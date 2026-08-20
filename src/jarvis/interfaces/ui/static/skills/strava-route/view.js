/**
 * Vue Strava Route — jarvis-skills
 * Affiche le trajet GPS d'une activité Strava sur une carte Mapbox GL.
 * Décode la Google Encoded Polyline → GeoJSON LineString, centre sur le trajet,
 * et affiche un panneau de stats persistant (distance, durée, D+, allure, FC).
 *
 * Réutilise les helpers loadScript/loadStyle et le pattern fetchToken de globe/view.js.
 * S'enregistre via Jarvis.views.register('strava-route', ...).
 *
 * Commandes view_command :
 *   show_activity { activity_id } — charge et affiche le trajet d'une activité
 */
(function () {
  if (!window.Jarvis?.views) return;

  const VIEW_ID = 'strava-route';
  const STYLE_ID = 'sr-styles';
  const MAPBOX_VERSION = '3.23.1';
  const MAPBOX_CDN = `https://api.mapbox.com/mapbox-gl-js/v${MAPBOX_VERSION}`;
  const MAPBOX_STYLE = 'mapbox://styles/mapbox/dark-v11';

  let map = null;
  let container = null;
  let _visible = false;
  let _mapReady = false;
  let _pendingActivity = null;

  // ── Helpers identiques à globe/view.js ─────────────────────────────────────

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${src}"]`)) return resolve();
      const s = document.createElement('script');
      s.src = src; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function loadStyle(href) {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = href;
    document.head.appendChild(l);
  }

  async function loadMapbox() {
    loadStyle(`${MAPBOX_CDN}/mapbox-gl.css`);
    await loadScript(`${MAPBOX_CDN}/mapbox-gl.js`);
  }

  async function fetchToken() {
    const headers = (window.Jarvis?.authHeaders) ? Jarvis.authHeaders() : {};
    const res = await fetch('/api/strava/config', { headers });
    if (!res.ok) throw new Error('strava config unavailable (' + res.status + ')');
    const data = await res.json();
    const token = (data.mapbox_token || '').trim();
    if (!token) throw new Error('MAPBOX_TOKEN vide — configure .env puis redémarre Jarvis.');
    return token;
  }

  // ── Google Encoded Polyline decoder ────────────────────────────────────────
  // Spec : https://developers.google.com/maps/documentation/utilities/polylinealgorithm

  function decodePoly(encoded) {
    const coords = [];
    let index = 0, lat = 0, lng = 0;
    while (index < encoded.length) {
      let b, shift = 0, result = 0;
      do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; }
      while (b >= 0x20);
      lat += (result & 1) ? ~(result >> 1) : (result >> 1);
      shift = 0; result = 0;
      do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; }
      while (b >= 0x20);
      lng += (result & 1) ? ~(result >> 1) : (result >> 1);
      coords.push([lng / 1e5, lat / 1e5]);
    }
    return coords;
  }

  // ── Formatage stats ─────────────────────────────────────────────────────────

  function fmtDuration(s) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h) return `${h}h${String(m).padStart(2, '0')}`;
    return `${m}m${String(sec).padStart(2, '0')}s`;
  }

  function fmtDistance(m) { return (m / 1000).toFixed(2) + ' km'; }

  function fmtPace(m, s) {
    if (!m || !s) return '—';
    const ps = s / (m / 1000);
    return `${Math.floor(ps / 60)}'${String(Math.floor(ps % 60)).padStart(2, '0')}"`;
  }

  function fmtSpeed(ms) { return (ms * 3.6).toFixed(1) + ' km/h'; }

  function typeIcon(type) {
    const icons = { Run: '🏃', Ride: '🚴', Swim: '🏊', Walk: '🚶', Hike: '🥾' };
    return icons[type] || '⚡';
  }

  // ── Panneau de stats ────────────────────────────────────────────────────────

  function buildStatsPanel(container, activity) {
    let panel = container.querySelector('.sr-stats-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'sr-stats-panel';
      container.appendChild(panel);
    }

    const { name, type, date, distance_m, moving_time_s,
            total_elevation_gain, average_heartrate, average_speed_ms } = activity;

    const isRun = type === 'Run';
    const perfLabel = isRun ? 'Allure' : 'Vitesse';
    const perfVal = isRun
      ? fmtPace(distance_m, moving_time_s) + '/km'
      : fmtSpeed(average_speed_ms);

    panel.innerHTML = `
      <div class="sr-panel-header">
        <span class="sr-type-icon">${typeIcon(type)}</span>
        <div class="sr-title-block">
          <div class="sr-activity-name">${esc(name)}</div>
          <div class="sr-activity-meta">${esc(type)} · ${esc(date || '')}</div>
        </div>
      </div>
      <div class="sr-stats-grid">
        <div class="sr-stat">
          <div class="sr-stat-val">${fmtDistance(distance_m)}</div>
          <div class="sr-stat-lbl">Distance</div>
        </div>
        <div class="sr-stat">
          <div class="sr-stat-val">${fmtDuration(moving_time_s)}</div>
          <div class="sr-stat-lbl">Durée</div>
        </div>
        <div class="sr-stat">
          <div class="sr-stat-val">${(total_elevation_gain || 0).toFixed(0)} m</div>
          <div class="sr-stat-lbl">Dénivelé+</div>
        </div>
        <div class="sr-stat">
          <div class="sr-stat-val">${perfVal}</div>
          <div class="sr-stat-lbl">${perfLabel}</div>
        </div>
        ${average_heartrate ? `
        <div class="sr-stat">
          <div class="sr-stat-val">${Math.round(average_heartrate)} bpm</div>
          <div class="sr-stat-lbl">FC moy.</div>
        </div>` : ''}
      </div>
    `;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── CSS ─────────────────────────────────────────────────────────────────────

  const CSS = `
    #strava-route-container { font-family: var(--sans, "Geist", system-ui, sans-serif); }
    #strava-route-map { position: absolute; inset: 0; }

    .sr-stats-panel {
      position: absolute;
      top: 28px; left: 28px;
      z-index: 10;
      min-width: 280px;
      background: rgba(6, 8, 13, 0.82);
      border: 1px solid rgba(220, 232, 255, 0.1);
      border-radius: 14px;
      padding: 18px 20px 16px;
      backdrop-filter: blur(16px) saturate(150%);
      -webkit-backdrop-filter: blur(16px) saturate(150%);
      color: rgba(220, 232, 255, 0.88);
      pointer-events: none;
    }
    .sr-panel-header {
      display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px;
    }
    .sr-type-icon { font-size: 26px; line-height: 1; flex-shrink: 0; margin-top: 2px; }
    .sr-title-block { flex: 1; min-width: 0; }
    .sr-activity-name {
      font-size: 15px; font-weight: 500; color: #DCE8FF;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      line-height: 1.3;
    }
    .sr-activity-meta {
      font-family: var(--mono, monospace); font-size: 10px;
      letter-spacing: .12em; text-transform: uppercase;
      color: rgba(220, 232, 255, 0.45); margin-top: 4px;
    }
    .sr-stats-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
      gap: 8px 12px;
    }
    .sr-stat { display: flex; flex-direction: column; gap: 2px; }
    .sr-stat-val {
      font-family: var(--serif, "Geist"); font-weight: 300; font-size: 18px;
      color: #DCE8FF; letter-spacing: -.02em; font-variant-numeric: tabular-nums;
      line-height: 1;
    }
    .sr-stat-lbl {
      font-family: var(--mono, monospace); font-size: 9px;
      letter-spacing: .12em; text-transform: uppercase;
      color: rgba(220, 232, 255, 0.4);
    }

    .sr-strava-badge {
      position: absolute; bottom: 28px; right: 28px; z-index: 10;
      display: flex; align-items: center; gap: 8px;
      padding: 7px 14px;
      background: rgba(6, 8, 13, 0.72);
      border: 1px solid rgba(220, 232, 255, 0.1);
      border-radius: 999px;
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      font-family: var(--mono, monospace); font-size: 10px;
      letter-spacing: .18em; text-transform: uppercase;
      color: rgba(220, 232, 255, 0.5);
    }
    .sr-strava-badge .dot {
      width: 6px; height: 6px; border-radius: 50%;
      background: #FC4C02; box-shadow: 0 0 6px rgba(252, 76, 2, .7);
    }

    .sr-loading {
      position: absolute; inset: 0; z-index: 20;
      display: flex; align-items: center; justify-content: center;
      background: rgba(6, 8, 13, 0.7);
      font-family: var(--mono, monospace); font-size: 12px;
      letter-spacing: .18em; text-transform: uppercase;
      color: rgba(220, 232, 255, 0.55);
    }
  `;

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style'); s.id = STYLE_ID; s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ── Carte Mapbox ────────────────────────────────────────────────────────────

  async function initMap(token) {
    if (map) return;

    const mapEl = document.createElement('div');
    mapEl.id = 'strava-route-map';
    container.appendChild(mapEl);

    mapboxgl.accessToken = token;
    const cores = navigator.hardwareConcurrency || 4;
    if (typeof mapboxgl.workerCount === 'number')
      mapboxgl.workerCount = Math.min(8, Math.max(4, Math.floor(cores / 2)));
    if (typeof mapboxgl.maxParallelImageRequests === 'number')
      mapboxgl.maxParallelImageRequests = 32;

    map = new mapboxgl.Map({
      container: 'strava-route-map',
      style: MAPBOX_STYLE,
      center: [2.35, 48.85],
      zoom: 10,
      antialias: true,
    });

    map.addControl(new mapboxgl.NavigationControl(), 'top-right');

    map.on('load', () => {
      _mapReady = true;
      if (_pendingActivity) {
        const act = _pendingActivity;
        _pendingActivity = null;
        _displayActivity(act);
      }
    });
  }

  // ── Affichage du trajet ──────────────────────────────────────────────────────

  function _displayActivity(activity) {
    if (!map || !_mapReady) { _pendingActivity = activity; return; }

    const polyline = activity.polyline || '';
    if (!polyline) {
      console.warn('[strava-route] Pas de polyline pour l\'activité', activity.id);
      return;
    }

    const coords = decodePoly(polyline);
    if (!coords.length) { console.warn('[strava-route] Polyline décodée vide'); return; }

    const geojson = { type: 'Feature', geometry: { type: 'LineString', coordinates: coords } };

    // Supprimer les layers/sources existants avant d'en ajouter de nouveaux
    ['sr-route-line', 'sr-route-glow'].forEach(id => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
    if (map.getSource('sr-route')) map.removeSource('sr-route');

    map.addSource('sr-route', { type: 'geojson', data: geojson });

    // Halo coloré (glow)
    map.addLayer({
      id: 'sr-route-glow',
      type: 'line',
      source: 'sr-route',
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': '#FC4C02',
        'line-width': 8,
        'line-opacity': 0.25,
        'line-blur': 4,
      },
    });

    // Trait principal (orange Strava)
    map.addLayer({
      id: 'sr-route-line',
      type: 'line',
      source: 'sr-route',
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': '#FC4C02',
        'line-width': 3.5,
        'line-opacity': 0.92,
      },
    });

    // Centrer la carte sur le trajet
    const lngs = coords.map(c => c[0]);
    const lats = coords.map(c => c[1]);
    const bounds = [
      [Math.min(...lngs), Math.min(...lats)],
      [Math.max(...lngs), Math.max(...lats)],
    ];
    map.fitBounds(bounds, { padding: 80, maxZoom: 16, duration: 1000 });

    buildStatsPanel(container, activity);

    // Supprimer le loading overlay si présent
    const loading = container.querySelector('.sr-loading');
    if (loading) loading.remove();
  }

  async function loadActivity(activityId) {
    // Afficher un indicateur de chargement
    let loading = container.querySelector('.sr-loading');
    if (!loading) {
      loading = document.createElement('div');
      loading.className = 'sr-loading';
      loading.textContent = 'Chargement…';
      container.appendChild(loading);
    }

    const headers = (window.Jarvis?.authHeaders) ? Jarvis.authHeaders() : {};
    const res = await fetch(`/api/strava/activity/${activityId}`, { headers });
    if (!res.ok) {
      loading.textContent = `Erreur ${res.status} — vérifie les credentials Strava`;
      return;
    }
    const activity = await res.json();
    _displayActivity(activity);
  }

  // ── Container ───────────────────────────────────────────────────────────────

  function ensureContainer() {
    if (container) return;
    injectStyle();
    container = document.createElement('div');
    container.id = 'strava-route-container';
    Object.assign(container.style, {
      position: 'fixed', inset: '0', zIndex: '2',
      display: 'none', opacity: '0',
      transition: 'opacity .35s ease',
      background: '#06080D',
    });

    // Badge Strava
    const badge = document.createElement('div');
    badge.className = 'sr-strava-badge';
    badge.innerHTML = '<div class="dot"></div>Strava';
    container.appendChild(badge);

    document.body.appendChild(container);
  }

  // ── Enregistrement ──────────────────────────────────────────────────────────

  Jarvis.views.register(VIEW_ID, {
    meta: {
      name: 'Strava Route',
      desc: 'Trajet GPS d\'une activité Strava sur carte Mapbox avec stats',
      glyph: 'STR',
      tags: ['strava', 'sport', 'map', 'running', 'cycling'],
    },

    async show(params = {}) {
      ensureContainer();
      _visible = true;
      container.style.display = 'block';
      container.getBoundingClientRect();
      container.style.opacity = '1';

      if (!map) {
        try {
          await loadMapbox();
          const token = await fetchToken();
          await initMap(token);
        } catch (err) {
          console.error('[strava-route] init failed:', err);
          let msg = container.querySelector('.sr-loading');
          if (!msg) { msg = document.createElement('div'); msg.className = 'sr-loading'; container.appendChild(msg); }
          msg.textContent = 'Erreur : ' + err.message;
        }
      }

      if (params.activity_id) loadActivity(params.activity_id);
    },

    hide() {
      if (!container) return;
      _visible = false;
      container.style.opacity = '0';
      setTimeout(() => { if (!_visible && container) container.style.display = 'none'; }, 360);
    },

    command(cmd, params = {}) {
      switch (cmd) {
        case 'show': this.show(params); break;
        case 'hide': this.hide(); break;
        case 'show_activity':
          if (!_visible) this.show({});
          if (params.activity_id) loadActivity(params.activity_id);
          break;
      }
    },
  });
})();
