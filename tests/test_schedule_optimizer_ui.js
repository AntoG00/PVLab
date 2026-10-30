// tests/test_schedule_optimizer_ui.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const schedulePath = path.resolve(__dirname, '../feasibility/web/schedule.js');
const scheduleSource = fs.readFileSync(schedulePath, 'utf8');

function setupTest() {
  const documentListeners = {};
  let targetHtml = '';
  const buttonList = [];
  const targetListeners = new Map();

  function parseButtons(html) {
    buttonList.length = 0;
    const btnRegex = /<button\b([^>]*)>([\s\S]*?)<\/button>/gi;
    let match;
    while ((match = btnRegex.exec(html)) !== null) {
      const attrs = match[1];
      const content = match[2];
      const idM = attrs.match(/\bid=["']([^"']+)["']/i);
      const classM = attrs.match(/\bclass=["']([^"']+)["']/i);
      const dataRowM = attrs.match(/\bdata-row-idx=["']([^"']+)["']/i);
      const isDisabled = /\bdisabled\b/i.test(attrs);

      const id = idM ? idM[1] : null;
      const classList = new Set(classM ? classM[1].split(/\s+/).filter(Boolean) : []);
      const dataset = {};
      if (dataRowM) dataset.rowIdx = dataRowM[1];

      const dataRegex = /\bdata-([a-z0-9\-]+)=["']([^"']+)["']/gi;
      let dMatch;
      while ((dMatch = dataRegex.exec(attrs)) !== null) {
        const camelKey = dMatch[1].replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase());
        dataset[camelKey] = dMatch[2];
      }

      const listeners = new Map();
      const btnObj = {
        id,
        classList,
        className: classM ? classM[1] : '',
        dataset,
        disabled: isDisabled,
        innerHTML: content,
        textContent: content.replace(/<[^>]*>/g, '').trim(),
        addEventListener(event, fn) {
          if (!listeners.has(event)) listeners.set(event, []);
          listeners.get(event).push(fn);
        },
        click() {
          if (this.disabled) return;
          const ev = { target: this, currentTarget: this, preventDefault: () => {} };
          const fns = listeners.get('click') || [];
          for (const fn of fns) fn.call(this, ev);
          const tFns = targetListeners.get('click') || [];
          for (const fn of tFns) fn.call(this, ev);
        },
        closest(sel) {
          if (sel.startsWith('#') && this.id === sel.slice(1)) return this;
          if (sel.startsWith('.') && this.classList.has(sel.slice(1))) return this;
          if (sel.includes('[data-row-idx') && this.dataset.rowIdx) return this;
          return null;
        }
      };
      buttonList.push(btnObj);
    }
  }

  const target = {
    get innerHTML() { return targetHtml; },
    set innerHTML(html) {
      targetHtml = String(html);
      parseButtons(targetHtml);
    },
    addEventListener(event, fn) {
      if (!targetListeners.has(event)) targetListeners.set(event, []);
      targetListeners.get(event).push(fn);
    },
    querySelector(sel) {
      for (const btn of buttonList) {
        if (sel.startsWith('#') && btn.id === sel.slice(1)) return btn;
        if (sel.startsWith('.') && btn.classList.has(sel.slice(1))) return btn;
        if (sel.includes('[data-row-idx=')) {
          const m = sel.match(/\[data-row-idx=["']?(\d+)["']?\]/);
          if (m && btn.dataset.rowIdx === m[1]) return btn;
        }
      }
      return null;
    },
    querySelectorAll(sel) {
      const results = [];
      for (const btn of buttonList) {
        if (sel.startsWith('.') && btn.classList.has(sel.slice(1))) {
          if (!results.includes(btn)) results.push(btn);
        } else if (sel === 'button') {
          if (!results.includes(btn)) results.push(btn);
        }
      }
      return results;
    }
  };

  const mockDoc = {
    chartPending: false,
    getElementById(id) {
      if (id === 'switchHourOptimizer') return target;
      return null;
    },
    addEventListener(event, fn) { documentListeners[event] = fn; },
    querySelector(sel) {
      if (sel === '.chart-pending') return mockDoc.chartPending ? { className: 'chart-pending' } : null;
      return null;
    },
    querySelectorAll() { return []; }
  };

  let renderControlsCalls = 0;
  let scheduleCalls = 0;

  const defaultSettings = {
    routing_priority: 'battery_first',
    routing_switch_hour: 11,
    scheduled_start_month: 2,
    scheduled_end_month: 8,
    keep_battery_capacity: 15,
    custom_unrelated_setting: 'preserve_value'
  };

  const defaultQuote = { id: 'q-100' };
  const validFingerprint = 'fp_valid_123';

  const contextObj = {
    document: mockDoc,
    window: {},
    console,
    settings: Object.assign({}, defaultSettings),
    quote: Object.assign({}, defaultQuote),
    behaviorMatrixCache: null,
    loanPending: false,
    acceptedInputFingerprint: validFingerprint,
    inputFingerprint: () => validFingerprint,
    renderControls: () => { renderControlsCalls++; },
    schedule: () => { scheduleCalls++; }
  };
  contextObj.window = contextObj;

  const ctx = vm.createContext(contextObj);
  vm.runInContext(scheduleSource, ctx);

  return {
    ctx,
    target,
    mockDoc,
    clickMonth: month => documentListeners.click({target:{closest:()=>({dataset:{scheduleMonth:String(month)},disabled:false,hasAttribute:()=>false,getAttribute:()=>null})}}),
    getCounters: () => ({ renderControlsCalls, scheduleCalls }),
    resetCounters: () => { renderControlsCalls = 0; scheduleCalls = 0; }
  };
}

// 1. Test apply 4 fields preserving other settings
{
  const { ctx, target, getCounters } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', switch_hour: null, clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'feasible', switch_hour: 14, scheduled_start_month: 4, scheduled_end_month: 10, clipped_kwh: 20, grid_import_kwh: 1800, net_energy_value_delta_eur: 50 }
      ],
      recommendation: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 14,
        scheduled_start_month: 4,
        scheduled_end_month: 10
      }
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  const btn = target.querySelector('#applyOptimalScheduleBtn');
  assert.ok(btn, 'Apply button found after render');
  assert.strictEqual(btn.disabled, false, 'Apply button is enabled');

  btn.click();

  assert.strictEqual(ctx.settings.routing_priority, 'scheduled_hybrid', 'routing_priority updated');
  assert.strictEqual(ctx.settings.routing_switch_hour, 14, 'routing_switch_hour updated');
  assert.strictEqual(ctx.settings.scheduled_start_month, 4, 'scheduled_start_month updated');
  assert.strictEqual(ctx.settings.scheduled_end_month, 10, 'scheduled_end_month updated');
  assert.strictEqual(ctx.settings.keep_battery_capacity, 15, 'unrelated setting 1 preserved');
  assert.strictEqual(ctx.settings.custom_unrelated_setting, 'preserve_value', 'unrelated setting 2 preserved');

  const counters = getCounters();
  assert.strictEqual(counters.renderControlsCalls, 1, 'renderControls called');
  assert.strictEqual(counters.scheduleCalls, 1, 'schedule called');
  console.log('✔ Test 1: apply 4 fields preserving other settings passed');
}

