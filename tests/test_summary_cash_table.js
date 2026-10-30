'use strict';
/* Unit test suite for Summary Annual Cash Flows disclosure and table (S1/S2).
 * Exercises correlated per-weather totals (sum median != median sum),
 * real deflation, negative outflows, latest data export while collapsed,
 * disclosure state persistence/reset, Median/Range state attributes,
 * and print open/restore lifecycle.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appPath = [
  path.join(__dirname, '../feasibility/web/app.js'),
  path.join(process.cwd(), 'feasibility/web/app.js')
].find(p => p && fs.existsSync(p));
const appSource = fs.readFileSync(appPath, 'utf8');

// Extract only cash table helpers and relevant functions from app.js
const escFn = appSource.match(/const\s+esc\s*=\s*[^;]+;/)?.[0] || 'const esc=s=>String(s??\'\').replace(/[&<>"\']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;","\'":"&#39;"}[c]));';
const nfFn = appSource.match(/const\s+nf\s*=\s*[^;]+;/)?.[0] || 'const nf=(n,d=0)=>n==null?"—":Number(n).toLocaleString("en-IE",{maximumFractionDigits:d,minimumFractionDigits:d});';
const fmtFn = appSource.match(/const\s+fmt\s*=\s*[^;]+;/)?.[0] || 'const fmt=(n,type="number")=>n==null?"—":typeof type==="number"?nf(n,type):type==="eur"?"€"+nf(n):type==="eur2"?"€"+nf(n,2):type==="pct"?nf(n*100,1)+"%":type==="lcoe"?"€"+nf(n,3):type==="years"?nf(n,1)+" y":nf(n);';

const downloadFn = appSource.slice(
  appSource.indexOf('function download(data,name,type='),
  appSource.indexOf('let comparisonToken=0;')
);

const cashTableBlock = appSource.slice(
  appSource.indexOf('let cashTableMode'),
  appSource.indexOf('function financedPaybackCard(')
);

const scopeCashMetricsFn = appSource.slice(
  appSource.indexOf('function getScopeCashMetrics('),
  appSource.indexOf('function syncSummaryControls()')
);

function createHarness() {
  const elements = new Map();
  const sessionStore = new Map();
  const sessionStorage = {
    getItem: k => sessionStore.get(k) || null,
    setItem: (k, v) => sessionStore.set(k, String(v)),
    removeItem: k => sessionStore.delete(k)
  };

  const windowListeners = new Map();
  const windowMock = {
    addEventListener: (evt, fn) => {
      if (!windowListeners.has(evt)) windowListeners.set(evt, []);
      windowListeners.get(evt).push(fn);
    },
    dispatchEvent: evt => {
      const list = windowListeners.get(evt.type) || [];
      list.forEach(fn => fn(evt));
    }
  };

  const $ = id => {
    if (!elements.has(id)) {
      const classSet = new Set();
      const attrs = new Map();
      const listeners = new Map();
      let innerHTMLVal = '';
      elements.set(id, {
        id,
        get innerHTML() { return innerHTMLVal; },
        set innerHTML(v) {
          innerHTMLVal = v;
          if (id === 'cashTable' && typeof v === 'string') {
            const m = v.match(/<table[^>]*class="([^"]*cash-table[^"]*)"[^>]*data-mode="([^"]*)"/);
            if (m) {
              const tableElem = $('cashTableElement');
              tableElem.setAttribute('data-mode', m[2]);
            }
          }
        },
        textContent: '',
        value: '',
        open: false,
        hidden: false,
        disabled: false,
        querySelector: sel => {
          if (sel === '.cash-table') return elements.get('cashTableElement');
          return null;
        },
        classList: {
          add: c => classSet.add(c),
          remove: c => classSet.delete(c),
          contains: c => classSet.has(c),
          toggle: (c, force) => {
            if (force === true) classSet.add(c);
            else if (force === false) classSet.delete(c);
            else if (classSet.has(c)) classSet.delete(c);
            else classSet.add(c);
            return classSet.has(c);
          }
        },
        setAttribute: (k, v) => attrs.set(k, String(v)),
        getAttribute: k => attrs.get(k) || null,
        addEventListener: (evt, fn) => {
          if (!listeners.has(evt)) listeners.set(evt, []);
          listeners.get(evt).push(fn);
        },
        dispatchEvent: evt => {
          const list = listeners.get(evt.type) || [];
          list.forEach(fn => fn(evt));
        }
      });
    }
    return elements.get(id);
  };

  let downloaded = null;
  const context = {
    $,
    current: null,
    quote:{line_items:[]},
    settings:{},
    summaryScope: 'solar',
    summaryCash: 'cash',
    isItemizedPricingActive: () => false,
    range: (v, u) => String(v ?? ''),
    inputFingerprint:(settings,quote)=>JSON.stringify({settings,quote}),
    elements,
    sessionStorage,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    window: windowMock,
    document: {
      getElementById: $,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      createElement: tag => {
        let dl = '';
        return {
          set href(v) {},
          set download(v) { dl = v; if (downloaded) downloaded.filename = dl; },
          click: () => { if (downloaded) downloaded.clicked = true; }
        };
      }
    },
    URL: {
      createObjectURL: blob => {
        downloaded = { blob, clicked: false };
        return 'blob:mock-url';
      },
      revokeObjectURL: () => {}
    },
    Blob: globalThis.Blob,
    Math,
    Number,
    String,
    Date,
    Array,
    JSON,
    parseInt,
    parseFloat,
    isFinite,
    setTimeout: (fn, ms) => {},
    clearTimeout: () => {},
    console
  };

  vm.createContext(context);
  vm.runInContext([escFn, nfFn, fmtFn, downloadFn, cashTableBlock, scopeCashMetricsFn].join('\n'), context);

  return { context, elements, $, sessionStorage, windowMock, getDownloaded: () => downloaded };
}

async function runAllTests() {
  // Test 1: Correlated per-weather totals where sum of medians != median of sums
  {
    const { context, elements } = createHarness();
    // 3 weather paths across 21 years (y=0 capex, then years 1-20)
    // Construct 3 paths where for year 1 and 2:
    // Path 0: y1=100, y2=800, y3..20=0 -> sum = 900
    // Path 1: y1=300, y2=700, y3..20=0 -> sum = 1000
    // Path 2: y1=600, y2=1000, y3..20=0 -> sum = 1600
    // Medians per year: y1 median = 300; y2 median = 800; y3..20 median = 0
    // Sum of annual medians = 300 + 800 = 1100.
    // True median of correlated path sums = median(900, 1000, 1600) = 1000!
    const pvPaths = [
      Array.from({ length: 21 }, (_, y) => y === 0 ? -10000 : (y === 1 ? 100 : (y === 2 ? 800 : 0))),
      Array.from({ length: 21 }, (_, y) => y === 0 ? -10000 : (y === 1 ? 300 : (y === 2 ? 700 : 0))),
      Array.from({ length: 21 }, (_, y) => y === 0 ? -10000 : (y === 1 ? 600 : (y === 2 ? 1000 : 0)))
    ];
    const eqPaths = pvPaths;

    const cashRows = Array.from({ length: 21 }, (_, y) => ({
      year: y,
      pv_incremental: y === 0 ? -10000 : (y === 1 ? 300 : (y === 2 ? 800 : 0)),
      pv_incremental_range: {
        mean: pvPaths.reduce((sum, row) => sum + row[y], 0) / pvPaths.length,
        min: y === 0 ? -10000 : (y === 1 ? 100 : (y === 2 ? 800 : 0)),
        median: y === 0 ? -10000 : (y === 1 ? 300 : (y === 2 ? 800 : 0)),
        max: y === 0 ? -10000 : (y === 1 ? 600 : (y === 2 ? 1000 : 0))
      },
      whole_package: y === 0 ? -10000 : 0,
      whole_package_range: { min: 0, median: 0, max: 0 },
      equity: y === 0 ? -10000 : (y === 1 ? 300 : (y === 2 ? 800 : 0)),
      equity_range: {
        mean: eqPaths.reduce((sum, row) => sum + row[y], 0) / eqPaths.length,
        min: y === 0 ? -10000 : (y === 1 ? 100 : (y === 2 ? 800 : 0)),
        median: y === 0 ? -10000 : (y === 1 ? 300 : (y === 2 ? 800 : 0)),
        max: y === 0 ? -10000 : (y === 1 ? 600 : (y === 2 ? 1000 : 0))
      },
      debt_service: 0,
      loan_interest: 0,
      tax_deduction: 0,
      replacement: 0
    }));

    const mockResult = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'real', inflation: 0 },
      layout: { kwp: 6 },
      finance: {
        capex_eur: 10000,
        conversion_capex_eur: 0,
        cashflows_by_weather: {
          pv_incremental: pvPaths,
          whole_package: pvPaths,
          equity: eqPaths
        }
      },
      annual_cashflows: cashRows
    };

    context.renderSummaryCashTable(mockResult, cashRows);
    const html = elements.get('cashTable').innerHTML;

    // Sum of annual medians would be -10000 + 300 + 800 = -8,900, but true correlated path sum median is -10000 + 1000 = -9,000!
    assert.match(html, /Net cash \(Years 0–20, undiscounted\)/, 'Has Net cash totals row');
    assert.match(html, /Net Present Value \(Years 0–20, 0\.0% real\)/, 'Has Net Present Value totals row');
    const expectedMean = pvPaths.reduce((sum, row) => sum + row.reduce((a, b) => a + b, 0), 0) / pvPaths.length;
    const expectedDisplay = vm.runInContext(`fmt(${expectedMean}, 'eur')`, context);
    assert.ok(html.includes(`<span class="cash-val">${expectedDisplay}</span>`), 'Totals row shows the mean of complete weather cash paths');
    assert.doesNotMatch(html, /<span class="cash-val">−€9,000<\/span>/, 'Totals row does not falsely use path median (−€9,000)');
    console.log('✔ Test 1: Correlated per-weather totals (ensemble mean) passed');
  }

  // Test 2: Real deflation handling under nonzero inflation
  {
    const { context, elements } = createHarness();
    const inflation = 0.05; // 5% inflation
    // Year 1 nominal 105 -> real 100; Year 2 nominal 110.25 -> real 100
    const pvPaths = [
      Array.from({ length: 21 }, (_, y) => y === 0 ? -1000 : (y === 1 ? 105 : (y === 2 ? 110.25 : 0)))
    ];
    const cashRows = Array.from({ length: 21 }, (_, y) => ({
      year: y,
      pv_incremental: y === 0 ? -1000 : (y === 1 ? 105 : (y === 2 ? 110.25 : 0)),
      display_pv_incremental_range: {
        min: y === 0 ? -1000 : 100,
        median: y === 0 ? -1000 : 100,
        max: y === 0 ? -1000 : 100
      },
      whole_package: y === 0 ? -1000 : 0,
      whole_package_range: { min: 0, median: 0, max: 0 },
      equity: y === 0 ? -1000 : 0,
      equity_range: { min: 0, median: 0, max: 0 },
      debt_service: 0,
      loan_interest: 0,
      tax_deduction: 0,
      replacement: 0
    }));

    const mockResult = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'real', inflation },
      layout: { kwp: 6 },
      finance: {
        capex_eur: 1000,
        cashflows_by_weather: {
          pv_incremental: pvPaths,
          whole_package: pvPaths,
          equity: pvPaths
        }
      },
      annual_cashflows: cashRows
    };

    context.renderSummaryCashTable(mockResult, cashRows);
    const html = elements.get('cashTable').innerHTML;
    // Year 1 and 2 deflated totals: 105/1.05 = 100, 110.25/(1.05^2) = 100 -> Operating sum = 200
    // Year 0 capex is -1000 -> Total net cash is -1000 + 200 = -800
    assert.match(html, /<span class="cash-val">−€800<\/span>/, 'Real totals deflated properly with year 0 included (sum is −€800)');
    console.log('✔ Test 2: Real deflation handling under nonzero inflation passed');
  }

  // Test 3: Negative outflows formatting (−€ with unicode minus)
  {
    const { context, elements } = createHarness();
    const pvPaths = [Array.from({ length: 21 }, (_, y) => y === 0 ? -5000 : 500)];
    const cashRows = Array.from({ length: 21 }, (_, y) => ({
      year: y,
      pv_incremental: y === 0 ? -5000 : 500,
      pv_incremental_range: { min: 500, median: 500, max: 500 },
      whole_package: y === 0 ? -7000 : 600,
      whole_package_range: { min: 600, median: 600, max: 600 },
      equity: y === 0 ? -2000 : 300,
      equity_range: { min: 300, median: 300, max: 300 },
      debt_service: y === 1 ? 350 : 0,
      loan_interest: y === 1 ? 80 : 0,
      tax_deduction: 0,
      replacement: y === 10 ? 1500 : 0
    }));

    const mockResult = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'real', inflation: 0, inverter_replace_year: 10, inverter_replacement_cost: 1500, battery_kwh: 0 },
      layout: { kwp: 5 },
      finance: {
        capex_eur: 5000,
        conversion_capex_eur: 2000,
        cashflows_by_weather: {
          pv_incremental: pvPaths,
          whole_package: pvPaths,
          equity: pvPaths
        }
      },
      annual_cashflows: cashRows
    };

        context.summaryCash = "loan";
context.renderSummaryCashTable(mockResult, cashRows);
    const html = elements.get('cashTable').innerHTML;

    // Check unicode minus format −€
    assert.match(html, /−€5,000/, 'Year 0 capex has unicode −€');
    assert.match(html, /−€350/, 'Debt service outflow has unicode −€');
    assert.match(html, /−€80/, 'Loan interest outflow has unicode −€');
    assert.match(html, /−€1,500/, 'Replacement outflow has unicode −€');
    assert.match(html, /<tr class="has-replacement" title="Inverter replacement">/, 'Replacement row highlighted with cause tooltip');
    console.log('✔ Test 3: Negative outflows (−€) and replacement row tooltip passed');
  }

  // Test 4: Export callback uses latest rerender data while details closed
  {
    const { context, $, getDownloaded } = createHarness();
    const details = $('cashFlowDetails');
    const exportBtn = $('exportCashCsvBtn');

    // Initially details closed
    details.open = false;

    // Scenario 1: capex 5100, yearly 510 -> total 1-20 = 10200
    const paths1 = [Array.from({ length: 21 }, (_, y) => y === 0 ? -5100 : 510)];
    const res1 = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'nominal' },
      layout: { kwp: 5 },
      finance: { capex_eur: 5100, cashflows_by_weather: { pv_incremental: paths1, whole_package: paths1, equity: paths1 } },
      annual_cashflows: Array.from({ length: 21 }, (_, y) => ({
        year: y, pv_incremental: y === 0 ? -5100 : 510, pv_incremental_range: { min: 510, median: 510, max: 510 },
        whole_package: 0, whole_package_range: { min: 0, median: 0, max: 0 },
        equity: 0, equity_range: { min: 0, median: 0, max: 0 }
      }))
    };

    context.renderSummaryCashTable(res1, res1.annual_cashflows);
    assert.equal(details.open, false, 'Details closed initially');

    // Click export while closed
    exportBtn.dispatchEvent({ type: 'click', stopPropagation: () => {} });
    let dl = getDownloaded();
    assert.ok(dl && dl.clicked, 'Download triggered while closed');
    assert.equal(dl.filename, 'pv-lab-annual-cashflows.csv', 'Actual download filename matches signature');
    assert.equal(dl.blob.type, 'text/csv', 'Actual download MIME type matches text/csv');
    let text1 = await dl.blob.text();
    assert.ok(text1.includes('5100'), 'Scenario 1 data exported (5100 net cash)');
    assert.ok(text1.includes('Net cash (Years 0-20, undiscounted)'), 'Scenario 1 has undiscounted footer row');
    assert.ok(text1.includes('Net Present Value (Years 0-20'), 'Scenario 1 has NPV footer row');

    // Rerender with Scenario 2: capex 8200, yearly 2100 -> net cash = -8200 + 42000 = 33800
    const paths2 = [Array.from({ length: 21 }, (_, y) => y === 0 ? -8200 : 2100)];
    const res2 = {
      settings: { load_scenario: 'electrified', cashflow_mode: 'nominal' },
      layout: { kwp: 8 },
      finance: { capex_eur: 8200, cashflows_by_weather: { pv_incremental: paths2, whole_package: paths2, equity: paths2 } },
      annual_cashflows: Array.from({ length: 21 }, (_, y) => ({
        year: y, pv_incremental: y === 0 ? -8200 : 2100, pv_incremental_range: { min: 2100, median: 2100, max: 2100 },
        whole_package: 0, whole_package_range: { min: 0, median: 0, max: 0 },
        equity: 0, equity_range: { min: 0, median: 0, max: 0 }
      }))
    };

    context.renderSummaryCashTable(res2, res2.annual_cashflows);
    assert.equal(details.open, false, 'Details remains closed on scenario change');

    // Export again without re-binding: MUST export Scenario 2 data
    exportBtn.dispatchEvent({ type: 'click', stopPropagation: () => {} });
    dl = getDownloaded();
    let text2 = await dl.blob.text();
    assert.ok(text2.includes('33800'), 'Export uses latest rerender data (33800), not stale Scenario 1 data');
    assert.doesNotMatch(text2, /5100/, 'Stale Scenario 1 data is not present');
    console.log('✔ Test 4: Export callback uses latest rerender data while details closed passed');
  }

  // Test 5: Details close initial, reset on changed settings/quote, preserve on same rerender
  {
    const { context, $, sessionStorage } = createHarness();
    const details = $('cashFlowDetails');
    const summary = $('cashFlowSummary');

    const resA = {
      settings: { load_scenario: 'scenarioA', cashflow_mode: 'real', kwp: 6 },
      finance: { cashflows_by_weather: {} },
      annual_cashflows: [{ year: 0, pv_incremental: -5000 }]
    };

    // 1. Initial render -> closed
    context.renderSummaryCashTable(resA, resA.annual_cashflows);
    assert.equal(details.open, false, 'Initially closed');
    assert.equal(summary.getAttribute('aria-expanded'), 'false');

    // 2. User toggles open
    details.open = true;
    details.dispatchEvent({ type: 'toggle' });
    assert.equal(sessionStorage.getItem('summary_cash_disclosure_open'), '1');
    assert.equal(summary.getAttribute('aria-expanded'), 'true');

    // 3. Same scenario rerender (e.g. view toggle or live refresh) -> MUST PRESERVE open state
    context.renderSummaryCashTable(resA, resA.annual_cashflows);
    assert.equal(details.open, true, 'Preserves open state on same scenario rerender');
    assert.equal(summary.getAttribute('aria-expanded'), 'true');

    // 4. Changed settings/quote -> MUST RESET to closed
    const resB = {
      settings: { load_scenario: 'scenarioB', cashflow_mode: 'real', kwp: 8 },
      finance: { cashflows_by_weather: {} },
      annual_cashflows: [{ year: 0, pv_incremental: -8000 }]
    };
    context.renderSummaryCashTable(resB, resB.annual_cashflows);
    assert.equal(details.open, false, 'Resets to closed on changed scenario settings');
    assert.equal(sessionStorage.getItem('summary_cash_disclosure_open'), null, 'sessionStorage cleared on scenario change');
    assert.equal(summary.getAttribute('aria-expanded'), 'false');
    details.open=true;
    details.dispatchEvent({type:'toggle'});
    context.quote={line_items:[{net_eur:1234}]};
    context.renderSummaryCashTable(resB,resB.annual_cashflows);
    assert.equal(details.open,false,'accepted quote change resets disclosure');
    console.log('✔ Test 5: Details close initial and on changed settings preserving same rerender passed');
  }

  // Test 6: Median / Range state attributes
  {
    const { context, $ } = createHarness();
    const res = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'nominal' },
      finance: { cashflows_by_weather: {} },
      annual_cashflows: [{ year: 0, pv_incremental: -5000 }]
    };
    context.renderSummaryCashTable(res, res.annual_cashflows);

    const medianBtn = $('cashTableMeanBtn');
    const rangeBtn = $('cashTableRangeBtn');
    const ctWrap = $('cashTable');
    const tableElem = $('cashTableElement');

    // Click Range
    rangeBtn.dispatchEvent({ type: 'click' });
    assert.equal(ctWrap.getAttribute('data-mode'), 'range', 'Container receives data-mode="range"');
    assert.equal(tableElem.getAttribute('data-mode'), 'range', 'Table receives data-mode="range"');
    assert.ok(rangeBtn.classList.contains('active'), 'Range button active');
    assert.ok(!medianBtn.classList.contains('active'), 'Median button inactive');

    // Click Median
    medianBtn.dispatchEvent({ type: 'click' });
    assert.equal(ctWrap.getAttribute('data-mode'), 'mean', 'Container receives data-mode="mean"');
    assert.equal(tableElem.getAttribute('data-mode'), 'mean', 'Table receives data-mode="mean"');
    assert.ok(medianBtn.classList.contains('active'), 'Median button active');
    assert.ok(!rangeBtn.classList.contains('active'), 'Range button inactive');
    console.log('✔ Test 6: Median/Range state attributes passed');
  }

  // Test 7: Print lifecycle open / restore
  {
    const { $, windowMock } = createHarness();
    const details = $('cashFlowDetails');

    // Case A: Closed before print
    details.open = false;
    windowMock.dispatchEvent({ type: 'beforeprint' });
    assert.equal(details.open, true, 'beforeprint forces open = true');
    windowMock.dispatchEvent({ type: 'afterprint' });
    assert.equal(details.open, false, 'afterprint restores closed state');

    // Case B: Open before print
    details.open = true;
    windowMock.dispatchEvent({ type: 'beforeprint' });
    assert.equal(details.open, true, 'beforeprint keeps open = true');
    windowMock.dispatchEvent({ type: 'afterprint' });
    assert.equal(details.open, true, 'afterprint restores open state');
    console.log('✔ Test 7: Print open/restore lifecycle passed');
  }

  // Test 8: Nonzero discount rate discounting and nominal vs real rate labels
  {
    const { context, elements } = createHarness();
    const realDiscountRate = 0.03; // 3%
    const inflation = 0.02; // 2%
    const pvPaths = [
      Array.from({ length: 21 }, (_, y) => y === 0 ? -1000 : (y === 1 ? 510 : (y === 2 ? 520.2 : 0)))
    ];
    const cashRows = Array.from({ length: 21 }, (_, y) => ({
      year: y,
      pv_incremental: y === 0 ? -1000 : (y === 1 ? 510 : (y === 2 ? 520.2 : 0)),
      display_pv_incremental_range: { min: y === 0 ? -1000 : 500, median: y === 0 ? -1000 : 500, max: y === 0 ? -1000 : 500 },
      whole_package: y === 0 ? -1000 : 0,
      whole_package_range: { min: 0, median: 0, max: 0 },
      equity: y === 0 ? -1000 : (y === 1 ? 510 : (y === 2 ? 520.2 : 0)),
      equity_range: { min: 0, median: 0, max: 0 },
      debt_service: 0, loan_interest: 0, tax_deduction: 0, replacement: 0
    }));

    // Nominal mode: nominal discount rate = (1 + 0.03)*(1 + 0.02) - 1 = 0.0506 (5.1%)
    const resNom = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'nominal', real_discount_rate: realDiscountRate, inflation },
      layout: { kwp: 5 },
      finance: {
        capex_eur: 1000,
        real_discount_rate: realDiscountRate,
        cashflows_by_weather: { pv_incremental: pvPaths, whole_package: pvPaths, equity: pvPaths }
      },
      annual_cashflows: cashRows
    };

    context.renderSummaryCashTable(resNom, cashRows);
    let html = elements.get('cashTable').innerHTML;
    assert.match(html, /5\.1% nominal/, 'Nominal rate label matches Fisher equation (5.1% nominal)');

    // Real mode: real discount rate = 3.0%
    const resReal = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'real', real_discount_rate: realDiscountRate, inflation },
      layout: { kwp: 5 },
      finance: {
        capex_eur: 1000,
        real_discount_rate: realDiscountRate,
        cashflows_by_weather: { pv_incremental: pvPaths, whole_package: pvPaths, equity: pvPaths }
      },
      annual_cashflows: cashRows
    };

    context.renderSummaryCashTable(resReal, cashRows);
    html = elements.get('cashTable').innerHTML;
    assert.match(html, /3\.0% real/, 'Real rate label matches 3.0% real');
    console.log('✔ Test 8: Nonzero rate and real/nominal mode discounting passed');
  }

  // Test 9: Scope and cash modes with canonical paths and equity/cumEquity agreement
  {
    const { context, elements } = createHarness();
    const pvPaths = [Array.from({ length: 21 }, (_, y) => y === 0 ? -10000 : 1000)];
    const pkgPaths = [Array.from({ length: 21 }, (_, y) => y === 0 ? -14000 : 1500)];
    const solarEqPaths = [Array.from({ length: 21 }, (_, y) => y === 0 ? -4000 : 600)];
    const pkgEqPaths = [Array.from({ length: 21 }, (_, y) => y === 0 ? -6000 : 800)];

    const cashRows = Array.from({ length: 21 }, (_, y) => ({
      year: y,
      pv_incremental: y === 0 ? -10000 : 1000,
      whole_package: y === 0 ? -14000 : 1500,
      solar_equity: y === 0 ? -4000 : 600,
      package_equity: y === 0 ? -6000 : 800,
      tax_deduction: 0,
      replacement: 0,
      debt_service: y > 0 && y <= 10 ? 400 : 0,
      loan_interest: y > 0 && y <= 10 ? 100 : 0
    }));

    const mockRes = {
      settings: { load_scenario: 'baseline', cashflow_mode: 'real', inflation: 0, horizon_years: 20 },
      finance: {
        capex_eur: 10000,
        conversion_capex_eur: 4000,
        loan_principal_eur: 8000,
        solar_loan_principal_eur: 6000,
        solar_loan_ratio: 0.75,
        cashflows_by_weather: {
          pv_incremental: pvPaths,
          whole_package: pkgPaths,
          solar_equity: solarEqPaths,
          package_equity: pkgEqPaths
        }
      },
      annual_cashflows: cashRows
    };

    // Mode A: Solar + Cash
    context.summaryScope = 'solar';
    context.summaryCash = 'cash';
    context.renderSummaryCashTable(mockRes, cashRows);
    let html = elements.get('cashTable').innerHTML;
    assert.match(html, /PROJECT CF \+ LOAN CF/, 'Solar scope subtitle formula');
    const dataA = context.buildSummaryCashRows(mockRes, cashRows);
    assert.equal(dataA.flowKey, 'pv_incremental');
    assert.equal(dataA.rows[1].debtService, 0, 'Cash mode has 0 debt service');
    assert.equal(dataA.rows[1].loanInterest, 0, 'Cash mode has 0 loan interest');
    assert.equal(dataA.netCashTotals.debtService, 0);
    assert.equal(dataA.netCashTotals.equity.median, 10000);
    assert.equal(dataA.netCashTotals.cumEquity.median, 10000, 'Row 1 Equity and CumEquity agree identically');
    assert.equal(dataA.npvTotals.equity.median, dataA.npvTotals.cumEquity.median, 'Row 2 Equity and CumEquity agree identically');

    // Mode B: Solar + Loan
    context.summaryScope = 'solar';
    context.summaryCash = 'loan';
    context.renderSummaryCashTable(mockRes, cashRows);
    const dataB = context.buildSummaryCashRows(mockRes, cashRows);
    assert.equal(dataB.flowKey, 'solar_equity');
    assert.equal(dataB.rows[0].debtService, -6000, 'Solar loan Year 0 drawdown is -6000');
    assert.equal(dataB.netCashTotals.debtService, -3000, 'Net debt service reflects 3000 repayments - 6000 drawdown = -3000');
    assert.equal(dataB.rows[1].debtService, 300, 'Solar loan scales debt service by 0.75 (400 * 0.75 = 300)');
    assert.equal(dataB.rows[1].loanInterest, 75, 'Solar loan scales interest by 0.75 (100 * 0.75 = 75)');
    assert.equal(dataB.netCashTotals.equity.median, 8000);
    assert.equal(dataB.netCashTotals.cumEquity.median, 8000, 'Row 1 Equity and CumEquity agree identically');
    assert.equal(dataB.npvTotals.equity.median, dataB.npvTotals.cumEquity.median, 'Row 2 Equity and CumEquity agree identically');

    // Mode C: Package + Loan
    context.summaryScope = 'package';
    context.summaryCash = 'loan';
    context.renderSummaryCashTable(mockRes, cashRows);
    html = elements.get('cashTable').innerHTML;
    assert.match(html, /WHOLE-HOUSE CF \+ LOAN CF/, 'Package scope subtitle formula');
    const dataC = context.buildSummaryCashRows(mockRes, cashRows);
    assert.equal(dataC.flowKey, 'package_equity');
    assert.equal(dataC.rows[0].debtService, -8000, 'Package loan Year 0 drawdown is -8000');
    assert.equal(dataC.netCashTotals.debtService, -4000, 'Net debt service reflects 4000 repayments - 8000 drawdown = -4000');
    assert.equal(dataC.rows[1].debtService, 400, 'Package loan has full debt service 400');
    assert.equal(dataC.netCashTotals.equity.median, 10000);
    assert.equal(dataC.netCashTotals.cumEquity.median, 10000, 'Row 1 Equity and CumEquity agree identically');
    assert.equal(dataC.npvTotals.equity.median, dataC.npvTotals.cumEquity.median, 'Row 2 Equity and CumEquity agree identically');

    console.log('✔ Test 9: Scope and cash modes with canonical paths passed');
  }

  // Test 10: Loan drawdown at Year 0, horizontal reconciliation, net financing NPV, and genuine zero in getY0
  {
    const { context, elements } = createHarness();

    // 1. Verify getY0 genuine zero preservation
    const fnBuild = context.buildSummaryCashRows;
    const zeroPathResult = fnBuild({
      settings: { load_scenario: 'baseline', cashflow_mode: 'real', inflation: 0, horizon_years: 2 },
      finance: {
        capex_eur: 5000,
        cashflows_by_weather: {
          pv_incremental: [[0, 200, 300]],
          whole_package: [[0, 200, 300]],
          equity: [[0, 200, 300]]
        }
      }
    }, [
      { year: 0, pv_incremental: -5000 },
      { year: 1, pv_incremental: 200 },
      { year: 2, pv_incremental: 300 }
    ]);
    assert.equal(zeroPathResult.netCashTotals.project.median, 500, 'getY0 honors genuine zero row[0] without fallback substitution');

    // 2. Full horizontal math reconciliation test matching browser feedback
    // Whole house net 21643 minus net debt service 2347 = equity 19296 (~19295)
    const realDiscountRate = 0.03;
    const pkgPaths = [
      Array.from({ length: 21 }, (_, y) => y === 0 ? -15000 : 1832.15)
    ];
    const equityPaths = [
      Array.from({ length: 21 }, (_, y) => {
        if (y === 0) return 0;
        const wh = 1832.15;
        const ds = y <= 10 ? 1734.7 : 0;
        return wh - ds;
      })
    ];

    const cashRows = Array.from({ length: 21 }, (_, y) => ({
      year: y,
      whole_package: y === 0 ? -15000 : 1832.15,
      debt_service: y === 0 ? 0 : (y <= 10 ? 1734.7 : 0),
      loan_interest: y === 0 ? 0 : (y <= 10 ? 234.7 : 0),
      tax_deduction: 0,
      replacement: 0,
      pv_incremental: y === 0 ? -15000 : 1000,
      package_equity: y === 0 ? 0 : (1832.15 - (y <= 10 ? 1734.7 : 0))
    }));

    const mockBrowser = {
      settings: { load_scenario: 'electrified', cashflow_mode: 'real', inflation: 0, horizon_years: 20 },
      finance: {
        capex_eur: 10000,
        conversion_capex_eur: 5000,
        loan_principal_eur: 15000,
        real_discount_rate: realDiscountRate,
        cashflows_by_weather: {
          whole_package: pkgPaths,
          package_equity: equityPaths,
          pv_incremental: pkgPaths
        }
      },
      annual_cashflows: cashRows
    };

    context.summaryScope = 'package';
    context.summaryCash = 'loan';
    context.renderSummaryCashTable(mockBrowser, cashRows);
    const html = elements.get('cashTable').innerHTML;

    // Header check
    assert.match(html, /Loan repayment<br>\/ drawdown/, 'Header contains Loan repayment / drawdown');

    // Year 0 financing drawdown inflow check (+€15,000)
    assert.match(html, /\+€15,000/, 'Year 0 financing displays positive inflow drawdown +€15,000');

    // Footer row 1: Net cash horizontal arithmetic
    const data = context.buildSummaryCashRows(mockBrowser, cashRows);
    assert.equal(data.rows[0].debtService, -15000, 'Year 0 debt service records negative drawdown -15000');
    assert.equal(Math.round(data.rows[0].equity), 0, 'Year 0 equity is 0 (-15000 capex + 15000 loan)');
    assert.equal(Math.round(data.netCashTotals.wholeHouse.median), 21643, 'Whole house net cash is 21643');
    assert.equal(Math.round(data.netCashTotals.debtService), 2347, 'Net debt service is 2347 (17347 repayments - 15000 drawdown)');
    assert.equal(Math.round(data.netCashTotals.equity.median), 19296, 'Equity net cash matches wholehouse (21643) - debt (2347) = 19296 (~19295)');
    assert.match(html, /−€2,347/, 'Footer displays net loan outflow −€2,347');

    // Net financing NPV check when net debt NPV is negative (+€... inflow)
    const mockNpvInflow = {
      ...mockBrowser,
      finance: {
        ...mockBrowser.finance,
        real_discount_rate: 0.05
      }
    };
    const dataNpv = context.buildSummaryCashRows(mockNpvInflow, cashRows);
    assert.ok(dataNpv.npvTotals.debtService < 0, 'Net debt NPV is negative (drawdown exceeds discounted repayments)');
    context.renderSummaryCashTable(mockNpvInflow, cashRows);
    const htmlNpv = elements.get('cashTable').innerHTML;
    assert.match(htmlNpv, /\+€[0-9,]+/, 'Negative net debt service NPV formats as positive inflow (+€...)');

    console.log('✔ Test 10: Loan drawdown at Year 0, horizontal reconciliation, net financing NPV, and genuine zero in getY0 passed');
  }

  console.log('\nALL 10 SUMMARY CASH TABLE UNIT TESTS PASSED SUCCESSFULLY!');
}

runAllTests().catch(err => {
  console.error(err);
  process.exit(1);
});
