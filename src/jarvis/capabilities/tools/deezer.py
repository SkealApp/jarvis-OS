# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""DeezerTool — outil vocal pour contrôler Deezer via l'ARL cookie.

Fonctionnalités :
  - Rechercher et lancer un morceau, une playlist ou un album (ouvre dans le navigateur)
  - Contrôles play/pause/next/previous via les touches média Windows
  - Lister, créer et alimenter des playlists utilisateur (nécessite l'ARL)
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from loguru import logger

from jarvis.capabilities.tools.base import Tool, ToolResult
from jarvis.kernel.settings import settings

_API_BASE = "https://api.deezer.com"


def _arl() -> str:
    """Retourne l'ARL depuis les settings."""
    return settings.deezer_arl.get_secret_value().strip()


def _arl_headers() -> dict:
    return {
        "Cookie": f"arl={_arl()}",
        "User-Agent": "Mozilla/5.0",
    }


def _has_arl() -> bool:
    return bool(_arl())


def _media_key(key: str) -> None:
    """Simule une touche média Windows (play/pause/next/prev) via ctypes."""
    import ctypes

    VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
    vk = VK.get(key)
    if vk is None:
        return
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


async def _search(query: str, type_: str, limit: int = 5) -> list[dict]:
    """Recherche publique Deezer (pas d'auth requise)."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(
            f"{_API_BASE}/search/{type_}",
            params={"q": query, "limit": limit},
        )
        r.raise_for_status()
        return r.json().get("data", [])


async def _get_user_id() -> str | None:
    """Récupère l'ID utilisateur Deezer via l'ARL."""
    if not _has_arl():
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{_API_BASE}/user/me", headers=_arl_headers())
            data = r.json()
            return str(data.get("id", "")) or None
    except Exception:
        return None


def _deezer_embed_url(resource: str, resource_id: str | int) -> str:
    """Construit l'URL du widget embed Deezer (track / playlist / album)."""
    return f"https://widget.deezer.com/widget/dark/{resource}/{resource_id}"


def _open_player(
    broadcast: Callable[[dict], None] | None,
    embed_url: str,
    label: str = "",
) -> None:
    """Ouvre le mini player Deezer flottant dans l'interface Jarvis via WebSocket.

    Si aucun broadcast disponible (tests unitaires), utilise webbrowser en fallback.
    """
    if broadcast is not None:
        broadcast({"type": "deezer_player", "embed_url": embed_url, "label": label})
    else:
        import webbrowser
        webbrowser.open(embed_url)


class DeezerTool(Tool):
    name = "deezer_control"
    description = (
        "Contrôle Deezer. Actions disponibles : "
        "'play' (reprendre la lecture), 'pause', 'toggle' (play/pause), "
        "'next' (piste suivante), 'previous' (piste précédente), "
        "'search_track' (chercher et ouvrir un morceau), "
        "'search_playlist' (chercher et ouvrir une playlist), "
        "'search_album' (chercher et ouvrir un album), "
        "'my_playlists' (lister tes playlists), "
        "'create_playlist' (créer une nouvelle playlist), "
        "'add_to_playlist' (ajouter un morceau à une playlist). "
        "Pour les recherches, fournir 'query'. "
        "Pour create_playlist, fournir 'name'. "
        "Pour add_to_playlist, fournir 'playlist_id' et 'track_id'."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "play",
                    "pause",
                    "toggle",
                    "next",
                    "previous",
                    "search_track",
                    "search_playlist",
                    "search_album",
                    "my_playlists",
                    "create_playlist",
                    "add_to_playlist",
                ],
                "description": "Action à effectuer.",
            },
            "query": {
                "type": "string",
                "description": "Terme de recherche (pour search_*).",
            },
            "name": {
                "type": "string",
                "description": "Nom de la playlist à créer (pour create_playlist).",
            },
            "playlist_id": {
                "type": "string",
                "description": "ID de la playlist (pour add_to_playlist).",
            },
            "track_id": {
                "type": "string",
                "description": "ID du morceau à ajouter (pour add_to_playlist).",
            },
        },
        "required": ["action"],
    }

    def __init__(self, broadcast_event: Callable[[dict], None] | None = None) -> None:
        self._broadcast = broadcast_event

    async def execute(self, **kwargs: object) -> ToolResult:
        action = str(kwargs.get("action", ""))
        query = str(kwargs.get("query", ""))
        name = str(kwargs.get("name", ""))
        playlist_id = str(kwargs.get("playlist_id", ""))
        track_id = str(kwargs.get("track_id", ""))

        # ── Contrôles média (touches Windows, aucune auth requise) ────────────

        if action == "play":
            _media_key("play_pause")
            return ToolResult(content="Deezer : lecture reprise.")

        if action == "pause":
            _media_key("play_pause")
            return ToolResult(content="Deezer : lecture en pause.")

        if action == "toggle":
            _media_key("play_pause")
            return ToolResult(content="Deezer : play/pause basculé.")

        if action == "next":
            _media_key("next")
            return ToolResult(content="Deezer : piste suivante.")

        if action == "previous":
            _media_key("previous")
            return ToolResult(content="Deezer : piste précédente.")

        # ── Recherches & ouverture ─────────────────────────────────────────────

        if action == "search_track":
            if not query:
                return ToolResult(content="'query' requis pour search_track.", is_error=True)
            try:
                results = await _search(query, "track", limit=5)
            except Exception as e:
                return ToolResult(content=f"Erreur recherche Deezer : {e}", is_error=True)
            if not results:
                return ToolResult(content=f"Aucun morceau trouvé pour « {query} ».", is_error=True)
            track = results[0]
            title = track.get("title", query)
            artist = (track.get("artist") or {}).get("name", "")
            track_id = track.get("id", "")
            embed = _deezer_embed_url("track", track_id)
            _open_player(self._broadcast, embed, f"{title} — {artist}")
            logger.info("Deezer: lecture morceau '%s' (id=%s)", title, track_id)
            return ToolResult(content=f"Je lance « {title} » par {artist}.")

        if action == "search_playlist":
            if not query:
                return ToolResult(content="'query' requis pour search_playlist.", is_error=True)
            try:
                results = await _search(query, "playlist", limit=5)
            except Exception as e:
                return ToolResult(content=f"Erreur recherche Deezer : {e}", is_error=True)
            if not results:
                return ToolResult(
                    content=f"Aucune playlist trouvée pour « {query} ».", is_error=True
                )
            pl = results[0]
            pl_name = pl.get("title", query)
            pl_id = pl.get("id", "")
            embed = _deezer_embed_url("playlist", pl_id)
            _open_player(self._broadcast, embed, f"Playlist : {pl_name}")
            logger.info("Deezer: lecture playlist '%s' (id=%s)", pl_name, pl_id)
            return ToolResult(content=f"Je lance la playlist « {pl_name} ».")

        if action == "search_album":
            if not query:
                return ToolResult(content="'query' requis pour search_album.", is_error=True)
            try:
                results = await _search(query, "album", limit=5)
            except Exception as e:
                return ToolResult(content=f"Erreur recherche Deezer : {e}", is_error=True)
            if not results:
                return ToolResult(
                    content=f"Aucun album trouvé pour « {query} ».", is_error=True
                )
            album = results[0]
            album_name = album.get("title", query)
            artist = (album.get("artist") or {}).get("name", "")
            album_id = album.get("id", "")
            embed = _deezer_embed_url("album", album_id)
            _open_player(self._broadcast, embed, f"{album_name} — {artist}")
            logger.info("Deezer: lecture album '%s' (id=%s)", album_name, album_id)
            return ToolResult(
                content=f"Je lance l'album « {album_name} » de {artist}."
            )

        # ── Actions utilisateur (ARL requis) ──────────────────────────────────

        if action == "my_playlists":
            if not _has_arl():
                return ToolResult(
                    content="ARL Deezer non configuré. Ajoute DEEZER_ARL dans .env.",
                    is_error=True,
                )
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.get(
                        f"{_API_BASE}/user/me/playlists",
                        headers=_arl_headers(),
                        params={"limit": 20},
                    )
                    r.raise_for_status()
                    playlists = r.json().get("data", [])
            except Exception as e:
                return ToolResult(content=f"Erreur récupération playlists : {e}", is_error=True)

            if not playlists:
                return ToolResult(content="Aucune playlist trouvée dans ton compte Deezer.")

            names = [f"• {p.get('title', '?')} ({p.get('nb_tracks', 0)} titres)" for p in playlists]
            return ToolResult(content="Tes playlists Deezer :\n" + "\n".join(names))

        if action == "create_playlist":
            if not _has_arl():
                return ToolResult(
                    content="ARL Deezer non configuré. Ajoute DEEZER_ARL dans .env.",
                    is_error=True,
                )
            if not name:
                return ToolResult(content="'name' requis pour create_playlist.", is_error=True)
            user_id = await _get_user_id()
            if not user_id:
                return ToolResult(
                    content="Impossible de récupérer l'ID utilisateur Deezer (ARL invalide ?).",
                    is_error=True,
                )
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(
                        f"{_API_BASE}/user/{user_id}/playlists",
                        headers=_arl_headers(),
                        params={"title": name},
                    )
                    r.raise_for_status()
                    data = r.json()
                    pl_id = data.get("id", "")
            except Exception as e:
                return ToolResult(content=f"Erreur création playlist : {e}", is_error=True)

            embed = _deezer_embed_url("playlist", pl_id)
            _open_player(self._broadcast, embed, f"Playlist : {name}")
            return ToolResult(
                content=f"Playlist « {name} » créée (ID: {pl_id}). Je l'ouvre dans le player."
            )

        if action == "add_to_playlist":
            if not _has_arl():
                return ToolResult(
                    content="ARL Deezer non configuré. Ajoute DEEZER_ARL dans .env.",
                    is_error=True,
                )
            if not playlist_id or not track_id:
                return ToolResult(
                    content="'playlist_id' et 'track_id' requis pour add_to_playlist.",
                    is_error=True,
                )
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(
                        f"{_API_BASE}/playlist/{playlist_id}/tracks",
                        headers=_arl_headers(),
                        params={"songs": track_id},
                    )
                    ok = r.status_code in (200, 201) or r.json() is True
            except Exception as e:
                return ToolResult(content=f"Erreur ajout morceau : {e}", is_error=True)

            if ok:
                return ToolResult(
                    content=f"Morceau {track_id} ajouté à la playlist {playlist_id}."
                )
            return ToolResult(
                content=f"Impossible d'ajouter le morceau (statut {r.status_code}).",
                is_error=True,
            )

        return ToolResult(content=f"Action inconnue : {action}", is_error=True)
