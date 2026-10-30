/* Run with Node: exercise the battery sizing UI renderer, controls, stale invalidation, and persistence. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) {
    const classSet = new Set();
    const listeners = {};
    elements.set(id, {
      id,
      innerHTML: '',
      textContent: '',
      value: '',
      disabled: false,
      hidden: false,
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
      addEventListener: (evt, handler) => {
        listeners[evt] = listeners[evt] || [];
        listeners[evt].push(handler);
      },
      dispatchEvent: evtName => {
        (listeners[evtName] || []).forEach(h => h({ target: elements.get(id) }));
      },
      onclick: null
    });
  }
  return elements.get(id);
};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = (n, d = 0) => Number(n ?? 0).toLocaleString('en-IE', { maximumFractionDigits: d, minimumFractionDigits: d });
const fmt = (n, type = 'number') => n == null ? '—' : type === 'eur' ? '€' + nf(n) : type === 'eur2' ? '€' + nf(n, 2) : type === 'pct' ? nf(n * 100, 1) + '%' : type === 'lcoe' ? '€' + nf(n, 3) : type === 'years' ? nf(n, 1) + ' y' : nf(n);
const dayhourScenarioLabel = s => ({ baseline: 'Historical electricity', electrified: 'Electrified · gas eliminated', ev: 'Electrified + EV' })[s] || s;
const chartTarget = detail => `tabindex="0" role="img" aria-label="${esc(detail)}" data-chart-detail="${esc(detail)}"`;
const chartSvg = (label, body, height = 285, width = 720) => `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}">${body}</svg>`;
const table = (headers, rows) => `<table><thead><tr>${headers.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(x => `<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const title = s => s.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

const storage = new Map();
const mockLocalStorage = {
  getItem: k => storage.has(k) ? storage.get(k) : null,
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: k => storage.delete(k)
};

const start = source.indexOf('/* BATTERY SIZING SECTION */');
assert(start >= 0, '/* BATTERY SIZING SECTION */ marker is present in app.js');
const end = source.indexOf('/* END BATTERY SIZING */', start);
assert(end > start, '/* END BATTERY SIZING */ marker is present in app.js');

const metaStart = source.indexOf('function getWeatherEnsembleMeta(');
assert(metaStart >= 0, 'function getWeatherEnsembleMeta marker is present in app.js');
const metaEnd = source.indexOf('\nfunction ', metaStart);
assert(metaEnd > metaStart, 'end of getWeatherEnsembleMeta marker is present in app.js');

let apiCalls = [];
let apiHandler = async (url, body) => {
  apiCalls.push({ url, body });
  return mockFixture;
};

const context = vm.createContext({
  $, esc, nf, fmt, dayhourScenarioLabel, chartTarget, chartSvg, table, title, console,
  hideChartTooltip: () => {},
  showError: err => { console.error('UI Error:', err); },
  localStorage: mockLocalStorage,
  structuredClone: v => JSON.parse(JSON.stringify(v)),
  deepClone: v => JSON.parse(JSON.stringify(v)),
  settings: { load_scenario: 'electrified', battery_kwh: 5, deduction_rate: 0.5 },
  quote: { line_items: [] },
  normalizeBatterySettings: () => {},
  cleanSettingsForBackend: snapshot => snapshot,
  current: { result: { settings: { load_scenario: 'electrified', battery_kwh: 5, deduction_rate: 0.5 } } },
  api: (url, body) => apiHandler(url, body)
});
vm.runInContext(source.slice(metaStart, metaEnd), context);
assert.equal(typeof context.getWeatherEnsembleMeta, 'function', 'getWeatherEnsembleMeta helper loaded');
vm.runInContext(source.slice(start, end), context);

