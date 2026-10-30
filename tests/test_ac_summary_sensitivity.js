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
      layout: { kwp: 6, panel_count: 14 },
      metrics: { npv_eur: 5000, autarky: 0.65, self_consumption: 0.55 },
      finance: {
        capex_eur: 8000,
        conversion_capex_eur: 0,
        annual_deduction_eur: 400,
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
        },
        solar_equity: {
          npv: { median: 5200, min: 3200, max: 7200 },
          simple_payback: { median: 8.5, recovered_weather_years: 19 },
          discounted_payback: { median: 10.5, recovered_weather_years: 19 }
        },
        package_equity: {
          npv: { median: 4700, min: 2700, max: 6700 },
          simple_payback: { median: 9.5, recovered_weather_years: 19 },
          discounted_payback: { median: 11.5, recovered_weather_years: 19 }
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
      },
      annual_cashflows: [
        {
          year: 1,
          pv_incremental_range: [200, 300],
          equity_range: [180, 280],
          whole_package_range: [220, 320],
          debt_service: 0,
          loan_interest: 0,
          tax_deduction: 400,
          replacement: 0
        }
      ]
    }
  };
}

function makeSensitivityFixture(options = {}) {
  const baseNpv = options.base_npv_eur ?? 5000;
  return {
    scope: options.scope || 'solar',
    cash_mode: options.cash_mode || 'cash',
    base_npv_eur: baseNpv,
    base_npv: { median: baseNpv, min: 3000, max: 7000 },
    electricity_price: {
      parameter: 'retail_commodity',
      unit: 'EUR/kWh',
      base: { price: 0.12, npv_eur: baseNpv },
      minus_20_pct: { price: 0.096, npv_eur: baseNpv - 800, delta_npv: -800 },
      plus_20_pct: { price: 0.144, npv_eur: baseNpv + 800, delta_npv: 800 }
    },
    discount_rate: {
      parameter: 'real_discount_rate',
      unit: '%',
      base: { rate: 0.03, npv_eur: baseNpv },
      minus_2_pct: { rate: 0.01, npv_eur: baseNpv + 1200, delta_npv: 1200 },
      plus_2_pct: { rate: 0.05, npv_eur: baseNpv - 900, delta_npv: -900 }
    },
    weather: {
      worst_year: { year: 2017, npv_eur: baseNpv - 1200, delta_npv: -1200 },
      best_year: { year: 2021, npv_eur: baseNpv + 1100, delta_npv: 1100 }
    },
    export_price_ramp: {
      parameter: 'actual_price_future_factor',
      unit: 'multiplier',
      base: { factor: 1.0, npv_eur: baseNpv },
      ramp_down: { factor: 0.75, npv_eur: baseNpv - 350, delta_npv: -350 },
      ramp_up: { factor: 1.25, npv_eur: baseNpv + 350, delta_npv: 350 }
    },
    capex: {
      parameter: 'capex',
      unit: 'EUR',
      base: { npv_eur: baseNpv },
      plus_10_pct: { npv_eur: baseNpv - 700, delta_npv: -700 },
      minus_10_pct: { npv_eur: baseNpv + 700, delta_npv: 700 }
    },
    replacement_cost: {
      parameter: 'replacement_cost',
      unit: 'EUR',
      base: { npv_eur: baseNpv },
      plus_20_pct: { npv_eur: baseNpv - 260, delta_npv: -260 },
      minus_20_pct: { npv_eur: baseNpv + 260, delta_npv: 260 }
    },
    gas_price: {
      parameter: 'gas_commodity',
      unit: 'EUR/Smc',
      base: { price: 0.47, npv_eur: baseNpv },
      minus_20_pct: { price: 0.376, npv_eur: baseNpv, delta_npv: 0 },
      plus_20_pct: { price: 0.564, npv_eur: baseNpv, delta_npv: 0 }
    },
    battery_degradation: {
      parameter: 'battery_calendar_fade',
      unit: 'fraction/year',
      base: { fade: 0.015, npv_eur: baseNpv },
      higher_fade: { npv_eur: baseNpv - 150, delta_npv: -150 },
      lower_fade: { npv_eur: baseNpv + 150, delta_npv: 150 }
    },
    heat_pump_cop: {
      parameter: 'scop,dhw_scop',
      unit: 'kWh heat/kWh electricity',
      base: { scop: 4.0, dhw_scop: 3.0, npv_eur: baseNpv },
      minus_20_pct: { scop: 3.2, dhw_scop: 2.4, npv_eur: baseNpv - 350, delta_npv: -350 },
      plus_20_pct: { scop: 4.8, dhw_scop: 3.6, npv_eur: baseNpv + 350, delta_npv: 350 },
      delta_low: -350,
      delta_high: 350
    },
    real_energy_price_escalation: {
      parameter: 'energy_real_escalation',
      unit: 'fraction/year',
      base: { rate: 0.0, npv_eur: baseNpv },
      minus_1_pct: { rate: -0.01, npv_eur: baseNpv - 220, delta_npv: -220 },
      plus_1_pct: { rate: 0.01, npv_eur: baseNpv + 220, delta_npv: 220 },
      delta_low: -220,
      delta_high: 220
    },
    export_price_level: {
      parameter: 'export_market_price',
      unit: 'EUR/kWh',
      base: { price: 0.128, npv_eur: baseNpv },
      minus_20_pct: { price: 0.1024, npv_eur: baseNpv - 180, delta_npv: -180 },
      plus_20_pct: { price: 0.1536, npv_eur: baseNpv + 180, delta_npv: 180 },
      delta_low: -180,
      delta_high: 180
    },
    loan_apr: {
      parameter: 'loan_apr',
      unit: 'fraction/year',
      base: { rate: 0.06, npv_eur: baseNpv },
      minus_2_pct: { rate: 0.04, npv_eur: baseNpv + 420, delta_npv: 420 },
      plus_2_pct: { rate: 0.08, npv_eur: baseNpv - 420, delta_npv: -420 },
      delta_low: 420,
      delta_high: -420
    },
    pv_yield: {
      parameter: 'panel_watts',
      unit: 'W',
      base: { watts: 400.0, npv_eur: baseNpv },
      minus_10_pct: { watts: 360.0, npv_eur: baseNpv - 480, delta_npv: -480 },
      plus_10_pct: { watts: 440.0, npv_eur: baseNpv + 480, delta_npv: 480 },
      delta_low: -480,
      delta_high: 480
    },
    tax_deduction: {
      parameter: 'deduction_tax_capacity',
      unit: 'EUR/year',
      base: { capacity: 13500.0, npv_eur: baseNpv },
      low_capacity: { capacity: 0.0, npv_eur: baseNpv - 650, delta_npv: -650 },
      high_capacity: { capacity: 100000.0, npv_eur: baseNpv + 650, delta_npv: 650 },
      delta_low: -650,
      delta_high: 650
    },
    all_adverse_together: {
      group: 'combined',
      npv_eur: baseNpv - 2200,
      delta_npv: -2200,
      delta_low: -2200,
      delta_high: 0
    }
  };
}

