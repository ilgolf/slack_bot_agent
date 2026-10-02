"""Path classification for plan.md Phase 16: spelling variants must not slip through."""

from __future__ import annotations

import pytest

from src.plan_guard import is_risky_path, is_secret_path


@pytest.mark.parametrize(
    "path",
    [
        "./pyproject.toml",
        "PyProject.TOML",
        "tests/CONFTEST.py",
        "tests//conftest.py",
        "sub/../tests/conftest.py",
        "scripts\\run.sh",
        ".\\scripts\\RUN.SH",
        "./.github/workflows/ci.yml",
        ".GitHub/workflows/ci.yml",
        "x/./.husky/pre-commit",
    ],
)
def test_risky_path_spelling_variants_are_still_risky(path: str) -> None:
    assert is_risky_path(path)


@pytest.mark.parametrize("path", ["./.env", "CONFIG\\.ENV.local", "a//.env.production"])
def test_secret_path_spelling_variants_are_still_secret(path: str) -> None:
    assert is_secret_path(path)


@pytest.mark.parametrize("path", ["src/app.py", "README.md", "docs/env.md", ".env.example"])
def test_ordinary_paths_are_neither_risky_nor_secret(path: str) -> None:
    assert not is_risky_path(path)
    assert not is_secret_path(path)
