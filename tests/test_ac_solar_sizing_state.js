'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SharedScenarioSync = require('../feasibility/web/sync.js');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function makeEvaluateFixture(settings) {
  return {
    compute_seconds: 0.02,
    claims: [],
    warnings: [],
    result: {
      settings: deepClone(settings),
      limitations: [],
      weather_years: [2015, 2016],
      layout: { kwp: 9, panel_count: 18 },
      metrics: { npv_eur: 5000, autarky: 0.65, self_consumption: 0.55 },
      finance: {
        capex_eur: 11000,
        conversion_capex_eur: 0,
        annual_deduction_eur: 550,
        nominal_discount_rate: 0.04,
        real_discount_rate: 0.02,
        loan_principal_eur: 0,
        loan_scope: 'solar',
        pv_incremental: {
          npv: { median: 5000, min: 3000, max: 7000 },
          simple_payback: { median: 8, recovered_weather_years: 19 },
          discounted_payback: { median: 10, recovered_weather_years: 19 }
        },
        whole_package: {
          npv: { median: 4500, min: 2500, max: 6500 },
          simple_payback: { median: 9, recovered_weather_years: 19 },
          discounted_payback: { median: 11, recovered_weather_years: 19 }
        }
      },
      energy: {
        load_kwh: 3500,
        pv_kwh: 7000,
        direct_kwh: 2000,
        discharge_kwh: 1500,
        grid_import_kwh: 500,
        export_kwh: 3500,
        battery_loss_kwh: 200,
        clipped_kwh: 50,
        self_consumption: 0.55,
        useful_self_consumption: 0.52
      }
    }
  };
}

