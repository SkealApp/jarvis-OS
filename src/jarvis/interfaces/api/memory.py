# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from jarvis.kernel.http_errors import raise_api_error
from jarvis.kernel.settings import settings
from jarvis.providers.memory.schemas import FactStatus

router = APIRouter()


class _ContentBody(BaseModel):
    content: str


class _ThemeBody(BaseModel):
    name: str
    color: str


class _AssignThemeBody(BaseModel):
    theme: str | None = None


class _TopicCreateBody(BaseModel):
    name: str
    content: str = ""
    theme: str | None = None


class _CorrectionBody(BaseModel):
    target_fact_id: str
    new_status: str | None = None
    new_object: str | None = None
    new_confidence: float | None = None
    correction_text: str = ""


def _mem_dir(request: Request) -> Path:  # noqa: ARG001

    return Path(settings.memory_dir)


@router.get("/api/memory/index")
async def get_memory_index(request: Request) -> dict:
    p = _mem_dir(request) / "MEMORY.md"
    return {"content": p.read_text(encoding="utf-8") if p.exists() else ""}


@router.put("/api/memory/index")
async def put_memory_index(body: _ContentBody, request: Request) -> dict:
    p = _mem_dir(request) / "MEMORY.md"
    p.write_text(body.content, encoding="utf-8")
    return {"ok": True}


@router.get("/api/memory/topics")
async def list_memory_topics(request: Request) -> list[dict]:
    topics_dir = _mem_dir(request) / "topics"
    if not topics_dir.exists():
        return []
    result = []
    for p in sorted(topics_dir.glob("*.md")):
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        result.append(
            {
                "name": p.name,
                "size": stat.st_size,
                "mtime": mtime.isoformat(),
            }
        )
    return result


@router.get("/api/memory/topics/{name}")
async def get_memory_topic(name: str, request: Request) -> dict:
    if "/" in name or "\\" in name or ".." in name:
        raise_api_error("JRV-API-004", 400, "Nom invalide")
    p = _mem_dir(request) / "topics" / name
    if not p.exists():
        raise_api_error("JRV-API-003", 404, "Fichier introuvable")
    return {"name": name, "content": p.read_text(encoding="utf-8")}


@router.put("/api/memory/topics/{name}")
async def put_memory_topic(name: str, body: _ContentBody, request: Request) -> dict:
    if "/" in name or "\\" in name or ".." in name:
        raise_api_error("JRV-API-004", 400, "Nom invalide")
    p = _mem_dir(request) / "topics" / name
    if not p.exists():
        raise_api_error("JRV-API-003", 404, "Fichier introuvable")
    p.write_text(body.content, encoding="utf-8")

    # Synchronise le VectorIndex si disponible
    vector_index = getattr(request.app.state, "vector_index", None)
    if vector_index is not None:

        async def _update_vector() -> None:
            await vector_index.add(
                doc_id=f"topic:{name}",
                text=body.content,
                metadata={"source": "topic", "filename": name},
            )
            await vector_index.persist()

        asyncio.create_task(_update_vector(), name=f"vector-update-{name}")

    return {"ok": True}


@router.post("/api/memory/topics")
async def create_memory_topic(body: _TopicCreateBody, request: Request) -> dict:
    """Crée un nouveau fichier thématique depuis l'UI (vue memory-graph).

    Indexe immédiatement dans le VectorIndex (même logique que le PUT) et
    rattache au thème demandé s'il existe.
    """
    from jarvis.capabilities.tools.memory import _is_invalid_filename
    from jarvis.providers.memory.themes import ThemeStore

    name = body.name.strip()
    if _is_invalid_filename(name):
        raise_api_error("JRV-API-004", 400, "Nom invalide (attendu : nom_fichier.md sans chemin)")
    topics_dir = _mem_dir(request) / "topics"
    p = topics_dir / name
    if p.exists():
        raise_api_error("JRV-API-004", 409, f"Le fichier '{name}' existe déjà.")
    topics_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")

    theme_applied: str | None = None
    if body.theme:
        store = ThemeStore(_mem_dir(request))
        canonical = store.resolve(body.theme)
        if canonical:
            store.assign(name, canonical)
            theme_applied = canonical

    vector_index = getattr(request.app.state, "vector_index", None)
    if vector_index is not None:

        async def _index_new() -> None:
            await vector_index.add(
                doc_id=f"topic:{name}",
                text=body.content,
                metadata={"source": "topic", "filename": name},
            )
            await vector_index.persist()

        asyncio.create_task(_index_new(), name=f"vector-create-{name}")

    return {"ok": True, "name": name, "theme": theme_applied}


