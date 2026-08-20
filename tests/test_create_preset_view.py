# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Tests de la création de presets (routines) et vues par le LLM.

Pipeline : preset_create / view_create → SkillLab.propose_preset/propose_view
→ candidates/{name}/ → sandbox → validation humaine (promote).
Les vues ne sont copiées vers static/skills/ qu'à la promotion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jarvis.capabilities.skills.lab import SkillLab
from jarvis.capabilities.skills.lifecycle import SkillLifecycle, SkillStatus
from jarvis.capabilities.skills.synthesizer import SkillSynthesizer
from jarvis.capabilities.tools.skills import PresetCreateTool, ViewCreateTool
from jarvis.providers.memory.kernel import MemoryKernel

_VALID_STEPS = [
    {
        "type": "open_app",
        "name": "Ouvrir Steam",
        "action": "Steam",
        "process": "steam",
        "windows_paths": ["C:/Program Files (x86)/Steam/steam.exe"],
    },
    {"type": "tts", "name": "Annonce", "text": "Mode gaming activé."},
    {"type": "wait", "name": "Pause", "seconds": 2},
]

_VALID_VIEW_JS = """\
(function () {
  const VIEW_ID = 'test-panel';
  Jarvis.views.register(VIEW_ID, {
    meta: { label: 'Test Panel', icon: 'grid' },
    show(container, payload) { container.textContent = 'hello'; },
    hide() {},
  });
})();
"""


@pytest.fixture
def kernel(tmp_path: Path) -> MemoryKernel:
    return MemoryKernel(tmp_path / "memory.db")


@pytest.fixture
def lifecycle(tmp_path: Path) -> SkillLifecycle:
    return SkillLifecycle(db_path=tmp_path / "memory.db")