function makeSolarSizingFixture(options = {}) {
  const settings = deepClone(options.settings || {});
  const opts = deepClone(options.options || { load_scenario: 'electrified', objective: 'npv_eur' });
  const recommended = {
    panel_count: 18,
    kwp: 7.92,
    roof_counts: { south: 14, west: 4, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
    battery_kwh: 10.0,
    battery_modules: 2,
    battery_module_kwh: 5.0,
    capex_eur: 14128,
    npv_median: 11200,
    npv_min: 9100,
    npv_max: 13500,
    discounted_payback_years: 7.8,
    simple_payback_years: 6.5,
    autarky_median: 0.74,
    self_consumption_median: 0.62,
    annual_pv_kwh_median: 10200,
    annual_grid_import_kwh_median: 1850,
    inverter_kw: 6.0,
    inverter_held_fixed: true,
    inverter_status: 'Fixed selected inverter (6.00 kW) with AC clipping',
    settings: deepClone(settings)
  };
  return {
    status: 'SCENARIO_PENDING',
    compute_seconds: 0.05,
    settings,
    options: opts,
    recommended_candidate: recommended,
    recommendation_choices: {
      main_recommendation: 'balanced',
      balanced: {
        role_label: 'Balanced recommendation',
        panel_count: 18,
        kwp: 7.92,
        battery_kwh: 10.0,
        capex_eur: 14128,
        npv_median: 11200,
        discounted_payback_years: 7.8,
        autarky_median: 0.74,
        settings: deepClone(settings)
      },
      financial: {
        role_label: 'Financial maximum',
        panel_count: 24,
        kwp: 10.56,
        battery_kwh: 10.0,
        capex_eur: 18500,
        npv_median: 10800,
        discounted_payback_years: 9.4,
        autarky_median: 0.78,
        settings: deepClone(settings)
      }
    },
    size_curve: [
      {
        panel_count: 0, kwp: 0.0, battery_kwh: 0.0, capex_eur: 0,
        npv_median: 0, discounted_payback_years: null, autarky_median: 0.0
      },
      {
        panel_count: 18, kwp: 7.92, battery_kwh: 10.0, capex_eur: 14128,
        npv_median: 11200, discounted_payback_years: 7.8, autarky_median: 0.74
      }
    ],
    search_summary: {
      objective: 'npv_eur',
      evaluated_count: 128,
      limitations: ['Test simulation limits']
    },
    cost_assumptions: [
      { item: 'PV benchmark', value: '900 €/kWp', source: 'Benchmark catalog', status: 'provided' }
    ]
  };
}

function makeSolarSensitivityFixture(options = {}) {
  const settings = deepClone(options.settings || {});
  const opts = deepClone(options.options || {});
  const sensOpts = deepClone(options.sensitivity_options || {});
  const finBasis = options.financial_basis || 'project';

  return {
    status: 'SCENARIO_PENDING',
    compute_seconds: 0.04,
    financial_basis: finBasis,
    settings,
    options: opts,
    sensitivity_options: sensOpts,
    scenarios: {
      base: {
        status: 'OK',
        label: 'Base scenario',
        recommendation: {
          panel_count: 18,
          kwp: 7.92,
          battery_kwh: 10.0,
          npv_median: 11200,
          discounted_payback_years: 7.8
        }
      },
      lower_export: {
        status: 'OK',
        label: 'Lower export price',
        recommendation: {
          panel_count: 18,
          kwp: 7.92,
          battery_kwh: 10.0,
          npv_median: 9500,
          discounted_payback_years: 8.5
        }
      },
      higher_installed_cost: {
        status: 'OK',
        label: 'Higher installed cost',
        recommendation: {
          panel_count: 14,
          kwp: 6.16,
          battery_kwh: 5.0,
          npv_median: 8200,
          discounted_payback_years: 9.1
        }
      },
      lower_consumption: {
        status: 'OK',
        label: 'Lower consumption',
        recommendation: {
          panel_count: 16,
          kwp: 7.04,
          battery_kwh: 10.0,
          npv_median: 10100,
          discounted_payback_years: 8.2
        }
      },
      higher_apr: {
        status: 'OK',
        label: 'Higher financing APR',
        recommendation: {
          panel_count: 18,
          kwp: 7.92,
          battery_kwh: 10.0,
          npv_median: 10400,
          discounted_payback_years: 8.0
        }
      }
    }
  };
}

function createAppContext(options = {}) {
  const elements = new Map();

  function makeEl(tag = 'div', id = '') {
    const classes = new Set();
    const attrs = new Map();
    const listeners = new Map();
    const children = [];
    let parent = null;

    const el = {
      tagName: tag.toUpperCase(),
      id: id || '',
      children,
      get parentElement() { return parent; },
      get parentNode() { return parent; },
      get firstChild() { return children[0] || null; },
      get nextSibling() {
        if (!parent) return null;
        const idx = parent.children.indexOf(this);
        return idx !== -1 && idx + 1 < parent.children.length ? parent.children[idx + 1] : null;
      },
      get className() { return Array.from(classes).join(' '); },
      set className(val) {
        classes.clear();
        if (val) String(val).split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
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
      textContent: '',
      innerHTML: '',
      value: '',
      style: {},
      dataset: {},
      options: [],
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
        const ev = typeof event === 'string' ? { type: event } : event;
        const list = listeners.get(ev.type) || [];
        list.forEach(fn => fn(ev));
      },
      setAttribute(k, v) { attrs.set(k, String(v)); },
      getAttribute(k) { return attrs.get(k) ?? null; },
      hasAttribute(k) { return attrs.has(k); },
      removeAttribute(k) { attrs.delete(k); },
      closest(sel) {
        let cur = this;
        while (cur) {
          if (sel && cur.tagName && cur.tagName.toLowerCase() === sel.toLowerCase()) return cur;
          cur = cur.parentElement;
        }
        return makeEl(sel || 'div');
      },
      validity: { valid: true },
      querySelector(sel) {
        if (sel.startsWith('#')) return elements.get(sel.slice(1)) || null;
        if (sel.startsWith('.')) {
          const cls = sel.slice(1);
          const inChild = children.find(c => c.classList?.contains?.(cls) || c.className?.includes(cls));
          if (inChild) return inChild;
          for (const ch of children) {
            const nested = ch.querySelector ? ch.querySelector(sel) : null;
            if (nested) return nested;
          }
        }
        return null;
      },
      querySelectorAll(sel) {
        const out = [];
        for (const ch of children) {
          if (sel.startsWith('.')) {
            if (ch.classList?.contains?.(sel.slice(1))) out.push(ch);
          }
          if (ch.querySelectorAll) {
            out.push(...ch.querySelectorAll(sel));
          }
        }
        return out;
      },
      prepend(node) {
        if (node) { node.parent = this; children.unshift(node); }
      },
      appendChild(node) {
        if (node) { node.parent = this; children.push(node); }
      },
      insertBefore(newNode, refNode) {
        if (!newNode) return;
        newNode.parent = this;
        const idx = children.indexOf(refNode);
        if (idx !== -1) children.splice(idx, 0, newNode);
        else children.push(newNode);
      },
      focus() {}
    };

    if (id) elements.set(id, el);
    return el;
  }

  // Pre-create Solar Sizing UI elements
  makeEl('div', 'solarResultsWrap');
  const runSolarSizing = makeEl('button', 'runSolarSizing');
  runSolarSizing.textContent = 'Run sizing analysis';
  makeEl('div', 'solarStatus');
  makeEl('div', 'solarApplyStatus');
  const applySolarSystem = makeEl('button', 'applySolarSystem');
  applySolarSystem.textContent = 'Apply recommended system';
  applySolarSystem.disabled = true;

  // Solar options controls
  const solarScenario = makeEl('select', 'solarScenario');
  solarScenario.value = 'electrified';
  const solarObjective = makeEl('select', 'solarObjective');
  solarObjective.value = 'npv_eur';
  const solarFinancialBasis = makeEl('select', 'solarFinancialBasis');
  solarFinancialBasis.value = 'project';
  const solarBatteryMode = makeEl('select', 'solarBatteryMode');
  solarBatteryMode.value = 'joint';
  const solarBatteryCostMode = makeEl('select', 'solarBatteryCostMode');
  solarBatteryCostMode.value = 'purchased';
  const solarBatteryMin = makeEl('input', 'solarBatteryMin');
  solarBatteryMin.value = '0';
  const solarBatteryMax = makeEl('input', 'solarBatteryMax');
  solarBatteryMax.value = '20';
  const solarBatteryStep = makeEl('select', 'solarBatteryStep');
  solarBatteryStep.value = '5';
  const solarIncludeTheoretical = makeEl('select', 'solarIncludeTheoretical');
  solarIncludeTheoretical.value = 'true';
  const solarDeduction = makeEl('input', 'solarDeduction');
  solarDeduction.value = '0.5';
  const solarRepresentative = makeEl('select', 'solarRepresentative');
  solarRepresentative.value = 'median';
  const solarNpvSacrifice = makeEl('input', 'solarNpvSacrifice');
  solarNpvSacrifice.value = '5';
  const solarCoverageTarget = makeEl('input', 'solarCoverageTarget');
  solarCoverageTarget.value = '70';
  const solarRetainedProfit = makeEl('input', 'solarRetainedProfit');
  solarRetainedProfit.value = '95';

  // Solar Sensitivity controls
  makeEl('div', 'solarSensitivityResultsWrap');
  const runSolarSensitivity = makeEl('button', 'runSolarSensitivity');
  runSolarSensitivity.textContent = 'Run economic sensitivity';
  makeEl('div', 'solarSensitivityStatus');

  const sensExportDelta = makeEl('input', 'sensExportDelta');
  sensExportDelta.value = '20';
  const sensCostDelta = makeEl('input', 'sensCostDelta');
  sensCostDelta.value = '20';
  const sensConsDelta = makeEl('input', 'sensConsDelta');
  sensConsDelta.value = '10';
  const sensAprDelta = makeEl('input', 'sensAprDelta');
  sensAprDelta.value = '2.0';

  // Result render containers
  makeEl('div', 'solarChoiceCards');
  makeEl('div', 'solarAlternativesTable');
  makeEl('div', 'solarComparisonsTable');
  makeEl('div', 'solarSearchSummaryCards');
  makeEl('div', 'solarLimitationsList');
  makeEl('div', 'solarRoofPills');
  makeEl('div', 'solarPricingNote');
  makeEl('div', 'solarSizeCurveTitle');
  makeEl('div', 'solarRecCards');
  makeEl('div', 'solarSensitivityScenariosGrid');
  makeEl('div', 'solarSensitivityComparisonTable');

  // Battery helper elements
  makeEl('input', 'batQuote10').value = '';
  makeEl('input', 'batQuote15').value = '';
  makeEl('div', 'batResultsWrap');
  makeEl('button', 'runBatterySizing');

  // AC Heating elements
  const acHeatingDemandPctInput = makeEl('input', 'ac_heating_additional_demand_pct');
  acHeatingDemandPctInput.dataset.setting = 'ac_heating_additional_demand_pct';
  const acHeatingDemandPctError = makeEl('div', 'ac_heating_additional_demand_pct_error');
  acHeatingDemandPctError.hidden = true;

  makeEl('span', 'acScheduleUnionText');
  makeEl('div', 'acScheduleTimeline');
  makeEl('div', 'acScheduleRows');
  makeEl('div', 'acScheduleSummary');
  makeEl('button', 'acWindowAddBtn');

  // Generic app elements
  makeEl('div', 'status');
  makeEl('div', 'error').hidden = true;
  makeEl('div', 'latency');
  makeEl('select', 'weatherYear').options = [{ value: '2015' }];

  const doc = {
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, makeEl('div', id));
      }
      return elements.get(id);
    },
    createElement(tag) {
      return makeEl(tag);
    },
    querySelector(sel) {
      if (sel.startsWith('#')) return doc.getElementById(sel.slice(1));
      if (sel.startsWith('[data-setting="')) {
        const m = sel.match(/\[data-setting="([^"]+)"\]/);
        if (m) {
          for (const el of elements.values()) {
            if (el.dataset?.setting === m[1]) return el;
          }
        }
      }
      return null;
    },
    querySelectorAll(sel) {
      const out = [];
      for (const el of elements.values()) {
        if (sel.startsWith('.')) {
          if (el.classList.contains(sel.slice(1))) out.push(el);
        } else if (sel.startsWith('button[')) {
          const attr = sel.slice(7, -1);
          if (el.hasAttribute(attr) || el.dataset[attr.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())] !== undefined) {
            out.push(el);
          }
        } else if (sel.startsWith('[data-control="')) {
          const m = sel.match(/\[data-control="([^"]+)"\]/);
          if (m && el.dataset?.control === m[1]) out.push(el);
        }
      }
      return out;
    },
    addEventListener() {},
    removeEventListener() {}
  };

  const win = {
    addEventListener() {},
    removeEventListener() {},
    location: { hash: '' },
    matchMedia: () => ({ matches: false }),
    innerWidth: 1024,
    innerHeight: 768
  };
  win.window = win;
  win.document = doc;

  const evaluateCalls = [];
  const solarCalls = [];
  const pendingSolarCalls = [];
  let autoResolveSolar = true;

  const solarSensitivityCalls = [];
  const pendingSensitivityCalls = [];
  let autoResolveSensitivity = true;

  const batteryCalls = [];

  let pendingTimers = [];
  let timerSeq = 1;
  function fakeSetTimeout(fn, ms) {
    const tid = timerSeq++;
    pendingTimers.push({ id: tid, fn, ms });
    return tid;
  }
  function fakeClearTimeout(tid) {
    const idx = pendingTimers.findIndex(t => t.id === tid);
    if (idx !== -1) pendingTimers.splice(idx, 1);
  }
  async function flushTimers() {
    const list = [...pendingTimers];
    pendingTimers.length = 0;
    for (const t of list) {
      const res = t.fn();
      if (res && typeof res.then === 'function') await res;
    }
  }

  const fallbackDefaults = {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    timezone_mode: 'civil',
    ac_heating_additional_demand_pct: 12.5,
    ac_heating_windows: [{ id: 'w1', start_minute: 480, end_minute: 720 }],
    battery_kwh: 10,
    battery_mixed_sizes: false,
    battery_module_kwh: 5.0,
    panel_watts: 440.0,
    south_panels: 18,
    inverter_kw: 6.0
  };
  const fallbackRecords = [
    { name: 'load_scenario', unit: 'scenario', group: 'loads' },
    { name: 'planning_heat_pump', unit: 'boolean', group: 'heat_pump' },
    { name: 'heating_schedule_mode', unit: 'mode', group: 'loads' },
    { name: 'timezone_mode', unit: 'mapping' },
    { name: 'ac_heating_windows', unit: 'windows', group: 'loads' },
    { name: 'ac_heating_additional_demand_pct', unit: '%', group: 'loads' },
    { name: 'battery_kwh', unit: 'kWh', group: 'battery' },
    { name: 'battery_mixed_sizes', unit: 'boolean', group: 'battery' },
    { name: 'battery_module_kwh', unit: 'kWh', group: 'battery' },
    { name: 'panel_watts', unit: 'W', group: 'layout' },
    { name: 'south_panels', unit: 'panels', group: 'layout' },
    { name: 'inverter_kw', unit: 'kW', group: 'equipment' }
  ];

  const storageMap = new Map();
  const mockLocalStorage = {
    getItem: k => storageMap.get(k) || null,
    setItem: (k, v) => storageMap.set(k, String(v)),
    removeItem: k => storageMap.delete(k)
  };

  const ctx = {
    console,
    SharedScenarioSync,
    window: win,
    document: doc,
    structuredClone,
    localStorage: mockLocalStorage,
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    fetch: async (pathUrl, fetchOpts) => {
      if (pathUrl === '/api/schema') {
        return { ok: true, json: async () => ({ defaults: fallbackDefaults, records: fallbackRecords }) };
      }
      if (pathUrl === '/api/health') {
        return { ok: false, status: 500, json: async () => ({ error: 'init suppressed' }) };
      }
      if (pathUrl === '/api/state') {
        return { ok: true, json: async () => ({ revision: 'rev_0', settings: fallbackDefaults, quote: { line_items: [] } }) };
      }
      if (pathUrl === '/api/evaluate') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        evaluateCalls.push(body);
        return { ok: true, json: async () => makeEvaluateFixture(body.settings || {}) };
      }
      if (pathUrl === '/api/battery-sizing') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        batteryCalls.push({ url: pathUrl, body });
        return { ok: true, json: async () => ({ status: 'OK' }) };
      }
      if (pathUrl === '/api/solar-sizing') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        solarCalls.push({ url: pathUrl, body });
        const fixture = makeSolarSizingFixture({
          settings: body.settings,
          options: body.options
        });
        if (autoResolveSolar) {
          return { ok: true, json: async () => fixture };
        }
        return new Promise((resolve, reject) => {
          pendingSolarCalls.push({
            body,
            resolve: (val) => resolve({ ok: true, json: async () => val || fixture }),
            reject: (err) => reject(err)
          });
        });
      }
      if (pathUrl === '/api/solar-sizing-sensitivity') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        solarSensitivityCalls.push({ url: pathUrl, body });
        const fixture = makeSolarSensitivityFixture({
          settings: body.settings,
          options: body.options,
          sensitivity_options: body.sensitivity_options,
          financial_basis: body.options?.financial_basis || 'project'
        });
        if (autoResolveSensitivity) {
          return { ok: true, json: async () => fixture };
        }
        return new Promise((resolve, reject) => {
          pendingSensitivityCalls.push({
            body,
            resolve: (val) => resolve({ ok: true, json: async () => val || fixture }),
            reject: (err) => reject(err)
          });
        });
      }
      return { ok: true, json: async () => ({}) };
    }
  };

  vm.createContext(ctx);
  vm.runInContext(appSource, ctx);

  return {
    ctx,
    elements,
    evaluateCalls,
    solarCalls,
    pendingSolarCalls,
    get autoResolveSolar() { return autoResolveSolar; },
    set autoResolveSolar(val) { autoResolveSolar = val; },
    solarSensitivityCalls,
    pendingSensitivityCalls,
    get autoResolveSensitivity() { return autoResolveSensitivity; },
    set autoResolveSensitivity(val) { autoResolveSensitivity = val; },
    batteryCalls,
    flushTimers,
    hasPendingTimer: () => pendingTimers.length > 0,
    get pendingTimers() { return pendingTimers; },
    run: (code) => vm.runInContext(code, ctx)
  };
}

