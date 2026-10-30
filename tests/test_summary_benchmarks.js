'use strict';
/* Regression test suite for Summary H1 benchmarks (renderConfigVsRecommended).
 * Verifies idle-callback autorun scheduling, duplicate suppression, stale discard,
 * empty state "Run sizing now" (suppressing infoflag), complete getScopeCashMetrics
 * alignment across all 8 scope/cash/mode combinations, deltas, headroom coexistence with battery hint,
 * and exact configKey deduplication (preventing merge of distinct roof/routing configs).
 */
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appPath = [
  path.join(__dirname, '../feasibility/web/app.js'),
  path.join(process.cwd(), 'feasibility/web/app.js')
].find(p => p && fs.existsSync(p));
const appSource = fs.readFileSync(appPath, 'utf8');

function createHarness() {
  const elements = new Map();
  function makeEl(id = '') {
    const classes = new Set();
    const attrs = new Map();
    const listeners = new Map();
    return {
      id,
      innerHTML: '',
      textContent: '',
      hidden: false,
      disabled: false,
      children: [],
      classList: {
        contains: c => classes.has(c),
        add: c => classes.add(c),
        remove: c => classes.delete(c),
        toggle: c => classes.has(c) ? classes.delete(c) : classes.add(c)
      },
      addEventListener: (evt, fn) => {
        if (!listeners.has(evt)) listeners.set(evt, []);
        listeners.get(evt).push(fn);
      },
      removeEventListener: () => {},
      dispatchEvent: () => {},
      querySelectorAll: function(sel) {
        const res = [];
        if (sel.includes('.empty-state') && this.innerHTML.includes('empty-state')) {
          res.push({ textContent: this.innerHTML, innerHTML: this.innerHTML });
        }
        if (sel.includes('.config-card') || sel.includes('.card')) {
          const matches = this.innerHTML.match(/<div class="card config-card [^"]+">/g) || [];
          matches.forEach(m => res.push({ textContent: m, innerHTML: m }));
        }
        if (sel.includes('.config-benchmark-flag') && this.innerHTML.includes('config-benchmark-flag')) {
          res.push({ textContent: this.innerHTML, innerHTML: this.innerHTML });
        }
        return res;
      },
      querySelector: function(sel) {
        const all = this.querySelectorAll(sel);
        return all[0] || null;
      }
    };
  }

  const $ = id => {
    if (!elements.has(id)) {
      elements.set(id, makeEl(id));
    }
    return elements.get(id);
  };

  const idleCallbacks = [];
  const requestIdleCallbackMock = (cb) => {
    const handle = idleCallbacks.length + 1;
    idleCallbacks.push({ handle, cb, active: true });
    return handle;
  };
  const cancelIdleCallbackMock = (h) => {
    const found = idleCallbacks.find(x => x.handle === h);
    if (found) found.active = false;
  };

  const doc = {
    getElementById: $,
    createElement: tag => makeEl(tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {}
  };

  const win = {
    requestIdleCallback: requestIdleCallbackMock,
    cancelIdleCallback: cancelIdleCallbackMock,
    addEventListener: () => {},
    removeEventListener: () => {}
  };

  const sandbox = {
    console,
    $,
    document: doc,
    window: win,
    requestIdleCallback: requestIdleCallbackMock,
    cancelIdleCallback: cancelIdleCallbackMock,
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    localStorage: { getItem: () => null, setItem: () => {} },
    setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
    clearTimeout: (id) => globalThis.clearTimeout(id),
    idleCallbacks
  };

  vm.createContext(sandbox);
  vm.runInContext(appSource, sandbox);

  return {
    run: (code) => vm.runInContext(code, sandbox),
    sandbox,
    $: sandbox.$,
    idleCallbacks
  };
}

function makeEvaluateFixture(options = {}) {
  const mode = options.cashflow_mode || 'real';
  const bat = options.battery_kwh ?? 10.0;
  return {
    settings: { cashflow_mode: mode, battery_kwh: bat, inflation: 0.02 },
    layout: { panel_count: 14, kwp: 6.0 },
    metrics: {
      npv_eur: { median: 13270 },
      discounted_payback_years: { median: 2.7 }
    },
    finance: {
      capex_eur: 8000,
      conversion_capex_eur: 4000,
      nominal_discount_rate: 0.0404,
      real_discount_rate: 0.02,
      loan_principal_eur: 0,
      pv_incremental: {
        npv: { median: 13270 },
        discounted_payback: { median: 2.7 }
      },
      whole_package: {
        npv: { median: 17500 },
        discounted_payback: { median: 3.5 }
      },
      solar_equity: {
        npv: { median: 14100 },
        discounted_payback: { median: 2.5 }
      },
      package_equity: {
        npv: { median: 18200 },
        discounted_payback: { median: 3.2 }
      }
    }
  };
}

test('Autorun idle scheduling and inplace rerender on accepted sizing result', async () => {
  const app = createHarness();
  app.run(`
    let sizingCallCount = 0;
    let simulateAsyncResolve = null;
    runSolarSizingAnalysis = () => {
      sizingCallCount++;
      return new Promise(resolve => { simulateAsyncResolve = resolve; });
    };
    let fp = 'fp_v1';
    solarStateFingerprint = () => fp;
    isSolarSizingMatching = () => false;
    solarInFlight = false;
    solarData = null;
    current = { result: ${JSON.stringify(makeEvaluateFixture())} };
    summaryScope = 'solar';
    summaryCash = 'cash';
    renderConfigVsRecommended();
  `);

  assert.equal(app.idleCallbacks.length, 1, 'Should schedule idle callback for sizing');
  // Initial render is empty state
  let el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /empty-state/);

  // Trigger idle callback -> starts analysis
  app.idleCallbacks[0].cb();
  assert.equal(app.run('sizingCallCount'), 1, 'runSolarSizingAnalysis invoked');

  // Verify duplicate call is suppressed while in flight
  app.run('solarInFlight = true; renderConfigVsRecommended();');
  assert.equal(app.idleCallbacks.length, 1, 'No duplicate callback while in flight');

  // Simulate sizing resolution rerendering benchmark
  app.run(`
    solarInFlight = false;
    solarAcceptedFingerprint = fp;
    solarData = {
      recommendation_choices: {
        financial: {
          panel_count: 18, kwp: 8.0, battery_kwh: 10.0,
          project_npv: 16000, project_discounted_payback: 2.2, capex_eur: 9000
        },
        balanced: {
          panel_count: 16, kwp: 7.0, battery_kwh: 10.0,
          project_npv: 15000, project_discounted_payback: 2.5, capex_eur: 8500
        }
      }
    };
    renderConfigVsRecommended();
  `);

  el = app.$('configVsRecContent');
  assert.equal(el.innerHTML.includes('empty-state'), false, 'Empty state replaced by populated cards');
  assert.match(el.innerHTML, /BEST RETURN \(FINANCIAL\)/);
  assert.match(el.innerHTML, /BALANCED CHOICE \(RECOMMENDED\)/);
});

