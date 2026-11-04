'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Diagnostics = require('../feasibility/web/diagnostics.js');
const { HouseholdClient } = require('../feasibility/web/sync.js');
const { mount, Workflow, numbers, validateDraft, csvMetadata, RoofListEditor, YearMenu, SUPPORTED_YEARS } = require('../feasibility/web/onboarding.js');

class Element {
  constructor(tag, doc) {
    this.tag = tag;
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this._text = '';
    this.value = '';
    this.parentNode = null;
    this.hidden = false;
    this.disabled = false;
    this.className = '';
    this.style = {};
  }
  get classList() {
    const el = this;
    return {
      add: (cls) => {
        const classes = new Set((el.className || '').split(' ').filter(Boolean));
        classes.add(cls);
        el.className = Array.from(classes).join(' ');
      },
      remove: (cls) => {
        const classes = new Set((el.className || '').split(' ').filter(Boolean));
        classes.delete(cls);
        el.className = Array.from(classes).join(' ');
      },
      contains: (cls) => (el.className || '').split(' ').includes(cls),
    };
  }
  set textContent(v) {
    this._text = String(v ?? '');
    this.children = [];
  }
  get textContent() {
    if (this.children.length > 0) return this.children.map(c => c.textContent).join('');
    return this._text;
  }
  appendChild(n) {
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    this.children.push(n);
    return n;
  }
  removeChild(n) {
    const i = this.children.indexOf(n);
    if (i !== -1) {
      this.children.splice(i, 1);
      n.parentNode = null;
    }
    return n;
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
  }
  getAttribute(k) {
    return this.attrs[k] !== undefined ? this.attrs[k] : null;
  }
  removeAttribute(k) {
    delete this.attrs[k];
  }
  addEventListener(k, f) {
    const prev = this.listeners[k];
    this.listeners[k] = prev ? (e) => { prev.call(this, e); f.call(this, e); } : f;
  }
  click() {
    if (typeof this.onclick === 'function') this.onclick({ currentTarget: this });
  }
  select() {}
  querySelector(selector) {
    if (selector.startsWith('.')) {
      const cls = selector.slice(1);
      for (const c of this.children) {
        if ((c.className || '').split(' ').includes(cls)) return c;
        const found = c.querySelector(selector);
        if (found) return found;
      }
    }
    if (selector.startsWith('#')) {
      const id = selector.slice(1);
      for (const c of this.children) {
        if (c.id === id) return c;
        const found = c.querySelector(selector);
        if (found) return found;
      }
    }
    for (const c of this.children) {
      if (c.tag === selector.toLowerCase()) return c;
      const found = c.querySelector(selector);
      if (found) return found;
    }
    return null;
  }
  querySelectorAll(selector) {
    const matches = [];
    const walk = node => {
      for (const c of node.children) {
        if (selector.startsWith('.')) {
          if ((c.className || '').split(' ').includes(selector.slice(1))) matches.push(c);
        } else if (c.tag === selector.toLowerCase()) {
          matches.push(c);
        }
        walk(c);
      }
    };
    walk(this);
    return matches;
  }
}

Element.prototype.reportValidity = () => true;
Element.prototype.reset = function () {
  const visit = n => {
    if (n.defaultValue !== undefined) n.value = n.defaultValue;
    if (n.defaultChecked !== undefined) n.checked = n.defaultChecked;
    for (const c of n.children) visit(c);
  };
  visit(this);
};

const createMockDoc = () => {
  const doc = {
    createElement: tag => new Element(tag, doc),
    body: null,
    execCommand: () => true,
  };
  doc.body = new Element('body', doc);
  return doc;
};

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
  headers: { get: () => null }
});

function defaultProfile() {
  return {
    country: 'IT',
    timezone: 'Europe/Rome',
    location: { latitude: 45, longitude: 9 },
    consumption: { kind: 'monthly', acknowledged: true },
    roofs: [],
    thermal: { end_uses: [], electric_loads: [] },
    fiscal: { parameters: { confirmed: true } },
    market: { valuation_mode: 'flat' }
  };
}

