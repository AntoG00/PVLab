'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function makeDayhourFixture(options = {}) {
  const settings = options.settings || {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    timezone_mode: 'civil',
    ac_heating_additional_demand_pct: 0,
    ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }]
  };
  const hours = [];
  for (let h = 0; h < 24; h++) {
    hours.push({
      hour: h,
      pv_kwh: options.pv_kwh != null ? options.pv_kwh : (h >= 8 && h <= 18 ? 2.5 : 0),
      load_kwh: options.load_kwh != null ? options.load_kwh : (h === 8 ? 4.0 : 1.2),
      direct_kwh: 0.8,
      charge_input_kwh: 1.0,
      discharge_delivered_kwh: 0.5,
      grid_import_kwh: 0.4,
      grid_export_kwh: 0.7,
      soc_start_kwh: 2.0,
      soc_end_kwh: 3.0,
      heat_pump: {
        space_compressor_electricity_kwh: 0.5,
        dhw_compressor_electricity_kwh: 0.1,
        delivered_space_heat_kwh: 1.8,
        delivered_draw_heat_kwh: 0.4,
        unmet_space_heat_kwh: 0,
        unmet_draw_heat_kwh: 0
      }
    });
  }
  return {
    settings: deepClone(settings),
    mode: options.mode || 'forecast_2027',
    period: options.period || 'day',
    scenario: options.scenario || settings.load_scenario || 'electrified',
    representative: options.representative || 'median',
    weather_year: options.weather_year || 2015,
    date: options.date || '2027-01-01',
    month: options.month || 1,
    timezone: 'Europe/Rome (DST)',
    available_hours: Array.from({ length: 24 }, (_, i) => i),
    battery_capacity_kwh: options.battery_capacity_kwh != null ? options.battery_capacity_kwh : 10,
    max_balance_error_kwh: 0,
    hours,
    totals: {
      pv_kwh: 25.0,
      load_kwh: 28.8,
      direct_kwh: 10.0,
      charge_input_kwh: 8.0,
      discharge_delivered_kwh: 6.0,
      grid_import_kwh: 5.0,
      grid_export_kwh: 7.0,
      soc_start_kwh: 2.0,
      soc_end_kwh: 3.0
    }
  };
}

function makeEvaluateFixture(settings) {
  return {
    compute_seconds: 0.02,
    claims: [],
    warnings: [],
    result: {
      settings: deepClone(settings),
      limitations: [],
      weather_years: [2015, 2016],
      layout: { kwp: 6, panel_count: 14 },
      metrics: { npv_eur: 5000 },
      finance: {
        capex_eur: 8000,
        conversion_capex_eur: 0,
        annual_deduction_eur: 400,
        nominal_discount_rate: 0.04,
        real_discount_rate: 0.02,
        whole_package: { simple_payback: 8, discounted_payback: 10 }
      },
      energy: {
        load_kwh: 3500,
        pv_kwh: 7000,
        direct_kwh: 2000,
        discharge_kwh: 1500,
        grid_import_kwh: 500,
        export_kwh: 3500,
        battery_loss_kwh: 200,
        clipped_kwh: 50,
        self_consumption: 0.5,
        useful_self_consumption: 0.48
      }
    }
  };
}

