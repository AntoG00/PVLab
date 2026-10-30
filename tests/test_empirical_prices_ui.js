/* Run with Node: test UI rendering of empirical CSUD price curve, export valuation, and solar/battery sizing. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

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
      options: [],
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
      onclick: null,
      onchange: null
    });
  }
  return elements.get(id);
};

const domNodes = [];
const mockDocument = {
  getElementById: $,
  querySelector: () => null,
  addEventListener: () => {},
  querySelectorAll: selector => {
    if (selector === '[data-control]') return domNodes;
    return [];
  }
};

const mockFetch = async (url) => {
  if (url.includes('/api/empirical-prices')) {
    return {
      ok: true,
      json: async () => ({
        window: 'oct2025_sep2026',
        window_meta: {
          label: 'October 2025 - September 2026',
          dates_coverage: '2025-10-01 to 2026-09-30 (365 civil days)',
          total_hours: 8760,
          mean_csud_eur_mwh: 136.588089494292,
          mean_csud_eur_kwh: 0.136588089494292,
          zero_negative_count: 69
        },
        matrix_eur_kwh: Array.from({ length: 12 }, () => Array.from({ length: 24 }, (_, h) => (h >= 11 && h <= 14 ? 0.090 : 0.150)))
      })
    };
  }
  return { ok: false, status: 404, json: async () => ({ error: 'Not found' }) };
};

const sandbox = {
  document: mockDocument,
  window: {
    location: { reload: () => {} },
    addEventListener: () => {},
    localStorage: {
      getItem: () => null,
      setItem: () => {}
    }
  },
  console,
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  fetch: mockFetch,
  localStorage: {
    getItem: () => null,
    setItem: () => {}
  }
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const setCurrent = (res) => {
  sandbox._testCurrent = res;
  vm.runInContext(`
    current = _testCurrent;
    if (current && current.result && current.result.settings) {
      settings = deepClone(current.result.settings);
      quote = { line_items: [] };
      const sClone = deepClone(settings);
      delete sClone.hasItemized;
      if (typeof cleanSettingsForBackend === 'function') cleanSettingsForBackend(sClone);
      acceptedInputFingerprint = inputFingerprint(sClone, deepClone(quote));
    }
  `, sandbox);
};


test('renderExportValuation renders cards and comparison table cleanly with active empirical curve', () => {
  const mockResult = {
    export_price_analysis: {
      profile_source: 'empirical_csud_oct2025_sep2026_proxy',
      profile_label: 'Empirical CSUD shape',
      profile_type: 'empirical',
      window: 'oct2025_sep2026',
      baseload_price_eur_kwh: 0.128,
      legacy_capture_factor: 0.74,
      legacy_capture_factor_active: false,
      export_weighted_capture_factor: 0.815,
      export_weighted_captured_price_eur_kwh: 0.10432,
      flat_benchmark_comparison: {
        flat_captured_price_eur_kwh: 0.09472,
        flat_year1_pzo_gross_eur: 420.50,
        curve_year1_pzo_gross_eur: 463.15,
        delta_year1_pzo_gross_eur: 42.65,
        note: 'Comparison holds physical solar production and self-consumption dispatch identical.'
      }
    }
  };

  sandbox.renderExportValuation(mockResult);

  const cardsHtml = $('exportValuationCards').innerHTML;
  assert.match(cardsHtml, /Wholesale profile/i);
  assert.match(cardsHtml, /Empirical CSUD/i);
  assert.match(cardsHtml, /Oct 2025 – Sep 2026/i);
  assert.match(cardsHtml, /0\.10/); // Captured export price
  assert.match(cardsHtml, /81\.5%/); // Capture factor
  assert.match(cardsHtml, /Empirical outcome \(74\.0% inactive\)/i);

  const compHtml = $('exportSensitivityComparison').innerHTML;
  assert.match(compHtml, /Empirical CSUD shape/i);
  assert.match(compHtml, /Flat solar capture benchmark/i);
  assert.match(compHtml, /74\.0%/);
  assert.match(compHtml, /\+€43/); // delta rounded or formatted

  const noteText = $('exportValuationNote').textContent;
  assert.match(noteText, /Empirical CSUD curve active/i);
  assert.match(noteText, /legacy capture factor \(74\.0%\) is inactive/i);
});

test('renderExportValuation handles flat benchmark active with dynamic capture factor', () => {
  const mockFlatResult = {
    export_price_analysis: {
      profile_source: 'flat_solar_capture_benchmark',
      profile_label: 'Flat solar capture benchmark',
      profile_type: 'flat',
      window: 'oct2025_sep2026',
      baseload_price_eur_kwh: 0.128,
      legacy_capture_factor: 0.80, // Dynamic user value
      legacy_capture_factor_active: true,
      export_weighted_capture_factor: 0.80,
      export_weighted_captured_price_eur_kwh: 0.1024,
      flat_benchmark_comparison: {
        flat_captured_price_eur_kwh: 0.1024,
        flat_year1_pzo_gross_eur: 450.00,
        curve_year1_pzo_gross_eur: null, // Empirical not calculated!
        delta_year1_pzo_gross_eur: null,
        note: 'Flat benchmark active: a constant capture factor (0.80) applies directly to the baseload forecast. Empirical curve comparison is not calculated while flat benchmark is active.'
      }
    }
  };

  sandbox.renderExportValuation(mockFlatResult);

  const cardsHtml = $('exportValuationCards').innerHTML;
  assert.match(cardsHtml, /Fixed 80\.0% multiplier active/i);
  assert.match(cardsHtml, /450/); // Shows active flat gross, not curve

  const compHtml = $('exportSensitivityComparison').innerHTML;
  assert.match(compHtml, /Not calculated/); // Empirical row shows Not calculated!
  assert.match(compHtml, /fixed 80\.0%/);

  const noteText = $('exportValuationNote').textContent;
  assert.match(noteText, /Flat solar capture benchmark active/i);
  assert.match(noteText, /80\.0%/);
});

test('renderExportValuation handles zero exports safely without NaN or errors', () => {
  const mockZeroResult = {
    export_price_analysis: {
      profile_source: 'empirical_csud_oct2025_sep2026_proxy',
      profile_type: 'empirical',
      window: 'oct2025_sep2026',
      baseload_price_eur_kwh: 0.128,
      legacy_capture_factor: 0.74,
      legacy_capture_factor_active: false,
      export_weighted_capture_factor: null,
      export_weighted_captured_price_eur_kwh: null,
      flat_benchmark_comparison: {
        flat_captured_price_eur_kwh: 0.09472,
        flat_year1_pzo_gross_eur: 0.0,
        curve_year1_pzo_gross_eur: 0.0,
        delta_year1_pzo_gross_eur: 0.0
      }
    }
  };

  sandbox.renderExportValuation(mockZeroResult);

  const cardsHtml = $('exportValuationCards').innerHTML;
  assert.doesNotMatch(cardsHtml, /NaN/i);
  assert.match(cardsHtml, /No exports/i);
  assert.match(cardsHtml, /Unavailable/i);
});

test('renderMarketCurve fetches empirical prices, renders day-weighted SVG and clear time label', async () => {
  await sandbox.renderMarketCurve('oct2025_sep2026');

  const provText = $('marketCurveProvenance').textContent;
  assert.match(provText, /October 2025 - September 2026/);
  assert.match(provText, /365 civil days/);

  const chartHtml = $('marketSeasonalChart').innerHTML;
  assert.match(chartHtml, /<svg/);
  assert.match(chartHtml, /polyline/);
  assert.match(chartHtml, /Summer \(Jun–Aug\)/);
  assert.match(chartHtml, /Fixed UTC\+1 standard time; in summer add one hour to read Italy's civil clock/);

  const tableHtml = $('marketCurveTable').innerHTML;
  assert.match(tableHtml, /January/);
  assert.match(tableHtml, /December/);
  assert.match(tableHtml, /Annual average/i);
});

test('renderSolarSizing displays captured export price, ceiling pill, and bounds note', () => {
  const mockData = {
    recommended_candidate: {
      panel_count: 14,
      kwp: 6.16,
      battery_kwh: 5.0,
      battery_modules: 1,
      capex_eur: 7500,
      npv_median: 8200,
      npv_min: 7100,
      npv_max: 9300,
      autarky_median: 0.65,
      self_consumption_median: 0.45,
      annual_pv_kwh_median: 7800,
      annual_grid_import_kwh_median: 1800,
      reached_inverter_ceiling: true,
      reached_roof_bound: false,
      bounds_note: 'The recommended system reaches the conservative inverter sizing ceiling (14 panels, 6.16 kWp <= 6.00 kW inverter).',
      roof_counts: { south: 14, west: 0, east: 0, dormer: 0, flat: 0, secondary_south: 0 },
      detail: {
        export_price_analysis: {
          export_weighted_captured_price_eur_kwh: 0.104,
          export_weighted_capture_factor: 0.812
        }
      }
    },
    search_summary: {
      evaluated_count: 50,
      weather_count: 19,
      exact: true,
      objective: 'npv',
      objective_label: 'NPV',
      inverter_kw: 6.0
    }
  };

  sandbox.renderSolarSizing(mockData);

  const bannerHtml = $('solarRecommendedBanner').innerHTML;
  assert.match(bannerHtml, /At PV array limit/);

  const cardsHtml = $('solarRecCards').innerHTML;
  assert.match(cardsHtml, /Captured Export Price/);
  assert.match(cardsHtml, /0\.10/);

  const noteText = $('solarPricingNote').textContent;
  assert.match(noteText, /inverter sizing ceiling/);
});

test('renderBatterySizing displays wholesale profile pill in banner and honest heuristic label in Chart 2', () => {
  const mockBatData = {
    chart1_capacity_dispatch: {
      capacities: [0, 5, 10].map(i => ({
        capacity_kwh: i,
        autarky: { min: 0.4, median: 0.5, max: 0.6 },
        self_consumption: { min: 0.5, median: 0.6, max: 0.7 },
        grid_import_kwh: { min: 2000, median: 2200, max: 2400 },
        battery_discharge_kwh: { min: 500, median: 600, max: 700 },
        annual_savings_eur: { min: 100, median: 150, max: 200 }
      }))
    },
    chart2_incremental_savings: {
      steps: [{ capacity_from: 0, capacity_to: 5, delta_capacity_kwh: 5, incremental_savings_eur_kwh: { min: 20, median: 25, max: 30 } }],
      annualized_marginal_cost_gross: 45.83,
      annualized_marginal_cost_net: 28.18,
      optimum_band: {
        weather_min_kwh: 5,
        weather_max_kwh: 10,
        weather_median_kwh: 5,
        definition: 'First-year annualized approximation: economic optimum capacity band based on year-1 dispatch savings levelized with annual degradation minus annualized capital cost. Future export price ramp is omitted in this heuristic; refer to the full discounted lifetime NPV heatmap (Chart 4) for authoritative multi-year sizing incorporating the future export price ramp.'
      }
    },
    options: { max_capacity: 10, load_scenario: 'electrified' },
    economics: {
      effective_marginal_battery_cost_eur_kwh: 550,
      marginal_cost_source: 'benchmark',
      annualized_marginal_cost_net: 28.18,
      annualized_marginal_cost_gross: 45.83,
      base_spread_eur_kwh: 0.125,
      margin_per_charge_cycle_eur_kwh: 0.105,
      export_price_active_source: 'empirical_csud_oct2025_sep2026_proxy',
      export_price_profile: 'empirical_csud'
    },
    source_vs_scenario: [],
    chart3_efc_breakeven: {
      capacity_efc: [{
        capacity_kwh: 5,
        efc_physical: { min: 100, median: 150, max: 200 },
        efc_delivered: { min: 90, median: 135, max: 180 },
        efc_charge: { min: 110, median: 165, max: 220 }
      }],
      breakeven_charge_cycles_gross: 436.3,
      breakeven_charge_cycles_net: 268.4,
      cycle_conversion_note: 'Chart 3 provides a single-year equivalent-cycle heuristic approximation that omits multi-year future export price ramp.'
    },
    chart4_npv_heatmap: {
      pv_grid_kwp: [3.0, 6.0],
      battery_grid_kwh: [0.0, 5.0],
      cells: [
        { pv_kwp: 3.0, battery_kwh: 0.0, npv_median: 4000, npv_min: 3500, npv_max: 4500, capex_eur: 5000 },
        { pv_kwp: 6.0, battery_kwh: 5.0, npv_median: 7500, npv_min: 6800, npv_max: 8200, capex_eur: 11000 }
      ],
      current_configuration: { pv_kwp: 6.0, battery_kwh: 5.0 }
    },
    chart5_needed_capacity_cdf: {
      cdf_points: [{ capacity_kwh: 5, fraction_days_met: 0.8, days_met_count: 292, fraction_days_full_utilization: 0.5 }],
      percentiles: { min: 0, p10: 0.5, p25: 2.2, p50: 5.8, p75: 7.9, p80: 8.7, p90: 9.5, max: 13.8 }
    },
    chart6_soc_heatmap: {
      active_capacity_kwh: 5,
      weather_year: 2015,
      weather_representative: 'median',
      empty_fraction: 0.566,
      full_fraction: 0.143,
      mid_fraction: 0.291,
      hourly_soc_matrix: Array.from({ length: 365 }, () => Array.from({ length: 24 }, () => 50))
    }
  };

  sandbox.renderBatterySizing(mockBatData);

  const bannerHtml = $('batEconomicsBanner').innerHTML;
  assert.match(bannerHtml, /Wholesale profile:/);
  assert.match(bannerHtml, /empirical_csud_oct2025_sep2026_proxy/);

  // Check Chart 2 callout labeling
  sandbox.renderBatteryIncrementalChart(mockBatData);
  const calloutHtml = $('batOptimumCallout').innerHTML;
  assert.match(calloutHtml, /first-year annualized approximation/i);
  assert.match(calloutHtml, /omits multi-year future export price ramp/i);
  assert.match(calloutHtml, /full discounted NPV heatmap/i);
});

test('onePage generates truthful downloadable HTML across empirical proxy, actual chronological, flat, and no-export without NaN/undefined/invented 2024', () => {
  let downloadedArtifact = null;
  sandbox.download = (content, filename) => {
    downloadedArtifact = { content, filename };
  };

  const baseResult = {
    settings: {
      load_scenario: 'electrified',
      battery_kwh: 11.04,
      inverter_kw: 6.0,
      horizon_years: 20,
      inflation: 0.02,
      deduction_rate: 0.5,
      energy_real_escalation: 0.01
    },
    layout: { kwp: 8.28, panel_count: 19 },
    metrics: {
      pv_kwh: { median: 10400, min: 9500, max: 11200 },
      export_kwh: { median: 3200, min: 2800, max: 3600 },
      autarky: { min: 0.65, median: 0.72, max: 0.78 },
      self_consumption: { min: 0.58, median: 0.65, max: 0.72 },
      npv_eur: { min: 8300, median: 9100, max: 9900 },
      package_npv_eur: { min: 12000, median: 14000, max: 16000 },
      payback_years: { min: 7.0, median: 7.8, max: 8.5 },
      discounted_payback_years: { min: 8.0, median: 8.9, max: 10.0 },
      irr: { min: 0.08, median: 0.10, max: 0.12 },
      lcoe_eur_kwh: { min: 0.06, median: 0.07, max: 0.08 }
    },
    finance: {
      capex_eur: 15236,
      conversion_capex_eur: 0,
      nominal_discount_rate: 0.0608,
      real_discount_rate: 0.04,
      annual_deduction_eur: 761.8,
      loan_principal_eur: 0,
      requested_loan_amount_eur: 0,
      loan_scope: 'solar',
      whole_package: { simple_payback: { min: 6.5, median: 7.2, max: 8.0 }, discounted_payback: { min: 7.5, median: 8.5, max: 9.5 } }
    }
  };

  // setCurrent helper is defined in outer test scope

  // 1. Empirical Proxy Case
  setCurrent({
    result: {
      ...baseResult,
      export_price_analysis: {
        profile_source: 'empirical_csud_oct2025_sep2026_proxy',
        profile_label: 'Empirical CSUD shape',
        profile_type: 'empirical',
        window: 'oct2025_sep2026',
        baseload_price_eur_kwh: 0.128,
        legacy_capture_factor: 0.74,
        legacy_capture_factor_active: false,
        export_weighted_capture_factor: 0.767247845,
        export_weighted_captured_price_eur_kwh: 0.09821,
        annual_export_kwh: 3200,
        flat_benchmark_comparison: { flat_captured_price_eur_kwh: 0.09472 }
      }
    }
  });
  sandbox.onePage();
  assert.ok(downloadedArtifact, 'HTML summary was downloaded');
  assert.strictEqual(downloadedArtifact.filename, 'pv-lab-one-page.html');
  assert.doesNotMatch(downloadedArtifact.content, /NaN/, 'No NaN in empirical proxy export HTML');
  assert.doesNotMatch(downloadedArtifact.content, /undefined/, 'No undefined in empirical proxy export HTML');
  assert.doesNotMatch(downloadedArtifact.content, /\b2024\b/, 'No invented 2024 in empirical proxy summary');
  assert.match(downloadedArtifact.content, /Empirical CSUD shape \(oct2025 sep2026\) profile is active/);
  assert.match(downloadedArtifact.content, /baseload €0\.1280\/kWh/);
  assert.match(downloadedArtifact.content, /export-weighted capture factor 76\.7%/);
  assert.match(downloadedArtifact.content, /effective captured price €0\.0982\/kWh/);
  assert.match(downloadedArtifact.content, /€9,100 \(€8,300 – €9,900\)/, 'Incremental NPV renders median headline and weather range');
  assert.match(downloadedArtifact.content, /€14,000 \(€12,000 – €16,000\)/, 'Package NPV renders median headline and weather range');
  assert.match(downloadedArtifact.content, /8\.9 y \(8\.0 y – 10\.0 y\)/, 'Discounted payback renders median headline and weather range');
  assert.match(downloadedArtifact.content, /7\.8 y \(7\.0 y – 8\.5 y\)/, 'Simple payback renders median headline and weather range');
  assert.match(downloadedArtifact.content, /72\.0% \(65\.0% – 78\.0%\)/, 'Autarky renders median headline and weather range');
  assert.match(downloadedArtifact.content, /65\.0% \(58\.0% – 72\.0%\)/, 'Self-consumption renders median headline and weather range');
  assert.match(downloadedArtifact.content, /10,400 kWh \(9,500 – 11,200 kWh\)/, 'PV generation renders median headline and weather range');
  assert.match(downloadedArtifact.content, /3,200 kWh \(2,800 – 3,600 kWh\)/, 'Grid export renders median headline and weather range');
  assert.match(downloadedArtifact.content, /10\.0% \(8\.0% – 12\.0%\) \/ €0\.070 \(€0\.060 – €0\.080\) per AC kWh/, 'IRR and LCOE render median headlines and weather ranges');

  // 2. Actual Chronological GME Case
  downloadedArtifact = null;
  setCurrent({
    result: {
      ...baseResult,
      export_price_analysis: {
        profile_source: 'actual_hourly_gme_profile',
        profile_label: 'Actual chronological hourly GME',
        profile_type: 'empirical',
        window: 'historic_gme',
        baseload_price_eur_kwh: 0.115,
        legacy_capture_factor: 0.74,
        legacy_capture_factor_active: false,
        export_weighted_capture_factor: 0.75,
        export_weighted_captured_price_eur_kwh: 0.0862,
        annual_export_kwh: 3200
      }
    }
  });
  sandbox.onePage();
  assert.ok(downloadedArtifact);
  assert.doesNotMatch(downloadedArtifact.content, /NaN/);
  assert.doesNotMatch(downloadedArtifact.content, /undefined/);
  assert.match(downloadedArtifact.content, /Actual chronological hourly GME \(historic gme\) profile is active/);
  assert.match(downloadedArtifact.content, /effective captured price €0\.0862\/kWh/);
  assert.match(downloadedArtifact.content, /€9,100 \(€8,300 – €9,900\)/, 'Retains median NPV in actual chronological GME export');

  // 3. Flat Benchmark Case
  downloadedArtifact = null;
  setCurrent({
    result: {
      ...baseResult,
      export_price_analysis: {
        profile_type: 'flat',
        baseload_price_eur_kwh: 0.128,
        legacy_capture_factor: 0.74,
        annual_export_kwh: 3200,
        flat_benchmark_comparison: { flat_captured_price_eur_kwh: 0.09472 }
      }
    }
  });
  sandbox.onePage();
  assert.ok(downloadedArtifact);
  assert.doesNotMatch(downloadedArtifact.content, /NaN/);
  assert.doesNotMatch(downloadedArtifact.content, /undefined/);
  assert.match(downloadedArtifact.content, /Flat solar capture benchmark is active \(baseload €0\.1280\/kWh × constant capture factor 74\.0% = €0\.0947\/kWh\)/);
  assert.match(downloadedArtifact.content, /€9,100 \(€8,300 – €9,900\)/, 'Retains median NPV in flat benchmark export');

  // 4. Zero Exports Case
  downloadedArtifact = null;
  setCurrent({
    result: {
      ...baseResult,
      metrics: {
        ...baseResult.metrics,
        export_kwh: { median: 0, min: 0, max: 0 }
      },
      export_price_analysis: {
        profile_source: 'empirical_csud_oct2025_sep2026_proxy',
        profile_label: 'Empirical CSUD shape',
        profile_type: 'empirical',
        window: 'oct2025_sep2026',
        baseload_price_eur_kwh: 0.128,
        annual_export_kwh: 0,
        export_weighted_capture_factor: null,
        export_weighted_captured_price_eur_kwh: null
      }
    }
  });
  sandbox.onePage();
  assert.ok(downloadedArtifact);
  assert.doesNotMatch(downloadedArtifact.content, /NaN/);
  assert.doesNotMatch(downloadedArtifact.content, /undefined/);
  assert.match(downloadedArtifact.content, /Empirical CSUD shape \(oct2025 sep2026\) profile is active with no grid exports modeled/);
  assert.doesNotMatch(downloadedArtifact.content, /100% autarky/i, 'Zero exports does not infer or claim 100% autarky');
  assert.match(downloadedArtifact.content, /0 kWh/, 'Export row shows 0 kWh');
});

test('onePage handles unrecovered payback honestly without NaN or broken formatting', () => {
  let downloadedArtifact = null;
  sandbox.download = (content, filename) => {
    downloadedArtifact = { content, filename };
  };
  const unrecoveredResult = {
    settings: {
      load_scenario: 'electrified',
      battery_kwh: 0,
      inverter_kw: 6.0,
      horizon_years: 20,
      inflation: 0.02,
      deduction_rate: 0.5,
      energy_real_escalation: 0.01
    },
    layout: { kwp: 8.28, panel_count: 19 },
    metrics: {
      pv_kwh: { median: 10400, min: 9500, max: 11200 },
      export_kwh: { median: 3200, min: 2800, max: 3600 },
      autarky: { min: 0.65, median: 0.72, max: 0.78 },
      self_consumption: { min: 0.58, median: 0.65, max: 0.72 },
      npv_eur: { min: -5000, median: -3000, max: -1000 },
      package_npv_eur: { min: -4000, median: -2000, max: -500 },
      payback_years: { min: null, median: null, max: null, recovered_weather_years: 0 },
      discounted_payback_years: { min: null, median: null, max: null, recovered_weather_years: 0 },
      irr: { min: -0.05, median: -0.02, max: 0.01 },
      lcoe_eur_kwh: { min: 0.18, median: 0.20, max: 0.22 }
    },
    finance: {
      capex_eur: 20000,
      conversion_capex_eur: 0,
      nominal_discount_rate: 0.0608,
      real_discount_rate: 0.04,
      annual_deduction_eur: 1000,
      loan_principal_eur: 0,
      requested_loan_amount_eur: 0,
      loan_scope: 'solar',
      whole_package: {
        simple_payback: { min: null, median: null, max: null, recovered_weather_years: 0 },
        discounted_payback: { min: null, median: null, max: null, recovered_weather_years: 0 }
      }
    }
  };

  setCurrent({ result: unrecoveredResult });
  sandbox.onePage();

  assert.ok(downloadedArtifact);
  assert.match(downloadedArtifact.content, /Not reached/, 'Unrecovered payback renders Not reached');
  assert.doesNotMatch(downloadedArtifact.content, /NaN/);
});

test('evidence calibration headline formats without duplicate euro sign and uses invoice kwh with closed fold', () => {
  sandbox._testSchema = {
    calibration: {
      invoice: {
        total_eur: 145.20,
        kwh: 743.0,
        commodity_eur: 85.10,
        regulated_eur: 25.30,
        excise_eur: 12.00,
        fixed_eur: 10.50,
        vat_eur: 12.30
      },
      annual_kwh: 12500,
      annual_gas_smc: 980,
      annual_electric_bill_eur: 2800,
      annual_gas_runrate_eur: 1100,
      calibrated_6kwp_eur: 7800,
      original_cost_formula_6kwp_eur: 8200,
      provided_capture_eur_kwh: 0.08,
      base_times_capture_eur_kwh: 0.09,
      roofs: [],
      research: {
        research_results: {
          turnkey_market_benchmarks: {
            pv_installed_benchmark_6kwp_total_eur: 7500,
            pv_installed_eur_kwp_range: [1100, 1400],
            lfp_battery_storage_eur_kwh_usable_range: [500, 700]
          }
        }
      }
    },
    audit: {
      timezone: 'UTC+1',
      leap_policy: 'deterministic 29 Feb',
      resolution: 'hourly',
      shade_method: 'PVGIS beam-shaded'
    }
  };
  vm.runInContext('schema = _testSchema;', sandbox);

  sandbox.evidence();
  const calHtml = $('calibration').innerHTML;
  assert.doesNotMatch(calHtml, /€€/, 'No duplicate euro sign in calibration headline');
  assert.match(calHtml, /Reconciled total €145 across 743 kWh billed sample/, 'Uses formatted total and actual sample kWh');
  assert.doesNotMatch(calHtml, /780 kWh/, 'Does not hardcode 780 kWh');
  assert.match(calHtml, /&lt;details class=&quot;technical-details-fold&quot;&gt;&lt;summary&gt;Audit payload \(raw JSON\)&lt;\/summary&gt;/, 'Raw JSON audit is in closed details fold');
});
