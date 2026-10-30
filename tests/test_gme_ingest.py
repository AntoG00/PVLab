"""Synthetic-only GME parser security, series and civil-time contracts."""
import copy
import shutil
import stat

import numpy as np
import pytest

from feasibility import prepare_market_prices as prep

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

ZONES = ('NORD', 'CNOR', 'CSUD', 'SUD', 'CALA', 'SICI', 'SARD', 'PUN')
BASE_PRICES = {
    'NORD': (110.0, 1.0), 'CNOR': (120.0, 1.1), 'CSUD': (130.0, 1.2),
    'SUD': (140.0, 1.3), 'CALA': (150.0, 1.4), 'SICI': (160.0, 1.5),
    'SARD': (170.0, 1.6), 'PUN': (180.0, 1.7), 'NAT': (999.0, 0.0),
}


def _fmt(v):
    if isinstance(v, float):
        s = f"{v:.2f}" if round(v, 2) == v else f"{v}"
        return s.replace('.', ',')
    if isinstance(v, int):
        return f"{v:.2f}".replace('.', ',')
    return '' if v is None else str(v)


def day_xml(day, periods=None, quarter=False, prices=None, omit=(), market='MGP', nat=True):
    if hasattr(day, 'strftime'):
        day_str, y, m, d = day.strftime('%Y%m%d'), day.year, day.month, day.day
    else:
        s = str(day).replace('-', '').replace('/', '').strip()
        day_str, y, m, d = s[:8], int(s[:4]), int(s[4:6]), int(s[6:8])

    if periods is None:
        rome = ZoneInfo('Europe/Rome')
        t0 = datetime(y, m, d, tzinfo=rome).astimezone(timezone.utc)
        d1 = date(y, m, d) + timedelta(days=1)
        t1 = datetime(d1.year, d1.month, d1.day, tzinfo=rome).astimezone(timezone.utc)
        hours = int(round((t1 - t0).total_seconds() / 3600))
        periods = hours * 4 if quarter else hours

    def _price(z, p):
        if prices is not None:
            if callable(prices):
                return prices(p, z)
            if isinstance(prices, dict):
                if z in prices:
                    v = prices[z]
                    return v(p) if callable(v) else v[p - 1] if isinstance(v, (list, tuple)) else v
            elif isinstance(prices, (list, tuple)):
                return prices[p - 1]
            elif isinstance(prices, (int, float, str)):
                return prices
        base, slope = BASE_PRICES.get(z, (100.0, 1.0))
        return base + p * slope

    root = ET.Element('NewDataSet')
    omit_set = set(omit)
    p_field = 'Periodo' if quarter else 'Ora'
    series = [z for z in ZONES if z not in omit_set]
    if (nat or (isinstance(prices, dict) and 'NAT' in prices)) and 'NAT' not in omit_set:
        series.append('NAT')

    for p in range(1, periods + 1):
        row = ET.SubElement(root, 'Prezzi15' if quarter else 'Prezzi')
        if 'Data' not in omit_set:
            ET.SubElement(row, 'Data').text = day_str
        if 'Mercato' not in omit_set:
            ET.SubElement(row, 'Mercato').text = str(market)
        if p_field not in omit_set:
            ET.SubElement(row, p_field).text = str(p)
        for z in series:
            ET.SubElement(row, z).text = _fmt(_price(z, p))

    return ET.tostring(root, encoding='utf-8')


def write_zip(path, members):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        items = members.items() if hasattr(members, 'items') else members
        for name, data in items:
            z.writestr(name, data.encode('utf-8') if isinstance(data, str) else data)


def ingest_day(tmp_path, day='2025-01-01', **kwargs):
    write_zip(tmp_path / 'input.zip', {day.replace('-', '') + ('MGPPrezzi15.xml' if kwargs.get('quarter') else 'MGPPrezzi.xml'): day_xml(day, **kwargs)})
    end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return prep.ingest_mgp_data(tmp_path, day, end)


