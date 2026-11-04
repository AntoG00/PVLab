'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const SharedScenarioSync = require('../feasibility/web/sync.js');
const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

class MockBlob {
  constructor(parts, options = {}) {
    this.parts = parts || [];
    this.type = options.type || '';
    this.size = this.parts.reduce((acc, p) => acc + (typeof p === 'string' ? Buffer.byteLength(p) : (p?.length || 0)), 0);
  }
  async text() {
    return this.parts.map(p => (typeof p === 'string' ? p : p.toString())).join('');
  }
}

function createExportHarness() {
  const elements = new Map();
  const clickedDownloads = [];
  const createdObjectUrls = [];
  const revokedObjectUrls = [];
  const blobUrlMap = new Map();
  let blobCounter = 1;

  function makeEl(tag = 'div', id = '') {
    const classes = new Set();
    const attrs = new Map();
    const listeners = new Map();
    const children = [];
    let val = '';
    let isChecked = false;
    let txt = '';
    let html = '';

    const el = {
      tagName: tag.toUpperCase(),
      id: id || '',
      type: tag === 'input' ? 'text' : '',
      style: {},
      dataset: {},
      options: [],
      children,
      get className() { return Array.from(classes).join(' '); },
      set className(v) {
        classes.clear();
        if (v) String(v).split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
      },
      classList: {
        add(c) { classes.add(c); },
        remove(c) { classes.delete(c); },
        contains(c) { return classes.has(c); },
        toggle(c, force) {
          if (force === undefined) {
            if (classes.has(c)) classes.delete(c); else classes.add(c);
          } else if (force) {
            classes.add(c);
          } else {
            classes.delete(c);
          }
          return classes.has(c);
        }
      },
      hidden: false,
      disabled: false,
      get value() { return val; },
      set value(v) { val = String(v ?? ''); },
      get checked() { return isChecked; },
      set checked(v) { isChecked = Boolean(v); },
      get textContent() { return txt; },
      set textContent(v) { txt = String(v ?? ''); },
      get innerHTML() { return html; },
      set innerHTML(v) { html = String(v ?? ''); },
      insertAdjacentHTML(position, text) { html += String(text ?? ''); },
      addEventListener(event, fn) {
        if (!listeners.has(event)) listeners.set(event, []);
        listeners.get(event).push(fn);
      },
      removeEventListener(event, fn) {
        if (listeners.has(event)) {
          listeners.set(event, listeners.get(event).filter(f => f !== fn));
        }
      },
      dispatchEvent(event) {
        const list = listeners.get(event.type) || [];
        list.forEach(fn => fn(event));
      },
      setAttribute(k, v) { attrs.set(k, String(v)); },
      getAttribute(k) { return attrs.get(k) ?? null; },
      hasAttribute(k) { return attrs.has(k); },
      removeAttribute(k) { attrs.delete(k); },
      querySelector(sel) {
        if (sel.startsWith('#')) return elements.get(sel.slice(1)) || null;
        return null;
      },
      querySelectorAll() { return []; },
      prepend(...nodes) {
        nodes.forEach(node => {
          children.unshift(node);
          if (node && typeof node === 'object') node.parentElement = this;
        });
      },
      append(...nodes) {
        nodes.forEach(node => {
          children.push(node);
          if (node && typeof node === 'object') node.parentElement = this;
        });
      },
      appendChild(node) {
        children.push(node);
        if (node && typeof node === 'object') node.parentElement = this;
        return node;
      },
      insertBefore(newNode, refNode) {
        const idx = children.indexOf(refNode);
        if (idx !== -1) {
          children.splice(idx, 0, newNode);
        } else {
          children.unshift(newNode);
        }
        if (newNode && typeof newNode === 'object') newNode.parentElement = this;
        return newNode;
      },
      removeChild(node) {
        const idx = children.indexOf(node);
        if (idx !== -1) children.splice(idx, 1);
        return node;
      },
      remove() {
        if (this.parentElement && this.parentElement.children) {
          const idx = this.parentElement.children.indexOf(this);
          if (idx !== -1) this.parentElement.children.splice(idx, 1);
        }
      },
      closest(sel) {
        return makeEl(sel || 'div');
      },
      click() {
        if (tag.toLowerCase() === 'a') {
          clickedDownloads.push({
            href: el.href,
            download: el.download,
            blob: blobUrlMap.get(el.href),
            rawBlob: blobUrlMap.get(el.href)
          });
        }
        if (listeners.has('click')) {
          const list = listeners.get('click') || [];
          list.forEach(fn => fn({ type: 'click', target: el }));
        } else if (typeof el.onclick === 'function') {
          el.onclick({ type: 'click', target: el });
        }
      }
    };
    if (id) elements.set(id, el);
    return el;
  }

  // Pre-populate standard DOM elements
  const requiredElementIds = [
    'download', 'onepage', 'error', 'status', 'latency', 'controls',
    'systemTitle', 'systemTags', 'metrics', 'unconfirmedAssumptionsBadge',
    'moneyBasisSelect', 'energyBalance', 'comparisons', 'cashFlowDescription',
    'cashTable', 'method', 'limitations', 'claims', 'syncStatus', 'defaultConfirm',
    'reloadServer', 'search', 'dayhourMode', 'dayhourPeriod', 'dayhourMonth',
    'dayhourScenario', 'weatherRepresentative', 'weatherYear', 'weatherDate',
    'hourSlider', 'previousHour', 'nextHour', 'reset', 'save', 'clearSaved',
    'loadFile', 'cashFlowBasis', 'addLine', 'loanModeControl', 'loanAmountControl',
    'loanFractionControl', 'loanAprControl', 'loanYearsControl', 'loanMaxMonthlyPaymentControl',
    'loanInflationControl', 'loanDiscountControl', 'loanScopeControl', 'solveLoanAmount',
    'saveLoanSimulation', 'loanSaveName', 'loanSaveStatus', 'fairMetric', 'fairTarget',
    'solve', 'fairResult', 'chartX', 'chartY', 'recomputePareto', 'fullStudy',
    'jobState', 'paretoMethod', 'quoteFields', 'quoteLines', 'calibration',
    'roofCheck', 'dataNotes', 'market', 'configVsRecContent', 'exportValuationCards',
    'exportSensitivityComparison', 'exportValuationNote', 'activeTabAssumptionsPill',
    'batterySizingContent', 'solarSizingContent', 'hpDisconnectError', 'mapValidationStatus',
    'acDemandPctHelp', 'acDemandPctInput', 'acScheduleUnionText', 'typicalDayArticle',
    'monthHourHeatmapArticle', 'dayhourExplorerFold', 'batScenario', 'solarScenario',
    'filterLoad', 'solarApplyStatus', 'applyOptimalLoan', 'saveLoanSimulation'
  ];

  requiredElementIds.forEach(id => {
    if (!elements.has(id)) elements.set(id, makeEl('div', id));
  });

  const storageMap = new Map();
  const mockLocalStorage = {
    getItem: k => storageMap.get(k) || null,
    setItem: (k, v) => storageMap.set(k, String(v)),
    removeItem: k => storageMap.delete(k)
  };

  const doc = {
    activeElement: null,
    getElementById: id => elements.get(id) || makeEl('div', id),
    createElement: tag => makeEl(tag),
    querySelector: sel => {
      if (sel.startsWith('#')) return elements.get(sel.slice(1)) || null;
      return null;
    },
    querySelectorAll: () => [],
    addEventListener() {},
    removeEventListener() {}
  };

  const win = {
    addEventListener() {},
    removeEventListener() {},
    location: { hash: '' },
    matchMedia: () => ({ matches: false }),
    innerWidth: 1024,
    innerHeight: 768,
    Blob: MockBlob,
    URL: {
      createObjectURL: blob => {
        const url = `blob:mock-export-${blobCounter++}`;
        blobUrlMap.set(url, blob);
        createdObjectUrls.push({ url, blob });
        return url;
      },
      revokeObjectURL: url => {
        revokedObjectUrls.push(url);
        blobUrlMap.delete(url);
      }
    },
    print() {}
  };
  win.window = win;
  win.document = doc;

  const pendingTimers = [];
  let timerSeq = 1;
  function fakeSetTimeout(fn, ms) {
    const id = timerSeq++;
    pendingTimers.push({ id, fn, ms });
    return id;
  }
  function fakeClearTimeout(id) {
    const idx = pendingTimers.findIndex(t => t.id === id);
    if (idx !== -1) pendingTimers.splice(idx, 1);
  }

  const evaluateCalls = [];
  const pendingEvaluateCalls = [];
  let autoResolveEvaluate = true;

  const fallbackDefaults = {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [
      { id: 'morning', start_minute: 360, end_minute: 540 },
      { id: 'evening', start_minute: 1020, end_minute: 1320 }
    ],
    ac_heating_additional_demand_pct: 12.3456789,
    timezone_mode: 'civil',
    south_panels: 12,
    west_panels: 4,
    battery_kwh: 10.0,
    battery_modules: 2,
    battery_module_kwh: 5.0,
    battery_mixed_sizes: false,
    battery_secondary_modules: 0,
    inverter_kw: 6.0,
    charge_kw: 3.0,
    discharge_kw: 3.0,
    scop: 3.8,
    heating_balance_temp: 18.0,
    horizon_years: 20,
    deduction_rate: 0.50,
    energy_real_escalation: 0.02,
    inflation: 0.02,
    export_market_price: 0.10,
    export_capture_factor: 0.85,
    loan_amount: 0,
    loan_years: 10,
    loan_apr: 0.05,
    loan_fraction: 0,
    loan_mode: 'amount',
    loan_max_monthly_payment: null,
    loan_include_electrification: false,
    retail_commodity: 0.25,
    gas_commodity: 1.10,
    real_discount_rate: 0.03
  };

  const fallbackRecords = [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' },
    { name: 'heating_balance_temp', unit: '°C' },
    { name: 'timezone_mode', unit: 'mode' },
    { name: 'south_panels', unit: 'panels' },
    { name: 'west_panels', unit: 'panels' },
    { name: 'battery_kwh', unit: 'kWh' },
    { name: 'battery_modules', unit: 'modules' },
    { name: 'battery_module_kwh', unit: 'kWh' },
    { name: 'battery_mixed_sizes', unit: 'boolean' },
    { name: 'battery_secondary_modules', unit: 'modules' },
    { name: 'inverter_kw', unit: 'kW' },
    { name: 'charge_kw', unit: 'kW' },
    { name: 'discharge_kw', unit: 'kW' },
    { name: 'scop', unit: '' },
    { name: 'horizon_years', unit: 'years' },
    { name: 'deduction_rate', unit: '%' },
    { name: 'energy_real_escalation', unit: '%' },
    { name: 'inflation', unit: '%' },
    { name: 'export_market_price', unit: '€/kWh' },
    { name: 'export_capture_factor', unit: '%' },
    { name: 'loan_amount', unit: '€', bounds: [0, 200000] },
    { name: 'loan_years', unit: 'years', bounds: [1, 30] },
    { name: 'loan_apr', unit: '%', bounds: [0, 0.2] },
    { name: 'loan_fraction', unit: '%', bounds: [0, 1] },
    { name: 'loan_mode', unit: 'mode' },
    { name: 'loan_max_monthly_payment', unit: '€' },
    { name: 'loan_include_electrification', unit: 'boolean' },
    { name: 'retail_commodity', unit: '€/kWh' },
    { name: 'gas_commodity', unit: '€/Smc' },
    { name: 'real_discount_rate', unit: '%' }
  ];

  function makeStandardResult(evalSettings) {
    const s = evalSettings ? deepClone(evalSettings) : deepClone(fallbackDefaults);
    const pct = s.ac_heating_additional_demand_pct ?? 0;
    const isBaseline = s.load_scenario === 'baseline';
    const isHp = Boolean(s.planning_heat_pump);

    return {
      settings: s,
      layout: { kwp: 6.0, panel_count: 16, theoretical: false },
      metrics: {
        pv_kwh: { median: 8200, min: 7400, max: 9000 },
        export_kwh: { median: 3200, min: 2800, max: 3700 },
        autarky: { median: 0.68, min: 0.58, max: 0.78 },
        self_consumption: { median: 0.58, min: 0.50, max: 0.66 },
        npv_eur: { median: 4800, min: 3500, max: 6200 },
        package_npv_eur: { median: 6500, min: 5100, max: 7900 },
        payback_years: { median: 7.2, min: 6.0, max: 9.0, recovered_weather_years: 19 },
        discounted_payback_years: { median: 8.8, min: 7.5, max: 10.8, recovered_weather_years: 19 },
        irr: { median: 0.085, min: 0.070, max: 0.102 },
        lcoe_eur_kwh: { median: 0.082, min: 0.072, max: 0.092 }
      },
      finance: {
        capex_eur: 8500,
        conversion_capex_eur: isHp ? 6000 : 0,
        nominal_discount_rate: 0.05,
        real_discount_rate: 0.03,
        annual_deduction_eur: 425,
        loan_principal_eur: 0,
        requested_loan_amount_eur: 0,
        loan_scope: 'solar',
        whole_package: {
          simple_payback: { median: 7.8, min: 6.2, max: 9.8, recovered_weather_years: 19 },
          discounted_payback: { median: 9.6, min: 7.8, max: 12.0, recovered_weather_years: 19 }
        }
      },
      energy: {
        load_kwh: { median: 5500 },
        pv_kwh: { median: 8200 },
        direct_kwh: { median: 2600 },
        discharge_kwh: { median: 2100 },
        grid_import_kwh: { median: 1400 },
        export_kwh: { median: 3200 },
        battery_loss_kwh: { median: 280 },
        clipped_kwh: { median: 40 },
        self_consumption: { median: 0.58 },
        useful_self_consumption: { median: 0.58 }
      },
      enduses: isBaseline ? {
        ac_heating_baseline_electricity_kwh: 0,
        ac_heating_requested_electricity_kwh: 0,
        ac_heating_extra_electricity_kwh: 0,
        ac_heating_baseline_useful_kwh: 0,
        ac_heating_requested_useful_kwh: 0,
        ac_heating_scop: s.scop ?? 3.8,
        ac_heating_additional_demand_pct: 0
      } : {
        ac_heating_baseline_electricity_kwh: 2350,
        ac_heating_requested_electricity_kwh: Math.round(2350 * (1 + pct / 100)),
        ac_heating_extra_electricity_kwh: Math.round(2350 * (pct / 100)),
        ac_heating_baseline_useful_kwh: 8930,
        ac_heating_requested_useful_kwh: Math.round(8930 * (1 + pct / 100)),
        ac_heating_scop: s.scop ?? 3.8,
        ac_heating_additional_demand_pct: pct
      },
      heat_pump: isHp ? {
        metadata: { equipment_model: 'Daikin Altherma 3 H HT 14kW', provisional_nominal_capacity_kw: 14.0 },
        envelopes: {
          seasonal_scop_space: { median: 3.45 },
          seasonal_performance_factor_delivered_heat: { median: 3.12 },
          annual_total_electricity_kwh: { median: 4200 }
        }
      } : null,
      annual_cashflows: [],
      limitations: ['Hourly energy conservation active.', 'PVGIS weather series repeated across horizon.']
    };
  }

  const ctx = {
    console,
    fallbackDefaults,
    fallbackRecords,
    SharedScenarioSync,
    window: win,
    document: doc,
    structuredClone,
    localStorage: mockLocalStorage,
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    URL: win.URL,
    Blob: win.Blob,
    fetch: async (pathUrl, fetchOpts) => {
      if (pathUrl === '/api/schema') {
        return { ok: true, json: async () => ({ defaults: fallbackDefaults, records: fallbackRecords }) };
      }
      if (pathUrl === '/api/health') {
        return { ok: false, status: 500, json: async () => ({ error: 'init suppressed' }) };
      }
      if (pathUrl === '/api/state') {
        return { ok: true, json: async () => ({ revision: 'rev_0', settings: fallbackDefaults, quote: { line_items: [{ description: 'Base panels', quantity: 16, net_eur: 5000, vat: 0.1 }] } }) };
      }
      if (pathUrl === '/api/evaluate') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        evaluateCalls.push(body);
        const standardRes = {
          compute_seconds: 0.15,
          result: makeStandardResult(body.settings)
        };
        if (autoResolveEvaluate) {
          return { ok: true, json: async () => standardRes };
        }
        return new Promise((resolve, reject) => {
          pendingEvaluateCalls.push({
            body,
            resolve: val => resolve({ ok: true, json: async () => val || standardRes }),
            reject: err => reject(err)
          });
        });
      }
      return { ok: true, json: async () => ({}) };
    }
  };

  vm.createContext(ctx);
  vm.runInContext(appSource, ctx);

  async function flushTimers() {
    while (pendingTimers.length > 0) {
      const batch = pendingTimers.splice(0);
      for (const t of batch) {
        t.fn();
        await new Promise(r => setImmediate(r));
      }
    }
  }

  function run(code) {
    return vm.runInContext(code, ctx);
  }

  return {
    ctx,
    elements,
    doc,
    win,
    evaluateCalls,
    pendingEvaluateCalls,
    clickedDownloads,
    createdObjectUrls,
    revokedObjectUrls,
    blobUrlMap,
    flushTimers,
    run,
    makeStandardResult,
    set autoResolveEvaluate(v) { autoResolveEvaluate = Boolean(v); }
  };
}

