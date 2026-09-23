"""LLM-drafted, user-confirmed generation of text, CSV, and XLSX artifacts."""

from __future__ import annotations

import csv
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from xml.sax.saxutils import escape

from src.project_resolver import ProjectResolver, UnknownProject
from src.request_classifier import classify_request
from src.thread_context import ThreadContextStore

logger = logging.getLogger(__name__)

ArtifactKind = Literal["text", "table"]
_REQUEST = re.compile(r"(?:저장\s*위치|저장위치)\s*[:：]\s*(\S+)", re.IGNORECASE)
_CONFIRMATION = re.compile(r"^(저장|저장해줘|저장합니다)$")
_SLACK_MENTION = re.compile(r"<@[^>]+>")
_TEXT_SUFFIXES = {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".html", ".css"}
_TABLE_SUFFIXES = {".csv", ".xlsx"}
_APPLICATION_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ArtifactDraft:
    destination: Path
    kind: ArtifactKind
    content: str | None = None
    headers: list[str] | None = None
    rows: list[dict[str, str]] | None = None


class ArtifactDraftCreator(Protocol):
    def create_artifact_draft(self, thread_context: str, destination: Path) -> ArtifactDraft: ...


class PendingArtifactStore:
    def __init__(self) -> None:
        self._drafts: dict[tuple[str, str], ArtifactDraft] = {}

    def put(self, channel_id: str, thread_ts: str, draft: ArtifactDraft) -> None:
        self._drafts[(channel_id, thread_ts)] = draft

    def get(self, channel_id: str, thread_ts: str) -> ArtifactDraft | None:
        return self._drafts.get((channel_id, thread_ts))

    def remove(self, channel_id: str, thread_ts: str) -> None:
        self._drafts.pop((channel_id, thread_ts), None)


class ArtifactGenerationWorkflow:
    """Builds a preview from thread context and writes it only after confirmation."""

    def __init__(self, *, draft_store: PendingArtifactStore | None = None) -> None:
        self.draft_store = draft_store or PendingArtifactStore()

    def process(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: object,
    ) -> str | None:
        command_text = _SLACK_MENTION.sub("", text).strip()
        if _CONFIRMATION.fullmatch(command_text):
            return self._save_pending(channel_id, thread_ts)

        match = _REQUEST.search(command_text)
        if not _is_generation_request(command_text):
            return None

        creator = getattr(agent, "create_artifact_draft", None)
        if not callable(creator):
            return "파일 생성에는 LLM 분석 에이전트가 필요합니다."

        prior_context = thread_context.read(channel_id, thread_ts)
        context = f"{prior_context}\n{command_text}".strip()
        try:
            destination = (
                validate_destination(match.group(1))
                if match
                else _default_destination(context, command_text, agent)
            )
        except ValueError as exc:
            return f"저장 위치가 올바르지 않습니다: {exc}"
        try:
            draft = creator(context, destination)
        except Exception as exc:
            logger.exception("artifact_draft_failed destination=%s", destination)
            return f"파일 초안 생성에 실패했습니다: {exc}"

        self.draft_store.put(channel_id, thread_ts, draft)
        logger.info(
            "artifact_preview_ready destination=%s kind=%s row_count=%s",
            destination,
            draft.kind,
            len(draft.rows or []),
        )
        return render_preview(draft)

    def _save_pending(self, channel_id: str, thread_ts: str) -> str | None:
        draft = self.draft_store.get(channel_id, thread_ts)
        if draft is None:
            return None
        try:
            write_artifact(draft)
        except ValueError as exc:
            return f"파일을 저장하지 못했습니다: {exc}"

        self.draft_store.remove(channel_id, thread_ts)
        logger.info(
            "artifact_saved destination=%s kind=%s row_count=%s",
            draft.destination,
            draft.kind,
            len(draft.rows or []),
        )
        return f"✅ 파일을 저장했습니다: `{draft.destination}`"


