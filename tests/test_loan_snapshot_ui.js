'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../feasibility/web/app.js'),'utf8');
const start=source.indexOf('function inputFingerprint('),end=source.indexOf('function renderLoanPivot(');
const elements={loanSolveResult:{textContent:'stale solver output'},loanSolveStatus:{textContent:'Best recovery found across 19 weather paths.'},solveLoanAmount:{disabled:false}};
const context=vm.createContext({structuredClone:global.structuredClone,$:id=>elements[id],document:{getElementById:id=>elements[id]}});
vm.runInContext(source.slice(start,source.indexOf('async function recompute(')),context);
const run=code=>vm.runInContext(code,context);
run(`settings={quote_total:null,battery_kwh:4,loan_amount:500};quote={line_items:[{net_eur:100}],battery_nameplate_kwh:10,battery_dod:.4};current={result:{settings:{quote_total:110,battery_kwh:4,loan_amount:500}}};loanPending=false;acceptedInputFingerprint=inputFingerprint();`);
assert.equal(run('acceptedLoanInputsCurrent()'),true,'accepted input and quote snapshots remain valid when evaluation normalizes quote totals and battery settings');
run(`loanPending=true`);
assert.equal(run('acceptedLoanInputsCurrent()'),false,'pending recalculation cannot be saved');
run(`loanPending=false;settings.loan_amount=600`);
assert.equal(run('acceptedLoanInputsCurrent()'),false,'edited inputs cannot reuse the prior accepted result');
run(`acceptedInputFingerprint=inputFingerprint();quote.line_items[0].net_eur=200`);
assert.equal(run('acceptedLoanInputsCurrent()'),false,'edited quote cannot reuse the prior accepted result');
run(`loanSolveToken=7;loanSolveResult={status:'OPTIMAL'};invalidateLoanSolve()`);
assert.equal(run('loanSolveToken'),8,'input changes invalidate any in-flight solver response');
assert.equal(run('loanSolveResult'),null,'input changes clear the prior optimal amount');
assert.equal(elements.loanSolveResult.textContent,'','a stale result is removed from the Loan panel');
assert.equal(elements.loanSolveStatus.textContent,'','a stale solver status is removed when inputs change');
console.log('Loan snapshots require the exact accepted settings and quote while tolerating normalized result settings.');
const validationContext=vm.createContext({document:{querySelectorAll:()=>[]}});
vm.runInContext(source.slice(source.indexOf('function loanControlsValid()'),source.indexOf('function currentLoanMetrics(')),validationContext);
const budget={id:'loanMaxMonthlyPaymentControl',value:'',validity:{valid:true},closest:()=>({hidden:false})};
validationContext.document.querySelectorAll=()=>[budget];
assert.equal(vm.runInContext('loanControlsValid()',validationContext),true,'an empty optional monthly budget remains valid');
budget.value='2.5';
assert.equal(vm.runInContext('loanControlsValid()',validationContext),true,'a valid monthly budget remains valid');
budget.value='100001';budget.validity.valid=false;
assert.equal(vm.runInContext('loanControlsValid()',validationContext),false,'a monthly budget outside the control bounds is rejected');
console.log('The optional monthly payment control accepts blank input and validates entered values.');

const restoreContext=vm.createContext({
 schema:{defaults:{loan_amount:14999.72,loan_apr:0.051,loan_upfront_fees_or_tax:16}},
 settings:{loan_amount:1000,loan_apr:0.05,loan_upfront_fees_or_tax:16,loan_mode:'amount'},
 current:null,
 $:id=>id==='loanFeesControl'?feesEl:null,
 document:{activeElement:null,querySelector:()=>null},
 getLoanScopeContextText:()=>''
});
const feesEl={value:'0.00'};
restoreContext.document.activeElement=feesEl;
vm.runInContext(source.slice(source.indexOf('function getSavedRecordFinancedFees('),source.indexOf('function loanControlsValid(')),restoreContext);

assert.equal(restoreContext.getSavedRecordFinancedFees({result:{finance:{loan_principal_eur:1000,loan_upfront_fees_or_tax_eur:16}}}),16,'retrieves financed fee from result finance when settings key missing');
assert.equal(restoreContext.getSavedRecordFinancedFees({result:{finance:{loan_principal_eur:1000}},settings:{loan_upfront_fees_or_tax:16}}),16,'retrieves fee from settings when explicitly present');
assert.equal(restoreContext.getSavedRecordFinancedFees({result:{finance:{loan_principal_eur:0,loan_upfront_fees_or_tax_eur:16}}}),0,'zero principal returns zero fees');
assert.equal(restoreContext.getSavedRecordFinancedFees({result:{finance:{loan_principal_eur:1000}},settings:{loan_upfront_fees_or_tax:0}}),0,'explicit zero fee is preserved');

