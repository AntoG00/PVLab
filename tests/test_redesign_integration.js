'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { createStableQuoteScenarioFixture } = require('./quote_scenario_fixture.js');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const cp = require('node:child_process');
const pythonBin = process.env.PV_PYTHON || path.join(__dirname, '..', '.venv', 'bin', 'python');

let supportedSettingsKeys = null;
function getSupportedSettingsKeys() {
  if (!supportedSettingsKeys) {
    const script = `
import sys, json
sys.path.insert(0, 'feasibility/vendor')
sys.path.insert(0, '.')
import feasibility.engine as e
print(json.dumps(list(e.SPEC.keys())))
`;
    const out = cp.execFileSync(pythonBin, ['-c', script], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      cwd: path.join(__dirname, '..')
    });
    supportedSettingsKeys = new Set(JSON.parse(out));
  }
  return supportedSettingsKeys;
}

function assertValidEngineSettings(settings) {
  assert.ok(settings && typeof settings === 'object', 'Settings must be an object');
  const allowed = getSupportedSettingsKeys();
  const unknown = Object.keys(settings).filter(k => !allowed.has(k));
  if (unknown.length > 0) {
    throw new Error(`Unknown settings: ${unknown.sort().join(', ')}`);
  }
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
    q_res = e.quote_evaluate(data.get('settings'), data.get('quote'))
    print(json.dumps(q_res))
`;
  const out = cp.execFileSync(pythonBin, ['-c', script, JSON.stringify(payload)], {
    encoding: 'utf8',
    cwd: path.join(__dirname, '..'),
    stdio: ['ignore', 'pipe', 'pipe'],
    maxBuffer: 16 * 1024 * 1024,
    timeout: 90000
  });
  return JSON.parse(out);
}

function createTestEnvironment() {
  const elements = {};
  function makeEl(id, tagName = 'div') {
    if (!elements[id]) {
      elements[id] = {
        id,
        tagName: tagName.toUpperCase(),
        classList: {
          classes: new Set(),
          add(c) { this.classes.add(c); },
          remove(c) { this.classes.delete(c); },
          toggle(c, force) {
            if (force === undefined) {
              if (this.classes.has(c)) this.classes.delete(c); else this.classes.add(c);
            } else if (force) {
              this.classes.add(c);
            } else {
              this.classes.delete(c);
            }
            return this.classes.has(c);
          },
          contains(c) { return this.classes.has(c); }
        },
        hidden: false,
        disabled: false,
        innerHTML: '',
        textContent: '',
        value: '',
        style: {},
        options: [],
        dataset: {},
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return this[k]; },
        removeAttribute(k) { delete this[k]; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        closest() { return null; },
        addEventListener() {},
        removeEventListener() {}
      };
    }
    return elements[id];
  }

  const knownIds = [
    'metrics', 'summaryScopeNote', 'unconfirmedAssumptionsBadge', 'moneyBasisSelect',
    'cashFlowBasis', 'cashFlowHeading', 'cashFlowDescription', 'cashFlowBasisNote',
    'cashChart', 'cashInsights', 'scopeSolarBtn', 'scopePackageBtn', 'cashBeforeBtn', 'cashAfterBtn',
    'runSummarySensitivityBtn', 'summarySensitivityResults', 'summarySensitivityStatus',
    'configVsRecContent', 'saved',
    'solarChart1', 'solarChart2Wrap', 'solarSizeLegend', 'toggleSolarCurveView',
    'solarChart3', 'solarTable3', 'solarRoofPills', 'solarRoofLimitsTable',
    'designUnifiedComparisonTable', 'solarComparisonsTable', 'solarAlternativesTable',
    'solarSizeCurveTitle', 'batMarginalTitle', 'solarResultsWrap', 'applySolarSystem',
    'loanPayment', 'loanAssumptions', 'loanCashChart', 'loanInsights', 'loanCashHeading',
    'loanPivot', 'loanMetricCards', 'saveLoanSimulation', 'solveLoanAmount',
    'annualEnergyFlowVisual', 'typicalDayChart', 'typicalDayTitle', 'monthHourHeatmap',
    'dayhourScenario', 'seasonSelectorPills',
    'adjacentPricesContent', 'lcoeContent', 'exportValuationCards', 'exportSensitivityComparison',
    'exportValuationNote', 'marketSeasonalChart', 'marketCurveTable', 'marketCurveProvenance',
    'marketCurveWindow', 'ledger', 'checklistProgress', 'checklistItems',
    'quoteComparisonSheetContent', 'printPitchBtn', 'scenarioMenu', 'toggleInputs',
    'controls', 'pinnedControlsList', 'modifiedCountBadge', 'activeTabAssumptionsPill',
    'status', 'error', 'latency', 'systemTitle', 'systemTags'
  ];
  knownIds.forEach(id => makeEl(id));

  const listeners = {};
  const bodyEl = makeEl('body', 'body');
  const doc = {
    body: bodyEl,
    getElementById: id => elements[id] || makeEl(id),
    querySelector: sel => {
      if (sel === 'body') return bodyEl;
      if (sel.startsWith('#')) return elements[sel.slice(1)] || null;
      return null;
    },
    querySelectorAll: () => [],
    addEventListener: (ev, fn) => {
      listeners[ev] = listeners[ev] || [];
      listeners[ev].push(fn);
    },
    removeEventListener: () => {}
  };

  const localStorageMock = {
    data: {},
    getItem(k) { return this.data[k] ?? null; },
    setItem(k, v) { this.data[k] = String(v); },
    removeItem(k) { delete this.data[k]; }
  };

  const win = {
    document: doc,
    localStorage: localStorageMock,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    print() { win.printed = true; },
    matchMedia: () => ({ matches: false }),
    addEventListener: () => {},
    removeEventListener: () => {}
  };

  const context = vm.createContext({
    window: win,
    document: doc,
    fetch: win.fetch,
    localStorage: localStorageMock,
    matchMedia: win.matchMedia,
    console,
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    structuredClone: obj => JSON.parse(JSON.stringify(obj)),
    Number,
    Math,
    String,
    Boolean,
    Array,
    Object,
    JSON
  });

  return { context, elements, doc, win, listeners };
}

function loadAppInContext(context) {
  vm.runInContext(appSource, context);
}

function setCurrent(context, resultObj) {
  context._testResult = resultObj;
  vm.runInContext('current = { result: _testResult };', context);
}

test('Bug A: getScopeCashMetrics handles all four scope/cash combinations canonically', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    settings: { cashflow_mode: 'real', inflation: 0.02, real_discount_rate: 0.04 },
    finance: {
      capex_eur: 9000,
      conversion_capex_eur: 4000,
      loan_principal_eur: 6000,
      solar_loan_principal_eur: 4500,
      real_discount_rate: 0.04,
      nominal_discount_rate: 0.0608,
      cashflows_by_weather: {
        pv_incremental: [[-9000, 1000, 1000]],
        solar_equity: [[-4500, 700, 700]],
        whole_package: [[-13000, 1600, 1600]],
        package_equity: [[-7000, 1100, 1100]]
      }
    },
    metrics: {
      npv_eur: { min: 6000, median: 7500, max: 9000 },
      package_npv_eur: { min: 7000, median: 8500, max: 10000 },
      discounted_payback_years: { min: 7, median: 8.5, max: 10 },
      autarky: { min: 0.6, median: 0.72, max: 0.8 },
      self_consumption: { min: 0.35, median: 0.42, max: 0.5 }
    },
    annual_cashflows: [
      {},
      {
        pv_incremental_range: { median: 1020 },
        solar_equity_range: { median: 714 },
        whole_package_range: { median: 1632 },
        package_equity_range: { median: 1122 }
      }
    ]
  };

  setCurrent(context, mockResult);

  // 1. Solar + Cash -> pv_incremental
  const m1 = vm.runInContext('getScopeCashMetrics(current.result, "solar", "cash")', context);
  assert.strictEqual(m1.flowKey, 'pv_incremental');
  assert.strictEqual(m1.capexVal, 9000);
  assert.strictEqual(m1.npvTitle, 'PV + STORAGE INCREMENTAL NPV');
  assert.strictEqual(m1.pbTitle, 'DISCOUNTED SOLAR PAYBACK');

  // 2. Solar + Loan -> solar_equity
  const m2 = vm.runInContext('getScopeCashMetrics(current.result, "solar", "loan")', context);
  assert.strictEqual(m2.flowKey, 'solar_equity');
  assert.strictEqual(m2.capexVal, 4500); // 9000 capex - 4500 loan
  assert.strictEqual(m2.capexExtra, 'Total cost: €9,000 · Loan: €4,500');
  assert.strictEqual(m2.npvTitle, 'PERSONAL EQUITY NPV (SOLAR)');
  assert.strictEqual(m2.pbTitle, 'DISCOUNTED CASH RECOVERY (SOLAR)');
  assert.strictEqual(m2.pbExtra, 'Payback of ensemble-mean cash flows · after allocated solar loan repayments');

  // 3. Package + Cash -> whole_package
  const m3 = vm.runInContext('getScopeCashMetrics(current.result, "package", "cash")', context);
  assert.strictEqual(m3.flowKey, 'whole_package');
  assert.strictEqual(m3.capexVal, 13000); // 9000 + 4000
  assert.strictEqual(m3.npvTitle, 'WHOLE-HOUSE PACKAGE NPV');
  assert.strictEqual(m3.pbTitle, 'DISCOUNTED PACKAGE PAYBACK');

  // 4. Package + Loan -> package_equity
  const m4 = vm.runInContext('getScopeCashMetrics(current.result, "package", "loan")', context);
  assert.strictEqual(m4.flowKey, 'package_equity');
  assert.strictEqual(m4.capexVal, 7000); // 13000 total - 6000 loan
  assert.strictEqual(m4.capexExtra, 'Total CAPEX: €13,000 (€9,000 solar+storage + €4,000 electrification) · Loan: €6,000');
  assert.strictEqual(m4.npvTitle, 'WHOLE-HOUSE EQUITY NPV');
  assert.strictEqual(m4.pbTitle, 'DISCOUNTED CASH RECOVERY (WHOLE PROJECT)');
  assert.strictEqual(m4.pbExtra, 'Payback of ensemble-mean cash flows · after whole-project loan repayments');

  // Verify setSummaryScope and setSummaryCash synchronize canonical key
  vm.runInContext('setSummaryScope("solar"); setSummaryCash("loan");', context);
  assert.strictEqual(vm.runInContext('summaryScope', context), 'solar');
  assert.strictEqual(vm.runInContext('summaryCash', context), 'loan');
  assert.strictEqual(elements.cashFlowBasis.value, 'solar_equity');

  // Verify renderVerdictStrip renders and includes anchor to #financing
  vm.runInContext('renderVerdictStrip(current.result);', context);
  const metricsHtml = elements.metrics.innerHTML;
  assert.match(metricsHtml, /href="#financing"/, 'Payback card links to #financing definition');
  assert.match(metricsHtml, /PERSONAL EQUITY NPV \(SOLAR\)/);
  assert.match(metricsHtml, /Total cost: €9,000 · Loan: €4,500/);
  assert.match(metricsHtml, /DISCOUNTED CASH RECOVERY \(SOLAR\)/);
  assert.match(metricsHtml, /After proportional solar loan debt service repayments/);
});

test('Bug B: Financing tab calculates signed NPV cost, undiscounted breakeven, and comparisons', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    settings: {
      installation_year: 2026,
      loan_apr: 0.05,
      loan_years: 10,
      loan_scope: 'solar_and_conversion',
      horizon_years: 20,
      cashflow_mode: 'real',
      inflation: 0.02
    },
    annual_cashflows: [
      {},
      ...Array.from({ length: 10 }, () => ({
        whole_package: 1500,
        debt_service: 1018.23
      }))
    ],
    finance: {
      loan_scope: 'solar_and_conversion',
      loan_principal_eur: 8000,
      requested_loan_amount_eur: 8000,
      equity: {
        simple_payback: { min: 7.2, median: 8.4, max: 9.8, simple_payback_of_mean_cash_flows: 8.4, recovered_weather_years: 19 },
        discounted_payback: { min: 9.0, median: 10.5, max: 12.0, discounted_payback_of_mean_cash_flows: 10.5, recovered_weather_years: 19 }
      },
      package_equity: {
        npv: { min: 6500, median: 7800, max: 8900, mean: 7800 },
        simple_payback: { min: 7.2, median: 8.4, max: 9.8, simple_payback_of_mean_cash_flows: 8.4, recovered_weather_years: 19 }
      },
      financed_project: {
        discounted_payback: { min: 9.0, median: 10.5, max: 12.0 },
        unpaid_discounted_weather_years: 0
      },
      whole_package: {
        npv: { min: 6800, median: 8200, max: 9400, mean: 8200 }
      },
      cashflows_by_weather: {
        package_equity: [[-5000, 800, 800, 800]],
        whole_package: [[-13000, 1500, 1500, 1500]]
      }
    },
    metrics: {
      package_npv_eur: { min: 6800, median: 8200, max: 9400, mean: 8200 } // envelope object!
    }
  };

  setCurrent(context, mockResult);

  vm.runInContext('renderLoanPanel();', context);
  const paymentHtml = elements.loanPayment.innerHTML;

  // Assert NO €NaN anywhere
  assert.doesNotMatch(paymentHtml, /NaN/, 'Financing panel does not display €NaN');

  // Assert financing NPV cost is 8200 - 7800 = 400 €
  assert.match(paymentHtml, /€400/, 'Calculates matched signed financing NPV cost');

  // Independent computation from mock cashflow rows over loan term (10 years):
  // annual net cash = 1500 - 1018.23 = 481.77 EUR/yr => monthly net cash = 481.77 / 12 = 40.1475 EUR/mo (~40.15)
  assert.match(paymentHtml, /\+€40\.15 <small>\/ month<\/small>/, 'Monthly cash-flow coverage matches independent computation from mock cashflow rows');

  // The personal recovery card uses discounted 10.5 y, not simple 8.4 y.
  assert.match(elements.loanMetricCards.innerHTML, /DISCOUNTED PERSONAL CASH RECOVERY[\s\S]*?10\.5/, 'Personal recovery retains its discounted definition');

  // Assert genuine comparison chart is rendered with dashed cash line
  const chartHtml = elements.loanCashChart.innerHTML;
  assert.match(chartHtml, /Taking loan \(ensemble mean NPV\)/, 'Legend includes taking loan');
  assert.match(chartHtml, /Paying cash \(ensemble mean NPV\)/, 'Legend includes paying cash');
  assert.match(chartHtml, /stroke-dasharray="6 4"/, 'Paying cash upfront rendered as dashed comparison line');
});

test('Bug C: Year-1 median cash is deflated once, overview renders remaining cards, and savings breakdown removed', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    settings: { cashflow_mode: 'real', inflation: 0.05 },
    finance: {
      capex_eur: 8000,
      conversion_capex_eur: 3000,
      loan_principal_eur: 5000,
      cashflows_by_weather: {
        pv_incremental: [[-8000, 1050, 1050]]
      }
    },
    metrics: {
      npv_eur: { median: 5000 },
      package_npv_eur: { median: 7000 },
      autarky: { median: 0.7 },
      self_consumption: { median: 0.45 }
    },
    annual_cashflows: [
      {},
      {
        pv_incremental_range: { median: 1050 }, // nominal 1050 / (1 + 0.05) = 1000 real
        avoided_import: 735,
        net_export: 210,
        tax_deduction: 420,
        maintenance: 105,
        replacement: 0,
        residual: 0
      }
    ]
  };

  setCurrent(context, mockResult);
  const metrics = vm.runInContext('getScopeCashMetrics(current.result, "solar", "cash")', context);

  // Retain meaningful existing real deflation assertion: 1050 / 1.05 = 1000.00
  assert.strictEqual(Math.round(metrics.y1Net), 1000, 'Deflates Year 1 nominal median to real purchasing power');

  // Verify renderSavingsBreakdown is removed from global scope
  assert.strictEqual(vm.runInContext('typeof renderSavingsBreakdown', context), 'undefined', 'renderSavingsBreakdown is undefined');

  // Verify overview renders remaining four cards and omits Year-1 net cash flow KPI
  vm.runInContext('renderVerdictStrip(current.result);', context);
  const visualHtml = elements.metrics.innerHTML;
  assert.match(visualHtml, /id="metricSummaryCapex"/, 'Renders capex card');
  assert.match(visualHtml, /id="metricSummaryNpv"/, 'Renders NPV card');
  assert.match(visualHtml, /id="metricSummaryPayback"/, 'Renders payback card');
  assert.match(visualHtml, /id="metricSummaryAutarky"/, 'Renders autarky card');
  assert.doesNotMatch(visualHtml, /metricSummaryYear1/, 'Overview omits Year-1 net cash flow KPI card');
  assert.doesNotMatch(visualHtml, /YEAR-1 NET CASH FLOW/, 'Overview omits Year-1 net cash flow label');

  // Verify scope and cash basis switches update verdict strip with remaining cards and without removed section
  vm.runInContext('setSummaryScope("package");', context);
  assert.match(elements.metrics.innerHTML, /id="metricSummaryCapex"/, 'Renders capex under package scope');
  assert.doesNotMatch(elements.metrics.innerHTML, /metricSummaryYear1/, 'Omits Year-1 under package scope');

  vm.runInContext('setSummaryCash("loan");', context);
  assert.match(elements.metrics.innerHTML, /id="metricSummaryCapex"/, 'Renders capex under loan basis');
  assert.doesNotMatch(elements.metrics.innerHTML, /metricSummaryYear1/, 'Omits Year-1 under loan basis');
});

test('Bug D: Sizing analysis completion populates 4-configuration benchmark table and cards', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    layout: { panel_count: 14, kwp: 6.5 },
    settings: { battery_kwh: 5.0, inverter_kw: 6.0 },
    finance: { capex_eur: 7800, pv_incremental: { npv_eur: { median: 6100 } } },
    metrics: {
      npv_eur: { median: 6100 },
      autarky: { median: 0.68 },
      self_consumption: { median: 0.45 },
      discounted_payback_years: { median: 8.2 }
    }
  };

  const mockSolarData = {
    recommended_candidate: { kwp: 6.5, panel_count: 14, battery_kwh: 5.0, npv_median: 6100 },
    recommendation_choices: {
      financial: { kwp: 8.0, panel_count: 17, battery_kwh: 5.0, npv_median: 6450, discounted_payback_years: 7.9, autarky_median: 0.74, capex_eur: 8800 },
      balanced: { kwp: 6.5, panel_count: 14, battery_kwh: 5.0, npv_median: 6100, discounted_payback_years: 8.2, autarky_median: 0.68, capex_eur: 7800 },
      budget: { kwp: 4.5, panel_count: 10, battery_kwh: 0.0, npv_median: 4800, discounted_payback_years: 6.5, autarky_median: 0.45, capex_eur: 4500 }
    }
  };

  setCurrent(context, mockResult);
  context._testSolarData = mockSolarData;
  vm.runInContext('solarData = _testSolarData;', context);

  vm.runInContext('renderDesignUnifiedComparisonTable(solarData);', context);
  const tableHtml = elements.designUnifiedComparisonTable.innerHTML;

  assert.match(tableHtml, /Current configuration/, 'Renders Current row');
  assert.match(tableHtml, /Best return \(financial\)/, 'Renders Best return row');
  assert.match(tableHtml, /Balanced choice/, 'Renders Balanced row');
  assert.match(tableHtml, /Budget coverage/, 'Renders Budget row');
  assert.match(tableHtml, /data-apply-config-idx/, 'Renders Apply buttons');

  // Verify renderConfigVsRecommended displays candidate values without dashes
  vm.runInContext('renderConfigVsRecommended();', context);
  const recHtml = elements.configVsRecContent.innerHTML;
  assert.match(recHtml, /CURRENT CONFIGURATION/, 'Current card rendered');
  assert.match(recHtml, /BEST RETURN \(FINANCIAL\)/, 'Best return card rendered');
  assert.match(recHtml, /BALANCED CHOICE \(RECOMMENDED\)/, 'Balanced card rendered');
  assert.doesNotMatch(recHtml, /BUDGET COVERAGE/, 'Budget card excluded from main Summary tile (budget belongs in Design)');
  assert.match(recHtml, /Explore full sizing in System design/, 'Summary tile links to System design');
});

test('Bug F: Sidebar filters tab-relevant groups and pinned key five controls', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockSchema = {
    records: [
      { name: 'retail_commodity', group: 'prices', label: 'Electricity rate', unit: 'EUR/kWh', status: 'verified', source: 'Bill' },
      { name: 'gas_commodity', group: 'gas', label: 'Gas rate', unit: 'EUR/Smc', status: 'verified', source: 'Bill' },
      { name: 'real_discount_rate', group: 'finance', label: 'Real discount', unit: 'fraction', status: 'proposed', source: 'Standard' },
      { name: 'deduction_rate', group: 'tax', label: 'Deduction', unit: 'fraction', status: 'proposed', source: 'Law' },
      { name: 'south_panels', group: 'layout', label: 'South panels', unit: 'panels', status: 'measured', source: 'Roof' },
      { name: 'battery_kwh', group: 'equipment', label: 'Battery capacity', unit: 'EUR/usable kWh', status: 'proposed', source: 'Model' },
      { name: 'random_advanced_param', group: 'aging', label: 'Aging param', unit: 'multiplier', status: 'proposed', source: 'Default' }
    ],
    defaults: {
      retail_commodity: 0.12,
      gas_commodity: 0.45,
      real_discount_rate: 0.04,
      deduction_rate: 0.5,
      south_panels: 12,
      battery_kwh: 5.0,
      random_advanced_param: 1.0
    }
  };

  context._mockSchema = mockSchema;
  vm.runInContext('schema = _mockSchema; settings = { ...schema.defaults }; renderControls();', context);

  // Switch to behavior view
  vm.runInContext('switchView("behavior");', context);

  // Check pinned conceptual controls
  const pinnedHtml = elements.pinnedControlsList.innerHTML;
  assert.match(pinnedHtml, /data-control="retail_commodity"/, 'Pinned list includes electricity rate');
  assert.match(pinnedHtml, /data-control="gas_commodity"/, 'Pinned list includes gas rate');
  assert.match(pinnedHtml, /data-control="real_discount_rate"/, 'Pinned list includes real discount');
  assert.match(pinnedHtml, /data-control="deduction_rate"/, 'Pinned list includes deduction rate');
  assert.match(pinnedHtml, /data-control="battery_kwh"/, 'Pinned list includes battery');
  assert.doesNotMatch(pinnedHtml, /data-control="south_panels"/, 'Pinned list does not include panels');

  // Check category controls
  assert.match(elements.controls.innerHTML, /data-category="System"[^>]*>[\s\S]*data-control="south_panels"/, 'System category includes panels');

  // Check active tab pill
  assert.strictEqual(elements.activeTabAssumptionsPill.textContent, 'How It Behaves');
});

test('Bug H: Pricing calculations and signed LCOE advantage', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    settings: {
      retail_commodity: 0.12,
      retail_regulated: 0.05,
      electric_excise: 0.02,
      electric_vat: 0.10,
      gas_commodity: 0.48,
      export_market_price: 0.13,
      export_capture_factor: 0.75,
      deduction_rate: 0.50
    },
    export_price_analysis: {
      baseload_price_eur_kwh: 0.13,
      export_weighted_captured_price_eur_kwh: 0.098,
      export_weighted_capture_factor: 0.7538,
      legacy_capture_factor_active: false
    },
    finance: {
      lcoe: { median: 0.11 },
      lcoe_net_incentives: { median: 0.065 }
    },
    metrics: {
      lcoe_eur_kwh: { median: 0.11 }
    }
  };

  setCurrent(context, mockResult);

  vm.runInContext('renderAdjacentPrices();', context);
  const pricesHtml = elements.adjacentPricesContent.innerHTML;

  // Marginal unit rate = (0.12 + 0.05 + 0.02) * (1 + 0.10) = 0.19 * 1.10 = 0.209 €/kWh
  assert.match(pricesHtml, /Marginal Variable Electricity Rate:/, 'Labels marginal variable electricity rate');
  assert.match(pricesHtml, /€0\.209/, 'Calculates exact marginal electricity variable rate');
  assert.match(pricesHtml, /€0\.480\/Smc/, 'Displays gas_commodity rate');

  vm.runInContext('renderLcoeCards();', context);
  const lcoeHtml = elements.lcoeContent.innerHTML;

  // LCOE advantage = 0.209 - 0.065 = +0.144 €/kWh
  assert.match(lcoeHtml, /NET LCOE ADVANTAGE VS GRID/, 'Displays LCOE advantage header');
  assert.match(lcoeHtml, /\+€0\.144/, 'Signed positive advantage vs grid rate');
  assert.match(lcoeHtml, /Exported energy earns wholesale market value/, 'Includes honest explanation of wholesale worth');
});

test('Bug I: metricCard headlines median value and renders weather range below', () => {
  const { context } = createTestEnvironment();
  loadAppInContext(context);

  const cardHtml = vm.runInContext('metricCard("TOTAL NPV", {min: 6200, median: 7400, max: 8800}, "eur", "Detail text", true, "customNpvAnchor")', context);

  // Value must be the median: €7,400
  assert.match(cardHtml, /<div class="value">€7,400<\/div>/, 'Headline value is strictly the median');
  assert.match(cardHtml, /Historical min–max €6,200 – €8,800/, 'Sub-band displays historical weather range');
  assert.match(cardHtml, /id="customNpvAnchor"/, 'Includes anchor ID for cross-tab linking');

  // Censored / unrecovered payback
  const pbHtml = vm.runInContext('metricCard("PAYBACK", {min: null, median: null, max: null, recovered_weather_years: 0, weather_count: 19}, "years")', context);
  assert.match(pbHtml, /<div class="value">Not reached<\/div>/, 'Unrecovered payback cleanly displays Not reached without NaN');
});

test('Bug J: Contractor pitch sheet comparison and printing', () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  const mockQuote = {
    installer: 'SolarTech Srl',
    price_basis: 'itemized',
    line_items: [
      { description: 'Modules 6.5 kWp', net_eur: 4500, vat: 0.1, quantity: 1 },
      { description: 'Inverter 6 kW', net_eur: 1500, vat: 0.1, quantity: 1 }
    ]
  };
  const mockCurrent = {
    result: {
      layout: { kwp: 6.5, panel_count: 14 },
      settings: { battery_kwh: 5.0 },
      finance: { capex_eur: 7800 },
      metrics: { npv_eur: { median: 6100 }, payback_years: { median: 8.2 } },
      weather_count: 19
    },
    quote: mockQuote,
    itemized_total_eur: 6600,
    claims: [
      { field: 'claimed_yield_kwh', claim: 12000, simulated: { min: 8500, median: 9200, max: 9800 }, outside_weather_range: true, interpretation: 'Exceeds maximum simulated weather envelope' },
      { field: 'claimed_self_consumption_pct', claim: 0.95, simulated: { min: 0.45, median: 0.55, max: 0.65 }, outside_weather_range: true, interpretation: 'Exceeds modeled autarky envelope' },
      { field: 'claimed_payback_years', claim: 6.0, simulated: { min: 7.2, median: 8.2, max: 9.5 }, outside_weather_range: true, interpretation: 'Promised payback shorter than modeled range' }
    ],
    missing_fields: ['battery_model', 'warranties']
  };

  context._testCurrent = mockCurrent;
  context._testQuote = mockQuote;
  vm.runInContext('current = _testCurrent; quote = _testQuote;', context);

  vm.runInContext('renderQuoteComparisonSheet();', context);
  const sheetHtml = elements.quoteComparisonSheetContent.innerHTML;

  assert.match(sheetHtml, /Modeled physical benchmark/, 'Sheet compares modeled specs');
  assert.match(sheetHtml, /Contractor offer/, 'Sheet compares contractor offer');
  assert.match(sheetHtml, /Claimed yields &amp; savings vs 19-year physical simulation/, 'Audits contractor claims');
  assert.match(sheetHtml, /Contractor disclosure gaps/, 'Lists missing fields checklist');

  // Verify printing action
  vm.runInContext('printPitchComparisonSheet();', context);
  assert.strictEqual(win.printed, true, 'Invokes window.print()');
});

test('Bug K: Input ledger status badges preserve raw provenance', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    assumptions: [
      { name: 'retail_commodity', value: 0.1169, unit: '€/kWh', status: 'measured', source: 'Enel invoice', date_accessed: '2026-10-01', owner: 'User' },
      { name: 'inverter_kw', value: 6.0, unit: 'kW', status: 'proposed', source: 'Contractor pitch', date_accessed: '2026-10-02', owner: 'Installer' }
    ]
  };

  setCurrent(context, mockResult);

  vm.runInContext('renderLedger(current.result);', context);
  const ledgerHtml = elements.ledger.innerHTML;

  assert.match(ledgerHtml, /<span class="tag measured">measured<\/span>/, 'Preserves measured status chip');
  assert.match(ledgerHtml, /<span class="tag proposed">proposed<\/span>/, 'Preserves proposed status chip');

  // Status badge count
  vm.runInContext('updateAssumptionsBadge(current.result);', context);
  const badgeText = elements.unconfirmedAssumptionsBadge.textContent;
  assert.match(badgeText, /1 unconfirmed assumptions/, 'Correctly counts 1 proposed input as unconfirmed while 1 measured is verified');
});

test('Bug L: Menu keyboard Escape closes scenario menu', () => {
  const { context, elements, listeners } = createTestEnvironment();
  loadAppInContext(context);

  const menu = elements.scenarioMenu;
  menu.open = true;

  // Trigger escape key on document listeners
  const keyListeners = listeners?.keydown || [];
  keyListeners.forEach(fn => fn({ key: 'Escape' }));

  assert.strictEqual(menu.open, false, 'Pressing Escape closes the scenario menu');
});

test('Asynchronous race condition: delayed summary sensitivity response is rejected on scope or input change', async () => {
  const { context, win } = createTestEnvironment();
  loadAppInContext(context);

  let resolveApi;
  const pendingPromise = new Promise(resolve => { resolveApi = resolve; });
  win.fetch = async (url) => {
    if (url === '/api/summary-sensitivity') {
      return { ok: true, json: async () => pendingPromise };
    }
    return { ok: true, json: async () => ({}) };
  };

  vm.runInContext(`
    settings = { retail_commodity: 0.12 };
    quote = { line_items: [] };
    current = { result: { settings: { retail_commodity: 0.12 }, finance: {}, metrics: {} } };
    summaryScope = 'solar';
    summaryCash = 'cash';
  `, context);

  const fetchPromise = vm.runInContext('fetchSummarySensitivity()', context);

  // In-flight user interaction: user toggles scope to 'package'
  vm.runInContext("summaryScope = 'package';", context);

  // Delayed API response for 'solar' resolves
  resolveApi({
    base_npv_eur: 5000,
    electricity_price: { low_npv: 4000, high_npv: 6000 },
    discount_rate: {},
    weather: {}
  });
  await fetchPromise;

  const cacheKey = vm.runInContext('summarySensitivityKey', context);
  assert.strictEqual(cacheKey, '', 'Cache key must remain empty because request key mismatched currentKey');
  const cache = vm.runInContext('summarySensitivityCache', context);
  assert.strictEqual(cache, null, 'Cache must remain null on mismatched in-flight response');
});

test('Asynchronous race condition: delayed behavior matrix response is rejected on scenario change', async () => {
  const { context, win } = createTestEnvironment();
  loadAppInContext(context);

  let resolveApi;
  const pendingPromise = new Promise(resolve => { resolveApi = resolve; });
  win.fetch = async (url) => {
    if (url === '/api/behavior-matrix') {
      return { ok: true, json: async () => pendingPromise };
    }
    return { ok: true, json: async () => ({}) };
  };

  vm.runInContext(`
    settings = { load_scenario: 'electrified' };
    quote = { line_items: [] };
    current = { result: { settings: { load_scenario: 'electrified' } } };
  `, context);

  const refreshPromise = vm.runInContext('refreshBehaviorViews()', context);

  // In-flight change: scenario changes to 'ev'
  vm.runInContext("settings.load_scenario = 'ev';", context);

  resolveApi({
    annual_flows: { pv_generation_kwh: 4000, load_demand_kwh: 3500 },
    matrix: { seasons: { annual: [] } }
  });
  await refreshPromise;

  const cache = vm.runInContext('behaviorMatrixCache', context);
  assert.strictEqual(cache, null, 'Cache must not accept delayed response for different scenario');
});

test('Stale Solar Sizing Apply controls disabled consistently across cards and unified table', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockSolarData = {
    recommended_candidate: { kwp: 6.5, panel_count: 14, battery_kwh: 5.0, npv_median: 6100 },
    recommendation_choices: {
      financial: { kwp: 8.0, panel_count: 17, battery_kwh: 5.0, npv_median: 6450, capex_eur: 8800 },
      balanced: { kwp: 6.5, panel_count: 14, battery_kwh: 5.0, npv_median: 6100, capex_eur: 7800 },
      budget: { kwp: 4.5, panel_count: 10, battery_kwh: 0.0, npv_median: 4800, capex_eur: 4500 }
    }
  };

  vm.runInContext(`
    solarData = ${JSON.stringify(mockSolarData)};
    current = { result: { layout: { kwp: 6.5, panel_count: 14 }, settings: { battery_kwh: 5.0 }, metrics: { npv_eur: { median: 6100 }, autarky: { median: 0.68 }, self_consumption: { median: 0.45 } }, finance: { capex_eur: 7800 } } };
  `, context);

  vm.runInContext('renderDesignUnifiedComparisonTable(solarData);', context);
  let tableHtml = elements.designUnifiedComparisonTable.innerHTML;
  assert.match(tableHtml, /data-apply-config-idx/, 'Rendered apply buttons');

  // Stale invalidation
  vm.runInContext('markSolarSizingStale();', context);

  vm.runInContext('renderDesignUnifiedComparisonTable(solarData);', context);
  tableHtml = elements.designUnifiedComparisonTable.innerHTML;

  assert.match(tableHtml, /stale-table/, 'Table marked with stale-table class');
  assert.match(tableHtml, /stale-comparison-notice/, 'Stale comparison notice displayed');
  const disabledBtnCount = (tableHtml.match(/disabled/g) || []).length;
  assert(disabledBtnCount >= 3, 'All candidate Apply buttons disabled in stale comparison table');
});

test('Contractor comparison sheet displays genuine benchmark vs offered specs, c.claim, and missing evidence fields', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    layout: { kwp: 6.5, panel_count: 14 },
    metrics: { npv_eur: { median: 5500 }, discounted_payback_years: { median: 8.5 } },
    finance: { capex_eur: 7500 },
    settings: { panel_watts: 400, battery_kwh: 5.0 }
  };
  const mockQuote = {
    installer_name: 'Test Solar Ltd',
    line_items: [{ description: 'Turnkey PV installation', net_eur: 8000, vat: 0.1, quantity: 1 }]
  };
  const mockCurrent = {
    result: mockResult,
    quote: mockQuote,
    itemized_total_eur: 8000,
    claims: [
      { field: 'claimed_self_consumption_pct', claim: 0.95, outside_weather_range: true, simulated: { min: 0.4, median: 0.55, max: 0.65 }, interpretation: 'Exceeds modeled autarky envelope' }
    ],
    missing_fields: ['inverter_model']
  };

  context._testCurrent = mockCurrent;
  context._testQuote = mockQuote;
  vm.runInContext('current = _testCurrent; quote = _testQuote;', context);

  vm.runInContext('renderQuoteComparisonSheet();', context);
  const sheetHtml = elements.quoteComparisonSheetContent.innerHTML;

  assert.match(sheetHtml, /Awaiting installer specs/, 'Displays awaiting installer specs when installer gave no panel count/kWp');
  assert.doesNotMatch(sheetHtml, /Matches modeled layout/, 'Does not fabricate matches modeled layout when contractor layout is absent');
  assert.match(sheetHtml, /Module model &amp; datasheet guarantee missing/, 'Lists missing module model in disclosure gaps');
  assert.match(sheetHtml, /Outside modeled range; check definitions/, 'Flags exaggerated claim with honest unconfirmed tag');
});

test('Annual season option in typical day chart and weather envelope scaling', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockData = {
    matrix: {
      seasons: {
        annual: Array.from({ length: 24 }, (_, i) => ({
          pv: i >= 6 && i <= 18 ? 3.5 : 0,
          pv_envelope: [0, i >= 6 && i <= 18 ? 5.2 : 0],
          load: 1.2,
          load_envelope: [0.8, 2.8],
          direct: 1.0,
          import: 0.2,
          export: 2.0,
          soc: 3.5
        }))
      }
    }
  };

  context._testData = mockData;
  vm.runInContext(`
    activeSeason = 'annual';
    drawTypicalDayChart(_testData, 'annual');
  `, context);

  const chartHtml = elements.typicalDayChart.innerHTML;
  assert.match(chartHtml, /<svg/, 'Renders typical day SVG chart for annual season');
  assert.match(chartHtml, /aria-label="Average day hourly dispatch for annual; shaded bands show P10–P90 weather variation"/, 'SVG aria-label includes annual season and complete P10–P90 weather variation meaning');
  assert.match(chartHtml, /<polygon points=/, 'Renders weather envelope polygon');
});

test('All three numeric claims (including 0 and absent envelopes) render truthfully without crash', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    layout: { kwp: 8.28, panel_count: 19 },
    metrics: { npv_eur: { median: 8500 }, discounted_payback_years: { median: 8.0 } },
    finance: { capex_eur: 15000 },
    settings: { panel_watts: 435, battery_kwh: 11.04 }
  };
  const mockQuote = {
    installer_name: 'Canonical Test Installer',
    module_model: 'Trina Solar 435W Vertex S+',
    line_items: [{ description: 'Turnkey system', net_eur: 16000, vat: 0.1, quantity: 1 }]
  };

  // 1. All three numeric claims with realistic positive values
  const mockCurrent1 = {
    result: mockResult,
    quote: mockQuote,
    itemized_total_eur: 17600,
    claims: [
      { field: 'claimed_yield_kwh', claim: 12000, simulated: { min: 9500, median: 10400, max: 11200 }, outside_weather_range: true, interpretation: 'Weather range is not a warranty.' },
      { field: 'claimed_self_consumption_pct', claim: 0.95, simulated: { min: 0.55, median: 0.65, max: 0.75 }, outside_weather_range: true, interpretation: 'High battery usage assumed.' },
      { field: 'claimed_payback_years', claim: 6.5, simulated: { min: 7.0, median: 8.0, max: 9.2 }, outside_weather_range: true, interpretation: 'Compare retail tariff inflation assumptions.' }
    ],
    missing_fields: []
  };

  context._testCurrent = mockCurrent1;
  context._testQuote = mockQuote;
  vm.runInContext('current = _testCurrent; quote = _testQuote; renderQuoteComparisonSheet();', context);
  let sheetHtml = elements.quoteComparisonSheetContent.innerHTML;

  assert.match(sheetHtml, /Promised annual PV yield/, 'Friendly label for claimed_yield_kwh');
  assert.match(sheetHtml, /12,000 kWh/, 'Formatted numeric kWh yield');
  assert.match(sheetHtml, /Promised self-consumption/, 'Friendly label for claimed_self_consumption_pct');
  assert.match(sheetHtml, /95\.0%/, 'Formatted fraction as percentage without double conversion');
  assert.match(sheetHtml, /Promised simple payback/, 'Friendly label for claimed_payback_years');
  assert.match(sheetHtml, /6\.5 y/, 'Formatted years claim');

  // 2. Numeric claims with 0 and absent/null envelopes
  const mockCurrent2 = {
    result: mockResult,
    quote: mockQuote,
    itemized_total_eur: 17600,
    claims: [
      { field: 'claimed_yield_kwh', claim: 0, simulated: null, outside_weather_range: false, interpretation: 'Zero claim test' },
      { field: 'claimed_self_consumption_pct', claim: 0, simulated: { min: 0, max: 1 }, outside_weather_range: false },
      { field: 'claimed_payback_years', claim: 0, simulated: { min: null, max: null }, outside_weather_range: false, interpretation: 'Immediate payback test' }
    ],
    missing_fields: []
  };

  context._testCurrent = mockCurrent2;
  vm.runInContext('current = _testCurrent; renderQuoteComparisonSheet();', context);
  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;

  assert.match(sheetHtml, /0 kWh/, 'Correctly formats 0 kWh without falling back to dash');
  assert.match(sheetHtml, /0\.0%/, 'Correctly formats 0.0% without falling back to dash');
  assert.match(sheetHtml, /0\.0 y/, 'Correctly formats 0.0 y without falling back to dash');
  assert.match(sheetHtml, /Within simulated range/, 'Honest range status for in-range zero claims');
});

test('Quote normalization contract: raw 11.04 plus quote 6/.8 sends 4.8 kWh usable to behavior matrix', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  let capturedPayload = null;
  context.fetch = win.fetch = async (url, options) => {
    if (url === '/api/behavior-matrix') {
      capturedPayload = JSON.parse(options.body);
      return {
        ok: true,
        json: async () => ({
          matrix: {
            seasons: {
              summer: Array.from({ length: 24 }, () => ({ pv: 2, load: 1, direct: 1, import: 0, export: 1, soc: 4.8 })),
              annual: Array.from({ length: 24 }, () => ({ pv: 2, load: 1, direct: 1, import: 0, export: 1, soc: 4.8 }))
            },
            months: Array.from({ length: 12 }, () => Array.from({ length: 24 }, () => ({ import: 0, export: 1 })))
          }
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  // Set up raw settings with 11.04 kWh battery, quote with 6 kW nameplate and 0.8 DoD
  vm.runInContext(`
    settings = {
      south_panels: 19,
      panel_watts: 435,
      battery_kwh: 11.04,
      battery_modules: 4,
      battery_module_kwh: 2.76,
      inverter_kw: 6.0,
      load_scenario: 'electrified'
    };
    quote = {
      price_basis: 'modeled',
      battery_nameplate_kwh: 6.0,
      battery_dod: 0.8,
      line_items: []
    };
    // Result settings has normalized 4.8 kWh usable
    current = {
      result: {
        settings: {
          south_panels: 19,
          panel_watts: 435,
          battery_kwh: 4.8,
          battery_modules: 4,
          battery_module_kwh: 2.76,
          inverter_kw: 6.0,
          load_scenario: 'electrified'
        },
        layout: { kwp: 8.265, panel_count: 19 }
      }
    };
    acceptedInputFingerprint = inputFingerprint(settings, quote);
  `, context);

  await vm.runInContext('refreshBehaviorViews()', context);

  assert.ok(capturedPayload, 'behavior-matrix API was invoked');
  assert.strictEqual(capturedPayload.settings.battery_kwh, 4.8, 'Behavior request sent using normalized 4.8 kWh usable rather than raw 11.04 kWh');
  assert.strictEqual(capturedPayload.scenario, 'electrified', 'Behavior request sent with active load scenario');
});

test('Lifecycle invalidation: settings and quote changes invalidate already rendered behavior and sensitivity views', () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  vm.runInContext(`
    behaviorMatrixCache = { some: 'cached_data' };
    behaviorMatrixKey = 'electrified_dummy_key';
    summarySensitivityReqId = 5;
    const btn = $('runSummarySensitivityBtn');
    if (btn) btn.textContent = 'Calculating…';
    invalidateBehaviorAndSensitivity();
  `, context);

  assert.strictEqual(vm.runInContext('behaviorMatrixCache', context), null, 'Cache is cleared');
  assert.strictEqual(vm.runInContext('behaviorMatrixKey', context), '', 'Cache key is reset');
  assert.strictEqual(vm.runInContext('summarySensitivityReqId', context), 6, 'Token is incremented');
  assert.strictEqual(elements.runSummarySensitivityBtn.textContent, 'Update sensitivity', 'Button reset from Calculating');
  assert.match(elements.typicalDayChart.innerHTML, /Simulation pending/, 'Typical day chart set to pending state');
  assert.match(elements.monthHourHeatmap.innerHTML, /Simulation pending/, 'Heatmap chart set to pending state');
});

test('Quote comparison benchmark races: queueing handles rapid A->B and blank lines without false zero variance', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  let resolveA, resolveB;
  const apiCalls = [];

  context.fetch = win.fetch = (url, options) => {
    if (!url.includes('/api/evaluate')) {
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }
    const payload = options?.body ? JSON.parse(options.body) : {};
    assertValidEngineSettings(payload.settings);
    apiCalls.push(payload);
    return new Promise((resolve) => {
      if (apiCalls.length === 1) {
        resolveA = resolve;
      } else if (apiCalls.length === 2) {
        resolveB = resolve;
      } else {
        const isBench = payload.quote.price_basis === 'modeled' && payload.quote.line_items.length === 0;
        resolve({
          ok: true,
          json: async () => ({
            result: {
              layout: { kwp: 8.835 },
              finance: { capex_eur: isBench ? 13321.515 : 22000 },
              metrics: {
                npv_eur: { median: isBench ? 9485.1171 : 4147.5386 },
                discounted_payback_years: { median: isBench ? 8.0 : 12.0 }
              }
            }
          })
        });
      }
    });
  };

  // Start active itemized A settings with battery 4.8 kWh (normalized from 6/.8)
  vm.runInContext(`
    settings = {
      panel_watts: 465,
      battery_kwh: 11.04,
      quote_vat_included: true,
      roundtrip_efficiency: 0.9,
      charge_kw: 3.3,
      discharge_kw: 3.3
    };
    current = {
      result: {
        layout: { kwp: 8.835, panel_count: 19 },
        finance: { capex_eur: 22000 },
        metrics: {
          npv_eur: { median: 4147.5386 },
          discounted_payback_years: { median: 12.0 }
        },
        settings: {
          panel_watts: 465,
          battery_kwh: 4.8,
          roundtrip_efficiency: 0.9,
          charge_kw: 3.3,
          discharge_kw: 3.3,
          quote_total: 22000,
          quote_vat_included: true
        }
      },
      itemized_total_eur: 22000,
      missing_fields: []
    };
    quote = {
      price_basis: 'itemized',
      line_items: [{ description: 'PV', net_eur: 20000, vat: 0.1, quantity: 1 }],
      battery_nameplate_kwh: 6,
      battery_dod: 0.8,
      roundtrip_efficiency: 0.9,
      continuous_charge_kw: 3.3,
      continuous_discharge_kw: 3.3
    };
    loanPending = false;
    renderQuoteComparisonSheet();
  `, context);

  // Request A is triggered in-flight:
  assert.strictEqual(apiCalls.length, 1, 'Initial request A dispatched');
  assert.strictEqual(apiCalls[0].settings.battery_kwh, 4.8, 'Request payload retains physical battery 4.8 kWh');
  assert.strictEqual(apiCalls[0].settings.roundtrip_efficiency, 0.9, 'Request payload retains roundtrip efficiency');
  assert.strictEqual(apiCalls[0].settings.charge_kw, 3.3, 'Request payload retains charge power limit');
  assert.strictEqual(apiCalls[0].settings.quote_total, null, 'Benchmark eval clears quote_total cost override');
  assert.strictEqual(apiCalls[0].settings.quote_vat_included, true, 'Benchmark eval retains raw accepted quote_vat_included');
  assert.strictEqual(apiCalls[0].quote.price_basis, 'modeled', 'Benchmark eval sets modeled price basis');

  let sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /Calculating benchmark/, 'DOM shows Calculating benchmark while A in-flight');

  // While A is pending, set accepted B result settings with battery 8.28 kWh and render
  vm.runInContext(`
    current.result.settings.battery_kwh = 8.28;
    current.result.settings.charge_kw = 4.0;
    renderQuoteComparisonSheet();
  `, context);

  assert.strictEqual(apiCalls.length, 1, 'No second request dispatched while A still in-flight');

  // Now resolve A with A's payload
  resolveA({
    ok: true,
    json: async () => ({
      result: {
        layout: { kwp: 8.835 },
        finance: { capex_eur: 13321.515 },
        metrics: {
          npv_eur: { median: 9485.1171 },
          discounted_payback_years: { median: 8.0 }
        }
      }
    })
  });

  // Allow Promise resolution and queue pump
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  // Request B should now be dispatched!
  assert.strictEqual(apiCalls.length, 2, 'Request B automatically scheduled upon resolving stale A');
  assert.strictEqual(apiCalls[1].settings.battery_kwh, 8.28, 'Request B contains accepted B battery capacity');
  assert.strictEqual(apiCalls[1].settings.quote_total, null, 'Request B clears cost override');

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.doesNotMatch(sheetHtml, /€13,322/, 'A payload capex never shown in DOM');

  // Resolve B
  resolveB({
    ok: true,
    json: async () => ({
      result: {
        layout: { kwp: 8.835 },
        finance: { capex_eur: 15236 },
        metrics: {
          npv_eur: { median: 8200 },
          discounted_payback_years: { median: 9.5 }
        }
      }
    })
  });

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /€15,236/, 'DOM renders B benchmark capex');
  assert.match(sheetHtml, /€8,200/, 'DOM renders B benchmark NPV');
  assert.doesNotMatch(sheetHtml, /€13,322/, 'A payload never admitted into cache or DOM');

  // Test benchmark failure and retry
  let retryResolver;
  context.fetch = win.fetch = (url, options) => {
    return new Promise((resolve, reject) => {
      if (retryResolver) {
        retryResolver(resolve);
      } else {
        reject(new Error('Simulated network timeout'));
      }
    });
  };

  vm.runInContext(`
    current.result.settings.battery_kwh = 10.0;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /Benchmark failed/, 'Displays failure indicator upon rejection');
  assert.match(sheetHtml, /Retry/, 'Displays matching retry button');

  // Test retry succeeds
  const retryPromise = new Promise((resolve) => {
    retryResolver = (fetchResolve) => {
      fetchResolve({
        ok: true,
        json: async () => ({
          result: {
            layout: { kwp: 8.835 },
            finance: { capex_eur: 16100 },
            metrics: {
              npv_eur: { median: 7800 },
              discounted_payback_years: { median: 10.0 }
            }
          }
        })
      });
      resolve();
    };
  });

  await vm.runInContext('fetchQuoteComparison(null, null, null, null, true)', context);
  await retryPromise;
  await new Promise((resolve) => setImmediate(resolve));

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /€16,100/, 'Retry updates benchmark capex');

  // Test switch to stale no-offer immediately invalidates comparison
  vm.runInContext(`
    current.itemized_total_eur = null;
    current.result.settings.quote_total = null;
    settings.quote_total = null;
    quote.line_items = [];
    renderQuoteComparisonSheet();
  `, context);

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /Awaiting quote/, 'Switch to no-offer displays awaiting quote');
  assert.doesNotMatch(sheetHtml, /€16,100/, 'Stale benchmark finance removed');

  // Blank quote line handling: blank line item (null/empty net_eur) does not count as populated offer
  const callsBeforeBlank = apiCalls.length;
  vm.runInContext(`
    current.itemized_total_eur = null;
    current.result.settings.quote_total = null;
    settings.quote_total = null;
    quote.line_items = [{ description: '', net_eur: null, vat: 0.1, quantity: 1 }];
    renderQuoteComparisonSheet();
  `, context);

  assert.strictEqual(apiCalls.length, callsBeforeBlank, 'No comparison request dispatched for blank line items');
  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /Awaiting quote/, 'Blank line items do not register as a valid offer');
  assert.match(sheetHtml, /No economic offer comparison/, 'Blank line items show No economic offer comparison');
  assert.doesNotMatch(sheetHtml, /Pending comparison/, 'Blank line items never cause pending comparison');
  assert.doesNotMatch(sheetHtml, /Offer eval failed/, 'Blank line items never show offer eval failed');
  assert.doesNotMatch(sheetHtml, /Scaffolding \/ roof safety/, 'No scaffolding disclosure gap for blank lines');
  assert.doesNotMatch(sheetHtml, /GSE grid connection/, 'No GSE disclosure gap for blank lines');
  assert.match(sheetHtml, /Itemized turnkey cost breakdown missing \(no lines entered\)/, 'Shows no lines entered disclosure');

  // After removing the blank row, comparison remains normal
  vm.runInContext(`
    quote.line_items = [];
    renderQuoteComparisonSheet();
  `, context);
  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /Awaiting quote/, 'Empty lines list remains awaiting quote');
  assert.match(sheetHtml, /No economic offer comparison/, 'Empty lines list remains no economic offer comparison');
});

