/* Run with Node: test AC battery sizing state, schedule invalidation, candidate transitions, out-of-order protection, and validation bounds.
 * Note: Reconstructed coverage from current public behavior; does not recover unknown original contents (corrupted file was 38854 NUL bytes).
 */
'use strict';
const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function extractNamedFunction(source, funcName) {
  const regex = new RegExp('(?:async\\s+)?function\\s+' + funcName + '\\s*\\([^\\{]*\\)\\s*\\{');
  const match = regex.exec(source);
  if (!match) throw new Error('Function ' + funcName + ' not found in source');
  const start = match.index;
  let idx = start + match[0].length;
  let depth = 1;
  let inString = false;
  let quote = null;
  let inComment = false;
  while (idx < source.length && depth > 0) {
    const ch = source[idx];
    const prev = source[idx - 1];
    if (inString) {
      if (ch === quote && prev !== '\\') inString = false;
    } else if (inComment) {
      if (inComment === 'line' && ch === '\n') inComment = false;
      else if (inComment === 'block' && ch === '/' && prev === '*') inComment = false;
    } else {
      if (ch === '"' || ch === "'" || ch === '`') {
        inString = true; quote = ch;
      } else if (ch === '/' && source[idx + 1] === '/') {
        inComment = 'line'; idx++;
      } else if (ch === '/' && source[idx + 1] === '*') {
        inComment = 'block'; idx++;
      } else if (ch === '{') {
        depth++;
      } else if (ch === '}') {
        depth--;
      }
    }
    idx++;
  }
  return source.slice(start, idx);
}

function createHarness(overrides = {}) {
  const DEFAULT_VALUES = {
    batScenario: "electrified",
    batRepresentative: "median",
    batStep: "1",
    batMaxCapacity: "25",
    batLifetime: "12",
    batDegradation: "1.5",
    batDeduction: "0.5",
    batCostSens: "0",
    batSpreadDelta: "0",
    batQuote10: "",
    batQuote15: "",
    optBatteryMin: "0",
    optBatteryMax: "4",
    maxSouth: "10",
    maxWest: "10",
    maxEast: "10",
    maxDormer: "0",
    maxFlat: "0",
    maxSecondarySouth: "0",
    optPanelWatts: "400",
    optModuleKwh: "5",
    optInverter: "6",
    optCharge: "3",
    optDischarge: "3"
  };
  const elements = new Map();
  function makeElement(id) {
    const classSet = new Set();
    const listeners = {};
    const parent = { style: { display: '' } };
    const el = {
      id,
      innerHTML: '',
      textContent: '',
      value: DEFAULT_VALUES[id] !== undefined ? DEFAULT_VALUES[id] : "",
      disabled: false,
      hidden: false,
      style: { display: '' },
      dataset: {},
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
      closest: sel => sel === 'label' ? parent : null,
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
      elements.set(id, makeElement(id));
    }
    return elements.get(id);
  };

  const storage = new Map();
  const mockLocalStorage = {
    getItem: k => storage.has(k) ? storage.get(k) : null,
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: k => storage.delete(k)
  };

  let apiHandler = overrides.apiHandler || (async (url, body) => ({ status: 'OK' }));
  const shownErrors = [];

  const contextState = {
    $,
    console,
    localStorage: mockLocalStorage,
    structuredClone: v => JSON.parse(JSON.stringify(v)),
    deepClone: v => JSON.parse(JSON.stringify(v)),
    cleanSettingsForBackend: s => s,
    normalizeBatterySettings: s => s,
    hideChartTooltip: () => {},
    showError: err => { shownErrors.push(err); },
    shownErrors,
    api: (url, body) => apiHandler(url, body),
    setApiHandler: fn => { apiHandler = fn; },

    // State bindings
    BATTERY_STORAGE_KEY: 'pv-lab-battery-sizing-v1',
    batteryData: null,
    batteryRequestId: 0,
    batteryInFlight: false,
    batteryAcceptedFingerprint: null,
    batteryControlsInitialized: false,
    batteryControlIds: [
      'batScenario', 'batRepresentative', 'batStep', 'batMaxCapacity',
      'batQuote10', 'batQuote15', 'batLifetime', 'batDegradation',
      'batDeduction', 'batCostSens', 'batSpreadDelta'
    ],

    optimizerResult: null,
    optimizerRequestId: 0,
    optimizerStale: false,
    optimizerFingerprint: null,
    panelKeys: ['south', 'west', 'east'],
    saveOptimizerPrefs: () => {},
    renderCandidates: () => {},
    renderBatterySizing: () => {},
    markSolarSizingStale: () => {},
    editSetting: (k, v) => { contextState.settings[k] = v; },

    settings: {
      load_scenario: 'electrified',
      battery_kwh: 5,
      battery_modules: 1,
      battery_module_kwh: 5,
      battery_mixed_sizes: false,
      deduction_rate: 0.5,
      ac_heating_schedule: 'daily',
      ...(overrides.settings || {})
    },
    quote: {
      line_items: [],
      ...(overrides.quote || {})
    },

    acScheduleInvalid: false,
    acDraftInvalid: false,
    optimizerFingerprintVal: 'opt-state-default'
  };

  const context = vm.createContext(contextState);

  // Implement fallback / mock state helpers
  vm.runInContext(`
    function shouldBlockAcRecompute(s = settings) {
      return Boolean(acScheduleInvalid || acDraftInvalid);
    }
    function shouldBlockOptimizerForAcDraft(search = null) {
      return Boolean(acScheduleInvalid || acDraftInvalid);
    }
    function optimizerStateFingerprint() {
      return optimizerFingerprintVal;
    }
    function optimizerSearch() {
      return {
        objective: 'npv_eur',
        load_scenario: settings.load_scenario,
        battery_min_modules: 0,
        battery_max_modules: 4,
        panel_maxima: { south: 10, west: 10, east: 10 },
        include_theoretical: false
      };
    }
    function optimizerEquipment() {
      return {
        panel_watts: 400,
        battery_module_kwh: 5,
        inverter_kw: 6,
        charge_kw: 3,
        discharge_kw: 3,
        battery_mixed_sizes: Boolean(settings.battery_mixed_sizes)
      };
    }
  `, context);

  // Load complete named functions from appSource
  const functionNames = [
    'markBatterySizingStale',
    'getBatteryOptions',
    'shouldBlockBatterySizingForAcDraft',
    'batteryStateFingerprint',
    'isBatterySizingRequestValid',
    'isBatterySizingMatching',
    'saveBatteryOptionsToStorage',
    'loadBatteryOptionsFromStorage',
    'syncBatterySizingControls',
    'initBatterySizingControls',
    'runBatterySizingAnalysis',
    'markOptimizerStale',
    'loadCandidates'
  ];

  for (const name of functionNames) {
    const fnDef = extractNamedFunction(appSource, name);
    vm.runInContext(fnDef, context);
  }

  return { context, elements, $ };
}