// 2. Stale behaviorMatrixCache = null / replaced rejects
{
  const { ctx, target, getCounters, resetCounters } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [{ status: 'baseline', switch_hour: null, clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 }],
      recommendation: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 13 }
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);
  const btn = target.querySelector('#applyOptimalScheduleBtn');

  // Case A: null
  ctx.behaviorMatrixCache = null;
  btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'battery_first', 'Apply rejected when behaviorMatrixCache is null');
  assert.strictEqual(getCounters().scheduleCalls, 0, 'schedule not called on null cache');

  // Case B: replaced
  resetCounters();
  ctx.behaviorMatrixCache = { other: true };
  btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'battery_first', 'Apply rejected when behaviorMatrixCache is replaced');
  assert.strictEqual(getCounters().scheduleCalls, 0, 'schedule not called on replaced cache');
  console.log('✔ Test 2: stale behaviorMatrixCache rejection passed');
}

// 3. Stale input rejects
{
  const { ctx, target, getCounters } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [{ status: 'baseline', switch_hour: null, clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 }],
      recommendation: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 13 }
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);
  const btn = target.querySelector('#applyOptimalScheduleBtn');

  ctx.acceptedInputFingerprint = 'stale_fingerprint_mismatch';
  btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'battery_first', 'Apply rejected when fingerprint mismatches');
  assert.strictEqual(getCounters().scheduleCalls, 0, 'schedule not called on stale input');
  console.log('✔ Test 3: stale input rejection passed');
}

