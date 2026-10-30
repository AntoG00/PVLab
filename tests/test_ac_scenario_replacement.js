'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const SharedScenarioSync = require('../feasibility/web/sync.js');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function createScenarioTestHarness(options = {}) {
 const sharedStore = options.stateStore || null;
 const elements = new Map();

 function makeEl(tag = 'div', id = '') {
  const classes = new Set();
  const attrs = new Map();
  const listeners = new Map();
  let focused = false;
  let open = false;
  let returnValue = '';
  const el = {
   tagName: tag.toUpperCase(),
   id: id || '',
   options: [],
   classList: {
    toggle(c, force) {
     if (force === undefined) {
      if (classes.has(c)) classes.delete(c); else classes.add(c);
     } else if (force) classes.add(c); else classes.delete(c);
     return classes.has(c);
    },
    add(c) { classes.add(c); },
    remove(c) { classes.delete(c); },
    contains(c) { return classes.has(c); }
   },
   hidden: false,
   textContent: '',
   innerHTML: '',
   value: '',
   dataset: {},
   style: {},
   files: [],
   get open() { return open; },
   set open(v) { open = Boolean(v); },
   get returnValue() { return returnValue; },
   set returnValue(v) { returnValue = String(v); },
   showModal() { open = true; },
   close() { open = false; this.dispatchEvent({ type: 'close' }); },
   get disabled() { const d = attrs.get('disabled'); return d === true || d === '' || d === 'true' || d === 'disabled'; },
   set disabled(v) { if (v) attrs.set('disabled', true); else attrs.delete('disabled'); },
   querySelector(sel) {
    if (sel.startsWith('#')) return elements.get(sel.slice(1)) || null;
    if (sel === '[data-save-default]') {
     for (const child of elements.values()) { if (child.dataset?.saveDefault) return child; }
    }
    if (sel === '[data-reset-setting]') {
     for (const child of elements.values()) { if (child.dataset?.resetSetting) return child; }
    }
    return null;
   },
   querySelectorAll() { return []; },
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
   closest(sel) {
    if (sel === 'label') return this;
    if (sel === '[data-reset-setting]' && this.dataset?.resetSetting) return this;
    if (sel === '[data-remove-window]' && this.dataset?.removeWindow) return this;
    if (sel === '[data-load-loan]' && this.dataset?.loadLoan !== undefined) return this;
    if (sel === '[data-remove-loan]' && this.dataset?.removeLoan !== undefined) return this;
    return null;
   },
   removeAttribute(k) { attrs.delete(k); },
   setAttribute(k, v) { attrs.set(k, String(v)); },
   getAttribute(k) { return attrs.get(k) ?? null; },
   hasAttribute(k) { return attrs.has(k); },
   focus() { focused = true; if (typeof doc !== 'undefined') doc.activeElement = this; },
   get isFocused() { return focused; },
   set isFocused(v) { focused = v; }
  };
  if (id) elements.set(id, el);
  return el;
 }

 const badge = { textContent: '', className: '' };
 const controlsEl = makeEl('div', 'controls');
 const statusEl = makeEl('div', 'status');
 const errorEl = makeEl('div', 'error');
 errorEl.hidden = true;
 const defaultConfirmEl = makeEl('dialog', 'defaultConfirm');
 const systemSummaryEl = makeEl('div', 'systemSummary');
 const savedEl = makeEl('div', 'saved');
 const loanPivotEl = makeEl('div', 'loanPivot');
 const resetBtn = makeEl('button', 'reset');
 const saveBtn = makeEl('button', 'save');
 const clearSavedBtn = makeEl('button', 'clearSaved');
 const loadFileInput = makeEl('input', 'loadFile');
 const loanSaveStatusEl = makeEl('div', 'loanSaveStatus');
 const loanSaveNameInput = makeEl('input', 'loanSaveName');
 const saveLoanSimulationBtn = makeEl('button', 'saveLoanSimulation');

 const storageMap = new Map();
 const mockLocalStorage = {
  getItem: (k) => storageMap.has(k) ? storageMap.get(k) : null,
  setItem: (k, v) => storageMap.set(k, String(v)),
  removeItem: (k) => storageMap.delete(k),
  clear: () => storageMap.clear()
 };

 const ui = {
  modifiedCountBadge: badge,
  controls: controlsEl,
  status: statusEl,
  error: errorEl,
  defaultConfirm: defaultConfirmEl,
  systemSummary: systemSummaryEl,
  saved: savedEl,
  loanPivot: loanPivotEl,
  reset: resetBtn,
  save: saveBtn,
  clearSaved: clearSavedBtn,
  loadFile: loadFileInput,
  loanSaveStatus: loanSaveStatusEl,
  loanSaveName: loanSaveNameInput,
  saveLoanSimulation: saveLoanSimulationBtn
 };

 const doc = {
  activeElement: null,
  getElementById: (id) => {
   if (id === 'quoteComparisonSheetContent') return null;
   if (ui[id]) return ui[id];
   if (!elements.has(id)) {
    elements.set(id, makeEl('div', id));
   }
   return elements.get(id);
  },
  querySelector: (sel) => {
   if (sel === 'aside' || sel === 'nav button.active') return null;
   if (sel.startsWith('#')) return doc.getElementById(sel.slice(1));
   if (sel.startsWith('[data-setting="')) {
    const m = sel.match(/\[data-setting="([^"]+)"\]/);
    if (m) { for (const el of elements.values()) { if (el.dataset?.setting === m[1]) return el; } }
   }
   if (sel.startsWith('[data-control="')) {
    const m = sel.match(/\[data-control="([^"]+)"\]/);
    if (m) { for (const el of elements.values()) { if (el.dataset?.control === m[1]) return el; } }
   }
   if (sel.includes('.is-invalid') || sel.includes('[aria-invalid="true"]')) {
    for (const el of elements.values()) {
     if (el.classList?.contains?.('is-invalid') || el.getAttribute?.('aria-invalid') === 'true') return el;
    }
   }
   return null;
  },
  querySelectorAll: (sel) => {
   const results = [];
   for (const el of elements.values()) {
    if (sel === '[data-setting],[data-slider]') {
     if (el.dataset?.setting || el.dataset?.slider) results.push(el);
    } else if (sel.startsWith('[data-control="')) {
     const m = sel.match(/\[data-control="([^"]+)"\]/);
     if (m && el.dataset?.control === m[1]) results.push(el);
    }
   }
   return results;
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
  innerHeight: 768,
  prompt: (msg, def) => def || 'Test Scenario'
 };
 win.window = win;
 win.document = doc;
 win.SharedScenarioSync = SharedScenarioSync;

 const evaluateCalls = [];
 const stateCalls = [];
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
  ac_heating_additional_demand_pct: 10,
  south_panels: 12,
  scop: 4.0,
  battery_kwh: 5.0,
  battery_modules: 2,
  battery_module_kwh: 2.5,
  battery_mixed_sizes: false,
  battery_secondary_modules: 0
 };
 const fallbackRecords = [
  { name: 'load_scenario', unit: 'scenario' },
  { name: 'planning_heat_pump', unit: 'boolean' },
  { name: 'heating_schedule_mode', unit: 'mode' },
  { name: 'ac_heating_windows', unit: 'windows' },
  { name: 'ac_heating_additional_demand_pct', unit: '%' },
  { name: 'south_panels', unit: 'panels' },
  { name: 'scop', unit: 'multiplier' },
  { name: 'battery_kwh', unit: 'kWh' },
  { name: 'battery_modules', unit: 'modules' },
  { name: 'battery_module_kwh', unit: 'kWh' },
  { name: 'battery_mixed_sizes', unit: 'boolean' },
  { name: 'battery_secondary_modules', unit: 'modules' }
 ];

 const consoleWarnings = [];
 const ctx = {
  SharedScenarioSync,
  console: {
   ...console,
   warn: (...args) => { consoleWarnings.push(args); }
  },

  window: win,
  document: doc,
  localStorage: mockLocalStorage,
  prompt: (msg, def) => def || 'Test Scenario',
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  fetch: async (urlPath, opts = {}) => {
   if (urlPath === '/api/health') {
    return { ok: false, status: 500, json: async () => ({ error: 'init suppressed' }) };
   }
   if (urlPath === '/api/evaluate') {
    const body = JSON.parse(opts.body);
    evaluateCalls.push(body);
    return {
     ok: true,
     json: async () => ({
      compute_seconds: 0.01,
      result: {
       settings: body.settings,
       layout: { kwp: 6, panel_count: 14 },
       metrics: { npv_eur: { median: 1000 }, autarky: { median: 0.6 }, payback_years: { median: 8 } },
       finance: { capex_eur: 5000 },
       energy: {}
      }
     })
    };
   }
   if (urlPath === '/api/state' || urlPath.startsWith('/api/state?')) {
    const body = opts.body ? JSON.parse(opts.body) : {};
    stateCalls.push(body);
    const store = sharedStore;
    if (store && opts.body && store.failStateSaves > 0) {
     store.failStateSaves--;
     return { ok: false, status: 500, statusText: 'Mock save failure', json: async () => ({ error: 'Mock save failure' }) };
    }
    if (store && opts.body) {
     store.settings = body.replace
      ? Object.assign({}, body.settings || {})
      : Object.assign({}, store.settings || {}, body.settings || {});
     store.quote = body.replace
      ? Object.assign({ line_items: [] }, body.quote || {})
      : Object.assign({ line_items: [] }, store.quote || {}, body.quote || {});
     if (!Array.isArray(store.quote.line_items)) store.quote.line_items = [];
     store.revision = 'rev_' + stateCalls.length;
    }
    const curSettings = store ? store.settings : (ctx.settings || fallbackDefaults);
    const curQuote = store ? store.quote : (ctx.quote || { line_items: [] });
    if (curQuote && !Array.isArray(curQuote.line_items)) curQuote.line_items = [];
    const curDefaults = (store && store.settings) ? JSON.parse(JSON.stringify(store.settings)) : fallbackDefaults;
    const curRecords = (store && store.settings) ? Object.keys(store.settings).map(name => ({ name, unit: 'number' })) : fallbackRecords;
    const unchanged = Boolean(store && urlPath.includes('?revision=') && decodeURIComponent(urlPath.split('revision=')[1]) === store.revision);
    return {
     ok: true,
     json: async () => ({
      ...(unchanged ? { unchanged: true } : {}),
      revision: store ? store.revision : 'initial_0',
      settings: JSON.parse(JSON.stringify(curSettings)),
      quote: JSON.parse(JSON.stringify(curQuote)),
      defaults: curDefaults,
      records: curRecords
     })
    };
   }
   return { ok: true, json: async () => ({}) };
  }
 };

 vm.createContext(ctx);
 vm.runInContext(appSource, ctx);

 function run(code) {
  return vm.runInContext(code, ctx);
 }

 return {
  ctx,
  run,
  doc,
  ui,
  storageMap,
  evaluateCalls,
  stateCalls,
  pendingTimers,
  flushTimers,
  consoleWarnings
 };
}

