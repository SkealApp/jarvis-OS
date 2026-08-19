# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from loguru import logger

from jarvis.kernel.error_collector import collector  # jrv: autofix
from jarvis.kernel.settings import settings

router = APIRouter(prefix="/api/deezer")

_AUTH_URL = "https://connect.deezer.com/oauth/auth.php"
_TOKEN_URL = "https://connect.deezer.com/oauth/access_token.php"
_API_BASE = "https://api.deezer.com"
_PERMS = "basic_access,email,offline_access,listening_history,manage_library"

_UNCONFIGURED_HTML = (
    "<!doctype html><meta charset='utf-8'>"
    "<body style='font-family:system-ui;background:#0e0e12;color:#e8e8ec;"
    "padding:48px;max-width:560px;margin:auto'>"
    "<h2>Deezer non configuré</h2>"
    "<p>Deezer n'accepte plus les nouvelles inscriptions d'app pour le moment.</p>"
    "<p><b>Solution : utilise ton cookie ARL</b></p>"
    "<ol>"
    "<li>Connecte-toi sur <a style='color:#7aa2ff' href='https://www.deezer.com' "
    "target='_blank'>deezer.com</a></li>"
    "<li>Ouvre les DevTools (F12) → <b>Application</b> → <b>Cookies</b> → "
    "<code>https://www.deezer.com</code></li>"
    "<li>Copie la valeur du cookie <b>arl</b></li>"
    "<li>Colle-la dans ton <code>.env</code> : <code>DEEZER_ARL=valeur_ici</code></li>"
    "<li>Redémarre Jarvis — Deezer sera connecté automatiquement.</li>"
    "</ol>"
    "<p><a style='color:#7aa2ff' href='/capabilities#integrations'>← Retour aux capacités</a></p>"
    "</body>"
)


def _token_path() -> Path:
    return Path(settings.deezer_token_path)


def _load_token() -> dict | None:
    p = _token_path()
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _save_token(data: dict) -> None:
    _token_path().parent.mkdir(parents=True, exist_ok=True)
    _token_path().write_text(json.dumps(data))


def _get_arl() -> str | None:
    """Retourne le cookie ARL depuis les settings (priorité sur OAuth)."""
    arl = settings.deezer_arl.get_secret_value().strip()
    return arl if arl else None


def _get_access_token() -> str | None:
    """Retourne l'access token OAuth, ou None si on utilise l'ARL."""
    token = _load_token()
    return token.get("access_token") if token else None


def _is_connected() -> bool:
    """Vrai si Deezer est authentifié via ARL ou via OAuth."""
    return bool(_get_arl() or _get_access_token())


def _arl_headers() -> dict:
    """Headers HTTP avec cookie ARL pour l'API Deezer."""
    return {"Cookie": f"arl={_get_arl()}", "User-Agent": "Mozilla/5.0"}


async def _api_get(path: str, params: dict | None = None) -> dict:
    """Appel GET vers l'API Deezer publique, avec ARL ou access_token."""
    arl = _get_arl()
    token = _get_access_token()

    if arl:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_API_BASE}{path}",
                params=params or {},
                headers=_arl_headers(),
            )
        resp.raise_for_status()
        return resp.json()
    elif token:
        p = dict(params or {})
        p["access_token"] = token
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_API_BASE}{path}", params=p)
        resp.raise_for_status()
        return resp.json()
    return {}


# ── OAuth ─────────────────────────────────────────────────────


@router.get("/auth")
async def deezer_auth() -> Response:
    # Si ARL configuré, pas besoin d'OAuth
    if _get_arl():
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<body style='font-family:system-ui;background:#0e0e12;color:#e8e8ec;"
            "padding:48px;max-width:560px;margin:auto'>"
            "<h2 style='color:#7aa2ff'>✓ Deezer connecté via ARL</h2>"
            "<p>Ton cookie ARL est configuré dans <code>.env</code>. "
            "Deezer est prêt à l'emploi, aucune app OAuth nécessaire.</p>"
            "<p><a style='color:#7aa2ff' href='/capabilities#integrations'>← Retour aux capacités</a></p>"
            "</body>",
            status_code=200,
        )
    if not settings.deezer_app_id or not settings.deezer_app_secret.get_secret_value():
        return HTMLResponse(_UNCONFIGURED_HTML, status_code=400)
    params = {
        "app_id": settings.deezer_app_id,
        "redirect_uri": settings.deezer_redirect_uri,
        "perms": _PERMS,
    }
    return RedirectResponse(f"{_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def deezer_callback(
    code: str | None = None, error_reason: str | None = None
) -> RedirectResponse:
    if error_reason or not code:
        logger.error("Deezer OAuth error", error=error_reason)
        return RedirectResponse("/?deezer_error=1")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _TOKEN_URL,
            params={
                "app_id": settings.deezer_app_id,
                "secret": settings.deezer_app_secret.get_secret_value(),
                "code": code,
                "output": "json",
            },
        )
        if not resp.is_success:
            logger.error("Deezer token fetch failed", status=resp.status_code)
            return RedirectResponse("/?deezer_error=1")

        data = resp.json()
        _save_token({"access_token": data["access_token"]})
        logger.info("Deezer token saved")

    return RedirectResponse("/?deezer_ok=1")


