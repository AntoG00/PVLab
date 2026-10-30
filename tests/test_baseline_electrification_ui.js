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
    modifiedCountBadge: { textContent: '', className: '' },
    activeTabAssumptionsPill: { textContent: '' },
    status: { textContent: '' },
    error: { hidden: true }
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

  const chartCalls = [];
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
    chartCalls
  });

  const scheduleIdx = appSource.indexOf('function schedule(');
  vm.runInContext(appSource.slice(0, scheduleIdx), context);
  vm.runInContext('let scheduledCount = 0; function schedule(opts) { scheduledCount++; }', context);

  const syncLoanControlsIdx = appSource.indexOf('function computeImpliedTaeg(');
  const endLoanPivotIdx = appSource.indexOf('function persistLoanSimulations(');
  assert.ok(syncLoanControlsIdx >= 0 && endLoanPivotIdx > syncLoanControlsIdx, 'Loan helper block must exist');
  vm.runInContext(appSource.slice(syncLoanControlsIdx, endLoanPivotIdx), context);

  const metricCardIdx = appSource.indexOf('function metricCard(');
  const renderIdx = appSource.indexOf('function render(){');
  vm.runInContext(appSource.slice(metricCardIdx, renderIdx), context);

  const chartSvgIdx = appSource.indexOf('function chartSvg(');
  const renderEnergyChartsIdx = appSource.indexOf('function renderEnergyCharts()');
  vm.runInContext(appSource.slice(chartSvgIdx, renderEnergyChartsIdx), context);

  const loanScopeWiringIdx = appSource.indexOf("$('loanScopeControl').onchange");
  const loanScopeWiringEnd = appSource.indexOf("$('solveLoanAmount').onclick");
  vm.runInContext(appSource.slice(loanScopeWiringIdx, loanScopeWiringEnd), context);

  vm.runInContext('loanSaved = []; loanPending = false; acceptedInputFingerprint = null;', context);

  vm.runInContext('const origRenderCashChart = renderCashChart; renderCashChart = (r, target, mode) => { chartCalls.push({ target, mode }); return origRenderCashChart(r, target, mode); };', context);

  return { context, elements, switchContextElements, registeredInputs, chartCalls };
}

// ---------------------------------------------------------------------------
// Test 1: Checkbox switch placement and exact labelling
// ---------------------------------------------------------------------------
{
  const { context } = createTestContext();
  vm.runInContext(`
    schema = {
      defaults: {
        electrification_capex: 6000,
        electrification_capex_include_baseline: false
      },
      records: [
        { name: 'electrification_capex', unit: 'EUR', bounds: [0, 20000], group: 'loads' },
        { name: 'electrification_capex_include_baseline', unit: 'boolean', group: 'loads' }
      ]
    };
    settings = { ...schema.defaults };
  `, context);

  const capexRecord = vm.runInContext('schema.records[0]', context);
  const html = vm.runInContext('control(schema.records[0])', context);

  // Switch must be within the Electrification CAPEX control
  assert.match(html, /data-control="electrification_capex"/, 'Control container is for electrification_capex');
  assert.match(html, /<label class="switch">/, 'Uses existing .switch styling');
  assert.match(html, /role="switch"/, 'Input has role="switch"');
  assert.match(html, /data-setting="electrification_capex_include_baseline"/, 'Input binds to electrification_capex_include_baseline');
  assert.match(html, /<span>Include in baseline scenario\?<\/span>/, 'Label is exactly "Include in baseline scenario?"');
  assert.match(html, /aria-label="Include in baseline scenario\?"/, 'aria-label is exactly "Include in baseline scenario?"');
  assert.match(html, /<span class="switch-track" aria-hidden="true"><\/span>/, 'Includes switch-track element');

  // Compact rendering (pinned controls) also includes the switch
  const compactHtml = vm.runInContext('control(schema.records[0], true)', context);
  assert.match(compactHtml, /data-setting="electrification_capex_include_baseline"/, 'Compact rendering retains the baseline switch');

  console.log('✔ Checkbox switch placement, exact labelling and compact rendering passed');
}

// ---------------------------------------------------------------------------
// Test 2: No duplicate standalone control in renderControls
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

  const renderedHtml = elements.controls.innerHTML;
  // It should contain the switch within electrification_capex
  assert.match(renderedHtml, /data-setting="electrification_capex_include_baseline"/, 'Switch exists in rendered controls');
  // It should NOT contain a standalone duplicate control container
  assert.equal(
    (renderedHtml.match(/data-control="electrification_capex_include_baseline"/g) || []).length,
    0,
    'No duplicate standalone control container for electrification_capex_include_baseline'
  );

  console.log('✔ No duplicate standalone control far away');
}

