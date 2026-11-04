'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appPath = process.env.APP_JS_PATH || [
  path.join(__dirname, "../feasibility/web/app.js"),
  path.join(process.cwd(), "feasibility/web/app.js")
].find(p => p && fs.existsSync(p));

const appSource = fs.readFileSync(appPath, "utf8");

function extractFn(name) {
  const idx = appSource.indexOf(`function ${name}(`);
  if (idx === -1) throw new Error(`Missing ${name} in app.js`);
  let depth = 0, start = appSource.indexOf("{", idx);
  for (let i = start; i < appSource.length; i++) {
    if (appSource[i] === "{") depth++;
    else if (appSource[i] === "}" && --depth === 0) return appSource.slice(idx, i + 1);
  }
  throw new Error(`Unterminated ${name}`);
}

function createHarness() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        innerHTML: "",
        textContent: "",
        value: "",
        style: {},
        classList: {
          toggle: () => {},
          contains: () => false,
          add: () => {},
          remove: () => {}
        },
        setAttribute: () => {},
        getAttribute: () => null,
        querySelectorAll: () => []
      });
    }
    return elements.get(id);
  };

  const nf = (n, d = 0) => {
    if (n == null) return "—";
    const num = Number(n);
    return isNaN(num) ? String(n) : num.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  };

  const fmt = (n, type = "number") => {
    if (n == null) return "—";
    if (type === "eur") return "€" + nf(Math.round(n));
    if (type === "pct") return (n * 100).toFixed(1) + "%";
    if (type === "years") return typeof n === "number" ? n.toFixed(1) + " y" : String(n);
    return nf(n);
  };

  const range = (r, type = "number") => {
    if (!r) return "—";
    if (typeof r === "number") return fmt(r, type);
    if (r.min != null && r.max != null) {
      if (Math.abs(r.max - r.min) < 1e-7) return fmt(r.min, type);
      return `${fmt(r.min, type)}–${fmt(r.max, type)}`;
    }
    if (r.headline != null) return fmt(r.headline, type);
    if (r.median != null) return fmt(r.median, type);
    if (r.mean != null) return fmt(r.mean, type);
    return "—";
  };

  const verdictCard = (title, val, unit, extra, isHighlight, id) => {
    return `<div id="${id}" class="card"><h4>${title}</h4><div class="val">${typeof val === "object" ? range(val, unit) : fmt(val, unit)}</div><div class="extra">${extra}</div></div>`;
  };

  const sandbox = {
    console,
    Math,
    Number,
    Array,
    Object,
    String,
    isNaN,
    isFinite,
    $,
    document: { getElementById: $, querySelector: () => null, querySelectorAll: () => [] },
    nf,
    fmt,
    range,
    verdictCard,
    esc: s => String(s ?? "").replace(/[&<>"\x27]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    isItemizedPricingActive: () => false,
    recommendationSubtitle: () => "",
    metricCard: (title, pb, type, detail) => ({ title, pb, type, detail }),
    solarInFlight: false,
    solarAcceptedFingerprint: null,
    solarStateFingerprint: () => "",
    formatBatteryComposition: item => (item && item.battery_kwh ? `${item.battery_kwh} kWh` : "0 kWh"),
    summaryScope: "solar",
    summaryCash: "cash",
    settings: { cashflow_mode: "real" },
    current: { result: null },
    elements,
    getWeatherEnsembleMeta: () => ({ count: 21, rangeLabel: "2005–2025" }),
    getMainRecommendation: d => d?.recommendation_choices?.balanced || d?.recommendation_choices?.financial || null
  };

  const targetFns = [
    "formatPaybackYearsValue",
    "getCanonicalEnsembleFlows",
    "getScopeCashMetrics",
    "renderVerdictStrip",
    "renderDesignUnifiedComparisonTable",
    "renderSolarRecommendationChoices",
    "renderSolarAlternativesTable",
    "renderSolarSizing",
    "renderSolarComparisonsTable",
    "renderSolarSizeCurveChart",
    "renderSolarSensitivityResults"
  ];

  const code = targetFns.map(extractFn).join("\n");
  const ctx = vm.createContext(sandbox);
  vm.runInContext(code, ctx);
  return { ctx, elements, $ };
}

function makeDefaultCurrentResult() {
  return {
    layout: { kwp: 9.0, panel_count: 18 },
    headline_energy_flows: {
      autarky: 0.847,
      self_consumption: 0.782,
      clipped_kwh: 99.8,
      clipped_pct: 0.78,
      pv_generation_kwh: 12680.4,
      load_demand_kwh: 14971.0
    },
    energy: { ensemble_autarky: 0.847, autarky: 0.853 },
    metrics: {
      autarky: { median: 0.853, mean: 0.849 },
      self_consumption: { median: 0.760, mean: 0.775 }
    },
    finance: {
      capex_eur: 15000,
      pv_incremental: { npv_mean: 5000, npv: { median: 4800 }, discounted_payback_of_mean_cash_flows: 7.2 },
      solar_equity: { npv_mean: 6000, npv: { median: 5800 } }
    }
  };
}

test("1. Current card and table autarky strictly use canonical ensemble flows (0.847 vs median 0.853)", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();

  const sizingData = {
    options: { financial_basis: "project" },
    current_system: {
      kwp: 9.0,
      battery_kwh: 0,
      panel_count: 18,
      autarky_mean: 0.847,
      autarky_median: 0.853,
      annual_clipped_kwh_mean: 99.8,
      annual_clipped_pct_mean: 0.78
    },
    recommendation_choices: {
      financial: {
        panel_count: 18,
        kwp: 9.0,
        battery_kwh: 0,
        capex_eur: 15000,
        project_npv_mean: 5000,
        project_npv_median: 4800,
        autarky_mean: 0.847,
        autarky_median: 0.853,
        roles: ["Best financial return"]
      }
    }
  };

  ctx.renderDesignUnifiedComparisonTable(sizingData);
  const tableHtml = $("designUnifiedComparisonTable").innerHTML;
  assert.ok(tableHtml.includes("84.7%"), "Unified comparison table row shows canonical 84.7% autarky");
  assert.ok(!tableHtml.includes("85.3%"), "Unified comparison table does NOT use 85.3% median autarky");

  ctx.renderSolarRecommendationChoices(sizingData);
  const choicesHtml = $("solarChoiceCards").innerHTML;
  assert.ok(choicesHtml.includes("84.7%"), "Current choice card displays 84.7% coverage");
  assert.ok(!choicesHtml.includes("85.3%"), "Current choice card does NOT display 85.3% coverage");
});