async function initDefaultHarness(harness) {
  const { run, elements } = harness;
  run(`
    schema = {
      defaults: fallbackDefaults,
      records: fallbackRecords
    };
    settings = deepClone(schema.defaults);
    quote = { line_items: [{ description: 'Base panels', quantity: 16, net_eur: 5000, vat: 0.1 }] };
    discardAcDrafts();
  `);
  await run("recompute({ sync: false })");
  await harness.flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
  elements.get('error').textContent = '';
  elements.get('error').hidden = true;
}

test('1. Production download click exports coherent snapshot: pv-lab-scenario.json with exact numeric payload, canonical windows, arbitrary pct, and no legacy heating keys', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads } = harness;
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'initial evaluation current');

  // Trigger production download click
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'download action triggered');

  const dl = clickedDownloads[0];
  assert.equal(dl.download, 'pv-lab-scenario.json');
  assert.equal(dl.blob.type, 'application/json');

  const payloadText = await dl.blob.text();
  const payload = JSON.parse(payloadText);

  // Format and top-level schema
  assert.equal(payload.format, 'pv-lab-pv-v1');
  assert.ok(payload.settings, 'settings present');
  assert.ok(payload.quote, 'quote present');
  assert.ok(payload.result, 'result present');

  // Exact arbitrary percentage preserved without rounding/clamping or extra multiplier
  assert.equal(payload.settings.ac_heating_additional_demand_pct, 12.3456789);
  assert.equal(payload.settings.heating_schedule_mode, 'scheduled');

  // Unchanged canonical nested arrays
  assert.deepEqual(payload.settings.ac_heating_windows, [
    { id: 'morning', start_minute: 360, end_minute: 540 },
    { id: 'evening', start_minute: 1020, end_minute: 1320 }
  ]);

  // Derived legacy keys stripped
  const legacyKeys = ['heating_on_hour', 'heating_off_hour', 'heating_season_mode', 'ac_heating_mode', 'ac_heating_on_hour', 'ac_heating_off_hour', 'hasItemized'];
  legacyKeys.forEach(k => {
    assert.equal(Object.hasOwn(payload.settings, k), false, `legacy key ${k} removed`);
  });

  // Authoritative evaluated metrics and enduses
  assert.ok(payload.result.enduses, 'result enduses present');
  assert.equal(payload.result.enduses.ac_heating_baseline_electricity_kwh, 2350);
  assert.equal(payload.result.enduses.ac_heating_additional_demand_pct, 12.3456789);
  assert.equal(payload.result.enduses.ac_heating_scop, 3.8);

  // Quote snapshot
  assert.equal(payload.quote.line_items.length, 1);
  assert.equal(payload.quote.line_items[0].description, 'Base panels');
});

