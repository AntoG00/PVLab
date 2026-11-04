'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { bindInput } = require('../feasibility/web/consumption-csv.js');

const makeHeader = () => '"Giorno";' + Array.from({ length: 96 }, (_, i) => {
  const p = m => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  return `"${p(i * 15)}-${p((i + 1) * 15)}"`;
}).join(';') + ';';

const makeRow = (d, v = '0,250') => `"${d}";` + Array(96).fill(`"${v}"`).join(';') + ';';
const makeFile = (name, rows, text) => ({
  name, size: 1024,
  text: async () => text ?? [makeHeader(), ...rows].join('\n') + '\n',
});

function makeEl(tag = 'div') {
  const children = [], attrs = {}, listeners = {};
  return {
    tagName: tag.toUpperCase(), children, attributes: attrs, parentNode: null, className: '', textContent: '',
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k] ?? null; },
    appendChild(c) { c.parentNode = this; children.push(c); return c; },
    append(...nodes) { for (const n of nodes) this.appendChild(n); },
    removeChild(c) { const i = children.indexOf(c); if (i !== -1) children.splice(i, 1); c.parentNode = null; return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    async dispatch(ev) { for (const fn of (listeners[ev] || [])) await fn(); },
  };
}

function makeEnv(scopeVal = '') {
  const fields = {};
  for (const k of ['csv', 'fixed_clock', 'csv_start', 'csv_end', 'coverage_start', 'coverage_end',
    'totals', 'timestamp', 'energy', 'fold', 'unit', 'timestamp_convention', 'delimiter', 'decimal_separator', 'duration_seconds']) {
    fields[k] = Object.assign(makeEl('input'), { value: '', checked: false, files: [] });
  }
  const container = makeEl('div'), form = makeEl('form');
  container.ownerDocument = { createElement: tag => makeEl(tag) };
  let scope = scopeVal, notified = 0, errors = [];
  const binding = bindInput({
    fields, container, form,
    onChange: el => { if (el === fields.csv) notified++; },
    reportError: err => errors.push(err),
    getScope: () => scope,
  });
  return { fields, container, form, binding, getScope: () => scope, setScope: s => { scope = s; }, getNotified: () => notified, errors };
}

test('selection wide conversion autofills metadata, Rome bounds, retains partial months, sets banner and notifies once', async () => {
  const { fields, container, binding, getNotified } = makeEnv();
  assert.equal(await binding.prepare(), null);

  const fileA = makeFile('jan.csv', [makeRow('01/01/2026', '0,250')]);
  fields.csv.files = [fileA];
  const blobA = await binding.prepare();

  assert.notEqual(blobA, fileA);
  assert.equal(blobA.name, 'consumption.csv');
  for (const [k, v] of Object.entries({ timestamp: 'timestamp', energy: 'energy_kwh', fold: '', unit: 'kWh', timestamp_convention: 'start', delimiter: ',', decimal_separator: '.', duration_seconds: '900' })) {
    assert.equal(fields[k].value, v);
  }
  assert.equal(fields.coverage_start.value, '2026-01-01');
  assert.equal(fields.coverage_end.value, '2026-01-02');
  assert.equal(fields.csv_start.value, '2026-01-01T00:00:00+01:00');
  assert.equal(fields.csv_end.value, '2026-01-02T00:00:00+01:00');
  assert.equal(fields.totals.value, '24,,,,,,,,,,,');
  assert.equal(getNotified(), 1);

  assert.equal(container.children.length, 1);
  const banner = container.children[0];
  assert.equal(banner.getAttribute('role'), 'status');
  assert.equal(banner.className, 'csv-auto-confirmation');
  const bannerText = banner.children.map(c => c.textContent).join(' ');
  assert.match(bannerText, /E-Distribuzione 15-minute matrix detected/);
  assert.match(bannerText, /Partial coverage detected/);

  fields.csv.files = [makeFile('zero.csv', [makeRow('01/01/2026', '0,000')])];
  await binding.prepare();
  assert.equal(fields.totals.value, '0,,,,,,,,,,,');
});

test('cached prepare reuses normalized blob and preserves user corrected totals', async () => {
  const { fields, binding } = makeEnv();
  fields.csv.files = [makeFile('jan.csv', [makeRow('01/01/2026', '0,250')])];
  const blob1 = await binding.prepare();
  fields.totals.value = '1,2,3,4,5,6,7,8,9,10,11,12';
  const blob2 = await binding.prepare();
  assert.equal(blob2, blob1);
  assert.equal(fields.totals.value, '1,2,3,4,5,6,7,8,9,10,11,12');
});