test("2. Curtailed PV uses ensemble mean first-year dispatch and 1-decimal formatting (99.8 kWh/yr · 0.78%)", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();

  const sizingData = {
    options: { financial_basis: "project" },
    recommendation_choices: {
      financial: {
        panel_count: 20,
        kwp: 10.0,
        battery_kwh: 5.0,
        capex_eur: 17000,
        project_npv_mean: 6200,
        annual_clipped_kwh_mean: 99.8,
        annual_clipped_pct_mean: 0.78,
        annual_clipped_kwh_median: 125.0,
        annual_clipped_pct_median: 1.05,
        roles: ["Best financial return"]
      }
    }
  };

  ctx.renderSolarRecommendationChoices(sizingData);
  const choicesHtml = $("solarChoiceCards").innerHTML;
  assert.ok(choicesHtml.includes("99.8 kWh/yr · 0.78%"), `Expected "99.8 kWh/yr · 0.78%" in choice card, got: ${choicesHtml}`);
  assert.ok(choicesHtml.includes("Historical-weather ensemble mean first-year dispatch"), `Expected ensemble dispatch sublabel, got: ${choicesHtml}`);
  assert.ok(!choicesHtml.includes("21-year mean first-year dispatch"), "Must not claim a fixed weather sample size");
  assert.ok(!choicesHtml.includes("Median first-year dispatch"), "Old median sublabel must be replaced");
});