test('Stale fingerprint discard prevents unnecessary expensive compute', () => {
  const app = createHarness();
  app.run(`
    let runs = 0;
    runSolarSizingAnalysis = () => { runs++; return Promise.resolve({}); };
    let fp = 'fp_initial';
    solarStateFingerprint = () => fp;
    isSolarSizingMatching = () => false;
    solarInFlight = false;
    solarData = null;
    current = { result: ${JSON.stringify(makeEvaluateFixture())} };
    renderConfigVsRecommended();
    fp = 'fp_modified_before_idle';
  `);

  app.idleCallbacks[0].cb();
  assert.equal(app.run('runs'), 0, 'Stale hash compute must be discarded');
});

test('Empty state: when no choices, renders exactly ONE empty-state and suppresses info flag entirely', () => {
  const app = createHarness();
  app.run(`
    current = { result: ${JSON.stringify(makeEvaluateFixture())} };
    solarData = null;
    renderConfigVsRecommended();
  `);

  const el = app.$('configVsRecContent');
  const emptyMatches = el.innerHTML.match(/class="card config-card empty-state"/g) || [];
  assert.equal(emptyMatches.length, 1, 'Must render exactly ONE empty state card');
  assert.match(el.innerHTML, /Run sizing now/);

  // Infoflag MUST be suppressed entirely when no choices
  assert.equal(el.innerHTML.includes('config-benchmark-flag'), false, 'Info flag must be suppressed when no choices');
});

