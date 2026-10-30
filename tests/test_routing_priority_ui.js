/* Run with Node: test the routing-priority schema, UI labels, resolved-mode pill,
 * charge-bus units, morning ceiling, reso-lution fallbacks and optimizer/candidate
 * integration in the PV Lab app.
 * Backend fixtures are not required: schema is read from assumptions.py and the
 * pure routing helpers are extracted from app.js and executed in a vm context.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const pySource = fs.readFileSync(path.join(__dirname, '../feasibility/assumptions.py'), 'utf8');
const cssSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/style.css'), 'utf8');
const htmlSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/index.html'), 'utf8');

console.log('--- Running PV Lab routing-priority UI tests ---');

// ---------------------------------------------------------------------------
// Part 1: Scenario schema in assumptions.py
// ---------------------------------------------------------------------------
{
  assert.match(
    pySource,
    /'routing_priority': \('auto','enum',\['auto','battery_first','grid_first','scheduled_hybrid','dynamic_reserve'\],'equipment',/,
    'routing_priority is an auto-default enum in the System (equipment) group'
  );
  assert.match(
    pySource,
    /'routing_switch_hour': \(11,'hour',\[9,15\],'equipment',/,
    'routing_switch_hour is an integer hour, bounds 9-15, default 11, System group'
  );

  const iPv = pySource.indexOf("'pv_battery_coupling':");
  const iDispatch = pySource.indexOf("'pv_dispatch_model':");
  const iRouting = pySource.indexOf("'routing_priority':");
  const iSwitch = pySource.indexOf("'routing_switch_hour':");
  const iNext = pySource.indexOf("'inverter_efficiency':");
  assert(iDispatch > iPv, 'dispatch model follows coupling');
  assert(iRouting > iDispatch, 'routing_priority is below dispatch/coupling');
  assert(iSwitch > iRouting, 'routing_switch_hour follows routing_priority');
  assert(iNext > iSwitch, 'routing inputs are immediately below dispatch/coupling');

  assert.match(pySource, /'pv_dispatch_model': \('legacy','choice',\['legacy','physical'\]/, 'dispatch default preserved');
  assert.match(pySource, /'pv_battery_coupling': \('ac_equivalent','topology'/, 'coupling default preserved');
  assert.match(pySource, /'routing_switch_hour': \(11,'hour',\[9,15\],'equipment','Local solar\/clock hour when Scheduled AM\/PM mode switches from Feed-in Priority \(Grid-First\) to Self-Use \(Battery-First\) during high-irradiance months \(Mar-Sep by default\)\.'\)/, 'switch description matches the spec');
  assert.match(pySource, /routing_priority[\s\S]{0,1200}highest median equity NPV/i, 'Auto description records the NPV objective');
  console.log('\u2714 Part 1: schema order, defaults and descriptions passed');
}

// ---------------------------------------------------------------------------
// Part 2: Pure routing helpers extracted from app.js
// ---------------------------------------------------------------------------
function loadRoutingHelpers() {
  const start = appSource.indexOf('/* ROUTING PRIORITY HELPERS (start) */');
  const end = appSource.indexOf('/* ROUTING PRIORITY HELPERS (end) */');
  assert(start >= 0 && end > start, 'Routing helper block must exist in app.js');
  const context = vm.createContext({ console });
  vm.runInContext(appSource.slice(start, end + '/* ROUTING PRIORITY HELPERS (end) */'.length), context);
  return context;
}

{
  const ctx = loadRoutingHelpers();
  const label = ctx.routingPriorityLabel;

  assert.match(label('auto'), /Auto.*NPV/, 'auto label states the objective');
  assert.match(label('battery_first'), /Battery-First.*Load . Battery . Grid/, 'battery_first label is the self-use route');
  assert.match(label('grid_first'), /Grid-First.*Clipping Recapture/, 'grid_first label shows clipping recapture priority');
  assert.match(label('scheduled_hybrid'), /FoxESS.*Grid-First AM . Self-Use PM/, 'scheduled_hybrid label names the EMS scheduler');
  assert.match(label('dynamic_reserve'), /Dynamic Clipping Reserve.*Look-Ahead/, 'dynamic_reserve label names the smart EMS mode');
  assert.equal(ctx.routingPriorityCompact('scheduled_hybrid'), 'Scheduled AM/PM (grid \u2192 battery)', 'compact label for pills');
  console.log('\u2714 Part 2a: explicit human enum labels passed');
}