test("3. Candidate means take precedence over medians in comparison and sensitivity", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();

  const sizingData = {
    options: { financial_basis: "project" },
    recommendation_choices: {
      financial: {
        panel_count: 20,
        kwp: 10.0,
        battery_kwh: 5.0,
        capex_eur: 17000,
        project_npv_mean: 11200,
        project_npv_median: 10500,
        equity_npv_mean: 12400,
        equity_npv_median: 11800,
        autarky_mean: 0.812,
        autarky_median: 0.795,
        self_consumption_mean: 0.745,
        self_consumption_median: 0.760,
        annual_clipped_kwh_mean: 145.2,
        annual_clipped_pct_mean: 1.15,
        annual_clipped_kwh_median: 120.0,
        annual_clipped_pct_median: 0.95,
        roles: ["Best financial return"]
      }
    }
  };

  ctx.renderDesignUnifiedComparisonTable(sizingData);
  const tableHtml = $("designUnifiedComparisonTable").innerHTML;
  assert.ok(tableHtml.includes("€11,200"), "Project NPV uses mean 11,200");
  assert.ok(!tableHtml.includes("€10,500"), "Project NPV does NOT use median 10,500");
  assert.ok(tableHtml.includes("81.2%"), "Autarky uses mean 81.2%");
  assert.ok(!tableHtml.includes("79.5%"), "Autarky does NOT use median 79.5%");
  assert.ok(tableHtml.includes("74.5%"), "Self consumption uses mean 74.5%");
  assert.ok(!tableHtml.includes("76.0%"), "Self consumption does NOT use median 76.0%");

  // Sensitivity table checks (scenarios without comparisons derived directly into table rows)
  const sensData = {
    financial_basis: "project",
    scenarios: {
      base: {
        scenario_label: "Base Scenario",
        roles: {
          financial: {
            panel_count: 20,
            kwp: 10.0,
            battery_kwh: 5.0,
            project_npv_mean: 11200,
            project_npv_median: 10500,
            equity_npv_mean: 12400,
            equity_npv_median: 11800,
            autarky_mean: 0.812,
            autarky_median: 0.795
          }
        },
        base_preferred_repriced: {
          npv_mean: 9800,
          npv_median: 9200
        }
      }
    }
  };
  ctx.renderSolarSensitivityResults(sensData);
  const sensTableHtml = $("solarSensitivityComparisonTable").innerHTML;
  assert.ok(sensTableHtml.includes("€11,200"), "Sensitivity table best project NPV uses mean €11,200");
  assert.ok(!sensTableHtml.includes("€10,500"), "Sensitivity table best project NPV does NOT use median €10,500");
  assert.ok(sensTableHtml.includes("81.2%"), "Sensitivity table coverage uses mean 81.2%");
  assert.ok(sensTableHtml.includes("€9,800"), "Sensitivity table base repriced uses mean €9,800");
  assert.ok(!sensTableHtml.includes("€9,200"), "Sensitivity table base repriced does NOT use median €9,200");
});

test("4. Summary generation anchor uses 12,680.4 kWh/yr and displays canonical self-consumption ratio", () => {
  const { ctx, $ } = createHarness();
  const r = {
    settings: { cashflow_mode: "real" },
    headline_energy_flows: {
      pv_generation_kwh: 12680.4,
      load_demand_kwh: 14971.0,
      direct_use_kwh: 7500.0,
      battery_charge_kwh: 3200.0,
      autarky: 0.847,
      flow_ranges: {
        autarky: { min: 0.82, max: 0.87, p10: 0.83, p90: 0.86 }
      }
    },
    energy: {
      ensemble_autarky: 0.847,
      autarky: { median: 0.853, mean: 0.847 },
      self_consumption: { median: 0.81, mean: 0.8438 }
    },
    metrics: {
      autarky: { median: 0.853, mean: 0.847 },
      self_consumption: { median: 0.81, mean: 0.8438 }
    },
    finance: {
      capex_eur: 15000,
      pv_incremental: {
        discounted_payback_of_mean_cash_flows: 6.8,
        discounted_payback: { median: 7.1 }
      }
    }
  };

  const m = ctx.getScopeCashMetrics(r, "solar", "cash");
  assert.equal(m.pvKwh, 12680.4, "m.pvKwh must be canonical mean 12680.4");
  assert.equal(m.selfConsumption.headline, 10700 / 12680.4, "m.selfConsumption.headline uses ratio of mean flows");

  ctx.renderVerdictStrip(r);
  const metricsHtml = $("metrics").innerHTML;
  assert.ok(metricsHtml.includes("12,680.4 kWh/yr"), `Autarky subtext generation anchor must format as 12,680.4 kWh/yr, got: ${metricsHtml}`);
  assert.ok(metricsHtml.includes("Self-consumption 84.4%"), `Autarky subtext must display Self-consumption 84.4%, got: ${metricsHtml}`);
});