test('2. Stale evaluation guard: pending recompute, in-flight evaluate, and stale success/error races block export and print', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, pendingEvaluateCalls, flushTimers } = harness;
  harness.autoResolveEvaluate = false;

  // A: Edit input, triggers debounced recompute
  run(`
    editSetting('south_panels', 20);
  `);
  assert.equal(run('isScenarioEvaluationCurrent()'), false, 'marked stale immediately on input edit');

  // Attempt export while debouncing
  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export blocked while recompute debouncing');
  assert.match(elements.get('error').textContent, /inputs have changed|Wait for calculation to finish/i);

  // Attempt print while debouncing
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 0, 'print blocked while recompute debouncing');
  assert.match(elements.get('error').textContent, /inputs have changed|Wait for calculation to finish/i);

  // Flush debounce timer -> /api/evaluate is now in-flight (deferred network)
  await flushTimers();
  assert.equal(pendingEvaluateCalls.length, 1, 'first evaluate call dispatched');
  const req1 = pendingEvaluateCalls[0];

  // Still blocked while in-flight
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export blocked while evaluate in-flight');

  // B: Second edit before req1 resolves -> triggers request 2
  run(`
    editSetting('south_panels', 22);
  `);
  await flushTimers();
  assert.equal(pendingEvaluateCalls.length, 2, 'second evaluate call dispatched');
  const req2 = pendingEvaluateCalls[1];

  // Stale success race: resolve req1 (older response)
  req1.resolve({
    compute_seconds: 0.1,
    result: harness.makeStandardResult({ ...run('settings'), south_panels: 20 })
  });
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  // Req1 response must be ignored (id !== requestId)
  assert.equal(run('isScenarioEvaluationCurrent()'), false, 'still stale after obsolete request 1 resolved');
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export still blocked after obsolete request 1');

  // Stale error race simulation: if an older request rejected, it must not kill current state
  // Now resolve req2 (matching current state)
  req2.resolve({
    compute_seconds: 0.1,
    result: harness.makeStandardResult({ ...run('settings'), south_panels: 22 })
  });
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  // Now current!
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'current after matching request 2 resolved');

  // Export succeeds and contains south_panels = 22
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1);
  const payload = JSON.parse(await clickedDownloads[0].blob.text());
  assert.equal(payload.settings.south_panels, 22);
  assert.equal(payload.result.settings.south_panels, 22);

  // Print succeeds
  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  assert.equal(clickedDownloads[0].download, 'pv-lab-one-page.html');
});

