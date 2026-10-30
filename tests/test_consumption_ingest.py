# tests/test_consumption_ingest.py
from collections.abc import Mapping
from datetime import datetime, timezone, timedelta
import hashlib
import io
from pathlib import Path
import pytest

from feasibility.consumption_ingest import (
    IngestOptions,
    ConsumptionIngestError,
    parse_consumption,
)


def make_opts(**overrides):
    defaults = dict(
        adapter="timestamp",
        delimiter=",",
        decimal_separator=".",
        columns={"timestamp": "ts", "value": "val"},
        unit="kWh",
        duration_seconds=3600,
        timezone="Europe/Rome",
        timestamp_convention="start",
        demand_basis="gross",
    )
    defaults.update(overrides)
    return IngestOptions(**defaults)


def make_distributor_csv(date_str, periods, val_str="0,250", drop_period=None, dup_period=None):
    rows = ["DATA;PERIODO;VALORE"]
    for p in range(1, periods + 1):
        if p == drop_period:
            continue
        rows.append(f"{date_str};{p};{val_str}")
        if p == dup_period:
            rows.append(f"{date_str};{p};{val_str}")
    return "\n".join(rows)


# --- Distributor Period Adapter (Italian 92/96/100 DST & Standard) ---

@pytest.mark.parametrize(
    "date_str,periods,expected_kwh,first_utc,last_utc",
    [
        ("2024-03-31", 92, 23.0, datetime(2024, 3, 30, 23, 0, tzinfo=timezone.utc), datetime(2024, 3, 31, 21, 45, tzinfo=timezone.utc)),
        ("2024-01-15", 96, 24.0, datetime(2024, 1, 14, 23, 0, tzinfo=timezone.utc), datetime(2024, 1, 15, 22, 45, tzinfo=timezone.utc)),
        ("2024-10-27", 100, 25.0, datetime(2024, 10, 26, 22, 0, tzinfo=timezone.utc), datetime(2024, 10, 27, 22, 45, tzinfo=timezone.utc)),
    ],
)
def test_distributor_period_dst_and_standard_cases(date_str, periods, expected_kwh, first_utc, last_utc):
    opts = make_opts(
        adapter="distributor_period",
        delimiter=";",
        decimal_separator=",",
        columns={"date": "DATA", "period": "PERIODO", "value": "VALORE"},
        duration_seconds=900,
    )
    csv_data = make_distributor_csv(date_str, periods, "0,250")
    res = parse_consumption(io.StringIO(csv_data), opts)
    assert len(res.intervals) == periods
    assert res.total_energy_kwh == pytest.approx(expected_kwh)
    assert res.intervals[0].start_utc == first_utc
    assert res.intervals[-1].start_utc == last_utc
    assert res.coverage_start == first_utc
    assert res.coverage_end == last_utc + timedelta(seconds=900)
    assert res.ready_for_projection is False
    assert isinstance(res.source_digest, str) and len(res.source_digest) == 64
    assert isinstance(res.summary_counts, Mapping)


# --- Parametrized Error Cases ---

