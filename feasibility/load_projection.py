"""Explicit month/hour load models; measured intervals are never repaired.

All target slots start on UTC hours. Both mappings are normalized using the
fixed UTC+1 compatibility months consumed by the existing engine. Civil shape
training uses Europe/Rome, including both DST folds as separate elapsed hours.
These are modeled hourly kWh, never newly claimed measurements.

Month vs band semantics: fixed UTC+1 calendar months partition monthly totals,
while Europe/Rome civil clock classifies hourly ARERA TOU bands (1, 2, 3) across
both civil and standard arrays.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo
import calendar
import hashlib
import math
import numpy as np

from .consumption_ingest import ValidatedIntervals
from .profiles import canonical_json

PROJECTION_VERSION = 'month-hour-v1'
SYNTHETIC_SHAPE_VERSION = 'synthetic-shape-v1'
UTC = timezone.utc
STANDARD = timezone(timedelta(hours=1))
ROME = ZoneInfo('Europe/Rome')


_ITALIAN_NATIONAL_HOLIDAYS = {
    (1, 1), (1, 6), (4, 25), (5, 1), (6, 2),
    (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)
}


def _easter_monday(year: int) -> date:
    """Compute Easter Monday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day) + timedelta(days=1)


def arera_band(dt_rome: datetime) -> int:
    """Classify datetime into ARERA TOU band (1=F1, 2=F2, 3=F3) under Europe/Rome civil clock."""
    if dt_rome.tzinfo is None:
        dt = dt_rome.replace(tzinfo=ROME)
    else:
        dt = dt_rome.astimezone(ROME)

    if (dt.month, dt.day) in _ITALIAN_NATIONAL_HOLIDAYS or dt.date() == _easter_monday(dt.year):
        return 3

    weekday = dt.weekday()
    if weekday == 6:  # Sunday
        return 3
    hour = dt.hour
    if weekday < 5:  # Monday to Friday
        if 8 <= hour < 19:
            return 1
        if hour == 7 or 19 <= hour < 23:
            return 2
        return 3
    # Saturday
    if 7 <= hour < 23:
        return 2
    return 3


def immutable_array(value):
    """Bytes-backed arrays cannot have WRITEABLE re-enabled by a caller."""
    a = np.ascontiguousarray(value)
    return np.frombuffer(a.tobytes(), dtype=a.dtype).reshape(a.shape)


def checked_years(years):
    years = tuple(years)
    if (not 1 <= len(years) <= 30 or
            any(type(y) is not int or not 1900 <= y <= 2100 for y in years) or
            years != tuple(sorted(set(years)))):
        raise ValueError('years must be sorted unique integers, 1..30 within 1900..2100')
    return years


def utc_slots(year):
    start = datetime(year, 1, 1, tzinfo=UTC)
    return tuple(start + timedelta(hours=i) for i in range(8784 if calendar.isleap(year) else 8760))


@dataclass(frozen=True)
class ProjectionPolicy:
    mode: str                         # measured or synthetic
    leap_policy: str                   # february_month_hour
    reference_year: int                # annual-only allocation year
    acknowledged: bool = False
    synthetic_completion: bool = False
    variant: str = 'evening'
    annual_kwh: float | None = None
    monthly_f1: tuple[float, ...] | None = None
    monthly_f2: tuple[float, ...] | None = None
    monthly_f3: tuple[float, ...] | None = None


@dataclass(frozen=True)
class LoadProjection:
    """All target slots are modeled; provenance separately records training support."""
    years: tuple[int, ...]
    load_civil: np.ndarray
    load_standard: np.ndarray
    monthly_totals: tuple[float, ...]
    provenance: object
    synthesized_fraction: float
    acknowledgement_required: bool
    acknowledged: bool
    digest: str


def projection_digest(years, totals, provenance, arrays, acknowledged):
    metadata = dict(provenance=dict(provenance), totals=totals, years=years,
                    synthesized_fraction=1.0, acknowledgement_required=True,
                    acknowledged=acknowledged)
    return hashlib.sha256(canonical_json(metadata) + b''.join(a.tobytes() for a in arrays)).hexdigest()