test('3. Invalid governing AC draft blocks export/print; same-value correction restores exportability', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, flushTimers } = harness;

  // Case A: Invalid schedule draft in electrified scenario
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  assert.equal(run('isScenarioEvaluationCurrent()'), false);

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export blocked by invalid schedule draft');
  assert.match(elements.get('error').textContent, /invalid drafts|Fix errors/i);

  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 0, 'print blocked by invalid schedule draft');
  assert.match(elements.get('error').textContent, /invalid drafts|Fix errors/i);

  // Case B: Same-value correction back to valid canonical value (06:00)
  run(`
    handleAcScheduleTimeInput('morning', 'start', '06:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), false);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'evaluation current after same-value correction');

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'export unblocked after draft corrected');

  // Case C: Invalid demand pct draft
  run(`
    handleAcHeatingDemandPctInput('-10');
  `);
  assert.equal(run('hasInvalidAcDemandPctDraft()'), true);
  assert.equal(run('isScenarioEvaluationCurrent()'), false);

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export blocked by invalid demand pct draft');

  // Correct demand pct back to valid arbitrary pct
  run(`
    handleAcHeatingDemandPctInput('12.3456789');
  `);
  assert.equal(run('hasInvalidAcDemandPctDraft()'), false);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'export unblocked after pct corrected');
  const payload = JSON.parse(await clickedDownloads[0].blob.text());
  assert.equal(payload.settings.ac_heating_additional_demand_pct, 12.3456789);
});

test('4. Scoping: irrelevant AC drafts (baseline load scenario, heat pump planning, continuous mode) do NOT block export/print', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, flushTimers } = harness;

  // Scope 1: Baseline scenario (AC is inactive, gas boiler serves space heating)
  run(`
    editSetting('load_scenario', 'baseline');
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
  assert.equal(run('isScenarioEvaluationCurrent()'), true);

  // Introduce an invalid AC schedule draft while on baseline
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  assert.equal(run('isAcScheduleGoverningActiveHeating(settings)'), false, 'AC schedule does not govern baseline');
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'evaluation remains current on baseline');

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'export succeeds on baseline despite irrelevant AC draft');

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1, 'print succeeds on baseline despite irrelevant AC draft');

  // Scope 2: Heat pump planning active (AC heating is inactive)
  run(`
    discardAcDrafts();
    editSetting('load_scenario', 'electrified');
    editSetting('planning_heat_pump', true);
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
  assert.equal(run('isScenarioEvaluationCurrent()'), true);

  // Introduce invalid AC draft while planning HP
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('isAcScheduleGoverningActiveHeating(settings)'), false, 'AC does not govern HP');
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'evaluation remains current on HP');

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'export succeeds under HP despite irrelevant AC draft');

  // Scope 3: Continuous mode (heating_schedule_mode: 'continuous')
  run(`
    discardAcDrafts();
    editSetting('planning_heat_pump', false);
    editSetting('heating_schedule_mode', 'continuous');
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));
  assert.equal(run('isScenarioEvaluationCurrent()'), true);

  // Introduce invalid schedule window draft while in continuous mode
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  assert.equal(run('isAcScheduleGoverningActiveHeating(settings)'), false, 'schedule windows do not govern continuous mode');
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'evaluation remains current in continuous mode');

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'export succeeds in continuous mode');

  const dlPayload = JSON.parse(await clickedDownloads[0].blob.text());
  assert.equal(dlPayload.settings.heating_schedule_mode, 'continuous');
  // Canonical windows are preserved in continuous mode export
  assert.ok(Array.isArray(dlPayload.settings.ac_heating_windows));
  assert.equal(dlPayload.settings.ac_heating_windows.length, 2);
});

