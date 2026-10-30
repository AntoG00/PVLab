/* Run with Node: test DC clipping cap UI bindings in feasibility/web/app.js.
 * Evaluates extracted syncSetting and initSolarSizingControls with minimal stubs.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
const htmlSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/index.html'), 'utf8');

function extractFunction(src, name) {
  const match = src.match(new RegExp('function\\s+' + name + '\\s*\\([^\\)]*\\)\\s*\\{'));
  assert(match, `Function ${name} must exist in source`);
  const start = match.index;
  let open = 0, started = false;
  for (let i = start; i < src.length; i++) {
    if (src[i] === '{') { open++; started = true; }
    else if (src[i] === '}') {
      open--;
      if (started && open === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`Unterminated function: ${name}`);
}

function makeMockElement(id, dataset = {}, type = 'number') {
  const attrs = {};
  const classes = new Set();
  const listeners = {};
  return {
    id,
    value: '',
    dataset,
    type,
    getAttribute: k => attrs[k] !== undefined ? attrs[k] : null,
    setAttribute: (k, v) => { attrs[k] = String(v); },
    removeAttribute: k => { delete attrs[k]; },
    classList: {
      add: c => classes.add(c),
      remove: c => classes.delete(c),
      contains: c => classes.has(c)
    },
    addEventListener: (evt, fn) => {
      listeners[evt] = listeners[evt] || [];
      listeners[evt].push(fn);
    },
    dispatchEvent: evt => {
      const type = typeof evt === 'string' ? evt : (evt?.type || 'input');
      const eventObj = typeof evt === 'string' ? { type, target: this } : evt;
      (listeners[type] || []).forEach(fn => fn(eventObj));
    }
  };
}

// 1. Read actual preset values from HTML
const presetMatch = [...htmlSource.matchAll(/data-target="solarClippingDcMax"\s+data-value="([^"]+)"/g)].map(m => m[1]);
assert.deepEqual(presetMatch, ['3', '4', '5', '7'], 'HTML preset chips must match 3, 4, 5, 7');

// 2. Set up DOM and elements (absent by default)
const elements = new Map();
const clipInput = makeMockElement('solarClippingDcMax');
const sidebarInput = makeMockElement('sidebarClippingDcMax', { setting: 'clipping_dc_max' });
elements.set('solarClippingDcMax', clipInput);
elements.set('sidebarClippingDcMax', sidebarInput);

const presetChips = presetMatch.map(v => {
  const attrs = { 'data-target': 'solarClippingDcMax', 'data-value': v };
  return {
    getAttribute: k => attrs[k] !== undefined ? attrs[k] : null,
    onclick: null
  };
});

let staleCount = 0;
let editSettingCalls = [];
const settings = { clipping_dc_max: 0.04 };
const schema = { records: [{ name: 'clipping_dc_max' }], defaults: { clipping_dc_max: 0.04 } };

class Event {
  constructor(type) {
    this.type = type;
  }
}

const context = {
  console,
  Event,
  settings,
  schema,
  BATTERY_SETTINGS: [],
  BATTERY_CONTROL_KEYS: new Set(),
  panelKeys: [],
  numericStep: () => 0.01,
  solarControlIds: [],
  solarControlsInitialized: false,
  queryAll: sel => {
    if (sel === '.preset-chip[data-target]') return presetChips;
    return [];
  },
  $: id => elements.get(id) || null,
  document: {
    getElementById: id => elements.get(id) || null,
    querySelectorAll: sel => {
      if (sel === '.preset-chip[data-target]') return presetChips;
      if (sel.includes('[data-setting]')) return [sidebarInput];
      return [];
    }
  },
  loadSolarOptionsFromStorage: () => {},
  updateSolarBatteryModeUi: () => {},
  syncSolarBatteryStep: () => {},
  saveSolarOptionsToStorage: () => {},
  markSolarSizingStale: () => { staleCount++; },
  markSolarSensitivityStale: () => {},
  updateControlModifiedState: () => {},
  editSetting: (key, val, src) => {
    editSettingCalls.push({ key, val, src });
    settings[key] = val;
    if (context.syncSetting) context.syncSetting(key, src);
  }
};

vm.createContext(context);
const excludedKeysMatch = appSource.match(/const\s+EXCLUDED_PERCENT_KEYS\s*=\s*new Set\([\s\S]*?\);/);
assert(excludedKeysMatch, 'EXCLUDED_PERCENT_KEYS marker must exist in source');
vm.runInContext(excludedKeysMatch[0], context);

const isPercentControlCode = extractFunction(appSource, 'isPercentControl');
const formatPercentValueCode = extractFunction(appSource, 'formatPercentValue');
const syncSettingCode = extractFunction(appSource, 'syncSetting');
const initControlsCode = extractFunction(appSource, 'initSolarSizingControls');

vm.runInContext(isPercentControlCode, context);
vm.runInContext(formatPercentValueCode, context);
vm.runInContext(syncSettingCode, context);
vm.runInContext(initControlsCode, context);

assert.equal(typeof context.isPercentControl, 'function', 'isPercentControl must be loaded');
assert.equal(typeof context.formatPercentValue, 'function', 'formatPercentValue must be loaded');

// Test A: Initializing fraction .04 => 4%
settings.clipping_dc_max = 0.04;
context.initSolarSizingControls();
assert.equal(clipInput.value, '4', 'initial fraction .04 converts to 4%');

// Test B: Restored fraction .07 => 7%
settings.clipping_dc_max = 0.07;
context.syncSetting('clipping_dc_max');
assert.equal(clipInput.value, '7', 'restored fraction .07 converts to 7%');

// Test C: Editing 5 => .05 calls editSetting and invalidates
staleCount = 0;
editSettingCalls = [];
clipInput.value = '5';
clipInput.dispatchEvent(new Event('input'));
assert.equal(settings.clipping_dc_max, 0.05, 'setting updated to 0.05');
assert(staleCount > 0, 'editing invalidates solar sizing');
assert.equal(editSettingCalls[editSettingCalls.length - 1].key, 'clipping_dc_max');

// Test D: Presets 3/4/5/7 dispatch input and update setting
presetChips.forEach(chip => {
  const targetId = chip.getAttribute('data-target');
  const val = chip.getAttribute('data-value');
  assert.equal(typeof chip.onclick, 'function', 'preset chip has generic click handler');
  chip.onclick();
  assert.equal(clipInput.value, val, `preset chip sets input value to ${val}`);
  assert.equal(settings.clipping_dc_max, Number(val) / 100, `preset chip sets setting to ${Number(val)/100}`);
});

// Test E: Zero accepted (0 => 0%)
clipInput.value = '0';
clipInput.dispatchEvent(new Event('input'));
assert.equal(settings.clipping_dc_max, 0, 'zero clipping cap is accepted');
assert.equal(clipInput.value, '0', 'zero input value preserved');
assert.equal(clipInput.getAttribute('aria-invalid'), null, 'zero is not marked aria-invalid');

// Test F: Invalid/blank does not corrupt settings
const previousVal = settings.clipping_dc_max;
clipInput.value = 'abc';
clipInput.dispatchEvent(new Event('input'));
assert.equal(settings.clipping_dc_max, previousVal, 'invalid input does not corrupt settings');
assert.equal(clipInput.getAttribute('aria-invalid'), 'true', 'invalid input marked aria-invalid');

clipInput.value = '';
clipInput.dispatchEvent(new Event('input'));
assert.equal(settings.clipping_dc_max, previousVal, 'blank input does not corrupt settings');
assert.equal(clipInput.getAttribute('aria-invalid'), 'true', 'blank input marked aria-invalid');

// Test G: Sidebar syncSetting updates new field
settings.clipping_dc_max = 0.055;
context.syncSetting('clipping_dc_max', sidebarInput);
assert.equal(clipInput.value, '5.5', 'sidebar sync updates solarClippingDcMax');
assert.equal(clipInput.getAttribute('aria-invalid'), null, 'valid sync clears invalid status');
context.syncSetting('clipping_dc_max', clipInput);
assert.equal(sidebarInput.value, 0.055, 'sidebar control reflects fraction value when updated from other sources');

console.log('All DC clipping cap UI tests passed.');