test('Golden contract: identical benchmark capex and NPV before and after quote mode switch', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  context.fetch = win.fetch = async (url, options) => {
    const payload = options?.body ? JSON.parse(options.body) : {};
    const s = payload.settings;
    const q = payload.quote;
    const isBench = q.price_basis === 'modeled' && (!q.line_items || q.line_items.length === 0);
    const vatInc = s.quote_vat_included !== false;
    const capex = isBench ? (vatInc ? 13321.515 : 14653.6665) : 22000;
    const npv = isBench ? (vatInc ? 9485.1171 : 8665.7963) : 4147.5386;
    return {
      ok: true,
      json: async () => ({
        result: {
          layout: { kwp: 8.835, panel_count: 19 },
          finance: { capex_eur: capex },
          metrics: {
            npv_eur: { min: npv - 500, median: npv, max: npv + 500 },
            discounted_payback_years: { min: 7.0, median: 8.0, max: 9.0 }
          },
          settings: { ...s, battery_kwh: 4.8 }
        }
      })
    };
  };

  // Case 1: Raw quote_vat_included = true
  vm.runInContext(`
    settings = {
      panel_watts: 465,
      battery_kwh: 11.04,
      quote_vat_included: true
    };
    quote = {
      price_basis: 'modeled',
      line_items: [{ description: 'PV', net_eur: 20000, vat: 0.1, quantity: 1 }],
      battery_nameplate_kwh: 6,
      battery_dod: 0.8
    };
    current = {
      result: {
        layout: { kwp: 8.835, panel_count: 19 },
        finance: { capex_eur: 13321.515 },
        metrics: {
          npv_eur: { min: 8985.1171, median: 9485.1171, max: 9985.1171 },
          discounted_payback_years: { median: 8.0 }
        },
        settings: {
          panel_watts: 465,
          battery_kwh: 4.8,
          quote_total: null,
          quote_vat_included: true
        }
      },
      itemized_total_eur: 22000,
      missing_fields: []
    };
    loanPending = false;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  let sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /€13,322/, 'Modeled mode displays €13,322 benchmark capex');
  assert.match(sheetHtml, /€9,485/, 'Modeled mode displays €9,485 benchmark NPV');
  assert.match(sheetHtml, /€22,000/, 'Modeled mode displays €22,000 offer capex');

  // Switch SAME quote to active itemized mode
  vm.runInContext(`
    quote.price_basis = 'itemized';
    current.result.finance.capex_eur = 22000;
    current.result.metrics.npv_eur.median = 4147.5386;
    current.result.settings.quote_total = 22000;
    current.result.settings.quote_vat_included = true;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /€13,322/, 'Active itemized mode retains identical €13,322 benchmark capex without extra 10% VAT');
  assert.match(sheetHtml, /€9,485/, 'Active itemized mode retains identical €9,485 benchmark NPV');
  assert.match(sheetHtml, /€22,000/, 'Active itemized mode displays €22,000 offer capex');

  // Case 2: Raw quote_vat_included = false
  vm.runInContext(`
    comparisonCache = null;
    comparisonKey = '';
    settings.quote_vat_included = false;
    quote.price_basis = 'modeled';
    current.result.finance.capex_eur = 14653.6665;
    current.result.metrics.npv_eur.median = 8665.7963;
    current.result.settings.quote_vat_included = false;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /€14,654/, 'Raw vat=false modeled mode displays €14,654 benchmark capex');
  assert.match(sheetHtml, /€8,666/, 'Raw vat=false modeled mode displays €8,666 benchmark NPV');

  // Switch to itemized with raw vat=false
  vm.runInContext(`
    quote.price_basis = 'itemized';
    current.result.finance.capex_eur = 22000;
    current.result.metrics.npv_eur.median = 4147.5386;
    current.result.settings.quote_total = 22000;
    current.result.settings.quote_vat_included = true; // backend normalized result forced to true
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  sheetHtml = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(sheetHtml, /€14,654/, 'Raw vat=false itemized mode preserves raw vat convention and displays €14,654 benchmark capex');
  assert.match(sheetHtml, /€8,666/, 'Raw vat=false itemized mode preserves raw vat convention and displays €8,666 benchmark NPV');
});