function makeDayhourFixture(options = {}) {
  const settings = options.settings || {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    timezone_mode: 'civil',
    ac_heating_additional_demand_pct: 0,
    ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }]
  };
  const hours = [];
  for (let h = 0; h < 24; h++) {
    hours.push({
      hour: h,
      pv_kwh: options.pv_kwh != null ? options.pv_kwh : (h >= 8 && h <= 18 ? 2.5 : 0),
      load_kwh: options.load_kwh != null ? options.load_kwh : (h === 8 ? 4.0 : 1.2),
      direct_kwh: 0.8,
      charge_input_kwh: 1.0,
      discharge_delivered_kwh: 0.5,
      grid_import_kwh: 0.4,
      grid_export_kwh: 0.7,
      soc_start_kwh: 2.0,
      soc_end_kwh: 3.0,
      heat_pump: {
        space_compressor_electricity_kwh: 0.5,
        dhw_compressor_electricity_kwh: 0.1,
        delivered_space_heat_kwh: 1.8,
        delivered_draw_heat_kwh: 0.4,
        unmet_space_heat_kwh: 0,
        unmet_draw_heat_kwh: 0
      }
    });
  }
  return {
    settings: deepClone(settings),
    mode: options.mode || 'forecast_2027',
    period: options.period || 'day',
    scenario: options.scenario || settings.load_scenario || 'electrified',
    representative: options.representative || 'median',
    weather_year: options.weather_year || 2015,
    date: options.date || '2027-01-01',
    month: options.month || 1,
    timezone: 'Europe/Rome (DST)',
    available_hours: Array.from({ length: 24 }, (_, i) => i),
    battery_capacity_kwh: options.battery_capacity_kwh != null ? options.battery_capacity_kwh : 10,
    max_balance_error_kwh: 0,
    hours,
    totals: {
      pv_kwh: 25.0,
      load_kwh: 28.8,
      direct_kwh: 10.0,
      charge_input_kwh: 8.0,
      discharge_delivered_kwh: 6.0,
      grid_import_kwh: 5.0,
      grid_export_kwh: 7.0,
      soc_start_kwh: 2.0,
      soc_end_kwh: 3.0
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
        const list = listeners.get(event.type) || [];
        list.forEach(fn => fn(event));
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
        for (const child of children) {
          if (sel.startsWith('.') && child.classList?.contains?.(sel.slice(1))) out.push(child);
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

  // Pre-create elements
  const summarySensitivityContent = makeEl('div', 'summarySensitivityContent');
  const runSummarySensitivityBtn = makeEl('button', 'runSummarySensitivityBtn');
  runSummarySensitivityBtn.textContent = 'Update sensitivity';
  const summarySensitivityBadge = makeEl('span', 'summarySensitivityBadge');
  summarySensitivityBadge.style.display = 'none';

  const scopeSolarBtn = makeEl('button', 'scopeSolarBtn');
  const scopePackageBtn = makeEl('button', 'scopePackageBtn');
  const cashBeforeBtn = makeEl('button', 'cashBeforeBtn');
  const cashAfterBtn = makeEl('button', 'cashAfterBtn');

  const statusEl = makeEl('div', 'status');
  const errorEl = makeEl('div', 'error');
  errorEl.hidden = true;
  const latencyEl = makeEl('div', 'latency');

  const metricsEl = makeEl('div', 'metrics');
  const summaryScopeNote = makeEl('div', 'summaryScopeNote');
  const systemTitle = makeEl('div', 'systemTitle');
  const systemTags = makeEl('div', 'systemTags');
  const moneyBasisSelect = makeEl('select', 'moneyBasisSelect');
  const cashFlowBasis = makeEl('select', 'cashFlowBasis');
  const cashFlowHeading = makeEl('div', 'cashFlowHeading');
  const cashChart = makeEl('div', 'cashChart');

  const acScheduleUnionText = makeEl('span', 'acScheduleUnionText');
  const acScheduleTimeline = makeEl('div', 'acScheduleTimeline');
  const acScheduleRows = makeEl('div', 'acScheduleRows');

  const acHeatingDemandPctInput = makeEl('input', 'ac_heating_additional_demand_pct');
  acHeatingDemandPctInput.dataset.setting = 'ac_heating_additional_demand_pct';
  const acHeatingDemandPctError = makeEl('div', 'ac_heating_additional_demand_pct_error');
  acHeatingDemandPctError.hidden = true;

  const weatherYearEl = makeEl('select', 'weatherYear');
  weatherYearEl.options = [{ value: '2015' }];

  const hourSliderEl = makeEl('input', 'hourSlider');
  hourSliderEl.value = '12';
  const selectedHourLabelEl = makeEl('span', 'selectedHourLabel');
  selectedHourLabelEl.textContent = '12:00–13:00';
  const dayhourModeEl = makeEl('select', 'dayhourMode');
  dayhourModeEl.value = 'forecast_2027';
  const dayhourPeriodEl = makeEl('select', 'dayhourPeriod');
  dayhourPeriodEl.value = 'day';
  const dayhourMonthEl = makeEl('select', 'dayhourMonth');
  dayhourMonthEl.value = '1';
  const dayhourScenarioEl = makeEl('select', 'dayhourScenario');
  dayhourScenarioEl.value = 'electrified';
  const weatherRepresentativeEl = makeEl('select', 'weatherRepresentative');
  weatherRepresentativeEl.value = 'median';
  const weatherDateEl = makeEl('input', 'weatherDate');
  weatherDateEl.value = '2027-01-01';

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
      return [];
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
  const sensitivityCalls = [];
  const pendingSensitivities = [];
  const pendingEvaluations = [];
  let autoResolveEvaluate = true;
  let autoResolveSensitivity = true;

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
    ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }],
    battery_kwh: 10
  };
  const fallbackRecords = [
    { name: 'load_scenario', unit: 'scenario', group: 'loads' },
    { name: 'planning_heat_pump', unit: 'boolean', group: 'heat_pump' },
    { name: 'heating_schedule_mode', unit: 'mode', group: 'loads' },
    { name: 'timezone_mode', unit: 'mapping' },
    { name: 'ac_heating_windows', unit: 'windows', group: 'loads' },
    { name: 'ac_heating_additional_demand_pct', unit: '%', group: 'loads' },
    { name: 'battery_kwh', unit: 'kWh', group: 'battery' }
  ];

  const ctx = {
    console,
    SharedScenarioSync,
    window: win,
    document: doc,
    structuredClone,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    fetch: async (pathUrl, fetchOpts) => {
      if (pathUrl === '/api/schema') {
        const curSchema = (ctx.schema && ctx.schema.records && ctx.schema.records.length > 0)
          ? ctx.schema
          : { defaults: fallbackDefaults, records: fallbackRecords };
        return { ok: true, json: async () => curSchema };
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
        const fixture = makeEvaluateFixture(body.settings || {});
        if (autoResolveEvaluate) {
          return { ok: true, json: async () => fixture };
        }
        return new Promise((resolve, reject) => {
          pendingEvaluations.push({
            resolve: (val) => resolve({ ok: true, json: async () => val || fixture }),
            reject: (err) => reject(err)
          });
        });
      }
      if (pathUrl === '/api/summary-sensitivity') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        sensitivityCalls.push({ url: pathUrl, body });
        const fixture = makeSensitivityFixture({ scope: body.scope, cash_mode: body.cash_mode });
        if (autoResolveSensitivity) {
          return { ok: true, json: async () => fixture };
        }
        return new Promise((resolve, reject) => {
          pendingSensitivities.push({
            body,
            resolve: (val) => resolve({ ok: true, json: async () => val || fixture }),
            reject: (err) => reject(err)
          });
        });
      }
      if (pathUrl === '/api/dayhour') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        const scenario = body.scenario || body.settings?.load_scenario || 'electrified';
        const backendSettings = { ...(body.settings || {}), load_scenario: scenario };
        const fixture = makeDayhourFixture({
          settings: backendSettings,
          scenario,
          mode: body.mode || 'forecast_2027',
          period: body.period || 'day',
          month: body.month,
          date: body.date,
          weather_year: body.weather_year,
          representative: body.representative,
          battery_capacity_kwh: backendSettings.battery_kwh ?? 10
        });
        return { ok: true, json: async () => fixture };
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
    sensitivityCalls,
    pendingSensitivities,
    pendingEvaluations,
    get autoResolveSensitivity() { return autoResolveSensitivity; },
    set autoResolveSensitivity(val) { autoResolveSensitivity = val; },
    get autoResolveEvaluate() { return autoResolveEvaluate; },
    set autoResolveEvaluate(val) { autoResolveEvaluate = val; },
    flushTimers,
    hasPendingTimer: () => pendingTimers.length > 0,
    get pendingTimers() { return pendingTimers; },
    run: (code) => vm.runInContext(code, ctx)
  };
}