test('Current NPV aligns with getScopeCashMetrics across all 8 scope/cash/mode combinations', () => {
  const app = createHarness();
  const scopes = ['solar', 'package'];
  const cashes = ['cash', 'loan'];
  const modes = ['real', 'nominal'];

  for (const sc of scopes) {
    for (const ca of cashes) {
      for (const mo of modes) {
        const fixture = makeEvaluateFixture({ cashflow_mode: mo });
        app.run(`
          current = { result: ${JSON.stringify(fixture)} };
          solarData = null;
          summaryScope = '${sc}';
          summaryCash = '${ca}';
          renderConfigVsRecommended();
        `);

        const expectedVal = app.run(`
          (() => {
            const metrics = getScopeCashMetrics(current.result, '${sc}', '${ca}');
            const npv = metrics?.npvObj?.median ?? (typeof metrics?.npvObj === 'number' ? metrics.npvObj : 0);
            return fmt(npv, 'eur');
          })()
        `);

        const el = app.$('configVsRecContent');
        const curCardMatch = el.innerHTML.match(/<div class="card config-card current">[\s\S]*?<div class="value">([^<]+)<\/div>/);
        assert(curCardMatch, `Current card value must be present for ${sc}/${ca}/${mo}`);
        assert.equal(curCardMatch[1], expectedVal, `Current NPV for ${sc}/${ca}/${mo} must match getScopeCashMetrics value ${expectedVal}`);
      }
    }
  }
});

test('Deduplication: identical configs merge into Recommended card; distinct roof/routing do NOT merge', () => {
  const app = createHarness();
  const curFixture = makeEvaluateFixture();

  // Test 1: Identical configs merge
  const identicalChoices = {
    financial: {
      panel_count: 16, kwp: 7.0, battery_kwh: 10.0,
      capex_eur: 8500, project_npv: 15000, project_discounted_payback: 2.2,
      roof_counts: { south: 16 }
    },
    balanced: {
      panel_count: 16, kwp: 7.0, battery_kwh: 10.0,
      capex_eur: 8500, project_npv: 15000, project_discounted_payback: 2.2,
      roof_counts: { south: 16 }
    }
  };

  app.run(`
    current = { result: ${JSON.stringify(curFixture)} };
    solarData = { recommendation_choices: ${JSON.stringify(identicalChoices)} };
    solarStateFingerprint = () => 'fp_valid';
    solarAcceptedFingerprint = 'fp_valid';
    summaryScope = 'solar';
    summaryCash = 'cash';
    renderConfigVsRecommended();
  `);

  let el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /RECOMMENDED CONFIGURATION \(OPTIMAL RETURN & BALANCE\)/);
  assert.equal(el.innerHTML.includes('BEST RETURN (FINANCIAL)'), false);

  // Test 2: Same capacity but different roofs/routing MUST NOT merge
  const differentRoofChoices = {
    financial: {
      panel_count: 16, kwp: 7.0, battery_kwh: 10.0,
      capex_eur: 8500, project_npv: 15200, project_discounted_payback: 2.1,
      roof_counts: { south: 16 }
    },
    balanced: {
      panel_count: 16, kwp: 7.0, battery_kwh: 10.0,
      capex_eur: 8500, project_npv: 15000, project_discounted_payback: 2.2,
      roof_counts: { south: 10, west: 6 }
    }
  };

  app.run(`
    solarData = { recommendation_choices: ${JSON.stringify(differentRoofChoices)} };
    renderConfigVsRecommended();
  `);

  el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /BEST RETURN \(FINANCIAL\)/, 'Different roof distribution must render Best return card');
  assert.match(el.innerHTML, /BALANCED CHOICE \(RECOMMENDED\)/, 'Different roof distribution must render Balanced choice card');
  assert.equal(el.innerHTML.includes('RECOMMENDED CONFIGURATION (OPTIMAL RETURN & BALANCE)'), false, 'Must not merge configs with different roof allocations');
});

