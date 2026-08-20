# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Client Strava OAuth2 — gère le refresh automatique du token (expire toutes les 6h).

Variables d'env requises :
  STRAVA_CLIENT_ID     — ID de l'application Strava (developers.strava.com)
  STRAVA_CLIENT_SECRET — Secret de l'application (jamais logué ni exposé au frontend)
  STRAVA_REFRESH_TOKEN — Token de refresh OAuth2 (obtenu via le premier flow OAuth)

Rolling refresh token : si Strava renvoie un nouveau refresh_token lors du refresh,
il est persisté dans config/strava_token.json et utilisé pour les prochains appels.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from loguru import logger

STRAVA_API_BASE = "https://www.strava.com/api/v3"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
_TOKEN_CACHE_PATH = Path("config/strava_token.json")


class StravaClient:
    """Client Strava avec refresh automatique et backoff sur 429."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_token: str | None = None
        self._athlete_id: int | None = None
        self._load_cache()

    # ── Cache local ─────────────────────────────────────────────────────────

    def _load_cache(self) -> None:
        """Charge le token depuis les settings (source de vérité) puis le cache."""
        # Import ici pour éviter un import circulaire au démarrage
        from jarvis.kernel.settings import settings

        rt = settings.strava_refresh_token.get_secret_value().strip()
        if rt:
            self._refresh_token = rt

        if _TOKEN_CACHE_PATH.exists():
            try:
                data = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
                self._access_token = data.get("access_token")
                self._expires_at = float(data.get("expires_at", 0.0))
                # Rolling refresh token : priorité au cache si plus récent
                if data.get("refresh_token"):
                    self._refresh_token = data["refresh_token"]
                self._athlete_id = data.get("athlete_id")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Strava token cache illisible : %s", exc)

    def _save_cache(self, token_data: dict) -> None:
        """Persiste le token (jamais le client_secret) dans le cache local."""
        _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        safe = {
            "access_token": token_data["access_token"],
            "expires_at": token_data["expires_at"],
            "refresh_token": token_data.get("refresh_token", self._refresh_token),
        }
        if self._athlete_id is not None:
            safe["athlete_id"] = self._athlete_id
        _TOKEN_CACHE_PATH.write_text(json.dumps(safe, indent=2), encoding="utf-8")

    # ── Token lifecycle ──────────────────────────────────────────────────────

    async def _do_refresh(self) -> None:
        """Échange le refresh_token contre un nouvel access_token.

        Lève RuntimeError si les credentials ne sont pas configurés.
        """
        from jarvis.kernel.settings import settings

        if not self._refresh_token:
            raise RuntimeError(
                "STRAVA_REFRESH_TOKEN non configuré — "
                "obtiens-le via developers.strava.com et ajoute-le dans .env."
            )

        client_id = settings.strava_client_id.strip()
        # Ne jamais loguer client_secret
        client_secret = settings.strava_client_secret.get_secret_value().strip()

        if not client_id or not client_secret:
            raise RuntimeError(
                "STRAVA_CLIENT_ID ou STRAVA_CLIENT_SECRET manquant dans .env."
            )

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                STRAVA_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
            )
            if resp.status_code == 401:
                raise RuntimeError(
                    "Strava : refresh_token invalide ou révoqué. "
                    "Réautorise l'accès sur developers.strava.com."
                )
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        self._expires_at = float(data["expires_at"])

        # Rolling refresh token
        if "refresh_token" in data and data["refresh_token"] != self._refresh_token:
            logger.debug("Strava : nouveau refresh_token reçu, mise à jour du cache.")
            self._refresh_token = data["refresh_token"]

        self._save_cache(data)
        logger.debug(
            "Strava token refreshed — expire le %s",
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._expires_at)),
        )

    async def _get_token(self) -> str:
        """Retourne un access_token valide, en le rafraîchissant si nécessaire."""
        # Tampon de 60 s pour absorber la latence réseau
        if not self._access_token or time.time() >= self._expires_at - 60:
            await self._do_refresh()
        return self._access_token  # type: ignore[return-value]

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    async def _get(
        self,
        path: str,
        params: dict | None = None,
        *,
        _retry_on_401: bool = True,
    ) -> dict | list:
        """GET authentifié vers l'API Strava.

        - Retry une fois sur 401 (token invalide → force refresh).
        - Lève RuntimeError sur 429 (rate limit) avec le délai de retry.
        """
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{STRAVA_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
            )

        if resp.status_code == 401 and _retry_on_401:
            logger.debug("Strava 401 — force refresh et retry.")
            self._expires_at = 0.0
            return await self._get(path, params, _retry_on_401=False)

        if resp.status_code == 429:
            reset_at = resp.headers.get("X-RateLimit-Reset", "")
            wait = max(0, int(reset_at) - int(time.time())) if reset_at else 60
            raise RuntimeError(
                f"Strava rate limit dépassé. Réessaie dans {wait}s "
                f"(limite : {resp.headers.get('X-RateLimit-Limit', '?')} req/15min)."
            )

        resp.raise_for_status()
        return resp.json()

    # ── Endpoints publics ────────────────────────────────────────────────────

    async def get_activities(
        self,
        per_page: int = 10,
        page: int = 1,
        before: int | None = None,
        after: int | None = None,
    ) -> list[dict]:
        """Liste des activités de l'athlète (ordre antéchronologique)."""
        params: dict = {"per_page": min(per_page, 200), "page": page}
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after
        result = await self._get("/athlete/activities", params)
        return result if isinstance(result, list) else []

    async def get_activity(self, activity_id: int) -> dict:
        """Détail complet d'une activité (inclut map.polyline)."""
        result = await self._get(f"/activities/{activity_id}")
        return result if isinstance(result, dict) else {}

    async def get_activity_streams(
        self,
        activity_id: int,
        keys: list[str] | None = None,
    ) -> dict:
        """Streams GPS/altitude/vitesse/FC d'une activité.

        Retourne un dict {stream_type: {data: [...], ...}}.
        """
        if keys is None:
            keys = ["latlng", "altitude", "velocity_smooth", "heartrate", "time"]
        result = await self._get(
            f"/activities/{activity_id}/streams",
            {"keys": ",".join(keys), "key_by_type": "true"},
        )
        return result if isinstance(result, dict) else {}

    async def get_athlete(self) -> dict:
        """Profil de l'athlète authentifié."""
        result = await self._get("/athlete")
        if isinstance(result, dict):
            self._athlete_id = result.get("id")
        return result if isinstance(result, dict) else {}

    async def get_athlete_stats(self) -> dict:
        """Totaux récents/YTD/all-time de l'athlète."""
        if self._athlete_id is None:
            athlete = await self.get_athlete()
            self._athlete_id = athlete.get("id")
        if not self._athlete_id:
            raise RuntimeError("Impossible de récupérer l'ID athlète Strava.")
        result = await self._get(f"/athletes/{self._athlete_id}/stats")
        return result if isinstance(result, dict) else {}


# Singleton partagé par le tool et l'API router
strava_client = StravaClient()
