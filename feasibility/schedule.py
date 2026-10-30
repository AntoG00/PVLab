"""Canonical AC heating schedule schema, validation, layer migration, and minute overlaps."""
from copy import deepcopy
import math
import warnings
import numpy as np

DEFAULT_AC_HEATING_WINDOWS = [
    {'id': 'morning', 'start_minute': 360, 'end_minute': 480},
    {'id': 'evening', 'start_minute': 960, 'end_minute': 1200},
]

LEGACY_HEATING_KEYS = {
    'heating_on_hour',
    'heating_off_hour',
    'heating_evening_on_hour',
    'heating_evening_off_hour',
}

SCHEDULE_SETTINGS = ('ac_heating_windows', 'heating_schedule_mode')

DIAGNOSTICS = []


def record_diagnostic(msg, category="warning"):
    """Record an observable diagnostic message and emit UserWarning."""
    DIAGNOSTICS.append({'message': msg, 'category': category})
    warnings.warn(msg, UserWarning, stacklevel=2)


def get_diagnostics():
    """Return a shallow copy of recorded diagnostics."""
    return list(DIAGNOSTICS)


def clear_diagnostics():
    """Clear recorded diagnostics."""
    DIAGNOSTICS.clear()


def validate_ac_heating_windows(windows):
    """Validate and normalize canonical AC heating windows.

    Each window must be an object with:
      - 'id': non-empty string. If omitted, assigned stable internal ID 'window_1', 'window_2', ...
      - 'start_minute': integer 0..1439
      - 'end_minute': integer 0..1440

    Rules:
      - start inclusive, end exclusive.
      - start > end: overnight window wrapping midnight.
      - start == end: inactive window (valid, preserved!).
      - 0..1440: all-day window.
      - []: empty schedule (valid UI draft!).

    Strictly rejects:
      - booleans (isinstance(v, bool))
      - non-integers (float even if int-like, strings, None, etc.)
      - non-finite numbers (NaN, Inf)
      - out-of-bounds start (<0 or >1439) or end (<0 or >1440)
      - unknown fields in window objects (anything besides id, start_minute, end_minute)
      - inappropriate IDs (non-string, empty string, etc.)
      - duplicate window IDs
      - input not a list
    """
    if not isinstance(windows, list):
        raise ValueError(f"ac_heating_windows must be a list of window objects, got {type(windows).__name__}")

    normalized = []
    seen_ids = set()
    for idx, item in enumerate(windows):
        if not isinstance(item, dict):
            raise ValueError(f"Each window in ac_heating_windows must be an object, got {type(item).__name__} at index {idx}")

        unknown = set(item) - {'id', 'start_minute', 'end_minute'}
        if unknown:
            raise ValueError(f"Unknown fields in window at index {idx}: {', '.join(sorted(unknown))}")

        if 'start_minute' not in item or 'end_minute' not in item:
            raise ValueError(f"Window at index {idx} must specify both 'start_minute' and 'end_minute'")

        start_m = item['start_minute']
        end_m = item['end_minute']

        if isinstance(start_m, bool) or not isinstance(start_m, int) or type(start_m) is not int:
            raise ValueError(f"Window 'start_minute' at index {idx} must be an integer, got {start_m!r}")
        if isinstance(end_m, bool) or not isinstance(end_m, int) or type(end_m) is not int:
            raise ValueError(f"Window 'end_minute' at index {idx} must be an integer, got {end_m!r}")

        if start_m < 0 or start_m > 1439:
            raise ValueError(f"Window 'start_minute' at index {idx} must be between 0 and 1439, got {start_m}")
        if end_m < 0 or end_m > 1440:
            raise ValueError(f"Window 'end_minute' at index {idx} must be between 0 and 1440, got {end_m}")

        if 'id' in item:
            wid = item['id']
            if isinstance(wid, bool) or not isinstance(wid, str) or not wid.strip():
                raise ValueError(f"Window 'id' at index {idx} must be a non-empty string, got {wid!r}")
        else:
            wid = f"window_{idx + 1}"

        if wid in seen_ids:
            raise ValueError(f"Duplicate window id {wid!r} at index {idx}")
        seen_ids.add(wid)

        normalized.append({
            'id': wid,
            'start_minute': start_m,
            'end_minute': end_m,
        })

    return normalized


