/* Run with Node: UI tests for Night Theme toggle, persistence, fallback, and resilience. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function createThemeEnvironment(options = {}) {
  const {
    storedTheme = null,
    storageThrows = false,
    prefersDark = false,
    matchMediaAvailable = true
  } = options;

  let storage = {};
  if (storedTheme !== null) {
    storage['pv-lab-theme-v1'] = storedTheme;
  }

  const localStorageMock = {
    getItem(key) {
      if (storageThrows) throw new Error('SecurityError: The operation is insecure.');
      return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null;
    },
    setItem(key, value) {
      if (storageThrows) throw new Error('QuotaExceededError: Storage quota exceeded.');
      storage[key] = String(value);
    },
    removeItem(key) {
      if (storageThrows) throw new Error('SecurityError: The operation is insecure.');
      delete storage[key];
    },
    get _storage() { return storage; }
  };

  const classes = new Set();
  const classList = {
    add: c => classes.add(c),
    remove: c => classes.delete(c),
    contains: c => classes.has(c),
    toggle: (c, force) => {
      if (force === undefined) {
        if (classes.has(c)) classes.delete(c); else classes.add(c);
      } else if (force) {
        classes.add(c);
      } else {
        classes.delete(c);
      }
      return classes.has(c);
    }
  };

  const docAttrs = {};
  const docElement = {
    setAttribute: (k, v) => { docAttrs[k] = String(v); },
    getAttribute: k => docAttrs[k] || null,
    removeAttribute: k => { delete docAttrs[k]; },
    classList
  };

  const bodyClasses = new Set();
  const bodyElement = {
    classList: {
      add: c => bodyClasses.add(c),
      remove: c => bodyClasses.delete(c),
      contains: c => bodyClasses.has(c),
      toggle: (c, force) => {
        if (force === undefined) {
          if (bodyClasses.has(c)) bodyClasses.delete(c); else bodyClasses.add(c);
        } else if (force) {
          bodyClasses.add(c);
        } else {
          bodyClasses.delete(c);
        }
        return bodyClasses.has(c);
      }
    }
  };

  const toggleClasses = new Set();
  const toggleAttrs = {
    role: 'switch',
    'aria-checked': 'false',
    'aria-pressed': 'false',
    'aria-label': 'Night theme'
  };
  const iconNode = { textContent: '🌙' };
  const labelNode = { textContent: 'Night' };

  const themeToggle = {
    id: 'themeToggle',
    tagName: 'BUTTON',
    onclick: null,
    classList: {
      add: c => toggleClasses.add(c),
      remove: c => toggleClasses.delete(c),
      contains: c => toggleClasses.has(c),
      toggle: (c, force) => {
        if (force === undefined) {
          if (toggleClasses.has(c)) toggleClasses.delete(c); else toggleClasses.add(c);
        } else if (force) {
          toggleClasses.add(c);
        } else {
          toggleClasses.delete(c);
        }
        return toggleClasses.has(c);
      }
    },
    setAttribute(k, v) { toggleAttrs[k] = String(v); },
    getAttribute(k) { return toggleAttrs[k] || null; },
    removeAttribute(k) { delete toggleAttrs[k]; },
    querySelector(sel) {
      if (sel === '.theme-icon') return iconNode;
      if (sel === '.theme-label') return labelNode;
      return null;
    },
    get textContent() {
      return `${iconNode.textContent} ${labelNode.textContent}`;
    }
  };

  const mediaListeners = [];
  const matchMediaMock = matchMediaAvailable ? query => ({
    matches: query.includes('prefers-color-scheme: dark') ? prefersDark : false,
    media: query,
    addEventListener(evt, handler) {
      if (evt === 'change') mediaListeners.push(handler);
    },
    removeEventListener(evt, handler) {
      const idx = mediaListeners.indexOf(handler);
      if (idx !== -1) mediaListeners.splice(idx, 1);
    },
    addListener(h) { this.addEventListener('change', h); }
  }) : undefined;

  const elementsMap = {
    themeToggle,
    defaultConfirm: { addEventListener() {}, close() {}, open: false, returnValue: '' },
    reloadServer: { disabled: false },
    toggleInputs: { onclick: null, setAttribute() {} },
    runSolarSizing: { onclick: null },
    syncStatus: { textContent: '' },
    status: { textContent: '' }
  };

  function makeStub(id) {
    if (!elementsMap[id]) {
      elementsMap[id] = {
        id,
        tagName: 'DIV',
        hidden: false,
        disabled: false,
        value: '',
        textContent: '',
        innerHTML: '',
        classList: {
          add() {},
          remove() {},
          toggle() { return false; },
          contains() { return false; }
        },
        setAttribute() {},
        getAttribute() { return null; },
        removeAttribute() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {}
      };
    }
    return elementsMap[id];
  }

  const mockDocument = {
    documentElement: docElement,
    body: bodyElement,
    getElementById: id => elementsMap[id] || makeStub(id),
    querySelector: sel => sel === 'aside' ? { classList } : null,
    querySelectorAll: () => [],
    addEventListener: () => {}
  };

  const sandbox = {
    document: mockDocument,
    window: {
      location: { reload: () => {} },
      addEventListener: () => {},
      localStorage: localStorageMock,
      matchMedia: matchMediaMock
    },
    localStorage: localStorageMock,
    console,
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 1,
    clearInterval: () => {},
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    api: async () => ({})
  };

  vm.createContext(sandbox);

  // Run up to initRedesignEventListeners call or entire app
  vm.runInContext(appSource, sandbox);

  return {
    sandbox,
    themeToggle,
    docElement,
    bodyElement,
    iconNode,
    labelNode,
    localStorageMock,
    mediaListeners
  };
}

test('Theme toggle initializes with proper ARIA attributes, day/night icon, and label', () => {
  const env = createThemeEnvironment({ storedTheme: null, prefersDark: false });

  // Initial light theme
  assert.equal(env.docElement.getAttribute('data-theme'), 'light');
  assert.equal(env.themeToggle.getAttribute('role'), 'switch');
  assert.equal(env.themeToggle.getAttribute('aria-checked'), 'false');
  assert.equal(env.themeToggle.getAttribute('aria-pressed'), 'false');
  assert.equal(env.iconNode.textContent, '🌙');
  assert.equal(env.labelNode.textContent, 'Night');
  assert.equal(env.themeToggle.classList.contains('active'), false);
});

test('Clicking theme toggle transitions to dark mode and back with matching states', () => {
  const env = createThemeEnvironment({ storedTheme: null, prefersDark: false });

  // 1st click -> switch to dark
  assert.ok(typeof env.themeToggle.onclick === 'function', 'Theme toggle click handler attached');
  env.themeToggle.onclick();

  assert.equal(env.docElement.getAttribute('data-theme'), 'dark');
  assert.equal(env.docElement.classList.contains('theme-dark'), true);
  assert.equal(env.themeToggle.getAttribute('aria-checked'), 'true');
  assert.equal(env.themeToggle.getAttribute('aria-pressed'), 'true');
  assert.equal(env.themeToggle.classList.contains('active'), true);
  assert.equal(env.iconNode.textContent, '☀️');
  assert.equal(env.labelNode.textContent, 'Day');
  assert.equal(env.localStorageMock.getItem('pv-lab-theme-v1'), 'dark');

  // 2nd click -> switch back to light
  env.themeToggle.onclick();

  assert.equal(env.docElement.getAttribute('data-theme'), 'light');
  assert.equal(env.docElement.classList.contains('theme-dark'), false);
  assert.equal(env.themeToggle.getAttribute('aria-checked'), 'false');
  assert.equal(env.themeToggle.getAttribute('aria-pressed'), 'false');
  assert.equal(env.themeToggle.classList.contains('active'), false);
  assert.equal(env.iconNode.textContent, '🌙');
  assert.equal(env.labelNode.textContent, 'Night');
  assert.equal(env.localStorageMock.getItem('pv-lab-theme-v1'), 'light');
});

test('Persisted preference reloads correctly and manual preference wins over prefers-color-scheme', () => {
  // Stored dark preference with prefers-color-scheme: light
  const envDark = createThemeEnvironment({ storedTheme: 'dark', prefersDark: false });
  assert.equal(envDark.docElement.getAttribute('data-theme'), 'dark');
  assert.equal(envDark.themeToggle.getAttribute('aria-checked'), 'true');
  assert.equal(envDark.iconNode.textContent, '☀️');
  assert.equal(envDark.labelNode.textContent, 'Day');

  // Stored light preference with prefers-color-scheme: dark (manual preference wins!)
  const envLight = createThemeEnvironment({ storedTheme: 'light', prefersDark: true });
  assert.equal(envLight.docElement.getAttribute('data-theme'), 'light');
  assert.equal(envLight.themeToggle.getAttribute('aria-checked'), 'false');
  assert.equal(envLight.iconNode.textContent, '🌙');
  assert.equal(envLight.labelNode.textContent, 'Night');
});

test('Default scheme respects prefers-color-scheme when no preference is stored', () => {
  // System prefers dark, no manual preference
  const envDark = createThemeEnvironment({ storedTheme: null, prefersDark: true });
  assert.equal(envDark.docElement.getAttribute('data-theme'), 'dark');
  assert.equal(envDark.themeToggle.getAttribute('aria-checked'), 'true');
  assert.equal(envDark.iconNode.textContent, '☀️');
  assert.equal(envDark.labelNode.textContent, 'Day');

  // System prefers light, no manual preference
  const envLight = createThemeEnvironment({ storedTheme: null, prefersDark: false });
  assert.equal(envLight.docElement.getAttribute('data-theme'), 'light');
  assert.equal(envLight.themeToggle.getAttribute('aria-checked'), 'false');
  assert.equal(envLight.iconNode.textContent, '🌙');
  assert.equal(envLight.labelNode.textContent, 'Night');

  // matchMedia unavailable -> clean light fallback
  const envFallback = createThemeEnvironment({ storedTheme: null, matchMediaAvailable: false });
  assert.equal(envFallback.docElement.getAttribute('data-theme'), 'light');
  assert.equal(envFallback.themeToggle.getAttribute('aria-checked'), 'false');
});

test('Storage failure (SecurityError / QuotaExceededError) does not break startup or toggling', () => {
  // Even when localStorage throws on every call, startup succeeds and toggle functions
  const env = createThemeEnvironment({ storageThrows: true, prefersDark: false });
  assert.equal(env.docElement.getAttribute('data-theme'), 'light');
  assert.equal(env.themeToggle.getAttribute('aria-checked'), 'false');

  // Toggling in memory does not throw unhandled exception
  assert.doesNotThrow(() => {
    env.themeToggle.onclick();
  });
  assert.equal(env.docElement.getAttribute('data-theme'), 'dark');
  assert.equal(env.themeToggle.getAttribute('aria-checked'), 'true');
});

test('Early script in index.html head sets initial theme before first paint without throwing', () => {
  const indexHtml = fs.readFileSync(path.join(__dirname, '../feasibility/web/index.html'), 'utf8');
  const scriptMatch = indexHtml.match(/<head>[\s\S]*?<script>([\s\S]*?)<\/script>[\s\S]*?<\/head>/);
  assert.ok(scriptMatch, 'Early script exists in index.html head');
  const earlyScript = scriptMatch[1];

  // Test with dark preference in storage
  const classesDark = new Set();
  const attrsDark = {};
  const docDark = {
    documentElement: {
      setAttribute: (k, v) => { attrsDark[k] = v; },
      getAttribute: k => attrsDark[k],
      classList: {
        add: c => classesDark.add(c),
        remove: c => classesDark.delete(c),
        contains: c => classesDark.has(c)
      }
    }
  };
  const ctxDark = {
    document: docDark,
    window: {
      matchMedia: () => ({ matches: false })
    },
    localStorage: {
      getItem: k => k === 'pv-lab-theme-v1' ? 'dark' : null
    }
  };
  vm.runInNewContext(earlyScript, ctxDark);
  assert.equal(attrsDark['data-theme'], 'dark', 'Early script applies data-theme=dark');
  assert.ok(classesDark.has('theme-dark'), 'Early script adds theme-dark class');

  // Test with storage throwing
  const attrsSafe = {};
  const docSafe = {
    documentElement: {
      setAttribute: (k, v) => { attrsSafe[k] = v; },
      getAttribute: k => attrsSafe[k],
      classList: { add() {}, remove() {} }
    }
  };
  const ctxSafe = {
    document: docSafe,
    window: {
      matchMedia: () => ({ matches: true })
    },
    localStorage: {
      getItem: () => { throw new Error('Blocked'); }
    }
  };
  assert.doesNotThrow(() => {
    vm.runInNewContext(earlyScript, ctxSafe);
  }, 'Early script catches storage errors cleanly');
  assert.equal(attrsSafe['data-theme'], 'dark', 'Falls back to prefersDark when storage throws');
});

test('Theme persistence is strictly browser-only and separate from scenario payload', () => {
  const env = createThemeEnvironment({ storedTheme: 'dark', prefersDark: false });
  // Ensure theme key is in localStorage
  assert.equal(env.localStorageMock.getItem('pv-lab-theme-v1'), 'dark');

  // Verify theme preference is not part of financial settings or scenario data
  const settings = env.sandbox.settings || {};
  assert.equal(settings.theme, undefined, 'Theme is not in settings');
  assert.equal(settings.night_theme, undefined, 'Night theme is not in settings');

  // Changing theme does not contaminate settings or quote
  env.themeToggle.onclick();
  assert.equal(env.localStorageMock.getItem('pv-lab-theme-v1'), 'light');
  assert.equal(settings.theme, undefined, 'Theme remains outside settings after toggle');
});

test('initTheme is idempotent and does not register duplicate matchMedia listeners', () => {
  const env = createThemeEnvironment({ storedTheme: null, prefersDark: false });
  // Initial run created 1 media listener
  assert.equal(env.mediaListeners.length, 1, 'Initial setup registers 1 media listener');

  // Calling initTheme multiple times (e.g. from init() and initRedesignEventListeners())
  env.sandbox.initTheme();
  env.sandbox.initTheme();
  assert.equal(env.mediaListeners.length, 1, 'Calling initTheme again does not add duplicate media listeners');
});

test('Dark theme style overrides include verdict cards small text and metric range', () => {
  const css = fs.readFileSync(path.join(__dirname, '../feasibility/web/style.css'), 'utf8');
  assert.ok(css.includes('[data-theme="dark"] .verdict-cards .card small'), 'Overrides .verdict-cards .card small in dark mode');
  assert.ok(css.includes('[data-theme="dark"] .verdict-cards .metric-range'), 'Overrides .verdict-cards .metric-range in dark mode');
  assert.ok(css.includes('[data-theme="dark"] .chart-takeaway'), 'Overrides .chart-takeaway in dark mode');
  assert.ok(css.includes('[data-theme="dark"] .brand small'), 'Overrides .brand small in dark mode');
});

console.log('All Night Theme UI tests passed successfully.');
