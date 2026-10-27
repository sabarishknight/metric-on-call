/* Metric On-Call portal: state, network and DOM wiring. All HTML comes from views.js. */
(() => {
  'use strict';
  const { esc, humanize, fmtMetric, fmtDate } = Lib;
  const V = Views;
  const $ = (s) => document.querySelector(s);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };
  const DONE = ['awaiting_approval', 'published', 'rejected', 'error'];

  const S = {
    meta: { providers: [], ready: false }, incidents: [], agents: [], agentById: {}, dash: null, hist: [], pub: null, sys: null, rep: null, repId: null,
    view: 'dashboard', pickId: null, provider: 'scripted', pace: 0.7, metric: 'net_sales', range: 180,
    run: null, events: [], nEv: 0, nodes: {}, truth: null, showTruth: false, drawer: null, tasks: store.get('moc.tasks', []), chart: null,
  };
  let pollTimer = null, toastTimer = null;

  async function api(path, opts) {
    const r = await fetch(path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) throw Object.assign(new Error((data && data.error) || r.statusText), { status: r.status });
    return data;
  }
  const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  function toast(msg, err) {
    const t = $('#toast');
    t.textContent = msg; t.className = 'toast on' + (err ? ' err' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = 'toast'; }, err ? 6000 : 3200);
  }
  const setHTML = (sel, html) => { const el = $(sel); if (el) el.innerHTML = html; };

  /* ---------- theme ---------- */
  function applyTheme() {
    const t = store.get('moc.theme', 'auto');
    document.documentElement.dataset.theme = t;
    document.querySelectorAll('.theme button').forEach((b) => b.classList.toggle('on', b.dataset.t === t));
  }

  /* ---------- shell ---------- */
  function renderSide() {
    setHTML('#nav', Object.keys(V.PAGES).map((k) => `<a href="#/${k}" class="${S.view === k ? 'on' : ''}">${V.icon(k)}${V.PAGES[k][0]}</a>`).join(''));
    setHTML('#status', V.sideStatusHTML(S));
  }
  const renderTop = () => setHTML('#top', V.topHTML(S, S.view));

  function renderDrawer() {
    const dr = $('#drawer'), sc = $('#scrim');
    if (!S.drawer) { dr.classList.remove('on'); sc.classList.remove('on'); return; }
    dr.innerHTML = V.drawerHTML(S);
    dr.classList.add('on'); sc.classList.add('on');
  }

  /* ---------- routing ---------- */
  function route() {
    const k = (location.hash.replace(/^#\//, '') || 'dashboard').split('?')[0];
    S.view = V.PAGES[k] ? k : 'dashboard';
    renderSide(); renderTop(); show();
  }
  async function show() {
    const v = $('#view'), mine = S.view;
    v.style.animation = 'none'; void v.offsetHeight; v.style.animation = '';
    if (!S.meta.ready) { v.innerHTML = V.loadingHTML(S.meta.error ? 'Could not load the data: ' + S.meta.error : 'Loading the warehouse. BigQuery mode can take half a minute…'); return; }
    try {
      if (mine === 'dashboard') {
        if (!S.dash) { v.innerHTML = V.loadingHTML('Loading dashboard…'); S.dash = await api('/api/dashboard'); }
        try { S.hist = await api('/api/runs'); } catch (e) { /* optional */ }
        if (S.view !== mine) return;
        v.innerHTML = V.dashboardHTML(S); drawChart();
      } else if (mine === 'investigate') v.innerHTML = V.investigateHTML(S);
      else if (mine === 'agents') v.innerHTML = V.agentsHTML(S);
      else if (mine === 'tasks') { try { S.pub = await api('/api/published'); } catch (e) { /* optional */ } if (S.view !== mine) return; v.innerHTML = V.tasksHTML(S); }
      else if (mine === 'reports') { [S.hist, S.pub] = await Promise.all([api('/api/runs'), api('/api/published')]); if (S.view !== mine) return; v.innerHTML = V.reportsHTML(S); }
      else if (mine === 'system') { S.sys = await api('/api/system'); if (S.view !== mine) return; v.innerHTML = V.systemHTML(S); }
    } catch (e) { if (S.view === mine) v.innerHTML = V.emptyHTML('Something went wrong', e.message); }
  }

  /* ---------- dashboard chart ---------- */
  function drawChart() {
    const el = $('#chart');
    if (!el || !S.dash) return;
    const W = Math.max(520, Math.floor(el.clientWidth));
    const { html, model } = V.chartSVG(S, W);
    el.innerHTML = html; S.chart = model;
    if (!model) return;
    const o = $('#xo'), tip = $('#tip'), xh = $('#xh'), xd = $('#xd');
    o.addEventListener('mousemove', (e) => {
      const r = el.getBoundingClientRect(), scale = r.width / model.W, x = (e.clientX - r.left) / scale;
      let i = Math.round(((x - model.m.l) / (model.W - model.m.l - model.m.r)) * (model.n - 1));
      i = Math.max(0, Math.min(model.n - 1, i));
      const val = model.vals[i];
      if (val == null) return;
      const px = model.sx(i), py = model.sy(val), inc = model.bandAt[i];
      xh.setAttribute('x1', px); xh.setAttribute('x2', px); xh.setAttribute('opacity', 1);
      xd.setAttribute('cx', px); xd.setAttribute('cy', py); xd.setAttribute('opacity', 1);
      tip.innerHTML = `<b>${esc(fmtMetric(model.key, val))}</b><span class="faint">${esc(fmtDate(model.dates[i]))}</span>${inc ? `<div class="inc">${esc(inc.name)}</div>` : ''}`;
      let left = px * scale + 14;
      if (left > r.width - 200) left = px * scale - 190;
      tip.style.left = Math.max(0, left) + 'px'; tip.style.top = Math.max(0, py * scale - 64) + 'px'; tip.style.opacity = 1;
    });
    o.addEventListener('mouseleave', () => { tip.style.opacity = 0; xh.setAttribute('opacity', 0); xd.setAttribute('opacity', 0); });
  }

  /* ---------- running an investigation ---------- */
  function refreshRun(full) {
    if (S.view === 'investigate') {
      if (full) { const v = $('#view'), sc = v.scrollTop; v.innerHTML = V.investigateHTML(S); v.scrollTop = sc; }
      else {
        setHTML('#pipe', V.pipelineHTML(S)); setHTML('#runstate', V.runStateHTML(S)); setHTML('#log', V.logHTML(S));
        const lg = $('#log'); if (lg) lg.scrollTop = lg.scrollHeight;
      }
    } else if (S.view === 'tasks') setHTML('#view', V.tasksHTML(S));
    else if (S.view === 'agents') setHTML('#view', V.agentsHTML(S));
    if (S.drawer) renderDrawer();
  }
  function takeSnapshot(snap, replaceEvents) {
    if (replaceEvents) S.events = snap.events || []; else S.events = S.events.concat(snap.events || []);
    S.nEv = snap.n_events;
    S.run = Object.assign({}, snap); delete S.run.events;
    S.nodes = V.deriveNodes(S.events, S.run);
  }
  async function startRun() {
    try {
      const { run_id } = await post('/api/runs', { incident_id: S.pickId, provider: S.provider, pace: S.pace });
      S.events = []; S.nEv = 0; S.nodes = {}; S.truth = null; S.showTruth = false;
      S.run = { id: run_id, status: 'running', incident: S.incidents.find((i) => i.id === S.pickId) };
      renderSide(); refreshRun(true); poll(run_id);
    } catch (e) { toast(e.message, true); }
  }
  function poll(id) {
    clearInterval(pollTimer);
    let busy = false;
    pollTimer = setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        takeSnapshot(await api(`/api/runs/${id}?since=${S.nEv}`), false);
        renderSide();
        if (DONE.includes(S.run.status)) { clearInterval(pollTimer); await onRunFinished(); } else refreshRun(false);
      } catch (e) { clearInterval(pollTimer); toast('Lost contact with the portal: ' + e.message, true); }
      busy = false;
    }, 450);
  }
  async function onRunFinished() {
    await syncTasks();
    refreshRun(true);
    try { S.hist = await api('/api/runs'); } catch (e) { /* optional */ }
    if (S.run.status === 'error') toast('The run hit an error: ' + (S.run.error || ''), true);
    else { toast('Investigation complete. Review the brief below.'); const r = $('#result'); if (r) r.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }
  async function decide(action) {
    let comment = '';
    if (action === 'reject') { comment = prompt('Why are you rejecting this brief? (optional)'); if (comment === null) return; }
    try {
      takeSnapshot(await post(`/api/runs/${S.run.id}/decision`, { action, comment }), true);
      S.dash = null; S.pub = null;
      await syncTasks(); renderSide(); refreshRun(true);
      toast(action === 'approve' ? 'Published. The dashboard now carries this annotation.' : 'Rejected. Nothing was published.');
    } catch (e) { toast(e.message, true); }
  }
  async function toggleTruth() {
    S.showTruth = !S.showTruth;
    if (S.showTruth && !S.truth) { try { S.truth = await api(`/api/incidents/${S.run.incident.id}/truth`); } catch (e) { toast(e.message, true); } }
    setHTML('#result', V.resultHTML(S));
  }

  /* ---------- follow-up tasks ---------- */
  function addTask(id, text, source, done) {
    if (!text || S.tasks.some((t) => t.id === id)) return false;
    S.tasks.unshift({ id, text, source, done: !!done });
    return true;
  }
  async function syncTasks() {
    let changed = false;
    const r = S.run;
    if (r && r.result && r.result.findings) {
      const f = r.result.findings, closed = r.status === 'published' || r.status === 'rejected';
      changed = addTask(`review:${r.id}`, `Decide on the brief: ${(r.incident || {}).name}`, 'Review', closed) || changed;
      changed = addTask(`action:${r.id}`, f.recommended_action, `Recommended action · ${humanize(f.cause_category)}`, false) || changed;
      const rv = S.tasks.find((t) => t.id === `review:${r.id}`);
      if (rv && closed && !rv.done) { rv.done = true; changed = true; }
    }
    try { const p = await api('/api/published'); S.pub = p; (p.tickets || []).forEach((t) => { changed = addTask(`ticket:${t.title}`, t.title, 'Data-engineering ticket', false) || changed; }); } catch (e) { /* optional */ }
    if (changed) store.set('moc.tasks', S.tasks);
  }
  function addTaskFromInput() {
    const inp = $('#newtask');
    if (!inp || !inp.value.trim()) return;
    S.tasks.unshift({ id: 'u' + Date.now(), text: inp.value.trim(), source: 'Added by you', done: false });
    store.set('moc.tasks', S.tasks); setHTML('#view', V.tasksHTML(S));
  }

  /* ---------- events ---------- */
  const H = {
    agent: (t) => { S.drawer = t.dataset.id; renderDrawer(); },
    close: () => { S.drawer = null; renderDrawer(); },
    theme: (t) => { store.set('moc.theme', t.dataset.t); applyTheme(); },
    pick: (t) => { S.pickId = +t.dataset.id; refreshRun(true); },
    prov: (t) => { S.provider = t.dataset.id; refreshRun(true); },
    pace: (t) => { S.pace = +t.dataset.v; refreshRun(true); },
    run: startRun, approve: () => decide('approve'), reject: () => decide('reject'), truth: toggleTruth,
    metric: (t) => { S.metric = t.dataset.k; setHTML('#view', V.dashboardHTML(S)); drawChart(); },
    range: (t) => { S.range = +t.dataset.k; setHTML('#view', V.dashboardHTML(S)); drawChart(); },
    rep: async (t) => {
      S.repId = t.dataset.id;
      try { S.rep = await api('/api/runs/' + encodeURIComponent(S.repId)); } catch (e) { toast(e.message, true); return; }
      if (S.view === 'reports') setHTML('#view', V.reportsHTML(S));
    },
    'task-add': addTaskFromInput,
    'task-del': (t) => { S.tasks = S.tasks.filter((x) => x.id !== t.dataset.id); store.set('moc.tasks', S.tasks); setHTML('#view', V.tasksHTML(S)); },
  };
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-act]');
    if (t && H[t.dataset.act] && !t.disabled) { if (t.tagName === 'LABEL') return; H[t.dataset.act](t, e); }
  });
  document.addEventListener('change', (e) => {
    const id = e.target && e.target.dataset && e.target.dataset.task;
    if (!id) return;
    const t = S.tasks.find((x) => x.id === id);
    if (t) { t.done = e.target.checked; store.set('moc.tasks', S.tasks); setHTML('#view', V.tasksHTML(S)); }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && S.drawer) H.close();
    if (e.key === 'Enter' && e.target && e.target.id === 'newtask') addTaskFromInput();
  });
  let rt = null;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if (S.view === 'dashboard' && S.dash) drawChart(); }, 160); });
  window.addEventListener('error', (e) => toast('Something broke in the page: ' + e.message, true));

  /* ---------- boot ---------- */
  async function boot() {
    applyTheme(); renderSide();
    window.addEventListener('hashchange', route);
    try {
      S.meta = await api('/api/meta');
      const [inc, ag] = await Promise.all([api('/api/incidents'), api('/api/agents')]);
      S.incidents = inc; S.agents = ag; ag.forEach((a) => { S.agentById[a.id] = a; });
      S.pickId = (inc[0] || {}).id; S.provider = S.meta.default_provider || 'scripted';
    } catch (e) { setHTML('#view', V.emptyHTML('Cannot reach the portal server', e.message)); return; }
    route();
    while (!S.meta.ready && !S.meta.error) { await sleep(1200); try { S.meta = await api('/api/meta'); } catch (e) { /* retry */ } renderTop(); }
    renderTop(); if (S.meta.ready) show();
  }
  boot();
})();