function initDefaultValidState(app) {
  const { run, elements } = app;
  run(`
    const defs = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil',
      ac_heating_additional_demand_pct: 12.5,
      ac_heating_windows: [{ id: 'w1', start_minute: 480, end_minute: 720 }],
      battery_kwh: 10,
      battery_mixed_sizes: false,
      battery_module_kwh: 5.0,
      panel_watts: 440.0,
      south_panels: 18,
      inverter_kw: 6.0
    };
    schema = {
      defaults: defs,
      records: [
        { name: 'load_scenario', unit: 'scenario', group: 'loads' },
        { name: 'planning_heat_pump', unit: 'boolean', group: 'heat_pump' },
        { name: 'heating_schedule_mode', unit: 'mode', group: 'loads' },
        { name: 'timezone_mode', unit: 'mapping' },
        { name: 'ac_heating_windows', unit: 'windows', group: 'loads' },
        { name: 'ac_heating_additional_demand_pct', unit: '%', group: 'loads' },
        { name: 'battery_kwh', unit: 'kWh', group: 'battery' },
        { name: 'battery_mixed_sizes', unit: 'boolean', group: 'battery' },
        { name: 'battery_module_kwh', unit: 'kWh', group: 'battery' },
        { name: 'panel_watts', unit: 'W', group: 'layout' },
        { name: 'south_panels', unit: 'panels', group: 'layout' },
        { name: 'inverter_kw', unit: 'kW', group: 'equipment' }
      ]
    };
    settings = deepClone(schema.defaults);
    quote = { line_items: [] };
    acScheduleDraft = null;
    acScheduleDraftInvalid = false;
    acDemandPctDraft = null;
    acDemandPctError = null;
    solarData = null;
    solarAcceptedFingerprint = null;
    solarInFlight = false;
    solarSensitivityData = null;
    solarSensitivityAcceptedFingerprint = null;
    solarSensitivityInFlight = false;
  `);

  const solarScenario = elements.get('solarScenario');
  if (solarScenario) solarScenario.value = 'electrified';
  const solarObjective = elements.get('solarObjective');
  if (solarObjective) solarObjective.value = 'npv_eur';
  const solarBatteryMode = elements.get('solarBatteryMode');
  if (solarBatteryMode) solarBatteryMode.value = 'joint';
  const solarBatteryMin = elements.get('solarBatteryMin');
  if (solarBatteryMin) solarBatteryMin.value = '0';
  const solarBatteryMax = elements.get('solarBatteryMax');
  if (solarBatteryMax) solarBatteryMax.value = '20';
  const solarNpvSacrifice = elements.get('solarNpvSacrifice');
  if (solarNpvSacrifice) solarNpvSacrifice.value = '5';
  const solarCoverageTarget = elements.get('solarCoverageTarget');
  if (solarCoverageTarget) solarCoverageTarget.value = '70';
  const solarRetainedProfit = elements.get('solarRetainedProfit');
  if (solarRetainedProfit) solarRetainedProfit.value = '95';

  const sensExport = elements.get('sensExportDelta');
  if (sensExport) sensExport.value = '20';
  const sensCost = elements.get('sensCostDelta');
  if (sensCost) sensCost.value = '20';
  const sensCons = elements.get('sensConsDelta');
  if (sensCons) sensCons.value = '10';
  const sensApr = elements.get('sensAprDelta');
  if (sensApr) sensApr.value = '2.0';
}

