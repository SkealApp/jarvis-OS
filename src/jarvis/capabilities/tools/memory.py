# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.capabilities.tools.base import Tool, ToolResult
from jarvis.kernel.contracts import FTSIndex, TopicStore, VectorIndex
from jarvis.kernel.error_collector import collector  # jrv: autofix
from jarvis.kernel.settings import settings

if TYPE_CHECKING:
    from jarvis.providers.memory.retrieval import MemoryRetrieval


def _is_invalid_filename(filename: str) -> bool:
    """Vérifie qu'un nom de fichier topic est sûr (pas de path traversal)."""
    return "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".md")


class MemoryTopicWriteTool(Tool):
    """Écrit, met à jour ou crée un fichier de mémoire thématique."""

    name = "memory_write"
    description = (
        "Écrire ou mettre à jour le contenu d'un fichier mémoire thématique (topics). "
        "Utiliser pour sauvegarder des préférences utilisateur, informations personnelles, "
        "contexte projet, etc. La mise à jour REMPLACE le contenu du fichier. "
        "Exemples existants : user_prefs.md, user_profile.md, spotify.md, notion.md. "
        "NOUVEAU DOMAINE : si aucun fichier existant ne correspond au sujet (ex: nutrition, "
        "sport, finances), tu PEUX créer un nouveau fichier thématique en passant "
        "create_if_missing=true avec un nom parlant en snake_case (ex: nutrition.md). "
        "Le nouveau fichier est immédiatement indexé pour memory_search. "
        "THÈME : les fichiers peuvent être groupés par thèmes colorés. À la création, "
        "passe theme=<nom> si un thème existant correspond au sujet (le résultat du tool "
        "liste les thèmes disponibles si le nom ne matche pas). Si aucun thème n'est "
        "adapté, omets le paramètre — un fichier sans thème est parfaitement valide. "
        "IMPORTANT : pour un fichier existant, lire le fichier d'abord (memory_load_topic) "
        "pour préserver l'existant."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Nom exact du fichier topic (ex: user_prefs.md, nutrition.md). "
                    "Doit se terminer par .md, sans chemin."
                ),
            },
            "content": {
                "type": "string",
                "description": "Nouveau contenu complet du fichier (Markdown).",
            },
            "create_if_missing": {
                "type": "boolean",
                "description": (
                    "Si true, crée le fichier s'il n'existe pas encore (nouveau domaine "
                    "thématique). Défaut : false — un fichier inexistant est une erreur."
                ),
            },
            "theme": {
                "type": "string",
                "description": (
                    "Optionnel : nom d'un thème EXISTANT auquel rattacher le fichier "
                    "(groupe coloré dans la vue mémoire). Omets si aucun thème n'est adapté."
                ),
            },
        },
        "required": ["filename", "content"],
    }

    def __init__(
        self,
        topics_dir: Path | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self._dir = topics_dir or (Path(settings.memory_dir) / "topics")
        self._vector_index = vector_index

    async def execute(
        self,
        filename: str,
        content: str,
        create_if_missing: bool = False,
        theme: str | None = None,
    ) -> ToolResult:
        if _is_invalid_filename(filename):
            return ToolResult(content="Nom de fichier invalide.", is_error=True)
        path = self._dir / filename
        created = not path.exists()
        if created and not create_if_missing:
            existing = [p.name for p in self._dir.glob("*.md")]
            return ToolResult(
                content=(
                    f"Fichier '{filename}' introuvable."
                    f" Fichiers disponibles : {', '.join(existing)}."
                    " Pour créer un nouveau domaine thématique, repasse l'appel avec"
                    " create_if_missing=true."
                ),
                is_error=True,
            )
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if self._vector_index is not None:
            await self._vector_index.add(
                doc_id=f"topic:{filename}",
                text=content,
                metadata={"source": "topic", "filename": filename},
            )
            await self._vector_index.persist()
        verb = "créée et indexée" if created else "mise à jour"
        theme_note = self._apply_theme(filename, theme)
        return ToolResult(
            content=f"Mémoire '{filename}' {verb} ({len(content)} caractères).{theme_note}"
        )

    def _apply_theme(self, filename: str, theme: str | None) -> str:
        """Rattache le fichier au thème demandé s'il existe (matching insensible à la casse).

        Thème inconnu → fichier laissé sans thème (comportement voulu), avec la
        liste des thèmes existants dans le message pour guider le prochain appel.
        """
        if not theme:
            return ""
        from jarvis.providers.memory.themes import ThemeStore

        store = ThemeStore(self._dir.parent)
        canonical = store.resolve(theme)
        if canonical is None:
            known = ", ".join(t["name"] for t in store.list_themes()) or "(aucun)"
            return (
                f" Thème '{theme}' inconnu — fichier laissé sans thème."
                f" Thèmes existants : {known}."
            )
        store.assign(filename, canonical)
        return f" Rattaché au thème « {canonical} »."


class MemoryLoadTopicTool(Tool):
    """Charge à la demande le contenu d'un fichier thématique mémoire."""

    name = "memory_load_topic"
    description = (
        "Charger le contenu complet d'un fichier mémoire thématique (topics) à la demande. "
        "Les fichiers thématiques ne sont PLUS préchargés dans le prompt — utilise cet outil "
        "lorsque tu as besoin de consulter le détail d'un sujet précis. "
        "Conseil : utilise d'abord `memory_search` pour identifier le bon fichier."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Nom exact du fichier topic à lire (ex: user_prefs.md).",
            },
        },
        "required": ["filename"],
    }

    def __init__(self, topic_store: TopicStore) -> None:
        self._store = topic_store

    async def execute(self, filename: str) -> ToolResult:
        if _is_invalid_filename(filename):
            return ToolResult(content="Nom de fichier invalide.", is_error=True)
        if not self._store.exists(filename):
            existing = ", ".join(self._store.list_all()) or "(aucun)"
            return ToolResult(
                content=f"Fichier '{filename}' introuvable. Fichiers disponibles : {existing}",
                is_error=True,
            )
        content = self._store.load(filename)
        return ToolResult(content=f"# {filename}\n\n{content}")


