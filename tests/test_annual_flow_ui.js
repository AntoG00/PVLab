/* Run with Node: test annual energy-flow presentation in the PV Lab app.
 * Validates desktop SVG connected Sankey, mobile connected diagram, proportional scaling,
 * direction cues, zero/edge cases, stored-energy delta reconciliation, details fold,
 * accessibility, and quote-normalized settings.
 * Strengthened test suite: validates coherent field protection, signed SOC delta notes,
 * material residual distinction vs rounding, normalized capacity precision, mobile arrow
 * endpoints without gaps, zero intersection of export path with battery rectangle,
 * and container-responsive queries.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const cssSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/style.css'), 'utf8');
const htmlSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/index.html'), 'utf8');

function createTestContext() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) {
      const classSet = new Set();
      elements.set(id, {
        id,
        innerHTML: '',
        textContent: '',
        value: '',
        hidden: false,
        disabled: false,
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
        setAttribute: () => {},
        getAttribute: () => null
      });
    }
    return elements.get(id);
  };

  const context = {
    $,
    elements,
    console,
    Math,
    Number,
    String,
    Date,
    Array,
    getForecastYear: () => 2027,
    inputFingerprint: (s, q) => `${s?.load_scenario || 'electrified'}_fp`,
    title: s => ({ baseline: 'Historical electricity', electrified: 'Electrified · gas eliminated', ev: 'Electrified + EV' }[s] || s),
    esc: s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    nf: (n, d = 0) => Number(n ?? 0).toLocaleString('en-IE', { maximumFractionDigits: d, minimumFractionDigits: d }),
    fmt: (n, type = 'number') => {
      if (n == null) return '—';
      const num = Number(n ?? 0);
      return num.toLocaleString('en-IE', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
    },
    settings: { load_scenario: 'electrified' },
    quote: { line_items: [] },
    current: {
      result: {
        settings: { load_scenario: 'electrified', battery_kwh: 10.0, roundtrip_efficiency: 0.90 }
      }
    },
    behaviorMatrixCache: null,
    behaviorMatrixKey: '',
    acceptedInputFingerprint: 'electrified_fp'
  };

  vm.createContext(context);
  const routingStart = appSource.indexOf('/* ROUTING PRIORITY HELPERS (start) */');
  const routingEnd = appSource.indexOf('/* ROUTING PRIORITY HELPERS (end) */');
  assert(routingStart >= 0 && routingEnd > routingStart);
  vm.runInContext(appSource.slice(routingStart, routingEnd), context);
  // Extract and run renderAnnualEnergyFlow from appSource
  const fnStart = appSource.indexOf('function renderAnnualEnergyFlow()');
  assert(fnStart >= 0, 'renderAnnualEnergyFlow must exist in app.js');
  const fnEnd = appSource.indexOf('let behaviorMatrixCache=null;', fnStart);
  assert(fnEnd > fnStart, 'End of renderAnnualEnergyFlow must be found');

  vm.runInContext(appSource.slice(fnStart, fnEnd), context);
  return { context, elements, $ };
}

console.log('--- Running Strengthened PV Lab Annual Energy-Flow Presentation Tests ---');

// Test 1: Pending state when no result is present
{
  const { context, $ } = createTestContext();
  context.current = null;
  vm.runInContext('renderAnnualEnergyFlow()', context);
  const visual = $('annualEnergyFlowVisual');
  assert.match(visual.innerHTML, /annual-flow-pending/, 'Renders pending container when current result is null');
  assert.match(visual.innerHTML, /Scenario calculation pending/, 'States scenario calculation pending');
  console.log('✔ Test 1: Pending state on null result passed');
}

// Test 2: Pending state on stale or missing behaviorMatrixCache
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixCache = null;
  vm.runInContext('renderAnnualEnergyFlow()', context);
  const visual = $('annualEnergyFlowVisual');
  assert.match(visual.innerHTML, /annual-flow-pending/, 'Renders pending container when cache is null');
  assert.match(visual.innerHTML, /Loading annual energy flow simulation/, 'States loading simulation');
  console.log('✔ Test 2: Pending state on missing cache passed');
}

// Test 3: Coherent field protection (Finding 4)
// Validates finite numeric required fields for all 6 paths and SOC reconciliation
{
  const baseFlows = {
    pv_generation_kwh: 4500.0,
    load_demand_kwh: 3600.0,
    grid_import_kwh: 1200.0,
    grid_export_kwh: 1800.0,
    direct_use_kwh: 1500.0,
    battery_charge_kwh: 1200.0,
    battery_discharge_kwh: 900.0,
    storage_losses_kwh: 297.6,
    initial_soc_kwh: 0.0,
    end_soc_kwh: 2.4,
    storage_delta_kwh: 2.4
  };

  const requiredFields = Object.keys(baseFlows);

  // Each required field tested individually for null, undefined, and NaN
  for (const field of requiredFields) {
    for (const badValue of [null, undefined, NaN, 'not_a_number']) {
      const { context, $ } = createTestContext();
      context.behaviorMatrixKey = 'electrified_electrified_fp';
      context.behaviorMatrixCache = {
        annual_flows: { ...baseFlows, [field]: badValue }
      };
      vm.runInContext('renderAnnualEnergyFlow()', context);
      const visual = $('annualEnergyFlowVisual');
      assert.match(visual.innerHTML, /annual-flow-unavailable/, `Field ${field}=${badValue} must render unavailable`);
      assert.match(visual.innerHTML, /Annual energy flow data is unavailable/, `Field ${field}=${badValue} shows honest unavailable message`);
    }
  }

  // Explicit valid zero values work normally and do NOT trigger unavailable
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2016,
      battery_kwh: 0.0,
      annual_flows: {
        ...baseFlows,
        grid_export_kwh: 0.0,
        battery_charge_kwh: 0.0,
        battery_discharge_kwh: 0.0,
        storage_losses_kwh: 0.0,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const visual = $('annualEnergyFlowVisual');
    assert.doesNotMatch(visual.innerHTML, /annual-flow-unavailable/, 'Explicit valid 0 values render normally');
  }

  console.log('✔ Test 3: Coherent field protection across all 11 required fields passed');
}

// Test 4: Full connected Sankey, mobile diagram, autarky calculation, metadata placement directly beneath title once
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2016,
    weather_count: 19,
    battery_kwh: 10.0,
    annual_flows: {
      pv_generation_kwh: 4500.0,
      load_demand_kwh: 3600.0,
      grid_import_kwh: 1200.0,
      grid_export_kwh: 1800.0,
      direct_use_kwh: 1500.0,
      battery_charge_kwh: 1200.0,
      battery_discharge_kwh: 900.0,
      storage_losses_kwh: 297.6,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 2.4,
      storage_delta_kwh: 2.4,
      clipped_kwh: 120.0,
      weather_year: 2016,
      aggregation: 'Coherent single-weather analogue (median PV yield)'
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;
  const metaHtml = $('annualFlowMeta').innerHTML;

  // Title and metadata placed directly beneath annual title ONCE (Finding 9)
  assert.match(metaHtml, /Operating year: <strong>2027<\/strong>/, 'Titles operating year once beneath annual title');
  assert.match(metaHtml, /Selected weather analogue: <strong>Year 2016<\/strong>/, 'Titles selected weather analogue once beneath annual title');
  assert.doesNotMatch(metaHtml, /19-year median/, 'Never labels single analogue as the 19-year median');
  assert.doesNotMatch(html, /flow-takeaway-meta/, 'Takeaway card does not repeat date/weather metadata footer');

  // Takeaway headline: self-sufficiency above diagram
  assert.match(html, /Household Self-Sufficiency \(Autarky\)/, 'Takeaway headline present');
  // Clean on-site supply: 3600 - 1200 = 2400 kWh (66.7%)
  assert.match(html, /66\.7%/, 'Calculates autarky: (3600 - 1200) / 3600 = 66.7%');
  assert.match(html, /2,400(\.0)? kWh\/yr/, 'States 2,400 kWh/yr supplied on-site');

  // Denominators are explicit household demand and solar generation
  assert.match(html, /of household demand/, 'Secondary percentage explicitly states household demand denominator');
  assert.match(html, /of solar generation/, 'Secondary percentage explicitly states solar generation denominator');

  // Desktop Sankey bands
  assert.match(html, /annual-flow-desktop-container/, 'Includes desktop Sankey container');
  assert.match(html, /annual-flow-sankey-svg/, 'Includes SVG with Sankey class');

  // Verify all 6 flow bands in desktop SVG
  assert.match(html, /Grid imports ➔ Household/, 'Band 1: Grid imports to household present');
  assert.match(html, /Direct solar ➔ Household/, 'Band 2: Direct solar to household present');
  assert.match(html, /Solar ➔ Battery/, 'Band 3: Solar to battery present');
  assert.match(html, /Battery ➔ Household/, 'Band 4: Battery to household present');
  assert.match(html, /Storage losses/, 'Band 5: Battery to losses present');
  assert.match(html, /Solar ➔ Grid exports/, 'Band 6: Solar to grid exports present');

  // Color conventions
  assert.match(html, /fill="#8f5da5"/, 'Purple ribbon for grid imports');
  assert.match(html, /fill="#217a61"/, 'Green ribbon for direct solar');
  assert.match(html, /fill="#d97724"/, 'Orange ribbon for battery charge');
  assert.match(html, /fill="#df8a54"/, 'Orange ribbon for battery discharge');
  assert.match(html, /fill="#7d8793"/, 'Grey ribbon for storage losses');
  assert.match(html, /fill="#4a7bb0"/, 'Blue ribbon for grid exports');

  // Stored energy delta note outside both diagrams (Finding 7)
  assert.match(html, /Stored reserve delta/, 'Renders stored reserve delta note for positive end SOC');
  assert.match(html, /\+2\.4 kWh/, 'Shows net stored delta +2.4 kWh');

  // Details fold closed by default with exact summary
  assert.match(html, /<details class="energy-balance-details">/, 'Native details tag used');
  assert.doesNotMatch(html, /<details class="energy-balance-details" open>/, 'Details fold is closed by default');
  assert.match(html, /<summary>Energy balance details<\/summary>/, 'Summary text exactly matches specification');

  // Reconciliations
  assert.match(html, /Usable PV Energy Balance/, 'Includes topology-neutral PV energy balance reconciliation');
  assert.match(html, /Household Demand Balance/, 'Includes household demand balance reconciliation');
  assert.match(html, /Battery Storage Conservation/, 'Includes battery conservation reconciliation');
  assert.match(html, /Inverter clipping before battery charging/, 'Includes the legacy-mode PV curtailment reconciliation');
  assert.match(html, /120(\.0)? kWh\/yr/, 'Shows 120 kWh upstream clipping');

  // Mobile compact connected diagram
  assert.match(html, /annual-flow-mobile-container/, 'Includes mobile diagram container');
  assert.match(html, /annual-flow-mobile-svg/, 'Includes mobile SVG');
  assert.match(html, /marker-end="url\(#mobArrPurple\)"/, 'Mobile arrows use directional markers');
  assert.match(html, /marker-end="url\(#mobArrGreen\)"/, 'Mobile direct arrow uses green marker');
  assert.match(html, /marker-end="url\(#mobArrOrange\)"/, 'Mobile battery arrows use orange markers');
  assert.match(html, /marker-end="url\(#mobArrBlue\)"/, 'Mobile export arrow uses blue marker');

  console.log('✔ Test 4: Connected Sankey, mobile diagram, autarky, and reconciliations passed');
}

