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

function makeRealisticCandidatesResult(searchScenario = 'electrified', objective = 'autarky') {
  return {
    objective,
    recommended_id: 'opt_1',
    search: {
      exact: true,
      evaluated_count: 8,
      search_space_size: 40,
      load_scenario: searchScenario,
      requested_panel_maxima: { south: 22, west: 11 },
      best_at_panel_maxima: {}
    },
    best_candidate: {
      id: 'opt_1',
      settings: {
        south_panels: 12,
        west_panels: 4,
        battery_modules: 2,
        battery_module_kwh: 5.0,
        battery_kwh: 10.0,
        load_scenario: searchScenario
      },
      metrics: {
        autarky: { min: 0.55, median: 0.68, max: 0.78 },
        npv_eur: { min: 3200, median: 4800, max: 6200 },
        payback_years: { min: 6, median: 8, max: 10 },
        self_consumption: { min: 0.45, median: 0.55, max: 0.65 }
      },
      finance: { capex_eur: 8500 },
      theoretical: false
    },
    candidates: [
      {
        id: 'opt_1',
        settings: {
          south_panels: 12,
          west_panels: 4,
          battery_modules: 2,
          battery_module_kwh: 5.0,
          battery_kwh: 10.0,
          load_scenario: searchScenario
        },
        battery_kwh: 10.0,
        metrics: {
          autarky: { min: 0.55, median: 0.68, max: 0.78 },
          npv_eur: { min: 3200, median: 4800, max: 6200 },
          payback_years: { min: 6, median: 8, max: 10 },
          discounted_payback_years: { min: 8, median: 10, max: 12 },
          self_consumption: { min: 0.45, median: 0.55, max: 0.65 }
        },
        finance: { capex_eur: 8500 },
        theoretical: false
      }
    ]
  };
}

