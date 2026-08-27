# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Construction du graphe mémoire pour la vue « cerveau » (memory-graph).

Produit le format D3 force-directed `{nodes: [...], links: [...]}` à partir :
- des facts ACTIFS du MemoryKernel (+ les facts SUPERSEDED référencés par une
  relation d'un fact actif, pour rendre visibles les contradictions résolues) ;
- des fact_relations entre facts inclus ;
- des fichiers thématiques Markdown (topics), reliés heuristiquement aux facts
  dont la `category` correspond au nom du fichier (ex: catégorie `spotify` →
  `spotify.md`). Sans correspondance, le topic reste un nœud autonome.

Lecture seule — aucun DELETE, aucun embedding : on respecte les invariants du
kernel (facts jamais supprimés, FTS5 seul pour la pertinence).

Léger par design : pas de contenu Markdown complet ici — le détail d'un nœud
se charge à la demande via /api/memory/facts/{id} et /api/memory/topics/{name}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.providers.memory.kernel import MemoryKernel, normalize
from jarvis.providers.memory.schemas import FactStatus
from jarvis.providers.memory.themes import ThemeStore

_MAX_LABEL = 60


def _fact_label(subject: str, predicate: str, obj: str) -> str:
    label = f"{subject} {predicate} {obj}"
    return label[: _MAX_LABEL - 1] + "…" if len(label) > _MAX_LABEL else label


def build_memory_graph(
    kernel: MemoryKernel,
    topics_dir: Path,
    max_facts: int = 300,
) -> dict[str, Any]:
    """Construit {nodes, links} pour D3. Nodes facts + topics, links relations + heuristique."""
    active = kernel.list_facts_by_status(FactStatus.ACTIVE, limit=max_facts)
    facts_by_id = {f.id: f for f in active}

    relations = kernel.list_all_relations()

    # Inclure les facts non-actifs référencés par une relation touchant un fact
    # actif (ex: l'ancien fact SUPERSEDED) — sinon les liens de contradiction
    # pointeraient vers des nœuds absents et ne se dessineraient jamais.
    for rel in relations:
        for fid in (rel.from_fact_id, rel.to_fact_id):
            if fid in facts_by_id:
                continue
            other = rel.to_fact_id if fid == rel.from_fact_id else rel.from_fact_id
            if other not in facts_by_id:
                continue
            extra = kernel.get_fact(fid)
            if extra is not None:
                facts_by_id[fid] = extra

    nodes: list[dict[str, Any]] = [
        {
            "id": f.id,
            "type": "fact",
            "label": _fact_label(f.subject, f.predicate, f.object),
            "subject": f.subject,
            "predicate": f.predicate,
            "object": f.object,
            "category": f.category,
            "status": f.status.value,
            "confidence": round(f.confidence, 3),
            "importance": round(f.importance, 3),
        }
        for f in facts_by_id.values()
    ]

    links: list[dict[str, Any]] = [
        {
            "source": rel.from_fact_id,
            "target": rel.to_fact_id,
            "type": rel.relation_type.value,
        }
        for rel in relations
        if rel.from_fact_id in facts_by_id and rel.to_fact_id in facts_by_id
    ]

    # Topics : nœuds distincts + lien heuristique category == nom du fichier.
    categories: dict[str, list[str]] = {}
    for f in facts_by_id.values():
        categories.setdefault(normalize(f.category), []).append(f.id)

    theme_store = ThemeStore(topics_dir.parent)
    topic_files = sorted(topics_dir.glob("*.md")) if topics_dir.exists() else []
    for p in topic_files:
        topic_id = f"topic:{p.name}"
        theme = theme_store.theme_of(p.name)
        nodes.append(
            {
                "id": topic_id,
                "type": "topic",
                "label": p.name,
                "filename": p.name,
                "size": p.stat().st_size,
                "theme": theme["name"] if theme else None,
                "color": theme["color"] if theme else None,
            }
        )
        for fact_id in categories.get(normalize(p.stem), []):
            links.append({"source": topic_id, "target": fact_id, "type": "topic"})

    return {
        "nodes": nodes,
        "links": links,
        "themes": theme_store.list_themes(),
        "meta": {
            "fact_count": len(facts_by_id),
            "topic_count": len(topic_files),
            "relation_count": sum(1 for lk in links if lk["type"] != "topic"),
        },
    }
