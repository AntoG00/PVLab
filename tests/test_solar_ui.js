/* Run with Node: exercise the solar sizing UI renderer, controls, stale invalidation, charts, and apply action. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const cp = require('node:child_process');

const pythonBin = process.env.PV_PYTHON || path.join(__dirname, '..', '.venv', 'bin', 'python');

function getWorkerLimit() {
  const raw = process.env.PV_TEST_WORKERS;
  if (typeof raw === 'string' && /^[1-9]\d*$/.test(raw.trim())) {
    const parsed = Number(raw.trim());
    if (Number.isSafeInteger(parsed) && parsed > 0) return parsed;
  }
  return 1;
}

const maxWorkers = getWorkerLimit();
let activeWorkers = 0;
const workerQueue = [];

function runWorkerTask(taskFn) {
  return new Promise((resolve, reject) => {
    const run = () => {
      activeWorkers++;
      let promise;
      try {
        promise = taskFn();
      } catch (err) {
        promise = Promise.reject(err);
      }
      promise
        .then(resolve, reject)
        .finally(() => {
          activeWorkers--;
          if (workerQueue.length > 0) {
            const next = workerQueue.shift();
            next();
          }
        });
    };
    if (activeWorkers < maxWorkers) {
      run();
    } else {
      workerQueue.push(run);
    }
  });
}

function prefetch(promise) {
  promise.catch(() => {});
  return promise;
}

function execPythonAsync(script, payloadJson) {
  const promise = runWorkerTask(() => new Promise((resolve, reject) => {
    cp.execFile(pythonBin, ['-c', script, payloadJson], {
      encoding: 'utf8',
      cwd: path.join(__dirname, '..'),
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 16 * 1024 * 1024,
      timeout: 90000
    }, (err, stdout) => {
      if (err) return reject(err);
      try {
        resolve(JSON.parse(stdout));
      } catch (parseErr) {
        reject(parseErr);
      }
    });
  }));
  promise.catch(() => {});
  return promise;
}

function pythonEvaluate(payload) {
  const script = `
import sys, json, tempfile, pathlib
sys.path.insert(0, 'feasibility/vendor')
sys.path.insert(0, '.')
import feasibility.assumptions as fa
with tempfile.TemporaryDirectory() as tmpdir:
    fa.USER_DEFAULTS_PATH = pathlib.Path(tmpdir) / 'defaults.json'
    from feasibility.legacy_profile import bootstrap_legacy
    bootstrap_legacy(root=pathlib.Path(tmpdir) / 'profiles')
    import feasibility.engine as e
    data = json.loads(sys.argv[1])
    q_res = e.quote_evaluate(data['settings'], data.get('quote'))
    res = q_res['result']
    print(json.dumps({
        'capex_eur': res['finance']['capex_eur'],
        'npv_median': res['metrics']['npv_eur']['median'],
        'settings': res['settings']
    }))
`;
  return execPythonAsync(script, JSON.stringify(payload));
}

function pythonSolarSizing(settings, options, quote) {
  const script = `
import sys, json, tempfile, pathlib
sys.path.insert(0, 'feasibility/vendor')
sys.path.insert(0, '.')
import feasibility.assumptions as fa
with tempfile.TemporaryDirectory() as tmpdir:
    fa.USER_DEFAULTS_PATH = pathlib.Path(tmpdir) / 'defaults.json'
    from feasibility.legacy_profile import bootstrap_legacy
    bootstrap_legacy(root=pathlib.Path(tmpdir) / 'profiles')
    import feasibility.engine as e
    data = json.loads(sys.argv[1])
    res = e.solar_sizing(data['settings'], data.get('options'), data.get('quote'))
    print(json.dumps(res))
`;
  return execPythonAsync(script, JSON.stringify({ settings, options, quote }));
}

const source = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const elements = new Map();
function makeMockElement(id) {
  const classSet = new Set();
  const listeners = {};
  const attrs = {};
  const el = {
    id,
    _innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    hidden: false,
    style: {},
    _buttons: [],
    _listeners: listeners,
    getAttribute: name => attrs[name] !== undefined ? attrs[name] : null,
    setAttribute: (name, val) => { attrs[name] = String(val); },
    removeAttribute: name => { delete attrs[name]; },
    get innerHTML() { return this._innerHTML; },
    set innerHTML(val) {
      this._innerHTML = String(val);
      this._buttons = [];
      const btnRegex = /<button\b([^>]*)>(.*?)<\/button>/gs;
      let match;
      while ((match = btnRegex.exec(this._innerHTML)) !== null) {
        const attrsStr = match[1];
        const btnText = match[2];
        const btnListeners = {};
        const attrs = {};
        const attrRegex = /([a-zA-Z0-9_-]+)(?:=["']([^"']*)["'])?/g;
        let aMatch;
        while ((aMatch = attrRegex.exec(attrsStr)) !== null) {
          attrs[aMatch[1]] = aMatch[2] !== undefined ? aMatch[2] : true;
        }
        const classNames = (attrs.class || '').split(/\s+/).filter(Boolean);
        const isDisabled = attrsStr.includes('disabled') || attrs.disabled === true;
        const btn = {
          textContent: btnText,
          disabled: isDisabled,
          getAttribute: name => attrs[name] !== undefined ? attrs[name] : null,
          setAttribute: (name, val) => { attrs[name] = val; },
          classList: {
            contains: c => classNames.includes(c)
          },
          addEventListener: (evt, fn) => {
            btnListeners[evt] = btnListeners[evt] || [];
            btnListeners[evt].push(fn);
          },
          click: () => {
            if (!btn.disabled && btnListeners['click']) {
              btnListeners['click'].forEach(fn => fn({ target: btn }));
            }
          }
        };
        this._buttons.push(btn);
      }
    },
    querySelectorAll: selector => {
      if (selector === '.solar-choice-apply') {
        return el._buttons.filter(b => b.classList.contains('solar-choice-apply'));
      }
      if (selector === 'button[data-alt-idx]') {
        return el._buttons.filter(b => b.getAttribute('data-alt-idx') != null);
      }
      return el._buttons;
    },
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
    dispatchEvent: evtName => {
      (listeners[evtName] || []).forEach(h => h({ target: el }));
    },
    onclick: null
  };
  return el;
}

const $ = id => {
  if (!elements.has(id)) {
    elements.set(id, makeMockElement(id));
  }
  return elements.get(id);
};

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = (n, d = 0) => Number(n ?? 0).toLocaleString('en-IE', { maximumFractionDigits: d, minimumFractionDigits: d });
const fmt = (n, type = 'number') => n == null ? '—' : type === 'eur' ? '€' + nf(n) : type === 'eur2' ? '€' + nf(n, 2) : type === 'pct' ? nf(n * 100, 1) + '%' : type === 'lcoe' ? '€' + nf(n, 3) : type === 'years' ? nf(n, 1) + ' y' : nf(n);
const formatPaybackYearsValue = (val, unit = ' y') => {
  const num = (val && typeof val === 'object') ? val.median : val;
  if (num !== null && num !== undefined && Number.isFinite(Number(num))) {
    return `${nf(Number(num), 1)}${unit}`;
  }
  return num === null ? 'Not reached' : '—';
};
const dayhourScenarioLabel = s => ({ baseline: 'Historical electricity', electrified: 'Electrified · gas eliminated', ev: 'Electrified + EV' })[s] || s;
const chartTarget = detail => `class="chart-point" tabindex="0" role="img" aria-label="${esc(detail)}" data-chart-detail="${esc(detail)}"`;
const chartSvg = (label, body, height = 285, width = 720) => `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}">${body}</svg>`;
const table = (headers, rows) => `<table><thead><tr>${headers.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(x => `<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const title = s => s.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

const storage = new Map();
const mockLocalStorage = {
  getItem: k => storage.has(k) ? storage.get(k) : null,
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: k => storage.delete(k)
};

// Extract real normalizeBatterySettings from app.js
const normStart = source.indexOf('function normalizeBatterySettings');
const normEnd = source.indexOf('function clearEditedQuoteOverrides', normStart);
const normCode = source.slice(normStart, normEnd);

// Extract formatBatteryComposition and BATTERY_SETTINGS from app.js
const fmtStart = source.indexOf('function formatBatteryComposition');
const fmtEnd = source.indexOf('function batterySummaryText', fmtStart);
const fmtCode = fmtStart >= 0 && fmtEnd > fmtStart ? source.slice(fmtStart, fmtEnd) : '';

const batSetStart = source.indexOf('const BATTERY_SETTINGS=');
const batSetEnd = source.indexOf(';', batSetStart) + 1;
const batSetCode = batSetStart >= 0 ? source.slice(batSetStart, batSetEnd) : '';

// Extract battery controls init from app.js (to test batQuote10 / batQuote15 hook)
const batIdsStart = source.indexOf('const BATTERY_STORAGE_KEY =');
const batIdsEnd = source.indexOf('function renderBatterySizing', batIdsStart);
const batControlsCode = source.slice(batIdsStart, batIdsEnd);

// Extract renderDesignUnifiedComparisonTable from app.js
const unifiedTableStart = source.indexOf('function renderDesignUnifiedComparisonTable');
const unifiedTableEnd = source.indexOf('function renderRoofPills', unifiedTableStart);
const unifiedTableCode = unifiedTableStart >= 0 && unifiedTableEnd > unifiedTableStart ? source.slice(unifiedTableStart, unifiedTableEnd) : '';

// Extract getWeatherEnsembleMeta from app.js
const weatherMetaStart = source.indexOf('function getWeatherEnsembleMeta');
assert(weatherMetaStart >= 0, 'function getWeatherEnsembleMeta marker is present in app.js');
const weatherMetaEnd = source.indexOf('function schedule', weatherMetaStart);
assert(weatherMetaEnd > weatherMetaStart, 'function schedule marker is present in app.js');
const weatherMetaCode = source.slice(weatherMetaStart, weatherMetaEnd);

// Extract solar sizing section
const start = source.indexOf('/* SOLAR SIZING SECTION */');
assert(start >= 0, '/* SOLAR SIZING SECTION */ marker is present in app.js');
const end = source.indexOf('/* END SOLAR SIZING */', start);
assert(end > start, '/* END SOLAR SIZING */ marker is present in app.js');

let apiCalls = [];
let apiHandler = async (url, body) => {
  apiCalls.push({ url, body });
  return mockSolarFixture;
};

let scheduleCalls = [];
let syncSettingCalls = [];
let syncQuoteCalls = 0;
let batteryAnalysisCalls = 0;
let isBatterySizingMatchingStub = () => false;

const context = vm.createContext({
  $, esc, nf, fmt, formatPaybackYearsValue, dayhourScenarioLabel, chartTarget, chartSvg, table, title, console,
  solarAppliedMessage: '',
  hideChartTooltip: () => {},
  showError: err => { console.error('UI Error:', err.message || err); },
  document: {
    querySelectorAll: sel => {
      let results = [];
      for (const el of elements.values()) {
        if (typeof el.querySelectorAll === 'function') {
          results.push(...el.querySelectorAll(sel));
        }
      }
      return results;
    }
  },
  localStorage: mockLocalStorage,
  structuredClone: v => JSON.parse(JSON.stringify(v)),
  deepClone: v => JSON.parse(JSON.stringify(v)),
  settings: {
    south_panels: 12, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
    panel_watts: 440, battery_kwh: 5.0, battery_modules: 1, battery_module_kwh: 5.0,
    inverter_kw: 6.0, charge_kw: 5.0, discharge_kw: 5.0, roundtrip_efficiency: 0.9,
    pv_fixed_cost: 1500, pv_cost_kwp: 900, battery_cost_kwh: 550,
    horizon_years: 20, load_scenario: 'electrified', deduction_rate: 0.5, dormer_south_proxy: false
  },
  quote: {
    installer: 'Contractor A',
    module_model: 'Standard 440W',
    battery_nameplate_kwh: 5.5,
    battery_dod: 0.9,
    continuous_charge_kw: 5.0,
    continuous_discharge_kw: 5.0,
    price_basis: 'itemized',
    line_items: []
  },
  optimizerPrefs: { panel_maxima: { south: 22, west: 11, east: 6, dormer: 4, flat: 8, secondary_south: 5 } },
  defaultRoofLimits: { south: 22, west: 11, east: 6, dormer: 4, flat: 8, secondary_south: 5 },
  syncSetting: key => syncSettingCalls.push(key),
  syncQuoteFields: () => { syncQuoteCalls++; },
  cleanSettingsForBackend: snapshot => snapshot,
  renderControls: () => {},
  renderRoofLimitsComparison: () => {},
  schedule: opts => scheduleCalls.push(opts),
  api: (url, body) => apiHandler(url, body),
  runBatterySizingAnalysis: () => { batteryAnalysisCalls++; },
  renderBatterySizing: () => {}
});

// Load real normalizeBatterySettings, battery sizing controls, and solar sizing section into vm
vm.runInContext(normCode, context);
if (batSetCode) vm.runInContext(batSetCode, context);
if (fmtCode) vm.runInContext(fmtCode, context);
vm.runInContext(batControlsCode, context);
if (unifiedTableCode) vm.runInContext(unifiedTableCode, context);
vm.runInContext(weatherMetaCode, context);
vm.runInContext(source.slice(start, end), context);
context.isBatterySizingMatching = (...args) => isBatterySizingMatchingStub(...args);