function createAppContext(options = {}) {
  const elements = new Map();

  function makeEl(tag = 'div', id = '') {
    const classes = new Set();
    const attrs = new Map();
    const listeners = new Map();
    const children = [];
    let parent = null;

    const el = {
      tagName: tag.toUpperCase(),
      id: id || '',
      children,
      get parentElement() { return parent; },
      get parentNode() { return parent; },
      get firstChild() { return children[0] || null; },
      get nextSibling() {
        if (!parent) return null;
        const idx = parent.children.indexOf(this);
        return idx !== -1 && idx + 1 < parent.children.length ? parent.children[idx + 1] : null;
      },
      get className() { return Array.from(classes).join(' '); },
      set className(val) {
        classes.clear();
        if (val) String(val).split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
      },
      classList: {
        add(c) { classes.add(c); },
        remove(c) { classes.delete(c); },
        contains(c) { return classes.has(c); },
        toggle(c, force) {
          if (force === undefined) {
            if (classes.has(c)) classes.delete(c); else classes.add(c);
          } else if (force) {
            classes.add(c);
          } else {
            classes.delete(c);
          }
          return classes.has(c);
        }
      },
      hidden: false,
      textContent: '',
      innerHTML: '',
      value: '',
      style: {},
      dataset: {},
      options: [],
      addEventListener(event, fn) {
        if (!listeners.has(event)) listeners.set(event, []);
        listeners.get(event).push(fn);
      },
      removeEventListener(event, fn) {
        if (listeners.has(event)) {
          listeners.set(event, listeners.get(event).filter(f => f !== fn));
        }
      },
      dispatchEvent(event) {
        const list = listeners.get(event.type) || [];
        list.forEach(fn => fn(event));
      },
      setAttribute(k, v) { attrs.set(k, String(v)); },
      getAttribute(k) { return attrs.get(k) ?? null; },
      hasAttribute(k) { return attrs.has(k); },
      removeAttribute(k) { attrs.delete(k); },
      closest(sel) {
        let cur = this;
        while (cur) {
          if (sel && cur.tagName && cur.tagName.toLowerCase() === sel.toLowerCase()) return cur;
          cur = cur.parentElement;
        }
        return makeEl(sel || 'div');
      },
      validity: { valid: true },
      querySelector(sel) {
        if (sel.startsWith('#')) {
          const tid = sel.slice(1);
          return elements.get(tid) || null;
        }
        if (sel.startsWith('.')) {
          const cls = sel.slice(1);
          const inChild = children.find(c => (c.classList && c.classList.contains(cls)) || c.className.includes(cls));
          if (inChild) return inChild;
          for (const ch of children) {
            const nested = ch.querySelector ? ch.querySelector(sel) : null;
            if (nested) return nested;
          }
          return null;
        }
        return null;
      },
      querySelectorAll(sel) {
        const out = [];
        for (const child of children) {
          if (sel.startsWith('.') && child.classList?.contains?.(sel.slice(1))) out.push(child);
        }
        return out;
      },
      prepend(node) {
        if (node) {
          node.parent = this;
          children.unshift(node);
        }
      },
      appendChild(node) {
        if (node) {
          node.parent = this;
          children.push(node);
        }
      },
      insertBefore(newNode, refNode) {
        if (!newNode) return;
        newNode.parent = this;
        const idx = children.indexOf(refNode);
        if (idx !== -1) children.splice(idx, 0, newNode);
        else children.push(newNode);
      },
      focus() {}
    };

    if (id) elements.set(id, el);
    return el;
  }

  // Pre-instantiate essential elements
  const foldEl = makeEl('details', 'dayhourExplorerFold');
  const dayhourModeEl = makeEl('select', 'dayhourMode');
  dayhourModeEl.value = 'forecast_2027';
  const dayhourPeriodEl = makeEl('select', 'dayhourPeriod');
  dayhourPeriodEl.value = 'day';
  const dayhourMonthEl = makeEl('select', 'dayhourMonth');
  dayhourMonthEl.value = '1';
  const dayhourScenarioEl = makeEl('select', 'dayhourScenario');
  dayhourScenarioEl.value = 'electrified';
  const weatherRepresentativeEl = makeEl('select', 'weatherRepresentative');
  weatherRepresentativeEl.value = 'median';
  const weatherYearEl = makeEl('select', 'weatherYear');
  weatherYearEl.value = '2015';
  weatherYearEl.options = [{ value: '2015' }, { value: '2016' }];
  const weatherDateEl = makeEl('input', 'weatherDate');
  weatherDateEl.value = '2027-01-01';
  const hourSliderEl = makeEl('input', 'hourSlider');
  hourSliderEl.value = '12';
  const selectedHourLabelEl = makeEl('span', 'selectedHourLabel');
  selectedHourLabelEl.textContent = '12:00–13:00';
  const previousHourEl = makeEl('button', 'previousHour');
  const nextHourEl = makeEl('button', 'nextHour');

  const dayhourStatusEl = makeEl('p', 'dayhourStatus');
  foldEl.appendChild(dayhourStatusEl);
  const hourSummaryEl = makeEl('div', 'hourSummary');
  foldEl.appendChild(hourSummaryEl);
  const dayhourUncertaintyEl = makeEl('div', 'dayhourUncertainty');
  foldEl.appendChild(dayhourUncertaintyEl);
  const dailySolarFlowEl = makeEl('div', 'dailySolarFlow');
  foldEl.appendChild(dailySolarFlowEl);
  const dailyGridBatteryEl = makeEl('div', 'dailyGridBattery');
  foldEl.appendChild(dailyGridBatteryEl);
  const dailySocEl = makeEl('div', 'dailySoc');
  foldEl.appendChild(dailySocEl);
  const dayTotalsEl = makeEl('div', 'dayTotals');
  foldEl.appendChild(dayTotalsEl);

  const dayhourHpSectionEl = makeEl('div', 'dayhourHpSection');
  dayhourHpSectionEl.hidden = true;
  const dayhourHpStatusEl = makeEl('p', 'dayhourHpStatus');
  dayhourHpSectionEl.appendChild(dayhourHpStatusEl);
  const dayhourHpHourCardsEl = makeEl('div', 'dayhourHpHourCards');
  dayhourHpSectionEl.appendChild(dayhourHpHourCardsEl);
  const dayhourHpTableEl = makeEl('div', 'dayhourHpTable');
  dayhourHpSectionEl.appendChild(dayhourHpTableEl);
  foldEl.appendChild(dayhourHpSectionEl);

  const statusEl = makeEl('div', 'status');
  const errorEl = makeEl('div', 'error');
  errorEl.hidden = true;

  const doc = {
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, makeEl('div', id));
      }
      return elements.get(id);
    },
    createElement(tag) {
      return makeEl(tag);
    },
    querySelector(sel) {
      if (sel.startsWith('#')) return doc.getElementById(sel.slice(1));
      if (sel.startsWith('[data-setting="')) {
        const m = sel.match(/\[data-setting="([^"]+)"\]/);
        if (m) {
          for (const el of elements.values()) {
            if (el.dataset?.setting === m[1]) return el;
          }
        }
      }
      return null;
    },
    querySelectorAll(sel) {
      return [];
    },
    addEventListener() {},
    removeEventListener() {}
  };

  const win = {
    addEventListener() {},
    removeEventListener() {},
    location: { hash: '' },
    matchMedia: () => ({ matches: false }),
    innerWidth: 1024,
    innerHeight: 768
  };
  win.window = win;
  win.document = doc;

  const dayhourCalls = [];
  const evaluateCalls = [];
  const pendingDayhours = [];
  const pendingEvaluations = [];
  let autoResolveDayhour = true;
  let autoResolveEvaluate = true;

  let pendingTimers = [];
  let timerSeq = 1;
  function fakeSetTimeout(fn, ms) {
    const tid = timerSeq++;
    pendingTimers.push({ id: tid, fn, ms });
    return tid;
  }
  function fakeClearTimeout(tid) {
    const idx = pendingTimers.findIndex(t => t.id === tid);
    if (idx !== -1) pendingTimers.splice(idx, 1);
  }
  async function flushTimers() {
    const list = [...pendingTimers];
    pendingTimers.length = 0;
    for (const t of list) {
      const res = t.fn();
      if (res && typeof res.then === 'function') await res;
    }
  }

  const ctx = {
    window: win,
    document: doc,
    structuredClone,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout: fakeSetTimeout,
    clearTimeout: fakeClearTimeout,
    fetch: async (pathUrl, fetchOpts) => {
      if (pathUrl === '/api/dayhour') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        dayhourCalls.push(body);
        const scenario = body.scenario || body.settings?.load_scenario || 'electrified';
        const backendSettings = { ...(body.settings || {}), load_scenario: scenario };
        const fixtureOptions = {
          settings: backendSettings,
          scenario,
          mode: body.mode || 'forecast_2027',
          period: body.period || 'day',
          month: body.month,
          date: body.date,
          weather_year: body.weather_year,
          representative: body.representative,
          battery_capacity_kwh: backendSettings.battery_kwh ?? 10
        };
        if (autoResolveDayhour) {
          return {
            ok: true,
            json: async () => makeDayhourFixture(fixtureOptions)
          };
        }
        return new Promise((resolve, reject) => {
          pendingDayhours.push({
            body,
            resolve: (customResult) => resolve({
              ok: true,
              json: async () => customResult || makeDayhourFixture(fixtureOptions)
            }),
            reject: (err) => reject(err || new Error('Network error on /api/dayhour'))
          });
        });
      }
      if (pathUrl === '/api/evaluate') {
        const body = fetchOpts.body ? JSON.parse(fetchOpts.body) : {};
        evaluateCalls.push(body);
        if (autoResolveEvaluate) {
          return {
            ok: true,
            json: async () => makeEvaluateFixture(body.settings)
          };
        }
        return new Promise((resolve, reject) => {
          pendingEvaluations.push({
            body,
            resolve: (customResult) => resolve({
              ok: true,
              json: async () => customResult || makeEvaluateFixture(body.settings)
            }),
            reject: (err) => reject(err || new Error('Network error on /api/evaluate'))
          });
        });
      }
      return { ok: true, json: async () => ({}) };
    }
  };

  const context = vm.createContext(ctx);
  vm.runInContext(appSource, context);

  return {
    context,
    run: (code) => vm.runInContext(code, context),
    fold: foldEl,
    dayhourStatus: dayhourStatusEl,
    hourSummary: hourSummaryEl,
    dailySolarFlow: dailySolarFlowEl,
    dailyGridBattery: dailyGridBatteryEl,
    dailySoc: dailySocEl,
    dayTotals: dayTotalsEl,
    dayhourHpSection: dayhourHpSectionEl,
    dayhourCalls,
    evaluateCalls,
    pendingDayhours,
    pendingEvaluations,
    setAutoResolveDayhour: (val) => { autoResolveDayhour = val; },
    setAutoResolveEvaluate: (val) => { autoResolveEvaluate = val; },
    resolvePendingDayhour: (idx, res) => {
      const p = pendingDayhours[idx];
      if (p) p.resolve(res);
    },
    rejectPendingDayhour: (idx, err) => {
      const p = pendingDayhours[idx];
      if (p) p.reject(err);
    },
    resolvePendingEvaluation: (idx, res) => {
      const p = pendingEvaluations[idx];
      if (p) p.resolve(res);
    },
    rejectPendingEvaluation: (idx, err) => {
      const p = pendingEvaluations[idx];
      if (p) p.reject(err);
    },
    flushTimers,
    makeEl,
    elements
  };
}