// 4. No recommendation disabled
{
  const { ctx, target, getCounters } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [{ status: 'baseline', switch_hour: null, clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 }],
      recommendation: null,
      recommendation_npv: null,
      recommendation_clipping: null
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  assert.match(target.innerHTML, /no recommendation/i, 'No recommendation label rendered');
  const btn = target.querySelector('#applyOptimalScheduleBtn');
  assert.ok(btn, 'Button element exists');
  assert.strictEqual(btn.disabled, true, 'Button is disabled without recommendation');

  btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'battery_first', 'No apply action when disabled');
  assert.strictEqual(getCounters().scheduleCalls, 0, 'schedule not called without recommendation');
  console.log('✔ Test 4: no recommendation disabled passed');
}

// 5. Reset removes recommendation
{
  const { ctx, target } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [{ status: 'baseline', switch_hour: null, clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 }],
      recommendation: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 12 }
    }
  };

  ctx.renderSwitchHourOptimizer(optData);
  assert.ok(target.querySelector('#applyOptimalScheduleBtn'), 'Button present before reset');

  ctx.resetSwitchHourOptimizer('Inputs changed');
  assert.strictEqual(target.querySelector('#applyOptimalScheduleBtn'), null, 'Button removed after reset with message');
  assert.match(target.innerHTML, /Inputs changed/, 'Reset message rendered');

  ctx.resetSwitchHourOptimizer();
  assert.strictEqual(target.innerHTML, '', 'Container empty after clean reset');
  assert.strictEqual(target.querySelector('#applyOptimalScheduleBtn'), null, 'Button remains absent');
  console.log('✔ Test 5: reset removes recommendation passed');
}

// 6. Inclusive range edits preserve the selected routing mode, including resolved Auto
{
  const {ctx,clickMonth,getCounters}=setupTest();
  ctx.behaviorMatrixCache={selected_config:{routing_priority:'scheduled_hybrid'}};
  Object.assign(ctx.settings,{routing_priority:'auto',scheduled_start_month:3,scheduled_end_month:9});
  clickMonth(3);assert.equal(ctx.settings.routing_priority,'scheduled_hybrid');assert.equal(ctx.settings.scheduled_start_month,4);
  clickMonth(6);assert.equal(ctx.settings.scheduled_start_month,7);
  clickMonth(10);assert.equal(ctx.settings.scheduled_end_month,10);
  Object.assign(ctx.settings,{scheduled_start_month:11,scheduled_end_month:2});
  clickMonth(1);assert.equal(ctx.settings.scheduled_end_month,12);
  Object.assign(ctx.settings,{scheduled_start_month:3,scheduled_end_month:3});
  clickMonth(3);assert.equal(ctx.settings.routing_priority,'battery_first');
  const calls=getCounters().scheduleCalls;ctx.loanPending=true;clickMonth(4);assert.equal(getCounters().scheduleCalls,calls);
  console.log('✔ Test 6: Inline month range, wrap, Auto lock, and pending checks passed');
}

