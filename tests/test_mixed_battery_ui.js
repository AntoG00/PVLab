/* Run with Node: UI tests for mixed battery controls, atomic persistence, validation, and transitions. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function createTestEnvironment() {
  const registeredElements = new Map();
  let apiRequests = [];

  class MockElement {
    constructor(id = '', tag = 'div') {
      this.id = id;
      this.tagName = tag.toUpperCase();
      this.innerHTML = '';
      this.textContent = '';
      this._value = '';
      this.disabled = false;
      this.hidden = false;
      this.readOnly = false;
      this.type = 'text';
      this.dataset = {};
      this.attributes = new Map();
      this.classList = {
        _classes: new Set(),
        add: (...cls) => cls.forEach(c => this.classList._classes.add(c)),
        remove: (...cls) => cls.forEach(c => this.classList._classes.delete(c)),
        contains: c => this.classList._classes.has(c),
        toggle: (c, force) => {
          if (force === true) this.classList._classes.add(c);
          else if (force === false) this.classList._classes.delete(c);
          else if (this.classList._classes.has(c)) this.classList._classes.delete(c);
          else this.classList._classes.add(c);
          return this.classList._classes.has(c);
        }
      };
      this.listeners = {};
    }
    get value() { return this._value; }
    set value(v) { this._value = String(v ?? ''); }
    setAttribute(k, v) { this.attributes.set(k, String(v)); if (k === 'value') this._value = String(v); }
    getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; }
    removeAttribute(k) { this.attributes.delete(k); }
    addEventListener(evt, fn) { this.listeners[evt] = this.listeners[evt] || []; this.listeners[evt].push(fn); }
    dispatchEvent(evt) { (this.listeners[evt.type || evt] || []).forEach(fn => fn(evt)); }
    focus() {}
    closest(sel) {
      if (sel === '[data-save-default]' && this.dataset.saveDefault) return this;
      if (sel === '[data-reset-setting]' && this.dataset.resetSetting) return this;
      if (sel === '[data-control]' && this.dataset.control) return this;
      return null;
    }
    querySelector(sel) {
      const all = this.querySelectorAll(sel);
      return all.length ? all[0] : null;
    }
    querySelectorAll(sel) {
      return Array.from(registeredElements.values()).filter(el => {
        if (sel.startsWith('#') && el.id === sel.slice(1)) return true;
        if (sel.startsWith('.') && el.classList.contains(sel.slice(1))) return true;
        if (sel === 'input[data-battery-row="first"]' && el.dataset.batteryRow === 'first') return true;
        if (sel === 'input[data-battery-row="secondary"]' && el.dataset.batteryRow === 'secondary') return true;
        if (sel === '[data-setting="battery_kwh"]' && el.dataset.setting === 'battery_kwh') return true;
        if (sel === '[data-slider="battery_kwh"]' && el.dataset.slider === 'battery_kwh') return true;
        if (sel === '[data-reset-setting]' && el.dataset.resetSetting) return true;
        if (sel === '.battery-group-control' && el.classList.contains('battery-group-control')) return true;
        if (sel === '[data-control="battery_group"]' && el.dataset.control === 'battery_group') return true;
        if (sel.includes('[data-control=')) {
          const m = sel.match(/\[data-control="([^"]+)"\]/);
          if (m && el.dataset.control === m[1]) return true;
        }
        return false;
      });
    }
  }

  const getEl = id => {
    if (!registeredElements.has(id)) {
      registeredElements.set(id, new MockElement(id));
    }
    return registeredElements.get(id);
  };

  const controls = getEl('controls');
  const pinnedList = getEl('pinnedControlsList');
  const dialog = getEl('defaultConfirm');
  dialog.open = false;
  dialog.returnValue = '';
  dialog.showModal = function() { this.open = true; };

  const mockDoc = {
    getElementById: id => getEl(id),
    querySelector: sel => {
      if (sel.startsWith('#')) return getEl(sel.slice(1));
      const all = mockDoc.querySelectorAll(sel);
      return all.length ? all[0] : null;
    },
    querySelectorAll: sel => {
      return Array.from(registeredElements.values()).filter(el => {
        if (sel === 'input[data-battery-row="first"]' && el.dataset.batteryRow === 'first') return true;
        if (sel === 'input[data-battery-row="secondary"]' && el.dataset.batteryRow === 'secondary') return true;
        if (sel === '[data-setting="battery_kwh"]' && el.dataset.setting === 'battery_kwh') return true;
        if (sel === '[data-slider="battery_kwh"]' && el.dataset.slider === 'battery_kwh') return true;
        if (sel === '.battery-group-control' && el.classList.contains('battery-group-control')) return true;
        if (sel.includes('[data-control="battery_group"]')) return el.dataset.control === 'battery_group';
        if (sel.includes('[data-control=')) {
          const m = sel.match(/\[data-control="([^"]+)"\]/);
          if (m && el.dataset.control === m[1]) return true;
        }
        if (sel.includes('[data-setting],[data-slider]')) return el.dataset.setting || el.dataset.slider;
        if (sel === 'details.category-accordion') return false;
        return false;
      });
    },
    activeElement: null
  };

  const mockFetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {};
    apiRequests.push({ url, body });
    if (url === '/api/defaults') {
      const name = body.name;
      const val = body.value;
      const ctx = body.battery_settings || {};
      const batDefaults = {
        battery_kwh: ctx.battery_kwh ?? 5.0,
        battery_modules: ctx.battery_modules ?? 1,
        battery_module_kwh: ctx.battery_module_kwh ?? 5.0,
        battery_mixed_sizes: ctx.battery_mixed_sizes ?? false,
        battery_secondary_module_kwh: ctx.battery_secondary_module_kwh ?? 6.9,
        battery_secondary_modules: ctx.battery_secondary_modules ?? 0
      };
      const batRecords = Object.keys(batDefaults).map(k => ({
        name: k,
        value: batDefaults[k],
        status: 'provided',
        source: 'User-saved shared app default'
      }));
      return {
        ok: true,
        json: async () => ({
          name,
          value: val,
          record: { name, value: val, status: 'provided', source: 'User-saved shared app default' },
          defaults: batDefaults,
          records: batRecords
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const ctxObj = {
    document: mockDoc,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: mockFetch,
    syncOptimizerEquipmentInputs: () => {},
    setTimeout,
    clearTimeout,
    console
  };

  const context = vm.createContext(ctxObj);
  const code = source.slice(0, source.indexOf('function schedule(')) + '\nlet scheduled=0;function schedule(){scheduled++;}';
  vm.runInContext(code, context);

  const initialSchema = {
    defaults: {
      battery_kwh: 5.0,
      battery_modules: 1,
      battery_module_kwh: 5.0,
      battery_mixed_sizes: false,
      battery_secondary_module_kwh: 6.9,
      battery_secondary_modules: 0,
      retail_commodity: 0.12,
      gas_commodity: 0.08,
      real_discount_rate: 0.03,
      deduction_rate: 0.5
    },
    records: [
      { name: 'battery_kwh', label: 'Battery Capacity', unit: 'kWh', bounds: [0, null], group: 'equipment' },
      { name: 'battery_modules', label: 'Count of battery modules', unit: 'modules', bounds: [0, null], group: 'equipment' },
      { name: 'battery_module_kwh', label: 'Usable capacity per module', unit: 'usable kWh/module', bounds: [0, null], group: 'equipment' },
      { name: 'battery_mixed_sizes', label: 'Modules of different sizes?', unit: 'boolean', bounds: null, group: 'equipment' },
      { name: 'battery_secondary_module_kwh', label: 'Secondary module usable kWh', unit: 'usable kWh/module', bounds: [0, null], group: 'equipment' },
      { name: 'battery_secondary_modules', label: 'Count of secondary modules', unit: 'modules', bounds: [0, null], group: 'equipment' },
      { name: 'retail_commodity', unit: 'EUR/kWh', bounds: [0, 0.5], group: 'bill' },
      { name: 'gas_commodity', unit: 'EUR/smc', bounds: [0, 2], group: 'gas' },
      { name: 'real_discount_rate', unit: 'fraction', bounds: [0, 0.2], group: 'finance' },
      { name: 'deduction_rate', unit: 'fraction', bounds: [0, 1], group: 'finance' }
    ]
  };

  const run = script => vm.runInContext(script, context);
  run(`
    schema = ${JSON.stringify(initialSchema)};
    settings = { ...schema.defaults };
    quote = { line_items: [{ net_eur: 5000 }], battery_model: 'Alpha 5', battery_nameplate_kwh: 5.5, battery_dod: 0.9 };
  `);

  return { context, run, registeredElements, mockDoc, getEl, apiRequests };
}

// -----------------------------------------------------------------------------
// Test 1: Rendering of Battery Group Control & Accessibility
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  const html = run('renderBatteryGroupControl()');

  assert.ok(html.includes('class="battery-group-control'), 'renders .battery-group-control wrapper');
  assert.ok(html.includes('data-control="battery_group"'), 'has data-control="battery_group"');
  assert.ok(html.includes('role="switch"'), 'renders accessible switch for mixed sizes');
  assert.ok(html.includes('aria-label="Modules of different sizes?"'), 'switch has accessible aria-label');
  assert.ok(html.includes('id="batterySingleControls"'), 'contains single controls container');
  assert.ok(html.includes('id="batteryMixedControls"'), 'contains mixed controls container');
  assert.ok(html.includes('id="batteryMixedControls" hidden'), 'mixed controls are hidden in single mode by default');
  assert.ok(!html.includes('id="batterySingleControls" hidden'), 'single controls are visible by default');
  assert.ok(html.includes('id="batterySummaryStrip"'), 'summary strip element is present');
}

// -----------------------------------------------------------------------------
// Test 2: Single Mode Retains Familiar Controls & Calculations
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  assert.strictEqual(run('settings.battery_mixed_sizes'), false);
  assert.strictEqual(run('settings.battery_modules'), 1);
  assert.strictEqual(run('settings.battery_module_kwh'), 5.0);
  assert.strictEqual(run('settings.battery_kwh'), 5.0);

  // In single mode, batterySummaryText shows single-mode breakdown
  const summaryText = run('batterySummaryText()');
  assert.strictEqual(summaryText, '1 × 5 = 5 kWh · 1 module');

  // Editing module count in single mode computes count * capacity
  run('editSetting("battery_modules", 3)');
  assert.strictEqual(run('settings.battery_modules'), 3);
  assert.strictEqual(run('settings.battery_kwh'), 15.0);
  assert.strictEqual(run('batterySummaryText()'), '3 × 5 = 15 kWh · 3 modules');

  // Editing capacity in single mode updates total
  run('editSetting("battery_module_kwh", 4.5)');
  assert.strictEqual(run('settings.battery_module_kwh'), 4.5);
  assert.strictEqual(run('settings.battery_kwh'), 13.5);
}

// -----------------------------------------------------------------------------
// Test 3: Switch to Mixed Mode reveals Two Neat Rows with Correct Quantities
// -----------------------------------------------------------------------------
{
  const { run, getEl } = createTestEnvironment();
  const singleWrap = getEl('batterySingleControls');
  const mixedWrap = getEl('batteryMixedControls');

  // Turn on mixed sizes
  run('editSetting("battery_mixed_sizes", true)');
  assert.strictEqual(run('settings.battery_mixed_sizes'), true);
  assert.strictEqual(singleWrap.hidden, true, 'single controls are hidden when mixed mode is enabled');
  assert.strictEqual(mixedWrap.hidden, false, 'mixed controls are revealed when mixed mode is enabled');

  // Summary in mixed mode: totalModules=1, secModules=0 -> first=1, sec=0
  const summary = run('batterySummaryText()');
  assert.strictEqual(summary, '1 × 5 + 0 × 6.9 = 5 kWh · 1 module');
}

// -----------------------------------------------------------------------------
// Test 4: Editing Row Quantities Atomically Updates Total, Secondary & Derived Total
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  run('editSetting("battery_mixed_sizes", true)');

  // Edit primary (first) quantity to 2 -> first=2, sec=0 => total=2, sec=0
  run('editMixedBatteryRowQuantity("first", 2)');
  assert.strictEqual(run('settings.battery_modules'), 2);
  assert.strictEqual(run('settings.battery_secondary_modules'), 0);
  assert.strictEqual(run('settings.battery_kwh'), 10.0);
  assert.strictEqual(run('batterySummaryText()'), '2 × 5 + 0 × 6.9 = 10 kWh · 2 modules');

  // Edit secondary quantity to 3 -> first=2, sec=3 => total=5, sec=3
  run('editMixedBatteryRowQuantity("secondary", 3)');
  assert.strictEqual(run('settings.battery_modules'), 5);
  assert.strictEqual(run('settings.battery_secondary_modules'), 3);
  assert.strictEqual(run('settings.battery_kwh'), 2 * 5.0 + 3 * 6.9); // 10.0 + 20.7 = 30.7
  assert.strictEqual(run('batterySummaryText()'), '2 × 5 + 3 × 6.9 = 30.7 kWh · 5 modules');

  // Verify secondary never exceeds total modules
  assert.ok(run('settings.battery_secondary_modules <= settings.battery_modules'), 'secondary <= total is preserved');
}

// -----------------------------------------------------------------------------
// Test 5: Exact Unrounded Arithmetic Sum & High Precision
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  run('editSetting("battery_mixed_sizes", true)');
  run('editSetting("battery_module_kwh", 5.123456789123)');
  run('editSetting("battery_secondary_module_kwh", 6.789012345678)');
  run('editMixedBatteryRowQuantity("first", 1)');
  run('editMixedBatteryRowQuantity("secondary", 1)');

  // Exact sum without premature rounding
  const expectedSum = 5.123456789123 + 6.789012345678;
  assert.strictEqual(run('settings.battery_kwh'), expectedSum, 'capacity is exact unrounded arithmetic sum');
}

// -----------------------------------------------------------------------------
// Test 6: Read-Only Derived Capacity in All Entry Points (Pinned Input Blocked)
// -----------------------------------------------------------------------------
{
  const { run, mockDoc } = createTestEnvironment();
  const pinnedBatInput = {
    dataset: { setting: 'battery_kwh' },
    value: '5',
    readOnly: false,
    classList: { toggle: (cls, force) => { pinnedBatInput._hasReadonlyClass = force; } },
    setAttribute: (k, v) => {},
    removeAttribute: (k) => {}
  };
  const pinnedBatSlider = {
    dataset: { slider: 'battery_kwh' },
    value: '5',
    disabled: false,
    hidden: false
  };

  mockDoc.querySelectorAll = sel => {
    if (sel.includes('[data-setting="battery_kwh"]')) return [pinnedBatInput];
    if (sel.includes('[data-slider="battery_kwh"]')) return [pinnedBatSlider];
    return [];
  };

  // When mixed mode is activated, syncSetting marks battery_kwh readonly and hides slider
  run('editSetting("battery_mixed_sizes", true)');
  assert.strictEqual(pinnedBatInput.readOnly, true, 'pinned battery_kwh is marked readOnly in mixed mode');
  assert.strictEqual(pinnedBatInput._hasReadonlyClass, true, 'pinned battery_kwh has readonly-input class');
  assert.strictEqual(pinnedBatSlider.disabled, true, 'pinned slider is disabled');
  assert.strictEqual(pinnedBatSlider.hidden, true, 'pinned slider is hidden');

  // Direct edit attempts to battery_kwh in mixed mode are blocked
  const prevKwh = run('settings.battery_kwh');
  run('editSetting("battery_kwh", 99.9)');
  assert.strictEqual(run('settings.battery_kwh'), prevKwh, 'direct editSetting for battery_kwh in mixed mode is blocked');

  // Toggling back to single mode restores editable state and slider
  run('editSetting("battery_mixed_sizes", false)');
  assert.strictEqual(pinnedBatInput.readOnly, false, 'pinned battery_kwh is editable in single mode');
  assert.strictEqual(pinnedBatInput._hasReadonlyClass, false, 'readonly-input class removed in single mode');
  assert.strictEqual(pinnedBatSlider.disabled, false, 'pinned slider is enabled in single mode');
  assert.strictEqual(pinnedBatSlider.hidden, false, 'pinned slider is visible in single mode');
}

// -----------------------------------------------------------------------------
// Test 7: Input Validation: Invalid-then-Valid Edits
// -----------------------------------------------------------------------------
{
  const { context, run } = createTestEnvironment();
  run('editSetting("battery_mixed_sizes", true)');
  run('editMixedBatteryRowQuantity("first", 1)');
  run('editMixedBatteryRowQuantity("secondary", 1)');
  const validTotal = run('settings.battery_modules'); // 2

  const mockInput = {
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    classList: {
      classes: new Set(),
      add(c) { this.classes.add(c); },
      remove(c) { this.classes.delete(c); }
    }
  };

  // Test negative quantity
  context.sourceEl = mockInput;
  run('editMixedBatteryRowQuantity("first", -1, sourceEl)');
  assert.strictEqual(mockInput.attrs['aria-invalid'], 'true', 'negative quantity flags aria-invalid');
  assert.ok(mockInput.classList.classes.has('is-invalid'), 'is-invalid class added');
  assert.strictEqual(run('settings.battery_modules'), validTotal, 'invalid input does not alter scenario settings');

  // Test non-integer quantity
  run('editMixedBatteryRowQuantity("first", 1.5, sourceEl)');
  assert.strictEqual(mockInput.attrs['aria-invalid'], 'true', 'non-integer quantity flags aria-invalid');
  assert.strictEqual(run('settings.battery_modules'), validTotal, 'non-integer does not alter settings');

  // Test NaN / empty string
  run('editMixedBatteryRowQuantity("first", "", sourceEl)');
  assert.strictEqual(mockInput.attrs['aria-invalid'], 'true', 'empty input flags aria-invalid');
  assert.strictEqual(run('settings.battery_modules'), validTotal);

  // Test valid edit clears error
  run('editMixedBatteryRowQuantity("first", 3, sourceEl)');
  assert.strictEqual(mockInput.attrs['aria-invalid'], undefined, 'valid edit clears aria-invalid');
  assert.ok(!mockInput.classList.classes.has('is-invalid'), 'valid edit removes is-invalid class');
  assert.strictEqual(run('settings.battery_modules'), 4, 'valid edit updates total modules');
  assert.strictEqual(run('settings.battery_secondary_modules'), 1);
}

// -----------------------------------------------------------------------------
// Test 8: Single/Mixed Transitions & Preservation of Dormant Secondary Settings
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  // Set mixed configuration: 1 x 5.0 + 2 x 6.9 = 18.8
  run('editSetting("battery_mixed_sizes", true)');
  run('editMixedBatteryRowQuantity("first", 1)');
  run('editMixedBatteryRowQuantity("secondary", 2)');
  assert.strictEqual(run('settings.battery_modules'), 3);
  assert.strictEqual(run('settings.battery_secondary_modules'), 2);
  assert.strictEqual(run('settings.battery_kwh'), 18.8);

  // Switch to single mode
  run('editSetting("battery_mixed_sizes", false)');
  assert.strictEqual(run('settings.battery_mixed_sizes'), false);
  // Dormant secondary modules preserved
  assert.strictEqual(run('settings.battery_secondary_modules'), 2);
  // Total capacity in single mode: 3 * 5.0 = 15.0
  assert.strictEqual(run('settings.battery_kwh'), 15.0);

  // In single mode, lower total modules to 1
  run('editSetting("battery_modules", 1)');
  assert.strictEqual(run('settings.battery_modules'), 1);
  assert.strictEqual(run('settings.battery_kwh'), 5.0);
  // Secondary is still dormant at 2
  assert.strictEqual(run('settings.battery_secondary_modules'), 2);

  // Re-enable mixed mode: secondary (2) > total (1) must be clamped consistently to 1
  run('editSetting("battery_mixed_sizes", true)');
  assert.strictEqual(run('settings.battery_modules'), 1);
  assert.strictEqual(run('settings.battery_secondary_modules'), 1, 'secondary clamped to total on re-enable');
  // First modules = 1 - 1 = 0, total kWh = 0 * 5 + 1 * 6.9 = 6.9
  assert.strictEqual(run('settings.battery_kwh'), 6.9);
}

// -----------------------------------------------------------------------------
// Test 9: Changing Battery Composition Clears Only Incompatible Quote Overrides
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  assert.strictEqual(run('quote.battery_nameplate_kwh'), 5.5);
  assert.strictEqual(run('quote.battery_dod'), 0.9);
  assert.strictEqual(run('quote.battery_model'), 'Alpha 5');
  assert.strictEqual(run('quote.line_items.length'), 1);

  // Changing battery composition in mixed mode
  run('editSetting("battery_mixed_sizes", true)');
  run('editMixedBatteryRowQuantity("first", 2)');

  // Quote capacity overrides cleared
  assert.strictEqual(run('quote.battery_nameplate_kwh'), undefined, 'battery_nameplate_kwh cleared');
  assert.strictEqual(run('quote.battery_dod'), undefined, 'battery_dod cleared');

  // Other quote fields preserved
  assert.strictEqual(run('quote.battery_model'), 'Alpha 5', 'battery_model preserved');
  assert.strictEqual(run('quote.line_items.length'), 1, 'line items preserved');
}

// -----------------------------------------------------------------------------
// Test 10: Atomic Group Save Default with battery_settings Context
async function main() {
// -----------------------------------------------------------------------------
// Test 10: Atomic Group Save Default with battery_settings Context
// -----------------------------------------------------------------------------
{
  const { run, apiRequests } = createTestEnvironment();
  // Set mixed sizes: 2 x 5.0 + 1 x 6.9 = 16.9 kWh (total = 3, sec = 1)
  run('editSetting("battery_mixed_sizes", true)');
  run('editMixedBatteryRowQuantity("first", 2)');
  run('editMixedBatteryRowQuantity("secondary", 1)');

  // Save default for battery_modules: mock auto-confirm
  run('confirmDefault = () => Promise.resolve(true)');
  await run('saveDefault("battery_modules")');

  // Find the /api/defaults call
  const req = apiRequests.find(r => r.url === '/api/defaults');
  assert.ok(req, 'called /api/defaults');
  assert.strictEqual(req.body.name, 'battery_modules');
  assert.strictEqual(req.body.value, 3);
  assert.ok(req.body.battery_settings, 'included battery_settings context');
  assert.strictEqual(req.body.battery_settings.battery_mixed_sizes, true);
  assert.strictEqual(req.body.battery_settings.battery_modules, 3);
  assert.strictEqual(req.body.battery_settings.battery_secondary_modules, 1);
  assert.strictEqual(req.body.battery_settings.battery_kwh, 16.9);
}

// -----------------------------------------------------------------------------
// Test 11: Atomic Group Reset Restores Consistent Defaults
// -----------------------------------------------------------------------------
{
  const { run } = createTestEnvironment();
  // Modify battery settings away from defaults
  run('editSetting("battery_mixed_sizes", true)');
  run('editMixedBatteryRowQuantity("first", 3)');
  run('editMixedBatteryRowQuantity("secondary", 2)');
  assert.strictEqual(run('settings.battery_modules'), 5);

  // Reset battery group
  run('resetSetting("battery_group")');

  // Verify all 6 settings restored to schema.defaults in one operation
  assert.strictEqual(run('settings.battery_mixed_sizes'), false, 'mixed sizes reset to false');
  assert.strictEqual(run('settings.battery_modules'), 1, 'modules reset to 1');
  assert.strictEqual(run('settings.battery_module_kwh'), 5.0, 'module kwh reset to 5');
  assert.strictEqual(run('settings.battery_secondary_modules'), 0, 'sec modules reset to 0');
  assert.strictEqual(run('settings.battery_secondary_module_kwh'), 6.9, 'sec module kwh reset to 6.9');
  assert.strictEqual(run('settings.battery_kwh'), 5.0, 'total kwh reset to 5.0');
}

// -----------------------------------------------------------------------------
// Test 12: Shared State Sync & Legacy Scenario Normalization
// -----------------------------------------------------------------------------
{
  const { context, run } = createTestEnvironment();
  // Simulate receiving legacy shared state (missing battery_mixed_sizes)
  const legacyState = {
    settings: {
      battery_modules: 2,
      battery_module_kwh: 5.0,
      battery_kwh: 10.0
    },
    defaults: run('schema.defaults'),
    records: run('schema.records'),
    quote: { line_items: [] }
  };

  run('sharedUiReady = false');
  context.stateToApply = legacyState;
  run('applySharedState(stateToApply)');

  assert.strictEqual(run('settings.battery_mixed_sizes'), false, 'legacy state normalizes to single mode');
  assert.strictEqual(run('settings.battery_modules'), 2);
  assert.strictEqual(run('settings.battery_kwh'), 10.0);
  assert.strictEqual(run('batterySummaryText()'), '2 × 5 = 10 kWh · 2 modules');

  // Simulate receiving mixed shared state
  const mixedState = {
    settings: {
      battery_mixed_sizes: true,
      battery_modules: 4,
      battery_secondary_modules: 1,
      battery_module_kwh: 5.0,
      battery_secondary_module_kwh: 6.9,
      battery_kwh: 21.9
    },
    defaults: run('schema.defaults'),
    records: run('schema.records'),
    quote: { line_items: [] }
  };
  context.stateToApply = mixedState;
  run('applySharedState(stateToApply)');

  assert.strictEqual(run('settings.battery_mixed_sizes'), true);
  assert.strictEqual(run('settings.battery_modules'), 4);
  assert.strictEqual(run('settings.battery_secondary_modules'), 1);
  assert.strictEqual(run('settings.battery_kwh'), 21.9);
  assert.strictEqual(run('batterySummaryText()'), '3 × 5 + 1 × 6.9 = 21.9 kWh · 4 modules');
}
}

main().then(() => {
  console.log('All 12 mixed battery UI, atomic persistence, validation, and transition fixtures passed successfully.');
}).catch(err => {
  console.error(err);
  process.exit(1);
});
