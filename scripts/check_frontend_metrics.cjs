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
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.className = ''; this._text = ''; this.classList = {add(){}, remove(){}, toggle(){}}; }
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
