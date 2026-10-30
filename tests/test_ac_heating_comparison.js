'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

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
      querySelector(sel) {
        if (sel.startsWith('#')) {
          const tid = sel.slice(1);
          return elements.get(tid) || null;
        }
        if (sel.startsWith('.')) {
          const cls = sel.slice(1);
          const inChild = children.find(c => (c.classList && c.classList.contains(cls)) || c.className === cls);
          if (inChild) return inChild;
          if (this.innerHTML && this.innerHTML.includes(cls)) {
            const found = makeEl('div');
            found.className = cls;
            found.hidden = false;
            return found;
          }
          return null;
        }
        if (sel === '.section-head') {
          if (this.innerHTML && this.innerHTML.includes('section-head')) {
            const head = makeEl('div');
            head.className = 'section-head';
            return head;
          }
          return null;
        }
        return null;
      },
      querySelectorAll() {
        return [];
      },
      prepend(node) {
        children.unshift(node);
        if (node) node.parent = this;
      },
      insertBefore(newNode) {
        children.unshift(newNode);
        if (newNode) newNode.parent = this;
      }
    };

    if (id) elements.set(id, el);
    return el;
  }

  const articleEl = makeEl('article', 'acHeatingComparisonArticle');
  articleEl.hidden = true;
  const panelEl = makeEl('div', 'acHeatingComparisonPanel');
  panelEl.hidden = true;
  articleEl.children.push(panelEl);

  const statusEl = makeEl('div', 'status');
  const errorEl = makeEl('div', 'error');
  errorEl.hidden = true;
  const weatherYearEl = makeEl('select', 'weatherYear');
  weatherYearEl.options = [];
  const defaultConfirmEl = makeEl('dialog', 'defaultConfirm');
  const defaultConfirmTitleEl = makeEl('h2', 'defaultConfirmTitle');
  const defaultConfirmDescriptionEl = makeEl('p', 'defaultConfirmDescription');

  const ui = {
    acHeatingComparisonArticle: articleEl,
    acHeatingComparisonPanel: panelEl,
    status: statusEl,
    error: errorEl,
    weatherYear: weatherYearEl,
    defaultConfirm: defaultConfirmEl,
    defaultConfirmTitle: defaultConfirmTitleEl,
    defaultConfirmDescription: defaultConfirmDescriptionEl
  };

  const doc = {
    getElementById(id) {
      if (ui[id]) return ui[id];
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
      const m = sel.match(/\[data-setting="([^"]+)"\]/);
      if (m) return doc.getElementById(m[1]);
      return null;
    },
    querySelectorAll() {
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

  const ctx = {
    window: win,
    document: doc,
    structuredClone,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    fetch: options.fetch || (async () => ({ ok: true, json: async () => ({}) }))
  };

  const context = vm.createContext(ctx);
  vm.runInContext(appSource, context);

  return {
    context,
    run: (code) => vm.runInContext(code, context),
    article: articleEl,
    panel: panelEl,
    elements
  };
}

test('baseline load scenario hides and clears AC heating comparison', () => {
  const env = createAppContext();
  env.run(`
    settings = { load_scenario: 'baseline', planning_heat_pump: false };
    current = {
      result: {
        settings: { load_scenario: 'baseline', planning_heat_pump: false },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200,
          ac_heating_requested_electricity_kwh: 1200,
          ac_heating_extra_electricity_kwh: 0,
          ac_heating_baseline_useful_kwh: 4560,
          ac_heating_requested_useful_kwh: 4560,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8
        }
      }
    };
    renderAcHeatingComparison();
  `);

  assert.equal(env.panel.hidden, true);
  assert.equal(env.article.hidden, true);
  assert.equal(env.panel.innerHTML, '');
});

test('air-to-water heat pump scenario hides and clears AC heating comparison', () => {
  const env = createAppContext();
  env.run(`
    settings = { load_scenario: 'electrified', planning_heat_pump: true };
    current = {
      result: {
        settings: { load_scenario: 'electrified', planning_heat_pump: true },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200,
          ac_heating_requested_electricity_kwh: 1200,
          ac_heating_extra_electricity_kwh: 0,
          ac_heating_baseline_useful_kwh: 4560,
          ac_heating_requested_useful_kwh: 4560,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8
        }
      }
    };
    renderAcHeatingComparison();
  `);

  assert.equal(env.panel.hidden, true);
  assert.equal(env.article.hidden, true);
  assert.equal(env.panel.innerHTML, '');
});

test('missing required AC fields in payload renders unavailable without inventing 0', () => {
  const env = createAppContext();
  env.run(`
    settings = { load_scenario: 'electrified', planning_heat_pump: false };
    current = {
      result: {
        settings: { load_scenario: 'electrified', planning_heat_pump: false },
        enduses: {
          // Missing ac_heating_baseline_electricity_kwh and others
          cooking_smc: 50
        }
      }
    };
    renderAcHeatingComparison();
  `);

  assert.equal(env.panel.hidden, false);
  assert.equal(env.article.hidden, false);
  assert.ok(env.panel.innerHTML.includes('ac-heating-unavailable'));
  assert.ok(env.panel.innerHTML.includes('AC heating comparison data is unavailable'));
  assert.ok(!env.panel.innerHTML.includes('0 kWh/year'));
});

test('zero values are valid numbers and display cleanly (not treated as missing)', () => {
  const env = createAppContext();
  env.run(`
    settings = { load_scenario: 'electrified', planning_heat_pump: false, ac_heating_additional_demand_pct: 0 };
    current = {
      result: {
        settings: { load_scenario: 'electrified', planning_heat_pump: false, ac_heating_additional_demand_pct: 0 },
        enduses: {
          ac_heating_baseline_electricity_kwh: 0,
          ac_heating_requested_electricity_kwh: 0,
          ac_heating_extra_electricity_kwh: 0,
          ac_heating_baseline_useful_kwh: 0,
          ac_heating_requested_useful_kwh: 0,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 0,
            realizations: []
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  assert.equal(env.panel.hidden, false);
  assert.ok(!env.panel.innerHTML.includes('ac-heating-unavailable'));
  assert.ok(env.panel.innerHTML.includes('0 kWh/year'));
  assert.ok(env.panel.innerHTML.includes('0 kWh thermal/year'));
});

test('0% additional demand: counterfactual baseline, 0 kWh extra, SCOP, and explanations', () => {
  const env = createAppContext();
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 0,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 0,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1200.0,
          ac_heating_extra_electricity_kwh: 0.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 4560.0,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 4.0,
            realizations: [
              {
                weather_year: 2005,
                eligible_schedule_duration_hours: 1460.0,
                max_modeled_interval_average_electrical_demand_kw: 1.25,
                max_modeled_permitted_duration_electrical_requirement_kw: 1.88,
                max_modeled_permitted_duration_useful_heat_requirement_kw: 7.14
              }
            ],
            max_modeled_interval_average_electrical_demand_kw: 1.25,
            max_modeled_permitted_duration_electrical_requirement_kw: 1.88,
            max_modeled_permitted_duration_useful_heat_requirement_kw: 7.14
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;
  assert.equal(env.panel.hidden, false);
  // Baseline AC electricity
  assert.ok(html.includes('1,200 kWh/year'));
  // Same-scenario counterfactual note
  assert.ok(html.includes('Same-scenario counterfactual'));
  assert.ok(html.includes('not historical baseline load scenario'));
  // Requested AC electricity (+0%)
  assert.ok(html.includes('Requested AC heating (+0%)'));
  // Additional electricity
  assert.ok(html.includes('+0 kWh/year') || html.includes('0 kWh/year'));
  // SCOP
  assert.ok(html.includes('3.8'));
  // Useful heat
  assert.ok(html.includes('4,560 kWh thermal/year'));
  // Explanations
  assert.ok(html.includes('Monthly heating budgets are fixed from historical gas consumption'));
  assert.ok(html.includes('weather variations distribute timing across hours'));
  assert.ok(html.includes('equipment adequacy and indoor comfort are unassessed'));
  assert.ok(html.includes('Hourly simulation cannot resolve within-hour instantaneous cycling peaks'));
  assert.ok(html.includes('No equipment sizing or hardware recommendations are provided'));
});

test('25% additional demand: correct requested, additional kWh, useful heat', () => {
  const env = createAppContext();
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 25,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 25,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1500.0,
          ac_heating_extra_electricity_kwh: 300.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 5700.0,
          ac_heating_additional_demand_pct: 25,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 4.0,
            realizations: [
              {
                weather_year: 2005,
                eligible_schedule_duration_hours: 1460.0,
                max_modeled_interval_average_electrical_demand_kw: 1.56,
                max_modeled_permitted_duration_electrical_requirement_kw: 2.34,
                max_modeled_permitted_duration_useful_heat_requirement_kw: 8.89
              }
            ],
            max_modeled_interval_average_electrical_demand_kw: 1.56,
            max_modeled_permitted_duration_electrical_requirement_kw: 2.34,
            max_modeled_permitted_duration_useful_heat_requirement_kw: 8.89
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;
  assert.ok(html.includes('1,200 kWh/year'), 'Contains baseline 1,200 kWh/year');
  assert.ok(html.includes('1,500 kWh/year'), 'Contains requested 1,500 kWh/year');
  assert.ok(html.includes('+300 kWh/year'), 'Contains additional +300 kWh/year');
  assert.ok(html.includes('5,700 kWh thermal/year'), 'Contains requested 5,700 kWh thermal/year');
  assert.ok(html.includes('3.8'), 'Contains SCOP 3.8');
  assert.ok(html.includes('+25%'), 'Contains +25% label');
});

test('arbitrary fractional percentage (12.345%) preserved without rounding or clamping', () => {
  const env = createAppContext();
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 12.345,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 12.345,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1000.0,
          ac_heating_requested_electricity_kwh: 1123.45,
          ac_heating_extra_electricity_kwh: 123.45,
          ac_heating_baseline_useful_kwh: 3800.0,
          ac_heating_requested_useful_kwh: 4269.11,
          ac_heating_additional_demand_pct: 12.345,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 5.0,
            realizations: []
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;
  assert.ok(html.includes('12.345%'), 'Arbitrary fractional percentage 12.345% is preserved in labels');
  assert.ok(!html.includes('+12%'), 'Not rounded to integer 12%');
  assert.ok(html.includes('1,123 kWh/year'), 'Requested electricity formatted correctly');
  assert.ok(html.includes('+123 kWh/year'), 'Additional electricity formatted correctly');
  assert.ok(html.includes('4,269 kWh thermal/year'), 'Useful heat formatted correctly');
});

test('accepted result vs newer settings mismatch: displays accepted values and marks outdated', () => {
  const env = createAppContext();
  env.run(`
    // Accepted result has 10%
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 10,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1320.0,
          ac_heating_extra_electricity_kwh: 120.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 5016.0,
          ac_heating_additional_demand_pct: 10,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 4.0,
            realizations: []
          }
        }
      }
    };
    // Newer local setting edited to 40%
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 40,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;
  // MUST show accepted result (10%), NOT newer setting (40%)
  assert.ok(html.includes('10%'), 'Renders accepted result percentage (10%)');
  assert.ok(!html.includes('40% demand'), 'Does not render draft/newer setting (40%)');
  assert.ok(html.includes('1,320 kWh/year'), 'Renders accepted requested electricity (1,320)');

  // Visibly marked outdated
  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Panel has stale-overlay class');
  assert.ok(html.includes('stale-comparison-notice'), 'Contains stale notice banner');
  assert.ok(html.includes('previously accepted result'), 'Mentions previously accepted result');
});

