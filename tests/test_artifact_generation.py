"""User-confirmed generation of table and text artifacts from a Slack thread."""

from __future__ import annotations

import zipfile
from pathlib import Path

from src.artifact_generation import ArtifactDraft, ArtifactGenerationWorkflow
from src.project_resolver import ProjectResolver
from src.thread_context import ThreadContextStore


class DraftingAgent:
    def create_artifact_draft(self, thread_context: str, destination: Path) -> ArtifactDraft:
        if destination.suffix == ".md":
            return ArtifactDraft(destination=destination, kind="text", content="# Generated")
        return ArtifactDraft(
            destination=destination,
            kind="table",
            headers=["company", "email"],
            rows=[{"company": "효성중공업", "email": "contact@example.com"}],
        )


class ProjectDraftingAgent(DraftingAgent):
    def __init__(self, root: Path) -> None:
        self.project_resolver = ProjectResolver(root=root)


def test_workflow_previews_then_confirms_csv_write(tmp_path: Path) -> None:
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = ArtifactGenerationWorkflow()
    target = tmp_path / "emails.csv"

    preview = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text=f"이메일 정리해줘 저장 위치: {target}",
        thread_context=context,
        agent=DraftingAgent(),
    )

    assert preview is not None
    assert "총 1행" in preview
    assert not target.exists()

    saved = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="<@U123> 저장",
        thread_context=context,
        agent=DraftingAgent(),
    )

    assert saved is not None
    assert "저장했습니다" in saved
    assert "contact@example.com" in target.read_text(encoding="utf-8-sig")


def test_workflow_writes_a_text_artifact_only_after_confirmation(tmp_path: Path) -> None:
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = ArtifactGenerationWorkflow()
    target = tmp_path / "report.md"

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text=f"보고서 만들어줘 저장 위치: {target}",
        thread_context=context,
        agent=DraftingAgent(),
    )
    assert not target.exists()

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="저장",
        thread_context=context,
        agent=DraftingAgent(),
    )

    assert target.read_text() == "# Generated"


def test_workflow_writes_an_xlsx_workbook(tmp_path: Path) -> None:
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = ArtifactGenerationWorkflow()
    target = tmp_path / "emails.xlsx"

    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text=f"이메일 정리해줘 저장 위치: {target}",
        thread_context=context,
        agent=DraftingAgent(),
    )
    workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="저장",
        thread_context=context,
        agent=DraftingAgent(),
    )

    with zipfile.ZipFile(target) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()


def test_workflow_defaults_to_the_selected_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "my-project"
    project_root.mkdir()
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = ArtifactGenerationWorkflow()

    preview = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="my-project 이메일 정리해줘",
        thread_context=context,
        agent=ProjectDraftingAgent(tmp_path),
    )

    assert preview is not None
    assert str(project_root / "generated-artifact.csv") in preview


def test_workflow_defaults_to_the_application_root_without_a_selected_project(
    tmp_path: Path,
) -> None:
    context = ThreadContextStore(root=tmp_path / "context")
    workflow = ArtifactGenerationWorkflow()

    preview = workflow.process(
        channel_id="C1",
        thread_ts="1.1",
        text="스레드 요약해서 md 파일로 만들어",
        thread_context=context,
        agent=DraftingAgent(),
    )

    assert preview is not None
    assert "generated-artifact.md" in preview
