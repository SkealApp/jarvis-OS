# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Strava API router — endpoints frontend pour les vues strava-route et strava-dashboard.

⚠️  Sécurité : ces endpoints ne transmettent JAMAIS client_secret ni refresh_token.
    Seules les données nécessaires à l'affichage sont exposées.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger

from jarvis.kernel.error_collector import collector  # jrv: autofix

router = APIRouter(prefix="/api/strava", tags=["strava"])


@router.get("/config")
async def get_strava_config() -> dict[str, Any]:
    """Expose au frontend les métadonnées de configuration Strava (jamais les secrets).

    Analogue à /api/globe/config pour le token Mapbox.
    Le frontend utilise cette réponse pour savoir si Strava est configuré.
    """
    from jarvis.kernel.settings import settings

    configured = bool(
        settings.strava_client_id.strip()
        and settings.strava_client_secret.get_secret_value().strip()
        and settings.strava_refresh_token.get_secret_value().strip()
    )
    return {
        "configured": configured,
        "client_id": settings.strava_client_id.strip() or "",
        # mapbox_token ré-exposé ici pour éviter un double fetch côté strava-route
        "mapbox_token": settings.mapbox_token.get_secret_value() or "",
    }


@router.get("/activity/{activity_id}")
async def get_activity(activity_id: int) -> dict[str, Any]:
    """Retourne le détail d'une activité Strava formaté pour l'affichage frontend.

    Inclut la polyline encodée (Google Encoded Polyline) pour le rendu Mapbox.
    Ne renvoie aucun secret.
    """
    from jarvis.capabilities.skills.strava.client import strava_client

    try:
        activity = await strava_client.get_activity(activity_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        collector.error("JRV-API-001", "JRV-API-001", cause=exc)
        logger.error("Strava get_activity %d : %s", activity_id, exc)
        raise HTTPException(status_code=500, detail="Erreur API Strava") from exc

    activity_map = activity.get("map") or {}
    # Strava renvoie deux polylines : polyline (complète) et summary_polyline (allégée)
    polyline = activity_map.get("polyline") or activity_map.get("summary_polyline") or ""

    atype = activity.get("type", "")
    distance = float(activity.get("distance", 0))
    moving_time = int(activity.get("moving_time", 0))

    return {
        "id": activity_id,
        "name": activity.get("name", ""),
        "type": atype,
        "date": (activity.get("start_date_local") or "")[:10],
        "distance_m": distance,
        "moving_time_s": moving_time,
        "total_elevation_gain": float(activity.get("total_elevation_gain", 0)),
        "average_heartrate": activity.get("average_heartrate"),
        "average_speed_ms": float(activity.get("average_speed", 0)),
        "polyline": polyline,
        "start_latlng": activity.get("start_latlng") or [],
        "end_latlng": activity.get("end_latlng") or [],
    }


@router.get("/activities")
async def get_activities(per_page: int = 10, page: int = 1) -> list[dict[str, Any]]:
    """Liste paginée des activités récentes pour le dashboard."""
    from jarvis.capabilities.skills.strava.client import strava_client

    try:
        acts = await strava_client.get_activities(per_page=min(per_page, 30), page=page)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        collector.error("JRV-API-001", "JRV-API-001", cause=exc)
        raise HTTPException(status_code=500, detail="Erreur API Strava") from exc

    return [
        {
            "id": a.get("id"),
            "name": a.get("name", ""),
            "type": a.get("type", ""),
            "date": (a.get("start_date_local") or "")[:10],
            "distance_m": float(a.get("distance", 0)),
            "moving_time_s": int(a.get("moving_time", 0)),
            "total_elevation_gain": float(a.get("total_elevation_gain", 0)),
            "average_heartrate": a.get("average_heartrate"),
            "average_speed_ms": float(a.get("average_speed", 0)),
        }
        for a in acts
    ]


@router.get("/stats")
async def get_athlete_stats() -> dict[str, Any]:
    """Totaux récents/YTD/all-time pour le dashboard."""
    from jarvis.capabilities.skills.strava.client import strava_client

    try:
        return await strava_client.get_athlete_stats()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        collector.error("JRV-API-001", "JRV-API-001", cause=exc)
        raise HTTPException(status_code=500, detail="Erreur API Strava") from exc