def _validate_legacy_hour(val, name):
    """Validate a single legacy hour field strictly (0..23 integer, reject bool/float/nan)."""
    if isinstance(val, bool) or not isinstance(val, int) or isinstance(val, float):
        raise ValueError(f"{name} must be an integer, got {val!r}")
    if val < 0 or val > 23:
        raise ValueError(f"{name} must be between 0 and 23, got {val}")
    return val


def migrate_and_resolve_schedule(settings, base_windows=None):
    """Resolve schedule settings across layers before flattening.

    Precedence:
    1. Explicit canonical `ac_heating_windows` in settings wins.
       If legacy heating fields are also present in settings, canonical wins and
       conflicting legacy fields are reported via observable diagnostics and UserWarning.
    2. If only legacy heating fields are present in settings:
       Applies partial/full legacy patch to base_windows (or factory default).
       Identifiable 'morning' and 'evening' windows are updated.
       Extra windows are preserved in order.
       If 'morning' or 'evening' is missing or ambiguous (>1 match), raises ValueError.
    3. If neither canonical nor legacy schedule keys are present:
       Returns validated base_windows (or factory default).
    """
    settings = dict(settings) if settings is not None else {}
    has_canonical = 'ac_heating_windows' in settings
    present_legacy = set(settings) & LEGACY_HEATING_KEYS

    if has_canonical:
        canonical_raw = settings['ac_heating_windows']
        validated = validate_ac_heating_windows(canonical_raw)
        if present_legacy:
            derived = derive_legacy_heating_hours(validated)
            conflicts = {k for k in present_legacy if settings.get(k) != derived.get(k)}
            if conflicts:
                record_diagnostic(
                    f"Payload provides both canonical 'ac_heating_windows' and conflicting legacy heating fields "
                    f"({', '.join(sorted(conflicts))}). Explicit canonical windows take precedence; "
                    f"legacy fields are ignored.",
                    category="conflict"
                )
        return validated

    if present_legacy:
        # Resolve against base_windows or factory defaults
        if base_windows is None:
            source_windows = deepcopy(DEFAULT_AC_HEATING_WINDOWS)
        else:
            source_windows = deepcopy(base_windows)

        windows = [dict(w) for w in source_windows]

        # Morning patch
        morning_keys = {'heating_on_hour', 'heating_off_hour'} & present_legacy
        if morning_keys:
            morning_matches = [w for w in windows if w.get('id') == 'morning']
            if len(morning_matches) != 1:
                raise ValueError(
                    f"Cannot apply legacy heating window patch: 'morning' window is "
                    f"{'missing' if len(morning_matches) == 0 else 'ambiguous'} in current schedule "
                    f"(found {len(morning_matches)} matches)."
                )
            m_win = morning_matches[0]
            if 'heating_on_hour' in settings:
                h = _validate_legacy_hour(settings['heating_on_hour'], 'heating_on_hour')
                m_win['start_minute'] = h * 60
            if 'heating_off_hour' in settings:
                h = _validate_legacy_hour(settings['heating_off_hour'], 'heating_off_hour')
                m_win['end_minute'] = h * 60

        # Evening patch
        evening_keys = {'heating_evening_on_hour', 'heating_evening_off_hour'} & present_legacy
        if evening_keys:
            evening_matches = [w for w in windows if w.get('id') == 'evening']
            if len(evening_matches) != 1:
                raise ValueError(
                    f"Cannot apply legacy heating window patch: 'evening' window is "
                    f"{'missing' if len(evening_matches) == 0 else 'ambiguous'} in current schedule "
                    f"(found {len(evening_matches)} matches)."
                )
            e_win = evening_matches[0]
            if 'heating_evening_on_hour' in settings:
                h = _validate_legacy_hour(settings['heating_evening_on_hour'], 'heating_evening_on_hour')
                e_win['start_minute'] = h * 60
            if 'heating_evening_off_hour' in settings:
                h = _validate_legacy_hour(settings['heating_evening_off_hour'], 'heating_evening_off_hour')
                e_win['end_minute'] = h * 60

        return validate_ac_heating_windows(windows)

    if base_windows is not None:
        return validate_ac_heating_windows(base_windows)

    return deepcopy(DEFAULT_AC_HEATING_WINDOWS)


