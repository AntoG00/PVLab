"""Editable assumptions and their provenance; no inferred approval of scenarios."""
from copy import deepcopy
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import threading

DATE = '2026-10-06'
USER_DEFAULTS_PATH = Path(os.environ.get('PV_LEGACY_DATA_ROOT', Path(__file__).resolve().parent / 'data')) / 'user_defaults.json'
_DEFAULTS_LOCK = threading.RLock()
from feasibility.schedule import (
    DEFAULT_AC_HEATING_WINDOWS,
    SCHEDULE_SETTINGS,
    migrate_and_resolve_schedule,
    LEGACY_HEATING_KEYS,
)
GOOGLE = 'https://developers.google.com/maps/documentation/solar/concepts'
PVGIS = 'https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/pvgis-5-user-manual_en'
BYD = 'https://bydbatterybox.com/uploads/downloads/BBOX_HVS_Datasheet_EN_V1.0_240626_L-668f92b10b0a6.pdf'
TESLA_POWERWALL3_IT = 'https://energylibrary.tesla.com/docs/Public/EnergyStorage/Powerwall/3/InstallManual/BackupGateway/2/it-it/GUID-77BFC6C0-346E-4838-8578-26A5E60C8D83.html'

# name: value, unit, accepted range, group, description. Ranges are modeling bounds,
# not probability distributions or assurances of physical feasibility.
SPEC = {
 'installation_year': (2026,'year',[2026,2035],'tax','Neutral example for installation_year; supply profile-specific inputs and verify applicability.'),
 'principal_residence_confirmed': (False,'boolean',None,'tax','Neutral example for principal_residence_confirmed; supply profile-specific inputs and verify applicability.'),
 'south_panels': (10,'panels',[0,None],'layout','Neutral example for south_panels; supply profile-specific inputs and verify applicability.'),
 'west_panels': (0,'panels',[0,None],'layout','Neutral example for west_panels; supply profile-specific inputs and verify applicability.'),
 'east_panels': (0,'panels',[0,None],'layout','Neutral example for east_panels; supply profile-specific inputs and verify applicability.'),
 'dormer_panels': (0,'panels',[0,None],'layout','Neutral example for dormer_panels; supply profile-specific inputs and verify applicability.'),
 'flat_panels': (0,'panels',[0,None],'layout','Neutral example for flat_panels; supply profile-specific inputs and verify applicability.'),
 'secondary_south_panels': (0,'panels',[0,None],'layout','Neutral example for secondary_south_panels; supply profile-specific inputs and verify applicability.'),
 'panel_watts': (400,'W',[1,None],'layout','Module power used to scale production; physical module fit requires a verified layout.'),
 'inverter_kw': (4,'kW',[3,20],'equipment','PV inverter AC rating; DC coupling shares it with battery discharge, AC coupling uses a separate battery inverter. Modeling bounds are 3–20 kW, not a product rating or equipment selection; PV battery topology is selected separately.'),
 'pv_battery_coupling': ('ac_equivalent','topology',['ac_equivalent','dc_coupled'],'equipment','AC coupling caps PV before battery charging and physical mode uses a separate battery inverter. DC coupling charges before the hybrid inverter and shares its AC output across PV, discharge and export. Legacy mode retains the earlier AC-equivalent approximation.'),
 'pv_dispatch_model': ('legacy','choice',['legacy','physical'],'equipment','System designer uses physical bus routing; legacy preserves earlier scenario dispatch.'),
 'routing_priority': ('auto','enum',['auto','battery_first','grid_first','scheduled_hybrid','dynamic_reserve'],'equipment','EMS work mode for the physical dispatch kernel. Auto evaluates battery-first, grid-first, scheduled AM/PM and dynamic clipping reserve for the active system and picks the highest median equity NPV mode; the sidebar stays Auto until a recommendation is applied. Battery-first is the standard self-use inverter mode (Load -> Battery -> Grid). Grid-first exports sub-inverter surplus and only charges from over-inverter DC power (Load -> Clipping Recapture -> Grid -> Battery). Scheduled AM/PM uses grid-first before the switch hour during the configured active months to preserve midday clipping headroom. Dynamic clipping reserve caps morning sub-inverter charging at SoC ceiling = usable capacity minus remaining over-inverter capturable energy.'),
 'routing_switch_hour': (11,'hour',[9,15],'equipment','Local solar/clock hour when Scheduled AM/PM mode switches from Feed-in Priority (Grid-First) to Self-Use (Battery-First) during high-irradiance months (Mar-Sep by default).'),
 'scheduled_start_month': (3,'month',[1,12],'equipment','Scheduled AM/PM start month (inclusive, fixed UTC+1). A start later than end wraps across December.'),
 'scheduled_end_month': (9,'month',[1,12],'equipment','Scheduled AM/PM end month (inclusive). Outside this span dispatch uses Self-Use 24/7.'),
 'inverter_efficiency': (.96,'fraction',[.5,1],'equipment','DC to AC conversion efficiency. DC availability is reconstructed from AC-equivalent PVGIS by dividing by this value.'),
 'ilr_ac_max': (1.35,'ratio',[1,3],'equipment','Maximum PV DC nameplate / PV inverter AC rating for AC coupling.'),
 'ilr_dc_max': (1.70,'ratio',[1,3],'equipment','Maximum PV DC nameplate / shared hybrid AC rating for DC coupling.'),
 'clipping_ac_max': (.03,'fraction',[0,1],'equipment','Maximum annual DC clipping fraction in every simulated weather and project year for AC coupling.'),
 'clipping_dc_max': (.04,'fraction',[0,1],'equipment','Maximum annual residual DC clipping fraction in every simulated weather and project year for DC coupling.'),
 'battery_inverter_kw': (3.,'kW',[0,20],'equipment','Dedicated battery inverter AC rating, used only for AC coupling; zero for DC candidates.'),
 'battery_c_rate_max': (.5,'1/hour',[.01,5],'equipment','Maximum DC terminal charge/discharge power divided by nominal battery energy.'),
 'battery_soc_min': (0.,'fraction',[0,.99],'equipment','Minimum nominal SoC. Existing battery_kwh remains usable energy; nominal energy = usable / (SoC max - SoC min).'),
 'battery_soc_max': (1.,'fraction',[.01,1],'equipment','Maximum nominal SoC; must exceed minimum SoC.'),
 'battery_charge_efficiency': (None,'fraction',[.5,1],'equipment','Battery DC charging efficiency; blank derives sqrt(roundtrip_efficiency).'),
 'battery_discharge_efficiency': (None,'fraction',[.5,1],'equipment','Battery DC discharging efficiency; blank derives sqrt(roundtrip_efficiency).'),
 'pv_max_kwp': (None,'kWp',[0,None],'equipment','Maximum connected PV array nameplate capacity used by system sizing. Separate from inverter AC capacity; blank means use the inverter AC capacity.'),
 'battery_kwh': (5,'usable kWh',[0,None],'equipment','Total usable storage; canonical value retained for existing scenarios.'),
 'battery_modules': (1,'modules',[0,None],'equipment','Count of identical battery modules; total usable capacity is module count times module capacity.'),
 'battery_module_kwh': (5,'usable kWh/module',[0,None],'equipment','Usable capacity of each battery module.'),
 'battery_mixed_sizes': (False,'boolean',None,'equipment','Derive battery capacity from two module sizes; battery_modules remains total count.'),
 'battery_secondary_module_kwh': (6.9,'usable kWh/module',[0,None],'equipment','Usable capacity of secondary battery modules when mixed sizes are enabled.'),
 'battery_secondary_modules': (0,'modules',[0,None],'equipment','Count of second-size modules included in total battery_modules when mixed sizes are enabled.'),
 'charge_kw': (3,'kW',[0,10],'equipment','Battery charge-input power limit (DC terminals in physical designer); not a manufacturer-verified combined PV/battery rating.'),
 'discharge_kw': (3,'kW',[0,20],'equipment','Legacy: battery discharge delivered to load. Physical designer: DC terminal discharge limit, additionally constrained by C-rate and the applicable inverter. Modeling bounds are 0–20 kW, not a product rating or equipment selection.'),
 'roundtrip_efficiency': (.90,'fraction',[.65,1],'equipment','Legacy AC-equivalent round trip. Physical dispatch uses its square root for each DC battery efficiency unless explicitly overridden; inverter losses are separate.'),
 'shade_strength': (1,'fraction',[0,1],'energy','Apply local beam shadow once to PVGIS production.'),
 'diffuse_suppression': (0,'fraction',[0,1],'energy','Sensitivity for diffuse blocked light; sky view and strings unmeasured.'),
 'pv_first_year_loss': (.01,'fraction',[0,.10],'aging','AIKO Neostar 3S+54 first-year warranty loss: 1% of nameplate power; existing PVGIS 14% remains intact.'),
 'pv_annual_fade': (.0035,'fraction/year',[0,.03],'aging','AIKO Neostar 3S+54 linear warranty loss after year one: 0.35% of nameplate power per year, years 2–30.'),
 'battery_calendar_fade': (.015,'fraction/year',[0,.08],'aging','Forecast assumption, not a warranty-derived decay curve.'),
 'battery_cycle_fade': (.00005,'fraction/EFC',[0,.0003],'aging','Multiplicative usable-capacity reduction per equivalent full cycle.'),
 'battery_replace_year': (12,'year',[0,30],'aging','Zero disables replacement; cost at end of specified year; new battery operates from the following year.'),
 'inverter_replace_year': (13,'year',[0,30],'aging','Zero disables replacement.'),
 'horizon_years': (20,'years',[1,30],'finance','Year-end cash flows.'),
 'real_discount_rate': (.03,'fraction/year',[0,.20],'finance','Nominal rate=(1+real rate)*(1+inflation)-1.'),
 'inflation': (.02,'fraction/year',[0,.15],'finance','Applied consistently to nominal costs and real component price paths.'),
 'energy_real_escalation': (0,'fraction/year',[-.10,.10],'prices','Retail commodity and export market move together.'),
 'regulated_real_escalation': (0,'fraction/year',[-.05,.05],'prices','Electric network/tax/fixed-cost scenario.'),
 'gas_real_escalation': (0,'fraction/year',[-.10,.10],'prices','Gas commodity/regulated variable path scenario.'),
 'retail_commodity': (0.12,'EUR/kWh',[0,.50],'bill','Neutral example for retail_commodity; supply profile-specific inputs and verify applicability.'),
 'retail_regulated': (0.05,'EUR/kWh',[0,.50],'bill','Neutral example for retail_regulated; supply profile-specific inputs and verify applicability.'),
 'electric_excise': (.0227,'EUR/kWh',[0,.05],'bill',"Accisa: national domestic electricity consumption tax, normally 0.0227 EUR per imported kWh, separate from network/system charges and subject to VAT. Italian bill: summary 'Accise e IVA', detailed bill 'IMPOSTE' > 'accisa'. Flat-rate model is appropriate above 3 kW; resident supplies up to 3 kW have consumption-dependent exemptions not modeled here."),
 'electric_vat': (.10,'fraction',[0,.22],'bill',"Electricity VAT scenario. Italian bill: summary 'Accise e IVA', detailed 'TOTALE IVA'."),
 'supplier_fixed_month': (8.0,'EUR/month',[0,30],'bill','Neutral example for supplier_fixed_month; supply profile-specific inputs and verify applicability.'),
 'network_fixed_month': (2.0,'EUR/month',[0,20],'bill','Neutral example for network_fixed_month; supply profile-specific inputs and verify applicability.'),
 'power_fixed_kw_month': (2.0,'EUR/kW/month',[0,5],'bill','Neutral example for power_fixed_kw_month; supply profile-specific inputs and verify applicability.'),
 'baseline_power_kw': (3.0,'kW',[3,10],'bill','Neutral example for baseline_power_kw; supply profile-specific inputs and verify applicability.'),
 'electrified_power_kw': (6,'kW',[3,15],'bill',"Separate future contracted-power scenario (same bill field as 'Quota potenza')."),
 'export_market_price': (0.1,'EUR/kWh',[0,.30],'export','Neutral example for export_market_price; supply profile-specific inputs and verify applicability.'),
 'export_capture_factor': (1.0,'fraction',[.3,1.2],'export','Neutral example for export_capture_factor; supply profile-specific inputs and verify applicability.'),
 'actual_price_future_factor': (1,'multiplier',[.3,2],'export','Future export-price factor to reach. It starts at 1.0 in the first modeled year, moves linearly to this target over the selected ramp duration, then stays at the target. For example, 0.75 means export prices are 25% lower once the ramp finishes; a value above 1.0 increases them. Inflation and energy escalation are applied separately. This changes export revenue only, not import prices or retail self-consumption.'),
 'export_price_future_years': (10,'years',[1,30],'export','Number of years for the future export-price factor to reach its target. The factor starts at 1.0 in modeled year 1, moves linearly to the target, and holds there after the ramp; a 10-year duration reaches the target in year 11. Inflation and energy escalation are applied separately. The ramp changes export revenue only, not import prices or retail self-consumption.'),
 'export_price_profile': ('flat_benchmark','choice',['empirical_csud','flat_benchmark'],'export','Neutral example for export_price_profile; supply profile-specific inputs and verify applicability.'),
 'export_price_window': ('oct2025_sep2026','choice',['oct2025_sep2026','calendar_2025'],'export','Empirical GME market data window: default Oct 2025 - Sep 2026 (recent 12 complete months) vs Calendar 2025.'),
 'gse_fee_eur_kw_year': (.70,'EUR/kW/year',[0,5],'export','Neutral example for gse_fee_eur_kw_year; supply profile-specific inputs and verify applicability.'),
 'gse_fee_vat': (.22,'fraction',[0,.22],'export','Neutral example for gse_fee_vat; supply profile-specific inputs and verify applicability.'),
 'pmg_election': (True,'boolean',None,'export','Annual true-up max(hourly PZO proceeds, PMG*export); eligibility pending.'),
 'pmg_price': (.0475,'EUR/kWh',[0,.15],'export','PMG input to verify for installation year.'),
 'pmg_nominal_escalation': (0,'fraction/year',[-.05,.10],'export','Nominal PMG scenario separate from wholesale path.'),
 'export_irpef': (.33,'fraction',[0,.50],'tax','User scenario; marginal rate/tax treatment still pending.'),
 'regional_additional_tax': (0.0,'fraction',[0,.05],'tax','Neutral example for regional_additional_tax; supply profile-specific inputs and verify applicability.'),
 'municipal_additional_tax': (0.0,'fraction',[0,.015],'tax','Neutral example for municipal_additional_tax; supply profile-specific inputs and verify applicability.'),
 'export_fee_tax_deductible': (False,'boolean',None,'tax','Neutral example for export_fee_tax_deductible; supply profile-specific inputs and verify applicability.'),
 'deduction_rate': (0.0,'fraction',[0,.50],'tax','Neutral example for deduction_rate; supply profile-specific inputs and verify applicability.'),
 'deduction_tax_capacity': (0.0,'EUR/year',[0,100000],'tax','Neutral example for deduction_tax_capacity; supply profile-specific inputs and verify applicability.'),
 'deduction_start_year': (1,'year',[1,10],'tax','Ten equal nominal annual installments.'),
 'deduction_eligible_fraction': (1,'fraction',[0,1],'tax','Solar capex eligibility; conversion capex excluded from this deduction model.'),
 'installation_vat': (.10,'fraction',[0,.22],'cost','Added only when quote_vat_included is false.'),
 'pv_fixed_cost': (1000.0,'EUR',[0,15000],'cost','Neutral example for pv_fixed_cost; supply profile-specific inputs and verify applicability.'),
 'pv_cost_kwp': (1000.0,'EUR/kWp',[0,5000],'cost','Neutral example for pv_cost_kwp; supply profile-specific inputs and verify applicability.'),
 'battery_cost_kwh': (500.0,'EUR/usable kWh',[0,2000],'cost','Neutral example for battery_cost_kwh; supply profile-specific inputs and verify applicability.'),
 'deduction_price_premium': (0,'fraction',[0,.50],'cost','Proposed supplier premium when deduction positive; applies to estimated cost only, never actual quote.'),
 'quote_total': (None,'EUR',[0,150000],'cost','Optional solar+storage total override; conversion excluded.'),
 'quote_vat_included': (True,'boolean',None,'cost','Quote scope must be checked against itemized installer prices.'),
 'south_extra_cost_per_panel': (0.0,'EUR/panel',[0,2000],'cost','Optional extra mounting or layout installation cost per panel on south roof.'),
 'west_extra_cost_per_panel': (0.0,'EUR/panel',[0,2000],'cost','Optional extra mounting or layout installation cost per panel on west roof.'),
 'east_extra_cost_per_panel': (0.0,'EUR/panel',[0,2000],'cost','Optional extra mounting or layout installation cost per panel on east roof.'),
 'dormer_extra_cost_per_panel': (0.0,'EUR/panel',[0,2000],'cost','Optional extra mounting or layout installation cost per panel on dormer roof.'),
 'flat_extra_cost_per_panel': (0.0,'EUR/panel',[0,2000],'cost','Optional extra mounting or ballast installation cost per panel on flat roof.'),
 'secondary_south_extra_cost_per_panel': (0.0,'EUR/panel',[0,2000],'cost','Optional extra mounting or layout installation cost per panel on secondary south roof.'),
 'battery_module_cost_override': (None,'EUR/module',[0,20000],'cost','Optional explicit unit purchase cost for primary battery modules; replaces battery_module_kwh * battery_cost_kwh when set.'),
 'battery_secondary_module_cost_override': (None,'EUR/module',[0,20000],'cost','Optional explicit unit purchase cost for secondary battery modules; replaces battery_secondary_module_kwh * battery_cost_kwh when set.'),
 'battery_module_extra_cost': (0.0,'EUR/module',[0,5000],'cost','Optional extra installation or hardware cost per primary battery module.'),
 'battery_secondary_module_extra_cost': (0.0,'EUR/module',[0,5000],'cost','Optional extra installation or hardware cost per secondary battery module.'),
 'om_annual': (100,'EUR/year',[0,1000],'cost','Operations/maintenance scenario.'),
 'insurance_annual': (0,'EUR/year',[0,500],'cost','Incremental PV insurance scenario.'),
 'cleaning_annual': (0,'EUR/year',[0,500],'cost','Incremental cleaning scenario.'),
 'inverter_replacement_cost': (1200,'EUR',[0,10000],'cost','PLACEHOLDER installed replacement cost in current euros.'),
 'battery_replacement_cost_kwh': (250,'EUR/kWh',[0,2000],'cost','PLACEHOLDER replacement cost in current euros.'),
 'residual_fraction': (0,'fraction',[0,.5],'finance','Terminal solar asset value relative to initial capex, current euros.'),
 'loan_fraction': (0,'fraction',[0,1],'finance','Financed solar capex share; unlevered and equity metrics separated.'),
 'loan_mode': ('fraction','mode',['fraction','amount'],'finance','Choose a loan share or an absolute loan principal amount; the other input is inactive.'),
 'loan_amount': (10000.0,'EUR',[0,200000],'finance','Neutral example for loan_amount; supply profile-specific inputs and verify applicability.'),
 'loan_upfront_fees_or_tax': (0.0,'EUR',[0,100000],'finance','Neutral example for loan_upfront_fees_or_tax; supply profile-specific inputs and verify applicability.'),
 'loan_max_monthly_payment': (None,'EUR/month',[0,100000],'finance','Optional maximum scheduled monthly loan instalment for the loan amount search; blank means no limit.'),
 'loan_include_electrification': (False,'boolean',None,'finance','Include electrification equipment and gas disconnection capex in the loan and financed package cash flows. Electrification remains excluded from the modeled tax deduction basis.'),
 'loan_apr': (0.05,'fraction/year',[0,.30],'finance','Neutral example for loan_apr; supply profile-specific inputs and verify applicability.'),
 'loan_years': (10,'years',[1,30],'finance','Debt service continues to maturity; remaining debt settles at horizon.'),
 'load_scenario': ('baseline','scenario',['baseline','electrified','ev'],'loads','EV scenario includes electrification plus EV home charging.'),
 'load_growth': (1,'multiplier',[.5,2],'loads','Measured electrical-load sensitivity.'),
 'summer_ac_multiplier': (1.0,'multiplier',[1.0,3.0],'loads','Summer AC electricity multiplier (default 1.0 = unchanged, e.g. 1.3 = +30% cooling). Baseline cooling is estimated from measured household demand using August mean daily kWh minus October mean daily kWh across June–September (note: this seasonal difference proxy is an estimate that may include non-AC seasonal variations). Incremental electricity (multiplier - 1) × baseline AC is distributed across cooling degree hours above 24°C.'),
 'timezone_mode': ('civil','mapping',['civil','standard'],'loads','Naive 96-interval export; both mappings conserve measured monthly energy.'),
 'summer_gas_base': (20.0,'Smc/month',[0,None],'loads','Neutral example for summer_gas_base; supply profile-specific inputs and verify applicability.'),
 'cooking_smc_annual': (20.0,'Smc/year',[15,40],'loads','Neutral example for cooking_smc_annual; supply profile-specific inputs and verify applicability.'),
 'gas_pcs': (11.0,'kWh/Smc',[10,12],'loads','Neutral example for gas_pcs; supply profile-specific inputs and verify applicability.'),
 'boiler_efficiency': (0.8,'fraction',[.65,.9],'loads','Neutral example for boiler_efficiency; supply profile-specific inputs and verify applicability.'),
 'gas_cooking_efficiency': (.45,'fraction',[.3,.6],'loads','Useful gas cooking heat.'),
 'induction_efficiency': (.85,'fraction',[.75,.95],'loads','Cooking electricity.'),
 'dhw_resistance_efficiency': (.98,'fraction',[.9,1],'loads','DHW modeled as electrical resistance, not a heat pump.'),
 'dhw_heating_type': ('resistance','choice',['resistance','heat_pump'],'loads','DHW equipment assumption; select resistance efficiency or heat-pump SCOP.'),
 'dhw_scop': (3.0,'kWh heat/kWh electricity',[1,8],'loads','Seasonal efficiency of a dedicated DHW heat pump.'),
 'scop': (4.0,'kWh heat/kWh electricity',[1,8],'loads','Neutral example for scop; supply profile-specific inputs and verify applicability.'),
 'dhw_midday_fraction': (.5,'fraction',[0,1],'loads','Fixed shiftable DHW fraction scheduled 11-15 local standard hours.'),
 'heating_balance_temp': (18,'C',[12,22],'loads','Outdoor degree-hour balance-temperature threshold; distinct from the indoor comfort setpoint.'),
 'heating_schedule_mode': ('scheduled','choice',['scheduled','continuous'],'loads','Space-heating schedule mode: "scheduled" restricts space-heating heat pump operation to permitted daily clock windows (morning and afternoon/evening); "continuous" restores unconstrained 24h temperature-weighted operation. Both conserve the exact monthly heating-electricity budget.'),
 'ac_heating_windows': (deepcopy(DEFAULT_AC_HEATING_WINDOWS),'windows',None,'loads','Space-heating operating windows (start-inclusive, end-exclusive local clock minutes from 0..1440). Empty schedule allows draft UI configuration.'),
 'ac_heating_additional_demand_pct': (0,'%',[0,None],'loads','Additional useful space-heating demand for AC heat pump operation as a percentage of historical gas heating displacement. Sensitivity at fixed SCOP and operating hours; does not model indoor temperature, comfort, or equipment adequacy.'),
 'ev_km_annual': (7500,'km/year',[0,40000],'loads','EV mobility benefit and vehicle purchase excluded from PV economics.'),
 'ev_kwh_100km': (15,'kWh/100km',[10,25],'loads','Vehicle battery energy consumption.'),
 'ev_charge_efficiency': (.9,'fraction',[.7,1],'loads','Wall-to-vehicle efficiency.'),
 'ev_home_fraction': (1,'fraction',[0,1],'loads','Fraction of charging supplied at home.'),
 'ev_day_fraction': (.2,'fraction',[0,1],'loads','Home charging at 11-16 vs 18-23; availability unverified.'),
 'ev_start_year': (1,'year',[1,30],'loads','EV charging starts in this project year.'),
 'electrification_capex': (6000,'EUR',[0,20000],'loads','PROPOSED conversion equipment/installation cost; no quote.'),
 'electrification_capex_include_baseline': (False,'boolean',None,'loads','Include electrification equipment CAPEX as an equipment cost in baseline scenario; gas disconnection is excluded and gas service is retained.'),
 'gas_disconnect_cost': (150,'EUR',[0,1000],'loads','PROPOSED disconnection cost; no quote.'),
 'gas_commodity': (.5,'EUR/Smc',[0,2],'gas','Pre-tax gas commodity scenario price per Smc.'),
 'gas_regulated': (0.4,'EUR/Smc',[0,2],'gas','Pre-tax network and system charges; an explicit flat approximation.'),
 'gas_fixed_day': (.3,'EUR/day',[0,1],'gas','Pre-tax combined supplier and network fixed charge per day.'),
 'gas_excise_0_120': (.038,'EUR/Smc',[0,1],'gas','National gas excise rate for 0–120 Smc/year (Mezzogiorno/non-industrial).'),
 'gas_excise_120_480': (.135,'EUR/Smc',[0,1],'gas','National gas excise rate for 120–480 Smc/year (Mezzogiorno/non-industrial).'),
 'gas_excise_480_1560': (.120,'EUR/Smc',[0,1],'gas','National gas excise rate for 480–1560 Smc/year (Mezzogiorno/non-industrial).'),
 'gas_excise_over_1560': (.150,'EUR/Smc',[0,1],'gas','National gas excise rate over 1560 Smc/year (Mezzogiorno/non-industrial).'),
 'gas_regional_0_120': (.019,'EUR/Smc',[0,1],'gas','Campania regional gas excise surcharge (ARISGAN) for 0–120 Smc/year.'),
 'gas_regional_over_120': (.031,'EUR/Smc',[0,1],'gas','Campania regional gas excise surcharge (ARISGAN) over 120 Smc/year.'),
 'gas_vat_reduced': (.10,'fraction',[0,1],'gas','Reduced VAT rate for natural gas (applies to first 480 Smc/year variable consumption including excise and surcharge).'),
 'gas_vat_standard': (.22,'fraction',[0,1],'gas','Standard VAT rate for natural gas (applies to variable consumption over 480 Smc/year and all fixed charges).'),
 'cer_enabled': (False,'boolean',None,'cer','Excluded from base case; actual community matching/terms absent.'),
 'cer_eligible_export_share': (0,'fraction',[0,1],'cer','Modeled fraction, not computed hourly community sharing.'),
 'cer_tariff': (.10,'EUR/kWh',[0,.12],'cer','Conditional tariff input: actual plant/market terms require verification.'),
 'cer_user_allocation': (0,'fraction',[0,1],'cer','Community allocation to user; unknown until contract.'),
 'dormer_south_proxy': (False,'boolean',None,'layout','Neutral example for dormer_south_proxy; supply profile-specific inputs and verify applicability.'),
 'cashflow_mode': ('nominal','mode',['nominal','real'],'finance','Real display deflates nominal flows using the same inflation assumption.'),
}

