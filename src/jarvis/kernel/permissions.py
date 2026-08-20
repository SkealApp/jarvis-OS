# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

from __future__ import annotations

import json
from pathlib import Path

from jarvis.kernel.paths import CONFIG_DIR

_KEYS: frozenset[str] = frozenset({"microphone", "screen", "camera", "files"})
_DEFAULTS: dict[str, bool] = {
    "microphone": True,
    "screen": False,
    "camera": False,
    # Assistant local : lecture/écriture de fichiers demandées par l'utilisateur.
    # Le toggle UI peut toujours désactiver. Persisté pour que le process vocal
    # (séparé de l'API) voie le même état.
    "files": True,
}


class PermissionStore:
    """Permissions runtime accordées par l'utilisateur depuis l'UI.

    Persistées dans config/runtime_permissions.json pour être partagées
    entre le process API et le process vocal.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (CONFIG_DIR / "runtime_permissions.json")
        self._state: dict[str, bool] = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for key in _KEYS:
            if key in data:
                self._state[key] = bool(data[key])

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._state, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def get(self, key: str) -> bool:
        # Relecture disque : le process vocal voit les PATCH de l'API.
        self._load()
        return self._state.get(key, True)

    def set(self, key: str, value: bool) -> None:
        if key not in _KEYS:
            return
        self._load()
        self._state[key] = value
        self._save()

    def all(self) -> dict[str, bool]:
        self._load()
        return dict(self._state)


permissions = PermissionStore()
