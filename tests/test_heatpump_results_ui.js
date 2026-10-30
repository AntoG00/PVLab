/* Run with Node: Comprehensive unit and regression test suite for Stage 6C Heat Pump UI Results Display & Focused Controls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

// Load committed schema and results fixtures (safe across sandbox environments without runtime child processes)
const schemaFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/heatpump_schema_fixture.json'), 'utf8'));
const resultsFixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/heatpump_results_fixture.json'), 'utf8'));

function createTestHarness() {
  const elements = new Map();
  const makeElem = (id, props = {}) => ({
    id,
    addEventListener: () => {},
    removeEventListener: () => {},
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => makeElem('mockClosest_' + id),
    textContent: '',
    innerHTML: '',
    value: id === 'hourSlider' ? '12' : '',
    disabled: false,
    hidden: false,
    style: {},
    classList: { toggle: () => {}, add: () => {}, remove: () => {}, contains: () => false },
    setAttribute: () => {},
    getAttribute: () => null,
    ...props
  });

  const getElem = id => {
    if (!elements.has(id)) {
      elements.set(id, makeElem(id));
    }
    return elements.get(id);
  };

  const documentMock = {
    getElementById: getElem,
    querySelector: () => null,
    querySelectorAll: (selector) => {
      if (selector === '[data-control]') return [];
      if (selector === '[data-behavior-scen]') return [];
      return [];
    },
    addEventListener: () => {}
  };

  const windowMock = {
    location: { reload: () => {} },
    addEventListener: () => {},
    localStorage: { getItem: () => null, setItem: () => {} }
  };

  const sandbox = {
    document: documentMock,
    window: windowMock,
    $: getElem,
    console,
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 1,
    clearInterval: () => {},
    localStorage: { getItem: () => null, setItem: () => {} },
    fetch: async () => ({ ok: false, json: async () => ({}) })
  };

  const context = vm.createContext(sandbox);
  vm.runInContext(appSource, context);
  const run = code => vm.runInContext(code, context);
  run(`schema = ${JSON.stringify(schemaFixture)};`);
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
  Object.defineProperty(context, 'current', {
    get() { return run('current'); },
    set(v) {
      sandbox._newCurrent = v;
      run(`
        current = _newCurrent;
        if (typeof inputFingerprint === 'function' && typeof settings !== 'undefined') {
          const sClone = typeof deepClone === 'function' ? deepClone(settings) : Object.assign({}, settings);
          delete sClone.hasItemized;
          if (typeof cleanSettingsForBackend === 'function') cleanSettingsForBackend(sClone);
          const qClone = typeof quote !== 'undefined' ? (typeof deepClone === 'function' ? deepClone(quote) : Object.assign({}, quote)) : { line_items: [] };
          acceptedInputFingerprint = inputFingerprint(sClone, qClone);
        }
      `);
    }
  });

  return { context, elements, getElem };
}

console.log('--- Running Stage 6C Heat Pump UI Results Display & Reporting Tests ---');

// Test 1: Node fixture host independence (no child_process spawn)
{
  assert.equal(typeof resultsFixture, 'object', 'committed results fixture loads as JSON object');
  assert.ok(resultsFixture.heat_pump, 'heat_pump payload is present in fixture');
  assert.ok(resultsFixture.heat_pump.envelopes, 'envelopes present in fixture');
  assert.ok(resultsFixture.heat_pump.metadata, 'metadata present in fixture');
  assert.ok(resultsFixture.heat_pump.gas_finance, 'gas_finance present in fixture');
  console.log('✔ Test 1: Node fixture host independence (no runtime child_process) passed');
}

// Test 2: Annual sum of components & electricity/heat balance
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));
  context.current = { result: res };
  context.renderHeatPumpReport(res);

  const panel = getElem('heatPumpReportPanel');
  assert.equal(panel.hidden, false, 'report panel is visible for active HP');
  assert.match(panel.innerHTML, /Electricity consumption breakdown/, 'electricity breakdown table rendered');
  assert.match(panel.innerHTML, /Heat delivery &amp; thermal balance/, 'heat delivery table rendered');

  // Check electricity components sum of means
  const env = res.heat_pump.envelopes;
  const compElec = env.annual_space_compressor_electricity_kwh.mean;
  const dhwCompElec = env.annual_dhw_compressor_electricity_kwh.mean;
  const spaceBackupElec = env.annual_space_backup_electricity_kwh.mean;
  const dhwBackupElec = env.annual_dhw_backup_electricity_kwh.mean;
  const auxElec = env.annual_auxiliary_electricity_kwh.mean;
  const totalElec = env.annual_total_electricity_kwh.mean;

  const sumElec = compElec + dhwCompElec + spaceBackupElec + dhwBackupElec + auxElec;
  assert.ok(Math.abs(sumElec - totalElec) < 1.0, `electricity component means sum (${sumElec}) equals total mean (${totalElec})`);
  assert.match(panel.innerHTML, /Space compressor, DHW compressor, backup resistance, and auxiliary pump\/standby weather means sum to total electricity input/, 'electricity weather means note rendered');
  assert.match(panel.innerHTML, /rounding may leave up to 1 kWh; range endpoints reflect individual weather distributions and are not additive/, 'explains range endpoints not additive and rounding');

  // Check initial and final storage delta on real results fixture
  assert.match(panel.innerHTML, /Cylinder thermal storage delta/, 'cylinder thermal storage delta row rendered');
  const initial=((res.heat_pump.by_weather[0].initial_storage_kwh+res.heat_pump.by_weather[1].initial_storage_kwh)/2).toFixed(2);
  const final=((res.heat_pump.by_weather[0].final_storage_kwh+res.heat_pump.by_weather[1].final_storage_kwh)/2).toFixed(2);
  assert.ok(panel.innerHTML.includes(`Start ${initial} kWh → End ${final} kWh`), 'initial and final analytic storage values disclosed');

  // Verify deliberate different medians vs means with synthetic weather outcomes
  const synthRes = JSON.parse(JSON.stringify(resultsFixture));
  synthRes.heat_pump.by_weather = [
    { annual_space_compressor_electricity_kwh: 1000, annual_dhw_compressor_electricity_kwh: 800, annual_space_backup_electricity_kwh: 10, annual_dhw_backup_electricity_kwh: 50, annual_auxiliary_electricity_kwh: 200, annual_total_electricity_kwh: 2060 },
    { annual_space_compressor_electricity_kwh: 600, annual_dhw_compressor_electricity_kwh: 700, annual_space_backup_electricity_kwh: 50, annual_dhw_backup_electricity_kwh: 10, annual_auxiliary_electricity_kwh: 250, annual_total_electricity_kwh: 1610 },
    { annual_space_compressor_electricity_kwh: 800, annual_dhw_compressor_electricity_kwh: 400, annual_space_backup_electricity_kwh: 20, annual_dhw_backup_electricity_kwh: 0, annual_auxiliary_electricity_kwh: 210, annual_total_electricity_kwh: 1430 }
  ];
  delete synthRes.heat_pump.envelopes;
  context.renderHeatPumpReport(synthRes);
  const synthHtml = getElem('heatPumpReportPanel').innerHTML;
  // Means: space=800, dhw=633, backup=(60+60+20)/3=46.7, aux=220, total=(2060+1610+1430)/3=1700
  assert.match(synthHtml, /800 kWh/, 'renders weather mean for space');
  assert.match(synthHtml, /633 kWh/, 'renders weather mean for dhw');
  assert.match(synthHtml, /1,700 kWh/, 'renders weather mean for total');
  // Coherent backup envelope: run 1 total backup=60, run 2 total backup=60, run 3 total backup=20.
  // Envelope is min=20, max=60 (whereas naive sum of mins is 10+0=10, sum of maxs is 50+50=100)
  assert.match(synthHtml, /20 – 60 kWh/, 'renders coherent per-weather backup envelope 20 - 60 kWh');
  console.log('✔ Test 2: Annual sum of components & balance reconciliation passed');
}

// Even-count fallback medians are arithmetic midpoints of per-run values.
{
  const { context,getElem }=createTestHarness();
  const result=JSON.parse(JSON.stringify(resultsFixture));
  result.heat_pump.envelopes={};
  result.heat_pump.by_weather=[
    {initial_storage_kwh:2,final_storage_kwh:8,storage_delta_kwh:6,
     annual_delivered_space_heat_kwh:100,annual_delivered_draw_heat_kwh:20},
    {initial_storage_kwh:10,final_storage_kwh:2,storage_delta_kwh:-8,
     annual_delivered_space_heat_kwh:200,annual_delivered_draw_heat_kwh:60}
  ];
  context.renderHeatPumpReport(result);
  const html=getElem('heatPumpReportPanel').innerHTML;
  assert.match(html,/Start 6\.00 kWh → End 5\.00 kWh \(delta -1\.00 kWh\)/);
  assert.match(html,/190\.00 <small>kWh th\/yr<\/small>/);
}

// Test 3: Zero / missing data graceful handling (no invented outputs)
{
  const { context, getElem } = createTestHarness();
  const panel = getElem('heatPumpReportPanel');

  // Test missing heat_pump payload
  const emptyRes = {
    settings: { planning_heat_pump: true, load_scenario: 'electrified' },
    finance: { service_comparable: true }
  };
  context.current = { result: emptyRes };
  assert.doesNotThrow(() => context.renderHeatPumpReport(emptyRes), 'missing heat_pump does not throw');
  assert.equal(panel.hidden, false, 'panel is visible');
  assert.match(panel.innerHTML, /telemetry pending or unavailable/i, 'displays pending telemetry notice');

  // Test completely null result
  assert.doesNotThrow(() => context.renderHeatPumpReport(null), 'null result does not throw');
  assert.equal(panel.hidden, true, 'panel hidden on null result');

  // Test missing simulation output fields do not invent values
  const missingRes = JSON.parse(JSON.stringify(resultsFixture));
  missingRes.heat_pump.envelopes = {};
  missingRes.heat_pump.by_weather = [];
  missingRes.heat_pump.metadata = {};
  missingRes.heat_pump.gas_finance = {};
  delete missingRes.settings.hp_tank_litres;
  delete missingRes.settings.hp_tank_target_temp;
  missingRes.settings.hp_tank_loss_w_k = 0; // Legitimate 0 must NOT become 1
  context.renderHeatPumpReport(missingRes);
  const missingHtml = getElem('heatPumpReportPanel').innerHTML;
  assert.doesNotMatch(missingHtml, /11\.63/, 'no invented 11.63 storage fallback');
  assert.doesNotMatch(missingHtml, /10\.75/, 'no invented 10.75 storage fallback');
  assert.doesNotMatch(missingHtml, /718 Smc/, 'no invented 718 Smc fallback');
  assert.doesNotMatch(missingHtml, /412 Smc/, 'no invented 412 Smc fallback');
  assert.doesNotMatch(missingHtml, /€150\/yr/, 'no invented €150 cost fallback');
  assert.match(missingHtml, /standing loss rate of 0\.0 W\/K/, 'legitimate 0 W/K tank loss preserved without converting to 1');
  assert.match(missingHtml, /Not available \(cylinder thermal storage delta not evaluated or untracked\)/, 'null storage adjusted SPF explains reason and does not render 0');

  console.log('✔ Test 3: Zero / missing data graceful handling passed');
}

// Test 4: Shortfall warning & fuel credit withholding when service_comparable is false
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));
  
  // Explicit five-run visibility case; no household shortfall snapshot.
  res.heat_pump.by_weather=Array.from({length:5},(_,i)=>({
    annual_unmet_space_heat_kwh:i===0?4:0,annual_unmet_draw_heat_kwh:0}));
  res.heat_pump.envelopes.annual_unmet_space_heat_kwh={min:0,median:0,mean:.8,max:4};
  res.heat_pump.envelopes.annual_unmet_draw_heat_kwh={min:0,median:0,mean:0,max:0};
  const allowance={min:0,mean:.1,max:.5};
  for(const gas of [res.heat_pump.gas_finance,res.finance.heat_pump_gas_finance]){
    if(gas){gas.unserved_service_allowance=allowance;gas.allowance=allowance;}
  }
  // Set service_comparable to false
  res.finance.service_comparable = false;
  context.current = { result: res };
  context.renderHeatPumpReport(res);

  const panel = getElem('heatPumpReportPanel');
  assert.match(panel.innerHTML, /hp-unmet-warning/, 'warning banner rendered');
  assert.match(panel.innerHTML, /Unmet Thermal Demand Warning/i, 'unmet thermal demand warning heading present');
  assert.match(panel.innerHTML, /avoided gas fuel credit is withheld/, 'fuel credit withholding explicitly disclosed');
  assert.match(panel.innerHTML, /full economic equivalence is not validated/i, 'does not claim validated cost comparison');
  assert.doesNotMatch(panel.innerHTML, /cold-weather snaps/i, 'does not assume cold-weather snaps');
  assert.match(panel.innerHTML, /simulated data envelope/i, 'describes shortfall across simulated data envelope');
  assert.match(panel.innerHTML, /Maximum annual unmet demand reaches 4\.00 kWh space heating \(1 of 5 weather runs affected\)/, 'discloses max shortfall and runs affected');
  assert.match(panel.innerHTML, /median annual unmet demand is 0\.00 kWh space and 0\.00 kWh DHW because most weather runs experience zero or near-zero shortfall/, 'explains why median may be zero');
  assert.match(panel.innerHTML, /no room comfort validation or full hygiene compliance is claimed/, 'explicitly disclaims comfort and hygiene compliance');
  assert.match(panel.innerHTML, /Financial shortfall allowance: mean €0\.10\/yr \(weather range €0\.00 to €0\.50\/yr\)/, 'visibly distinguishes financial allowance mean vs weather range');

  // When service_comparable is true, warning banner must NOT appear
  res.finance.service_comparable = true;
  context.renderHeatPumpReport(res);
  assert.doesNotMatch(panel.innerHTML, /hp-unmet-warning/, 'warning banner removed when service is comparable');
  console.log('✔ Test 4: Shortfall warning & fuel credit withholding passed');
}

// Test 5: Baseline HP excluded / off hidden
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));
  const panel = getElem('heatPumpReportPanel');

  // 1. HP switch off
  res.settings.planning_heat_pump = false;
  res.settings.load_scenario = 'electrified';
  context.renderHeatPumpReport(res);
  assert.equal(panel.hidden, true, 'panel hidden when planning_heat_pump is false');

  // 2. Scenario is baseline
  res.settings.planning_heat_pump = true;
  res.settings.load_scenario = 'baseline';
  context.renderHeatPumpReport(res);
  assert.equal(panel.hidden, true, 'panel hidden when load_scenario is baseline');

  // 3. HP on and electrified scenario
  res.settings.planning_heat_pump = true;
  res.settings.load_scenario = 'electrified';
  context.renderHeatPumpReport(res);
  assert.equal(panel.hidden, false, 'panel visible when HP on and electrified');
  console.log('✔ Test 5: Baseline HP excluded & off hidden passed');
}

// Test 6: Simulated labels and correct ratios
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));
  context.current = { result: res };
  context.renderHeatPumpReport(res);

  const panel = getElem('heatPumpReportPanel');
  // Labels state SIMULATED
  assert.match(panel.innerHTML, /SIMULATED engineering estimates/, 'explicit SIMULATED disclaimer rendered');
  assert.match(panel.innerHTML, /not onsite physical measurements or validated thermal comfort/, 'discloses not physical measurements');
  assert.match(panel.innerHTML, /Gas-calibrated heat\/service approximation/, 'describes gas-calibrated approximation');
  assert.match(panel.innerHTML, /indoor-temperature changes and recovery are not simulated/, 'indoor-temperature changes and recovery are not simulated');

  // Ratios present and correctly categorized
  assert.match(panel.innerHTML, /Achieved space SCOP/, 'achieved space SCOP displayed');
  assert.match(panel.innerHTML, /Compressor \+ backup \(excl\. aux\)/, 'space SCOP excludes auxiliaries');
  assert.match(panel.innerHTML, /Combined delivered SPF/, 'combined delivered SPF displayed');
  assert.match(panel.innerHTML, /Useful delivered heat \/ all electricity/, 'SPF includes all electricity input');
  assert.match(panel.innerHTML, /Storage-adjusted SPF/, 'storage-adjusted SPF displayed');
  assert.match(panel.innerHTML, /Heat-pump unit COP \(map boundary\)/, 'explicit map boundary label for unit COP');
  assert.match(panel.innerHTML, /includes only effects declared by the selected map; additional external pumps\/standby\/backup counted separately/, 'map COP includes only effects declared by selected map');
  assert.match(panel.innerHTML, /fallback is provisional not manufacturer/, 'fallback map is provisional not manufacturer');
  assert.match(panel.innerHTML, /do not transfer air-conditioning or summer cooling ratings/, 'disclaims transferring AC ratings');

  // Exact decimal formatting follows the wholly synthetic fixture values.
  const cop=res.heat_pump.envelopes.seasonal_compressor_cop;
  const spf=res.heat_pump.envelopes.seasonal_performance_factor_storage_adjusted;
  assert.ok(panel.innerHTML.includes(`range ${cop.min.toFixed(2)} – ${cop.max.toFixed(2)}`),'overall COP range formatted with 2 decimals');
  assert.ok(panel.innerHTML.includes(`range ${spf.min.toFixed(2)} – ${spf.max.toFixed(2)}`),'storage adjusted SPF range formatted with 2 decimals');
  console.log('✔ Test 6: Simulated labels and correct ratios passed');
}

// Test 7: Gas connection status reflection
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));

  // Case A: Disconnected
  res.settings.hp_disconnect_gas = true;
  res.settings.hp_electric_cooking = true;
  context.renderHeatPumpReport(res);
  assert.match(getElem('heatPumpReportPanel').innerHTML, /Gas service disconnected/, 'disconnected status rendered');
  assert.match(getElem('heatPumpReportPanel').innerHTML, /All-electric home · gas meter removed · standing charges eliminated/, 'meter removed text');

  // Case B: Retained for cooking
  res.settings.hp_disconnect_gas = false;
  res.settings.hp_electric_cooking = false;
  context.renderHeatPumpReport(res);
  assert.match(getElem('heatPumpReportPanel').innerHTML, /Gas retained for cooking/, 'retained cooking status rendered');
  assert.match(getElem('heatPumpReportPanel').innerHTML, /Meter active · cooking gas modeled/, 'cooking gas modeled note');

  // Case C: Retained but 0 Smc (cooking electrified)
  res.settings.hp_disconnect_gas = false;
  res.settings.hp_electric_cooking = true;
  context.renderHeatPumpReport(res);
  assert.match(getElem('heatPumpReportPanel').innerHTML, /Gas connection retained \(0 Smc\)/, 'retained 0 Smc status rendered');
  assert.match(getElem('heatPumpReportPanel').innerHTML, /no gas backup modeled/, 'explicitly disclaims gas backup modeled');
  console.log('✔ Test 7: Gas connection status reflection passed');
}

// Test 8: Annual gas actual settings derived (not hardcoded)
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));

  // Change metadata gas allocation values to verify they are dynamically derived
  res.heat_pump.metadata.reference_gas_allocation = {
    total_reference_gas_smc: 850,
    heating_reference_smc: 500,
    dhw_reference_smc: 320,
    cooking_reference_smc: 30,
    gas_reference_period: 'Oct 2024 – Sep 2025'
  };
  res.heat_pump.metadata.boiler_efficiency = 0.75;
  res.heat_pump.metadata.boiler_efficiency_status = 'Custom audit efficiency estimate (0.75)';

  context.renderHeatPumpReport(res);
  let panelHtml = getElem('heatPumpReportPanel').innerHTML;

  assert.match(panelHtml, /850 Smc/, 'dynamically derives 850 total gas Smc');
  assert.match(panelHtml, /500 Smc space heating/, 'dynamically derives 500 heating Smc');
  assert.match(panelHtml, /320 Smc DHW/, 'dynamically derives 320 DHW Smc');
  assert.match(panelHtml, /30 Smc cooking/, 'dynamically derives 30 cooking Smc');
  assert.match(panelHtml, /Oct 2024 – Sep 2025/, 'dynamically derives gas reference period');
  assert.match(panelHtml, /75% seasonal efficiency/, 'dynamically derives boiler efficiency');
  assert.match(panelHtml, /Custom audit efficiency estimate \(0\.75\)/, 'dynamically derives boiler status');
  assert.match(panelHtml, /18\.0°C indoor thermostat setpoint \(conceptually distinct from outdoor heating balance threshold\)/, 'indoor thermostat setpoint distinguished from balance threshold');

  // Verify fallback to actual settings.boiler_efficiency (not hp_gas_boiler_efficiency)
  delete res.heat_pump.metadata.boiler_efficiency;
  delete res.heat_pump.metadata.boiler_efficiency_status;
  res.settings.boiler_efficiency = 0.82;
  context.renderHeatPumpReport(res);
  panelHtml = getElem('heatPumpReportPanel').innerHTML;
  assert.match(panelHtml, /82% seasonal efficiency/, 'falls back to actual settings.boiler_efficiency');
  console.log('✔ Test 8: Annual gas actual settings derived passed');
}

// Test 9: Selected hour cards and monthly 24-row table without battery regressions
{
  const { context, getElem } = createTestHarness();

  // Create hourly fixture with heat_pump telemetry
  const hours = Array.from({ length: 24 }, (_, hour) => ({
    hour,
    pv_kwh: hour === 12 ? 2.5 : 0,
    load_kwh: 1.0,
    direct_kwh: hour === 12 ? 1.0 : 0,
    charge_input_kwh: 0,
    discharge_delivered_kwh: 0,
    grid_import_kwh: hour === 12 ? 0 : 1.0,
    grid_export_kwh: hour === 12 ? 1.5 : 0,
    battery_loss_kwh: 0,
    soc_start_kwh: 0,
    soc_end_kwh: 0,
    balance_error_kwh: 0,
    end_uses: {
      household_kwh: 0.4,
      cooking_kwh: 0.1,
      ev_kwh: 0.0,
      heat_pump_heating_kwh: 0.3,
      heat_pump_hot_water_kwh: 0.15,
      heat_pump_auxiliary_kwh: 0.05
    },
    heat_pump: {
      space_compressor_electricity_kwh: 0.3,
      dhw_compressor_electricity_kwh: 0.15,
      space_backup_electricity_kwh: 0.0,
      dhw_backup_electricity_kwh: 0.0,
      auxiliary_electricity_kwh: 0.05,
      requested_space_heat_kwh: 1.2,
      delivered_space_heat_kwh: 1.2,
      unmet_space_heat_kwh: 0.0,
      requested_draw_heat_kwh: 0.5,
      delivered_draw_heat_kwh: 0.5,
      unmet_draw_heat_kwh: 0.0,
      actual_draw_heat_kwh: 0.5,
      tank_losses_kwh: 0.02,
      tank_temp_c: 50.0,
      space_flow_temp_c: 40.0,
      dhw_flow_temp_c: 55.0,
      space_capacity_kw: 7.0,
      dhw_capacity_kw: 6.5,
      radiator_bound_kw: 9.0,
      space_runtime_hours: 0.7,
      dhw_runtime_hours: 0.2,
      tank_start_storage_kwh: 11.0,
      tank_end_storage_kwh: 11.0,
      map_outside_flag: false,
      hygiene_target_miss: false
    }
  }));

  const dayData = {
    period: 'day',
    date: '2027-01-15',
    scenario: 'electrified',
    weather_year: 2015,
    timezone: 'UTC+01:00',
    available_hours: Array.from({ length: 24 }, (_, i) => i),
    hours,
    totals: { pv_kwh: 2.5, load_kwh: 24.0, direct_kwh: 1.0, charge_input_kwh: 0, discharge_delivered_kwh: 0, grid_import_kwh: 23.0, grid_export_kwh: 1.5 },
    battery_capacity_kwh: 0,
    soc_start_kwh: 0,
    soc_end_kwh: 0,
    max_balance_error_kwh: 0,
    selected_hour: hours[12]
  };

  context.settings = { planning_heat_pump: true, load_scenario: 'electrified' };
  context.current = { result: resultsFixture };

  // Render Daily View
  context.renderDayhour(dayData);
  const hpSection = getElem('dayhourHpSection');
  assert.equal(hpSection.hidden, false, 'dayhourHpSection is visible');
  assert.match(getElem('dayhourHpHourCards').innerHTML, /Space heating electricity/, 'space heating card rendered');
  assert.match(getElem('dayhourHpHourCards').innerHTML, /DHW heating electricity/, 'DHW heating card rendered');
  assert.match(getElem('dayhourHpHourCards').innerHTML, /Backup resistance electricity/, 'backup electricity card rendered');
  assert.match(getElem('dayhourHpHourCards').innerHTML, /Auxiliary electricity/, 'aux electricity card rendered');
  assert.match(getElem('dayhourHpHourCards').innerHTML, /Delivered useful heat/, 'delivered useful heat card rendered');
  assert.match(getElem('dayhourHpTable').innerHTML, /Selected day total \(kWh\)/, 'daily total row labeled kWh');
  assert.match(getElem('dayhourHpHourCards').innerHTML, /available output 7\.0 kW/, 'space_capacity_kw labeled available output in hourly card');
  assert.equal(getElem('dayhourHpTitle').textContent, 'Hourly heat pump dispatch · heat pump use and temperatures');

  // Verify core battery and solar flow titles remain intact (no regressions)
  assert.equal(getElem('solarFlowTitle').textContent, 'PV generation and household use · kWh per hour');
  assert.equal(getElem('gridBatteryTitle').textContent, 'Battery and grid flows · kWh per hour');
  assert.equal(getElem('socTitle').textContent, 'Battery state of charge · kWh stored at end of hour');

  // Render Monthly View
  const monthlyData = {
    ...dayData,
    period: 'month',
    month: 1,
    month_name: 'January',
    year: 2027,
    days_in_month: 31,
    total_samples: 744,
    hp_totals: {
      space_compressor_electricity_kwh: 223.2,
      dhw_compressor_electricity_kwh: 111.6,
      space_backup_electricity_kwh: 0.0,
      dhw_backup_electricity_kwh: 0.0,
      auxiliary_electricity_kwh: 37.2,
      delivered_space_heat_kwh: 892.8,
      delivered_draw_heat_kwh: 372.0,
      unmet_space_heat_kwh: 0.0,
      unmet_draw_heat_kwh: 0.0
    }
  };

  context.renderDayhour(monthlyData);
  assert.match(getElem('dayhourHpTable').innerHTML, /Space comp · kWh\/h \(mean\)/, 'monthly table header clearly indicates kWh/h (mean)');
  assert.match(getElem('dayhourHpTable').innerHTML, /Month total \(kWh\/month\)/, 'monthly table total row clearly indicates kWh/month');
  assert.match(getElem('dayhourHpTable').innerHTML, /223\.2/, 'renders monthly total space compressor kWh');
  console.log('✔ Test 9: Selected hour cards and monthly 24-row table without battery regressions passed');
}

// Test 10: Legacy gas disconnect control visible when HP is off
{
  const { context } = createTestHarness();
  context.settings.planning_heat_pump = false;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'gas_disconnect_cost' }), true, 'gas_disconnect_cost visible when HP off');
  context.settings.planning_heat_pump = true;
  context.settings.hp_disconnect_gas = false;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'gas_disconnect_cost' }), false, 'hidden when HP on and gas not disconnected');
  context.settings.hp_disconnect_gas = true;
  assert.equal(context.isRecordConditionallyAvailable({ name: 'gas_disconnect_cost' }), true, 'visible when HP on and gas disconnected');
  assert.equal(context.schema.defaults.gas_disconnect_cost, 150, 'legacy gas disconnect default preserved');
  console.log('✔ Test 10: Legacy gas disconnect control visible when HP is off passed');
}

// Test 11: Text escaping & exact primary links
{
  const { context, getElem } = createTestHarness();
  const res = JSON.parse(JSON.stringify(resultsFixture));
  context.current = { result: res };
  context.renderHeatPumpReport(res);

  const panelHtml = getElem('heatPumpReportPanel').innerHTML;
  assert.match(panelHtml, /https:\/\/www\.vaillant\.com\/en\/products\/arothermplus\//, 'exact Vaillant link present');
  assert.match(panelHtml, /https:\/\/www\.stelrad\.ie\/wp-content\/uploads\/2021\/10\/Stelrad-Correction-Factor-Oct-21\.pdf/, 'exact Stelrad link present');

  // Verify HTML escaping of malicious equipment model
  res.heat_pump.metadata.equipment_model = '<script>alert("xss")</script>';
  context.renderHeatPumpReport(res);
  assert.match(getElem('heatPumpReportPanel').innerHTML, /&lt;script&gt;alert\(&quot;xss&quot;\)&lt;\/script&gt;/, 'equipment model safely escaped');
  assert.doesNotMatch(getElem('heatPumpReportPanel').innerHTML, /<script>alert/, 'raw script tag not injected');
  console.log('✔ Test 11: Text escaping & exact primary links passed');
}

// Test 12: Financial UI waterfall, displayCashRows, cashTable & onePage summary
{
  const { context, getElem } = createTestHarness();

  // Test displayCashRows unpacking
  const rawRows = [{
    year: 1,
    pv_incremental_range: { min: 100, median: 150, max: 200 },
    display_pv_incremental_range: { min: 50, median: 75, max: 100 },
    whole_package_range: { min: 80, median: 120, max: 160 },
    display_whole_package_range: { min: 40, median: 60, max: 80 },
    replacement: 0,
    display_replacement: 0,
    hp_maintenance: 100,
    display_hp_maintenance: 90,
    hp_replacement: 5000,
    display_hp_replacement: 4500,
    unserved_service_allowance: 50,
    display_unserved_service_allowance: 45,
    avoided_gas_credit: 800,
    display_avoided_gas_credit: 720
  }];

  const displayRows = context.displayCashRows(rawRows);
  assert.equal(displayRows[0].hp_maintenance, 90, 'hp_maintenance unpacked from display_*');
  assert.equal(displayRows[0].hp_replacement, 4500, 'hp_replacement unpacked from display_*');
  assert.equal(displayRows[0].unserved_service_allowance, 45, 'unserved_service_allowance unpacked from display_*');
  assert.equal(displayRows[0].avoided_gas_credit, 720, 'avoided_gas_credit unpacked from display_*');

  // Test cashTable rendering with active HP
  context.settings.planning_heat_pump = true;
  const res = JSON.parse(JSON.stringify(resultsFixture));
  res.annual_cashflows = rawRows;
  res.energy = { load_kwh: 3000, pv_kwh: 6000, direct_kwh: 2000, discharge_kwh: 1500, grid_import_kwh: 1000, export_kwh: 2500, battery_loss_kwh: 100, clipped_kwh: 50, self_consumption: 0.6, useful_self_consumption: 0.6 };
  res.metrics = { npv_eur: { min: 1000, max: 2000 }, payback_years: 6, discounted_payback_years: 8, autarky: 0.7, irr: 0.12, lcoe_eur_kwh: 0.08, package_npv_eur: { min: 1000, max: 2000 }, equity_npv_eur: { min: 1000, max: 2000 } };
  res.finance.whole_package = { simple_payback: 8.5, discounted_payback: 10.2 };
  res.limitations = [];
  res.assumptions = [];
  context.settings = Object.assign({}, res.settings);
  context.current = { result: res, claims: [], warnings: [] };
  context.render();

  const cashTableHtml = getElem('cashTable').innerHTML;
  // Scope contract: Whole-house scope incorporates HP maintenance & replacements into whole-house CF without forcing HP costs into solar incremental project comparison
  assert.match(cashTableHtml, /Whole house/, 'Whole house group header present in cashTable');
  assert.match(cashTableHtml, /Whole-house<br>CF|Whole-house CF/, 'Whole-house cash flow column present in cashTable');
  assert.match(cashTableHtml, /Replacements/, 'Replacements column present in cashTable');

  // Exact numeric assertions inside Year 1 row/columns:
  // 1. Year 1 sticky column
  assert.match(cashTableHtml, /<td class="sticky-col">1<\/td>/, 'Year 1 sticky column rendered');
  // 2. Solar project CF displays solar incremental range (€75 median, €50…€100 range) without HP costs forced in
  assert.match(cashTableHtml, /<td><span class="cash-val">€75<\/span><small class="muted cash-minmax">€50 … €100<\/small><\/td>/, 'Solar incremental CF column displays median €75 and €50…€100 range');
  // 3. Whole-house CF displays provided display_whole_package_range (€60 median, €40…€80 range)
  assert.match(cashTableHtml, /<td><span class="cash-val">€60<\/span><small class="muted cash-minmax">€40 … €80<\/small><\/td>/, 'Whole house CF column displays median €60 and €40…€80 range');
  // 4. Exact displayed numeric replacement total (−€4,500) and replacement cause title without double counting
  assert.match(cashTableHtml, /<span class="replacement-highlight" title="Heat pump replacement">−€4,500<\/span>/, 'Replacements column displays exact numeric replacement total −€4,500');

  // Test onePage downloadable HTML
  let downloadedContent = null;
  context.download = (content, filename) => {
    downloadedContent = { content, filename };
  };

  res.finance.service_comparable = false;
  context.onePage();
  assert.ok(downloadedContent, 'onePage downloaded artifact');
  assert.match(downloadedContent.content, /MAJOR UNMET THERMAL DEMAND WARNING/, 'onePage includes unmet thermal demand warning');
  assert.match(downloadedContent.content, /Heat pump provisional technical summary:/, 'onePage includes HP provisional technical summary');
  assert.match(downloadedContent.content, /Gas connection status:/, 'onePage includes gas connection status');
  console.log('✔ Test 12: Financial UI waterfall, displayCashRows, cashTable & onePage summary passed');
}

console.log('\nALL 12 STAGE 6C HEAT PUMP RESULTS & CONTROLS REVIEW TESTS PASSED SUCCESSFULLY!');