test('5. Printed onePage() report: includes accurate enduses metrics, baseline distinction, HP separation, and all 5 boundary prose items', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, flushTimers } = harness;

  // A: Electrified scenario with AC heating
  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);

  const dl = clickedDownloads[0];
  assert.equal(dl.download, 'pv-lab-one-page.html');
  assert.equal(dl.blob.type, 'text/html');

  const html = await dl.blob.text();

  // 1. Technical summary & enduses metrics
  assert.match(html, /Air conditioner heating technical summary/i);
  assert.match(html, /Nominal SCOP 3\.8/i);
  assert.match(html, /Baseline useful heat 8,930 kWh th\/yr/i);
  assert.match(html, /Requested useful heat 10,032 kWh th\/yr/i);
  assert.match(html, /Additional demand margin 12\.3456789%/i);

  // 2. Electricity consumption & counterfactual (clearly distinct from baseline scenario)
  assert.match(html, /AC baseline 0% counterfactual 2,350 kWh\/yr/i);
  assert.match(html, /Requested AC electricity 2,640 kWh\/yr/i);
  assert.match(html, /Extra electricity 290 kWh\/yr/i);

  // 3. Operating schedule summary & clock context
  assert.match(html, /morning: 06:00–09:00/i);
  assert.match(html, /evening: 17:00–22:00/i);
  assert.match(html, /Civil local time Europe\/Rome/i);

  // 4. Five required physical boundary and methodology items in concise prose
  assert.match(html, /Fixed monthly budgets &amp; weather timing/i);
  assert.match(html, /Monthly useful thermal budgets derive from historical natural gas consumption and remain strictly fixed for each month/i);
  assert.match(html, /below 18\.0°C balance temperature/i);
  assert.match(html, /Unchanged historical gas saving/i);
  assert.match(html, /Heating electricity is computed by dividing required useful heat by nominal SCOP once/i);
  assert.match(html, /avoided fuel credit remains capped at historical displaced gas consumption/i);
  assert.doesNotMatch(html, /enhanced comfort/i);
  assert.match(html, /requested extra useful heat only \(no inferred comfort\)/i);
  assert.match(html, /Equipment adequacy &amp; comfort unassessed/i);
  assert.match(html, /Equipment adequacy, indoor comfort levels, and low-temperature compressor derating/i);
  assert.doesNotMatch(html, /peak power requirements/i);
  assert.match(html, /Modeled interval-average vs permitted-duration requirements/i);
  assert.match(html, /modeled interval-average versus permitted-duration requirements/i);
  assert.match(html, /actual equipment capacity and comfort remain unassessed/i);
  assert.match(html, /Hourly PV coincidence limitation/i);
  assert.match(html, /Hourly energy simulation conserves total energy; minute-level window bounds define hourly allocation eligibility rather than sub-hourly solar PV coincidence/i);

  // 5. HP print section must NOT appear when HP is not active
  assert.doesNotMatch(html, /Heat pump provisional technical summary/i);

  // B: Baseline load scenario distinct report
  run(`
    editSetting('load_scenario', 'baseline');
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  const baselineHtml = await clickedDownloads[0].blob.text();
  assert.ok(baselineHtml.includes('Baseline load scenario (<code>baseline</code>):'), 'baseline load scenario text included');
  assert.match(baselineHtml, /Space heating is supplied entirely by the existing natural gas boiler/i);
  assert.doesNotMatch(baselineHtml, /AC baseline 0% counterfactual/i);

  // C: Heat pump scenario separate print
  run(`
    editSetting('load_scenario', 'electrified');
    editSetting('planning_heat_pump', true);
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  const hpHtml = await clickedDownloads[0].blob.text();

  assert.match(hpHtml, /Heat pump provisional technical summary/i);
  assert.match(hpHtml, /Daikin Altherma 3 H HT 14kW/i);
  assert.match(hpHtml, /Gas connection status/i);
  // AC heating summary must NOT appear when HP is planning
  assert.doesNotMatch(hpHtml, /Air conditioner heating technical summary/i);
});

