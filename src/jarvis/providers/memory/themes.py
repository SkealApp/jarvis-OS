# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""ThemeStore — groupes thématiques colorés pour les fichiers mémoire (topics).

Un thème = un nom + une couleur, auquel des fichiers .md sont rattachés.
Persisté dans `memory_data/topic_themes.json` (à côté du dossier topics/).
Utilisé par la vue memory-graph (couleur des hexagones) et par memory_write
(rattachement automatique d'un nouveau fichier au thème adapté).

Un fichier sans thème est parfaitement valide — il apparaît simplement dans
la couleur par défaut du graphe.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_FILENAME = "topic_themes.json"


def is_valid_color(color: str) -> bool:
    return bool(_COLOR_RE.match(color))


class ThemeStore:
    """Lecture/écriture des thèmes de fichiers thématiques (JSON sur disque)."""

    def __init__(self, memory_dir: Path) -> None:
        self._path = Path(memory_dir) / _FILENAME

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"themes": {}, "assignments": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("ThemeStore corrompu ou illisible", path=str(self._path), error=str(e))
            return {"themes": {}, "assignments": {}}
        data.setdefault("themes", {})
        data.setdefault("assignments", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── Thèmes ────────────────────────────────────────────────────────────────

    def list_themes(self) -> list[dict[str, Any]]:
        """[{name, color, files}] — files = fichiers rattachés (tels qu'assignés)."""
        data = self._load()
        by_theme: dict[str, list[str]] = {name: [] for name in data["themes"]}
        for filename, theme in data["assignments"].items():
            if theme in by_theme:
                by_theme[theme].append(filename)
        return [
            {"name": name, "color": meta.get("color", "#4A9EFF"), "files": sorted(by_theme[name])}
            for name, meta in sorted(data["themes"].items())
        ]

    def upsert_theme(self, name: str, color: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Nom de thème vide.")
        if not is_valid_color(color):
            raise ValueError(f"Couleur invalide '{color}' (attendu : #RRGGBB).")
        data = self._load()
        data["themes"][name] = {"color": color}
        self._save(data)

    def delete_theme(self, name: str) -> bool:
        """Supprime un thème ; ses fichiers deviennent simplement sans thème."""
        data = self._load()
        if name not in data["themes"]:
            return False
        del data["themes"][name]
        data["assignments"] = {f: t for f, t in data["assignments"].items() if t != name}
        self._save(data)
        return True

    def resolve(self, name: str) -> str | None:
        """Nom canonique d'un thème, matching insensible à la casse. None si inconnu."""
        wanted = name.strip().lower()
        for existing in self._load()["themes"]:
            if existing.lower() == wanted:
                return existing
        return None

    # ── Assignations fichier → thème ─────────────────────────────────────────

    def assign(self, filename: str, theme: str | None) -> None:
        """Rattache un fichier à un thème (None = détacher). Le thème doit exister."""
        data = self._load()
        if theme is None:
            data["assignments"].pop(filename, None)
        else:
            if theme not in data["themes"]:
                raise ValueError(f"Thème inconnu : '{theme}'.")
            data["assignments"][filename] = theme
        self._save(data)

    def theme_of(self, filename: str) -> dict[str, str] | None:
        """{name, color} du thème du fichier, ou None si sans thème."""
        data = self._load()
        theme = data["assignments"].get(filename)
        if theme is None or theme not in data["themes"]:
            return None
        return {"name": theme, "color": data["themes"][theme].get("color", "#4A9EFF")}
