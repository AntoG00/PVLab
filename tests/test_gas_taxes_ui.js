/* Run with Node: focused tests for gas tax interface, auditable accounting, and stale-result handling. */
'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const SharedScenarioSync = require(path.join(__dirname, '../feasibility/web/sync.js'));

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function makeGasCostsPayload(overrides = {}) {
  const baseline = {
  "volume": 600.0,
  "days": 365,
  "prior_year_smc": 0.0,
  "prior_cumulative_smc": 0.0,
  "year": 1,
  "include_fixed": true,
  "commodity_eur": 300.0,
  "network_eur": 240.0,
  "regulated_eur": 240.0,
  "variable_pretax_eur": 540.0,
  "fixed_eur": 109.5,
  "national_excise_eur": 67.56,
  "regional_surcharge_eur": 17.16,
  "reduced_vat_base_eur": 498.6,
  "reduced_vat_taxable_base": 498.6,
  "reduced_vat_rate": 0.1,
  "reduced_vat_eur": 49.86000000000001,
  "reduced_vat_amount": 49.86000000000001,
  "standard_vat_variable_base_eur": 126.12,
  "standard_vat_fixed_base_eur": 109.5,
  "standard_vat_total_base_eur": 235.62,
  "standard_vat_base_eur": 235.62,
  "standard_vat_rate": 0.22,
  "standard_vat_eur": 51.836400000000005,
  "standard_vat_amount": 51.836400000000005,
  "standard_vat_variable_eur": 27.7464,
  "standard_vat_fixed_eur": 24.09,
  "vat_eur": 101.69640000000001,
  "variable_taxable_eur": 624.7199999999999,
  "variable_vat_eur": 77.60640000000001,
  "variable_total_eur": 702.3263999999999,
  "fixed_total_eur": 133.59,
  "total_eur": 835.9164,
  "brackets": [
    {
      "bracket_index": 0,
      "name": "0_120",
      "lower_smc": 0.0,
      "upper_smc": 120.0,
      "quantity_smc": 120.0,
      "national_excise_rate": 0.038,
      "national_excise_eur": 4.56,
      "regional_surcharge_rate": 0.019,
      "regional_surcharge_eur": 2.28,
      "vat_rate": 0.1,
      "commodity_eur": 60.0,
      "network_eur": 48.0,
      "variable_pretax_eur": 108.0,
      "taxable_base_eur": 114.84,
      "vat_eur": 11.484000000000002,
      "total_eur": 126.32400000000001
    },
    {
      "bracket_index": 1,
      "name": "120_480",
      "lower_smc": 120.0,
      "upper_smc": 480.0,
      "quantity_smc": 360.0,
      "national_excise_rate": 0.135,
      "national_excise_eur": 48.6,
      "regional_surcharge_rate": 0.031,
      "regional_surcharge_eur": 11.16,
      "vat_rate": 0.1,
      "commodity_eur": 180.0,
      "network_eur": 144.0,
      "variable_pretax_eur": 324.0,
      "taxable_base_eur": 383.76000000000005,
      "vat_eur": 38.376000000000005,
      "total_eur": 422.1360000000001
    },
    {
      "bracket_index": 2,
      "name": "480_1560",
      "lower_smc": 480.0,
      "upper_smc": 1560.0,
      "quantity_smc": 120.0,
      "national_excise_rate": 0.12,
      "national_excise_eur": 14.399999999999999,
      "regional_surcharge_rate": 0.031,
      "regional_surcharge_eur": 3.7199999999999998,
      "vat_rate": 0.22,
      "commodity_eur": 60.0,
      "network_eur": 48.0,
      "variable_pretax_eur": 108.0,
      "taxable_base_eur": 126.12,
      "vat_eur": 27.7464,
      "total_eur": 153.8664
    },
    {
      "bracket_index": 3,
      "name": "over_1560",
      "lower_smc": 1560.0,
      "upper_smc": null,
      "quantity_smc": 0.0,
      "national_excise_rate": 0.15,
      "national_excise_eur": 0.0,
      "regional_surcharge_rate": 0.031,
      "regional_surcharge_eur": 0.0,
      "vat_rate": 0.22,
      "commodity_eur": 0.0,
      "network_eur": 0.0,
      "variable_pretax_eur": 0.0,
      "taxable_base_eur": 0.0,
      "vat_eur": 0.0,
      "total_eur": 0.0
    }
  ]
};
  const remaining = {
  "volume": 0.0,
  "days": 365,
  "prior_year_smc": 0.0,
  "prior_cumulative_smc": 0.0,
  "year": 1,
  "include_fixed": false,
  "commodity_eur": 0.0,
  "network_eur": 0.0,
  "regulated_eur": 0.0,
  "variable_pretax_eur": 0.0,
  "fixed_eur": 0.0,
  "national_excise_eur": 0.0,
  "regional_surcharge_eur": 0.0,
  "reduced_vat_base_eur": 0.0,
  "reduced_vat_taxable_base": 0.0,
  "reduced_vat_rate": 0.1,
  "reduced_vat_eur": 0.0,
  "reduced_vat_amount": 0.0,
  "standard_vat_variable_base_eur": 0.0,
  "standard_vat_fixed_base_eur": 0.0,
  "standard_vat_total_base_eur": 0.0,
  "standard_vat_base_eur": 0.0,
  "standard_vat_rate": 0.22,
  "standard_vat_eur": 0.0,
  "standard_vat_amount": 0.0,
  "standard_vat_variable_eur": 0.0,
  "standard_vat_fixed_eur": 0.0,
  "vat_eur": 0.0,
  "variable_taxable_eur": 0.0,
  "variable_vat_eur": 0.0,
  "variable_total_eur": 0.0,
  "fixed_total_eur": 0.0,
  "total_eur": 0.0,
  "brackets": [
    {
      "bracket_index": 0,
      "name": "0_120",
      "lower_smc": 0.0,
      "upper_smc": 120.0,
      "quantity_smc": 0.0,
      "national_excise_rate": 0.038,
      "national_excise_eur": 0.0,
      "regional_surcharge_rate": 0.019,
      "regional_surcharge_eur": 0.0,
      "vat_rate": 0.1,
      "commodity_eur": 0.0,
      "network_eur": 0.0,
      "variable_pretax_eur": 0.0,
      "taxable_base_eur": 0.0,
      "vat_eur": 0.0,
      "total_eur": 0.0
    },
    {
      "bracket_index": 1,
      "name": "120_480",
      "lower_smc": 120.0,
      "upper_smc": 480.0,
      "quantity_smc": 0.0,
      "national_excise_rate": 0.135,
      "national_excise_eur": 0.0,
      "regional_surcharge_rate": 0.031,
      "regional_surcharge_eur": 0.0,
      "vat_rate": 0.1,
      "commodity_eur": 0.0,
      "network_eur": 0.0,
      "variable_pretax_eur": 0.0,
      "taxable_base_eur": 0.0,
      "vat_eur": 0.0,
      "total_eur": 0.0
    },
    {
      "bracket_index": 2,
      "name": "480_1560",
      "lower_smc": 480.0,
      "upper_smc": 1560.0,
      "quantity_smc": 0.0,
      "national_excise_rate": 0.12,
      "national_excise_eur": 0.0,
      "regional_surcharge_rate": 0.031,
      "regional_surcharge_eur": 0.0,
      "vat_rate": 0.22,
      "commodity_eur": 0.0,
      "network_eur": 0.0,
      "variable_pretax_eur": 0.0,
      "taxable_base_eur": 0.0,
      "vat_eur": 0.0,
      "total_eur": 0.0
    },
    {
      "bracket_index": 3,
      "name": "over_1560",
      "lower_smc": 1560.0,
      "upper_smc": null,
      "quantity_smc": 0.0,
      "national_excise_rate": 0.15,
      "national_excise_eur": 0.0,
      "regional_surcharge_rate": 0.031,
      "regional_surcharge_eur": 0.0,
      "vat_rate": 0.22,
      "commodity_eur": 0.0,
      "network_eur": 0.0,
      "variable_pretax_eur": 0.0,
      "taxable_base_eur": 0.0,
      "vat_eur": 0.0,
      "total_eur": 0.0
    }
  ]
};
  const reference_bill = {
  "invoice": {
    "period_days": 30,
    "volume_smc": 50.0,
    "consumption_eur": 45.0,
    "fixed_eur": 9.0,
    "excise_including_regional_eur": 8.3,
    "vat_eur": 7.31,
    "total_eur": 69.61
  },
  "modeled": {
    "volume": 50.0,
    "days": 30,
    "prior_year_smc": 200.0,
    "prior_cumulative_smc": 200.0,
    "year": 1,
    "include_fixed": true,
    "commodity_eur": 25.0,
    "network_eur": 20.0,
    "regulated_eur": 20.0,
    "variable_pretax_eur": 45.0,
    "fixed_eur": 9.0,
    "national_excise_eur": 6.75,
    "regional_surcharge_eur": 1.55,
    "reduced_vat_base_eur": 53.3,
    "reduced_vat_taxable_base": 53.3,
    "reduced_vat_rate": 0.1,
    "reduced_vat_eur": 5.33,
    "reduced_vat_amount": 5.33,
    "standard_vat_variable_base_eur": 0.0,
    "standard_vat_fixed_base_eur": 9.0,
    "standard_vat_total_base_eur": 9.0,
    "standard_vat_base_eur": 9.0,
    "standard_vat_rate": 0.22,
    "standard_vat_eur": 1.98,
    "standard_vat_amount": 1.98,
    "standard_vat_variable_eur": 0.0,
    "standard_vat_fixed_eur": 1.98,
    "vat_eur": 7.3100000000000005,
    "variable_taxable_eur": 53.3,
    "variable_vat_eur": 5.33,
    "variable_total_eur": 58.629999999999995,
    "fixed_total_eur": 10.98,
    "total_eur": 69.61,
    "brackets": [
      {
        "bracket_index": 0,
        "name": "0_120",
        "lower_smc": 0.0,
        "upper_smc": 120.0,
        "quantity_smc": 0.0,
        "national_excise_rate": 0.038,
        "national_excise_eur": 0.0,
        "regional_surcharge_rate": 0.019,
        "regional_surcharge_eur": 0.0,
        "vat_rate": 0.1,
        "commodity_eur": 0.0,
        "network_eur": 0.0,
        "variable_pretax_eur": 0.0,
        "taxable_base_eur": 0.0,
        "vat_eur": 0.0,
        "total_eur": 0.0
      },
      {
        "bracket_index": 1,
        "name": "120_480",
        "lower_smc": 120.0,
        "upper_smc": 480.0,
        "quantity_smc": 50.0,
        "national_excise_rate": 0.135,
        "national_excise_eur": 6.75,
        "regional_surcharge_rate": 0.031,
        "regional_surcharge_eur": 1.55,
        "vat_rate": 0.1,
        "commodity_eur": 25.0,
        "network_eur": 20.0,
        "variable_pretax_eur": 45.0,
        "taxable_base_eur": 53.3,
        "vat_eur": 5.33,
        "total_eur": 58.629999999999995
      },
      {
        "bracket_index": 2,
        "name": "480_1560",
        "lower_smc": 480.0,
        "upper_smc": 1560.0,
        "quantity_smc": 0.0,
        "national_excise_rate": 0.12,
        "national_excise_eur": 0.0,
        "regional_surcharge_rate": 0.031,
        "regional_surcharge_eur": 0.0,
        "vat_rate": 0.22,
        "commodity_eur": 0.0,
        "network_eur": 0.0,
        "variable_pretax_eur": 0.0,
        "taxable_base_eur": 0.0,
        "vat_eur": 0.0,
        "total_eur": 0.0
      },
      {
        "bracket_index": 3,
        "name": "over_1560",
        "lower_smc": 1560.0,
        "upper_smc": null,
        "quantity_smc": 0.0,
        "national_excise_rate": 0.15,
        "national_excise_eur": 0.0,
        "regional_surcharge_rate": 0.031,
        "regional_surcharge_eur": 0.0,
        "vat_rate": 0.22,
        "commodity_eur": 0.0,
        "network_eur": 0.0,
        "variable_pretax_eur": 0.0,
        "taxable_base_eur": 0.0,
        "vat_eur": 0.0,
        "total_eur": 0.0
      }
    ]
  },
  "residuals": {
    "consumption_eur": 0.0,
    "fixed_eur": 0.0,
    "excise_including_regional_eur": 0.0,
    "vat_eur": 0.0,
    "total_eur": 0.0
  },
  "illustrative_prior_smc": 200.0,
  "bracket_assumed": "120_480 (second bracket)",
  "notes": [
    "Wholly invented UI invoice, no household evidence."
  ]
};

  const tax_rules = {
    statutory_sources: {
      national_excise: {
        title: 'Gazzetta Ufficiale — tabella aliquote accise gas naturale, Allegato I al TUA',
        scope: 'Aliquote di accisa sul gas naturale per usi civili/riscaldamento - Territori ex Cassa Mezzogiorno',
        url: 'https://www.gazzettaufficiale.it/atto/serie_generale/caricaArticoloDefault/originario?atto.codiceRedazionale=26A03280&atto.dataPubblicazioneGazzetta=2026-06-27&atto.tipoProvvedimento=TESTO+COORDINATO+DEL+DECRETO-LEGGE',
        verified_date: '2026-10-16'
      },
      regional_surcharge: {
        title: 'Regione Campania ARISGAN',
        scope: 'Regione Campania (L.R. 3 aprile 1991 n. 7 e succ. mod.)',
        url: 'https://archivioportale.regione.campania.it/it/printable/addizionale-regionale-all-accisa-sul-gas-naturale-arisgan-uccs',
        verified_date: '2026-10-16'
      },
      vat: {
        title: 'ARERA guida bolletta gas; Senato risposta interrogazione 4-00121',
        scope: 'Aliquota 10% fino a 480 Smc/anno su quota variabile; aliquota 22% oltre 480 Smc e su tutte le quote fisse',
        urls: [
          'https://www.arera.it/en/bolletta/guida-alla-lettura-delle-voci-di-spesa-gas',
          'https://www.senato.it/show-doc?id=1084673&leg=18&tipodoc=Sindispr'
        ],
        verified_date: '2026-10-16'
      }
    },
    brackets: [
      { name: '0_120', lower_smc: 0, upper_smc: 120, default_national_excise_eur_smc: 0.038, default_regional_surcharge_eur_smc: 0.019, default_vat_rate: 0.10 },
      { name: '120_480', lower_smc: 120, upper_smc: 480, default_national_excise_eur_smc: 0.135, default_regional_surcharge_eur_smc: 0.031, default_vat_rate: 0.10 },
      { name: '480_1560', lower_smc: 480, upper_smc: 1560, default_national_excise_eur_smc: 0.120, default_regional_surcharge_eur_smc: 0.031, default_vat_rate: 0.22 },
      { name: 'over_1560', lower_smc: 1560, upper_smc: null, default_national_excise_eur_smc: 0.150, default_regional_surcharge_eur_smc: 0.031, default_vat_rate: 0.22 }
    ]
  };

  return {
    year: 1,
    baseline,
    remaining,
    connection_status: 'disconnected',
    reference_bill,
    tax_rules,
    notes: [
      'First-year (Year 1) statutory gas accounting based on current scenario settings.',
      'Baseline uses 600 Smc annual consumption, 365 days, 0 prior cumulative Smc.',
      "Remaining scenario gas bill reflects 'electrified' scenario (connection status: disconnected).",
      'Statutory taxes include progressive national excise (Mezzogiorno), Campania regional surcharge (ARISGAN), and differentiated VAT.'
    ],
    ...overrides
  };
}