{
  const ctx = loadRoutingHelpers();

  assert.equal(ctx.resolvedRoutingPriority({ settings: { pv_dispatch_model: 'legacy', routing_priority: 'auto' } }), null, 'legacy dispatch has no resolved physical routing');
  assert.equal(ctx.resolvedRoutingPriority({ settings: { pv_dispatch_model: 'physical', routing_priority: 'auto' } }), null, 'unresolved Auto stays unresolved');
  assert.equal(ctx.resolvedRoutingPriority({ resolved_routing_priority: 'dynamic_reserve', settings: { pv_dispatch_model: 'physical', routing_priority: 'auto' } }), 'dynamic_reserve', 'resolved_routing_priority wins');
  assert.equal(ctx.resolvedRoutingPriority({ settings: { pv_dispatch_model: 'physical', route: 'grid_first', routing_priority: 'auto' } }), 'grid_first', 'settings.route fallback is used');
  assert.equal(ctx.resolvedRoutingPriority({ settings: { pv_dispatch_model: 'physical', routing_priority: 'scheduled_hybrid' } }), 'scheduled_hybrid', 'fixed sidebar lock is honored');
  console.log('\u2714 Part 2b: resolved routing + settings.route fallback passed');
}

{
  const ctx = loadRoutingHelpers();

  const legacy = ctx.routingPillText({ settings: { pv_dispatch_model: 'legacy', routing_priority: 'auto' } });
  assert.match(legacy, /Legacy dispatch.*physical routing not modeled/i, 'legacy dispatch is not claimed as physical routing');

  const resolved = ctx.routingPillText({ resolved_routing_priority: 'scheduled_hybrid', settings: { pv_dispatch_model: 'physical', routing_priority: 'auto' } });
  assert.match(resolved, /Physical routing.*Scheduled AM\/PM/, 'pill prefers resolved_routing_priority');

  const auto = ctx.routingPillText({ settings: { pv_dispatch_model: 'physical', routing_priority: 'auto' } });
  assert.match(auto, /Physical routing.*Auto/, 'pill keeps Auto visible when unresolved');

  const locked = ctx.routingPillText({ settings: { pv_dispatch_model: 'physical', routing_priority: 'grid_first' } });
  assert.match(locked, /Grid-first.*locked/, 'pill flags a fixed sidebar lock');
  console.log('\u2714 Part 2c: header pill text and legacy honesty passed');
}

{
  const ctx = loadRoutingHelpers();

  assert.equal(ctx.chargeBusPhaseLabel('dc_coupled'), 'DC', 'DC coupling charges on the DC bus');
  assert.equal(ctx.chargeBusPhaseLabel('ac_equivalent'), 'AC', 'AC coupling charges on the AC bus');
  assert.match(ctx.chargeBusLabel('dc_coupled'), /DC battery charge input/, 'charge bus label carries the DC phase');
  assert.match(ctx.chargeBusLabel('ac_equivalent'), /AC battery charge input/, 'charge bus label carries the AC phase');

  const split = ctx.routingChargingSplit({
    charged_from_normal_solar_kwh: 1800,
    charged_from_recaptured_clipping_kwh: 320,
    clipped_kwh: 80,
    pv_input_kwh: 6000
  });
  assert.equal(split.normal, 1800, 'normal-solar charging is parsed');
  assert.equal(split.recaptured, 320, 'recaptured-clipping charging is parsed');
  assert.ok(Math.abs(split.clippedPct - 80 / 6000) < 1e-9, 'clipped share is computed against modeled PV input');
  assert.equal(ctx.routingChargingSplit({}), null, 'absent fields yield no split block');
  const zeroClip = ctx.routingChargingSplit({ clipped_kwh: 0 });
  assert.ok(zeroClip && zeroClip.hasClipped && zeroClip.clipped === 0, 'explicit zero clipping is still a valid split');
  console.log('\u2714 Part 2d: charge-bus units + charging source split passed');
}

