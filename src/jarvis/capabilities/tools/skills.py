# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Outils LLM pour la gestion des skills Jarvis (création, amélioration, liste)."""

from __future__ import annotations

from jarvis.capabilities.skills.lab import SkillLab
from jarvis.capabilities.skills.registry import skill_registry
from jarvis.capabilities.skills.synthesizer import SkillSynthesizer
from jarvis.capabilities.tools.base import Tool, ToolResult
from jarvis.kernel.error_collector import collector  # jrv: autofix


class SkillCreateTool(Tool):
    """Propose une nouvelle skill candidate via le SkillLab (PHASE 4).

    Le LLM ne peut PLUS installer une skill directement : ce tool passe
    obligatoirement par `SkillLab.propose_from_trajectory()` qui écrit en
    zone tampon `skills/candidates/{name}/` ET lance le test sandbox.
    La promotion vers `skills/installed/` exige une validation humaine
    explicite via l'endpoint `POST /api/skills/lab/{name}/promote`.
    """

    name = "skill_create"
    description = (
        "Propose une nouvelle skill Jarvis CANDIDATE depuis une tâche accomplie. "
        "La skill est générée puis testée en sandbox automatique. "
        "Elle N'EST PAS installée tant qu'un humain ne l'a pas validée via "
        "l'endpoint /api/skills/lab/{name}/promote — c'est intentionnel pour "
        "éviter qu'un agent installe du code arbitraire dans le système. "
        "Appeler après avoir réussi une tâche non-triviale et répétable pour "
        "soumettre le savoir-faire à la validation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Description concise de la tâche accomplie (1-3 phrases).",
            },
            "messages": {
                "type": "array",
                "description": (
                    "Extrait de l'historique de conversation (liste de {role, content})."
                ),
                "items": {"type": "object"},
            },
            "tool_calls": {
                "type": "array",
                "description": "Outils utilisés pendant la tâche (liste de {name, result}).",
                "items": {"type": "object"},
            },
            "result": {
                "type": "string",
                "description": "Résultat ou livrable final de la tâche.",
            },
        },
        "required": ["task_description"],
    }

    def __init__(self, lab: SkillLab) -> None:
        # Lab requis : aucun chemin sans gate. Pas de fallback "construct
        # default" pour éviter qu'un appelant oublie l'injection et bypass
        # accidentellement le sandbox.
        self._lab = lab

    async def execute(  # type: ignore[override]
        self,
        task_description: str,
        messages: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
        result: str = "",
    ) -> ToolResult:
        trajectory: dict = {
            "task_description": task_description,
            "messages": messages or [],
            "tool_calls": tool_calls or [],
            "result": result,
        }
        try:
            record = await self._lab.propose_from_trajectory(trajectory)
        except Exception as exc:  # noqa: BLE001
            collector.error("JRV-TOL-001", "JRV-TOL-001", cause=exc)
            return ToolResult(content=f"Erreur Lab : {exc}", is_error=True)

        if record is None:
            return ToolResult(
                content=(
                    "Génération de la candidate échouée (LLM down ou JSON "
                    "non parsable). Aucune skill créée."
                ),
                is_error=True,
            )

        if record.status.value == "sandboxed_pass":
            return ToolResult(
                content=(
                    f"Skill candidate '{record.name}' générée et test sandbox VERT. "
                    f"En attente de validation humaine "
                    f"(POST /api/skills/lab/{record.name}/promote). "
                    f"La skill n'est PAS installée tant que la validation "
                    f"n'a pas eu lieu."
                )
            )
        # SANDBOXED_FAIL — la skill est rejetée par le gate
        return ToolResult(
            content=(
                f"Skill candidate '{record.name}' REJETÉE par le test sandbox. "
                f"Cause : {record.sandbox_notes or '(détail manquant)'}. "
                f"Aucune installation."
            ),
            is_error=True,
        )