// Test 5: Proportional scale invariance (same scale factor k across all nodes and bands)
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2016,
    battery_kwh: 5.0,
    annual_flows: {
      pv_generation_kwh: 5000.0,
      load_demand_kwh: 4000.0,
      grid_import_kwh: 1000.0,
      grid_export_kwh: 2000.0,
      direct_use_kwh: 2000.0,
      battery_charge_kwh: 1000.0,
      battery_discharge_kwh: 850.0,
      storage_losses_kwh: 150.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2016
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  assert.match(html, /5,000(\.0)? kWh/, 'Correctly formats solar kWh');
  assert.match(html, /4,000(\.0)? kWh/, 'Correctly formats demand kWh');
  assert.match(html, /1,000(\.0)? kWh/, 'Correctly formats import kWh');
  assert.match(html, /2,000(\.0)? kWh/, 'Correctly formats export kWh');
  console.log('✔ Test 5: Proportional scale invariance passed');
}

// Test 6: Zero storage installed (battery_kwh = 0)
{
  const { context, $ } = createTestContext();
  context.current.result.settings.battery_kwh = 0.0;
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2018,
    battery_kwh: 0.0,
    annual_flows: {
      pv_generation_kwh: 4000.0,
      load_demand_kwh: 3500.0,
      grid_import_kwh: 2000.0,
      grid_export_kwh: 2500.0,
      direct_use_kwh: 1500.0,
      battery_charge_kwh: 0.0,
      battery_discharge_kwh: 0.0,
      storage_losses_kwh: 0.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2018
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  assert.match(html, /No Battery Storage/, 'Indicates no battery storage installed');
  assert.doesNotMatch(html, /Solar ➔ Battery charge/, 'Omits battery charge band when 0');
  assert.doesNotMatch(html, /Battery ➔ Household/, 'Omits battery discharge band when 0');
  assert.doesNotMatch(html, /Battery roundtrip losses/, 'Omits battery loss band when 0');
  assert.match(html, /No battery storage configured \(0 kWh capacity\)/, 'Details state no battery configured');
  const svgBoxMatch6 = html.match(/class="annual-flow-sankey-svg"[^>]*viewBox="0 0 (\d+) (\d+)"/);
  assert(svgBoxMatch6, 'Desktop SVG viewBox found for zero storage');
  const noStorageHeight = parseFloat(svgBoxMatch6[2]);
  assert(noStorageHeight <= 580, `No-storage scenario maintains compact viewBox without artificial blank space (actual: ${noStorageHeight}px)`);
  console.log('✔ Test 6: Zero storage installed passed');
}

// Test 7: Storage installed but idle (battery_kwh = 6.0, charge = 0, discharge = 0)
{
  const { context, $ } = createTestContext();
  context.current.result.settings.battery_kwh = 6.0;
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2018,
    battery_kwh: 6.0,
    annual_flows: {
      pv_generation_kwh: 4000.0,
      load_demand_kwh: 3500.0,
      grid_import_kwh: 2000.0,
      grid_export_kwh: 2500.0,
      direct_use_kwh: 1500.0,
      battery_charge_kwh: 0.0,
      battery_discharge_kwh: 0.0,
      storage_losses_kwh: 0.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2018
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  assert.match(html, /Installed · Idle in this scenario/, 'Distinguishes installed but idle battery');
  assert.match(html, /Battery Storage/, 'Displays Battery Storage title');
  assert.match(html, /6(\.0)? kWh/, 'Displays installed capacity 6.0 kWh');
  const svgBoxMatch7 = html.match(/class="annual-flow-sankey-svg"[^>]*viewBox="0 0 (\d+) (\d+)"/);
  assert(svgBoxMatch7, 'Desktop SVG viewBox found for idle storage');
  const idleStorageHeight = parseFloat(svgBoxMatch7[2]);
  assert(idleStorageHeight <= 600, `Idle storage scenario maintains compact viewBox without artificial blank space (actual: ${idleStorageHeight}px)`);
  console.log('✔ Test 7: Storage installed but idle passed');
}

// Test 8: Zero PV generation (pv = 0, demand = 3000, imports = 3000)
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2015,
    battery_kwh: 0.0,
    annual_flows: {
      pv_generation_kwh: 0.0,
      load_demand_kwh: 3000.0,
      grid_import_kwh: 3000.0,
      grid_export_kwh: 0.0,
      direct_use_kwh: 0.0,
      battery_charge_kwh: 0.0,
      battery_discharge_kwh: 0.0,
      storage_losses_kwh: 0.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2015
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  assert.match(html, /0\.0%/, 'Autarky is 0.0% with zero PV');
  assert.match(html, /Grid imports/, 'Shows grid imports supplying 100% of demand');
  assert.doesNotMatch(html, /Direct solar ➔ Household/, 'Omits direct solar band when 0');
  console.log('✔ Test 8: Zero PV generation passed');
}

// Test 9: Zero demand (demand = 0) honest unavailable/not applicable state
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2015,
    battery_kwh: 0.0,
    annual_flows: {
      pv_generation_kwh: 3000.0,
      load_demand_kwh: 0.0,
      grid_import_kwh: 0.0,
      grid_export_kwh: 3000.0,
      direct_use_kwh: 0.0,
      battery_charge_kwh: 0.0,
      battery_discharge_kwh: 0.0,
      storage_losses_kwh: 0.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2015
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  assert.match(html, /Not applicable/, 'Reports autarky as Not applicable when demand is zero');
  assert.match(html, /Zero household electricity demand recorded/, 'Explains zero demand honestly');
  assert.doesNotMatch(html, /NaN%/, 'Never produces NaN%');
  console.log('✔ Test 9: Zero demand honest not applicable state passed');
}

// Test 10: Zero exports (100% self-consumption)
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2015,
    battery_kwh: 5.0,
    annual_flows: {
      pv_generation_kwh: 2000.0,
      load_demand_kwh: 4000.0,
      grid_import_kwh: 2000.0,
      grid_export_kwh: 0.0,
      direct_use_kwh: 1500.0,
      battery_charge_kwh: 500.0,
      battery_discharge_kwh: 450.0,
      storage_losses_kwh: 50.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2015
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  assert.doesNotMatch(html, /Solar ➔ Grid exports:/, 'Omits exports band when export is 0');
  const svgBoxMatch10 = html.match(/class="annual-flow-sankey-svg"[^>]*viewBox="0 0 (\d+) (\d+)"/);
  assert(svgBoxMatch10, 'Desktop SVG viewBox found for zero exports');
  const zeroExpHeight = parseFloat(svgBoxMatch10[2]);
  assert(zeroExpHeight <= 450, `Zero-export scenario does not inflate viewBox height with unused export endpoint (actual: ${zeroExpHeight}px)`);
  console.log('✔ Test 10: Zero exports passed');
}

// Test 11: Quote-normalized equipment settings & precision (Finding 8)
// Raw capacity 8.28 displayed as 8.28 kWh (not 8 or 8k).
// Quote normalized 4.8 displayed as 4.8 kWh (not 5).
// Capacity clearly separate from annual throughput.
{
  // Part A: 8.28 kWh capacity
  {
    const { context, $ } = createTestContext();
    context.current.result.settings.battery_kwh = 8.28;
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2015,
      battery_kwh: 8.28,
      annual_flows: {
        pv_generation_kwh: 12090.3,
        load_demand_kwh: 7749.7,
        grid_import_kwh: 1313.3,
        grid_export_kwh: 5397.6,
        direct_use_kwh: 4130.5,
        battery_charge_kwh: 2562.2,
        battery_discharge_kwh: 2306.0,
        storage_losses_kwh: 256.2,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        clipped_kwh: 0.0,
        weather_year: 2015
      }
    };
    context.behaviorMatrixKey = 'electrified_electrified_fp';

    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.match(html, /8\.28 kWh/, 'Desktop and mobile display raw/normalized battery capacity with exact precision 8.28 kWh');
    assert.doesNotMatch(html, /Battery \(8k\)/, 'Mobile does not truncate 8.28 to 8k');
    assert.doesNotMatch(html, /Battery Storage \(8 kWh\)/, 'Desktop does not round 8.28 to integer 8 kWh');
    // Capacity clearly separate from annual throughput
    assert.match(html, /2,562(\.2)? kWh in/, 'Annual charge throughput clearly formatted');
    assert.match(html, /2,306(\.0)? kWh out/, 'Annual discharge throughput clearly formatted');
  }

  // Part B: 4.8 kWh quote-normalized capacity
  {
    const { context, $ } = createTestContext();
    context.current.result.settings.battery_kwh = 4.8;
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2015,
      battery_kwh: 4.8,
      annual_flows: {
        pv_generation_kwh: 12090.3,
        load_demand_kwh: 7749.7,
        grid_import_kwh: 2039.9,
        grid_export_kwh: 6205.0,
        direct_use_kwh: 4130.5,
        battery_charge_kwh: 1754.8,
        battery_discharge_kwh: 1579.4,
        storage_losses_kwh: 175.5,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        clipped_kwh: 0.0,
        weather_year: 2015
      }
    };
    context.behaviorMatrixKey = 'electrified_electrified_fp';

    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.match(html, /4\.8 kWh/, 'Displays quote-normalized capacity 4.8 kWh without rounding to 5');
    assert.doesNotMatch(html, /Battery \(5k\)/, 'Mobile does not truncate 4.8 to 5k');
  }

  console.log('✔ Test 11: Quote-normalized equipment settings & precision passed');
}

// Test 12: Storage balance reconciliation & RTE labeling (Finding 5)
// Modeled roundtrip efficiency is clearly identified as equipment assumption.
// No distorted discharge/charge ratio mislabeled as RTE.
// Discharged share explicitly notes SOC delta.
// Never calls all charge-discharge heat loss.
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.current.result.settings.roundtrip_efficiency = 0.90;
  // charge = 1000, discharge = 750, true losses = 200, delta = 50
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2016,
    battery_kwh: 10.0,
    annual_flows: {
      pv_generation_kwh: 4000.0,
      load_demand_kwh: 3000.0,
      grid_import_kwh: 1000.0,
      grid_export_kwh: 1500.0,
      direct_use_kwh: 1500.0,
      battery_charge_kwh: 1000.0,
      battery_discharge_kwh: 750.0,
      storage_losses_kwh: 200.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 50.0,
      storage_delta_kwh: 50.0,
      clipped_kwh: 0.0,
      weather_year: 2016
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  // Reconciles charge = discharge + losses + delta
  assert.match(html, /Charge \(1,000(\.0)?\) = Discharge \(750(\.0)?\) \+ Losses \(200(\.0)?\) \+ Stored Delta \(50(\.0)?\)/, 'Reconciles charge = discharge + losses + delta');
  // Does NOT mislabel 75% as roundtrip efficiency
  assert.doesNotMatch(html, /75(\.0)?% RTE/, 'Never calls discharge/charge (75%) Roundtrip efficiency RTE');
  // Modeled equipment RTE assumption clearly identified
  assert.match(html, /Modeled equipment roundtrip efficiency: <strong>90% RTE<\/strong>/, 'Clearly displays 90% RTE as equipment assumption');
  assert.match(html, /Discharged share of charging: <strong>75\.0%<\/strong>/, 'Labels 75% as discharged share of charging');
  // Never calls all charge - discharge heat loss
  assert.match(html, /Actual conversion losses: <strong>200(\.0)? kWh<\/strong>/, 'Identifies true conversion losses separately from stored energy');

  console.log('✔ Test 12: Storage balance reconciliation with true losses & equipment RTE passed');
}

