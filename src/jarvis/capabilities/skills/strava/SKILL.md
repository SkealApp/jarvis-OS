---
name: strava
description: Activités sportives Strava — affiche tes dernières sorties, stats et trajets sur carte.
metadata:
  openclaw:
    os: ["darwin", "linux", "win32"]
---

## Instructions

Utilise l'outil `strava_activities` quand l'utilisateur parle de ses activités sportives Strava :

**Phrases déclenchantes :**
- "montre-moi ma dernière activité Strava"
- "montre-moi ma dernière course / mon dernier run"
- "affiche mon dernier run / ma dernière sortie vélo"
- "ouvre mon dashboard Strava"
- "montre-moi mes stats de la semaine / du mois / de l'année"
- "quel a été mon trajet hier"
- "combien de km ai-je couru ce mois-ci"
- "mes activités récentes"

**Actions disponibles :**
- `get_latest` — récupère la dernière activité et affiche ses stats
- `get_by_type` — filtre par type (run/ride/swim/walk), passe le type dans `query`
- `get_stats` — totaux récents (4 semaines), YTD, all-time pour course et vélo
- `list_recent` — liste les N dernières activités (utilise `count` pour préciser)
- `show_activity` — ouvre la vue carte avec le trajet de l'activité (passe `activity_id`)
- `show_dashboard` — ouvre le dashboard Strava complet

**Workflow recommandé :**
1. Pour "dernière course" → `get_by_type` avec `query="run"`, puis propose `show_activity` avec l'ID
2. Pour "dashboard" ou "stats globales" → `show_dashboard` puis `get_stats`
3. Pour un trajet spécifique → `show_activity` avec l'`activity_id`

**Sécurité :**
Ne jamais mentionner ni loguer le `client_secret` ou le `refresh_token` Strava dans une réponse.

**Configuration requise :** `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN` dans `.env`.
