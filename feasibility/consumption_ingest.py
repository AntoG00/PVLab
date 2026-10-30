"""Strict, side-effect-free measured consumption CSV ingestion.

Callers supply every format/clock choice through IngestOptions; there are no
vendor presets or header guesses. Binary streams must be UTF-8 (optional BOM).
Text streams are hashed as UTF-8, without newline normalization by this parser.
Delivery periods are 1-based elapsed quarter-hours from local midnight, including
both autumn folds. Fixed-clock sources instead use UTC+1 throughout the year.
Coverage is half-open UTC; omitted bounds are inferred, never filled. Results
are preview-only: monthly projection readiness belongs to a later compiler.
"""
from __future__ import annotations

import codecs
import csv
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
import hashlib
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from zoneinfo import ZoneInfo

PARSER_VERSION = "consumption-csv-v1"
UTC = timezone.utc
_LIMITS = {"max_bytes": 16 * 1024 * 1024, "max_rows": 250_000,
           "max_columns": 64, "max_field_bytes": 4096}
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@dataclass(frozen=True)
class IngestOptions:
    delimiter: str
    decimal_separator: str
    columns: Mapping[str, str]
    unit: str
    duration_seconds: int
    timezone: str
    timestamp_convention: str
    demand_basis: str
    adapter: str = "timestamp"
    fixed_clock: bool = False
    existing_pv: bool = False
    existing_battery: bool = False
    timestamp_format: str | None = None
    date_format: str = "%Y-%m-%d"
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    max_bytes: int = _LIMITS["max_bytes"]
    max_rows: int = _LIMITS["max_rows"]
    max_columns: int = _LIMITS["max_columns"]
    max_field_bytes: int = _LIMITS["max_field_bytes"]


@dataclass(frozen=True)
class Interval:
    start_utc: datetime
    duration_seconds: int
    energy_kwh: float


@dataclass(frozen=True)
class IngestDiagnostic:
    code: str
    count: int


@dataclass(frozen=True)
class ValidatedIntervals:
    intervals: tuple[Interval, ...]
    total_energy_kwh: float
    coverage_start: datetime
    coverage_end: datetime
    coverage_declared: bool
    anomalies: tuple[IngestDiagnostic, ...]
    source_digest: str
    summary_counts: Mapping[str, int]
    options: IngestOptions
    ready_for_projection: bool = False
    parser_version: str = PARSER_VERSION


class ConsumptionIngestError(ValueError):
    """One bounded, fail-fast diagnostic, containing no source text or paths."""

    def __init__(self, code, row=None, field=None, rows_seen=0):
        self.code, self.row, self.field = code, row, field
        self.summary_counts = MappingProxyType({"rows_seen": rows_seen, "errors": 1})
        super().__init__(code)


def _options(options):
    if not isinstance(options, IngestOptions):
        raise ConsumptionIngestError("invalid_options")
    o = options
    if (not isinstance(o.delimiter, str) or len(o.delimiter) != 1 or
            o.delimiter in '\r\n"\x00' or o.decimal_separator not in (".", ",") or
            o.delimiter == o.decimal_separator or o.timezone != "Europe/Rome" or
            o.adapter not in ("timestamp", "distributor_period") or
            type(o.duration_seconds) is not int or o.duration_seconds not in (900, 3600) or
            o.timestamp_convention not in ("start", "end") or
            o.unit not in ("kWh", "Wh", "average_kW") or
            any(type(x) is not bool for x in (o.fixed_clock, o.existing_pv, o.existing_battery)) or
            (o.timestamp_format is not None and
             (not isinstance(o.timestamp_format, str) or not o.timestamp_format)) or
            not isinstance(o.date_format, str) or not o.date_format):
        raise ConsumptionIngestError("invalid_options")
    if o.demand_basis not in ("gross", "grid_import", "reconciled_gross"):
        raise ConsumptionIngestError("unsupported_demand_basis")
    if o.demand_basis == "grid_import" and (o.existing_pv or o.existing_battery):
        raise ConsumptionIngestError("unsupported_demand_basis")
    required = {"timestamp", "value"} if o.adapter == "timestamp" else {"date", "period", "value"}
    allowed = required | ({"fold"} if o.adapter == "timestamp" else set())
    if (not isinstance(o.columns, Mapping) or not required <= o.columns.keys() or
            not o.columns.keys() <= allowed or
            any(not isinstance(v, str) or not v or len(v.encode("utf-8")) > 4096
                for v in o.columns.values()) or
            len(set(o.columns.values())) != len(o.columns)):
        raise ConsumptionIngestError("mapping_required")
    if o.adapter == "distributor_period" and o.duration_seconds != 900:
        raise ConsumptionIngestError("invalid_options")
    for name, maximum in _LIMITS.items():
        if type(getattr(o, name)) is not int or not 1 <= getattr(o, name) <= maximum:
            raise ConsumptionIngestError("invalid_options", field=name)
    return replace(o, columns=MappingProxyType(dict(o.columns)))