test('fresh valid solar sizing and sensitivity runs dispatch production requests and preserve structured windows and arbitrary percentage', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Set distinct structured windows and arbitrary fractional percentage
  run(`
    settings.ac_heating_windows = [
      { id: 'morning', start_minute: 480, end_minute: 720 },
      { id: 'evening', start_minute: 960, end_minute: 1200 }
    ];
    settings.ac_heating_additional_demand_pct = 12.345;
    acScheduleDraft = null;
    acScheduleDraftInvalid = false;
    acDemandPctDraft = null;
    acDemandPctError = null;
  `);

  const btnSizing = elements.get('runSolarSizing');
  const btnSens = elements.get('runSolarSensitivity');
  const wrapSizing = elements.get('solarResultsWrap');
  const wrapSens = elements.get('solarSensitivityResultsWrap');
  const applyBtn = elements.get('applySolarSystem');

  // Trigger production runSolarSizingAnalysis
  await run("runSolarSizingAnalysis()");

  assert.equal(solarCalls.length, 1, 'solar sizing API called once');
  const sizingBody = solarCalls[0].body;

  // Verify full structured array of operating windows is preserved
  assert.equal(sizingBody.settings.ac_heating_windows.length, 2, '2 operating windows sent');
  assert.equal(sizingBody.settings.ac_heating_windows[0].id, 'morning');
  assert.equal(sizingBody.settings.ac_heating_windows[0].start_minute, 480);
  assert.equal(sizingBody.settings.ac_heating_windows[0].end_minute, 720);
  assert.equal(sizingBody.settings.ac_heating_windows[1].id, 'evening');
  assert.equal(sizingBody.settings.ac_heating_windows[1].start_minute, 960);
  assert.equal(sizingBody.settings.ac_heating_windows[1].end_minute, 1200);

  // Verify arbitrary percentage value is preserved without rounding
  assert.equal(sizingBody.settings.ac_heating_additional_demand_pct, 12.345, 'percentage sent exactly');

  // Results accepted and stale overlay cleared
  assert.equal(wrapSizing.classList.contains('stale'), false, 'results wrap not stale');
  assert.equal(run("isSolarSizingMatching()"), true, 'solar sizing is matching');
  assert.equal(btnSizing.disabled, false, 'button re-enabled');
  assert.equal(run("solarInFlight"), false, 'in-flight flag cleared');
  assert.equal(applyBtn.disabled, false, 'apply button enabled');

  // Trigger production runSolarSensitivityAnalysis
  await run("runSolarSensitivityAnalysis()");

  assert.equal(solarSensitivityCalls.length, 1, 'sensitivity API called once');
  const sensBody = solarSensitivityCalls[0].body;

  assert.equal(sensBody.settings.ac_heating_windows.length, 2, 'sensitivity preserved windows');
  assert.equal(sensBody.settings.ac_heating_additional_demand_pct, 12.345, 'sensitivity preserved percentage');
  assert.equal(sensBody.sensitivity_options.export_revenue_delta_pct, 20);
  assert.equal(sensBody.sensitivity_options.installed_cost_delta_pct, 20);
  assert.equal(sensBody.sensitivity_options.household_consumption_delta_pct, 10);
  assert.equal(sensBody.sensitivity_options.financing_apr_delta_pp, 2.0);

  assert.equal(wrapSens.classList.contains('stale'), false, 'sensitivity wrap not stale');
  assert.equal(run("isSolarSensitivityMatching()"), true, 'sensitivity is matching');
  assert.equal(btnSens.disabled, false, 'sensitivity button re-enabled');
  assert.equal(run("solarSensitivityInFlight"), false, 'in-flight flag cleared');
});

