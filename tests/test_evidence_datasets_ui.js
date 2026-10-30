const assert = require('assert');

let fetchCalls = [], fetchHandler = async (url, opts) => ({ ok: true, json: async () => ({ datasets: [], coverage: [], max_file_bytes: 67108864 }) });
global.fetch = (url, opts) => fetchHandler(url, opts);

function makeEl(tag = 'div') {
  const children = [], attrs = {}, listeners = {};
  return {
    tagName: tag.toUpperCase(), children, dataset: {}, disabled: false, hidden: false, value: '', files: [],
    checked: false, type: '',
    get innerHTML() { return ''; }, set innerHTML(_) { throw new Error('Security: innerHTML forbidden'); },
    _text: '', get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); if (v === '') children.length = 0; },
    appendChild(c) { children.push(c); return c; }, append(...nodes) { nodes.forEach(n => children.push(n)); },
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return attrs[k] ?? null; }, hasAttribute(k) { return k in attrs; },
    classList: { classes: new Set(), toggle(c, f) { f ? this.classes.add(c) : this.classes.delete(c); }, contains(c) { return this.classes.has(c); }, add(c) { this.classes.add(c); }, delete(c) { this.classes.delete(c); } },
    addEventListener(e, fn) { (listeners[e] = listeners[e] || []).push(fn); },
    dispatchEvent(e) { (listeners[e.type || e] || []).forEach(fn => fn(e)); }, focus() { this.focused = true; }
  };
}

let curFixture = null;
global.document = {
  createElement: tag => makeEl(tag),
  createTextNode: text => ({ nodeType: 3, textContent: String(text) }),
  getElementById: id => (id === 'datasetEvidence' && curFixture ? curFixture.article : null)
};

function createFixture() {
  const els = {
    datasetPanelV6: makeEl('div'), datasetPanelV53: makeEl('div'), datasetTabV6: makeEl('button'), datasetTabV53: makeEl('button'),
    datasetSeriesLabel: makeEl('input'), datasetFilesV6: makeEl('input'), datasetFilesV53: makeEl('input'), datasetFilesGoogle: makeEl('input'),
    datasetUpload: makeEl('button'), datasetUploadStatus: makeEl('span'), datasetRows: makeEl('tbody'), datasetCoverage: makeEl('div')
  };
  els.datasetPanelV53.hidden = true;
  els.datasetTabV6.setAttribute('data-dataset-tab', 'pvgis6'); els.datasetTabV6.setAttribute('aria-selected', 'true');
  els.datasetTabV53.setAttribute('data-dataset-tab', 'pvgis53'); els.datasetTabV53.setAttribute('aria-selected', 'false');
  els.datasetSeriesLabel.value = 'Roof South';
  const article = makeEl('article'); article.id = 'datasetEvidence';
  const tabs = [els.datasetTabV6, els.datasetTabV53];
  article.querySelector = sel => sel.startsWith('#') ? els[sel.slice(1)] || null : null;
  article.querySelectorAll = sel => sel === '[data-dataset-tab]' ? tabs : [];
  return (curFixture = { article, els, tabs });
}

// 1. Module exports & require safety (no network on require)
const ed = require('../feasibility/web/evidence-datasets.js');
assert.strictEqual(fetchCalls.length, 0, 'Require must not trigger network');
['init', 'handleUpload', 'loadData', 'renderTable', 'renderCoverage', 'formatBytes'].forEach(m => assert.strictEqual(typeof ed[m], 'function'));
assert.strictEqual(ed.getActiveCalculationsState, undefined, 'No getActiveCalculationsState constant after review');

