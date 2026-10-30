'use strict';
// Wholly invented equipment and neutral settings. No captured household scenario.
// Net capital = 1000 fixed + 4 kWp*2000 + 5 kWh*500 = 11500 EUR;
// at 10% VAT the gross benchmark is 12650 EUR.
const defaults = require('./fixtures/heatpump_schema_fixture.json').defaults;
const STABLE_QUOTE_SCENARIO_SETTINGS = Object.freeze(Object.assign({}, defaults, {
  south_panels: 10, west_panels: 0, east_panels: 0, dormer_panels: 0,
  flat_panels: 0, secondary_south_panels: 0, panel_watts: 400,
  inverter_kw: 6, battery_modules: 1, battery_module_kwh: 5, battery_kwh: 5,
  battery_mixed_sizes: false, battery_secondary_modules: 0,
  pv_fixed_cost: 1000, pv_cost_kwp: 2000, battery_cost_kwh: 500,
  quote_vat_included: false, installation_vat: .1, quote_total: null,
  planning_heat_pump: false, load_scenario: 'electrified', horizon_years: 20,
}));
function createStableQuoteScenarioFixture(overrides = {}) {
  return Object.assign({}, STABLE_QUOTE_SCENARIO_SETTINGS, overrides);
}
module.exports = { STABLE_QUOTE_SCENARIO_SETTINGS, createStableQuoteScenarioFixture };
