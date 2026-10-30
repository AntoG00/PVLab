'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function createTestContext() {
  const elements = {
    loanPayment: { innerHTML: '', textContent: '' },
    loanAssumptions: { textContent: '' },
    loanCashChart: { innerHTML: '', textContent: '' },
    loanInsights: { innerHTML: '', textContent: '' },
    loanCashHeading: { textContent: '' },
    loanPivot: { innerHTML: '', textContent: '', onclick: null },
    loanMetricCards: { innerHTML: '', textContent: '' },
    saveLoanSimulation: { disabled: false },
    solveLoanAmount: { disabled: false },
    loanAmountControl: { value: '', max: '', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanModeControl: { value: 'amount', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanFractionControl: { value: '100', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanFractionWrap: { hidden: false },
    loanAprControl: { value: '5.24', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanYearsControl: { value: '10', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanMaxMonthlyPaymentControl: { value: '', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanScopeControl: { value: 'solar', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanScopeContext: { textContent: '' },
    loanInflationControl: { value: '2.00', validity: { valid: true }, closest: () => ({ hidden: false }) },
    loanDiscountControl: { value: '3.00', validity: { valid: true }, closest: () => ({ hidden: false }) },
    cashFlowBasis: { value: 'equity' },
    cashFlowHeading: { textContent: '' },
    cashFlowDescription: { textContent: '' },
    cashFlowBasisNote: { textContent: '' },
    cashChart: { innerHTML: '', textContent: '' },
    cashInsights: { innerHTML: '', textContent: '' },
    controls: { innerHTML: '', oninput: null, onchange: null, onclick: null, onkeydown: null },
    pinnedControlsList: { innerHTML: '', oninput: null, onchange: null, onclick: null, onkeydown: null },
  };

  const registeredInputs = [];
  const switchContextElements = {};

  const doc = {
    getElementById: id => elements[id] || { textContent: '', innerHTML: '', value: '' },
    querySelector: sel => {
      const match = sel.match(/\[data-switch-context="([^"]+)"\]/);
      if (match) return switchContextElements[match[1]] || null;
      if (sel.startsWith('#')) return elements[sel.slice(1)] || null;
      return null;
    },
    querySelectorAll: sel => {
      if (sel.includes('.loan-controls input')) return [elements.loanAmountControl, elements.loanAprControl, elements.loanYearsControl];
      return registeredInputs;
    },
    addEventListener: () => {},
    removeEventListener: () => {},
    body: { appendChild: () => {}, querySelector: () => null, addEventListener: () => {} },
    activeElement: null
  };

  const win = {
    addEventListener: () => {},
    removeEventListener: () => {},
    innerWidth: 1024,
    innerHeight: 768,
    document: doc
  };
  win.window = win;

  const context = vm.createContext({
    structuredClone: global.structuredClone,
    console,
    $: id => elements[id],
    document: doc,
    window: win,
    localStorage: { getItem: () => null, setItem: () => {} },
    location: { reload: () => {} },
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
  });

  // Load app code up to schedule
  const scheduleIdx = appSource.indexOf('function schedule(');
  vm.runInContext(appSource.slice(0, scheduleIdx), context);
  vm.runInContext('let scheduledCount = 0; function schedule(opts) { scheduledCount++; }', context);

  // Load computeImpliedTaeg, coverage, payback cards, syncLoanControls through renderLoanPivot
  const syncLoanControlsIdx = appSource.indexOf('function computeImpliedTaeg(');
  const endLoanPivotIdx = appSource.indexOf('function persistLoanSimulations(');
  vm.runInContext(appSource.slice(syncLoanControlsIdx, endLoanPivotIdx), context);

  // Load metricCard
  const metricCardIdx = appSource.indexOf('function metricCard(');
  const renderIdx = appSource.indexOf('function render(){');
  vm.runInContext(appSource.slice(metricCardIdx, renderIdx), context);

  // Load chart SVG and chartTarget helpers
  const chartSvgIdx = appSource.indexOf('function chartSvg(');
  const renderEnergyChartsIdx = appSource.indexOf('function renderEnergyCharts()');
  vm.runInContext(appSource.slice(chartSvgIdx, renderEnergyChartsIdx), context);

  // Wire loanScopeControl event listeners
  const loanScopeWiringIdx = appSource.indexOf("$('loanScopeControl').onchange");
  const loanScopeWiringEnd = appSource.indexOf("$('solveLoanAmount').onclick");
  vm.runInContext(appSource.slice(loanScopeWiringIdx, loanScopeWiringEnd), context);

  // Initialize loan globals
  vm.runInContext('loanSaved = []; loanPending = false; acceptedInputFingerprint = null;', context);

  return { context, elements, switchContextElements, registeredInputs };
}

// ---------------------------------------------------------------------------
// Test 1: Solar summary label qualification
// ---------------------------------------------------------------------------
{
  const { context } = createTestContext();
  const mockResult = {
    settings: { cashflow_mode: 'real', inflation: 0.02, real_discount_rate: 0.03, load_scenario: 'electrified' },
    finance: {
      capex_eur: 10000,
      conversion_capex_eur: 4000,
      loan_principal_eur: 5000,
      solar_loan_principal_eur: 5000,
      cashflows_by_weather: {
        solar_equity: [[-5000, 600, 600]],
        package_equity: [[-9000, 1000, 1000]]
      }
    },
    metrics: {}
  };

  context.mockResult = mockResult;
  vm.runInContext('current = { result: mockResult };', context);
  const solarMetrics = vm.runInContext('getScopeCashMetrics(current.result, "solar", "loan")', context);
  const packageMetrics = vm.runInContext('getScopeCashMetrics(current.result, "package", "loan")', context);

  assert.equal(
    solarMetrics.capexTitle,
    'PERSONAL CASH OUTLAY (SOLAR ONLY · AFTER LOAN)',
    'Solar loan outlay label must be qualified as solar-only to distinguish from total package outlay'
  );
  assert.equal(
    packageMetrics.capexTitle,
    'PERSONAL CASH OUTLAY (AFTER LOAN)',
    'Whole-house loan outlay label remains full personal outlay'
  );
  console.log('✔ Solar vs Whole-house summary cash outlay labels are properly distinguished');
}

// ---------------------------------------------------------------------------
// Test 2: Active electrified scenario preserves full household scope when switching loan coverage
// ---------------------------------------------------------------------------
{
  const { context, elements } = createTestContext();
  vm.runInContext(`
    schema = {
      defaults: {
        loan_amount: 14000,
        loan_mode: 'fraction',
        loan_fraction: 1.0,
        loan_apr: 0.05,
        loan_years: 10,
        loan_include_electrification: true,
        load_scenario: 'electrified',
        installation_year: 2026,
        cashflow_mode: 'real',
        inflation: 0.02,
        real_discount_rate: 0.03
      },
      records: [
        { name: 'loan_amount', bounds: [0, 200000] },
        { name: 'loan_fraction', bounds: [0, 1] },
        { name: 'loan_include_electrification', group: 'finance' }
      ]
    };
    settings = { ...schema.defaults };
    quote = { line_items: [] };
    loanPending = false;
    acceptedInputFingerprint = null;
  `, context);

  // Result with electrification included in loan: capex=10000, conversion=4000, principal=14000
  const resultWithElectrification = {
    settings: {
      load_scenario: 'electrified',
      loan_include_electrification: true,
      loan_apr: 0.05,
      loan_years: 10,
      installation_year: 2026,
      cashflow_mode: 'real',
      inflation: 0.02
    },
    finance: {
      loan_scope: 'solar_and_conversion',
      capex_eur: 10000,
      conversion_capex_eur: 4000,
      loan_principal_eur: 14000,
      requested_loan_amount_eur: 14000,
      whole_package: { npv: { median: 8000 } },
      equity: {
        discounted_payback: { median: 8.5, recovered_weather_years: 19 },
        npv: { median: 7200 }
      },
      financed_project: {
        discounted_payback: { median: 9.0, recovered_weather_years: 19 },
        unpaid_discounted_weather_years: 0
      },
      package_equity: {
        npv: { median: 7200 },
        simple_payback: { median: 8.5, recovered_weather_years: 19 }
      },
      solar_equity: {
        npv: { median: 5500 },
        simple_payback: { median: 6.0, recovered_weather_years: 19 }
      },
      cashflows_by_weather: {
        package_equity: [[0, 800, 800, 800]],
        whole_package: [[-14000, 1600, 1600, 1600]],
        solar_equity: [[0, 500, 500, 500]],
        pv_incremental: [[-10000, 1000, 1000, 1000]]
      }
    },
    metrics: {
      package_npv_eur: { median: 8000, mean: 8000 },
      npv_eur: { median: 6000, mean: 6000 }
    }
  };

  context.resultWithElectrification = resultWithElectrification;
  vm.runInContext('current = { result: resultWithElectrification };', context);
  vm.runInContext('renderLoanPanel();', context);

  // Check initial state with electrification in loan:
  // cashNpv = 8000 (package), loanNpv = 7200 (package_equity), financingNpvCost = 800
  assert.match(elements.loanPayment.innerHTML, /FINANCING NPV IMPACT[\s\S]*?[−-]€800[\s\S]*?cost/, 'Full package financing cost is 8000 - 7200 = 800');
  assert.match(elements.loanMetricCards.innerHTML, /8\.5 y/, 'Recovery year uses package discounted payback (8.5 y)');
  assert.match(elements.loanCashChart.innerHTML, /Taking loan \(ensemble mean(?: NPV)?\)/);
  assert.match(elements.loanCashChart.innerHTML, /Paying cash \(ensemble mean(?: NPV)?\)/);

  // Now switch loan_include_electrification to false, so loan covers solar only (principal = 10000)
  // Household is STILL electrified (load_scenario = 'electrified').
  // Full household cost: solar capex 10000 + conversion 4000.
  // Equity outlay at year 0: -(14000) + 10000 = -4000 (user pays 4000 conversion cash).
  const resultSolarOnlyLoan = {
    settings: {
      load_scenario: 'electrified',
      loan_include_electrification: false,
      loan_apr: 0.05,
      loan_years: 10,
      installation_year: 2026,
      cashflow_mode: 'real',
      inflation: 0.02
    },
    finance: {
      loan_scope: 'solar',
      capex_eur: 10000,
      conversion_capex_eur: 4000,
      loan_principal_eur: 10000,
      requested_loan_amount_eur: 10000,
      whole_package: { npv: { median: 8000 } },
      equity: {
        discounted_payback: { median: 7.8, recovered_weather_years: 19 },
        npv: { median: 7450 }
      },
      financed_project: {
        discounted_payback: { median: 9.0, recovered_weather_years: 19 },
        unpaid_discounted_weather_years: 0
      },
      package_equity: {
        npv: { median: 7450 },
        simple_payback: { median: 7.8, recovered_weather_years: 19 }
      },
      solar_equity: {
        npv: { median: 5500 },
        simple_payback: { median: 6.0, recovered_weather_years: 19 }
      },
      cashflows_by_weather: {
        package_equity: [[-4000, 1000, 1000, 1000]],
        whole_package: [[-14000, 1600, 1600, 1600]],
        solar_equity: [[0, 500, 500, 500]],
        pv_incremental: [[-10000, 1000, 1000, 1000]],
        equity: [[-4000, 1000, 1000, 1000]]
      }
    },
    metrics: {
      package_npv_eur: { median: 8000, mean: 8000 },
      npv_eur: { median: 6000, mean: 6000 }
    }
  };

  context.resultSolarOnlyLoan = resultSolarOnlyLoan;
  vm.runInContext('current = { result: resultSolarOnlyLoan };', context);
  vm.runInContext('renderLoanPanel();', context);

  // Critically: Evaluated cash scope MUST STILL be whole package (8000), NOT solar (6000)!
  // financingNpvCost must be 8000 - 7450 = 550 €, NOT 6000 - 5500 = 500 €!
  assert.match(
    elements.loanPayment.innerHTML,
    /FINANCING NPV IMPACT[\s\S]*?[−-]€550[\s\S]*?cost/,
    'Financing cost remains evaluated against full package cash purchase (8000 - 7450 = 550)'
  );
  assert.doesNotMatch(
    elements.loanPayment.innerHTML,
    /FINANCING NPV IMPACT[\s\S]*?[−-]€500/,
    'Loan panel must NOT flip evaluated cash baseline to solar-only (6000 - 5500) when switching loan coverage'
  );
  assert.match(elements.loanMetricCards.innerHTML, /7\.8 y/, 'Recovery year continues to reflect whole-house equity payback (7.8 y)');
  console.log('✔ Electrified scenario consistently evaluates full household package scope across loan coverages');
}

// ---------------------------------------------------------------------------
// Test 3: Baseline scenario naturally evaluates solar scope
// ---------------------------------------------------------------------------
{
  const { context, elements } = createTestContext();
  const baselineResult = {
    settings: {
      load_scenario: 'baseline',
      loan_include_electrification: true, // Note: switch is true, but baseline has 0 conversion
      loan_apr: 0.05,
      loan_years: 10,
      installation_year: 2026,
      cashflow_mode: 'real',
      inflation: 0.02
    },
    finance: {
      loan_scope: 'solar_and_conversion',
      capex_eur: 10000,
      conversion_capex_eur: 0,
      loan_principal_eur: 10000,
      requested_loan_amount_eur: 10000,
      whole_package: { npv: { median: 6000 } },
      equity: {
        discounted_payback: { median: 6.0, recovered_weather_years: 19 },
        npv: { median: 5500 }
      },
      financed_project: {
        discounted_payback: { median: 7.0, recovered_weather_years: 19 },
        unpaid_discounted_weather_years: 0
      },
      package_equity: {
        npv: { median: 5500 },
        simple_payback: { median: 6.0, recovered_weather_years: 19 }
      },
      solar_equity: {
        npv: { median: 5500 },
        simple_payback: { median: 6.0, recovered_weather_years: 19 }
      },
      pv_incremental: { npv: { median: 6000 } },
      cashflows_by_weather: {
        solar_equity: [[0, 500, 500, 500]],
        package_equity: [[0, 500, 500, 500]],
        pv_incremental: [[-10000, 1000, 1000, 1000]],
        whole_package: [[-10000, 1000, 1000, 1000]]
      }
    },
    metrics: {
      npv_eur: { median: 6000 },
      package_npv_eur: { median: 6000 }
    }
  };

  context.baselineResult = baselineResult;
  vm.runInContext('current = { result: baselineResult };', context);
  vm.runInContext('renderLoanPanel();', context);

  // In baseline, conversion is naturally zero, so solar and package match (6000 - 5500 = 500)
  assert.match(elements.loanPayment.innerHTML, /FINANCING NPV IMPACT[\s\S]*?[−-]€500[\s\S]*?cost/);
  assert.match(elements.loanMetricCards.innerHTML, /6\.0 y/);
  console.log('✔ Baseline scenario naturally evaluates solar scope without error');
}

// ---------------------------------------------------------------------------
// Test 4: Switch boolean input handling & 2-way sync
// ---------------------------------------------------------------------------
{
  const { context, elements } = createTestContext();
  vm.runInContext(`
    schema = {
      defaults: {
        loan_include_electrification: false,
        load_scenario: 'electrified',
        loan_mode: 'amount',
        loan_amount: 5000
      },
      records: [
        { name: 'loan_include_electrification', group: 'finance' },
        { name: 'load_scenario', group: 'loads' },
        { name: 'loan_mode', group: 'finance' },
        { name: 'loan_amount', group: 'finance' }
      ]
    };
    settings = { ...schema.defaults };
    quote = { line_items: [] };
    renderControls();
  `, context);

  // Check initial state
  assert.equal(vm.runInContext('settings.loan_include_electrification', context), false);

  // 1. Trigger switch change via controls.oninput with checkbox checked = true
  const switchTarget = {
    type: 'checkbox',
    checked: true,
    value: 'on',
    dataset: { setting: 'loan_include_electrification' }
  };
  vm.runInContext('$("controls").oninput', context)({ target: switchTarget });
  assert.equal(vm.runInContext('settings.loan_include_electrification', context), true, 'controls oninput parses checkbox checked as boolean true');
  assert.equal(elements.loanScopeControl.value, 'solar_and_conversion', 'syncSetting synchronizes loanScopeControl');

  // 2. Trigger switch change via controls.onchange with checkbox checked = false
  switchTarget.checked = false;
  vm.runInContext('$("controls").onchange', context)({ target: switchTarget });
  assert.equal(vm.runInContext('settings.loan_include_electrification', context), false, 'controls onchange parses checkbox checked as boolean false');
  assert.equal(elements.loanScopeControl.value, 'solar', 'syncSetting synchronizes loanScopeControl to solar');

  // 3. Trigger loanScopeControl onchange in loan panel -> should editSetting and update settings
  elements.loanScopeControl.value = 'solar_and_conversion';
  elements.loanScopeControl.onchange({ target: elements.loanScopeControl });
  assert.equal(vm.runInContext('settings.loan_include_electrification', context), true, 'loanScopeControl onchange updates loan_include_electrification to true');

  elements.loanScopeControl.value = 'solar';
  elements.loanScopeControl.onchange({ target: elements.loanScopeControl });
  assert.equal(vm.runInContext('settings.loan_include_electrification', context), false, 'loanScopeControl onchange updates loan_include_electrification to false');

  console.log('✔ Switch boolean input handling and bidirectional sync pass');
}

// ---------------------------------------------------------------------------
// Test 5: Contextual no-effect cases (baseline and amount mode below caps)
// ---------------------------------------------------------------------------
{
  const { context } = createTestContext();

  // Case A: Baseline scenario
  const textBaseline = vm.runInContext('getLoanScopeContextText({ load_scenario: "baseline" })', context);
  assert.match(
    textBaseline,
    /Baseline load has no electrification costs to finance/i,
    'Baseline context explains there are no electrification costs to finance'
  );
  assert.match(
    textBaseline,
    /changes eligible borrowing cost, not total equipment cost/i,
    'Baseline context notes electrified load changes eligible borrowing cost'
  );

  // Case B: Electrified scenario, amount mode, requested loan amount <= solar capex (below both caps)
  const mockElectrifiedResult = {
    settings: { load_scenario: 'electrified', loan_mode: 'amount', loan_amount: 5000 },
    finance: { capex_eur: 10000, conversion_capex_eur: 4000 }
  };
  context.mockElectrifiedResult = mockElectrifiedResult;
  vm.runInContext('current = { result: mockElectrifiedResult };', context);
  const textAmountBelow = vm.runInContext(
    'getLoanScopeContextText({ load_scenario: "electrified", loan_mode: "amount", loan_amount: 5000 }, current.result)',
    context
  );
  assert.match(
    textAmountBelow,
    /below both caps, so scope leaves principal and payment unchanged/i,
    'Amount below caps context explains principal and payment remain unchanged'
  );
  assert.match(
    textAmountBelow,
    /changes eligible borrowing cost, not total equipment cost/i,
    'Explains scope changes eligible borrowing capacity, not equipment cost'
  );

  // Case C: Electrified scenario, fraction mode
  const textFraction = vm.runInContext(
    'getLoanScopeContextText({ load_scenario: "electrified", loan_mode: "fraction", loan_fraction: 1 }, current.result)',
    context
  );
  assert.match(
    textFraction,
    /With an electrified load, loan scope changes eligible borrowing cost, not total equipment cost/i,
    'Fraction mode explains scope changes eligible borrowing cost'
  );

  console.log('✔ Contextual helper explains baseline no-cost and amount-below-caps invariants truthfully');
}

// ---------------------------------------------------------------------------
// Test 6: Baseline scenario with optional electrification cost selects package personal paths
// ---------------------------------------------------------------------------
{
  const { context, elements } = createTestContext();
  const baselineWithCostResult = {
    settings: {
      load_scenario: 'baseline',
      electrification_capex_include_baseline: true,
      loan_include_electrification: true,
      loan_apr: 0.05,
      loan_years: 10,
      installation_year: 2026,
      cashflow_mode: 'real',
      inflation: 0.02
    },
    finance: {
      loan_scope: 'solar_and_conversion',
      capex_eur: 10000,
      conversion_capex_eur: 6000,
      loan_principal_eur: 16000,
      requested_loan_amount_eur: 16000,
      whole_package: { npv: { median: 8000 } },
      equity: {
        discounted_payback: { median: 8.5, recovered_weather_years: 19 },
        npv: { median: 7200 }
      },
      financed_project: {
        discounted_payback: { median: 9.0, recovered_weather_years: 19 },
        unpaid_discounted_weather_years: 0
      },
      package_equity: {
        npv: { median: 7200 },
        simple_payback: { median: 8.5, recovered_weather_years: 19 }
      },
      solar_equity: {
        npv: { median: 5500 },
        simple_payback: { median: 6.0, recovered_weather_years: 19 }
      },
      pv_incremental: { npv: { median: 6000 } },
      cashflows_by_weather: {
        package_equity: [[0, 800, 800, 800]],
        whole_package: [[-16000, 1600, 1600, 1600]],
        solar_equity: [[0, 500, 500, 500]],
        pv_incremental: [[-10000, 1000, 1000, 1000]]
      }
    },
    metrics: {
      package_npv_eur: { median: 8000, mean: 8000 },
      npv_eur: { median: 6000, mean: 6000 }
    }
  };

  context.baselineWithCostResult = baselineWithCostResult;
  vm.runInContext('current = { result: baselineWithCostResult };', context);
  vm.runInContext('renderLoanPanel();', context);

  // When conversion_capex_eur > 0, baseline evaluates whole package cash purchase (8000 - 7200 = 800 €)
  assert.match(
    elements.loanPayment.innerHTML,
    /FINANCING NPV IMPACT[\s\S]*?[−-]€800[\s\S]*?cost/,
    'Financing cost evaluated against package cash purchase (8000 - 7200 = 800)'
  );
  assert.match(elements.loanMetricCards.innerHTML, /8\.5 y/, 'Recovery year reflects package equity payback (8.5 y)');
  console.log('✔ Baseline scenario with optional electrification capex selects package personal paths');
}

// ---------------------------------------------------------------------------
// Test 7: Baseline electrification CAPEX switch placement, sync and context helper
// ---------------------------------------------------------------------------
{
  const { context, elements } = createTestContext();
  vm.runInContext(`
    schema = {
      defaults: {
        electrification_capex: 6000,
        electrification_capex_include_baseline: false,
        load_scenario: 'baseline'
      },
      records: [
        { name: 'electrification_capex', unit: 'EUR', bounds: [0, 20000], group: 'loads' },
        { name: 'electrification_capex_include_baseline', unit: 'boolean', group: 'loads' },
        { name: 'load_scenario', unit: 'scenario', bounds: ['baseline', 'electrified', 'ev'], group: 'loads' }
      ]
    };
    settings = { ...schema.defaults };
    quote = { line_items: [] };
    renderControls();
  `, context);

  // Placement check: switch is inside Electrification CAPEX control
  const capexHtml = vm.runInContext('control(schema.records[0])', context);
  assert.match(capexHtml, /data-control="electrification_capex"/);
  assert.match(capexHtml, /<label class="switch">/);
  assert.match(capexHtml, /data-setting="electrification_capex_include_baseline"/);
  assert.match(capexHtml, /<span>Include in baseline scenario\?<\/span>/);
  assert.match(capexHtml, /aria-label="Include in baseline scenario\?"/);

  // Compact rendering retains the switch
  const compactHtml = vm.runInContext('control(schema.records[0], true)', context);
  assert.match(compactHtml, /data-setting="electrification_capex_include_baseline"/);

  // No duplicate standalone control
  assert.equal(
    (elements.controls.innerHTML.match(/data-control="electrification_capex_include_baseline"/g) || []).length,
    0
  );

  // Real checkbox event updates setting
  const switchTarget = {
    type: 'checkbox',
    checked: true,
    value: 'on',
    dataset: { setting: 'electrification_capex_include_baseline' }
  };
  vm.runInContext('$("controls").oninput', context)({ target: switchTarget });
  assert.equal(vm.runInContext('settings.electrification_capex_include_baseline', context), true);

  // Context text when flag is on in baseline: explains switch in plain words without saying no costs
  const textOn = vm.runInContext('getLoanScopeContextText(settings, current?.result)', context);
  assert.doesNotMatch(textOn, /no electrification costs/i);
  assert.match(textOn, /With baseline electrification capex included, loan scope changes eligible borrowing cost, not total equipment cost/i);

  // Reset setting
  vm.runInContext('resetSetting("electrification_capex_include_baseline")', context);
  assert.equal(vm.runInContext('settings.electrification_capex_include_baseline', context), false);

  console.log('✔ Baseline electrification CAPEX switch placement, sync and context helper pass');
}

console.log('\nAll focused loan panel scope UI behavioral tests passed successfully.');