function initScenario(env, overrides = {}) {
  const initialSettings = {
    load_scenario: 'electrified',
    planning_heat_pump: false,
    heating_schedule_mode: 'scheduled',
    timezone_mode: 'civil',
    ac_heating_additional_demand_pct: 0,
    ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }],
    battery_kwh: 10,
    ...overrides
  };
  env.run(`
    schema = {
      defaults: ${JSON.stringify(initialSettings)},
      records: [
        { name: 'load_scenario', unit: 'scenario', group: 'loads' },
        { name: 'planning_heat_pump', unit: 'boolean', group: 'heat_pump' },
        { name: 'heating_schedule_mode', unit: 'mode', group: 'loads' },
        { name: 'timezone_mode', unit: 'mapping' },
        { name: 'ac_heating_additional_demand_pct', unit: 'pct', group: 'loads' },
        { name: 'ac_heating_windows', unit: 'windows', group: 'loads' },
        { name: 'battery_kwh', unit: 'kWh', group: 'storage' }
      ]
    };
    settings = deepClone(schema.defaults);
    quote = { line_items: [] };
    current = ${JSON.stringify(makeEvaluateFixture(initialSettings))};
    loanPending = false;
    acceptedInputFingerprint = inputFingerprint(settings, quote);
    dayhourData = null;
    dayhourOutdated = false;
    acDemandPctDraft = null;
    acDemandPctError = null;
    acScheduleDraft = null;
    acScheduleDraftInvalid = false;
  `);
  if (env.fold) {
    env.fold.className = '';
  }
  return initialSettings;
}

