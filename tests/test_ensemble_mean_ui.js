'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.resolve(__dirname, '../feasibility/web/app.js'), 'utf8');

function extractFn(name) {
  const idx = appSource.indexOf(`function ${name}(`);
  if (idx === -1) throw new Error(`Missing ${name} in app.js`);
  let depth = 0, start = appSource.indexOf('{', idx);
  for (let i = start; i < appSource.length; i++) {
    if (appSource[i] === '{') depth++;
    else if (appSource[i] === '}' && --depth === 0) return appSource.slice(idx, i + 1);
  }
  throw new Error(`Unterminated ${name}`);
}

const fns = ['computeEnvelope', 'loanDebtTimingAdjustments', 'getDiscountingCashflows', 'buildSummaryCashRows', 'getScopeCashMetrics', 'unleveredPaybackCard', 'renderVerdictStrip', 'financedPaybackCard', 'personalPaybackCard'];
const ctx = vm.createContext({
  console, Math, Number, Array, Object, String, isNaN, isFinite,
  summaryScope: 'solar', summaryCash: 'cash', settings: {},
  $: id => {
    if (!ctx.__elements) ctx.__elements = {};
    if (!ctx.__elements[id]) ctx.__elements[id] = { innerHTML: '', style: {}, classList: { toggle: () => {} }, setAttribute: () => {} };
    return ctx.__elements[id];
  },
  nf: n => (typeof n === 'number' ? n.toLocaleString('en-US') : String(n)),
  fmt: (n, type) => (type === 'years' ? `${n} y` : type === 'pct' ? `${(n * 100).toFixed(1)}%` : (typeof n === 'number' ? n.toLocaleString('en-US') : String(n))),
  range: (r, type) => (r && typeof r === 'object' && r.min != null && r.max != null ? `${ctx.fmt(r.min, type)}–${ctx.fmt(r.max, type)}` : String(r)),
  isItemizedPricingActive: () => false,
  metricCard: (title, pb, type, detail) => ({ title, pb, type, detail })
});
vm.runInContext(fns.map(extractFn).join('\n'), ctx);

function makeFixture(cashflowMode = 'real') {
  return {
    settings: { cashflow_mode: cashflowMode, inflation: 0.02, real_discount_rate: 0.03, horizon_years: 3 },
    energy: { ensemble_autarky: 0.72, autarky: 0.68 },
    metrics: { npv_eur: { min: 5000, median: 7000, max: 12000, mean: 8000 }, autarky: 0.68 },
    finance: {
      capex_eur: 6000, loan_principal_eur: 0, real_discount_rate: 0.03, nominal_discount_rate: 0.0506,
      cashflows_by_weather: {
        pv_incremental: [[-6000, 500, 600, 700], [-6000, 300, 350, 400], [-6000, 1000, 1200, 1400]],
        equity: [[-6000, 500, 600, 700], [-6000, 300, 350, 400], [-6000, 1000, 1200, 1400]]
      },
      pv_incremental: {
        discounted_payback: { median: 3.2, recovered_weather_years: 3, weather_count: 3 },
        discounted_payback_of_mean_cash_flows: 2.7
      }
    }
  };
}

test('1. Chart helper buildSummaryCashRows computes mean annual and cumulative rows including t0', () => {
  const r = makeFixture('nominal');
  const displayCash = [
    { year: 0, pv_incremental: -6000, equity: -6000 },
    { year: 1, pv_incremental: 600, equity: 600 },
    { year: 2, pv_incremental: 700, equity: 700 },
    { year: 3, pv_incremental: 800, equity: 800 }
  ];
  const data = ctx.buildSummaryCashRows(r, displayCash);
  assert.equal(data.rows[0].year, 0);
  assert.equal(data.rows[0].project, -6000);
  const y1Project = data.rows[1].project;
  assert.equal(y1Project.median, 500, 'Median preserved on annual cash row');
  assert.equal(y1Project.mean, 600, 'Annual project cash envelope must compute mean');
  assert(Math.abs(data.netCashTotals.project.mean - data.rows.slice(1).reduce((s, row) => s + row.project.mean, data.rows[0].project)) < 1e-9);
});

