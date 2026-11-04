const test = require('node:test');
const assert = require('node:assert/strict');
const { HouseholdClient } = require('../feasibility/web/sync.js');

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
    this.listeners[k] = f;
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

const createMockDoc = () => {
  const doc = {
    elements: new Map(),
    createElement: tag => new Element(tag, doc),
    getElementById: id => doc.elements.get(id) || null,
    body: null,
    execCommand: () => true,
  };
  doc.body = new Element('body', doc);
  return doc;
};

test('HouseholdClient waitJob propagates allowlisted error codes and metadata on failure', async () => {
  let jobPayload = {
    id: 'job-123',
    profile_id: 'p1',
    revision: 'r1',
    state: 'failed',
    error: 'Calculation engine encountered an internal mathematical or logic error.',
    error_code: 'COMPUTE_EXCEPTION',
    version: '0.1.0',
    model_build_id: 'hash123',
    repo_url: 'https://github.com/test-owner/test-repo',
    diagnostic: {
      job_id: 'job-123',
      error_code: 'COMPUTE_EXCEPTION',
      error_name: 'ZeroDivisionError',
      operation: 'evaluate',
      version: '0.1.0',
    },
  };

  const client = new HouseholdClient({
    fetch: async () => ({
      ok: true,
      status: 200,
      json: async () => jobPayload,
    }),
  });
  client.select('p1', 'r1');

  await assert.rejects(
    client.waitJob('job-123'),
    err => {
      assert.equal(err.code, 'COMPUTE_EXCEPTION');
      assert.equal(err.jobId, 'job-123');
      assert.equal(err.version, '0.1.0');
      assert.equal(err.modelBuildId, 'hash123');
      assert.equal(err.repoUrl, 'https://github.com/test-owner/test-repo');
      assert.equal(err.diagnostic.error_name, 'ZeroDivisionError');
      assert.match(err.message, /COMPUTE_EXCEPTION: Calculation engine/);
      return true;
    }
  );
});

test('HouseholdClient waitJob handles legacy failed jobs without error_code backwards-compatibly', async () => {
  const jobPayload = {
    id: 'legacy-job',
    profile_id: 'p1',
    revision: 'r1',
    state: 'failed',
    error: 'Computation failed',
  };

  const client = new HouseholdClient({
    fetch: async () => ({
      ok: true,
      status: 200,
      json: async () => jobPayload,
    }),
  });
  client.select('p1', 'r1');

  await assert.rejects(
    client.waitJob('legacy-job'),
    err => {
      assert.equal(err.code, undefined);
      assert.equal(err.message, 'JOB_FAILED: job failed; review inputs and retry.');
      return true;
    }
  );
});