def _totals(value):
    try:
        values = tuple(value)
    except TypeError:
        raise ValueError('twelve monthly totals required') from None
    if (len(values) != 12 or any(type(x) not in (int, float) or
            not math.isfinite(x) or x < 0 for x in values) or
            not math.isfinite(sum(values))):
        raise ValueError('twelve finite nonnegative monthly totals required')
    return tuple(float(x) for x in values)


def aggregate_tou_to_monthly(monthly_f1, monthly_f2, monthly_f3):
    """Pure aggregation of 12 monthly F1/F2/F3 values into monthly totals."""
    t1 = _totals(monthly_f1)
    t2 = _totals(monthly_f2)
    t3 = _totals(monthly_f3)
    return _totals(a + b + c for a, b, c in zip(t1, t2, t3))


def _train(source, zone):
    sums = np.zeros((12, 24))
    hours = np.zeros((12, 24))
    for rec in source.intervals:
        pos = rec.start_utc
        end = pos + timedelta(seconds=rec.duration_seconds)
        while pos < end:
            nxt = min(end, pos.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            fraction = (nxt - pos).total_seconds() / rec.duration_seconds
            local = pos.astimezone(zone)
            sums[local.month - 1, local.hour] += rec.energy_kwh * fraction
            hours[local.month - 1, local.hour] += (nxt - pos).total_seconds() / 3600
            pos = nxt
    return np.divide(sums, hours, out=np.zeros_like(sums), where=hours > 0), hours


def _measured_totals(source):
    # Only twelve whole consecutive local months may supply their own totals.
    zone = STANDARD if source.options.fixed_clock else ROME
    start, end = source.coverage_start.astimezone(zone), source.coverage_end.astimezone(zone)
    if (start.day != 1 or end.day != 1 or any((x.hour, x.minute, x.second, x.microsecond) !=
            (0, 0, 0, 0) for x in (start, end)) or
            (end.year * 12 + end.month) - (start.year * 12 + start.month) != 12):
        raise ValueError('partial coverage requires twelve supplied monthly totals')
    shape, hours = _train(source, zone)
    return _totals(np.sum(shape * hours, axis=1).tolist())


def _validate_source(source):
    if (not isinstance(source, ValidatedIntervals) or not source.coverage_declared or
            not 1 <= len(source.intervals) <= 250_000):
        raise ValueError('explicit validated coverage required')
    cursor = source.coverage_start
    if cursor.tzinfo is None or cursor.utcoffset() != timedelta(0):
        raise ValueError('UTC coverage required')
    for rec in source.intervals:
        if (rec.start_utc != cursor or rec.start_utc.utcoffset() != timedelta(0) or
                type(rec.duration_seconds) is not int or rec.duration_seconds not in (900, 3600) or
                not math.isfinite(rec.energy_kwh) or rec.energy_kwh < 0):
            raise ValueError('invalid measured coverage: gaps/overlaps are never repaired')
        cursor += timedelta(seconds=rec.duration_seconds)
    if cursor != source.coverage_end:
        raise ValueError('measured coverage mismatch')
    if (not math.isfinite(source.total_energy_kwh) or
            not math.isclose(math.fsum(r.energy_kwh for r in source.intervals),
                             source.total_energy_kwh, rel_tol=1e-12, abs_tol=1e-9)):
        raise ValueError('measured energy total mismatch')


def project_load(intervals, monthly_totals, years, policy):
    """Build a model, with no filesystem access, source mutation or gap filling."""
    years = checked_years(years)
    if (not isinstance(policy, ProjectionPolicy) or policy.mode not in ('measured', 'synthetic') or
            policy.leap_policy != 'february_month_hour' or type(policy.reference_year) is not int or
            not 1900 <= policy.reference_year <= 2100 or
            any(type(x) is not bool for x in (policy.acknowledged, policy.synthetic_completion))):
        raise ValueError('explicit projection/leap/reference-year policy required')
    synthetic_weights(years[0], policy.variant)  # Validate even for measured mode.
    has_f1 = policy.monthly_f1 is not None
    has_f2 = policy.monthly_f2 is not None
    has_f3 = policy.monthly_f3 is not None
    has_bands = has_f1 or has_f2 or has_f3
    if has_bands:
        if not (has_f1 and has_f2 and has_f3):
            raise ValueError('all three TOU bands (monthly_f1, monthly_f2, monthly_f3) must be provided together')
        if policy.mode != 'synthetic':
            raise ValueError('TOU bands require synthetic mode')
        if policy.annual_kwh is not None:
            raise ValueError('cannot specify both annual_kwh and TOU bands')
        f1_totals = _totals(policy.monthly_f1)
        f2_totals = _totals(policy.monthly_f2)
        f3_totals = _totals(policy.monthly_f3)
        band_totals = aggregate_tou_to_monthly(monthly_f1=f1_totals, monthly_f2=f2_totals, monthly_f3=f3_totals)
        if monthly_totals is None:
            monthly_totals = band_totals
        else:
            reconciled = _totals(monthly_totals)
            for pt, bt in zip(reconciled, band_totals):
                if not math.isclose(pt, bt, rel_tol=1e-9, abs_tol=1e-6):
                    raise ValueError('provided monthly totals do not reconcile with TOU band sums')
            monthly_totals = band_totals
    if policy.mode == 'synthetic':
        if intervals is not None:
            raise ValueError('synthetic mode must not discard supplied measurements')
        if monthly_totals is None:
            annual = policy.annual_kwh
            if type(annual) not in (int, float) or not math.isfinite(annual) or annual < 0:
                raise ValueError('annual-only synthetic mode requires finite annual_kwh')
            days = 366 if calendar.isleap(policy.reference_year) else 365
            monthly_totals = [annual * calendar.monthrange(policy.reference_year, m)[1] / days
                              for m in range(1, 13)]
        elif policy.annual_kwh is not None:
            raise ValueError('choose monthly totals OR annual total')
        shape_pairs = None
    else:
        _validate_source(intervals)
        if policy.annual_kwh is not None:
            raise ValueError('annual-only totals require synthetic mode')
        if monthly_totals is None:
            monthly_totals = _measured_totals(intervals)
        shape_pairs = [_train(intervals, z) for z in (ROME, STANDARD)]
    totals = _totals(monthly_totals)
    outputs = [np.zeros((len(years), 8784)), np.zeros((len(years), 8784))]
    completed = 0
    slots_used = 0
    for yi, year in enumerate(years):
        slots = utc_slots(year)
        norm_month = np.array([t.astimezone(STANDARD).month for t in slots])
        slot_bands = np.array([arera_band(t) for t in slots]) if has_bands else None
        template = synthetic_weights(year, policy.variant)
        for oi, zone in enumerate((ROME, STANDARD)):
            if shape_pairs is None:
                weights = template.copy()
                if zone == STANDARD:
                    # Explicit fixed-clock sensitivity, independently of DST.
                    weights = _synthetic_at(slots, zone, policy.variant)
            else:
                shape, counts = shape_pairs[oi]
                local = [t.astimezone(zone) for t in slots]
                weights = np.array([shape[t.month - 1, t.hour] for t in local])
                missing = np.array([counts[t.month - 1, t.hour] == 0 for t in local])
                needed = missing & np.array([totals[m - 1] > 0 for m in norm_month])
                if np.any(needed) and not policy.synthetic_completion:
                    raise ValueError('missing month/hour shape cell; explicit synthetic completion required')
                if np.any(needed):
                    fallback = _synthetic_at(slots, zone, policy.variant)
                    # Preserve measured shape scale when completing missing cells.
                    observed = shape[counts > 0]
                    scale = float(observed.mean()) if observed.size else 1.0
                    weights[needed] = fallback[needed] * scale
                    completed += int(needed.sum())
            if has_bands:
                for month in range(1, 13):
                    for b_id, b_target in ((1, f1_totals[month - 1]),
                                           (2, f2_totals[month - 1]),
                                           (3, f3_totals[month - 1])):
                        use = (norm_month == month) & (slot_bands == b_id)
                        if b_target == 0:
                            weights[use] = 0
                            continue
                        mass = float(weights[use].sum())
                        if not math.isfinite(mass) or mass <= 0:
                            raise ValueError('positive monthly target has zero shape mass')
                        weights[use] *= b_target / mass
                        # Remove floating summation residue from one positive slot.
                        index = np.flatnonzero(use & (weights > 0))[-1]
                        weights[index] += b_target - float(weights[use].sum())
            else:
                for month, total in enumerate(totals, 1):
                    use = norm_month == month
                    if total == 0:
                        weights[use] = 0
                        continue
                    mass = float(weights[use].sum())
                    if not math.isfinite(mass) or mass <= 0:
                        raise ValueError('positive monthly target has zero shape mass')
                    weights[use] *= total / mass
                    # Remove floating summation residue from one positive slot.
                    index = np.flatnonzero(use & (weights > 0))[-1]
                    weights[index] += total - float(weights[use].sum())
            if not np.all(np.isfinite(weights)) or np.any(weights < 0):
                raise ValueError('invalid projected energy')
            outputs[oi][yi, :len(slots)] = weights
            slots_used += len(slots)
    provenance = dict(projection_version=PROJECTION_VERSION,
                      template_version=SYNTHETIC_SHAPE_VERSION, template_seed=0,
                      mode=policy.mode, variant=policy.variant, reference_year=policy.reference_year,
                      leap_policy=policy.leap_policy, normalization='fixed_utc_plus_1_month',
                      source_digest=intervals.source_digest if intervals else None,
                      coverage_start=intervals.coverage_start.isoformat() if intervals else None,
                      coverage_end=intervals.coverage_end.isoformat() if intervals else None,
                      source_energy_kwh=intervals.total_energy_kwh if intervals else 0,
                      synthetic_completion=policy.synthetic_completion,
                      completion_fraction=completed / slots_used,
                      measured_training_fraction=(1 - completed / slots_used) if intervals else 0.0,
                      synthetic_training_fraction=(completed / slots_used) if intervals else 1.0,
                      limitations=('Modeled month/hour load; no intrahour peaks or inferred heating.',))
    if has_bands:
        provenance['tou_mode'] = 'arera_f1_f2_f3'
    frozen = tuple(immutable_array(x) for x in outputs)
    digest = projection_digest(years, totals, provenance, frozen, policy.acknowledged)
    # Every target value is a model, including values trained on measured data.
    return LoadProjection(years, *frozen, totals, MappingProxyType(provenance), 1.0,
                          True, policy.acknowledged, digest)


_DAYTIME_HOURS = np.array([
    0.30, 0.25, 0.25, 0.25, 0.30, 0.40,  # 00-05 Night
    0.70, 1.10, 1.60, 1.80, 1.90, 1.90,  # 06-11 Morning
    1.80, 1.80, 1.80, 1.70, 1.50, 1.20,  # 12-17 Day
    0.90, 0.70, 0.60, 0.50, 0.40, 0.35,  # 18-23 Evening
], dtype=np.float64)

_EVENING_HOURS = np.array([
    0.35, 0.30, 0.25, 0.25, 0.25, 0.30,  # 00-05 Night
    0.60, 1.00, 0.90, 0.70, 0.60, 0.65,  # 06-11 Morning
    0.70, 0.65, 0.65, 0.70, 0.90, 1.30,  # 12-17 Day ramp
    1.80, 2.00, 1.90, 1.60, 1.10, 0.60,  # 18-23 Evening peak
], dtype=np.float64)


def synthetic_weights(year: int, variant: str) -> np.ndarray:
    """Return hourly float64 weights for full UTC year starting Jan 1 00:00 UTC."""
    if type(year) is not int or not (1900 <= year <= 2100):
        raise ValueError(f"year must be integer in [1900, 2100], got {year!r}")
    if variant not in ('flat', 'daytime', 'evening'):
        raise ValueError(f"variant must be one of {('flat', 'daytime', 'evening')}, got {variant!r}")

    return _synthetic_at(utc_slots(year), ROME, variant)


def _synthetic_at(slots, zone, variant):
    if variant == 'flat':
        return np.ones(len(slots), dtype=np.float64)
    hour_weights = _DAYTIME_HOURS if variant == 'daytime' else _EVENING_HOURS
    weekend_factor = 0.65 if variant == 'daytime' else 1.15
    return np.array([hour_weights[t.astimezone(zone).hour] *
                     (weekend_factor if t.astimezone(zone).weekday() >= 5 else 1.0)
                     for t in slots], dtype=np.float64)