test('active invalid schedule draft blocks production solar sizing run in electrified mode and preserves visible stale state', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls } = app;
  initDefaultValidState(app);

  // First obtain valid accepted results
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1);
  const wrap = elements.get('solarResultsWrap');
  const btn = elements.get('runSolarSizing');
  const applyBtn = elements.get('applySolarSystem');
  assert.equal(wrap.classList.contains('stale'), false);
  assert.equal(applyBtn.disabled, false);

  // User enters invalid schedule draft via production handleAcScheduleTimeInput
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), true, 'schedule draft is invalid');
  assert.equal(run("shouldBlockAcScheduleRecompute(settings)"), true, 'blocks AC recompute');
  assert.equal(wrap.classList.contains('stale'), true, 'retained results immediately marked stale');
  assert.equal(run("isSolarSizingMatching()"), false, 'matching is false');

  // User attempts to click "Run sizing analysis" while draft is invalid
  await run("runSolarSizingAnalysis()");

  // Guard MUST block execution and not dispatch backend request with hidden last-valid values
  assert.equal(solarCalls.length, 1, 'no new API call dispatched while draft is invalid');
  assert.equal(wrap.classList.contains('stale'), true, 'retained results remain visibly outdated');
  assert.equal(btn.disabled, false, 'button is not stuck disabled');
  assert.equal(run("solarInFlight"), false, 'in-flight flag is false');
  assert.equal(elements.get('solarStatus').textContent, 'Cannot run solar sizing with invalid heating draft.');
  assert.equal(applyBtn.disabled, true, 'apply button stays disabled');
});

test('active invalid percentage draft blocks production solar sizing and sensitivity runs in electrified mode', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Obtain valid initial results
  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarCalls.length, 1);
  assert.equal(solarSensitivityCalls.length, 1);

  const wrapSizing = elements.get('solarResultsWrap');
  const wrapSens = elements.get('solarSensitivityResultsWrap');
  const btnSizing = elements.get('runSolarSizing');
  const btnSens = elements.get('runSolarSensitivity');

  // User enters invalid percentage via production handleAcHeatingDemandPctInput
  run("handleAcHeatingDemandPctInput('-5')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), true, 'percentage draft is invalid');
  assert.equal(wrapSizing.classList.contains('stale'), true, 'sizing marked stale on invalid percentage');
  assert.equal(wrapSens.classList.contains('stale'), true, 'sensitivity marked stale on invalid percentage');
  assert.equal(run("isSolarSizingMatching()"), false);
  assert.equal(run("isSolarSensitivityMatching()"), false);

  // Click Run sizing analysis
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'sizing call blocked for negative percentage');
  assert.equal(wrapSizing.classList.contains('stale'), true, 'wrap remains stale');
  assert.equal(btnSizing.disabled, false, 'button not stuck disabled');
  assert.equal(elements.get('solarStatus').textContent, 'Cannot run solar sizing with invalid heating draft.');

  // Click Run economic sensitivity
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'sensitivity call blocked for negative percentage');
  assert.equal(wrapSens.classList.contains('stale'), true, 'sensitivity wrap remains stale');
  assert.equal(btnSens.disabled, false, 'button not stuck disabled');
  assert.equal(elements.get('solarSensitivityStatus').textContent, 'Cannot run solar sensitivity with invalid heating draft.');

  // Also test non-numeric percentage input
  run("handleAcHeatingDemandPctInput('abc')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), true);
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'call blocked for non-numeric percentage');
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'sensitivity call blocked for non-numeric percentage');
});

test('same-value correction clears invalid draft and allows production runs to recover', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Valid runs
  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarCalls.length, 1);
  assert.equal(solarSensitivityCalls.length, 1);

  const wrapSizing = elements.get('solarResultsWrap');
  const wrapSens = elements.get('solarSensitivityResultsWrap');

  // Invalidate percentage
  run("handleAcHeatingDemandPctInput('invalid_pct')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), true);
  assert.equal(wrapSizing.classList.contains('stale'), true);
  assert.equal(wrapSens.classList.contains('stale'), true);

  // Blocked runs
  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarCalls.length, 1);
  assert.equal(solarSensitivityCalls.length, 1);

  // Correct back to same canonical value (12.5)
  run("handleAcHeatingDemandPctInput('12.5')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), false, 'same-value correction clears error');

  // Run sizing analysis -> recovers cleanly
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 2, 'fresh API call dispatched on recovery');
  assert.equal(wrapSizing.classList.contains('stale'), false, 'stale class removed on fresh valid result');
  assert.equal(run("isSolarSizingMatching()"), true);

  // Run sensitivity analysis -> recovers cleanly
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 2, 'fresh sensitivity API call dispatched on recovery');
  assert.equal(wrapSens.classList.contains('stale'), false, 'sensitivity wrap active');
  assert.equal(run("isSolarSensitivityMatching()"), true);

  // Now test same-value correction on schedule draft
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), true);
  assert.equal(wrapSizing.classList.contains('stale'), true);
  assert.equal(wrapSens.classList.contains('stale'), true);

  // Blocked
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 2);

  // Correct schedule start back to 08:00 (480 mins)
  run("handleAcScheduleTimeInput('w1', 'start', '08:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), false, 'schedule draft error cleared');

  // Run sizing analysis recovers
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 3, 'fresh API call dispatched');
  assert.equal(wrapSizing.classList.contains('stale'), false, 'wrap active');
  assert.equal(run("isSolarSizingMatching()"), true);
});

