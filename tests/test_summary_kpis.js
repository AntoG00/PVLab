'use strict';
/* Focused unit tests for Executive Verdict KPI refinements:
 * 1. Cash outlay card after loan repayments: total CAPEX split solar+storage vs electrification and loan amount.
 * 2. Whole-House Equity NPV card: primary NPV + weather range preserved, muted secondary Undiscounted N-y net cash: +€X from weather traces.
 * 3. Solar Coverage / Autarky subtext: annual kWh demand and generation anchors from physical quantities.
 * 4. Behavior preservation for solar-only, cash purchases, absent electrification/loan, and existing preview test fixtures.
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
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        innerHTML: '',
        textContent: '',
        style: {},
        classList: {
          toggle: () => {},
          contains: () => false
        },
        setAttribute: () => {}
      });
    }
    return elements.get(id);
  };

  const sandbox = {
    $,
    document: { getElementById: $ },
    title: s => typeof s !== 'string' ? '' : (s.charAt(0).toUpperCase() + s.slice(1).replaceAll('_', ' ')),
    esc: s => String(s ?? '').replace(/[&<>"\']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    getWeatherEnsembleMeta: () => ({ count: 20 }),
    updateAssumptionsBadge: () => {},
    current: { result: null },
    summaryScope: 'whole',
    summaryCash: 'loan',
    quote: { price_basis: 'modeled', use_itemized: false },
    settings: { quote_total: null },
    isItemizedPricingActive: () => false,
    nf: (n, d = 0) => n == null ? '—' : Number(n).toLocaleString('en-IE', { maximumFractionDigits: d, minimumFractionDigits: d }),
    fmt: (n, type = 'number') => n == null ? '—' : type === 'eur' ? '€' + (n == null ? '—' : Number(n).toLocaleString('en-IE', { maximumFractionDigits: 0, minimumFractionDigits: 0 })) : type === 'eur2' ? '€' + (n == null ? '—' : Number(n).toLocaleString('en-IE', { maximumFractionDigits: 2, minimumFractionDigits: 2 })) : type === 'pct' ? (n * 100).toFixed(1) + '%' : type === 'lcoe' ? '€' + Number(n).toFixed(3) : type === 'years' ? Number(n).toFixed(1) + ' y' : Number(n).toLocaleString('en-IE'),
    range: (r, type = 'number') => !r ? '—' : r.min == null ? (type === 'years' ? 'Not reached' : '—') : (type === 'pct' ? (r.min * 100).toFixed(1) + '%' : (type === 'eur' ? '€' + Number(r.min).toLocaleString('en-IE') : Number(r.min).toLocaleString('en-IE'))) + (r.max == null ? ' – beyond horizon' : Math.abs(r.max - r.min) < 1e-7 ? '' : ' – ' + (type === 'pct' ? (r.max * 100).toFixed(1) + '%' : (type === 'eur' ? '€' + Number(r.max).toLocaleString('en-IE') : Number(r.max).toLocaleString('en-IE')))),
    getMetricDefAnchor: () => null
  };

  const codeSlice = appSource.substring(
    appSource.indexOf('function getScopeCashMetrics'),
    appSource.indexOf('function updateAssumptionsBadge')
  );

  vm.createContext(sandbox);
  vm.runInContext(codeSlice, sandbox);

  const renderStart = appSource.indexOf('function render(');
  const stripCall = 'renderVerdictStrip(r);';
  const renderEnd = renderStart !== -1 ? appSource.indexOf(stripCall, renderStart) : -1;
  if (renderStart !== -1 && renderEnd !== -1) {
    const renderPrefix = appSource.substring(renderStart, renderEnd) + "\n}";
    vm.runInContext(renderPrefix, sandbox);
  }

  function renderSummary(result) {
    sandbox.current.result = result;
    sandbox.render(result);
  }

  return { sandbox, elements, renderSummary };
}

// -------------------------------------------------------------
// Test 1: Cash outlay card after loan repayments with electrification
// -------------------------------------------------------------
{
  const { sandbox } = createHarness();
  const mockResult = {
    settings: { cashflow_mode: 'nominal', inflation: 0.02, horizon_years: 20 },
    finance: {
      capex_eur: 15000,
      conversion_capex_eur: 5000,
      loan_principal_eur: 18000,
      solar_loan_principal_eur: 13500,
      cashflows_by_weather: {
        package_equity: [
          [-2000, 1000, 1000, 1000, 1000]
        ]
      }
    },
    metrics: {}
  };

  const data = sandbox.getScopeCashMetrics(mockResult, 'whole', 'loan');
  assert.equal(data.capexTitle, 'PERSONAL CASH OUTLAY (AFTER LOAN)');
  assert.equal(data.capexVal, 2000);
  assert.ok(data.capexExtra.includes('Total CAPEX: €20,000'), `Expected Total CAPEX: €20,000 in "${data.capexExtra}"`);
  assert.ok(data.capexExtra.includes('€15,000 solar+storage'), `Expected solar+storage split in "${data.capexExtra}"`);
  assert.ok(data.capexExtra.includes('€5,000 electrification'), `Expected electrification split in "${data.capexExtra}"`);
  assert.ok(data.capexExtra.includes('Loan: €18,000'), `Expected loan amount in "${data.capexExtra}"`);
}

// -------------------------------------------------------------
// Test 2: Whole-House Equity NPV card with undiscounted N-y net cash
// -------------------------------------------------------------
{
  const { sandbox, elements } = createHarness();
  sandbox.summaryScope = 'whole';
  sandbox.summaryCash = 'loan';

  // 3 weather traces over 3 years: year 0 initial outlay -2000
  // Trace 1 net: -2000 + 2000 + 2000 = +2000
  // Trace 2 net: -2000 + 3000 + 3000 = +4000
  // Trace 3 net: -2000 + 4000 + 4000 = +6000
  // Median undiscounted 2-y net cash = +4000
  const mockResult = {
    settings: { cashflow_mode: 'nominal', inflation: 0.02, horizon_years: 2 },
    finance: {
      capex_eur: 10000,
      conversion_capex_eur: 2000,
      loan_principal_eur: 10000,
      package_equity: {
        npv: { median: 3500, mean: 3500, min: 2800, max: 4200 }
      },
      cashflows_by_weather: {
        package_equity: [
          [-2000, 2000, 2000],
          [-2000, 3000, 3000],
          [-2000, 4000, 4000]
        ]
      }
    },
    metrics: {}
  };

  const data = sandbox.getScopeCashMetrics(mockResult, 'whole', 'loan');
  assert.equal(data.npvTitle, 'WHOLE-HOUSE EQUITY NPV');
  assert.equal(data.npvObj.median, 3500);
  assert.ok(data.npvExtra.includes('Versus historic bills after loan debt service'));
  assert.ok(data.npvExtra.includes('Undiscounted 2-y net cash: +€4,000'), `Expected undiscounted net cash in "${data.npvExtra}"`);

  sandbox.renderVerdictStrip(mockResult);
  const metricsHtml = elements.get('metrics').innerHTML;
  assert.ok(metricsHtml.includes('WHOLE-HOUSE EQUITY NPV'), 'NPV title in card HTML');
  assert.ok(metricsHtml.includes('€3,500'), 'Primary NPV value in card HTML');
  assert.ok(metricsHtml.includes('Historical min–max') || metricsHtml.includes('Weather range'), 'Weather bounds preserved in card HTML');
  assert.ok(metricsHtml.includes('Undiscounted 2-y net cash: +€4,000'), 'Muted secondary undiscounted cash in card HTML');
}

// -------------------------------------------------------------
// Test 3: Whole-House Equity NPV with negative net cash
// -------------------------------------------------------------
{
  const { sandbox } = createHarness();
  const mockResult = {
    settings: { cashflow_mode: 'nominal', inflation: 0.02, horizon_years: 2 },
    finance: {
      capex_eur: 10000,
      conversion_capex_eur: 2000,
      loan_principal_eur: 5000,
      package_equity: {
        npv: { median: -1200, mean: -1200, min: -2000, max: -500 }
      },
      cashflows_by_weather: {
        package_equity: [
          [-7000, 1000, 1000],
          [-7000, 1500, 1500],
          [-7000, 2000, 2000]
        ]
      }
    },
    metrics: {}
  };

  // Traces:
  // 1: -7000 + 2000 = -5000
  // 2: -7000 + 3000 = -4000
  // 3: -7000 + 4000 = -3000
  // Median: -4000 -> −€4,000
  const data = sandbox.getScopeCashMetrics(mockResult, 'whole', 'loan');
  assert.ok(data.npvExtra.includes('Undiscounted 2-y net cash: −€4,000'), `Expected negative net cash in "${data.npvExtra}"`);
}

// -------------------------------------------------------------
// Test 4: Solar Coverage / Autarky subtext with kWh demand and generation anchors
// -------------------------------------------------------------
{
  const { sandbox, elements } = createHarness();
  sandbox.summaryScope = 'whole';
  sandbox.summaryCash = 'cash';

  const mockResult = {
    settings: { cashflow_mode: 'nominal' },
    layout: { kwp: 6.0 },
    finance: { capex_eur: 12000, conversion_capex_eur: 0 },
    metrics: {
      autarky: { median: 0.85, min: 0.80, max: 0.90 },
      self_consumption: { median: 0.45, min: 0.40, max: 0.50 }
    },
    energy: {
      load_kwh: { median: 3200, min: 3200, max: 3200 },
      pv_kwh: { median: 6500, min: 6100, max: 6900 }
    }
  };

  sandbox.renderVerdictStrip(mockResult);
  const metricsHtml = elements.get('metrics').innerHTML;
  assert.ok(metricsHtml.includes('SOLAR COVERAGE'), 'Autarky card title');
  assert.ok(metricsHtml.includes('Share of demand (3,200 kWh/yr) met by solar'), `Expected demand anchor in "${metricsHtml}"`);
  assert.ok(metricsHtml.includes('Share of generation (6,100 – 6,900 kWh/yr) used on-site'), `Expected PV generation range anchor in "${metricsHtml}"`);
}

// -------------------------------------------------------------
// Test 5: Behavior preservation for solar-only and cash modes
// -------------------------------------------------------------
{
  const { sandbox } = createHarness();
  const mockResult = {
    settings: { cashflow_mode: 'nominal', inflation: 0.02 },
    finance: {
      capex_eur: 12000,
      conversion_capex_eur: 4000,
      loan_principal_eur: 16000,
      solar_loan_principal_eur: 12000
    },
    metrics: {}
  };

  // Solar scope with loan: outlay shows solar-only cost, no conversion split
  const solarLoan = sandbox.getScopeCashMetrics(mockResult, 'solar', 'loan');
  assert.equal(solarLoan.capexTitle, 'PERSONAL CASH OUTLAY (SOLAR ONLY · AFTER LOAN)');
  assert.equal(solarLoan.capexExtra, 'Total cost: €12,000 · Loan: €12,000');
  assert.equal(solarLoan.npvExtra, 'After proportional solar loan debt service repayments');

  // Whole scope cash: outlay shows investment without loan
  const wholeCash = sandbox.getScopeCashMetrics(mockResult, 'whole', 'cash');
  assert.equal(wholeCash.capexTitle, 'TOTAL INVESTMENT (WHOLE PROJECT)');
  assert.equal(wholeCash.capexExtra, 'Includes €12,000 solar + €4,000 electrification');
  assert.equal(wholeCash.npvExtra, 'Against historic electricity & gas bills');
}

// -------------------------------------------------------------
// Test 6: Behavior preservation when electrification or loan is absent
// -------------------------------------------------------------
{
  const { sandbox } = createHarness();
  // Absent electrification (conversion_capex_eur is 0)
  const noConv = {
    settings: { cashflow_mode: 'nominal' },
    finance: { capex_eur: 12000, conversion_capex_eur: 0, loan_principal_eur: 12000 },
    metrics: {}
  };
  const dataNoConv = sandbox.getScopeCashMetrics(noConv, 'whole', 'loan');
  assert.equal(dataNoConv.capexExtra, 'Total cost: €12,000 · Loan: €12,000');

  // Absent loan (loan_principal_eur is 0)
  const noLoan = {
    settings: { cashflow_mode: 'nominal' },
    finance: { capex_eur: 12000, conversion_capex_eur: 4000, loan_principal_eur: 0 },
    metrics: {}
  };
  const dataNoLoan = sandbox.getScopeCashMetrics(noLoan, 'whole', 'loan');
  assert.equal(dataNoLoan.capexExtra, 'Total cost: €16,000 · Loan: €0');
}

// -------------------------------------------------------------
// Test 7: Behavior preservation for existing preview test fixture (screenshotResult)
// -------------------------------------------------------------
{
  const { sandbox, elements } = createHarness();
  const screenshotResult = {
    settings: {
      cashflow_mode: 'nominal',
      inflation: 0.02,
      installation_year: 2026,
      loan_scope: 'solar_and_conversion'
    },
    layout: { kwp: 6.0, panel_count: 14 },
    finance: {
      capex_eur: 18429,
      conversion_capex_eur: 3403,
      loan_principal_eur: 21832,
      solar_loan_principal_eur: 18429,
      package_equity: {
        npv: { median: 8117, min: 7246, max: 8847 },
        discounted_payback: { median: 9.4, min: 2.9, max: 10.4 }
      },
      cashflows_by_weather: {
        package_equity: [[0, 0]],
        solar_equity: [[0, 0]]
      }
    },
    metrics: {
      npv_eur: { median: 7444, min: 6600, max: 8150 },
      package_npv_eur: { median: 10250, min: 9100, max: 11200 },
      discounted_payback_years: { median: 8.5, min: 7.8, max: 9.3 },
      payback_years: { median: 6.8, min: 6.2, max: 7.4 },
      autarky: { median: 0.861, min: 0.819, max: 0.884 },
      self_consumption: { median: 0.432, min: 0.412, max: 0.458 }
    }
  };

  sandbox.summaryScope = 'whole';
  sandbox.summaryCash = 'loan';
  sandbox.renderVerdictStrip(screenshotResult);

  const metricsHtml = elements.get('metrics').innerHTML;
  assert.ok(metricsHtml.includes('id="metricSummaryCapex"'), 'Capex card present');
  assert.ok(metricsHtml.includes('id="metricSummaryNpv"'), 'NPV card present');
  assert.ok(metricsHtml.includes('id="metricSummaryPayback"'), 'Payback card present');
  assert.ok(metricsHtml.includes('id="metricSummaryAutarky"'), 'Autarky card present');

  // Because energy was absent, autarky subtext falls back cleanly without anchor error
  assert.ok(metricsHtml.includes('Share of demand met by solar'), 'Clean fallback without demand anchor');
  assert.ok(metricsHtml.includes('Share of generation used on-site'), 'Clean fallback without PV anchor');

  // Outlay card shows the split using screenshot values
  assert.ok(metricsHtml.includes('Total CAPEX: €21,832 (€18,429 solar+storage + €3,403 electrification) · Loan: €21,832'));
}


// -------------------------------------------------------------
// Test 8: Summary header chips (DC coupling, loan financing, itemized pricing, electrified)
// -------------------------------------------------------------
{
  const { sandbox, elements, renderSummary } = createHarness();
  const mockResult = {
    settings: {
      load_scenario: 'electrified',
      panel_watts: 480,
      inverter_kw: 6.0,
      pv_max_kwp: 12.0,
      pv_battery_coupling: 'dc_coupled',
      battery_kwh: 10.0,
      installation_year: 2026
    },
    layout: {
      panel_count: 19,
      kwp: 9.12,
      theoretical: true
    },
    finance: {
      loan_principal_eur: 15000,
      capex_eur: 18000
    },
    metrics: {}
  };

  sandbox.isItemizedPricingActive = () => true;
  renderSummary(mockResult);

  const subText = elements.get('systemSubtitle').textContent;
  const tagsHtml = elements.get('systemTags').innerHTML;

  // Subtitle format: two decimal places for kWp and kWh
  assert.equal(subText, '9.12 kWp · 10.00 kWh', 'systemSubtitle formats with two decimal places');

  // Exactly six .summary-setting chips
  const chips = tagsHtml.match(/class=["\']summary-setting["\']/g) || [];
  assert.equal(chips.length, 6, `Expected exactly six .summary-setting chips, found ${chips.length}`);

  // Chip 1: Load scenario
  assert.ok(tagsHtml.includes('Load scenario') && tagsHtml.includes('Electrified'), 'Load scenario chip present');

  // Chip 2: Count × panel Wp (19 panels at 480 Wp)
  assert.ok(tagsHtml.includes('19 × 480 Wp') && tagsHtml.includes('at 480 Wp each'), 'Panel count × Wp chip present');

  // Chip 3: Inverter kW AC distinct from max PV input (6 kW AC vs 12 kW)
  assert.ok(tagsHtml.includes('6.00 kW AC'), 'Inverter kW AC chip present');
  assert.ok(!tagsHtml.includes('12.00 kW AC'), 'Max PV input (12) must not override inverter AC rating');

  // Chip 4: DC coupling
  assert.ok(tagsHtml.includes('DC-coupled'), 'DC-coupled chip present');

  // Chip 5: Cost source itemized
  assert.ok(tagsHtml.includes('Itemized quote'), 'Itemized quote cost source chip present');

  // Chip 6: Financing loan
  assert.ok(tagsHtml.includes('Loan'), 'Financing loan chip present');

  // Assert old tags are absent
  assert.ok(!tagsHtml.includes('Site survey required'), 'Site survey pill absent');
  assert.ok(!tagsHtml.includes('Placement within modeled limits'), 'Placement pill absent');
  assert.ok(!tagsHtml.includes('expenditure'), 'Expenditure year pill absent');
  assert.ok(!tagsHtml.includes('routing-mode-pill'), 'Routing pill absent');
}

// -------------------------------------------------------------
// Test 9: Summary header chips (AC coupling, cash financing, manual total quote, baseline)
// -------------------------------------------------------------
{
  const { sandbox, elements, renderSummary } = createHarness();
  const mockResult = {
    settings: {
      load_scenario: 'baseline',
      panel_watts: 400,
      inverter_kw: 5.0,
      pv_max_kwp: 8.0,
      pv_battery_coupling: 'ac_equivalent',
      battery_kwh: 5.0,
      installation_year: 2026
    },
    layout: {
      panel_count: 14,
      kwp: 5.6,
      theoretical: false
    },
    finance: {
      loan_principal_eur: 0,
      capex_eur: 12000
    },
    metrics: {}
  };

  sandbox.isItemizedPricingActive = () => false;
  sandbox.settings.quote_total = 12000;
  renderSummary(mockResult);

  const subText = elements.get('systemSubtitle').textContent;
  const tagsHtml = elements.get('systemTags').innerHTML;

  assert.equal(subText, '5.60 kWp · 5.00 kWh', 'systemSubtitle formats with two decimal places');

  const chips = tagsHtml.match(/class=["\']summary-setting["\']/g) || [];
  assert.equal(chips.length, 6, `Expected exactly six .summary-setting chips, found ${chips.length}`);

  assert.ok(tagsHtml.includes('Load scenario') && tagsHtml.includes('Baseline'), 'Baseline load scenario chip present');
  assert.ok(tagsHtml.includes('14 × 400 Wp'), 'Panel count × Wp chip present');
  assert.ok(tagsHtml.includes('5.00 kW AC'), 'Inverter kW AC chip present');
  assert.ok(tagsHtml.includes('AC-equivalent') || tagsHtml.includes('AC-coupled'), 'AC coupling chip present');
  assert.ok(tagsHtml.includes('Manual total'), 'Manual total cost source chip present');
  assert.ok(tagsHtml.includes('Cash'), 'Financing cash chip present');

  assert.ok(!tagsHtml.includes('Site survey required'), 'Site survey pill absent');
  assert.ok(!tagsHtml.includes('Placement within modeled limits'), 'Placement pill absent');
  assert.ok(!tagsHtml.includes('expenditure'), 'Expenditure year pill absent');
  assert.ok(!tagsHtml.includes('routing-mode-pill'), 'Routing pill absent');
}

// -------------------------------------------------------------
// Test 10: Summary header cost source fallback to modeled benchmark estimate
// -------------------------------------------------------------
{
  const { sandbox, elements, renderSummary } = createHarness();
  const mockResult = {
    settings: {
      load_scenario: 'electrified',
      panel_watts: 480,
      inverter_kw: 6.0,
      pv_battery_coupling: 'dc_coupled',
      battery_kwh: 10.0
    },
    layout: {
      panel_count: 19,
      kwp: 9.12
    },
    finance: {
      loan_principal_eur: 0,
      capex_eur: 18000
    },
    metrics: {}
  };

  sandbox.isItemizedPricingActive = () => false;
  sandbox.settings.quote_total = null;
  renderSummary(mockResult);

  const tagsHtml = elements.get('systemTags').innerHTML;
  assert.ok(tagsHtml.includes('Modeled estimate'), 'Modeled estimate cost source chip present');
  assert.ok(tagsHtml.includes('Cash'), 'Financing cash chip present');
}

console.log('✔ All summary verdict KPI tests passed successfully.');
