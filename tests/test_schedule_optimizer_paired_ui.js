// Standalone Node regression test for Switch Hour Optimizer UI paired statistics

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const appSource = fs.readFileSync(path.resolve(__dirname, '../feasibility/web/app.js'), 'utf8');
const sharedHelperLines = appSource.split('\n').filter(line =>
  line.startsWith('const $=') || line.startsWith('function table('));
assert.equal(sharedHelperLines.length, 2, 'Load the actual app escaping and table helpers');
const sharedHelpers = sharedHelperLines.join('\n');

function loadScheduleScript() {
  const resolvedPath = path.resolve(__dirname, '../feasibility/web/schedule.js');
  return { scriptCode: fs.readFileSync(resolvedPath, 'utf8'), resolvedPath };
}

function createMockEnvironment() {
  const optimizerElement = {
    innerHTML: '',
    querySelector(selector) {
      return {
        addEventListener(event, fn) {}
      };
    }
  };

  const documentMock = {
    getElementById(id) {
      if (id === 'switchHourOptimizer') return optimizerElement;
      return null;
    },
    querySelector(selector) {
      return null;
    },
    addEventListener(event, fn) {}
  };

  const sandbox = {
    console,
    document: documentMock,
    Number,
    String,
    Array,
    Object,
    parseInt,
    isNaN,
    Math
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const ctx = vm.createContext(sandbox);
  vm.runInContext(sharedHelpers, ctx);
  assert.equal(typeof ctx.table, 'function', 'Exercise the production shared-table environment');
  return { ctx, optimizerElement };
}

function runTest() {
  const { scriptCode, resolvedPath } = loadScheduleScript();
  console.log(`Testing schedule optimizer UI script from: ${resolvedPath}`);

  // 1. Paired statistics rendering (all 19-weather fields)
  {
    const { ctx, optimizerElement } = createMockEnvironment();
    vm.runInContext(scriptCode, ctx);

    assert(typeof ctx.renderSwitchHourOptimizer === 'function', 'renderSwitchHourOptimizer must be exported to global');

    const testData = {
      schedule_optimizer: {
        elapsed_seconds: 0.35,
        time_basis: '19-weather ensemble',
        weather_year_count: 19,
        recommendation: {
          routing_priority: 'scheduled_hybrid',
          routing_switch_hour: 12,
          scheduled_start_month: 3,
          scheduled_end_month: 9
        },
        rows: [
          {
            status: 'baseline',
            routing_priority: 'baseline',
            clipped_kwh: 165.7,
            clipping_saved_kwh: 0,
            net_energy_value_delta_eur: 0,
            summer_max_soc_pct: 98.0,
            summer_p25_peak_soc_pct: 88.0,
            grid_import_kwh: 650,
            autarky: 0.80
          },
          {
            status: 'benchmark',
            routing_priority: 'dynamic_reserve',
            routing_switch_hour: null,
            clipped_kwh: 85.2,
            clipping_saved_kwh: 80.5,
            clipping_saved_p10_kwh: 40.0,
            clipping_saved_p90_kwh: 110.0,
            clipping_improving_years: 19,
            net_energy_value_delta_eur: 62.0,
            net_energy_value_delta_p10_eur: 20.0,
            net_energy_value_delta_p90_eur: 105.0,
            value_improving_years: 19,
            weather_year_count: 19,
            summer_max_soc_pct: 90.0,
            summer_p25_peak_soc_pct: 78.5,
            grid_import_kwh: 420,
            autarky: 0.89
          },
          {
            status: 'feasible',
            metrics_basis: 'verified_trace',
            routing_switch_hour: 12,
            scheduled_start_month: 3,
            scheduled_end_month: 9,
            active_months: [3, 4, 5, 6, 7, 8, 9],
            clipped_kwh: 120.5,
            clipping_saved_kwh: 45.2,
            clipping_saved_p10_kwh: 15.0,
            clipping_saved_p90_kwh: 72.3,
            clipping_improving_years: 19,
            net_energy_value_delta_eur: 35.0,
            net_energy_value_delta_p10_eur: -12.0,
            net_energy_value_delta_p90_eur: 80.0,
            value_improving_years: 18,
            weather_year_count: 19,
            summer_max_soc_pct: 95.0,
            summer_p25_peak_soc_pct: 82.0,
            grid_import_kwh: 500,
            autarky: 0.85
          }
        ]
      }
    };

    ctx.renderSwitchHourOptimizer(testData);
    const html = optimizerElement.innerHTML;
    assert(html.length > 0, 'UI output must not be empty');
    assert(/<td\b[^>]*><div>\+45\.2<\/div>/.test(html), 'Formatted cells must render as HTML');
    assert(!html.includes('&lt;div'), 'Formatted cell markup must not appear as literal text');

    // Average curtailment and clipping savings headers
    assert(html.includes('Curt. kWh · avg'), 'Must render average curtailment header Curt. kWh · avg');
    assert(html.includes('+45.2') && html.includes('Saved kWh/y'), 'Must display clipping savings with units in the header');

    // Clipping P10–P90 range
    assert(html.includes('>15.0–72.3</span>'), 'Must display the compact clipping P10–P90 range');

    // 19/19 clipping improving years
    assert(html.includes('19/19 ↑'), 'Must display 19/19 clipping improving years');

    // Net energy value delta mean
    assert(html.includes('+€35'), 'Must display mean net energy value delta (+€35)');

    // Negative value P10 and range
    assert(html.includes('-€12'), 'Must display negative value P10 (-€12)');
    assert(html.includes('-€12–+€80'), 'Must display net energy value P10–P90 range (-€12–+€80)');

    // 18/19 value improving years
    assert(html.includes('18/19 ↑'), 'Must display 18/19 value improving years');

    // Weather spread label and footnote
    assert(
      html.includes('historical weather spread') || html.toLowerCase().includes('historical weather spread'),
      'Must clearly label historical weather spread'
    );
    assert(
      html.includes('not a statistical confidence interval or future probability') ||
      html.includes('not a confidence interval or future probability'),
      'Must clarify P10-P90 is not a confidence interval or future probability'
    );
    assert(
      !html.includes('Results use the same Self-Use median weather analogue'),
      'Must remove fixed median analogue basis text from footnote'
    );
    assert(
      !html.includes('Curtailment and paired savings retain their median basis'),
      'Footnote must not state curtailment and savings retain median basis'
    );
    assert(
      html.includes('arithmetic means across all weather years') &&
      html.includes('matching the annual Sankey and monthly clipping profile'),
      'Footnote must state headline values are arithmetic means matching Sankey and monthly profile'
    );
    assert(
      html.includes('lifetime equity NPV and physical clipping saved') &&
      html.includes('same weather year'),
      'Footnote must describe both objectives and pairing within the same weather year'
    );

    // P25 Peak SoC header and row values
    assert(html.includes('P25 Peak SoC'), 'Must render P25 Peak SoC column header');
    assert(html.includes('78.5%') && html.includes('82.0%'), 'Must render P25 Peak SoC values');

    // Dynamic Reserve (Look-Ahead) benchmark row distinct from Self-Use and metrics retained
    assert(html.includes('Dynamic Reserve (Look-Ahead)'), 'Must render Dynamic Reserve (Look-Ahead) distinctly');
    assert(html.includes('Self-Use'), 'Must render Self-Use baseline distinctly');
    assert(html.includes('<div>+80.5</div>'), 'Dynamic Reserve clipping saved metric retained');
    assert(html.includes('+€62'), 'Dynamic Reserve net energy value metric retained');
    assert(html.includes('Look-ahead'), 'Dynamic Reserve active months must display Look-ahead');
    assert(!html.includes('0 mo') && !html.includes('0mo'), 'Must not display 0 mo for Look-ahead months');
    assert(html.includes('Apply optimal schedule (Mar–Sep · switch 12:00)'), 'Recommendation apply button must target fixed schedule');
    assert(!html.includes('Apply optimal schedule (Dynamic'), 'Dynamic recommendation must never be applied');

    console.log('✔ Paired statistics test passed');
  }

  // 2. Legacy row missing new stats (graceful degradation, no NaN or undefined)
  {
    const { ctx, optimizerElement } = createMockEnvironment();
    vm.runInContext(scriptCode, ctx);

    const legacyData = {
      schedule_optimizer: {
        rows: [
          {
            status: 'feasible',
            routing_switch_hour: 11,
            scheduled_start_month: 4,
            scheduled_end_month: 8,
            clipped_kwh: 130.0,
            net_energy_value_delta_eur: 20.0
          }
        ]
      }
    };

    ctx.renderSwitchHourOptimizer(legacyData);
    const html = optimizerElement.innerHTML;
    assert(html.length > 0, 'Legacy output must not be empty');

    assert(!html.includes('undefined'), 'Output must not contain literal "undefined"');
    assert(!html.includes('NaN'), 'Output must not contain literal "NaN"');

    console.log('✔ Missing stats legacy row test passed (no undefined / NaN)');
  }

  // 3. Hostile input escaping
  {
    const { ctx, optimizerElement } = createMockEnvironment();
    vm.runInContext(scriptCode, ctx);

    const hostileData = {
      schedule_optimizer: {
        rows: [
          {
            status: 'feasible',
            routing_switch_hour: '<img src=x onerror=alert(1)>',
            scheduled_start_month: 3,
            scheduled_end_month: 9,
            clipped_kwh: 100,
            clipping_saved_kwh: 10
          }
        ]
      }
    };

    ctx.renderSwitchHourOptimizer(hostileData);
    const html = optimizerElement.innerHTML;

    assert(!html.includes('<img src=x'), 'Hostile HTML tag must NOT appear unescaped in output');
    assert(html.includes('&lt;img src=x'), 'Hostile HTML tag must be properly escaped');

    console.log('✔ Hostile HTML input escaping test passed');
  }

  console.log('All schedule optimizer UI regression tests passed successfully!');
}

runTest();