class MemorySearchTool(Tool):
    """Recherche dans les DEUX couches mémoire : facts structurés (Kernel FTS5)
    + topics/transcripts (VectorIndex sémantique).

    Chaque résultat porte une provenance explicite (`source: fact` + fact_id,
    ou `source: topic` + filename) — utilisée aussi pour mettre en évidence
    les nœuds correspondants dans la vue memory-graph (highlight_nodes).
    """

    name = "memory_search"
    description = (
        "Recherche dans TOUTE la mémoire en une seule requête : "
        "facts structurés (connaissances sujet-prédicat-objet avec confiance) "
        "ET fichiers thématiques + transcripts (recherche sémantique). "
        "Renvoie les résultats les plus pertinents avec leur provenance "
        "([fact:id] ou [topic:fichier]). "
        "Utiliser AVANT de répondre à toute question qui pourrait être couverte par "
        "la mémoire utilisateur. Enchaîner avec `memory_load_topic` si le détail "
        "complet d'un fichier est nécessaire."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Question ou mots-clés en langage naturel.",
            },
            "k": {
                "type": "integer",
                "description": "Nombre de résultats à renvoyer par couche (défaut : 5).",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        vector_index: VectorIndex,
        retrieval: MemoryRetrieval | None = None,
        broadcast_event: Callable[[dict], None] | None = None,
    ) -> None:
        self._index = vector_index
        self._retrieval = retrieval
        self._broadcast = broadcast_event

    async def execute(self, query: str, k: int = 5) -> ToolResult:
        if not query.strip():
            return ToolResult(content="Requête vide.", is_error=True)
        try:
            k_int = max(1, min(20, int(k)))
        except (TypeError, ValueError):
            collector.error("JRV-TOL-001", "JRV-TOL-001")
            k_int = 5

        # Couche topics/transcripts (sémantique) + couche facts (FTS5) en parallèle.
        # retrieval.retrieve est synchrone (sqlite3) → thread pour ne pas bloquer.
        vec_results = await self._index.search(query=query, k=k_int)
        scored_facts = []
        if self._retrieval is not None:
            scored_facts = await asyncio.to_thread(self._retrieval.retrieve, query, k_int)
            # Le fallback cold-start de retrieve() renvoie des facts récents avec
            # relevance=0 quand rien ne matche — hors-sujet ici, on les écarte.
            scored_facts = [sf for sf in scored_facts if sf.relevance > 0.0]

        fact_ids: list[str] = []
        topic_filenames: list[str] = []
        lines: list[str] = []

        if scored_facts:
            lines.append("== FACTS STRUCTURÉS ==")
            for sf in scored_facts:
                f = sf.fact
                fact_ids.append(f.id)
                entry = (
                    f"[fact:{f.id}] {f.subject} — {f.predicate} — {f.object} "
                    f"(catégorie {f.category}, confiance {f.confidence:.2f}, "
                    f"score={sf.score:.3f})"
                )
                if sf.contradictions:
                    olds = "; ".join(
                        f"{c.subject} {c.predicate} {c.object}" for c in sf.contradictions
                    )
                    entry += f"\n  ancienne valeur remplacée : {olds}"
                lines.append(entry)

        if vec_results:
            if lines:
                lines.append("")
            lines.append("== TOPICS & TRANSCRIPTS ==")
            for r in vec_results:
                meta = r.get("metadata", {})
                filename = meta.get("filename")
                doc_id = r.get("doc_id", "?")
                if filename and meta.get("source", "topic") == "topic":
                    topic_filenames.append(filename)
                    source = f"[topic:{filename}]"
                else:
                    source = f"[{doc_id}]"
                score = r.get("score", 0.0)
                text = r.get("text", "").strip()
                lines.append(f"{source} (score={score:.3f})\n{text}")

        if not lines:
            return ToolResult(content="Aucun résultat pertinent trouvé en mémoire.")

        self._emit_highlight(fact_ids, topic_filenames)
        return ToolResult(content="\n\n".join(lines))

    def _emit_highlight(self, fact_ids: list[str], topic_filenames: list[str]) -> None:
        """Met en évidence les nœuds utilisés dans la vue memory-graph (si affichée).

        Broadcast inoffensif si la vue n'est pas ouverte — le frontend l'ignore.
        Aucun tracking serveur de l'état de la vue.
        """
        if self._broadcast is None or not (fact_ids or topic_filenames):
            return
        try:
            self._broadcast(
                {
                    "type": "view_command",
                    "view_id": "memory-graph",
                    "command": "highlight_nodes",
                    "params": {
                        "fact_ids": fact_ids,
                        "topic_filenames": sorted(set(topic_filenames)),
                    },
                }
            )
        except Exception:  # noqa: BLE001 — le highlight ne doit jamais casser la recherche
            collector.error("JRV-TOL-001", "JRV-TOL-001")