test('deferred stale /api/dayhour success cannot render or overwrite active UI after valid schedule edit', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // Set deferred dayhour
  env.setAutoResolveDayhour(false);
  const p1 = env.run('refreshDayhour()');
  assert.equal(env.dayhourCalls.length, 1, 'First dayhour request dispatched');

  // While in flight, user makes a valid schedule edit (e.g. shifts morning window)
  const newWindows = [{ id: 'w1', start_minute: 480, end_minute: 600 }];
  env.run(`editSetting('ac_heating_windows', ${JSON.stringify(newWindows)}, 'ac_editor')`);

  // Assert dayhour was marked outdated by the edit
  assert.equal(env.run('dayhourOutdated'), true, 'dayhourOutdated set to true');

  // Now server responds to the deferred old request with old data
  const oldDayhourFixture = makeDayhourFixture({
    settings: initSettings,
    pv_kwh: 99.9,
    load_kwh: 88.8
  });
  env.resolvePendingDayhour(0, oldDayhourFixture);
  await p1;

  // Assert old response was NOT rendered
  assert.equal(env.run('dayhourData'), null, 'Stale dayhour response was discarded and not stored in dayhourData');
  assert.equal(env.hourSummary.innerHTML, '', 'hourSummary was not populated with stale data');
  assert.equal(env.dailySolarFlow.innerHTML, '', 'dailySolarFlow was not populated with stale data');
  assert.equal(env.run('dayhourOutdated'), true, 'Outdated state is preserved');
});

test('deferred stale /api/dayhour error cannot clear current UI after valid percentage edit', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // First, render valid dayhour data on screen
  const initialFixture = makeDayhourFixture({ settings: initSettings, load_kwh: 1.5 });
  env.run(`renderDayhour(${JSON.stringify(initialFixture)})`);
  assert.ok(env.hourSummary.innerHTML.includes('Household load'), 'Initial hourly UI rendered');
  assert.ok(env.dayTotals.innerHTML.includes('Selected day total'), 'Day totals rendered');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'Clean state before edit');

  // Start new refreshDayhour deferred
  env.setAutoResolveDayhour(false);
  const p1 = env.run('refreshDayhour()');
  assert.equal(env.dayhourCalls.length, 1);

  // User edits percentage setting to 25
  env.run(`editSetting('ac_heating_additional_demand_pct', 25, 'controls')`);
  assert.equal(env.run('dayhourOutdated'), true);
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Fold has stale overlay after edit');
  const noticeEl = env.elements.get('dayhourStaleNotice');
  assert.ok(noticeEl && !noticeEl.hidden, 'Stale notice visible after edit');

  // Deferred dayhour fails with network/server error
  env.rejectPendingDayhour(0, new Error('500 Internal Server Error'));
  await p1;

  // Stale error must NOT clear current UI or overwrite dayhourStatus with error message
  assert.ok(env.run('dayhourData') !== null, 'dayhourData was not cleared to null by stale error');
  assert.ok(env.hourSummary.innerHTML.includes('Household load'), 'hourSummary was not wiped by stale error');
  assert.ok(env.dayTotals.innerHTML.includes('Selected day total'), 'dayTotals was not wiped by stale error');
  assert.ok(!env.dayhourStatus.textContent.includes('500 Internal Server Error'), 'dayhourStatus does not show stale error message');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Visible outdated state preserved');
});

test('deferred stale /api/dayhour response cannot render after invalid schedule draft (retaining same canonical settings)', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  env.setAutoResolveDayhour(false);
  const p1 = env.run('refreshDayhour()');
  assert.equal(env.dayhourCalls.length, 1);

  // User types invalid text into start time input ('99:00')
  // Note: Canonical settings.ac_heating_windows retains the same valid settings
  env.run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(env.run('hasInvalidAcScheduleDraft()'), true, 'Draft is invalid');
  assert.equal(env.run('dayhourOutdated'), true, 'Dayhour invalidated by invalid draft');
  assert.deepEqual(env.run('settings.ac_heating_windows'), initSettings.ac_heating_windows, 'Canonical settings retained');

  // Server responds to earlier in-flight request
  const oldFixture = makeDayhourFixture({ settings: initSettings, pv_kwh: 77.7 });
  env.resolvePendingDayhour(0, oldFixture);
  await p1;

  // Assert old response was not rendered
  assert.equal(env.run('dayhourData'), null, 'Stale response not accepted');
  assert.equal(env.hourSummary.innerHTML, '', 'hourSummary remains clean');
  assert.equal(env.run('dayhourOutdated'), true, 'Outdated state preserved');
});

