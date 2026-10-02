"""Every `src` module imports with only the declared dependencies installed."""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import src


def _module_names() -> list[str]:
    return sorted(m.name for m in pkgutil.walk_packages(src.__path__, "src."))


@pytest.mark.parametrize("name", _module_names())
def test_src_module_imports(name: str) -> None:
    importlib.import_module(name)