class CrossSessionRecallTool(Tool):
    """Recherche dans les sessions passées par FTS5 + vectoriel.

    Permet à l'agent de rappeler explicitement des échanges antérieurs
    en combinant recherche plein texte exacte et recherche sémantique.
    """

    name = "session_recall"
    description = (
        "Recherche dans les sessions de conversation passées (FTS5 + sémantique). "
        "Retourne les extraits les plus pertinents des échanges précédents. "
        "Utilise pour retrouver ce qui a été dit lors de sessions antérieures, "
        "comme des décisions, préférences ou contexte de projets passés."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Question ou mots-clés à rechercher dans les sessions.",
            },
            "k": {
                "type": "integer",
                "description": "Nombre de résultats (défaut : 6).",
            },
        },
        "required": ["query"],
    }

    def __init__(self, fts_index: FTSIndex, vector_index: VectorIndex) -> None:
        self._fts = fts_index
        self._vector = vector_index

    async def execute(self, query: str, k: int = 6) -> ToolResult:  # type: ignore[override]
        import asyncio

        if not query.strip():
            return ToolResult(content="Requête vide.", is_error=True)
        k_int = max(1, min(20, int(k)))

        fts_results, vec_results = await asyncio.gather(
            self._fts.search(query, k=k_int),
            self._vector.search(query, k=k_int),
        )

        seen: set[str] = set()
        lines: list[str] = []
        for r in fts_results + vec_results:
            doc_id = r["doc_id"]
            if doc_id in seen:
                continue
            seen.add(doc_id)
            text = r["text"][:400].strip()
            score = r.get("score", 0.0)
            lines.append(f"[{doc_id}] (score={score:.3f})\n{text}")
            if len(lines) >= k_int:
                break

        if not lines:
            return ToolResult(content="Aucun résultat trouvé dans les sessions passées.")
        return ToolResult(content="\n\n---\n\n".join(lines))