# ── Token for frontend SDK ─────────────────────────────────────


@router.get("/token")
async def get_token() -> JSONResponse:
    # Retourne l'access_token OAuth si dispo, sinon indique le mode ARL
    arl = _get_arl()
    token = _get_access_token()
    return JSONResponse({"token": token, "arl_mode": bool(arl)})


# ── ARL connect (POST /api/deezer/arl) ────────────────────────


@router.post("/arl")
async def set_arl(request: Request) -> JSONResponse:
    """Enregistre un cookie ARL Deezer (alternative à OAuth)."""
    body = await request.json()
    arl = (body.get("arl") or "").strip()
    if not arl:
        return JSONResponse({"ok": False, "error": "ARL vide"}, status_code=400)

    # Vérification rapide : appel à l'API avec cet ARL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_API_BASE}/user/me",
                headers={"Cookie": f"arl={arl}", "User-Agent": "Mozilla/5.0"},
            )
        data = resp.json()
        if data.get("error") or not data.get("id"):
            return JSONResponse({"ok": False, "error": "ARL invalide ou expiré"}, status_code=401)
        logger.info("Deezer ARL validé pour user %s", data.get("name", "?"))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    # Sauvegarde dans le fichier de token (champ dédié)
    existing = _load_token() or {}
    existing["arl"] = arl
    _save_token(existing)

    # Note : pour la persistance au redémarrage, l'utilisateur doit aussi mettre à jour .env
    return JSONResponse({"ok": True, "message": "ARL enregistré. Ajoute DEEZER_ARL=... dans .env pour persister."})


# ── Player state ──────────────────────────────────────────────


async def _get_player_state() -> dict:
    if not _is_connected():
        return {"connected": False}

    try:
        data = await _api_get("/user/me/history", {"limit": 1})
    except httpx.TimeoutException:
        collector.error("JRV-API-001", "JRV-API-001")
        logger.debug("Deezer player timeout")
        return {"connected": True, "is_playing": False, "track": None}
    except Exception as e:
        collector.error("JRV-API-001", "JRV-API-001", cause=e)
        logger.warning("Deezer player request error", error=str(e))
        return {"connected": False}

    tracks = data.get("data", [])
    if not tracks:
        return {"connected": True, "is_playing": False, "track": None}

    t = tracks[0]
    album = t.get("album") or {}
    return {
        "connected": True,
        "is_playing": False,
        "track": t.get("title", ""),
        "artist": (t.get("artist") or {}).get("name", ""),
        "album": album.get("title", ""),
        "album_art": album.get("cover_medium") or None,
        "progress_ms": 0,
        "duration_ms": t.get("duration", 0) * 1000,
    }


@router.get("/player")
async def get_player() -> JSONResponse:
    return JSONResponse(await _get_player_state())


# ── Controls ──────────────────────────────────────────────────


async def _action(method: str, endpoint: str) -> JSONResponse:
    if not _is_connected():
        return JSONResponse({"ok": False}, status_code=401)

    arl = _get_arl()
    token = _get_access_token()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            fn = getattr(client, method)
            if arl:
                resp = await fn(
                    f"{_API_BASE}/user/me/player/{endpoint}",
                    headers=_arl_headers(),
                )
            else:
                resp = await fn(
                    f"{_API_BASE}/user/me/player/{endpoint}",
                    params={"access_token": token},
                )
        return JSONResponse({"ok": resp.status_code in (200, 204)})
    except (httpx.TimeoutException, httpx.RequestError) as e:
        collector.error("JRV-API-001", "JRV-API-001", cause=e)
        logger.warning("Deezer action error", endpoint=endpoint, error=str(e))
        return JSONResponse({"ok": False})


@router.post("/play")
async def play() -> JSONResponse:
    return await _action("put", "play")


@router.post("/pause")
async def pause() -> JSONResponse:
    return await _action("put", "pause")


@router.post("/next")
async def next_track() -> JSONResponse:
    return await _action("post", "next")


@router.post("/previous")
async def previous_track() -> JSONResponse:
    return await _action("post", "previous")