test('Typical day chart uses normalized capacity and weather SOC envelope; heatmap derives matrix peak', async () => {
  const { context, elements } = createTestEnvironment();
  loadAppInContext(context);

  const mockDataWith4_8 = {
    battery_kwh: 4.8,
    matrix: {
      seasons: {
        summer: [
          { hour: 0, pv: 0, load: 0.3, direct: 0, import: 0.3, export: 0, soc: 2.4, soc_envelope: [1.8, 3.0] },
          { hour: 12, pv: 5.2, load: 1.0, direct: 1.0, import: 0, export: 2.2, soc: 4.8, soc_envelope: [4.2, 4.8] }
        ]
      },
      months: Array.from({ length: 12 }, (_, m) =>
        Array.from({ length: 24 }, (_, h) => ({
          import: m === 0 && h === 8 ? 2.17 : 0.15,
          export: m === 6 && h === 13 ? 4.5 : 0.0
        }))
      )
    }
  };

  // Test 1: Normalized 4.8 kWh capacity in typical day chart
  vm.runInContext(`
    settings = { battery_kwh: 11.04 };
    current = { result: { settings: { battery_kwh: 4.8 } } };
    drawTypicalDayChart(_mockData, 'summer');
  `, Object.assign(context, { _mockData: mockDataWith4_8 }));

  let chartHtml = elements.typicalDayChart.innerHTML;
  assert.match(chartHtml, /4\.8 kWh/, 'Axis reflects normalized 4.8 kWh capacity');
  assert.doesNotMatch(chartHtml, /11\.0 kWh/, 'Does not use raw 11.04 kWh setting');
  assert.match(chartHtml, /max battery storage utilization 4\.8 kWh \(100\.0%\)/, 'Reports 100% peak utilization based on 4.8 kWh usable capacity');
  assert.match(chartHtml, /<polygon points="[^"]+" fill="#e6b65b" opacity="0.18"\/>/, 'Renders weather SOC envelope polygon');

  // Test 2: 0 capacity storage
  const mockData0 = {
    battery_kwh: 0,
    matrix: {
      seasons: {
        summer: [
          { hour: 0, pv: 0, load: 0.3, direct: 0, import: 0.3, export: 0, soc: 0 },
          { hour: 12, pv: 5.2, load: 1.0, direct: 1.0, import: 0, export: 4.2, soc: 0 }
        ]
      },
      months: mockDataWith4_8.matrix.months
    }
  };

  vm.runInContext(`
    settings = { battery_kwh: 0 };
    current = { result: { settings: { battery_kwh: 0 } } };
    drawTypicalDayChart(_mockData0, 'summer');
  `, Object.assign(context, { _mockData0: mockData0 }));

  chartHtml = elements.typicalDayChart.innerHTML;
  assert.match(chartHtml, /no battery storage installed \(0\.0 kWh\)/, 'Honest 0 storage reporting without division by zero or NaN');
  assert.doesNotMatch(chartHtml, /NaN/, 'No NaN on 0 storage');

  // Test 3: Month-hour heatmap derives peak flow from actual matrix
  vm.runInContext(`
    drawMonthHourHeatmap(_mockData, 'import');
  `, context);

  let heatmapHtml = elements.monthHourHeatmap.innerHTML;
  assert.match(heatmapHtml, /peak hourly flow reaches 2\.17 kWh\/h in Jan at 08:00–09:00/, 'Derives actual Jan 8-9 peak 2.17 kWh/h from matrix');
  assert.doesNotMatch(heatmapHtml, /8,760h/, 'No hardcoded 8,760h');
  assert.doesNotMatch(heatmapHtml, /winter evening/, 'No fictional winter evening attribution');

  // Test 4: Zero export heatmap handling
  const mockDataNoExport = {
    matrix: {
      months: Array.from({ length: 12 }, () =>
        Array.from({ length: 24 }, () => ({ import: 0.5, export: 0.0 }))
      )
    }
  };
  vm.runInContext(`
    drawMonthHourHeatmap(_mockNoExport, 'export');
  `, Object.assign(context, { _mockNoExport: mockDataNoExport }));

  heatmapHtml = elements.monthHourHeatmap.innerHTML;
  assert.match(heatmapHtml, /zero grid exports modeled across all monthly intervals/, 'Handles zero exports honestly');

  // Test 5: Wholesale market curve computed takeaway without arbitrary fallback 120 or 100
  const mockMarketData = {
    annual_baseload_eur_mwh: 114.5,
    matrix_eur_kwh: Array.from({ length: 12 }, (_, m) =>
      Array.from({ length: 24 }, (_, h) => ((m >= 5 && m <= 7 && h === 13) ? 0.0882 : 0.1145))
    ),
    series: [
      { key: 'annual', label: 'Annual Average', hourly: Array.from({ length: 24 }, () => 114.5), color: '#183b33', bold: true },
      { key: 'summer', label: 'Summer', hourly: Array.from({ length: 24 }, (_, h) => (h === 13 ? 88.2 : 110.0)), color: '#e6b65b' }
    ]
  };
  elements.marketSeasonalChart = { innerHTML: '' };
  elements.marketCurveTable = { innerHTML: '' };
  elements.marketCurveProvenance = { innerHTML: '', textContent: '' };
  elements.marketCurveWindow = { innerHTML: '', textContent: '' };

  await vm.runInContext(`
    (async () => {
      initMarketCurve();
      empiricalCache['oct2025_sep2026'] = _mockMarket;
      await renderMarketCurve('oct2025_sep2026');
    })()
  `, Object.assign(context, { _mockMarket: mockMarketData }));

  const marketChartHtml = elements.marketSeasonalChart.innerHTML;
  assert.match(marketChartHtml, /114\.[25] €\/MWh/, 'Computes annual baseload from series');
  assert.match(marketChartHtml, /~88\.2 €\/MWh/, 'Computes summer midday dip ~88.2 €/MWh');
  assert.doesNotMatch(marketChartHtml, /120/, 'No arbitrary fallback 120');
  assert.doesNotMatch(marketChartHtml, /~100/, 'No arbitrary fallback 100');
});