test("5. getCanonicalEnsembleFlows strictly uses r.headline_energy_flows and ignores stale behavior cache", () => {
  const { ctx } = createHarness();
  const rWithFlows = { headline_energy_flows: { autarky: 0.847, pv_generation_kwh: 12680.4 } };
  assert.deepEqual(ctx.getCanonicalEnsembleFlows(rWithFlows), { autarky: 0.847, pv_generation_kwh: 12680.4 });

  // Simulate stale cache in scope
  ctx.behaviorMatrixCache = { annual_flows: { autarky: 0.999, pv_generation_kwh: 99999 } };

  const rWithoutFlows = { headline_energy_flows: null };
  assert.strictEqual(ctx.getCanonicalEnsembleFlows(rWithoutFlows), null, "Must return null when headline_energy_flows is null, ignoring behaviorMatrixCache");

  const rEmpty = {};
  assert.strictEqual(ctx.getCanonicalEnsembleFlows(rEmpty), null, "Must return null when headline_energy_flows is missing");
  assert.strictEqual(ctx.getCanonicalEnsembleFlows(null), null, "Must return null when r is null");
});

test("6. Explicit null payback of mean cash flows is preserved and never falls back to median", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();

  const sizingData = {
    options: { financial_basis: "project" },
    recommendation_choices: {
      financial: {
        panel_count: 18,
        kwp: 9.0,
        battery_kwh: 0,
        capex_eur: 15000,
        project_npv_mean: -2000,
        project_npv_median: 1000,
        discounted_payback_of_mean_cash_flows: null,
        discounted_payback_years: 8.5,
        metrics: { discounted_payback_years: 8.5 },
        roles: ["Best financial return"]
      }
    }
  };

  ctx.renderDesignUnifiedComparisonTable(sizingData);
  const tableHtml = $("designUnifiedComparisonTable").innerHTML;
  assert.ok(tableHtml.includes("Not reached") || tableHtml.includes("—"), "Payback displays Not reached or —");
  assert.ok(!tableHtml.includes("8.5 y"), "Payback must NOT fall back to 8.5 y when mean payback is explicit null");

  ctx.renderSolarRecommendationChoices(sizingData);
  const choicesHtml = $("solarChoiceCards").innerHTML;
  assert.ok(choicesHtml.includes("Not reached") || choicesHtml.includes("—"), "Choice card displays Not reached or —");
  assert.ok(!choicesHtml.includes("8.5 y"), "Choice card must NOT fall back to 8.5 y");
});


function makeSizingChoices({ finDiff = false, noInstall = false } = {}) {
  const base = { panel_count: 18, kwp: 9.0, battery_kwh: 5.0, capex_eur: 16000, project_npv_mean: 8000, autarky_mean: 0.85 };
  return {
    options: { financial_basis: "project" },
    recommendation_choices: {
      financial: noInstall
        ? { is_no_install: true, panel_count: 0, kwp: 0, battery_kwh: 0, capex_eur: 0, project_npv_mean: 0, autarky_mean: 0, discounted_payback_of_mean_cash_flows: null, roles: ["Best financial return"] }
        : { ...base, project_npv_mean: finDiff ? 8200 : 8000, settings: { routing_priority: finDiff ? "export_priority" : "self_consumption", routing_switch_hour: finDiff ? 16 : 0 }, roles: ["Best financial return"] },
      balanced: { ...base, settings: { routing_priority: "self_consumption", routing_switch_hour: 0 }, roles: ["Main recommendation", "Balanced choice"] },
      budget: { panel_count: 14, kwp: 7.0, battery_kwh: 0, capex_eur: 9500, project_npv_mean: 4200, autarky_mean: 0.70, roles: ["Budget choice"] },
      smaller: { panel_count: 16, kwp: 8.0, battery_kwh: 2.5, capex_eur: 12000, project_npv_mean: 5500, autarky_mean: 0.78, roles: ["Lower-cost alternative"] }
    }
  };
}

