/* Run with Node: Comprehensive unit and regression test suite for Stage 6A Heat Pump UI controls & state. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

// Load live schema from committed factory fixture (safe across sandbox environments without child processes)
const schemaData = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/heatpump_schema_fixture.json'), 'utf8'));

function createTestHarness() {
  const makeElem = (props = {}) => ({
    id: '',
    addEventListener: () => {},
    removeEventListener: () => {},
    querySelectorAll: () => [],
    querySelector: () => null,
    textContent: '',
    innerHTML: '',
    value: '',
    disabled: false,
    hidden: false,
    classList: { toggle: () => {}, add: () => {}, remove: () => {}, contains: () => false },
    setAttribute: () => {},
    getAttribute: () => null,
    style: { display: '' },
    ...props
  });

  const elements = {
    controls: makeElem({ innerHTML: '', oninput: null, onchange: null, onclick: null, onkeydown: null }),
    pinnedControlsList: makeElem({ innerHTML: '', oninput: null, onchange: null, onclick: null, onkeydown: null }),
    pinnedAssumptions: makeElem({ hidden: false }),
    sidebarFilterMode: makeElem({
      querySelectorAll: () => [],
      children: []
    }),
    sidebarFilterStatus: makeElem({ textContent: '' }),
    sidebarSearchHint: makeElem({ hidden: true }),
    sidebarEmptyState: makeElem({ hidden: true, textContent: '' }),
    modifiedCountBadge: makeElem({ textContent: '', className: '' }),
    activeTabAssumptionsPill: makeElem({ textContent: '' }),
    status: makeElem({ textContent: '' }),
    error: makeElem({ hidden: true, textContent: '' }),
    systemSummary: makeElem({ innerHTML: '' }),
    search: makeElem({ value: '' }),
    solarScenario: makeElem({ value: 'baseline', options: [{ value: 'baseline', textContent: 'Historical electricity' }, { value: 'electrified', textContent: 'Electrified · gas eliminated' }, { value: 'ev', textContent: 'Electrified + EV' }] }),
    batScenario: makeElem({ value: 'baseline', options: [{ value: 'baseline', textContent: 'Historical electricity' }, { value: 'electrified', textContent: 'Electrified · gas eliminated' }, { value: 'ev', textContent: 'Electrified + EV' }] }),
    dayhourScenario: makeElem({ value: 'baseline', options: [{ value: 'baseline', textContent: 'Historical electricity' }, { value: 'electrified', textContent: 'Electrified · gas eliminated' }, { value: 'ev', textContent: 'Electrified + EV' }] }),
    hpContextNoticeSummary: makeElem({ textContent: '', hidden: true }),
    hpContextNoticeDesign: makeElem({ textContent: '', hidden: true }),
    hpContextNoticeBehavior: makeElem({ textContent: '', hidden: true }),
    hpBaselineNoticeSummary: makeElem({ innerHTML: '', textContent: '', hidden: true }),
    hpBaselineNoticeBehavior: makeElem({ innerHTML: '', textContent: '', hidden: true }),
    hpDisconnectError: makeElem({ textContent: '', hidden: true }),
    mapValidationStatus: makeElem({ className: '', textContent: '', innerHTML: '' }),
    heatPumpReportPanel: makeElem({ hidden: true })
  };

  const behaviorButtons = [
    { dataset: { behaviorScen: 'baseline' }, classList: { toggle: () => {} }, setAttribute: () => {}, textContent: 'Historical electricity' },
    { dataset: { behaviorScen: 'electrified' }, classList: { toggle: () => {} }, setAttribute: () => {}, textContent: 'Electrified · gas eliminated' },
    { dataset: { behaviorScen: 'ev' }, classList: { toggle: () => {} }, setAttribute: () => {}, textContent: 'Electrified + EV' }
  ];

  const doc = {
    getElementById: id => elements[id] || makeElem({ id }),
    querySelector: sel => {
      if (sel === '#hpDisconnectError') return elements.hpDisconnectError;
      if (sel === '#mapValidationStatus') return elements.mapValidationStatus;
      if (sel.startsWith('#')) return elements[sel.slice(1)] || null;
      if (sel === '.hp-controls-wrap') return elements.controls?.querySelector?.('.hp-controls-wrap') || null;
      return null;
    },
    querySelectorAll: sel => {
      if (sel === '[data-behavior-scen]') return behaviorButtons;
      if (sel === '[data-behavior-scen="electrified"]') return [behaviorButtons[1]];
      if (sel === '[data-behavior-scen="ev"]') return [behaviorButtons[2]];
      if (sel === '#solarScenario option[value="electrified"]') return [elements.solarScenario.options[1]];
      if (sel === '#solarScenario option[value="ev"]') return [elements.solarScenario.options[2]];
      if (sel === '#batScenario option[value="electrified"]') return [elements.batScenario.options[1]];
      if (sel === '#batScenario option[value="ev"]') return [elements.batScenario.options[2]];
      if (sel === '#dayhourScenario option[value="electrified"]') return [elements.dayhourScenario.options[1]];
      if (sel === '#dayhourScenario option[value="ev"]') return [elements.dayhourScenario.options[2]];
      return [];
    },
    addEventListener: () => {}
  };

  const win = {
    addEventListener: () => {},
    removeEventListener: () => {},
    innerWidth: 1024,
    innerHeight: 768,
    document: doc
  };
  win.window = win;

  const context = vm.createContext({
    document: doc,
    window: win,
    structuredClone: global.structuredClone,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout: (fn) => setTimeout(fn, 0),
    clearTimeout: () => {},
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    console
  });

  vm.runInContext(appSource, context);
  const run = code => vm.runInContext(code, context);
  context.CATEGORY_MAP = run('CATEGORY_MAP');
  context.VIEW_RELEVANT_INPUTS = run('VIEW_RELEVANT_INPUTS');
  context.HP_SPEC_KEYS = run('HP_SPEC_KEYS');
  context.HP_ALL_KEYS_SET = run('HP_ALL_KEYS_SET');
  context.HP_COST_KEYS_SET = run('HP_COST_KEYS_SET');
  context.HP_LEGACY_KEYS = run('HP_LEGACY_KEYS');
  context.FRIENDLY_NAMES = run('FRIENDLY_NAMES');
  Object.defineProperty(context, 'priorHouseholdScenario', {
    get() { return run('priorHouseholdScenario'); },
    set(v) { run(`priorHouseholdScenario = ${JSON.stringify(v)};`); }
  });
  run(`schema = ${JSON.stringify(schemaData)};`);
  run(`settings = Object.assign({}, schema.defaults);`);
  Object.defineProperty(context, 'schema', {
    get() { return run('schema'); },
    set(v) { run(`schema = ${JSON.stringify(v)};`); }
  });
  Object.defineProperty(context, 'settings', {
    get() { return run('settings'); },
    set(v) {
      run('for (const k of Object.keys(settings)) delete settings[k];');
      run(`Object.assign(settings, ${JSON.stringify(v)});`);
    }
  });
  Object.defineProperty(context, 'quote', {
    get() { return run('quote'); },
    set(v) { run(`quote = ${JSON.stringify(v)};`); }
  });

  return { context, elements, behaviorButtons, run };
}

console.log('--- Running Stage 6A Heat Pump UI Controls & State Tests ---');

// Test 1: Factory default is off and control renders exact label with switch role
{
  const { context } = createTestHarness();
  assert.equal(context.schema.defaults.planning_heat_pump, false, 'factory default must be false');
  assert.equal(context.settings.planning_heat_pump, false, 'initial settings has planning_heat_pump false');

  const rec = context.schema.records.find(r => r.name === 'planning_heat_pump');
  assert.ok(rec, 'planning_heat_pump record exists in schema');
  const rendered = context.control(rec);
  assert.match(rendered, /role="switch"/, 'renders accessible switch role');
  assert.match(rendered, /aria-label="Planning a heat pump"/, 'exact switch aria-label Planning a heat pump');
  assert.match(rendered, />Planning a heat pump<\/span>/, 'exact switch text Planning a heat pump');
  assert.match(rendered, /control-planning-heat-pump/, 'has distinct CSS class');
  console.log('✔ Test 1: Factory default off and exact switch label/role passed');
}

// Test 2: View relevance & categorization
{
  const { context } = createTestHarness();
  assert.equal(context.CATEGORY_MAP.heat_pump, 'Household demand', 'heat_pump group maps to Household demand');
  assert.ok(context.VIEW_RELEVANT_INPUTS.summary.keys.has('planning_heat_pump'), 'summary view includes planning_heat_pump key');
  assert.ok(context.VIEW_RELEVANT_INPUTS.summary.groups.has('heat_pump'), 'summary view includes heat_pump group');
  assert.ok(context.VIEW_RELEVANT_INPUTS.design.groups.has('heat_pump'), 'design view includes heat_pump group');
  assert.ok(context.VIEW_RELEVANT_INPUTS.behavior.groups.has('heat_pump'), 'behavior view includes heat_pump group');
  assert.ok(context.VIEW_RELEVANT_INPUTS.assumptions.all, 'assumptions includes all');
  console.log('✔ Test 2: View relevance in summary, design, behavior and assumptions passed');
}

// Test 3: Enabling from baseline auto-selects electrified, syncs household selectors, and shows plain notice
{
  const { context, elements } = createTestHarness();
  context.settings.load_scenario = 'baseline';
  assert.equal(context.settings.load_scenario, 'baseline');

  context.editSetting('planning_heat_pump', true);
  assert.equal(context.settings.planning_heat_pump, true);
  assert.equal(context.settings.load_scenario, 'electrified', 'enabling from baseline auto-switches to electrified');
  assert.equal(context.priorHouseholdScenario, 'baseline', 'stores prior session household scenario');

  assert.equal(elements.solarScenario.value, 'electrified', 'solarScenario selector synced');
  assert.equal(elements.batScenario.value, 'electrified', 'batScenario selector synced');
  assert.equal(elements.dayhourScenario.value, 'electrified', 'dayhourScenario selector synced');

  assert.equal(elements.hpContextNoticeSummary.hidden, false, 'context notice is visible in summary');
  assert.match(elements.hpContextNoticeSummary.textContent, /Radiators \+ hot water plan; gas cooking retained unless converted/, 'shows plain notice');
  assert.equal(elements.hpBaselineNoticeSummary.hidden, true, 'baseline notice is hidden');
  console.log('✔ Test 3: Enabling from baseline auto-selects electrified and syncs selectors passed');
}

// Test 4: Does not force EV or change EV start
{
  const { context } = createTestHarness();
  context.settings.load_scenario = 'ev';
  const origEvStart = context.settings.ev_start_year;

  context.editSetting('planning_heat_pump', true);
  assert.equal(context.settings.load_scenario, 'ev', 'enabling while on EV does not override to electrified');
  assert.equal(context.settings.ev_start_year, origEvStart, 'ev_start_year unchanged');
  console.log('✔ Test 4: Does not force EV or change EV start passed');
}

// Test 5: Preserves legacy SCOP, standalone DHW, and heating schedule values on toggle
{
  const { context } = createTestHarness();
  context.settings.load_scenario = 'baseline';
  context.settings.scop = 5.25;
  context.settings.dhw_scop = 3.65;
  context.settings.heating_on_hour = 7;
  context.settings.heating_off_hour = 10;
  context.settings.heating_evening_on_hour = 17;
  context.settings.heating_evening_off_hour = 21;

  // Turn on
  context.editSetting('planning_heat_pump', true);
  assert.equal(context.settings.scop, 5.25, 'legacy scop preserved while plan on');
  assert.equal(context.settings.dhw_scop, 3.65, 'legacy dhw_scop preserved while plan on');
  assert.equal(context.settings.heating_on_hour, 7, 'legacy heating_on_hour preserved while plan on');

  // Turn off
  context.editSetting('planning_heat_pump', false);
  assert.equal(context.settings.planning_heat_pump, false);
  assert.equal(context.settings.scop, 5.25, 'legacy scop intact after turning off');
  assert.equal(context.settings.dhw_scop, 3.65, 'legacy dhw_scop intact after turning off');
  assert.equal(context.settings.heating_on_hour, 7, 'legacy heating_on_hour intact after turning off');
  assert.equal(context.settings.heating_evening_off_hour, 21, 'legacy heating_evening_off_hour intact after turning off');
  assert.equal(context.settings.load_scenario, 'baseline', 'restored previous household scenario');
  console.log('✔ Test 5: Legacy SCOP, DHW, and schedule values preserved intact passed');
}

// Test 6: Deliberate baseline selection visibly explains HP excluded & provides Show heat-pump plan action
{
  const { context, elements } = createTestHarness();
  context.editSetting('planning_heat_pump', true);
  assert.equal(context.settings.load_scenario, 'electrified');

  // Deliberately choose baseline while HP plan is active
  context.editSetting('load_scenario', 'baseline');
  assert.equal(context.settings.load_scenario, 'baseline');
  assert.equal(elements.hpContextNoticeSummary.hidden, true, 'context notice hidden when baseline selected');
  assert.equal(elements.hpBaselineNoticeSummary.hidden, false, 'baseline notice shown');
  assert.match(elements.hpBaselineNoticeSummary.innerHTML, /Historical baseline selected: heat pump space heating and hot water plan are excluded/, 'explains HP exclusion');
  assert.match(elements.hpBaselineNoticeSummary.innerHTML, /data-action="show-hp-plan"/, 'provides Show heat-pump plan action');

  // Trigger action to restore plan
  context.editSetting('load_scenario', 'electrified');
  assert.equal(context.settings.load_scenario, 'electrified');
  assert.equal(elements.hpBaselineNoticeSummary.hidden, true, 'baseline notice hidden after returning to plan');
  assert.equal(elements.hpContextNoticeSummary.hidden, false, 'context notice restored');
  console.log('✔ Test 6: Deliberate baseline selection notice and Show heat-pump plan action passed');
}

// Test 7: Visibility switching: legacy controls hidden when plan on, HP controls hidden when plan off
{
  const { context } = createTestHarness();

  // Initially off
  context.settings.planning_heat_pump = false;
  for (const legacyKey of context.HP_LEGACY_KEYS) {
    const rec = context.schema.records.find(r => r.name === legacyKey);
    if (rec && !['dhw_scop', 'dhw_resistance_efficiency'].includes(legacyKey)) {
      assert.equal(context.isRecordConditionallyAvailable(rec), true, `legacy ${legacyKey} available when HP off`);
    }
  }
  for (const hpKey of context.HP_SPEC_KEYS) {
    if (hpKey === 'planning_heat_pump') continue;
    const rec = context.schema.records.find(r => r.name === hpKey);
    if (rec) {
      assert.equal(context.isRecordConditionallyAvailable(rec), false, `hp key ${hpKey} hidden when HP off`);
    }
  }

  // Turn on
  context.settings.planning_heat_pump = true;
  for (const legacyKey of context.HP_LEGACY_KEYS) {
    const rec = context.schema.records.find(r => r.name === legacyKey);
    if (rec) {
      assert.equal(context.isRecordConditionallyAvailable(rec), false, `legacy ${legacyKey} hidden when HP on`);
    }
  }
  const hpNominalRec = context.schema.records.find(r => r.name === 'hp_nominal_kw');
  assert.equal(context.isRecordConditionallyAvailable(hpNominalRec), true, 'hp_nominal_kw available when HP on');
  console.log('✔ Test 7: Legacy hidden when plan on, HP hidden when plan off passed');
}

// Test 8: Non-HP heating and demand controls remain visible & distinct
{
  const { context } = createTestHarness();
  context.settings.planning_heat_pump = true;

  const outdoorProxy = context.schema.records.find(r => r.name === 'heating_balance_temp');
  const boilerEff = context.schema.records.find(r => r.name === 'boiler_efficiency');
  const cookingSmc = context.schema.records.find(r => r.name === 'cooking_smc_annual');
  const summerAc = context.schema.records.find(r => r.name === 'summer_ac_multiplier');

  assert.equal(context.isRecordConditionallyAvailable(outdoorProxy), true, 'outdoor balance temp proxy remains available');
  assert.equal(context.isRecordConditionallyAvailable(boilerEff), true, 'boiler efficiency remains available');
  assert.equal(context.isRecordConditionallyAvailable(cookingSmc), true, 'cooking smc remains available');
  assert.equal(context.isRecordConditionallyAvailable(summerAc), true, 'summer ac multiplier remains available');

  const proxyTitle = context.FRIENDLY_NAMES.heating_balance_temp || context.FRIENDLY_NAMES.outdoor_heating_balance_temp;
  const hpIndoorTitle = context.FRIENDLY_NAMES.hp_indoor_temp;
  assert.match(proxyTitle, /space heating threshold proxy/, 'outdoor heating balance temp clearly labeled as proxy');
  assert.match(hpIndoorTitle, /Indoor heating setpoint/, 'hp_indoor_temp clearly distinct setpoint');
  console.log('✔ Test 8: Non-HP demand controls remain visible & distinct passed');
}

// Test 9: Friendly labels (no "Hp " prefix), thermal units, and steps
{
  const { context } = createTestHarness();
  for (const key of context.HP_SPEC_KEYS) {
    const label = context.FRIENDLY_NAMES[key] || context.title(key);
    assert.ok(!label.startsWith('Hp '), `label for ${key} must not start with 'Hp ': got '${label}'`);
    assert.ok(!label.startsWith('Hp_'), `label for ${key} must not start with 'Hp_': got '${label}'`);
  }

  const nomRec = context.schema.records.find(r => r.name === 'hp_nominal_kw');
  const nomControl = context.control(nomRec);
  assert.match(nomControl, /kW th/, 'nominal capacity displays kW th unit');
  assert.equal(context.numericStep(nomRec), 0.5, 'nominal capacity step is 0.5');

  // Verify unit 'boolean' is hidden next to switches
  for (const boolKey of ['hp_disconnect_gas', 'hp_electric_cooking', 'hp_hygiene_enabled', 'planning_heat_pump']) {
    const rec = context.schema.records.find(r => r.name === boolKey);
    if (rec) {
      const ctrlHtml = context.control(rec);
      assert.doesNotMatch(ctrlHtml, />boolean<\/span>/, `must not display 'boolean' unit text for switch ${boolKey}`);
    }
  }

  // Verify continuous choice label is (24h available), not (24h constant)
  const schedRec = context.schema.records.find(r => r.name === 'hp_schedule_mode');
  const schedControl = context.control(schedRec);
  assert.match(schedControl, /Continuous \(24h available\)/, 'continuous mode labeled 24h available');
  assert.doesNotMatch(schedControl, /Continuous \(24h constant\)/, 'does not claim constant compressor');

  // Verify exact headline button Planning a heat pump
  const planRec = context.schema.records.find(r => r.name === 'planning_heat_pump');
  const planLabel = context.FRIENDLY_NAMES.planning_heat_pump || context.title('planning_heat_pump');
  assert.equal(planLabel, 'Planning a heat pump', 'headline button label exact');

  const litresRec = context.schema.records.find(r => r.name === 'hp_tank_litres');
  assert.equal(context.numericStep(litresRec), 5, 'tank litres step is 5');

  const capexRec = context.schema.records.find(r => r.name === 'hp_capex');
  assert.equal(context.numericStep(capexRec), 50, 'capex step is 50');

  const maintRec = context.schema.records.find(r => r.name === 'hp_annual_maintenance');
  assert.equal(context.numericStep(maintRec), 10, 'maintenance step is 10');
  console.log('✔ Test 9: Friendly labels, thermal units, and numeric steps passed');
}

// Test 10: Copy current boiler windows action
{
  const { context } = createTestHarness();
  context.settings.ac_heating_windows = [
    { id: 'morning', start_minute: 6 * 60, end_minute: 9 * 60 },
    { id: 'evening', start_minute: 17 * 60, end_minute: 22 * 60 }
  ];
  context.settings.hp_schedule_mode = 'continuous';

  const copied = vm.runInContext('copyCurrentBoilerWindows()', context);
  assert.equal(copied.success, true, 'canonical schedule copy succeeds');
  assert.equal(context.settings.hp_morning_on, 6, 'morning on hour copied');
  assert.equal(context.settings.hp_morning_off, 9, 'morning off hour copied');
  assert.equal(context.settings.hp_evening_on, 17, 'evening on hour copied');
  assert.equal(context.settings.hp_evening_off, 22, 'evening off hour copied');
  assert.equal(context.settings.hp_schedule_mode, 'scheduled', 'schedule mode switched to scheduled');
  assert.equal(context.schema.defaults.hp_schedule_mode, 'continuous', 'continuous remains factory default');
  console.log('✔ Test 10: Copy current boiler windows action passed');
}

// Test 11: Independent HP conditional logic
{
  const { context } = createTestHarness();
  context.settings.planning_heat_pump = true;

  // 1. Windows condition on scheduled
  context.settings.hp_schedule_mode = 'continuous';
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_morning_on' }), false);
  context.settings.hp_schedule_mode = 'scheduled';
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_morning_on' }), true);

  // 2. DHW litres vs gas_calibrated
  context.settings.hp_dhw_demand_mode = 'gas_calibrated';
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_dhw_litres_day' }), false);
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_prior_dhw_loss_fraction' }), true);
  context.settings.hp_dhw_demand_mode = 'litres';
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_dhw_litres_day' }), true);
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_prior_dhw_loss_fraction' }), false);

  // 3. Reheat window condition
  context.settings.hp_reheat_mode = 'continuous';
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_reheat_on' }), false);
  context.settings.hp_reheat_mode = 'scheduled';
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_reheat_on' }), true);

  // 4. Hygiene options condition
  context.settings.hp_hygiene_enabled = false;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_hygiene_temp' }), false);
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_hygiene_weekday' }), false);
  context.settings.hp_hygiene_enabled = true;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_hygiene_temp' }), true);
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_hygiene_weekday' }), true);

  // 5. Cooking capex condition
  context.settings.hp_electric_cooking = false;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_cooking_capex' }), false);
  context.settings.hp_electric_cooking = true;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'hp_cooking_capex' }), true);

  // 6. Disconnect cost condition: when HP is off, gas_disconnect_cost is ordinary and conditionally available (true)
  context.settings.planning_heat_pump = false;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'gas_disconnect_cost' }), true, 'gas_disconnect_cost is available when HP is off');
  // When HP is on, gas_disconnect_cost is gated strictly by hp_disconnect_gas
  context.settings.planning_heat_pump = true;
  context.settings.hp_disconnect_gas = false;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'gas_disconnect_cost' }), false, 'gas_disconnect_cost hidden when hp_disconnect_gas is false and HP is on');
  context.settings.hp_disconnect_gas = true;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'gas_disconnect_cost' }), true, 'gas_disconnect_cost available when hp_disconnect_gas is true and HP is on');
  console.log('✔ Test 11: Independent HP conditional logic passed');
}

// Test 12: Cooking and disconnection separate toggles & validation rules
{
  const { context, elements } = createTestHarness();
  context.settings.cooking_smc_annual = 25.0;
  context.settings.hp_electric_cooking = false;
  context.settings.hp_disconnect_gas = false;

  const mockInput = { checked: true };
  // 1. Attempt to disconnect gas while cooking is still gas
  context.editSetting('hp_disconnect_gas', true, mockInput);
  assert.equal(context.settings.hp_disconnect_gas, false, 'disconnect rejected because cooking is gas');
  assert.equal(mockInput.checked, false, 'checkbox reverted to false');
  assert.equal(elements.hpDisconnectError.hidden, false, 'local error message shown');
  assert.match(elements.hpDisconnectError.textContent, /requires converting cooking to electric/, 'error explains cooking convert required');

  // 2. Convert cooking first
  context.editSetting('hp_electric_cooking', true);
  assert.equal(context.settings.hp_electric_cooking, true);

  // 3. Now disconnection succeeds
  context.editSetting('hp_disconnect_gas', true);
  assert.equal(context.settings.hp_disconnect_gas, true, 'disconnect succeeds after cooking converted');
  assert.equal(elements.hpDisconnectError.hidden, true, 'error cleared');

  // 4. Attempt to turn off electric cooking while disconnected
  const cookInput = { checked: false };
  context.editSetting('hp_electric_cooking', false, cookInput);
  assert.equal(context.settings.hp_electric_cooking, true, 'cannot revert electric cooking while disconnected');
  assert.equal(cookInput.checked, true, 'checkbox kept true');
  assert.equal(elements.hpDisconnectError.hidden, false, 'local error shown');
  assert.match(elements.hpDisconnectError.textContent, /Cannot retain gas cooking while gas service is disconnected/, 'explains re-enable gas service first');

  // 5. Re-enable gas service first, then convert cooking back to gas
  context.editSetting('hp_disconnect_gas', false);
  assert.equal(context.settings.hp_disconnect_gas, false);
  context.editSetting('hp_electric_cooking', false);
  assert.equal(context.settings.hp_electric_cooking, false, 'reverting cooking succeeds when gas service retained');
  console.log('✔ Test 12: Cooking & disconnection separate toggles and validation passed');
}

// Test 13: Manufacturer performance map textarea, validation message & citations
{
  const { context, elements } = createTestHarness();
  const mapRec = context.schema.records.find(r => r.name === 'hp_manufacturer_map_json');
  const rendered = context.control(mapRec);

  assert.match(rendered, /<textarea[^>]*data-setting="hp_manufacturer_map_json"/, 'renders textarea');
  assert.match(rendered, /synthetic fallback/, 'default explains fallback map');
  assert.match(rendered, /https:\/\/www\.stelrad\.ie\/wp-content\/uploads\/2021\/10\/Stelrad-Correction-Factor-Oct-21\.pdf/, 'includes Stelrad exact link');
  assert.match(rendered, /https:\/\/www\.vaillant\.com\/en\/products\/arothermplus\//, 'includes Vaillant exact link');

  // Test invalid JSON string input with HTML escaping check
  context.editSetting('hp_manufacturer_map_json', '<script>alert("xss")</script>');
  assert.equal(context.settings.hp_manufacturer_map_json, '<script>alert("xss")</script>', 'uncommitted input preserved in settings');
  assert.equal(elements.mapValidationStatus.className, 'map-validation-status is-invalid');
  assert.match(elements.mapValidationStatus.innerHTML, /JSON validation message:/, 'validation error displayed');
  assert.match(elements.mapValidationStatus.innerHTML, /&lt;script&gt;/, 'error message HTML escaped');

  // Test valid JSON string input
  const validMap = JSON.stringify({
    metadata: { model: 'aroTHERM plus 7kW', source: 'Vaillant UK Data', includes_defrost: true },
    outdoor_temps: [-7, 2, 7],
    water_temps: [35, 45, 55],
    cop_grid: [[3, 2.5, 2], [3.5, 3, 2.5], [4.5, 4, 3.5]],
    capacity_grid_kw: [[6, 5.5, 5], [7, 6.5, 6], [8, 7.5, 7]]
  });
  context.editSetting('hp_manufacturer_map_json', validMap);
  assert.equal(elements.mapValidationStatus.className, 'map-validation-status is-valid');
  assert.match(elements.mapValidationStatus.innerHTML, /aroTHERM plus 7kW/, 'shows model metadata');
  assert.match(elements.mapValidationStatus.innerHTML, /Defrost included/, 'shows defrost badge');

  // Hygiene cycle plain homeowner notice without regulatory warnings
  const hygieneRec = context.schema.records.find(r => r.name === 'hp_hygiene_enabled');
  const hygieneControl = context.control(hygieneRec);
  assert.match(hygieneControl, /Weekly thermal disinfection cycle/, 'plain homeowner description');
  assert.ok(!hygieneControl.includes('does not certify regulatory hygiene'), 'no unnecessary regulatory warnings');
  console.log('✔ Test 13: Manufacturer map JSON textarea, validation message & citations passed');
}

// Test 14: prepareScenarioSettings helper handles missing planning_heat_pump
{
  const { context } = createTestHarness();

  // Case A: User has saved shared default planning_heat_pump = true
  context.schema.defaults.planning_heat_pump = true;

  // A legacy scenario lacking planning_heat_pump
  const legacyScenario = { south_panels: 14, scop: 4.8 };
  const loadedSettings = context.prepareScenarioSettings(legacyScenario, false);
  assert.equal(loadedSettings.planning_heat_pump, false, 'must explicitly set planning_heat_pump: false even if default is true');
  assert.equal(loadedSettings.south_panels, 14, 'merges scenario values');

  // Case B: A scenario with planning_heat_pump = true
  const hpScenario = { south_panels: 14, planning_heat_pump: true, hp_nominal_kw: 10 };
  const loadedHp = context.prepareScenarioSettings(hpScenario, false);
  assert.equal(loadedHp.planning_heat_pump, true, 'preserves planning_heat_pump: true');
  assert.equal(loadedHp.hp_nominal_kw, 10, 'preserves custom HP settings');

  // Case C: Partial shared patch preserves current state
  context.settings.planning_heat_pump = true;
  const partialPatch = { retail_commodity: 0.28 };
  const patched = context.prepareScenarioSettings(partialPatch, true);
  assert.equal(patched.retail_commodity, 0.28);
  // planning_heat_pump was not forced to false by partial patch
  assert.equal(patched.planning_heat_pump, true);

  // Restore factory default
  context.schema.defaults.planning_heat_pump = false;
  console.log('✔ Test 14: prepareScenarioSettings helper handles missing planning_heat_pump passed');
}

// Test 15: cleanSettingsForBackend removes unknown keys
{
  const { context } = createTestHarness();
  const snapshot = {
    planning_heat_pump: true,
    hp_nominal_kw: 8.5,
    south_panels: 16,
    adhoc_unknown_field: 'illegal',
    hasItemized: true
  };
  context.cleanSettingsForBackend(snapshot);
  assert.equal(snapshot.planning_heat_pump, true);
  assert.equal(snapshot.hp_nominal_kw, 8.5);
  assert.equal(snapshot.south_panels, 16);
  assert.equal(Object.hasOwn(snapshot, 'adhoc_unknown_field'), false, 'unknown field removed');
  assert.equal(Object.hasOwn(snapshot, 'hasItemized'), false, 'hasItemized removed');
  console.log('✔ Test 15: cleanSettingsForBackend prevents unknown fields sent to backend passed');
}

// Test 16: Fingerprint participation and stale invalidation
{
  const { context } = createTestHarness();
  const fp1 = context.inputFingerprint(context.settings, context.quote);

  context.settings.planning_heat_pump = true;
  const fp2 = context.inputFingerprint(context.settings, context.quote);
  assert.notEqual(fp1, fp2, 'planning_heat_pump participates in inputFingerprint');

  context.settings.hp_manufacturer_map_json = '{"model":"custom"}';
  const fp3 = context.inputFingerprint(context.settings, context.quote);
  assert.notEqual(fp2, fp3, 'manufacturer map text participates in inputFingerprint');

  context.settings.hp_disconnect_gas = true;
  const fp4 = context.inputFingerprint(context.settings, context.quote);
  assert.notEqual(fp3, fp4, 'hp_disconnect_gas participates in inputFingerprint');
  console.log('✔ Test 16: Fingerprint participation and stale invalidation passed');
}

// Test 17: Dynamic "gas eliminated" label updating
{
  const { context, elements } = createTestHarness();

  // 1. Off state
  context.settings.planning_heat_pump = false;
  context.updateGasEliminatedLabels();
  assert.equal(context.dayhourScenarioLabel('electrified'), 'Electrified · gas eliminated');
  assert.equal(context.dayhourScenarioLabel('ev'), 'Electrified + EV');
  assert.equal(elements.solarScenario.options[1].textContent, 'Electrified · gas eliminated');
  assert.equal(elements.solarScenario.options[2].textContent, 'Electrified + EV');
  assert.equal(elements.dayhourScenario.options[1].textContent, 'Electrified · gas eliminated');

  // 2. Plan on with gas retained (cooking retained)
  context.settings.planning_heat_pump = true;
  context.settings.hp_disconnect_gas = false;
  context.settings.hp_electric_cooking = false;
  context.updateGasEliminatedLabels();
  assert.equal(context.dayhourScenarioLabel('electrified'), 'Electrified · heat pump (gas cooking retained)');
  assert.equal(context.dayhourScenarioLabel('ev'), 'Electrified + EV · heat pump (gas cooking retained)');
  assert.equal(elements.solarScenario.options[1].textContent, 'Electrified · heat pump (gas cooking retained)');
  assert.equal(elements.solarScenario.options[2].textContent, 'Electrified + EV · heat pump (gas cooking retained)');
  assert.equal(elements.dayhourScenario.options[1].textContent, 'Electrified · heat pump (gas cooking retained)');

  // 3. Plan on with all-electric cooking (gas connection active)
  context.settings.hp_electric_cooking = true;
  context.updateGasEliminatedLabels();
  assert.equal(context.dayhourScenarioLabel('electrified'), 'Electrified · heat pump (all-electric)');
  assert.equal(context.dayhourScenarioLabel('ev'), 'Electrified + EV · heat pump (all-electric)');
  assert.equal(elements.solarScenario.options[2].textContent, 'Electrified + EV · heat pump (all-electric)');

  // 4. Plan on with gas disconnected
  context.settings.hp_disconnect_gas = true;
  context.updateGasEliminatedLabels();
  assert.equal(context.dayhourScenarioLabel('electrified'), 'Electrified · heat pump (gas disconnected)');
  assert.equal(context.dayhourScenarioLabel('ev'), 'Electrified + EV · heat pump (gas disconnected)');
  assert.equal(elements.solarScenario.options[2].textContent, 'Electrified + EV · heat pump (gas disconnected)');

  // 5. Plan turned off restores legacy text
  context.settings.planning_heat_pump = false;
  context.settings.hp_disconnect_gas = false;
  context.updateGasEliminatedLabels();
  assert.equal(context.dayhourScenarioLabel('electrified'), 'Electrified · gas eliminated');
  assert.equal(context.dayhourScenarioLabel('ev'), 'Electrified + EV');
  assert.equal(elements.solarScenario.options[1].textContent, 'Electrified · gas eliminated');
  assert.equal(elements.solarScenario.options[2].textContent, 'Electrified + EV');
  console.log('✔ Test 17: Dynamic gas eliminated labels updating passed');
}

console.log('\nALL 17 STAGE 6A HEAT PUMP UI TESTS PASSED SUCCESSFULLY!');
