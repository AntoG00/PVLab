'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { detectEdistribuzione, convertEdistribuzione } = require('../feasibility/web/consumption-csv.js');

const makeHeader = () => {
  const cols = ['Giorno', ...Array.from({ length: 96 }, (_, i) => {
    const h1 = String(Math.floor((i * 15) / 60)).padStart(2, '0');
    const m1 = String((i * 15) % 60).padStart(2, '0');
    const h2 = String(Math.floor(((i + 1) * 15 / 60) % 24)).padStart(2, '0');
    const m2 = String(((i + 1) * 15) % 60).padStart(2, '0');
    return `${h1}:${m1}-${h2}:${m2}`;
  })];
  return cols.map(c => `"${c}"`).join(';') + ';';
};

const makeRow = (d, val = '0,250') => {
  const vals = Array.isArray(val) ? val : Array(96).fill(val);
  return `"${d}";` + vals.map(v => `"${v}"`).join(';') + ';';
};

const makeFile = (name, rows, bom = '', eol = '\n', size = 1024) => ({
  name, size,
  text: async () => bom + [makeHeader(), ...rows].join(eol) + eol,
});

test('detectEdistribuzione identifies Giorno 97-column header and rejects custom', () => {
  assert.equal(detectEdistribuzione(makeHeader()), true);
  assert.equal(detectEdistribuzione(makeHeader() + '\n' + makeRow('01/01/2026')), true);
  assert.equal(detectEdistribuzione('timestamp,value\n2026-01-01T00:00:00,1.0'), false);
  assert.equal(detectEdistribuzione('Data;00:15;00:30;'), false);
  assert.equal(detectEdistribuzione('Giorno;1;2;3;'), false);
  assert.equal(detectEdistribuzione(''), false);
  assert.equal(detectEdistribuzione(null), false);
});

test('standard conversion parses quoted comma-decimal rows, zero values, BOM and CRLF', async () => {
  const file = makeFile('jan.csv', [makeRow('01/01/2026', '0,250')], '\uFEFF', '\r\n');
  const res = await convertEdistribuzione([file]);
  assert.equal(res.days, 1);
  assert.ok(Math.abs(res.totalKwh - 24.0) < 1e-6);
  assert.equal(res.monthlyTotals.length, 12);
  assert.ok(Math.abs(res.monthlyTotals[0] - 24.0) < 1e-6);
  for (let m = 1; m < 12; m++) assert.equal(res.monthlyTotals[m], null);
  assert.deepEqual(res.monthsPresent, [1]);
  assert.equal(res.startDate, '2026-01-01');
  assert.equal(res.endDate, '2026-01-02');
  assert.equal(res.coverageStart, '2026-01-01T00:00:00+01:00');
  assert.equal(res.coverageEnd, '2026-01-02T00:00:00+01:00');
  assert.deepEqual(res.dstDays, []);

  const text = await res.file.text();
  const lines = text.trim().split('\n');
  assert.equal(lines[0], 'timestamp,energy_kwh');
  assert.equal(lines.length, 97);
  assert.equal(lines[1], '2026-01-01T00:00:00,0.25');
  assert.equal(lines[96], '2026-01-01T23:45:00,0.25');

  const zeroFile = makeFile('zero.csv', [makeRow('02/01/2026', '0,000')]);
  const zeroRes = await convertEdistribuzione([zeroFile]);
  assert.equal(zeroRes.totalKwh, 0);
  assert.equal(zeroRes.monthlyTotals[0], 0);
});

test('multiple files in reverse order are sorted chronologically with exclusive bounds', async () => {
  const fFeb = makeFile('2026-02.csv', [makeRow('01/02/2026', '0,100')]);
  const fJan = makeFile('2026-01.csv', [makeRow('31/01/2026', '0,100')]);
  const res = await convertEdistribuzione([fFeb, fJan]);
  assert.equal(res.days, 2);
  assert.deepEqual(res.monthsPresent, [1, 2]);
  assert.equal(res.startDate, '2026-01-31');
  assert.equal(res.endDate, '2026-02-02');
  assert.equal(res.coverageStart, '2026-01-31T00:00:00+01:00');
  assert.equal(res.coverageEnd, '2026-02-02T00:00:00+01:00');

  const text = await res.file.text();
  const lines = text.trim().split('\n');
  assert.equal(lines.length, 193);
  assert.equal(lines[1].slice(0, 10), '2026-01-31');
  assert.equal(lines[97].slice(0, 10), '2026-02-01');
});

test('exclusive bounds correctly roll over year end', async () => {
  const fEnd = makeFile('year-end.csv', [makeRow('31/12/2026', '0,100')]);
  const res = await convertEdistribuzione([fEnd]);
  assert.equal(res.startDate, '2026-12-31');
  assert.equal(res.endDate, '2027-01-01');
  assert.equal(res.coverageStart, '2026-12-31T00:00:00+01:00');
  assert.equal(res.coverageEnd, '2027-01-01T00:00:00+01:00');
  assert.deepEqual(res.monthsPresent, [12]);
  assert.equal(res.monthlyTotals[0], null);
  assert.ok(res.monthlyTotals[11] > 0);
});

