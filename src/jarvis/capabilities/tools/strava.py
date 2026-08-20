# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""StravaTool — outil LLM pour les activités sportives Strava.

Patterns de broadcast identiques à ShowViewTool : envoie des events WebSocket
show_view / view_command vers le frontend pour déclencher strava-route et strava-dashboard.
"""

from __future__ import annotations

from collections.abc import Callable

from jarvis.capabilities.tools.base import Tool, ToolResult
from jarvis.kernel.error_collector import collector  # jrv: autofix

# Mapping mots-clés FR/EN → type Strava (majuscule car c'est ce que retourne l'API)
_TYPE_MAP: dict[str, str] = {
    "run": "Run",
    "course": "Run",
    "running": "Run",
    "jogging": "Run",
    "vélo": "Ride",
    "velo": "Ride",
    "ride": "Ride",
    "cycling": "Ride",
    "bike": "Ride",
    "swim": "Swim",
    "natation": "Swim",
    "swimming": "Swim",
    "walk": "Walk",
    "marche": "Walk",
    "walking": "Walk",
    "randonnée": "Hike",
    "rando": "Hike",
    "hike": "Hike",
    "hiking": "Hike",
}


def _fmt_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}h{m:02d}"
    return f"{m}m{s:02d}s"


def _fmt_distance(meters: float) -> str:
    return f"{meters / 1000:.2f} km"


def _fmt_pace(meters: float, seconds: int) -> str:
    """Allure en min/km (pour course à pied)."""
    if meters <= 0 or seconds <= 0:
        return "—"
    pace_s = seconds / (meters / 1000)
    return f"{int(pace_s // 60)}'{int(pace_s % 60):02d}\"/km"


def _fmt_speed(ms: float) -> str:
    return f"{ms * 3.6:.1f} km/h"


def _resolve_type(query: str) -> str | None:
    q = query.lower().strip()
    for kw, strava_type in _TYPE_MAP.items():
        if kw in q:
            return strava_type
    return None


def _fmt_activity(a: dict) -> str:
    name = a.get("name", "Activité")
    atype = a.get("type", "—")
    date = (a.get("start_date_local") or "")[:10]
    dist = _fmt_distance(a.get("distance", 0))
    dur = _fmt_duration(a.get("moving_time", 0))
    elev = a.get("total_elevation_gain", 0)
    hr = a.get("average_heartrate")
    speed_ms = a.get("average_speed", 0)

    perf = (
        _fmt_pace(a.get("distance", 0), a.get("moving_time", 0))
        if atype == "Run"
        else _fmt_speed(speed_ms)
    )
    label = "Allure" if atype == "Run" else "Vitesse"

    lines = [
        f"**{name}** — {atype} · {date}",
        f"Distance : {dist} | Durée : {dur} | {label} : {perf}",
        f"Dénivelé+ : {elev:.0f} m",
    ]
    if hr:
        lines.append(f"FC moyenne : {hr:.0f} bpm")
    lines.append(f"*(id:{a['id']})*")
    return "\n".join(lines)


def _fmt_activity_short(a: dict) -> str:
    atype = a.get("type", "—")
    date = (a.get("start_date_local") or "")[:10]
    dist = _fmt_distance(a.get("distance", 0))
    dur = _fmt_duration(a.get("moving_time", 0))
    return f"• [{date}] {atype} — {dist} en {dur} (id:{a['id']})"


def _fmt_totals(t: dict | None, label: str) -> str:
    if not t:
        return f"{label} : —"
    count = t.get("count", 0)
    dist = _fmt_distance(t.get("distance", 0))
    dur = _fmt_duration(t.get("moving_time", 0))
    elev = t.get("elevation_gain", 0)
    return f"{label} : {count} activité(s) · {dist} · {dur} · {elev:.0f} m D+"


class StravaTool(Tool):
    """Outil LLM pour les activités sportives Strava."""

    name = "strava_activities"
    description = (
        "Accès à tes données Strava (activités sportives).\n"
        "Actions disponibles :\n"
        "- get_latest   : dernière activité (toutes disciplines)\n"
        "- get_by_type  : dernière activité d'un type précis (run/ride/swim/walk/hike) — "
        "précise le type en clair dans `query` (ex: 'course', 'vélo')\n"
        "- get_stats    : totaux récents (4 semaines), année en cours, tout temps\n"
        "- list_recent  : liste des N dernières activités (`count` = nombre, défaut 5)\n"
        "- show_activity: affiche le trajet sur la carte Mapbox (`activity_id` requis ; "
        "si omis, utilise la dernière activité)\n"
        "- show_dashboard: ouvre le dashboard Strava (vue grille avec stats + liste)"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get_latest",
                    "get_by_type",
                    "get_stats",
                    "list_recent",
                    "show_activity",
                    "show_dashboard",
                ],
                "description": "Action à effectuer.",
            },
            "query": {
                "type": "string",
                "description": (
                    "Type d'activité en langage naturel pour get_by_type "
                    "(ex: 'course', 'vélo', 'run', 'ride')."
                ),
            },
            "activity_id": {
                "type": "integer",
                "description": "ID Strava de l'activité (pour show_activity).",
            },
            "count": {
                "type": "integer",
                "description": "Nombre d'activités à lister pour list_recent (défaut: 5, max: 20).",
                "default": 5,
            },
        },
        "required": ["action"],
    }

    def __init__(self, broadcast_event: Callable[[dict], None] | None = None) -> None:
        self._broadcast = broadcast_event

    def _emit(self, event: dict) -> None:
        if self._broadcast is not None:
            self._broadcast(event)

    async def execute(  # type: ignore[override]
        self,
        action: str,
        query: str = "",
        activity_id: int | None = None,
        count: int = 5,
        **_: object,
    ) -> ToolResult:
        from jarvis.capabilities.skills.strava.client import strava_client

        try:
            if action == "get_latest":
                acts = await strava_client.get_activities(per_page=1)
                if not acts:
                    return ToolResult(content="Aucune activité trouvée sur Strava.")
                return ToolResult(content=_fmt_activity(acts[0]))

            if action == "get_by_type":
                strava_type = _resolve_type(query) if query else None
                acts = await strava_client.get_activities(per_page=30)
                if strava_type:
                    acts = [a for a in acts if a.get("type") == strava_type]
                if not acts:
                    return ToolResult(
                        content=f"Aucune activité de type '{query}' trouvée dans les 30 dernières."
                    )
                return ToolResult(content=_fmt_activity(acts[0]))

            if action == "get_stats":
                stats = await strava_client.get_athlete_stats()
                lines = [
                    "**Statistiques Strava**",
                    _fmt_totals(stats.get("recent_run_totals"), "Course (4 semaines)"),
                    _fmt_totals(stats.get("ytd_run_totals"), "Course (année en cours)"),
                    _fmt_totals(stats.get("all_run_totals"), "Course (tout temps)"),
                    "",
                    _fmt_totals(stats.get("recent_ride_totals"), "Vélo (4 semaines)"),
                    _fmt_totals(stats.get("ytd_ride_totals"), "Vélo (année en cours)"),
                    _fmt_totals(stats.get("all_ride_totals"), "Vélo (tout temps)"),
                ]
                return ToolResult(content="\n".join(lines))

            if action == "list_recent":
                n = max(1, min(count, 20))
                acts = await strava_client.get_activities(per_page=n)
                if not acts:
                    return ToolResult(content="Aucune activité trouvée.")
                lines = ["**Activités récentes :**"] + [_fmt_activity_short(a) for a in acts[:n]]
                return ToolResult(content="\n".join(lines))

            if action == "show_activity":
                if activity_id is None:
                    acts = await strava_client.get_activities(per_page=1)
                    if not acts:
                        return ToolResult(content="Aucune activité trouvée.", is_error=True)
                    activity_id = acts[0]["id"]
                self._emit({"type": "show_view", "view_id": "strava-route"})
                self._emit(
                    {
                        "type": "view_command",
                        "view_id": "strava-route",
                        "command": "show_activity",
                        "params": {"activity_id": activity_id},
                    }
                )
                return ToolResult(content=f"J'affiche le trajet de l'activité #{activity_id}.")

            if action == "show_dashboard":
                self._emit({"type": "show_view", "view_id": "strava-dashboard"})
                return ToolResult(content="J'ouvre le dashboard Strava.")

            return ToolResult(content=f"Action inconnue : {action}", is_error=True)

        except RuntimeError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001
            collector.error("JRV-TOL-001", "JRV-TOL-001", cause=exc)
            return ToolResult(content=f"Erreur Strava : {exc}", is_error=True)