function createOptimizerHarness() {
  const elements = new Map();

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
        if (sel === '[data-apply-best]' && html.includes('data-apply-best')) {
          return makeEl('button', 'applyBestMock');
        }
        return null;
      },
      querySelectorAll(sel) {
        if (sel === '[data-use]' && html.includes('data-use')) {
          return [makeEl('button', 'useCandidateMock')];
        }
        return [];
      },
      closest(sel) {
        if (sel === '[data-apply-best]') return this;
        return makeEl(sel || 'div');
      },
      prepend(node) {
        children.unshift(node);
        if (node) node.parentElement = this;
      },
      appendChild(node) {
        children.push(node);
        if (node) node.parentElement = this;
      },
      insertBefore(newNode) {
        children.unshift(newNode);
        if (newNode) newNode.parentElement = this;
      },
      removeChild(node) {
        const idx = children.indexOf(node);
        if (idx !== -1) children.splice(idx, 1);
      },
      remove() {
        if (this.parentElement && this.parentElement.children) {
          const idx = this.parentElement.children.indexOf(this);
          if (idx !== -1) this.parentElement.children.splice(idx, 1);
        }
      }
    };
    if (id) elements.set(id, el);
    return el;
  }

  // Pre-populate optimizer IDs
  const optimizerIds = [
    'optPanelWatts', 'optModuleKwh', 'optBatteryMin', 'optBatteryMax',
    'optInverter', 'optCharge', 'optDischarge', 'filterLoad', 'optObjective',
    'maxSouth', 'maxWest', 'maxEast', 'maxDormer', 'maxFlat', 'maxSecondarySouth',
    'includeTheoretical', 'chartX', 'chartY', 'bestConfiguration', 'candidates',
    'paretoChart', 'paretoMethod', 'recomputePareto', 'error', 'status',
    'fullStudy', 'jobState', 'reset', 'save', 'clearSaved', 'loadFile',
    'roofLimitsStatus', 'roofLimitsComparison', 'resetRoofLimits'
  ];
  optimizerIds.forEach(id => makeEl(id.startsWith('opt') || id.startsWith('max') ? 'input' : 'div', id));

  elements.get('optPanelWatts').value = '400';
  elements.get('optModuleKwh').value = '5.0';
  elements.get('optBatteryMin').value = '0';
  elements.get('optBatteryMax').value = '4';
  elements.get('optInverter').value = '6';
  elements.get('optCharge').value = '3';
  elements.get('optDischarge').value = '3';
  elements.get('filterLoad').value = 'electrified';
  elements.get('optObjective').value = 'autarky';
  elements.get('maxSouth').value = '22';
  elements.get('maxWest').value = '11';
  elements.get('maxEast').value = '6';
  elements.get('maxDormer').value = '4';
  elements.get('maxFlat').value = '8';
  elements.get('maxSecondarySouth').value = '5';
  elements.get('includeTheoretical').checked = true;

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
    innerHeight: 768
  };
  win.window = win;
  win.document = doc;

  const optimizeCalls = [];
  const studyCalls = [];
  const jobCalls = [];
  const pendingOptimizeCalls = [];
  const pendingJobCalls = [];
  let autoResolveOptimize = true;
  let autoResolveJob = true;
  let studyPostHandler = null;
  let studyGetHandler = null;

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
  async function flushTimers() {
    const timers = [...pendingTimers];
    pendingTimers.length = 0;
    for (const t of timers) {
      const res = t.fn();
      if (res && typeof res.then === 'function') await res;
    }
  }

  const fallbackDefaults = {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [
      { id: 'morning', start_minute: 360, end_minute: 480 },
      { id: 'evening', start_minute: 960, end_minute: 1200 }
    ],
    ac_heating_additional_demand_pct: 10.0,
    south_panels: 14,
    west_panels: 6,
    east_panels: 0,
    dormer_panels: 0,
    flat_panels: 0,
    secondary_south_panels: 0,
    panel_watts: 400.0,
    battery_kwh: 10.0,
    battery_modules: 2,
    battery_module_kwh: 5.0,
    battery_mixed_sizes: false,
    battery_secondary_modules: 0,
    inverter_kw: 6.0,
    charge_kw: 3.0,
    discharge_kw: 3.0
  };

  const fallbackRecords = [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' },
    { name: 'south_panels', unit: 'panels' },
    { name: 'west_panels', unit: 'panels' },
    { name: 'battery_kwh', unit: 'kWh' },
    { name: 'battery_modules', unit: 'modules' },
    { name: 'battery_module_kwh', unit: 'kWh' },
    { name: 'panel_watts', unit: 'W' },
    { name: 'inverter_kw', unit: 'kW' }
  ];

  const consoleWarnings = [];
  const ctx = {
    console: {
      ...console,
      warn: (...args) => consoleWarnings.push(args)
    },
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
        return {
          ok: true,
          json: async () => ({
            compute_seconds: 0.01,
            result: {
              settings: body.settings || {},
              layout: { kwp: 8, panel_count: 20 },
              metrics: { npv_eur: { median: 4000 }, autarky: { median: 0.6 } },
              finance: { capex_eur: 8000 }
            }
          })
        };
      }
      if (pathUrl === '/api/optimize-configurations') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        optimizeCalls.push(body);
        const fixture = makeRealisticCandidatesResult(body.settings?.load_scenario || 'electrified', body.search?.objective || 'autarky');
        if (autoResolveOptimize) {
          return { ok: true, json: async () => fixture };
        }
        return new Promise((resolve, reject) => {
          pendingOptimizeCalls.push({
            body,
            resolve: val => resolve({ ok: true, json: async () => val || fixture }),
            reject: err => reject(err)
          });
        });
      }
      if (pathUrl === '/api/study') {
        if (fetchOpts && fetchOpts.method === 'POST') {
          const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
          studyCalls.push(body);
          if (studyPostHandler) return studyPostHandler(body);
          return { ok: true, json: async () => ({ state: 'running', progress: 'Starting candidates' }) };
        }
        if (studyGetHandler) return studyGetHandler();
        const lastDispatched = studyCalls.length ? studyCalls[studyCalls.length - 1].settings : fallbackDefaults;
        return {
          ok: true,
          json: async () => ({
            status: 'SCENARIO_PENDING',
            baseline_settings: lastDispatched,
            base: { candidates: [] }
          })
        };
      }
      if (pathUrl === '/api/job') {
        jobCalls.push({});
        const fixture = { state: 'complete', progress: 'Reports ready' };
        if (autoResolveJob) {
          return { ok: true, json: async () => fixture };
        }
        return new Promise((resolve, reject) => {
          pendingJobCalls.push({
            resolve: val => resolve({ ok: true, json: async () => val || fixture }),
            reject: err => reject(err)
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
    optimizeCalls,
    studyCalls,
    jobCalls,
    pendingOptimizeCalls,
    pendingJobCalls,
    get autoResolveOptimize() { return autoResolveOptimize; },
    set autoResolveOptimize(v) { autoResolveOptimize = v; },
    get autoResolveJob() { return autoResolveJob; },
    set autoResolveJob(v) { autoResolveJob = v; },
    get studyPostHandler() { return studyPostHandler; },
    set studyPostHandler(v) { studyPostHandler = v; },
    get studyGetHandler() { return studyGetHandler; },
    set studyGetHandler(v) { studyGetHandler = v; },
    flushTimers,
    consoleWarnings,
    run: code => vm.runInContext(code, ctx)
  };
}

function initDefaultState(app) {
  const { run, elements } = app;
  run(`
    schema = {
      defaults: {
        load_scenario: 'electrified',
        planning_heat_pump: false,
        heating_schedule_mode: 'scheduled',
        ac_heating_windows: [
          { id: 'morning', start_minute: 360, end_minute: 480 },
          { id: 'evening', start_minute: 960, end_minute: 1200 }
        ],
        ac_heating_additional_demand_pct: 12.5,
        south_panels: 14,
        west_panels: 6,
        east_panels: 0,
        dormer_panels: 0,
        flat_panels: 0,
        secondary_south_panels: 0,
        panel_watts: 400.0,
        battery_kwh: 10.0,
        battery_modules: 2,
        battery_module_kwh: 5.0,
        battery_mixed_sizes: false,
        battery_secondary_modules: 0,
        inverter_kw: 6.0,
        charge_kw: 3.0,
        discharge_kw: 3.0
      },
      records: [
        { name: 'load_scenario', unit: 'scenario' },
        { name: 'planning_heat_pump', unit: 'boolean' },
        { name: 'heating_schedule_mode', unit: 'mode' },
        { name: 'ac_heating_windows', unit: 'windows' },
        { name: 'ac_heating_additional_demand_pct', unit: '%' },
        { name: 'south_panels', unit: 'panels' },
        { name: 'west_panels', unit: 'panels' },
        { name: 'battery_kwh', unit: 'kWh' },
        { name: 'battery_modules', unit: 'modules' },
        { name: 'battery_module_kwh', unit: 'kWh' },
        { name: 'panel_watts', unit: 'W' },
        { name: 'inverter_kw', unit: 'kW' }
      ]
    };
    settings = deepClone(schema.defaults);
    quote = { line_items: [] };
    discardAcDrafts();
    initializeOptimizer();
  `);
  elements.get('error').textContent = '';
  elements.get('error').hidden = true;
}

test('1. Production loadCandidates dispatch submits canonical windows at minute precision and exact percentage with no extra multiplier or cap', async () => {
  const app = createOptimizerHarness();
  const { run, optimizeCalls } = app;
  initDefaultState(app);

  run(`
    settings.ac_heating_windows = [
      { id: 'morning', start_minute: 450, end_minute: 570 },
      { id: 'evening', start_minute: 1050, end_minute: 1290 }
    ];
    settings.ac_heating_additional_demand_pct = 23.45;
  `);

  await run('loadCandidates()');

  assert.equal(optimizeCalls.length, 1, 'dispatches optimize-configurations request');
  const req = optimizeCalls[0];
  assert.equal(req.settings.load_scenario, 'electrified');
  assert.equal(req.settings.ac_heating_additional_demand_pct, 23.45, 'preserves exact percentage without 0.01 multiplier or cap');
  assert.deepEqual(req.settings.ac_heating_windows, [
    { id: 'morning', start_minute: 450, end_minute: 570 },
    { id: 'evening', start_minute: 1050, end_minute: 1290 }
  ], 'canonical windows preserved with minute precision');
  assert.equal(req.settings.south_panels, 0, 'panel counts cleared for search');
  assert.equal(req.settings.battery_modules, 0, 'battery modules cleared for search');
});

test('2. Invalid same-canonical drafts: invalid time draft blocks loadCandidates dispatch and retains visibly outdated result with disabled apply paths', async () => {
  const app = createOptimizerHarness();
  const { run, elements, optimizeCalls } = app;
  initDefaultState(app);

  // 1. Run successful initial search
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 1);
  assert.ok(run('optimizerResult'), 'optimizer result is populated');
  assert.match(elements.get('bestConfiguration').innerHTML, /Apply this configuration/);

  // 2. User types invalid draft: '99:00'
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  // Canonical in settings is still valid:
  assert.equal(run('settings.ac_heating_windows[0].start_minute'), 360);

  // 3. Attempting to load candidates while invalid draft governs is BLOCKED
  optimizeCalls.length = 0;
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 0, 'dispatch is blocked by invalid draft');
  assert.match(elements.get('paretoMethod').textContent, /Cannot search configurations with invalid heating draft/);

  // 4. Existing result is retained visibly outdated with disabled apply button
  assert.ok(run('optimizerResult'), 'visibly outdated result is retained in memory');
  assert.ok(elements.get('bestConfiguration').classList.contains('stale'), 'card marked stale');
  assert.match(elements.get('bestConfiguration').innerHTML, /disabled/, 'apply button is disabled');

  // 5. Clicking or calling apply does NOT apply
  const originalSouth = run('settings.south_panels');
  run(`
    applyOptimizedSettings(optimizerResult.best_candidate);
  `);
  assert.equal(run('settings.south_panels'), originalSouth, 'stale apply path is disabled');
});

test('3. Scoping: baseline search allows dispatch despite invalid AC draft; electrified search blocks dispatch', async () => {
  const app = createOptimizerHarness();
  const { run, elements, optimizeCalls } = app;
  initDefaultState(app);

  // Enter invalid draft
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);

  // Case A: Settings is electrified, but optimizer filterLoad is set to 'baseline'
  // Baseline household does NOT use AC heating -> invalid AC draft does not govern baseline search!
  elements.get('filterLoad').value = 'baseline';
  run('optimizerPrefs.load_scenario = "baseline";');
  optimizeCalls.length = 0;
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 1, 'baseline search dispatches despite invalid AC draft');
  assert.equal(optimizeCalls[0].settings.load_scenario, 'baseline');

  // Case B: Settings is baseline, but optimizer filterLoad is set to 'electrified'
  // Electrified household DOES use AC heating -> invalid AC draft DOES govern electrified search!
  run(`
    settings.load_scenario = 'baseline';
    optimizerPrefs.load_scenario = 'electrified';
  `);
  elements.get('filterLoad').value = 'electrified';
  optimizeCalls.length = 0;
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 0, 'electrified search is blocked by invalid AC draft');
  assert.match(elements.get('paretoMethod').textContent, /Cannot search configurations with invalid heating draft/);
});