test('changed canonical schedule and percentage recovery submits new values to backend', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarCalls.length, 1);
  assert.equal(solarSensitivityCalls.length, 1);

  // Change canonical schedule window to 06:00–10:00 (360–600 mins)
  run("handleAcScheduleTimeInput('w1', 'start', '06:00')");
  run("handleAcScheduleTimeInput('w1', 'end', '10:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), false);

  // Change canonical percentage to 33.75%
  run("handleAcHeatingDemandPctInput('33.75')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), false);

  const wrap = elements.get('solarResultsWrap');
  assert.equal(wrap.classList.contains('stale'), true, 'marked stale by setting changes');

  // Run sizing analysis with new canonical values
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 2);
  const sizingPayload = solarCalls[1].body;

  assert.equal(sizingPayload.settings.ac_heating_windows[0].start_minute, 360);
  assert.equal(sizingPayload.settings.ac_heating_windows[0].end_minute, 600);
  assert.equal(sizingPayload.settings.ac_heating_additional_demand_pct, 33.75);
  assert.equal(wrap.classList.contains('stale'), false);

  // Run sensitivity analysis with new canonical values
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 2);
  const sensPayload = solarSensitivityCalls[1].body;

  assert.equal(sensPayload.settings.ac_heating_windows[0].start_minute, 360);
  assert.equal(sensPayload.settings.ac_heating_windows[0].end_minute, 600);
  assert.equal(sensPayload.settings.ac_heating_additional_demand_pct, 33.75);
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), false);
});

test('scopes: baseline sizing scenario ignores inactive AC drafts, but electrified sizing with baseline settings blocks', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Case A: solarScenario is 'baseline', settings.load_scenario is 'electrified'
  elements.get('solarScenario').value = 'baseline';

  // Enter invalid schedule draft and invalid percentage
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  run("handleAcHeatingDemandPctInput('-10')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), true);
  assert.equal(run("hasInvalidAcDemandPctDraft()"), true);

  // Baseline sizing does NOT model AC heating, so it CAN run
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'baseline sizing dispatches despite inactive AC draft');
  assert.equal(solarCalls[0].body.options.load_scenario, 'baseline');
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);

  // Baseline sensitivity also runs
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'baseline sensitivity dispatches');
  assert.equal(solarSensitivityCalls[0].body.options.load_scenario, 'baseline');
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), false);

  // Case B: settings.load_scenario is 'baseline', but solarScenario is 'electrified'
  run("settings.load_scenario = 'baseline';");
  elements.get('solarScenario').value = 'electrified';

  // The draft is still invalid. Because solar sizing runs electrified, AC drafts DO govern!
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'electrified sizing BLOCKED when draft is invalid, even if settings.load_scenario is baseline');
  assert.equal(elements.get('solarStatus').textContent, 'Cannot run solar sizing with invalid heating draft.');
  assert.equal(elements.get('runSolarSizing').disabled, false);

  // Electrified sensitivity is also blocked
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'electrified sensitivity BLOCKED');
  assert.equal(elements.get('solarSensitivityStatus').textContent, 'Cannot run solar sensitivity with invalid heating draft.');
  assert.equal(elements.get('runSolarSensitivity').disabled, false);
});

test('scopes: air-to-water heat pump physics separates AC drafts from solar sizing and sensitivity', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Enable heat pump
  run("settings.planning_heat_pump = true;");
  assert.equal(run("isAcScheduleGoverningActiveHeating(settings)"), false);
  assert.equal(run("isAcDemandPctGoverningActiveHeating(settings)"), false);

  // Enter invalid AC drafts
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  run("handleAcHeatingDemandPctInput('-99')");

  // Sizing run is NOT blocked because air-to-water HP is active
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'HP sizing dispatches cleanly');
  assert.equal(solarCalls[0].body.settings.planning_heat_pump, true);
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);

  // Sensitivity run is NOT blocked
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'HP sensitivity dispatches cleanly');
  assert.equal(solarSensitivityCalls[0].body.settings.planning_heat_pump, true);
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), false);
});

test('scopes: continuous mode exempts schedule draft text but enforces percentage draft', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Set continuous mode
  run("settings.heating_schedule_mode = 'continuous';");
  assert.equal(run("isAcScheduleGoverningActiveHeating(settings)"), false, 'schedule does not govern continuous');
  assert.equal(run("isAcDemandPctGoverningActiveHeating(settings)"), true, 'percentage governs continuous');

  // Invalid schedule text does NOT block continuous solar sizing
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'continuous mode runs despite schedule draft error');
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);

  // Invalid schedule text does NOT block continuous solar sensitivity
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'continuous sensitivity runs despite schedule error');
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), false);

  // Invalid percentage draft DOES block continuous solar sizing
  run("handleAcHeatingDemandPctInput('-20')");
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 1, 'continuous mode BLOCKED by invalid percentage in sizing');
  assert.equal(elements.get('solarStatus').textContent, 'Cannot run solar sizing with invalid heating draft.');

  // Invalid percentage draft DOES block continuous solar sensitivity
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 1, 'continuous mode BLOCKED by invalid percentage in sensitivity');
  assert.equal(elements.get('solarSensitivityStatus').textContent, 'Cannot run solar sensitivity with invalid heating draft.');
});