function createMockEnvironment(options = {}) {
  let elemCounter = 0;
  const elements = new Map();
  const classSets = new Map();

  function makeElement(id) {
    const cSet = new Set();
    classSets.set(id, cSet);
    const el = {
      id,
      tagName: 'DIV',
      innerHTML: '',
      textContent: '',
      value: '',
      disabled: false,
      hidden: false,
      style: {},
      dataset: {},
      checked: false,
      classList: {
        add: c => cSet.add(c),
        remove: c => cSet.delete(c),
        contains: c => cSet.has(c),
        toggle: (c, force) => {
          if (force === true) cSet.add(c);
          else if (force === false) cSet.delete(c);
          else if (cSet.has(c)) cSet.delete(c);
          else cSet.add(c);
          return cSet.has(c);
        }
      },
      setAttribute: (k, v) => { el[k] = v; },
      getAttribute: k => el[k] || null,
      removeAttribute: k => { delete el[k]; },
      addEventListener: () => {},
      removeEventListener: () => {},
      onclick: null,
      oninput: null,
      children: [],
      appendChild: node => { el.children.push(node); },
      prepend: node => { el.children.unshift(node); },
      closest: () => null,
      querySelector: sel => {
        if (sel === '.stale-comparison-notice') return elements.get('gasCostsStaleNotice') || null;
        if (sel === '[data-apply-best]') return el._btnApplyBest || null;
        return null;
      },
      querySelectorAll: sel => {
        if (sel === '[data-use]') return el._btnUses || [];
        return [];
      }
    };
    return el;
  }

  function getElement(id) {
    if (!elements.has(id)) {
      elements.set(id, makeElement(id));
    }
    return elements.get(id);
  }

  const domNodes = [];
  const mockDoc = {
    getElementById: getElement,
    createElement: tag => makeElement('elem_' + (++elemCounter)),
    querySelector: sel => {
      if (sel === '#gasCostsArticle' || sel === 'gasCostsArticle') return getElement('gasCostsArticle');
      if (sel === '#gasCostsContent' || sel === 'gasCostsContent') return getElement('gasCostsContent');
      if (sel === '#gasCostsStaleNotice') return getElement('gasCostsStaleNotice');
      if (sel === '#tariffsAdjacentArticle' || sel === 'tariffsAdjacentArticle') return getElement('tariffsAdjacentArticle');
      if (sel === '#adjacentPricesContent' || sel === 'adjacentPricesContent') return getElement('adjacentPricesContent');
      if (sel === '#controls' || sel === 'controls') return getElement('controls');
      if (typeof sel === 'string' && sel.startsWith('#')) return getElement(sel.slice(1));
      if (typeof sel === 'string') return getElement(sel);
      return null;
    },
    querySelectorAll: sel => {
      if (sel === '[data-control]') return domNodes;
      return [];
    },
    addEventListener: () => {},
    removeEventListener: () => {}
  };

  const dispatchedRequests = [];
  const mockFetch = async (path, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : {};
    dispatchedRequests.push({ path, body });
    if (path.includes('/api/evaluate')) {
      const s = body.settings || {};
      const gasPayload = makeGasCostsPayload();
      // If commodity or excise edited, update modeled amounts
      if (s.gas_commodity !== undefined) gasPayload.baseline.commodity_eur = 600.0 * Number(s.gas_commodity);
      return {
        ok: true,
        json: async () => ({
          result: {
            settings: deepClone(s),
            metrics: { npv_eur: { median: 5000 }, package_npv_eur: { median: 6000 } },
            finance: { capex_eur: 8000, conversion_capex_eur: 0, nominal_discount_rate: 0.045, real_discount_rate: 0.025 },
            layout: { kwp: 6.0, panel_count: 14, theoretical: false },
            energy: { load_kwh: 4000, pv_kwh: 7500, self_consumption: 0.6, useful_self_consumption: 0.6 },
            export_price_analysis: { baseload_price_eur_kwh: 0.128, export_weighted_captured_price_eur_kwh: 0.104, export_weighted_capture_factor: 0.815 },
            gas_costs: gasPayload
          },
          gas_costs: gasPayload,
          compute_seconds: 0.05
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const sandbox = {
    document: mockDoc,
    window: {
      location: { reload: () => {} },
      addEventListener: () => {},
      removeEventListener: () => {},
      localStorage: { getItem: () => null, setItem: () => {} },
      SharedScenarioSync
    },
    SharedScenarioSync,
    console,
    setTimeout: (fn, ms) => { if (options.immediateTimeout) fn(); return 1; },
    clearTimeout: () => {},
    setInterval: () => 1,
    clearInterval: () => {},
    fetch: mockFetch,
    localStorage: { getItem: () => null, setItem: () => {} }
  };

  vm.createContext(sandbox);
  vm.runInContext(appSource, sandbox);

  sandbox.getElement = getElement;
  vm.runInContext(`
    globalThis.FRIENDLY_NAMES = FRIENDLY_NAMES;
    globalThis.numericStep = numericStep;
    globalThis.control = control;
    globalThis.renderControls = renderControls;
    globalThis.renderGasCostsAudit = renderGasCostsAudit;
    globalThis.invalidateGasCostsAudit = invalidateGasCostsAudit;
    globalThis.acceptGasCostsAudit = acceptGasCostsAudit;
    globalThis.isGasCostsAuditMatching = isGasCostsAuditMatching;
    globalThis.editSetting = editSetting;
    globalThis.resetSetting = resetSetting;
    globalThis.schedule = schedule;
    globalThis.recompute = recompute;
    globalThis.applySharedState = applySharedState;
    globalThis.inputFingerprint = inputFingerprint;
    globalThis.renderAdjacentPrices = renderAdjacentPrices;
    globalThis.markOptimizerStale = markOptimizerStale;
    globalThis.markBatterySizingStale = markBatterySizingStale;
    globalThis.markSolarSizingStale = markSolarSizingStale;
    globalThis.invalidateSummarySensitivity = invalidateSummarySensitivity;
  `, sandbox);

  vm.runInContext(`
    sharedSync = new SharedScenarioSync({
      request: (path, opts) => fetch(path, opts).then(r => r.json()),
      onState: applySharedState,
      onStatus: () => {}
    });
  `, sandbox);

  Object.defineProperty(sandbox, 'sharedSync', {
    get: () => vm.runInContext('sharedSync', sandbox),
    set: val => { sandbox._sharedSync = val; vm.runInContext('sharedSync = globalThis._sharedSync;', sandbox); },
    configurable: true
  });

  Object.defineProperty(sandbox, 'schema', {
    get: () => vm.runInContext('schema', sandbox),
    set: val => { sandbox._schema = val; vm.runInContext('schema = globalThis._schema;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'settings', {
    get: () => vm.runInContext('settings', sandbox),
    set: val => { sandbox._settings = val; vm.runInContext('settings = globalThis._settings;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'quote', {
    get: () => vm.runInContext('quote', sandbox),
    set: val => { sandbox._quote = val; vm.runInContext('quote = globalThis._quote;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'current', {
    get: () => vm.runInContext('current', sandbox),
    set: val => { sandbox._current = val; vm.runInContext('current = globalThis._current;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'acceptedInputFingerprint', {
    get: () => vm.runInContext('acceptedInputFingerprint', sandbox),
    set: val => { sandbox._acceptedInputFingerprint = val; vm.runInContext('acceptedInputFingerprint = globalThis._acceptedInputFingerprint;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'gasCostsAuditOutdated', {
    get: () => vm.runInContext('gasCostsAuditOutdated', sandbox),
    set: val => { sandbox._gasCostsAuditOutdated = val; vm.runInContext('gasCostsAuditOutdated = globalThis._gasCostsAuditOutdated;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'loanPending', {
    get: () => vm.runInContext('loanPending', sandbox),
    set: val => { sandbox._loanPending = val; vm.runInContext('loanPending = globalThis._loanPending;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'requestId', {
    get: () => vm.runInContext('requestId', sandbox),
    set: val => { sandbox._requestId = val; vm.runInContext('requestId = globalThis._requestId;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'optimizerStale', {
    get: () => vm.runInContext('optimizerStale', sandbox),
    set: val => { sandbox._optimizerStale = val; vm.runInContext('optimizerStale = globalThis._optimizerStale;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'optimizerResult', {
    get: () => vm.runInContext('optimizerResult', sandbox),
    set: val => { sandbox._optimizerResult = val; vm.runInContext('optimizerResult = globalThis._optimizerResult;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'batteryData', {
    get: () => vm.runInContext('batteryData', sandbox),
    set: val => { sandbox._batteryData = val; vm.runInContext('batteryData = globalThis._batteryData;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'solarData', {
    get: () => vm.runInContext('solarData', sandbox),
    set: val => { sandbox._solarData = val; vm.runInContext('solarData = globalThis._solarData;', sandbox); },
    configurable: true
  });
  Object.defineProperty(sandbox, 'summarySensitivityOutdated', {
    get: () => vm.runInContext('summarySensitivityOutdated', sandbox),
    set: val => { sandbox._summarySensitivityOutdated = val; vm.runInContext('summarySensitivityOutdated = globalThis._summarySensitivityOutdated;', sandbox); },
    configurable: true
  });

  return { sandbox, elements, dispatchedRequests, getElement };
}

test('1. Gas controls: friendly names, precision, bounds, stepping, and fraction percentage display', () => {
  const { sandbox } = createMockEnvironment();

  const fn = sandbox.FRIENDLY_NAMES;
  assert.equal(fn.gas_commodity, 'Gas commodity (pre-tax)');
  assert.equal(fn.gas_regulated, 'Gas network/system charges (pre-tax)');
  assert.equal(fn.gas_fixed_day, 'Gas supplier + network fixed charges (pre-tax)');
  assert.equal(fn.gas_excise_0_120, 'National gas excise: 0–120 Smc/yr');
  assert.equal(fn.gas_excise_120_480, 'National gas excise: 120–480 Smc/yr');
  assert.equal(fn.gas_excise_480_1560, 'National gas excise: 480–1560 Smc/yr');
  assert.equal(fn.gas_excise_over_1560, 'National gas excise: over 1560 Smc/yr');
  assert.equal(fn.gas_regional_0_120, 'Campania regional gas surcharge: 0–120 Smc/yr');
  assert.equal(fn.gas_regional_over_120, 'Campania regional gas surcharge: over 120 Smc/yr');
  assert.equal(fn.gas_vat_reduced, 'Gas VAT reduced: ≤480 Smc/yr variable');
  assert.equal(fn.gas_vat_standard, 'Gas VAT standard: >480 Smc/yr variable & fixed');

  // Verify numericStep: 0.001 precision for EUR/Smc, EUR/day, and fractions
  assert.equal(sandbox.numericStep({ name: 'gas_commodity', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_regulated', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_fixed_day', unit: 'EUR/day' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_excise_0_120', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_excise_120_480', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_excise_480_1560', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_excise_over_1560', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_regional_0_120', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_regional_over_120', unit: 'EUR/Smc' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_vat_reduced', unit: 'fraction' }), 0.001);
  assert.equal(sandbox.numericStep({ name: 'gas_vat_standard', unit: 'fraction' }), 0.001);

  // Setup schema and settings in sandbox
  sandbox.schema = {
    defaults: {
      gas_commodity: 0.470,
      gas_regulated: 0.395,
      gas_fixed_day: 0.280,
      gas_excise_0_120: 0.038,
      gas_excise_120_480: 0.135,
      gas_excise_480_1560: 0.120,
      gas_excise_over_1560: 0.150,
      gas_regional_0_120: 0.019,
      gas_regional_over_120: 0.031,
      gas_vat_reduced: 0.10,
      gas_vat_standard: 0.22
    },
    records: [
      { name: 'gas_commodity', unit: 'EUR/Smc', bounds: [0, 2], group: 'gas', status: 'verified', source: 'Utility Bill' },
      { name: 'gas_regulated', unit: 'EUR/Smc', bounds: [0, 2], group: 'gas', status: 'verified', source: 'Utility Bill' },
      { name: 'gas_fixed_day', unit: 'EUR/day', bounds: [0, 1], group: 'gas', status: 'verified', source: 'Utility Bill' },
      { name: 'gas_excise_0_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'D.Lgs. 504/1995 TUA' },
      { name: 'gas_excise_120_480', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'D.Lgs. 504/1995 TUA' },
      { name: 'gas_excise_480_1560', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'D.Lgs. 504/1995 TUA' },
      { name: 'gas_excise_over_1560', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'D.Lgs. 504/1995 TUA' },
      { name: 'gas_regional_0_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'L.R. Campania 1/1991' },
      { name: 'gas_regional_over_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'L.R. Campania 1/1991' },
      { name: 'gas_vat_reduced', unit: 'fraction', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'DPR 633/1972' },
      { name: 'gas_vat_standard', unit: 'fraction', bounds: [0, 1], group: 'gas', status: 'statutory', source: 'DPR 633/1972' }
    ]
  };
  sandbox.settings = { ...sandbox.schema.defaults };

  // Verify fraction units explicitly display raw and percentage (0.10 = 10% and 0.22 = 22%)
  const reducedVatHtml = sandbox.control(sandbox.schema.records.find(r => r.name === 'gas_vat_reduced'));
  assert.ok(reducedVatHtml.includes('fraction (0.10 = 10%)'), 'Reduced VAT displays 0.10 = 10%');
  assert.ok(reducedVatHtml.includes('data-save-default="gas_vat_reduced"'), 'Save default button exists');
  assert.ok(reducedVatHtml.includes('data-reset-setting="gas_vat_reduced"'), 'Reset button exists');

  const standardVatHtml = sandbox.control(sandbox.schema.records.find(r => r.name === 'gas_vat_standard'));
  assert.ok(standardVatHtml.includes('fraction (0.22 = 22%)'), 'Standard VAT displays 0.22 = 22%');

  // Verify renderControls builds section headers in Electricity & gas
  sandbox.renderControls();
  const controlsHtml = sandbox.getElement('controls').innerHTML;
  assert.ok(controlsHtml.includes('Natural gas retail tariffs (pre-tax)'), 'Gas pre-tax section header rendered');
  assert.ok(controlsHtml.includes('Gas consumption taxes &amp; VAT'), 'Gas consumption taxes section header rendered');
  assert.ok(controlsHtml.includes('distinct from solar-export income taxes'), 'Gas consumption taxes distinguished from solar export income taxes');
});

test('2. Edited and zero values: preservation of numerical values, step bounds, and reset machinery', () => {
  const { sandbox } = createMockEnvironment();

  sandbox.schema = {
    defaults: {
      gas_commodity: 0.470,
      gas_excise_120_480: 0.135,
      gas_regional_0_120: 0.019,
      gas_vat_reduced: 0.10
    },
    records: [
      { name: 'gas_commodity', unit: 'EUR/Smc', bounds: [0, 2], group: 'gas' },
      { name: 'gas_excise_120_480', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
      { name: 'gas_regional_0_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
      { name: 'gas_vat_reduced', unit: 'fraction', bounds: [0, 1], group: 'gas' }
    ]
  };
  sandbox.settings = { ...sandbox.schema.defaults };

  // Set zero value on regional surcharge
  sandbox.editSetting('gas_regional_0_120', 0.0);
  assert.strictEqual(sandbox.settings.gas_regional_0_120, 0.0, 'Zero rate is preserved as 0.0');

  // Set edited value on excise
  sandbox.editSetting('gas_excise_120_480', 0.205);
  assert.strictEqual(sandbox.settings.gas_excise_120_480, 0.205, 'Edited excise is preserved with 0.001 precision');

  // Set edited value on reduced VAT
  sandbox.editSetting('gas_vat_reduced', 0.05);
  assert.strictEqual(sandbox.settings.gas_vat_reduced, 0.05, 'Edited reduced VAT is preserved');

  // Verify fraction formatting updates for custom values
  const editedVatHtml = sandbox.control(sandbox.schema.records.find(r => r.name === 'gas_vat_reduced'));
  assert.ok(editedVatHtml.includes('fraction (0.05 = 5%)'), 'Custom reduced VAT displays 0.05 = 5%');

  // Reset setting
  sandbox.resetSetting('gas_excise_120_480');
  assert.strictEqual(sandbox.settings.gas_excise_120_480, 0.135, 'Excise reset to saved default 0.135');
  assert.strictEqual(sandbox.settings.gas_regional_0_120, 0.0, 'Other edited settings survive reset');
});

test('3. Auditable gas costs article: baseline vs remaining side-by-side, 9 required rows, and 600 reference disclosure', () => {
  const { sandbox } = createMockEnvironment();

  const gasCosts = makeGasCostsPayload();
  sandbox.current = {
    result: {
      settings: { load_scenario: 'electrified', gas_regulated: 0.395 },
      gas_costs: gasCosts
    },
    gas_costs: gasCosts
  };

  sandbox.renderGasCostsAudit();
  const html = sandbox.getElement('gasCostsContent').innerHTML;

  // Header and reference disclosure
  assert.ok(html.includes('First-Year Annual Baseline'), 'Header includes First-Year Annual Baseline');
  assert.ok(html.includes('Profile annual reference (not single-month annualization)'), 'Explicit 600 annual reference disclosure present');
  assert.ok(html.includes('Selected Scenario: Electrified'), 'Selected scenario labeled');
  assert.ok(html.includes('Meter disconnected (gas service eliminated)'), 'Connection status displayed for electrified scenario');

  // All 9 required rows present
  assert.ok(html.includes('Gas commodity (pre-tax)'), 'Row 1: Commodity pre-tax');
  assert.ok(html.includes('Gas network/system charges (pre-tax)'), 'Row 2: Network/system pre-tax');
  assert.ok(html.includes('Gas supplier + network fixed charges (pre-tax)'), 'Row 3: Fixed pre-tax');
  assert.ok(html.includes('National gas excise'), 'Row 4: National excise');
  assert.ok(html.includes('Campania regional surcharge (ARISGAN)'), 'Row 5: Campania regional surcharge');
  assert.ok(html.includes('Reduced VAT'), 'Row 6: Reduced VAT');
  assert.ok(html.includes('Standard VAT'), 'Row 7: Standard VAT');
  assert.ok(html.includes('Total VAT'), 'Row 8: Total VAT');
  assert.ok(html.includes('Total first-year gas bill'), 'Row 9: Total first-year bill');

  // Check VAT formulas rendered in table
  assert.ok(html.includes('base × 10.0%'), 'Reduced VAT taxable base times current rate displayed');
  assert.ok(html.includes('(var) +'), 'Standard VAT variable base + fixed base explicitly added');
  assert.ok(html.includes('(fixed) ='), 'Standard VAT total base times standard rate displayed');

  // Verify natural gas benchmark in renderAdjacentPrices is pre-tax
  sandbox.renderAdjacentPrices();
  const adjHtml = sandbox.getElement('adjacentPricesContent').innerHTML;
  assert.ok(adjHtml.includes('Natural Gas Commodity Rate (pre-tax)'), 'renderAdjacentPrices explicitly labels commodity rate pre-tax');
});

test('4. Retained zero-volume and connected remaining scenarios', () => {
  const { sandbox } = createMockEnvironment();

  const retainedGas = makeGasCostsPayload({
    connection_status: 'retained_zero_volume',
    remaining: {
      volume: 0.0,
      days: 365,
      prior_year_smc: 0.0,
      year: 1,
      include_fixed: true,
      commodity_eur: 0.0,
      network_eur: 0.0,
      fixed_eur: 102.20,
      national_excise_eur: 0.0,
      regional_surcharge_eur: 0.0,
      reduced_vat_base_eur: 0.0,
      reduced_vat_rate: 0.10,
      reduced_vat_eur: 0.0,
      standard_vat_variable_base_eur: 0.0,
      standard_vat_fixed_base_eur: 102.20,
      standard_vat_total_base_eur: 102.20,
      standard_vat_rate: 0.22,
      standard_vat_eur: 22.484,
      vat_eur: 22.484,
      total_eur: 124.684,
      brackets: []
    }
  });

  sandbox.current = { result: { settings: { load_scenario: 'electrified' }, gas_costs: retainedGas }, gas_costs: retainedGas };
  sandbox.renderGasCostsAudit();
  const html = sandbox.getElement('gasCostsContent').innerHTML;

  assert.ok(html.includes('Meter active (retained zero volume, fixed fee only)'), 'Retained zero volume label rendered');
  assert.ok(html.includes('€124.68'), 'Fixed fee only total rendered correctly');
});

test('5. Detail fold: progressive brackets allocation, rendered current rates, and statutory sources', () => {
  const { sandbox } = createMockEnvironment();

  // Create custom rates payload (e.g. 0.20 excise and 0.05 reduced VAT)
  const customGas = makeGasCostsPayload();
  customGas.baseline.reduced_vat_rate = 0.05;
  customGas.baseline.brackets[1].national_excise_rate = 0.200;
  customGas.baseline.brackets[1].vat_rate = 0.05;

  sandbox.current = { result: { settings: { load_scenario: 'baseline', gas_regulated: 0.420 }, gas_costs: customGas }, gas_costs: customGas };
  sandbox.renderGasCostsAudit();
  const html = sandbox.getElement('gasCostsContent').innerHTML;

  // Brackets fold
  assert.ok(html.includes('Annual progressive brackets allocation (auditable detail)'), 'Brackets fold present');
  assert.ok(html.includes('120–480 Smc'), '120-480 bracket band displayed');
  assert.ok(html.includes('€0.200/Smc'), 'Current excise rate (0.200) displayed instead of default factory metadata');
  assert.ok(html.includes('5.0%'), 'Current custom reduced VAT rate displayed instead of hardcoded 10%');

  // Network flat approximation explanation
  assert.ok(html.includes('Pre-tax network and system charges are modeled at a flat approximation of'), 'Network flat approximation described');
  assert.ok(html.includes('€0.420/Smc'), 'Current network setting value rendered in note');
  assert.ok(html.includes('Underlying real network slabs and quarterly ARERA tariff adjustments are not reconstructed'), 'Real slabs not reconstructed disclaimer present');

  // Statutory sources
  assert.ok(html.includes('Gazzetta Ufficiale'), 'National excise statutory source link present');
  assert.ok(html.includes('Regione Campania ARISGAN'), 'Regional surcharge statutory source link present');
  assert.ok(html.includes('ARERA'), 'VAT statutory source link present');
  assert.ok(html.includes('verified 2026-10-16'), 'Verified date 2026-10-16 displayed');
});

test('6. Reconciliation fold: utility reference invoice 50 Smc / 30 days vs modeled reference and caveats', () => {
  const { sandbox } = createMockEnvironment();

  const gasCosts = makeGasCostsPayload();
  sandbox.current = { result: { settings: { load_scenario: 'baseline' }, gas_costs: gasCosts }, gas_costs: gasCosts };
  sandbox.renderGasCostsAudit();
  const html = sandbox.getElement('gasCostsContent').innerHTML;

  assert.ok(html.includes('Utility invoice reconciliation (50 Smc / 30 days reference)'), 'Reconciliation fold present');
  assert.ok(html.includes('€45.00'), 'Consumption invoice amount present');
  assert.ok(html.includes('€9.00'), 'Fixed fee invoice amount present');
  assert.ok(html.includes('€8.30'), 'Excise + surcharge invoice amount present');
  assert.ok(html.includes('€7.31'), 'VAT invoice amount present');
  assert.ok(html.includes('€69.61'), 'Total invoice amount present');

  // Required caveats
  assert.ok(html.includes('confirm actual cumulative YTD consumption'), 'Assumes 120-480 bracket disclosed');
  assert.ok(html.includes('The 200.0 Smc prior cumulative figure is strictly an illustrative assumption, not measured'), '200 Smc illustrative disclosure present');
  assert.ok(html.includes('identify the applicable progressive brackets'), 'Bracket tolerance disclosed');
  assert.ok(html.includes('Actual calendar-YTD consumption cannot be inferred from a rolling 12-month model'), 'Cannot infer YTD from rolling 12-month model disclosed');
  assert.ok(html.includes('do not reset monthly'), 'No monthly reset disclosed');

  // When settings differ from reference tariffs, variance is displayed plainly
  const editedGas = makeGasCostsPayload();
  editedGas.reference_bill.residuals.total_eur = 3.50;
  sandbox.current = { result: { settings: { load_scenario: 'baseline' }, gas_costs: editedGas }, gas_costs: editedGas };
  sandbox.renderGasCostsAudit();
  const editedHtml = sandbox.getElement('gasCostsContent').innerHTML;
  assert.ok(editedHtml.includes('Current settings variance:'), 'Variance warning rendered when residuals non-zero');
  assert.ok(editedHtml.includes('€3.50'), 'Variance amount rendered');
});

test('7. Invalidation: gas tax edit triggers regular schedule, marks optimizer/battery/solar/loan/audit stale, rejects in-flight responses', async () => {
  const { sandbox, getElement } = createMockEnvironment();

  const allGasRecords = [
    { name: 'gas_excise_0_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
    { name: 'gas_excise_120_480', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
    { name: 'gas_excise_480_1560', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
    { name: 'gas_excise_over_1560', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
    { name: 'gas_regional_0_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
    { name: 'gas_regional_over_120', unit: 'EUR/Smc', bounds: [0, 1], group: 'gas' },
    { name: 'gas_vat_reduced', unit: 'fraction', bounds: [0, 1], group: 'gas' },
    { name: 'gas_vat_standard', unit: 'fraction', bounds: [0, 1], group: 'gas' },
    { name: 'south_panels', unit: 'panels', bounds: [0, 22], group: 'layout' },
    { name: 'battery_kwh', unit: 'kWh', bounds: [0, 25], group: 'equipment' }
  ];
  const allGasDefaults = {
    gas_excise_0_120: 0.038,
    gas_excise_120_480: 0.135,
    gas_excise_480_1560: 0.120,
    gas_excise_over_1560: 0.150,
    gas_regional_0_120: 0.019,
    gas_regional_over_120: 0.031,
    gas_vat_reduced: 0.10,
    gas_vat_standard: 0.22,
    south_panels: 12,
    battery_kwh: 10
  };

  sandbox.schema = {
    defaults: allGasDefaults,
    records: allGasRecords
  };
  sandbox.settings = { ...sandbox.schema.defaults };
  sandbox.quote = { line_items: [] };
  sandbox.renderControls();

  // Setup DOM elements for dependent features
  const controlsEl = getElement('controls');
  const bestConfigEl = getElement('bestConfiguration');
  const applyBestBtn = { disabled: false, setAttribute: (k, v) => {}, removeAttribute: (k) => {} };
  bestConfigEl._btnApplyBest = applyBestBtn;

  const candidatesEl = getElement('candidates');
  candidatesEl._btnUses = [{ disabled: false, setAttribute: () => {} }];

  const batResultsWrap = getElement('batResultsWrap');
  const solarResultsWrap = getElement('solarResultsWrap');
  const applySolarBtn = getElement('applySolarSystem');
  const saveLoanBtn = getElement('saveLoanSimulation');
  const gasCostsArticle = getElement('gasCostsArticle');
  const gasCostsStaleNotice = getElement('gasCostsStaleNotice');

  // Initial sync state
  sandbox.sharedSync.receive({
    revision: 'rev_base',
    settings: deepClone(sandbox.settings),
    quote: deepClone(sandbox.quote),
    defaults: deepClone(sandbox.schema.defaults),
    records: deepClone(sandbox.schema.records)
  });

  const GAS_TAX_KEYS = [
    'gas_excise_0_120',
    'gas_excise_120_480',
    'gas_excise_480_1560',
    'gas_excise_over_1560',
    'gas_regional_0_120',
    'gas_regional_over_120',
    'gas_vat_reduced',
    'gas_vat_standard'
  ];

  // 1. Assert REAL controls.oninput invalidation for EACH of the 8 gas keys, including zero
  for (const key of GAS_TAX_KEYS) {
    for (const testVal of [0, 0.25]) {
      // Re-arm all systems to valid, accepted states
      sandbox.settings = { ...sandbox.schema.defaults };
      sandbox.acceptedInputFingerprint = sandbox.inputFingerprint(sandbox.settings, sandbox.quote);
      sandbox.loanPending = false;
      saveLoanBtn.disabled = false;
      sandbox.optimizerResult = { candidates: [{ id: 1 }] };
      sandbox.optimizerStale = false;
      bestConfigEl.classList.remove('stale');
      applyBestBtn.disabled = false;
      sandbox.batteryData = { economics: {} };
      sandbox.solarData = { layout: {} };
      batResultsWrap.classList.remove('stale');
      solarResultsWrap.classList.remove('stale');
      applySolarBtn.disabled = false;
      sandbox.summarySensitivityOutdated = false;
      sandbox.gasCostsAuditOutdated = false;
      gasCostsArticle.classList.remove('stale-overlay');
      gasCostsStaleNotice.hidden = true;

      const gasPayload = makeGasCostsPayload();
      sandbox.current = { result: { settings: { ...sandbox.settings }, gas_costs: gasPayload }, gas_costs: gasPayload };
      sandbox.renderGasCostsAudit();

      assert.equal(sandbox.isGasCostsAuditMatching(sandbox.current.result), true, `Gas costs matching initially for ${key}`);
      assert.equal(gasCostsArticle.classList.contains('stale-overlay'), false, `No stale overlay initially for ${key}`);

      // Dispatch REAL controls.oninput (normal input handler, real schedule without stubbing invalidation)
      controlsEl.oninput({
        target: {
          dataset: { setting: key },
          value: String(testVal),
          type: 'number'
        }
      });

      // Assert settings updated correctly
      assert.strictEqual(sandbox.settings[key], testVal, `Settings updated to ${testVal} for ${key}`);

      // Regular schedule persistence staging
      assert.strictEqual(sandbox.sharedSync.pending(), true, `Persistence staging pending for ${key}=${testVal}`);
      assert.strictEqual(sandbox.sharedSync.dirtySettings.has(key), true, `dirtySettings contains ${key}`);
      assert.strictEqual(sandbox.sharedSync.dirtySettings.get(key).value, testVal, `Staged dirty value matches ${testVal} for ${key}`);

      // Optimizer stale / unapplicable
      assert.strictEqual(sandbox.optimizerStale, true, `optimizerStale marked true for ${key}`);
      assert.strictEqual(bestConfigEl.classList.contains('stale'), true, `bestConfiguration marked stale for ${key}`);
      assert.strictEqual(applyBestBtn.disabled, true, `applyBestBtn disabled for ${key}`);

      // Battery sizing stale
      assert.strictEqual(batResultsWrap.classList.contains('stale'), true, `batResultsWrap marked stale for ${key}`);

      // Solar sizing stale and unapplicable
      assert.strictEqual(solarResultsWrap.classList.contains('stale'), true, `solarResultsWrap marked stale for ${key}`);
      assert.strictEqual(applySolarBtn.disabled, true, `applySolarBtn disabled for ${key}`);

      // Loan disabled and invalidated
      assert.strictEqual(sandbox.loanPending, true, `loanPending marked true for ${key}`);
      assert.strictEqual(sandbox.acceptedInputFingerprint, null, `acceptedInputFingerprint cleared to null for ${key}`);
      assert.strictEqual(saveLoanBtn.disabled, true, `saveLoanSimulation disabled for ${key}`);

      // Summary sensitivity invalidated
      assert.strictEqual(sandbox.summarySensitivityOutdated, true, `summarySensitivityOutdated marked true for ${key}`);

      // Audit invalidated
      assert.strictEqual(sandbox.gasCostsAuditOutdated, true, `gasCostsAuditOutdated marked true for ${key}`);
      assert.strictEqual(sandbox.isGasCostsAuditMatching(sandbox.current.result), false, `Audit no longer matching for ${key}`);
      assert.strictEqual(gasCostsArticle.classList.contains('stale-overlay'), true, `gasCostsArticle has stale-overlay for ${key}`);
      assert.strictEqual(gasCostsStaleNotice.hidden, false, `gasCostsStaleNotice visible for ${key}`);
    }
  }

  // 2. Genuinely deferred request resolution:
  // Start request A, change tax, start B, resolve B FIRST then A, verify accepted financial/audit payload remains B
  const deferredResolvers = [];
  sandbox.fetch = async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : {};
    if (url.includes('/api/evaluate')) {
      const s = body.settings || {};
      const gasPayload = makeGasCostsPayload();
      const exciseVal = Number(s.gas_excise_120_480 ?? 0.135);
      gasPayload.baseline.brackets[1].national_excise_rate = exciseVal;
      gasPayload.baseline.brackets[1].national_excise_eur = 360.0 * exciseVal;
      gasPayload.baseline.total_eur = 1000 + exciseVal * 1000;
      return new Promise((resolve) => {
        deferredResolvers.push({
          settings: deepClone(s),
          exciseVal,
          resolve: () => resolve({
            ok: true,
            json: async () => ({
              result: {
                settings: deepClone(s),
                metrics: { npv_eur: { median: 5000 + exciseVal * 1000 } },
                finance: { capex_eur: 8000 },
                gas_costs: gasPayload
              },
              gas_costs: gasPayload,
              compute_seconds: 0.05
            })
          })
        });
      });
    }
    return { ok: true, json: async () => ({}) };
  };

  // Start request A with tax edit 0.200
  controlsEl.oninput({ target: { dataset: { setting: 'gas_excise_120_480' }, value: '0.200', type: 'number' } });
  const pA = sandbox.recompute({ sync: false });
  assert.equal(deferredResolvers.length, 1, 'Request A is deferred in flight');

  // Change tax to B with edit 0.350 and start request B
  controlsEl.oninput({ target: { dataset: { setting: 'gas_excise_120_480' }, value: '0.350', type: 'number' } });
  const pB = sandbox.recompute({ sync: false });
  assert.equal(deferredResolvers.length, 2, 'Request B is deferred in flight');

  // Resolve B FIRST!
  const reqA = deferredResolvers[0];
  const reqB = deferredResolvers[1];
  reqB.resolve();
  await pB;

  // Accepted result must be B
  assert.strictEqual(sandbox.current.result.settings.gas_excise_120_480, 0.350, 'Accepted result settings match B after B resolves first');
  assert.strictEqual(sandbox.current.result.gas_costs.baseline.brackets[1].national_excise_rate, 0.350, 'Accepted gas costs rate matches B');
  assert.strictEqual(sandbox.current.result.metrics.npv_eur.median, 5000 + 0.350 * 1000, 'Accepted financial metrics match B');

  // Now resolve A SECOND!
  reqA.resolve();
  await pA;

  // Verify accepted financial/audit payload REMAINS B (stale A was rejected!)
  assert.strictEqual(sandbox.current.result.settings.gas_excise_120_480, 0.350, 'Accepted result settings remain B after late A resolves');
  assert.strictEqual(sandbox.current.result.gas_costs.baseline.brackets[1].national_excise_rate, 0.350, 'Accepted gas costs rate remains B');
  assert.strictEqual(sandbox.current.result.metrics.npv_eur.median, 5000 + 0.350 * 1000, 'Accepted financial metrics remain B');

  // 3. Remote sync invalidation: remote settings update marks gasCostsAudit, loan, optimizer, battery, solar stale
  sandbox.gasCostsAuditOutdated = false;
  sandbox.loanPending = false;
  sandbox.optimizerStale = false;
  sandbox.summarySensitivityOutdated = false;
  gasCostsArticle.classList.remove('stale-overlay');
  batResultsWrap.classList.remove('stale');
  solarResultsWrap.classList.remove('stale');
  applySolarBtn.disabled = false;

  sandbox.applySharedState({
    settings: { ...sandbox.settings, gas_excise_120_480: 0.180 },
    defaults: sandbox.schema.defaults,
    records: sandbox.schema.records,
    quote: { line_items: [] }
  });

  assert.strictEqual(sandbox.gasCostsAuditOutdated, true, 'applySharedState marks gasCostsAuditOutdated');
  assert.strictEqual(gasCostsArticle.classList.contains('stale-overlay'), true, 'gasCostsArticle marked stale by sync');
  assert.strictEqual(sandbox.summarySensitivityOutdated, true, 'applySharedState marks summarySensitivityOutdated');
  assert.strictEqual(batResultsWrap.classList.contains('stale'), true, 'batResultsWrap marked stale by sync');
  assert.strictEqual(solarResultsWrap.classList.contains('stale'), true, 'solarResultsWrap marked stale by sync');

  // Recompute following remote sync invalidates loan and optimizer
  sandbox.recompute({ sync: false });
  assert.strictEqual(sandbox.loanPending, true, 'recompute after sync marks loanPending');
  assert.strictEqual(sandbox.acceptedInputFingerprint, null, 'recompute after sync clears acceptedInputFingerprint');
  assert.strictEqual(sandbox.optimizerStale, true, 'recompute after sync marks optimizerStale');
});

test('8. Missing gas_costs handled gracefully and tolerates missing DOM article in VM tests', () => {
  const { sandbox } = createMockEnvironment();

  // Missing gas_costs
  sandbox.current = { result: { settings: {} } };
  sandbox.renderGasCostsAudit();
  const html = sandbox.getElement('gasCostsContent').innerHTML;
  assert.ok(html.includes('Gas cost audit unavailable for this scenario result'), 'Graceful refresh message rendered');

  // Tolerates missing article/content without throwing
  const origGetElement = sandbox.document.getElementById;
  const origQuerySelector = sandbox.document.querySelector;
  sandbox.document.getElementById = () => null;
  sandbox.document.querySelector = () => null;
  assert.doesNotThrow(() => {
    sandbox.invalidateGasCostsAudit();
    sandbox.acceptGasCostsAudit();
    sandbox.renderGasCostsAudit();
  }, 'DOM functions tolerate null elements without throwing');
  sandbox.document.getElementById = origGetElement;
  sandbox.document.querySelector = origQuerySelector;
});
