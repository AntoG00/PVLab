/* Run with Node: test solar tradeoff heatmap readable layout, intrinsic SVG dimensions,
   expanded dialog interaction, financial basis labels, and tooltip/marker regressions. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const elements = new Map();

function createElement(id) {
  const classSet = new Set();
  const listeners = {};
  const attrs = new Map();
  const children = [];
  let isOpen = false;
  let parentNode = null;
  const el = {
    id,
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    hidden: false,
    style: {},
    dataset: {},
    tagName: (id.includes('Btn') || id.includes('Close') || id.includes('button')) ? 'BUTTON' : (id.includes('Dialog') ? 'DIALOG' : (id === 'body' ? 'BODY' : 'DIV')),
    get parentNode() { return parentNode; },
    set parentNode(p) { parentNode = p; },
    children,
    classList: {
      add: c => classSet.add(c),
      remove: c => classSet.delete(c),
      contains: c => classSet.has(c),
      toggle: (c, force) => {
        if (force === true) classSet.add(c);
        else if (force === false) classSet.delete(c);
        else if (classSet.has(c)) classSet.delete(c);
        else classSet.add(c);
        return classSet.has(c);
      }
    },
    setAttribute: (k, v) => {
      attrs.set(k, String(v));
      if (k === 'open') isOpen = true;
    },
    getAttribute: k => attrs.has(k) ? attrs.get(k) : null,
    removeAttribute: k => {
      attrs.delete(k);
      if (k === 'open') isOpen = false;
    },
    hasAttribute: k => attrs.has(k),
    addEventListener: (evt, handler) => {
      listeners[evt] = listeners[evt] || [];
      listeners[evt].push(handler);
    },
    dispatchEvent: evt => {
      const evtName = typeof evt === 'string' ? evt : evt.type;
      const targetEl = (typeof evt === 'object' && evt.target) ? evt.target : (elements.get(id) || el);
      const evObj = typeof evt === 'object' ? { target: targetEl, ...evt, preventDefault: () => {} } : { target: targetEl, type: evtName, preventDefault: () => {} };
      if (evtName === 'cancel' && el.tagName === 'DIALOG') {
        isOpen = false;
        attrs.delete('open');
      }
      if (typeof targetEl['on' + evtName] === 'function') {
        targetEl['on' + evtName](evObj);
      }
      (listeners[evtName] || []).forEach(h => h(evObj));
    },
    showModal: function() {
      isOpen = true;
      attrs.set('open', '');
    },
    close: function() {
      isOpen = false;
      attrs.delete('open');
      (listeners['close'] || []).forEach(h => h({ target: this, type: 'close' }));
    },
    focus: function() {
      mockDoc.activeElement = this;
    },
    getBoundingClientRect: function() {
      return this._rect || { left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100 };
    },
    contains: function(node) {
      if (!node) return false;
      if (node === this) return true;
      let curr = node.parentNode;
      while (curr) {
        if (curr === this) return true;
        curr = curr.parentNode;
      }
      return false;
    },
    closest: function(sel) {
      let curr = this;
      while (curr) {
        if (sel === 'dialog[open]' && curr.tagName === 'DIALOG' && curr.hasAttribute('open')) return curr;
        if (sel === 'dialog' && curr.tagName === 'DIALOG') return curr;
        if (sel === '[data-chart-detail]' && curr.dataset && curr.dataset.chartDetail) return curr;
        if (sel === '.cash-chart' && curr.classList.contains('cash-chart')) return curr;
        curr = curr.parentNode;
      }
      return null;
    },
    appendChild: function(child) {
      if (child.parentNode && typeof child.parentNode.removeChild === 'function') {
        child.parentNode.removeChild(child);
      }
      child.parentNode = this;
      children.push(child);
      return child;
    },
    removeChild: function(child) {
      const idx = children.indexOf(child);
      if (idx >= 0) children.splice(idx, 1);
      child.parentNode = null;
      return child;
    },
    replaceChildren: function() {
      children.length = 0;
    },
    onclick: null
  };
  return el;
}

const $ = id => {
  if (!elements.has(id)) {
    elements.set(id, createElement(id));
  }
  return elements.get(id);
};

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = (n, d = 0) => Number(n ?? 0).toLocaleString('en-IE', { maximumFractionDigits: d, minimumFractionDigits: d });
const fmt = (n, type = 'number') => n == null ? '—' : type === 'eur' ? '€' + nf(n) : type === 'eur2' ? '€' + nf(n, 2) : type === 'pct' ? nf(n * 100, 1) + '%' : type === 'lcoe' ? '€' + nf(n, 3) : type === 'years' ? nf(n, 1) + ' y' : nf(n);
const chartTarget = detail => `class="chart-point" tabindex="0" role="img" aria-label="${esc(detail)}" data-chart-detail="${esc(detail)}"`;
const chartSvg = (label, body, height = 285, width = 720) => `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}">${body}</svg>`;
const table = (headers, rows) => `<table><thead><tr>${headers.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(x => `<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const title = s => s.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

// Extract formatBatteryComposition and BATTERY_SETTINGS
const fmtStart = source.indexOf('function formatBatteryComposition');
const fmtEnd = source.indexOf('function batterySummaryText', fmtStart);
const fmtCode = source.slice(fmtStart, fmtEnd);

const batSetStart = source.indexOf('const BATTERY_SETTINGS=');
const batSetEnd = source.indexOf(';', batSetStart) + 1;
const batSetCode = source.slice(batSetStart, batSetEnd);

// Extract solar sizing section
const start = source.indexOf('/* SOLAR SIZING SECTION */');
assert(start >= 0, '/* SOLAR SIZING SECTION */ marker is present in app.js');
const end = source.indexOf('/* END SOLAR SIZING */', start);
assert(end > start, '/* END SOLAR SIZING */ marker is present in app.js');