@pytest.mark.parametrize(
    "case,bad_input,opts_override,expected_code",
    [
        ("gap_period", make_distributor_csv("2024-01-15", 96, drop_period=50), {"adapter": "distributor_period", "delimiter": ";", "decimal_separator": ",", "columns": {"date": "DATA", "period": "PERIODO", "value": "VALORE"}, "duration_seconds": 900}, "gap"),
        ("dup_period", make_distributor_csv("2024-01-15", 96, dup_period=10), {"adapter": "distributor_period", "delimiter": ";", "decimal_separator": ",", "columns": {"date": "DATA", "period": "PERIODO", "value": "VALORE"}, "duration_seconds": 900}, "duplicate"),
        ("nonexistent_dst", "ts,val\n2024-03-31 02:30:00,1.0\n", {}, "nonexistent_time"),
        ("ambiguous_dst", "ts,val\n2024-10-27 02:30:00,1.0\n", {}, "ambiguous_time"),
        ("gap_ts", "ts,val\n2024-01-01 00:00:00,1.0\n2024-01-01 02:00:00,1.0\n", {}, "gap"),
        ("duplicate_ts", "ts,val\n2024-01-01 00:00:00,1.0\n2024-01-01 00:00:00,1.0\n", {}, "duplicate"),
        ("negative_val", "ts,val\n2024-01-01 00:00:00,-1.0\n", {}, "invalid_value"),
        ("nan_val", "ts,val\n2024-01-01 00:00:00,NaN\n", {}, "invalid_value"),
        ("limit_bytes", "ts,val\n2024-01-01 00:00:00,1.0\n2024-01-01 01:00:00,1.0\n", {"max_bytes": 10}, "limits"),
        ("limit_rows", "ts,val\n2024-01-01 00:00:00,1.0\n2024-01-01 01:00:00,1.0\n", {"max_rows": 1}, "limits"),
        ("limit_cols", "ts,val,extra\n2024-01-01 00:00:00,1.0,x\n", {"max_columns": 2}, "limits"),
        ("limit_field", "ts,val\n2024-01-01 00:00:00," + "9" * 100 + "\n", {"max_field_bytes": 10}, "limits"),
        ("missing_col", "wrong_ts,val\n2024-01-01 00:00:00,1.0\n", {}, "mapping_required"),
        ("import_with_pv", "ts,val\n2024-01-01 00:00:00,1.0\n", {"demand_basis": "grid_import", "existing_pv": True}, "unsupported_demand_basis"),
        ("import_with_battery", "ts,val\n2024-01-01 00:00:00,1.0\n", {"demand_basis": "grid_import", "existing_battery": True}, "unsupported_demand_basis"),
        ("coverage_mismatch", "ts,val\n2024-02-01 00:00:00,1.0\n", {"coverage_start": datetime(2024, 1, 1, tzinfo=timezone.utc)}, "coverage_mismatch"),
    ],
)
def test_consumption_ingest_errors(case, bad_input, opts_override, expected_code):
    opts = make_opts(**opts_override)
    with pytest.raises(ConsumptionIngestError) as exc_info:
        parse_consumption(io.StringIO(bad_input), opts)
    assert exc_info.value.code == expected_code
    assert hasattr(exc_info.value, "row")
    assert hasattr(exc_info.value, "field")
    assert isinstance(exc_info.value.summary_counts, Mapping)


# --- Unit Conversions & Zeros ---

@pytest.mark.parametrize(
    "unit,dur,val_str,expected_kwh",
    [
        ("kWh", 900, "2.5", 2.5),
        ("Wh", 900, "2500", 2.5),
        ("average_kW", 900, "10.0", 2.5),
        ("average_kW", 3600, "2.5", 2.5),
    ],
)
def test_unit_conversions(unit, dur, val_str, expected_kwh):
    opts = make_opts(unit=unit, duration_seconds=dur)
    res = parse_consumption(io.StringIO(f"ts,val\n2024-01-01 00:00:00,{val_str}\n"), opts)
    assert res.total_energy_kwh == pytest.approx(expected_kwh)


def test_zero_consumption():
    opts = make_opts()
    res = parse_consumption(io.StringIO("ts,val\n2024-01-01 00:00:00,0.0\n"), opts)
    assert res.intervals[0].energy_kwh == 0.0
    assert res.total_energy_kwh == 0.0


# --- Conventions, DST Folds, Fixed Clock ---

@pytest.mark.parametrize(
    "conv,ts,expected_utc",
    [
        ("start", "2024-01-01 01:00:00", datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)),
        ("end", "2024-01-01 01:00:00", datetime(2023, 12, 31, 23, 0, tzinfo=timezone.utc)),
        ("end", "2024-01-02 00:00:00", datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)),
    ],
)
def test_timestamp_conventions_and_midnight(conv, ts, expected_utc):
    opts = make_opts(timestamp_convention=conv)
    res = parse_consumption(io.StringIO(f"ts,val\n{ts},1.0\n"), opts)
    assert res.intervals[0].start_utc == expected_utc


def test_autumn_fold_mapping():
    opts = make_opts(columns={"timestamp": "ts", "value": "val", "fold": "f"})
    csv_data = "ts,val,f\n2024-10-27 02:00:00,1.0,0\n2024-10-27 02:00:00,1.0,1\n"
    res = parse_consumption(io.StringIO(csv_data), opts)
    assert len(res.intervals) == 2
    assert res.intervals[0].start_utc == datetime(2024, 10, 27, 0, 0, tzinfo=timezone.utc)
    assert res.intervals[1].start_utc == datetime(2024, 10, 27, 1, 0, tzinfo=timezone.utc)


def test_fixed_clock_mode():
    opts = make_opts(fixed_clock=True)
    res = parse_consumption(io.StringIO("ts,val\n2024-07-01 12:00:00,1.0\n"), opts)
    assert res.intervals[0].start_utc == datetime(2024, 7, 1, 11, 0, tzinfo=timezone.utc)
    assert any("fixed_clock" in getattr(a, "code", "") for a in res.anomalies)


# --- Fixture: Semicolon, Comma Decimal, UTF-8 BOM ---