test('Manual ex-VAT quote offer: net 10,000 => 11,000 gross against 12,650 invented benchmark and gross 11,000 convention', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  const evalCalls = [];
  let capturedBenchmarkEval = null;
  context.fetch = win.fetch = async (url, options) => {
    if (url !== '/api/evaluate') return { ok: true, json: async () => ({}) };
    const payload = options?.body ? JSON.parse(options.body) : {};
    assertValidEngineSettings(payload.settings);
    evalCalls.push(payload);
    const pyRes = pythonEvaluate({ settings: payload.settings, quote: payload.quote });
    if (payload.quote?.price_basis === 'modeled' && (!payload.quote?.line_items || payload.quote.line_items.length === 0)) {
      capturedBenchmarkEval = pyRes;
    }
    return {
      ok: true,
      json: async () => ({
        result: pyRes.result,
        itemized_total_eur: pyRes.itemized_total_eur,
        claims: pyRes.claims,
        missing_fields: pyRes.missing_fields,
        warnings: pyRes.warnings
      })
    };
  };

  // Base scenario inputs matching reproduction:
  // south19/panel465/inverter9/battery11.04, raw settings.quote_total=10000, quote_vat_included=false, installation_vat=.1
  const rawOfferSettings = createStableQuoteScenarioFixture({
    quote_total: 10000,
    quote_vat_included: false,
    installation_vat: 0.1
  });
  rawOfferSettings.ac_heating_windows = vm.runInContext(`migrateAndResolveSchedule(${JSON.stringify(rawOfferSettings)})`, context);
  for (const k of vm.runInContext('LEGACY_HEATING_KEYS', context)) delete rawOfferSettings[k];
  const rawOfferQuote = { price_basis: 'modeled', line_items: [] };
  const initialOfferEval = pythonEvaluate({ settings: rawOfferSettings, quote: rawOfferQuote });
  assert.equal(initialOfferEval.result.finance.capex_eur, 11000, 'Real engine confirms gross offer capex €11,000');
  // Establish robust independent invariant from fixture financial cashflows and gross capex t0:
  // Gross capex at t0 is exactly -11,000 EUR, and discounted net cashflow distribution rigorously matches npv_eur.median
  const traces = initialOfferEval.result.finance.cashflows_by_weather.pv_incremental;
  const npvSettings = initialOfferEval.result.settings;
  const rate = (1 + npvSettings.real_discount_rate) * (1 + npvSettings.inflation) - 1;
  const traceNpvs = traces.map(trace => {
    assert.equal(trace[0], -11000, 't0 investment outlay matches gross offer capex €11,000');
    return trace.reduce((acc, cf, t) => acc + cf / Math.pow(1 + rate, t), 0);
  }).sort((a, b) => a - b);
  const mid = Math.floor(traceNpvs.length / 2);
  const calculatedMedianNpv = traceNpvs.length % 2 ? traceNpvs[mid] : (traceNpvs[mid - 1] + traceNpvs[mid]) / 2;
  assert.ok(Math.abs(initialOfferEval.result.metrics.npv_eur.median - calculatedMedianNpv) < 0.1, 'Offer NPV median rigorously matches discounted cashflow invariant from gross capex t0');
  const calculatedMeanNpv = traceNpvs.reduce((acc, v) => acc + v, 0) / traceNpvs.length;
  assert.ok(Math.abs(initialOfferEval.result.metrics.npv_eur.mean - calculatedMeanNpv) < 0.1, 'Offer NPV mean rigorously matches discounted cashflow invariant from gross capex t0');

  // Case 1: Manual net 10,000 ex-VAT (quote_vat_included = false, 10% VAT => 11,000 gross)
  vm.runInContext(`
    settings = ${JSON.stringify(rawOfferSettings)};
    quote = ${JSON.stringify(rawOfferQuote)};
    current = {
      result: ${JSON.stringify(initialOfferEval.result)},
      itemized_total_eur: null,
      missing_fields: []
    };
    loanPending = false;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Only one benchmark evaluate call dispatched');
  assert.equal(evalCalls[0].settings.quote_total, null, 'Benchmark evaluate clears quote_total');
  assert.equal(evalCalls[0].settings.quote_vat_included, false, 'Benchmark evaluate preserves raw quote_vat_included = false');
  assert.equal(evalCalls[0].settings.hasItemized, undefined, 'hasItemized is omitted from serialized settings');
  assert.equal(evalCalls[0].quote.price_basis, 'modeled', 'Benchmark evaluate uses modeled price basis');

  assert.ok(capturedBenchmarkEval, 'Captured benchmark evaluation response from fetch mock');
  const benchTraces = capturedBenchmarkEval.result.finance.cashflows_by_weather.pv_incremental;
  const benchSettings = capturedBenchmarkEval.result.settings;
  const benchRate = (1 + benchSettings.real_discount_rate) * (1 + benchSettings.inflation) - 1;
  const benchTraceNpvs = benchTraces.map(trace => {
    return trace.reduce((acc, cf, t) => acc + cf / Math.pow(1 + benchRate, t), 0);
  }).sort((a, b) => a - b);
  const benchMid = Math.floor(benchTraceNpvs.length / 2);
  const benchCalculatedMedianNpv = benchTraceNpvs.length % 2 ? benchTraceNpvs[benchMid] : (benchTraceNpvs[benchMid - 1] + benchTraceNpvs[benchMid]) / 2;
  assert.ok(Math.abs(capturedBenchmarkEval.result.metrics.npv_eur.median - benchCalculatedMedianNpv) < 0.1, 'Benchmark NPV median matches independently discounted cashflow invariant');
  const benchCalculatedMeanNpv = benchTraceNpvs.reduce((acc, v) => acc + v, 0) / benchTraceNpvs.length;
  assert.ok(Math.abs(capturedBenchmarkEval.result.metrics.npv_eur.mean - benchCalculatedMeanNpv) < 0.1, 'Benchmark NPV mean matches independently discounted cashflow invariant');

  const staticOfferGrossCapex = 11000;
  const staticBenchCapexCase1 = 12650;
  const staticBenchCapexCase2 = 11500;

  const capexVarianceCase1 = staticOfferGrossCapex - staticBenchCapexCase1;
  const capexVarianceCase2 = staticOfferGrossCapex - staticBenchCapexCase2;
  const npvVariance = calculatedMeanNpv - benchCalculatedMeanNpv;

  const formatDocEur = v => vm.runInContext(`fmt(${v}, "eur")`, context);

  let html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, new RegExp(formatDocEur(staticBenchCapexCase1).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'Displays real engine modeled benchmark capex €12,650');
  assert.match(html, new RegExp(formatDocEur(staticOfferGrossCapex).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'Displays contractor offer turnkey capex €11,000 gross');
  assert.match(html, /€10,000 net \+ 10(\.0)?% VAT/, 'Displays net and VAT breakdown for ex-VAT manual offer');
  assert.match(html, new RegExp(formatDocEur(capexVarianceCase1).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' vs modeled benchmark'), 'Displays negative capex variance vs benchmark with documented currency format');
  assert.match(html, new RegExp(formatDocEur(benchCalculatedMeanNpv).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'Displays independently discounted modeled benchmark NPV');
  assert.match(html, new RegExp(formatDocEur(calculatedMeanNpv).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'Displays independently discounted contractor offer NPV');
  assert.match(html, new RegExp('\\+' + formatDocEur(npvVariance).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' NPV'), 'Displays positive NPV variance');
  assert.match(html, /Modeled with offer capex \(€11,000\)/, 'Labels NPV with actual gross offer capex €11,000');

  // Case 2: Manual gross 11,000 convention (quote_vat_included = true)
  evalCalls.length = 0;
  const rawGrossSettings = { ...rawOfferSettings, quote_total: 11000, quote_vat_included: true };
  const initialGrossEval = pythonEvaluate({ settings: rawGrossSettings, quote: rawOfferQuote });
  vm.runInContext(`
    settings = ${JSON.stringify(rawGrossSettings)};
    current.result = ${JSON.stringify(initialGrossEval.result)};
    current.result.settings.quote_total = 11000;
    current.result.settings.quote_vat_included = true;
    comparisonCache = null;
    comparisonKey = '';
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Gross evaluate call dispatched');
  assert.equal(evalCalls[0].settings.quote_total, null, 'Gross benchmark clears quote_total');
  assert.equal(evalCalls[0].settings.quote_vat_included, true, 'Gross benchmark sends quote_vat_included = true');
  assert.equal(evalCalls[0].settings.hasItemized, undefined, 'Gross benchmark omits hasItemized');

  html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, new RegExp(formatDocEur(staticBenchCapexCase2).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'Displays gross convention modeled benchmark capex €11,500');
  assert.match(html, new RegExp(formatDocEur(staticOfferGrossCapex).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'Displays contractor offer turnkey capex €11,000 gross');
  assert.doesNotMatch(html, /net \+/, 'No ex-VAT breakdown when VAT is already included');
  assert.match(html, new RegExp(formatDocEur(capexVarianceCase2).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' vs modeled benchmark'), 'Displays capex variance vs gross benchmark with documented currency format');
});

test('Delayed renderer maintains rawVAT=false cost convention across queued A->B requests with normalized resultVAT=true', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  const evalCalls = [];
  let resolveA;
  const promiseA = new Promise((resolve) => { resolveA = resolve; });

  context.fetch = win.fetch = async (url, options) => {
    if (url !== '/api/evaluate') return { ok: true, json: async () => ({}) };
    const payload = options?.body ? JSON.parse(options.body) : {};
    assertValidEngineSettings(payload.settings);
    evalCalls.push(payload);
    if (evalCalls.length === 1) {
      await promiseA;
    }
    const s = payload.settings;
    const isBench = payload.quote.price_basis === 'modeled' && (!payload.quote.line_items || payload.quote.line_items.length === 0);
    const vatInc = s.quote_vat_included !== false;
    const capex = isBench ? (vatInc ? 13321.515 : 14653.6665) : 22000;
    const npv = isBench ? (vatInc ? 9485.1171 : 8665.7963) : 4147.5386;
    return {
      ok: true,
      json: async () => ({
        result: {
          layout: { kwp: 8.835, panel_count: 19 },
          finance: { capex_eur: capex },
          metrics: {
            npv_eur: { min: npv - 500, median: npv, max: npv + 500 },
            discounted_payback_years: { median: 8.0 }
          },
          settings: { ...s, battery_kwh: 4.8 }
        }
      })
    };
  };

  // Step 1: Modeled mode with raw VAT = false and manual offer 10000
  vm.runInContext(`
    settings = { panel_watts: 465, battery_kwh: 11.04, quote_vat_included: false };
    quote = { price_basis: 'modeled', line_items: [] };
    current = {
      result: {
        layout: { kwp: 8.835, panel_count: 19 },
        finance: { capex_eur: 11000 },
        metrics: { npv_eur: { min: 11500, median: 12000, max: 12500 } },
        settings: { panel_watts: 465, battery_kwh: 4.8, quote_total: 10000, quote_vat_included: false }
      },
      itemized_total_eur: null,
      missing_fields: []
    };
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(evalCalls.length, 1, 'Request A is dispatched and in flight');

  // Step 2: Switch to itemized while request A is in flight. Backend normalized resultVAT to true!
  vm.runInContext(`
    quote.price_basis = 'itemized';
    quote.line_items = [{ description: 'PV', net_eur: 20000, vat: 0.1, quantity: 1 }];
    current.itemized_total_eur = 22000;
    current.result.finance.capex_eur = 22000;
    current.result.metrics.npv_eur.median = 4147.5386;
    current.result.settings.quote_total = 22000;
    current.result.settings.quote_vat_included = true; // backend normalized result forced to true!
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(evalCalls.length, 1, 'Request B is queued, not executed yet');

  // Step 3: Resolve delayed request A
  resolveA();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 2, 'Request B fired after request A completed');
  assert.equal(evalCalls[1].settings.hasItemized, undefined, 'Queued request B does not contain client-only hasItemized flag');
  assert.equal(evalCalls[1].settings.quote_vat_included, false, 'Queued request B retained raw quote_vat_included = false despite result.settings.quote_vat_included = true');
  assert.equal(evalCalls[1].settings.battery_kwh, 4.8, 'Queued request B retained normalized 4.8 kWh equipment');

  const html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, /€14,654/, 'Displays VAT-exclusive benchmark €14,654');
  assert.doesNotMatch(html, /€13,322/, 'Does not display VAT-inclusive benchmark €13,322');
});