// Extract tooltip section
const tipStart = source.indexOf('// One delegated tooltip survives chart redraws');
assert(tipStart >= 0, 'tooltip section is present in app.js');
const tipEnd = source.indexOf('function optimizerEquipment()', tipStart);
assert(tipEnd > tipStart, 'tooltip section end is present in app.js');
const tipCode = source.slice(tipStart, tipEnd);

// Extract getWeatherEnsembleMeta from app.js
const weatherMetaStart = source.indexOf('function getWeatherEnsembleMeta');
assert(weatherMetaStart >= 0, 'function getWeatherEnsembleMeta marker is present in app.js');
const weatherMetaEnd = source.indexOf('function schedule', weatherMetaStart);
assert(weatherMetaEnd > weatherMetaStart, 'function schedule marker is present in app.js');
const weatherMetaCode = source.slice(weatherMetaStart, weatherMetaEnd);

const docListeners = {};
const mockDoc = {
  activeElement: null,
  get body() { return $('body'); },
  createElement: tag => {
    const el = createElement(tag + '_' + Math.random().toString(36).slice(2, 7));
    el.tagName = tag.toUpperCase();
    return el;
  },
  addEventListener: (evt, h) => {
    docListeners[evt] = docListeners[evt] || [];
    docListeners[evt].push(h);
  },
  dispatchEvent: ev => {
    const evObj = typeof ev === 'string' ? { type: ev } : ev;
    (docListeners[evObj.type] || []).forEach(h => h(evObj));
  },
  querySelector: sel => {
    if (sel.startsWith('#')) return $(sel.slice(1));
    return null;
  },
  querySelectorAll: () => []
};

const mockWindow = {
  innerWidth: 390,
  innerHeight: 844,
  addEventListener: () => {}
};

const context = vm.createContext({
  $, esc, nf, fmt, chartTarget, chartSvg, table, title, console,
  setTimeout, clearTimeout,
  document: mockDoc,
  window: mockWindow,
  showError: err => { console.error('UI Error:', err); },
  queryAll: () => [],
  parsePercentInput: () => 5,
  syncSetting: () => {}, // Form-control synchronization is outside this chart-layout harness.
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  settings: {
    kwp: 6.0,
    battery_kwh: 5.0,
    horizon_years: 20,
    load_scenario: 'electrified'
  },
  current: {
    result: {
      layout: { kwp: 6.0 },
      settings: { battery_kwh: 5.0 },
      finance: {
        pv_incremental: { npv: { median: 8500 } },
        solar_equity: { npv: { median: 11200 } }
      }
    }
  }
});