{
  const ctx = loadRoutingHelpers();

  assert.equal(
    ctx.routeDisplayForCandidate({ settings: { routing_priority: 'scheduled_hybrid', routing_switch_hour: 11 } }),
    'Scheduled AM/PM (grid \u2192 battery) \u00b7 switch 11:00',
    'candidate route includes the scheduled switch hour'
  );
  assert.equal(
    ctx.routeDisplayForCandidate({ resolved_routing_priority: 'dynamic_reserve', settings: { routing_priority: 'auto', routing_switch_hour: 10 } }),
    'Dynamic clipping reserve \u00b7 switch 10:00',
    'resolved candidate route includes the switch hour'
  );
  assert.equal(ctx.routeDisplayForCandidate(null), '\u2014', 'missing candidate is rendered as a dash');
  const scheduled = {settings:{pv_dispatch_model:'physical',routing_priority:'scheduled_hybrid',routing_switch_hour:14,scheduled_start_month:11,scheduled_end_month:2}};
  assert.match(ctx.routeDisplayForCandidate(scheduled), /switch 14:00.*Nov–Feb/, 'candidate shows optimized winter wraparound months');
  assert.equal(ctx.routeDisplayForResult(scheduled), ctx.routeDisplayForCandidate(scheduled), 'applied result shows the same full schedule');
  assert.match(ctx.routeDisplayForCandidate({routing_priority:'scheduled_hybrid',routing_switch_hour:9,scheduled_start_month:1,scheduled_end_month:12}), /switch 9:00.*Jan–Dec/, 'top-level schedule fields are displayed');
  assert.match(
    ctx.routeDisplayForResult({ settings: { pv_dispatch_model: 'physical', routing_priority: 'battery_first' } }),
    /Battery-first/,
    'result route uses the requested fixed mode'
  );
  assert.match(
    ctx.routeDisplayForResult({ settings: { pv_dispatch_model: 'legacy' } }),
    /Legacy dispatch/,
    'legacy result route is honest'
  );
  console.log('\u2714 Part 2e: candidate/benchmark route display passed');
}

// ---------------------------------------------------------------------------
// Part 3: UI integration points in app.js / index.html / style.css
// ---------------------------------------------------------------------------
{
  assert.match(appSource, /routing_priority:'Routing priority \(EMS work mode\)'/, 'friendly schema label present');
  assert.match(appSource, /routing_switch_hour:'Scheduled mode switch hour \(AM \u2192 PM\)'/, 'switch-hour friendly label present');
  assert.match(appSource, /n==='routing_priority'\?routingPriorityLabel\(c\)/, 'enum select renders full human labels');

  assert.match(appSource, /'routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month', 'inverter_efficiency'/, 'applySolarCandidate copies routing together with topology');
  assert.match(appSource, /'routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month', 'inverter_efficiency', 'ilr_ac_max'/, 'applied topology fields are re-synced including routing');
  assert.match(appSource, /const route = c\.settings\?\.routing_priority \|\| c\.routing_priority \|\| ''/, 'candidate signature reads routing priority');
  assert.match(appSource, /\$\{route\}_\$\{resRoute\}_\$\{swHour\}/, 'candidate signature includes requested and resolved routing priorities');
  assert.match(appSource, /routing_switch_hour \?\? c\.routing_switch_hour/, 'candidate signature includes the switch hour');

  assert.match(appSource, /Charging source: from normal solar \(sub-inverter\)/, 'How it behaves shows the charging source split');
  assert.match(appSource, /from recaptured DC clipping \(over-inverter\)/, 'How it behaves shows recaptured clipping charging');
  assert.match(appSource, /Dispatch routing: <strong>\$\{esc\(routingModeLabel\)\}/, 'clipping reconciliation states the active route');

  assert.match(appSource, /chargeBusPhaseLabel\(dhCoupling\)/, 'dayhour charge bus label is DC/AC aware');
  assert.match(appSource, /const chargeBusLbl=typeof chargeBusLabel==='function'\?chargeBusLabel\(dhCoupling\)/, 'charge bus label is built from the phase');
  assert.match(appSource, /h\.soc_ceiling_sub_kwh/, 'typical-day chart plots the morning ceiling');
  assert.match(appSource, /r\.soc_ceiling_sub_kwh/, 'dayhour SOC chart plots the morning ceiling');
  assert.match(appSource, /metric==='clipping'&&heatmapRoute/, 'clipping heatmap tooltip includes the dispatch route');

  assert.match(appSource, /Solar choice route|Routing priority<\/div>/, 'recommendation cards expose the selected route');
  assert.match(appSource, /Routing priority: <strong>\$\{esc\(routeText\(best\)\)\}/, 'best-configuration card shows the route');
  assert.match(appSource, /<th>Routing<\/th>/, 'candidate table has a routing column');
  assert.match(appSource, /Routing priority \(EMS work mode\)<\/div><div class="value">/, 'solar search summary benchmark shows the route');
  assert.match(appSource, /Routing: \$\{esc\(routeText\(p\.r\)\)\}/, 'pareto heatmap tooltip shows the route');

  assert.match(cssSource, /\.pills \.routing-mode-pill\b/, 'routing pill styling present');
  assert.match(htmlSource, /id="systemTags"/, 'header pill bar container present');
  console.log('\u2714 Part 3: UI/schema integration points passed');
}

console.log('All routing-priority UI tests passed.');