@pytest.fixture
def lab(
    kernel: MemoryKernel,
    lifecycle: SkillLifecycle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SkillLab:
    # Force le fallback direct (pas de Docker en test)
    monkeypatch.setattr("jarvis.capabilities.skills.lab.settings.docker_enabled", False)
    synth = SkillSynthesizer(llm=None)  # aucun appel LLM pour les créations structurées
    return SkillLab(
        kernel=kernel,
        lifecycle=lifecycle,
        synthesizer=synth,
        candidates_dir=tmp_path / "candidates",
        installed_dir=tmp_path / "installed",
        ui_skills_dir=tmp_path / "static_skills",
    )


# ── Synthesizer : validation preset ───────────────────────────────────────────


def test_preset_candidate_files_written(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    name = synth.propose_preset_candidate(
        name="mode-gaming",
        description="Lance l'environnement gaming.",
        triggers=["mode gaming", "on joue"],
        steps=_VALID_STEPS,
        target_dir=tmp_path,
    )
    assert name == "mode-gaming"
    cand = tmp_path / "mode-gaming"
    meta = yaml.safe_load((cand / "skill.yaml").read_text(encoding="utf-8"))
    assert meta["type"] == "preset"
    assert meta["triggers"] == ["mode gaming", "on joue"]
    assert len(meta["steps"]) == 3
    assert "PresetSkill" in (cand / "skill.py").read_text(encoding="utf-8")
    assert (cand / "SKILL.md").exists()


def test_preset_invalid_name_rejected(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    with pytest.raises(ValueError, match="kebab-case"):
        synth.propose_preset_candidate(
            name="Mode Gaming!",
            description="x",
            triggers=["go"],
            steps=_VALID_STEPS,
            target_dir=tmp_path,
        )


def test_preset_unknown_step_type_rejected(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    with pytest.raises(ValueError, match="inconnu"):
        synth.propose_preset_candidate(
            name="mode-x",
            description="x",
            triggers=["go"],
            steps=[{"type": "format_disk", "name": "boom"}],
            target_dir=tmp_path,
        )


def test_preset_dangerous_cli_rejected(tmp_path: Path) -> None:
    """Un step cli avec pattern shell dangereux est refusé à la création."""
    synth = SkillSynthesizer(llm=None)
    with pytest.raises(ValueError, match="dangereux"):
        synth.propose_preset_candidate(
            name="mode-evil",
            description="x",
            triggers=["go"],
            steps=[{"type": "cli", "name": "boom", "command": "shutdown -h now"}],
            target_dir=tmp_path,
        )


def test_preset_no_triggers_rejected(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    with pytest.raises(ValueError, match="trigger"):
        synth.propose_preset_candidate(
            name="mode-x",
            description="x",
            triggers=[],
            steps=_VALID_STEPS,
            target_dir=tmp_path,
        )


# ── Synthesizer : validation vue ──────────────────────────────────────────────


def test_view_candidate_files_written(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    name = synth.propose_view_candidate(
        name="test-panel",
        description="Panneau de test.",
        view_js=_VALID_VIEW_JS,
        view_css=".test-panel { color: red; }",
        capabilities=["Afficher un panneau"],
        target_dir=tmp_path,
    )
    cand = tmp_path / name
    meta = yaml.safe_load((cand / "skill.yaml").read_text(encoding="utf-8"))
    assert meta["type"] == "view"
    assert (cand / "view.js").read_text(encoding="utf-8") == _VALID_VIEW_JS
    assert (cand / "view.css").exists()
    skill_py = (cand / "skill.py").read_text(encoding="utf-8")
    assert "show_view" in skill_py


def test_view_without_register_rejected(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    with pytest.raises(ValueError, match="Jarvis.views.register"):
        synth.propose_view_candidate(
            name="bad-view",
            description="x",
            view_js="console.log('no register');",
            target_dir=tmp_path,
        )


def test_view_with_eval_rejected(tmp_path: Path) -> None:
    synth = SkillSynthesizer(llm=None)
    with pytest.raises(ValueError, match="interdit"):
        synth.propose_view_candidate(
            name="bad-view",
            description="x",
            view_js="Jarvis.views.register('x', {}); eval('alert(1)');",
            target_dir=tmp_path,
        )


# ── Lab : pipeline complet preset ─────────────────────────────────────────────


async def test_lab_preset_passes_sandbox(lab: SkillLab) -> None:
    record = await lab.propose_preset(
        name="mode-gaming",
        description="Lance l'environnement gaming.",
        triggers=["mode gaming"],
        steps=_VALID_STEPS,
    )
    assert record is not None
    assert record.status == SkillStatus.SANDBOXED_PASS


async def test_lab_preset_collision_with_installed(lab: SkillLab, tmp_path: Path) -> None:
    (tmp_path / "installed" / "mode-gaming").mkdir(parents=True)
    with pytest.raises(ValueError, match="existe déjà"):
        await lab.propose_preset(
            name="mode-gaming",
            description="x",
            triggers=["go"],
            steps=_VALID_STEPS,
        )


# ── Lab : pipeline complet vue + promotion ────────────────────────────────────


async def test_lab_view_promote_publishes_assets(lab: SkillLab, tmp_path: Path) -> None:
    """La vue passe le sandbox, et promote() copie view.js vers static/skills/."""
    record = await lab.propose_view(
        name="test-panel",
        description="Panneau de test.",
        view_js=_VALID_VIEW_JS,
        view_css=".x { color: red; }",
    )
    assert record is not None
    assert record.status == SkillStatus.SANDBOXED_PASS

    # Avant promotion : rien dans static
    static_js = tmp_path / "static_skills" / "test-panel" / "view.js"
    assert not static_js.exists()

    promoted = lab.promote("test-panel")
    assert promoted is not None
    assert promoted.status == SkillStatus.ACTIVE
    assert (tmp_path / "installed" / "test-panel" / "view.js").exists()
    assert static_js.exists()
    assert (tmp_path / "static_skills" / "test-panel" / "view.css").exists()


async def test_lab_preset_promote_no_static(lab: SkillLab, tmp_path: Path) -> None:
    """Un preset promu ne publie rien dans static/skills/."""
    await lab.propose_preset(
        name="mode-calme",
        description="x",
        triggers=["mode calme"],
        steps=[{"type": "tts", "name": "Annonce", "text": "ok"}],
    )
    promoted = lab.promote("mode-calme")
    assert promoted is not None
    assert not (tmp_path / "static_skills" / "mode-calme").exists()


# ── Tools : erreurs de spec remontées proprement ──────────────────────────────


async def test_preset_create_tool_invalid_spec(lab: SkillLab) -> None:
    tool = PresetCreateTool(lab=lab)
    result = await tool.execute(
        name="BAD NAME",
        description="x",
        triggers=["go"],
        steps=_VALID_STEPS,
    )
    assert result.is_error
    assert "Spec invalide" in result.content


async def test_view_create_tool_success_message(lab: SkillLab) -> None:
    tool = ViewCreateTool(lab=lab)
    result = await tool.execute(
        name="panel-ok",
        description="x",
        view_js=_VALID_VIEW_JS.replace("test-panel", "panel-ok"),
    )
    assert not result.is_error
    assert "N'EST PAS active" in result.content
    assert "panel-ok" in result.content
