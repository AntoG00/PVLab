'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../feasibility/web/app.js'),'utf8');
const makeToggleBtn=()=>{const classes=new Set(),attrs={};return {classes,attrs,classList:{toggle(c,v){if(v)classes.add(c);else classes.delete(c);},contains(c){return classes.has(c);}},setAttribute(k,v){attrs[k]=String(v);},getAttribute(k){return attrs[k];}};};
const elements={
 cashFlowBasis:{value:'project'},cashFlowHeading:{textContent:''},cashFlowDescription:{textContent:''},cashFlowBasisNote:{textContent:''},
 cashInsights:{innerHTML:''},cashChart:{innerHTML:''},
 loanCashChart:{innerHTML:''},loanInsights:{innerHTML:''},loanCashArticle:{},loanPivot:{innerHTML:'',onclick:null},
 curveUndiscountedBtn:makeToggleBtn(),curveDiscountedBtn:makeToggleBtn(),loanCurveUndiscounted:makeToggleBtn(),loanCurveDiscounted:makeToggleBtn(),cashLegendLine:{textContent:''},cashLegendAnnual:{textContent:''}
};
const context=vm.createContext({localStorage:{getItem:()=>null},document:{getElementById:id=>elements[id]}});
vm.runInContext(source.slice(0,source.indexOf('function schedule(')),context);
vm.runInContext(source.slice(source.indexOf('function metricCard('),source.indexOf('function render(){')),context);
vm.runInContext('function chartTarget(detail){return `class=\"chart-point\" tabindex=\"0\" role=\"img\" aria-label=\"${esc(detail)}\" data-chart-detail=\"${esc(detail)}\"`}',context);
const run=code=>vm.runInContext(code,context);
const ranges=()=>({min:0,median:0,max:0});
const cashRows=Array.from({length:4},(_,year)=>({year,pv_incremental_range:ranges(),whole_package_range:ranges(),equity_range:ranges(),financed_project_range:ranges(),avoided_import:1000,net_export:200,cer:10,tax_deduction:100,maintenance:80,debt_service:500,display_debt_service:9999,loan_interest:100,display_loan_interest:8888,tax_deduction:100,display_tax_deduction:7777,replacement:year===2?900:0,display_replacement:6666,residual:year===3?300:0,display_residual:5555,baseline_package_bill:4000,with_system_electric_bill:2600,remaining_gas_bill:0}));
run(`current={result:{settings:{cashflow_mode:'real',inflation:.02,real_discount_rate:.04,load_scenario:'baseline'},finance:{real_discount_rate:.04,nominal_discount_rate:.0608,loan_principal_eur:0,loan_scope:'solar',cashflows_by_weather:{pv_incremental:[[-1000,600,600,-250],[-1000,1000,500,-800],[-1000,700,650,-600]],whole_package:[[-1500,900,900,-250],[-1500,1200,800,-800],[-1500,1000,950,-600]],equity:[[-1000,600,600,-250],[-1000,1000,500,-800],[-1000,700,650,-600]],financed_project:[[-1000,600,600,-250],[-1000,1000,500,-800],[-1000,700,650,-600]]}},annual_cashflows:${JSON.stringify(cashRows)}}};renderCashChart();`);
assert.equal(elements.cashFlowHeading.textContent,'Solar project cash · before financing');
assert.match(elements.cashChart.innerHTML,/Historical-weather ensemble mean annual net cash · years 1–3/);
assert.match(elements.cashChart.innerHTML,/Cumulative real cash impact · historical-weather ensemble mean −€1,000/,'year zero begins at the upfront cash outlay');
assert.match(elements.cashChart.innerHTML,/Cumulative real cash impact · historical-weather ensemble mean −€248/,'year one cumulative amount sums the ensemble mean cash and deflates it at the inflation rate');
assert.match(elements.cashChart.innerHTML,/Discounted annual contribution/);
assert.match(elements.cashChart.innerHTML,/Cumulative discounted impact · historical-weather ensemble mean −€277/,'discounted cumulative impact reflects real discounting');
assert.match(elements.cashChart.innerHTML,/real rate/,'selected discount rate is named in the chart');
assert.match(elements.cashFlowDescription.textContent,/4\.0% real/,'selected discount rate is explained in plain language');
assert.match(elements.cashInsights.innerHTML,/Payback of ensemble-mean cash flows|Undiscounted break-even/);
assert.match(elements.cashInsights.innerHTML,/Not reached in 3 years/,'a temporary zero crossing followed by losses is not called recovery');
assert.match(elements.cashInsights.innerHTML,/At year 3 · historical-weather ensemble mean real impact/);
assert.match(elements.cashInsights.innerHTML,/[−-]€206/,'real ensemble mean impact at year 3 is displayed in the insight card');
assert.match(elements.cashChart.innerHTML,/Avoided same-load electricity bills: €980/,'annual diagnostic components are converted to real euros');
assert.match(elements.cashChart.innerHTML,/Replacement: −€865/,'replacement details use real euros');
assert.match(elements.cashChart.innerHTML,/Residual value: €283/,'residual details use real euros');
assert.doesNotMatch(elements.cashChart.innerHTML,/Loan repayments · principal/,'project view omits loan repayment from annual breakdown');
assert.equal(run(`cashFlowLabel('real')`),'Real year-end cash flows · upfront payment at year 0, then annual savings, receipts, operating costs and replacements.');

const nominalPaths=[[-1000,600,600,-250],[-1000,1000,500,-800],[-1000,700,650,-600]];
const terminalValues=nominalPaths.map(path=>path.reduce((sum,amount,year)=>sum+amount/Math.pow(1+.0608,year),0)).sort((a,b)=>a-b);
const expectedNpv=terminalValues[1];
run(`current.result.finance.pv_incremental={npv:{median:${expectedNpv}}};renderCashChart()`);
const expectedNpvStr=run(`fmt(${expectedNpv},'eur')`);

// Undiscounted real chart end is distinct from discounted NPV end
assert.match(elements.cashInsights.innerHTML,/At year 3 · historical-weather ensemble mean real impact/);
assert.match(elements.cashInsights.innerHTML,/[−-]€206/);
assert.match(elements.cashChart.innerHTML,/Cumulative discounted impact · historical-weather ensemble mean −€220/);
assert.notEqual('−€206',expectedNpvStr,'undiscounted chart end is distinct from discounted NPV');