test('Coexistence: headroom hint coexists with battery sizing hint without being hidden', () => {
  const app = createHarness();
  // current has 14 panels, 0 kWh battery.
  // recommended has 16 panels, 10 kWh battery.
  // Both storage capacity deficit and array headroom must be reported together!
  const curFixture = makeEvaluateFixture({ battery_kwh: 0 });
  const choices = {
    balanced: {
      panel_count: 16, kwp: 7.0, battery_kwh: 10.0,
      capex_eur: 8500, project_npv: 15000, project_discounted_payback: 2.2
    }
  };

  app.run(`
    current = { result: ${JSON.stringify(curFixture)} };
    solarData = { recommendation_choices: ${JSON.stringify(choices)} };
    solarStateFingerprint = () => 'fp_valid';
    solarAcceptedFingerprint = 'fp_valid';
    summaryScope = 'solar';
    summaryCash = 'cash';
    renderConfigVsRecommended();
  `);

  const el = app.$('configVsRecContent');
  const flagText = el.innerHTML;
  assert.match(flagText, /Storage capacity is 10\.0 kWh below balanced optimum/);
  assert.match(flagText, /Array has headroom for 2 additional panels on high-yield facets\./);
});

test('Roof max constraint filtering with real candidate format and single valid recommendation display', () => {
  const app = createHarness();
  const curFixture = makeEvaluateFixture();

  // Test 1a: Only South22 active. Financial candidate has 23 panels exposing only settings (no roof_counts),
  // Balanced candidate has 16 panels on South with full real schema.
  const choices = {
    financial: {
      panel_count: 23, kwp: 10.12, battery_kwh: 10.0, capex_eur: 11000,
      settings: {
        south_panels: 23, west_panels: 0, east_panels: 0,
        dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
        panel_watts: 440, inverter_kw: 6.0, pv_battery_coupling: 'dc_coupled',
        battery_kwh: 10.0, battery_modules: 2, routing_priority: 'self_consumption'
      }
    },
    balanced: {
      panel_count: 16, kwp: 7.04, battery_kwh: 10.0, capex_eur: 8500,
      roof_counts: { south: 16, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      settings: {
        south_panels: 16, west_panels: 0, east_panels: 0,
        dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
        panel_watts: 440, inverter_kw: 6.0, pv_battery_coupling: 'dc_coupled',
        battery_kwh: 10.0, battery_modules: 2, routing_priority: 'self_consumption'
      }
    }
  };

  app.run(`
    current = { result: ${JSON.stringify(curFixture)} };
    solarData = {
      recommendation_choices: ${JSON.stringify(choices)},
      search_summary: {
        effective_roof_limits: { south: 22, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 }
      }
    };
    solarStateFingerprint = () => 'fp_valid';
    solarAcceptedFingerprint = 'fp_valid';
    summaryScope = 'solar';
    summaryCash = 'cash';
    renderConfigVsRecommended();
  `);

  let el = app.$('configVsRecContent');
  // Financial candidate has 23 panels on South22 -> MUST be rejected even if only exposing settings
  assert.equal(el.innerHTML.includes('BEST RETURN (FINANCIAL)'), false, 'Candidate with 23 panels on South22 must be rejected even if only exposing settings');
  // Balanced candidate has 16 panels on South22 -> valid shared recommendation remains displayable
  assert.match(el.innerHTML, /BALANCED CHOICE \(RECOMMENDED\)/, 'One valid shared recommendation must remain displayable');

  // Test 1b: Disabled roof maxima honored vs secondary roof badge
  // When secondary_south limit is 0 (disabled), candidate with secondary_south is rejected
  const secCandidate = {
    balanced: {
      panel_count: 14, kwp: 6.16, battery_kwh: 10.0, capex_eur: 8000,
      roof_counts: { south: 12, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 2 },
      settings: {
        south_panels: 12, west_panels: 0, east_panels: 0,
        dormer_panels: 0, flat_panels: 0, secondary_south_panels: 2,
        panel_watts: 440, inverter_kw: 6.0, pv_battery_coupling: 'dc_coupled',
        battery_kwh: 10.0, battery_modules: 2
      }
    }
  };

  app.run(`
    solarData = {
      recommendation_choices: ${JSON.stringify(secCandidate)},
      search_summary: {
        effective_roof_limits: { south: 22, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 }
      }
    };
    renderConfigVsRecommended();
  `);
  el = app.$('configVsRecContent');
  assert.equal(el.innerHTML.includes('BALANCED CHOICE (RECOMMENDED)'), false, 'Candidate requiring secondary facet must be rejected when secondary limit is 0 (disabled)');

  // When secondary_south limit is 4 (active), candidate is feasible and displays secondary roof badge
  app.run(`
    solarData.search_summary.effective_roof_limits.secondary_south = 4;
    renderConfigVsRecommended();
  `);
  el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /BALANCED CHOICE \(RECOMMENDED\)/, 'Candidate must be feasible when secondary facet limit allows it');
  assert.match(el.innerHTML, /secondary-roof-badge/, 'Secondary roof badge must be present when secondary facet is used');
  assert.match(el.innerHTML, /Secondary roof required/, 'Secondary roof badge text must be displayed');
});