def test_bom_semicolon_decimal_comma_fixture():
    path = Path(__file__).parent / "fixtures" / "consumption" / "hourly_italian_semicolon_comma_bom.csv"
    raw = path.read_bytes()
    opts = make_opts(
        delimiter=";",
        decimal_separator=",",
        columns={"timestamp": "DATA_ORA", "value": "ENERGIA_ATTIVA"},
    )
    res = parse_consumption(io.BytesIO(raw), opts)
    assert len(res.intervals) == 4
    assert res.total_energy_kwh == pytest.approx(5.0)
    assert res.source_digest == hashlib.sha256(raw).hexdigest()
    assert isinstance(res.summary_counts, Mapping)


# --- Full Contiguous October–September Range, Leap Feb 29, Partial Month ---

def test_contiguous_oct_sep_full_range():
    # 366-day contiguous range 2023-10-01T00:00:00 to 2024-09-30T23:00:00 = 8784 hours
    start_dt = datetime(2023, 10, 1, 0, 0, tzinfo=timezone.utc)
    total_hours = 8784
    csv_rows = ["ts,val"] + [(start_dt + timedelta(hours=i)).isoformat() + ",1.0" for i in range(total_hours)]
    opts = make_opts(
        coverage_start=start_dt,
        coverage_end=start_dt + timedelta(hours=total_hours),
    )
    res = parse_consumption(io.StringIO("\n".join(csv_rows)), opts)
    assert len(res.intervals) == 8784
    assert res.total_energy_kwh == pytest.approx(8784.0)
    assert res.coverage_start == start_dt
    assert res.coverage_end == start_dt + timedelta(hours=total_hours)
    assert res.ready_for_projection is False


def test_leap_feb29_and_partial_month_ranges():
    # Feb 28 to Mar 1 in leap year 2024 = 72 hours
    start_dt = datetime(2024, 2, 28, 0, 0, tzinfo=timezone.utc)
    csv_rows = ["ts,val"] + [(start_dt + timedelta(hours=i)).isoformat() + ",0.5" for i in range(72)]
    opts = make_opts()
    res = parse_consumption(io.StringIO("\n".join(csv_rows)), opts)
    assert len(res.intervals) == 72
    assert res.total_energy_kwh == pytest.approx(36.0)
    assert res.ready_for_projection is False


@pytest.mark.parametrize("bad_kw", [{"duration_seconds": 1800}, {"unit": "MW"}, {"demand_basis": "unknown"}])
def test_invalid_options(bad_kw):
    with pytest.raises((ConsumptionIngestError, ValueError)):
        parse_consumption(io.StringIO("ts,val\n2024-01-01 00:00:00,1\n"), make_opts(**bad_kw))


@pytest.mark.parametrize("day,hours", [("2024-03-31", 23), ("2024-10-27", 25)])
@pytest.mark.parametrize("duration", [900, 3600])
@pytest.mark.parametrize("convention", ["start", "end"])
def test_every_utc_slot_across_dst(day, hours, duration, convention):
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("Europe/Rome")
    start = datetime.fromisoformat(day).replace(tzinfo=zone).astimezone(timezone.utc)
    count = hours * 3600 // duration
    step = timedelta(seconds=duration)
    rows = ["ts,val"]
    for i in range(count):
        stamp = start + (i + (convention == "end")) * step
        rows.append(f"{stamp.astimezone(zone).isoformat()},{duration / 3600}")
    result = parse_consumption(io.StringIO("\n".join(rows)), make_opts(
        duration_seconds=duration, timestamp_convention=convention))
    assert [i.start_utc for i in result.intervals] == [start + i * step for i in range(count)]
    assert result.total_energy_kwh == hours
    assert result.coverage_end == start + timedelta(hours=hours)


@pytest.mark.parametrize("day,periods", [("2024-03-31", 92), ("2024-10-27", 100)])
def test_distributor_out_of_range_and_fixed_clock(day, periods):
    opts = make_opts(adapter="distributor_period", duration_seconds=900,
                     columns={"date": "d", "period": "p", "value": "val"})
    with pytest.raises(ConsumptionIngestError, match="invalid_value"):
        parse_consumption(io.StringIO(f"d,p,val\n{day},{periods + 1},1\n"), opts)
    rows = "d,p,val\n" + "\n".join(f"{day},{p},0.25" for p in range(1, 97))
    from dataclasses import replace
    result = parse_consumption(io.StringIO(rows), replace(opts, fixed_clock=True))
    assert len(result.intervals) == 96
    assert result.total_energy_kwh == 24
    assert result.anomalies[0].code == "fixed_clock"