function initDefaultValidState(app) {
  const { run } = app;
  run(`
    const defs = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil',
      ac_heating_additional_demand_pct: 12.5,
      ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }],
      battery_kwh: 10
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
        { name: 'battery_kwh', unit: 'kWh', group: 'battery' }
      ]
    };
    settings = deepClone(schema.defaults);
    quote = { line_items: [] };
    summaryScope = 'solar';
    summaryCash = 'cash';
  `);
}

test('successful fresh cache state and payload source', async () => {
  const app = createAppContext();
  const { run, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  assert.equal(run("loanPending"), false);
  assert.ok(run("acceptedInputFingerprint"));

  // Initial pending message before sensitivity is fetched
  const contentEl = elements.get('summarySensitivityContent');
  assert.match(contentEl.innerHTML, /Sensitivity analysis pending/);

  // Trigger sensitivity calculation via production action
  await run("fetchSummarySensitivity()");

  assert.equal(sensitivityCalls.length, 1);
  const call = sensitivityCalls[0];
  assert.equal(call.url, '/api/summary-sensitivity');

  // Verify payload semantics and source: canonical settings, quote, scope, and cash mode
  assert.equal(call.body.scope, 'solar');
  assert.equal(call.body.cash_mode, 'cash');
  assert.equal(call.body.settings.ac_heating_additional_demand_pct, 12.5);
  assert.equal(call.body.settings.ac_heating_windows[0].start_minute, 360);
  assert.equal(call.body.settings.ac_heating_windows[0].end_minute, 480);
  assert.equal(call.body.settings.battery_kwh, 10);
  assert.deepEqual(call.body.quote.line_items, []);

  // Fresh cache state
  assert.ok(run("summarySensitivityCache"));
  assert.ok(run("isSummarySensitivityMatching()"));
  assert.equal(run("isSummarySensitivityOutdated()"), false);

  // Fresh rendering: cards present, no stale overlay, no stale badge strip
  assert.match(contentEl.innerHTML, /RETAIL ELECTRICITY TARIFF/);
  assert.match(contentEl.innerHTML, /REAL DISCOUNT RATE/);
  assert.match(contentEl.innerHTML, /WEATHER ANALOGUE RISK/);
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false, 'no stale overlay on fresh cache');
  assert.equal(contentEl.innerHTML.includes('stale-badge-strip'), false, 'no stale badge on fresh cache');

  const btn = elements.get('runSummarySensitivityBtn');
  assert.equal(btn.disabled, true);
  assert.equal(btn.textContent, 'Update sensitivity');
  const badge = elements.get('summarySensitivityBadge');
  assert.equal(badge.textContent, '✓ Up to date');
  assert.equal(badge.style.display, '');
});