test('Backend contract enforcement: engine SPEC rejects hasItemized and client never sends client-only flags', async () => {
  const { context } = createTestEnvironment();
  loadAppInContext(context);

  // 1. Calling assertValidEngineSettings with hasItemized must throw
  assert.throws(() => {
    assertValidEngineSettings({ hasItemized: true, battery_kwh: 5.0 });
  }, /Unknown settings: hasItemized/, 'Validator rejects hasItemized');

  // 2. Real Python engine quote_evaluate rejects hasItemized with ValueError
  assert.throws(() => {
    pythonEvaluate({ settings: { hasItemized: true, south_panels: 10 }, quote: { price_basis: 'modeled' } });
  }, /Unknown settings: hasItemized/, 'Real Python engine rejects hasItemized');

  // 3. Client getComparisonPayload never puts hasItemized or client-only flags on evalSettings
  const testSettings = { south_panels: 19, quote_total: 10000, quote_vat_included: false, hasItemized: true };
  const payload = vm.runInContext(`
    getComparisonPayload(${JSON.stringify(testSettings)}, { price_basis: 'modeled', line_items: [] }, true, 10000)
  `, context);

  assert.equal(payload.evalSettings.hasItemized, undefined, 'evalSettings has no hasItemized flag');
  assertValidEngineSettings(payload.evalSettings);
  assert.equal(payload.evalSettings.quote_total, null, 'Benchmark clears quote_total');
  assert.equal(payload.evalSettings.quote_vat_included, false, 'Benchmark preserves raw VAT false');
});