function setupHelper(initialDoc = null) {
  const doc = createMockDoc();
  globalThis.document = doc;
  const root = new Element('section', doc);
  let rev = 0, savedDoc = null, savedLoad = null;
  const store = new Map();
  if (initialDoc) store.set(initialDoc.id, initialDoc);

  const client = new HouseholdClient({
    fetch: async (path, opts) => {
      const b = opts.body ? JSON.parse(opts.body) : null;
      if (path === '/api/session') return response({ csrf_token: 't' });
      if (path === '/api/profiles') {
        if (opts.method === 'POST') {
          const nid = 'p_' + (++rev);
          store.set(nid, defaultProfile());
          return response({ id: nid, revision: 'r' + rev });
        }
        return response({ profiles: Array.from(store.keys()).map(id => ({ id })) });
      }
      if (path.endsWith('/validation')) return response({ ready: false, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
      const id = path.split('/').pop();
      if (opts.method === 'PATCH') { savedDoc = b.profile; rev++; if (id) store.set(id, savedDoc); }
      return response({ id: id || 'p_1', revision: 'r' + rev, profile: store.get(id) || savedDoc || defaultProfile() });
    }
  });

  const setup = mount({ root, client });
  const all = () => {
    const res = [];
    const v = n => { res.push(n); for (const c of n.children) v(c); };
    v(root);
    return res;
  };
  const click = async txt => {
    const b = all().find(n => n.tag === 'button' && (n.textContent === txt || n.textContent?.includes(txt)));
    assert.ok(b, 'btn: ' + txt);
    await b.onclick();
  };
  const origSave = setup.workflow.save.bind(setup.workflow);
  setup.workflow.save = async (p, l, c) => { savedDoc = p; savedLoad = l; return origSave(p, l, c); };
  const fillBase = () => {
    for (const [k, v] of Object.entries({
      country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
      reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025',
      technology: 'cSi', zone: 'CSUD', series: 'CSUD', valuation: 'flat',
      window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
      effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
      sources: 'Synthetic test', ev_action: 'unchanged', cooling_action: 'unchanged',
      retail_commodity: '0.135', retail_regulated: '0.045', baseline_cooling_kwh: '0',
      coverage_start: '2025-01-01', coverage_end: '2026-01-01'
    })) if (setup.fields[k]) setup.fields[k].value = v;
    for (const k of ['gross', 'ack', 'confirmed']) if (setup.fields[k]) setup.fields[k].checked = true;
  };
  return { doc, root, client, setup, f: setup.fields, all, click, fillBase };
}

test('Onboarding compile failure renders expandable diagnostic report, copy, download, and issue link next to button', async () => {
  const h = setupHelper();
  await h.click('Create profile');
  h.fillBase();
  h.f.years.value = '2020';
  h.f.providerConsent.checked = true;

  const origFetch = h.client.fetcher;
  h.client.fetcher = async (path, opts) => {
    if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
    if (path.endsWith('/solar-jobs')) return response({ id: 'job-solar-compile-456' });
    return origFetch(path, opts);
  };

  await h.click('Save and review draft');
  await h.click('Check solar coverage');

  const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
  assert.ok(step3Btn, 'Step 3 button found');

  const failureError = Object.assign(
    new Error('COMPUTE_EXCEPTION: Internal simulation mathematical error'),
    {
      code: 'COMPUTE_EXCEPTION',
      jobId: 'job-solar-compile-456',
      version: '0.3.0',
      modelBuildId: 'deadbeef01234567',
      repoUrl: 'https://github.com/my-test-org/pv-system',
      diagnostic: {
        job_id: 'job-solar-compile-456',
        error_code: 'COMPUTE_EXCEPTION',
        error_name: 'ZeroDivisionError',
        operation: 'solar',
        version: '0.3.0',
        model_build_id: 'deadbeef01234567',
        actionable_message: 'Calculation engine encountered an internal mathematical or logic error. Review inputs or inspect the sanitized bug report.',
        repo_url: 'https://github.com/my-test-org/pv-system',
        traceback: [
          'feasibility/solar_candidate.py:105 in run',
          'feasibility/dataset_compile.py:169 in compile_dataset',
        ],
      },
    }
  );

  h.client.waitJob = async () => {
    throw failureError;
  };

  await step3Btn.onclick();

  // Verify button reset
  assert.equal(step3Btn.disabled, false);
  assert.equal(step3Btn.attrs['aria-busy'], undefined);

  // Verify error container next to button
  const controls = step3Btn.parentNode;
  const errEl = controls.children.find(c => c.className?.includes('action-error'));
  assert.ok(errEl, 'action-error element exists next to step 3 button');
  assert.equal(errEl.hidden, false, 'action-error is visible');
  assert.ok(errEl.className?.includes('has-diagnostic'), 'has-diagnostic class applied');

  // Verify error header
  const header = errEl.querySelector('.error-header');
  assert.ok(header, 'Error header exists');
  assert.match(header.textContent, /Setup operation failed/);
  assert.match(header.textContent, /Calculation engine encountered an internal mathematical or logic error/);

  // Verify expandable details element
  const details = errEl.querySelector('.diagnostic-details');
  assert.ok(details, 'Expandable <details> element exists');
  assert.equal(details.tag, 'details');
  assert.ok(details.className?.includes('job-failure-diagnostic'));

  const summary = details.querySelector('.diagnostic-summary');
  assert.ok(summary, '<summary> element exists');
  assert.equal(summary.tag, 'summary');
  assert.match(summary.textContent, /Sanitized diagnostic details/);

  // Verify diagnostic pre
  const pre = details.querySelector('.diagnostic-pre');
  assert.ok(pre, '<pre> diagnostic element exists');
  assert.equal(pre.getAttribute('tabindex'), '0');
  assert.equal(pre.getAttribute('aria-label'), 'Sanitized diagnostic bug report');

  const reportText = pre.textContent;
  assert.match(reportText, /### PV Lab Bug Report/);
  assert.match(reportText, /\*\*Version:\*\*\s*0\.3\.0/);
  assert.match(reportText, /\*\*Model Build ID:\*\*\s*deadbeef01234567/);
  assert.match(reportText, /\*\*Job Correlation ID:\*\*\s*job-solar-compile-456/);
  assert.match(reportText, /\*\*Operation:\*\*\s*solar/);
  assert.match(reportText, /\*\*Error Code:\*\*\s*COMPUTE_EXCEPTION/);
  assert.match(reportText, /\*\*Error Type:\*\*\s*ZeroDivisionError/);
  assert.match(reportText, /\*\*Repository:\*\*\s*https:\/\/github\.com\/my-test-org\/pv-system/);
  assert.match(reportText, /Stack Summary \(repo-relative\)/);
  assert.match(reportText, /feasibility\/solar_candidate\.py:105 in run/);
  assert.match(reportText, /feasibility\/dataset_compile\.py:169 in compile_dataset/);

  // Verify copy action
  const copyBtn = details.querySelector('.copy-btn');
  assert.ok(copyBtn, 'Copy bug report button exists');
  assert.equal(copyBtn.textContent, 'Copy bug report');

  let copiedText = '';
  Object.defineProperty(globalThis.navigator, 'clipboard', {
    value: { writeText: async t => { copiedText = t; } },
    configurable: true,
  });

  await copyBtn.onclick();
  assert.equal(copiedText, reportText);
  const statusSpan = details.querySelector('.copy-status');
  assert.ok(statusSpan);
  assert.equal(statusSpan.textContent, 'Copied to clipboard');

  // Verify download action
  const downloadBtn = details.querySelector('.download-btn');
  assert.ok(downloadBtn, 'Download bug report button exists');
  assert.equal(downloadBtn.textContent, 'Download bug report');

  let downloadedPayload = null;
  globalThis.download = (content, filename, mime) => {
    downloadedPayload = { content, filename, mime };
  };
  downloadBtn.click();
  assert.ok(downloadedPayload);
  assert.equal(downloadedPayload.filename, 'pv-lab-bug-report.txt');
  assert.equal(downloadedPayload.content, reportText);
  assert.equal(downloadedPayload.mime, 'text/plain; charset=utf-8');
  assert.equal(statusSpan.textContent, 'Downloaded bug report');

  // Verify GitHub issue link
  const issueLink = details.querySelector('.issue-link');
  assert.ok(issueLink, 'GitHub issue link exists');
  assert.equal(issueLink.href, 'https://github.com/my-test-org/pv-system/issues/new?template=bug_report.md');
  assert.equal(issueLink.getAttribute('target'), '_blank');
  assert.equal(issueLink.getAttribute('rel'), 'noopener noreferrer');
  assert.equal(issueLink.textContent, 'Open GitHub issue →');
});

test('Setup job error safety: private coordinates, tokens, and local paths are never leaked', async () => {
  const h = setupHelper();
  await h.click('Create profile');
  h.fillBase();
  h.f.years.value = '2020';
  h.f.providerConsent.checked = true;

  const origFetch = h.client.fetcher;
  h.client.fetcher = async (path, opts) => {
    if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
    if (path.endsWith('/solar-jobs')) return response({ id: 'job-leak-check' });
    return origFetch(path, opts);
  };

  await h.click('Save and review draft');
  await h.click('Check solar coverage');

  const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
  const secretData = 'LAT_45.12345_LON_9.67890_AUTH_TOKEN_SECRET_XYZ';

  h.client.waitJob = async () => {
    throw Object.assign(
      new Error(`COMPUTE_EXCEPTION: Internal failure with ${secretData}`),
      {
        code: 'COMPUTE_EXCEPTION',
        jobId: 'job-leak-check',
        diagnostic: {
          job_id: 'job-leak-check',
          error_code: 'COMPUTE_EXCEPTION',
          error_name: 'KeyError',
          operation: 'solar',
          actionable_message: 'Calculation engine encountered an internal mathematical or logic error. Review inputs or inspect the sanitized bug report.',
          traceback: [
            `/synthetic-fixture/secret/path/foo.py:10 in test_${secretData}`,
            'feasibility/solar_candidate.py:50 in run',
          ],
        },
      }
    );
  };

  await step3Btn.onclick();

  const errEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-error'));
  const pre = errEl.querySelector('.diagnostic-pre');
  const reportText = pre.textContent;

  // Strict omission checks on bug report
  assert.doesNotMatch(reportText, new RegExp(secretData));
  assert.doesNotMatch(reportText, /\/home\//);
  assert.doesNotMatch(reportText, /\/run\/media\//);
  assert.doesNotMatch(reportText, /\/Users\//);

  // Stack summary only includes repo-relative feasibility/ frames
  assert.match(reportText, /feasibility\/solar_candidate\.py:50 in run/);
  assert.doesNotMatch(reportText, /foo\.py/);

  // Container text content check
  assert.doesNotMatch(errEl.textContent, new RegExp(secretData));
});

test('Setup job error without valid repoUrl omits GitHub issue link', async () => {
  const h = setupHelper();
  await h.click('Create profile');
  h.fillBase();
  h.f.years.value = '2020';
  h.f.providerConsent.checked = true;

  const origFetch = h.client.fetcher;
  h.client.fetcher = async (path, opts) => {
    if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
    if (path.endsWith('/solar-jobs')) return response({ id: 'job-no-repo' });
    return origFetch(path, opts);
  };

  await h.click('Save and review draft');
  await h.click('Check solar coverage');

  const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');

  h.client.waitJob = async () => {
    throw Object.assign(new Error('JOB_FAILED: job failed'), {
      code: 'JOB_FAILED',
      jobId: 'job-no-repo',
      repoUrl: null,
      diagnostic: {
        job_id: 'job-no-repo',
        error_code: 'JOB_FAILED',
        error_name: 'RuntimeError',
        operation: 'solar',
        repo_url: null,
      },
    });
  };

  await step3Btn.onclick();

  const errEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-error'));
  const pre = errEl.querySelector('.diagnostic-pre');
  assert.ok(pre, 'Diagnostic report is still rendered');
  assert.doesNotMatch(pre.textContent, /Repository:/);

  const issueLink = errEl.querySelector('.issue-link');
  assert.equal(issueLink, null, 'Issue link is omitted when repoUrl is null');
});

test('Normal validation errors show readable feedback without diagnostic block', async () => {
  const h = setupHelper();
  await h.click('Create profile');
  h.fillBase();
  h.f.providerConsent.checked = false; // missing consent blocks

  const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
  const errEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-error'));

  await step3Btn.onclick();

  assert.equal(errEl.hidden, false, 'action-error is visible');
  assert.match(errEl.textContent, /Review coordinate destinations and check the authorization box/);

  // Diagnostic block must NOT be rendered for normal validation errors
  const diagDetails = errEl.querySelector('.job-failure-diagnostic');
  assert.equal(diagDetails, null, 'No diagnostic details for normal validation error');
  const copyBtn = errEl.querySelector('.copy-btn');
  assert.equal(copyBtn, null, 'No copy button for normal validation error');
  const downloadBtn = errEl.querySelector('.download-btn');
  assert.equal(downloadBtn, null, 'No download button for normal validation error');
});

test('Retrying or clicking another action clears earlier error state', async () => {
  const h = setupHelper();
  await h.click('Create profile');
  h.fillBase();
  h.f.years.value = '2020';
  h.f.providerConsent.checked = true;

  const origFetch = h.client.fetcher;
  h.client.fetcher = async (path, opts) => {
    if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
    if (path.endsWith('/solar-jobs')) return response({ id: 'job-retry' });
    return origFetch(path, opts);
  };

  await h.click('Save and review draft');
  await h.click('Check solar coverage');

  const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
  const errEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-error'));

  // 1. Fail first
  h.client.waitJob = async () => {
    throw Object.assign(new Error('COMPUTE_TIMEOUT: Deadline exceeded'), {
      code: 'COMPUTE_TIMEOUT',
      jobId: 'job-timeout-1',
      diagnostic: { job_id: 'job-timeout-1', error_code: 'COMPUTE_TIMEOUT', operation: 'solar' }
    });
  };

  await step3Btn.onclick();
  assert.equal(errEl.hidden, false);
  assert.ok(errEl.querySelector('.job-failure-diagnostic'));

  // 2. Click Save draft again: all action errors are cleared on click
  h.client.waitJob = async () => ({ state: 'complete' });
  const step1Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Save and review draft');
  await step1Btn.onclick();

  assert.equal(errEl.hidden, true, 'previous step 3 error is hidden');
  assert.equal(errEl.children.length, 0, 'previous step 3 error elements cleared');
});

test('Shared Diagnostics module handles isJobFailure, report formatting, and allowlists', () => {
  assert.ok(Diagnostics.ALLOWLISTED_ERROR_CODES.has('COMPUTE_EXCEPTION'));
  assert.ok(Diagnostics.ALLOWLISTED_ERROR_CODES.has('SOLAR_TIMEOUT'));
  assert.ok(Diagnostics.ALLOWLISTED_OPERATIONS.has('solar'));
  assert.ok(Diagnostics.ALLOWLISTED_OPERATIONS.has('evaluate'));

  assert.equal(Diagnostics.isJobFailure(null), false);
  assert.equal(Diagnostics.isJobFailure(new Error('LOCATION_REQUIRED')), false);
  assert.equal(Diagnostics.isJobFailure({ code: 'COMPUTE_EXCEPTION' }), true);
  assert.equal(Diagnostics.isJobFailure({ jobId: 'j-1' }), true);
  assert.equal(Diagnostics.isJobFailure(new Error('JOB_FAILED: job failed')), true);

  const report = Diagnostics.generateBugReport({
    code: 'SOLAR_TIMEOUT',
    jobId: 'job-xyz-123',
    diagnostic: {
      job_id: 'job-xyz-123',
      error_code: 'SOLAR_TIMEOUT',
      operation: 'solar',
      error_name: 'TimeoutError',
    }
  });

  assert.match(report, /Operation:\*\*\s*solar/);
  assert.match(report, /Error Code:\*\*\s*SOLAR_TIMEOUT/);
  assert.match(report, /Error Type:\*\*\s*TimeoutError/);
});

test('Browser-style regression (no CommonJS): script delivery, script order in index.html, same-context load without collision, and report controls', async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');

  // 1. Verify STATIC_ASSETS in server.py
  const serverPy = fs.readFileSync(path.join(__dirname, '../feasibility/server.py'), 'utf8');
  assert.match(serverPy, /'\/diagnostics\.js'\s*:\s*'web\/diagnostics\.js'/, 'STATIC_ASSETS registers /diagnostics.js');

  // 2. Verify script tags in index.html
  const indexHtml = fs.readFileSync(path.join(__dirname, '../feasibility/web/index.html'), 'utf8');
  const diagIdx = indexHtml.indexOf('<script src="/diagnostics.js"></script>');
  const onbIdx = indexHtml.indexOf('<script src="/onboarding.js"></script>');
  assert.ok(diagIdx !== -1, 'index.html includes diagnostics.js script tag');
  assert.ok(onbIdx !== -1, 'index.html includes onboarding.js script tag');
  assert.ok(diagIdx < onbIdx, 'diagnostics.js is loaded before onboarding.js');

  // 3. Load classic scripts in SAME VM context with no CommonJS (no module, no require)
  const diagSrc = fs.readFileSync(path.join(__dirname, '../feasibility/web/diagnostics.js'), 'utf8');
  const onbSrc = fs.readFileSync(path.join(__dirname, '../feasibility/web/onboarding.js'), 'utf8');

  const win = {};
  win.window = win;
  const mockDoc = createMockDoc();
  win.document = mockDoc;

  const sandbox = {
    window: win,
    globalThis: win,
    document: mockDoc,
    console,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(sandbox);

  // Run diagnostics.js first
  vm.runInContext(diagSrc, sandbox);
  assert.ok(win.Diagnostics, 'window.Diagnostics defined by diagnostics.js');
  assert.equal(typeof win.Diagnostics.renderDiagnosticReport, 'function');
  assert.equal(typeof win.Diagnostics.generateBugReport, 'function');
  assert.equal(typeof win.Diagnostics.isJobFailure, 'function');

  // Run onboarding.js in the SAME context without collision
  vm.runInContext(onbSrc, sandbox);
  assert.ok(win.Onboarding, 'window.Onboarding defined without identifier clash');
  assert.equal(win.Onboarding.Diagnostics, win.Diagnostics, 'Onboarding accesses loaded Diagnostics');

  // 4. Verify report controls and fallback download in browser environment
  const err = Object.assign(new Error('COMPUTE_EXCEPTION: internal error'), {
    code: 'COMPUTE_EXCEPTION',
    jobId: 'job-browser-test-1234',
    modelBuildId: 'deadbeef12345678',
    diagnostic: {
      job_id: 'job-browser-test-1234',
      error_code: 'COMPUTE_EXCEPTION',
      error_name: 'ZeroDivisionError',
      operation: 'solar',
      model_build_id: 'deadbeef12345678',
    }
  });

  const rendered = win.Diagnostics.renderDiagnosticReport(err, { doc: mockDoc, expandable: true });
  assert.equal(rendered.tag, 'details');
  const dlBtn = rendered.querySelector('.download-btn');
  const statusSpan = rendered.querySelector('.copy-status');
  assert.ok(dlBtn);
  assert.ok(statusSpan);

  // Truthful unavailable status when no download mechanism exists
  dlBtn.click();
  assert.equal(statusSpan.textContent, 'Download unavailable; copy report above');

  // Blob anchor download fallback when Blob/URL are present
  let clickedHref = null, clickedDownload = null;
  win.Blob = class MockBlob {
    constructor(parts, opts) {
      this.parts = parts;
      this.type = opts?.type;
    }
  };
  win.URL = {
    createObjectURL: blob => 'blob:pv-lab/' + Math.random(),
    revokeObjectURL: url => {},
  };
  const origCreateElement = mockDoc.createElement.bind(mockDoc);
  mockDoc.createElement = tag => {
    const el = origCreateElement(tag);
    if (tag === 'a') {
      el.click = () => {
        clickedHref = el.href;
        clickedDownload = el.download;
      };
    }
    return el;
  };

  dlBtn.click();
  assert.ok(clickedHref?.startsWith('blob:pv-lab/'));
  assert.equal(clickedDownload, 'pv-lab-bug-report.txt');
  assert.equal(statusSpan.textContent, 'Downloaded bug report');
});


test('Setup failure with SYNTHETIC_COMPLETION_REQUIRED renders actionable message and sanitized bug report', async () => {
  const h = setupHelper();
  await h.click('Create profile');
  h.fillBase();
  h.f.years.value = '2020';
  h.f.providerConsent.checked = true;

  const origFetch = h.client.fetcher;
  h.client.fetcher = async (path, opts) => {
    if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
    if (path.endsWith('/solar-jobs')) return response({ id: 'job-synth-123' });
    return origFetch(path, opts);
  };

  await h.click('Save and review draft');
  await h.click('Check solar coverage');

  const syntheticError = Object.assign(
    new Error('SYNTHETIC_COMPLETION_REQUIRED: Interval training data does not cover required month/hour cells; enable explicit synthetic completion or supply complete coverage.'),
    {
      code: 'SYNTHETIC_COMPLETION_REQUIRED',
      jobId: 'job-synth-123',
      version: '0.4.1',
      modelBuildId: 'fedcba9876543210',
      repoUrl: 'https://github.com/my-test-org/pv-system',
      diagnostic: {
        job_id: 'job-synth-123',
        error_code: 'SYNTHETIC_COMPLETION_REQUIRED',
        error_name: 'ValueError',
        operation: 'solar',
        version: '0.4.1',
        model_build_id: 'fedcba9876543210',
        repo_url: 'https://github.com/my-test-org/pv-system',
        traceback: [
          'feasibility/solar_candidate.py:98 in build_candidate',
          'feasibility/load_projection.py:280 in project_load',
        ],
      },
    }
  );

  h.setup.workflow.generate = async () => {
    throw syntheticError;
  };

  await h.click('Generate solar data and compile');

  const step3Btn = h.all().find(n => n.tag === 'button' && (n.textContent === 'Generate solar data and compile' || n.textContent.includes('Generate solar data and compile')));
  assert.ok(step3Btn, 'Step 3 button found');

  const controls = step3Btn.parentNode;
  const errEl = controls.children.find(c => c.className?.includes('action-error'));
  assert.ok(errEl, 'action-error element exists next to step 3 button');
  assert.equal(errEl.hidden, false, 'action-error is visible');
  assert.ok(errEl.className?.includes('has-diagnostic'), 'has-diagnostic class applied');

  const header = errEl.querySelector('.error-header');
  assert.ok(header, 'Error header exists');
  assert.match(header.textContent, /Setup operation failed/);
  assert.match(header.textContent, /Interval training data does not cover required month\/hour cells; enable explicit synthetic completion or supply complete coverage\./);

  const details = errEl.querySelector('.diagnostic-details');
  assert.ok(details, 'Expandable <details> element exists');
  const pre = details.querySelector('.diagnostic-pre');
  assert.ok(pre, '<pre> diagnostic element exists');
  const report = pre.textContent;

  assert.match(report, /### PV Lab Bug Report/);
  assert.match(report, /\*\*Error Code:\*\*\s*SYNTHETIC_COMPLETION_REQUIRED/);
  assert.match(report, /\*\*Actionable Message:\*\*\s*Interval training data does not cover required month\/hour cells; enable explicit synthetic completion or supply complete coverage\./);
  assert.match(report, /feasibility\/load_projection\.py:280 in project_load/);
  assert.ok(!report.includes('missing month/hour shape cell'));
});


test('Negative test: arbitrary actionable_message secret string is excluded from report and UI', () => {
  const secretString = 'SECRET_INJECTED_ACTIONABLE_MESSAGE_LEAK_TEST_42';
  const infoWithSecret = {
    code: 'COMPUTE_EXCEPTION',
    actionableMessage: secretString,
    diagnostic: {
      job_id: 'job-sec-999',
      error_code: 'COMPUTE_EXCEPTION',
      error_name: 'ComputationError',
      operation: 'evaluate',
      actionable_message: secretString,
    }
  };

  const report = Diagnostics.generateBugReport(infoWithSecret);
  assert.ok(!report.includes(secretString), 'Arbitrary actionableMessage secret must never be included in bug report');
  assert.ok(report.includes('Calculation engine encountered an internal mathematical or logic error.'), 'Allowlisted code-to-message lookup must be used instead');
});
