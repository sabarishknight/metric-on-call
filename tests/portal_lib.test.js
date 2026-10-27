// Run with:  node tests/portal_lib.test.js
const assert = require('assert');
const L = require('../portal/static/lib.js');

assert.strictEqual(L.fmtMoney(-7760.71), '-$7,761');
assert.strictEqual(L.fmtMoney(1234567, true), '$1.23M');
assert.strictEqual(L.fmtMoney(null), 'n/a');
assert.strictEqual(L.fmtPct(0.034), '+3.4%');
assert.strictEqual(L.fmtPct(-0.1234, 1), '-12.3%');
assert.strictEqual(L.fmtDate('2025-07-23'), '23 Jul 2025');
assert.strictEqual(L.humanize('pos_outage'), 'Pos outage');
assert.strictEqual(L.esc('<a href="x">&</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');

const t = L.niceTicks(3, 97, 5);
assert.ok(t[0] <= 3 && t[t.length - 1] >= 97 && t.length >= 3, 'ticks cover range: ' + t);
const sx = L.scaleLinear(0, 10, 0, 100);
assert.strictEqual(sx(5), 50);

const p = L.monotonePath([[0, 10], [1, 20], [2, 15], [3, 15], [4, 30]]);
assert.ok(p.startsWith('M0,10C') && !p.includes('NaN'), p);
assert.strictEqual(L.monotonePath([]), '');
const sp = L.sparkPath([1, 3, 2, 5, 4], 100, 30);
assert.ok(sp.line && sp.area.endsWith('Z') && !sp.line.includes('NaN'));

const md = L.mdToHtml('# Title\n\n**Answer: `pos_outage`** (confidence: high)\n\n- Dollar impact: **-$7,761**\n  - pos_outage: -$7,761\n- Where: a=b\n\n## Evidence\n- x <script>alert(1)</script>\n');
assert.ok(md.includes('<h1>Title</h1>') && md.includes('<strong>') && md.includes('<code>pos_outage</code>'));
assert.ok(md.includes('<ul><li>Dollar impact') && md.includes('<ul><li>pos_outage'), md);
assert.ok(!md.includes('<script>'), 'html must be escaped');
assert.strictEqual((md.match(/<ul>/g) || []).length, (md.match(/<\/ul>/g) || []).length, 'balanced lists');

const kf = L.keyFacts('data_quality', { clean: false, likely_causes: ['pipeline_duplicate_load'], summary: 's', phantom_net_sales: 1000 });
assert.ok(kf.some(([k, v]) => k === 'Data trustworthy' && v.startsWith('No')));
assert.deepStrictEqual(L.keyFacts('skeptic', null), []);
console.log('portal lib tests passed');
