"""Validate operator-supplied GME archives; build private, immutable price catalogs.

No scraping, redistribution, active-engine import or household state is involved.
Coverage and windows are half-open civil dates in Europe/Rome. Canonical intervals
retain UTC boundaries; month/hour statistics use fixed UTC+1 standard time.
"""
import argparse
import calendar
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfo

from defusedxml.ElementTree import iterparse
from defusedxml.common import DefusedXmlException
import numpy as np

SERIES = ('NORD', 'CNOR', 'CSUD', 'SUD', 'CALA', 'SICI', 'SARD', 'PUN')
SOURCE_SCHEMA = 'gme-newdataset-v1'
# Explicit source contract. NAT must never be used as a substitute for PUN.
SOURCE_SCHEMAS = {
    SOURCE_SCHEMA: {'root': 'NewDataSet', 'hourly': ('Prezzi', 'Ora', 3600),
                    'quarter': ('Prezzi15', 'Periodo', 900),
                    'prices': {series: series for series in SERIES}},
}
PARSER_VERSION = 'gme-intervals-v1'
ROME = ZoneInfo('Europe/Rome')
STANDARD = timezone(timedelta(hours=1))
MAX_MEMBERS = 2000
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_RATIO = 100
MAX_RECORDS = 200_000  # XML rows per archive; each row has all eight series.
MAX_XML_NODES = 20_000  # Per daily member, including optional inline schema.


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def civil_date(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Expected civil date YYYY-MM-DD')
    return date.fromisoformat(value)


def coverage_dates(start, end):
    start, end = civil_date(start), civil_date(end)
    if start >= end:
        raise ValueError('Coverage must be a nonempty half-open date range')
    return [start + timedelta(days=i) for i in range((end - start).days)]


def day_bounds(day):
    day = civil_date(day)
    following = day + timedelta(days=1)
    return (datetime.combine(day, datetime.min.time(), ROME).astimezone(timezone.utc),
            datetime.combine(following, datetime.min.time(), ROME).astimezone(timezone.utc))


def _supported(message):
    return ValueError(f'{message}; supported source formats: {", ".join(SOURCE_SCHEMAS)}')


def _read_day(stream, name, schema, budget):
    """Stream one day without entity expansion or retaining the complete XML tree."""
    quarter = name.endswith('MGPPrezzi15.xml')
    row_tag, period_tag, duration = schema['quarter' if quarter else 'hourly']
    rows, raw_day, stack = [], None, []
    nodes = 0
    try:
        for event, element in iterparse(stream, events=('start', 'end'),
                                        forbid_dtd=True, forbid_entities=True,
                                        forbid_external=True):
            if event == 'start':
                nodes += 1
                if nodes > MAX_XML_NODES:
                    raise ValueError('XML node limit exceeded')
                stack.append(element.tag)
                if len(stack) == 1 and element.tag != schema['root']:
                    raise _supported('Unknown XML root')
                in_schema = len(stack) >= 2 and stack[1] == '{http://www.w3.org/2001/XMLSchema}schema'
                if len(stack) > (32 if in_schema else 3):
                    raise _supported('Nested XML structure')
                continue
            if len(stack) > 2 and stack[1] == '{http://www.w3.org/2001/XMLSchema}schema':
                element.clear()
            if len(stack) == 2:
                if element.tag != row_tag:
                    # GME NewDataSet may carry its inline XSD declaration.
                    if element.tag != '{http://www.w3.org/2001/XMLSchema}schema':
                        raise _supported('Unknown GME row')
                else:
                    budget[0] += 1
                    if budget[0] > MAX_RECORDS or len(rows) >= 100:
                        raise ValueError('XML record limit exceeded')
                    fields = {}
                    for child in element:
                        if child.tag in fields or len(child):
                            raise _supported('Duplicate or nested row field')
                        fields[child.tag] = child.text
                    stamp = fields.get('Data')
                    if not isinstance(stamp, str) or not re.fullmatch(r'\d{8}', stamp):
                        raise ValueError('Missing or invalid Data')
                    parsed_day = datetime.strptime(stamp, '%Y%m%d').date()
                    if raw_day is not None and parsed_day != raw_day:
                        raise ValueError('Inconsistent Data in daily XML')
                    raw_day = parsed_day
                    if fields.get('Mercato') != 'MGP':
                        raise ValueError('Missing or invalid Mercato (expected MGP)')
                    if fields.get(period_tag) != str(len(rows) + 1):
                        raise ValueError(f'{period_tag} must be sequential 1..N')
                    values = {}
                    for series, source_tag in schema['prices'].items():
                        text = fields.get(source_tag)
                        if not isinstance(text, str) or not text.strip():
                            raise _supported(f'Missing {source_tag} price')
                        try:
                            value = float(text.replace(',', '.'))
                        except ValueError as exc:
                            raise ValueError(f'Invalid {source_tag} price') from exc
                        if not math.isfinite(value):
                            raise ValueError(f'Non-finite {source_tag} price')
                        values[series] = value
                    if 'NAT' in fields:
                        try:
                            if not math.isfinite(float(fields['NAT'].replace(',', '.'))):
                                raise ValueError('Non-finite NAT price')
                        except (TypeError, AttributeError) as exc:
                            raise ValueError('Invalid NAT price') from exc
                    rows.append(values)
                element.clear()
            stack.pop()
    except (DefusedXmlException, zipfile.BadZipFile, ParseError) as exc:
        raise ValueError('Unsafe XML or damaged ZIP member') from exc
    if not rows:
        raise _supported('No price rows')
    begin, finish = day_bounds(raw_day)
    if len(rows) != int((finish - begin).total_seconds()) // duration:
        raise ValueError(f'Wrong daily period count for {raw_day}')
    # A delivery date in the conventional member name is also a source assertion.
    if Path(name).name != raw_day.strftime('%Y%m%d') + ('MGPPrezzi15.xml' if quarter else 'MGPPrezzi.xml'):
        raise ValueError('Member date does not match Data')
    return raw_day.isoformat(), duration, rows


def _archive_members(archive):
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ValueError('ZIP member count limit exceeded')
    seen, total = set(), 0
    for item in members:
        name, parts = item.filename, PurePosixPath(item.filename).parts
        mode = item.external_attr >> 16
        if (not name or '\\' in name or '\x00' in name or ':' in name
                or name.startswith('/') or any(p in {'.', '..'} for p in name.split('/'))
                or name in seen or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
            raise ValueError('Unsafe ZIP member name or type')
        seen.add(name)
        if item.is_dir():
            if item.file_size:
                raise ValueError('ZIP directories must be empty')
            continue
        total += item.file_size
        if (item.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES
                or item.file_size > MAX_RATIO * max(item.compress_size, 1)):
            raise ValueError('ZIP size or compression ratio limit exceeded')
        if item.flag_bits & 1:
            raise ValueError('Encrypted ZIP members unsupported')
        if not parts or not (name.endswith('MGPPrezzi.xml') or name.endswith('MGPPrezzi15.xml')):
            raise _supported('Unexpected or nested archive member')
    return [item for item in members if not item.is_dir()]


def ingest_mgp_data(input_dir, start, end, *, source_schema=SOURCE_SCHEMA):
    """Parse all eight series, strictly covering [start,end); no fallback prices."""
    if source_schema not in SOURCE_SCHEMAS:
        raise _supported('Unknown source schema/version')
    requested = coverage_dates(start, end)
    input_dir = Path(input_dir)
    archives = sorted(input_dir.glob('*.zip'))
    if not archives:
        raise ValueError('No supplied ZIP archives')
    parsed, hashes = {}, set()
    for path in archives:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Archive must be a regular file')
        archive_hash = sha256_file(path)
        if archive_hash in hashes:
            continue
        hashes.add(archive_hash)
        with zipfile.ZipFile(path) as archive:
            budget = [0]
            for member in _archive_members(archive):
                with archive.open(member) as stream:
                    day, duration, values = _read_day(stream, member.filename,
                                                      SOURCE_SCHEMAS[source_schema], budget)
                key = (day, duration)
                if key in parsed and parsed[key]['values'] != values:
                    raise ValueError(f'Conflicting day {day} within supplied archives')
                prior = parsed.setdefault(key, {'values': values, 'sources': set()})
                prior['sources'].add(archive_hash)
        if sha256_file(path) != archive_hash:
            raise ValueError('Source archive changed during ingestion')
    days = {}
    for day in requested:
        stamp = day.isoformat()
        hourly, quarters = parsed.get((stamp, 3600)), parsed.get((stamp, 900))
        if hourly is None and quarters is None:
            raise ValueError(f'Missing delivery date {stamp}')
        max_diff, checked = 0.0, 0
        if hourly and quarters:
            for i, values in enumerate(hourly['values']):
                for series in SERIES:
                    mean = math.fsum(v[series] / 4 for v in quarters['values'][i * 4:i * 4 + 4])
                    max_diff = max(max_diff, abs(mean - values[series]))
                    checked += 1
            if max_diff > 1e-4:
                raise ValueError(f'Quarter/hour overlap mismatch on {stamp}')
        chosen, duration = (quarters, 900) if quarters else (hourly, 3600)
        begin, _ = day_bounds(day)
        records = [[series, (begin + timedelta(seconds=i * duration)).isoformat(),
                    duration, row[series] / 1000.0]
                   for i, row in enumerate(chosen['values']) for series in SERIES]
        sources = sorted(set().union(*(v['sources'] for v in (hourly, quarters) if v)))
        days[stamp] = {'date': stamp, 'records': records, 'source_hashes': sources,
                       'source_schema': source_schema, 'parser_version': PARSER_VERSION,
                       'overlap': {'compared_series_hours': checked,
                                   'max_abs_diff_eur_mwh': max_diff,
                                   'selected_resolution_seconds': duration}}
    return {'days': days, 'archive_hashes': sorted(hashes), 'source_schema': source_schema,
            'parser_version': PARSER_VERSION, 'coverage': [requested[0].isoformat(), civil_date(end).isoformat()]}


def compute_empirical_window(days, start, end, *, series, require_climatology=False):
    """Duration-weighted actual prices/counts, plus an explicitly available shape."""
    if series not in SERIES:
        raise ValueError('Unknown price series')
    requested = coverage_dates(start, end)
    sums, counts, observations = np.zeros((12, 24)), np.zeros((12, 24)), np.zeros((12, 24), dtype=int)
    values, durations = [], []
    for day in requested:
        if day.isoformat() not in days:
            raise ValueError(f'Missing delivery date {day}')
        rows = [r for r in days[day.isoformat()]['records'] if r[0] == series]
        begin, finish = day_bounds(day)
        cursor = begin
        for _, stamp, duration, value in rows:
            dt = datetime.fromisoformat(stamp)
            if (dt.utcoffset() != timedelta(0) or dt != cursor or type(duration) is not int
                    or duration not in (900, 3600) or not math.isfinite(value)):
                raise ValueError('Invalid canonical price timeline')
            cursor += timedelta(seconds=duration)
            standard = dt.astimezone(STANDARD)
            index = (standard.month - 1, standard.hour)
            hours = duration / 3600.0
            sums[index] += value * hours
            counts[index] += hours
            observations[index] += 1
            values.append(value)
            durations.append(hours)
        if cursor != finish:
            raise ValueError(f'Incomplete series {series} on {day}')
    matrix = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    mean = float(np.average(values, weights=durations))
    a, b = civil_date(start), civil_date(end)
    climatology = a.day == b.day == 1 and (b.year - a.year) * 12 + b.month - a.month == 12
    if require_climatology and not climatology:
        raise ValueError('Climatology requires twelve complete calendar months')
    reason = None
    shape = None
    if mean <= 0:
        reason = 'Nonpositive actual mean; normalized shape unavailable'
    elif not climatology:
        reason = 'Window must span twelve complete calendar months'
    elif not np.all(counts > 0):
        raise ValueError('Incomplete climatology cells')
    else:
        shape = matrix / mean
    if not math.isfinite(mean) or not np.all(np.isfinite(matrix)) or (shape is not None and not np.all(np.isfinite(shape))):
        raise ValueError('Non-finite aggregate price product')
    begin, finish = day_bounds(a)[0], day_bounds(b)[0]
    return {'series': series, 'window': [a.isoformat(), b.isoformat()],
            'dates_count': len(requested), 'total_hours': float(sum(durations)),
            'mean_eur_kwh': mean, 'min_eur_kwh': min(values), 'max_eur_kwh': max(values),
            'zero_negative_count': sum(v <= 0 for v in values),
            'matrix_eur_kwh': matrix, 'counts_hours': counts, 'observation_counts': observations,
            'normalized_shape': shape, 'shape_unavailable_reason': reason,
            'boundaries_utc': [begin.isoformat(), finish.isoformat()],
            'boundaries_standard': [begin.astimezone(STANDARD).isoformat(), finish.astimezone(STANDARD).isoformat()]}


def expand_pzo_proxy(normalized_shape_12x24, engine_month, engine_hour, engine_valid):
    """Expand against a caller-supplied validated calendar, normalizing each year."""
    shape = np.asarray(normalized_shape_12x24)
    month, hour, valid = map(np.asarray, (engine_month, engine_hour, engine_valid))
    if (shape.shape != (12, 24) or not np.all(np.isfinite(shape)) or month.ndim != 2
            or month.shape != hour.shape or month.shape != valid.shape or valid.dtype != np.bool_
            or not np.issubdtype(month.dtype, np.integer) or not np.issubdtype(hour.dtype, np.integer)
            or not np.all((month[valid] >= 1) & (month[valid] <= 12))
            or not np.all((hour[valid] >= 0) & (hour[valid] < 24))):
        raise ValueError('Invalid shape or supplied engine calendar')
    result = np.zeros(hour.shape, dtype=float)
    for index in range(len(month)):
        mask = valid[index]
        n = int(mask.sum())
        if n not in (8760, 8784) or not np.array_equal(mask, np.arange(mask.size) < n):
            raise ValueError('Engine calendar requires complete years and trailing padding')
        h = hour[index, mask]
        m = month[index, mask]
        if not np.all(h[1:] == (h[:-1] + 1) % 24):
            raise ValueError('Engine calendar hours must be sequential')
        expected = [calendar.monthrange(2000 if n == 8784 else 2001, k)[1] * 24 for k in range(1, 13)]
        transitions = m[1:] != m[:-1]
        if (not np.array_equal(np.bincount(m, minlength=13)[1:], expected)
                or not np.all(h[1:][transitions] == 0)
                or not np.all(m[1:][transitions] == m[:-1][transitions] % 12 + 1)):
            raise ValueError('Engine calendar months must describe a complete Gregorian year')
        raw = shape[m - 1, h]
        mean = float(np.mean(raw))
        if not math.isfinite(mean) or mean <= 0:
            raise ValueError('Nonpositive calendar shape mean')
        result[index, mask] = raw / mean
        if not np.all(np.isfinite(result[index, mask])):
            raise ValueError('Non-finite expanded shape')
    return result


def prepare_market_artifacts(input_dir, catalog_dir, start, end, *, publish=False,
                             replace=False, source_schema=SOURCE_SCHEMA,
                             window_start=None, window_end=None):
    from .market_catalog import update_catalog
    ingest = ingest_mgp_data(input_dir, start, end, source_schema=source_schema)
    return update_catalog(catalog_dir, ingest, publish=publish, replace=replace,
                          window_start=window_start, window_end=window_end)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--source-schema', default=SOURCE_SCHEMA, choices=tuple(SOURCE_SCHEMAS))
    parser.add_argument('--window-start')
    parser.add_argument('--window-end')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--replace', action='store_true', help='Explicitly correct existing delivery days in a new revision')
    args = parser.parse_args(argv)
    try:
        result = prepare_market_artifacts(args.input, args.catalog, args.start, args.end,
                                          publish=args.publish, replace=args.replace,
                                          source_schema=args.source_schema,
                                          window_start=args.window_start, window_end=args.window_end)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