function makeSensScenarios() {
  return {
    financial_basis: "project",
    scenarios: {
      base: {
        scenario_label: "Base Scenario",
        perturbation_description: "Baseline economic parameters.",
        roles: { financial: { panel_count: 20, kwp: 10.0, battery_kwh: 5.0, capex_eur: 17000, project_npv_mean: 11200, project_npv: { min: 9500, max: 12800 }, autarky_mean: 0.812 } },
        base_preferred_repriced: { npv_mean: 11200, regret_vs_scenario_financial_best_eur: 0, changed_layout: false },
        smaller_sacrifice: { sacrifice_eur: 350, sacrifice_pct: 3.1 }
      },
      lower_export: {
        scenario_label: "Lower Export",
        perturbation_description: "Export compensation drops by 40%.",
        roles: { financial: { panel_count: 20, kwp: 10.0, battery_kwh: 5.0, capex_eur: 17000, project_npv_mean: 8400, project_npv: { min: 7100, max: 9600 }, autarky_mean: 0.785 } },
        base_preferred_repriced: { npv_mean: 8400, regret_vs_scenario_financial_best_eur: 0, changed_layout: false },
        smaller_sacrifice: { sacrifice_eur: 420, sacrifice_pct: 5.0 }
      },
      higher_cost: {
        scenario_label: "Higher Cost",
        perturbation_description: "Capex up 35%.",
        roles: { financial: { panel_count: 0, kwp: 0, battery_kwh: 0, is_no_install: true, capex_eur: 0, project_npv_mean: 0, autarky_mean: 0 } },
        base_preferred_repriced: { npv_mean: -1500, regret_vs_scenario_financial_best_eur: 1500, changed_layout: true }
      },
      grid_limit: {
        scenario_label: "Grid Limit",
        status: "UNAVAILABLE",
        perturbation_description: "Grid feed-in restricted.",
        unavailable_reason: "Grid constraint violated."
      },
      higher_apr: {
        scenario_label: "Higher APR",
        perturbation_description: "Borrowing rate rises by 300 bps.",
        roles: { financial: { panel_count: 18, kwp: 9.0, capex_eur: 15000, project_npv_mean: 7500, autarky_mean: 0.79 } },
        base_preferred_repriced: null
      }
    }
  };
}