test('Reference-only and active itemized offer: real Python engine evaluation parity', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  const evalCalls = [];
  context.fetch = win.fetch = async (url, options) => {
    if (url !== '/api/evaluate') return { ok: true, json: async () => ({}) };
    const payload = options?.body ? JSON.parse(options.body) : {};
    assertValidEngineSettings(payload.settings);
    evalCalls.push(payload);
    const pyRes = pythonEvaluate({ settings: payload.settings, quote: payload.quote });
    return {
      ok: true,
      json: async () => ({
        result: pyRes.result,
        itemized_total_eur: pyRes.itemized_total_eur,
        claims: pyRes.claims,
        missing_fields: pyRes.missing_fields,
        warnings: pyRes.warnings
      })
    };
  };

  const baseSettings = createStableQuoteScenarioFixture({
    quote_total: null,
    quote_vat_included: false,
    installation_vat: 0.1
  });
  const itemizedQuote = {
    price_basis: 'modeled', // reference-only itemized lines
    line_items: [{ description: 'PV turnkey package', net_eur: 20000, vat: 0.1, quantity: 1 }]
  };

  // Evaluate reference-only itemized with real Python engine
  const refEval = pythonEvaluate({ settings: baseSettings, quote: itemizedQuote });
  assert.equal(refEval.itemized_total_eur, 22000, 'Real engine computes itemized total €22,000');
  assert.equal(refEval.result.finance.capex_eur, (1000 + 4*2000 + 5*500)*1.1, 'Real engine modeled active capex is benchmark capex');

  vm.runInContext(`
    settings = ${JSON.stringify(baseSettings)};
    quote = ${JSON.stringify(itemizedQuote)};
    current = {
      result: ${JSON.stringify(refEval.result)},
      itemized_total_eur: ${refEval.itemized_total_eur},
      missing_fields: []
    };
    loanPending = false;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Dispatched evaluate for reference-only itemized offer');
  assert.equal(evalCalls[0].settings.hasItemized, undefined, 'No hasItemized in request settings');
  assert.equal(evalCalls[0].quote.price_basis, 'itemized', 'Offer evaluate requests itemized price basis');

  let html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, /€12,650/, 'Displays modeled benchmark capex €12,650');
  assert.match(html, /€22,000/, 'Displays reference itemized offer gross capex €22,000');
  assert.match(html, /\+€9,350 vs modeled benchmark/, 'Displays capex variance vs benchmark');

  // Now switch to active itemized
  evalCalls.length = 0;
  itemizedQuote.price_basis = 'itemized';
  const activeEval = pythonEvaluate({ settings: baseSettings, quote: itemizedQuote });
  assert.equal(activeEval.result.finance.capex_eur, 22000, 'Active itemized uses offer capex €22,000');

  vm.runInContext(`
    quote.price_basis = 'itemized';
    current.result = ${JSON.stringify(activeEval.result)};
    comparisonCache = null;
    comparisonKey = '';
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Dispatched evaluate for benchmark comparison');
  assert.equal(evalCalls[0].settings.hasItemized, undefined, 'Benchmark evaluate omits hasItemized');
  assert.equal(evalCalls[0].settings.quote_total, null, 'Benchmark evaluate clears quote_total');

  html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, /€12,650/, 'Displays modeled benchmark capex €12,650');
  assert.match(html, /€22,000/, 'Displays active contractor offer capex €22,000');
});