const year3RealCumulative=elements.cashChart.innerHTML.match(/Year 3\n[\s\S]*?Cumulative real cash impact · historical-weather ensemble mean ([^\(]+) \(weather range/)?.[1].trim();
assert.equal(year3RealCumulative,'−€206');

// Switch to nominal mode: undiscounted cumulative path changes
run(`current.result.settings.cashflow_mode='nominal';renderCashChart()`);
assert.match(elements.cashInsights.innerHTML,/At year 3 · historical-weather ensemble mean nominal impact/);
assert.match(elements.cashInsights.innerHTML,/[−-]€200/);

const year3NominalCumulative=elements.cashChart.innerHTML.match(/Year 3\n[\s\S]*?Cumulative nominal cash impact · historical-weather ensemble mean ([^\(]+) \(weather range/)?.[1].trim();
assert.equal(year3NominalCumulative,'−€200');

// Under nonzero inflation, real and nominal cumulative values differ
assert.notEqual(year3RealCumulative,year3NominalCumulative,'real and nominal cumulative values differ under nonzero inflation');

// Year 0 remains identical in both modes
assert.match(elements.cashChart.innerHTML,/Cumulative nominal cash impact · historical-weather ensemble mean −€1,000/,'year 0 is unchanged between real and nominal');

// Discounted cumulative impact remains invariant in nominal mode
assert.match(elements.cashChart.innerHTML,/Cumulative discounted impact · historical-weather ensemble mean −€220/,'discounted cumulative impact remains invariant across modes');

// Independent numeric verification for all four overview bases under nonzero inflation
const independentPathMeans=(rawPaths,inflationRate,isReal)=>{
 const nYears=rawPaths[0].length;
 const deflatedRows=rawPaths.map(row=>row.map((amt,yr)=>amt/(isReal?Math.pow(1+inflationRate,yr):1)));
 const means=[];
 let tot=0;
 for(let yr=0;yr<nYears;yr++){
  const sum=deflatedRows.reduce((acc,row)=>acc+row[yr],0);
  tot+=sum/deflatedRows.length;
  means.push(tot);
 }
 return means;
};
const extractCumulativeMeans=(html,mode)=>{
 const regex=new RegExp(`Year (\\d+)\\n[\\s\\S]*?Cumulative ${mode} cash impact · historical-weather ensemble mean ([^\\(]+)`,'g');
 const result=[];
 for(const m of html.matchAll(regex)){
  result[Number(m[1])]=m[2].trim();
 }
 return result;
};

const fourBases=[
 {basis:'project',key:'pv_incremental'},
 {basis:'package',key:'whole_package'},
 {basis:'equity',key:'equity'},
 {basis:'financed',key:'financed_project'}
];
for(const {basis,key} of fourBases){
 elements.cashFlowBasis.value=basis;
 run("current.result.settings.cashflow_mode='real';renderCashChart()");
 const realMeans=extractCumulativeMeans(elements.cashChart.innerHTML,'real');
 run("current.result.settings.cashflow_mode='nominal';renderCashChart()");
 const nominalMeans=extractCumulativeMeans(elements.cashChart.innerHTML,'nominal');

 const rawFlows=run(`current.result.finance.cashflows_by_weather.${key}`);
 const expectedReal=independentPathMeans(rawFlows,.02,true).map(v=>run(`fmt(${v},'eur')`));
 const expectedNominal=independentPathMeans(rawFlows,.02,false).map(v=>run(`fmt(${v},'eur')`));

 assert.equal(realMeans[0],nominalMeans[0],`${basis} year 0 cumulative amount is unchanged between real and nominal`);
 assert.deepEqual(realMeans,expectedReal,`${basis} real cumulative path matches independent arithmetic`);
 assert.deepEqual(nominalMeans,expectedNominal,`${basis} nominal cumulative path matches independent arithmetic`);
 for(let yr=1;yr<realMeans.length;yr++){
  assert.notEqual(realMeans[yr],nominalMeans[yr],`${basis} year ${yr} cumulative differs between real and nominal under inflation`);
 }
}
elements.cashFlowBasis.value='project';

// Under zero inflation, real and nominal cumulative paths are identical
run(`current.result.settings.inflation=0;current.result.settings.cashflow_mode='nominal';renderCashChart()`);
const zeroInflNominalEnd=elements.cashChart.innerHTML.match(/Year 3\n[\s\S]*?Cumulative nominal cash impact · historical-weather ensemble mean ([^\(]+) \(weather range/)?.[1].trim();
run(`current.result.settings.cashflow_mode='real';renderCashChart()`);
const zeroInflRealEnd=elements.cashChart.innerHTML.match(/Year 3\n[\s\S]*?Cumulative real cash impact · historical-weather ensemble mean ([^\(]+) \(weather range/)?.[1].trim();
assert.equal(zeroInflNominalEnd,zeroInflRealEnd,'with zero inflation, real and nominal cumulative paths are identical');

// Restore test assumptions
run(`current.result.settings.inflation=.02;current.result.settings.cashflow_mode='real';renderCashChart()`);

elements.cashFlowBasis.value='package';run('renderCashChart()');
assert.equal(elements.cashFlowHeading.textContent,'Whole-house cash · versus historic bills');
assert.match(elements.cashChart.innerHTML,/Avoided historic package bills: €1,373/,'whole-house view explains savings against historic electricity and gas bills');
assert.doesNotMatch(elements.cashChart.innerHTML,/Loan payment/,'unfinanced package view does not imply loan repayments');

elements.cashFlowBasis.value='equity';run('current.result.finance.loan_principal_eur=8000;current.result.finance.loan_scope="solar_and_conversion";current.result.finance.cashflows_by_weather.equity[0][0]=-13150;current.result.finance.cashflows_by_weather.financed_project[0][0]=-21150;current.result.finance.equity={discounted_payback:{min:8.70965,median:8.91123,max:9.31298,recovered_weather_years:19,weather_count:19},discounted_payback_of_mean_cash_flows:8.91123};current.result.finance.financed_project={discounted_payback:{min:10.20235,median:10.42914,max:10.87056,recovered_weather_years:19,weather_count:19},discounted_payback_of_mean_cash_flows:10.42914};renderCashChart()');
assert.equal(elements.cashFlowHeading.textContent,'Your cash · solar + electrification');
assert.equal(elements.cashInsights.innerHTML.match(/<span>(?:Discounted personal cash recovery|Undiscounted break-even|Discounted payback):?<\/span>/)?.length,1);
assert.match(elements.cashFlowBasisNote.textContent,/€13,150 down payment; loan €8,000 repaid from savings/);
assert.match(elements.cashInsights.innerHTML,/(?:Discounted personal cash recovery|Undiscounted break-even|Payback of ensemble-mean cash flows)[\s\S]*?(?:8\.9 y|Not reached in 3 years)/);
assert.match(elements.cashInsights.innerHTML,/(?:Weather range|discounted per-weather payback min–max) 8\.7 y – 9\.3 y/);
const personalDashboard=run('personalPaybackCard(current.result)');
assert.match(personalDashboard,/DISCOUNTED PERSONAL CASH RECOVERY/);
assert.match(personalDashboard,/8\.7 y – 9\.3 y/);
assert.match(personalDashboard,/Your upfront cash after €8,000 of loan proceeds: €13,150/);
assert.match(elements.cashChart.innerHTML,/Loan repayments · principal \+ interest: −€490/,'equity breakdown uses full real loan payments');
assert.match(elements.cashChart.innerHTML,/Tax deduction: €98/,'raw nominal diagnostics are deflated once even when display fields are also present');
assert.match(elements.cashChart.innerHTML,/Replacement: −€865/,'raw replacement diagnostics are deflated once rather than using their separate display field');

elements.cashFlowBasis.value='financed';run('renderCashChart()');
assert.match(elements.cashChart.innerHTML,/Loan interest only: −€98/,'financed system view deducts interest only');
assert.match(elements.cashInsights.innerHTML,/(?:Discounted full-cost recovery \+ interest|Undiscounted break-even|Payback of ensemble-mean cash flows)[\s\S]*?(?:10\.4 y|Not reached in 3 years)/);
assert.match(elements.cashInsights.innerHTML,/(?:Weather range|discounted per-weather payback min–max) 10\.2 y – 10\.9 y/);
// full-cost basis note replaced by dynamic loan note
const fullCostDashboard=run('financedPaybackCard(current.result)');
assert.match(fullCostDashboard,/DISCOUNTED FULL-COST RECOVERY \+ INTEREST/);
assert.match(fullCostDashboard,/10\.2 y – 10\.9 y/);
assert.match(fullCostDashboard,/Full purchase cost €21,150 counted at year zero/);
assert.doesNotMatch(elements.cashChart.innerHTML,/Loan repayments · principal/);

// Full financing begins at zero equity, but later cash can dip before it recovers.
elements.cashFlowBasis.value='equity';run(`current.result.finance.cashflows_by_weather.equity=[[0,-100,100,50],[0,-200,300,100],[0,-100,200,50]];current.result.finance.loan_scope='solar';current.result.finance.equity={};renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/Initial outlay: €0/,'full financing removes the initial equity payment');
assert.match(elements.cashInsights.innerHTML,/(?:Discounted personal cash recovery|Undiscounted break-even|Payback of ensemble-mean cash flows)[\s\S]*?<strong>1\.7 y/,'zero at year zero is not called recovery if the path later goes negative');
assert.doesNotMatch(elements.cashChart.innerHTML,/NaN|undefined/,'horizon longer than weather-count does not break chart rendering');

// Discounting delays a nominal recovery: the undiscounted path is +€10 after two years, discounted stays negative.
elements.cashFlowBasis.value='project';run(`current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=0;current.result.finance.real_discount_rate=.1;current.result.finance.nominal_discount_rate=.1;current.result.finance.pv_incremental={discounted_payback:{min:null,median:null,max:null,recovered_weather_years:0,weather_count:1},discounted_payback_of_mean_cash_flows:null};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,55,55]];renderCashChart()`);
assert.match(elements.cashInsights.innerHTML,/discounted per-weather payback min–max Not reached/,'configured rate is applied to each annual cash flow');
assert.match(elements.cashFlowDescription.textContent,/10\.0% nominal/);

// A replacement expense breaks recovery until the final lasting crossing.
run(`current.result.finance.real_discount_rate=0;current.result.finance.nominal_discount_rate=0;current.result.finance.pv_incremental={discounted_payback:{min:2.5,median:2.5,max:2.5,recovered_weather_years:1,weather_count:1},discounted_payback_of_mean_cash_flows:2.5};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,150,-100,100]];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/Cash break-even · year 2\.5/,'display chart shows persistent break-even after replacement dip');
assert.match(elements.cashInsights.innerHTML,/(?:Discounted solar project recovery|Undiscounted break-even|Payback of ensemble-mean cash flows)[\s\S]*?<strong>2\.5 y/);

// Annual weather ranks swap, so median cumulative cash differs from summing annual medians.
run(`current.result.settings.inflation=0;current.result.finance.real_discount_rate=0;current.result.finance.nominal_discount_rate=0;current.result.finance.pv_incremental={};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,0,100],[-100,1,-100],[-100,2,0]];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/Cumulative discounted impact · historical-weather ensemble mean −€99/,'mean is calculated from complete weather paths');

// Absent precomputed finance discounted_payback with nonzero discount:
// undiscounted display curve reaches +€10 and shows cash break-even marker, but discounted card is not reached (-€4.55 end).
elements.cashFlowBasis.value='project';
run(`current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=0;current.result.finance.real_discount_rate=.1;current.result.finance.nominal_discount_rate=.1;current.result.finance.pv_incremental={};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,55,55]];current.result.annual_cashflows=[{year:0},{year:1},{year:2}];renderCashChart()`);
assert.match(elements.cashInsights.innerHTML,/At year 2 · historical-weather ensemble mean nominal impact:?<\/span>\s*<strong>\+€10<\/strong>/,'undiscounted display curve reaches +10 cumulative end');
assert.match(elements.cashChart.innerHTML,/Cash break-even · year 1\.8/,'display chart shows undiscounted median cash break-even marker');
assert.match(elements.cashInsights.innerHTML,/discounted per-weather payback min–max Not reached/,'discounted card is not reached when discounted cumulative cash stays negative');
assert.match(elements.cashInsights.innerHTML,/1 non-recovering cases/,'paybackDetail default weather counts reference the discounted population');

// Absent field with nonzero inflation AND consistent Fisher rates:
// discounted fallback is invariant across real/nominal mode while display graph ends differ.
const fisherInfl=.04,fisherRealRate=.05,fisherNomRate=(1+fisherRealRate)*(1+fisherInfl)-1;
const fisherPaths=[[-1000,600,600],[-1000,500,750]];
run(`current.result.settings.inflation=${fisherInfl};current.result.finance.real_discount_rate=${fisherRealRate};current.result.finance.nominal_discount_rate=${fisherNomRate};current.result.finance.pv_incremental={};current.result.finance.cashflows_by_weather.pv_incremental=${JSON.stringify(fisherPaths)};current.result.annual_cashflows=[{year:0},{year:1},{year:2}]`);
run(`current.result.settings.cashflow_mode='real';renderCashChart()`);
const fisherRealEnd=elements.cashInsights.innerHTML.match(/Historical-weather ensemble mean 2-y cumulative impact<\/span><div class="cash-popover">[\s\S]*?<\/div><\/div><\/div><strong>([^<]+)<\/strong>/)?.[1];
const fisherRealPayback=elements.cashInsights.innerHTML.match(/discounted per-weather payback min–max ([\d.]+ y)/)?.[1];
const fisherRealDetail=elements.cashInsights.innerHTML.match(/\(discounted per-weather payback min–max [^)]+\)/)?.[0];

run(`current.result.settings.cashflow_mode='nominal';renderCashChart()`);
const fisherNomEnd=elements.cashInsights.innerHTML.match(/Historical-weather ensemble mean 2-y cumulative impact<\/span><div class="cash-popover">[\s\S]*?<\/div><\/div><\/div><strong>([^<]+)<\/strong>/)?.[1];
const fisherNomPayback=elements.cashInsights.innerHTML.match(/discounted per-weather payback min–max ([\d.]+ y)/)?.[1];
const fisherNomDetail=elements.cashInsights.innerHTML.match(/\(discounted per-weather payback min–max [^)]+\)/)?.[0];

assert.notEqual(fisherRealEnd,fisherNomEnd,'display graph ends differ under inflation');
assert.equal(fisherRealPayback,fisherNomPayback,'discounted fallback payback is identical across real and nominal modes under consistent Fisher rates');
assert.equal(fisherRealDetail,fisherNomDetail,'discounted fallback detail is identical across real and nominal modes under consistent Fisher rates');
assert.equal(fisherRealPayback,'1.9 y','discounted payback matches independent discounted crossing');
assert.match(fisherRealDetail,/1\.9 y – 1\.9 y/,'discounted fallback detail matches');

// Fallback persistent crossing after later costs remains correct.
run(`current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=0;current.result.finance.real_discount_rate=.1;current.result.finance.nominal_discount_rate=.1;current.result.finance.pv_incremental={};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,70,70,-60,70]];current.result.annual_cashflows=[{year:0},{year:1},{year:2},{year:3},{year:4}];renderCashChart()`);
assert.match(elements.cashInsights.innerHTML,/(?:Undiscounted break-even|Payback of ensemble-mean cash flows)[\s\S]*?<strong>3\.3 y<\/strong>/,'fallback payback requires persistent crossing and rejects premature recovery before later dip');
assert.doesNotMatch(elements.cashInsights.innerHTML,/1\.6 y|1\.7 y/,'first non-persistent zero crossing is not reported as recovery');
assert.match(elements.cashInsights.innerHTML,/discounted per-weather payback min–max 3\.5 y/,'payback detail reflects single persistent recovery');

run(`const financedResult={settings:{loan_fraction:0,loan_mode:'amount'},finance:{loan_principal_eur:0,requested_loan_amount_eur:0,capex_eur:21150,loan_scope:'solar',equity:{discounted_payback:{min:0,median:0,max:0}},financed_project:{discounted_payback:{min:9.5,median:9.63,max:9.8},discounted_payback_of_mean_cash_flows:9.63,unpaid_discounted_weather_years:0}}};`);
assert.equal(run('financedPaybackCard(financedResult)'),'', 'no financing card without a loan');
run(`const packageResult={settings:{load_scenario:'baseline'},metrics:{},finance:{nominal_discount_rate:.06,whole_package:{simple_payback:{min:7,median:7.5,max:8,recovered_weather_years:19},discounted_payback:{min:9,median:9.5,max:10,recovered_weather_years:19},simple_payback_of_mean_cash_flows:7.5,discounted_payback_of_mean_cash_flows:9.5}}}`);
assert.equal(run('packagePaybackCards(packageResult)'),'', 'baseline load does not show a conversion package payback card');
run(`packageResult.settings.load_scenario='electrified'`);
const packageCards=run('packagePaybackCards(packageResult)');
assert.match(packageCards,/WHOLE-HOUSE PACKAGE PAYBACK/);
assert.match(packageCards,/7\.0 y – 8\.0 y/);
assert.match(packageCards,/includes conversion cost and avoided historic gas\/electric bills/);
assert.match(packageCards,/DISCOUNTED WHOLE-HOUSE PACKAGE PAYBACK/);
assert.match(packageCards,/9\.0 y – 10\.0 y/);
run('financedResult.finance.loan_principal_eur=500;financedResult.finance.requested_loan_amount_eur=500');
const partial=run('financedPaybackCard(financedResult)');
assert.match(partial,/DISCOUNTED FULL-COST RECOVERY \+ INTEREST/);
assert.match(partial,/9\.5 y – 9\.8 y/,'shows full-cost payback rather than the zero equity payback');
assert.match(partial,/Full purchase cost €21,150 counted at year zero/);
assert.match(partial,/Solar loan €500/);
run('financedResult.finance.loan_scope="solar_and_conversion";financedResult.finance.requested_loan_amount_eur=2000;financedResult.finance.loan_principal_eur=1500');
const full=run('financedPaybackCard(financedResult)');
assert.match(full,/9\.5 y – 9\.8 y/,'full financing still recovers the entire system cost');
assert.doesNotMatch(full,/0\.0 y/);
assert.match(full,/Solar \+ electrification loan €1,500 \(requested €2,000; capped at eligible capex\)/,'absolute principal and financed scope remain visible');
run('financedResult.finance.financed_project.discounted_payback={min:null,median:null,max:null};financedResult.finance.financed_project.discounted_payback_of_mean_cash_flows=null;financedResult.finance.financed_project.unpaid_discounted_weather_years=19');
assert.match(run('financedPaybackCard(financedResult)'),/Not reached/);
assert.match(run('financedPaybackCard(financedResult)'),/19 weather cases do not recover/);

// A single extreme weather outcome must not flatten the median annual bars.
elements.cashFlowBasis.value='project';run(`current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=0;current.result.finance.nominal_discount_rate=0;current.result.finance.cashflows_by_weather.pv_incremental=[[-100,10,110],[-100,50,60],[-100,1000,1000]];current.result.finance.pv_incremental={};renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/viewBox="0 0 960 440"/,'the annual cash panel has enough vertical room to read');
assert.match(elements.cashChart.innerHTML,/Historical-weather ensemble mean annual net cash · years 1–2/,'the annual axis identifies the statistic used by its bars');
assert.match(elements.cashChart.innerHTML,/width="\d+" height="22" rx="4" fill="white"/,'the break-even annotation has a high-contrast backing so it stays legible over chart lines');
const yearOneMeanBar=elements.cashChart.innerHTML.match(/<rect x="501" y="[\d.]+" width="10" height="([\d.]+)"/);
assert.ok(yearOneMeanBar&&Math.abs(Number(yearOneMeanBar[1])-49.47)<0.1,'mean €353 uses the rounded €400 axis even when a weather outlier reaches €1,000');
assert.match(elements.cashChart.innerHTML,/\+€400/,'annual axis bound is rounded to a nice tick (400 rather than raw 390)');
assert.doesNotMatch(elements.cashChart.innerHTML,/\+€390/,'raw unrounded mean maximum is not used as axis bound');

