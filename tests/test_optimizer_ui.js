/* Optimizer UI request and stale-result regressions. Run with Node. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../feasibility/web/app.js'),'utf8');
const fmtStart=source.indexOf('function formatBatteryComposition');
const fmtEnd=source.indexOf('function batterySummaryText',fmtStart);
const fmtCode=fmtStart>=0&&fmtEnd>fmtStart?source.slice(fmtStart,fmtEnd):'';
const segment=source.slice(source.indexOf('function optimizerEquipment()'),source.indexOf('async function pollJob()'));
const ids=['optPanelWatts','optModuleKwh','optBatteryMin','optBatteryMax','optInverter','optCharge','optDischarge','filterLoad','optObjective','maxSouth','maxWest','maxEast','maxDormer','maxFlat','maxSecondarySouth','includeTheoretical','chartX','chartY','bestConfiguration','candidates','paretoChart','paretoMethod','recomputePareto','error'];
function makeContext(){
 const elements=Object.fromEntries(ids.map(id=>[id,{value:'',checked:false,type:id==='includeTheoretical'?'checkbox':'number',innerHTML:'',textContent:'',disabled:false,setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];}}]));
 Object.assign(elements,{optPanelWatts:{...elements.optPanelWatts,value:'410'},optModuleKwh:{...elements.optModuleKwh,value:'5.12'},optBatteryMin:{...elements.optBatteryMin,value:'0'},optBatteryMax:{...elements.optBatteryMax,value:'4'},optInverter:{...elements.optInverter,value:'6'},optCharge:{...elements.optCharge,value:'4'},optDischarge:{...elements.optDischarge,value:'5'},filterLoad:{...elements.filterLoad,value:'ev'},optObjective:{...elements.optObjective,value:'autarky'},maxSouth:{...elements.maxSouth,value:'22'},maxWest:{...elements.maxWest,value:'11'},maxEast:{...elements.maxEast,value:'6'},maxDormer:{...elements.maxDormer,value:'4'},maxFlat:{...elements.maxFlat,value:'8'},maxSecondarySouth:{...elements.maxSecondarySouth,value:'5'},includeTheoretical:{...elements.includeTheoretical,checked:true}});
 const requests=[],store={};let resolveRequest;
 const context=vm.createContext({
  $:id=>elements[id],document:{activeElement:null},localStorage:{getItem:key=>store[key]??null,setItem:(key,value)=>store[key]=value},
  panelKeys:['south_panels','west_panels','east_panels','dormer_panels','flat_panels','secondary_south_panels'],
  settings:{load_scenario:'baseline',south_panels:18,west_panels:8,east_panels:3,dormer_panels:2,flat_panels:2,secondary_south_panels:1,battery_modules:3,battery_kwh:15.36,battery_module_kwh:5.12,panel_watts:400,inverter_kw:5,charge_kw:3,discharge_kw:3,horizon_years:20},quote:{line_items:[]},schema:{defaults:{},records:[]},optimizerDefaults:{objective:'autarky',battery_min_modules:0,battery_max_modules:4,panel_maxima:{south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5},include_theoretical:true,load_scenario:'electrified',chart_x:'autarky',chart_y:'npv_eur'},
  optimizerPrefs:{objective:'autarky',battery_min_modules:0,battery_max_modules:4,panel_maxima:{south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5},include_theoretical:true,load_scenario:'electrified',_loadScenarioExplicit:true,chart_x:'autarky',chart_y:'npv_eur'},optimizerResult:null,optimizerFingerprint:null,optimizerRequestId:0,
  inputFingerprint:(s,q)=>JSON.stringify(s),showError:e=>{elements.error.textContent=e.message;},title:s=>String(s),nf:n=>String(n),fmt:n=>String(n),range:r=>JSON.stringify(r),esc:s=>String(s),
  api:async(path,body)=>{requests.push({path,body});return new Promise(resolve=>{resolveRequest=resolve;});},
 });
 if(fmtCode)vm.runInContext(fmtCode,context);
 vm.runInContext(segment,context);
 const actualRenderCandidates=context.renderCandidates;context.renderCandidates=()=>{};
 return {context,elements,requests,get resolveRequest(){return resolveRequest;},store,actualRenderCandidates};
}
(async()=>{
 for(const [saved,expected] of [[false,'baseline'],[true,'ev']]){
  const init=makeContext(),i=init.context;i.schema={defaults:{},records:[]};i.settings.load_scenario='baseline';i.settings.panel_watts=400;i.settings.battery_module_kwh=5.12;i.settings.inverter_kw=5;i.settings.charge_kw=3;i.settings.discharge_kw=3;i.optimizerPrefs._loadScenarioExplicit=saved;i.optimizerPrefs.load_scenario='ev';i.initializeOptimizer();
  assert.equal(init.elements.filterLoad.value,expected,saved?'an explicitly saved household is retained':'a new optimizer follows the selected shared household');
 }
 const t=makeContext(),c=t.context;
 const requestPromise=vm.runInContext('loadCandidates()',c);
 while(!t.resolveRequest)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(t.requests[0].path,'/api/optimize-configurations');
 const body=t.requests[0].body;
 assert.equal(body.search.objective,'autarky','autarky is the initial search objective');
 assert.equal(body.search.load_scenario,undefined,'household choice is sent with settings, not the search schema');
 assert.equal(body.settings.load_scenario,'ev','the explicitly selected household is used by optimization');
 assert.equal(body.settings.panel_watts,410,'the requested panel wattage overrides installed equipment');
 assert.equal(body.settings.battery_module_kwh,5.12,'the requested module capacity is sent explicitly');
 assert.equal(body.settings.battery_modules,0,'installed battery count cannot constrain the search');
 assert.equal(body.settings.battery_kwh,0,'installed total battery cannot constrain the search');
 assert.deepEqual(['south_panels','west_panels','east_panels','dormer_panels','flat_panels','secondary_south_panels'].map(k=>body.settings[k]),[0,0,0,0,0,0],'installed roof panel counts are cleared before search');
 assert.deepEqual(Object.keys(body.search).sort(),['battery_max_modules','battery_min_modules','include_theoretical','objective','panel_maxima'].sort());
 t.resolveRequest({best_candidate:{settings:{south_panels:10,battery_modules:2,battery_module_kwh:5.12,battery_kwh:10.24}},recommended_id:'x',candidates:[],search:{exact:true,evaluated_count:1}});
 await requestPromise;
 assert.ok(c.optimizerResult,'successful response becomes the current result');
 c.optimizerResult={};c.optimizerFingerprint=c.optimizerStateFingerprint();
 c.optimizerPrefs.objective='npv_eur';c.$('optObjective').value='npv_eur';c.optimizerChanged();
 assert.equal(c.optimizerResult,null,'changing the objective invalidates prior search results');
 assert.match(t.store['pv-lab-optimizer-v1'],/npv_eur/,'objective preference is saved locally');

 const stale=makeContext(),s=stale.context;
 const pending=vm.runInContext('loadCandidates()',s);
 while(!stale.resolveRequest)await new Promise(resolve=>setImmediate(resolve));
 s.$('optObjective').value='self_consumption';s.optimizerChanged();
 stale.resolveRequest({best_candidate:{settings:{south_panels:1}},candidates:[],search:{exact:true,evaluated_count:1}});
 await pending;
 assert.equal(s.optimizerResult,null,'a response for an outdated objective is discarded');

 const bad=makeContext(),b=bad.context;b.$('optBatteryMin').value='5';b.$('optBatteryMax').value='4';
 await vm.runInContext('loadCandidates()',b);
 assert.equal(bad.requests.length,0,'invalid module ranges are rejected before the request');
 assert.match(bad.elements.paretoMethod.textContent,/cannot exceed/);

 const applied=makeContext(),a=applied.context;let recomputed=false;a.optimizerResult={};a.optimizerFingerprint=a.optimizerStateFingerprint();a.normalizeBatterySettings=target=>{target.battery_kwh=target.battery_modules*target.battery_module_kwh;};a.clearQuote=()=>{a.quote={line_items:[]};};a.renderLines=()=>{};a.syncQuoteFields=()=>{};a.renderControls=()=>{};a.renderQuickConfig=()=>{};a.recompute=()=>{recomputed=true;};a.switchView=()=>{};a.applyOptimizedSettings({settings:{south_panels:12,west_panels:3,battery_modules:2,battery_module_kwh:5.12}});
 assert.equal(a.settings.south_panels,12,'applying the recommendation transfers the selected roof count');assert.equal(a.settings.west_panels,3);assert.equal(a.settings.battery_modules,2,'applying the recommendation transfers the selected module count');assert.equal(a.settings.battery_kwh,10.24);assert.ok(recomputed,'the chosen configuration recalculates the overview');

 const chart=makeContext(),g=chart.context;g.optimizerResult={objective:'autarky',recommended_id:'opt_1',search:{exact:true,evaluated_count:2,search_space_size:2,requested_panel_maxima:{},best_at_panel_maxima:{}},best_candidate:{settings:{south_panels:1,battery_modules:0,battery_module_kwh:5},metrics:{autarky:{min:.2,median:.4,max:.6},npv_eur:{min:10,median:20,max:30}}},candidates:[{id:'opt_1',settings:{south_panels:1,battery_modules:0,battery_module_kwh:5},battery_kwh:0,metrics:{autarky:{min:.2,median:.4,max:.6},npv_eur:{min:10,median:20,max:30},payback_years:{min:2,median:3,max:4},discounted_payback_years:{min:3,median:4,max:5},self_consumption:{min:.5,median:.6,max:.7}},finance:{capex_eur:1000},theoretical:false}]};g.optimizerFingerprint=g.optimizerStateFingerprint();chart.elements.chartX.value='autarky';chart.elements.chartY.value='npv_eur';chart.actualRenderCandidates();const firstChart=chart.elements.paretoChart.innerHTML;chart.elements.chartX.value='panel_count';chart.actualRenderCandidates();const secondChart=chart.elements.paretoChart.innerHTML;assert.notEqual(firstChart,secondChart,'axis changes redraw the plot from the existing result');assert.match(secondChart,/Total panels/,'new x-axis label is shown');

 const migSegment=source.slice(source.indexOf('const defaultRoofLimits='),source.indexOf('function readLoanSimulations()'));
 const listenersSegment=source.slice(source.indexOf('Object.entries(roofLimitInputMap).forEach'),source.indexOf('/* BATTERY SIZING SECTION */'));

 // 1. Absent Configurations DOM elements do not throw during init, clear, render, apply, or event listener registration
 {
  const actualIds=['maxSouth','maxWest','maxEast','maxDormer','maxFlat','maxSecondarySouth','resetRoofLimits','roofLimitsStatus'];
  const elements=Object.fromEntries(actualIds.map(id=>[id,{value:'0',checked:false,innerHTML:'',textContent:'',disabled:false,setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];},addEventListener(){}}]));
  const store={};
  const context=vm.createContext({
   $:id=>elements[id]||null,document:{activeElement:null},localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>store[k]=v},
   panelKeys:['south_panels','west_panels','east_panels','dormer_panels','flat_panels','secondary_south_panels'],
   settings:{south_panels:10,west_panels:5,panel_watts:400,inverter_kw:5,battery_module_kwh:5,battery_modules:0,battery_kwh:0,load_scenario:'baseline'},
   quote:{line_items:[]},schema:{defaults:{panel_watts:400,inverter_kw:5,battery_module_kwh:5},records:[]},
   optimizerDefaults:{objective:'autarky',battery_min_modules:0,battery_max_modules:4,panel_maxima:{south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5},include_theoretical:true,load_scenario:'electrified',chart_x:'autarky',chart_y:'npv_eur'},
   optimizerPrefs:{objective:'autarky',battery_min_modules:0,battery_max_modules:4,panel_maxima:{south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5},include_theoretical:true,load_scenario:'electrified',_loadScenarioExplicit:true,chart_x:'autarky',chart_y:'npv_eur'},
   optimizerResult:null,optimizerFingerprint:null,optimizerRequestId:0,
   inputFingerprint:s=>JSON.stringify(s),showError:()=>{},title:s=>String(s),nf:n=>String(n),fmt:n=>String(n),range:r=>JSON.stringify(r),esc:s=>String(s),
   editSetting:()=>{},normalizeBatterySettings:()=>{},clearQuote:()=>{},renderLines:()=>{},syncQuoteFields:()=>{},renderControls:()=>{},recompute:()=>{},switchView:()=>{}
  });
  vm.runInContext(migSegment,context);
  vm.runInContext(segment,context);
  vm.runInContext(listenersSegment,context);

  context.initializeOptimizer();
  assert.equal(elements.maxSouth.value,22,'initializes roof limits inputs from prefs without error');
  assert.equal(elements.maxWest.value,11);

  context.clearOptimizerResults();

  context.optimizerResult={
   objective:'autarky',search:{exact:true,evaluated_count:5},
   best_candidate:{settings:{south_panels:10,battery_modules:2,battery_module_kwh:5.12},metrics:{autarky:{median:0.8}}},
   candidates:[{id:'opt_1',settings:{south_panels:10,battery_modules:2,battery_module_kwh:5.12},metrics:{autarky:{median:0.8}}}],
   recommended_id:'opt_1'
  };
  context.optimizerFingerprint=context.optimizerStateFingerprint();
  context.renderCandidates();
  context.applyOptimizedSettings(context.optimizerResult.best_candidate);
 }

 // 2. One-time migration resets old overrides to initial estimates while preserving unrelated preferences
 {
  const store={
   'pv-lab-optimizer-v1':JSON.stringify({
    objective:'npv_eur',load_scenario:'electrified',battery_max_modules:3,
    panel_maxima:{south:99,west:88,east:77,dormer:66,flat:55,secondary_south:44}
   })
  };
  const context=vm.createContext({localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>store[k]=v}});
  vm.runInContext(migSegment,context);
  const prefs=context.readOptimizerPrefs();
  assert.deepEqual({...prefs.panel_maxima},{south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5},'migration resets panel maxima to initial estimates');
  assert.equal(prefs.objective,'npv_eur','migration preserves saved objective');
  assert.equal(prefs.battery_max_modules,3,'migration preserves other preferences');
  assert.equal(store['pv-lab-roof-limits-migrated-v1'],'1','migration flag recorded');

  // Subsequent edits survive future reloads
  store['pv-lab-optimizer-v1']=JSON.stringify({
   ...JSON.parse(store['pv-lab-optimizer-v1']),
   panel_maxima:{south:24,west:12,east:6,dormer:4,flat:8,secondary_south:5}
  });
  const contextReloaded=vm.createContext({localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>store[k]=v}});
  vm.runInContext(migSegment,contextReloaded);
  const reloadedPrefs=contextReloaded.readOptimizerPrefs();
  assert.equal(reloadedPrefs.panel_maxima.south,24,'subsequent edit is retained across reloads');
 }

 // 3. Validation, input handling, and reset control
 {
  const initialLimits={maxSouth:'22',maxWest:'11',maxEast:'6',maxDormer:'4',maxFlat:'8',maxSecondarySouth:'5'};
  const actualIds=['maxSouth','maxWest','maxEast','maxDormer','maxFlat','maxSecondarySouth','resetRoofLimits','roofLimitsStatus','roofLimitsComparison'];
  const elements=Object.fromEntries(actualIds.map(id=>[id,{value:initialLimits[id]||'',checked:false,innerHTML:'',textContent:'',disabled:false,setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];},addEventListener(){}}]));
  const store={};
  const context=vm.createContext({
   $:id=>elements[id]||null,document:{activeElement:null},localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>store[k]=v},
   panelKeys:['south_panels','west_panels','east_panels','dormer_panels','flat_panels','secondary_south_panels'],
   settings:{south_panels:18,west_panels:8,east_panels:3,dormer_panels:2,flat_panels:2,secondary_south_panels:1,load_scenario:'electrified'},quote:{},schema:{defaults:{},records:[]},
   inputFingerprint:s=>JSON.stringify(s),showError:()=>{},title:s=>String(s),nf:n=>String(n),fmt:n=>String(n),range:r=>JSON.stringify(r),esc:s=>String(s)
  });
  vm.runInContext(migSegment,context);
  vm.runInContext(segment,context);

  assert.equal(context.validateRoofLimit('-1').valid,false);
  assert.equal(context.validateRoofLimit('3.14').valid,false);
  assert.equal(context.validateRoofLimit('abc').valid,false);
  assert.equal(context.validateRoofLimit('').valid,false);
  assert.equal(context.validateRoofLimit(' 0 ').valid,true);
  assert.equal(context.validateRoofLimit('10').valid,true);
  assert.equal(context.validateRoofLimit('10').value,10);

  elements.maxSouth.value='-5';
  context.handleRoofLimitInput({target:elements.maxSouth},'south');
  assert.equal(elements.maxSouth['aria-invalid'],'true');
  assert.match(elements.roofLimitsStatus.textContent,/Must be a non-negative whole number/);
  assert.equal(vm.runInContext('optimizerPrefs.panel_maxima.south',context),22,'invalid input is not saved');

  elements.maxSouth.value='25';
  context.handleRoofLimitInput({target:elements.maxSouth},'south');
  assert.equal(elements.maxSouth['aria-invalid'],undefined);
  assert.equal(elements.roofLimitsStatus.textContent,'');
  assert.equal(vm.runInContext('optimizerPrefs.panel_maxima.south',context),25);
  const savedStore=JSON.parse(store['pv-lab-optimizer-v1']);
  assert.equal(savedStore.panel_maxima.south,25,'valid edit persisted to localStorage');

  context.resetRoofCapacityLimits();
  assert.deepEqual({...vm.runInContext('optimizerPrefs.panel_maxima',context)},{south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5});
  assert.equal(elements.maxSouth.value,22);
  assert.equal(elements.maxSouth['aria-invalid'],undefined);
  assert.match(elements.roofLimitsStatus.textContent,/reset to estimated initial values/);
  assert.equal(context.settings.south_panels,18,'actual scenario panel counts are unchanged');

  // 4. Comparison view rendering, layout check warnings, and scenario change refreshes
  context.renderRoofLimitsComparison();
  assert.match(elements.roofLimitsComparison.innerHTML,/All selected scenario panel counts are within estimated physical roof limits/);
  assert.match(elements.roofLimitsComparison.innerHTML,/Within limit \(18 of 22\)/);

  // Edits causing warning: lower South limit to 15 (scenario has 18 panels)
  elements.maxSouth.value='15';
  context.handleRoofLimitInput({target:elements.maxSouth},'south');
  assert.match(elements.roofLimitsComparison.innerHTML,/Advisory layout check warning/,'advisory warning banner is rendered');
  assert.match(elements.roofLimitsComparison.innerHTML,/South \(\+3 panels\)/,'exceeded roof is named in summary');
  assert.match(elements.roofLimitsComparison.innerHTML,/role="alert"/,'accessible alert role is present on exceeded item');
  assert.match(elements.roofLimitsComparison.innerHTML,/Exceeds limit by 3 panels \(18 of 15\)/,'accessible text details the panel excess');

  // Scenario change refreshing comparison: scenario increases West to 15 (saved limit is 11)
  context.settings.west_panels=15;
  context.renderRoofLimitsComparison();
  assert.match(elements.roofLimitsComparison.innerHTML,/West \(\+4 panels\)/,'West excess is shown after scenario update');
  assert.match(elements.roofLimitsComparison.innerHTML,/Exceeds limit by 4 panels \(15 of 11\)/);

  // Resetting limits restores South to 22 (18 <= 22), so South is no longer exceeded
  context.resetRoofCapacityLimits();
  assert.match(elements.roofLimitsComparison.innerHTML,/Within limit \(18 of 22\)/,'South returns to within limit upon reset');
  assert.match(elements.roofLimitsComparison.innerHTML,/West \(\+4 panels\)/,'West remains flagged until scenario changes');

  // Restoring West scenario panels to within limit (8 <= 11) clears the warning banner
  context.settings.west_panels=8;
  context.renderRoofLimitsComparison();
  assert.match(elements.roofLimitsComparison.innerHTML,/All selected scenario panel counts are within estimated physical roof limits/,'clears warning once all roofs are compliant');
 }

   // Test: current configuration NPV in optimizer benchmark matches getScopeCashMetrics and hero verdict across all scope and cash basis combinations
  {
    const {context, elements} = makeContext();
    elements.configVsRecContent = {innerHTML: '', textContent: ''};
    elements.systemTitle = {innerHTML: '', textContent: ''};
    elements.systemSubtitle = {innerHTML: '', textContent: ''};
    elements.metrics = {innerHTML: '', textContent: ''};
    elements.summaryScopeNote = {innerHTML: '', textContent: '', style: {}};
    elements.verdictStrip = {scrollIntoView: () => {}};

    const appSource = fs.readFileSync(require('node:path').join(__dirname, '../feasibility/web/app.js'), 'utf8');
    const fmtStart = appSource.indexOf('function fmt(');
    const fmtEnd = appSource.indexOf('function parseNumber(');
    const fmtCode = appSource.slice(fmtStart, fmtEnd);

    const getMetricsStart = appSource.indexOf('function getScopeCashMetrics');
    const getMetricsEnd = appSource.indexOf('function syncSummaryControls');
    const getMetricsCode = appSource.slice(getMetricsStart, getMetricsEnd);

    const renderVerdictStart = appSource.indexOf('function renderVerdictStrip');
    const renderVerdictEnd = appSource.indexOf('function updateAssumptionsBadge');
    const renderVerdictCode = appSource.slice(renderVerdictStart, renderVerdictEnd);

    const renderCfgStart = appSource.indexOf('let candidateEvalCache=new Map();');
    const renderCfgEnd = appSource.indexOf('let summarySensitivityCache=null;');
    const renderCfgCode = appSource.slice(renderCfgStart, renderCfgEnd);

    context.isItemizedPricingActive = () => false;
    context.range = (obj, type) => (obj?.median != null ? `${obj.median}` : `${obj}`);
    context.title = s => s;
    context.esc = s => s;
    context.getMetricDefAnchor = () => null;

    vm.runInContext(fmtCode, context);
    vm.runInContext(getMetricsCode, context);
    vm.runInContext(renderVerdictCode, context);
    vm.runInContext(renderCfgCode, context);

    const testRes = {
      settings: { cashflow_mode: 'real', inflation: 0.02, battery_kwh: 10, inverter_kw: 6 },
      layout: { kwp: 6.0, panel_count: 14 },
      finance: {
        capex_eur: 18000,
        conversion_capex_eur: 3000,
        loan_principal_eur: 21000,
        solar_loan_principal_eur: 18000,
        pv_incremental: {
          npv_eur: { median: 7000 },
          npv_by_weather: [6000, 7000, 8000] // 3 of 3 positive
        },
        whole_package: {
          npv: { median: 9500 },
          npv_by_weather: [-1000, 9500, 11000], // 2 of 3 positive (mixed weather)
          discounted_payback: { median: 10 }
        },
        solar_equity: {
          npv: { median: 6200 },
          npv_by_weather: [-500, -200, 6200], // 1 of 3 positive (mixed weather)
          discounted_payback: { median: 8 }
        },
        package_equity: {
          npv: { median: 8000 },
          // Missing npv_by_weather -> test honest unknown
          discounted_payback: { median: 9 }
        }
      },
      metrics: {
        npv_eur: { median: 7000 },
        package_npv_eur: { median: 9500 },
        discounted_payback_years: { median: 8.5 }
      }
    };

    const mockChoices = {
      financial: {
        panel_count: 16,
        kwp: 6.8,
        battery_kwh: 10,
        capex_eur: 19000,
        initial_solar_equity_eur: 2500,
        roof_counts: {south:16},
        financial_basis: 'after_financing',
        project_npv: { median: 7500 },
        project_discounted_payback_years: 7.9,
        equity_npv: { median: 6700 },
        equity_discounted_payback_years: 7.2,
        package_npv: { median: 10100 },
        package_discounted_payback: { median: 9.2 }
      },
      balanced: {
        panel_count: 14,
        kwp: 6.0,
        battery_kwh: 10,
        capex_eur: 18000,
        initial_solar_equity_eur: 2000,
        roof_counts: {south:14},
        financial_basis: 'after_financing',
        project_npv: { median: 7000 },
        project_discounted_payback_years: 8.5,
        equity_npv: { median: 6200 },
        equity_discounted_payback_years: 8.0,
        package_npv: { median: 9500 },
        package_discounted_payback: { median: 10.0 }
      }
    };
    context.solarData = { recommendation_choices: mockChoices };
    const candidateRequests=[];
    context.api=async(path,payload)=>{
      candidateRequests.push({path,payload});
      const isFinancial=payload.settings.south_panels===16;
      return {result:{
        ...testRes,
        settings:payload.settings,
        metrics:{
          ...testRes.metrics,
          npv_eur:{median:isFinancial?7500:7000},
          package_npv_eur:{median:isFinancial?10100:9500},
          discounted_payback_years:{median:isFinancial?7.9:8.5}
        },
        finance:{
          ...testRes.finance,
          capex_eur:isFinancial?19000:18000,
          pv_incremental:{
            npv_eur:{median:isFinancial?7500:7000},
            discounted_payback:{median:isFinancial?7.9:8.5}
          },
          solar_equity:{
            npv:{median:isFinancial?6700:6200},
            discounted_payback:{median:isFinancial?7.2:8.0}
          },
          whole_package:{npv:{median:isFinancial?10100:9500},discounted_payback:{median:isFinancial?9.2:10.0}},
          package_equity:{npv:{median:isFinancial?9000:8000},discounted_payback:{median:isFinancial?7.1:8.0}}
        }
      }};
    };

    const scopes = ['solar', 'package'];
    const cashBases = ['cash', 'loan'];

    for (const s of scopes) {
      for (const c of cashBases) {
        context.summaryScope = s;
        context.summaryCash = c;
        context.current = { result: testRes };

        // Render verdict and benchmark
        context.renderVerdictStrip(testRes);
        context.renderConfigVsRecommended();
        await new Promise(resolve=>setImmediate(resolve));
        context.renderConfigVsRecommended();

        const curMetrics = context.getScopeCashMetrics(testRes, s, c);
        const expectedNpv = curMetrics.npvObj.median;
        const expectedFmt = context.fmt(expectedNpv, 'eur');

        // 1. Assert hero systemTitle and benchmark Current card equality
        assert.ok(elements.systemTitle.textContent.includes(expectedFmt), `Hero title must include ${expectedFmt} for ${s}/${c}`);
        assert.ok(elements.configVsRecContent.innerHTML.includes(expectedFmt), `Benchmark current card must include ${expectedFmt} for ${s}/${c}`);

        // 2. Assert scope caption present on benchmark card
        assert.ok(elements.configVsRecContent.innerHTML.includes('card-scope-caption'), 'Scope caption must be present on cards');

        // 3. Assert candidate scope/basis values match expected basis:
        // Cash should use project_npv (7,500), Loan should use equity_npv (6,700), Package should use package_npv (10,100)
        if (s === 'solar' && c === 'cash') {
          assert.ok(elements.configVsRecContent.innerHTML.includes(context.fmt(7500, 'eur')), 'Financial candidate cash uses project_npv 7500');
        } else if (s === 'solar' && c === 'loan') {
          assert.ok(elements.configVsRecContent.innerHTML.includes(context.fmt(6700, 'eur')), 'Financial candidate loan uses equity_npv 6700');
        } else if (s === 'package') {
          const candidateNpv=c==='cash'?10100:9000;
          assert.ok(elements.configVsRecContent.innerHTML.includes(context.fmt(candidateNpv, 'eur')), 'Package candidate uses evaluated active cash basis');
        }


      }
    }

    const htmlSource = fs.readFileSync(require('node:path').join(__dirname, '../feasibility/web/index.html'), 'utf8');
    assert.doesNotMatch(htmlSource, /id="viewingContext(?:Menu)?"/);

    assert.equal(candidateRequests.length,2,'each candidate evaluated once, independent of scope/cash rerenders');
    assert.ok(candidateRequests.every(req=>req.path==='/api/evaluate'));
    assert.equal(candidateRequests[0].payload.settings.quote_total,19000,'candidate retains its own VAT-inclusive modeled capex');
    assert.equal(candidateRequests[0].payload.quote.price_basis,'summary_candidate','candidate uses the backend candidate-pricing contract');
    assert.equal(candidateRequests[0].payload.quote.line_items.length,0,'current itemized lines cannot replace candidate pricing');
    const scopeHandlers=appSource.slice(appSource.indexOf('function setSummaryScope('),appSource.indexOf('function cashFlowView('));
    Object.assign(context,{VALID_SUMMARY_SCOPES:new Set(['solar','package']),VALID_SUMMARY_CASH:new Set(['cash','loan']),persistSummaryPrefs:()=>{},syncSummaryControls:()=>{},invalidateSummarySensitivity:()=>{},renderCashChart:()=>{},renderSummarySensitivity:()=>{},renderSaved:()=>{}});
    vm.runInContext(scopeHandlers,context);
    context.setSummaryScope('solar');context.setSummaryCash('cash');
    context.setSummaryScope('package');context.setSummaryCash('loan');
    assert.ok(elements.systemTitle.textContent.includes(context.fmt(8000,'eur')));
    assert.match(elements.configVsRecContent.innerHTML,/Whole house · After loan/);
    assert.ok(elements.configVsRecContent.innerHTML.includes(context.fmt(8000,'eur')),'toggle handlers refresh benchmark and verdict together');

    // Assert weather count logic:
    // Solar cash: 3 of 3 positive
    context.summaryScope = 'solar'; context.summaryCash = 'cash';
    context.renderVerdictStrip(testRes);
    assert.ok(elements.systemTitle.textContent.includes('3 of 3 weather years positive'));

    // Package cash: 2 of 3 positive (mixed weather)
    context.summaryScope = 'package'; context.summaryCash = 'cash';
    context.renderVerdictStrip(testRes);
    assert.ok(elements.systemTitle.textContent.includes('2 of 3 weather years positive'));

    // Solar loan: 1 of 3 positive (mixed weather)
    context.summaryScope = 'solar'; context.summaryCash = 'loan';
    context.renderVerdictStrip(testRes);
    assert.ok(elements.systemTitle.textContent.includes('1 of 3 weather years positive'));

    // Package loan: missing npv_by_weather -> honest unknown
    context.summaryScope = 'package'; context.summaryCash = 'loan';
    context.renderVerdictStrip(testRes);
    assert.ok(elements.systemTitle.textContent.includes('weather positive count unknown'));

    // Test dedupe configs when best and balanced are identical
    const identicalChoices = {
      financial: { ...mockChoices.balanced },
      balanced: { ...mockChoices.balanced }
    };
    context.solarData = { recommendation_choices: identicalChoices };
    context.renderConfigVsRecommended();
    assert.ok(elements.configVsRecContent.innerHTML.includes('RECOMMENDED CONFIGURATION'), 'Merges identical configs into single Recommended card');
    assert.ok(!elements.configVsRecContent.innerHTML.includes('BEST RETURN (FINANCIAL)'), 'Hides duplicate best return when identical');
  }

  console.log('Optimizer UI regression fixtures passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