// Create a representative mock fixture
const mockFixture = {
  status: 'SCENARIO_PENDING',
  settings: { battery_kwh: 5, roundtrip_efficiency: 0.9 },
  options: { step: 1, max_capacity: 25, load_scenario: 'electrified' },
  economics: {
    marginal_battery_cost_eur_kwh: 550,
    effective_marginal_battery_cost_eur_kwh: 550,
    marginal_cost_source: 'benchmark',
    quote_variants_status: 'missing',
    quote_10kwh_eur: null,
    quote_15kwh_eur: null,
    missing_fields: ['quote_10kwh_eur', 'quote_15kwh_eur'],
    battery_lifetime_years: 12,
    deduction_rate: 0.5,
    annualized_marginal_cost_gross: 45.83,
    annualized_marginal_cost_net: 22.92,
    import_price_eur_kwh: 0.206,
    export_price_eur_kwh: 0.08,
    base_spread_eur_kwh: 0.126,
    roundtrip_efficiency: 0.9,
    margin_per_charge_cycle_eur_kwh: 0.1054,
    margin_per_delivered_cycle_eur_kwh: 0.1171,
    margin_per_physical_cycle_eur_kwh: 0.1111,
    breakeven_charge_cycles_gross: 434.8,
    breakeven_charge_cycles_net: 217.4
  },
  source_vs_scenario: [
    { item: 'Benchmark battery cost', value: '550 €/usable kWh', source: 'Research benchmark', status: 'provided' },
    { item: 'Effective marginal cost', value: '550.00 €/kWh', source: 'Benchmark', status: 'calculated' }
  ],
  load_representativeness_note: 'Note on load representativeness: Measured household electricity load is derived from a single historical year.',
  chart1_capacity_dispatch: {
    capacities: Array.from({ length: 26 }, (_, i) => ({
      capacity_kwh: i,
      autarky: { min: 0.4 + i * 0.01, p10: 0.42 + i * 0.01, median: 0.45 + i * 0.01, mean: 0.44 + i * 0.01, p90: 0.48 + i * 0.01, max: 0.5 + i * 0.01 },
      self_consumption: { min: 0.5 + i * 0.015, p10: 0.52 + i * 0.015, median: 0.55 + i * 0.015, mean: 0.54 + i * 0.015, p90: 0.58 + i * 0.015, max: 0.6 + i * 0.015 },
      grid_import_kwh: { min: 2000 - i * 40, p10: 2100 - i * 40, median: 2200 - i * 40, mean: 2210 - i * 40, p90: 2300 - i * 40, max: 2400 - i * 40 },
      battery_discharge_kwh: { min: i * 80, p10: i * 90, median: i * 100, mean: i * 96, p90: i * 110, max: i * 120 },
      annual_savings_eur: { min: i * 20, p10: i * 22, median: i * 25, mean: i * 24, p90: i * 28, max: i * 30 }
    }))
  },
  chart2_incremental_savings: {
    steps: Array.from({ length: 25 }, (_, i) => ({
      capacity_from: i,
      capacity_to: i + 1,
      delta_capacity_kwh: 1,
      incremental_savings_eur_kwh: { min: 35 - i * 1.2, p10: 37 - i * 1.2, median: 40 - i * 1.2, mean: 39 - i * 1.2, p90: 43 - i * 1.2, max: 45 - i * 1.2 }
    })),
    annualized_marginal_cost_gross: 45.83,
    annualized_marginal_cost_net: 22.92,
    optimum_band: {
      weather_min_kwh: 6,
      weather_max_kwh: 8,
      weather_median_kwh: 7,
      gross_optimum_min_kwh: 0,
      gross_optimum_max_kwh: 1,
      sensitivity_min_kwh: 5,
      sensitivity_max_kwh: 10,
      definition: 'Band of capacities where incremental savings exceed annualized cost'
    }
  },
  chart3_efc_breakeven: {
    capacity_efc: Array.from({ length: 25 }, (_, i) => ({
      capacity_kwh: i + 1,
      efc_physical: { min: 300 / (i + 1), p10: 320 / (i + 1), median: 350 / (i + 1), mean: 342 / (i + 1), p90: 380 / (i + 1), max: 400 / (i + 1) },
      efc_delivered: { min: 285 / (i + 1), p10: 305 / (i + 1), median: 332 / (i + 1), mean: 325 / (i + 1), p90: 360 / (i + 1), max: 380 / (i + 1) },
      efc_charge: { min: 316 / (i + 1), p10: 338 / (i + 1), median: 368 / (i + 1), mean: 360 / (i + 1), p90: 400 / (i + 1), max: 421 / (i + 1) }
    })),
    breakeven_charge_cycles_gross: 434.8,
    breakeven_charge_cycles_net: 217.4,
    margin_per_charge_cycle_eur_kwh: 0.1054,
    import_price_eur_kwh: 0.206,
    export_price_eur_kwh: 0.08,
    roundtrip_efficiency: 0.9,
    physical_efc_definition: 'Physical cell throughput EFC',
    delivered_efc_definition: 'Delivered discharge EFC',
    charge_input_efc_definition: 'Charge input EFC',
    cycle_conversion_note: 'Consistent conversion'
  },
  chart4_npv_heatmap: {
    pv_grid_kwp: [3.0, 4.8, 6.0, 7.2, 9.6],
    battery_grid_kwh: [0.0, 2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 25.0],
    cells: [
      { pv_kwp: 3.0, battery_kwh: 0.0, npv_mean: 4100, npv_median: 4000, npv_min: 3500, npv_max: 4500, capex_eur: 5000 },
      { pv_kwp: 6.0, battery_kwh: 5.0, npv_mean: 7600, npv_median: 7500, npv_min: 6800, npv_max: 8200, capex_eur: 11000 }
    ],
    current_configuration: { pv_kwp: 4.8, battery_kwh: 5.0 }
  },
  chart5_needed_capacity_cdf: {
    cdf_points: Array.from({ length: 26 }, (_, i) => ({
      capacity_kwh: i,
      fraction_days_met: Math.min(1, i * 0.08),
      days_met_count: Math.min(7665, Math.round(i * 0.08 * 7665)),
      fraction_days_full_utilization: Math.max(0, 1 - i * 0.08)
    })),
    percentiles: { min: 0, p10: 0.5, p25: 2.2, p50: 5.8, p75: 7.9, p80: 8.7, p90: 9.5, max: 13.8 },
    mean_needed_kwh: 5.4,
    day_count: 7665,
    weather_count: 21,
    weather_basis: 'ensemble_mean',
    representative_weather_year: 2015,
    weather_representative: 'mean',
    heuristic_description: 'Heuristic: For each of 365 calendar days, needed capacity is min(surplus*sqrt(RTE), night_load/sqrt(RTE)).'
  },
  chart6_soc_heatmap: {
    active_capacity_kwh: 5,
    weather_year: null,
    weather_representative: 'mean',
    weather_count: 21,
    weather_basis: 'ensemble_mean',
    empty_fraction: 0.566,
    full_fraction: 0.143,
    mid_fraction: 0.291,
    empty_hours: 4958,
    full_hours: 1253,
    mid_hours: 2549,
    hourly_soc_matrix: Array.from({ length: 365 }, () => Array.from({ length: 24 }, (_, h) => Math.round(h * 4))),
    monthly_summaries: Array.from({ length: 12 }, (_, m) => ({
      month: m + 1,
      mean_soc_pct: 42.0 + m * 0.5,
      empty_fraction: 0.35 + m * 0.01,
      full_fraction: 0.15 + m * 0.005,
      mid_fraction: 0.50 - m * 0.015
    }))
  }
};