// Focused checks for cash break-even zero-crossing redesign:
// 1. Layering: break-even callout rendered AFTER annual marks so it cannot be covered
const annualMarksLastIdx=elements.cashChart.innerHTML.lastIndexOf('class="chart-point"');
const calloutIdx=elements.cashChart.innerHTML.indexOf('class="cash-breakeven-callout"');
assert.ok(annualMarksLastIdx!==-1&&calloutIdx!==-1,'both annual marks and callout are rendered');
assert.ok(calloutIdx>annualMarksLastIdx,'callout is rendered after annual marks so green markers cannot cover the amber point');

// 2. Nonblocking overlay: pointer-events="none" on visual callout overlay
assert.match(elements.cashChart.innerHTML,/<g class="cash-breakeven-callout" pointer-events="none"/,'overlay has pointer-events="none" to keep year hover/focus functional');

// 3. Amber marker with white halo and leader line
assert.match(elements.cashChart.innerHTML,/<circle cx="[\d.]+" cy="[\d.]+" r="7\.5" fill="white" opacity="\.96"\/>/,'amber marker includes white halo');
assert.match(elements.cashChart.innerHTML,/<circle cx="[\d.]+" cy="[\d.]+" r="5" fill="#b45309" stroke="white" stroke-width="1\.5"\/>/,'amber marker uses high-contrast styling');
assert.match(elements.cashChart.innerHTML,/<line x1="[\d.]+" y1="[\d.]+" x2="[\d.]+" y2="[\d.]+" stroke="#b45309" stroke-width="1\.5"/,'callout leader connects to exact zero crossing');

// 4. Concise explanation outside plot
assert.match(elements.cashChart.innerHTML,/<p class="cash-chart-explanation">The historical-weather ensemble mean cash balance reaches €0 and stays non-negative through year 2\. Discounted recovery above also accounts for the time value of money\. Band: historical scenario min–max; each weather profile repeats throughout the project horizon\.<\/p>/,'concise explanation outside plot is present');

// 5. Accessible label with crossing year and horizon
assert.match(elements.cashChart.innerHTML,/aria-label="Cash break-even at year 0\.3 through year 2; historical-weather ensemble mean cumulative cash reaches €0 and stays non-negative"/,'callout includes accessible label with year and horizon');
elements.cashFlowHeading.textContent='Overview heading stays put';elements.cashFlowDescription.textContent='Overview description stays put';elements.cashFlowBasisNote.textContent='Overview note stays put';
run("renderCashChart(current.result,'loanCashChart','equity')");
assert.equal(elements.cashFlowHeading.textContent,'Overview heading stays put','the loan chart does not rewrite the Overview heading');
assert.equal(elements.cashFlowDescription.textContent,'Overview description stays put','the loan chart does not rewrite the Overview description');
assert.equal(elements.cashFlowBasisNote.textContent,'Overview note stays put','the loan chart does not rewrite the Overview cash-basis note');
assert.match(elements.loanCashChart.innerHTML,/loanCashChart-cashBand/,'the duplicated chart uses its own gradient id');