test('Manager repro 1: invalid AC drafts are discarded on global reset ($("reset").onclick) and calculation proceeds', async () => {
 const harness = createScenarioTestHarness();
 const { run, ui, evaluateCalls, flushTimers } = harness;

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
    ac_heating_additional_demand_pct: 10,
    south_panels: 12,
    battery_kwh: 5,
    battery_modules: 2,
    battery_module_kwh: 2.5
   },
   records: [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' },
    { name: 'south_panels', unit: 'panels' },
    { name: 'battery_kwh', unit: 'kWh' },
    { name: 'battery_modules', unit: 'modules' },
    { name: 'battery_module_kwh', unit: 'kWh' }
   ]
  };
  settings = deepClone(schema.defaults);
  quote = { line_items: [{ description: 'Test Line', quantity: 1, net_eur: 500 }] };
  renderControls();
  renderLoanPivot();
  // Enter invalid draft text into morning window start
  handleAcScheduleTimeInput('morning', 'start', '99:00');
  // Also enter invalid draft text for demand pct
  handleAcHeatingDemandPctInput('-15');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), true, 'schedule draft has error');
 assert.equal(run('hasInvalidAcDemandPctDraft()'), true, 'percentage draft has error');
 assert.equal(run('shouldBlockAcRecompute(settings)'), true, 'recompute is blocked by invalid drafts');

 evaluateCalls.length = 0;

 // Production button trigger: $('reset').onclick()
 ui.reset.onclick();

 // Verify drafts are completely discarded
 assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'schedule draft error cleared after reset');
 assert.equal(run('hasInvalidAcDemandPctDraft()'), false, 'demand pct draft error cleared after reset');
 assert.equal(run('shouldBlockAcRecompute(settings)'), false, 'shouldBlockAcRecompute is now false');

 // Canonical values are restored to schema defaults
 assert.equal(run('settings.heating_schedule_mode'), 'scheduled');
 assert.equal(run('settings.ac_heating_windows[0].id'), 'morning');
 assert.equal(run('settings.ac_heating_windows[0].start_minute'), 360);
 assert.equal(run('settings.ac_heating_windows[0].end_minute'), 480);
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 10);
 assert.equal(run('quote.line_items.length'), 0, 'quote cleared on reset');

 // Evaluates successfully without being blocked
 await flushTimers();
 assert.equal(evaluateCalls.length, 1, 'recompute evaluated after reset');
 const evaluatedSettings = evaluateCalls[0].settings;
 assert.equal(evaluatedSettings.ac_heating_windows[0].start_minute, 360);
 assert.equal(evaluatedSettings.ac_heating_windows[0].end_minute, 480);
});