test('valid changed inputs mark retained results visibly outdated, and refresh produces fresh cache', async () => {
  const app = createAppContext();
  const { run, flushTimers, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  await run("fetchSummarySensitivity()");
  assert.equal(run("isSummarySensitivityMatching()"), true);
  const contentEl = elements.get('summarySensitivityContent');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false);

  // Edit valid setting: battery_kwh changed from 10 to 15 via production editSetting
  run("editSetting('battery_kwh', 15)");

  // Schedule was triggered immediately: results are marked visibly outdated
  assert.equal(run("isSummarySensitivityMatching()"), false);
  assert.equal(run("isSummarySensitivityOutdated()"), true);
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'retained results have stale-overlay immediately');
  assert.ok(contentEl.innerHTML.includes('stale-badge-strip'), 'retained results have stale-badge-strip');
  assert.match(contentEl.innerHTML, /Inputs changed since last evaluation · click Update sensitivity to refresh/);
  assert.match(contentEl.innerHTML, /RETAIL ELECTRICITY TARIFF/, 'retained cards still visibly rendered');

  // Flush timers so recompute finishes evaluate
  await flushTimers();
  assert.equal(run("settings.battery_kwh"), 15);
  // Recompute finished for new battery, but sensitivity cache was computed for battery 10
  assert.equal(run("isSummarySensitivityMatching()"), false, 'cached sensitivity still outdated against new battery');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'retained cards remain visibly outdated after evaluate');

  // Now refresh sensitivity via production fetchSummarySensitivity
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 2);
  assert.equal(sensitivityCalls[1].body.settings.battery_kwh, 15);

  // Fresh again
  assert.equal(run("isSummarySensitivityMatching()"), true);
  assert.equal(run("isSummarySensitivityOutdated()"), false);
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false, 'stale overlay removed on fresh update');
  assert.equal(contentEl.innerHTML.includes('stale-badge-strip'), false, 'stale badge removed on fresh update');
});

test('invalid same-canonical draft (schedule and demand pct) marks retained results visibly outdated and blocks silent fetch', async () => {
  const app = createAppContext();
  const { run, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  await run("fetchSummarySensitivity()");
  assert.equal(run("isSummarySensitivityMatching()"), true);
  assert.equal(sensitivityCalls.length, 1);

  const contentEl = elements.get('summarySensitivityContent');
  const btn = elements.get('runSummarySensitivityBtn');

  // --- Part A: Invalid schedule draft ---
  // User enters invalid time '99:00' via production handler
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");

  // Canonical settings is NOT updated!
  assert.equal(run("settings.ac_heating_windows[0].start_minute"), 360, 'canonical inputs retained unchanged');
  assert.equal(run("hasInvalidAcScheduleDraft()"), true, 'draft invalid');
  assert.equal(run("shouldBlockAcRecompute(settings)"), true, 'governing schedule draft blocks recompute');

  // Freshness helper and DOM must visibly reflect outdated state
  assert.equal(run("isSummarySensitivityMatching()"), false, 'not matching during invalid draft');
  assert.equal(run("isSummarySensitivityOutdated()"), true, 'outdated during invalid draft');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'visibly outdated overlay applied');
  assert.ok(contentEl.innerHTML.includes('stale-badge-strip'), 'visibly outdated badge applied');
  assert.match(contentEl.innerHTML, /RETAIL ELECTRICITY TARIFF/, 'retained results remain visible');

  // User attempts to click "Update sensitivity" while draft is invalid
  await run("fetchSummarySensitivity()");

  // MUST NOT silently run sensitivity using hidden last-valid values!
  assert.equal(sensitivityCalls.length, 1, 'no sensitivity API call dispatched during invalid draft');
  assert.equal(btn.disabled, false, 'button is not disabled');
  assert.equal(btn.textContent, 'Update sensitivity', 'button text preserved');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'retained results stay visibly outdated');

  // Correct schedule draft back to valid
  run("handleAcScheduleTimeInput('w1', 'start', '06:00')");
  await app.flushTimers();
  assert.equal(run("isSummarySensitivityMatching()"), true, 'matching restored after correction');

  // --- Part B: Invalid demand percentage draft ---
  // User enters negative percentage '-15' via production handler
  run("handleAcHeatingDemandPctInput('-15')");

  // Canonical settings is NOT updated!
  assert.equal(run("settings.ac_heating_additional_demand_pct"), 12.5, 'canonical percentage retained unchanged');
  assert.equal(run("hasInvalidAcDemandPctDraft()"), true, 'percentage draft invalid');
  assert.equal(run("shouldBlockAcRecompute(settings)"), true, 'governing percentage draft blocks recompute');

  // Freshness helper and DOM visibly reflect outdated state
  assert.equal(run("isSummarySensitivityMatching()"), false);
  assert.equal(run("isSummarySensitivityOutdated()"), true);
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));
  assert.ok(contentEl.innerHTML.includes('stale-badge-strip'));

  // User attempts to click "Update sensitivity"
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 1, 'percentage invalid draft blocks sensitivity dispatch');
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Update sensitivity');
});

test('same-value correction restores fresh state without extra sensitivity fetch', async () => {
  const app = createAppContext();
  const { run, flushTimers, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 1);
  assert.equal(run("isSummarySensitivityMatching()"), true);
  const contentEl = elements.get('summarySensitivityContent');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false);

  // 1. Enter invalid schedule time
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), true);
  assert.equal(run("isSummarySensitivityMatching()"), false);
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));

  // 2. Correct draft back to the SAME canonical value (06:00 -> 360m)
  run("handleAcScheduleTimeInput('w1', 'start', '06:00')");
  assert.equal(run("hasInvalidAcScheduleDraft()"), false);
  assert.equal(run("shouldBlockAcRecompute(settings)"), false);

  // Automatic recovery scheduled evaluate
  assert.equal(app.hasPendingTimer(), true, 'automatic recovery timer scheduled');
  await flushTimers();

  // Evaluated result matches settings and sensitivity cache matches canonical inputs
  assert.equal(run("isSummarySensitivityMatching()"), true, 'matching result is fresh again without sticky stale flag');
  assert.equal(run("isSummarySensitivityOutdated()"), false);
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false, 'stale overlay removed');
  assert.equal(contentEl.innerHTML.includes('stale-badge-strip'), false, 'stale badge removed');
  assert.equal(sensitivityCalls.length, 1, 'sensitivity API was not called again');

  // 3. Repeat for same-value percentage correction
  run("handleAcHeatingDemandPctInput('-5')");
  assert.equal(run("isSummarySensitivityMatching()"), false);
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));

  // Correct back to same percentage (12.5)
  run("handleAcHeatingDemandPctInput('12.5')");
  assert.equal(run("hasInvalidAcDemandPctDraft()"), false);
  await flushTimers();

  assert.equal(run("isSummarySensitivityMatching()"), true, 'fresh again after same percentage correction');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false);
  assert.equal(sensitivityCalls.length, 1, 'sensitivity API still not called again');
});