const mockSolarFixture = {
  status: 'SCENARIO_PENDING',
  compute_seconds: 1.25,
  settings: { panel_watts: 440, inverter_kw: 6.0, load_scenario: 'electrified', horizon_years: 20 },
  recommended_candidate: {
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
    settings: {
      load_scenario: 'electrified',
      deduction_rate: 0.5,
      battery_cost_kwh: 550,
      inverter_kw: 6.0,
      charge_kw: 5.0,
      discharge_kw: 5.0,
      roundtrip_efficiency: 0.9,
      dormer_south_proxy: false
    }
  },
  size_curve: [
    {
      panel_count: 0, kwp: 0.0, battery_kwh: 0.0, battery_modules: 0, capex_eur: 0,
      npv_median: 0, npv_min: 0, npv_max: 0, discounted_payback_years: null,
      autarky_median: 0.0, self_consumption_median: 0.0, is_recommended: false,
      marginal_npv_per_panel: null, marginal_npv_per_kwp: null, delta_panels: 0, delta_battery_kwh: 0
    },
    {
      panel_count: 12, kwp: 5.28, battery_kwh: 5.0, battery_modules: 1, capex_eur: 9000,
      npv_median: 8500, npv_min: 7100, npv_max: 10200, discounted_payback_years: 8.2,
      autarky_median: 0.61, self_consumption_median: 0.72, is_recommended: false,
      marginal_npv_per_panel: 708.33, marginal_npv_per_kwp: 1609.85, delta_panels: 12, delta_battery_kwh: 5.0
    },
    {
      panel_count: 18, kwp: 7.92, battery_kwh: 10.0, battery_modules: 2, capex_eur: 14128,
      npv_median: 11200, npv_min: 9100, npv_max: 13500, discounted_payback_years: 7.8,
      autarky_median: 0.74, self_consumption_median: 0.62, is_recommended: true,
      marginal_npv_per_panel: 450.0, marginal_npv_per_kwp: 1022.73, delta_panels: 6, delta_battery_kwh: 5.0
    },
    {
      panel_count: 24, kwp: 10.56, battery_kwh: 10.0, battery_modules: 2, capex_eur: 18500,
      npv_median: 10800, npv_min: 8500, npv_max: 13000, discounted_payback_years: 9.4,
      autarky_median: 0.78, self_consumption_median: 0.51, is_recommended: false,
      marginal_npv_per_panel: -66.67, marginal_npv_per_kwp: -151.52, delta_panels: 6, delta_battery_kwh: 0.0
    }
  ],
  tradeoff_matrix: {
    pv_axis_kwp: [0.0, 5.28, 7.92, 10.56],
    battery_axis_kwh: [0.0, 5.0, 10.0],
    cells: [
      { pv_kwp: 0.0, battery_kwh: 0.0, npv_median: 0, npv_min: 0, npv_max: 0, capex_eur: 0 },
      { pv_kwp: 5.28, battery_kwh: 0.0, npv_median: 6200, npv_min: 5100, npv_max: 7500, capex_eur: 6252 },
      { pv_kwp: 5.28, battery_kwh: 5.0, npv_median: 8500, npv_min: 7100, npv_max: 10200, capex_eur: 9000 },
      { pv_kwp: 7.92, battery_kwh: 5.0, npv_median: 9800, npv_min: 8100, npv_max: 11900, capex_eur: 11378 },
      { pv_kwp: 7.92, battery_kwh: 10.0, npv_median: 11200, npv_min: 9100, npv_max: 13500, capex_eur: 14128 },
      { pv_kwp: 10.56, battery_kwh: 10.0, npv_median: 10800, npv_min: 8500, npv_max: 13000, capex_eur: 18500 }
    ]
  },
  comparisons: [
    {
      id: 'no_install', label: 'No-install baseline', panel_count: 0, kwp: 0.0,
      battery_kwh: 0.0, capex_eur: 0, npv_median: 0, npv_min: 0, npv_max: 0,
      discounted_payback_years: null, autarky_median: 0.0, self_consumption_median: 0.0,
      annual_pv_kwh_median: 0, annual_grid_import_kwh_median: 4800
    },
    {
      id: 'current', label: 'Current scenario settings', panel_count: 12, kwp: 5.28,
      battery_kwh: 5.0, capex_eur: 9000, npv_median: 8500, npv_min: 7100, npv_max: 10200,
      discounted_payback_years: 8.2, autarky_median: 0.61, self_consumption_median: 0.72,
      annual_pv_kwh_median: 6900, annual_grid_import_kwh_median: 2400
    },
    {
      id: 'oversized_test', label: 'Oversized reference system', panel_count: 24, kwp: 10.56,
      battery_kwh: 10.0, capex_eur: 18500, npv_median: 10800, npv_min: 8500, npv_max: 13000,
      discounted_payback_years: 9.4, autarky_median: 0.78, self_consumption_median: 0.51,
      annual_pv_kwh_median: 13000, annual_grid_import_kwh_median: 1600,
      exceeds_inverter_ceiling: true,
      inverter_ceiling_note: 'Exceeds selected inverter ceiling (6.00 kW)'
    },
    {
      id: 'recommended', label: 'Recommended system', panel_count: 18, kwp: 7.92,
      battery_kwh: 10.0, capex_eur: 14128, npv_median: 11200, npv_min: 9100, npv_max: 13500,
      discounted_payback_years: 7.8, autarky_median: 0.74, self_consumption_median: 0.62,
      annual_pv_kwh_median: 10200, annual_grid_import_kwh_median: 1850
    }
  ],
  search_summary: {
    objective: 'npv_eur',
    objective_label: 'Maximize median discounted incremental project NPV',
    exact: false,
    best_found: true,
    certified_global_optimum: false,
    search_method: 'bounded_discrete_sampling_and_refinement',
    evaluated_count: 128,
    search_space_size: 14200,
    evaluation_budget: 350,
    battery_mode: 'joint',
    inverter_kw: 6.0,
    inverter_panel_ceiling: 13,
    inverter_kwp_ceiling: 5.72,
    inverter_ceiling_enforced: true,
    roof_yield_priority: ['south', 'secondary_south', 'flat', 'east', 'west', 'dormer'],
    roof_production_kwh_kwp: { south: 1382, secondary_south: 1382, flat: 1225, east: 1213, west: 1167, dormer: 816 },
    weather_count: 19,
    effective_roof_limits: { south: 22, west: 11, east: 6, dormer: 4, flat: 8, secondary_south: 5 },
    battery_capacities_evaluated: [0.0, 5.0, 10.0, 15.0, 20.0],
    battery_module_kwh: 5.0,
    limitations: [
      'Search space was explored via bounded deterministic coordinate sampling and local refinement.',
      'The inverter AC limit is held fixed at 6.00 kW.',
      'Economic evaluation covers all 19 historical PVGIS weather analogues.'
    ]
  },
  cost_assumptions: [
    { item: 'PV fixed benchmark cost', value: '1,500 €', source: 'Benchmark catalog', status: 'provided' },
    { item: 'PV marginal benchmark cost', value: '900 €/kWp', source: 'Benchmark catalog', status: 'provided' },
    { item: 'Battery marginal cost', value: '550 €/usable kWh', source: 'Research benchmark', status: 'benchmark' }
  ]
};

context.fixture = mockSolarFixture;