test('6. Printed onePage() schedule formats: multiple, minute, overnight, inactive, all-day, and continuous-retained', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, flushTimers } = harness;

  // A: Minute-precision and overnight window
  run(`
    settings.ac_heating_windows = [
      { id: 'morning_shift', start_minute: 375, end_minute: 555 },
      { id: 'night_shift', start_minute: 1320, end_minute: 360 }
    ];
    settings.ac_heating_additional_demand_pct = 15;
    recompute({ sync: false });
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  let html = await clickedDownloads[0].blob.text();

  assert.match(html, /morning_shift: 06:15–09:15/i);
  assert.match(html, /night_shift: overnight \(22:00–06:00, crosses midnight\)/i);

  // B: Inactive and all-day windows
  run(`
    settings.ac_heating_windows = [
      { id: 'inactive_slot', start_minute: 480, end_minute: 480 },
      { id: 'full_day', start_minute: 0, end_minute: 1440 }
    ];
    recompute({ sync: false });
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  html = await clickedDownloads[0].blob.text();

  assert.match(html, /inactive_slot: inactive \(08:00–08:00, 0 min\)/i);
  assert.match(html, /full_day: all-day 24h \(00:00–24:00\)/i);

  // C: Continuous mode with retained canonical windows and standard solar timezone
  run(`
    settings.heating_schedule_mode = 'continuous';
    settings.timezone_mode = 'standard';
    settings.ac_heating_windows = [
      { id: 'day_slot', start_minute: 540, end_minute: 1020 }
    ];
    recompute({ sync: false });
  `);
  await flushTimers();
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  html = await clickedDownloads[0].blob.text();

  assert.match(html, /Continuous 24h heating \(unconstrained historical continuous profile; canonical operating windows retained: day_slot: 09:00–17:00\)/i);
  assert.match(html, /Fixed UTC\+1 standard time \(no DST\)/i);
  assert.doesNotMatch(html, /solar time/i);
});

