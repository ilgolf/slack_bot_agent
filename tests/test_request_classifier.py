"""Deterministic project and narrow-request classification."""

from __future__ import annotations

from pathlib import Path

from src.project_resolver import ProjectResolver
from src.request_classifier import RequestKind, classify_request


def test_classify_request_finds_project_from_thread_context(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    request = classify_request(
        "스레드 맥락:\nmy-project이 있어?\n\n현재 요청:\nREADME.md 요약해줘",
        ProjectResolver(root=tmp_path),
    )

    assert request.project_name == "my-project"
    assert request.kind is RequestKind.README_SUMMARY


def test_classify_request_keeps_readme_request_without_a_project(tmp_path: Path) -> None:
    request = classify_request("README.md 요약해줘", ProjectResolver(root=tmp_path))

    assert request.project_name is None
    assert request.kind is RequestKind.README_SUMMARY


def test_classify_request_prefers_project_in_current_message(tmp_path: Path) -> None:
    (tmp_path / "old-project").mkdir()
    (tmp_path / "new-project").mkdir()

    request = classify_request(
        "스레드 맥락:\nold-project을 분석해줘\n\n현재 요청:\nnew-project README.md 요약해줘",
        ProjectResolver(root=tmp_path),
    )

    assert request.project_name == "new-project"


def test_classify_request_finds_project_under_orca_projects_collection(tmp_path: Path) -> None:
    (tmp_path / "projects" / "my-project").mkdir(parents=True)

    request = classify_request(
        "~/orca/projects/my-project README.md 요약해줘", ProjectResolver(root=tmp_path)
    )

    assert request.project_name == "my-project"


def test_classify_request_recognizes_a_single_file_summary(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()

    request = classify_request(
        "my-project src/service.py 요약해줘",
        ProjectResolver(root=tmp_path),
    )

    assert request.kind is RequestKind.FILE_SUMMARY
    assert request.relative_path == "src/service.py"


def test_classify_request_keeps_email_csv_creation_out_of_analysis_fast_paths(
    tmp_path: Path,
) -> None:
    request = classify_request(
        "~/orca root에 관광 효성중공업 이메일들 csv로 만들어줘",
        ProjectResolver(root=tmp_path),
    )

    assert request.kind is RequestKind.GENERAL_ANALYSIS