@router.delete("/api/memory/topics/{name}")
async def delete_memory_topic(name: str, request: Request) -> dict:
    if "/" in name or "\\" in name or ".." in name:
        raise_api_error("JRV-API-004", 400, "Nom invalide")
    p = _mem_dir(request) / "topics" / name
    if not p.exists():
        raise_api_error("JRV-API-003", 404, "Fichier introuvable")
    p.unlink()

    # Détache le fichier de son thème éventuel
    from jarvis.providers.memory.themes import ThemeStore

    ThemeStore(_mem_dir(request)).assign(name, None)

    # Retire le document du VectorIndex si disponible
    vector_index = getattr(request.app.state, "vector_index", None)
    if vector_index is not None:

        async def _remove_vector() -> None:
            async with vector_index._lock:
                vector_index._remove_doc_locked(f"topic:{name}")
            await vector_index.persist()

        asyncio.create_task(_remove_vector(), name=f"vector-remove-{name}")

    return {"ok": True}


@router.get("/api/memory/facts")
async def list_memory_facts(
    request: Request,
    status: str = "active",
    category: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Liste les facts du Kernel (vraie fenêtre sur ce que Jarvis sait).

    Le miroir Markdown (memory_data/mirror/) reste la version *lisible*
    régénérée par AutoDream deep ; cet endpoint expose les facts BRUTS
    pour l'UI Atelier/Mémoire. Lecture seule — la correction passe par
    POST /api/memory/correct.
    """

    kernel = getattr(request.app.state, "memory_kernel", None)
    if kernel is None:
        raise_api_error("JRV-API-005", 503, "Memory Kernel non disponible.")
    try:
        st = FactStatus(status)
    except ValueError:
        raise_api_error(
            "JRV-API-004",
            400,
            f"Status invalide '{status}'. Valeurs : {[s.value for s in FactStatus]}",
        )
    facts = kernel.list_facts_by_status(st, limit=limit)
    if category:
        facts = [f for f in facts if f.category == category]
    return [
        {
            "id": f.id,
            "subject": f.subject,
            "predicate": f.predicate,
            "object": f.object,
            "category": f.category,
            "status": f.status.value,
            "confidence": round(f.confidence, 3),
            "support_count": f.support_count,
            "decay_policy": f.decay_policy.value,
            "importance": round(f.importance, 3),
            "last_seen_at": f.last_seen_at.isoformat(),
            "created_at": f.created_at.isoformat(),
        }
        for f in facts
    ]


# ── Thèmes (groupes colorés de fichiers thématiques) ─────────────────────────


@router.get("/api/memory/themes")
async def list_memory_themes(request: Request) -> dict:
    from jarvis.providers.memory.themes import ThemeStore

    return {"themes": ThemeStore(_mem_dir(request)).list_themes()}


@router.post("/api/memory/themes")
async def upsert_memory_theme(body: _ThemeBody, request: Request) -> dict:
    from jarvis.providers.memory.themes import ThemeStore

    try:
        ThemeStore(_mem_dir(request)).upsert_theme(body.name, body.color)
    except ValueError as e:
        raise_api_error("JRV-API-004", 400, str(e))
    return {"ok": True, "name": body.name.strip(), "color": body.color}


@router.delete("/api/memory/themes/{name}")
async def delete_memory_theme(name: str, request: Request) -> dict:
    from jarvis.providers.memory.themes import ThemeStore

    if not ThemeStore(_mem_dir(request)).delete_theme(name):
        raise_api_error("JRV-API-003", 404, f"Thème '{name}' introuvable")
    return {"ok": True}


@router.put("/api/memory/topics/{name}/theme")
async def assign_topic_theme(name: str, body: _AssignThemeBody, request: Request) -> dict:
    """Rattache (ou détache si theme=null) un fichier thématique à un thème."""
    from jarvis.providers.memory.themes import ThemeStore

    if "/" in name or "\\" in name or ".." in name:
        raise_api_error("JRV-API-004", 400, "Nom invalide")
    if not (_mem_dir(request) / "topics" / name).exists():
        raise_api_error("JRV-API-003", 404, "Fichier introuvable")
    store = ThemeStore(_mem_dir(request))
    try:
        store.assign(name, body.theme)
    except ValueError as e:
        raise_api_error("JRV-API-004", 400, str(e))
    return {"ok": True, "name": name, "theme": body.theme}


@router.get("/api/memory/facts/{fact_id}")
async def get_memory_fact_detail(fact_id: str, request: Request) -> dict:
    """Détail complet d'un fact pour le panneau de la vue memory-graph.

    Inclut l'historique des observations et les contradictions connues
    (facts SUPERSEDED par celui-ci). Lecture seule.
    """

    kernel = getattr(request.app.state, "memory_kernel", None)
    if kernel is None:
        raise_api_error("JRV-API-005", 503, "Memory Kernel non disponible.")
    fact = kernel.get_fact(fact_id)
    if fact is None:
        raise_api_error("JRV-API-003", 404, f"Fact '{fact_id}' introuvable.")

    observations = [
        {
            "id": o.id,
            "event_id": o.event_id,
            "observation_type": o.observation_type.value,
            "confidence_delta": round(o.confidence_delta, 3),
            "created_at": o.created_at.isoformat(),
        }
        for o in kernel.list_observations(fact_id)
    ]

    # Contradictions connues : facts que celui-ci SUPERSEDE (même logique que
    # MemoryRetrieval._known_contradictions, inlinée pour rester lecture seule).
    contradictions = []
    from jarvis.providers.memory.schemas import RelationType

    for rel in kernel.list_relations(fact_id):
        if rel.relation_type != RelationType.SUPERSEDES or rel.from_fact_id != fact_id:
            continue
        old = kernel.get_fact(rel.to_fact_id)
        if old is not None:
            contradictions.append(
                {
                    "id": old.id,
                    "subject": old.subject,
                    "predicate": old.predicate,
                    "object": old.object,
                    "status": old.status.value,
                    "last_seen_at": old.last_seen_at.isoformat(),
                }
            )

    return {
        "id": fact.id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "object": fact.object,
        "category": fact.category,
        "status": fact.status.value,
        "confidence": round(fact.confidence, 3),
        "support_count": fact.support_count,
        "decay_policy": fact.decay_policy.value,
        "importance": round(fact.importance, 3),
        "created_at": fact.created_at.isoformat(),
        "last_seen_at": fact.last_seen_at.isoformat(),
        "observations": observations,
        "contradictions": contradictions,
    }


@router.get("/api/memory/graph")
async def get_memory_graph(request: Request, limit: int = 300) -> dict:
    """Graphe mémoire {nodes, links, meta} pour la vue memory-graph (D3).

    Léger par design : pas de contenu Markdown ni d'observations ici —
    le détail se charge au clic via /api/memory/facts/{id} et
    /api/memory/topics/{name}.
    """

    kernel = getattr(request.app.state, "memory_kernel", None)
    if kernel is None:
        raise_api_error("JRV-API-005", 503, "Memory Kernel non disponible.")
    from jarvis.providers.memory.graph import build_memory_graph

    topics_dir = _mem_dir(request) / "topics"
    limit = max(10, min(1000, limit))
    return await asyncio.to_thread(build_memory_graph, kernel, topics_dir, limit)


@router.post("/api/memory/correct")
async def correct_memory_fact(body: _CorrectionBody, request: Request) -> dict:
    """Applique une correction humaine sur un fact (§6.7).

    Délègue à kernel.apply_correction() qui trace un event human_correction
    en audit immuable et met à jour le fact. C'est l'UNIQUE chemin d'écriture
    légitime sur la mémoire depuis l'UI — le miroir Markdown reste lecture seule.
    """

    kernel = getattr(request.app.state, "memory_kernel", None)
    if kernel is None:
        raise_api_error("JRV-API-005", 503, "Memory Kernel non disponible.")

    new_status_enum: FactStatus | None = None
    if body.new_status:
        try:
            new_status_enum = FactStatus(body.new_status)
        except ValueError:
            raise_api_error(
                "JRV-API-004",
                400,
                f"Status invalide '{body.new_status}'. Valeurs : {[s.value for s in FactStatus]}",
            )

    event, fact = kernel.apply_correction(
        target_fact_id=body.target_fact_id,
        new_object=body.new_object,
        new_status=new_status_enum,
        new_confidence=body.new_confidence,
        correction_text=body.correction_text,
        source="ui_atelier",
    )
    return {
        "event_id": event.id,
        "fact_found": fact is not None,
        "fact": (
            {
                "id": fact.id,
                "status": fact.status.value,
                "object": fact.object,
                "confidence": round(fact.confidence, 3),
            }
            if fact
            else None
        ),
    }


@router.post("/api/memory/autodream")
async def trigger_autodream(request: Request) -> dict:
    import asyncio

    auto_dream = getattr(request.app.state, "auto_dream", None)
    if not auto_dream:
        raise_api_error("JRV-API-005", 503, "AutoDream non disponible")
    asyncio.create_task(
        auto_dream._run_micro_safe(user_message="[trigger manuel]", assistant_message=""),
        name="autodream-manual",
    )
    return {"triggered": True}


@router.post("/api/memory/trigger-deep")
async def trigger_deep(request: Request) -> dict:
    """Force une passe AutoDream.deep_analyze() sans attendre 3h du matin.

    Outil d'observation pour la phase d'activation (MOUVEMENT 2 option D) :
    permet de déclencher une ingestion batch à la demande, regarder ce qu'elle
    produit via scripts/observe_kernel_ingest.py, ajuster le prompt si bruit,
    re-déclencher — itération en minutes au lieu d'une nuit.

    Garde-fou : refuse si settings.ingest_deep_enabled=False. L'endpoint NE
    DOIT PAS contourner le flag de bascule, sinon on crée une porte
    d'ingestion qui shunte l'interrupteur principal.
    """

    if not settings.ingest_deep_enabled:
        raise_api_error(
            "JRV-API-005",
            503,
            "Ingestion deep désactivée (settings.ingest_deep_enabled=False). "
            "Flip le flag avant d'utiliser cet endpoint.",
        )

    auto_dream = getattr(request.app.state, "auto_dream", None)
    if not auto_dream:
        raise_api_error("JRV-API-005", 503, "AutoDream non disponible")

    asyncio.create_task(auto_dream.deep_analyze(), name="autodream-deep-manual")
    return {"triggered": True, "scope": "deep"}