test('Blank draft line, manual quote with blank draft, and mixed valid/blank line semantics', async () => {
  const { context, elements, win } = createTestEnvironment();
  loadAppInContext(context);

  const evalCalls = [];
  context.fetch = win.fetch = async (url, options) => {
    if (url !== '/api/evaluate') return { ok: true, json: async () => ({}) };
    const payload = options?.body ? JSON.parse(options.body) : {};
    assertValidEngineSettings(payload.settings);
    evalCalls.push(payload);
    const pyRes = pythonEvaluate({ settings: payload.settings, quote: payload.quote });
    return {
      ok: true,
      json: async () => ({
        result: pyRes.result,
        itemized_total_eur: pyRes.itemized_total_eur,
        claims: pyRes.claims,
        missing_fields: pyRes.missing_fields,
        warnings: pyRes.warnings
      })
    };
  };

  // Case 1: Manual quote supplied alongside blank draft line
  // Manual quote must still work, evaluate benchmark, and avoid scaffolding/GSE gaps
  const test1Settings = createStableQuoteScenarioFixture({
    quote_total: 10000,
    quote_vat_included: false,
    installation_vat: 0.1
  });
  test1Settings.ac_heating_windows = vm.runInContext(`migrateAndResolveSchedule(${JSON.stringify(test1Settings)})`, context);
  for (const k of vm.runInContext('LEGACY_HEATING_KEYS', context)) delete test1Settings[k];
  vm.runInContext(`
    settings = ${JSON.stringify(test1Settings)};
    quote = { price_basis: 'modeled', line_items: [{ description: '', net_eur: null, vat: 0.1, quantity: 1 }] };
    current = {
      result: {
        layout: { kwp: 8.835, panel_count: 19 },
        finance: { capex_eur: 11000 },
        metrics: { npv_eur: { min: 11000, median: 12013.41, max: 13000 }, discounted_payback_years: { median: 6.2 }, payback_years: { min: 5, median: 5.5, max: 6 } },
        settings: ${JSON.stringify(test1Settings)}
      },
      itemized_total_eur: null,
      missing_fields: []
    };
    loanPending = false;
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Dispatches benchmark evaluate for manual quote with blank draft line');
  assert.equal(evalCalls[0].settings.hasItemized, undefined);
  let html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, /€11,000/, 'Displays manual offer capex €11,000 gross');
  assert.match(html, /€12,650/, 'Displays modeled benchmark capex €12,650');
  assert.doesNotMatch(html, /Scaffolding \/ roof safety/, 'No scaffolding disclosure gap when itemized offer is not populated');
  assert.doesNotMatch(html, /GSE grid connection/, 'No GSE disclosure gap when itemized offer is not populated');

  // Case 2: Mixed valid line and blank draft line
  evalCalls.length = 0;
  vm.runInContext(`
    quote.line_items = [
      { description: 'Solar PV Panels', net_eur: 10000, vat: 0.1, quantity: 1 },
      { description: '', net_eur: null, vat: 0.1, quantity: 1 }
    ];
    current.itemized_total_eur = 11000;
    current.result.settings.quote_total = null;
    settings.quote_total = null;
    comparisonCache = null;
    comparisonKey = '';
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Dispatches evaluate for populated itemized offer despite blank line');
  assert.equal(evalCalls[0].settings.hasItemized, undefined);
  html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, /€11,000/, 'Displays itemized offer €11,000 from populated line');

  // Case 3: Legitimate zero-euro populated line
  evalCalls.length = 0;
  vm.runInContext(`
    quote.line_items = [
      { description: 'Free solar promotion', net_eur: 0, vat: 0.1, quantity: 1 }
    ];
    current.itemized_total_eur = 0;
    current.result.finance.capex_eur = 0;
    comparisonCache = null;
    comparisonKey = '';
    renderQuoteComparisonSheet();
  `, context);

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(evalCalls.length, 1, 'Dispatches evaluate for legitimate zero-euro populated offer');
  assert.equal(evalCalls[0].settings.hasItemized, undefined);
  html = elements.quoteComparisonSheetContent.innerHTML;
  assert.match(html, /€0/, 'Displays zero-euro offer');
});

test('Evidence-link reveal and keyboard mode navigation', () => {
  const { context, elements, doc } = createTestEnvironment();
  loadAppInContext(context);

  const mockResult = {
    assumptions: [
      { name: 'retail_commodity', value: 0.1169, unit: '€/kWh', status: 'measured', source: 'Enel invoice', date_accessed: '2026-10-01', owner: 'User' },
      { name: 'dhw_resistance_efficiency', value: 0.95, unit: 'fraction', status: 'assumed', source: 'Standard boiler spec', date_accessed: '2026-10-02', owner: 'Model' }
    ]
  };
  setCurrent(context, mockResult);

  vm.runInContext(`
    schema = {
      defaults: { retail_commodity: 0.1169, dhw_heating_type: 'heat_pump', dhw_resistance_efficiency: 0.95 },
      records: [
        { name: 'retail_commodity', label: 'Electricity price', group: 'bill' },
        { name: 'dhw_resistance_efficiency', label: 'DHW Resistance Boiler Efficiency', group: 'loads' }
      ]
    };
    settings = { ...schema.defaults };
  `, context);

  // 1. Radiogroup keyboard navigation
  const modeSelector = elements.sidebarFilterMode || {
    id: 'sidebarFilterMode',
    children: [],
    querySelectorAll: (sel) => sel === '.sidebar-mode-btn' ? modeSelector.children : []
  };
  function makeModeBtn(mode, isDefault = false) {
    return {
      tagName: 'BUTTON',
      dataset: { mode },
      classList: {
        classes: new Set(isDefault ? ['active'] : []),
        add(c) { this.classes.add(c); },
        remove(c) { this.classes.delete(c); },
        toggle(c, f) { if (f === undefined) { if (this.classes.has(c)) this.classes.delete(c); else this.classes.add(c); } else if (f) this.classes.add(c); else this.classes.delete(c); },
        contains(c) { return this.classes.has(c); }
      },
      tabIndex: isDefault ? 0 : -1,
      attributes: { 'aria-checked': isDefault ? 'true' : 'false', tabindex: isDefault ? '0' : '-1' },
      setAttribute(k, v) { this.attributes[k] = String(v); if (k === 'tabindex') this.tabIndex = Number(v); },
      getAttribute(k) { return this.attributes[k]; },
      focus() { modeSelector.focused = this; },
      onclick: null,
      onkeydown: null
    };
  }
  const btnAll = makeModeBtn('all', true);
  const btnView = makeModeBtn('view', false);
  const btnModified = makeModeBtn('modified', false);
  modeSelector.children = [btnAll, btnView, btnModified];
  elements.sidebarFilterMode = modeSelector;

  vm.runInContext('initSidebarModeSelector();', context);
  assert.equal(btnAll.tabIndex, 0);
  assert.equal(btnView.tabIndex, -1);
  assert.equal(btnModified.tabIndex, -1);
  assert.equal(btnAll.getAttribute('aria-checked'), 'true');

  // Key navigation: ArrowRight moves all -> view
  btnAll.onkeydown({ key: 'ArrowRight', preventDefault() {} });
  assert.equal(vm.runInContext('sidebarFilterMode', context), 'view');
  assert.equal(btnView.tabIndex, 0);
  assert.equal(btnAll.tabIndex, -1);
  assert.equal(btnView.getAttribute('aria-checked'), 'true');
  assert.equal(btnAll.getAttribute('aria-checked'), 'false');

  // Key navigation: End moves to modified
  btnView.onkeydown({ key: 'End', preventDefault() {} });
  assert.equal(vm.runInContext('sidebarFilterMode', context), 'modified');
  assert.equal(btnModified.tabIndex, 0);
  assert.equal(btnModified.getAttribute('aria-checked'), 'true');

  // Key navigation: Home moves back to all
  btnModified.onkeydown({ key: 'Home', preventDefault() {} });
  assert.equal(vm.runInContext('sidebarFilterMode', context), 'all');
  assert.equal(btnAll.tabIndex, 0);
  assert.equal(btnAll.getAttribute('aria-checked'), 'true');

  // 2. Evidence link click from ledger
  vm.runInContext('renderLedger(current.result);', context);
  const ledgerEl = elements.ledger;
  assert.match(ledgerEl.innerHTML, /data-focus-setting="retail_commodity"/);

  let focusedEditor = null;
  const mockRetailControl = {
    dataset: { control: 'retail_commodity' },
    parentElement: null,
    classList: {
      classes: new Set(),
      pulsed: false,
      add(c) { this.classes.add(c); if (c === 'highlight-pulse') this.pulsed = true; },
      remove(c) { this.classes.delete(c); },
      contains(c) { return this.classes.has(c); }
    },
    querySelector: () => ({ focus() { focusedEditor = 'retail_commodity'; } }),
    scrollIntoView: () => { mockRetailControl.scrolled = true; }
  };
  const mockCategoryAccordion = {
    tagName: 'DETAILS',
    open: false,
    classList: { contains: () => true },
    parentElement: null
  };
  mockRetailControl.parentElement = mockCategoryAccordion;

  const mockAside = {
    tagName: 'ASIDE',
    classList: {
      classes: new Set(),
      add(c) { this.classes.add(c); },
      contains(c) { return this.classes.has(c); }
    }
  };

  const origDocQuerySelector = doc.querySelector;
  doc.querySelector = (sel) => {
    if (sel === 'aside') return mockAside;
    if (sel.includes('retail_commodity')) return mockRetailControl;
    if (sel.includes('details.category-accordion')) return mockCategoryAccordion;
    return origDocQuerySelector(sel);
  };

  // Click on retail_commodity evidence link when search is active and mode is modified
  vm.runInContext(`
    activeSearch = 'unrelated search';
    sidebarFilterMode = 'modified';
    settings.dhw_heating_type = 'heat_pump';
  `, context);

  ledgerEl.onclick({
    preventDefault() {},
    target: {
      closest: (sel) => sel === '[data-focus-setting]' ? { dataset: { focusSetting: 'retail_commodity' } } : null
    }
  });

  assert.equal(vm.runInContext('activeSearch', context), '', 'Search is cleared');
  assert.equal(vm.runInContext('sidebarFilterMode', context), 'all', 'Filter mode switches to All inputs');
  assert.equal(mockCategoryAccordion.open, true, 'Category accordion is expanded');
  assert.equal(mockAside.classList.contains('inputs-open'), true, 'Mobile inputs are expanded');
  assert.equal(mockRetailControl.scrolled, true, 'Field is scrolled into view');
  assert.equal(mockRetailControl.classList.pulsed, true, 'Pulse effect is applied');
  assert.equal(focusedEditor, 'retail_commodity', 'Editor input is focused');

  // 3. Evidence link click for conditionally unavailable setting (e.g. dhw_resistance_efficiency when dhw_heating_type is heat_pump)
  const settingsBefore = vm.runInContext('JSON.stringify(settings);', context);
  focusedEditor = null;
  ledgerEl.onclick({
    preventDefault() {},
    target: {
      closest: (sel) => sel === '[data-focus-setting]' ? { dataset: { focusSetting: 'dhw_resistance_efficiency' } } : null
    }
  });

  const settingsAfter = vm.runInContext('JSON.stringify(settings);', context);
  assert.equal(settingsAfter, settingsBefore, 'Scenario values are not changed when field is conditionally unavailable');
  assert.match(elements.status.textContent, /unavailable because DHW heating type is set to heat pump/i, 'Status explains conditional unavailability');
  assert.equal(focusedEditor, null, 'Conditionally unavailable field editor is not focused');
});