test('2. getScopeCashMetrics exposes payback_of_mean_cash_flows distinct from median and preserves null', () => {
  const r = makeFixture('real');
  const m = ctx.getScopeCashMetrics(r, 'solar', 'cash');
  assert(m && m.pbObj, 'Payback metric object present');
  assert.equal(m.pbObj.median, 3.2, 'Preserves median weather payback');
  assert.equal(m.pbObj.discounted_payback_of_mean_cash_flows, 2.7, 'Payback of mean path differs from median');
  assert.notEqual(m.pbObj.discounted_payback_of_mean_cash_flows, m.pbObj.median);

  r.finance.pv_incremental.discounted_payback_of_mean_cash_flows = null;
  const unrec = ctx.getScopeCashMetrics(r, 'solar', 'cash');
  assert.strictEqual(unrec.pbObj.discounted_payback_of_mean_cash_flows, null, 'Unrecovered mean payback stays null even with a finite weather median');
});

test('3. getDiscountingCashflows strictly routes cashflows_for_discounting_by_weather over nominal', () => {
  const r = makeFixture('nominal');
  r.finance.cashflows_for_discounting_by_weather = {
    pv_incremental: r.finance.cashflows_by_weather.pv_incremental,
    equity: [[-6000, 480, 580, 680], [-6000, 280, 330, 380], [-6000, 980, 1180, 1380]]
  };
  const disc = ctx.getDiscountingCashflows(r);
  assert.deepEqual(disc.equity, r.finance.cashflows_for_discounting_by_weather.equity, 'Must route backend discounting equity paths');
  assert.deepEqual(disc.pv_incremental, r.finance.cashflows_by_weather.pv_incremental, 'Preserves nominal operating paths');
  assert.notDeepEqual(disc.equity, r.finance.cashflows_by_weather.equity, 'Discounting equity diverges from nominal');
});

test('4. getScopeCashMetrics Year1 net uses explicit mean and arithmetic mean with real conversion, not median', () => {
  // 4a. Explicit display mean on annual_cashflows
  const r1 = makeFixture('real');
  r1.annual_cashflows = [
    {},
    {
      display_pv_incremental_mean: 450,
      pv_incremental_range: { min: 200, median: 300, max: 800 }
    }
  ];
  const m1 = ctx.getScopeCashMetrics(r1, 'solar', 'cash');
  assert.equal(m1.y1Net, 450, 'Uses explicit display_<scope>_mean over range median');

  // 4b. Explicit nominal mean converted to real via deflator
  const r2 = makeFixture('real');
  r2.settings.inflation = 0.05;
  r2.annual_cashflows = [
    {},
    {
      pv_incremental_mean: 420,
      pv_incremental_range: { min: 200, median: 300, max: 800 }
    }
  ];
  const m2 = ctx.getScopeCashMetrics(r2, 'solar', 'cash');
  assert.equal(m2.y1Net, 420 / 1.05, 'Nominal mean converted to real via deflator');

  // 4c. Arithmetic mean fallback from cashflows_by_weather traces
  const r3 = makeFixture('real');
  r3.settings.inflation = 0.02;
  r3.annual_cashflows = [{}, {}];
  r3.finance.cashflows_by_weather = {
    pv_incremental: [
      [-6000, 200],
      [-6000, 200],
      [-6000, 800]
    ]
  };
  const m3 = ctx.getScopeCashMetrics(r3, 'solar', 'cash');
  // median is 200, arithmetic mean is (200 + 200 + 800) / 3 = 400. Real deflator = 1.02
  assert.ok(Math.abs(m3.y1Net - 400 / 1.02) < 1e-10, 'Computes arithmetic mean with real conversion, not median');
});

test('5. unleveredPaybackCard uses discounted_payback_of_mean_cash_flows preserving envelope diagnostics and explicit null', () => {
  const r = makeFixture('real');
  r.finance.loan_principal_eur = 4000;
  r.finance.pv_incremental.discounted_payback_of_mean_cash_flows = 2.5;
  r.finance.pv_incremental.discounted_payback = { min: 2.0, median: 3.5, max: 5.0 };
  r.finance.solar_equity = {
    discounted_payback_of_mean_cash_flows: 2.0,
    discounted_payback: { min: 1.5, median: 4.0, max: 4.5 }
  };

  // Mean paths accelerate by 0.5 y; the weather medians instead imply a 0.5 y delay.
  const card1 = ctx.unleveredPaybackCard(r, false);
  assert(card1 && card1.detail, 'Card detail generated');
  assert.match(card1.detail, /Loan accelerates discounted cash recovery by 0\.5 y vs 100% cash upfront \(2\.5 y\)/);
  assert.equal(card1.pb.median, 3.5, 'Retains perweather envelope diagnostics on pbObj');
  assert.equal(card1.pb.discounted_payback_of_mean_cash_flows, 2.5);

  // 5b. Explicit null in unlevered payback of mean cash flows is preserved (not falling back to finite median)
  r.finance.pv_incremental.discounted_payback_of_mean_cash_flows = null;
  const card2 = ctx.unleveredPaybackCard(r, false);
  assert.match(card2.detail, /100% cash recovery not reached within horizon; loan achieves recovery in 2 y\./);

  // 5c. Explicit null in levered payback of mean cash flows is preserved
  r.finance.pv_incremental.discounted_payback_of_mean_cash_flows = 2.5;
  r.finance.solar_equity.discounted_payback_of_mean_cash_flows = null;
  const card3 = ctx.unleveredPaybackCard(r, false);
  assert.match(card3.detail, /Personal recovery with loan not reached within horizon; 100% cash recovers in 2\.5 y\./);

  // 5d. Both null
  r.finance.pv_incremental.discounted_payback_of_mean_cash_flows = null;
  r.finance.solar_equity.discounted_payback_of_mean_cash_flows = null;
  const card4 = ctx.unleveredPaybackCard(r, false);
  assert.match(card4.detail, /Neither loan nor cash option reaches persistent recovery within horizon\./);
});