restoreContext.syncLoanControls();
assert.equal(feesEl.value,'0.00','routine syncLoanControls does not overwrite active input while editing');

restoreContext.syncLoanControls(true);
assert.equal(feesEl.value,'16.00','forced sync on snapshot load overrides active element and immediately restores snapshot value');
console.log('Loan snapshot restoration correctly recovers fees and forcibly updates UI controls.');

// --- Regression: currentLoanMetrics arithmetic mean & explicit mean-CF persistent discounted payback ---
{
 const domElements = {};
 const persistent$ = id => domElements[id] || (domElements[id] = { textContent: "", innerHTML: "", onclick: null, closest: () => null });
 const testMetricsContext = vm.createContext({
  getDiscountingCashflows: r => r.finance?.cashflows_by_weather,
  computeLoanTermCashCoverage: r => ({ monthlyNetCash: 120, N: 10 }),
  getSavedRecordFinancedFees: c => (c.result?.finance?.loan_upfront_fees_or_tax_eur ?? c.settings?.loan_upfront_fees_or_tax ?? 0),
  computeImpliedTaeg: () => null,
  fmt: (v, t) => t === "years" ? (v == null ? "—" : `${v} y`) : t === "eur" ? `€${Math.round(v)}` : t === "eur2" ? `€${Number(v).toFixed(2)}` : String(v),
  nf: (v, d) => d != null ? Number(v).toFixed(d) : String(v),
  esc: s => String(s),
  Math, Number, Array, Object, String, isFinite,
  $: persistent$,
  document: { getElementById: id => persistent$(id), querySelector: () => null },
  loanSaved: [],
  current: null
 });

 const metricsStart = source.indexOf("function currentLoanMetrics(");
 const metricsEnd = source.indexOf("\nfunction ", metricsStart + 20);
 vm.runInContext(source.slice(metricsStart, metricsEnd), testMetricsContext);

 const pivotStart = source.indexOf("function renderLoanPivot(");
 const pivotEnd = source.indexOf("\nfunction ", pivotStart + 20);
 vm.runInContext(source.slice(pivotStart, pivotEnd), testMetricsContext);

 // 1. Asymmetric weather paths: mean vs median divergence and exact reference year20 mean
 const realRate = 0.03;
 const paths = [
  [-6000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000],
  [-7000, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800, 800],
  [-9000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]
 ];
 const asymmetricResult = {
  settings: { cashflow_mode: "real", inflation: 0.02, real_discount_rate: realRate, horizon_years: 20 },
  finance: {
   real_discount_rate: realRate,
   cashflows_by_weather: { equity: paths },
   equity: {
    discounted_payback: { median: 2.6 },
    discounted_payback_of_mean_cash_flows: 2.7
   }
  }
 };

 const computed = testMetricsContext.currentLoanMetrics(asymmetricResult);
 assert.equal(computed.stat, "mean", "coherent statistic naming specifies mean");
 assert.equal(computed.recovery, 2.7, "recovery adheres to explicit mean-CF persistent discounted payback, not median");
 assert.notEqual(computed.recovery, asymmetricResult.finance.equity.discounted_payback.median, "recovery diverges from median 2.6");
 assert.equal(computed.initial, (6000 + 7000 + 9000) / 3, "initial outlay uses arithmetic mean across weather paths");

 // Reference discounted cumulative calculation for year 20
 const discCumYear20 = paths.map(path => {
  let total = 0;
  for (let y = 0; y <= 20; y++) {
   total += path[y] / Math.pow(1.02, y) / Math.pow(1 + realRate, y);
  }
  return total;
 });
 const refYear20Mean = discCumYear20.reduce((s, v) => s + v, 0) / discCumYear20.length;
 assert(Math.abs(computed.year20 - refYear20Mean) < 1e-9, "year20 matches reference arithmetic mean of discounted paths");
 const medYear20 = [...discCumYear20].sort((a, b) => a - b)[1];
 assert(Math.abs(computed.year20 - medYear20) > 10, "year20 arithmetic mean diverges from median");

 // 2. Explicit null recovery preservation
 asymmetricResult.finance.equity.discounted_payback_of_mean_cash_flows = null;
 const unrecovered = testMetricsContext.currentLoanMetrics(asymmetricResult);
 assert.strictEqual(unrecovered.recovery, null, "explicit null mean-CF recovery is preserved despite finite weather median");

 // 3. Horizon < 20 semantics (null if short)
 const shortResult = {
  settings: { cashflow_mode: "real", inflation: 0.02, real_discount_rate: realRate, horizon_years: 15 },
  finance: {
   real_discount_rate: realRate,
   cashflows_by_weather: { equity: [[-5000, 500, 500], [-5000, 500, 500]] },
   equity: { discounted_payback_of_mean_cash_flows: 4.2 }
  }
 };
 const shortMetrics = testMetricsContext.currentLoanMetrics(shortResult);
 assert.strictEqual(shortMetrics.year20, null, "horizon < 20 leaves year20 null");

 // 4. Fallback persistent payback: zero-start & later-negative cases
 const zeroStartResult = {
  settings: { cashflow_mode: "real", real_discount_rate: 0 },
  finance: {
   cashflows_by_weather: {
    equity: [[0, 100, 100], [0, 200, 200]]
   }
  }
 };
 const zeroStartMetrics = testMetricsContext.currentLoanMetrics(zeroStartResult);
 assert.equal(zeroStartMetrics.recovery, 0, "fallback persistent payback returns 0 for non-negative start");
 const zeroThenDip={...zeroStartResult,finance:{cashflows_by_weather:{equity:[[0,-100,200,-150,100]]}}};
 assert.equal(testMetricsContext.currentLoanMetrics(zeroThenDip).recovery,3.5,"zero initial outlay does not bypass later negative balances");

 const laterNegativeResult = {
  settings: { cashflow_mode: "real", real_discount_rate: 0 },
  finance: {
   cashflows_by_weather: {
    // Year 0: -100, Year 1: +150 (cum +50), Year 2: -100 (cum -50), Year 3: +100 (cum +50)
    equity: [
     [-100, 150, -100, 100],
     [-100, 150, -100, 100]
    ]
   }
  }
 };
 const laterNegMetrics = testMetricsContext.currentLoanMetrics(laterNegativeResult);
 // At year 1, cum=+50 but year 2 dips to -50; persistently non-negative starting year 3, crossing between yr 2 (-50) and yr 3 (+50) at 2.5
 assert.equal(laterNegMetrics.recovery, 2.5, "fallback payback requires persistence through remaining horizon");

 const unrecoveredDip = {
  settings: { cashflow_mode: "real", real_discount_rate: 0 },
  finance: {
   cashflows_by_weather: {
    // Crosses positive then dips negative at the end of the horizon
    equity: [[-100, 150, -100], [-100, 150, -100]]
   }
  }
 };
 assert.strictEqual(testMetricsContext.currentLoanMetrics(unrecoveredDip).recovery, null, "dip at horizon end prevents recovery");

 // 5. Snapshot handling & comparator guard in renderLoanPivot
 testMetricsContext.current = { result: asymmetricResult };
 testMetricsContext.loanSaved = [
  { name: "Legacy Scalar", metrics: { initial: 6000, year20: 13624, recovery: 2.6, horizon: 20, stat: "median" } },
  { name: "Full Snapshot", result: asymmetricResult, metrics: { initial: 6000, year20: 13624, recovery: 2.6, horizon: 20, stat: "median" } }
 ];
 testMetricsContext.renderLoanPivot();
 const pivotHtml = persistent$("loanPivot").innerHTML;
 assert.match(pivotHtml, /Legacy Scalar/);
 assert.match(pivotHtml, /legacy median/, "scalar-only snapshot marked honestly with legacy median diagnostic");

 // Ensure comparator guard excludes legacy median so best-metric class is not mixed/awarded to legacy median
 const pivotTableMatch = pivotHtml.match(/<table class="loan-pivot">[\s\S]*?<\/table>/);
 assert(pivotTableMatch, "loan pivot table rendered");
 assert.doesNotMatch(pivotHtml, /<td class="best-metric">[^<]*legacy median/, "Legacy median cells cannot win mean-based rankings");
 console.log("All loan snapshot and currentLoanMetrics regression tests pass cleanly.");
}