test('invalidation functions mark comparison outdated', () => {
  const env = createAppContext();
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 0,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 0,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1200.0,
          ac_heating_extra_electricity_kwh: 0.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 4560.0,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 4.0,
            realizations: []
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  // Initially fresh
  assert.equal(env.panel.classList.contains('stale-overlay'), false);
  assert.ok(!env.panel.innerHTML.includes('stale-comparison-notice'));

  // Invalidate via invalidateAcHeatingComparison
  env.run('invalidateAcHeatingComparison();');
  assert.equal(env.panel.classList.contains('stale-overlay'), true);

  // Invalidate via markResultsOutdated
  env.run('markResultsOutdated();');
  assert.equal(env.panel.classList.contains('stale-overlay'), true);
});

test('diagnostic duration and power units: distinguishes units, ranges, and electrical vs thermal', () => {
  const env = createAppContext();
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 20,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 20,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1440.0,
          ac_heating_extra_electricity_kwh: 240.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 5472.0,
          ac_heating_additional_demand_pct: 20,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: 5.5,
            realizations: [
              {
                weather_year: 2005,
                eligible_schedule_duration_hours: 1420.0,
                max_modeled_interval_average_electrical_demand_kw: 1.20,
                max_modeled_permitted_duration_electrical_requirement_kw: 2.10,
                max_modeled_permitted_duration_useful_heat_requirement_kw: 7.98
              },
              {
                weather_year: 2006,
                eligible_schedule_duration_hours: 1480.0,
                max_modeled_interval_average_electrical_demand_kw: 1.65,
                max_modeled_permitted_duration_electrical_requirement_kw: 2.75,
                max_modeled_permitted_duration_useful_heat_requirement_kw: 10.45
              }
            ],
            max_modeled_interval_average_electrical_demand_kw: 1.65,
            max_modeled_permitted_duration_electrical_requirement_kw: 2.75,
            max_modeled_permitted_duration_useful_heat_requirement_kw: 10.45
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;

  // Duration units
  assert.ok(html.includes('5.5 h/day'), 'Nominal daily union duration has h/day');
  assert.ok(html.includes('1,420 – 1,480 h/year'), 'Eligible schedule duration has h/year range');

  // Power units
  assert.ok(html.includes('1.65 kW electrical'), 'Interval-average demand has kW electrical');
  assert.ok(html.includes('2.75 kW electrical'), 'Permitted-duration requirement has kW electrical');
  assert.ok(html.includes('10.45 kW thermal'), 'Permitted-duration useful heat requirement has kW thermal');

  // Labels distinguish interval vs permitted duration and electrical vs thermal
  assert.ok(html.includes('Max interval-average electrical demand'));
  assert.ok(html.includes('Max permitted-duration electrical requirement'));
  assert.ok(html.includes('Max permitted-duration useful heat requirement'));
});

