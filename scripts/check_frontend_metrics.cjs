// Execute actual rendering functions with a minimal DOM; this is not browser visual QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
// Reproduce the synthetic fixture in memory; never require a saved local report.
const fixture = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c',
  'import json, sys; sys.path.insert(0, "scripts"); from run_example import reproduce; print(json.dumps(reproduce()))',
], { cwd: root, encoding: 'utf8' }));
class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.className = ''; this.style = {}; this._text = ''; this.classList = {add(){}, remove(){}, toggle(){}}; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent ?? String(child)).join(''); }
}
const hosts = new Map();
const context = vm.createContext({
  document: {createElement: tag => new Element(tag), addEventListener(){},
    querySelector: selector => { if (!hosts.has(selector)) hosts.set(selector, new Element()); return hosts.get(selector); }},
  Node: Element, console,
});
vm.runInContext(fs.readFileSync(path.join(root, 'src/bom_change_assistant/web_assets/app.js'), 'utf8'), context);
function descendants(node) { return [node, ...node.children.flatMap(child => child instanceof Element ? descendants(child) : [])]; }
for (const [unfinished, undelivered, expectedUnfinished, expectedUndelivered] of [
  [6, 8, '6', '8'], [null, 8, '待补数据', '8'], [6, null, '6', '待补数据'], [0, 0, '0', '0'],
]) {
  const result = structuredClone(fixture);
  Object.assign(result.planning_impact.metrics, {
    unfinished_production_quantity: unfinished, undelivered_production_quantity: undelivered,
    remaining_production_quantity: 999, // UI must not accidentally render the legacy field.
  });
  context.result = result;
  vm.runInContext('renderPlanningImpact(result.planning_impact); renderReport("", result);', context);
  for (const selector of ['#tab-planning', '#report-content']) {
    const cards = descendants(hosts.get(selector)).filter(n => ['operational-metric', 'report-metric'].includes(n.className));
    for (const [label, expected] of [['未完工成品', expectedUnfinished], ['待交付成品', expectedUndelivered]]) {
      const card = cards.find(n => n.children.some(child => child.textContent === label));
      assert.ok(card, `${selector}: missing ${label}`);
      assert.equal(card.children.find(child => child.tag === 'strong').textContent, expected);
    }
    assert.ok(!hosts.get(selector).textContent.includes('待交付/未完工'));
  }
}
console.log('Frontend rendering verified: 4 scenarios × 2 views; distinct quantities, unknowns and zero preserved.');
// Display the same business facts in the planning view and downloadable-report preview.
for (const check of [
  {status: '已计算', by_top: [{top_material: 'FG-TRACE', delta_per_top_unit: 9}], pending_paths: [], boundary: '理论差量，非实际需求'},
  {status: '需人工复核', by_top: [], pending_paths: [{path_id: 'ECN-TRACE-UP-001', reason: '上层用量缺失'}], boundary: '不得输出不完整合计'},
  {status: '需人工复核', by_top: [], pending_paths: [{path_id: 'ECN-TRACE-UP-002', reason: '旧用量与来源不一致'}], boundary: '不得输出不完整合计'},
]) {
  const result = structuredClone(fixture);
  result.change_checks = [{ecn: 'ECN-TRACE', quantity_impact: check,
    effectivity_comparison: {added_paths: 2, removed_paths: 1, unchanged_paths: 3, boundary: '按两个日期比较'}}];
  result.sales_orders = [{sales_order: 'SO-TRACE', item: '10', material: 'FG-TRACE', status: '未知', requested_date: null, source_id: 'SRC-TRACE', source_row: 7}];
  context.result = result;
  vm.runInContext('renderPlanningImpact(result.planning_impact); appendChangeChecks(document.querySelector("#tab-planning"), result); renderReport("", result);', context);
  for (const selector of ['#tab-planning', '#report-content']) {
    const text = hosts.get(selector).textContent;
    for (const expected of ['ECN-TRACE', check.status, check.boundary, '新增使用路径：2', '退出使用路径：1', '仍然有效路径：3']) assert.ok(text.includes(expected), expected);
    for (const pending of check.pending_paths) {
      assert.ok(text.includes(pending.path_id));
      assert.ok(text.includes(pending.reason));
    }
    if (check.by_top.length) assert.ok(text.includes('FG-TRACE：每单位成品理论差量 9'));
    else assert.ok(!text.includes('每单位成品理论差量'));
  }
  for (const expected of ['SO-TRACE / 10', '未知', '待确认关联订单', 'SRC-TRACE 第 7 行']) assert.ok(hosts.get('#report-content').textContent.includes(expected), expected);
}
console.log('Report consistency verified: quantity delta, two review reasons, evidence IDs, effectivity counts and unknown sales status.');
// Keep procurement presentation covered by the existing CI frontend job.
for (const quantity of ['20', '0', '待补数据']) {
  const result = structuredClone(fixture);
  const facts = ['PO-CI/00010 · EA · 截至 2026-11-30',
    `累计净到货量：${quantity}；剩余未到货承诺：30`,
    '覆盖待确认 · SRC-CI 工作表 2 第 7 行',
    '复核角色：工程、采购、计划', '已批准动作：无（approved_action=null）',
    '<img src=x onerror=alert(1)>'];
  result.procurement_impact = {sections: [{title: '采购承诺与到货核查', lines: facts}]};
  context.result = result;
  vm.runInContext('renderPlanningImpact(result.planning_impact); appendProcurement(document.querySelector("#tab-planning"), result); renderReport("", result);', context);
  for (const selector of ['#tab-planning', '#report-content']) {
    const host = hosts.get(selector);
    for (const fact of ['采购承诺与到货核查', ...facts]) assert.ok(host.textContent.includes(fact), `${selector}: ${fact}`);
    assert.ok(!descendants(host).some(node => String(node.innerHTML ?? '').includes('<img src=x')), 'Purchase input must never become executable HTML');
  }
}
console.log('Procurement UI verified: quantity, zero, unknown, scope, evidence, review roles and unapproved action in both views; input stays text.');
// Exercise the real import workflow: a failed batch retains the old report;
// a successful publication must stop showing links into the previous snapshot.
(async () => {
  for (const fails of [true, false]) {
    const calls = [];
    context.document.querySelector('#report-download').removeAttribute = function(key) { delete this[key]; };
    context.calls = calls;
    context.failImport = fails;
    vm.runInContext(`
      state.files = [{name: 'synthetic.xlsx'}];
      state.result = {analysis_id: 'OLD'}; state.report = 'old report';
      state.batchItems = []; document.querySelector('#report-download').href = '/old-report';
      api = async (path) => { calls.push(path); if (failImport && path === '/api/import/file') throw Error('bad workbook'); return {}; };
      refreshStatus = async () => {}; chooseFiles = () => {}; toast = () => {};
    `, context);
    await vm.runInContext('importFiles()', context);
    assert.equal(vm.runInContext('state.result?.analysis_id ?? null', context), fails ? 'OLD' : null);
    assert.equal(vm.runInContext('state.report', context), fails ? 'old report' : '');
    assert.equal(hosts.get('#report-download').href, fails ? '/old-report' : undefined);
    assert.ok(calls.includes(fails ? '/api/snapshot/abort' : '/api/snapshot/finish'));
  }
  console.log('Import UI verified: failures preserve the old report; successful publication clears stale results and links.');
})().catch(error => { console.error(error); process.exitCode = 1; });