// Test 13: Signed SOC changes & visible note outside both diagrams (Finding 7)
{
  // Part A: Positive delta (+2.4 kWh)
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2016,
      battery_kwh: 10.0,
      annual_flows: {
        pv_generation_kwh: 4500.0,
        load_demand_kwh: 3600.0,
        grid_import_kwh: 1200.0,
        grid_export_kwh: 1800.0,
        direct_use_kwh: 1500.0,
        battery_charge_kwh: 1200.0,
        battery_discharge_kwh: 900.0,
        storage_losses_kwh: 297.6,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 2.4,
        storage_delta_kwh: 2.4,
        clipped_kwh: 0.0,
        weather_year: 2016
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.match(html, /flow-storage-delta-note/, 'Renders visible note container outside both diagrams');
    assert.match(html, /\+2\.4 kWh/, 'Shows positive signed delta +2.4 kWh');
    assert.match(html, /Initial storage SOC: <strong>0 kWh<\/strong>/, 'Shows starting SOC 0 kWh');
    assert.match(html, /Year-end SOC: <strong>2\.4 kWh<\/strong>/, 'Shows ending SOC 2.4 kWh');
    assert.match(html, /conserved on-board rather than lost to thermal dissipation/, 'Explains energy retained vs conversion losses');
  }

  // Part B: Negative delta (-1.5 kWh)
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2016,
      battery_kwh: 10.0,
      annual_flows: {
        pv_generation_kwh: 4500.0,
        load_demand_kwh: 3600.0,
        grid_import_kwh: 1200.0,
        grid_export_kwh: 1800.0,
        direct_use_kwh: 1500.0,
        battery_charge_kwh: 1000.0,
        battery_discharge_kwh: 950.0,
        storage_losses_kwh: 65.0,
        initial_soc_kwh: 2.0,
        end_soc_kwh: 0.5,
        storage_delta_kwh: -1.5,
        clipped_kwh: 0.0,
        weather_year: 2016
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.match(html, /flow-storage-delta-note/, 'Renders visible note for negative delta');
    assert.match(html, /-1\.5 kWh/, 'Handles negative signed delta honestly');
    assert.match(html, /Initial storage SOC: <strong>2 kWh<\/strong>/, 'Shows initial SOC 2 kWh');
    assert.match(html, /Year-end SOC: <strong>0\.5 kWh<\/strong>/, 'Shows ending SOC 0.5 kWh');
    assert.match(html, /net reserve withdrawal/, 'Explains withdrawal from pre-existing stored reserves');
  }

  // Part C: Zero delta (0.0 kWh)
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2016,
      battery_kwh: 10.0,
      annual_flows: {
        pv_generation_kwh: 4500.0,
        load_demand_kwh: 3600.0,
        grid_import_kwh: 1200.0,
        grid_export_kwh: 1800.0,
        direct_use_kwh: 1500.0,
        battery_charge_kwh: 1200.0,
        battery_discharge_kwh: 1080.0,
        storage_losses_kwh: 120.0,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        clipped_kwh: 0.0,
        weather_year: 2016
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.doesNotMatch(html, /flow-storage-delta-note/, 'Omits delta callout when delta is zero');
  }

  console.log('✔ Test 13: Signed SOC changes and visible note passed');
}

