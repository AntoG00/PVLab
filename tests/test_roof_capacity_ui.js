const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const appSource = fs.readFileSync('feasibility/web/app.js', 'utf-8');

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
    this.dataset = {};
    this.className = '';
    this.classList = {
      add: cls => {
        const set = new Set((this.className || '').split(' ').filter(Boolean));
        set.add(cls);
        this.className = [...set].join(' ');
      },
      remove: cls => {
        const set = new Set((this.className || '').split(' ').filter(Boolean));
        set.delete(cls);
        this.className = [...set].join(' ');
      },
      contains: cls => (this.className || '').split(' ').filter(Boolean).includes(cls),
      toggle: (cls, force) => {
        if (force === true) this.classList.add(cls);
        else if (force === false) this.classList.remove(cls);
        else if (this.classList.contains(cls)) this.classList.remove(cls);
        else this.classList.add(cls);
      },
    };
    this.customValidity = '';
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
  contains(n) {
    let curr = n;
    while (curr) {
      if (curr === this) return true;
      curr = curr.parentNode;
    }
    return false;
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
  setCustomValidity(msg) {
    this.customValidity = String(msg || '');
  }
  reportValidity() {
    return !this.customValidity;
  }
  addEventListener(k, f) {
    this.listeners[k] = f;
  }
  querySelector(selector) {
    if (selector.startsWith('.')) {
      const cls = selector.slice(1);
      for (const c of this.children) {
        if (c.classList.contains(cls)) return c;
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
}

const createMockDoc = () => {
  const doc = {
    elements: new Map(),
    createElement: tag => new Element(tag, doc),
    getElementById: id => doc.elements.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  return doc;
};

function extractFunction(name) {
  let start = appSource.indexOf('function ' + name + '(');
  assert.ok(start >= 0, `function ${name} not found in app.js`);
  if (appSource.slice(start - 6, start) === 'async ') start -= 6;
  const rest = appSource.slice(start + 1);
  const match = /\n(?:async )?function /.exec(rest);
  return appSource.slice(start, match ? start + 1 + match.index : undefined);
}

test('UI preflight validation blocks submission and displays feedback when panel_watts increases with saved roof counts', async () => {
  const doc = createMockDoc();
  const rootControls = new Element('div', doc);
  rootControls.id = 'profileRoofControls';
  doc.elements.set('profileRoofControls', rootControls);

  const statusEl = new Element('div', doc);
  statusEl.id = 'status';
  doc.elements.set('status', statusEl);

  const errorEl = new Element('div', doc);
  errorEl.id = 'error';
  errorEl.hidden = true;
  doc.elements.set('error', errorEl);

  const profile = {
    id: 'p_test',
    roofs: [
      { id: 'plane0', label: 'South Slope', max_kwp: 2.0 },
      { id: 'plane1', label: 'East Dormer', max_kwp: 1.2 },
    ],
  };

  let apiCalls = [];
  const fakeApi = async (path, payload) => {
    apiCalls.push({ path, payload });
    return { ok: true, result: {} };
  };

  const sandbox = {
    activeResultProfile: profile,
    settings: {
      panel_watts: 400,
      roof_panels: { plane0: 5, plane1: 3 }, // 5*400=2000W=2.0kWp (exact capacity), 3*400=1200W=1.2kWp (exact capacity)
      battery_kwh: 0,
      battery_modules: 0,
    },
    quote: { line_items: [] },
    requestId: 0,
    timer: null,
    clearTimeout: globalThis.clearTimeout,
    setTimeout: globalThis.setTimeout,
    document: doc,
    $: id => doc.elements.get(id) || null,
    api: fakeApi,
    deepClone: obj => JSON.parse(JSON.stringify(obj)),
    normalizeBatterySettings: () => {},
    inputFingerprint: () => 'fp_test',
    invalidateLoanSolve: () => {},
    markBatterySizingStale: () => {},
    markSolarSizingStale: () => {},
    markOptimizerStale: () => {},
    cleanSettingsForBackend: () => {},
    shouldBlockAcRecompute: () => false,
    flushSharedChangesWithoutEvaluation: () => {},
    stageSharedChanges: () => {},
    sharedSync: null,
    optimizerResult: null,
    candidates: null,
    render: () => {},
    setupDayhour: () => {},
    refreshDayhour: () => {},
    nf: (v, d) => Number(v).toFixed(d),
    fmt: v => String(v),
    showError: err => {
      errorEl.textContent = err.message || String(err);
      errorEl.hidden = false;
    },
    updateActionableWarnings: () => {},
    optimizerPrefs: {},
  };

  const ctx = vm.createContext(sandbox);

  // Load functions under test
  const fnNames = [
    'getRoofValidationError',
    'shouldBlockRoofRecompute',
    'showRoofValidationError',
    'renderProfileRoofControls',
    'schedule',
    'recompute',
  ];
  for (const name of fnNames) {
    vm.runInContext(extractFunction(name), ctx);
  }

  // 1. Initial state: 400 W, exact maximum capacity on both roofs
  assert.equal(ctx.shouldBlockRoofRecompute(), false);
  assert.equal(ctx.getRoofValidationError(), null);

  ctx.renderProfileRoofControls();
  const labels = rootControls.children.filter(c => c.tag === 'label');
  const inputs = labels.map(label => label.children[0]);
  assert.equal(inputs.length, 2);
  assert.equal(inputs[0].dataset.roofId, 'plane0');
  assert.equal(inputs[0].value, '5');
  assert.equal(inputs[0].max, '5');
  assert.equal(inputs[0].getAttribute('aria-invalid'), null);
  assert.equal(inputs[1].dataset.roofId, 'plane1');
  assert.equal(inputs[1].value, '3');
  assert.equal(inputs[1].max, '3');
  assert.equal(inputs[1].getAttribute('aria-invalid'), null);

  // 2. Increase panel rating to 450 W (max for plane0 becomes floor(2000/450)=4, plane1 becomes floor(1200/450)=2)
  ctx.settings.panel_watts = 450;

  // Actual cause demonstration:
  // Before fix, recompute() would run and submit plane0=5 (2.25 kWp > 2.0 kWp) to /api/evaluate,
  // triggering validate_roof_counts line 101 ValueError: Roof 'plane0' capacity exceeds max_kwp 2.0.
  // With fix: preflight detects the violation without silently altering saved inputs:
  assert.equal(ctx.settings.roof_panels.plane0, 5, 'Saved inputs are NOT silently changed');
  assert.equal(ctx.shouldBlockRoofRecompute(), true, 'Preflight detects capacity exceeded');

  const errMsg = ctx.getRoofValidationError();
  assert.match(errMsg, /Roof plane 'South Slope'/);
  assert.match(errMsg, /exceeds 2 kWp maximum capacity \(4 panels\)/);

  // UI rendering updates validation state and marks inputs invalid
  ctx.renderProfileRoofControls();
  const input0 = rootControls.children[0].children[0];
  assert.equal(input0.max, '4');
  assert.equal(input0.value, '5');
  assert.equal(input0.getAttribute('aria-invalid'), 'true');
  assert.equal(input0.classList.contains('is-invalid'), true);

  const roofErrMsg = rootControls.querySelector('.roof-error-message');
  assert.ok(roofErrMsg, 'Error message container exists');
  assert.equal(roofErrMsg.hidden, false);
  assert.match(roofErrMsg.textContent, /South Slope/);

  // 3. Trigger recompute and schedule: verify API submission is BLOCKED
  apiCalls = [];
  await ctx.recompute();
  assert.equal(apiCalls.length, 0, 'recompute blocked API submission to /api/evaluate');
  assert.equal(statusEl.textContent, 'Check inputs: roof capacity exceeded');
  assert.equal(errorEl.hidden, false);
  assert.match(errorEl.textContent, /South Slope/);

  ctx.schedule();
  assert.equal(apiCalls.length, 0, 'schedule blocked evaluation without submitting');

  // 4. Correct plane0 to 4 and plane1 to 2: verify validation clears and evaluation proceeds
  ctx.settings.roof_panels.plane0 = 4;
  ctx.settings.roof_panels.plane1 = 2;

  assert.equal(ctx.shouldBlockRoofRecompute(), false, 'Validation passes when within capacity');
  assert.equal(ctx.getRoofValidationError(), null);

  ctx.renderProfileRoofControls();
  const fixedInput0 = rootControls.children[0].children[0];
  assert.equal(fixedInput0.getAttribute('aria-invalid'), null);
  assert.equal(fixedInput0.classList.contains('is-invalid'), false);

  apiCalls = [];
  await ctx.recompute();
  assert.equal(apiCalls.length, 1, 'recompute successfully calls /api/evaluate when valid');
  assert.equal(apiCalls[0].path, '/api/evaluate');
  assert.equal(apiCalls[0].payload.settings.roof_panels.plane0, 4);
});