from .heatpump_settings import HP_SPEC, HP_ALIASES
SPEC.update(HP_SPEC)

def _saved_defaults(path=None):
    from .tenant_store import TenantDefaults
    if isinstance(path, TenantDefaults):return path.overrides()
    if path is None:path=USER_DEFAULTS_PATH
    path=Path(path)
    if not path.exists():return {}
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or not isinstance(data.get('overrides',{}),dict):
        raise ValueError('Saved defaults file must contain an overrides object')
    return data.get('overrides',{})

@contextmanager
def _defaults_file_lock(path):
    lock_path=Path(str(path)+'.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with _DEFAULTS_LOCK, lock_path.open('a+') as lock_file:
        fcntl.flock(lock_file.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(lock_file.fileno(),fcntl.LOCK_UN)

def _resolve_saved_schedule(overrides, base_windows=None, base_mode=None):
    """Resolve saved schedule settings and legacy heating overrides against base/factory."""
    if base_windows is None:
        base_windows = deepcopy(SPEC['ac_heating_windows'][0])
    if base_mode is None:
        base_mode = SPEC['heating_schedule_mode'][0]
    sched_overrides = {}
    for k, v in overrides.items():
        if k in SCHEDULE_SETTINGS or k in LEGACY_HEATING_KEYS:
            if isinstance(v, dict):
                if 'value' in v:
                    sched_overrides[k] = v['value']
            else:
                sched_overrides[k] = v
    windows = migrate_and_resolve_schedule(sched_overrides, base_windows=base_windows)
    mode = sched_overrides.get('heating_schedule_mode', base_mode)
    return {'ac_heating_windows': windows, 'heating_schedule_mode': mode}


STRUCTURAL_SETTINGS = frozenset({'roof_panels'})


def thaw_roof_settings(settings):
    """Restore the manifest mapping after canonical settings-key freezing."""
    if isinstance(settings.get('roof_panels'), tuple):
        settings['roof_panels'] = dict(settings['roof_panels'])
    return settings


def defaults(path=None):
    """Simulation snapshot when bound; explicit paths always read storage."""
    from .runtime_context import runtime_if_initialized, mutable_copy
    ctx = runtime_if_initialized()
    if path is None and ctx is not None:
        return mutable_copy(ctx.defaults_snapshot)
    return persisted_defaults(path)


def persisted_defaults(path=None):
    """Read effective stored defaults without changing a running simulation."""
    from .tenant_store import TenantDefaults
    if isinstance(path, TenantDefaults):return path.effective()
    from .runtime_context import runtime_if_initialized, mutable_copy
    ctx = runtime_if_initialized()
    legacy = ctx.metadata.get('presentation') if ctx is not None and ctx.household_profile is None else None
    result=(mutable_copy(legacy['factory_defaults']) if legacy is not None
            else {name:deepcopy(row[0]) for name,row in SPEC.items()})
    overrides=_saved_defaults(path)
    result.update({k:deepcopy(v['value']) for k,v in overrides.items() if k in SPEC and isinstance(v,dict) and 'value' in v})
    _normalize_battery(result, set(overrides))
    if any(k in overrides for k in SCHEDULE_SETTINGS) or any(k in overrides for k in LEGACY_HEATING_KEYS):
        sched = _resolve_saved_schedule(overrides, base_windows=result.get('ac_heating_windows'), base_mode=result.get('heating_schedule_mode'))
        result.update(sched)
    return result

BATTERY_SETTINGS = (
    'battery_kwh',
    'battery_modules',
    'battery_module_kwh',
    'battery_mixed_sizes',
    'battery_secondary_module_kwh',
    'battery_secondary_modules',
)

def _normalize_battery(settings, supplied):
    """Keep battery capacity and module representations in sync."""
    mixed = bool(settings.get('battery_mixed_sizes', False))
    if mixed:
        count = settings['battery_modules']
        sec_count = settings['battery_secondary_modules']
        first_cap = settings['battery_module_kwh']
        sec_cap = settings['battery_secondary_module_kwh']
        if count == 0:
            settings['battery_kwh'] = 0.0
        elif sec_count <= count:
            first_count = count - sec_count
            settings['battery_kwh'] = float(first_count * first_cap + sec_count * sec_cap)
        # Note: if sec_count > count, engine.validate() will reject it.
        # Stale supplied battery_kwh never rewrites physical sizes or synthesizes an average.
    else:
        count = settings['battery_modules']
        capacity = settings['battery_module_kwh']
        total = settings['battery_kwh']
        if 'battery_kwh' in supplied:
            if count == 0 and total > 0:
                count = 1
                settings['battery_modules'] = count
            if count > 0:
                if not math.isclose(total, count * capacity, rel_tol=1e-9, abs_tol=1e-9):
                    settings['battery_module_kwh'] = total / count
        elif {'battery_modules', 'battery_module_kwh', 'battery_mixed_sizes'} & set(supplied):
            settings['battery_kwh'] = count * capacity
    return settings

def calculate_battery_composition(
    settings=None,
    *,
    total_modules=None,
    secondary_modules=None,
    module_kwh=None,
    secondary_module_kwh=None,
    mixed_sizes=None,
):
    """Calculate battery composition and derived usable capacity."""
    if settings is not None:
        if not isinstance(settings, dict):
            raise ValueError('settings must be a dict')
        if mixed_sizes is None:
            mixed_sizes = settings.get('battery_mixed_sizes', False)
        if total_modules is None:
            total_modules = settings.get('battery_modules', 1)
        if secondary_modules is None:
            secondary_modules = settings.get('battery_secondary_modules', 0)
        if module_kwh is None:
            module_kwh = settings.get('battery_module_kwh', 5.0)
        if secondary_module_kwh is None:
            secondary_module_kwh = settings.get('battery_secondary_module_kwh', 6.9)
    else:
        if mixed_sizes is None:
            mixed_sizes = False
        if total_modules is None:
            total_modules = 1
        if secondary_modules is None:
            secondary_modules = 0
        if module_kwh is None:
            module_kwh = 5.0
        if secondary_module_kwh is None:
            secondary_module_kwh = 6.9

    if not isinstance(mixed_sizes, bool):
        raise ValueError('battery_mixed_sizes must be boolean')
    if isinstance(total_modules, bool) or not isinstance(total_modules, (int, float)) or not math.isfinite(total_modules) or int(total_modules) != total_modules or total_modules < 0:
        raise ValueError('battery_modules must be a nonnegative integer')
    if isinstance(secondary_modules, bool) or not isinstance(secondary_modules, (int, float)) or not math.isfinite(secondary_modules) or int(secondary_modules) != secondary_modules or secondary_modules < 0:
        raise ValueError('battery_secondary_modules must be a nonnegative integer')
    if isinstance(module_kwh, bool) or not isinstance(module_kwh, (int, float)) or not math.isfinite(module_kwh) or module_kwh < 0:
        raise ValueError('battery_module_kwh must be a nonnegative finite number')
    if isinstance(secondary_module_kwh, bool) or not isinstance(secondary_module_kwh, (int, float)) or not math.isfinite(secondary_module_kwh) or secondary_module_kwh < 0:
        raise ValueError('battery_secondary_module_kwh must be a nonnegative finite number')

    total_modules = int(total_modules)
    secondary_modules = int(secondary_modules)
    module_kwh = float(module_kwh)
    secondary_module_kwh = float(secondary_module_kwh)

    if not mixed_sizes:
        first_modules = total_modules
        sec_eff = 0
        total_kwh = float(total_modules * module_kwh) if total_modules > 0 else 0.0
        stable_id = f'{total_modules}m'
    else:
        if secondary_modules > total_modules:
            raise ValueError(f'Secondary module count ({secondary_modules}) cannot exceed total modules ({total_modules})')
        first_modules = total_modules - secondary_modules
        sec_eff = secondary_modules
        if total_modules == 0:
            total_kwh = 0.0
        else:
            if first_modules > 0 and module_kwh <= 0:
                raise ValueError('First battery module size must be strictly positive when used')
            if secondary_modules > 0 and secondary_module_kwh <= 0:
                raise ValueError('Secondary battery module size must be strictly positive when used')
            total_kwh = float(first_modules * module_kwh + secondary_modules * secondary_module_kwh)
        stable_id = f'{total_modules}m_{secondary_modules}sec'

    return {
        'id': stable_id,
        'identity': stable_id,
        'battery_modules': total_modules,
        'battery_secondary_modules': sec_eff if mixed_sizes else secondary_modules,
        'first_modules': first_modules,
        'battery_module_kwh': module_kwh,
        'battery_secondary_module_kwh': secondary_module_kwh,
        'battery_mixed_sizes': mixed_sizes,
        'battery_kwh': total_kwh,
        'total_modules': total_modules,
        'secondary_modules': sec_eff if mixed_sizes else secondary_modules,
        'first_count': first_modules,
        'secondary_count': sec_eff if mixed_sizes else secondary_modules,
        'total_kwh': total_kwh,
    }

battery_composition = calculate_battery_composition

def enumerate_battery_combinations(
    min_modules=0,
    max_modules=None,
    *,
    module_kwh=None,
    secondary_module_kwh=None,
    mixed_sizes=None,
    max_combinations=1000,
    settings=None,
):
    """Enumerate valid battery compositions across a range of total module counts."""
    if settings is not None:
        if not isinstance(settings, dict):
            raise ValueError('settings must be a dict')
        if mixed_sizes is None:
            mixed_sizes = settings.get('battery_mixed_sizes', False)
        if module_kwh is None:
            module_kwh = settings.get('battery_module_kwh', 5.0)
        if secondary_module_kwh is None:
            secondary_module_kwh = settings.get('battery_secondary_module_kwh', 6.9)
    else:
        if mixed_sizes is None:
            mixed_sizes = False
        if module_kwh is None:
            module_kwh = 5.0
        if secondary_module_kwh is None:
            secondary_module_kwh = 6.9

    if max_modules is None:
        raise ValueError('max_modules must be specified')
    if isinstance(min_modules, bool) or not isinstance(min_modules, (int, float)) or not math.isfinite(min_modules) or int(min_modules) != min_modules or min_modules < 0:
        raise ValueError('min_modules must be a nonnegative integer')
    if isinstance(max_modules, bool) or not isinstance(max_modules, (int, float)) or not math.isfinite(max_modules) or int(max_modules) != max_modules or max_modules < 0:
        raise ValueError('max_modules must be a nonnegative integer')
    min_modules = int(min_modules)
    max_modules = int(max_modules)
    if max_modules < min_modules:
        raise ValueError(f'max_modules ({max_modules}) cannot be less than min_modules ({min_modules})')

    if not mixed_sizes:
        expected_count = max(0, max_modules - min_modules + 1)
    else:
        expected_count = ((max_modules - min_modules + 1) * (min_modules + max_modules + 2)) // 2

    if max_combinations is not None and expected_count > max_combinations:
        raise ValueError(f'Combination count ({expected_count}) exceeds maximum allowed limit ({max_combinations})')

    results = []
    for m in range(min_modules, max_modules + 1):
        if not mixed_sizes:
            results.append(calculate_battery_composition(
                total_modules=m,
                secondary_modules=0,
                module_kwh=module_kwh,
                secondary_module_kwh=secondary_module_kwh,
                mixed_sizes=False,
            ))
        else:
            for s in range(0, m + 1):
                results.append(calculate_battery_composition(
                    total_modules=m,
                    secondary_modules=s,
                    module_kwh=module_kwh,
                    secondary_module_kwh=secondary_module_kwh,
                    mixed_sizes=True,
                ))
    return results

battery_combinations = enumerate_battery_combinations

def enumerate_battery_compositions_by_capacity(
    min_kwh=0.0,
    max_kwh=None,
    *,
    module_kwh=None,
    secondary_module_kwh=None,
    mixed_sizes=None,
    max_combinations=50,
    settings=None,
):
    """Enumerate valid battery compositions across a usable capacity range [min_kwh..max_kwh]."""
    if settings is not None:
        if not isinstance(settings, dict):
            raise ValueError('settings must be a dict')
        if mixed_sizes is None:
            mixed_sizes = settings.get('battery_mixed_sizes', False)
        if module_kwh is None:
            module_kwh = settings.get('battery_module_kwh', 5.0)
        if secondary_module_kwh is None:
            secondary_module_kwh = settings.get('battery_secondary_module_kwh', 6.9)
    else:
        if mixed_sizes is None:
            mixed_sizes = False
        if module_kwh is None:
            module_kwh = 5.0
        if secondary_module_kwh is None:
            secondary_module_kwh = 6.9

    if max_kwh is None:
        raise ValueError('max_kwh must be specified')
    if isinstance(min_kwh, bool) or not isinstance(min_kwh, (int, float)) or not math.isfinite(min_kwh) or min_kwh < 0:
        raise ValueError('min_kwh must be a nonnegative finite number')
    if isinstance(max_kwh, bool) or not isinstance(max_kwh, (int, float)) or not math.isfinite(max_kwh) or max_kwh < 0:
        raise ValueError('max_kwh must be a nonnegative finite number')
    min_kwh = float(min_kwh)
    max_kwh = float(max_kwh)
    if min_kwh > max_kwh:
        raise ValueError(f'max_kwh ({max_kwh}) cannot be less than min_kwh ({min_kwh})')

    if not isinstance(mixed_sizes, bool):
        raise ValueError('mixed_sizes must be boolean')

    if isinstance(module_kwh, bool) or not isinstance(module_kwh, (int, float)) or not math.isfinite(module_kwh) or module_kwh < 0:
        raise ValueError('battery_module_kwh must be a nonnegative finite number')
    module_kwh = float(module_kwh)

    if isinstance(secondary_module_kwh, bool) or not isinstance(secondary_module_kwh, (int, float)) or not math.isfinite(secondary_module_kwh) or secondary_module_kwh < 0:
        raise ValueError('battery_secondary_module_kwh must be a nonnegative finite number')
    secondary_module_kwh = float(secondary_module_kwh)

    if not mixed_sizes:
        if max_kwh == 0:
            if min_kwh <= 0:
                return [calculate_battery_composition(total_modules=0, secondary_modules=0, module_kwh=module_kwh, mixed_sizes=False)]
            raise ValueError(f"No whole battery module capacities exist between {min_kwh:g} kWh and {max_kwh:g} kWh with module size {module_kwh:g} kWh.")
        if module_kwh <= 0:
            raise ValueError('battery_module_kwh must be positive when searching storage capacities')
        m_start = 1 if min_kwh <= 0 else math.ceil((min_kwh - 1e-9) / module_kwh)
        m_end = math.floor((max_kwh + 1e-9) / module_kwh)
        if min_kwh > 0 and m_start > m_end:
            raise ValueError(f"No whole battery module capacities exist between {min_kwh:g} kWh and {max_kwh:g} kWh with module size {module_kwh:g} kWh.")
        pos_steps = max(0, m_end - m_start + 1) if m_end >= m_start else 0
        if max_combinations is not None and pos_steps > max_combinations:
            raise ValueError(f'battery_module_kwh generates too many candidate capacities (maximum {max_combinations} steps)')
        cand_m = [m for m in range(m_start, m_end + 1) if m > 0]
        if min_kwh <= 0:
            cand_m = [0] + cand_m
        return [calculate_battery_composition(total_modules=m, secondary_modules=0, module_kwh=module_kwh, mixed_sizes=False) for m in cand_m]
    else:
        if max_kwh == 0:
            if min_kwh <= 0:
                return [calculate_battery_composition(total_modules=0, secondary_modules=0, module_kwh=module_kwh, secondary_module_kwh=secondary_module_kwh, mixed_sizes=True)]
            raise ValueError(f"No whole battery module compositions exist between {min_kwh:g} kWh and {max_kwh:g} kWh with module sizes {module_kwh:g} kWh and {secondary_module_kwh:g} kWh.")
        if module_kwh <= 0:
            raise ValueError('battery_module_kwh must be positive for mixed battery capacity search')
        if secondary_module_kwh <= 0:
            raise ValueError('battery_secondary_module_kwh must be positive for mixed battery capacity search')

        max_first = math.floor((max_kwh + 1e-9) / module_kwh)
        max_sec = math.floor((max_kwh + 1e-9) / secondary_module_kwh)
        if max_combinations is not None:
            if max_first > max_combinations or max_sec > max_combinations:
                raise ValueError(f'Mixed battery module sizes generate too many candidate capacities (maximum {max_combinations} steps)')

        pairs = []
        for c2 in range(0, max_sec + 1):
            rem_cap = max_kwh - c2 * secondary_module_kwh
            c1_max = math.floor((rem_cap + 1e-9) / module_kwh)
            for c1 in range(0, c1_max + 1):
                if c1 == 0 and c2 == 0:
                    if min_kwh <= 0:
                        pairs.append((0, 0, 0.0))
                else:
                    kwh = float(c1 * module_kwh + c2 * secondary_module_kwh)
                    if min_kwh - 1e-9 <= kwh <= max_kwh + 1e-9:
                        pairs.append((c1, c2, kwh))

        pos_count = len([p for p in pairs if (p[0] > 0 or p[1] > 0)])
        if max_combinations is not None and pos_count > max_combinations:
            raise ValueError(f'Mixed battery module sizes generate too many candidate capacities (maximum {max_combinations} steps)')

        if min_kwh > 0 and len(pairs) == 0:
            raise ValueError(f"No whole battery module compositions exist between {min_kwh:g} kWh and {max_kwh:g} kWh with module sizes {module_kwh:g} kWh and {secondary_module_kwh:g} kWh.")

        pairs.sort(key=lambda p: (round(p[2], 9), p[0] + p[1], p[1]))
        return [
            calculate_battery_composition(
                total_modules=c1 + c2,
                secondary_modules=c2,
                module_kwh=module_kwh,
                secondary_module_kwh=secondary_module_kwh,
                mixed_sizes=True,
            )
            for c1, c2, _ in pairs
        ]

battery_compositions_by_capacity = enumerate_battery_compositions_by_capacity


def save_user_default(name,value,validator,path=None,battery_settings=None,schedule_settings=None):
    """Validate and atomically persist one shared default, preserving other keys."""
    from .tenant_store import TenantDefaults
    if isinstance(path, TenantDefaults):
        return path.save(name,value,validator,battery_settings=battery_settings,schedule_settings=schedule_settings)
    path=Path(USER_DEFAULTS_PATH if path is None else path)
    with _defaults_file_lock(path):
        existing=_saved_defaults(path)
        # Caller validation sees the currently persisted merged defaults. Run it
        # under the same lock as read/modify/write to prevent cross-key races.
        if name in BATTERY_SETTINGS and battery_settings is not None:
            cand = {}
            for k in BATTERY_SETTINGS:
                if k in battery_settings:
                    cand[k] = battery_settings[k]
                elif k in existing:
                    cand[k] = existing[k]['value']
                else:
                    cand[k] = SPEC[k][0]
            cand[name] = value
            try:
                normalized = validator(cand, base=persisted_defaults(path))
            except TypeError:
                normalized = validator(cand)
        elif name in SCHEDULE_SETTINGS:
            resolved = _resolve_saved_schedule(existing)
            cand_windows = deepcopy(resolved['ac_heating_windows'])
            cand_mode = resolved['heating_schedule_mode']
            if schedule_settings is not None:
                if 'ac_heating_windows' in schedule_settings or any(k in schedule_settings for k in LEGACY_HEATING_KEYS):
                    cand_windows = migrate_and_resolve_schedule(schedule_settings, base_windows=resolved['ac_heating_windows'])
                if 'heating_schedule_mode' in schedule_settings:
                    cand_mode = schedule_settings['heating_schedule_mode']
            if name == 'ac_heating_windows':
                cand_windows = value
            elif name == 'heating_schedule_mode':
                cand_mode = value
            cand = {
                'ac_heating_windows': cand_windows,
                'heating_schedule_mode': cand_mode,
            }
            base = {n: deepcopy(row[0]) for n, row in SPEC.items()}
            base.update({k: deepcopy(v['value']) for k, v in existing.items() if k in SPEC and isinstance(v, dict) and 'value' in v})
            _normalize_battery(base, set(existing))
            base.update(resolved)
            if 'planning_heat_pump' not in existing and name != 'planning_heat_pump':
                base['planning_heat_pump'] = False
            try:
                normalized = validator(cand, base=base, supplied=set(cand))
            except TypeError:
                normalized = validator(cand)
        else:
            base = {n: deepcopy(row[0]) for n, row in SPEC.items()}
            base.update({k: deepcopy(v['value']) for k, v in existing.items() if k in SPEC and isinstance(v, dict) and 'value' in v})
            _normalize_battery(base, set(existing))
            if any(k in existing for k in SCHEDULE_SETTINGS) or any(k in existing for k in LEGACY_HEATING_KEYS):
                sched = _resolve_saved_schedule(existing, base_windows=base.get('ac_heating_windows'), base_mode=base.get('heating_schedule_mode'))
                base.update(sched)
            if 'planning_heat_pump' not in existing and name != 'planning_heat_pump':
                base['planning_heat_pump'] = False
            try:
                normalized = validator({name: value}, base=base, supplied={name})
            except TypeError:
                normalized = validator({name: value})
        names = BATTERY_SETTINGS if name in BATTERY_SETTINGS else (SCHEDULE_SETTINGS if name in SCHEDULE_SETTINGS else (name,))
        for linked_name in names:
            factory = SPEC[linked_name][0]
            if name in SCHEDULE_SETTINGS:
                linked_value = normalized[linked_name] if linked_name in normalized else (value if linked_name == name else cand[linked_name])
                if linked_name in existing and isinstance(existing[linked_name], dict):
                    entry = dict(existing[linked_name])
                    entry['value'] = deepcopy(linked_value)
                    if 'factory_default' not in entry:
                        entry['factory_default'] = deepcopy(factory)
                    if linked_name == name or (schedule_settings and linked_name in schedule_settings):
                        entry['status'] = 'provided'
                        entry['date'] = dt.date.today().isoformat()
                        entry['owner'] = 'user'
                else:
                    entry = {'value': deepcopy(linked_value), 'status': 'provided', 'date': dt.date.today().isoformat(), 'owner': 'user', 'factory_default': deepcopy(factory)}
            else:
                linked_value = normalized[linked_name] if linked_name in normalized else (value if linked_name == name else SPEC[linked_name][0])
                entry = {'value': deepcopy(linked_value), 'status': 'provided', 'date': dt.date.today().isoformat(), 'owner': 'user', 'factory_default': deepcopy(factory)}
            existing[linked_name] = entry
        if name in SCHEDULE_SETTINGS:
            for lk in LEGACY_HEATING_KEYS:
                existing.pop(lk, None)
        payload = json.dumps({'overrides': existing}, indent=2, allow_nan=False) + '\n'
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                out.write(payload); out.flush(); os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return existing[name]


def reset_user_default(name, path=None):
    """Atomically reset a user default override (or schedule group), preserving other overrides."""
    from .tenant_store import TenantDefaults
    if isinstance(path, TenantDefaults):return path.reset(name)
    path = Path(USER_DEFAULTS_PATH if path is None else path)
    with _defaults_file_lock(path):
        existing = _saved_defaults(path)
        if name in SCHEDULE_SETTINGS:
            names = SCHEDULE_SETTINGS
        elif name in BATTERY_SETTINGS:
            names = BATTERY_SETTINGS
        else:
            names = (name,)
        changed = False
        for k in names:
            if k in existing:
                del existing[k]
                changed = True
        if name in SCHEDULE_SETTINGS:
            for lk in LEGACY_HEATING_KEYS:
                if lk in existing:
                    del existing[lk]
                    changed = True
        if changed:
            payload = json.dumps({'overrides': existing}, indent=2, allow_nan=False) + '\n'
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as out:
                    out.write(payload); out.flush(); os.fsync(out.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        return True

def records(path=None):
    from .runtime_context import runtime_if_initialized, mutable_copy
    ctx = runtime_if_initialized()
    if path is None and ctx is not None:
        return mutable_copy(ctx.records_snapshot)
    return persisted_records(path)


def persisted_records(path=None):
    """Neutral model descriptors. Profile evidence is supplied explicitly by its owner."""
    from .tenant_store import TenantDefaults
    if isinstance(path, TenantDefaults):
        return path.records()
    saved = _saved_defaults(path)
    from .runtime_context import runtime_if_initialized, mutable_copy
    ctx = runtime_if_initialized()
    legacy = ctx.metadata.get('presentation') if ctx is not None and ctx.household_profile is None else None
    configured = {r['name']: mutable_copy(r) for r in legacy['factory_records']} if legacy is not None else {}
    result = []
    for name, (value, unit, bounds, group, description) in SPEC.items():
        record = dict(name=name, value=deepcopy(value), unit=unit,
            status=('placeholder' if 'PLACEHOLDER' in description or name in
                    ('hp_capex','hp_replacement_cost','hp_cooking_capex') else 'proposed'),
            source='Neutral modeling example; profile-specific evidence required',
            date_accessed=None, plausible_range=deepcopy(bounds), bounds=deepcopy(bounds),
            owner='user / installer / tax adviser', group=group, description=description,
            reasoning=description, confidence='Example only; no household evidence')
        if name=='ac_heating_additional_demand_pct':
            record['choices']=[0,25,50]
            record['range_interpretation']='illustrative useful-heat sensitivity scenarios, not uncertainty range or temperature/comfort prediction'
        if isinstance(value, str):
            record.update(choices=deepcopy(bounds), plausible_range=None)
        if name in configured:
            record = configured[name]
        if name in saved:
            entry = saved[name]
            record.update(value=deepcopy(entry['value']), status='provided',
                source='User-saved shared app default', date_accessed=entry['date'],
                owner=entry['owner'], factory_default=deepcopy(entry['factory_default']),
                confidence='User-provided default; not measured or independently verified')
        result.append(record)
    return result