test('6. getScopeCashMetrics and renderVerdictStrip prefer headline_energy_flows over native lifecycle diagnostics with labeled P10–P90 bounds while raw energy remains intact', () => {
  const r = makeFixture('real');
  // Native lifecycle diagnostics on native weather calendars
  r.energy = {
    load_kwh: { mean: 3000, median: 2950, min: 2800, max: 3200 },
    pv_kwh: { mean: 5000, median: 4900, min: 4600, max: 5400 },
    autarky: { min: 0.50, median: 0.60, max: 0.70, mean: 0.62 },
    ensemble_autarky: 0.62,
    self_consumption: { min: 0.35, median: 0.40, max: 0.45, mean: 0.41 }
  };
  r.metrics = {
    load_kwh: 3000,
    pv_kwh: 5000,
    autarky: r.energy.autarky,
    self_consumption: r.energy.self_consumption
  };

  // Explicit aggregate flows with asymmetric ratio-of-means and P10-P90 bounds
  r.headline_energy_flows = {
    load_demand_kwh: 4200,
    pv_generation_kwh: 7500,
    grid_import_kwh: 756,
    autarky: 0.82,
    flow_ranges: {
      autarky: { p10: 0.74, p90: 0.88 }
    },
    weather_count: 20,
    forecast_year: 1
  };

  const metrics = ctx.getScopeCashMetrics(r, 'solar', 'cash');
  assert.ok(metrics, 'Scope metrics computed');

  // Verify aggregate overrides different native lifecycle means/ratios
  assert.equal(metrics.demandKwh, 4200, 'Headline load_demand_kwh overrides native lifecycle load_kwh mean');
  assert.equal(metrics.pvKwh, 7500, 'Headline pv_generation_kwh overrides native lifecycle pv_kwh mean');
  assert.equal(metrics.autarky.ensembleAutarky, 0.82, 'Headline ratio-of-means autarky overrides native ensembleAutarky');
  assert.equal(metrics.autarky.headline, 0.82, 'Headline autarky overrides native mean');
  assert.equal(metrics.autarky.p10, 0.74, 'Uses flows.flow_ranges.autarky.p10');
  assert.equal(metrics.autarky.p90, 0.88, 'Uses flows.flow_ranges.autarky.p90');
  assert.equal(metrics.autarky.bounds_label, 'P10–P90', 'Explicit P10–P90 bounds label on autarky metric');

  // Verify native raw energy and medians remain intact (not overwritten)
  assert.equal(r.energy.load_kwh.mean, 3000, 'Raw energy load_kwh mean remains intact');
  assert.equal(r.energy.load_kwh.median, 2950, 'Raw energy load_kwh median remains intact');
  assert.equal(r.energy.pv_kwh.mean, 5000, 'Raw energy pv_kwh mean remains intact');
  assert.equal(r.energy.pv_kwh.median, 4900, 'Raw energy pv_kwh median remains intact');
  assert.equal(r.energy.autarky.median, 0.60, 'Raw energy autarky median remains intact');
  assert.equal(r.energy.autarky.min, 0.50, 'Raw energy autarky min remains intact');
  assert.equal(r.energy.autarky.max, 0.70, 'Raw energy autarky max remains intact');
  assert.equal(r.energy.ensemble_autarky, 0.62, 'Raw energy ensemble_autarky remains intact');
  assert.equal(metrics.autarky.median, 0.60, 'Autarky metric preserves raw weather median without overwrite');

  // Test renderVerdictStrip with headline_energy_flows
  ctx.renderVerdictStrip(r);
  const renderedHtml = ctx.__elements.metrics.innerHTML;
  assert.ok(renderedHtml.includes('SOLAR COVERAGE'), 'Includes SOLAR COVERAGE card');
  assert.ok(renderedHtml.includes('Autarky 82.0%'), 'Renders headline ratio-of-means autarky value');
  assert.ok(renderedHtml.includes('(P10–P90: 74.0%–88.0%)'), 'Includes explicit P10–P90 bounds label in custom widget');
  assert.ok(renderedHtml.includes('Share of demand (4,200 kWh/yr) met by solar'), 'Demand anchor uses headline aggregate demand');
  assert.ok(renderedHtml.includes('Share of generation (7,500 kWh/yr) used on-site'), 'PV anchor uses headline aggregate generation');

  // Test fallback when headline_energy_flows is absent
  delete r.headline_energy_flows;
  const fallbackMetrics = ctx.getScopeCashMetrics(r, 'solar', 'cash');
  assert.equal(fallbackMetrics.demandKwh, 3000, 'Fallback restores native load_kwh');
  assert.equal(fallbackMetrics.pvKwh, 5000, 'Fallback restores native pv_kwh');
  assert.equal(fallbackMetrics.autarky.headline, 0.62, 'Fallback restores native ensemble autarky');
  assert.equal(fallbackMetrics.autarky.p10, undefined, 'Fallback has no p10');
  assert.equal(fallbackMetrics.autarky.bounds_label, 'min–max', 'Fallback preserves honest min–max bounds label');

  ctx.renderVerdictStrip(r);
  const fallbackHtml = ctx.__elements.metrics.innerHTML;
  assert.ok(fallbackHtml.includes('Autarky 62.0%'), 'Fallback renders native autarky');
  assert.ok(fallbackHtml.includes('(50.0%–70.0%)'), 'Fallback preserves honest min–max bounds without P10–P90 label');
  assert.ok(!fallbackHtml.includes('P10–P90:'), 'Fallback does not contain P10–P90 label');
});