test('option and sensitivity option changes mark results stale and update request identities', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Initial valid runs
  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarCalls.length, 1);
  assert.equal(solarSensitivityCalls.length, 1);

  const wrapSizing = elements.get('solarResultsWrap');
  const wrapSens = elements.get('solarSensitivityResultsWrap');
  assert.equal(wrapSizing.classList.contains('stale'), false);
  assert.equal(wrapSens.classList.contains('stale'), false);
  assert.equal(run("isSolarSizingMatching()"), true);
  assert.equal(run("isSolarSensitivityMatching()"), true);

  // Change solarBatteryMax from 20 to 30
  elements.get('solarBatteryMax').value = '30';
  elements.get('solarBatteryMax').dispatchEvent({ type: 'change' });

  assert.equal(wrapSizing.classList.contains('stale'), true, 'sizing wrap stale on option change');
  assert.equal(wrapSens.classList.contains('stale'), true, 'sensitivity wrap also stale on sizing option change');
  assert.equal(run("isSolarSizingMatching()"), false);
  assert.equal(run("isSolarSensitivityMatching()"), false);

  // Re-run solar sizing
  await run("runSolarSizingAnalysis()");
  assert.equal(solarCalls.length, 2);
  assert.equal(solarCalls[1].body.options.battery_max_kwh, 30);
  assert.equal(wrapSizing.classList.contains('stale'), false);
  assert.equal(run("isSolarSizingMatching()"), true);

  // Now change sensitivity option (sensCostDelta from 20 to 35)
  elements.get('sensCostDelta').value = '35';
  elements.get('sensCostDelta').dispatchEvent({ type: 'change' });

  // Sizing wrap remains valid because only sensitivity stress parameters changed
  assert.equal(wrapSizing.classList.contains('stale'), false, 'sizing not stale on sensitivity stress change');
  assert.equal(run("isSolarSizingMatching()"), true);

  // Sensitivity wrap is marked stale
  assert.equal(wrapSens.classList.contains('stale'), true, 'sensitivity wrap stale on sensCostDelta change');
  assert.equal(run("isSolarSensitivityMatching()"), false);

  // Re-run sensitivity
  await run("runSolarSensitivityAnalysis()");
  assert.equal(solarSensitivityCalls.length, 2);
  assert.equal(solarSensitivityCalls[1].body.sensitivity_options.installed_cost_delta_pct, 35);
  assert.equal(wrapSens.classList.contains('stale'), false);
  assert.equal(run("isSolarSensitivityMatching()"), true);
});

test('in-flight race: edits during deferred request reject stale success response and stale error', async () => {
  const app = createAppContext();
  const { run, elements, pendingSolarCalls, pendingSensitivityCalls } = app;
  initDefaultValidState(app);

  // Initial runs
  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), false);

  app.autoResolveSolar = false;
  app.autoResolveSensitivity = false;

  // Case A: Schedule edit during in-flight sizing request
  const pA = run("runSolarSizingAnalysis()");
  assert.equal(pendingSolarCalls.length, 1);
  assert.equal(run("solarInFlight"), true);

  // Edit schedule while request A is in flight
  run("handleAcScheduleTimeInput('w1', 'start', '07:00')");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true);

  // Request A completes
  pendingSolarCalls[0].resolve(makeSolarSizingFixture());
  await pA;

  // Stale response must NOT clear stale status
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true, 'stale response rejected');
  assert.equal(run("isSolarSizingMatching()"), false);

  // Case B: Percentage edit during in-flight sensitivity request
  const pB = run("runSolarSensitivityAnalysis()");
  assert.equal(pendingSensitivityCalls.length, 1);
  assert.equal(run("solarSensitivityInFlight"), true);

  run("handleAcHeatingDemandPctInput('40')");
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), true);

  pendingSensitivityCalls[0].resolve(makeSolarSensitivityFixture());
  await pB;
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), true, 'stale sensitivity rejected');
  assert.equal(run("isSolarSensitivityMatching()"), false);

  // Case C: Invalid draft entered during in-flight sizing request
  const pC = run("runSolarSizingAnalysis()");
  assert.equal(pendingSolarCalls.length, 2);
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  pendingSolarCalls[1].resolve(makeSolarSizingFixture());
  await pC;
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true, 'rejected on active invalid draft');

  // Clear draft
  run("handleAcScheduleTimeInput('w1', 'start', '08:00')");

  // Case D: Stale server error does not overwrite solarStatus or show stale alert
  const pD = run("runSolarSizingAnalysis()");
  assert.equal(pendingSolarCalls.length, 3);
  // Modify option while in flight
  elements.get('solarObjective').value = 'autarky';
  elements.get('solarObjective').dispatchEvent({ type: 'change' });
  elements.get('solarStatus').textContent = 'Inputs changed · Click Run sizing analysis to update';

  // Server returns 500 error for stale request D
  pendingSolarCalls[2].reject(new Error('Internal 500 Error'));
  await pD;

  // Error is suppressed because request was stale
  assert.equal(elements.get('solarStatus').textContent, 'Inputs changed · Click Run sizing analysis to update');
  assert.equal(run("solarInFlight"), false);
  assert.equal(elements.get('runSolarSizing').disabled, false);

  // Case E: Stale error in sensitivity does not overwrite solarSensitivityStatus
  const pE = run("runSolarSensitivityAnalysis()");
  assert.equal(pendingSensitivityCalls.length, 2);
  elements.get('sensExportDelta').value = '40';
  elements.get('sensExportDelta').dispatchEvent({ type: 'change' });
  elements.get('solarSensitivityStatus').textContent = 'Assumptions or inputs changed · Click Run economic sensitivity to update';

  pendingSensitivityCalls[1].reject(new Error('Internal 500 Error in Sensitivity'));
  await pE;

  assert.equal(elements.get('solarSensitivityStatus').textContent, 'Assumptions or inputs changed · Click Run economic sensitivity to update');
  assert.equal(run("solarSensitivityInFlight"), false);
  assert.equal(elements.get('runSolarSensitivity').disabled, false);
});

test('in-flight race: newer request supersedes older request', async () => {
  const app = createAppContext();
  const { run, elements, pendingSolarCalls, pendingSensitivityCalls } = app;
  initDefaultValidState(app);

  await run("runSolarSizingAnalysis()");
  await run("runSolarSensitivityAnalysis()");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);

  app.autoResolveSolar = false;
  app.autoResolveSensitivity = false;

  // Sizing: Request 1
  const p1 = run("runSolarSizingAnalysis()");
  assert.equal(pendingSolarCalls.length, 1);

  // Sizing: Request 2
  elements.get('solarBatteryMax').value = '25';
  elements.get('solarBatteryMax').dispatchEvent({ type: 'change' });
  const p2 = run("runSolarSizingAnalysis()");
  assert.equal(pendingSolarCalls.length, 2);

  // Request 1 resolves with max 20
  pendingSolarCalls[0].resolve(makeSolarSizingFixture({ options: { battery_max_kwh: 20 } }));
  await p1;
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true, 'request 1 ignored');

  // Request 2 resolves with max 25
  pendingSolarCalls[1].resolve(makeSolarSizingFixture({ options: { battery_max_kwh: 25 } }));
  await p2;
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false, 'request 2 accepted');
  assert.equal(run("isSolarSizingMatching()"), true);

  // Sensitivity: Request 1
  const s1 = run("runSolarSensitivityAnalysis()");
  assert.equal(pendingSensitivityCalls.length, 1);

  // Sensitivity: Request 2
  elements.get('sensCostDelta').value = '30';
  elements.get('sensCostDelta').dispatchEvent({ type: 'change' });
  const s2 = run("runSolarSensitivityAnalysis()");
  assert.equal(pendingSensitivityCalls.length, 2);

  pendingSensitivityCalls[0].resolve(makeSolarSensitivityFixture({ sensitivity_options: { installed_cost_delta_pct: 20 } }));
  await s1;
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), true, 'sensitivity request 1 ignored');

  pendingSensitivityCalls[1].resolve(makeSolarSensitivityFixture({ sensitivity_options: { installed_cost_delta_pct: 30 } }));
  await s2;
  assert.equal(elements.get('solarSensitivityResultsWrap').classList.contains('stale'), false, 'sensitivity request 2 accepted');
  assert.equal(run("isSolarSensitivityMatching()"), true);
});