test('Manager repro 2: prepareScenarioSettings and cleanSettingsForBackend preserve imported legacy schedule over canonical defaults', () => {
 const harness = createScenarioTestHarness();
 const { run } = harness;

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
    ac_heating_additional_demand_pct: 10
   },
   records: [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' }
   ]
  };
 `);

 // Imported legacy payload: morning 08:00-12:00 (480-720) and evening 17:00-21:00 (1020-1260)
 const legacyInput = {
  heating_on_hour: 8,
  heating_off_hour: 12,
  heating_evening_on_hour: 17,
  heating_evening_off_hour: 21
 };

 const prepared = run(`prepareScenarioSettings(${JSON.stringify(legacyInput)}, false)`);
 const cleaned = run(`cleanSettingsForBackend(${JSON.stringify(prepared)})`);

 // Must retain the imported 08:00-12:00 and 17:00-21:00, NOT the factory canonical 06:00-08:00/16:00-20:00!
 assert.equal(cleaned.ac_heating_windows.length, 2);
 const mWin = cleaned.ac_heating_windows.find(w => w.id === 'morning');
 const eWin = cleaned.ac_heating_windows.find(w => w.id === 'evening');
 assert.ok(mWin, 'morning window present');
 assert.ok(eWin, 'evening window present');
 assert.equal(mWin.start_minute, 480, 'morning start is 08:00 (480m)');
 assert.equal(mWin.end_minute, 720, 'morning end is 12:00 (720m)');
 assert.equal(eWin.start_minute, 1020, 'evening start is 17:00 (1020m)');
 assert.equal(eWin.end_minute, 1260, 'evening end is 21:00 (1260m)');

 // Legacy fields must be completely stripped from output
 for (const k of ['heating_on_hour', 'heating_off_hour', 'heating_evening_on_hour', 'heating_evening_off_hour']) {
  assert.equal(Object.hasOwn(cleaned, k), false, `${k} must be stripped from clean settings`);
 }
});

test('Legacy schedule resolution: canonical precedence, partial lower-layer patch, and extra windows', () => {
 const harness = createScenarioTestHarness();
 const { run } = harness;

 run(`
  schema = {
   defaults: {
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [
     { id: 'morning', start_minute: 360, end_minute: 480 },
     { id: 'midday', start_minute: 720, end_minute: 840 },
     { id: 'evening', start_minute: 960, end_minute: 1200 }
    ],
    ac_heating_additional_demand_pct: 15
   },
   records: [
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' }
   ]
  };
 `);

 // Case 1: Canonical windows take authoritative precedence when both canonical and legacy keys are present
 const dualPayload = {
  ac_heating_windows: [
   { id: 'custom_1', start_minute: 300, end_minute: 500 }
  ],
  heating_on_hour: 8,
  heating_off_hour: 10
 };
 const resolvedDual = run(`prepareScenarioSettings(${JSON.stringify(dualPayload)}, false)`);
 assert.equal(resolvedDual.ac_heating_windows.length, 1);
 assert.equal(resolvedDual.ac_heating_windows[0].id, 'custom_1');
 assert.equal(resolvedDual.ac_heating_windows[0].start_minute, 300);
 assert.equal(Object.hasOwn(resolvedDual, 'heating_on_hour'), false);

 // Case 2: Partial legacy lower-layer patch preserves other base windows and extra windows in order
 const partialLegacy = {
  heating_on_hour: 7 // Only morning start updated
 };
 const resolvedPartial = run(`prepareScenarioSettings(${JSON.stringify(partialLegacy)}, false)`);
 assert.equal(resolvedPartial.ac_heating_windows.length, 3, 'all 3 base windows preserved');
 assert.equal(resolvedPartial.ac_heating_windows[0].id, 'morning');
 assert.equal(resolvedPartial.ac_heating_windows[0].start_minute, 420, 'morning start updated to 07:00 (420m)');
 assert.equal(resolvedPartial.ac_heating_windows[0].end_minute, 480, 'morning end preserved from base default');
 assert.equal(resolvedPartial.ac_heating_windows[1].id, 'midday', 'extra midday window preserved');
 assert.equal(resolvedPartial.ac_heating_windows[1].start_minute, 720);
 assert.equal(resolvedPartial.ac_heating_windows[2].id, 'evening', 'evening window preserved');

 // Case 3: Inactive window (equal start/end) and overnight window (start > end) semantics preserved
 const overnightLegacy = {
  heating_evening_on_hour: 22,
  heating_evening_off_hour: 6
 };
 const resolvedOvernight = run(`prepareScenarioSettings(${JSON.stringify(overnightLegacy)}, false)`);
 const ovWin = resolvedOvernight.ac_heating_windows.find(w => w.id === 'evening');
 assert.equal(ovWin.start_minute, 1320);
 assert.equal(ovWin.end_minute, 360);
});

test('Rejects malformed or ambiguous schedules clearly without mutating state', () => {
 const harness = createScenarioTestHarness();
 const { run } = harness;

 run(`
  schema = {
   defaults: {
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [
     { id: 'morning', start_minute: 360, end_minute: 480 },
     { id: 'evening', start_minute: 960, end_minute: 1200 }
    ]
   },
   records: [{ name: 'ac_heating_windows', unit: 'windows' }]
  };
 `);

 // Out-of-bounds hour (> 23 or < 0)
 assert.throws(() => run(`prepareScenarioSettings({ heating_on_hour: 24 }, false)`), /between 0 and 23/);
 assert.throws(() => run(`prepareScenarioSettings({ heating_on_hour: -1 }, false)`), /between 0 and 23/);

 // Non-integer types strictly rejected (boolean, float, string)
 assert.throws(() => run(`prepareScenarioSettings({ heating_on_hour: true }, false)`), /must be an integer/);
 assert.throws(() => run(`prepareScenarioSettings({ heating_on_hour: 8.5 }, false)`), /must be an integer/);
 assert.throws(() => run(`prepareScenarioSettings({ heating_on_hour: "8" }, false)`), /must be an integer/);

 // Malformed canonical windows
 assert.throws(() => run(`prepareScenarioSettings({ ac_heating_windows: "invalid" }, false)`), /must be an array/);
 assert.throws(() => run(`prepareScenarioSettings({ ac_heating_windows: [{ id: 'w1', start_minute: -1, end_minute: 100 }] }, false)`), /start_minute/);
 assert.throws(() => run(`prepareScenarioSettings({ ac_heating_windows: [{ id: 'w1', start_minute: 0, end_minute: 1500 }] }, false)`), /end_minute/);
 assert.throws(() => run(`prepareScenarioSettings({ ac_heating_windows: [{ id: 'dup', start_minute: 100, end_minute: 200 }, { id: 'dup', start_minute: 300, end_minute: 400 }] }, false)`), /Duplicate window id/);
 assert.throws(() => run(`prepareScenarioSettings({ ac_heating_windows: [{ id: 'w1', start_minute: 100, end_minute: 200, unknown_field: 123 }] }, false)`), /Unknown fields/);

 // Missing / ambiguous window matches on legacy patch
 const baseNoMorning = [{ id: 'window_a', start_minute: 100, end_minute: 200 }];
 assert.throws(() => run(`migrateAndResolveSchedule({ heating_on_hour: 8 }, ${JSON.stringify(baseNoMorning)})`), /'morning' window is missing/);
 const baseDupMorning = [
  { id: 'morning', start_minute: 100, end_minute: 200 },
  { id: 'morning', start_minute: 300, end_minute: 400 }
 ];
 assert.throws(() => run(`migrateAndResolveSchedule({ heating_on_hour: 8 }, ${JSON.stringify(baseDupMorning)})`), /'morning' window is ambiguous/);
});

test('Percentage migration: old replacement export missing pct migrates to 0; partial shared update retains existing value', () => {
 const harness = createScenarioTestHarness();
 const { run } = harness;

 run(`
  schema = {
   defaults: {
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [{ id: 'morning', start_minute: 360, end_minute: 480 }],
    ac_heating_additional_demand_pct: 25 // Non-zero user default
   },
   records: [
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' }
   ]
  };
  settings = deepClone(schema.defaults);
  settings.ac_heating_additional_demand_pct = 18; // Existing resolved setting
 `);

 // 1. Complete replacement export missing pct: must migrate to 0, not inherit the 25% user default
 const fullExportWithoutPct = {
  ac_heating_windows: [{ id: 'morning', start_minute: 400, end_minute: 500 }]
 };
 const resolvedFull = run(`prepareScenarioSettings(${JSON.stringify(fullExportWithoutPct)}, false)`);
 assert.equal(resolvedFull.ac_heating_additional_demand_pct, 0, 'complete replacement missing pct migrates to 0');

 // 2. Partial shared update missing pct: retains existing resolved value (18)
 const partialSharedWithoutPct = {
  south_panels: 15
 };
 const resolvedPartial = run(`prepareScenarioSettings(${JSON.stringify(partialSharedWithoutPct)}, true)`);
 assert.equal(resolvedPartial.ac_heating_additional_demand_pct, 18, 'partial shared update preserves current 18%');
});

test('File import ($("loadFile").onchange): success discards prior drafts; error preserves active scenario and drafts', async () => {
 const harness = createScenarioTestHarness();
 const { run, ui, evaluateCalls, flushTimers } = harness;

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
    ac_heating_additional_demand_pct: 0,
    south_panels: 10
   },
   records: [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' },
    { name: 'south_panels', unit: 'panels' }
   ]
  };
  settings = deepClone(schema.defaults);
  quote = { line_items: [{ description: 'Existing Item', quantity: 1, net_eur: 1000 }] };
  renderControls();
  renderLoanPivot();
  handleAcScheduleTimeInput('morning', 'start', '99:00');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), true);

 // Case A: Malformed import file (e.g. invalid hour in schedule)
 const badFilePayload = JSON.stringify({
  settings: {
   heating_on_hour: 99
  },
  quote: { line_items: [] }
 });
 const badFile = { text: async () => badFilePayload };
 ui.error.hidden = true;

 await ui.loadFile.onchange({ target: { files: [badFile] } });

 // Error must be displayed, and prior scenario, quote, and drafts must remain intact!
 assert.equal(ui.error.hidden, false, 'error message shown to user');
 assert.equal(run('hasInvalidAcScheduleDraft()'), true, 'prior invalid draft preserved on failed import');
 assert.equal(run('settings.south_panels'), 10, 'settings unchanged');
 assert.equal(run('quote.line_items[0].description'), 'Existing Item', 'quote unchanged');

 // Case A2: Malformed import with invalid demand percentage (-10)
 const badPctPayload = JSON.stringify({
  settings: {
   ac_heating_additional_demand_pct: -10
  },
  quote: { line_items: [] }
 });
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => badPctPayload }] } });
 assert.equal(ui.error.hidden, false, 'error on negative demand pct');
 assert.equal(run('hasInvalidAcScheduleDraft()'), true);
 assert.equal(run('settings.south_panels'), 10);

 // Case A3: Malformed import with invalid heating mode
 const badModePayload = JSON.stringify({
  settings: {
   heating_schedule_mode: 'unsupported_mode'
  },
  quote: { line_items: [] }
 });
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => badModePayload }] } });
 assert.equal(ui.error.hidden, false, 'error on invalid mode');
 assert.equal(run('hasInvalidAcScheduleDraft()'), true);
 assert.equal(run('settings.south_panels'), 10);

 // Case A4: Malformed import with non-object settings
 const badSettingsPayload = JSON.stringify({
  settings: 'not-an-object',
  quote: { line_items: [] }
 });
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => badSettingsPayload }] } });
 assert.equal(ui.error.hidden, false, 'error on non-object settings');
 assert.equal(run('hasInvalidAcScheduleDraft()'), true);
 assert.equal(run('settings.south_panels'), 10);

 // Case B: Valid legacy scenario file import
 const goodFilePayload = JSON.stringify({
  settings: {
   heating_on_hour: 8,
   heating_off_hour: 11,
   heating_evening_on_hour: 18,
   heating_evening_off_hour: 22,
   south_panels: 16
  },
  quote: { line_items: [{ description: 'New Panels', quantity: 16, net_eur: 4000 }] }
 });
 const goodFile = { text: async () => goodFilePayload };
 evaluateCalls.length = 0;

 await ui.loadFile.onchange({ target: { files: [goodFile] } });

 // Prior draft discarded
 assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'draft error discarded on successful import');
 assert.equal(run('shouldBlockAcRecompute(settings)'), false);

 // Imported legacy heating mapped to canonical windows
 assert.equal(run('settings.south_panels'), 16);
 assert.equal(run('settings.ac_heating_windows[0].start_minute'), 480);
 assert.equal(run('settings.ac_heating_windows[0].end_minute'), 660);
 assert.equal(run('settings.ac_heating_windows[1].start_minute'), 1080);
 assert.equal(run('settings.ac_heating_windows[1].end_minute'), 1320);
 assert.equal(run('quote.line_items[0].description'), 'New Panels');

 await flushTimers();
 assert.equal(evaluateCalls.length, 1, 'recompute triggered with replaced scenario');
});

test('Browser-saved scenarios: invalid draft blocks saving; restore discards drafts and deep-clones snapshots', async () => {
 const harness = createScenarioTestHarness();
 const { run, ui, storageMap } = harness;

 run(`
  schema = {
   defaults: {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [{ id: 'morning', start_minute: 360, end_minute: 480 }],
    ac_heating_additional_demand_pct: 0
   },
   records: [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' }
   ]
  };
  settings = deepClone(schema.defaults);
  quote = { line_items: [{ description: 'Item 1', quantity: 1, net_eur: 100 }] };
  current = { result: { settings: deepClone(settings), layout: { kwp: 5, panel_count: 10 }, finance: { capex_eur: 5000 }, metrics: {} } };
  saved = [];
  renderControls();
  renderLoanPivot();
  renderSaved();
  handleAcScheduleTimeInput('morning', 'start', '99:00');
 `);

 // 1. Attempting to save scenario while draft is invalid is blocked
 ui.error.hidden = true;
 ui.save.onclick();
 assert.equal(ui.error.hidden, false, 'save scenario blocked while invalid draft governs heating');
 assert.equal(storageMap.has('pv-lab-scenarios-v1'), false, 'no scenario saved with hidden last-valid values');

 // 2. Correct draft and save valid scenario
 run(`
  handleAcScheduleTimeInput('morning', 'start', '06:00');
 `);
 ui.save.onclick();
 assert.equal(storageMap.has('pv-lab-scenarios-v1'), true, 'scenario saved to localStorage');
 const rawSaved = JSON.parse(storageMap.get('pv-lab-scenarios-v1'));
 assert.equal(rawSaved.length, 1);
 assert.equal(rawSaved[0].settings.ac_heating_windows[0].start_minute, 360);

 // 3. Now enter an invalid draft in the active scenario
 run(`
  editSetting('ac_heating_windows', [{ id: 'custom', start_minute: 500, end_minute: 700 }]);
  handleAcScheduleTimeInput('custom', 'start', '99:00');
 `);
 assert.equal(run('hasInvalidAcScheduleDraft()'), true);

 // 4. Click restore on the saved scenario card
 ui.saved.onclick({ target: { dataset: { restore: '0' } } });

 // Restored scenario discards the invalid draft
 assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'invalid draft discarded on saved scenario restore');
 assert.equal(run('settings.ac_heating_windows[0].id'), 'morning');
 assert.equal(run('settings.ac_heating_windows[0].start_minute'), 360);

 // 5. Later edits to active settings/quote do NOT mutate the stored snapshot
 run(`
  settings.ac_heating_windows[0].start_minute = 123;
  quote.line_items.push({ description: 'Mutated Item', quantity: 2, net_eur: 200 });
 `);
 const rawSavedAfterEdit = JSON.parse(storageMap.get('pv-lab-scenarios-v1'));
 assert.equal(rawSavedAfterEdit[0].settings.ac_heating_windows[0].start_minute, 360, 'stored snapshot unmutated');
 assert.equal(rawSavedAfterEdit[0].quote.line_items.length, 1, 'stored quote unmutated');
});

test('Loan saved simulations: restore discards drafts and isolates snapshots from subsequent mutations', () => {
 const harness = createScenarioTestHarness();
 const { run, ui } = harness;

 run(`
  schema = {
   defaults: {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [{ id: 'morning', start_minute: 360, end_minute: 480 }],
    ac_heating_additional_demand_pct: 0
   },
   records: [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' }
   ]
  };
  settings = deepClone(schema.defaults);
  quote = { line_items: [{ description: 'Solar Base', quantity: 1, net_eur: 3000 }] };
  current = {
   result: {
    settings: deepClone(settings),
    layout: { kwp: 4 },
    finance: {
     capex_eur: 3000,
     cashflows_by_weather: {
      equity: [[-3000, 300, 300]]
     }
    }
   }
  };
  loanSaved = [{
   id: 'loan_1',
   name: 'Simulation 1',
   settings: {
    load_scenario: 'electrified',
    ac_heating_windows: [{ id: 'morning', start_minute: 420, end_minute: 540 }],
    ac_heating_additional_demand_pct: 5
   },
   quote: { line_items: [{ description: 'Loan PV', quantity: 10, net_eur: 2500 }] },
   result: {
    settings: { load_scenario: 'electrified' },
    layout: { kwp: 4 },
    finance: {
     capex_eur: 2500,
     cashflows_by_weather: {
      equity: [[-2500, 250, 250]]
     }
    }
   }
  }];
  renderControls();
  renderLoanPivot();
  // Active state has invalid draft
  handleAcScheduleTimeInput('morning', 'start', '99:00');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), true);

 // Restore loan simulation via loanPivot load button
 const loadTarget = {
  closest: (sel) => sel === '[data-load-loan]' ? { dataset: { loadLoan: '0' } } : null
 };
 ui.loanPivot.onclick({ target: loadTarget });

 // Draft discarded, restored simulation applied
 assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'draft discarded on loan restore');
 assert.equal(run('settings.ac_heating_windows[0].start_minute'), 420);
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 5);
 assert.equal(run('quote.line_items[0].description'), 'Loan PV');

 // Subsequent edits do not mutate the loanSaved snapshot
 run(`
  settings.ac_heating_windows[0].start_minute = 100;
  quote.line_items[0].description = 'Tampered PV';
 `);
 assert.equal(run('loanSaved[0].settings.ac_heating_windows[0].start_minute'), 420, 'loanSaved settings snapshot isolated');
 assert.equal(run('loanSaved[0].quote.line_items[0].description'), 'Loan PV', 'loanSaved quote snapshot isolated');
});