// ---------------------------------------------------------------------------
// Test 3: Real checkbox events and bidirectional synchronization
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

  assert.equal(vm.runInContext('settings.electrification_capex_include_baseline', context), false);

  // 1. Simulate user checking the switch
  const switchInput = {
    type: 'checkbox',
    checked: true,
    value: 'on',
    dataset: { setting: 'electrification_capex_include_baseline' }
  };
  vm.runInContext('$("controls").oninput', context)({ target: switchInput });
  assert.equal(
    vm.runInContext('settings.electrification_capex_include_baseline', context),
    true,
    'Checkbox check event updates setting to true'
  );

  // 2. Simulate user unchecking the switch via onchange
  switchInput.checked = false;
  vm.runInContext('$("controls").onchange', context)({ target: switchInput });
  assert.equal(
    vm.runInContext('settings.electrification_capex_include_baseline', context),
    false,
    'Checkbox uncheck event updates setting to false'
  );

  // 3. syncSetting updates DOM input elements
  const mockDomInput1 = { dataset: { setting: 'electrification_capex_include_baseline' }, type: 'checkbox', checked: false };
  const mockDomInput2 = { dataset: { setting: 'electrification_capex_include_baseline' }, type: 'checkbox', checked: false };
  const registered = [mockDomInput1, mockDomInput2];
  context.document.querySelectorAll = sel => sel.includes('data-setting') ? registered : [];

  vm.runInContext('settings.electrification_capex_include_baseline = true; syncSetting("electrification_capex_include_baseline");', context);
  assert.equal(mockDomInput1.checked, true, 'First input synchronized to true');
  assert.equal(mockDomInput2.checked, true, 'Second input synchronized to true');

  // 4. Reset setting restores default False
  vm.runInContext('resetSetting("electrification_capex_include_baseline");', context);
  assert.equal(
    vm.runInContext('settings.electrification_capex_include_baseline', context),
    false,
    'resetSetting restores default false'
  );
  assert.equal(mockDomInput1.checked, false, 'Input synchronized back to false after reset');

  // 5. Resetting parent electrification_capex also resets the switch
  vm.runInContext('settings.electrification_capex = 9000; settings.electrification_capex_include_baseline = true;', context);
  vm.runInContext('resetSetting("electrification_capex");', context);
  assert.equal(vm.runInContext('settings.electrification_capex', context), 6000);
  assert.equal(vm.runInContext('settings.electrification_capex_include_baseline', context), false);

  console.log('✔ Real checkbox events and bidirectional synchronization passed');
}

// ---------------------------------------------------------------------------
// Test 4: renderLoanPanel selects package personal paths when baseline conversion cost > 0
// ---------------------------------------------------------------------------
{
  const { context, elements, chartCalls } = createTestContext();
  const baselineResultWithCapex = {
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
      conversion_capex_eur: 6000, // Switch is ON in baseline!
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
      package_npv_eur: { median: 8000 },
      npv_eur: { median: 6000 }
    }
  };

  context.baselineResultWithCapex = baselineResultWithCapex;
  vm.runInContext('current = { result: baselineResultWithCapex };', context);
  vm.runInContext('renderLoanPanel();', context);

  // When conversion_capex_eur > 0 in baseline, renderLoanPanel must evaluate package personal paths!
  // cashNpv = 8000 (package), loanNpv = 7200 (package_equity), financingNpvCost = 800 € (NOT 6000 - 5500 = 500 €)
  assert.match(
    elements.loanPayment.innerHTML,
    /FINANCING NPV IMPACT[\s\S]*?−€800/,
    'Financing cost evaluated against whole package cash purchase (8000 - 7200 = 800 €)'
  );
  assert.match(
    elements.loanMetricCards.innerHTML,
    /DISCOUNTED PERSONAL CASH RECOVERY[\s\S]*?8\.5 y/,
    'Personal recovery card uses the discounted 8.5-year value'
  );

  // Cash chart must render package_equity, not solar_equity
  const lastChart = chartCalls[chartCalls.length - 1];
  assert.equal(lastChart.mode, 'package_equity', 'renderCashChart selects package_equity when baseline has conversion cost');

  console.log('✔ renderLoanPanel selects package personal paths when baseline conversion cost > 0');
}

// ---------------------------------------------------------------------------
// Test 5: getLoanScopeContextText updates for baseline when flag is on
// ---------------------------------------------------------------------------
{
  const { context } = createTestContext();

  // Baseline with flag OFF: says baseline has no electrification costs
  const textOff = vm.runInContext(
    'getLoanScopeContextText({ load_scenario: "baseline", electrification_capex_include_baseline: false })',
    context
  );
  assert.match(
    textOff,
    /Baseline load has no electrification costs to finance/i,
    'Flag OFF explains baseline has no electrification costs'
  );

  // Baseline with flag ON: no longer says baseline lacks costs! Explains switch in short plain wording
  const textOn = vm.runInContext(
    'getLoanScopeContextText({ load_scenario: "baseline", electrification_capex_include_baseline: true, loan_mode: "fraction" })',
    context
  );
  assert.doesNotMatch(
    textOn,
    /no electrification costs/i,
    'Flag ON must not say baseline lacks electrification costs'
  );
  assert.match(
    textOn,
    /With baseline electrification capex included, loan scope changes eligible borrowing cost, not total equipment cost/i,
    'Flag ON explains baseline includes electrification capex'
  );

  // Baseline with flag ON in amount mode below caps
  const mockResult = {
    settings: { load_scenario: 'baseline', electrification_capex_include_baseline: true, loan_mode: 'amount', loan_amount: 5000 },
    finance: { capex_eur: 10000, conversion_capex_eur: 6000 }
  };
  context.mockResult = mockResult;
  vm.runInContext('current = { result: mockResult };', context);
  const textAmountBelow = vm.runInContext(
    'getLoanScopeContextText({ load_scenario: "baseline", electrification_capex_include_baseline: true, loan_mode: "amount", loan_amount: 5000 }, current.result)',
    context
  );
  assert.match(
    textAmountBelow,
    /below both caps, so scope leaves principal and payment unchanged/i,
    'Amount below caps context explains principal and payment remain unchanged'
  );
  assert.match(
    textAmountBelow,
    /With baseline electrification capex included/i,
    'Mentions baseline electrification capex included'
  );

  console.log('✔ getLoanScopeContextText truthfully updates for baseline with switch enabled');
}

console.log('\nAll focused baseline electrification UI tests passed successfully.');
