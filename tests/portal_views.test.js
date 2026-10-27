// Run with:  node tests/portal_views.test.js   (renders every screen with fixture data and checks the HTML)
const assert = require('assert');
const V = require('../portal/static/views.js');

const AG = [['monitor', 'Monitor', 'code', 'teal'], ['supervisor', 'Supervisor', 'llm', 'indigo'], ['data_quality', 'Data Quality Auditor', 'llm', 'orange'],
  ['decomposer', 'Metric Decomposer', 'llm', 'blue'], ['context', 'Context Investigator', 'llm', 'purple'], ['impact', 'Impact and Forecast', 'llm', 'green'],
  ['hypothesis', 'Hypothesis Tester', 'llm', 'pink'], ['skeptic', 'Skeptic', 'llm', 'red'], ['reporter', 'Reporter', 'llm', 'yellow'],
  ['approval', 'Human approval', 'human', 'mint'], ['publish', 'Publish', 'code', 'cyan']]
  .map(([id, label, kind, color]) => ({ id, label, kind, color, role: 'Does ' + label, tools: id === 'decomposer' ? [{ name: 'drill_down', description: 'd' }] : [] }));

const dates = [];
for (let i = 0; i < 300; i++) { const d = new Date(Date.UTC(2025, 6, 1 + i)); dates.push(d.toISOString().slice(0, 10)); }
const wave = (b, a) => dates.map((_, i) => b + a * Math.sin(i / 6));
const S = {
  meta: { ready: true, warehouse: 'duckdb', data_window: { from: dates[0], to: dates[299] }, providers: [{ id: 'scripted', label: 'Offline baseline', note: 'n' }, { id: 'ollama', label: 'Ollama', note: 'n' }, { id: 'gemini', label: 'Gemini', note: 'n' }] },
  incidents: [{ id: 1, name: 'Card outage', category: 'pos_outage', difficulty: 'medium', held_out: false, date_from: '2025-07-23', date_to: '2025-07-24', scope: { region: 'Western Sydney', stores: ['WS01', 'WS02'] } },
    { id: 3, name: 'Late data', category: 'pipeline_missing_data', difficulty: 'easy', held_out: true, date_from: '2025-09-01', date_to: '2025-09-02', scope: {} }],
  agents: AG, agentById: Object.fromEntries(AG.map((a) => [a.id, a])), pickId: 1, provider: 'scripted', pace: 0.7, metric: 'net_sales', range: 180,
  dash: { dates, series: { net_sales: wave(100000, 9000), transactions: wave(9000, 800), visits: wave(30000, 2000), avg_basket: wave(11, 1), conversion: wave(0.3, 0.02) },
    kpis: ['net_sales', 'transactions', 'avg_basket', 'conversion'].map((k) => ({ key: k, label: V.IC[k] ? k : k, value: 1234.5, prev: 1200, pct: 0.028, spark: wave(10, 2).slice(0, 28) })),
    regions: [{ region: 'cbd', net_sales: 800000, pct: 0.03 }, { region: 'inner_west', net_sales: 500000, pct: -0.02 }],
    incidents: [{ id: 1, name: 'Card outage', from: '2025-07-23', to: '2025-07-24', category: 'pos_outage' }], annotations: [{ from: '2025-07-23', to: '2025-07-24', label: 'pos_outage' }] },
  hist: [{ id: 'r1', incident: { id: 1, name: 'Card outage' }, provider: 'scripted', status: 'published', created: '2026-10-26T10:00:00', cause: 'pos_outage', impact: -7700 }],
  pub: { tickets: [{ title: 'Fix dup load', severity: 'high' }], slack_outbox: [{ text: 'hi' }], briefs: [], annotations: [] },
  sys: { warehouse: 'duckdb', project: 'metric-on-call', region: 'australia-southeast1', tables: [{ name: 'raw_sales_txn', rows: 244890 }, { name: 'dim_store', rows: 24 }],
    guardrails: { max_tool_calls_per_run: 40, max_skeptic_rounds: 2, max_bytes_billed: 1e9 }, console: { bigquery: 'https://x', project: 'https://y', billing: 'https://z' } },
  tasks: [{ id: 't1', text: 'Do a thing', source: 'Review', done: false }], showTruth: true,
  truth: { causes: ['pos_outage'], planted_impact_dollars: -7760.7, expected_stop_at_data_gate: false },
};