def _utc_bound(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ConsumptionIngestError("coverage_mismatch")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ConsumptionIngestError("coverage_mismatch") from None


def _resolve_clock(value, zone, fold, fail):
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    candidates = {}
    for f in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=f).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates[candidate] = f
    if not candidates:
        fail("nonexistent_time", "timestamp")
    if len(candidates) == 2:
        if fold is None:
            fail("ambiguous_time", "timestamp")
        return value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
    return next(iter(candidates))


def _lines(stream, o, digest, fail):
    """Read bounded chunks, keeping at most one bounded physical CSV line."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    pending, total, stream_type = "", 0, None
    # Quotes may double on disk; a valid row can contain up to 64 full fields.
    line_limit = o.max_columns * (2 * o.max_field_bytes + 3) + 2
    while True:
        chunk = stream.read(4096)
        if not isinstance(chunk, (str, bytes)):
            fail("invalid_encoding")
        if stream_type is None:
            stream_type = type(chunk)
        if type(chunk) is not stream_type:
            fail("invalid_encoding")
        raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
        total += len(raw)
        if total > o.max_bytes:
            fail("limits", "body")
        digest.update(raw)
        pending += decoder.decode(raw, final=not raw)
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            if len(line.encode("utf-8")) > line_limit:
                fail("limits", "row")
            yield line + "\n"
        if len(pending.encode("utf-8")) > line_limit:
            fail("limits", "row")
        if not raw:
            if pending:
                yield pending
            return


def parse_consumption(stream, options) -> ValidatedIntervals:
    """Validate measured intervals; never interpolate, project, fetch, or write.

    Units are per-interval energy, except explicitly interval-average kW.
    Cumulative meters have no accepted unit/basis. Explicit UTC coverage bounds,
    when supplied, must both match the entire contiguous measured range exactly.
    Errors stop at the first failure, so diagnostics stay bounded for bad uploads.
    """
    o = _options(options)
    declared = o.coverage_start is not None or o.coverage_end is not None
    bounds = None
    if declared:
        bounds = (_utc_bound(o.coverage_start), _utc_bound(o.coverage_end))
        if bounds[0] >= bounds[1]:
            raise ConsumptionIngestError("coverage_mismatch")
    zone = timezone(timedelta(hours=1)) if o.fixed_clock else ZoneInfo(o.timezone)
    row_number, rows_seen = None, 0

    def fail(code, field=None):
        raise ConsumptionIngestError(code, row_number, field, rows_seen) from None

    digest, intervals, anomalies = hashlib.sha256(), [], []
    delta = timedelta(seconds=o.duration_seconds)
    reader = csv.reader(_lines(stream, o, digest, fail), delimiter=o.delimiter, strict=True)
    try:
        header = next(reader, None)
        row_number = reader.line_num or None
        if not header:
            fail("mapping_required")

        def check_fields(row):
            if len(row) > o.max_columns:
                fail("limits", "columns")
            if any(len(v.encode("utf-8")) > o.max_field_bytes for v in row):
                fail("limits", "field")
            if any("\x00" in v for v in row):
                fail("invalid_csv")

        check_fields(header)
        if len(set(header)) != len(header) or any(v not in header for v in o.columns.values()):
            fail("mapping_required")
        columns = {key: header.index(value) for key, value in o.columns.items()}
        for row in reader:
            row_number = reader.line_num
            rows_seen += 1
            if rows_seen > o.max_rows:
                fail("limits", "rows")
            check_fields(row)
            if len(row) != len(header):
                fail("invalid_csv")
            fields = {key: row[index].strip() for key, index in columns.items()}
            value = fields["value"]
            if o.decimal_separator == ",":
                if "." in value:
                    fail("invalid_value", "value")
                value = value.replace(",", ".")
            if not _NUMBER.fullmatch(value):
                fail("invalid_value", "value")
            energy = float(value)
            if not math.isfinite(energy) or energy < 0:
                fail("invalid_value", "value")
            if o.unit == "Wh":
                energy /= 1000
            elif o.unit == "average_kW":
                energy *= o.duration_seconds / 3600
            if o.adapter == "distributor_period":
                date = datetime.strptime(fields["date"], o.date_format).date()
                midnight = datetime.combine(date, time(), zone).astimezone(UTC)
                next_midnight = datetime.combine(date + timedelta(days=1), time(), zone).astimezone(UTC)
                expected = int((next_midnight - midnight).total_seconds()) // 900
                period = fields["period"]
                if not re.fullmatch(r"[0-9]{1,3}", period) or not 1 <= int(period) <= expected:
                    fail("invalid_value", "period")
                start = midnight + (int(period) - 1) * delta
            else:
                stamp = fields["timestamp"]
                if o.timestamp_format is None:
                    # fromisoformat accepts bare dates; these are not measured timestamps.
                    if "T" not in stamp and " " not in stamp:
                        fail("invalid_timestamp", "timestamp")
                    value_dt = datetime.fromisoformat(stamp)
                else:
                    value_dt = datetime.strptime(stamp, o.timestamp_format)
                fold = fields.get("fold")
                if fold is not None and fold not in ("0", "1"):
                    fail("invalid_value", "fold")
                start = _resolve_clock(value_dt, zone, None if fold is None else int(fold), fail)
                if o.fixed_clock and value_dt.tzinfo is not None and value_dt.utcoffset() != timedelta(hours=1):
                    fail("invalid_timestamp", "timestamp")
                if o.timestamp_convention == "end":
                    start -= delta
            # Check the exclusive endpoint is representable before publishing.
            _ = start + delta
            intervals.append((Interval(start, o.duration_seconds, energy), row_number))
    except ConsumptionIngestError:
        raise
    except UnicodeError:
        fail("invalid_encoding")
    except csv.Error as exc:
        # csv's own field-size bound can be reached before our stricter check.
        fail("limits" if "field larger" in str(exc) else "invalid_csv")
    except (ValueError, OverflowError):
        fail("invalid_timestamp", "timestamp")
    except (OSError, TypeError, AttributeError):
        fail("stream_error")
    if not intervals:
        fail("empty_data")
    ordered = sorted(intervals, key=lambda item: item[0].start_utc)
    moved = sum(a != b for a, b in zip(intervals, ordered))
    if moved:
        anomalies.append(IngestDiagnostic("sorted", moved))
    if o.fixed_clock:
        anomalies.append(IngestDiagnostic("fixed_clock", len(ordered)))
    for (previous, _), (current, row_number) in zip(ordered, ordered[1:]):
        difference = current.start_utc - previous.start_utc
        if not difference:
            fail("duplicate", "timestamp")
        if difference < delta:
            fail("overlap", "timestamp")
        if difference > delta:
            fail("gap", "timestamp")
    measured = (ordered[0][0].start_utc, ordered[-1][0].start_utc + delta)
    if bounds is not None and measured != bounds:
        fail("coverage_mismatch")
    result = tuple(item[0] for item in ordered)
    try:
        total = math.fsum(item.energy_kwh for item in result)
    except OverflowError:
        fail("invalid_value", "total_energy_kwh")
    return ValidatedIntervals(result, total, *measured, declared, tuple(anomalies),
                              digest.hexdigest(), MappingProxyType({"rows_seen": rows_seen,
                              "intervals": len(result), "errors": 0}), o)