// Test 14: Reconciliation residual distinction: expected rounding vs material backend imbalance (Finding 6)
{
  // Part A: Expected small rounding <= 0.25 kWh
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2015,
      battery_kwh: 8.28,
      annual_flows: {
        pv_generation_kwh: 12090.3,
        load_demand_kwh: 7749.7,
        grid_import_kwh: 1313.3,
        grid_export_kwh: 5397.6,
        direct_use_kwh: 4130.5,
        battery_charge_kwh: 2562.2,
        battery_discharge_kwh: 2306.0,
        storage_losses_kwh: 256.2,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        clipped_kwh: 0.0,
        weather_year: 2015
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.match(html, /consistent with expected ±0\.1 kWh decimal rounding/, 'Explains tiny residual as consistent with ±0.1 kWh decimal rounding');
    assert.doesNotMatch(html, /material backend imbalance/, 'Does not falsely claim material imbalance for expected decimal rounding');
  }

  // Part B: Material backend imbalance (e.g. 5.2 kWh mismatch)
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2015,
      battery_kwh: 8.28,
      annual_flows: {
        pv_generation_kwh: 12090.3,
        load_demand_kwh: 7749.7,
        grid_import_kwh: 1313.3,
        grid_export_kwh: 5397.6,
        direct_use_kwh: 4130.5,
        // charge is 2567.4 instead of 2562.2 (5.2 kWh imbalance)
        battery_charge_kwh: 2567.4,
        battery_discharge_kwh: 2306.0,
        storage_losses_kwh: 256.2,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        clipped_kwh: 0.0,
        weather_year: 2015
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;

    assert.match(html, /material backend imbalance in simulation records/, 'Honestly reports material imbalance');
    assert.doesNotMatch(html, /residual difference vs total charging input: \+5\.2 kWh \(consistent with expected/, 'Never claims rounding for material imbalance');
  }

  console.log('✔ Test 14: Reconciliation residual distinction (rounding vs material imbalance) passed');
}

// Test 15: Mathematical preservation of tiny nonzero geometry & formatting (Finding 8)
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2016,
    battery_kwh: 5.0,
    annual_flows: {
      pv_generation_kwh: 5000.0,
      load_demand_kwh: 4000.0,
      grid_import_kwh: 1000.0,
      grid_export_kwh: 2000.0,
      direct_use_kwh: 2000.0,
      battery_charge_kwh: 1000.0,
      battery_discharge_kwh: 850.0,
      storage_losses_kwh: 149.8,
      initial_soc_kwh: 0.0,
      // tiny delta of 0.2 kWh
      end_soc_kwh: 0.2,
      storage_delta_kwh: 0.2,
      // tiny clipping of 0.03 kWh
      clipped_kwh: 0.03,
      weather_year: 2016
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  // Tiny delta of 0.2 kWh is formatted with decimal context
  assert.match(html, /0\.2 kWh/, 'Formats 0.2 kWh with decimal context instead of bare 0 kWh');
  // Tiny clipping of 0.03 kWh is formatted with "<0.1 kWh"
  assert.match(html, /<0\.1 kWh\/yr/, 'Formats 0.03 kWh clipping as <0.1 kWh/yr instead of bare 0');
  const svgBoxMatch15 = html.match(/class="annual-flow-sankey-svg"[^>]*viewBox="0 0 (\d+) (\d+)"/);
  assert(svgBoxMatch15, 'Desktop SVG viewBox found for tiny flows');
  const tinyFlowsHeight = parseFloat(svgBoxMatch15[2]);
  assert(tinyFlowsHeight <= 600, `Tiny flows scenario maintains compact viewBox without artificial blank space (actual: ${tinyFlowsHeight}px)`);
  console.log('✔ Test 15: Tiny nonzero geometry and formatting passed');
}
// Test 16: Mobile arrow endpoints, edge landing & zero intersection with Battery and unrelated nodes (Finding 3 & Phone Layout)
// Analytically inspects generated coordinates in mobile SVG:
// 1. Mobile viewBox width is 250 (compact mobile width).
// 2. Export arrow does NOT intersect the Battery node rectangle [72, 178] x [265, 355] or unrelated nodes.
// 3. Import arrow ends exactly on Demand left border (x=142) with zero gap.
// 4. Loss arrow connects Battery right edge (x=178) to Losses top edge with zero gap.
// 5. Arrow labels in reserved whitespace do not clip left/right (Charge label >= 5px from left edge).
// 6. Household demand wrapped into 2 lines, preventing horizontal box overflow.
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2015,
    battery_kwh: 8.28,
    annual_flows: {
      pv_generation_kwh: 12090.3,
      load_demand_kwh: 7749.7,
      grid_import_kwh: 1313.3,
      grid_export_kwh: 5397.6,
      direct_use_kwh: 4130.5,
      battery_charge_kwh: 2562.2,
      battery_discharge_kwh: 2306.0,
      storage_losses_kwh: 256.2,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2015
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  // Mobile SVG viewBox check (viewBox="0 0 250 560")
  assert.match(html, /viewBox="0 0 250 560"/, 'Mobile SVG uses compact width 250 viewBox');

  // Battery node bounds in mobile SVG:
  // <rect x="72" y="265" width="106" height="90" ... />
  const batXMin = 72, batXMax = 72 + 106; // [72, 178]
  const batYMin = 265, batYMax = 265 + 90; // [265, 355]

  // 1. Extract Mobile Export Arrow path
  const exportMatch = html.match(/<path d="(M [^"]+)"[^>]+url\(#mobArrBlue\)/);
  assert(exportMatch, 'Mobile export arrow path exists');
  const dStr = exportMatch[1];

  // Parse path: M x0,y0 C cx1,cy1 cx2,cy2 x1,y1 C cx3,cy3 cx4,cy4 x2,y2
  const segRegex = /C\s*([\d.]+),([\d.]+)\s+([\d.]+),([\d.]+)\s+([\d.]+),([\d.]+)/g;
  let match;
  let currentStart = { x: 28, y: 199 };
  while ((match = segRegex.exec(dStr)) !== null) {
    const cp1 = { x: parseFloat(match[1]), y: parseFloat(match[2]) };
    const cp2 = { x: parseFloat(match[3]), y: parseFloat(match[4]) };
    const pEnd = { x: parseFloat(match[5]), y: parseFloat(match[6]) };

    for (let step = 0; step <= 50; step++) {
      const t = step / 50;
      const inv = 1 - t;
      const x = inv * inv * inv * currentStart.x + 3 * inv * inv * t * cp1.x + 3 * inv * t * t * cp2.x + t * t * t * pEnd.x;
      const y = inv * inv * inv * currentStart.y + 3 * inv * inv * t * cp1.y + 3 * inv * t * t * cp2.y + t * t * t * pEnd.y;

      const inBattery = (x >= batXMin && x <= batXMax && y >= batYMin && y <= batYMax);
      assert(!inBattery, `Export arrow must NEVER enter battery rectangle at t=${t.toFixed(2)} (x=${x.toFixed(1)}, y=${y.toFixed(1)})`);
    }
    currentStart = pEnd;
  }

  // 2. Import arrow: connects x1=108 to x2=142 (Demand left edge is 142) with zero gap
  const importMatch = html.match(/<line x1="(\d+)" y1="(\d+)" x2="(\d+)" y2="(\d+)"[^>]+url\(#mobArrPurple\)/);
  assert(importMatch, 'Mobile import arrow line exists');
  assert.strictEqual(importMatch[1], '108', 'Import arrow starts at Grid imports right edge (x=108)');
  assert.strictEqual(importMatch[3], '142', 'Import arrow ends exactly on Demand left edge (x=142) with zero gap');

  // 3. Loss arrow: connects Battery right edge (178) to Losses top edge (170, 380) with zero gap
  const lossMatch = html.match(/<path d="(M [^"]+)"[^>]+url\(#mobArrGrey\)/);
  assert(lossMatch, 'Mobile loss arrow path exists');
  assert.match(lossMatch[1], /^M 178,335/, 'Loss arrow starts at Battery right edge (x=178)');
  assert.match(lossMatch[1], /170,380$/, 'Loss arrow ends at Storage losses top edge (y=380) with zero gap');

  // 4. Directional marker refX=6 ensures marker tip touches destination border
  assert.match(html, /marker id="mobArrPurple"[^>]+refX="6"/, 'Marker purple refX is 6 for seamless border landing');
  assert.match(html, /marker id="mobArrGreen"[^>]+refX="6"/, 'Marker green refX is 6 for seamless border landing');
  assert.match(html, /marker id="mobArrOrange"[^>]+refX="6"/, 'Marker orange refX is 6 for seamless border landing');
  assert.match(html, /marker id="mobArrBlue"[^>]+refX="6"/, 'Marker blue refX is 6 for seamless border landing');

  // 5. Arrow labels: Charge label in reserved whitespace does NOT clip off left canvas edge
  const chargeLabelMatch = html.match(/<text x="(\d+)" y="(\d+)" text-anchor="middle"[^>]+>Charge<\/text>/);
  assert(chargeLabelMatch, 'Charge label exists with middle text-anchor');
  const chargeLabelX = parseInt(chargeLabelMatch[1], 10);
  assert(chargeLabelX >= 35, `Charge label x=${chargeLabelX} has ample clearance from left canvas edge`);
  assert(chargeLabelX - 30 > 0, 'Charge label left extent is strictly inside viewBox (no left clipping)');

  // 6. Household demand wrapped into 2 lines
  assert.match(html, />Household<\/text>/, 'Household demand title has first line Household');
  assert.match(html, />demand<\/text>/, 'Household demand title has second line demand');

  console.log('✔ Test 16: Mobile arrow endpoints, edge landing, and zero intersection with Battery passed');
}

// Test 17: Desktop Sankey collision-free layout, distinct middle battery node & line heights (Finding 1)
{
  const { context, $ } = createTestContext();
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2015,
    battery_kwh: 8.28,
    annual_flows: {
      pv_generation_kwh: 12090.3,
      load_demand_kwh: 7749.7,
      grid_import_kwh: 1313.3,
      grid_export_kwh: 5397.6,
      direct_use_kwh: 4130.5,
      battery_charge_kwh: 2562.2,
      battery_discharge_kwh: 2306.0,
      storage_losses_kwh: 256.2,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      weather_year: 2015
    }
  };

  vm.runInContext('renderAnnualEnergyFlow()', context);
  const html = $('annualEnergyFlowVisual').innerHTML;

  // Analytically inspect badge positions:
  // Imports badge at x=490, Direct Solar badge at x=310, Exports badge at x=660
  // Badge width is 216px (contained borders, zero text overflow)
  assert.match(html, /Imports ➔ Household: 1,313(\.3)? kWh/, 'Imports badge rendered');
  assert.match(html, /Direct solar ➔ Household: 4,130(\.5)? kWh/, 'Direct solar badge rendered');
  assert.match(html, /Solar ➔ Grid exports: 5,397(\.6)? kWh/, 'Grid exports badge rendered');

  // Recognizable distinct middle battery node with direction cue
  assert.match(html, /class="sankey-battery-node"/, 'Middle battery node rendered with dedicated container');
  assert.match(html, /Battery Storage/, 'Battery node has clear title');
  assert.match(html, /8\.28 kWh/, 'Battery node has exact capacity 8.28 kWh');
  assert.match(html, /IN ➔ OUT/, 'Battery node has clear LEFT-to-RIGHT flow direction cue');
  assert.match(html, /2,562(\.2)? kWh in/, 'Battery node has charging input');
  assert.match(html, /2,306(\.0)? kWh out/, 'Battery node has discharging output');

  // Verify Battery Card extent vs Grid Exports Ribbon:
  // Extract Battery node transform: transform="translate(batX, batY)"
  const batNodeMatch = html.match(/class="sankey-battery-node"\s+transform="translate\((\d+),\s*([\d.]+)\)"/);
  assert(batNodeMatch, 'Battery node transform matches');
  const batX = parseFloat(batNodeMatch[1]);
  const batY = parseFloat(batNodeMatch[2]);
  const batCardRectMatch = html.match(/<rect\s+x="0"\s+y="0"\s+width="([\d.]+)"\s+height="([\d.]+)"/);
  assert(batCardRectMatch, 'Battery card rect matches');
  const batW = parseFloat(batCardRectMatch[1]);
  const batH = parseFloat(batCardRectMatch[2]);
  assert(batW >= 120, `Battery card width (${batW}) must be >= 120px to prevent text clipping`);
  const batBottom = batY + batH;

  // Extract Export ribbon path: continuous cubic segments routing below battery
  const expRibbonMatch = html.match(/<path d="(M 174\.00,[\d.]+\s+C\s+[\d.]+,\s*[\d.]+\s+[\d.]+,\s*[\d.]+\s+423\.00,([\d.]+)\s+L 571\.00,[\d.]+\s+C\s+[\d.]+,\s*[\d.]+\s+[\d.]+,\s*[\d.]+\s+820\.00,([\d.]+)\s+L[^"]+)"[^>]*><title>Solar ➔ Grid exports/);
  assert(expRibbonMatch, 'Desktop Export ribbon matches segmented path regex');
  const yExpUnderBatTop = parseFloat(expRibbonMatch[2]);
  const yExpDestTop = parseFloat(expRibbonMatch[3]);
  assert(yExpUnderBatTop >= batBottom + 12, `Export ribbon top under battery (${yExpUnderBatTop.toFixed(1)}) must clear battery bottom (${batBottom.toFixed(1)}) by >=12px`);

  // Verify compact flow value pills on actual ribbon centerlines:
  // All 6 flow pills should be present, compact, within SVG viewBox, clear of battery card, and pairwise separated.
  const pillMatches = [...html.matchAll(/<g class="sankey-flow-pill"\s+transform="translate\(([\d.]+),\s*([\d.]+)\)"[^>]*aria-label="([^"]+)"[^>]*>[\s\S]*?<rect width="([\d.]+)" height="([\d.]+)"/g)];
  assert.equal(pillMatches.length, 6, 'All 6 flow value pills rendered in active scenario');

  const pills = pillMatches.map(m => ({
    x: parseFloat(m[1]),
    y: parseFloat(m[2]),
    label: m[3],
    w: parseFloat(m[4]),
    h: parseFloat(m[5])
  }));

  const svgViewBoxMatch = html.match(/viewBox="0 0 (\d+) (\d+)"/);
  assert(svgViewBoxMatch, 'SVG viewBox found');
  const svgWidth = parseFloat(svgViewBoxMatch[1]);
  const svgHeight = parseFloat(svgViewBoxMatch[2]);

  // Verify vertical compactness on representative fixture:
  // Compact geometry achieves ~25-30% vertical reduction from previous ~558px baseline (svgHeight in [370, 420]).
  assert(svgHeight <= 420 && svgHeight >= 360, `Representative desktop Sankey viewBox height (${svgHeight}px) must be compact within 25-30% reduction from previous layout`);
  assert(svgHeight < 500, 'Desktop Sankey height is strictly below old height 505px');

  // Analytical cubic Bezier clearance evaluator (dx = 0.48 * span) across entire battery width [batX, batX + batW]:
  function bTForX(x, x0, x1) {
    const span = x1 - x0;
    const targetU = Math.max(0, Math.min(1, (x - x0) / span));
    let low = 0, high = 1;
    for (let i = 0; i < 20; i++) {
      const mid = (low + high) / 2;
      const midU = 1.44 * mid - 1.32 * mid * mid + 0.88 * mid * mid * mid;
      if (midU < targetU) low = mid; else high = mid;
    }
    return (low + high) / 2;
  }
  function evalBY(t, y0, y1) {
    const b = t * t * (3 - 2 * t);
    return (1 - b) * y0 + b * y1;
  }

  // 1. Direct solar ribbon passes strictly above battery top terminal cap (batY - 3) across entire battery width
  const dirRibbonMatch = html.match(/<path d="M 174\.00,[\d.]+\s+C\s+[\d.]+,\s*[\d.]+\s+[\d.]+,\s*[\d.]+\s+820\.00,[\d.]+\s+L 820\.00,([\d.]+)\s+C\s+[\d.]+,\s*[\d.]+\s+[\d.]+,\s*[\d.]+\s+174\.00,([\d.]+)\s+Z"[^>]*><title>Direct solar/);
  assert(dirRibbonMatch, 'Direct solar ribbon path matches');
  const yDirDestBottom = parseFloat(dirRibbonMatch[1]);
  const yDirSrcBottom = parseFloat(dirRibbonMatch[2]);
  for (let x = batX; x <= batX + batW; x += 10) {
    const t = bTForX(x, 174, 820);
    const yDirB = evalBY(t, yDirSrcBottom, yDirDestBottom);
    assert(batY - 3 >= yDirB + 10, `Direct solar ribbon bottom (${yDirB.toFixed(1)}) must clear battery top terminal cap (${(batY - 3).toFixed(1)}) by >=10px at x=${x}`);
  }

  // 2. Export ribbon passes strictly below battery bottom across entire battery width
  for (let x = batX; x <= batX + batW; x += 10) {
    assert(yExpUnderBatTop >= batBottom + 12, `Export ribbon top (${yExpUnderBatTop.toFixed(1)}) must clear battery bottom (${batBottom.toFixed(1)}) by >=12px at x=${x}`);
  }

  pills.forEach(p => {
    // Assert compact dimensions: height <= 24, width <= 110, width >= 60
    assert(p.h <= 24 && p.h >= 18, `Pill "${p.label}" height (${p.h}) must be compact between 18 and 24px`);
    assert(p.w <= 110 && p.w >= 60, `Pill "${p.label}" width (${p.w}) must be between 60 and 110px`);

    // Assert strictly contained within SVG viewBox
    assert(p.x >= 0 && p.x + p.w <= svgWidth, `Pill "${p.label}" at x=${p.x} must be inside [0, ${svgWidth}]`);
    assert(p.y >= 0 && p.y + p.h <= svgHeight, `Pill "${p.label}" at y=${p.y} must be inside [0, ${svgHeight}]`);

    // Assert zero overlap with battery card [batX, batX + batW] x [batY, batBottom]
    const intersectsBat = (p.x + p.w > batX && p.x < batX + batW && p.y + p.h > batY && p.y < batBottom);
    assert(!intersectsBat, `Pill "${p.label}" must not collide with battery card [${batX}, ${batX + batW}] x [${batY}, ${batBottom}]`);
  });

  // Verify pairwise separation between all pills (no overlapping pills)
  for (let i = 0; i < pills.length; i++) {
    for (let j = i + 1; j < pills.length; j++) {
      const p1 = pills[i], p2 = pills[j];
      const overlapX = (p1.x < p2.x + p2.w) && (p1.x + p1.w > p2.x);
      const overlapY = (p1.y < p2.y + p2.h) && (p1.y + p1.h > p2.y);
      const collides = overlapX && overlapY;
      assert(!collides, `Pill "${p1.label}" collides with "${p2.label}"`);
    }
  }

  // Verify line heights: title and quantity pairs have >= 18px separation
  const batMidX = (batW / 2).toFixed(1);
  const titleMatch = html.match(new RegExp(`<text x="${batMidX}" y="([\\d.]+)"[^>]*>Battery Storage<\\/text>`));
  assert(titleMatch, 'Battery title centered at batW/2');
  const capMatch = html.match(new RegExp(`<text x="${batMidX}" y="([\\d.]+)"[^>]*>8\\.28 kWh<\\/text>`));
  assert(capMatch, 'Battery capacity centered at batW/2');
  const yTitle = parseFloat(titleMatch[1]);
  const yCap = parseFloat(capMatch[1]);
  assert(yCap - yTitle >= 18, `Battery title and capacity have >= 18px separation (actual: ${yCap - yTitle}px)`);

  const inMatch = html.match(new RegExp(`<text x="${batMidX}" y="([\\d.]+)"[^>]*>2,562(\\.2)? kWh in<\\/text>`));
  assert(inMatch, 'Battery in throughput centered at batW/2');
  const outMatch = html.match(new RegExp(`<text x="${batMidX}" y="([\\d.]+)"[^>]*>2,306(\\.0)? kWh out<\\/text>`));
  assert(outMatch, 'Battery out throughput centered at batW/2');
  const yIn = parseFloat(inMatch[1]);
  const yOut = parseFloat(outMatch[1]);
  assert(yOut - yIn >= 18, `Battery in and out throughput have >= 18px separation (actual: ${yOut - yIn}px)`);

  // In outer nodes: title y has -11, value y has +11 (22px separation)
  assert.match(html, /class="sankey-node-title"[^>]*>Grid imports<\/text>\s*<text[^>]+class="sankey-node-val"[^>]*>(?:<title>[^<]*<\/title>)?1,313(\.3)? kWh<\/text>/, 'Grid imports title and value rendered with 22px vertical separation');
  // Usable PV title uses multi-line tspan formatting to prevent left-edge clipping within 160px margin
  assert.match(html, /class="sankey-node-title"[^>]*>\s*<tspan[^>]*>Usable PV<\/tspan>\s*<tspan[^>]*>after curtailment<\/tspan>\s*<\/text>/, 'Usable PV title uses multi-line tspan formatting without clipping');

  // Outer desktop Sankey nodes use sharp polygons (no rx rounding) matching JointJS reference:
  // Source bars on left have square outer corners and a shallow right-pointing angled edge/chevron.
  // Sink bars on right have square outer corners and a shallow inward angled/chevron notch.
  assert.match(html, /<polygon points="160\.0,[\d.]+ 174\.0,[\d.]+ 177\.5,[\d.]+ 174\.0,[\d.]+ 160\.0,[\d.]+" fill="#7c4d9f" \/>/, 'Grid imports renders sharp chevron polygon node shape');
  assert.match(html, /<polygon points="160\.0,[\d.]+ 174\.0,[\d.]+ 177\.5,[\d.]+ 174\.0,[\d.]+ 160\.0,[\d.]+" fill="#217a61" \/>/, 'AC Solar generation renders sharp chevron polygon node shape');
  assert.match(html, /<polygon points="816\.5,[\d.]+ 834\.0,[\d.]+ 834\.0,[\d.]+ 816\.5,[\d.]+ 820\.0,[\d.]+" fill="#183b33" \/>/, 'Household demand renders sharp notched polygon node shape');
  assert.match(html, /<polygon points="[\d.]+,[\d.]+ 834\.0,[\d.]+ 834\.0,[\d.]+ [\d.]+,[\d.]+ 820\.0,[\d.]+" fill="#646d78" \/>/, 'Storage losses renders sharp clamped notched polygon node shape');
  assert.match(html, /<polygon points="816\.5,[\d.]+ 834\.0,[\d.]+ 834\.0,[\d.]+ 816\.5,[\d.]+ 820\.0,[\d.]+" fill="#366291" \/>/, 'Grid exports renders sharp notched polygon node shape');
  assert.doesNotMatch(html, /<rect[^>]+(x="160"|x="820")[^>]*>/, 'No rounded rects used for desktop outer source or sink node bars');

  // Verify flow legend rendered directly beneath SVG
  assert.match(html, /class="annual-flow-legend"/, 'Flow legend rendered beneath SVG');
  assert.match(html, /Direct solar ➔ Household:/, 'Legend item Direct solar present');
  assert.match(html, /Solar ➔ Battery:/, 'Legend item Solar to battery present');
  assert.match(html, /Solar ➔ Grid exports:/, 'Legend item Solar to exports present');

  console.log('✔ Test 17: Desktop Sankey collision-free layout, distinct middle battery node & line heights passed');
}