test('no extra multiplier: raw authoritative result values are displayed as-is', () => {
  const env = createAppContext();
  // If a multiplier of (1 + 25/100) were re-applied to requested 1,500 kWh, it would become 1,875 kWh
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 25,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 25,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1500.0,
          ac_heating_extra_electricity_kwh: 300.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 5700.0,
          ac_heating_additional_demand_pct: 25,
          ac_heating_scop: 3.8
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;
  assert.ok(html.includes('1,500 kWh/year'), 'Requested AC electricity is exact authoritative value (1,500)');
  assert.ok(!html.includes('1,875'), 'No redundant multiplier was applied to requested electricity');
  assert.ok(html.includes('300 kWh/year'), 'Additional electricity is exact authoritative value (300)');
  assert.ok(!html.includes('375'), 'No redundant multiplier was applied to extra electricity');
});

test('timezone and schedule mode labels read accurately from accepted result', () => {
  const env = createAppContext();

  // Test Standard fixed UTC+1 and Continuous mode
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 0,
      heating_schedule_mode: 'continuous',
      timezone_mode: 'standard'
    };
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 0,
          heating_schedule_mode: 'continuous',
          timezone_mode: 'standard'
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1200.0,
          ac_heating_requested_electricity_kwh: 1200.0,
          ac_heating_extra_electricity_kwh: 0.0,
          ac_heating_baseline_useful_kwh: 4560.0,
          ac_heating_requested_useful_kwh: 4560.0,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8
        }
      }
    };
    renderAcHeatingComparison();
  `);

  assert.ok(env.panel.innerHTML.includes('Standard fixed UTC+1'));
  assert.ok(env.panel.innerHTML.includes('Continuous (24h)'));

  // Test Europe/Rome (DST) and Scheduled mode
  env.run(`
    current.result.settings.timezone_mode = 'civil';
    current.result.settings.heating_schedule_mode = 'scheduled';
    renderAcHeatingComparison();
  `);

  assert.ok(env.panel.innerHTML.includes('Europe/Rome (DST)'));
  assert.ok(env.panel.innerHTML.includes('Scheduled operating windows'));
});

test('complete lifecycle: invalidation -> production recompute with evaluate mocked response -> acceptance -> fresh rendering', async () => {
  const env = createAppContext();
  env.context.fetch = async (url, opts) => {
    if (url === '/api/evaluate') {
      const body = JSON.parse(opts.body);
      const pct = body.settings.ac_heating_additional_demand_pct || 0;
      return {
        ok: true,
        json: async () => ({
          compute_seconds: 0.05,
          claims: [],
          warnings: [],
          result: {
            layout: { kwp: 6, panel_count: 14 },
            settings: JSON.parse(JSON.stringify(body.settings)),
            metrics: { npv_eur: 1000 },
            finance: { capex_eur: 5000, conversion_capex_eur: 0, nominal_discount_rate: 0.05, real_discount_rate: 0.03, annual_deduction_eur: 200, whole_package: { simple_payback: {}, discounted_payback: {} } },
            energy: {},
            annual_cashflows: [],
            limitations: [],
            enduses: {
              ac_heating_baseline_electricity_kwh: 1000,
              ac_heating_requested_electricity_kwh: 1000 * (1 + pct / 100),
              ac_heating_extra_electricity_kwh: 1000 * (pct / 100),
              ac_heating_baseline_useful_kwh: 3800,
              ac_heating_requested_useful_kwh: 3800 * (1 + pct / 100),
              ac_heating_additional_demand_pct: pct,
              ac_heating_scop: 3.8
            }
          }
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  // Initial recompute
  await env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 10,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil',
      ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }]
    };
    quote = { line_items: [] };
    recompute();
  `);
  await new Promise(r => setTimeout(r, 10));

  assert.equal(env.panel.classList.contains('stale-overlay'), false, 'Initially fresh after recompute');
  assert.ok(env.panel.innerHTML.includes('10%'));
  assert.ok(!env.panel.innerHTML.includes('stale-comparison-notice'));

  // Invalidate via input change
  env.run(`
    settings.ac_heating_additional_demand_pct = 25;
    invalidateAcHeatingComparison();
  `);
  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Panel is stale after invalidation');

  // In-flight render keeps stale overlay
  env.run('renderAcHeatingComparison();');
  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'In-flight render retains stale overlay');

  // Production recompute evaluates and accepts new response
  await env.run(`
    recompute();
  `);
  await new Promise(r => setTimeout(r, 10));

  assert.equal(env.panel.classList.contains('stale-overlay'), false, 'Fresh after recompute acceptance');
  assert.ok(!env.panel.innerHTML.includes('stale-comparison-notice'), 'Stale notice removed');
  assert.ok(env.panel.innerHTML.includes('25%'), 'Displays freshly accepted 25%');
});