test('8. personalPaybackCard and financedPaybackCard use discounted_payback_of_mean_cash_flows preserving envelope diagnostics and explicit null', () => {
  const r = makeFixture('real');
  r.finance.loan_principal_eur = 5000;
  r.finance.cashflows_by_weather = {
    equity: [[-1000, 500, 500]],
    financed_project: [[-6000, 700, 700]]
  };
  r.finance.equity = {
    discounted_payback_of_mean_cash_flows: 2.7,
    discounted_payback: { min: 2.0, median: 2.6, max: 4.0, recovered_weather_years: 3 }
  };
  r.finance.financed_project = {
    discounted_payback_of_mean_cash_flows: 3.1,
    discounted_payback: { min: 2.5, median: 3.0, max: 5.0, recovered_weather_years: 3 },
    unpaid_discounted_weather_years: 0
  };

  // 8a. Asymmetric meanPB != medianPB is used for payback card rendering while diagnostics remain intact
  const personalCard1 = ctx.personalPaybackCard(r);
  assert.equal(personalCard1.pb.discounted_payback_of_mean_cash_flows, 2.7);
  assert.equal(personalCard1.pb.median, 2.6, 'Preserves median on personal pb object');
  assert.equal(personalCard1.pb.recovered_weather_years, 3, 'Preserves censor counts diagnostics');
  assert.notEqual(personalCard1.pb.discounted_payback_of_mean_cash_flows, personalCard1.pb.median);

  const financedCard1 = ctx.financedPaybackCard(r);
  assert.equal(financedCard1.pb.discounted_payback_of_mean_cash_flows, 3.1);
  assert.equal(financedCard1.pb.median, 3.0, 'Preserves median on financed pb object');
  assert.equal(financedCard1.pb.recovered_weather_years, 3);
  assert.notEqual(financedCard1.pb.discounted_payback_of_mean_cash_flows, financedCard1.pb.median);

  // 8b. Explicit null with finite median preserves null (not falling back to median)
  r.finance.equity.discounted_payback_of_mean_cash_flows = null;
  r.finance.financed_project.discounted_payback_of_mean_cash_flows = null;
  const personalCard2 = ctx.personalPaybackCard(r);
  assert.strictEqual(personalCard2.pb.discounted_payback_of_mean_cash_flows, null, 'Explicit null preserved for personal card');
  assert.equal(personalCard2.pb.median, 2.6, 'Finite median retained for diagnostics');

  const financedCard2 = ctx.financedPaybackCard(r);
  assert.strictEqual(financedCard2.pb.discounted_payback_of_mean_cash_flows, null, 'Explicit null preserved for financed card');
  assert.equal(financedCard2.pb.median, 3.0, 'Finite median retained for diagnostics');
});