test('Same source: reuses System design recommendation and never invents or clamps unevaluated candidate', () => {
  const app = createHarness();
  const curFixture = makeEvaluateFixture();

  const infeasibleChoices = {
    financial: {
      panel_count: 24, kwp: 10.56, battery_kwh: 10.0, capex_eur: 12000,
      roof_counts: { south: 24, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      settings: { south_panels: 24, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0 }
    },
    balanced: {
      panel_count: 18, kwp: 7.92, battery_kwh: 10.0, capex_eur: 9500,
      roof_counts: { south: 18, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      settings: { south_panels: 18, west_panels: 0, east_panels: 0, dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0 }
    }
  };

  app.run(`
    current = { result: ${JSON.stringify(curFixture)} };
    solarData = {
      recommendation_choices: ${JSON.stringify(infeasibleChoices)},
      search_summary: {
        effective_roof_limits: { south: 15, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 }
      }
    };
    solarStateFingerprint = () => 'fp_valid';
    solarAcceptedFingerprint = 'fp_valid';
    summaryScope = 'solar';
    summaryCash = 'cash';
    renderConfigVsRecommended();
  `);

  const el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /empty-state/, 'Must render empty state when no feasible System design recommendation exists');
  assert.match(el.innerHTML, /Run sizing now/);
  assert.equal(el.innerHTML.includes('BEST RETURN (FINANCIAL)'), false);
  assert.equal(el.innerHTML.includes('BALANCED CHOICE (RECOMMENDED)'), false);
});

test('Real evaluation: proves current 2.7 y vs candidate capex and loan tradeoff without fabricated 0.0', () => {
  const app = createHarness();
  const curFixture = makeEvaluateFixture();

  const cand = {
    panel_count: 18, kwp: 7.92, battery_kwh: 10.0, battery_modules: 2,
    capex_eur: 9500,
    roof_counts: { south: 18, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
    settings: {
      south_panels: 18, west_panels: 0, east_panels: 0,
      dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
      panel_watts: 440, inverter_kw: 6.0, pv_battery_coupling: 'dc_coupled',
      battery_kwh: 10.0, battery_modules: 2, routing_priority: 'self_consumption'
    }
  };

  const choices = { balanced: cand };

  // Full mocked evaluation result from backend
  const candidateEvalResult = {
    settings: { cashflow_mode: 'real', battery_kwh: 10.0, inflation: 0.02 },
    layout: { panel_count: 18, kwp: 7.92 },
    metrics: {
      npv_eur: { median: 16500 },
      package_npv_eur: { median: 19500 },
      discounted_payback_years: { median: 2.3 }
    },
    finance: {
      capex_eur: 9500,
      conversion_capex_eur: 4000,
      nominal_discount_rate: 0.0404,
      real_discount_rate: 0.02,
      loan_principal_eur: 4750,
      solar_loan_principal_eur: 4750,
      pv_incremental: {
        npv: { median: 16500 },
        discounted_payback: { median: 2.3 }
      },
      whole_package: {
        npv: { median: 19500 },
        discounted_payback: { median: 3.1 }
      },
      solar_equity: {
        npv: { median: 15800 },
        discounted_payback: { median: 2.1 }
      },
      package_equity: {
        npv: { median: 20200 },
        discounted_payback: { median: 2.8 }
      }
    }
  };

  // Wire full mocked evaluation into candidateEvalCache
  app.run(`
    current = {
      result: {
        ...${JSON.stringify(curFixture)},
        settings: { ...${JSON.stringify(curFixture.settings)}, loan_mode: 'fraction', loan_fraction: 0.5 }
      }
    };
    solarData = {
      recommendation_choices: ${JSON.stringify(choices)},
      search_summary: {
        effective_roof_limits: { south: 22, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 }
      }
    };
    solarStateFingerprint = () => 'fp_valid';
    solarAcceptedFingerprint = 'fp_valid';

    const req = summaryCandidateRequest(solarData.recommendation_choices.balanced, current.result.settings);
    candidateEvalCache.set(JSON.stringify(req), {
      loading: false,
      result: ${JSON.stringify(candidateEvalResult)},
      error: null
    });

    summaryScope = 'solar';
    summaryCash = 'cash';
    renderConfigVsRecommended();
  `);

  let el = app.$('configVsRecContent');
  // Cash basis: baseline is 2.7 y, candidate is 2.3 y -> Δ -0.4 y; ΔCAPEX is +€1,500 (9500 - 8000)
  assert.match(el.innerHTML, /Payback 2\.3 y \(Δ -0\.4 y\)/, 'Cash mode must reflect evaluated candidate payback vs baseline 2.7 y');
  assert.match(el.innerHTML, /Unlevered payback 2\.3 y \(Δ -0\.4 y\)/, 'Unlevered payback must reflect evaluated candidate payback vs baseline 2.7 y');
  assert.match(el.innerHTML, /ΔCAPEX \+€1,500/, 'Capex delta must reflect candidate capex 9500 vs baseline 8000');

  // Loan basis: baseline levered payback is 2.5 y (solar_equity), candidate levered payback is 2.1 y -> Δ -0.4 y
  // Baseline unlevered payback is 2.7 y, candidate unlevered payback is 2.3 y -> Δ -0.4 y
  app.run(`
    summaryCash = 'loan';
    renderConfigVsRecommended();
  `);
  el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /Payback 2\.1 y \(Δ -0\.4 y\)/, 'Loan mode must reflect evaluated equity payback 2.1 y vs baseline 2.5 y without fabricated 0.0');
  assert.match(el.innerHTML, /Unlevered payback 2\.3 y \(Δ -0\.4 y\)/, 'Loan mode must reflect unlevered payback 2.3 y vs baseline 2.7 y');
});

test('Backend null recovery preserved: Not reached handling without fabricated 0.0 and no fake delta', () => {
  const app = createHarness();
  const curFixture = makeEvaluateFixture();

  const cand = {
    panel_count: 14, kwp: 6.16, battery_kwh: 10.0, capex_eur: 8000,
    roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
    settings: {
      south_panels: 14, west_panels: 0, east_panels: 0,
      dormer_panels: 0, flat_panels: 0, secondary_south_panels: 0,
      panel_watts: 440, inverter_kw: 6.0, pv_battery_coupling: 'dc_coupled',
      battery_kwh: 10.0, battery_modules: 2, routing_priority: 'self_consumption'
    }
  };

  const choices = { balanced: cand };

  // Mocked evaluation result where backend returns null for solar_equity discounted payback
  const unrecoveredEvalResult = {
    settings: { cashflow_mode: 'real', battery_kwh: 10.0, inflation: 0.02 },
    layout: { panel_count: 14, kwp: 6.16 },
    metrics: {
      npv_eur: { median: 14000 },
      package_npv_eur: { median: 17500 },
      discounted_payback_years: { median: 2.6 }
    },
    finance: {
      capex_eur: 8000,
      conversion_capex_eur: 4000,
      nominal_discount_rate: 0.0404,
      real_discount_rate: 0.02,
      loan_principal_eur: 8000,
      solar_loan_principal_eur: 8000,
      pv_incremental: {
        npv: { median: 14000 },
        discounted_payback: { median: 2.6 }
      },
      solar_equity: {
        npv: { median: 13500 },
        discounted_payback: null
      }
    }
  };

  app.run(`
    current = {
      result: {
        ...${JSON.stringify(curFixture)},
        settings: { ...${JSON.stringify(curFixture.settings)}, loan_mode: 'fraction', loan_fraction: 1.0 }
      }
    };
    solarData = {
      recommendation_choices: ${JSON.stringify(choices)},
      search_summary: {
        effective_roof_limits: { south: 22, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 }
      }
    };
    solarStateFingerprint = () => 'fp_valid';
    solarAcceptedFingerprint = 'fp_valid';

    const req = summaryCandidateRequest(solarData.recommendation_choices.balanced, current.result.settings);
    candidateEvalCache.set(JSON.stringify(req), {
      loading: false,
      result: ${JSON.stringify(unrecoveredEvalResult)},
      error: null
    });

    summaryScope = 'solar';
    summaryCash = 'loan';
    renderConfigVsRecommended();
  `);

  let el = app.$('configVsRecContent');
  // Backend returns null for equity payback -> MUST preserve backend null recovery as Not reached, NOT fabricated 0.0!
  assert.match(el.innerHTML, /Payback Not reached/, 'Unrecovered equity payback must preserve backend null as Not reached');
  assert.doesNotMatch(el.innerHTML, /Payback Not reached \(Δ/, 'Must not compute or display fake delta when candidate payback is Not reached');
  assert.match(el.innerHTML, /Unlevered payback 2\.6 y \(Δ -0\.1 y\)/, 'Unlevered payback must display valid 2.6 y with delta vs baseline 2.7 y');

  // When baseline payback is null, candidate with valid payback displays value without fake delta
  app.run(`
    current.result.finance.solar_equity.discounted_payback = null;
    current.result.finance.pv_incremental.discounted_payback = null;
    current.result.metrics.discounted_payback_years = null;
    candidateEvalCache.get(JSON.stringify(req)).result.finance.solar_equity.discounted_payback = { median: 2.2 };
    renderConfigVsRecommended();
  `);
  el = app.$('configVsRecContent');
  assert.match(el.innerHTML, /Payback 2\.2 y/, 'Candidate payback 2.2 y is shown');
  assert.doesNotMatch(el.innerHTML, /Payback 2\.2 y \(Δ/, 'Must not compute or display fake delta when baseline payback is null');
});


test('Candidate request preserves quote reference and the fixed loan cap', () => {
  const app = createHarness();
  const base = {...makeEvaluateFixture().settings, quote_total:16000,
    quote_vat_included:true, loan_mode:'amount', loan_amount:15000,
    south_panels:19, battery_kwh:9.728};
  const candidate = {panel_count:23, capex_eur:14004.48,
    roof_counts:{south:23}, battery_kwh:9.728};
  const payload = app.run(`summaryCandidateRequest(${JSON.stringify(candidate)}, ${JSON.stringify(base)})`);
  assert.equal(payload.quote.price_basis, 'summary_candidate');
  assert.equal(JSON.stringify(payload.quote.reference_settings), JSON.stringify(base));
  assert.equal(payload.settings.loan_amount, 15000);
  assert.equal(payload.settings.south_panels, 23);
  assert.equal(payload.quote.reference_settings.south_panels, 19);
});
