/* Run with Node: regression fixtures for assumption resets and number stepping. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../feasibility/web/app.js'),'utf8');
const elements=[];
let searchNodes=null,searchGroups=[];
const controls={innerHTML:''};
let requests=[];
const dialog={open:false,returnValue:'',listeners:{},showModal(){this.open=true;},addEventListener(name,fn){this.listeners[name]=fn;}};
const pinnedList={innerHTML:'',oninput:null,onclick:null,onkeydown:null};
const filterStatus={textContent:'',role:'status'};
const searchHint={hidden:true};
const emptyState={hidden:true,textContent:''};
const pinnedAssumptions={hidden:false};
const modeBtns=[
 {dataset:{mode:'all'},classList:{classes:new Set(['active']),toggle(c,force){if(force===undefined){if(this.classes.has(c))this.classes.delete(c);else this.classes.add(c);}else if(force)this.classes.add(c);else this.classes.delete(c);return this.classes.has(c);},contains(c){return this.classes.has(c);}},setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k];},focus(){},onclick:null,onkeydown:null},
 {dataset:{mode:'view'},classList:{classes:new Set(),toggle(c,force){if(force===undefined){if(this.classes.has(c))this.classes.delete(c);else this.classes.add(c);}else if(force)this.classes.add(c);else this.classes.delete(c);return this.classes.has(c);},contains(c){return this.classes.has(c);}},setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k];},focus(){},onclick:null,onkeydown:null},
 {dataset:{mode:'modified'},classList:{classes:new Set(),toggle(c,force){if(force===undefined){if(this.classes.has(c))this.classes.delete(c);else this.classes.add(c);}else if(force)this.classes.add(c);else this.classes.delete(c);return this.classes.has(c);},contains(c){return this.classes.has(c);}},setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k];},focus(){},onclick:null,onkeydown:null}
];
const filterMode={querySelectorAll:sel=>sel==='.sidebar-mode-btn'?modeBtns:[],children:modeBtns};
const ui={defaultConfirm:dialog,defaultConfirmTitle:{textContent:''},defaultConfirmDescription:{textContent:''},status:{textContent:''},error:{hidden:true},pinnedControlsList:pinnedList,pinnedAssumptions,modifiedCountBadge:{textContent:'',className:''},activeTabAssumptionsPill:{textContent:''},sidebarFilterStatus:filterStatus,sidebarSearchHint:searchHint,sidebarEmptyState:emptyState,sidebarFilterMode:filterMode};
const context=vm.createContext({
 localStorage:{getItem:()=>null},
 fetch:async(path,options)=>{const body=JSON.parse(options.body);requests.push({path,body});return {ok:true,json:async()=>({restart_required:true,name:body.name,value:body.value,record:{name:body.name,value:body.value,unit:body.name==='scop'?'kWh heat/kWh electricity':'years',status:'provided',source:'User-saved shared app default',date_accessed:'2026-10-07',owner:'user',factory_default:body.name==='scop'?4:10}})};},
 document:{getElementById:id=>ui[id]||controls,querySelectorAll:selector=>selector==='details.group'?searchGroups:selector==='[data-control]'&&searchNodes?searchNodes:elements,querySelector:()=>null},
 syncOptimizerEquipmentInputs:()=>{},
 setTimeout,clearTimeout,
});
vm.runInContext(source.slice(0,source.indexOf('function schedule('))+'\nlet scheduled=0;function schedule(){scheduled++;}',context);
const run=code=>vm.runInContext(code,context);
assert.equal(run("title('export_price_future_years')"),'Years to Ramp Future Factor');
assert.equal(run("title('summer_ac_multiplier')"),'Summer AC Multiplier');
run("$('defaultConfirm').addEventListener('close',()=>{const resolve=pendingDefaultConfirmation;pendingDefaultConfirmation=null;if(resolve)resolve($('defaultConfirm').returnValue==='save');});");
run(`schema={defaults:{export_market_price:.128,retail_commodity:91.23/780,scop:4,quote_total:null,cer_enabled:false,load_scenario:'baseline',south_panels:12,actual_price_future_factor:1,export_price_future_years:10},records:[
 {name:'export_market_price',unit:'EUR/kWh',bounds:[0,.30],group:'export'},
 {name:'retail_commodity',unit:'EUR/kWh',bounds:[0,.5],group:'bill'},
 {name:'actual_price_future_factor',unit:'multiplier',bounds:[.3,2],group:'export',description:'Starts at 1.0, moves linearly to this target over the selected ramp duration, then stays at the target.'},
 {name:'export_price_future_years',unit:'years',bounds:[1,30],group:'export',description:'Number of years for the future export-price factor to reach its target; the factor moves linearly, then holds.'},
 {name:'scop',unit:'kWh heat/kWh electricity',bounds:[1,8],group:'loads'},
 {name:'quote_total',unit:'EUR',bounds:[0,150000],group:'cost'},
 {name:'cer_enabled',unit:'boolean',group:'cer'},
 {name:'load_scenario',unit:'scenario',bounds:['baseline','electrified','ev'],group:'loads'},
 {name:'south_panels',unit:'panels',bounds:[0,22],group:'layout'}]};settings={...schema.defaults};quote={line_items:[{net_eur:1234}],battery_model:'Keep this'};renderControls();`);
assert.ok(controls.innerHTML.includes('category-accordion'),'renders category accordions');
assert.match(controls.innerHTML,/data-category="System"[^>]*>[\s\S]*data-control="south_panels"/,'System category includes south_panels');
const totalSaveDefaults=(controls.innerHTML.match(/data-save-default=/g)||[]).length+(pinnedList.innerHTML.match(/data-save-default=/g)||[]).length;
const totalResetSettings=(controls.innerHTML.match(/data-reset-setting=/g)||[]).length+(pinnedList.innerHTML.match(/data-reset-setting=/g)||[]).length;
assert.equal(totalSaveDefaults,9);
assert.ok(!/<details[^>]*\bopen\b/.test(controls.innerHTML),'every assumption group starts collapsed');
assert.equal(totalResetSettings,9);
assert.match(controls.innerHTML,/aria-label="Reset Export Market Price"/);
assert.match(controls.innerHTML,/data-setting="export_price_future_years" value="10"/,'future-factor duration renders its default value');
assert.match(controls.innerHTML,/Number of years for the future export-price factor to reach its target/,'duration control explains the ramp');
assert.ok(controls.innerHTML.indexOf('Export Price Future Factor')<controls.innerHTML.indexOf('Years to Ramp Future Factor'),'duration follows the target factor in export controls');
assert.match(controls.innerHTML,/data-save-default="export_price_future_years"/);
assert.match(controls.innerHTML,/data-reset-setting="export_price_future_years"/);
assert.match(run('control(schema.records[0])'),/step="0.001"/);
assert.ok(!run('control(schema.records[0])').includes('step="any"'));
run("schema.defaults.inverter_kw=4;settings.inverter_kw=11.04;const highPowerInverter={name:'inverter_kw',unit:'kW',bounds:[3,20],group:'equipment'};");
const inverterControl=run('control(highPowerInverter)');
assert.match(inverterControl,/value="11\.04" min="3" data-min="3" max="20" step="0\.01"/,'exact Powerwall output is accepted without rounding');
assert.match(inverterControl,/type="range"[^>]*max="20"[^>]*step="0\.01"[^>]*value="11\.04"/,'slider supports the same power and precision');
assert.ok(!run('numericBounds(schema.records[1],schema.defaults.retail_commodity)').startsWith('min='),'invoice ratio uses exact value as spinner base');
function input(key,value){
 return {type:'number',dataset:{setting:key},textValue:String(value),get value(){return this.textValue;},set value(v){this.textValue=String(v);},setAttribute(k,v){this[k==='value'?'defaultValue':k]=String(v);},removeAttribute(k){delete this[k];}};
}
const price=input('export_market_price',.128),scop=input('scop',4),invoice=input('retail_commodity',91.23/780),total=input('quote_total',7000),futureYears=input('export_price_future_years',10);
const slider=input('scop',4);slider.type='range';slider.dataset={slider:'scop'};
elements.push(price,scop,invoice,total,futureYears,slider);
run("settings.scop=5.1;settings.quote_total=7000;settings.retail_commodity=.3;settings.load_scenario='ev';resetSetting('retail_commodity');resetSetting('quote_total');");
assert.equal(run('settings.retail_commodity'),91.23/780,'reset retains full invoice precision');
assert.equal(invoice.value,String(91.23/780));
assert.equal(run('settings.quote_total'),null,'optional quote resets to empty, not zero');
assert.equal(total.value,'');
assert.equal(run('settings.scop'),5.1,'unrelated assumptions survive individual resets');
assert.equal(run('settings.load_scenario'),'ev');
assert.equal(run('JSON.stringify(quote)'),'{"line_items":[{"net_eur":1234}],"battery_model":"Keep this"}');
assert.ok(!/<details[^>]*\bopen\b/.test(controls.innerHTML),'individual reset does not rerender group state');
assert.ok(controls.innerHTML.includes('category-accordion'),'category accordions preserved');
assert.match(controls.innerHTML,/data-category="System"[^>]*>[\s\S]*data-control="south_panels"/,'System category includes south_panels');
function arrow(target,key){let prevented=false;controls.onkeydown({target,key,preventDefault(){prevented=true;}});assert.equal(prevented,true);}
arrow(price,'ArrowDown');assert.equal(price.value,'0.127');assert.equal(run('settings.export_market_price'),.127);
arrow(price,'ArrowUp');assert.equal(price.value,'0.128');arrow(price,'ArrowUp');assert.equal(price.value,'0.129');
price.value='0';arrow(price,'ArrowDown');assert.equal(price.value,'0');
price.value='.3';arrow(price,'ArrowUp');assert.equal(price.value,'0.3');
scop.value='4';arrow(scop,'ArrowUp');assert.equal(scop.value,'4.1');assert.equal(slider.value,'4.1');
scop.value='8';arrow(scop,'ArrowUp');assert.equal(scop.value,'8');
invoice.value='-0.0003';controls.oninput({target:invoice,isTrusted:true});assert.equal(invoice.value,'0','mouse spinner is bounded when its min attribute is absent');
price.value='0.';controls.oninput({target:price,isTrusted:true,inputType:'insertText'});assert.equal(price.value,'0.','typing an unfinished decimal does not rewrite the field');
invoice.value='-0.01';controls.oninput({target:invoice,isTrusted:true,inputType:'insertText'});assert.equal(run('settings.retail_commodity'),-.01,'direct text edits remain available for model validation');
controls.onclick({target:{closest:()=>({dataset:{resetSetting:'scop'}})}});
assert.equal(scop.value,'4');assert.equal(slider.value,'4');
async function verifySavedDefaults(){
 run('settings.scop=5.1');
 const cancelled=run('saveDefault("scop")');
 assert.equal(dialog.open,true,'save opens an in-page confirmation dialog');
 assert.match(ui.defaultConfirmTitle.textContent,/Scop/);
 assert.match(ui.defaultConfirmDescription.textContent,/5.1 kWh heat\/kWh electricity/);
 assert.match(ui.defaultConfirmDescription.textContent,/Reset will restore 5.1/);
 assert.equal(requests.length,0,'no API request occurs before an explicit choice');
 dialog.returnValue='cancel';dialog.open=false;dialog.listeners.close();await cancelled;
 assert.equal(requests.length,0,'canceling confirmation makes no request');
 assert.equal(run('schema.defaults.scop'),4,'canceling leaves the shared default unchanged');
 const escaped=run('saveDefault("scop")');dialog.returnValue='';dialog.open=false;dialog.listeners.close();await escaped;
 assert.equal(requests.length,0,'Escape follows the same safe cancel path');
run('settings.scop=5.1');const saved=run('saveDefault("scop")');
 assert.equal(requests.length,0,'saving still waits for confirmation');
 dialog.returnValue='save';dialog.open=false;dialog.listeners.close();await saved;
 assert.match(ui.status.textContent,/restart the local server/);
 assert.equal(requests.length,1);assert.deepEqual(requests[0],{path:'/api/defaults',body:{name:'scop',value:5.1}});
 assert.equal(run('schema.defaults.scop'),5.1,'successful save becomes the individual reset value');
 assert.equal(run('schema.defaults.south_panels'),12,'saving one input preserves every other default');
 run('settings.scop=4;resetSetting("scop")');assert.equal(run('settings.scop'),5.1);
 run('settings.export_price_future_years=12');const savedRamp=run('saveDefault("export_price_future_years")');
 dialog.returnValue='save';dialog.open=false;dialog.listeners.close();await savedRamp;
 assert.equal(run('schema.defaults.export_price_future_years'),12,'future-factor duration can be saved as the shared default');
 run('settings.export_price_future_years=10;resetSetting("export_price_future_years")');
 assert.equal(run('settings.export_price_future_years'),12);assert.equal(futureYears.value,'12','reset restores the saved future-factor duration');
}
const defaultsPromise = verifySavedDefaults().catch(error=>{console.error(error);process.exitCode=1;});
run("resetSetting('__unknown__');");assert.equal(run('settings.__unknown__'),undefined);
const cashRows=run(`displayCashRows([{year:1,pv_incremental_range:{min:100,median:150,max:200},display_pv_incremental_range:{min:50,median:75,max:100},whole_package_range:{min:80,median:120,max:160},display_whole_package_range:{min:40,median:60,max:80},tax_deduction:20,display_tax_deduction:10,replacement:30,display_replacement:15}])`);
assert.deepEqual(JSON.parse(JSON.stringify(cashRows[0].pv_incremental_range)),{min:50,median:75,max:100},'cash chart receives real-denominated flow bounds');
assert.deepEqual(JSON.parse(JSON.stringify(cashRows[0].whole_package_range)),{min:40,median:60,max:80});
assert.equal(cashRows[0].tax_deduction,10);assert.equal(cashRows[0].replacement,15);
run(`quote={line_items:[{net_eur:500}],battery_nameplate_kwh:10,battery_dod:.8,roundtrip_efficiency:.9,continuous_charge_kw:3};useCandidate({settings:{south_panels:8,battery_kwh:4,roundtrip_efficiency:.88}})`);
assert.equal(run('JSON.stringify(settings)'),'{"south_panels":8,"battery_kwh":4,"roundtrip_efficiency":0.88}','candidate Use applies its selected settings');
assert.equal(run('JSON.stringify(quote)'),'{"line_items":[]}','candidate Use clears quote overrides for its independent comparison');
run(`schema.records.push({name:'battery_modules',unit:'modules',bounds:[0,null]},{name:'battery_module_kwh',unit:'usable kWh/module',bounds:[0,null]},{name:'battery_kwh',unit:'usable kWh',bounds:[0,null]},{name:'south_panels',unit:'panels',bounds:[0,null]});schema.defaults.battery_modules=2;schema.defaults.battery_module_kwh=5;schema.defaults.battery_kwh=10;settings={...schema.defaults};quote={line_items:[{net_eur:500}],battery_nameplate_kwh:12,battery_dod:.9,battery_model:'Keep',continuous_charge_kw:4,continuous_discharge_kw:5};`);
run("editSetting('battery_modules',4)");assert.equal(run('settings.battery_kwh'),20,'module count updates total usable capacity');assert.equal(run('settings.battery_module_kwh'),5);assert.equal(run('quote.battery_nameplate_kwh'),undefined);assert.equal(run('quote.battery_dod'),undefined);assert.equal(run('quote.battery_model'),'Keep');assert.equal(run('quote.continuous_charge_kw'),4,'battery sizing preserves unrelated charge evidence');
run("editSetting('battery_kwh',18)");assert.equal(run('settings.battery_module_kwh'),4.5,'direct total updates module capacity');assert.equal(run('settings.battery_modules'),4);
run("editSetting('battery_modules',0);editSetting('battery_kwh',6)");assert.equal(run('settings.battery_modules'),1,'positive total repairs a zero-module configuration');assert.equal(run('settings.battery_module_kwh'),6);
run("editSetting('charge_kw',2);editSetting('discharge_kw',6)");assert.equal(run('quote.continuous_charge_kw'),undefined);assert.equal(run('quote.continuous_discharge_kw'),undefined);assert.equal(run('quote.battery_model'),'Keep','power edits preserve unrelated quote evidence');
const openEnded=run('numericBounds({name:"south_panels",unit:"panels",bounds:[0,null]},24)');assert.match(openEnded,/data-min="0"/);assert.ok(!openEnded.includes('max='),'open-ended assumptions have no HTML maximum');
assert.match(run('control({name:"south_panels",unit:"panels",bounds:[0,null],group:"layout",status:"provided",description:"roof",source:"scenario"})'),/value="12"/);assert.ok(!run('control({name:"south_panels",unit:"panels",bounds:[0,null],group:"layout",status:"provided",description:"roof",source:"scenario"})').includes('type="range"'),'open-ended panel counts do not render a clamping slider');
const unlimitedPanel=input('south_panels',22);elements.push(unlimitedPanel);run("schema.records.find(r=>r.name==='south_panels').bounds=[0,null]");unlimitedPanel.value='24';controls.onkeydown({target:unlimitedPanel,key:'ArrowUp',preventDefault(){}});assert.equal(unlimitedPanel.value,'25','arrow stepping remains unbounded when max is null');
assert.match(run('cashFlowLabel("real")'),/^Real year-end cash flows/);
assert.match(run('cashFlowLabel("nominal")'),/^Nominal year-end cash flows/);
assert.equal(run('title("actual_price_future_factor")'),'Export Price Future Factor');
function uiControl(control,when,text,whenValue){return {dataset:{control,when,...(whenValue?{whenValue}:{})},textContent:text,hidden:false,hasAttribute:key=>key==='data-when'&&Boolean(when),querySelectorAll:()=>[]};}
const dhwResistance=uiControl('dhw_resistance_efficiency','dhw_heating_type','DHW Resistance Efficiency','resistance');
const dhwScop=uiControl('dhw_scop','dhw_heating_type','DHW Heat Pump SCOP','heat_pump');
const includeLoan=uiControl('loan_include_electrification','loan_active','Include electrification capex');
const futureYearsControl=uiControl('export_price_future_years',null,'Years to Ramp Future Factor Number of years for the future export-price factor to reach its target; the factor moves linearly, then holds.');
const loadGroup={open:false,hidden:false,querySelectorAll:()=>[dhwResistance,dhwScop]};
const financeGroup={open:true,hidden:false,querySelectorAll:()=>[includeLoan]};
const exportGroup={open:false,hidden:false,querySelectorAll:()=>[futureYearsControl]};
searchNodes=[dhwResistance,dhwScop,includeLoan,futureYearsControl];searchGroups=[loadGroup,financeGroup,exportGroup];
run("schema.defaults.loan_mode='fraction';schema.defaults.loan_fraction=0;schema.defaults.loan_amount=0;schema.records.push({name:'loan_mode'},{name:'loan_fraction'},{name:'loan_amount'});settings.dhw_heating_type='heat_pump';settings.loan_mode='amount';settings.loan_amount=2000;");
run('updateSearch("SCOP")');assert.equal(loadGroup.open,true);assert.equal(financeGroup.open,true);
assert.equal(dhwScop.hidden,false,'active heat-pump control matches search');assert.equal(dhwResistance.hidden,true,'inactive resistance control remains hidden during search');assert.equal(includeLoan.hidden,true,'nonmatching loan control is filtered');
run('updateSearch("futu")');assert.equal(futureYearsControl.hidden,false,'future-factor duration is visible when searching for future');assert.equal(exportGroup.hidden,false,'export group is visible when the future-factor duration matches');
run('updateSearch("heat pump")');assert.equal(dhwScop.hidden,false,'changing search keeps active controls visible');
run('updateSearch("")');assert.equal(loadGroup.open,false,'clearing search restores its original collapsed state');assert.equal(financeGroup.open,true,'clearing search restores its original open state');assert.equal(dhwScop.hidden,false);assert.equal(dhwResistance.hidden,true,'clearing search retains conditional visibility');assert.equal(includeLoan.hidden,false,'clearing search reveals conditionally active loan control');
run("var savedSchemaDefaults={...schema.defaults},savedSchemaRecords=schema.records,savedSettings={...settings};schema.defaults={loan_fraction:0,loan_apr:.06,loan_years:10};schema.records=[{name:'loan_fraction'},{name:'loan_apr'},{name:'loan_years'}];settings={loan_fraction:0,loan_apr:.06,loan_years:10};");
assert.equal(run('conditionalVisible({dataset:{when:"loan_mode",control:"loan_fraction"}})'),true,'legacy schemas keep the share control visible without loan_mode');
assert.equal(run('conditionalVisible({dataset:{when:"loan_active",control:"loan_apr"}})'),false,'legacy APR remains hidden when no loan share is selected');
run('settings.loan_fraction=.4');assert.equal(run('conditionalVisible({dataset:{when:"loan_active",control:"loan_apr"}})'),true,'legacy APR and term controls appear when financing is enabled');
assert.equal(run('loanUsesAmount()'),false,'legacy settings never infer amount mode from a missing field');
run('schema.defaults=savedSchemaDefaults;schema.records=savedSchemaRecords;settings=savedSettings');
run("schema.defaults.loan_include_electrification=false;settings.loan_include_electrification=false;schema.defaults.loan_mode='fraction';settings.loan_mode='fraction';schema.defaults.dhw_heating_type='resistance';settings.dhw_heating_type='resistance';");
const switchMarkup=run('control({name:"loan_include_electrification",unit:"boolean",group:"finance",status:"proposed",description:"Include conversion capex",source:"scenario"})');
assert.match(switchMarkup,/type="checkbox" role="switch"/,'conversion financing uses a switch control');
assert.match(run('control({name:"dhw_scop",unit:"kWh heat\/kWh electricity",bounds:[1,8],group:"loads",status:"proposed",description:"DHW SCOP",source:"scenario"})'),/data-when="dhw_heating_type" data-when-value="heat_pump"/);
run("schema.defaults.cer_enabled=false;settings.cer_enabled=false;$(\"controls\").oninput({target:{type:'select',dataset:{setting:'cer_enabled'},value:'false'}})");assert.equal(run('settings.cer_enabled'),false,'existing boolean selects remain false when selecting No');
assert.equal(run("title('heating_schedule_mode')"),'Heating schedule');
assert.equal(run("title('heating_on_hour')"),'Morning heating on hour');
assert.equal(run("title('heating_off_hour')"),'Morning heating off hour');
assert.equal(run("title('heating_evening_on_hour')"),'Evening heating on hour');
assert.equal(run("title('heating_evening_off_hour')"),'Evening heating off hour');
assert.equal(run("numericStep({name:'heating_on_hour',unit:'hour'})"),1);
assert.equal(run("numericStep({name:'heating_evening_off_hour',unit:'hour'})"),1);
run("settings.heating_schedule_mode='scheduled'");
assert.equal(run('conditionalVisible({dataset:{when:"heating_schedule_mode",whenValue:"scheduled"}})'),true,'heating hour controls visible in scheduled mode');
run("settings.heating_schedule_mode='continuous'");
assert.equal(run('conditionalVisible({dataset:{when:"heating_schedule_mode",whenValue:"scheduled"}})'),false,'heating hour controls hidden in continuous mode');
const heatingModeMarkup=run('control({name:"heating_schedule_mode",choices:["scheduled","continuous"],bounds:["scheduled","continuous"],group:"loads",status:"proposed",description:"schedule mode"})');
assert.match(heatingModeMarkup,/Scheduled operating windows/);
assert.match(heatingModeMarkup,/Continuous \(24h old model\)/);
const morningOnMarkup=run('control({name:"heating_on_hour",unit:"hour",bounds:[0,23],group:"loads",status:"proposed",description:"morning on hour"})');
assert.match(morningOnMarkup,/data-when="heating_schedule_mode" data-when-value="scheduled"/);
assert.match(morningOnMarkup,/step="1"/);
assert.match(morningOnMarkup,/data-min="0" max="23"/);
// Dynamic forecast year and dayhour bounds
ui.dayhourModeForecastOption={textContent:''};
ui.dayhourEyebrow={textContent:''};
ui.weatherDate={value:'2027-01-01',min:'2027-01-01',max:'2027-12-31'};
ui.dayhourMode={value:'forecast_2027'};
run('settings.installation_year=2026;');
assert.equal(run('getForecastYear()'),2027);
run('updateForecastDateBounds();');
assert.equal(ui.dayhourModeForecastOption.textContent,'Forecast first year · 2027');
assert.equal(ui.dayhourEyebrow.textContent,'FIRST OPERATING YEAR · 2027 WEATHER SCENARIOS');
assert.equal(ui.weatherDate.min,'2027-01-01');
assert.equal(ui.weatherDate.max,'2027-12-31');

// Dynamic update on installation_year change (e.g. leap year 2028)
run('settings.installation_year=2027;');
assert.equal(run('getForecastYear()'),2028);
run('updateForecastDateBounds();');
assert.equal(ui.dayhourModeForecastOption.textContent,'Forecast first year · 2028');
assert.equal(ui.dayhourEyebrow.textContent,'FIRST OPERATING YEAR · 2028 WEATHER SCENARIOS');
assert.equal(ui.weatherDate.min,'2028-01-01');
assert.equal(ui.weatherDate.max,'2028-12-31');

// Leap day calculation and clamping
assert.equal(run('maxDaysInMonth(2028,2)'),29);
assert.equal(run('maxDaysInMonth(2027,2)'),28);
assert.equal(run('maxDaysInMonth(2036,2)'),29);
ui.weatherDate.value='2028-02-29';
assert.equal(run('dayhourDateForYear(2028)'),'2028-02-29');
assert.equal(run('dayhourDateForYear(2027)'),'2027-02-28','Feb 29 is safely clamped to Feb 28 on non-leap years');

async function runRegressions() {
  // Await the asynchronous verifySavedDefaults suite before running regressions
  await defaultsPromise;

  // Regression Issue 4: Two decimals for battery storage in system summary
  ui.systemSummary = { innerHTML: '' };
  ui.summarySolarPanels = { innerHTML: '' };
  ui.summaryActiveRoofs = { innerHTML: '' };
  ui.summaryStorage = { innerHTML: '' };
  run("settings.south_panels = 12; settings.panel_watts = 400; settings.battery_kwh = 8.28; updateCurrentSystemSummary();");
  assert.equal(ui.summaryStorage.innerHTML, '<strong>8.28</strong> kWh', 'usable storage displays with two decimals');

  // Regression Issue 3: CSS direct-child summary selector, explicit chevron, focus-visible
  const cssContent = fs.readFileSync(require('node:path').join(__dirname, '../feasibility/web/style.css'), 'utf8');
  assert.ok(cssContent.includes('aside details.group > summary'), 'uses direct-child category summary selector');
  assert.ok(!cssContent.includes('aside details.group summary{'), 'does not use descendant summary selector on details.group');
  assert.match(cssContent, /aside details\.group > summary \.category-title::before/, 'explicit visible chevron on category summary');
  assert.match(cssContent, /aside details\.group > summary:focus-visible/, 'keyboard focus-visible on group summary');
  assert.match(cssContent, /aside \.control-details summary:focus-visible/, 'keyboard focus-visible on control-details summary');

  // Regression Issue 1: Search, tab switch, filter application, and preservation
  run(`
  schema = {
    defaults: {
      retail_commodity: 0.12,
      gas_commodity: 0.45,
      real_discount_rate: 0.04,
      deduction_rate: 0.5,
      battery_kwh: 5.0,
      south_panels: 12,
      scop: 4
    },
    records: [
      { name: 'retail_commodity', unit: 'EUR/kWh', bounds: [0, 0.5], group: 'bill', label: 'Electricity rate' },
      { name: 'gas_commodity', unit: 'EUR/Smc', bounds: [0, 1.0], group: 'gas', label: 'Gas price' },
      { name: 'real_discount_rate', unit: 'fraction', bounds: [0, 0.2], group: 'finance', label: 'Real discount' },
      { name: 'deduction_rate', unit: 'fraction', bounds: [0, 1.0], group: 'tax', label: 'Deduction' },
      { name: 'battery_kwh', unit: 'usable kWh', bounds: [0, 30], group: 'equipment', label: 'Battery capacity' },
      { name: 'south_panels', unit: 'panels', bounds: [0, 30], group: 'layout', label: 'South panels' },
      { name: 'scop', unit: 'kWh/kWh', bounds: [1, 8], group: 'loads', label: 'Heat pump SCOP' }
    ]
  };
  settings = { ...schema.defaults };
  renderControls();
  `);

  // Pinned conceptual controls include five common fields, excluding south_panels
  assert.ok(pinnedList.innerHTML.includes('data-control="retail_commodity"'));
  assert.ok(pinnedList.innerHTML.includes('data-control="gas_commodity"'));
  assert.ok(!pinnedList.innerHTML.includes('data-control="south_panels"'));
  assert.ok(controls.innerHTML.includes('data-category="System"'));
  assert.ok(controls.innerHTML.includes('data-control="south_panels"'));

  // Search "gas price" hides non-matching controls
  function makeDomControl(name, text) {
    return {
      dataset: { control: name },
      textContent: text,
      hidden: false,
      hasAttribute: () => false,
      querySelectorAll: () => []
    };
  }
  const ctrlRetail = makeDomControl('retail_commodity', 'Electricity rate 0.12 EUR/kWh');
  const ctrlGas = makeDomControl('gas_commodity', 'Gas price 0.45 EUR/Smc');
  const ctrlDiscount = makeDomControl('real_discount_rate', 'Real discount 0.04');
  const ctrlDeduction = makeDomControl('deduction_rate', 'Deduction 0.5');
  const ctrlBattery = makeDomControl('battery_kwh', 'Battery capacity 5');
  const ctrlSouth = makeDomControl('south_panels', 'South panels 12');
  const ctrlScop = makeDomControl('scop', 'Heat pump SCOP 4');

  const sysGroup = { dataset: { category: 'System' }, open: false, hidden: false, querySelectorAll: () => [ctrlSouth] };
  const loadsGroup = { dataset: { category: 'Household demand' }, open: true, hidden: false, querySelectorAll: () => [ctrlScop] };

  searchNodes = [ctrlRetail, ctrlGas, ctrlDiscount, ctrlDeduction, ctrlBattery, ctrlSouth, ctrlScop];
  searchGroups = [sysGroup, loadsGroup];

  run('updateSearch("gas price");');
  assert.equal(ctrlGas.hidden, false, 'gas_commodity matches "gas price"');
  assert.equal(ctrlRetail.hidden, true, 'retail_commodity is hidden during "gas price" search');
  assert.equal(ctrlBattery.hidden, true, 'battery_kwh is hidden during "gas price" search');

  // Switch to Financing view: updateSidebarForTab should NOT rebuild pinned controls
  pinnedList.children = [1, 2, 3, 4, 5];
  const initialPinnedHtml = pinnedList.innerHTML;
  run('updateSidebarForTab("financing");');
  assert.equal(ui.activeTabAssumptionsPill.textContent, 'Financing');
  assert.equal(pinnedList.innerHTML, initialPinnedHtml, 'pinned controls are not rebuilt on tab switch');
  assert.equal(ctrlRetail.hidden, true, 'pinned controls remain filtered after tab switch');

  // Clearing search restores category disclosures keyed by category identity
  assert.equal(loadsGroup.open, true);
  assert.equal(sysGroup.open, true, 'search opened the system group during search');
  run('updateSearch("");');
  assert.equal(sysGroup.open, false, 'clearing search restores System group to closed state');
  assert.equal(loadsGroup.open, true, 'clearing search restores Household demand group to open state');

  // Regression Issue 2: applySharedState wires into modified badges, modified state, system summary, and preserves dirty/focus rules
  let summaryUpdated = 0;
  let badgeUpdated = 0;
  let modifiedStatesUpdated = [];

  context.updateCurrentSystemSummary = () => { summaryUpdated++; };
  context.updateModifiedBadge = () => { badgeUpdated++; };
  context.updateControlModifiedState = (k) => { modifiedStatesUpdated.push(k); };

  run('sharedUiReady = true;');
  context._mockSharedSync = {
    dirtySettings: new Map([['retail_commodity', { value: 0.15, version: 1 }]]),
    update: () => false
  };
  run('sharedSync = _mockSharedSync;');

  const activeInput = { dataset: { setting: 'retail_commodity' }, value: '0.15' };
  context.document.activeElement = activeInput;

  const remoteState = {
    settings: { retail_commodity: 0.15, gas_commodity: 0.50, south_panels: 16, battery_kwh: 8.28 },
    defaults: { retail_commodity: 0.10, gas_commodity: 0.40, south_panels: 12, battery_kwh: 5.0, scop: 4 },
    records: [
      { name: 'retail_commodity', status: 'verified', source: 'Bill 2026', unit: 'EUR/kWh' },
      { name: 'gas_commodity', status: 'verified', source: 'Bill 2026', unit: 'EUR/Smc' },
      { name: 'south_panels', status: 'measured', source: 'Roof survey', unit: 'panels' },
      { name: 'battery_kwh', status: 'proposed', source: 'Design', unit: 'usable kWh' }
    ],
    quote: { line_items: [] }
  };

  context._remoteState = remoteState;
  run('applySharedState(_remoteState);');

  assert.ok(summaryUpdated > 0, 'applySharedState calls updateCurrentSystemSummary');
  assert.ok(badgeUpdated > 0, 'applySharedState calls updateModifiedBadge');
  assert.ok(modifiedStatesUpdated.includes('retail_commodity'), 'applySharedState updates control modified state for retail_commodity');
  assert.ok(modifiedStatesUpdated.includes('gas_commodity'), 'applySharedState updates control modified state for gas_commodity');

  // Local reset updates state coherently
  modifiedStatesUpdated = [];
  summaryUpdated = 0;
  badgeUpdated = 0;
  run('resetSetting("gas_commodity");');
  assert.ok(summaryUpdated > 0, 'resetSetting calls updateCurrentSystemSummary');
  assert.ok(badgeUpdated > 0, 'resetSetting calls updateModifiedBadge');
  assert.ok(modifiedStatesUpdated.includes('gas_commodity'), 'resetSetting calls updateControlModifiedState');

  // ==========================================
  // Task 3: Sidebar Navigation & Filtering Regressions
  // ==========================================

  // 1. Setup mock schema with records across diverse groups and conditional fields
  run(`
    schema = {
      defaults: {
        retail_commodity: 0.12,
        gas_commodity: 0.45,
        real_discount_rate: 0.04,
        deduction_rate: 0.50,
        battery_kwh: 5.0,
        load_scenario: 'baseline',
        inverter_kw: 6.0,
        horizon_years: 20,
        inflation: 0.02,
        south_panels: 12,
        scop: 4.0,
        quote_total: 10000,
        dhw_heating_type: 'heat_pump',
        dhw_scop: 3.5,
        dhw_resistance_efficiency: 0.95
      },
      records: [
        { name: 'retail_commodity', group: 'bill', label: 'Electricity rate', unit: 'EUR/kWh', status: 'verified', source: 'Bill' },
        { name: 'gas_commodity', group: 'gas', label: 'Gas price', unit: 'EUR/Smc', status: 'verified', source: 'Bill' },
        { name: 'real_discount_rate', group: 'finance', label: 'Real discount', unit: 'fraction', status: 'proposed', source: 'Standard' },
        { name: 'deduction_rate', group: 'tax', label: 'Deduction', unit: 'fraction', status: 'proposed', source: 'Law' },
        { name: 'battery_kwh', group: 'equipment', label: 'Battery capacity', unit: 'usable kWh', status: 'proposed', source: 'Design' },
        { name: 'load_scenario', group: 'loads', label: 'Load profile', unit: 'scenario', status: 'provided', source: 'User' },
        { name: 'inverter_kw', group: 'equipment', label: 'Inverter power', unit: 'kW', status: 'proposed', source: 'Design' },
        { name: 'horizon_years', group: 'finance', label: 'Analysis horizon', unit: 'years', status: 'provided', source: 'Default' },
        { name: 'inflation', group: 'finance', label: 'Inflation rate', unit: 'fraction', status: 'provided', source: 'Default' },
        { name: 'south_panels', group: 'layout', label: 'South panels', unit: 'panels', status: 'measured', source: 'Roof' },
        { name: 'scop', group: 'loads', label: 'Heat pump SCOP', unit: 'kWh/kWh', status: 'provided', source: 'Default' },
        { name: 'quote_total', group: 'cost', label: 'Quote gross total', unit: 'EUR', status: 'provided', source: 'Offer' },
        { name: 'dhw_heating_type', group: 'loads', label: 'DHW heating type', unit: 'type', status: 'provided', source: 'Default' },
        { name: 'dhw_scop', group: 'loads', label: 'DHW heat pump SCOP', unit: 'kWh/kWh', status: 'provided', source: 'Default' },
        { name: 'dhw_resistance_efficiency', group: 'loads', label: 'DHW resistance eff', unit: 'fraction', status: 'provided', source: 'Default' }
      ]
    };
    settings = { ...schema.defaults };
  `);

  const tCtrlRetail = makeDomControl('retail_commodity', 'Electricity rate 0.12 EUR/kWh Source: Bill');
  const tCtrlGas = makeDomControl('gas_commodity', 'Gas price 0.45 EUR/Smc Source: Bill');
  const tCtrlDiscount = makeDomControl('real_discount_rate', 'Real discount 0.04 Source: Standard');
  const tCtrlDeduction = makeDomControl('deduction_rate', 'Deduction 0.50 Source: Law');
  const tCtrlBattery = makeDomControl('battery_kwh', 'Battery capacity 5.0 usable kWh Source: Design');

  const tCtrlScenario = makeDomControl('load_scenario', 'Load profile baseline Source: User');
  const tCtrlInverter = makeDomControl('inverter_kw', 'Inverter power 6.0 kW Source: Design');
  const tCtrlHorizon = makeDomControl('horizon_years', 'Analysis horizon 20 years Source: Default');
  const tCtrlInflation = makeDomControl('inflation', 'Inflation rate 0.02 Source: Default');
  const tCtrlSouth = makeDomControl('south_panels', 'South panels 12 panels Source: Roof');
  const tCtrlScop = makeDomControl('scop', 'Heat pump SCOP 4.0 Source: Default');
  const tCtrlQuoteTotal = makeDomControl('quote_total', 'Quote gross total 10000 EUR Source: Offer');
  const tCtrlDhwType = makeDomControl('dhw_heating_type', 'DHW heating type heat_pump Source: Default');
  const tCtrlDhwScop = makeDomControl('dhw_scop', 'DHW heat pump SCOP 3.5 Source: Default');
  tCtrlDhwScop.dataset.when = 'dhw_heating_type';
  tCtrlDhwScop.dataset.whenValue = 'heat_pump';
  const tCtrlDhwResist = makeDomControl('dhw_resistance_efficiency', 'DHW resistance eff 0.95 Source: Default');
  tCtrlDhwResist.dataset.when = 'dhw_heating_type';
  tCtrlDhwResist.dataset.whenValue = 'resistance';

  const tSysGroup = {
    dataset: { category: 'System' },
    open: false,
    hidden: false,
    countEl: { textContent: '' },
    badgeEl: { textContent: '', hidden: true, style: {}, className: '' },
    querySelector(sel) { return sel === '.category-count' ? this.countEl : sel === '.category-badge' ? this.badgeEl : null; },
    querySelectorAll: () => [tCtrlSouth, tCtrlInverter]
  };
  const tDemandGroup = {
    dataset: { category: 'Household demand' },
    open: false,
    hidden: false,
    countEl: { textContent: '' },
    badgeEl: { textContent: '', hidden: true, style: {}, className: '' },
    querySelector(sel) { return sel === '.category-count' ? this.countEl : sel === '.category-badge' ? this.badgeEl : null; },
    querySelectorAll: () => [tCtrlScenario, tCtrlScop, tCtrlDhwType, tCtrlDhwScop, tCtrlDhwResist]
  };
  const tFinanceGroup = {
    dataset: { category: 'Investment & financing' },
    open: false,
    hidden: false,
    countEl: { textContent: '' },
    badgeEl: { textContent: '', hidden: true, style: {}, className: '' },
    querySelector(sel) { return sel === '.category-count' ? this.countEl : sel === '.category-badge' ? this.badgeEl : null; },
    querySelectorAll: () => [tCtrlHorizon, tCtrlInflation, tCtrlQuoteTotal]
  };

  searchNodes = [
    tCtrlRetail, tCtrlGas, tCtrlDiscount, tCtrlDeduction, tCtrlBattery,
    tCtrlScenario, tCtrlInverter, tCtrlHorizon, tCtrlInflation, tCtrlSouth,
    tCtrlScop, tCtrlQuoteTotal, tCtrlDhwType, tCtrlDhwScop, tCtrlDhwResist
  ];
  searchGroups = [tSysGroup, tDemandGroup, tFinanceGroup];

  pinnedList.querySelectorAll = () => [tCtrlRetail, tCtrlGas, tCtrlDiscount, tCtrlDeduction, tCtrlBattery];

  // 2. Default Mode: 'all'
  run('setSidebarFilterMode("all");');
  assert.equal(run('sidebarFilterMode'), 'all', 'mode defaults to all');
  assert.equal(tCtrlRetail.hidden, false, 'common input visible in all mode');
  assert.equal(tCtrlSouth.hidden, false, 'category input visible in all mode');
  assert.equal(tCtrlDhwScop.hidden, false, 'active conditional input visible in all mode');
  assert.equal(tCtrlDhwResist.hidden, true, 'inactive conditional input hidden in all mode');
  assert.equal(pinnedAssumptions.hidden, false, 'common inputs panel visible');
  assert.equal(tSysGroup.hidden, false, 'system group visible');
  assert.equal(tDemandGroup.hidden, false, 'demand group visible');
  assert.equal(tFinanceGroup.hidden, false, 'finance group visible');
  assert.ok(Number(filterStatus.textContent.split(' ')[0]) > 0, 'status displays result count in all mode');

  // Category counts reflect conditionally available fields
  assert.equal(tDemandGroup.countEl.textContent, '4 inputs', 'category field count excludes inactive conditional input');

  // 3. Mode: 'view' on summary tab
  run('currentViewName = "summary"; setSidebarFilterMode("view");');
  assert.equal(run('sidebarFilterMode'), 'view');
  // Common inputs: all 5 are visible
  assert.equal(tCtrlRetail.hidden, false, 'common input visible in summary view');
  assert.equal(tCtrlGas.hidden, false, 'common input visible in summary view');
  assert.equal(tCtrlBattery.hidden, false, 'common input visible in summary view');
  // Summary-specific inputs: load_scenario, inverter_kw, horizon_years, inflation
  assert.equal(tCtrlScenario.hidden, false, 'load_scenario visible in summary view');
  assert.equal(tCtrlInverter.hidden, false, 'inverter_kw visible in summary view');
  assert.equal(tCtrlHorizon.hidden, false, 'horizon_years visible in summary view');
  assert.equal(tCtrlInflation.hidden, false, 'inflation visible in summary view');
  // Non-summary inputs must be hidden: south_panels, scop, quote_total, dhw_heating_type
  assert.equal(tCtrlSouth.hidden, true, 'south_panels hidden in summary view');
  assert.equal(tCtrlScop.hidden, true, 'scop hidden in summary view');
  assert.equal(tCtrlQuoteTotal.hidden, true, 'quote_total hidden in summary view');
  assert.equal(tCtrlDhwType.hidden, true, 'dhw_heating_type hidden in summary view');
  // Matching categories auto-opened
  assert.equal(tSysGroup.open, true, 'system group auto-opened with inverter_kw');
  assert.equal(tDemandGroup.open, true, 'demand group auto-opened with load_scenario');
  assert.equal(tFinanceGroup.open, true, 'finance group auto-opened with horizon/inflation');

  // 4. View switch: switch to 'behavior' (loads group only)
  pinnedList.children = [1, 2, 3, 4, 5];
  const pinnedHtmlBeforeSwitch = pinnedList.innerHTML;
  run('updateSidebarForTab("behavior");');
  assert.equal(pinnedList.innerHTML, pinnedHtmlBeforeSwitch, 'inputs are not redrawn or rebuilt on tab switch');
  assert.equal(tCtrlRetail.hidden, false, 'common input remains visible in behavior view');
  assert.equal(tCtrlScenario.hidden, false, 'load_scenario (loads group) visible in behavior view');
  assert.equal(tCtrlScop.hidden, false, 'scop (loads group) visible in behavior view');
  assert.equal(tCtrlDhwType.hidden, false, 'dhw_heating_type (loads group) visible in behavior view');
  assert.equal(tCtrlInverter.hidden, true, 'inverter_kw (equipment group) hidden in behavior view');
  assert.equal(tCtrlHorizon.hidden, true, 'horizon_years hidden in behavior view');
  assert.equal(tCtrlInflation.hidden, true, 'inflation hidden in behavior view');
  assert.equal(tCtrlSouth.hidden, true, 'south_panels hidden in behavior view');
  assert.equal(tSysGroup.hidden, true, 'system group without matching inputs is hidden in behavior view');
  assert.equal(tFinanceGroup.hidden, true, 'finance group without matching inputs is hidden in behavior view');
  assert.equal(tDemandGroup.hidden, false, 'demand group with loads is visible and auto-opened');
  assert.equal(tDemandGroup.open, true);

  // 5. View switch: switch to 'quote' (cost group + inverter_kw)
  run('updateSidebarForTab("quote");');
  assert.equal(tCtrlQuoteTotal.hidden, false, 'quote_total (cost group) visible in quote view');
  assert.equal(tCtrlInverter.hidden, false, 'inverter_kw visible in quote view');
  assert.equal(tCtrlSouth.hidden, true, 'south_panels hidden in quote view');
  assert.equal(tCtrlScenario.hidden, true, 'load_scenario hidden in quote view');
  assert.equal(tCtrlHorizon.hidden, true, 'horizon_years hidden in quote view');
  assert.equal(tSysGroup.hidden, false, 'system group with inverter_kw visible in quote view');
  assert.equal(tFinanceGroup.hidden, false, 'finance group with quote_total visible in quote view');
  assert.equal(tDemandGroup.hidden, true, 'demand group without quote inputs hidden in quote view');

  // 6. View switch: switch to 'design' (layout + equipment + energy)
  run('updateSidebarForTab("design");');
  assert.equal(tCtrlSouth.hidden, false, 'south_panels (layout group) visible in design view');
  assert.equal(tCtrlInverter.hidden, false, 'inverter_kw (equipment group) visible in design view');
  assert.equal(tCtrlScenario.hidden, true, 'load_scenario hidden in design view');
  assert.equal(tCtrlQuoteTotal.hidden, true, 'quote_total hidden in design view');
  assert.equal(tSysGroup.hidden, false);
  assert.equal(tFinanceGroup.hidden, true);
  assert.equal(tDemandGroup.hidden, true);

  // 7. Mode: 'modified'
  run('setSidebarFilterMode("modified");');
  assert.equal(run('sidebarFilterMode'), 'modified');
  // Initially, all settings equal defaults: zero modified inputs
  assert.equal(tCtrlRetail.hidden, true, 'unmodified common input hidden in modified mode');
  assert.equal(tCtrlGas.hidden, true, 'unmodified common input hidden in modified mode');
  assert.equal(tCtrlSouth.hidden, true, 'unmodified category input hidden in modified mode');
  assert.equal(pinnedAssumptions.hidden, true, 'common inputs panel hidden when no common input modified');
  assert.equal(tSysGroup.hidden, true, 'system group hidden when no modified inputs');
  assert.equal(tDemandGroup.hidden, true, 'demand group hidden when no modified inputs');
  assert.equal(tFinanceGroup.hidden, true, 'finance group hidden when no modified inputs');
  assert.equal(emptyState.hidden, false, 'empty state visible when 0 modified inputs');
  assert.equal(filterStatus.textContent, 'No modified inputs. All values match shared defaults.', 'status displays empty-state wording');

  // Modify a category input (south_panels)
  run('editSetting("south_panels", 18);');
  assert.equal(tCtrlSouth.hidden, false, 'modified south_panels is visible');
  assert.equal(tSysGroup.hidden, false, 'system group visible with modified south_panels');
  assert.equal(tSysGroup.open, true, 'system group auto-opened');
  assert.equal(tSysGroup.badgeEl.textContent, '1 modified', 'category modified badge updated');
  assert.equal(pinnedAssumptions.hidden, true, 'common inputs panel remains hidden');
  assert.equal(emptyState.hidden, true, 'empty state hidden when modified inputs exist');
  assert.equal(filterStatus.textContent, '1 modified input', 'status displays 1 modified input');

  // Modify a common input (retail_commodity)
  run('editSetting("retail_commodity", 0.20);');
  assert.equal(tCtrlRetail.hidden, false, 'modified common input visible');
  assert.equal(tCtrlGas.hidden, true, 'unmodified common input remains hidden');
  assert.equal(pinnedAssumptions.hidden, false, 'common inputs panel visible with modified input');
  assert.equal(filterStatus.textContent, '2 modified inputs', 'status displays 2 modified inputs');

  // Reset south_panels
  run('resetSetting("south_panels");');
  assert.equal(tCtrlSouth.hidden, true, 'reset south_panels hides again');
  assert.equal(tSysGroup.hidden, true, 'system group without modified inputs hides again');
  assert.equal(filterStatus.textContent, '1 modified input', 'status updates to 1 modified input');

  // Save retail_commodity as shared default
  run('schema.defaults.retail_commodity = 0.20; applyControlFilters();');
  assert.equal(tCtrlRetail.hidden, true, 'saved default retail_commodity is no longer modified and hides');
  assert.equal(pinnedAssumptions.hidden, true, 'common inputs panel hides when all common inputs match defaults');
  assert.equal(filterStatus.textContent, 'No modified inputs. All values match shared defaults.');
  assert.equal(emptyState.hidden, false);

  // Remote sync updates modified state
  const remoteSyncState = {
    settings: { retail_commodity: 0.20, gas_commodity: 0.88, south_panels: 12 },
    defaults: { retail_commodity: 0.20, gas_commodity: 0.45, south_panels: 12 },
    records: run('schema.records'),
    quote: { line_items: [] }
  };
  context._remoteSyncState = remoteSyncState;
  run('applySharedState(_remoteSyncState);');
  assert.equal(tCtrlGas.hidden, false, 'remote-modified gas_commodity is visible in modified mode');
  assert.equal(pinnedAssumptions.hidden, false, 'common inputs panel unhides after remote sync');
  assert.equal(filterStatus.textContent, '1 modified input');

  // 8. Global search while in 'modified' mode (searches ALL inputs globally)
  run('updateSearch("Roof");');
  assert.equal(searchHint.hidden, false, 'shows "Searching all inputs" hint');
  assert.equal(tCtrlSouth.hidden, false, 'south_panels matches global search on source "Roof" despite unmodified and in modified mode');
  assert.equal(tSysGroup.hidden, false, 'system group matches global search');
  assert.equal(tSysGroup.open, true, 'system group auto-opens during search');
  assert.match(filterStatus.textContent, /input.*found/, 'status displays found inputs');

  // Global search matching description
  run('updateSearch("offer");');
  assert.equal(tCtrlQuoteTotal.hidden, false, 'quote_total matches global search on source "Offer"');

  // Global search respecting conditional visibility
  run('updateSearch("DHW");');
  assert.equal(tCtrlDhwScop.hidden, false, 'active dhw_scop matches search');
  assert.equal(tCtrlDhwResist.hidden, true, 'inactive dhw_resistance_efficiency remains hidden despite matching "DHW"');

  // Global search with no results
  run('updateSearch("nonexistent_keyword_xyz");');
  assert.equal(tSysGroup.hidden, true, 'empty category hidden on no match');
  assert.equal(tDemandGroup.hidden, true, 'empty category hidden on no match');
  assert.equal(tFinanceGroup.hidden, true, 'empty category hidden on no match');
  assert.equal(pinnedAssumptions.hidden, true, 'common section hidden on no match');
  assert.equal(emptyState.hidden, false, 'empty state visible on no match');
  assert.match(filterStatus.textContent, /No inputs match “nonexistent_keyword_xyz”/, 'status displays useful empty-state message');

  // Clearing search returns to selected mode ('modified')
  run('updateSearch("");');
  assert.equal(searchHint.hidden, true, 'search hint hidden after clearing search');
  assert.equal(tCtrlSouth.hidden, true, 'unmodified south_panels hidden again in modified mode');
  assert.equal(tCtrlGas.hidden, false, 'modified gas_commodity remains visible in modified mode');
  assert.equal(pinnedAssumptions.hidden, false, 'common section visible for modified gas_commodity');

  // 9. All-mode disclosure restoration
  run('setSidebarFilterMode("all");');
  tSysGroup.open = true;
  tDemandGroup.open = false;
  tFinanceGroup.open = true;
  run('recordAllModeDisclosures();');

  // Switch to view mode on behavior tab (auto-opens matching demand group)
  run('updateSidebarForTab("behavior"); setSidebarFilterMode("view");');
  assert.equal(tDemandGroup.hidden, false);
  assert.equal(tDemandGroup.open, true, 'demand group auto-opened in view mode');

  // Return to all mode: restores user's ordinary All-mode disclosures
  run('setSidebarFilterMode("all");');
  assert.equal(tSysGroup.open, true, 'system group restored to user open state');
  assert.equal(tDemandGroup.open, false, 'demand group restored to user collapsed state');
  assert.equal(tFinanceGroup.open, true, 'finance group restored to user open state');

  // 10. Preservation across renderControls()
  run('activeSearch = "panels"; sidebarFilterMode = "view"; renderControls();');
  assert.equal(run('sidebarFilterMode'), 'view', 'sidebar filter mode preserved across renderControls');
  assert.equal(run('activeSearch'), 'panels', 'active search query preserved across renderControls');

  // Scoping: Main view controls with data-control outside aside are not affected
  const mainControl = { dataset: { control: 'south_panels' }, textContent: 'Main view panel control', hidden: false };
  const mockAside = {
    querySelectorAll: (sel) => sel === '[data-control]' ? [tCtrlSouth] : sel === 'details.group' ? [tSysGroup] : []
  };
  context.document.querySelector = (sel) => sel === 'aside' ? mockAside : null;
  run('activeSearch = "something_else"; applyControlFilters();');
  assert.equal(mainControl.hidden, false, 'main-view control not hidden by sidebar filter');

  context.document.querySelector = () => null;

  // 11. Provenance status tag in Details disclosure & Save as shared default label
  const renderedRecord = run("control({name:'scop',unit:'multiplier',bounds:[1,8],group:'loads',status:'measured',source:'Heat pump meter',description:'Coefficient of performance'})");
  assert.ok(!renderedRecord.includes('<div class="control-header"><label>Scop <span class="tag'), 'provenance status tag is not in control-header label');
  assert.match(renderedRecord, /<div class="control-header"><label>Scop<\/label><button[^>]*class="control-reset"/, 'label and reset button remain in control-header');
  assert.match(renderedRecord, /<details class="control-details">[\s\S]*<div class="control-details-meta"><span class="tag measured">measured<\/span><small class="control-source"[^>]*>Source: Heat pump meter<\/small><\/div>/, 'provenance tag is inside control-details beside source');
  assert.match(renderedRecord, /<button type="button" data-save-default="scop"[^>]*>Save as shared default<\/button>/, 'default action is labeled Save as shared default');

  // 12. Input filter radiogroup keyboard navigation & accessible state
  run('setSidebarFilterMode("all");');
  assert.equal(modeBtns[0].getAttribute('tabindex'), '0', 'selected button has tabindex=0');
  assert.equal(modeBtns[1].getAttribute('tabindex'), '-1', 'unselected button has tabindex=-1');
  assert.equal(modeBtns[2].getAttribute('tabindex'), '-1', 'unselected button has tabindex=-1');
  assert.equal(modeBtns[0].getAttribute('aria-checked'), 'true');
  assert.equal(modeBtns[1].getAttribute('aria-checked'), 'false');
  assert.equal(modeBtns[2].getAttribute('aria-checked'), 'false');

  let focusedBtn = null;
  modeBtns.forEach(b => { b.focus = function() { focusedBtn = this; }; });

  // ArrowRight advances to next button ('view')
  modeBtns[0].onkeydown({ key: 'ArrowRight', preventDefault() {} });
  assert.equal(run('sidebarFilterMode'), 'view');
  assert.equal(modeBtns[1].tabIndex, 0);
  assert.equal(modeBtns[0].tabIndex, -1);
  assert.equal(modeBtns[1].getAttribute('aria-checked'), 'true');
  assert.equal(modeBtns[0].getAttribute('aria-checked'), 'false');
  assert.equal(focusedBtn, modeBtns[1], 'ArrowRight focuses next button');

  // End key jumps to last button ('modified')
  modeBtns[1].onkeydown({ key: 'End', preventDefault() {} });
  assert.equal(run('sidebarFilterMode'), 'modified');
  assert.equal(modeBtns[2].tabIndex, 0);
  assert.equal(modeBtns[1].tabIndex, -1);
  assert.equal(modeBtns[2].getAttribute('aria-checked'), 'true');
  assert.equal(focusedBtn, modeBtns[2], 'End key focuses last button');

  // Home key jumps to first button ('all')
  modeBtns[2].onkeydown({ key: 'Home', preventDefault() {} });
  assert.equal(run('sidebarFilterMode'), 'all');
  assert.equal(modeBtns[0].tabIndex, 0);
  assert.equal(modeBtns[2].tabIndex, -1);
  assert.equal(modeBtns[0].getAttribute('aria-checked'), 'true');
  assert.equal(focusedBtn, modeBtns[0], 'Home key focuses first button');

  // 13. Evidence-link reveal (revealAndFocusSetting)
  let focusedInput = null;
  const mockEditor = { focus() { focusedInput = this; } };
  const mockCtrlElement = {
    dataset: { control: 'south_panels' },
    parentElement: tSysGroup,
    classList: { classes: new Set(), add(c) { this.classes.add(c); }, contains(c) { return this.classes.has(c); }, remove(c) { this.classes.delete(c); } },
    querySelector: (sel) => sel.includes('input') ? mockEditor : null,
    scrollIntoView: () => { mockCtrlElement.scrolled = true; }
  };
  const mockAsideEl = {
    classList: { classes: new Set(), contains(c) { return this.classes.has(c); }, add(c) { this.classes.add(c); } }
  };
  const savedQuerySelector = context.document.querySelector;
  context.document.querySelector = (sel) => {
    if (sel === 'aside') return mockAsideEl;
    if (sel.includes('south_panels')) return mockCtrlElement;
    if (sel.includes('details.category-accordion')) return tSysGroup;
    return savedQuerySelector ? savedQuerySelector(sel) : null;
  };

  run('activeSearch = "other"; sidebarFilterMode = "modified";');
  tSysGroup.open = false;
  run('revealAndFocusSetting("south_panels");');

  assert.equal(run('activeSearch'), '', 'clears active search query');
  assert.equal(run('sidebarFilterMode'), 'all', 'chooses All inputs mode');
  assert.equal(tSysGroup.open, true, 'expands its category accordion');
  assert.equal(mockAsideEl.classList.contains('inputs-open'), true, 'expands mobile inputs on aside');
  assert.equal(mockCtrlElement.scrolled, true, 'scrolls control into view');
  assert.equal(mockCtrlElement.classList.contains('highlight-pulse'), true, 'pulses the control element');
  assert.equal(focusedInput, mockEditor, 'focuses actual editor input');

  // Conditionally unavailable input explains rather than changing scenario values
  run('settings.dhw_heating_type = "heat_pump";');
  const prevSettingsJson = run('JSON.stringify(settings)');
  focusedInput = null;
  run('revealAndFocusSetting("dhw_resistance_efficiency");');
  assert.equal(run('JSON.stringify(settings)'), prevSettingsJson, 'scenario values are untouched and preserved');
  assert.match(ui.status.textContent, /unavailable because DHW heating type is set to heat pump/i, 'explains conditional unavailability');
  assert.equal(focusedInput, null, 'conditionally unavailable input is not focused');

  context.document.querySelector = savedQuerySelector;


  // Regression fixtures for percent input presentation, typing, change-only, precision, bounds, and empty scenario state
  run(`
  schema = {
    defaults: {
      retail_commodity: 0.12,
      real_discount_rate: 0.03,
      deduction_rate: 0.50
    },
    records: [
      { name: 'retail_commodity', unit: 'EUR/kWh', bounds: [0, 0.5], group: 'bill' },
      { name: 'real_discount_rate', unit: 'fraction/year', bounds: [0, 0.20], group: 'finance' },
      { name: 'deduction_rate', unit: 'fraction', bounds: [0, 0.50], group: 'tax' }
    ]
  };
  settings = { ...schema.defaults };
  renderControls();
  `);

  // 1. real_discount_rate display 3%
  const discControlMarkup = run("control(schema.records.find(r=>r.name==='real_discount_rate'))");
  assert.match(discControlMarkup, /value="3%"/, 'real_discount_rate displays 3% initially');
  assert.match(discControlMarkup, /<span class="unit">\/year<\/span>/, 'unit displays /year');
  assert.equal(run("FRIENDLY_NAMES['retail_commodity']"), 'Electricity price (including dispatching, pre-tax)', 'retail_commodity has updated friendly label');
  assert.equal(run("FRIENDLY_NAMES['retail_regulated']"), 'Electricity network and system charges (pre-tax)', 'retail_regulated has updated friendly label');
  assert.equal(run("FRIENDLY_NAMES['electric_excise']"), 'Electricity excise (accisa)', 'electric_excise has updated friendly label');

  const taxChoiceMarkup=run("control({...schema.records.find(r=>r.name==='deduction_rate'),choices:[0,.36,.5]})");
  assert.match(taxChoiceMarkup,/value="0.5"[^>]*>50%<\/option>/,'percentage choice displays 50% and retains canonical option value');
  run('$("controls").onchange')({target:{type:'select-one',dataset:{setting:'deduction_rate'},value:'0.36'}});
  assert.equal(run('settings.deduction_rate'),.36,'percent select keeps its canonical fraction');

  // 2. Type 4% = .04
  let discAriaInvalid = null;
  const mockClassList = {
    classes: new Set(),
    add(c) { this.classes.add(c); },
    remove(c) { this.classes.delete(c); },
    contains(c) { return this.classes.has(c); }
  };
  const testDiscInput = {
    type: 'text',
    dataset: { setting: 'real_discount_rate' },
    value: '4%',
    classList: mockClassList,
    setAttribute(k, v) { if (k === 'aria-invalid') discAriaInvalid = v; },
    removeAttribute(k) { if (k === 'aria-invalid') discAriaInvalid = null; }
  };
  run('$("controls").oninput')({ target: testDiscInput });
  assert.equal(run('settings.real_discount_rate'), 0.04, 'typing 4% updates settings to 0.04');
  assert.equal(discAriaInvalid, null, 'valid 4% input clears aria-invalid');

  // 3. Change-only input: paste or typed value directly triggers change without prior input
  testDiscInput.value = '5%';
  run('$("controls").onchange')({ target: testDiscInput });
  assert.equal(run('settings.real_discount_rate'), 0.05, 'change-only event parses typed draft and updates settings to 0.05');
  assert.equal(testDiscInput.value, '5%', 'canonical formatted value is set on change');

  // Also test change with plain number "6" (interpreted as 6%)
  testDiscInput.value = '6';
  run('$("controls").onchange')({ target: testDiscInput });
  assert.equal(run('settings.real_discount_rate'), 0.06, 'change-only event parses plain number as percentage');
  assert.equal(testDiscInput.value, '6%', 'formats to 6% on change');

  // 4. Invalid input does not mutate settings or set null
  testDiscInput.value = 'invalid_text';
  run('$("controls").oninput')({ target: testDiscInput });
  assert.equal(run('settings.real_discount_rate'), 0.06, 'invalid text does not corrupt settings');
  assert.equal(discAriaInvalid, 'true', 'invalid text sets aria-invalid');
  assert.equal(mockClassList.contains('is-invalid'), true, 'invalid text adds is-invalid class');

  // Blank on required field does not set null
  testDiscInput.value = '';
  run('$("controls").oninput')({ target: testDiscInput });
  assert.equal(run('settings.real_discount_rate'), 0.06, 'blank required field does not mutate settings to null');
  assert.equal(discAriaInvalid, 'true', 'blank required field is marked invalid');

  // 5. Unchanged high precision is preserved
  run('settings.real_discount_rate = 0.0325;');
  assert.equal(run('formatPercentValue(settings.real_discount_rate)'), '3.25%', 'formats high precision without unwanted rounding');
  assert.equal(run('settings.real_discount_rate'), 0.0325, 'untouched field retains exact fraction');

  run('settings.real_discount_rate = 0.03250000000123;');
  testDiscInput.value=run('formatPercentValue(settings.real_discount_rate)');
  run('$("controls").onchange')({target:testDiscInput});
  assert.equal(run('settings.real_discount_rate'),0.03250000000123,'unchanged high-precision display survives change without rounding canonical value');

  // 6. Arrow increments and bounds
  run('settings.real_discount_rate = 0.03;');
  testDiscInput.value = '3%';
  const triggerArrow = key => run('$("controls").onkeydown')({ target: testDiscInput, key, preventDefault() {} });
  triggerArrow('ArrowUp');
  assert.equal(run('settings.real_discount_rate'), 0.031, 'ArrowUp increments by step (0.001 -> 0.1%)');
  assert.equal(testDiscInput.value, '3.1%', 'ArrowUp updates displayed percentage');

  // Bounds check: lower bound at 0
  run('settings.real_discount_rate = 0.0005;');
  testDiscInput.value = '0.05%';
  triggerArrow('ArrowDown');
  assert.equal(run('settings.real_discount_rate'), 0, 'ArrowDown clamps to lower bound 0');
  assert.equal(testDiscInput.value, '0%', 'displays 0% at lower bound');

  // Bounds check: upper bound at 0.20
  run('settings.real_discount_rate = 0.1995;');
  testDiscInput.value = '19.95%';
  triggerArrow('ArrowUp');
  assert.equal(run('settings.real_discount_rate'), 0.20, 'ArrowUp clamps to upper bound 0.20');
  assert.equal(testDiscInput.value, '20%', 'displays 20% at upper bound');

  // 7. S7 Saved scenarios empty state hides section head and description
  run(source.slice(source.indexOf('function saveCurrentScenario('), source.indexOf('const textFields=')));
  const mockHead = { hidden: false };
  const mockDesc = { hidden: false };
  ui.savedScenariosArticle = {
    querySelector: sel => (sel === '.section-head' ? mockHead : (sel === 'p.muted' ? mockDesc : null))
  };
  ui.clearSaved = { hidden: false };
  const mockSavedTarget = { innerHTML: '', onclick: null };
  ui.saved = mockSavedTarget;

  run('saved = []; renderSaved();');
  assert.equal(mockHead.hidden, true, 'section head is hidden when saved is empty');
  assert.equal(mockDesc.hidden, true, 'description is hidden when saved is empty');
  assert.equal(ui.clearSaved.hidden, true, 'clearSaved is hidden when empty');
  assert.ok(mockSavedTarget.innerHTML.includes('No saved scenarios'), 'renders No saved scenarios row');
  assert.ok(mockSavedTarget.innerHTML.includes('data-save-current="true"'), 'has Save current scenario button');
  assert.equal(typeof mockSavedTarget.onclick, 'function', 'delegated target onclick is attached');

  // Verify delegated save handler
  run('getScopeCashMetrics = () => ({capexVal:100,npvObj:{median:200},pbObj:{median:3}}); summaryScope=\"solar\"; summaryCash=\"cash\"; localStorage.setItem = () => {}; current = { result: { layout: { kwp: 5 }, settings: {}, finance: {} } }; prompt = () => "Test scenario";');
  const initialSavedCount = run('saved.length');
  mockSavedTarget.onclick({ target: { dataset: { saveCurrent: 'true' } } });
  assert.equal(run('saved.length'), initialSavedCount + 1, 'delegated save button invokes save handler and saves scenario');
  assert.equal(run('saved[saved.length - 1].name'), 'Test scenario', 'saved scenario has expected name');

  // Verify populated state restores head and description
  run('renderSaved();');
  assert.equal(mockHead.hidden, false, 'section head is restored when scenarios exist');
  assert.equal(mockDesc.hidden, false, 'description is restored when scenarios exist');
  assert.equal(ui.clearSaved.hidden, false, 'clearSaved is restored when scenarios exist');
  assert.ok(mockSavedTarget.innerHTML.includes('saved-scenarios-table'), 'renders comparison grid when scenarios exist');

    console.log('Assumption control regression fixtures passed.');
}

runRegressions().catch(error => { console.error(error); process.exitCode = 1; });

