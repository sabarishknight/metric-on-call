/* HTML builders for every screen. Pure functions of the state object, so they can be tested in node. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./lib.js'));
  else root.Views = factory(root.Lib);
})(typeof self !== 'undefined' ? self : this, function (L) {
  const { esc, fmtMoney, fmtPct, fmtMetric, fmtDate, fmtNum, humanize, secs, niceTicks, scaleLinear, monotonePath, sparkPath, mdToHtml, keyFacts } = L;

  /* ---------- icons ---------- */
  const IC = {
    dashboard: '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>',
    investigate: '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z"/>',
    agents: '<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
    tasks: '<path d="M10 6h10M10 12h10M10 18h10"/><path d="m3.5 6 1.4 1.4L7 5M3.5 12l1.4 1.4L7 11M3.5 18l1.4 1.4L7 17"/>',
    reports: '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5M9.5 13h6M9.5 17h6"/>',
    system: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    monitor: '<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>',
    supervisor: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4M12 11.5l-7 5.5M12 11.5l7 5.5"/>',
    data_quality: '<path d="M12 3 4.5 6v6c0 4.5 3.2 7.8 7.5 9 4.3-1.2 7.5-4.5 7.5-9V6z"/><path d="m8.8 12 2.4 2.4 4.2-4.6"/>',
    decomposer: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
    context: '<rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    impact: '<circle cx="12" cy="12" r="9"/><path d="M14.8 9.2c-.5-1-1.5-1.5-2.8-1.5-1.6 0-2.8.8-2.8 2 0 3 5.8 1.4 5.8 4.4 0 1.2-1.3 2-3 2-1.4 0-2.5-.6-3-1.6M12 6v1.7M12 16.3V18"/>',
    hypothesis: '<path d="M9 3h6M10 3v6l-5.5 9.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7.5 15h9"/>',
    skeptic: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.9-4.9M10.5 7.5v3.5M10.5 13.5h.01"/>',
    reporter: '<path d="M5 4h10l4 4v12H5z"/><path d="M8.5 12h7M8.5 16h5"/>',
    approval: '<circle cx="10" cy="8" r="3.5"/><path d="M3.5 20c.5-3.5 3-5.5 6.5-5.5 1.5 0 2.8.4 3.8 1M15.5 18l2 2 3.5-4"/>',
    publish: '<path d="m21 3-9.5 18-2.5-7.5L1.5 11z"/><path d="m21 3-11.7 10.5"/>',
  };
  const icon = (id) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${IC[id] || ''}</svg>`;

  const ORDER = ['monitor', 'supervisor', 'data_quality', 'decomposer', 'context', 'impact', 'hypothesis', 'skeptic', 'reporter', 'approval', 'publish'];
  const SKIPPABLE = ['decomposer', 'context', 'impact', 'hypothesis', 'skeptic'];
  const PAGES = {
    dashboard: ['Dashboard', 'How the business is trading, and what the agents have explained'],
    investigate: ['Investigate', 'Pick an incident, press Run, and watch the agent team work'],
    agents: ['Agents', 'Who does what, which tools each may use, and what it last produced'],
    tasks: ['Tasks', 'Live progress of the current run, and follow-ups from the briefs'],
    reports: ['Reports', 'Every investigation, its brief and what was published'],
    system: ['System', 'Data source, guardrails and cost controls'],
  };
  const DIFF = { easy: 'green', medium: 'orange', hard: 'red' };
  const CAT_COLOR = { pos_outage: 'red', partner_outage: 'red', pipeline_duplicate_load: 'orange', pipeline_missing_data: 'orange', weather: 'cyan', calendar_shift: 'yellow',
    promo_cannibalisation: 'purple', promo_uplift: 'purple', price_change: 'pink', stockout: 'pink', planned_closure: 'teal', external_unknown: 'indigo', mix_shift: 'blue', multi_cause: 'pink' };
  const catColor = (c) => CAT_COLOR[c] || 'blue';
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthYear = (iso) => { const [y, m] = String(iso).split('-'); return `${MONTHS[+m - 1]} '${String(y).slice(2)}`; };
  const loadingHTML = (msg) => `<div class="loading"><div class="spinner"></div><div>${esc(msg)}</div></div>`;
  const emptyHTML = (title, msg, extra = '') => `<div class="empty"><b>${esc(title)}</b>${esc(msg)}${extra}</div>`;
  const statusChip = (s) => {
    const m = { awaiting_approval: ['Needs your decision', 'green'], published: ['Published', 'green'], rejected: ['Rejected', 'orange'], error: ['Error', 'red'], running: ['Running', 'blue'], queued: ['Queued', 'blue'] };
    const [t, c] = m[s] || [humanize(s), 'indigo'];
    return `<span class="chip" style="--c:var(--${c})">${esc(t)}</span>`;
  };

  /* ---------- run state -> per-agent node state ---------- */
  function deriveNodes(events, run) {
    const nodes = {};
    const get = (id) => (nodes[id] = nodes[id] || { status: 'idle', ms: 0, tools: 0, rounds: 0, output: null, calls: [], error: null, brief: null });
    for (const ev of events || []) {
      if (!ev.agent || ev.agent === 'system') continue;
      const n = get(ev.agent);
      if (ev.type === 'start') n.status = 'running';
      else if (ev.type === 'done') {
        n.status = ev.error ? 'error' : 'done'; n.ms += ev.ms || 0; n.rounds += 1; n.output = ev.output || n.output; n.error = ev.error || null;
        n.calls = n.calls.concat(ev.tool_calls || []); n.tools = n.calls.length; if (ev.brief) n.brief = ev.brief;
      } else if (ev.type === 'approval') { n.status = 'wait'; n.output = ev.output || null; }
      else if (ev.type === 'publish') { n.status = 'done'; n.output = ev.output || null; }
      else if (ev.type === 'error') n.status = 'error';
    }
    const st = run && run.status;
    if (st === 'published' || st === 'rejected') get('approval').status = 'done';
    if (st && ['awaiting_approval', 'published', 'rejected'].includes(st)) SKIPPABLE.forEach((id) => { if (get(id).status === 'idle') nodes[id].status = 'skipped'; });
    return nodes;
  }

  function nodeText(id, n) {
    switch (n.status) {
      case 'running': return 'Working…';
      case 'wait': return 'Needs your decision';
      case 'skipped': return 'Not needed here';
      case 'error': return 'Error';
      case 'done': {
        if (id === 'monitor') return 'Incident opened';
        if (id === 'approval') return 'Decided';
        if (id === 'publish') return 'Published';
        const r = n.rounds > 1 ? `round ${n.rounds} · ` : '';
        return n.tools ? `${r}${n.tools} tool call${n.tools === 1 ? '' : 's'} · ${secs(n.ms)}` : `${r}${secs(n.ms)}`;
      }
      default: return 'Waiting';
    }
  }

  /* ---------- sidebar / top ---------- */
  function sideStatusHTML(S) {
    const r = S.run;
    if (!r) return '<b><span class="dot ok"></span>Ready</b>Manual runs only. Nothing is scheduled.';
    const live = r.status === 'running' || r.status === 'queued';
    return `<b><span class="dot ${live ? 'live' : r.status === 'error' ? 'bad' : 'ok'}"></span>${live ? 'Investigating…' : humanize(r.status)}</b>${esc((r.incident || {}).name || '')}`;
  }
  function topHTML(S, view) {
    const [title, sub] = PAGES[view] || ['', ''];
    const m = S.meta || {};
    const cloud = m.warehouse === 'bigquery';
    const w = m.data_window;
    return `<div><h1>${esc(title)}</h1><div class="sub">${esc(sub)}</div></div><div class="spacer"></div>
      <span class="pill"><span class="dot ${m.ready ? 'ok' : m.error ? 'bad' : 'warn'}"></span>${cloud ? 'BigQuery · ' + esc(m.project || '') : 'Local DuckDB'}${w ? ' · ' + monthYear(w.from) + ' to ' + monthYear(w.to) : ''}</span>
      <span class="pill" title="No scheduler is configured">Manual runs only</span>`;
  }

  /* ---------- dashboard ---------- */
  function dashboardHTML(S) {
    const d = S.dash;
    if (!d) return loadingHTML('Loading dashboard…');
    const colors = { net_sales: 'blue', transactions: 'purple', avg_basket: 'orange', conversion: 'green' };
    const kpis = d.kpis.map((k) => {
      const c = colors[k.key] || 'blue', sp = sparkPath(k.spark || [], 200, 46), up = (k.pct || 0) >= 0;
      return `<div class="glass kpi" style="--c:var(--${c})"><div class="lab"><i></i>${esc(k.label)}</div><div class="val">${fmtMetric(k.key, k.value)}</div>
        <div class="delta ${up ? 'up' : 'down'}">${k.pct == null ? '–' : (up ? '▲ ' : '▼ ') + fmtPct(Math.abs(k.pct), 1, false)} <span class="faint" style="font-weight:500">vs previous 28 days</span></div>
        <svg class="spark" viewBox="0 0 200 46" preserveAspectRatio="none"><defs><linearGradient id="sg-${k.key}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--${c})" stop-opacity=".35"/><stop offset="1" stop-color="var(--${c})" stop-opacity="0"/></linearGradient></defs>
        <path d="${sp.area}" fill="url(#sg-${k.key})"/><path d="${sp.line}" fill="none" stroke="var(--${c})" stroke-width="2" vector-effect="non-scaling-stroke"/></svg></div>`;
    }).join('');
    const metrics = [['net_sales', 'Net sales'], ['transactions', 'Transactions'], ['visits', 'Visits'], ['avg_basket', 'Avg basket'], ['conversion', 'Conversion']];
    const ranges = [[90, '90 days'], [180, '6 months'], [365, '1 year'], [0, 'All']];
    const maxReg = Math.max(1, ...d.regions.map((r) => r.net_sales));
    const regions = d.regions.map((r, i) => {
      const up = (r.pct || 0) >= 0, c1 = ['blue', 'purple', 'pink', 'orange', 'teal', 'green'][i % 6], c2 = ['indigo', 'pink', 'orange', 'yellow', 'cyan', 'mint'][i % 6];
      return `<div class="bar" style="--c1:var(--${c1});--c2:var(--${c2})"><div class="t"><span>${esc(humanize(r.region))}</span><span><b>${fmtMoney(r.net_sales, true)}</b> <span class="${up ? 'up' : 'down'}" style="font-size:12px">${r.pct == null ? '' : fmtPct(r.pct)}</span></span></div><div class="track"><div class="fill" style="width:${(100 * r.net_sales / maxReg).toFixed(1)}%"></div></div></div>`;
    }).join('');
    const runs = (S.hist || []).slice(0, 5).map((r) => `<a class="row" href="#/reports" data-act="rep" data-id="${esc(r.id)}" style="text-decoration:none;color:inherit"><span class="num" style="--c:var(--${catColor(r.cause)})">${esc((r.incident || {}).id)}</span><span class="body"><b>${esc((r.incident || {}).name || '')}</b><small>${esc(humanize(r.cause || r.status))} · ${esc(r.impact != null ? fmtMoney(r.impact) : 'no $ impact')}</small></span>${statusChip(r.status)}</a>`).join('');
    const legend = [['red', 'Outage'], ['orange', 'Data fault'], ['cyan', 'Weather'], ['purple', 'Promo'], ['pink', 'Price / stock'], ['indigo', 'Unknown cause']].map(([c, t]) => `<span><i style="background:var(--${c});opacity:.7"></i>${t}</span>`).join('');
    return `<div class="grid">
      <div class="glass card" style="background:linear-gradient(135deg,color-mix(in srgb,var(--blue) 22%,var(--glass)),color-mix(in srgb,var(--purple) 20%,var(--glass)))"><div class="cardhead" style="margin:0"><div><h2 style="font-size:19px;letter-spacing:-.02em">Why did the number move?</h2><div class="sub" style="margin-top:3px">${d.incidents.length} incidents are planted in this data. Pick one and let the agent team find out which, how much it cost and what to do.</div></div><div class="spacer"></div><a class="btn primary" href="#/investigate" style="text-decoration:none">Run an investigation</a></div></div>
      <div class="grid g4">${kpis}</div>
      <div class="glass card"><div class="cardhead"><h2>Trading, day by day</h2><div class="spacer"></div><div class="seg">${metrics.map(([k, t]) => `<button data-act="metric" data-k="${k}" class="${S.metric === k ? 'on' : ''}">${t}</button>`).join('')}</div><div class="seg">${ranges.map(([k, t]) => `<button data-act="range" data-k="${k}" class="${S.range === k ? 'on' : ''}">${t}</button>`).join('')}</div></div>
        <div class="chartwrap" id="chart"></div><div class="legend"><span><i style="background:var(--blue)"></i>Actual, as loaded</span>${legend}<span class="faint">Shaded bands are planted incidents. Hover for detail.</span></div></div>
      <div class="grid g2"><div class="glass card"><div class="cardhead"><h2>Regions</h2><span class="sub">net sales, last 28 days, change vs previous 28</span></div><div class="bars">${regions}</div></div>
      <div class="glass card"><div class="cardhead"><h2>Recent investigations</h2></div>${runs ? `<div class="list">${runs}</div>` : emptyHTML('Nothing run yet', 'Open Investigate and press Run.')}</div></div></div>`;
  }

  // Returns {html, model}. The model lets the DOM layer answer hover questions without re-parsing the SVG.
  function chartSVG(S, W) {
    const d = S.dash, key = S.metric, n = d.dates.length, start = S.range ? Math.max(0, n - S.range) : 0;
    const dates = d.dates.slice(start), vals = d.series[key].slice(start);
    const H = 330, m = { l: 62, r: 16, t: 14, b: 32 };
    const fin = vals.filter((v) => v != null);
    if (!fin.length) return { html: emptyHTML('No data', ''), model: null };
    const lo = Math.min(...fin), hi = Math.max(...fin), pad = (hi - lo) * 0.14 || 1;
    const ticks = niceTicks(lo - pad, hi + pad, 5), y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const sx = scaleLinear(0, Math.max(1, dates.length - 1), m.l, W - m.r), sy = scaleLinear(y0, y1, H - m.b, m.t);
    const pts = [];
    vals.forEach((v, i) => { if (v != null) pts.push([+sx(i).toFixed(1), +sy(v).toFixed(1)]); });
    const line = monotonePath(pts), base = H - m.b;
    const area = pts.length ? `${line}L${pts[pts.length - 1][0]},${base}L${pts[0][0]},${base}Z` : '';
    const step = (sx(1) - sx(0)) / 2 || 2;
    const bandAt = new Array(dates.length).fill(null);
    let bands = '', marks = '';
    (d.incidents || []).forEach((inc) => {
      let a = -1, b = -1;
      dates.forEach((x, i) => { if (x >= inc.from && x <= inc.to) { if (a < 0) a = i; b = i; } });
      if (a < 0) return;
      for (let i = a; i <= b; i++) bandAt[i] = inc;
      const c = catColor(inc.category), x = sx(a) - step, w = Math.max(5, sx(b) - sx(a) + 2 * step);
      bands += `<rect x="${x.toFixed(1)}" y="${m.t}" width="${w.toFixed(1)}" height="${base - m.t}" rx="5" fill="var(--${c})" opacity=".2"/><rect x="${x.toFixed(1)}" y="${m.t}" width="${w.toFixed(1)}" height="3" rx="1.5" fill="var(--${c})"/>`;
    });
    (d.annotations || []).forEach((an) => {
      const i = dates.findIndex((x) => x >= an.from);
      if (i >= 0 && an.from <= dates[dates.length - 1]) marks += `<g><circle cx="${sx(i).toFixed(1)}" cy="${m.t + 10}" r="5.5" fill="var(--green)" stroke="var(--bg)" stroke-width="2"/><title>${esc(humanize(an.label))}: published</title></g>`;
    });
    const grid = ticks.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${sy(t).toFixed(1)}" y2="${sy(t).toFixed(1)}" stroke="var(--grid)"/><text class="axis" x="${m.l - 10}" y="${(sy(t) + 4).toFixed(1)}" text-anchor="end">${esc(fmtMetric(key, t))}</text>`).join('');
    const nx = Math.min(6, dates.length), xl = [];
    for (let k = 0; k < nx; k++) { const i = Math.round((k * (dates.length - 1)) / Math.max(1, nx - 1)); xl.push(`<text class="axis" x="${sx(i).toFixed(1)}" y="${H - 9}" text-anchor="${k === 0 ? 'start' : k === nx - 1 ? 'end' : 'middle'}">${monthYear(dates[i])}</text>`); }
    const html = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Daily ${esc(key)}"><defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--blue)" stop-opacity=".42"/><stop offset="1" stop-color="var(--blue)" stop-opacity="0"/></linearGradient></defs>
      ${grid}${bands}<path d="${area}" fill="url(#ag)"/><path d="${line}" fill="none" stroke="var(--blue)" stroke-width="2.4" stroke-linejoin="round"/>${marks}${xl.join('')}
      <line id="xh" x1="0" x2="0" y1="${m.t}" y2="${base}" stroke="var(--text3)" stroke-dasharray="3 4" opacity="0"/><circle id="xd" r="5.5" fill="var(--blue)" stroke="#fff" stroke-width="2.5" opacity="0"/>
      <rect id="xo" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${base - m.t}" fill="transparent"/></svg><div class="tip" id="tip"></div>`;
    return { html, model: { n: dates.length, m, W, H, dates, vals, bandAt, sx, sy, key } };
  }

  /* ---------- investigate ---------- */
  function nodeHTML(S, id) {
    const a = S.agentById[id] || { label: id, color: 'blue' }, n = (S.nodes || {})[id] || { status: 'idle' };
    const cls = n.status === 'wait' ? 'is-wait' : 'is-' + n.status;
    return `<button class="node ${cls}" style="--c:var(--${a.color})" data-act="agent" data-id="${id}"><span class="ic">${icon(id)}</span><span class="nm">${esc(a.label)}</span><span class="st">${esc(nodeText(id, n))}</span></button>`;
  }
  function pipelineHTML(S) {
    const N = (id) => nodeHTML(S, id), arr = '<span class="arr"></span>';
    const rounds = ((S.nodes || {}).skeptic || {}).rounds || 0;
    return `<div class="pipe"><div class="flow">${N('monitor')}${arr}${N('supervisor')}${arr}${N('data_quality')}</div>
      <div class="varr"><span>clean → analyse &nbsp;·&nbsp; broken → stop and raise a ticket</span></div>
      <div class="stage"><span class="tag">Analysis</span><div class="flow">${N('decomposer')}${N('context')}${N('impact')}</div></div><div class="varr"></div>
      <div class="stage"><span class="tag">Reasoning loop</span><div class="flow">${N('hypothesis')}${arr}${N('skeptic')}${rounds > 1 ? `<span class="loop">↺ sent back ${rounds - 1}×</span>` : ''}</div></div><div class="varr"></div>
      <div class="flow">${N('reporter')}${arr}${N('approval')}${arr}${N('publish')}</div></div>`;
  }
  function runStateHTML(S) {
    const r = S.run;
    if (!r) return '<span class="dot"></span>Ready';
    if (r.status === 'running' || r.status === 'queued') return '<span class="dot live"></span>Running…';
    const m = { awaiting_approval: ['warn', 'Needs your decision'], published: ['ok', 'Published'], rejected: ['warn', 'Rejected'], error: ['bad', 'Error'] }[r.status] || ['', humanize(r.status)];
    return `<span class="dot ${m[0]}"></span>${m[1]}`;
  }
  function investigateHTML(S) {
    const sel = S.incidents.find((i) => i.id === S.pickId) || S.incidents[0];
    if (!sel) return emptyHTML('No incidents found', 'Run `make data` first.');
    const busy = !!S.run && (S.run.status === 'running' || S.run.status === 'queued');
    const list = S.incidents.map((i) => `<button class="row ${i.id === sel.id ? 'on' : ''}" data-act="pick" data-id="${i.id}" style="--c:var(--${DIFF[i.difficulty] || 'blue'})"><span class="num">${i.id}</span><span class="body"><b>${esc(i.name)}</b><small>${fmtDate(i.date_from)} to ${fmtDate(i.date_to)}${i.held_out ? ' · held-out' : ''}</small></span><span class="chip" style="--c:var(--${DIFF[i.difficulty] || 'blue'})">${esc(i.difficulty)}</span></button>`).join('');
    const scope = Object.entries(sel.scope || {}).map(([k, v]) => `<span class="chip" style="--c:var(--indigo)">${esc(k)}: ${esc(Array.isArray(v) ? v.join(', ') : v)}</span>`).join(' ');
    const prov = (S.meta.providers || []).find((p) => p.id === S.provider) || { note: '' };
    const short = { scripted: 'Offline', ollama: 'Ollama', gemini: 'Gemini' };
    const paces = [[0, 'Instant'], [0.7, 'Demo'], [1.6, 'Slow']];
    return `<div class="invest"><div class="glass card picker"><div class="cardhead"><h2>Incidents</h2><span class="sub">${S.incidents.length} planted in the data</span></div><div class="list">${list}</div></div>
      <div class="grid"><div class="glass card"><div class="selected soft"><b>${esc(sel.id)}. ${esc(sel.name)}</b><div class="note" style="margin-top:4px">${fmtDate(sel.date_from)} to ${fmtDate(sel.date_to)} · ${scope}</div></div>
        <div class="runbar"><div class="opt"><label>Brain</label><div class="seg">${(S.meta.providers || []).map((p) => `<button data-act="prov" data-id="${p.id}" class="${S.provider === p.id ? 'on' : ''}" ${busy ? 'disabled' : ''}>${short[p.id] || p.label}</button>`).join('')}</div></div>
        <div class="opt"><label>Pace</label><div class="seg">${paces.map(([v, t]) => `<button data-act="pace" data-v="${v}" class="${S.pace === v ? 'on' : ''}">${t}</button>`).join('')}</div></div><div class="spacer"></div>
        <button class="btn primary" data-act="run" ${busy || !S.meta.ready ? 'disabled' : ''}>${busy ? 'Running…' : '▶ Run investigation'}</button></div>
        <div class="note">${esc(prov.note)} A run only starts when you press the button.</div></div>
      <div class="glass card"><div class="cardhead"><h2>Agent pipeline</h2><span class="sub">click any agent to see what it did</span><div class="spacer"></div><span class="pill" id="runstate">${runStateHTML(S)}</span></div><div id="pipe">${pipelineHTML(S)}</div></div>
      <div id="result">${resultHTML(S)}</div>
      <div class="glass card"><div class="cardhead"><h2>Live log</h2></div><div class="log" id="log">${logHTML(S)}</div></div></div></div>`;
  }
  function logHTML(S) {
    const ev = S.events || [];
    if (!ev.length) return '<div><span class="m faint">Press Run to start. Every step appears here as it happens.</span></div>';
    return ev.map((e) => {
      const a = S.agentById[e.agent], c = a ? `var(--${a.color})` : 'var(--text2)';
      let msg = e.summary || '';
      if (e.type === 'start') msg = 'started';
      else if (e.type === 'done') msg = `finished in ${secs(e.ms || 0)}${(e.tool_calls || []).length ? ' · ' + e.tool_calls.length + ' tool call(s): ' + [...new Set(e.tool_calls.map((t) => t.tool))].join(', ') : ''}${e.error ? ' · ERROR ' + e.error : ''}`;
      return `<div style="--c:${c}"><span class="t">${(e.t || 0).toFixed(1)}s</span><span class="a">${esc(e.label || e.agent)}</span><span class="m">${esc(msg)}</span></div>`;
    }).join('');
  }

  /* ---------- result card (shared by Investigate and Reports) ---------- */
  function resultHTML(S, run, opts) {
    run = run || S.run; opts = opts || {};
    if (!run) return '';
    if (run.status === 'error') return `<div class="glass card"><div class="cardhead"><h2>The run hit an error</h2></div><p class="muted">${esc(run.error || 'unknown error')}</p><p class="note">Nothing was published. If you chose Ollama or Gemini, check that it is running or that GOOGLE_API_KEY is set, or switch to Offline.</p></div>`;
    const r = run.result;
    if (!r || !r.findings) return '';
    const f = r.findings, stopped = f.status === 'stopped_data_quality';
    const impactNeg = f.dollar_impact != null && f.dollar_impact < 0;
    const verdict = `<div class="verdict">
      <div class="glass vbox"><small>Outcome</small><b>${stopped ? 'Stopped: data fault' : 'Investigated'}</b></div>
      <div class="glass vbox" style="--c:var(--${catColor(f.cause_category)})"><small>Cause found</small><b style="color:var(--c)">${esc(humanize(f.cause_category))}</b></div>
      <div class="glass vbox"><small>Dollar impact</small><b class="${impactNeg ? 'down' : ''}">${stopped ? 'n/a (not trustworthy)' : fmtMoney(f.dollar_impact)}</b></div>
      <div class="glass vbox"><small>Confidence</small><b>${esc(humanize(f.confidence))}</b></div></div>`;
    let ticket = '';
    const tk = (r.dq || {}).ticket;
    if (stopped && tk) ticket = `<div class="soft" style="padding:14px 16px;margin-bottom:14px"><b>Ticket raised for data engineering</b><div class="note" style="margin-top:4px"><b>${esc(tk.title || '')}</b><br>${esc(tk.detail || '')}</div></div>`;
    let decide = '';
    if (!opts.readonly) {
      if (run.status === 'awaiting_approval' && run.approvable) decide = `<div class="actions"><button class="btn good" data-act="approve">Approve and publish</button><button class="btn bad" data-act="reject">Reject…</button><span class="faint">Nothing is published until you approve.</span></div>`;
      else if (run.status === 'awaiting_approval') decide = `<div class="note">This run was loaded from disk, so it can no longer be approved. Run it again to decide.</div>`;
      else if (run.status === 'published') decide = `<div class="soft" style="padding:12px 16px;margin-top:14px"><b class="up">Published</b><div class="note">${esc(((run.published || {}).brief_path) || '')}<br>Team channel: ${esc((run.published || {}).slack || '')}${(run.published || {}).ticket ? '<br>Ticket: ' + esc(run.published.ticket) : ''}</div></div>`;
      else if (run.status === 'rejected') decide = `<div class="soft" style="padding:12px 16px;margin-top:14px"><b>Rejected</b><div class="note">${esc(run.comment || 'No comment.')} Nothing was published.</div></div>`;
    }
    const truthBtn = opts.readonly ? '' : `<button class="btn" data-act="truth" style="margin-left:auto">${S.showTruth ? 'Hide' : 'Reveal'} planted ground truth</button>`;
    return `<div class="glass card"><div class="cardhead"><h2>Result</h2>${statusChip(run.status)}${truthBtn}</div>${verdict}${ticket}<div class="brief">${mdToHtml(r.brief)}</div>${decide}${!opts.readonly && S.showTruth ? truthHTML(S, f) : ''}</div>`;
  }
  function truthHTML(S, f) {
    const t = S.truth;
    if (!t) return '<div class="soft truth faint">Loading…</div>';
    const stopOk = t.expected_stop_at_data_gate ? f.status === 'stopped_data_quality' : f.status === 'investigated';
    const causeOk = (t.causes || []).includes(f.cause_category);
    let imp = '';
    if (t.planted_impact_dollars != null && f.dollar_impact != null) {
      const err = (f.dollar_impact - t.planted_impact_dollars) / Math.abs(t.planted_impact_dollars || 1);
      imp = `<div class="fact"><small>Dollar impact</small><span>Agents ${fmtMoney(f.dollar_impact)} vs planted ${fmtMoney(t.planted_impact_dollars)} · error ${fmtPct(Math.abs(err), 1, false)}</span></div>`;
    } else if (t.planted_impact_dollars == null) imp = '<div class="fact"><small>Dollar impact</small><span>None planted: this is a data fault, not a business effect.</span></div>';
    const ok = (b) => `<span class="chip" style="--c:var(--${b ? 'green' : 'red'})">${b ? 'Match' : 'Mismatch'}</span>`;
    return `<div class="soft truth"><div class="cardhead" style="margin-bottom:10px"><h2>Planted ground truth</h2><span class="sub">what the generator really did</span></div><div class="facts">
      <div class="fact"><small>Cause</small><span>Planted ${esc((t.causes || []).map(humanize).join(' + '))} · agents said ${esc(humanize(f.cause_category))} ${ok(causeOk)}</span></div>
      <div class="fact"><small>Data gate</small><span>${t.expected_stop_at_data_gate ? 'Should stop at the data-quality gate' : 'Should analyse the business'} ${ok(stopOk)}</span></div>${imp}</div></div>`;
  }

  /* ---------- agents ---------- */
  function agentsHTML(S) {
    if (!S.agents.length) return loadingHTML('Loading agents…');
    const kind = { code: 'Statistics', llm: 'AI agent', human: 'Human' };
    return `<div class="grid g3">${S.agents.map((a) => {
      const n = (S.nodes || {})[a.id], tools = (a.tools || []).slice(0, 5).map((t) => `<code>${esc(t.name)}</code>`).join('') + ((a.tools || []).length > 5 ? `<code>+${a.tools.length - 5}</code>` : '');
      return `<button class="glass agentcard" style="--c:var(--${a.color})" data-act="agent" data-id="${a.id}"><div style="display:flex;align-items:center;gap:12px"><span class="ic">${icon(a.id)}</span><div><h3>${esc(a.label)}</h3><span class="chip" style="--c:var(--${a.color})">${kind[a.kind] || a.kind}</span></div></div>
        <p>${esc(a.role)}</p>${tools ? `<div class="tools">${tools}</div>` : ''}<div class="faint" style="font-size:12px">${n ? 'Last run: ' + esc(nodeText(a.id, n)) : 'No run yet. Press Run on the Investigate page.'}</div></button>`;
    }).join('')}</div>`;
  }

  /* ---------- tasks ---------- */
  function tasksHTML(S) {
    const nodes = S.nodes || {}, any = Object.keys(nodes).length > 0;
    const cols = { todo: [], doing: [], done: [] };
    ORDER.forEach((id) => {
      const n = nodes[id] || { status: 'idle' };
      (['running', 'wait'].includes(n.status) ? cols.doing : ['done', 'skipped'].includes(n.status) ? cols.done : cols.todo).push(id);
    });
    const card = (id) => { const a = S.agentById[id], n = nodes[id] || { status: 'idle' }; return `<button class="tcard" style="--c:var(--${a.color})" data-act="agent" data-id="${id}"><b>${esc(a.label)}</b><small>${esc(nodeText(id, n))}</small></button>`; };
    const col = (t, k) => `<div class="col"><h3><span>${t}</span><span>${cols[k].length}</span></h3>${cols[k].map(card).join('') || '<small class="faint">–</small>'}</div>`;
    const todos = (S.tasks || []).map((t) => `<label class="todo ${t.done ? 'done' : ''}"><input type="checkbox" data-task="${esc(t.id)}" ${t.done ? 'checked' : ''}><div><span>${esc(t.text)}</span><small>${esc(t.source || 'Added by you')}</small></div><button class="x" data-act="task-del" data-id="${esc(t.id)}" title="Remove">×</button></label>`).join('');
    return `<div class="grid g2"><div class="glass card"><div class="cardhead"><h2>Current run</h2><span class="sub">${any ? esc(((S.run || {}).incident || {}).name || '') : 'no run yet'}</span></div>
      ${any ? `<div class="board">${col('Queued', 'todo')}${col('In progress', 'doing')}${col('Done', 'done')}</div>` : emptyHTML('No run yet', 'Start one from the Investigate page and the steps move across this board.')}</div>
      <div class="glass card"><div class="cardhead"><h2>Follow-ups</h2><span class="sub">from briefs and tickets, plus your own</span></div><div class="list">${todos || '<div class="faint">Nothing yet. Follow-ups from each finished run appear here.</div>'}</div>
      <div class="addrow"><input id="newtask" placeholder="Add a task and press Enter" autocomplete="off"><button class="btn" data-act="task-add">Add</button></div></div></div>`;
  }

  /* ---------- reports ---------- */
  function reportsHTML(S) {
    const hist = S.hist || [], p = S.pub || {};
    const list = hist.map((r) => `<button class="row ${S.repId === r.id ? 'on' : ''}" data-act="rep" data-id="${esc(r.id)}" style="--c:var(--${catColor(r.cause)})"><span class="num">${esc((r.incident || {}).id)}</span><span class="body"><b>${esc((r.incident || {}).name || '')}</b><small>${esc((r.created || '').replace('T', ' '))} · ${esc(r.provider)}</small></span>${statusChip(r.status)}</button>`).join('');
    const rep = S.rep;
    const detail = rep ? (resultHTML(S, rep, { readonly: true }) || '<div class="glass card"><div class="empty">This run has no result.</div></div>') : '<div class="glass card">' + emptyHTML('Pick a report', 'Choose an investigation on the left to read its brief.') + '</div>';
    const tickets = (p.tickets || []).map((t) => `<div class="fact"><small>${esc(t.severity || 'ticket')}</small><span>${esc(t.title)}</span></div>`).join('');
    const outbox = (p.slack_outbox || []).slice(-5).map((t) => `<div class="fact"><small>Team channel (outbox)</small><span>${esc(t.text)}</span></div>`).join('');
    return `<div class="invest"><div class="glass card picker"><div class="cardhead"><h2>Investigations</h2><span class="sub">${hist.length}</span></div><div class="list">${list || '<div class="faint">None yet.</div>'}</div></div>
      <div class="grid">${detail}<div class="glass card"><div class="cardhead"><h2>Published so far</h2><span class="sub">${(p.briefs || []).length} brief(s) · ${(p.annotations || []).length} dashboard annotation(s) · ${(p.tickets || []).length} ticket(s)</span></div>
      <div class="facts">${tickets}${outbox}${!tickets && !outbox ? '<div class="faint">Approve a brief on the Investigate page and it shows up here.</div>' : ''}</div></div></div></div>`;
  }

  /* ---------- system ---------- */
  function systemHTML(S) {
    const s = S.sys;
    if (!s) return loadingHTML('Loading system…');
    const maxRows = Math.max(1, ...s.tables.map((t) => t.rows));
    const tables = s.tables.map((t) => `<div class="bar"><div class="t"><span class="mono">${esc(t.name)}</span><span>${fmtNum(t.rows, 1)} rows</span></div><div class="track"><div class="fill" style="width:${Math.max(2, 100 * t.rows / maxRows).toFixed(1)}%"></div></div></div>`).join('');
    const g = s.guardrails || {}, cloud = s.warehouse === 'bigquery';
    const li = (t) => `<div class="row"><span class="dot ok"></span><span class="body"><b>${t}</b></span></div>`;
    return `<div class="grid g2"><div class="grid">
      <div class="glass card"><div class="cardhead"><h2>Data source</h2><span class="chip" style="--c:var(--${cloud ? 'blue' : 'green'})">${cloud ? 'BigQuery (cloud)' : 'DuckDB (local)'}</span></div>
        <div class="facts"><div class="fact"><small>Project · region</small><span>${esc(s.project)} · ${esc(s.region)}</span></div><div class="fact"><small>Switch source</small><span>Local: <code>make portal</code> &nbsp; Cloud: <code>make portal-cloud</code></span></div></div>
        <div class="actions"><a class="btn" target="_blank" rel="noopener" href="${esc(s.console.bigquery)}">Open BigQuery</a><a class="btn" target="_blank" rel="noopener" href="${esc(s.console.project)}">Cloud console</a><a class="btn" target="_blank" rel="noopener" href="${esc(s.console.billing)}">Budgets</a></div></div>
      <div class="glass card"><div class="cardhead"><h2>Tables in the warehouse</h2></div><div class="bars">${tables || '<div class="faint">Loading…</div>'}</div></div></div>
      <div class="grid"><div class="glass card"><div class="cardhead"><h2>Cost control</h2></div><div class="list">${li('No scheduler. A run starts only when someone presses Run.')}${li('Offline baseline costs nothing: no model, no network.')}${li('Ollama is free on this Mac. Gemini uses the free API tier.')}${li('BigQuery reads are capped at ' + fmtNum((g.max_bytes_billed || 0) / 1e9, 1) + ' GB per query.')}</div></div>
      <div class="glass card"><div class="cardhead"><h2>Guardrails</h2></div><div class="list">${li('Agents call read-only, parameterised tools. They never write SQL.')}${li('At most ' + g.max_tool_calls_per_run + ' tool calls per investigation.')}${li('The Skeptic can send an answer back at most ' + g.max_skeptic_rounds + ' times.')}${li('Nothing is published without human approval.')}</div></div></div></div>`;
  }

  /* ---------- agent drawer ---------- */
  function drawerHTML(S) {
    const id = S.drawer, a = S.agentById[id];
    if (!a) return '';
    const n = (S.nodes || {})[id];
    const facts = n && n.output ? keyFacts(id, n.output) : [];
    const calls = (n && n.calls) || [];
    const kind = { code: 'Statistics, no language model', llm: 'AI agent', human: 'Human decision' }[a.kind] || a.kind;
    const head = `<header style="--c:var(--${a.color})"><span class="ic">${icon(id)}</span><div><h2>${esc(a.label)}</h2><span class="note" style="margin:0">${esc(kind)}${n ? ' · ' + esc(nodeText(id, n)) : ''}</span></div><button class="x" data-act="close">✕</button></header>`;
    const raw = (v) => esc(JSON.stringify(v, null, 2));
    const body = `<div class="body"><div><h4>What it does</h4><p class="muted">${esc(a.role)}</p></div>
      ${n && n.error ? `<div class="soft" style="padding:12px;color:var(--red)"><b>Error</b><div>${esc(n.error)}</div></div>` : ''}
      ${facts.length ? `<div><h4>What it found</h4><div class="facts">${facts.map(([k, v]) => `<div class="fact"><small>${esc(k)}</small><span>${esc(v)}</span></div>`).join('')}</div></div>` : `<div class="soft" style="padding:14px"><span class="muted">${n ? 'No structured output for this step.' : 'Run an investigation to see what this agent produces.'}</span></div>`}
      ${calls.length ? `<div><h4>Tool calls (${calls.length})</h4>${calls.map((c) => `<details><summary><code>${esc(c.tool)}</code> ${c.error ? '<span class="down">· error</span>' : ''}</summary><pre>${raw(c.arguments)}\n\n→ ${esc(c.result_preview || c.error || '')}</pre></details>`).join('')}</div>` : ''}
      ${(a.tools || []).length ? `<div><h4>Tools this agent may use</h4><div class="facts">${a.tools.map((t) => `<div class="fact"><small>${esc(t.name)}</small><span>${esc(t.description)}</span></div>`).join('')}</div></div>` : ''}
      ${n && n.output ? `<div><h4>Raw output</h4><details><summary>Show JSON</summary><pre>${raw(n.output)}</pre></details></div>` : ''}</div>`;
    return head + body;
  }

  return { IC, icon, ORDER, PAGES, catColor, deriveNodes, nodeText, sideStatusHTML, topHTML, dashboardHTML, chartSVG, investigateHTML, pipelineHTML, runStateHTML, logHTML, resultHTML, agentsHTML, tasksHTML, reportsHTML, systemHTML, drawerHTML, loadingHTML, emptyHTML, monthYear };
});