test("7. Hero merges matching configs without financial button; routing diff yields secondary button", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();

  // Same physical config & routing -> merged hero, no financial button
  ctx.renderSolarRecommendationChoices(makeSizingChoices());
  let html = $("solarChoiceCards").innerHTML;
  assert.ok(html.includes("solar-hero-comparison") && html.includes("solar-hero-column"), "Hero comparison layout classes");
  assert.ok(/financial/i.test(html) && /main|balanced/i.test(html), "Merged hero displays both badges");
  assert.ok(!html.includes('data-choice="financial"'), "No separate financial button on matching routing");
  assert.ok(html.includes('data-choice="balanced"'), "Balanced button present");
  assert.ok(html.includes("solar-wide-alternatives") || html.includes("solar-alternative-card"), "Alternatives layout class");
  assert.ok(html.includes('data-choice="budget"') && html.includes('data-choice="smaller"'), "Alternative buttons present");
  assert.ok(!html.includes("Same hardware · different schedule"), "Merged matching config must not have schedule warning");
  assert.ok(!html.includes("solar-schedule-difference"), "Merged matching config must not have solar-schedule-difference");

  // Routing schedule differs -> secondary financial button appears
  ctx.renderSolarRecommendationChoices(makeSizingChoices({ finDiff: true }));
  html = $("solarChoiceCards").innerHTML;
  assert.ok(html.includes('data-choice="financial"'), "Secondary financial button rendered when routing differs");
  const scheduleBadges = html.match(/Same hardware\s*·\s*different schedule/g) || [];
  assert.equal(scheduleBadges.length, 2, "Both cards display 'Same hardware · different schedule'");
  assert.ok(html.includes("solar-schedule-difference"), "Prominently displays solar-schedule-difference class");
  assert.ok(html.includes("export_priority"), "Shows candidate route text export_priority");
  assert.ok(html.includes("self_consumption"), "Shows candidate route text self_consumption");

  // Differing hardware -> no same hardware badge
  const diffHardwareChoices = makeSizingChoices({ finDiff: true });
  diffHardwareChoices.recommendation_choices.financial = {
    ...diffHardwareChoices.recommendation_choices.financial,
    panel_count: 20,
    kwp: 10.0,
    battery_kwh: 10.0,
    capex_eur: 18000
  };
  ctx.renderSolarRecommendationChoices(diffHardwareChoices);
  html = $("solarChoiceCards").innerHTML;
  assert.ok(!html.includes("Same hardware · different schedule"), "Differing hardware must not show same hardware badge");
  assert.ok(!html.includes("solar-schedule-difference"), "Differing hardware must not show solar-schedule-difference");

  // Scheduled routing switch 12 vs 13 (same hardware and priority)
  ctx.routeDisplayForCandidate = item => {
    const sw = item?.settings?.routing_switch_hour;
    return `Scheduled AM/PM · switch ${sw}:00 · Mar–Oct`;
  };

  const makeScheduledChoices = ({ finSwitch = 12, balSwitch = 13, diffRoof = false } = {}) => {
    const base = {
      panel_count: 18,
      kwp: 9.0,
      battery_kwh: 5.0,
      capex_eur: 16000,
      project_npv_mean: 8000,
      autarky_mean: 0.85
    };
    const roofA = diffRoof ? { roof_counts: { south: 10, east: 8 } } : {};
    const roofB = diffRoof ? { roof_counts: { south: 14, east: 4 } } : {};
    return {
      options: { financial_basis: "project" },
      recommendation_choices: {
        financial: { ...base, ...roofA, project_npv_mean: 8200, settings: { routing_priority: "scheduled", routing_switch_hour: finSwitch, scheduled_start_month: 3, scheduled_end_month: 10 }, roles: ["Best financial return"] },
        balanced: { ...base, ...roofB, settings: { routing_priority: "scheduled", routing_switch_hour: balSwitch, scheduled_start_month: 3, scheduled_end_month: 10 }, roles: ["Main recommendation", "Balanced choice"] },
        budget: { panel_count: 14, kwp: 7.0, battery_kwh: 0, capex_eur: 9500, project_npv_mean: 4200, autarky_mean: 0.70, roles: ["Budget choice"] },
        smaller: { panel_count: 16, kwp: 8.0, battery_kwh: 2.5, capex_eur: 12000, project_npv_mean: 5500, autarky_mean: 0.78, roles: ["Lower-cost alternative"] }
      }
    };
  };

  ctx.renderSolarRecommendationChoices(makeScheduledChoices({ finSwitch: 12, balSwitch: 13 }));
  html = $("solarChoiceCards").innerHTML;
  const schedBadges = html.match(/Same hardware\s*·\s*different schedule/g) || [];
  assert.equal(schedBadges.length, 2, "Both cards display 'Same hardware · different schedule' exactly 2 times");
  assert.ok(html.includes("solar-schedule-difference-route"), "Class solar-schedule-difference-route present");
  assert.ok(html.includes("<strong>switch 12:00</strong>"), "Financial card route includes <strong>switch 12:00</strong> with space");
  assert.ok(html.includes("<strong>switch 13:00</strong>"), "Balanced card route includes <strong>switch 13:00</strong> with space");

  // Same-full config still merges without separate financial button or schedule warning
  ctx.renderSolarRecommendationChoices(makeScheduledChoices({ finSwitch: 12, balSwitch: 12 }));
  html = $("solarChoiceCards").innerHTML;
  assert.ok(!html.includes('data-choice="financial"'), "Matching scheduled config merges without separate financial button");
  assert.ok(!html.includes("Same hardware · different schedule"), "Matching scheduled config must not show schedule warning");
  assert.ok(!html.includes("solar-schedule-difference"), "Matching scheduled config must not show solar-schedule-difference");

  // Differing roof distribution with same totals must NOT show same hardware badge
  ctx.renderSolarRecommendationChoices(makeScheduledChoices({ finSwitch: 12, balSwitch: 13, diffRoof: true }));
  html = $("solarChoiceCards").innerHTML;
  assert.ok(!html.includes("Same hardware · different schedule"), "Differing roof distribution must not show same hardware badge");
  assert.ok(!html.includes("solar-schedule-difference"), "Differing roof distribution must not show solar-schedule-difference");
});

test("8. Current vs Main explicit deltas (+20.0 pp) with valid baseline and fallbacks for null/no-install", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = {
    layout: { kwp: 6.0, panel_count: 12 },
    headline_energy_flows: { autarky: 0.65, self_consumption: 0.70 },
    finance: { capex_eur: 10000, pv_incremental: { npv_mean: 3000 } }
  };

  ctx.renderSolarRecommendationChoices(makeSizingChoices());
  let html = $("solarChoiceCards").innerHTML;
  assert.ok(html.includes("€5,000"), "NPV delta +€5,000 vs current");
  assert.ok(html.includes("€6,000"), "Capex delta +€6,000 vs current");
  assert.ok(html.includes("+20.0 pp") || html.includes("20.0 pp"), "Coverage delta MUST be +20.0 pp");

  // Baseline null -> no NaN
  ctx.current.result = null;
  ctx.renderSolarRecommendationChoices(makeSizingChoices());
  assert.ok(!$("solarChoiceCards").innerHTML.includes("NaN"), "Null baseline yields no NaN deltas");

  // No-install baseline with null payback
  ctx.renderSolarRecommendationChoices(makeSizingChoices({ noInstall: true }));
  html = $("solarChoiceCards").innerHTML;
  assert.ok(/no-install/i.test(html) && !html.includes("NaN"), "No-install handles mean zero capex/NPV without NaN");
  assert.ok(html.includes("Not reached") || html.includes("—"), "Preserves null-payback for no-install");
});