test('Remote sync is NOT explicit replacement: unrelated remote patches retain local invalid drafts and dirty canonical edits', async () => {
 const store = {
  revision: 'rev_0',
  failStateSaves: 1,
  settings: {
   load_scenario: 'electrified',
   planning_heat_pump: false,
   heating_schedule_mode: 'scheduled',
   ac_heating_windows: [
    { id: 'morning', start_minute: 360, end_minute: 480 },
    { id: 'evening', start_minute: 960, end_minute: 1200 }
   ],
   ac_heating_additional_demand_pct: 10,
   south_panels: 10,
   scop: 4.0
  },
  quote: { line_items: [] }
 };

 const harness = createScenarioTestHarness({ stateStore: store });
 const { run } = harness;

 run(`
  schema = { defaults: deepClone(${JSON.stringify(store.settings)}), records: Object.keys(${JSON.stringify(store.settings)}).map(name => ({ name, unit: 'number' })) };
  settings = deepClone(schema.defaults);
  quote = { line_items: [] };
  sharedSync = new SharedScenarioSync({ request: api, onState: applySharedState });
  sharedSync.revision = 'rev_0';
  sharedSync.settings = deepClone(settings);
  sharedSync.quote = deepClone(quote);
  sharedUiReady = true;
  renderControls();
  renderLoanPivot();

  // Local user enters an invalid draft in morning window
  handleAcScheduleTimeInput('morning', 'start', '99:00');
  // Local user also makes a canonical dirty edit on south_panels
  editSetting('south_panels', 14);
 `);
 await run('sharedSync.saving.catch(()=>{})');

 assert.equal(run('hasInvalidAcScheduleDraft()'), true);
 assert.equal(run('sharedSync.dirtySettings.has("south_panels")'), true);

 // Unrelated remote change: remote user updates scop from 4.0 to 4.9
 store.settings.scop = 4.9;
 store.revision = 'rev_1';

 // Local client receives remote state via pollSharedState()
 await run('pollSharedState()');

 // Local invalid draft is preserved
 assert.equal(run('hasInvalidAcScheduleDraft()'), true, 'local invalid draft retained after unrelated remote patch');
 assert.equal(run('getAcScheduleDraft()[0].startText'), '99:00', 'raw draft text preserved');

 // Local dirty canonical edit is preserved
 assert.equal(run('settings.south_panels'), 14, 'local dirty edit preserved');
 assert.equal(store.settings.south_panels, 14, 'durable store updated after retry save');
 assert.equal(run('sharedSync.dirtySettings.has("south_panels")'), false, 'dirty cleared on successful ack');

 // Unrelated remote edit is merged into settings
 assert.equal(run('settings.scop'), 4.9, 'remote scop change accepted');

 // Retained dirty on deliberately failed retry
 store.failStateSaves = 1;
 run("editSetting('south_panels', 16)");
 await run('sharedSync.saving.catch(()=>{})');
 assert.equal(run('sharedSync.dirtySettings.has("south_panels")'), true);
 store.failStateSaves = 1;
 store.settings.scop = 5.0;
 store.revision = 'rev_2';
 await run('pollSharedState()');
 assert.equal(run('sharedSync.dirtySettings.has("south_panels")'), true, 'dirty retained when retry fails');
 assert.equal(run('settings.south_panels'), 16, 'local unpersisted value retained');
 assert.equal(run('settings.scop'), 5.0, 'remote edit merged');
 assert.equal(run('hasInvalidAcScheduleDraft()'), true, 'draft error remains retained');
});

