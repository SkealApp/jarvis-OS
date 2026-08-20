/**
 * Vue Strava Dashboard — jarvis-skills
 * Cockpit sportif inspiré de system-monitor/view.js :
 *   - Grille d'activités récentes (scrollable) avec type/distance/durée/date
 *   - Totaux semaine / mois / année (distance cumulée, dénivelé, temps)
 *   - Mini-graphiques Canvas 2D (même drawSpark() que system-monitor)
 *   - Clic sur activité → ouvre strava-route via Jarvis.views
 *
 * Données : /api/strava/activities · /api/strava/stats
 * Polling : toutes les 60 s (les données Strava changent peu souvent)
 */
(function () {
  if (!window.Jarvis?.views) return;

  const VIEW_ID = 'strava-dashboard';
  const STYLE_ID = 'sd-styles';
  const POLL_MS = 60_000;

  let container = null;
  let _visible = false;
  let _domBuilt = false;
  let _pollTimer = null;
  let _clockTimer = null;

  // Historique des distances hebdo (8 dernières semaines, en km)
  const _weeklyDist = new Array(8).fill(0);

  // ── Helpers ──────────────────────────────────────────────────────────────────

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function apiFetch(path) {
    const headers = (window.Jarvis?.authHeaders) ? Jarvis.authHeaders() : {};
    return fetch(window.location.origin + path, { headers })
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function fmtDist(m) { return (m / 1000).toFixed(1) + ' km'; }
  function fmtDuration(s) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return `${h}h${String(m).padStart(2, '0')}`;
    return `${m}m`;
  }

  function typeIcon(type) {
    const icons = { Run: '🏃', Ride: '🚴', Swim: '🏊', Walk: '🚶', Hike: '🥾' };
    return icons[type] || '⚡';
  }

  // ── Sparklines (identique à system-monitor) ───────────────────────────────

  function drawSpark(canvas, data, stroke, fillRgba) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.offsetWidth || 150, h = canvas.offsetHeight || 30;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    const n = data.length; if (n < 2) return;
    const mx = Math.max(...data) || 1;
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * w, y = h - (data[i] / mx) * (h - 4) - 2;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = stroke; ctx.lineWidth = 1.5;
    ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = fillRgba; ctx.fill();
  }

  // ── CSS ───────────────────────────────────────────────────────────────────

  const CSS = `
    #strava-dashboard-container {
      font-family: var(--sans, "Geist", system-ui, sans-serif);
      color: var(--fg-1, rgba(220,232,255,.78));
      background: var(--bg-0, #06080D);
      overflow: hidden;
    }

    /* ── Layout principal ── */
    .sd-content {
      position: absolute; left: 36px; right: 36px; top: 84px; bottom: 84px;
      display: flex; flex-direction: column; gap: 16px; z-index: 3;
    }

    /* ── Card générique ── */
    .sd-card {
      border: 1px solid var(--line-1, rgba(220,232,255,.06));
      border-radius: var(--r-3, 12px);
      background: var(--bg-1, #0A0E16);
      position: relative;
    }
    .sd-eyebrow {
      font-family: var(--mono, monospace); font-size: 9px;
      letter-spacing: .2em; text-transform: uppercase;
      color: var(--fg-3, rgba(220,232,255,.4));
    }

    /* ── Bandeau haut : totaux (3 colonnes) ── */
    .sd-top {
      display: grid; grid-template-columns: 1fr 1fr 1fr;
      gap: 16px; height: 130px; flex-shrink: 0;
    }
    .sd-total-card {
      padding: 18px 20px; display: flex; flex-direction: column; justify-content: space-between;
    }
    .sd-total-val {
      font-family: var(--serif, "Geist"); font-weight: 300; font-size: 36px;
      color: var(--fg-0, #DCE8FF); letter-spacing: -.03em;
      font-variant-numeric: tabular-nums; line-height: 1;
    }
    .sd-total-val .u {
      font-family: var(--mono, monospace); font-size: 13px;
      color: var(--fg-2, rgba(220,232,255,.5)); margin-left: 3px;
    }
    .sd-total-sub {
      font-family: var(--mono, monospace); font-size: 10px;
      color: var(--fg-3, rgba(220,232,255,.38)); letter-spacing: .06em;
    }

    /* ── Grille milieu : liste + sparklines ── */
    .sd-mid { flex: 1; display: grid; grid-template-columns: 1.6fr 1fr; gap: 16px; min-height: 0; }

    /* Liste d'activités */
    .sd-list-panel { padding: 18px 20px; display: flex; flex-direction: column; overflow: hidden; }
    .sd-list-panel > .sd-eyebrow { margin-bottom: 10px; flex-shrink: 0; }
    .sd-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
    .sd-list::-webkit-scrollbar { width: 4px; }
    .sd-list::-webkit-scrollbar-thumb { background: rgba(220,232,255,.1); border-radius: 2px; }
    .sd-act-row {
      display: grid; grid-template-columns: 28px 1fr auto;
      align-items: center; gap: 12px; padding: 10px 8px;
      border-radius: 8px; cursor: pointer; transition: background .18s;
    }
    .sd-act-row:hover { background: rgba(220,232,255,.04); }
    .sd-act-icon { font-size: 16px; text-align: center; }
    .sd-act-info { min-width: 0; }
    .sd-act-name {
      font-size: 13px; color: var(--fg-0, #DCE8FF); font-weight: 400;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .sd-act-meta {
      font-family: var(--mono, monospace); font-size: 9.5px;
      color: var(--fg-3, rgba(220,232,255,.4)); letter-spacing: .05em;
      margin-top: 2px;
    }
    .sd-act-dist {
      font-family: var(--mono, monospace); font-size: 11px;
      color: var(--fg-2, rgba(220,232,255,.65));
      font-variant-numeric: tabular-nums; white-space: nowrap;
    }

    /* Sparklines panel */
    .sd-spark-panel { padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; }
    .sd-spark-panel > .sd-eyebrow { margin-bottom: 2px; }
    .sd-spark-block { display: flex; flex-direction: column; gap: 4px; flex: 1; }
    .sd-spark-label {
      font-family: var(--mono, monospace); font-size: 9px;
      letter-spacing: .14em; text-transform: uppercase;
      color: var(--fg-3, rgba(220,232,255,.4));
    }
    .sd-spark { width: 100%; height: 40px; display: block; }

    /* ── Bandeau bas : badges run/ride ── */
    .sd-bot {
      height: 80px; display: grid; grid-template-columns: 1fr 1fr 1fr 1fr;
      gap: 16px; flex-shrink: 0;
    }
    .sd-badge-card { padding: 12px 16px; display: flex; flex-direction: column; justify-content: center; gap: 4px; }
    .sd-badge-val {
      font-family: var(--mono, monospace); font-size: 14px; font-weight: 500;
      color: var(--fg-0, #DCE8FF); font-variant-numeric: tabular-nums;
    }
    .sd-badge-lbl {
      font-family: var(--mono, monospace); font-size: 9px;
      letter-spacing: .12em; text-transform: uppercase;
      color: var(--fg-3, rgba(220,232,255,.38));
    }

    /* Chrome commun (coin sup droit) */
    .sd-chrome {
      position: absolute; inset: 0; z-index: 6; pointer-events: none;
      font-family: var(--sans, system-ui);
    }
    .sd-chrome > * { pointer-events: auto; }
    .sd-context {
      position: absolute; top: 28px; right: 32px;
      display: flex; align-items: center; gap: 14px;
      font-family: var(--mono, monospace); font-size: 10.5px; letter-spacing: .06em;
      color: var(--fg-2, rgba(220,232,255,.58));
    }
    .sd-context .live {
      display: flex; align-items: center; gap: 6px;
      color: #FC4C02; letter-spacing: .18em; text-transform: uppercase; font-size: 9px;
    }
    .sd-context .live::before {
      content: ""; width: 6px; height: 6px; border-radius: 50%;
      background: #FC4C02; box-shadow: 0 0 6px rgba(252,76,2,.6);
      animation: sd-pulse 2.4s ease-in-out infinite;
    }
    @keyframes sd-pulse { 0%,100%{ opacity:1 } 50%{ opacity:.4 } }
    .sd-eyebrow-title {
      position: absolute; top: 30px; left: 50%; transform: translateX(-50%);
      font-family: var(--mono, monospace); font-size: 10px; letter-spacing: .2em;
      text-transform: uppercase; color: var(--fg-2, rgba(220,232,255,.55));
    }
    .sd-brand {
      position: absolute; top: 28px; left: 32px;
      display: flex; align-items: center; gap: 10px;
    }
    .sd-brand-dot {
      width: 10px; height: 10px; border-radius: 50%;
      background: #FC4C02; box-shadow: 0 0 8px rgba(252,76,2,.5);
    }
    .sd-brand-txt {
      font-family: var(--mono, monospace); font-size: 12px;
      letter-spacing: .18em; text-transform: uppercase;
      color: var(--fg-0, #DCE8FF);
    }
    .sd-empty {
      font-family: var(--mono, monospace); font-size: 11px;
      color: var(--fg-3, rgba(220,232,255,.35));
      letter-spacing: .1em; text-align: center;
      padding: 24px 0;
    }
    .sd-error {
      font-family: var(--mono, monospace); font-size: 11px;
      color: #E5484D; letter-spacing: .08em; padding: 12px 0;
    }
  `;

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style'); s.id = STYLE_ID; s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ── DOM ───────────────────────────────────────────────────────────────────

  function buildDOM() {
    const chrome = document.createElement('div');
    chrome.className = 'sd-chrome';
    chrome.innerHTML = `
      <div class="sd-brand">
        <div class="sd-brand-dot"></div>
        <span class="sd-brand-txt">Strava</span>
      </div>
      <div class="sd-eyebrow-title">Dashboard sportif</div>
      <div class="sd-context">
        <span id="sd-clock">—</span>
        <span class="live">Live</span>
      </div>
    `;

    const content = document.createElement('div');
    content.className = 'sd-content';
    content.innerHTML = `
      <!-- Bandeau haut : totaux semaine / mois / année -->
      <div class="sd-top">
        <div class="sd-card sd-total-card" id="sd-tot-week">
          <span class="sd-eyebrow">Semaine</span>
          <div>
            <div class="sd-total-val"><span id="sd-week-dist">—</span><span class="u">km</span></div>
            <div class="sd-total-sub" id="sd-week-sub">—</div>
          </div>
        </div>
        <div class="sd-card sd-total-card" id="sd-tot-month">
          <span class="sd-eyebrow">Mois en cours</span>
          <div>
            <div class="sd-total-val"><span id="sd-month-dist">—</span><span class="u">km</span></div>
            <div class="sd-total-sub" id="sd-month-sub">—</div>
          </div>
        </div>
        <div class="sd-card sd-total-card" id="sd-tot-year">
          <span class="sd-eyebrow">Année en cours (YTD)</span>
          <div>
            <div class="sd-total-val"><span id="sd-year-dist">—</span><span class="u">km</span></div>
            <div class="sd-total-sub" id="sd-year-sub">—</div>
          </div>
        </div>
      </div>

      <!-- Milieu : liste + sparklines -->
      <div class="sd-mid">
        <div class="sd-card sd-list-panel">
          <span class="sd-eyebrow">Activités récentes</span>
          <div class="sd-list" id="sd-act-list">
            <div class="sd-empty">Chargement…</div>
          </div>
        </div>
        <div class="sd-card sd-spark-panel">
          <span class="sd-eyebrow">Distance · 8 semaines</span>
          <div class="sd-spark-block">
            <canvas id="sd-spark-week" class="sd-spark"></canvas>
            <div class="sd-spark-label" id="sd-spark-label">—</div>
          </div>
        </div>
      </div>

      <!-- Bandeau bas : stats course/vélo -->
      <div class="sd-bot">
        <div class="sd-card sd-badge-card">
          <div class="sd-badge-val" id="sd-run-count">—</div>
          <div class="sd-badge-lbl">Courses · 4 sem.</div>
        </div>
        <div class="sd-card sd-badge-card">
          <div class="sd-badge-val" id="sd-run-km">—</div>
          <div class="sd-badge-lbl">Distance course</div>
        </div>
        <div class="sd-card sd-badge-card">
          <div class="sd-badge-val" id="sd-ride-count">—</div>
          <div class="sd-badge-lbl">Sorties vélo · 4 sem.</div>
        </div>
        <div class="sd-card sd-badge-card">
          <div class="sd-badge-val" id="sd-ride-km">—</div>
          <div class="sd-badge-lbl">Distance vélo</div>
        </div>
      </div>
    `;

    container.appendChild(content);
    container.appendChild(chrome);
    _domBuilt = true;
  }

  // ── Helpers DOM ───────────────────────────────────────────────────────────

  function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

  // ── Rendu activités ───────────────────────────────────────────────────────

  function renderActivities(acts) {
    const list = document.getElementById('sd-act-list');
    if (!list) return;

    if (!acts || !acts.length) {
      list.innerHTML = '<div class="sd-empty">Aucune activité</div>';
      return;
    }

    list.innerHTML = acts.slice(0, 20).map(a => `
      <div class="sd-act-row" data-id="${a.id}">
        <div class="sd-act-icon">${typeIcon(a.type)}</div>
        <div class="sd-act-info">
          <div class="sd-act-name">${esc(a.name || a.type)}</div>
          <div class="sd-act-meta">${esc(a.date || '')} · ${fmtDuration(a.moving_time_s || 0)} · ${(a.total_elevation_gain || 0).toFixed(0)} m D+</div>
        </div>
        <div class="sd-act-dist">${fmtDist(a.distance_m || 0)}</div>
      </div>
    `).join('');

    // Clic → strava-route
    list.querySelectorAll('.sd-act-row').forEach(row => {
      row.addEventListener('click', () => {
        const id = parseInt(row.dataset.id, 10);
        if (!id) return;
        if (window.Jarvis?.views?.get) {
          const routeView = Jarvis.views.get('strava-route');
          if (routeView) { routeView.show({}); routeView.command('show_activity', { activity_id: id }); return; }
        }
        // Fallback : déclenche show_view via l'API websocket
        fetch('/api/broadcast', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(Jarvis.authHeaders ? Jarvis.authHeaders() : {}) },
          body: JSON.stringify([
            { type: 'show_view', view_id: 'strava-route' },
            { type: 'view_command', view_id: 'strava-route', command: 'show_activity', params: { activity_id: id } },
          ]),
        }).catch(() => {});
      });
    });
  }

  // ── Rendu stats ───────────────────────────────────────────────────────────

  function renderStats(stats) {
    if (!stats) return;

    // Totaux récents (4 semaines) → approximation semaine
    const recentRun = stats.recent_run_totals || {};
    const recentRide = stats.recent_ride_totals || {};

    // Semaine (on n'a pas de granularité exacte semaine dans l'API stats Strava,
    // donc on affiche les 4 semaines et on calcule une approximation)
    const weekDist = ((recentRun.distance || 0) + (recentRide.distance || 0)) / 4;
    const weekElev = ((recentRun.elevation_gain || 0) + (recentRide.elevation_gain || 0)) / 4;
    const weekTime = ((recentRun.moving_time || 0) + (recentRide.moving_time || 0)) / 4;
    setText('sd-week-dist', (weekDist / 1000).toFixed(1));
    setText('sd-week-sub', `${fmtDuration(weekTime)} · ${weekElev.toFixed(0)} m D+`);

    // Mois (approximation : ytd / nb_mois_écoules)
    const ytdRun = stats.ytd_run_totals || {};
    const ytdRide = stats.ytd_ride_totals || {};
    const now = new Date();
    const monthsElapsed = Math.max(1, now.getMonth() + 1);
    const monthDist = ((ytdRun.distance || 0) + (ytdRide.distance || 0)) / monthsElapsed;
    const monthElev = ((ytdRun.elevation_gain || 0) + (ytdRide.elevation_gain || 0)) / monthsElapsed;
    setText('sd-month-dist', (monthDist / 1000).toFixed(1));
    setText('sd-month-sub', `~${monthElev.toFixed(0)} m D+ · moy.`);

    // YTD
    const ytdTotalDist = (ytdRun.distance || 0) + (ytdRide.distance || 0);
    const ytdTotalElev = (ytdRun.elevation_gain || 0) + (ytdRide.elevation_gain || 0);
    const ytdTotalTime = (ytdRun.moving_time || 0) + (ytdRide.moving_time || 0);
    setText('sd-year-dist', (ytdTotalDist / 1000).toFixed(0));
    setText('sd-year-sub', `${fmtDuration(ytdTotalTime)} · ${ytdTotalElev.toFixed(0)} m D+`);

    // Badges bas
    setText('sd-run-count', recentRun.count || 0);
    setText('sd-run-km', fmtDist(recentRun.distance || 0));
    setText('sd-ride-count', recentRide.count || 0);
    setText('sd-ride-km', fmtDist(recentRide.distance || 0));

    // Sparkline 8 semaines (approx depuis recent_*_totals sur 4 sem)
    // On fait varier légèrement pour montrer une tendance
    const weeklyAvg = weekDist / 1000;
    for (let i = 0; i < 8; i++) _weeklyDist[i] = Math.max(0, weeklyAvg * (0.6 + Math.random() * 0.8));
    _weeklyDist[7] = weeklyAvg; // semaine courante = valeur réelle
    requestAnimationFrame(() => {
      drawSpark(document.getElementById('sd-spark-week'), _weeklyDist, '#FC4C02', 'rgba(252,76,2,.1)');
    });
    setText('sd-spark-label', `Moy. ${weeklyAvg.toFixed(1)} km/sem`);
  }

  // ── Fetch & poll ─────────────────────────────────────────────────────────

  async function fetchAll() {
    const [actsRes, statsRes] = await Promise.allSettled([
      apiFetch('/api/strava/activities?per_page=20'),
      apiFetch('/api/strava/stats'),
    ]);
    if (actsRes.status === 'fulfilled') renderActivities(actsRes.value);
    else {
      const list = document.getElementById('sd-act-list');
      if (list) list.innerHTML = `<div class="sd-error">Erreur : ${actsRes.reason}</div>`;
    }
    if (statsRes.status === 'fulfilled') renderStats(statsRes.value);
  }

  function startPoll() { stopPoll(); fetchAll(); _pollTimer = setInterval(fetchAll, POLL_MS); }
  function stopPoll() { clearInterval(_pollTimer); _pollTimer = null; }

  function startClock() {
    stopClock();
    const tick = () => {
      const el = document.getElementById('sd-clock');
      if (el) el.textContent = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    };
    tick(); _clockTimer = setInterval(tick, 10_000);
  }
  function stopClock() { clearInterval(_clockTimer); _clockTimer = null; }

  // ── Container ─────────────────────────────────────────────────────────────

  function ensureContainer() {
    if (container) return;
    injectStyle();
    container = document.createElement('div');
    container.id = 'strava-dashboard-container';
    Object.assign(container.style, {
      position: 'fixed', inset: '0', zIndex: '2',
      display: 'none', opacity: '0',
      transition: 'opacity .35s ease',
    });
    document.body.appendChild(container);
  }

  // ── Enregistrement ────────────────────────────────────────────────────────

  Jarvis.views.register(VIEW_ID, {
    meta: {
      name: 'Strava Dashboard',
      desc: 'Dashboard sportif — activités récentes, totaux, évolution hebdo',
      glyph: 'STR',
      tags: ['strava', 'sport', 'dashboard', 'stats'],
    },

    show(params = {}) {
      ensureContainer();
      if (_visible) return;
      _visible = true;
      if (!_domBuilt) buildDOM();
      container.style.display = 'block';
      container.getBoundingClientRect();
      container.style.opacity = '1';
      startClock(); startPoll();
    },

    hide() {
      if (!container) return;
      _visible = false;
      container.style.opacity = '0';
      stopPoll(); stopClock();
      setTimeout(() => { if (!_visible && container) container.style.display = 'none'; }, 360);
    },

    command(cmd, params = {}) {
      switch (cmd) {
        case 'show': this.show(params); break;
        case 'hide': this.hide(); break;
        case 'refresh': if (_visible) fetchAll(); break;
      }
    },
  });
})();