// 7. Dual distinct header recommendations: Max-NPV and Min-Clipping buttons
{
  const { ctx, target, getCounters, resetCounters } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', routing_priority: 'battery_first', clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'feasible', routing_priority: 'scheduled_hybrid', routing_switch_hour: 10, scheduled_start_month: 2, scheduled_end_month: 10, equity_npv_delta: 250.0, clipping_saved_kwh: 30.0, net_energy_value_delta_eur: 60.0, clipped_kwh: 70, grid_import_kwh: 1940 },
        { status: 'benchmark', routing_priority: 'dynamic_reserve', equity_npv_delta: 200.0, clipping_saved_kwh: 90.0, net_energy_value_delta_eur: 45.0, clipped_kwh: 10, grid_import_kwh: 1955 }
      ],
      recommendation_npv: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 10,
        scheduled_start_month: 2,
        scheduled_end_month: 10,
        net_energy_value_delta_eur: 60.0
      },
      recommendation_clipping: {
        routing_priority: 'dynamic_reserve',
        clipping_saved_kwh: 90.0
      },
      recommendation: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 10,
        scheduled_start_month: 2,
        scheduled_end_month: 10
      },
      horizon_years: 25
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  const npvBtn = target.querySelector('#applyOptimalScheduleBtn');
  const clipBtn = target.querySelector('#applyMinClippingScheduleBtn');

  assert.ok(npvBtn, 'Max-NPV apply button found');
  assert.ok(clipBtn, 'Min-Clipping apply button found');
  assert.match(npvBtn.textContent, /Max-NPV/i, 'NPV button text matches Max-NPV');
  assert.match(clipBtn.textContent, /Min-Clipping/i, 'Clipping button text matches Min-Clipping');
  assert.match(clipBtn.textContent, /Dynamic Reserve · \+90 kWh saved/, 'Dynamic savings use a positive sign');

  // Click Max-NPV button
  npvBtn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'scheduled_hybrid');
  assert.strictEqual(ctx.settings.routing_switch_hour, 10);
  assert.strictEqual(ctx.settings.scheduled_start_month, 2);
  assert.strictEqual(ctx.settings.scheduled_end_month, 10);

  // Reset to hybrid and click Min-Clipping (Dynamic Reserve) button
  resetCounters();
  ctx.settings.routing_priority = 'battery_first';
  ctx.settings.scheduled_start_month = 5;
  ctx.settings.scheduled_end_month = 8;
  clipBtn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'dynamic_reserve', 'Dynamic reserve applied on clipping button click');
  assert.strictEqual(ctx.settings.scheduled_start_month, 5, 'Unrelated start month preserved under dynamic reserve');
  assert.strictEqual(ctx.settings.scheduled_end_month, 8, 'Unrelated end month preserved under dynamic reserve');
  assert.strictEqual(getCounters().scheduleCalls, 1, 'Schedule called on dynamic reserve apply');
  console.log('✔ Test 7: Dual distinct header recommendations passed');
}

// 8. Identical config identity yields single unified Apply button
{
  const { ctx, target } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', routing_priority: 'battery_first', clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'feasible', routing_priority: 'scheduled_hybrid', routing_switch_hour: 12, scheduled_start_month: 3, scheduled_end_month: 9, clipped_kwh: 40, grid_import_kwh: 1900, net_energy_value_delta_eur: 40 }
      ],
      recommendation_npv: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 12,
        scheduled_start_month: 3,
        scheduled_end_month: 9
      },
      recommendation_clipping: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 12,
        scheduled_start_month: 3,
        scheduled_end_month: 9
      },
      recommendation: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 12,
        scheduled_start_month: 3,
        scheduled_end_month: 9
      }
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  const npvBtn = target.querySelector('#applyOptimalScheduleBtn');
  const clipBtn = target.querySelector('#applyMinClippingScheduleBtn');

  assert.ok(npvBtn, 'Single optimal apply button present');
  assert.strictEqual(clipBtn, null, 'Second button absent when configs match');
  assert.match(npvBtn.textContent, /Apply optimal/i, 'Single button has unified optimal label');
  console.log('✔ Test 8: Identical config identity yields single unified button passed');
}

