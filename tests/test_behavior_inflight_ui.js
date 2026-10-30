/* Run with Node: test in-flight behavior request coalescing and race conditions. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const fnStart = appSource.indexOf('async function refreshBehaviorViews()');
const fnEnd = appSource.indexOf('function drawTypicalDayChart(', fnStart);
assert(fnStart !== -1 && fnEnd !== -1, 'Found refreshBehaviorViews slice');
const fnSlice = appSource.slice(fnStart, fnEnd);
const pendingDeclaration = appSource.match(/^const pendingBehaviorRequests=new Map\(\);$/m)?.[0];
assert(pendingDeclaration, 'Found actual pending request map declaration');

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function createHarness() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) {
      elements.set(id, { id, hidden: false, value: '', innerHTML: '', textContent: '', disabled: false });
    }
    return elements.get(id);
  };
  const buttons = [];
  const querySelectorAll = sel => (sel === '[data-behavior-scen]' ? buttons : []);

  const renders = {
    annualFlow: 0,
    heatPump: 0,
    acHeating: 0,
    typicalDay: 0,
    monthHeatmap: 0,
    switchOptimizer: 0,
    pending: 0,
    errors: []
  };

  const apiCalls = [];
  let apiHandler = null;

  const ctx = {
    $,
    document: { querySelectorAll },
    inputFingerprint: (s, q) => s._fp || 'fp_default',
    acceptedInputFingerprint: 'fp_default',
    deepClone: obj => JSON.parse(JSON.stringify(obj)),
    renderAnnualEnergyFlow: () => { renders.annualFlow++; },
    renderHeatPumpReport: () => { renders.heatPump++; },
    renderAcHeatingComparison: () => { renders.acHeating++; },
    drawTypicalDayChart: () => { renders.typicalDay++; },
    drawMonthHourHeatmap: () => { renders.monthHeatmap++; },
    renderSwitchHourOptimizer: () => { renders.switchOptimizer++; },
    renderBehaviorPending: () => { renders.pending++; },
    renderBehaviorError: err => { renders.errors.push(err); },
    api: (url, body) => {
      apiCalls.push({ url, body });
      if (apiHandler) return apiHandler(url, body);
      return Promise.resolve({ matrix: {}, annual_flows: {} });
    },
    settings: { load_scenario: 'electrified', _fp: 'fp_default' },
    quote: {},
    current: { result: { settings: { battery_kwh: 5 } } },
    behaviorMatrixCache: null,
    behaviorMatrixKey: '',
    activeSeason: 'summer',
    activeHeatmapMetric: 'import',
    behaviorToken: 0,
    JSON,
    Number
  };

  vm.createContext(ctx);
  vm.runInContext(pendingDeclaration + '\n' + fnSlice, ctx);
  ctx.pendingBehaviorRequests = vm.runInContext('pendingBehaviorRequests', ctx);

  return { ctx, renders, apiCalls, setApiHandler: fn => { apiHandler = fn; } };
}

test('identical overlapping calls issue 1 API, update once, and subsequent hits completed cache', async () => {
  const h = createHarness();
  const d = deferred();
  h.setApiHandler(() => d.promise);

  const p1 = h.ctx.refreshBehaviorViews();
  const p2 = h.ctx.refreshBehaviorViews();

  assert.equal(h.apiCalls.length, 1, 'Only one API call issued for overlapping identical requests');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 1, 'Map holds one in-flight promise');

  d.resolve({ matrix: { seasons: {} }, annual_flows: {} });
  await Promise.all([p1, p2]);

  assert.equal(h.renders.typicalDay, 1, 'UI rendered once on completion');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'In-flight map cleaned up after resolution');
  assert.ok(h.ctx.behaviorMatrixCache, 'Completed cache populated');

  const p3 = h.ctx.refreshBehaviorViews();
  await p3;
  assert.equal(h.apiCalls.length, 1, 'No new API call on completed cache hit');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'No residual pending requests');
});

test('A->B->A issues 2 APIs and latest A renders correctly even when B resolves first', async () => {
  const h = createHarness();
  const dA = deferred();
  const dB = deferred();
  h.setApiHandler((_, body) => (body.scenario === 'baseline' ? dB.promise : dA.promise));

  h.ctx.settings.load_scenario = 'electrified';
  const pA1 = h.ctx.refreshBehaviorViews();

  h.ctx.settings.load_scenario = 'baseline';
  const pB = h.ctx.refreshBehaviorViews();

  h.ctx.settings.load_scenario = 'electrified';
  const pA2 = h.ctx.refreshBehaviorViews();

  assert.equal(h.apiCalls.length, 2, 'Exactly 2 API calls issued (A shared, B separate)');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 2, 'Map tracks both active keys');

  dB.resolve({ scenario: 'baseline', matrix: { seasons: {} }, annual_flows: {} });
  await pB;
  assert.equal(h.renders.typicalDay, 0, 'B ignored because token is superseded by A2');

  dA.resolve({ scenario: 'electrified', matrix: { seasons: {} }, annual_flows: {} });
  await Promise.all([pA1, pA2]);

  assert.equal(h.renders.typicalDay, 1, 'Latest A rendered successfully');
  assert.equal(h.ctx.behaviorMatrixCache.scenario, 'electrified', 'Completed cache holds scenario A');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'No residual pending requests');
});

test('same fingerprint but differing normalized settings produce 2 API calls', async () => {
  const h = createHarness();
  const d1 = deferred();
  const d2 = deferred();
  let callCount = 0;
  h.setApiHandler(() => (++callCount === 1 ? d1.promise : d2.promise));

  h.ctx.current.result.settings = { battery_kwh: 5 };
  const p1 = h.ctx.refreshBehaviorViews();

  h.ctx.current.result.settings = { battery_kwh: 10 };
  const p2 = h.ctx.refreshBehaviorViews();

  assert.equal(h.apiCalls.length, 2, 'Differing normalized settings produce 2 API requests');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 2, 'Both distinct payloads pending in map');

  d1.resolve({ matrix: {}, annual_flows: {} });
  d2.resolve({ matrix: {}, annual_flows: {} });
  await Promise.all([p1, p2]);

  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'No residual pending requests');
});

test('stale fingerprint completion is ignored', async () => {
  const h = createHarness();
  const d = deferred();
  h.setApiHandler(() => d.promise);

  h.ctx.settings._fp = 'fp_initial';
  h.ctx.acceptedInputFingerprint = 'fp_initial';
  const p = h.ctx.refreshBehaviorViews();

  h.ctx.settings._fp = 'fp_updated';
  h.ctx.acceptedInputFingerprint = 'fp_updated';

  d.resolve({ matrix: {}, annual_flows: {} });
  await p;

  assert.equal(h.renders.typicalDay, 0, 'Render skipped due to fingerprint mismatch');
  assert.equal(h.ctx.behaviorMatrixCache, null, 'Cache not populated by stale result');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'No residual pending requests');
});

test('shared rejected request cleans map, reports error to latest token only, and retry succeeds', async () => {
  const h = createHarness();
  const dReject = deferred();
  h.setApiHandler(() => dReject.promise);

  const p1 = h.ctx.refreshBehaviorViews();
  const p2 = h.ctx.refreshBehaviorViews();

  assert.equal(h.apiCalls.length, 1, 'Single API call for shared request');
  dReject.reject(new Error('Server dispatch failure 500'));
  await Promise.all([p1, p2]);

  assert.equal(h.renders.errors.length, 1, 'Only latest token reports error');
  assert.match(h.renders.errors[0], /Server dispatch failure 500/);
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'Map cleaned after rejection');

  const dRetry = deferred();
  h.setApiHandler(() => dRetry.promise);
  const p3 = h.ctx.refreshBehaviorViews();

  assert.equal(h.apiCalls.length, 2, 'Retry issues new API request');
  dRetry.resolve({ matrix: { seasons: {} }, annual_flows: {} });
  await p3;

  assert.equal(h.renders.typicalDay, 1, 'Retry successfully renders UI');
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'No residual pending requests');
});

test('synchronous API throw is caught and map is cleaned', async () => {
  const h = createHarness();
  h.setApiHandler(() => { throw new Error('Immediate sync network failure'); });

  await h.ctx.refreshBehaviorViews();

  assert.equal(h.renders.errors.length, 1, 'Sync error captured');
  assert.match(h.renders.errors[0], /Immediate sync network failure/);
  assert.equal(h.ctx.pendingBehaviorRequests.size, 0, 'Map clean after sync throw');
});
