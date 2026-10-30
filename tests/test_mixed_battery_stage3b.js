/* Run with Node: Stage 3B interface integration test suite for mixed battery sizes. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function createStage3bEnvironment(initialSettings = {}) {
  const registeredElements = new Map();
  const apiRequests = [];
  let scheduled = [];

  class MockElement {
    constructor(id = '', tag = 'div') {
      this.id = id;
      this.tagName = tag.toUpperCase();
      this.innerHTML = '';
      this.textContent = '';
      this._value = '';
      this.disabled = false;
      this.hidden = false;
      this.readOnly = false;
      this.type = 'text';
      this.dataset = {};
      this.attributes = new Map();
      this.style = {};
      this.classList = {
        _classes: new Set(),
        add: (...cls) => cls.forEach(c => this.classList._classes.add(c)),
        remove: (...cls) => cls.forEach(c => this.classList._classes.delete(c)),
        contains: c => this.classList._classes.has(c),
        toggle: (c, force) => {
          if (force === true) this.classList._classes.add(c);
          else if (force === false) this.classList._classes.delete(c);
          else if (this.classList._classes.has(c)) this.classList._classes.delete(c);
          else this.classList._classes.add(c);
          return this.classList._classes.has(c);
        }
      };
      this.listeners = {};
    }
    get value() { return this._value; }
    set value(v) { this._value = String(v ?? ''); }
    setAttribute(k, v) { this.attributes.set(k, String(v)); if (k === 'value') this._value = String(v); }
    getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
    removeAttribute(k) { this.attributes.delete(k); }
    addEventListener(evt, fn) { this.listeners[evt] = this.listeners[evt] || []; this.listeners[evt].push(fn); }
    dispatchEvent(evt) { (this.listeners[evt.type || evt] || []).forEach(fn => fn(evt)); }
    focus() {}
    closest(sel) {
      if (sel === 'label') return this;
      if (sel === '[data-save-default]' && this.dataset.saveDefault) return this;
      if (sel === '[data-reset-setting]' && this.dataset.resetSetting) return this;
      if (sel === '[data-control]' && this.dataset.control) return this;
      return null;
    }
    querySelector(sel) {
      const all = this.querySelectorAll(sel);
      return all.length ? all[0] : null;
    }
    querySelectorAll(sel) {
      return Array.from(registeredElements.values()).filter(el => {
        if (sel.startsWith('#') && el.id === sel.slice(1)) return true;
        if (sel.startsWith('.') && el.classList.contains(sel.slice(1))) return true;
        if (sel.includes('[data-setting=')) {
          const m = sel.match(/\[data-setting="([^"]+)"\]/);
          if (m && el.dataset.setting === m[1]) return true;
        }
        if (sel.includes('[data-battery-row=')) {
          const m = sel.match(/\[data-battery-row="([^"]+)"\]/);
          if (m && el.dataset.batteryRow === m[1]) return true;
        }
        return false;
      });
    }
  }

  const getEl = id => {
    if (!registeredElements.has(id)) {
      registeredElements.set(id, new MockElement(id));
    }
    return registeredElements.get(id);
  };

  const defaultSettings = {
    south_panels: 12, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
    panel_watts: 400, inverter_kw: 6, charge_kw: 3, discharge_kw: 3, roundtrip_efficiency: 0.9,
    battery_modules: 2, battery_module_kwh: 5.0, battery_kwh: 10.0,
    battery_mixed_sizes: false, battery_secondary_modules: 0, battery_secondary_module_kwh: 6.9,
    load_scenario: 'electrified', deduction_rate: 0.5, horizon_years: 20, dormer_south_proxy: false,
    ...initialSettings
  };

  getEl('maxSouth').value = '22';
  getEl('maxWest').value = '11';
  getEl('maxEast').value = '6';
  getEl('maxDormer').value = '4';
  getEl('maxFlat').value = '8';
  getEl('maxSecondarySouth').value = '5';
  getEl('solarNpvSacrifice').value = '5';
  getEl('solarCoverageTarget').value = '70';
  getEl('solarRetainedProfit').value = '95';
  getEl('optPanelWatts').value = '400';
  getEl('optModuleKwh').value = String(defaultSettings.battery_module_kwh ?? 5.0);
  getEl('optSecondaryModuleKwh').value = String(defaultSettings.battery_secondary_module_kwh ?? 6.9);
  getEl('optInverter').value = '6';
  getEl('optCharge').value = '3';
  getEl('optDischarge').value = '3';
  getEl('optBatteryMin').value = '0';
  getEl('optBatteryMax').value = '4';

  const defaultQuote = {
    price_basis: 'modeled',
    line_items: []
  };

  const schema = {
    defaults: { ...defaultSettings },
    records: [
      { name: 'battery_modules', unit: 'modules', bounds: [0, 20] },
      { name: 'battery_module_kwh', unit: 'kWh/module', bounds: [0, 25] },
      { name: 'battery_kwh', unit: 'kWh', bounds: [0, 50] },
      { name: 'battery_mixed_sizes', unit: 'flag' },
      { name: 'battery_secondary_modules', unit: 'modules', bounds: [0, 20] },
      { name: 'battery_secondary_module_kwh', unit: 'kWh/module', bounds: [0, 25] }
    ]
  };

  const store = {};
  const mockLocalStorage = {
    getItem: k => store[k] ?? null,
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; }
  };

  const context = vm.createContext({
    $: getEl,
    document: {
      activeElement: null,
      getElementById: id => getEl(id),
      querySelector: sel => sel.startsWith('#') ? getEl(sel.slice(1)) : null,
      querySelectorAll: sel => Array.from(registeredElements.values()).filter(el => {
        if (sel.startsWith('#') && el.id === sel.slice(1)) return true;
        if (sel.startsWith('.') && el.classList.contains(sel.slice(1))) return true;
        if (sel.includes('[data-setting=')) {
          const m = sel.match(/\[data-setting="([^"]+)"\]/);
          if (m && el.dataset.setting === m[1]) return true;
        }
        if (sel.includes('[data-battery-row=')) {
          const m = sel.match(/\[data-battery-row="([^"]+)"\]/);
          if (m && el.dataset.batteryRow === m[1]) return true;
        }
        return false;
      }),
      addEventListener: () => {},
      removeEventListener: () => {}
    },
    window: {
      addEventListener: () => {},
      removeEventListener: () => {}
    },
    matchMedia: () => ({ matches: false }),
    localStorage: mockLocalStorage,
    settings: { ...defaultSettings },
    quote: { ...defaultQuote },
    schema,
    panelKeys: ['south_panels', 'west_panels', 'east_panels', 'dormer_panels', 'flat_panels', 'secondary_south_panels'],
    defaultRoofLimits: { south: 22, west: 11, east: 6, dormer: 4, flat: 8, secondary_south: 5 },
    optimizerPrefs: {
      battery_min_modules: 0,
      battery_max_modules: 4,
      objective: 'autarky',
      load_scenario: 'electrified',
      panel_maxima: { south: 22, west: 11, east: 6, dormer: 4, flat: 8, secondary_south: 5 },
      include_theoretical: true,
      chart_x: 'autarky',
      chart_y: 'npv_eur'
    },
    current: {
      result: {
        layout: { panel_count: 12, kwp: 4.8 },
        settings: { ...defaultSettings },
        metrics: { npv_eur: { median: 5000 }, autarky: { median: 0.65 }, self_consumption: { median: 0.70 }, discounted_payback_years: { median: 7.5 } },
        finance: { capex_eur: 8500 }
      }
    },
    fetch: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      apiRequests.push({ url, body });
      if (url === '/api/solar-sizing') {
        return {
          ok: true,
          json: async () => ({
            status: 'OK',
            recommended_candidate: {
              panel_count: 12,
              battery_kwh: 11.9,
              battery_modules: 2,
              battery_secondary_modules: 1,
              battery_module_kwh: 5.0,
              battery_secondary_module_kwh: 6.9,
              battery_mixed_sizes: true,
              roof_counts: { south: 12 },
              metrics: { npv_eur: { median: 5000 }, autarky: { median: 0.65 } }
            },
            search_summary: { evaluated_count: 10, weather_count: 19, inverter_kw: 6.0 },
            recommendation_choices: {},
            size_curve: [],
            comparisons: [],
            tradeoff_matrix: { pv_axis_kwp: [], battery_axis_kwh: [], cells: [] }
          })
        };
      }
      return {
        ok: true,
        json: async () => ({ status: 'OK' })
      };
    },
    api: async (url, body) => {
      apiRequests.push({ url, body });
      return { status: 'OK' };
    },
    schedule: opts => { scheduled.push(opts); },
    recompute: () => {},
    syncSetting: () => {},
    updateControlModifiedState: () => {},
    updateCurrentSystemSummary: () => {},
    updateModifiedBadge: () => {},
    clearEditedQuoteOverrides: () => {},
    refreshConditionalControls: () => {},
    renderControls: () => {},
    renderLines: () => {},
    syncQuoteFields: () => {},
    syncQuotePricingDescription: () => {},
    renderRoofLimitsComparison: () => {},
    switchView: () => {},
    showError: err => { console.error('UI Error in test:', err.message || err); },
    setTimeout,
    clearTimeout,
    console
  });
  context.window.fetch = context.fetch;

  vm.runInContext(source, context);
  vm.runInContext(`
    settings = ${JSON.stringify(defaultSettings)};
    schema = ${JSON.stringify(schema)};
    quote = ${JSON.stringify(defaultQuote)};
  `, context);

  Object.defineProperty(context, 'settings', {
    get() { return vm.runInContext('settings', context); },
    set(v) { vm.runInContext('settings = ' + JSON.stringify(v), context); },
    configurable: true
  });
  Object.defineProperty(context, 'schema', {
    get() { return vm.runInContext('schema', context); },
    set(v) { vm.runInContext('schema = ' + JSON.stringify(v), context); },
    configurable: true
  });
  Object.defineProperty(context, 'quote', {
    get() { return vm.runInContext('quote', context); },
    set(v) { vm.runInContext('quote = ' + JSON.stringify(v), context); },
    configurable: true
  });
  Object.defineProperty(context, 'saved', {
    get() { return vm.runInContext('saved', context); },
    set(v) { vm.runInContext('saved = ' + JSON.stringify(v), context); },
    configurable: true
  });

  return { context, registeredElements, getEl, apiRequests, scheduled };
}

async function runTests() {
  console.log('Starting Stage 3B Interface Integration Tests...');

  // -------------------------------------------------------------------------
  // TEST 1: Solar search allows [11, 12] in mixed mode with 5 + 6.9 kWh = 11.9 kWh
  // -------------------------------------------------------------------------
  {
    console.log('Test 1: Solar search bounded module combination [11, 12] with 5 + 6.9 kWh...');
    const { context, getEl, apiRequests } = createStage3bEnvironment({
      battery_mixed_sizes: true,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_modules: 2,
      battery_secondary_modules: 1,
      battery_kwh: 11.9
    });

    // Setup input elements
    getEl('solarBatteryMin').value = '11';
    getEl('solarBatteryMax').value = '12';
    getEl('solarBatteryMode').value = 'joint';
    getEl('solarBatteryStep').value = 'combinations';
    getEl('solarNpvSacrifice').value = '5';
    getEl('solarCoverageTarget').value = '70';

    context.syncSolarBatteryStep();
    assert.equal(getEl('solarBatteryStep').disabled, true, 'step select is disabled in mixed mode');
    assert.match(getEl('solarBatteryStep').innerHTML, /Module combinations \((5|5\.0) \+ 6\.9 kWh\)/, 'displays module combination label with physical sizes');

    // getSolarOptions must NOT send battery_step_kwh in mixed mode
    const opts = context.getSolarOptions();
    assert.equal(opts.battery_step_kwh, undefined, 'omits battery_step_kwh in mixed mode');

    // Run solar sizing: [11, 12] should succeed because 1 * 5 + 1 * 6.9 = 11.9 is in [11, 12]
    let errorThrown = false;
    try {
      await context.runSolarSizingAnalysis();
    } catch (e) {
      errorThrown = true;
    }
    assert.equal(errorThrown, false, 'runSolarSizingAnalysis does not throw single-size divisibility error');
    const solarReq = apiRequests.find(r => r.url === '/api/solar-sizing');
    assert.ok(solarReq, 'API request was dispatched for /api/solar-sizing');
    assert.equal(solarReq.url, '/api/solar-sizing');
    assert.equal(solarReq.body.options.battery_min_kwh, 11);
    assert.equal(solarReq.body.options.battery_max_kwh, 12);
    assert.equal(solarReq.body.options.battery_step_kwh, undefined);
    console.log('  ✔ Test 1 passed: [11, 12] combination 11.9 kWh is accepted without divisibility errors');
  }

  // -------------------------------------------------------------------------
  // TEST 2: applySolarCandidate preserves full 6-field mixed composition and zero-mix
  // -------------------------------------------------------------------------
  {
    console.log('Test 2: applySolarCandidate atomic copy across mixed, single, and zero-storage...');
    const { context, getEl } = createStage3bEnvironment({
      battery_mixed_sizes: true,
      battery_modules: 4,
      battery_secondary_modules: 2, // 2 primary + 2 secondary = 23.8 kWh
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 23.8
    });

    // Make sure solarAcceptedFingerprint matches current
    vm.runInContext('solarData = { status: "OK" }; $("solarResultsWrap").classList.remove("stale"); solarAcceptedFingerprint = solarStateFingerprint()', context);

    // 2A: Apply candidate 11.9 kWh (1 primary + 1 secondary) from installed state with 2 secondary modules (23.8 kWh)
    const mixedCand11_9 = {
      panel_count: 14,
      kwp: 5.6,
      roof_counts: { south: 14 },
      battery_mixed_sizes: true,
      battery_modules: 2,
      battery_secondary_modules: 1,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 11.9,
      capex_eur: 13000,
      npv_median: 6000
    };

    context.applySolarCandidate(mixedCand11_9, 'mixed candidate 11.9');
    assert.equal(context.settings.battery_mixed_sizes, true);
    assert.equal(context.settings.battery_modules, 2);
    assert.equal(context.settings.battery_secondary_modules, 1, 'installed secondary count 2 was overwritten with candidate secondary count 1');
    assert.equal(context.settings.battery_module_kwh, 5.0);
    assert.equal(context.settings.battery_secondary_module_kwh, 6.9);
    assert.equal(Math.abs(context.settings.battery_kwh - 11.9) < 1e-4, true, 'exact unrounded mixed sum preserved');

    // 2B: Apply a single-size candidate (reverting to single mode)
    vm.runInContext('solarData = { status: "OK" }; $("solarResultsWrap").classList.remove("stale"); solarAcceptedFingerprint = solarStateFingerprint()', context);
    const singleCand = {
      panel_count: 14,
      kwp: 5.6,
      roof_counts: { south: 14 },
      battery_mixed_sizes: false,
      battery_modules: 2,
      battery_secondary_modules: 0,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 10.0,
      capex_eur: 12000,
      npv_median: 5500
    };

    context.applySolarCandidate(singleCand, 'single candidate');
    assert.equal(context.settings.battery_mixed_sizes, false, 'mixed mode toggled off');
    assert.equal(context.settings.battery_modules, 2);
    assert.equal(context.settings.battery_secondary_modules, 0, 'secondary modules reset to 0');
    assert.equal(context.settings.battery_kwh, 10.0);

    // 2C: Apply zero-storage candidate
    vm.runInContext('solarData = { status: "OK" }; $("solarResultsWrap").classList.remove("stale"); solarAcceptedFingerprint = solarStateFingerprint()', context);
    const zeroCand = {
      panel_count: 12,
      kwp: 4.8,
      roof_counts: { south: 12 },
      battery_mixed_sizes: false,
      battery_modules: 0,
      battery_secondary_modules: 0,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 0.0,
      capex_eur: 7500,
      npv_median: 4800
    };

    context.applySolarCandidate(zeroCand, 'zero storage candidate');
    assert.equal(context.settings.battery_modules, 0);
    assert.equal(context.settings.battery_secondary_modules, 0);
    assert.equal(context.settings.battery_kwh, 0.0);

    // 2D: Stale safeguard check: changed inputs reject apply
    vm.runInContext('solarData = { status: "OK" }; $("solarResultsWrap").classList.remove("stale"); solarAcceptedFingerprint = solarStateFingerprint()', context);
    context.settings.battery_module_kwh = 6; // Change a field present in the mock schema.
    assert.notEqual(context.solarStateFingerprint(), vm.runInContext('solarAcceptedFingerprint', context));
    context.applySolarCandidate(mixedCand11_9, 'stale candidate');
    assert.match(getEl('solarApplyStatus').textContent, /Cannot apply: inputs changed/);
    assert.equal(context.settings.battery_kwh, 0, 'stale apply preserves the current zero-storage configuration');
    console.log('  ✔ Test 2 passed: applySolarCandidate atomically handles mixed, single, zero-storage, and stale guards');
  }

  // -------------------------------------------------------------------------
  // TEST 3: applyOptimizedSettings round-trips all 6 battery fields and loadCandidates works
  // -------------------------------------------------------------------------
  {
    console.log('Test 3: applyOptimizedSettings round-trips all 6 fields & loadCandidates mixed validation...');
    const { context } = createStage3bEnvironment({
      battery_mixed_sizes: false,
      battery_modules: 1,
      battery_module_kwh: 5.0,
      battery_kwh: 5.0
    });

    const optResult = {
      candidates: [],
      best_candidate: {
        settings: {
          south_panels: 14,
          battery_mixed_sizes: true,
          battery_modules: 3,
          battery_secondary_modules: 1,
          battery_module_kwh: 5.0,
          battery_secondary_module_kwh: 6.9,
          battery_kwh: 16.9
        }
      }
    };
    context.optResult = optResult;
    vm.runInContext('optimizerResult = optResult; optimizerFingerprint = optimizerStateFingerprint();', context);

    context.applyOptimizedSettings(optResult.best_candidate);
    assert.equal(context.settings.battery_mixed_sizes, true);
    assert.equal(context.settings.battery_modules, 3);
    assert.equal(context.settings.battery_secondary_modules, 1);
    assert.equal(context.settings.battery_module_kwh, 5.0);
    assert.equal(context.settings.battery_secondary_module_kwh, 6.9);
    assert.equal(Math.abs(context.settings.battery_kwh - 16.9) < 1e-4, true);

    // 3B: loadCandidates runs cleanly with battery_mixed_sizes: true and coherently zeroes secondary modules
    const { context: optCtx, apiRequests: optApiRequests, getEl: getOptEl } = createStage3bEnvironment({
      battery_mixed_sizes: true,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_modules: 2,
      battery_secondary_modules: 1,
      battery_kwh: 11.9
    });

    getOptEl('optPanelWatts').value = '400';
    getOptEl('optModuleKwh').value = '5.0';
    getOptEl('optSecondaryModuleKwh').value = '6.9';
    getOptEl('optInverter').value = '6';
    getOptEl('optCharge').value = '3';
    getOptEl('optDischarge').value = '3';
    getOptEl('optBatteryMin').value = '0';
    getOptEl('optBatteryMax').value = '4';
    getOptEl('maxSouth').value = '22';
    getOptEl('maxWest').value = '11';

    await optCtx.loadCandidates();
    const optReq = optApiRequests.find(r => r.url === '/api/optimize-configurations');
    assert.ok(optReq, 'loadCandidates dispatched API request without throwing on boolean battery_mixed_sizes');
    assert.equal(optReq.body.settings.battery_modules, 0, 'clears battery_modules in search request');
    assert.equal(optReq.body.settings.battery_secondary_modules, 0, 'clears battery_secondary_modules in search request');
    assert.equal(optReq.body.settings.battery_kwh, 0, 'clears battery_kwh in search request');
    assert.equal(optReq.body.settings.battery_mixed_sizes, true, 'preserves battery_mixed_sizes in search settings');
    assert.equal(optReq.body.settings.battery_secondary_module_kwh, 6.9, 'preserves battery_secondary_module_kwh in search settings');
    console.log('  ✔ Test 3 passed: applyOptimizedSettings and loadCandidates mixed validation & coherent payload');
  }

  // -------------------------------------------------------------------------
  // TEST 4: Legacy JSON import and saved scenario restore ignore mixed defaults
  // -------------------------------------------------------------------------
  {
    console.log('Test 4: Legacy JSON import & saved scenario restore with active mixed defaults...');
    const { context, getEl } = createStage3bEnvironment();

    // Emulate saved schema defaults that enable mixed mode
    context.schema.defaults.battery_mixed_sizes = true;
    context.schema.defaults.battery_secondary_modules = 2;
    context.schema.defaults.battery_secondary_module_kwh = 6.9;
    context.settings.battery_mixed_sizes = true;
    context.settings.battery_secondary_modules = 2;

    // 4A: Import a legacy JSON scenario lacking battery_mixed_sizes
    const legacyJson = {
      settings: {
        south_panels: 16,
        battery_modules: 3,
        battery_module_kwh: 5.0,
        battery_kwh: 15.0
        // battery_mixed_sizes omitted!
      }
    };

    const mockFile = { text: async () => JSON.stringify(legacyJson) };
    const loadEvent = { target: { files: [mockFile] } };

    await getEl('loadFile').onchange(loadEvent);
    assert.equal(context.settings.battery_mixed_sizes, false, 'legacy import forces mixed mode to false');
    assert.equal(context.settings.battery_secondary_modules, 0, 'legacy import zeroes secondary modules');
    assert.equal(context.settings.battery_modules, 3);
    assert.equal(context.settings.battery_kwh, 15.0);

    // 4B: Restore a saved scenario that omits battery_mixed_sizes
    context.settings.battery_mixed_sizes = true;
    context.settings.battery_secondary_modules = 1;
    context.saved = [{
      name: 'Old Saved Scenario',
      settings: {
        south_panels: 10,
        battery_modules: 2,
        battery_module_kwh: 5.0,
        battery_kwh: 10.0
      }
    }];

    context.renderSaved();
    getEl('saved').onclick({ target: { dataset: { restore: '0' } } });
    assert.equal(context.settings.battery_mixed_sizes, false, 'restoring legacy scenario forces mixed mode to false');
    assert.equal(context.settings.battery_secondary_modules, 0, 'restoring legacy scenario zeroes secondary modules');
    assert.equal(context.settings.battery_kwh, 10.0);
    console.log('  ✔ Test 4 passed: legacy JSON import and saved scenario restore never reinterpret old counts as mixed');
  }

  // -------------------------------------------------------------------------
  // TEST 5: Both sizes invalidate stale solar sizing and optimizer searches
  // -------------------------------------------------------------------------
  {
    console.log('Test 5: Both primary and secondary module sizes invalidate stale searches...');
    const { context } = createStage3bEnvironment({
      battery_mixed_sizes: true,
      battery_modules: 2,
      battery_secondary_modules: 1,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 11.9
    });

    const initialSolarFp = context.solarStateFingerprint();
    const initialOptFp = context.optimizerStateFingerprint();

    // 5A: Edit primary size
    context.editSetting('battery_module_kwh', 4.8);
    const solarFpAfterFirst = context.solarStateFingerprint();
    const optFpAfterFirst = context.optimizerStateFingerprint();
    assert.notEqual(solarFpAfterFirst, initialSolarFp, 'editing primary size changes solar fingerprint');
    assert.notEqual(optFpAfterFirst, initialOptFp, 'editing primary size changes optimizer fingerprint');

    // 5B: Edit secondary size
    context.editSetting('battery_secondary_module_kwh', 7.2);
    const solarFpAfterSec = context.solarStateFingerprint();
    const optFpAfterSec = context.optimizerStateFingerprint();
    assert.notEqual(solarFpAfterSec, solarFpAfterFirst, 'editing secondary size changes solar fingerprint');
    assert.notEqual(optFpAfterSec, optFpAfterFirst, 'editing secondary size changes optimizer fingerprint');

    // 5C: Toggle mixed mode
    context.editSetting('battery_mixed_sizes', false);
    const solarFpAfterToggle = context.solarStateFingerprint();
    const optFpAfterToggle = context.optimizerStateFingerprint();
    assert.notEqual(solarFpAfterToggle, solarFpAfterSec, 'toggling mixed mode changes solar fingerprint');
    assert.notEqual(optFpAfterToggle, optFpAfterSec, 'toggling mixed mode changes optimizer fingerprint');
    console.log('  ✔ Test 5 passed: both physical sizes and mode toggle invalidate fingerprints');
  }

  // -------------------------------------------------------------------------
  // TEST 6: Formatting labels across cards, tables, tooltips without averages
  // -------------------------------------------------------------------------
  {
    console.log('Test 6: Format composition breakdown without averages...');
    const { context, getEl } = createStage3bEnvironment();

    const mixedItem = {
      panel_count: 14,
      kwp: 5.6,
      battery_mixed_sizes: true,
      battery_modules: 3,
      battery_secondary_modules: 1,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 16.9,
      capex_eur: 14000,
      npv_median: 6000,
      autarky_median: 0.72,
      discounted_payback_years: 7.2
    };

    // 6A: formatBatteryComposition output
    const fullText = context.formatBatteryComposition(mixedItem);
    assert.equal(fullText, '2 × 5 kWh + 1 × 6.9 kWh = 16.9 usable kWh (3 modules)', 'full text shows exact breakdown');

    const shortText = context.formatBatteryComposition(mixedItem, { short: true });
    assert.equal(shortText, '16.9 kWh (2×5 + 1×6.9)', 'short text displays readable count breakdown');

    // 6B: renderCandidates displays formatBatteryComposition on best configuration card
    context.mockOptResult = {
      objective: 'autarky',
      best_candidate: mixedItem,
      candidates: [mixedItem],
      search: { exact: true, evaluated_count: 1 }
    };
    vm.runInContext('optimizerResult = mockOptResult; optimizerFingerprint = optimizerStateFingerprint(); renderCandidates();', context);
    assert.match(getEl('bestConfiguration').innerHTML, /16\.9 usable kWh \(3 modules\)/);
    assert.match(getEl('candidates').innerHTML, /2 \+ 1 \(3\)/, 'candidate row formats module breakdown');

    // 6C: Recommendation choices cards show exact breakdown
    const solarData = {
      search_summary: { evaluated_count: 10, weather_count: 19, inverter_kw: 6.0 },
      recommendation_choices: {
        financial: mixedItem,
        balanced: mixedItem,
        budget: mixedItem
      },
      recommended_candidate: mixedItem,
      size_curve: [mixedItem],
      comparisons: [{ id: 'balanced', label: 'Balanced', ...mixedItem }],
      top_alternatives: [mixedItem],
      tradeoff_matrix: { pv_axis_kwp: [5.6], battery_axis_kwh: [16.9], cells: [{ pv_kwp: 5.6, battery_kwh: 16.9, npv_median: 6000, capex_eur: 14000, settings: mixedItem }] }
    };

    context.renderSolarRecommendationChoices(solarData);
    assert.match(getEl('solarChoiceCards').innerHTML, /2 × 5 \+ 1 × 6\.9 mod/);

    // 6D: Alternatives table and heatmap
    context.renderSolarAlternativesTable(solarData);
    assert.match(getEl('solarAlternativesTable').innerHTML, /16\.9 kWh \(2×5 \+ 1×6\.9\)/);

    context.renderSolarTradeoffHeatmap(solarData);
    assert.match(getEl('solarChart3').innerHTML, /16\.9 kWh/, 'heatmap axis rounded cleanly');
    assert.match(getEl('solarChart3').innerHTML, /Battery: 16\.9 kWh \(2×5 \+ 1×6\.9\)/, 'heatmap cell tooltip has breakdown');
    console.log('  ✔ Test 6 passed: composition labels formatted truthfully without averages');
  }

  // -------------------------------------------------------------------------
  // TEST 7: Battery sizing Chart 2 delta gaps, fractional bands, #batStep sync
  // -------------------------------------------------------------------------
  {
    console.log('Test 7: Battery sizing Chart 2 delta capacity gaps and #batStep hiding...');
    const { context, getEl } = createStage3bEnvironment({
      battery_mixed_sizes: true
    });

    // 7A: #batStep is disabled and hidden when mixed
    context.syncBatterySizingControls();
    assert.equal(getEl('batStep').disabled, true);
    assert.equal(getEl('batStep').hidden, true);

    // When not mixed, it is enabled
    context.settings.battery_mixed_sizes = false;
    context.syncBatterySizingControls();
    assert.equal(getEl('batStep').disabled, false);
    assert.equal(getEl('batStep').hidden, false);

    // 7B: Chart 2 renders delta_capacity_kwh steps and fractional optimum band
    const mockBatData = {
      chart2_incremental_savings: {
        annualized_marginal_cost_gross: 45,
        annualized_marginal_cost_net: 30,
        optimum_band: {
          ensemble_optimum_kwh: 11.9,
          gross_ensemble_optimum_kwh: 5.0,
          weather_mean_kwh: 10.8,
          weather_min_kwh: 5.0,
          weather_max_kwh: 11.9,
          weather_median_kwh: 11.9,
          sensitivity_min_kwh: 5.0,
          sensitivity_max_kwh: 16.9,
          gross_optimum_min_kwh: 0,
          gross_optimum_max_kwh: 5.0
        },
        steps: [
          {
            capacity_from: 0,
            capacity_to: 5.0,
            delta_capacity_kwh: 5.0,
            incremental_savings_eur_kwh: { min: 35, median: 42, mean: 41.5, max: 48 }
          },
          {
            capacity_from: 5.0,
            capacity_to: 11.9,
            delta_capacity_kwh: 6.9,
            incremental_savings_eur_kwh: { min: 28, median: 32, mean: 31.8, max: 36 }
          }
        ]
      }
    };

    context.renderBatteryIncrementalChart(mockBatData);
    const chartSvg = getEl('batChart2').innerHTML;
    assert.match(chartSvg, /\+6\.9 kWh/, 'step detail reflects actual delta gap of 6.9 kWh');
    assert.match(chartSvg, /Ensemble optimum 11\.9 kWh \(weather spread 5\.0–11\.9 kWh\)/, 'fractional ensemble optimum and weather spread rendered');
    assert.match(getEl('batTable2').innerHTML, /€31\.80\/kWh\/yr/, 'marginal savings use the mean rather than median');
    assert.match(getEl('batTable2').innerHTML, /0\.0 → 5\.0 kWh \(\+5\.0 kWh\)/);
    assert.match(getEl('batTable2').innerHTML, /5\.0 → 11\.9 kWh \(\+6\.9 kWh\)/);
    console.log('  ✔ Test 7 passed: battery sizing handles irregular delta gaps and hides step selector');
  }

  // -------------------------------------------------------------------------
  // TEST 8: Input validation edge cases: zero capacity rejected before mutation
  // -------------------------------------------------------------------------
  {
    console.log('Test 8: Zero capacity input rejection before mutation & error recovery...');
    const { context, scheduled } = createStage3bEnvironment({
      battery_mixed_sizes: true,
      battery_modules: 3,
      battery_secondary_modules: 1, // 2 primary + 1 secondary
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 16.9
    });

    const mockPrimaryInput = {
      setAttribute: (k, v) => { mockPrimaryInput[k] = v; },
      removeAttribute: k => { delete mockPrimaryInput[k]; },
      classList: {
        add: c => { mockPrimaryInput._invalid = true; },
        remove: c => { delete mockPrimaryInput._invalid; }
      }
    };

    // 8A: Try to set primary module capacity to 0 while primary modules > 0
    const priorScheduleCount = scheduled.length;
    context.editSetting('battery_module_kwh', 0, mockPrimaryInput);
    assert.equal(mockPrimaryInput['aria-invalid'], 'true', 'aria-invalid set on input');
    assert.equal(context.settings.battery_module_kwh, 5.0, 'settings.battery_module_kwh NOT mutated');
    assert.equal(scheduled.length, priorScheduleCount, 'schedule() was NOT called');

    // 8B: Try to set secondary module capacity to 0 while secondary modules > 0
    const mockSecInput = {
      setAttribute: (k, v) => { mockSecInput[k] = v; },
      removeAttribute: k => { delete mockSecInput[k]; },
      classList: {
        add: c => {},
        remove: c => {}
      }
    };
    context.editSetting('battery_secondary_module_kwh', 0, mockSecInput);
    assert.equal(mockSecInput['aria-invalid'], 'true');
    assert.equal(context.settings.battery_secondary_module_kwh, 6.9, 'settings.battery_secondary_module_kwh NOT mutated');

    // 8C: Try to activate dormant secondary size (currently count=0, capacity=0) with row quantity edit
    context.settings.battery_secondary_modules = 0;
    context.settings.battery_secondary_module_kwh = 0; // zero capacity is allowed when unused!
    context.settings.battery_modules = 2; // only 2 primary modules active

    const mockSecRowInput = {
      setAttribute: (k, v) => { mockSecRowInput[k] = v; },
      removeAttribute: k => { delete mockSecRowInput[k]; },
      classList: {
        add: c => {},
        remove: c => {}
      }
    };
    context.editMixedBatteryRowQuantity('secondary', 1, mockSecRowInput);
    assert.equal(mockSecRowInput['aria-invalid'], 'true', 'activating dormant size with 0 capacity rejected locally');
    assert.equal(context.settings.battery_secondary_modules, 0, 'secondary modules remained 0');

    // 8D: Valid subsequent edit clears field error
    context.editSetting('battery_secondary_module_kwh', 6.9, mockSecInput);
    assert.equal(mockSecInput['aria-invalid'], undefined, 'aria-invalid cleared on valid edit');
    assert.equal(context.settings.battery_secondary_module_kwh, 6.9, 'settings mutated on valid edit');

    // Now activating secondary row quantity succeeds
    context.editMixedBatteryRowQuantity('secondary', 1, mockSecRowInput);
    assert.equal(mockSecRowInput['aria-invalid'], undefined);
    assert.equal(context.settings.battery_secondary_modules, 1);
    assert.equal(context.settings.battery_modules, 3);
    assert.equal(Math.abs(context.settings.battery_kwh - 16.9) < 1e-4, true);
    console.log('  ✔ Test 8 passed: zero capacity rejected before mutation and recovered gracefully');
  }

  console.log('\nAll Stage 3B Interface Integration Tests passed successfully!');
}

runTests().catch(err => {
  console.error('Test failure:', err);
  process.exit(1);
});