vm.runInContext(batSetCode, context);
vm.runInContext(fmtCode, context);
vm.runInContext(weatherMetaCode, context);
vm.runInContext(tipCode, context);
vm.runInContext(source.slice(start, end), context);

(async () => {
  // Test 1: Empty or missing tradeoff matrix fallback
  vm.runInContext('renderSolarTradeoffHeatmap({})', context);
  assert.equal($('solarChart3').innerHTML, '<p class="muted">No tradeoff matrix available.</p>', 'Empty object triggers fallback message');

  vm.runInContext('renderSolarTradeoffHeatmap({ tradeoff_matrix: { cells: [] } })', context);
  assert.equal($('solarChart3').innerHTML, '<p class="muted">No tradeoff matrix available.</p>', 'Empty cells triggers fallback message');

  // Test 2: Small matrix (2 PV x 2 Battery) scales dimensions intrinsically
  const smallFixture = {
    weather_count: 19,
    tradeoff_matrix: {
      pv_axis_kwp: [3.0, 6.0],
      battery_axis_kwh: [0.0, 5.0],
      cells: [
        { pv_kwp: 3.0, battery_kwh: 0.0, npv_median: 3500, npv_min: 3000, npv_max: 4000, capex_eur: 4500 },
        { pv_kwp: 3.0, battery_kwh: 5.0, npv_median: 5000, npv_min: 4400, npv_max: 5600, capex_eur: 7500 },
        { pv_kwp: 6.0, battery_kwh: 0.0, npv_median: 6000, npv_min: 5200, npv_max: 6800, capex_eur: 8000 },
        { pv_kwp: 6.0, battery_kwh: 5.0, npv_median: 8500, npv_min: 7600, npv_max: 9400, capex_eur: 11000 }
      ]
    },
    recommended_candidate: { kwp: 6.0, battery_kwh: 5.0, npv_median: 8500 },
    recommendation_choices: {
      balanced: { kwp: 6.0, battery_kwh: 5.0, npv_median: 8500 },
      financial: { kwp: 6.0, battery_kwh: 5.0, npv_median: 8500 }
    },
    search_summary: { exact: true }
  };
  context.smallFixture = smallFixture;
  vm.runInContext('renderSolarTradeoffHeatmap(smallFixture)', context);

  const smallHtml = $('solarChart3').innerHTML;
  assert.match(smallHtml, /<svg[^>]*width="306"[^>]*height="182"/, 'Small grid SVG sets intrinsic width & height');
  assert.match(smallHtml, /viewBox="0 0 306 182"/, 'viewBox matches computed dimensions');
  assert.match(smallHtml, /style="min-width:306px;width:306px;height:182px;"/, 'Inline style enforces intrinsic dimensions');
  assert.match(smallHtml, /width="92"\s+height="40"/, 'Cell dimensions are ~92x40 (readable, within 85-110w x 40-48h)');
  assert.match(smallHtml, /font-size="13"/, 'Cell text uses readable 13px font');
  assert.match(smallHtml, /€8\.5k/, 'Cell displays formatted median NPV');
  assert.match(smallHtml, /Basis: Discounted Project NPV/, 'Default Project NPV basis label rendered');

  // Test 3: Large matrix (15 PV x 10 Battery) scales without row-height cap
  const pvs15 = Array.from({ length: 15 }, (_, i) => +(3.0 + i * 0.8).toFixed(1));
  const bats10 = Array.from({ length: 10 }, (_, j) => +(j * 2.5).toFixed(1));
  const cellsLarge = [];
  pvs15.forEach(p => {
    bats10.forEach(b => {
      cellsLarge.push({
        pv_kwp: p,
        battery_kwh: b,
        npv_median: Math.round(3000 + p * 800 + b * 400),
        npv_min: Math.round(2500 + p * 700 + b * 350),
        npv_max: Math.round(3500 + p * 900 + b * 450),
        capex_eur: Math.round(4000 + p * 900 + b * 500)
      });
    });
  });
  const largeFixture = {
    tradeoff_matrix: {
      pv_axis_kwp: pvs15,
      battery_axis_kwh: bats10,
      cells: cellsLarge
    },
    recommended_candidate: { kwp: pvs15[7], battery_kwh: bats10[4], npv_median: 12000 },
    recommendation_choices: {
      balanced: { kwp: pvs15[7], battery_kwh: bats10[4], npv_median: 12000 }
    },
    search_summary: { exact: false }
  };
  context.largeFixture = largeFixture;
  vm.runInContext('renderSolarTradeoffHeatmap(largeFixture)', context);

  const largeHtml = $('solarChart3').innerHTML;
  // 10 columns: plotW = 960, right = 1050, width = 1074
  // 15 rows: plotH = 660, bottom = 716, height = 754
  assert.match(largeHtml, /<svg[^>]*width="1074"[^>]*height="754"/, 'Large grid scales width to 1074 and height to 754');
  assert.match(largeHtml, /viewBox="0 0 1074 754"/, 'Large grid viewBox scales fully without 420 cap');
  assert.match(largeHtml, /style="min-width:1074px;width:1074px;height:754px;"/, 'Large grid style sets min-width to prevent shrinking');

  // Test 4: Financial basis labels (Project NPV vs Equity NPV)
  const equityFixture = {
    ...smallFixture,
    options: { financial_basis: 'after_financing' }
  };
  context.equityFixture = equityFixture;
  vm.runInContext('renderSolarTradeoffHeatmap(equityFixture)', context);
  const equityHtml = $('solarChart3').innerHTML;
  assert.match(equityHtml, /Basis: Discounted Equity NPV \(mean\)/, 'Equity basis label rendered in chart');
  assert.match(equityHtml, /discounted equity NPV across 19 weather years/, 'SVG aria-label specifies equity basis');
  assert.match($('solarTable3').innerHTML, /Discounted Equity NPV \(mean\)/, 'Table 3 header shows Equity NPV');

  // Test 5: Weather min-max tooltips, composition, and markers
  assert.match(equityHtml, /weather: €3,000 – €4,000/, 'Cell detail includes full weather min-max');
  assert.match(equityHtml, /Battery: 0 kWh/, 'Cell detail includes formatted battery composition');
  assert.match(equityHtml, /★/, 'Recommended marker star rendered in cell');
  assert.match(equityHtml, /●/, 'Current configuration marker circle rendered');
  assert.match(equityHtml, /stroke="#2b6cb0"/, 'Current active configuration has blue highlight stroke');

  // Test 6: Out-of-grid note when active system is outside evaluated matrix
  const outGridFixture = {
    ...smallFixture,
    tradeoff_matrix: {
      pv_axis_kwp: [12.0, 15.0],
      battery_axis_kwh: [15.0, 20.0],
      cells: [
        { pv_kwp: 12.0, battery_kwh: 15.0, npv_median: 14000, npv_min: 12000, npv_max: 16000, capex_eur: 18000 },
        { pv_kwp: 15.0, battery_kwh: 20.0, npv_median: 17000, npv_min: 15000, npv_max: 19000, capex_eur: 23000 }
      ]
    }
  };
  context.outGridFixture = outGridFixture;
  vm.runInContext('renderSolarTradeoffHeatmap(outGridFixture)', context);
  assert.match($('solarChart3').innerHTML, /lies outside the evaluated matrix range \(12–15 kWp, 15–20 kWh\)/, 'Out-of-grid note accurately notes placement');

  // Test 7: Expanded view dialog and button interaction
  const expandBtn = $('solarHeatmapExpandBtn');
  const dialog = $('solarHeatmapDialog');
  const closeBtn = $('solarHeatmapDialogClose');
  const dialogContent = $('solarHeatmapDialogContent');

  assert(expandBtn, 'Expand button exists');
  assert(dialog, 'Dialog exists');
  assert(closeBtn, 'Close button exists');

  // Initialize controls
  vm.runInContext('initSolarSizingControls()', context);

  // Configure dialog bounding rect (matching mobile 390x844 modal)
  dialog._rect = { left: 8, top: 34, right: 382, bottom: 810, width: 374, height: 776 };

  // Click expand button
  expandBtn.dispatchEvent('click');
  assert.equal(expandBtn.getAttribute('aria-expanded'), 'true', 'aria-expanded set to true on open');
  assert(dialog.hasAttribute('open'), 'dialog is open');
  assert.match(dialogContent.innerHTML, /<svg[^>]*width=/, 'Heatmap is rendered into dialog content');

  // Retargeted click inside dialog (tap on cell produces click targeted to dialog at inside coordinates)
  // Inside coordinates: x=350, y=750 (inside dialog rect left=8, top=34, right=382, bottom=810)
  dialog.dispatchEvent({ type: 'click', target: dialog, clientX: 350, clientY: 750 });
  mockDoc.dispatchEvent({ type: 'click', target: dialog, clientX: 350, clientY: 750 });
  assert.equal(dialog.hasAttribute('open'), true, 'Dialog remains open on retargeted click inside dialog bounds');

  // True outside backdrop click: x=2, y=10 (outside dialog rect)
  dialog.dispatchEvent({ type: 'click', target: dialog, clientX: 2, clientY: 10 });
  assert.equal(dialog.hasAttribute('open'), false, 'Dialog closes on true outside backdrop click');
  assert.equal(expandBtn.getAttribute('aria-expanded'), 'false', 'aria-expanded set to false on backdrop close');
  assert.equal(context.document.activeElement, expandBtn, 'Focus restored to expand button on backdrop close');

  // Re-open dialog for close button test
  expandBtn.dispatchEvent('click');
  assert.equal(dialog.hasAttribute('open'), true, 'Dialog re-opened');

  // Click close button
  closeBtn.dispatchEvent('click');
  assert.equal(expandBtn.getAttribute('aria-expanded'), 'false', 'aria-expanded set to false on close');
  assert.equal(dialog.hasAttribute('open'), false, 'dialog is closed');
  assert.equal(context.document.activeElement, expandBtn, 'Focus restored to expand button on close');

  // Test 7b: Touch cell interaction and tooltip persistence across retargeted tap
  expandBtn.dispatchEvent('click');
  assert.equal(dialog.hasAttribute('open'), true, 'Dialog open for touch test');

  // Create a mock chart point cell inside dialog
  const testCell = createElement('testCellPoint');
  testCell.dataset.chartDetail = 'PV: 9.0 kWp · Battery: 20 kWh\nMedian Discounted Equity NPV: €6,498 (weather: €5,426 – €7,039)';
  testCell._rect = { left: 280, top: 720, right: 370, bottom: 760, width: 90, height: 40 };
  dialog.appendChild(testCell);

  // Focus cell (as happens on pointerdown / touchstart)
  mockDoc.dispatchEvent({ type: 'focusin', target: testCell });
  const chartTip = vm.runInContext('chartTooltip', context);
  assert(chartTip, 'Chart tooltip exists after focusin');
  assert.equal(chartTip.hidden, false, 'Tooltip is visible on focusin');
  assert.match(chartTip.textContent, /20 kWh/, 'Tooltip contains cell details');
  assert.equal(chartTip.parentNode, dialog, 'Tooltip appended to open dialog');

  // Tap completes: browser emits click retargeted to dialog at cell coords
  mockDoc.dispatchEvent({ type: 'click', target: dialog, clientX: 320, clientY: 740 });
  dialog.dispatchEvent({ type: 'click', target: dialog, clientX: 320, clientY: 740 });
  assert.equal(dialog.hasAttribute('open'), true, 'Dialog stays OPEN after retargeted tap');
  assert.equal(chartTip.hidden, false, 'Tooltip remains VISIBLE after retargeted tap');
  assert.match(chartTip.textContent, /20 kWh/, 'Tooltip detail preserved after retargeted tap');

  // True backdrop click dismisses dialog and hides tooltip
  mockDoc.dispatchEvent({ type: 'click', target: dialog, clientX: 2, clientY: 10 });
  dialog.dispatchEvent({ type: 'click', target: dialog, clientX: 2, clientY: 10 });
  assert.equal(dialog.hasAttribute('open'), false, 'Dialog closed on backdrop tap');
  assert.equal(chartTip.hidden, true, 'Tooltip hidden on backdrop close');
  assert.equal(context.document.activeElement, expandBtn, 'Focus restored to expand button');

  // Re-open and verify Escape key dismisses dialog and hides tooltip
  expandBtn.dispatchEvent('click');
  mockDoc.dispatchEvent({ type: 'focusin', target: testCell });
  assert.equal(chartTip.hidden, false, 'Tooltip visible before Escape');
  mockDoc.dispatchEvent({ type: 'keydown', key: 'Escape' });
  assert.equal(chartTip.hidden, true, 'Tooltip hidden on Escape');
  dialog.dispatchEvent({ type: 'cancel', target: dialog });
  assert.equal(dialog.hasAttribute('open'), false, 'Dialog closed on Escape/cancel');
  await new Promise(r => setTimeout(r, 10));
  assert.equal(context.document.activeElement, expandBtn, 'Focus restored on Escape');

  // Test 8: Keyboard/touch accessible data-chart-detail on cells
  assert.match(dialogContent.innerHTML, /data-chart-detail="PV: 12 kWp/, 'Cells have data-chart-detail attribute');
  assert.match(dialogContent.innerHTML, /tabindex="0"/, 'Cells are keyboard focusable (tabindex="0")');
  assert.match(dialogContent.innerHTML, /class="chart-point"/, 'Cells have chart-point class');

  // Test 9: CSS cascade regression verification
  const cssContent = fs.readFileSync(path.join(__dirname, '../feasibility/web/style.css'), 'utf8');
  assert(cssContent.includes('.chart svg{width:100%;height:auto;max-height:270px}'), 'Global .chart svg specifies max-height:270px');
  
  // Verify scoped rule overrides max-height: none with ID specificity
  const scopedSvgMatch = cssContent.match(/#solarChart3(?:\.heatmap-chart)?\s+svg[^\{]*\{([^}]+)\}/s);
  assert(scopedSvgMatch, 'Scoped SVG rule for #solarChart3 exists in style.css');
  const scopedSvgBody = scopedSvgMatch[1];
  assert.match(scopedSvgBody, /max-height:\s*none/, 'Scoped #solarChart3 svg rule explicitly declares max-height: none');
  assert.match(scopedSvgBody, /min-width:\s*max-content/, 'Scoped SVG rule preserves intrinsic min-width');

  // Verify dialog open/closed cascade
  assert.match(cssContent, /dialog#solarHeatmapDialog:not\(\[open\]\)\s*\{[^}]*display:\s*none\s*!important/s, 'Closed dialog is guaranteed hidden');
  assert.match(cssContent, /dialog#solarHeatmapDialog\[open\]\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column/s, 'Open dialog is flexible column');
  const bodyRuleMatch = cssContent.match(/dialog#solarHeatmapDialog\s+\.heatmap-dialog-body\s*\{([^}]+)\}/s);
  assert(bodyRuleMatch, 'Dialog body rule exists in style.css');
  assert.match(bodyRuleMatch[1], /flex:\s*1/, 'Dialog body uses flex: 1');
  assert.match(bodyRuleMatch[1], /min-height:\s*0/, 'Dialog body specifies min-height: 0');
  assert.match(bodyRuleMatch[1], /max-height:\s*none/, 'Dialog body specifies max-height: none');
  assert.match(bodyRuleMatch[1], /overflow:\s*auto/, 'Dialog body specifies overflow: auto');

  // Verify responsive wrapping HTML styles
  assert.match(cssContent, /\.solar-heatmap-meta\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap/s, 'Heatmap meta row uses flex-wrap: wrap');
  assert.match(cssContent, /\.solar-heatmap-legend\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap/s, 'Heatmap legend uses flex-wrap: wrap');

  // Test 10: 1-column battery fixture (fixed battery sizing edge case)
  const oneColFixture = {
    tradeoff_matrix: {
      pv_axis_kwp: [3.0, 6.0, 9.0],
      battery_axis_kwh: [10.0],
      cells: [
        { pv_kwp: 3.0, battery_kwh: 10.0, npv_median: 4500, npv_min: 3800, npv_max: 5200, capex_eur: 8000 },
        { pv_kwp: 6.0, battery_kwh: 10.0, npv_median: 7800, npv_min: 6900, npv_max: 8700, capex_eur: 11500 },
        { pv_kwp: 9.0, battery_kwh: 10.0, npv_median: 9200, npv_min: 8100, npv_max: 10300, capex_eur: 15000 }
      ]
    },
    recommended_candidate: { kwp: 9.0, battery_kwh: 10.0, npv_median: 9200 },
    recommendation_choices: {
      balanced: { kwp: 9.0, battery_kwh: 10.0, npv_median: 9200 }
    },
    search_summary: { exact: true, battery_mode: 'fixed' },
    options: { financial_basis: 'after_financing' }
  };
  context.oneColFixture = oneColFixture;
  vm.runInContext('renderSolarTradeoffHeatmap(oneColFixture)', context);

  const oneColHtml = $('solarChart3').innerHTML;
  // Caption and basis in responsive wrapping HTML, not overlapping inside SVG
  assert.match(oneColHtml, /class="solar-heatmap-meta"/, '1-col fixture renders responsive meta container');
  assert.match(oneColHtml, /class="solar-heatmap-caption">Battery storage \(kWh\) →<\/span>/, 'Caption rendered in HTML span');
  assert.match(oneColHtml, /class="solar-heatmap-basis">Basis: Discounted Equity NPV \(mean\)<\/span>/, 'Basis rendered in HTML span');
  
  // Legend in responsive wrapping HTML, not clipped outside narrow SVG viewBox
  assert.match(oneColHtml, /class="solar-heatmap-legend"/, '1-col fixture renders responsive legend container');
  assert.match(oneColHtml, /★.*Best candidate/, 'Legend contains recommended marker');
  assert.match(oneColHtml, /●.*Current system configuration/, 'Legend contains current configuration marker');
  assert.match(oneColHtml, /Darker green = Higher discounted equity NPV/, 'Legend contains color ramp explanation');

  // Axis labels inside SVG are intact
  assert.match(oneColHtml, />PV \(kWp\)<\/text>/, 'PV (kWp) axis label rendered in SVG');
  assert.match(oneColHtml, />10\.0 kWh<\/text>/, 'Battery column tick rendered in SVG');
  assert.match(oneColHtml, />9\.0 kWp<\/text>/, 'PV row tick rendered in SVG');

  // Cell values and tooltips
  assert.match(oneColHtml, /€9\.2k/, 'Cell displays formatted median NPV');
  assert.match(oneColHtml, /weather: €8,100 – €10,300/, 'Tooltip includes full weather min-max range');

  // Test 11: 3-column battery fixture
  const threeColFixture = {
    tradeoff_matrix: {
      pv_axis_kwp: [3.0, 6.0],
      battery_axis_kwh: [0.0, 5.0, 10.0],
      cells: [
        { pv_kwp: 3.0, battery_kwh: 0.0, npv_median: 3000, npv_min: 2500, npv_max: 3500, capex_eur: 4500 },
        { pv_kwp: 3.0, battery_kwh: 5.0, npv_median: 4500, npv_min: 4000, npv_max: 5000, capex_eur: 7500 },
        { pv_kwp: 3.0, battery_kwh: 10.0, npv_median: 5500, npv_min: 4800, npv_max: 6200, capex_eur: 10500 },
        { pv_kwp: 6.0, battery_kwh: 0.0, npv_median: 6000, npv_min: 5200, npv_max: 6800, capex_eur: 8000 },
        { pv_kwp: 6.0, battery_kwh: 5.0, npv_median: 8000, npv_min: 7100, npv_max: 8900, capex_eur: 11000 },
        { pv_kwp: 6.0, battery_kwh: 10.0, npv_median: 9500, npv_min: 8500, npv_max: 10500, capex_eur: 14000 }
      ]
    },
    recommended_candidate: { kwp: 6.0, battery_kwh: 10.0, npv_median: 9500 },
    recommendation_choices: {
      balanced: { kwp: 6.0, battery_kwh: 10.0, npv_median: 9500 }
    },
    search_summary: { exact: true }
  };
  context.threeColFixture = threeColFixture;
  vm.runInContext('renderSolarTradeoffHeatmap(threeColFixture)', context);

  const threeColHtml = $('solarChart3').innerHTML;
  assert.match(threeColHtml, /class="solar-heatmap-meta"/, '3-col fixture renders responsive meta container');
  assert.match(threeColHtml, /class="solar-heatmap-legend"/, '3-col fixture renders responsive legend container');
  assert.match(threeColHtml, /Basis: Discounted Project NPV \(mean\)/, 'Basis label rendered');

  console.log('Solar tradeoff heatmap readable layout & interaction tests passed successfully.');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