// loanCashChart respects real/nominal cashflow_mode
run("current.result.settings.cashflow_mode='real';current.result.settings.inflation=.02;renderCashChart(current.result,'loanCashChart','equity')");
assert.match(elements.loanCashChart.innerHTML,/Cumulative real cash impact/,'loan chart renders real basis');
const loanRealYear3=elements.loanCashChart.innerHTML.match(/Year 2\n[\s\S]*?Cumulative real cash impact(?: \(undiscounted\))? · historical-weather ensemble mean ([^\(]+) \(weather range/)?.[1].trim();

run("current.result.settings.cashflow_mode='nominal';renderCashChart(current.result,'loanCashChart','equity')");
assert.match(elements.loanCashChart.innerHTML,/Cumulative nominal cash impact/,'loan chart renders nominal basis');
const loanNominalYear3=elements.loanCashChart.innerHTML.match(/Year 2\n[\s\S]*?Cumulative nominal cash impact(?: \(undiscounted\))? · historical-weather ensemble mean ([^\(]+) \(weather range/)?.[1].trim();
assert.ok(loanRealYear3 && loanNominalYear3, 'loan chart cumulative values captured');
assert.notEqual(loanRealYear3,loanNominalYear3,'loan chart cumulative values differ between real and nominal under inflation');

vm.runInContext(source.slice(source.indexOf('function computeImpliedTaeg('),source.indexOf('function persistLoanSimulations(')),context);
run("const realFlow=[-500,...Array.from({length:20},(_,i)=>40)];const nominalPath=realFlow.map((v,y)=>v*Math.pow(1.02,y));const paired={settings:{cashflow_mode:'real',inflation:.02,real_discount_rate:.04},finance:{real_discount_rate:.04,nominal_discount_rate:.0608,equity:{discounted_payback:{median:17}},cashflows_by_weather:{equity:[nominalPath]}}};const realMetrics=currentLoanMetrics(paired);paired.settings.cashflow_mode='nominal';const nominalMetrics=currentLoanMetrics(paired);");
assert.ok(Math.abs(run('realMetrics.year20-nominalMetrics.year20'))<1e-8,'year-20 impact is equal in real and nominal display modes');
run("current.result={settings:{loan_apr:.05,loan_years:10,loan_max_monthly_payment:123.45,loan_include_electrification:false,inflation:.02,real_discount_rate:.04},finance:{loan_principal_eur:6000,nominal_discount_rate:.0608,real_discount_rate:.04,equity:{discounted_payback:{median:8}},cashflows_by_weather:{equity:[[-5000,...Array(20).fill(500)]]}}};const oldSnapshotSettings={...current.result.settings};delete oldSnapshotSettings.loan_max_monthly_payment;loanSaved=[{name:'Saved case',settings:oldSnapshotSettings,result:current.result,metrics:currentLoanMetrics(current.result),quote:{line_items:[]}}];renderLoanPivot()");
assert.match(elements.loanPivot.innerHTML,/Initial cash/);
assert.match(elements.loanPivot.innerHTML,/At year 20 · discounted cash impact/);
assert.match(elements.loanPivot.innerHTML,/Discounted cash recovery/);
assert.match(elements.loanPivot.innerHTML,/Loan principal/);
assert.match(elements.loanPivot.innerHTML,/€123\.45/,'comparison preserves the budget to cents');
assert.match(elements.loanPivot.innerHTML,/No limit/,'older saved snapshots without a monthly budget remain compatible');
assert.match(elements.loanPivot.innerHTML,/5\.00% TAN/);
assert.match(elements.loanPivot.innerHTML,/Simulation horizon/);

// --- Component medians in cash-chart tooltip and renderCashTooltip tests ---
vm.runInContext(source.slice(source.indexOf('function renderCashTooltip('),source.indexOf('function showChartTooltip(')),context);
const makeMockElement=(tag='div')=>{
 const el={
  tagName:tag.toUpperCase(),
  className:'',
  textContent:'',
  children:[],
  style:{},
  appendChild(child){el.children.push(child);return child;},
  append(...nodes){el.children.push(...nodes);},
  replaceChildren(...nodes){el.children=nodes?[...nodes]:[];}
 };
 return el;
};
context.document.createElement=makeMockElement;

// Asymmetric 3-weather fixture with internally consistent component means and cashflows
const weatherAvoided = [800, 900, 1300]; // mean 1000, median 900
const weatherNetExport = [150, 180, 270]; // mean 200, median 180
const weatherElectricBill = [2800, 2700, 2300]; // mean 2600, median 2700
const weatherAllowance = [30, 60, 0]; // mean 30, median 30

const meanAvoided = 1000;
const meanNetExport = 200;
const meanCer = 10;
const basePkgBill = 4000;
const withPvBill = 2600;
const remGasBill = 0;
const meanPkgSaving = basePkgBill - withPvBill - remGasBill; // 1400

const consistentCashRows = Array.from({length: 4}, (_, year) => ({
 year,
 pv_incremental_range: ranges(),
 whole_package_range: ranges(),
 equity_range: ranges(),
 financed_project_range: ranges(),
 avoided_import: meanAvoided,
 net_export: meanNetExport,
 cer: meanCer,
 baseline_package_bill: basePkgBill,
 with_system_electric_bill: withPvBill,
 remaining_gas_bill: remGasBill,
 unserved_service_allowance: (year === 1 || year === 2) ? 30 : 0,
 hp_maintenance: year > 0 ? 40 : 0,
 hp_replacement: year === 2 ? 600 : 0,
 tax_deduction: year > 0 ? 100 : 0,
 maintenance: year === 1 ? 0 : (year > 1 ? 80 : 0),
 debt_service: year > 0 ? 500 : 0,
 loan_interest: year > 0 ? 100 : 0,
 replacement: year === 2 ? 900 : 0,
 inverter_replacement: year === 2 ? 400 : 0,
 battery_replacement: year === 2 ? 500 : 0,
 residual: year === 3 ? 300 : 0,
 ...(year > 0 ? {
  component_medians: {
   avoided_import: 900,
   net_export_cer: 190,
   avoided_package_bills: 1300
  }
 } : {})
}));

const consistentWeatherCashflows = {
 pv_incremental: [
  [-4000, 1060, 80, 1280],
  [-4000, 1190, 210, 1410],
  [-4000, 1680, 700, 1900]
 ],
 solar_equity: [
  [0, 810, -170, 1030],
  [0, 940, -40, 1160],
  [0, 1430, 450, 1650]
 ],
 whole_package: [
  [-8000, 1390, -190, 1610],
  [-8000, 1490, -90, 1710],
  [-8000, 2040, 460, 2260]
 ],
 package_equity: [
  [0, 890, -690, 1110],
  [0, 990, -590, 1210],
  [0, 1540, -40, 1760]
 ],
 financed_project: [
  [-8000, 1290, -290, 1510],
  [-8000, 1390, -190, 1610],
  [-8000, 1940, 360, 2160]
 ],
 equity: [
  [0, 890, -690, 1110],
  [0, 990, -590, 1210],
  [0, 1540, -40, 1760]
 ]
};

run(`current.result.annual_cashflows=${JSON.stringify(consistentCashRows)};`);
run(`current.result.finance.cashflows_by_weather=${JSON.stringify(consistentWeatherCashflows)};`);
run("current.result.finance.loan_principal_eur=8000;current.result.finance.capex_eur=4000;current.result.finance.solar_loan_principal_eur=4000;");
run("current.result.finance.real_discount_rate=0.04;current.result.finance.nominal_discount_rate=0.0608;current.result.settings.real_discount_rate=0.04;");

const parseBreakdownComponents = (detailText) => {
 const lines = detailText.split('\n');
 const bIdx = lines.findIndex(l => l.startsWith('Annual breakdown'));
 if (bIdx === -1) return null;
 const header = lines[bIdx];
 const items = [];
 for (let i = bIdx + 1; i < lines.length; i++) {
  const line = lines[i];
  if (!line) continue;
  const isSubitem = line.startsWith('  ');
  const split = line.lastIndexOf(': ');
  if (split < 0) continue;
  const label = line.slice(0, split).trim();
  const valStr = line.slice(split + 2).trim();
  const numMatch = valStr.match(/([−-]?)€([\d,]+)/);
  if (!numMatch) continue;
  const sign = numMatch[1] ? -1 : 1;
  const amount = sign * Number(numMatch[2].replace(/,/g, ''));
  items.push({label, amount, isSubitem});
 }
 return {header, items};
};

const sumBreakdown = (parsed) => {
 return parsed.items.filter(it => !it.isSubitem).reduce((sum, it) => sum + it.amount, 0);
};

// 1. Solar after-loan view (solar_equity) - Nominal mode & Summary target (cashChart)
run("current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=.02;renderCashChart(current.result,'cashChart','solar_equity','undiscounted');");
assert.match(elements.cashInsights.innerHTML, /Payback of ensemble-mean cash flows|Undiscounted break-even/, 'solar equity insight shows payback');
assert.match(elements.cashChart.innerHTML, /Annual breakdown · ensemble mean \(nominal undiscounted\):/, 'solar equity nominal shows exact ensemble mean undiscounted header');
assert.doesNotMatch(elements.cashChart.innerHTML, /Annual breakdown · weather medians/, 'nominal breakdown does not claim weather medians');
assert.doesNotMatch(elements.cashChart.innerHTML, /component medians may not sum to annual cash median/, 'nominal detail removes non-additivity note');
assert.doesNotMatch(elements.cashChart.innerHTML, /not additive/i, 'nominal detail removes not additive disclaimer');
assert.match(elements.cashChart.innerHTML, /Avoided same-load electricity bills: €1,000/, 'solar equity nominal uses mean avoided_import');
assert.doesNotMatch(elements.cashChart.innerHTML, /Avoided same-load electricity bills: €850|Avoided same-load electricity bills: €900/, 'solar equity does not use median avoided_import');
assert.match(elements.cashChart.innerHTML, /Net export \+ CER: €210/, 'solar equity nominal uses mean net_export_cer');
assert.doesNotMatch(elements.cashChart.innerHTML, /Net export \+ CER: €160|Net export \+ CER: €190/, 'solar equity does not use median net_export_cer');
assert.match(elements.cashChart.innerHTML, /Tax deduction: €100/, 'deterministic tax deduction preserved');
assert.match(elements.cashChart.innerHTML, /Maintenance: €0/, 'zero expense formats as €0');
assert.doesNotMatch(elements.cashChart.innerHTML, /Maintenance: −€0|-€0/, 'zero expense does not show negative zero');
assert.match(elements.cashChart.innerHTML, /Solar loan repayments: −€250/, 'proportional solar loan repayments preserved');
assert.match(elements.cashChart.innerHTML, /Maintenance: −€80/, 'nonzero expense formats with negative sign');
assert.match(elements.cashChart.innerHTML, /Replacement: −€900/, 'replacement expense formats with negative sign');
assert.match(elements.cashChart.innerHTML, /Inverter replacement: −€400/, 'inverter replacement subitem is displayed undiscounted');
assert.match(elements.cashChart.innerHTML, /Battery replacement \/ augmentation: −€500/, 'battery replacement subitem is displayed undiscounted');
assert.match(elements.cashChart.innerHTML, /Residual value: €300/, 'residual value formats correctly');

// Assert component sum matches annual undiscounted ensemble mean in Year 1 and Year 2 (solar_equity)
const solarNomY1Detail = elements.cashChart.innerHTML.match(/data-chart-detail="([^"]*Year 1[\s\S]*?Annual breakdown[\s\S]*?)"/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const parsedSolarNomY1 = parseBreakdownComponents(solarNomY1Detail);
assert.equal(sumBreakdown(parsedSolarNomY1), 1060, 'Solar equity Year 1 nominal component sum matches annual ensemble mean cashflow');
const solarNomY2Detail = elements.cashChart.innerHTML.match(/data-chart-detail="([^"]*Year 2[\s\S]*?Annual breakdown[\s\S]*?)"/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const parsedSolarNomY2 = parseBreakdownComponents(solarNomY2Detail);
assert.equal(sumBreakdown(parsedSolarNomY2), 80, 'Solar equity Year 2 nominal component sum matches annual ensemble mean cashflow (ignoring subitems)');

// 2. Solar after-loan view (solar_equity) - Real mode & Discounted curve (nonzero discount rate test)
run("current.result.settings.cashflow_mode='real';current.result.settings.inflation=.02;renderCashChart(current.result,'cashChart','solar_equity','discounted');");
assert.match(elements.cashChart.innerHTML, /Annual breakdown · ensemble mean \(real undiscounted\):/, 'solar equity real discounted curve shows real undiscounted header');
assert.match(elements.cashChart.innerHTML, /Avoided same-load electricity bills: €980/, 'real mode deflates mean avoided_import once (undiscounted despite discounted curve)');
assert.match(elements.cashChart.innerHTML, /Net export \+ CER: €206/, 'real mode deflates mean net export + cer once');
assert.match(elements.cashChart.innerHTML, /Solar loan repayments: −€245/, 'real mode deflates proportional solar loan repayments');
assert.match(elements.cashChart.innerHTML, /Replacement: −€865/, 'replacement is deflated once in real mode and NOT discounted (former bug divided by discDiv)');
assert.match(elements.cashChart.innerHTML, /Inverter replacement: −€384/, 'inverter replacement subitem is deflated once and undiscounted');
assert.match(elements.cashChart.innerHTML, /Battery replacement \/ augmentation: −€481/, 'battery replacement subitem is deflated once and undiscounted');

const solarRealY1Detail = elements.cashChart.innerHTML.match(/data-chart-detail="([^"]*Year 1[\s\S]*?Annual breakdown[\s\S]*?)"/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&');
assert.doesNotMatch(solarRealY1Detail, /Avoided same-load electricity bills: €942|Avoided same-load electricity bills: €943/, 'Year 1 avoided_import is not discounted by nonzero discount rate');
assert.doesNotMatch(solarRealY1Detail, /Net export \+ CER: €198/, 'Year 1 net export + cer is not discounted by nonzero discount rate');
const parsedSolarRealY1 = parseBreakdownComponents(solarRealY1Detail);
assert.ok(Math.abs(sumBreakdown(parsedSolarRealY1) - Math.round(1060 / 1.02)) <= 3, 'Solar equity Year 1 real component sum matches deflated undiscounted ensemble mean');

// 3. Whole-package after-loan view (package_equity) - Nominal mode & Financing target (loanCashChart)
run("current.result.settings.cashflow_mode='nominal';renderCashChart(current.result,'loanCashChart','package_equity','undiscounted');");
assert.match(elements.loanInsights.innerHTML, /Discounted financial recovery|Initial outlay/, 'package equity loan insight shows comparison');
assert.match(elements.loanCashChart.innerHTML, /Annual breakdown · ensemble mean \(nominal undiscounted\):/, 'package_equity nominal shows exact header in loanCashChart');
assert.doesNotMatch(elements.loanCashChart.innerHTML, /Annual breakdown · weather medians/, 'loanCashChart does not claim weather medians');
assert.match(elements.loanCashChart.innerHTML, /Avoided historic package bills: €1,400/, 'package_equity nominal uses mean package saving');
assert.doesNotMatch(elements.loanCashChart.innerHTML, /Avoided historic package bills: €1,250|Avoided historic package bills: €1,300/, 'package_equity does not use median package bills');
assert.doesNotMatch(elements.loanCashChart.innerHTML, /Avoided same-load electricity bills/, 'package_equity does not show solar-only electricity bills');
assert.match(elements.loanCashChart.innerHTML, /Net export \+ CER: €210/, 'package_equity nominal uses mean net export + cer');
assert.match(elements.loanCashChart.innerHTML, /Heat pump maintenance: −€40/, 'HP maintenance is included as separate expense');
assert.match(elements.loanCashChart.innerHTML, /Heat pump replacement: −€600/, 'HP replacement is included as separate expense');
assert.match(elements.loanCashChart.innerHTML, /Unserved service allowance: −€30/, 'unserved service allowance is kept as separate expense without double subtract');
assert.match(elements.loanCashChart.innerHTML, /Whole-project loan repayments: −€500/, 'package_equity shows whole-project loan debt service');

const pkgNomY2Detail = elements.loanCashChart.innerHTML.match(/data-chart-detail="([^"]*Year 2[\s\S]*?Annual breakdown[\s\S]*?)"/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const parsedPkgNomY2 = parseBreakdownComponents(pkgNomY2Detail);
assert.equal(sumBreakdown(parsedPkgNomY2), -440, 'Package equity Year 2 nominal component sum matches annual ensemble mean (-440)');