@pytest.mark.parametrize('day,hours', [('2025-03-30', 23), ('2025-10-26', 25), ('2024-02-29', 24), ('2025-07-15', 24)])
@pytest.mark.parametrize('quarter', [False, True])
def test_all_series_dst_and_utc_boundaries(tmp_path, day, hours, quarter):
    result = ingest_day(tmp_path, day, quarter=quarter)
    records = result['days'][day]['records']
    assert len(records) == hours * (4 if quarter else 1) * 8
    for i, series in enumerate(prep.SERIES):
        rows = [r for r in records if r[0] == series]
        assert rows[0][3] == pytest.approx((BASE_PRICES[series][0] + BASE_PRICES[series][1]) / 1000)
        starts = [datetime.fromisoformat(r[1]) for r in rows]
        assert all(b - a == timedelta(seconds=rows[0][2]) for a, b in zip(starts, starts[1:]))
        assert (starts[-1] + timedelta(seconds=rows[-1][2]) - starts[0]).total_seconds() == hours * 3600
    assert not any(r[0] == 'NAT' for r in records)
    assert result['parser_version'] == prep.PARSER_VERSION


@pytest.mark.parametrize('field', list(prep.SERIES) + ['Data', 'Mercato', 'Ora'])
def test_required_fields(tmp_path, field):
    with pytest.raises(ValueError):
        ingest_day(tmp_path, omit=(field,))


@pytest.mark.parametrize('periods', [23, 25, 0, 96])
def test_wrong_ordinary_day_length(tmp_path, periods):
    with pytest.raises(ValueError):
        ingest_day(tmp_path, periods=periods)


@pytest.mark.parametrize('prices', [-80, 0, -0.25])
def test_signed_finite_prices(tmp_path, prices):
    result = ingest_day(tmp_path, prices=prices)
    assert all(r[3] == prices / 1000 for r in result['days']['2025-01-01']['records'])


@pytest.mark.parametrize('bad', ['NaN', 'inf', '-inf', 'abc', ''])
def test_invalid_prices(tmp_path, bad):
    with pytest.raises(ValueError):
        ingest_day(tmp_path, prices=bad)


@pytest.mark.parametrize('field,value', [('Data', '20250102'), ('Mercato', 'MI'), ('Ora', '1')])
def test_every_row_validated(tmp_path, field, value):
    root = ET.fromstring(day_xml('2025-01-01'))
    root[1].find(field).text = value
    write_zip(tmp_path / 'bad.zip', {'20250101MGPPrezzi.xml': ET.tostring(root)})
    with pytest.raises(ValueError):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


