# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Tests des thèmes de fichiers thématiques (ThemeStore + endpoints + tool).

Un thème = groupe de fichiers .md avec une couleur choisie. Utilisé par la
vue memory-graph (couleur des hexagones) et par memory_write (rattachement
automatique à la création d'un nouveau fichier).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.capabilities.tools.memory import MemoryTopicWriteTool
from jarvis.interfaces.api.memory import router
from jarvis.providers.memory.graph import build_memory_graph
from jarvis.providers.memory.kernel import MemoryKernel
from jarvis.providers.memory.themes import ThemeStore

# ── ThemeStore ────────────────────────────────────────────────


def test_theme_store_upsert_and_list(tmp_path: Path) -> None:
    store = ThemeStore(tmp_path)
    store.upsert_theme("santé", "#36D399")
    store.upsert_theme("projets", "#4A9EFF")

    themes = store.list_themes()
    assert [t["name"] for t in themes] == ["projets", "santé"]
    assert themes[1]["color"] == "#36D399"
    assert themes[1]["files"] == []


def test_theme_store_invalid_color_rejected(tmp_path: Path) -> None:
    store = ThemeStore(tmp_path)
    for bad in ["rouge", "#36D39", "#GGGGGG", ""]:
        with pytest.raises(ValueError):
            store.upsert_theme("x", bad)


def test_theme_store_assign_and_theme_of(tmp_path: Path) -> None:
    store = ThemeStore(tmp_path)
    store.upsert_theme("santé", "#36D399")
    store.assign("nutrition.md", "santé")

    assert store.theme_of("nutrition.md") == {"name": "santé", "color": "#36D399"}
    assert store.theme_of("autre.md") is None
    assert store.list_themes()[0]["files"] == ["nutrition.md"]

    # Détacher
    store.assign("nutrition.md", None)
    assert store.theme_of("nutrition.md") is None


def test_theme_store_assign_unknown_theme_raises(tmp_path: Path) -> None:
    store = ThemeStore(tmp_path)
    with pytest.raises(ValueError):
        store.assign("x.md", "inexistant")


def test_theme_store_resolve_case_insensitive(tmp_path: Path) -> None:
    store = ThemeStore(tmp_path)
    store.upsert_theme("Santé", "#36D399")
    assert store.resolve("santé") == "Santé"
    assert store.resolve("SANTÉ") == "Santé"
    assert store.resolve("inconnu") is None


def test_theme_store_delete_detaches_files(tmp_path: Path) -> None:
    store = ThemeStore(tmp_path)
    store.upsert_theme("santé", "#36D399")
    store.assign("nutrition.md", "santé")

    assert store.delete_theme("santé") is True
    assert store.theme_of("nutrition.md") is None
    assert store.list_themes() == []
    assert store.delete_theme("santé") is False


# ── Graphe : couleur du thème sur les nœuds topics ────────────


def test_graph_topic_node_carries_theme_color(tmp_path: Path) -> None:
    kernel = MemoryKernel(tmp_path / "mem.db")
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()
    (topics_dir / "nutrition.md").write_text("# Nutrition\n", encoding="utf-8")
    (topics_dir / "libre.md").write_text("# Libre\n", encoding="utf-8")
    store = ThemeStore(tmp_path)
    store.upsert_theme("santé", "#36D399")
    store.assign("nutrition.md", "santé")

    graph = build_memory_graph(kernel, topics_dir)
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["topic:nutrition.md"]["theme"] == "santé"
    assert by_id["topic:nutrition.md"]["color"] == "#36D399"
    # Fichier sans thème : parfaitement valide, sans couleur imposée
    assert by_id["topic:libre.md"]["theme"] is None
    assert by_id["topic:libre.md"]["color"] is None
    # Les thèmes sont fournis au frontend dans le payload
    assert graph["themes"][0]["name"] == "santé"


# ── memory_write : rattachement automatique au thème ──────────


async def test_memory_write_create_with_existing_theme(tmp_path: Path) -> None:
    topics_dir = tmp_path / "topics"
    ThemeStore(tmp_path).upsert_theme("Santé", "#36D399")

    tool = MemoryTopicWriteTool(topics_dir=topics_dir)
    result = await tool.execute(
        filename="nutrition.md", content="# Nutrition\n", create_if_missing=True, theme="santé"
    )

    assert not result.is_error
    assert "Santé" in result.content  # matching insensible à la casse → nom canonique
    assert ThemeStore(tmp_path).theme_of("nutrition.md") == {"name": "Santé", "color": "#36D399"}


async def test_memory_write_create_with_unknown_theme_stays_unthemed(tmp_path: Path) -> None:
    """Thème inconnu → fichier créé SANS thème (pas d'erreur), thèmes listés."""
    topics_dir = tmp_path / "topics"
    ThemeStore(tmp_path).upsert_theme("projets", "#4A9EFF")

    tool = MemoryTopicWriteTool(topics_dir=topics_dir)
    result = await tool.execute(
        filename="cuisine.md", content="# Cuisine\n", create_if_missing=True, theme="gastronomie"
    )

    assert not result.is_error
    assert (topics_dir / "cuisine.md").exists()
    assert "sans thème" in result.content
    assert "projets" in result.content  # liste des thèmes existants pour guider le LLM
    assert ThemeStore(tmp_path).theme_of("cuisine.md") is None


# ── Endpoints API ─────────────────────────────────────────────


@pytest.fixture
def api(tmp_path: Path) -> TestClient:
    kernel = MemoryKernel(tmp_path / "mem.db")
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()
    (topics_dir / "existant.md").write_text("# Existant\n", encoding="utf-8")

    app = FastAPI()
    app.include_router(router)
    app.state.memory_kernel = kernel
    client = TestClient(app)
    with patch("jarvis.kernel.settings.settings.memory_dir", str(tmp_path)):
        yield client


def test_api_themes_crud(api: TestClient) -> None:
    # Création
    r = api.post("/api/memory/themes", json={"name": "santé", "color": "#36D399"})
    assert r.status_code == 200

    r = api.get("/api/memory/themes")
    assert r.status_code == 200
    assert r.json()["themes"][0] == {"name": "santé", "color": "#36D399", "files": []}

    # Couleur invalide
    r = api.post("/api/memory/themes", json={"name": "x", "color": "rouge"})
    assert r.status_code == 400

    # Suppression
    assert api.delete("/api/memory/themes/santé").status_code == 200
    assert api.delete("/api/memory/themes/santé").status_code == 404


def test_api_create_topic_with_theme(api: TestClient, tmp_path: Path) -> None:
    api.post("/api/memory/themes", json={"name": "santé", "color": "#36D399"})

    r = api.post(
        "/api/memory/topics",
        json={"name": "nutrition.md", "content": "# Nutrition\n", "theme": "santé"},
    )
    assert r.status_code == 200
    assert r.json()["theme"] == "santé"
    assert (tmp_path / "topics" / "nutrition.md").read_text(encoding="utf-8") == "# Nutrition\n"

    # Doublon → 409
    r = api.post("/api/memory/topics", json={"name": "nutrition.md", "content": "x"})
    assert r.status_code == 409

    # Nom invalide → 400
    r = api.post("/api/memory/topics", json={"name": "../evil.md", "content": "x"})
    assert r.status_code == 400


def test_api_assign_theme_to_topic(api: TestClient) -> None:
    api.post("/api/memory/themes", json={"name": "projets", "color": "#4A9EFF"})

    r = api.put("/api/memory/topics/existant.md/theme", json={"theme": "projets"})
    assert r.status_code == 200

    themes = api.get("/api/memory/themes").json()["themes"]
    assert themes[0]["files"] == ["existant.md"]

    # Détacher
    r = api.put("/api/memory/topics/existant.md/theme", json={"theme": None})
    assert r.status_code == 200
    assert api.get("/api/memory/themes").json()["themes"][0]["files"] == []

    # Thème inconnu → 400 ; fichier inconnu → 404
    assert api.put("/api/memory/topics/existant.md/theme", json={"theme": "zzz"}).status_code == 400
    r = api.put("/api/memory/topics/absent.md/theme", json={"theme": "projets"})
    assert r.status_code == 404


def test_api_delete_topic_detaches_theme(api: TestClient) -> None:
    api.post("/api/memory/themes", json={"name": "projets", "color": "#4A9EFF"})
    api.put("/api/memory/topics/existant.md/theme", json={"theme": "projets"})

    assert api.delete("/api/memory/topics/existant.md").status_code == 200
    assert api.get("/api/memory/themes").json()["themes"][0]["files"] == []
