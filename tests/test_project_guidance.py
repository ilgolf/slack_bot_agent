"""with_project_guidance: the project's AGENTS.md handed to Claude ahead of the request."""

from __future__ import annotations

from pathlib import Path

from src.project_guidance import MAX_AGENTS_MD_BYTES, with_project_guidance


def test_an_agents_md_at_the_size_limit_is_handed_over(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("a" * MAX_AGENTS_MD_BYTES, encoding="utf-8")

    assert "a" * 100 in with_project_guidance("요청", tmp_path)


def test_an_agents_md_over_the_size_limit_is_not_handed_over(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("a" * (MAX_AGENTS_MD_BYTES + 1), encoding="utf-8")

    assert with_project_guidance("요청", tmp_path) == "요청"


def _slack_md(project: Path, text: str) -> Path:
    (project / ".piplup").mkdir(exist_ok=True)
    path = project / ".piplup" / "slack.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_slack_md_follows_agents_md_and_both_precede_the_request(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("공통 규칙", encoding="utf-8")
    _slack_md(tmp_path, "Slack 전용 규칙")

    prompt = with_project_guidance("요청", tmp_path)

    assert prompt.index("공통 규칙") < prompt.index("Slack 전용 규칙") < prompt.index("요청")


def test_slack_md_alone_is_handed_over(tmp_path: Path) -> None:
    _slack_md(tmp_path, "Slack 전용 규칙")

    assert "Slack 전용 규칙" in with_project_guidance("요청", tmp_path)


def test_an_unusable_slack_md_is_not_handed_over(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("바깥 파일", encoding="utf-8")
    (project / ".piplup").mkdir()
    (project / ".piplup" / "slack.md").symlink_to(outside)
    assert with_project_guidance("요청", project) == "요청"

    (project / ".piplup" / "slack.md").unlink()
    _slack_md(project, "a" * (MAX_AGENTS_MD_BYTES + 1))
    assert with_project_guidance("요청", project) == "요청"