test('custom single retains manual settings & clears banner; multiple custom, mixed and failed matrix reject safely', async () => {
  const { fields, container, binding } = makeEnv();
  fields.csv.files = [makeFile('jan.csv', [makeRow('01/01/2026')])];
  const oldBlob = await binding.prepare();
  assert.equal(container.children.length, 1);

  const customFile = { name: 'custom.csv', size: 50, text: async () => 'ts,val\n2026-01-01,1\n' };
  fields.csv.files = [customFile];
  fields.delimiter.value = ';';
  const resCustom = await binding.prepare();
  assert.equal(resCustom, customFile);
  assert.equal(fields.delimiter.value, ';');
  assert.equal(container.children.length, 0);

  fields.csv.files = [customFile, { ...customFile, name: 'c2.csv' }];
  await assert.rejects(binding.prepare(), /Multiple custom CSV files not supported/);

  const validFile = makeFile('v.csv', [makeRow('01/01/2026')]);
  fields.csv.files = [validFile, customFile];
  await assert.rejects(binding.prepare());

  fields.csv.files = [makeFile('bad.csv', [], 'Giorno;00:00-00:15;\n"01/01/2026";"bad";\n')];
  await assert.rejects(binding.prepare());
  await assert.rejects(binding.prepare());
});

test('async race critical: two prepare calls on pending B wait for B and never return cached A', async () => {
  const { fields, binding } = makeEnv();
  fields.csv.files = [makeFile('a.csv', [makeRow('01/01/2026', '0,250')])];
  const blobA = await binding.prepare();

  let resolveB;
  const pB = new Promise(r => { resolveB = r; });
  const fileB = { name: 'b.csv', size: 1000, text: () => pB };
  fields.csv.files = [fileB];

  const p1 = binding.prepare();
  const p2 = binding.prepare();
  assert.equal(p1, p2);

  resolveB(makeHeader() + '\n' + makeRow('01/01/2026', '0,500') + '\n');
  const [b1, b2] = await Promise.all([p1, p2]);
  assert.equal(b1, b2);
  assert.notEqual(b1, blobA);
  const textB = await b1.text();
  assert.ok(textB.includes(',0.5\n'));
});

test('pending abort on new file selection or reset, scope change busts cache, stale errors ignored', async () => {
  const { fields, binding, form, setScope, errors } = makeEnv('scope1');
  let resolveSlow;
  const pSlowFile = new Promise(r => { resolveSlow = r; });
  fields.csv.files = [{ name: 'slow.csv', size: 100, text: () => pSlowFile }];
  const pSlow = binding.prepare();
  pSlow.catch(() => {});

  fields.csv.files = [makeFile('quick.csv', [makeRow('01/01/2026')])];
  const pQuick = binding.prepare();
  resolveSlow(makeHeader() + '\n' + makeRow('01/01/2026') + '\n');

  await assert.rejects(pSlow, err => err.name === 'AbortError');
  const quickBlob = await pQuick;
  assert.ok(quickBlob);

  let resolveR;
  const pR = new Promise(r => { resolveR = r; });
  fields.csv.files = [{ name: 'r.csv', size: 100, text: () => pR }];
  const pReset = binding.prepare();
  pReset.catch(() => {});
  await form.dispatch('reset');
  resolveR(makeHeader() + '\n' + makeRow('01/01/2026') + '\n');
  await assert.rejects(pReset, err => err.name === 'AbortError');

  fields.csv.files = [makeFile('same.csv', [makeRow('01/01/2026')])];
  const s1 = await binding.prepare();
  setScope('scope2');
  const s2 = await binding.prepare();
  assert.notEqual(s1, s2);

  let resolveStale;
  const pStale = new Promise(r => { resolveStale = r; });
  fields.csv.files = [{ name: 'stale.csv', size: 100, text: () => pStale }];
  const chg = fields.csv.dispatch('change');
  binding.reset();
  resolveStale('bad data');
  await chg;
  assert.equal(errors.length, 0);
});

test('explicit fixed clock checked only on user change updates bounds to +01 and does not auto-enable', async () => {
  const { fields, binding } = makeEnv();
  fields.csv.files = [makeFile('summer.csv', [makeRow('15/07/2026', '0,500')])];
  assert.equal(fields.fixed_clock.checked, false);

  await binding.prepare();
  assert.equal(fields.fixed_clock.checked, false);
  assert.equal(fields.csv_start.value, '2026-07-15T00:00:00+02:00');
  assert.equal(fields.csv_end.value, '2026-07-16T00:00:00+02:00');

  fields.fixed_clock.checked = true;
  await fields.fixed_clock.dispatch('change');
  assert.equal(fields.csv_start.value, '2026-07-15T00:00:00+01:00');
  assert.equal(fields.csv_end.value, '2026-07-16T00:00:00+01:00');
});