test("9. Sensitivity table derives rows from scenarios, checks isolated rows, and asserts exact 2 of 5 robust banner", () => {
  const { ctx, $ } = createHarness();
  ctx.renderSolarSensitivityResults(makeSensScenarios());
  const tableHtml = $("solarSensitivityComparisonTable").innerHTML;

  const getRow = label => "<tr" + (tableHtml.split("<tr").find(r => r.includes(label)) || "");

  // Base row: exact perturbation, mean NPV, both range endpoints, coverage, both sacrifice eur and pct
  const baseRow = getRow("Base Scenario");
  assert.ok(baseRow.includes("Baseline economic parameters."), "Exact base perturbation");
  assert.ok(baseRow.includes("€11,200"), "Base mean NPV");
  assert.ok(baseRow.includes("€9,500") && baseRow.includes("€12,800"), "Both base weather range endpoints");
  assert.ok(baseRow.includes("81.2%"), "Base coverage");
  assert.ok(baseRow.includes("€350") && baseRow.includes("3.1%"), "Both sacrifice EUR and pct");

  // Lower export row: exact perturbation, both range endpoints, both sacrifice
  const expRow = getRow("Lower Export");
  assert.ok(expRow.includes("Export compensation drops by 40%."), "Lower export perturbation");
  assert.ok(expRow.includes("€7,100") && expRow.includes("€9,600"), "Both lower export range endpoints");
  assert.ok(expRow.includes("€420") && expRow.includes("5.0%"), "Both lower export sacrifice EUR and pct");
  assert.ok(/robust/i.test(expRow), "Lower export isolated row marked robust");

  // Higher cost row: mean zero no-install, layout shift not robust
  const costRow = getRow("Higher Cost");
  assert.ok(costRow.includes("€0"), "Mean zero no-install capex/NPV");
  assert.ok(!/robust/i.test(costRow), "Layout shift isolated row not robust");

  // Grid limit row: UNAVAILABLE and reason, never robust
  const gridRow = getRow("Grid Limit");
  assert.ok(gridRow.includes("UNAVAILABLE") && gridRow.includes("Grid constraint violated."), "Unavailable status and reason");
  assert.ok(!/robust/i.test(gridRow), "Unavailable isolated row never robust");

  // Higher APR row: unknown regret, never robust
  const aprRow = getRow("Higher APR");
  assert.ok(!/robust/i.test(aprRow), "Unknown regret isolated row never robust");

  // Banner / verdict: exact robust count 2 of 5 (base + lower_export, 1 unavailable, 1 unknown)
  const verdictText = $("solarSensitivityScenariosGrid").innerHTML;
  assert.ok(verdictText.includes("solar-robustness-verdict"), "solar-robustness-verdict class present");
  assert.ok(/2\s*(?:of|\/)\s*5/i.test(verdictText), "Exact robust count asserted in banner: 2 of 5");
});