def _lab_record_to_result(record, kind: str, name: str) -> ToolResult:  # noqa: ANN001
    """Formate le verdict sandbox d'une candidate en ToolResult."""
    if record is None:
        return ToolResult(
            content=f"Génération de la {kind} '{name}' échouée. Rien n'a été créé.",
            is_error=True,
        )
    if record.status.value == "sandboxed_pass":
        return ToolResult(
            content=(
                f"{kind.capitalize()} candidate '{record.name}' créée et test sandbox VERT. "
                f"Elle N'EST PAS active : demande à l'utilisateur de la valider "
                f"dans le dashboard Jarvis (section Skill Lab) ou via "
                f"POST /api/skills/lab/{record.name}/promote."
            )
        )
    return ToolResult(
        content=(
            f"{kind.capitalize()} candidate '{record.name}' REJETÉE par le test sandbox. "
            f"Cause : {record.sandbox_notes or '(détail manquant)'}."
        ),
        is_error=True,
    )


class PresetCreateTool(Tool):
    """Crée une routine (preset) candidate via le SkillLab.

    Même gate que skill_create : zone candidate + sandbox + validation
    humaine obligatoire avant activation. Les steps `cli` sont validés
    contre la blocklist shell à la création.
    """

    name = "preset_create"
    description = (
        "Crée une ROUTINE Jarvis (preset) : une séquence d'étapes déclenchée à la voix "
        "(ex: 'mode gaming' → ouvrir Steam + Discord + musique). La routine est créée en "
        "zone candidate et N'EST PAS active tant que l'utilisateur ne l'a pas validée "
        "dans le dashboard. Types de steps : "
        "open_app (action=nom app, process=nom processus, windows_paths=[chemins exe]), "
        "cli (platforms={windows: cmd, mac: cmd}), "
        "tts (text=phrase à dire), ai (prompt=instruction LLM), wait (seconds), "
        "notify (title, body), spotify (action=play/pause, query=recherche). "
        "Utilise ce tool quand l'utilisateur demande une nouvelle routine/automatisation "
        "multi-étapes réutilisable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Nom kebab-case unique (ex: 'mode-gaming').",
            },
            "label": {
                "type": "string",
                "description": "Nom d'affichage (ex: 'Mode Gaming').",
            },
            "description": {
                "type": "string",
                "description": "Ce que fait la routine, en 1-2 phrases.",
            },
            "triggers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Phrases vocales qui déclenchent la routine (2-4).",
            },
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Étapes ordonnées. Chaque step : {type, name, ...champs du type}. "
                    "Ex: {type: 'open_app', name: 'Ouvrir Steam', action: 'Steam', "
                    "process: 'steam', windows_paths: ['C:/Program Files (x86)/Steam/steam.exe']}"
                ),
            },
        },
        "required": ["name", "description", "triggers", "steps"],
    }

    def __init__(self, lab: SkillLab) -> None:
        self._lab = lab

    async def execute(  # type: ignore[override]
        self,
        name: str,
        description: str,
        triggers: list[str],
        steps: list[dict],
        label: str = "",
    ) -> ToolResult:
        try:
            record = await self._lab.propose_preset(
                name=name,
                description=description,
                triggers=triggers,
                steps=steps,
                label=label,
            )
        except ValueError as exc:
            return ToolResult(content=f"Spec invalide : {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            collector.error("JRV-TOL-001", "JRV-TOL-001", cause=exc)
            return ToolResult(content=f"Erreur Lab : {exc}", is_error=True)
        return _lab_record_to_result(record, "routine", name)


class ViewCreateTool(Tool):
    """Crée une vue frontend candidate via le SkillLab.

    Le view.js fourni n'est JAMAIS chargé par le navigateur avant la
    validation humaine (copie vers static/skills/ uniquement à la promotion).
    """

    name = "view_create"
    description = (
        "Crée une VUE visuelle Jarvis (panneau interactif affiché dans l'interface, "
        "comme le globe ou le dashboard Strava). Fournis le code JS complet dans view_js. "
        "Contrat OBLIGATOIRE du view.js : IIFE qui appelle "
        "Jarvis.views.register('<name>', { meta: {label, icon}, "
        "show(container, payload) {...}, hide() {...}, command(payload) {...} }). "
        "Le container est un div plein écran ; style sombre translucide comme les vues "
        "existantes. eval/new Function/document.cookie sont interdits. "
        "La vue est créée en zone candidate et N'EST PAS chargée tant que l'utilisateur "
        "ne l'a pas validée dans le dashboard. Après validation, elle s'affiche via "
        "show_view avec view_id='<name>'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Identifiant kebab-case unique de la vue (ex: 'meteo-radar').",
            },
            "label": {
                "type": "string",
                "description": "Nom d'affichage (ex: 'Radar Météo').",
            },
            "description": {
                "type": "string",
                "description": "Ce que montre la vue, en 1-2 phrases.",
            },
            "capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Liste des capacités visibles (pour le catalogue).",
            },
            "view_js": {
                "type": "string",
                "description": (
                    "Code JavaScript COMPLET de la vue, incluant l'appel "
                    "Jarvis.views.register(...)."
                ),
            },
            "view_css": {
                "type": "string",
                "description": "CSS optionnel de la vue.",
            },
        },
        "required": ["name", "description", "view_js"],
    }

    def __init__(self, lab: SkillLab) -> None:
        self._lab = lab

    async def execute(  # type: ignore[override]
        self,
        name: str,
        description: str,
        view_js: str,
        label: str = "",
        capabilities: list[str] | None = None,
        view_css: str = "",
    ) -> ToolResult:
        try:
            record = await self._lab.propose_view(
                name=name,
                description=description,
                view_js=view_js,
                label=label,
                view_css=view_css,
                capabilities=capabilities,
            )
        except ValueError as exc:
            return ToolResult(content=f"Spec invalide : {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            collector.error("JRV-TOL-001", "JRV-TOL-001", cause=exc)
            return ToolResult(content=f"Erreur Lab : {exc}", is_error=True)
        return _lab_record_to_result(record, "vue", name)


class SkillImproveTool(Tool):
    """Améliore un skill existant à partir d'une nouvelle expérience."""

    name = "skill_improve"
    description = (
        "Affine et améliore un skill Jarvis existant avec une nouvelle expérience. "
        "Appeler quand une tâche déjà couverte par un skill a révélé des cas "
        "non gérés, des meilleures pratiques ou des corrections utiles."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Nom kebab-case du skill à améliorer (ex: 'web-research').",
            },
            "new_experience": {
                "type": "string",
                "description": (
                    "Description de la nouvelle expérience à intégrer : "
                    "ce qui a changé, ce qui a mieux fonctionné, les cas limites découverts."
                ),
            },
        },
        "required": ["skill_name", "new_experience"],
    }

    def __init__(self, synthesizer: SkillSynthesizer) -> None:
        self._synthesizer = synthesizer

    async def execute(  # type: ignore[override]
        self,
        skill_name: str,
        new_experience: str,
    ) -> ToolResult:
        try:
            await self._synthesizer.improve_skill(skill_name, new_experience)
            return ToolResult(content=f"Skill '{skill_name}' amélioré avec la nouvelle expérience.")
        except FileNotFoundError as exc:
            collector.error("JRV-TOL-001", "JRV-TOL-001", cause=exc)
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001
            collector.error("JRV-TOL-001", "JRV-TOL-001", cause=exc)
            return ToolResult(content=f"Erreur amélioration : {exc}", is_error=True)


