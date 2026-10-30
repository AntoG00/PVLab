'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');

const elements = {
  loanCurveDiscounted: { classList: { toggle: () => {} }, setAttribute: () => {} },
  loanCurveUndiscounted: { classList: { toggle: () => {} }, setAttribute: () => {} }
};

const context = vm.createContext({
  VALID_CURVE_BASES: new Set(['discounted', 'undiscounted']),
  _initialCurvePrefs: { summary: 'discounted', loan: 'discounted' },
  persistCashCurvePrefs: () => {},
  summaryCurveBasis: 'discounted',
  $: id => elements[id],
  current: null,
  renderCashChart: () => {}
});

// Extract loan calculations and basis toggle ranges
const start1 = appSource.indexOf('function computeImpliedTaeg(');
const end1 = appSource.indexOf('function syncLoanControls(', start1);
vm.runInContext(appSource.slice(start1, end1), context);

const start2 = appSource.indexOf('let loanCurveBasis=');
const end2 = appSource.indexOf('function cashFlowView(', start2);
vm.runInContext(appSource.slice(start2, end2), context);

// 1. TAEG Contract tests
const computeTaeg = context.computeImpliedTaeg;
const taeg = computeTaeg(14999.72, 159.9999763880213, 120);
assert.ok(Math.abs(taeg - 0.052452094) < 1e-6, 'TAEG contract matches standard amortized loan rate');
assert.equal(computeTaeg(0, 160, 120), null, 'Zero loan principal yields null TAEG');
const zeroTanFeeTaeg = computeTaeg(9700, 10000 / 120, 120);
assert.ok(zeroTanFeeTaeg > 0 && zeroTanFeeTaeg < 0.02, 'Zero TAN with upfront fees yields positive implied TAEG');

// 2. Exact ledger numbers independent assertions (coverage net/bills/tax/other/DSCR + horizon)
const coverageResult = {
  settings: { load_scenario: 'electrified', loan_years: 10, horizon_years: 5 },
  finance: { loan_principal_eur: 12000, conversion_capex_eur: 6000, solar_loan_principal_eur: 12000 },
  annual_cashflows: [
    null,
    { whole_package: 2500, baseline_package_bill: 3000, with_system_electric_bill: 1200, remaining_gas_bill: 200, tax_deduction: 400, debt_service: 1200 },
    { whole_package: 2500, baseline_package_bill: 3000, with_system_electric_bill: 1200, remaining_gas_bill: 200, tax_deduction: 400, debt_service: 1200 },
    { whole_package: 2500, baseline_package_bill: 3000, with_system_electric_bill: 1200, remaining_gas_bill: 200, tax_deduction: 400, debt_service: 1200 },
    { whole_package: 2500, baseline_package_bill: 3000, with_system_electric_bill: 1200, remaining_gas_bill: 200, tax_deduction: 400, debt_service: 1200 },
    { whole_package: 2500, baseline_package_bill: 3000, with_system_electric_bill: 1200, remaining_gas_bill: 200, tax_deduction: 400, debt_service: 1200 }
  ]
};
const cov = context.computeLoanTermCashCoverage(coverageResult);
assert.equal(cov.N, 5, 'Truncated horizon limits term N to min(loan_years, horizon_years)');
assert.equal(cov.horizon, 5, 'Reported horizon reflects truncated setting');
assert.ok(Math.abs(cov.monthlyOperatingInflow - (2500 / 12)) < 1e-9, 'Operating inflow exact monthly match');
assert.ok(Math.abs(cov.monthlyBillSavings - (1600 / 12)) < 1e-9, 'Bill savings exact monthly match');
assert.ok(Math.abs(cov.monthlyBonusCasa - (400 / 12)) < 1e-9, 'Bonus casa exact monthly match');
assert.ok(Math.abs(cov.monthlyOther - (500 / 12)) < 1e-9, 'Other residual exact monthly match');
assert.ok(Math.abs(cov.monthlyDebtService - (1200 / 12)) < 1e-9, 'Debt service exact monthly match');
assert.ok(Math.abs(cov.monthlyNetCash - (1300 / 12)) < 1e-9, 'Net cash exact monthly match');
assert.ok(Math.abs(cov.dscr - (2500 / 1200)) < 1e-9, 'DSCR matches operating inflow / debt service');

// 3. getSavedRecord fees require loan_principal_eur>0 in fixtures, legacy0/metadata0 precedence
const getFees = context.getSavedRecordFinancedFees;
assert.equal(getFees({ result: { finance: { loan_principal_eur: 10000, loan_upfront_fees_or_tax_eur: 45 } }, settings: { loan_upfront_fees_or_tax: 20 } }), 45, 'Finance upfront fees take precedence');
assert.equal(getFees({ result: { finance: { loan_principal_eur: 10000 } }, settings: { loan_upfront_fees_or_tax: 20 } }), 20, 'Settings fallback when finance record has no fee');
assert.equal(getFees({ result: { finance: { loan_principal_eur: 10000, loan_upfront_fees_or_tax_eur: 0 } }, settings: { loan_upfront_fees_or_tax: 20 } }), 0, 'Explicit 0 in finance metadata takes precedence');
assert.equal(getFees({ result: { finance: { loan_principal_eur: 10000 } }, settings: {} }), 0, 'Legacy snapshot without fee setting defaults to 0');
assert.equal(getFees({ result: { finance: { loan_principal_eur: 0, loan_upfront_fees_or_tax_eur: 50 } }, settings: { loan_upfront_fees_or_tax: 50 } }), 0, 'Zero loan principal forces 0 fee');

// 4. loanCurveBasis default discounted and toggles isolated summaryCurveBasis
assert.equal(vm.runInContext('loanCurveBasis', context), 'discounted', 'Default basis is discounted');
context.setLoanCurveBasis('undiscounted');
assert.equal(vm.runInContext('loanCurveBasis', context), 'undiscounted', 'Toggles to undiscounted');
assert.equal(context.summaryCurveBasis, 'discounted', 'Summary curve basis remains isolated');
context.setLoanCurveBasis('invalid_mode');

console.log('All loan behavior UI tests passed.');