def derive_legacy_heating_hours(windows):
    """Derive backward-compatible legacy heating hour keys from canonical windows.

    Populates:
      - heating_on_hour (morning window start_minute // 60, default 6)
      - heating_off_hour (morning window end_minute // 60, default 8)
      - heating_evening_on_hour (evening window start_minute // 60, default 16)
      - heating_evening_off_hour (evening window end_minute // 60, default 20)
    """
    if not windows:
        return {
            'heating_on_hour': 6,
            'heating_off_hour': 8,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 20,
        }

    m_win = next((w for w in windows if w.get('id') == 'morning'), None)
    e_win = next((w for w in windows if w.get('id') == 'evening'), None)

    if m_win is None and len(windows) >= 1:
        m_win = windows[0]
    if e_win is None and len(windows) >= 2 and windows[1] is not m_win:
        e_win = windows[1]

    on_h = (m_win['start_minute'] // 60) if m_win is not None else 6
    off_h = (m_win['end_minute'] // 60) if m_win is not None else 8
    e_on_h = (e_win['start_minute'] // 60) if e_win is not None else 16
    e_off_h = (e_win['end_minute'] // 60) if e_win is not None else 20

    return {
        'heating_on_hour': int(on_h),
        'heating_off_hour': int(off_h),
        'heating_evening_on_hour': int(e_on_h),
        'heating_evening_off_hour': int(e_off_h),
    }


def canonical_windows_minute_mask(windows):
    """Return a boolean array of length 1440 indicating active minutes in the day.

    Start minute is inclusive, end minute is exclusive.
    start > end wraps midnight (e.g. 1320..360 active for 1320..1439 and 0..359).
    start == end is inactive (0 minutes active).
    """
    mask = np.zeros(1440, dtype=bool)
    if not windows:
        return mask

    for w in windows:
        start = w['start_minute']
        end = w['end_minute']
        if start == end:
            continue
        elif start < end:
            mask[start:end] = True
        else:
            mask[start:1440] = True
            mask[0:end] = True

    return mask


def canonical_windows_hourly_overlaps(windows):
    """Return an array of length 24 containing the number of active minutes in each clock hour (0..60)."""
    mask = canonical_windows_minute_mask(windows)
    overlaps = np.zeros(24, dtype=np.float64)
    for h in range(24):
        overlaps[h] = np.sum(mask[h * 60:(h + 1) * 60])
    return overlaps


def freeze(val):
    """Deterministically convert nested structures (dicts, lists, sets) into immutable hashable tuples."""
    if isinstance(val, dict):
        return tuple((k, freeze(v)) for k, v in sorted(val.items()))
    if isinstance(val, list):
        return tuple(freeze(v) for v in val)
    if isinstance(val, set):
        return tuple(sorted(freeze(v) for v in val))
    if isinstance(val, tuple):
        return tuple(freeze(v) for v in val)
    return val


def thaw(val):
    """Recursively thaw frozen immutable tuples back into standard dicts and lists."""
    if isinstance(val, tuple):
        if val and all(isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str) for x in val):
            return {k: thaw(v) for k, v in val}
        return [thaw(x) for x in val]
    if isinstance(val, list):
        return [thaw(x) for x in val]
    if isinstance(val, dict):
        return {k: thaw(v) for k, v in val.items()}
    return val


def thaw_settings(s):
    """Thaw a settings mapping or tuple of pairs into a dict with canonical list of window objects."""
    if isinstance(s, (tuple, list)):
        s_dict = dict(s)
    else:
        s_dict = dict(s)

    if 'ac_heating_windows' in s_dict:
        raw_windows = s_dict['ac_heating_windows']
        if isinstance(raw_windows, (tuple, list)):
            thawed = []
            for w in raw_windows:
                if isinstance(w, dict):
                    thawed.append(dict(w))
                elif isinstance(w, (tuple, list)):
                    thawed.append(dict(w))
                else:
                    thawed.append(w)
            s_dict['ac_heating_windows'] = thawed
    return s_dict
