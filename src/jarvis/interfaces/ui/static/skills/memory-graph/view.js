/**
 * Vue Memory Graph — le « cerveau » de Jarvis.
 * Graphe force-directed D3 v7 des facts structurés (MemoryKernel) et des
 * fichiers thématiques Markdown (topics) :
 *   - facts = cercles colorés par category, rayon ∝ importance
 *   - topics = hexagones plus gros, colorés par THÈME (groupes de fichiers
 *     avec couleur choisie), reliés aux facts de même catégorie
 *   - relation SUPERSEDES = pointillé rouge (contradiction résolue)
 *   - pulsation légère des nœuds + liens semi-transparents animés (CSS only)
 *   - clic fact  → panneau latéral détail (/api/memory/facts/{id})
 *   - clic topic → GRAND panneau (≥ 50 % de l'écran, graphe décalé à gauche)
 *     avec lecture Markdown, ÉDITION + enregistrement, changement de thème,
 *     suppression ; bouton « + Fichier » pour créer un nouveau topic.
 *
 * Réutilise les patterns existants : loadScript de globe/view.js, chrome et
 * container de system-monitor/view.js, Jarvis.views.register().
 *
 * Commandes view_command (pilotées par ShowViewTool / memory_search) :
 *   - highlight_nodes  params: { fact_ids: [...], topic_filenames: [...] }
 *   - clear_highlight
 *   - refresh
 */
