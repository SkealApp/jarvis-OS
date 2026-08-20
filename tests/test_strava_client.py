"""Tests unitaires du client Strava — aucun appel réseau réel.

Couvre :
- Refresh de token expiré (appel POST /oauth/token)
- Retry automatique sur 401 (force refresh + 1 retry)
- Backoff / RuntimeError sur 429 (rate limit)
- Décodage de la Google Encoded Polyline
- Lecture du token depuis le cache JSON
- Refresh token rolling (Strava renvoie un nouveau refresh_token)
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


# ── Helpers polyline ────────────────────────────────────────────────────────

def _decode_poly(encoded: str) -> list[tuple[float, float]]:
    """Implémentation Python du décodeur — miroir de la version JS du client."""
    coords: list[tuple[float, float]] = []
    index, lat, lng = 0, 0, 0
    while index < len(encoded):
        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lat += ~(result >> 1) if (result & 1) else (result >> 1)
        result, shift = 0, 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        lng += ~(result >> 1) if (result & 1) else (result >> 1)
        coords.append((lat / 1e5, lng / 1e5))
    return coords


# ── Fixtures ────────────────────────────────────────────────────────────────

VALID_TOKEN_RESP = {
    "access_token": "at-new-123",
    "expires_at": int(time.time()) + 21600,  # +6h
    "refresh_token": "rt-new-456",
    "token_type": "Bearer",
}

ATHLETE_RESP = {"id": 9999, "firstname": "Test", "lastname": "User"}

ACTIVITY_RESP = {
    "id": 1234567,
    "name": "Morning Run",
    "type": "Run",
    "distance": 10000.0,
    "moving_time": 3600,
    "total_elevation_gain": 50.0,
    "average_heartrate": 155.0,
    "average_speed": 2.78,
    "start_date_local": "2026-08-19T07:30:00Z",
    "map": {
        "summary_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
    },
}


def _make_response(status: int, json_data: dict | list | None = None, headers: dict | None = None):
    """Crée un mock de httpx.Response."""
    resp = Mock()
    resp.status_code = status
    resp.headers = headers or {}
    if json_data is not None:
        resp.json = Mock(return_value=json_data)
    resp.raise_for_status = Mock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response
        req = Mock(spec=Request)
        raw_resp = Mock(spec=Response)
        raw_resp.status_code = status
        resp.raise_for_status.side_effect = HTTPStatusError("error", request=req, response=raw_resp)
    return resp


# ── Tests ────────────────────────────────────────────────────────────────────

class TestPolylineDecoder(unittest.TestCase):
    """Teste le décodage de la Google Encoded Polyline."""

    def test_known_polyline(self):
        # Polyline canonique des docs Google :
        # coords : (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)
        encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        coords = _decode_poly(encoded)
        self.assertEqual(len(coords), 3)
        self.assertAlmostEqual(coords[0][0], 38.5, places=4)
        self.assertAlmostEqual(coords[0][1], -120.2, places=4)
        self.assertAlmostEqual(coords[1][0], 40.7, places=4)
        self.assertAlmostEqual(coords[1][1], -120.95, places=4)
        self.assertAlmostEqual(coords[2][0], 43.252, places=3)
        self.assertAlmostEqual(coords[2][1], -126.453, places=3)

    def test_empty_polyline(self):
        self.assertEqual(_decode_poly(""), [])


class TestStravaClientTokenRefresh(unittest.IsolatedAsyncioTestCase):
    """Teste le refresh automatique du token."""

    def _get_fresh_client(self, tmp_path: Path | None = None):
        """Instancie StravaClient avec le cache pointant vers tmp_path."""
        from jarvis.capabilities.skills.strava import client as strava_module

        # Patch le chemin du cache pour éviter d'écrire dans config/
        if tmp_path:
            patcher = patch.object(strava_module, "_TOKEN_CACHE_PATH", tmp_path / "strava_token.json")
            patcher.start()
            self.addCleanup(patcher.stop)

        c = strava_module.StravaClient.__new__(strava_module.StravaClient)
        c._access_token = None
        c._expires_at = 0.0
        c._refresh_token = "rt-initial"
        c._athlete_id = None
        return c

    async def test_refresh_called_when_token_expired(self):
        """Si expires_at est dans le passé, _do_refresh est appelé."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            client = self._get_fresh_client(Path(tmp))

        with (
            patch("jarvis.kernel.settings.settings") as mock_settings,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_settings.strava_client_id = "12345"
            mock_settings.strava_client_secret = MagicMock()
            mock_settings.strava_client_secret.get_secret_value.return_value = "secret"

            mock_http = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_http.post = AsyncMock(return_value=_make_response(200, VALID_TOKEN_RESP))
            mock_http.get = AsyncMock(return_value=_make_response(200, []))

            await client._get_token()
            mock_http.post.assert_called_once()

    async def test_refresh_not_called_when_token_valid(self):
        """Si le token n'est pas expiré, pas de refresh."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            client = self._get_fresh_client(Path(tmp))
            client._access_token = "at-valid"
            client._expires_at = time.time() + 3600  # valide pour 1h

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock()

            token = await client._get_token()
            mock_http.post.assert_not_called()
            self.assertEqual(token, "at-valid")

    async def test_rolling_refresh_token_persisted(self):
        """Strava renvoie un nouveau refresh_token → il doit être sauvegardé."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client = self._get_fresh_client(tmp_path)

            with (
                patch("jarvis.kernel.settings.settings") as mock_settings,
                patch("httpx.AsyncClient") as mock_client_cls,
                patch.object(client, "_save_cache") as mock_save,
            ):
                mock_settings.strava_client_id = "12345"
                mock_settings.strava_client_secret = MagicMock()
                mock_settings.strava_client_secret.get_secret_value.return_value = "secret"

                mock_http = AsyncMock()
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_http.post = AsyncMock(return_value=_make_response(200, VALID_TOKEN_RESP))

                await client._do_refresh()

                # Le nouveau refresh_token doit être mis à jour en mémoire
                self.assertEqual(client._refresh_token, "rt-new-456")
                mock_save.assert_called_once()

    async def test_missing_credentials_raises(self):
        """Sans client_id ou client_secret, une RuntimeError est levée."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            client = self._get_fresh_client(Path(tmp))
            client._refresh_token = "rt"

            with patch("jarvis.kernel.settings.settings") as mock_settings:
                mock_settings.strava_client_id = ""
                mock_settings.strava_client_secret = MagicMock()
                mock_settings.strava_client_secret.get_secret_value.return_value = ""

                with self.assertRaises(RuntimeError, msg="Should raise for missing credentials"):
                    await client._do_refresh()

    async def test_no_refresh_token_raises(self):
        """Sans refresh_token, une RuntimeError est levée."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            client = self._get_fresh_client(Path(tmp))
            client._refresh_token = None

            with patch("jarvis.kernel.settings.settings") as mock_settings:
                mock_settings.strava_client_id = "12345"
                mock_settings.strava_client_secret = MagicMock()
                mock_settings.strava_client_secret.get_secret_value.return_value = "secret"

                with self.assertRaises(RuntimeError):
                    await client._do_refresh()


class TestStravaClientRetryOn401(unittest.IsolatedAsyncioTestCase):
    """Teste le retry automatique sur 401."""

    async def test_retry_once_on_401(self):
        """Un 401 déclenche un refresh puis un retry. Pas de boucle infinie."""
        import tempfile

        from jarvis.capabilities.skills.strava import client as strava_module

        with tempfile.TemporaryDirectory() as tmp:
            patcher = patch.object(strava_module, "_TOKEN_CACHE_PATH", Path(tmp) / "tok.json")
            patcher.start()

            c = strava_module.StravaClient.__new__(strava_module.StravaClient)
            c._access_token = "at-stale"
            c._expires_at = time.time() + 3600
            c._refresh_token = "rt"
            c._athlete_id = None

            refresh_called = []

            async def mock_do_refresh():
                refresh_called.append(True)
                c._access_token = "at-fresh"

            c._do_refresh = mock_do_refresh

            call_count = [0]

            with patch("httpx.AsyncClient") as mock_cls:
                mock_http = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                def side_effect(*args, **kwargs):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        return _make_response(401)
                    return _make_response(200, [])

                mock_http.get = AsyncMock(side_effect=side_effect)

                result = await c._get("/athlete/activities")
                self.assertEqual(result, [])
                self.assertEqual(len(refresh_called), 1, "refresh doit être appelé exactement 1 fois")
                self.assertEqual(call_count[0], 2, "GET doit être appelé 2 fois (1 échec + 1 retry)")

            patcher.stop()

    async def test_no_infinite_retry_on_401(self):
        """Le second appel (retry=False) ne relance pas de refresh si 401 à nouveau."""
        import tempfile

        from jarvis.capabilities.skills.strava import client as strava_module

        with tempfile.TemporaryDirectory() as tmp:
            patcher = patch.object(strava_module, "_TOKEN_CACHE_PATH", Path(tmp) / "tok.json")
            patcher.start()

            c = strava_module.StravaClient.__new__(strava_module.StravaClient)
            c._access_token = "at-stale"
            c._expires_at = time.time() + 3600
            c._refresh_token = "rt"
            c._athlete_id = None
            c._do_refresh = AsyncMock()

            with patch("httpx.AsyncClient") as mock_cls:
                mock_http = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_http.get = AsyncMock(return_value=_make_response(401))

                with self.assertRaises(Exception):
                    await c._get("/athlete/activities", _retry_on_401=False)
                # _do_refresh ne doit PAS avoir été appelé (retry déjà fait)
                c._do_refresh.assert_not_called()

            patcher.stop()


class TestStravaClientRateLimit(unittest.IsolatedAsyncioTestCase):
    """Teste le comportement sur rate limit (HTTP 429)."""

    async def test_429_raises_runtime_error(self):
        """Un 429 doit lever RuntimeError avec le délai de retry."""
        import tempfile

        from jarvis.capabilities.skills.strava import client as strava_module

        with tempfile.TemporaryDirectory() as tmp:
            patcher = patch.object(strava_module, "_TOKEN_CACHE_PATH", Path(tmp) / "tok.json")
            patcher.start()

            c = strava_module.StravaClient.__new__(strava_module.StravaClient)
            c._access_token = "at"
            c._expires_at = time.time() + 3600
            c._refresh_token = "rt"
            c._athlete_id = None

            reset_time = int(time.time()) + 90
            resp_429 = _make_response(429, headers={"X-RateLimit-Reset": str(reset_time)})
            resp_429.raise_for_status = Mock()  # 429 ne lève pas via raise_for_status ici

            with patch("httpx.AsyncClient") as mock_cls:
                mock_http = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_http.get = AsyncMock(return_value=resp_429)

                with self.assertRaises(RuntimeError) as ctx:
                    await c._get("/athlete/activities")

                self.assertIn("rate limit", str(ctx.exception).lower())

            patcher.stop()


class TestStravaClientTokenCache(unittest.TestCase):
    """Teste la lecture/écriture du cache token JSON."""

    def test_load_cache_from_file(self):
        """Le client lit le token depuis le fichier de cache."""
        import tempfile

        from jarvis.capabilities.skills.strava import client as strava_module

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "strava_token.json"
            cache_path.write_text(
                json.dumps({
                    "access_token": "at-cached",
                    "expires_at": time.time() + 3600,
                    "refresh_token": "rt-cached",
                    "athlete_id": 42,
                }),
                encoding="utf-8",
            )

            with (
                patch.object(strava_module, "_TOKEN_CACHE_PATH", cache_path),
                patch("jarvis.kernel.settings.settings") as mock_settings,
            ):
                mock_settings.strava_refresh_token = MagicMock()
                mock_settings.strava_refresh_token.get_secret_value.return_value = ""

                c = strava_module.StravaClient()
                self.assertEqual(c._access_token, "at-cached")
                self.assertEqual(c._refresh_token, "rt-cached")
                self.assertEqual(c._athlete_id, 42)

    def test_save_cache(self):
        """_save_cache écrit le token (sans client_secret) sur disque."""
        import tempfile

        from jarvis.capabilities.skills.strava import client as strava_module

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "strava_token.json"

            with patch.object(strava_module, "_TOKEN_CACHE_PATH", cache_path):
                c = strava_module.StravaClient.__new__(strava_module.StravaClient)
                c._access_token = "at-old"
                c._expires_at = 0.0
                c._refresh_token = "rt-old"
                c._athlete_id = 99
                c._load_cache = lambda: None  # skip

                token_data = {
                    "access_token": "at-new",
                    "expires_at": time.time() + 3600,
                    "refresh_token": "rt-new",
                }
                c._save_cache(token_data)

                saved = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["access_token"], "at-new")
                self.assertEqual(saved["refresh_token"], "rt-new")
                self.assertNotIn("client_secret", saved)


if __name__ == "__main__":
    unittest.main()