test('deferred stale /api/dayhour error cannot clear current UI after invalid percentage draft (retaining same canonical settings)', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // Initial dayhour data rendered
  const initialFixture = makeDayhourFixture({ settings: initSettings, pv_kwh: 3.2 });
  env.run(`renderDayhour(${JSON.stringify(initialFixture)})`);
  assert.ok(env.hourSummary.innerHTML.includes('PV production'));

  // Start new refreshDayhour deferred
  env.setAutoResolveDayhour(false);
  const p1 = env.run('refreshDayhour()');

  // User enters negative percentage ('-5')
  env.run("handleAcHeatingDemandPctInput('-5')");
  assert.equal(env.run('hasInvalidAcDemandPctDraft()'), true, 'Demand pct draft is invalid');
  assert.equal(env.run('settings.ac_heating_additional_demand_pct'), 0, 'Canonical pct retained');
  assert.equal(env.run('dayhourOutdated'), true, 'Dayhour marked outdated');

  // In-flight request rejects with connection failure
  env.rejectPendingDayhour(0, new Error('ECONNRESET'));
  await p1;

  // Current UI must NOT be cleared
  assert.ok(env.run('dayhourData') !== null, 'Existing data retained');
  assert.ok(env.hourSummary.innerHTML.includes('PV production'), 'Existing cards retained');
  assert.ok(!env.dayhourStatus.textContent.includes('ECONNRESET'), 'Stale error ignored');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay active');
});

test('remote shared changes (applySharedState) invalidate in-flight dayhour response and mark dayhour outdated', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  env.setAutoResolveDayhour(false);
  const p1 = env.run('refreshDayhour()');

  // Remote changes arrive via sync
  const remoteSettings = { ...initSettings, battery_kwh: 15 };
  env.run(`
    applySharedState({
      settings: ${JSON.stringify(remoteSettings)},
      quote: { line_items: [] },
      defaults: schema.defaults,
      records: schema.records
    });
  `);
  assert.equal(env.run('dayhourOutdated'), true, 'Remote change marked dayhour outdated');

  // Deferred dayhour from previous settings arrives
  const oldFixture = makeDayhourFixture({ settings: initSettings });
  env.resolvePendingDayhour(0, oldFixture);
  await p1;

  // Must not render old result
  assert.equal(env.run('dayhourData'), null, 'Old result dropped');
  assert.equal(env.hourSummary.innerHTML, '', 'No stale hourly cards rendered');
});

test('newer accepted evaluation allows fresh /api/dayhour result to render and clears outdated state', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // 1. Initial dayhour data rendered and up to date
  const fixture1 = makeDayhourFixture({ settings: initSettings, pv_kwh: 1.0 });
  env.run(`renderDayhour(${JSON.stringify(fixture1)})`);
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'Initially clean');

  // 2. User edits schedule -> invalidated
  const newWindows = [{ id: 'w1', start_minute: 420, end_minute: 540 }];
  env.run(`editSetting('ac_heating_windows', ${JSON.stringify(newWindows)}, 'ac_editor')`);
  assert.equal(env.run('dayhourOutdated'), true, 'Outdated after edit');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay displayed');

  // 3. Evaluation executes and succeeds for the new settings
  const updatedSettings = { ...initSettings, ac_heating_windows: newWindows };
  env.run(`
    current = ${JSON.stringify(makeEvaluateFixture(updatedSettings))};
    loanPending = false;
    acceptedInputFingerprint = inputFingerprint(settings, quote);
  `);

  // 4. Production refreshDayhour() runs for the new accepted scenario
  env.setAutoResolveDayhour(false);
  const pFresh = env.run('refreshDayhour()');

  // Assert payload strictly matches the accepted current.result.settings
  const lastCall = env.dayhourCalls[env.dayhourCalls.length - 1];
  assert.deepEqual(lastCall.settings.ac_heating_windows, newWindows, 'Payload sent new accepted settings');

  // 5. Server responds with fresh dayhour simulation for updatedSettings
  const fixture2 = makeDayhourFixture({ settings: updatedSettings, pv_kwh: 4.5 });
  env.resolvePendingDayhour(env.pendingDayhours.length - 1, fixture2);
  await pFresh;

  // 6. Assert fresh result rendered and outdated state cleared
  assert.equal(env.run('dayhourOutdated'), false, 'dayhourOutdated cleared');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'Stale overlay removed');
  const noticeEl = env.elements.get('dayhourStaleNotice');
  assert.ok(!noticeEl || noticeEl.hidden || noticeEl.style.display === 'none', 'Stale notice hidden');
  assert.equal(env.run('dayhourData.settings.ac_heating_windows[0].start_minute'), 420, 'Rendered data matches new scenario');
});