// 9. Clipping-only recommendation shows Apply Min-Clipping label
{
  const { ctx, target } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', routing_priority: 'battery_first', clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'feasible', routing_priority: 'scheduled_hybrid', routing_switch_hour: 11, scheduled_start_month: 5, scheduled_end_month: 8, clipped_kwh: 30, grid_import_kwh: 1950, net_energy_value_delta_eur: 20 }
      ],
      recommendation_npv: null,
      recommendation_clipping: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 11,
        scheduled_start_month: 5,
        scheduled_end_month: 8,
        clipping_saved_kwh: 70.0
      },
      recommendation: {
        routing_priority: 'scheduled_hybrid',
        routing_switch_hour: 11,
        scheduled_start_month: 5,
        scheduled_end_month: 8
      }
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  const btn = target.querySelector('#applyOptimalScheduleBtn');
  assert.ok(btn, 'Actionable button exists');
  assert.match(btn.textContent, /Min-Clipping/i, 'Label reflects clipping-only objective');
  Object.assign(optData.schedule_optimizer.recommendation_clipping, {
    scheduled_start_month: 4, scheduled_end_month: 6,
    routing_switch_hour: 13, clipping_saved_kwh: 49.2
  });
  ctx.renderSwitchHourOptimizer(optData);
  assert.match(target.querySelector('#applyOptimalScheduleBtn').textContent,
    /Apr–Jun · switch 13:00 · \+49 kWh saved/, 'Schedule label shows saved energy clearly');
  console.log('✔ Test 9: Clipping-only recommendation label passed');
}

// 10. Per-row buttons (.schedule-apply-row-btn) apply candidate row schedule
{
  const { ctx, target, getCounters } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', routing_priority: 'battery_first', clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'no_eligible_months', routing_priority: 'scheduled_hybrid', routing_switch_hour: 9, scheduled_start_month: null, scheduled_end_month: null },
        { status: 'feasible', routing_priority: 'scheduled_hybrid', routing_switch_hour: 13, scheduled_start_month: 4, scheduled_end_month: 6, clipped_kwh: 35, grid_import_kwh: 1850, net_energy_value_delta_eur: 45 },
        { status: 'infeasible_verification', routing_priority: 'scheduled_hybrid', routing_switch_hour: 11, scheduled_start_month: 3, scheduled_end_month: 7, clipped_kwh: 40, grid_import_kwh: 1900, net_energy_value_delta_eur: 25 }
      ],
      recommendation_npv: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 13, scheduled_start_month: 4, scheduled_end_month: 6 },
      recommendation_clipping: null
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  // Baseline (idx 0) and no_eligible_months (idx 1) placeholders have NO apply buttons
  assert.strictEqual(target.querySelector('[data-row-idx="0"]'), null, 'Baseline row has no apply button');
  assert.strictEqual(target.querySelector('[data-row-idx="1"]'), null, 'no_eligible_months row has no apply button');

  // Complete feasible row (idx 2) and complete infeasible_verification row (idx 3) DO have apply buttons
  const row13Btn = target.querySelector('[data-row-idx="2"]');
  assert.ok(row13Btn, 'Row 13 button present');
  const rowInfeasibleBtn = target.querySelector('[data-row-idx="3"]');
  assert.ok(rowInfeasibleBtn, 'Complete infeasible_verification row button present');

  row13Btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'scheduled_hybrid');
  assert.strictEqual(ctx.settings.routing_switch_hour, 13);
  assert.strictEqual(ctx.settings.scheduled_start_month, 4);
  assert.strictEqual(ctx.settings.scheduled_end_month, 6);
  assert.strictEqual(getCounters().scheduleCalls, 1, 'Schedule called on row 13 apply');
  console.log('✔ Test 10: Per-row buttons apply candidate row passed');
}