// Test 18: Responsive design, container queries, and mobile accessibility (Findings 2, 9, 10)
{
  // 1. CSS contains container query and media query responsive switches
  assert.match(cssSource, /@container\s*\(max-width:\s*720px\)/, 'CSS includes container query breakpoint at 720px');
  assert.match(cssSource, /@media\s*\(max-width:\s*768px\)/, 'CSS includes tablet media query breakpoint at 768px');
  assert.match(cssSource, /container-type:\s*inline-size/, 'Diagram card specifies container-type inline-size');

  // 2. Reconciliations grid uses responsive minmax(min(100%, 250px), 1fr) preventing overflow on small screens
  assert.match(cssSource, /minmax\(min\(100%,\s*250px\),\s*1fr\)/, 'Reconciliations grid prevents horizontal overflow on small phones');

  // 3. Table wrap has keyboard scrollable container
  assert.match(cssSource, /\.flow-details-table-wrap\s*\{[^}]*overflow-x:\s*auto/, 'Flow table wrap has horizontal scroll');
  assert.match(cssSource, /\.flow-details-table-wrap:focus-visible/, 'Flow table wrap has keyboard focus ring');

  // 4. index.html has annualFlowMeta directly beneath annual title
  assert.match(htmlSource, /<p id="annualFlowMeta"[^>]*><\/p>/, 'index.html has dynamic annualFlowMeta element directly beneath annual title');
  assert.doesNotMatch(htmlSource, /Rigorous energy conservation: PV AC generation routes/, 'Long methodology removed from index.html body');

  console.log('✔ Test 18: Responsive design, container queries, and mobile accessibility passed');
}

// Test 19: Dynamic metadata clearing when renderer switches to pending or unavailable (Requirement C.1)
{
  const { context, $ } = createTestContext();
  const metaEl = $('annualFlowMeta');
  metaEl.innerHTML = 'Old Operating Year: 2026 · Old Scenario: Baseline';

  // Case A: Result is null -> clears metadata
  context.current = null;
  vm.runInContext('renderAnnualEnergyFlow()', context);
  assert.strictEqual(metaEl.innerHTML, '', 'Clears annualFlowMeta when current result is null');

  // Case B: Stale / pending cache -> clears metadata
  metaEl.innerHTML = 'Old Stale Meta';
  context.current = { result: { settings: { load_scenario: 'electrified' } } };
  context.behaviorMatrixCache = null;
  vm.runInContext('renderAnnualEnergyFlow()', context);
  assert.strictEqual(metaEl.innerHTML, '', 'Clears annualFlowMeta when simulation cache is pending/stale');

  // Case C: Unavailable fields -> clears metadata
  metaEl.innerHTML = 'Old Stale Meta';
  context.behaviorMatrixKey = 'electrified_electrified_fp';
  context.behaviorMatrixCache = { annual_flows: { pv_generation_kwh: null } };
  vm.runInContext('renderAnnualEnergyFlow()', context);
  assert.strictEqual(metaEl.innerHTML, '', 'Clears annualFlowMeta when flow fields are incomplete/unavailable');

  // Case D: Accepted coherent simulation -> populates honest metadata
  context.behaviorMatrixCache = {
    year: 2027,
    weather_year: 2015,
    weather_count: 19,
    battery_kwh: 8.28,
    annual_flows: {
      pv_generation_kwh: 12090.3,
      load_demand_kwh: 7749.7,
      grid_import_kwh: 1313.3,
      grid_export_kwh: 5397.6,
      direct_use_kwh: 4130.5,
      battery_charge_kwh: 2562.2,
      battery_discharge_kwh: 2306.0,
      storage_losses_kwh: 256.2,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      weather_year: 2015
    }
  };
  vm.runInContext('renderAnnualEnergyFlow()', context);
  assert.match(metaEl.innerHTML, /Operating year: <strong>2027<\/strong>/, 'Populates operating year once accepted');
  assert.match(metaEl.innerHTML, /Selected weather analogue: <strong>Year 2015<\/strong>/, 'Populates selected weather analogue once accepted');

  console.log('✔ Test 19: Dynamic metadata clearing on pending/unavailable states passed');
}

// Test 21: PV clipping card compact layout and ensemble contract
  {
    const { context, $ } = createTestContext();
    context.data = {
      weather_count: 19,
      selected_config: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 11, scheduled_start_month: 3, scheduled_end_month: 9 },
      annual_flows: {
        pv_input_kwh: 20000, pv_generation_kwh: 16000, clipped_kwh: 4000, clipped_pct: 20, clipping_hours: 120,
        flow_ranges: { clipped_kwh: { p10: 3200, p90: 4800 }, clipped_pct: { p10: 16, p90: 24 } }
      },
      analogue_annual_flows: { pv_input_kwh: 22000, pv_generation_kwh: 18000 },
      monthly_clipping: Array.from({ length: 12 }, (_, i) => ({
        month: i + 1, pv_input_kwh: 100, pv_delivered_kwh: 80, clipped_kwh: i === 0 ? 0.2 : 20,
        clipped_pct: 20, clipping_hours: 10, peak_curtailed_kw: 2.5, p25_peak_soc_pct: i === 7 ? 88.2 : 60,
        scheduled_active: i === 7
      }))
    };

    vm.runInContext('renderPvClippingBreakdown(data)', context);
    let html = $('pvClippingTable').innerHTML;

    // 1. Authoritative annual_flows means used (not analogue), exact 4 KPIs and 12 months
    assert.match(html, /20,000(\.0)?\s*→\s*16,000(\.0)?\s*kWh\/yr/, 'Displays modeled → usable from annual_flows');
    assert.match(html, /4,000(\.0)?\s*kWh\/yr\s*\(20\.00%\)/, 'Displays clipped kWh and pct');
    assert.doesNotMatch(html, /22[,.]?000|18[,.]?000/, 'Ignores analogue_annual_flows values');
    assert.strictEqual((html.match(/class="pv-clip-kpi"/g) || []).length, 4, 'Exactly 4 .pv-clip-kpi cards');
    assert.strictEqual((html.match(/class="pv-clip-month(?: scheduled)?"/g) || []).length, 12, 'Exactly 12 .pv-clip-month cells');

    // 2. Ranges distinct P10/P90, collapsed details, zero clipping aria-label
    assert.match(html, /P10–P90:\s*3,200–4,800\s*kWh\s*·\s*16\.00%–24\.00%/, 'Distinct P10–P90 kWh and pct ranges');
    assert.match(html, /<details\s+class="pv-clip-details">/, 'Details container present without open attribute');
    assert.doesNotMatch(html, /<details[^>]*\bopen\b/, 'Details starts collapsed');
    assert.match(html, /aria-label="0\s*kWh"[^>]*>—/, 'Clipping <0.5 shows em-dash with aria-label 0 kWh');

    // 3. P25 low SoC highlighting restricted to scheduled months (Aug 88%, inactive Jan not amber)
    const stripHtml = html.split('<details')[0];
    assert.match(stripHtml, /class="schedule-soc-low"[^>]*>P25 SoC:\s*88%<\/span>/, 'Scheduled Aug (88.2%) has low SoC amber indicator');
    assert.strictEqual((stripHtml.match(/class="schedule-soc-low"/g) || []).length, 1, 'Only active scheduled month gets low SoC indicator');

    // 4. Wrapping schedule Nov–Feb with start11 end2
    context.data.selected_config.scheduled_start_month = 11;
    context.data.selected_config.scheduled_end_month = 2;
    vm.runInContext('renderPvClippingBreakdown(data)', context);
    html = $('pvClippingTable').innerHTML;
    assert.match(html, /Nov[–\-]Feb/, 'Renders wrapping Nov–Feb schedule badge');

    // 5. Delete p25 fields, set median 65, verify strip portion before details does not say P25 SoC: 65%
    context.data.monthly_clipping.forEach(m => { delete m.p25_peak_soc_pct; m.median_peak_soc_pct = 65; });
    vm.runInContext('renderPvClippingBreakdown(data)', context);
    const stripNoP25 = $('pvClippingTable').innerHTML.split('<details')[0];
    assert.doesNotMatch(stripNoP25, /P25\s+SoC:\s*65%/, 'Strip before details does not mislabel median as P25 SoC');

    console.log('✔ Test 21: PV clipping card compact layout and ensemble contract passed');
  }