test('7. Missing diagnostics vs legitimate zero formatting in onePage()', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads } = harness;

  // A: Legitimate zero extra demand (0%), zero SCOP, and zero balance temp
  run(`
    settings.ac_heating_additional_demand_pct = 0;
    settings.heating_balance_temp = 0;
    settings.scop = 0;
    current.result.settings.ac_heating_additional_demand_pct = 0;
    current.result.settings.heating_balance_temp = 0;
    current.result.settings.scop = 0;
    current.result.enduses.ac_heating_additional_demand_pct = 0;
    current.result.enduses.ac_heating_extra_electricity_kwh = 0;
    current.result.enduses.ac_heating_scop = 0;
    current.result.enduses.ac_heating_requested_electricity_kwh = 2350;
    current.result.enduses.ac_heating_baseline_electricity_kwh = 2350;
    acceptedInputFingerprint = inputFingerprint(cleanSettingsForBackend(deepClone(settings)), deepClone(quote));
  `);

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  let html = await clickedDownloads[0].blob.text();

  assert.match(html, /Extra electricity 0 kWh\/yr/i);
  assert.match(html, /Additional demand margin 0%/i);
  assert.match(html, /Nominal SCOP 0\.0/i);
  assert.match(html, /below 0\.0°C balance temperature/i);
  assert.doesNotMatch(html, /Extra electricity Unavailable/i);
  assert.doesNotMatch(html, /Additional demand margin Unavailable/i);
  assert.doesNotMatch(html, /Nominal SCOP Unavailable/i);
  assert.doesNotMatch(html, /below Unavailable balance temperature/i);

  // B: Nondefault balance temperature (e.g. 16.5°C)
  run(`
    settings.heating_balance_temp = 16.5;
    current.result.settings.heating_balance_temp = 16.5;
    acceptedInputFingerprint = inputFingerprint(cleanSettingsForBackend(deepClone(settings)), deepClone(quote));
  `);

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  html = await clickedDownloads[0].blob.text();
  assert.match(html, /below 16\.5°C balance temperature/i);

  // C: Missing and nonfinite metadata (null, undefined, NaN, Infinity) explicitly render Unavailable
  run(`
    current.result.enduses.ac_heating_baseline_useful_kwh = null;
    current.result.enduses.ac_heating_requested_useful_kwh = undefined;
    current.result.enduses.ac_heating_scop = NaN;
    current.result.settings.scop = NaN;
    settings.scop = NaN;
    current.result.enduses.ac_heating_additional_demand_pct = Infinity;
    current.result.settings.ac_heating_additional_demand_pct = Infinity;
    settings.ac_heating_additional_demand_pct = Infinity;
    current.result.settings.heating_balance_temp = NaN;
    settings.heating_balance_temp = NaN;
    current.result.enduses.ac_heating_extra_electricity_kwh = NaN;
    acceptedInputFingerprint = inputFingerprint(cleanSettingsForBackend(deepClone(settings)), deepClone(quote));
  `);

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  html = await clickedDownloads[0].blob.text();

  assert.match(html, /Baseline useful heat Unavailable/i);
  assert.match(html, /Requested useful heat Unavailable/i);
  assert.match(html, /Nominal SCOP Unavailable/i);
  assert.match(html, /Additional demand margin Unavailable/i);
  assert.match(html, /below Unavailable balance temperature/i);
  assert.match(html, /Extra electricity Unavailable/i);

  // D: Exact finite additional demand percentage preserved without toFixed rounding
  run(`
    settings.ac_heating_additional_demand_pct = 15.6789123;
    settings.heating_balance_temp = 18.0;
    settings.scop = 3.8;
    current.result.settings.ac_heating_additional_demand_pct = 15.6789123;
    current.result.settings.heating_balance_temp = 18.0;
    current.result.settings.scop = 3.8;
    current.result.enduses.ac_heating_additional_demand_pct = 15.6789123;
    current.result.enduses.ac_heating_scop = 3.8;
    current.result.enduses.ac_heating_baseline_useful_kwh = 8930;
    current.result.enduses.ac_heating_requested_useful_kwh = 10032;
    current.result.enduses.ac_heating_extra_electricity_kwh = 290;
    acceptedInputFingerprint = inputFingerprint(cleanSettingsForBackend(deepClone(settings)), deepClone(quote));
  `);

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);
  html = await clickedDownloads[0].blob.text();
  assert.match(html, /Additional demand margin 15\.6789123%/i);
  assert.doesNotMatch(html, /Additional demand margin 15\.6789%/i);

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1);
  const dlPayload = JSON.parse(await clickedDownloads[0].blob.text());
  assert.equal(dlPayload.settings.ac_heating_additional_demand_pct, 15.6789123);
});

test('8. Missing production test: quote-only edit blocks download/print until matching evaluation accepted, then payload coherent and deep cloned without mutating sources', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, pendingEvaluateCalls, flushTimers } = harness;
  harness.autoResolveEvaluate = false;

  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'initial scenario evaluation current');

  // Trigger quote-only edit (add a line item)
  run(`
    quote.line_items.push({ description: 'Contractor special inverter warranty', quantity: 1, net_eur: 1250, vat: 0.1 });
    schedule();
  `);

  // Immediate debouncing state marks calculation stale
  assert.equal(run('isScenarioEvaluationCurrent()'), false, 'quote-only edit immediately invalidates calculation currency');

  // Attempt export while debouncing
  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export blocked while quote-only edit debouncing');
  assert.match(elements.get('error').textContent, /inputs have changed|Wait for calculation to finish/i);

  // Attempt print while debouncing
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 0, 'print blocked while quote-only edit debouncing');
  assert.match(elements.get('error').textContent, /inputs have changed|Wait for calculation to finish/i);

  // Flush debounce timer -> evaluate is now in-flight
  await flushTimers();
  assert.equal(pendingEvaluateCalls.length, 1, 'evaluate dispatched for quote change');
  const req = pendingEvaluateCalls[0];

  // Still blocked while in-flight
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 0, 'export blocked while evaluate in-flight');

  // Resolve evaluate matching the updated quote
  req.resolve({
    compute_seconds: 0.1,
    result: harness.makeStandardResult(run('settings'))
  });
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  // Now current!
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'current after matching evaluation accepted');

  // Production download click succeeds
  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'download succeeds once matching evaluation accepted');

  const dlPayload = JSON.parse(await clickedDownloads[0].blob.text());

  // Coherent payload quote, settings, and result
  assert.equal(dlPayload.quote.line_items.length, 2);
  assert.equal(dlPayload.quote.line_items[1].description, 'Contractor special inverter warranty');
  assert.equal(dlPayload.quote.line_items[1].net_eur, 1250);
  assert.ok(dlPayload.settings);
  assert.ok(dlPayload.result);

  // Deep clone verification: mutating downloaded payload must NOT mutate application sources
  dlPayload.quote.line_items[0].description = 'MUTATED_PAYLOAD_QUOTE';
  dlPayload.settings.south_panels = 999;
  dlPayload.result.metrics.npv_eur = -99999;

  assert.notEqual(run('quote.line_items[0].description'), 'MUTATED_PAYLOAD_QUOTE', 'source quote was not mutated');
  assert.notEqual(run('settings.south_panels'), 999, 'source settings was not mutated');
  assert.notEqual(run('current.result.metrics.npv_eur'), -99999, 'source current.result was not mutated');

  // Print also succeeds
  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1, 'print succeeds once matching evaluation accepted');
});