test('Remote sync with schedule changes: draft ID associations prevent raw draft from overwriting newer remote canonical windows', async () => {
 const initialWindows = [
  { id: 'morning', start_minute: 360, end_minute: 480 },
  { id: 'evening', start_minute: 960, end_minute: 1200 }
 ];
 const store = {
  revision: 'rev_0',
  settings: {
   load_scenario: 'electrified',
   planning_heat_pump: false,
   heating_schedule_mode: 'scheduled',
   ac_heating_windows: initialWindows,
   ac_heating_additional_demand_pct: 10
  },
  quote: { line_items: [] }
 };

 const harness = createScenarioTestHarness({ stateStore: store });
 const { run, evaluateCalls, flushTimers } = harness;

 run(`
  schema = { defaults: deepClone(${JSON.stringify(store.settings)}), records: Object.keys(${JSON.stringify(store.settings)}).map(name => ({ name, unit: 'number' })) };
  settings = deepClone(schema.defaults);
  quote = { line_items: [] };
  sharedSync = new SharedScenarioSync({ request: api, onState: applySharedState });
  sharedSync.revision = 'rev_0';
  sharedSync.settings = deepClone(settings);
  sharedSync.quote = deepClone(quote);
  sharedUiReady = true;
  renderControls();
  renderLoanPivot();

  // Local user enters invalid time on morning window: '99:00'
  handleAcScheduleTimeInput('morning', 'start', '99:00');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), true);

 // Remote user updates morning window to 06:00-09:00 (360-540), evening window to 17:00-21:00 (1020-1260), adds 'night' (1320-1380)
 const updatedRemoteWindows = [
  { id: 'morning', start_minute: 360, end_minute: 540 },
  { id: 'evening', start_minute: 1020, end_minute: 1260 },
  { id: 'night', start_minute: 1320, end_minute: 1380 }
 ];
 store.settings.ac_heating_windows = updatedRemoteWindows;
 store.revision = 'rev_1';

 await run('pollSharedState()');

 // Local invalid draft on morning is still retained so user does not lose input
 assert.equal(run('hasInvalidAcScheduleDraft()'), true);
 assert.equal(run('getAcScheduleDraft()[0].startText'), '99:00');

 // Draft rows reflect newer remote canonical windows; same-window invalid start kept while remote valid end updated!
 const draft = run('getAcScheduleDraft()');
 const morningDraft = draft.find(r => r.id === 'morning');
 const eveningDraft = draft.find(r => r.id === 'evening');
 const nightDraft = draft.find(r => r.id === 'night');
 assert.ok(morningDraft, 'morning window in draft');
 assert.ok(eveningDraft, 'evening window in draft');
 assert.ok(nightDraft, 'night window in draft');
 assert.equal(morningDraft.startText, '99:00', 'local invalid start preserved');
 assert.equal(morningDraft.endText, '09:00', 'remote valid end updated in same window');
 assert.equal(eveningDraft.startText, '17:00', 'evening updated to remote 17:00');
 assert.equal(eveningDraft.endText, '21:00', 'evening updated to remote 21:00');
 assert.equal(nightDraft.startText, '22:00', 'night added from remote');

 // User corrects morning draft to '07:00'
 evaluateCalls.length = 0;
 run(`
  handleAcScheduleTimeInput('morning', 'start', '07:00');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'draft error corrected');

 // Canonical array committed retains morning 07:00 AND remote morning end 09:00 AND remote evening 17:00-21:00 AND remote night 22:00-23:00!
 const committed = run('settings.ac_heating_windows');
 assert.equal(committed.length, 3);
 const m = committed.find(w => w.id === 'morning');
 const e = committed.find(w => w.id === 'evening');
 const n = committed.find(w => w.id === 'night');
 assert.equal(m.start_minute, 420, 'morning committed with user edit');
 assert.equal(m.end_minute, 540, 'morning retains remote end update on same window');
 assert.equal(e.start_minute, 1020, 'evening NOT overwritten with old 16:00 (960m); retains remote 17:00');
 assert.equal(e.end_minute, 1260, 'evening retains remote 21:00');
 assert.equal(n.start_minute, 1320, 'night window from remote preserved');

 await flushTimers();
 assert.equal(evaluateCalls.length, 1);
});

