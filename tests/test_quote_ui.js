/* Run with Node: test UI pricing description, cost source indicators, and quote controls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

const elements = new Map();
const $ = id => {
  if (!elements.has(id)) {
    const classSet = new Set();
    const listeners = {};
    elements.set(id, {
      id,
      innerHTML: '',
      textContent: '',
      style: {},
      querySelector: () => null,
      value: '',
      disabled: false,
      hidden: false,
      options: [],
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
      addEventListener: (evt, handler) => {
        listeners[evt] = listeners[evt] || [];
        listeners[evt].push(handler);
      },
      insertAdjacentHTML(pos, html) {
        this.innerHTML += html;
      },
      dispatchEvent: evtName => {
        (listeners[evtName] || []).forEach(h => h({ target: elements.get(id) }));
      },
      onclick: null
    });
  }
  return elements.get(id);
};

const domNodes = [];
const mockDocument = {
  getElementById: $,
  querySelector: () => null,
  addEventListener: () => {},
  querySelectorAll: selector => {
    if (selector === '[data-quote]') return domNodes;
    return [];
  }
};

const context = vm.createContext({
  $,
  document: mockDocument,
  window: { addEventListener: () => {} },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  settings: {},
  quote: { line_items: [] },
  current: null,
  schema: { defaults: {}, records: [] },
  schedule: () => {},
  fmt: (n, type = 'number') => n == null ? '—' : type === 'eur' ? '€' + Number(n).toLocaleString('en-IE', { maximumFractionDigits: 0 }) : String(n),
  title: s => s.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()),
  range: r => typeof r === 'object' && r ? `${r.min} – ${r.max}` : String(r ?? '—'),
  displayCashRows: r => r || [],
  cashFlowLabel: () => '',
  renderCashChart: () => {},
  renderEnergyCharts: () => {},
  table: () => '',
  esc: s => s,
  renderSaved: () => {},
  renderLoanPanel: () => {},
  renderRoofLimitsComparison: () => {}
});

// Run up to renderSaved to acquire helper definitions
vm.runInContext(source.slice(0, source.indexOf('function onePage(')), context);

const setContextState = state => {
  if (state.settings !== undefined) {
    context.settings = state.settings;
    vm.runInContext(`settings = ${JSON.stringify(state.settings)};`, context);
  }
  if (state.quote !== undefined) {
    context.quote = state.quote;
    vm.runInContext(`quote = ${JSON.stringify(state.quote)};`, context);
  }
  if (state.current !== undefined) {
    context.current = state.current;
    vm.runInContext(`current = ${JSON.stringify(state.current)};`, context);
  }
};

// Test 1: buildQuoteFields creates price basis options with accurate descriptions
$('quoteFields').innerHTML = '';
setContextState({ quote: { price_basis: 'modeled', line_items: [] } });
vm.runInContext('buildQuoteFields();', context);
const quoteFieldsHtml = $('quoteFields').innerHTML;
assert.ok(quoteFieldsHtml.includes('quotePriceBasisHelp'), 'buildQuoteFields contains quotePriceBasisHelp');
assert.ok(quoteFieldsHtml.includes('Modeled sizing / manual quote'), 'buildQuoteFields clarifies modeled option for manual quote');
assert.ok(quoteFieldsHtml.includes('Itemized contractor quote'), 'buildQuoteFields includes itemized option');

// Test 2: Modeled mode + manual quote 16000 labels active cost as manual quote
setContextState({
  settings: { quote_total: 16000 },
  quote: { price_basis: 'modeled', line_items: [] },
  current: {
    itemized_total_eur: null,
    result: { finance: { capex_eur: 16000 } }
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('manual turnkey quote'), 'Price basis help explains manual quote is active in modeled mode');
assert.ok($('quoteTotal').innerHTML.includes('Manual quote'), 'quoteTotal displays Manual quote tag');
assert.ok($('quoteTotal').innerHTML.includes('16,000'), 'quoteTotal displays €16,000 amount');

// Test 3: Modeled mode + null manual quote labels active cost as modeled benchmark
setContextState({
  settings: { quote_total: null },
  quote: { price_basis: 'modeled', line_items: [] },
  current: {
    itemized_total_eur: null,
    result: { finance: { capex_eur: 20745 } }
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('modeled benchmark formula'), 'Price basis help explains modeled benchmark formula is active');
assert.ok($('quoteTotal').innerHTML.includes('Modeled benchmark'), 'quoteTotal displays Modeled benchmark tag');

// Test 4: Itemized mode with populated lines labels active cost as itemized lines
setContextState({
  settings: { quote_total: 16000 },
  quote: { price_basis: 'itemized', line_items: [{ net_eur: 10000 }] },
  current: {
    itemized_total_eur: 11000,
    result: { finance: { capex_eur: 11000 } }
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('Itemized lines determine the active system cost'), 'Price basis help explains itemized lines determine cost');
assert.ok($('quoteTotal').innerHTML.includes('Active system cost'), 'quoteTotal displays Active system cost tag');
assert.ok($('quoteTotal').innerHTML.includes('11,000'), 'quoteTotal displays itemized €11,000 amount');

// Test 5: Modeled mode with retained populated lines keeps lines as reference and uses manual quote
setContextState({
  settings: { quote_total: 16000 },
  quote: { price_basis: 'modeled', line_items: [{ net_eur: 10000 }] },
  current: {
    itemized_total_eur: 11000,
    result: { finance: { capex_eur: 16000 } }
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quoteTotal').innerHTML.includes('Reference only'), 'quoteTotal displays Reference only tag for itemized lines');
assert.ok($('quoteTotal').innerHTML.includes('manual turnkey quote'), 'quoteTotal confirms active cost is manual turnkey quote');
assert.ok($('quoteTotal').innerHTML.includes('16,000'), 'quoteTotal confirms manual quote amount €16,000');

// Test 6: Comparisons capex label reflects manual quote vs modeled benchmark vs itemized quote
const makeRenderResult = capex => ({
  layout: { kwp: 6.0, panel_count: 14, theoretical: false },
  settings: { load_scenario: 'baseline', battery_kwh: 5.0, inverter_kw: 6.0, cashflow_mode: 'nominal', loan_years: 10 },
  metrics: { npv_eur: { min: 1000, max: 2000 }, payback_years: 6, discounted_payback_years: 8, autarky: 0.7, irr: 0.12, lcoe_eur_kwh: 0.08, package_npv_eur: { min: 1000, max: 2000 }, equity_npv_eur: { min: 1000, max: 2000 } },
  finance: {
    capex_eur: capex,
    conversion_capex_eur: 0,
    annual_deduction_eur: 800,
    nominal_discount_rate: 0.05,
    real_discount_rate: 0.03,
    pv_incremental: { unpaid_weather_years: 0, unpaid_discounted_weather_years: 0, irr_multiple_roots: false },
    whole_package: { simple_payback: null, discounted_payback: null }
  },
  energy: { load_kwh: 3000, pv_kwh: 6000, direct_kwh: 2000, discharge_kwh: 1500, grid_import_kwh: 1000, export_kwh: 2500, battery_loss_kwh: 100, clipped_kwh: 50, self_consumption: 0.6, useful_self_consumption: 0.6 },
  annual_cashflows: [],
  limitations: [],
  assumptions: []
});

setContextState({
  settings: { quote_total: 16000 },
  quote: { price_basis: 'modeled', line_items: [] },
  current: {
    itemized_total_eur: null,
    claims: [],
    warnings: [],
    result: makeRenderResult(16000)
  }
});
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(manual quote)'), 'Comparisons shows (manual quote) when manual quote is active');

setContextState({
  settings: { quote_total: null },
  quote: { price_basis: 'modeled', line_items: [] },
  current: {
    itemized_total_eur: null,
    claims: [],
    warnings: [],
    result: makeRenderResult(20745)
  }
});
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(modeled benchmark)'), 'Comparisons shows (modeled benchmark) when quote_total is null');

setContextState({
  settings: { quote_total: 16000 },
  quote: { price_basis: 'itemized', line_items: [{ net_eur: 10000 }] },
  current: {
    itemized_total_eur: 11000,
    claims: [],
    warnings: [],
    result: makeRenderResult(11000)
  }
});
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(itemized quote)'), 'Comparisons shows (itemized quote) when itemized lines are active');

// Test 7: Itemized mode with use_itemized === false keeps lines as reference only
// 7a: with manual quote 16000
setContextState({
  settings: { quote_total: 16000 },
  quote: { price_basis: 'itemized', use_itemized: false, line_items: [{ net_eur: 10000 }] },
  current: {
    itemized_total_eur: 11000,
    claims: [],
    warnings: [],
    result: makeRenderResult(16000)
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('Itemized lines kept as reference only; active system cost uses manual turnkey quote.'), 'Help text shows manual turnkey quote when use_itemized is false');
assert.ok($('quoteTotal').innerHTML.includes('Reference only'), 'quoteTotal shows Reference only when use_itemized is false');
assert.ok($('quoteTotal').innerHTML.includes('16,000'), 'quoteTotal shows manual quote amount 16,000');
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(manual quote)'), 'Comparisons shows (manual quote) when use_itemized is false with manual quote');

// 7b: with manual quote 0
setContextState({
  settings: { quote_total: 0 },
  quote: { price_basis: 'itemized', use_itemized: false, line_items: [{ net_eur: 10000 }] },
  current: {
    itemized_total_eur: 11000,
    claims: [],
    warnings: [],
    result: makeRenderResult(0)
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('active system cost uses manual turnkey quote'), 'Help text shows manual turnkey quote when use_itemized is false and manual quote is 0');
assert.ok($('quoteTotal').innerHTML.includes('Reference only'), 'quoteTotal shows Reference only tag when manual quote is 0');
assert.ok($('quoteTotal').innerHTML.includes('€0'), 'quoteTotal shows active cost €0');
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(manual quote)'), 'Comparisons shows (manual quote) when use_itemized is false and manual quote is 0');

// 7c: with null manual quote
setContextState({
  settings: { quote_total: null },
  quote: { price_basis: 'itemized', use_itemized: false, line_items: [{ net_eur: 10000 }] },
  current: {
    itemized_total_eur: 11000,
    claims: [],
    warnings: [],
    result: makeRenderResult(20745)
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('active system cost uses modeled benchmark formula'), 'Help text shows modeled benchmark formula when use_itemized is false and quote_total is null');
assert.ok($('quoteTotal').innerHTML.includes('Reference only (Modeled benchmark formula active)'), 'quoteTotal shows benchmark formula active tag');
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(modeled benchmark)'), 'Comparisons shows (modeled benchmark) when use_itemized is false and quote_total is null');
// Test 8: Blank draft lines are not treated as populated offers
setContextState({
  settings: { quote_total: null },
  quote: { price_basis: 'itemized', line_items: [{ description: '', quantity: 1, net_eur: null, vat: 0.1 }] },
  current: {
    itemized_total_eur: null,
    claims: [],
    warnings: [],
    result: makeRenderResult(20745)
  }
});
vm.runInContext('syncQuotePricingDescription();', context);
assert.ok($('quotePriceBasisHelp').textContent.includes('No itemized lines entered'), 'Help text warns that no itemized lines are entered');
assert.ok($('quotePriceBasisHelp').textContent.includes('modeled benchmark formula'), 'Help text explains fallback to modeled benchmark formula');
assert.ok($('quoteTotal').innerHTML.includes('Modeled benchmark'), 'quoteTotal displays Modeled benchmark tag when draft line is blank');
vm.runInContext('render();', context);
assert.ok($('comparisons').innerHTML.includes('(modeled benchmark)'), 'Comparisons shows (modeled benchmark) when itemized lines are only blank drafts');

console.log('Quote pricing description and cost source UI tests passed successfully.');
