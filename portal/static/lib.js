/* Pure helpers (no DOM). Loaded in the browser as window.Lib and in node for tests. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Lib = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

  function fmtNum(x, d = 1) {
    if (x == null || isNaN(x)) return '–';
    const a = Math.abs(x);
    if (a >= 1e9) return (x / 1e9).toFixed(d) + 'B';
    if (a >= 1e6) return (x / 1e6).toFixed(d) + 'M';
    if (a >= 1e4) return (x / 1e3).toFixed(d) + 'k';
    return Math.round(x).toLocaleString('en-US');
  }
  function fmtMoney(x, compact = false) {
    if (x == null || isNaN(x)) return 'n/a';
    const sign = x < 0 ? '-' : '';
    const a = Math.abs(x);
    if (compact) return sign + '$' + fmtNum(a, a >= 1e6 ? 2 : 1);
    return sign + '$' + Math.round(a).toLocaleString('en-US');
  }
  function fmtPct(x, d = 1, signed = true) {
    if (x == null || isNaN(x)) return '–';
    const v = (x * 100).toFixed(d);
    return (signed && x > 0 ? '+' : '') + v + '%';
  }
  function fmtMetric(key, v) {
    if (v == null || isNaN(v)) return '–';
    if (key === 'net_sales') return fmtMoney(v, true);
    if (key === 'avg_basket') return '$' + v.toFixed(2);
    if (key === 'conversion') return (v * 100).toFixed(1) + '%';
    return fmtNum(v);
  }
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fmtDate(iso, withYear = true) {
    if (!iso) return '';
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    return `${d} ${MONTHS[m - 1]}${withYear ? ' ' + y : ''}`;
  }
  const humanize = (s) => String(s ?? '').replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
  const secs = (ms) => (ms < 1000 ? ms + ' ms' : (ms / 1000).toFixed(1) + ' s');

  function niceNum(range, round) {
    const exp = Math.floor(Math.log10(range || 1));
    const f = range / Math.pow(10, exp);
    let nf;
    if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
    else nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nf * Math.pow(10, exp);
  }
  function niceTicks(min, max, count = 5) {
    if (!(max > min)) { max = min + 1; }
    const step = niceNum(niceNum(max - min, false) / (count - 1), true);
    const start = Math.floor(min / step) * step;
    const end = Math.ceil(max / step) * step;
    const out = [];
    for (let v = start; v <= end + step / 2; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const scaleLinear = (d0, d1, r0, r1) => (x) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((x - d0) / (d1 - d0)) * (r1 - r0));

  // Monotone cubic interpolation (same idea as d3.curveMonotoneX): smooth, never overshoots.
  function monotonePath(pts) {
    const n = pts.length;
    if (n === 0) return '';
    if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
    if (n === 2) return `M${pts[0][0]},${pts[0][1]}L${pts[1][0]},${pts[1][1]}`;
    const dx = [], m = [], t = new Array(n);
    for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / (dx[i] || 1); }
    t[0] = m[0]; t[n - 1] = m[n - 2];
    for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
    for (let i = 0; i < n - 1; i++) {
      if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
      const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
      if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
    }
    let d = `M${pts[0][0]},${pts[0][1]}`;
    for (let i = 0; i < n - 1; i++) {
      const h = dx[i] / 3;
      d += `C${pts[i][0] + h},${pts[i][1] + t[i] * h},${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h},${pts[i + 1][0]},${pts[i + 1][1]}`;
    }
    return d;
  }
  const r1 = (x) => Math.round(x * 10) / 10;

  function sparkPath(values, w, h, pad = 2) {
    const v = values.filter((x) => x != null);
    if (v.length < 2) return { line: '', area: '' };
    const lo = Math.min(...v), hi = Math.max(...v);
    const sx = scaleLinear(0, v.length - 1, pad, w - pad), sy = scaleLinear(lo, hi === lo ? lo + 1 : hi, h - pad, pad);
    const pts = v.map((y, i) => [r1(sx(i)), r1(sy(y))]);
    const line = monotonePath(pts);
    return { line, area: `${line}L${pts[pts.length - 1][0]},${h}L${pts[0][0]},${h}Z` };
  }

  // Minimal, safe markdown for our own brief template: headings, bold, italics, code, nested bullets, paragraphs.
  function inline(s) {
    return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
  }
  function mdToHtml(md) {
    const lines = String(md ?? '').split('\n');
    let html = '', para = [], depth = 0;
    const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(' '))}</p>`; para = []; } };
    const closeTo = (d) => { while (depth > d) { html += '</ul>'; depth--; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      let m;
      if (!line.trim()) { flushPara(); closeTo(0); continue; }
      if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) { flushPara(); closeTo(0); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; continue; }
      if ((m = /^(\s*)[-*]\s+(.*)$/.exec(line))) {
        flushPara();
        const lvl = Math.floor(m[1].length / 2) + 1;
        while (depth < lvl) { html += '<ul>'; depth++; }
        closeTo(lvl);
        html += `<li>${inline(m[2])}</li>`;
        continue;
      }
      closeTo(0);
      para.push(line.trim());
    }
    flushPara(); closeTo(0);
    return html;
  }

  // Short "key facts" for an agent's output, used in node subtitles and the detail drawer.
  function keyFacts(agent, o) {
    if (!o) return [];
    const f = [];
    const add = (k, v) => { if (v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && !v.length)) f.push([k, Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : String(v)]); };
    switch (agent) {
      case 'monitor': {
        const h = o.headline || {};
        add('Window', `${fmtDate(o.date_from)} to ${fmtDate(o.date_to)}`);
        add('Headline series', `${h.level || ''} · ${h.metric || ''}${h.slice ? ' · ' + h.slice : ''}`);
        add('Move vs expected', h.pct_dev != null ? fmtPct(h.pct_dev) : null);
        add('Observed', h.observed != null ? fmtMoney(h.observed) : null);
        add('Expected', h.expected != null ? fmtMoney(h.expected) : null);
        add('Series flagged', o.n_series_flagged);
        break;
      }
      case 'supervisor': add('Plan', o.summary); add('Look first at', o.decomposer_filters); add('Notes', o.notes); break;
      case 'data_quality':
        add('Data trustworthy', o.clean ? 'Yes' : 'No: stop here');
        add('Likely cause', o.likely_causes); add('Summary', o.summary);
        add('Overstated sales', o.phantom_net_sales != null ? fmtMoney(o.phantom_net_sales) : null);
        add('Ticket', o.ticket && o.ticket.title);
        break;
      case 'decomposer':
        add('Located at', o.located_filters); add('Share explained', o.explained_share != null ? fmtPct(o.explained_share, 0, false) : null);
        add('Broad-based', o.broad_based ? 'Yes' : null); add('Path', o.driver_path); add('Summary', o.summary);
        break;
      case 'context':
        add('Promos', o.promos); add('Price changes', o.price_changes); add('Ops log', o.ops_log);
        add('Store events', o.store_events); add('Holidays', o.holidays); add('Weather', o.weather); add('Summary', o.summary);
        break;
      case 'impact':
        add('Total impact', o.total_impact_dollars != null ? fmtMoney(o.total_impact_dollars) : null);
        (o.by_cause || []).forEach((c) => add('By cause · ' + humanize(c.category), c.impact_dollars != null ? fmtMoney(c.impact_dollars) : 'n/a'));
        add('Projection', o.projection); add('Summary', o.summary);
        break;
      case 'hypothesis':
        (o.ranked || []).slice(0, 4).forEach((h, i) => add(`#${i + 1} ${humanize(h.category)}`, `${Math.round(h.confidence * 100)}% · ${h.rationale}`));
        add('Several causes', o.multi_cause ? 'Yes' : null);
        break;
      case 'skeptic':
        add('Answer survives', o.survives ? 'Yes' : 'No: sent back');
        (o.objections || []).forEach((x) => add(`${humanize(x.check)} (${x.severity})`, x.text));
        add('Summary', o.summary);
        break;
      case 'reporter':
        add('Status', humanize(o.status)); add('Cause', humanize(o.cause_category));
        add('Impact', o.dollar_impact != null ? fmtMoney(o.dollar_impact) : null); add('Confidence', o.confidence);
        add('Action', o.recommended_action);
        break;
      default:
        Object.entries(o).slice(0, 8).forEach(([k, v]) => add(humanize(k), v));
    }
    return f;
  }

  return { esc, clamp, fmtNum, fmtMoney, fmtPct, fmtMetric, fmtDate, humanize, secs, niceTicks, scaleLinear, monotonePath, sparkPath, mdToHtml, keyFacts };
});