class SkillListTool(Tool):
    """Liste les skills installés dans Jarvis."""

    name = "skill_list"
    description = (
        "Liste tous les skills installés dans Jarvis avec leur nom, version, "
        "description et tags. Utiliser pour savoir quels skills sont disponibles "
        "avant d'en créer un nouveau similaire."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "filter_tag": {
                "type": "string",
                "description": "Filtrer par tag (optionnel). Ex: 'research', 'coding'.",
            },
        },
        "required": [],
    }

    async def execute(self, filter_tag: str = "") -> ToolResult:  # type: ignore[override]

        skills = skill_registry.list_installed()
        if filter_tag:
            skills = [
                s for s in skills if filter_tag.lower() in [t.lower() for t in s.get("tags", [])]
            ]

        if not skills:
            msg = "Aucun skill installé" + (f" avec le tag '{filter_tag}'" if filter_tag else "")
            return ToolResult(content=msg)

        lines = [f"## Skills installés ({len(skills)})\n"]
        for s in skills:
            tags_str = ", ".join(s.get("tags", [])) or "—"
            lines.append(
                f"**{s['name']}** v{s['version']} — {s['description']}\n"
                f"  Tags : {tags_str} | Type : {s.get('type', 'conversational')}"
            )

        return ToolResult(content="\n\n".join(lines))