context.fixture = mockFixture;

// Test 1: Render functions populate elements
vm.runInContext('renderBatterySizing(fixture)', context);

assert.match($('batEconomicsBanner').innerHTML, /Marginal battery cost/, 'Economics banner rendered');
assert.match($('batSourceVsScenario').innerHTML, /Benchmark battery cost/, 'Source vs scenario table rendered');
assert.match($('batChart1').innerHTML, /<svg/, 'Chart 1 SVG rendered');
assert.match($('batTable1').innerHTML, /<table/, 'Table 1 rendered');
assert.match($('batChart2').innerHTML, /<svg/, 'Chart 2 SVG rendered');
assert.match($('batTable2').innerHTML, /<table/, 'Table 2 rendered');
assert.match($('batChart3').innerHTML, /<svg/, 'Chart 3 SVG rendered');
assert.match($('batTable3').innerHTML, /<table/, 'Table 3 rendered');
assert.match($('batChart4').innerHTML, /<svg/, 'Chart 4 SVG rendered');
assert.match($('batTable4').innerHTML, /<table/, 'Table 4 rendered');
assert.match($('batChart5').innerHTML, /<svg/, 'Chart 5 SVG rendered');
assert.match($('batCdfPercentiles').innerHTML, /P80 needed/, 'CDF P80 percentile card rendered');
assert.match($('batTable5').innerHTML, /<table/, 'Table 5 rendered');
assert.match($('batChart6').innerHTML, /<svg/, 'Chart 6 SVG rendered');
assert.match($('batTable6').innerHTML, /<table/, 'Table 6 rendered');
assert.match($('batTable4').innerHTML, /4,100/, 'NPV heatmap table uses ensemble mean');
assert.match($('batTable6').innerHTML, /42\.5%/, 'February uses its own monthly mean SOC');
assert.match($('batTable6').innerHTML, /Empty state fraction \(%\)/, 'Table 6 header includes Empty state fraction (%)');
assert.match($('batTable6').innerHTML, /35\.0%|35%/, 'Table 6 renders backend empty state fraction (35.0%) differing from matrix threshold (4.2%)');
assert.match($('batTable6').innerHTML, /15\.0%|15%/, 'Table 6 renders backend full state fraction (15.0%)');
assert.match($('batTable6').innerHTML, /50\.0%|50%/, 'Table 6 renders backend mid state fraction (50.0%)');
assert.match($('batTable6').innerHTML, /42\.0%|42%/, 'Table 6 renders backend mean SOC pct (42.0%)');
assert.doesNotMatch($('batTable6').innerHTML, /4\.2%/, 'Table 6 does NOT derive empty state fraction from matrix threshold');
assert.equal($('batSocNotice').hidden, true, 'SOC notice is hidden when storage is active');
assert.match($('batTable1').innerHTML, /44\.0%|44%/, 'Storage dispatch table renders mean autarky');
assert.match($('batTable1').innerHTML, /€24\/yr/, 'Storage dispatch table renders mean annual savings on row 1 (i=1)');
assert.match($('batTable2').innerHTML, /€39\.00\/kWh\/yr/, 'Marginal table renders mean incremental savings');
assert.match($('batTable3').innerHTML, /342\.0 cycles\/yr|342 cycles\/yr/, 'EFC table renders mean physical EFC');
assert.match($('batTable3').innerHTML, /325\.0 cycles\/yr|325 cycles\/yr/, 'EFC table renders mean delivered EFC');
assert.match($('batTable3').innerHTML, /360\.0 cycles\/yr|360 cycles\/yr/, 'EFC table renders mean charge EFC');
assert.match($('batCdfPercentiles').innerHTML, /5\.4\s*<small>kWh<\/small>/, 'CDF mean needed capacity card rendered');
assert.match($('batTable5').innerHTML, /7,665 pooled days \(21 yrs\)/, 'CDF table renders pooled 7665 days');
const socHtml = $('batChart6').innerHTML + $('batTable6').innerHTML + $('batSocFractions').innerHTML;
assert.match(socHtml, /21\s*<small>years<\/small>/, 'SOC heatmap renders 21 years weather ensemble count');
assert.doesNotMatch(socHtml, /Analogue 2015/i, 'SOC heatmap suppresses single analogue year labeling');