test('Remote schedule window removal: draft ID association removes deleted window and does not resurrect it on edit', async () => {
 const initialWindows = [
  { id: 'morning', start_minute: 360, end_minute: 480 },
  { id: 'evening', start_minute: 960, end_minute: 1200 }
 ];
 const store = {
  revision: 'rev_0',
  settings: {
   load_scenario: 'electrified',
   planning_heat_pump: false,
   heating_schedule_mode: 'scheduled',
   ac_heating_windows: initialWindows,
   ac_heating_additional_demand_pct: 10
  },
  quote: { line_items: [] }
 };

 const harness = createScenarioTestHarness({ stateStore: store });
 const { run } = harness;

 run(`
  schema = { defaults: deepClone(${JSON.stringify(store.settings)}), records: Object.keys(${JSON.stringify(store.settings)}).map(name => ({ name, unit: 'number' })) };
  settings = deepClone(schema.defaults);
  quote = { line_items: [] };
  sharedSync = new SharedScenarioSync({ request: api, onState: applySharedState });
  sharedSync.revision = 'rev_0';
  sharedSync.settings = deepClone(settings);
  sharedSync.quote = deepClone(quote);
  sharedUiReady = true;
  renderControls();
  renderLoanPivot();

  // Local user enters invalid time on morning window: '99:00'
  handleAcScheduleTimeInput('morning', 'start', '99:00');
 `);

 // Remote user deletes the evening window completely!
 store.settings.ac_heating_windows = [
  { id: 'morning', start_minute: 360, end_minute: 480 }
 ];
 store.revision = 'rev_1';

 await run('pollSharedState()');

 // Draft should have dropped the evening window
 const draftAfterRemoteDelete = run('getAcScheduleDraft()');
 assert.equal(draftAfterRemoteDelete.length, 1);
 assert.equal(draftAfterRemoteDelete[0].id, 'morning');

 // When user corrects morning draft, evening window is NOT resurrected
 run(`
  handleAcScheduleTimeInput('morning', 'start', '06:30');
 `);
 const committed = run('settings.ac_heating_windows');
 assert.equal(committed.length, 1, 'evening window was not resurrected');
 assert.equal(committed[0].id, 'morning');
 assert.equal(committed[0].start_minute, 390);
});