test('hourly selection (#hourSlider, previousHour, nextHour) still works while in outdated state', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // Render initial dayhour
  const initialFixture = makeDayhourFixture({ settings: initSettings });
  env.run(`renderDayhour(${JSON.stringify(initialFixture)})`);

  // Invalidate via invalid percentage draft
  env.run("handleAcHeatingDemandPctInput('-20')");
  assert.equal(env.run('dayhourOutdated'), true, 'Dayhour is outdated');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay active');

  // Scrub hourSlider to hour 8
  const sliderEl = env.elements.get('hourSlider');
  sliderEl.value = '8';
  env.run("$('hourSlider').oninput()");

  // Hourly selection must update
  const labelEl = env.elements.get('selectedHourLabel');
  assert.equal(labelEl.textContent, '08:00–09:00', 'Hour label updated to selected hour');
  assert.ok(env.hourSummary.innerHTML.includes('08:00–09:00') || env.hourSummary.innerHTML.includes('Household load'), 'Hour cards updated');

  // Outdated state must REMAIN active
  assert.equal(env.run('dayhourOutdated'), true, 'Outdated state remains true after hour scrubbing');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay remains active after hour scrubbing');
  const noticeEl = env.elements.get('dayhourStaleNotice');
  assert.ok(noticeEl && !noticeEl.hidden && noticeEl.style.display !== 'none', 'Stale notice remains visible');

  // Click nextHour
  env.run("$('nextHour').onclick()");
  assert.equal(sliderEl.value, '9', 'Slider advanced to hour 9');
  assert.equal(labelEl.textContent, '09:00–10:00', 'Label updated to 09:00–10:00');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay preserved after nextHour');

  // Click previousHour
  env.run("$('previousHour').onclick()");
  assert.equal(sliderEl.value, '8', 'Slider decremented to hour 8');
  assert.equal(labelEl.textContent, '08:00–09:00', 'Label restored to 08:00–09:00');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay preserved after previousHour');
});

test('request payload strictly uses accepted current.result.settings, never unevaluated drafts', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // User modifies local settings directly without evaluate completing
  env.run("settings.panel_watts = 550; settings.battery_kwh = 99;");

  // Call refreshDayhour()
  env.run('refreshDayhour()');

  assert.equal(env.dayhourCalls.length, 1);
  const sentSettings = env.dayhourCalls[0].settings;

  // Sent settings must be accepted current.result.settings (not the unevaluated local modifications)
  assert.equal(sentSettings.battery_kwh, initSettings.battery_kwh, 'Used accepted current.result.settings battery_kwh');
  assert.notEqual(sentSettings.battery_kwh, 99, 'Did not send unevaluated local draft settings');
});

test('AC inactive / continuous scoping: invalid AC draft does not invalidate dayhour when AC heating is inactive or continuous', async () => {
  const env = createAppContext();

  // Test 1: baseline load scenario (AC heating inactive)
  initScenario(env, { load_scenario: 'baseline' });
  const baseFixture = makeDayhourFixture({ settings: { load_scenario: 'baseline' } });
  env.run(`renderDayhour(${JSON.stringify(baseFixture)})`);
  assert.equal(env.run('dayhourOutdated'), false);

  // Invalid demand pct in baseline
  env.run("handleAcHeatingDemandPctInput('-99')");
  assert.equal(env.run('dayhourOutdated'), false, 'Baseline scenario dayhour not invalidated by AC draft');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'No stale overlay on baseline');

  // Test 2: continuous schedule mode
  initScenario(env, { heating_schedule_mode: 'continuous' });
  const contFixture = makeDayhourFixture({ settings: { heating_schedule_mode: 'continuous' } });
  env.run(`renderDayhour(${JSON.stringify(contFixture)})`);
  assert.equal(env.run('dayhourOutdated'), false);

  // Invalid schedule time in continuous mode
  env.run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
  assert.equal(env.run('dayhourOutdated'), false, 'Continuous mode schedule draft does not invalidate dayhour');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'No stale overlay on continuous schedule edit');

  // But invalid percentage in continuous DOES govern and invalidate
  env.run("handleAcHeatingDemandPctInput('-50')");
  assert.equal(env.run('dayhourOutdated'), true, 'Continuous mode percentage draft DOES govern and invalidate dayhour');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay appears for invalid pct in continuous mode');

  // Test 3: planning_heat_pump = true (air-to-water HP, AC inactive)
  initScenario(env, { planning_heat_pump: true });
  const hpFixture = makeDayhourFixture({ settings: { planning_heat_pump: true } });
  env.run(`renderDayhour(${JSON.stringify(hpFixture)})`);
  assert.equal(env.run('dayhourOutdated'), false);

  env.run("handleAcHeatingDemandPctInput('-100')");
  assert.equal(env.run('dayhourOutdated'), false, 'Heat pump planning scenario dayhour not invalidated by AC draft');
});

test('day and month period views correctly display and clear visible outdated state', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // 1. Day view outdated behavior
  const dayFixture = makeDayhourFixture({ settings: initSettings, period: 'day' });
  env.run(`renderDayhour(${JSON.stringify(dayFixture)})`);
  assert.equal(env.fold.classList.contains('stale-overlay'), false);

  // Invalidate via edit
  env.run("editSetting('ac_heating_additional_demand_pct', 15, 'controls')");
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Day view has stale overlay');
  assert.ok(env.elements.get('dayhourStaleNotice') && !env.elements.get('dayhourStaleNotice').hidden, 'Day view has stale notice');

  // 2. Month view outdated behavior
  // Switch to month view while still outdated
  const monthFixture = makeDayhourFixture({ settings: initSettings, period: 'month', month: 2 });
  env.run(`renderDayhour(${JSON.stringify(monthFixture)})`);
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Month view retains stale overlay while outdated');
  assert.ok(env.elements.get('dayhourStaleNotice') && !env.elements.get('dayhourStaleNotice').hidden, 'Month view retains stale notice');
  assert.ok(env.dayTotals.innerHTML.includes('Monthly energy summary'), 'Month totals table rendered');

  // Accept new evaluation
  const newSettings = { ...initSettings, ac_heating_additional_demand_pct: 15 };
  env.run(`
    current = ${JSON.stringify(makeEvaluateFixture(newSettings))};
    loanPending = false;
    acceptedInputFingerprint = inputFingerprint(settings, quote);
  `);
  const freshMonthFixture = makeDayhourFixture({ settings: newSettings, period: 'month', month: 2 });
  env.run(`renderDayhour(${JSON.stringify(freshMonthFixture)})`);
  assert.equal(env.run('dayhourOutdated'), false, 'Outdated cleared on accepted month render');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'Month view stale overlay removed');
  assert.ok(env.elements.get('dayhourStaleNotice').hidden, 'Month view stale notice hidden');
});