async function runTests() {
  // Set initial element values
  $('solarScenario').value = 'electrified';
  $('solarObjective').value = 'npv_eur';
  $('solarBatteryMode').value = 'joint';
  $('solarBatteryCostMode').value = 'purchased';
  $('solarBatteryMin').value = '0';
  $('solarBatteryMax').value = '20';
  $('solarBatteryStep').value = '5';
  $('solarIncludeTheoretical').value = 'true';
  $('solarDeduction').value = '0.5';
  $('solarRepresentative').value = 'median';
  $('solarNpvSacrifice').value = '5';
  $('solarCoverageTarget').value = '70';
  $('batQuote10').value = '';
  $('batQuote15').value = '';

  // Test 1: Initialize controls
  vm.runInContext('initSolarSizingControls()', context);
  assert.equal(typeof $('runSolarSizing').onclick, 'function', 'runSolarSizing onclick handler registered');
  assert.equal(typeof $('applySolarSystem').onclick, 'function', 'applySolarSystem onclick handler registered');

  // Test 2: Mode switching & joint mode cannot be existing
  $('solarBatteryMode').value = 'fixed';
  $('solarBatteryMode').dispatchEvent('change');
  assert.strictEqual($('solarBatteryMin').disabled, true, 'battery min is disabled in fixed mode');
  assert.strictEqual($('solarBatteryMax').disabled, true, 'battery max is disabled in fixed mode');
  assert.strictEqual($('solarBatteryStep').disabled, true, 'battery step is disabled in fixed mode');
  assert.strictEqual($('solarBatteryCostMode').disabled, false, 'battery cost mode is enabled in fixed mode');

  // Select 'existing' in fixed mode
  $('solarBatteryCostMode').value = 'existing';
  $('solarBatteryCostMode').dispatchEvent('change');
  assert.strictEqual($('solarBatteryCostMode').value, 'existing', 'existing storage selected in fixed mode');

  // Switch back to joint mode: cost mode MUST reset to 'purchased' and be disabled
  $('solarBatteryMode').value = 'joint';
  $('solarBatteryMode').dispatchEvent('change');
  assert.strictEqual($('solarBatteryMin').disabled, false, 'battery min is enabled in joint mode');
  assert.strictEqual($('solarBatteryStep').disabled, true, 'battery step is disabled in joint mode because it follows module capacity');
  assert.strictEqual($('solarBatteryCostMode').disabled, true, 'battery cost mode is disabled in joint mode');
  assert.strictEqual($('solarBatteryCostMode').value, 'purchased', 'joint mode canonicalized cost mode to purchased');

  // Test 2b: Storage step synchronizes with battery module settings (e.g. 2.35 kWh)
  context.settings.battery_module_kwh = 2.35;
  context.settings.battery_modules = 4;
  context.settings.battery_kwh = 9.4;
  vm.runInContext('syncSolarBatteryStep()', context);
  assert.strictEqual($('solarBatteryStep').value, '2.35', 'solarBatteryStep value synchronized with 2.35 kWh');
  assert.strictEqual($('solarBatteryStep').disabled, true, 'solarBatteryStep remains disabled');
  assert($('solarBatteryStepHelp').textContent.includes('2.35'), 'Help text mentions 2.35 kWh/mod');
  assert($('solarBatteryStepHelp').textContent.includes('4 mod'), 'Help text mentions installed modules');
  const opts = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(opts.battery_step_kwh, 2.35, 'getSolarOptions returns 2.35 battery_step_kwh');

  // Test 2c: Stale localStorage does not overwrite active battery module step
  mockLocalStorage.setItem('pv-lab-solar-sizing-v1', JSON.stringify({ battery_step_kwh: 5.0, battery_min_kwh: 0, battery_max_kwh: 20 }));
  vm.runInContext('loadSolarOptionsFromStorage()', context);
  assert.strictEqual($('solarBatteryStep').value, '2.35', 'Stale stored step 5.0 does not override active 2.35 kWh module setting');

  // Test 2d: Zero / invalid module capacity does not invent 5.0 in UI and directs user to left menu
  context.settings.battery_module_kwh = 0.0;
  context.settings.battery_modules = 0;
  context.settings.battery_kwh = 0.0;
  vm.runInContext('syncSolarBatteryStep()', context);
  assert.strictEqual($('solarBatteryStep').value, '', 'solarBatteryStep value empty when unset');
  assert($('solarBatteryStepHelp').textContent.includes('left menu'), 'Help text directs to left menu when unset');
  $('solarBatteryMode').value = 'joint';
  $('solarBatteryMax').value = '10';
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert($('solarStatus').textContent.includes('left menu'), 'Status displays actionable error directing to left menu');

  // Test 2e: Valid 0.4 kWh and >25 kWh modules do not throw arbitrary 0.5-25 step errors
  context.settings.battery_module_kwh = 0.4;
  context.settings.battery_modules = 1;
  context.settings.battery_kwh = 0.4;
  vm.runInContext('syncSolarBatteryStep()', context);
  assert.strictEqual($('solarBatteryStep').value, '0.4', 'Step value matches 0.4');
  let currentOpts = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(currentOpts.battery_step_kwh, 0.4, 'getSolarOptions returns 0.4');

  context.settings.battery_module_kwh = 28.0;
  context.settings.battery_modules = 1;
  context.settings.battery_kwh = 28.0;
  vm.runInContext('syncSolarBatteryStep()', context);
  assert.strictEqual($('solarBatteryStep').value, '28', 'Step value matches 28');
  currentOpts = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(currentOpts.battery_step_kwh, 28.0, 'getSolarOptions returns 28.0');

  // Test 2f: Fixed mode and joint zero-only mode with zero module and zero storage execute successfully through real backend
  context.settings.battery_module_kwh = 0.0;
  context.settings.battery_modules = 0;
  context.settings.battery_kwh = 0.0;
  $('solarBatteryMode').value = 'fixed';
  vm.runInContext('updateSolarBatteryModeUi()', context);
  assert.strictEqual($('solarBatteryStep').disabled, true, 'Step disabled in fixed mode');

  // Constrain roof limits temporarily to keep integration simulation fast
  const fastRoofLimits2f = { south: 3, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 };
  const origRoofLimits = context.optimizerPrefs.panel_maxima;
  context.optimizerPrefs.panel_maxima = fastRoofLimits2f;

  // Verify actual UI request payload omits battery_step_kwh when no valid positive module
  const fixedZeroUiOpts = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(fixedZeroUiOpts.battery_step_kwh, undefined, 'UI options omit battery_step_kwh when module capacity is 0');

  // Prelaunch independent fixed-zero backend jobs together
  const fixedZeroPromise = prefetch(pythonSolarSizing(context.settings, fixedZeroUiOpts));
  const fixedZeroDirectPromise = prefetch(pythonSolarSizing(context.settings, { ...fixedZeroUiOpts, battery_step_kwh: 0 }));

  // Execute UI request payload through real backend for fixed-zero mode
  const fixedZeroRes = await fixedZeroPromise;
  assert(fixedZeroRes && fixedZeroRes.recommended_candidate, 'Real backend executes fixed-zero UI payload successfully');
  assert.strictEqual(fixedZeroRes.recommended_candidate.battery_kwh, 0.0, 'Fixed-zero recommendation has 0 kWh storage');

  // Direct API zero-step form executes successfully in fixed mode
  const fixedZeroDirectRes = await fixedZeroDirectPromise;
  assert(fixedZeroDirectRes && fixedZeroDirectRes.recommended_candidate, 'Real backend executes direct API fixed-zero with battery_step_kwh=0');
  assert.strictEqual(fixedZeroDirectRes.recommended_candidate.battery_kwh, 0.0, 'Direct API fixed-zero recommendation has 0 kWh storage');

  // Joint zero-only mode (max = 0) with zero module also executes successfully
  $('solarBatteryMode').value = 'joint';
  $('solarBatteryMin').value = '0';
  $('solarBatteryMax').value = '0';
  vm.runInContext('updateSolarBatteryModeUi()', context);

  const jointZeroUiOpts = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(jointZeroUiOpts.battery_step_kwh, undefined, 'UI options omit battery_step_kwh in joint zero-only mode');

  // Prelaunch independent joint zero-only backend jobs together
  const jointZeroPromise = prefetch(pythonSolarSizing(context.settings, jointZeroUiOpts));
  const jointZeroDirectPromise = prefetch(pythonSolarSizing(context.settings, { ...jointZeroUiOpts, battery_step_kwh: 0 }));

  // Execute UI request payload through real backend for joint zero-only mode
  const jointZeroRes = await jointZeroPromise;
  assert(jointZeroRes && jointZeroRes.recommended_candidate, 'Real backend executes joint-zero-only UI payload successfully');
  assert.strictEqual(jointZeroRes.recommended_candidate.battery_kwh, 0.0, 'Joint-zero-only recommendation has 0 kWh storage');

  // Direct API zero-step form executes successfully in joint zero-only mode
  const jointZeroDirectRes = await jointZeroDirectPromise;
  assert(jointZeroDirectRes && jointZeroDirectRes.recommended_candidate, 'Real backend executes direct API joint-zero with battery_step_kwh=0');
  assert.strictEqual(jointZeroDirectRes.recommended_candidate.battery_kwh, 0.0, 'Direct API joint-zero recommendation has 0 kWh storage');

  // Restore roof limits
  context.optimizerPrefs.panel_maxima = origRoofLimits;

  // Restore 5.0 kWh default for subsequent tests
  context.settings.battery_module_kwh = 5.0;
  context.settings.battery_modules = 1;
  context.settings.battery_kwh = 5.0;
  $('solarBatteryMode').value = 'joint';
  $('solarBatteryMin').value = '0';
  $('solarBatteryMax').value = '20';
  vm.runInContext('updateSolarBatteryModeUi()', context);
  vm.runInContext('syncSolarBatteryStep()', context);

  // Matching battery results are reused; missing or stale results are recomputed.
  apiCalls = [];
  batteryAnalysisCalls = 0;
  isBatterySizingMatchingStub = () => false;
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert(apiCalls.some(c => c.url === '/api/solar-sizing'), 'Solar sizing still runs with stale battery sizing');
  assert.strictEqual(batteryAnalysisCalls, 1, 'Stale or missing battery sizing starts companion analysis');
  apiCalls = [];
  batteryAnalysisCalls = 0;
  isBatterySizingMatchingStub = () => true;
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert(apiCalls.some(c => c.url === '/api/solar-sizing'), 'Solar sizing still runs with matching battery sizing');
  assert.strictEqual(batteryAnalysisCalls, 0, 'Matching battery sizing skips companion analysis');
  isBatterySizingMatchingStub = () => false;

  // Test 3: LocalStorage persistence
  $('solarScenario').value = 'ev';
  $('solarObjective').value = 'autarky';
  $('solarScenario').dispatchEvent('input');
  const stored = JSON.parse(mockLocalStorage.getItem('pv-lab-solar-sizing-v1'));
  assert.strictEqual(stored.load_scenario, 'ev', 'load scenario saved in storage');
  assert.strictEqual(stored.objective, 'autarky', 'objective saved in storage');

  // Test 4: Render functions populate elements
  vm.runInContext('renderSolarSizing(fixture)', context);
  assert($('solarRecTitle').textContent.includes('18 Panels'), 'Rec title includes panel count');
  assert($('solarRecCards').innerHTML.includes('14,128'), 'Rec cards include capex');
  assert($('solarRecCards').innerHTML.includes('20-Yr Project NPV'), 'Rec cards include dynamic horizon Project NPV');
  assert($('solarRoofPills').innerHTML.includes('South:'), 'Roof pills include South');
  assert($('solarComparisonsTable').innerHTML.includes('No-install baseline'), 'Comparisons table includes baseline');
  assert($('solarComparisonsTable').innerHTML.includes('Exceeds PV array ceiling'), 'Comparisons table renders exceeds PV array ceiling warning tag');
  assert($('solarChart1').innerHTML.includes('<svg'), 'Chart 1 SVG rendered');
  assert($('solarTable1').innerHTML.includes('18 panels ★'), 'Table 1 includes recommended mark');
  assert($('solarChart2').innerHTML.includes('<svg'), 'Chart 2 SVG rendered');
  assert($('solarDiminishingNotice').innerHTML.includes('Finite differences between evaluated sizes'), 'Diminishing returns notice non-causal');
  assert($('solarChart3').innerHTML.includes('<svg'), 'Chart 3 Heatmap SVG rendered');
  assert($('solarSearchSummaryCards').innerHTML.includes('Bounded Sampling'), 'Search summary includes method');
  assert($('solarSearchSummaryCards').innerHTML.includes('Maximum PV Array'), 'Search summary card separates PV array limit from inverter AC capacity');
  assert($('solarSearchSummaryCards').innerHTML.includes('13 whole panels at 440 W each'), 'Search summary card shows the whole-panel PV ceiling');
  assert($('solarLimitationsList').innerHTML.includes('Roof priority by modeled annual yield'), 'Notices include dynamic roof priority');
  assert($('solarLimitationsList').innerHTML.includes('<li>'), 'Limitations list populated');

  // Test 5: Stale invalidation
  $('solarResultsWrap').classList.remove('stale');
  vm.runInContext('markSolarSizingStale()', context);
  assert($('solarResultsWrap').classList.contains('stale'), 'solarResultsWrap has stale class after markSolarSizingStale');
  assert.strictEqual($('applySolarSystem').disabled, true, 'applySolarSystem is disabled when stale');
  assert.strictEqual($('solarStatus').textContent, 'Inputs changed · Click Run sizing analysis to update');

  // Test 6: batQuote10 and batQuote15 hook invalidates solar sizing
  vm.runInContext('renderSolarSizing(fixture)', context);
  $('solarResultsWrap').classList.remove('stale');
  $('applySolarSystem').disabled = false;
  $('batQuote10').value = '9000';
  $('batQuote10').dispatchEvent('input');
  assert($('solarResultsWrap').classList.contains('stale'), 'Editing batQuote10 marks solar results stale');
  assert.strictEqual($('applySolarSystem').disabled, true, 'Editing batQuote10 disables apply');

  vm.runInContext('renderSolarSizing(fixture)', context);
  $('solarResultsWrap').classList.remove('stale');
  $('applySolarSystem').disabled = false;
  $('batQuote15').value = '12000';
  $('batQuote15').dispatchEvent('input');
  assert($('solarResultsWrap').classList.contains('stale'), 'Editing batQuote15 marks solar results stale');
  assert.strictEqual($('applySolarSystem').disabled, true, 'Editing batQuote15 disables apply');

  // Reset quote values for standard runs
  $('batQuote10').value = '';
  $('batQuote15').value = '';

  // Test 7: Run analysis and fingerprint registration
  apiCalls = [];
  await $('runSolarSizing').onclick();
  assert.strictEqual(apiCalls.length, 1, 'API called once');
  assert.strictEqual(apiCalls[0].url, '/api/solar-sizing');
  assert.strictEqual(apiCalls[0].body.options.storage_cost_mode, 'purchased', 'Joint mode posts storage_cost_mode=purchased');
  assert(!$('solarResultsWrap').classList.contains('stale'), 'stale class removed after successful run');
  assert.strictEqual($('applySolarSystem').disabled, false, 'applySolarSystem enabled after successful run');

  // Test 8: Fingerprint stale guards on Apply
  // Programmatically change setting without running analysis
  context.settings.south_panels = 99;
  scheduleCalls = [];
  $('applySolarSystem').onclick();
  assert.strictEqual(scheduleCalls.length, 0, 'Apply blocked when fingerprint changed');
  assert($('solarApplyStatus').textContent.includes('inputs changed since sizing analysis'), 'Apply notes fingerprint mismatch');
  context.settings.south_panels = 12; // restore

  // Test 9: In-flight change protection
  let resolveSlow;
  apiHandler = () => new Promise(res => { resolveSlow = res; });
  const inFlightPromise = $('runSolarSizing').onclick();
  // While running, user edits deduction
  $('solarDeduction').value = '0';
  resolveSlow(mockSolarFixture);
  await inFlightPromise;
  assert($('solarResultsWrap').classList.contains('stale'), 'Late response with altered input marked stale');
  assert.strictEqual($('applySolarSystem').disabled, true, 'Apply disabled when inputs modified during fetch');
  $('solarDeduction').value = '0.5';

  // Test 10: Graceful handling of no feasible configuration
  const infeasibleFixture = {
    status: 'NO_FEASIBLE_CONFIGURATIONS',
    recommended_candidate: null,
    search_summary: { exact: true, evaluated_count: 0, search_space_size: 1, battery_mode: 'fixed' },
    comparisons: [],
    size_curve: [],
    tradeoff_matrix: { pv_axis_kwp: [], battery_axis_kwh: [], cells: [] }
  };
  context.infeasibleFixture = infeasibleFixture;
  vm.runInContext('renderSolarSizing(infeasibleFixture)', context);
  assert.strictEqual($('applySolarSystem').disabled, true, 'Apply disabled when no feasible candidate');
  assert($('solarStatus').textContent.includes('No feasible solar installation configurations found'), 'Infeasible status shown');
  assert($('solarRecommendedBanner').innerHTML.includes('No feasible configuration found'), 'Infeasible banner rendered');
  scheduleCalls = [];
  $('applySolarSystem').onclick();
  assert.strictEqual(scheduleCalls.length, 0, 'Apply no-op when no recommendation');

  // Test 11: CRITICAL APPLY CONSISTENCY & INTEGRATION PARITY WITH REAL BACKEND EVALUATE
  console.log('Running Critical Apply Consistency & Python Evaluation Parity Test...');
  const basePreSettings = {
    south_panels: 10, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
    panel_watts: 440, battery_kwh: 5.0, battery_modules: 1, battery_module_kwh: 5.0,
    inverter_kw: 6.0, charge_kw: 2.5, discharge_kw: 2.5, roundtrip_efficiency: 0.9,
    pv_fixed_cost: 1500, pv_cost_kwp: 900, battery_cost_kwh: 550,
    horizon_years: 20, load_scenario: 'baseline', deduction_rate: 0.36,
    dormer_south_proxy: true, quote_total: 18000
  };
  const basePreQuote = {
    installer: 'Contractor X',
    module_model: '440W Mono',
    quote_total: 18000,
    price_basis: 'itemized',
    continuous_charge_kw: 2.5,
    continuous_discharge_kw: 2.5,
    battery_nameplate_kwh: 12.0,
    battery_dod: 0.8,
    line_items: [
      { description: 'Modules', quantity: 1, net_eur: 8000, vat: 0.1 },
      { description: 'Inverter + Battery', quantity: 1, net_eur: 7000, vat: 0.1 }
    ]
  };

  const fastRoofLimits = { south: 8, west: 4, east: 0, dormer: 0, flat: 0, secondary_south: 0 };
  context.optimizerPrefs.panel_maxima = fastRoofLimits;

  const mod235Settings = {
    ...basePreSettings,
    battery_module_kwh: 2.35001,
    battery_modules: 4,
    battery_kwh: 4 * 2.35001
  };

  // Prelaunch independent sizing jobs for Test 11 and Test 12 together
  const realSizingPromise = prefetch(pythonSolarSizing(basePreSettings, {
    battery_mode: 'joint',
    battery_min_kwh: 0,
    battery_max_kwh: 10,
    battery_step_kwh: 5,
    panel_maxima: fastRoofLimits,
    load_scenario: 'electrified',
    deduction_rate: 0.5
  }, basePreQuote));

  const exactSizingPromise = prefetch(pythonSolarSizing(mod235Settings, {
    battery_mode: 'joint',
    battery_min_kwh: 0,
    battery_max_kwh: 10,
    panel_maxima: fastRoofLimits,
    load_scenario: 'electrified',
    deduction_rate: 0.5
  }));

  // Run real solar sizing on backend to obtain authoritative recommendation
  const realSizing = await realSizingPromise;

  const realRec = realSizing.recommended_candidate;
  assert(realRec, 'Authoritative sizing returned recommended candidate');
  assert(realRec.panel_count > 0, 'Recommended panels > 0');
  assert.strictEqual(realSizing.search_summary.battery_module_kwh, 5.0, 'Search summary battery_module_kwh preserves input module capacity 5.0');
  assert.deepStrictEqual(realSizing.search_summary.battery_capacities_evaluated, [0, 5, 10], 'Search capacities evaluated are multiples of 5.0');
  assert.strictEqual(realRec.battery_module_kwh, 5.0, 'Authoritative recommended candidate preserves input module capacity 5.0');
  assert.strictEqual(realRec.settings.battery_module_kwh, 5.0, 'Authoritative recommended candidate settings preserves input module capacity 5.0');

  // Set up context with legacy dirty state
  context.settings = structuredClone(basePreSettings);
  context.quote = structuredClone(basePreQuote);
  $('solarBatteryMode').value = 'joint';
  $('solarScenario').value = 'electrified';
  $('solarDeduction').value = '0.5';

  // Render authoritative sizing result into UI
  vm.runInContext(`renderSolarSizing(${JSON.stringify(realSizing)})`, context);
  // Synchronize fingerprint to simulate user directly following analysis run
  vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
  $('solarResultsWrap').classList.remove('stale');
  $('applySolarSystem').disabled = false;
  scheduleCalls = [];

  // Execute UI apply
  $('applySolarSystem').onclick();

  assert.strictEqual(scheduleCalls.length, 1, 'Schedule called after apply');
  assert.strictEqual(context.settings.quote_total, null, 'Old settings.quote_total cleared');
  assert.strictEqual(context.quote.quote_total, undefined, 'Old quote.quote_total cleared');
  assert.strictEqual(context.quote.price_basis, 'modeled', 'Quote price basis set to modeled');
  assert.strictEqual(context.quote.line_items.length, 2, 'Quote line items preserved as evidence');
  assert.strictEqual(context.quote.battery_nameplate_kwh, undefined, 'Quote battery nameplate override deleted');
  assert.strictEqual(context.quote.battery_dod, undefined, 'Quote battery DoD override deleted');
  assert.strictEqual(context.quote.continuous_charge_kw, undefined, 'Quote continuous charge override deleted');
  assert.strictEqual(context.quote.continuous_discharge_kw, undefined, 'Quote continuous discharge override deleted');
  assert.strictEqual(context.settings.dormer_south_proxy, false, 'dormer_south_proxy set to false');
  const appliedRec = realSizing.recommendation_choices?.balanced || realRec;
  assert.strictEqual(context.settings.load_scenario, appliedRec.settings.load_scenario, 'load_scenario applied');
  assert.strictEqual(context.settings.deduction_rate, appliedRec.settings.deduction_rate, 'deduction_rate applied');
  assert.strictEqual(context.settings.battery_kwh, appliedRec.battery_kwh, 'battery_kwh applied');
  assert.strictEqual(context.settings.battery_modules, appliedRec.battery_modules, 'battery_modules applied');
  assert.strictEqual(context.settings.battery_module_kwh, 5.0, 'Applied settings preserves original left-menu module capacity 5.0');
  assert.strictEqual(context.settings.battery_module_kwh, appliedRec.battery_module_kwh, 'battery_module_kwh applied');
  assert.strictEqual(context.settings.charge_kw, appliedRec.settings.charge_kw, 'charge_kw applied');
  assert.strictEqual(context.settings.discharge_kw, appliedRec.settings.discharge_kw, 'discharge_kw applied');
  assert.strictEqual(context.settings.battery_cost_kwh, appliedRec.battery_kwh > 0 ? appliedRec.settings.battery_cost_kwh : basePreSettings.battery_cost_kwh, 'battery price applied, or retained for future purchases with zero storage');
  assert.strictEqual(context.settings.pv_dispatch_model, 'physical', 'Physical dispatch mode applied');
  assert.strictEqual(context.settings.pv_battery_coupling, appliedRec.settings.pv_battery_coupling, 'Chosen topology applied');
  assert($('solarApplyStatus').textContent.includes('Applied'), 'Apply status confirms application with modeled capex note');

  // Verify full parity by evaluating the applied payload through the real Python engine
  const evalResult = await pythonEvaluate({ settings: context.settings, quote: context.quote });
  assert.strictEqual(evalResult.settings.quote_total, null, 'Python normalized settings quote_total is None');
  assert.strictEqual(evalResult.settings.dormer_south_proxy, false, 'Python validated settings proxy is False');
  assert.strictEqual(evalResult.settings.battery_module_kwh, 5.0, 'Python evaluated battery_module_kwh preserves 5.0');
  assert.strictEqual(evalResult.settings.battery_kwh, appliedRec.battery_kwh, 'Python evaluated battery_kwh matches');
  assert(Math.abs(evalResult.capex_eur - appliedRec.capex_eur) < 0.01, `Capex parity: eval ${evalResult.capex_eur} vs rec ${appliedRec.capex_eur}`);
  console.log(`Parity certified: Capex €${evalResult.capex_eur.toFixed(2)}, NPV €${evalResult.npv_median.toFixed(2)}`);

  // Test 12: Exact module product parity for module = 2.35001 kWh
  console.log('Running Exact Module Product Parity Test (module = 2.35001 kWh)...');
  const exactSizing = await exactSizingPromise;
  const exactRec = exactSizing.recommended_candidate;
  assert(exactRec, 'Exact sizing returned recommended candidate');
  assert.strictEqual(exactRec.battery_module_kwh, 2.35001, 'Recommended module_kwh matches 2.35001');
  assert.strictEqual(exactRec.battery_kwh, exactRec.battery_modules === 0 ? 0 : exactRec.battery_modules * 2.35001, 'Recommended battery_kwh is exact product');

  const exactApplied = exactSizing.recommendation_choices?.balanced || exactRec;
  context.settings = structuredClone(mod235Settings);
  context.quote = structuredClone(basePreQuote);
  vm.runInContext(`renderSolarSizing(${JSON.stringify(exactSizing)})`, context);
  vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
  $('solarResultsWrap').classList.remove('stale');
  $('applySolarSystem').disabled = false;
  $('applySolarSystem').onclick();

  assert.strictEqual(context.settings.battery_module_kwh, 2.35001, 'Applied settings.battery_module_kwh preserves 2.35001');
  assert.strictEqual(context.settings.battery_kwh, exactApplied.battery_kwh, 'Applied settings.battery_kwh matches exact product');
  const exactEval = await pythonEvaluate({ settings: context.settings, quote: context.quote });
  assert.strictEqual(exactEval.settings.battery_module_kwh, 2.35001, 'Python evaluated battery_module_kwh preserves 2.35001');
  assert.strictEqual(exactEval.settings.battery_kwh, exactApplied.battery_kwh, 'Python evaluated battery_kwh matches exact product');
  assert(Math.abs(exactEval.capex_eur - exactApplied.capex_eur) < 0.01, 'Capex parity certified for 2.35001');
  assert(Math.abs(exactEval.npv_median - exactApplied.npv_median) < 0.01, 'NPV parity certified for 2.35001');
  console.log(`Parity certified for 2.35001: Capex €${exactEval.capex_eur.toFixed(2)}, NPV €${exactEval.npv_median.toFixed(2)}`);

  // Test 13: UI client-side validation rejects empty whole-module interval
  context.settings.battery_module_kwh = 5.0;
  $('solarBatteryMode').value = 'joint';
  $('solarBatteryMin').value = '6.0';
  $('solarBatteryMax').value = '9.0';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on empty whole-module interval');
  assert($('solarStatus').textContent.includes('No whole battery module capacities exist'), 'Status shows actionable empty interval error');
  assert.strictEqual($('applySolarSystem').disabled, true, 'Apply disabled on validation failure');

  // Test 14: UI client-side validation rejects positive storage when no PV is allowed
  context.settings.battery_module_kwh = 2.76;
  $('solarBatteryMin').value = '8.0';
  $('solarBatteryMax').value = '20.0';
  context.optimizerPrefs.panel_maxima = { south: 0, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 };
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented when positive storage requested with no PV');
  assert($('solarStatus').textContent.includes('Positive storage capacity cannot be installed without solar PV panels'), 'Status shows actionable no-PV positive storage error');

  // Test 15: Negative constrained NPV renders advisory banner pill and search summary notice
  const negNpvFixture = {
    status: 'SCENARIO_PENDING',
    recommended_candidate: {
      panel_count: 5,
      kwp: 2.2,
      roof_counts: { south: 5, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      battery_kwh: 8.28,
      battery_modules: 3,
      battery_module_kwh: 2.76,
      capex_eur: 12000,
      npv_median: -520.0,
      npv_min: -800.0,
      npv_max: -250.0,
      discounted_payback_years: null,
      simple_payback_years: null,
      autarky_median: 0.65,
      self_consumption_median: 0.85,
      annual_pv_kwh_median: 3200,
      annual_grid_import_kwh_median: 1100,
      reached_inverter_ceiling: false,
      reached_roof_bound: false,
      bounds_note: 'The recommended system sits below ceiling. Note: All evaluated configurations show negative median project NPV.',
      settings: { ...basePreSettings, south_panels: 5, battery_kwh: 8.28, battery_modules: 3, battery_module_kwh: 2.76 }
    },
    search_summary: {
      exact: true,
      evaluated_count: 20,
      search_space_size: 20,
      inverter_kw: 6.0,
      weather_count: 19,
      battery_mode: 'joint',
      battery_module_kwh: 2.76,
      battery_capacities_evaluated: [8.28, 11.04, 13.8, 16.56, 19.32],
      battery_modules_evaluated: [3, 4, 5, 6, 7],
      advisory_note: 'The recommended configuration has a negative median project NPV (-520 €) under current cost assumptions. Refer to the separate no-install comparison baseline (€0 capex, €0 NPV).',
      limitations: ['Economic evaluation covers all 19 historical analogues.']
    },
    size_curve: [],
    tradeoff_matrix: { pv_axis_kwp: [2.2], battery_axis_kwh: [8.28], cells: [] },
    comparisons: [
      { id: 'no_install', label: 'No-install baseline', panel_count: 0, kwp: 0, battery_kwh: 0, capex_eur: 0, npv_median: 0 },
      { id: 'recommended', label: 'Recommended system', panel_count: 5, kwp: 2.2, battery_kwh: 8.28, capex_eur: 12000, npv_median: -520 }
    ]
  };
  vm.runInContext(`renderSolarSizing(${JSON.stringify(negNpvFixture)})`, context);
  assert($('solarRecommendedBanner').innerHTML.includes('Negative constrained NPV (refer to No-install baseline)'), 'Banner includes negative constrained NPV pill');
  assert($('solarLimitationsList').innerHTML.includes('Economic advisory:'), 'Summary notices include economic advisory for negative constrained NPV');

  // Test 16: Preference controls persistence and validation
  console.log('Running Test 16: Preference controls persistence & validation...');
  $('solarNpvSacrifice').value = '5';
  $('solarCoverageTarget').value = '70';
  let optsNow = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(optsNow.npv_sacrifice_pct, 5.0, 'Default sacrifice pct is 5');
  assert.strictEqual(optsNow.coverage_target_pct, 70.0, 'Default coverage target pct is 70');

  // Custom values (fractional support)
  $('solarNpvSacrifice').value = '3.5';
  $('solarCoverageTarget').value = '75.5';
  optsNow = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(optsNow.npv_sacrifice_pct, 3.5, 'Custom sacrifice pct is 3.5');
  assert.strictEqual(optsNow.coverage_target_pct, 75.5, 'Custom coverage target pct is 75.5');

  // Persistence to localStorage
  vm.runInContext('saveSolarOptionsToStorage()', context);
  const storedOpts = JSON.parse(mockLocalStorage.getItem('pv-lab-solar-sizing-v1'));
  assert.strictEqual(storedOpts.npv_sacrifice_pct, 3.5, 'Stored npv_sacrifice_pct is 3.5');
  assert.strictEqual(storedOpts.coverage_target_pct, 75.5, 'Stored coverage_target_pct is 75.5');

  // Reset inputs and restore from storage
  $('solarNpvSacrifice').value = '5';
  $('solarCoverageTarget').value = '70';
  vm.runInContext('loadSolarOptionsFromStorage()', context);
  assert.strictEqual($('solarNpvSacrifice').value, '3.5', 'Restored npv_sacrifice_pct is 3.5');
  assert.strictEqual($('solarCoverageTarget').value, '75.5', 'Restored coverage_target_pct is 75.5');

  // Validation rejects out-of-range sacrifice
  $('solarNpvSacrifice').value = '-5';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on negative sacrifice');
  assert($('solarStatus').textContent.includes('Allowed NPV sacrifice percentage must be a finite number between 0 and 100%'), 'Status shows sacrifice range error');

  $('solarNpvSacrifice').value = '105';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on >100 sacrifice');
  assert($('solarStatus').textContent.includes('Allowed NPV sacrifice percentage must be a finite number between 0 and 100%'), 'Status shows sacrifice range error');

  // Validation rejects blank sacrifice
  $('solarNpvSacrifice').value = '';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on blank sacrifice');
  assert($('solarStatus').textContent.includes('Allowed NPV sacrifice percentage must be a finite number between 0 and 100%'), 'Status shows error on blank sacrifice');

  // Validation rejects Infinity sacrifice
  $('solarNpvSacrifice').value = 'Infinity';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on Infinity sacrifice');
  assert($('solarStatus').textContent.includes('Allowed NPV sacrifice percentage must be a finite number between 0 and 100%'), 'Status shows error on Infinity sacrifice');

  // Validation rejects out-of-range coverage target
  $('solarNpvSacrifice').value = '5';
  $('solarCoverageTarget').value = '-1';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on negative coverage target');
  assert($('solarStatus').textContent.includes('Budget household demand coverage target must be a finite number between 0 and 100%'), 'Status shows coverage target range error');

  // Validation rejects blank coverage target
  $('solarCoverageTarget').value = '   ';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on blank coverage target');
  assert($('solarStatus').textContent.includes('Budget household demand coverage target must be a finite number between 0 and 100%'), 'Status shows error on blank coverage target');

  // Validation rejects NaN coverage target
  $('solarCoverageTarget').value = 'NaN';
  apiCalls = [];
  await vm.runInContext('runSolarSizingAnalysis()', context);
  assert.strictEqual(apiCalls.length, 0, 'API call prevented on NaN coverage target');
  assert($('solarStatus').textContent.includes('Budget household demand coverage target must be a finite number between 0 and 100%'), 'Status shows error on NaN coverage target');

  // Valid 0 values
  $('solarNpvSacrifice').value = '0';
  $('solarCoverageTarget').value = '0';
  const optsZero = vm.runInContext('getSolarOptions()', context);
  assert.strictEqual(optsZero.npv_sacrifice_pct, 0, 'Valid 0 sacrifice is accepted');
  assert.strictEqual(optsZero.coverage_target_pct, 0, 'Valid 0 coverage target is accepted');

  // Test 17: Rendering of 3 choice cards & coincidence / unavailable states
  console.log('Running Test 17: Recommendation choices cards rendering...');
  const choicesFixture = {
    ...mockSolarFixture,
    recommendation_choices: {
      financial: {
        role: 'financial', role_label: 'Best financial return',
        panel_count: 14, kwp: 6.16, battery_kwh: 0.0, battery_modules: 0, capex_eur: 7044,
        npv_median: 10000, npv_min: 8000, npv_max: 12000, autarky_median: 0.613, self_consumption_median: 0.85,
        discounted_payback_years: 6.2, simple_payback_years: 5.5,
        roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
        settings: { south_panels: 14, battery_kwh: 0.0, battery_modules: 0 }
      },
      balanced: {
        role: 'balanced', role_label: 'Balanced choice (Main)',
        panel_count: 14, kwp: 6.16, battery_kwh: 8.28, battery_modules: 3, capex_eur: 11598,
        npv_median: 9630, npv_min: 7600, npv_max: 11700, autarky_median: 0.813, self_consumption_median: 0.92,
        discounted_payback_years: 7.8, simple_payback_years: 6.8,
        allowed_sacrifice_pct: 5.0, npv_threshold_eur: 9500.0, retained_npv_pct: 96.3, coverage_gain_pct: 20.0,
        is_same_as_financial: false,
        roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
        settings: { south_panels: 14, battery_kwh: 8.28, battery_modules: 3 }
      },
      budget: {
        role: 'budget', role_label: 'Budget choice',
        panel_count: 14, kwp: 6.16, battery_kwh: 5.52, battery_modules: 2, capex_eur: 10080,
        npv_median: 9920, npv_min: 7900, npv_max: 11950, autarky_median: 0.740, self_consumption_median: 0.89,
        discounted_payback_years: 7.1, simple_payback_years: 6.2,
        coverage_target_pct: 70.0, capex_savings_vs_financial: -3036.0,
        is_same_as_financial: false, is_same_as_balanced: false,
        roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
        settings: { south_panels: 14, battery_kwh: 5.52, battery_modules: 2 }
      },
      preferences: { allowed_sacrifice_pct: 5.0, coverage_target_pct: 70.0 },
      main_recommendation: 'balanced'
    },
    top_alternatives: [
      {
        roles: ['Best financial return'], role_label: 'Best financial return',
        panel_count: 14, kwp: 6.16, battery_kwh: 0.0, battery_modules: 0, capex_eur: 7044,
        extra_capex_vs_financial: 0.0, npv_median: 10000, npv_min: 8000, npv_max: 12000,
        npv_diff_vs_financial: 0.0, retained_npv_pct: 100.0,
        autarky_median: 0.613, coverage_diff_vs_financial_pct: 0.0,
        annual_grid_import_kwh_median: 2200, annual_grid_export_kwh_median: 4000,
        discounted_payback_years: 6.2,
        settings: { south_panels: 14, battery_kwh: 0.0 }
      },
      {
        roles: ['Balanced choice (Main)'], role_label: 'Balanced choice (Main)',
        panel_count: 14, kwp: 6.16, battery_kwh: 8.28, battery_modules: 3, capex_eur: 11598,
        extra_capex_vs_financial: 4554.0, npv_median: 9630, npv_min: 7600, npv_max: 11700,
        npv_diff_vs_financial: -370.0, retained_npv_pct: 96.3,
        autarky_median: 0.813, coverage_diff_vs_financial_pct: 20.0,
        annual_grid_import_kwh_median: 1060, annual_grid_export_kwh_median: 1500,
        discounted_payback_years: 7.8,
        settings: { south_panels: 14, battery_kwh: 8.28 }
      },
      {
        roles: ['Budget choice', 'Lowest upfront investment'], role_label: 'Budget choice · Lowest upfront investment',
        panel_count: 14, kwp: 6.16, battery_kwh: 5.52, battery_modules: 2, capex_eur: 10080,
        extra_capex_vs_financial: 3036.0, npv_median: 9920, npv_min: 7900, npv_max: 11950,
        npv_diff_vs_financial: -80.0, retained_npv_pct: 99.2,
        autarky_median: 0.740, coverage_diff_vs_financial_pct: 12.7,
        annual_grid_import_kwh_median: 1480, annual_grid_export_kwh_median: 2200,
        discounted_payback_years: 7.1,
        settings: { south_panels: 14, battery_kwh: 5.52 }
      }
    ]
  };

  vm.runInContext(`renderSolarSizing(${JSON.stringify(choicesFixture)})`, context);
  const cardsHtml = $('solarChoiceCards').innerHTML;
  assert(cardsHtml.includes('Best financial return'), 'Renders Best financial return card');
  assert(cardsHtml.includes('Balanced choice'), 'Renders Balanced choice card');
  assert(cardsHtml.includes('Main recommendation'), 'Balanced choice has Main recommendation badge');
  assert(cardsHtml.includes('96.3%'), 'Balanced choice displays retained NPV percentage');
  assert(cardsHtml.includes('20%'), 'Balanced choice displays coverage gain vs financial');
  assert(cardsHtml.includes('Budget choice'), 'Renders Budget choice card');
  assert(cardsHtml.includes('Apply financial choice'), 'Has Apply financial choice button');
  assert(cardsHtml.includes('Apply recommended choice'), 'Has Apply recommended choice button');
  assert(cardsHtml.includes('Apply budget choice'), 'Has Apply budget choice button');

  // Test 18: Top alternatives table rendering
  console.log('Running Test 18: Top alternatives table rendering...');
  const altsHtml = $('solarAlternativesTable').innerHTML;
  assert(altsHtml.includes('Extra investment vs financial choice'), 'Alternatives table has extra capex header');
  assert(altsHtml.includes('Difference vs financial choice'), 'Alternatives table has NPV difference header');
  assert(altsHtml.includes('Household coverage'), 'Alternatives table has coverage header');
  assert(altsHtml.includes('Annual Exports'), 'Alternatives table has annual exports header');
  assert(altsHtml.includes('4,000 kWh/yr'), 'Displays annual grid exports for financial choice');
  assert(altsHtml.includes('(baseline)'), 'Baseline labeled for financial winner in alternatives table');

  // Test 19: Unavailable states and coincidence labels
  console.log('Running Test 19: Coincidence labels & unavailable states...');
  const coincidentFixture = {
    ...choicesFixture,
    recommendation_choices: {
      ...choicesFixture.recommendation_choices,
      balanced: {
        ...choicesFixture.recommendation_choices.balanced,
        battery_kwh: 0.0,
        battery_modules: 0,
        capex_eur: 7044,
        npv_median: 10000,
        retained_npv_pct: 100.0,
        coverage_gain_pct: 0.0,
        is_same_as_financial: true
      },
      budget: null
    }
  };
  vm.runInContext(`renderSolarSizing(${JSON.stringify(coincidentFixture)})`, context);
  const coincHtml = $('solarChoiceCards').innerHTML;
  assert(coincHtml.includes('Balanced &amp; Financial Winner'), 'Coincident configurations merge into one winner card');
  assert(!coincHtml.includes('data-choice="financial"'), 'Merged winner has no separate financial apply button');
  assert(coincHtml.includes('No configuration reaches the 70% household demand coverage target'), 'Unavailable budget choice renders informative message');

  // Test 20: Apply buttons lifecycle (cards & alternatives) and stale protection
  console.log('Running Test 20: Apply buttons lifecycle & stale protection...');
  apiHandler = async () => choicesFixture;
  scheduleCalls = [];
  syncSettingCalls = [];

  // Reset inputs to clean state
  $('solarNpvSacrifice').value = '5';
  $('solarCoverageTarget').value = '70';
  $('solarBatteryMin').value = '0';
  $('solarBatteryMax').value = '20';

  // Run sizing analysis to establish fresh, non-stale state with choicesFixture
  await vm.runInContext('runSolarSizingAnalysis()', context);

  assert.strictEqual($('solarResultsWrap').classList.contains('stale'), false, 'Results wrap is not stale');

  const cardApplyButtons = $('solarChoiceCards').querySelectorAll('.solar-choice-apply');
  assert.strictEqual(cardApplyButtons.length, 3, 'Found 3 choice card apply buttons');
  cardApplyButtons.forEach(btn => {
    assert.strictEqual(btn.disabled, false, `Choice button "${btn.textContent.trim()}" is enabled`);
  });

  const altApplyButtons = $('solarAlternativesTable').querySelectorAll('button[data-alt-idx]');
  assert.strictEqual(altApplyButtons.length, 3, 'Found 3 alternative table apply buttons');
  altApplyButtons.forEach(btn => {
    assert.strictEqual(btn.disabled, false, `Alternative button idx ${btn.getAttribute('data-alt-idx')} is enabled`);
  });

  // Click Financial choice apply button
  const finBtn = cardApplyButtons.find(b => b.getAttribute('data-choice') === 'financial');
  assert(finBtn, 'Financial apply button exists');
  scheduleCalls = [];
  finBtn.click();
  assert.strictEqual(scheduleCalls.length, 1, 'Schedule called on financial choice apply');
  assert.strictEqual(context.settings.battery_kwh, 0.0, 'Settings battery_kwh updated to financial choice (0 kWh)');
  assert.strictEqual(context.quote.price_basis, 'modeled', 'Quote price basis set to modeled');
  assert($('solarApplyStatus').textContent.includes('Best financial return'), 'Apply status mentions Best financial return');

  // Synchronize fingerprint to simulate fresh state and click Balanced choice apply button
  vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
  const balBtn = cardApplyButtons.find(b => b.getAttribute('data-choice') === 'balanced');
  assert(balBtn, 'Balanced apply button exists');
  scheduleCalls = [];
  balBtn.click();
  assert.strictEqual(scheduleCalls.length, 1, 'Schedule called on balanced choice apply');
  assert.strictEqual(context.settings.battery_kwh, 8.28, 'Settings battery_kwh updated to balanced choice (8.28 kWh)');
  assert($('solarApplyStatus').textContent.includes('Balanced choice'), 'Apply status mentions Balanced choice');

  // Synchronize fingerprint to simulate fresh state and click Budget choice apply button
  vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
  const budBtn = cardApplyButtons.find(b => b.getAttribute('data-choice') === 'budget');
  assert(budBtn, 'Budget apply button exists');
  scheduleCalls = [];
  budBtn.click();
  assert.strictEqual(scheduleCalls.length, 1, 'Schedule called on budget choice apply');
  assert.strictEqual(context.settings.battery_kwh, 5.52, 'Settings battery_kwh updated to budget choice (5.52 kWh)');
  assert($('solarApplyStatus').textContent.includes('Budget choice'), 'Apply status mentions Budget choice');

  // Synchronize fingerprint to simulate fresh state and click an alternative button
  vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
  const altBtn1 = altApplyButtons[1];
  scheduleCalls = [];
  altBtn1.click();
  assert.strictEqual(scheduleCalls.length, 1, 'Schedule called on alternative apply');
  assert.strictEqual(context.settings.battery_kwh, 8.28, 'Settings battery_kwh updated to alternative choice');

  // Invalidate with stale input change
  $('solarNpvSacrifice').value = '10';
  vm.runInContext('markSolarSizingStale()', context);

  assert.strictEqual($('solarResultsWrap').classList.contains('stale'), true, 'Results wrap marked stale');
  assert.strictEqual($('applySolarSystem').disabled, true, 'Main apply button disabled when stale');

  // Verify all card and alternative buttons are disabled when stale
  cardApplyButtons.forEach(btn => {
    assert.strictEqual(btn.disabled, true, `Choice button "${btn.textContent.trim()}" is disabled when stale`);
  });
  altApplyButtons.forEach(btn => {
    assert.strictEqual(btn.disabled, true, `Alternative button idx ${btn.getAttribute('data-alt-idx')} is disabled when stale`);
  });

  // Attempt to click disabled buttons while stale
  scheduleCalls = [];
  balBtn.click();
  altBtn1.click();
  assert.strictEqual(scheduleCalls.length, 0, 'No schedule calls when clicking disabled buttons while stale');

  // Attempt to call applySolarCandidate directly while stale
  vm.runInContext('applySolarCandidate(solarData.recommendation_choices.balanced)', context);
  assert($('solarApplyStatus').textContent.includes('Cannot apply: analysis is stale or running'), 'Guards against applying when stale');

  // Test 21: Non-NPV objective with negative NPVs returns financial fallback
  console.log('Running Test 21: Non-NPV objective fallback semantics...');
  const negAutarkyData = {
    status: 'SCENARIO_PENDING',
    recommended_candidate: {
      panel_count: 22,
      kwp: 9.68,
      battery_kwh: 20.0,
      capex_eur: 25000,
      npv_median: -15000.0,
      autarky_median: 0.95,
      roof_counts: { south: 22 }
    },
    recommendation_choices: {
      financial: {
        role: 'financial',
        role_label: 'Best financial return',
        panel_count: 10,
        kwp: 4.4,
        battery_kwh: 0.0,
        capex_eur: 5500,
        npv_median: -500.0,
        autarky_median: 0.45,
        roof_counts: { south: 10 }
      },
      balanced: null,
      budget: null,
      preferences: { allowed_sacrifice_pct: 5.0, coverage_target_pct: 70.0 },
      main_recommendation: 'balanced'
    }
  };

  const resolvedMain = vm.runInContext(`getMainRecommendation(${JSON.stringify(negAutarkyData)})`, context);
  assert(resolvedMain, 'Main recommendation resolved');
  assert.strictEqual(resolvedMain.panel_count, 10, 'Resolves to financial reference (10 panels), not autarky winner (22 panels)');
  assert.strictEqual(resolvedMain.npv_median, -500.0, 'Resolves to financial reference NPV (-500 €), not autarky winner (-15,000 €)');

  // Legacy data without recommendation_choices should still fall back to recommended_candidate
  const legacyData = {
    status: 'SCENARIO_PENDING',
    recommended_candidate: {
      panel_count: 22,
      kwp: 9.68,
      battery_kwh: 20.0,
      capex_eur: 25000,
      npv_median: -15000.0,
      autarky_median: 0.95
    }
  };
  const legacyResolved = vm.runInContext(`getMainRecommendation(${JSON.stringify(legacyData)})`, context);
  assert.strictEqual(legacyResolved.panel_count, 22, 'Legacy data without choices returns recommended_candidate');

  // Test 22: All three choice cards render in no-feasible scenario
  console.log('Running Test 22: No-feasible scenario choice cards rendering...');
  const noFeasibleData = {
    status: 'NO_FEASIBLE_CANDIDATE',
    recommended_candidate: null,
    recommendation_choices: {
      financial: null,
      balanced: null,
      budget: null,
      preferences: { allowed_sacrifice_pct: 5.0, coverage_target_pct: 70.0 },
      main_recommendation: 'balanced'
    },
    top_alternatives: [],
    search_summary: {
      evaluated_count: 0,
      exact: true
    }
  };

  vm.runInContext(`renderSolarSizing(${JSON.stringify(noFeasibleData)})`, context);

  const noFeasCardsHtml = $('solarChoiceCards').innerHTML;
  assert(noFeasCardsHtml.includes('Best financial return'), 'Best financial return card header rendered');
  assert(noFeasCardsHtml.includes('Balanced choice'), 'Balanced choice card header rendered');
  assert(noFeasCardsHtml.includes('Budget choice'), 'Budget choice card header rendered');

  // Verify all cards explain unfeasibility
  assert(noFeasCardsHtml.includes('No feasible configuration was found within the evaluated search constraints.'), 'Explains unfeasibility in card notices');

  // Stage 5 requirement: No apply button for unavailable choices
  const noFeasButtons = $('solarChoiceCards').querySelectorAll('.solar-choice-apply');
  assert.strictEqual(noFeasButtons.length, 0, 'No apply button rendered for unavailable choices');

  assert.strictEqual($('applySolarSystem').disabled, true, 'Main Apply button is disabled');

  // Test 23: Financial basis and retained profit persistence & options generation
  {
    console.log('Running Test 23: Financial basis and retained profit persistence & options generation...');
    $('solarFinancialBasis').value = 'after_financing';
    $('solarRetainedProfit').value = '96';

    vm.runInContext('saveSolarOptionsToStorage()', context);
    const stored = JSON.parse(mockLocalStorage.getItem('pv-lab-solar-sizing-v1'));
    assert.strictEqual(stored.financial_basis, 'after_financing', 'Stored financial basis is after_financing');
    assert.strictEqual(stored.retained_profit_pct, 96, 'Stored retained profit is 96');

    const opts1 = vm.runInContext('getSolarOptions()', context);
    assert.strictEqual(opts1.financial_basis, 'after_financing', 'getSolarOptions returns after_financing');
    assert.strictEqual(opts1.retained_profit_pct, 96, 'getSolarOptions returns 96');

    // Switch back to project
    $('solarFinancialBasis').value = 'project';
    vm.runInContext('saveSolarOptionsToStorage()', context);
    const opts2 = vm.runInContext('getSolarOptions()', context);
    assert.strictEqual(opts2.financial_basis, 'project', 'getSolarOptions returns project');
  }

  // Test 24 & 25: Four recommendation roles + current card rendering with dual Project NPV & Equity NPV, and Smaller role
  {
    console.log('Running Test 24: Four recommendation roles + current card rendering with dual Project NPV & Equity NPV...');
    const stage5ChoicesFixture = {
      status: 'SCENARIO_PENDING',
      compute_seconds: 1.10,
      settings: { panel_watts: 440, inverter_kw: 6.0, load_scenario: 'electrified', horizon_years: 20 },
      recommendation_choices: {
        financial: {
          role: 'financial', role_label: 'Best financial return',
          panel_count: 14, kwp: 6.16,
          roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
          battery_kwh: 0.0, battery_modules: 0, capex_eur: 7044,
          project_npv_median: 10000, equity_npv_median: 8500, npv_median: 10000,
          initial_equity_eur: 7044, loan_principal_eur: 0,
          autarky_median: 0.613, coverage_pct: 61.3, discounted_payback_years: 6.2,
          settings: { south_panels: 14, battery_kwh: 0.0 }
        },
        balanced: {
          role: 'balanced', role_label: 'Balanced choice',
          panel_count: 14, kwp: 6.16,
          roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
          battery_kwh: 8.28, battery_modules: 3, capex_eur: 11598,
          project_npv_median: 9630, equity_npv_median: 8100, npv_median: 9630,
          initial_equity_eur: 11598, loan_principal_eur: 0,
          retained_npv_pct: 96.3, coverage_gain_pct: 20.0,
          autarky_median: 0.813, coverage_pct: 81.3, discounted_payback_years: 7.8,
          settings: { south_panels: 14, battery_kwh: 8.28 }
        },
        budget: {
          role: 'budget', role_label: 'Budget choice',
          panel_count: 14, kwp: 6.16,
          roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
          battery_kwh: 5.52, battery_modules: 2, capex_eur: 10080,
          project_npv_median: 9920, equity_npv_median: 8400, npv_median: 9920,
          initial_equity_eur: 10080, loan_principal_eur: 0,
          capex_savings_vs_financial: 1518, coverage_target_pct: 70.0,
          autarky_median: 0.740, coverage_pct: 74.0, discounted_payback_years: 7.1,
          settings: { south_panels: 14, battery_kwh: 5.52 }
        },
        smaller: {
          role: 'smaller', role_label: 'Lower-cost alternative',
          panel_count: 10, kwp: 4.40,
          roof_counts: { south: 10, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
          battery_kwh: 0.0, battery_modules: 0, capex_eur: 5200,
          project_npv_median: 9550, equity_npv_median: 8050, npv_median: 9550,
          initial_equity_eur: 5200, loan_principal_eur: 0,
          retained_profit_pct: 95.5, retained_npv_pct: 95.5,
          npv_sacrificed_eur: 450, npv_sacrificed_pct: 4.5, capex_savings_eur: 1844,
          autarky_median: 0.550, coverage_pct: 55.0, discounted_payback_years: 5.8,
          settings: { south_panels: 10, battery_kwh: 0.0 }
        },
        preferences: { allowed_sacrifice_pct: 5.0, coverage_target_pct: 70.0, retained_profit_pct: 95.0 },
        main_recommendation: 'balanced'
      },
      top_alternatives: [],
      search_summary: { evaluated_count: 48, exact: true }
    };

    // Set current result in context
    context.current = {
      result: {
        layout: { panel_count: 18, kwp: 9.0 },
        finance: { capex_eur: 16500, initial_equity_eur: 16500, loan_amount_eur: 0 },
        metrics: {
          npv_eur: { median: 11000 },
          equity_npv_eur: { median: 9500 },
          autarky_ratio: { median: 0.78 },
          payback_years: { discounted: 8.5 }
        }
      }
    };

    $('solarFinancialBasis').value = 'project';
    vm.runInContext(`renderSolarSizing(${JSON.stringify(stage5ChoicesFixture)})`, context);

    const cardsHtml = $('solarChoiceCards').innerHTML;
    assert(cardsHtml.includes('Current configuration'), 'Renders Current configuration card');
    assert(cardsHtml.includes('Best financial return'), 'Renders Best financial return card');
    assert(cardsHtml.includes('Balanced choice'), 'Renders Balanced choice card');
    assert(cardsHtml.includes('Budget choice'), 'Renders Budget choice card');
    assert(cardsHtml.includes('Lower-cost alternative'), 'Renders Lower-cost alternative card');

    // Expose BOTH Project NPV and Equity NPV
    assert(cardsHtml.includes('Project NPV'), 'Exposes Project NPV metric in choice cards');
    assert(cardsHtml.includes('Equity NPV'), 'Exposes Equity NPV metric in choice cards');
    assert(cardsHtml.includes('Initial Equity'), 'Exposes Initial Equity in choice cards');
    assert(cardsHtml.includes('Loan Principal'), 'Exposes Loan Principal in choice cards');

    // In project basis, Project NPV has ranking pill
    assert(cardsHtml.includes('Project NPV <span class="metric-basis-pill">Ranking</span>'), 'Project NPV marked with Ranking pill under project basis');

    // Switch to after_financing basis and re-render
    $('solarFinancialBasis').value = 'after_financing';
    vm.runInContext(`renderSolarSizing(${JSON.stringify(stage5ChoicesFixture)})`, context);
    const cardsHtmlFin = $('solarChoiceCards').innerHTML;
    assert(cardsHtmlFin.includes('Equity NPV <span class="metric-basis-pill">Ranking</span>'), 'Equity NPV marked with Ranking pill under after_financing basis');

    // Test 25: Smaller / Lower-cost alternative card specifics
    console.log('Running Test 25: Smaller / Lower-cost alternative card specifics...');
    assert(cardsHtml.includes('95.5%'), 'Smaller role displays retained profit percentage (95.5%)');
    assert(cardsHtml.includes('€450'), 'Smaller role displays sacrificed NPV amount (€450)');
    assert(cardsHtml.includes('4.5%'), 'Smaller role displays sacrificed NPV percent (4.5%)');
    assert(cardsHtml.includes('€1,844'), 'Smaller role displays capex savings (€1,844)');
    assert(cardsHtml.includes('10 panels (4.40 kWp)'), 'Smaller role displays correct panel counts and kWp');

    const cardButtons = $('solarChoiceCards').querySelectorAll('.solar-choice-apply');
    const smallerBtn = cardButtons.find(b => b.getAttribute('data-choice') === 'smaller');
    assert(smallerBtn, 'Apply button for smaller alternative exists');
  }

  // Test 26: Applying no-install baseline preserves module size assumptions
  {
    console.log('Running Test 26: Applying no-install baseline preserves module size assumptions...');
    context.settings.south_panels = 18;
    context.settings.battery_kwh = 10.0;
    context.settings.battery_modules = 2;
    context.settings.battery_module_kwh = 5.0;
    context.settings.battery_secondary_module_kwh = 6.9;
    context.settings.battery_mixed_sizes = true;

    const noInstallCandidate = {
      is_no_install: true,
      panel_count: 0,
      kwp: 0,
      battery_kwh: 0,
      battery_modules: 0,
      battery_secondary_modules: 0,
      roof_counts: { south: 0, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      settings: {
        south_panels: 0, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
        battery_kwh: 0, battery_modules: 0
      }
    };

    // Clear stale state and synchronize fingerprint to allow applying
    $('solarResultsWrap').classList.remove('stale');
    vm.runInContext('solarInFlight = false;', context);
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext(`applySolarCandidate(${JSON.stringify(noInstallCandidate)})`, context);

    assert.strictEqual(context.settings.south_panels, 0, 'Panels zeroed on no-install apply');
    assert.strictEqual(context.settings.battery_kwh, 0.0, 'Battery kWh zeroed on no-install apply');
    assert.strictEqual(context.settings.battery_modules, 0, 'Battery modules zeroed on no-install apply');
    assert.strictEqual(context.settings.battery_module_kwh, 5.0, 'battery_module_kwh strictly preserved on no-install apply');
    assert.strictEqual(context.settings.battery_secondary_module_kwh, 6.9, 'battery_secondary_module_kwh strictly preserved on no-install apply');
    assert.strictEqual(context.settings.battery_mixed_sizes, true, 'battery_mixed_sizes strictly preserved on no-install apply');
  }

  // Test 27: Applying candidate preserves quote line_items reference
  {
    console.log('Running Test 27: Applying candidate preserves quote line_items reference...');
    const sampleItems = [{ description: 'Roof labor', net_eur: 1500 }];
    context.quote.line_items = sampleItems;

    const storageCandidate = {
      panel_count: 14,
      kwp: 6.16,
      roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      battery_kwh: 5.0,
      battery_modules: 1,
      settings: { south_panels: 14, battery_kwh: 5.0, battery_modules: 1 }
    };

    $('solarResultsWrap').classList.remove('stale');
    vm.runInContext('solarInFlight = false;', context);
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext(`applySolarCandidate(${JSON.stringify(storageCandidate)})`, context);

    assert.strictEqual(context.quote.line_items, sampleItems, 'quote.line_items array reference preserved on candidate apply');
  }

  // Test 28: Stale invalidation rules
  {
    console.log('Running Test 28: Stale invalidation rules...');
    vm.runInContext('solarSensitivityData = { scenarios: {} };', context);
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext('solarSensitivityAcceptedFingerprint = solarSensitivityStateFingerprint();', context);
    $('solarResultsWrap').classList.remove('stale');
    $('solarSensitivityResultsWrap').classList.remove('stale');
    $('solarSensitivityStatus').textContent = 'Sensitivity ready';

    // Stress control tweak invalidates only sensitivity
    vm.runInContext('markSolarSensitivityStale()', context);
    assert($('solarSensitivityStatus').textContent.includes('Assumptions or inputs changed') || $('solarSensitivityResultsWrap').classList.contains('stale'), 'Sensitivity status or wrap marked stale on stress tweak');
    assert.strictEqual($('solarResultsWrap').classList.contains('stale'), false, 'Sizing remains VALID on sensitivity stress tweak');

    // Standard input tweak invalidates both sizing and sensitivity
    vm.runInContext('markSolarSizingStale()', context);
    assert.strictEqual($('solarResultsWrap').classList.contains('stale'), true, 'Sizing marked stale on standard input change');
  }

  // Test 29: Economic Sensitivity runner and renderer
  {
    console.log('Running Test 29: Economic Sensitivity runner and renderer...');
    const mockSensData = {
      status: 'SUCCESS',
      financial_basis: 'project',
      scenarios: {
        base: {
          scenario_id: 'base',
          scenario_label: 'Base economic scenario',
          perturbation_description: 'Standard planning assumptions',
          status: 'SUCCESS',
          roles: {
            financial: {
              role_label: 'Best financial return',
              panel_count: 14, kwp: 6.16, battery_kwh: 0.0, capex_eur: 7044,
              project_npv_median: 10000, equity_npv_median: 8500, coverage_pct: 61.3
            },
            smaller: {
              role_label: 'Lower-cost alternative',
              panel_count: 10, kwp: 4.4, battery_kwh: 0, capex_eur: 5200, npv_median: 9550
            }
          },
          base_preferred_repriced: {
            panel_count: 14, kwp: 6.16, battery_kwh: 0, capex_eur: 7044, npv_median: 10000,
            changed_layout: false, regret_vs_scenario_financial_best_eur: 0
          },
          smaller_sacrifice: { sacrifice_eur: 450, sacrifice_pct: 4.5 },
          search_summary: { evaluated_count: 48, exact: true }
        },
        lower_export: {
          scenario_id: 'lower_export',
          scenario_label: 'Lower export price (-20%)',
          perturbation_description: 'Export price reduced 20%',
          status: 'SUCCESS',
          roles: {
            financial: {
              role_label: 'Best financial return',
              panel_count: 12, kwp: 5.28, battery_kwh: 5.0, capex_eur: 9000,
              project_npv_median: 8800, equity_npv_median: 7400, coverage_pct: 68.0
            }
          },
          base_preferred_repriced: {
            panel_count: 14, kwp: 6.16, battery_kwh: 0, capex_eur: 7044, npv_median: 8400,
            changed_layout: true, delta_panels: 2, delta_kwp: 0.88, delta_battery_kwh: -5.0,
            regret_vs_scenario_financial_best_eur: 400
          },
          smaller_sacrifice: { sacrifice_eur: 200, sacrifice_pct: 2.3 },
          search_summary: { evaluated_count: 48, exact: true }
        },
        higher_installed_cost: {
          scenario_id: 'higher_installed_cost',
          scenario_label: 'Higher installed cost (+20%)',
          perturbation_description: 'Turnkey capex increased 20%',
          status: 'SUCCESS',
          roles: {
            financial: {
              role_label: 'Best financial return',
              panel_count: 10, kwp: 4.4, battery_kwh: 0, capex_eur: 6240,
              project_npv_median: 7500, equity_npv_median: 6200, coverage_pct: 55.0
            }
          },
          base_preferred_repriced: {
            panel_count: 14, kwp: 6.16, battery_kwh: 0, capex_eur: 8450, npv_median: 7200,
            changed_layout: true, delta_panels: 4, delta_kwp: 1.76, delta_battery_kwh: 0,
            regret_vs_scenario_financial_best_eur: 300
          },
          smaller_sacrifice: null,
          search_summary: { evaluated_count: 48, exact: true }
        },
        lower_consumption: {
          scenario_id: 'lower_consumption',
          scenario_label: 'Lower baseline load (-10%)',
          perturbation_description: 'Measured household load reduced 10%',
          status: 'SUCCESS',
          roles: {
            financial: {
              role_label: 'Best financial return',
              panel_count: 14, kwp: 6.16, battery_kwh: 0, capex_eur: 7044,
              project_npv_median: 9400,
              project_npv: { median: 9400, min: 7500, max: 11000 },
              project_npv_min: 7500, project_npv_max: 11000,
              equity_npv_median: 8000,
              equity_npv: { median: 8000, min: 6200, max: 9500 },
              equity_npv_min: 6200, equity_npv_max: 9500,
              coverage_pct: 75.5
            }
          },
          base_preferred_repriced: {
            panel_count: 14, kwp: 6.16, battery_kwh: 0, capex_eur: 7044, npv_median: 9400,
            npv_min: 7500, npv_max: 11000,
            changed_layout: false, regret_vs_scenario_financial_best_eur: 0
          },
          smaller_sacrifice: null,
          search_summary: { evaluated_count: 48, valid_candidates_count: 48, exact: true }
        },
        higher_apr: {
          scenario_id: 'higher_apr',
          scenario_label: 'Higher borrowing APR (+2.0 pp)',
          perturbation_description: 'Loan APR increased by 2.0 percentage points',
          status: 'UNAVAILABLE',
          unavailable_reason: 'Financing APR exceeds maximum allowable borrowing rate (0.25).'
        }
      },
      comparisons: [
        {
          scenario_id: 'base', label: 'Base economic scenario', status: 'SUCCESS',
          financial_panel_count: 14, financial_kwp: 6.16, financial_battery_kwh: 0.0,
          financial_capex_eur: 7044, financial_project_npv_median: 10000, financial_equity_npv_median: 8500,
          base_preferred_repriced_npv: 10000, regret_eur: 0, changed_layout: false
        },
        {
          scenario_id: 'lower_export', label: 'Lower export price (-20%)', status: 'SUCCESS',
          financial_panel_count: 12, financial_kwp: 5.28, financial_battery_kwh: 5.0,
          financial_capex_eur: 9000, financial_project_npv_median: 8800, financial_equity_npv_median: 7400,
          base_preferred_repriced_npv: 8400, regret_eur: 400, changed_layout: true, delta_panels: 2, delta_battery_kwh: -5.0
        },
        {
          scenario_id: 'higher_installed_cost', label: 'Higher installed cost (+20%)', status: 'SUCCESS',
          financial_panel_count: 10, financial_kwp: 4.4, financial_battery_kwh: 0.0,
          financial_capex_eur: 6240, financial_project_npv_median: 7500, financial_equity_npv_median: 6200,
          base_preferred_repriced_npv: 7200, regret_eur: 300, changed_layout: true, delta_panels: 4, delta_battery_kwh: 0.0
        },
        {
          scenario_id: 'lower_consumption', label: 'Lower baseline load (-10%)', status: 'SUCCESS',
          financial_panel_count: 14, financial_kwp: 6.16, financial_battery_kwh: 0.0,
          financial_capex_eur: 7044, financial_project_npv_median: 9400, financial_equity_npv_median: 8000,
          base_preferred_repriced_npv: 9400, regret_eur: 0, changed_layout: false
        },
        {
          scenario_id: 'higher_apr', label: 'Higher borrowing APR (+2.0 pp)', status: 'UNAVAILABLE',
          unavailable_reason: 'Financing APR exceeds maximum allowable borrowing rate (0.25).'
        }
      ]
    };

    vm.runInContext(`renderSolarSensitivityResults(${JSON.stringify(mockSensData)})`, context);

    const sensGridHtml = $('solarSensitivityScenariosGrid').innerHTML;
    assert(sensGridHtml.includes('Economic Robustness Verdict'), 'Renders economic robustness verdict');
    assert(sensGridHtml.includes('Base configuration'), 'Verdict describes base configuration robustness');
    assert(!sensGridHtml.includes('NaN'), 'No NaN in sensitivity scenarios grid HTML');

    const sensTableHtml = $('solarSensitivityComparisonTable').innerHTML;
    assert(sensTableHtml.includes('design-unified-table'), 'Renders comparison table');
    assert(sensTableHtml.includes('Base economic scenario'), 'Table includes base scenario row');
    assert(sensTableHtml.includes('Lower export price (-20%)'), 'Table includes scenario rows');
    assert(sensTableHtml.includes('Higher installed cost (+20%)'), 'Table includes higher installed cost row');
    assert(sensTableHtml.includes('Lower baseline load (-10%)'), 'Table includes lower baseline load row');
    assert(sensTableHtml.includes('Higher borrowing APR (+2.0 pp)'), 'Table includes unavailable scenario row');
    assert(sensTableHtml.includes('75.5%'), 'Coverage formatted as 75.5%');
    assert(!sensTableHtml.includes('7,550') && !sensTableHtml.includes('7550'), 'No 7550% coverage multiplication bug');
    assert(sensTableHtml.includes('Weather:'), 'Weather range rendered');
    assert(sensTableHtml.includes('Financing APR exceeds maximum allowable'), 'Renders unavailable reason cleanly');
    assert(!sensTableHtml.includes('NaN'), 'No NaN in sensitivity table HTML');
    assert(!sensTableHtml.includes('P50'), 'No P50 or probability confidence claims in comparison output');
  }

  // Test 30: Sensitivity API payload, roof map, ordinary edit stale invalidation, and in-flight cancellation
  {
    console.log('Running Test 30: Sensitivity API payload, roof map, ordinary edit stale invalidation, and in-flight cancellation...');

    context.optimizerPrefs.panel_maxima = { south: 22, west: 11, east: 6, dormer: 4, flat: 8, secondary_south: 5 };
    const testSensData = {
      status: 'SUCCESS',
      financial_basis: 'project',
      scenarios: {
        base: { status: 'SUCCESS', roles: { financial: { panel_count: 14, kwp: 6.16, capex_eur: 7044, project_npv_median: 10000, equity_npv_median: 8500 } } }
      },
      comparisons: []
    };

    // 1. Verify sensitivity request passes complete roof maxima map
    apiCalls = [];
    apiHandler = async (url, body) => {
      apiCalls.push({ url, body });
      return testSensData;
    };

    $('sensExportDelta').value = '15';
    $('sensCostDelta').value = '25';
    $('sensConsDelta').value = '12';
    $('sensAprDelta').value = '3.5';

    await vm.runInContext('runSolarSensitivityAnalysis()', context);

    assert.strictEqual(apiCalls.length, 1, 'One API call made for sensitivity analysis');
    assert.strictEqual(apiCalls[0].url, '/api/solar-sizing-sensitivity', 'Calls /api/solar-sizing-sensitivity');
    const sentOpts = apiCalls[0].body.options;
    assert(sentOpts && sentOpts.panel_maxima, 'Options include panel_maxima roof map');
    assert.strictEqual(sentOpts.panel_maxima.south, 22, 'panel_maxima includes south limit (22)');
    assert.strictEqual(sentOpts.panel_maxima.west, 11, 'panel_maxima includes west limit (11)');
    assert.strictEqual(sentOpts.panel_maxima.east, 6, 'panel_maxima includes east limit (6)');

    const sentSensOpts = apiCalls[0].body.sensitivity_options;
    assert.strictEqual(sentSensOpts.export_revenue_delta_pct, 15, 'Sends editable export delta');
    assert.strictEqual(sentSensOpts.installed_cost_delta_pct, 25, 'Sends editable cost delta');
    assert.strictEqual(sentSensOpts.household_consumption_delta_pct, 12, 'Sends editable consumption delta');
    assert.strictEqual(sentSensOpts.financing_apr_delta_pp, 3.5, 'Sends editable APR delta');

    // 2. Ordinary edit invalidates both sizing and sensitivity through markSolarSizingStale (Defect 3)
    $('solarResultsWrap').classList.remove('stale');
    $('solarSensitivityResultsWrap').classList.remove('stale');
    context.settings.loan_apr = 0.06;
    vm.runInContext('markSolarSizingStale()', context);
    // Verified: markSolarSizingStale calls markSolarSensitivityStale without manual invocation
    assert.strictEqual($('solarResultsWrap').classList.contains('stale'), true, 'Sizing invalidated on loan change');
    assert.strictEqual($('solarSensitivityResultsWrap').classList.contains('stale'), true, 'Sensitivity invalidated through markSolarSizingStale');
    assert($('solarSensitivityStatus').textContent.includes('Assumptions or inputs changed'), 'Sensitivity status updated on input change');

    // 3. In-flight cancellation protection for sensitivity
    apiCalls = [];
    let resolveFirst;
    const slowPromise = new Promise(resolve => { resolveFirst = resolve; });
    apiHandler = async (url, body) => {
      apiCalls.push({ url, body });
      return slowPromise;
    };

    const firstRunPromise = vm.runInContext('runSolarSensitivityAnalysis()', context);
    assert.strictEqual(vm.runInContext('solarSensitivityInFlight', context), true, 'Sensitivity is in-flight');

    // Trigger second request or mark stale while in flight
    vm.runInContext('markSolarSensitivityStale()', context);
    assert.strictEqual(vm.runInContext('solarSensitivityInFlight', context), false, 'In-flight reset on stale invalidation');

    // Resolve first request - should be discarded because token changed
    resolveFirst(testSensData);
    await firstRunPromise;
    assert.strictEqual(vm.runInContext('solarSensitivityAcceptedFingerprint', context), null, 'Discarded in-flight response does not set accepted fingerprint');
  }

  // Test 31: Canonical CURRENT solar financial values on card and unified comparison table (Defect 1)
  {
    console.log('Running Test 31: Canonical CURRENT solar financial values on card and unified comparison table...');
    const currentBackup = context.current;
    const settingsBackup = { ...context.settings };
    const finBasisEl = $('solarFinancialBasis');
    if (finBasisEl) finBasisEl.value = 'project';

    context.current = {
      result: {
        layout: { kwp: 9.0, panel_count: 18 },
        settings: { battery_kwh: 10.0, battery_modules: 2, battery_module_kwh: 5.0, loan_mode: 'amount', loan_amount: 20060.0 },
        finance: {
          capex_eur: 20500.0,
          solar_loan_principal_eur: 20060.0,
          initial_solar_equity_eur: 440.0,
          pv_incremental: { npv: { median: 6433.48, min: 4000.0, max: 8000.0 } },
          solar_equity: { npv: { median: 6617.45, min: 4200.0, max: 8200.0 } }
        },
        metrics: {
          equity_npv_eur: { median: 10055.23 }, // Whole-package equity NPV
          npv_eur: { median: 6433.48 },
          discounted_payback_years: 9.2,
          financed_discounted_payback_years: 8.8,
          autarky: 0.65,
          self_consumption: 0.75
        }
      }
    };

    const sizingData = {
      options: { financial_basis: 'project' },
      recommendation_choices: {
        financial: {
          panel_count: 18, kwp: 9.0, battery_kwh: 5.0, capex_eur: 11995,
          npv_median: 10682, npv_min: 8000, npv_max: 13000, autarky_median: 0.68, self_consumption_median: 0.72,
          roles: ['Best financial return']
        }
      },
      role_status: {
        budget: { unavailable: true, reason: 'Coverage target not met' }
      }
    };

    // 1. Under project basis
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(sizingData)})`, context);
    const choicesHtml = $('solarChoiceCards').innerHTML;
    assert(/Initial [Ee]quity/.test(choicesHtml), 'Current card has Initial equity');
    assert(choicesHtml.includes('€440'), 'Current card shows initial solar equity €440 (not €20,500)');
    assert(/Loan [Pp]rincipal/.test(choicesHtml), 'Current card has Loan principal');
    assert(choicesHtml.includes('€20,060'), 'Current card shows solar loan principal €20,060 (not €0)');
    assert(choicesHtml.includes('€6,433'), 'Current card shows project NPV €6,433');
    assert(!choicesHtml.includes('10,055'), 'Current card does NOT display whole-package equity NPV 10,055');

    // 2. Under after_financing basis
    if (finBasisEl) finBasisEl.value = 'after_financing';
    sizingData.options.financial_basis = 'after_financing';
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(sizingData)})`, context);
    const choicesEquityHtml = $('solarChoiceCards').innerHTML;
    assert(choicesEquityHtml.includes('€6,617'), 'Current card shows solar equity NPV €6,617 under after_financing basis');
    assert(!choicesEquityHtml.includes('10,055'), 'Current card does NOT display whole-package equity NPV 10,055 in after_financing mode');

    // 3. Unified comparison table
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(sizingData)})`, context);
    const unifiedHtml = $('designUnifiedComparisonTable').innerHTML;
    assert(unifiedHtml.includes('€440'), 'Unified table row 0 shows initial solar equity €440');
    assert(unifiedHtml.includes('€20,060'), 'Unified table row 0 shows solar loan principal €20,060');
    assert(unifiedHtml.includes('€20,500'), 'Unified table row 0 shows capex €20,500');
    assert(unifiedHtml.includes('Active'), 'Unified table row 0 shows Active state');
    assert(unifiedHtml.includes('Unavailable'), 'Unified table unavailable role shows Unavailable');
    assert(!unifiedHtml.includes('Active</span></td>\n            <td><button') && !unifiedHtml.includes('data-apply-config-idx="3"'), 'Unavailable role does NOT have Apply button');

    // 4. Missing financial data stays unavailable '—'
    context.current.result.finance.initial_solar_equity_eur = null;
    context.current.result.finance.solar_loan_principal_eur = null;
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(sizingData)})`, context);
    const missingHtml = $('designUnifiedComparisonTable').innerHTML;
    assert(missingHtml.includes('—'), 'Missing solar loan / equity displays dash —');

    // Restore
    context.current = currentBackup;
    context.settings = settingsBackup;
    if (finBasisEl) finBasisEl.value = 'project';
  }

  // Test 32: Apply/evaluate parity with module overrides and quote variants (Defect 2)
  {
    console.log('Running Test 32: Apply/evaluate parity with module overrides and quote variants...');
    const settingsBackup = { ...context.settings };
    const quoteBackup = { ...context.quote };

    // Setup user settings with overrides and quote variants
    context.settings.battery_mixed_sizes = true;
    context.settings.battery_module_kwh = 5.0;
    context.settings.battery_secondary_module_kwh = 6.9;
    context.settings.battery_kwh = 11.9;
    context.settings.battery_module_cost_override = 3000.0;
    context.settings.battery_secondary_module_cost_override = 4000.0;
    context.settings.load_scenario = 'baseline';
    context.settings.inverter_kw = 6.0;

    context.quote = {
      quote_10kwh_eur: 5000.0,
      quote_15kwh_eur: 7000.0,
      line_items: [{ description: 'Existing line item', net_eur: 1000 }]
    };

    const sizingOptions = {
      load_scenario: 'electrified',
      panel_maxima: { south: 8, west: 4, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      battery_min_kwh: 11.9,
      battery_max_kwh: 11.9
    };
    const sizingRes = await pythonSolarSizing(context.settings, sizingOptions, context.quote);
    const cand = sizingRes.recommendation_choices.financial;
    $('solarResultsWrap').classList.remove('stale');
    vm.runInContext('solarInFlight = false;', context);
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext(`applySolarCandidate(${JSON.stringify(cand)}, 'Best financial return')`, context);

    // Assert settings overrides are cleared to match candidate settings
    assert.strictEqual(context.settings.battery_module_cost_override, null, 'Settings primary override cleared on apply');
    assert.strictEqual(context.settings.battery_secondary_module_cost_override, null, 'Settings secondary override cleared on apply');

    // Assert quote original overrides are preserved as recoverable references
    assert.strictEqual(context.quote.battery_module_cost_override, 3000.0, 'Quote primary override preserved');
    assert.strictEqual(context.quote.battery_secondary_module_cost_override, 4000.0, 'Quote secondary override preserved');
    assert.strictEqual(context.quote.line_items.length, 1, 'Quote line items preserved');

    // Assert Python evaluate with applied settings respects effective pricing
    const evalRes = await pythonEvaluate({ settings: context.settings, quote: context.quote });
    assert(Math.abs(evalRes.capex_eur - cand.capex_eur) < 1.0, `Applied capex (${evalRes.capex_eur}) matches candidate (${cand.capex_eur})`);

    // Test no-install apply preserves module specifications
    const noInstallCand = {
      role_label: 'No installation',
      panel_count: 0, kwp: 0, battery_kwh: 0, battery_modules: 0, battery_secondary_modules: 0,
      roof_counts: { south: 0, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      settings: {
        south_panels: 0, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
        battery_kwh: 0, battery_modules: 0, battery_secondary_modules: 0,
        battery_mixed_sizes: true, battery_module_kwh: 5.0, battery_secondary_module_kwh: 6.9
      }
    };
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext(`applySolarCandidate(${JSON.stringify(noInstallCand)}, 'No installation')`, context);
    assert.strictEqual(context.settings.south_panels, 0, 'No-install zeroes south panels');
    assert.strictEqual(context.settings.battery_kwh, 0, 'No-install zeroes battery kWh');
    assert.strictEqual(context.settings.battery_module_kwh, 5.0, 'No-install preserves battery_module_kwh');
    assert.strictEqual(context.settings.battery_secondary_module_kwh, 6.9, 'No-install preserves battery_secondary_module_kwh');
    assert.strictEqual(context.settings.battery_mixed_sizes, true, 'No-install preserves battery_mixed_sizes');

    // Restore
    context.settings = settingsBackup;
    context.quote = quoteBackup;
  }

  // Test 33: Visible percentage validation for retained profit and sacrifices (Defect 5)
  {
    console.log('Running Test 33: Visible percentage validation for retained profit and sacrifices...');
    const profitInput = $('solarRetainedProfit');
    assert(profitInput, 'solarRetainedProfit input exists');

    // Empty input should NOT silently become 95
    profitInput.value = '';
    const parsedEmpty = vm.runInContext(`parsePercentInput('solarRetainedProfit', 95)`, context);
    assert(Number.isNaN(parsedEmpty), 'parsePercentInput returns NaN for empty input');

    // getSolarOptions should throw Error on invalid profit input
    let threw = false;
    try {
      vm.runInContext('getSolarOptions()', context);
    } catch (e) {
      threw = true;
      assert(e.message.includes('Profit retained') || e.message.includes('valid percentage'), 'Error message specifies profit percentage');
    }
    assert(threw, 'getSolarOptions threw on empty solarRetainedProfit');

    // Input receives aria-invalid and is-invalid
    profitInput.value = 'abc';
    profitInput.dispatchEvent('input');
    assert.strictEqual(profitInput.getAttribute('aria-invalid'), 'true', 'aria-invalid is true for invalid input');
    assert(profitInput.classList.contains('is-invalid'), 'is-invalid class added for invalid input');

    // Valid input clears invalid state
    profitInput.value = '95';
    profitInput.dispatchEvent('input');
    assert.strictEqual(profitInput.getAttribute('aria-invalid'), null, 'aria-invalid is removed for valid input');
    assert(!profitInput.classList.contains('is-invalid'), 'is-invalid class removed for valid input');
  }

  // Test 34: Discounted payback explicit Not reached / unavailable and zero simple payback substitution (Defect 2)
  {
    console.log('Running Test 34: Discounted payback explicit Not reached / unavailable and zero simple payback substitution...');
    const currentBackup = context.current;
    const settingsBackup = { ...context.settings };
    const finBasisEl = $('solarFinancialBasis');
    if (finBasisEl) finBasisEl.value = 'project';

    const testSizingData = {
      options: { financial_basis: 'project' },
      recommendation_choices: {
        financial: {
          panel_count: 14, kwp: 6.16, battery_kwh: 5.0, capex_eur: 9000,
          npv_median: 7500, npv_min: 5000, npv_max: 10000, autarky_median: 0.65, self_consumption_median: 0.70,
          discounted_payback_years: null, // candidate discounted payback did not recover
          roles: ['Best financial return']
        }
      },
      role_status: {}
    };

    // 1. Project basis: discounted median is null, simple payback median is 7.0
    context.current = {
      result: {
        layout: { kwp: 9.0, panel_count: 18 },
        settings: { battery_kwh: 10.0, battery_modules: 2, battery_module_kwh: 5.0 },
        finance: {
          capex_eur: 20500.0,
          solar_loan_principal_eur: 0,
          initial_solar_equity_eur: 20500.0,
          pv_incremental: {
            npv: { median: 6433.48 },
            discounted_payback: { median: null, recovered_weather_years: 0 },
            simple_payback: { median: 7.0, recovered_weather_years: 19 }
          },
          solar_equity: {
            npv: { median: 6617.45 },
            discounted_payback: { median: null, recovered_weather_years: 0 },
            simple_payback: { median: 7.0, recovered_weather_years: 19 }
          }
        },
        metrics: {
          autarky: { median: 0.65 },
          self_consumption: { median: 0.75 }
        }
      }
    };

    // Card rendering under project basis
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(testSizingData)})`, context);
    const cardHtmlProject = $('solarChoiceCards').innerHTML;
    assert(cardHtmlProject.includes('Not reached'), 'Current card displays Not reached when discounted payback median is null');
    assert(!cardHtmlProject.includes('7.0 y') && !cardHtmlProject.includes('7.0 <small>years</small>') && !cardHtmlProject.includes('7.0<'), 'Current card does NOT substitute simple payback 7.0 under project basis');

    // Table rendering under project basis
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(testSizingData)})`, context);
    const tableHtmlProject = $('designUnifiedComparisonTable').innerHTML;
    assert(tableHtmlProject.includes('Not reached'), 'Unified table row 0 displays Not reached when discounted payback median is null');
    assert(!tableHtmlProject.includes('7.0 y'), 'Unified table row 0 does NOT substitute simple payback 7.0 under project basis');

    // 2. Equity basis (after_financing): discounted median is null, simple payback median is 7.0
    if (finBasisEl) finBasisEl.value = 'after_financing';
    testSizingData.options.financial_basis = 'after_financing';
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(testSizingData)})`, context);
    const cardHtmlEquity = $('solarChoiceCards').innerHTML;
    assert(cardHtmlEquity.includes('Not reached'), 'Current card displays Not reached under after_financing basis');
    assert(!cardHtmlEquity.includes('7.0 y'), 'Current card does NOT substitute simple payback 7.0 under after_financing basis');

    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(testSizingData)})`, context);
    const tableHtmlEquity = $('designUnifiedComparisonTable').innerHTML;
    assert(tableHtmlEquity.includes('Not reached'), 'Unified table row 0 displays Not reached under after_financing basis');
    assert(!tableHtmlEquity.includes('7.0 y'), 'Unified table row 0 does NOT substitute simple payback 7.0 under after_financing basis');

    // 3. Zero numeric median: finite 0 is a legitimate engine persistent payback (0.0 y)
    context.current.result.finance.pv_incremental.discounted_payback.median = 0;
    context.current.result.finance.solar_equity.discounted_payback.median = 0;
    if (finBasisEl) finBasisEl.value = 'project';
    testSizingData.options.financial_basis = 'project';
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(testSizingData)})`, context);
    assert($('solarChoiceCards').innerHTML.includes('0.0 y'), 'Zero discounted payback renders 0.0 y on card under project basis');
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(testSizingData)})`, context);
    assert($('designUnifiedComparisonTable').innerHTML.includes('0.0 y'), 'Zero discounted payback renders 0.0 y in table under project basis');

    if (finBasisEl) finBasisEl.value = 'after_financing';
    testSizingData.options.financial_basis = 'after_financing';
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(testSizingData)})`, context);
    assert($('solarChoiceCards').innerHTML.includes('0.0 y'), 'Zero discounted payback renders 0.0 y on card under after_financing basis');
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(testSizingData)})`, context);
    assert($('designUnifiedComparisonTable').innerHTML.includes('0.0 y'), 'Zero discounted payback renders 0.0 y in table under after_financing basis');

    // Candidate with discounted_payback_years: 0
    testSizingData.recommendation_choices.financial.discounted_payback_years = 0;
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(testSizingData)})`, context);
    assert($('solarChoiceCards').innerHTML.includes('0.0 y'), 'Zero candidate discounted payback renders 0.0 y on choice card');
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(testSizingData)})`, context);
    assert($('designUnifiedComparisonTable').innerHTML.includes('0.0 y'), 'Zero candidate discounted payback renders 0.0 y in comparison table');

    // Also test solarRecCards with rec.discounted_payback_years = 0
    const recFixture = {
      ...testSizingData.recommendation_choices.financial,
      discounted_payback_years: 0,
      simple_payback_years: 5.2
    };
    const sizingDataWithRec = {
      ...testSizingData,
      recommended_candidate: recFixture
    };
    vm.runInContext(`renderSolarSizing(${JSON.stringify(sizingDataWithRec)})`, context);
    assert($('solarRecCards').innerHTML.includes('0.0 <small>years</small>'), 'Zero candidate discounted payback renders 0.0 <small>years</small> on solarRecCards');

    // 4. Missing financial data renders unavailable '—'
    context.current.result.finance = null;
    testSizingData.recommendation_choices.financial.discounted_payback_years = undefined;
    delete testSizingData.recommendation_choices.financial.discounted_payback_years;
    if (finBasisEl) finBasisEl.value = 'project';
    testSizingData.options.financial_basis = 'project';
    vm.runInContext(`renderSolarRecommendationChoices(${JSON.stringify(testSizingData)})`, context);
    assert($('solarChoiceCards').innerHTML.includes('—'), 'Missing finance renders — on card');
    vm.runInContext(`renderDesignUnifiedComparisonTable(${JSON.stringify(testSizingData)})`, context);
    assert($('designUnifiedComparisonTable').innerHTML.includes('—'), 'Missing finance renders — in table');

    // Restore
    context.current = currentBackup;
    context.settings = settingsBackup;
    if (finBasisEl) finBasisEl.value = 'project';
  }

  // Test 35: Preserve genuine battery pricing assumptions on zero-storage and existing-storage apply (Defect 1)
  {
    console.log('Running Test 35: Preserve genuine battery pricing assumptions on zero-storage and existing-storage apply...');
    const settingsBackup = { ...context.settings };
    const quoteBackup = { ...context.quote };

    // Setup starting state with genuine 550 €/kWh battery cost, module overrides and extra costs
    context.settings.battery_cost_kwh = 550.0;
    context.settings.battery_module_cost_override = 3000.0;
    context.settings.battery_secondary_module_cost_override = 4000.0;
    context.settings.battery_module_extra_cost = 150.0;
    context.settings.battery_secondary_module_extra_cost = 200.0;
    context.settings.storage_cost_mode = 'purchased';
    context.settings.battery_kwh = 10.0;
    context.settings.battery_modules = 2;
    context.settings.battery_module_kwh = 5.0;
    const originalLineItems = [{ description: 'Roof inspection', net_eur: 800 }];
    context.quote = {
      line_items: originalLineItems
    };

    const southOnly = { south: 10, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 };

    // 1. Obtain and apply a zero-storage candidate whose candidate settings have battery_cost_kwh: 0.0
    const zeroStorageRes = await pythonSolarSizing(context.settings, {
      load_scenario: 'electrified',
      battery_mode: 'joint',
      battery_min_kwh: 0.0,
      battery_max_kwh: 0.0,
      panel_maxima: southOnly
    }, context.quote);
    const zeroStorageCand = zeroStorageRes.recommendation_choices.financial;
    assert.strictEqual(zeroStorageCand.battery_kwh, 0.0, 'Candidate has 0 kWh storage');
    assert.strictEqual(zeroStorageCand.settings.battery_cost_kwh, 0.0, 'Candidate settings has battery_cost_kwh: 0.0');

    $('solarResultsWrap').classList.remove('stale');
    vm.runInContext('solarInFlight = false;', context);
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext(`applySolarCandidate(${JSON.stringify(zeroStorageCand)}, 'Zero-storage')`, context);

    // Verify settings preserve genuine battery €/kWh and overrides
    assert.strictEqual(context.settings.battery_cost_kwh, 550.0, 'Settings battery_cost_kwh preserved at 550 on zero-storage apply');
    assert.strictEqual(context.settings.battery_module_cost_override, 3000.0, 'Settings battery_module_cost_override preserved on zero-storage apply');
    assert.strictEqual(context.settings.battery_secondary_module_cost_override, 4000.0, 'Settings battery_secondary_module_cost_override preserved on zero-storage apply');
    assert.strictEqual(context.settings.battery_module_extra_cost, 150.0, 'Settings battery_module_extra_cost preserved on zero-storage apply');
    assert.strictEqual(context.settings.battery_secondary_module_extra_cost, 200.0, 'Settings battery_secondary_module_extra_cost preserved on zero-storage apply');
    assert.strictEqual(context.settings.battery_kwh, 0.0, 'Settings battery_kwh zeroed');
    assert.strictEqual(context.settings.battery_modules, 0, 'Settings battery_modules zeroed');

    // Verify quote preserves genuine references and line_items array identity
    assert.strictEqual(context.quote.battery_cost_kwh, 550.0, 'Quote battery_cost_kwh preserved as reference');
    assert.strictEqual(context.quote.battery_module_cost_override, 3000.0, 'Quote battery_module_cost_override preserved');
    assert.strictEqual(context.quote.line_items, originalLineItems, 'Quote line_items array identity preserved');
    assert.strictEqual(context.quote.line_items.length, 1, 'Quote line_items length preserved');

    const newSizingOpts = {
      load_scenario: 'electrified',
      battery_mode: 'joint',
      battery_min_kwh: 5.0,
      battery_max_kwh: 5.0,
      panel_maxima: southOnly
    };
    // Prelaunch evalZero and newSizingRes together (both read the applied settings)
    const evalZeroPromise = prefetch(pythonEvaluate({ settings: context.settings, quote: context.quote }));
    const newSizingPromise = prefetch(pythonSolarSizing(context.settings, newSizingOpts, context.quote));

    // Verify evaluate parity: installed capex is zero for battery through capacity
    const evalZero = await evalZeroPromise;
    assert(Math.abs(evalZero.capex_eur - zeroStorageCand.capex_eur) < 1.0, `Installed capex (${evalZero.capex_eur}) matches zero-storage candidate (${zeroStorageCand.capex_eur})`);

    // Verify new sizing request: zero-storage -> positive storage (e.g. 5 kWh) prices storage, does NOT price free!
    const newSizingRes = await newSizingPromise;
    const newFinCand = newSizingRes.recommendation_choices.financial;
    assert.strictEqual(newFinCand.battery_kwh, 5.0, 'New sizing evaluated 5 kWh storage');
    assert(newFinCand.capex_eur > evalZero.capex_eur + 2000, `Positive storage capex (${newFinCand.capex_eur}) reflects battery cost above PV capex (${evalZero.capex_eur}); storage is NOT free`);

    // 2. Fixed existing storage -> purchased
    // Sizing for fixed existing storage produces candidate with storage_cost_mode: 'existing' and settings.battery_cost_kwh: 0.0
    context.settings.battery_kwh = 10.0;
    context.settings.battery_modules = 2;
    context.settings.battery_module_kwh = 5.0;
    const existingStorageRes = await pythonSolarSizing(context.settings, {
      load_scenario: 'electrified',
      battery_mode: 'fixed',
      storage_cost_mode: 'existing',
      panel_maxima: southOnly
    }, context.quote);
    const existingStorageCand = existingStorageRes.recommendation_choices.financial;
    assert.strictEqual(existingStorageCand.settings.storage_cost_mode, 'existing', 'Candidate settings has storage_cost_mode: existing');
    assert.strictEqual(existingStorageCand.settings.battery_cost_kwh, 0.0, 'Candidate settings has battery_cost_kwh: 0.0');

    $('solarResultsWrap').classList.remove('stale');
    vm.runInContext('solarInFlight = false;', context);
    vm.runInContext('solarAcceptedFingerprint = solarStateFingerprint();', context);
    vm.runInContext(`applySolarCandidate(${JSON.stringify(existingStorageCand)}, 'Existing storage')`, context);

    // Verify settings preserve genuine assumptions while storage_cost_mode is existing
    assert.strictEqual(context.settings.storage_cost_mode, 'existing', 'Settings storage_cost_mode set to existing');
    assert.strictEqual(context.settings.battery_cost_kwh, 550.0, 'Settings battery_cost_kwh preserved at 550 on existing-storage apply');
    assert.strictEqual(context.settings.battery_kwh, existingStorageCand.battery_kwh, 'Settings battery_kwh matches candidate capacity');
    assert.strictEqual(context.settings.battery_modules, existingStorageCand.battery_modules, 'Settings battery_modules matches candidate modules');

    // Prelaunch evalExisting and purchasedSizingRes together
    const purchasedSizingOpts = {
      load_scenario: 'electrified',
      battery_mode: 'joint', // Joint mode searches purchased storage
      battery_min_kwh: 5.0,
      battery_max_kwh: 5.0,
      panel_maxima: southOnly
    };
    const evalExistingPromise = prefetch(pythonEvaluate({ settings: context.settings, quote: context.quote }));
    const purchasedSizingPromise = prefetch(pythonSolarSizing(context.settings, purchasedSizingOpts, context.quote));

    // Verify evaluate parity: battery capex is 0 through storage_cost_mode='existing'
    const evalExisting = await evalExistingPromise;
    assert(Math.abs(evalExisting.capex_eur - existingStorageCand.capex_eur) < 1.0, `Installed capex (${evalExisting.capex_eur}) matches existing storage candidate (${existingStorageCand.capex_eur})`);

    // Verify transition from existing -> purchased in new sizing request
    const purchasedSizingRes = await purchasedSizingPromise;
    const purchasedFinCand = purchasedSizingRes.recommendation_choices.financial;
    assert.strictEqual(purchasedFinCand.battery_kwh, 5.0, 'Purchased sizing evaluated 5 kWh storage');
    assert(purchasedFinCand.capex_eur > evalExisting.capex_eur + 2000, `Purchased storage capex (${purchasedFinCand.capex_eur}) reflects battery cost above existing capex (${evalExisting.capex_eur}); storage is NOT free`);

    // 3. User starting explicit battery_cost_kwh: 0.0
    context.settings.battery_cost_kwh = 0.0;
    context.quote.battery_cost_kwh = 0.0;
    context.settings.storage_cost_mode = 'purchased';
    vm.runInContext(`applySolarCandidate(${JSON.stringify(zeroStorageCand)}, 'Zero-storage with explicit 0')`, context);
    assert.strictEqual(context.settings.battery_cost_kwh, 0.0, 'Settings battery_cost_kwh preserved at 0.0 on zero-storage apply');
    assert.strictEqual(context.quote.battery_cost_kwh, 0.0, 'Quote battery_cost_kwh preserved at 0.0 on zero-storage apply');

    // 4. Positive purchased candidate accepting finite nonnegative cost (including 0.0) without fallback to benchmark
    const freeBatCand = {
      ...purchasedFinCand,
      settings: {
        ...purchasedFinCand.settings,
        battery_cost_kwh: 0.0,
        storage_cost_mode: 'purchased'
      }
    };
    vm.runInContext(`applySolarCandidate(${JSON.stringify(freeBatCand)}, 'Purchased free battery')`, context);
    assert.strictEqual(context.settings.battery_cost_kwh, 0.0, 'Positive candidate with 0.0 battery_cost_kwh applied without fallback to benchmark');

    // 5. Quoted 0 with original positive overrides preserved
    context.quote.battery_cost_kwh = 0.0;
    context.settings.battery_cost_kwh = 0.0;
    context.settings.battery_module_cost_override = 2500.0;
    context.settings.battery_module_extra_cost = 175.0;
    vm.runInContext(`applySolarCandidate(${JSON.stringify(zeroStorageCand)}, 'Zero-storage with quote 0 and overrides')`, context);
    assert.strictEqual(context.settings.battery_cost_kwh, 0.0, 'Settings battery_cost_kwh remains 0.0');
    assert.strictEqual(context.settings.battery_module_cost_override, 2500.0, 'Settings battery_module_cost_override preserved');
    assert.strictEqual(context.settings.battery_module_extra_cost, 175.0, 'Settings battery_module_extra_cost preserved');
    assert.strictEqual(context.quote.line_items, originalLineItems, 'Quote line items array identity preserved');

    const evalZero2 = await pythonEvaluate({ settings: context.settings, quote: context.quote });
    assert(typeof evalZero2.capex_eur === 'number', 'Quote evaluate works with zero battery cost');

    // Restore
    context.settings = settingsBackup;
    context.quote = quoteBackup;
  }

  console.log('Solar sizing UI renderer, controls, stale invalidation, and apply tests passed successfully.');
}

runTests().catch(err => {
  console.error('Test failure:', err);
  process.exit(1);
});
