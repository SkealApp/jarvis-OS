# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Tests de la vue graphe mémoire : builder build_memory_graph() +
endpoints /api/memory/graph et /api/memory/facts/{id}.

Le builder est lecture seule — aucun test ne vérifie de suppression,
conformément aux invariants (jamais de DELETE sur facts/events).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.interfaces.api.memory import router
from jarvis.providers.memory.graph import build_memory_graph
from jarvis.providers.memory.kernel import MemoryKernel
from jarvis.providers.memory.schemas import (
    Fact,
    FactStatus,
    ObservationType,
    RelationType,
)

# ── Fixtures ──────────────────────────────────────────────────


def _make_kernel(tmp_path: Path) -> MemoryKernel:
    """Kernel avec : 1 fact actif musique, 1 fact SUPERSEDED lié (SUPERSEDES),
    1 fact actif sport sans relation."""
    kernel = MemoryKernel(tmp_path / "mem.db")
    kernel.insert_fact(
        Fact(
            id="fact_new",
            subject="barth",
            predicate="préfère",
            object="spotify",
            category="musique",
            importance=0.8,
        )
    )
    kernel.insert_fact(
        Fact(
            id="fact_old",
            subject="barth",
            predicate="préfère",
            object="deezer",
            category="musique",
            status=FactStatus.SUPERSEDED,
        )
    )
    kernel.insert_fact(
        Fact(
            id="fact_sport",
            subject="barth",
            predicate="pratique",
            object="course à pied",
            category="sport",
        )
    )
    kernel.link_facts("fact_new", "fact_old", RelationType.SUPERSEDES)
    return kernel


def _make_topics(tmp_path: Path) -> Path:
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()
    (topics_dir / "musique.md").write_text("# Musique\n\nSpotify.", encoding="utf-8")
    (topics_dir / "notion.md").write_text("# Notion\n\nTâches.", encoding="utf-8")
    return topics_dir


# ── build_memory_graph ────────────────────────────────────────


def test_graph_contains_facts_topics_and_relations(tmp_path: Path) -> None:
    kernel = _make_kernel(tmp_path)
    topics_dir = _make_topics(tmp_path)

    graph = build_memory_graph(kernel, topics_dir)
    node_ids = {n["id"] for n in graph["nodes"]}

    # Facts actifs présents
    assert "fact_new" in node_ids
    assert "fact_sport" in node_ids
    # Le fact SUPERSEDED lié par une relation est inclus (pour dessiner le lien rouge)
    assert "fact_old" in node_ids
    # Topics en nœuds distincts
    assert "topic:musique.md" in node_ids
    assert "topic:notion.md" in node_ids

    # Lien SUPERSEDES présent et typé
    sup = [lk for lk in graph["links"] if lk["type"] == "supersedes"]
    assert sup == [{"source": "fact_new", "target": "fact_old", "type": "supersedes"}]


def test_graph_topic_heuristic_links_by_category(tmp_path: Path) -> None:
    """Un topic est relié aux facts dont la category == nom du fichier (stem)."""
    kernel = _make_kernel(tmp_path)
    topics_dir = _make_topics(tmp_path)

    graph = build_memory_graph(kernel, topics_dir)
    topic_links = [lk for lk in graph["links"] if lk["type"] == "topic"]

    # musique.md ↔ facts de catégorie musique (actif ET superseded inclus)
    targets = {lk["target"] for lk in topic_links if lk["source"] == "topic:musique.md"}
    assert "fact_new" in targets
    # notion.md sans fact correspondant → nœud autonome, aucun lien
    assert not any(lk["source"] == "topic:notion.md" for lk in topic_links)


def test_graph_nodes_carry_render_metadata(tmp_path: Path) -> None:
    """Nodes légers : métadonnées de rendu, PAS de contenu Markdown."""
    kernel = _make_kernel(tmp_path)
    topics_dir = _make_topics(tmp_path)

    graph = build_memory_graph(kernel, topics_dir)
    fact_node = next(n for n in graph["nodes"] if n["id"] == "fact_new")
    assert fact_node["type"] == "fact"
    assert fact_node["category"] == "musique"
    assert fact_node["status"] == "active"
    assert 0.0 <= fact_node["confidence"] <= 1.0
    assert 0.0 <= fact_node["importance"] <= 1.0

    topic_node = next(n for n in graph["nodes"] if n["id"] == "topic:musique.md")
    assert topic_node["type"] == "topic"
    assert topic_node["filename"] == "musique.md"
    assert "content" not in topic_node  # léger : le détail se charge au clic

    assert graph["meta"]["topic_count"] == 2
    assert graph["meta"]["relation_count"] == 1


def test_graph_empty_memory(tmp_path: Path) -> None:
    kernel = MemoryKernel(tmp_path / "mem.db")
    graph = build_memory_graph(kernel, tmp_path / "no_topics")
    assert graph["nodes"] == []
    assert graph["links"] == []


# ── Endpoints API ─────────────────────────────────────────────


@pytest.fixture
def api(tmp_path: Path) -> TestClient:
    kernel = _make_kernel(tmp_path)
    _make_topics(tmp_path)
    # Une observation pour le détail (event requis par la FK)
    evt = kernel.log_event(type="user_message", source="test", content="J'utilise Spotify")
    kernel.record_observation("fact_new", evt.id, ObservationType.CONFIRM, 0.1)

    app = FastAPI()
    app.include_router(router)
    app.state.memory_kernel = kernel
    client = TestClient(app)
    # _mem_dir() lit settings.memory_dir → pointé sur le tmp_path du test
    with patch("jarvis.kernel.settings.settings.memory_dir", str(tmp_path)):
        yield client


def test_api_graph_returns_nodes_and_links(api: TestClient) -> None:
    r = api.get("/api/memory/graph")
    assert r.status_code == 200
    data = r.json()
    node_ids = {n["id"] for n in data["nodes"]}
    assert "fact_new" in node_ids
    assert "topic:musique.md" in node_ids
    assert any(lk["type"] == "supersedes" for lk in data["links"])
    assert data["meta"]["fact_count"] >= 2


def test_api_graph_503_without_kernel(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.get("/api/memory/graph")
    assert r.status_code == 503


def test_api_fact_detail_with_observations_and_contradictions(api: TestClient) -> None:
    r = api.get("/api/memory/facts/fact_new")
    assert r.status_code == 200
    f = r.json()
    assert f["subject"] == "barth"
    assert f["object"] == "spotify"
    # Historique des observations
    assert len(f["observations"]) == 1
    assert f["observations"][0]["observation_type"] == "confirm"
    # Contradiction connue : le fact SUPERSEDED lié
    assert len(f["contradictions"]) == 1
    assert f["contradictions"][0]["id"] == "fact_old"
    assert f["contradictions"][0]["object"] == "deezer"


def test_api_fact_detail_404_unknown(api: TestClient) -> None:
    r = api.get("/api/memory/facts/fact_inconnu")
    assert r.status_code == 404