test('selection changes (changing date/month) discard previous in-flight dayhour request', async () => {
  const env = createAppContext();
  initScenario(env);

  env.setAutoResolveDayhour(false);

  // Request 1: day 2027-01-05
  env.elements.get('weatherDate').value = '2027-01-05';
  const p1 = env.run('refreshDayhour()');
  assert.equal(env.dayhourCalls.length, 1);
  assert.equal(env.dayhourCalls[0].date, '2027-01-05');

  // User changes date again before request 1 returns: day 2027-01-06
  env.elements.get('weatherDate').value = '2027-01-06';
  const p2 = env.run('refreshDayhour()');
  assert.equal(env.dayhourCalls.length, 2);
  assert.equal(env.dayhourCalls[1].date, '2027-01-06');

  // Request 1 finishes (stale)
  const fixtureDay5 = makeDayhourFixture({ date: '2027-01-05', pv_kwh: 1.1 });
  env.resolvePendingDayhour(0, fixtureDay5);
  await p1;

  // Stale request 1 must not render
  assert.equal(env.run('dayhourData'), null, 'Request 1 dropped as stale');

  // Request 2 finishes (current)
  const fixtureDay6 = makeDayhourFixture({ date: '2027-01-06', pv_kwh: 2.2 });
  env.resolvePendingDayhour(1, fixtureDay6);
  await p2;

  // Request 2 must render
  assert.equal(env.run('dayhourData.date'), '2027-01-06', 'Request 2 rendered successfully');
  assert.ok(env.dayhourStatus.textContent.includes('2027-01-06'), 'Status reflects day 6');
});
test('manager repro 1: evaluate acceptance with battery_kwh edit preserves stale dayhour until matching hourly response', async () => {
  const env = createAppContext();
  const initial = initScenario(env);
  const initialFixture = makeDayhourFixture({ settings: initial });
  env.run(`renderDayhour(${JSON.stringify(initialFixture)})`);
  assert.equal(env.run('isDayhourOutdated()'), false);

  env.run("editSetting('battery_kwh', 15, 'controls')");
  // Accept an evaluate result with battery_kwh=15 (current updated, loanPending=false, acceptedInputFingerprint=inputFingerprint(settings,quote))
  const updatedSettings = { ...initial, battery_kwh: 15 };
  env.run(`
    current = ${JSON.stringify(makeEvaluateFixture(updatedSettings))};
    loanPending = false;
    acceptedInputFingerprint = inputFingerprint(settings, quote);
    acceptDayhour(current.result);
  `);

  assert.equal(env.run('dayhourData.settings.battery_kwh'), 10, 'dayhourData settings remains 10');
  assert.equal(env.run('isDayhourOutdated()'), true, 'isDayhourOutdated() returns true');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'stale overlay is visible');

  // When matching hourly response arrives for battery_kwh=15
  const matchingDayhour = makeDayhourFixture({ settings: updatedSettings, battery_capacity_kwh: 15 });
  env.run(`renderDayhour(${JSON.stringify(matchingDayhour)})`);
  assert.equal(env.run('isDayhourOutdated()'), false, 'outdated state cleared once matching hourly arrives');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'stale overlay removed');
});

test('manager repro 2: legitimate comparison scenario selection with backend-shaped settings is not marked outdated', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env, { load_scenario: 'electrified' });

  // Initial hourly rendered for electrified
  const initFixture = makeDayhourFixture({
    settings: { ...initSettings, load_scenario: 'electrified' },
    scenario: 'electrified',
    mode: 'forecast_2027',
    period: 'day'
  });
  env.run(`renderDayhour(${JSON.stringify(initFixture)})`);
  assert.equal(env.run('isDayhourOutdated()'), false);

  // User selects baseline in hourly scenario selector
  env.elements.get('dayhourScenario').value = 'baseline';

  // Backend /api/dayhour response shaped by server.py / engine.dayhour_forecast:
  // settings.load_scenario='baseline', scenario='baseline'
  // while annual scenario settings.load_scenario remains 'electrified'
  const baselineFixture = makeDayhourFixture({
    settings: { ...initSettings, load_scenario: 'baseline' },
    scenario: 'baseline',
    mode: 'forecast_2027',
    period: 'day'
  });

  env.run(`renderDayhour(${JSON.stringify(baselineFixture)})`);
  assert.equal(env.run('isDayhourOutdated()'), false, 'Legitimate hourly scenario comparison is not stale');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'No stale overlay on legitimate baseline comparison');

  // Also test historical mode and month period with backend-shaped semantics
  env.elements.get('dayhourMode').value = 'historical';
  env.elements.get('weatherYear').value = '2016';
  env.elements.get('dayhourPeriod').value = 'month';
  env.elements.get('dayhourMonth').value = '3';

  const historicalMonthFixture = makeDayhourFixture({
    settings: { ...initSettings, load_scenario: 'baseline' },
    scenario: 'baseline',
    mode: 'historical',
    weather_year: 2016,
    period: 'month',
    month: 3
  });
  env.run(`renderDayhour(${JSON.stringify(historicalMonthFixture)})`);
  assert.equal(env.run('isDayhourOutdated()'), false, 'Historical month baseline comparison is not stale');
  assert.equal(env.fold.classList.contains('stale-overlay'), false);
});