test('stale success races are rejected after schedule edits, percentage edits, invalid drafts, and newer requests', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  app.autoResolveSensitivity = false;

  // Case A: schedule edit while in-flight
  const promiseA = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 1);
  // Schedule edit while request A is in-flight
  run("handleAcScheduleTimeInput('w1', 'start', '07:00')");
  // Resolve delayed response for request A
  app.pendingSensitivities[0].resolve(makeSensitivityFixture({ base_npv_eur: 9999 }));
  await promiseA;

  assert.equal(run("summarySensitivityCache"), null, 'stale response A rejected on schedule edit');

  // Reset schedule back to 06:00 and recompute
  run("handleAcScheduleTimeInput('w1', 'start', '06:00')");
  await app.flushTimers();

  // Case B: percentage edit while in-flight
  const promiseB = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 2);
  run("handleAcHeatingDemandPctInput('25')");
  app.pendingSensitivities[1].resolve(makeSensitivityFixture({ base_npv_eur: 8888 }));
  await promiseB;

  assert.equal(run("summarySensitivityCache"), null, 'stale response B rejected on percentage edit');

  // Reset percentage and recompute
  run("handleAcHeatingDemandPctInput('12.5')");
  await app.flushTimers();

  // Case C: invalid draft entered while in-flight
  const promiseC = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 3);
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  app.pendingSensitivities[2].resolve(makeSensitivityFixture({ base_npv_eur: 7777 }));
  await promiseC;

  assert.equal(run("summarySensitivityCache"), null, 'stale response C rejected on invalid draft');

  // Correct schedule and recompute
  run("handleAcScheduleTimeInput('w1', 'start', '06:00')");
  await app.flushTimers();

  // Case D: newer request race
  const promiseD1 = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 4);
  const promiseD2 = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 5);

  // Response D1 arrives
  app.pendingSensitivities[3].resolve(makeSensitivityFixture({ base_npv_eur: 1111 }));
  await promiseD1;
  assert.equal(run("summarySensitivityCache"), null, 'older response D1 superseded by newer request D2');

  // Response D2 arrives
  app.pendingSensitivities[4].resolve(makeSensitivityFixture({ base_npv_eur: 5555 }));
  await promiseD2;
  assert.ok(run("summarySensitivityCache"), 'newer response D2 accepted');
  assert.equal(run("summarySensitivityCache.base_npv_eur"), 5555);
  assert.equal(run("isSummarySensitivityMatching()"), true);
});

test('stale error races are rejected and do not overwrite retained results or UI', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  // First, obtain a valid cached sensitivity result
  await run("fetchSummarySensitivity()");
  assert.equal(run("isSummarySensitivityMatching()"), true);
  const contentEl = elements.get('summarySensitivityContent');
  assert.match(contentEl.innerHTML, /RETAIL ELECTRICITY TARIFF/);

  // Now initiate a deferred fetch
  app.autoResolveSensitivity = false;
  const fetchPromise = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 1);

  // User enters invalid schedule draft while request is in flight
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'visibly outdated overlay active');

  // The in-flight request fails with an error
  app.pendingSensitivities[0].reject(new Error('Simulated backend error 500'));
  await fetchPromise;

  // Stale error must be REJECTED: no error text in contentEl, retained results preserved!
  assert.equal(contentEl.innerHTML.includes('Could not evaluate sensitivity'), false, 'stale error rejected');
  assert.match(contentEl.innerHTML, /RETAIL ELECTRICITY TARIFF/, 'retained results still present');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'still visibly outdated');

  const btn = elements.get('runSummarySensitivityBtn');
  assert.equal(btn.disabled, false);
  assert.equal(btn.textContent, 'Update sensitivity');
});

test('scope and cash changes invalidate cache and reject stale in-flight responses', async () => {
  const app = createAppContext();
  const { run, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  await run("fetchSummarySensitivity()");
  assert.equal(run("summaryScope"), 'solar');
  assert.equal(run("summaryCash"), 'cash');
  assert.equal(run("isSummarySensitivityMatching()"), true);

  const contentEl = elements.get('summarySensitivityContent');

  // 1. Change scope to 'package' via production setSummaryScope
  run("setSummaryScope('package')");
  assert.equal(run("summaryScope"), 'package');
  assert.equal(run("isSummarySensitivityMatching()"), false, 'outdated because scope changed');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));

  // 2. Delayed sensitivity fetch for 'package' + 'cash'
  app.autoResolveSensitivity = false;
  const promiseScope = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 1);
  assert.equal(app.pendingSensitivities[0].body.scope, 'package');
  assert.equal(app.pendingSensitivities[0].body.cash_mode, 'cash');

  // While in flight, user toggles cash mode to 'loan' via production setSummaryCash
  run("setSummaryCash('loan')");
  assert.equal(run("summaryCash"), 'loan');

  // Delayed response for 'package' + 'cash' arrives
  app.pendingSensitivities[0].resolve(makeSensitivityFixture({ scope: 'package', cash_mode: 'cash' }));
  await promiseScope;

  assert.equal(run("isSummarySensitivityMatching()"), false, 'stale cash_mode response rejected');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));

  // 3. Fresh fetch for 'package' + 'loan'
  app.autoResolveSensitivity = true;
  await run("fetchSummarySensitivity()");
  assert.equal(run("isSummarySensitivityMatching()"), true, 'fresh for package + loan');
  assert.equal(run("summarySensitivityCache.scope"), 'package');
  assert.equal(run("summarySensitivityCache.cash_mode"), 'loan');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false);
});

