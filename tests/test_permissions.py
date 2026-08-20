# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Tests unitaires de kernel.permissions (PermissionStore runtime)."""

from __future__ import annotations

from pathlib import Path

from jarvis.kernel.permissions import PermissionStore


def test_defaults(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    assert store.get("microphone") is True
    assert store.get("screen") is False
    assert store.get("camera") is False
    assert store.get("files") is True


def test_set_known_key(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    store.set("camera", True)
    assert store.get("camera") is True


def test_set_unknown_key_ignored(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    store.set("unknown_perm", True)
    assert store.get("unknown_perm") is True
    assert "unknown_perm" not in store.all()


def test_get_unknown_defaults_true(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    assert store.get("not_tracked") is True


def test_all_returns_copy(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    snapshot = store.all()
    snapshot["microphone"] = False
    assert store.get("microphone") is True


def test_all_contains_all_known_keys(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    assert set(store.all()) == {"microphone", "screen", "camera", "files"}


def test_toggle_persists_on_instance(tmp_path: Path) -> None:
    store = PermissionStore(path=tmp_path / "perms.json")
    store.set("screen", True)
    store.set("screen", False)
    assert store.get("screen") is False


def test_persisted_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "perms.json"
    a = PermissionStore(path=path)
    a.set("files", False)
    b = PermissionStore(path=path)
    assert b.get("files") is False
    a.set("files", True)
    assert b.get("files") is True