def test_sorting_is_reported_and_does_not_change_digest():
    raw = b"ts,val\n2024-01-01T01:00:00Z,2\n2024-01-01T00:00:00Z,1\n"
    result = parse_consumption(io.BytesIO(raw), make_opts())
    assert [i.energy_kwh for i in result.intervals] == [1, 2]
    assert result.anomalies[0].code == "sorted"
    assert result.source_digest == hashlib.sha256(raw).hexdigest()
    assert result.summary_counts == {"rows_seen": 2, "intervals": 2, "errors": 0}


@pytest.mark.parametrize("value", ["inf", "-inf", "1e999", "1,2", "1.000,5", "", "2kWh"])
def test_bad_values(value):
    with pytest.raises(ConsumptionIngestError):
        parse_consumption(io.StringIO(f'ts,val\n2024-01-01T00:00:00Z,"{value}"\n'), make_opts())


@pytest.mark.parametrize("raw,code", [
    (b"ts,val\n\xff,1\n", "invalid_encoding"),
    (b'ts,val\n"unfinished,1\n', "invalid_csv"),
    (b"ts,val\n2024-01-01T00:00:00Z,1,extra\n", "invalid_csv"),
    (b"ts,val\n", "empty_data"),
    (b"ts,ts,val\n", "mapping_required"),
    (b"ts,val\n2024-01-01T00:00:00Z,\x00\n", "invalid_csv"),
])
def test_bad_csv(raw, code):
    with pytest.raises(ConsumptionIngestError) as error:
        parse_consumption(io.BytesIO(raw), make_opts())
    assert error.value.code == code
    assert len(str(error.value)) < 80
    assert "2024" not in str(error.value)


@pytest.mark.parametrize("rows,code", [
    (["2024-01-01T00:00:00Z", "2024-01-01T00:30:00Z"], "overlap"),
    (["2024-01-01T00:00:00Z", "2024-01-01T01:00:00+01:00"], "duplicate"),
    (["2024-01-01T00:00:00Z", "2024-01-01T00:15:00Z"], "overlap"),
])
def test_overlap_duplicate_and_mixed_intervals(rows, code):
    with pytest.raises(ConsumptionIngestError) as error:
        parse_consumption(io.StringIO("ts,val\n" + "\n".join(t + ",1" for t in rows)), make_opts())
    assert error.value.code == code


def test_explicit_long_timestamp_and_reconciled_gross():
    result = parse_consumption(io.StringIO("Ora;Energia\n29/02/2024 00:15;250\n"), make_opts(
        delimiter=";", timestamp_format="%d/%m/%Y %H:%M", unit="Wh", duration_seconds=900,
        timestamp_convention="end", columns={"timestamp": "Ora", "value": "Energia"},
        demand_basis="reconciled_gross", existing_pv=True, existing_battery=True))
    assert result.intervals[0].start_utc == datetime(2024, 2, 28, 23, tzinfo=timezone.utc)
    assert result.total_energy_kwh == 0.25
    assert result.options.demand_basis == "reconciled_gross"


def test_actual_limits_and_chunked_read_only_stream():
    class Stream:
        def __init__(self, raw):
            self.stream = io.BytesIO(raw)

        def read(self, size):
            assert 0 < size <= 4096
            return self.stream.read(min(size, 7))

    # A quoted UTF-8 label split across tiny chunks, including BOM/CRLF.
    raw = '\ufeffts,val,note\r\n2024-01-01T00:00:00Z,1,"caffè\nquoted"\r\n'.encode()
    result = parse_consumption(Stream(raw), make_opts())
    assert result.source_digest == hashlib.sha256(raw).hexdigest()
    assert result.total_energy_kwh == 1
    for bad in (b"ts,val,note\n2024-01-01T00:00:00Z,1," + b"x" * 4097,
                b"ts,val\n" + b"," * 65,
                b" " * (16 * 1024 * 1024 + 1)):
        with pytest.raises(ConsumptionIngestError, match="limits"):
            parse_consumption(Stream(bad), make_opts())


def test_result_metadata_is_immutable_and_preserves_declaration():
    from dataclasses import FrozenInstanceError
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    opts = make_opts(coverage_start=start, coverage_end=start + timedelta(hours=1))
    result = parse_consumption(io.StringIO("ts,val\n2024-01-01T00:00:00Z,1\n"), opts)
    opts.columns["value"] = "changed"
    assert result.options.columns["value"] == "val"
    assert result.coverage_declared is True
    with pytest.raises(TypeError):
        result.summary_counts["errors"] = 1
    with pytest.raises(FrozenInstanceError):
        result.intervals[0].energy_kwh = 2