test('remote inputs via applySharedState mark retained results outdated and reject in-flight requests', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  await run("fetchSummarySensitivity()");
  assert.equal(run("isSummarySensitivityMatching()"), true);

  app.autoResolveSensitivity = false;
  const fetchPromise = run("fetchSummarySensitivity()");
  assert.equal(app.pendingSensitivities.length, 1);

  // Incoming remote state via production applySharedState with changed battery
  run(`
    const newState = {
      settings: { ...settings, battery_kwh: 30 },
      quote: { line_items: [] },
      defaults: schema.defaults,
      records: schema.records
    };
    applySharedState(newState);
  `);

  const contentEl = elements.get('summarySensitivityContent');
  assert.equal(run("isSummarySensitivityMatching()"), false);
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));

  // In-flight sensitivity resolves
  app.pendingSensitivities[0].resolve(makeSensitivityFixture());
  await fetchPromise;

  assert.equal(run("isSummarySensitivityMatching()"), false, 'in-flight request rejected after remote state change');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));
});

test('inactive AC and continuous mode scoping', async () => {
  const app = createAppContext();
  const { run, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  // --- Scenario 1: Baseline load_scenario ---
  run("settings.load_scenario = 'baseline';");
  await run("recompute()");

  // Enter invalid AC schedule draft
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  // In baseline, AC schedule does NOT govern
  assert.equal(run("isAcScheduleGoverningActiveHeating(settings)"), false);
  assert.equal(run("shouldBlockAcRecompute(settings)"), false);

  // Sensitivity CAN run for baseline
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 1);
  assert.equal(run("isSummarySensitivityMatching()"), true, 'baseline sensitivity matching despite inactive AC draft');

  // --- Scenario 2: Air-to-water heat pump (planning_heat_pump: true) ---
  run("settings.load_scenario = 'electrified'; settings.planning_heat_pump = true;");
  await run("recompute()");

  // Enter invalid AC percentage
  run("handleAcHeatingDemandPctInput('-50')");
  // For air-to-water heat pump, AC percentage does NOT govern
  assert.equal(run("isAcDemandPctGoverningActiveHeating(settings)"), false);
  assert.equal(run("shouldBlockAcRecompute(settings)"), false);

  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 2);
  assert.equal(run("isSummarySensitivityMatching()"), true, 'HP sensitivity matching despite inactive AC draft');

  // --- Scenario 3: Continuous heating mode ---
  run("settings.planning_heat_pump = false; settings.heating_schedule_mode = 'continuous';");
  // Clear percentage error
  run("handleAcHeatingDemandPctInput('15')");
  await run("recompute()");

  // Enter invalid schedule window text: '99:00'
  run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  // In continuous mode, schedule windows do NOT govern
  assert.equal(run("isAcScheduleGoverningActiveHeating(settings)"), false);
  assert.equal(run("shouldBlockAcScheduleRecompute(settings)"), false);
  assert.equal(run("shouldBlockAcRecompute(settings)"), false);

  // Continuous mode sensitivity CAN run
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 3);
  assert.equal(run("isSummarySensitivityMatching()"), true, 'continuous sensitivity matching despite window text');

  // BUT invalid percentage in continuous mode DOES govern!
  run("handleAcHeatingDemandPctInput('-99')");
  assert.equal(run("isAcDemandPctGoverningActiveHeating(settings)"), true);
  assert.equal(run("shouldBlockAcDemandPctRecompute(settings)"), true);
  assert.equal(run("shouldBlockAcRecompute(settings)"), true);

  // Retained sensitivity is now outdated and blocked
  assert.equal(run("isSummarySensitivityMatching()"), false);
  const contentEl = elements.get('summarySensitivityContent');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'));

  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 3, 'percentage invalid in continuous mode blocked sensitivity API');
});

test('tornado chart renders all 14 sensitivity drivers grouped with evidence badges and sorted swings', async () => {
  const app = createAppContext();
  const { run, sensitivityCalls, elements } = app;
  initDefaultValidState(app);
  await run("recompute()");

  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 1);
  const contentEl = elements.get('summarySensitivityContent');

  const driverLabels = [
    'RETAIL ELECTRICITY TARIFF',
    'WEATHER ANALOGUE RISK',
    'EXPORT PRICE RAMP-DOWN',
    'INITIAL CAPEX',
    'EQUIPMENT REPLACEMENT COST',
    'HEAT PUMP SEASONAL COP',
    'REAL ELECTRICITY ESCALATION',
    'EXPORT TARIFF BENCHMARK',
    'LOAN FINANCING TAN',
    'SOLAR PV SPECIFIC YIELD',
    'TAX DEDUCTION ELIGIBILITY',
    'NATURAL GAS TARIFF',
    'BATTERY DEGRADATION',
    'REAL DISCOUNT RATE'
  ];
  for (const label of driverLabels) {
    assert.match(contentEl.innerHTML, new RegExp(label), `driver ${label} rendered`);
  }

  assert.ok(contentEl.innerHTML.includes('UNCERTAIN DRIVERS'), 'uncertain drivers group header rendered');
  assert.ok(contentEl.innerHTML.includes('VALUATION SETTINGS'), 'valuation settings group header rendered');
  assert.ok(contentEl.innerHTML.includes('ALL ADVERSE SCENARIO') || contentEl.innerHTML.includes('ALL ADVERSE TOGETHER'), 'all adverse group header rendered');
  assert.ok(contentEl.innerHTML.includes('evidence-badge'), 'evidence badges rendered');
  assert.ok(contentEl.innerHTML.includes('tornado-row'), 'tornado rows rendered');
});

test('auto-run initial skeleton and debounced auto scheduling prevent loops', async () => {
  const app = createAppContext();
  const { run, flushTimers, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  const contentEl = elements.get('summarySensitivityContent');
  assert.ok(contentEl.innerHTML.includes('tornado-row') || contentEl.innerHTML.includes('skeleton'), 'initial skeleton rendered');
  assert.equal(sensitivityCalls.length, 0, 'no immediate sync fetch');

  await flushTimers();
  assert.equal(sensitivityCalls.length, 1, 'auto initial run executed once');

  await run("renderSummarySensitivity()");
  await flushTimers();
  assert.equal(sensitivityCalls.length, 1, 'subsequent render does not loop');
});

test('missing driver data is not fabricated and displays Not evaluated with no bar', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);
  await run("recompute()");

  // Feed partial data that omits capex, gas, and degradation
  const partialData = {
    scope: 'solar',
    cash_mode: 'cash',
    base_npv_eur: 5000,
    electricity_price: {
      minus_20_pct: { delta_npv: -800 },
      plus_20_pct: { delta_npv: 800 }
    }
  };

  run(`displaySummarySensitivity(${JSON.stringify(partialData)}, false)`);
  const contentEl = elements.get('summarySensitivityContent');

  // Verify that Not evaluated appears for un-evaluated drivers
  assert.ok(contentEl.innerHTML.includes('Not evaluated'), 'displays Not evaluated for missing drivers');
  // Verify that invented numbers like -1875 or 1200 do NOT appear in the DOM
  assert.equal(contentEl.innerHTML.includes('-1875'), false, 'no invented -1875 in DOM');
  assert.equal(contentEl.innerHTML.includes('1200'), false, 'no invented 1200 in DOM');
});