def validate_destination(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("절대 경로 또는 ~/로 시작하는 경로를 사용하세요")
    if path.suffix.casefold() not in _TEXT_SUFFIXES | _TABLE_SUFFIXES:
        raise ValueError(
            "지원 형식: .csv, .xlsx, .md, .txt, .py, .js, .ts, .json, .yaml, .yml, .html, .css"
        )
    return path


def write_artifact(draft: ArtifactDraft) -> None:
    if draft.destination.suffix.casefold() == ".csv":
        write_csv(draft)
    elif draft.destination.suffix.casefold() == ".xlsx":
        write_xlsx(draft)
    else:
        write_text(draft)


def write_text(draft: ArtifactDraft) -> None:
    if draft.kind != "text" or draft.content is None:
        raise ValueError("텍스트 파일 초안이 아닙니다")
    _create_parent(draft.destination)
    try:
        with draft.destination.open("x", encoding="utf-8", newline="") as output:
            output.write(draft.content)
    except FileExistsError as exc:
        raise ValueError("같은 이름의 파일이 이미 있어 덮어쓰지 않았습니다") from exc
    except OSError as exc:
        raise ValueError(str(exc)) from exc


def write_csv(draft: ArtifactDraft) -> None:
    headers, rows = _table_data(draft)
    _create_parent(draft.destination)
    try:
        with draft.destination.open("x", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except FileExistsError as exc:
        raise ValueError("같은 이름의 파일이 이미 있어 덮어쓰지 않았습니다") from exc
    except OSError as exc:
        raise ValueError(str(exc)) from exc


def write_xlsx(draft: ArtifactDraft) -> None:
    """Write a minimal standards-compliant XLSX workbook without another dependency."""
    headers, rows = _table_data(draft)
    _create_parent(draft.destination)
    values = [headers, *[[row.get(header, "") for header in headers] for row in rows]]
    worksheet_rows = "".join(
        f'<row r="{index}">' + "".join(
            f'<c r="{_column_name(column)}{index}" t="inlineStr">'
            f"<is><t>{escape(value)}</t></is></c>"
            for column, value in enumerate(row, start=1)
        ) + "</row>"
        for index, row in enumerate(values, start=1)
    )
    files = {
        "[Content_Types].xml": _content_types_xml(),
        "_rels/.rels": _root_relationships_xml(),
        "xl/workbook.xml": _workbook_xml(),
        "xl/_rels/workbook.xml.rels": _workbook_relationships_xml(),
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{worksheet_rows}</sheetData></worksheet>',
    }
    try:
        with zipfile.ZipFile(draft.destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
    except FileExistsError as exc:
        raise ValueError("같은 이름의 파일이 이미 있어 덮어쓰지 않았습니다") from exc
    except OSError as exc:
        raise ValueError(str(exc)) from exc


def render_preview(draft: ArtifactDraft) -> str:
    if draft.kind == "table":
        headers, rows = _table_data(draft)
        sample = "\n".join(
            " | ".join(row.get(header, "") for header in headers) for row in rows[:3]
        ) or "(행 없음)"
        description = (
            f"총 {len(rows)}행, 열: {', '.join(headers)}\n"
            f"샘플(최대 3행):\n```\n{sample}\n```"
        )
    else:
        content_preview = "\n".join((draft.content or "").splitlines()[:20])
        description = f"내용 미리보기(최대 20행):\n```\n{content_preview}\n```"
    return (
        f"파일 초안을 만들었습니다. 저장 위치: `{draft.destination}`\n{description}\n"
        "내용과 위치를 확인한 뒤 같은 스레드에 `저장`이라고 보내면 파일을 생성합니다."
    )


def _is_generation_request(text: str) -> bool:
    return any(marker in text.casefold() for marker in ("생성", "만들", "정리", "create", "write"))


def _default_destination(context: str, text: str, agent: object) -> Path:
    resolver = getattr(agent, "project_resolver", None)
    if not isinstance(resolver, ProjectResolver):
        return _APPLICATION_ROOT / _default_filename(text)
    request = classify_request(context, resolver)
    if request.project_name is None:
        return _APPLICATION_ROOT / _default_filename(text)
    try:
        project_root = resolver.resolve(request.project_name)
    except UnknownProject as exc:
        raise ValueError("대상 프로젝트를 찾을 수 없습니다") from exc
    return project_root / _default_filename(text)


def _default_filename(text: str) -> str:
    normalized_text = text.casefold()
    if ".xlsx" in normalized_text or "xlsx" in normalized_text:
        return "generated-artifact.xlsx"
    if ".csv" in normalized_text or "csv" in normalized_text or "이메일" in normalized_text:
        return "generated-artifact.csv"
    if ".py" in normalized_text or "python" in normalized_text:
        return "generated-artifact.py"
    if ".ts" in normalized_text or "typescript" in normalized_text:
        return "generated-artifact.ts"
    return "generated-artifact.md"


def _table_data(draft: ArtifactDraft) -> tuple[list[str], list[dict[str, str]]]:
    if draft.kind != "table" or not draft.headers or draft.rows is None:
        raise ValueError("표 형식 파일 초안이 아닙니다")
    return draft.headers, draft.rows


def _create_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Types '
        'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )


def _root_relationships_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )


def _workbook_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><workbook '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )


def _workbook_relationships_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
