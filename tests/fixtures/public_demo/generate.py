"""Standalone analytic synthetic electricity/thermal/solar/price generator.

All quantities are explicitly fictional formulas with no real location claim.
Invented purely for public demonstrations and testing.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

HOURS_PER_YEAR = 8784
YEARS = (2023, 2024)
LEAP_YEARS = {2024}
ROME_TZ = ZoneInfo("Europe/Rome")


def generate(plane_count: int = 2) -> dict[str, np.ndarray]:
    """Generate synthetic time series for years 2023 and 2024.

    All quantities are based on explicitly fictional analytic formulas:
    - Daily load: 0.2 + 0.1 when hour 18..21 inclusive (load_civil, load_standard)
    - Seasonal temperature: 12 + 8 * cos(2 * pi * day_of_year / days_in_year)
    - Solar power per kWp: 0.6 * max(0, 1 - abs(hour - 12) / 6) * (1 + 0.1 * cos(2 * pi * day_of_year / days_in_year))
      scaled per plane by (1 - index * 0.04)
    - Beam loss: 0.05 * power
    - Diffuse loss: zeros
    - Thermal demand: max(0, 18 - temperature) * 0.03
    - Prices: 0.1 + 0.02 * (hour >= 18)

    Parameters
    ----------
    plane_count : int, default=2
        Number of solar planes (exact integer from 1 to 12).

    Returns
    -------
    dict[str, np.ndarray]
        Dictionary with keys:
        years, power, beam_loss, diffuse_loss, load_civil, load_standard,
        month, hour, temperature, valid, thermal, prices, utc_timestamp.
    """
    if isinstance(plane_count, bool) or not isinstance(plane_count, int):
        raise TypeError(f"plane_count must be an exact int, got {type(plane_count).__name__}")
    if not (1 <= plane_count <= 12):
        raise ValueError(f"plane_count must be between 1 and 12 inclusive, got {plane_count}")

    n_years = len(YEARS)
    years_arr = np.array(YEARS, dtype=np.int32)

    power = np.zeros((plane_count, n_years, HOURS_PER_YEAR), dtype=np.float64)
    beam_loss = np.zeros((plane_count, n_years, HOURS_PER_YEAR), dtype=np.float64)
    diffuse_loss = np.zeros((plane_count, n_years, HOURS_PER_YEAR), dtype=np.float64)
    load_civil = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.float64)
    load_standard = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.float64)
    month = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.int32)
    hour = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.int32)
    temperature = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.float64)
    valid = np.zeros((n_years, HOURS_PER_YEAR), dtype=bool)
    thermal = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.float64)
    prices = np.zeros((n_years, HOURS_PER_YEAR), dtype=np.float64)
    utc_timestamp = np.zeros((n_years, HOURS_PER_YEAR), dtype="<U20")

    for y_idx, y in enumerate(YEARS):
        is_leap = y in LEAP_YEARS
        d_in_y = 366 if is_leap else 365
        valid_hours = d_in_y * 24
        valid[y_idx, :valid_hours] = True

        m_arr = np.zeros(valid_hours, dtype=np.int32)
        h_std_arr = np.zeros(valid_hours, dtype=np.int32)
        h_civ_arr = np.zeros(valid_hours, dtype=np.int32)
        ts_arr = np.zeros(valid_hours, dtype="<U20")
        d_of_y = np.repeat(np.arange(d_in_y, dtype=np.float64), 24)

        base_dt = datetime(y, 1, 1, 0, 0, tzinfo=timezone.utc)
        for i in range(valid_hours):
            dt_utc = base_dt + timedelta(hours=i)
            ts_arr[i] = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            dt_std = dt_utc + timedelta(hours=1)
            m_arr[i] = dt_std.month
            h_std_arr[i] = dt_std.hour
            dt_civ = dt_utc.astimezone(ROME_TZ)
            h_civ_arr[i] = dt_civ.hour

        month[y_idx, :valid_hours] = m_arr
        hour[y_idx, :valid_hours] = h_std_arr
        utc_timestamp[y_idx, :valid_hours] = ts_arr

        # load_civil uses Europe/Rome civil hour with DST folds and gaps
        load_civil[y_idx, :valid_hours] = 0.2 + 0.1 * ((h_civ_arr >= 18) & (h_civ_arr <= 21)).astype(np.float64)

        # load_standard uses fixed standard UTC+1 hour
        load_standard[y_idx, :valid_hours] = 0.2 + 0.1 * ((h_std_arr >= 18) & (h_std_arr <= 21)).astype(np.float64)

        # seasonal temperature uses fixed-standard day of year
        seasonal_angle = 2.0 * np.pi * d_of_y / float(d_in_y)
        cos_seasonal = np.cos(seasonal_angle)
        temp_val = 12.0 + 8.0 * cos_seasonal
        temperature[y_idx, :valid_hours] = temp_val

        # thermal demand
        thermal[y_idx, :valid_hours] = np.maximum(0.0, 18.0 - temp_val) * 0.03

        # prices use fixed-standard hour
        prices[y_idx, :valid_hours] = 0.1 + 0.02 * (h_std_arr >= 18).astype(np.float64)

        # solar power per kWp uses fixed-standard hour
        diurnal = np.maximum(0.0, 1.0 - np.abs(h_std_arr - 12.0) / 6.0)
        solar_base = 0.6 * diurnal * (1.0 + 0.1 * cos_seasonal)

        for p in range(plane_count):
            plane_scale = 1.0 - p * 0.04
            p_val = solar_base * plane_scale
            power[p, y_idx, :valid_hours] = p_val
            beam_loss[p, y_idx, :valid_hours] = 0.05 * p_val

    return {
        "years": years_arr,
        "power": power,
        "beam_loss": beam_loss,
        "diffuse_loss": diffuse_loss,
        "load_civil": load_civil,
        "load_standard": load_standard,
        "month": month,
        "hour": hour,
        "temperature": temperature,
        "valid": valid,
        "thermal": thermal,
        "prices": prices,
        "utc_timestamp": utc_timestamp,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic demo electricity/thermal/solar data."
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Target directory where hourly.npz and provenance.json are written.",
    )
    parser.add_argument(
        "--planes",
        "--plane-count",
        type=int,
        default=2,
        help="Number of planes (default: 2, range: 1..12).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[3]
    output_dir = Path(args.output).resolve()

    try:
        output_dir.relative_to(repo_root)
        is_in_repo = True
    except ValueError:
        is_in_repo = False

    if is_in_repo or output_dir == repo_root:
        raise ValueError(
            f"Output directory cannot be inside repository root ({repo_root}): {output_dir}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    npz_path = output_dir / "hourly.npz"
    prov_path = output_dir / "provenance.json"

    if npz_path.exists() or prov_path.exists():
        raise FileExistsError(
            f"Target output file(s) already exist in {output_dir}; will not overwrite."
        )

    data = generate(plane_count=args.planes)
    np.savez_compressed(npz_path, **data)

    provenance = {
        "generator": "generate.py",
        "plane_count": args.planes,
        "plane_names": [f"p{i + 1}" for i in range(args.planes)],
        "years": [int(y) for y in YEARS],
        "hours_per_year": HOURS_PER_YEAR,
        "fictional": True,
        "disclaimer": "All quantities are explicitly fictional formulas with no real location claim.",
        "formulas": {
            "daily_load": "0.2 + 0.1 when hour in 18..21 inclusive",
            "temperature": "12 + 8 * cos(2 * pi * day_of_year / days_in_year)",
            "solar_power_per_kwp": "0.6 * max(0, 1 - abs(hour - 12) / 6) * (1 + 0.1 * cos(2 * pi * day_of_year / days_in_year)) * (1 - index * 0.04)",
            "beam_loss": "0.05 * power",
            "diffuse_loss": "0.0",
            "thermal_demand": "max(0, 18 - temperature) * 0.03",
            "prices": "0.1 + 0.02 * (hour >= 18)",
        },
    }
    with open(prov_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2)




def prepare_test_inputs(destination):
    """Build a separate synthetic legacy counterpart for legacy API coverage.

    No input file is read. Five analytic planes exercise the fixed legacy API;
    the public profile uses the independent two-plane v2 compiler. Monthly load
    normalization is explicit, matching the legacy projection contract.
    """
    import calendar
    import hashlib
    import os
    root = Path(destination).resolve()
    if root == Path(__file__).resolve().parents[3] or Path(__file__).resolve().parents[3] in root.parents:
        raise ValueError('Synthetic assets must stay outside the checkout')
    data_dir = root / 'legacy' / 'data'
    runtime = root / 'runtime'
    data_dir.mkdir(parents=True, mode=0o700)
    if os.name == 'nt':
        from feasibility.profile_store import ProfileStore
        ProfileStore(runtime)
    else:
        runtime.mkdir(mode=0o700)
    os.environ['PV_LEGACY_DATA_ROOT'] = str(data_dir)
    os.environ['PV_DATA_ROOT'] = str(runtime)
    arrays = generate(5)
    # Separate analytic compatibility weather keeps some heating degree-hours
    # in every month for monthly-budget tests; no measured weather is copied.
    for yi, year in enumerate(YEARS):
        n = 8784 if year in LEAP_YEARS else 8760
        day = np.arange(n) // 24
        arrays['temperature'][yi, :n] = (6. - 3.*np.cos(2*np.pi*day/(n/24))
            + 20.*np.cos(2*np.pi*(arrays['hour'][yi, :n]-14)/24) + (6.*yi-3.))
    # Independent compatibility demand: 10 kWh/day, 15 in summer.
    # It is deliberately separate from the basic 5.2 kWh/day generator.
    monthly = [(15.0 if 6 <= m <= 9 else 10.0) * calendar.monthrange(2023, m)[1]
               for m in range(1, 13)]
    for yi in range(len(YEARS)):
        for m in range(1, 13):
            mask = arrays['valid'][yi] & (arrays['month'][yi] == m)
            for key in ('load_civil', 'load_standard'):
                arrays[key][yi, mask] *= monthly[m-1] / arrays[key][yi, mask].sum()
    required = ('years','power','beam_loss','diffuse_loss','load_civil','load_standard',
                'month','hour','temperature','valid')
    np.savez_compressed(data_dir / 'hourly.npz', **{k: arrays[k] for k in required})
    names = ('south_exact','west','east','dormer','flat')
    yields = {}
    for i,name in enumerate(names):
        annual = arrays['power'][i].sum(axis=1)
        yields[name] = dict(unshaded_annual=annual.tolist(),
            beam_shaded_annual=(annual*.95).tolist(), all_radiation_shaded_annual=(annual*.95).tolist())
    audit = dict(fictional=True, annual_kwh=sum(monthly), monthly_kwh=monthly,
        pvgis_yields=yields, roofs=[dict(google_dc_kwh_kwp=None, annual_flux_kwh_kwp=None,
            segments=[i], panels=10) for i in range(5)],
        provenance='Analytic synthetic legacy counterpart; no provider observations or real home')
    (data_dir / 'audit.json').write_text(json.dumps(audit))
    from feasibility import assumptions
    factory = {k: v[0] for k,v in assumptions.SPEC.items()}
    # Keep the selected price shape explicit and generated, never a missing-file fallback.
    factory['export_price_profile'] = 'empirical_csud'
    (data_dir/'current_scenario.json').write_text(json.dumps(dict(settings=factory,quote=dict(line_items=[]),state_revision='synthetic-fixture-v1')))
    (data_dir/'user_defaults.json').write_text(json.dumps(dict(overrides={})))
    gas = [40.0] + [80.0 + 10.0 * (m % 3) for m in range(1,12)]
    gas[6:8] = [20.0,20.0]  # invented zero-space-heating summer months
    thermal = dict(GAS_MONTHLY=gas, COOLING_BALANCE_TEMP=24.)
    (runtime / 'legacy-thermal.json').write_text(json.dumps(thermal))
    invoice = dict(period_days=30, volume_smc=50., consumption_eur=45., fixed_eur=9.,
                   excise_including_regional_eur=8.3, vat_eur=7.31, total_eur=69.61)
    sizing = dict(version='synthetic-legacy-sizing-v1',
        roof_ids=['south','west','east','dormer','flat','secondary_south'],
        panel_maxima=dict(south=20,west=10,east=8,dormer=4,flat=6,secondary_south=4),
        sweep_layouts=[['no_pv',{}],['8S',{'south_panels':8}],['12S',{'south_panels':12}],
                      ['10S4E',{'south_panels':10,'east_panels':4}],
                      ['10S4W',{'south_panels':10,'west_panels':4}],
                      ['large_fictional',{'south_panels':20,'secondary_south_panels':4,'dormer_panels':4}]])
    presentation = dict(factory_defaults=factory, factory_records=assumptions.persisted_records(data_dir/'user_defaults.json'),
        known_invoice=invoice, electric_invoice_kwh=200., illustrative_prior_smc=200.,
        bracket_assumed='explicit synthetic interval', reference_notes=['Invented public test invoice; not measured.', '200 Smc is an illustrative assumption; actual prior cumulative is unknown and cannot be inferred from a rolling 12-month model.'],
        baseline_note=f'Synthetic annual gas input: {sum(gas)} Smc.', sizing_reference=sizing,
        calibration={}, calibration_benchmarks=dict(original_fixed_cost=1000., original_cost_kwp=1000.,
            benchmark_kwp=6., provided_capture_eur_kwh=.1, date='2024-01-01'))
    (runtime / 'legacy-presentation.json').write_text(json.dumps(presentation))
    prices = arrays['prices']
    shape = np.tile(np.where(np.arange(24)>=18, .12, .1), (12,1))
    shape /= shape.mean()
    np.savez_compressed(data_dir/'hourly_prices.npz', pzo=prices, normalized_shape=shape)
    np.savez_compressed(data_dir/'empirical_csud_prices.npz',
        month_hour_csud_oct2025_sep2026=shape*.1,month_hour_csud_cal2025=shape*.1,
        normalized_shape_oct2025_sep2026=shape,normalized_shape_cal2025=shape)
    prepare_price_assets(root/'price-archives',data_dir)
    prepare_compatibility_assets(root/'compatibility',arrays,gas,factory)
    for generated in root.rglob('*'):
        if generated.is_file(): generated.chmod(0o600)
    return data_dir, runtime


def model_snapshot(plane_count=2):
    """Complete deterministic model output descriptor for one fictional profile.

    Every scalar is retained with its type; every array is hashed with its exact
    dtype, shape and storage order. Only root compute duration is omitted. The
    fixed test build identity never supplies a real service worker.
    """
    import calendar
    import hashlib
    from copy import deepcopy
    from feasibility import assumptions, engine
    from feasibility.dataset_compile import SolarArtifact, compile_dataset
    from feasibility.load_projection import ProjectionPolicy, project_load
    from feasibility.profiles import canonical_json
    from feasibility.fiscal import rule_digest
    from feasibility.runtime_context import RuntimeContext, initialize_runtime
    from feasibility.thermal_baseline import resolve_thermal_baseline
    profile = json.loads(Path(__file__).with_name('profile.json').read_text())
    profile['roofs'] = [dict(id=f'p{i+1}',label=f'Fictional plane {i+1}',tilt_deg=20.,
        azimuth_deg=float((i*45+90)%360),max_kwp=4.,shading_fraction=.05) for i in range(plane_count)]
    profile['fiscal']['rule_revision'] = rule_digest(profile['fiscal'])
    arrays = generate(plane_count)
    monthly = [5.2*calendar.monthrange(2023,m)[1] for m in range(1,13)]
    load = project_load(None,monthly,YEARS,ProjectionPolicy(mode='synthetic',
        leap_policy='february_month_hour',reference_year=2023,acknowledged=True,variant='evening'))
    source_digest = hashlib.sha256(canonical_json(dict(fictional=True,formula_version='analytic-demo-v1',
        years=YEARS,planes=plane_count))).hexdigest()
    solar = SolarArtifact(YEARS,tuple(r['id'] for r in profile['roofs']),45.,9.,
        arrays['power'],arrays['beam_loss'],arrays['diffuse_loss'],arrays['temperature'],source_digest,0)
    compiled = compile_dataset(profile,solar,load)
    settings = {k:deepcopy(v[0]) for k,v in assumptions.SPEC.items()}
    settings.update(profile['default_settings'])
    settings.pop('baseline_cooling_kwh')
    settings.update(battery_kwh=5.,battery_modules=1,installation_year=2026,horizon_years=3,
        inverter_replace_year=1,battery_replace_year=2,pv_dispatch_model='physical',routing_priority='battery_first',
        pmg_election=False,pmg_eligible_confirmed=False,deduction_eligible_confirmed=False,
        unsupported_fiscal_year_acknowledged=True)
    ctx = RuntimeContext(owner_id='fictional-demo',input_revision=compiled.digest,dataset_digest=compiled.digest,
        price_revision='fictional-flat-v1',defaults_snapshot=settings,
        thermal_reference=resolve_thermal_baseline(profile),fiscal_revision=profile['fiscal']['rule_revision'],
        calendar_version='utc_hourly_v2',dataset={**compiled.arrays,'audit':json.loads(compiled.audit_bytes)},
        weather_identity=('fictional',compiled.digest,compiled.digest),records_snapshot=(),
        metadata=dict(dataset_manifest=json.loads(compiled.manifest_bytes),compiler_version='dataset-compiler-v2'),
        household_profile=profile,model_build_id='synthetic-public-model-v1')
    initialize_runtime(ctx)
    count = {r['id']:2 for r in profile['roofs']}
    scenarios = {
        'no_install':dict(roof_panels={},battery_kwh=0,battery_modules=0),
        'ac_baseline':dict(roof_panels=count),
        'dc_electrified':dict(roof_panels=count,pv_battery_coupling='dc_coupled',load_scenario='electrified'),
    }
    results = dict(profile=profile,arrays=arrays,compiled=dict(compiled.arrays),
                   calibration=engine.calibration(),scenarios={})
    for name,scenario in scenarios.items():
        result=engine.evaluate(scenario,detail=True)
        result.pop('compute_seconds',None)
        results['scenarios'][name]=result
    results['charts']=engine.chart_data(scenarios['ac_baseline'])
    results['dispatch']=engine.dispatch_matrix(scenarios['ac_baseline'],mode='forecast_2027')
    results['dispatch']['schedule_optimizer'].pop('elapsed_seconds',None)
    from tools.regression import model_records
    def describe(value):
        if isinstance(value,np.ndarray):
            order='F' if value.flags.f_contiguous and not value.flags.c_contiguous else 'C'
            return dict(kind='array',dtype=value.dtype.str,shape=list(value.shape),order=order,
                        sha256=hashlib.sha256(value.tobytes(order=order)).hexdigest())
        if isinstance(value,np.generic):
            return dict(kind='numpy_scalar',dtype=value.dtype.str,value=describe(value.item()))
        if isinstance(value,dict): return {k:describe(v) for k,v in sorted(value.items())}
        if isinstance(value,(list,tuple)):
            items=[describe(v) for v in value]
            if len(items)>16:
                raw=json.dumps(items,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
                return dict(kind=type(value).__name__,length=len(items),sha256=hashlib.sha256(raw).hexdigest())
            return dict(kind=type(value).__name__,items=items)
        if isinstance(value,float): return dict(kind='float',value=value.hex())
        return dict(kind=type(value).__name__,value=value)
    return describe(model_records(results))


def compare_reference(path):
    """Compare the committed synthetic reference; no capture/overwrite interface."""
    import os
    import subprocess
    import sys
    expected=json.loads(Path(path).read_text())
    actual={}
    for n in (2,7):
        proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--snapshot',str(n)],
            env={**os.environ,'NUMBA_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'},
            check=True,capture_output=True,text=True)
        actual[str(n)]=json.loads(proc.stdout)
    if expected.get('snapshots') != actual:
        raise ValueError('Public reference differs; never refresh expected values to obtain a pass')
    print('Public reference: exact (two and seven planes; all recorded fields)')

def build_synthetic_fixture():
    """Create a completely hand-computable independent synthetic fixture.

    Formulas:
        useful_heat_kwh = (gas_smc - min(gas_smc, summer_base)) * pcs * boiler_efficiency
        requested_heat_kwh = useful_heat_kwh * (1 + p / 100)  [p=0 here]
        electricity_kwh = requested_heat_kwh / scop
    """
    monthly_gas = 126.0
    summer_gas_base = 26.0
    net_heating_gas = 100.0  # 126.0 - 26.0
    gas_pcs = 10.0
    boiler_efficiency = 0.80
    scop = 4.0
    useful_heat = net_heating_gas * gas_pcs * boiler_efficiency  # 100 * 10 * 0.8 = 800.0 kWh
    electricity = useful_heat / scop  # 800.0 / 4.0 = 200.0 kWh

    # 48-hour timeline (2 days)
    # Permitted hours: 8..10 (hours 8, 9) and 18..20 (hours 18, 19)
    # Balance temperature: 18.0 C
    temps = np.full(48, 15.0, dtype=np.float64)  # default off-hour temp
    hours = np.tile(np.arange(24), 2)
    valid = np.ones(48, dtype=bool)
    months = np.ones(48, dtype=np.int64)

    # Day 1 active temps
    temps[8] = 8.0    # 18 - 8 = 10 deg
    temps[9] = 10.0   # 18 - 10 = 8 deg
    temps[18] = 6.0   # 18 - 6 = 12 deg
    temps[19] = 8.0   # 18 - 8 = 10 deg

    # Day 2 active temps
    temps[24 + 8] = 10.0  # 18 - 10 = 8 deg
    temps[24 + 9] = 12.0  # 18 - 12 = 6 deg
    temps[24 + 18] = 4.0  # 18 - 4 = 14 deg
    temps[24 + 19] = 6.0  # 18 - 6 = 12 deg

    active_indices = [8, 9, 18, 19, 32, 33, 42, 43]
    active_mask = np.zeros(48, dtype=bool)
    active_mask[active_indices] = True

    degree_hours = np.zeros(48, dtype=np.float64)
    degree_hours[8] = 10.0
    degree_hours[9] = 8.0
    degree_hours[18] = 12.0
    degree_hours[19] = 10.0
    degree_hours[32] = 8.0
    degree_hours[33] = 6.0
    degree_hours[42] = 14.0
    degree_hours[43] = 12.0

    total_degrees = float(degree_hours.sum())  # 80.0
    kwh_per_deg = electricity / total_degrees  # 200.0 / 80.0 = 2.5 kWh/deg

    expected_electricity = np.zeros(48, dtype=np.float64)
    for idx in active_indices:
        expected_electricity[idx] = degree_hours[idx] * kwh_per_deg

    metadata = {
        'description': 'Hand-computable independent synthetic fixture (2 days, 48 hours)',
        'monthly_gas_smc': monthly_gas,
        'summer_gas_base_smc': summer_gas_base,
        'net_heating_gas_smc': net_heating_gas,
        'gas_pcs_kwh_smc': gas_pcs,
        'boiler_efficiency': boiler_efficiency,
        'scop': scop,
        'balance_temp_c': 18.0,
        'useful_heat_kwh': useful_heat,
        'electricity_kwh': electricity,
        'total_degree_hours': total_degrees,
        'kwh_per_degree_hour': kwh_per_deg,
        'active_slots_count': len(active_indices),
        'active_slot_details': {
            'day1_h08': {'index': 8, 'temp_c': 8.0, 'deg_hours': 10.0, 'expected_kwh': 25.0},
            'day1_h09': {'index': 9, 'temp_c': 10.0, 'deg_hours': 8.0, 'expected_kwh': 20.0},
            'day1_h18': {'index': 18, 'temp_c': 6.0, 'deg_hours': 12.0, 'expected_kwh': 30.0},
            'day1_h19': {'index': 19, 'temp_c': 8.0, 'deg_hours': 10.0, 'expected_kwh': 25.0},
            'day2_h08': {'index': 32, 'temp_c': 10.0, 'deg_hours': 8.0, 'expected_kwh': 20.0},
            'day2_h09': {'index': 33, 'temp_c': 12.0, 'deg_hours': 6.0, 'expected_kwh': 15.0},
            'day2_h18': {'index': 42, 'temp_c': 4.0, 'deg_hours': 14.0, 'expected_kwh': 35.0},
            'day2_h19': {'index': 43, 'temp_c': 6.0, 'deg_hours': 12.0, 'expected_kwh': 30.0},
        },
        'sum_active_kwh': float(expected_electricity.sum()),
        'sum_inactive_kwh': float(expected_electricity[~active_mask].sum()),
    }

    arrays = {
        'synthetic_temperature': temps,
        'synthetic_hours': hours,
        'synthetic_valid': valid,
        'synthetic_months': months,
        'synthetic_active_mask': active_mask,
        'synthetic_degree_hours': degree_hours,
        'synthetic_expected_heat_kwh': expected_electricity,
    }
    return metadata, arrays

def prepare_compatibility_assets(root, arrays, gas, factory):
    """Independent analytic old-allocation counterpart, not a captured model result."""
    import hashlib
    from copy import deepcopy
    from tests.heating_oracle import heating_oracle
    root=Path(root);root.mkdir(mode=0o700)
    temp=arrays['temperature'];valid=arrays['valid'];civil=np.zeros_like(arrays['hour'])
    lookup=[]
    for yi,year in enumerate(YEARS):
        table={}
        for j in range(int(valid[yi].sum())):
            utc=datetime(year,1,1,tzinfo=timezone.utc)+timedelta(hours=j)
            standard=utc+timedelta(hours=1)
            civil[yi,j]=utc.astimezone(ROME_TZ).hour
            table[(standard.month,standard.day,standard.hour)]=j
        lookup.append(table)
    start=datetime(2027,1,1,tzinfo=timezone(timedelta(hours=1)))
    targets=[start+timedelta(hours=j) for j in range(8760)]
    months=np.array([t.month for t in targets]);hours=np.array([t.hour for t in targets])
    civil_target=np.array([t.astimezone(ROME_TZ).hour for t in targets])
    mapped=np.array([[temp[yi,lookup[yi][(t.month,t.day,t.hour)]] for t in targets] for yi in range(len(YEARS))])
    def windows(a,b,c,d):
        return [dict(id='morning',start_minute=a*60,end_minute=b*60),dict(id='evening',start_minute=c*60,end_minute=d*60)]
    definitions={
      'current_saved_scenario':('scheduled',8,12,16,20,'civil',4.),
      'factory_defaults':('scheduled',6,8,16,20,'civil',4.),
      'user_defaults_scop2':('scheduled',6,8,16,20,'civil',2.),
      'continuous_mode':('continuous',6,8,16,20,'civil',4.),
      'single_window_morning':('scheduled',6,8,16,16,'civil',4.),
      'single_window_evening':('scheduled',6,6,16,20,'civil',4.),
      'overnight_window':('scheduled',22,6,16,16,'civil',4.),
      'standard_timezone_saved':('scheduled',8,12,16,20,'standard',4.)}
    saved_arrays={};scenarios={}
    for name,(mode,a,b,c,d,clock,scop) in definitions.items():
        settings={**deepcopy(factory),'load_scenario':'electrified','planning_heat_pump':False,
            'heating_schedule_mode':mode,'ac_heating_windows':windows(a,b,c,d),'timezone_mode':clock,'scop':scop}
        src=heating_oracle(settings,temp,arrays['month'],arrays['hour'],valid,civil,gas,legacy=True)
        fc=heating_oracle(settings,mapped,months,hours,np.ones_like(mapped,bool),civil_target,gas,legacy=True)
        monthly=(np.array(gas)-np.minimum(gas,settings['summer_gas_base']))*settings['gas_pcs']*settings['boiler_efficiency']/scop
        sk='source_heating_'+name;fk='forecast_heating_'+name
        saved_arrays.update({sk:src,fk:fc})
        scenarios[name]=dict(settings=settings,source_array_key=sk,forecast_array_key=fk,
            annual_heating_electricity_kwh=float(monthly.sum()),monthly_heating_electricity_kwh=monthly.tolist())
    hand_meta,hand_arrays=build_synthetic_fixture();saved_arrays.update(hand_arrays)
    npz=root/'ac_heating_compatibility_fixtures.npz';np.savez_compressed(npz,**saved_arrays)
    saved=dict(heating_schedule_mode='scheduled',heating_on_hour=8,heating_off_hour=12,
        heating_evening_on_hour=16,heating_evening_off_hour=20,timezone_mode='civil',scop=4.,load_scenario='electrified',planning_heat_pump=False)
    data_dir=root.parent/'legacy/data'
    meta=dict(fictional=True,scenarios=scenarios,synthetic_fixture=hand_meta,
        live_inspection=dict(saved_scenario=saved,user_defaults=dict(scop=2.),factory_defaults={**saved,'heating_on_hour':6,'heating_off_hour':8}),
        weather_provenance=dict(hourly_npz_sha256=hashlib.sha256((data_dir/'hourly.npz').read_bytes()).hexdigest(),
            audit_json_sha256=hashlib.sha256((data_dir/'audit.json').read_bytes()).hexdigest(),
            pvgis_south_exact_json_sha256=None,fixtures_npz_sha256=hashlib.sha256(npz.read_bytes()).hexdigest()),
        tolerance_guidelines=dict(interval_equality='atol <= 1e-12 kWh, rtol = 0'),
        layer_precedence=dict(contract_invariants=['Explicit canonical windows in same payload win',
            'legacy-only override in a higher layer replaces lower-layer windows',
            '0% difference is checked against independent analytic allocation']))
    (root/'ac_heating_compatibility_metadata.json').write_text(json.dumps(meta))

def prepare_price_assets(root, data_dir):
    """Generate actual invented XML archives, then exercise the normal ingester."""
    import calendar
    import hashlib
    import zipfile
    from xml.etree.ElementTree import Element, SubElement, tostring
    from feasibility import prepare_market_prices as market
    from feasibility.market_catalog import update_catalog
    root=Path(root);root.mkdir(mode=0o700)
    archive_hashes={}
    for year in YEARS:
        archive=root/f'MGP_Prezzi_Synthetic_{year}.zip'
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_STORED) as z:
            for day in market.coverage_dates(f'{year}-01-01',f'{year+1}-01-01'):
                begin,end=market.day_bounds(day)
                n=int((end-begin).total_seconds()/3600)
                stamp=day.strftime('%Y%m%d')
                for duration,tag,period,suffix in ((3600,'Prezzi','Ora','MGPPrezzi.xml'),(900,'Prezzi15','Periodo','MGPPrezzi15.xml')):
                    xml=Element('NewDataSet')
                    for i in range(n*3600//duration):
                        row=SubElement(xml,tag)
                        SubElement(row,'Data').text=stamp
                        SubElement(row,'Mercato').text='MGP'
                        SubElement(row,period).text=str(i+1)
                        instant=begin+timedelta(seconds=i*duration)
                        standard=instant+timedelta(hours=1)
                        for j,series in enumerate(market.SERIES):
                            SubElement(row,series).text=str(100+20*(standard.hour>=18)+j)
                    info=zipfile.ZipInfo(stamp+suffix,date_time=(1980,1,1,0,0,0))
                    info.external_attr=0o100600 << 16
                    z.writestr(info,tostring(xml))
        archive_hashes[archive.name]=hashlib.sha256(archive.read_bytes()).hexdigest()
    ingest=market.ingest_mgp_data(root,'2023-01-01','2025-01-01')
    catalog=update_catalog(root.parent/'catalog',ingest,publish=True,window_start='2023-01-01',window_end='2024-01-01')
    first=market.compute_empirical_window(ingest['days'],'2023-01-01','2024-01-01',series='CSUD',require_climatology=True)
    second=market.compute_empirical_window(ingest['days'],'2024-01-01','2025-01-01',series='CSUD',require_climatology=True)
    counts={23:0,24:0,25:0};dates={};checked=0;max_diff=0.
    for stamp,day in ingest['days'].items():
        n=len(day['records'])//(4*len(market.SERIES));counts[n]+=1
        if n!=24:dates[('spring_' if n==23 else 'autumn_')+stamp[:4]]=stamp.replace('-','')
        checked+=day['overlap']['compared_series_hours']//len(market.SERIES)
        max_diff=max(max_diff,day['overlap']['max_abs_diff_eur_mwh'])
    meta=dict(source='Synthetic GME-format analytic archives; not observed provider data',fictional=True,zone='CSUD',
        catalog_revision=catalog['revision'],total_zip_archives=len(archive_hashes),archive_hashes=archive_hashes,
        total_civil_days=len(ingest['days']),total_hourly_records=checked,
        hourly_quarterhour_agreement=dict(checked_hours=checked,max_abs_difference_eur_mwh=max_diff),
        dst_counts={f'{n}_hour_days':v for n,v in counts.items()},dst_dates=dates)
    meta.update(origin_url='urn:synthetic:analytic-prices',retrieval='generated offline',
        edge_month_spill_handling='Exact half-open Europe/Rome civil dates; fixed UTC+1 statistics',
        windows={'oct2025_sep2026':dict(dates_coverage=['2023-01-01','2024-01-01'],mean_csud_eur_kwh=first['mean_eur_kwh']),
                 'calendar_2025':dict(dates_coverage=['2024-01-01','2025-01-01'],mean_csud_eur_kwh=second['mean_eur_kwh'])})
    with np.load(data_dir/'hourly.npz',allow_pickle=False) as archive:
        months,hours,valid=(archive[key] for key in ('month','hour','valid'))
    np.savez_compressed(data_dir/'empirical_csud_prices.npz',
        month_hour_csud_oct2025_sep2026=first['matrix_eur_kwh'],month_hour_csud_cal2025=second['matrix_eur_kwh'],
        normalized_shape_oct2025_sep2026=first['normalized_shape'],normalized_shape_cal2025=second['normalized_shape'],meta_json=json.dumps(meta),
        pzo_proxy_oct2025_sep2026=market.expand_pzo_proxy(first['normalized_shape'],
            months,hours,valid),
        pzo_proxy_cal2025=market.expand_pzo_proxy(second['normalized_shape'],
            months,hours,valid))
    (data_dir/'empirical_csud_meta.json').write_text(json.dumps(meta))


if __name__ == '__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--snapshot':
        print(json.dumps(model_snapshot(int(sys.argv[2])),sort_keys=True,separators=(',',':')))
    elif len(sys.argv)==3 and sys.argv[1]=='--compare-reference':
        compare_reference(sys.argv[2])
    else:
        main()