test('evidence statuses are conservatively aggregated and weather is never measured', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);
  await run("recompute()");

  await run("fetchSummarySensitivity()");
  const contentEl = elements.get('summarySensitivityContent');

  // Weather row must have unconfirmed badge, never measured
  const weatherRowMatch = contentEl.innerHTML.match(/data-driver="weather"[\s\S]*?evidence-badge\s+([^"]+)/);
  assert.ok(weatherRowMatch, 'weather row found');
  assert.ok(weatherRowMatch[1].includes('unconfirmed'), 'weather evidence is unconfirmed');
});


test('regression: accepted scope and input change while sensitivity in-flight auto-reschedules latest once with stale badge and no loops', async () => {
  const app = createAppContext();
  const { run, flushTimers, sensitivityCalls, elements } = app;
  initDefaultValidState(app);

  await run("recompute()");
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 1);
  assert.equal(run("isSummarySensitivityMatching()"), true);
  const contentEl = elements.get('summarySensitivityContent');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false);

  // --- Part 1: Scope change while in-flight, followed by stale success ---
  app.autoResolveSensitivity = false;
  const promiseA = run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 2);
  assert.equal(app.pendingSensitivities.length, 1);
  assert.equal(run("summarySensitivityInFlight"), true);

  // Accepted scope change while request A is in-flight
  run("setSummaryScope('package')");
  assert.equal(run("summaryScope"), 'package');
  assert.equal(run("isSummarySensitivityMatching()"), false);

  // Stale badge and overlay visible while pending
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'stale overlay visible while pending');
  assert.ok(contentEl.innerHTML.includes('stale-badge'), 'stale badge visible while pending');

  // No concurrent auto-run while old request is running
  await flushTimers();
  assert.equal(sensitivityCalls.length, 2, 'no concurrent auto while old request is running');

  // Request A completes with stale solar data
  app.pendingSensitivities[0].resolve(makeSensitivityFixture({ scope: 'solar', cash_mode: 'cash' }));
  await promiseA;

  // Stale response A rejected, stale overlay preserved, no manual refresh clicked
  assert.equal(run("summarySensitivityKey").startsWith('package_'), false, 'stale response A not accepted for package');
  assert.equal(run("isSummarySensitivityMatching()"), false, 'outdated because latest accepted is package');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'stale overlay retained after old response discarded');

  // After old settles, flushTimers runs auto-run for latest package scope exactly once
  app.autoResolveSensitivity = true;
  await flushTimers();
  await new Promise(r => setImmediate(r));
  assert.equal(sensitivityCalls.length, 3, 'auto-run executed automatically for latest package scope');
  assert.equal(sensitivityCalls[2].body.scope, 'package');
  assert.equal(run("isSummarySensitivityMatching()"), true, 'fresh result accepted for package');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false, 'stale overlay removed');

  // No loops on subsequent render
  await run("renderSummarySensitivity()");
  await flushTimers();
  await new Promise(r => setImmediate(r));
  assert.equal(sensitivityCalls.length, 3, 'no loops after fresh subsequent render');

  // --- Part 2: Input change while in-flight, followed by stale error ---
  app.autoResolveSensitivity = false;
  const promiseB = run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 4);
  assert.equal(app.pendingSensitivities.length, 2);
  assert.equal(run("summarySensitivityInFlight"), true);

  // Accepted battery input change while request B is in-flight
  run("editSetting('battery_kwh', 25)");
  await flushTimers(); // evaluate finishes and updates acceptedInputFingerprint
  await new Promise(r => setImmediate(r));
  assert.equal(run("settings.battery_kwh"), 25);
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'stale while pending after input change');
  assert.equal(sensitivityCalls.length, 4, 'no concurrent auto while request B running');

  // Request B fails with network error
  app.pendingSensitivities[1].reject(new Error('Superseded in-flight network timeout'));
  await promiseB;

  // Stale error rejected (no error banner), and auto-rescheduled for battery 25
  assert.equal(contentEl.innerHTML.includes('Could not evaluate sensitivity'), false, 'superseded error rejected');
  assert.ok(contentEl.innerHTML.includes('stale-overlay'), 'stale overlay preserved');

  // After old settles, flushTimers runs auto-run for battery 25 exactly once
  app.autoResolveSensitivity = true;
  await flushTimers();
  await new Promise(r => setImmediate(r));
  assert.equal(sensitivityCalls.length, 5, 'auto-run executed automatically for updated battery');
  assert.equal(sensitivityCalls[4].body.settings.battery_kwh, 25);
  assert.equal(run("isSummarySensitivityMatching()"), true, 'fresh result accepted for new battery');
  assert.equal(contentEl.innerHTML.includes('stale-overlay'), false, 'stale overlay removed on fresh result');

  // No loops after fresh subsequent render
  await run("renderSummarySensitivity()");
  await flushTimers();
  await new Promise(r => setImmediate(r));
  assert.equal(sensitivityCalls.length, 5, 'no loops after subsequent render');
});