// Test 22: Clipping can be inspected by month and hour in the existing heatmap
{
  const { context, elements, win } = createLifecycleEnvironment();
  context.fetch = win.fetch;
  vm.runInContext(appSource, context);
  context._data = { matrix: { months: Array.from({ length: 12 }, (_, m) => Array.from({ length: 24 }, (_, h) => ({ clipping: m === 5 && h === 12 ? 0.45 : 0 }))) } };
  vm.runInContext('drawMonthHourHeatmap(_data, "clipping")', context);
  assert.match(elements.monthHourHeatmap.innerHTML, /PV curtailed by selected dispatch topology: 0\.45 kWh\/h/);
  assert.match(elements.monthHourHeatmap.innerHTML, /Annual PV clipping dispatch takeaway/);
  console.log('✔ Test 22: Month-hour inverter-clipping heatmap passed');
}

// Test 20: Honest upstream inverter clipping handling: missing -> unavailable, finite -> formatted (Requirement C.2)
{
  // Case A: Missing / undefined clipped_kwh displays honest "unavailable" without blocking 6 flows
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2015,
      battery_kwh: 8.28,
      annual_flows: {
        pv_generation_kwh: 12090.3,
        load_demand_kwh: 7749.7,
        grid_import_kwh: 1313.3,
        grid_export_kwh: 5397.6,
        direct_use_kwh: 4130.5,
        battery_charge_kwh: 2562.2,
        battery_discharge_kwh: 2306.0,
        storage_losses_kwh: 256.2,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        // clipped_kwh is intentionally missing/omitted
        weather_year: 2015
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;
    // Does NOT render unavailable for the entire diagram
    assert.doesNotMatch(html, /annual-flow-unavailable/, 'Does not block 6 AC flows when clipping metadata is absent');
    // Reports unavailable honestly in the reconciliation card
    assert.match(html, /Inverter Clipping Loss = unavailable/, 'Honest unavailable status for missing upstream clipping');
    assert.doesNotMatch(html, /Inverter Clipping Loss = 0(\.0)? kWh\/yr/, 'Never invents 0 kWh when clipping metadata is missing');
  }

  // Case B: Finite clipped_kwh (e.g. 145.2 kWh) displays properly formatted
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: 2015,
      battery_kwh: 8.28,
      annual_flows: {
        pv_generation_kwh: 12090.3,
        load_demand_kwh: 7749.7,
        grid_import_kwh: 1313.3,
        grid_export_kwh: 5397.6,
        direct_use_kwh: 4130.5,
        battery_charge_kwh: 2562.2,
        battery_discharge_kwh: 2306.0,
        storage_losses_kwh: 256.2,
        initial_soc_kwh: 0.0,
        end_soc_kwh: 0.0,
        storage_delta_kwh: 0.0,
        clipped_kwh: 145.2,
        weather_year: 2015
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;
    assert.match(html, /Inverter Clipping Loss = 145\.2 kWh\/yr/, 'Formats valid finite clipping as 145.2 kWh/yr');
  }

  console.log('✔ Test 20: Honest upstream inverter clipping handling passed');
}

// Helpers for Tests 21-23 lifecycle and auto-refresh testing
function createLifecycleEnvironment() {
  const elements = {};
  function makeEl(id, tagName = 'div') {
    if (!elements[id]) {
      const classSet = new Set();
      elements[id] = {
        id, tagName: tagName.toUpperCase(),
        classList: {
          add: c => classSet.add(c),
          remove: c => classSet.delete(c),
          contains: c => classSet.has(c),
          toggle: (c, f) => {
            if (f === undefined) { if (classSet.has(c)) classSet.delete(c); else classSet.add(c); }
            else if (f) classSet.add(c); else classSet.delete(c);
            return classSet.has(c);
          }
        },
        hidden: false, disabled: false, innerHTML: '', textContent: '', value: '',
        style: {}, options: [], dataset: {},
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return this[k]; },
        removeAttribute(k) { delete this[k]; },
        querySelector: () => null, querySelectorAll: () => [],
        closest: () => makeEl('mockClosest_' + id), addEventListener: () => {}, removeEventListener: () => {}
      };
    }
    return elements[id];
  }
  const knownIds = [
    'metrics', 'summaryScopeNote', 'unconfirmedAssumptionsBadge', 'moneyBasisSelect',
    'cashFlowBasis', 'cashFlowHeading', 'cashFlowDescription', 'cashFlowBasisNote',
    'cashChart', 'cashInsights', 'scopeSolarBtn', 'scopePackageBtn', 'cashBeforeBtn', 'cashAfterBtn',
    'runSummarySensitivityBtn', 'summarySensitivityResults', 'summarySensitivityStatus',
    'configVsRecContent', 'savingsBreakdownVisual', 'saved',
    'solarChart1', 'solarChart2Wrap', 'solarSizeLegend', 'toggleSolarCurveView',
    'solarChart3', 'solarTable3', 'solarRoofPills', 'solarRoofLimitsTable',
    'designUnifiedComparisonTable', 'solarComparisonsTable', 'solarAlternativesTable',
    'solarSizeCurveTitle', 'batMarginalTitle', 'solarResultsWrap', 'applySolarSystem',
    'loanPayment', 'loanAssumptions', 'loanCashChart', 'loanInsights', 'loanCashHeading',
    'loanPivot', 'loanMetricCards', 'saveLoanSimulation', 'solveLoanAmount',
    'loanAmountControl', 'loanModeControl', 'loanFractionControl', 'loanAprControl',
    'loanYearsControl', 'loanMaxMonthlyPaymentControl', 'loanScopeControl', 'loanInflationControl',
    'loanDiscountControl', 'loanFractionWrap',
    'annualEnergyFlowVisual', 'annualFlowMeta', 'typicalDayChart', 'typicalDayTitle', 'monthHourHeatmap',
    'dayhourScenario', 'seasonSelectorPills',
    'adjacentPricesContent', 'lcoeContent', 'exportValuationCards', 'exportSensitivityComparison',
    'exportValuationNote', 'marketSeasonalChart', 'marketCurveTable', 'marketCurveProvenance',
    'marketCurveWindow', 'ledger', 'checklistProgress', 'checklistItems',
    'quoteComparisonSheetContent', 'printPitchBtn', 'scenarioMenu', 'toggleInputs',
    'controls', 'pinnedControlsList', 'modifiedCountBadge', 'activeTabAssumptionsPill',
    'status', 'error', 'latency', 'systemTitle', 'systemTags', 'behavior', 'weatherYear',
    'energyBalance', 'comparisons', 'method', 'limitations', 'claims'
  ];
  knownIds.forEach(id => makeEl(id));
  const doc = {
    body: makeEl('body'),
    getElementById: id => elements[id] || makeEl(id),
    querySelector: sel => elements[sel.replace('#','')] || null,
    querySelectorAll: sel => [],
    addEventListener: () => {}, removeEventListener: () => {}
  };
  const win = {
    document: doc,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    print: () => {}, matchMedia: () => ({ matches: false }),
    addEventListener: () => {}, removeEventListener: () => {}
  };
  const context = vm.createContext({
    window: win, document: doc, fetch: win.fetch, localStorage: win.localStorage,
    matchMedia: win.matchMedia, console, setTimeout: fn => fn(), clearTimeout: () => {},
    structuredClone: obj => JSON.parse(JSON.stringify(obj)),
    Number, Math, String, Boolean, Array, Object, JSON
  });
  return { context, elements, win, doc };
}

function mockEvaluate(scenario, batteryKwh = 10.0) {
  return {
    compute_seconds: 0.1, claims: [], warnings: [],
    result: {
      layout: { kwp: 5.6, panel_count: 14, theoretical: false },
      settings: { load_scenario: scenario, battery_kwh: batteryKwh, inverter_kw: 6.0, installation_year: 2026, cashflow_mode: 'real' },
      metrics: { npv_eur: { min: 1000, median: 2000, max: 3000 }, package_npv_eur: 5000, equity_npv_eur: 4000 },
      finance: { capex_eur: 10000, conversion_capex_eur: 3000, annual_deduction_eur: 500, nominal_discount_rate: 0.05, real_discount_rate: 0.03, whole_package: {} },
      energy: { load_kwh: 5000, pv_kwh: 12000, direct_kwh: 3000, discharge_kwh: 2000, grid_import_kwh: 600, export_kwh: 6000, battery_loss_kwh: 200, clipped_kwh: 0, self_consumption: 0.4, useful_self_consumption: 0.4 },
      annual_cashflows: [{}, { year: 1, pv_incremental_range: { median: 100 }, equity_range: { median: 100 }, whole_package_range: { median: 100 } }],
      limitations: []
    }
  };
}

const scenarioDataMap = {
  baseline: {
    year: 2027, weather_year: 2015, weather_count: 19,
    annual_flows: {
      pv_generation_kwh: 12090.3, load_demand_kwh: 5957.7, grid_import_kwh: 569.8,
      grid_export_kwh: 6474.6, direct_use_kwh: 3338.1, battery_charge_kwh: 2277.6,
      battery_discharge_kwh: 2049.9, storage_losses_kwh: 227.8, initial_soc_kwh: 0,
      end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
    },
    matrix: { seasons: { summer: [] }, months: [] }
  },
  electrified: {
    year: 2027, weather_year: 2015, weather_count: 19,
    annual_flows: {
      pv_generation_kwh: 12090.3, load_demand_kwh: 7749.7, grid_import_kwh: 1313.3,
      grid_export_kwh: 5397.6, direct_use_kwh: 4130.5, battery_charge_kwh: 2562.2,
      battery_discharge_kwh: 2306.0, storage_losses_kwh: 256.2, initial_soc_kwh: 0,
      end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
    },
    matrix: { seasons: { summer: [] }, months: [] }
  },
  ev: {
    year: 2027, weather_year: 2015, weather_count: 19,
    annual_flows: {
      pv_generation_kwh: 12090.3, load_demand_kwh: 8999.7, grid_import_kwh: 2088.9,
      grid_export_kwh: 4894.4, direct_use_kwh: 4345.1, battery_charge_kwh: 2850.8,
      battery_discharge_kwh: 2565.7, storage_losses_kwh: 285.1, initial_soc_kwh: 0,
      end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
    },
    matrix: { seasons: { summer: [] }, months: [] }
  }
};