test('Legacy schedule conflict check: warns on conflicting legacy hours vs canonical windows, silent when consistent, preserving canonical precedence', () => {
 const harness = createScenarioTestHarness();
 const { run, consoleWarnings } = harness;

 run(`
  schema = {
   defaults: {
    heating_schedule_mode: 'scheduled',
    ac_heating_windows: [
     { id: 'morning', start_minute: 360, end_minute: 480 },
     { id: 'evening', start_minute: 960, end_minute: 1200 }
    ],
    ac_heating_additional_demand_pct: 0
   },
   records: [
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' }
   ]
  };
  settings = deepClone(schema.defaults);
 `);

 // Case 1: Conflicting payload (canonical is 06:00-08:00, legacy is 08:00-11:00)
 const conflictingPayload = {
  ac_heating_windows: [
   { id: 'morning', start_minute: 360, end_minute: 480 },
   { id: 'evening', start_minute: 960, end_minute: 1200 }
  ],
  heating_on_hour: 8,
  heating_off_hour: 11
 };
 consoleWarnings.length = 0;
 const resolvedConflicting = run(`prepareScenarioSettings(${JSON.stringify(conflictingPayload)}, false)`);
 assert.equal(resolvedConflicting.ac_heating_windows[0].start_minute, 360, 'canonical windows take precedence');
 assert.equal(resolvedConflicting.ac_heating_windows[0].end_minute, 480);
 assert.equal(Object.hasOwn(resolvedConflicting, 'heating_on_hour'), false, 'legacy keys stripped');
 assert.equal(consoleWarnings.length, 1, 'warning logged on conflict');
 assert.match(consoleWarnings[0][0], /conflicting legacy heating hours; canonical windows take precedence/);

 // Case 2: Consistent payload (canonical is 08:00-11:00, 18:00-22:00, legacy hours match exactly)
 const consistentPayload = {
  ac_heating_windows: [
   { id: 'morning', start_minute: 480, end_minute: 660 },
   { id: 'evening', start_minute: 1080, end_minute: 1320 }
  ],
  heating_on_hour: 8,
  heating_off_hour: 11,
  heating_evening_on_hour: 18,
  heating_evening_off_hour: 22
 };
 consoleWarnings.length = 0;
 const resolvedConsistent = run(`prepareScenarioSettings(${JSON.stringify(consistentPayload)}, false)`);
 assert.equal(resolvedConsistent.ac_heating_windows[0].start_minute, 480);
 assert.equal(resolvedConsistent.ac_heating_windows[0].end_minute, 660);
 assert.equal(resolvedConsistent.ac_heating_windows[1].start_minute, 1080);
 assert.equal(resolvedConsistent.ac_heating_windows[1].end_minute, 1320);
 assert.equal(consoleWarnings.length, 0, 'no warning when legacy and canonical are consistent');
});