test('regression: ALL ADVERSE TOGETHER row renders em dash upside, visible Net NPV label, no green positive upside, and net NPV value', async () => {
  const app = createAppContext();
  const { run, elements } = app;
  initDefaultValidState(app);
  await run("recompute()");
  await run("fetchSummarySensitivity()");

  const contentEl = elements.get('summarySensitivityContent');
  // Robust split by data-driver boundaries to isolate complete row without nested div truncation
  const rows = contentEl.innerHTML.split(/data-driver="/).slice(1);
  const advRow = rows.find(r => r.startsWith('all_adverse'));
  assert.ok(advRow, 'all adverse row found in DOM');

  // Downside delta present
  assert.ok(advRow.includes('−€2,200'), 'downside delta displayed');

  // Upside must be em dash, NOT green positive upside
  assert.ok(advRow.includes('>—<'), 'upside cell is em dash');
  assert.equal(advRow.includes('tornado-val pos'), false, 'no tornado-val pos class in all-adverse row');
  assert.equal(advRow.includes('+€0'), false, 'no erroneous +€0 upside');

  // Rightmost column must show visible Net NPV: label and net NPV value
  assert.ok(advRow.includes('all-adverse-impact'), 'all-adverse-impact class present');
  assert.ok(advRow.includes('Net NPV:'), 'explicit Net NPV: label present');
  assert.ok(advRow.includes('<strong>€2,800</strong>'), 'rightmost Net NPV equals base NPV plus combined adverse delta');
  assert.equal(advRow.includes('<strong>€0</strong>'), false, 'no zero swing');

  // Verify individual driver rows are unchanged
  const retailRow = rows.find(r => r.startsWith('retail'));
  assert.ok(retailRow, 'retail row found');
  assert.ok(retailRow.includes('tornado-val pos'), 'retail row has tornado-val pos');
  assert.ok(retailRow.includes('+€800'), 'retail row has positive upside');
  assert.ok(retailRow.includes('<strong>€1,600</strong>'), 'retail row has swing');
  assert.equal(retailRow.includes('Net NPV:'), false, 'retail row does not have Net NPV label');
});

test('regression: sensitivity update button state: cached up-to-date mutes button with badge, highlights on input change, restores actionable on error', async () => {
  const app = createAppContext();
  const { run, flushTimers, elements, sensitivityCalls } = app;
  initDefaultValidState(app);
  await run("recompute()");

  const btn = elements.get('runSummarySensitivityBtn');
  const badge = elements.get('summarySensitivityBadge');

  // 1. Fresh sensitivity run completes -> button disabled/muted, badge visible
  await run("fetchSummarySensitivity()");
  assert.equal(sensitivityCalls.length, 1);
  assert.equal(btn.disabled, true, 'button disabled on fresh cache');
  assert.ok(btn.classList.contains('muted-btn'), 'button has muted-btn class');
  assert.equal(btn.classList.contains('highlight'), false, 'button is not highlighted');
  assert.equal(badge.textContent, '✓ Up to date');
  assert.equal(badge.style.display, '', 'badge is visible');

  // 2. Evaluation input change -> button becomes enabled and highlighted, badge hidden
  run("editSetting('battery_kwh', 20)");
  await flushTimers(); // evaluation recomputes
  assert.equal(btn.disabled, false, 'button actionable when inputs changed');
  assert.ok(btn.classList.contains('highlight'), 'button highlights when evaluation inputs changed');
  assert.equal(badge.style.display, 'none', 'badge hidden when inputs changed');

  // 3. While sensitivity in-flight -> button disabled, 'Calculating…', highlight removed
  const originalFetch = app.ctx.fetch;
  let resolvePending;
  const pendingPromise = new Promise(r => { resolvePending = r; });
  app.ctx.fetch = async (url, opts) => {
    if (url === '/api/summary-sensitivity') {
      await pendingPromise;
      return { ok: true, json: async () => makeSensitivityFixture() };
    }
    return originalFetch(url, opts);
  };

  const inFlightPromise = run("fetchSummarySensitivity()");
  assert.equal(btn.disabled, true, 'button disabled while running');
  assert.equal(btn.textContent, 'Calculating…');
  assert.equal(btn.classList.contains('highlight'), false, 'highlight removed while running');
  assert.equal(badge.style.display, 'none');

  resolvePending();
  await inFlightPromise;
  assert.equal(btn.disabled, true, 'button disabled again when completed');
  assert.equal(badge.textContent, '✓ Up to date');
  assert.equal(badge.style.display, '');

  // 4. Realistic normal flow: failure after input change
  run("editSetting('battery_kwh', 22)");
  await flushTimers();
  assert.equal(btn.disabled, false);
  assert.ok(btn.classList.contains('highlight'));

  app.ctx.fetch = async (url, opts) => {
    if (url === '/api/summary-sensitivity') {
      throw new Error('Network timeout');
    }
    return originalFetch(url, opts);
  };

  await run("fetchSummarySensitivity()");
  assert.equal(btn.disabled, false, 'button actionable after failure on changed input');
  assert.equal(btn.textContent, 'Update sensitivity');
  assert.ok(btn.classList.contains('highlight'), 'highlight retained on changed input failure');
  assert.equal(badge.style.display, 'none', 'badge hidden after failure');

  // Successful retry on changed input restores up-to-date state
  app.ctx.fetch = originalFetch;
  await run("fetchSummarySensitivity()");
  assert.equal(btn.disabled, true, 'button disabled after successful retry');
  assert.equal(btn.classList.contains('highlight'), false);
  assert.equal(badge.textContent, '✓ Up to date');
  assert.equal(badge.style.display, '');

  // 5. Explicit rerun on already fresh cache fails: keeps cache but enables actionable retry and hides badge
  app.ctx.fetch = async (url, opts) => {
    if (url === '/api/summary-sensitivity') {
      throw new Error('Simulated transient 500 error on explicit rerun');
    }
    return originalFetch(url, opts);
  };

  await run("fetchSummarySensitivity()");
  assert.ok(elements.get('summarySensitivityContent').innerHTML.includes('Could not evaluate sensitivity'));
  assert.equal(btn.disabled, false, 'button actionable for retry even on explicit rerun failure');
  assert.equal(btn.textContent, 'Update sensitivity');
  assert.equal(btn.classList.contains('highlight'), false, 'no highlight since inputs are identical to cache');
  assert.equal(badge.style.display, 'none', 'badge hidden while failure is displayed');

  // Rerun recovery restores up-to-date state
  app.ctx.fetch = originalFetch;
  await run("fetchSummarySensitivity()");
  assert.equal(btn.disabled, true, 'button disabled again after recovery');
  assert.equal(badge.textContent, '✓ Up to date');
  assert.equal(badge.style.display, '');

  // 6. Scope change without input change: button enabled, NOT highlighted
  run("setSummaryScope('package')");
  assert.equal(btn.disabled, false, 'button actionable on scope change');
  assert.equal(btn.classList.contains('highlight'), false, 'button not highlighted since evaluation inputs unchanged');
  assert.equal(badge.style.display, 'none');
});