// Test 2: Zero storage notice is displayed when active_capacity_kwh === 0
const zeroStorageFixture = {
  ...mockFixture,
  chart6_soc_heatmap: {
    active_capacity_kwh: 0,
    no_storage: true,
    weather_year: 2015,
    weather_representative: 'median',
    empty_fraction: 1.0,
    full_fraction: 0.0,
    mid_fraction: 0.0,
    empty_hours: 8760,
    full_hours: 0,
    mid_hours: 0,
    hourly_soc_matrix: Array.from({ length: 365 }, () => Array.from({ length: 24 }, () => 0))
  }
};
context.zeroFixture = zeroStorageFixture;
vm.runInContext('renderBatterySocHeatmap(zeroFixture)', context);
assert.equal($('batSocNotice').hidden, false, 'SOC notice is unhidden when active_capacity_kwh is 0');
assert.match($('batSocNotice').innerHTML, /No storage configured/, 'SOC notice explains 0 kWh');

// Set initial defaults matching HTML
$('batStep').value = '1';
$('batMaxCapacity').value = '25';
$('batLifetime').value = '12';
$('batDegradation').value = '1.5';
$('batDeduction').value = '0.5';
$('batCostSens').value = '0';
$('batSpreadDelta').value = '0';
$('batScenario').value = 'electrified';
$('batRepresentative').value = 'median';

// Test 3: Initialize controls, verify button handler and local storage
vm.runInContext('initBatterySizingControls()', context);
assert.equal(typeof $('runBatterySizing').onclick, 'function', 'runBatterySizing onclick handler is wired');