// 4. Whole-package after-loan view (package_equity) - Real mode & Discounted curve on Financing target
run("current.result.settings.cashflow_mode='real';renderCashChart(current.result,'loanCashChart','package_equity','discounted');");
assert.match(elements.loanCashChart.innerHTML, /Annual breakdown · ensemble mean \(real undiscounted\):/, 'loanCashChart discounted curve retains real undiscounted header');
assert.match(elements.loanCashChart.innerHTML, /Avoided historic package bills: €1,373/, 'package_equity real deflates mean package bills once (undiscounted despite curve)');
assert.doesNotMatch(elements.loanCashChart.innerHTML, /Avoided historic package bills: €1,225/, 'package_equity does not use median package bills');
assert.match(elements.loanCashChart.innerHTML, /Net export \+ CER: €206/, 'package_equity real deflates mean net_export_cer once');
assert.match(elements.loanCashChart.innerHTML, /Heat pump maintenance: −€39/, 'real mode deflates HP maintenance once');
assert.match(elements.loanCashChart.innerHTML, /Heat pump replacement: −€577/, 'real mode deflates HP replacement once');
assert.match(elements.loanCashChart.innerHTML, /Unserved service allowance: −€29/, 'real mode deflates unserved service allowance once');
assert.match(elements.loanCashChart.innerHTML, /Whole-project loan repayments: −€490/, 'package_equity real deflates whole-project debt service');

const pkgRealY2Detail = elements.loanCashChart.innerHTML.match(/data-chart-detail="([^"]*Year 2[\s\S]*?Annual breakdown[\s\S]*?)"/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&');
assert.doesNotMatch(pkgRealY2Detail, /Avoided historic package bills: €1,270|Avoided historic package bills: €1,320/, 'Year 2 package bills is not discounted by nonzero discount rate');
const parsedPkgRealY2 = parseBreakdownComponents(pkgRealY2Detail);
assert.ok(Math.abs(sumBreakdown(parsedPkgRealY2) - Math.round(-440 / (1.02 * 1.02))) <= 3, 'Package equity Year 2 real component sum matches deflated undiscounted ensemble mean');