describe('AC Battery Sizing State and Invalidation Coverage', () => {

  describe('1. State initialization and dirty changes', () => {
    it('markBatterySizingStale increments requestId and marks UI and wrap stale', () => {
      const { context, $ } = createHarness();
      context.batteryData = { status: 'OK' };
      $('batResultsWrap').classList.remove('stale');
      $('runBatterySizing').disabled = true;

      context.markBatterySizingStale();

      assert.strictEqual(context.batteryRequestId, 1);
      assert.strictEqual(context.batteryInFlight, false);
      assert.strictEqual(context.batteryAcceptedFingerprint, null);
      assert.strictEqual($('batResultsWrap').classList.contains('stale'), true);
      assert.strictEqual($('runBatterySizing').disabled, false);
      assert.strictEqual($('batStatus').textContent, 'Inputs changed · Click Run sizing analysis to update');
    });

    it('initBatterySizingControls attaches listeners and marks stale on input changes', () => {
      const { context, $ } = createHarness();
      context.initBatterySizingControls();

      context.batteryData = { status: 'OK' };
      $('batStep').value = '2';
      $('batStep').dispatchEvent('change');

      assert.strictEqual($('batResultsWrap').classList.contains('stale'), true);
      assert.strictEqual(context.batteryRequestId > 0, true);
    });

    it('persists and reloads battery options across storage cycles', () => {
      const { context, $ } = createHarness();
      $('batScenario').value = 'electrified';
      $('batStep').value = '2';
      $('batMaxCapacity').value = '30';
      $('batLifetime').value = '15';

      context.saveBatteryOptionsToStorage();

      $('batScenario').value = 'baseline';
      $('batStep').value = '1';
      $('batMaxCapacity').value = '25';
      $('batLifetime').value = '12';

      context.loadBatteryOptionsFromStorage();

      assert.strictEqual($('batScenario').value, 'electrified');
      assert.strictEqual($('batStep').value, '2');
      assert.strictEqual($('batMaxCapacity').value, '30');
      assert.strictEqual($('batLifetime').value, '15');
    });
  });

  describe('2. AC schedule changes invalidate candidate sizing and battery sizing', () => {
    it('shouldBlockBatterySizingForAcDraft identifies invalid AC schedule draft', () => {
      const { context } = createHarness();
      assert.strictEqual(context.shouldBlockBatterySizingForAcDraft(), false);

      context.acScheduleInvalid = true;
      assert.strictEqual(context.shouldBlockBatterySizingForAcDraft(), true);
    });

    it('runBatterySizingAnalysis blocks execution when AC schedule draft is invalid', async () => {
      let apiCalled = false;
      const { context, $ } = createHarness({
        apiHandler: async () => { apiCalled = true; return { status: 'OK' }; }
      });

      context.acScheduleInvalid = true;
      context.batteryData = { existing: true };

      await context.runBatterySizingAnalysis();

      assert.strictEqual(apiCalled, false, 'API must not be called when AC draft is invalid');
      assert.strictEqual($('batStatus').textContent, 'Cannot run battery sizing with invalid heating draft.');
      assert.strictEqual($('batResultsWrap').classList.contains('stale'), true);
      assert.strictEqual(context.batteryInFlight, false);
      assert.strictEqual($('runBatterySizing').disabled, false);
    });

    it('isBatterySizingMatching returns false when AC schedule is invalid', () => {
      const { context, $ } = createHarness();
      const fp = context.batteryStateFingerprint();
      context.batteryData = { status: 'OK' };
      context.batteryAcceptedFingerprint = fp;
      $('batResultsWrap').classList.remove('stale');

      assert.strictEqual(context.isBatterySizingMatching(), true);

      context.acScheduleInvalid = true;
      assert.strictEqual(context.isBatterySizingMatching(), false, 'Must not match when AC schedule invalid');
    });

    it('loadCandidates marks optimizer stale and aborts on invalid AC draft', async () => {
      let apiCalled = false;
      const { context, $ } = createHarness({
        apiHandler: async () => { apiCalled = true; return { candidates: [] }; }
      });

      context.acDraftInvalid = true;
      await context.loadCandidates();

      assert.strictEqual(apiCalled, false, 'API must not be called for optimizer search when AC draft is invalid');
      assert.strictEqual($('paretoMethod').textContent, 'Cannot search configurations with invalid heating draft.');
      assert.strictEqual(context.optimizerStale, true);
      assert.strictEqual($('candidates').classList.contains('stale'), true);
      assert.strictEqual($('bestConfiguration').classList.contains('stale'), true);
      assert.strictEqual($('paretoChart').classList.contains('stale'), true);
    });
  });

  describe('3. Candidate and battery application state transitions', () => {
    it('applies battery sizing result and transitions wrap out of stale state on valid arrival', async () => {
      const mockResult = { status: 'SUCCESS', capacities: [5, 10, 15] };
      const { context, $ } = createHarness({
        apiHandler: async () => mockResult
      });

      $('batStep').value = '1';
      $('batMaxCapacity').value = '25';
      $('batLifetime').value = '12';
      $('batResultsWrap').classList.add('stale');

      await context.runBatterySizingAnalysis();

      assert.strictEqual(context.batteryData, mockResult);
      assert.strictEqual(context.batteryAcceptedFingerprint, context.batteryStateFingerprint());
      assert.strictEqual($('batResultsWrap').classList.contains('stale'), false);
      assert.strictEqual(context.batteryInFlight, false);
      assert.strictEqual(context.isBatterySizingMatching(), true);
    });

    it('loadCandidates transitions candidate UI out of stale state on success', async () => {
      const mockPareto = {
        candidates: [{ id: 'c1', panel_count: 10, battery_modules: 2 }],
        best_candidate: { id: 'c1' },
        recommended_id: 'c1'
      };
      const { context, $ } = createHarness({
        apiHandler: async () => mockPareto
      });

      $('candidates').classList.add('stale');
      $('bestConfiguration').classList.add('stale');
      $('paretoChart').classList.add('stale');

      await context.loadCandidates();

      assert.strictEqual(context.optimizerResult, mockPareto);
      assert.strictEqual(context.optimizerStale, false);
      assert.strictEqual($('candidates').classList.contains('stale'), false);
      assert.strictEqual($('bestConfiguration').classList.contains('stale'), false);
      assert.strictEqual($('paretoChart').classList.contains('stale'), false);
    });
  });

  describe('4. Stale and out-of-order response protection', () => {
    it('discards out-of-order battery sizing responses when a newer request was dispatched', async () => {
      let resolveFirst;
      const firstPromise = new Promise(r => { resolveFirst = r; });
      let callCount = 0;

      const { context } = createHarness({
        apiHandler: async (url, body) => {
          callCount++;
          if (callCount === 1) {
            return await firstPromise;
          }
          return { status: 'FAST_SECOND_RESPONSE', id: 2 };
        }
      });

      const p1 = context.runBatterySizingAnalysis();
      const p2 = context.runBatterySizingAnalysis();

      await p2;
      assert.strictEqual(context.batteryData.status, 'FAST_SECOND_RESPONSE');

      resolveFirst({ status: 'SLOW_FIRST_RESPONSE', id: 1 });
      await p1;

      assert.strictEqual(context.batteryData.status, 'FAST_SECOND_RESPONSE');
    });

    it('discards battery sizing result if settings state mutated while in flight', async () => {
      let resolveApi;
      const apiPromise = new Promise(r => { resolveApi = r; });

      const { context, $ } = createHarness({
        apiHandler: async () => await apiPromise
      });

      const sizingPromise = context.runBatterySizingAnalysis();

      context.settings.battery_kwh = 15;

      resolveApi({ status: 'STALE_SETTINGS_RESULT' });
      await sizingPromise;

      assert.strictEqual(context.batteryData, null, 'Must not accept result after state mutation');
      assert.strictEqual(context.batteryAcceptedFingerprint, null);
    });

    it('loadCandidates discards stale response if optimizerRequestId incremented', async () => {
      let resolveFirst;
      const firstPromise = new Promise(r => { resolveFirst = r; });
      let callCount = 0;

      const { context } = createHarness({
        apiHandler: async () => {
          callCount++;
          if (callCount === 1) return await firstPromise;
          return { candidates: [{ id: 'new' }] };
        }
      });

      const p1 = context.loadCandidates();
      const p2 = context.loadCandidates();

      await p2;
      assert.deepStrictEqual(context.optimizerResult.candidates, [{ id: 'new' }]);

      resolveFirst({ candidates: [{ id: 'old' }] });
      await p1;

      assert.deepStrictEqual(context.optimizerResult.candidates, [{ id: 'new' }]);
    });
  });

  describe('5. Distinct enablement, mixed mode, and input validation bounds', () => {
    it('syncBatterySizingControls disables and hides step control in mixed mode', () => {
      const { context, $ } = createHarness({
        settings: { battery_mixed_sizes: true }
      });

      context.syncBatterySizingControls();

      assert.strictEqual($('batStep').disabled, true);
      assert.strictEqual($('batStep').hidden, true);
      assert.strictEqual($('batStep').closest('label').style.display, 'none');

      context.settings.battery_mixed_sizes = false;
      context.syncBatterySizingControls();

      assert.strictEqual($('batStep').disabled, false);
      assert.strictEqual($('batStep').hidden, false);
      assert.strictEqual($('batStep').closest('label').style.display, '');
    });

    it('runBatterySizingAnalysis validates integer step bounds (1 or 2)', async () => {
      const { context, $ } = createHarness();
      $('batStep').value = '3';

      await context.runBatterySizingAnalysis();

      assert.strictEqual($('batStatus').textContent, 'Step must be integer 1 or 2.');
      assert.strictEqual(context.batteryInFlight, false);
      assert.strictEqual($('runBatterySizing').disabled, false);
    });

    it('runBatterySizingAnalysis validates max_capacity bounds (10 to 50)', async () => {
      const { context, $ } = createHarness();
      $('batStep').value = '1';
      $('batMaxCapacity').value = '55';

      await context.runBatterySizingAnalysis();

      assert.strictEqual($('batStatus').textContent, 'Maximum capacity must be an integer between 10 and 50.');
      assert.strictEqual(context.batteryInFlight, false);
    });

    it('runBatterySizingAnalysis validates battery lifetime bounds (1 to 30)', async () => {
      const { context, $ } = createHarness();
      $('batStep').value = '1';
      $('batMaxCapacity').value = '25';
      $('batLifetime').value = '35';

      await context.runBatterySizingAnalysis();

      assert.strictEqual($('batStatus').textContent, 'Battery lifetime must be an integer between 1 and 30.');
      assert.strictEqual(context.batteryInFlight, false);
    });

    it('handles api error for current valid request and cleans up button/inflight state', async () => {
      const { context, $ } = createHarness({
        apiHandler: async () => { throw new Error('Network timeout'); }
      });

      $('batStep').value = '1';
      $('batMaxCapacity').value = '25';
      $('batLifetime').value = '12';

      await context.runBatterySizingAnalysis();

      assert.strictEqual($('batStatus').textContent, 'Error: Network timeout');
      assert.strictEqual(context.batteryInFlight, false);
      assert.strictEqual($('runBatterySizing').disabled, false);
      assert.strictEqual(context.shownErrors.length, 1);
    });
  });
});