test('production recompute() regression: old hourly data stays visibly stale after evaluate acceptance, old errors cannot clear newer UI, matching arrival clears stale', async () => {
  const env = createAppContext();
  const initSettings = initScenario(env);

  // 1. Initial dayhour rendered and up-to-date
  const initFixture = makeDayhourFixture({ settings: initSettings, pv_kwh: 2.0, battery_capacity_kwh: 10 });
  env.run(`renderDayhour(${JSON.stringify(initFixture)})`);
  assert.equal(env.run('isDayhourOutdated()'), false, 'Clean initially');
  assert.equal(env.fold.classList.contains('stale-overlay'), false);

  // 2. Configure deferred evaluate and deferred dayhour
  env.setAutoResolveEvaluate(false);
  env.setAutoResolveDayhour(false);

  // User edits battery_kwh setting
  env.run("editSetting('battery_kwh', 15, 'controls')");
  assert.equal(env.run('isDayhourOutdated()'), true, 'Outdated immediately on edit');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Overlay visible on edit');

  // 3. Flush timers so production recompute() triggers /api/evaluate
  const recomputePromise = env.flushTimers();
  assert.equal(env.evaluateCalls.length, 1, 'Evaluate call dispatched by recompute');
  assert.equal(env.pendingEvaluations.length, 1);

  // 4. Resolve /api/evaluate with updated settings (battery_kwh: 15)
  const updatedEvaluate = makeEvaluateFixture({ ...initSettings, battery_kwh: 15 });
  env.resolvePendingEvaluation(0, updatedEvaluate);
  await recomputePromise;

  // Evaluate is now accepted!
  assert.equal(env.run('loanPending'), false, 'loanPending cleared by evaluate acceptance');
  assert.equal(env.run('current.result.settings.battery_kwh'), 15, 'current.result has new settings');

  // Production recompute() has dispatched refreshDayhour() for the new settings
  assert.equal(env.dayhourCalls.length, 1, 'Dayhour dispatched by production recompute');
  assert.deepEqual(env.dayhourCalls[0].settings.battery_kwh, 15, 'Dispatched payload has new accepted settings');

  // PROVE: Old rendered hourly data stays visibly stale after evaluate acceptance
  assert.equal(env.run('dayhourData.settings.battery_kwh'), 10, 'dayhourData still holds old rendered data');
  assert.equal(env.run('isDayhourOutdated()'), true, 'Hourly view stays visibly stale after evaluate acceptance');
  assert.equal(env.run('dayhourOutdated'), true, 'dayhourOutdated remains true');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay remains displayed');
  const noticeEl = env.elements.get('dayhourStaleNotice');
  assert.ok(noticeEl && !noticeEl.hidden && noticeEl.style.display !== 'none', 'Stale notice visible');
  assert.ok(env.hourSummary.innerHTML.includes('Household load'), 'Old hourly summary cards remain rendered');

  // 5. Old in-flight error rejects; old error cannot clear newer UI
  const oldPending = env.pendingDayhours[0];
  const dayhourPromise = env.run("refreshDayhour()");
  oldPending.reject(new Error('500 Old request failed'));
  await Promise.resolve();

  // PROVE: Old error cannot clear newer UI
  assert.ok(env.run('dayhourData') !== null, 'dayhourData not wiped to null by old error');
  assert.ok(env.hourSummary.innerHTML.includes('Household load'), 'hourSummary not wiped by old error');
  assert.ok(!env.dayhourStatus.textContent.includes('500 Old request failed'), 'Old error message not displayed');
  assert.equal(env.fold.classList.contains('stale-overlay'), true, 'Stale overlay retained');

  // 6. Now matching refreshDayhour() response for the new accepted settings arrives
  const matchingDayhourFixture = makeDayhourFixture({
    settings: { ...initSettings, battery_kwh: 15 },
    battery_capacity_kwh: 15,
    pv_kwh: 3.5
  });
  env.resolvePendingDayhour(env.pendingDayhours.length - 1, matchingDayhourFixture);
  await dayhourPromise;

  // PROVE: Only matching hourly arrival clears stale state and renders new data
  assert.equal(env.run('isDayhourOutdated()'), false, 'Outdated cleared on matching hourly arrival');
  assert.equal(env.run('dayhourOutdated'), false, 'dayhourOutdated set to false');
  assert.equal(env.fold.classList.contains('stale-overlay'), false, 'Stale overlay removed');
  assert.ok(!noticeEl || noticeEl.hidden || noticeEl.style.display === 'none', 'Stale notice hidden');
  assert.equal(env.run('dayhourData.settings.battery_kwh'), 15, 'New hourly data rendered');
});