test('remote changes via applySharedState mark results stale and reject in-flight requests', async () => {
  const app = createAppContext();
  const { run, elements, pendingSolarCalls } = app;
  initDefaultValidState(app);

  await run("runSolarSizingAnalysis()");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);
  assert.equal(elements.get('applySolarSystem').disabled, false);

  app.autoResolveSolar = false;

  // Start in-flight request
  const p = run("runSolarSizingAnalysis()");
  assert.equal(pendingSolarCalls.length, 1);

  // Remote patch arrives via applySharedState
  run(`
    applySharedState({
      settings: {
        ...settings,
        ac_heating_additional_demand_pct: 25.0
      },
      quote: quote,
      defaults: schema.defaults,
      records: schema.records
    });
  `);

  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true, 'remote state marks wrap stale');
  assert.equal(elements.get('applySolarSystem').disabled, true, 'apply button disabled on remote state');
  assert.equal(run("isSolarSizingMatching()"), false);

  // Older in-flight response arrives
  pendingSolarCalls[0].resolve(makeSolarSizingFixture());
  await p;

  // Must remain stale!
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true);
  assert.equal(elements.get('applySolarSystem').disabled, true);
  assert.equal(run("isSolarSizingMatching()"), false);
});

test('stale apply controls and candidate apply guards', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);

  await run("runSolarSizingAnalysis()");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);
  const applyBtn = elements.get('applySolarSystem');
  assert.equal(applyBtn.disabled, false);

  // When wrap is stale from an input change
  elements.get('solarObjective').value = 'autarky';
  elements.get('solarObjective').dispatchEvent({ type: 'change' });
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true);
  assert.equal(applyBtn.disabled, true, 'main apply button disabled when stale');

  // Attempting to invoke applyCandidate directly when stale is guarded
  run(`{
    const rec = getMainRecommendation(solarData);
    applySolarCandidate(rec);
  }`);
  assert.equal(elements.get('solarApplyStatus').textContent, 'Cannot apply: analysis is stale or running. Re-run analysis first.');

  // Reset objective back to npv_eur and re-run
  elements.get('solarObjective').value = 'npv_eur';
  await run("runSolarSizingAnalysis()");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), false);
  assert.equal(applyBtn.disabled, false);

  // Invalidate AC draft
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(elements.get('solarResultsWrap').classList.contains('stale'), true);
  assert.equal(applyBtn.disabled, true);

  run(`{
    const rec = getMainRecommendation(solarData);
    applySolarCandidate(rec);
  }`);
  assert.equal(elements.get('solarApplyStatus').textContent, 'Cannot apply: analysis is stale or running. Re-run analysis first.');
});

test('invalidation covers draft edits when main scenario is baseline but sizing scenario is electrified', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);

  // Main settings scenario is baseline, but solarScenario is electrified
  run("settings.load_scenario = 'baseline';");
  elements.get('solarScenario').value = 'electrified';

  // Obtain valid sizing result
  await run("runSolarSizingAnalysis()");
  const wrap = elements.get('solarResultsWrap');
  assert.equal(wrap.classList.contains('stale'), false);
  assert.equal(run("isSolarSizingMatching()"), true);

  // User edits AC schedule draft with invalid text
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), true);

  // Invalidation MUST mark solar sizing stale even though settings.load_scenario is baseline!
  assert.equal(wrap.classList.contains('stale'), true, 'solar sizing wrap invalidated by draft when solarScenario is active');
  assert.equal(run("isSolarSizingMatching()"), false);

  // Clear schedule draft
  run("handleAcScheduleTimeInput('w1', 'start', '08:00')");

  // Re-run
  await run("runSolarSizingAnalysis()");
  assert.equal(wrap.classList.contains('stale'), false);

  // User edits percentage draft with invalid text
  run("handleAcHeatingDemandPctInput('-15')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), true);
  assert.equal(wrap.classList.contains('stale'), true, 'solar sizing wrap invalidated by percentage draft when solarScenario is active');
  assert.equal(run("isSolarSizingMatching()"), false);
});

test('visible option validation errors before dispatch are displayed and not swallowed', async () => {
  const app = createAppContext();
  const { run, elements, solarCalls, solarSensitivityCalls } = app;
  initDefaultValidState(app);

  // Solar sizing: invalid NPV sacrifice percentage (-5)
  elements.get('solarNpvSacrifice').value = '-5';
  await run("runSolarSizingAnalysis()");

  assert.equal(solarCalls.length, 0, 'no network call made when option validation fails');
  assert.match(elements.get('solarStatus').textContent, /Error: Allowed NPV sacrifice percentage must be a finite number between 0 and 100%/);
  assert.equal(elements.get('runSolarSizing').disabled, false, 'sizing button re-enabled');

  // Fix NPV sacrifice
  elements.get('solarNpvSacrifice').value = '5';

  // Solar sizing: battery min > battery max
  elements.get('solarBatteryMin').value = '25';
  elements.get('solarBatteryMax').value = '20';
  await run("runSolarSizingAnalysis()");

  assert.equal(solarCalls.length, 0);
  assert.match(elements.get('solarStatus').textContent, /Error: Storage minimum capacity cannot exceed maximum capacity/);
  assert.equal(elements.get('runSolarSizing').disabled, false);

  // Restore valid battery min/max
  elements.get('solarBatteryMin').value = '0';
  elements.get('solarBatteryMax').value = '20';

  // Solar sensitivity: invalid export delta (> 100)
  elements.get('sensExportDelta').value = '150';
  await run("runSolarSensitivityAnalysis()");

  assert.equal(solarSensitivityCalls.length, 0, 'no sensitivity network call when option validation fails');
  assert.match(elements.get('solarSensitivityStatus').textContent, /Error: Export price reduction percentage must be between 0% and 100%/);
  assert.equal(elements.get('runSolarSensitivity').disabled, false, 'sensitivity button re-enabled');
});