(function () {
  if (!window.Jarvis?.views) return;

  const VIEW_ID = 'memory-graph';
  const STYLE_ID = 'mg-styles';
  const D3_SRC = 'https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js';

  // ── Helpers identiques à globe/view.js ─────────────────────────────────────

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`Échec chargement ${src}`));
      document.head.appendChild(s);
    });
  }

  let _d3Loading = null;
  function loadD3() {
    if (window.d3) return Promise.resolve();
    if (!_d3Loading) _d3Loading = loadScript(D3_SRC);
    return _d3Loading;
  }

  function authHeaders(extra) {
    const base = window.Jarvis && Jarvis.authHeaders ? Jarvis.authHeaders() : {};
    return Object.assign({}, base, extra || {});
  }

  function apiFetch(path) {
    return fetch(window.location.origin + path, { headers: authHeaders() })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function apiSend(path, method, body) {
    return fetch(window.location.origin + path, {
      method,
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(async (r) => {
      if (!r.ok) {
        let detail = r.status;
        try { detail = (await r.json()).detail || r.status; } catch (_) {}
        throw new Error(detail);
      }
      return r.json();
    });
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Rendu Markdown minimal (titres, gras, listes) — évite une lib externe.
  function renderMd(md) {
    const lines = esc(md).split(/\r?\n/);
    const out = [];
    let inList = false;
    for (const raw of lines) {
      const line = raw.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`(.+?)`/g, '<code>$1</code>');
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      const li = line.match(/^\s*[-*]\s+(.*)$/);
      if (li) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push(`<li>${li[1]}</li>`);
        continue;
      }
      if (inList) { out.push('</ul>'); inList = false; }
      if (h) out.push(`<h${h[1].length + 2}>${h[2]}</h${h[1].length + 2}>`);
      else if (line.trim()) out.push(`<p>${line}</p>`);
    }
    if (inList) out.push('</ul>');
    return out.join('');
  }

  // ── Couleurs par catégorie (palette stable par hash) ───────────────────────

  const PALETTE = ['#4A9EFF', '#36D399', '#B8963E', '#C084FC', '#F472B6', '#22D3EE', '#FB923C', '#A3E635'];
  const TOPIC_COLOR = '#DCE8FF';
  function catColor(cat) {
    let h = 0;
    for (let i = 0; i < cat.length; i++) h = (h * 31 + cat.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function hexagonPath(r) {
    const pts = [];
    for (let i = 0; i < 6; i++) {
      const a = (Math.PI / 3) * i - Math.PI / 2;
      pts.push(`${(r * Math.cos(a)).toFixed(2)},${(r * Math.sin(a)).toFixed(2)}`);
    }
    return `M${pts.join('L')}Z`;
  }

  // ── État interne ───────────────────────────────────────────────────────────

  let container = null, _visible = false, _domBuilt = false;
  let svg = null, gRoot = null, simulation = null;
  let _graph = { nodes: [], links: [] };
  let _themes = [];               // [{name, color, files}]
  let _highlight = null;          // { factIds: Set, topicFiles: Set } ou null
  let _panelTopic = null;         // filename du topic ouvert dans le panneau

  // ── CSS ────────────────────────────────────────────────────────────────────

  const CSS = `
    #memory-graph-container { font-family:var(--sans,"Geist",system-ui,sans-serif); color:var(--fg-1,rgba(220,232,255,.78)); background:var(--bg-0,#06080D); overflow:hidden; }
    .mg-svg { position:absolute; inset:0; width:100%; height:100%; cursor:grab; transition:transform .35s cubic-bezier(.4,0,.2,1); }
    .mg-svg:active { cursor:grabbing; }
    .mg-shift .mg-svg { transform:translateX(-25vw); }
    .mg-header { position:absolute; top:28px; left:32px; z-index:5; display:flex; flex-direction:column; gap:4px; pointer-events:none; }
    .mg-title { font-family:var(--display-mark,"Landasans",var(--serif,"Geist")); font-size:14px; letter-spacing:.22em; color:var(--fg-0,#DCE8FF); }
    .mg-sub { font-family:var(--mono,"Geist Mono",monospace); font-size:9px; letter-spacing:.14em; text-transform:uppercase; color:var(--fg-3,rgba(220,232,255,.4)); }
    .mg-toolbar { position:absolute; top:26px; right:32px; z-index:8; display:flex; gap:8px; pointer-events:auto; }
    .mg-legend { position:absolute; bottom:26px; left:32px; z-index:5; display:flex; flex-wrap:wrap; gap:10px 16px; max-width:46vw; }
    .mg-leg { display:flex; align-items:center; gap:6px; font-family:var(--mono,monospace); font-size:9.5px; letter-spacing:.08em; color:var(--fg-2,rgba(220,232,255,.58)); }
    .mg-leg .dot { width:8px; height:8px; border-radius:50%; }
    .mg-leg .hex { width:9px; height:9px; clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%); opacity:.9; }
    .mg-leg .sup { width:16px; height:0; border-top:2px dashed #E5484D; }

    /* boutons génériques */
    .mg-btn { cursor:pointer; font-family:var(--mono,monospace); font-size:10px; letter-spacing:.1em; text-transform:uppercase; padding:6px 12px; border-radius:6px; border:1px solid var(--line-2,rgba(220,232,255,.12)); background:rgba(10,14,22,.7); color:var(--fg-1,rgba(220,232,255,.78)); backdrop-filter:blur(10px); transition:border-color .2s,color .2s; }
    .mg-btn:hover { border-color:var(--accent,#4A9EFF); color:var(--fg-0,#DCE8FF); }
    .mg-btn.primary { border-color:var(--accent-line,rgba(74,158,255,.4)); background:var(--accent-soft,rgba(74,158,255,.12)); color:var(--accent,#4A9EFF); }
    .mg-btn.danger:hover { border-color:#E5484D; color:#E5484D; }
    .mg-btn:disabled { opacity:.4; cursor:default; }

    /* nœuds : pulsation légère via CSS (aucune boucle JS) */
    .mg-node { cursor:pointer; }
    .mg-node .halo { animation:mg-pulse 3.2s ease-in-out infinite; transform-box:fill-box; transform-origin:center; }
    @keyframes mg-pulse { 0%,100%{ opacity:.18; transform:scale(1); } 50%{ opacity:.42; transform:scale(1.25); } }
    .mg-node text { font-family:var(--mono,monospace); font-size:8.5px; fill:var(--fg-3,rgba(220,232,255,.45)); pointer-events:none; }
    .mg-node.dim { opacity:.13; }
    .mg-node.hl .halo { animation-duration:1.4s; opacity:.6; }
    .mg-node.hl .core { stroke:#fff; stroke-width:1.6px; }
    .mg-node.superseded { opacity:.38; }
    .mg-node.superseded.dim { opacity:.08; }

    /* liens semi-transparents animés */
    .mg-link { stroke:rgba(220,232,255,.16); stroke-width:1; fill:none; }
    .mg-link.flow { stroke-dasharray:3 5; animation:mg-flow 1.6s linear infinite; }
    @keyframes mg-flow { to { stroke-dashoffset:-8; } }
    .mg-link.supersedes { stroke:rgba(229,72,77,.75); stroke-dasharray:5 4; stroke-width:1.4; }
    .mg-link.topic { stroke:rgba(220,232,255,.10); stroke-dasharray:1 4; }
    .mg-link.dim { opacity:.06; }
    .mg-link.hl { stroke-opacity:1; stroke-width:1.8; }

    /* panneau latéral — étroit (facts) ou large (topics : ≥ 50 % de l'écran) */
    .mg-panel { position:absolute; top:0; right:0; bottom:0; width:380px; z-index:8; background:rgba(8,11,18,.94); border-left:1px solid var(--line-2,rgba(220,232,255,.1)); backdrop-filter:blur(16px); transform:translateX(110%); transition:transform .32s cubic-bezier(.4,0,.2,1), width .32s cubic-bezier(.4,0,.2,1); display:flex; flex-direction:column; pointer-events:auto; }
    .mg-panel.open { transform:translateX(0); }
    .mg-panel.wide { width:max(50vw, 560px); }
    .mg-panel-head { padding:22px 24px 14px; border-bottom:1px solid var(--line-1,rgba(220,232,255,.06)); flex-shrink:0; }
    .mg-panel-kind { font-family:var(--mono,monospace); font-size:9px; letter-spacing:.2em; text-transform:uppercase; color:var(--fg-3,rgba(220,232,255,.4)); margin-bottom:8px; display:flex; align-items:center; justify-content:space-between; }
    .mg-panel-close { pointer-events:auto; cursor:pointer; border:1px solid var(--line-2,rgba(220,232,255,.1)); border-radius:4px; background:none; color:var(--fg-2,rgba(220,232,255,.58)); font-size:11px; padding:2px 8px; }
    .mg-panel-close:hover { color:var(--fg-0,#DCE8FF); }
    .mg-panel-title { font-size:15px; font-weight:500; color:var(--fg-0,#DCE8FF); line-height:1.4; }
    .mg-panel-body { padding:18px 24px; overflow-y:auto; flex:1; font-size:12.5px; line-height:1.65; display:flex; flex-direction:column; }
    .mg-panel-body::-webkit-scrollbar { width:6px; } .mg-panel-body::-webkit-scrollbar-thumb { background:rgba(220,232,255,.12); border-radius:3px; }
    .mg-kv { display:grid; grid-template-columns:110px 1fr; gap:5px 12px; margin-bottom:16px; }
    .mg-kv .k { font-family:var(--mono,monospace); font-size:9.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--fg-3,rgba(220,232,255,.4)); padding-top:2px; }
    .mg-kv .v { color:var(--fg-1,rgba(220,232,255,.82)); word-break:break-word; }
    .mg-sec { font-family:var(--mono,monospace); font-size:9px; letter-spacing:.18em; text-transform:uppercase; color:var(--fg-3,rgba(220,232,255,.4)); margin:18px 0 8px; padding-top:14px; border-top:1px solid var(--line-1,rgba(220,232,255,.06)); }
    .mg-obs { font-family:var(--mono,monospace); font-size:10.5px; color:var(--fg-2,rgba(220,232,255,.58)); padding:4px 0; }
    .mg-contra { border-left:2px dashed #E5484D; padding:4px 0 4px 10px; margin:6px 0; color:var(--fg-2,rgba(220,232,255,.62)); }
    .mg-md h3,.mg-md h4,.mg-md h5,.mg-md h6 { color:var(--fg-0,#DCE8FF); font-weight:500; margin:14px 0 6px; }
    .mg-md p { margin:6px 0; } .mg-md ul { margin:6px 0; padding-left:18px; }
    .mg-md code { font-family:var(--mono,monospace); font-size:11px; background:rgba(220,232,255,.07); padding:1px 4px; border-radius:3px; }
    .mg-empty { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; z-index:4; font-family:var(--mono,monospace); font-size:11px; letter-spacing:.1em; color:var(--fg-3,rgba(220,232,255,.4)); }

    /* barre d'actions + éditeur du panneau topic */
    .mg-actions { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; flex-shrink:0; }
    .mg-editor { flex:1; display:flex; flex-direction:column; gap:10px; min-height:0; }
    .mg-edit-area { flex:1; min-height:220px; width:100%; box-sizing:border-box; background:rgba(220,232,255,.035); border:1px solid var(--line-2,rgba(220,232,255,.12)); border-radius:8px; color:var(--fg-0,#DCE8FF); font-family:var(--mono,monospace); font-size:12px; line-height:1.6; padding:14px; resize:none; outline:none; }
    .mg-edit-area:focus { border-color:var(--accent-line,rgba(74,158,255,.4)); }
    .mg-input { box-sizing:border-box; background:rgba(220,232,255,.035); border:1px solid var(--line-2,rgba(220,232,255,.12)); border-radius:6px; color:var(--fg-0,#DCE8FF); font-family:var(--mono,monospace); font-size:12px; padding:8px 12px; outline:none; }
    .mg-input:focus { border-color:var(--accent-line,rgba(74,158,255,.4)); }
    select.mg-input { cursor:pointer; }
    select.mg-input option { background:#0A0E16; }
    input[type=color].mg-color { width:34px; height:30px; padding:2px; background:rgba(220,232,255,.035); border:1px solid var(--line-2,rgba(220,232,255,.12)); border-radius:6px; cursor:pointer; }
    .mg-theme-row { display:flex; align-items:center; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
    .mg-theme-row .lbl { font-family:var(--mono,monospace); font-size:9.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--fg-3,rgba(220,232,255,.4)); }
    .mg-theme-dot { width:10px; height:10px; clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%); flex-shrink:0; }
    .mg-newtheme { display:none; flex-direction:column; gap:8px; margin:0 0 14px; padding:12px; border:1px dashed var(--line-2,rgba(220,232,255,.16)); border-radius:8px; background:rgba(74,158,255,.06); }
    .mg-newtheme.show { display:flex; }
    .mg-newtheme-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .mg-newtheme .hint { font-family:var(--mono,monospace); font-size:9.5px; letter-spacing:.06em; color:var(--fg-3,rgba(220,232,255,.45)); }
    .mg-msg { font-family:var(--mono,monospace); font-size:10px; color:var(--fg-3,rgba(220,232,255,.45)); min-height:14px; }
    .mg-msg.err { color:#E5484D; }
  `;

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement('style'); s.id = STYLE_ID; s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ── DOM ────────────────────────────────────────────────────────────────────

  function ensureContainer() {
    if (container) return;
    injectStyle();
    container = document.createElement('div');
    container.id = `${VIEW_ID}-container`;
    Object.assign(container.style, { position: 'fixed', inset: '0', zIndex: '5', display: 'none', opacity: '0', transition: 'opacity .35s ease' });
    document.body.appendChild(container);
  }

  function buildDOM() {
    container.innerHTML = `
      <div class="mg-header">
        <span class="mg-title">MÉMOIRE</span>
        <span class="mg-sub" id="mg-stats">chargement…</span>
      </div>
      <div class="mg-toolbar">
        <button class="mg-btn primary" id="mg-new-file">＋ Fichier</button>
      </div>
      <div class="mg-legend" id="mg-legend"></div>
      <div class="mg-panel" id="mg-panel">
        <div class="mg-panel-head">
          <div class="mg-panel-kind"><span id="mg-panel-kind">—</span><button class="mg-panel-close" id="mg-panel-close">✕</button></div>
          <div class="mg-panel-title" id="mg-panel-title">—</div>
        </div>
        <div class="mg-panel-body" id="mg-panel-body"></div>
      </div>`;
    document.getElementById('mg-panel-close').addEventListener('click', closePanel);
    document.getElementById('mg-new-file').addEventListener('click', openCreatePanel);
    _domBuilt = true;
  }

  // ── Panneau latéral ────────────────────────────────────────────────────────

  function openPanel(kind, title, wide) {
    const p = document.getElementById('mg-panel');
    document.getElementById('mg-panel-kind').textContent = kind;
    document.getElementById('mg-panel-title').textContent = title;
    p.classList.toggle('wide', !!wide);
    p.classList.add('open');
    container.classList.toggle('mg-shift', !!wide);
    // Signale au chrome de la home (home.css) qu'un panneau large est ouvert :
    // masque la signature (nom + heure) et la barre de contrôles.
    document.body.classList.toggle('view-panel-wide', !!wide);
  }

  function closePanel() {
    const p = document.getElementById('mg-panel');
    if (p) { p.classList.remove('open'); p.classList.remove('wide'); }
    if (container) container.classList.remove('mg-shift');
    document.body.classList.remove('view-panel-wide');
    _panelTopic = null;
  }

  function panelBody() { return document.getElementById('mg-panel-body'); }

  // ── Panneau FACT (étroit, lecture seule) ───────────────────────────────────

  async function showFactDetail(node) {
    try {
      const f = await apiFetch(`/api/memory/facts/${encodeURIComponent(node.id)}`);
      const kv = `
        <div class="mg-kv">
          <span class="k">Sujet</span><span class="v">${esc(f.subject)}</span>
          <span class="k">Prédicat</span><span class="v">${esc(f.predicate)}</span>
          <span class="k">Objet</span><span class="v">${esc(f.object)}</span>
          <span class="k">Catégorie</span><span class="v">${esc(f.category)}</span>
          <span class="k">Statut</span><span class="v">${esc(f.status)}</span>
          <span class="k">Confiance</span><span class="v">${(f.confidence * 100).toFixed(0)} %</span>
          <span class="k">Importance</span><span class="v">${(f.importance * 100).toFixed(0)} %</span>
          <span class="k">Support</span><span class="v">${f.support_count} observation(s)</span>
          <span class="k">Décroissance</span><span class="v">${esc(f.decay_policy)}</span>
          <span class="k">Vu la dernière fois</span><span class="v">${new Date(f.last_seen_at).toLocaleString('fr-FR')}</span>
        </div>`;
      const obs = f.observations.length
        ? `<div class="mg-sec">Historique des observations</div>` + f.observations.map((o) =>
            `<div class="mg-obs">${new Date(o.created_at).toLocaleDateString('fr-FR')} · ${esc(o.observation_type)} (Δ ${o.confidence_delta >= 0 ? '+' : ''}${o.confidence_delta})</div>`
          ).join('')
        : '';
      const contra = f.contradictions.length
        ? `<div class="mg-sec">Contradictions résolues</div>` + f.contradictions.map((c) =>
            `<div class="mg-contra">${esc(c.subject)} — ${esc(c.predicate)} — ${esc(c.object)}<br><span style="font-size:10px;opacity:.6">remplacé (${esc(c.status)})</span></div>`
          ).join('')
        : '';
      openPanel('FACT · ' + esc(f.category), `${f.subject} ${f.predicate} ${f.object}`, false);
      panelBody().innerHTML = kv + obs + contra;
    } catch (e) {
      openPanel('FACT', node.label || node.id, false);
      panelBody().innerHTML = `<p>Détail indisponible (${esc(e.message)})</p>`;
    }
  }

  // ── Thèmes : sélecteur réutilisable ────────────────────────────────────────

  function themeOf(filename) {
    for (const t of _themes) if ((t.files || []).includes(filename)) return t;
    return null;
  }

  function themeSelectorHtml(current) {
    const opts = ['<option value="">— sans thème —</option>']
      .concat(_themes.map((t) =>
        `<option value="${esc(t.name)}" ${t.name === current ? 'selected' : ''}>${esc(t.name)}</option>`));
    const cur = _themes.find((t) => t.name === current);
    return `
      <div class="mg-theme-row">
        <span class="lbl">Thème</span>
        <span class="mg-theme-dot" id="mg-theme-dot" style="background:${cur ? cur.color : 'rgba(220,232,255,.25)'}"></span>
        <select class="mg-input" id="mg-theme-select">${opts.join('')}</select>
        <button class="mg-btn" type="button" id="mg-theme-new-btn">＋ Thème</button>
      </div>
      <div class="mg-newtheme" id="mg-newtheme">
        <span class="hint">Nouveau thème — nom + couleur, puis Créer</span>
        <div class="mg-newtheme-row">
          <input class="mg-input" id="mg-newtheme-name" placeholder="ex. Santé" style="width:160px">
          <input type="color" class="mg-color" id="mg-newtheme-color" value="#4A9EFF" title="Couleur">
          <button class="mg-btn primary" type="button" id="mg-newtheme-ok">Créer</button>
          <button class="mg-btn" type="button" id="mg-newtheme-cancel">Annuler</button>
        </div>
      </div>`;
  }

  // Branche le sélecteur : onChange(themeName|null) après choix ou création.
  function wireThemeSelector(onChange) {
    const sel = document.getElementById('mg-theme-select');
    const form = document.getElementById('mg-newtheme');
    const newBtn = document.getElementById('mg-theme-new-btn');
    if (!sel || !form || !newBtn) return;

    sel.addEventListener('change', () => {
      form.classList.remove('show');
      const t = _themes.find((x) => x.name === sel.value) || null;
      const dot = document.getElementById('mg-theme-dot');
      if (dot) dot.style.background = t ? t.color : 'rgba(220,232,255,.25)';
      onChange(sel.value || null);
    });

    newBtn.addEventListener('click', () => {
      form.classList.toggle('show');
      if (form.classList.contains('show')) {
        const nameInput = document.getElementById('mg-newtheme-name');
        if (nameInput) nameInput.focus();
      }
    });

    const cancel = document.getElementById('mg-newtheme-cancel');
    if (cancel) cancel.addEventListener('click', () => form.classList.remove('show'));

    document.getElementById('mg-newtheme-ok').addEventListener('click', async () => {
      const name = document.getElementById('mg-newtheme-name').value.trim();
      const color = document.getElementById('mg-newtheme-color').value;
      if (!name) { setMsg('Nom du thème requis', true); return; }
      try {
        await apiSend('/api/memory/themes', 'POST', { name, color });
        await loadThemes();
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
        sel.value = name;
        form.classList.remove('show');
        const dot = document.getElementById('mg-theme-dot');
        if (dot) dot.style.background = color;
        onChange(name);
        setMsg(`Thème « ${name} » créé`);
      } catch (e) { setMsg(`Erreur thème : ${e.message}`, true); }
    });
  }

  function setMsg(text, isErr) {
    const el = document.getElementById('mg-msg');
    if (el) { el.textContent = text || ''; el.classList.toggle('err', !!isErr); }
  }

  // ── Panneau TOPIC (large : lecture / édition / thème / suppression) ────────

  async function showTopicDetail(node) {
    _panelTopic = node.filename;
    openPanel('FICHIER THÉMATIQUE', node.filename, true);
    panelBody().innerHTML = '<p>chargement…</p>';
    try {
      const [t] = await Promise.all([
        apiFetch(`/api/memory/topics/${encodeURIComponent(node.filename)}`),
        loadThemes(),
      ]);
      if (_panelTopic !== node.filename) return; // l'utilisateur a changé de nœud
      renderTopicView(node.filename, t.content);
    } catch (e) {
      panelBody().innerHTML = `<p>Contenu indisponible (${esc(e.message)})</p>`;
    }
  }

  function renderTopicView(filename, content) {
    const current = themeOf(filename);
    panelBody().innerHTML = `
      ${themeSelectorHtml(current ? current.name : null)}
      <div class="mg-actions">
        <button class="mg-btn primary" id="mg-edit">Éditer</button>
        <button class="mg-btn danger" id="mg-delete">Supprimer</button>
        <span class="mg-msg" id="mg-msg"></span>
      </div>
      <div class="mg-md">${renderMd(content)}</div>`;

    wireThemeSelector(async (theme) => {
      try {
        await apiSend(`/api/memory/topics/${encodeURIComponent(filename)}/theme`, 'PUT', { theme });
        await loadThemes();
        setMsg(theme ? `Rattaché au thème « ${theme} »` : 'Thème retiré');
        loadGraph();
      } catch (e) { setMsg(`Erreur : ${e.message}`, true); }
    });

    document.getElementById('mg-edit').addEventListener('click', () => renderTopicEdit(filename, content));
    document.getElementById('mg-delete').addEventListener('click', async () => {
      if (!window.confirm(`Supprimer définitivement ${filename} ?`)) return;
      try {
        await apiSend(`/api/memory/topics/${encodeURIComponent(filename)}`, 'DELETE');
        closePanel();
        loadGraph();
      } catch (e) { setMsg(`Erreur : ${e.message}`, true); }
    });
  }

  function renderTopicEdit(filename, content) {
    panelBody().innerHTML = `
      <div class="mg-editor">
        <div class="mg-actions">
          <button class="mg-btn primary" id="mg-save">Enregistrer</button>
          <button class="mg-btn" id="mg-cancel">Annuler</button>
          <span class="mg-msg" id="mg-msg"></span>
        </div>
        <textarea class="mg-edit-area" id="mg-textarea" spellcheck="false"></textarea>
      </div>`;
    const ta = document.getElementById('mg-textarea');
    ta.value = content;
    ta.focus();
    document.getElementById('mg-cancel').addEventListener('click', () => renderTopicView(filename, content));
    document.getElementById('mg-save').addEventListener('click', async () => {
      const btn = document.getElementById('mg-save');
      btn.disabled = true;
      setMsg('enregistrement…');
      try {
        await apiSend(`/api/memory/topics/${encodeURIComponent(filename)}`, 'PUT', { content: ta.value });
        renderTopicView(filename, ta.value);
        loadGraph();
      } catch (e) {
        btn.disabled = false;
        setMsg(`Erreur : ${e.message}`, true);
      }
    });
  }

  // ── Panneau CRÉATION (large) ───────────────────────────────────────────────

  async function openCreatePanel() {
    _panelTopic = null;
    openPanel('NOUVEAU FICHIER', 'Créer un fichier thématique', true);
    panelBody().innerHTML = '<p>chargement…</p>';
    await loadThemes();
    panelBody().innerHTML = `
      <div class="mg-theme-row">
        <span class="lbl">Nom</span>
        <input class="mg-input" id="mg-create-name" placeholder="nutrition.md" style="width:220px">
      </div>
      ${themeSelectorHtml(null)}
      <div class="mg-editor">
        <div class="mg-actions">
          <button class="mg-btn primary" id="mg-create-ok">Créer</button>
          <span class="mg-msg" id="mg-msg"></span>
        </div>
        <textarea class="mg-edit-area" id="mg-create-content" spellcheck="false" placeholder="# Titre&#10;&#10;Contenu Markdown…"></textarea>
      </div>`;

    let chosenTheme = null;
    wireThemeSelector((theme) => { chosenTheme = theme; });

    document.getElementById('mg-create-ok').addEventListener('click', async () => {
      let name = document.getElementById('mg-create-name').value.trim();
      if (!name) { setMsg('Nom requis', true); return; }
      if (!name.endsWith('.md')) name += '.md';
      const btn = document.getElementById('mg-create-ok');
      btn.disabled = true;
      setMsg('création…');
      try {
        await apiSend('/api/memory/topics', 'POST', {
          name,
          content: document.getElementById('mg-create-content').value,
          theme: chosenTheme,
        });
        await loadThemes();
        await loadGraph();
        const node = _graph.nodes.find((n) => n.type === 'topic' && n.filename === name);
        if (node) showTopicDetail(node); else closePanel();
      } catch (e) {
        btn.disabled = false;
        setMsg(`Erreur : ${e.message}`, true);
      }
    });
  }

  // ── Rendu D3 ───────────────────────────────────────────────────────────────

  function factRadius(n) { return 4 + (n.importance || 0.5) * 6; }
  function topicColor(n) { return n.color || TOPIC_COLOR; }

  function renderGraph() {
    const d3 = window.d3;
    const W = container.clientWidth || window.innerWidth;
    const H = container.clientHeight || window.innerHeight;

    if (simulation) { simulation.stop(); simulation = null; }
    container.querySelectorAll('.mg-svg, .mg-empty').forEach((el) => el.remove());

    if (!_graph.nodes.length) {
      const empty = document.createElement('div');
      empty.className = 'mg-empty';
      empty.textContent = 'MÉMOIRE VIDE — AUCUN FACT NI TOPIC POUR L\'INSTANT';
      container.appendChild(empty);
      renderLegend([]);
      return;
    }

    svg = d3.create('svg').attr('class', 'mg-svg').attr('viewBox', [0, 0, W, H]);
    gRoot = svg.append('g');

    svg.call(d3.zoom().scaleExtent([0.25, 4]).on('zoom', (ev) => gRoot.attr('transform', ev.transform)));

    const links = _graph.links.map((l) => ({ ...l }));
    const nodes = _graph.nodes.map((n) => ({ ...n }));

    const link = gRoot.append('g').selectAll('path')
      .data(links).join('path')
      .attr('class', (l) => {
        if (l.type === 'supersedes') return 'mg-link supersedes';
        if (l.type === 'topic') return 'mg-link topic';
        return 'mg-link flow';
      });

    const node = gRoot.append('g').selectAll('g')
      .data(nodes).join('g')
      .attr('class', (n) => 'mg-node' + (n.status === 'superseded' ? ' superseded' : ''))
      .call(d3.drag()
        .on('start', (ev, d) => { if (!ev.active) simulation.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on('end', (ev, d) => { if (!ev.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

    // halo pulsant + cœur — facts en cercles, topics en hexagones (couleur = thème)
    node.each(function (n) {
      const g = d3.select(this);
      if (n.type === 'topic') {
        const c = topicColor(n);
        g.append('path').attr('class', 'halo').attr('d', hexagonPath(20)).attr('fill', c);
        g.append('path').attr('class', 'core').attr('d', hexagonPath(12))
          .attr('fill', 'rgba(220,232,255,.14)').attr('stroke', c).attr('stroke-width', 1.4);
        g.append('text').attr('dy', 26).attr('text-anchor', 'middle').text(n.filename);
      } else {
        const r = factRadius(n), c = catColor(n.category || '?');
        g.append('circle').attr('class', 'halo').attr('r', r * 2.1).attr('fill', c);
        g.append('circle').attr('class', 'core').attr('r', r)
          .attr('fill', c).attr('fill-opacity', 0.85).attr('stroke', 'rgba(6,8,13,.9)').attr('stroke-width', 1);
      }
    });

    // désynchronise les pulsations (aspect organique, coût nul)
    node.selectAll('.halo').style('animation-delay', () => `${(Math.random() * 3.2).toFixed(2)}s`);

    node.append('title').text((n) => n.type === 'topic' ? n.filename : n.label);
    node.on('click', (ev, n) => { ev.stopPropagation(); n.type === 'topic' ? showTopicDetail(n) : showFactDetail(n); });
    svg.on('click', closePanel);

    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((n) => n.id).distance((l) => l.type === 'topic' ? 70 : 46).strength(0.5))
      .force('charge', d3.forceManyBody().strength(-140))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide().radius((n) => (n.type === 'topic' ? 26 : factRadius(n) + 6)))
      .on('tick', () => {
        link.attr('d', (l) => `M${l.source.x},${l.source.y}L${l.target.x},${l.target.y}`);
        node.attr('transform', (n) => `translate(${n.x},${n.y})`);
      });
    // la simulation s'arrête seule (alphaMin) — pas de boucle permanente

    container.appendChild(svg.node());
    renderLegend(nodes);
    applyHighlight();
  }

  function renderLegend(nodes) {
    const el = document.getElementById('mg-legend');
    if (!el) return;
    const cats = [...new Set(nodes.filter((n) => n.type === 'fact').map((n) => n.category || '?'))].sort();
    const catHtml = cats.map((c) =>
      `<span class="mg-leg"><span class="dot" style="background:${catColor(c)}"></span>${esc(c)}</span>`).join('');
    const themeHtml = _themes.map((t) =>
      `<span class="mg-leg"><span class="hex" style="background:${esc(t.color)}"></span>${esc(t.name)}</span>`).join('');
    el.innerHTML = catHtml + themeHtml +
      `<span class="mg-leg"><span class="hex" style="background:${TOPIC_COLOR}"></span>fichier sans thème</span>` +
      `<span class="mg-leg"><span class="sup"></span>contradiction résolue</span>`;
  }

  // ── Highlight (view_command highlight_nodes) ───────────────────────────────

  function applyHighlight() {
    if (!svg) return;
    const nodeSel = gRoot.selectAll('.mg-node');
    const linkSel = gRoot.selectAll('.mg-link');
    if (!_highlight) {
      nodeSel.classed('dim', false).classed('hl', false);
      linkSel.classed('dim', false).classed('hl', false);
      return;
    }
    const isHl = (n) => n.type === 'topic'
      ? _highlight.topicFiles.has(n.filename)
      : _highlight.factIds.has(n.id);
    nodeSel.classed('hl', isHl).classed('dim', (n) => !isHl(n));
    linkSel
      .classed('hl', (l) => isHl(l.source) && isHl(l.target))
      .classed('dim', (l) => !(isHl(l.source) || isHl(l.target)));
  }

  function setHighlight(params) {
    _highlight = {
      factIds: new Set(params.fact_ids || []),
      topicFiles: new Set(params.topic_filenames || []),
    };
    applyHighlight();
  }

  // ── Chargement des données ─────────────────────────────────────────────────

  async function loadThemes() {
    try {
      const data = await apiFetch('/api/memory/themes');
      _themes = data.themes || [];
    } catch (_) { _themes = []; }
  }

  async function loadGraph() {
    try {
      const data = await apiFetch('/api/memory/graph');
      _graph = data;
      _themes = data.themes || _themes;
      const m = data.meta || {};
      const stats = document.getElementById('mg-stats');
      if (stats) stats.textContent = `${m.fact_count ?? 0} facts · ${m.relation_count ?? 0} relations · ${m.topic_count ?? 0} topics`;
      renderGraph();
    } catch (e) {
      const stats = document.getElementById('mg-stats');
      if (stats) stats.textContent = `erreur de chargement (${e.message})`;
    }
  }

  // ── Enregistrement ─────────────────────────────────────────────────────────

  Jarvis.views.register(VIEW_ID, {
    meta: {
      name: 'Cerveau Mémoire',
      desc: 'Graphe interactif des connaissances — facts, relations, fichiers thématiques éditables',
      glyph: 'MEM',
      tags: ['mémoire', 'graphe', 'connaissances', 'cerveau'],
    },

    show() {
      ensureContainer();
      if (_visible) return;
      _visible = true;
      if (!_domBuilt) buildDOM();
      container.style.display = 'block';
      container.getBoundingClientRect();
      container.style.opacity = '1';
      loadD3().then(loadGraph).catch(() => {
        const stats = document.getElementById('mg-stats');
        if (stats) stats.textContent = 'D3.js indisponible (hors-ligne ?)';
      });
    },

    hide() {
      if (!container) return;
      _visible = false;
      closePanel();
      if (simulation) { simulation.stop(); simulation = null; }
      container.style.opacity = '0';
      setTimeout(() => { if (!_visible && container) container.style.display = 'none'; }, 360);
    },

    command(cmd, params = {}) {
      switch (cmd) {
        case 'show': this.show(); break;
        case 'hide': this.hide(); break;
        case 'refresh': if (_visible && window.d3) loadGraph(); break;
        case 'highlight_nodes':
          // mémorisé même si la vue est cachée — appliqué au prochain rendu
          setHighlight(params || {});
          break;
        case 'clear_highlight': _highlight = null; applyHighlight(); break;
        // commandes inconnues ignorées silencieusement
      }
    },
  });
})();