test('DST spring and autumn transitions preserve 96 slots, track dstDays and Rome offsets', async () => {
  const fSpring = makeFile('spring.csv', [makeRow('29/03/2026', '0,500')]);
  const resSpring = await convertEdistribuzione([fSpring]);
  assert.equal(resSpring.days, 1);
  assert.ok(Math.abs(resSpring.totalKwh - 48.0) < 1e-6);
  assert.deepEqual(resSpring.dstDays, ['2026-03-29']);
  assert.equal(resSpring.coverageStart, '2026-03-29T00:00:00+01:00');
  assert.equal(resSpring.coverageEnd, '2026-03-30T00:00:00+02:00');
  const springLines = (await resSpring.file.text()).trim().split('\n');
  assert.equal(springLines.length, 97);

  const fAutumn = makeFile('autumn.csv', [makeRow('25/10/2026', '0,500')]);
  const resAutumn = await convertEdistribuzione([fAutumn]);
  assert.equal(resAutumn.days, 1);
  assert.ok(Math.abs(resAutumn.totalKwh - 48.0) < 1e-6);
  assert.deepEqual(resAutumn.dstDays, ['2026-10-25']);
  assert.equal(resAutumn.coverageStart, '2026-10-25T00:00:00+02:00');
  assert.equal(resAutumn.coverageEnd, '2026-10-26T00:00:00+01:00');
  const autumnLines = (await resAutumn.file.text()).trim().split('\n');
  assert.equal(autumnLines.length, 97);
});

test('host timezone independence with process.env.TZ America/New_York ensures +02 bounds', async () => {
  const prevTz = process.env.TZ;
  try {
    process.env.TZ = 'America/New_York';
    const summerFile = makeFile('summer.csv', [makeRow('15/07/2026', '0,500')]);
    const res = await convertEdistribuzione([summerFile]);
    assert.equal(res.coverageStart, '2026-07-15T00:00:00+02:00');
    assert.equal(res.coverageEnd, '2026-07-16T00:00:00+02:00');
  } finally {
    if (prevTz === undefined) delete process.env.TZ;
    else process.env.TZ = prevTz;
  }
});

test('fail-closed rejections for invalid inputs, gaps, duplicates and malformed rows', async () => {
  await assert.rejects(convertEdistribuzione([]), /EMPTY|REQUIRED/i);
  await assert.rejects(convertEdistribuzione([{ name: 'big.csv', size: 17 * 1024 * 1024, text: async () => '' }]), /LIMIT|SIZE/i);

  const badHeader = { name: 'h.csv', size: 10, text: async () => 'Col1;Col2\n1;2' };
  await assert.rejects(convertEdistribuzione([badHeader]), /HEADER|COLUMN/i);

  const badDate = makeFile('baddate.csv', [makeRow('99/99/2026')]);
  await assert.rejects(convertEdistribuzione([badDate]), /DATE|INVALID/i);

  const negative = makeFile('neg.csv', [makeRow('01/01/2026', '-0,100')]);
  await assert.rejects(convertEdistribuzione([negative]), /NEGATIVE|VALUE/i);

  const nanVal = makeFile('nan.csv', [makeRow('01/01/2026', 'NaN')]);
  await assert.rejects(convertEdistribuzione([nanVal]), /NAN|NUMERIC|VALUE/i);

  const infVal = makeFile('inf.csv', [makeRow('01/01/2026', 'Infinity')]);
  await assert.rejects(convertEdistribuzione([infVal]), /INF|NUMERIC|VALUE/i);

  const overflow = makeFile('overflow.csv', [makeRow('01/01/2026', '1e309')]);
  await assert.rejects(convertEdistribuzione([overflow]), /OVERFLOW|VALUE|NUMERIC/i);

  const blankCell = makeFile('blank.csv', [makeRow('01/01/2026', '')]);
  await assert.rejects(convertEdistribuzione([blankCell]), /BLANK|EMPTY|VALUE/i);

  const missingCells = { name: 'miss.csv', size: 10, text: async () => makeHeader() + '\n"01/01/2026";"0,10";\n' };
  await assert.rejects(convertEdistribuzione([missingCells]), /CELL|ROW|COLUMN/i);

  const dup = makeFile('dup.csv', [makeRow('01/01/2026'), makeRow('01/01/2026')]);
  await assert.rejects(convertEdistribuzione([dup]), /DUPLICATE/i);

  const gap = makeFile('gap.csv', [makeRow('01/01/2026'), makeRow('03/01/2026')]);
  await assert.rejects(convertEdistribuzione([gap]), /GAP|CONTINUITY/i);

  const mixed = makeFile('mixed.csv', [makeRow('01/01/2026'), makeRow('02/01/2026', 'bad')]);
  await assert.rejects(convertEdistribuzione([mixed]), /VALUE|NUMERIC/i);

  const customFile = { name: 'c.csv', size: 50, text: async () => 'timestamp,value\n2026-01-01T00:00:00,1.0\n' };
  const validFile = makeFile('v.csv', [makeRow('01/01/2026')]);
  await assert.rejects(convertEdistribuzione([validFile, customFile]), /HEADER|FORMAT|EDISTRIBUZIONE/i);
});