const ev = (type, agent, extra) => Object.assign({ type, agent, label: agent, t: 0.5 }, extra);
const events = [ev('done', 'monitor', { ms: 0, output: { date_from: '2025-07-23', date_to: '2025-07-24', headline: { level: 'total', metric: 'net_sales', pct_dev: -0.04, observed: 1, expected: 2 }, n_series_flagged: 5 } }),
  ev('start', 'supervisor'), ev('done', 'supervisor', { ms: 12, output: { summary: 'plan', decomposer_filters: { payment: 'card' }, notes: [] } }),
  ev('start', 'data_quality'), ev('done', 'data_quality', { ms: 300, tool_calls: [{ tool: 'run_dq_checks', arguments: { a: 1 }, result_preview: '{"ok":true}' }], output: { clean: true, summary: 'ok', likely_causes: [] } }),
  ev('start', 'decomposer'), ev('done', 'decomposer', { ms: 500, tool_calls: [{ tool: 'drill_down', arguments: {}, result_preview: '{}' }], output: { located_filters: { region: 'ws' }, explained_share: 0.8, summary: 's', driver_path: ['a', 'b'] } }),
  ev('done', 'hypothesis', { ms: 20, output: { ranked: [{ category: 'pos_outage', confidence: 0.9, rationale: 'r' }], summary: 's' } }),
  ev('done', 'skeptic', { ms: 20, output: { survives: false, objections: [{ check: 'seasonality', severity: 'major', text: 't' }], summary: 's' } }),
  ev('done', 'hypothesis', { ms: 20, output: { ranked: [{ category: 'pos_outage', confidence: 0.9, rationale: 'r' }], summary: 's' } }),
  ev('done', 'skeptic', { ms: 20, output: { survives: true, objections: [], summary: 's' } }),
  ev('approval', 'approval', { summary: 'wait', output: { cause: 'pos_outage' } })];
const findings = { status: 'investigated', cause_category: 'pos_outage', dollar_impact: -7700, confidence: 'high', recommended_action: 'Fix it' };
const result = { findings, brief: '# Brief\n\n**Answer: `pos_outage`**\n\n- Impact: **-$7,700**\n  - pos_outage: -$7,700\n\n## Recommended action\nFix it\n', dq: {} };

function check(name, html) {
  assert.ok(typeof html === 'string' && html.length > 40, name + ': empty output');
  for (const bad of ['undefined', 'NaN', '[object', 'null<', '>null']) assert.ok(!html.includes(bad), `${name}: contains "${bad}": ...${html.slice(Math.max(0, html.indexOf(bad) - 80), html.indexOf(bad) + 60)}`);
  return html;
}

// idle (no run)
S.run = null; S.events = []; S.nodes = {};
check('dashboard', V.dashboardHTML(S));
check('investigate idle', V.investigateHTML(S));
check('agents idle', V.agentsHTML(S));
check('tasks idle', V.tasksHTML(S));
check('reports idle', V.reportsHTML(S));
check('system', V.systemHTML(S));
check('top', V.topHTML(S, 'dashboard'));
check('side', V.sideStatusHTML(S));
const chart = V.chartSVG(S, 900);
check('chart', chart.html);
assert.ok(chart.model && chart.model.n === 180 && chart.model.bandAt.length === 180);
for (const m of ['transactions', 'visits', 'avg_basket', 'conversion']) { S.metric = m; check('chart ' + m, V.chartSVG(S, 700).html); }
S.metric = 'net_sales'; S.range = 0; check('chart all', V.chartSVG(S, 700).html); S.range = 180;

// mid-run
S.events = events.slice(0, 5); S.run = { id: 'r', status: 'running', incident: S.incidents[0] }; S.nodes = V.deriveNodes(S.events, S.run);
assert.strictEqual(S.nodes.supervisor.status, 'done'); assert.strictEqual(S.nodes.data_quality.status, 'done'); assert.strictEqual(S.nodes.data_quality.tools, 1);
check('pipeline mid', V.pipelineHTML(S)); check('log mid', V.logHTML(S)); check('investigate mid', V.investigateHTML(S));
assert.ok(V.investigateHTML(S).includes('Running…'));

// finished, awaiting approval
S.events = events; S.run = { id: 'r', status: 'awaiting_approval', approvable: true, incident: S.incidents[0], result };
S.nodes = V.deriveNodes(S.events, S.run);
assert.strictEqual(S.nodes.approval.status, 'wait'); assert.strictEqual(S.nodes.skeptic.rounds, 2); assert.strictEqual(S.nodes.impact.status, 'skipped');
const inv = check('investigate done', V.investigateHTML(S));
assert.ok(inv.includes('data-act="approve"') && inv.includes('Reveal') === false && inv.includes('Match'), 'approve button + truth compare');
assert.ok(inv.includes('sent back 1×'));
check('agents after run', V.agentsHTML(S)); check('tasks after run', V.tasksHTML(S));
for (const id of V.ORDER) { S.drawer = id; check('drawer ' + id, V.drawerHTML(S)); }

// data-quality stop
const stopped = { findings: { status: 'stopped_data_quality', cause_category: 'pipeline_duplicate_load', dollar_impact: null, confidence: 'high' }, brief: '# Stop\n\n**Stopped.**', dq: { ticket: { title: 'Fix', detail: 'dup' } } };
S.run = { id: 'r2', status: 'published', published: { brief_path: '/x.md', slack: 'outbox', ticket: 'Fix' }, incident: S.incidents[1], result: stopped };
S.truth = { causes: ['pipeline_duplicate_load'], planted_impact_dollars: null, expected_stop_at_data_gate: true };
const st = check('stopped', V.resultHTML(S));
assert.ok(st.includes('Ticket raised') && st.includes('Published'));
check('reports detail', V.reportsHTML(Object.assign({}, S, { rep: S.run, repId: 'r2' })));
S.run = { id: 'r3', status: 'error', error: 'boom <b>' }; assert.ok(V.resultHTML(S).includes('boom &lt;b&gt;'));
console.log('portal view tests passed');