test('UI failure state stops Calculating indicators in production and renders reviewable sanitized bug report', async () => {
  const doc = createMockDoc();
  const errorEl = new Element('div', doc);
  errorEl.id = 'error';
  errorEl.hidden = true;
  doc.elements.set('error', errorEl);

  const sysTitle = new Element('h1', doc);
  sysTitle.id = 'systemTitle';
  sysTitle.textContent = 'Calculating…';
  sysTitle.setAttribute('aria-busy', 'true');
  doc.elements.set('systemTitle', sysTitle);

  const statusEl = new Element('div', doc);
  statusEl.id = 'status';
  statusEl.textContent = 'Computing…';
  doc.elements.set('status', statusEl);

  const hpCards = new Element('div', doc);
  hpCards.id = 'hpCardsEl';
  hpCards.textContent = 'Calculating hourly dispatch…';
  doc.elements.set('hpCardsEl', hpCards);

  globalThis.document = doc;
  globalThis.$ = id => doc.elements.get(id);

  // Load app code functions
  const fs = require('node:fs');
  const appCode = fs.readFileSync('feasibility/web/app.js', 'utf-8');

  // Extract allowlists, generateBugReport and showError functions from app.js using Function wrapper
  const allowlistsMatch = appCode.match(/const ALLOWLISTED_ERROR_CODES[\s\S]*?const ALLOWLISTED_ERROR_NAMES[\s\S]*?\]\);/);
  const genMatch = appCode.match(/function generateBugReport\(info\)\{[\s\S]*?\n\}/);
  const showMatch = appCode.match(/const showError=e=>\{[\s\S]*?\n\};/);
  assert.ok(allowlistsMatch, 'allowlists found in app.js');
  assert.ok(genMatch, 'generateBugReport found in app.js');
  assert.ok(showMatch, 'showError found in app.js');
  const combinedScript = `
    ${allowlistsMatch[0]}
    ${genMatch[0]}
    ${showMatch[0]}
    globalThis.generateBugReport = generateBugReport;
    globalThis.showError = showError;
    globalThis.__test_generateBugReport = generateBugReport;
    globalThis.__test_showError = showError;
  `;
  new Function(combinedScript)();

  const generateBugReport = globalThis.__test_generateBugReport;
  const showError = globalThis.__test_showError;

  const privateSensitiveStr = 'LEAK_CHECK_COORDINATES_45.123_SECRET_TOKEN_XYZ';
  const failureError = Object.assign(
    new Error(`COMPUTE_EXCEPTION: Internal failure containing ${privateSensitiveStr}`),
    {
      code: 'COMPUTE_EXCEPTION',
      jobId: 'job-correl-987654321',
      version: '0.1.0',
      modelBuildId: 'build-digest-abc123456789',
      repoUrl: 'https://github.com/user/pv-lab',
      message: `Raw private error message: ${privateSensitiveStr}`,
      diagnostic: {
        job_id: 'job-correl-987654321',
        error_code: 'COMPUTE_EXCEPTION',
        error_name: 'ZeroDivisionError',
        operation: 'evaluate',
        version: '0.1.0',
        model_build_id: 'build-digest-abc123456789',
        actionable_message: 'Calculation engine encountered an internal mathematical error.',
        repo_url: 'https://github.com/user/pv-lab',
        traceback: [
          'feasibility/engine.py:42 in solve',
          'feasibility/worker_pool.py:100 in _tick',
        ],
      },
    }
  );

  // 1. Verify bug report text sanitization
  const reportText = generateBugReport(failureError);
  assert.match(reportText, /### PV Lab Bug Report/);
  assert.match(reportText, /\*\*Version:\*\*\s*0\.1\.0/);
  assert.match(reportText, /\*\*Error Code:\*\*\s*COMPUTE_EXCEPTION/);
  assert.match(reportText, /\*\*Error Type:\*\*\s*ZeroDivisionError/);
  assert.match(reportText, /\*\*Job Correlation ID:\*\*\s*job-correl-987654321/);
  assert.match(reportText, /\*\*Repository:\*\*\s*https:\/\/github\.com\/user\/pv-lab/);
  assert.match(reportText, /Stack Summary \(repo-relative\)/);
  assert.match(reportText, /feasibility\/engine\.py:42 in solve/);

  // Strict omission check: private sensitive string NEVER appears in bug report
  assert.doesNotMatch(reportText, new RegExp(privateSensitiveStr));
  assert.doesNotMatch(reportText, /Platform:\s*Linux/i); // No fabricated platform
  assert.doesNotMatch(reportText, /\/home\//);
  assert.doesNotMatch(reportText, /\/run\/media\//);
  assert.doesNotMatch(reportText, /\/Users\//);

  // 2. Production failure handler stops Calculating indicators (no manual test cleanup)
  showError(failureError);

  assert.equal(sysTitle.textContent, 'Calculation failed');
  assert.equal(sysTitle.getAttribute('aria-busy'), null);
  assert.equal(statusEl.textContent, 'Calculation failed');
  assert.ok(hpCards.querySelector('.bad'));
  assert.equal(hpCards.querySelector('.bad').textContent, 'Failed');

  // 3. Test UI elements
  assert.equal(errorEl.hidden, false);
  const pre = errorEl.querySelector('.diagnostic-pre');
  assert.ok(pre, 'Diagnostic <pre> element exists');
  assert.equal(pre.getAttribute('tabindex'), '0');
  assert.equal(pre.getAttribute('aria-label'), 'Sanitized diagnostic bug report');
  assert.equal(pre.textContent, reportText);

  const copyBtn = errorEl.querySelector('.copy-btn');
  assert.ok(copyBtn, 'Copy bug report button exists');

  const downloadBtn = errorEl.querySelector('.download-btn');
  assert.ok(downloadBtn, 'Download bug report button exists');

  const issueLink = errorEl.querySelector('.issue-link');
  assert.ok(issueLink, 'GitHub issue link exists');
  assert.equal(issueLink.href, 'https://github.com/user/pv-lab/issues/new?template=bug_report.md');
  assert.equal(issueLink.getAttribute('target'), '_blank');
  assert.equal(issueLink.getAttribute('rel'), 'noopener noreferrer');

  // 4. Test copy action: navigator.clipboard
  let copiedText = '';
  Object.defineProperty(globalThis.navigator, 'clipboard', {
    value: {
      writeText: async text => {
        copiedText = text;
      },
    },
    configurable: true,
  });
  await copyBtn.onclick();
  assert.equal(copiedText, reportText);
  const statusSpan = errorEl.querySelector('.copy-status');
  assert.equal(statusSpan.textContent, 'Copied to clipboard');

  // 5. Test copy fallback with execCommand success and failure
  Object.defineProperty(globalThis.navigator, 'clipboard', {
    value: null,
    configurable: true,
  });

  // execCommand returns true
  doc.execCommand = () => true;
  await copyBtn.onclick();
  assert.equal(statusSpan.textContent, 'Copied to clipboard');

  // execCommand returns false
  doc.execCommand = () => false;
  await copyBtn.onclick();
  assert.equal(statusSpan.textContent, 'Copy failed; select text above');

  // 6. Test download action with matching signature
  let downloadedPayload = null;
  globalThis.download = (content, filename, mime) => {
    downloadedPayload = { content, filename, mime };
  };
  downloadBtn.click();
  assert.ok(downloadedPayload);
  assert.equal(downloadedPayload.filename, 'pv-lab-bug-report.txt');
  assert.equal(downloadedPayload.content, reportText);
  assert.equal(downloadedPayload.mime, 'text/plain; charset=utf-8');
});

test('UI failure state handles non-job errors and missing repoUrl gracefully', async () => {
  const doc = createMockDoc();
  const errorEl = new Element('div', doc);
  errorEl.id = 'error';
  errorEl.hidden = true;
  doc.elements.set('error', errorEl);

  globalThis.document = doc;
  globalThis.$ = id => doc.elements.get(id);

  const showError = globalThis.__test_showError;

  // 1. Plain validation error: no diagnostic block
  showError(new Error('Invalid numeric input in field X'));
  assert.equal(errorEl.hidden, false);
  assert.equal(errorEl.textContent, 'Invalid numeric input in field X');
  assert.equal(errorEl.querySelector('.job-failure-diagnostic'), null);

  // 2. PROFILE_NOT_READY error
  showError(new Error('PROFILE_NOT_READY: save draft first'));
  assert.match(errorEl.textContent, /Profile not ready: complete the setup sequence/);
  assert.equal(errorEl.querySelector('.job-failure-diagnostic'), null);

  // 3. Job failure without repo_url: issue link omitted
  const noRepoError = Object.assign(new Error('COMPUTE_EXCEPTION: Internal failure'), {
    code: 'COMPUTE_EXCEPTION',
    jobId: 'job-norepo-1',
    diagnostic: {
      job_id: 'job-norepo-1',
      error_code: 'COMPUTE_EXCEPTION',
      error_name: 'OverflowError',
      operation: 'evaluate',
      version: '0.1.0',
      actionable_message: 'Calculation failed due to numerical overflow.',
    },
  });

  showError(noRepoError);
  assert.ok(errorEl.querySelector('.diagnostic-pre'));
  assert.equal(errorEl.querySelector('.issue-link'), null, 'Issue link omitted when repoUrl is missing');
});