// 11. Stale input, loanPending, and chart-pending prevent apply mutations
{
  const { ctx, target, mockDoc } = setupTest();
  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', routing_priority: 'battery_first', clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'feasible', routing_priority: 'scheduled_hybrid', routing_switch_hour: 12, scheduled_start_month: 3, scheduled_end_month: 9, clipped_kwh: 40, grid_import_kwh: 1900, net_energy_value_delta_eur: 30 }
      ],
      recommendation: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 12, scheduled_start_month: 3, scheduled_end_month: 9 }
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  const btn = target.querySelector('#applyOptimalScheduleBtn');

  // Case A: loanPending
  ctx.loanPending = true;
  ctx.settings.routing_priority = 'battery_first';
  btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'battery_first', 'Mutation blocked by loanPending');
  ctx.loanPending = false;

  // Case B: chartPending
  mockDoc.chartPending = true;
  btn.click();
  assert.strictEqual(ctx.settings.routing_priority, 'battery_first', 'Mutation blocked by chartPending');
  mockDoc.chartPending = false;
  console.log('✔ Test 11: Stale input, loanPending, and chart-pending guard passed');
}

// 12. Badges, columns, and footer source fields regression
{
  const { ctx, target } = setupTest();
  Object.assign(ctx.settings, {
    export_irpef: 0.35,
    regional_additional_tax: 0.015,
    municipal_additional_tax: 0.005,
    export_price_profile: 'empirical_csud',
    export_price_future_years: 10,
    actual_price_future_factor: 0.3
  });

  const optData = {
    schedule_optimizer: {
      rows: [
        { status: 'baseline', routing_priority: 'battery_first', clipped_kwh: 100, grid_import_kwh: 2000, net_energy_value_delta_eur: 0 },
        { status: 'feasible', routing_priority: 'scheduled_hybrid', routing_switch_hour: 10, scheduled_start_month: 2, scheduled_end_month: 10, equity_npv_delta: 350.0, clipping_saved_kwh: 25.0, net_energy_value_delta_eur: 75.0, clipped_kwh: 75, grid_import_kwh: 1925, summer_p25_peak_soc_pct: 94.0 },
        { status: 'benchmark', routing_priority: 'dynamic_reserve', equity_npv_delta: 280.0, clipping_saved_kwh: 95.0, net_energy_value_delta_eur: 40.0, clipped_kwh: 5, grid_import_kwh: 1960, summer_p25_peak_soc_pct: 96.0 }
      ],
      recommendation_npv: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 10, scheduled_start_month: 2, scheduled_end_month: 10 },
      recommendation_clipping: { routing_priority: 'dynamic_reserve' },
      horizon_years: 25
    }
  };

  ctx.behaviorMatrixCache = optData;
  ctx.renderSwitchHourOptimizer(optData);

  const html = target.innerHTML;
  // Columns
  assert.match(html, /Year 1 €\/y/i, 'Year-1 column present');
  assert.match(html, /25Y NPV €/i, '25Y NPV delta column present');
  assert.match(html, /Min P25 SoC/i, 'Min P25 SoC column present');

  // Badges
  assert.match(html, /★ Best NPV/i, 'Best NPV badge present');
  assert.match(html, /★ Min Clipping/i, 'Min Clipping badge present');

  // Footer fields: 37.0% tax, 10-year ramp, and empirical CSUD wording
  assert.match(html, /37\.0% export tax/i, '37.0% export tax displayed');
  assert.match(html, /10-year price ramp/i, '10-year price ramp displayed');
  assert.match(html, /empirical CSUD 07:00–10:00 AM/i, 'Empirical CSUD arbitrage explanation present');

  // Non-empirical profile check: should not claim active empirical CSUD arbitrage
  ctx.settings.export_price_profile = 'flat_solar_capture_benchmark';
  ctx.renderSwitchHourOptimizer(optData);
  assert.match(target.innerHTML, /Without empirical CSUD export pricing/i, 'Non-CSUD profile correctly noted');
  assert.doesNotMatch(target.innerHTML, /capture empirical CSUD 07:00–10:00 AM morning price arbitrage/i, 'No active empirical CSUD claim under flat profile');
  console.log('✔ Test 12: Badges, columns, and footer source fields regression passed');
}

console.log('All schedule optimizer UI tests passed.');