(async function run() {
  // 2. Dual PVGIS inputs (v6 + hidden v53 panel) -> 2 sequential POSTs with source & encoded filenames, then local GET only
  let f = createFixture();
  f.els.datasetFilesV6.files = [{ name: '<img onerror=alert(1)>.csv', size: 100 }];
  f.els.datasetFilesV53.files = [{ name: 'v53.json', size: 200 }];
  f.els.datasetSeriesLabel.value = 'Roof / South';
  fetchCalls = [];
  fetchHandler = async (url, opts) => {
    fetchCalls.push({ url, opts: opts || {} });
    if (opts && opts.method === 'POST') return { ok: true, text: async () => '' };
    return { ok: true, json: async () => ({ datasets: [], coverage: [], max_file_bytes: 67108864 }) };
  };
  await ed.handleUpload(f.article);
  assert.strictEqual(fetchCalls.length, 3, 'Expected 2 POSTs and 1 GET');
  assert.strictEqual(fetchCalls[0].opts.headers['X-Dataset-Source'], 'pvgis6');
  assert.strictEqual(fetchCalls[0].opts.headers['X-Dataset-Filename'], encodeURIComponent('<img onerror=alert(1)>.csv'));
  assert.strictEqual(fetchCalls[0].opts.headers['X-Dataset-Series'], encodeURIComponent('Roof / South'));
  assert.strictEqual(fetchCalls[1].opts.headers['X-Dataset-Source'], 'pvgis53');
  assert.strictEqual(fetchCalls[1].opts.headers['X-Dataset-Filename'], encodeURIComponent('v53.json'));
  assert.strictEqual(fetchCalls[2].opts.method, undefined, 'Follow-up must be local GET');
  assert(fetchCalls.every(c => c.url === '/api/evidence/datasets'), 'Only local API called');

  // 3. Inputs retained on failure; button disabled in flight and restored finally
  f = createFixture();
  f.els.datasetFilesV6.files = [{ name: 'fail.csv', size: 50 }];
  f.els.datasetFilesV6.value = 'fakepath/fail.csv';
  let btnDisabledDuringUpload = false;
  fetchHandler = async (url, opts) => {
    fetchCalls.push({ url, opts: opts || {} });
    if (opts && opts.method === 'POST') {
      btnDisabledDuringUpload = f.els.datasetUpload.disabled;
      return { ok: false, status: 500, text: async () => 'Upload failed' };
    }
    return { ok: true, json: async () => ({ datasets: [], coverage: [] }) };
  };
  await ed.handleUpload(f.article);
  assert.strictEqual(btnDisabledDuringUpload, true, 'Button disabled inflight');
  assert.strictEqual(f.els.datasetUpload.disabled, false, 'Button restored finally');
  assert.strictEqual(f.els.datasetFilesV6.value, 'fakepath/fail.csv', 'Input retained on failure');

  // 4. Concurrent upload guard & zero POST for empty series or oversized files
  fetchCalls = [];
  fetchHandler = async (url, opts) => {
    fetchCalls.push({ url, opts: opts || {} });
    if (opts && opts.method === 'POST') {
      await ed.handleUpload(f.article); // duplicate concurrent call
      return { ok: true, text: async () => '' };
    }
    return { ok: true, json: async () => ({ datasets: [], coverage: [] }) };
  };
  f.els.datasetFilesV6.files = [{ name: 'test.csv', size: 100 }];
  await ed.handleUpload(f.article);
  assert.strictEqual(fetchCalls.filter(c => c.opts.method === 'POST').length, 1, 'Duplicate concurrent call ignored');

  fetchCalls = [];
  f.els.datasetSeriesLabel.value = '   ';
  f.els.datasetFilesV6.files = [{ name: 'pvgis.csv', size: 100 }];
  await ed.handleUpload(f.article);
  assert.strictEqual(fetchCalls.length, 0, 'Empty series causes zero POST');

  f.els.datasetSeriesLabel.value = 'South';
  f.els.datasetFilesV6.files = [{ name: 'big.csv', size: 70 * 1024 * 1024 }];
  await ed.handleUpload(f.article);
  assert.strictEqual(fetchCalls.filter(c => c.opts.method === 'POST').length, 0, 'Oversized file causes zero POST');

  // 5. Google file optional type
  f.els.datasetFilesV6.files = [];
  f.els.datasetFilesGoogle.files = [{ name: 'solar.tif', size: 500 }];
  fetchCalls = [];
  await ed.handleUpload(f.article);
  assert.strictEqual(fetchCalls[0].opts.headers['X-Dataset-Source'], 'google');

  // 6. Table exactly 5 cells, textContent assigned for untrusted data (setter innerHTML throws)
  const tbody = makeEl('tbody');
  ed.renderTable(tbody, [{
    source: 'pvgis6', filename: '<svg onload=alert(1)>', series_label: 'South', size_bytes: 1024, uploaded_at: '2026-03-01T00:00:00Z',
    summary: { kind: 'hourly', format: 'hourly', start: '2025-01-01', end: '2025-01-02', row_count: 24, warnings: ['<script>xss</script>'] }
  }]);
  assert.strictEqual(tbody.children.length, 1);
  const row = tbody.children[0];
  assert.strictEqual(row.children.length, 5, 'Table must have exactly 5 cells');
  assert(row.children[1].children[0].textContent.includes('<svg'), 'Filename assigned textContent');
  assert(row.children[4].children[1].textContent.includes('<script>'), 'Warning assigned textContent');

  // 7. Monthly summary vs Google Solar JSON and Raster labels (NOT monthly)
  ed.renderTable(tbody, [{ source: 'pvgis53', filename: 'm.csv', series_label: 'S1', summary: { kind: 'monthly_summary', format: 'CSV' } }]);
  assert.strictEqual(tbody.children[0].children[3].textContent, 'Monthly summary (reference only)');

  ed.renderTable(tbody, [{ source: 'google', filename: 'solar.json', series_label: '-', summary: { format: 'JSON' } }]);
  assert.strictEqual(tbody.children[0].children[3].textContent, 'Reference evidence; no hourly coverage');
  assert(!tbody.children[0].children[3].textContent.includes('Monthly'), 'Google Solar JSON must not be labeled Monthly');

  ed.renderTable(tbody, [{ source: 'google', filename: 'raster.tif', series_label: '-', summary: { provider: 'Raster', format: 'GeoTIFF' } }]);
  assert.strictEqual(tbody.children[0].children[3].textContent, 'Raster layer (reference only)');
  assert(!tbody.children[0].children[3].textContent.includes('Monthly'), 'Raster layer must not be labeled Monthly');

  ed.renderTable(tbody, []);
  assert.strictEqual(tbody.children[0].children[0].colSpan, 5, 'Empty table colSpan is 5');

  // 8. Reversible coverage checkbox selection with optional callback
  let selectedDs = null, selectedCb = null;
  const onSelection = (ds, cb) => { selectedDs = ds; selectedCb = cb; };
  const hourlyDs = { source: 'pvgis6', filename: 'h.csv', summary: { kind: 'hourly' } };
  ed.renderTable(tbody, [hourlyDs], onSelection);
  const cb = tbody.children[0].children[0].children[0];
  assert.strictEqual(cb.type, 'checkbox');
  assert.strictEqual(cb.checked, true);
  assert.strictEqual(cb.getAttribute('aria-label'), 'Include in preview coverage (non-calculator input): h.csv');
  cb.checked = false;
  cb.dispatchEvent({ type: 'change' });
  assert.strictEqual(selectedDs, hourlyDs);
  assert.strictEqual(selectedCb, cb);

  // 9. Init idempotent & tab keyboard navigation
  f = createFixture();
  assert.strictEqual(ed.init(), true);
  assert.strictEqual(f.article.dataset.evidenceDatasetsInit, 'true');
  assert.strictEqual(ed.init(), true, 'Init is idempotent');
  f.tabs[0].dispatchEvent({ type: 'keydown', key: 'ArrowRight', preventDefault() {} });
  assert.strictEqual(f.tabs[1].getAttribute('aria-selected'), 'true');
  assert.strictEqual(f.els.datasetPanelV53.hidden, false);
  assert.strictEqual(f.els.datasetPanelV6.hidden, true);

  console.log('Evidence datasets UI tests: 9/9 checks passed');
})().catch(err => { console.error(err); process.exit(1); });
