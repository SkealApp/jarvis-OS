"""Tests WriteFileTool — écriture bornée aux roots autorisés."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.capabilities.tools.filesystem import WriteFileTool


@pytest.fixture()
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WriteFileTool:
    monkeypatch.setattr("jarvis.capabilities.tools.filesystem._perms.get", lambda _k: True)
    return WriteFileTool(allowed_roots=[tmp_path])


async def test_write_file_ok(tool: WriteFileTool, tmp_path: Path) -> None:
    target = tmp_path / "autoclicker.py"
    result = await tool.execute(path=str(target), content="print('ok')\n")
    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "print('ok')\n"


async def test_write_file_blocked_env(tool: WriteFileTool, tmp_path: Path) -> None:
    result = await tool.execute(path=str(tmp_path / ".env"), content="SECRET=1")
    assert result.is_error
    assert "sensible" in result.content.lower()


async def test_write_file_outside_root(tool: WriteFileTool, tmp_path: Path) -> None:
    result = await tool.execute(path=str(tmp_path.parent / "outside.py"), content="x")
    assert result.is_error
    assert "refusé" in result.content.lower()


def test_projects_dir_in_description(tmp_path: Path) -> None:
    """La description mentionne le dossier projets (ex: Documents/Atlas)."""
    atlas = tmp_path / "Documents" / "Atlas"
    tool = WriteFileTool(allowed_roots=[tmp_path], projects_dir=atlas)
    assert str(atlas) in tool.description
    assert "<nom-du-projet>" in tool.description


def test_no_projects_dir_keeps_class_description(tmp_path: Path) -> None:
    tool = WriteFileTool(allowed_roots=[tmp_path])
    assert tool.description == WriteFileTool.description