test('9. Missing production test: deferred obsolete evaluate ERROR actually rejected and cannot replace newer accepted result/export', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads, pendingEvaluateCalls, flushTimers } = harness;
  harness.autoResolveEvaluate = false;

  // Request 1 dispatched
  run("editSetting('south_panels', 20);");
  await flushTimers();
  assert.equal(pendingEvaluateCalls.length, 1);
  const req1 = pendingEvaluateCalls[0];

  // Request 2 dispatched (supersedes Request 1)
  run("editSetting('south_panels', 24);");
  await flushTimers();
  assert.equal(pendingEvaluateCalls.length, 2);
  const req2 = pendingEvaluateCalls[1];

  // Resolve Request 2 (matching current state)
  req2.resolve({
    compute_seconds: 0.1,
    result: harness.makeStandardResult({ ...run('settings'), south_panels: 24 })
  });
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'accepted Request 2');
  assert.equal(run('current.result.settings.south_panels'), 24);

  // Now deferred Request 1 fails with an actual network/server ERROR
  req1.reject(new Error('500 Internal Server Error from obsolete request 1'));
  for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r));

  // The obsolete ERROR must be rejected and must not overwrite accepted result, status, or currency
  assert.equal(run('isScenarioEvaluationCurrent()'), true, 'state remains current despite obsolete error');
  assert.doesNotMatch(elements.get('error').textContent, /500 Internal Server Error from obsolete request 1/);
  assert.equal(run('current.result.settings.south_panels'), 24, 'current.result not replaced by obsolete error');

  // Production export still succeeds and exports current south_panels = 24
  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1, 'export succeeds after obsolete error rejection');
  const payload = JSON.parse(await clickedDownloads[0].blob.text());
  assert.equal(payload.settings.south_panels, 24);
  assert.equal(payload.result.settings.south_panels, 24);

  // Production print still succeeds
  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1, 'print succeeds after obsolete error rejection');
});

test('10. Missing production test: result settings and source settings retain unrelated fields across evaluation and download', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads } = harness;

  // Attach unrelated custom fields to in-memory settings and result
  run(`
    settings.unrelated_source_custom_flag = 'source_preserved_abc';
    current.result.settings.unrelated_result_custom_metric = 'result_preserved_xyz';
    acceptedInputFingerprint = inputFingerprint(cleanSettingsForBackend(deepClone(settings)), deepClone(quote));
  `);

  assert.equal(run('isScenarioEvaluationCurrent()'), true);

  clickedDownloads.length = 0;
  elements.get('download').click();
  assert.equal(clickedDownloads.length, 1);

  const payload = JSON.parse(await clickedDownloads[0].blob.text());

  // Source settings retains unrelated fields
  assert.equal(run('settings.unrelated_source_custom_flag'), 'source_preserved_abc');

  // In-memory current.result.settings retains unrelated fields
  assert.equal(run('current.result.settings.unrelated_result_custom_metric'), 'result_preserved_xyz');

  // Downloaded payload result settings retains unrelated fields
  assert.equal(payload.result.settings.unrelated_result_custom_metric, 'result_preserved_xyz');
});

test('11. Browser QA artifact capture: write production onePage() HTML into the OS temporary directory', async () => {
  const harness = createExportHarness();
  await initDefaultHarness(harness);

  const { run, elements, clickedDownloads } = harness;

  // Realistic electrified fixture with 12.3456789%, nondefault balance temp (16.5°C), minute/overnight canonical windows
  run(`
    settings.load_scenario = 'electrified';
    settings.planning_heat_pump = false;
    settings.heating_schedule_mode = 'scheduled';
    settings.ac_heating_additional_demand_pct = 12.3456789;
    settings.heating_balance_temp = 16.5;
    settings.timezone_mode = 'civil';
    settings.ac_heating_windows = [
      { id: 'morning_slot', start_minute: 375, end_minute: 555 },
      { id: 'overnight_slot', start_minute: 1320, end_minute: 360 }
    ];
    settings.scop = 3.8;

    current.result.settings = deepClone(settings);
    current.result.enduses = {
      ac_heating_baseline_useful_kwh: 8930,
      ac_heating_requested_useful_kwh: 10032,
      ac_heating_baseline_electricity_kwh: 2350,
      ac_heating_requested_electricity_kwh: 2640,
      ac_heating_extra_electricity_kwh: 290,
      ac_heating_scop: 3.8,
      ac_heating_additional_demand_pct: 12.3456789
    };
    acceptedInputFingerprint = inputFingerprint(cleanSettingsForBackend(deepClone(settings)), deepClone(quote));
  `);

  clickedDownloads.length = 0;
  elements.get('onepage').click();
  assert.equal(clickedDownloads.length, 1);

  const qaHtml = await clickedDownloads[0].blob.text();

  assert.match(qaHtml, /Additional demand margin 12\.3456789%/);
  assert.match(qaHtml, /below 16\.5°C balance temperature/);
  assert.match(qaHtml, /morning_slot: 06:15–09:15/);
  assert.match(qaHtml, /overnight_slot: overnight \(22:00–06:00, crosses midnight\)/);
  assert.doesNotMatch(qaHtml, /enhanced comfort/);
  assert.match(qaHtml, /requested extra useful heat only \(no inferred comfort\)/);
  assert.doesNotMatch(qaHtml, /peak power requirements/);
  assert.match(qaHtml, /Modeled interval-average vs permitted-duration requirements/);
  assert.match(qaHtml, /modeled interval-average versus permitted-duration requirements/);
  assert.match(qaHtml, /actual equipment capacity and comfort remain unassessed/);

  // Write artifact for manager browser QA
  const artifactPath = path.join(os.tmpdir(), 'ac-heating-final-print-qa.html');
  fs.writeFileSync(artifactPath, qaHtml, 'utf8');
  assert.ok(fs.existsSync(artifactPath), `artifact captured at ${artifactPath}`);
});

