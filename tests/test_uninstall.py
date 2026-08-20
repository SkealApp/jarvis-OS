"""Désinstallation : résolution dossier vs nom yaml, nettoyage des assets vue."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.capabilities.skills.installer import SkillInstaller
from jarvis.capabilities.skills import installer as inst_mod


def test_uninstall_by_yaml_name_when_folder_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tmp_path / "installed"
    static = tmp_path / "static" / "skills"
    folder = installed / "globe"
    folder.mkdir(parents=True)
    (folder / "skill.yaml").write_text("name: globe-view\ntype: view\n", encoding="utf-8")
    (folder / "skill.py").write_text("# dummy\n", encoding="utf-8")
    (static / "globe").mkdir(parents=True)
    (static / "globe" / "view.js").write_text("/* js */", encoding="utf-8")

    monkeypatch.setattr(inst_mod, "SKILLS_INSTALLED_DIR", installed)
    monkeypatch.setattr(inst_mod, "UI_STATIC_DIR", tmp_path / "static")
    monkeypatch.setattr(inst_mod.skill_registry, "reload", lambda: None)

    result = SkillInstaller().uninstall("globe-view")
    assert result["success"] is True
    assert not folder.exists()
    assert not (static / "globe").exists()


def test_uninstall_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(inst_mod, "SKILLS_INSTALLED_DIR", tmp_path / "installed")
    monkeypatch.setattr(inst_mod, "UI_STATIC_DIR", tmp_path / "static")
    result = SkillInstaller().uninstall("does-not-exist")
    assert result["success"] is False
