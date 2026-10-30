'use strict';
/*
 * Unit test suite for monthly loan discounting contract across UI consumers:
 * - Independent explicit monthly loan oracle (loan 14999.72, fee 16, APR 0.051, term 10, rnom 0.0506)
 * - buildSummaryCashRows NPV totals and debt component reconciliation
 * - renderCashChart discounted matched loan vs cash comparison endpoints
 * - currentLoanMetrics year20 and financing top card
 * - Real-mode invariance (inflation 0.02, real discount 0.03, nominal discount 0.0506)
 * - Undiscounted views and coverage preserving genuine nominal flows
 * - Legacy snapshot backward compatibility fallback
 * - Mixed solar / package scope support
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appPath = [
  path.join(__dirname, '../feasibility/web/app.js'),
  path.join(process.cwd(), 'feasibility/web/app.js')
].find(p => p && fs.existsSync(p));
const appSource = fs.readFileSync(appPath, 'utf8');

function createHarness() {
  const elements = {};
  const getElem = id => {
    if (!elements[id]) {
      const classSet = new Set();
      const attrs = new Map();
      elements[id] = {
        id, innerHTML: '', textContent: '', value: '', open: false, hidden: false, disabled: false,
        classList: {
          add: c => classSet.add(c), remove: c => classSet.delete(c), contains: c => classSet.has(c),
          toggle: (c, v) => (v !== undefined ? v : !classSet.has(c)) ? classSet.add(c) : classSet.delete(c)
        },
        setAttribute: (k, v) => attrs.set(k, String(v)), getAttribute: k => attrs.get(k) || null,
        querySelector: () => null, querySelectorAll: () => [], addEventListener: () => {}, dispatchEvent: () => {},
        closest: () => ({ hidden: false }), validity: { valid: true }
      };
    }
    return elements[id];
  };

  const doc = {
    getElementById: getElem, querySelector: () => null, querySelectorAll: () => [],
    addEventListener: () => {}, removeEventListener: () => {},
    createElement: () => ({ setAttribute: () => {}, style: {}, classList: { add: () => {}, remove: () => {} }, appendChild: () => {} }),
    body: { appendChild: () => {}, querySelector: () => null, addEventListener: () => {} }, activeElement: null
  };
  const win = { addEventListener: () => {}, removeEventListener: () => {}, innerWidth: 1024, innerHeight: 768, document: doc };
  win.window = win;

  const context = vm.createContext({
    console, $: getElem, document: doc, window: win,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout: fn => fn(), clearTimeout: () => {},
    Math, Number, String, Date, Array, JSON, parseInt, parseFloat, isFinite,
    structuredClone: globalThis.structuredClone || (x => JSON.parse(JSON.stringify(x)))
  });

  vm.runInContext(appSource.slice(0, appSource.indexOf('function schedule(')), context);
  vm.runInContext('let scheduledCount = 0; function schedule() { scheduledCount++; }', context);
  vm.runInContext(appSource.slice(appSource.indexOf('function computeImpliedTaeg('), appSource.indexOf('function persistLoanSimulations(')), context);
  vm.runInContext(appSource.slice(appSource.indexOf('function metricCard('), appSource.indexOf('function render(){')), context);
  vm.runInContext(appSource.slice(appSource.indexOf('function chartSvg('), appSource.indexOf('function renderEnergyCharts()')), context);
  vm.runInContext('loanSaved = []; loanPending = false; acceptedInputFingerprint = null;', context);

  const fmt = vm.runInContext('fmt', context);
  const nf = vm.runInContext('nf', context);

  return { context, elements, $: getElem, fmt, nf };
}

// 1. Independent explicit monthly loan oracle
function computeMonthlyOracle({ principal, fee, apr, termYears, horizonYears, rnom }) {
  const amortized = principal + fee, n = termYears * 12, rm = apr / 12.0;
  const monthly = amortized * rm / (1.0 - Math.pow(1.0 + rm, -n)), dfFactor = 1.0 + rnom;
  const actualDebt = Array(horizonYears).fill(0), equivDebt = Array(horizonYears).fill(0);
  let bal = amortized;
  for (let y = 0; y < horizonYears; y++) {
    for (let m = 1; m <= 12; m++) {
      if (y * 12 + (m - 1) < n) {
        const pmt = Math.min(monthly, bal * (1.0 + rm));
        bal = Math.max(bal + bal * rm - pmt, 0);
        actualDebt[y] += pmt;
        equivDebt[y] += pmt * Math.pow(dfFactor, (12 - m) / 12.0);
      }
    }
  }
  let pvDebt = 0;
  for (let y = 0; y < horizonYears; y++) pvDebt += equivDebt[y] / Math.pow(dfFactor, y + 1);
  return { monthly, annualNominal: actualDebt[0], actualDebt, equivDebt, pvDebt, netFinancingCost: pvDebt - principal };
}

const FIXTURE = { principal: 14999.72, fee: 16.0, apr: 0.051, termYears: 10, horizonYears: 20, rnom: 0.0506, unleveredNpv: 13139.0, expectedLeveredNpv: 13016.3166 };
const oracle = computeMonthlyOracle(FIXTURE);

assert.ok(Math.abs(oracle.monthly - 160.0) < 0.01 && Math.abs(oracle.annualNominal - 1920.0) < 0.05);
assert.ok(Math.abs(oracle.pvDebt - 15122.40) < 0.05 && Math.abs(oracle.netFinancingCost - 122.68) < 0.05);
assert.ok(Math.abs((FIXTURE.unleveredNpv - oracle.netFinancingCost) - FIXTURE.expectedLeveredNpv) < 0.01);

const annuityFactor20 = (1.0 - Math.pow(1.0 + FIXTURE.rnom, -20)) / FIXTURE.rnom;
const annualOperatingCash = (FIXTURE.unleveredNpv + FIXTURE.principal) / annuityFactor20;
const projectPath = [-FIXTURE.principal, ...Array(20).fill(annualOperatingCash)];
const nominalEquityPath = [0, ...Array(10).fill(annualOperatingCash - oracle.annualNominal), ...Array(10).fill(annualOperatingCash)];
const discEquityPath = [0, ...oracle.equivDebt.map((eq, y) => y < 10 ? (annualOperatingCash - eq) : annualOperatingCash)];

const cashRows = Array.from({ length: 21 }, (_, y) => ({
  year: y, pv_incremental: projectPath[y], whole_package: projectPath[y], equity: nominalEquityPath[y], package_equity: nominalEquityPath[y], solar_equity: nominalEquityPath[y],
  debt_service: y === 0 ? 0 : (y <= 10 ? oracle.annualNominal : 0),
  debt_service_year_end_equivalent: y === 0 ? 0 : (y <= 10 ? oracle.equivDebt[y - 1] : 0),
  loan_interest: y === 0 ? 0 : (y <= 10 ? 250 : 0), tax_deduction: 0, replacement: 0
}));

const mockResult = {
  settings: { load_scenario: 'electrified', cashflow_mode: 'nominal', real_discount_rate: FIXTURE.rnom, inflation: 0.0, horizon_years: 20, loan_mode: 'amount', loan_amount: FIXTURE.principal, loan_apr: FIXTURE.apr, loan_years: FIXTURE.termYears, loan_include_electrification: true },
  finance: {
    capex_eur: 10000.0, conversion_capex_eur: 4999.72, loan_principal_eur: FIXTURE.principal, loan_upfront_fees_or_tax_eur: FIXTURE.fee, loan_amortized_principal_eur: FIXTURE.principal + FIXTURE.fee, loan_monthly_payment_eur: oracle.monthly, loan_scope: 'solar_and_conversion', nominal_discount_rate: FIXTURE.rnom, real_discount_rate: FIXTURE.rnom,
    whole_package: { npv: { median: FIXTURE.unleveredNpv } }, package_equity: { npv: { median: FIXTURE.expectedLeveredNpv } }, equity: { discounted_payback: { median: 8.0 } },
    cashflows_by_weather: { pv_incremental: [projectPath, projectPath], whole_package: [projectPath, projectPath], equity: [nominalEquityPath, nominalEquityPath], package_equity: [nominalEquityPath, nominalEquityPath] },
    cashflows_for_discounting_by_weather: { pv_incremental: [projectPath, projectPath], whole_package: [projectPath, projectPath], equity: [discEquityPath, discEquityPath], package_equity: [discEquityPath, discEquityPath] }
  },
  metrics: { package_npv_eur: { median: FIXTURE.unleveredNpv }, npv_eur: { median: FIXTURE.unleveredNpv } }, annual_cashflows: cashRows
};

// 2. buildSummaryCashRows with cashflows_for_discounting_by_weather & reconciliation
{
  const { context } = createHarness();
  context.mockResult = mockResult;
  vm.runInContext('summaryScope = "package"; summaryCash = "loan"; current = { result: mockResult }; settings = { ...mockResult.settings };', context);
  const data = context.buildSummaryCashRows(mockResult, cashRows);
  assert.ok(Math.abs(data.rows[1].debtService - 1920.0) < 0.05);
  const row1Eq = data.rows[1].equity?.median ?? data.rows[1].equity;
  assert.ok(Math.abs(row1Eq - (annualOperatingCash - 1920.0)) < 0.05);
  assert.ok(Math.abs(data.netCashTotals.debtService - (10 * oracle.annualNominal - FIXTURE.principal)) < 0.05);
  assert.ok(Math.abs(data.npvTotals.wholeHouse.median - 13139.0) < 0.05);
  assert.ok(Math.abs(data.npvTotals.debtService - oracle.netFinancingCost) < 0.05);
  assert.ok(Math.abs(data.npvTotals.equity.median - 13016.3166) < 0.05);
  assert.ok(Math.abs((data.npvTotals.wholeHouse.median - data.npvTotals.debtService) - data.npvTotals.equity.median) < 0.01);
}

// 3. Real-mode invariance (inflation 0.02, real rate 0.03 -> nominal 0.0506)
{
  const { context } = createHarness();
  const inflation = 0.02, realRate = 0.03, defl = yr => Math.pow(1 + inflation, yr);
  const rawRealCashRows = cashRows.map(c => {
    const yr = c.year, factor = defl(yr);
    return { ...c, display_debt_service: c.debt_service / factor, display_debt_service_year_end_equivalent: c.debt_service_year_end_equivalent / factor, display_pv_incremental: c.pv_incremental / factor, display_whole_package: c.whole_package / factor, display_package_equity: c.package_equity / factor };
  });
  const realCashRows = context.displayCashRows(rawRealCashRows);
  const mockResultReal = { ...mockResult, settings: { ...mockResult.settings, cashflow_mode: 'real', inflation, real_discount_rate: realRate }, finance: { ...mockResult.finance, real_discount_rate: realRate, nominal_discount_rate: FIXTURE.rnom }, annual_cashflows: realCashRows };
  context.mockResultReal = mockResultReal;
  vm.runInContext('summaryScope = "package"; summaryCash = "loan"; current = { result: mockResultReal }; settings = { ...mockResultReal.settings };', context);
  const dataReal = context.buildSummaryCashRows(mockResultReal, realCashRows);
  assert.ok(Math.abs(dataReal.npvTotals.wholeHouse.median - 13139.0) < 0.05);
  assert.ok(Math.abs(dataReal.npvTotals.debtService - oracle.netFinancingCost) < 0.05);
  assert.ok(Math.abs(dataReal.npvTotals.equity.median - 13016.3166) < 0.05);
  assert.ok(Math.abs((dataReal.npvTotals.wholeHouse.median - dataReal.npvTotals.debtService) - dataReal.npvTotals.equity.median) < 0.01);
}

// 4. renderCashChart matched comparison loan vs cash endpoints
{
  const { context, elements, fmt, nf } = createHarness();
  context.mockResult = mockResult;
  vm.runInContext('summaryScope = "package"; summaryCash = "loan"; current = { result: mockResult }; settings = { ...mockResult.settings };', context);
  context.renderCashChart(mockResult, 'loanCashChart', 'package_equity', 'discounted');
  assert.match(elements.loanInsights.innerHTML, /13,016/);
  assert.match(elements.loanInsights.innerHTML, /13,139/);

  context.renderCashChart(mockResult, 'loanCashChart', 'package_equity', 'undiscounted');
  const nominalEquitySum = nominalEquityPath.reduce((a, b) => a + b, 0);
  const formattedNominalSum = fmt(Math.round(nominalEquitySum), 'eur');
  const numOnly = nf(Math.round(nominalEquitySum));
  assert.ok(elements.loanInsights.innerHTML.includes(formattedNominalSum) || elements.loanInsights.innerHTML.includes(numOnly));
}

// 5. currentLoanMetrics year20 and renderLoanPanel financing top card
{
  const { context, elements } = createHarness();
  context.mockResult = mockResult;
  vm.runInContext('summaryScope = "package"; summaryCash = "loan"; current = { result: mockResult }; settings = { ...mockResult.settings };', context);
  const metrics = context.currentLoanMetrics(mockResult);
  assert.ok(metrics && metrics.initial === 0 && metrics.horizon === 20);
  assert.ok(Math.abs(metrics.year20 - 13016.3166) < 0.05);
  context.renderLoanPanel();
  assert.match(elements.loanPayment.innerHTML, /FINANCING NPV IMPACT/);
  assert.match(elements.loanPayment.innerHTML, /[−-]€123/);
}

// 6. Legacy snapshot fallback & equivalent-debt reconstruction
{
  const { context, elements } = createHarness();
  const projectPathBefore = JSON.stringify(projectPath);
  const nominalEquityPathBefore = JSON.stringify(nominalEquityPath);

  const legacyCashRows = cashRows.map(({ debt_service_year_end_equivalent, ...rest }) => rest);
  const legacyResult = {
    ...mockResult,
    finance: { ...mockResult.finance, cashflows_for_discounting_by_weather: undefined },
    annual_cashflows: legacyCashRows
  };
  context.legacyResult = legacyResult;
  vm.runInContext('summaryScope = "package"; summaryCash = "loan"; current = { result: legacyResult }; settings = { ...legacyResult.settings };', context);

  // 6a. Helper returns object with scope keys; verify reconstructed package_equity flows
  const reconScopeFlows = context.getDiscountingCashflows(legacyResult);
  assert.ok(reconScopeFlows && typeof reconScopeFlows === 'object');
  const reconFlows = reconScopeFlows.package_equity;
  assert.ok(reconFlows && reconFlows.length > 0);
  assert.equal(reconFlows[0][0], 0); // loanPrincipal0: net proceeds at t=0, no timing correction
  for (let y = 1; y <= 10; y++) {
    assert.ok(Math.abs(reconFlows[0][y] - (annualOperatingCash - oracle.equivDebt[y - 1])) < 1e-4);
  }

  // 6b. buildSummaryCashRows actual legacy NPV totals 13016.3166 / cash 13139 and debt 122.68
  const data = context.buildSummaryCashRows(legacyResult, legacyCashRows);
  assert.ok(Math.abs(data.npvTotals.wholeHouse.median - 13139.0) < 0.05);
  assert.ok(Math.abs(data.npvTotals.debtService - 122.68) < 0.05);
  assert.ok(Math.abs(data.npvTotals.equity.median - 13016.3166) < 0.05);
  assert.ok(Math.abs((data.npvTotals.wholeHouse.median - data.npvTotals.debtService) - data.npvTotals.equity.median) < 0.01);

  // 6c. Real-mode legacy totals invariance
  const inflation = 0.02, realRate = 0.03, defl = yr => Math.pow(1 + inflation, yr);
  const rawRealLegacyCashRows = legacyCashRows.map(c => {
    const yr = c.year, factor = defl(yr);
    return {
      ...c,
      display_debt_service: (c.debt_service || 0) / factor,
      display_pv_incremental: (c.pv_incremental || 0) / factor,
      display_whole_package: (c.whole_package || 0) / factor,
      display_package_equity: (c.package_equity || 0) / factor
    };
  });
  const realLegacyCashRows = context.displayCashRows ? context.displayCashRows(rawRealLegacyCashRows) : rawRealLegacyCashRows;
  const mockRealLegacyResult = {
    ...legacyResult,
    settings: { ...legacyResult.settings, cashflow_mode: 'real', inflation, real_discount_rate: realRate },
    finance: { ...legacyResult.finance, real_discount_rate: realRate, nominal_discount_rate: FIXTURE.rnom },
    annual_cashflows: realLegacyCashRows
  };
  context.mockRealLegacyResult = mockRealLegacyResult;
  vm.runInContext('summaryScope = "package"; summaryCash = "loan"; current = { result: mockRealLegacyResult }; settings = { ...mockRealLegacyResult.settings };', context);
  const dataRealLegacy = context.buildSummaryCashRows(mockRealLegacyResult, realLegacyCashRows);
  assert.ok(Math.abs(dataRealLegacy.npvTotals.wholeHouse.median - 13139.0) < 0.05);
  assert.ok(Math.abs(dataRealLegacy.npvTotals.debtService - 122.68) < 0.05);
  assert.ok(Math.abs(dataRealLegacy.npvTotals.equity.median - 13016.3166) < 0.05);
  assert.ok(Math.abs((dataRealLegacy.npvTotals.wholeHouse.median - dataRealLegacy.npvTotals.debtService) - dataRealLegacy.npvTotals.equity.median) < 0.01);

  // 6d. currentLoanMetrics computes 13016.3166 on legacy result
  vm.runInContext('current = { result: legacyResult }; settings = { ...legacyResult.settings };', context);
  const metrics = context.currentLoanMetrics(legacyResult);
  assert.ok(metrics && metrics.initial === 0 && metrics.horizon === 20);
  assert.ok(Math.abs(metrics.year20 - 13016.3166) < 0.05);

  // 6e. actual renderLoanPivot with stale loanSaved.metrics13356 must render 13016 and exclude 13356
  vm.runInContext(`
    loanSaved = [{
      id: '0',
      name: 'Legacy Stale Simulation',
      metrics: { initial: 0, horizon: 20, year20: 13356 },
      settings: legacyResult.settings,
      result: legacyResult,
      saved: true
    }];
  `, context);
  context.renderLoanPivot();
  const pivotHtml = elements.loanPivot.innerHTML || elements.loanPivot.textContent || '';
  assert.match(pivotHtml, /13,016/);
  assert.doesNotMatch(pivotHtml, /13,356/);

  // 6f. Zero-loan: raw unlevered arrays identity test & no correction applied
  const zeroLoanResult = {
    ...legacyResult,
    finance: { ...legacyResult.finance, loan_principal_eur: 0, loan_amortized_principal_eur: 0 }
  };
  const zeroFlows = context.getDiscountingCashflows(zeroLoanResult);
  assert.deepEqual(zeroFlows.package_equity, zeroLoanResult.finance.cashflows_by_weather.package_equity);

  // Raw arrays unchanged
  assert.equal(JSON.stringify(projectPath), projectPathBefore);
  assert.equal(JSON.stringify(nominalEquityPath), nominalEquityPathBefore);

  // renderCashChart completes without error
  assert.doesNotThrow(() => context.renderCashChart(legacyResult, 'loanCashChart', 'package_equity', 'discounted'));
}

// 7. Mixed solar scope with proportional allocation
{
  const { context } = createHarness();
  const solarCapex = 10000.0, solarRatio = 0.75, solarPrincipal = FIXTURE.principal * solarRatio;
  const solarYear0 = -solarCapex + solarPrincipal;
  const solarProject = [-solarCapex, ...Array(20).fill(annualOperatingCash * solarRatio)];
  const solarNominalEquity = [solarYear0, ...Array(10).fill(annualOperatingCash * solarRatio - oracle.annualNominal * solarRatio), ...Array(10).fill(annualOperatingCash * solarRatio)];
  const solarDiscEquity = [solarYear0, ...oracle.equivDebt.map((eq, y) => y < 10 ? (annualOperatingCash * solarRatio - eq * solarRatio) : annualOperatingCash * solarRatio)];

  const mockResultSolar = {
    ...mockResult,
    settings: { ...mockResult.settings, load_scenario: 'baseline', loan_include_electrification: false },
    finance: {
      ...mockResult.finance, capex_eur: solarCapex, conversion_capex_eur: 0, loan_scope: 'solar', solar_loan_principal_eur: solarPrincipal, solar_loan_ratio: solarRatio,
      cashflows_by_weather: { pv_incremental: [solarProject, solarProject], solar_equity: [solarNominalEquity, solarNominalEquity], equity: [solarNominalEquity, solarNominalEquity] },
      cashflows_for_discounting_by_weather: { pv_incremental: [solarProject, solarProject], solar_equity: [solarDiscEquity, solarDiscEquity], equity: [solarDiscEquity, solarDiscEquity] }
    }
  };
  context.mockResultSolar = mockResultSolar;
  vm.runInContext('summaryScope = "solar"; summaryCash = "loan"; current = { result: mockResultSolar }; settings = { ...mockResultSolar.settings };', context);
  const solarData = context.buildSummaryCashRows(mockResultSolar, cashRows);
  assert.ok(Math.abs(solarData.npvTotals.debtService - oracle.netFinancingCost * solarRatio) < 0.05);
}

console.log('ALL TESTS PASSED CLEANLY!');