test('older in-flight evaluate response cannot freshen panel', async () => {
  const env = createAppContext();
  let resolve1, resolve2;
  const p1 = new Promise(r => { resolve1 = r; });
  const p2 = new Promise(r => { resolve2 = r; });
  let callSeq = 0;

  env.context.fetch = async (url, opts) => {
    if (url === '/api/evaluate') {
      callSeq++;
      const num = callSeq;
      const body = JSON.parse(opts.body);
      if (num === 1) await p1;
      if (num === 2) await p2;
      return {
        ok: true,
        json: async () => ({
          compute_seconds: 0.05,
          claims: [],
          warnings: [],
          result: {
            layout: { kwp: 6, panel_count: 14 },
            settings: JSON.parse(JSON.stringify(body.settings)),
            metrics: { npv_eur: 1000 },
            finance: { capex_eur: 5000, conversion_capex_eur: 0, nominal_discount_rate: 0.05, real_discount_rate: 0.03, annual_deduction_eur: 200, whole_package: { simple_payback: {}, discounted_payback: {} } },
            energy: {},
            annual_cashflows: [],
            limitations: [],
            enduses: {
              ac_heating_baseline_electricity_kwh: 1000,
              ac_heating_requested_electricity_kwh: 1000 * (1 + (body.settings.ac_heating_additional_demand_pct || 0) / 100),
              ac_heating_extra_electricity_kwh: 100,
              ac_heating_baseline_useful_kwh: 3800,
              ac_heating_requested_useful_kwh: 4180,
              ac_heating_additional_demand_pct: body.settings.ac_heating_additional_demand_pct || 0,
              ac_heating_scop: 3.8
            }
          }
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  // Accepted 5%
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 5,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil',
      ac_heating_windows: []
    };
    quote = { line_items: [] };
    current = {
      result: {
        layout: { kwp: 6, panel_count: 14 },
        settings: deepClone(settings),
        metrics: { npv_eur: 1000 },
        finance: { capex_eur: 5000, conversion_capex_eur: 0, nominal_discount_rate: 0.05, real_discount_rate: 0.03, annual_deduction_eur: 200, whole_package: { simple_payback: {}, discounted_payback: {} } },
        energy: {},
        annual_cashflows: [],
        limitations: [],
        enduses: {
          ac_heating_baseline_electricity_kwh: 1000,
          ac_heating_requested_electricity_kwh: 1050,
          ac_heating_extra_electricity_kwh: 50,
          ac_heating_baseline_useful_kwh: 3800,
          ac_heating_requested_useful_kwh: 3990,
          ac_heating_additional_demand_pct: 5,
          ac_heating_scop: 3.8
        }
      }
    };
    renderAcHeatingComparison();
  `);

  assert.equal(env.panel.classList.contains('stale-overlay'), false);

  // Dispatch request 1 (15%)
  env.run(`
    settings.ac_heating_additional_demand_pct = 15;
    recompute();
  `);

  // Dispatch request 2 (25%)
  env.run(`
    settings.ac_heating_additional_demand_pct = 25;
    recompute();
  `);

  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Panel is stale while requests in flight');

  // Request 1 arrives now (older in-flight response)
  resolve1();
  await new Promise(r => setTimeout(r, 20));

  // Request 1 was dropped, panel is still stale and displays 5%
  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Panel remains stale after older in-flight response arrives');
  assert.ok(env.panel.innerHTML.includes('5% demand'), 'Still reflects accepted result (5%), not older in-flight (15%)');

  // Now Request 2 arrives
  resolve2();
  await new Promise(r => setTimeout(r, 20));

  assert.equal(env.panel.classList.contains('stale-overlay'), false, 'Fresh after current in-flight response arrives');
  assert.ok(env.panel.innerHTML.includes('25%'), 'Displays accepted 25%');
});

test('settings mismatch remains stale even when accepted result is rendered', () => {
  const env = createAppContext();
  env.run(`
    current = {
      result: {
        settings: {
          load_scenario: 'electrified',
          planning_heat_pump: false,
          ac_heating_additional_demand_pct: 10,
          heating_schedule_mode: 'scheduled',
          timezone_mode: 'civil',
          ac_heating_windows: []
        },
        enduses: {
          ac_heating_baseline_electricity_kwh: 1000,
          ac_heating_requested_electricity_kwh: 1100,
          ac_heating_extra_electricity_kwh: 100,
          ac_heating_baseline_useful_kwh: 3800,
          ac_heating_requested_useful_kwh: 4180,
          ac_heating_additional_demand_pct: 10,
          ac_heating_scop: 3.8
        }
      }
    };
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 30, // Mismatched!
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil',
      ac_heating_windows: []
    };
    quote = { line_items: [] };
    renderAcHeatingComparison();
  `);

  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Panel marked stale on settings mismatch');
  assert.ok(env.panel.innerHTML.includes('10%'), 'Renders accepted result values');
  assert.ok(!env.panel.innerHTML.includes('30% demand'), 'Does not render draft setting values');

  // Calling acceptAcHeatingComparison fails and leaves it stale
  const accepted = env.run('acceptAcHeatingComparison(current.result)');
  assert.equal(accepted, false, 'acceptAcHeatingComparison rejects mismatched result');
  assert.equal(env.panel.classList.contains('stale-overlay'), true);
});

test('same-value invalid to valid recovery triggers recompute and gets fresh comparison', async () => {
  const env = createAppContext();
  env.context.fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    const pct = body.settings.ac_heating_additional_demand_pct || 0;
    return {
      ok: true,
      json: async () => ({
        compute_seconds: 0.05,
        claims: [],
        warnings: [],
        result: {
          layout: { kwp: 6, panel_count: 14 },
          settings: JSON.parse(JSON.stringify(body.settings)),
          metrics: { npv_eur: 1000 },
          finance: { capex_eur: 5000, conversion_capex_eur: 0, nominal_discount_rate: 0.05, real_discount_rate: 0.03, annual_deduction_eur: 200, whole_package: { simple_payback: {}, discounted_payback: {} } },
          energy: {},
          annual_cashflows: [],
          limitations: [],
          enduses: {
            ac_heating_baseline_electricity_kwh: 1000,
            ac_heating_requested_electricity_kwh: 1200,
            ac_heating_extra_electricity_kwh: 200,
            ac_heating_baseline_useful_kwh: 3800,
            ac_heating_requested_useful_kwh: 4560,
            ac_heating_additional_demand_pct: pct,
            ac_heating_scop: 3.8
          }
        }
      })
    };
  };

  // Initial valid state with 20%
  await env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 20,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil',
      ac_heating_windows: []
    };
    quote = { line_items: [] };
    recompute();
  `);
  await new Promise(r => setTimeout(r, 10));
  assert.equal(env.panel.classList.contains('stale-overlay'), false);

  // Enter invalid text '-20'
  env.run(`
    handleAcHeatingDemandPctInput('-20');
  `);
  assert.equal(env.run('hasInvalidAcDemandPctDraft()'), true);
  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Invalid input marks comparison stale');

  // Restore same valid value '20'
  env.run(`
    handleAcHeatingDemandPctInput('20');
  `);
  assert.equal(env.run('hasInvalidAcDemandPctDraft()'), false);
  await new Promise(r => setTimeout(r, 10));

  // Panel is fresh again!
  assert.equal(env.panel.classList.contains('stale-overlay'), false, 'Recovered fresh after valid same-value restoration');
  assert.ok(env.panel.innerHTML.includes('20%'));
});

test('continuous mode: retained invalid schedule windows produce fresh comparison, invalid percentage blocks', async () => {
  const env = createAppContext();
  env.context.fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    const pct = body.settings.ac_heating_additional_demand_pct || 0;
    return {
      ok: true,
      json: async () => ({
        compute_seconds: 0.05,
        claims: [],
        warnings: [],
        result: {
          layout: { kwp: 6, panel_count: 14 },
          settings: JSON.parse(JSON.stringify(body.settings)),
          metrics: { npv_eur: 1000 },
          finance: { capex_eur: 5000, conversion_capex_eur: 0, nominal_discount_rate: 0.05, real_discount_rate: 0.03, annual_deduction_eur: 200, whole_package: { simple_payback: {}, discounted_payback: {} } },
          energy: {},
          annual_cashflows: [],
          limitations: [],
          enduses: {
            ac_heating_baseline_electricity_kwh: 1000,
            ac_heating_requested_electricity_kwh: 1200,
            ac_heating_extra_electricity_kwh: 200,
            ac_heating_baseline_useful_kwh: 3800,
            ac_heating_requested_useful_kwh: 4560,
            ac_heating_additional_demand_pct: pct,
            ac_heating_scop: 3.8
          }
        }
      })
    };
  };

  // Continuous mode with invalid schedule window draft
  await env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 15,
      heating_schedule_mode: 'continuous',
      timezone_mode: 'civil',
      ac_heating_windows: [{ id: 'w1', start_minute: 360, end_minute: 480 }]
    };
    quote = { line_items: [] };
    acScheduleDraft = [{ id: 'w1', start: '25:00', end: '10:00', startError: 'Invalid hour', endError: '' }];
    recompute();
  `);
  await new Promise(r => setTimeout(r, 10));

  assert.equal(env.run('hasInvalidAcScheduleDraft()'), true, 'Schedule draft is invalid');
  assert.equal(env.run('shouldBlockAcScheduleRecompute(settings)'), false, 'Does not block continuous mode');
  assert.equal(env.panel.classList.contains('stale-overlay'), false, 'Continuous mode produces fresh comparison with invalid schedule windows');
  assert.ok(env.panel.innerHTML.includes('Continuous (24h)'));

  // Now enter invalid percentage in continuous mode
  env.run(`
    handleAcHeatingDemandPctInput('-5');
  `);
  assert.equal(env.run('hasInvalidAcDemandPctDraft()'), true);
  assert.equal(env.run('shouldBlockAcDemandPctRecompute(settings)'), true, 'Invalid pct DOES block continuous mode');
  assert.equal(env.panel.classList.contains('stale-overlay'), true, 'Invalid pct marks continuous mode stale');
});

test('diagnostic measurements: missing realization max fields render unavailable, legitimate numeric 0 preserved', () => {
  const env = createAppContext();
  // Case a: missing measurements
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 0,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: deepClone(settings),
        enduses: {
          ac_heating_baseline_electricity_kwh: 1000,
          ac_heating_requested_electricity_kwh: 1000,
          ac_heating_extra_electricity_kwh: 0,
          ac_heating_baseline_useful_kwh: 3800,
          ac_heating_requested_useful_kwh: 3800,
          ac_heating_additional_demand_pct: 0,
          ac_heating_scop: 3.8,
          ac_heating_allocation_diagnostics: {
            nominal_daily_union_duration_hours: null,
            realizations: [
              { weather_year: 2005 } // No power fields!
            ]
          }
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const htmlMissing = env.panel.innerHTML;
  assert.ok(!htmlMissing.includes('0.00 kW electrical'), 'Does not manufacture 0.00 kW electrical for missing data');
  assert.ok(!htmlMissing.includes('0.00 kW thermal'), 'Does not manufacture 0.00 kW thermal for missing data');
  assert.ok(htmlMissing.includes('—'), 'Renders dash for unavailable diagnostic');

  // Case b: legitimate 0
  env.run(`
    current.result.enduses.ac_heating_allocation_diagnostics = {
      nominal_daily_union_duration_hours: 0,
      max_modeled_interval_average_electrical_demand_kw: 0,
      max_modeled_permitted_duration_electrical_requirement_kw: 0,
      max_modeled_permitted_duration_useful_heat_requirement_kw: 0,
      realizations: [
        {
          weather_year: 2005,
          eligible_schedule_duration_hours: 0,
          max_modeled_interval_average_electrical_demand_kw: 0,
          max_modeled_permitted_duration_electrical_requirement_kw: 0,
          max_modeled_permitted_duration_useful_heat_requirement_kw: 0
        }
      ]
    };
    renderAcHeatingComparison();
  `);

  const htmlZero = env.panel.innerHTML;
  assert.ok(htmlZero.includes('0.00 kW electrical'), 'Preserves true numeric 0 kW electrical');
  assert.ok(htmlZero.includes('0.00 kW thermal'), 'Preserves true numeric 0 kW thermal');
  assert.ok(htmlZero.includes('0.0 h/day'), 'Preserves true numeric 0 h/day');
});

test('labels use requested/modeled useful heat without implying proven equipment delivery', () => {
  const env = createAppContext();
  env.run(`
    settings = {
      load_scenario: 'electrified',
      planning_heat_pump: false,
      ac_heating_additional_demand_pct: 10,
      heating_schedule_mode: 'scheduled',
      timezone_mode: 'civil'
    };
    current = {
      result: {
        settings: deepClone(settings),
        enduses: {
          ac_heating_baseline_electricity_kwh: 1000,
          ac_heating_requested_electricity_kwh: 1100,
          ac_heating_extra_electricity_kwh: 100,
          ac_heating_baseline_useful_kwh: 3800,
          ac_heating_requested_useful_kwh: 4180,
          ac_heating_additional_demand_pct: 10,
          ac_heating_scop: 3.8
        }
      }
    };
    renderAcHeatingComparison();
  `);

  const html = env.panel.innerHTML;
  assert.ok(html.includes('Requested useful heat'), 'Card label is Requested useful heat');
  assert.ok(html.includes('Requested/modeled useful heat at requested demand'), 'Card subtitle describes requested/modeled useful heat');
  assert.ok(html.includes(`<div class="value">${env.run('fmt(3800 * 1.1, 0)')} kWh thermal/year</div>`), 'Requested useful heat card preserves the 10% demand increase and thermal units');
  assert.ok(!html.includes('Delivered thermal heat energy'), 'Does not say Delivered thermal heat energy');
  assert.ok(!html.includes('Delivered useful heat'), 'Does not say Delivered useful heat');
  assert.ok(html.includes('equipment adequacy and indoor comfort are unassessed'), 'States equipment adequacy unassessed');
});