test('File import ($("loadFile").onchange): strictly rejects malformed numeric types for ac_heating_additional_demand_pct without coercion', async () => {
 const harness = createScenarioTestHarness();
 const { run, ui } = harness;

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
    ac_heating_additional_demand_pct: 10,
    south_panels: 12
   },
   records: [
    { name: 'load_scenario', unit: 'scenario' },
    { name: 'planning_heat_pump', unit: 'boolean' },
    { name: 'heating_schedule_mode', unit: 'mode' },
    { name: 'ac_heating_windows', unit: 'windows' },
    { name: 'ac_heating_additional_demand_pct', unit: '%' },
    { name: 'south_panels', unit: 'panels' }
   ]
  };
  settings = deepClone(schema.defaults);
  quote = { line_items: [{ description: 'Existing Panel', quantity: 1, net_eur: 500 }] };
  renderControls();
  renderLoanPivot();
  handleAcScheduleTimeInput('morning', 'start', '99:00');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), true);

 // 1. Reject numeric string "25" (no coercion)
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => JSON.stringify({ settings: { ac_heating_additional_demand_pct: "25" }, quote: { line_items: [] } }) }] } });
 assert.equal(ui.error.hidden, false, 'error on string demand pct');
 assert.match(ui.error.textContent, /must be a non-negative finite number/);
 assert.equal(run('hasInvalidAcScheduleDraft()'), true, 'drafts preserved on failed import');
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 10, 'settings preserved');
 assert.equal(run('quote.line_items[0].description'), 'Existing Panel', 'quote preserved');

 // 2. Reject boolean true (no coercion to 1)
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => JSON.stringify({ settings: { ac_heating_additional_demand_pct: true }, quote: { line_items: [] } }) }] } });
 assert.equal(ui.error.hidden, false, 'error on boolean true');
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 10);

 // 3. Reject boolean false (no coercion to 0)
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => JSON.stringify({ settings: { ac_heating_additional_demand_pct: false }, quote: { line_items: [] } }) }] } });
 assert.equal(ui.error.hidden, false, 'error on boolean false');
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 10);

 // 4. Reject null
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => JSON.stringify({ settings: { ac_heating_additional_demand_pct: null }, quote: { line_items: [] } }) }] } });
 assert.equal(ui.error.hidden, false, 'error on null');
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 10);

 // 5. Reject negative numeric -5
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => JSON.stringify({ settings: { ac_heating_additional_demand_pct: -5 }, quote: { line_items: [] } }) }] } });
 assert.equal(ui.error.hidden, false, 'error on negative number');
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 10);

 // 6. Accept valid finite non-negative number 18.5
 ui.error.hidden = true;
 await ui.loadFile.onchange({ target: { files: [{ text: async () => JSON.stringify({ settings: { ac_heating_additional_demand_pct: 18.5, south_panels: 15 }, quote: { line_items: [] } }) }] } });
 assert.equal(run('hasInvalidAcScheduleDraft()'), false, 'draft discarded on valid import');
 assert.equal(run('settings.ac_heating_additional_demand_pct'), 18.5, 'valid number applied');
 assert.equal(typeof run('settings.ac_heating_additional_demand_pct'), 'number');
 assert.equal(run('settings.south_panels'), 15);
});

module.exports = { createScenarioTestHarness };