def test_quarter_only_and_overlap_for_every_series(tmp_path):
    hourly = day_xml('2025-01-01', prices=lambda p, z: BASE_PRICES[z][0] + p)
    quarters = day_xml('2025-01-01', quarter=True, prices=lambda p, z: BASE_PRICES[z][0] + (p - 1) // 4 + 1 + (-0.3, -0.1, 0.1, 0.3)[(p - 1) % 4])
    write_zip(tmp_path / 'day.zip', {'20250101MGPPrezzi.xml': hourly, '20250101MGPPrezzi15.xml': quarters})
    result = prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')
    assert result['days']['2025-01-01']['overlap']['compared_series_hours'] == 24 * 8
    assert len(result['days']['2025-01-01']['records']) == 96 * 8
    root = ET.fromstring(quarters)
    root[0].find('PUN').text = '999'
    write_zip(tmp_path / 'day.zip', {'20250101MGPPrezzi.xml': hourly, '20250101MGPPrezzi15.xml': ET.tostring(root)})
    with pytest.raises(ValueError, match='overlap mismatch'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


def test_duplicate_archive_and_conflict(tmp_path):
    result = ingest_day(tmp_path)
    shutil.copyfile(tmp_path / 'input.zip', tmp_path / 'copy.zip')
    assert prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02') == result
    write_zip(tmp_path / 'conflict.zip', {'20250101MGPPrezzi.xml': day_xml('2025-01-01', prices=3)})
    with pytest.raises(ValueError, match='Conflicting day'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


def test_gaps_and_unknown_format(tmp_path):
    ingest_day(tmp_path)
    with pytest.raises(ValueError, match='Missing delivery date'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-03')
    with pytest.raises(ValueError, match='supported source formats'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02', source_schema='future')
    write_zip(tmp_path / 'input.zip', {'20250101MGPPrezzi.xml': '<Unknown />'})
    with pytest.raises(ValueError, match='supported source formats'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


@pytest.mark.parametrize('name', ['../20250101MGPPrezzi.xml', '/20250101MGPPrezzi.xml', 'a\\20250101MGPPrezzi.xml', 'inner.zip', 'C:20250101MGPPrezzi.xml'])
def test_archive_paths_and_nested_archives(tmp_path, name):
    write_zip(tmp_path / 'input.zip', {name: day_xml('2025-01-01')})
    with pytest.raises(ValueError):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


def test_zip_symlink(tmp_path):
    member = zipfile.ZipInfo('20250101MGPPrezzi.xml')
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(tmp_path / 'bad.zip', 'w') as archive:
        archive.writestr(member, '/tmp/target')
    with pytest.raises(ValueError, match='Unsafe ZIP'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


@pytest.mark.parametrize('payload', ['<!DOCTYPE NewDataSet><NewDataSet/>', '<!DOCTYPE NewDataSet [<!ENTITY x SYSTEM "file:///not-read">]><NewDataSet>&x;</NewDataSet>'])
def test_xml_dtd_entities(tmp_path, payload):
    write_zip(tmp_path / 'bad.zip', {'20250101MGPPrezzi.xml': payload})
    with pytest.raises(ValueError, match='Unsafe XML'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


@pytest.mark.parametrize('limit,value', [('MAX_MEMBERS', 0), ('MAX_MEMBER_BYTES', 100), ('MAX_ARCHIVE_BYTES', 100), ('MAX_RECORDS', 1), ('MAX_RATIO', 1)])
def test_limits_enforced(tmp_path, monkeypatch, limit, value):
    monkeypatch.setattr(prep, limit, value)
    with pytest.raises(ValueError, match='limit'):
        ingest_day(tmp_path)


def test_real_high_ratio_payload_rejected(tmp_path):
    write_zip(tmp_path / 'bad.zip', {'20250101MGPPrezzi.xml': b' ' * 200000})
    with pytest.raises(ValueError, match='compression ratio'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


def test_inline_schema_and_malformed_xml(tmp_path):
    root = ET.fromstring(day_xml('2025-01-01'))
    schema = ET.Element('{http://www.w3.org/2001/XMLSchema}schema')
    node = schema
    for tag in ('element', 'complexType', 'choice', 'element', 'complexType', 'sequence'):
        node = ET.SubElement(node, '{http://www.w3.org/2001/XMLSchema}' + tag)
    root.insert(0, schema)
    write_zip(tmp_path / 'day.zip', {'20250101MGPPrezzi.xml': ET.tostring(root)})
    assert prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')['days']
    write_zip(tmp_path / 'day.zip', {'20250101MGPPrezzi.xml': '<broken'})
    with pytest.raises(ValueError, match='Unsafe XML'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')


def test_quarter_duration_weighted_actual_mean(tmp_path):
    data = ingest_day(tmp_path, quarter=True, prices=lambda p, s: p)
    product = prep.compute_empirical_window(data['days'], '2025-01-01', '2025-01-02', series='NORD')
    assert product['total_hours'] == 24
    assert product['mean_eur_kwh'] == pytest.approx(48.5 / 1000)
    assert product['matrix_eur_kwh'][0, 0] == pytest.approx(2.5 / 1000)
    assert product['observation_counts'].sum() == 96
    assert product['counts_hours'].sum() == 24
    assert product['normalized_shape'] is None


def test_xml_nodes_duplicate_fields_and_member_date(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(prep, 'MAX_XML_NODES', 3)
        with pytest.raises(ValueError, match='node limit'):
            ingest_day(tmp_path)
    root = ET.fromstring(day_xml('2025-01-01'))
    ET.SubElement(root[0], 'PUN').text = '100'
    write_zip(tmp_path / 'input.zip', {'20250101MGPPrezzi.xml': ET.tostring(root)})
    with pytest.raises(ValueError, match='Duplicate'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')
    write_zip(tmp_path / 'input.zip', {'20250102MGPPrezzi.xml': day_xml('2025-01-01')})
    with pytest.raises(ValueError, match='Member date'):
        prep.ingest_mgp_data(tmp_path, '2025-01-01', '2025-01-02')