test('Executive verdict scope and cash basis: reload round-trips of all four pairs, preservation, and storage fallbacks', () => {
  const pairs = [
    { scope: 'solar', cash: 'cash', flowKey: 'pv_incremental' },
    { scope: 'solar', cash: 'loan', flowKey: 'solar_equity' },
    { scope: 'package', cash: 'cash', flowKey: 'whole_package' },
    { scope: 'package', cash: 'loan', flowKey: 'package_equity' }
  ];

  // 1. Reload round-trips of all four pairs via actual click handlers and startup restoration
  for (const { scope, cash, flowKey } of pairs) {
    const env1 = createTestEnvironment();
    loadAppInContext(env1.context);

    // Exercise actual click handlers attached by initRedesignEventListeners
    if (scope === 'solar') env1.elements.scopeSolarBtn.onclick();
    else env1.elements.scopePackageBtn.onclick();

    if (cash === 'cash') env1.elements.cashBeforeBtn.onclick();
    else env1.elements.cashAfterBtn.onclick();

    // Verify current state before reload
    assert.strictEqual(vm.runInContext('summaryScope', env1.context), scope);
    assert.strictEqual(vm.runInContext('summaryCash', env1.context), cash);
    assert.strictEqual(env1.elements.cashFlowBasis.value, flowKey);

    // Simulate page reload with identical localStorage state
    const env2 = createTestEnvironment();
    env2.win.localStorage.data = { ...env1.win.localStorage.data };
    env2.context.localStorage.data = env2.win.localStorage.data;
    loadAppInContext(env2.context);

    // Exercise actual startup/read path: assert restored variables, active classes, aria-pressed, and cashFlowBasis
    assert.strictEqual(vm.runInContext('summaryScope', env2.context), scope, `Restored scope must be ${scope}`);
    assert.strictEqual(vm.runInContext('summaryCash', env2.context), cash, `Restored cash must be ${cash}`);

    assert.strictEqual(env2.elements.scopeSolarBtn.classList.contains('active'), scope === 'solar');
    assert.strictEqual(env2.elements.scopeSolarBtn.getAttribute('aria-pressed'), String(scope === 'solar'));
    assert.strictEqual(env2.elements.scopePackageBtn.classList.contains('active'), scope === 'package');
    assert.strictEqual(env2.elements.scopePackageBtn.getAttribute('aria-pressed'), String(scope === 'package'));

    assert.strictEqual(env2.elements.cashBeforeBtn.classList.contains('active'), cash === 'cash');
    assert.strictEqual(env2.elements.cashBeforeBtn.getAttribute('aria-pressed'), String(cash === 'cash'));
    assert.strictEqual(env2.elements.cashAfterBtn.classList.contains('active'), cash === 'loan');
    assert.strictEqual(env2.elements.cashAfterBtn.getAttribute('aria-pressed'), String(cash === 'loan'));

    assert.strictEqual(env2.elements.cashFlowBasis.value, flowKey, `Restored canonical cashFlowBasis must be ${flowKey}`);
  }

  // 2. Changing either choice preserves the other without erasing
  {
    const env = createTestEnvironment();
    loadAppInContext(env.context);

    // Set to package / loan
    env.elements.scopePackageBtn.onclick();
    env.elements.cashAfterBtn.onclick();
    assert.strictEqual(vm.runInContext('summaryScope', env.context), 'package');
    assert.strictEqual(vm.runInContext('summaryCash', env.context), 'loan');

    // Change only cash basis to cash -> scope must remain package
    env.elements.cashBeforeBtn.onclick();
    assert.strictEqual(vm.runInContext('summaryScope', env.context), 'package', 'Scope remains package when toggling cash basis');
    assert.strictEqual(vm.runInContext('summaryCash', env.context), 'cash', 'Cash basis toggles to cash');
    assert.strictEqual(env.elements.cashFlowBasis.value, 'whole_package');

    // Reload preserves package + cash
    const reloadEnv1 = createTestEnvironment();
    reloadEnv1.win.localStorage.data = { ...env.win.localStorage.data };
    reloadEnv1.context.localStorage.data = reloadEnv1.win.localStorage.data;
    loadAppInContext(reloadEnv1.context);
    assert.strictEqual(vm.runInContext('summaryScope', reloadEnv1.context), 'package');
    assert.strictEqual(vm.runInContext('summaryCash', reloadEnv1.context), 'cash');
    assert.strictEqual(reloadEnv1.elements.cashFlowBasis.value, 'whole_package');
    assert.strictEqual(reloadEnv1.elements.scopePackageBtn.classList.contains('active'), true);
    assert.strictEqual(reloadEnv1.elements.cashBeforeBtn.classList.contains('active'), true);

    // From reloadEnv1, change only scope to solar -> cash basis must remain cash
    reloadEnv1.elements.scopeSolarBtn.onclick();
    assert.strictEqual(vm.runInContext('summaryScope', reloadEnv1.context), 'solar', 'Scope toggles to solar');
    assert.strictEqual(vm.runInContext('summaryCash', reloadEnv1.context), 'cash', 'Cash basis remains cash when toggling scope');
    assert.strictEqual(reloadEnv1.elements.cashFlowBasis.value, 'pv_incremental');

    // Reload preserves solar + cash
    const reloadEnv2 = createTestEnvironment();
    reloadEnv2.win.localStorage.data = { ...reloadEnv1.win.localStorage.data };
    reloadEnv2.context.localStorage.data = reloadEnv2.win.localStorage.data;
    loadAppInContext(reloadEnv2.context);
    assert.strictEqual(vm.runInContext('summaryScope', reloadEnv2.context), 'solar');
    assert.strictEqual(vm.runInContext('summaryCash', reloadEnv2.context), 'cash');
    assert.strictEqual(reloadEnv2.elements.cashFlowBasis.value, 'pv_incremental');
    assert.strictEqual(reloadEnv2.elements.scopeSolarBtn.classList.contains('active'), true);
    assert.strictEqual(reloadEnv2.elements.cashBeforeBtn.classList.contains('active'), true);
  }

  // 3. Corrupt, invalid, and unavailable storage defaults to solar / cash
  const fallbackCases = [
    { label: 'malformed JSON', data: '{bad-json-syntax' },
    { label: 'array payload', data: '["package", "loan"]' },
    { label: 'primitive payload', data: '42' },
    { label: 'completely invalid fields', data: JSON.stringify({ scope: 'unknown', cash: 'crypto' }) },
    { label: 'empty object', data: '{}' },
    { label: 'null raw string', data: 'null' }
  ];

  for (const { label, data } of fallbackCases) {
    const env = createTestEnvironment();
    env.win.localStorage.setItem('pv-lab-summary-prefs-v1', data);
    env.context.localStorage.data = env.win.localStorage.data;
    loadAppInContext(env.context);

    assert.strictEqual(vm.runInContext('summaryScope', env.context), 'solar', `Fallback for ${label} must default scope to solar`);
    assert.strictEqual(vm.runInContext('summaryCash', env.context), 'cash', `Fallback for ${label} must default cash to cash`);
    assert.strictEqual(env.elements.scopeSolarBtn.classList.contains('active'), true);
    assert.strictEqual(env.elements.scopeSolarBtn.getAttribute('aria-pressed'), 'true');
    assert.strictEqual(env.elements.scopePackageBtn.classList.contains('active'), false);
    assert.strictEqual(env.elements.cashBeforeBtn.classList.contains('active'), true);
    assert.strictEqual(env.elements.cashBeforeBtn.getAttribute('aria-pressed'), 'true');
    assert.strictEqual(env.elements.cashAfterBtn.classList.contains('active'), false);
    assert.strictEqual(env.elements.cashFlowBasis.value, 'pv_incremental');
  }

  // 4. Per-field validation: valid field is preserved while invalid field falls back
  {
    // Valid scope 'package', invalid cash
    const envScopeValid = createTestEnvironment();
    envScopeValid.win.localStorage.setItem('pv-lab-summary-prefs-v1', JSON.stringify({ scope: 'package', cash: 'invalid' }));
    envScopeValid.context.localStorage.data = envScopeValid.win.localStorage.data;
    loadAppInContext(envScopeValid.context);
    assert.strictEqual(vm.runInContext('summaryScope', envScopeValid.context), 'package');
    assert.strictEqual(vm.runInContext('summaryCash', envScopeValid.context), 'cash');
    assert.strictEqual(envScopeValid.elements.cashFlowBasis.value, 'whole_package');

    // Invalid scope, valid cash 'loan'
    const envCashValid = createTestEnvironment();
    envCashValid.win.localStorage.setItem('pv-lab-summary-prefs-v1', JSON.stringify({ scope: 'invalid', cash: 'loan' }));
    envCashValid.context.localStorage.data = envCashValid.win.localStorage.data;
    loadAppInContext(envCashValid.context);
    assert.strictEqual(vm.runInContext('summaryScope', envCashValid.context), 'solar');
    assert.strictEqual(vm.runInContext('summaryCash', envCashValid.context), 'loan');
    assert.strictEqual(envCashValid.elements.cashFlowBasis.value, 'solar_equity');
  }

  // 5. Storage unavailable on read (throws exception)
  {
    const envBlockedRead = createTestEnvironment();
    const origGet = envBlockedRead.win.localStorage.getItem.bind(envBlockedRead.win.localStorage);
    envBlockedRead.win.localStorage.getItem = (k) => {
      if (k === 'pv-lab-summary-prefs-v1') throw new Error('Blocked / SecurityError');
      return origGet(k);
    };
    envBlockedRead.context.localStorage = envBlockedRead.win.localStorage;
    loadAppInContext(envBlockedRead.context);
    assert.strictEqual(vm.runInContext('summaryScope', envBlockedRead.context), 'solar');
    assert.strictEqual(vm.runInContext('summaryCash', envBlockedRead.context), 'cash');
    assert.strictEqual(envBlockedRead.elements.cashFlowBasis.value, 'pv_incremental');
  }

  // 6. Storage unavailable or quota exceeded on write: clicking handlers does not throw and updates UI
  {
    const envBlockedWrite = createTestEnvironment();
    loadAppInContext(envBlockedWrite.context);
    envBlockedWrite.win.localStorage.setItem = () => { throw new Error('QuotaExceededError'); };
    envBlockedWrite.context.localStorage = envBlockedWrite.win.localStorage;

    assert.doesNotThrow(() => {
      envBlockedWrite.elements.scopePackageBtn.onclick();
      envBlockedWrite.elements.cashAfterBtn.onclick();
    });

    assert.strictEqual(vm.runInContext('summaryScope', envBlockedWrite.context), 'package');
    assert.strictEqual(vm.runInContext('summaryCash', envBlockedWrite.context), 'loan');
    assert.strictEqual(envBlockedWrite.elements.cashFlowBasis.value, 'package_equity');
    assert.strictEqual(envBlockedWrite.elements.scopePackageBtn.classList.contains('active'), true);
    assert.strictEqual(envBlockedWrite.elements.cashAfterBtn.classList.contains('active'), true);
  }
});