// Test 4: Modifying an input marks results stale and updates localStorage
assert.equal($('batResultsWrap').classList.contains('stale'), false, 'Initially not stale');
$('batDegradation').value = '2.0';
$('batDegradation').dispatchEvent('change');
assert.equal($('batResultsWrap').classList.contains('stale'), true, 'batResultsWrap is marked stale on change');
assert.match($('batStatus').textContent, /Inputs changed/, 'batStatus indicates inputs changed');

const stored = JSON.parse(mockLocalStorage.getItem('pv-lab-battery-sizing-v1'));
assert.equal(stored.battery_degradation_pct, 2.0, 'battery_degradation_pct saved to localStorage');

// Test: Fractional input is rejected without calling API
(async () => {
  $('batStep').value = '1.5';
  apiCalls = [];
  await $('runBatterySizing').onclick();
  assert.equal(apiCalls.length, 0, 'Fractional step rejected, api not called');
  assert.match($('batStatus').textContent, /Step must be integer/, 'Error message set on fractional step');
  $('batStep').value = '1'; // restore valid

  // Test 5: Clicking Run button invokes api('/api/battery-sizing') and clears stale state on success
  apiCalls = [];
  await $('runBatterySizing').onclick();
  assert.equal(apiCalls.length, 1, 'api called exactly once');
  assert.equal(apiCalls[0].url, '/api/battery-sizing', 'Endpoint is /api/battery-sizing');
  assert.equal(apiCalls[0].body.options.battery_degradation_annual, 0.02, 'Passed updated degradation option');
  assert.equal($('batResultsWrap').classList.contains('stale'), false, 'batResultsWrap is no longer stale after successful run');

  // Test 6: In-flight race rejection (stale token rejection)
  let delayedResolve;
  apiHandler = () => new Promise(res => { delayedResolve = res; });
  const run1Promise = $('runBatterySizing').onclick();
  assert.equal($('runBatterySizing').disabled, true, 'Button disabled while in flight');

  // User changes input and clicks Run again (or triggers new request)
  $('batDegradation').value = '2.5';
  $('batDegradation').dispatchEvent('change');
  assert.equal($('batResultsWrap').classList.contains('stale'), true, 'Marked stale again');

  let secondResultFixture = {
    ...mockFixture,
    economics: { ...mockFixture.economics, annualized_marginal_cost_net: 99.99 }
  };
  apiHandler = async () => secondResultFixture;
  const run2Promise = $('runBatterySizing').onclick();

  // Now resolve first slow request with stale data
  delayedResolve(mockFixture);
  await run1Promise;
  await run2Promise;

  assert.equal($('batResultsWrap').classList.contains('stale'), false, 'Fresh run 2 cleared stale class');

  // Test 7: Varying marginal cost by step renders stepped curves and step-specific surplus in Table 2
  const varyingFixture = {
    ...mockFixture,
    economics: {
      ...mockFixture.economics,
      reference_candidate_description: 'Benchmark single module (5.0 kWh)'
    },
    chart2_incremental_savings: {
      ...mockFixture.chart2_incremental_savings,
      marginal_cost_varies_by_step: true,
      steps: [
        {
          capacity_from: 0,
          capacity_to: 5,
          delta_capacity_kwh: 5,
          incremental_savings_eur_kwh: { min: 30, p10: 33, median: 38, mean: 37, p90: 40, max: 42 },
          annualized_cost_gross: 41.67,
          annualized_cost_net: 25.63,
          breakeven_charge_cycles_net: 243.2
        },
        {
          capacity_from: 5,
          capacity_to: 10,
          delta_capacity_kwh: 5,
          incremental_savings_eur_kwh: { min: 15, p10: 17, median: 20, mean: 19, p90: 23, max: 25 },
          annualized_cost_gross: 55.00,
          annualized_cost_net: 35.00,
          breakeven_charge_cycles_net: 332.1
        }
      ]
    },
    chart3_efc_breakeven: {
      ...mockFixture.chart3_efc_breakeven,
      reference_candidate_description: 'Benchmark single module (5.0 kWh)',
      capacity_efc: [
        {
          capacity_kwh: 5,
          efc_physical: { min: 200, p10: 220, median: 250, mean: 245, p90: 270, max: 280 },
          efc_delivered: { min: 190, p10: 205, median: 237, mean: 232, p90: 255, max: 265 },
          efc_charge: { min: 210, p10: 230, median: 263, mean: 258, p90: 285, max: 295 },
          breakeven_charge_cycles_net: 243.2,
          breakeven_charge_cycles_gross: 395.3
        }
      ]
    }
  };
  context.varyingFixture = varyingFixture;
  vm.runInContext('renderBatteryIncrementalChart(varyingFixture)', context);
  assert.match($('batChart2').innerHTML, /<path d=.*stroke="#bd8334"/, 'Stepped net marginal cost path rendered');
  assert.match($('batChart2').innerHTML, /Net marginal cost \(varying by step\)/, 'Legend indicates varying cost');
  assert.match($('batTable2').innerHTML, /€25\.63\/kWh\/yr/, 'Step 1 net cost rendered in Table 2');
  assert.match($('batTable2').innerHTML, /€35\.00\/kWh\/yr/, 'Step 2 net cost rendered in Table 2');

  vm.runInContext('renderBatteryEfcChart(varyingFixture)', context);
  assert.match($('batBreakevenFormula').innerHTML, /Benchmark single module \(5\.0 kWh\)/, 'Formula cites reference candidate description');
  assert.match($('batTable3').innerHTML, /243\.2\/yr/, 'Per-capacity net break-even cycles rendered in Table 3');
  assert.match($('batChart3').innerHTML, /Net break-even \(capacity-avg, varying\)/, 'Chart 3 legend marks varying cost');

  // Test 8: Multi-row varying fixture coverage and off-scale handling for EFC chart
  const multiVaryingFixture = {
    ...varyingFixture,
    chart3_efc_breakeven: {
      ...varyingFixture.chart3_efc_breakeven,
      reference_candidate_description: 'Benchmark single module (5.0 kWh)',
      marginal_cost_varies_by_step: true,
      capacity_efc: [
        {
          capacity_kwh: 5,
          efc_physical: { min: 200, median: 250, max: 280 },
          efc_delivered: { min: 190, median: 237, max: 265 },
          efc_charge: { min: 210, median: 263, max: 295 },
          breakeven_charge_cycles_net: 243.2,
          breakeven_charge_cycles_gross: 395.3
        },
        {
          capacity_kwh: 10,
          efc_physical: { min: 120, p10: 130, median: 150, mean: 145, p90: 165, max: 175 },
          efc_delivered: { min: 114, p10: 125, median: 142, mean: 138, p90: 155, max: 166 },
          efc_charge: { min: 126, p10: 138, median: 158, mean: 152, p90: 172, max: 184 },
          breakeven_charge_cycles_net: 287.6,
          breakeven_charge_cycles_gross: 468.2
        }
      ]
    }
  };
  context.multiVaryingFixture = multiVaryingFixture;
  vm.runInContext('renderBatteryEfcChart(multiVaryingFixture)', context);
  const chart3Html = $('batChart3').innerHTML;
  const table3Html = $('batTable3').innerHTML;
  assert.match(chart3Html, /Net break-even \(capacity-avg, varying\)/, 'Legend clearly notes capacity-average varying threshold');
  assert.match(chart3Html, /<polyline points=.*stroke="#bd8334"/, 'Varying net break-even polyline rendered instead of constant scalar line');
  assert.match(table3Html, /Net break-even \(capacity-avg\)/, 'Table 3 header identifies capacity-average threshold');
  assert.match(table3Html, /287\.6\/yr/, 'Step 2 per-capacity net break-even rendered');

  // Verify off-scale threshold display
  const offScaleFixture = {
    ...multiVaryingFixture,
    chart3_efc_breakeven: {
      ...multiVaryingFixture.chart3_efc_breakeven,
      capacity_efc: [
        {
          capacity_kwh: 5,
          efc_physical: { min: 200, median: 250, max: 280 },
          efc_delivered: { min: 190, median: 237, max: 265 },
          efc_charge: { min: 210, median: 263, max: 295 },
          breakeven_charge_cycles_net: 5500.0,
          breakeven_charge_cycles_gross: 8500.0
        }
      ]
    }
  };
  context.offScaleFixture = offScaleFixture;
  vm.runInContext('renderBatteryEfcChart(offScaleFixture)', context);
  assert.match($('batChart3').innerHTML, /exceed visible range/, 'Honest warning shown when threshold is off scale');

  // Verify legacy uniform cost fixture retains constant scalar line
  vm.runInContext('renderBatteryEfcChart(fixture)', context);
  assert.match($('batChart3').innerHTML, /Net break-even: 217\.4\/yr/, 'Legacy fixture renders scalar threshold line');
  assert.match($('batTable3').innerHTML, /Net break-even cycles/, 'Legacy fixture retains uniform header');

  // Test 9: Missing-mean test: median never substitutes primary values and no NaN in charts
  const missingMeanFixture = {
    ...mockFixture,
    chart1_capacity_dispatch: {
      capacities: [
        {
          capacity_kwh: 5,
          autarky: { min: 0.4, median: 0.45, max: 0.5 },
          self_consumption: { min: 0.5, median: 0.55, max: 0.6 },
          grid_import_kwh: { min: 1800, median: 2000, max: 2200 },
          battery_discharge_kwh: { min: 400, median: 500, max: 600 },
          annual_savings_eur: { min: 100, median: 125, max: 150 }
        }
      ]
    },
    chart2_incremental_savings: {
      ...mockFixture.chart2_incremental_savings,
      steps: [
        {
          capacity_from: 0,
          capacity_to: 1,
          delta_capacity_kwh: 1,
          incremental_savings_eur_kwh: { min: 30, median: 40, max: 50 }
        }
      ]
    },
    chart3_efc_breakeven: {
      ...mockFixture.chart3_efc_breakeven,
      capacity_efc: [
        {
          capacity_kwh: 5,
          efc_physical: { min: 200, median: 250, max: 300 },
          efc_delivered: { min: 180, median: 230, max: 280 },
          efc_charge: { min: 210, median: 260, max: 310 }
        }
      ]
    },
    chart5_needed_capacity_cdf: {
      ...mockFixture.chart5_needed_capacity_cdf,
      mean_needed_kwh: null
    },
    chart6_soc_heatmap: {
      ...mockFixture.chart6_soc_heatmap,
      monthly_summaries: null
    }
  };
  context.missingMeanFixture = missingMeanFixture;
  vm.runInContext('renderBatteryDispatchChart(missingMeanFixture)', context);
  vm.runInContext('renderBatteryIncrementalChart(missingMeanFixture)', context);
  vm.runInContext('renderBatteryEfcChart(missingMeanFixture)', context);
  vm.runInContext('renderBatteryCdfChart(missingMeanFixture)', context);
  vm.runInContext('renderBatterySocHeatmap(missingMeanFixture)', context);

  assert.doesNotMatch($('batChart1').innerHTML, /NaN/, 'Chart 1 contains no NaN when mean is missing');
  assert.doesNotMatch($('batChart2').innerHTML, /NaN/, 'Chart 2 contains no NaN when mean is missing');
  assert.doesNotMatch($('batChart3').innerHTML, /NaN/, 'Chart 3 contains no NaN when mean is missing');
  assert.doesNotMatch($('batChart5').innerHTML, /NaN/, 'Chart 5 contains no NaN when mean is missing');
  assert.doesNotMatch($('batChart6').innerHTML, /NaN/, 'Chart 6 contains no NaN when mean is missing');

  assert.doesNotMatch($('batTable1').innerHTML, /45\.0%|45%/, 'Table 1 does NOT substitute median 45% when mean autarky is missing');
  assert.doesNotMatch($('batTable2').innerHTML, /€40\.00|€40/, 'Table 2 does NOT substitute median 40 when mean incremental savings is missing');
  assert.doesNotMatch($('batTable3').innerHTML, /250/, 'Table 3 does NOT substitute median 250 when mean physical EFC is missing');
  assert.match($('batTable6').innerHTML, /—/, 'Table 6 renders — for state fractions when monthly_summaries is missing');
  assert.doesNotMatch($('batCdfPercentiles').innerHTML, /5\.8\s*<small>kWh<\/small>/, 'CDF card does NOT substitute median P50 (5.8 kWh) when mean needed capacity is missing');

  console.log('Battery sizing UI renderer & interactive tests passed successfully.');
})().catch(err => {
  console.error(err);
  process.exit(1);
});