// 5. Tooltip parser and rendered DOM verification (renderCashTooltip)
run("current.result.settings.cashflow_mode='real';renderCashChart(current.result,'cashChart','solar_equity','discounted');");
const realPointMatch = elements.cashChart.innerHTML.match(/data-chart-detail="([^"]*Annual breakdown · ensemble mean \(real undiscounted\)[^"]*)"/);
assert.ok(realPointMatch, 'chart point has data-chart-detail attribute with real undiscounted header');
const realPointDetail = realPointMatch[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&#39;/g, "'");
const mockTooltip = makeMockElement('div');
context.renderCashTooltip(mockTooltip, realPointDetail);
const headingEl = mockTooltip.children.find(c => c.className === 'chart-tooltip-section-title');
assert.equal(headingEl?.textContent, 'Annual breakdown · ensemble mean (real undiscounted)', 'rendered tooltip heading is intact with colon stripped');
const noteEl = mockTooltip.children.find(c => c.className === 'chart-tooltip-note');
assert.equal(noteEl, undefined, 'rendered tooltip does not include median disclaimer note');
const breakdownEl = mockTooltip.children.find(c => c.className === 'chart-tooltip-breakdown');
assert.ok(breakdownEl, 'rendered tooltip contains breakdown container');
const breakdownText = breakdownEl.children.map(c => c.textContent).join(' ');
assert.match(breakdownText, /Avoided same-load electricity bills/);
assert.match(breakdownText, /€980/);
assert.match(breakdownText, /Net export \+ CER/);
assert.match(breakdownText, /€206/);

run("current.result.settings.cashflow_mode='nominal';renderCashChart(current.result,'cashChart','solar_equity','undiscounted');");
const nomPointMatch = elements.cashChart.innerHTML.match(/data-chart-detail="([^"]*Annual breakdown · ensemble mean \(nominal undiscounted\)[^"]*)"/);
assert.ok(nomPointMatch, 'chart point has data-chart-detail attribute with nominal undiscounted header');
const nomPointDetail = nomPointMatch[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&#39;/g, "'");
const nomTooltip = makeMockElement('div');
context.renderCashTooltip(nomTooltip, nomPointDetail);
const nomHeadingEl = nomTooltip.children.find(c => c.className === 'chart-tooltip-section-title');
assert.equal(nomHeadingEl?.textContent, 'Annual breakdown · ensemble mean (nominal undiscounted)', 'nominal tooltip heading is intact with colon stripped');
const nomNoteEl = nomTooltip.children.find(c => c.className === 'chart-tooltip-note');
assert.equal(nomNoteEl, undefined, 'nominal tooltip does not include median disclaimer note');
// --- Focused checks for zero-crossing callout redesign ---

// 1. Replacement dip: path crosses zero early at yr 1.2, but dips below zero at yr 3 due to battery replacement; persistent break-even waits until yr 3.5
elements.cashFlowBasis.value='project';
run(`current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=0;current.result.finance.real_discount_rate=0;current.result.finance.nominal_discount_rate=0;current.result.finance.pv_incremental={};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,60,60,-50,60]];current.result.annual_cashflows=[{year:0},{year:1},{year:2},{year:3},{year:4}];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/Cash break-even · year 3\.5/,'persistent crossing waits until after replacement dip');
assert.doesNotMatch(elements.cashChart.innerHTML,/Cash break-even · year 1\./,'early temporary zero crossing before replacement is rejected');

// 2. No persistent crossing: honestly omit point/marker/callout and explain in takeaway
run(`current.result.finance.cashflows_by_weather.pv_incremental=[[-100,-20,-30,-40]];current.result.annual_cashflows=[{year:0},{year:1},{year:2},{year:3}];renderCashChart()`);
assert.doesNotMatch(elements.cashChart.innerHTML,/class="cash-breakeven-callout"/,'no callout point when persistent crossing is never reached');
assert.match(elements.cashChart.innerHTML,/<p class="cash-chart-explanation">The historical-weather ensemble mean cash balance does not stay at or above €0 within these 3 years\. Discounted recovery above also accounts for the time value of money\. Band: historical scenario min–max; each weather profile repeats throughout the project horizon\.<\/p>/,'honest explanation when break-even is not reached');

// 2b. Grammatical singular when horizon is 1 year
run(`current.result.finance.cashflows_by_weather.pv_incremental=[[-100,-20]];current.result.annual_cashflows=[{year:0},{year:1}];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/<p class="cash-chart-explanation">The historical-weather ensemble mean cash balance does not stay at or above €0 within this 1 year\. Discounted recovery above also accounts for the time value of money\. Band: historical scenario min–max; each weather profile repeats throughout the project horizon\.<\/p>/,'singular phrasing when horizon is 1 year and crossing not reached');

// 3. Zero-start distinctly: when cash starts and stays non-negative from year 0, no claim of investment recovery
run(`current.result.finance.cashflows_by_weather.pv_incremental=[[0,50,50,50]];current.result.annual_cashflows=[{year:0},{year:1},{year:2},{year:3}];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/<text[^>]*>Non-negative from year 0<\/text>/,'concise visible zero-start label');
const zeroBox=elements.cashChart.innerHTML.match(/<rect x="([\d.]+)" y="[\d.]+" width="(\d+)" height="22"/);
const zeroText=elements.cashChart.innerHTML.match(/<text x="([\d.]+)" y="[\d.]+"[^>]*>Non-negative from year 0<\/text>/);
assert.ok(zeroBox&&zeroText,'zero-start pill and text rendered');
assert.ok(Number(zeroText[1])-Number(zeroBox[1])>=20&&Number(zeroBox[2])>=180,'zero-start pill reserves room for marker, text and padding');
run("setSummaryCurveBasis('discounted');renderCashChart()");
assert.match(elements.cashChart.innerHTML,/<text[^>]*>Non-negative from year 0<\/text>/,'discounted zero-start label is concise');
assert.match(elements.cashChart.innerHTML,/aria-label="Discounted NPV stays non-negative from year 0 through year 3"/,'full discounted explanation is retained');
run("setSummaryCurveBasis('undiscounted');renderCashChart()");
assert.doesNotMatch(elements.cashChart.innerHTML,/Cash break-even · year 0/,'no investment recovery claim when no initial cash outlay was spent');
assert.match(elements.cashChart.innerHTML,/aria-label="Cash stays non-negative from year 0 through year 3; no initial cash spent"/,'accessible title clarifies zero-start');
assert.match(elements.cashChart.innerHTML,/<p class="cash-chart-explanation">The historical-weather ensemble mean cash balance stays non-negative from year 0 through year 3\. Discounted recovery above also accounts for the time value of money\. Band: historical scenario min–max; each weather profile repeats throughout the project horizon\.<\/p>/,'zero-start explanation outside plot');

// 4. Actual crossing year is distinct from discounted financial payback card
run(`current.result.settings.cashflow_mode='nominal';current.result.settings.inflation=0;current.result.finance.real_discount_rate=.1;current.result.finance.nominal_discount_rate=.1;current.result.finance.pv_incremental={discounted_payback:{median:null,recovered_weather_years:0,weather_count:1}};current.result.finance.cashflows_by_weather.pv_incremental=[[-100,55,55]];current.result.annual_cashflows=[{year:0},{year:1},{year:2}];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/Cash break-even · year 1\.8/,'chart annotation reports undiscounted median crossing (year 1.8)');
assert.match(elements.cashInsights.innerHTML,/discounted per-weather payback min–max Not reached/,'discounted card independently reflects time-value discounting');
assert.match(elements.cashChart.innerHTML,/discounted recovery above also accounts for the time value of money/i,'callout explanation relates undiscounted cash to discounted recovery card');

// 5. Clamping bounds check: crossing at horizon (year 2 of 2) clamps box within chart area
run(`current.result.finance.cashflows_by_weather.pv_incremental=[[-100,50,50]];current.result.annual_cashflows=[{year:0},{year:1},{year:2}];renderCashChart()`);
assert.match(elements.cashChart.innerHTML,/Cash break-even · year 2\.0/);
const clampedBoxMatch=elements.cashChart.innerHTML.match(/<rect x="([\d.]+)" y="[\d.]+" width="(\d+)" height="22"/);
assert.ok(clampedBoxMatch&&Number(clampedBoxMatch[1])+Number(clampedBoxMatch[2])<=936-4,'callout box is clamped within chart right edge');

// Browser measurement reserves only the marker inset and right padding.
run(`globalThis.OffscreenCanvas=class { getContext(){return {font:'',measureText:()=>({width:120.4})};} };renderCashChart()`);
const measuredBox=elements.cashChart.innerHTML.match(/<rect x="[\d.]+" y="[\d.]+" width="(\d+)" height="22"/);
assert.ok(measuredBox&&Number(measuredBox[1])===149,'measured text has 20px marker inset and 8px right padding');
assert.match(elements.cashChart.innerHTML,/font-family="system-ui"/,'measurement and rendered text use the same font');
run('delete globalThis.OffscreenCanvas;renderCashChart()');

// --- Narrow review test additions ---
// 1. Inverter-only replacement
run(`current.result.settings = { cashflow_mode: 'real', inflation: .02, real_discount_rate: .04, load_scenario: 'baseline', inverter_replace_year: 2, battery_replace_year: 0, battery_kwh: 0 };
current.result.annual_cashflows = [
  { year: 0 },
  { year: 1, avoided_import: 500, net_export: 100, cer: 0, tax_deduction: 0, maintenance: 0, debt_service: 0, replacement: 0 },
  { year: 2, avoided_import: 200, net_export: 50, cer: 0, tax_deduction: 0, maintenance: 0, debt_service: 0, replacement: 800 },
  { year: 3, avoided_import: 500, net_export: 100, cer: 0, tax_deduction: 0, maintenance: 0, debt_service: 0, replacement: 0 }
];
current.result.finance.cashflows_by_weather.pv_incremental = [[-1000, 600, -550, 600]];
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);
assert.match(elements.cashInsights.innerHTML, /<span>Annual cash<\/span>/);
assert.match(elements.cashInsights.innerHTML, /<small>Year 1 vs Years 2–3<\/small>/);
assert.match(elements.cashChart.innerHTML, /Year 2: Inverter replacement [−-]€769/);
assert.match(elements.cashChart.innerHTML, /aria-label="Year 2: Inverter replacement [−-]€769"/);
assert.match(elements.cashChart.innerHTML, /x="86" y="264.0" text-anchor="start"[^>]*>Initial outlay: [−-]€1,000<\/text>/, 'initial outlay label sits below the plot and inside the horizontal axis bounds');

// 2. Zero downpayment
run(`current.result.finance.loan_principal_eur = 12000;
current.result.finance.solar_loan_principal_eur = 12000;
current.result.finance.capex_eur = 12000;
current.result.finance.cashflows_by_weather.solar_equity = [[0, 200, 200, 200]];
renderCashChart(current.result, 'cashChart', 'solar_equity');`);
assert.match(elements.cashFlowBasisNote.textContent, /€0 down payment; loan €12,000 repaid from savings\./);

// 3. Solar view excludes HP replacement
run(`current.result.settings.load_scenario = 'baseline';
current.result.annual_cashflows = [
  { year: 0 },
  { year: 1, avoided_import: 500, net_export: 100, cer: 0, tax_deduction: 0, maintenance: 0, debt_service: 0, replacement: 0, hp_replacement: 0 },
  { year: 2, avoided_import: 500, net_export: 100, cer: 0, tax_deduction: 0, maintenance: 0, debt_service: 0, replacement: 0, hp_replacement: 2500 },
  { year: 3, avoided_import: 500, net_export: 100, cer: 0, tax_deduction: 0, maintenance: 0, debt_service: 0, replacement: 0, hp_replacement: 0 }
];
current.result.finance.cashflows_by_weather.pv_incremental = [[-1000, 600, 600, 600]];
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);
assert.doesNotMatch(elements.cashChart.innerHTML, /Heat pump replacement/);

// 4. No negative annual dip
run(`current.result.finance.cashflows_by_weather.pv_incremental = [[-1000, 600, 600, 600]];
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);
assert.match(elements.cashInsights.innerHTML, /<span>Annual cash<\/span>/);
assert.match(elements.cashInsights.innerHTML, /<small>Year 1 vs Years 2–3<\/small>/);
assert.doesNotMatch(elements.cashInsights.innerHTML, /Worst dip/);

// --- Curve basis: Undiscounted cash | Discounted (NPV) & Replacements verification tests ---

// 1. Default curve basis is undiscounted
assert.equal(run('summaryCurveBasis'), 'undiscounted');
assert.ok(elements.curveUndiscountedBtn.classList.contains('active'), 'undiscounted button active by default');
assert.ok(!elements.curveDiscountedBtn.classList.contains('active'), 'discounted button inactive by default');

// 2. Switch to Discounted (NPV) mode
run(`current.result.finance.real_discount_rate = .04; current.result.settings.real_discount_rate = .04; setSummaryCurveBasis('discounted')`);
assert.equal(run('summaryCurveBasis'), 'discounted');
assert.ok(!elements.curveUndiscountedBtn.classList.contains('active'), 'undiscounted button inactive after switch');
assert.ok(elements.curveDiscountedBtn.classList.contains('active'), 'discounted button active after switch');
assert.match(elements.cashFlowDescription.textContent, /Cumulative discounted cash impact \(NPV\)/);
assert.match(elements.cashFlowDescription.textContent, /constant purchasing-power euros \(4\.0% real discount rate\)/);
assert.equal(elements.cashLegendLine.textContent, 'Cumulative discounted impact (NPV)');
assert.equal(elements.cashLegendAnnual.textContent, 'Discounted annual cash flow');
assert.match(elements.cashChart.innerHTML, /Cumulative discounted cash impact \(NPV\) · discounted at 4\.0% real/);
assert.match(elements.cashChart.innerHTML, /Historical-weather ensemble mean discounted annual net cash · years 1–3/);
assert.match(elements.cashChart.innerHTML, /Initial outlay: [−-]€1,000/, 'Year 0 outlay is unchanged in discounted mode');
assert.match(elements.cashChart.innerHTML, /Discounted annual contribution \(4\.0% real rate\) · historical-weather ensemble mean €566/);
assert.match(elements.cashChart.innerHTML, /Cumulative discounted impact \(NPV\) · historical-weather ensemble mean −€434/);
assert.match(elements.cashInsights.innerHTML, /Historical-weather ensemble mean 3-y discounted NPV/);
assert.match(elements.cashInsights.innerHTML, /Discounted payback/);

// 3. Preserve loan tab chart behavior even when summaryCurveBasis is discounted
run(`renderCashChart(current.result, 'loanCashChart', 'equity')`);
assert.match(elements.loanCashChart.innerHTML, /Cumulative real cash impact/, 'loan chart retains real cumulative cash, not discounted curve');
assert.match(elements.loanCashChart.innerHTML, /Taking loan \(ensemble mean NPV\)/, 'loan comparison line preserved');
assert.match(elements.loanCashChart.innerHTML, /Paying cash \(ensemble mean NPV\)/, 'paying cash comparison line preserved');

// 4. Year 15 replacements tooltip and callout with exact engine fields
const testInfl = 0.02;
const testRealDisc = 0.04;
const y15Deflator = Math.pow(1 + testInfl, 15);
const y15Discountor = Math.pow(1 + testRealDisc, 15);
const invNominal = 1200 * Math.pow(1 + testInfl, 14);
const batNominal = 250 * 10 * Math.pow(1 + testInfl, 14);
const repNominal = invNominal + batNominal;
const invReal = invNominal / y15Deflator;
const batReal = batNominal / y15Deflator;
const repReal = repNominal / y15Deflator;

run(`current.result.settings = {
  cashflow_mode: 'real',
  inflation: ${testInfl},
  real_discount_rate: ${testRealDisc},
  load_scenario: 'baseline',
  inverter_replace_year: 15,
  battery_replace_year: 15,
  battery_kwh: 10,
  inverter_replacement_cost: 1200,
  battery_replacement_cost_kwh: 250,
  horizon_years: 15
};
current.result.annual_cashflows = Array.from({length: 16}, (_, yr) => ({
  year: yr,
  replacement: yr === 15 ? ${repNominal} : 0,
  inverter_replacement: yr === 15 ? ${invNominal} : 0,
  battery_replacement: yr === 15 ? ${batNominal} : 0,
  display_replacement: yr === 15 ? ${repReal} : 0,
  display_inverter_replacement: yr === 15 ? ${invReal} : 0,
  display_battery_replacement: yr === 15 ? ${batReal} : 0,
  avoided_import: yr > 0 ? 800 : 0,
  net_export: yr > 0 ? 100 : 0,
  cer: 0,
  tax_deduction: 0,
  maintenance: 0,
  debt_service: 0
}));
current.result.finance.cashflows_by_weather.pv_incremental = [
  [-1000, ...Array(14).fill(900), -3600]
];
setSummaryCurveBasis('undiscounted');
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);

// In undiscounted mode, verify sublabel and tooltip display exact deflated amounts without guessing
assert.match(elements.cashChart.innerHTML, /Year 15: Inverter [−-]€[\d,]+ · Battery [−-]€[\d,]+/);
assert.match(elements.cashChart.innerHTML, /aria-label="Year 15: Inverter replacement [−-]€[\d,]+, battery replacement\/augmentation [−-]€[\d,]+ \(total [−-]€[\d,]+\)"/);
assert.match(elements.cashChart.innerHTML, /Inverter replacement: [−-]€[\d,]+/);
assert.match(elements.cashChart.innerHTML, /Battery replacement \/ augmentation: [−-]€[\d,]+/);

// In discounted mode, verify amounts are discounted uniformly
run(`setSummaryCurveBasis('discounted')`);
assert.match(elements.cashChart.innerHTML, /Year 15: Inverter [−-]€[\d,]+ · Battery [−-]€[\d,]+/);
assert.match(elements.cashChart.innerHTML, /aria-label="Year 15: Inverter replacement [−-]€[\d,]+, battery replacement\/augmentation [−-]€[\d,]+ \(total [−-]€[\d,]+\)"/);

// Reset curve basis back to undiscounted
run(`setSummaryCurveBasis('undiscounted')`);
assert.equal(run('summaryCurveBasis'), 'undiscounted');

// --- Years 1–10 vs 11–20 annual cash card regression tests ---
const weatherFlows = [
  [-10000, ...Array(10).fill(800), ...Array(4).fill(1200), -3000, ...Array(5).fill(1200)],
  [-10000, ...Array(10).fill(800), ...Array(4).fill(1200), -3000, ...Array(5).fill(1200)]
];
const twentyYearCashRows = Array.from({length: 21}, (_, yr) => ({
  year: yr,
  avoided_import: yr > 0 ? (yr <= 10 ? 800 : 1200) : 0,
  net_export: 0,
  cer: 0,
  tax_deduction: 0,
  maintenance: 0,
  debt_service: 0,
  replacement: yr === 15 ? 3000 : 0
}));
run(`current.result.settings = {
  cashflow_mode: 'real',
  inflation: 0,
  real_discount_rate: 0.03,
  load_scenario: 'baseline',
  horizon_years: 20
};
current.result.finance = {
  real_discount_rate: 0.03,
  nominal_discount_rate: 0.03,
  loan_principal_eur: 0,
  loan_scope: 'solar',
  pv_incremental: {},
  cashflows_by_weather: {
    pv_incremental: ${JSON.stringify(weatherFlows)}
  }
};
current.result.annual_cashflows = ${JSON.stringify(twentyYearCashRows)};
setSummaryCurveBasis('undiscounted');
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);

// Undiscounted project cash flows: Years 1-10 avg (+€800/yr) and Years 11-20 avg (+€780/yr, including Yr 15 replacement)
assert.match(elements.cashInsights.innerHTML, /<span>Annual cash<\/span>/);
assert.match(elements.cashInsights.innerHTML, /<strong>\+€800 \/ yr<span> vs <\/span>\+€780 \/ yr<\/strong>/);
assert.match(elements.cashInsights.innerHTML, /<small>Years 1–10 vs Years 11–20<\/small>/);
assert.doesNotMatch(elements.cashInsights.innerHTML, /<small>[^<]*\+€780/);
assert.match(elements.cashInsights.innerHTML, /Mean annual cash flow across Years 1–10 \(\+€800 \/ yr\) and Years 11–20 \(\+€780 \/ yr\)/);

// Verify Year 15 replacement is included in second half window (+€780 instead of old excluded +€1,200)
assert.match(elements.cashInsights.innerHTML, /\+€780/);

// Discounted output at 3%: independent formula verification (Y1-10 avg ~682.42 -> +€682, Y11-20 replacement-inclusive avg ~492.09 -> +€492)
const expDiscY1to10 = Math.round(800 * (1 - Math.pow(1.03, -10)) / 0.03 / 10);
assert.equal(expDiscY1to10, 682);
const expDiscY11to20 = Math.round((1200 * (Math.pow(1.03, -10) - Math.pow(1.03, -14)) / 0.03 - 3000 * Math.pow(1.03, -15) + 1200 * (Math.pow(1.03, -15) - Math.pow(1.03, -20)) / 0.03) / 10);
assert.equal(expDiscY11to20, 492);

run(`setSummaryCurveBasis('discounted');
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);
assert.match(elements.cashInsights.innerHTML, /<span>Discounted annual cash<\/span>/);
assert.match(elements.cashInsights.innerHTML, /<strong>\+€682 \/ yr<span> vs <\/span>\+€492 \/ yr<\/strong>/);
assert.match(elements.cashInsights.innerHTML, /<small>Years 1–10 vs Years 11–20<\/small>/);
assert.doesNotMatch(elements.cashInsights.innerHTML, /<small>[^<]*\+€492/);
assert.match(elements.cashInsights.innerHTML, /Mean annual cash flow across Years 1–10 \(\+€682 \/ yr\) and Years 11–20 \(\+€492 \/ yr\)/);

// Loan payoff scenario: verify loan wording variations in headlines and captions
run(`current.result.settings.loan_years = 10;
current.result.finance.loan_principal_eur = 5000;
current.result.finance.solar_loan_principal_eur = 5000;
current.result.finance.cashflows_by_weather.solar_equity = ${JSON.stringify(weatherFlows)};
setSummaryCurveBasis('undiscounted');
renderCashChart(current.result, 'cashChart', 'solar_equity');`);
assert.match(elements.cashInsights.innerHTML, /<strong>\+€800 \/ yr<span> vs <\/span>\+€780 \/ yr<\/strong>/);
assert.match(elements.cashInsights.innerHTML, /<small>Years 1–10 vs Years 11–20 · after loan payoff<\/small>/);

run(`setSummaryCurveBasis('discounted');
renderCashChart(current.result, 'cashChart', 'solar_equity');`);
assert.match(elements.cashInsights.innerHTML, /<strong>\+€677 \/ yr<span> vs <\/span>\+€492 \/ yr<\/strong>/);
assert.match(elements.cashInsights.innerHTML, /<small>Years 1–10 vs Years 11–20 · after loan payoff<\/small>/);

// --- Non-20 horizon regression tests: 10-year horizon with replacement in Year 8 ---
const tenYearFlows = [
  [-4000, 500, 500, 500, 500, 500, 1000, 1000, -1000, 1000, 1000],
  [-4000, 500, 500, 500, 500, 500, 1000, 1000, -1000, 1000, 1000]
];
const tenYearCashRows = Array.from({length: 11}, (_, yr) => ({
  year: yr,
  avoided_import: yr > 0 ? (yr <= 5 ? 500 : 1000) : 0,
  net_export: 0,
  cer: 0,
  tax_deduction: 0,
  maintenance: 0,
  debt_service: 0,
  replacement: yr === 8 ? 2000 : 0
}));
run(`current.result.settings = {
  cashflow_mode: 'real',
  inflation: 0,
  real_discount_rate: 0.04,
  load_scenario: 'baseline',
  horizon_years: 10
};
current.result.finance = {
  real_discount_rate: 0.04,
  nominal_discount_rate: 0.04,
  loan_principal_eur: 0,
  loan_scope: 'solar',
  pv_incremental: {},
  cashflows_by_weather: {
    pv_incremental: ${JSON.stringify(tenYearFlows)}
  }
};
current.result.annual_cashflows = ${JSON.stringify(tenYearCashRows)};
setSummaryCurveBasis('undiscounted');
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);

// 10-year horizon splits into Years 1–5 and Years 6–10; Y6-10 includes Year 8 replacement: (1000+1000-1000+1000+1000)/5 = +600
assert.match(elements.cashInsights.innerHTML, /<span>Annual cash<\/span>/);
assert.match(elements.cashInsights.innerHTML, /<strong>\+€500 \/ yr<span> vs <\/span>\+€600 \/ yr<\/strong>/);
assert.match(elements.cashInsights.innerHTML, /<small>Years 1–5 vs Years 6–10<\/small>/);
assert.doesNotMatch(elements.cashInsights.innerHTML, /<small>[^<]*\+€600/);
assert.match(elements.cashInsights.innerHTML, /Mean annual cash flow across Years 1–5 \(\+€500 \/ yr\) and Years 6–10 \(\+€600 \/ yr\)/);

// Discounted comparison on 10-year horizon
run(`setSummaryCurveBasis('discounted');
renderCashChart(current.result, 'cashChart', 'pv_incremental');`);
assert.match(elements.cashInsights.innerHTML, /<span>Discounted annual cash<\/span>/);
assert.match(elements.cashInsights.innerHTML, /<small>Years 1–5 vs Years 6–10<\/small>/);
assert.doesNotMatch(elements.cashInsights.innerHTML, /excl\./);

// Reset curve basis back to undiscounted
run(`setSummaryCurveBasis('undiscounted')`);
assert.equal(run('summaryCurveBasis'), 'undiscounted');

// Curve basis persistence and reload regression tests
const makeCurveTestCtx=(storage=null)=>{
 const btnMap={
  curveUndiscountedBtn:makeToggleBtn(),curveDiscountedBtn:makeToggleBtn(),
  loanCurveUndiscounted:makeToggleBtn(),loanCurveDiscounted:makeToggleBtn()
 };
 const storageObj=storage&&typeof storage.getItem==='function'?storage:(storage===null?undefined:{
  getItem(k){return Object.prototype.hasOwnProperty.call(storage,k)?storage[k]:null;},
  setItem(k,v){storage[k]=String(v);},
  removeItem(k){delete storage[k];}
 });
 const ctx=vm.createContext({
  localStorage:storageObj,
  document:{getElementById:id=>btnMap[id]||elements[id]||null}
 });
 vm.runInContext(source.slice(0,source.indexOf('function schedule(')),ctx);
 return {run:code=>vm.runInContext(code,ctx),buttons:btnMap};
};

// Summary discounted survives reload with aria-pressed and active state
const sharedCurveStore={};
const curveCtx1=makeCurveTestCtx(sharedCurveStore);
assert.equal(curveCtx1.run('summaryCurveBasis'),'undiscounted');
assert.equal(curveCtx1.run('loanCurveBasis'),'discounted');
curveCtx1.run("setSummaryCurveBasis('discounted')");
assert.equal(curveCtx1.run('summaryCurveBasis'),'discounted');
assert.equal(curveCtx1.buttons.curveDiscountedBtn.classList.contains('active'),true);
assert.equal(curveCtx1.buttons.curveDiscountedBtn.getAttribute('aria-pressed'),'true');
assert.equal(curveCtx1.buttons.curveUndiscountedBtn.classList.contains('active'),false);
assert.equal(curveCtx1.buttons.curveUndiscountedBtn.getAttribute('aria-pressed'),'false');

const curveCtx2=makeCurveTestCtx(sharedCurveStore);
assert.equal(curveCtx2.run('summaryCurveBasis'),'discounted');
curveCtx2.run('syncCurveBasisControls()');
assert.equal(curveCtx2.buttons.curveDiscountedBtn.classList.contains('active'),true);
assert.equal(curveCtx2.buttons.curveDiscountedBtn.getAttribute('aria-pressed'),'true');
assert.equal(curveCtx2.buttons.curveUndiscountedBtn.classList.contains('active'),false);
assert.equal(curveCtx2.buttons.curveUndiscountedBtn.getAttribute('aria-pressed'),'false');

// Financing undiscounted independent and survives reload
curveCtx2.run("setLoanCurveBasis('undiscounted')");
assert.equal(curveCtx2.run('loanCurveBasis'),'undiscounted');
assert.equal(curveCtx2.run('summaryCurveBasis'),'discounted');
assert.equal(curveCtx2.buttons.loanCurveUndiscounted.classList.contains('active'),true);
assert.equal(curveCtx2.buttons.loanCurveUndiscounted.getAttribute('aria-pressed'),'true');

const curveCtx3=makeCurveTestCtx(sharedCurveStore);
assert.equal(curveCtx3.run('summaryCurveBasis'),'discounted');
assert.equal(curveCtx3.run('loanCurveBasis'),'undiscounted');
curveCtx3.run('syncCurveBasisControls()');
curveCtx3.run('syncLoanCurveBasisControls()');
assert.equal(curveCtx3.buttons.curveDiscountedBtn.classList.contains('active'),true);
assert.equal(curveCtx3.buttons.loanCurveUndiscounted.classList.contains('active'),true);

// Changing summary scope/cash preferences does not erase curve prefs
curveCtx3.run("if(typeof persistSummaryPrefs==='function')persistSummaryPrefs('package','loan')");
assert.ok(sharedCurveStore['pv-lab-summary-prefs-v1']);
const parsedCurve=JSON.parse(sharedCurveStore['pv-lab-cash-curve-prefs-v1']);
assert.equal(parsedCurve.summary,'discounted');
assert.equal(parsedCurve.loan,'undiscounted');
const curveCtx4=makeCurveTestCtx(sharedCurveStore);
assert.equal(curveCtx4.run('summaryCurveBasis'),'discounted');
assert.equal(curveCtx4.run('loanCurveBasis'),'undiscounted');

// Absent key defaults
const curveCtxEmpty=makeCurveTestCtx({});
assert.equal(curveCtxEmpty.run('summaryCurveBasis'),'undiscounted');
assert.equal(curveCtxEmpty.run('loanCurveBasis'),'discounted');

// Malformed JSON / array / invalid values / absent / blocked storage safely default
assert.equal(makeCurveTestCtx({'pv-lab-cash-curve-prefs-v1':'{broken'}).run('summaryCurveBasis'),'undiscounted');
assert.equal(makeCurveTestCtx({'pv-lab-cash-curve-prefs-v1':'{broken'}).run('loanCurveBasis'),'discounted');
assert.equal(makeCurveTestCtx({'pv-lab-cash-curve-prefs-v1':JSON.stringify(['discounted'])}).run('summaryCurveBasis'),'undiscounted');
assert.equal(makeCurveTestCtx({'pv-lab-cash-curve-prefs-v1':JSON.stringify({summary:'bad',loan:'unknown'})}).run('summaryCurveBasis'),'undiscounted');
assert.equal(makeCurveTestCtx({'pv-lab-cash-curve-prefs-v1':JSON.stringify({summary:'bad',loan:'unknown'})}).run('loanCurveBasis'),'discounted');
assert.equal(makeCurveTestCtx(null).run('summaryCurveBasis'),'undiscounted');
assert.equal(makeCurveTestCtx(null).run('loanCurveBasis'),'discounted');

const blockedGetCtx=makeCurveTestCtx({getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}});
assert.equal(blockedGetCtx.run('summaryCurveBasis'),'undiscounted');
assert.equal(blockedGetCtx.run('loanCurveBasis'),'discounted');

// Setter with blocked storage does not throw
const blockedSetCtx=makeCurveTestCtx({getItem(){return null;},setItem(){throw new Error('blocked');}});
assert.doesNotThrow(()=>blockedSetCtx.run("setSummaryCurveBasis('discounted')"));
assert.equal(blockedSetCtx.run('summaryCurveBasis'),'discounted');
assert.doesNotThrow(()=>blockedSetCtx.run("setLoanCurveBasis('undiscounted')"));
assert.equal(blockedSetCtx.run('loanCurveBasis'),'undiscounted');

console.log('Cashflow chart reuse, real/nominal loan comparison and cash recovery UI checks passed.');
