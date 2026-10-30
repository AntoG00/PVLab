"""Canonical roof validation and whole-panel search capacities."""
from collections.abc import Mapping
from typing import Any

from .profiles import number


def maximum_panel_count(max_kwp, panel_watts):
    """Whole-panel capacity with a four-ULP exact-boundary tolerance.

    Only a quotient within four floating-point ULPs of its nearest integer
    is snapped to that integer (and at most 1e-9 of a panel). This covers
    binary conversion at an exact decimal panel boundary, never a partial
    panel or a user-requested increase in roof capacity.
    """
    import math
    number(max_kwp, 'max_kwp', low=0)
    number(panel_watts, 'panel_watts', positive=True)
    quotient = float(max_kwp) * 1000.0 / float(panel_watts)
    if not math.isfinite(quotient):
        raise ValueError('roof capacity causes numeric overflow')
    nearest = round(quotient)
    if abs(quotient - nearest) <= min(4 * math.ulp(quotient), 1e-9):
        quotient = nearest
    return math.floor(quotient)


def validate_roof_counts(
    profile: Any,
    roof_panels: Any,
    panel_watts: float | int,
) -> tuple[tuple[str, int], ...]:
    """Validate and normalize roof panel counts against profile roofs and panel wattage.

    Args:
        profile: HouseholdProfileV1 instance, or any object/dict containing a `roofs` sequence.
        roof_panels: Mapping JSON object of {roof_id: panel_count}. Missing IDs count as 0.
        panel_watts: Wattage rating per solar panel in Watts (positive finite number).

    Returns:
        Manifest-ordered tuple of (roof_id, panel_count) for all roofs in the profile.

    Raises:
        ValueError: On unknown roof IDs, non-Mapping roof_panels, bool/non-integer/negative counts,
            huge int numeric overflow, non-finite/non-positive panel_watts,
            or overcapacity beyond the documented exact-boundary rounding tolerance.
    """
    number(panel_watts, 'panel_watts', positive=True)
    panel_watts_val = float(panel_watts)

    if hasattr(profile, 'roofs'):
        roofs = profile.roofs
    elif isinstance(profile, Mapping) and 'roofs' in profile:
        roofs = profile['roofs']
    else:
        raise ValueError('profile must provide a roofs attribute or key')

    if not isinstance(roofs, (list, tuple)):
        raise ValueError('profile.roofs must be a sequence of roofs')

    if len(roofs) > 12:
        raise ValueError('profile.roofs: max 12 roofs allowed')

    roof_entries: list[tuple[str, float]] = []
    seen_ids: set[str] = set()

    for r in roofs:
        if hasattr(r, 'id') and hasattr(r, 'max_kwp'):
            r_id = r.id
            max_kwp = r.max_kwp
        elif isinstance(r, Mapping) and 'id' in r and 'max_kwp' in r:
            r_id = r['id']
            max_kwp = r['max_kwp']
        else:
            raise ValueError(f'Invalid roof entry: {r!r}')

        if type(r_id) is not str or not r_id:
            raise ValueError(f'Roof ID must be a non-empty string, got {r_id!r}')
        if r_id in seen_ids:
            raise ValueError(f'Duplicate roof ID in profile: {r_id!r}')
        seen_ids.add(r_id)

        number(max_kwp, f'roof {r_id} max_kwp', low=0)
        roof_entries.append((r_id, float(max_kwp)))

    if not isinstance(roof_panels, Mapping):
        raise ValueError('roof_panels must be a Mapping JSON object')
    if any(type(k) is not str for k in roof_panels):
        raise ValueError('roof_panels keys must be strings')

    for roof_id, count in roof_panels.items():
        if roof_id not in seen_ids:
            raise ValueError(f'Unknown roof ID: {roof_id!r}')
        number(count, f'roof {roof_id} panel count', low=0, integer=True)

    manifest: list[tuple[str, int]] = []
    for r_id, max_kwp in roof_entries:
        count = roof_panels.get(r_id, 0)
        try:
            if count > maximum_panel_count(max_kwp, panel_watts_val):
                raise ValueError(
                    f'Roof {r_id!r} capacity exceeds max_kwp {max_kwp}'
                )
        except OverflowError:
            raise ValueError(f'Roof {r_id!r} panel count causes numeric overflow')
        manifest.append((r_id, count))

    return tuple(manifest)