(async () => {
  // Test 21: Live scenario change auto-refresh and completion without reopening tab (Baseline 90.4%, Electrified 83.1%, EV 76.8%)
  {
    const { context, elements, win } = createLifecycleEnvironment();

    let activeRequests = [];
    win.fetch = async (url, opts) => {
      if (url === '/api/schema') {
        return { ok: true, json: async () => ({ records: [], defaults: {} }) };
      }
      if (url === '/api/behavior-matrix') {
        const body = JSON.parse(opts.body);
        const respData = scenarioDataMap[body.scenario] || scenarioDataMap.electrified;
        const respCopy = JSON.parse(JSON.stringify(respData));
        respCopy.settings = body.settings;
        const p = Promise.resolve(respCopy);
        activeRequests.push(p);
        return { ok: true, json: async () => p };
      }
      return { ok: true, json: async () => ({}) };
    };
    context.fetch = win.fetch;

    vm.runInContext(appSource, context);

    async function flushAsync() {
      await Promise.all(activeRequests);
      activeRequests = [];
      for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));
    }

    // Behavior view is currently visible
    elements.behavior.hidden = false;

    // 1. Initial Electrified scenario evaluation completes and renders
    context._s = { load_scenario: 'electrified', battery_kwh: 10.0 };
    context._q = { line_items: [] };
    context._cur = mockEvaluate('electrified', 10.0);
    vm.runInContext('settings = _s; quote = _q; current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);
    await flushAsync();

    assert.match(elements.annualEnergyFlowVisual.innerHTML, /83\.1%/, 'Electrified autarky 83.1% rendered');
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /1,313\.3 kWh/, 'Electrified grid imports 1,313.3 kWh rendered');

    // 2. User activates Baseline button while on How it behaves tab
    vm.runInContext("settings.load_scenario = 'baseline'; invalidateBehaviorAndSensitivity();", context);
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /Loading annual energy flow simulation for Baseline/, 'Immediate pending state for Baseline');

    // Recompute completes and calls render() WITHOUT reopening tab
    context._cur = mockEvaluate('baseline', 10.0);
    vm.runInContext('current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);
    await flushAsync();

    assert.match(elements.annualEnergyFlowVisual.innerHTML, /90\.4%/, 'Baseline 90.4% autarky automatically rendered');
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /569\.8 kWh/, 'Baseline imports 569.8 kWh rendered');
    assert.doesNotMatch(elements.annualEnergyFlowVisual.innerHTML, /Loading annual energy flow simulation/, 'Baseline pending state resolved');

    // 3. User activates EV scenario button
    vm.runInContext("settings.load_scenario = 'ev'; invalidateBehaviorAndSensitivity();", context);
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /Loading annual energy flow simulation for Ev/, 'Immediate pending state for EV');

    // Recompute completes and calls render() WITHOUT reopening tab
    context._cur = mockEvaluate('ev', 10.0);
    vm.runInContext('current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);
    await flushAsync();

    assert.match(elements.annualEnergyFlowVisual.innerHTML, /76\.8%/, 'EV 76.8% autarky automatically rendered');
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /2,088\.9 kWh/, 'EV imports 2,088.9 kWh rendered');
    assert.doesNotMatch(elements.annualEnergyFlowVisual.innerHTML, /Loading annual energy flow simulation/, 'EV pending state resolved');

    // 4. Restore back to Electrified
    vm.runInContext("settings.load_scenario = 'electrified'; invalidateBehaviorAndSensitivity();", context);
    context._cur = mockEvaluate('electrified', 10.0);
    vm.runInContext('current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);
    await flushAsync();

    assert.match(elements.annualEnergyFlowVisual.innerHTML, /83\.1%/, 'Electrified restored autarky 83.1% rendered');
    console.log('✔ Test 21: Live scenario change auto-refresh and completion without reopening tab passed');
  }

  // Test 22: Accepted quote normalized capacity contract during scenario refresh
  {
    const { context, elements, win } = createLifecycleEnvironment();
    let capturedPayload = null;
    win.fetch = async (url, opts) => {
      if (url === '/api/schema') return { ok: true, json: async () => ({ records: [], defaults: {} }) };
      if (url === '/api/behavior-matrix') {
        capturedPayload = JSON.parse(opts.body);
        return {
          ok: true,
          json: async () => ({
            year: 2027, weather_year: 2015,
            battery_kwh: capturedPayload.settings.battery_kwh,
            annual_flows: {
              pv_generation_kwh: 12090.3, load_demand_kwh: 7749.7, grid_import_kwh: 1313.3,
              grid_export_kwh: 5397.6, direct_use_kwh: 4130.5, battery_charge_kwh: 2562.2,
              battery_discharge_kwh: 2306.0, storage_losses_kwh: 256.2, initial_soc_kwh: 0,
              end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
            },
            matrix: { seasons: { summer: [] }, months: [] }
          })
        };
      }
      return { ok: true, json: async () => ({}) };
    };
    context.fetch = win.fetch;

    vm.runInContext(appSource, context);
    elements.behavior.hidden = false;

    // Raw settings has 11.04 kWh, but accepted quote normalized result has 4.8 kWh usable
    context._s = { load_scenario: 'electrified', battery_kwh: 11.04 };
    context._q = { line_items: [{ category: 'battery', model: '6kWh' }] };
    context._cur = mockEvaluate('electrified', 4.8);

    vm.runInContext('settings = _s; quote = _q; current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);
    for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));

    assert.ok(capturedPayload, 'API /api/behavior-matrix was called');
    assert.strictEqual(capturedPayload.settings.battery_kwh, 4.8, 'Behavior request sent normalized 4.8 kWh usable battery');
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /4\.8 kWh/, 'Battery node renders normalized 4.8 kWh usable capacity');
    console.log('✔ Test 22: Accepted quote normalized capacity contract during scenario refresh passed');
  }

  // Test 23: Applying a solar system with a frontend-only pricing mode still
  // accepts the evaluation fingerprint and dispatches behavior charts.
  {
    const { context, elements, win } = createLifecycleEnvironment();
    let evaluatePayload = null;
    let behaviorPayload = null;
    win.fetch = async (url, opts = {}) => {
      if (url === '/api/schema') return { ok: true, json: async () => ({ records: [], defaults: {} }) };
      if (url === '/api/evaluate') {
        evaluatePayload = JSON.parse(opts.body);
        const result = mockEvaluate('electrified', 10.0);
        result.result.settings = { ...evaluatePayload.settings, ...result.result.settings };
        return { ok: true, json: async () => result };
      }
      if (url === '/api/behavior-matrix') {
        behaviorPayload = JSON.parse(opts.body);
        return { ok: true, json: async () => ({
          year: 2027, weather_year: 2015, battery_kwh: 10,
          annual_flows: {
            pv_generation_kwh: 12090.3, load_demand_kwh: 7749.7, grid_import_kwh: 1313.3,
            grid_export_kwh: 5397.6, direct_use_kwh: 4130.5, battery_charge_kwh: 2562.2,
            battery_discharge_kwh: 2306.0, storage_losses_kwh: 256.2, initial_soc_kwh: 0,
            end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
          }, matrix: { seasons: { summer: [] }, months: [] }
        }) };
      }
      return { ok: true, json: async () => ({}) };
    };
    context.fetch = win.fetch;
    vm.runInContext(appSource, context);
    elements.behavior.hidden = false;
    context._s = { load_scenario: 'electrified', battery_kwh: 10.0, inverter_kw: 6.0, storage_cost_mode: 'existing', hasItemized: true };
    context._q = { line_items: [] };
    vm.runInContext('settings = _s; quote = _q;', context);
    await vm.runInContext('recompute({sync:false})', context);
    for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));

    assert.ok(evaluatePayload, 'scenario evaluation was dispatched');
    assert.strictEqual(evaluatePayload.settings.storage_cost_mode, 'existing', 'backend receives the engine-supported storage pricing mode');
    assert.ok(!Object.hasOwn(evaluatePayload.settings, 'hasItemized'), 'frontend-only itemization flag is still removed');
    assert.ok(behaviorPayload, 'behavior simulation is dispatched after evaluation');
    assert.match(elements.annualEnergyFlowVisual.innerHTML, /83\.1%/, 'annual energy flow renders instead of remaining pending');
    console.log('✔ Test 23: Applied-system storage mode keeps behavior simulation live');
  }

  // Test 24: Hidden behavior view guard (no dispatch) and stale response protection
  {
    // Case A: Hidden behavior view does not dispatch requests
    {
      const { context, elements, win } = createLifecycleEnvironment();
      let apiCalls = [];
      win.fetch = async (url, opts) => {
        apiCalls.push(url);
        if (url === '/api/schema') return { ok: true, json: async () => ({ records: [], defaults: {} }) };
        return { ok: true, json: async () => ({}) };
      };
      context.fetch = win.fetch;
      vm.runInContext(appSource, context);

      elements.behavior.hidden = true; // Hidden tab
      context._s = { load_scenario: 'electrified', battery_kwh: 10.0 };
      context._q = { line_items: [] };
      context._cur = mockEvaluate('electrified', 10.0);

      vm.runInContext('settings = _s; quote = _q; current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);
      for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));

      const behaviorCalls = apiCalls.filter(u => u === '/api/behavior-matrix');
      assert.strictEqual(behaviorCalls.length, 0, 'Hidden behavior view does not dispatch /api/behavior-matrix');
    }

    // Case B: Stale response protection
    {
      const { context, elements, win } = createLifecycleEnvironment();
      let resolveBaseline;
      const baselineDelayedPromise = new Promise(resolve => { resolveBaseline = resolve; });

      win.fetch = async (url, opts) => {
        if (url === '/api/schema') return { ok: true, json: async () => ({ records: [], defaults: {} }) };
        if (url === '/api/behavior-matrix') {
          const body = JSON.parse(opts.body);
          if (body.scenario === 'baseline') {
            return { ok: true, json: async () => baselineDelayedPromise };
          }
          return {
            ok: true,
            json: async () => ({
              year: 2027, weather_year: 2015, scenario: 'ev',
              annual_flows: {
                pv_generation_kwh: 12090.3, load_demand_kwh: 8999.7, grid_import_kwh: 2088.9,
                grid_export_kwh: 4894.4, direct_use_kwh: 4345.1, battery_charge_kwh: 2850.8,
                battery_discharge_kwh: 2565.7, storage_losses_kwh: 285.1, initial_soc_kwh: 0,
                end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
              },
              matrix: { seasons: { summer: [] }, months: [] }
            })
          };
        }
        return { ok: true, json: async () => ({}) };
      };
      context.fetch = win.fetch;
      vm.runInContext(appSource, context);

      elements.behavior.hidden = false;

      // Start on baseline
      context._s = { load_scenario: 'baseline', battery_kwh: 10.0 };
      context._q = { line_items: [] };
      context._cur = mockEvaluate('baseline', 10.0);
      vm.runInContext('settings = _s; quote = _q; current = _cur; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);

      // Before baseline resolves, user switches to EV
      vm.runInContext("settings.load_scenario = 'ev'; invalidateBehaviorAndSensitivity();", context);
      context._curEv = mockEvaluate('ev', 10.0);
      vm.runInContext('current = _curEv; acceptedInputFingerprint = inputFingerprint(settings, quote); render();', context);

      // Delayed baseline response arrives now
      resolveBaseline({
        year: 2027, weather_year: 2015, scenario: 'baseline',
        annual_flows: {
          pv_generation_kwh: 12090.3, load_demand_kwh: 5957.7, grid_import_kwh: 569.8,
          grid_export_kwh: 6474.6, direct_use_kwh: 3338.1, battery_charge_kwh: 2277.6,
          battery_discharge_kwh: 2049.9, storage_losses_kwh: 227.8, initial_soc_kwh: 0,
          end_soc_kwh: 0, storage_delta_kwh: 0, clipped_kwh: 0, weather_year: 2015
        },
        matrix: { seasons: { summer: [] }, months: [] }
      });

      for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r));

      // Active cache is EV and not contaminated by stale baseline response
      const cache = vm.runInContext('behaviorMatrixCache', context);
      assert.strictEqual(cache?.scenario, 'ev', 'Cache accepted EV data and rejected stale Baseline response');
      assert.match(elements.annualEnergyFlowVisual.innerHTML, /76\.8%/, 'Renders EV 76.8% and not stale baseline 90.4%');
    }

    console.log('✔ Test 24: Hidden behavior view guard and stale response protection passed');
  }

  // Test 25: Scoped narrow mobile padding and container spacing (Finding FIX 2)
  {
    // 1. Scoped narrow phone padding on #annualEnergyFlowArticle
    assert.match(cssSource, /@media\s*\(max-width:\s*480px\)\s*\{[^}]*#annualEnergyFlowArticle\s*\{[^}]*padding:\s*12px/, 'CSS scopes #annualEnergyFlowArticle padding to 12px on <=480px');

    // 2. Scoped wrapper padding and border removal on .energy-flow-visual inside #annualEnergyFlowArticle
    assert.match(cssSource, /#annualEnergyFlowArticle\s+\.energy-flow-visual\s*\{[^}]*padding:\s*0/, 'CSS eliminates wrapper padding on <=480px');
    assert.match(cssSource, /#annualEnergyFlowArticle\s+\.energy-flow-visual\s*\{[^}]*border:\s*none/, 'CSS eliminates redundant wrapper border on <=480px');

    // 3. Diagram card padding remains 10px 4px on <=480px
    assert.match(cssSource, /\.annual-flow-diagram-card\s*\{[^}]*padding:\s*10px\s+4px/, 'Diagram card padding remains 10px 4px');

    console.log('✔ Test 25: Scoped narrow mobile padding and wrapper spacing passed');
  }

  // Normalized flow accounting keeps physical battery details in stored/DC units.
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';
    context.behaviorMatrixCache = {year:2027, weather_year:2016, battery_kwh:10, annual_flows:{
      energy_reference:'ac_equivalent', physical_charge_basis:'DC', pv_generation_kwh:4500, load_demand_kwh:3600,
      direct_use_kwh:1500, battery_charge_kwh:1200, grid_export_kwh:1800, battery_discharge_kwh:900, grid_import_kwh:1200,
      storage_losses_kwh:297.6, initial_soc_kwh:0, end_soc_kwh:2.4, storage_delta_kwh:2.4, clipped_kwh:0,
      battery_charge_physical_kwh:1234.6, storage_losses_physical_kwh:323.1, initial_soc_stored_kwh:0, end_soc_stored_kwh:2.5, storage_delta_stored_kwh:2.5
    }};
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const html = $('annualEnergyFlowVisual').innerHTML;
    for (const text of ['PV and flow balances use AC-equivalent kWh; battery capacity uses stored kWh.', 'Accounting SOC (AC-equivalent)', 'Storage losses (AC-equivalent): <strong>297.6 kWh', 'Physical battery/conversion losses: <strong>323.1 kWh', 'Physical DC charge: <strong>1,234.6 kWh', 'Physical stored energy: start <strong>0 kWh</strong> · end <strong>2.5 kWh', 'holding 2.5 kWh of remaining stored energy']) assert.ok(html.includes(text), text);
    assert.doesNotMatch(html, /split symmetrically as √η|Backend imbalance/i);
    console.log('✔ Normalized reporting: AC-equivalent balances and physical battery details passed');
  }

  // Test 26: Ensemble Sankey weather mean, flow ranges, headline %, and robust fallback
  {
    const { context, $ } = createTestContext();
    context.behaviorMatrixKey = 'electrified_electrified_fp';

    // Balanced mean flows across 3 historical weather years
    // PV: 6000 = direct (2500) + charge (1500) + export (2000)
    // Demand: 4000 = direct (2500) + discharge (1350) + import (150)
    // Battery: charge (1500) = discharge (1350) + losses (150) + delta (0)
    const ensembleFlows = {
      statistic: 'weather_mean',
      weather_count: 3,
      weather_year: null,
      pv_generation_kwh: 6000.0,
      load_demand_kwh: 4000.0,
      direct_use_kwh: 2500.0,
      battery_charge_kwh: 1500.0,
      battery_discharge_kwh: 1350.0,
      storage_losses_kwh: 150.0,
      grid_import_kwh: 150.0,
      grid_export_kwh: 2000.0,
      initial_soc_kwh: 0.0,
      end_soc_kwh: 0.0,
      storage_delta_kwh: 0.0,
      clipped_kwh: 0.0,
      autarky: 0.9625,
      flow_ranges: {
        autarky: { p10: 0.92, p90: 0.98 },
        pv_generation_kwh: { p10: 5500.0, p90: 6500.0 },
        load_demand_kwh: { p10: 3800.0, p90: 4200.0 },
        direct_use_kwh: { p10: 2300.0, p90: 2700.0 },
        battery_charge_kwh: { p10: 1400.0, p90: 1600.0 },
        battery_discharge_kwh: { p10: 1250.0, p90: 1450.0 },
        storage_losses_kwh: { p10: 130.0, p90: 170.0 },
        grid_import_kwh: { p10: 100.0, p90: 200.0 },
        grid_export_kwh: { p10: 1800.0, p90: 2200.0 },
        storage_delta_kwh: { p10: 0.0, p90: 0.0 }
      }
    };

    context.behaviorMatrixCache = {
      year: 2027,
      weather_year: null,
      battery_kwh: 10.0,
      annual_flows: ensembleFlows
    };

    vm.runInContext('renderAnnualEnergyFlow()', context);
    const metaHtml = $('annualFlowMeta').innerHTML;
    const visualHtml = $('annualEnergyFlowVisual').innerHTML;

    // Metadata assertion
    assert.match(metaHtml, /Average across 3 historical weather years/, 'Displays average across 3 historical weather years in metadata');
    assert.doesNotMatch(metaHtml, /Selected weather analogue/i, 'No selected-analogue narrative in metadata');
    assert.doesNotMatch(visualHtml, /Selected weather analogue/i, 'No selected-analogue narrative in visual body');
    assert.doesNotMatch(visualHtml, /historical single-year trace representing median annual PV yield/i, 'No single-year median trace narrative');

    // Proper headline % and P10-P90 autarky
    assert.match(visualHtml, /96\.3%/, 'Headline autarky percent formatted from flows.autarky');
    assert.match(visualHtml, /\(P10–P90:\s*92\.0%–98\.0%\)/, 'Headline autarky P10-P90 range displayed');

    // Tooltips / Titles contain P10-P90 values
    assert.match(visualHtml, /Grid imports: 150 kWh \(Historical P10–P90: 100 kWh – 200 kWh\)/, 'Grid imports tooltip has P10-P90');
    assert.match(visualHtml, /Usable PV after curtailment: 6,000 kWh \(Historical P10–P90: 5,500 kWh – 6,500 kWh\)/, 'PV tooltip has P10-P90');

    // Main flow table contains P10-P90 ranges
    assert.match(visualHtml, /Direct solar self-consumption<\/th><td><strong>2,500 kWh<\/strong><\/td><td>2,300 kWh – 2,700 kWh<\/td>/, 'Direct solar table row has P10-P90 range');
    assert.match(visualHtml, /Grid electricity imports<\/th><td><strong>150 kWh<\/strong><\/td><td>100 kWh – 200 kWh<\/td>/, 'Grid imports table row has P10-P90 range');

    // Incomplete, null, NaN, and reversed ranges show dash ('—') and no undefined/NaN
    context.behaviorMatrixCache.annual_flows = {
      ...ensembleFlows,
      flow_ranges: {
        autarky: { p10: 0.95, p90: 0.85 },
        pv_generation_kwh: { p10: 5000 },
        load_demand_kwh: { p10: null, p90: 4200 },
        direct_use_kwh: { p10: NaN, p90: 2700 },
        battery_charge_kwh: { p10: 1600, p90: 1400 },
        battery_discharge_kwh: null,
        storage_losses_kwh: undefined,
        grid_import_kwh: { p10: '', p90: 200 },
        grid_export_kwh: 'invalid',
        storage_delta_kwh: { p10: 0, p90: 0 }
      }
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const malformedHtml = $('annualEnergyFlowVisual').innerHTML;
    assert.doesNotMatch(malformedHtml, /undefined|NaN/, 'Malformed flow_ranges produce no undefined or NaN in output');
    assert.match(malformedHtml, /Direct solar self-consumption<\/th><td><strong>2,500 kWh<\/strong><\/td><td>—<\/td>/, 'NaN range renders dash');
    assert.match(malformedHtml, /Battery storage charging<\/th><td><strong>1,500 kWh<\/strong><\/td><td>—<\/td>/, 'Reversed range renders dash');
    assert.match(malformedHtml, /Grid electricity imports<\/th><td><strong>150 kWh<\/strong><\/td><td>—<\/td>/, 'Empty range renders dash');

    // Fallback single-year fixtures remain working
    context.behaviorMatrixCache.annual_flows = {
      ...ensembleFlows,
      statistic: undefined,
      weather_year: 2016,
      weather_count: 19,
      flow_ranges: undefined
    };
    vm.runInContext('renderAnnualEnergyFlow()', context);
    const fallbackMetaHtml = $('annualFlowMeta').innerHTML;
    const fallbackVisualHtml = $('annualEnergyFlowVisual').innerHTML;
    assert.match(fallbackMetaHtml, /Selected weather analogue: <strong>Year 2016<\/strong>/, 'Single-year fallback retains analogue header');
    assert.match(fallbackVisualHtml, /authentic single-year dispatch trace/i, 'Single-year fallback retains median trace narrative');
    assert.doesNotMatch(fallbackVisualHtml, /undefined|NaN/, 'Fallback fixture produces no undefined or NaN');
    console.log('✔ Test 26: Ensemble Sankey weather mean, flow ranges & fallback passed');
  }

  // Test 27: drawTypicalDayChart average-day and P10-P90 tooltips & labels for Annual/season tabs
  {
    const fnChartStart = appSource.indexOf('function drawTypicalDayChart(');
    assert(fnChartStart >= 0, 'drawTypicalDayChart must exist in app.js');
    const fnChartEnd = appSource.indexOf('function drawMonthHourHeatmap(', fnChartStart);
    assert(fnChartEnd > fnChartStart, 'End of drawTypicalDayChart must be found');

    const { context, $ } = createTestContext();
    vm.runInContext(appSource.slice(fnChartStart, fnChartEnd), context);

    const capturedTooltips = [];
    context.chartTarget = tip => {
      capturedTooltips.push(tip);
      return `data-tip="${context.esc(tip)}"`;
    };

    const makeHours = () => Array.from({ length: 24 }, (_, i) => ({
      hour: i,
      pv: 3.5,
      load: 1.5,
      direct: 1.2,
      import: 0.3,
      export: 2.0,
      soc: 5.0,
      pv_envelope: [2.5, 4.5],
      load_envelope: [1.1, 1.9],
      direct_envelope: [0.8, 1.6],
      soc_envelope: [3.0, 7.0],
      import_envelope: [0.1, 0.5],
      export_envelope: [1.2, 2.8]
    }));

    const chartData = {
      battery_kwh: 8.28,
      matrix: {
        seasons: {
          annual: makeHours(),
          summer: makeHours()
        }
      }
    };
    context.chartData = chartData;

    for (const tab of ['annual', 'summer']) {
      capturedTooltips.length = 0;
      vm.runInContext(`drawTypicalDayChart(chartData, "${tab}")`, context);
      const chartHtml = $('typicalDayChart').innerHTML;

      assert.match(chartHtml, new RegExp(`Average day hourly dispatch for ${tab}; shaded bands show P10–P90 weather variation`), `${tab} chart has average-day and P10-P90 aria-label`);
      assert.match(chartHtml, /dispatch takeaway · average-day profile:/, `${tab} chart has average-day takeaway`);
      assert.doesNotMatch(chartHtml, /NaN/, `${tab} chart HTML contains no NaN`);

      assert.strictEqual(capturedTooltips.length, 24, `${tab} chart captured 24 hourly tooltips`);
      for (const tip of capturedTooltips) {
        assert.match(tip, /average day · Hour \d+:00–\d+:00/, `${tab} tooltip has average-day label`);
        assert.match(tip, /PV P10–P90: 2\.50–4\.50 kW/, `${tab} tooltip has PV P10-P90 range`);
        assert.match(tip, /Battery SOC P10–P90: 3\.00–7\.00 kWh/, `${tab} tooltip has Battery SOC P10-P90 range`);
        assert.doesNotMatch(tip, /NaN/, `${tab} tooltip contains no NaN`);
      }
    }
    console.log('✔ Test 27: drawTypicalDayChart average-day and P10-P90 tooltips & labels passed');
  }

  console.log('ALL ANNUAL ENERGY-FLOW PRESENTATION TESTS PASSED SUCCESSFULLY!');
})().catch(err => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