test('4. Scoping: HP active and continuous mode prevent AC draft from governing active heating', async () => {
  const app = createOptimizerHarness();
  const { run, elements, optimizeCalls } = app;
  initDefaultState(app);

  // Both schedule draft and demand pct draft invalid
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
    handleAcHeatingDemandPctInput('-10');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  assert.equal(run('hasInvalidAcDemandPctDraft()'), true);

  // Heat pump active -> AC drafts do not govern active heating
  run(`
    settings.planning_heat_pump = true;
  `);
  optimizeCalls.length = 0;
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 1, 'heat pump active allows dispatch despite AC drafts');

  // Heat pump inactive, continuous mode -> schedule draft does not govern active heating
  run(`
    settings.planning_heat_pump = false;
    settings.heating_schedule_mode = 'continuous';
    handleAcHeatingDemandPctInput('15'); // demand pct valid
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  assert.equal(run('hasInvalidAcDemandPctDraft()'), false);
  optimizeCalls.length = 0;
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 1, 'continuous mode allows dispatch despite schedule draft');
});

test('5. Same-value correction: correcting invalid draft back to valid canonical value unblocks optimizer dispatch', async () => {
  const app = createOptimizerHarness();
  const { run, elements, optimizeCalls } = app;
  initDefaultState(app);

  // Enter invalid draft
  run(`
    handleAcScheduleTimeInput('morning', 'start', 'invalid');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);
  optimizeCalls.length = 0;
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 0, 'blocked while draft is invalid');

  // Correct back to 06:00 (same canonical value 360m)
  run(`
    handleAcScheduleTimeInput('morning', 'start', '06:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'draft error cleared');

  // Dispatch is now allowed
  await run('loadCandidates()');
  assert.equal(optimizeCalls.length, 1, 'unblocked after same-value correction');
});

test('6. Valid options change: changing objective or equipment marks optimizer results stale and disables apply paths', async () => {
  const app = createOptimizerHarness();
  const { run, elements } = app;
  initDefaultState(app);

  await run('loadCandidates()');
  assert.ok(run('optimizerResult'));

  // Objective change clears results and preferences per test_optimizer_ui contract
  elements.get('optObjective').value = 'self_consumption';
  run('optimizerPrefs.objective = "self_consumption"; optimizerChanged();');
  assert.equal(run('optimizerResult'), null, 'objective change invalidates prior results');

  // Re-run search
  await run('loadCandidates()');
  assert.ok(run('optimizerResult'));

  // Edit equipment in left settings panel (e.g. panel_watts)
  run(`
    editSetting('panel_watts', 450);
  `);
  assert.ok(run('optimizerResult'), 'visibly outdated result retained');
  assert.ok(run('optimizerStale'), 'optimizer marked stale');

  // Stale apply fails
  const oldSettings = deepClone(run('settings'));
  run(`
    applyOptimizedSettings(optimizerResult.best_candidate);
  `);
  assert.deepEqual(run('settings'), oldSettings, 'stale apply rejected');
});

test('7. Stale success and error races: obsolete responses from earlier requests or edits are rejected', async () => {
  const app = createOptimizerHarness();
  const { run, elements, pendingOptimizeCalls } = app;
  initDefaultState(app);
  app.autoResolveOptimize = false;

  // 1. Launch Request A
  const reqAPromise = run('loadCandidates()');
  assert.equal(pendingOptimizeCalls.length, 1);
  const reqA = pendingOptimizeCalls[0];

  // While Request A is in flight, user introduces an invalid AC draft!
  run(`
    handleAcScheduleTimeInput('morning', 'start', '25:00');
  `);

  // Request A completes with success
  reqA.resolve(makeRealisticCandidatesResult('electrified'));
  await reqAPromise;

  // Since draft became invalid, Request A success MUST BE REJECTED
  assert.equal(run('optimizerResult'), null, 'obsolete success was rejected');

  // 2. Clear draft and test newer request race
  run(`
    handleAcScheduleTimeInput('morning', 'start', '06:00');
  `);
  elements.get('error').textContent = '';
  pendingOptimizeCalls.length = 0;

  // Launch Request 1
  const req1Promise = run('loadCandidates()');
  assert.equal(pendingOptimizeCalls.length, 1);
  const req1 = pendingOptimizeCalls[0];

  // Launch Request 2 (overrides Request 1 ID)
  const req2Promise = run('loadCandidates()');
  assert.equal(pendingOptimizeCalls.length, 2);
  const req2 = pendingOptimizeCalls[1];

  // Request 1 fails with network error
  req1.reject(new Error('Network drop on Request 1'));
  await req1Promise;

  // Obsolete error from Request 1 MUST NOT show in UI
  assert.equal(elements.get('error').textContent, '', 'obsolete error from Request 1 is rejected');

  // Request 2 completes successfully
  const customResult = makeRealisticCandidatesResult('electrified');
  customResult.recommended_id = 'winner_from_req2';
  req2.resolve(customResult);
  await req2Promise;

  // Request 2 result IS accepted
  assert.ok(run('optimizerResult'));
  assert.equal(run('optimizerResult.recommended_id'), 'winner_from_req2', 'newest request result accepted');
});

test('8. Remote sync race: remote settings update while optimizer request in flight rejects obsolete response', async () => {
  const app = createOptimizerHarness();
  const { run, pendingOptimizeCalls } = app;
  initDefaultState(app);
  app.autoResolveOptimize = false;

  const reqPromise = run('loadCandidates()');
  assert.equal(pendingOptimizeCalls.length, 1);
  const req = pendingOptimizeCalls[0];

  // Remote patch arrives with different ac_heating_windows
  run(`
    settings.ac_heating_windows = [
      { id: 'morning', start_minute: 400, end_minute: 500 }
    ];
    schedule();
  `);

  // Deferred request completes
  req.resolve(makeRealisticCandidatesResult('electrified'));
  await reqPromise;

  // Obsolete success rejected
  assert.equal(run('optimizerResult'), null, 'response outdated by remote sync is rejected');
});

test('9. FullStudy onclick and pollJob context: draft guard, error scoping, and global study settings verification', async () => {
  const app = createOptimizerHarness();
  const { ctx, run, elements, studyCalls, pendingJobCalls } = app;
  initDefaultState(app);
  app.autoResolveJob = false;

  // Case A: FullStudy with invalid AC draft is BLOCKED
  run(`
    handleAcScheduleTimeInput('morning', 'start', '99:00');
  `);
  assert.equal(run('hasInvalidAcScheduleDraft()'), true);

  await elements.get('fullStudy').onclick();
  assert.equal(studyCalls.length, 0, 'full study dispatch blocked by invalid draft');
  assert.match(elements.get('error').textContent, /Cannot run full study with invalid heating draft/);

  // Clear invalid draft
  run(`
    handleAcScheduleTimeInput('morning', 'start', '06:00');
  `);
  elements.get('error').textContent = '';

  // Case B: POST /api/study failure scoping - catch block correctly references token without ReferenceError
  let rejectStudyPost;
  app.studyPostHandler = () => new Promise((_, rej) => { rejectStudyPost = rej; });
  const failPromise = elements.get('fullStudy').onclick();
  rejectStudyPost(new Error('Network drop on study dispatch'));
  await failPromise;
  assert.match(elements.get('error').textContent, /Network drop on study dispatch/, 'catch block caught study dispatch error cleanly');

  // Case C: Obsolete study dispatch error does not show if newer study started
  elements.get('error').textContent = '';
  let rejectStudy1;
  app.studyPostHandler = () => new Promise((_, rej) => { rejectStudy1 = rej; });
  const study1Promise = elements.get('fullStudy').onclick();

  // Second study click supersedes study 1
  let resolveStudy2;
  app.studyPostHandler = () => new Promise(res => { resolveStudy2 = res; });
  const study2Promise = elements.get('fullStudy').onclick();

  // Study 1 rejects obsoletely
  rejectStudy1(new Error('Obsolete study 1 failure'));
  await study1Promise;
  assert.equal(elements.get('error').textContent, '', 'obsolete study 1 error was rejected');

  // Study 2 resolves and initiates polling
  resolveStudy2({ ok: true, json: async () => ({ state: 'running', progress: 'Starting candidates' }) });
  await study2Promise;
  assert.equal(pendingJobCalls.length, 1, 'pollJob initiated for study 2');

  // Case D: While job is polling, settings change (invalidating study token)
  run(`
    settings.south_panels = 22;
    schedule();
  `);
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  await app.flushTimers();
  assert.equal(run('study'), null, 'late study poll completion rejected after settings change');

  // Case E: Global study context verification: mismatched baseline_settings rejected, matching accepted
  app.studyPostHandler = null; // restore default POST handler
  pendingJobCalls.length = 0;
  run(`
    settings = deepClone(schema.defaults);
    discardAcDrafts();
  `);
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);

  // E1: Backend returns study with mismatched baseline_settings (e.g. from an old baseline scenario run)
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: { ...run('schema.defaults'), load_scenario: 'baseline' },
      base: { candidates: [{ id: 'old_study_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'foreign/mismatched study.json rejected by baseline_settings comparison');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i, 'honest global-job limitation reported on jobState');

  // E2: Direct unit checks on studySettingsMatch contract
  const dispatched = run('studyDispatchedSettings');
  assert.equal(run('studySettingsMatch({}, studyDispatchedSettings)'), false, 'empty baseline rejected');
  assert.equal(run('studySettingsMatch(null, studyDispatchedSettings)'), false, 'missing null baseline rejected');
  assert.equal(run('studySettingsMatch(undefined, studyDispatchedSettings)'), false, 'undefined baseline rejected');
  assert.equal(run('studySettingsMatch(studyDispatchedSettings, {})'), false, 'empty dispatched rejected');

  // E3: Tiny real percentage change (must NOT be masked by arbitrary 1e-6 tolerance)
  const tinyMismatch = { ...dispatched, ac_heating_additional_demand_pct: 12.5000005 };
  ctx.tinyMismatch = tinyMismatch;
  assert.equal(run('studySettingsMatch(tinyMismatch, studyDispatchedSettings)'), false, 'tiny real pct change rejected');

  // E4: Reordered object keys in canonical windows matches accurately
  const reorderedWindows = dispatched.ac_heating_windows.map(w => ({
    start_minute: w.start_minute,
    end_minute: w.end_minute,
    id: w.id
  }));
  const reorderedBaseline = { ...dispatched, ac_heating_windows: reorderedWindows };
  ctx.reorderedBaseline = reorderedBaseline;
  assert.equal(run('studySettingsMatch(reorderedBaseline, studyDispatchedSettings)'), true, 'reordered object keys match cleanly');

  // E5: Real relevant mismatch (load_scenario or window minute)
  const windowMismatch = {
    ...dispatched,
    ac_heating_windows: dispatched.ac_heating_windows.map((w, idx) => idx === 0 ? { ...w, start_minute: 375 } : w)
  };
  ctx.windowMismatch = windowMismatch;
  assert.equal(run('studySettingsMatch(windowMismatch, studyDispatchedSettings)'), false, 'window minute mismatch rejected');

  // E6: pollJob with empty baseline {} is rejected and reports global-job limitation (existing case remains)
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: {},
      base: { candidates: [{ id: 'empty_baseline_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'empty baseline_settings {} rejected by pollJob');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i);

  // E7: pollJob with absent context (study baseline_settings absent or dispatched context absent)
  run('study = null;');
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: null,
      base: { candidates: [{ id: 'absent_baseline_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'null baseline_settings rejected by pollJob');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i);

  // Absent dispatched context in pollJob
  run('study = null;');
  pendingJobCalls.length = 0;
  run('pollJob(studyRequestId, studyFingerprint, null);');
  assert.equal(pendingJobCalls.length, 1);
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: { ...dispatched },
      base: { candidates: [{ id: 'absent_dispatched_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'absent dispatched context rejected by pollJob');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i);

  // E8: pollJob with missing required key in baseline_settings
  run('study = null;');
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);
  const missingKeyBaseline = { ...dispatched };
  delete missingKeyBaseline.ac_heating_windows;
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: missingKeyBaseline,
      base: { candidates: [{ id: 'missing_key_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'missing required key in baseline_settings rejected by pollJob');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i);

  // E9: pollJob with tiny percentage mismatch (must NOT be masked)
  run('study = null;');
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);
  const tinyMismatchBaseline = { ...dispatched, ac_heating_additional_demand_pct: (dispatched.ac_heating_additional_demand_pct || 0) + 0.0000005 };
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: tinyMismatchBaseline,
      base: { candidates: [{ id: 'tiny_mismatch_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'tiny percentage mismatch rejected by pollJob');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i);

  // E10: pollJob with actual window mismatch
  run('study = null;');
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);
  const actualWindowMismatch = {
    ...dispatched,
    ac_heating_windows: dispatched.ac_heating_windows.map((w, idx) => idx === 0 ? { ...w, start_minute: 375 } : w)
  };
  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: actualWindowMismatch,
      base: { candidates: [{ id: 'window_mismatch_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.equal(run('study'), null, 'actual window mismatch rejected by pollJob');
  assert.match(elements.get('jobState').textContent, /mismatched baseline settings|single study job|cannot safely apply/i);

  // E11: pollJob with reordered object keys succeeds and accepts study (not only direct helper)
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);

  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: { ...reorderedBaseline },
      base: { candidates: [{ id: 'current_dispatched_candidate' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.ok(run('study'), 'matching study with reordered keys accepted by pollJob');
  assert.equal(run('study.base.candidates[0].id'), 'current_dispatched_candidate');

  // E12: Keep global single-job limitation honest: no per-client/run ownership; identical baseline old/foreign jobs cannot be distinguished
  pendingJobCalls.length = 0;
  await elements.get('fullStudy').onclick();
  assert.equal(pendingJobCalls.length, 1);

  app.studyGetHandler = () => ({
    ok: true,
    json: async () => ({
      status: 'SCENARIO_PENDING',
      baseline_settings: { ...dispatched }, // identical baseline from older or foreign client run
      base: { candidates: [{ id: 'identical_baseline_foreign_job' }] }
    })
  });
  pendingJobCalls[0].resolve({ state: 'complete', progress: 'Done' });
  for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
  await app.flushTimers();
  assert.ok(run('study'), 'identical baseline old/foreign jobs cannot be distinguished (honest single-job limitation without run/client ownership)');
  assert.equal(run('study.base.candidates[0].id'), 'identical_baseline_foreign_job');
});