test("10. Payback delta aligns with displayed endpoints, zero is neutral, avoids -0.0, and equity label is expanded", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();
  ctx.current.result.finance.pv_incremental.discounted_payback_of_mean_cash_flows = 6.04;

  const makeChoices = candPayback => ({
    options: { financial_basis: "project" },
    recommendation_choices: {
      financial: {
        panel_count: 18,
        kwp: 9.0,
        battery_kwh: 0,
        capex_eur: 15000,
        project_npv_mean: 5000,
        discounted_payback_of_mean_cash_flows: candPayback,
        roles: ["Best financial return"]
      },
      balanced: {
        panel_count: 18,
        kwp: 9.0,
        battery_kwh: 0,
        capex_eur: 15000,
        project_npv_mean: 5000,
        discounted_payback_of_mean_cash_flows: candPayback,
        roles: ["Main recommendation", "Balanced choice"]
      }
    }
  });

  // 1. Displayed payback endpoints 6.04 and 6.06 show 6.0/6.1 delta +0.1 y bad, and equity label expanded
  ctx.renderSolarRecommendationChoices(makeChoices(6.06));
  let html = $("solarChoiceCards").innerHTML;
  assert.ok(html.includes("6.0 y"), "Current payback endpoint displays 6.0 y");
  assert.ok(html.includes("6.1 y"), "Main candidate payback endpoint displays 6.1 y");
  assert.ok(
    /<span class="solar-delta-pill bad">\(\+0\.1 y\)<\/span>/.test(html),
    "Payback delta between 6.04 (disp 6.0) and 6.06 (disp 6.1) shows +0.1 y with bad styling"
  );
  assert.ok(
    html.includes('<small class="muted">equity</small>') && !html.includes('<small class="muted">eq</small>'),
    "Equity value annotation must spell out equity"
  );

  // 2. Endpoints 6.01 and 6.04 yield zero delta with neutral class, not good/green or bad/red
  ctx.current.result.finance.pv_incremental.discounted_payback_of_mean_cash_flows = 6.01;
  ctx.renderSolarRecommendationChoices(makeChoices(6.04));
  html = $("solarChoiceCards").innerHTML;
  assert.ok(
    /<span class="solar-delta-pill neutral">\([+]?0\.0 y\)<\/span>/.test(html),
    "Endpoints 6.01 and 6.04 yielding zero displayed delta must use neutral styling"
  );
  assert.ok(
    !/<span class="solar-delta-pill (?:good|bad)">\([+]?0\.0 y\)<\/span>/.test(html),
    "Zero displayed payback delta must not have good or bad styling"
  );

  // 3. Negative tiny delta avoids -0.0
  ctx.current.result.finance.pv_incremental.discounted_payback_of_mean_cash_flows = 6.04;
  ctx.renderSolarRecommendationChoices(makeChoices(6.03));
  html = $("solarChoiceCards").innerHTML;
  assert.ok(!html.includes("-0.0"), "Negative tiny delta must avoid displaying -0.0");
});

test("11. Recommendations assert actual 21 South+1 Dormer vs 22 South 12 vs 13 switches across both financial bases", () => {
  const { ctx, $ } = createHarness();
  ctx.current.result = makeDefaultCurrentResult();
  ctx.routeDisplayForCandidate = item => {
    const sw = item?.settings?.routing_switch_hour;
    return `Scheduled AM/PM · switch ${sw}:00 · Mar–Oct`;
  };

  ['project', 'after_financing'].forEach(basis => {
    const payload = {
      options: { financial_basis: basis },
      recommendation_choices: {
        balanced: {
          panel_count: 22,
          kwp: 11.0,
          battery_kwh: 5.0,
          capex_eur: 18000,
          project_npv_mean: 9200,
          equity_npv_mean: 9600,
          autarky_mean: 0.88,
          roof_counts: { south: 21, dormer: 1 },
          settings: { routing_priority: "scheduled", routing_switch_hour: 12, scheduled_start_month: 3, scheduled_end_month: 10 },
          roles: ["Main recommendation", "Balanced choice"]
        },
        financial: {
          panel_count: 22,
          kwp: 11.0,
          battery_kwh: 5.0,
          capex_eur: 18000,
          project_npv_mean: 9500,
          equity_npv_mean: 10000,
          autarky_mean: 0.87,
          roof_counts: { south: 22 },
          settings: { routing_priority: "scheduled", routing_switch_hour: 13, scheduled_start_month: 3, scheduled_end_month: 10 },
          roles: ["Best financial return"]
        }
      }
    };

    ctx.renderSolarRecommendationChoices(payload);
    const html = $("solarChoiceCards").innerHTML;
    assert.ok(html.includes("Same size · different roof layout &amp; schedule") || html.includes("Same size · different roof layout & schedule"), `Basis ${basis}: Must show same-size badge`);
    assert.ok(html.includes("21 South, 1 Dormer"), `Basis ${basis}: Balanced card displays roof breakdown (21 South, 1 Dormer)`);
    assert.ok(html.includes("22 South"), `Basis ${basis}: Financial card displays roof breakdown (22 South)`);
    assert.ok(html.includes("<strong>switch 12:00</strong>"), `Basis ${basis}: Balanced card displays bold switch 12:00`);
    assert.ok(html.includes("<strong>switch 13:00</strong>"), `Basis ${basis}: Financial card displays bold switch 13:00`);
    assert.ok(!html.includes("Same hardware · different schedule"), `Basis ${basis}: Different roof placement must not claim identical hardware`);
  });
});
