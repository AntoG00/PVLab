'use strict';
// Only display preferences reach browser persistence. Household snapshots are tab-local.
const localStorage=typeof HouseholdStorage!=='undefined'?new HouseholdStorage():globalThis.localStorage;
let householdClient=null,householdSetup=null,householdSyncInterval=null,householdViewsOpened=false;
const LEGACY_ID_MAP={overview:'summary',solar:'design',battery:'design',pareto:'design',loan:'financing',dayhour:'behavior',evidence:'assumptions',quote:'quote',contractor:'quote'};
const $=id=>typeof document!=='undefined'&&document?(document.getElementById(id)||document.getElementById(LEGACY_ID_MAP[id]||id)):null, esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const deepClone=obj=>typeof structuredClone==='function'?structuredClone(obj):JSON.parse(JSON.stringify(obj));
function settingValuesEqual(a,b){
 const sync=typeof SharedScenarioSync!=='undefined'?SharedScenarioSync:(typeof window!=='undefined'?window.SharedScenarioSync:null);
 if(sync&&typeof sync.equal==='function')return sync.equal(a,b);
 if(a===b)return true;
 if(a==null||b==null||typeof a!=='object'||typeof b!=='object')return false;
 return JSON.stringify(a)===JSON.stringify(b);
}
let schema,settings={},quote={line_items:[]},current=null,candidates=null,study=null,candidateFingerprint=null,candidateLoadId=0,requestId=0,timer,dayhourData=null,dayhourRequest=0,pendingDefaultConfirmation=null,searchDisclosure=null,activeSearch='',cashBasisInitialized=false,solarAppliedMessage='';
let sidebarFilterMode='all',allModeDisclosures=new Map(),currentViewName='summary';
let priorHouseholdScenario=null;

const HP_SPEC_KEYS=[
 'planning_heat_pump',
 'hp_nominal_kw',
 'hp_weather_sensitive',
 'hp_indoor_temp',
 'hp_setback_delta',
 'hp_schedule_mode',
 'hp_morning_on',
 'hp_morning_off',
 'hp_evening_on',
 'hp_evening_off',
 'hp_curve_outdoor_cold',
 'hp_curve_outdoor_mild',
 'hp_curve_flow_cold',
 'hp_curve_flow_mild',
 'hp_radiator_dt50_kw',
 'hp_radiator_exponent',
 'hp_return_drop',
 'hp_tank_litres',
 'hp_tank_target_temp',
 'hp_tank_inlet_temp',
 'hp_tank_ambient_temp',
 'hp_tank_loss_w_k',
 'hp_tank_deadband',
 'hp_tank_service_temp',
 'hp_tank_initial_temp',
 'hp_coil_approach',
 'hp_dhw_demand_mode',
 'hp_dhw_litres_day',
 'hp_prior_dhw_loss_fraction',
 'hp_draw_morning_on',
 'hp_draw_morning_off',
 'hp_draw_evening_on',
 'hp_draw_evening_off',
 'hp_draw_morning_fraction',
 'hp_reheat_mode',
 'hp_reheat_on',
 'hp_reheat_off',
 'hp_hygiene_enabled',
 'hp_hygiene_temp',
 'hp_hygiene_weekday',
 'hp_hygiene_hour',
 'hp_hygiene_duration_hours',
 'hp_backup_kw',
 'hp_backup_efficiency',
 'hp_external_pump_kw',
 'hp_standby_kw',
 'hp_min_modulation_fraction',
 'hp_cycling_penalty',
 'hp_distribution_loss_fraction',
 'hp_additional_defrost_penalty',
 'hp_additional_unit_aux_kw',
 'hp_capex',
 'hp_annual_maintenance',
 'hp_replacement_year',
 'hp_replacement_cost',
 'hp_electric_cooking',
 'hp_cooking_capex',
 'hp_disconnect_gas',
 'hp_manufacturer_map_json'
];
const HP_ALL_KEYS_SET=new Set(HP_SPEC_KEYS);
const HP_COST_KEYS_SET=new Set(['hp_capex','hp_annual_maintenance','hp_replacement_year','hp_replacement_cost','hp_cooking_capex','hp_disconnect_gas']);
const HP_LEGACY_KEYS=new Set([
 'scop','dhw_heating_type','dhw_scop','dhw_resistance_efficiency',
 'dhw_midday_fraction','heating_schedule_mode','heating_on_hour',
 'heating_off_hour','heating_evening_on_hour','heating_evening_off_hour',
 'electrification_capex','electrification_capex_include_baseline',
 'ac_heating_windows','ac_heating_additional_demand_pct'
]);

const VIEW_RELEVANT_INPUTS={
 summary:{keys:new Set(['load_scenario','inverter_kw','horizon_years','inflation','planning_heat_pump']),groups:new Set(['heat_pump'])},
 design:{groups:new Set(['layout','equipment','energy','heat_pump']),keys:new Set(['planning_heat_pump'])},
 financing:{groups:new Set(['finance','tax'])},
 behavior:{groups:new Set(['loads','heat_pump']),keys:new Set(['planning_heat_pump'])},
 prices:{groups:new Set(['bill','gas','prices','export','cer'])},
 assumptions:{all:true},
 quote:{groups:new Set(['cost']),keys:new Set(['inverter_kw'])}
};
const THEME_STORAGE_KEY='pv-lab-theme-v1';
function readThemePref(){
 try{
  if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.getItem!=='function')return null;
  const val=localStorage.getItem(THEME_STORAGE_KEY)||localStorage.getItem('pv_lab_theme')||localStorage.getItem('theme');
  if(val==='dark'||val==='light')return val;
 }catch(_){}
 return null;
}
function saveThemePref(theme){
 try{
  if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.setItem!=='function')return;
  localStorage.setItem(THEME_STORAGE_KEY,theme);
  localStorage.setItem('pv_lab_theme',theme);
 }catch(_){}
}
function getSystemTheme(){
 try{
  if(typeof window!=='undefined'&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches){
   return 'dark';
  }
 }catch(_){}
 return 'light';
}
function getEffectiveTheme(){
 const manual=readThemePref();
 if(manual)return manual;
 return getSystemTheme();
}
function syncThemeToggleUI(theme){
 if(typeof document==='undefined')return;
 const toggle=$('themeToggle');
 if(!toggle)return;
 const isDark=theme==='dark';
 if(toggle.setAttribute){
  toggle.setAttribute('aria-checked',isDark?'true':'false');
  toggle.setAttribute('aria-pressed',isDark?'true':'false');
 }
 if(toggle.classList&&toggle.classList.toggle){
  toggle.classList.toggle('active',isDark);
 }
 const icon=toggle.querySelector?toggle.querySelector('.theme-icon'):null;
 if(icon)icon.textContent=isDark?'☀️':'🌙';
 const label=toggle.querySelector?toggle.querySelector('.theme-label'):null;
 if(label)label.textContent=isDark?'Day':'Night';
 const desc=isDark?'Switch to light theme':'Switch to night theme';
 if(toggle.setAttribute){
  toggle.setAttribute('title',desc);
  toggle.setAttribute('aria-label',desc);
 }
}
function setTheme(theme,persist=false){
 const targetTheme=(theme==='dark'||theme==='light')?theme:'light';
 const isDark=targetTheme==='dark';
 if(typeof document!=='undefined'&&document.documentElement){
  if(document.documentElement.setAttribute)document.documentElement.setAttribute('data-theme',targetTheme);
  if(document.documentElement.classList&&document.documentElement.classList.toggle){
   document.documentElement.classList.toggle('theme-dark',isDark);
  }
 }
 if(typeof document!=='undefined'&&document.body){
  if(document.body.classList&&document.body.classList.toggle){
   document.body.classList.toggle('theme-dark',isDark);
  }
 }
 if(persist)saveThemePref(targetTheme);
 syncThemeToggleUI(targetTheme);
 return targetTheme;
}
function toggleTheme(){
 const curTheme=(typeof document!=='undefined'&&document.documentElement&&document.documentElement.getAttribute&&document.documentElement.getAttribute('data-theme'))||getEffectiveTheme();
 const nextTheme=curTheme==='dark'?'light':'dark';
 setTheme(nextTheme,true);
 return nextTheme;
}
let themeInitialized=false;
function initTheme(){
 const effective=getEffectiveTheme();
 setTheme(effective,false);
 const toggle=$('themeToggle');
 if(toggle){
  toggle.onclick=()=>toggleTheme();
 }
 if(!themeInitialized&&typeof window!=='undefined'&&window.matchMedia){
  try{
   const media=window.matchMedia('(prefers-color-scheme: dark)');
   const listener=e=>{
    if(!readThemePref()){
     setTheme(e.matches?'dark':'light',false);
    }
   };
   if(media.addEventListener)media.addEventListener('change',listener);
   else if(media.addListener)media.addListener(listener);
   themeInitialized=true;
  }catch(_){}
 }
}
const SUMMARY_STORAGE_KEY='pv-lab-summary-prefs-v1';
const VALID_SUMMARY_SCOPES=new Set(['solar','package']);
const VALID_SUMMARY_CASH=new Set(['cash','loan']);
function readSummaryPrefs(){
 try{
  if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.getItem!=='function')return {scope:'solar',cash:'cash'};
  const raw=localStorage.getItem(SUMMARY_STORAGE_KEY);
  if(!raw)return {scope:'solar',cash:'cash'};
  const parsed=JSON.parse(raw);
  if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))return {scope:'solar',cash:'cash'};
  const scope=VALID_SUMMARY_SCOPES.has(parsed.scope)?parsed.scope:'solar';
  const cash=VALID_SUMMARY_CASH.has(parsed.cash)?parsed.cash:'cash';
  return {scope,cash};
 }catch{
  return {scope:'solar',cash:'cash'};
 }
}
function persistSummaryPrefs(scope=summaryScope,cash=summaryCash){
 try{
  if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.setItem!=='function')return;
  const validScope=VALID_SUMMARY_SCOPES.has(scope)?scope:'solar';
  const validCash=VALID_SUMMARY_CASH.has(cash)?cash:'cash';
  localStorage.setItem(SUMMARY_STORAGE_KEY,JSON.stringify({scope:validScope,cash:validCash}));
 }catch{}
}
const _initialSummaryPrefs=readSummaryPrefs();
let summaryScope=_initialSummaryPrefs.scope,summaryCash=_initialSummaryPrefs.cash;
let sharedSync=null,sharedUiReady=false,serverInstanceId=null,loanSaved=readLoanSimulations(),loanPending=false,acceptedInputFingerprint=null,loanSolveToken=0,loanSolveResult=null;
let saved=[];try{saved=JSON.parse((typeof localStorage!=='undefined'&&localStorage&&typeof localStorage.getItem==='function'?localStorage.getItem('pv-lab-scenarios-v1'):null)||'[]');}catch{saved=[];}
const defaultRoofLimits={south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5};
const ROOF_LIMITS_MIGRATION_KEY='pv-lab-roof-limits-migrated-v1';
function migrateRoofLimits(){try{if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.getItem!=='function')return;if(localStorage.getItem(ROOF_LIMITS_MIGRATION_KEY))return;if(typeof localStorage.setItem!=='function')return;const raw=localStorage.getItem('pv-lab-optimizer-v1');if(raw){const saved=JSON.parse(raw);if(saved&&typeof saved==='object'){saved.panel_maxima={...defaultRoofLimits};localStorage.setItem('pv-lab-optimizer-v1',JSON.stringify(saved));}}localStorage.setItem(ROOF_LIMITS_MIGRATION_KEY,'1');}catch{}}
const optimizerDefaults={objective:'autarky',battery_min_modules:0,battery_max_modules:4,panel_maxima:{...defaultRoofLimits},include_theoretical:true,load_scenario:null,chart_x:'autarky',chart_y:'npv_eur'};
let optimizerPrefs=readOptimizerPrefs(),optimizerResult=null,optimizerFingerprint=null,optimizerRequestId=0;
function readOptimizerPrefs(){migrateRoofLimits();try{const saved=JSON.parse((typeof localStorage!=='undefined'&&localStorage&&typeof localStorage.getItem==='function'?localStorage.getItem('pv-lab-optimizer-v1'):null)||'{}');return {...optimizerDefaults,...saved,_loadScenarioExplicit:Object.hasOwn(saved,'load_scenario'),panel_maxima:{...defaultRoofLimits,...(saved.panel_maxima||{})}};}catch{return {...optimizerDefaults,_loadScenarioExplicit:false,panel_maxima:{...defaultRoofLimits}};}}
function readLoanSimulations(){try{const value=JSON.parse(localStorage.getItem('pv-lab-loan-simulations-v1')||'[]');return Array.isArray(value)?value.filter(x=>x&&x.name&&x.settings&&x.result):[];}catch{return [];}}
const nf=(n,d=0)=>n==null?'—':Number(n).toLocaleString('en-IE',{maximumFractionDigits:d,minimumFractionDigits:d});
const fmt=(n,type='number')=>n==null?'—':typeof type==='number'?nf(n,type):type==='eur'?(n<0?'−€'+nf(-n):'€'+nf(n)):type==='eur2'?(n<0?'−€'+nf(-n,2):'€'+nf(n,2)):type==='pct'?nf(n*100,1)+'%':type==='lcoe'?'€'+nf(n,3):type==='years'?nf(n,1)+' y':nf(n);
const range=(r,type='number')=>!r?'—':r.min==null?(type==='years'?'Not reached':'—'):fmt(r.min,type)+(r.max==null?' – beyond horizon':Math.abs(r.max-r.min)<1e-7?'':' – '+fmt(r.max,type));
function formatPaybackYearsValue(val, unit = ' y') {
  const num = (val && typeof val === 'object') ? val.median : val;
  if (num !== null && num !== undefined && Number.isFinite(Number(num))) {
    return `${nf(Number(num), 1)}${unit}`;
  }
  return num === null ? 'Not reached' : '—';
}
const title=s=>typeof s!=='string'?'':s==='actual_price_future_factor'?'Export Price Future Factor':s==='export_price_future_years'?'Years to Ramp Future Factor':s==='loan_mode'?'Loan input':s==='loan_max_monthly_payment'?'Maximum monthly payment':s==='dhw_heating_type'?'DHW equipment':s==='summer_ac_multiplier'?'Summer AC Multiplier':s==='heating_schedule_mode'?'Heating schedule':s==='ac_heating_windows'?'AC Heating Operating Windows':s==='ac_heating_additional_demand_pct'?'Additional AC Heating Demand':s==='heating_on_hour'?'Morning heating on hour':s==='heating_off_hour'?'Morning heating off hour':s==='heating_evening_on_hour'?'Evening heating on hour':s==='heating_evening_off_hour'?'Evening heating off hour':FRIENDLY_NAMES[s]||(s.startsWith('hp_')?title(s.slice(3)):s.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase()));
// Selected-profile presentation; legacy adapters keep their own axes.
let activeResultProfile=null,activeResultJob=null;
function profileRoofRows(){
 return (typeof activeResultProfile!=='undefined'&&activeResultProfile)?activeResultProfile.roofs.map(r=>({key:r.id,label:r.label,max_kwp:r.max_kwp})):roofComparisonRows;
}
function profileCounts(candidate=null){
 if(!activeResultProfile)return candidate?.roof_counts||{};
 const counts=candidate?(candidate.settings?.roof_panels||candidate.roof_panels||candidate.roof_counts):settings.roof_panels;
 const watts=Number(candidate?.settings?.panel_watts??settings.panel_watts);
 if(!Number.isFinite(watts)||watts<=0)throw Error('Invalid panel rating.');
 const ids=new Set(activeResultProfile.roofs.map(r=>r.id));
 if(!counts||Object.keys(counts).some(id=>!ids.has(id))||[...ids].some(id=>!Object.hasOwn(counts,id)))throw Error('Unknown or missing roof plane. Re-run for the selected profile.');
 for(const r of activeResultProfile.roofs){const n=counts[r.id];if(!Number.isInteger(n)||n<0||n*watts/1000>r.max_kwp+1e-9)throw Error('Invalid roof panel capacity.');}
 return {...counts};
}
function renderProfileMarketMetadata(profile,catalog){
 const m=profile?.market||{};
 const mode={shape_scaled:'mean-one normalized shape × selected annual price level',absolute_proxy:'signed raw prices; no extra capture factor',flat:'explicit flat benchmark'}[m.valuation_mode]||'Valuation unavailable';
 return `<p>Zone ${esc(m.zone??'unavailable')} · Series ${esc(m.series??'unavailable')} · ${esc(m.window_start??'unavailable')} to ${esc(m.window_end??'unavailable')} (exclusive). ${esc(mode)}. Units: EUR/kWh; display EUR/MWh (×1000).</p><p>Pinned source revision: ${esc(m.price_revision??'explicit flat')}. Available catalogue revision: ${esc(catalog?.revision??'unavailable')}.</p><p>PUN export is a comparison-only benchmark. Fixed retail only; indexed retail billing is unsupported.</p>`;
}
function renderProfileDisclosures(presentation){
 if(!presentation)return '<p>Result quality metadata pending calculation.</p>';
 const q=presentation.quality||{},fraction=v=>Number.isFinite(v)?nf(100*v,1)+'%':'unavailable';
 const touHtml=presentation.load_provenance?.tou_mode==='arera_f1_f2_f3'?'<p>Hourly shape: ARERA F1/F2/F3 bands</p>':'';
 return `<p>Profile ${esc(presentation.profile_id)} · input revision ${esc(presentation.input_revision)}</p><p>Measured training shape ${fraction(q.measured_training_fraction)}; synthetic training shape ${fraction(q.synthetic_training_fraction)}; projected target output ${fraction(q.projected_output_fraction)}.</p>${touHtml}<details><summary>Weather, thermal, fiscal and data limitations</summary><p>Weather years: ${esc((presentation.weather_years||[]).join(', '))}. ${esc(presentation.solar_loss)} Additional shading fractions: ${esc(JSON.stringify(presentation.shading_fractions||{}))}. Provider system efficiency (fraction) by plane/year: ${esc(presentation.solar_parameters?JSON.stringify(presentation.solar_parameters):'unavailable in source metadata')}.</p><p>Thermal baseline/replacement: ${esc((presentation.thermal||[]).map(u=>u.id+': '+u.carrier+' / '+u.action).join('; ')||'none')}. Fiscal rule ${esc(presentation.fiscal?.rule_revision??'unavailable')}, as of ${esc(presentation.fiscal?.as_of??'unavailable')}, region ${esc(presentation.fiscal?.region??'not applicable')}, ${esc(presentation.fiscal?.mode??'unavailable')}.</p>`+(presentation.limitations||[]).map(x=>`<p>${esc(x)}</p>`).join('')+'</details>';
}
function updateActionableWarnings(customMessage=null){
 if(!activeResultProfile){
  if($('profileResultsPanel'))$('profileResultsPanel').hidden=true;
  return;
 }
 const fiscal=activeResultProfile.fiscal?.parameters;
 const year=Number(settings.installation_year);
 const horizon=Number(settings.horizon_years);
 const needsDeduction=Boolean(fiscal?.deduction_rate>0&&!settings.deduction_eligible_confirmed);
 const needsPmg=Boolean(settings.pmg_election&&!settings.pmg_eligible_confirmed);
 const horizonOutsidePinnedDates=Boolean(fiscal&&(String(year+1)+'-01-01'<fiscal.effective_from||String(year+horizon)+'-12-31'>=fiscal.effective_to));
 const needsProjection=Boolean(horizonOutsidePinnedDates&&!settings.unsupported_fiscal_year_acknowledged);
 const isInvalidated=Boolean(customMessage||current===null||!activeResultJob);

 const panel=$('profileResultsPanel');
 const warnEl=$('profileActionableWarning');
 const fieldset=$('profileConfirmationsFieldset');
 const actions=$('profileActionButtons');

 if($('lbl_pmg_eligible_confirmed'))$('lbl_pmg_eligible_confirmed').hidden=!settings.pmg_election;
 if($('lbl_deduction_eligible_confirmed'))$('lbl_deduction_eligible_confirmed').hidden=!(fiscal?.deduction_rate>0);
 if($('lbl_unsupported_fiscal_year_acknowledged'))$('lbl_unsupported_fiscal_year_acknowledged').hidden=!horizonOutsidePinnedDates;

 const hasConfirmations=needsDeduction||needsPmg||needsProjection;
 if(fieldset)fieldset.hidden=!hasConfirmations;
 if(actions)actions.hidden=!isInvalidated;

 const messages=[];
 if(customMessage)messages.push(customMessage);
 else if(isInvalidated)messages.push('Inputs modified or calculation pending. Rerun the selected revision to update results.');
 if(needsEligibilityWarning(needsDeduction,needsPmg))messages.push('Explicit scenario confirmation required for elected fiscal parameters.');
 if(needsProjection)messages.push('Selected project horizon exceeds pinned fiscal dates. Confirm assumption projection to proceed.');

 if(warnEl){
  warnEl.textContent=messages.join(' ');
  warnEl.hidden=messages.length===0;
 }

 const shouldShow=Boolean(messages.length>0||hasConfirmations||isInvalidated);
 if(panel)panel.hidden=!shouldShow;
}
function needsEligibilityWarning(deduction,pmg){return deduction||pmg;}
function invalidateProfileResults(message='Inputs changed. Re-run calculations.'){
 requestId++;householdClient.invalidate();
 const main=document.querySelector('main');if(main)main.hidden=true;
 current=null;activeResultJob=null;acceptedInputFingerprint=null;candidateEvalCache.clear();empiricalCache={};
 markSolarSizingStale();markBatterySizingStale();clearOptimizerResults();
 const header=$('profileResultHeader');if(header)header.textContent=message;
 const headerAssumptions=$('profileResultHeaderAssumptions');if(headerAssumptions)headerAssumptions.textContent=message;
 for(const id of ['ownedReportHtml','ownedReportMarkdown','ownedReportCsv'])if($(id))$(id).disabled=true;
 updateActionableWarnings(message);
}
function getRoofValidationError(s=settings,profile=(typeof activeResultProfile!=='undefined'?activeResultProfile:null)){
 if(!profile||!Array.isArray(profile.roofs))return null;
 const pw=Number(s?.panel_watts);
 if(!Number.isFinite(pw)||pw<=0)return 'Invalid panel rating.';
 const counts=s?.roof_panels||{};
 for(const r of profile.roofs){
  const n=Number(counts[r.id]??0);
  const max=Math.floor((r.max_kwp*1000+1e-7)/pw);
  if(!Number.isInteger(n)||n<0){
   return `Roof plane '${r.label}' panel count must be a non-negative whole number.`;
  }
  if(n>max){
   return `Roof plane '${r.label}' (${n} panels at ${pw} W = ${(n*pw/1000).toFixed(2)} kWp) exceeds ${r.max_kwp} kWp maximum capacity (${max} panels).`;
  }
 }
 return null;
}
function shouldBlockRoofRecompute(s=settings,profile=(typeof activeResultProfile!=='undefined'?activeResultProfile:null)){
 return Boolean(getRoofValidationError(s,profile));
}
function showRoofValidationError(err){
 const errEl=$('roofValidationMessage');
 if(errEl){
  errEl.textContent=err||'';
  errEl.hidden=!err;
 }
 const errContainer=$('error');
 if(errContainer&&err){
  errContainer.textContent=err;
  errContainer.hidden=false;
 }
}
function renderProfileRoofControls(){
 const root=$('profileRoofControls');if(!root)return;
 if(!activeResultProfile||!Array.isArray(activeResultProfile.roofs)||!activeResultProfile.roofs.length){root.textContent='';root.hidden=true;return;}
 root.textContent='';root.hidden=false;
 const pw=Number(settings.panel_watts);
 for(const r of activeResultProfile.roofs){
  const maxCount=Math.floor((r.max_kwp*1000+1e-7)/pw);
  const currentVal=Number(settings.roof_panels?.[r.id]??0);
  const isOver=currentVal>maxCount||!Number.isInteger(currentVal)||currentVal<0;

  const label=document.createElement('label');
  label.className=isOver?'roof-control-label is-invalid':'roof-control-label';
  label.textContent=r.label+' ('+r.id+') · '+r.max_kwp+' kWp maximum';

  const input=document.createElement('input');
  input.type='number';input.min='0';input.step='1';input.max=String(maxCount);
  input.value=String(settings.roof_panels?.[r.id]??0);
  input.dataset.roofId=r.id;

  if(isOver){
   input.setCustomValidity(`Plane capacity exceeded (${currentVal} panels > ${maxCount} max at ${pw} W). Reduce panel count.`);
   input.setAttribute('aria-invalid','true');
   input.classList.add('is-invalid');
  }

  input.oninput=()=>{
   const n=Number(input.value);
   if(input.value.trim()===''||!Number.isInteger(n)||n<0||n>maxCount){
    input.setCustomValidity(`Enter a whole panel count up to ${maxCount}.`);
    input.setAttribute('aria-invalid','true');
    input.classList.add('is-invalid');
   }else{
    input.setCustomValidity('');
    input.removeAttribute('aria-invalid');
    input.classList.remove('is-invalid');
   }
  };

  input.onchange=()=>{
   const n=Number(input.value);
   if(input.value.trim()===''||!Number.isInteger(n)||n<0||n>Number(input.max)){
    input.setCustomValidity(`Plane capacity exceeded (${input.value} panels > ${input.max} max at ${pw} W). Enter a whole count up to ${input.max}.`);
    input.setAttribute('aria-invalid','true');
    input.classList.add('is-invalid');
    input.reportValidity();
    showRoofValidationError(getRoofValidationError({...settings,roof_panels:{...settings.roof_panels,[r.id]:n}}));
    return;
   }
   input.setCustomValidity('');
   input.removeAttribute('aria-invalid');
   input.classList.remove('is-invalid');
   settings.roof_panels={...settings.roof_panels,[r.id]:n};
   editSetting('roof_panels',settings.roof_panels);
  };
  label.appendChild(input);root.appendChild(label);
 }

 const errContainer=document.createElement('div');
 errContainer.id='roofValidationMessage';
 errContainer.className='hp-error-message roof-error-message';
 const errText=getRoofValidationError(settings);
 if(errText){
  errContainer.textContent=errText;
  errContainer.hidden=false;
 }else{
  errContainer.textContent='';
  errContainer.hidden=true;
 }
 root.appendChild(errContainer);
}
async function downloadOwnedReport(format){
 if(!activeResultJob||getExportBlockReason('download report'))throw Error('Re-run the selected revision before downloading a report.');
 const job=activeResultJob,generation=householdClient.generation,revision=householdClient.revision;
 await householdClient.waitJob(job.id,{generation});
 const path=format==='html'?'/report':'/report.'+format;
 // Fetch the report through the same cookie/CSRF session; server rechecks ownership/revision.
 const response=await householdClient.fetcher(path+'?job_id='+encodeURIComponent(job.id),{credentials:'same-origin'});
 if(generation!==householdClient.generation||revision!==householdClient.revision)throw householdClient.stale();
 if(!response.ok){invalidateProfileResults('Report is stale or unavailable. Re-run the selected profile.');throw Error('Report unavailable ('+response.status+'). Re-run.');}
 const text=await response.text();
 if(generation!==householdClient.generation||revision!==householdClient.revision)throw householdClient.stale();
 download(text,'pv-lab-report.'+format,format==='html'?'text/html':format==='csv'?'text/csv':'text/markdown');
}
function setupProfileResults(){
 if(!activeResultProfile)return;
 settings.roof_panels=settings.roof_panels||Object.fromEntries(activeResultProfile.roofs.map(r=>[r.id,0]));
 const limits=Object.fromEntries(activeResultProfile.roofs.map(r=>[r.id,Math.floor((r.max_kwp*1000+1e-7)/Number(settings.panel_watts))]));
 optimizerPrefs.panel_maxima=limits;
 document.querySelectorAll('.roof-limit-controls label').forEach(node=>{node.hidden=true;});
 for(const id of ['resetRoofLimits','solarIncludeTheoretical'])if($(id))$(id).closest('label')?$(id).closest('label').hidden=true:$(id).hidden=true;
 renderProfileRoofControls();
 if($('roofLimitsArticle')?.appendChild&&$('profileRoofControls'))$('roofLimitsArticle').appendChild($('profileRoofControls'));
 for(const key of ['pmg_eligible_confirmed','deduction_eligible_confirmed','unsupported_fiscal_year_acknowledged'])if($(key)){$(key).checked=Boolean(settings[key]);$(key).onchange=e=>{editSetting(key,e.target.checked);updateActionableWarnings();};}
 if($('solarDeduction')){$('solarDeduction').value=String(activeResultProfile.fiscal.parameters.deduction_rate);$('solarDeduction').disabled=true;$('solarDeduction').title='Pinned fiscal rule; edit and compile the profile to change this rate.';}
 const metadata=$('profileMarketMetadata');if(metadata)metadata.innerHTML=renderProfileMarketMetadata(activeResultProfile,schema?.catalog);
 const makeOptions=(id,values)=>{const select=$(id);if(!select)return;select.textContent='';for(const value of values){const option=document.createElement('option');option.value=value;option.textContent=value;select.appendChild(option);}select.onchange=()=>invalidateProfileResults('Market selection changed. Edit household setup, save and compile the new market revision before rerunning.');};
 makeOptions('profileMarketZone',(schema?.catalog?.series||[]).filter(s=>s!=='PUN'));
 makeOptions('profileMarketSeries',schema?.catalog?.series||[]);makeOptions('profileMarketWindow',(schema?.catalog?.windows||[]).map(w=>w.join(' / ')));makeOptions('profileMarketValuation',['shape_scaled','absolute_proxy','flat']);
 if($('profileMarketZone'))$('profileMarketZone').value=activeResultProfile.market.zone;
 if($('profileMarketSeries'))$('profileMarketSeries').value=activeResultProfile.market.series;
 if($('profileMarketValuation'))$('profileMarketValuation').value=activeResultProfile.market.valuation_mode;
 if($('refreshProfileCatalog'))$('refreshProfileCatalog').onclick=async()=>{invalidateProfileResults('Catalogue refreshed. Save and compile a new pinned market revision in household setup, then rerun.');schema=await api('/api/schema');if(metadata)metadata.innerHTML=renderProfileMarketMetadata(activeResultProfile,schema.catalog);};
 for(const [id,format] of [['ownedReportHtml','html'],['ownedReportMarkdown','md'],['ownedReportCsv','csv']])if($(id)){$(id).disabled=true;$(id).onclick=()=>downloadOwnedReport(format).catch(showError);}
 if($('saveProfileMarket'))$('saveProfileMarket').onclick=async()=>{try{
  const profile=deepClone(activeResultProfile),window=($('profileMarketWindow')?.value||'').split(' / ');
  profile.market={...profile.market,zone:$('profileMarketZone')?.value||profile.market.zone,series:$('profileMarketSeries')?.value||profile.market.series,valuation_mode:$('profileMarketValuation')?.value||profile.market.valuation_mode,window_start:window[0]||profile.market.window_start,window_end:window[1]||profile.market.window_end,price_revision:$('profileMarketValuation')?.value==='flat'?null:schema?.catalog?.revision};
  await householdClient.profileRequest('',{profile},{method:'PATCH'});householdClient.invalidate();clearHouseholdView();
 }catch(error){showError(error);}};
 if($('cancelProfileJob'))$('cancelProfileJob').onclick=()=>householdClient.cancelJob().catch(showError);
 if($('retryProfileJob'))$('retryProfileJob').onclick=()=>recompute();
 // Standalone legacy battery searches do not support v2 planes; joint solar search does.
 if($('runBatterySizing')){$('runBatterySizing').disabled=true;$('runBatterySizing').title='Use joint solar and storage sizing for profile planes.';}
 updateActionableWarnings();
}
function acceptProfilePresentation(response,job){
 if(!activeResultProfile)return response;
 const presentation=response?._presentation;
 if(!presentation||presentation.profile_id!==householdClient.profileId||presentation.input_revision!==householdClient.revision)throw householdClient.stale();
 if(job){Object.defineProperty(response,'_ownedJob',{value:job,enumerable:false});}
 return response;
}
const api=async(path,body)=>{
 if(householdClient){
  const generation=householdClient.generation;
  if(['/api/state','/api/defaults','/api/schema'].includes(path.split('?')[0])){
   if(!householdClient.profileId)throw Error('Select a household profile first.');
   if(body===undefined)path+=(path.includes('?')?'&':'?')+'profile_id='+encodeURIComponent(householdClient.profileId);
   else body={...body,profile_id:householdClient.profileId,revision:householdClient.revision};
  }else if(body!==undefined&&path.startsWith('/api/')&&!path.startsWith('/api/session')){
   if(!householdClient.ready)throw Error('PROFILE_NOT_READY: complete household setup before evaluation.');
   body={...body,profile_id:householdClient.profileId,revision:householdClient.revision};
  }
  const submit=async()=>{
   const result=await householdClient.request(path,body);
  if(result.revision&&['/api/state','/api/defaults'].includes(path.split('?')[0]))householdClient.revision=result.revision;
  if(result.id&&['queued','running'].includes(result.state)){
   await householdClient.waitJob(result.id,{generation});
   const response=await householdClient.request('/api/jobs/'+encodeURIComponent(result.id)+'/result');
   return acceptProfilePresentation(response,result);
  }
  return result;
  };
  const heavy=body!==undefined&&!['/api/state','/api/defaults','/api/schema'].includes(path.split('?')[0]);
  return heavy?householdClient.runComputation(generation,submit):submit();
 }
 const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const obj=await r.json();if(!r.ok){const error=Error(obj.error||r.statusText);error.status=r.status;throw error;}return obj;
};
const ALLOWLISTED_ERROR_CODES = new Set([
  'COMPUTE_EXCEPTION', 'COMPUTE_TIMEOUT', 'COMPUTE_MEMORY', 'PROCESS_EXIT',
  'INTERRUPTED', 'QUEUE_TIMEOUT', 'SOLAR_TIMEOUT', 'SOLAR_NETWORK_ERROR',
  'SOLAR_DATA_GAPS', 'SOLAR_PROVIDER_ERROR', 'JOB_FAILED'
]);
const ALLOWLISTED_OPERATIONS = new Set([
  'compute', 'evaluate', 'quote', 'loan', 'dayhour', 'summary-sensitivity', 'matrix', 'study', 'solar'
]);
const ALLOWLISTED_ERROR_NAMES = new Set([
  'ComputationError', 'WorkerProcessDied', 'WorkerMemoryExceeded',
  'WorkerDeadlineExceeded', 'QueueTimeout', 'ServerInterrupted',
  'TimeoutError', 'ConnectionError', 'SolarDataGapError',
  'ZeroDivisionError', 'ValueError', 'TypeError', 'KeyError',
  'IndexError', 'OverflowError', 'FloatingPointError', 'RuntimeError'
]);
function generateBugReport(info){
 const diag=info?.diagnostic||{};
 const rawCode=info?.code||diag.error_code||'';
 const code=ALLOWLISTED_ERROR_CODES.has(rawCode)?rawCode:'JOB_FAILED';
 const rawOp=diag.operation||'';
 const operation=ALLOWLISTED_OPERATIONS.has(rawOp)?rawOp:'evaluate';
 const rawName=diag.error_name||'';
 const errorType=ALLOWLISTED_ERROR_NAMES.has(rawName)?rawName:'ComputationError';

 const rawVersion=String(info?.version||diag.version||'').trim();
 const version=/^[0-9]+(\.[0-9]+)*([a-zA-Z0-9_.-]+)?$/.test(rawVersion)?rawVersion:'unknown';

 const rawBuildId=String(info?.modelBuildId||diag.model_build_id||'').trim();
 const modelBuildId=/^[0-9a-fA-F]{8,64}$/.test(rawBuildId)?rawBuildId:'unknown';

 const rawJobId=String(info?.jobId||diag.job_id||'').trim();
 const jobId=/^[0-9a-zA-Z_-]{8,64}$/.test(rawJobId)?rawJobId:'unknown';

 const actionableMessage='Calculation failed. Review the error code and stack summary below.';

 const lines=[
  '### PV Lab Bug Report',
  '',
  '- **Version:** '+version,
  '- **Model Build ID:** '+modelBuildId,
  '- **Job Correlation ID:** '+jobId,
  '- **Operation:** '+operation,
  '- **Error Code:** '+code,
  '- **Error Type:** '+errorType,
  '- **Actionable Message:** '+actionableMessage,
  ''
 ];

 const rawRepo=String(info?.repoUrl||diag.repo_url||'').trim();
 if(/^https:\/\/github\.com\/[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(rawRepo)){
  lines.push('- **Repository:** '+rawRepo);
  lines.push('');
 }

 if(Array.isArray(diag.traceback)&&diag.traceback.length>0){
  const safeFrames=[];
  for(const frame of diag.traceback.slice(0,12)){
   if(typeof frame==='string'&&/^feasibility\/(?:[A-Za-z_][A-Za-z0-9_]*\/)*[A-Za-z_][A-Za-z0-9_]*\.py:\d+ in [A-Za-z_<>][A-Za-z0-9_<>]*$/.test(frame)){
    safeFrames.push(frame.slice(0,120));
   }
  }
  if(safeFrames.length>0){
   lines.push('#### Stack Summary (repo-relative)');
   lines.push('```text');
   for(const frame of safeFrames){
    lines.push(frame);
   }
   lines.push('```');
   lines.push('');
  }
 }

 lines.push('#### What happened?');
 lines.push('[Please describe what you were doing when the error occurred]');
 return lines.join('\n');
}
const showError=e=>{
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile&&e.status===429){
  $('syncStatus').textContent='Additional results pending · server quota reached. Retry after '+(e.retryAfter||60)+' seconds.';
  return;
 }
 const errContainer=$('error');
 if(!errContainer)return;
 errContainer.textContent='';
 errContainer.hidden=false;

 const msg=e?.message||String(e);
 const isJobFailure=Boolean(e?.jobId||e?.diagnostic||(e?.code&&typeof e.code==='string'&&(e.code.startsWith('COMPUTE_')||['PROCESS_EXIT','INTERRUPTED','QUEUE_TIMEOUT','SOLAR_TIMEOUT','SOLAR_NETWORK_ERROR','SOLAR_DATA_GAPS','SOLAR_PROVIDER_ERROR'].includes(e.code)))||msg.startsWith('JOB_FAILED:'));

 if(isJobFailure){
  const sysTitle=$('systemTitle');
  if(sysTitle){
   sysTitle.removeAttribute?.('aria-busy');
   if(sysTitle.textContent?.includes('Calculating')){
    sysTitle.textContent='Calculation failed';
   }
  }
  const hpCards=$('hpCardsEl');
  if(hpCards&&hpCards.textContent?.includes('Calculating')){
   hpCards.textContent='';
   const doc=errContainer.ownerDocument||document;
   const failedCard=doc.createElement('div');
   failedCard.className='card';
   const lbl=doc.createElement('div');
   lbl.className='label';
   lbl.textContent='Hourly dispatch';
   failedCard.appendChild(lbl);
   const val=doc.createElement('div');
   val.className='value bad';
   val.textContent='Failed';
   failedCard.appendChild(val);
   const sm=doc.createElement('small');
   sm.textContent='Review calculation error and inputs';
   failedCard.appendChild(sm);
   hpCards.appendChild(failedCard);
  }
  const statusEl=$('status');
  if(statusEl&&(statusEl.textContent?.includes('Calculating')||statusEl.textContent?.includes('Computing'))){
   statusEl.textContent='Calculation failed';
  }
 }

 if(msg.includes('PROFILE_NOT_READY')||msg.toLowerCase().includes('profile not ready')){
  errContainer.textContent='Profile not ready: complete the setup sequence (Step 1: Save draft → Step 2: Check solar coverage → Step 3: Generate data) before evaluation.';
  return;
 }

 if(!isJobFailure){
  errContainer.textContent=msg;
  return;
 }

 const doc=errContainer.ownerDocument||document;
 const headerDiv=doc.createElement('div');
 headerDiv.className='error-header';
 const strong=doc.createElement('strong');
 strong.textContent='Calculation failed: ';
 headerDiv.appendChild(strong);
 const msgSpan=doc.createElement('span');
 msgSpan.textContent=e?.diagnostic?.actionable_message||msg;
 headerDiv.appendChild(msgSpan);
 errContainer.appendChild(headerDiv);

 const diagDiv=doc.createElement('div');
 diagDiv.className='job-failure-diagnostic';

 const summaryP=doc.createElement('p');
 summaryP.className='diagnostic-summary';
 summaryP.textContent='Sanitized diagnostic details (safe to share; no personal data, coordinates, or inputs):';
 diagDiv.appendChild(summaryP);

 const bugReportText=generateBugReport(e);

 const pre=doc.createElement('pre');
 pre.className='diagnostic-pre';
 pre.setAttribute('tabindex','0');
 pre.setAttribute('aria-label','Sanitized diagnostic bug report');
 pre.textContent=bugReportText;
 diagDiv.appendChild(pre);

 const actionsDiv=doc.createElement('div');
 actionsDiv.className='diagnostic-actions';

 const copyBtn=doc.createElement('button');
 copyBtn.type='button';
 copyBtn.className='button btn-sm copy-btn';
 copyBtn.textContent='Copy bug report';

 const downloadBtn=doc.createElement('button');
 downloadBtn.type='button';
 downloadBtn.className='button btn-sm download-btn';
 downloadBtn.textContent='Download bug report';

 actionsDiv.appendChild(copyBtn);
 actionsDiv.appendChild(downloadBtn);

 const repoUrl=e?.repoUrl||e?.diagnostic?.repo_url;
 if(typeof repoUrl==='string'&&/^https:\/\/github\.com\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(repoUrl)){
  const issueLink=doc.createElement('a');
  issueLink.href=repoUrl+'/issues/new?template=bug_report.md';
  issueLink.target='_blank';
  issueLink.rel='noopener noreferrer';
  if(typeof issueLink.setAttribute==='function'){
   issueLink.setAttribute('href',repoUrl+'/issues/new?template=bug_report.md');
   issueLink.setAttribute('target','_blank');
   issueLink.setAttribute('rel','noopener noreferrer');
  }
  issueLink.className='button btn-sm issue-link';
  issueLink.textContent='Open GitHub issue →';
  actionsDiv.appendChild(issueLink);
 }

 const statusSpan=doc.createElement('span');
 statusSpan.className='copy-status muted';
 statusSpan.setAttribute('role','status');
 statusSpan.setAttribute('aria-live','polite');
 actionsDiv.appendChild(statusSpan);

 copyBtn.onclick=async()=>{
  try{
   if(typeof navigator!=='undefined'&&navigator.clipboard?.writeText){
    await navigator.clipboard.writeText(bugReportText);
    statusSpan.textContent='Copied to clipboard';
   }else if(typeof document!=='undefined'&&document.execCommand){
    const ta=doc.createElement('textarea');
    ta.value=bugReportText;
    doc.body.appendChild(ta);
    ta.select();
    const successful=document.execCommand('copy');
    doc.body.removeChild(ta);
    if(successful===true){
     statusSpan.textContent='Copied to clipboard';
    }else{
     statusSpan.textContent='Copy failed; select text above';
    }
   }else{
    statusSpan.textContent='Copy unavailable; select text above';
   }
  }catch(_){
   statusSpan.textContent='Copy failed; select text above';
  }
 };

 downloadBtn.onclick=()=>{
  if(typeof download==='function'){
   download(bugReportText,'pv-lab-bug-report.txt','text/plain; charset=utf-8');
   statusSpan.textContent='Downloaded bug report';
  }
 };

 diagDiv.appendChild(actionsDiv);
 errContainer.appendChild(diagDiv);
};
const EXCLUDED_PERCENT_KEYS = new Set([
  'gas_vat_reduced',
  'gas_vat_standard',
  'clipping_dc_max',
  'clipping_ac_max',
  'ac_heating_additional_demand_pct'
]);

function isPercentControl(record){
 if(!record)return false;
 const n=record.name;
 if(EXCLUDED_PERCENT_KEYS.has(n))return false;
 const u=record.unit||'';
 if(u==='fraction'||u==='fraction/year'||u==='fraction/EFC')return true;
 return n==='real_discount_rate'||n==='deduction_rate';
}

function formatPercentValue(v){
 if(v==null||v==='')return '';
 const num=Number(v);
 if(!Number.isFinite(num))return '';
 const pct=Number((num*100).toPrecision(15));
 return `${pct}%`;
}

function parsePercentInput(rawText, record){
 if(rawText==null)return {valid:false,value:null};
 const str=String(rawText).trim();
 const nullable=Boolean(record&&schema?.defaults&&schema.defaults[record.name]===null);
 if(str===''){
  if(nullable)return {valid:true,value:null};
  return {valid:false,value:null,reason:'Value required'};
 }
 let s=str;
 if(s.endsWith('%'))s=s.slice(0,-1).trim();
 if(!/^[-+]?(\d+(\.\d*)?|\.\d+)$/.test(s)){
  return {valid:false,value:null,reason:'Invalid number'};
 }
 const pctNum=Number(s);
 if(!Number.isFinite(pctNum)){
  return {valid:false,value:null,reason:'Invalid number'};
 }
 const existing=settings?.[record?.name];
 const unchanged=typeof existing==='number'&&pctNum===Number(formatPercentValue(existing).replace('%',''));
 const fraction=unchanged?existing:pctNum/100;
 if(record?.bounds){
  const [min,max]=record.bounds;
  if(min!=null&&fraction<min-1e-9)return {valid:false,value:null,reason:`Minimum is ${formatPercentValue(min)}`};
  if(max!=null&&fraction>max+1e-9)return {valid:false,value:null,reason:`Maximum is ${formatPercentValue(max)}`};
 }
 return {valid:true,value:fraction};
}
// Keep useful spinner increments without rounding the underlying bill inputs.
function numericStep(record){
 if(!record)return .1;
 const n=record.name,u=record.unit||'';
 if(n==='inverter_kw')return .01;
 if(n==='hp_nominal_kw')return .5;
 if(['hp_tank_litres','hp_dhw_litres_day'].includes(n))return 5;
 if(['hp_capex','hp_replacement_cost','hp_cooking_capex','gas_disconnect_cost'].includes(n))return 50;
 if(n==='hp_annual_maintenance')return 10;
 if(n==='hp_tank_loss_w_k')return .1;
 if(['hp_backup_efficiency','hp_prior_dhw_loss_fraction','hp_draw_morning_fraction','hp_min_modulation_fraction','hp_cycling_penalty','hp_distribution_loss_fraction','hp_additional_defrost_penalty'].includes(n))return .01;
 if(['hp_external_pump_kw','hp_standby_kw','hp_additional_unit_aux_kw'].includes(n))return .01;
 if(n==='hp_radiator_exponent')return .05;
 if(['panels','year','years','W','km/year','hour','hours','month'].includes(u))return 1;
 if(n==='scop')return .1;
 if(n==='battery_cycle_fade')return .00001;
 if(['electric_excise','pmg_price','gas_pcs'].includes(n))return .0001;
 if(n==='power_fixed_kw_month')return .000001;
 if(n==='battery_replacement_cost_kwh')return 1;
 if(n==='gas_fixed_day')return .001;
 if(u.startsWith('EUR/'))return ['EUR/kWh','EUR/Smc','EUR/day'].includes(u)?.001:u==='EUR/year'||u==='EUR/usable kWh'||u==='EUR/kWp'?1:.01;
 if(u==='EUR')return 1;
 if(u.startsWith('fraction'))return .001;
 if(u==='multiplier')return .01;
 return .1;
}
function numericBounds(record,value){
 if(!record?.bounds)return '';
 const [min,max]=record.bounds,step=numericStep(record);
 // HTML uses min as the step base. Exact invoice ratios need their own base
 // so native spinners do not snap away precision or flag the baseline invalid.
 const aligned=value==null||Math.abs((value-min)/step-Math.round((value-min)/step))<1e-7;
 return `${aligned?`min="${min}" `:''}data-min="${min}"${max==null?'':` max="${max}"`}`;
}
const FRIENDLY_NAMES={
 ac_heating_windows:'AC heating operating windows',
 ac_heating_additional_demand_pct:'Additional AC heating demand',
 retail_commodity:'Electricity price (including dispatching, pre-tax)',
 retail_regulated:'Electricity network and system charges (pre-tax)',
 electric_excise:'Electricity excise (accisa)',
 gas_commodity:'Gas commodity (pre-tax)',
 gas_regulated:'Gas network/system charges (pre-tax)',
 gas_fixed_day:'Gas supplier + network fixed charges (pre-tax)',
 gas_excise_0_120:'National gas excise: 0–120 Smc/yr',
 gas_excise_120_480:'National gas excise: 120–480 Smc/yr',
 gas_excise_480_1560:'National gas excise: 480–1560 Smc/yr',
 gas_excise_over_1560:'National gas excise: over 1560 Smc/yr',
 gas_regional_0_120:'Campania regional gas surcharge: 0–120 Smc/yr',
 gas_regional_over_120:'Campania regional gas surcharge: over 120 Smc/yr',
 gas_vat_reduced:'Gas VAT reduced: ≤480 Smc/yr variable',
 gas_vat_standard:'Gas VAT standard: >480 Smc/yr variable & fixed',
 real_discount_rate:'Discount rate',
 deduction_rate:'Tax deduction',
 battery_kwh:'Usable battery capacity',
 pv_max_kwp:'Maximum connected PV capacity',
 pv_battery_coupling:'PV / battery coupling',
 pv_dispatch_model:'Dispatch model',
 routing_priority:'Routing priority (EMS work mode)',
 routing_switch_hour:'Scheduled mode switch hour (AM → PM)',
 scheduled_start_month:'Scheduled mode start month',
 scheduled_end_month:'Scheduled mode end month',
 inverter_efficiency:'Inverter conversion efficiency',
 ilr_ac_max:'Maximum DC/AC ratio · AC coupling',
 ilr_dc_max:'Maximum DC/AC ratio · DC coupling',
 clipping_ac_max:'Annual clipping cap · AC coupling',
 clipping_dc_max:'Annual clipping cap · DC coupling',
 battery_inverter_kw:'Dedicated AC battery inverter',
 battery_c_rate_max:'Maximum battery C-rate',
 battery_soc_min:'Minimum nominal battery SoC',
 battery_soc_max:'Maximum nominal battery SoC',
 battery_charge_efficiency:'Battery DC charging efficiency',
 battery_discharge_efficiency:'Battery DC discharging efficiency',
 electrification_capex:'Electrification CAPEX',
 electrification_capex_include_baseline:'Include in baseline scenario?',
 heating_balance_temp:'Outdoor heating balance temperature (space heating threshold proxy)',
 outdoor_heating_balance_temp:'Outdoor heating balance temperature (space heating threshold proxy)',
 planning_heat_pump:'Planning a heat pump',
 hp_nominal_kw:'Nominal thermal capacity (fallback A7/W35)',
 hp_weather_sensitive:'Weather-sensitive heating demand scaling',
 hp_indoor_temp:'Indoor heating setpoint',
 hp_setback_delta:'Setback temperature drop',
 hp_schedule_mode:'Heating schedule mode',
 hp_morning_on:'Morning heating window start',
 hp_morning_off:'Morning heating window stop',
 hp_evening_on:'Evening heating window start',
 hp_evening_off:'Evening heating window stop',
 hp_curve_outdoor_cold:'Outdoor design temperature (cold)',
 hp_curve_outdoor_mild:'Outdoor mild temperature',
 hp_curve_flow_cold:'Flow temperature at cold design',
 hp_curve_flow_mild:'Flow temperature at mild design',
 hp_radiator_dt50_kw:'Installed radiator capacity (ΔT 50)',
 hp_radiator_exponent:'Radiator exponent n',
 hp_return_drop:'Radiator return temperature drop (flow - return)',
 hp_tank_litres:'Hot water cylinder volume',
 hp_tank_target_temp:'Cylinder target storage temperature',
 hp_tank_inlet_temp:'Cold mains inlet temperature',
 hp_tank_ambient_temp:'Cylinder ambient air temperature',
 hp_tank_loss_w_k:'Cylinder standing heat loss',
 hp_tank_deadband:'Cylinder thermostat deadband',
 hp_tank_service_temp:'Minimum hot water delivery threshold',
 hp_tank_initial_temp:'Initial cylinder water temperature',
 hp_coil_approach:'Reheat coil approach temperature delta',
 hp_dhw_demand_mode:'Hot water demand basis',
 hp_dhw_litres_day:'Daily hot water draw volume',
 hp_prior_dhw_loss_fraction:'Prior gas water heating standing loss fraction',
 hp_draw_morning_on:'Morning hot water draw start',
 hp_draw_morning_off:'Morning hot water draw stop',
 hp_draw_evening_on:'Evening hot water draw start',
 hp_draw_evening_off:'Evening hot water draw stop',
 hp_draw_morning_fraction:'Morning hot water draw share',
 hp_reheat_mode:'Cylinder reheat schedule',
 hp_reheat_on:'Reheat window start',
 hp_reheat_off:'Reheat window stop',
 hp_hygiene_enabled:'Weekly thermal disinfection cycle',
 hp_hygiene_temp:'Disinfection target temperature',
 hp_hygiene_weekday:'Disinfection day of week',
 hp_hygiene_hour:'Disinfection cycle start hour',
 hp_hygiene_duration_hours:'Disinfection cycle duration',
 hp_backup_kw:'Auxiliary electric backup heater capacity',
 hp_backup_efficiency:'Auxiliary heater conversion efficiency',
 hp_external_pump_kw:'Hydronic distribution pump power',
 hp_standby_kw:'Continuous standby electric power',
 hp_min_modulation_fraction:'Minimum compressor modulation fraction',
 hp_cycling_penalty:'Part-load cycling COP degradation factor',
 hp_distribution_loss_fraction:'Hydronic piping thermal distribution loss',
 hp_additional_defrost_penalty:'Additional seasonal defrost penalty',
 hp_additional_unit_aux_kw:'Additional unit auxiliary electric power',
 hp_capex:'Heat pump turnkey installed CAPEX',
 hp_annual_maintenance:'Annual heat pump maintenance allowance',
 hp_replacement_year:'Heat pump replacement year',
 hp_replacement_cost:'Replacement heat pump unit cost',
 hp_electric_cooking:'Convert cooking to induction / electric',
 hp_cooking_capex:'Induction cooktop equipment & install cost',
 hp_disconnect_gas:'Permanently disconnect gas service',
 hp_manufacturer_map_json:'Manufacturer performance map JSON',
 gas_disconnect_cost:'Gas service disconnection cost',
 south_extra_cost_per_panel:'South roof extra cost per panel',
 west_extra_cost_per_panel:'West roof extra cost per panel',
 east_extra_cost_per_panel:'East roof extra cost per panel',
 dormer_extra_cost_per_panel:'Dormer roof extra cost per panel',
 flat_extra_cost_per_panel:'Flat roof extra cost per panel',
 secondary_south_extra_cost_per_panel:'Secondary south roof extra cost per panel',
 battery_module_cost_override:'Primary battery module cost override',
 battery_secondary_module_cost_override:'Secondary battery module cost override',
 battery_module_extra_cost:'Primary battery module extra cost',
 battery_secondary_module_extra_cost:'Secondary battery module extra cost'
};

/* ROUTING PRIORITY HELPERS (start) */
const ROUTING_PRIORITY_LABELS={
 auto:'Auto (Optimize for active NPV objective)',
 battery_first:'Battery-First (Self-Use: Load → Battery → Grid)',
 grid_first:'Grid-First (Feed-in Priority: Load → Clipping Recapture → Grid → Battery)',
 scheduled_hybrid:'Scheduled AM/PM (FoxESS Mode Scheduler: Grid-First AM → Self-Use PM)',
 dynamic_reserve:'Dynamic Clipping Reserve (Smart EMS / Modbus Look-Ahead)'
};
const ROUTING_PRIORITY_COMPACT={
 auto:'Auto routing',
 pv_only:'PV-only (no storage)',
 battery_first:'Battery-first (self-use)',
 grid_first:'Grid-first (feed-in)',
 scheduled_hybrid:'Scheduled AM/PM (grid → battery)',
 dynamic_reserve:'Dynamic clipping reserve'
};
function routingPriorityLabel(mode){return ROUTING_PRIORITY_LABELS[mode]||mode||'—';}
function routingPriorityCompact(mode){return ROUTING_PRIORITY_COMPACT[mode]||routingPriorityLabel(mode);}
function isPhysicalDispatch(result,s){const model=(s&&s.pv_dispatch_model)||result?.settings?.pv_dispatch_model;return model==='physical';}
// Resolved physical routing mode; null for legacy dispatch or an unresolved Auto request.
function resolvedRoutingPriority(result){
 if(!result||!isPhysicalDispatch(result))return null;
 const s=result.settings||{};
 const mode=result.resolved_routing_priority||s.route||s.routing_priority||null;
 return mode==='auto'?null:mode;
}
// Header pill text: prefers resolved_routing_priority, falls back to the requested settings.route so Auto stays visible.
function routingPillText(result){
 if(!result)return null;
 if(!isPhysicalDispatch(result))return 'Legacy dispatch · physical routing not modeled';
 const resolved=result.resolved_routing_priority;
 const requested=(result.settings&&(result.settings.route||result.settings.routing_priority))||'auto';
 const mode=resolved||requested;
 if(mode==='auto')return 'Physical routing · Auto (evaluating modes)';
 const locked=requested!=='auto'?(mode===requested?' · locked':` · requested ${routingPriorityCompact(requested)}`):'';
 return `Physical routing · ${routingPriorityCompact(mode)}${locked}`;
}
// Charge bus is DC terminal power for DC coupling and AC terminal power for AC coupling; never stored kWh.
function chargeBusPhaseLabel(coupling){return coupling==='dc_coupled'?'DC':'AC';}
function chargeBusLabel(coupling){return `${chargeBusPhaseLabel(coupling)} battery charge input`;}
function routingChargingSplit(flows){
 const f=flows||{};
 const normal=f.charged_from_normal_solar_kwh==null?NaN:Number(f.charged_from_normal_solar_kwh);
 const recaptured=f.charged_from_recaptured_clipping_kwh==null?NaN:Number(f.charged_from_recaptured_clipping_kwh);
 const clipped=f.clipped_kwh==null?NaN:Number(f.clipped_kwh);
 const hasSplit=Number.isFinite(normal)||Number.isFinite(recaptured);
 const hasClipped=Number.isFinite(clipped);
 if(!hasSplit&&!hasClipped)return null;
 const pvInput=Number(f.pv_input_kwh);
 const delivered=Number(f.pv_generation_kwh);
 const base=Number.isFinite(pvInput)&&pvInput>0?pvInput:(Number.isFinite(delivered)?delivered+(hasClipped?clipped:0):null);
 const clippedPct=hasClipped&&base>0?clipped/base:null;
 return {normal:Number.isFinite(normal)?normal:null,recaptured:Number.isFinite(recaptured)?recaptured:null,clipped:hasClipped?clipped:null,clippedPct,hasSplit,hasClipped};
}
function routeDisplayForCandidate(c){
 const s=c?.settings||{};
 const mode=c?.resolved_routing_priority||c?.routing_priority||s.routing_priority||s.route||null;
 const switchHour=c?.routing_switch_hour??s.routing_switch_hour;
 if(!mode)return '—';
 const txt=routingPriorityCompact(mode);
 if(mode==='scheduled_hybrid'){
  const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const start=c?.scheduled_start_month??s.scheduled_start_month,end=c?.scheduled_end_month??s.scheduled_end_month;
  const span=months[start-1]&&months[end-1]?` · ${months[start-1]}–${months[end-1]}`:'';
  return `${txt}${switchHour!=null?` · switch ${switchHour}:00`:''}${span}`;
 }
 return (mode==='scheduled_hybrid'||mode==='dynamic_reserve')&&switchHour!=null?`${txt} · switch ${switchHour}:00`:txt;
}
function routeDisplayForResult(result){
 if(!result)return '—';
 if(!isPhysicalDispatch(result))return 'Legacy dispatch (physical routing not modeled)';
 const mode=result.resolved_routing_priority||result.settings?.route||result.settings?.routing_priority;
 if(!mode||mode==='auto')return 'Auto routing (resolving)';
 if(mode==='scheduled_hybrid')return routeDisplayForCandidate({...result,resolved_routing_priority:mode});
 const txt=routingPriorityCompact(mode);
 const sh=result.settings?.routing_switch_hour;
 return (mode==='scheduled_hybrid'||mode==='dynamic_reserve')&&sh!=null?`${txt} · switch ${sh}:00`:txt;
}
/* ROUTING PRIORITY HELPERS (end) */

function getLoanScopeContextText(s=settings,r=current?.result){
 const scen=s?.load_scenario||r?.settings?.load_scenario||'baseline';
 const incBaseline=Boolean(s?.electrification_capex_include_baseline||r?.settings?.electrification_capex_include_baseline);
 const capex=r?.finance?.capex_eur??0;
 const mode=s?.loan_mode||r?.settings?.loan_mode||'amount';
 const amount=Number(s?.loan_amount??r?.settings?.loan_amount??0);
 if(scen==='baseline'){
  if(!incBaseline){
   return 'Baseline load has no electrification costs to finance. With an electrified load, scope changes eligible borrowing cost, not total equipment cost.';
  }
  if(mode==='amount'&&capex>0&&amount<=capex+0.01){
   return 'Requested loan amount is within solar capex, below both caps, so scope leaves principal and payment unchanged. With baseline electrification capex included, scope changes eligible borrowing cost, not total equipment cost.';
  }
  return 'With baseline electrification capex included, loan scope changes eligible borrowing cost, not total equipment cost.';
 }
 if(mode==='amount'&&capex>0&&amount<=capex+0.01){
  return 'Requested loan amount is within solar capex, below both caps, so scope leaves principal and payment unchanged. With an electrified load, scope changes eligible borrowing cost, not total equipment cost.';
 }
 return 'With an electrified load, loan scope changes eligible borrowing cost, not total equipment cost.';
}

function control(record,isCompact=false){
 const n=record.name,v=settings[n],name=esc(n);
 const rawLabel=record.label||FRIENDLY_NAMES[n]||title(n);
 const label=esc(rawLabel);
 const choices=n==='ac_heating_additional_demand_pct'?null:(record.choices||(typeof schema?.defaults?.[n]==='string'?record.bounds:null));
 const bounds=record.bounds;
 if(n==='ac_heating_windows'){
  return renderAcHeatingScheduleControl(record,isCompact);
 }
 if(n==='electrification_capex_include_baseline'){
  const isMod=typeof isRecordModified==='function'?isRecordModified(n):(schema?.defaults?.[n]!==undefined&&settings[n]!==undefined&&!settingValuesEqual(schema.defaults[n],settings[n]));
  return `<div class="control${isMod?' is-modified':''}" data-control="${name}"><div class="control-header"><label>${label}</label><button type="button" class="control-reset" data-reset-setting="${name}" aria-label="Reset ${label}" title="Reset ${label} to its saved default"${isMod?'':' hidden'}>Reset</button></div><div class="row"><label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="Include in baseline scenario?" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>Include in baseline scenario?</span></label></div></div>`;
 }
 if(n==='planning_heat_pump'){
  const isMod=typeof isRecordModified==='function'?isRecordModified(n):(schema?.defaults?.[n]!==undefined&&settings[n]!==undefined&&!settingValuesEqual(schema.defaults[n],settings[n]));
  return `<div class="control control-planning-heat-pump${isMod?' is-modified':''}" data-control="${name}"><div class="control-header"><label>${label}</label><button type="button" class="control-reset" data-reset-setting="${name}" aria-label="Reset ${label}" title="Reset ${label} to its saved default"${isMod?'':' hidden'}>Reset</button></div><div class="row"><label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="Planning a heat pump" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>Planning a heat pump</span></label></div><details class="control-details"><summary>Details</summary><div class="control-details-body"><div class="control-details-meta"><span class="tag ${esc(record.status||'proposed')}">${esc(record.status||'proposed')}</span><small class="control-source" title="${esc(record.source||'')}">Source: ${esc(record.source||'')}</small></div><small class="control-desc">${esc(record.description||'Planning an air-to-water heat pump for space heating and domestic hot water.')}</small><div class="control-details-actions"><button type="button" data-save-default="${name}" aria-label="Save ${label} as default">Save as shared default</button></div></div></details></div>`;
 }
 let input;
 if(n==='loan_include_electrification')input=`<label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="${label}" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>Include electrification capex</span></label><small class="switch-context muted" data-switch-context="${name}" style="display:block;margin-top:4px;">${esc(getLoanScopeContextText(settings,current?.result))}</small>`;
 else if(n==='battery_mixed_sizes')input=`<label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="Modules of different sizes?" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>Modules of different sizes?</span></label>`;
 else if(n==='hp_electric_cooking')input=`<label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="${label}" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>${label}</span></label>`;
 else if(n==='hp_disconnect_gas')input=`<label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="${label}" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>${label}</span></label><div id="hpDisconnectError" class="hp-error-message" hidden></div>`;
 else if(n==='hp_hygiene_enabled')input=`<label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="${label}" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>${label}</span></label><small class="muted" style="display:block;margin-top:4px;">Weekly thermal disinfection cycle.</small>`;
 else if(n==='hp_weather_sensitive')input=`<label class="switch"><input type="checkbox" role="switch" data-setting="${name}" aria-label="${label}" ${v?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>${label}</span></label>`;
 else if(n==='hp_manufacturer_map_json'){
  const mapText=typeof v==='string'?v:(v?JSON.stringify(v):'');
  let mapStatus='Provisional fallback map scaled by nominal capacity estimate; no manufacturer equipment selected.';
  let mapClass='map-validation-status';
  if(mapText.trim()){
   try{
    const d=JSON.parse(mapText);
    const meta=d.metadata||{};
    mapStatus=`<span class="hp-meta-badge">Model: ${esc(meta.model||'Unknown')} · ${esc(meta.source||'Custom')} · ${meta.includes_defrost?'Defrost included':'No defrost in map'}</span>`;
    mapClass='map-validation-status is-valid';
   }catch(err){
    mapStatus=`JSON validation message: ${esc(err.message)}`;
    mapClass='map-validation-status is-invalid';
   }
  }
  input=`<textarea data-setting="${name}" class="map-json-textarea" aria-label="${label}" rows="3" placeholder="Paste optional manufacturer performance map JSON...">${esc(mapText)}</textarea><div class="${mapClass}" id="mapValidationStatus">${mapStatus}</div><details class="control-details hp-citations-details" style="margin-top:4px"><summary>Manufacturer &amp; radiator citations</summary><div class="control-details-body"><p>Default model is a synthetic fallback scaled by nominal capacity (not a selected manufacturer unit). Performance tables often mix part-load and maximum capacity ratings.</p><p>Stelrad compact radiator EN 442 catalogue data (<a href="https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf" target="_blank" rel="noopener">Stelrad correction factor documentation</a>).</p><p>Vaillant aroTHERM plus VWL EN 14511 / EN 14825 test data (<a href="https://www.vaillant.com/en/products/arothermplus/" target="_blank" rel="noopener">Vaillant aroTHERM plus product specifications</a>).</p></div></details>`;
 }
 else if(n==='battery_kwh'&&settings.battery_mixed_sizes)input=`<input type="number" data-setting="${name}" value="${v??''}" readonly aria-label="${label}" placeholder="Derived from modules" class="readonly-input">`;
 else if(n==='ac_heating_additional_demand_pct'){
  const draftVal=acDemandPctDraft!==null?acDemandPctDraft:(v??(schema?.defaults?.[n]??0));
  const hasError=Boolean(acDemandPctError);
  const errorId=`${name}_error`;
  const errAttr=hasError?` aria-invalid="true" aria-describedby="${errorId}"`:'';
  const invalidClass=hasError?' is-invalid':'';
  input=`<input type="number" data-setting="${name}" id="${name}" class="${invalidClass}" value="${esc(draftVal)}" min="0" step="any" aria-label="${label}" placeholder="0"${errAttr}><div id="${errorId}" class="hp-error-message ac-field-error"${hasError?'':' hidden'}>${esc(acDemandPctError||'')}</div>`;
 }
 else if(typeof schema?.defaults?.[n]==='boolean')input=`<select data-setting="${name}" aria-label="${label}"><option value="false" ${!v?'selected':''}>No</option><option value="true" ${v?'selected':''}>Yes</option></select>`;
 else if(choices)input=`<select data-setting="${name}" aria-label="${label}">${choices.map(c=>`<option value="${esc(c)}" ${c===v?'selected':''}>${esc(n==='loan_mode'?({fraction:'Share of capex',amount:'Absolute EUR amount'}[c]):n==='pv_battery_coupling'?({ac_equivalent:'AC-equivalent · clipping before battery charging',dc_coupled:'DC-coupled approximation · battery may recover PV surplus'}[c]):n==='dhw_heating_type'?({resistance:'Resistance boiler',heat_pump:'Heat pump'}[c]):n==='heating_schedule_mode'?({scheduled:'Scheduled operating windows',continuous:'Continuous (24h old model)'}[c]):n==='hp_schedule_mode'?({scheduled:'Scheduled operating windows',continuous:'Continuous (24h available)'}[c]):n==='hp_dhw_demand_mode'?({gas_calibrated:'Gas-calibrated monthly basis',litres:'Daily draw volume basis (litres)'}[c]):n==='hp_reheat_mode'?({continuous:'Continuous (24h on demand)',scheduled:'Scheduled reheat window'}[c]):n==='hp_hygiene_weekday'?({0:'Monday',1:'Tuesday',2:'Wednesday',3:'Thursday',4:'Friday',5:'Saturday',6:'Sunday'}[c]):n==='routing_priority'?routingPriorityLabel(c):(isPercentControl(record)?formatPercentValue(c):c))}</option>`).join('')}</select>`;
  else if(isPercentControl(record))input=`<input type="text" inputmode="decimal" data-setting="${name}" value="${formatPercentValue(v)}" aria-label="${label}" placeholder="${formatPercentValue(schema?.defaults?.[n])}">`;
 else input=`<input type="number" data-setting="${name}" value="${v??''}" ${numericBounds(record,v)} step="${numericStep(record)}" aria-label="${label}" placeholder="${n==='pv_max_kwp'?'Blank: use inverter AC capacity':'Not provided'}">`;
 let baselineSwitch='';
 if(n==='electrification_capex'){
  const incBase=Boolean(settings.electrification_capex_include_baseline);
  baselineSwitch=`<div class="control-baseline-switch" style="margin-top:6px;"><label class="switch"><input type="checkbox" role="switch" data-setting="electrification_capex_include_baseline" aria-label="Include in baseline scenario?" ${incBase?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>Include in baseline scenario?</span></label></div>`;
 }
 let slider='';
 if(['south_panels','west_panels','east_panels','inverter_kw','battery_kwh','roundtrip_efficiency','scop','deduction_tax_capacity','real_discount_rate','export_capture_factor','ev_day_fraction','summer_ac_multiplier'].includes(n)&&bounds?.[1]!=null){
  if(!(n==='battery_kwh'&&settings.battery_mixed_sizes)){
   slider=`<input type="range" data-slider="${name}" min="${n==='deduction_tax_capacity'?record.plausible_range[0]:bounds[0]}" max="${n==='deduction_tax_capacity'?record.plausible_range[1]:bounds[1]}" step="${numericStep(record)}" value="${v}" aria-label="${label} slider">`;
  }
 }
 let extraAction='';
 if(n==='hp_schedule_mode'){
  extraAction='<div style="margin-top:6px;"><button type="button" id="copyBoilerWindowsBtn" class="action-btn-small" aria-label="Copy AC heating schedule">Copy AC heating schedule</button></div>';
 }else if(n==='hp_nominal_kw'&&settings.hp_manufacturer_map_json){
  extraAction='<small class="muted" style="display:block;margin-top:3px;">Fallback nominal estimate (overridden by custom map capacity)</small>';
 }
 const condition=n==='loan_fraction'||n==='loan_amount'?'data-when="loan_mode"':['loan_include_electrification','loan_apr','loan_years','loan_max_monthly_payment'].includes(n)?'data-when="loan_active"':n==='dhw_resistance_efficiency'?'data-when="dhw_heating_type" data-when-value="resistance"':n==='dhw_scop'?'data-when="dhw_heating_type" data-when-value="heat_pump"':['heating_on_hour','heating_off_hour','heating_evening_on_hour','heating_evening_off_hour'].includes(n)?'data-when="heating_schedule_mode" data-when-value="scheduled"':['hp_morning_on','hp_morning_off','hp_evening_on','hp_evening_off'].includes(n)?'data-when="hp_schedule_mode" data-when-value="scheduled"':n==='hp_dhw_litres_day'?'data-when="hp_dhw_demand_mode" data-when-value="litres"':n==='hp_prior_dhw_loss_fraction'?'data-when="hp_dhw_demand_mode" data-when-value="gas_calibrated"':['hp_reheat_on','hp_reheat_off'].includes(n)?'data-when="hp_reheat_mode" data-when-value="scheduled"':['hp_hygiene_temp','hp_hygiene_weekday','hp_hygiene_hour','hp_hygiene_duration_hours'].includes(n)?'data-when="hp_hygiene_enabled"':n==='hp_cooking_capex'?'data-when="hp_electric_cooking"':n==='gas_disconnect_cost'?(Boolean(settings.planning_heat_pump)?'data-when="hp_disconnect_gas"':''):'';
 const isBoolUnit=record.unit==='boolean'||record.unit==='bool'||n==='planning_heat_pump';
 let unitDisplay=isBoolUnit?'':(n==='hp_nominal_kw'?'kW th':(record.unit==='C'?'°C':(record.unit||'')));
  if(isPercentControl(record)){
   if(record.unit==='fraction/year')unitDisplay='/year';
   else if(record.unit==='fraction'||record.unit==='%')unitDisplay='';
   else if(record.unit==='fraction/EFC')unitDisplay='/EFC';
  }
 if(record.unit==='fraction'&&(n==='gas_vat_reduced'||n==='gas_vat_standard')){
  const numVal=Number(v??schema?.defaults?.[n]??0);
  if(Number.isFinite(numVal)){
   const pctStr=`${Number((numVal*100).toFixed(2))}%`;
   const rawStr=(numVal===0.1)?'0.10':`${numVal}`;
   unitDisplay=`fraction (${rawStr} = ${pctStr})`;
  }
 }
 const isModified=typeof isRecordModified==='function'?isRecordModified(n):(schema?.defaults?.[n]!==undefined&&settings[n]!==undefined&&!settingValuesEqual(schema.defaults[n],settings[n]));
 const resetAvailable=typeof isResetAvailable==='function'?isResetAvailable(n):isModified;
 return `<div class="control${isModified?' is-modified':''}" data-control="${name}" ${condition}><div class="control-header"><label>${label}</label><button type="button" class="control-reset" data-reset-setting="${name}" aria-label="Reset ${label}" title="Reset ${label} to its saved default"${resetAvailable?'':' hidden'}>Reset</button></div><div class="row">${input}<span class="unit"${isBoolUnit?' style="display:none;"':''}>${esc(unitDisplay)}</span></div>${baselineSwitch}${slider}${extraAction}<details class="control-details"><summary>Details</summary><div class="control-details-body"><div class="control-details-meta"><span class="tag ${esc(record.status)}">${esc(record.status)}</span><small class="control-source" title="${esc(record.source||'')}">Source: ${esc(record.source||'')}</small></div><small class="control-desc">${esc(record.description||'')}</small><div class="control-details-actions"><button type="button" data-save-default="${name}" aria-label="Save ${label} as default">Save as shared default</button></div></div></details></div>`;
}

function formatAcScheduleMinute(minute){
 if(minute==null||!Number.isFinite(Number(minute)))return '00:00';
 const m=Math.round(Number(minute));
 if(m===1440)return '24:00';
 const clamped=Math.max(0,Math.min(1439,m));
 const h=Math.floor(clamped/60);
 const min=clamped%60;
 return `${String(h).padStart(2,'0')}:${String(min).padStart(2,'0')}`;
}

function parseAcScheduleTime(text,isEnd=false){
 if(typeof text!=='string')return null;
 const str=text.trim();
 if(isEnd&&str==='24:00')return 1440;
 const match=/^([01]\d|2[0-3]):([0-5]\d)$/.exec(str);
 if(!match)return null;
 const h=Number(match[1]);
 const m=Number(match[2]);
 const total=h*60+m;
 if(!isEnd&&total>1439)return null;
 if(isEnd&&total>1440)return null;
 return total;
}

function calculateAcScheduleUnionMinutes(items){
 if(!items||!items.length)return 0;
 const mask=new Array(1440).fill(false);
 for(const item of items){
  let sm,em;
  if(typeof item.startText==='string'){
   sm=parseAcScheduleTime(item.startText,false);
   em=parseAcScheduleTime(item.endText,true);
  }else{
   sm=item.start_minute;
   em=item.end_minute;
  }
  if(sm==null||em==null||!Number.isFinite(sm)||!Number.isFinite(em))continue;
  if(sm===em)continue;
  if(sm<em){
   const endClamped=Math.min(1440,em);
   for(let i=sm;i<endClamped;i++)mask[i]=true;
  }else{
   for(let i=sm;i<1440;i++)mask[i]=true;
   for(let i=0;i<em;i++)mask[i]=true;
  }
 }
 return mask.filter(Boolean).length;
}

function formatAcScheduleUnionDuration(minutes){
 const total=Math.max(0,Math.round(Number(minutes)||0));
 const h=Math.floor(total/60);
 const m=total%60;
 return `${h}h ${String(m).padStart(2,'0')}m / day`;
}

function generateUniqueWindowId(items){
 const existing=new Set((items||[]).map(w=>w.id).filter(Boolean));
 let idx=1;
 while(existing.has(`window_${idx}`))idx++;
 return `window_${idx}`;
}

const DEFAULT_AC_HEATING_WINDOWS=[
 {id:'morning',start_minute:360,end_minute:480},
 {id:'evening',start_minute:960,end_minute:1200}
];

const LEGACY_HEATING_KEYS=[
 'heating_on_hour',
 'heating_off_hour',
 'heating_evening_on_hour',
 'heating_evening_off_hour'
];

function validateAcHeatingWindows(windows){
 if(!Array.isArray(windows)){
  throw new Error(`ac_heating_windows must be an array of window objects, got ${typeof windows}`);
 }
 const normalized=[];
 const seenIds=new Set();
 for(let idx=0;idx<windows.length;idx++){
  const item=windows[idx];
  if(!item||typeof item!=='object'||Array.isArray(item)){
   throw new Error(`Each window in ac_heating_windows must be an object at index ${idx}`);
  }
  const allowedKeys=new Set(['id','start_minute','end_minute']);
  const unknown=Object.keys(item).filter(k=>!allowedKeys.has(k));
  if(unknown.length>0){
   throw new Error(`Unknown fields in window at index ${idx}: ${unknown.sort().join(', ')}`);
  }
  if(!Object.hasOwn(item,'start_minute')||!Object.hasOwn(item,'end_minute')){
   throw new Error(`Window at index ${idx} must specify both 'start_minute' and 'end_minute'`);
  }
  const sm=item.start_minute;
  const em=item.end_minute;
  if(typeof sm!=='number'||typeof em!=='number'||!Number.isInteger(sm)||!Number.isInteger(em)){
   throw new Error(`Window minutes at index ${idx} must be integers`);
  }
  if(sm<0||sm>1439){
   throw new Error(`Window 'start_minute' at index ${idx} must be between 0 and 1439, got ${sm}`);
  }
  if(em<0||em>1440){
   throw new Error(`Window 'end_minute' at index ${idx} must be between 0 and 1440, got ${em}`);
  }
  let wid=item.id;
  if(Object.hasOwn(item,'id')){
   if(typeof wid!=='string'||!wid.trim()){
    throw new Error(`Window 'id' at index ${idx} must be a non-empty string`);
   }
   wid=wid.trim();
  }else{
   wid=`window_${idx+1}`;
  }
  if(seenIds.has(wid)){
   throw new Error(`Duplicate window id '${wid}' at index ${idx}`);
  }
  seenIds.add(wid);
  normalized.push({
   id:wid,
   start_minute:sm,
   end_minute:em
  });
 }
 return normalized;
}

function validateLegacyHour(val,name){
 if(typeof val!=='number'||!Number.isInteger(val)){
  throw new Error(`${name} must be an integer, got ${val}`);
 }
 if(val<0||val>23){
  throw new Error(`${name} must be between 0 and 23, got ${val}`);
 }
 return val;
}

function migrateAndResolveSchedule(settingsObj,baseWindows=null){
 const settings=(settingsObj&&typeof settingsObj==='object')?settingsObj:{};
 const hasCanonical=Object.hasOwn(settings,'ac_heating_windows');
 const presentLegacy=LEGACY_HEATING_KEYS.filter(k=>Object.hasOwn(settings,k));

 if(hasCanonical){
  return validateAcHeatingWindows(settings.ac_heating_windows);
 }

 if(presentLegacy.length>0){
  const sourceWindows=baseWindows?baseWindows:DEFAULT_AC_HEATING_WINDOWS;
  const windows=deepClone(sourceWindows);

  const morningKeys=presentLegacy.filter(k=>k==='heating_on_hour'||k==='heating_off_hour');
  if(morningKeys.length>0){
   const morningMatches=windows.filter(w=>w.id==='morning');
   if(morningMatches.length!==1){
    throw new Error(
     `Cannot apply legacy heating window patch: 'morning' window is ${morningMatches.length===0?'missing':'ambiguous'} in current schedule (found ${morningMatches.length} matches).`
    );
   }
   const mWin=morningMatches[0];
   if(Object.hasOwn(settings,'heating_on_hour')){
    const h=validateLegacyHour(settings.heating_on_hour,'heating_on_hour');
    mWin.start_minute=h*60;
   }
   if(Object.hasOwn(settings,'heating_off_hour')){
    const h=validateLegacyHour(settings.heating_off_hour,'heating_off_hour');
    mWin.end_minute=h*60;
   }
  }

  const eveningKeys=presentLegacy.filter(k=>k==='heating_evening_on_hour'||k==='heating_evening_off_hour');
  if(eveningKeys.length>0){
   const eveningMatches=windows.filter(w=>w.id==='evening');
   if(eveningMatches.length!==1){
    throw new Error(
     `Cannot apply legacy heating window patch: 'evening' window is ${eveningMatches.length===0?'missing':'ambiguous'} in current schedule (found ${eveningMatches.length} matches).`
    );
   }
   const eWin=eveningMatches[0];
   if(Object.hasOwn(settings,'heating_evening_on_hour')){
    const h=validateLegacyHour(settings.heating_evening_on_hour,'heating_evening_on_hour');
    eWin.start_minute=h*60;
   }
   if(Object.hasOwn(settings,'heating_evening_off_hour')){
    const h=validateLegacyHour(settings.heating_evening_off_hour,'heating_evening_off_hour');
    eWin.end_minute=h*60;
   }
  }

  return validateAcHeatingWindows(windows);
 }

 if(baseWindows!==null){
  return validateAcHeatingWindows(baseWindows);
 }

 return deepClone(DEFAULT_AC_HEATING_WINDOWS);
}

const SCHEDULE_SETTINGS=['ac_heating_windows','heating_schedule_mode'];

function formatScheduleConfirmationSummary(windows,mode,tz){
 const modeStr=mode==='continuous'?'Continuous (24h old model)':'Scheduled operating windows';
 const tzStr=(tz==='standard'||tz==='Standard fixed UTC+1')?'Standard fixed UTC+1':'Europe/Rome (DST)';
 let winStr='';
 if(!windows||!windows.length){
  winStr='no operating windows (0h 00m)';
 }else{
  winStr=windows.map((w,idx)=>{
   const sm=w.start_minute;
   const em=w.end_minute;
   const sText=formatAcScheduleMinute(sm);
   const eText=formatAcScheduleMinute(em);
   let semantics='';
   if(sm===em){
    semantics='inactive (0h 00m)';
   }else if(sm>em){
    const dur=(1440-sm)+em;
    const dh=Math.floor(dur/60);
    const dm=dur%60;
    semantics=`crosses midnight (${dh}h ${String(dm).padStart(2,'0')}m)`;
   }else if(sm===0&&em===1440){
    semantics='all day (24h 00m)';
   }else{
    const dur=em-sm;
    const dh=Math.floor(dur/60);
    const dm=dur%60;
    semantics=`${dh}h ${String(dm).padStart(2,'0')}m`;
   }
   const idLabel=w.id?`${w.id}: `:`Window ${idx+1}: `;
   return `${idLabel}${sText}–${eText} (${semantics})`;
  }).join('; ');
 }
 const continuousNote=mode==='continuous'?' [retained operating windows]':'';
 return `Mode: ${modeStr}${continuousNote}; Clock context: ${tzStr}; Windows: ${winStr}`;
}

function isScheduleGroupModified(){
 if(!schema?.defaults)return false;
 return SCHEDULE_SETTINGS.some(k=>schema.defaults[k]!==undefined&&settings[k]!==undefined&&!settingValuesEqual(schema.defaults[k],settings[k]));
}

function isResetAvailable(key){
 if(key==='ac_heating_windows'||key==='heating_schedule_mode'||key==='schedule_group'){
  return isScheduleGroupModified()||hasInvalidAcScheduleDraft();
 }
 if(key==='ac_heating_additional_demand_pct'){
  return isRecordModified(key)||hasInvalidAcDemandPctDraft();
 }
 return isRecordModified(key);
}

let acScheduleDraft=null;
let acScheduleDraftInvalid=false;

function hasInvalidAcScheduleDraft(){
 if(!acScheduleDraft)return false;
 return acScheduleDraft.some(r=>r.startError||r.endError);
}

function discardAcDrafts(){
 acScheduleDraft=null;
 acScheduleDraftInvalid=false;
 acDemandPctDraft=null;
 acDemandPctError=null;
}

function reconcileAcScheduleDraftWithRemote(remoteWindows){
 if(!Array.isArray(remoteWindows)||!acScheduleDraft)return;
 const reconciled=[];
 for(const w of remoteWindows){
  const existing=acScheduleDraft.find(r=>r.id===w.id);
  if(existing){
   if(!existing.startError)existing.startText=formatAcScheduleMinute(w.start_minute);
   if(!existing.endError)existing.endText=formatAcScheduleMinute(w.end_minute);
   reconciled.push(existing);
  }else{
   reconciled.push({
    id:w.id,
    startText:formatAcScheduleMinute(w.start_minute),
    endText:formatAcScheduleMinute(w.end_minute),
    startError:null,
    endError:null
   });
  }
 }
 acScheduleDraft=reconciled;
 acScheduleDraftInvalid=acScheduleDraft.some(r=>r.startError||r.endError);
}

function isAcScheduleGoverningActiveHeating(s=settings){
 if(!s)return false;
 if(s.load_scenario==='baseline')return false;
 if(Boolean(s.planning_heat_pump))return false;
 if(s.heating_schedule_mode==='continuous')return false;
 return true;
}

function shouldBlockAcScheduleRecompute(s=settings){
 return hasInvalidAcScheduleDraft()&&isAcScheduleGoverningActiveHeating(s);
}

let acDemandPctDraft=null;
let acDemandPctError=null;

function hasInvalidAcDemandPctDraft(){
 return acDemandPctError!==null;
}

function getAcDemandPctDraft(){
 return acDemandPctDraft;
}

function isAcDemandPctGoverningActiveHeating(s=settings){
 if(!s)return false;
 if(s.load_scenario==='baseline')return false;
 if(Boolean(s.planning_heat_pump))return false;
 return true;
}

function shouldBlockAcDemandPctRecompute(s=settings){
 return hasInvalidAcDemandPctDraft()&&isAcDemandPctGoverningActiveHeating(s);
}

function shouldBlockAcRecompute(s=settings){
 return shouldBlockAcScheduleRecompute(s)||shouldBlockAcDemandPctRecompute(s);
}

function validateAcDemandPct(val){
 if(val===null||val===undefined)return {valid:false,error:'Percentage is required (0 or greater).'};
 const str=typeof val==='string'?val.trim():String(val);
 if(str==='')return {valid:false,error:'Percentage is required (0 or greater).'};
 const num=Number(str);
 if(!Number.isFinite(num))return {valid:false,error:'Enter a valid finite percentage.'};
 if(num<0)return {valid:false,error:'Percentage must be 0 or greater.'};
 return {valid:true,value:Object.is(num,-0)?0:num,error:null};
}

function markResultsOutdated(){
 loanPending=true;
 acceptedInputFingerprint=null;
 invalidateLoanSolve();
 if(typeof markBatterySizingStale==='function')markBatterySizingStale();
 if(typeof markSolarSizingStale==='function')markSolarSizingStale();
 if(typeof markOptimizerStale==='function')markOptimizerStale();
 if(typeof studyRequestId!=='undefined'){studyRequestId++;studyFingerprint=null;}
 if(typeof invalidateBehaviorAndSensitivity==='function')invalidateBehaviorAndSensitivity();
 if(typeof invalidateQuoteComparison==='function')invalidateQuoteComparison();
 if(typeof invalidateAcHeatingComparison==='function')invalidateAcHeatingComparison();
 if(typeof invalidateDayhour==='function')invalidateDayhour();
 const statusEl=typeof document!=='undefined'?document.getElementById('status'):null;
 if(statusEl)statusEl.textContent='Inputs changed…';
 requestId++;
 clearTimeout(timer);
 flushSharedChangesWithoutEvaluation();
}

function handleAcHeatingDemandPctInput(rawVal,inputEl=null){
 const wasInvalid=hasInvalidAcDemandPctDraft();
 const validation=validateAcDemandPct(rawVal);
 const draftText=String(rawVal??'');
 const key='ac_heating_additional_demand_pct';
 const el=inputEl||(typeof document!=='undefined'?(document.getElementById(key)||document.querySelector(`[data-setting="${key}"]`)):null);
 const errEl=typeof document!=='undefined'?document.getElementById(`${key}_error`):null;

 if(!validation.valid){
  acDemandPctDraft=draftText;
  acDemandPctError=validation.error;
  if(el){
   if(el.value!==draftText)el.value=draftText;
   el.setAttribute?.('aria-invalid','true');
   el.setAttribute?.('aria-describedby',`${key}_error`);
   el.classList?.add?.('is-invalid');
  }
  if(errEl){
   errEl.textContent=validation.error;
   errEl.hidden=false;
  }
  if(isAcDemandPctGoverningActiveHeating(settings)){
   markResultsOutdated();
  }else{
   const batScenario=$('batScenario')?$('batScenario').value:settings?.load_scenario;
   if(isAcDemandPctGoverningActiveHeating({...settings,load_scenario:batScenario})){
    if(typeof markBatterySizingStale==='function')markBatterySizingStale();
   }
   const solScenario=$('solarScenario')?$('solarScenario').value:settings?.load_scenario;
   if(isAcDemandPctGoverningActiveHeating({...settings,load_scenario:solScenario})){
    if(typeof markSolarSizingStale==='function')markSolarSizingStale();
   }
   const optScenario=(typeof optimizerSearch==='function'?optimizerSearch()?.load_scenario:null)||($('filterLoad')?$('filterLoad').value:settings?.load_scenario);
   if(isAcDemandPctGoverningActiveHeating({...settings,load_scenario:optScenario})){
    if(typeof markOptimizerStale==='function')markOptimizerStale();
   }
  }
  updateControlModifiedState('ac_heating_additional_demand_pct');
 }else{
  acDemandPctDraft=null;
  acDemandPctError=null;
  if(el){
   el.removeAttribute?.('aria-invalid');
   el.removeAttribute?.('aria-describedby');
   el.classList?.remove?.('is-invalid');
  }
  if(errEl){
   errEl.textContent='';
   errEl.hidden=true;
  }
  const num=validation.value;
  const currentCanonical=settings[key];
  if(!settingValuesEqual(currentCanonical,num)){
   editSetting(key,num,el||'ac_demand_pct_input');
  }else if(wasInvalid){
   schedule();
   updateControlModifiedState('ac_heating_additional_demand_pct');
  }
 }
}

function getAcScheduleDraft(){
 if(acScheduleDraft===null){
  const source=Array.isArray(settings?.ac_heating_windows)?settings.ac_heating_windows:[];
  acScheduleDraft=source.map(w=>({
   id:String(w.id||''),
   startText:formatAcScheduleMinute(w.start_minute),
   endText:formatAcScheduleMinute(w.end_minute),
   startError:null,
   endError:null
  }));
  acScheduleDraftInvalid=false;
 }
 return acScheduleDraft;
}

const AC_TIMELINE_HOURS=[0,6,12,18,24];

function getAcScheduleUnionIntervals(items){
 if(!items||!items.length)return [];
 const mask=new Array(1440).fill(false);
 for(const item of items){
  let sm,em;
  if(typeof item.startText==='string'){
   sm=parseAcScheduleTime(item.startText,false);
   em=parseAcScheduleTime(item.endText,true);
  }else{
   sm=item.start_minute;
   em=item.end_minute;
  }
  if(sm==null||em==null||!Number.isFinite(sm)||!Number.isFinite(em))continue;
  if(sm===em)continue;
  if(sm<em){
   const endClamped=Math.min(1440,em);
   for(let i=sm;i<endClamped;i++)mask[i]=true;
  }else{
   for(let i=sm;i<1440;i++)mask[i]=true;
   for(let i=0;i<em;i++)mask[i]=true;
  }
 }
 const intervals=[];
 let inInterval=false;
 let startMin=0;
 for(let i=0;i<1440;i++){
  if(mask[i]&&!inInterval){
   inInterval=true;
   startMin=i;
  }else if(!mask[i]&&inInterval){
   inInterval=false;
   intervals.push({start_minute:startMin,end_minute:i});
  }
 }
 if(inInterval){
  intervals.push({start_minute:startMin,end_minute:1440});
 }
 return intervals;
}

function renderAcScheduleTimelineContent(){
 const draft=getAcScheduleDraft();
 const isInvalid=hasInvalidAcScheduleDraft();
 const isContinuous=settings?.heating_schedule_mode==='continuous';

 const scaleTicks=AC_TIMELINE_HOURS.map(h=>{
  const pct=(h/24)*100;
  const label=formatAcScheduleMinute(h*60);
  return `<span class="ac-timeline-tick" data-hour="${h}" style="left:${pct.toFixed(2)}%;"><span class="ac-timeline-tick-mark"></span><span class="ac-timeline-tick-label">${label}</span></span>`;
 }).join('');

 const scaleHtml=`<div class="ac-timeline-scale" aria-hidden="true">${scaleTicks}</div>`;

 let unionSegmentsHtml='';
 if(isContinuous){
  unionSegmentsHtml=`<div class="ac-timeline-segment ac-timeline-segment-continuous" style="left:0%;width:100%;" title="Continuous 24h heating active"><span class="ac-timeline-segment-label">Continuous 24h heating</span></div>`;
 }else if(isInvalid){
  unionSegmentsHtml=`<div class="ac-timeline-segment ac-timeline-segment-invalid" style="left:0%;width:100%;" title="Schedule draft invalid"><span class="ac-timeline-segment-label">Draft invalid — fix errors below</span></div>`;
 }else if(!draft.length){
  unionSegmentsHtml=`<div class="ac-timeline-segment-empty"><span class="ac-timeline-empty-label">No operating windows (0h 00m)</span></div>`;
 }else{
  const intervals=getAcScheduleUnionIntervals(draft);
  if(!intervals.length){
   unionSegmentsHtml=`<div class="ac-timeline-segment-empty"><span class="ac-timeline-empty-label">0h 00m / day permitted</span></div>`;
  }else{
   unionSegmentsHtml=intervals.map(iv=>{
    const left=(iv.start_minute/1440)*100;
    const width=((iv.end_minute-iv.start_minute)/1440)*100;
    const dur=iv.end_minute-iv.start_minute;
    const dh=Math.floor(dur/60);
    const dm=dur%60;
    const durText=`${dh}h ${String(dm).padStart(2,'0')}m`;
    const label=`Permitted: ${formatAcScheduleMinute(iv.start_minute)}–${formatAcScheduleMinute(iv.end_minute)} (${durText})`;
    return `<div class="ac-timeline-segment ac-timeline-segment-union" style="left:${left.toFixed(4)}%;width:${width.toFixed(4)}%;" title="${esc(label)}"><span class="ac-timeline-segment-label">${esc(label)}</span></div>`;
   }).join('');
  }
 }

 const unionTrackHtml=`<div class="ac-timeline-track ac-timeline-track-union" data-timeline-track="union">`+
  `<div class="ac-timeline-track-header">`+
   `<span class="ac-timeline-track-name">Permitted heating (union)</span>`+
  `</div>`+
  `<div class="ac-timeline-track-bar">${unionSegmentsHtml}</div>`+
 `</div>`;

 let windowTracksHtml='';
 if(!draft.length){
  windowTracksHtml='<div class="ac-timeline-empty-notice">No operating windows defined.</div>';
 }else{
  windowTracksHtml=draft.map((row,idx)=>{
   const rowId=esc(row.id);
   const titlePrefix=isContinuous?`Retained Window ${idx+1}`:`Window ${idx+1}`;
   let segsHtml='';

   const sm=(row.startError||row.endError)?null:parseAcScheduleTime(row.startText,false);
   const em=(row.startError||row.endError)?null:parseAcScheduleTime(row.endText,true);
   const isInactive=(sm!==null&&em!==null&&sm===em);

   if(row.startError||row.endError||sm===null||em===null){
    segsHtml=`<div class="ac-timeline-segment ac-timeline-segment-invalid" style="left:0%;width:100%;"><span class="ac-timeline-segment-label">W${idx+1}: Invalid input</span></div>`;
   }else if(isInactive){
    const left=(sm/1440)*100;
    const titleText=`${titlePrefix}: Inactive (${row.startText})`;
    const edgeAttr=sm===0?' data-edge="start"':(sm>=1439?' data-edge="end"':'');
    segsHtml=`<div class="ac-timeline-marker ac-timeline-marker-inactive" data-inactive="true"${edgeAttr} style="left:${left.toFixed(4)}%;" title="${esc(titleText)}" role="img" aria-label="${esc(titleText)}"></div>`;
   }else if(sm<em){
    const isAllDay=(sm===0&&em===1440);
    const left=(sm/1440)*100;
    const width=((em-sm)/1440)*100;
    const dur=em-sm;
    const dh=Math.floor(dur/60);
    const dm=dur%60;
    const durText=`${dh}h ${String(dm).padStart(2,'0')}m`;
    const segClass=isAllDay?'ac-timeline-segment-allday':'ac-timeline-segment-sameday';
    const label=isAllDay?`W${idx+1}: All day (00:00–24:00)`:`W${idx+1}: ${esc(row.startText)}–${esc(row.endText)} (${durText})`;
    segsHtml=`<div class="ac-timeline-segment ${segClass}" style="left:${left.toFixed(4)}%;width:${width.toFixed(4)}%;" title="${esc(titlePrefix)}: ${esc(label)}"><span class="ac-timeline-segment-label">${esc(label)}</span></div>`;
   }else{
    const durLate=1440-sm;
    let segLate='';
    if(durLate>0){
     const leftLate=(sm/1440)*100;
     const widthLate=((1440-sm)/1440)*100;
     const dhLate=Math.floor(durLate/60);
     const dmLate=durLate%60;
     const durLateText=`${dhLate}h ${String(dmLate).padStart(2,'0')}m`;
     const labelLate=`W${idx+1} (late): ${esc(row.startText)}–24:00`;
     segLate=`<div class="ac-timeline-segment ac-timeline-segment-overnight ac-timeline-segment-overnight-late" data-timeline-split="late" style="left:${leftLate.toFixed(4)}%;width:${widthLate.toFixed(4)}%;" title="${esc(titlePrefix)}: ${esc(row.startText)}–24:00 (${durLateText})"><span class="ac-timeline-segment-label">${esc(labelLate)}</span></div>`;
    }

    const durEarly=em;
    let segEarly='';
    if(durEarly>0){
     const widthEarly=(em/1440)*100;
     const dhEarly=Math.floor(durEarly/60);
     const dmEarly=durEarly%60;
     const durEarlyText=`${dhEarly}h ${String(dmEarly).padStart(2,'0')}m`;
     const labelEarly=`W${idx+1} (early): 00:00–${esc(row.endText)}`;
     segEarly=`<div class="ac-timeline-segment ac-timeline-segment-overnight ac-timeline-segment-overnight-early" data-timeline-split="early" style="left:0%;width:${widthEarly.toFixed(4)}%;" title="${esc(titlePrefix)}: 00:00–${esc(row.endText)} (${durEarlyText})"><span class="ac-timeline-segment-label">${esc(labelEarly)}</span></div>`;
    }

    segsHtml=segEarly+segLate;
   }

   const trackModClass=isContinuous?' is-retained':'';
   const statusHtml=isInactive?' <span class="ac-timeline-track-status ac-timeline-status-inactive">Inactive (0h 00m)</span>':'';
   return `<div class="ac-timeline-track ac-timeline-track-window${trackModClass}" data-window-id="${rowId}">`+
    `<div class="ac-timeline-track-header">`+
     `<span class="ac-timeline-track-name">${esc(titlePrefix)}</span>`+
     `<span class="ac-timeline-track-times">${esc(row.startText)} – ${esc(row.endText)}${statusHtml}</span>`+
    `</div>`+
    `<div class="ac-timeline-track-bar">${segsHtml}</div>`+
   `</div>`;
  }).join('');
 }

 return `<div class="ac-timeline-inner">`+
  scaleHtml+
  `<div class="ac-timeline-tracks">`+
   unionTrackHtml+
   `<div class="ac-timeline-windows-group">${windowTracksHtml}</div>`+
  `</div>`+
 `</div>`;
}

function updateAcScheduleTimeline(){
 if(typeof document==='undefined')return;
 const timelineEl=document.getElementById('acScheduleTimeline');
 if(timelineEl&&timelineEl!==document.getElementById('controls')){
  timelineEl.innerHTML=renderAcScheduleTimelineContent();
 }
}

function renderAcHeatingScheduleControl(record,isCompact=false){
 const n=record.name;
 const rawLabel=record.label||FRIENDLY_NAMES[n]||title(n);
 const label=esc(rawLabel);
 const isMod=typeof isRecordModified==='function'
  ?isRecordModified(n)
  :(schema?.defaults?.[n]!==undefined&&settings[n]!==undefined&&!settingValuesEqual(schema.defaults[n],settings[n]));
 const resetAvailable=typeof isResetAvailable==='function'?isResetAvailable(n):isMod;
 const hpActive=Boolean(settings.planning_heat_pump);
 const isContinuous=settings.heating_schedule_mode==='continuous';
 const tzMode=settings.timezone_mode||schema?.defaults?.timezone_mode||'civil';
 const tz=tzMode==='standard'?'Standard fixed UTC+1':'Europe/Rome (DST)';

 const draft=getAcScheduleDraft();
 const isInvalid=hasInvalidAcScheduleDraft();
 const unionMins=isInvalid?0:calculateAcScheduleUnionMinutes(draft);
 const unionText=isInvalid
  ?'Draft invalid (fix errors below)'
  :(isContinuous?'24h 00m / day (continuous 24h)':formatAcScheduleUnionDuration(unionMins));
 const modeText=isContinuous?'Continuous (24h old model)':'Scheduled operating windows';

 let rowsHtml='';
 if(!draft.length){
  rowsHtml='<div class="ac-schedule-empty" id="acScheduleEmpty">No operating windows defined. Active AC space-heating demand requires at least one operating window.</div>';
 }else{
  rowsHtml=draft.map((row,idx)=>{
   const rowId=esc(row.id);
   let badgeClass='badge-duration';
   let badgeText='';

   const sm=parseAcScheduleTime(row.startText,false);
   const em=parseAcScheduleTime(row.endText,true);

   if(row.startError||row.endError){
    badgeClass='badge-invalid';
    badgeText='Invalid input';
   }else{
    if(sm===em){
     badgeClass='badge-inactive';
     badgeText='Inactive window';
    }else if(sm>em){
     badgeClass='badge-overnight';
     badgeText='Crosses midnight';
    }else{
     const dur=em-sm;
     const dh=Math.floor(dur/60);
     const dm=dur%60;
     badgeClass='badge-duration';
     badgeText=`${dh}h ${String(dm).padStart(2,'0')}m`;
    }
   }

   const disabledAttr=(isContinuous||hpActive)?' disabled':'';
   const disabledRangeAttr=(isContinuous||hpActive||isInvalid)?' disabled':'';
   const startVal=(sm!==null&&!row.startError)?sm:0;
   const endVal=(em!==null&&!row.endError)?em:0;
   const startErrAttr=row.startError?` aria-invalid="true" aria-describedby="ac_window_start_err_${rowId}"`:'';
   const endErrAttr=row.endError?` aria-invalid="true" aria-describedby="ac_window_end_err_${rowId}"`:'';

   return `<div class="ac-window-row" data-window-id="${rowId}" id="ac_window_row_${rowId}">`+
    `<div class="ac-window-row-header">`+
      `<span class="ac-window-label">Window ${idx+1}</span>`+
      `<span class="ac-window-badge ${badgeClass}" id="ac_window_badge_${rowId}">${esc(badgeText)}</span>`+
    `</div>`+
    `<div class="ac-window-fields">`+
      `<div class="ac-field-group">`+
        `<div class="ac-field-header">`+
          `<label for="ac_window_start_${rowId}">Start</label>`+
          `<span class="ac-range-val-badge" id="ac_window_start_val_${rowId}">${esc(row.startText)}</span>`+
        `</div>`+
        `<input type="text" inputmode="numeric" id="ac_window_start_${rowId}" class="ac-time-input${row.startError?' is-invalid':''}" data-ac-window-id="${rowId}" data-ac-time-type="start" value="${esc(row.startText)}" placeholder="HH:MM" pattern="([01][0-9]|2[0-3]):[0-5][0-9]" aria-label="Window ${idx+1} start time (00:00 to 23:59)"${startErrAttr}${disabledAttr}>`+
        `<input type="range" class="ac-range-slider" id="ac_window_start_range_${rowId}" data-ac-window-id="${rowId}" data-ac-range-type="start" min="0" max="1439" step="1" value="${startVal}" aria-label="Window ${idx+1} start time range" aria-valuemin="0" aria-valuemax="1439" aria-valuenow="${startVal}" aria-valuetext="${esc(row.startText)}"${disabledRangeAttr}>`+
        `<div id="ac_window_start_err_${rowId}" class="ac-field-error" role="alert"${row.startError?'':' hidden'}>${esc(row.startError||'')}</div>`+
      `</div>`+
      `<div class="ac-field-group">`+
        `<div class="ac-field-header">`+
          `<label for="ac_window_end_${rowId}">End</label>`+
          `<span class="ac-range-val-badge" id="ac_window_end_val_${rowId}">${esc(row.endText)}</span>`+
        `</div>`+
        `<input type="text" inputmode="numeric" id="ac_window_end_${rowId}" class="ac-time-input${row.endError?' is-invalid':''}" data-ac-window-id="${rowId}" data-ac-time-type="end" value="${esc(row.endText)}" placeholder="HH:MM" pattern="([01][0-9]|2[0-3]):[0-5][0-9]|24:00" aria-label="Window ${idx+1} end time (00:00 to 24:00)"${endErrAttr}${disabledAttr}>`+
        `<input type="range" class="ac-range-slider" id="ac_window_end_range_${rowId}" data-ac-window-id="${rowId}" data-ac-range-type="end" min="0" max="1440" step="1" value="${endVal}" aria-label="Window ${idx+1} end time range" aria-valuemin="0" aria-valuemax="1440" aria-valuenow="${endVal}" aria-valuetext="${esc(row.endText)}"${disabledRangeAttr}>`+
        `<div id="ac_window_end_err_${rowId}" class="ac-field-error" role="alert"${row.endError?'':' hidden'}>${esc(row.endError||'')}</div>`+
      `</div>`+
      `<div class="ac-window-actions">`+
        `<button type="button" class="ac-btn-remove" data-remove-window="${rowId}" id="ac_window_remove_${rowId}" aria-label="Remove Window ${idx+1}"${disabledAttr}>Remove</button>`+
      `</div>`+
    `</div>`+
   `</div>`;
  }).join('');
 }

 const disabledBtnAttr=(isContinuous||hpActive)?' disabled':'';
 const continuousNote=isContinuous
  ?'<div class="ac-schedule-continuous-note" role="note">Continuous mode active (24h). Saved windows are retained and will apply if switched to scheduled mode.</div>'
  :'';

 return `<div class="control control-ac-schedule${isMod?' is-modified':''}" data-control="${n}">`+
  `<div class="control-header">`+
    `<label id="ac_schedule_label">${label}</label>`+
    `<button type="button" class="control-reset" data-reset-setting="${n}" aria-label="Reset ${label}" title="Reset ${label} to its saved default"${resetAvailable?'':' hidden'}>Reset</button>`+
  `</div>`+
  `<p class="ac-schedule-instructions" id="ac_schedule_instructions">Enter operating windows in HH:MM format (start 00:00–23:59, end 00:00–24:00 at minute precision). End time 24:00 represents midnight at end of day.</p>`+
  `<div class="ac-schedule-summary" id="acScheduleSummary" role="status" aria-live="polite">`+
    `<div class="ac-schedule-union-duration">Permitted heating: <strong id="acScheduleUnionText">${esc(unionText)}</strong></div>`+
    `<div class="ac-schedule-meta-info"><span class="ac-schedule-mode-label">Mode: ${esc(modeText)}</span> · <span class="ac-schedule-tz-label">Timezone: ${esc(tz)}</span></div>`+
  `</div>`+
  continuousNote+
  `<div class="ac-schedule-timeline" id="acScheduleTimeline" role="region" aria-label="AC heating schedule 24-hour timeline">`+
    renderAcScheduleTimelineContent()+
  `</div>`+
  `<div class="ac-schedule-windows-list" id="acScheduleWindowsList" role="group" aria-labelledby="ac_schedule_label" aria-describedby="ac_schedule_instructions">`+
    rowsHtml+
  `</div>`+
  `<div class="ac-schedule-actions">`+
    `<button type="button" class="ac-btn-add" id="acWindowAddBtn" data-ac-add-window="true" aria-label="Add operating window"${disabledBtnAttr}>+ Add window</button>`+
  `</div>`+
  `<details class="control-details">`+
    `<summary>Details</summary>`+
    `<div class="control-details-body">`+
      `<div class="control-details-meta">`+
        `<span class="tag ${esc(record.status||'proposed')}">${esc(record.status||'proposed')}</span>`+
        `<small class="control-source" title="${esc(record.source||'')}">Source: ${esc(record.source||'')}</small>`+
      `</div>`+
      `<small class="control-desc">${esc(record.description||'Space-heating operating windows (start-inclusive, end-exclusive local clock minutes from 0..1440).')}</small>`+
      `<div class="control-details-actions">`+
        `<button type="button" data-save-default="${n}" aria-label="Save ${label} as default">Save as shared default</button>`+
      `</div>`+
    `</div>`+
  `</details>`+
 `</div>`;
}

function handleAcScheduleTimeInput(windowId,timeType,text,inputEl=null){
 const draft=getAcScheduleDraft();
 const row=draft.find(r=>r.id===windowId);
 if(!row)return;

 const isEnd=timeType==='end';
 const parsed=parseAcScheduleTime(text,isEnd);
 if(isEnd){
  row.endText=text;
  row.endError=(parsed===null)?'End time must be HH:MM between 00:00 and 24:00':null;
 }else{
  row.startText=text;
  row.startError=(parsed===null)?'Start time must be HH:MM between 00:00 and 23:59':null;
 }

 const rowHasError=Boolean(row.startError||row.endError);
 const draftHasError=draft.some(r=>r.startError||r.endError);
 const wasInvalid=acScheduleDraftInvalid;
 acScheduleDraftInvalid=draftHasError;

 const el=inputEl||(typeof document!=='undefined'?document.getElementById(`ac_window_${timeType}_${windowId}`):null);
 const errEl=typeof document!=='undefined'?document.getElementById(`ac_window_${timeType}_err_${windowId}`):null;
 const errorMsg=isEnd?row.endError:row.startError;

 if(el){
  if(el.value!==text)el.value=text;
  if(errorMsg){
   el.setAttribute?.('aria-invalid','true');
   el.setAttribute?.('aria-describedby',`ac_window_${timeType}_err_${windowId}`);
   el.classList?.add?.('is-invalid');
  }else{
   el.removeAttribute?.('aria-invalid');
   el.removeAttribute?.('aria-describedby');
   el.classList?.remove?.('is-invalid');
  }
 }
 if(errEl){
  if(errorMsg){
   errEl.textContent=errorMsg;
   errEl.hidden=false;
  }else{
   errEl.textContent='';
   errEl.hidden=true;
  }
 }

 const badgeEl=typeof document!=='undefined'?document.getElementById(`ac_window_badge_${windowId}`):null;
 if(badgeEl){
  if(rowHasError){
   badgeEl.className='ac-window-badge badge-invalid';
   badgeEl.textContent='Invalid input';
  }else{
   const sm=parseAcScheduleTime(row.startText,false);
   const em=parseAcScheduleTime(row.endText,true);
   if(sm===em){
    badgeEl.className='ac-window-badge badge-inactive';
    badgeEl.textContent='Inactive window';
   }else if(sm>em){
    badgeEl.className='ac-window-badge badge-overnight';
    badgeEl.textContent='Crosses midnight';
   }else{
    const dur=em-sm;
    const dh=Math.floor(dur/60);
    const dm=dur%60;
    badgeEl.className='ac-window-badge badge-duration';
    badgeEl.textContent=`${dh}h ${String(dm).padStart(2,'0')}m`;
   }
  }
 }

 const rangeEl=typeof document!=='undefined'?document.getElementById(`ac_window_${timeType}_range_${windowId}`):null;
 if(rangeEl&&parsed!==null){
  rangeEl.value=String(parsed);
  rangeEl.setAttribute?.('aria-valuenow',String(parsed));
  rangeEl.setAttribute?.('aria-valuetext',text);
 }
 const valBadge=typeof document!=='undefined'?document.getElementById(`ac_window_${timeType}_val_${windowId}`):null;
 if(valBadge){
  valBadge.textContent=text;
 }

 const isContinuous=settings?.heating_schedule_mode==='continuous';
 const hpActive=Boolean(settings?.planning_heat_pump);
 if(typeof document!=='undefined'){
  for(const r of draft){
   const sRange=document.getElementById(`ac_window_start_range_${r.id}`);
   const eRange=document.getElementById(`ac_window_end_range_${r.id}`);
   if(sRange){
    if(draftHasError||isContinuous||hpActive){
     sRange.disabled=true;
     sRange.setAttribute?.('disabled','');
    }else{
     sRange.disabled=false;
     sRange.removeAttribute?.('disabled');
    }
   }
   if(eRange){
    if(draftHasError||isContinuous||hpActive){
     eRange.disabled=true;
     eRange.setAttribute?.('disabled','');
    }else{
     eRange.disabled=false;
     eRange.removeAttribute?.('disabled');
    }
   }
  }
 }

 updateAcScheduleTimeline();

 const summaryTextEl=typeof document!=='undefined'?document.getElementById('acScheduleUnionText'):null;
 if(draftHasError){
  if(summaryTextEl)summaryTextEl.textContent='Draft invalid (fix errors below)';
  if(isAcScheduleGoverningActiveHeating(settings)){
   markResultsOutdated();
  }else{
   const batScenario=$('batScenario')?$('batScenario').value:settings?.load_scenario;
   if(isAcScheduleGoverningActiveHeating({...settings,load_scenario:batScenario})){
    if(typeof markBatterySizingStale==='function')markBatterySizingStale();
   }
   const solScenario=$('solarScenario')?$('solarScenario').value:settings?.load_scenario;
   if(isAcScheduleGoverningActiveHeating({...settings,load_scenario:solScenario})){
    if(typeof markSolarSizingStale==='function')markSolarSizingStale();
   }
   const optScenario=(typeof optimizerSearch==='function'?optimizerSearch()?.load_scenario:null)||($('filterLoad')?$('filterLoad').value:settings?.load_scenario);
   if(isAcScheduleGoverningActiveHeating({...settings,load_scenario:optScenario})){
    if(typeof markOptimizerStale==='function')markOptimizerStale();
   }
  }
 }else{
  const unionMins=calculateAcScheduleUnionMinutes(draft);
  if(summaryTextEl){
   summaryTextEl.textContent=isContinuous
    ?'24h 00m / day (continuous 24h)'
    :formatAcScheduleUnionDuration(unionMins);
  }
  const canonical=draft.map(r=>({
   id:r.id,
   start_minute:parseAcScheduleTime(r.startText,false),
   end_minute:parseAcScheduleTime(r.endText,true)
  }));
  if(!settingValuesEqual(settings.ac_heating_windows,canonical)){
   editSetting('ac_heating_windows',deepClone(canonical),'ac_editor');
  }else if(wasInvalid){
   schedule();
  }
 }
 updateControlModifiedState('ac_heating_windows');
}

function handleAcScheduleRangeInput(windowId,rangeType,minuteVal,inputEl=null){
 if(hasInvalidAcScheduleDraft())return;
 if(settings?.heating_schedule_mode==='continuous'||Boolean(settings?.planning_heat_pump))return;
 const isEnd=rangeType==='end';
 const minute=Math.max(0,Math.min(isEnd?1440:1439,Math.round(Number(minuteVal)||0)));
 const timeText=formatAcScheduleMinute(minute);
 handleAcScheduleTimeInput(windowId,rangeType,timeText,null);
}

function addAcScheduleWindow(){
 const draft=getAcScheduleDraft();
 const newId=generateUniqueWindowId(draft);
 draft.push({
  id:newId,
  startText:'00:00',
  endText:'00:00',
  startError:null,
  endError:null
 });
 acScheduleDraftInvalid=draft.some(r=>r.startError||r.endError);
 if(!acScheduleDraftInvalid){
  const canonical=draft.map(r=>({
   id:r.id,
   start_minute:parseAcScheduleTime(r.startText,false),
   end_minute:parseAcScheduleTime(r.endText,true)
  }));
  editSetting('ac_heating_windows',deepClone(canonical),'ac_editor');
 }
 if(typeof renderControls==='function'){
  renderControls();
 }
 updateAcScheduleTimeline();
 const startEl=typeof document!=='undefined'?document.getElementById(`ac_window_start_${newId}`):null;
 startEl?.focus?.();
}

function removeAcScheduleWindow(windowId){
 const draft=getAcScheduleDraft();
 const idx=draft.findIndex(r=>r.id===windowId);
 if(idx===-1)return;
 draft.splice(idx,1);
 acScheduleDraftInvalid=draft.some(r=>r.startError||r.endError);
 if(!acScheduleDraftInvalid){
  const canonical=draft.map(r=>({
   id:r.id,
   start_minute:parseAcScheduleTime(r.startText,false),
   end_minute:parseAcScheduleTime(r.endText,true)
  }));
  editSetting('ac_heating_windows',deepClone(canonical),'ac_editor');
 }else{
  markResultsOutdated();
 }
 if(typeof renderControls==='function'){
  renderControls();
 }
 updateAcScheduleTimeline();
 if(typeof document!=='undefined'){
  if(draft.length===0){
   const addBtn=document.getElementById('acWindowAddBtn');
   addBtn?.focus?.();
  }else{
   const nextIdx=idx<draft.length?idx:draft.length-1;
   const targetRow=draft[nextIdx];
   if(targetRow){
    const focusTarget=document.getElementById(`ac_window_start_${targetRow.id}`)||document.getElementById(`ac_window_remove_${targetRow.id}`);
    focusTarget?.focus?.();
   }
  }
 }
}

function updateControlModifiedState(key){
 if(!schema?.defaults)return;
 const isMod=typeof isRecordModified==='function'?isRecordModified(key):(schema.defaults[key]!==undefined&&settings[key]!==undefined&&!settingValuesEqual(schema.defaults[key],settings[key]));
 const resetAvail=typeof isResetAvailable==='function'?isResetAvailable(key):isMod;
 document.querySelectorAll?.(`[data-control="${key}"]`)?.forEach?.(el=>{
  el.classList?.toggle?.('is-modified',isMod);
  const resetBtn=el.querySelector?.('[data-reset-setting]');
  if(resetBtn)resetBtn.hidden=!resetAvail;
 });
 if(key==='electrification_capex_include_baseline'||key==='electrification_capex'){
  const isCapexMod=typeof isRecordModified==='function'?isRecordModified('electrification_capex'):false;
  document.querySelectorAll?.('[data-control="electrification_capex"]').forEach?.(el=>{
   el.classList?.toggle?.('is-modified',isCapexMod);
   const resetBtn=el.querySelector?.('[data-reset-setting]');
   if(resetBtn)resetBtn.hidden=!isCapexMod;
  });
 }
 if(SCHEDULE_SETTINGS.includes(key)||key==='schedule_group'){
  const isSchedMod=isScheduleGroupModified();
  const schedResetAvail=isSchedMod||hasInvalidAcScheduleDraft();
  for(const k of SCHEDULE_SETTINGS){
   document.querySelectorAll?.(`[data-control="${k}"]`)?.forEach?.(el=>{
    el.classList?.toggle?.('is-modified',isSchedMod);
    const resetBtn=el.querySelector?.('[data-reset-setting]');
    if(resetBtn)resetBtn.hidden=!schedResetAvail;
   });
  }
 }
 if(key==='ac_heating_additional_demand_pct'){
  const isPctMod=isRecordModified('ac_heating_additional_demand_pct');
  const pctResetAvail=isPctMod||hasInvalidAcDemandPctDraft();
  document.querySelectorAll?.(`[data-control="${key}"]`)?.forEach?.(el=>{
   el.classList?.toggle?.('is-modified',isPctMod);
   const resetBtn=el.querySelector?.('[data-reset-setting]');
   if(resetBtn)resetBtn.hidden=!pctResetAvail;
  });
 }
 if(BATTERY_SETTINGS.includes(key)||key==='battery_group'){
  const isBatMod=BATTERY_SETTINGS.some(k=>schema?.defaults?.[k]!==undefined&&settings[k]!==undefined&&!settingValuesEqual(schema.defaults[k],settings[k]));
  document.querySelectorAll?.('.battery-group-control,[data-control="battery_group"]').forEach?.(el=>{
   el.classList?.toggle?.('is-modified',isBatMod);
   const resetBtn=el.querySelector?.('[data-reset-setting]');
   if(resetBtn)resetBtn.hidden=!isBatMod;
  });
 }
}

function updateCurrentSystemSummary(){
 const summary=$('systemSummary');
 if(!summary||summary===$('controls')||!schema?.records)return;
 const totalPanels=(typeof activeResultProfile!=='undefined'&&activeResultProfile)?Object.values(settings.roof_panels||{}).reduce((a,b)=>a+Number(b),0):panelKeys.reduce((sum,k)=>sum+(Number(settings[k])||0),0);
 const totalKwp=totalPanels*(Number(settings.panel_watts)||400)/1000;
 const activeRoofs=(typeof activeResultProfile!=='undefined'&&activeResultProfile)?Object.values(settings.roof_panels||{}).filter(n=>n>0).length:panelKeys.filter(k=>(Number(settings[k])||0)>0).length;
 const storageKwh=Number(settings.battery_kwh)||0;

 const panelsEl=$('summarySolarPanels');
 const roofsEl=$('summaryActiveRoofs');
 const storageEl=$('summaryStorage');

 if(panelsEl&&roofsEl&&storageEl){
  panelsEl.innerHTML=`<strong>${totalPanels}</strong> panels (${nf(totalKwp,2)} kWp)`;
  roofsEl.innerHTML=`<strong>${activeRoofs}</strong> ${activeRoofs===1?'roof':'roofs'}`;
  storageEl.innerHTML=`<strong>${nf(storageKwh,2)}</strong> kWh`;
 }else{
  summary.innerHTML=`<div class="system-summary-title">Current system</div>`+
   `<div class="system-summary-rows">`+
    `<div class="system-summary-row"><span class="system-summary-label">Solar PV</span><span class="system-summary-value" id="summarySolarPanels"><strong>${totalPanels}</strong> panels (${nf(totalKwp,2)} kWp)</span></div>`+
    `<div class="system-summary-row"><span class="system-summary-label">Active roofs</span><span class="system-summary-value" id="summaryActiveRoofs"><strong>${activeRoofs}</strong> ${activeRoofs===1?'roof':'roofs'}</span></div>`+
    `<div class="system-summary-row"><span class="system-summary-label">Usable storage</span><span class="system-summary-value" id="summaryStorage"><strong>${nf(storageKwh,2)}</strong> kWh</span></div>`+
   `</div>`+
   `<a href="#design" data-view-target="design" class="system-summary-link">Configure system →</a>`;
 }
}
function hasLoanMode(){return Object.hasOwn(schema?.defaults||{},'loan_mode')&&schema.records.some(r=>r.name==='loan_mode');}
function loanUsesAmount(){return hasLoanMode()&&settings.loan_mode==='amount';}
function conditionalVisible(el){
 const name=el.dataset?.control;
 const hpActive=Boolean(settings.planning_heat_pump);
 if(name==='planning_heat_pump')return true;
 if(name&&HP_LEGACY_KEYS.has(name)){
  if(hpActive)return false;
 }
 if(name&&HP_ALL_KEYS_SET.has(name)){
  if(!hpActive)return false;
 }
 if(name==='gas_disconnect_cost'){
  if(!hpActive)return true;
  return Boolean(settings.hp_disconnect_gas);
 }
 const key=el.dataset?.when,mode=settings[key];
 if(!key)return true;
 if(key==='loan_active')return Number(loanUsesAmount()?settings.loan_amount:settings.loan_fraction)>0;
 if(key==='hp_hygiene_enabled')return Boolean(settings.hp_hygiene_enabled);
 if(key==='hp_electric_cooking')return Boolean(settings.hp_electric_cooking);
 if(key==='hp_disconnect_gas')return Boolean(settings.hp_disconnect_gas);
 if(el.dataset?.whenValue)return mode===el.dataset.whenValue;
 if(key==='loan_mode'){
  if(!hasLoanMode())return el.dataset?.control==='loan_fraction';
  return mode==='fraction'?el.dataset?.control==='loan_fraction':el.dataset?.control==='loan_amount';
 }
 return false;
}

function getSidebarRoot(){
 if(typeof document==='undefined')return null;
 const aside=document.querySelector?.('aside');
 if(aside)return aside;
 const controls=document.getElementById?.('controls');
 if(controls?.closest){
  const parentAside=controls.closest('aside');
  if(parentAside)return parentAside;
 }
 return document;
}

function getCurrentViewName(){
 if(typeof document!=='undefined'){
  const activeNav=document.querySelector?.('nav button.active')?.dataset?.view;
  if(activeNav)return LEGACY_ID_MAP[activeNav]||activeNav;
 }
 return LEGACY_ID_MAP[currentViewName]||currentViewName||'summary';
}

function isRecordInCurrentView(record,view=getCurrentViewName()){
 if(!record)return false;
 if(COMMON_KEYS.has(record.name))return true;
 const canonicalView=LEGACY_ID_MAP[view]||view;
 const mapping=VIEW_RELEVANT_INPUTS[canonicalView];
 if(!mapping)return true;
 if(mapping.all)return true;
 if(mapping.keys&&mapping.keys.has(record.name))return true;
 if(mapping.groups&&mapping.groups.has(record.group))return true;
 return false;
}

function isRecordModified(key){
 if(key==='battery_group')return BATTERY_SETTINGS.some(k=>schema?.defaults?.[k]!==undefined&&settings[k]!==undefined&&!settingValuesEqual(schema.defaults[k],settings[k]));
 if(key==='schedule_group'||key==='ac_heating_windows'||key==='heating_schedule_mode')return isScheduleGroupModified();
 if(key==='electrification_capex')return (schema?.defaults?.electrification_capex!==undefined&&settings.electrification_capex!==undefined&&!settingValuesEqual(schema.defaults.electrification_capex,settings.electrification_capex))||(schema?.defaults?.electrification_capex_include_baseline!==undefined&&settings.electrification_capex_include_baseline!==undefined&&!settingValuesEqual(schema.defaults.electrification_capex_include_baseline,settings.electrification_capex_include_baseline));
 if(key==='ac_heating_additional_demand_pct'){
  const defVal=schema?.defaults?.ac_heating_additional_demand_pct??0;
  const curVal=settings?.ac_heating_additional_demand_pct??0;
  return !settingValuesEqual(defVal,curVal);
 }
 if(!schema?.defaults||!Object.hasOwn(schema.defaults,key))return false;
 return !settingValuesEqual(schema.defaults[key],settings[key]);
}

function isRecordConditionallyAvailable(record){
 if(!record)return false;
 const n=record.name;
 const hpActive=Boolean(settings.planning_heat_pump);

 if(n==='planning_heat_pump')return true;

 if(HP_LEGACY_KEYS.has(n)){
  if(hpActive)return false;
  if(n==='dhw_resistance_efficiency')return settings.dhw_heating_type==='resistance';
  if(n==='dhw_scop')return settings.dhw_heating_type==='heat_pump';
  if(['heating_on_hour','heating_off_hour','heating_evening_on_hour','heating_evening_off_hour'].includes(n)){
   return settings.heating_schedule_mode==='scheduled';
  }
  return true;
 }

 if(n==='gas_disconnect_cost'){
  if(!hpActive)return true;
  return Boolean(settings.hp_disconnect_gas);
 }

 if(HP_ALL_KEYS_SET.has(n)){
  if(!hpActive)return false;
  if(['hp_morning_on','hp_morning_off','hp_evening_on','hp_evening_off'].includes(n)){
   return settings.hp_schedule_mode==='scheduled';
  }
  if(n==='hp_dhw_litres_day')return settings.hp_dhw_demand_mode==='litres';
  if(n==='hp_prior_dhw_loss_fraction')return settings.hp_dhw_demand_mode==='gas_calibrated';
  if(['hp_reheat_on','hp_reheat_off'].includes(n))return settings.hp_reheat_mode==='scheduled';
  if(['hp_hygiene_temp','hp_hygiene_weekday','hp_hygiene_hour','hp_hygiene_duration_hours'].includes(n)){
   return Boolean(settings.hp_hygiene_enabled);
  }
  if(n==='hp_cooking_capex')return Boolean(settings.hp_electric_cooking);
  return true;
 }

 if(n==='loan_fraction')return !hasLoanMode()||settings.loan_mode==='fraction';
 if(n==='loan_amount')return hasLoanMode()&&settings.loan_mode==='amount';
 if(['loan_include_electrification','loan_apr','loan_years','loan_max_monthly_payment'].includes(n)){
  return Number(loanUsesAmount()?settings.loan_amount:settings.loan_fraction)>0;
 }
 if(n==='dhw_resistance_efficiency')return settings.dhw_heating_type==='resistance';
 if(n==='dhw_scop')return settings.dhw_heating_type==='heat_pump';
 if(['battery_secondary_module_kwh','battery_secondary_modules'].includes(n))return Boolean(settings.battery_mixed_sizes);
 if(['heating_on_hour','heating_off_hour','heating_evening_on_hour','heating_evening_off_hour'].includes(n)){
  return settings.heating_schedule_mode==='scheduled';
 }
 return true;
}

function recordAllModeDisclosures(){
 const root=getSidebarRoot();
 if(!root?.querySelectorAll)return;
 const groups=Array.from(root.querySelectorAll?.('details.group')||[]);
 groups.forEach(el=>{
  const key=el.dataset?.category||el.querySelector?.('.category-title')?.textContent?.trim()||el;
  allModeDisclosures.set(key,Boolean(el.open));
 });
}

function restoreAllModeDisclosures(){
 const root=getSidebarRoot();
 if(!root?.querySelectorAll)return;
 const groups=Array.from(root.querySelectorAll?.('details.group')||[]);
 groups.forEach(el=>{
  const key=el.dataset?.category||el.querySelector?.('.category-title')?.textContent?.trim()||el;
  if(allModeDisclosures.has(key)){
   el.open=Boolean(allModeDisclosures.get(key));
  }
 });
}

function setSidebarFilterMode(mode){
 if(!['all','view','modified'].includes(mode))return;
 if(sidebarFilterMode==='all'&&mode!=='all'&&!activeSearch){
  recordAllModeDisclosures();
 }
 sidebarFilterMode=mode;
 updateModeSelectorUi();
 applyControlFilters();
}

function updateModeSelectorUi(){
 const container=$('sidebarFilterMode');
 const root=getSidebarRoot();
 if(!container&&!root?.querySelectorAll)return;
 const buttons=container
  ?Array.from(container.querySelectorAll?.('.sidebar-mode-btn')||[])
  :Array.from(root?.querySelectorAll?.('.sidebar-mode-btn')||[]);
 buttons.forEach(btn=>{
  const isSelected=btn.dataset?.mode===sidebarFilterMode;
  btn.classList?.toggle?.('active',isSelected);
  btn.setAttribute?.('aria-checked',String(isSelected));
  btn.tabIndex=isSelected?0:-1;
  btn.setAttribute?.('tabindex',isSelected?'0':'-1');
 });
}

function initSidebarModeSelector(){
 const container=$('sidebarFilterMode');
 if(!container||container===$('controls'))return;
 const buttons=Array.from(container.querySelectorAll?.('.sidebar-mode-btn')||[]);
 buttons.forEach((btn,idx)=>{
  btn.onclick=()=>{
   setSidebarFilterMode(btn.dataset.mode);
  };
  btn.onkeydown=(e)=>{
   if(['ArrowLeft','ArrowUp'].includes(e.key)){
    e.preventDefault();
    const prevIdx=(idx-1+buttons.length)%buttons.length;
    buttons[prevIdx].focus();
    setSidebarFilterMode(buttons[prevIdx].dataset.mode);
   }else if(['ArrowRight','ArrowDown'].includes(e.key)){
    e.preventDefault();
    const nextIdx=(idx+1)%buttons.length;
    buttons[nextIdx].focus();
    setSidebarFilterMode(buttons[nextIdx].dataset.mode);
   }else if(e.key==='Home'){
    e.preventDefault();
    buttons[0].focus();
    setSidebarFilterMode(buttons[0].dataset.mode);
   }else if(e.key==='End'){
    e.preventDefault();
    buttons[buttons.length-1].focus();
    setSidebarFilterMode(buttons[buttons.length-1].dataset.mode);
   }
  };
 });
 updateModeSelectorUi();
}

function updateCategoryHeaderCounts(groupEl){
 const catControls=Array.from(groupEl.querySelectorAll?.('[data-control]')||[]);
 let availableCount=0,modCount=0;
 catControls.forEach(ctrl=>{
  if(conditionalVisible(ctrl)){
   availableCount++;
   const k=ctrl.dataset?.control;
   if(k&&isRecordModified(k))modCount++;
  }
 });
 const countEl=groupEl.querySelector?.('.category-count');
 if(countEl){
  countEl.textContent=`${availableCount} ${availableCount===1?'input':'inputs'}`;
 }
 const badgeEl=groupEl.querySelector?.('.category-badge');
 if(badgeEl){
  if(modCount>0){
   badgeEl.textContent=`${modCount} modified`;
   badgeEl.className='badge modified category-badge';
   badgeEl.hidden=false;
   if(badgeEl.style)badgeEl.style.display='';
  }else{
   badgeEl.textContent='';
   badgeEl.className='category-badge';
   badgeEl.hidden=true;
   if(badgeEl.style)badgeEl.style.display='none';
  }
 }
}

function updateFilterStatusAndHints(totalVisible){
 const statusEl=$('sidebarFilterStatus');
 const hintEl=$('sidebarSearchHint');
 const emptyEl=$('sidebarEmptyState');

 if(hintEl&&hintEl!==$('controls')){
  hintEl.hidden=!activeSearch;
 }

 let statusText='',emptyText='';
 if(activeSearch){
  if(totalVisible>0){
   statusText=`${totalVisible} ${totalVisible===1?'input':'inputs'} found`;
  }else{
   statusText=`No inputs match “${activeSearch}”`;
   emptyText=`No inputs match “${activeSearch}”. Clear search to show all inputs.`;
  }
 }else if(sidebarFilterMode==='modified'){
  if(totalVisible>0){
   statusText=`${totalVisible} modified ${totalVisible===1?'input':'inputs'}`;
  }else{
   statusText='No modified inputs. All values match shared defaults.';
   emptyText='No modified inputs. All values match shared defaults.';
  }
 }else if(sidebarFilterMode==='view'){
  const viewLabel=TAB_VIEW_NAMES[getCurrentViewName()]||title(getCurrentViewName());
  if(totalVisible>0){
   statusText=`${totalVisible} ${totalVisible===1?'input':'inputs'} for ${viewLabel}`;
  }else{
   statusText='No inputs for this view';
   emptyText='No inputs relevant to the current view.';
  }
 }else{
  if(totalVisible>0){
   statusText=`${totalVisible} ${totalVisible===1?'input':'inputs'}`;
  }else{
   statusText='No inputs available';
   emptyText='No inputs available.';
  }
 }

 if(statusEl&&statusEl!==$('controls')){
  statusEl.textContent=statusText;
 }

 if(emptyEl&&emptyEl!==$('controls')){
  if(totalVisible===0){
   emptyEl.hidden=false;
   emptyEl.textContent=emptyText;
  }else{
   emptyEl.hidden=true;
   emptyEl.textContent='';
  }
 }
}

function applyControlFilters(){
 const root=getSidebarRoot();
 if(!root)return;
 const currentView=getCurrentViewName();
 const isSearchActive=Boolean(activeSearch);
 const controlEls=Array.from(root.querySelectorAll?.('[data-control]')||[]);
 let totalVisible=0;

 controlEls.forEach(el=>{
  const isCondActive=conditionalVisible(el);
  if(!isCondActive){
   el.hidden=true;
   return;
  }

  const key=el.dataset?.control;
  const record=schema?.records?.find(r=>r.name===(key==='battery_group'?'battery_modules':key));
  let isMatch=false;

  if(isSearchActive){
   const cat=record?(CATEGORY_MAP[record.group]||record.group||''):'';
   const label=record?.label||FRIENDLY_NAMES[key]||title(key)||'';
   const desc=record?.description||'';
   const src=record?.source||'';
   const text=el.textContent||'';
   const blob=`${key||''} ${key==='battery_group'?'battery modules storage different sizes':''} ${label} ${desc} ${src} ${cat} ${text}`.toLowerCase();
   isMatch=blob.includes(activeSearch);
  }else if(sidebarFilterMode==='all'){
   isMatch=true;
  }else if(sidebarFilterMode==='view'){
   isMatch=record?isRecordInCurrentView(record,currentView):true;
  }else if(sidebarFilterMode==='modified'){
   isMatch=isRecordModified(key);
  }

  el.hidden=!isMatch;
  if(isMatch)totalVisible++;
 });

 const secHeaders=Array.from(root.querySelectorAll?.('.control-section-header[data-section]')||[]);
 secHeaders.forEach(sh=>{
  const sec=sh.dataset?.section;
  if(sec==='gas_tax'){
   const anyVis=Array.from(root.querySelectorAll?.('[data-control^="gas_excise_"],[data-control^="gas_regional_"],[data-control^="gas_vat_"]')||[]).some(c=>!c.hidden);
   sh.hidden=!anyVis;
  }else if(sec==='gas_pretax'){
   const anyVis=Array.from(root.querySelectorAll?.('[data-control="gas_commodity"],[data-control="gas_regulated"],[data-control="gas_fixed_day"],[data-control="gas_pcs"]')||[]).some(c=>!c.hidden);
   sh.hidden=!anyVis;
  }else if(sec==='elec'){
   const anyVis=Array.from(root.querySelectorAll?.('[data-control^="retail_"],[data-control^="electric_"],[data-control^="power_"]')||[]).some(c=>!c.hidden);
   sh.hidden=!anyVis;
  }else if(sec==='solar_tax'){
   const anyVis=Array.from(root.querySelectorAll?.('[data-control="export_irpef"],[data-control="regional_additional_tax"],[data-control="municipal_additional_tax"]')||[]).some(c=>!c.hidden);
   sh.hidden=!anyVis;
  }
 });

 const pinnedSection=$('pinnedAssumptions');
 const pinnedList=$('pinnedControlsList');
 if(pinnedSection&&pinnedList&&pinnedSection!==$('controls')){
  const visiblePinned=Array.from(pinnedList.querySelectorAll?.('[data-control]')||[]).filter(c=>!c.hidden);
  pinnedSection.hidden=visiblePinned.length===0;
 }

 const groups=Array.from(root.querySelectorAll?.('details.group')||[]);
 groups.forEach(groupEl=>{
  const catControls=Array.from(groupEl.querySelectorAll?.('[data-control]')||[]);
  const visibleControls=catControls.filter(c=>!c.hidden);
  const hasVisible=visibleControls.length>0;
  const catKey=groupEl.dataset?.category||groupEl.querySelector?.('.category-title')?.textContent?.trim()||groupEl;

  if(isSearchActive){
   groupEl.hidden=!hasVisible;
   groupEl.open=true;
  }else if(sidebarFilterMode!=='all'){
   groupEl.hidden=!hasVisible;
   if(hasVisible)groupEl.open=true;
  }else{
   const hasCondActive=catControls.some(c=>conditionalVisible(c));
   groupEl.hidden=!hasCondActive;
   if(allModeDisclosures.has(catKey)){
    groupEl.open=Boolean(allModeDisclosures.get(catKey));
   }
  }

  updateCategoryHeaderCounts(groupEl);
 });

 const hpWrap=root.querySelector?.('.hp-controls-wrap');
 if(hpWrap){
  hpWrap.hidden=!Boolean(settings.planning_heat_pump);
 }
 const folds=Array.from(root.querySelectorAll?.('details.hp-advanced-fold')||[]);
 folds.forEach(foldEl=>{
  const foldControls=Array.from(foldEl.querySelectorAll?.('[data-control]')||[]);
  const hasVis=foldControls.some(c=>!c.hidden);
  foldEl.hidden=!hasVis||!Boolean(settings.planning_heat_pump);
  if(isSearchActive&&hasVis)foldEl.open=true;
 });

 updateFilterStatusAndHints(totalVisible);
}

function refreshConditionalControls(){applyControlFilters();if(typeof syncBatterySizingControls==='function')syncBatterySizingControls();}

function updateSearch(value){
 const q=String(value??'').trim().toLowerCase(),root=getSidebarRoot(),groups=Array.from(root?.querySelectorAll?.('details.group')||[]);
 if(q&&!searchDisclosure){
  searchDisclosure=new Map();
  groups.forEach(el=>{
   const key=el.dataset?.category||el.querySelector?.('.category-title')?.textContent?.trim()||el;
   searchDisclosure.set(key,Boolean(el.open));
  });
 }
 if(!q&&searchDisclosure){
  groups.forEach(el=>{
   const key=el.dataset?.category||el.querySelector?.('.category-title')?.textContent?.trim()||el;
   if(searchDisclosure.has(key))el.open=searchDisclosure.get(key);
  });
  searchDisclosure=null;
 }
 activeSearch=q;
 applyControlFilters();
}

function getConditionalUnavailableExplanation(name,record){
 const label=record?.label||FRIENDLY_NAMES[name]||title(name);
 const hpActive=Boolean(settings.planning_heat_pump);
 if(HP_LEGACY_KEYS.has(name)&&hpActive){
  return `${label} is unavailable because heat pump planning is active (planning_heat_pump is enabled).`;
 }
 if(HP_ALL_KEYS_SET.has(name)&&!hpActive){
  return `${label} is unavailable because heat pump planning is off. Enable "Planning a heat pump" to configure.`;
 }
 if(name==='loan_fraction')return `${label} is unavailable because loan mode is set to absolute amount.`;
 if(name==='loan_amount')return `${label} is unavailable because loan mode is set to share of capex.`;
 if(['loan_include_electrification','loan_apr','loan_years','loan_max_monthly_payment'].includes(name)){
  return `${label} is unavailable because loan financing is inactive (loan share or amount is 0).`;
 }
 if(name==='dhw_resistance_efficiency')return `${label} is unavailable because DHW heating type is set to heat pump.`;
 if(name==='dhw_scop')return `${label} is unavailable because DHW heating type is set to resistance boiler.`;
 if(['heating_on_hour','heating_off_hour','heating_evening_on_hour','heating_evening_off_hour'].includes(name)){
  return `${label} is unavailable because heating schedule mode is set to continuous (24h).`;
 }
 if(['hp_morning_on','hp_morning_off','hp_evening_on','hp_evening_off'].includes(name)){
  return `${label} is unavailable because heat pump schedule mode is set to continuous.`;
 }
 if(name==='hp_dhw_litres_day'){
  return `${label} is unavailable because DHW demand mode is set to gas-calibrated monthly basis.`;
 }
 if(name==='hp_prior_dhw_loss_fraction'){
  return `${label} is unavailable because DHW demand mode is set to daily draw volume basis.`;
 }
 if(['hp_reheat_on','hp_reheat_off'].includes(name)){
  return `${label} is unavailable because cylinder reheat schedule is set to continuous (24h).`;
 }
 if(['hp_hygiene_temp','hp_hygiene_weekday','hp_hygiene_hour','hp_hygiene_duration_hours'].includes(name)){
  return `${label} is unavailable because weekly thermal disinfection cycle is disabled.`;
 }
 if(name==='hp_cooking_capex'){
  return `${label} is unavailable because cooking is not converted to electric/induction.`;
 }
 if(name==='gas_disconnect_cost'){
  return `${label} is unavailable because gas service disconnection is not enabled.`;
 }
 return `${label} is conditionally unavailable in current settings.`;
}

function revealAndFocusSetting(settingName){
 if(!settingName)return;
 const record=schema?.records?.find(r=>r.name===settingName)||{name:settingName};
 const isCondAvailable=isRecordConditionallyAvailable(record);

 const searchEl=$('search');
 if(searchEl)searchEl.value='';
 if(typeof updateSearch==='function')updateSearch('');

 if(typeof setSidebarFilterMode==='function')setSidebarFilterMode('all');

 const catName=record?(CATEGORY_MAP[record.group]||(record.group?title(record.group):'Other')):null;
 if(catName&&typeof allModeDisclosures!=='undefined'){
  allModeDisclosures.set(catName,true);
 }
 const catEl=(catName?document.querySelector?.(`details.category-accordion[data-category="${catName}"]`):null)||
             document.querySelector?.(`[data-control="${settingName}"]`)?.closest?.('details.group, details.category-accordion');
 if(catEl)catEl.open=true;

 const aside=document.querySelector?.('aside');
 if(aside&&!aside.classList?.contains?.('inputs-open')){
  aside.classList?.add?.('inputs-open');
  const toggleBtn=$('toggleInputs');
  if(toggleBtn){
   toggleBtn.setAttribute?.('aria-expanded','true');
   toggleBtn.textContent='Hide inputs';
  }
 }

 if(!isCondAvailable){
  const explanation=getConditionalUnavailableExplanation(settingName,record);
  const statusEl=$('status');
  if(statusEl)statusEl.textContent=explanation;
  const filterStatusEl=$('sidebarFilterStatus');
  if(filterStatusEl)filterStatusEl.textContent=explanation;
  return;
 }

 const controlEl=document.querySelector?.(`[data-control="${settingName}"]`);
 if(controlEl){
  let parent=controlEl.parentElement;
  while(parent&&parent!==document.body){
   if(parent.tagName==='DETAILS')parent.open=true;
   parent=parent.parentElement;
  }
  if(typeof controlEl.scrollIntoView==='function'){
   controlEl.scrollIntoView({behavior:'smooth',block:'center'});
  }
  const editor=controlEl.querySelector?.('.row input, .row select, input:not([type=range]), select, textarea');
  if(editor&&typeof editor.focus==='function')editor.focus();
  controlEl.classList?.add?.('highlight-pulse');
  setTimeout(()=>controlEl.classList?.remove?.('highlight-pulse'),2000);
 }
}
function dayhourDateForYear(year){const input=$('weatherDate'),today=new Date(),month=String(today.getMonth()+1).padStart(2,'0'),day=String(today.getDate()).padStart(2,'0'),current=`${today.getFullYear()}-${month}-${day}`,parts=(input?.value||current).split('-'),m=Number(parts[1])||(today.getMonth()+1),d=Number(parts[2])||today.getDate(),maxD=maxDaysInMonth(year,m),clampedD=Math.min(Math.max(1,d),maxD);return `${year}-${String(m).padStart(2,'0')}-${String(clampedD).padStart(2,'0')}`;}
function maxDaysInMonth(year,month){if(month===2){const isLeap=(year%4===0&&year%100!==0)||(year%400===0);return isLeap?29:28;}return [4,6,9,11].includes(month)?30:31;}
function getForecastYear(){return Number((typeof settings!=='undefined'?settings?.installation_year:null)??(typeof current!=='undefined'?current?.result?.settings?.installation_year:null)??2026)+1;}
function updateForecastDateBounds(){
 const fy=getForecastYear(),opt=$('dayhourModeForecastOption'),eyebrow=$('dayhourEyebrow');
 if(opt)opt.textContent=`Forecast first year · ${fy}`;
 if(eyebrow)eyebrow.textContent=`FIRST OPERATING YEAR · ${fy} WEATHER SCENARIOS`;
 if($('dayhourMode')?.value==='forecast_2027'){
  const dateInput=$('weatherDate');
  if(dateInput){
   dateInput.min=`${fy}-01-01`;
   dateInput.max=`${fy}-12-31`;
   dateInput.value=dayhourDateForYear(fy);
  }
 }
}
function syncSetting(key,source=null){
 const record=schema?.records?.find(r=>r.name===key),value=settings[key];
 document.querySelectorAll?.('[data-setting],[data-slider]')?.forEach?.(el=>{
  if((el.dataset.setting||el.dataset.slider)!==key)return;
  if(el!==source){
   if(el.type==='checkbox')el.checked=Boolean(value);
   else if(isPercentControl(record)&&el.dataset.setting===key&&el.type!=='select-one'){
    el.removeAttribute?.('aria-invalid');
    el.classList?.remove?.('is-invalid');
    el.value=formatPercentValue(value);
   }else{
    el.value=value??'';
   }
  }
  if(key==='ac_heating_additional_demand_pct'&&el.type==='number'){
   el.min='0';
   el.step='any';
   el.removeAttribute?.('max');
   if(source!=='ac_demand_pct_input'&&!hasInvalidAcDemandPctDraft()){
    el.removeAttribute?.('aria-invalid');
    el.removeAttribute?.('aria-describedby');
    el.classList?.remove?.('is-invalid');
    const errEl=document.getElementById('ac_heating_additional_demand_pct_error');
    if(errEl){errEl.textContent='';errEl.hidden=true;}
   }
  }else if(el.type==='number'&&record){
   el.setAttribute?.('value',value??'');
   const min=record.bounds?.[0],step=numericStep(record);
   if(min!==undefined){
    if(value==null||Math.abs((value-min)/step-Math.round((value-min)/step))<1e-7)el.min=min;
    else el.removeAttribute?.('min');
   }
  }
 });

 if(key==='loan_include_electrification'){
  const scopeEl=$('loanScopeControl');
  if(scopeEl&&scopeEl!==source)scopeEl.value=value?'solar_and_conversion':'solar';
 }
 if(key==='clipping_dc_max'){
  const clipEl=$('solarClippingDcMax');
  if(clipEl&&clipEl!==source){
   clipEl.value=(value!=null&&Number.isFinite(Number(value)))?String(Number((Number(value)*100).toFixed(4))):'4.0';
   clipEl.removeAttribute?.('aria-invalid');
   clipEl.classList?.remove?.('is-invalid');
  }
 }
 if(key==='ac_heating_windows'&&source!=='ac_editor'){
  if(!hasInvalidAcScheduleDraft()){
   acScheduleDraft=null;
  }else{
   reconcileAcScheduleDraftWithRemote(settings.ac_heating_windows);
   if(typeof updateAcScheduleTimeline==='function')updateAcScheduleTimeline();
  }
 }
 if(key==='ac_heating_additional_demand_pct'&&source!=='ac_demand_pct_input'){
  if(!hasInvalidAcDemandPctDraft()){
   acDemandPctDraft=null;
  }
 }
 if(key==='load_scenario'||key==='loan_mode'||key==='loan_amount'||key==='loan_include_electrification'||key==='electrification_capex_include_baseline'){
  const ctxText=getLoanScopeContextText(settings,current?.result);
  const switchCtx=document.querySelector?.('[data-switch-context="loan_include_electrification"]');
  if(switchCtx)switchCtx.textContent=ctxText;
  const scopeCtx=$('loanScopeContext');
  if(scopeCtx)scopeCtx.textContent=ctxText;
 }

 if(BATTERY_SETTINGS.includes(key)){
  const isMixed=Boolean(settings.battery_mixed_sizes);
  const singleWrap=$('batterySingleControls');
  const mixedWrap=$('batteryMixedControls');
  if(singleWrap)singleWrap.hidden=isMixed;
  if(mixedWrap)mixedWrap.hidden=!isMixed;

  const firstQtyInput=document.querySelector?.('input[data-battery-row="first"]');
  if(firstQtyInput&&firstQtyInput!==source){
   const firstMods=Math.max(0,(Number(settings.battery_modules)||0)-(Number(settings.battery_secondary_modules)||0));
   firstQtyInput.value=String(firstMods);
   firstQtyInput.setAttribute?.('value',String(firstMods));
  }

  const secQtyInput=document.querySelector?.('input[data-battery-row="secondary"]');
  if(secQtyInput&&secQtyInput!==source){
   secQtyInput.value=String(settings.battery_secondary_modules??0);
   secQtyInput.setAttribute?.('value',String(settings.battery_secondary_modules??0));
  }

  const summaryStrip=$('batterySummaryStrip');
  if(summaryStrip){
   summaryStrip.textContent=batterySummaryText();
  }

  document.querySelectorAll?.('[data-setting="battery_kwh"]').forEach?.(el=>{
   if(el!==source){
    el.value=settings.battery_kwh!=null?String(settings.battery_kwh):'';
    el.setAttribute?.('value',el.value);
   }
   el.readOnly=isMixed;
   el.classList?.toggle?.('readonly-input',isMixed);
   if(isMixed){
    el.setAttribute?.('title','Derived from battery module combination');
   }else{
    el.removeAttribute?.('title');
   }
  });

  document.querySelectorAll?.('[data-slider="battery_kwh"]').forEach?.(el=>{
   if(el!==source){
    el.value=settings.battery_kwh!=null?String(settings.battery_kwh):'';
   }
   el.disabled=isMixed;
   el.hidden=isMixed;
  });

  const isBatMod=BATTERY_SETTINGS.some(k=>schema?.defaults?.[k]!==undefined&&settings[k]!==undefined&&!settingValuesEqual(schema.defaults[k],settings[k]));
  document.querySelectorAll?.('.battery-group-control').forEach?.(el=>{
   el.classList?.toggle?.('is-modified',isBatMod);
   const resetBtn=el.querySelector?.('[data-reset-setting]');
   if(resetBtn)resetBtn.hidden=!isBatMod;
  });

  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
 }

 if(typeof updateControlModifiedState==='function')updateControlModifiedState(key);
 if(panelKeys.includes(key)||['panel_watts','battery_kwh','battery_modules','battery_module_kwh'].includes(key)){
  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
 }
 if(key==='installation_year'&&typeof updateForecastDateBounds==='function')updateForecastDateBounds();
 if(key==='load_scenario'){
  const scen=value||'electrified';
  const solScen=$('solarScenario');if(solScen&&solScen.value!==scen)solScen.value=scen;
  const batScen=$('batScenario');if(batScen&&batScen.value!==scen)batScen.value=scen;
  const dhScen=$('dayhourScenario');if(dhScen&&dhScen.value!==scen)dhScen.value=scen;
  document.querySelectorAll?.('[data-behavior-scen]')?.forEach?.(b=>{
   const isActive=b.dataset?.behaviorScen===scen;
   b.classList?.toggle?.('active',isActive);
   b.setAttribute?.('aria-pressed',isActive?'true':'false');
  });
 }
}
const panelKeys=['south_panels','west_panels','east_panels','dormer_panels','flat_panels','secondary_south_panels'];
const BATTERY_SETTINGS=['battery_kwh','battery_modules','battery_module_kwh','battery_mixed_sizes','battery_secondary_module_kwh','battery_secondary_modules'];
const BATTERY_CONTROL_KEYS=new Set(['battery_modules','battery_module_kwh','battery_mixed_sizes','battery_secondary_module_kwh','battery_secondary_modules']);

function renderBatteryGroupControl(){
 const isMixed=Boolean(settings.battery_mixed_sizes);
 const totalMods=Number(settings.battery_modules)||0;
 const secMods=Number(settings.battery_secondary_modules)||0;
 const firstMods=Math.max(0,totalMods-secMods);
 const firstCap=settings.battery_module_kwh!=null?Number(settings.battery_module_kwh):5;
 const secCap=settings.battery_secondary_module_kwh!=null?Number(settings.battery_secondary_module_kwh):6.9;

 const isBatMod=BATTERY_SETTINGS.some(k=>schema?.defaults?.[k]!==undefined&&settings[k]!==undefined&&!settingValuesEqual(schema.defaults[k],settings[k]));
 const recModules=schema?.records?.find(r=>r.name==='battery_modules');
 const recModuleKwh=schema?.records?.find(r=>r.name==='battery_module_kwh');
 const recSecKwh=schema?.records?.find(r=>r.name==='battery_secondary_module_kwh');
 const recSecMods=schema?.records?.find(r=>r.name==='battery_secondary_modules');
 const primaryUnit=recModuleKwh?.unit==='usable kWh/module'?'kWh':(recModuleKwh?.unit||'kWh');
 const secUnit=recSecKwh?.unit==='usable kWh/module'?'kWh':(recSecKwh?.unit||'kWh');

 return `<div class="battery-group-control${isBatMod?' is-modified':''}" data-control="battery_group">`+
  `<div class="battery-group-header">`+
   `<label style="font-weight:600;font-size:13px;color:var(--ink)">Battery storage</label>`+
   `<button type="button" class="control-reset" data-reset-setting="battery_group" aria-label="Reset Battery storage" title="Reset battery configuration to its saved default"${isBatMod?'':' hidden'}>Reset</button>`+
  `</div>`+
  `<div class="battery-switch-wrap" style="margin-bottom:12px">`+
   `<label class="switch"><input type="checkbox" role="switch" data-setting="battery_mixed_sizes" aria-label="Modules of different sizes?" ${isMixed?'checked':''}><span class="switch-track" aria-hidden="true"></span><span>Modules of different sizes?</span></label>`+
  `</div>`+
  `<div id="batterySingleControls"${isMixed?' hidden':''}>`+
   (recModules?control(recModules):'')+
   (recModuleKwh?control(recModuleKwh):'')+
  `</div>`+
  `<div id="batteryMixedControls"${!isMixed?' hidden':''}>`+
   `<div class="battery-mixed-rows">`+
    `<div class="battery-mixed-row">`+
     `<div class="battery-row-header"><span>Primary module size</span></div>`+
     `<div class="battery-row-grid">`+
      `<div class="control" data-control="battery_module_kwh" style="margin-bottom:0">`+
       `<label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Usable kWh per module</label>`+
       `<div class="row"><input type="number" data-setting="battery_module_kwh" value="${firstCap}" ${numericBounds(recModuleKwh,firstCap)} step="${numericStep(recModuleKwh)}" aria-label="Primary usable kWh per module" placeholder="5"><span class="unit">${esc(primaryUnit)}</span></div>`+
      `</div>`+
      `<div class="control" data-control="battery_modules" style="margin-bottom:0">`+
       `<label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Quantity</label>`+
       `<div class="row"><input type="number" data-battery-row="first" value="${firstMods}" min="0" step="1" aria-label="Primary module quantity" placeholder="0"><span class="unit">modules</span></div>`+
      `</div>`+
     `</div>`+
    `</div>`+
    `<div class="battery-mixed-row">`+
     `<div class="battery-row-header"><span>Secondary module size</span></div>`+
     `<div class="battery-row-grid">`+
      `<div class="control" data-control="battery_secondary_module_kwh" style="margin-bottom:0">`+
       `<label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Usable kWh per module</label>`+
       `<div class="row"><input type="number" data-setting="battery_secondary_module_kwh" value="${secCap}" ${numericBounds(recSecKwh,secCap)} step="${numericStep(recSecKwh)}" aria-label="Secondary usable kWh per module" placeholder="6.9"><span class="unit">${esc(secUnit)}</span></div>`+
      `</div>`+
      `<div class="control" data-control="battery_secondary_modules" style="margin-bottom:0">`+
       `<label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Quantity</label>`+
       `<div class="row"><input type="number" data-battery-row="secondary" data-setting="battery_secondary_modules" value="${secMods}" min="0" step="1" aria-label="Secondary module quantity" placeholder="0"><span class="unit">modules</span></div>`+
      `</div>`+
     `</div>`+
    `</div>`+
   `</div>`+
   `<div id="batterySummaryStrip" class="battery-summary-strip">${batterySummaryText()}</div>`+
  `</div>`+
  `<details class="control-details" style="margin-top:10px"><summary>Details</summary><div class="control-details-body">`+
   `<div class="control-details-meta"><span class="tag ${esc(recModules?.status||'default')}">${esc(recModules?.status||'default')}</span><small class="control-source" title="${esc(recModules?.source||'')}">Source: ${esc(recModules?.source||'')}</small></div>`+
   `<small class="control-desc">${esc(recModules?.description||'Battery storage sizing and modules.')}</small>`+
   `<div class="control-details-actions"><button type="button" data-save-default="battery_modules" aria-label="Save battery settings as default">Save as shared default</button></div>`+
  `</div></details>`+
 `</div>`;
}
function formatBatteryComposition(item, options = {}) {
 if (!item) return '0 kWh';
 const s = item.settings || item;
 const totalKwh = Number(item.battery_kwh ?? s.battery_kwh ?? 0);
 const totalMods = Number(item.battery_modules ?? s.battery_modules ?? 0);
 const isMixed = Boolean(item.battery_mixed_sizes ?? s.battery_mixed_sizes);
 const secMods = isMixed ? Number(item.battery_secondary_modules ?? s.battery_secondary_modules ?? 0) : 0;
 const firstMods = Number(item.first_modules ?? Math.max(0, totalMods - secMods));
 const firstKwh = Number(item.battery_module_kwh ?? s.battery_module_kwh ?? (typeof settings !== 'undefined' ? settings.battery_module_kwh : 0));
 const secKwh = Number(item.battery_secondary_module_kwh ?? s.battery_secondary_module_kwh ?? (typeof settings !== 'undefined' ? settings.battery_secondary_module_kwh : 0));

 const fmtN = n => Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));

 if (totalMods === 0 || totalKwh === 0) {
  return options.short ? '0 kWh (no storage)' : '0 kWh (no battery storage)';
 }

 if (isMixed) {
  if (firstMods > 0 && secMods > 0) {
   if (options.short) {
    return `${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} kWh (${firstMods}×${fmtN(firstKwh)} + ${secMods}×${fmtN(secKwh)})`;
   }
   return `${firstMods} × ${fmtN(firstKwh)} kWh + ${secMods} × ${fmtN(secKwh)} kWh = ${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} usable kWh (${totalMods} modules)`;
  } else if (secMods > 0) {
   if (options.short) {
    return `${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} kWh (${secMods}×${fmtN(secKwh)})`;
   }
   return `${secMods} × ${fmtN(secKwh)} kWh = ${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} usable kWh (${secMods} ${secMods === 1 ? 'module' : 'modules'})`;
  } else {
   if (options.short) {
    return `${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} kWh (${firstMods}×${fmtN(firstKwh)})`;
   }
   return `${firstMods} × ${fmtN(firstKwh)} kWh = ${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} usable kWh (${firstMods} ${firstMods === 1 ? 'module' : 'modules'})`;
  }
 }

 if (options.short) {
  return `${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} kWh (${totalMods}×${fmtN(firstKwh)})`;
 }
 return `${totalMods} × ${fmtN(firstKwh)} kWh = ${typeof nf === 'function' ? nf(totalKwh, 1) : totalKwh.toFixed(1)} usable kWh (${totalMods} ${totalMods === 1 ? 'module' : 'modules'})`;
}
function batterySummaryText(){
 const isMixed=Boolean(settings.battery_mixed_sizes);
 const totalMods=Number(settings.battery_modules)||0;
 const secMods=Number(settings.battery_secondary_modules)||0;
 const firstMods=Math.max(0,totalMods-secMods);
 const firstKwh=Number(settings.battery_module_kwh)||0;
 const secKwh=Number(settings.battery_secondary_module_kwh)||0;
 const totalKwh=Number(settings.battery_kwh)||0;
 const fmtN=n=>Number.isInteger(n)?String(n):String(parseFloat(n.toFixed(4)));
 if(!isMixed){
  return `${totalMods} × ${fmtN(firstKwh)} = ${fmtN(totalKwh)} kWh · ${totalMods} ${totalMods===1?'module':'modules'}`;
 }
 return `${firstMods} × ${fmtN(firstKwh)} + ${secMods} × ${fmtN(secKwh)} = ${fmtN(totalKwh)} kWh · ${totalMods} ${totalMods===1?'module':'modules'}`;
}
function editMixedBatteryRowQuantity(row,value,source=null){
 solarAppliedMessage='';
 const num=Number(value);
 if(value===''||!Number.isFinite(num)||!Number.isInteger(num)||num<0){
  source?.setAttribute?.('aria-invalid','true');
  source?.classList?.add?.('is-invalid');
  return;
 }
 const firstCap=Number(settings.battery_module_kwh)||0;
 const secCap=Number(settings.battery_secondary_module_kwh)||0;
 if(row==='first'&&num>0&&firstCap<=0){
  source?.setAttribute?.('aria-invalid','true');
  source?.classList?.add?.('is-invalid');
  return;
 }
 if(row==='secondary'&&num>0&&secCap<=0){
  source?.setAttribute?.('aria-invalid','true');
  source?.classList?.add?.('is-invalid');
  return;
 }
 source?.removeAttribute?.('aria-invalid');
 source?.classList?.remove?.('is-invalid');
 if(row==='first'){
  const first=num;
  const sec=Number(settings.battery_secondary_modules)||0;
  settings.battery_modules=first+sec;
  settings.battery_secondary_modules=sec;
 }else if(row==='secondary'){
  const sec=num;
  const currentTotal=Number(settings.battery_modules)||0;
  const currentSec=Number(settings.battery_secondary_modules)||0;
  const first=Math.max(0,currentTotal-currentSec);
  settings.battery_modules=first+sec;
  settings.battery_secondary_modules=sec;
 }
 normalizeBatterySettings(settings,'battery_modules');
 for(const linked of BATTERY_SETTINGS){
  syncSetting(linked,source?.dataset?.setting===linked?source:null);
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(linked);
 }
 updateControlModifiedState('battery_group');
 syncOptimizerEquipmentInputs();
 clearEditedQuoteOverrides('battery_modules');
 refreshConditionalControls();
 schedule();
 if(typeof updateModifiedBadge==='function')updateModifiedBadge();
 if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
}
function normalizeBatterySettings(target,changed=null){
 const mixed=Boolean(target.battery_mixed_sizes);
 if(mixed){
  let count=Number(target.battery_modules);
  let secCount=Number(target.battery_secondary_modules);
  const firstCap=Number(target.battery_module_kwh);
  const secCap=Number(target.battery_secondary_module_kwh);
  if(!Number.isFinite(count)||count<0)count=0;
  if(!Number.isFinite(secCount)||secCount<0)secCount=0;
  if(secCount>count){
   secCount=count;
   target.battery_secondary_modules=secCount;
  }
  if(count===0){
   target.battery_kwh=0.0;
  }else{
   const firstCount=count-secCount;
   target.battery_kwh=firstCount*firstCap+secCount*secCap;
  }
 }else{
  if(['battery_modules','battery_module_kwh','battery_kwh'].some(key=>target[key]==null||!Number.isFinite(Number(target[key]))))return target;
  const count=Number(target.battery_modules),capacity=Number(target.battery_module_kwh),total=Number(target.battery_kwh);
  if(!Number.isInteger(count)||count<0||capacity<0||total<0)return target;
  const totalWins=changed==='battery_kwh'||(changed==null&&Object.hasOwn(target,'battery_kwh'));
  if(totalWins){
   if(Number.isFinite(total)&&total>=0){
    if(count===0&&total>0){target.battery_modules=1;target.battery_module_kwh=total;}
    else if(count>0){
     if(Math.abs(total-count*capacity)>1e-9)target.battery_module_kwh=total/count;
    }
   }
  }else if(changed==='battery_modules'||changed==='battery_module_kwh'||changed==='battery_mixed_sizes'){
   if(Number.isFinite(count)&&count>=0&&Number.isFinite(capacity)&&capacity>=0){const value=count*capacity;if(Number.isFinite(value))target.battery_kwh=value;}
  }else if(Number.isFinite(count)&&count>=0&&Number.isFinite(capacity)&&capacity>=0){const value=count*capacity;if(Number.isFinite(value))target.battery_kwh=value;}
 }
 return target;
}
function clearEditedQuoteOverrides(key){
 if(BATTERY_SETTINGS.includes(key)){delete quote.battery_nameplate_kwh;delete quote.battery_dod;}
 if(key==='charge_kw')delete quote.continuous_charge_kw;
 if(key==='discharge_kw')delete quote.continuous_discharge_kw;
 if(typeof syncQuoteFields==='function')syncQuoteFields();
}
function syncOptimizerEquipmentInputs(changedKey=null){const mapping={panel_watts:'optPanelWatts',battery_module_kwh:'optModuleKwh',battery_secondary_module_kwh:'optSecondaryModuleKwh',inverter_kw:'optInverter',charge_kw:'optCharge',discharge_kw:'optDischarge'};for(const [key,id]of Object.entries(mapping)){if(changedKey&&key!==changedKey)continue;const field=$(id);if(field&&document.activeElement!==field)field.value=settings[key]??'';}}
function editSetting(key,value,source=null){
 solarAppliedMessage='';
 if(settings.battery_mixed_sizes&&key==='battery_kwh')return;
 if(key==='ac_heating_additional_demand_pct'){
  const validation=validateAcDemandPct(value);
  if(!validation.valid){
   handleAcHeatingDemandPctInput(value,source);
   return;
  }
  value=validation.value;
  acDemandPctDraft=null;
  acDemandPctError=null;
 }
 if(BATTERY_SETTINGS.includes(key)){
  let valid=true;
  if(key==='battery_mixed_sizes'){
   valid=typeof value==='boolean';
  }else if(['battery_modules','battery_secondary_modules'].includes(key)){
   valid=typeof value==='number'&&Number.isFinite(value)&&value>=0&&Number.isInteger(value);
   if(valid&&value>0){
    if(key==='battery_secondary_modules'&&(Number(settings.battery_secondary_module_kwh)||0)<=0){
     valid=false;
    }
   }
  }else{
   valid=typeof value==='number'&&Number.isFinite(value)&&value>=0;
   if(valid){
    const isMixed=Boolean(settings.battery_mixed_sizes);
    const totMods=Number(settings.battery_modules)||0;
    const secMods=Number(settings.battery_secondary_modules)||0;
    const firstMods=isMixed?Math.max(0,totMods-secMods):totMods;
    if(key==='battery_module_kwh'&&firstMods>0&&value<=0){
     valid=false;
    }
    if(key==='battery_secondary_module_kwh'&&isMixed&&secMods>0&&value<=0){
     valid=false;
    }
   }
  }
  if(!valid){
   source?.setAttribute?.('aria-invalid','true');
   source?.classList?.add?.('is-invalid');
   return;
  }
  source?.removeAttribute?.('aria-invalid');
  source?.classList?.remove?.('is-invalid');
  if(typeof document!=='undefined'&&document.querySelectorAll){
   document.querySelectorAll(`[data-setting="${key}"]`).forEach(el=>{
    el?.removeAttribute?.('aria-invalid');
    el?.classList?.remove?.('is-invalid');
   });
  }
 }
 if(key==='battery_mixed_sizes'&&value===true){
  if(Number(settings.battery_secondary_modules)>Number(settings.battery_modules)){
   settings.battery_secondary_modules=Number(settings.battery_modules)||0;
  }
 }
 if(key==='planning_heat_pump'){
  const isPlan=Boolean(value);
  if(isPlan){
   if(settings.load_scenario==='baseline'){
    priorHouseholdScenario='baseline';
    editSetting('load_scenario','electrified');
   }
  }else{
   if(priorHouseholdScenario){
    editSetting('load_scenario',priorHouseholdScenario);
    priorHouseholdScenario=null;
   }
  }
 }
 if(key==='hp_disconnect_gas'){
  const cookingSmc=Number(settings.cooking_smc_annual??25.0);
  if(value===true&&cookingSmc>0&&!settings.hp_electric_cooking){
   const errEl=$('hpDisconnectError');
   if(errEl){
    errEl.textContent='Gas service disconnection requires converting cooking to electric/induction when cooking gas demand is positive.';
    errEl.hidden=false;
   }
   if(source&&typeof source.checked==='boolean')source.checked=false;
   return;
  }
  const errEl=$('hpDisconnectError');
  if(errEl){errEl.textContent='';errEl.hidden=true;}
 }
 if(key==='hp_electric_cooking'){
  const cookingSmc=Number(settings.cooking_smc_annual??25.0);
  if(value===false&&settings.hp_disconnect_gas&&cookingSmc>0){
   const errEl=$('hpDisconnectError');
   if(errEl){
    errEl.textContent='Cannot retain gas cooking while gas service is disconnected. Re-enable gas service first.';
    errEl.hidden=false;
   }
   if(source&&typeof source.checked==='boolean')source.checked=true;
   return;
  }
  const errEl=$('hpDisconnectError');
  if(errEl){errEl.textContent='';errEl.hidden=true;}
 }
 if(key==='hp_manufacturer_map_json'){
  const statusEl=$('mapValidationStatus');
  if(statusEl){
   const mapText=String(value||'').trim();
   if(!mapText){
    statusEl.className='map-validation-status';
    statusEl.textContent='Provisional fallback map scaled by nominal capacity estimate; no manufacturer equipment selected.';
   }else{
    try{
     const d=JSON.parse(mapText);
     const meta=d.metadata||{};
     statusEl.className='map-validation-status is-valid';
     statusEl.innerHTML=`<span class="hp-meta-badge">Model: ${esc(meta.model||'Unknown')} · ${esc(meta.source||'Custom')} · ${meta.includes_defrost?'Defrost included':'No defrost in map'}</span>`;
    }catch(err){
     statusEl.className='map-validation-status is-invalid';
     statusEl.innerHTML=`JSON validation message: ${esc(err.message)}`;
    }
   }
  }
 }
 settings[key]=(value&&typeof value==='object')?deepClone(value):value;
 if(key==='load_scenario'||key==='planning_heat_pump'||key==='hp_disconnect_gas'||key==='hp_electric_cooking'){
  updateGasEliminatedLabels();
  updateHpNotices();
 }
 if(BATTERY_SETTINGS.includes(key)){
  normalizeBatterySettings(settings,key);
  for(const linked of BATTERY_SETTINGS){
   syncSetting(linked,source?.dataset?.setting===linked?source:null);
   if(typeof updateControlModifiedState==='function')updateControlModifiedState(linked);
  }
  updateControlModifiedState('battery_group');
  if(typeof syncSolarBatteryStep==='function')syncSolarBatteryStep();
 }else{
  syncSetting(key,source);
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(key);
 }
 syncOptimizerEquipmentInputs(key);
 clearEditedQuoteOverrides(key);refreshConditionalControls();schedule();
 if(key==='panel_watts'||key==='roof_panels'){
  if(typeof activeResultProfile!=='undefined'&&activeResultProfile){
   const limits=Object.fromEntries(activeResultProfile.roofs.map(r=>[r.id,Math.floor((r.max_kwp*1000+1e-7)/Number(settings.panel_watts))]));
   if(typeof optimizerPrefs!=='undefined')optimizerPrefs.panel_maxima=limits;
   if(typeof renderProfileRoofControls==='function')renderProfileRoofControls();
  }
 }
 if((panelKeys.includes(key)||key==='roof_panels'||key==='panel_watts')&&typeof renderRoofLimitsComparison==='function')renderRoofLimitsComparison();
 if(optimizerResult)renderCandidates();
 if(typeof updateModifiedBadge==='function')updateModifiedBadge();
 if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
 if(key==='planning_heat_pump'&&typeof renderControls==='function')renderControls();
 if(key==='heating_schedule_mode'){
  if(typeof renderControls==='function')renderControls();
  updateAcScheduleTimeline();
 }
 if(key==='ac_heating_windows'&&source!=='ac_editor'){
  if(typeof renderControls==='function')renderControls();
  updateAcScheduleTimeline();
 }
}
function resetSetting(key){
 if(key==='ac_heating_additional_demand_pct'){
  acDemandPctDraft=null;
  acDemandPctError=null;
  const defVal=schema?.defaults?.ac_heating_additional_demand_pct??0;
  editSetting(key,defVal);
  if(typeof updateModifiedBadge==='function')updateModifiedBadge();
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(key);
  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
  return;
 }
 if(SCHEDULE_SETTINGS.includes(key)||key==='schedule_group'){
  acScheduleDraft=null;
  acScheduleDraftInvalid=false;
  for(const k of SCHEDULE_SETTINGS){
   if(Object.hasOwn(schema.defaults,k))settings[k]=deepClone(schema.defaults[k]);
  }
  for(const k of SCHEDULE_SETTINGS){
   syncSetting(k);
   if(typeof updateControlModifiedState==='function')updateControlModifiedState(k);
  }
  refreshConditionalControls();
  schedule();
  if(typeof updateModifiedBadge==='function')updateModifiedBadge();
  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
  if(typeof renderControls==='function')renderControls();
  updateAcScheduleTimeline();
  return;
 }
 if(key==='electrification_capex'){
  if(Object.hasOwn(schema.defaults,'electrification_capex_include_baseline')){
   editSetting('electrification_capex_include_baseline',deepClone(schema.defaults.electrification_capex_include_baseline));
  }
  editSetting(key,deepClone(schema.defaults[key]));
  if(typeof updateModifiedBadge==='function')updateModifiedBadge();
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(key);
  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
  return;
 }
 if(key!=='battery_group'&&!Object.hasOwn(schema.defaults,key))return;
 if(BATTERY_SETTINGS.includes(key)||key==='battery_group'){
  for(const k of BATTERY_SETTINGS){
   if(Object.hasOwn(schema.defaults,k))settings[k]=deepClone(schema.defaults[k]);
  }
  normalizeBatterySettings(settings);
  for(const k of BATTERY_SETTINGS){
   syncSetting(k);
   if(typeof updateControlModifiedState==='function')updateControlModifiedState(k);
  }
  updateControlModifiedState('battery_group');
  clearEditedQuoteOverrides('battery_kwh');
  syncOptimizerEquipmentInputs();
  refreshConditionalControls();
  schedule();
  if(typeof updateModifiedBadge==='function')updateModifiedBadge();
  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
  return;
 }
 editSetting(key,deepClone(schema.defaults[key]));
 if(typeof updateModifiedBadge==='function')updateModifiedBadge();
 if(typeof updateControlModifiedState==='function')updateControlModifiedState(key);
 if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
}
async function saveDefault(key){
 const targetKey=key==='battery_group'?'battery_modules':key;
 if(!Object.hasOwn(settings,targetKey))return;

 if(SCHEDULE_SETTINGS.includes(targetKey)){
  if(hasInvalidAcScheduleDraft()){
   const firstInvalidInput=document.querySelector?.('.ac-time-input.is-invalid, .ac-time-input[aria-invalid="true"]');
   firstInvalidInput?.focus?.();
   showError(new Error('Cannot save default: schedule draft contains invalid time windows. Fix errors before saving.'));
   return;
  }
 }
 if(targetKey==='ac_heating_additional_demand_pct'){
  if(hasInvalidAcDemandPctDraft()){
   const input=document.getElementById?.('ac_heating_additional_demand_pct')||document.querySelector?.('[data-setting="ac_heating_additional_demand_pct"]');
   input?.focus?.();
   const errEl=document.getElementById?.('ac_heating_additional_demand_pct_error');
   const msg=acDemandPctError||'Enter a valid non-negative percentage.';
   if(errEl){errEl.textContent=msg;errEl.hidden=false;}
   showError(new Error(msg));
   return;
  }
 }

 const record=schema.records.find(r=>r.name===targetKey);
 let snapshotValue, snapshotContext={};

 if(SCHEDULE_SETTINGS.includes(targetKey)){
  const snapshotWindows=deepClone(settings.ac_heating_windows||[]);
  const snapshotMode=settings.heating_schedule_mode;
  snapshotValue=targetKey==='ac_heating_windows'?snapshotWindows:snapshotMode;
  snapshotContext={snapshotWindows,snapshotMode};
 }else if(BATTERY_SETTINGS.includes(targetKey)){
  snapshotValue=settings[targetKey];
  snapshotContext={
   battery_kwh:settings.battery_kwh,
   battery_modules:settings.battery_modules,
   battery_module_kwh:settings.battery_module_kwh,
   battery_mixed_sizes:settings.battery_mixed_sizes,
   battery_secondary_module_kwh:settings.battery_secondary_module_kwh,
   battery_secondary_modules:settings.battery_secondary_modules
  };
 }else{
  snapshotValue=deepClone(settings[targetKey]);
 }

 if(!await confirmDefault(targetKey,snapshotValue,record,snapshotContext))return;

 try{
  let body={name:targetKey,value:snapshotValue};
  if(SCHEDULE_SETTINGS.includes(targetKey)){
   body.schedule_settings={
    ac_heating_windows:deepClone(snapshotContext.snapshotWindows),
    heating_schedule_mode:snapshotContext.snapshotMode
   };
  }
  if(BATTERY_SETTINGS.includes(targetKey)){
   body.battery_settings=snapshotContext;
  }
  const saved=await api('/api/defaults',body);
  if(saved.defaults&&saved.records){
   Object.assign(schema.defaults,saved.defaults);
   for(const rec of saved.records){
    const idx=schema.records.findIndex(r=>r.name===rec.name);
    if(idx>=0)schema.records[idx]=rec;
   }
   for(const rec of saved.records){
    const linked=rec.name;
    const nodes=document.querySelectorAll?.(`[data-control="${linked}"]`);
    nodes?.forEach?.(node=>{
     const tag=node.querySelector?.('.tag'),source=node.querySelector?.('.control-source')||node.querySelectorAll?.('small')?.[1];
     if(tag&&rec){tag.textContent=rec.status;tag.className=`tag ${rec.status}`;}
     if(source&&rec){source.textContent=`Source: ${rec.source}`;source.title=rec.source;}
    });
    if(typeof updateControlModifiedState==='function')updateControlModifiedState(linked);
   }
   if(BATTERY_SETTINGS.includes(targetKey)){
    const batGroupNodes=document.querySelectorAll?.('.battery-group-control,[data-control="battery_group"]');
    batGroupNodes?.forEach?.(node=>{
     const rec=schema.records.find(r=>r.name==='battery_modules');
     const tag=node.querySelector?.('.tag'),source=node.querySelector?.('.control-source')||node.querySelectorAll?.('small')?.[1];
     if(tag&&rec){tag.textContent=rec.status;tag.className=`tag ${rec.status}`;}
     if(source&&rec){source.textContent=`Source: ${rec.source}`;source.title=rec.source;}
    });
    updateControlModifiedState('battery_group');
   }
  }else{
   schema.defaults[targetKey]=saved.value;
   const index=schema.records.findIndex(r=>r.name===targetKey);if(index>=0)schema.records[index]=saved.record;
   const nodes=document.querySelectorAll?.(`[data-control="${targetKey}"]`);
   nodes?.forEach?.(node=>{
    const tag=node.querySelector?.('.tag'),source=node.querySelector?.('.control-source')||node.querySelectorAll?.('small')?.[1];
    if(tag){tag.textContent=saved.record.status;tag.className=`tag ${saved.record.status}`;}
    if(source){source.textContent=`Source: ${saved.record.source}`;source.title=saved.record.source;}
   });
   if(typeof updateControlModifiedState==='function')updateControlModifiedState(targetKey);
  }
  if(typeof updateModifiedBadge==='function')updateModifiedBadge();
  if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
  applyControlFilters();
  $('error').hidden=true;
  if(sharedSync)await pollSharedState();
  $('status').textContent=`${title(targetKey)} saved as shared default${saved.restart_required?'; restart the local server to load the new simulation defaults':''}`;
 }catch(error){showError(error);}
}
function confirmDefault(key,value,record,context={}){
 const dialog=$('defaultConfirm');
 let shown;
 if(SCHEDULE_SETTINGS.includes(key)){
  const windows=context.snapshotWindows??(settings.ac_heating_windows||[]);
  const mode=context.snapshotMode??settings.heating_schedule_mode;
  const tzMode=settings.timezone_mode||schema?.defaults?.timezone_mode||'civil';
  const tz=tzMode==='standard'?'Standard fixed UTC+1':'Europe/Rome (DST)';
  shown=formatScheduleConfirmationSummary(windows,mode,tz);
 }else{
  if(isPercentControl(record)){
   shown=value==null?'Not provided':`${formatPercentValue(value)}${record.unit==='fraction/year'?' /year':''}`;
  }else{
   shown=value==null?'Not provided':`${value} ${record?.unit||''}`.trim();
  }
 }
 $('defaultConfirmTitle').textContent=`Save ${title(key)} as the shared default?`;
 $('defaultConfirmDescription').textContent=`Current value: ${shown}. This becomes the default for this shared app, including future reloads and LAN clients. Reset will restore ${shown}. Saved scenarios and exports keep their own values.`;
 return new Promise(resolve=>{pendingDefaultConfirmation=resolve;dialog.returnValue='';dialog.showModal();});
}
const COMMON_KEYS = new Set(['retail_commodity', 'gas_commodity', 'real_discount_rate', 'deduction_rate', 'battery_kwh']);
const PINNED_KEYS = COMMON_KEYS;

function renderPinnedControls(){
 const pinnedList=$('pinnedControlsList');
 if(!pinnedList||pinnedList===$('controls')||!schema?.records)return;
 
 const commonRecords=[
  schema.records.find(r=>r.name==='retail_commodity'),
  schema.records.find(r=>r.name==='gas_commodity'),
  schema.records.find(r=>r.name==='real_discount_rate'),
  schema.records.find(r=>r.name==='deduction_rate'),
  schema.records.find(r=>r.name==='battery_kwh')
 ].filter(Boolean);

 pinnedList.innerHTML=commonRecords.map(r=>control(r,true)).join('');
 pinnedList.oninput=$('controls').oninput;
 pinnedList.onchange=$('controls').oninput;
 pinnedList.onclick=$('controls').onclick;
 pinnedList.onkeydown=$('controls').onkeydown;
 updateCurrentSystemSummary();
}

function updateModifiedBadge(){
 const badge=$('modifiedCountBadge');
 if(!badge||badge===$('controls')||!schema?.defaults)return;
 let count=0;
 for(const [k,v] of Object.entries(settings)){
  if(schema.defaults[k]!==undefined&&!settingValuesEqual(schema.defaults[k],v))count++;
 }
 badge.textContent=count>0?`${count} modified`:'Default';
 badge.className=count>0?'badge modified':'badge default';

 const root=getSidebarRoot();
 if(root?.querySelectorAll){
  root.querySelectorAll('details.category-accordion').forEach(accordion=>{
   updateCategoryHeaderCounts(accordion);
  });
 }
}

const TAB_VIEW_NAMES={
 summary:'Summary',
 design:'System design',
 financing:'Financing',
 behavior:'How It Behaves',
 prices:'Prices & market',
 assumptions:'Assumptions & evidence'
};

const CATEGORY_MAP={
 layout:'System',
 equipment:'System',
 energy:'System',
 loads:'Household demand',
 heat_pump:'Household demand',
 bill:'Electricity & gas',
 gas:'Electricity & gas',
 prices:'Electricity & gas',
 export:'Export revenue',
 cer:'Export revenue',
 finance:'Investment & financing',
 cost:'Investment & financing',
 tax:'Tax',
 aging:'Degradation & replacements'
};

const CATEGORY_ORDER=[
 'System',
 'Household demand',
 'Electricity & gas',
 'Export revenue',
 'Investment & financing',
 'Tax',
 'Degradation & replacements'
];

const HP_MAIN_KEYS=[
 'hp_nominal_kw',
 'hp_indoor_temp',
 'hp_setback_delta',
 'hp_weather_sensitive',
 'hp_schedule_mode',
 'hp_morning_on',
 'hp_morning_off',
 'hp_evening_on',
 'hp_evening_off',
 'hp_curve_outdoor_cold',
 'hp_curve_flow_cold',
 'hp_curve_outdoor_mild',
 'hp_curve_flow_mild',
 'hp_tank_litres',
 'hp_tank_target_temp',
 'hp_electric_cooking',
 'hp_disconnect_gas'
];

const HP_FOLD_CONFIGS=[
 {
  id:'radiators',
  title:'Radiators & hydronics',
  keys:['hp_radiator_dt50_kw','hp_radiator_exponent','hp_return_drop','hp_distribution_loss_fraction']
 },
 {
  id:'dhw',
  title:'Domestic hot water details',
  keys:[
   'hp_tank_inlet_temp','hp_tank_ambient_temp','hp_tank_loss_w_k','hp_tank_deadband',
   'hp_tank_service_temp','hp_tank_initial_temp','hp_coil_approach','hp_dhw_demand_mode',
   'hp_dhw_litres_day','hp_prior_dhw_loss_fraction','hp_draw_morning_on','hp_draw_morning_off',
   'hp_draw_evening_on','hp_draw_evening_off','hp_draw_morning_fraction','hp_reheat_mode',
   'hp_reheat_on','hp_reheat_off','hp_hygiene_enabled','hp_hygiene_temp','hp_hygiene_weekday',
   'hp_hygiene_hour','hp_hygiene_duration_hours'
  ]
 },
 {
  id:'aux',
  title:'Auxiliary electric & losses',
  keys:[
   'hp_backup_kw','hp_backup_efficiency','hp_external_pump_kw','hp_standby_kw',
   'hp_min_modulation_fraction','hp_cycling_penalty','hp_additional_defrost_penalty',
   'hp_additional_unit_aux_kw'
  ]
 },
 {
  id:'costs',
  title:'Turnkey costs, maintenance & replacement',
  keys:[
   'hp_capex','hp_annual_maintenance','hp_replacement_year','hp_replacement_cost',
   'hp_cooking_capex','gas_disconnect_cost'
  ]
 },
 {
  id:'map',
  title:'Manufacturer performance map',
  keys:['hp_manufacturer_map_json']
 }
];

function renderHeatPumpControls(openHpFolds=new Set()){
 if(!schema?.records)return '';
 const recMap=new Map(schema.records.map(r=>[r.name,r]));
 const planRec=recMap.get('planning_heat_pump');
 const isPlanOn=Boolean(settings.planning_heat_pump);

 let html='';
 if(planRec){
  html+=control(planRec);
 }

 const mainControlsHtml=HP_MAIN_KEYS.map(k=>recMap.get(k)).filter(Boolean).map(r=>control(r)).join('');

 let foldsHtml='';
 for(const fold of HP_FOLD_CONFIGS){
  const isOpen=openHpFolds.has(fold.id);
  const foldRecords=fold.keys.map(k=>recMap.get(k)).filter(Boolean);
  const controlsInside=foldRecords.map(r=>control(r)).join('');
  foldsHtml+=`<details class="hp-advanced-fold" data-hp-fold="${esc(fold.id)}"${isOpen?' open':''}${!isPlanOn?' hidden':''}>`+
   `<summary><span class="hp-fold-title">${esc(fold.title)}</span></summary>`+
   `<div class="hp-advanced-fold-body">${controlsInside}</div>`+
   `</details>`;
 }

 html+=`<div class="hp-controls-wrap"${!isPlanOn?' hidden':''}>${mainControlsHtml}${foldsHtml}</div>`;
 return html;
}

function copyAcHeatingSchedule(){
 if(hasInvalidAcScheduleDraft()||acScheduleDraftInvalid){
  const msg='Cannot copy AC heating schedule: current schedule contains invalid time entries. Correct errors before copying.';
  showError(new Error(msg));
  return {success:false,error:msg};
 }

 const mode=settings.heating_schedule_mode||'scheduled';
 const windows=settings.ac_heating_windows;

 const targetUpdates={};

 if(mode==='continuous'){
  targetUpdates.hp_schedule_mode='continuous';
 }else{
  if(!Array.isArray(windows)||windows.length===0){
   const msg='Cannot copy AC heating schedule: schedule has no operating windows. Scheduled heat pump operation requires at least one active window. Manual edit needed.';
   showError(new Error(msg));
   return {success:false,error:msg};
  }

  if(windows.length>2){
   const msg=`Cannot copy AC heating schedule: schedule has ${windows.length} windows, but air-to-water heat pump model only supports up to 2 operating windows. Manual edit needed.`;
   showError(new Error(msg));
   return {success:false,error:msg};
  }

  for(const w of windows){
   if(!w||typeof w!=='object'){
    const msg='Cannot copy AC heating schedule: invalid window object. Manual edit needed.';
    showError(new Error(msg));
    return {success:false,error:msg};
   }
   const sm=w.start_minute;
   const em=w.end_minute;
   if(typeof sm!=='number'||typeof em!=='number'||!Number.isInteger(sm)||!Number.isInteger(em)||sm<0||sm>1439||em<0||em>1440){
    const msg='Cannot copy AC heating schedule: window bounds must be valid integer minutes. Manual edit needed.';
    showError(new Error(msg));
    return {success:false,error:msg};
   }
   if(sm%60!==0||em%60!==0){
    const startText=typeof formatAcScheduleMinute==='function'?formatAcScheduleMinute(sm):`${Math.floor(sm/60)}:${String(sm%60).padStart(2,'0')}`;
    const endText=typeof formatAcScheduleMinute==='function'?formatAcScheduleMinute(em):`${Math.floor(em/60)}:${String(em%60).padStart(2,'0')}`;
    const msg=`Cannot copy AC heating schedule: window '${w.id||'window'}' uses minute-level timing (${startText}–${endText}). Air-to-water heat pump model only supports whole-hour boundaries. Manual edit needed.`;
    showError(new Error(msg));
    return {success:false,error:msg};
   }
   if(sm===0&&em===1440){
    const msg=`Cannot copy AC heating schedule: window '${w.id||'window'}' is an all-day (24h) window, which cannot be represented in scheduled heat pump mode. Switch heat pump to continuous mode instead, or edit windows manually.`;
    showError(new Error(msg));
    return {success:false,error:msg};
   }
  }

  let targetMorning=[0,0];
  let targetEvening=[0,0];

  if(windows.length===1){
   const w0=windows[0];
   const s0=w0.start_minute/60;
   const e0=(w0.end_minute/60)%24;
   if(s0===e0){
    const msg='Cannot copy AC heating schedule: window is inactive (no active operating hours). Scheduled heat pump operation requires at least one active window. Manual edit needed.';
    showError(new Error(msg));
    return {success:false,error:msg};
   }
   if(w0.id==='evening'||(w0.id!=='morning'&&s0>=12)){
    targetEvening=[s0,e0];
    targetMorning=[0,0];
   }else{
    targetMorning=[s0,e0];
    targetEvening=[0,0];
   }
  }else{
   let mWin=windows.find(w=>w.id==='morning');
   let eWin=windows.find(w=>w.id==='evening');
   if(!mWin&&!eWin){
    if(windows[0].start_minute<=windows[1].start_minute){
     mWin=windows[0];
     eWin=windows[1];
    }else{
     mWin=windows[1];
     eWin=windows[0];
    }
   }else if(mWin&&!eWin){
    eWin=windows.find(w=>w!==mWin);
   }else if(!mWin&&eWin){
    mWin=windows.find(w=>w!==eWin);
   }
   const m_on=mWin.start_minute/60;
   const m_off=(mWin.end_minute/60)%24;
   const e_on=eWin.start_minute/60;
   const e_off=(eWin.end_minute/60)%24;

   if(m_on===m_off&&e_on===e_off){
    const msg='Cannot copy AC heating schedule: all operating windows are inactive. Scheduled heat pump operation requires at least one active window. Manual edit needed.';
    showError(new Error(msg));
    return {success:false,error:msg};
   }
   targetMorning=[m_on,m_off];
   targetEvening=[e_on,e_off];
  }

  targetUpdates.hp_schedule_mode='scheduled';
  targetUpdates.hp_morning_on=targetMorning[0];
  targetUpdates.hp_morning_off=targetMorning[1];
  targetUpdates.hp_evening_on=targetEvening[0];
  targetUpdates.hp_evening_off=targetEvening[1];
 }

 const errEl=$('error');
 if(errEl){errEl.textContent='';errEl.hidden=true;}
 for(const [k,v] of Object.entries(targetUpdates)){
  settings[k]=v;
  syncSetting(k);
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(k);
 }
 refreshConditionalControls();
 schedule();
 if(typeof updateModifiedBadge==='function')updateModifiedBadge();
 if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
 return {success:true,updates:targetUpdates};
}
const copyCurrentBoilerWindows=copyAcHeatingSchedule;


function dayhourScenarioLabel(scenario, activeSettings){
 const effSettings = activeSettings || (typeof current !== 'undefined' && current?.result?.settings) || (typeof settings !== 'undefined' ? settings : {});
 const hpActive = Boolean(effSettings?.planning_heat_pump);
 if(scenario==='electrified'){
  if(hpActive){
   if(effSettings.hp_disconnect_gas){
    return 'Electrified · heat pump (gas disconnected)';
   }
   if(effSettings.hp_electric_cooking){
    return 'Electrified · heat pump (all-electric)';
   }
   return 'Electrified · heat pump (gas cooking retained)';
  }
  return 'Electrified · gas eliminated';
 }
 if(scenario==='ev'){
  if(hpActive){
   if(effSettings.hp_disconnect_gas){
    return 'Electrified + EV · heat pump (gas disconnected)';
   }
   if(effSettings.hp_electric_cooking){
    return 'Electrified + EV · heat pump (all-electric)';
   }
   return 'Electrified + EV · heat pump (gas cooking retained)';
  }
  return 'Electrified + EV';
 }
 return ({baseline:'Historical electricity',electrified:'Electrified · gas eliminated',ev:'Electrified + EV'})[scenario]||scenario;
}

function updateGasEliminatedLabels(){
 const label=dayhourScenarioLabel('electrified');
 const evLabel=dayhourScenarioLabel('ev');
 const selectors=['#solarScenario option[value="electrified"]','#batScenario option[value="electrified"]','#dayhourScenario option[value="electrified"]','[data-behavior-scen="electrified"]'];
 const evSelectors=['#solarScenario option[value="ev"]','#batScenario option[value="ev"]','#dayhourScenario option[value="ev"]','[data-behavior-scen="ev"]'];
 if(typeof document!=='undefined'&&document.querySelectorAll){
  selectors.forEach(sel=>{
   document.querySelectorAll(sel).forEach(el=>{
    el.textContent=label;
   });
  });
  evSelectors.forEach(sel=>{
   document.querySelectorAll(sel).forEach(el=>{
    el.textContent=evLabel;
   });
  });
 }
}

function updateHpNotices(){
 if(typeof document==='undefined')return;
 const isHp=Boolean(settings?.planning_heat_pump);
 const scen=settings?.load_scenario||'baseline';
 const isBase=scen==='baseline';

 const ctxSummary=$('hpContextNoticeSummary');
 const ctxDesign=$('hpContextNoticeDesign');
 const ctxBehavior=$('hpContextNoticeBehavior');
 const baseSummary=$('hpBaselineNoticeSummary');
 const baseBehavior=$('hpBaselineNoticeBehavior');

 if(!isHp){
  if(ctxSummary)ctxSummary.hidden=true;
  if(ctxDesign)ctxDesign.hidden=true;
  if(ctxBehavior)ctxBehavior.hidden=true;
  if(baseSummary)baseSummary.hidden=true;
  if(baseBehavior)baseBehavior.hidden=true;
  return;
 }

 if(isBase){
  if(ctxSummary)ctxSummary.hidden=true;
  if(ctxDesign)ctxDesign.hidden=true;
  if(ctxBehavior)ctxBehavior.hidden=true;
  const makeBaseHtml=id=>`<span>Historical baseline selected: heat pump space heating and hot water plan are excluded in this view to show historical bills and electricity demand.</span> <button type="button" class="action-btn-small" data-action="show-hp-plan"${id?` id="${id}"`:''}>Show heat-pump plan</button>`;
  if(baseSummary){baseSummary.innerHTML=makeBaseHtml('showHpPlanBtnSummary');baseSummary.hidden=false;}
  if(baseBehavior){baseBehavior.innerHTML=makeBaseHtml('showHpPlanBtnBehavior');baseBehavior.hidden=false;}
 }else{
  if(baseSummary)baseSummary.hidden=true;
  if(baseBehavior)baseBehavior.hidden=true;
  let notice='Radiators + hot water plan; gas cooking retained unless converted.';
  if(settings.hp_disconnect_gas){
   notice='Radiators + hot water plan; gas service permanently disconnected (all-electric household).';
  }else if(settings.hp_electric_cooking){
   notice='Radiators + hot water plan; cooking converted to induction (gas service retained with active meter; no gas backup modeled).';
  }
  const hasServiceShortfall=Boolean(current?.result&&(current.result.finance?.service_comparable===false||current.result.service_comparable===false));
  if(hasServiceShortfall){
   notice+=' · Note: Unmet heating detected across simulated weather years; fuel savings credit withheld.';
  }
  if(ctxSummary){ctxSummary.textContent=notice;ctxSummary.hidden=false;}
  if(ctxDesign){ctxDesign.textContent=notice;ctxDesign.hidden=false;}
  if(ctxBehavior){ctxBehavior.textContent=notice;ctxBehavior.hidden=false;}
 }
}

function prepareScenarioSettings(incoming,isPartialSharedPatch=false){
 if(!incoming||typeof incoming!=='object')return deepClone(schema?.defaults||{});
 const result=deepClone(incoming);

 const hasCanonical=Object.hasOwn(incoming,'ac_heating_windows');
 const hasLegacy=LEGACY_HEATING_KEYS.some(k=>Object.hasOwn(incoming,k));

 if(hasCanonical){
  result.ac_heating_windows=validateAcHeatingWindows(result.ac_heating_windows);
  if(hasLegacy){
   try{
    const baseWindows=isPartialSharedPatch
     ?(settings?.ac_heating_windows||schema?.defaults?.ac_heating_windows||DEFAULT_AC_HEATING_WINDOWS)
     :(schema?.defaults?.ac_heating_windows||DEFAULT_AC_HEATING_WINDOWS);
    const legacyOnlyObj={};
    for(const k of LEGACY_HEATING_KEYS){
     if(Object.hasOwn(incoming,k)) legacyOnlyObj[k]=incoming[k];
    }
    const legacyDerived=migrateAndResolveSchedule(legacyOnlyObj,baseWindows);
    const differs=!settingValuesEqual(result.ac_heating_windows,legacyDerived);
    if(differs&&typeof console!=='undefined'&&console.warn){
     console.warn('Imported scenario contains both canonical ac_heating_windows and conflicting legacy heating hours; canonical windows take precedence.',{canonical:result.ac_heating_windows,legacy:legacyDerived});
    }
   }catch(_){}
  }
 }else if(hasLegacy){
  const baseWindows=isPartialSharedPatch
   ?(settings?.ac_heating_windows||schema?.defaults?.ac_heating_windows||DEFAULT_AC_HEATING_WINDOWS)
   :(schema?.defaults?.ac_heating_windows||DEFAULT_AC_HEATING_WINDOWS);
  result.ac_heating_windows=migrateAndResolveSchedule(result,baseWindows);
 }

 for(const k of LEGACY_HEATING_KEYS){
  delete result[k];
 }

 if(!isPartialSharedPatch){
  if(!Object.hasOwn(incoming,'planning_heat_pump')){
   result.planning_heat_pump=false;
  }
  if(!Object.hasOwn(incoming,'ac_heating_additional_demand_pct')){
   result.ac_heating_additional_demand_pct=0;
  }
 }else{
  if(!Object.hasOwn(incoming,'ac_heating_additional_demand_pct')&&typeof settings!=='undefined'&&settings&&Object.hasOwn(settings,'ac_heating_additional_demand_pct')){
   result.ac_heating_additional_demand_pct=settings.ac_heating_additional_demand_pct;
  }
  if(!Object.hasOwn(incoming,'planning_heat_pump')&&typeof settings!=='undefined'&&settings&&Object.hasOwn(settings,'planning_heat_pump')){
   result.planning_heat_pump=settings.planning_heat_pump;
  }
 }

 return {...deepClone(schema?.defaults||{}),...result};
}

function cleanSettingsForBackend(snapshot){
 if(!snapshot||typeof snapshot!=='object')return snapshot;
 for(const k of LEGACY_HEATING_KEYS){
  delete snapshot[k];
 }
 if(!schema?.records)return snapshot;
 const validKeys=new Set(schema.records.map(r=>r.name));
 for(const k of Object.keys(snapshot)){
  // This engine-level pricing mode is accepted by validate() even though it is
  // deliberately not part of the editable assumptions schema.
  if(!validKeys.has(k)&&k!=='storage_cost_mode'&&!(typeof activeResultProfile!=='undefined'&&activeResultProfile&&k==='roof_panels')){
   delete snapshot[k];
  }
 }
 return snapshot;
}

if(typeof document!=='undefined'&&document.addEventListener){
 document.addEventListener('click',e=>{
  if(e.target?.dataset?.action==='show-hp-plan'||e.target?.id==='showHpPlanBtn'||e.target?.id==='showHpPlanBtnSummary'||e.target?.id==='showHpPlanBtnBehavior'||e.target?.closest?.('[data-action="show-hp-plan"]')){
   editSetting('load_scenario','electrified');
  }
 });
}

function renderControls(viewName=null){
 if(!schema?.records)return;
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile)renderProfileRoofControls();
 const activeTab=viewName||(typeof document!=='undefined'&&document.querySelector?.('nav button.active')?.dataset?.view)||'summary';
 if(activeTab&&$('activeTabAssumptionsPill'))$('activeTabAssumptionsPill').textContent=TAB_VIEW_NAMES[activeTab]||title(activeTab);

 const activeEl=typeof document!=='undefined'?document.activeElement:null;
 let focusInfo=null;
 if(activeEl&&(activeEl.dataset?.setting||activeEl.dataset?.slider||activeEl.id)){
  focusInfo={
   setting:activeEl.dataset?.setting,
   slider:activeEl.dataset?.slider,
   id:activeEl.id,
   isPinned:Boolean(activeEl.closest?.('#pinnedControlsList')),
   selectionStart:(typeof activeEl.selectionStart==='number')?activeEl.selectionStart:null,
   selectionEnd:(typeof activeEl.selectionEnd==='number')?activeEl.selectionEnd:null
  };
 }

 const openDetailsControls=new Set();
 if(typeof document!=='undefined'&&document.querySelectorAll){
  document.querySelectorAll('details.control-details[open]').forEach(d=>{
   const ctrl=d.closest?.('[data-control]')?.dataset?.control;
   if(ctrl)openDetailsControls.add(ctrl);
  });
 }

 const openCategories=new Map();
 if(typeof document!=='undefined'&&document.querySelectorAll){
  document.querySelectorAll('details.category-accordion, details.group[data-category]').forEach(el=>{
   const cat=el.dataset?.category||el.querySelector?.('.category-title')?.textContent?.trim();
   if(cat)openCategories.set(cat,Boolean(el.open));
  });
 }

 const openHpFolds=new Set();
 if(typeof document!=='undefined'&&document.querySelectorAll){
  document.querySelectorAll('details.hp-advanced-fold[open]').forEach(d=>{
   const foldId=d.dataset?.hpFold;
   if(foldId)openHpFolds.add(foldId);
  });
 }

 const hpActive=Boolean(settings.planning_heat_pump);
 const editableRecords=schema.records.filter(r=>!(typeof activeResultProfile!=='undefined'&&activeResultProfile&&r.name==='roof_panels')&&!COMMON_KEYS.has(r.name)&&r.name!=='cashflow_mode'&&r.name!=='electrification_capex_include_baseline'&&!HP_ALL_KEYS_SET.has(r.name)&&(hpActive ? r.name!=='gas_disconnect_cost' : true));

 const groups=new Map();
 for(const cat of CATEGORY_ORDER)groups.set(cat,[]);

 for(const r of editableRecords){
  const cat=CATEGORY_MAP[r.group]||(r.group?title(r.group):'Other');
  if(!groups.has(cat))groups.set(cat,[]);
  groups.get(cat).push(r);
 }

 let accordionsHtml='';
 for(const [category,records] of groups.entries()){
  if(!records.length&&category!=='Household demand')continue;
  const availableRecords=records.filter(r=>isRecordConditionallyAvailable(r));
  const availableCount=availableRecords.length;
  let catModCount=0;
  for(const r of availableRecords){
   if(schema.defaults[r.name]!==undefined&&!settingValuesEqual(schema.defaults[r.name],settings[r.name]))catModCount++;
  }
  const badgeHtml=catModCount>0?`<span class="badge modified category-badge">${catModCount} modified</span>`:`<span class="category-badge"></span>`;
  const countHtml=`<span class="category-count">${availableCount} ${availableCount===1?'input':'inputs'}</span>`;
  const isCatOpen=(searchDisclosure&&searchDisclosure.has(category))
   ?Boolean(searchDisclosure.get(category))
   :(allModeDisclosures.has(category)?Boolean(allModeDisclosures.get(category)):Boolean(openCategories.get(category)));
  let controlsHtml='';
  if(category==='System'){
   let renderedBatteryGroup=false;
   for(const r of records){
    if(BATTERY_CONTROL_KEYS.has(r.name)){
     if(!renderedBatteryGroup){
      controlsHtml+=renderBatteryGroupControl();
      renderedBatteryGroup=true;
     }
    }else{
     controlsHtml+=control(r);
    }
   }
  }else if(category==='Household demand'){
   controlsHtml=records.map(r=>control(r)).join('');
   controlsHtml+=renderHeatPumpControls(openHpFolds);
  }else if(category==='Electricity & gas'){
   const elecRecords=[];
   const gasPretaxRecords=[];
   const gasTaxRecords=[];
   const otherRecords=[];
   for(const r of records){
    if(['gas_commodity','gas_regulated','gas_fixed_day','gas_pcs'].includes(r.name)){
     gasPretaxRecords.push(r);
    }else if(['gas_excise_0_120','gas_excise_120_480','gas_excise_480_1560','gas_excise_over_1560','gas_regional_0_120','gas_regional_over_120','gas_vat_reduced','gas_vat_standard'].includes(r.name)){
     gasTaxRecords.push(r);
    }else if(r.group==='bill'||r.name.startsWith('retail_')||r.name.startsWith('electric_')||r.name.startsWith('power_')){
     elecRecords.push(r);
    }else{
     otherRecords.push(r);
    }
   }
   let catHtml='';
   if(elecRecords.length){
    catHtml+=`<div class="control-section-header" data-section="elec"><span class="control-section-title">Electricity retail tariffs</span></div>`+elecRecords.map(r=>control(r)).join('');
   }
   if(gasPretaxRecords.length){
    catHtml+=`<div class="control-section-header" data-section="gas_pretax"><span class="control-section-title">Natural gas retail tariffs (pre-tax)</span></div>`+gasPretaxRecords.map(r=>control(r)).join('');
   }
   if(gasTaxRecords.length){
    catHtml+=`<div class="control-section-header" data-section="gas_tax"><span class="control-section-title">Gas consumption taxes &amp; VAT</span><small class="muted control-section-note">Progressive national excise, Campania regional surcharge (ARISGAN), and gas VAT. Applies to delivered residential gas; distinct from solar-export income taxes (Tax group).</small></div>`+gasTaxRecords.map(r=>control(r)).join('');
   }
   if(otherRecords.length){
    catHtml+=otherRecords.map(r=>control(r)).join('');
   }
   controlsHtml=catHtml;
  }else if(category==='Tax'){
   controlsHtml=`<div class="control-section-header" data-section="solar_tax"><small class="muted control-section-note">Personal income taxes (IRPEF) and regional/municipal surcharges on solar export revenue. Distinct from natural gas consumption taxes.</small></div>`+records.map(r=>control(r)).join('');
  }else{
   controlsHtml=records.map(r=>control(r)).join('');
  }
  accordionsHtml+=`<details class="group category-accordion" data-category="${esc(category)}"${isCatOpen?' open':''}>`+
   `<summary><span class="category-title">${esc(category)}</span> <span class="category-meta">${countHtml} ${badgeHtml}</span></summary>`+
   `<div class="category-controls-container">${controlsHtml}</div>`+
   `</details>`;
 }

 $('controls').innerHTML=accordionsHtml;
 updateAcScheduleTimeline();
 if(typeof document!=='undefined'&&document.querySelectorAll){
  document.querySelectorAll('details.category-accordion').forEach(accordion=>{
   accordion.addEventListener?.('toggle',()=>{
    if(sidebarFilterMode==='all'&&!activeSearch){
     const cat=accordion.dataset?.category||accordion.querySelector?.('.category-title')?.textContent?.trim();
     if(cat)allModeDisclosures.set(cat,Boolean(accordion.open));
    }
   });
  });
 }
 $('controls').oninput=e=>{
  if(e.target.dataset.acWindowId){
   if(e.target.dataset.acRangeType){
    handleAcScheduleRangeInput(e.target.dataset.acWindowId,e.target.dataset.acRangeType,Number(e.target.value),e.target);
    return;
   }
   handleAcScheduleTimeInput(e.target.dataset.acWindowId,e.target.dataset.acTimeType,e.target.value,e.target);
   return;
  }
  if(e.target.dataset.batteryRow){
   editMixedBatteryRowQuantity(e.target.dataset.batteryRow,e.target.value,e.target);
   return;
  }
  const key=e.target.dataset.setting||e.target.dataset.slider;if(!key)return;
  if(key==='ac_heating_additional_demand_pct'){
   handleAcHeatingDemandPctInput(e.target.value,e.target);
   return;
  }
  const record=schema.records.find(r=>r.name===key),original=schema.defaults[key];let value=e.target.type==='checkbox'?e.target.checked:e.target.value;
  if(isPercentControl(record)&&e.target.type!=='range'&&e.target.type!=='select-one'){
   const res=parsePercentInput(value,record);
   if(!res.valid){
    e.target.setAttribute?.('aria-invalid','true');
    e.target.classList?.add?.('is-invalid');
    return;
   }
   e.target.removeAttribute?.('aria-invalid');
   e.target.classList?.remove?.('is-invalid');
   editSetting(key,res.value,e.target);
   return;
  }
  if(e.isTrusted&&e.target.type==='number'&&!e.inputType&&value!==''&&record.bounds){
   const bounded=Math.max(record.bounds[0]??-Infinity,record.bounds[1]==null?Number(value):Math.min(record.bounds[1],Number(value)));
   if(bounded!==Number(value))e.target.value=String(bounded);
   value=String(bounded);
  }
  const parsed=e.target.type==='checkbox'?Boolean(e.target.checked):(typeof original==='boolean'?value==='true':(typeof original==='string'?value:(value===''?null:Number(value))));
  if(typeof parsed==='number'&&!Number.isFinite(parsed))return;
  editSetting(key,parsed,e.target);};
  $('controls').onchange=e=>{
   const key=e.target.dataset.setting;
   if(key){
    const record=schema?.records?.find(r=>r.name===key);
    if(isPercentControl(record)&&e.target.type!=='range'&&e.target.type!=='select-one'){
     const res=parsePercentInput(e.target.value,record);
     if(!res.valid){
      e.target.setAttribute?.('aria-invalid','true');
      e.target.classList?.add?.('is-invalid');
      return;
     }
     e.target.removeAttribute?.('aria-invalid');
     e.target.classList?.remove?.('is-invalid');
     editSetting(key,res.value,e.target);
     e.target.value=formatPercentValue(res.value);
     return;
    }
   }
   $('controls').oninput(e);
  };
 $('controls').onclick=e=>{
  const addBtn=e.target.id==='acWindowAddBtn'?e.target:(typeof e.target.closest==='function'?e.target.closest('#acWindowAddBtn,[data-ac-add-window]'):null);
  if(addBtn&&(addBtn.id==='acWindowAddBtn'||addBtn.dataset?.acAddWindow)){
   addAcScheduleWindow();
   return;
  }
  const removeBtn=e.target.dataset?.removeWindow?e.target:(typeof e.target.closest==='function'?e.target.closest('[data-remove-window]'):null);
  if(removeBtn&&removeBtn.dataset?.removeWindow){
   removeAcScheduleWindow(removeBtn.dataset.removeWindow);
   return;
  }
  const copyBtn=(e.target.id==='copyBoilerWindowsBtn'||e.target.id==='copyAcScheduleBtn')?e.target:(typeof e.target.closest==='function'?e.target.closest('#copyBoilerWindowsBtn,#copyAcScheduleBtn'):null);
  if(copyBtn&&(copyBtn.id==='copyBoilerWindowsBtn'||copyBtn.id==='copyAcScheduleBtn')){
   copyCurrentBoilerWindows();
   return;
  }
  const saveKey=e.target.closest('[data-save-default]')?.dataset.saveDefault;if(saveKey){saveDefault(saveKey);return;}const key=e.target.closest('[data-reset-setting]')?.dataset.resetSetting;if(key)resetSetting(key);
 };
 $('controls').onkeydown=e=>{
  if(e.target.dataset.batteryRow&&e.target.type==='number'&&['ArrowUp','ArrowDown'].includes(e.key)){
   e.preventDefault();
   const val=Number(e.target.value)||0;
   const next=Math.max(0,val+(e.key==='ArrowUp'?1:-1));
   e.target.value=String(next);
   editMixedBatteryRowQuantity(e.target.dataset.batteryRow,next,e.target);
   return;
  }
  const key=e.target.dataset.setting;
   if(!key||!['ArrowUp','ArrowDown'].includes(e.key))return;
   const record=schema.records.find(r=>r.name===key);
   if(isPercentControl(record)&&e.target.type!=='select-one'){
    e.preventDefault();
    const curVal=settings[key]??schema.defaults[key]??0;
    const step=numericStep(record)||0.01;
    let next=Number((curVal+(e.key==='ArrowUp'?step:-step)).toFixed(6));
    if(record.bounds){
     const [min,max]=record.bounds;
     if(min!=null)next=Math.max(min,next);
     if(max!=null)next=Math.min(max,next);
    }
    editSetting(key,next,e.target);
    e.target.value=formatPercentValue(next);
    e.target.removeAttribute?.('aria-invalid');
    e.target.classList?.remove?.('is-invalid');
    return;
   }
   if(e.target.type!=='number')return;
   if(key==='ac_heating_additional_demand_pct'){
    e.preventDefault();
    const curVal=e.target.value===''?(schema?.defaults?.[key]??0):Number(e.target.value);
    const current=Number.isFinite(curVal)?curVal:0;
    const next=Math.max(0,current+(e.key==='ArrowUp'?1:-1));
    e.target.value=String(next);
    handleAcHeatingDemandPctInput(String(next),e.target);
    return;
   }
   e.preventDefault();const step=numericStep(record);
  const value=e.target.value===''?(schema.defaults[key]??record.bounds?.[0]??0):Number(e.target.value);
  if(!Number.isFinite(value))return;
  const decimals=Math.max(...[value,step].map(v=>{const [mantissa,exponent='0']=String(v).split('e');return Math.max(0,(mantissa.split('.')[1]?.length||0)-Number(exponent));}));
  let next=Number((value+(e.key==='ArrowUp'?step:-step)).toFixed(Math.min(20,decimals)));
  if(record.bounds)next=Math.max(record.bounds[0]??-Infinity,record.bounds[1]==null?next:Math.min(record.bounds[1],next));
  editSetting(key,next);
 };
 syncOptimizerEquipmentInputs();
 if(typeof renderRoofLimitsComparison==='function')renderRoofLimitsComparison();

 renderPinnedControls();
 initSidebarModeSelector();
 if(activeSearch&&$('search')&&$('search')!==$('controls')){
  $('search').value=activeSearch;
 }

 if(openDetailsControls.size>0&&typeof document!=='undefined'&&document.querySelectorAll){
  document.querySelectorAll('details.control-details').forEach(d=>{
   const ctrl=d.closest?.('[data-control]')?.dataset?.control;
   if(ctrl&&openDetailsControls.has(ctrl))d.open=true;
  });
 }

 refreshConditionalControls();
 updateGasEliminatedLabels();
 updateHpNotices();

 if(focusInfo&&typeof document!=='undefined'){
  let target=null;
  if(focusInfo.setting){
   if(focusInfo.isPinned)target=document.querySelector?.(`#pinnedControlsList [data-setting="${focusInfo.setting}"]`);
   if(!target)target=document.querySelector?.(`[data-setting="${focusInfo.setting}"]`);
  }else if(focusInfo.slider){
   target=document.querySelector?.(`[data-slider="${focusInfo.slider}"]`);
  }else if(focusInfo.id){
   target=document.getElementById?.(focusInfo.id);
  }
  if(target&&typeof target.focus==='function'){
   target.focus();
   if(focusInfo.selectionStart!=null&&typeof target.setSelectionRange==='function'){
    try{target.setSelectionRange(focusInfo.selectionStart,focusInfo.selectionEnd??focusInfo.selectionStart);}catch(_){}
   }
  }
 }

 updateModifiedBadge();
 updateCurrentSystemSummary();
}
function updateSidebarForTab(viewName){
 const canonical=LEGACY_ID_MAP[viewName]||viewName;
 currentViewName=canonical;
 if(viewName&&$('activeTabAssumptionsPill'))$('activeTabAssumptionsPill').textContent=TAB_VIEW_NAMES[canonical]||title(canonical);
 const pinnedList=$('pinnedControlsList');
 if(pinnedList&&pinnedList!==$('controls')&&(!pinnedList.innerHTML||!pinnedList.children||pinnedList.children.length===0)){
  renderPinnedControls();
 }
 applyControlFilters();
 updateCurrentSystemSummary();
}
function displayCashRows(rows){return (rows||[]).map(c=>({...c,...Object.fromEntries(['pv_incremental_range','whole_package_range','equity_range','financed_project_range','debt_service','debt_service_year_end_equivalent','loan_interest','loan_principal','tax_deduction','replacement','residual','hp_maintenance','hp_replacement','unserved_service_allowance','avoided_gas_credit','actual_remaining_gas_bill'].map(k=>[k,c['display_'+k]??c[k]]))}));}
function clearQuote(){quote={line_items:[]};}
function useCandidate(row){if(typeof activeResultProfile!=='undefined'&&activeResultProfile)profileCounts(row);settings={...row.settings};clearQuote();}
function cashFlowLabel(mode){return `${mode==='real'?'Real':'Nominal'} year-end cash flows · upfront payment at year 0, then annual savings, receipts, operating costs and replacements.`;}
let cashTableMode = 'mean';
let lastSummaryScenarioFingerprint = null;
let currentSummaryData = null;
let prePrintOpenState = false;
let cashStickyCleanup = null;

if (typeof window !== 'undefined' && window.addEventListener && !window._cashPrintBound) {
 window._cashPrintBound = true;
 window.addEventListener('beforeprint', () => {
  const d = $('cashFlowDetails');
  if (d) {
   prePrintOpenState = d.open;
   d.open = true;
  }
 });
 window.addEventListener('afterprint', () => {
  const d = $('cashFlowDetails');
  if (d) d.open = prePrintOpenState;
 });
}

function computeEnvelope(vals) {
 if (!vals || !vals.length) return null;
 const sorted = [...vals].sort((a, b) => a - b);
 const m = Math.floor(sorted.length / 2);
 const median = sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2;
 const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
 return { min: sorted[0], median, mean, max: sorted.at(-1) };
}

/**
 * Computes calendar-year timing adjustments (compounding corrections) for monthly loan repayments.
 * Scheduled monthly repayments within each calendar year are compounded to year-end at nominal discount rate rnom.
 * Horizon balloon / settlement payments at horizon end are not adjusted.
 */
function loanDebtTimingAdjustments(result) {
  if (!result) return [];
  const f = result.finance || {};
  const s = result.settings || {};
  const principal = f.loan_principal_eur ?? s.loan_amount ?? 0;
  if (principal <= 0) return [];
  const fees = f.loan_upfront_fees_or_tax_eur || 0;
  const amortized = f.loan_amortized_principal_eur ?? (principal + fees);
  if (amortized <= 0) return [];

  const termYears = f.loan_years ?? s.loan_years ?? 0;
  const n = Math.round(termYears * 12);
  if (n <= 0) return [];

  const apr = f.loan_apr ?? s.loan_apr ?? f.loan_tan ?? s.loan_tan ?? 0;
  const rm = apr / 12.0;
  let monthly = f.loan_monthly_payment_eur;
  if (!monthly || monthly <= 0) {
    if (rm > 0) {
      monthly = amortized * rm / (1.0 - Math.pow(1.0 + rm, -n));
    } else {
      monthly = amortized / n;
    }
  }

  const inflation = s.inflation || 0;
  const realRate = f.real_discount_rate ?? s.real_discount_rate ?? 0;
  const rnom = f.nominal_discount_rate ?? ((1 + realRate) * (1 + inflation) - 1);
  if (!rnom || rnom <= -1 || !isFinite(rnom)) return [];

  const maxYear = s.horizon_years || (result.annual_cashflows?.length ? (result.annual_cashflows.length - 1) : 20);
  const adjustments = Array(maxYear + 1).fill(0);

  let bal = amortized;
  const dfFactor = 1.0 + rnom;
  for (let y = 0; y < maxYear; y++) {
    const yr = y + 1;
    let yearCorrection = 0;
    for (let m = 1; m <= 12; m++) {
      const monthIdx = y * 12 + (m - 1);
      if (monthIdx < n && bal > 1e-6) {
        const pmt = Math.min(monthly, bal * (1.0 + rm));
        bal = Math.max(bal + bal * rm - pmt, 0);
        yearCorrection += pmt * (Math.pow(dfFactor, (12 - m) / 12.0) - 1.0);
      }
    }
    adjustments[yr] = yearCorrection;
  }
  return adjustments;
}

/**
 * Shared discounting cash flow selector:
 * Returns backend cashflows_for_discounting_by_weather when available;
 * for legacy results/snapshots, derives year-end equivalent debt adjustments and subtracts them
 * from nominal equity/package_equity paths (and solar_equity proportionally), leaving operating flows untouched.
 */
function getDiscountingCashflows(result) {
  if (!result) return null;
  const f = result.finance;
  if (f?.cashflows_for_discounting_by_weather) {
    const nominal = f.cashflows_by_weather || {};
    return { ...f.cashflows_for_discounting_by_weather,
      pv_incremental: nominal.pv_incremental ?? f.cashflows_for_discounting_by_weather.pv_incremental,
      whole_package: nominal.whole_package ?? f.cashflows_for_discounting_by_weather.whole_package };
  }
  const wPaths = f?.cashflows_by_weather;
  if (!wPaths) return null;
  const adjustments = loanDebtTimingAdjustments(result);
  if (!adjustments || !adjustments.some(v => v !== 0)) {
    return wPaths;
  }
  const solarLoanPrincipal = f.solar_loan_principal_eur ?? Math.min(f.capex_eur || 0, f.loan_principal_eur || 0);
  const solarLoanRatio = f.solar_loan_ratio ?? ((f.loan_principal_eur > 0) ? (solarLoanPrincipal / f.loan_principal_eur) : 1.0);

  const adjustPath = (path, ratio) => {
    if (!Array.isArray(path)) return path;
    return path.map((amt, yr) => yr === 0 ? amt : amt - (adjustments[yr] || 0) * ratio);
  };
  const adjustPaths = (paths, ratio) => {
    if (!Array.isArray(paths)) return paths;
    return paths.map(p => adjustPath(p, ratio));
  };

  const disc = { ...wPaths };
  if (wPaths.equity) disc.equity = adjustPaths(wPaths.equity, 1.0);
  if (wPaths.package_equity) disc.package_equity = adjustPaths(wPaths.package_equity, 1.0);
  if (wPaths.solar_equity) disc.solar_equity = adjustPaths(wPaths.solar_equity, solarLoanRatio);
  return disc;
}

function buildSummaryCashRows(r, displayCash) {
 if (!r || !displayCash || !displayCash.length) return null;
 const activeScope = (typeof summaryScope !== 'undefined' && summaryScope) ? summaryScope : (r.settings?.summary_scope || 'solar');
 const activeCash = (typeof summaryCash !== 'undefined' && summaryCash) ? summaryCash : (r.settings?.summary_cash || ((displayCash.some(c => (c.debt_service || 0) > 0 || (c.loan_interest || 0) > 0)) ? 'loan' : 'cash'));
 const scopeMetrics = typeof getScopeCashMetrics === 'function' ? getScopeCashMetrics(r, activeScope, activeCash) : null;
 const flowKey = scopeMetrics?.flowKey || (activeScope === 'solar' ? (activeCash === 'loan' ? 'solar_equity' : 'pv_incremental') : (activeCash === 'loan' ? 'package_equity' : 'whole_package'));

 const isReal = r.settings?.cashflow_mode === 'real';
 const inflation = r.settings?.inflation || 0;
 const defl = yr => isReal ? Math.pow(1 + inflation, yr) : 1;

 const f = r.finance || {};
 const wPaths = f.cashflows_by_weather || {};
 const pvPaths = wPaths.pv_incremental || [];
 const pkgPaths = wPaths.whole_package || [];
 const eqPaths = wPaths[flowKey] || (flowKey === 'package_equity' ? (wPaths.package_equity || wPaths.equity) : (flowKey === 'solar_equity' ? (wPaths.solar_equity || wPaths.equity) : (flowKey === 'whole_package' ? wPaths.whole_package : wPaths.pv_incremental))) || [];
 const discWPaths = getDiscountingCashflows(r) || wPaths;
 const discPvPaths = discWPaths.pv_incremental || pvPaths;
 const discPkgPaths = discWPaths.whole_package || pkgPaths;
 const discEqPaths = discWPaths[flowKey] || (flowKey === 'package_equity' ? (discWPaths.package_equity || discWPaths.equity) : (flowKey === 'solar_equity' ? (discWPaths.solar_equity || discWPaths.equity) : (flowKey === 'whole_package' ? discWPaths.whole_package : discWPaths.pv_incremental))) || eqPaths;

 const realDiscountRate = f.real_discount_rate ?? r.settings?.real_discount_rate ?? 0;
 const nominalDiscountRate = f.nominal_discount_rate ?? ((1 + realDiscountRate) * (1 + inflation) - 1);
 const activeDiscountRate = isReal ? realDiscountRate : nominalDiscountRate;
 const disc = yr => Math.pow(1 + activeDiscountRate, yr);
 const rateLabel = `${fmt(activeDiscountRate, 'pct')} ${isReal ? 'real' : 'nominal'}`;

 const maxYear = displayCash[displayCash.length - 1]?.year ?? (r.settings?.horizon_years || 20);

 const solarLoanPrincipal = f.solar_loan_principal_eur ?? Math.min(f.capex_eur || 0, f.loan_principal_eur || 0);
 const solarLoanRatio = f.solar_loan_ratio ?? ((f.loan_principal_eur > 0) ? (solarLoanPrincipal / f.loan_principal_eur) : 1.0);
 const loanRatio = (activeCash === 'cash') ? 0 : ((activeScope === 'solar') ? solarLoanRatio : 1.0);
 const loanDrawdown = (activeCash === 'cash') ? 0 : ((activeScope === 'solar') ? solarLoanPrincipal : (f.loan_principal_eur || 0));

 const cumEqPaths = eqPaths.map(row => {
  let tot = 0;
  return row.map((amt, yr) => {
   tot += (yr === 0 ? amt : amt / defl(yr));
   return tot;
  });
 });

 const rows = [];
 for (let i = 0; i < displayCash.length; i++) {
  const c = displayCash[i];
  const yr = c.year;
  if (yr === 0) {
   const p0 = c.pv_incremental ?? (-(f.capex_eur || 0));
   const w0 = c.whole_package ?? (-((f.capex_eur || 0) + (f.conversion_capex_eur || 0)));
   let eq0 = c[flowKey] ?? eqPaths[0]?.[0];
   if (eq0 == null) {
    if (activeCash === 'cash') {
     eq0 = (activeScope === 'solar' ? p0 : w0);
    } else {
     if (activeScope === 'solar') {
      const slp = f.solar_loan_principal_eur ?? Math.min(f.capex_eur || 0, f.loan_principal_eur || 0);
      eq0 = -((f.capex_eur || 0) - slp);
     } else {
      eq0 = -(((f.capex_eur || 0) + (f.conversion_capex_eur || 0)) - (f.loan_principal_eur || 0));
     }
    }
   }
   rows.push({
    year: 0,
    project: p0,
    wholeHouse: w0,
    taxDeduction: 0,
    replacement: 0,
    repCauses: [],
    debtService: -loanDrawdown,
    loanInterest: 0,
    equity: eq0,
    cumEquity: eq0
   });
  } else {
   const rep = (c.display_replacement ?? c.replacement ?? 0) + (c.hp_replacement ?? 0);
   const causes = [];
   if (rep > 0) {
    if (r.layout?.kwp > 0 && yr === r.settings?.inverter_replace_year) causes.push('Inverter replacement');
    if (r.layout?.kwp > 0 && r.settings?.battery_kwh > 0 && yr === r.settings?.battery_replace_year) causes.push('Battery replacement');
    if (c.hp_replacement > 0) causes.push('Heat pump replacement');
    if (!causes.length) causes.push('Scheduled equipment replacement');
   }
   const meanEq = isReal ? (c['display_' + flowKey + '_mean'] ?? c['display_' + flowKey] ?? c[flowKey + '_mean'] ?? c[flowKey]) : (c[flowKey + '_mean'] ?? c[flowKey]);
   const rowEq = (eqPaths.length
    ? computeEnvelope(eqPaths.map(p => p[yr] / defl(yr)))
    : (c['display_' + flowKey + '_range'] || c[flowKey + '_range'] || (meanEq ?? (c.equity_range || (c.equity ?? 0)))));
   const cumEqEnv = cumEqPaths.length
    ? computeEnvelope(cumEqPaths.map(p => p[yr]))
    : (rows[yr - 1]?.cumEquity != null
        ? (typeof rows[yr - 1].cumEquity === 'number'
            ? rows[yr - 1].cumEquity + (typeof rowEq === 'number' ? rowEq : (rowEq.mean ?? rowEq.median ?? 0))
            : {
                min: rows[yr - 1].cumEquity.min + (typeof rowEq === 'number' ? rowEq : (rowEq.min ?? rowEq.mean ?? rowEq.median ?? 0)),
                median: rows[yr - 1].cumEquity.median + (typeof rowEq === 'number' ? rowEq : (rowEq.median ?? 0)),
                mean: (rows[yr - 1].cumEquity.mean ?? rows[yr - 1].cumEquity.median) + (typeof rowEq === 'number' ? rowEq : (rowEq.mean ?? rowEq.median ?? 0)),
                max: rows[yr - 1].cumEquity.max + (typeof rowEq === 'number' ? rowEq : (rowEq.max ?? rowEq.mean ?? rowEq.median ?? 0))
              })
        : null);

   const projMean = isReal ? (c.display_pv_incremental_mean ?? c.display_pv_incremental ?? c.pv_incremental_mean ?? c.pv_incremental) : (c.pv_incremental_mean ?? c.pv_incremental);
   const pkgMean = isReal ? (c.display_whole_package_mean ?? c.display_whole_package ?? c.whole_package_mean ?? c.whole_package) : (c.whole_package_mean ?? c.whole_package);

   rows.push({
    year: yr,
    project: c.pv_incremental_range || (pvPaths.length ? computeEnvelope(pvPaths.map(p => p[yr] / defl(yr))) : (projMean ?? 0)),
    wholeHouse: c.whole_package_range || (pkgPaths.length ? computeEnvelope(pkgPaths.map(p => p[yr] / defl(yr))) : (pkgMean ?? 0)),
    taxDeduction: c.tax_deduction || 0,
    replacement: rep,
    repCauses: causes,
    debtService: (c.debt_service || 0) * loanRatio,
    loanInterest: (c.loan_interest || 0) * loanRatio,
    equity: rowEq,
    cumEquity: cumEqEnv
   });
  }
 }

 const getY0 = (row, fallback) => {
  if (row && row.length > 0 && row[0] != null && !isNaN(row[0])) {
   return row[0];
  }
  return typeof fallback === 'number' ? fallback : (fallback?.median ?? 0);
 };

 const netCashSumPath = (paths, fallbackY0) => paths.map(row => {
  const y0 = getY0(row, fallbackY0);
  let sum = y0;
  for (let yr = 1; yr <= maxYear && yr < row.length; yr++) {
   sum += (row[yr] || 0) / defl(yr);
  }
  return sum;
 });

 const npvSumPath = (paths, fallbackY0) => paths.map(row => {
  const y0 = getY0(row, fallbackY0);
  let sum = y0;
  for (let yr = 1; yr <= maxYear && yr < row.length; yr++) {
   sum += ((row[yr] || 0) / defl(yr)) / disc(yr);
  }
  return sum;
 });

 const sumFromRows = (rowList, key, discounted) => {
  let min = 0, med = 0, mean = 0, max = 0;
  for (let yr = 0; yr <= maxYear && yr < rowList.length; yr++) {
   const cell = rowList[yr][key];
   const d = (discounted && yr > 0) ? disc(yr) : 1;
   if (cell != null) {
    if (typeof cell === 'number') {
     min += cell / d;
     med += cell / d;
     mean += cell / d;
     max += cell / d;
    } else {
     min += (cell.min ?? cell.mean ?? cell.median ?? 0) / d;
     med += (cell.median ?? 0) / d;
     mean += (cell.mean ?? cell.median ?? 0) / d;
     max += (cell.max ?? cell.mean ?? cell.median ?? 0) / d;
    }
   }
  }
  return { min, median: med, mean, max };
 };

 const pvNetCashRange = pvPaths.length ? computeEnvelope(netCashSumPath(pvPaths, rows[0]?.project)) : sumFromRows(rows, 'project', false);
 const pkgNetCashRange = pkgPaths.length ? computeEnvelope(netCashSumPath(pkgPaths, rows[0]?.wholeHouse)) : sumFromRows(rows, 'wholeHouse', false);
 const eqNetCashRange = eqPaths.length ? computeEnvelope(netCashSumPath(eqPaths, rows[0]?.equity)) : sumFromRows(rows, 'equity', false);

 const pvNpvRange = discPvPaths.length ? computeEnvelope(npvSumPath(discPvPaths, rows[0]?.project)) : sumFromRows(rows, 'project', true);
 const pkgNpvRange = discPkgPaths.length ? computeEnvelope(npvSumPath(discPkgPaths, rows[0]?.wholeHouse)) : sumFromRows(rows, 'wholeHouse', true);
 const eqNpvRange = discEqPaths.length ? computeEnvelope(npvSumPath(discEqPaths, rows[0]?.equity)) : sumFromRows(rows, 'equity', true);

 const finalCumEqRange = eqNetCashRange;

 const timingAdjustments = loanDebtTimingAdjustments(r);
 let totTax = 0, totRep = 0, totDebt = -loanDrawdown, totInt = 0;
 let pvTax = 0, pvRep = 0, pvDebt = -loanDrawdown, pvInt = 0;
 for (let i = 1; i < displayCash.length && displayCash[i].year <= maxYear; i++) {
  const c = displayCash[i];
  const yr = c.year;
  const d = yr > 0 ? disc(yr) : 1;
  const tax = c.tax_deduction || 0;
  const rep = (c.display_replacement ?? c.replacement ?? 0) + (c.hp_replacement ?? 0);
  const debt = (c.debt_service || 0) * loanRatio;
  const loanInt = (c.loan_interest || 0) * loanRatio;

  totTax += tax;
  totRep += rep;
  totDebt += debt;
  totInt += loanInt;

  pvTax += tax / d;
  pvRep += rep / d;
  const timingAdj = timingAdjustments[yr] || 0;
  const legacyEquivDebt = (c.debt_service || 0) + (isReal ? (timingAdj / defl(yr)) : timingAdj);
  const discDebt = (c.debt_service_year_end_equivalent ?? legacyEquivDebt) * loanRatio;
  pvDebt += discDebt / d;
  pvInt += loanInt / d;
 }

 const netCashTotals = {
  project: pvNetCashRange,
  wholeHouse: pkgNetCashRange,
  taxDeduction: totTax,
  replacement: totRep,
  debtService: totDebt,
  loanInterest: totInt,
  equity: eqNetCashRange,
  cumEquity: finalCumEqRange
 };

 const npvTotals = {
  project: pvNpvRange,
  wholeHouse: pkgNpvRange,
  taxDeduction: pvTax,
  replacement: pvRep,
  debtService: pvDebt,
  loanInterest: pvInt,
  equity: eqNpvRange,
  cumEquity: eqNpvRange
 };

 return { rows, totals: netCashTotals, netCashTotals, npvTotals, maxYear, rateLabel, activeScope, activeCash, flowKey };
}

function exportCashFlowCsv(r, displayCash) {
 const data = buildSummaryCashRows(r, displayCash);
 if (!data) return;
 const { rows, netCashTotals, npvTotals, maxYear, rateLabel } = data;
 const headers = [
  'Year',
  'Project CF (historical-weather ensemble mean EUR)',
  'Project CF min (EUR)',
  'Project CF max (EUR)',
  'Whole-house CF (historical-weather ensemble mean EUR)',
  'Whole-house CF min (EUR)',
  'Whole-house CF max (EUR)',
  'Tax deduction (EUR)',
  'Replacements (EUR)',
  'Loan repayment / drawdown (EUR)',
  'Interest (EUR)',
  'Equity CF after loan (historical-weather ensemble mean EUR)',
  'Equity CF min (EUR)',
  'Equity CF max (EUR)',
  'Cumulative equity CF (historical-weather ensemble mean EUR)',
  'Cumulative equity min (EUR)',
  'Cumulative equity max (EUR)'
 ];

 const getVals = obj => typeof obj === 'number' ? [obj, obj, obj] : (obj ? [obj.mean ?? obj.median, obj.min, obj.max] : [0, 0, 0]);
 const csvRows = rows.map(r => {
  const p = getVals(r.project);
  const w = getVals(r.wholeHouse);
  const eq = getVals(r.equity);
  const ce = getVals(r.cumEquity);
  return [
   r.year,
   p[0], p[1], p[2],
   w[0], w[1], w[2],
   r.taxDeduction,
   r.replacement ? -r.replacement : 0,
   r.debtService ? -r.debtService : 0,
   r.loanInterest ? -r.loanInterest : 0,
   eq[0], eq[1], eq[2],
   ce[0], ce[1], ce[2]
  ];
 });

 const ntp = getVals(netCashTotals.project);
 const ntw = getVals(netCashTotals.wholeHouse);
 const nteq = getVals(netCashTotals.equity);
 const ntce = getVals(netCashTotals.cumEquity);
 csvRows.push([
  `Net cash (Years 0-${maxYear}, undiscounted)`,
  ntp[0], ntp[1], ntp[2],
  ntw[0], ntw[1], ntw[2],
  netCashTotals.taxDeduction,
  netCashTotals.replacement ? -netCashTotals.replacement : 0,
  netCashTotals.debtService ? -netCashTotals.debtService : 0,
  netCashTotals.loanInterest ? -netCashTotals.loanInterest : 0,
  nteq[0], nteq[1], nteq[2],
  ntce[0], ntce[1], ntce[2]
 ]);

 const dtp = getVals(npvTotals.project);
 const dtw = getVals(npvTotals.wholeHouse);
 const dteq = getVals(npvTotals.equity);
 const dtce = getVals(npvTotals.cumEquity);
 csvRows.push([
  `Net Present Value (Years 0-${maxYear}, ${rateLabel})`,
  dtp[0], dtp[1], dtp[2],
  dtw[0], dtw[1], dtw[2],
  npvTotals.taxDeduction,
  npvTotals.replacement ? -npvTotals.replacement : 0,
  npvTotals.debtService ? -npvTotals.debtService : 0,
  npvTotals.loanInterest ? -npvTotals.loanInterest : 0,
  dteq[0], dteq[1], dteq[2],
  dtce[0], dtce[1], dtce[2]
 ]);

 const safeCell=value=>{const text=String(value??'');return /^[\s]*[=+@-]|^[\t\r\n]/.test(text)?"'"+text:text;};
 const csvContent = [headers.join(','), ...csvRows.map(r => r.map(v => typeof v === 'number' ? Math.round(v) : `"${safeCell(v).replaceAll('"','""')}"`).join(','))].join(String.fromCharCode(10));
 if (typeof download === 'function') {
  download(csvContent, 'pv-lab-annual-cashflows.csv', 'text/csv');
 }
}

function renderSummaryCashTable(r, displayCash) {
 if (!r || !displayCash || !displayCash.length) return;
 currentSummaryData = { r, displayCash };

 const details = $('cashFlowDetails');
 const summary = $('cashFlowSummary');
 const summaryTitle=summary?.querySelector?.('.cash-summary-title');
 const nYears = r.weather_years?.length || r.weather_count || current?.result?.weather_years?.length || current?.result?.weather_count || r.finance?.cashflows_by_weather?.pv_incremental?.length || 0;
 const getModeCaption = (mode) => mode === 'range'
  ? `Min–max range across ${nYears} weather years`
  : `Historical-weather ensemble mean of ${nYears} weather years`;
 const scenarioKey = typeof inputFingerprint === 'function'
  ? inputFingerprint(r.settings, quote)
  : JSON.stringify([r.settings?.load_scenario, r.layout?.kwp, r.settings?.battery_kwh, r.settings?.cashflow_mode, r.settings?.inflation]);

 const isScenarioChange = lastSummaryScenarioFingerprint === null || lastSummaryScenarioFingerprint !== scenarioKey;
 lastSummaryScenarioFingerprint = scenarioKey;

 if (details) {
  if (isScenarioChange) {
   try { sessionStorage.removeItem('summary_cash_disclosure_open'); } catch {}
   details.open = false;
  } else {
   let isOpen = false;
   try { isOpen = sessionStorage.getItem('summary_cash_disclosure_open') === '1'; } catch {}
   details.open = isOpen;
  }
  if (summary) summary.setAttribute('aria-expanded', details.open ? 'true' : 'false');
  if (!details._cashBound) {
   details._cashBound = true;
   details.addEventListener('toggle', () => {
    if (summary) summary.setAttribute('aria-expanded', details.open ? 'true' : 'false');
    try {
     sessionStorage.setItem('summary_cash_disclosure_open', details.open ? '1' : '0');
    } catch {}
   });
  }
 }

 const exportBtn = $('exportCashCsvBtn');
 if (exportBtn && !exportBtn._cashBound) {
  exportBtn._cashBound = true;
  exportBtn.addEventListener('click', (e) => {
   e?.stopPropagation?.();
   if (currentSummaryData) exportCashFlowCsv(currentSummaryData.r, currentSummaryData.displayCash);
  });
 }

 const meanBtn = $('cashTableMeanBtn');
 const rangeBtn = $('cashTableRangeBtn');
 const updateModeButtons = (mode) => {
  cashTableMode = mode;
  if (meanBtn) meanBtn.classList.toggle('active', mode === 'mean');
  if (rangeBtn) rangeBtn.classList.toggle('active', mode === 'range');
  const ct = $('cashTable');
  if (ct) ct.setAttribute('data-mode', mode);
  const tbl = ct?.querySelector?.('.cash-table');
  if (tbl) tbl.setAttribute('data-mode', mode);
  const proxyWrap = $('cashTableStickyHeader');
  if (proxyWrap) {
   proxyWrap.setAttribute('data-mode', mode);
   const ptbl = proxyWrap.querySelector?.('.cash-table');
   if (ptbl) ptbl.setAttribute('data-mode', mode);
  }
  const cap = getModeCaption(mode);
  if (summaryTitle) summaryTitle.textContent = `Annual cash flows & discounting method · ${displayCash.length} rows · ${cap}`;
  const modeCaption = $('cashTableModeCaption');
  if (modeCaption) modeCaption.textContent = cap;
 };

 if (meanBtn && rangeBtn && !meanBtn._cashBound) {
  meanBtn._cashBound = true;
  rangeBtn._cashBound = true;
  meanBtn.addEventListener('click', () => updateModeButtons('mean'));
  rangeBtn.addEventListener('click', () => updateModeButtons('range'));
 }
 updateModeButtons(cashTableMode);

 const data = buildSummaryCashRows(r, displayCash);
 if (!data) return;
 const { rows, netCashTotals, npvTotals, maxYear, rateLabel, activeScope } = data;

 const formatCashVal = (v) => {
  if (v == null || !Number.isFinite(v)) return '—';
  if (Math.abs(v) < 0.5) return '€0';
  if (v < 0) return '−€' + nf(Math.abs(v));
  return '€' + nf(v);
 };

 const formatFinancingVal = (v) => {
  if (v == null || !Number.isFinite(v) || Math.abs(v) < 0.5) return '—';
  if (v < 0) return '+€' + nf(Math.abs(v));
  return '−€' + nf(v);
 };

 const formatCell = (rangeObj) => {
  if (rangeObj == null) return '—';
  if (typeof rangeObj === 'number') {
   return `<span class="cash-val">${formatCashVal(rangeObj)}</span>`;
  }
  const medStr = formatCashVal(rangeObj.mean ?? rangeObj.median);
  const minStr = formatCashVal(rangeObj.min);
  const maxStr = formatCashVal(rangeObj.max);
  return `<span class="cash-val">${medStr}</span><small class="muted cash-minmax">${minStr} … ${maxStr}</small>`;
 };

 const isSolarScope = activeScope === 'solar';
 const eqTitle = isSolarScope ? 'Equity CF = project CF + loan CF' : 'Equity CF = whole-house CF + loan CF';
 const eqFormula = isSolarScope ? 'PROJECT CF + LOAN CF' : 'WHOLE-HOUSE CF + LOAN CF';

 const theadHtml = `<thead>
  <tr>
   <th rowspan="2" class="sticky-col">Year</th>
   <th colspan="1" class="group-header">Solar + battery project</th>
   <th colspan="3" class="group-header">Whole house</th>
   <th colspan="4" class="group-header">Financing</th>
  </tr>
  <tr>
   <th title="Project cash flow">Project<br>CF</th>
   <th title="Whole-house cash flow">Whole-house<br>CF</th>
   <th class="cash-secondary-col" title="Tax deduction (already included in project and whole-house CF)"><span class="muted cash-col-sub">(incl.) </span>Tax<br>deduction</th>
   <th class="cash-secondary-col" title="Replacements (already included in CF)"><span class="muted cash-col-sub">(incl.) </span>Replacements</th>
   <th title="Loan repayment & Year 0 drawdown (net financing cash flow)">Loan repayment<br>/ drawdown</th>
   <th class="cash-secondary-col" title="Interest (already included in loan repayment)"><span class="muted cash-col-sub">(incl.) </span>Interest</th>
   <th title="${eqTitle}">Equity CF<br>after loan<small class="muted cash-col-formula">${eqFormula}</small></th>
   <th title="Cumulative equity CF">Cumulative<br>equity CF</th>
  </tr>
 </thead>`;

 const tbodyRows = rows.map(row => {
  const isY0 = row.year === 0;
  const isRep = row.replacement > 0;
  const rowClass = isY0 ? 'year-0-row' : (isRep ? 'has-replacement' : '');
  const rowTitle = row.repCauses.length ? ` title="${esc(row.repCauses.join(' + '))}"` : '';

  const taxCell = row.taxDeduction > 0.5 ? formatCashVal(row.taxDeduction) : '—';
  const repCell = row.replacement > 0.5
   ? `<span class="replacement-highlight" title="${esc(row.repCauses.join(' + '))}">${formatCashVal(-row.replacement)}</span>`
   : '—';
  const debtCell = formatFinancingVal(row.debtService);
  const intCell = row.loanInterest > 0.5 ? formatCashVal(-row.loanInterest) : '—';

  return `<tr class="${rowClass}"${rowTitle}>
   <td class="sticky-col">${row.year}</td>
   <td>${formatCell(row.project)}</td>
   <td>${formatCell(row.wholeHouse)}</td>
   <td class="cash-secondary-col muted">${taxCell}</td>
   <td class="cash-secondary-col muted">${repCell}</td>
   <td>${debtCell}</td>
   <td class="cash-secondary-col muted">${intCell}</td>
   <td>${formatCell(row.equity)}</td>
   <td>${formatCell(row.cumEquity)}</td>
  </tr>`;
 });

 const tfootHtml = `<tfoot>
  <tr class="totals-row net-cash-row">
   <td class="sticky-col">Net cash (Years 0–${maxYear}, undiscounted)</td>
   <td>${formatCell(netCashTotals.project)}</td>
   <td>${formatCell(netCashTotals.wholeHouse)}</td>
   <td class="cash-secondary-col muted">${netCashTotals.taxDeduction > 0.5 ? formatCashVal(netCashTotals.taxDeduction) : '—'}</td>
   <td class="cash-secondary-col muted">${netCashTotals.replacement > 0.5 ? formatCashVal(-netCashTotals.replacement) : '—'}</td>
   <td>${formatFinancingVal(netCashTotals.debtService)}</td>
   <td class="cash-secondary-col muted">${netCashTotals.loanInterest > 0.5 ? formatCashVal(-netCashTotals.loanInterest) : '—'}</td>
   <td>${formatCell(netCashTotals.equity)}</td>
   <td>${formatCell(netCashTotals.cumEquity)}</td>
  </tr>
  <tr class="totals-row npv-row">
   <td class="sticky-col">Net Present Value (Years 0–${maxYear}, ${rateLabel})</td>
   <td>${formatCell(npvTotals.project)}</td>
   <td>${formatCell(npvTotals.wholeHouse)}</td>
   <td class="cash-secondary-col muted">${npvTotals.taxDeduction > 0.5 ? formatCashVal(npvTotals.taxDeduction) : '—'}</td>
   <td class="cash-secondary-col muted">${npvTotals.replacement > 0.5 ? formatCashVal(-npvTotals.replacement) : '—'}</td>
   <td>${formatFinancingVal(npvTotals.debtService)}</td>
   <td class="cash-secondary-col muted">${npvTotals.loanInterest > 0.5 ? formatCashVal(-npvTotals.loanInterest) : '—'}</td>
   <td>${formatCell(npvTotals.equity)}</td>
   <td>${formatCell(npvTotals.cumEquity)}</td>
  </tr>
 </tfoot>`;

 const tableHtml = `<table class="cash-table" data-mode="${cashTableMode}">${theadHtml}<tbody>${tbodyRows.join('')}</tbody>${tfootHtml}</table>`;
 const container = $('cashTable');
 if (typeof cashStickyCleanup === 'function') {
  try { cashStickyCleanup(); } catch {}
  cashStickyCleanup = null;
 }
 if (container) {
  container.innerHTML = tableHtml;
  container.setAttribute('data-mode', cashTableMode);

  let proxyWrap = $('cashTableStickyHeader');
  if (!proxyWrap && typeof document !== 'undefined' && document.createElement && container.parentNode) {
   proxyWrap = document.createElement('div');
   proxyWrap.id = 'cashTableStickyHeader';
   proxyWrap.className = 'cash-sticky-header-wrap';
   proxyWrap.setAttribute('aria-hidden', 'true');
   container.parentNode.insertBefore(proxyWrap, container);
  }

  if (proxyWrap) {
   proxyWrap.innerHTML = `<table class="cash-table cash-proxy-table" data-mode="${cashTableMode}" aria-hidden="true">${theadHtml}</table>`;
   proxyWrap.setAttribute('data-mode', cashTableMode);
   proxyWrap.classList.remove('is-sticky');

   const realTable = container.querySelector?.('table');
   const proxyTable = proxyWrap.querySelector?.('table');

   const syncWidthsAndScroll = () => {
    if (!realTable || !proxyTable) return;
    const realYearTh = realTable.querySelector?.('thead tr:first-child th.sticky-col') || realTable.querySelector?.('tbody tr td:first-child');
    const proxyYearTh = proxyTable.querySelector?.('thead tr:first-child th.sticky-col');
    if (realYearTh && proxyYearTh && realYearTh.getBoundingClientRect) {
     const yw = realYearTh.getBoundingClientRect().width;
     if (yw > 0) {
      proxyYearTh.style.width = yw + 'px';
      proxyYearTh.style.minWidth = yw + 'px';
      proxyYearTh.style.maxWidth = yw + 'px';
     }
    }
    const row1 = realTable.querySelector?.('thead tr:first-child');
    if (row1 && row1.getBoundingClientRect) {
     const h1 = row1.getBoundingClientRect().height;
     if (h1 > 0) {
      container.style.setProperty('--thead-row1-h', h1 + 'px');
      if (proxyWrap) proxyWrap.style.setProperty('--thead-row1-h', h1 + 'px');
     }
    }
    const realGroupThs = realTable.querySelectorAll?.('thead tr:first-child th.group-header') || [];
    const proxyGroupThs = proxyTable.querySelectorAll?.('thead tr:first-child th.group-header') || [];
    realGroupThs.forEach?.((th, i) => {
     if (proxyGroupThs[i] && th.getBoundingClientRect) {
      const rect = th.getBoundingClientRect();
      if (rect.width > 0) {
       proxyGroupThs[i].style.width = rect.width + 'px';
       proxyGroupThs[i].style.minWidth = rect.width + 'px';
       proxyGroupThs[i].style.maxWidth = rect.width + 'px';
      }
      if (rect.height > 0) {
       proxyGroupThs[i].style.height = rect.height + 'px';
      }
     }
    });
    const realThs = realTable.querySelectorAll?.('thead tr:nth-child(2) th') || [];
    const proxyThs = proxyTable.querySelectorAll?.('thead tr:nth-child(2) th') || [];
    realThs.forEach?.((th, i) => {
     if (proxyThs[i] && th.getBoundingClientRect) {
      const rect = th.getBoundingClientRect();
      if (rect.width > 0) {
       proxyThs[i].style.width = rect.width + 'px';
       proxyThs[i].style.minWidth = rect.width + 'px';
       proxyThs[i].style.maxWidth = rect.width + 'px';
      }
      if (rect.height > 0) {
       proxyThs[i].style.height = rect.height + 'px';
      }
     }
    });
    if (realTable.getBoundingClientRect) {
     const tw = realTable.getBoundingClientRect().width;
     if (tw > 0) {
      proxyTable.style.width = tw + 'px';
      proxyTable.style.minWidth = tw + 'px';
     }
    }
    proxyWrap.scrollLeft = container.scrollLeft;
   };

   const getStickyTop = () => {
    let top = 0;
    if (typeof document !== 'undefined' && document.querySelectorAll) {
     document.querySelectorAll('header, nav, .sticky-nav').forEach(el => {
      if (typeof window !== 'undefined' && window.getComputedStyle) {
       const cs = window.getComputedStyle(el);
       if ((cs.position === 'sticky' || cs.position === 'fixed') && el.getBoundingClientRect) {
        top = Math.max(top, el.getBoundingClientRect().bottom);
       }
      }
     });
     if (top === 0 && typeof window !== 'undefined' && window.getComputedStyle && document.documentElement) {
      const pad = parseInt(window.getComputedStyle(document.documentElement).scrollPaddingTop, 10);
      if (Number.isFinite(pad) && pad > 0) top = pad;
     }
    }
    return top;
   };

   const updateStickyState = () => {
    if (!realTable?.getBoundingClientRect || !proxyWrap.getBoundingClientRect || !container.getBoundingClientRect) return;
    const detailsEl = $('cashFlowDetails');
    if (detailsEl && !detailsEl.open) {
     proxyWrap.classList.remove('is-sticky');
     return;
    }
    const containerRect = container.getBoundingClientRect();
    const topOffset = getStickyTop();
    const realRect = realTable.getBoundingClientRect();
    const thead = realTable.querySelector?.('thead');
    const theadHeight = thead?.getBoundingClientRect?.()?.height || 60;
    const isSticky = realRect.top <= topOffset && realRect.bottom > (topOffset + theadHeight);

    if (isSticky) {
     proxyWrap.style.top = topOffset + 'px';
     proxyWrap.style.left = containerRect.left + 'px';
     proxyWrap.style.width = containerRect.width + 'px';
     if (!proxyWrap.classList.contains('is-sticky')) {
      proxyWrap.classList.add('is-sticky');
     }
     syncWidthsAndScroll();
    } else {
     proxyWrap.classList.remove('is-sticky');
    }
   };

   const updateScrollState = () => {
    if (!container) return;
    const isScrollable = container.scrollWidth > container.clientWidth + 1;
    container.classList.toggle('has-scroll', isScrollable);
    if (proxyWrap) proxyWrap.classList.toggle('has-scroll', isScrollable);
   };
   const updateSummaryHeight = () => {
    if (!summary || !details) return;
    const h = summary.offsetHeight;
    if (h > 0) details.parentElement?.style.setProperty('--cash-summary-h', h + 'px');
   };
   updateScrollState();
   updateSummaryHeight();

   const onContainerScroll = () => { proxyWrap.scrollLeft = container.scrollLeft; };
   const onWindowScroll = () => { updateStickyState(); };
   const onWindowResize = () => { updateScrollState(); updateSummaryHeight(); syncWidthsAndScroll(); updateStickyState(); };
   const onDetailsToggle = () => {
    setTimeout(() => {
     updateScrollState();
     updateSummaryHeight();
     syncWidthsAndScroll();
     updateStickyState();
    }, 0);
   };

   container.addEventListener?.('scroll', onContainerScroll);

   let ro = null;
   if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
     updateScrollState();
     updateSummaryHeight();
     syncWidthsAndScroll();
     updateStickyState();
    });
    try { ro.observe(container); } catch {}
   }

   if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('scroll', onWindowScroll, { passive: true });
    window.addEventListener('resize', onWindowResize);
   }

   const detailsEl = $('cashFlowDetails');
   if (detailsEl) {
    detailsEl.addEventListener?.('toggle', onDetailsToggle);
   }

   cashStickyCleanup = () => {
    container.removeEventListener?.('scroll', onContainerScroll);
    if (typeof window !== 'undefined' && window.removeEventListener) {
     window.removeEventListener('scroll', onWindowScroll);
     window.removeEventListener('resize', onWindowResize);
    }
    if (detailsEl) {
     detailsEl.removeEventListener?.('toggle', onDetailsToggle);
    }
    if (ro) {
     try { ro.disconnect(); } catch {}
    }
    if (proxyWrap && proxyWrap.parentNode) {
     try { proxyWrap.parentNode.removeChild(proxyWrap); } catch {}
    }
   };

   syncWidthsAndScroll();
   updateStickyState();
  }
 }
}

function financedPaybackCard(result){
 if(!(result.finance.loan_principal_eur>0))return '';
 const finance=result.finance;
 const modeledInitial=finance.cashflows_by_weather?.financed_project?.[0]?.[0],fallbackInitial=(finance.capex_eur||0)+(finance.loan_scope==='solar_and_conversion'?(finance.conversion_capex_eur||0):0),initialCost=modeledInitial==null?fallbackInitial:Math.abs(modeledInitial);
 const detail=`Full purchase cost ${fmt(initialCost,'eur')} counted at year zero; yearly cash subtracts loan interest only, with principal counted upfront. ${finance.loan_scope==='solar_and_conversion'?'Solar + electrification':'Solar'} loan ${fmt(finance.loan_principal_eur,'eur')}${finance.requested_loan_amount_eur>finance.loan_principal_eur?` (requested ${fmt(finance.requested_loan_amount_eur,'eur')}; capped at eligible capex)`:''} · ${finance.financed_project.unpaid_discounted_weather_years} weather cases do not recover within the horizon`;
 const basePb=finance.financed_project?.discounted_payback;
 const discOfMean=finance.financed_project?.discounted_payback_of_mean_cash_flows!==undefined
  ? finance.financed_project.discounted_payback_of_mean_cash_flows
  : basePb?.discounted_payback_of_mean_cash_flows;
 const pbObj=basePb
  ? (typeof basePb==='object'?{...basePb,discounted_payback_of_mean_cash_flows:discOfMean}:basePb)
  : discOfMean;
 return metricCard('DISCOUNTED FULL-COST RECOVERY + INTEREST',pbObj,'years',`Payback of ensemble-mean cash flows. ${detail}`);
}
function personalPaybackCard(result){
 const finance=result.finance;if(!(finance.loan_principal_eur>0))return '';
 const initial=Math.max(0,-(finance.cashflows_by_weather?.equity?.[0]?.[0]??0));
 const basePb=finance.equity?.discounted_payback;
 const discOfMean=finance.equity?.discounted_payback_of_mean_cash_flows!==undefined
  ? finance.equity.discounted_payback_of_mean_cash_flows
  : (basePb?.discounted_payback_of_mean_cash_flows!==undefined
   ? basePb.discounted_payback_of_mean_cash_flows
   : (finance.loan_scope==='solar_and_conversion'
    ? finance.package_equity?.discounted_payback_of_mean_cash_flows
    : finance.solar_equity?.discounted_payback_of_mean_cash_flows));
 const pbObj=basePb
  ? (typeof basePb==='object'?{...basePb,discounted_payback_of_mean_cash_flows:discOfMean}:basePb)
  : discOfMean;
 const detail=`Your upfront cash after ${fmt(finance.loan_principal_eur,'eur')} of loan proceeds: ${fmt(initial,'eur')}. Annual cash subtracts full loan repayments, principal + interest; ${basePb?.recovered_weather_years??finance.equity?.recovered_weather_years??0} weather paths recover persistently.`;
 return metricCard('DISCOUNTED PERSONAL CASH RECOVERY',pbObj,'years',`Payback of ensemble-mean cash flows. ${detail}`);
}
function calculateItemizedTotal(q){
 const items=q?.line_items;
 if(!Array.isArray(items)||!items.length)return null;
 let total=0, populated=0;
 for(const line of items){
  if(!line||typeof line!=='object')continue;
  if(line.net_eur==null||line.net_eur==='')continue;
  const net=Number(line.net_eur);
  const vat=Number(line.vat??0.1);
  const qty=Number(line.quantity??1);
  if(!Number.isFinite(net)||!Number.isFinite(vat)||!Number.isFinite(qty))continue;
  if(net<0||qty<0||vat<0||vat>1)continue;
  total+=qty*net*(1+vat);
  populated++;
 }
 return populated>0?total:null;
}
function hasPopulatedItemizedOffer(q, itemizedTotal=current?.itemized_total_eur){
 if(itemizedTotal!=null&&Number.isFinite(Number(itemizedTotal)))return true;
 return calculateItemizedTotal(q)!=null;
}
function isItemizedPricingActive(qTarget=quote, itemizedTotal=current?.itemized_total_eur){
 const total=itemizedTotal??calculateItemizedTotal(qTarget||quote);
 return (qTarget||quote).price_basis!=='modeled'&&(qTarget||quote).use_itemized!==false&&total!=null;
}
function getCanonicalEnsembleFlows(r = (typeof current !== 'undefined' ? current?.result : null)) {
  return r?.headline_energy_flows ?? null;
}

function getScopeCashMetrics(r,scope=summaryScope,cashMode=summaryCash){
 if(!r)return null;
 const f=r.finance||{},m=r.metrics||{},isReal=r.settings?.cashflow_mode==='real';
 const defl=isReal?(1+(r.settings?.inflation||0)):1;
 const isSolar=scope==='solar';
 const isCash=cashMode==='cash';

 let capexTitle='',capexVal=0,capexExtra='';
 let npvTitle='',npvObj=null,npvExtra='';
 let pbTitle='',pbObj=null,pbExtra='';
 let flowKey='';

 if(isSolar){
  if(isCash){
   flowKey='pv_incremental';
   capexTitle='TOTAL INVESTMENT (SOLAR + STORAGE)';
   capexVal=f.capex_eur;
   capexExtra=isItemizedPricingActive()?'Itemized quote capex':(settings.quote_total!=null?'Manual quote capex':'Modeled benchmark capex');
   npvTitle='PV + STORAGE INCREMENTAL NPV';
   npvObj=m.npv_eur;
   npvExtra=`Against same load without solar · ${fmt(isReal?f.real_discount_rate:f.nominal_discount_rate,'pct')} discount`;
   pbTitle='DISCOUNTED SOLAR PAYBACK';
   const discOfMean = f.pv_incremental?.discounted_payback_of_mean_cash_flows !== undefined ? f.pv_incremental.discounted_payback_of_mean_cash_flows : m.discounted_payback_of_mean_cash_flows;
   const simpOfMean = f.pv_incremental?.simple_payback_of_mean_cash_flows !== undefined ? f.pv_incremental.simple_payback_of_mean_cash_flows : m.simple_payback_of_mean_cash_flows;
   const basePb = f.pv_incremental?.discounted_payback ?? m.discounted_payback_years;
   pbObj = basePb ? (typeof basePb === 'object' ? { ...basePb, discounted_payback_of_mean_cash_flows: discOfMean, simple_payback_of_mean_cash_flows: simpOfMean } : basePb) : discOfMean;
   const simpDetail = simpOfMean === null ? 'Not reached' : (simpOfMean != null ? `${fmt(simpOfMean, 'years')} (ensemble-mean CF)` : range(m.payback_years ?? f.pv_incremental?.simple_payback, 'years'));
   pbExtra=`Payback of ensemble-mean cash flows · Simple: ${simpDetail} · before financing`;
  }else{
   flowKey='solar_equity';
   const solarLoanPrincipal=f.solar_loan_principal_eur??Math.min(f.capex_eur,f.loan_principal_eur||0);
   const init=Math.max(0,-(f.cashflows_by_weather?.solar_equity?.[0]?.[0]??(f.capex_eur-solarLoanPrincipal)));
   capexTitle='PERSONAL CASH OUTLAY (SOLAR ONLY · AFTER LOAN)';
   capexVal=init;
   capexExtra=`Total cost: ${fmt(f.capex_eur,'eur')} · Loan: ${fmt(solarLoanPrincipal,'eur')}`;
   npvTitle='PERSONAL EQUITY NPV (SOLAR)';
   npvObj=f.solar_equity?.npv??f.solar_equity?.npv_eur??m.npv_eur;
   npvExtra='After proportional solar loan debt service repayments';
   pbTitle='DISCOUNTED CASH RECOVERY (SOLAR)';
   const discOfMean = f.solar_equity?.discounted_payback_of_mean_cash_flows;
   const simpOfMean = f.solar_equity?.simple_payback_of_mean_cash_flows;
   const basePb = f.solar_equity?.discounted_payback??f.equity?.discounted_payback;
   pbObj = basePb ? (typeof basePb === 'object' ? { ...basePb, discounted_payback_of_mean_cash_flows: discOfMean, simple_payback_of_mean_cash_flows: simpOfMean } : basePb) : discOfMean;
   pbExtra='Payback of ensemble-mean cash flows · after allocated solar loan repayments';
  }
 }else{
  const totalOutlay=(f.capex_eur||0)+(f.conversion_capex_eur||0);
  if(isCash){
   flowKey='whole_package';
   capexTitle='TOTAL INVESTMENT (WHOLE PROJECT)';
   capexVal=totalOutlay;
   capexExtra=`Includes ${fmt(f.capex_eur,'eur')} solar + ${fmt(f.conversion_capex_eur,'eur')} electrification`;
   npvTitle='WHOLE-HOUSE PACKAGE NPV';
   npvObj=m.package_npv_eur??f.whole_package?.npv;
   npvExtra='Against historic electricity & gas bills';
   pbTitle='DISCOUNTED PACKAGE PAYBACK';
   const discOfMean = f.whole_package?.discounted_payback_of_mean_cash_flows;
   const simpOfMean = f.whole_package?.simple_payback_of_mean_cash_flows;
   const basePb = f.whole_package?.discounted_payback;
   pbObj = basePb ? (typeof basePb === 'object' ? { ...basePb, discounted_payback_of_mean_cash_flows: discOfMean, simple_payback_of_mean_cash_flows: simpOfMean } : basePb) : discOfMean;
   const simpDetail = simpOfMean === null ? 'Not reached' : (simpOfMean != null ? `${fmt(simpOfMean, 'years')} (ensemble-mean CF)` : range(f.whole_package?.simple_payback, 'years'));
   pbExtra=`Payback of ensemble-mean cash flows · Simple: ${simpDetail}`;
  }else{
   flowKey='package_equity';
   const init=Math.max(0,-(f.cashflows_by_weather?.package_equity?.[0]?.[0]??(totalOutlay-(f.loan_principal_eur||0))));
   capexTitle='PERSONAL CASH OUTLAY (AFTER LOAN)';
   capexVal=init;
   capexExtra=(f.conversion_capex_eur>0&&f.loan_principal_eur>0)
    ? `Total CAPEX: ${fmt(totalOutlay,'eur')} (${fmt(f.capex_eur||0,'eur')} solar+storage + ${fmt(f.conversion_capex_eur,'eur')} electrification) · Loan: ${fmt(f.loan_principal_eur,'eur')}`
    : `Total cost: ${fmt(totalOutlay,'eur')} · Loan: ${fmt(f.loan_principal_eur||0,'eur')}`;
   const traces=f.cashflows_by_weather?.package_equity;
   let undiscountedText=null;
   if(Array.isArray(traces)&&traces.length>0&&Array.isArray(traces[0])&&traces[0].length>1){
    const horizon=r.settings?.horizon_years||(traces[0].length>2?traces[0].length-1:20);
    const inflation=r.settings?.inflation||0;
    const deflator=year=>isReal?Math.pow(1+inflation,year):1;
    const traceTotals=traces.map(row=>{
     let sum=0;
     const len=Math.min(row.length,horizon+1);
     for(let y=0;y<len;y++){
      sum+=(row[y]||0)/deflator(y);
     }
     return sum;
    });
    const meanNetCash=traceTotals.reduce((sum,value)=>sum+value,0)/traceTotals.length;
    const rounded=Math.round(meanNetCash);
    const absFormatted=fmt(Math.abs(rounded),'eur');
    const signed=rounded>=0?`+${absFormatted}`:`−${absFormatted}`;
    undiscountedText=`Undiscounted ${horizon}-y net cash: ${signed}`;
   }
   npvTitle='WHOLE-HOUSE EQUITY NPV';
   npvObj=f.package_equity?.npv??f.package_equity?.npv_eur??m.package_npv_eur;
   npvExtra=undiscountedText
    ? `<span class="metric-def-line">Versus historic bills after loan debt service</span> <span class="metric-def-line muted">${undiscountedText}</span>`
    : 'Versus historic bills after loan debt service';
   pbTitle='DISCOUNTED CASH RECOVERY (WHOLE PROJECT)';
   const discOfMean = f.package_equity?.discounted_payback_of_mean_cash_flows;
   const simpOfMean = f.package_equity?.simple_payback_of_mean_cash_flows;
   const basePb = f.package_equity?.discounted_payback??f.whole_package?.discounted_payback;
   pbObj = basePb ? (typeof basePb === 'object' ? { ...basePb, discounted_payback_of_mean_cash_flows: discOfMean, simple_payback_of_mean_cash_flows: simpOfMean } : basePb) : discOfMean;
   pbExtra='Payback of ensemble-mean cash flows · after whole-project loan repayments';
  }
 }

 const y1=r.annual_cashflows?.[1]||{};
 const explicitMean=isReal
  ?(y1['display_'+flowKey+'_mean']??(y1[flowKey+'_mean']!=null?y1[flowKey+'_mean']/defl:null))
  :y1[flowKey+'_mean'];
 let y1Net=null;
 if(explicitMean!=null){
  y1Net=explicitMean;
 }else if(f.cashflows_by_weather?.[flowKey]?.length){
  const traces=f.cashflows_by_weather[flowKey];
  const sum=traces.reduce((acc,row)=>acc+((row[1]||0)/defl),0);
  y1Net=sum/traces.length;
 }else{
  if(isSolar){
   let net=(y1.avoided_import??0)+(y1.net_export??0)+(y1.cer??0)+(y1.tax_deduction??0)-(y1.maintenance??0)-(y1.replacement??0)+(y1.residual??0);
   if(!isCash){
    const solarLoanPrincipal=f.solar_loan_principal_eur??Math.min(f.capex_eur,f.loan_principal_eur||0);
    const ratio=(f.loan_principal_eur>0)?(solarLoanPrincipal/f.loan_principal_eur):1.0;
    net-=(y1.debt_service??0)*ratio;
   }
   y1Net=net/defl;
  }else{
   const baseBill=y1.baseline_package_bill??((y1.baseline_electric_bill??0)+(y1.baseline_gas_bill??0));
   const sysElec=y1.with_system_electric_bill??0;
   const sysGas=y1.remaining_gas_bill??0;
   let net=(baseBill-sysElec-sysGas)+(y1.net_export??0)+(y1.cer??0)+(y1.tax_deduction??0)-(y1.maintenance??0)-(y1.replacement??0)+(y1.residual??0);
   if(!isCash)net-=(y1.debt_service??0);
   y1Net=net/defl;
  }
 }

 const flows = (typeof getCanonicalEnsembleFlows === 'function' ? getCanonicalEnsembleFlows(r) : null) ?? r?.headline_energy_flows ?? null;
 const autRange = flows?.flow_ranges?.autarky;
 const rawAutarky = r.energy?.autarky ?? m?.autarky;
 let autarkyMetric;
 if (flows?.autarky != null) {
  const base = typeof rawAutarky === 'object' && rawAutarky !== null
   ? { ...rawAutarky }
   : (rawAutarky != null ? { min: rawAutarky, median: rawAutarky, max: rawAutarky } : {});
  autarkyMetric = {
   ...base,
   ensembleAutarky: flows.autarky,
   headline: flows.autarky
  };
  if (autRange?.p10 != null && autRange?.p90 != null) {
   autarkyMetric.p10 = autRange.p10;
   autarkyMetric.p90 = autRange.p90;
   autarkyMetric.bounds_label = 'P10–P90';
  } else if (base.min != null && base.max != null) {
   autarkyMetric.bounds_label = 'min–max';
  }
 } else if (rawAutarky != null) {
  autarkyMetric = typeof rawAutarky === 'object'
   ? { ...rawAutarky, ensembleAutarky: r.energy?.ensemble_autarky ?? rawAutarky.mean, headline: r.energy?.ensemble_autarky ?? rawAutarky.mean, bounds_label: (rawAutarky.min != null && rawAutarky.max != null ? 'min–max' : undefined) }
   : { min: rawAutarky, median: rawAutarky, max: rawAutarky, ensembleAutarky: r.energy?.ensemble_autarky ?? rawAutarky, headline: r.energy?.ensemble_autarky ?? rawAutarky, bounds_label: 'min–max' };
 } else if (r.energy?.ensemble_autarky != null) {
  autarkyMetric = { ensembleAutarky: r.energy.ensemble_autarky, headline: r.energy.ensemble_autarky };
 } else {
  autarkyMetric = null;
 }
 const rawSelfCons = r.energy?.self_consumption ?? m?.self_consumption;
 const flowsSelfCons = flows?.self_consumption != null
   ? flows.self_consumption
   : (flows?.pv_generation_kwh > 0 && flows?.direct_use_kwh != null && flows?.battery_charge_kwh != null
       ? (flows.direct_use_kwh + flows.battery_charge_kwh) / flows.pv_generation_kwh
       : (flows?.pv_generation_kwh > 0 && flows?.grid_export_kwh != null
           ? (flows.pv_generation_kwh - flows.grid_export_kwh) / flows.pv_generation_kwh
           : null));
 const selfConsMetric = flowsSelfCons != null
   ? (typeof rawSelfCons === 'object' && rawSelfCons !== null
       ? { ...rawSelfCons, headline: flowsSelfCons, mean: flowsSelfCons }
       : { min: flowsSelfCons, median: flowsSelfCons, max: flowsSelfCons, headline: flowsSelfCons, mean: flowsSelfCons })
   : (rawSelfCons != null
       ? (typeof rawSelfCons === 'object'
           ? { ...rawSelfCons, headline: rawSelfCons.mean !== undefined ? rawSelfCons.mean : rawSelfCons.median }
           : { min: rawSelfCons, median: rawSelfCons, max: rawSelfCons, headline: rawSelfCons })
       : null);
 const demandMetric = flows?.load_demand_kwh ?? (r.energy?.load_kwh?.mean ?? r.energy?.load_kwh ?? m?.load_kwh);
 const pvMetric = flows?.pv_generation_kwh ?? (r.energy?.pv_kwh?.mean ?? r.energy?.pv_kwh ?? m?.pv_kwh);
 return {flowKey,capexTitle,capexVal,capexExtra,npvTitle,npvObj,npvExtra,pbTitle,pbObj,pbExtra,y1Net,autarky:autarkyMetric,selfConsumption:selfConsMetric,demandKwh:demandMetric,pvKwh:pvMetric};
}
function syncSummaryControls(){
 const isSolar=summaryScope==='solar';
 const isCash=summaryCash==='cash';
 const scopeSolar=$('scopeSolarBtn');
 if(scopeSolar){
  scopeSolar.classList?.toggle?.('active',isSolar);
  scopeSolar.setAttribute?.('aria-pressed',isSolar?'true':'false');
 }
 const scopePackage=$('scopePackageBtn');
 if(scopePackage){
  scopePackage.classList?.toggle?.('active',!isSolar);
  scopePackage.setAttribute?.('aria-pressed',!isSolar?'true':'false');
 }
 const cashBefore=$('cashBeforeBtn');
 if(cashBefore){
  cashBefore.classList?.toggle?.('active',isCash);
  cashBefore.setAttribute?.('aria-pressed',isCash?'true':'false');
 }
 const cashAfter=$('cashAfterBtn');
 if(cashAfter){
  cashAfter.classList?.toggle?.('active',!isCash);
  cashAfter.setAttribute?.('aria-pressed',!isCash?'true':'false');
 }
 syncCurveBasisControls();
 const data=typeof getScopeCashMetrics==='function'?getScopeCashMetrics(current?.result,summaryScope,summaryCash):null;
 const flowKey=data?.flowKey||(summaryScope==='solar'?(summaryCash==='loan'?'solar_equity':'pv_incremental'):(summaryCash==='loan'?'package_equity':'whole_package'));
 const sel=$('cashFlowBasis');
 if(sel)sel.value=flowKey;
}
function restoreSummaryPrefs(){
 const prefs=readSummaryPrefs();
 summaryScope=prefs.scope;
 summaryCash=prefs.cash;
 syncSummaryControls();
 return prefs;
}
function setSummaryScope(scope){
 summaryScope=VALID_SUMMARY_SCOPES.has(scope)?scope:'solar';
 persistSummaryPrefs(summaryScope,summaryCash);
 syncSummaryControls();
 if(typeof invalidateSummarySensitivity==='function')invalidateSummarySensitivity();
 else summarySensitivityReqId++;
 if(current?.result){
  if(typeof renderVerdictStrip==='function')renderVerdictStrip(current.result);
  renderCashChart();
  if(typeof renderConfigVsRecommended==='function')renderConfigVsRecommended();
  if(typeof renderSaved==='function')renderSaved();
  if(typeof renderSummarySensitivity==='function')renderSummarySensitivity();
 }
}
function setSummaryCash(mode){
 summaryCash=VALID_SUMMARY_CASH.has(mode)?mode:'cash';
 persistSummaryPrefs(summaryScope,summaryCash);
 syncSummaryControls();
 if(typeof invalidateSummarySensitivity==='function')invalidateSummarySensitivity();
 else summarySensitivityReqId++;
 if(current?.result){
  if(typeof renderVerdictStrip==='function')renderVerdictStrip(current.result);
  renderCashChart();
  if(typeof renderConfigVsRecommended==='function')renderConfigVsRecommended();
  if(typeof renderSaved==='function')renderSaved();
  if(typeof renderSummarySensitivity==='function')renderSummarySensitivity();
 }
}
const CASH_CURVE_STORAGE_KEY='pv-lab-cash-curve-prefs-v1';
const VALID_CURVE_BASES=new Set(['undiscounted','discounted']);
function readCashCurvePrefs(){
 try{
  if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.getItem!=='function')return {summary:'undiscounted',loan:'discounted'};
  const raw=localStorage.getItem(CASH_CURVE_STORAGE_KEY);
  if(!raw)return {summary:'undiscounted',loan:'discounted'};
  const parsed=JSON.parse(raw);
  if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))return {summary:'undiscounted',loan:'discounted'};
  const summary=VALID_CURVE_BASES.has(parsed.summary)?parsed.summary:'undiscounted';
  const loan=VALID_CURVE_BASES.has(parsed.loan)?parsed.loan:'discounted';
  return {summary,loan};
 }catch{
  return {summary:'undiscounted',loan:'discounted'};
 }
}
function persistCashCurvePrefs(summary=summaryCurveBasis,loan=loanCurveBasis){
 try{
  if(typeof localStorage==='undefined'||!localStorage||typeof localStorage.setItem!=='function')return;
  const validSummary=VALID_CURVE_BASES.has(summary)?summary:'undiscounted';
  const validLoan=VALID_CURVE_BASES.has(loan)?loan:'discounted';
  localStorage.setItem(CASH_CURVE_STORAGE_KEY,JSON.stringify({summary:validSummary,loan:validLoan}));
 }catch{}
}
const _initialCurvePrefs=readCashCurvePrefs();
let summaryCurveBasis=_initialCurvePrefs.summary;
function syncCurveBasisControls(){
 const isUndisc=summaryCurveBasis!=='discounted';
 const undiscBtn=$('curveUndiscountedBtn');
 if(undiscBtn){
  undiscBtn.classList?.toggle?.('active',isUndisc);
  undiscBtn.setAttribute?.('aria-pressed',isUndisc?'true':'false');
 }
 const discBtn=$('curveDiscountedBtn');
 if(discBtn){
  discBtn.classList?.toggle?.('active',!isUndisc);
  discBtn.setAttribute?.('aria-pressed',!isUndisc?'true':'false');
 }
}
function setSummaryCurveBasis(basis){
 summaryCurveBasis=VALID_CURVE_BASES.has(basis)?basis:'undiscounted';
 persistCashCurvePrefs();
 syncCurveBasisControls();
 if(current?.result){
  renderCashChart();
 }
}
let loanCurveBasis=_initialCurvePrefs.loan;
function syncLoanCurveBasisControls(){
 const isUndisc=loanCurveBasis!=='discounted';
 const undiscBtn=$('loanCurveUndiscounted');
 if(undiscBtn){
  undiscBtn.classList?.toggle?.('active',isUndisc);
  undiscBtn.setAttribute?.('aria-pressed',isUndisc?'true':'false');
 }
 const discBtn=$('loanCurveDiscounted');
 if(discBtn){
  discBtn.classList?.toggle?.('active',!isUndisc);
  discBtn.setAttribute?.('aria-pressed',!isUndisc?'true':'false');
 }
}
function setLoanCurveBasis(basis){
 loanCurveBasis=VALID_CURVE_BASES.has(basis)?basis:'discounted';
 persistCashCurvePrefs();
 syncLoanCurveBasisControls();
 if(current?.result){
  const r=current.result,f=r.finance;
  const hasPackageCost=(f.conversion_capex_eur>0)||(r.settings?.load_scenario?(r.settings.load_scenario!=='baseline'):(f.loan_scope==='solar_and_conversion'||Boolean(r.metrics?.package_npv_eur&&!r.metrics?.npv_eur)));
  renderCashChart(r,'loanCashChart',hasPackageCost?'package_equity':'solar_equity',loanCurveBasis);
 }
}
function cashFlowView(mode){
 return mode==='solar_equity'?{key:'solar_equity',range:'solar_equity_range',label:'Your solar equity cash flow after loan repayments'}
  :mode==='package_equity'?{key:'package_equity',range:'package_equity_range',label:'Your whole-house equity cash flow after loan repayments'}
  :mode==='equity'?{key:'equity',range:'equity_range',label:'Your equity cash flow after loan repayments'}
  :mode==='financed'||mode==='financed_project'?{key:'financed_project',range:'financed_project_range',label:'Financed system cash flow after interest'}
  :mode==='package'||mode==='whole_package'?{key:'whole_package',range:'whole_package_range',label:'Whole-house cash flow versus historic bills'}
  :{key:'pv_incremental',range:'pv_incremental_range',label:'Solar project cash flow before financing'};
}
function renderCashChart(result=current?.result,targetId='cashChart',basis=null,curveBasis=null){
 if(!result)return;
 const finance=result.finance,loanTarget=targetId==='loanCashChart',node=id=>$(loanTarget?({cashChart:'loanCashChart',cashInsights:'loanInsights'}[id]||id):id);
 const activeCurveBasis=loanTarget?(curveBasis||loanCurveBasis):(curveBasis||summaryCurveBasis);
 const isDiscounted=activeCurveBasis==='discounted';
 if(!loanTarget&&typeof syncCurveBasisControls==='function')syncCurveBasisControls();
 if(loanTarget&&typeof syncLoanCurveBasisControls==='function')syncLoanCurveBasisControls();
 const summaryDefaultKey=summaryScope==='solar'?(summaryCash==='loan'?'solar_equity':'pv_incremental'):(summaryCash==='loan'?'package_equity':'whole_package');
 const canonicalKey=getScopeCashMetrics(result,summaryScope,summaryCash)?.flowKey||summaryDefaultKey;
 const selectedBasis=basis||(loanTarget?'equity':(($('scopeSolarBtn')?canonicalKey:($('cashFlowBasis')?.value||canonicalKey))));
 if(!loanTarget&&$('cashFlowBasis'))$('cashFlowBasis').value=selectedBasis;
 const view=cashFlowView(selectedBasis),scope=finance.loan_scope==='solar_and_conversion'?'solar + electrification':'solar';
 if(!loanTarget)$('cashFlowHeading').textContent=view.key==='solar_equity'?`Your cash · Solar PV + storage (after loan)`:view.key==='package_equity'?`Your cash · Whole electrified house (after loan)`:view.key==='equity'?`Your cash · ${scope}`:view.key==='financed_project'?`System cash · ${scope} + loan interest`:view.key==='whole_package'?'Whole-house cash · versus historic bills':'Solar project cash · before financing';
 if(loanTarget&&$('loanCashHeading'))$('loanCashHeading').textContent=`Taking loan vs Paying cash upfront · Cumulative cash comparison over ${result.settings.horizon_years||20} years`;
 const historicBaseline=view.key==='whole_package'||view.key==='package_equity'||((result.settings.load_scenario!=='baseline'||finance.loan_scope==='solar_and_conversion')&&(view.key==='equity'||view.key==='financed_project'));
 const real=result.settings.cashflow_mode==='real',inflation=result.settings.inflation||0,discountRate=real?(finance.real_discount_rate??result.settings.real_discount_rate??0):(finance.nominal_discount_rate??((1+(result.settings.real_discount_rate||0))*(1+inflation)-1));
  if(!loanTarget){
   $('cashFlowDescription').textContent=isDiscounted
    ? `Cumulative discounted cash impact (NPV) versus ${historicBaseline?'keeping the historic electricity and gas setup':'the same selected electricity demand without solar'}, in ${real?`constant purchasing-power euros (${fmt(discountRate,'pct')} real discount rate)`:`nominal present-value euros (${fmt(discountRate,'pct')} nominal discount rate)`}.`
    : `Cumulative cash impact versus ${historicBaseline?'keeping the historic electricity and gas setup':'the same selected electricity demand without solar'} in ${real?'real euros (inflation-adjusted)':'nominal euros (year-of-payment)'}, with financial metrics discounted at ${fmt(discountRate,'pct')} ${real?'real':'nominal'}.`;
   if($('cashLegendLine'))$('cashLegendLine').textContent=isDiscounted?'Cumulative discounted impact (NPV)':'Cumulative cash impact';
   if($('cashLegendAnnual'))$('cashLegendAnnual').textContent=isDiscounted?'Discounted annual cash flow':'Annual net cash flow';
  }
 const years=finance.cashflows_by_weather?.[view.key]
  || (view.key==='equity'?(result.settings.load_scenario!=='baseline'||finance.loan_scope==='solar_and_conversion'?finance.cashflows_by_weather?.package_equity:finance.cashflows_by_weather?.solar_equity):null)
  || (view.key==='package_equity'||view.key==='solar_equity'?finance.cashflows_by_weather?.equity:null);
 if(!years?.length){node('cashChart').textContent='Weather-by-weather cash-flow detail is unavailable for this result.';return;}
 const discFinancePaths=getDiscountingCashflows(result)||finance.cashflows_by_weather;
 const discYears=discFinancePaths?.[view.key]
  || (view.key==='equity'?(result.settings.load_scenario!=='baseline'||finance.loan_scope==='solar_and_conversion'?discFinancePaths?.package_equity:discFinancePaths?.solar_equity):null)
  || (view.key==='package_equity'||view.key==='solar_equity'?discFinancePaths?.equity:null)
  || years;
 const equityInitial=Math.max(0,-(finance.cashflows_by_weather?.equity?.[0]?.[0]??0)),fullCostInput=finance.cashflows_by_weather?.financed_project?.[0]?.[0],fallbackCost=(finance.capex_eur||0)+(finance.loan_scope==='solar_and_conversion'?(finance.conversion_capex_eur||0):0),fullCostInitial=Math.max(0,fullCostInput==null?fallbackCost:-fullCostInput);
 const solarLoanPrincipal=finance.solar_loan_principal_eur??Math.min(finance.capex_eur,finance.loan_principal_eur||0);
 const solarEquityInitial=Math.max(0,-(finance.cashflows_by_weather?.solar_equity?.[0]?.[0]??(finance.capex_eur-solarLoanPrincipal)));
 const packageEquityInitial=Math.max(0,-(finance.cashflows_by_weather?.package_equity?.[0]?.[0]??(((finance.capex_eur||0)+(finance.conversion_capex_eur||0))-(finance.loan_principal_eur||0))));
 const isPackage=view.key==='package_equity'||view.key==='whole_package'||(finance.loan_scope==='solar_and_conversion'&&(view.key==='equity'||view.key==='financed_project'));
 const activeDownPayment=view.key==='solar_equity'?solarEquityInitial:(view.key==='package_equity'?packageEquityInitial:(view.key==='equity'?equityInitial:(isPackage?(packageEquityInitial??equityInitial):(solarEquityInitial??equityInitial))));
 const activeLoanPrincipal=view.key==='solar_equity'?solarLoanPrincipal:(isPackage?(finance.loan_principal_eur||0):solarLoanPrincipal);
 const loanSentence=`${fmt(activeDownPayment,'eur')} down payment; loan ${fmt(activeLoanPrincipal,'eur')} repaid from savings.`;
 if(!loanTarget){
  const showNote=activeLoanPrincipal>0&&(summaryCash==='loan'||view.key.includes('equity')||finance.loan_principal_eur>0);
  $('cashFlowBasisNote').textContent=showNote?loanSentence:'';
 }
 const deflator=year=>real?Math.pow(1+inflation,year):1,discountor=year=>Math.pow(1+discountRate,year);
 const paths=years.map(row=>{let total=0;return row.map((amount,year)=>{total+=amount/deflator(year);return total;});});
 const discountedPaths=discYears.map(row=>{let total=0;return row.map((amount,year)=>{total+=amount/deflator(year)/(year===0?1:discountor(year));return total;});});
 const annual=years[0].map((_,year)=>years.map(row=>row[year]/deflator(year)));
 const discountedAnnual=discYears[0].map((_,year)=>discYears.map(row=>(row[year]/deflator(year))/(year===0?1:discountor(year))));
 const ordered=xs=>[...xs].sort((a,b)=>a-b),median=xs=>{const a=ordered(xs),m=Math.floor(a.length/2);return a.length%2?a[m]:(a[m-1]+a[m])/2;};
 const mean=xs=>(xs&&xs.length)?(xs.reduce((a,b)=>a+b,0)/xs.length):0;
 const cumulative=paths[0].map((_,year)=>paths.map(path=>path[year])),mid=cumulative.map(mean),mins=cumulative.map(xs=>Math.min(...xs)),maxs=cumulative.map(xs=>Math.max(...xs));
 const discountedCumulative=discountedPaths[0].map((_,year)=>discountedPaths.map(path=>path[year])),discountedMid=discountedCumulative.map(mean),discountedMins=discountedCumulative.map(xs=>Math.min(...xs)),discountedMaxs=discountedCumulative.map(xs=>Math.max(...xs));
 const plotPaths=isDiscounted?discountedPaths:paths;
 const plotAnnual=isDiscounted?discountedAnnual:annual;
 const plotMid=isDiscounted?discountedMid:mid;
 const plotMins=isDiscounted?discountedMins:mins;
 const plotMaxs=isDiscounted?discountedMaxs:maxs;
 let cashMid=null,cashInitial=0,cashFinalMean=0;
 if(loanTarget){
  const cashKey=(view.key==='package_equity'||(view.key==='equity'&&result.settings.load_scenario!=='baseline')||(view.key!=='solar_equity'&&finance.loan_scope==='solar_and_conversion'))?'whole_package':'pv_incremental';
  const cashYears=finance.cashflows_by_weather?.[cashKey];
  if(cashYears?.length){
   const discCashYears=discFinancePaths?.[cashKey]||cashYears;
   const cashPaths=cashYears.map(row=>{let t=0;return row.map((amount,year)=>{t+=amount/deflator(year);return t;});});
   const cashDiscountedPaths=discCashYears.map(row=>{let t=0;return row.map((amount,year)=>{t+=amount/deflator(year)/(year===0?1:discountor(year));return t;});});
   const activeCashPaths=isDiscounted?cashDiscountedPaths:cashPaths;
   const cashCum=activeCashPaths[0].map((_,year)=>activeCashPaths.map(p=>p[year]));
   cashMid=cashCum.map(mean);
   cashInitial=Math.abs(cashMid[0])<1e-8?0:-cashMid[0];
   cashFinalMean=cashMid.at(-1);

  }
 }
 const allMins=cashMid?[...plotMins,...cashMid]:plotMins;
 const allMaxs=cashMid?[...plotMaxs,...cashMid]:plotMaxs;
 const roughLo=Math.min(0,...allMins),roughHi=Math.max(0,...allMaxs),rawStep=(roughHi-roughLo)/5||1;
 const magnitude=10**Math.floor(Math.log10(rawStep)),ratio=rawStep/magnitude,axisStep=(ratio<=1?1:ratio<=2?2:ratio<=5?5:10)*magnitude;
 const horizon=mid.length-1,lo=Math.floor(roughLo/axisStep)*axisStep,hi=Math.ceil(roughHi/axisStep)*axisStep,span=hi-lo||1,W=960,H=440,left=76,right=936,top=34,bottom=250,barTop=292,barBottom=404;
 const rawMaxAnnual=Math.max(1,...plotAnnual.slice(1).map(flows=>Math.abs(mean(flows))));
 const annualMag=10**Math.floor(Math.log10(rawMaxAnnual||1)),annualNorm=rawMaxAnnual/annualMag;
 const annualNiceNorm=annualNorm<=1?1:annualNorm<=1.5?1.5:annualNorm<=2?2:annualNorm<=2.5?2.5:annualNorm<=3?3:annualNorm<=4?4:annualNorm<=5?5:annualNorm<=6?6:annualNorm<=8?8:10;
 const maxAnnual=Math.round(annualNiceNorm*annualMag);
 const x=i=>left+i*(right-left)/(horizon||1),y=v=>bottom-(v-lo)/span*(bottom-top),barY=v=>barTop+(barBottom-barTop)/2-v/maxAnnual*(barBottom-barTop)/2;
 const line=values=>values.map((v,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
 const band=`${plotMaxs.map((v,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')} ${plotMins.map((v,i)=>`L${x(horizon-i).toFixed(1)},${y(plotMins[horizon-i]).toFixed(1)}`).join(' ')} Z`;
 const persistentCross=path=>{for(let i=0;i<path.length;i++){if(path[i]<-1e-8||path.slice(i).some(value=>value<-1e-8))continue;if(i===0)return 0;if(path[i-1]<0&&path[i]>path[i-1])return i-1+(-path[i-1])/(path[i]-path[i-1]);return i;}return null;};
 const discountedWeatherPaybacks=discountedPaths.map(persistentCross),discountedRecovered=discountedWeatherPaybacks.filter(value=>value!==null).length;
 const quantileCensored=q=>{const sorted=discountedWeatherPaybacks.map(value=>value??Infinity).sort((a,b)=>a-b),position=(sorted.length-1)*q,lower=Math.floor(position),upper=Math.ceil(position);if(!Number.isFinite(sorted[lower])||!Number.isFinite(sorted[upper]))return null;return sorted[lower]+(sorted[upper]-sorted[lower])*(position-lower);};
 const fallbackPayback={min:discountedRecovered?Math.min(...discountedWeatherPaybacks.filter(value=>value!==null)):null,median:quantileCensored(.5),max:discountedRecovered===discountedPaths.length?Math.max(...discountedWeatherPaybacks):null,recovered_weather_years:discountedRecovered,weather_count:discountedPaths.length};
 const breakeven=persistentCross(plotMid),finalMedian=plotMid.at(-1),initial=Math.abs(plotMid[0])<1e-8?0:-plotMid[0],horizonWord=horizon===1?'year':'years',payback=finance[view.key]?.discounted_payback||(view.key==='equity'?(result.settings.load_scenario!=='baseline'||finance.loan_scope==='solar_and_conversion'?finance.package_equity?.discounted_payback:finance.solar_equity?.discounted_payback):null)||fallbackPayback;
 const fmtEur=v=>{
  if(v==null||!Number.isFinite(v))return '—';
  const abs=Math.abs(v);
  const formatted=fmt(abs,'eur').replace('€','').trim();
  if(Math.round(abs)===0)return '€0';
  return v<0?`−€${formatted}`:`€${formatted}`;
 };
 const fmtEurSigned=v=>{
  if(v==null||!Number.isFinite(v))return '—';
  const abs=Math.abs(v);
  const formatted=fmt(abs,'eur').replace('€','').trim();
  if(Math.round(abs)===0)return '€0';
  return v<0?`−€${formatted}`:`+€${formatted}`;
 };
 const endLabel=isDiscounted?`At year ${horizon} · historical-weather ensemble mean discounted NPV`:`At year ${horizon} · historical-weather ensemble mean ${real?'real':'nominal'} impact`,endValue=fmtEurSigned(finalMedian),endDetail=isDiscounted?`Discounted cumulative impact (NPV) at ${fmt(discountRate,'pct')} ${real?'real':'nominal'} rate`:`${real?'Inflation-adjusted (real euros)':'Year-of-payment (nominal euros)'} cumulative cash versus selected baseline`;
 const paybackValue=breakeven==null?`Not reached in ${horizon} ${horizonWord}`:(breakeven===0?'Year 0':fmt(breakeven,'years'));
 const nonRecoveringCount=payback.weather_count?(payback.weather_count-(payback.recovered_weather_years??discountedRecovered)):0;
 const nonRecText=nonRecoveringCount>0?` · ${nonRecoveringCount} non-recovering cases`:'';
 const paybackDetail=isDiscounted
  ? (breakeven==null?`Does not reach €0 discounted balance across ${horizon} ${horizonWord} (discounted per-weather payback min–max ${range(payback,'years')}${nonRecText})`:(breakeven===0?`Stays ≥ €0 discounted balance from start (discounted per-weather payback min–max ${range(payback,'years')}${nonRecText})`:`Year ${nf(breakeven,1)} persistent payback of ensemble-mean cash flows (discounted per-weather payback min–max ${range(payback,'years')}${nonRecText})`))
  : (breakeven==null?`Does not reach €0 balance across ${horizon} ${horizonWord} (discounted per-weather payback min–max ${range(payback,'years')}${nonRecText})`:(breakeven===0?`Stays ≥ €0 from start (discounted per-weather payback min–max ${range(payback,'years')}${nonRecText})`:`Year ${nf(breakeven,1)} persistent payback of ensemble-mean cash flows (discounted per-weather payback min–max ${range(payback,'years')}${nonRecText})`));
 const paybackLabel='Payback of ensemble-mean cash flows';

 const annualRows=result.annual_cashflows||[];
 const getReplacementDetail=yr=>{
  const r=annualRows[yr]||{},s=result.settings||{};
  const hasGenRep=(r.replacement||0)>0,hasHpRep=isPackage&&(r.hp_replacement||0)>0;
  if(!hasGenRep&&!hasHpRep)return null;
  const realDiv=real?deflator(yr):1,discDiv=isDiscounted?(yr===0?1:discountor(yr)):1,totalDiv=realDiv*discDiv;
  const hpCost=hasHpRep?((r.hp_replacement||0)/totalDiv):0;
  const totalGenCost=(r.replacement||0)/totalDiv;
  const totalCost=totalGenCost+hpCost;
  let invCost=0,batCost=0;
  if(r.inverter_replacement!=null||r.battery_replacement!=null){
   invCost=(r.inverter_replacement||0)/totalDiv;
   batCost=(r.battery_replacement||0)/totalDiv;
  }else if(hasGenRep){
   const isInv=s.inverter_replace_year===yr,isBat=s.battery_replace_year===yr&&((s.battery_kwh||0)>0||(result.layout?.battery_kwh||0)>0);
   if(isInv&&!isBat)invCost=totalGenCost;
   else if(isBat&&!isInv)batCost=totalGenCost;
  }
  const items=[];
  if(invCost>0)items.push({name:'Inverter replacement',cost:invCost});
  if(batCost>0)items.push({name:'Battery replacement / augmentation',cost:batCost});
  if(items.length===0&&hasGenRep){
   const isInv=s.inverter_replace_year===yr,isBat=s.battery_replace_year===yr&&((s.battery_kwh||0)>0||(result.layout?.battery_kwh||0)>0);
   const label=isInv&&isBat?'Inverter & battery replacement':(isInv?'Inverter replacement':(isBat?'Battery replacement':'Solar equipment replacement'));
   items.push({name:label,cost:totalGenCost});
  }
  if(hpCost>0)items.push({name:'Heat pump replacement',cost:hpCost});
  const cause=items.map(it=>it.name).join(' & ');
  return {year:yr,totalCost,totalGenCost,hpCost,invCost,batCost,items,cause};
 };

  // Mean actual active-basis annual cash across first and second half of horizon (including replacements)
  const h1End=Math.max(1,Math.floor(horizon/2));
  const h2Start=h1End+1;
  const h2End=horizon;
  const h1Label=h1End===1?'Year 1':`Years 1–${h1End}`;
  const h2Label=h2Start>h2End?'':(h2Start===h2End?`Year ${h2End}`:`Years ${h2Start}–${h2End}`);
  const card3Title=isDiscounted?'Discounted annual cash':'Annual cash';

  const yH1=[];
  for(let yr=1;yr<=h1End;yr++){
   const flows=plotAnnual[yr]||[];
   if(flows.length) yH1.push(mean(flows));
  }
  const yH2=[];
  for(let yr=h2Start;yr<=h2End;yr++){
   const flows=plotAnnual[yr]||[];
   if(flows.length) yH2.push(mean(flows));
  }
  const avgH1=yH1.length?(yH1.reduce((a,b)=>a+b,0)/yH1.length):null;
  const avgH2=yH2.length?(yH2.reduce((a,b)=>a+b,0)/yH2.length):null;
  const avg1to10Display=avgH1!=null?`${fmtEurSigned(avgH1)} / yr`:'—';
  const avg11to20Display=avgH2!=null?fmtEurSigned(avgH2):'—';

  const loanTerm=Number(result.settings?.loan_years??10);
  const isLoanView=(view.key==='solar_equity'||view.key==='package_equity'||view.key==='equity'||view.key==='financed_project'||view.key==='financed')&&((finance.loan_principal_eur||0)>0);
  const hasLoanPayoff=isLoanView&&loanTerm<=h1End;

  const card3Subtitle=avgH2!=null?`${h1Label} vs ${h2Label}${hasLoanPayoff?' · after loan payoff':''}`:h1Label;
  const card3Value=avgH2!=null?`${esc(avg1to10Display)}<span> vs </span>${esc(avg11to20Display)} / yr`:esc(avg1to10Display);
 const undiscountedBreakEvenValue=breakeven==null?`Not reached in ${horizon} ${horizonWord}`:(breakeven===0?'Year 0':fmt(breakeven,'years'));
 const undiscountedBreakEvenCaption=breakeven==null?'Does not reach €0 balance':(breakeven===0?'Stays ≥ €0 from start':`Year ${nf(breakeven,1)} persistent payback of ensemble-mean cash flows`);

 if(loanTarget){
  const scope=(view.key==='package_equity'||(view.key==='equity'&&result.settings.load_scenario!=='baseline')||(view.key!=='solar_equity'&&finance.loan_scope==='solar_and_conversion'))?'package':'solar';
  const unleveredScope=getScopeCashMetrics(result,scope,'cash');
  const leveredScope=getScopeCashMetrics(result,scope,'loan');
  const unleveredPbObj=unleveredScope?.pbObj;
  const leveredPbObj=leveredScope?.pbObj;
  const uPb=(unleveredPbObj!=null&&typeof unleveredPbObj==='object')
   ?(unleveredPbObj.discounted_payback_of_mean_cash_flows!==undefined?unleveredPbObj.discounted_payback_of_mean_cash_flows:unleveredPbObj.median)
   :(typeof unleveredPbObj==='number'?unleveredPbObj:null);
  const lPb=(leveredPbObj!=null&&typeof leveredPbObj==='object')
   ?(leveredPbObj.discounted_payback_of_mean_cash_flows!==undefined?leveredPbObj.discounted_payback_of_mean_cash_flows:leveredPbObj.median)
   :(typeof leveredPbObj==='number'?leveredPbObj:null);
  const loanPbStr=lPb!=null?fmt(lPb,'years'):'Not reached';
  const cashPbStr=uPb!=null?fmt(uPb,'years'):'Not reached';
  let accelDiffStr='';
  if(uPb!=null&&lPb!=null){
   const diff=uPb-lPb;
   accelDiffStr=diff>0.05?`Loan accelerates recovery by ${fmt(diff,'years')}`:(diff<-0.05?`Loan extends recovery by ${fmt(-diff,'years')}`:'Matches cash recovery');
  }else if(lPb!=null){
   accelDiffStr=`Loan achieves recovery in ${fmt(lPb,'years')} (cash not reached)`;
  }else if(uPb!=null){
   accelDiffStr=`Cash achieves recovery in ${fmt(uPb,'years')} (loan not reached)`;
  }else{
   accelDiffStr='Neither reaches persistent recovery in horizon';
  }

  const savedLiquidity=Math.max(0,cashInitial-initial);

  const insights=[
   [
    'Initial outlay · with loan vs paying cash',
    `${fmt(initial,'eur')} <small class="muted">vs</small> ${fmt(cashInitial,'eur')}`,
    `With loan: ${fmt(initial,'eur')} down payment (${fmt(activeLoanPrincipal,'eur')} financed) · Paying cash: ${fmt(cashInitial,'eur')} · Saved liquidity delta: +${fmt(savedLiquidity,'eur')}`
   ],
   [
    isDiscounted ? `At year ${horizon} · discounted NPV (Loan vs Cash)` : `At year ${horizon} · cumulative cash (Loan vs Cash)`,
    `${endValue} <small class="muted">vs</small> ${fmtEurSigned(cashFinalMean)}`,
    `Loan: ${endValue} · Cash: ${fmtEurSigned(cashFinalMean)} (${isDiscounted ? `discounted at ${fmt(discountRate,'pct')} ${real?'real':'nominal'}` : (real?'real euros':'nominal euros')})`
   ],
   [
    'Discounted financial recovery (Loan vs Cash)',
    `${loanPbStr} <small class="muted">vs</small> ${cashPbStr}`,
    `Loan: ${loanPbStr} · Cash: ${cashPbStr} · ${accelDiffStr}`
   ]
  ];
  node('cashInsights').innerHTML=insights.map(([label,value,detail])=>`<div class="cash-insight"><span>${label}</span><strong>${value}</strong><small>${detail}</small></div>`).join('');
 }else if(isDiscounted){
  const popover1=`<div class="cash-popover"><button type="button" class="cash-popover-trigger" aria-label="Details" aria-describedby="${targetId}-popover-1"><span aria-hidden="true">i</span></button><div class="cash-popover-card" id="${targetId}-popover-1" role="tooltip"><div><span>${esc(paybackLabel)}:</span> <strong>${esc(paybackValue)}</strong></div><small>${esc(paybackDetail)}</small></div></div>`;
  const popover2=`<div class="cash-popover"><button type="button" class="cash-popover-trigger" aria-label="Details" aria-describedby="${targetId}-popover-2"><span aria-hidden="true">i</span></button><div class="cash-popover-card" id="${targetId}-popover-2" role="tooltip"><div><span>${esc(endLabel)}:</span> <strong>${esc(endValue)}</strong></div><small>${esc(endDetail)}</small></div></div>`;
  const popover3=`<div class="cash-popover"><button type="button" class="cash-popover-trigger" aria-label="Details" aria-describedby="${targetId}-popover-3"><span aria-hidden="true">i</span></button><div class="cash-popover-card" id="${targetId}-popover-3" role="tooltip"><div>Mean annual cash flow across ${h1Label} (${avg1to10Display})${avgH2!=null?` and ${h2Label} (${avg11to20Display} / yr)`:''}.</div></div></div>`;
  node('cashInsights').innerHTML=`<div class="cash-insight"><div class="cash-insight-head"><span>Discounted payback</span>${popover1}</div><strong>${esc(paybackValue)}</strong><small>${esc(paybackDetail)}</small></div><div class="cash-insight"><div class="cash-insight-head"><span>Historical-weather ensemble mean ${horizon}-y discounted NPV</span>${popover2}</div><strong>${esc(endValue)}</strong><small>Discounted cumulative cash (NPV)</small></div><div class="cash-insight"><div class="cash-insight-head"><span>${card3Title}</span>${popover3}</div><strong>${card3Value}</strong><small>${esc(card3Subtitle)}</small></div>`;
 }else{
  const popover1=`<div class="cash-popover"><button type="button" class="cash-popover-trigger" aria-label="Details" aria-describedby="${targetId}-popover-1"><span aria-hidden="true">i</span></button><div class="cash-popover-card" id="${targetId}-popover-1" role="tooltip"><div><span>${esc(paybackLabel)}:</span> <strong>${esc(paybackValue)}</strong></div><small>${esc(paybackDetail)}</small></div></div>`;
  const popover2=`<div class="cash-popover"><button type="button" class="cash-popover-trigger" aria-label="Details" aria-describedby="${targetId}-popover-2"><span aria-hidden="true">i</span></button><div class="cash-popover-card" id="${targetId}-popover-2" role="tooltip"><div><span>${esc(endLabel)}:</span> <strong>${esc(endValue)}</strong></div><small>${esc(endDetail)}</small></div></div>`;
  const popover3=`<div class="cash-popover"><button type="button" class="cash-popover-trigger" aria-label="Details" aria-describedby="${targetId}-popover-3"><span aria-hidden="true">i</span></button><div class="cash-popover-card" id="${targetId}-popover-3" role="tooltip"><div>Mean annual cash flow across ${h1Label} (${avg1to10Display})${avgH2!=null?` and ${h2Label} (${avg11to20Display} / yr)`:''}.</div></div></div>`;
  node('cashInsights').innerHTML=`<div class="cash-insight"><div class="cash-insight-head"><span>Undiscounted break-even</span>${popover1}</div><strong>${esc(undiscountedBreakEvenValue)}</strong><small>${esc(undiscountedBreakEvenCaption)}</small></div><div class="cash-insight"><div class="cash-insight-head"><span>Historical-weather ensemble mean ${horizon}-y cumulative impact</span>${popover2}</div><strong>${esc(endValue)}</strong><small>${real?'Real (inflation-adjusted)':'Nominal'} cumulative cash</small></div><div class="cash-insight"><div class="cash-insight-head"><span>${card3Title}</span>${popover3}</div><strong>${card3Value}</strong><small>${esc(card3Subtitle)}</small></div>`;
 }
 const ticks=Array.from({length:horizon+1},(_,i)=>i).filter(i=>i===0||i===horizon||i%Math.max(1,Math.ceil(horizon/8))===0);
 const scenarioLines=plotPaths.map((path,index)=>`<path d="${line(path)}" fill="none" stroke="#7eac93" stroke-width="1.25" opacity=".28"><title>Weather scenario ${index+1}${isDiscounted?' (discounted NPV)':''}</title></path>`).join('');
 const marks=plotMid.map((value,year)=>{
  const row=annualRows[year]||{},realDiv=real?Math.pow(1+inflation,year):1,discDiv=isDiscounted?(year===0?1:discountor(year)):1,divisor=realDiv,costFmt=cost=>fmt(Math.abs(cost)<0.5?0:-Math.abs(cost),'eur');
  const packageScope=view.key==='whole_package'||view.key==='package_equity'||(view.key==='equity'&&(result.settings.load_scenario!=='baseline'||finance.loan_scope==='solar_and_conversion'))||(view.key==='financed_project'&&(result.settings.load_scenario!=='baseline'||finance.loan_scope==='solar_and_conversion'));
  const packageSaving=((row.baseline_package_bill||0)-(row.with_system_electric_bill||0)-(row.remaining_gas_bill||0))/divisor;
  const stochasticFirstRow=packageScope
   ? `Avoided historic package bills: ${fmt(packageSaving,'eur')}`
   : `Avoided same-load electricity bills: ${fmt((row.avoided_import||0)/divisor,'eur')}`;
  const stochasticSecondRow=`Net export + CER: ${fmt(((row.net_export||0)+(row.cer||0))/divisor,'eur')}`;
  const loanLine=view.key==='equity'?`
Loan repayments · principal + interest: ${costFmt((row.debt_service||0)/divisor)}`
   :view.key==='solar_equity'?`
Solar loan repayments: ${costFmt(((row.debt_service||0)/divisor)*(finance.loan_principal_eur>0?(solarLoanPrincipal/finance.loan_principal_eur):1))}`
   :view.key==='package_equity'?`
Whole-project loan repayments: ${costFmt((row.debt_service||0)/divisor)}`
   :view.key==='financed_project'?`
Loan interest only: ${costFmt((row.loan_interest||0)/divisor)}`
   :'';
  const breakdownHeader=real
   ? 'Annual breakdown · ensemble mean (real undiscounted):'
   : 'Annual breakdown · ensemble mean (nominal undiscounted):';
  const hpParts=(packageScope&&(row.hp_maintenance||row.hp_replacement||row.unserved_service_allowance))?`${row.hp_maintenance?`
Heat pump maintenance: ${costFmt((row.hp_maintenance||0)/divisor)}`:''}${row.hp_replacement?`
Heat pump replacement: ${costFmt((row.hp_replacement||0)/divisor)}`:''}${row.unserved_service_allowance?`
Unserved service allowance: ${costFmt((row.unserved_service_allowance||0)/divisor)}`:''}`:'';
  const repDetail=getReplacementDetail(year);
  let repLines='';
  if(repDetail){
   repLines=`
Replacement: ${costFmt(repDetail.totalGenCost*discDiv)}`;
   if(repDetail.invCost>0&&repDetail.batCost>0){
    repLines+=`
  Inverter replacement: ${costFmt(repDetail.invCost*discDiv)}
  Battery replacement / augmentation: ${costFmt(repDetail.batCost*discDiv)}`;
   }
  }
  const breakdownParts=year>0?`
${breakdownHeader}
${stochasticFirstRow}
${stochasticSecondRow}
Tax deduction: ${fmt((row.tax_deduction||0)/divisor,'eur')}
Maintenance: ${costFmt((row.maintenance||0)/divisor)}${repLines}${row.residual?`
Residual value: ${fmt((row.residual||0)/divisor,'eur')}`:''}${hpParts}${loanLine}`:'';
  const yearFlows=annual[year]||[],discountedFlows=discountedAnnual[year]||[],discVal=discountedMid[year],activeFlows=plotAnnual[year]||[];
  const detail=isDiscounted
   ? `Year ${year}
Discounted annual contribution (${fmt(discountRate,'pct')} ${real?'real':'nominal'} rate) · historical-weather ensemble mean ${fmt(mean(discountedFlows),'eur')} (weather range ${fmt(Math.min(...discountedFlows),'eur')} to ${fmt(Math.max(...discountedFlows),'eur')})
Cumulative discounted impact (NPV) · historical-weather ensemble mean ${fmt(discVal,'eur')} (weather range ${fmt(discountedMins[year],'eur')} to ${fmt(discountedMaxs[year],'eur')})
Annual cash (undiscounted) · ${real?'real':'nominal'} · historical-weather ensemble mean ${fmt(mean(yearFlows),'eur')} (weather range ${fmt(Math.min(...yearFlows),'eur')} to ${fmt(Math.max(...yearFlows),'eur')})
Cumulative ${real?'real':'nominal'} cash impact (undiscounted) · historical-weather ensemble mean ${fmt(mid[year],'eur')} (weather range ${fmt(mins[year],'eur')} to ${fmt(maxs[year],'eur')})${breakdownParts}`
   : `Year ${year}
Annual cash · ${real?'real':'nominal'} · historical-weather ensemble mean ${fmt(mean(yearFlows),'eur')} (weather range ${fmt(Math.min(...yearFlows),'eur')} to ${fmt(Math.max(...yearFlows),'eur')})
Discounted annual contribution (${fmt(discountRate,'pct')} ${real?'real':'nominal'} rate) · historical-weather ensemble mean ${fmt(mean(discountedFlows),'eur')}
Cumulative ${real?'real':'nominal'} cash impact · historical-weather ensemble mean ${fmt(mid[year],'eur')} (weather range ${fmt(mins[year],'eur')} to ${fmt(maxs[year],'eur')})
Cumulative discounted impact · historical-weather ensemble mean ${fmt(discVal,'eur')}${breakdownParts}`;
  const barBase=barY(0),barVal=barY(mean(activeFlows)),bh=Math.max(1,Math.abs(barBase-barVal));
  return `<g ${chartTarget(detail)}><line x1="${x(year)}" x2="${x(year)}" y1="${top}" y2="${barBottom+2}" stroke="transparent" stroke-width="${Math.max(18,(right-left)/(horizon||1)*.8)}" pointer-events="stroke"/><circle cx="${x(year)}" cy="${y(value)}" r="${year===0?5:3.5}" fill="#217a61" stroke="white" stroke-width="1.5"/>${year===0?'':`<rect x="${x(year)-5}" y="${Math.min(barBase,barVal)}" width="10" height="${bh}" rx="2" fill="${mean(activeFlows)>=0?'#217a61':'#bd704f'}" opacity=".82"/>`}<text x="${x(year)}" y="426" text-anchor="middle" font-size="10" fill="#62756e">${ticks.includes(year)?year:''}</text></g>`;
 }).join('');
 const crossing=breakeven==null?'':(()=>{
  const yy=y(0),cx=x(breakeven),cy=yy,isZeroStart=breakeven===0;
  const labelText=isZeroStart
   ? 'Non-negative from year 0'
   : (isDiscounted ? `Discounted payback · year ${nf(breakeven,1)}` : `Cash break-even · year ${nf(breakeven,1)}`);
  const ariaTitle=isZeroStart
   ? (isDiscounted ? `Discounted NPV stays non-negative from year 0 through year ${horizon}` : `Cash stays non-negative from year 0 through year ${horizon}; no initial cash spent`)
   : (isDiscounted ? `Discounted payback at year ${nf(breakeven,1)} through year ${horizon}; historical-weather ensemble mean cumulative discounted cash (NPV) reaches €0 and stays non-negative` : `Cash break-even at year ${nf(breakeven,1)} through year ${horizon}; historical-weather ensemble mean cumulative cash reaches €0 and stays non-negative`);
  let boxW=Math.max(160,Math.ceil(labelText.length*7+30));
  try{
   if(typeof OffscreenCanvas==='function'){
    const ctx=new OffscreenCanvas(1,1).getContext('2d');
    if(ctx){ctx.font='600 11px system-ui';boxW=Math.ceil(ctx.measureText(labelText).width)+28;}
   }
  }catch(_){}
  const boxH=22;
  const placeAbove=(yy-top)>=36;
  const idealY=placeAbove?yy-32:yy+12;
  const boxY=Math.max(top+2,Math.min(bottom-boxH-2,idealY));
  const idealX=cx>(left+right)/2?cx-boxW+24:(isZeroStart?left+8:cx-boxW+24);
  const boxX=Math.max(left+4,Math.min(right-boxW-4,idealX));
  const boxBottom=boxY+boxH,boxTop=boxY;
  const anchorY=boxBottom<=cy?boxBottom:(boxTop>=cy?boxTop:(cy>boxY+boxH/2?boxBottom:boxTop));
  const anchorX=Math.max(boxX+10,Math.min(boxX+boxW-10,cx));
  const leaderLine=`<line x1="${anchorX.toFixed(1)}" y1="${anchorY.toFixed(1)}" x2="${cx.toFixed(1)}" y2="${cy.toFixed(1)}" stroke="#b45309" stroke-width="1.5" stroke-linecap="round"/>`;
  const marker=`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="7.5" fill="white" opacity=".96"/><circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="5" fill="#b45309" stroke="white" stroke-width="1.5"/>`;
  const box=`<rect x="${boxX.toFixed(1)}" y="${boxY.toFixed(1)}" width="${boxW}" height="${boxH}" rx="4" fill="white" stroke="#b45309" stroke-width="1.5"/>`;
  const dot=`<circle cx="${(boxX+11).toFixed(1)}" cy="${(boxY+11).toFixed(1)}" r="3" fill="#b45309"/>`;
  const text=`<text x="${(boxX+20).toFixed(1)}" y="${(boxY+15).toFixed(1)}" font-family="system-ui" font-size="11" font-weight="600" fill="#183b33">${esc(labelText)}</text>`;
  return `<g class="cash-breakeven-callout" pointer-events="none" role="img" aria-label="${esc(ariaTitle)}"><title>${esc(ariaTitle)}</title>${leaderLine}${marker}${box}${dot}${text}</g>`;
 })();
 const label=view.label;
 let cashComparisonLine='';
 if(loanTarget&&cashMid){
  cashComparisonLine=`<path d="${line(cashMid)}" fill="none" stroke="#bd8334" stroke-width="2.5" stroke-dasharray="6 4" stroke-linejoin="round"><title>Paying cash upfront (ensemble mean${isDiscounted?' NPV':''})</title></path><g transform="translate(${right-240},20)"><line x1="0" y1="0" x2="24" y2="0" stroke="#183b33" stroke-width="3"/><text x="30" y="4" font-size="10" fill="#183b33">Taking loan (ensemble mean${isDiscounted?' NPV':''})</text><line x1="0" y1="14" x2="24" y2="14" stroke="#bd8334" stroke-width="2.5" stroke-dasharray="6 4"/><text x="30" y="18" font-size="10" fill="#bd8334">Paying cash (ensemble mean${isDiscounted?' NPV':''})</text></g>`;
 }
  const yStep=axisStep;
  const tickMin=Math.floor(lo/yStep)*yStep;
  const tickMax=Math.ceil(hi/yStep)*yStep;
  const yTicks=[];
  for(let v=tickMin;v<=tickMax;v+=yStep)yTicks.push(v);
  if(!yTicks.includes(0))yTicks.push(0);
  yTicks.sort((a,b)=>b-a);
  const yAxisSvg=yTicks.map(v=>{
   const yy=y(v),isZero=v===0;
   const label=isZero?'€0':fmtEur(v);
   return `<line x1="${left}" x2="${right}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" stroke="${isZero?'#6c7972':'#e6ebe4'}" stroke-width="${isZero?1.6:1}" ${isZero?'stroke-dasharray="6 5"':''}/><text x="${left-10}" y="${(yy+4).toFixed(1)}" text-anchor="end" font-size="11" fill="${isZero?'#6c7972':'#62756e'}" ${isZero?'font-weight="600"':''}>${label}</text>`;
  }).join('');

  const repMarkers=[],repEvents=[];
  for(let yr=1;yr<=horizon;yr++){
   const repDetail=getReplacementDetail(yr);
   if(repDetail){
    const cx=x(yr),cy=y(plotMid[yr]);
    const repCostSigned=fmtEur(-repDetail.totalCost);
    let labelText=`Year ${yr}: ${repDetail.cause} ${repCostSigned}${isDiscounted?' (PV)':''}`;
    let aria=`Year ${yr}: ${repDetail.cause} ${repCostSigned}`;
    if(repDetail.invCost>0&&repDetail.batCost>0){
     const invCostSigned=fmtEur(-repDetail.invCost);
     const batCostSigned=fmtEur(-repDetail.batCost);
     labelText=`Year ${yr}: Inverter ${invCostSigned} · Battery ${batCostSigned}`;
     aria=`Year ${yr}: Inverter replacement ${invCostSigned}, battery replacement/augmentation ${batCostSigned} (total ${repCostSigned})`;
    }
    repEvents.push({yr,cx,cy,labelText,aria});
    repMarkers.push(`<g class="cash-replacement-callout" role="img" tabindex="0" aria-label="${esc(aria)}"><title>${esc(aria)}</title><circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="4.5" fill="#bd704f" stroke="white" stroke-width="1.5"/></g>`);
   }
  }
  const repListHtml=repEvents.length?`<div class="cash-replacement-list" role="region" aria-label="Scheduled equipment replacements" style="display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 14px;margin:6px 0 10px;font-size:11.5px;line-height:1.45;"><span style="font-weight:600;color:var(--muted,#62756e);">Equipment replacements:</span><ul style="display:inline-flex;flex-wrap:wrap;gap:6px 16px;margin:0;padding:0;list-style:none;">${repEvents.map(it=>`<li style="display:inline-flex;align-items:center;gap:6px;"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#bd704f;border:1px solid white;" aria-hidden="true"></span><strong style="color:#8c3a27;font-weight:600;">${esc(it.labelText)}</strong></li>`).join('')}</ul></div>`:'';

  const initialAmountStr=plotMid[0]<-1e-6?`−€${fmt(Math.abs(plotMid[0]),'eur').replace('€','').trim()}`:(plotMid[0]>1e-6?`+${fmt(plotMid[0],'eur')}`:'€0');
  const initialOutlayLabel=`Initial outlay: ${initialAmountStr}`;
  const initialLabelY=bottom+14;
  const initialLabelSvg=`<g aria-label="${initialOutlayLabel}" role="img"><title>${initialOutlayLabel}</title><text x="${left+10}" y="${initialLabelY.toFixed(1)}" text-anchor="start" font-size="9.5" font-weight="600" fill="var(--text-main)" stroke="var(--surface)" stroke-width="3" stroke-linejoin="round" paint-order="stroke fill">${initialOutlayLabel}</text></g>`;

  const breakEvenExplanation=isDiscounted
   ? (breakeven==null
      ? `The historical-weather ensemble mean discounted cumulative cash (NPV) does not stay at or above €0 within ${horizon===1?'this 1 year':`these ${horizon} years`} at the ${fmt(discountRate,'pct')} ${real?'real':'nominal'} discount rate. Band: historical scenario min–max; each weather profile repeats throughout the project horizon.`
      : breakeven===0
      ? `The historical-weather ensemble mean discounted cumulative cash (NPV) stays non-negative from year 0 through year ${horizon}. Band: historical scenario min–max; each weather profile repeats throughout the project horizon.`
      : `The historical-weather ensemble mean discounted cumulative cash (NPV) reaches €0 and stays non-negative through year ${horizon} (Payback of ensemble-mean cash flows at year ${nf(breakeven,1)}). Band: historical scenario min–max; each weather profile repeats throughout the project horizon.`)
   : (breakeven==null
      ? `The historical-weather ensemble mean cash balance does not stay at or above €0 within ${horizon===1?'this 1 year':`these ${horizon} years`}. Discounted recovery above also accounts for the time value of money. Band: historical scenario min–max; each weather profile repeats throughout the project horizon.`
      : breakeven===0
      ? `The historical-weather ensemble mean cash balance stays non-negative from year 0 through year ${horizon}. Discounted recovery above also accounts for the time value of money. Band: historical scenario min–max; each weather profile repeats throughout the project horizon.`
      : `The historical-weather ensemble mean cash balance reaches €0 and stays non-negative through year ${horizon}. Discounted recovery above also accounts for the time value of money. Band: historical scenario min–max; each weather profile repeats throughout the project horizon.`);
  const cashTakeaway=`<p class="cash-chart-explanation">${esc(breakEvenExplanation)}</p>`;
  node('cashChart').innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}; cumulative ${isDiscounted?'discounted cash (NPV)':(real?'real':'nominal')+' cash impact'} over ${horizon} years"><defs><linearGradient id="${targetId}-cashBand" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#217a61" stop-opacity=".18"/><stop offset="1" stop-color="#217a61" stop-opacity=".04"/></linearGradient></defs>${yAxisSvg}<text x="${left}" y="18" font-size="11" fill="#62756e">${isDiscounted?`Cumulative discounted cash impact (NPV) · discounted at ${fmt(discountRate,'pct')} ${real?'real':'nominal'}`:`Cumulative ${real?'real':'nominal'} cash impact · ${real?'inflation-adjusted euros':'year-of-payment euros'}`}</text><path d="${band}" fill="url(#${targetId}-cashBand)" stroke="none"/>${scenarioLines}${initialLabelSvg}<path d="${line(plotMid)}" fill="none" stroke="#183b33" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round"/>${cashComparisonLine}<line x1="${left}" x2="${right}" y1="${barY(0)}" y2="${barY(0)}" stroke="#cbd5cc"/><text x="${left}" y="${barTop-13}" font-size="11" fill="#62756e">${isDiscounted?`Historical-weather ensemble mean discounted annual net cash · years 1–${horizon}`:`Historical-weather ensemble mean annual net cash · years 1–${horizon}`}</text><text x="${left-8}" y="${barY(maxAnnual)+4}" text-anchor="end" font-size="9" fill="#62756e">+${fmt(maxAnnual,'eur')}</text><text x="${left-8}" y="${barY(0)+4}" text-anchor="end" font-size="9" fill="#62756e">€0</text><text x="${left-8}" y="${barY(-maxAnnual)+4}" text-anchor="end" font-size="9" fill="#62756e">−${fmt(maxAnnual,'eur')}</text>${marks}${repMarkers.join('')}${crossing}</svg>${repListHtml}${cashTakeaway}`;
}
function applySharedState(state){
 const oldQuote=quote;
 const prevWindows=settings?.ac_heating_windows;
 const incoming={...state.settings};
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile)delete incoming.baseline_cooling_kwh;
 settings=prepareScenarioSettings(incoming,true);normalizeBatterySettings(settings);quote=state.quote||{line_items:[]};if(!Array.isArray(quote.line_items))quote.line_items=[];
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile){
  delete settings.baseline_cooling_kwh;
  schema.defaults={...schema.defaults,...state.defaults};delete schema.defaults.baseline_cooling_kwh;
  schema.records=(schema.records||[]).filter(r=>r.name!=='baseline_cooling_kwh').map(r=>({...r,value:settings[r.name]??r.value}));
 }else{schema.defaults=state.defaults;schema.records=state.records?.map(record=>({...(schema.records||[]).find(r=>r.name===record.name),...record}));}
 refreshConditionalControls();
 updateGasEliminatedLabels();
 updateHpNotices();
 if(typeof syncSolarBatteryStep==='function')syncSolarBatteryStep();
 if(typeof markBatterySizingStale==='function')markBatterySizingStale();
 if(typeof markSolarSizingStale==='function')markSolarSizingStale();
 if(typeof invalidateBehaviorAndSensitivity==='function')invalidateBehaviorAndSensitivity();
 if(typeof invalidateAcHeatingComparison==='function')invalidateAcHeatingComparison();
 if(typeof invalidateGasCostsAudit==='function')invalidateGasCostsAudit();
 if(typeof invalidateDayhour==='function')invalidateDayhour();
 if(hasInvalidAcScheduleDraft()){
  reconcileAcScheduleDraftWithRemote(settings.ac_heating_windows);
 }
 if(!sharedUiReady)return;
 for(const record of schema.records){
  syncSetting(record.name,typeof sharedSync!=='undefined'&&sharedSync?.dirtySettings?.has(record.name)?document.activeElement:null);
  const nodes=document.querySelectorAll?.(`[data-control="${record.name}"]`);
  nodes?.forEach?.(node=>{
   const tag=node.querySelector?.('.tag'),source=node.querySelector?.('.control-source')||node.querySelectorAll?.('small')?.[1];
   if(tag){tag.textContent=record.status;tag.className=`tag ${record.status}`;}
   if(source){source.textContent=`Source: ${record.source}`;source.title=record.source;}
  });
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(record.name);
 }
 if(hasInvalidAcScheduleDraft()&&typeof settingValuesEqual==='function'&&!settingValuesEqual(prevWindows,settings.ac_heating_windows)){
  if(typeof renderControls==='function')renderControls();
 }
 if(JSON.stringify(oldQuote?.line_items)!==JSON.stringify(quote?.line_items)&&typeof renderLines==='function')renderLines();
 if(typeof syncQuoteFields==='function')syncQuoteFields();if(optimizerResult&&typeof renderCandidates==='function')renderCandidates();if(typeof renderRoofLimitsComparison==='function')renderRoofLimitsComparison();
 if(typeof updateModifiedBadge==='function')updateModifiedBadge();
 if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
 applyControlFilters();
}
function stageSharedChanges(replace=false){if(sharedSync?.update(settings,quote,{replace}))requestId++;}
async function pollSharedState(){
 if(!sharedSync)return;
 const changed=await sharedSync.poll();
 if(sharedSync.lastPollSucceeded&&sharedSync.retryNeeded&&sharedSync.pending())await recompute();
 else if(changed)await recompute({sync:false});
}
function inputFingerprint(s=settings,q=quote){if(typeof activeResultProfile!=='undefined'&&activeResultProfile){s=deepClone(s);delete s.hasItemized;cleanSettingsForBackend(s);}const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;return JSON.stringify(canonical([s,q]));}
function acceptedLoanInputsCurrent(){return !loanPending&&Boolean(current)&&acceptedInputFingerprint===inputFingerprint();}
function invalidateLoanSolve(){loanSolveToken++;loanSolveResult=null;if($('loanSolveResult'))$('loanSolveResult').textContent='';if($('loanSolveStatus'))$('loanSolveStatus').textContent='';if($('solveLoanAmount'))$('solveLoanAmount').disabled=true;}
function getMetricDefAnchor(label){
 const l=String(label||'').toLowerCase();
 if(l.includes('npv'))return '#def-npv';
 if(l.includes('payback'))return '#def-payback';
 if(l.includes('autarky'))return '#def-autarky';
 if(l.includes('self-consumption')||l.includes('self consumption'))return '#def-self-consumption';
 if(l.includes('year-1')||l.includes('year 1'))return '#def-year1';
 if(l.includes('lcoe'))return '#def-lcoe';
 return null;
}
function flushSharedChangesWithoutEvaluation(){if(sharedSync?.pending())sharedSync.flush().catch(()=>{});}
function getWeatherEnsembleMeta(source){
	const active = typeof current !== 'undefined' ? current?.result : null;
	const res = source || active;
	const yrs = Array.isArray(res?.weather_years) ? res.weather_years
		: Array.isArray(res?.settings?.weather_years) ? res.settings.weather_years
		: (Array.isArray(source?.years) ? source.years : (Array.isArray(active?.weather_years) ? active.weather_years : null));
	const count = (yrs && yrs.length) || res?.weather_count || res?.settings?.weather_count || (typeof source === 'number' ? source : 0) || active?.weather_count || 0;
	const rangeLabel = (yrs && yrs.length > 1) ? `${yrs[0]}–${yrs[yrs.length - 1]}` : (yrs && yrs.length === 1 ? `${yrs[0]}` : '');
	const countLabel = count ? `${count}-year` : 'historical';
	const runsLabel = count ? `${count} historical weather runs` : 'historical weather runs';
	const yearsDesc = count ? `${count} historical weather years` : 'historical weather ensemble';
	const analoguesDesc = count ? `${count} weather analogues` : 'historical weather analogues';
	return { count, years: yrs, rangeLabel, countLabel, runsLabel, yearsDesc, analoguesDesc };
}
function schedule(options={}){if(typeof invalidateBehaviorAndSensitivity==='function')invalidateBehaviorAndSensitivity();if(typeof invalidateQuoteComparison==='function')invalidateQuoteComparison();if(typeof invalidateAcHeatingComparison==='function')invalidateAcHeatingComparison();if(typeof invalidateGasCostsAudit==='function')invalidateGasCostsAudit();if(typeof invalidateDayhour==='function')invalidateDayhour();normalizeBatterySettings(settings);if(optimizerResult&&(optimizerFingerprint!==optimizerStateFingerprint()||(typeof shouldBlockOptimizerForAcDraft==='function'&&shouldBlockOptimizerForAcDraft())))markOptimizerStale();if(typeof studyRequestId!=='undefined'){studyRequestId++;studyFingerprint=null;}loanPending=true;acceptedInputFingerprint=null;invalidateLoanSolve();if($('saveLoanSimulation'))$('saveLoanSimulation').disabled=true;stageSharedChanges(options.replace);clearTimeout(timer);if(shouldBlockAcRecompute(settings)||shouldBlockRoofRecompute(settings)){const roofErr=getRoofValidationError(settings);if(roofErr){if($('status'))$('status').textContent='Check inputs: roof capacity exceeded';showRoofValidationError(roofErr);}flushSharedChangesWithoutEvaluation();return;}$('status').textContent='Inputs changed…';timer=setTimeout(()=>recompute(options),450);if(candidates)renderCandidates();markBatterySizingStale();markSolarSizingStale();}
async function recompute({sync=true,replace=false}={}){
 loanPending=true;acceptedInputFingerprint=null;invalidateLoanSolve();if($('saveLoanSimulation'))$('saveLoanSimulation').disabled=true;
 markBatterySizingStale();
 markSolarSizingStale();
 if(typeof markOptimizerStale==='function')markOptimizerStale();
 if(typeof studyRequestId!=='undefined'){studyRequestId++;studyFingerprint=null;}
 if(typeof invalidateBehaviorAndSensitivity==='function')invalidateBehaviorAndSensitivity();
 if(typeof invalidateQuoteComparison==='function')invalidateQuoteComparison();
 if(typeof invalidateAcHeatingComparison==='function')invalidateAcHeatingComparison();
 if(typeof invalidateGasCostsAudit==='function')invalidateGasCostsAudit();
 if(typeof invalidateDayhour==='function')invalidateDayhour();
 if(sync)stageSharedChanges(replace);
 const id=++requestId;
 if(shouldBlockAcRecompute(settings)||shouldBlockRoofRecompute(settings)){
  const roofErr=getRoofValidationError(settings);
  if(roofErr){
   if($('status'))$('status').textContent='Check inputs: roof capacity exceeded';
   showRoofValidationError(roofErr);
  }
  if(sync&&sharedSync?.pending())try{await sharedSync.flush();}catch(_){}
  return;
 }
 try{
  $('status').textContent='Computing…';
  const sysTitle=$('systemTitle');
  if(sysTitle){
   sysTitle.setAttribute?.('aria-busy','true');
   sysTitle.innerHTML='<span class="progress-spinner" aria-hidden="true"></span> Calculating…';
  }
  if(sync&&sharedSync)await sharedSync.flush();
  if(id!==requestId||shouldBlockAcRecompute(settings)||shouldBlockRoofRecompute(settings))return;
  normalizeBatterySettings(settings);const inputSnapshot=deepClone(settings);delete inputSnapshot.hasItemized;cleanSettingsForBackend(inputSnapshot);const quoteSnapshot=deepClone(quote),fingerprint=inputFingerprint(settings,quoteSnapshot);
  const q=await api('/api/evaluate',{settings:inputSnapshot,quote:quoteSnapshot});if(id!==requestId||shouldBlockAcRecompute(settings)||shouldBlockRoofRecompute(settings))return;
  current=q;loanPending=false;acceptedInputFingerprint=fingerprint;if(typeof acceptAcHeatingComparison==='function')acceptAcHeatingComparison(q?.result||q);if(typeof acceptGasCostsAudit==='function')acceptGasCostsAudit(q?.result||q);if(typeof acceptDayhour==='function')acceptDayhour(q?.result||q);if(typeof acceptSummarySensitivity==='function')acceptSummarySensitivity(q?.result||q);render();if(!$('weatherYear').options.length)setupDayhour();else refreshDayhour();
  $('error').hidden=true;$('status').textContent='Scenario updated';$('latency').textContent=`Python computation ${nf(q.compute_seconds,2)} s`;if(typeof solarAppliedMessage!=='undefined'&&solarAppliedMessage&&$('solarApplyStatus'))$('solarApplyStatus').textContent=solarAppliedMessage.replace('Recalculating scenario…','Scenario updated.');
 }catch(e){if(id===requestId&&!shouldBlockAcRecompute(settings)&&!shouldBlockRoofRecompute(settings)){loanPending=true;if($('saveLoanSimulation'))$('saveLoanSimulation').disabled=true;const sysTitle=$('systemTitle');if(sysTitle){sysTitle.removeAttribute?.('aria-busy');if(sysTitle.textContent.includes('Calculating'))sysTitle.textContent='Calculation failed';}if($('hpCardsEl')&&$('hpCardsEl').textContent.includes('Calculating')){$('hpCardsEl').textContent='';const failedCard=document.createElement('div');failedCard.className='card';const lbl=document.createElement('div');lbl.className='label';lbl.textContent='Hourly dispatch';failedCard.appendChild(lbl);const val=document.createElement('div');val.className='value bad';val.textContent='Failed';failedCard.appendChild(val);const sm=document.createElement('small');sm.textContent='Review calculation error and inputs';failedCard.appendChild(sm);$('hpCardsEl').appendChild(failedCard);}$('status').textContent='Calculation failed';showError(e);}}
}
function metricCard(label,r,type,extra='',featured=false,cardId=null){
 let valStr='—',rangeStr='';
 if(r!=null){
  if(typeof r==='number'){
   valStr=fmt(r,type);
  }else if(type==='years'&&r.discounted_payback_of_mean_cash_flows!==undefined){
   valStr=r.discounted_payback_of_mean_cash_flows===null?'Not reached':fmt(r.discounted_payback_of_mean_cash_flows,type);
   if(r.min!=null&&r.max!=null){
    rangeStr=`Historical min–max ${range(r,type)}`;
   }
  }else if(type==='years'&&r.payback_of_mean!==undefined){
   valStr=r.payback_of_mean===null?'Not reached':fmt(r.payback_of_mean,type);
   if(r.min!=null&&r.max!=null){
    rangeStr=`Historical min–max ${range(r,type)}`;
   }
  }else if(type==='pct'&&(r.ensembleAutarky!=null||r.headline!=null)){
   valStr=fmt(r.ensembleAutarky??r.headline,type);
   if(r.min!=null&&r.max!=null){
    rangeStr=`Historical min–max ${range(r,type)}`;
   }
  }else if((type==='eur'||label.includes('NPV'))&&r.mean!=null){
   valStr=fmt(r.mean,type);
   if(r.min!=null&&r.max!=null){
    rangeStr=`Historical min–max ${range(r,type)}`;
   }
  }else if(r.median!=null){
   valStr=fmt(r.median,type);
   if(r.min!=null&&r.max!=null){
    rangeStr=`Historical min–max ${range(r,type)}`;
   }
  }else if(r.min!=null){
   valStr=range(r,type);
  }else if(type==='years'&&r.recovered_weather_years===0){
   valStr='Not reached';
  }else{
   valStr=range(r,type);
  }
 }
 const anchor=typeof getMetricDefAnchor==='function'?getMetricDefAnchor(label):null;
 const labelHtml=anchor?`<a href="${anchor}" class="metric-anchor-link" onclick="if(typeof switchView==='function')switchView('assumptions');" title="View metric definition">${label}</a>`:label;
 return `<div class="card ${featured?'featured':''}" ${cardId?`id="${cardId}"`:''}><div class="label">${labelHtml}</div><div class="value">${valStr}</div>${rangeStr?`<div class="metric-range">${rangeStr}</div>`:''}<small>${extra||'Historical weather range · pending assumptions'}</small></div>`;
}
function packagePaybackCards(result){
 if(result.settings.load_scenario==='baseline')return '';
 const f=result.finance,packageLabel='Whole-house package',simple=f.whole_package.simple_payback,discounted=f.whole_package.discounted_payback;
 return metricCard(`${packageLabel.toUpperCase()} PAYBACK`,simple,'years',`${nf(simple?.recovered_weather_years??0)} weather cases recover · includes conversion cost and avoided historic gas/electric bills`)+metricCard(`DISCOUNTED ${packageLabel.toUpperCase()} PAYBACK`,discounted,'years',`${nf(discounted?.recovered_weather_years??0)} weather cases recover · ${fmt(f.nominal_discount_rate,'pct')} nominal discount rate`);
}
function render(){if(typeof activeResultProfile!=='undefined'&&activeResultProfile){const h=$('profileResultHeader');if(h)h.innerHTML=renderProfileDisclosures(current._presentation);const ha=$('profileResultHeaderAssumptions');if(ha)ha.innerHTML=renderProfileDisclosures(current._presentation);activeResultJob=current._ownedJob||null;for(const id of ['ownedReportHtml','ownedReportMarkdown','ownedReportCsv'])if($(id))$(id).disabled=!activeResultJob;const main=document.querySelector('main');if(main)main.hidden=false;updateActionableWarnings();}const r=current.result,m=r.metrics,f=r.finance,en=r.energy;
 const meanWeatherOption=$('weatherRepresentative')?.querySelector?.('option[value="mean"]');
 if(meanWeatherOption)meanWeatherOption.textContent=`${getWeatherEnsembleMeta(r).count || 'Historical'}-year arithmetic mean · P10–P90 spread`;
 const subEl=$('systemSubtitle');
 if(subEl){
  subEl.textContent=`${nf(r.layout.kwp,2)} kWp · ${nf(r.settings.battery_kwh,2)} kWh`;
 }
 const panelCount = r.layout?.panel_count;
 const panelWatts = r.settings?.panel_watts ?? (panelCount && r.layout?.kwp ? Math.round((r.layout.kwp * 1000) / panelCount) : null);
 const panelsVal = panelCount != null ? (panelWatts != null ? `${nf(panelCount)} × ${nf(panelWatts)} Wp` : `${nf(panelCount)} panels`) : '—';
 const panelsDesc = panelCount != null ? `${nf(panelCount)} panels${panelWatts != null ? ` at ${nf(panelWatts)} Wp each` : ''}${r.layout?.kwp != null ? ` (${nf(r.layout.kwp, 2)} kWp total)` : ''}` : 'Panel configuration unavailable';
 const invKw = r.settings?.inverter_kw;
 const invVal = invKw != null ? `${nf(invKw, 2)} kW AC` : '—';
 const invDesc = invKw != null ? `Inverter continuous AC output capacity: ${nf(invKw, 2)} kW AC` : 'Inverter AC capacity unavailable';
 const isDc = (r.effective_pv_battery_coupling || r.settings?.pv_battery_coupling) === 'dc_coupled';
 const couplingVal = isDc ? 'DC-coupled' : 'AC-equivalent';
 const couplingDesc = isDc ? 'DC-coupled battery charging from solar DC surplus' : 'AC-equivalent PV dispatch with inverter clipping before battery';
 const isItemized = typeof isItemizedPricingActive === 'function' && isItemizedPricingActive();
 const costSourceVal = isItemized ? 'Itemized quote' : (settings.quote_total != null ? 'Manual total' : 'Modeled estimate');
 const costSourceDesc = isItemized ? 'Pricing derived from itemized quote line items' : (settings.quote_total != null ? 'Pricing based on manual quote total' : 'Pricing from modeled benchmark estimate');
 const loanPrincipal = f?.loan_principal_eur;
 const financingVal = loanPrincipal == null ? '—' : (loanPrincipal > 0 ? 'Loan' : 'Cash');
 const financingDesc = loanPrincipal == null ? 'Financing details unavailable' : (loanPrincipal > 0 ? `Financed with loan (${fmt(loanPrincipal, 'eur')} principal)` : '100% equity / cash purchase');
 const settingsPills = [
  {label: 'Load scenario', value: r.settings?.load_scenario ? title(r.settings.load_scenario) : '—', desc: r.settings?.load_scenario ? `Active demand profile: ${title(r.settings.load_scenario)}` : 'Load scenario unavailable'},
  {label: 'Panels', value: panelsVal, desc: panelsDesc},
  {label: 'Inverter', value: invVal, desc: invDesc},
  {label: 'Coupling', value: couplingVal, desc: couplingDesc},
  {label: 'Cost source', value: costSourceVal, desc: costSourceDesc},
  {label: 'Financing', value: financingVal, desc: financingDesc}
 ];
 $('systemTags').innerHTML = settingsPills.map(p =>
  `<span class="summary-setting" title="${esc(p.desc)}"><span class="summary-setting-label">${esc(p.label)}</span> <span class="summary-setting-value">${esc(p.value)}</span></span>`
 ).join('');
 renderVerdictStrip(r);
 updateAssumptionsBadge(r);
 if($('moneyBasisSelect'))$('moneyBasisSelect').value=r.settings.cashflow_mode||'real';
 const energyRows=[['Demand','load_kwh'],['PV generation','pv_kwh'],['Direct solar','direct_kwh'],['Battery delivered','discharge_kwh'],['Grid import','grid_import_kwh'],['Grid export','export_kwh'],['Battery losses','battery_loss_kwh'],['Clipped PV','clipped_kwh']];
 if($('energyBalance'))$('energyBalance').innerHTML=energyRows.map(([label,key])=>{
  const item=en[key];
  const val=(item&&typeof item==='object')?(item.mean??item.median??item):item;
  const numStr=typeof val==='number'?`${nf(Math.round(val))} kWh`:`${range(item)} kWh`;
  return `<div class="barrow"><span>${label}</span><div class="v">${numStr}</div></div>`;
 }).join('')+`<p>Gross self-consumption <b>${fmt(en.self_consumption?.mean??en.self_consumption?.median??en.self_consumption,'pct')}</b><br>Useful self-consumption <b>${fmt(en.useful_self_consumption?.mean??en.useful_self_consumption?.median??en.useful_self_consumption,'pct')}</b></p>`;
 const capexSourceLabel=isItemizedPricingActive()?'itemized quote':(settings.quote_total!=null?'manual quote':'modeled benchmark');
 if($('comparisons'))$('comparisons').innerHTML=`<div class="barrow"><span>PV + battery capex <small class="muted">(${capexSourceLabel})</small></span><b class="v">${fmt(f.capex_eur,'eur')}</b></div><div class="barrow"><span>Conversion capex</span><b class="v">${fmt(f.conversion_capex_eur,'eur')}</b></div><div class="barrow"><span>PV-only NPV</span><b class="v">${range(m.npv_eur,'eur')}</b></div><div class="barrow"><span>Whole-house NPV</span><b class="v">${range(m.package_npv_eur,'eur')}</b></div><div class="barrow"><span>Equity NPV</span><b class="v">${range(m.equity_npv_eur,'eur')}</b></div><p class="muted">Deduction actually usable each year: ${fmt(f.annual_deduction_eur,'eur')}. Nominal discount rate ${fmt(f.nominal_discount_rate,'pct')}; real rate ${fmt(f.real_discount_rate,'pct')}.</p>`;
 renderExportValuation(r);
 const displayCash=displayCashRows(r.annual_cashflows);
 if($('cashFlowDescription'))$('cashFlowDescription').textContent=cashFlowLabel(r.settings.cashflow_mode);
 renderCashChart();renderEnergyCharts();
 renderConfigVsRecommended();
 renderSummarySensitivity();
 renderAnnualEnergyFlow();
 renderHeatPumpReport(r);
 renderAcHeatingComparison(r);
 const behView=$('behavior');
 if(behView&&!behView.hidden&&typeof refreshBehaviorViews==='function'){
  refreshBehaviorViews();
 }
 renderSummaryCashTable(r, displayCash);
 if($('method'))$('method').innerHTML=`<dl class="cash-method-items">
  <dt>Cash basis</dt><dd>Year 0 records the initial outlay. Annual and cumulative cash are undiscounted, shown as real installation-year purchasing power or nominal payment-year euros.</dd>
  <dt>Discounting</dt><dd>NPV discounts operational cash annual year-end with monthly loan repayments at the nominal rate: (1 + real rate) × (1 + inflation) − 1, or equivalently discounts real cash at the real rate. NPV is invariant across currency display modes.</dd>
  <dt>Payback definitions</dt><dd>Solar payback compares the same demand without solar; whole-house payback compares historic electricity and gas bills and includes conversion costs. Recovery must persist after replacements. Headline metrics use the historical-weather ensemble-mean cash flows; individual weather paths define the scenario envelope.</dd>
  <dt>Loan treatment</dt><dd>Personal recovery starts after loan proceeds and subtracts principal plus interest. Full-cost recovery counts the purchase upfront and subtracts interest only, so principal is counted once.</dd>
  <dt>Tax deduction</dt><dd>Ten nominal installments are capped by available annual IRPEF; unused amounts are lost. Conversion capex is excluded from eligibility. Deduction and replacements are already included in cash flow.</dd>
 </dl>`;
 if($('limitations'))$('limitations').innerHTML=(r.limitations||[]).map(s=>`<li>${esc(s)}</li>`).join('');
 syncQuotePricingDescription();
 if($('claims'))$('claims').innerHTML=(current.claims||[]).map(c=>`<p class="${c.outside_weather_range?'bad':'good'}"><b>${title(c.field)}</b>: ${c.outside_weather_range?'Outside':'Within'} the simulated weather range (${range(c.simulated,c.field.includes('pct')?'pct':c.field.includes('years')?'years':'number')}).</p>`).join('')+(current.warnings||[]).map(w=>`<p class="muted">${esc(w)}</p>`).join('');
 renderLedger(r);
 renderSaved();
 renderLoanPanel();
 renderAdjacentPrices();
 if(typeof renderGasCostsAudit==='function')renderGasCostsAudit();
 renderLcoeCards();
 renderInstallerChecklist();
 if(typeof renderRoofLimitsComparison==='function')renderRoofLimitsComparison();
 if(typeof renderRoofPills==='function')renderRoofPills();
 if(typeof renderDesignUnifiedComparisonTable==='function')renderDesignUnifiedComparisonTable(typeof solarData!=='undefined'?solarData:null);
 if(typeof renderQuoteComparisonSheet==='function')renderQuoteComparisonSheet();
}
function renderExportValuation(r){
 const targetCards=$('exportValuationCards');
 const targetComp=$('exportSensitivityComparison');
 const targetNote=$('exportValuationNote');
 if(!targetCards||!r.export_price_analysis)return;
 const a=r.export_price_analysis;
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile){
  targetCards.innerHTML=renderProfileMarketMetadata(activeResultProfile,schema?.catalog);
  if(targetComp)targetComp.innerHTML=table(['Selected export valuation','Value'],[['Baseload level',fmt(a.baseload_price_eur_kwh,'lcoe')+' / kWh'],['Export-weighted price',fmt(a.export_weighted_captured_price_eur_kwh,'lcoe')+' / kWh']]);
  if(targetNote)targetNote.textContent='Wholesale export valuation uses the selected pinned series and mode. PUN is a comparison benchmark. Fixed retail contracts only.';
  return;
 }
 const comp=a.flat_benchmark_comparison||{};
 const isEmpirical=a.profile_type==='empirical';
 const isActual=a.profile_source==='actual_hourly_gme_profile';
 const profileLabel=a.profile_label||(isActual?'Actual chronological hourly GME':(isEmpirical?'Empirical CSUD':'Flat benchmark'));
 const windowLabel=a.window==='oct2025_sep2026'?'Oct 2025 – Sep 2026':(a.window==='calendar_2025'?'Calendar 2025':a.window);
 const profileSubtitle=isActual?'Supplied hourly profile':(isEmpirical?`${windowLabel} (12×24 curve)`:`Baseload × ${fmt(a.legacy_capture_factor,'pct')} capture`);
 const captureSubtitle=a.legacy_capture_factor_active?`Fixed ${fmt(a.legacy_capture_factor,'pct')} multiplier active`:`Empirical outcome (${fmt(a.legacy_capture_factor,'pct')} inactive)`;
 const activeYear1Gross=a.legacy_capture_factor_active?comp.flat_year1_pzo_gross_eur:comp.curve_year1_pzo_gross_eur;

 targetCards.innerHTML=`
   <div class="card"><div class="label">Wholesale profile</div><div class="value">${profileLabel}</div><small>${profileSubtitle}</small></div>
   <div class="card"><div class="label">Baseload wholesale price</div><div class="value">${fmt(a.baseload_price_eur_kwh,'eur2')} <small>/ kWh</small></div><small>${nf(a.baseload_price_eur_kwh*1000,1)} €/MWh baseload reference</small></div>
   <div class="card"><div class="label">Captured export price</div><div class="value">${a.export_weighted_captured_price_eur_kwh!=null?`${fmt(a.export_weighted_captured_price_eur_kwh,'eur2')} <small>/ kWh</small>`:'No exports'}</div><small>${a.export_weighted_captured_price_eur_kwh!=null?`${nf(a.export_weighted_captured_price_eur_kwh*1000,1)} €/MWh (dispatch-weighted)`:'Zero grid exports modeled'}</small></div>
   <div class="card"><div class="label">Solar capture factor</div><div class="value">${a.export_weighted_capture_factor!=null?fmt(a.export_weighted_capture_factor,'pct'):'Unavailable'}</div><small>${captureSubtitle}</small></div>
   <div class="card"><div class="label">Year 1 gross export value</div><div class="value">${fmt(activeYear1Gross,'eur')}</div><small>Before GSE fees, PMG & income tax</small></div>
 `;
 if(targetComp){
  const empiricalGrossText=isEmpirical&&comp.curve_year1_pzo_gross_eur!=null?fmt(comp.curve_year1_pzo_gross_eur,'eur'):'Not calculated';
  const empiricalCapPriceText=isEmpirical&&a.export_weighted_captured_price_eur_kwh!=null?`${fmt(a.export_weighted_captured_price_eur_kwh,'eur2')}/kWh (${nf(a.export_weighted_captured_price_eur_kwh*1000,1)} €/MWh)`:(isEmpirical?'—':'Not calculated');
  const empiricalCapFactorText=isEmpirical&&a.export_weighted_capture_factor!=null?fmt(a.export_weighted_capture_factor,'pct'):(isEmpirical?'—':'Not calculated');
  const rowTitle=isActual?'<strong>Actual chronological hourly GME</strong>':`<strong>Empirical CSUD shape</strong> (${windowLabel})`;

  targetComp.innerHTML=`
    <div class="table-wrap">
      <table>
        <thead><tr><th>Valuation method</th><th>Capture factor</th><th>Captured unit price</th><th>Year 1 gross export revenue</th><th>Status</th></tr></thead>
        <tbody>
          <tr><td>${rowTitle}</td><td>${empiricalCapFactorText}</td><td>${empiricalCapPriceText}</td><td>${empiricalGrossText}</td><td>${isEmpirical?'<span class="good">Active</span>':'<span class="muted">Not active</span>'}</td></tr>
          <tr><td><strong>Flat solar capture benchmark</strong> (fixed ${fmt(a.legacy_capture_factor,'pct')})</td><td>${fmt(a.legacy_capture_factor,'pct')}</td><td>${fmt(comp.flat_captured_price_eur_kwh,'eur2')}/kWh (${nf(comp.flat_captured_price_eur_kwh*1000,1)} €/MWh)</td><td>${fmt(comp.flat_year1_pzo_gross_eur,'eur')}</td><td>${a.legacy_capture_factor_active?'<span class="good">Active</span>':'<span class="muted">Benchmark alternative</span>'}</td></tr>
        </tbody>
      </table>
    </div>
    ${isEmpirical&&comp.delta_year1_pzo_gross_eur!=null&&Math.abs(comp.delta_year1_pzo_gross_eur)>0.01?`<p class="muted" style="margin-top:6px;">Export revenue difference under empirical curve vs flat benchmark: <strong>${comp.delta_year1_pzo_gross_eur>0?'+':''}${fmt(comp.delta_year1_pzo_gross_eur,'eur')} / year</strong>. ${comp.note||''}</p>`:(comp.note?`<p class="muted" style="margin-top:6px;">${comp.note}</p>`:'')}
  `;
 }
 if(targetNote){
  targetNote.textContent=isEmpirical
    ?`Empirical CSUD curve active: wholesale export valuation directly reflects hourly solar exports coinciding with lower midday market prices. The legacy capture factor (${fmt(a.legacy_capture_factor,'pct')}) is inactive on this curve to prevent double discounting. Night market clearing prices generate zero solar export revenue because solar generation is zero at night. Export income uses hourly zonal prices with annual aggregate PMG true-up, followed by GSE management fees and marginal income taxes.`
     :`Flat solar capture benchmark active: a constant capture factor (${fmt(a.legacy_capture_factor,'pct')}) is applied once to the baseload forecast. No empirical month/hour shape is applied.`;
  }
 }

function renderVerdictStrip(r){
 if(!$('metrics')||!r)return;
 const data=getScopeCashMetrics(r,summaryScope,summaryCash);
 if(!data)return;
 const f=r.finance;
 const m=r.metrics||{};
 const isReal=r.settings.cashflow_mode==='real';

 const verdictCard=(label,obj,type,extra='',featured=false,cardId=null)=>{
  let valStr='—',rangeStr='';
  if(obj!=null){
   if(typeof obj==='number'){
    valStr=fmt(obj,type);
   }else if(type==='years'&&obj.discounted_payback_of_mean_cash_flows!==undefined){
    valStr=obj.discounted_payback_of_mean_cash_flows===null?'Not reached':fmt(obj.discounted_payback_of_mean_cash_flows,type);
    if(obj.min!=null&&obj.max!=null){
     rangeStr=`Historical min–max ${range(obj,type)}`;
    }
   }else if(type==='years'&&obj.payback_of_mean!==undefined){
    valStr=obj.payback_of_mean===null?'Not reached':fmt(obj.payback_of_mean,type);
    if(obj.min!=null&&obj.max!=null){
     rangeStr=`Historical min–max ${range(obj,type)}`;
    }
   }else if(type==='pct'&&(obj.ensembleAutarky!=null||obj.headline!=null)){
    valStr=fmt(obj.ensembleAutarky??obj.headline,type);
    if(obj.p10!=null&&obj.p90!=null){
     rangeStr=`Historical P10–P90 ${fmt(obj.p10,type)}–${fmt(obj.p90,type)}`;
    }else if(obj.min!=null&&obj.max!=null){
     rangeStr=`Historical min–max ${range(obj,type)}`;
    }
   }else if((type==='eur'||label.includes('NPV'))&&obj.mean!=null){
    valStr=fmt(obj.mean,type);
    if(obj.min!=null&&obj.max!=null){
     rangeStr=`Historical min–max ${range(obj,type)}`;
    }
   }else if(obj.median!=null){
    valStr=fmt(obj.median,type);
    if(obj.min!=null&&obj.max!=null){
     rangeStr=`Historical min–max ${range(obj,type)}`;
    }
   }else if(obj.min!=null){
    valStr=range(obj,type);
   }else if(type==='years'&&obj.recovered_weather_years===0){
    valStr='Not reached';
   }else{
    valStr=range(obj,type);
   }
  }
  const anchor=typeof getMetricDefAnchor==='function'?getMetricDefAnchor(label):null;
  const labelHtml=anchor?`<a href="${anchor}" class="metric-anchor-link" onclick="if(typeof switchView==='function')switchView('assumptions');" title="View metric definition">${label}</a>`:label;
  const rangeHtml=rangeStr?`<div class="metric-range">${rangeStr}</div>`:`<div class="metric-range metric-range-placeholder" aria-hidden="true">&nbsp;</div>`;
  return `<div class="card ${featured?'featured':''}" ${cardId?`id="${cardId}"`:''}><div class="label">${labelHtml}</div><div class="value">${valStr}</div>${rangeHtml}<small>${extra||'Historical weather range · pending assumptions'}</small></div>`;
 };

 const capexCard=verdictCard(data.capexTitle,data.capexVal,'eur',data.capexExtra,false,'metricSummaryCapex');
 const npvCard=verdictCard(data.npvTitle,data.npvObj,'eur',`Historical-weather ensemble mean · ${data.npvExtra}`,true,'metricSummaryNpv');

 // Explicit discounted Before loan / After loan comparison in KPI secondary
 const cashPb = summaryScope === 'solar'
   ? (f.pv_incremental?.discounted_payback_of_mean_cash_flows !== undefined ? f.pv_incremental.discounted_payback_of_mean_cash_flows : m.discounted_payback_years)
   : (f.whole_package?.discounted_payback_of_mean_cash_flows !== undefined ? f.whole_package.discounted_payback_of_mean_cash_flows : (f.whole_package?.discounted_payback !== undefined ? f.whole_package.discounted_payback : f.whole_package?.discounted_payback_years));
 const loanPbScope = summaryScope === 'solar'
   ? (f.solar_equity ?? f.equity)
   : (f.package_equity ?? f.whole_package);
 const loanPb = loanPbScope?.discounted_payback_of_mean_cash_flows !== undefined
   ? loanPbScope.discounted_payback_of_mean_cash_flows
   : loanPbScope?.discounted_payback;
 const cashPbStr = (cashPb === null || cashPb?.recovered_weather_years === 0) ? 'Not reached' : fmt(cashPb?.median ?? cashPb, 'years');
 const loanPbStr = (loanPb === null || loanPb?.recovered_weather_years === 0) ? 'Not reached' : fmt(loanPb?.median ?? loanPb, 'years');
 const pbComparisonExtra = `Discounted comparison: Before loan ${cashPbStr} · After loan ${loanPbStr}`;

 const unpaidPayback = data.pbObj?.unrecovered_weather_years;
 const paybackWeatherNote = unpaidPayback != null ? ` · ${unpaidPayback}/${data.pbObj.weather_count} weather scenarios unrecovered` : '';
 const pbCard=verdictCard(data.pbTitle,data.pbObj,'years',`${data.pbExtra} · ${pbComparisonExtra}${paybackWeatherNote}<span class="metric-sub-link-wrap"><a href="#financing" class="metric-sub-link">Full loan recovery analysis →</a></span>`,false,'metricSummaryPayback');
 const formatKwhAnchor = obj => {
  if (obj == null) return null;
  if (typeof obj === 'number') return `${nf(obj, Number.isInteger(obj) ? 0 : 1)} kWh/yr`;
  if (obj.min != null && obj.max != null && Math.abs(obj.max - obj.min) >= 1) {
   return `${range(obj)} kWh/yr`;
  }
  if (obj.mean != null) return `${nf(obj.mean, Number.isInteger(obj.mean) ? 0 : 1)} kWh/yr`;
  if (obj.median != null) return `${nf(obj.median, Number.isInteger(obj.median) ? 0 : 1)} kWh/yr`;
  return null;
 };

 const demandObj = data.demandKwh ?? r.energy?.load_kwh ?? r.metrics?.load_kwh ?? r.lifetime_energy?.[0]?.load_kwh;
 const pvObj = data.pvKwh ?? r.energy?.pv_kwh ?? r.metrics?.pv_kwh ?? r.lifetime_energy?.[0]?.pv_kwh;
 const demandAnchor = formatKwhAnchor(demandObj);
 const pvAnchor = formatKwhAnchor(pvObj);

 const demandLine = demandAnchor
   ? `Share of demand (${demandAnchor}) met by solar`
   : `Share of demand met by solar`;
 const genLine = pvAnchor
   ? `Share of generation (${pvAnchor}) used on-site`
   : `Share of generation used on-site`;

 const autarkyExtra = `<span class="metric-def-line">${demandLine}</span><span class="metric-def-line">1 − mean grid imports / mean demand${r.headline_energy_flows?.forecast_year ? ` · ${r.headline_energy_flows.forecast_year}` : ''}</span><span class="metric-def-line"><strong>Self-consumption ${data.selfConsumption?.headline != null ? fmt(data.selfConsumption.headline, 'pct') : range(data.selfConsumption, 'pct')}</strong><br>${genLine}</span>`;
  const autarkyBounds = (data.autarky && data.autarky.p10 != null && data.autarky.p90 != null)
    ? { min: data.autarky.p10, max: data.autarky.p90, label: 'P10–P90: ' }
    : (data.autarky && data.autarky.min != null && data.autarky.max != null)
      ? { min: data.autarky.min, max: data.autarky.max, label: '' }
      : null;
  const autarkyRangeInline = autarkyBounds
    ? ` <small class="value-range" style="font-size:0.55em;font-weight:normal;color:#62756e;">(${autarkyBounds.label}${fmt(autarkyBounds.min,'pct')}–${fmt(autarkyBounds.max,'pct')})</small>`
    : '';
  const autarkyCard=verdictCard('SOLAR COVERAGE',data.autarky,'pct',autarkyExtra,false,'metricSummaryAutarky')
    .replace('<div class="value">','<div class="value">Autarky ')
    .replace(/(<div class="value">[^<]+)(<\/div>)/,`$1${autarkyRangeInline}$2`)
    .replace(/<div class="metric-range">[^<]*<\/div>/,autarkyRangeInline?'':'<div class="metric-range metric-range-placeholder" aria-hidden="true">&nbsp;</div>');

 $('metrics').innerHTML=capexCard+npvCard+pbCard+autarkyCard;

 // Verdict executive headline in systemTitle
 const meanNpv = data.npvObj?.mean ?? data.npvObj?.median ?? (typeof data.npvObj === 'number' ? data.npvObj : 0);
 const npvFmt = fmt(meanNpv, 'eur');

 // Never infer positive NPV count from payback; strictly use finance[flowKey].npv_by_weather, else unknown
 const weatherNpvList = f?.[data.flowKey]?.npv_by_weather ?? data.npvObj?.npv_by_weather;
 let weatherSummary = 'weather positive count unknown';
 if (Array.isArray(weatherNpvList) && weatherNpvList.length > 0) {
  const total = weatherNpvList.length;
  const pos = weatherNpvList.filter(v => v > 0).length;
  weatherSummary = `${pos} of ${total} weather years positive`;
 }

 const otherCash=summaryCash==='cash'?'loan':'cash';
 const otherData=getScopeCashMetrics(r,summaryScope,otherCash);
 const pbFmt=(obj)=>{
  if(!obj)return '—';
  if(typeof obj==='number')return fmt(obj,'years');
  if(obj.discounted_payback_of_mean_cash_flows!==undefined)return obj.discounted_payback_of_mean_cash_flows===null?'Not reached':fmt(obj.discounted_payback_of_mean_cash_flows,'years');
  if(obj.payback_of_mean!==undefined)return obj.payback_of_mean===null?'Not reached':fmt(obj.payback_of_mean,'years');
  if(obj.recovered_weather_years===0)return 'Not reached';
  return obj.median!=null?fmt(obj.median,'years'):range(obj,'years');
 };
 const activePbStr=pbFmt(data.pbObj);
 const otherPbStr=pbFmt(otherData?.pbObj);
 const activeBasisName=summaryCash==='cash'?'before loan':'after loan';
 const otherBasisName=summaryCash==='cash'?'after loan':'before loan';

 const verdictLabel=meanNpv>0?'Worth it':meanNpv<0?'Negative return':'Break-even';
 const verdictSentenceText=`${verdictLabel}: ${npvFmt} NPV, ${weatherSummary}, ${activePbStr} discounted cash recovery ${activeBasisName} (${otherPbStr} ${otherBasisName}).`;
 const sysTitle = $('systemTitle');
 if(sysTitle){
  sysTitle.removeAttribute?.('aria-busy');
  sysTitle.textContent = verdictSentenceText;
 }

 const subEl = $('systemSubtitle');
 if(subEl){
  subEl.textContent = `${nf(r.layout?.kwp??0,2)} kWp · ${nf(r.settings?.battery_kwh??0,2)} kWh`;
 }


  const scopeNote=$('summaryScopeNote');
  if(scopeNote){
   scopeNote.textContent='';
   scopeNote.style.display='none';
  }
}

function updateAssumptionsBadge(r){
 const badge=$('unconfirmedAssumptionsBadge');
 if(!badge)return;
 const assumptions=r?.assumptions||schema?.records||[];
 const pending=assumptions.filter(a=>['proposed','placeholder','missing','unconfirmed'].includes(a.status));
 const verified=assumptions.filter(a=>['measured','provided','confirmed','verified'].includes(a.status));
 const count=pending.length;
 if(count>0){
  badge.textContent=`⚠ ${count} unconfirmed assumptions`;
  badge.className='status-badge unconfirmed';
  badge.title=`${count} model inputs are proposed/placeholder engineering defaults; ${verified.length} inputs are measured or bill-provided. Click to review filtered unconfirmed assumptions.`;
  badge.href='#assumptions';
  badge.onclick=(e)=>{
   e.preventDefault();
   if(typeof switchView==='function')switchView('assumptions');
   if(typeof filterLedgerUnconfirmed==='function')filterLedgerUnconfirmed();
  };
 }else{
  badge.textContent='✓ Assumptions confirmed';
  badge.className='status-badge confirmed';
  badge.title='All key inputs have been verified.';
  badge.href='#assumptions';
  badge.onclick=(e)=>{
   e.preventDefault();
   if(typeof switchView==='function')switchView('assumptions');
   if(typeof resetLedgerFilter==='function')resetLedgerFilter();
  };
 }
}

let ledgerStatusFilter=null;
function filterLedgerUnconfirmed(){
 ledgerStatusFilter='unconfirmed';
 if(current?.result)renderLedger(current.result);
 const target=$('ledger');
 if(target&&typeof target.scrollIntoView==='function')target.scrollIntoView({behavior:'smooth'});
}
function resetLedgerFilter(){
 ledgerStatusFilter=null;
 if(current?.result)renderLedger(current.result);
}
let candidateEvalCache=new Map();
function summaryCandidateRequest(cand,baseSettings){
 const next={...baseSettings};
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile)next.roof_panels=profileCounts(cand);
 const candidateSettings=cand.settings||{};
 const keys=['panel_watts','pv_dispatch_model','pv_battery_coupling','inverter_kw','pv_max_kwp','battery_kwh','battery_modules','battery_module_kwh','battery_mixed_sizes','battery_secondary_modules','battery_secondary_module_kwh','storage_cost_mode','charge_kw','discharge_kw','roundtrip_efficiency','routing_priority','routing_switch_hour','scheduled_start_month','scheduled_end_month','inverter_efficiency','ilr_ac_max','ilr_dc_max','clipping_ac_max','clipping_dc_max','battery_inverter_kw','battery_c_rate_max','battery_soc_min','battery_soc_max','battery_charge_efficiency','battery_discharge_efficiency','battery_cost_kwh','battery_module_cost_override','battery_secondary_module_cost_override','battery_module_extra_cost','battery_secondary_module_extra_cost','dormer_south_proxy'];
 for(const key of keys){
  if(candidateSettings[key]!==undefined)next[key]=candidateSettings[key];
  if(cand[key]!==undefined)next[key]=cand[key];
 }
 for(const roof of ((typeof activeResultProfile!=='undefined'&&activeResultProfile)?[]:['south','west','east','dormer','flat','secondary_south'])){
  const key=roof+'_panels';
  next[key]=cand.roof_counts?.[roof]??candidateSettings[key]??0;
 }
 if(cand.pv_inverter_kw!=null)next.inverter_kw=cand.pv_inverter_kw;
 // Preserve the active quote's price level when comparing a different layout.
 next.quote_total=cand.capex_eur??null;
 next.quote_vat_included=true;
 return {settings:next,quote:{line_items:[],price_basis:'summary_candidate',reference_settings:baseSettings}};
}
function evaluateCandidateIfNeeded(cand,baseSettings){
 const payload=summaryCandidateRequest(cand,baseSettings);
 const key=JSON.stringify(payload);
 if(candidateEvalCache.has(key))return candidateEvalCache.get(key);
 const entry={loading:true,result:null,error:null};
 candidateEvalCache.set(key,entry);
 const accepted=current?.result;
 const inputKey=typeof inputFingerprint==='function'?inputFingerprint(settings,quote):'';
 if(typeof api!=='function'){entry.loading=false;return entry;}
 api('/api/evaluate',payload).then(response=>{
  entry.result=response.result||response;
 }).catch(error=>{entry.error=error.message||String(error);}).finally(()=>{
  entry.loading=false;
  const stillCurrent=current?.result===accepted&&(typeof inputFingerprint!=='function'||inputFingerprint(settings,quote)===inputKey);
  if(stillCurrent)renderConfigVsRecommended();
 });
 return entry;
}

let summarySizingScheduledHash=null;
let summarySizingIdleHandle=null;

function scheduleSummarySizingCompute(){
 if(typeof solarStateFingerprint!=='function'||typeof runSolarSizingAnalysis!=='function')return;
 if(typeof shouldBlockSolarSizingForAcDraft==='function'&&shouldBlockSolarSizingForAcDraft())return;
 const fp=solarStateFingerprint();
 if(typeof isSolarSizingMatching==='function'&&isSolarSizingMatching())return;
 if(typeof solarInFlight!=='undefined'&&solarInFlight)return;
 if(summarySizingScheduledHash===fp)return;
 summarySizingScheduledHash=fp;

 const runJob=()=>{
  summarySizingIdleHandle=null;
  if(typeof solarInFlight!=='undefined'&&solarInFlight)return;
  if(typeof isSolarSizingMatching==='function'&&isSolarSizingMatching())return;
  if(typeof solarStateFingerprint==='function'&&solarStateFingerprint()!==fp)return;
  if(typeof shouldBlockSolarSizingForAcDraft==='function'&&shouldBlockSolarSizingForAcDraft())return;
  if(typeof runSolarSizingAnalysis==='function'){
   try{
    const p=runSolarSizingAnalysis();
    if(p&&typeof p.catch==='function')p.catch(()=>{});
   }catch(_){}
  }
 };

 if(typeof cancelIdleCallback==='function'&&summarySizingIdleHandle){
  cancelIdleCallback(summarySizingIdleHandle);
 }else if(summarySizingIdleHandle){
  clearTimeout(summarySizingIdleHandle);
 }

 if(typeof requestIdleCallback==='function'){
  summarySizingIdleHandle=requestIdleCallback(runJob,{timeout:2000});
 }else{
  summarySizingIdleHandle=setTimeout(runJob,100);
 }
}

function getActiveRoofLimits(){
 if(typeof solarData!=='undefined'&&solarData?.search_summary?.effective_roof_limits){
  return solarData.search_summary.effective_roof_limits;
 }
 if(typeof optimizerPrefs!=='undefined'&&optimizerPrefs?.panel_maxima){
  return optimizerPrefs.panel_maxima;
 }
 if(typeof defaultRoofLimits!=='undefined'){
  return defaultRoofLimits;
 }
 return {south:22,west:11,east:6,dormer:4,flat:8,secondary_south:5};
}

function isCandidateRoofFeasible(cand, limits){
 if(!cand)return false;
 const lims=limits||getActiveRoofLimits();
 if(!lims)return true;

 const roofs=(typeof activeResultProfile!=='undefined'&&activeResultProfile)?activeResultProfile.roofs.map(r=>r.id):['south','west','east','dormer','flat','secondary_south'];
 let hasPerRoofInfo=false;
 let sumPerRoof=0;

 for(const r of roofs){
  let count=null;
  if(cand.roof_counts&&cand.roof_counts[r]!==undefined){
   count=Number(cand.roof_counts[r]);
   hasPerRoofInfo=true;
  }else if(cand.settings&&cand.settings[r+'_panels']!==undefined){
   count=Number(cand.settings[r+'_panels']);
   hasPerRoofInfo=true;
  }else if(cand[r+'_panels']!==undefined){
   count=Number(cand[r+'_panels']);
   hasPerRoofInfo=true;
  }
  if(count!==null&&Number.isFinite(count)){
   sumPerRoof+=count;
   const lim=Number(lims[r]??0);
   if(count>lim)return false;
  }
 }

 const totalLimit=roofs.reduce((sum,r)=>{
  const n=Number(lims[r]??0);
  return sum+(Number.isFinite(n)&&n>0?n:0);
 },0);

 const totalPanels=Number(cand.panel_count??cand.panels??cand.settings?.panel_count??(hasPerRoofInfo?sumPerRoof:0));
 if(Number.isFinite(totalPanels)&&totalPanels>totalLimit)return false;

 if(!hasPerRoofInfo&&totalPanels>0){
  const activeRoofs=roofs.filter(r=>Number(lims[r]??0)>0);
  if(activeRoofs.length===1&&totalPanels>Number(lims[activeRoofs[0]])){
   return false;
  }
 }

 return true;
}

function renderConfigVsRecommended(){
 const el=$('configVsRecContent');
 if(!el)return;
 const r=current?.result;
 if(!r){
  el.innerHTML='<p class="muted">Scenario calculation pending.</p>';
  return;
 }
 scheduleSummarySizingCompute();
 const choices=(typeof solarData!=='undefined'&&solarData?.recommendation_choices)?solarData.recommendation_choices:null;

 // MUST use getScopeCashMetrics same as verdict
 const curMetrics=getScopeCashMetrics(r,summaryScope,summaryCash);
 const scalarNpv=value=>typeof value==='number'?value:(value?.mean??value?.median??null);
 const scalarPb=value=>{
  if(value===null||typeof value==='number')return value;
  if(!value||typeof value!=='object')return null;
  return value.discounted_payback_of_mean_cash_flows!==undefined?value.discounted_payback_of_mean_cash_flows:(value.median??null);
 };
 const currentNpv=scalarNpv(curMetrics?.npvObj)??0;
 const currentCapex=curMetrics?.capexVal??0;
 const currentInstalledCapex=(r.finance?.capex_eur??0)+(summaryScope==='package'?(r.finance?.conversion_capex_eur??0):0);
 const currentPb=scalarPb(curMetrics?.pbObj);
 const curUnleveredMetrics=summaryCash==='cash'?curMetrics:getScopeCashMetrics(r,summaryScope,'cash');
 const currentUnleveredPb=scalarPb(curUnleveredMetrics?.pbObj);
 const currentBat=r.settings?.battery_kwh??0;
 const currentPanels=r.layout?.panel_count??0;
 const currentKwp=r.layout?.kwp??0;

 const isScopeSolar=summaryScope==='solar';
 const isCashBasis=summaryCash==='cash';
 const scopeCaption=`${isScopeSolar?'Solar + battery':'Whole house'} · ${isCashBasis?'Before loan':'After loan'} · ${r.settings.cashflow_mode==='real'?'Real €':'Nominal €'}`;

 const getCandMetrics=(cand)=>{
  if(!cand)return null;
  const specs={panels:cand.panel_count??cand.panels,kwp:cand.kwp,bat:cand.battery_kwh};
  if(isSizingStale)return {...specs,stale:true};
  const hasSecondaryRoof=Boolean((cand?.roof_counts?.secondary_south??cand?.settings?.secondary_south_panels??cand?.secondary_south_panels??0)>0);

  const evaluated=evaluateCandidateIfNeeded(cand,r.settings);
  if(!evaluated.result){
   return {...specs,loading:evaluated.loading,error:evaluated.error,hasSecondaryRoof};
  }
  const activeM=getScopeCashMetrics(evaluated.result,summaryScope,summaryCash);
  const unlM=getScopeCashMetrics(evaluated.result,summaryScope,'cash');
  return {
   ...specs,
   npv:scalarNpv(activeM?.npvObj),
   payback:scalarPb(activeM?.pbObj),
   unleveredPayback:scalarPb(unlM?.pbObj),
   capex:(evaluated.result.finance?.capex_eur??0)+(isScopeSolar?0:(evaluated.result.finance?.conversion_capex_eur??0)),
   hasSecondaryRoof
  };
 };

 const cardData=(title,cls,cand)=>{
   if(!cand)return `<div class="card config-card ${cls}"><div class="label">${title}</div><small class="card-scope-caption">${scopeCaption}</small><div class="value">—</div><small>Run System design sizing to generate</small></div>`;
   const m=getCandMetrics(cand);
   const npv=m?.npv;
   const p=m?.panels,k=m?.kwp,b=m?.bat;
   const capex=m?.capex;
   const pb=m?.payback;
   const unlPb=m?.unleveredPayback;

   let valDisplay='—';
   let deltaLine='';
   if(npv!=null){
     valDisplay=fmt(npv,'eur');
     const dNpv=npv-currentNpv;
     const dNpvClass=dNpv>=0?'delta-pos':'delta-neg';
     const dNpvStr=`${dNpv>=0?'+':''}${fmt(dNpv,'eur')}`;

     const dCapex=capex!=null?capex-currentInstalledCapex:null;
     const dCapexStr=dCapex!=null?` · ΔCAPEX ${dCapex>=0?'+':''}${fmt(dCapex,'eur')}`:'';

     const pbValStr=(pb!=null&&Number.isFinite(Number(pb)))?`${nf(Number(pb),1)} y`:'Not reached';
     const dPb=(pb!=null&&currentPb!=null)?(Number(pb)-Number(currentPb)):null;
     const dPbStr=dPb!=null?` (Δ ${dPb>=0?'+':''}${nf(dPb,1)} y)`:'';
     const pbPart=`Payback ${pbValStr}${dPbStr}`;

     const unlPbValStr=(unlPb!=null&&Number.isFinite(Number(unlPb)))?`${nf(Number(unlPb),1)} y`:'Not reached';
     const dUnlPb=(unlPb!=null&&currentUnleveredPb!=null)?(Number(unlPb)-Number(currentUnleveredPb)):null;
     const dUnlPbStr=dUnlPb!=null?` (Δ ${dUnlPb>=0?'+':''}${nf(dUnlPb,1)} y)`:'';
     const unlPbPart=`Unlevered payback ${unlPbValStr}${dUnlPbStr}`;

     deltaLine=`<div class="config-delta-line"><span class="${dNpvClass}">ΔNPV ${dNpvStr}</span>${dCapexStr}</div><div class="config-delta-line delta-neutral">${pbPart} · ${unlPbPart}</div>`;
   }else if(m?.loading){
     valDisplay='<span class="muted" style="font-size:13px;">Evaluating…</span>';
     deltaLine=`<div class="config-delta-line delta-neutral">Calculating candidate metrics for the active basis</div>`;
   }else{
     valDisplay='<span class="muted" style="font-size:13px;">Unavailable</span>';
     deltaLine=`<div class="config-delta-line delta-neutral">${m?.stale?'Inputs changed · re-run sizing':m?.error?'Evaluation failed · re-run sizing':'Metrics not available for active scope/basis'}</div>`;
   }

   const secBadge=m?.hasSecondaryRoof?' <span class="badge secondary-roof-badge">Secondary roof required</span>':'';

   return `<div class="card config-card ${cls}"><div class="label">${title}${secBadge}</div><small class="card-scope-caption">${scopeCaption}</small><div class="value">${valDisplay}</div><small>${p!=null?`${p} panels (${nf(k,1)} kWp) · ${nf(b,1)} kWh`:''}</small>${deltaLine}</div>`;
 };

 const isSizingStale=typeof solarAcceptedFingerprint!=='undefined'&&typeof solarStateFingerprint==='function'&&(solarAcceptedFingerprint!==solarStateFingerprint());

 const activeLimits=getActiveRoofLimits();
 const finCand=choices?.financial;
 const balCand=choices?.balanced;
 const finFeasible=Boolean(finCand&&isCandidateRoofFeasible(finCand,activeLimits));
 const balFeasible=Boolean(balCand&&isCandidateRoofFeasible(balCand,activeLimits));

 let flagText='';
 if(choices&&choices.balanced&&balFeasible){
   if(isSizingStale){
    flagText='Inputs have changed since sizing analysis was run. Re-run System design sizing to refresh recommendations.';
   }else{
    const optBat=choices.balanced.battery_kwh??choices.balanced.bat??0;
    const optPanels=choices.balanced.panel_count??choices.balanced.panels??0;
    const balancedSpecs=`${optPanels} panels (${nf(choices.balanced.kwp,1)} kWp) · ${nf(optBat,1)} kWh`;
    const parts=[];
    if(currentBat>optBat+2){
     parts.push(`Battery storage appears oversized by ~${(currentBat-optBat).toFixed(1)} kWh compared to balanced economic return (${balancedSpecs}).`);
    }else if(currentBat<optBat-2){
     parts.push(`Storage capacity is ${(optBat-currentBat).toFixed(1)} kWh below balanced optimum; adding capacity would increase autarky.`);
    }
    if(currentPanels<optPanels){
     parts.push(`Array has headroom for ${optPanels-currentPanels} additional panels on high-yield facets.`);
    }
    if(!parts.length){
     flagText=`Your system is well-aligned with the balanced recommendation (${balancedSpecs}).`;
    }else{
     flagText=parts.join(' ');
    }
   }
 }

 // Dedupe configs by complete relevant settings/layout (exact configKey equality only)
 const configKey=c=>c?JSON.stringify(Object.entries(summaryCandidateRequest(c,r.settings).settings).sort(([a],[b])=>a.localeCompare(b))):'';

 const areIdentical=Boolean(finFeasible&&balFeasible&&configKey(finCand)===configKey(balCand));

 let recCardsHtml='';
 let flagHtml='';
 if(!choices||(!finFeasible&&!balFeasible)){
  recCardsHtml=`<div class="card config-card empty-state"><div class="label">RECOMMENDED CONFIGURATION</div><small class="card-scope-caption">${scopeCaption}</small><div class="value">—</div><div class="empty-action" style="margin-top:8px;"><button type="button" class="action-btn" onclick="if(typeof runSolarSizingAnalysis==='function')runSolarSizingAnalysis();return false;">Run sizing now</button></div></div>`;
  flagHtml='';
 }else{
  if(finFeasible&&balFeasible){
   if(areIdentical){
    recCardsHtml=cardData('RECOMMENDED CONFIGURATION (OPTIMAL RETURN & BALANCE)','balanced recommended',balCand);
   }else{
    recCardsHtml=cardData('BEST RETURN (FINANCIAL)','best-return',finCand)+cardData('BALANCED CHOICE (RECOMMENDED)','balanced',balCand);
   }
  }else if(balFeasible){
   recCardsHtml=cardData('BALANCED CHOICE (RECOMMENDED)','balanced',balCand);
  }else{
   recCardsHtml=cardData('BEST RETURN (FINANCIAL)','best-return',finCand);
  }
  if(flagText){
   flagHtml=`
     <div class="config-benchmark-flag">
       <span class="flag-icon">ℹ</span>
       <span class="flag-text">${esc(flagText)}</span>
       <a href="#design" class="flag-link" onclick="if(typeof switchView==='function')switchView('design');return false;" style="margin-left:8px;text-decoration:underline;">Explore full sizing in System design →</a>
     </div>
   `;
  }
 }

 const curPbValStr=(currentPb!=null&&Number.isFinite(Number(currentPb)))?`${nf(Number(currentPb),1)} y`:'Not reached';
 const curUnlPbValStr=(currentUnleveredPb!=null&&Number.isFinite(Number(currentUnleveredPb)))?`${nf(Number(currentUnleveredPb),1)} y`:'Not reached';

 el.innerHTML=`
   <div class="card config-card current">
     <div class="label">CURRENT CONFIGURATION</div>
     <small class="card-scope-caption">${scopeCaption}</small>
     <div class="value">${fmt(currentNpv,'eur')}</div>
     <small>${currentPanels} panels (${nf(currentKwp,1)} kWp) · ${nf(currentBat,1)} kWh usable</small>
     <div class="config-delta-line delta-neutral">${isCashBasis?'Capex':'Initial cash'} ${fmt(currentCapex,'eur')} · Payback ${curPbValStr} · Unlevered payback ${curUnlPbValStr}</div>
   </div>
   ${recCardsHtml}
   ${flagHtml}
 `;
}

let summarySensitivityCache=null;
let summarySensitivityKey='';
let summarySensitivityReqId=0;
let summarySensitivityOutdated=false;
let summarySensitivityInFlight=false;
let summarySensitivityActiveRequestId=0;
let summarySensitivityDebounceTimer=null;
let summarySensitivityIdleId=null;
let summarySensitivityFailed=false;

function cancelScheduledSummarySensitivity(){
 if(summarySensitivityDebounceTimer){
  clearTimeout(summarySensitivityDebounceTimer);
  summarySensitivityDebounceTimer=null;
 }
 if(typeof cancelIdleCallback==='function'&&summarySensitivityIdleId){
  cancelIdleCallback(summarySensitivityIdleId);
  summarySensitivityIdleId=null;
 }
}

function scheduleSummarySensitivityAuto(){
 if(summarySensitivityInFlight)return;
 if(typeof loanPending!=='undefined'&&loanPending)return;
 const s=typeof settings!=='undefined'?settings:null;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(s))return;
 if(typeof shouldBlockAcDemandPctRecompute==='function'&&shouldBlockAcDemandPctRecompute(s))return;
 if(typeof shouldBlockAcScheduleRecompute==='function'&&shouldBlockAcScheduleRecompute(s))return;
 if(typeof acceptedInputFingerprint!=='undefined'&&typeof inputFingerprint==='function'){
  if(acceptedInputFingerprint===null||acceptedInputFingerprint!==inputFingerprint(s,typeof quote!=='undefined'?quote:{line_items:[]}))return;
 }
 if(summarySensitivityCache&&isSummarySensitivityMatching(summarySensitivityCache,summarySensitivityKey))return;

 cancelScheduledSummarySensitivity();

 const executeRun=()=>{
  summarySensitivityDebounceTimer=null;
  summarySensitivityIdleId=null;
  if(summarySensitivityInFlight)return;
  if(typeof loanPending!=='undefined'&&loanPending)return;
  const curS=typeof settings!=='undefined'?settings:null;
  if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(curS))return;
  if(typeof shouldBlockAcDemandPctRecompute==='function'&&shouldBlockAcDemandPctRecompute(curS))return;
  if(typeof shouldBlockAcScheduleRecompute==='function'&&shouldBlockAcScheduleRecompute(curS))return;
  if(typeof acceptedInputFingerprint!=='undefined'&&typeof inputFingerprint==='function'){
   if(acceptedInputFingerprint===null||acceptedInputFingerprint!==inputFingerprint(curS,typeof quote!=='undefined'?quote:{line_items:[]}))return;
  }
  if(summarySensitivityCache&&isSummarySensitivityMatching(summarySensitivityCache,summarySensitivityKey))return;
  fetchSummarySensitivity();
 };

 summarySensitivityDebounceTimer=setTimeout(()=>{
  summarySensitivityDebounceTimer=null;
  if(typeof requestIdleCallback==='function')summarySensitivityIdleId=requestIdleCallback(executeRun,{timeout:2000});
  else executeRun();
 },250);
}

const scheduleSummarySensitivityInitialRun=scheduleSummarySensitivityAuto;

function updateSummarySensitivityButtonState(){
 const btn=typeof document!=='undefined'?(document.getElementById('runSummarySensitivityBtn')||(typeof $==='function'?$('runSummarySensitivityBtn'):null)):null;
 const badge=typeof document!=='undefined'?(document.getElementById('summarySensitivityBadge')||(typeof $==='function'?$('summarySensitivityBadge'):null)):null;
 if(!btn)return;

 if(summarySensitivityInFlight){
  btn.disabled=true;
  btn.setAttribute?.('aria-busy','true');
  btn.textContent='Calculating…';
  btn.innerHTML='<span class="progress-spinner" aria-hidden="true"></span> Calculating…';
  btn.classList.remove('highlight');
  btn.classList.remove('muted-btn');
  if(badge)badge.style.display='none';
  return;
 }
 btn.removeAttribute?.('aria-busy');

 const isMatching=Boolean(!summarySensitivityFailed&&summarySensitivityCache&&isSummarySensitivityMatching(summarySensitivityCache,summarySensitivityKey));
 if(isMatching){
  btn.disabled=true;
  btn.textContent='Update sensitivity';
  btn.classList.remove('highlight');
  btn.classList.add('muted-btn');
  if(badge){
   badge.textContent='✓ Up to date';
   badge.style.display='';
  }
  return;
 }

 btn.disabled=false;
 btn.textContent='Update sensitivity';
 btn.classList.remove('muted-btn');
 if(badge)badge.style.display='none';

 const s=typeof settings!=='undefined'?settings:null;
 const q=typeof quote!=='undefined'?quote:{line_items:[]};
 const curFp=typeof inputFingerprint==='function'?inputFingerprint(s,q):'';
 const cachedFp=summarySensitivityKey?summarySensitivityKey.split('_').slice(2).join('_'):'';
 const inputsChanged=Boolean(summarySensitivityCache&&cachedFp&&curFp&&cachedFp!==curFp);

 if(inputsChanged){
  btn.classList.add('highlight');
 }else{
  btn.classList.remove('highlight');
 }
}

function isSummarySensitivityMatching(data=summarySensitivityCache,key=summarySensitivityKey){
 if(!data)return false;
 if(typeof loanPending!=='undefined'&&loanPending)return false;
 const s=typeof settings!=='undefined'?settings:null;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(s))return false;
 if(typeof shouldBlockAcDemandPctRecompute==='function'&&shouldBlockAcDemandPctRecompute(s))return false;
 if(typeof shouldBlockAcScheduleRecompute==='function'&&shouldBlockAcScheduleRecompute(s))return false;
 if(typeof acceptedInputFingerprint!=='undefined'&&typeof inputFingerprint==='function'){
  if(acceptedInputFingerprint===null||acceptedInputFingerprint!==inputFingerprint(s,typeof quote!=='undefined'?quote:{line_items:[]}))return false;
 }
 const scope=typeof summaryScope!=='undefined'?summaryScope:'solar';
 const cash=typeof summaryCash!=='undefined'?summaryCash:'cash';
 const expectedKey=`${scope}_${cash}_${typeof inputFingerprint==='function'?inputFingerprint(s,typeof quote!=='undefined'?quote:{line_items:[]}):''}`;
 if(!key||key!==expectedKey)return false;
 return true;
}

function isSummarySensitivityOutdated(data=summarySensitivityCache,key=summarySensitivityKey){
 if(summarySensitivityOutdated){
  if(isSummarySensitivityMatching(data,key)){
   summarySensitivityOutdated=false;
   return false;
  }
  return true;
 }
 return !isSummarySensitivityMatching(data,key);
}

function invalidateSummarySensitivity(){
 summarySensitivityReqId++;
 summarySensitivityOutdated=true;
 summarySensitivityFailed=false;
 updateSummarySensitivityButtonState();
 if(typeof renderSummarySensitivity==='function'){
  renderSummarySensitivity();
 }
}

const markSummarySensitivityOutdated=invalidateSummarySensitivity;

function acceptSummarySensitivity(result=current?.result){
 const r=(result&&result.result)?result.result:(result||current?.result);
 if(!r||!isSummarySensitivityMatching(summarySensitivityCache,summarySensitivityKey))return false;
 summarySensitivityOutdated=false;
 summarySensitivityFailed=false;
 const el=typeof document!=='undefined'?(document.getElementById('summarySensitivityContent')||(typeof $==='function'?$('summarySensitivityContent'):null)):null;
 if(el&&summarySensitivityCache){
  displaySummarySensitivity(summarySensitivityCache,false);
 }
 updateSummarySensitivityButtonState();
 return true;
}

function isSummarySensitivityRequestValid(token,reqKey){
 if(token!==summarySensitivityReqId)return false;
 const s=typeof settings!=='undefined'?settings:null;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(s))return false;
 if(typeof shouldBlockAcDemandPctRecompute==='function'&&shouldBlockAcDemandPctRecompute(s))return false;
 if(typeof shouldBlockAcScheduleRecompute==='function'&&shouldBlockAcScheduleRecompute(s))return false;
 const scope=typeof summaryScope!=='undefined'?summaryScope:'solar';
 const cash=typeof summaryCash!=='undefined'?summaryCash:'cash';
 const currentKey=`${scope}_${cash}_${typeof inputFingerprint==='function'?inputFingerprint(s,typeof quote!=='undefined'?quote:{line_items:[]}):''}`;
 if(reqKey!==currentKey)return false;
 return true;
}

function displaySummarySensitivitySkeleton(el, statusText='Evaluating…'){
 if(!el)return;
 const curMetrics=typeof getScopeCashMetrics==='function'?getScopeCashMetrics(current?.result,summaryScope,summaryCash):null;
 const baseNpv=curMetrics?.npvObj?.mean??curMetrics?.npvObj?.median??(typeof curMetrics?.npvObj==='number'?curMetrics.npvObj:(current?.result?.finance?.pv_incremental?.npv?.mean??current?.result?.finance?.pv_incremental?.npv?.median??current?.result?.metrics?.npv_eur?.mean??current?.result?.metrics?.npv_eur?.median??0));
 const skeletonDrivers=[
  {id:'retail',label:'RETAIL ELECTRICITY TARIFF (±20%)',baseDesc:'Active electricity tariff',evidence:'unconfirmed'},
  {id:'weather',label:'WEATHER ANALOGUE RISK',baseDesc:`Historical weather extremes (${getWeatherEnsembleMeta().count ? getWeatherEnsembleMeta().count + '-yr' : 'historical'} min/max)`,evidence:'unconfirmed'},
  {id:'capex',label:'INITIAL CAPEX (±10%)',baseDesc:'Installed system cost',evidence:'unconfirmed'},
  {id:'replacement',label:'EQUIPMENT REPLACEMENT COST',baseDesc:'Inverter & battery replacement',evidence:'unconfirmed'},
  {id:'export_ramp',label:'EXPORT PRICE RAMP-DOWN',baseDesc:'Future export factor',evidence:'unconfirmed'},
  {id:'heat_pump_cop',label:'HEAT PUMP SEASONAL COP (±20%)',baseDesc:'Seasonal coefficient of performance',evidence:'unconfirmed'},
  {id:'real_energy_price_escalation',label:'REAL ELECTRICITY ESCALATION',baseDesc:'Long-term tariff inflation',evidence:'unconfirmed'},
  {id:'export_price_level',label:'EXPORT TARIFF BENCHMARK (±20%)',baseDesc:'Baseline feed-in tariff',evidence:'unconfirmed'},
  {id:'loan_apr',label:'LOAN FINANCING TAN (±2pp)',baseDesc:'Active debt financing terms',evidence:'unconfirmed'},
  {id:'pv_yield',label:'SOLAR PV SPECIFIC YIELD (±10%)',baseDesc:'Specific generation (kWh/kWp)',evidence:'unconfirmed'},
  {id:'tax_deduction',label:'TAX DEDUCTION ELIGIBILITY',baseDesc:'Eligible fiscal incentive basis',evidence:'unconfirmed'},
  {id:'gas',label:'NATURAL GAS TARIFF (±20%)',baseDesc:'Active gas commodity tariff',evidence:'unconfirmed'},
  {id:'battery_deg',label:'BATTERY DEGRADATION',baseDesc:'Calendar & cycle fade',evidence:'unconfirmed'},
  {id:'discount',label:'REAL DISCOUNT RATE (±2pp)',baseDesc:'Active real discount rate',evidence:'unconfirmed'}
 ];
 const uncertain=skeletonDrivers.filter(d=>d.id!=='discount');
 const valuation=skeletonDrivers.filter(d=>d.id==='discount');
 const renderRows=(list)=>list.map(d=>`
  <div class="tornado-row skeleton" data-driver="${esc(d.id)}" title="${esc(d.label)} · ${esc(statusText)}">
    <div class="tornado-meta">
      <strong class="tornado-label">${esc(d.label)}</strong>
      <div class="tornado-sub">${esc(d.baseDesc)}</div>
    </div>
    <div class="tornado-col-evidence">
      <span class="badge evidence-badge ${esc(d.evidence)}">${esc(d.evidence)}</span>
    </div>
    <div class="tornado-val neg">—</div>
    <div class="tornado-chart-cell">
      <div class="tornado-not-eval" role="status" aria-live="polite"><span class="progress-spinner" aria-hidden="true"></span> <span>${esc(statusText)}</span></div>
    </div>
    <div class="tornado-val pos">—</div>
    <div class="tornado-impact">
      <span class="muted">—</span>
    </div>
  </div>
 `).join('');

 el.innerHTML=`
  <div class="summary-tornado-card skeleton-card">
    <div class="stale-badge-strip skeleton-strip" role="status" aria-live="polite" aria-busy="true"><span class="progress-spinner" aria-hidden="true"></span><span>Sensitivity analysis pending · Evaluating ΔNPV across key drivers…</span></div>
    <div class="tornado-header">
      <span class="tornado-col-driver">DRIVER</span>
      <span class="tornado-col-evidence">EVIDENCE</span>
      <span class="tornado-col-val neg">DOWNSIDE</span>
      <span class="tornado-col-bar">ΔNPV RANGE (BASELINE ${fmt(baseNpv,'eur')})</span>
      <span class="tornado-col-val pos">UPSIDE</span>
      <span class="tornado-col-impact">SWING</span>
    </div>
    <div class="tornado-list">
      <div class="tornado-group">
        <div class="tornado-group-header">UNCERTAIN DRIVERS</div>
        ${renderRows(uncertain)}
      </div>
      <div class="tornado-group">
        <div class="tornado-group-header">VALUATION SETTINGS</div>
        ${renderRows(valuation)}
      </div>
    </div>
  </div>
 `;
}

async function renderSummarySensitivity(){
 const el=typeof document!=='undefined'?(document.getElementById('summarySensitivityContent')||(typeof $==='function'?$('summarySensitivityContent'):null)):null;
 if(!el||!current?.result)return;
 if(summarySensitivityCache&&isSummarySensitivityMatching(summarySensitivityCache,summarySensitivityKey)){
  cancelScheduledSummarySensitivity();
  displaySummarySensitivity(summarySensitivityCache,false);
  updateSummarySensitivityButtonState();
  return;
 }
 if(summarySensitivityCache){
  displaySummarySensitivity(summarySensitivityCache,true);
  updateSummarySensitivityButtonState();
  scheduleSummarySensitivityAuto();
  return;
 }
 displaySummarySensitivitySkeleton(el);
 updateSummarySensitivityButtonState();
 scheduleSummarySensitivityAuto();
}

async function fetchSummarySensitivity(){
 const el=typeof document!=='undefined'?(document.getElementById('summarySensitivityContent')||(typeof $==='function'?$('summarySensitivityContent'):null)):null;
 const btn=typeof document!=='undefined'?(document.getElementById('runSummarySensitivityBtn')||(typeof $==='function'?$('runSummarySensitivityBtn'):null)):null;
 if(!el)return;
 cancelScheduledSummarySensitivity();
 const s=typeof settings!=='undefined'?settings:null;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(s)){
  if(summarySensitivityCache){
   displaySummarySensitivity(summarySensitivityCache,true);
  }
  updateSummarySensitivityButtonState();
  return;
 }
 const reqScope = typeof summaryScope!=='undefined'?summaryScope:'solar';
 const reqCash = typeof summaryCash!=='undefined'?summaryCash:'cash';
 const reqFp = typeof inputFingerprint==='function'?inputFingerprint(s, typeof quote!=='undefined'?quote:{line_items:[]}):'';
 const reqKey = `${reqScope}_${reqCash}_${reqFp}`;
 const token=++summarySensitivityReqId;
 summarySensitivityActiveRequestId=token;
 summarySensitivityInFlight=true;
 updateSummarySensitivityButtonState();
 let shouldReschedule=false;
 try{
  try{
   const hasCard=(typeof el.querySelector==='function'&&el.querySelector('.summary-tornado-card'))||(typeof el.innerHTML==='string'&&el.innerHTML.includes('summary-tornado-card'));
   if(!hasCard){
    displaySummarySensitivitySkeleton(el,'Calculating…');
   }
  }catch(_e){}
  const data=await api('/api/summary-sensitivity',{
   settings:s,
   quote:typeof quote!=='undefined'?quote:{line_items:[]},
   scope:reqScope,
   cash_mode:reqCash
  });
  if(!isSummarySensitivityRequestValid(token,reqKey)){
   if(token===summarySensitivityReqId){
    updateSummarySensitivityButtonState();
   }
   shouldReschedule=true;
   return;
  }
  summarySensitivityCache=data;
  summarySensitivityKey=reqKey;
  summarySensitivityOutdated=false;
  summarySensitivityFailed=false;
  displaySummarySensitivity(data,false);
 }catch(err){
  if(isSummarySensitivityRequestValid(token,reqKey)){
   summarySensitivityFailed=true;
   el.innerHTML=`<p class="bad">Could not evaluate sensitivity: ${esc(err.message||err)}</p>`;
  }else{
   shouldReschedule=true;
  }
 }finally{
  if(token===summarySensitivityActiveRequestId){
   summarySensitivityInFlight=false;
   updateSummarySensitivityButtonState();
  }
  if(shouldReschedule){
   scheduleSummarySensitivityAuto();
  }
 }
}

const runSummarySensitivity=fetchSummarySensitivity;
if(typeof window!=='undefined'){
 window.runSummarySensitivity=fetchSummarySensitivity;
}
function displaySummarySensitivity(data,isStale=undefined){
 const stale=isStale!==undefined?Boolean(isStale):isSummarySensitivityOutdated(data,summarySensitivityKey);
 const el=typeof document!=='undefined'?(document.getElementById('summarySensitivityContent')||(typeof $==='function'?$('summarySensitivityContent'):null)):null;
 if(!el||!data)return;
 const baseNpv=data.base_npv_eur??data.base_npv?.mean??(data.base_npv?.mean===undefined?data.base_npv?.median:null)??(data.weather?.mean_weather_npv??0);
 const fmtDelta=d=>(d!=null)?`${d>=0?'+':''}${fmt(d,'eur')}`:'—';

 const getEvidenceStatus=(paramNames)=>{
  if(!paramNames||paramNames.length===0)return 'unconfirmed';
  if(paramNames.includes('weather'))return 'unconfirmed';
  const recs=(typeof schema!=='undefined'&&schema?.records)?schema.records:[];
  for(const name of paramNames){
   const rec=recs.find(r=>r.name===name);
   if(!rec)return 'unconfirmed';
   const st=String(rec.status||'').toLowerCase();
   if(!['measured','provided','confirmed','verified'].includes(st)){
    return 'unconfirmed';
   }
  }
  return 'confirmed';
 };

 const ep=data.electricity_price||data.energy_price||null;
 const dr=data.discount_rate||null;
 const w=data.weather||null;
 const ex=data.export_price_ramp||null;
 const cx=data.capex||null;
 const rep=data.replacement_cost||null;
 const gas=data.gas_price||null;
 const deg=data.battery_degradation||null;

 const s=typeof settings!=='undefined'?settings:{};

 // Retail tariff
 let epLowDelta=null, epHighDelta=null;
 if(ep){
  if(ep.minus_20_pct?.delta_npv!=null) epLowDelta=ep.minus_20_pct.delta_npv;
  else if(ep.delta_low!=null) epLowDelta=ep.delta_low;
  else if(ep.minus_20_pct?.npv_eur!=null) epLowDelta=ep.minus_20_pct.npv_eur-baseNpv;
  else if(ep.low_npv!=null) epLowDelta=ep.low_npv-baseNpv;

  if(ep.plus_20_pct?.delta_npv!=null) epHighDelta=ep.plus_20_pct.delta_npv;
  else if(ep.delta_high!=null) epHighDelta=ep.delta_high;
  else if(ep.plus_20_pct?.npv_eur!=null) epHighDelta=ep.plus_20_pct.npv_eur-baseNpv;
  else if(ep.high_npv!=null) epHighDelta=ep.high_npv-baseNpv;
 }
 const epLowPrice=ep?.minus_20_pct?.price??ep?.low_value??((s.retail_commodity||0.1169)*0.8);
 const epHighPrice=ep?.plus_20_pct?.price??ep?.high_value??((s.retail_commodity||0.1169)*1.2);
 const epBasePrice=ep?.base?.price??ep?.base_value??(s.retail_commodity||0.1169);

 // Discount rate
 let drLowDelta=null, drHighDelta=null;
 if(dr){
  if(dr.minus_2_pct?.delta_npv!=null) drLowDelta=dr.minus_2_pct.delta_npv;
  else if(dr.delta_low!=null) drLowDelta=dr.delta_low;
  else if(dr.minus_2_pct?.npv_eur!=null) drLowDelta=dr.minus_2_pct.npv_eur-baseNpv;
  else if(dr.low_npv!=null) drLowDelta=dr.low_npv-baseNpv;

  if(dr.plus_2_pct?.delta_npv!=null) drHighDelta=dr.plus_2_pct.delta_npv;
  else if(dr.delta_high!=null) drHighDelta=dr.delta_high;
  else if(dr.plus_2_pct?.npv_eur!=null) drHighDelta=dr.plus_2_pct.npv_eur-baseNpv;
  else if(dr.high_npv!=null) drHighDelta=dr.high_npv-baseNpv;
 }
 const drLowRate=dr?.minus_2_pct?.rate??dr?.low_value??Math.max(0,(s.real_discount_rate||0.03)-0.02);
 const drHighRate=dr?.plus_2_pct?.rate??dr?.high_value??((s.real_discount_rate||0.03)+0.02);
 const drBaseRate=dr?.base?.rate??dr?.base_value??(s.real_discount_rate||0.03);

 // Weather
 let wWorstDelta=null, wBestDelta=null;
 if(w){
  if(w.worst_year?.delta_npv!=null) wWorstDelta=w.worst_year.delta_npv;
  else if(w.delta_worst!=null) wWorstDelta=w.delta_worst;
  else if(w.worst_year?.npv_eur!=null) wWorstDelta=w.worst_year.npv_eur-baseNpv;
  else if(w.worst_weather_npv!=null) wWorstDelta=w.worst_weather_npv-baseNpv;

  if(w.best_year?.delta_npv!=null) wBestDelta=w.best_year.delta_npv;
  else if(w.delta_best!=null) wBestDelta=w.delta_best;
  else if(w.best_year?.npv_eur!=null) wBestDelta=w.best_year.npv_eur-baseNpv;
  else if(w.best_weather_npv!=null) wBestDelta=w.best_weather_npv-baseNpv;
 }
 const wWorstYear=w?.worst_year?.year||w?.worst_weather_year||'Historical minimum';
 const wBestYear=w?.best_year?.year||w?.best_weather_year||'Historical maximum';

 // Export ramp
 let exLowDelta=null, exHighDelta=null;
 if(ex){
  if(ex.ramp_down?.delta_npv!=null) exLowDelta=ex.ramp_down.delta_npv;
  else if(ex.delta_low!=null) exLowDelta=ex.delta_low;
  else if(ex.ramp_down?.npv_eur!=null) exLowDelta=ex.ramp_down.npv_eur-baseNpv;

  if(ex.ramp_up?.delta_npv!=null) exHighDelta=ex.ramp_up.delta_npv;
  else if(ex.delta_high!=null) exHighDelta=ex.delta_high;
  else if(ex.ramp_up?.npv_eur!=null) exHighDelta=ex.ramp_up.npv_eur-baseNpv;
 }
 const exBaseFactor=ex?.base?.factor??(s.actual_price_future_factor||1.0);

 // Capex
 let cxLowDelta=null, cxHighDelta=null;
 if(cx){
  if(cx.plus_10_pct?.delta_npv!=null) cxLowDelta=cx.plus_10_pct.delta_npv;
  else if(cx.delta_low!=null) cxLowDelta=cx.delta_low;
  else if(cx.plus_10_pct?.npv_eur!=null) cxLowDelta=cx.plus_10_pct.npv_eur-baseNpv;

  if(cx.minus_10_pct?.delta_npv!=null) cxHighDelta=cx.minus_10_pct.delta_npv;
  else if(cx.delta_high!=null) cxHighDelta=cx.delta_high;
  else if(cx.minus_10_pct?.npv_eur!=null) cxHighDelta=cx.minus_10_pct.npv_eur-baseNpv;
 }

 // Replacement
 let repLowDelta=null, repHighDelta=null;
 if(rep){
  if(rep.plus_20_pct?.delta_npv!=null) repLowDelta=rep.plus_20_pct.delta_npv;
  else if(rep.delta_low!=null) repLowDelta=rep.delta_low;
  else if(rep.plus_20_pct?.npv_eur!=null) repLowDelta=rep.plus_20_pct.npv_eur-baseNpv;

  if(rep.minus_20_pct?.delta_npv!=null) repHighDelta=rep.minus_20_pct.delta_npv;
  else if(rep.delta_high!=null) repHighDelta=rep.delta_high;
  else if(rep.minus_20_pct?.npv_eur!=null) repHighDelta=rep.minus_20_pct.npv_eur-baseNpv;
 }

 // Gas
 let gasLowDelta=null, gasHighDelta=null;
 if(gas){
  if(gas.minus_20_pct?.delta_npv!=null) gasLowDelta=gas.minus_20_pct.delta_npv;
  else if(gas.delta_low!=null) gasLowDelta=gas.delta_low;
  else if(gas.minus_20_pct?.npv_eur!=null) gasLowDelta=gas.minus_20_pct.npv_eur-baseNpv;

  if(gas.plus_20_pct?.delta_npv!=null) gasHighDelta=gas.plus_20_pct.delta_npv;
  else if(gas.delta_high!=null) gasHighDelta=gas.delta_high;
  else if(gas.plus_20_pct?.npv_eur!=null) gasHighDelta=gas.plus_20_pct.npv_eur-baseNpv;
 }
 const gasLowPrice=gas?.minus_20_pct?.price??((s.gas_commodity||0.47)*0.8);
 const gasHighPrice=gas?.plus_20_pct?.price??((s.gas_commodity||0.47)*1.2);
 const gasBasePrice=gas?.base?.price??(s.gas_commodity||0.47);

 // Battery degradation
 let degLowDelta=null, degHighDelta=null;
 if(deg){
  if(deg.higher_fade?.delta_npv!=null) degLowDelta=deg.higher_fade.delta_npv;
  else if(deg.delta_low!=null) degLowDelta=deg.delta_low;
  else if(deg.higher_fade?.npv_eur!=null) degLowDelta=deg.higher_fade.npv_eur-baseNpv;

  if(deg.lower_fade?.delta_npv!=null) degHighDelta=deg.lower_fade.delta_npv;
  else if(deg.delta_high!=null) degHighDelta=deg.delta_high;
  else if(deg.lower_fade?.npv_eur!=null) degHighDelta=deg.lower_fade.npv_eur-baseNpv;
 }
 const degBaseFade=deg?.base?.fade??(s.battery_calendar_fade||0.015);

 const hasQuote=Boolean(typeof quote!=='undefined'&&quote?.line_items?.length);

 // Metadata mappings for the 6 top-level dicts
 const hp=data.heat_pump_cop||null;
 const escRate=data.real_energy_price_escalation||null;
 const expLvl=data.export_price_level||null;
 const lApr=data.loan_apr||null;
 const pvYld=data.pv_yield||null;
 const taxDed=data.tax_deduction||null;

 const parseDriverEndpoints=(obj,defaultLowDesc,defaultHighDesc,defaultBaseDesc)=>{
  if(!obj)return {hasData:false,lowDelta:null,highDelta:null,lowDesc:defaultLowDesc,highDesc:defaultHighDesc,baseDesc:defaultBaseDesc};
  let low=null, high=null;
  if(obj.delta_low!=null) low=obj.delta_low;
  else if(obj.minus_20_pct?.delta_npv!=null) low=obj.minus_20_pct.delta_npv;
  else if(obj.minus_10_pct?.delta_npv!=null) low=obj.minus_10_pct.delta_npv;
  else if(obj.minus_2_pct?.delta_npv!=null) low=obj.minus_2_pct.delta_npv;
  else if(obj.minus_1_pct?.delta_npv!=null) low=obj.minus_1_pct.delta_npv;
  else if(obj.low_capacity?.delta_npv!=null) low=obj.low_capacity.delta_npv;
  else if(obj.low?.delta_npv!=null) low=obj.low.delta_npv;
  else if(obj.low?.npv_eur!=null) low=obj.low.npv_eur-baseNpv;
  else if(obj.delta_worst!=null) low=obj.delta_worst;

  if(obj.delta_high!=null) high=obj.delta_high;
  else if(obj.plus_20_pct?.delta_npv!=null) high=obj.plus_20_pct.delta_npv;
  else if(obj.plus_10_pct?.delta_npv!=null) high=obj.plus_10_pct.delta_npv;
  else if(obj.plus_2_pct?.delta_npv!=null) high=obj.plus_2_pct.delta_npv;
  else if(obj.plus_1_pct?.delta_npv!=null) high=obj.plus_1_pct.delta_npv;
  else if(obj.high_capacity?.delta_npv!=null) high=obj.high_capacity.delta_npv;
  else if(obj.high?.delta_npv!=null) high=obj.high.delta_npv;
  else if(obj.high?.npv_eur!=null) high=obj.high.npv_eur-baseNpv;
  else if(obj.delta_best!=null) high=obj.delta_best;

  let bDesc=obj.base?.desc||obj.base_desc||defaultBaseDesc;
  if(obj.base?.scop!=null&&obj.base?.dhw_scop!=null) bDesc=`Active SCOP (${fmt(obj.base.scop,'num')} space / ${fmt(obj.base.dhw_scop,'num')} DHW)`;
  else if(obj.base?.scop!=null) bDesc=`Active SCOP (${fmt(obj.base.scop,'num')})`;
  else if(obj.base?.watts!=null) bDesc=`Active module (${fmt(obj.base.watts,'num')} W)`;
  else if(obj.base?.capacity!=null) bDesc=`Active tax capacity (${fmt(obj.base.capacity,'eur')}/yr)`;

  const lDesc=obj.low?.desc||obj.low_desc||defaultLowDesc;
  const hDesc=obj.high?.desc||obj.high_desc||defaultHighDesc;
  return {
   hasData:(low!=null&&high!=null),
   lowDelta:low,
   highDelta:high,
   lowDesc:lDesc,
   highDesc:hDesc,
   baseDesc:bDesc
  };
 };

 const hpParsed=parseDriverEndpoints(hp,`SCOP ${hp?.minus_20_pct?.scop??'—'} / DHW ${hp?.minus_20_pct?.dhw_scop??'—'}`,`SCOP ${hp?.plus_20_pct?.scop??'—'} / DHW ${hp?.plus_20_pct?.dhw_scop??'—'}`,`Active SCOP ${hp?.base?.scop??'—'} / DHW ${hp?.base?.dhw_scop??'—'}`);
 const escParsed=parseDriverEndpoints(escRate,`${fmt(escRate?.minus_1_pct?.rate,'pct')}/yr real escalation`,`${fmt(escRate?.plus_1_pct?.rate,'pct')}/yr real escalation`,escRate?.base?.rate!=null?`Active escalation (${fmt(escRate.base.rate,'pct')}/yr)`:'Active price trajectory');
 const expLvlParsed=parseDriverEndpoints(expLvl,`${fmt(expLvl?.minus_20_pct?.price,'lcoe')}/kWh market price`,`${fmt(expLvl?.plus_20_pct?.price,'lcoe')}/kWh market price`,expLvl?.base?.price!=null?`Active export (${fmt(expLvl.base.price,'lcoe')}/kWh)`:'Active export commodity rate');
 const loanParsed=parseDriverEndpoints(lApr,`${lApr?.minus_2_pct?.rate!=null?(Number(lApr.minus_2_pct.rate)*100).toFixed(2)+'%':'—'} TAN`,`${lApr?.plus_2_pct?.rate!=null?(Number(lApr.plus_2_pct.rate)*100).toFixed(2)+'%':'—'} TAN`,lApr?.base?.rate!=null?`Active TAN (${(Number(lApr.base.rate)*100).toFixed(2)}%)`:'Active financing terms');
 const pvYldParsed=parseDriverEndpoints(pvYld,'PV production −10%', 'PV production +10%', 'PV production perturbation · installed capacity and cost held fixed');
 const taxDedParsed=parseDriverEndpoints(taxDed,'No usable IRPEF capacity','Full IRPEF capacity',`Annual IRPEF capacity ${fmt(taxDed?.base?.capacity,'eur')}`);

 const uncertainDrivers=[
  {
   id:'retail',
   label:'RETAIL ELECTRICITY TARIFF (±20%)',
   evidence:getEvidenceStatus(['retail_commodity']),
   hasData:(epLowDelta!=null&&epHighDelta!=null),
   lowDelta:epLowDelta,
   highDelta:epHighDelta,
   lowDesc:`−20% (${fmt(epLowPrice,'lcoe')}/kWh)`,
   highDesc:`+20% (${fmt(epHighPrice,'lcoe')}/kWh)`,
   baseDesc:`Active (${fmt(epBasePrice,'lcoe')}/kWh)`
  },
  {
   id:'weather',
   label:'WEATHER ANALOGUE RISK',
   evidence:getEvidenceStatus(['weather']),
   hasData:(wWorstDelta!=null&&wBestDelta!=null),
   lowDelta:wWorstDelta,
   highDelta:wBestDelta,
   lowDesc:`Worst (${esc(wWorstYear)})`,
   highDesc:`Best (${esc(wBestYear)})`,
   baseDesc:`Historical weather extremes (${getWeatherEnsembleMeta().count ? getWeatherEnsembleMeta().count + '-year' : 'historical'} min/max)`
  },
  {
   id:'export_ramp',
   label:'EXPORT PRICE RAMP-DOWN',
   evidence:getEvidenceStatus(['actual_price_future_factor']),
   hasData:(exLowDelta!=null&&exHighDelta!=null),
   lowDelta:exLowDelta,
   highDelta:exHighDelta,
   lowDesc:'Ramp-down (−25% factor)',
   highDesc:'Upside (+25% factor)',
    baseDesc:`Active factor (${nf(exBaseFactor,2)}x)`
  },
  {
   id:'capex',
   label:'INITIAL CAPEX (±10%)',
   evidence:hasQuote?getEvidenceStatus(['quote_total']):getEvidenceStatus(['pv_cost_kwp','pv_fixed_cost','battery_cost_kwh']),
   hasData:(cxLowDelta!=null&&cxHighDelta!=null),
   lowDelta:cxLowDelta,
   highDelta:cxHighDelta,
   lowDesc:'+10% installed cost',
   highDesc:'−10% installed cost',
   baseDesc:'Baseline active installed cost'
  },
  {
   id:'replacement',
   label:'EQUIPMENT REPLACEMENT COST',
   evidence:getEvidenceStatus(['inverter_replacement_cost','battery_replacement_cost_kwh',...(data.scope==='package'&&s.planning_heat_pump?['hp_replacement_cost']:[])]),
   hasData:(repLowDelta!=null&&repHighDelta!=null),
   lowDelta:repLowDelta,
   highDelta:repHighDelta,
   lowDesc:'+20% replacement',
   highDesc:'−20% replacement',
   baseDesc:'Baseline replacement'
  },
  {
   id:'heat_pump_cop',
   label:'HEAT PUMP SEASONAL COP (±20%)',
   evidence:getEvidenceStatus(['scop','dhw_scop']),
   hasData:hpParsed.hasData,
   lowDelta:hpParsed.lowDelta,
   highDelta:hpParsed.highDelta,
   lowDesc:hpParsed.lowDesc,
   highDesc:hpParsed.highDesc,
   baseDesc:hpParsed.baseDesc
  },
  {
   id:'real_energy_price_escalation',
   label:'REAL ELECTRICITY ESCALATION',
   evidence:getEvidenceStatus(['energy_real_escalation']),
   hasData:escParsed.hasData,
   lowDelta:escParsed.lowDelta,
   highDelta:escParsed.highDelta,
   lowDesc:escParsed.lowDesc,
   highDesc:escParsed.highDesc,
   baseDesc:escParsed.baseDesc
  },
  {
   id:'export_price_level',
   label:'EXPORT TARIFF BENCHMARK (±20%)',
   evidence:getEvidenceStatus(['export_market_price']),
   hasData:expLvlParsed.hasData,
   lowDelta:expLvlParsed.lowDelta,
   highDelta:expLvlParsed.highDelta,
   lowDesc:expLvlParsed.lowDesc,
   highDesc:expLvlParsed.highDesc,
   baseDesc:expLvlParsed.baseDesc
  },
  {
   id:'loan_apr',
   label:'LOAN FINANCING TAN (±2pp)',
   evidence:getEvidenceStatus(['loan_apr']),
   hasData:loanParsed.hasData,
   lowDelta:loanParsed.lowDelta,
   highDelta:loanParsed.highDelta,
   lowDesc:loanParsed.lowDesc,
   highDesc:loanParsed.highDesc,
   baseDesc:loanParsed.baseDesc
  },
  {
   id:'pv_yield',
   label:'SOLAR PV SPECIFIC YIELD (±10%)',
   evidence:getEvidenceStatus(['panel_watts','shade_strength']),
   hasData:pvYldParsed.hasData,
   lowDelta:pvYldParsed.lowDelta,
   highDelta:pvYldParsed.highDelta,
   lowDesc:pvYldParsed.lowDesc,
   highDesc:pvYldParsed.highDesc,
   baseDesc:pvYldParsed.baseDesc
  },
  {
   id:'tax_deduction',
   label:'TAX DEDUCTION ELIGIBILITY',
   evidence:getEvidenceStatus(['deduction_tax_capacity','deduction_eligible_fraction']),
   hasData:taxDedParsed.hasData,
   lowDelta:taxDedParsed.lowDelta,
   highDelta:taxDedParsed.highDelta,
   lowDesc:taxDedParsed.lowDesc,
   highDesc:taxDedParsed.highDesc,
   baseDesc:taxDedParsed.baseDesc
  },
  {
   id:'gas',
   label:'NATURAL GAS TARIFF (±20%)',
   evidence:getEvidenceStatus(['gas_commodity']),
   hasData:(gasLowDelta!=null&&gasHighDelta!=null),
   lowDelta:gasLowDelta,
   highDelta:gasHighDelta,
   lowDesc:`−20% (${fmt(gasLowPrice,'eur2')}/Smc)`,
   highDesc:`+20% (${fmt(gasHighPrice,'eur2')}/Smc)`,
   baseDesc:`Active (${fmt(gasBasePrice,'eur2')}/Smc)`
  },
  {
   id:'battery_deg',
   label:'BATTERY DEGRADATION',
   evidence:getEvidenceStatus(['battery_calendar_fade','battery_cycle_fade']),
   hasData:(degLowDelta!=null&&degHighDelta!=null),
   lowDelta:degLowDelta,
   highDelta:degHighDelta,
   lowDesc:'Stress fade (+50%)',
   highDesc:'Low fade (−50%)',
   baseDesc:`Active (${fmt(degBaseFade,'pct')}/yr)`
  }
 ];

 const valuationDrivers=[
  {
   id:'discount',
   label:'REAL DISCOUNT RATE (±2pp)',
   evidence:getEvidenceStatus(['real_discount_rate']),
   hasData:(drLowDelta!=null&&drHighDelta!=null),
   lowDelta:drLowDelta,
   highDelta:drHighDelta,
   lowDesc:`−2pp (${fmt(drLowRate,'pct')})`,
   highDesc:`+2pp (${fmt(drHighRate,'pct')})`,
   baseDesc:`Active (${fmt(drBaseRate,'pct')})`
  }
 ];

 const adverseDrivers=[];
 const allAdv=data.all_adverse||data.all_adverse_together||data.combined_adverse||data.adverse_combined||data.combined||null;
 if(allAdv){
  let allAdvLow=null;
  if(allAdv.delta_npv!=null) allAdvLow=allAdv.delta_npv;
  else if(allAdv.delta_low!=null) allAdvLow=allAdv.delta_low;
  else if(allAdv.npv_eur!=null) allAdvLow=allAdv.npv_eur-baseNpv;
  else if(allAdv.minus_adverse?.delta_npv!=null) allAdvLow=allAdv.minus_adverse.delta_npv;

  const allAdvNetNpv = allAdv.npv_eur != null ? allAdv.npv_eur : (baseNpv + (allAdvLow ?? 0));

  adverseDrivers.push({
   id:'all_adverse',
   label:'ALL ADVERSE TOGETHER',
   evidence:'unconfirmed',
   hasData:(allAdvLow!=null),
   lowDelta:allAdvLow,
   highDelta:null,
   netNpv:allAdvNetNpv,
   lowDesc:allAdv.description||'Simultaneous adverse shock across all drivers',
   highDesc:'—',
   baseDesc:'Stress case: joint adverse realization'
  });
 }

 if(Array.isArray(data.drivers)){
  for(const extra of data.drivers){
   if(!extra||!extra.id)continue;
   const existing=[...uncertainDrivers,...valuationDrivers,...adverseDrivers].find(d=>d.id===extra.id);
   if(!existing){
    const lowD=extra.delta_low??extra.lowDelta??(extra.low_npv!=null?extra.low_npv-baseNpv:null);
    const highD=extra.delta_high??extra.highDelta??(extra.high_npv!=null?extra.high_npv-baseNpv:null);
    uncertainDrivers.push({
     id:extra.id,
     label:extra.label||extra.name||extra.id.toUpperCase(),
     evidence:extra.evidence||'unconfirmed',
     hasData:(lowD!=null&&highD!=null),
     lowDelta:lowD,
     highDelta:highD,
     lowDesc:extra.lowDesc||'Downside',
     highDesc:extra.highDesc||'Upside',
     baseDesc:extra.baseDesc||'Active setting'
    });
   }
  }
 }

 uncertainDrivers.sort((a,b)=>{
  if(!a.hasData&&!b.hasData)return 0;
  if(!a.hasData)return 1;
  if(!b.hasData)return -1;
  const swingA=Math.abs(a.highDelta-a.lowDelta);
  const swingB=Math.abs(b.highDelta-b.lowDelta);
  return swingB-swingA;
 });

 const allRows=[...uncertainDrivers,...valuationDrivers,...adverseDrivers];
 const evaluatedDrivers=allRows.filter(d=>d.hasData);
 const maxVal=Math.max(...evaluatedDrivers.map(d=>Math.max(Math.abs(d.lowDelta||0),Math.abs(d.highDelta||0))),1);

 const hasNpv0Line=(maxVal>0 && Math.abs(baseNpv)<=maxVal);
 const npv0Pct=hasNpv0Line?Math.max(0,Math.min(100,((maxVal-baseNpv)/(2*maxVal))*100)):null;

 const renderRow=(d)=>{
  if(!d.hasData){
   return `
     <div class="tornado-row un-evaluated" data-driver="${esc(d.id)}" title="${esc(d.label)} · Not evaluated\n${esc(d.baseDesc)}">
       <div class="tornado-meta">
         <strong class="tornado-label">${esc(d.label)}</strong>
         <div class="tornado-sub">${esc(d.baseDesc)}</div>
       </div>
       <div class="tornado-col-evidence">
         <span class="badge evidence-badge ${esc(d.evidence)}">${esc(d.evidence)}</span>
       </div>
       <div class="tornado-val neg">—</div>
       <div class="tornado-chart-cell">
         <div class="tornado-not-eval">Not evaluated</div>
       </div>
       <div class="tornado-val pos">—</div>
       <div class="tornado-impact">
         <span class="muted">—</span>
       </div>
     </div>
   `;
  }

  // Defect 4 fix: preserve actual endpoint label/delta association when sorting endpoints
  const isAllAdverse=(d.id==='all_adverse');
  let negVal, posVal, negDesc, posDesc;
  if(isAllAdverse){
   negVal=d.lowDelta;
   negDesc=d.lowDesc;
   posVal=null;
   posDesc='—';
  }else if(d.lowDelta<=d.highDelta){
   negVal=d.lowDelta; negDesc=d.lowDesc;
   posVal=d.highDelta; posDesc=d.highDesc;
  }else{
   negVal=d.highDelta; negDesc=d.highDesc;
   posVal=d.lowDelta; posDesc=d.lowDesc;
  }

  const negPct=Math.min(100,Math.round((Math.abs(Math.min(0,negVal||0))/maxVal)*100));
  const posPct=isAllAdverse?0:Math.min(100,Math.round((Math.max(0,posVal||0)/maxVal)*100));
  const swing=isAllAdverse?null:Math.abs(posVal-negVal);
  const netNpv=isAllAdverse?(d.netNpv??(baseNpv+(negVal||0))):null;

  const fullTooltip=isAllAdverse
   ? `${d.label}\nBaseline: ${d.baseDesc}\nDownside (${negDesc}): ${fmtDelta(negVal)}\nNet NPV: ${fmt(netNpv,'eur')}\nEvidence: ${d.evidence}`
   : `${d.label}\nBaseline: ${d.baseDesc}\nDownside (${negDesc}): ${fmtDelta(negVal)}\nUpside (${posDesc}): ${fmtDelta(posVal)}\nSwing: ${fmt(swing,'eur')}\nEvidence: ${d.evidence}`;

  return `
    <div class="tornado-row ${isAllAdverse?'all-adverse-row':''}" data-driver="${esc(d.id)}" title="${esc(fullTooltip)}">
      <div class="tornado-meta">
        <strong class="tornado-label">${esc(d.label)}</strong>
        <div class="tornado-sub">${esc(d.baseDesc)}</div>
      </div>
      <div class="tornado-col-evidence">
        <span class="badge evidence-badge ${esc(d.evidence)}">${esc(d.evidence)}</span>
      </div>
      <div class="tornado-val neg">${fmtDelta(negVal)}</div>
      <div class="tornado-chart-cell">
        <div class="tornado-bar-track">
          <div class="tornado-neg-side">
            <div class="tornado-bar neg" style="width:${negPct}%" title="${esc(negDesc)}: ${fmtDelta(negVal)}"></div>
          </div>
          <div class="tornado-axis-center"></div>
          ${hasNpv0Line?`<div class="tornado-axis-npv0" style="left:${npv0Pct.toFixed(2)}%" title="Break-even line: NPV = 0 (Baseline NPV: ${fmt(baseNpv,'eur')})"></div>`:''}
          <div class="tornado-pos-side">
            ${isAllAdverse?'':`<div class="tornado-bar pos" style="width:${posPct}%" title="${esc(posDesc)}: ${fmtDelta(posVal)}"></div>`}
          </div>
        </div>
      </div>
      ${isAllAdverse
        ? `<div class="tornado-val muted" style="text-align:left;">—</div>`
        : `<div class="tornado-val pos">${fmtDelta(posVal)}</div>`}
      <div class="tornado-impact ${isAllAdverse?'all-adverse-impact':''}">
        ${isAllAdverse
          ? `<span class="all-adverse-net-group"><span class="adverse-net-npv-label">Net NPV: </span><strong>${fmt(netNpv,'eur')}</strong></span>`
          : `<strong>${fmt(swing,'eur')}</strong>`}
      </div>
    </div>
  `;
 };

 el.innerHTML=`
   <div class="summary-tornado-card ${stale?'stale-overlay':''}">
     ${stale?'<div class="stale-badge-strip"><span class="badge stale-badge">STALE</span><span>Inputs changed since last evaluation · click Update sensitivity to refresh</span></div>':''}
     <div class="tornado-header">
       <span class="tornado-col-driver">DRIVER</span>
       <span class="tornado-col-evidence">EVIDENCE</span>
       <span class="tornado-col-val neg">DOWNSIDE</span>
       <span class="tornado-col-bar">ΔNPV RANGE (BASELINE ${fmt(baseNpv,'eur')}${hasNpv0Line?' · <span class="npv0-legend">| NPV 0</span>':''})</span>
       <span class="tornado-col-val pos">UPSIDE</span>
       <span class="tornado-col-impact">SWING</span>
     </div>
     <div class="tornado-list">
       <div class="tornado-group">
         <div class="tornado-group-header">UNCERTAIN DRIVERS (RANKED BY SWING)</div>
         ${uncertainDrivers.map(renderRow).join('')}
       </div>
       <div class="tornado-group">
         <div class="tornado-group-header">VALUATION SETTINGS</div>
         ${valuationDrivers.map(renderRow).join('')}
       </div>
       ${adverseDrivers.length?`
       <div class="tornado-group">
         <div class="tornado-group-header">ALL ADVERSE SCENARIO</div>
         ${adverseDrivers.map(renderRow).join('')}
       </div>`:''}
     </div>
   </div>
 `;
 updateSummarySensitivityButtonState();
}

function renderDesignUnifiedComparisonTable(data){
  const el=$('designUnifiedComparisonTable');
  if(!el)return;
  const choices=data?.recommendation_choices;
  const currentRes=(typeof current!=='undefined')?current?.result:null;
  if(!choices||!currentRes){
    el.innerHTML='<p class="muted">Run sizing analysis to generate the 5-configuration benchmark table.</p>';
    return;
  }
  const wrap=$('solarResultsWrap');
  const isStaleOrRunning = (wrap && wrap.classList.contains('stale')) || Boolean(solarInFlight) || (typeof solarAcceptedFingerprint!=='undefined' && solarAcceptedFingerprint!=null && typeof solarStateFingerprint==='function' && solarAcceptedFingerprint!==solarStateFingerprint());
  const finBasis = data?.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  const currCapex = currentRes.finance?.capex_eur ?? null;
  const currLoan = currentRes.finance?.solar_loan_principal_eur ?? null;
  const currEquity = currentRes.finance?.initial_solar_equity_eur ?? (currCapex != null && currLoan != null ? Math.max(0, currCapex - currLoan) : null);
  const currProjNpv = currentRes.finance?.pv_incremental?.npv_mean !== undefined
    ? currentRes.finance.pv_incremental.npv_mean
    : (currentRes.finance?.pv_incremental?.npv?.mean !== undefined
      ? currentRes.finance.pv_incremental.npv.mean
      : (currentRes.finance?.pv_incremental?.npv?.median ?? currentRes.finance?.pv_incremental?.npv_eur?.median ?? null));
  const currEqNpv = currentRes.finance?.solar_equity?.npv_mean !== undefined
    ? currentRes.finance.solar_equity.npv_mean
    : (currentRes.finance?.solar_equity?.npv?.mean !== undefined
      ? currentRes.finance.solar_equity.npv.mean
      : (currentRes.finance?.solar_equity?.npv?.median ?? currentRes.finance?.solar_equity?.npv_eur?.median ?? null));
  const pbEnv = isEquityBasis ? currentRes.finance?.solar_equity : currentRes.finance?.pv_incremental;
  let currPb = null;
  const pbOfMean = pbEnv?.discounted_payback_of_mean_cash_flows !== undefined
    ? pbEnv.discounted_payback_of_mean_cash_flows
    : (!isEquityBasis ? currentRes.metrics?.discounted_payback_of_mean_cash_flows : undefined);
  if (pbOfMean !== undefined) {
    if (pbOfMean === null) {
      currPb = 'Not reached';
    } else if (Number.isFinite(Number(pbOfMean))) {
      currPb = Number(pbOfMean);
    }
  } else if (pbEnv && pbEnv.discounted_payback !== undefined && pbEnv.discounted_payback !== null) {
    const med = pbEnv.discounted_payback?.median;
    if (med !== null && med !== undefined && Number.isFinite(Number(med))) {
      currPb = Number(med);
    } else if (med === null) {
      currPb = 'Not reached';
    }
  } else if (!isEquityBasis && currentRes.metrics?.discounted_payback_years !== undefined) {
    const rawVal = currentRes.metrics.discounted_payback_years;
    if (rawVal === null) {
      currPb = 'Not reached';
    } else {
      const med = typeof rawVal === 'number' ? rawVal : rawVal?.median;
      if (med !== null && med !== undefined && Number.isFinite(Number(med))) {
        currPb = Number(med);
      } else if (med === null) {
        currPb = 'Not reached';
      }
    }
  }
  const curFlows = (typeof getCanonicalEnsembleFlows === 'function' ? getCanonicalEnsembleFlows(currentRes) : null) ?? currentRes.headline_energy_flows ?? null;
  const currAut = curFlows?.autarky ?? currentRes.energy?.ensemble_autarky ?? (currentRes.metrics?.autarky?.mean !== undefined ? currentRes.metrics.autarky.mean : (currentRes.metrics?.autarky?.median ?? null));
  const currSc = curFlows?.self_consumption ?? (curFlows?.pv_generation_kwh > 0 && curFlows?.direct_use_kwh != null && curFlows?.battery_charge_kwh != null ? (curFlows.direct_use_kwh + curFlows.battery_charge_kwh) / curFlows.pv_generation_kwh : (curFlows?.pv_generation_kwh > 0 && curFlows?.grid_export_kwh != null ? (curFlows.pv_generation_kwh - curFlows.grid_export_kwh) / curFlows.pv_generation_kwh : null)) ?? (currentRes.metrics?.self_consumption?.mean !== undefined ? currentRes.metrics.self_consumption.mean : (currentRes.metrics?.self_consumption?.median ?? null));

  const getCandProp=(cand,prop)=>{
    if(!cand)return null;
    if(prop==='capex') return cand.capex_eur??cand.finance?.capex_eur;
    if(prop==='equity') return cand.initial_solar_equity_eur ?? (cand.capex_eur - (cand.solar_loan_principal_eur ?? 0));
    if(prop==='loan') return cand.solar_loan_principal_eur ?? 0;
    if(prop==='projNpv') return cand.project_npv_mean !== undefined ? cand.project_npv_mean : (cand.project_npv_median !== undefined ? cand.project_npv_median : (finBasis === 'project' ? (cand.npv_mean !== undefined ? cand.npv_mean : (cand.npv_median ?? null)) : null));
    if(prop==='eqNpv') return cand.equity_npv_mean !== undefined ? cand.equity_npv_mean : (cand.equity_npv_median !== undefined ? cand.equity_npv_median : (finBasis === 'after_financing' ? (cand.npv_mean !== undefined ? cand.npv_mean : (cand.npv_median ?? null)) : null));
    if(prop==='payback') {
      const rawPb = cand.discounted_payback_of_mean_cash_flows !== undefined
        ? cand.discounted_payback_of_mean_cash_flows
        : (cand.discounted_payback_years !== undefined
          ? cand.discounted_payback_years
          : (cand.metrics?.discounted_payback_of_mean_cash_flows !== undefined
            ? cand.metrics.discounted_payback_of_mean_cash_flows
            : (cand.metrics?.discounted_payback_years !== undefined
              ? cand.metrics.discounted_payback_years
              : null)));
      if (rawPb === null) return 'Not reached';
      const pb = (rawPb && typeof rawPb === 'object') ? rawPb.median : rawPb;
      if (pb !== null && pb !== undefined && Number.isFinite(Number(pb))) {
        return Number(pb);
      }
      if (pb === null) return 'Not reached';
      return null;
    }
    if(prop==='autarky') return cand.autarky_mean !== undefined ? cand.autarky_mean : (cand.autarky_median ?? cand.autarky ?? cand.metrics?.autarky?.mean ?? cand.metrics?.autarky?.median);
    if(prop==='selfCons') return cand.self_consumption_mean !== undefined ? cand.self_consumption_mean : (cand.self_consumption_median ?? cand.self_consumption ?? cand.metrics?.self_consumption?.mean ?? cand.metrics?.self_consumption?.median);
    return null;
  };

  const getRoleReason=(roleKey)=>choices[roleKey] ? null : (choices.role_status?.[roleKey]?.reason || 'Unavailable');

  const configs=[
    {name:'Current configuration',badge:'Active',kwp:currentRes.layout?.kwp,panels:currentRes.layout?.panel_count,bat:currentRes.settings?.battery_kwh,capex:currCapex,equity:currEquity,loan:currLoan,projNpv:currProjNpv,eqNpv:currEqNpv,payback:currPb,autarky:currAut,selfCons:currSc,cand:null,rawItem:currentRes,reason:null},
    {name:'Best return (financial)',badge:'Financial',kwp:choices.financial?.kwp,panels:choices.financial?.panel_count,bat:choices.financial?.battery_kwh,capex:getCandProp(choices.financial,'capex'),equity:getCandProp(choices.financial,'equity'),loan:getCandProp(choices.financial,'loan'),projNpv:getCandProp(choices.financial,'projNpv'),eqNpv:getCandProp(choices.financial,'eqNpv'),payback:getCandProp(choices.financial,'payback'),autarky:getCandProp(choices.financial,'autarky'),selfCons:getCandProp(choices.financial,'selfCons'),cand:choices.financial,rawItem:choices.financial,reason:getRoleReason('financial')},
    {name:'Balanced choice',badge:'Recommended',kwp:choices.balanced?.kwp,panels:choices.balanced?.panel_count,bat:choices.balanced?.battery_kwh,capex:getCandProp(choices.balanced,'capex'),equity:getCandProp(choices.balanced,'equity'),loan:getCandProp(choices.balanced,'loan'),projNpv:getCandProp(choices.balanced,'projNpv'),eqNpv:getCandProp(choices.balanced,'eqNpv'),payback:getCandProp(choices.balanced,'payback'),autarky:getCandProp(choices.balanced,'autarky'),selfCons:getCandProp(choices.balanced,'selfCons'),cand:choices.balanced,rawItem:choices.balanced,reason:getRoleReason('balanced')},
    {name:'Budget coverage',badge:'Budget',kwp:choices.budget?.kwp,panels:choices.budget?.panel_count,bat:choices.budget?.battery_kwh,capex:getCandProp(choices.budget,'capex'),equity:getCandProp(choices.budget,'equity'),loan:getCandProp(choices.budget,'loan'),projNpv:getCandProp(choices.budget,'projNpv'),eqNpv:getCandProp(choices.budget,'eqNpv'),payback:getCandProp(choices.budget,'payback'),autarky:getCandProp(choices.budget,'autarky'),selfCons:getCandProp(choices.budget,'selfCons'),cand:choices.budget,rawItem:choices.budget,reason:getRoleReason('budget')}
  ];

  if (choices.smaller || (choices.role_status && choices.role_status.smaller)) {
    configs.push({
      name:'Lower-cost alternative',badge:'Smaller',kwp:choices.smaller?.kwp,panels:choices.smaller?.panel_count,bat:choices.smaller?.battery_kwh,capex:getCandProp(choices.smaller,'capex'),equity:getCandProp(choices.smaller,'equity'),loan:getCandProp(choices.smaller,'loan'),projNpv:getCandProp(choices.smaller,'projNpv'),eqNpv:getCandProp(choices.smaller,'eqNpv'),payback:getCandProp(choices.smaller,'payback'),autarky:getCandProp(choices.smaller,'autarky'),selfCons:getCandProp(choices.smaller,'selfCons'),cand:choices.smaller,rawItem:choices.smaller,reason:getRoleReason('smaller')
    });
  }

  const staleNotice = isStaleOrRunning ? `<div class="stale-comparison-notice" role="status" style="margin-bottom:12px;padding:8px 12px;background:#fff8e9;border:1px solid #e6d49f;border-radius:6px;font-size:12px;color:#705018;">⚠️ Sizing comparisons reflect previous run. Click <strong>Run sizing analysis</strong> to refresh recommendations for new inputs.</div>` : '';

  let html=`
    ${staleNotice}
    <table class="design-unified-table ${isStaleOrRunning?'stale-table':''}">
      <thead>
        <tr>
          <th>Configuration</th>
          <th>Solar array</th>
          <th>Usable storage</th>
          <th>Turnkey capex</th>
          <th>Initial equity</th>
          <th>Loan principal</th>
          <th>Project NPV ${!isEquityBasis ? '<span class="tag">Ranked</span>' : ''}</th>
          <th>Equity NPV ${isEquityBasis ? '<span class="tag">Ranked</span>' : ''}</th>
          <th><abbr title="Discounted payback">Disc. payback</abbr></th>
          <th>Autarky</th>
          <th><abbr title="Self-consumption">Self-cons.</abbr></th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${configs.map((c,i)=>`
          <tr class="${i===0?'current-row':''}">
            <td data-label="Configuration"><strong>${esc(c.name)}</strong> <span class="tag ${c.badge.toLowerCase().replace(/[^a-z0-9]+/g,'-')}">${esc(c.badge)}</span></td>
            <td data-label="Solar array">${c.kwp!=null?`${nf(c.kwp,1)} kWp (${c.panels}p)`:'—'}</td>
            <td data-label="Usable storage">${c.rawItem?formatBatteryComposition(c.rawItem,{short:true}):(c.bat!=null?`${nf(c.bat,1)} kWh`:'—')}</td>
            <td data-label="Turnkey capex">${c.capex!=null?fmt(c.capex,'eur'):'—'}</td>
            <td data-label="Initial equity">${c.equity!=null?fmt(c.equity,'eur'):'—'}</td>
            <td data-label="Loan principal">${c.loan!=null?fmt(c.loan,'eur'):'—'}</td>
            <td data-label="Project NPV" class="${!isEquityBasis ? 'highlight-cell' : ''}"><strong>${c.projNpv!=null?fmt(c.projNpv,'eur'):'—'}</strong></td>
            <td data-label="Equity NPV" class="${isEquityBasis ? 'highlight-cell' : ''}"><strong>${c.eqNpv!=null?fmt(c.eqNpv,'eur'):'—'}</strong></td>
            <td data-label="Discounted payback">${c.payback!=null&&typeof c.payback==='number'?fmt(c.payback,'years'):(c.payback||'—')}</td>
            <td data-label="Autarky">${c.autarky!=null?fmt(c.autarky,'pct'):'—'}</td>
            <td data-label="Self-consumption">${c.selfCons!=null?fmt(c.selfCons,'pct'):'—'}</td>
            <td data-label="Action">${i===0?'<span class="muted">Active</span>':(c.cand?`<button type="button" class="action-btn-small solar-choice-apply" data-apply-config-idx="${i}" ${isStaleOrRunning?'disabled':''}>Apply</button>`:`<span class="muted" title="${esc(c.reason||'Unavailable')}">Unavailable</span>`)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  el.innerHTML=html;
  el.onclick=e=>{
    const btn=e.target.closest?e.target.closest('button[data-apply-config-idx]'):(e.target.dataset?.applyConfigIdx!==undefined?e.target:null);
    if(!btn||btn.disabled)return;
    btn.disabled=true;
    e.stopPropagation();
    const idx=Number(btn.dataset.applyConfigIdx);
    const target=configs[idx]?.cand;
    if(target)applySolarCandidate(target,configs[idx].name);
  };
}

function renderRoofPills(){
 const el=$('solarRoofPills');
 if(!el||!current?.result?.layout)return;
 const facets=(typeof activeResultProfile!=='undefined'&&activeResultProfile)?activeResultProfile.roofs.map(r=>[esc(r.label),settings.roof_panels?.[r.id]||0]):[
  ['South',settings.south_panels],
  ['West',settings.west_panels],
  ['East',settings.east_panels],
  ['Dormer',settings.dormer_panels],
  ['Flat',settings.flat_panels],
  ['Secondary South',settings.secondary_south_panels]
 ].filter(([_,count])=>Number(count)>0);
 el.innerHTML=facets.map(([label,count])=>`<span>${label}: <strong>${count} panels</strong> (${nf(count*(settings.panel_watts||400)/1000,2)} kWp)</span>`).join('');
}

function setBalanceView(view){
 activeBalanceView=view==='monthly'?'monthly':'annual';
 const annual=$('annualEnergyFlowVisual'),monthly=$('monthlyBalanceVisual');
 if(annual)annual.hidden=activeBalanceView!=='annual';
 if(monthly)monthly.hidden=activeBalanceView!=='monthly';
 document.querySelectorAll('[data-balance-view]').forEach(btn=>{
  const selected=btn.dataset.balanceView===activeBalanceView;
  btn.classList.toggle('active',selected);
  btn.setAttribute('aria-pressed',String(selected));
 });
 renderAnnualEnergyFlow();
}

function renderMonthlyBalance(){
 const el=$('monthlyBalanceVisual');
 if(!el)return;
 const key=`${settings.load_scenario||'electrified'}_${inputFingerprint(settings,quote)}`;
 if(!current?.result||!behaviorMatrixCache||behaviorMatrixKey!==key){
  el.innerHTML='<p class="muted" role="status">Monthly balance simulation pending…</p>';
  return;
 }
 const rows=behaviorMatrixCache.monthly_balance;
 const fields=['load_demand_kwh','direct_use_kwh','battery_discharge_kwh','grid_import_kwh','grid_export_kwh'];
 const valid=Array.isArray(rows)&&rows.length===12&&rows.every((r,i)=>r&&r.month===i+1&&fields.every(k=>r[k]!=null&&r[k]!==''&&Number.isFinite(Number(r[k]))&&Number(r[k])>=0));
 if(!valid){
  el.innerHTML='<p class="muted" role="status">Monthly balance data is unavailable for this configuration. Retry the dispatch simulation.</p>';
  return;
 }
 const names=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
 const series=[['direct_use_kwh','Direct Solar → Home','#217a61'],['battery_discharge_kwh','Battery → Home','#e6b65b'],['grid_import_kwh','Grid Import → Home','#9865a8']];
 const width=960,height=360,left=70,right=890,top=38,bottom=300;
 const step=(right-left)/12,bar=step*.32;
 const maxKwh=Math.max(1,...rows.map(r=>Math.max(Number(r.load_demand_kwh),Number(r.direct_use_kwh)+Number(r.battery_discharge_kwh)+Number(r.grid_import_kwh),Number(r.grid_export_kwh))))*1.1;
 const y=v=>bottom-(v/maxKwh)*(bottom-top);
 const yPct=v=>bottom-v/100*(bottom-top);
 let body=`<text x="${left}" y="18" class="monthly-axis">Monthly energy (kWh)</text><text x="${right}" y="18" text-anchor="end" class="monthly-axis">Monthly Autarky (%)</text>`;
 for(let i=0;i<=4;i++){
  const yy=bottom-i/4*(bottom-top);
  body+=`<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" class="monthly-grid"/><text x="${left-8}" y="${yy+4}" text-anchor="end" class="monthly-axis">${nf(maxKwh*i/4,0)}</text><text x="${right+8}" y="${yy+4}" class="monthly-axis">${i*25}%</text>`;
 }
 let line='',previousDefined=false;
 rows.forEach((r,i)=>{
  const cx=left+(i+.5)*step;
  let stacked=0;
  series.forEach(([field,label,color])=>{
   const value=Number(r[field]);
   const tip=`${names[i]} · ${label}: ${nf(value,1)} kWh`;
   body+=`<rect x="${cx-bar-2}" y="${y(stacked+value)}" width="${bar}" height="${y(stacked)-y(stacked+value)}" fill="${color}" tabindex="0" role="img" aria-label="${esc(tip)}"><title>${esc(tip)}</title></rect>`;
   stacked+=value;
  });
  const exports=Number(r.grid_export_kwh),exportTip=`${names[i]} · Grid Exports: ${nf(exports,1)} kWh`;
  body+=`<rect x="${cx+2}" y="${y(exports)}" width="${bar}" height="${bottom-y(exports)}" fill="#6285bd" tabindex="0" role="img" aria-label="${esc(exportTip)}"><title>${esc(exportTip)}</title></rect><text x="${cx}" y="${bottom+24}" text-anchor="middle" class="monthly-axis">${names[i]}</text>`;
  const demand=Number(r.load_demand_kwh);
  const autarky=demand>0?Math.max(0,Math.min(100,100*(Number(r.direct_use_kwh)+Number(r.battery_discharge_kwh))/demand)):null;
  if(autarky===null){
   previousDefined=false;
   body+=`<text x="${cx}" y="${bottom+42}" text-anchor="middle" class="monthly-axis">N/A</text>`;
  }else{
   line+=`${previousDefined?'L':'M'}${cx},${yPct(autarky)} `;
   previousDefined=true;
   const tip=`${names[i]} · Monthly Autarky: ${nf(autarky,1)}% · Household demand: ${nf(demand,1)} kWh`;
   body+=`<circle cx="${cx}" cy="${yPct(autarky)}" r="4" class="monthly-autarky-point" tabindex="0" role="img" aria-label="${esc(tip)}"><title>${esc(tip)}</title></circle>`;
  }
 });
 body+=`<path d="${line}" fill="none" class="monthly-autarky-line" pointer-events="none"/>`;
 const legend=series.concat([['grid_export_kwh','Grid Exports','#6285bd']]).map(([,label,color])=>`<span style="--series:${color}">${label}</span>`).join('');
 	const bmWeatherCount = behaviorMatrixCache.weather_count || behaviorMatrixCache.weather_year_count || current?.result?.weather_years?.length || current?.result?.weather_count || 0;
	const bmWeatherText = bmWeatherCount ? `${nf(bmWeatherCount, 0)} historical weather years` : 'historical weather ensemble';
	el.innerHTML=`<div class="chart-legend">${legend}<span class="monthly-autarky-legend">Monthly Autarky (%) · right axis</span></div><div class="monthly-balance-scroll"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly energy balance January to December: stacked household demand, separate grid exports, and monthly autarky on the right axis">${body}</svg></div><p class="muted">Monthly totals averaged across ${bmWeatherText}, using the same dispatch as the annual Sankey. Demand = direct solar + battery discharge + grid imports. Autarky = (direct solar + battery discharge) / demand; N/A for zero demand.</p>`;
}

function renderAnnualEnergyFlow(){
 if(typeof renderMonthlyBalance==='function')renderMonthlyBalance();
 renderPvClippingBreakdown(behaviorMatrixCache);
 const el=$('annualEnergyFlowVisual');
 if(!el)return;
 const titleMetaEl=$('annualFlowMeta');
 const r=current?.result;
 if(!r){
  if(titleMetaEl) titleMetaEl.innerHTML='';
  el.innerHTML='<div class="annual-flow-pending" role="status"><p class="muted">Scenario calculation pending. Energy flow will update once active scenario completes.</p></div>';
  return;
 }
 const scen=settings.load_scenario||'electrified';
 const curFp=inputFingerprint(settings,quote);
 const key=`${scen}_${curFp}`;
 if(!behaviorMatrixCache?.annual_flows || behaviorMatrixKey!==key){
  if(titleMetaEl) titleMetaEl.innerHTML='';
  el.innerHTML=`<div class="annual-flow-pending" role="status"><p class="muted">Loading annual energy flow simulation for ${esc(title(scen))}…</p></div>`;
  return;
 }
 const flows=behaviorMatrixCache.annual_flows;
 const dcCoupled=(behaviorMatrixCache.selected_config?.pv_battery_coupling||r.settings?.pv_battery_coupling||settings.pv_battery_coupling)==='dc_coupled';

 // Coherent field protection: validate finite numeric required fields for all 6 paths and SOC reconciliation
 const requiredFields=[
  'pv_generation_kwh',
  'load_demand_kwh',
  'grid_import_kwh',
  'grid_export_kwh',
  'direct_use_kwh',
  'battery_charge_kwh',
  'battery_discharge_kwh',
  'storage_losses_kwh',
  'initial_soc_kwh',
  'end_soc_kwh',
  'storage_delta_kwh'
 ];
 const hasAllRequired=requiredFields.every(f=>{
  const v=flows[f];
  return v!==null && v!==undefined && v!=='' && Number.isFinite(Number(v));
 });
 if(!hasAllRequired){
  if(titleMetaEl) titleMetaEl.innerHTML='';
  el.innerHTML='<div class="annual-flow-unavailable" role="status"><p class="muted">Annual energy flow data is unavailable for this configuration.</p></div>';
  return;
 }

 const pv=Number(flows.pv_generation_kwh);
 const demand=Number(flows.load_demand_kwh);
 const imports=Number(flows.grid_import_kwh);
 const exports=Number(flows.grid_export_kwh);
 const direct=Number(flows.direct_use_kwh);
 const charge=Number(flows.battery_charge_kwh);
 const discharge=Number(flows.battery_discharge_kwh);
 const storageLosses=Number(flows.storage_losses_kwh);
 const initialSoc=Number(flows.initial_soc_kwh);
 const endSoc=Number(flows.end_soc_kwh);
 const storageDelta=Number(flows.storage_delta_kwh);
 const hasClipped=(flows.clipped_kwh!==null && flows.clipped_kwh!==undefined && flows.clipped_kwh!=='' && Number.isFinite(Number(flows.clipped_kwh)));
 const clipped=hasClipped?Number(flows.clipped_kwh):null;
 const chargeSplit=typeof routingChargingSplit==='function'?routingChargingSplit(flows):null;
 const routingModeLabel=typeof routeDisplayForResult==='function'?routeDisplayForResult(current?.result):'';

 const forecastYear=behaviorMatrixCache.year??getForecastYear();
 const isWeatherMean=flows.statistic==='weather_mean';
 const weatherYear=flows.weather_year??behaviorMatrixCache.weather_year;
 const weatherAnalogueLabel=weatherYear!=null?`Year ${weatherYear}`:'Selected analogue';
 const weatherCount=flows.weather_count??flows.weather_year_count??flows.weather_years?.length??behaviorMatrixCache.weather_count??behaviorMatrixCache.weather_year_count??behaviorMatrixCache.weather_years?.length??current?.result?.weather_count??current?.result?.weather_years?.length??0;

 const installedBatteryKwh=Number((behaviorMatrixCache.battery_kwh!=null?behaviorMatrixCache.battery_kwh:current?.result?.settings?.battery_kwh)??0);
 const hasStorageInstalled=installedBatteryKwh>0.001;
 const storageActive=charge>0.001||discharge>0.001;

 function fmtCapacity(kwh){
  if(kwh==null||!Number.isFinite(Number(kwh)))return '—';
  const n=Number(kwh);
  if(n<=0.001)return '0 kWh';
  const d=n%1===0?0:(n*10)%1===0?1:2;
  return `${n.toLocaleString('en-IE',{minimumFractionDigits:d,maximumFractionDigits:2})} kWh`;
 }

 function fmtFlowKwh(val){
  if(val==null||!Number.isFinite(Number(val)))return '—';
  const n=Number(val);
  if(n===0)return '0 kWh';
  if(Math.abs(n)>0 && Math.abs(n)<0.05)return '<0.1 kWh';
  if(Math.abs(n)<1.0){
   return `${n.toLocaleString('en-IE',{minimumFractionDigits:1,maximumFractionDigits:1})} kWh`;
  }
  const d=n%1!==0?1:0;
  return `${n.toLocaleString('en-IE',{minimumFractionDigits:d,maximumFractionDigits:d})} kWh`;
 }

 function formatFlowRange(rng){
  if(!rng||typeof rng!=='object')return '—';
  const p10=rng.p10;
  const p90=rng.p90;
  if(p10===null||p10===undefined||p10===''||p90===null||p90===undefined||p90==='')return '—';
  const n10=Number(p10);
  const n90=Number(p90);
  if(!Number.isFinite(n10)||!Number.isFinite(n90)||n10>n90)return '—';
  return `${fmtFlowKwh(n10)} – ${fmtFlowKwh(n90)}`;
 }

 function flowTooltip(key,label,val){
  const str=fmtFlowKwh(val);
  const rngStr=formatFlowRange(flows.flow_ranges?.[key]);
  return rngStr!=='—'?`${label}: ${str} (Historical P10–P90: ${rngStr})`:`${label}: ${str}`;
 }

 function explainResidual(diff,targetName){
  const abs=Math.abs(diff);
  const sign=diff>=0?'+':'';
  const diffStr=`${sign}${diff.toFixed(1)} kWh`;
  if(abs<0.001){
   return `residual difference vs ${targetName}: 0.0 kWh (exact physical balance)`;
  }
  if(abs<=0.25){
   return `residual difference vs ${targetName}: ${diffStr} (consistent with expected ±0.1 kWh decimal rounding across individual flow components)`;
  }
  return `residual difference vs ${targetName}: ${diffStr} (material backend imbalance in simulation records)`;
 }

 // Title and metadata placed directly beneath annual title once
 const metaHtml=isWeatherMean
  ?`Operating year: <strong>${forecastYear}</strong> · Weather: <strong>Average across ${weatherCount} historical weather years</strong> <small class="muted">(${weatherCount} historical PVGIS records)</small> · Scenario: <strong>${esc(title(scen))}</strong>`
  :`Operating year: <strong>${forecastYear}</strong> · Selected weather analogue: <strong>${weatherAnalogueLabel}</strong> <small class="muted">(historical single-year trace representing median annual PV yield among ${weatherCount} PVGIS weather records)</small> · Scenario: <strong>${esc(title(scen))}</strong>`;
 if(titleMetaEl){
  titleMetaEl.innerHTML=metaHtml;
 }

 // 1. Main takeaway above diagram: self-sufficiency with explicit denominators
 let selfSufficiencyHtml='';
 if(demand<=0.001){
  selfSufficiencyHtml=`
   <div class="flow-takeaway-metric">
    <div class="flow-takeaway-label">Household Self-Sufficiency (Autarky)</div>
    <div class="flow-takeaway-val">Not applicable</div>
    <div class="flow-takeaway-desc">Zero household electricity demand recorded under the selected scenario (${esc(title(scen))}).</div>
   </div>
  `;
 } else {
  const selfSuffKwh=Math.max(0,demand-imports);
  const selfSuffPct=Math.min(100,Math.max(0,(selfSuffKwh/demand)*100));
  let autarkyPct=selfSuffPct;
  if(isWeatherMean&&flows.autarky!=null&&flows.autarky!==''&&Number.isFinite(Number(flows.autarky))&&Number(flows.autarky)>=0&&Number(flows.autarky)<=1){
   autarkyPct=Number(flows.autarky)*100;
  }
  autarkyPct=Math.min(100,Math.max(0,autarkyPct));

  let autarkyRangeStr='';
  const autRange=flows.flow_ranges?.autarky;
  if(autRange&&typeof autRange==='object'){
   const p10=autRange.p10;
   const p90=autRange.p90;
   if(p10!=null&&p90!=null&&p10!==''&&p90!==''){
    const n10=Number(p10);
    const n90=Number(p90);
    if(Number.isFinite(n10)&&Number.isFinite(n90)&&n10>=0&&n10<=n90&&n90<=1){
     const p10Pct=n10*100;
     const p90Pct=n90*100;
     autarkyRangeStr=`${p10Pct.toFixed(1)}%–${p90Pct.toFixed(1)}%`;
    }
   }
  }

  const directPctDemand=(direct/demand*100).toFixed(1);
  const batPctDemand=(discharge/demand*100).toFixed(1);
  const impPctDemand=(imports/demand*100).toFixed(1);
  selfSufficiencyHtml=`
   <div class="flow-takeaway-metric">
    <div class="flow-takeaway-label">Household Self-Sufficiency (Autarky)</div>
    <div class="flow-takeaway-val">${autarkyPct.toFixed(1)}%${autarkyRangeStr?` <span class="muted" style="font-size:14px;font-weight:500;">(P10–P90: ${esc(autarkyRangeStr)})</span>`:''}</div>
    <div class="flow-takeaway-desc">
     <strong>${fmtFlowKwh(selfSuffKwh)}/yr</strong> of <strong>${fmtFlowKwh(demand)}/yr</strong> household demand supplied on-site
     (${directPctDemand}% of household demand direct solar${storageActive?` + ${batPctDemand}% of household demand battery discharge`:''}).
     Residual grid imports: <strong>${fmtFlowKwh(imports)}/yr</strong> (${impPctDemand}% of household demand).
    </div>
    <div class="flow-routing-desc muted">${isWeatherMean?`Flows are annual averages across ${weatherCount} historical weather years. Autarky is 1 − average imports / average demand, matching the switch-hour table for the same applied settings. P10–P90 describes historical weather variation.`:`These flows and autarky use the selected weather analogue (${weatherAnalogueLabel}).`}</div>
    <div class="flow-routing-desc muted">Generation routes directly to household loads, battery charging, and grid exports; storage discharges during deficits.${flows.energy_reference==='ac_equivalent'?' PV and flow balances use AC-equivalent kWh; battery capacity uses stored kWh.':''}</div>
   </div>
  `;
 }

 const takeawayHeader=`
  <div class="annual-flow-takeaway">
   ${selfSufficiencyHtml}
  </div>
 `;

 // 2. Desktop SVG Sankey: proportional continuous bands on a shared scale factor
 let desktopSankeyHtml='';
 const totalSystemKwh=Math.max(0,imports+pv);

 const directPctPv=pv>0?(direct/pv*100).toFixed(1):'0.0';
 const chargePctPv=pv>0?(charge/pv*100).toFixed(1):'0.0';
 const exportPctPv=pv>0?(exports/pv*100).toFixed(1):'0.0';
 const directPctDem=demand>0?(direct/demand*100).toFixed(1):'0.0';
 const dischPctDem=demand>0?(discharge/demand*100).toFixed(1):'0.0';
 const importPctDem=demand>0?(imports/demand*100).toFixed(1):'0.0';

 const modeledRte=Number(current?.result?.settings?.roundtrip_efficiency??0.90);
 const modeledRtePct=(modeledRte*100).toFixed(0);
 const dischShareOfCharge=charge>0.001?(discharge/charge*100).toFixed(1):'0.0';

 if(totalSystemKwh<=0.001){
  desktopSankeyHtml='<div class="annual-flow-empty"><p class="muted">No annual energy generation or import flows recorded for this configuration.</p></div>';
 } else {
  const xLeft=160,wNode=14,wBat=148;
  const xRight=820;
  const xMid=Math.round((xLeft+wNode+xRight)/2 - wBat/2); // 425: centered symmetrically between left and right nodes

  // Exact cubic Bézier curve evaluation with analytical x-to-t inversion for ribbonPath (dx = 0.48 * span)
  function bezierTForX(x,x0,x1){
   const span=x1-x0;
   if(span<=1e-4)return 0.5;
   const targetU=Math.max(0,Math.min(1,(x-x0)/span));
   let low=0,high=1;
   for(let i=0;i<20;i++){
    const mid=(low+high)/2;
    const midU=1.44*mid - 1.32*mid*mid + 0.88*mid*mid*mid;
    if(midU<targetU) low=mid;
    else high=mid;
   }
   return (low+high)/2;
  }

  function evalBezierY(t,y0,y1){
   const b=t*t*(3-2*t);
   return (1-b)*y0 + b*y1;
  }

  function evalRibbonAtX(x,x0,y0T,y0B,x1,y1T,y1B){
   const t=bezierTForX(x,x0,x1);
   const yT=evalBezierY(t,y0T,y1T);
   const yB=evalBezierY(t,y0B,y1B);
   return {t, yTop:yT, yBottom:yB, yMid:(yT+yB)/2, thickness:Math.abs(yB-yT)};
  }

  const yTop=22,yBottom=447,availH=yBottom-yTop;
  const middleClearanceBudget=hasStorageInstalled?(storageActive?225:205):185;
  const plotH=Math.max(80,availH-middleClearanceBudget);
  const k=plotH/totalSystemKwh;

  const hImp=imports*k;
  const hPv=pv*k;
  const hDirect=direct*k;
  const hCharge=charge*k;
  const hExp=exports*k;
  const hDisch=discharge*k;
  const hLoss=storageLosses*k;
  const hDem=demand*k;

  // Left column coordinates
  const yImpStart=yTop;
  const yImpEnd=yImpStart+hImp;
  const gapLeft=(imports>0.001 && pv>0.001)?16:0;
  const yPvStart=yImpEnd+gapLeft;
  const yPvEnd=yPvStart+hPv;

  const yDirSrcStart=yPvStart;
  const yDirSrcEnd=yDirSrcStart+hDirect;
  const yChgSrcStart=yDirSrcEnd;
  const yChgSrcEnd=yChgSrcStart+hCharge;
  const yExpSrcStart=yChgSrcEnd;
  const yExpSrcEnd=yExpSrcStart+hExp;

  // Right column coordinates
  const yDemStart=yTop;
  const yDemEnd=yDemStart+hDem;
  const yDemImpStart=yDemStart;
  const yDemImpEnd=yDemImpStart+hImp;
  const yDemDirStart=yDemImpEnd;
  const yDemDirEnd=yDemDirStart+hDirect;
  const yDemDischStart=yDemDirEnd;
  const yDemDischEnd=yDemDischStart+hDisch;

  const gapRightDemLoss=(demand>0.001 && storageLosses>0.001)?12:0;
  const yLossStart=yDemEnd+gapRightDemLoss;
  const yLossEnd=yLossStart+hLoss;

  // Middle Battery coordinates: direct solar passes strictly above battery node with clearance across entire width
  const gapAboveBat=14;
  const gapBelowBat=14;

  const dirAtLeft=evalRibbonAtX(xMid, xLeft+wNode, yDirSrcStart, yDirSrcEnd, xRight, yDemDirStart, yDemDirEnd);
  const dirAtRight=evalRibbonAtX(xMid+wBat, xLeft+wNode, yDirSrcStart, yDirSrcEnd, xRight, yDemDirStart, yDemDirEnd);
  const yDirBottomMax=Math.max(dirAtLeft.yBottom, dirAtRight.yBottom);
  const minBatTop=yDirBottomMax+gapAboveBat+3;

  // Clean battery card height accommodation for large port bands without distorting proportional flow widths
  const baseBatHeight=98;
  const maxPortH=Math.max(hCharge, hDisch+hLoss);
  let batteryHeight=baseBatHeight;
  if(storageActive){
   if(maxPortH>baseBatHeight-20){
    batteryHeight=Math.max(baseBatHeight, Math.ceil(maxPortH+20));
   }
  } else if(hasStorageInstalled){
   batteryHeight=62;
  } else {
   batteryHeight=48;
  }

  let batteryTop=minBatTop;
  if(storageActive){
   batteryTop=Math.max(yChgSrcStart-Math.max(4,(batteryHeight-Math.max(4,hCharge))/2), minBatTop);
  }
  const batteryBottom=batteryTop+batteryHeight;
  const batTextYOffset=(batteryHeight-baseBatHeight)/2;

  // Flow ports at battery node: conserve proportional width without artificial minimum strokes
  const yBatInStart=batteryTop+(batteryHeight-Math.max(4,hCharge))/2;
  const yBatInEnd=yBatInStart+hCharge;
  const yBatDischStart=batteryTop+(batteryHeight-Math.max(4,hDisch+hLoss))/2;
  const yBatDischEnd=yBatDischStart+hDisch;
  const yBatLossStart=yBatDischEnd;
  const yBatLossEnd=yBatLossStart+hLoss;

  // Grid Exports ribbon: continuous cubic segments routing below battery horizontally, then to nearby right export node
  const yUnderBatTop=batteryBottom+gapBelowBat;
  const yUnderBatBottom=yUnderBatTop+hExp;
  const yExpStart=Math.max(yLossEnd+14, yUnderBatTop);
  const yExpEnd=yExpStart+hExp;

  // ViewBox dimensions: dynamically sized from actual diagram extents of PRESENT elements to prevent clipping while avoiding excess blank canvas
  const svgW=1000;
  const presentYs=[batteryBottom];
  if(demand>0.001) presentYs.push(yDemEnd, yDemStart+Math.max(18,hDem/2)+14);
  if(imports>0.001) presentYs.push(yImpEnd, yImpStart+Math.max(18,hImp/2)+14);
  if(pv>0.001) presentYs.push(yPvEnd, yPvStart+Math.max(18,hPv/2)+16);
  if(storageActive&&storageLosses>0.001) presentYs.push(yLossEnd, yLossStart+Math.max(18,hLoss/2)+14);
  if(exports>0.001) presentYs.push(yExpEnd, yExpStart+Math.max(18,hExp/2)+14);
  const maxDiagramY=Math.max(...presentYs);
  const svgH=Math.max(380, Math.ceil(maxDiagramY+18));

  function ribbonPath(x0,y0T,y0B,x1,y1T,y1B){
   if(Math.abs(y0B-y0T)<=1e-4 && Math.abs(y1B-y1T)<=1e-4)return '';
   const dx=(x1-x0)*0.48;
   return `M ${x0.toFixed(2)},${y0T.toFixed(2)} `+
          `C ${(x0+dx).toFixed(2)},${y0T.toFixed(2)} ${(x1-dx).toFixed(2)},${y1T.toFixed(2)} ${x1.toFixed(2)},${y1T.toFixed(2)} `+
          `L ${x1.toFixed(2)},${y1B.toFixed(2)} `+
          `C ${(x1-dx).toFixed(2)},${y1B.toFixed(2)} ${(x0+dx).toFixed(2)},${y0B.toFixed(2)} ${x0.toFixed(2)},${y0B.toFixed(2)} `+
          `Z`;
  }

  // Compact solid color rounded flow value pill (kWh/yr) centered on actual ribbon centerline point
  function flowPill(cx,cy,val,color,flowTitle,rangeKey){
   if(val<=0.001)return '';
   const str=fmtFlowKwh(val);
   const rngStr=rangeKey?formatFlowRange(flows.flow_ranges?.[rangeKey]):'—';
   const tip=rngStr!=='—'?`${flowTitle}: ${str} (Historical P10–P90: ${rngStr})`:`${flowTitle}: ${str}`;
   const w=Math.max(68, Math.round(str.length*6.6 + 16));
   const h=21;
   const x=cx - w/2;
   const y=cy - h/2;
   return `
    <g class="sankey-flow-pill" transform="translate(${x.toFixed(1)},${y.toFixed(1)})" role="note" aria-label="${esc(tip)}">
     <title>${esc(tip)}</title>
     <rect width="${w}" height="${h}" rx="${(h/2).toFixed(1)}" fill="${color}" />
     <text x="${(w/2).toFixed(1)}" y="${(h/2 + 3.8).toFixed(1)}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#ffffff" letter-spacing="0.2">${str}</text>
    </g>
   `;
  }

  // Sharp desktop Sankey node bar polygons matching JointJS reference silhouette:
  // Source bar on left: vertical left edge, horizontal top/bottom, shallow > point at mid-height into outgoing ribbon.
  // Sink bar on right: vertical right edge, horizontal top/bottom, shallow > notch at mid-height meeting incoming flow.
  // Angled depth clamped/scaled for tiny nodes to prevent self-intersection and preserve slim bar appearance.
  function sourceNodePolygon(x,y,w,h,fill){
   const hClamped=Math.max(2,h);
   const d=Math.min(3.5,Math.max(0.4,+(hClamped*0.18).toFixed(2)));
   const yMid=y+hClamped/2;
   const pts=`${x.toFixed(1)},${y.toFixed(1)} ${(x+w).toFixed(1)},${y.toFixed(1)} ${(x+w+d).toFixed(1)},${yMid.toFixed(1)} ${(x+w).toFixed(1)},${(y+hClamped).toFixed(1)} ${x.toFixed(1)},${(y+hClamped).toFixed(1)}`;
   return `<polygon points="${pts}" fill="${fill}" />`;
  }

  function sinkNodePolygon(x,y,w,h,fill){
   const hClamped=Math.max(2,h);
   const d=Math.min(3.5,Math.max(0.4,+(hClamped*0.18).toFixed(2)));
   const yMid=y+hClamped/2;
   const pts=`${(x-d).toFixed(1)},${y.toFixed(1)} ${(x+w).toFixed(1)},${y.toFixed(1)} ${(x+w).toFixed(1)},${(y+hClamped).toFixed(1)} ${(x-d).toFixed(1)},${(y+hClamped).toFixed(1)} ${x.toFixed(1)},${yMid.toFixed(1)}`;
   return `<polygon points="${pts}" fill="${fill}" />`;
  }

  // Continuous Proportional Ribbons (Light Translucent Smooth Style Matching Reference)
  const pImpToDem=imports>0.001?ribbonPath(xLeft+wNode,yImpStart,yImpEnd,xRight,yDemImpStart,yDemImpEnd):'';
  const pSolarToDem=direct>0.001?ribbonPath(xLeft+wNode,yDirSrcStart,yDirSrcEnd,xRight,yDemDirStart,yDemDirEnd):'';
  const pSolarToBat=(storageActive&&charge>0.001)?ribbonPath(xLeft+wNode,yChgSrcStart,yChgSrcEnd,xMid,yBatInStart,yBatInEnd):'';
  const pBatToDem=(storageActive&&discharge>0.001)?ribbonPath(xMid+wBat,yBatDischStart,yBatDischEnd,xRight,yDemDischStart,yDemDischEnd):'';
  const pBatToLoss=(storageActive&&storageLosses>0.001)?ribbonPath(xMid+wBat,yBatLossStart,yBatLossEnd,xRight,yLossStart,yLossEnd):'';
  function exportRibbonPath(x0,y0T,y0B,x1,x2,yUnderT,yUnderB,x3,y1T,y1B){
   if(Math.abs(y0B-y0T)<=1e-4 && Math.abs(y1B-y1T)<=1e-4)return '';
   const dx1=(x1-x0)*0.48;
   const dx2=(x3-x2)*0.48;
   return `M ${x0.toFixed(2)},${y0T.toFixed(2)} `+
          `C ${(x0+dx1).toFixed(2)},${y0T.toFixed(2)} ${(x1-dx1).toFixed(2)},${yUnderT.toFixed(2)} ${x1.toFixed(2)},${yUnderT.toFixed(2)} `+
          `L ${x2.toFixed(2)},${yUnderT.toFixed(2)} `+
          `C ${(x2+dx2).toFixed(2)},${yUnderT.toFixed(2)} ${(x3-dx2).toFixed(2)},${y1T.toFixed(2)} ${x3.toFixed(2)},${y1T.toFixed(2)} `+
          `L ${x3.toFixed(2)},${y1B.toFixed(2)} `+
          `C ${(x3-dx2).toFixed(2)},${y1B.toFixed(2)} ${(x2+dx2).toFixed(2)},${yUnderB.toFixed(2)} ${x2.toFixed(2)},${yUnderB.toFixed(2)} `+
          `L ${x1.toFixed(2)},${yUnderB.toFixed(2)} `+
          `C ${(x1-dx1).toFixed(2)},${yUnderB.toFixed(2)} ${(x0+dx1).toFixed(2)},${y0B.toFixed(2)} ${x0.toFixed(2)},${y0B.toFixed(2)} `+
          `Z`;
  }
  const pSolarToExp=exports>0.001?exportRibbonPath(xLeft+wNode,yExpSrcStart,yExpSrcEnd,xMid,xMid+wBat,yUnderBatTop,yUnderBatBottom,xRight,yExpStart,yExpEnd):'';

  // Distinct lane positions along ribbon centerlines with collision-free clearance
  const posImp=imports>0.001?evalRibbonAtX(490, xLeft+wNode, yImpStart, yImpEnd, xRight, yDemImpStart, yDemImpEnd):null;
  const posDirect=direct>0.001?evalRibbonAtX(350, xLeft+wNode, yDirSrcStart, yDirSrcEnd, xRight, yDemDirStart, yDemDirEnd):null;
  const posCharge=(storageActive&&charge>0.001)?evalRibbonAtX(280, xLeft+wNode, yChgSrcStart, yChgSrcEnd, xMid, yBatInStart, yBatInEnd):null;
  const posDisch=(storageActive&&discharge>0.001)?evalRibbonAtX(710, xMid+wBat, yBatDischStart, yBatDischEnd, xRight, yDemDischStart, yDemDischEnd):null;
  const posLoss=(storageActive&&storageLosses>0.001)?evalRibbonAtX(635, xMid+wBat, yBatLossStart, yBatLossEnd, xRight, yLossStart, yLossEnd):null;
  const posExp=exports>0.001?evalRibbonAtX(685, xMid+wBat, yUnderBatTop, yUnderBatBottom, xRight, yExpStart, yExpEnd):null;

  const flowLegendHtml=`
   <div class="annual-flow-legend" role="list" aria-label="Annual energy flow summary">
    ${direct>0.001?`<div class="flow-legend-item" role="listitem"><span class="flow-legend-dot" style="background:#217a61"></span><span><strong>Direct solar ➔ Household:</strong> ${fmtFlowKwh(direct)} <small class="muted">(${directPctPv}% solar · ${directPctDem}% demand)</small></span></div>`:''}
    ${imports>0.001?`<div class="flow-legend-item" role="listitem"><span class="flow-legend-dot" style="background:#8f5da5"></span><span><strong>Grid imports ➔ Household:</strong> ${fmtFlowKwh(imports)} <small class="muted">(${importPctDem}% demand)</small></span></div>`:''}
    ${(storageActive&&charge>0.001)?`<div class="flow-legend-item" role="listitem"><span class="flow-legend-dot" style="background:#d97724"></span><span><strong>Solar ➔ Battery:</strong> ${fmtFlowKwh(charge)} <small class="muted">(${chargePctPv}% solar)</small></span></div>`:''}
    ${(storageActive&&discharge>0.001)?`<div class="flow-legend-item" role="listitem"><span class="flow-legend-dot" style="background:#df8a54"></span><span><strong>Battery ➔ Household:</strong> ${fmtFlowKwh(discharge)} <small class="muted">(${dischPctDem}% demand)</small></span></div>`:''}
    ${(storageActive&&storageLosses>0.001)?`<div class="flow-legend-item" role="listitem"><span class="flow-legend-dot" style="background:#7d8793"></span><span><strong>Storage losses:</strong> ${fmtFlowKwh(storageLosses)} <small class="muted">(${dischShareOfCharge}% disch · ${(storageLosses/charge*100).toFixed(1)}% losses)</small></span></div>`:''}
    ${exports>0.001?`<div class="flow-legend-item" role="listitem"><span class="flow-legend-dot" style="background:#4a7bb0"></span><span><strong>Solar ➔ Grid exports:</strong> ${fmtFlowKwh(exports)} <small class="muted">(${exportPctPv}% solar)</small></span></div>`:''}
   </div>
  `;

  desktopSankeyHtml=`
   <div class="annual-flow-desktop-container">
    <svg class="annual-flow-sankey-svg" viewBox="0 0 ${svgW} ${svgH}" role="img" aria-labelledby="annualSankeyTitle annualSankeyDesc">
     <title id="annualSankeyTitle">Annual Energy Flow Sankey Diagram (${forecastYear})</title>
     <desc id="annualSankeyDesc">Proportional energy balance showing AC solar generation (${fmtFlowKwh(pv)}), grid imports (${fmtFlowKwh(imports)}), battery storage (${fmtFlowKwh(charge)} charged, ${fmtFlowKwh(discharge)} discharged), and household demand (${fmtFlowKwh(demand)}).</desc>

     <!-- Light Translucent Ribbons (Matching JointJS Reference Style, Opacity ~0.28, No Heavy Borders) -->
     ${pImpToDem?`<path d="${pImpToDem}" fill="#8f5da5" fill-opacity="0.28" stroke="none"><title>Grid imports ➔ Household: ${fmtFlowKwh(imports)} (${(imports/demand*100).toFixed(1)}% of household demand)</title></path>`:''}
     ${pSolarToDem?`<path d="${pSolarToDem}" fill="#217a61" fill-opacity="0.28" stroke="none"><title>Direct solar ➔ Household: ${fmtFlowKwh(direct)} (${(direct/pv*100).toFixed(1)}% of solar generation · ${(direct/demand*100).toFixed(1)}% of household demand)</title></path>`:''}
     ${pSolarToBat?`<path d="${pSolarToBat}" fill="#d97724" fill-opacity="0.30" stroke="none"><title>Solar ➔ Battery: ${fmtFlowKwh(charge)} (${(charge/pv*100).toFixed(1)}% of solar generation)</title></path>`:''}
     ${pBatToDem?`<path d="${pBatToDem}" fill="#df8a54" fill-opacity="0.30" stroke="none"><title>Battery ➔ Household: ${fmtFlowKwh(discharge)} (${(discharge/demand*100).toFixed(1)}% of household demand)</title></path>`:''}
     ${pBatToLoss?`<path d="${pBatToLoss}" fill="#7d8793" fill-opacity="0.28" stroke="none"><title>Storage losses: ${fmtFlowKwh(storageLosses)} (${(storageLosses/charge*100).toFixed(1)}% of battery charge)</title></path>`:''}
     ${pSolarToExp?`<path d="${pSolarToExp}" fill="#4a7bb0" fill-opacity="0.28" stroke="none"><title>Solar ➔ Grid exports: ${fmtFlowKwh(exports)} (${(exports/pv*100).toFixed(1)}% of solar generation)</title></path>`:''}

     <!-- Left Nodes (Narrow Saturated Vertical Bars with Simple External Titles) -->
     ${imports>0.001?`
      ${sourceNodePolygon(xLeft,yImpStart,wNode,hImp,'#7c4d9f')}
      <text x="${xLeft-10}" y="${(yImpStart+Math.max(18,hImp/2)-11).toFixed(1)}" text-anchor="end" class="sankey-node-title" fill="#7c4d9f">Grid imports</text>
      <text x="${xLeft-10}" y="${(yImpStart+Math.max(18,hImp/2)+11).toFixed(1)}" text-anchor="end" class="sankey-node-val"><title>${esc(flowTooltip('grid_import_kwh','Grid imports',imports))}</title>${fmtFlowKwh(imports)}</text>
     `:''}
     ${pv>0.001?`
      ${sourceNodePolygon(xLeft,yPvStart,wNode,hPv,'#217a61')}
      <text x="${xLeft-10}" y="${(yPvStart+Math.max(18,hPv/2)-17).toFixed(1)}" text-anchor="end" class="sankey-node-title" fill="#217a61">
       <tspan x="${xLeft-10}">Usable PV</tspan>
       <tspan x="${xLeft-10}" dy="14">after curtailment</tspan>
      </text>
      <text x="${xLeft-10}" y="${(yPvStart+Math.max(18,hPv/2)+13).toFixed(1)}" text-anchor="end" class="sankey-node-val"><title>${esc(flowTooltip('pv_generation_kwh','Usable PV after curtailment',pv))}</title>${fmtFlowKwh(pv)}</text>
     `:''}

     <!-- Middle Node (Battery) - Clean 148px Wide Node Card with Left-to-Right Flow and Accommodated Ports -->
     ${storageActive?`
      <g class="sankey-battery-node" transform="translate(${xMid}, ${batteryTop.toFixed(1)})">
       <!-- Subtle top terminal accent -->
       <rect x="${(wBat/2-10).toFixed(1)}" y="-3" width="20" height="3" rx="1.5" fill="#df8a54" opacity="0.75" />
       <!-- Battery card outline -->
       <rect x="0" y="0" width="${wBat}" height="${batteryHeight.toFixed(1)}" rx="6" fill="#fffcf9" stroke="#e6a370" stroke-width="1.2" />
       <!-- Left port (charge in) -->
       <rect x="0" y="${(yBatInStart-batteryTop).toFixed(1)}" width="4" height="${Math.max(2,hCharge).toFixed(1)}" rx="1" fill="#df8a54" />
       <!-- Right port (discharge + losses out) -->
       <rect x="${wBat-4}" y="${(yBatDischStart-batteryTop).toFixed(1)}" width="4" height="${Math.max(2,hDisch+hLoss).toFixed(1)}" rx="1" fill="#df8a54" />
       <!-- Card Title and Capacity (19px line separation) -->
       <text x="${(wBat/2).toFixed(1)}" y="${(17+batTextYOffset).toFixed(1)}" text-anchor="middle" font-size="12" font-weight="700" fill="#b45309" letter-spacing="0.2">Battery Storage</text>
       <text x="${(wBat/2).toFixed(1)}" y="${(36+batTextYOffset).toFixed(1)}" text-anchor="middle" font-size="14" font-weight="800" fill="#102820">${fmtCapacity(installedBatteryKwh)}</text>
       <!-- Direction indicator: LEFT to RIGHT -->
       <text x="${(wBat/2).toFixed(1)}" y="${(52+batTextYOffset).toFixed(1)}" text-anchor="middle" font-size="9" font-weight="700" fill="#c2631e" letter-spacing="0.8">IN ➔ OUT</text>
       <!-- Throughput in/out (19px line separation) -->
       <text x="${(wBat/2).toFixed(1)}" y="${(70+batTextYOffset).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="600" fill="#78350f">${fmtFlowKwh(charge)} in</text>
       <text x="${(wBat/2).toFixed(1)}" y="${(89+batTextYOffset).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="600" fill="#78350f">${fmtFlowKwh(discharge)} out</text>
      </g>
     `:hasStorageInstalled?`
      <g class="sankey-battery-node" transform="translate(${xMid}, ${batteryTop.toFixed(1)})">
       <rect x="${(wBat/2-10).toFixed(1)}" y="-3" width="20" height="3" rx="1.5" fill="#df8a54" opacity="0.6" />
       <rect x="0" y="0" width="${wBat}" height="${batteryHeight.toFixed(1)}" rx="6" fill="#fffcf9" stroke="#e6a370" stroke-width="1.2" stroke-dasharray="4,3" />
       <text x="${(wBat/2).toFixed(1)}" y="18" text-anchor="middle" font-size="12" font-weight="700" fill="#b45309" letter-spacing="0.2">Battery Storage</text>
       <text x="${(wBat/2).toFixed(1)}" y="37" text-anchor="middle" font-size="13.5" font-weight="800" fill="#102820">${fmtCapacity(installedBatteryKwh)}</text>
       <text x="${(wBat/2).toFixed(1)}" y="53" text-anchor="middle" font-size="9" font-weight="600" fill="#92400e">Installed · Idle in this scenario</text>
      </g>
     `:`
      <g class="sankey-battery-node" transform="translate(${xMid}, ${batteryTop.toFixed(1)})">
       <rect x="0" y="0" width="${wBat}" height="${batteryHeight.toFixed(1)}" rx="6" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="3,3" />
       <text x="${(wBat/2).toFixed(1)}" y="19" text-anchor="middle" font-size="12" font-weight="700" fill="#64748b">No Battery Storage</text>
       <text x="${(wBat/2).toFixed(1)}" y="36" text-anchor="middle" font-size="10.5" font-weight="500" fill="#94a3b8">(0 kWh capacity)</text>
      </g>
     `}

     <!-- Right Nodes (Slim Dark Sink Bars with Simple External Titles) -->
     ${demand>0.001?`
      ${sinkNodePolygon(xRight,yDemStart,wNode,hDem,'#183b33')}
      <text x="${xRight+wNode+10}" y="${(yDemStart+Math.max(18,hDem/2)-11).toFixed(1)}" text-anchor="start" class="sankey-node-title" fill="#183b33">Household demand</text>
      <text x="${xRight+wNode+10}" y="${(yDemStart+Math.max(18,hDem/2)+11).toFixed(1)}" text-anchor="start" class="sankey-node-val"><title>${esc(flowTooltip('load_demand_kwh','Household demand',demand))}</title>${fmtFlowKwh(demand)}</text>
     `:''}
     ${storageActive&&storageLosses>0.001?`
      ${sinkNodePolygon(xRight,yLossStart,wNode,hLoss,'#646d78')}
      <text x="${xRight+wNode+10}" y="${(yLossStart+Math.max(18,hLoss/2)-11).toFixed(1)}" text-anchor="start" class="sankey-node-title" fill="#646d78">Storage losses</text>
      <text x="${xRight+wNode+10}" y="${(yLossStart+Math.max(18,hLoss/2)+11).toFixed(1)}" text-anchor="start" class="sankey-node-val"><title>${esc(flowTooltip('storage_losses_kwh','Storage losses',storageLosses))}</title>${fmtFlowKwh(storageLosses)}</text>
     `:''}
     ${exports>0.001?`
      ${sinkNodePolygon(xRight,yExpStart,wNode,hExp,'#366291')}
      <text x="${xRight+wNode+10}" y="${(yExpStart+Math.max(18,hExp/2)-11).toFixed(1)}" text-anchor="start" class="sankey-node-title" fill="#366291">Grid exports</text>
      <text x="${xRight+wNode+10}" y="${(yExpStart+Math.max(18,hExp/2)+11).toFixed(1)}" text-anchor="start" class="sankey-node-val"><title>${esc(flowTooltip('grid_export_kwh','Grid exports',exports))}</title>${fmtFlowKwh(exports)}</text>
     `:''}

     <!-- Compact Flow Value Pills on Actual Ribbon Centerlines (Staggered Lanes, Zero Overlap, Reference Style) -->
     ${posImp?flowPill(490, posImp.yMid, imports, '#8f5da5', 'Imports ➔ Household', 'grid_import_kwh'):''}
     ${posDirect?flowPill(350, posDirect.yMid, direct, '#217a61', 'Direct solar ➔ Household', 'direct_use_kwh'):''}
     ${posCharge?flowPill(280, posCharge.yMid, charge, '#d97724', 'Solar ➔ Battery', 'battery_charge_kwh'):''}
     ${posLoss?flowPill(635, posLoss.yMid, storageLosses, '#707b86', 'Storage losses', 'storage_losses_kwh'):''}
     ${posDisch?flowPill(710, posDisch.yMid, discharge, '#df8a54', 'Battery ➔ Household', 'battery_discharge_kwh'):''}
     ${posExp?flowPill(685, posExp.yMid, exports, '#4a7bb0', 'Solar ➔ Grid exports', 'grid_export_kwh'):''}
    </svg>
    ${flowLegendHtml}
   </div>
  `;
 }

 // 3. Mobile Compact Connected Diagram: viewBox 250 with >=12.5px rendered text, edge-to-edge arrows, exports routed around battery
 const mobSvgW=250,mobSvgH=560;
 const mobImportsArrow=imports>0.001?`<line x1="108" y1="40" x2="142" y2="40" stroke="#7c4d9f" stroke-width="2.5" marker-end="url(#mobArrPurple)" />`:'';
 const mobDirectArrow=direct>0.001?`<path d="M 108,160 C 128,160 170,120 170,78" fill="none" stroke="#217a61" stroke-width="2.5" marker-end="url(#mobArrGreen)" />`:'';
 const mobChargeArrow=(storageActive&&charge>0.001)?`<path d="M 65,199 C 65,235 95,235 95,265" fill="none" stroke="#df8a54" stroke-width="2.5" marker-end="url(#mobArrOrange)" />`:'';
 const mobDischargeArrow=(storageActive&&discharge>0.001)?`<path d="M 150,265 C 150,170 205,150 205,78" fill="none" stroke="#df8a54" stroke-width="2.5" marker-end="url(#mobArrOrange)" />`:'';
 const mobLossArrow=(storageActive&&storageLosses>0.001)?`<path d="M 178,335 C 195,335 170,360 170,380" fill="none" stroke="#707b86" stroke-width="2.5" marker-end="url(#mobArrGrey)" />`:'';
 const mobExportArrow=exports>0.001?`<path d="M 28,199 C 18,230 18,350 18,440 C 18,495 80,495 142,495" fill="none" stroke="#4a7bb0" stroke-width="2.5" marker-end="url(#mobArrBlue)" />`:'';

 const mobileDiagramHtml=`
  <div class="annual-flow-mobile-container">
   <svg class="annual-flow-mobile-svg" viewBox="0 0 ${mobSvgW} ${mobSvgH}" role="img" aria-labelledby="mobFlowTitle mobFlowDesc">
    <title id="mobFlowTitle">Mobile Annual Energy Flow Diagram</title>
    <desc id="mobFlowDesc">Connected diagram of solar, grid imports, battery, demand, exports, and losses.</desc>
    <defs>
     <marker id="mobArrPurple" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto"><polygon points="0 0, 6 3, 0 6" fill="#7c4d9f"/></marker>
     <marker id="mobArrGreen" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto"><polygon points="0 0, 6 3, 0 6" fill="#217a61"/></marker>
     <marker id="mobArrOrange" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto"><polygon points="0 0, 6 3, 0 6" fill="#df8a54"/></marker>
     <marker id="mobArrGrey" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto"><polygon points="0 0, 6 3, 0 6" fill="#707b86"/></marker>
     <marker id="mobArrBlue" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto"><polygon points="0 0, 6 3, 0 6" fill="#4a7bb0"/></marker>
    </defs>

    <!-- Connected Edge-to-Edge Arrows -->
    ${mobImportsArrow}
    ${mobDirectArrow}
    ${mobChargeArrow}
    ${mobDischargeArrow}
    ${mobLossArrow}
    ${mobExportArrow}

    <!-- Dedicated Arrow Flow Labels in Reserved Whitespace (No Collisions) -->
    ${direct>0.001?`
     <text x="126" y="114" text-anchor="middle" font-size="11.5" font-weight="700" fill="#217a61">Direct</text>
     <text x="126" y="128" text-anchor="middle" font-size="11.5" font-weight="800" fill="#217a61">${fmtFlowKwh(direct)}</text>
    `:''}
    ${(storageActive&&charge>0.001)?`
     <text x="44" y="228" text-anchor="middle" font-size="11.5" font-weight="700" fill="#d97724">Charge</text>
     <text x="44" y="242" text-anchor="middle" font-size="11.5" font-weight="800" fill="#d97724">${fmtFlowKwh(charge)}</text>
    `:''}
    ${(storageActive&&discharge>0.001)?`
     <text x="198" y="170" text-anchor="middle" font-size="11.5" font-weight="700" fill="#df8a54">Discharge</text>
     <text x="198" y="184" text-anchor="middle" font-size="11.5" font-weight="800" fill="#df8a54">${fmtFlowKwh(discharge)}</text>
    `:''}

    <!-- Nodes with Legible Text (>=12.5px rendered on phone 320/390/430) -->
    <!-- Grid Imports (top left) -->
    <rect x="8" y="14" width="100" height="52" rx="6" fill="#f8f2fb" stroke="#7c4d9f" stroke-width="1.5" />
    <text x="58" y="34" text-anchor="middle" font-size="12.5" font-weight="700" fill="#7c4d9f">Grid imports</text>
    <text x="58" y="53" text-anchor="middle" font-size="14" font-weight="800" fill="#102820">${fmtFlowKwh(imports)}</text>

    <!-- Household Demand (top right) - Title wrapped into 2 lines, no border overflow -->
    <rect x="142" y="14" width="100" height="64" rx="6" fill="#f0f6f3" stroke="#183b33" stroke-width="1.5" />
    <text x="192" y="31" text-anchor="middle" font-size="12" font-weight="700" fill="#183b33">Household</text>
    <text x="192" y="45" text-anchor="middle" font-size="12" font-weight="700" fill="#183b33">demand</text>
    <text x="192" y="66" text-anchor="middle" font-size="14" font-weight="800" fill="#102820">${fmtFlowKwh(demand)}</text>

    <!-- Solar Generation (mid left) -->
    <rect x="8" y="145" width="100" height="54" rx="6" fill="#eef7f2" stroke="#217a61" stroke-width="1.5" />
    <text x="58" y="166" text-anchor="middle" font-size="12.5" font-weight="700" fill="#217a61">Usable PV</text>
    <text x="58" y="186" text-anchor="middle" font-size="14" font-weight="800" fill="#102820">${fmtFlowKwh(pv)}</text>

    <!-- Battery Storage (center) -->
    <rect x="72" y="265" width="106" height="90" rx="6" fill="${hasStorageInstalled?'#fff7ee':'#f5f5f5'}" stroke="${hasStorageInstalled?'#df8a54':'#aaa'}" stroke-width="1.5" ${!storageActive&&hasStorageInstalled?'stroke-dasharray="3,3"':''} />
    ${hasStorageInstalled?`<rect x="117" y="261" width="16" height="4" rx="2" fill="#bf6216" />`:''}
    <text x="125" y="283" text-anchor="middle" font-size="12" font-weight="700" fill="${hasStorageInstalled?'#bf6216':'#777'}">${hasStorageInstalled?'Battery Storage':'No battery'}</text>
    <text x="125" y="301" text-anchor="middle" font-size="13" font-weight="800" fill="#102820">${fmtCapacity(installedBatteryKwh)}</text>
    <text x="125" y="323" text-anchor="middle" font-size="11.5" font-weight="600" fill="#8c6415">${storageActive?`${fmtFlowKwh(charge)} in`:(hasStorageInstalled?'Installed · Idle':'0 kWh')}</text>
    <text x="125" y="341" text-anchor="middle" font-size="11.5" font-weight="600" fill="#8c6415">${storageActive?`${fmtFlowKwh(discharge)} out`:''}</text>

    <!-- Storage Losses (mid right) -->
    <rect x="142" y="380" width="100" height="54" rx="6" fill="#f5f7f9" stroke="#707b86" stroke-width="1.5" />
    <text x="192" y="401" text-anchor="middle" font-size="12" font-weight="700" fill="#646d78">Storage losses</text>
    <text x="192" y="421" text-anchor="middle" font-size="14" font-weight="800" fill="#102820">${fmtFlowKwh(storageLosses)}</text>

    <!-- Grid Exports (bottom right) -->
    <rect x="142" y="470" width="100" height="54" rx="6" fill="#edf4fc" stroke="#4a7bb0" stroke-width="1.5" />
    <text x="192" y="491" text-anchor="middle" font-size="12" font-weight="700" fill="#366291">Grid exports</text>
    <text x="192" y="511" text-anchor="middle" font-size="14" font-weight="800" fill="#102820">${fmtFlowKwh(exports)}</text>
   </svg>
  </div>
 `;

 // 4. Visible Stored Energy Change Note outside both diagrams whenever |storageDelta| materially nonzero
 let deltaNoteHtml='';
 if(Math.abs(storageDelta)>0.05){
  const isGain=storageDelta>0;
  deltaNoteHtml=`
   <div class="flow-storage-delta-note" role="note">
    <div class="delta-note-title">Stored reserve delta (Annual Net Energy Change)</div>
    <div class="delta-note-body">
     Initial storage SOC: <strong>${fmtFlowKwh(initialSoc)}</strong> · Year-end SOC: <strong>${fmtFlowKwh(endSoc)}</strong>
     (Net annual delta: <strong>${isGain?'+':''}${fmtFlowKwh(storageDelta)}</strong>).
     ${isGain
       ?`The battery ends the simulated year holding ${fmtFlowKwh(endSoc)} of stored energy (+${fmtFlowKwh(storageDelta)} net increase). This energy is conserved on-board rather than lost to thermal dissipation (actual conversion losses: <strong>${fmtFlowKwh(storageLosses)}</strong>).`
       :`The battery ends the simulated year with a net reserve withdrawal of ${fmtFlowKwh(Math.abs(storageDelta))} (${fmtFlowKwh(storageDelta)} net reduction), contributing to household supply alongside solar charging.`}
    </div>
   </div>
  `;
 }

 // 5. Details fold: native details closed by default with summary "Energy balance details"
  const mainFlowDefs=[
   {key:'pv_generation_kwh',label:'Usable PV generation',val:pv},
   {key:'load_demand_kwh',label:'Household demand',val:demand},
   {key:'grid_import_kwh',label:'Grid electricity imports',val:imports},
   {key:'grid_export_kwh',label:'Grid electricity exports',val:exports},
   {key:'direct_use_kwh',label:'Direct solar self-consumption',val:direct},
   {key:'battery_charge_kwh',label:'Battery storage charging',val:charge},
   {key:'battery_discharge_kwh',label:'Battery storage discharge',val:discharge},
   {key:'storage_losses_kwh',label:'Storage roundtrip losses',val:storageLosses},
   {key:'storage_delta_kwh',label:'Net stored reserve delta (SOC change)',val:storageDelta}
  ];
  const weatherRangeRowsHtml=mainFlowDefs.map(f=>{
   const valStr=f.key==='storage_delta_kwh'&&Math.abs(f.val)>0.001?(f.val>0?`+${fmtFlowKwh(f.val)}`:fmtFlowKwh(f.val)):fmtFlowKwh(f.val);
   const rngStr=formatFlowRange(flows.flow_ranges?.[f.key]);
   return `<tr><th scope="row">${esc(f.label)}</th><td><strong>${esc(valStr)}</strong></td><td>${esc(rngStr)}</td></tr>`;
  }).join('');

 const detailsFoldHtml=`
  <details class="energy-balance-details">
   <summary>Energy balance details</summary>
   <div class="flow-details-body">
    <!-- Detailed Methodology Box -->
    <div class="flow-metadata-box">
     <strong>Physical Dispatch Simulation Methodology &amp; Energy Conservation:</strong>
     Rigorous energy conservation: usable PV after topology-specific curtailment routes to direct household use, battery charging, and grid exports. Household demand is satisfied by direct solar, battery discharge, and grid imports. Storage roundtrip losses and net changes in state of charge are accounted for explicitly.
      ${isWeatherMean?`Evaluated for operating forecast year <strong>${forecastYear}</strong> as an <strong>Average across ${weatherCount} historical weather years</strong> (${weatherCount} historical PVGIS weather records).
      ${flows.aggregation||flows.aggregation_note||'Arithmetic mean of simulated annual energy flows across all historical weather years; identical physical balance and conservation contracts hold on mean flows.'}
      Annual energy values reflect arithmetic averages across historical weather years, preserving exact energy conservation across all nodes and paths.`:`Evaluated for operating forecast year <strong>${forecastYear}</strong> using historical weather analogue <strong>${weatherAnalogueLabel}</strong> (${weatherCount} historical PVGIS years evaluated).
     ${flows.aggregation||flows.aggregation_note||'Coherent single-weather analogue (median PV yield); flows satisfy rigorous hourly and annual energy conservation.'}
      The analogue represents an authentic single-year dispatch trace chosen for median annual PV yield, preserving physical conservation rather than an artificial vector of independent medians.`}
    </div>

     <!-- Historical Weather Variation Table -->
     <div class="flow-details-section">
      <h3 style="margin:0 0 8px;font-size:13px;color:var(--ink);">Historical weather variation</h3>
      <p class="muted" style="margin:0 0 10px;font-size:12px;line-height:1.4;">
       P10–P90 ranges reflect individual annual totals across ${weatherCount} historical weather years; they represent empirical multi-year weather variation, not confidence intervals or additive balances (individual flow percentiles do not sum to balances).
      </p>
      <div class="flow-details-table-wrap" tabindex="0" role="region" aria-label="Historical weather variation table">
       <table class="flow-data-table" aria-label="Historical Weather Variation">
        <thead>
         <tr>
          <th scope="col">Flow</th>
          <th scope="col">Average annual energy</th>
          <th scope="col">P10–P90</th>
         </tr>
        </thead>
        <tbody>
         ${weatherRangeRowsHtml}
        </tbody>
       </table>
      </div>
     </div>

    <!-- Accessible flows table -->
    <div class="flow-details-section">
     <h3 style="margin:0 0 8px;font-size:13px;color:var(--ink);">Annual Energy Conversion &amp; Routing Accounting</h3>
     <div class="flow-details-table-wrap" tabindex="0" role="region" aria-label="Annual energy conversions table">
      <table class="flow-data-table" aria-label="Annual Energy Conversions and Flows">
       <thead>
        <tr>
         <th scope="col">Flow / Conversion</th>
         <th scope="col">Source</th>
         <th scope="col">Destination</th>
         <th scope="col">Annual Energy</th>
         <th scope="col">% of Solar</th>
         <th scope="col">% of Demand</th>
         <th scope="col">Accounting Role</th>
        </tr>
       </thead>
       <tbody>
        <tr>
         <th scope="row">Direct Solar Self-Consumption</th>
         <td>Usable PV after curtailment</td>
         <td>Household Demand</td>
         <td><strong>${fmtFlowKwh(direct)}</strong></td>
         <td>${directPctPv}% of solar generation</td>
         <td>${directPctDem}% of household demand</td>
         <td>Clean generation powering appliances instantaneously on-site</td>
        </tr>
        <tr>
         <th scope="row">Battery Storage Charging</th>
         <td>Usable PV after curtailment</td>
         <td>Battery Storage</td>
         <td><strong>${fmtFlowKwh(charge)}</strong></td>
         <td>${chargePctPv}% of solar generation</td>
         <td>—</td>
         <td>Daytime surplus solar routed into storage for later dispatch</td>
        </tr>
        <tr>
         <th scope="row">Grid Electricity Exports</th>
         <td>Usable PV after curtailment</td>
         <td>Electrical Grid</td>
         <td><strong>${fmtFlowKwh(exports)}</strong></td>
         <td>${exportPctPv}% of solar generation</td>
         <td>—</td>
         <td>Surplus electricity injected into the national grid under PMG / RID market pricing</td>
        </tr>
        <tr>
         <th scope="row">Battery Storage Discharge</th>
         <td>Battery Storage</td>
         <td>Household Demand</td>
         <td><strong>${fmtFlowKwh(discharge)}</strong></td>
         <td>—</td>
         <td>${dischPctDem}% of household demand</td>
         <td>Stored daytime solar dispatched to supply evening/nighttime loads</td>
        </tr>
        <tr>
         <th scope="row">Storage Roundtrip Losses</th>
         <td>Battery Storage</td>
         <td>Thermal/Conversion Losses</td>
         <td><strong>${fmtFlowKwh(storageLosses)}</strong></td>
         <td>—</td>
         <td>—</td>
         <td>Actual roundtrip losses across battery charge and discharge cycles</td>
        </tr>
        <tr>
         <th scope="row">Grid Electricity Imports</th>
         <td>Electrical Grid</td>
         <td>Household Demand</td>
         <td><strong>${fmtFlowKwh(imports)}</strong></td>
         <td>—</td>
         <td>${importPctDem}% of household demand</td>
         <td>Grid electricity purchased to cover residual household consumption</td>
        </tr>
       </tbody>
      </table>
     </div>
    </div>

    <!-- Reconciliations Grid -->
    <div class="flow-reconciliations-grid">
     <div class="reconciliation-card solar">
      <div class="reconciliation-title">Usable PV Energy Balance</div>
      <div class="reconciliation-equation">PV (${fmt(pv,'number')}) = Direct (${fmt(direct,'number')}) + Charge (${fmt(charge,'number')}) + Export (${fmt(exports,'number')})</div>
      <div class="reconciliation-sub">
       Sum of routes: <strong>${(direct+charge+exports).toFixed(1)} kWh</strong> (${explainResidual(pv-direct-charge-exports,'modeled AC generation')}).
      </div>
     </div>

     <div class="reconciliation-card load">
      <div class="reconciliation-title">Household Demand Balance</div>
      <div class="reconciliation-equation">Demand (${fmt(demand,'number')}) = Direct (${fmt(direct,'number')}) + Battery (${fmt(discharge,'number')}) + Import (${fmt(imports,'number')})</div>
      <div class="reconciliation-sub">
       Sum of supplies: <strong>${(direct+discharge+imports).toFixed(1)} kWh</strong> (${explainResidual(demand-direct-discharge-imports,'total household demand')}).
      </div>
     </div>

     <div class="reconciliation-card storage">
      <div class="reconciliation-title">Battery Storage Conservation</div>
      <div class="reconciliation-equation">Charge (${fmt(charge,'number')}) = Discharge (${fmt(discharge,'number')}) + Losses (${fmt(storageLosses,'number')}) + Stored Delta (${fmt(storageDelta,'number')})</div>
      <div class="reconciliation-sub">
       ${hasStorageInstalled?`
        Installed usable capacity: <strong>${fmtCapacity(installedBatteryKwh)}</strong> (${storageActive?'Active cycling':'Installed · Idle in this scenario'}).<br/>
        ${flows.energy_reference==='ac_equivalent'?'Accounting SOC (AC-equivalent)':'Initial SOC'}: <strong>${fmtFlowKwh(initialSoc)}</strong> · Year-End SOC: <strong>${fmtFlowKwh(endSoc)}</strong> (Net stored delta: <strong>${storageDelta>0?'+':''}${fmtFlowKwh(storageDelta)}</strong>).<br/>
        ${flows.energy_reference==='ac_equivalent'?`Storage losses (AC-equivalent): <strong>${fmtFlowKwh(storageLosses)}</strong>${flows.storage_losses_physical_kwh!=null?` · Physical battery/conversion losses: <strong>${fmtFlowKwh(flows.storage_losses_physical_kwh)}</strong>`:''}${flows.battery_charge_physical_kwh!=null?` · Physical ${esc(flows.physical_charge_basis||'DC')} charge: <strong>${fmtFlowKwh(flows.battery_charge_physical_kwh)}</strong>`:''}.<br/>`:`Actual conversion losses: <strong>${fmtFlowKwh(storageLosses)}</strong> (thermal/electrical dissipation).<br/>`}
        ${flows.end_soc_stored_kwh!=null?`Physical stored energy: start <strong>${fmtFlowKwh(flows.initial_soc_stored_kwh)}</strong> · end <strong>${fmtFlowKwh(flows.end_soc_stored_kwh)}</strong>.<br/>`:''}
        ${flows.energy_reference!=='ac_equivalent'&&storageActive&&charge>0.001?`Modeled equipment roundtrip efficiency: <strong>${modeledRtePct}% RTE</strong> (equipment assumption, split symmetrically as √η). Discharged share of charging: <strong>${dischShareOfCharge}%</strong> (remaining ${(100-Number(dischShareOfCharge)).toFixed(1)}% comprises actual conversion losses${Math.abs(storageDelta)>0.05?' and net stored SOC delta':''}).<br/>`:''}
        ${chargeSplit&&chargeSplit.hasSplit?`Charging source: from normal solar (sub-inverter) <strong>${fmtFlowKwh(chargeSplit.normal??0)}</strong> · from recaptured DC clipping (over-inverter) <strong>${fmtFlowKwh(chargeSplit.recaptured??0)}</strong>.<br/>`:''}
        Sum accounted for: <strong>${(discharge+storageLosses+storageDelta).toFixed(1)} kWh</strong> (${explainResidual(charge-discharge-storageLosses-storageDelta,'total charging input')}).
        ${Math.abs(storageDelta)>0.05?`<br/><em>Note: The battery ends the simulated year holding ${fmtFlowKwh(flows.end_soc_stored_kwh??endSoc)} of remaining stored energy (${(flows.storage_delta_stored_kwh??storageDelta)>0?'+':''}${fmtFlowKwh(flows.storage_delta_stored_kwh??storageDelta)} net annual change). This energy remains conserved on-board rather than lost to thermal dissipation.</em>`:''}
       `:'No battery storage configured (0 kWh capacity). Storage cycling and conversion losses are zero.'}
      </div>
     </div>

     <div class="reconciliation-card clipping">
      <div class="reconciliation-title">${dcCoupled?'PV curtailment after battery charging and AC export':'Inverter clipping before battery charging'}</div>
      <div class="reconciliation-equation">Inverter Clipping Loss = ${hasClipped?`${fmtFlowKwh(clipped)}/yr${chargeSplit&&chargeSplit.clippedPct!=null?` (${nf(chargeSplit.clippedPct*100,2)}% of modeled PV)`:''}`:'unavailable'}</div>
      <div class="reconciliation-sub">
       ${routingModeLabel?`Dispatch routing: <strong>${esc(routingModeLabel)}</strong>.<br/>`:''}
       ${hasClipped
         ?(dcCoupled?'This generic DC-coupled approximation lets PV surplus charge the battery subject to charge power and usable capacity, then serves AC household loads and exports within the inverter limit. Remaining PV is curtailed. The profile is AC-equivalent, not measured DC, and product-specific joint output limits are not represented.':'This AC-equivalent mode caps PV at the selected inverter AC capacity before serving direct household demand, charging the battery, or exporting.')
         :'Curtailment data is unavailable for this simulation trace.'}
      </div>
     </div>
    </div>
   </div>
  </details>
 `;

 el.innerHTML=`
  ${takeawayHeader}
  <div class="annual-flow-diagram-card">
   ${desktopSankeyHtml}
   ${mobileDiagramHtml}
  </div>
  ${deltaNoteHtml}
  ${detailsFoldHtml}
 `;
}

function renderPvClippingBreakdown(data){
 const target=$('pvClippingTable');
 if(!target)return;
 const annual=data?.annual_flows;
 const months=data?.monthly_clipping;
 const note=$('pvClippingMethodNote');
 const coupling=data?.selected_config?.pv_battery_coupling||current?.result?.settings?.pv_battery_coupling||settings.pv_battery_coupling||'ac_equivalent';
 if(note)note.textContent=coupling==='dc_coupled'
  ?'DC-coupled approximation: battery charging may use PV surplus beyond the inverter AC output limit, subject to charge power and usable capacity. PV input is an AC-equivalent profile, not measured DC output; product-specific simultaneous-output limits are not represented, and actual recoverable energy may differ.'
  :'AC-equivalent mode: hourly modeled PV above the inverter AC limit is curtailed before household loads, battery charging, and export.';
 const heatmapNote=$('monthHourHeatmapNote');
 if(heatmapNote)heatmapNote.textContent=coupling==='dc_coupled'
  ?'Hourly averages by month in fixed UTC+1 standard time (civil DST labels shift during summer). PV clipping shows residual curtailment after modeled battery charging and inverter-limited AC output. DC-coupled recovery uses an AC-equivalent PV profile; manufacturer-specific simultaneous-output behavior is not represented.'
  :'Hourly averages by month in fixed UTC+1 standard time (civil DST labels shift during summer). PV clipping shows modeled PV curtailed at the inverter AC limit before battery charging.';
 if(!annual||!Array.isArray(months)||months.length!==12){
  const isRunning = typeof pendingBehaviorRequests !== 'undefined' && pendingBehaviorRequests.size > 0;
  if(isRunning){
   target.innerHTML='<p class="muted" role="status" aria-live="polite" aria-busy="true" style="display:inline-flex;align-items:center;gap:8px;"><span class="progress-spinner" aria-hidden="true"></span> Monthly inverter clipping detail is pending with the dispatch simulation.</p>';
  } else {
   target.innerHTML='<p class="muted">Monthly inverter clipping detail is pending with the dispatch simulation.</p>';
  }
  return;
 }
 const fmtKwh=v=>Number(v??0).toLocaleString('en-IE',{minimumFractionDigits:1,maximumFractionDigits:1});
 const monthNames=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
 const config=data.selected_config||{};
 const weatherCount=Number(data?.weather_count??data?.weather_years?.length??current?.result?.weather_count??current?.result?.weather_years?.length??0);

 // 1. Calculate KPI values
 const usablePvKwh=annual.pv_generation_kwh??annual.pv_delivered_kwh??0;
 let clipRangeStr='';
 const kwhRng=annual.flow_ranges?.clipped_kwh;
 const pctRng=annual.flow_ranges?.clipped_pct;
 const hasKwhRng=kwhRng?.p10!=null&&kwhRng?.p90!=null&&Number.isFinite(Number(kwhRng.p10))&&Number.isFinite(Number(kwhRng.p90));
 const hasPctRng=pctRng?.p10!=null&&pctRng?.p90!=null&&Number.isFinite(Number(pctRng.p10))&&Number.isFinite(Number(pctRng.p90));
 if(hasKwhRng&&hasPctRng){
  clipRangeStr=`<small class="muted">P10–P90: ${nf(Number(kwhRng.p10),0)}–${nf(Number(kwhRng.p90),0)} kWh · ${nf(Number(pctRng.p10),2)}%–${nf(Number(pctRng.p90),2)}%</small>`;
 }else if(hasKwhRng){
  clipRangeStr=`<small class="muted">P10–P90: ${nf(Number(kwhRng.p10),0)}–${nf(Number(kwhRng.p90),0)} kWh</small>`;
 }

 let maxPeakKw=0;
 let maxPeakMonth='';
 for(const r of months){
  const kw=Number(r.peak_curtailed_kw??0);
  if(kw>maxPeakKw){
   maxPeakKw=kw;
   maxPeakMonth=monthNames[Number(r.month)-1]||String(r.month);
  }
 }
 if(maxPeakKw<=0&&annual.peak_curtailed_kw){
  maxPeakKw=Number(annual.peak_curtailed_kw);
 }

 const isScheduled=config.routing_priority==='scheduled_hybrid';
 let emsBadge='';
 if(isScheduled){
  const switchH=String(config.routing_switch_hour??11).padStart(2,'0')+':00';
  const startM=Number(config.scheduled_start_month??current?.result?.settings?.scheduled_start_month??3);
  const endM=Number(config.scheduled_end_month??current?.result?.settings?.scheduled_end_month??9);
  const startName=monthNames[startM-1]||String(startM);
  const endName=monthNames[endM-1]||String(endM);
  const schedRange=startName===endName?startName:`${startName}–${endName}`;
  emsBadge=`AM Grid → PM Batt (switch ${switchH}) · ${schedRange}`;
 }else{
  emsBadge=!['battery_first','scheduled_hybrid','pv_only'].includes(config.routing_priority)&&config.routing_priority
   ?`${routingPriorityCompact(config.routing_priority)} (Inactive)`
   :'Self-Use 24/7 (Inactive)';
 }

 const kpisHtml=`<div class="pv-clip-kpis">
  <div class="pv-clip-kpi">
   <label>Modeled → Usable PV</label>
   <strong>${fmtKwh(annual.pv_input_kwh)} → ${fmtKwh(usablePvKwh)} kWh/yr</strong>
   <small>${weatherCount}-year arithmetic mean</small>
  </div>
  <div class="pv-clip-kpi">
   <label>Annual PV Curtailed</label>
   <strong>${fmtKwh(annual.clipped_kwh)} kWh/yr (${nf(annual.clipped_pct,2)}%)</strong>
   ${clipRangeStr}
  </div>
  <div class="pv-clip-kpi">
   <label>Clipping Incidence</label>
   <strong>${nf(annual.clipping_hours)} hrs/yr</strong>
   <small class="muted" title="Maximum curtailed power across historical weather years">Peak: ${nf(maxPeakKw,2)} kW${maxPeakMonth?` (${esc(maxPeakMonth)})`:''}</small>
  </div>
  <div class="pv-clip-kpi">
   <label>Active EMS Schedule</label>
   <strong class="pv-clip-kpi-badge"><span class="schedule-pill${isScheduled?' active':''}" style="white-space:normal;line-height:1.3;">${esc(emsBadge)}</span></strong>
  </div>
 </div>`;

 // 2. Render 12 month cards
 const maxInputKwh=Math.max(...months.map(r=>Number(r.pv_input_kwh??0)),1);
 const monthCellsHtml=months.map(row=>{
  const mName=monthNames[Number(row.month)-1]||String(row.month);
  const barHeightPct=Math.min(100,Math.max(0,(Number(row.pv_input_kwh??0)/maxInputKwh)*100));
  const capHeightPct=Math.min(100,Math.max(0,Number(row.clipped_pct??0)));
  const clipKwh=Number(row.clipped_kwh??0);
  const clipPct=Number(row.clipped_pct??0);
  const clipHrs=Number(row.clipping_hours??0);
  const isClipping=clipKwh>=0.5;

  const clipHtml=isClipping
   ?`<strong>${fmtKwh(clipKwh)} kWh</strong><small class="muted">${nf(clipPct,1)}% · ${nf(clipHrs,0)} hrs</small>`
   :`<strong><span class="muted" title="${esc('0 kWh')}" aria-label="0 kWh">—</span></strong><small class="muted" aria-hidden="true">&nbsp;</small>`;

  const hasP25=row.p25_peak_soc_pct!=null&&row.p25_peak_soc_pct!==''&&Number.isFinite(Number(row.p25_peak_soc_pct));
  let socHtml='<span class="muted" title="—">—</span>';
  if(hasP25){
   const p25Val=Number(row.p25_peak_soc_pct);
   const isLow=Boolean(row.scheduled_active&&p25Val<95);
   socHtml=`<span class="${isLow?'schedule-soc-low':''}" title="${esc(`P25 daily peak SoC across days & weather years: ${nf(p25Val,1)}%`)}">P25 SoC: ${nf(p25Val,0)}%</span>`;
  }

  return `<div class="pv-clip-month${row.scheduled_active?' scheduled':''}">
   <div class="pv-clip-month-label">
    <button type="button" class="pv-clip-month-label" data-schedule-month="${Number(row.month)}" aria-pressed="${Boolean(row.scheduled_active)}" title="${esc(`Toggle dispatch schedule for ${mName}`)}">
     <span>${esc(mName)}</span>${row.scheduled_active?'<span class="pv-clip-dot" aria-hidden="true"></span>':''}
    </button>
   </div>
   <div class="pv-clip-bar-track">
    <div class="pv-clip-bar" style="height:${barHeightPct.toFixed(1)}%;">
     <div class="pv-clip-cap" style="height:${capHeightPct.toFixed(1)}%;"></div>
    </div>
   </div>
   <div class="pv-clip-clipping">
    ${clipHtml}
   </div>
   <div class="pv-clip-soc">
    ${socHtml}
   </div>
  </div>`;
 }).join('');

 const monthsHtml=`<div class="pv-clip-strip-scroll"><div class="pv-clip-months">${monthCellsHtml}</div></div>`;

 // 3. Render collapsed table details
 const hasP25Monthly=months.some(r=>r.p25_peak_soc_pct!=null);
 const socHeader=hasP25Monthly?'P25 daily peak SoC':'Median daily peak SoC';
 const tableHeaders=['Month','Modeled PV input','Usable PV','Curtailed PV (%)','Clipping duration · Peak','Dispatch schedule',socHeader];
 const tableRows=months.map(row=>{
  const mName=monthNames[Number(row.month)-1]||String(row.month);
  let tSocHtml='—';
  if(row.p25_peak_soc_pct!=null&&Number.isFinite(Number(row.p25_peak_soc_pct))){
   const p25Val=Number(row.p25_peak_soc_pct);
   const isLow=Boolean(row.scheduled_active&&p25Val<95);
   const title=`P25 daily peak SoC across days & weather years: ${nf(p25Val,1)}%${row.median_peak_soc_pct!=null?` (median: ${nf(row.median_peak_soc_pct,1)}%)`:''}`;
   tSocHtml=`<span class="${isLow?'schedule-soc-low':''}" title="${esc(title)}">${nf(p25Val,1)}%</span>`;
  }else if(!hasP25Monthly&&row.median_peak_soc_pct!=null&&Number.isFinite(Number(row.median_peak_soc_pct))){
   const medVal=Number(row.median_peak_soc_pct);
   tSocHtml=`<span title="${esc(`Median daily peak SoC: ${nf(medVal,1)}%`)}">${nf(medVal,1)}%</span>`;
  }
  return [
   esc(mName),
   `${fmtKwh(row.pv_input_kwh)} kWh`,
   `${fmtKwh(row.pv_delivered_kwh)} kWh`,
   `${fmtKwh(row.clipped_kwh)} kWh (${nf(row.clipped_pct,2)}%)`,
   `${nf(row.clipping_hours)} hrs · ${nf(row.peak_curtailed_kw,2)} kW peak`,
   row.scheduled_active?'Active':'—',
   tSocHtml
  ];
 });

 const detailsHtml=`<details class="pv-clip-details">
  <summary>Monthly clipping, usable PV &amp; SoC breakdown table (${weatherCount}-year mean)</summary>
  <div class="table-wrap">
   <table>
    <thead><tr>${tableHeaders.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${tableRows.map(r=>`<tr>${r.map((c,i)=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
   </table>
  </div>
 </details>`;

 target.innerHTML=`${kpisHtml}${monthsHtml}${detailsHtml}`;
}

let behaviorMatrixCache=null;
let behaviorMatrixKey='';
let activeSeason='summer';
let activeBalanceView='annual';
let activeHeatmapMetric='import';
let behaviorToken=0;
const pendingBehaviorRequests=new Map();

function renderBehaviorPending(isQueued = false){
 const isRunning = isQueued || (Boolean(pendingBehaviorRequests && pendingBehaviorRequests.size > 0));
 if(typeof resetSwitchHourOptimizer==='function')resetSwitchHourOptimizer(isRunning ? 'Waiting for dispatch simulation…' : 'Waiting for dispatch simulation.');
 if(typeof document!=='undefined')document.querySelectorAll?.('#pvClippingTable [data-schedule-month]')?.forEach?.(b=>b.disabled=true);
 const titleMetaEl=$('annualFlowMeta');
 if(titleMetaEl) titleMetaEl.innerHTML='';
 const dayChart=$('typicalDayChart');
 if(dayChart){
  dayChart.innerHTML=isRunning
   ? '<div class="chart-pending" role="status" aria-busy="true" style="display:flex;align-items:center;justify-content:center;gap:8px;height:240px;color:#62756e;"><span class="progress-spinner" aria-hidden="true"></span><p style="margin:0;">Simulation pending · Dispatch chart will update when calculation completes.</p></div>'
   : '<div class="chart-pending" role="status" style="display:flex;align-items:center;justify-content:center;height:240px;color:#62756e;"><p style="margin:0;">Simulation pending · Dispatch chart will update when calculation completes.</p></div>';
 }
 const heatmap=$('monthHourHeatmap');
 if(heatmap){
  heatmap.innerHTML=isRunning
   ? '<div class="chart-pending" role="status" aria-busy="true" style="display:flex;align-items:center;justify-content:center;gap:8px;height:240px;color:#62756e;"><span class="progress-spinner" aria-hidden="true"></span><p style="margin:0;">Simulation pending · Heatmap will update when calculation completes.</p></div>'
   : '<div class="chart-pending" role="status" style="display:flex;align-items:center;justify-content:center;height:240px;color:#62756e;"><p style="margin:0;">Simulation pending · Heatmap will update when calculation completes.</p></div>';
 }
}

function renderBehaviorError(message){
 const monthly=$('monthlyBalanceVisual');
 if(monthly)monthly.innerHTML=`<p class="bad" role="alert">Could not load monthly balance: ${esc(message)}</p>`;
 if(typeof resetSwitchHourOptimizer==='function')resetSwitchHourOptimizer('Schedule comparison unavailable: '+message);
 if(typeof document!=='undefined')document.querySelectorAll?.('#pvClippingTable [data-schedule-month]')?.forEach?.(b=>b.disabled=true);
 const clippingTable=$('pvClippingTable');
 if(clippingTable){
  clippingTable.innerHTML=`<p class="bad" role="alert">Could not load monthly clipping: ${esc(message)}</p>`;
 }
 const titleMetaEl=$('annualFlowMeta');
 if(titleMetaEl) titleMetaEl.innerHTML='';
 const flowEl=$('annualEnergyFlowVisual');
 if(flowEl){
  flowEl.innerHTML=`<div class="annual-flow-error bad" role="alert" style="padding:16px;border:1px solid #c0392b;border-radius:6px;background:#fde8e8;"><p>Could not load annual energy flow simulation: ${esc(message)}</p><button type="button" class="btn btn-sm" onclick="refreshBehaviorViews()">Retry</button></div>`;
 }
 const dayChart=$('typicalDayChart');
 if(dayChart){
  dayChart.innerHTML=`<div class="chart-error bad" role="alert" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:240px;gap:8px;padding:16px;border:1px solid #c0392b;border-radius:6px;background:#fde8e8;"><p>Could not load typical day dispatch: ${esc(message)}</p><button type="button" class="btn btn-sm" onclick="refreshBehaviorViews()">Retry</button></div>`;
 }
 const heatmap=$('monthHourHeatmap');
 if(heatmap){
  heatmap.innerHTML=`<div class="chart-error bad" role="alert" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:240px;gap:8px;padding:16px;border:1px solid #c0392b;border-radius:6px;background:#fde8e8;"><p>Could not load dispatch heatmap: ${esc(message)}</p><button type="button" class="btn btn-sm" onclick="refreshBehaviorViews()">Retry</button></div>`;
 }
}

function renderHeatPumpReport(result = current?.result){
 const panel = $('heatPumpReportPanel');
 if(!panel) return;
 const activeHp = Boolean(result && result.settings && result.settings.planning_heat_pump && result.settings.load_scenario !== 'baseline');
 if(!activeHp){
  panel.hidden = true;
  panel.innerHTML = '';
  return;
 }
 panel.hidden = false;

 if(!result.heat_pump){
  panel.innerHTML = '<div class="hp-pending-card"><p>Heat pump annual simulation telemetry pending or unavailable for this scenario.</p></div>';
  return;
 }

 const hp = result.heat_pump;
 const meta = hp.metadata || {};
 const env = hp.envelopes || {};
 const gasFin = hp.gas_finance || {};
 const finance = result.finance || {};
 const s = result.settings || {};

 const isComparable = finance.service_comparable !== false && result.service_comparable !== false;

 function sortedMedian(values){
  const mid=Math.floor(values.length/2);
  return values.length%2?values[mid]:(values[mid-1]+values[mid])/2;
 }

 function metric(key){
  if(env[key]) return env[key];
  if(Array.isArray(hp.by_weather) && hp.by_weather.length){
   const vals = hp.by_weather.map(w => w[key]).filter(v => v != null && Number.isFinite(v));
   if(vals.length){
    const sorted = [...vals].sort((a,b)=>a-b);
    const mid = sortedMedian(sorted);
    return { min: sorted[0], median: mid, mean: sorted.reduce((a,b)=>a+b,0)/sorted.length, max: sorted.at(-1) };
   }
  }
  return { min: null, median: null, mean: null, max: null };
 }

 // Electricity metrics
 const spaceCompElec = metric('annual_space_compressor_electricity_kwh');
 const dhwCompElec = metric('annual_dhw_compressor_electricity_kwh');
 const spaceBackupElec = metric('annual_space_backup_electricity_kwh');
 const dhwBackupElec = metric('annual_dhw_backup_electricity_kwh');

 // Form backup SUM per weather then compute envelope, don't sum min/median/max from separate weather outcomes
 let backupTotalElec = null;
 if(Array.isArray(hp.by_weather) && hp.by_weather.length){
  const backupVals = hp.by_weather.map(w => {
   const sb = w.annual_space_backup_electricity_kwh;
   const db = w.annual_dhw_backup_electricity_kwh ?? w.dhw_backup_electricity_kwh;
   if(sb == null && db == null) return null;
   return (sb ?? 0) + (db ?? 0);
  }).filter(v => v != null && Number.isFinite(v));
  if(backupVals.length){
   const sorted = [...backupVals].sort((a,b)=>a-b);
   backupTotalElec = {
    min: sorted[0],
    median: sortedMedian(sorted),
    mean: sorted.reduce((a,b)=>a+b,0)/sorted.length,
    max: sorted.at(-1)
   };
  }
 }
 if(!backupTotalElec){
  backupTotalElec = {
   min: (spaceBackupElec.min != null && dhwBackupElec.min != null) ? spaceBackupElec.min + dhwBackupElec.min : (spaceBackupElec.min ?? dhwBackupElec.min ?? null),
   median: (spaceBackupElec.median != null && dhwBackupElec.median != null) ? spaceBackupElec.median + dhwBackupElec.median : (spaceBackupElec.median ?? dhwBackupElec.median ?? null),
   mean: (spaceBackupElec.mean != null && dhwBackupElec.mean != null) ? spaceBackupElec.mean + dhwBackupElec.mean : (spaceBackupElec.mean ?? dhwBackupElec.mean ?? null),
   max: (spaceBackupElec.max != null && dhwBackupElec.max != null) ? spaceBackupElec.max + dhwBackupElec.max : (spaceBackupElec.max ?? dhwBackupElec.max ?? null)
  };
 }

 const extPumpElec = metric('annual_external_pump_electricity_kwh');
 const standbyElec = metric('annual_standby_electricity_kwh');
 const auxElec = metric('annual_auxiliary_electricity_kwh');
 const totalHpElec = metric('annual_total_electricity_kwh');

 // Heat metrics
 const spaceReqHeat = metric('annual_requested_space_heat_kwh');
 const spaceDelivHeat = metric('annual_delivered_space_heat_kwh');
 const spaceUnmetHeat = metric('annual_unmet_space_heat_kwh');
 const dhwReqHeat = metric('annual_requested_draw_heat_kwh');
 const dhwDelivHeat = metric('annual_delivered_draw_heat_kwh');
 const dhwUnmetHeat = metric('annual_unmet_draw_heat_kwh');
 const tankLoss = metric('annual_tank_losses_kwh');

 // Total delivery card: calculate per weather SUM then median
 let totalDelivHeatMedian = null;
 if(Array.isArray(hp.by_weather) && hp.by_weather.length){
  const delivVals = hp.by_weather.map(w => {
   const s = w.annual_delivered_space_heat_kwh;
   const d = w.annual_delivered_draw_heat_kwh;
   if(s == null && d == null) return null;
   return (s ?? 0) + (d ?? 0);
  }).filter(v => v != null && Number.isFinite(v));
  if(delivVals.length){
   const sorted = [...delivVals].sort((a,b)=>a-b);
   totalDelivHeatMedian = sortedMedian(sorted);
  }
 }
 if(totalDelivHeatMedian == null && (spaceDelivHeat.median != null || dhwDelivHeat.median != null)){
  totalDelivHeatMedian = (spaceDelivHeat.median ?? 0) + (dhwDelivHeat.median ?? 0);
 }

 const initialStorage = metric('initial_storage_kwh').median ?? hp.by_weather?.[0]?.initial_storage_kwh ?? null;
 const finalStorage = metric('final_storage_kwh').median ?? hp.by_weather?.[0]?.final_storage_kwh ?? null;
 const storageDelta = metric('storage_delta_kwh').median ?? hp.by_weather?.[0]?.storage_delta_kwh ?? (initialStorage != null && finalStorage != null ? finalStorage - initialStorage : null);

 // Seasonal performance metrics
 const scopSpace = metric('seasonal_scop_space');
 const spfDelivered = metric('seasonal_performance_factor_delivered_heat');
 const spfStorageAdj = metric('seasonal_performance_factor_storage_adjusted');
 const copCompressor = metric('seasonal_compressor_cop');
 const copSpace = metric('seasonal_space_cop');
 const copDhw = metric('seasonal_dhw_cop_heat_to_tank');

 // Gas reference derivation from ACTUAL metadata and settings (no invented fallbacks)
 const alloc = meta.reference_gas_allocation || {};
 const totalGasSmc = alloc.total_reference_gas_smc ?? meta.total_reference_gas_smc ?? null;
 const heatingSmc = alloc.heating_reference_smc ?? null;
 const dhwSmc = alloc.dhw_reference_smc ?? null;
 const cookingSmc = alloc.cooking_reference_smc ?? null;
 const gasPeriod = alloc.gas_reference_period ?? meta.gas_reference_period ?? 'Explicit profile reference period';
 const boilerEff = meta.boiler_efficiency ?? s.boiler_efficiency ?? (typeof settings !== 'undefined' ? settings?.boiler_efficiency : null) ?? null;
 const boilerStatus = meta.boiler_efficiency_status || (boilerEff != null ? (s.boiler_efficiency != null || (typeof settings !== 'undefined' && settings?.boiler_efficiency != null) ? `Settings boiler efficiency (${nf(boilerEff * 100, 0)}%)` : `Seasonal boiler efficiency (${nf(boilerEff * 100, 0)}%)`) : 'Not available');
 const indoorSetpoint = s.hp_indoor_temp ?? null;
 const nominalKw = meta.provisional_nominal_capacity_kw ?? s.hp_nominal_kw ?? null;
 const radiatorKw = meta.provisional_radiator_dt50_kw ?? s.hp_radiator_dt50_kw ?? null;
 const equipModel = meta.equipment_model || 'Generic Air-to-Water Monobloc (provisional synthetic fallback)';
 const equipSource = meta.equipment_source || 'PROVISIONAL synthetic fallback model';

 // Gas status
 let gasStatusTitle = '';
 let gasStatusDesc = '';
 if(s.hp_disconnect_gas){
  gasStatusTitle = 'Gas service disconnected';
  gasStatusDesc = 'All-electric home · gas meter removed · standing charges eliminated';
 } else if(s.hp_electric_cooking){
  gasStatusTitle = 'Gas connection retained (0 Smc)';
  gasStatusDesc = `Meter active · modeled gas bill: ${gasFin.actual_remaining_gas_cost != null ? fmt(gasFin.actual_remaining_gas_cost, 'eur') + '/yr fixed charges' : 'fixed charges not available'} (no gas backup modeled)`;
 } else {
  gasStatusTitle = 'Gas retained for cooking';
  gasStatusDesc = `Meter active · cooking gas modeled · remaining bill: ${gasFin.actual_remaining_gas_cost != null ? fmt(gasFin.actual_remaining_gas_cost, 'eur') + '/yr' : 'not available'}`;
 }

 let spaceUnmetRuns = 0;
 let dhwUnmetRuns = 0;
 const totalWeatherRuns = Array.isArray(hp.by_weather) ? hp.by_weather.length : 0;
 if(Array.isArray(hp.by_weather)){
  for(const w of hp.by_weather){
   if((w.annual_unmet_space_heat_kwh ?? 0) > 1e-4) spaceUnmetRuns++;
   if((w.annual_unmet_draw_heat_kwh ?? 0) > 1e-4) dhwUnmetRuns++;
  }
 }
 const spaceUnmetMax = spaceUnmetHeat.max ?? 0;
 const dhwUnmetMax = dhwUnmetHeat.max ?? 0;
 const finAllowance = gasFin.unserved_service_allowance || gasFin.allowance;
 const finAllowanceText = (finAllowance && (finAllowance.max > 0 || finAllowance.mean > 0))
  ? ` Financial shortfall allowance: mean ${fmt(finAllowance.mean, 'eur2')}/yr (weather range ${fmt(finAllowance.min, 'eur2')} to ${fmt(finAllowance.max, 'eur2')}/yr) deducted from whole-package benefits.`
  : '';

 let unmetWarningHtml = '';
 if(!isComparable){
  unmetWarningHtml = `
   <div class="hp-unmet-warning" role="alert">
    <div class="hp-warning-icon">⚠️</div>
    <div>
     <strong>Unmet Thermal Demand Warning:</strong> Heat pump output could not fully satisfy modeled space heating or hot water demand across the simulated data envelope (shortfalls may arise from high coincident draws, constrained reheat schedules, emitter limits, or extreme weather).
     Maximum annual unmet demand reaches ${nf(spaceUnmetMax, 2)} kWh space heating (${spaceUnmetRuns} of ${totalWeatherRuns} weather runs affected) and ${nf(dhwUnmetMax, 2)} kWh DHW (${dhwUnmetRuns} of ${totalWeatherRuns} weather runs affected) — median annual unmet demand is ${nf(spaceUnmetHeat.median ?? 0, 2)} kWh space and ${nf(dhwUnmetHeat.median ?? 0, 2)} kWh DHW because most weather runs experience zero or near-zero shortfall.
     Because service delivery does not match the baseline, avoided gas fuel credit is withheld and full economic equivalence is not validated (no room comfort validation or full hygiene compliance is claimed).${finAllowanceText} Equipment sizing, radiator area, or electric backup capacity should be reviewed before treating financial savings as definitive.
    </div>
   </div>
  `;
 }

 panel.innerHTML = `
  <div class="section-head">
   <div>
    <p class="eyebrow">HEAT PUMP PERFORMANCE &amp; THERMAL BALANCE</p>
    <h2>Electrified heating and domestic hot water simulation</h2>
   </div>
  </div>
  <p class="muted">Hourly physical simulation of an inverter air-to-water monobloc heat pump across ${totalWeatherRuns ? totalWeatherRuns + " historical weather years" : "historical weather ensemble"}. Gas-calibrated heat/service approximation, domestic hot water delivery, cylinder thermal state, and auxiliary electricity (indoor-temperature changes and recovery are not simulated).</p>
  
  ${unmetWarningHtml}

  <div class="cards">
   ${dayMetric('Achieved space SCOP (median)', scopSpace.median, '', `Compressor + backup (excl. aux) · range ${range(scopSpace, 2)}`)}
   ${dayMetric('Combined delivered SPF (median)', spfDelivered.median, '', `Useful delivered heat / all electricity · range ${range(spfDelivered, 2)}`)}
   ${dayMetric('Total added electricity (median)', totalHpElec.median, 'kWh/yr', `Weather range ${range(totalHpElec)} kWh`)}
   ${dayMetric('Delivered thermal service (median)', totalDelivHeatMedian, 'kWh th/yr', `Space ${nf(spaceDelivHeat.median, 0)} kWh + DHW ${nf(dhwDelivHeat.median, 0)} kWh (marginal medians)`)}
   ${dayMetric('Gas connection status', gasStatusTitle, '', gasStatusDesc)}
  </div>

  <div class="hp-balance-grid">
   <div class="card hp-balance-card">
    <h3>Electricity consumption breakdown</h3>
    <table class="hp-balance-table">
     <thead>
      <tr>
       <th>Component</th>
       <th class="num">Weather mean</th>
       <th class="num">Weather range</th>
      </tr>
     </thead>
     <tbody>
      <tr>
       <td>Space heating compressor</td>
       <td class="num">${nf(spaceCompElec.mean, 0)} kWh</td>
       <td class="num">${range(spaceCompElec)} kWh</td>
      </tr>
      <tr>
       <td>DHW heating compressor</td>
       <td class="num">${nf(dhwCompElec.mean, 0)} kWh</td>
       <td class="num">${range(dhwCompElec)} kWh</td>
      </tr>
      <tr>
       <td>Resistance backup (space + DHW)</td>
       <td class="num">${nf(backupTotalElec.mean, 1)} kWh</td>
       <td class="num">${range(backupTotalElec)} kWh</td>
      </tr>
      <tr>
       <td>Auxiliaries (external pump + standby)</td>
       <td class="num">${nf(auxElec.mean, 0)} kWh</td>
       <td class="num">${range(auxElec)} kWh</td>
      </tr>
      <tr class="total-row">
       <td>Total added electricity</td>
       <td class="num">${nf(totalHpElec.mean, 0)} kWh</td>
       <td class="num">${range(totalHpElec)} kWh</td>
      </tr>
     </tbody>
    </table>
    <small class="muted" style="display:block;margin-top:6px;">Space compressor, DHW compressor, backup resistance, and auxiliary pump/standby weather means sum to total electricity input (rounding may leave up to 1 kWh; range endpoints reflect individual weather distributions and are not additive).</small>
   </div>

   <div class="card hp-balance-card">
    <h3>Heat delivery &amp; thermal balance</h3>
    <table class="hp-balance-table">
     <thead>
      <tr>
       <th>Thermal service</th>
       <th class="num">Requested</th>
       <th class="num">Delivered</th>
       <th class="num">Unmet</th>
      </tr>
     </thead>
     <tbody>
      <tr>
       <td>Space heating</td>
       <td class="num">${nf(spaceReqHeat.median, 0)} kWh</td>
       <td class="num">${nf(spaceDelivHeat.median, 0)} kWh</td>
       <td class="num ${(spaceUnmetHeat.median > 0 || spaceUnmetMax > 0.001) ? 'bad' : ''}">${nf(spaceUnmetHeat.median ?? 0, 2)} kWh${spaceUnmetMax > 0.001 ? `<br><small class="muted">Max: ${nf(spaceUnmetMax, 2)} kWh (${spaceUnmetRuns}/${totalWeatherRuns} runs)</small>` : ''}</td>
      </tr>
      <tr>
       <td>DHW water draw service</td>
       <td class="num">${nf(dhwReqHeat.median, 0)} kWh</td>
       <td class="num">${nf(dhwDelivHeat.median, 0)} kWh</td>
       <td class="num ${(dhwUnmetHeat.median > 0 || dhwUnmetMax > 0.001) ? 'bad' : ''}">${nf(dhwUnmetHeat.median ?? 0, 2)} kWh${dhwUnmetMax > 0.001 ? `<br><small class="muted">Max: ${nf(dhwUnmetMax, 2)} kWh (${dhwUnmetRuns}/${totalWeatherRuns} runs)</small>` : ''}</td>
      </tr>
      <tr>
       <td>Cylinder standing losses</td>
       <td class="num">—</td>
       <td class="num">${nf(tankLoss.median, 0)} kWh</td>
       <td class="num">—</td>
      </tr>
      <tr class="total-row">
       <td>Cylinder thermal storage delta</td>
       <td class="num" colspan="3">${initialStorage != null && finalStorage != null ? `Start ${nf(initialStorage, 2)} kWh → End ${nf(finalStorage, 2)} kWh (delta ${nf(storageDelta, 2)} kWh)` : (storageDelta != null ? `Delta ${nf(storageDelta, 2)} kWh` : '—')}</td>
      </tr>
     </tbody>
    </table>
    <small class="muted" style="display:block;margin-top:6px;">Storage delta discloses initial vs final cylinder thermal state for seasonal ratios. Useful delivered SPF excludes unserved lukewarm water withdrawals.</small>
   </div>
  </div>

  <div class="card" style="margin:14px 0;">
   <h3>Heat-pump unit COP (map boundary)</h3>
   <p class="muted">Heat-pump unit coefficient of performance (COP) at the map boundary (includes only effects declared by the selected map; additional external pumps/standby/backup counted separately; fallback is provisional not manufacturer), explicitly distinguished from seasonal performance factor (SPF) and overall system boundary which include external system pumps, standby, and electric resistance backup. (Manufacturer heat-pump heating maps do not transfer air-conditioning or summer cooling ratings).</p>
   <div class="cards hp-cop-cards">
    ${dayMetric('Overall compressor COP', copCompressor.median, '', copCompressor.median != null ? `Combined space + DHW compressor · range ${range(copCompressor, 2)}` : 'Not available')}
    ${dayMetric('Space compressor COP', copSpace.median, '', copSpace.median != null ? `Space heating mode at equipment boundary · range ${range(copSpace, 2)}` : 'Not available')}
    ${dayMetric('DHW compressor COP', copDhw.median, '', copDhw.median != null ? `Heat supplied to cylinder · range ${range(copDhw, 2)}` : 'Not available')}
    ${dayMetric('Storage-adjusted SPF', spfStorageAdj.median, '', spfStorageAdj.median != null ? `Useful heat + storage delta / electricity · range ${range(spfStorageAdj, 2)}` : 'Not available (cylinder thermal storage delta not evaluated or untracked)')}
   </div>
  </div>

  <div class="hp-assumptions-summary">
   <strong>Key modeling assumptions &amp; calibration boundaries:</strong>
   <p style="margin:4px 0 2px;">
    • <strong>Gas baseline calibration:</strong> ${totalGasSmc != null ? `Supplied baseline of ${nf(totalGasSmc, 0)} Smc (${esc(gasPeriod)}) · allocated to ${heatingSmc != null ? nf(heatingSmc, 0) + ' Smc space heating' : '—'}, ${dhwSmc != null ? nf(dhwSmc, 0) + ' Smc DHW' : '—'}, and ${cookingSmc != null ? nf(cookingSmc, 0) + ' Smc cooking' : '—'}.` : `Reference gas billing allocation not available in simulation telemetry (${esc(gasPeriod)}).`}<br>
    • <strong>Existing boiler efficiency:</strong> ${boilerEff != null ? `${nf(boilerEff * 100, 0)}% seasonal efficiency (${esc(boilerStatus)})` : 'Not available'}.<br>
    • <strong>Temperature control:</strong> ${indoorSetpoint != null ? `${nf(indoorSetpoint, 1)}°C indoor thermostat setpoint` : 'Setpoint not specified'} (conceptually distinct from outdoor heating balance threshold) · ${s.hp_schedule_mode === 'scheduled' ? 'Scheduled operating windows' : 'Continuous (24h available)'}.<br>
    • <strong>Equipment &amp; emitters:</strong> ${esc(equipModel)} (${nominalKw != null ? `${nf(nominalKw, 1)} kW nominal rating` : 'nominal rating not available'}) · ${radiatorKw != null ? `${nf(radiatorKw, 1)} kW EN 442 radiators at ΔT 50 (lower water emitters bound)` : 'radiator capacity not available'}.<br>
    • <strong>Simulation notice:</strong> All hourly thermal and electrical results are SIMULATED engineering estimates across a ${hp.metadata?.climatology_period || (getWeatherEnsembleMeta().rangeLabel ? getWeatherEnsembleMeta().rangeLabel + " climatological proxy" : "historical climatological proxy")}, not onsite physical measurements or validated thermal comfort.
   </p>
  </div>

  <details class="control-details hp-audit-fold">
   <summary>Secondary technical &amp; audit assumptions</summary>
   <div class="control-details-body">
    <p><strong>Climatology &amp; weather proxy:</strong> ${hp.metadata?.climatology_period ? esc(hp.metadata.climatology_period) : (getWeatherEnsembleMeta().rangeLabel ? getWeatherEnsembleMeta().rangeLabel + " PVGIS historical weather ensemble" : "Historical PVGIS weather ensemble")}. Reference heating and hot water demand are calibrated against the supplied natural gas baseline (${esc(gasPeriod)}); hourly coincident ambient temperatures use historical analogues.</p>
    <p><strong>Building dynamics (indoor-temperature changes and recovery are not simulated):</strong> No dynamic building RC network or dwelling thermal mass is inferred; room temperature decay during setbacks and morning recovery surge peaks are not modeled. Radiator cooldown does not run the compressor. Unmet thermal demands are explicitly reported.</p>
    <p><strong>Hot water cylinder model:</strong> ${s.hp_tank_litres != null ? nf(s.hp_tank_litres, 0) : '200'} L storage cylinder with ${s.hp_tank_target_temp != null ? nf(s.hp_tank_target_temp, 0) : '50'}°C target temperature and standing loss rate of ${s.hp_tank_loss_w_k != null ? nf(s.hp_tank_loss_w_k, 1) : '1.0'} W/K. Useful delivered SPF excludes unserved lukewarm water withdrawals.</p>
    <p><strong>Manufacturer &amp; radiator citations:</strong></p>
    <p>Vaillant aroTHERM plus VWL EN 14511 / EN 14825 test data (<a href="https://www.vaillant.com/en/products/arothermplus/" target="_blank" rel="noopener">Vaillant aroTHERM plus specifications</a>).</p>
    <p>Stelrad compact radiator EN 442 catalogue data (<a href="https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf" target="_blank" rel="noopener">Stelrad correction factor documentation</a>).</p>
    <p class="muted">Default equipment model is a provisional synthetic fallback scaled by nominal capacity (not a selected manufacturer unit). Literature examples illustrate inverter compressor modulation and emitter derating; performance tables often mix part-load and maximum capacity ratings.</p>
   </div>
  </details>
 `;
}

let acHeatingComparisonOutdated = false;

function invalidateAcHeatingComparison(){
 acHeatingComparisonOutdated = true;
 const panel = typeof document !== 'undefined' ? (document.getElementById('acHeatingComparisonPanel') || document.getElementById('acHeatingComparisonArticle')) : null;
 if(!panel || panel.hidden) return;
 panel.classList.add('stale-overlay');
 const article = typeof document !== 'undefined' ? document.getElementById('acHeatingComparisonArticle') : null;
 if(article) article.classList.add('stale-overlay');
 const notice = panel.querySelector('.stale-comparison-notice');
 if(notice){
  notice.hidden = false;
  notice.style.display = '';
 } else {
  const div = typeof document !== 'undefined' && typeof document.createElement === 'function' ? document.createElement('div') : null;
  if(div){
   div.className = 'stale-comparison-notice';
   div.setAttribute('role', 'status');
   div.style.cssText = 'margin-bottom:12px;padding:8px 12px;background:#fff8e9;border:1px solid #e6d49f;border-radius:6px;font-size:12px;color:#705018;';
   div.textContent = '⚠️ Inputs changed or recalculation pending · AC heating comparison reflects previously accepted result';
   const head = panel.querySelector('.section-head');
   if(head && head.nextSibling){
    panel.insertBefore(div, head.nextSibling);
   } else {
    panel.prepend(div);
   }
  }
 }
}

const markAcHeatingComparisonOutdated = invalidateAcHeatingComparison;

function isAcHeatingComparisonMatching(res){
 if(!res || !res.settings) return false;
 if(typeof loanPending !== 'undefined' && loanPending) return false;
 const s = typeof settings !== 'undefined' ? settings : null;
 if(typeof shouldBlockAcDemandPctRecompute === 'function' ? shouldBlockAcDemandPctRecompute(s) : (typeof hasInvalidAcDemandPctDraft === 'function' && hasInvalidAcDemandPctDraft())) return false;
 if(typeof shouldBlockAcScheduleRecompute === 'function' ? shouldBlockAcScheduleRecompute(s) : (typeof hasInvalidAcScheduleDraft === 'function' && hasInvalidAcScheduleDraft())) return false;
 if(s){
  if(s.load_scenario !== res.settings.load_scenario) return false;
  if(Boolean(s.planning_heat_pump) !== Boolean(res.settings.planning_heat_pump)) return false;
  if(s.ac_heating_additional_demand_pct !== res.settings.ac_heating_additional_demand_pct) return false;
  if(s.heating_schedule_mode !== res.settings.heating_schedule_mode) return false;
  if(s.timezone_mode !== res.settings.timezone_mode) return false;
  if(s.heating_schedule_mode !== 'continuous' && typeof settingValuesEqual === 'function' && !settingValuesEqual(s.ac_heating_windows, res.settings.ac_heating_windows)) return false;
  if(typeof acceptedInputFingerprint !== 'undefined' && acceptedInputFingerprint !== null && typeof inputFingerprint === 'function'){
   if(acceptedInputFingerprint !== inputFingerprint(s, typeof quote !== 'undefined' ? quote : {line_items:[]})) return false;
  }
 }
 return true;
}

function acceptAcHeatingComparison(result = current?.result){
 const r = (result && result.result) ? result.result : (result || current?.result);
 if(!r || !isAcHeatingComparisonMatching(r)) return false;
 if(typeof current !== 'undefined'){
  if(result && result.result){
   current = result;
  } else if(result && current && current.result !== r){
   current.result = r;
  }
 }
 acHeatingComparisonOutdated = false;
 const panel = typeof document !== 'undefined' ? (document.getElementById('acHeatingComparisonPanel') || document.getElementById('acHeatingComparisonArticle')) : null;
 if(panel){
  panel.classList.remove('stale-overlay');
  const notice = panel.querySelector('.stale-comparison-notice');
  if(notice){
   notice.hidden = true;
   notice.style.display = 'none';
  }
 }
 const article = typeof document !== 'undefined' ? document.getElementById('acHeatingComparisonArticle') : null;
 if(article) article.classList.remove('stale-overlay');
 return true;
}

function isAcHeatingComparisonOutdated(res){
 if(acHeatingComparisonOutdated) return true;
 if(typeof current !== 'undefined' && current && current.result && res && res !== current.result) return true;
 return !isAcHeatingComparisonMatching(res);
}

function renderAcHeatingComparison(result = current?.result){
 const panel = typeof document !== 'undefined' ? (document.getElementById('acHeatingComparisonPanel') || document.getElementById('acHeatingComparisonArticle')) : null;
 if(!panel) return;
 const article = typeof document !== 'undefined' ? document.getElementById('acHeatingComparisonArticle') : null;
 const drawerSummary = typeof document !== 'undefined' ? document.getElementById('hvacLoadSummary') : null;
 if(drawerSummary) drawerSummary.textContent = 'Electrified HVAC load model';

 const r = result || current?.result;
 const activeAc = Boolean(
  r &&
  r.settings &&
  r.settings.load_scenario !== 'baseline' &&
  !r.settings.planning_heat_pump
 );

 if(!activeAc){
  panel.hidden = true;
  if(article) article.hidden = true;
  panel.innerHTML = '';
  return;
 }

 const enduses = r.enduses;
 const requiredKeys = [
  'ac_heating_baseline_electricity_kwh',
  'ac_heating_requested_electricity_kwh',
  'ac_heating_extra_electricity_kwh',
  'ac_heating_baseline_useful_kwh',
  'ac_heating_requested_useful_kwh',
  'ac_heating_additional_demand_pct',
  'ac_heating_scop'
 ];
 const hasRequired = Boolean(
  enduses &&
  typeof enduses === 'object' &&
  requiredKeys.every(k => enduses[k] !== undefined && enduses[k] !== null && enduses[k] !== '' && Number.isFinite(Number(enduses[k])))
 );

 if(!hasRequired){
  panel.hidden = false;
  if(article) article.hidden = false;
  panel.innerHTML = `
   <div class="section-head">
    <div>
     <p class="eyebrow">ANNUAL AC HEATING DEMAND</p>
     <h2>AC heating demand · counterfactual vs requested</h2>
    </div>
   </div>
   <div class="ac-heating-unavailable" role="status">
    <p class="muted">AC heating comparison data is unavailable for this result payload.</p>
   </div>
  `;
  return;
 }

 panel.hidden = false;
 if(article) article.hidden = false;

 const isOutdated = isAcHeatingComparisonOutdated(r);
 if(isOutdated){
  panel.classList.add('stale-overlay');
  if(article) article.classList.add('stale-overlay');
 } else {
  panel.classList.remove('stale-overlay');
  if(article) article.classList.remove('stale-overlay');
  acHeatingComparisonOutdated = false;
 }

 const baseElec = Number(enduses.ac_heating_baseline_electricity_kwh);
 const reqElec = Number(enduses.ac_heating_requested_electricity_kwh);
 const extraElec = Number(enduses.ac_heating_extra_electricity_kwh);
 const baseUseful = Number(enduses.ac_heating_baseline_useful_kwh);
 const reqUseful = Number(enduses.ac_heating_requested_useful_kwh);
 const pct = Number(enduses.ac_heating_additional_demand_pct);
 const scop = Number(enduses.ac_heating_scop);

 const pctStr = Number.isInteger(pct) ? String(pct) : String(Number(pct.toFixed(4)).toString());

 const diag = enduses.ac_heating_allocation_diagnostics || {};
 const nominalDailyHours = (diag.nominal_daily_union_duration_hours != null && Number.isFinite(Number(diag.nominal_daily_union_duration_hours))) ? Number(diag.nominal_daily_union_duration_hours) : null;
 const realizations = Array.isArray(diag.realizations) ? diag.realizations : [];

 let eligHoursStr = '—';
 if(realizations.length > 0){
  const eligs = realizations.map(w => w.eligible_schedule_duration_hours).filter(v => v != null && Number.isFinite(v));
  if(eligs.length > 0){
   const minElig = Math.min(...eligs);
   const maxElig = Math.max(...eligs);
   eligHoursStr = Math.abs(maxElig - minElig) < 1e-4 ? `${nf(minElig, 0)} h/year` : `${nf(minElig, 0)} – ${nf(maxElig, 0)} h/year`;
  }
 }

 function resolveDiagMax(key){
  if(diag && diag[key] != null && Number.isFinite(Number(diag[key]))){
   return Number(diag[key]);
  }
  if(realizations.length > 0){
   const validVals = realizations
    .map(w => w && w[key])
    .filter(v => v != null && Number.isFinite(Number(v)))
    .map(Number);
   if(validVals.length > 0){
    return Math.max(...validVals);
   }
  }
  return null;
 }

 const maxIntAvgKw = resolveDiagMax('max_modeled_interval_average_electrical_demand_kw');
 const maxReqElecKw = resolveDiagMax('max_modeled_permitted_duration_electrical_requirement_kw');
 const maxReqThermalKw = resolveDiagMax('max_modeled_permitted_duration_useful_heat_requirement_kw');
 if(drawerSummary) drawerSummary.textContent = `Electrified HVAC load model: ${nf(reqElec, 0)} kWh/yr electrical (${extraElec >= 0 ? '+' : ''}${nf(extraElec, 0)} kWh vs baseline) · ${nf(reqUseful, 0)} kWh thermal @ SCOP ${nf(scop, 1)} · Peak ${maxReqElecKw != null ? nf(maxReqElecKw, 2) + ' kWe' : 'unavailable'}${isOutdated ? ' · Inputs changed; previous result' : ''}`;

 const s = r.settings || {};
 const tzMode = s.timezone_mode || 'civil';
 const tzText = tzMode === 'standard' ? 'Standard fixed UTC+1' : 'Europe/Rome (DST)';
 const schedMode = s.heating_schedule_mode === 'continuous' ? 'Continuous (24h)' : 'Scheduled operating windows';

 const staleNoticeHtml = isOutdated ? `
  <div class="stale-comparison-notice" role="status" style="margin-bottom:12px;padding:8px 12px;background:#fff8e9;border:1px solid #e6d49f;border-radius:6px;font-size:12px;color:#705018;">
    ⚠️ Inputs changed or recalculation pending · AC heating comparison reflects previously accepted result (${pctStr}% demand)
  </div>
 ` : '';

 panel.innerHTML = `
  <div class="section-head">
   <div>
    <p class="eyebrow">ANNUAL AC HEATING DEMAND</p>
    <h2>AC heating demand · counterfactual vs requested</h2>
   </div>
  </div>
  ${staleNoticeHtml}
  <div class="ac-heating-grid cards">
   <div class="ac-heating-card">
    <div class="label">Baseline AC heating (0%)</div>
    <div class="value">${fmt(baseElec, 0)} kWh/year</div>
    <small>Same-scenario counterfactual (0% extra demand margin with identical electrifying equipment), not historical baseline load scenario</small>
   </div>
   <div class="ac-heating-card">
    <div class="label">Requested AC heating (+${pctStr}%)</div>
    <div class="value">${fmt(reqElec, 0)} kWh/year</div>
    <small>Requested AC electricity at accepted ${pctStr}% demand</small>
   </div>
   <div class="ac-heating-card">
    <div class="label">Additional AC electricity</div>
    <div class="value">${extraElec >= 0 ? '+' : ''}${fmt(extraElec, 0)} kWh/year</div>
    <small>Additional electricity above 0% counterfactual</small>
   </div>
   <div class="ac-heating-card">
    <div class="label">Heating SCOP</div>
    <div class="value">${nf(scop, (scop % 1 === 0 ? 0 : 2))}</div>
    <small>Seasonal coefficient of performance</small>
   </div>
   <div class="ac-heating-card">
    <div class="label">Requested useful heat</div>
    <div class="value">${fmt(reqUseful, 0)} kWh thermal/year</div>
    <small>Requested/modeled useful heat at requested demand</small>
   </div>
  </div>

  <div class="ac-heating-diag-grid">
   <div class="ac-heating-diag-box">
    <h4>Schedule &amp; operating duration</h4>
    <div class="ac-heating-diag-item">
     <span>Operating mode</span>
     <span class="ac-heating-diag-val">${esc(schedMode)}</span>
    </div>
    <div class="ac-heating-diag-item">
     <span>Timezone clock</span>
     <span class="ac-heating-diag-val">${esc(tzText)}</span>
    </div>
    <div class="ac-heating-diag-item">
     <span>Nominal daily union duration</span>
     <span class="ac-heating-diag-val">${nominalDailyHours != null ? `${nf(nominalDailyHours, 1)} h/day` : '—'}</span>
    </div>
    <div class="ac-heating-diag-item">
     <span>Eligible duration per realization</span>
     <span class="ac-heating-diag-val">${eligHoursStr}</span>
    </div>
   </div>
   <div class="ac-heating-diag-box">
    <h4>Power &amp; capacity requirements</h4>
    <div class="ac-heating-diag-item">
     <span>Max interval-average electrical demand</span>
     <span class="ac-heating-diag-val">${maxIntAvgKw != null ? `${nf(maxIntAvgKw, 2)} kW electrical` : '—'}</span>
    </div>
    <div class="ac-heating-diag-item">
     <span>Max permitted-duration electrical requirement</span>
     <span class="ac-heating-diag-val">${maxReqElecKw != null ? `${nf(maxReqElecKw, 2)} kW electrical` : '—'}</span>
    </div>
    <div class="ac-heating-diag-item">
     <span>Max permitted-duration useful heat requirement</span>
     <span class="ac-heating-diag-val">${maxReqThermalKw != null ? `${nf(maxReqThermalKw, 2)} kW thermal` : '—'}</span>
    </div>
   </div>
  </div>

  <div class="ac-heating-notes muted">
   <p><strong>Simulation notes &amp; boundaries:</strong></p>
   <ul>
    <li>Monthly heating budgets are fixed from historical gas consumption; weather variations distribute timing across hours.</li>
    <li>Requested power is a model-derived requirement to meet the budget within permitted operating windows; equipment adequacy and indoor comfort are unassessed.</li>
    <li>Hourly simulation cannot resolve within-hour instantaneous cycling peaks or minute-accurate PV coincidence.</li>
    <li>No equipment sizing or hardware recommendations are provided.</li>
   </ul>
  </div>
 `;
}

function invalidateBehaviorAndSensitivity(){
 behaviorToken++;
 behaviorMatrixCache=null;
 behaviorMatrixKey='';
 if(typeof invalidateSummarySensitivity==='function')invalidateSummarySensitivity();
 else{
  summarySensitivityReqId++;
  const btn=typeof document!=='undefined'?(document.getElementById('runSummarySensitivityBtn')||(typeof $==='function'?$('runSummarySensitivityBtn'):null)):null;
  if(btn){
   btn.disabled=false;
   btn.textContent='Update sensitivity';
  }
 }
 renderAnnualEnergyFlow();
 renderBehaviorPending();
}

async function refreshBehaviorViews(){
 const behView=$('behavior');
 if(behView&&behView.hidden)return;
 const scen=settings.load_scenario||'electrified';
 document.querySelectorAll('[data-behavior-scen]').forEach(b=>{
  const isActive=b.dataset.behaviorScen===scen;
  b.classList.toggle('active',isActive);
  b.setAttribute('aria-pressed',isActive?'true':'false');
 });
 const dhSelect=$('dayhourScenario');
 if(dhSelect&&!dhSelect.value)dhSelect.value=scen;

 const currentFp=inputFingerprint(settings,quote);
 if(!current?.result||acceptedInputFingerprint!==currentFp){
  renderAnnualEnergyFlow();
  renderHeatPumpReport(current?.result);
  renderAcHeatingComparison(current?.result);
  renderBehaviorPending();
  return;
 }

 const normSettings=deepClone(current.result.settings);
 const key=`${scen}_${currentFp}`;
 if(behaviorMatrixCache&&behaviorMatrixKey===key){
  renderAnnualEnergyFlow();
  renderHeatPumpReport(current?.result);
  renderAcHeatingComparison(current?.result);
  drawTypicalDayChart(behaviorMatrixCache,activeSeason);
  drawMonthHourHeatmap(behaviorMatrixCache,activeHeatmapMetric);
  if(typeof renderSwitchHourOptimizer==='function')renderSwitchHourOptimizer(behaviorMatrixCache);
  return;
 }
 const token=++behaviorToken;
 const dispatchedFp=currentFp;
 const dispatchedScen=scen;
 const pendingKey=key+'_'+JSON.stringify(normSettings);
 let reqPromise;
 try{
  reqPromise=pendingBehaviorRequests.get(pendingKey);
  if(!reqPromise){
   try{
    reqPromise=api('/api/behavior-matrix',{
     settings:normSettings,
     scenario:scen,
     representative:'median'
    });
    pendingBehaviorRequests.set(pendingKey,reqPromise);
   }catch(syncErr){
    pendingBehaviorRequests.delete(pendingKey);
    throw syncErr;
   }
  }
  renderAnnualEnergyFlow();
  renderHeatPumpReport(current?.result);
  renderAcHeatingComparison(current?.result);
  renderBehaviorPending();
  const data=await reqPromise;
  if(token!==behaviorToken)return;
  if(dispatchedFp!==inputFingerprint(settings,quote))return;
  if(dispatchedScen!==(settings.load_scenario||'electrified'))return;
  behaviorMatrixCache=data;
  behaviorMatrixCache.battery_kwh=Number(normSettings.battery_kwh??0);
  behaviorMatrixKey=key;
  renderAnnualEnergyFlow();
  drawTypicalDayChart(data,activeSeason);
  drawMonthHourHeatmap(data,activeHeatmapMetric);
  if(typeof renderSwitchHourOptimizer==='function')renderSwitchHourOptimizer(data);
 }catch(err){
  if(token===behaviorToken){
   renderBehaviorError(err.message||err);
  }
 }finally{
  if(reqPromise&&pendingBehaviorRequests.get(pendingKey)===reqPromise){
   pendingBehaviorRequests.delete(pendingKey);
  }
 }
}

function drawTypicalDayChart(data,season='summer'){
 const chartEl=$('typicalDayChart');
 if(!chartEl)return;
 const seasons=data?.matrix?.seasons;
 if(!seasons)return;
 const hours=seasons[season]||(season==='annual'?seasons.annual:null)||seasons.summer;
 if(!hours)return;
 const width=720,height=300,left=60,right=650,top=25,bottom=250;
 const plotW=right-left,plotH=bottom-top;

 const maxPower=Math.max(1,...hours.map(h=>Math.max(
   h.pv||0,
   h.load||0,
   h.import||0,
   h.export||0,
   h.pv_envelope?.[1]||0,
   h.load_envelope?.[1]||0
 )))*1.15;
 const usableCapacity=Number((data?.battery_kwh!=null?data.battery_kwh:current?.result?.settings?.battery_kwh)??0);
 const maxEnvSoc=Math.max(0,...hours.map(h=>Math.max(h.soc||0,h.soc_envelope?.[1]||0)));
 const maxSoc=Math.max(1,usableCapacity,maxEnvSoc);

 const xPos=h=>left+(h/23)*plotW;
 const yPower=v=>bottom-(Math.max(0,v)/maxPower)*plotH;
 const ySoc=v=>bottom-(Math.max(0,Math.min(maxSoc,v))/maxSoc)*plotH;

 let body='';
 for(let i=0;i<=4;i++){
  const frac=i/4;
  const y=bottom-frac*plotH;
  const pVal=maxPower*frac;
  const sVal=maxSoc*frac;
  body+=`<line x1="${left}" x2="${right}" y1="${y}" y2="${y}" stroke="#e0e7df"/>`;
  body+=`<text x="${left-8}" y="${y+4}" text-anchor="end" font-size="10" fill="#62756e">${nf(pVal,1)} kW</text>`;
  body+=`<text x="${right+8}" y="${y+4}" text-anchor="start" font-size="10" fill="#e6b65b">${nf(sVal,1)} kWh</text>`;
 }
 body+=`<text x="${left-8}" y="${top-8}" text-anchor="end" font-size="10" font-weight="bold" fill="#62756e">Power (kW)</text>`;
 body+=`<text x="${right+8}" y="${top-8}" text-anchor="start" font-size="10" font-weight="bold" fill="#e6b65b">Battery SOC (kWh)</text>`;

 for(let h=0;h<24;h+=3){
  const x=xPos(h);
  body+=`<line x1="${x}" x2="${x}" y1="${bottom}" y2="${bottom+4}" stroke="#aab7ac"/>`;
  body+=`<text x="${x}" y="${bottom+16}" text-anchor="middle" font-size="10" fill="#62756e">${String(h).padStart(2,'0')}:00</text>`;
 }

 const envPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${yPower(h.pv_envelope?.[1]??h.pv).toFixed(1)}`).join(' ')+' '+hours.slice().reverse().map((h,i)=>`${xPos(23-i).toFixed(1)},${yPower(h.pv_envelope?.[0]??h.pv).toFixed(1)}`).join(' ');
 body+=`<polygon points="${envPts}" fill="#6285bd" opacity="0.15"/>`;

 if(usableCapacity>0&&hours.some(h=>h.soc_envelope)){
  const socEnvPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${ySoc(h.soc_envelope?.[1]??h.soc).toFixed(1)}`).join(' ')+' '+hours.slice().reverse().map((h,i)=>`${xPos(23-i).toFixed(1)},${ySoc(h.soc_envelope?.[0]??h.soc).toFixed(1)}`).join(' ');
  body+=`<polygon points="${socEnvPts}" fill="#e6b65b" opacity="0.18"/>`;
 }

 const pvPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${yPower(h.pv).toFixed(1)}`).join(' ');
 const loadPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${yPower(h.load).toFixed(1)}`).join(' ');
 const directPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${yPower(h.direct).toFixed(1)}`).join(' ');
 const importPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${yPower(h.import).toFixed(1)}`).join(' ');
 const exportPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${yPower(h.export).toFixed(1)}`).join(' ');
 const socPts=hours.map((h,i)=>`${xPos(i).toFixed(1)},${ySoc(h.soc).toFixed(1)}`).join(' ');

 body+=`<polyline points="${pvPts}" fill="none" stroke="#6285bd" stroke-width="2.5"/>`;
 body+=`<polyline points="${loadPts}" fill="none" stroke="#bd8334" stroke-width="2"/>`;
 body+=`<polyline points="${directPts}" fill="none" stroke="#217a61" stroke-width="2" stroke-dasharray="4 2"/>`;
 body+=`<polyline points="${importPts}" fill="none" stroke="#9865a8" stroke-width="1.8"/>`;
 body+=`<polyline points="${exportPts}" fill="none" stroke="#81929e" stroke-width="1.8"/>`;
 body+=`<polyline points="${socPts}" fill="none" stroke="#e6b65b" stroke-width="3"/>`;
 const ceilingHours=hours.filter(h=>h.soc_ceiling_sub_kwh!=null&&Number.isFinite(Number(h.soc_ceiling_sub_kwh)));
 if(ceilingHours.length>1)body+=`<polyline points="${ceilingHours.map(h=>`${xPos(h.hour??hours.indexOf(h)).toFixed(1)},${ySoc(h.soc_ceiling_sub_kwh).toFixed(1)}`).join(' ')}" fill="none" stroke="#c0392b" stroke-width="1.6" stroke-dasharray="5 3"/>`;

 hours.forEach((h,i)=>{
  const weatherRanges=[['pv','PV','kW'],['load','Load','kW'],['direct','Direct','kW'],['soc','Battery SOC','kWh'],['import','Grid Import','kW'],['export','Grid Export','kW']].map(([key,label,unit])=>{
   const range=h[`${key}_envelope`];
   return Array.isArray(range)&&range.length===2&&range.every(v=>v!=null&&Number.isFinite(Number(v)))?`\n${label} P10–P90: ${nf(range[0],2)}–${nf(range[1],2)} ${unit}`:'';
  }).join('');
  const tip=`${title(season)} average day · Hour ${i}:00–${(i+1)%24}:00\nPV: ${nf(h.pv,2)} kW\nLoad: ${nf(h.load,2)} kW\nDirect: ${nf(h.direct,2)} kW\nBattery SOC: ${nf(h.soc,2)} kWh\nGrid Import: ${nf(h.import,2)} kW\nGrid Export: ${nf(h.export,2)} kW${weatherRanges}${h.soc_ceiling_sub_kwh!=null&&Number.isFinite(Number(h.soc_ceiling_sub_kwh))?`\nAverage morning charge ceiling (sub-inverter): ${nf(h.soc_ceiling_sub_kwh,2)} kWh`:''}`;
  body+=`<g ${chartTarget(tip)}><circle cx="${xPos(i)}" cy="${ySoc(h.soc)}" r="3" fill="#e6b65b"/><circle class="chart-hit" cx="${xPos(i)}" cy="${yPower(h.pv)}" r="8"/></g>`;
 });

 const peakPv=Math.max(...hours.map(h=>h.pv||0));
 const peakSoc=Math.max(...hours.map(h=>h.soc||0));
 const peakImp=Math.max(...hours.map(h=>h.import||0));
 const storageTakeaway=usableCapacity>0
  ?`max battery storage utilization ${nf(peakSoc,1)} kWh (${fmt(peakSoc/usableCapacity,'pct')})`
  :`no battery storage installed (${nf(peakSoc,1)} kWh)`;
 const takeaway=`<p class="chart-takeaway">${title(season)} dispatch takeaway · average-day profile: peak PV generation ${nf(peakPv,2)} kW, ${storageTakeaway}, and peak grid import ${nf(peakImp,2)} kW.</p>`;
 chartEl.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Average day hourly dispatch for ${esc(season)}; shaded bands show P10–P90 weather variation">${body}</svg>${takeaway}`;
}

function drawMonthHourHeatmap(data,metric='import'){
 const chartEl=$('monthHourHeatmap');
 if(!chartEl||!data?.matrix?.months)return;
 const months=data.matrix.months;
 const unit=metric==='soc'?'kWh':'kWh/h';
 const heatmapRoute=(typeof current!=='undefined'&&current?.result&&typeof routeDisplayForResult==='function')?routeDisplayForResult(current.result):'';
 const monthNames=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
 const width=720,height=280,left=45,right=705,top=20,bottom=250;
 const cellW=(right-left)/24;
 const cellH=(bottom-top)/12;

 let allVals=[];
 let peakVal=0,peakMonth=0,peakHour=0;
 months.forEach((row,m)=>{
  row.forEach((cell,h)=>{
   const v=cell[metric]||0;
   allVals.push(v);
   if(v>peakVal){
    peakVal=v;
    peakMonth=m;
    peakHour=h;
   }
  });
 });
 const maxVal=Math.max(0.1,...allVals);

 let body='';
 monthNames.forEach((name,m)=>{
  const y=top+m*cellH+cellH/2+4;
  body+=`<text x="${left-6}" y="${y}" text-anchor="end" font-size="10" fill="#62756e">${name}</text>`;
 });
 for(let h=0;h<24;h+=2){
  const x=left+h*cellW+cellW/2;
  body+=`<text x="${x}" y="${bottom+14}" text-anchor="middle" font-size="9" fill="#62756e">${String(h).padStart(2,'0')}</text>`;
 }

 months.forEach((row,m)=>{
  row.forEach((cell,h)=>{
   const v=cell[metric]||0;
   const frac=Math.min(1,Math.max(0,v/maxVal));
   const x=left+h*cellW;
   const y=top+m*cellH;
   const color=metric==='import'
     ?`rgb(${Math.round(245-frac*210)}, ${Math.round(248-frac*170)}, ${Math.round(245-frac*180)})`
     :metric==='clipping'
      ?`rgb(${Math.round(250-frac*190)}, ${Math.round(245-frac*105)}, ${Math.round(235-frac*20)})`
      :`rgb(${Math.round(255-frac*160)}, ${Math.round(250-frac*170)}, ${Math.round(235-frac*200)})`;
   const metricTitle=metric==='soc'?'Battery SoC':metric==='import'?'Grid Import':metric==='clipping'?'PV curtailed by selected dispatch topology':'Grid Export';
   const tip=`${monthNames[m]} · Hour ${h}:00–${(h+1)%24}:00\n${metricTitle}: ${nf(v,2)} ${unit}${metric==='clipping'&&heatmapRoute?`\nDispatch routing: ${heatmapRoute}`:''}`;
   body+=`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(cellW-1).toFixed(1)}" height="${(cellH-1).toFixed(1)}" class="dispatch-heatmap-cell" style="--heatmap-intensity:${(frac*100).toFixed(3)}%" fill="${color}" rx="1"><title>${esc(tip)}</title></rect>`;
  });
 });

 const metricLabel=metric==='soc'?'battery state of charge':metric==='export'?'grid export':metric==='clipping'?'PV clipping':'grid import';
 let scheduleLegend='';
 const cfg=data.selected_config||{};
 if(cfg.routing_priority==='scheduled_hybrid'){
  const start=Number(cfg.scheduled_start_month??3),end=Number(cfg.scheduled_end_month??9),sw=Number(cfg.routing_switch_hour??11);
  for(let m=1;m<=12;m++){
   if(!(start<=end?m>=start&&m<=end:m>=start||m<=end))continue;
   const x=left+6*cellW,y=top+(m-1)*cellH,w=Math.max(0,sw-6)*cellW;
   body+=`<rect class="schedule-window" x="${x}" y="${y}" width="${w}" height="${cellH}" fill="none" stroke="#4b615b" stroke-width="1" stroke-dasharray="3 3" pointer-events="none"/><line x1="${x+w}" y1="${y}" x2="${x+w}" y2="${y+cellH}" stroke="#314f45" stroke-dasharray="3 3" pointer-events="none"/>`;
  }
  scheduleLegend='<p class="schedule-window-legend">┆ Scheduled AM Grid-First window (sub-inverter charging paused; clipping recapture active)</p>';
 }
 let heatmapTakeaway;
 if(peakVal<=1e-6){
  heatmapTakeaway=`<p class="chart-takeaway">Annual ${metricLabel} dispatch takeaway: zero ${metricLabel}s modeled across all monthly intervals.</p>`;
 }else{
  const hourLabel=`${String(peakHour).padStart(2,'0')}:00–${String((peakHour+1)%24).padStart(2,'0')}:00`;
  heatmapTakeaway=`<p class="chart-takeaway">Annual ${metricLabel} dispatch takeaway: ${metric==='soc'?'peak stored energy':'peak hourly flow'} reaches ${nf(peakVal,2)} ${unit} in ${monthNames[peakMonth]} at ${hourLabel}.</p>`;
 }
 chartEl.innerHTML=`<svg data-dispatch-metric="${esc(metric)}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Month × Hour ${esc(metric)} heatmap">${body}</svg>${scheduleLegend}${heatmapTakeaway}`;
}

function renderAdjacentPrices(){
 const el=$('adjacentPricesContent');
 if(!el||!current?.result)return;
 const r=current.result;
 const sett=r.settings;
 const analysis=r.export_price_analysis;
 const retailElec=sett.retail_commodity??0.1169;
 const marginalElecUnit=((sett.retail_commodity??0)+(sett.retail_regulated??0)+(sett.electric_excise??0))*(1+(sett.electric_vat??0.1));
 const gasPrice=sett.gas_commodity??0.45;
 const isLegacyActive=analysis?.legacy_capture_factor_active;
 const baseWholesale=analysis?.baseload_price_eur_kwh??sett.export_market_price??0.128;
 const capturedPrice=isLegacyActive
  ?(analysis?.flat_benchmark_comparison?.flat_captured_price_eur_kwh??(baseWholesale*(sett.export_capture_factor??0.74)))
  :(analysis?.export_weighted_captured_price_eur_kwh??(baseWholesale*(sett.export_capture_factor??0.74)));
 const captureMultiplier=isLegacyActive
  ?fmt(sett.export_capture_factor??0.74,'pct')
  :(analysis?.export_weighted_capture_factor!=null?fmt(analysis.export_weighted_capture_factor,'pct'):fmt(sett.export_capture_factor??0.74,'pct'));

 el.innerHTML=`
   <article class="card price-benchmark-card">
     <div class="label">RETAIL CONSUMPTION TARIFFS</div>
     <div class="price-row"><span>Electricity Commodity Rate:</span> <strong>${fmt(retailElec,'lcoe')}/kWh</strong></div>
     <div class="price-row"><span>Marginal Variable Electricity Rate:</span> <strong>${fmt(marginalElecUnit,'lcoe')}/kWh</strong></div>
     <div class="price-row"><span>Natural Gas Commodity Rate (pre-tax):</span> <strong>${fmt(gasPrice,'lcoe')}/Smc</strong></div>
     <small class="muted">Audited from residential utility invoices and regulated components. Marginal variable rate excludes annual fixed meter fees; avoided electricity consumption saves this marginal unit rate.</small>
   </article>
   <article class="card price-benchmark-card">
     <div class="label">EXPORT VALORIZATION (PMG / RID)</div>
     <div class="price-row"><span>Wholesale Baseload Reference:</span> <strong>${fmt(baseWholesale,'lcoe')}/kWh</strong></div>
     <div class="price-row"><span>Hourly Captured Export Price:</span> <strong>${fmt(capturedPrice,'lcoe')}/kWh</strong></div>
     <div class="price-row"><span>Effective Capture Multiplier:</span> <strong>${captureMultiplier}</strong></div>
     <small class="muted">Zonal CSUD daytime solar shape. Subject to GSE administrative fee and marginal income tax.</small>
   </article>
 `;
 }

let gasCostsAuditOutdated = false;

function invalidateGasCostsAudit(){
 gasCostsAuditOutdated = true;
 const article = typeof document !== 'undefined' ? (document.getElementById('gasCostsArticle') || document.querySelector?.('#gasCostsArticle')) : null;
 if(!article) return;
 article.classList.add('stale-overlay');
 const notice = article.querySelector?.('.stale-comparison-notice') || (typeof document !== 'undefined' ? document.getElementById('gasCostsStaleNotice') : null);
 if(notice){
  notice.hidden = false;
  if(notice.style) notice.style.display = '';
 }
}

const markGasCostsAuditOutdated = invalidateGasCostsAudit;

function isGasCostsAuditMatching(res = current?.result){
 if(!res || !res.settings) return false;
 if(typeof loanPending !== 'undefined' && loanPending) return false;
 const s = typeof settings !== 'undefined' ? settings : null;
 if(!s) return false;
 if(typeof acceptedInputFingerprint !== 'undefined' && acceptedInputFingerprint !== null && typeof inputFingerprint === 'function'){
  const q = typeof quote !== 'undefined' ? quote : {line_items:[]};
  if(acceptedInputFingerprint !== inputFingerprint(s, q)) return false;
 }
 if(gasCostsAuditOutdated) return false;
 return true;
}

function acceptGasCostsAudit(result = current?.result){
 const r = (result && result.result) ? result.result : (result || current?.result);
 if(!r) return false;
 gasCostsAuditOutdated = false;
 const article = typeof document !== 'undefined' ? (document.getElementById('gasCostsArticle') || document.querySelector?.('#gasCostsArticle')) : null;
 if(article){
  article.classList.remove('stale-overlay');
  const notice = article.querySelector?.('.stale-comparison-notice') || (typeof document !== 'undefined' ? document.getElementById('gasCostsStaleNotice') : null);
  if(notice){
   notice.hidden = true;
   if(notice.style) notice.style.display = 'none';
  }
 }
 if(typeof renderGasCostsAudit === 'function') renderGasCostsAudit();
 return true;
}

function renderGasCostsAudit(){
 const content = typeof document !== 'undefined' ? (document.getElementById('gasCostsContent') || document.querySelector?.('#gasCostsContent')) : null;
 const article = typeof document !== 'undefined' ? (document.getElementById('gasCostsArticle') || document.querySelector?.('#gasCostsArticle')) : null;
 if(!content) return;

 const r = current?.result || current;
 const gasCosts = current?.gas_costs || current?.result?.gas_costs;
 if(!gasCosts || !gasCosts.baseline){
  content.innerHTML = `<div class="annual-flow-unavailable"><p>Gas cost audit unavailable for this scenario result. Please recalculate or refresh the scenario to generate auditable gas costs.</p></div>`;
  return;
 }

 const isMatching = isGasCostsAuditMatching(r);
 const isStale = !isMatching || gasCostsAuditOutdated;
 if(article){
  if(isStale) article.classList.add('stale-overlay');
  else article.classList.remove('stale-overlay');
 }

 const baseline = gasCosts.baseline;
 const remaining = gasCosts.remaining;
 const connStatus = gasCosts.connection_status;
 const refBill = gasCosts.reference_bill;
 const taxRules = gasCosts.tax_rules || {};
 const sources = taxRules.statutory_sources || {};
 const sett = (r && r.settings) ? r.settings : (typeof settings !== 'undefined' ? settings : {});

 const connStatusLabel = connStatus === 'disconnected'
  ? 'Meter disconnected (gas service eliminated)'
  : connStatus === 'retained_zero_volume'
   ? 'Meter active (retained zero volume, fixed fee only)'
   : 'Meter active (connected)';

 const loadScenarioTitle = sett.load_scenario ? title(sett.load_scenario) : 'Selected Scenario';
 const regRate = sett.gas_regulated ?? 0.395;
 const redVatPct = (baseline.reduced_vat_rate * 100).toFixed(1);
 const stdVatPct = (baseline.standard_vat_rate * 100).toFixed(1);
 const remRedVatPct = (remaining.reduced_vat_rate * 100).toFixed(1);
 const remStdVatPct = (remaining.standard_vat_rate * 100).toFixed(1);

 const staleNoticeHtml = `<div class="stale-comparison-notice" role="status" id="gasCostsStaleNotice"${isStale ? '' : ' hidden'}>⚠️ Inputs changed or recalculation pending · Gas cost audit reflects previously accepted result</div>`;

 const contextHtml = `
  <div class="gas-tax-context-card">
   <p><strong>Cumulative Calendar-Annual Progressive Brackets:</strong> Natural gas consumption taxes apply across four statutory bands: 0–120 Smc, 120–480 Smc, 480–1560 Smc, and over 1560 Smc/year. Each national excise applies strictly to consumption within that band; volume counters accumulate across the full calendar year and do not restart monthly.</p>
   <p><strong>Campania Regional Surcharge (ARISGAN):</strong> Campania establishes two statutory rates: 0–120 Smc/year and over 120 Smc/year. In the progressive allocation table below, bands above 120 Smc repeat the same over-120 rate across the higher national brackets.</p>
   <p><strong>Differentiated VAT:</strong> Reduced VAT applies to the first 480 Smc/year of variable consumption (including commodity, network charges, national excise, and regional surcharge). Standard VAT applies to variable consumption in excess of 480 Smc/year and to all supplier and network fixed charges.</p>
   <p><strong>Scenario Tax Rates:</strong> Current scenario rates are <strong>${redVatPct}%</strong> reduced VAT and <strong>${stdVatPct}%</strong> standard VAT (statutory defaults: 10% and 22%).</p>
   <p class="muted"><strong>Network Tariff Approximation:</strong> Pre-tax network and system charges are modeled at a flat approximation of <strong>${fmt(regRate, 'lcoe')}/Smc</strong> derived from the bill-period average. Underlying real network slabs and quarterly ARERA tariff adjustments are not reconstructed.</p>
  </div>
 `;

 const compTableHtml = `
  <div class="table-wrap" tabindex="0" role="region" aria-label="First-year gas cost comparison">
   <div class="gas-scroll-hint">Swipe/scroll horizontally to see all amounts →</div>
   <table class="gas-cost-table" aria-label="First-year gas cost comparison">
    <thead>
     <tr>
      <th style="width:36%;">Cost Component &amp; Tax Scope</th>
      <th style="width:32%;">
       <div>First-Year Annual Baseline</div>
       <small class="muted" style="display:block;font-weight:normal;text-transform:none;">${fmt(baseline.volume)} Smc · ${baseline.days} days · Connected<br>Profile annual reference (not single-month annualization)</small>
      </th>
      <th style="width:32%;">
       <div>Selected Scenario: ${esc(loadScenarioTitle)}</div>
       <small class="muted" style="display:block;font-weight:normal;text-transform:none;">${fmt(remaining.volume)} Smc · ${remaining.days} days · ${esc(connStatusLabel)}</small>
      </th>
     </tr>
    </thead>
     <tbody>
     <tr>
      <td><strong>Gas commodity (pre-tax)</strong></td>
      <td>${fmt(baseline.commodity_eur, 'eur2')}</td>
      <td>${fmt(remaining.commodity_eur, 'eur2')}</td>
     </tr>
     <tr>
      <td><strong>Gas network/system charges (pre-tax)</strong><br><small class="muted">Flat approximation @ ${fmt(regRate, 'lcoe')}/Smc</small></td>
      <td>${fmt(baseline.network_eur, 'eur2')}</td>
      <td>${fmt(remaining.network_eur, 'eur2')}</td>
     </tr>
     <tr>
      <td><strong>Gas supplier + network fixed charges (pre-tax)</strong></td>
      <td>${fmt(baseline.fixed_eur, 'eur2')}</td>
      <td>${fmt(remaining.fixed_eur, 'eur2')}</td>
     </tr>
     <tr>
      <td><strong>National gas excise</strong><br><small class="muted">Progressive Mezzogiorno annual brackets</small></td>
      <td>${fmt(baseline.national_excise_eur, 'eur2')}</td>
      <td>${fmt(remaining.national_excise_eur, 'eur2')}</td>
     </tr>
     <tr>
      <td><strong>Campania regional surcharge (ARISGAN)</strong><br><small class="muted">Progressive Campania annual brackets</small></td>
      <td>${fmt(baseline.regional_surcharge_eur, 'eur2')}</td>
      <td>${fmt(remaining.regional_surcharge_eur, 'eur2')}</td>
     </tr>
     <tr>
      <td>
       <strong>Reduced VAT</strong><br>
       <small class="muted">Taxable base × current rate (${redVatPct}%)</small>
      </td>
      <td>
       ${fmt(baseline.reduced_vat_eur, 'eur2')}<br>
       <small class="muted">${fmt(baseline.reduced_vat_base_eur, 'eur2')} base × ${redVatPct}%</small>
      </td>
      <td>
       ${fmt(remaining.reduced_vat_eur, 'eur2')}<br>
       <small class="muted">${fmt(remaining.reduced_vat_base_eur, 'eur2')} base × ${remRedVatPct}%</small>
      </td>
     </tr>
     <tr>
      <td>
       <strong>Standard VAT</strong><br>
       <small class="muted">Variable base + fixed base = total base × current rate (${stdVatPct}%)</small>
      </td>
      <td>
       ${fmt(baseline.standard_vat_eur, 'eur2')}<br>
       <small class="muted">${fmt(baseline.standard_vat_variable_base_eur, 'eur2')} (var) + ${fmt(baseline.standard_vat_fixed_base_eur, 'eur2')} (fixed) = ${fmt(baseline.standard_vat_total_base_eur, 'eur2')} base × ${stdVatPct}%</small>
      </td>
      <td>
       ${fmt(remaining.standard_vat_eur, 'eur2')}<br>
       <small class="muted">${fmt(remaining.standard_vat_variable_base_eur, 'eur2')} (var) + ${fmt(remaining.standard_vat_fixed_base_eur, 'eur2')} (fixed) = ${fmt(remaining.standard_vat_total_base_eur, 'eur2')} base × ${remStdVatPct}%</small>
      </td>
     </tr>
     <tr>
      <td><strong>Total VAT</strong></td>
      <td>${fmt(baseline.vat_eur, 'eur2')}</td>
      <td>${fmt(remaining.vat_eur, 'eur2')}</td>
     </tr>
     <tr class="total-row">
      <td><strong>Total first-year gas bill</strong></td>
      <td><strong>${fmt(baseline.total_eur, 'eur2')}</strong></td>
      <td><strong>${fmt(remaining.total_eur, 'eur2')}</strong></td>
     </tr>
    </tbody>
   </table>
  </div>
 `;

 function formatBracketRows(brackets){
  if(!brackets || !brackets.length) return '<tr><td colspan="7" class="muted">No volume allocated</td></tr>';
  return brackets.map(b => {
   const rangeText = b.upper_smc != null ? `${b.lower_smc}–${b.upper_smc} Smc` : 'Over 1560 Smc/year';
   const natRate = fmt(b.national_excise_rate, 'lcoe');
   const regRateVal = fmt(b.regional_surcharge_rate, 'lcoe');
   const vatPct = (b.vat_rate * 100).toFixed(1);
   return `
    <tr>
     <td><strong>${rangeText}</strong></td>
     <td>${fmt(b.quantity_smc)} Smc</td>
     <td>${natRate}/Smc → ${fmt(b.national_excise_eur, 'eur2')}</td>
     <td>${regRateVal}/Smc → ${fmt(b.regional_surcharge_eur, 'eur2')}</td>
     <td>${fmt(b.taxable_base_eur, 'eur2')}</td>
     <td>${vatPct}% → ${fmt(b.vat_eur, 'eur2')}</td>
     <td><strong>${fmt(b.total_eur, 'eur2')}</strong></td>
    </tr>
   `;
  }).join('');
 }

 const bracketsFoldHtml = `
  <details class="technical-details-fold">
   <summary>Annual progressive brackets allocation (auditable detail)</summary>
   <div style="margin-top:10px;">
    <p class="muted">Progressive slab breakdown for the first-year baseline (${fmt(baseline.volume)} Smc annual reference):</p>
    <p class="muted" style="margin-top:4px;font-size:12px;">Note: Campania regional surcharge has two statutory rates (0–120 Smc/year and over 120 Smc/year); the allocation table aligns with the four national excise bands by repeating the over-120 rate across higher brackets.</p>
    <div class="table-wrap" tabindex="0" role="region" aria-label="Baseline progressive brackets allocation">
     <div class="gas-scroll-hint">Swipe/scroll horizontally to see all amounts →</div>
     <table class="gas-cost-table" aria-label="Baseline progressive brackets allocation">
      <thead>
       <tr>
        <th>Bracket band</th>
        <th>Volume</th>
        <th>National excise</th>
        <th>Campania surcharge</th>
        <th>Taxable base</th>
        <th>VAT</th>
        <th>Total</th>
       </tr>
      </thead>
      <tbody>
       ${formatBracketRows(baseline.brackets)}
      </tbody>
     </table>
    </div>
    ${(remaining && remaining.volume > 0 && remaining.brackets && remaining.brackets.length) ? `
     <p class="muted" style="margin-top:14px;">Progressive slab breakdown for remaining scenario (${fmt(remaining.volume)} Smc):</p>
     <div class="table-wrap" tabindex="0" role="region" aria-label="Remaining scenario progressive brackets allocation">
      <div class="gas-scroll-hint">Swipe/scroll horizontally to see all amounts →</div>
      <table class="gas-cost-table" aria-label="Remaining scenario progressive brackets allocation">
       <thead>
        <tr>
         <th>Bracket band</th>
         <th>Volume</th>
         <th>National excise</th>
         <th>Campania surcharge</th>
         <th>Taxable base</th>
         <th>VAT</th>
         <th>Total</th>
        </tr>
       </thead>
       <tbody>
        ${formatBracketRows(remaining.brackets)}
       </tbody>
      </table>
     </div>
    ` : ''}
   </div>
  </details>
 `;

 let reconciliationFoldHtml = '';
 if(refBill && refBill.invoice && refBill.modeled && refBill.residuals){
  const inv = refBill.invoice;
  const mod = refBill.modeled;
  const res = refBill.residuals;
  const modExciseTotal = (mod.national_excise_eur ?? 0) + (mod.regional_surcharge_eur ?? 0);
  const hasVariance = Math.abs(res.total_eur) > 0.01;

  reconciliationFoldHtml = `
   <details class="technical-details-fold">
    <summary>Utility invoice reconciliation (${nf(inv.volume_smc)} Smc / ${nf(inv.period_days)} days reference)</summary>
    <div style="margin-top:10px;">
     <p class="muted">Direct audit comparing the customer utility reference invoice against the live modeled reference bill under current tariffs and statutory tax rates:</p>
     <div class="table-wrap" tabindex="0" role="region" aria-label="Reference invoice reconciliation">
      <div class="gas-scroll-hint">Swipe/scroll horizontally to see all amounts →</div>
      <table class="gas-cost-table" aria-label="Reference invoice reconciliation">
       <thead>
        <tr>
         <th>Bill Component</th>
         <th>Audited Reference Invoice<br><small class="muted" style="font-weight:normal;">${inv.volume_smc} Smc · ${inv.period_days} days</small></th>
         <th>Live Modeled Reference<br><small class="muted" style="font-weight:normal;">Illustrative prior: ${refBill.illustrative_prior_smc} Smc</small></th>
         <th>Residual / Variance</th>
        </tr>
       </thead>
       <tbody>
        <tr>
         <td><strong>Consumption (pre-tax)</strong></td>
         <td>${fmt(inv.consumption_eur, 'eur2')}</td>
         <td>${fmt(mod.variable_pretax_eur, 'eur2')}</td>
         <td>${fmt(res.consumption_eur, 'eur2')}</td>
        </tr>
        <tr>
         <td><strong>Fixed charges (pre-tax)</strong></td>
         <td>${fmt(inv.fixed_eur, 'eur2')}</td>
         <td>${fmt(mod.fixed_eur, 'eur2')}</td>
         <td>${fmt(res.fixed_eur, 'eur2')}</td>
        </tr>
        <tr>
         <td><strong>Excise + Regional surcharge</strong></td>
         <td>${fmt(inv.excise_including_regional_eur, 'eur2')}</td>
         <td>${fmt(modExciseTotal, 'eur2')}</td>
         <td>${fmt(res.excise_including_regional_eur, 'eur2')}</td>
        </tr>
        <tr>
         <td><strong>VAT</strong></td>
         <td>${fmt(inv.vat_eur, 'eur2')}</td>
         <td>${fmt(mod.vat_eur, 'eur2')}</td>
         <td>${fmt(res.vat_eur, 'eur2')}</td>
        </tr>
        <tr class="total-row">
         <td><strong>Total reference bill</strong></td>
         <td><strong>${fmt(inv.total_eur, 'eur2')}</strong></td>
         <td><strong>${fmt(mod.total_eur, 'eur2')}</strong></td>
         <td><strong>${fmt(res.total_eur, 'eur2')}</strong></td>
        </tr>
       </tbody>
      </table>
     </div>
     ${hasVariance ? `
      <div class="notice" style="margin-top:10px;">
       <strong>Current settings variance:</strong> Current scenario settings differ from default reference tariffs/taxes, producing a modeled total variance of ${fmt(res.total_eur, 'eur2')} against the reference invoice.
      </div>
     ` : ''}
     <div class="muted" style="margin-top:10px;font-size:11.5px;line-height:1.5;">
      <p>• The reference calculation uses the supplied volume and tariff parameters; confirm actual cumulative YTD consumption to identify the applicable progressive brackets.</p>
      <p>• The ${nf(refBill.illustrative_prior_smc, 1)} Smc prior cumulative figure is strictly an illustrative assumption, not measured.</p>
      <p>• Progressive brackets accumulate across the calendar year and do not reset monthly. Actual calendar-YTD consumption cannot be inferred from a rolling 12-month model.</p>
     </div>
    </div>
   </details>
  `;
 }

 const natExc = sources.national_excise;
 const regSur = sources.regional_surcharge;
 const vatSrc = sources.vat;

 const sourcesHtml = `
  <div class="statutory-sources-card">
   <strong>Statutory Tax Sources &amp; Verification (verified 2026-10-16):</strong>
   <ul>
    ${natExc ? `<li><strong>National excise:</strong> <a href="${esc(natExc.url)}" target="_blank" rel="noopener">${esc(natExc.title)}</a> (${esc(natExc.scope)}; verified ${esc(natExc.verified_date)})</li>` : ''}
    ${regSur ? `<li><strong>Campania regional surcharge:</strong> <a href="${esc(regSur.url)}" target="_blank" rel="noopener">${esc(regSur.title)}</a> (${esc(regSur.scope)}; verified ${esc(regSur.verified_date)})</li>` : ''}
    ${vatSrc ? `<li><strong>Gas VAT:</strong> ${esc(vatSrc.title)} — ${esc(vatSrc.scope)} (${(vatSrc.urls||[]).map((u,i)=>`<a href="${esc(u)}" target="_blank" rel="noopener">Source ${i+1}</a>`).join(', ')}; verified ${esc(vatSrc.verified_date)})</li>` : ''}
   </ul>
  </div>
 `;

 content.innerHTML = `
  ${staleNoticeHtml}
  ${contextHtml}
  ${compTableHtml}
  ${bracketsFoldHtml}
  ${reconciliationFoldHtml}
  ${sourcesHtml}
 `;
}

function renderLcoeCards(){
 const el=$('lcoeContent');
 if(!el||!current?.result)return;
 const f=current.result.finance;
 const m=current.result.metrics;
 const sett=current.result.settings;
 const grossLcoe=f.lcoe?.median??m.lcoe_eur_kwh?.median??0;
 const netLcoe=f.lcoe_net_incentives?.median??grossLcoe;
 const marginalElecUnit=((sett.retail_commodity??0)+(sett.retail_regulated??0)+(sett.electric_excise??0))*(1+(sett.electric_vat??0.1));
 const lcoeAdvantage=marginalElecUnit-netLcoe;

 el.innerHTML=`
   <div class="card"><div class="label">GROSS LCOE (PRE-TAX DEDUCTION)</div><div class="value">${fmt(grossLcoe,'lcoe')} <small>/ kWh</small></div><small>Total discounted capex &amp; opex / 20-year generation</small></div>
   <div class="card"><div class="label">NET LCOE (${fmt(sett.deduction_rate,'pct')} FISCAL DEDUCTION)</div><div class="value">${fmt(netLcoe,'lcoe')} <small>/ kWh</small></div><small>True net generation cost after 10-year deduction</small></div>
   <div class="card"><div class="label">MARGINAL GRID PURCHASE BENCHMARK</div><div class="value">${fmt(marginalElecUnit,'lcoe')} <small>/ kWh</small></div><small>Marginal residential retail tariff (excl. fixed fees)</small></div>
   <div class="card featured"><div class="label">NET LCOE ADVANTAGE VS GRID</div><div class="value">${lcoeAdvantage>=0?'+':''}${fmt(lcoeAdvantage,'lcoe')} <small>/ kWh</small></div><small>Signed difference between grid rate and net solar generation cost. Exported energy earns wholesale market value, not retail grid rate.</small></div>
 `;
}

const CHECKLIST_KEY='pv-lab-installer-checklist-v1';
function readChecklist(){
 try{return JSON.parse(localStorage.getItem(CHECKLIST_KEY)||'{}');}catch{return {};}
}
function saveChecklist(state){
 try{localStorage.setItem(CHECKLIST_KEY,JSON.stringify(state));}catch{}
}
function renderLedger(r){
 const el=$('ledger');
 if(!el||!r?.assumptions)return;
 let list=r.assumptions;
 let filterNotice='';
 if(ledgerStatusFilter==='unconfirmed'){
  list=list.filter(a=>['proposed','placeholder','missing','unconfirmed'].includes(a.status));
  filterNotice=`<div class="ledger-filter-banner" style="margin-bottom:10px;padding:8px 12px;background:#fff8e6;border:1px solid #eed699;border-radius:6px;display:flex;align-items:center;justify-content:space-between;"><span style="font-size:12px;font-weight:600;color:#8a6508;">Filtering: showing ${list.length} unconfirmed / proposed assumptions</span><button type="button" class="action-btn-small" onclick="resetLedgerFilter()" style="font-size:11px;">Show all assumptions</button></div>`;
 }
 const rows=list.map(a=>{
   const nameLink=`<a href="#controls" data-focus-setting="${esc(a.name)}" class="ledger-setting-link">${esc(title(a.name))}</a>`;
   const statusChip=`<span class="tag ${esc(a.status||'unconfirmed')}">${esc(a.status||'unconfirmed')}</span>`;
   const val=a.value!=null?esc(String(a.value)):'<span class="muted">missing</span>';
   const rangeStr=a.plausible_range?`[${a.plausible_range.join(', ')}]`:'—';
   const sourceStr=`${esc(a.source||'')} · <small class="muted">${esc(a.date_accessed||'')} · ${esc(a.owner||'')}</small>`;
   return `<tr><td>${nameLink}</td><td><strong>${val}</strong></td><td>${esc(a.unit||'')}</td><td>${statusChip}</td><td><small>${esc(rangeStr)}</small></td><td>${sourceStr}</td></tr>`;
 });
 el.innerHTML=`
   ${filterNotice}
   <table class="ledger-table">
     <thead>
       <tr>
         <th>Assumption</th>
         <th>Value</th>
         <th>Unit</th>
         <th>Provenance status</th>
         <th>Plausible range</th>
         <th>Source / access date / owner</th>
       </tr>
     </thead>
     <tbody>
       ${rows.join('')}
     </tbody>
   </table>
 `;
 el.onclick=e=>{
   const link=e.target.closest('[data-focus-setting]');
   if(!link)return;
   e.preventDefault();
   revealAndFocusSetting(link.dataset.focusSetting);
 };
}
function renderInstallerChecklist(){
 const el=$('installerChecklist');
 if(!el)return;
 const items=[
   {id:'check_roof_south',title:'South Roof Physical Usable Area',desc:'Verify unobstructed square meters on south facet, chimney setback, and tile condition for up to 22 panels.'},
   {id:'check_roof_secondary',title:'Secondary Pitch & Dormer Structure',desc:'Inspect flat roof membrane / dormer pitch load capacity for ballast mounting.'},
   {id:'check_quadro_elettrico',title:'Main Electrical Panel (Quadro Generale)',desc:'Check DIN-rail spare space for dedicated PV circuit breaker, differential protection, and smart meter CT clamp.'},
   {id:'check_inverter_space',title:'Inverter Mounting Location',desc:'Verify well-ventilated wall space with ≥30cm clearances in garage or technical room.'},
   {id:'check_battery_temp',title:'Battery Storage Location & Ambient Temp',desc:'Ensure dry indoor location maintaining 5–30°C to prevent winter capacity degradation and thermal throttling.'},
   {id:'check_cable_pathway',title:'DC / AC Conduit & Cable Routing',desc:'Confirm vertical duct or exterior conduit pathway from roof to inverter and from inverter to meter.'},
   {id:'check_boiler_dhw',title:'Heating System & Water Heater Interface',desc:'Assess boiler gas connection, plumbing clearances, and electrical circuit for planned heat pump transition.'},
   {id:'check_tax_capacity',title:'Fiscal Tax Capacity (Capienza IRPEF)',desc:'Verify homeowner has sufficient gross annual personal income tax liability to absorb 10 equal deduction installments.'}
 ];
 const state=readChecklist();
 el.innerHTML=items.map(item=>`
   <div class="checklist-item ${state[item.id]?'checked':''}">
     <label class="check-label">
       <input type="checkbox" data-check-id="${item.id}" ${state[item.id]?'checked':''}>
       <div class="check-text">
         <strong>${esc(item.title)}</strong>
         <p>${esc(item.desc)}</p>
       </div>
     </label>
   </div>
 `).join('');
 el.onchange=e=>{
  const cb=e.target.closest?e.target.closest('input[data-check-id]'):(e.target.dataset?.checkId!==undefined?e.target:null);
  if(!cb)return;
  const id=cb.dataset.checkId;
  state[id]=cb.checked;
  saveChecklist(state);
  cb.closest?.('.checklist-item')?.classList.toggle('checked',cb.checked);
 };
 if(typeof el.querySelectorAll==='function'){
  el.querySelectorAll('input[data-check-id]').forEach(cb=>{
   cb.onchange=e=>{
    const id=e.target.dataset.checkId;
    state[id]=e.target.checked;
    saveChecklist(state);
    e.target.closest?.('.checklist-item')?.classList.toggle('checked',e.target.checked);
   };
  });
 }
}

let empiricalCache={};
async function renderMarketCurve(windowKey='oct2025_sep2026'){
 const provTarget=$('marketCurveProvenance');
 const chartEl=$('marketSeasonalChart');
 const tableTarget=$('marketCurveTable');
 if(!chartEl)return;
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile){if(provTarget)provTarget.innerHTML=renderProfileMarketMetadata(activeResultProfile,schema?.catalog);chartEl.textContent='Selected revision price curve is shown in the export valuation result. Catalogue previews are not calculator inputs.';if(tableTarget)tableTarget.textContent='';return;}
 if(!empiricalCache[windowKey]){
  try{
   const resp=await api(`/api/empirical-prices?window=${encodeURIComponent(windowKey)}`);
   empiricalCache[windowKey]=resp;
  }catch(err){
   if(provTarget)provTarget.textContent='Failed to load empirical market price data: '+(err.message||err);
   return;
  }
 }
 const data=empiricalCache[windowKey];
 if(!data||!data.matrix_eur_kwh)return;
 if(provTarget&&data.window_meta){
  provTarget.textContent=`${data.window_meta.label} · ${data.window_meta.dates_coverage} · Mean CSUD: ${nf(data.window_meta.mean_csud_eur_mwh,2)} €/MWh (${nf(data.window_meta.mean_csud_eur_kwh,4)} €/kWh) · Zero/negative hours: ${data.window_meta.zero_negative_count} · Source: Official GME Day-Ahead XML files.`;
 }
 const monthDays=[31,28,31,30,31,30,31,31,30,31,30,31];
 const seasons=[
  {key:'spring',label:'Spring (Mar–May)',months:[2,3,4],color:'#217a61'},
  {key:'summer',label:'Summer (Jun–Aug)',months:[5,6,7],color:'#df8a54'},
  {key:'autumn',label:'Autumn (Sep–Nov)',months:[8,9,10],color:'#bd8334'},
  {key:'winter',label:'Winter (Dec–Feb)',months:[11,0,1],color:'#6285bd'},
  {key:'annual',label:'Annual 24h average',months:[0,1,2,3,4,5,6,7,8,9,10,11],color:'#163d35',bold:true}
 ];
 const seriesData=seasons.map(s=>{
  const totalDays=s.months.reduce((acc,m)=>acc+monthDays[m],0);
  const hourly=[];
  for(let h=0;h<24;h++){
   const weighted=s.months.reduce((acc,m)=>acc+data.matrix_eur_kwh[m][h]*1000*monthDays[m],0);
   hourly.push(weighted/totalDays);
  }
  return {...s,hourly};
 });
 const width=720,height=280,left=60,right=690,top=20,bottom=230;
 const plotW=right-left,plotH=bottom-top;
 const allVals=seriesData.flatMap(s=>s.hourly);
 const maxVal=Math.max(180,...allVals)*1.12;
 const minVal=Math.min(0,...allVals);
 const valRange=maxVal-minVal||1;
 const xPos=h=>left+(h/23)*plotW;
 const yPos=v=>bottom-((v-minVal)/valRange)*plotH;
 let svgBody='';
 const gridSteps=5;
 for(let i=0;i<=gridSteps;i++){
  const v=minVal+(valRange*i)/gridSteps;
  const y=yPos(v);
  svgBody+=`<line x1="${left}" x2="${right}" y1="${y}" y2="${y}" stroke="#e0e7df"/>`;
  svgBody+=`<text x="${left-8}" y="${y+4}" text-anchor="end" font-size="10" fill="#62756e">${nf(v,0)}</text>`;
 }
 svgBody+=`<text x="8" y="15" font-size="10" fill="#62756e">EUR / MWh</text>`;
 for(let h=0;h<24;h++){
  const x=xPos(h);
  if(h%3===0||h===23){
   svgBody+=`<line x1="${x}" x2="${x}" y1="${bottom}" y2="${bottom+4}" stroke="#aab7ac"/>`;
   svgBody+=`<text x="${x}" y="${bottom+16}" text-anchor="middle" font-size="10" fill="#62756e">${String(h).padStart(2,'0')}:00</text>`;
  }
 }
 svgBody+=`<text x="${(left+right)/2}" y="${bottom+32}" text-anchor="middle" font-size="10" fill="#62756e">Fixed UTC+1 standard time; in summer add one hour to read Italy's civil clock</text>`;
 seriesData.forEach(s=>{
  const pts=s.hourly.map((v,h)=>`${xPos(h).toFixed(1)},${yPos(v).toFixed(1)}`).join(' ');
  const strokeWidth=s.bold?'2.8':(s.key==='summer'?'2.4':'1.8');
  const strokeDash=s.bold?'stroke-dasharray="5 3"':'';
  svgBody+=`<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${strokeWidth}" ${strokeDash} opacity="0.9"/>`;
  s.hourly.forEach((v,h)=>{
   const cx=xPos(h),cy=yPos(v);
   const tip=`${s.label} · Hour ${h}:00–${(h+1)%24}:00\nCSUD: ${nf(v,2)} €/MWh (${nf(v/1000,4)} €/kWh)\nFixed UTC+1 standard time; in summer add one hour to read Italy's civil clock`;
   svgBody+=`<g ${chartTarget(tip)}><circle cx="${cx}" cy="${cy}" r="${s.bold?3.5:2.5}" fill="${s.color}"/><circle class="chart-hit" cx="${cx}" cy="${cy}" r="6"/></g>`;
  });
 });
 const annSeries=seriesData.find(s=>s.key==='annual');
 const sumSeries=seriesData.find(s=>s.key==='summer');
 const baseAvg=annSeries?.hourly?.length?(annSeries.hourly.reduce((a,b)=>a+b,0)/annSeries.hourly.length):(data.annual_baseload_eur_mwh!=null?data.annual_baseload_eur_mwh:null);
 const middayDip=sumSeries?.hourly?.length?Math.min(...sumSeries.hourly.slice(11,16)):null;
 let marketTakeaway='';
 if(baseAvg!=null&&middayDip!=null){
  marketTakeaway=`<p class="chart-takeaway">Wholesale market takeaway: annual CSUD baseload averages ${nf(baseAvg,1)} €/MWh (${fmt(baseAvg/1000,'eur2')}/kWh); midday summer solar hours dip to ~${nf(middayDip,1)} €/MWh, lowering export value during peak production.</p>`;
 }else if(baseAvg!=null){
  marketTakeaway=`<p class="chart-takeaway">Wholesale market takeaway: annual CSUD baseload averages ${nf(baseAvg,1)} €/MWh (${fmt(baseAvg/1000,'eur2')}/kWh).</p>`;
 }
 chartEl.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="CSUD Day-Ahead hourly price curve by season">${svgBody}</svg>${marketTakeaway}`;
 if(tableTarget){
  const monthNames=['January','February','March','April','May','June','July','August','September','October','November','December'];
  const tableHeaders=['Month',...Array.from({length:24},(_,i)=>`${String(i).padStart(2,'0')}h`),'Month Mean'];
  const tableRows=monthNames.map((mName,mIdx)=>{
   const rowMwh=data.matrix_eur_kwh[mIdx].map(v=>v*1000);
   const mMean=rowMwh.reduce((a,b)=>a+b,0)/24;
   return [mName,...rowMwh.map(v=>nf(v,1)),nf(mMean,1)+' €/MWh'];
  });
  const annualHourly=Array.from({length:24},(_,h)=>{
   const weightedSum=data.matrix_eur_kwh.reduce((acc,row,m)=>acc+row[h]*1000*monthDays[m],0);
   return weightedSum/365.0;
  });
  const totalAnnualMean=annualHourly.reduce((a,b)=>a+b,0)/24;
  tableRows.push(['Annual average',...annualHourly.map(v=>nf(v,1)),nf(totalAnnualMean,1)+' €/MWh']);
  tableTarget.innerHTML=table(tableHeaders,tableRows);
 }
}
let marketCurveInitialized=false;
function initMarketCurve(){
 const winSel=$('marketCurveWindow');
 const activeWin=current?.result?.settings?.export_price_window||settings?.export_price_window||'oct2025_sep2026';
 if(winSel&&!marketCurveInitialized){
  winSel.value=activeWin;
  winSel.onchange=e=>renderMarketCurve(e.target.value);
  marketCurveInitialized=true;
 }
 renderMarketCurve(winSel?.value||activeWin);
}
function computeImpliedTaeg(netCredit,monthly,months){
 if(netCredit<=0||monthly<=0||months<=0)return null;
 const totalRepaid=monthly*months;
 if(totalRepaid<=netCredit)return 0;

 const pvAt=rate=>{
  if(rate<=1e-12)return totalRepaid;
  return (monthly/rate)*(1-Math.pow(1+rate,-months));
 };

 let low=0.0,high=2.0;
 while(pvAt(high)>netCredit&&high<100.0){
  high*=2.0;
 }
 if(pvAt(high)>netCredit)return null;

 for(let i=0;i<60;i++){
  const mid=(low+high)/2;
  const diff=pvAt(mid)-netCredit;
  if(Math.abs(diff)<1e-7){low=mid;high=mid;break;}
  if(diff>0){
   low=mid;
  }else{
   high=mid;
  }
 }
 const monthlyRate=(low+high)/2;
 if(monthlyRate>0&&Number.isFinite(monthlyRate)){
  return Math.pow(1+monthlyRate,12)-1;
 }
 return null;
}
function computeLoanTermCashCoverage(result){
 if(!result||!result.finance||!result.annual_cashflows)return null;
 const f=result.finance,r=result;
 const principal=f.loan_principal_eur||0;
 const termYears=Math.max(1,Number(r.settings?.loan_years||10));
 const horizon=Math.max(1,Number(r.settings?.horizon_years||(r.annual_cashflows.length>1?r.annual_cashflows.length-1:20)));
 const N=Math.min(termYears,horizon);
 const hasPackageCost=(f.conversion_capex_eur>0)||(r.settings?.load_scenario?(r.settings.load_scenario!=='baseline'):(f.loan_scope==='solar_and_conversion'||Boolean(r.metrics?.package_npv_eur&&!r.metrics?.npv_eur)));
 const isSolar=!hasPackageCost;
 const solarLoanPrincipal=f.solar_loan_principal_eur??Math.min(f.capex_eur||0,principal);
 const debtRatio=(isSolar&&principal>0)?(solarLoanPrincipal/principal):1.0;

 let totalOperatingInflow=0,totalBillSavings=0,totalBonusCasa=0,totalOther=0,totalDebtService=0,totalNetCash=0;
 let totalExport=0,totalCer=0,totalOpex=0;

 for(let y=1;y<=N;y++){
  const row=r.annual_cashflows[y]||{};
  const projInflow=isSolar?(row.pv_incremental??0):(row.whole_package??0);
  let billSavings=0;
  if(isSolar){
   billSavings=row.avoided_import??0;
  }else{
   const baseBill=row.baseline_package_bill??((row.baseline_electric_bill??0)+(row.remaining_gas_bill!=null?(row.baseline_gas_bill??0):0));
   const sysElec=row.with_system_electric_bill??0;
   const sysGas=row.remaining_gas_bill??0;
   billSavings=baseBill-sysElec-sysGas;
  }
  const bonusCasa=row.tax_deduction??0;
  const other=projInflow-billSavings-bonusCasa;
  const debt=(row.debt_service??0)*debtRatio;
  const net=projInflow-debt;

  totalOperatingInflow+=projInflow;
  totalBillSavings+=billSavings;
  totalBonusCasa+=bonusCasa;
  totalOther+=other;
  totalDebtService+=debt;
  totalNetCash+=net;
  totalExport+=(row.net_export??0);
  totalCer+=(row.cer??0);
  totalOpex+=((row.maintenance??0)+(row.replacement??0)+(row.hp_maintenance??0)+(row.hp_replacement??0)-(row.residual??0));
 }

 const totalMonths=N*12;
 const monthlyOperatingInflow=totalOperatingInflow/totalMonths;
 const monthlyBillSavings=totalBillSavings/totalMonths;
 const monthlyBonusCasa=totalBonusCasa/totalMonths;
 const monthlyOther=totalOther/totalMonths;
 const monthlyExport=totalExport/totalMonths;
 const monthlyCer=totalCer/totalMonths;
 const monthlyOpex=totalOpex/totalMonths;
 const monthlyDebtService=totalDebtService/totalMonths;
 const monthlyNetCash=totalNetCash/totalMonths;
 const dscr=monthlyDebtService>0?(monthlyOperatingInflow/monthlyDebtService):null;

 return {
  N,
  termYears,
  horizon,
  monthlyOperatingInflow,
  monthlyBillSavings,
  monthlyBonusCasa,
  monthlyOther,
  monthlyExport,
  monthlyCer,
  monthlyOpex,
  monthlyDebtService,
  monthlyNetCash,
  dscr
 };
}
function unleveredPaybackCard(result,hasPackageCost){
 const finance=result.finance;
 if(!(finance.loan_principal_eur>0))return '';
 const scope=hasPackageCost?'package':'solar';
 const unleveredMetrics=getScopeCashMetrics(result,scope,'cash');
 const unleveredPb=unleveredMetrics?.pbObj;
 const leveredMetrics=getScopeCashMetrics(result,scope,'loan');
 const leveredPb=leveredMetrics?.pbObj;

 const uVal=(unleveredPb!=null&&typeof unleveredPb==='object')
  ?(unleveredPb.discounted_payback_of_mean_cash_flows!==undefined?unleveredPb.discounted_payback_of_mean_cash_flows:unleveredPb.median)
  :(typeof unleveredPb==='number'?unleveredPb:null);
 const pVal=(leveredPb!=null&&typeof leveredPb==='object')
  ?(leveredPb.discounted_payback_of_mean_cash_flows!==undefined?leveredPb.discounted_payback_of_mean_cash_flows:leveredPb.median)
  :(typeof leveredPb==='number'?leveredPb:null);

 let accelText='';
 if(uVal!=null&&pVal!=null){
  const diff=uVal-pVal;
  if(diff>0.05){
   accelText=`Loan accelerates discounted cash recovery by ${fmt(diff,'years')} vs 100% cash upfront (${fmt(uVal,'years')}).`;
  }else if(diff<-0.05){
   accelText=`Loan extends discounted recovery by ${fmt(-diff,'years')} vs 100% cash upfront (${fmt(uVal,'years')}).`;
  }else{
   accelText=`Recovery timing matches 100% cash upfront (${fmt(uVal,'years')}).`;
  }
 }else if(pVal!=null&&uVal==null){
  accelText=`100% cash recovery not reached within horizon; loan achieves recovery in ${fmt(pVal,'years')}.`;
 }else if(pVal==null&&uVal!=null){
  accelText=`Personal recovery with loan not reached within horizon; 100% cash recovers in ${fmt(uVal,'years')}.`;
 }else{
  accelText='Neither loan nor cash option reaches persistent recovery within horizon.';
 }

 const title='UNLEVERED CASH PAYBACK (NO LOAN)';
 const detail=`100% cash upfront without borrowing (${fmt(unleveredMetrics?.capexVal||0,'eur')} initial). ${accelText}`;
 return metricCard(title,unleveredPb,'years',detail);
}
function getSavedRecordFinancedFees(c){
 const principal=c.result?.finance?.loan_principal_eur||0;
 if(principal<=0)return 0;
 if(c.result?.finance?.loan_upfront_fees_or_tax_eur!=null)return Number(c.result.finance.loan_upfront_fees_or_tax_eur);
 if(c.settings&&Object.hasOwn(c.settings,'loan_upfront_fees_or_tax')&&c.settings.loan_upfront_fees_or_tax!=null){
  return Number(c.settings.loan_upfront_fees_or_tax);
 }
 return 0;
}
function syncLoanControls(force=false){
 if(!schema)return;
 const put=(id,value)=>{const el=$(id);if(el&&(force||document.activeElement!==el))el.value=value??'';};
 const defAmount=schema.defaults?.loan_amount??14999.72;
 const defApr=schema.defaults?.loan_apr??0.051;
 const defFees=schema.defaults?.loan_upfront_fees_or_tax??16.0;

 const amtVal=settings.loan_amount!=null?Number(settings.loan_amount).toFixed(2):Number(defAmount).toFixed(2);
 const aprVal=(Number(settings.loan_apr??defApr)*100).toFixed(2);
 const feeVal=Number(settings.loan_upfront_fees_or_tax??defFees).toFixed(2);

 put('loanAmountControl',amtVal);
 put('loanModeControl',settings.loan_mode||'amount');
 put('loanFractionControl',(Number(settings.loan_fraction||0)*100).toFixed(1));
 put('loanAprControl',aprVal);
 put('loanYearsControl',settings.loan_years);
 put('loanFeesControl',feeVal);
 put('loanMaxMonthlyPaymentControl',settings.loan_max_monthly_payment!=null?Number(settings.loan_max_monthly_payment).toFixed(2):'');
 put('loanScopeControl',settings.loan_include_electrification?'solar_and_conversion':'solar');
 put('loanInflationControl',(Number(settings.inflation||0)*100).toFixed(2));
 put('loanDiscountControl',(Number(settings.real_discount_rate||0)*100).toFixed(2));
 if($('loanAmountControl'))$('loanAmountControl').max=String(schema.records?.find(r=>r.name==='loan_amount')?.bounds?.[1]??200000);
 if($('loanFractionWrap'))$('loanFractionWrap').hidden=settings.loan_mode!=='fraction';
 if($('loanAmountControl'))$('loanAmountControl').closest('label').hidden=settings.loan_mode==='fraction';
 const ctxText=getLoanScopeContextText(settings,current?.result);
 if($('loanScopeContext'))$('loanScopeContext').textContent=ctxText;
 const switchCtx=document.querySelector?.('[data-switch-context="loan_include_electrification"]');
 if(switchCtx)switchCtx.textContent=ctxText;
}
function loanControlsValid(){return [...document.querySelectorAll('#financing .loan-controls input')].filter(el=>!el.closest('label').hidden).every(el=>el.id==='loanMaxMonthlyPaymentControl'&&el.value===''||el.value!==''&&el.validity.valid);}
function currentLoanMetrics(result){
 const discSource=getDiscountingCashflows(result)||result?.finance?.cashflows_by_weather;
 const flowKey=['equity','package_equity','solar_equity'].find(key=>discSource?.[key]?.length);
 const paths=discSource?.[flowKey]||[];
 if(!paths.length)return null;
 const real=result.settings?.cashflow_mode==='real',inflation=result.settings?.inflation||0,rate=real?(result.finance?.real_discount_rate??result.settings?.real_discount_rate??0):(result.finance?.nominal_discount_rate??((1+(result.settings?.real_discount_rate||0))*(1+inflation)-1));
 const discounted=paths.map(path=>{let total=0;return path.map((amount,year)=>{total+=amount/(real?Math.pow(1+inflation,year):1)/Math.pow(1+rate,year);return total;});});
 const mean=values=>values.length?values.reduce((s,v)=>s+v,0)/values.length:0;
 const horizon=Math.min(...discounted.map(p=>p.length-1));
 const f=result.finance;
 let recovery=null;
 if(f?.[flowKey]?.discounted_payback_of_mean_cash_flows!==undefined){
  recovery=f[flowKey].discounted_payback_of_mean_cash_flows;
 }else{
  const meanCum=[];
  for(let y=0;y<=horizon;y++){
   const vals=discounted.map(p=>p[y]);
   meanCum.push(vals.reduce((s,v)=>s+v,0)/vals.length);
  }
  for(let y=0;y<=horizon;y++){
   if(meanCum[y]<-1e-8||meanCum.slice(y).some(value=>value<-1e-8))continue;
   recovery=y===0?0:(meanCum[y-1]<0&&meanCum[y]>meanCum[y-1]?y-1+(-meanCum[y-1])/(meanCum[y]-meanCum[y-1]):y);
   break;
  }
 }
 return {initial:Math.max(0,-mean(discounted.map(p=>p[0]))),year20:horizon>=20?mean(discounted.map(p=>p[20])):null,recovery,horizon,stat:'mean'};
}
function renderLoanPanel(){
 if(!current||!$('loanCashChart'))return;
 syncLoanControls();
 const r=current.result,f=r.finance;
 const principal=f.loan_principal_eur||0;
 const borrowingActive=principal>0;
 const rate=Number(r.settings?.loan_apr??0.051);
 const months=(Number(r.settings?.loan_years)||1)*12;
 const fees=borrowingActive?(f.loan_upfront_fees_or_tax_eur??(r.settings&&Object.hasOwn(r.settings,'loan_upfront_fees_or_tax')&&r.settings.loan_upfront_fees_or_tax!=null?Number(r.settings.loan_upfront_fees_or_tax):0)):0;
 const amortizedPrincipal=borrowingActive?(f.loan_amortized_principal_eur??(principal+fees)):0;
 const monthly=borrowingActive?(f.loan_monthly_payment_eur??(rate===0?amortizedPrincipal/months:amortizedPrincipal*(rate/12)/(1-Math.pow(1+rate/12,-months)))):0;
 const totalRepaid=borrowingActive?(f.loan_total_repaid_eur??(monthly*months)):0;
 const totalInterest=borrowingActive?(f.loan_total_interest_eur??Math.max(0,totalRepaid-amortizedPrincipal)):0;
 const totalNominalCost=borrowingActive?(totalInterest+fees):0;
 const realInterest=borrowingActive?(f.loan_real_interest_eur??null):0;
 const taeg=borrowingActive?(f.loan_implied_taeg??computeImpliedTaeg(principal,monthly,months)):null;
 const capped=f.requested_loan_amount_eur>principal+.01;
 const maxPayment=r.settings?.loan_max_monthly_payment;
 const overBudget=maxPayment!=null&&monthly>maxPayment+.000001;

 const hasPackageCost=(f.conversion_capex_eur>0)||(r.settings?.load_scenario?(r.settings.load_scenario!=='baseline'):(f.loan_scope==='solar_and_conversion'||Boolean(r.metrics?.package_npv_eur&&!r.metrics?.npv_eur)));
 const summaryScopeToUse=hasPackageCost?'package':'solar';
 const unleveredMetrics=getScopeCashMetrics(r,summaryScopeToUse,'cash');
 const leveredMetrics=getScopeCashMetrics(r,summaryScopeToUse,'loan');
 const unleveredNpv=typeof unleveredMetrics?.npvObj==='number'?unleveredMetrics.npvObj:(unleveredMetrics?.npvObj?.mean??unleveredMetrics?.npvObj?.median??null);
 const leveredNpv=typeof leveredMetrics?.npvObj==='number'?leveredMetrics.npvObj:(leveredMetrics?.npvObj?.mean??leveredMetrics?.npvObj?.median??null);

 let npvDisplay='—',npvColor='var(--text-main)',card4Subtitle='NPV calculation unavailable';
 if(unleveredNpv!=null&&leveredNpv!=null){
  const npvImpact=borrowingActive?(leveredNpv-unleveredNpv):0;
  const npvImpactRounded=Math.round(npvImpact);
  const npvBenefit=npvImpact>0.5;
  const npvCost=npvImpact<-0.5;
  npvColor=npvBenefit?'var(--green)':(npvCost?'var(--loan-cost,#b45309)':'var(--text-main)');
  npvDisplay=npvCost?`−€${nf(Math.abs(npvImpactRounded))}`:(npvBenefit?`+€${nf(npvImpactRounded)}`:'€0');

  const realRate=Number(r.settings?.real_discount_rate??f.real_discount_rate??0);
  const inflRate=Number(r.settings?.inflation??0);
  const nomRate=Number(f.nominal_discount_rate??((1+realRate)*(1+inflRate)-1));
  const ratesDesc=`${(realRate*100).toFixed(2)}% real · ${(inflRate*100).toFixed(2)}% infl · ${(nomRate*100).toFixed(2)}% nom`;
  const statusText=borrowingActive
   ?(npvBenefit?'Positive net benefit vs paying cash':(npvCost?'Net PV cost vs paying cash':'Zero net PV impact vs paying cash'))
   :'Zero loan: €0 impact vs paying cash';
  card4Subtitle=`ΔNPV = Levered NPV − Unlevered cash NPV · ${statusText} · ${ratesDesc}`;
 }

 const coverage=computeLoanTermCashCoverage(r);
 const monthlyNetCash=coverage?.monthlyNetCash??0;
 const avgWindowN=coverage?.N??Math.min(Number(r.settings?.loan_years||10),Number(r.settings?.horizon_years||20));

 const annualRepayment=monthly*12;
 const tanStr=(rate*100).toFixed(2)+'% TAN';
 const taegStr=taeg!=null?(Number(taeg)*100).toFixed(2)+'% TAEG':'— TAEG';
 const card1Subtitle=borrowingActive
  ? `${fmt(annualRepayment,'eur2')}/yr nominal · Credit ${fmt(principal,'eur2')} + fee ${fmt(fees,'eur2')} · ${nf(r.settings.loan_years)}y · ${tanStr} / ${taegStr}`
  : `€0.00/yr · No loan active · 0y · ${tanStr}`;

 const otherParts=[];
 const monthlyExportCer=(coverage?.monthlyExport||0)+(coverage?.monthlyCer||0);
 if(Math.abs(monthlyExportCer)>=0.005){
  const expLabel=(Math.abs(coverage.monthlyCer)>=0.005&&Math.abs(coverage.monthlyExport)>=0.005)?'export/CER':(Math.abs(coverage.monthlyCer)>=0.005?'CER':'export');
  otherParts.push(`${monthlyExportCer>=0?'+':'−'} ${fmt(Math.abs(monthlyExportCer),'eur2')} ${expLabel}`);
 }
 let netOpex=coverage?.monthlyOpex||0;
 if(coverage?.N&&r.annual_cashflows){
  let allowance=0;for(let y=1;y<=coverage.N;y++)allowance+=(r.annual_cashflows[y]?.unserved_service_allowance||0);
  netOpex+=allowance/(coverage.N*12);
 }
 if(Math.abs(netOpex)>=0.005){
  otherParts.push(`${netOpex<=0?'+':'−'} ${fmt(Math.abs(netOpex),'eur2')} ${netOpex<-0.005?'O&M credit':'O&M'}`);
 }
 const remResidual=(coverage?.monthlyOther||0)-(monthlyExportCer-netOpex);
 if(Math.abs(remResidual)>=0.005){
  otherParts.push(`${remResidual>=0?'+':'−'} ${fmt(Math.abs(remResidual),'eur2')} operating adjustments`);
 }
 const otherDesc=otherParts.length?` ${otherParts.join(' ')}`:'';

 const card2Subtitle=coverage
  ? `Nominal avg: ${fmt(coverage.monthlyOperatingInflow,'eur2')}/mo inflows (${fmt(coverage.monthlyBillSavings,'eur2')} bills + ${fmt(coverage.monthlyBonusCasa,'eur2')} Bonus Casa${otherDesc}) vs ${fmt(coverage.monthlyDebtService,'eur2')} loan · ${coverage.dscr!=null?coverage.dscr.toFixed(2)+'x DSCR':'—'}`
  : `Years 1–${avgWindowN} avg net cash`;

 const card3Subtitle=borrowingActive
  ? `${fmt(totalInterest,'eur2')} interest + ${fmt(fees,'eur2')} fee · Repaid ${fmt(totalRepaid,'eur2')}${realInterest!=null?` · Interest in real Year-0 €: ${fmt(realInterest,'eur2')}`:''}`
  : 'Zero financing cost · €0 interest · €0 fee';

 $('loanPayment').innerHTML=`
   <div class="loan-outcomes-grid">
     <div class="card"><div class="label">MONTHLY PAYMENT</div><div class="value">${fmt(monthly,'eur2')} <small>/ month</small></div><small>${esc(card1Subtitle)}</small></div>
     <div class="card"><div class="label">MONTHLY CASH-FLOW COVERAGE · YRS 1–${avgWindowN}</div><div class="value">${monthlyNetCash>=0?'+':''}${fmt(monthlyNetCash,'eur2')} <small>/ month</small></div><small>${esc(card2Subtitle)}</small></div>
     <div class="card"><div class="label">TOTAL NOMINAL LOAN COST</div><div class="value">${fmt(totalNominalCost,'eur2')}</div><small>${esc(card3Subtitle)}</small></div>
     <div class="card"><div class="label">FINANCING NPV IMPACT</div><div class="value" style="color:${npvColor};">${npvDisplay}</div><small>${esc(card4Subtitle)}</small></div>
   </div>
   <p class="chart-takeaway">Financing takeaway: borrowing ${fmt(principal,'eur2')}${fees>0?` (+ ${fmt(fees,'eur2')} fee)`:''} requires ${fmt(monthly,'eur2')}/month for ${nf(r.settings.loan_years)} years (total interest ${fmt(totalInterest,'eur2')}); loan-term monthly net cash averages ${monthlyNetCash>=0?'+':''}${fmt(monthlyNetCash,'eur2')}/month; financing NPV impact vs paying cash is ${npvDisplay}.</p>
   ${capped?`<p class="bad" style="margin-top:6px;">Requested loan capped at eligible investment cost (${fmt(principal,'eur')}).</p>`:''}
   ${maxPayment!=null?`<p class="muted" style="margin-top:4px;">Monthly payment limit: ${fmt(maxPayment,'eur2')}${overBudget?' · <span class="bad">Current payment exceeds this budget constraint!</span>':''}</p>`:''}
 `;

 const reconEl=$('loanReconciliation');
 if(reconEl){
  if(borrowingActive){
   reconEl.innerHTML=`<strong>Contract Reconciliation Summary</strong><div class="loan-reconciliation-strip"><span><strong>Credit received:</strong> ${fmt(principal,'eur2')}</span><span class="muted">+</span><span><strong>Financed tax:</strong> ${fmt(fees,'eur2')}</span><span class="muted">=</span><span><strong>Amortized principal:</strong> ${fmt(amortizedPrincipal,'eur2')}</span><span class="muted">→</span><span>${months} × ${fmt(monthly,'eur2')}</span><span class="muted">=</span><span><strong>Total repaid:</strong> ${fmt(totalRepaid,'eur2')}</span><span class="muted">(${fmt(totalInterest,'eur2')} interest · ${tanStr} / ${taegStr})</span></div>`;
  }else{
   reconEl.innerHTML='';
  }
 }

 $('loanAssumptions').textContent=(r.settings.loan_mode==='fraction'?nf((r.settings.loan_fraction||0)*100,1)+'% of ':'')+(f.loan_scope==='solar_and_conversion'?'eligible solar + electrification':'eligible solar')+' cost · nominal TAN divided by 12 · '+(fees>0?`upfront fees of ${fmt(fees,'eur2')} financed · `:'no fees modeled · ')+'repayments monthly · monthly repayments discounted at monthly resolution · remaining debt settled at the simulation horizon. The solver limit applies to the scheduled monthly instalment; if the term extends past the simulation horizon, settlement remains modeled there.';
 renderCashChart(r,'loanCashChart',hasPackageCost?'package_equity':'solar_equity',loanCurveBasis);
 renderLoanPivot();
 if($('loanMetricCards'))$('loanMetricCards').innerHTML=personalPaybackCard(r)+unleveredPaybackCard(r,hasPackageCost);
 $('saveLoanSimulation').disabled=loanPending||!loanControlsValid()||acceptedInputFingerprint!==inputFingerprint();
 $('solveLoanAmount').disabled=loanPending||!loanControlsValid()||acceptedInputFingerprint!==inputFingerprint();
}
function renderLoanPivot(){
 const now=currentLoanMetrics(current?.result),cards=[{id:'current',name:'Current inputs',metrics:now,settings:current?.result?.settings,result:current?.result}].concat(loanSaved.map((s,i)=>{
  let m=null;
  if(s.result){
   m=currentLoanMetrics(s.result);
  }else if(s.metrics){
   m=s.metrics.stat==='mean'?s.metrics:{...s.metrics,stat:'legacy_median'};
  }
  return {id:String(i),name:s.name,metrics:m,settings:s.settings,result:s.result,saved:true};
 }));
 if(!now){$('loanPivot').textContent='Loan comparison will appear after the scenario finishes calculating.';return;}
 const val=(m,key)=>{
  if(!m)return '—';
  const diag=m.stat==='legacy_median'?' (legacy median)':'';
  if(key==='initial')return fmt(m.initial,'eur')+diag;
  if(key==='year20')return (m.year20==null?'Not simulated (horizon '+nf(m.horizon)+' y)':((m.year20>=0?'+':'')+fmt(m.year20,'eur')))+diag;
  return (m.recovery==null?'Not reached':fmt(m.recovery,'years'))+diag;
 };
 const money=c=>fmt(c.result?.finance?.loan_principal_eur||0,'eur');

 const isComparable=c=>c.metrics?.stat!=='legacy_median'&&Math.abs((c.result?.layout?.kwp||0)-(current?.result?.layout?.kwp||0))<=0.1&&c.settings?.load_scenario===current?.result?.settings?.load_scenario&&(c.metrics?.horizon===now.horizon||c.settings?.horizon_years===current?.result?.settings?.horizon_years);
 const comparableCards=cards.filter(isComparable);
 const bestInitial=Math.min(...comparableCards.map(c=>c.metrics?.initial).filter(v=>v!=null&&Number.isFinite(v)));
 const bestYear20=Math.max(...comparableCards.map(c=>c.metrics?.year20).filter(v=>v!=null&&Number.isFinite(v)));
 const validRecoveries=comparableCards.map(c=>c.metrics?.recovery).filter(v=>v!=null&&Number.isFinite(v));
 const bestRecovery=validRecoveries.length?Math.min(...validRecoveries):null;

 const getNetCashValue=c=>computeLoanTermCashCoverage(c.result)?.monthlyNetCash;
 const validNetCashes=comparableCards.map(getNetCashValue).filter(v=>v!=null&&Number.isFinite(v));
 const bestNetCash=validNetCashes.length?Math.max(...validNetCashes):null;

 const isBestInitial=c=>isComparable(c)&&c.metrics?.initial!=null&&Math.abs(c.metrics.initial-bestInitial)<0.01;
 const isBestYear20=c=>isComparable(c)&&c.metrics?.year20!=null&&Math.abs(c.metrics.year20-bestYear20)<0.01;
 const isBestRecovery=c=>isComparable(c)&&bestRecovery!=null&&c.metrics?.recovery!=null&&Math.abs(c.metrics.recovery-bestRecovery)<0.01;
 const isBestNetCash=c=>{const v=getNetCashValue(c);return isComparable(c)&&bestNetCash!=null&&v!=null&&Math.abs(v-bestNetCash)<0.01;};

 const getMonthlyPayment=c=>{
  const f=c.result?.finance;
  const principal=f?.loan_principal_eur||0;
  if(principal<=0)return 0;
  if(f?.loan_monthly_payment_eur!=null)return f.loan_monthly_payment_eur;
  const fees=getSavedRecordFinancedFees(c);
  const amortized=f?.loan_amortized_principal_eur!=null?f.loan_amortized_principal_eur:(principal+fees);
  const rate=Number(c.settings?.loan_apr||0);
  const months=(Number(c.settings?.loan_years)||1)*12;
  return rate===0?amortized/months:amortized*(rate/12)/(1-Math.pow(1+rate/12,-months));
 };

 const getTanTaeg=c=>{
  const f=c.result?.finance;
  const principal=f?.loan_principal_eur||0;
  if(principal<=0)return '—';
  const tanVal=Number(c.settings?.loan_apr||0);
  const tanStr=(tanVal*100).toFixed(2)+'% TAN';
  const taegVal=f?.loan_implied_taeg;
  if(taegVal!=null)return `${tanStr} / ${(Number(taegVal)*100).toFixed(2)}% TAEG`;
  const m=getMonthlyPayment(c),months=(Number(c.settings?.loan_years)||1)*12;
  const implied=computeImpliedTaeg(principal,m,months);
  if(implied!=null)return `${tanStr} / ${(Number(implied)*100).toFixed(2)}% TAEG`;
  return tanStr;
 };

 const cardWindows=cards.map(c=>computeLoanTermCashCoverage(c.result)?.N).filter(n=>n!=null);
 const uniqueWindows=[...new Set(cardWindows)];
 const hasDifferingWindows=uniqueWindows.length>1;
 const defaultWindow=cardWindows[0]||10;
 const netCashRowLabel=hasDifferingWindows?'AVG MONTHLY NET CASH':`YEARS 1–${defaultWindow} AVG MONTHLY NET CASH`;

 const rows=[
  ['Initial cash',c=>val(c.metrics,'initial'),isBestInitial],
  ['At year 20 · discounted cash impact',c=>val(c.metrics,'year20'),isBestYear20],
  ['Discounted cash recovery',c=>val(c.metrics,'recovery'),isBestRecovery],
  ['Loan principal',money],
  ['FINANCED TAX / FEES',c=>fmt(getSavedRecordFinancedFees(c),'eur2')],
  ['MONTHLY PAYMENT',c=>{const m=getMonthlyPayment(c);return m>0?fmt(m,'eur2')+' / mo':'—';}],
  [netCashRowLabel,c=>{
   const cov=computeLoanTermCashCoverage(c.result);
   const nc=cov?.monthlyNetCash;
   if(nc==null)return '—';
   const valStr=`${nc>=0?'+':''}${fmt(nc,'eur2')} / mo`;
   return hasDifferingWindows?`${valStr} (Years 1–${cov.N})`:valStr;
  },isBestNetCash],
  ['PV capacity',c=>c.result?.layout?.kwp==null?'—':nf(c.result.layout.kwp,1)+' kWp'],
  ['Battery',c=>c.settings?.battery_kwh==null?'—':nf(c.settings.battery_kwh,1)+' kWh'],
  ['Inverter',c=>c.settings?.inverter_kw==null?'—':nf(c.settings.inverter_kw,2)+' kW'],
  ['Solar investment',c=>fmt(c.result?.finance?.capex_eur,'eur')],
  ['Household',c=>({baseline:'Historic electricity',electrified:'Gas eliminated',ev:'Gas eliminated + EV'}[c.settings?.load_scenario]||c.settings?.load_scenario||'—')],
  ['TAN / TAEG',getTanTaeg],
  ['Repayment term',c=>nf(c.settings?.loan_years)+' years'],
  ['Maximum monthly payment',c=>c.settings?.loan_max_monthly_payment==null?'No limit':fmt(c.settings.loan_max_monthly_payment,'eur2')],
  ['Loan scope',c=>c.settings?.loan_include_electrification?'Solar + electrification':'Solar'],
  ['Inflation',c=>fmt(c.settings?.inflation,'pct')],
  ['Real discount rate',c=>fmt(c.settings?.real_discount_rate,'pct')],
  ['Simulation horizon',c=>nf(c.metrics?.horizon)+' years']
 ];
 let html='<table class="loan-pivot"><thead><tr><th>Measure</th>'+cards.map(c=>'<th>'+esc(c.name)+(c.saved?'<br><button type="button" data-load-loan="'+esc(c.id)+'">Load</button> <button type="button" class="quiet" data-remove-loan="'+esc(c.id)+'" aria-label="Remove '+esc(c.name)+'">Remove</button>':'')+'</th>').join('')+'</tr></thead><tbody>'+rows.map(([label,show,isBest])=>'<tr><th>'+label+'</th>'+cards.map(c=>{
   const cls=(isBest&&isBest(c)&&cards.length>1)?' class="best-metric"':'';
   return '<td'+cls+'>'+esc(show(c))+'</td>';
 }).join('')+'</tr>').join('')+'</tbody></table>';
 $('loanPivot').innerHTML=html;
 $('loanPivot').onclick=e=>{
  const load=e.target.closest('[data-load-loan]'),remove=e.target.closest('[data-remove-loan]');
  if(load){
   const s=loanSaved[Number(load.dataset.loadLoan)];
   if(!s)return;
   const rawSettings=deepClone(s.settings||{});
   if(!Object.hasOwn(rawSettings,'loan_upfront_fees_or_tax')){
    rawSettings.loan_upfront_fees_or_tax=getSavedRecordFinancedFees(s);
   }
   const nextSettings=prepareScenarioSettings(rawSettings,false);
   if(!Object.hasOwn(s.settings||{},'battery_mixed_sizes')){
    nextSettings.battery_mixed_sizes=false;
    nextSettings.battery_secondary_modules=0;
   }
   normalizeBatterySettings(nextSettings,Object.hasOwn(s.settings,'battery_kwh')?'battery_kwh':'battery_modules');
   const nextQuote=deepClone(s.quote||{line_items:[]});
   discardAcDrafts();
   settings=nextSettings;
   quote=nextQuote;
   renderControls();renderLines();syncQuoteFields();syncLoanControls(true);schedule({replace:true});
  }
  if(remove){
   loanSaved.splice(Number(remove.dataset.removeLoan),1);
   persistLoanSimulations();
   renderLoanPivot();
  }
 };
}
function persistLoanSimulations(){try{localStorage.setItem('pv-lab-loan-simulations-v1',JSON.stringify(loanSaved));return true;}catch(error){$('loanSaveStatus').textContent='Could not save this simulation in this browser: '+(error.message||error);return false;}}
function table(headers,rows){return `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
function chartSvg(label,body,height=285,width=720){return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}">${body}</svg>`;}
function chartAxis(max,height=220,right=704){const y=v=>height-v/(max||1)*(height-25);return {y,body:[0,.25,.5,.75,1].map(f=>`<line x1="58" x2="${right}" y1="${y(max*f)}" y2="${y(max*f)}" stroke="#e0e7df"/><text x="50" y="${y(max*f)+4}" text-anchor="end" font-size="11" fill="#62756e">${nf(max*f)}</text>`).join('')};}
function chartDetail(label,period,band,unit='kWh',note='Historical weather range'){
 return `${label} · ${period}\nMedian: ${nf(band.median,2)} ${unit}\nWeather min: ${nf(band.min,2)} ${unit}\nWeather max: ${nf(band.max,2)} ${unit}\n${note}`;
}
function chartTarget(detail){return `class="chart-point" tabindex="0" role="img" aria-label="${esc(detail)}" data-chart-detail="${esc(detail)}"`;}
function bandMark(x,band,y,color,label,width=16,period='First year',hitPadding=2,note='Historical weather range'){
 const top=y(band.max),bottom=y(0),detail=chartDetail(label,period,band,'kWh',note);
 return `<g ${chartTarget(detail)}><rect x="${x-width/2}" y="${y(band.median)}" width="${width}" height="${Math.max(0,bottom-y(band.median))}" fill="${color}" opacity=".8"/><path d="M${x},${y(band.min)} V${y(band.max)} M${x-4},${y(band.min)} H${x+4} M${x-4},${y(band.max)} H${x+4}" stroke="${color}" stroke-width="2"/><rect class="chart-hit" x="${x-width/2-hitPadding}" y="${Math.min(top,bottom-8)-3}" width="${width+hitPadding*2}" height="${Math.max(8,bottom-top)+6}"/></g>`;
}
function measuredDetail(period,band){return `Current measured electricity · ${period}\nRecorded: ${nf(band.median,2)} kWh\nHousehold meter reference`;}
function renderEnergyCharts(){const c=current.charts;if(!c)return;const config=current.result;
 hideChartTooltip();
 $('consumptionContext').textContent=`All three scenarios use your selected ${nf(config.layout.kwp,1)} kWp panels, ${nf(config.settings.inverter_kw,2)} kW inverter and ${nf(config.settings.battery_kwh,1)} kWh usable battery, with identical shading and dispatch settings. First year · ${c.weather_count} weather years. The measured reference is the recorded household electricity; without PV the historical scenario's imports equal demand at load growth 1. Your load multiplier is ${nf(c.load_growth,2)}.${c.summer_ac_multiplier&&c.summer_ac_multiplier>1?` Summer AC multiplier is ${nf(c.summer_ac_multiplier,2)} (+${nf((c.summer_ac_multiplier-1)*100,0)}% cooling).`:''}${c.ev_active?' EV charging is included in year one.':` EV charging starts in year ${c.ev_start_year}; it is absent from these first-year results.`}`;
 const series=[['load_kwh','#bd8334','Gross demand'],['grid_import_kwh','#217a61','Grid imports'],['pv_kwh','#6285bd','PV production (AC)'],['export_kwh','#9865a8','Grid exports (AC)'],['discharge_kwh','#df8a54','Battery delivered to loads']];
 const annualMax=Math.max(c.measured.annual.max,...c.scenarios.flatMap(s=>series.map(([key])=>s.series[key].annual.max)))*1.12;const a=chartAxis(annualMax);
 let body=a.body;
 c.scenarios.forEach((s,i)=>{const x=170+i*212;series.forEach(([key,color,label],j)=>{const step=32,barWidth=24,note=key==='discharge_kwh'?'Delivered to loads; already reflected in lower grid imports.':'Historical weather range';body+=bandMark(x+(j-(series.length-1)/2)*step,s.series[key].annual,a.y,color,`${s.label} · ${label}`,barWidth,'First year · annual',2,note);});body+=`<text x="${x}" y="246" text-anchor="middle" font-size="12">${esc(s.label)}</text>`;});
 const measuredY=a.y(c.measured.annual.median);body+=`<line x1="58" x2="704" y1="${measuredY}" y2="${measuredY}" stroke="#7b827c" stroke-dasharray="5 4" pointer-events="none"/><g ${chartTarget(measuredDetail('Annual',c.measured.annual))}><circle cx="67" cy="${measuredY}" r="2.5" fill="#7b827c"/><circle class="chart-hit" cx="67" cy="${measuredY}" r="3.5"/></g>`;
 $('annualConsumption').innerHTML=chartSvg('Annual gross electricity demand, grid imports, AC PV production, grid exports and battery energy delivered to household loads, compared with measured consumption',body,265);
 const monthlyMax=Math.max(...c.measured.monthly.map(m=>m.max),...c.scenarios.flatMap(s=>series.flatMap(([key])=>s.series[key].monthly.map(m=>m.max))))*1.1;const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
 $('monthlyConsumption').innerHTML=c.scenarios.map(s=>{const a=chartAxis(monthlyMax,190,864),x=i=>100+i*64,measuredX=i=>x(i)-30;let body=a.body+`<polyline points="${c.measured.monthly.map((m,i)=>`${measuredX(i)},${a.y(m.median)}`).join(' ')}" fill="none" stroke="#7b827c" stroke-width="2" stroke-dasharray="4 3" pointer-events="none"/>`;
 months.forEach((m,i)=>{series.forEach(([key,color,label],j)=>{const note=key==='discharge_kwh'?'Delivered to loads; already reflected in lower grid imports.':'Historical weather range';body+=bandMark(x(i)+(j-(series.length-1)/2)*11,s.series[key].monthly[i],a.y,color,`${s.label} · ${label}`,8,`First year · ${m}`,1,note);});body+=`<g ${chartTarget(measuredDetail(`${s.label} · ${m}`,c.measured.monthly[i]))}><circle cx="${measuredX(i)}" cy="${a.y(c.measured.monthly[i].median)}" r="1.8" fill="#7b827c"/><circle class="chart-hit" cx="${measuredX(i)}" cy="${a.y(c.measured.monthly[i].median)}" r="2.5"/></g><text x="${x(i)}" y="213" text-anchor="middle" font-size="11">${m}</text>`;});return `<div class="monthly-panel"><h3>${esc(s.label)}</h3><div class="chart">${chartSvg(s.label+' monthly demand, grid imports, AC PV production, grid exports and battery energy delivered to household loads; same vertical scale across scenarios',body,228,880)}</div></div>`;}).join('');
 $('consumptionTable').innerHTML=table(['Scenario / period','Measured kWh','Gross demand kWh','Grid imports kWh','PV generation AC kWh','Grid exports AC kWh','Battery delivered to loads kWh'],c.scenarios.flatMap(s=>[[s.label+' · annual',range(c.measured.annual),range(s.series.load_kwh.annual),range(s.series.grid_import_kwh.annual),range(s.series.pv_kwh.annual),range(s.series.export_kwh.annual),range(s.series.discharge_kwh.annual)],...months.map((m,i)=>[s.label+' · '+m,range(c.measured.monthly[i]),range(s.series.load_kwh.monthly[i]),range(s.series.grid_import_kwh.monthly[i]),range(s.series.pv_kwh.monthly[i]),range(s.series.export_kwh.monthly[i]),range(s.series.discharge_kwh.monthly[i])])]))+`<p class="muted">Battery delivered is the AC energy that the battery supplies to household loads. It is already reflected in lower grid imports and is not additional generation. Annual ranges are computed from each complete weather year. Summing monthly range endpoints would combine different weather years and is not an annual range.</p>`;
 $('roofModelContext').textContent=`kWh/kWp/year · ${c.weather_count} historical weather years. Green is the selected hourly PVGIS input (shade strength ${nf(c.shade_strength,2)}, diffuse suppression ${nf(c.diffuse_suppression,2)}). At default shading it matches the beam-shaded series. These normalized roof yields precede selected system clipping and first-year PV degradation.`;
 const keys=[['selected_pvgis_ac','#217a61','Selected PVGIS AC'],['pvgis_beam_shaded','#7eac93','Default beam-shaded AC'],['pvgis_all_light_shaded','#a3a9ad','All-light-shaded AC'],['google_dc_kwh_kwp','#bd8334','Google annual DC'],['google_ac_kwh_kwp','#a2739a','Illustrative Google AC bridge']];const roofMax=Math.max(...c.roofs.flatMap(r=>keys.map(([key])=>r[key]!=null&&typeof r[key]==='object'?r[key].max:r[key]||0)))*1.1;const rx=v=>140+v/(roofMax||1)*545;let roofBody=[0,.25,.5,.75,1].map(f=>`<line x1="${rx(roofMax*f)}" x2="${rx(roofMax*f)}" y1="25" y2="${c.roofs.length*100+15}" stroke="#e0e7df"/><text x="${rx(roofMax*f)}" y="${c.roofs.length*100+38}" text-anchor="middle" font-size="11">${nf(roofMax*f)}</text>`).join('');
 c.roofs.forEach((r,i)=>{const top=40+i*100;roofBody+=`<text x="128" y="${top+20}" text-anchor="end" font-size="12">${esc(title(r.orientation))}</text>`;keys.forEach(([key,color,label],j)=>{const v=r[key],y=top+j*14;if(v==null||(typeof v==='object'&&(v.min==null||v.median==null||v.max==null))){roofBody+=`<text x="${rx(0)+8}" y="${y+4}" font-size="10" fill="${color}">Unavailable · ${esc(label)}</text>`;return;}const band=typeof v==='object',value=band?v.median:v,detail=band?chartDetail(`${title(r.orientation)} · ${label}`,'Annual normalized roof yield',v,'kWh/kWp/year','Before system clipping and first-year degradation'):`${title(r.orientation)} · ${label}\nAnnual: ${nf(value,2)} kWh/kWp/year\nGoogle source comparison only; does not drive production`;
 roofBody+=`<g ${chartTarget(detail)}>${band?`<line x1="${rx(v.min)}" x2="${rx(v.max)}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="${j===0?7:3}"/>`:''}<circle cx="${rx(value)}" cy="${y}" r="${j===0?4:3}" fill="${color}"/><rect class="chart-hit" x="${rx(band?v.min:value)-6}" y="${y-6}" width="${Math.max(12,rx(band?v.max:value)-rx(band?v.min:value)+12)}" height="12"/></g>`;});});$('roofChart').innerHTML=chartSvg('Roof yields in kWh per kWp per year: selected PVGIS input, shading alternatives and comparison-only Google values',roofBody,c.roofs.length*100+55);
}

const DAYHOUR_MONTH_NAMES=['January','February','March','April','May','June','July','August','September','October','November','December'];
function dayhourMonthName(m){return DAYHOUR_MONTH_NAMES[(m-1)%12]||'';}
function syncWeatherDateToMonth(){const monthSelect=$('dayhourMonth');if(!monthSelect)return;const month=Number(monthSelect.value)||1;const dateInput=$('weatherDate');if(!dateInput)return;const fy=getForecastYear();const currentVal=dateInput.value||`${fy}-01-01`;const parts=currentVal.split('-');const year=$('dayhourMode')?.value==='forecast_2027'?fy:(Number($('weatherYear')?.value)||Number(parts[0])||fy);const oldDay=Number(parts[2])||1;const maxD=maxDaysInMonth(year,month);const clampedDay=Math.min(Math.max(1,oldDay),maxD);dateInput.value=`${year}-${String(month).padStart(2,'0')}-${String(clampedDay).padStart(2,'0')}`;}
function syncMonthFromWeatherDate(){const dateInput=$('weatherDate'),monthSelect=$('dayhourMonth');if(!dateInput||!monthSelect)return;const parts=(dateInput.value||'').split('-');if(parts.length>=2){const m=Number(parts[1]);if(m>=1&&m<=12)monthSelect.value=String(m);}}
function hourLabel(hour){return `${String(hour).padStart(2,'0')}:00–${String((hour+1)%24).padStart(2,'0')}:00`+(hour===23?' (next day)':'');}
function selectedDayHour(){const hour=Number($('hourSlider').value);$('selectedHourLabel').textContent=hourLabel(hour);return hour;}
function dayMetric(label,value,unit='kWh',detail=''){return `<div class="card"><div class="label">${esc(label)}</div><div class="value">${typeof value==='number'?nf(value,2):esc(value)} <small>${unit}</small></div>${detail?`<small>${esc(detail)}</small>`:''}</div>`;}
function renderDayhour(data){
 dayhourData=data;
 if(data.period&&typeof $==='function'&&$('dayhourPeriod'))$('dayhourPeriod').value=data.period;
 if(data.month!=null&&typeof $==='function'&&$('dayhourMonth'))$('dayhourMonth').value=String(data.month);
 if(data.mode&&typeof $==='function'&&$('dayhourMode'))$('dayhourMode').value=data.mode;
 if(data.scenario&&typeof $==='function'&&$('dayhourScenario'))$('dayhourScenario').value=data.scenario;
 if(isDayhourMatching(data)){
  dayhourOutdated=false;
 }else{
  dayhourOutdated=true;
 }
 const width=1020,height=320,left=66,right=1004,top=18,bottom=274,cell=(right-left)/24,plotHeight=bottom-top;
 const dhCoupling=(data.selected_config?.pv_battery_coupling||(typeof current!=='undefined'&&current?.result?.settings?.pv_battery_coupling)||(typeof settings!=='undefined'&&settings?.pv_battery_coupling))||'ac_equivalent';
 const chargePhase=typeof chargeBusPhaseLabel==='function'?chargeBusPhaseLabel(dhCoupling):(dhCoupling==='dc_coupled'?'DC':'AC');
 const chargeBusLbl=typeof chargeBusLabel==='function'?chargeBusLabel(dhCoupling):`${chargePhase} battery charge input`;
 const chargeBusUnit=`kWh ${chargePhase}`;
 const isMonthly=data.period==='month';
 const isEnsemble=data.aggregation==='weather_mean'||data.representative==='mean';
 const weatherCount=data.weather_count||getWeatherEnsembleMeta(data).count;
 const hour=selectedDayHour(),row=data.hours.find(x=>x.hour===hour);
 const monthTitle=data.month_name||dayhourMonthName(data.month||1);
 const timeYear=data.year||(data.mode==='forecast_2027'?getForecastYear():data.weather_year);
 if(isMonthly){
  if(data.mode==='forecast_2027'){
   if(isEnsemble){
    $('dayhourStatus').textContent=`${monthTitle} ${timeYear} monthly average · ${dayhourScenarioLabel(data.scenario, data.settings)} · ${weatherCount}-year arithmetic mean with P10–P90 historical weather spread · ${data.timezone} · ${data.days_in_month} calendar days (${data.total_samples||(data.days_in_month*24)} hourly intervals). Historical weather scenarios, not an actual ${timeYear} weather forecast. Hourly flows are arithmetic means across simulated calendar intervals, not a single dispatch day.`;
   }else{
    $('dayhourStatus').textContent=`${monthTitle} ${timeYear} monthly average · ${dayhourScenarioLabel(data.scenario, data.settings)} · ${data.representative} PV analogue, source weather year ${data.weather_year} · ${data.timezone} · ${data.days_in_month} calendar days (${data.total_samples} hourly intervals). Historical weather scenarios, not an actual ${timeYear} weather forecast. Hourly flows are arithmetic means across simulated calendar intervals, not a single dispatch day.`;
   }
  }else{
   const covNote=data.unavailable_hours&&data.unavailable_hours.length
    ?` · ${data.unavailable_hours.length} boundary hour unavailable`
    :(data.hours.some(h=>h.sample_count<data.days_in_month)?` · ${data.total_samples} hourly observations (${data.hours[0].sample_count} at 00:00 due to source timeline)`:` · ${data.total_samples} hourly observations`);
   $('dayhourStatus').textContent=`${monthTitle} ${timeYear} monthly average · Weather year ${data.weather_year} · ${data.timezone} · ${data.days_in_month} calendar days${covNote}. The battery starts empty at the beginning of the weather year; values follow uninterrupted dispatch through the month. Arithmetic means across simulated intervals, not a single dispatch day.`;
  }
  $('dayChartTitle').textContent=isEnsemble
   ?`Monthly average hourly energy flows · ${monthTitle} ${timeYear} · ${weatherCount}-year arithmetic mean with P10–P90 historical weather spread · kWh per hour (mean)`
   :`Monthly average hourly energy flows · ${monthTitle} ${timeYear} · kWh per hour (mean)`;
  if($('solarFlowTitle'))$('solarFlowTitle').textContent='PV generation and household use · mean flows · kWh per hour';
  if($('gridBatteryTitle'))$('gridBatteryTitle').textContent='Battery and grid flows · mean flows · kWh per hour';
  if($('socTitle'))$('socTitle').textContent=isEnsemble
   ?`Battery state of charge · ${weatherCount}-year arithmetic mean with P10–P90 historical weather spread · kWh stored`
   :(row&&row.soc_end_median_kwh!==undefined
     ?'Battery state of charge · median end-of-hour SOC across days · kWh stored'
     :'Battery state of charge · mean end-of-hour SOC · kWh stored');
  const averagingNote=isEnsemble?`${weatherCount} weather years · ${data.days_in_month} days/year`:`${row?.sample_count} days averaged`;
  let socCard;
  if(!row){
   socCard=`<div class="card"><div class="label">${hourLabel(hour)}</div><div class="value">Unavailable</div><small>This hour is outside the stored local-standard timeline for this weather year.</small></div>`;
  }else if(!isEnsemble&&row.soc_end_median_kwh!==undefined){
   const hasBattery=(data.battery_capacity_kwh||0)>0.001;
   if(!hasBattery){
    socCard=dayMetric('Battery state of charge',0,'kWh stored','No battery installed');
   }else{
    const fullText=`Battery full on ${row.soc_full_days}/${row.soc_sample_days} sampled days · at end of ${hourLabel(hour)}`;
    const detail=`${fullText} · 10th–90th percentile: ${nf(row.soc_end_p10_kwh,2)}–${nf(row.soc_end_p90_kwh,2)} kWh · mean ${nf(row.soc_end_kwh,2)} kWh`;
    socCard=dayMetric('Battery state of charge',row.soc_end_median_kwh,'kWh stored (median)',detail);
   }
  }else{
   const hasBattery=(data.battery_capacity_kwh||0)>0.001;
   if(!hasBattery){
    socCard=dayMetric('Battery state of charge',0,isMonthly?'kWh stored (mean)':'kWh stored','No battery installed');
   }else if(isEnsemble){
    const p10=data.uncertainty_by_hour?.find(x=>x.hour===hour)?.soc_end_kwh?.p10??row.soc_end_p10_kwh;
    const p90=data.uncertainty_by_hour?.find(x=>x.hour===hour)?.soc_end_kwh?.p90??row.soc_end_p90_kwh;
    const spreadDetail=(p10!=null&&p90!=null)?`P10–P90 weather spread: ${nf(p10,2)}–${nf(p90,2)} kWh · start ${nf(row.soc_start_kwh,2)} kWh`:`start ${nf(row.soc_start_kwh,2)} kWh · end of interval`;
    socCard=dayMetric('Battery state of charge',row.soc_end_kwh,isMonthly?'kWh stored (mean)':'kWh stored',spreadDetail);
   }else{
    socCard=dayMetric('Battery state of charge',row.soc_end_kwh,'kWh stored (mean)',`start ${nf(row.soc_start_kwh,2)} kWh · end of interval`);
   }
  }
  $('hourSummary').innerHTML=row?dayMetric('PV production',row.pv_kwh,'kWh (mean)',`${averagingNote}`)+dayMetric('Household load',row.load_kwh,'kWh (mean)',`${averagingNote}`)+dayMetric('Direct PV to load',row.direct_kwh,'kWh (mean)',`${averagingNote}`)+dayMetric(chargeBusLbl,row.charge_input_kwh,chargeBusUnit+' (mean)',`${averagingNote}`)+dayMetric('Battery delivered',row.discharge_delivered_kwh,'kWh AC (mean)',`${averagingNote}`)+dayMetric('Grid import / export',row.grid_import_kwh,`kWh imp · ${nf(row.grid_export_kwh,2)} exp (mean)`,`${averagingNote}`)+socCard:`<div class="card"><div class="label">${hourLabel(hour)}</div><div class="value">Unavailable</div><small>This hour is outside the stored local-standard timeline for this weather year.</small></div>`;
 }else{
  if(data.mode==='forecast_2027'){
   if(isEnsemble){
    $('dayhourStatus').textContent=`${data.date} · ${dayhourScenarioLabel(data.scenario, data.settings)} · ${weatherCount}-year arithmetic mean with P10–P90 historical weather spread · ${data.timezone}. Historical weather scenarios, not an actual ${timeYear} weather forecast.`;
   }else{
    $('dayhourStatus').textContent=`${data.date} · ${dayhourScenarioLabel(data.scenario, data.settings)} · ${data.representative} PV analogue, source weather year ${data.weather_year} · ${data.timezone}. Historical weather scenarios, not an actual ${timeYear} weather forecast.`;
   }
  }else{
   $('dayhourStatus').textContent=`${data.date} · Weather year ${data.weather_year} · ${data.timezone} · ${data.available_hours.length} hourly intervals${data.available_hours.length<24?` · ${24-data.available_hours.length} boundary hour unavailable in this source year`:''}. The battery starts empty at the beginning of the weather year; values follow the uninterrupted dispatch through the previous hours.`;
  }
  $('dayChartTitle').textContent=isEnsemble
   ?`Daily energy flows · ${data.date} · ${weatherCount}-year arithmetic mean with P10–P90 historical weather spread · kWh per hour`
   :`Daily energy flows · ${data.date} · kWh per hour`;
  if($('solarFlowTitle'))$('solarFlowTitle').textContent='PV generation and household use · kWh per hour';
  if($('gridBatteryTitle'))$('gridBatteryTitle').textContent='Battery and grid flows · kWh per hour';
  if($('socTitle'))$('socTitle').textContent=isEnsemble
   ?`Battery state of charge · ${weatherCount}-year arithmetic mean with P10–P90 historical weather spread · kWh stored`
   :'Battery state of charge · kWh stored at end of hour';
  let dailySocCard;
  if(row){
   const hasBattery=(data.battery_capacity_kwh||0)>0.001;
   if(!hasBattery){
    dailySocCard=dayMetric('Battery state of charge',0,'kWh stored','No battery installed');
   }else if(isEnsemble){
    const p10=data.uncertainty_by_hour?.find(x=>x.hour===hour)?.soc_end_kwh?.p10??row.soc_end_p10_kwh;
    const p90=data.uncertainty_by_hour?.find(x=>x.hour===hour)?.soc_end_kwh?.p90??row.soc_end_p90_kwh;
    const spreadDetail=(p10!=null&&p90!=null)?`P10–P90 weather spread: ${nf(p10,2)}–${nf(p90,2)} kWh · start ${nf(row.soc_start_kwh,2)} kWh`:`start ${nf(row.soc_start_kwh,2)} kWh · end of interval`;
    dailySocCard=dayMetric('Battery state of charge',row.soc_end_kwh,'kWh stored',spreadDetail);
   }else{
    dailySocCard=dayMetric('Battery state of charge',row.soc_end_kwh,'kWh stored',`start ${nf(row.soc_start_kwh,2)} kWh · end of interval`);
   }
  }
  $('hourSummary').innerHTML=row?dayMetric('PV production',row.pv_kwh)+dayMetric('Household load',row.load_kwh)+dayMetric('Direct PV to load',row.direct_kwh)+dayMetric(chargeBusLbl,row.charge_input_kwh,chargeBusUnit)+dayMetric('Battery delivered',row.discharge_delivered_kwh,'kWh AC')+dayMetric('Grid import / export',row.grid_import_kwh,`kWh import · ${nf(row.grid_export_kwh,2)} kWh export`)+dailySocCard:`<div class="card"><div class="label">${hourLabel(hour)}</div><div class="value">Unavailable</div><small>This hour is outside the stored local-standard timeline for this weather year.</small></div>`;
 }
 renderHourlyFlowChart('dailySolarFlow',data,hour,[['pv_kwh','#6285bd','PV production'],['load_kwh','#bd8334','Household load'],['direct_kwh','#217a61','Direct PV to load']]);
 renderHourlyFlowChart('dailyGridBattery',data,hour,[['charge_input_kwh','#e6b65b',chargeBusLbl],['discharge_delivered_kwh','#df8a54','Battery discharge delivered'],['grid_import_kwh','#9865a8','Grid import'],['grid_export_kwh','#81929e','Grid export']]);
  const socMax=Math.max(data.battery_capacity_kwh,.1),sy=v=>bottom-v/socMax*plotHeight;let socBody='';for(let i=0;i<=4;i++){const v=socMax*i/4,yy=sy(v);socBody+=`<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/><text x="${left-8}" y="${yy+4}" text-anchor="end" font-size="10" fill="#62756e">${nf(v,1)}</text>`;}socBody+=`<text x="8" y="15" font-size="10" fill="#62756e">kWh stored</text>`;const ordered=[...data.hours].sort((a,b)=>a.hour-b.hour);const hasMedian=!isEnsemble&&isMonthly&&ordered.length>0&&ordered[0].soc_end_median_kwh!==undefined;const hasBattery=(data.battery_capacity_kwh||0)>0.001;const ceilHours=data.hours.filter(r=>r.soc_ceiling_sub_kwh!=null&&Number.isFinite(Number(r.soc_ceiling_sub_kwh))).sort((a,b)=>a.hour-b.hour);if(ceilHours.length>1){socBody+=`<polyline points="${ceilHours.map(r=>`${left+(r.hour+.5)*cell},${sy(r.soc_ceiling_sub_kwh)}`).join(' ')}" fill="none" stroke="#c0392b" stroke-width="1.6" stroke-dasharray="5 3"/>`;}if(hasMedian){socBody+=`<line x1="${left+16}" x2="${left+36}" y1="12" y2="12" stroke="#217a61" stroke-width="2.5"/><circle cx="${left+26}" cy="12" r="3" fill="#217a61"/><text x="${left+42}" y="15" font-size="10" fill="#217a61" font-weight="600">Median across days</text>`;socBody+=`<rect x="${left+160}" y="7" width="20" height="10" fill="#217a61" fill-opacity="0.22"/><text x="${left+186}" y="15" font-size="10" fill="#62756e">10th–90th percentile across days</text>`;if(row){const indicatorText=hasBattery?`Battery full on ${row.soc_full_days}/${row.soc_sample_days} sampled days · at end of ${hourLabel(hour)}`:'No battery installed';socBody+=`<text x="${right}" y="15" text-anchor="end" font-size="10.5" fill="#163d35" font-weight="600">${esc(indicatorText)}</text>`;}const p90Pts=ordered.map(r=>`${left+(r.hour+.5)*cell},${sy(r.soc_end_p90_kwh)}`);const p10Pts=[...ordered].reverse().map(r=>`${left+(r.hour+.5)*cell},${sy(r.soc_end_p10_kwh)}`);socBody+=`<polygon points="${p90Pts.concat(p10Pts).join(' ')}" fill="#217a61" fill-opacity="0.18"/>`;socBody+=`<polyline points="${p90Pts.join(' ')}" fill="none" stroke="#217a61" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.45"/>`;socBody+=`<polyline points="${[...ordered].map(r=>`${left+(r.hour+.5)*cell},${sy(r.soc_end_p10_kwh)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.45"/>`;socBody+=`<polyline points="${ordered.map(r=>`${left+(r.hour+.5)*cell},${sy(r.soc_end_median_kwh)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="2.5"/>`;ordered.forEach(r=>{const x=left+(r.hour+.5)*cell,yy=sy(r.soc_end_median_kwh);const fullNote=hasBattery?`\nBattery full: ${r.soc_full_days}/${r.soc_sample_days} sampled days (${nf((r.soc_full_fraction??(r.soc_full_days/r.soc_sample_days))*100,1)}%) at end of hour`:'\nNo battery installed';const detail=`${monthTitle} ${timeYear} · ${hourLabel(r.hour)}\nMedian end-of-hour state of charge: ${nf(r.soc_end_median_kwh,3)} kWh stored\n10th–90th percentile across days: ${nf(r.soc_end_p10_kwh,3)}–${nf(r.soc_end_p90_kwh,3)} kWh\nMonthly mean for context: ${nf(r.soc_end_kwh,3)} kWh (across ${r.soc_sample_days||r.sample_count||data.days_in_month} days)\nBattery capacity: ${nf(data.battery_capacity_kwh,2)} kWh${fullNote}\nMarginal distribution across days; not a single dispatch day.`;socBody+=`<g ${chartTarget(detail)}><circle cx="${x}" cy="${yy}" r="${r.hour===hour?5:3}" fill="${r.hour===hour?'#e6b65b':'#217a61'}"/><circle class="chart-hit" cx="${x}" cy="${yy}" r="8"/></g>`;});}else if(ordered.length){const uncertSoc=data.uncertainty_by_hour;const hasSocBand=ordered.length>0&&ordered.some(r=>{const u=uncertSoc?.find(x=>x.hour===r.hour)?.soc_end_kwh;return (u?.p10!=null&&u?.p90!=null)||(r.soc_end_p10_kwh!=null&&r.soc_end_p90_kwh!=null)||(r.soc_end_p10!=null&&r.soc_end_p90!=null);});if(isEnsemble){socBody+=`<line x1="${left+16}" x2="${left+36}" y1="12" y2="12" stroke="#217a61" stroke-width="2.5"/><circle cx="${left+26}" cy="12" r="3" fill="#217a61"/><text x="${left+42}" y="15" font-size="10" fill="#217a61" font-weight="600">${weatherCount}-year arithmetic mean</text>`;if(hasSocBand){socBody+=`<rect x="${left+190}" y="7" width="20" height="10" fill="#217a61" fill-opacity="0.22"/><text x="${left+216}" y="15" font-size="10" fill="#62756e">P10–P90 historical weather spread</text>`;}if(!hasBattery){socBody+=`<text x="${right}" y="15" text-anchor="end" font-size="10.5" fill="#163d35" font-weight="600">No battery installed</text>`;}}if(hasSocBand){const getP90=r=>{const u=uncertSoc?.find(x=>x.hour===r.hour)?.soc_end_kwh;return u?.p90??r.soc_end_p90_kwh??r.soc_end_p90??r.soc_end_kwh;};const getP10=r=>{const u=uncertSoc?.find(x=>x.hour===r.hour)?.soc_end_kwh;return u?.p10??r.soc_end_p10_kwh??r.soc_end_p10??r.soc_end_kwh;};const p90Pts=ordered.map(r=>`${left+(r.hour+.5)*cell},${sy(getP90(r))}`);const p10Pts=[...ordered].reverse().map(r=>`${left+(r.hour+.5)*cell},${sy(getP10(r))}`);socBody+=`<polygon points="${p90Pts.concat(p10Pts).join(' ')}" fill="#217a61" fill-opacity="0.18"/>`;socBody+=`<polyline points="${p90Pts.join(' ')}" fill="none" stroke="#217a61" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.45"/>`;socBody+=`<polyline points="${ordered.map(r=>`${left+(r.hour+.5)*cell},${sy(getP10(r))}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="1" stroke-dasharray="3 3" stroke-opacity="0.45"/>`;}socBody+=`<polyline points="${ordered.map(r=>`${left+(r.hour+.5)*cell},${sy(r.soc_end_kwh)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="2.5"/>`;ordered.forEach(r=>{const x=left+(r.hour+.5)*cell,detail=isMonthly?`${monthTitle} ${timeYear} · ${hourLabel(r.hour)}\nMean end-of-hour state of charge: ${nf(r.soc_end_kwh,3)} kWh stored (mean across ${r.sample_count||data.days_in_month} days)\nBattery capacity: ${nf(data.battery_capacity_kwh,2)} kWh${forecastRangeTooltip(data,r.hour,'soc_end_kwh')}`:`${data.date} · ${hourLabel(r.hour)}\nEnd-of-hour state of charge: ${nf(r.soc_end_kwh,3)} kWh stored\nBattery capacity: ${nf(data.battery_capacity_kwh,2)} kWh${forecastRangeTooltip(data,r.hour,'soc_end_kwh')}`;socBody+=`<g ${chartTarget(detail)}><circle cx="${x}" cy="${sy(r.soc_end_kwh)}" r="${r.hour===hour?5:3}" fill="${r.hour===hour?'#e6b65b':'#217a61'}"/><circle class="chart-hit" cx="${x}" cy="${sy(r.soc_end_kwh)}" r="8"/></g>`;});}for(let h=0;h<24;h++)socBody+=`<text x="${left+(h+.5)*cell}" y="${bottom+17}" text-anchor="middle" font-size="9" fill="#62756e">${String(h).padStart(2,'0')}</text>`;const svgLabel=hasMedian?'Median battery state of charge and 10th–90th percentile across days at the end of each hour across the month, in stored kWh':(isEnsemble?`${weatherCount}-year arithmetic mean battery state of charge and P10–P90 historical weather spread at the end of each hour, in stored kWh`:(isMonthly?'Mean battery state of charge at the end of each hour across the month, in stored kWh':'Battery state of charge at the end of each hour, in stored kWh'));$('dailySoc').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${svgLabel}">${socBody}</svg>`;
  if(isMonthly){
   const flowKeys=['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh'];
   const totalRow=['Month total (kWh/month)',...flowKeys.map(k=>nf(data.totals[k],2)),`month start ${nf(data.first_interval_soc_start_kwh??data.soc_start_kwh,2)} → end ${nf(data.last_interval_soc_end_kwh??data.soc_end_kwh,2)} kWh stored`];
   const dailyRow=['Daily average (kWh/day)',...flowKeys.map(k=>nf(data.daily_averages?data.daily_averages[k]:data.totals[k]/data.days_in_month,2)),`mean start ${nf(data.soc_start_kwh,2)} → end ${nf(data.soc_end_kwh,2)} kWh stored`];
   const hourlyRow=['Hourly mean (kWh/h)',...flowKeys.map(k=>nf(data.hourly_averages?data.hourly_averages[k]:data.totals[k]/data.total_samples,3)),`mean stored ${nf(data.mean_soc_kwh,2)} kWh`];
   const covDetails=data.total_samples<data.days_in_month*24
    ?`Coverage: ${data.total_samples} hourly observations across ${data.days_in_month} calendar days (${data.days_in_month*24-data.total_samples} boundary interval missing in source data). Daily averages divide covered month totals by ${data.days_in_month} calendar days; hourly means divide by ${data.total_samples} covered intervals without assuming missing intervals are zero.`
    :`Coverage: ${data.total_samples} hourly observations across ${data.days_in_month} calendar days. Daily averages divide month totals by ${data.days_in_month} calendar days; hourly means divide by ${data.total_samples} intervals.`;
   const monthTotalsNote=isEnsemble
    ?`Month totals represent the ${weatherCount}-year ensemble mean monthly totals, not an actual single trace; daily and hourly amounts are arithmetic means, not a single dispatch day. SOC is stored energy (not summed).`
    :`Month totals sum actual simulated intervals; daily and hourly amounts are arithmetic means, not a single dispatch day. SOC is stored energy (not summed).`;
   $('dayTotals').innerHTML=table(['Monthly energy summary','PV generation','Household load','Direct PV',('Battery charge input · '+chargePhase),'Battery delivered · AC','Grid imports','Grid exports','State of charge'],[totalRow,dailyRow,hourlyRow])+`<p class="muted">${covDetails} Maximum hourly energy-balance residual: ${nf(data.max_balance_error_kwh,8)} kWh. ${monthTotalsNote}</p>`;
  }else{
   const dayTotalsNote=isEnsemble
    ?`Day totals represent the ${weatherCount}-year ensemble mean day totals, not an actual single trace.`
    :'';
   $('dayTotals').innerHTML=table(['Selected day total','PV generation','Household load','Direct PV',('Battery charge input · '+chargePhase),'Battery delivered · AC','Grid imports','Grid exports','SOC start → end'],[['kWh',...['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh'].map(k=>nf(data.totals[k],2)),`${nf(data.soc_start_kwh,2)} → ${nf(data.soc_end_kwh,2)} kWh`]])+`<p class="muted">Maximum hourly energy-balance residual: ${nf(data.max_balance_error_kwh,8)} kWh. ${dayTotalsNote?dayTotalsNote+' ':''}Charge is ${chargePhase==='DC'?'DC terminal':'AC'} energy entering the battery; battery SOC is stored energy after charging efficiency and discharge losses.</p>`;
  }
 if(data.mode==='forecast_2027'&&data.uncertainty_by_hour){
  const band=data.uncertainty_by_hour.find(x=>x.hour===hour);
  const uncertNote=isMonthly
   ?`<p class="muted">Monthly mean hourly range across all ${getWeatherEnsembleMeta(data).runsLabel} · marginal P10 / P50 / P90 (kWh/h mean across ${data.days_in_month} days). Computed by first finding each analogue’s 24 monthly hourly means, then taking percentiles across the ${getWeatherEnsembleMeta(data).count || getWeatherEnsembleMeta().count || "historical"} runs. These ranges are not a single dispatch trace.</p>`
   :`<p class="muted">Hourly range across all ${getWeatherEnsembleMeta(data).count || getWeatherEnsembleMeta().count || "historical"} historical weather runs · marginal P10 / P50 / P90. These ranges are not a single dispatch trace.</p>`;
  const hourHeading=isMonthly?`Selected hour (${hourLabel(hour)}) · monthly mean kWh`:`Selected hour · kWh`;
  const socUncertLabel=isMonthly?'Monthly mean battery SOC across weather runs':'Battery SOC · end of hour';
   const annualTable=data.annual_scenarios&&data.annual_scenarios.length?table(['Annual PV analogue','Source weather year','PV · kWh','Demand · kWh','Grid import · kWh'],data.annual_scenarios.map(x=>[x.label,x.weather_year,nf(x.pv_kwh),nf(x.load_kwh),nf(x.grid_import_kwh)])):'';
   $('dayhourUncertainty').innerHTML=uncertNote+table([hourHeading,'P10','P50','P90'],[['PV production',...['p10','p50','p90'].map(k=>nf(band.pv_kwh[k],2))],['Household load',...['p10','p50','p90'].map(k=>nf(band.load_kwh[k],2))],['Grid import',...['p10','p50','p90'].map(k=>nf(band.grid_import_kwh[k],2))],['Grid export',...['p10','p50','p90'].map(k=>nf(band.grid_export_kwh[k],2))],[socUncertLabel,...['p10','p50','p90'].map(k=>nf(band.soc_end_kwh[k],2))]])+annualTable;
 }else $('dayhourUncertainty').innerHTML='';
 renderDayhourHeatPump(data,hour,isMonthly);
 updateDayhourOutdatedUI();
}
function forecastRangeTooltip(data,h,key){
 if(data.mode!=='forecast_2027')return '';
 const values=data.uncertainty_by_hour?.find(x=>x.hour===h)?.[key];
 if(!values||!Number.isFinite(values.p10)||!Number.isFinite(values.p90)||values.p90<values.p10)return '';
 const isMonthly=data.period==='month';
 const isEnsemble=data.aggregation==='weather_mean'||data.representative==='mean';
 const count=data.weather_count||getWeatherEnsembleMeta(data).count||getWeatherEnsembleMeta().count;
 if(isEnsemble){
  const rangeType=isMonthly?`Marginal monthly mean range across ${count} runs`:`Marginal weather range across ${count} runs`;
  const monthNote=isMonthly?'\nMonth average within each weather year first':'';
  return `\n${count}-year arithmetic mean · weather spread across ${count} runs\n${rangeType} (P10–P90): ${nf(values.p10,3)}–${nf(values.p90,3)} kWh${isMonthly?' (mean)':''}${monthNote}`;
 }
 const rangeType=isMonthly?`Marginal monthly mean range across ${count || 'historical'} runs`:`Marginal weather range across ${count || 'historical'} runs`;
 return `\n${data.representative} PV analogue · source weather year ${data.weather_year}\n${rangeType} (P10–P90): ${nf(values.p10,3)}–${nf(values.p90,3)} kWh${isMonthly?' (mean)':''}`;
}
function renderHourlyFlowChart(target,data,hour,series){
 const uncertMap=(data.mode==='forecast_2027'&&Array.isArray(data.uncertainty_by_hour))?new Map(data.uncertainty_by_hour.map(u=>[u.hour,u])):null;
 let peak=.1;
 (data.hours||[]).forEach(r=>{
  const u=uncertMap?.get(r.hour);
  series.forEach(([k])=>{
   const v=r[k];
   if(Number.isFinite(v)&&v>peak)peak=v;
   const uv=u?.[k];
   if(uv&&Number.isFinite(uv.p10)&&Number.isFinite(uv.p90)&&uv.p90>=uv.p10&&uv.p90>peak)peak=uv.p90;
  });
 });
 const max=peak*1.12,width=1020,height=320,left=66,right=1004,top=18,bottom=274,cell=(right-left)/24,plotHeight=bottom-top,y=v=>bottom-v/max*plotHeight;
 const isMonthly=data.period==='month';
 const monthTitle=data.month_name||dayhourMonthName(data.month||1);
 const timeYear=data.year||(data.mode==='forecast_2027'?getForecastYear():data.weather_year);
 let body='';
 for(let i=0;i<=4;i++){const v=max*i/4,yy=y(v);body+=`<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/><text x="${left-8}" y="${yy+4}" text-anchor="end" font-size="10" fill="#62756e">${nf(v,1)}</text>`;}
 body+=`<text x="8" y="15" font-size="10" fill="#62756e">${isMonthly?'kWh/h mean':'kWh per hour'}</text>`;
 if(uncertMap){
  const hasSpread=(data.hours||[]).some(r=>{
   const u=uncertMap.get(r.hour);
   return u&&series.some(([k])=>{
    const uv=u[k];
    return uv&&Number.isFinite(uv.p10)&&Number.isFinite(uv.p90)&&uv.p90>=uv.p10;
   });
  });
  if(hasSpread){
   const isEnsemble=data.aggregation==='weather_mean'||data.representative==='mean';
   const count=data.weather_count||getWeatherEnsembleMeta(data).count;
   const legendText=isEnsemble?`Bars: ${count}-year mean · whiskers: P10–P90 weather spread`:'Bars: selected analogue · whiskers: P10–P90 weather spread';
   body+=`<text x="${right}" y="15" text-anchor="end" font-size="10" fill="#62756e">${legendText}</text>`;
  }
 }
 for(let h=0;h<24;h++){
  const x=left+h*cell,record=data.hours.find(r=>r.hour===h);
  if(h===hour)body+=`<rect x="${x}" y="${top}" width="${cell}" height="${bottom-top+20}" fill="var(--soft, #e5eee6)"/>`;
  if(record){
   const step=cell*.9/series.length,bw=step*.68;
   series.forEach(([key,color,label],j)=>{
    const value=record[key],xx=x+cell*.05+j*step+(step-bw)/2;
    const detail=isMonthly
     ?`${monthTitle} ${timeYear} · ${hourLabel(h)} (monthly mean)\n${label}: ${nf(value,3)} kWh (mean)\nArithmetic mean across ${record.sample_count||data.days_in_month} calendar days${forecastRangeTooltip(data,h,key)}`
     :`${data.date} · ${hourLabel(h)}\n${label}: ${nf(value,3)} kWh\nHourly energy interval · fixed UTC+1${forecastRangeTooltip(data,h,key)}`;
    let whisker='';
    if(uncertMap){
     const uv=uncertMap.get(h)?.[key];
     if(uv&&Number.isFinite(uv.p10)&&Number.isFinite(uv.p90)&&uv.p90>=uv.p10){
      const midX=xx+bw/2,yTop=y(uv.p90),yBot=y(uv.p10),capW=bw*.35;
      whisker=`<g class="hourly-weather-range" stroke="var(--text-main,#183b33)" stroke-opacity="0.85" stroke-width="1" pointer-events="none"><line vector-effect="non-scaling-stroke" x1="${midX}" y1="${yBot}" x2="${midX}" y2="${yTop}"/><line vector-effect="non-scaling-stroke" x1="${midX-capW}" y1="${yTop}" x2="${midX+capW}" y2="${yTop}"/><line vector-effect="non-scaling-stroke" x1="${midX-capW}" y1="${yBot}" x2="${midX+capW}" y2="${yBot}"/></g>`;
     }
    }
    body+=`<g ${chartTarget(detail)}><rect x="${xx}" y="${y(value)}" width="${bw}" height="${Math.max(0,bottom-y(value))}" fill="${color}"/>${whisker}<rect class="chart-hit" x="${xx-1}" y="${top}" width="${bw+2}" height="${bottom-top}"/></g>`;
   });
  }else body+=`<line x1="${x+cell/2}" x2="${x+cell/2}" y1="${bottom-5}" y2="${bottom+3}" stroke="#aab7ac" stroke-dasharray="2 2"/>`;
  body+=`<text x="${x+cell/2}" y="${bottom+17}" text-anchor="middle" font-size="9" fill="#62756e">${String(h).padStart(2,'0')}</text>`;
 }
 const svgLabel=isMonthly?`Monthly average hourly ${series.map(([, ,label])=>label).join(', ')} for ${esc(monthTitle)} ${timeYear}`:`Hourly ${series.map(([, ,label])=>label).join(', ')} for ${esc(data.date)}`;
 $(`${target}`).innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${svgLabel}">${body}</svg>`;
}

function renderDayhourHeatPump(data, hour, isMonthly){
 const section = $('dayhourHpSection');
 if(!section) return;
 const r = (typeof current !== 'undefined') ? current?.result : null;
 const effSettings = data?.settings || r?.settings || (typeof settings !== 'undefined' ? settings : {});
 const activeHp = Boolean(data && data.scenario !== 'baseline' && effSettings.planning_heat_pump && data.hours?.some(h => h.heat_pump || h.end_uses));
 if(!activeHp){
  section.hidden = true;
  return;
 }
 section.hidden = false;

 const cardsEl = $('dayhourHpHourCards');
 const tableEl = $('dayhourHpTable');
 const statusEl = $('dayhourHpStatus');
 const titleEl = $('dayhourHpTitle');
 if(titleEl) titleEl.textContent = 'Hourly heat pump dispatch · heat pump use and temperatures';

 const hours = data.hours || [];
 const row = hours.find(h => h.hour === hour) || data.selected_hour;
 const hasHpData = Boolean(row && (row.heat_pump || row.end_uses));

 if(!hasHpData){
  if(cardsEl) cardsEl.innerHTML = `<div class="card"><div class="label">${hourLabel(hour)}</div><div class="value">Telemetry pending</div><small>Hourly heat pump use and temperatures pending backend simulation for this scenario.</small></div>`;
  if(tableEl) tableEl.innerHTML = `<p class="muted">Hourly heat pump simulation table will appear once hourly heat pump use and temperatures are generated.</p>`;
  if(statusEl) statusEl.textContent = 'Hourly heat pump dispatch breakdown pending calculation.';
  return;
 }

 const hp = row.heat_pump || {};
 const endUses = row.end_uses || {};
 const monthTitle = data.month_name || dayhourMonthName(data.month || 1);
 const timeYear = data.year || (data.mode === 'forecast_2027' ? getForecastYear() : data.weather_year);
 const unitLabel = isMonthly ? 'kWh/h (mean)' : 'kWh';

 if(statusEl){
  statusEl.textContent = isMonthly
   ? `Hourly heat pump dispatch breakdown for ${monthTitle} ${timeYear} · arithmetic mean across simulated calendar intervals.`
   : `Hourly heat pump dispatch breakdown for ${data.date} · fixed UTC+1 standard time.`;
 }

 // Render Selected Hour Cards
 if(cardsEl){
  const spaceCompElec = hp.space_compressor_electricity_kwh ?? endUses.heat_pump_heating_kwh ?? 0;
  const dhwCompElec = hp.dhw_compressor_electricity_kwh ?? endUses.heat_pump_hot_water_kwh ?? 0;
  const spaceBackupElec = hp.space_backup_electricity_kwh ?? 0;
  const dhwBackupElec = hp.dhw_backup_electricity_kwh ?? 0;
  const totalBackupElec = spaceBackupElec + dhwBackupElec;
  const auxElec = hp.auxiliary_electricity_kwh ?? endUses.heat_pump_auxiliary_kwh ?? 0;
  
  const spaceDeliveredHeat = hp.delivered_space_heat_kwh ?? 0;
  const spaceRequestedHeat = hp.requested_space_heat_kwh ?? 0;
  const spaceUnmetHeat = hp.unmet_space_heat_kwh ?? 0;
  
  const dhwDeliveredHeat = hp.delivered_draw_heat_kwh ?? 0;
  const dhwRequestedHeat = hp.requested_draw_heat_kwh ?? 0;
  const dhwUnmetHeat = hp.unmet_draw_heat_kwh ?? 0;
  
  const totalUnmet = spaceUnmetHeat + dhwUnmetHeat;
  const totalDelivered = spaceDeliveredHeat + dhwDeliveredHeat;
  
  const spaceRuntimeMin = (hp.space_runtime_hours ?? 0) * 60;
  const dhwRuntimeMin = (hp.dhw_runtime_hours ?? 0) * 60;

  let shortfallCard = '';
  if(totalUnmet > 0.001){
   shortfallCard = `<div class="card hp-shortfall-card"><div class="label">Thermal shortfall</div><div class="value bad">${nf(totalUnmet, 2)} <small>${unitLabel}</small></div><small>Space unmet: ${nf(spaceUnmetHeat, 2)} · DHW unmet: ${nf(dhwUnmetHeat, 2)}</small></div>`;
  } else {
   shortfallCard = `<div class="card"><div class="label">Delivered useful heat</div><div class="value good">${nf(totalDelivered, 2)} <small>${unitLabel}</small></div><small>Demand 100% satisfied · 0 shortfall</small></div>`;
  }

  const spaceDetail = `Delivered ${nf(spaceDeliveredHeat, 2)} heat · ${spaceRuntimeMin > 0 ? nf(spaceRuntimeMin, 0) + ' min runtime' : 'idle'}${hp.space_flow_temp_c != null ? ` · flow ${nf(hp.space_flow_temp_c, 1)}°C` : ''}${hp.space_capacity_kw != null ? ` · available output ${nf(hp.space_capacity_kw, 1)} kW` : ''}`;
  const dhwDetail = `Delivered ${nf(dhwDeliveredHeat, 2)} draw · ${dhwRuntimeMin > 0 ? nf(dhwRuntimeMin, 0) + ' min reheat' : 'idle'}${hp.tank_temp_c != null ? ` · cylinder ${nf(hp.tank_temp_c, 1)}°C` : ''}`;
  const backupDetail = totalBackupElec > 0 ? `Space backup: ${nf(spaceBackupElec, 2)} · DHW backup: ${nf(dhwBackupElec, 2)}` : 'Backup heaters idle';
  const auxDetail = `External circulation pump & standby electronics`;
  const storageDetail = hp.tank_start_storage_kwh != null ? `Start ${nf(hp.tank_start_storage_kwh, 2)} → End ${nf(hp.tank_end_storage_kwh, 2)} kWh · losses ${nf(hp.tank_losses_kwh, 2)} kWh` : `Standing loss ${nf(hp.tank_losses_kwh, 2)} kWh`;

  cardsEl.innerHTML = 
   dayMetric('Space heating electricity', spaceCompElec, unitLabel, spaceDetail) +
   dayMetric('DHW heating electricity', dhwCompElec, unitLabel, dhwDetail) +
   dayMetric('Backup resistance electricity', totalBackupElec, unitLabel, backupDetail) +
   dayMetric('Auxiliary electricity', auxElec, unitLabel, auxDetail) +
   shortfallCard +
   (hp.tank_temp_c != null ? dayMetric('Cylinder storage & temp', hp.tank_temp_c, '°C', storageDetail) : '');
 }

 // Render 24-Row Expandable Table
 if(tableEl){
  const tableHeaders = isMonthly ? [
   'Hour',
   'Space comp · kWh/h (mean)',
   'DHW comp · kWh/h (mean)',
   'Backup · kWh/h (mean)',
   'Auxiliary · kWh/h (mean)',
   'Space delivered · kWh/h (mean)',
   'DHW delivered · kWh/h (mean)',
   'Unmet heat · kWh/h (mean)',
   'Cylinder temp',
   'Flow temp'
  ] : [
   'Hour',
   'Space comp · kWh',
   'DHW comp · kWh',
   'Backup · kWh',
   'Auxiliary · kWh',
   'Space delivered · kWh',
   'DHW delivered · kWh',
   'Unmet heat · kWh',
   'Cylinder temp',
   'Flow temp'
  ];

  const tableRows = [];
  for(let h = 0; h < 24; h++){
   const hRow = hours.find(x => x.hour === h) || {};
   const hHp = hRow.heat_pump || {};
   const hEnd = hRow.end_uses || {};
   const scElec = hHp.space_compressor_electricity_kwh ?? hEnd.heat_pump_heating_kwh ?? 0;
   const dhwElec = hHp.dhw_compressor_electricity_kwh ?? hEnd.heat_pump_hot_water_kwh ?? 0;
   const bkElec = (hHp.space_backup_electricity_kwh ?? 0) + (hHp.dhw_backup_electricity_kwh ?? 0);
   const axElec = hHp.auxiliary_electricity_kwh ?? hEnd.heat_pump_auxiliary_kwh ?? 0;
   const scDeliv = hHp.delivered_space_heat_kwh ?? 0;
   const dhwDeliv = hHp.delivered_draw_heat_kwh ?? 0;
   const unDeliv = (hHp.unmet_space_heat_kwh ?? 0) + (hHp.unmet_draw_heat_kwh ?? 0);
   const tTemp = hHp.tank_temp_c != null ? nf(hHp.tank_temp_c, 1) + ' °C' : '—';
   const fTemp = hHp.space_flow_temp_c != null ? nf(hHp.space_flow_temp_c, 1) + ' °C' : '—';

   tableRows.push([
    hourLabel(h),
    nf(scElec, 3),
    nf(dhwElec, 3),
    nf(bkElec, 3),
    nf(axElec, 3),
    nf(scDeliv, 3),
    nf(dhwDeliv, 3),
    nf(unDeliv, 3),
    tTemp,
    fTemp
   ]);
  }

  // Add Total Row
  if(isMonthly){
   const hpTotals = data.hp_totals || {};
   const endTotals = data.end_use_totals || {};
   const mult = data.total_samples || (data.days_in_month * 24);
   const totSc = hpTotals.space_compressor_electricity_kwh ?? endTotals.heat_pump_heating_kwh ?? (hours.reduce((acc,h)=>acc+(h.heat_pump?.space_compressor_electricity_kwh||0),0) * (mult/24));
   const totDhw = hpTotals.dhw_compressor_electricity_kwh ?? endTotals.heat_pump_hot_water_kwh ?? (hours.reduce((acc,h)=>acc+(h.heat_pump?.dhw_compressor_electricity_kwh||0),0) * (mult/24));
   const totBk = (hpTotals.space_backup_electricity_kwh ?? 0) + (hpTotals.dhw_backup_electricity_kwh ?? 0) || (hours.reduce((acc,h)=>acc+((h.heat_pump?.space_backup_electricity_kwh||0)+(h.heat_pump?.dhw_backup_electricity_kwh||0)),0) * (mult/24));
   const totAx = hpTotals.auxiliary_electricity_kwh ?? endTotals.heat_pump_auxiliary_kwh ?? (hours.reduce((acc,h)=>acc+(h.heat_pump?.auxiliary_electricity_kwh||0),0) * (mult/24));
   const totScDeliv = hpTotals.delivered_space_heat_kwh ?? (hours.reduce((acc,h)=>acc+(h.heat_pump?.delivered_space_heat_kwh||0),0) * (mult/24));
   const totDhwDeliv = hpTotals.delivered_draw_heat_kwh ?? (hours.reduce((acc,h)=>acc+(h.heat_pump?.delivered_draw_heat_kwh||0),0) * (mult/24));
   const totUnDeliv = (hpTotals.unmet_space_heat_kwh ?? 0) + (hpTotals.unmet_draw_heat_kwh ?? 0) || (hours.reduce((acc,h)=>acc+((h.heat_pump?.unmet_space_heat_kwh||0)+(h.heat_pump?.unmet_draw_heat_kwh||0)),0) * (mult/24));

   tableRows.push([
    'Month total (kWh/month)',
    nf(totSc, 1),
    nf(totDhw, 1),
    nf(totBk, 1),
    nf(totAx, 1),
    nf(totScDeliv, 1),
    nf(totDhwDeliv, 1),
    nf(totUnDeliv, 1),
    '—',
    '—'
   ]);
  } else {
   const hpTotals = data.hp_totals || {};
   const totSc = hpTotals.space_compressor_electricity_kwh ?? hours.reduce((acc,h)=>acc+(h.heat_pump?.space_compressor_electricity_kwh||0),0);
   const totDhw = hpTotals.dhw_compressor_electricity_kwh ?? hours.reduce((acc,h)=>acc+(h.heat_pump?.dhw_compressor_electricity_kwh||0),0);
   const totBk = (hpTotals.space_backup_electricity_kwh ?? 0) + (hpTotals.dhw_backup_electricity_kwh ?? 0) || hours.reduce((acc,h)=>acc+((h.heat_pump?.space_backup_electricity_kwh||0)+(h.heat_pump?.dhw_backup_electricity_kwh||0)),0);
   const totAx = hpTotals.auxiliary_electricity_kwh ?? hours.reduce((acc,h)=>acc+(h.heat_pump?.auxiliary_electricity_kwh||0),0);
   const totScDeliv = hpTotals.delivered_space_heat_kwh ?? hours.reduce((acc,h)=>acc+(h.heat_pump?.delivered_space_heat_kwh||0),0);
   const totDhwDeliv = hpTotals.delivered_draw_heat_kwh ?? hours.reduce((acc,h)=>acc+(h.heat_pump?.delivered_draw_heat_kwh||0),0);
   const totUnDeliv = (hpTotals.unmet_space_heat_kwh ?? 0) + (hpTotals.unmet_draw_heat_kwh ?? 0) || hours.reduce((acc,h)=>acc+((h.heat_pump?.unmet_space_heat_kwh||0)+(h.heat_pump?.unmet_draw_heat_kwh||0)),0);

   tableRows.push([
    'Selected day total (kWh)',
    nf(totSc, 2),
    nf(totDhw, 2),
    nf(totBk, 2),
    nf(totAx, 2),
    nf(totScDeliv, 2),
    nf(totDhwDeliv, 2),
    nf(totUnDeliv, 2),
    '—',
    '—'
   ]);
  }

  tableEl.innerHTML = table(tableHeaders, tableRows) + `<p class="muted">${isMonthly ? 'Hourly rows report arithmetic means across simulated calendar intervals (kWh/h mean). Total row sums actual covered monthly dispatch (kWh/month).' : 'Hourly rows report dispatch per interval (kWh). Total row sums the 24 hours of the selected day.'} Temperatures and flow rates represent operating interval averages.</p>`;
 }
}

let dayhourOutdated=false;

function isDayhourMatching(data=dayhourData){
 if(!data)return false;
 if(typeof loanPending!=='undefined'&&loanPending)return false;
 const s=typeof settings!=='undefined'?settings:null;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(s))return false;
 if(typeof acceptedInputFingerprint!=='undefined'&&typeof inputFingerprint==='function'){
  if(acceptedInputFingerprint===null||acceptedInputFingerprint!==inputFingerprint(s,typeof quote!=='undefined'?quote:{line_items:[]}))return false;
 }
 if(typeof current==='undefined')return true;
 if(!current||!current.result||!current.result.settings)return false;
 const resSettings=current.result.settings;
 if(s){
  if(s.load_scenario!==resSettings.load_scenario)return false;
  if(Boolean(s.planning_heat_pump)!==Boolean(resSettings.planning_heat_pump))return false;
  if(resSettings.timezone_mode&&s.timezone_mode!==resSettings.timezone_mode)return false;
 }

 const expectedMode=typeof $==='function'&&$('dayhourMode')?$('dayhourMode').value:'forecast_2027';
 if(data.mode&&data.mode!==expectedMode)return false;

 const expectedPeriod=typeof $==='function'&&$('dayhourPeriod')?$('dayhourPeriod').value:'day';
 if(data.period&&data.period!==expectedPeriod)return false;

 const expectedScenario=typeof $==='function'&&$('dayhourScenario')?$('dayhourScenario').value:(resSettings.load_scenario||s?.load_scenario||'electrified');
 if(data.scenario&&data.scenario!==expectedScenario)return false;

 if(expectedPeriod==='day'){
  const expectedDate=typeof $==='function'&&$('weatherDate')?$('weatherDate').value:'2027-01-01';
  if(data.date&&data.date!==expectedDate)return false;
 }else if(expectedPeriod==='month'){
  const expectedMonth=Number(typeof $==='function'&&$('dayhourMonth')?$('dayhourMonth').value:1);
  if(data.month!=null&&Number(data.month)!==expectedMonth)return false;
 }

 if(expectedMode==='forecast_2027'){
  const expectedRep=typeof $==='function'&&$('weatherRepresentative')?($('weatherRepresentative').value||'mean'):'mean';
  const dataRep=data.representative||(data.aggregation==='weather_mean'?'mean':'');
  if(dataRep&&dataRep!==expectedRep)return false;
 }else{
  const expectedYear=Number(typeof $==='function'&&$('weatherYear')?.value)||2015;
  if(data.weather_year!=null&&Number(data.weather_year)!==expectedYear)return false;
 }

 const dSettings=data.settings;
 if(dSettings){
  if(dSettings.load_scenario&&dSettings.load_scenario!==expectedScenario)return false;
  if(dSettings.planning_heat_pump!==undefined&&Boolean(dSettings.planning_heat_pump)!==Boolean(resSettings.planning_heat_pump))return false;
  if(dSettings.timezone_mode&&resSettings.timezone_mode&&dSettings.timezone_mode!==resSettings.timezone_mode)return false;

  const isHourlyAcActive=expectedScenario!=='baseline'&&!resSettings.planning_heat_pump&&!dSettings.planning_heat_pump;
  if(isHourlyAcActive){
   if(dSettings.heating_schedule_mode&&resSettings.heating_schedule_mode&&dSettings.heating_schedule_mode!==resSettings.heating_schedule_mode)return false;
   if(dSettings.ac_heating_additional_demand_pct!=null&&resSettings.ac_heating_additional_demand_pct!=null&&dSettings.ac_heating_additional_demand_pct!==resSettings.ac_heating_additional_demand_pct)return false;
   if(resSettings.heating_schedule_mode!=='continuous'&&dSettings.heating_schedule_mode!=='continuous'){
    if(dSettings.ac_heating_windows&&resSettings.ac_heating_windows&&typeof settingValuesEqual==='function'&&!settingValuesEqual(dSettings.ac_heating_windows,resSettings.ac_heating_windows))return false;
   }
  }

  for(const [k,v] of Object.entries(dSettings)){
   if(k==='load_scenario'||k==='planning_heat_pump'||k==='timezone_mode'||k==='heating_schedule_mode'||k==='ac_heating_additional_demand_pct'||k==='ac_heating_windows')continue;
   if(resSettings[k]!==undefined){
    if(typeof settingValuesEqual==='function'){
     if(!settingValuesEqual(v,resSettings[k]))return false;
    }else if(v!==resSettings[k]){
     return false;
    }
   }
  }
 }
 if(data.battery_capacity_kwh!=null&&resSettings.battery_kwh!=null&&Math.abs(data.battery_capacity_kwh-resSettings.battery_kwh)>1e-4)return false;
 return true;
}

function isDayhourOutdated(data=dayhourData){
 if(dayhourOutdated)return true;
 return !isDayhourMatching(data);
}

function updateDayhourOutdatedUI(){
 if(typeof document==='undefined')return;
 const fold=document.getElementById('dayhourExplorerFold')||(typeof $==='function'?$('dayhourExplorerFold'):null);
 const isOutdated=isDayhourOutdated();
 if(fold){
  if(isOutdated&&dayhourData){
   fold.classList.add('stale-overlay');
   fold.classList.add('stale');
  }else if(!isOutdated){
   fold.classList.remove('stale-overlay');
   fold.classList.remove('stale');
  }
 }
 const hpSection=document.getElementById('dayhourHpSection');
 if(hpSection&&!hpSection.hidden){
  if(isOutdated&&dayhourData)hpSection.classList.add('stale-overlay');
  else hpSection.classList.remove('stale-overlay');
 }
 const statusEl=document.getElementById('dayhourStatus')||(typeof $==='function'?$('dayhourStatus'):null);
 let notice=document.getElementById('dayhourStaleNotice')||(fold?.querySelector?fold.querySelector('.stale-comparison-notice'):null);
 if(isOutdated&&dayhourData){
  if(!notice&&typeof document.createElement==='function'){
   notice=document.createElement('div');
   notice.id='dayhourStaleNotice';
   notice.className='stale-comparison-notice';
   notice.setAttribute('role','status');
   notice.style.cssText='margin-bottom:12px;padding:8px 12px;background:#fff8e9;border:1px solid #e6d49f;border-radius:6px;font-size:12px;color:#705018;';
   notice.textContent='⚠️ Inputs changed or recalculation pending · Hourly dispatch reflects previously accepted result';
   if(fold){
    if(typeof fold.insertBefore==='function'&&statusEl&&statusEl.nextSibling){
     fold.insertBefore(notice,statusEl.nextSibling);
    }else if(typeof fold.insertBefore==='function'&&fold.firstChild){
     fold.insertBefore(notice,fold.firstChild);
    }else if(typeof fold.prepend==='function'){
     fold.prepend(notice);
    }else if(typeof fold.appendChild==='function'){
     fold.appendChild(notice);
    }
   }
  }else if(notice){
   notice.hidden=false;
   notice.style.display='';
   notice.textContent='⚠️ Inputs changed or recalculation pending · Hourly dispatch reflects previously accepted result';
  }
 }else if(notice){
  notice.hidden=true;
  notice.style.display='none';
 }
}

function invalidateDayhour(){
 dayhourRequest++;
 dayhourOutdated=true;
 updateDayhourOutdatedUI();
}

const markDayhourOutdated=invalidateDayhour;

function acceptDayhour(result=(typeof current!=='undefined'?current?.result:null)){
 if(result&&result.hours){
  if(!isDayhourMatching(result))return false;
  dayhourData=result;
  dayhourOutdated=false;
  updateDayhourOutdatedUI();
  return true;
 }
 const curRes=typeof current!=='undefined'?current?.result:null;
 const r=(result&&result.result)?result.result:(result||curRes);
 if(!r||!dayhourData||!isDayhourMatching(dayhourData))return false;
 dayhourOutdated=false;
 updateDayhourOutdatedUI();
 return true;
}

function isDayhourRequestValid(id,payloadSettings){
 if(id!==dayhourRequest)return false;
 if(payloadSettings&&typeof current!=='undefined'&&current?.result?.settings){
  if(typeof settingValuesEqual==='function'&&!settingValuesEqual(payloadSettings,current.result.settings)){
   return false;
  }
 }
 return true;
}

async function refreshDayhour(){
 if(!current||!current.result?.settings)return;
 const id=++dayhourRequest;
 const mode=$('dayhourMode')?$('dayhourMode').value:'forecast_2027';
 const period=$('dayhourPeriod')?$('dayhourPeriod').value:'day';
 const year=Number($('weatherYear')?.value)||2015;
 const hour=selectedDayHour();
 const date=$('weatherDate')?$('weatherDate').value:'2027-01-01';
 const month=Number($('dayhourMonth')?$('dayhourMonth').value:1);
 const scenario=$('dayhourScenario')?$('dayhourScenario').value:(settings.load_scenario||'electrified');
 const representative=typeof $==='function'&&$('weatherRepresentative')?($('weatherRepresentative').value||'mean'):'mean';
 if($('dayhourStatus'))$('dayhourStatus').textContent=period==='month'
  ?'Calculating uninterrupted annual dispatch and monthly hourly averages in Python…'
  :'Running the full calendar through the Python dispatch…';
 const hpCardsEl=$('dayhourHpHourCards');
 const hpTableEl=$('dayhourHpTable');
 const hpStatusEl=$('dayhourHpStatus');
 if(hpCardsEl)hpCardsEl.innerHTML='<div class="card"><div class="label">Loading hourly dispatch…</div><div class="value">Pending</div><small>Calculating hourly heat pump dispatch for the updated mode/scenario…</small></div>';
 if(hpTableEl)hpTableEl.innerHTML='<p class="muted">Updating hourly heat pump dispatch data…</p>';
 if(hpStatusEl)hpStatusEl.textContent='Updating hourly heat pump dispatch…';
 const payload={
  settings:current.result.settings,
  mode,
  period,
  scenario,
  representative,
  weather_year:year,
  hour
 };
 if(period==='month')payload.month=month;
 else payload.date=date;
 try{
  const result=await api('/api/dayhour',payload);
  if(id===dayhourRequest&&isDayhourRequestValid(id,payload.settings)){
   if(isDayhourMatching(result)){
    dayhourOutdated=false;
   }
   renderDayhour(result);
  }
 }catch(e){
  if(id===dayhourRequest&&isDayhourRequestValid(id,payload.settings)){
   dayhourData=null;
   if($('hourSummary'))$('hourSummary').innerHTML='';
   if($('dailySolarFlow'))$('dailySolarFlow').innerHTML='';
   if($('dailyGridBattery'))$('dailyGridBattery').innerHTML='';
   if($('dailySoc'))$('dailySoc').innerHTML='';
   if($('dayTotals'))$('dayTotals').innerHTML='';
   if($('dayhourUncertainty'))$('dayhourUncertainty').innerHTML='';
   if($('dayhourStatus'))$('dayhourStatus').textContent=e.message||String(e);
   if($('dayhourHpSection'))$('dayhourHpSection').hidden=true;
   if($('dayhourHpHourCards'))$('dayhourHpHourCards').innerHTML='';
   if($('dayhourHpTable'))$('dayhourHpTable').innerHTML='';
   updateDayhourOutdatedUI();
  }
 }
}
function selectDayhourPeriod(){
 const isMonth=$('dayhourPeriod')?.value==='month';
 if($('dayPickerControl'))$('dayPickerControl').hidden=isMonth;
 if($('monthPickerControl'))$('monthPickerControl').hidden=!isMonth;
 if(isMonth)syncMonthFromWeatherDate();
 else syncWeatherDateToMonth();
}
function setupDayhour(){
 const years=current?.result.weather_years||[];
 if(!years.length)return;
 const select=$('weatherYear'),previous=select.value;
 select.innerHTML=years.map(y=>`<option value="${y}">${y}</option>`).join('');
 select.value=years.includes(Number(previous))?previous:(years.includes(2015)?'2015':String(years[0]));
 updateForecastDateBounds();
 selectDayhourPeriod();
 selectDayhourMode();
 refreshDayhour();
}
function selectDayhourMode(){
 const forecast=$('dayhourMode').value==='forecast_2027';
 $('weatherYearControl').hidden=forecast;
 $('representativeControl').hidden=!forecast;
 if($('dayhourPeriod')?.value==='month')syncWeatherDateToMonth();
 if(forecast){
  const fy=getForecastYear();
  $('weatherDate').value=dayhourDateForYear(fy);
  $('weatherDate').min=`${fy}-01-01`;
  $('weatherDate').max=`${fy}-12-31`;
 }else updateWeatherDateYear();
 syncMonthFromWeatherDate();
}
function updateWeatherDateYear(){
 const y=Number($('weatherYear').value);
 if($('dayhourPeriod')?.value==='month')syncWeatherDateToMonth();
 $('weatherDate').value=dayhourDateForYear(y);
 $('weatherDate').min=`${y}-01-01`;
 $('weatherDate').max=`${y}-12-31`;
 syncMonthFromWeatherDate();
}

// One delegated tooltip survives chart redraws and stays outside scrolling SVGs.
let chartTooltip,activeChartPoint;
function hideChartTooltip(){if(chartTooltip)chartTooltip.hidden=true;if(activeChartPoint)activeChartPoint.removeAttribute('aria-describedby');activeChartPoint=null;}
function renderCashTooltip(tooltip,detail){
 tooltip.replaceChildren();
 const lines=detail.split('\n'),title=document.createElement('div');title.className='chart-tooltip-title';title.textContent=lines.shift()||'';tooltip.appendChild(title);
 const totals=document.createElement('div');totals.className='chart-tooltip-totals';
 while(lines.length&&lines[0]&&!lines[0].startsWith('Annual breakdown')&&!lines[0].startsWith('Annual diagnostic means')){
  const line=lines.shift(),medianAt=line.indexOf(' · median '),row=document.createElement('div');row.className='chart-tooltip-metric';
  if(medianAt>=0){const label=document.createElement('span'),value=document.createElement('strong');label.textContent=line.slice(0,medianAt);const remainder=line.slice(medianAt+10),rangeAt=remainder.indexOf(' (weather range ');value.textContent=rangeAt<0?remainder:remainder.slice(0,rangeAt);row.append(label,value);if(rangeAt>=0){const range=document.createElement('small');range.textContent=remainder.slice(rangeAt+2,-1);row.appendChild(range);}}
  else row.textContent=line;
  totals.appendChild(row);
 }
 tooltip.appendChild(totals);
 if(lines[0]?.startsWith('Annual breakdown')||lines[0]?.startsWith('Annual diagnostic means')){
  const headerLine=lines.shift();
  const heading=document.createElement('div');heading.className='chart-tooltip-section-title';heading.textContent=headerLine.endsWith(':')?headerLine.slice(0,-1):headerLine;tooltip.appendChild(heading);
  const breakdown=document.createElement('div');breakdown.className='chart-tooltip-breakdown';
  for(const line of lines){if(!line)continue;const split=line.lastIndexOf(': '),label=document.createElement('span'),amount=document.createElement('strong');label.textContent=split<0?line:line.slice(0,split);amount.textContent=split<0?'':line.slice(split+2);breakdown.append(label,amount);}
  tooltip.appendChild(breakdown);
 }
}
function showChartTooltip(point,event){
 if(!chartTooltip){chartTooltip=document.createElement('div');chartTooltip.id='chartTooltip';chartTooltip.className='chart-tooltip';chartTooltip.setAttribute('role','tooltip');chartTooltip.addEventListener('pointerleave',hideChartTooltip);document.body.appendChild(chartTooltip);}
 const activeDialog = point.closest?.('dialog[open]') || null;
 if(activeDialog && chartTooltip.parentNode !== activeDialog){
  activeDialog.appendChild(chartTooltip);
 } else if(!activeDialog && chartTooltip.parentNode !== document.body && document.body){
  document.body.appendChild(chartTooltip);
 }
 if(activeChartPoint&&activeChartPoint!==point)activeChartPoint.removeAttribute('aria-describedby');
 activeChartPoint=point;const cash=Boolean(point.closest?.('.cash-chart'));chartTooltip.classList.toggle('cash-chart-tooltip',cash);if(cash)renderCashTooltip(chartTooltip,point.dataset.chartDetail);else chartTooltip.textContent=point.dataset.chartDetail;chartTooltip.hidden=false;point.setAttribute('aria-describedby','chartTooltip');
 const rect=point.getBoundingClientRect(),x=event?.clientX??rect.left+rect.width/2,y=event?.clientY??rect.top;
 const box=chartTooltip.getBoundingClientRect(),padding=8,gap=12,maxLeft=Math.max(padding,window.innerWidth-box.width-padding),maxTop=Math.max(padding,window.innerHeight-box.height-padding);
 let left=x+gap;if(left+box.width>window.innerWidth-padding)left=x-box.width-gap;
 let top=y+gap;if(top+box.height>window.innerHeight-padding)top=y-box.height-gap;
 chartTooltip.style.left=`${Math.max(padding,Math.min(left,maxLeft))}px`;
 chartTooltip.style.top=`${Math.max(padding,Math.min(top,maxTop))}px`;
}
function isRetargetedDialogClick(e){
 if(!e||!e.target||e.target.tagName!=='DIALOG')return false;
 const dlg=e.target;
 if(!dlg.hasAttribute('open'))return false;
 const rect=typeof dlg.getBoundingClientRect==='function'?dlg.getBoundingClientRect():null;
 if(!rect)return false;
 const isInside=typeof e.clientX==='number'&&typeof e.clientY==='number'&&e.clientX>=rect.left&&e.clientX<=rect.right&&e.clientY>=rect.top&&e.clientY<=rect.bottom;
 if(!isInside)return false;
 return Boolean((chartTooltip&&dlg.contains(chartTooltip))||(activeChartPoint&&dlg.contains(activeChartPoint)));
}
document.addEventListener('pointerover',e=>{if(e.pointerType==='touch')return;const point=e.target.closest?.('[data-chart-detail]');if(point)showChartTooltip(point,e);});
document.addEventListener('pointermove',e=>{if(e.pointerType==='touch')return;const point=e.target.closest?.('[data-chart-detail]');if(point)showChartTooltip(point,e);});
document.addEventListener('pointerout',e=>{const point=e.target.closest?.('[data-chart-detail]');if(point&&e.relatedTarget?.closest?.('[data-chart-detail]')!==point&&!chartTooltip?.contains(e.relatedTarget))hideChartTooltip();});
document.addEventListener('focusin',e=>{const point=e.target.closest?.('[data-chart-detail]');if(point)showChartTooltip(point);});
document.addEventListener('focusout',e=>{if(e.target.closest?.('[data-chart-detail]'))hideChartTooltip();});
document.addEventListener('click',e=>{
 const point=e.target.closest?.('[data-chart-detail]');
 if(point)showChartTooltip(point,e);
 else if(isRetargetedDialogClick(e)){
  if(activeChartPoint&&(!chartTooltip||chartTooltip.hidden)){
   showChartTooltip(activeChartPoint,e);
  } else if(!activeChartPoint&&typeof document!=='undefined'&&typeof document.elementFromPoint==='function'&&typeof e.clientX==='number'){
   const targetUnderPoint=document.elementFromPoint(e.clientX,e.clientY)?.closest?.('[data-chart-detail]');
   if(targetUnderPoint)showChartTooltip(targetUnderPoint,e);
  }
 }else if(!chartTooltip?.contains(e.target))hideChartTooltip();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideChartTooltip();});
document.addEventListener('scroll',e=>{if(!chartTooltip?.contains(e.target))hideChartTooltip();},true);
window.addEventListener('resize',hideChartTooltip);

function optimizerEquipment(){
 const isMixed=Boolean(settings.battery_mixed_sizes);
 const equip={
  panel_watts:Number($('optPanelWatts')?$('optPanelWatts').value:(settings.panel_watts??schema?.defaults?.panel_watts??400)),
  battery_module_kwh:Number($('optModuleKwh')?$('optModuleKwh').value:(settings.battery_module_kwh??schema?.defaults?.battery_module_kwh??5)),
  inverter_kw:Number($('optInverter')?$('optInverter').value:(settings.inverter_kw??schema?.defaults?.inverter_kw??4)),
  charge_kw:Number($('optCharge')?$('optCharge').value:(settings.charge_kw??schema?.defaults?.charge_kw??3)),
  discharge_kw:Number($('optDischarge')?$('optDischarge').value:(settings.discharge_kw??schema?.defaults?.discharge_kw??3))
 };
 if(isMixed){
  equip.battery_mixed_sizes=true;
  equip.battery_secondary_module_kwh=Number($('optSecondaryModuleKwh')?$('optSecondaryModuleKwh').value:(settings.battery_secondary_module_kwh??schema?.defaults?.battery_secondary_module_kwh??0));
 }
 return equip;
}
const roofLimitInputMap={maxSouth:'south',maxWest:'west',maxEast:'east',maxDormer:'dormer',maxFlat:'flat',maxSecondarySouth:'secondary_south'};
function validateRoofLimit(value){
  const s=String(value??'').trim();
  if(s==='')return {valid:false,error:'Must be a non-negative whole number.'};
  const num=Number(s);
  if(!Number.isFinite(num)||!Number.isInteger(num)||num<0){
    return {valid:false,error:'Must be a non-negative whole number.'};
  }
  return {valid:true,value:num};
}
function optimizerSearch(){
  const getVal=(id,fallback)=>{
    const el=$(id);
    if(!el||el.value==='')return fallback;
    const v=Number(el.value);
    return Number.isFinite(v)&&Number.isInteger(v)&&v>=0?v:fallback;
  };
  const currentLimits=optimizerPrefs?.panel_maxima||defaultRoofLimits;
  const maxima=(typeof activeResultProfile!=='undefined'&&activeResultProfile)?{...currentLimits}:{
    south:getVal('maxSouth',currentLimits.south),
    west:getVal('maxWest',currentLimits.west),
    east:getVal('maxEast',currentLimits.east),
    dormer:getVal('maxDormer',currentLimits.dormer),
    flat:getVal('maxFlat',currentLimits.flat),
    secondary_south:getVal('maxSecondarySouth',currentLimits.secondary_south)
  };
  return {
    objective:$('optObjective')?.value||optimizerPrefs?.objective||'autarky',
    battery_min_modules:$('optBatteryMin')?Number($('optBatteryMin').value):(optimizerPrefs?.battery_min_modules??0),
    battery_max_modules:$('optBatteryMax')?Number($('optBatteryMax').value):(optimizerPrefs?.battery_max_modules??4),
    panel_maxima:maxima,
    include_theoretical:$('includeTheoretical')?Boolean($('includeTheoretical').checked):(optimizerPrefs?.include_theoretical??true),
    load_scenario:$('filterLoad')?.value||optimizerPrefs?.load_scenario||settings.load_scenario||'ev'
  };
}
let optimizerStale=false;
function shouldBlockOptimizerForAcDraft(search=null,s=settings){
  if(!s)return false;
  if(typeof shouldBlockAcRecompute!=='function')return false;
  const effScenario=(search&&search.load_scenario)||($('filterLoad')?$('filterLoad').value:(optimizerPrefs?.load_scenario||s.load_scenario||'ev'));
  const effSettings={...s,load_scenario:effScenario};
  return Boolean(shouldBlockAcRecompute(effSettings));
}
function markOptimizerStale(message='Search inputs changed. Find the best configuration again to update results.'){
  optimizerStale=true;
  if($('paretoMethod')&&optimizerResult)$('paretoMethod').textContent=message;
  if($('bestConfiguration')){
    const btn=$('bestConfiguration').querySelector?.('[data-apply-best]');
    if(btn){btn.disabled=true;btn.setAttribute?.('disabled','');}
    $('bestConfiguration').classList?.add?.('stale');
    if($('bestConfiguration').innerHTML&&$('bestConfiguration').innerHTML.includes('data-apply-best')&&!$('bestConfiguration').innerHTML.includes('disabled')){
      $('bestConfiguration').innerHTML=$('bestConfiguration').innerHTML.replace('<button type="button" data-apply-best','<button type="button" data-apply-best disabled');
    }
  }
  if($('candidates')){
    const btns=$('candidates').querySelectorAll?.('[data-use]');
    btns?.forEach(b=>{b.disabled=true;b.setAttribute?.('disabled','');});
    $('candidates').classList?.add?.('stale');
    if($('candidates').innerHTML&&$('candidates').innerHTML.includes('data-use')&&!$('candidates').innerHTML.includes('disabled')){
      $('candidates').innerHTML=$('candidates').innerHTML.replaceAll('<button type="button" data-use','<button type="button" data-use disabled');
    }
  }
  if($('paretoChart'))$('paretoChart').classList?.add?.('stale');
}
function optimizerStateFingerprint(){return inputFingerprint(settings,{})+'|'+JSON.stringify(optimizerEquipment())+'|'+JSON.stringify(optimizerSearch());}
function saveOptimizerPrefs(){
  const s=optimizerSearch();
  optimizerPrefs={...optimizerPrefs,...s,_loadScenarioExplicit:true,panel_maxima:s.panel_maxima,chart_x:$('chartX')?.value||optimizerPrefs.chart_x,chart_y:$('chartY')?.value||optimizerPrefs.chart_y};
  const {_loadScenarioExplicit,...saved}=optimizerPrefs;
  try{if(typeof localStorage!=='undefined'&&localStorage&&typeof localStorage.setItem==='function'){localStorage.setItem('pv-lab-optimizer-v1',JSON.stringify(saved));}}catch{}
}
function clearOptimizerResults(message='Search inputs changed. Find the best configuration again to update results.'){
  optimizerRequestId++;
  optimizerResult=null;
  optimizerStale=false;
  if($('bestConfiguration')){$('bestConfiguration').innerHTML='';$('bestConfiguration').classList?.remove?.('stale');}
  if($('candidates')){$('candidates').innerHTML='';$('candidates').classList?.remove?.('stale');}
  if($('paretoChart')){$('paretoChart').innerHTML='';$('paretoChart').classList?.remove?.('stale');}
  if($('paretoMethod'))$('paretoMethod').textContent=message;
  if($('recomputePareto'))$('recomputePareto').disabled=false;
}
function optimizerChanged(){clearOptimizerResults();saveOptimizerPrefs();if(typeof markSolarSizingStale==='function')markSolarSizingStale();}
const roofComparisonRows=[
  {key:'south',setting:'south_panels',label:'South',id:'maxSouth'},
  {key:'west',setting:'west_panels',label:'West',id:'maxWest'},
  {key:'east',setting:'east_panels',label:'East',id:'maxEast'},
  {key:'dormer',setting:'dormer_panels',label:'Dormer',id:'maxDormer'},
  {key:'flat',setting:'flat_panels',label:'Flat roof',id:'maxFlat'},
  {key:'secondary_south',setting:'secondary_south_panels',label:'Secondary south',id:'maxSecondarySouth'}
];
function renderRoofLimitsComparison(){
  const container=$('roofLimitsComparison');
  if(!container)return;
  const limits=optimizerPrefs?.panel_maxima||defaultRoofLimits;
  const items=((typeof activeResultProfile!=='undefined'&&activeResultProfile)?profileRoofRows():roofComparisonRows).map(r=>{
    const count=Number((typeof activeResultProfile!=='undefined'&&activeResultProfile)?settings.roof_panels?.[r.key]||0:settings[r.setting]||0);
    const limit=Number(limits[r.key]??defaultRoofLimits[r.key]??0);
    const exceeded=count>limit;
    const delta=count-limit;
    return {...r,count,limit,exceeded,delta};
  });
  const exceededItems=items.filter(i=>i.exceeded);
  let summary='';
  if(exceededItems.length>0){
    const names=exceededItems.map(i=>`${i.label} (+${nf(i.delta)} ${i.delta===1?'panel':'panels'})`).join(', ');
    summary=`<div class="notice roof-limit-alert" role="status"><strong>⚠ Advisory layout check warning:</strong> Selected scenario panel count exceeds estimated physical capacity on: <b>${esc(names)}</b>. Physical setbacks and roof area should be confirmed with the installer. Modeled energy and financial results continue using your selected panel counts without clamping.</div>`;
  }else{
    summary=`<p class="roof-limit-ok" role="status">✓ All selected scenario panel counts are within estimated physical roof limits.</p>`;
  }
  const tableRows=items.map(i=>`<tr><td>${esc(i.label)}</td><td>${nf(i.count)} panels</td><td>${nf(i.limit)} max</td><td>${i.exceeded?`<strong class="roof-status-exceeded" role="alert">⚠ Exceeds limit by ${nf(i.delta)} ${i.delta===1?'panel':'panels'} (${nf(i.count)} of ${nf(i.limit)})</strong>`:i.count===i.limit?`<span class="roof-status-at-limit">At limit (${nf(i.count)} of ${nf(i.limit)})</span>`:`<span class="roof-status-ok">Within limit (${nf(i.count)} of ${nf(i.limit)})</span>`}</td></tr>`).join('');
  container.innerHTML=`${summary}<div class="table-wrap"><table><thead><tr><th>Roof section</th><th>Selected in scenario</th><th>Estimated limit</th><th>Layout check status</th></tr></thead><tbody>${tableRows}</tbody></table></div>`;
}
function handleRoofLimitInput(e,roofKey){
  const validation=validateRoofLimit(e.target.value);
  const status=$('roofLimitsStatus');
  if(!validation.valid){
    e.target.setAttribute('aria-invalid','true');
    if(status)status.textContent=`${title(roofKey)} limit: ${validation.error}`;
    return;
  }
  e.target.removeAttribute('aria-invalid');
  if(status)status.textContent='';
  optimizerPrefs.panel_maxima[roofKey]=validation.value;
  saveOptimizerPrefs();
  optimizerChanged();
  renderRoofLimitsComparison();
}
function resetRoofCapacityLimits(){
  optimizerPrefs.panel_maxima={...defaultRoofLimits};
  saveOptimizerPrefs();
  for(const [id,roof] of Object.entries(roofLimitInputMap)){
    const el=$(id);
    if(el){
      el.value=defaultRoofLimits[roof];
      el.removeAttribute('aria-invalid');
    }
  }
  const status=$('roofLimitsStatus');
  if(status)status.textContent='Roof limits reset to estimated initial values.';
  optimizerChanged();
  renderRoofLimitsComparison();
}
function initializeOptimizer(){
  if(!schema)return;
  const s=settings,loadScenario=optimizerPrefs._loadScenarioExplicit?optimizerPrefs.load_scenario:s.load_scenario;
  optimizerPrefs.load_scenario=loadScenario;
  const values={
    optPanelWatts:s.panel_watts??schema.defaults?.panel_watts,
    optModuleKwh:s.battery_module_kwh,
    optBatteryMin:optimizerPrefs.battery_min_modules,
    optBatteryMax:optimizerPrefs.battery_max_modules,
    optInverter:s.inverter_kw??schema.defaults?.inverter_kw,
    optCharge:s.charge_kw??schema.defaults?.charge_kw,
    optDischarge:s.discharge_kw??schema.defaults?.discharge_kw,
    filterLoad:loadScenario,
    optObjective:optimizerPrefs.objective,
    maxSouth:optimizerPrefs.panel_maxima.south,
    maxWest:optimizerPrefs.panel_maxima.west,
    maxEast:optimizerPrefs.panel_maxima.east,
    maxDormer:optimizerPrefs.panel_maxima.dormer,
    maxFlat:optimizerPrefs.panel_maxima.flat,
    maxSecondarySouth:optimizerPrefs.panel_maxima.secondary_south,
    includeTheoretical:optimizerPrefs.include_theoretical,
    chartX:optimizerPrefs.chart_x,
    chartY:optimizerPrefs.chart_y
  };
  for(const [id,v] of Object.entries(values)){
    const el=$(id);
    if(el&&v!=null){
      if(el.type==='checkbox')el.checked=Boolean(v);
      else el.value=v;
    }
  }
  const settingIds={optPanelWatts:'panel_watts',optInverter:'inverter_kw',optCharge:'charge_kw',optDischarge:'discharge_kw'};
  for(const [id,key] of Object.entries(settingIds)){
    const el=$(id),record=schema.records?.find(r=>r.name===key);
    if(!el||!record?.bounds)continue;
    el.min=record.bounds[0]??0;
    if(record.bounds[1]!=null)el.max=record.bounds[1];
    else el.removeAttribute('max');
  }
  const capacityRecord=schema.records?.find(r=>r.name==='battery_module_kwh');
  if(!capacityRecord&&$('paretoMethod')){
    $('paretoMethod').textContent='This server does not expose usable battery module capacity. Reload the server to enable the optimizer.';
    if($('recomputePareto'))$('recomputePareto').disabled=true;
  }
  renderRoofLimitsComparison();
}
function axisValue(r,key){if(key==='capex_eur')return {min:r.finance?.capex_eur??r.capex_eur,median:r.finance?.capex_eur??r.capex_eur,max:r.finance?.capex_eur??r.capex_eur};if(key==='panel_count'){const s=r.settings||{},v=panelKeys.reduce((n,k)=>n+(Number(s[k])||0),0);return {min:v,median:v,max:v};}if(key==='battery_modules'){const v=Number(r.settings?.battery_modules??r.battery_modules??0);return {min:v,median:v,max:v};}return r.metrics?.[key]||null;}
function axisLabel(key){return ({npv_eur:'NPV (€)',autarky:'Autarky (%)',self_consumption:'Self-consumption (%)',payback_years:'Simple payback (years)',discounted_payback_years:'Discounted payback (years)',capex_eur:'Installed cost benchmark (€)',panel_count:'Total panels',battery_modules:'Battery modules'})[key]||key;}
function axisNumber(v,key){return key==='autarky'||key==='self_consumption'?`${nf(v*100,1)}%`:key==='npv_eur'||key==='capex_eur'?fmt(v,'eur'):key.includes('payback')?fmt(v,'years'):nf(v);}
function renderCandidates(){if(!optimizerResult)return;const response=optimizerResult;const routeText=typeof routeDisplayForCandidate==='function'?routeDisplayForCandidate:(()=>'—');let rows=(response.candidates||[]).filter(r=>optimizerSearch().include_theoretical||!r.theoretical);const best=response.best_candidate||{},selectedId=response.recommended_id;
 const isStale=Boolean(optimizerStale||optimizerFingerprint!==optimizerStateFingerprint()||shouldBlockOptimizerForAcDraft());
 if(isStale){
  markOptimizerStale();
 }
 const found=response.search||{},exact=found.exact===true,metricKey=response.objective||optimizerSearch().objective,metricLabel=({autarky:'autarky',npv_eur:'NPV',self_consumption:'self-consumption',payback_years:'simple payback',discounted_payback_years:'discounted payback'})[metricKey]||metricKey;
 const isNpvObj=metricKey==='npv_eur',isPbObj=metricKey.includes('payback');
 const bestObjectiveStat=isNpvObj?'ensemble-mean':(isPbObj?'ensemble-mean cash flow':'weather-year median');
 if($('paretoMethod'))$('paretoMethod').textContent=`${exact?'Exact integer search':'Best found within the search budget'} · ${nf(found.evaluated_count)} configurations evaluated${found.search_space_size!=null?` of ${nf(found.search_space_size)}`:''}. Best by ${bestObjectiveStat} ${metricLabel}.`;
 const fullSettings=best.settings||{},totalPanels=panelKeys.reduce((n,k)=>n+(Number(fullSettings[k])||0),0),moduleKwh=Number(fullSettings.battery_module_kwh??optimizerEquipment().battery_module_kwh),moduleCount=Number(fullSettings.battery_modules??0),breakdown=panelKeys.map(k=>`${title(k).replace(' Panels','')}: ${nf(fullSettings[k]||0)}`).join(' · '),metricRange=best.metrics?.[metricKey],metricType=metricKey.includes('payback')?'years':metricKey==='npv_eur'?'eur':metricKey==='autarky'||metricKey==='self_consumption'?'pct':'number',explicitBestMeanPb=isPbObj?(metricKey==='discounted_payback_years'?(best.finance?.pv_incremental?.discounted_payback_of_mean_cash_flows!==undefined?best.finance.pv_incremental.discounted_payback_of_mean_cash_flows:best.metrics?.discounted_payback_of_mean_cash_flows):(best.finance?.pv_incremental?.simple_payback_of_mean_cash_flows!==undefined?best.finance.pv_incremental.simple_payback_of_mean_cash_flows:best.metrics?.simple_payback_of_mean_cash_flows)):undefined,objectiveVal=isNpvObj?(metricRange?.mean??metricRange?.median):(isPbObj?(explicitBestMeanPb!==undefined?explicitBestMeanPb:metricRange?.median):metricRange?.median),objectiveTag=isNpvObj?'mean':(isPbObj&&explicitBestMeanPb!==undefined?'ensemble-mean CF':'median'),objectiveDisplay=objectiveVal==null?'Not reached in the simulated horizon':`${fmt(objectiveVal,metricType)} ${objectiveTag} · weather range ${range(metricRange,metricType)}`,householdLabel=({ev:'electrified + EV',electrified:(typeof dayhourScenarioLabel==='function'?dayhourScenarioLabel('electrified').toLowerCase():'electrified · gas eliminated'),baseline:'historical electricity'})[optimizerSearch().load_scenario]||optimizerSearch().load_scenario;
 const requestedMaxima=found.requested_panel_maxima||{},atPositiveRoofLimit=Object.entries(found.best_at_panel_maxima||{}).some(([k,v])=>v&&Number(requestedMaxima[k])>0),atBatteryLimit=found.best_at_battery_maximum&&Number(found.battery_max_modules)>0;
 const bestApplyDisabled=isStale?' disabled':'';
 if($('bestConfiguration')){$('bestConfiguration').innerHTML=`<div class="recommended-card"><p class="eyebrow">${exact?'OPTIMAL WITHIN SELECTED BOUNDS':'BEST FOUND'}</p><h3>Best configuration for ${esc(householdLabel)}</h3><p><strong>${nf(totalPanels)} panels · ${formatBatteryComposition(best)}</strong></p><p>${esc(breakdown)}</p><p>Best ${bestObjectiveStat} ${esc(metricLabel)}: <strong>${objectiveDisplay}</strong> · Investment benchmark ${fmt(best.finance?.capex_eur??best.capex_eur,'eur')}</p><p>Routing priority: <strong>${esc(routeText(best))}</strong></p><button type="button" data-apply-best${bestApplyDisabled}>Apply this configuration</button>${(atBatteryLimit||atPositiveRoofLimit)?'<p class="muted">The best result reaches a panel or battery limit. Increase that limit to explore larger systems.</p>':''}</div>`;
 $('bestConfiguration').onclick=e=>{if(!e.target.closest('[data-apply-best]'))return;if(optimizerStale||optimizerFingerprint!==optimizerStateFingerprint()||shouldBlockOptimizerForAcDraft()){markOptimizerStale('Inputs changed since search. Re-run configuration search before applying.');return;}applyOptimizedSettings(best);};}
 const layout=r=>{const s=r.settings||{};return `${panelKeys.map(k=>`${title(k).replace(' Panels','')} ${nf(s[k]||0)}`).join(' · ')}`;};
 const paybackObjective=metricKey.includes('payback'),valueOf=r=>{if(isNpvObj)return axisValue(r,'npv_eur')?.mean??axisValue(r,'npv_eur')?.median;if(isPbObj){const meanPb=metricKey==='discounted_payback_years'?(r.finance?.pv_incremental?.discounted_payback_of_mean_cash_flows!==undefined?r.finance.pv_incremental.discounted_payback_of_mean_cash_flows:r.metrics?.discounted_payback_of_mean_cash_flows):(r.finance?.pv_incremental?.simple_payback_of_mean_cash_flows!==undefined?r.finance.pv_incremental.simple_payback_of_mean_cash_flows:r.metrics?.simple_payback_of_mean_cash_flows);return meanPb!==undefined?meanPb:axisValue(r,metricKey)?.median;}return axisValue(r,metricKey)?.median;};rows.sort((a,b)=>{if(a.id===selectedId)return -1;if(b.id===selectedId)return 1;const av=valueOf(a),bv=valueOf(b);if(av==null)return bv==null?0:1;if(bv==null)return -1;return (paybackObjective?1:-1)*(av-bv);});
 if($('candidates')){$('candidates').innerHTML=rows.length?`<table><thead><tr><th>Roof layout</th><th>Panels</th><th>Battery modules</th><th>Usable battery</th><th>Benchmark cost</th><th>NPV</th><th>Payback</th><th>Autarky</th><th>Routing</th><th></th></tr></thead><tbody>${rows.map(r=>{
  const isMix=Boolean(r.settings?.battery_mixed_sizes??r.battery_mixed_sizes);
  const secM=isMix?Number(r.settings?.battery_secondary_modules??r.battery_secondary_modules??0):0;
  const totM=Number(r.settings?.battery_modules??r.battery_modules??0);
  const firstM=Math.max(0,totM-secM);
  const modLabel=isMix?(firstM>0&&secM>0?`${firstM} + ${secM} (${totM})`:`${totM}`):`${nf(axisValue(r,'battery_modules')?.median)}`;
  return `<tr class="${r.id===selectedId?'frontier':''}"><td class="layout-cell">${esc(layout(r))}${r.theoretical?' · theoretical':''}</td><td>${nf(axisValue(r,'panel_count')?.median)}</td><td>${modLabel}</td><td>${nf(r.battery_kwh,1)} kWh</td><td>${fmt(r.finance?.capex_eur??r.capex_eur,'eur')}</td><td>${range(r.metrics?.npv_eur,'eur')}</td><td>${range(r.metrics?.payback_years,'years')}</td><td>${range(r.metrics?.autarky,'pct')}</td><td>${esc(routeText(r))}</td><td><button data-use="${esc(r.id)}"${bestApplyDisabled}>Use</button></td></tr>`;
 }).join('')}</tbody></table>`:'<p class="muted">No configurations are available for these limits.</p>';
 $('candidates').onclick=e=>{const id=e.target.dataset.use;if(!id)return;if(optimizerStale||optimizerFingerprint!==optimizerStateFingerprint()||shouldBlockOptimizerForAcDraft()){markOptimizerStale('Inputs changed since search. Re-run configuration search before applying.');return;}const row=rows.find(r=>r.id===id);if(row)applyOptimizedSettings(row);};}
 if(isStale){
  if($('bestConfiguration'))$('bestConfiguration').classList?.add?.('stale');
  if($('candidates'))$('candidates').classList?.add?.('stale');
  if($('paretoChart'))$('paretoChart').classList?.add?.('stale');
 }else{
  if($('bestConfiguration'))$('bestConfiguration').classList?.remove?.('stale');
  if($('candidates'))$('candidates').classList?.remove?.('stale');
  if($('paretoChart'))$('paretoChart').classList?.remove?.('stale');
 }
 if($('chartX')&&$('chartY')&&$('paretoChart')){
  const central=(r,key)=>{
   const env=axisValue(r,key);
   if(key==='npv_eur')return env?.mean??env?.median;
   const pbKey=key==='payback_years'?'simple_payback_of_mean_cash_flows':key==='discounted_payback_years'?'discounted_payback_of_mean_cash_flows':null;
   return pbKey&&r.metrics?.[pbKey]!==undefined?r.metrics[pbKey]:env?.median;
  };
  const caption=key=>`${axisLabel(key)}${key==='npv_eur'?' · scenario mean':key.includes('payback')?' · ensemble-mean cash flows':''}`;
  const xkey=$('chartX').value,ykey=$('chartY').value,points=rows.map(r=>({r,x:axisValue(r,xkey),y:axisValue(r,ykey),xValue:central(r,xkey),yValue:central(r,ykey)})).filter(p=>p.xValue!=null&&p.yValue!=null);if(!points.length){$('paretoChart').innerHTML='<p class="muted">No configurations have values for both selected axes.</p>';return;}
  const allx=points.flatMap(p=>[p.x.min,p.xValue,p.x.max]).filter(Number.isFinite),ally=points.flatMap(p=>[p.y.min,p.yValue,p.y.max]).filter(Number.isFinite),xmin=Math.min(...allx),xmax=Math.max(...allx),ymin=Math.min(...ally),ymax=Math.max(...ally),x=v=>65+(v-xmin)/(xmax-xmin||1)*600,y=v=>215-(v-ymin)/(ymax-ymin||1)*180;
  $('paretoChart').innerHTML=`<svg viewBox="0 0 720 280" role="img" aria-label="${esc(caption(xkey))} versus ${esc(caption(ykey))}"><line x1="65" y1="215" x2="685" y2="215" stroke="#b7c8bc"/><line x1="65" y1="30" x2="65" y2="215" stroke="#b7c8bc"/>${points.map(p=>`<line x1="${x(Number.isFinite(p.x.min)?p.x.min:p.xValue)}" x2="${x(Number.isFinite(p.x.max)?p.x.max:p.xValue)}" y1="${y(p.yValue)}" y2="${y(p.yValue)}" stroke="#c2d5c5" stroke-width="2"/><line x1="${x(p.xValue)}" x2="${x(p.xValue)}" y1="${y(Number.isFinite(p.y.min)?p.y.min:p.yValue)}" y2="${y(Number.isFinite(p.y.max)?p.y.max:p.yValue)}" stroke="#c2d5c5" stroke-width="2"/><circle cx="${x(p.xValue)}" cy="${y(p.yValue)}" r="${p.r.id===selectedId?7:4}" fill="${p.r.id===selectedId?'#217a61':p.r.theoretical?'#d97932':'#9dab9e'}"><title>${esc(layout(p.r))} · Storage: ${esc(formatBatteryComposition(p.r,{short:true}))} · Routing: ${esc(routeText(p.r))} · x ${axisNumber(p.xValue,xkey)} · y ${axisNumber(p.yValue,ykey)}</title></circle>`).join('')}<text x="65" y="245" font-size="11">${axisNumber(xmin,xkey)}</text><text x="600" y="245" font-size="11">${axisNumber(xmax,xkey)}</text><text x="250" y="270" font-size="11">${esc(caption(xkey))}</text><text x="4" y="35" font-size="11">${axisNumber(ymax,ykey)}</text><text x="4" y="215" font-size="11">${axisNumber(ymin,ykey)}</text><text x="7" y="122" font-size="11" transform="rotate(-90 12 122)">${esc(caption(ykey))}</text></svg>`;
 }
}
function applyOptimizedSettings(row){
 if(!optimizerResult||optimizerFingerprint!==optimizerStateFingerprint()||shouldBlockOptimizerForAcDraft()||optimizerStale){markOptimizerStale('Inputs changed since search. Re-run configuration search before applying.');return;}
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile){if(optimizerResult._presentation?.profile_id!==householdClient.profileId||optimizerResult._presentation?.input_revision!==householdClient.revision){markOptimizerStale('Profile revision changed. Re-run search.');return;}profileCounts(row);}
 const rowSettings=row.settings||{};
 const isMixed=Boolean(rowSettings.battery_mixed_sizes??row.battery_mixed_sizes??false);
 settings={...schema.defaults,...rowSettings};
 settings.battery_mixed_sizes=isMixed;
 settings.battery_modules=Number(rowSettings.battery_modules??row.battery_modules??0);
 settings.battery_module_kwh=Number(rowSettings.battery_module_kwh??row.battery_module_kwh??settings.battery_module_kwh??0);
 settings.battery_secondary_modules=isMixed?Number(rowSettings.battery_secondary_modules??row.battery_secondary_modules??0):0;
 settings.battery_secondary_module_kwh=Number(rowSettings.battery_secondary_module_kwh??row.battery_secondary_module_kwh??settings.battery_secondary_module_kwh??0);
 settings.battery_kwh=Number(rowSettings.battery_kwh??row.battery_kwh??0);

 if(settings.battery_modules===0){
  settings.battery_kwh=0.0;
  settings.battery_secondary_modules=0;
 }

 normalizeBatterySettings(settings,'battery_modules');
 if(typeof clearQuote==='function')clearQuote();
 if(typeof renderLines==='function')renderLines();
 if(typeof syncQuoteFields==='function')syncQuoteFields();
 if(typeof renderControls==='function')renderControls();
 const batterySettingsList=(typeof BATTERY_SETTINGS!=='undefined')?BATTERY_SETTINGS:['battery_kwh','battery_modules','battery_module_kwh','battery_mixed_sizes','battery_secondary_module_kwh','battery_secondary_modules'];
 for(const linked of batterySettingsList){
  if(typeof syncSetting==='function')syncSetting(linked);
  if(typeof updateControlModifiedState==='function')updateControlModifiedState(linked);
 }
 if(typeof updateControlModifiedState==='function')updateControlModifiedState('battery_group');
 if(typeof syncOptimizerEquipmentInputs==='function')syncOptimizerEquipmentInputs();
 if(typeof syncSolarBatteryStep==='function')syncSolarBatteryStep();
 if(typeof updateCurrentSystemSummary==='function')updateCurrentSystemSummary();
 if(typeof refreshConditionalControls==='function')refreshConditionalControls();

 optimizerResult=null;
 if($('paretoMethod'))$('paretoMethod').textContent='Configuration applied. Recalculating the selected system…';
 recompute({replace:true});
 switchView('overview');
}
function saveCurrentScenario(){
 if(!current)return;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(settings)){
  showError(new Error('Cannot save scenario: active AC heating inputs contain invalid drafts. Fix errors before saving.'));
  return;
 }
 const name=prompt('Scenario name',`${title(settings.load_scenario)} · ${nf(current.result.layout.kwp,1)} kWp`);
 if(!name)return;
 saved.push({...((typeof activeResultProfile!=='undefined'&&activeResultProfile)?{profile_id:householdClient.profileId,revision:householdClient.revision}:{}),name,settings:deepClone(settings),quote:deepClone(quote),result:deepClone(current.result)});
 try{if(typeof localStorage!=='undefined'&&localStorage&&typeof localStorage.setItem==='function')localStorage.setItem('pv-lab-scenarios-v1',JSON.stringify(saved));}catch{}
 renderSaved();
}
function renderSaved(){
 const article=$('savedScenariosArticle');
 const target=$('saved');
 if(!target)return;
 target.onclick=e=>{
  if(e.target.dataset.saveCurrent!==undefined||e.target.closest?.('[data-save-current]')){
   saveCurrentScenario();
   return;
  }
  if(e.target.dataset.restore!==undefined){
   const s=saved[Number(e.target.dataset.restore)];
   if(!s)return;
   if(typeof activeResultProfile!=='undefined'&&activeResultProfile){if(s.profile_id!==householdClient.profileId||s.revision!==householdClient.revision){showError(Error('Saved configuration revision differs. Re-run sizing for this revision.'));return;}profileCounts({settings:s.settings});}
   const nextSettings=prepareScenarioSettings(s.settings,false);
   if(!Object.hasOwn(s.settings||{},'battery_mixed_sizes')){
    nextSettings.battery_mixed_sizes=false;
    nextSettings.battery_secondary_modules=0;
   }
   normalizeBatterySettings(nextSettings,Object.hasOwn(s.settings,'battery_kwh')?'battery_kwh':'battery_modules');
   const nextQuote=deepClone(s.quote||{line_items:[]});
   discardAcDrafts();
   settings=nextSettings;
   quote=nextQuote;
   renderControls();renderLines();syncQuoteFields();
   recompute({replace:true});
  }else if(e.target.dataset.deleteSaved!==undefined){
   const idx=Number(e.target.dataset.deleteSaved);
   saved.splice(idx,1);
   try{if(typeof localStorage!=='undefined'&&localStorage&&typeof localStorage.setItem==='function')localStorage.setItem('pv-lab-scenarios-v1',JSON.stringify(saved));}catch{}
   renderSaved();
  }
 };
 const head=article?article.querySelector('.section-head'):null;
 const desc=article?article.querySelector('p.muted'):null;
 const clearBtn=$('clearSaved');
 if(!saved.length){
  if(head)head.hidden=true;
  if(desc)desc.hidden=true;
  if(clearBtn)clearBtn.hidden=true;
  target.innerHTML=`<div class="saved-scenarios-empty-row"><span>No saved scenarios</span><button type="button" class="action-btn-small" data-save-current="true">Save current scenario</button></div>`;
  return;
 }
 if(head)head.hidden=false;
 if(desc)desc.hidden=false;
 if(clearBtn)clearBtn.hidden=false;
 target.innerHTML=`<div class="table-wrap"><table class="saved-scenarios-table">
  <caption>Saved scenarios · ${summaryScope==='package'?'Whole house':'Solar + battery'} · ${summaryCash==='loan'?'After loan':'Before loan'}</caption>
  <thead><tr><th>Scenario</th><th>System</th><th>Outlay</th><th>NPV</th><th>Discounted payback</th><th>Autarky</th><th>Actions</th></tr></thead>
  <tbody>${saved.map((scenario,index)=>{
   const result=scenario.result;
   const metrics=result?getScopeCashMetrics(result,summaryScope,summaryCash):null;
    const savedNpvVal=metrics?.npvObj?.mean??metrics?.npvObj?.median??metrics?.npvObj;
    const savedPbOfMean=metrics?.pbObj?.discounted_payback_of_mean_cash_flows!==undefined?metrics.pbObj.discounted_payback_of_mean_cash_flows:(metrics?.pbObj?.median??(typeof metrics?.pbObj==='number'?metrics.pbObj:null));
    const savedPbDisplay=(savedPbOfMean!=null&&Number.isFinite(Number(savedPbOfMean)))?fmt(Number(savedPbOfMean),'years'):'Not reached';
   return `<tr><th scope="row">${esc(scenario.name)}</th><td>${nf(result?.layout?.kwp,1)} kWp · ${nf(scenario.settings?.battery_kwh,1)} kWh</td><td>${fmt(metrics?.capexVal,'eur')}</td><td>${fmt(savedNpvVal,'eur')}</td><td>${savedPbDisplay}</td><td>${range(metrics?.autarky,'pct')}</td><td><button type="button" class="action-btn-small" data-restore="${index}">Restore</button> <button type="button" class="quiet" data-delete-saved="${index}" aria-label="Delete ${esc(scenario.name)}">Remove</button></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
const textFields=['installer','module_model','inverter_model','battery_model','datasheet_urls','payment_schedule','layout_setbacks','warranties','guaranteed_yield_terms','monitoring_fees','inverter_replacement_terms','financing_terms','degradation_curves','power_curves'];
function syncQuotePricingDescription(){
 const help=$('quotePriceBasisHelp'),qTotal=$('quoteTotal');
 if(!help&&!qTotal)return;
 const itemizedActive=isItemizedPricingActive();
 const hasItemizedLines=hasPopulatedItemizedOffer(quote, current?.itemized_total_eur);
 const hasManualQuote=settings.quote_total!=null;
 const capexEur=current?.result?.finance?.capex_eur;
 if(help){
  if(itemizedActive){
   help.textContent='Itemized lines determine the active system cost and override the left-menu turnkey quote.';
  }else if(hasItemizedLines){
   help.textContent=hasManualQuote?'Itemized lines kept as reference only; active system cost uses manual turnkey quote.':'Itemized lines kept as reference only; active system cost uses modeled benchmark formula.';
  }else{
   help.textContent=hasManualQuote?'No itemized lines entered; active system cost uses manual turnkey quote.':'No itemized lines entered; active system cost uses modeled benchmark formula.';
  }
 }
 if(qTotal){
  if(!hasItemizedLines){
   qTotal.innerHTML=hasManualQuote?`No itemized offer entered. Active system cost: <b>${fmt(capexEur??settings.quote_total,'eur')}</b> <span class="tag">Manual quote</span>`:(capexEur!=null?`No itemized offer entered. Active system cost: <b>${fmt(capexEur,'eur')}</b> <span class="tag">Modeled benchmark</span>`:'No itemized offer entered.');
  }else if(itemizedActive){
   qTotal.innerHTML=`Itemized total including VAT: <b>${fmt(current?.itemized_total_eur,'eur')}</b> <span class="tag">Active system cost</span>`;
  }else{
   qTotal.innerHTML=hasManualQuote?`Itemized reference total: <b>${fmt(current?.itemized_total_eur,'eur')}</b> <span class="tag">Reference only</span> · Active cost: <b>${fmt(capexEur??settings.quote_total,'eur')}</b> (manual turnkey quote)`:`Itemized reference total: <b>${fmt(current?.itemized_total_eur,'eur')}</b> <span class="tag">Reference only (Modeled benchmark formula active)</span>`;
  }
 }
}
function buildQuoteFields(){const qf=$('quoteFields');if(!qf)return;qf.innerHTML=`<label>Price basis<select data-quote="price_basis" id="quotePriceBasis"><option value="itemized">Itemized contractor quote (lines determine total cost)</option><option value="modeled">Modeled sizing / manual quote (lines kept as reference only)</option></select><small id="quotePriceBasisHelp" class="muted"></small></label>`+textFields.map(k=>`<label>${title(k)}<textarea data-quote="${k}" placeholder="Not provided"></textarea></label>`).join('')+`<label>Battery nameplate (kWh)<input type="number" data-quote="battery_nameplate_kwh" step="any" placeholder="Not provided"></label><label>Usable DoD (fraction, 0–1)<input type="number" data-quote="battery_dod" step="any" min="0" max="1" placeholder="Not provided"></label>`;qf.insertAdjacentHTML('beforeend',['roundtrip_efficiency','continuous_charge_kw','continuous_discharge_kw'].map(k=>`<label>${title(k)}<input type="number" data-quote="${k}" step="any" placeholder="Not provided"></label>`).join(''));document.querySelectorAll('[data-quote]').forEach(el=>{const onMod=()=>{quote[el.dataset.quote]=el.value;schedule();};el.oninput=onMod;el.onchange=onMod;});syncQuotePricingDescription();}
function syncQuoteFields(){document.querySelectorAll('[data-quote]').forEach(el=>{const value=String(quote[el.dataset.quote]??(el.tagName==='SELECT'?(el.options[0]?.value??''):''));if(el.value!==value)el.value=value;});syncQuotePricingDescription();}
function renderLines(){const ql=$('quoteLines');if(!ql)return;ql.innerHTML=(quote?.line_items||[]).map((row,i)=>`<div class="quote-line"><input data-line="${i}" data-field="description" value="${esc(row.description||'')}" placeholder="Description" aria-label="Line description"><input data-line="${i}" data-field="quantity" type="number" min="0" value="${row.quantity??1}" aria-label="Quantity"><input data-line="${i}" data-field="net_eur" type="number" min="0" step="any" value="${row.net_eur??''}" placeholder="Net €" aria-label="Net euros"><input data-line="${i}" data-field="vat" type="number" min="0" max="1" step=".01" value="${row.vat??.1}" aria-label="VAT fraction"><button data-remove="${i}" aria-label="Remove line">×</button></div>`).join('');ql.oninput=e=>{if(e.target.dataset.line===undefined)return;quote.line_items[Number(e.target.dataset.line)][e.target.dataset.field]=e.target.value;schedule();};ql.onclick=e=>{if(e.target.dataset.remove===undefined)return;quote.line_items.splice(Number(e.target.dataset.remove),1);renderLines();schedule();};}
function csvSafeCell(value){const text=String(value??'');return /^[\s]*[=+@-]|^[\t\r\n]/.test(text)?"'"+text:text;}
function download(data,name,type='application/json'){if(typeof activeResultProfile!=='undefined'&&activeResultProfile&&type==='text/csv')data='"Profile revision","'+csvSafeCell(householdClient.revision)+'"\n"Limitations","'+csvSafeCell((current?._presentation?.limitations||[]).join(' ')).replaceAll('"','""')+'"\n'+data;const url=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
let comparisonToken=0;
let comparisonCache=null;
let comparisonKey='';
let comparisonInFlight=false;
let comparisonPendingReq=null;
let comparisonError=null;

let benchmarkCache=null;
let benchmarkFingerprint='';
let benchmarkInFlight=false;

function invalidateQuoteComparison(){
 comparisonToken++;
 comparisonCache=null;
 comparisonKey='';
 comparisonPendingReq=null;
 comparisonError=null;
 comparisonInFlight=false;
 benchmarkInFlight=false;
 benchmarkCache=null;
 benchmarkFingerprint='';
}

function getComparisonPayload(s, q, isOfferActive, offerTotal, opts={}){
 const isBench=Boolean(isOfferActive);
 const target=isBench?'bench':'offer';
 const hasItemized=(typeof opts?.hasItemized==='boolean')
  ?opts.hasItemized
  :hasPopulatedItemizedOffer(q, opts?.itemizedTotal ?? current?.itemized_total_eur);
 const rawVat=(typeof opts?.rawVat==='boolean')
  ?opts.rawVat
  :(typeof settings!=='undefined'&&typeof settings?.quote_vat_included==='boolean'
    ?settings.quote_vat_included
    :(typeof s?.quote_vat_included==='boolean'?s.quote_vat_included:true));

 let evalSettings=deepClone(s);
 delete evalSettings.hasItemized;
 evalSettings.quote_vat_included=rawVat;
 if(schema&&schema.defaults){
  for(const k of Object.keys(evalSettings)){
   if(!Object.hasOwn(schema.defaults,k))delete evalSettings[k];
  }
 }

 let evalQuote;

 if(target==='bench'){
  evalSettings.quote_total=null;
  evalSettings.quote_vat_included=rawVat;
  if(s.battery_kwh!=null)evalSettings.battery_kwh=Number(s.battery_kwh);
  if(s.roundtrip_efficiency!=null)evalSettings.roundtrip_efficiency=Number(s.roundtrip_efficiency);
  if(s.charge_kw!=null)evalSettings.charge_kw=Number(s.charge_kw);
  if(s.discharge_kw!=null)evalSettings.discharge_kw=Number(s.discharge_kw);

  evalQuote={price_basis:'modeled',line_items:[]};
  if(q.roundtrip_efficiency!=null&&q.roundtrip_efficiency!=='')evalQuote.roundtrip_efficiency=q.roundtrip_efficiency;
  if(q.continuous_charge_kw!=null&&q.continuous_charge_kw!=='')evalQuote.continuous_charge_kw=q.continuous_charge_kw;
  if(q.continuous_discharge_kw!=null&&q.continuous_discharge_kw!=='')evalQuote.continuous_discharge_kw=q.continuous_discharge_kw;
  if(q.battery_nameplate_kwh!=null&&q.battery_nameplate_kwh!=='')evalQuote.battery_nameplate_kwh=q.battery_nameplate_kwh;
  if(q.battery_dod!=null&&q.battery_dod!=='')evalQuote.battery_dod=q.battery_dod;
 }else{
  evalSettings.quote_vat_included=rawVat;
  if(hasItemized){
   evalQuote=deepClone(q);
   evalQuote.price_basis='itemized';
  }else{
   evalSettings.quote_total=offerTotal;
   evalQuote=deepClone(q);
   evalQuote.price_basis='modeled';
  }
 }

 const fp=`${target}_${hasItemized?'itemized':'manual'}_${rawVat?'gross':'net'}_${inputFingerprint(evalSettings,evalQuote)}`;
 return {target,evalSettings,evalQuote,fp};
}

async function fetchQuoteComparison(sParam, qParam, isOfferActiveParam, offerTotalParam, forceRetry=false, optsParam={}){
 const s=sParam||current?.result?.settings||settings;
 const q=qParam||quote;
 const rawVat=(typeof optsParam?.rawVat==='boolean')
  ?optsParam.rawVat
  :(typeof settings!=='undefined'&&typeof settings?.quote_vat_included==='boolean'
    ?settings.quote_vat_included
    :(typeof s?.quote_vat_included==='boolean'?s.quote_vat_included:true));
 const hasItemized=(typeof optsParam?.hasItemized==='boolean')
  ?optsParam.hasItemized
  :hasPopulatedItemizedOffer(q, optsParam?.itemizedTotal ?? current?.itemized_total_eur);
 const manualOfferVal=(s.quote_total!=null&&Number.isFinite(Number(s.quote_total)))
  ?Number(s.quote_total)
  :(q.quote_total!=null&&q.quote_total!==''&&Number.isFinite(Number(q.quote_total)))
    ?Number(q.quote_total)
    :(typeof settings!=='undefined'&&settings.quote_total!=null&&Number.isFinite(Number(settings.quote_total))
      ?Number(settings.quote_total)
      :null);
 const itemizedCalcTotal=calculateItemizedTotal(q);
 const offerTotal=offerTotalParam??(hasItemized?(current?.itemized_total_eur??itemizedCalcTotal):manualOfferVal);
 const isOfferActive=isOfferActiveParam??(
   isItemizedPricingActive(q, offerTotal) ||
   (manualOfferVal!=null && (s.quote_total!=null || current?.result?.settings?.quote_total!=null))
 );
 const { target, evalSettings, evalQuote, fp } = getComparisonPayload(s, q, isOfferActive, offerTotal, { rawVat, hasItemized });

 if(!forceRetry){
  if(comparisonCache&&comparisonKey===fp)return comparisonCache;
  if(comparisonError&&comparisonKey===fp)return null;
 }

 if(comparisonInFlight){
  if(comparisonKey!==fp){
   comparisonPendingReq={s:deepClone(s),q:deepClone(q),isOfferActive,offerTotal,rawVat,hasItemized,fp};
   delete comparisonPendingReq.s.hasItemized;
  }
  return null;
 }

 comparisonInFlight=true;
 benchmarkInFlight=true;
 if(forceRetry||comparisonKey!==fp){
  comparisonError=null;
  comparisonCache=null;
 }
 comparisonKey=fp;
 const token=++comparisonToken;

 try{
  const res=await api('/api/evaluate',{
   settings:evalSettings,
   quote:evalQuote
  });

  if(token!==comparisonToken)return null;
  if(comparisonPendingReq){
   return null;
  }
  comparisonCache=res;
  comparisonKey=fp;
  comparisonError=null;
  benchmarkCache=res;
  benchmarkFingerprint=fp;
  if(typeof renderQuoteComparisonSheet==='function')renderQuoteComparisonSheet();
  return res;
 }catch(e){
  if(token===comparisonToken&&!comparisonPendingReq){
   comparisonError=e;
   comparisonCache=null;
   comparisonKey=fp;
  }
 }finally{
  if(token===comparisonToken){
   comparisonInFlight=false;
   benchmarkInFlight=false;
   if(comparisonPendingReq){
    const next=comparisonPendingReq;
    comparisonPendingReq=null;
    fetchQuoteComparison(next.s,next.q,next.isOfferActive,next.offerTotal,false,next);
   }else if(comparisonError){
    if(typeof renderQuoteComparisonSheet==='function')renderQuoteComparisonSheet();
   }
  }
 }
 return null;
}

const fetchBenchmark=fetchQuoteComparison;

function printPitchComparisonSheet(){
 document.body.classList.add('printing-pitch-sheet');
 const cleanup=()=>{
  document.body.classList.remove('printing-pitch-sheet');
  window.removeEventListener('afterprint',cleanup);
 };
 window.addEventListener('afterprint',cleanup);
 window.print();
}

const CLAIM_FIELD_LABELS={
 claimed_yield_kwh:'Promised annual PV yield',
 claimed_self_consumption_pct:'Promised self-consumption',
 claimed_payback_years:'Promised simple payback'
};

const MISSING_FIELD_LABELS={
 module_model:'Module model & datasheet guarantee missing',
 inverter_model:'Inverter continuous rating / hybrid capability unspecified',
 battery_model:'Battery model & manufacturer datasheet missing',
 datasheet_urls:'Equipment manufacturer datasheet URLs missing',
 payment_schedule:'Milestone payment schedule unspecified',
 layout_setbacks:'Roof spatial setbacks & fire/service pathways unspecified',
 warranties:'Product & workmanship warranty terms unspecified',
 monitoring_fees:'Monitoring platform & recurring software fee terms unspecified',
 inverter_replacement_terms:'Inverter replacement reserve & warranty coverage terms unspecified',
 degradation_curves:'PV module degradation warranty curve unspecified',
 power_curves:'Inverter temperature derating & power curves unspecified',
 roundtrip_efficiency:'Certified battery round-trip efficiency (RTE) unspecified',
 continuous_charge_kw:'Continuous battery charge power limit unspecified',
 continuous_discharge_kw:'Continuous battery discharge power limit unspecified'
};

function renderQuoteComparisonSheet(){
 const el=$('quoteComparisonSheetContent');
 if(!el)return;
 const r=current?.result;
 if(!r || loanPending){
  el.innerHTML='<p class="muted">Scenario calculation pending. Run evaluation to compare quote with modeled benchmarks.</p>';
  return;
 }
 const m=r.metrics, f=r.finance, q=quote, s=r.settings;
 const kwp=r.layout.kwp||1;

 const rawVat=(typeof settings!=='undefined'&&typeof settings?.quote_vat_included==='boolean')
  ?settings.quote_vat_included
  :(typeof s?.quote_vat_included==='boolean'?s.quote_vat_included:true);

 const itemizedActive=isItemizedPricingActive(q);
 const hasItemized=hasPopulatedItemizedOffer(q, current?.itemized_total_eur);

 const manualOfferVal=(s.quote_total!=null&&Number.isFinite(Number(s.quote_total)))
  ?Number(s.quote_total)
  :(q.quote_total!=null&&q.quote_total!==''&&Number.isFinite(Number(q.quote_total)))
    ?Number(q.quote_total)
    :(typeof settings!=='undefined'&&settings.quote_total!=null&&Number.isFinite(Number(settings.quote_total))
      ?Number(settings.quote_total)
      :null);
 const hasManualOffer=manualOfferVal!=null;
 const hasOffer=hasItemized||hasManualOffer;

 const isOfferActive=itemizedActive||(hasManualOffer&&s.quote_total!=null);
 const itemizedCalcTotal=calculateItemizedTotal(q);
 const offerTotal=itemizedActive
  ?(current?.itemized_total_eur??itemizedCalcTotal)
  :(hasManualOffer?manualOfferVal:(hasItemized?(current?.itemized_total_eur??itemizedCalcTotal):null));

 let benchRes=null;
 let offerRes=null;

 if(!hasOffer){
  invalidateQuoteComparison();
  benchRes=r;
  offerRes=null;
 }else if(isOfferActive){
  offerRes=r;
  const { fp } = getComparisonPayload(s, q, true, offerTotal, { rawVat, hasItemized });
  if(comparisonCache&&comparisonKey===fp){
   benchRes=comparisonCache.result;
  }else{
   benchRes=null;
   if(typeof api==='function'&&(!comparisonError||comparisonKey!==fp))fetchQuoteComparison(s,q,true,offerTotal,false,{ rawVat, hasItemized });
  }
 }else{
  benchRes=r;
  const { fp } = getComparisonPayload(s, q, false, offerTotal, { rawVat, hasItemized });
  if(comparisonCache&&comparisonKey===fp){
   offerRes=comparisonCache.result;
  }else{
   offerRes=null;
   if(typeof api==='function'&&(!comparisonError||comparisonKey!==fp))fetchQuoteComparison(s,q,false,offerTotal,false,{ rawVat, hasItemized });
  }
 }

 const benchCapex=benchRes?benchRes.finance.capex_eur:null;
 const benchCostPerKwp=benchCapex!=null?benchCapex/kwp:null;
 const benchNpv=benchRes?benchRes.metrics.npv_eur:null;
 const benchPb=benchRes?benchRes.metrics.discounted_payback_years:null;

 const offerGrossCapex = offerRes
  ? offerRes.finance.capex_eur
  : (hasItemized
    ? (current?.itemized_total_eur??itemizedCalcTotal)
    : (hasManualOffer
      ? (rawVat ? manualOfferVal : manualOfferVal * (1 + (s.installation_vat ?? 0.1)))
      : null));

 const offerCostPerKwp=offerGrossCapex!=null?offerGrossCapex/kwp:null;
 const offerNpv=offerRes?offerRes.metrics.npv_eur:null;
 const offerPb=offerRes?offerRes.metrics.discounted_payback_years:null;

 const missingItems=[];
 const realMissing=Array.isArray(current?.missing_fields)?current.missing_fields:[];
 realMissing.forEach(item=>{
  missingItems.push(MISSING_FIELD_LABELS[item]||(title(item)+' unspecified'));
 });
 if(!q.module_model&&!missingItems.some(item=>/module model/i.test(item))){
  missingItems.unshift('Module model & datasheet guarantee missing');
 }

 if(!hasItemized&&!hasManualOffer&&!missingItems.some(item=>/itemiz|breakdown|turnkey/i.test(item))){
  missingItems.push('Itemized turnkey cost breakdown missing (no lines entered)');
 }else if(hasItemized&&q.line_items){
  if(!q.line_items.some(l=>/pontegg|sicurez|scaffold/i.test(l.description||''))){
   missingItems.push('Scaffolding / roof safety itemization not explicitly confirmed');
  }
  if(!q.line_items.some(l=>/gse|pratic|allacci/i.test(l.description||''))){
   missingItems.push('GSE grid connection & bureaucracy fee status unconfirmed');
  }
 }

 const missingHtml=missingItems.length
  ?`<div class="quote-missing-alert"><strong>⚠️ Contractor disclosure gaps:</strong><ul>${missingItems.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></div>`
  :`<div class="good" style="margin-bottom:12px;padding:8px 12px;background:#e8f5e9;border-radius:6px;color:#1b5e20;">✔ All key specifications and itemized breakdown entries provided.</div>`;

 const claimsRows=(current.claims&&current.claims.length)
  ?current.claims.map(c=>{
    const fieldKey=c.field||'';
    const claimLabel=CLAIM_FIELD_LABELS[fieldKey]||(fieldKey?title(fieldKey):(typeof c.claim==='string'&&isNaN(c.claim)?c.claim:'Performance claim'));
    const isPct=fieldKey.includes('pct')||fieldKey.includes('self_consumption')||fieldKey.includes('autarky');
    const isYears=fieldKey.includes('years')||fieldKey.includes('payback');
    const isKwh=fieldKey.includes('yield')||fieldKey.includes('kwh');

    let formattedClaim='—';
    if(c.claim!=null&&Number.isFinite(Number(c.claim))){
     const num=Number(c.claim);
     if(isPct)formattedClaim=fmt(num,'pct');
     else if(isYears)formattedClaim=fmt(num,'years');
     else if(isKwh)formattedClaim=fmt(num,'number')+' kWh';
     else formattedClaim=fmt(num,'number');
    }else if(typeof c.claim==='string'){
     formattedClaim=esc(c.claim);
    }

    let simVal='—';
    if(c.simulated){
     if(isPct)simVal=range(c.simulated,'pct');
     else if(isYears)simVal=range(c.simulated,'years');
     else if(isKwh)simVal=range(c.simulated,'number')+' kWh';
     else simVal=range(c.simulated,'number');
    }else if(c.comment){
     simVal=esc(c.comment);
    }

    const isOutside=Boolean(c.outside_weather_range);
    const statusHtml=isOutside
     ?'<span class="tag unconfirmed">Outside modeled range; check definitions</span>'
     :'<span class="tag confirmed">Within simulated range</span>';
    const interp=c.interpretation?`<br><small class="muted">${esc(c.interpretation)}</small>`:'';

    return `
      <tr>
        <td><strong>${esc(claimLabel)}</strong></td>
        <td><strong>${formattedClaim}</strong></td>
        <td>${simVal}</td>
        <td>${statusHtml}${interp}</td>
      </tr>
    `;
  }).join('')
  :`<tr><td colspan="4" class="muted">No specific performance claims entered below. Expand "Contractor claims vs physical simulation" to test promises.</td></tr>`;

 const contractorKwp=q.contractor_kwp??(q.panel_count!=null?q.panel_count*(s.panel_watts||400)/1000:null);
 const contractorKwpOffer=contractorKwp!=null
  ?`${nf(contractorKwp,2)} kWp (${q.panel_count!=null?q.panel_count+' panels':'layout'})`
  :'<span class="muted">Awaiting installer specs</span>';
 const contractorKwpEval=contractorKwp!=null
  ?`${(contractorKwp-r.layout.kwp>0?'+':'')}${nf(contractorKwp-r.layout.kwp,2)} kWp vs modeled`
  :'<span class="muted">No installer layout entered</span>';

 const contractorBatOffer=q.battery_nameplate_kwh!=null
  ?`${nf(q.battery_nameplate_kwh,1)} kWh nameplate${q.battery_dod!=null?` (${fmt(q.battery_dod,'pct')} DoD)`:''}`
  :'<span class="muted">Awaiting installer specs</span>';
 const contractorBatEval=q.battery_nameplate_kwh!=null
  ?(q.battery_dod!=null
    ?`Usable: ${nf(q.battery_nameplate_kwh*q.battery_dod,1)} kWh`
    :'<span class="muted">Usable: uncertified (DoD not specified)</span>')
  :'<span class="muted">No installer battery entered</span>';

 const benchCapexHtml=benchCapex!=null?fmt(benchCapex,'eur'):(comparisonError?'<span class="bad">Benchmark failed <button type="button" class="btn btn-sm" onclick="fetchQuoteComparison(null,null,null,null,true)">Retry</button></span>':'<span class="muted" role="status"><span class="progress-spinner" aria-hidden="true"></span> Calculating benchmark…</span>');
 
 let offerCapexBreakdown='';
 if(hasManualOffer && !rawVat && manualOfferVal!=null && !itemizedActive){
  offerCapexBreakdown=`<br><small class="muted">(${fmt(manualOfferVal,'eur')} net + ${fmt(s.installation_vat??0.1,'pct')} VAT)</small>`;
 }
 const offerCapexHtml=hasOffer
  ?(offerGrossCapex!=null?`<strong>${fmt(offerGrossCapex,'eur')}</strong>${offerCapexBreakdown}`:'<span class="muted">Awaiting quote</span>')
  :'<span class="muted">Awaiting quote</span>';

 const capexEvalHtml=hasOffer
  ?(benchCapex!=null&&offerGrossCapex!=null
    ?(offerGrossCapex>benchCapex
      ?`<span class="bad">+${fmt(offerGrossCapex-benchCapex,'eur')} vs modeled benchmark</span>`
      :`<span class="good">${fmt(offerGrossCapex-benchCapex,'eur')} vs modeled benchmark</span>`)
    :'<span class="muted">Pending comparison…</span>')
  :'<span class="muted">No economic offer comparison</span>';

 const benchUnitCostHtml=benchCostPerKwp!=null?`${fmt(benchCostPerKwp,'eur')} / kWp`:'<span class="muted" role="status"><span class="progress-spinner" aria-hidden="true"></span> Calculating…</span>';
 const offerUnitCostHtml=hasOffer&&offerCostPerKwp!=null?`${fmt(offerCostPerKwp,'eur')} / kWp`:'—';
 const unitCostEvalHtml=hasOffer
  ?(benchCostPerKwp!=null&&offerCostPerKwp!=null
    ?((offerGrossCapex-benchCapex)/kwp>0
      ?`<span class="bad">+${fmt((offerGrossCapex-benchCapex)/kwp,'eur')}/kWp</span>`
      :`<span class="good">${fmt((offerGrossCapex-benchCapex)/kwp,'eur')}/kWp</span>`)
    :'<span class="muted">Pending comparison…</span>')
  :'—';

  const benchNpvMean=benchNpv?(benchNpv.mean??benchNpv.median):null;
  const offerNpvMean=offerNpv?(offerNpv.mean??offerNpv.median):null;
  const benchNpvHtml=benchNpv
   ?`<strong>${fmt(benchNpvMean,'eur')}</strong><br><small class="muted">Weather range ${range(benchNpv,'eur')}</small>`
   :(comparisonError
     ?`<span class="bad">Benchmark failed <button type="button" class="btn btn-sm" onclick="fetchQuoteComparison(null,null,null,null,true)">Retry</button></span>`
     :'<span class="muted" role="status"><span class="progress-spinner" aria-hidden="true"></span> Calculating benchmark…</span>');
  const offerNpvHtml=hasOffer
   ?(offerNpv
     ?`<strong>${fmt(offerNpvMean,'eur')}</strong><br><small class="muted">Weather range ${range(offerNpv,'eur')}</small>`
     :(comparisonError
       ?`<span class="bad">Offer eval failed <button type="button" class="btn btn-sm" onclick="fetchQuoteComparison(null,null,null,null,true)">Retry</button></span>`
       :'<span class="muted" role="status"><span class="progress-spinner" aria-hidden="true"></span> Evaluating offer economics…</span>'))
   :'—';
  const npvEvalHtml=hasOffer
   ?(benchNpv&&offerNpv
     ?`${(offerNpvMean-benchNpvMean>=0?`<span class="good">+${fmt(offerNpvMean-benchNpvMean,'eur')} NPV</span>`:`<span class="bad">${fmt(offerNpvMean-benchNpvMean,'eur')} NPV</span>`)} vs modeled benchmark<br><small class="muted">Modeled with offer capex (${fmt(offerGrossCapex,'eur')})</small>`
     :'<span class="muted">Pending comparison…</span>')
   :'<small class="muted">Awaiting quote offer</small>';

  const benchPbOfMean=benchRes?(benchRes.finance?.pv_incremental?.discounted_payback_of_mean_cash_flows!==undefined?benchRes.finance.pv_incremental.discounted_payback_of_mean_cash_flows:benchRes.metrics?.discounted_payback_of_mean_cash_flows):undefined;
  const benchPbVal=benchPbOfMean!==undefined?benchPbOfMean:benchPb?.median;

  const offerPbOfMean=offerRes?(offerRes.finance?.pv_incremental?.discounted_payback_of_mean_cash_flows!==undefined?offerRes.finance.pv_incremental.discounted_payback_of_mean_cash_flows:offerRes.metrics?.discounted_payback_of_mean_cash_flows):undefined;
  const offerPbVal=offerPbOfMean!==undefined?offerPbOfMean:offerPb?.median;

  const benchPbHtml=benchPb
   ?(benchPbVal!=null&&Number.isFinite(Number(benchPbVal))
     ?`<strong>${fmt(benchPbVal,'years')}</strong><br><small class="muted">Weather range ${range(benchPb,'years')}</small>`
     :`<strong>Not reached</strong><br><small class="muted">Weather range ${range(benchPb,'years')}</small>`)
   :(comparisonError?'<span class="bad">Failed</span>':'<span class="muted" role="status"><span class="progress-spinner" aria-hidden="true"></span> Calculating…</span>');
  const offerPbHtml=hasOffer
   ?(offerPb
     ?(offerPbVal!=null&&Number.isFinite(Number(offerPbVal))
       ?`<strong>${fmt(offerPbVal,'years')}</strong><br><small class="muted">Weather range ${range(offerPb,'years')}</small>`
       :`<strong>Not reached</strong><br><small class="muted">Weather range ${range(offerPb,'years')}</small>`)
     :(comparisonError?'<span class="bad">Failed</span>':'<span class="muted" role="status"><span class="progress-spinner" aria-hidden="true"></span> Evaluating…</span>'))
   :'—';
  const pbEvalHtml=hasOffer
   ?(benchPb&&offerPb
     ?(benchPbVal!=null&&offerPbVal!=null&&Number.isFinite(Number(benchPbVal))&&Number.isFinite(Number(offerPbVal))
       ?`${(offerPbVal-benchPbVal<=0?`<span class="good">${fmt(offerPbVal-benchPbVal,'years')}</span>`:`<span class="bad">+${fmt(offerPbVal-benchPbVal,'years')}</span>`)} vs modeled benchmark<br><small class="muted">Simple payback: ${range(offerRes.metrics.payback_years,'years')}</small>`
       :`<span class="muted">Payback difference not reached</span><br><small class="muted">Simple payback: ${range(offerRes.metrics.payback_years,'years')}</small>`)
     :'<span class="muted">Pending comparison…</span>')
   :`<small class="muted">Simple payback: ${range(m.payback_years,'years')}</small>`;
 el.innerHTML=`
   ${missingHtml}
   <div class="table-wrap">
     <table class="quote-comparison-table">
       <thead>
         <tr>
           <th>System specification / Metric</th>
           <th>Modeled physical benchmark</th>
           <th>Contractor offer</th>
           <th>Evaluation / Variance</th>
         </tr>
       </thead>
       <tbody>
         <tr>
           <td><strong>Solar PV capacity</strong></td>
           <td>${nf(r.layout.kwp,2)} kWp (${r.layout.panel_count} panels)</td>
           <td>${contractorKwpOffer}</td>
           <td>${contractorKwpEval}</td>
         </tr>
         <tr>
           <td><strong>Storage capacity</strong></td>
           <td>${nf(s.battery_kwh,1)} kWh usable</td>
           <td>${contractorBatOffer}</td>
           <td>${contractorBatEval}</td>
         </tr>
         <tr>
           <td><strong>Turnkey investment cost</strong></td>
           <td>${benchCapexHtml}</td>
           <td>${offerCapexHtml}</td>
           <td>${capexEvalHtml}</td>
         </tr>
         <tr>
           <td><strong>Turnkey unit cost</strong></td>
           <td>${benchUnitCostHtml}</td>
           <td>${offerUnitCostHtml}</td>
           <td>${unitCostEvalHtml}</td>
         </tr>
         <tr>
           <td><strong>Discounted incremental NPV</strong></td>
           <td>${benchNpvHtml}</td>
           <td>${offerNpvHtml}</td>
           <td>${npvEvalHtml}</td>
         </tr>
         <tr>
           <td><strong>Discounted payback</strong></td>
           <td>${benchPbHtml}</td>
           <td>${offerPbHtml}</td>
           <td>${pbEvalHtml}</td>
         </tr>
       </tbody>
     </table>
   </div>

   <h4 class="quote-claims-head" style="margin:16px 0 8px;">Claimed yields &amp; savings vs ${getWeatherEnsembleMeta(current?.result).count ? getWeatherEnsembleMeta(current?.result).count + '-year' : 'historical'} physical simulation</h4>
   <div class="table-wrap">
     <table class="quote-comparison-table">
       <thead>
         <tr>
           <th>Claimed parameter</th>
           <th>Contractor claim</th>
           <th>PVGIS ${getWeatherEnsembleMeta(current?.result).count ? getWeatherEnsembleMeta(current?.result).count + '-year' : 'historical'} simulation</th>
           <th>Reality check status</th>
         </tr>
       </thead>
       <tbody>
         ${claimsRows}
       </tbody>
     </table>
   </div>
 `;

 const pitchBtn=$('printPitchBtn');
 if(pitchBtn)pitchBtn.onclick=printPitchComparisonSheet;
}

function switchView(name){
 if((name==='quote'||name==='contractor')&&!$('quote'))name='summary';
 document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);
 document.querySelectorAll('nav button').forEach(el=>{
  const isActive=el.dataset.view===name;
  el.classList.toggle('active',isActive);
  if(isActive)el.setAttribute('aria-current','page');
  else el.removeAttribute('aria-current');
 });
 const r=(typeof current!=='undefined')?current?.result:null;
 if(name==='summary'&&r){
  renderVerdictStrip(r);
  renderCashChart();
  renderConfigVsRecommended();
  renderSummarySensitivity();
 }else if(name==='design'){
  if(typeof solarData!=='undefined'&&solarData)renderSolarSizing(solarData);
  if(typeof renderRoofLimitsComparison==='function')renderRoofLimitsComparison();
  if(typeof renderRoofPills==='function')renderRoofPills();
 }else if(name==='financing'){
  renderLoanPanel();
 }else if(name==='behavior'){
  if(typeof refreshBehaviorViews==='function')refreshBehaviorViews();
 }else if(name==='prices'&&r){
  renderAdjacentPrices();
  if(typeof renderGasCostsAudit==='function')renderGasCostsAudit();
  renderLcoeCards();
  renderExportValuation(r);
  if(typeof renderMarketCurveWrap==='function')renderMarketCurveWrap();
 }else if(name==='assumptions'&&r){
  renderLedger(r);
  updateAssumptionsBadge(r);
 }else if(name==='quote'&&$('quote')){
  renderLines();
  syncQuoteFields();
  syncQuotePricingDescription();
  if(typeof renderQuoteComparisonSheet==='function')renderQuoteComparisonSheet();
 }
 if(typeof updateSidebarForTab==='function')updateSidebarForTab(name);
 if(typeof matchMedia==='function'&&matchMedia('(max-width:720px)').matches)document.querySelector('main')?.scrollIntoView?.({block:'start'});
}
async function loadCandidates(){const id=++optimizerRequestId;let search=null,fingerprint=null;if($('recomputePareto'))$('recomputePareto').disabled=true;if($('error'))$('error').hidden=true;try{search=optimizerSearch();const equip=optimizerEquipment(),settingsForSearch={...settings,...equip,load_scenario:search.load_scenario},searchRequest={objective:search.objective,battery_min_modules:search.battery_min_modules,battery_max_modules:search.battery_max_modules,panel_maxima:search.panel_maxima,include_theoretical:search.include_theoretical};if(shouldBlockOptimizerForAcDraft(search)){const message='Cannot search configurations with invalid heating draft.';if($('paretoMethod'))$('paretoMethod').textContent=message;markOptimizerStale(message);return;}for(const key of panelKeys)settingsForSearch[key]=0;settingsForSearch.battery_modules=0;settingsForSearch.battery_secondary_modules=0;settingsForSearch.battery_kwh=0;const integerFields=[['minimum battery modules',$('optBatteryMin')?.value??search.battery_min_modules,search.battery_min_modules],['maximum battery modules',$('optBatteryMax')?.value??search.battery_max_modules,search.battery_max_modules],...Object.entries(search.panel_maxima).map(([k,v])=>[`${k} roof maximum`,$({south:'maxSouth',west:'maxWest',east:'maxEast',dormer:'maxDormer',flat:'maxFlat',secondary_south:'maxSecondarySouth'}[k])?.value??v,v])];for(const [label,raw,value]of integerFields){if(String(raw).trim()===''||!Number.isInteger(value)||value<0)throw Error(`${label} must be a non-negative whole number.`);}if(search.battery_min_modules>search.battery_max_modules)throw Error('Minimum battery modules cannot exceed the maximum.');const required=['optPanelWatts','optModuleKwh','optInverter','optCharge','optDischarge'];if(required.some(reqId=>$(reqId)&&String($(reqId).value).trim()===''))throw Error('Fill in all equipment values before searching.');const numericEquipKeys=['panel_watts','battery_module_kwh','inverter_kw','charge_kw','discharge_kw'];if(equip.battery_mixed_sizes)numericEquipKeys.push('battery_secondary_module_kwh');if(numericEquipKeys.some(k=>!Number.isFinite(equip[k])||equip[k]<0)||equip.panel_watts<=0||equip.inverter_kw<=0||(search.battery_max_modules>0&&(equip.battery_module_kwh<=0||(equip.battery_mixed_sizes&&equip.battery_secondary_module_kwh<=0))))throw Error('Enter valid panel and inverter values, plus positive battery module capacities when searching batteries and non-negative charge/discharge power.');saveOptimizerPrefs();if(typeof cleanSettingsForBackend==='function')cleanSettingsForBackend(settingsForSearch);fingerprint=optimizerStateFingerprint();if($('paretoMethod'))$('paretoMethod').textContent='Searching integer panel counts and battery modules…';const result=await api('/api/optimize-configurations',{settings:settingsForSearch,search:searchRequest});if(id!==optimizerRequestId||fingerprint!==optimizerStateFingerprint()||shouldBlockOptimizerForAcDraft(search))return;optimizerResult=result;optimizerFingerprint=fingerprint;optimizerStale=false;if($('bestConfiguration'))$('bestConfiguration').classList?.remove?.('stale');if($('candidates'))$('candidates').classList?.remove?.('stale');if($('paretoChart'))$('paretoChart').classList?.remove?.('stale');if($('error'))$('error').hidden=true;renderCandidates();}catch(e){if(id===optimizerRequestId&&(!fingerprint||(fingerprint===optimizerStateFingerprint()&&!shouldBlockOptimizerForAcDraft(search)))){optimizerResult=null;if($('bestConfiguration'))$('bestConfiguration').innerHTML='';if($('candidates'))$('candidates').innerHTML='';if($('paretoChart'))$('paretoChart').innerHTML='';const message=e.status===404?'The configuration optimizer is unavailable on this server. Reload the server and try again.':(e.message||'The configuration search could not run.');showError(Error(message));if($('paretoMethod'))$('paretoMethod').textContent=message;}}finally{if(id===optimizerRequestId&&$('recomputePareto'))$('recomputePareto').disabled=false;}}
function studySettingsMatch(studyBaseline,dispatched){
 if(!studyBaseline||typeof studyBaseline!=='object'||Array.isArray(studyBaseline))return false;
 if(!dispatched||typeof dispatched!=='object'||Array.isArray(dispatched))return false;
 const cleanBaseline=typeof cleanSettingsForBackend==='function'?cleanSettingsForBackend(deepClone(studyBaseline)):deepClone(studyBaseline);
 const cleanDispatched=typeof cleanSettingsForBackend==='function'?cleanSettingsForBackend(deepClone(dispatched)):deepClone(dispatched);
 const baselineKeys=Object.keys(cleanBaseline).filter(k=>cleanBaseline[k]!==undefined);
 const dispatchedKeys=Object.keys(cleanDispatched).filter(k=>cleanDispatched[k]!==undefined);
 if(baselineKeys.length===0||dispatchedKeys.length===0)return false;
 if(baselineKeys.length!==dispatchedKeys.length)return false;
 for(const k of dispatchedKeys){
  if(!Object.hasOwn(cleanBaseline,k)||cleanBaseline[k]===undefined)return false;
  const actual=cleanBaseline[k];
  const v=cleanDispatched[k];
  if(typeof settingValuesEqual==='function'){
   if(!settingValuesEqual(actual,v))return false;
  }else if(typeof v==='object'&&v!==null){
   if(JSON.stringify(v)!==JSON.stringify(actual))return false;
  }else if(actual!==v){
   return false;
  }
 }
 return true;
}
let studyRequestId=0,studyFingerprint=null,studyDispatchedSettings=null;
async function pollJob(){const token=arguments[0]!==undefined?arguments[0]:studyRequestId,fingerprint=arguments[1]!==undefined?arguments[1]:studyFingerprint,dispatched=arguments[2]!==undefined?arguments[2]:studyDispatchedSettings;try{const j=await api('/api/job');const currentSettings=current?.result?.settings||settings;if(token!==studyRequestId||fingerprint!==studyFingerprint||(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(currentSettings)))return;if($('jobState'))$('jobState').textContent=j.state+' · '+j.progress;if(j.state==='running')setTimeout(()=>pollJob(token,fingerprint,dispatched),2000);if(j.state==='complete'){const res=await api('/api/study');if(token!==studyRequestId||fingerprint!==studyFingerprint||(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(currentSettings)))return;if(!res||!res.baseline_settings||!dispatched||!studySettingsMatch(res.baseline_settings,dispatched)){const mismatchMsg='Global study completed with mismatched baseline settings (server shares a single study job across clients and runs; cannot safely apply unrelated study result).';if($('jobState'))$('jobState').textContent=mismatchMsg;if($('paretoMethod'))$('paretoMethod').textContent=mismatchMsg;console.warn(mismatchMsg);return;}study=res;candidates=study.base;if(typeof renderCandidates==='function')renderCandidates();}}catch(e){const currentSettings=current?.result?.settings||settings;if(token===studyRequestId&&fingerprint===studyFingerprint&&(!shouldBlockAcRecompute||!shouldBlockAcRecompute(currentSettings))){showError(e);}}}
function formatExportMetric(stat,type,unit=''){
 if(stat==null)return '—';
 if(typeof stat==='number')return fmt(stat,type)+(unit?' '+unit:'');
 if(stat.median!=null){
  const med=fmt(stat.median,type)+(unit?' '+unit:'');
  const rng=range(stat,type);
  if(stat.min!=null&&stat.max!=null){
   return `${med} (${rng}${unit?' '+unit:''})`;
  }
  if(type==='years'&&stat.recovered_weather_years===0){
   return `${med} (Not reached)`;
  }
  return med;
 }
 if(type==='years'&&stat.recovered_weather_years===0){
  return 'Not reached';
 }
 return range(stat,type)+(unit?' '+unit:'');
}

function isScenarioEvaluationCurrent(s=settings,q=quote){
 if(!current||!current.result)return false;
 if(typeof loanPending!=='undefined'&&loanPending)return false;
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(s))return false;
 if(typeof acceptedInputFingerprint!=='undefined'&&typeof inputFingerprint==='function'){
  const sClone=deepClone(s);
  delete sClone.hasItemized;
  if(typeof cleanSettingsForBackend==='function')cleanSettingsForBackend(sClone);
  const qClone=deepClone(q||{line_items:[]});
  const fp=inputFingerprint(sClone,qClone);
  if(acceptedInputFingerprint===null||acceptedInputFingerprint!==fp)return false;
 }
 if(current.result.settings){
  const cur=current.result.settings;
  if(s.load_scenario!==cur.load_scenario)return false;
  if(Boolean(s.planning_heat_pump)!==Boolean(cur.planning_heat_pump))return false;
  if(s.load_scenario!=='baseline'&&!s.planning_heat_pump){
   if(s.heating_schedule_mode!==cur.heating_schedule_mode)return false;
   if(s.ac_heating_additional_demand_pct!==cur.ac_heating_additional_demand_pct)return false;
   if(s.heating_schedule_mode!=='continuous'){
    if(typeof settingValuesEqual==='function'){
     if(!settingValuesEqual(s.ac_heating_windows,cur.ac_heating_windows))return false;
    }
   }
  }
 }
 return true;
}

function getExportBlockReason(action='export scenario'){
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile&&(!current?._presentation||current._presentation.profile_id!==householdClient.profileId||current._presentation.input_revision!==householdClient.revision))return 'Selected profile revision changed. Re-run before exporting.';
 if(!current||!current.result){
  return `Cannot ${action}: no completed scenario evaluation available. Wait for calculation to finish.`;
 }
 if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(settings)){
  return `Cannot ${action}: active AC heating inputs contain invalid drafts. Fix errors before ${action.startsWith('print')?'printing':'exporting'}.`;
 }
 if(!isScenarioEvaluationCurrent(settings,quote)){
  return `Cannot ${action}: inputs have changed since the last calculation. Wait for calculation to finish before ${action.startsWith('print')?'printing':'exporting'}.`;
 }
 return null;
}

function formatAcPrintSchedule(s){
 if(!s||typeof s!=='object')return 'operating schedule unavailable';
 const schedMode=s.heating_schedule_mode||'scheduled';
 const tzMode=s.timezone_mode||schema?.defaults?.timezone_mode||'civil';
 const clockText=tzMode==='standard'?'Fixed UTC+1 standard time (no DST)':'Civil local time Europe/Rome (DST: CET in winter, CEST in summer)';
 const rawWindows=s.ac_heating_windows;
 let windowsDesc='';
 if(Array.isArray(rawWindows)&&rawWindows.length>0){
  windowsDesc=rawWindows.map((w,idx)=>{
   const sm=Number(w.start_minute);
   const em=Number(w.end_minute);
   const id=w.id||`window_${idx+1}`;
   if(!Number.isFinite(sm)||!Number.isFinite(em)||sm<0||sm>1440||em<0||em>1440){
    return `${id}: invalid minutes (${sm}–${em})`;
   }
   const sH=Math.floor(sm/60),sM=sm%60;
   const eH=Math.floor(em/60),eM=em%60;
   const sStr=`${String(sH).padStart(2,'0')}:${String(sM).padStart(2,'0')}`;
   const eStr=`${String(eH).padStart(2,'0')}:${String(eM).padStart(2,'0')}`;
   if(sm===em){
    return `${id}: inactive (${sStr}–${eStr}, 0 min)`;
   }else if(sm===0&&em===1440){
    return `${id}: all-day 24h (00:00–24:00)`;
   }else if(sm>em){
    return `${id}: overnight (${sStr}–${eStr}, crosses midnight)`;
   }else{
    return `${id}: ${sStr}–${eStr}`;
   }
  }).join('; ');
 }else if(Array.isArray(rawWindows)&&rawWindows.length===0){
  windowsDesc='no operating windows defined';
 }else{
  windowsDesc='operating windows unavailable';
 }
 if(schedMode==='continuous'){
  return `Continuous 24h heating (unconstrained historical continuous profile; canonical operating windows retained: ${windowsDesc}). Clock context: ${clockText}.`;
 }
 return `Scheduled operating windows: ${windowsDesc}. Clock context: ${clockText}.`;
}

function onePage(){
 if(typeof activeResultProfile!=='undefined'&&activeResultProfile){downloadOwnedReport('html').catch(showError);return;}
 const reason=getExportBlockReason('print report');
 if(reason){
  showError(new Error(reason));
  return;
 }
 const r=current.result,m=r.metrics,f=r.finance;
 const packageRows=r.settings.load_scenario==='baseline'?[]:[
  ['Whole-house package simple payback',formatExportMetric(f.whole_package.simple_payback,'years')],
  ['Discounted whole-house package payback',formatExportMetric(f.whole_package.discounted_payback,'years')]
 ];
 const expAnalysis=r.export_price_analysis;
 let exportCurveProse='';
 if(expAnalysis){
  if(expAnalysis.profile_type==='empirical'){
   const isActual=expAnalysis.profile_source==='actual_hourly_gme_profile';
   const label=isActual?(expAnalysis.profile_label||'Actual chronological hourly GME'):(expAnalysis.profile_label||'Empirical CSUD shape');
   const winLabel=expAnalysis.window?` (${expAnalysis.window.replace('_',' ')})`:'';
   if(expAnalysis.annual_export_kwh>0&&expAnalysis.export_weighted_capture_factor!=null){
    const capFactor=nf(expAnalysis.export_weighted_capture_factor*100,1)+'%';
    const capPrice='€'+nf(expAnalysis.export_weighted_captured_price_eur_kwh,4)+'/kWh';
    const basePrice='€'+nf(expAnalysis.baseload_price_eur_kwh,4)+'/kWh';
    exportCurveProse=`${label}${winLabel} profile is active (baseload ${basePrice}, export-weighted capture factor ${capFactor}, effective captured price ${capPrice} with dynamic future ramp).`;
   }else{
    exportCurveProse=`${label}${winLabel} profile is active with no grid exports modeled (all generation directly self-consumed/stored, or no solar generation). Dynamic future ramp applies to any future surplus.`;
   }
  }else{
   const basePrice='€'+nf(expAnalysis.baseload_price_eur_kwh??r.settings.export_market_price,4)+'/kWh';
   const capFactor=nf((expAnalysis.legacy_capture_factor??r.settings.export_capture_factor)*100,1)+'%';
   const flatPrice=expAnalysis.flat_benchmark_comparison?.flat_captured_price_eur_kwh!=null?'€'+nf(expAnalysis.flat_benchmark_comparison.flat_captured_price_eur_kwh,4)+'/kWh':'€'+nf((expAnalysis.baseload_price_eur_kwh??r.settings.export_market_price)*(expAnalysis.legacy_capture_factor??r.settings.export_capture_factor),4)+'/kWh';
   exportCurveProse=`Flat solar capture benchmark is active (baseload ${basePrice} × constant capture factor ${capFactor} = ${flatPrice}) with dynamic future ramp.`;
  }
 }
  const pvRow=m.pv_kwh?['PV generation / year',formatExportMetric(m.pv_kwh,'number','kWh')]:['PV generation / year','—'];
  const exportRow=m.export_kwh?['Grid export / year',formatExportMetric(m.export_kwh,'number','kWh')]:['Grid export / year','0 kWh'];
  const moneyBasisLabel=r.settings.cashflow_mode==='nominal'?'Nominal cash flows (including inflation)':'Real cash flows (constant purchasing power)';
  const activeHp=Boolean(r.settings?.planning_heat_pump&&r.settings?.load_scenario!=='baseline');
  let hpPrintSection='';
  let hpNoticeSection='';
  if(activeHp){
   const hp=r.heat_pump||{};
   const meta=hp.metadata||{};
   const env=hp.envelopes||{};
   const isComparable=r.finance?.service_comparable!==false&&r.service_comparable!==false;
   const gasStatusText=r.settings.hp_disconnect_gas?'Natural gas fully disconnected (meter removed, standing charges eliminated)':(r.settings.hp_electric_cooking?'Natural gas retained (meter active, 0 Smc modeled, no gas backup modeled)':'Natural gas retained for cooking (meter and standing charges active)');
   const modelText=meta.equipment_model||'Generic Air-to-Water Monobloc (provisional synthetic fallback)';
   const nominalKw=meta.provisional_nominal_capacity_kw??r.settings.hp_nominal_kw??null;
   const scopSpace=env.seasonal_scop_space?.median??hp.by_weather?.[0]?.seasonal_scop_space;
   const spfHeat=env.seasonal_performance_factor_delivered_heat?.median??hp.by_weather?.[0]?.seasonal_performance_factor_delivered_heat;
   const hpElec=env.annual_total_electricity_kwh?.median??hp.by_weather?.[0]?.annual_total_electricity_kwh;
   if(!isComparable){
    hpNoticeSection=`<div class="notice" style="background:#fee2e2;border:1px solid #dc2626;color:#991b1b;padding:4px 8px;margin:4px 0;border-radius:4px;"><strong>MAJOR UNMET THERMAL DEMAND WARNING:</strong> Heat pump simulation identified unserved heating or hot water demand across the simulated weather envelope. Avoided gas credit is withheld and full economic equivalence is not validated (no room comfort or full hygiene compliance is claimed). Equipment sizing, radiator area, or backup capacity must be reviewed.</div>`;
   }
   hpPrintSection=`<div class="compact-summary"><p><strong>Heat pump provisional technical summary:</strong> ${esc(modelText)} (${nominalKw!=null?nf(nominalKw,1)+' kW nominal':'—'}) · Achieved space SCOP ${scopSpace?nf(scopSpace,2):'—'} · Combined SPF ${spfHeat?nf(spfHeat,2):'—'} · Added electricity ${hpElec?nf(hpElec,0)+' kWh/yr':'—'}<br><strong>Gas connection status:</strong> ${esc(gasStatusText)}</p></div>`;
  }
  let acPrintSection='';
  if(!activeHp){
   const s=r.settings||{};
   const isBaseline=s.load_scenario==='baseline';
   if(isBaseline){
    acPrintSection=`<div class="compact-summary"><p><strong>Baseline load scenario (<code>baseline</code>):</strong> Space heating is supplied entirely by the existing natural gas boiler (unchanged historical gas demand; 0 kWh AC electricity modeled). AC heat pump simulation and 0% extra demand counterfactual apply to electrified scenarios (<code>electrified</code> or <code>ev</code>).</p></div>`;
   }else{
    const enduses=r.enduses||{};
    const baseUseful=enduses.ac_heating_baseline_useful_kwh;
    const reqUseful=enduses.ac_heating_requested_useful_kwh;
    const cf0Elec=enduses.ac_heating_baseline_electricity_kwh;
    const reqElec=enduses.ac_heating_requested_electricity_kwh;
    const extraElec=enduses.ac_heating_extra_electricity_kwh;
    const evalScop=enduses.ac_heating_scop??s.scop;
    const evalPct=enduses.ac_heating_additional_demand_pct??s.ac_heating_additional_demand_pct;
    const formatPrintKwh=(val,unit='kWh/yr')=>{
     if(val===null||val===undefined)return 'Unavailable';
     const num=Number(val);
     if(!Number.isFinite(num))return 'Unavailable';
     return `${nf(num,0)}${unit?' '+unit:''}`;
    };
    const formatPrintPct=(val)=>{
     if(val===null||val===undefined)return 'Unavailable';
     const num=Number(val);
     if(!Number.isFinite(num))return 'Unavailable';
     return `${num}%`;
    };
    const formatPrintScop=(val)=>{
     if(val===null||val===undefined)return 'Unavailable';
     const num=Number(val);
     if(!Number.isFinite(num))return 'Unavailable';
     return nf(num,1);
    };
    const formatPrintTemp=(val)=>{
     if(val===null||val===undefined)return 'Unavailable';
     const num=Number(val);
     if(!Number.isFinite(num))return 'Unavailable';
     return `${nf(num,1)}°C`;
    };
    const schedSummary=formatAcPrintSchedule(s);
    acPrintSection=`<div class="compact-summary"><p><strong>Air conditioner heating technical summary:</strong> Nominal SCOP ${formatPrintScop(evalScop)} · Baseline useful heat ${formatPrintKwh(baseUseful,'kWh th/yr')} · Requested useful heat ${formatPrintKwh(reqUseful,'kWh th/yr')} · Additional demand margin ${formatPrintPct(evalPct)}<br><strong>Electricity consumption &amp; counterfactual:</strong> AC baseline 0% counterfactual ${formatPrintKwh(cf0Elec)} · Requested AC electricity ${formatPrintKwh(reqElec)} · Extra electricity ${formatPrintKwh(extraElec)}<br><strong>Operating schedule &amp; clock:</strong> ${esc(schedSummary)}</p><p><strong>Physical boundaries &amp; methodology:</strong></p><ul style="margin:2px 0 4px;padding-left:16px;"><li><strong>Fixed monthly budgets &amp; weather timing:</strong> Monthly useful thermal budgets derive from historical natural gas consumption and remain strictly fixed for each month; historical weather variations redistribute hourly timing across permitted operating hours based on heating degree-days below ${formatPrintTemp(s.heating_balance_temp)} balance temperature.</li><li><strong>Unchanged historical gas saving:</strong> Heating electricity is computed by dividing required useful heat by nominal SCOP once (no secondary demand multiplier). Additional thermal demand represents requested extra useful heat only (no inferred comfort) rather than displaced fuel, and generates no additional savings on historical gas bills (avoided fuel credit remains capped at historical displaced gas consumption).</li><li><strong>Equipment adequacy &amp; comfort unassessed:</strong> Equipment adequacy, indoor comfort levels, and low-temperature compressor derating or auxiliary electric behavior are unassessed. Modeled interval-average thermal and electrical requirements during cold spells must be verified against actual equipment capacity and electrical supply service limits, with actual equipment capacity and comfort unassessed.</li><li><strong>Modeled interval-average vs permitted-duration requirements:</strong> Hourly modeled values reflect interval-average energy within permitted operating fractions; modeled interval-average versus permitted-duration requirements mean partial-hour operation requires higher operating rates than full-hour averages, while actual equipment capacity and comfort remain unassessed.</li><li><strong>Hourly PV coincidence limitation:</strong> Hourly energy simulation conserves total energy; minute-level window bounds define hourly allocation eligibility rather than sub-hourly solar PV coincidence. Standard UTC+1 hourly charts may show an offset during summer daylight saving time (Europe/Rome CEST).</li></ul></div>`;
   }
  }
  const html=`<!doctype html><html><head><meta charset="utf-8"><title>PV Lab scenario summary</title><style>
body{font:11px/1.35 system-ui,-apple-system,sans-serif;color:#183b33;background:#ffffff;max-width:760px;margin:20px auto;padding:0 12px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:13px;margin:10px 0 4px}
h3{font-size:12px;margin:8px 0 3px}
table{border-collapse:collapse;width:100%;margin:6px 0}
td,th{padding:4px 6px;border-bottom:1px solid #ddd;text-align:left;font-size:11px;line-height:1.3}
.notice{background:#fff4d8;padding:6px 10px;margin:6px 0;border-radius:4px;font-size:10.5px;line-height:1.3}
.compact-summary{margin:6px 0;font-size:10.5px;line-height:1.35}
.compact-summary p{margin:2px 0}
small{font-size:9.5px;color:#555}
details.audit-detail{margin-top:10px;border:1px solid #d0ded6;border-radius:5px;padding:6px 10px;background:#f9fbf9}
details.audit-detail summary{cursor:pointer;font-weight:600;font-size:11px;color:#183b33}
.audit-content{margin-top:6px;font-size:10.5px;line-height:1.4}
@page{size:A4 portrait;margin:8mm 10mm}
@media print{
 button,.no-print,details.audit-detail{display:none!important}
 body{margin:0;padding:0;max-width:100%;font-size:8.5pt!important;line-height:1.2!important}
 h1{font-size:13pt!important;margin:0 0 2pt!important}
 h2{font-size:9.5pt!important;margin:4pt 0 2pt!important}
 .notice{padding:3pt 6pt!important;margin:3pt 0!important;font-size:7.5pt!important}
 table{margin:2pt 0!important;font-size:7.5pt!important}
 td,th{padding:2pt 4pt!important}
 .compact-summary{font-size:7.5pt!important;line-height:1.2!important}
 .compact-summary p{margin:1.5pt 0!important}
}
</style></head><body><button onclick="window.print()">Print / Save PDF</button><h1>PV Lab · contractor scenario</h1><div class="compact-summary"><p><strong>${esc(title(r.settings.load_scenario))}</strong> · ${nf(r.layout.kwp,1)} kWp · ${nf(r.settings.battery_kwh,1)} kWh usable · ${nf(r.settings.inverter_kw,2)} kW inverter<br><strong>Money basis:</strong> ${moneyBasisLabel} · Nominal discount ${fmt(f.nominal_discount_rate,'pct')}, real discount ${fmt(f.real_discount_rate,'pct')}, inflation ${fmt(r.settings.inflation,'pct')}</p></div><p class="notice">PENDING SCENARIO. Weather ranges for selected assumptions; quote, physical layout and annual tax capacity require confirmation.</p>${hpNoticeSection}${table(['Metric','Computed median & weather range'],[pvRow,exportRow,['Autarky',formatExportMetric(m.autarky,'pct')],['Self-consumption',formatExportMetric(m.self_consumption,'pct')],['PV + battery incremental NPV',formatExportMetric(m.npv_eur,'eur')],['Whole-house package NPV',formatExportMetric(m.package_npv_eur,'eur')],['PV + battery simple payback',formatExportMetric(m.payback_years,'years')],['Discounted PV + battery payback',formatExportMetric(m.discounted_payback_years,'years')],...packageRows,...(f.loan_principal_eur>0?[['Discounted personal cash recovery',formatExportMetric(f.equity.discounted_payback,'years')],['Discounted full-cost recovery + interest ('+(f.loan_scope==='solar_and_conversion'?'solar + electrification':'solar')+')',formatExportMetric(f.financed_project.discounted_payback,'years')]]:[]),['PV + battery IRR / LCOE',`${formatExportMetric(m.irr,'pct')} / ${formatExportMetric(m.lcoe_eur_kwh,'lcoe')} per AC kWh`],['Solar capex / conversion capex',`${fmt(f.capex_eur,'eur')} / ${fmt(f.conversion_capex_eur,'eur')}`]])}${hpPrintSection}${acPrintSection}<div class="compact-summary"><p><strong>Key parameters:</strong> Horizon ${r.settings.horizon_years}y · Tax deduction ${fmt(r.settings.deduction_rate,'pct')} (${fmt(f.annual_deduction_eur,'eur')}/yr, 10 installments) · Loan principal ${fmt(f.loan_principal_eur,'eur')} of requested ${fmt(f.requested_loan_amount_eur,'eur')} (${f.loan_scope==='solar_and_conversion'?'solar + electrification':'solar'}) · Retail escalation ${fmt(r.settings.energy_real_escalation,'pct')}.</p><p><strong>Key boundaries:</strong> ${(Array.isArray(r.weather_years)&&r.weather_years.length)?r.weather_years.length+" historical PVGIS weather years":"historical PVGIS weather ensemble"} with PV/battery degradation; hourly energy conservation. ${exportCurveProse} Gas savings credited only in whole-house economics alongside conversion costs. Roof layouts uninspected; CER income excluded.</p></div><details class="audit-detail"><summary>Financial conventions &amp; methodology (closed audit detail)</summary><div class="audit-content"><h2>Financial conventions</h2><p>PV + battery payback compares with the same selected household load without solar. More efficient heating or hot water can lower avoided grid-import savings and lengthen this solar-only payback; conversion gas and electricity savings appear in whole-house package payback and NPV. Whole-house package payback compares with historic household electricity and gas bills and includes conversion equipment costs. Horizon ${r.settings.horizon_years} years. Nominal discount ${fmt(f.nominal_discount_rate,'pct')}; real discount ${fmt(f.real_discount_rate,'pct')}; inflation ${fmt(r.settings.inflation,'pct')}. Deduction ${fmt(r.settings.deduction_rate,'pct')}, annual usable ${fmt(f.annual_deduction_eur,'eur')}. Capital at t=0, operational cash annual year-end with monthly loan repayments; ten nominal deduction installments. Loan principal ${fmt(f.loan_principal_eur,'eur')} of requested ${fmt(f.requested_loan_amount_eur,'eur')}; scope ${f.loan_scope==='solar_and_conversion'?'solar + electrification':'solar'}. Conversion capex remains outside the deduction basis. Financed payback counts full purchase cost upfront plus annual loan interest; principal is not counted twice. Equity cash flows separately include actual principal and interest repayments. Retail commodity real escalation ${fmt(r.settings.energy_real_escalation,'pct')}. RID uses annual aggregate PMG true-up, then deductible fee and marginal income tax.</p><h2>Boundaries</h2><p>All ${getWeatherEnsembleMeta(r).count || "historical"} PVGIS weather years are repeated separately over life, with PV/battery degradation. Hourly energy simulation conserves energy. ${exportCurveProse} Gas savings are credited only in whole-house economics alongside conversion costs. Roof layouts are uninspected; shading uses segment averages. CER income is excluded by default and registry evidence conflicts.</p><small>Source: local Step 1 research, audited invoices and meter export, PVGIS and Solar API rasters. Engine: feasibility/engine.py. Full settings and yearly cash flows are available in the companion scenario JSON.</small></div></details><div class="no-print"><small>Source: local Step 1 research, audited invoices and meter export, PVGIS and Solar API rasters. Engine: feasibility/engine.py. Full settings and yearly cash flows are available in the companion scenario JSON.</small></div></body></html>`;
 download((typeof activeResultProfile!=='undefined'&&activeResultProfile)?html.replace('</body>',renderProfileDisclosures(current?._presentation)+'</body>'):html,'pv-lab-one-page.html','text/html');
}
function evidence(){const c=schema.calibration,a=schema.audit;const activeWeather=$('activeWeatherCoverage');if(activeWeather)activeWeather.textContent=`Active calculations: ${c.weather_count} weather years${getWeatherEnsembleMeta(a).rangeLabel?' ('+getWeatherEnsembleMeta(a).rangeLabel+')':''} · ${nf(c.hour_count,0)} UTC hourly slots.`;const inv=c.invoice;const invHeadline=inv?`Reconciled total ${fmt(inv.total_eur,'eur')} across ${nf(inv.kwh)} kWh billed sample (commodity ${fmt(inv.commodity_eur,'eur')}, regulated ${fmt(inv.regulated_eur,'eur')}, excise ${fmt(inv.excise_eur,'eur')}, fixed fees ${fmt(inv.fixed_eur,'eur')}, VAT ${fmt(inv.vat_eur,'eur')}). Verifies electric billing against audited utility invoice lines.<details class="technical-details-fold"><summary>Audit payload (raw JSON)</summary><pre><code>${esc(JSON.stringify(inv,null,2))}</code></pre></details>`:'Invoice calibration unavailable';$('calibration').innerHTML=table(['Check','Executed result'],[['Annual measured demand',nf(c.annual_kwh,3)+' kWh'],['Annual measured gas',nf(c.annual_gas_smc)+' Smc'],['Invoice regression',invHeadline],['Annual electricity run rate',fmt(c.annual_electric_bill_eur,'eur')],['Annual gas run rate (approx.)',fmt(c.annual_gas_runrate_eur,'eur')],['Calibrated 6 kWp benchmark',fmt(c.calibrated_6kwp_eur,'eur')],['Original supplied cost formula',fmt(c.original_cost_formula_6kwp_eur,'eur')],['Supplied capture price / factor route',fmt(c.provided_capture_eur_kwh,'lcoe')+' / '+fmt(c.base_times_capture_eur_kwh,'lcoe')]]);
 $('roofCheck').innerHTML=table(['Orientation','PVGIS beam-shaded kWh/kWp','All-light shaded','Google DC','Google AC bridge'],c.roofs.map(r=>[r.orientation,range(r.pvgis_beam_shaded),range(r.pvgis_all_light_shaded),r.google_dc_kwh_kwp==null?'Unavailable':nf(r.google_dc_kwh_kwp),r.google_ac_kwh_kwp==null?'Unavailable':nf(r.google_ac_kwh_kwp)]));
 $('dataNotes').innerHTML=[a.timezone,a.leap_policy,a.resolution,a.shade_method].map(x=>`<p>${esc(x)}</p>`).join('');
 const market=c.research.research_results.turnkey_market_benchmarks;if($('market'))$('market').innerHTML=table(['Supplied benchmark','Value'],[['6 kWp turnkey',fmt(market.pv_installed_benchmark_6kwp_total_eur,'eur')],['PV range',market.pv_installed_eur_kwp_range.map(v=>fmt(v,'eur')).join(' – ')+' / kWp'],['Battery range',market.lfp_battery_storage_eur_kwh_usable_range.map(v=>fmt(v,'eur')).join(' – ')+' / usable kWh']]);
 initMarketCurve();
}
function clearHouseholdView(){
 activeResultProfile=null;activeResultJob=null;
 if($('profileResultsPanel'))$('profileResultsPanel').hidden=true;
 if($('profileResultHeader'))$('profileResultHeader').textContent='';
 if(householdViewsOpened){householdViewsOpened=false;}
 clearTimeout(timer);clearInterval(householdSyncInterval);householdSyncInterval=null;requestId++;candidateLoadId++;dayhourRequest++;
 if(sharedSync?.setScope)sharedSync.setScope(null);
 sharedSync=null;sharedUiReady=false;current=null;candidates=null;study=null;dayhourData=null;settings={};quote={line_items:[]};schema=null;
 saved=[];loanSaved=[];localStorage?.clearPrivate?.();
 document.querySelectorAll('main, aside, nav, header .header-actions, header .header-center').forEach(node=>{node.hidden=true;});
 if($('householdOnboarding'))$('householdOnboarding').hidden=false;
 $('status').textContent='Select or configure a household profile';
}
async function openHouseholdViews(){
 schema=await api('/api/schema');
 activeResultProfile=schema.profile||null;
 sharedSync=new SharedScenarioSync({request:api,onState:applySharedState,onStatus:message=>$('syncStatus').textContent=message});
 sharedSync.setScope(householdClient.profileId,householdClient.revision);
 sharedSync.receive(await api('/api/state'));
 if(activeResultProfile){
  const presentation=await api('/api/jobs',{operation:'presentation-context',settings:{},options:{}});
  schema.defaults=presentation.settings;schema.records=presentation.records;
  settings={...schema.defaults,...settings};delete settings.baseline_cooling_kwh;
 }
 renderControls();buildQuoteFields();renderLines();syncQuoteFields();initializeOptimizer();initBatterySizingControls();initSolarSizingControls();setupProfileResults();sharedUiReady=true;
 document.querySelectorAll('main, aside, nav, header .header-actions, header .header-center').forEach(node=>{node.hidden=false;});
 $('householdOnboarding').hidden=true;householdViewsOpened=true;
 householdSyncInterval=setInterval(pollSharedState,10000);
 $('status').textContent='Household ready';
 const fiscal=activeResultProfile?.fiscal.parameters,year=Number(settings.installation_year),horizon=Number(settings.horizon_years);
 const needsEligibility=(fiscal?.deduction_rate>0&&!settings.deduction_eligible_confirmed)||(settings.pmg_election&&!settings.pmg_eligible_confirmed);
 const needsProjection=fiscal&&!settings.unsupported_fiscal_year_acknowledged&&(String(year+1)+'-01-01'<fiscal.effective_from||String(year+horizon)+'-12-31'>=fiscal.effective_to);
 updateActionableWarnings();
 if(needsEligibility||needsProjection){$('status').textContent='Confirm fiscal eligibility/projection before calculating.';return;}
 await recompute({replace:true});
}
async function init(){try{
 initTheme();
 const sysTitle=$('systemTitle');
 if(sysTitle&&sysTitle.textContent.includes('Calculating')){
  sysTitle.setAttribute?.('aria-busy','true');
  sysTitle.innerHTML='<span class="progress-spinner" aria-hidden="true"></span> Calculating…';
 }
 if(typeof Onboarding!=='undefined'&&$('householdOnboarding')){
  householdClient=new HouseholdClient({storage:localStorage,onClear:clearHouseholdView,onStatus:message=>$('syncStatus').textContent=message});
  clearHouseholdView();
  householdSetup=Onboarding.mount({root:$('householdOnboarding'),client:householdClient,onReady:openHouseholdViews,onClear:clearHouseholdView});
  const setupButton=document.createElement('button');
  setupButton.type='button';
  setupButton.id='householdProfilesBtn';
  setupButton.className='button household-profiles-btn';
  setupButton.textContent='Household profiles';
  setupButton.title='Household profile setup (scroll to profile selector)';
  setupButton.setAttribute('aria-label','Household profiles: navigate to profile selector');
  setupButton.onclick=async()=>{
   const onboardingEl=$('householdOnboarding');
   const inOnboarding=onboardingEl&&!onboardingEl.hidden;
   if(!inOnboarding){
    householdClient.invalidate();
    clearHouseholdView();
    if(householdSetup?.refresh)await householdSetup.refresh({hydrate:true});
   }
   const target=document.getElementById('householdProfileSelector')||onboardingEl?.querySelector('select[name="profile"]')||document.getElementById('profileSetupGroup');
   const group=document.getElementById('profileSetupGroup');
   if(target){
    target.scrollIntoView({behavior:'smooth',block:'center'});
    try{target.focus({preventScroll:true});}catch(_){target.focus();}
    target.classList.remove('profile-selector-highlight');
    void target.offsetWidth;
    target.classList.add('profile-selector-highlight');
   }
   if(group&&group!==target){
    group.classList.remove('profile-selector-highlight');
    void group.offsetWidth;
    group.classList.add('profile-selector-highlight');
   }
  };
  document.querySelector('header')?.appendChild(setupButton);
  await householdSetup.refresh();return;
 }
 restoreSummaryPrefs();
 serverInstanceId=(await api('/api/health')).instance_id;
 schema=await api('/api/schema');
 sharedSync=new SharedScenarioSync({request:api,onState:applySharedState,onStatus:message=>$('syncStatus').textContent=message});
 sharedSync.receive(await api('/api/state'));
 renderControls();buildQuoteFields();renderLines();syncQuoteFields();initializeOptimizer();evidence();initBatterySizingControls();initSolarSizingControls();sharedUiReady=true;
 setInterval(pollSharedState,1500);window.addEventListener('online',()=>{recompute();pollSharedState();});
 await recompute({sync:false});
}catch(e){showError(e);$('syncStatus').textContent='Unable to load shared scenario';}}
$('defaultConfirm').addEventListener('close',()=>{const resolve=pendingDefaultConfirmation;pendingDefaultConfirmation=null;if(resolve)resolve($('defaultConfirm').returnValue==='save');});
$('reloadServer').onclick=async()=>{
 const button=$('reloadServer');if(button.disabled)return;
 button.disabled=true;clearTimeout(timer);
 try{
  if(!serverInstanceId)serverInstanceId=(await api('/api/health')).instance_id;
  await ServerReload.reloadServer({sync:sharedSync,settings,quote,request:api,onStatus:message=>$('status').textContent=message,waitForReady:previousId=>ServerReload.waitForNewServer(previousId),onReload:()=>window.location.reload()});
 }catch(error){
  $('status').textContent='Server reload failed · '+(error.message||error);
  showError(error);
 }finally{button.disabled=false;}
};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>switchView(b.dataset.view));
$('search').oninput=e=>updateSearch(e.target.value);
$('dayhourMode').onchange=()=>{selectDayhourMode();refreshDayhour();};
if($('dayhourPeriod'))$('dayhourPeriod').onchange=()=>{selectDayhourPeriod();refreshDayhour();};
if($('dayhourMonth'))$('dayhourMonth').onchange=()=>{syncWeatherDateToMonth();refreshDayhour();};
$('dayhourScenario').onchange=()=>{if(typeof refreshDayhour==='function')refreshDayhour();};
$('weatherRepresentative').onchange=refreshDayhour;
$('weatherYear').onchange=()=>{updateWeatherDateYear();refreshDayhour();};
$('weatherDate').onchange=()=>{syncMonthFromWeatherDate();refreshDayhour();};
$('hourSlider').oninput=()=>{selectedDayHour();if(dayhourData)renderDayhour(dayhourData);else refreshDayhour();};
$('previousHour').onclick=()=>{$('hourSlider').value=String(Math.max(0,Number($('hourSlider').value)-1));selectedDayHour();if(dayhourData)renderDayhour(dayhourData);else refreshDayhour();};$('nextHour').onclick=()=>{$('hourSlider').value=String(Math.min(23,Number($('hourSlider').value)+1));selectedDayHour();if(dayhourData)renderDayhour(dayhourData);else refreshDayhour();};
$('reset').onclick=()=>{discardAcDrafts();settings=deepClone(schema.defaults);normalizeBatterySettings(settings);clearQuote();renderControls();renderLines();syncQuoteFields();if(typeof syncSolarBatteryStep==='function')syncSolarBatteryStep();recompute({replace:true});};
$('save').onclick=saveCurrentScenario;
$('clearSaved').onclick=()=>{saved=[];localStorage.removeItem('pv-lab-scenarios-v1');renderSaved();};
$('download').onclick=()=>{const reason=getExportBlockReason('export scenario');if(reason){showError(new Error(reason));return;}const exportSettings=deepClone(current.result.settings||settings);delete exportSettings.hasItemized;if(typeof cleanSettingsForBackend==='function'){cleanSettingsForBackend(exportSettings);}else{for(const k of LEGACY_HEATING_KEYS)delete exportSettings[k];}const payload={...((typeof activeResultProfile!=='undefined'&&activeResultProfile)?{presentation:current._presentation}:{}),format:'pv-lab-pv-v1',settings:exportSettings,quote:deepClone(quote),result:deepClone(current.result)};download(payload,'pv-lab-scenario.json');};
$('loadFile').onchange=async e=>{try{const file=e.target?.files?.[0];if(!file)return;const text=await file.text();const obj=JSON.parse(text);if(!obj||typeof obj!=='object'||Array.isArray(obj))throw new Error('Import file must contain a scenario object.');if(obj.settings!==undefined){if(!obj.settings||typeof obj.settings!=='object'||Array.isArray(obj.settings))throw new Error('Import file settings must be an object.');if(obj.settings.heating_schedule_mode!==undefined&&!['scheduled','continuous'].includes(obj.settings.heating_schedule_mode))throw new Error(`Invalid heating_schedule_mode: ${obj.settings.heating_schedule_mode}. Must be 'scheduled' or 'continuous'.`);if(obj.settings.ac_heating_additional_demand_pct!==undefined){const rawPct=obj.settings.ac_heating_additional_demand_pct;if(typeof rawPct!=='number'||!Number.isFinite(rawPct)||rawPct<0){throw new Error('Invalid ac_heating_additional_demand_pct: must be a non-negative finite number.');}}}if(typeof activeResultProfile!=='undefined'&&activeResultProfile){if(obj.presentation?.profile_id!==householdClient.profileId||obj.presentation?.input_revision!==householdClient.revision)throw Error('Imported configuration belongs to a different profile revision.');profileCounts({settings:obj.settings});}const nextSettings=prepareScenarioSettings(obj.settings,false);if(!Object.hasOwn(obj.settings||{},'battery_mixed_sizes')){nextSettings.battery_mixed_sizes=false;nextSettings.battery_secondary_modules=0;}const batteryDirection=Object.hasOwn(obj.settings||{},'battery_kwh')?'battery_kwh':(['battery_modules','battery_module_kwh'].some(k=>Object.hasOwn(obj.settings||{},k))?'battery_modules':'battery_kwh');normalizeBatterySettings(nextSettings,batteryDirection);const nextQuote=deepClone(obj.quote||{line_items:[]});nextQuote.line_items ||= [];clearTimeout(timer);discardAcDrafts();settings=nextSettings;quote=nextQuote;renderControls();renderLines();syncQuoteFields();if(typeof syncSolarBatteryStep==='function')syncSolarBatteryStep();await recompute({replace:true});}catch(ex){showError(ex);}};
$('cashFlowBasis').onchange=()=>renderCashChart();$('onepage').onclick=onePage;if($('addLine'))$('addLine').onclick=()=>{quote.line_items.push({description:'',quantity:1,net_eur:null,vat:.1});renderLines();schedule();};
$('loanModeControl').onchange=e=>{settings.loan_mode=e.target.value;syncLoanControls();schedule();};
const loanInputMap={loanAmountControl:['loan_amount',1,'amount'],loanFractionControl:['loan_fraction',.01,'fraction'],loanAprControl:['loan_apr',.01],loanYearsControl:['loan_years',1],loanFeesControl:['loan_upfront_fees_or_tax',1],loanMaxMonthlyPaymentControl:['loan_max_monthly_payment',1,null,true],loanInflationControl:['inflation',.01],loanDiscountControl:['real_discount_rate',.01]};
Object.entries(loanInputMap).forEach(([id,[key,scale,mode,nullable]])=>{const el=$(id);if(!el)return;el.oninput=e=>{if(!e.target.validity.valid||e.target.value===''&&!nullable){loanPending=true;acceptedInputFingerprint=null;invalidateLoanSolve();if($('saveLoanSimulation'))$('saveLoanSimulation').disabled=true;if($('loanSaveStatus'))$('loanSaveStatus').textContent='Enter a valid value to calculate and save this simulation.';return;}if(mode)settings.loan_mode=mode;settings[key]=e.target.value===''?null:Number(e.target.value)*scale;if(mode==='amount')settings.loan_amount=settings[key];syncLoanControls();if($('loanSaveStatus'))$('loanSaveStatus').textContent='';schedule();};});
if($('loanCurveUndiscounted'))$('loanCurveUndiscounted').onclick=()=>setLoanCurveBasis('undiscounted');
if($('loanCurveDiscounted'))$('loanCurveDiscounted').onclick=()=>setLoanCurveBasis('discounted');
$('loanScopeControl').onchange=e=>{editSetting('loan_include_electrification',e.target.value==='solar_and_conversion',e.target);};
$('loanScopeControl').oninput=$('loanScopeControl').onchange;
$('solveLoanAmount').onclick=async()=>{if(loanPending||!loanControlsValid()||acceptedInputFingerprint!==inputFingerprint()||!current)return;const token=++loanSolveToken,fingerprint=inputFingerprint(),button=$('solveLoanAmount');button.disabled=true;$('loanSolveStatus').textContent='Searching feasible loan amounts…';$('loanSolveResult').textContent='';try{const result=await api('/api/loan-solve',{settings,quote});if(token!==loanSolveToken||fingerprint!==inputFingerprint())return;loanSolveResult=result;if(result.status==='OPTIMAL'){const year20=result.median_year20_discounted_impact_eur==null?'Not simulated (horizon '+nf(result.horizon_years)+' y)':(result.median_year20_discounted_impact_eur>=0?'+':'')+fmt(result.median_year20_discounted_impact_eur,'eur');const recoveryNote=result.median_discounted_recovery_years===0?'0 y means median paths start with no personal outlay and stay at or above break-even through the horizon; repayments still occur.':'';$('loanSolveStatus').textContent='Best recovery found across '+nf(result.weather_count)+' weather paths.';$('loanSolveResult').innerHTML='<p><strong>Optimal loan amount: '+fmt(result.optimal_amount_eur,'eur2')+'</strong><br>Scheduled monthly payment: '+fmt(result.optimal_monthly_payment_eur,'eur2')+'<br>Monthly payment limit: '+(result.max_monthly_payment_eur==null?'No limit':fmt(result.max_monthly_payment_eur,'eur2'))+'<br>Median discounted recovery: '+fmt(result.median_discounted_recovery_years,'years')+'<br>Median initial cash: '+fmt(result.median_initial_cash_eur,'eur')+'<br>At year 20 · discounted impact: '+year20+'<br>Eligible cap: '+fmt(result.eligible_cap_eur,'eur2')+'<br>Maximum loan within payment limit: '+fmt(result.affordable_search_cap_eur,'eur2')+'</p>'+(recoveryNote?'<p class="muted">'+recoveryNote+'</p>':'')+'<p class="muted">'+esc(result.definition)+' Resolution: €1.</p><button id="applyOptimalLoan" type="button">Apply optimal loan amount</button>';$('applyOptimalLoan').onclick=()=>{settings.loan_mode='amount';settings.loan_amount=result.optimal_amount_eur;settings.loan_fraction=0;syncLoanControls();schedule();$('loanSolveStatus').textContent='Optimal amount applied. Recalculating current scenario…';};}else{const constrained=result.affordable_search_cap_eur+1e-8<result.eligible_cap_eur;$('loanSolveStatus').textContent='No feasible amount reaches persistent discounted recovery in the simulation horizon.';$('loanSolveResult').innerHTML='<p class="muted">'+(constrained?'No recovery was found through the maximum loan within the payment limit of '+fmt(result.affordable_search_cap_eur,'eur2')+' (eligible cap '+fmt(result.eligible_cap_eur,'eur2')+').':'No recovery was found through the full eligible cap of '+fmt(result.eligible_cap_eur,'eur2')+'.')+' No amount was applied.</p><p class="muted">'+esc(result.definition)+'</p>';}}catch(error){if(token===loanSolveToken){$('loanSolveStatus').textContent='Loan search failed: '+(error.message||error);}}finally{if(token===loanSolveToken)button.disabled=loanPending||!loanControlsValid()||acceptedInputFingerprint!==inputFingerprint();}};
$('saveLoanSimulation').onclick=()=>{const name=$('loanSaveName').value.trim();if(!name){$('loanSaveName').focus();$('loanSaveStatus').textContent='Enter a name for this simulation.';return;}if(!loanControlsValid()||!acceptedLoanInputsCurrent()){$('loanSaveStatus').textContent='Wait for valid current inputs to finish calculating before saving.';return;}const entry={id:Date.now()+'-'+Math.random().toString(36).slice(2),name,settings:deepClone(settings),quote:deepClone(quote),result:deepClone(current.result),metrics:currentLoanMetrics(current.result),savedAt:new Date().toISOString()};loanSaved.push(entry);if(persistLoanSimulations()){$('loanSaveName').value='';$('loanSaveStatus').textContent='Saved as an unchanged snapshot in this browser.';renderLoanPivot();}};
if($('fairMetric'))$('fairMetric').onchange=()=>{$('fairTarget').value=$('fairMetric').value==='npv'?0:$('fairMetric').value==='irr'?.05:$('fairMetric').value==='lcoe'?.10:10;};
if($('solve'))$('solve').onclick=async()=>{try{$('solve').disabled=true;if($('fairResult'))$('fairResult').textContent='Solving across weather years…';const r=await api('/api/fair-price',{settings:current?.result.settings||settings,metric:$('fairMetric')?.value,target:Number($('fairTarget')?.value)});if($('fairResult'))$('fairResult').innerHTML=r.price_eur==null?`<p>${esc(r.status)}: target not bracketed or no PV.</p>`:`<h2>${range(r.price_range_eur,'eur')}</h2><p class="muted">Pending weather range for this target. ${esc(r.definition)}. ${esc(r.residual_definition)}</p>`;}catch(e){if($('fairResult'))$('fairResult').textContent='Price solve failed: '+(e.message||e);showError(e);}finally{if($('solve'))$('solve').disabled=false;}};
Object.entries(roofLimitInputMap).forEach(([id,roofKey])=>{const el=$(id);if(el){el.addEventListener('input',e=>handleRoofLimitInput(e,roofKey));el.addEventListener('change',e=>handleRoofLimitInput(e,roofKey));}});
if($('resetRoofLimits'))$('resetRoofLimits').addEventListener('click',resetRoofCapacityLimits);
['optPanelWatts','optModuleKwh','optBatteryMin','optBatteryMax','optInverter','optCharge','optDischarge','filterLoad','optObjective','includeTheoretical'].forEach(id=>{const el=$(id);if(el){el.addEventListener('input',optimizerChanged);el.addEventListener('change',optimizerChanged);}});
const optimizerSettingMap={optPanelWatts:'panel_watts',optModuleKwh:'battery_module_kwh',optInverter:'inverter_kw',optCharge:'charge_kw',optDischarge:'discharge_kw'};
Object.entries(optimizerSettingMap).forEach(([id,key])=>{const el=$(id);if(el)el.addEventListener('change',e=>{const value=Number(e.target.value);if(!Number.isFinite(value)||value<0||(['panel_watts','inverter_kw'].includes(key)&&value===0)){e.target.setAttribute('aria-invalid','true');if($('paretoMethod'))$('paretoMethod').textContent=`Enter a valid ${title(key)} value.`;return;}e.target.removeAttribute('aria-invalid');editSetting(key,value,e.target);});});
['chartX','chartY'].forEach(id=>{const el=$(id);if(el)el.onchange=()=>{saveOptimizerPrefs();renderCandidates();};});
if($('recomputePareto'))$('recomputePareto').onclick=()=>loadCandidates();
if($('fullStudy'))$('fullStudy').onclick=async()=>{let token=0;try{const studySettings=current?.result?.settings||settings;if(typeof shouldBlockAcRecompute==='function'&&shouldBlockAcRecompute(studySettings)){showError(new Error('Cannot run full study with invalid heating draft.'));return;}token=++studyRequestId;studyFingerprint=typeof inputFingerprint==='function'?inputFingerprint(studySettings,{}):null;studyDispatchedSettings=deepClone?deepClone(studySettings):JSON.parse(JSON.stringify(studySettings));const dispatched=studyDispatchedSettings;await api('/api/study',{settings:studySettings});if(token!==studyRequestId||(studyFingerprint!==null&&studyFingerprint!==(typeof inputFingerprint==='function'?inputFingerprint(current?.result?.settings||settings,{}):null)))return;pollJob(token,studyFingerprint,dispatched);}catch(e){if(token&&studyRequestId===token)showError(e);}};

/* BATTERY SIZING SECTION */
const BATTERY_STORAGE_KEY = 'pv-lab-battery-sizing-v1';
let batteryData = null, batteryRequestId = 0, batteryInFlight = false, batteryAcceptedFingerprint = null;

function markBatterySizingStale() {
  batteryRequestId++;
  batteryInFlight = false;
  batteryAcceptedFingerprint = null;
  const wrap = $('batResultsWrap');
  if (wrap && batteryData) {
    wrap.classList.add('stale');
  }
  const btn = $('runBatterySizing');
  if (btn) btn.disabled = false;
  if ($('batStatus') && batteryData) {
    $('batStatus').textContent = 'Inputs changed · Click Run sizing analysis to update';
  }
}

function getBatteryOptions() {
  const q10Val = $('batQuote10') ? $('batQuote10').value.trim() : '';
  const q15Val = $('batQuote15') ? $('batQuote15').value.trim() : '';
  const isMixed = Boolean(typeof settings !== 'undefined' && settings.battery_mixed_sizes);
  const stepVal = Number($('batStep') ? $('batStep').value : 1);
  const maxCapVal = Number($('batMaxCapacity') ? $('batMaxCapacity').value : 25);
  const lifetimeVal = Number($('batLifetime') ? $('batLifetime').value : 12);
  const opts = {
    load_scenario: $('batScenario') ? $('batScenario').value : (settings?.load_scenario || 'electrified'),
    weather_representative: $('batRepresentative') ? $('batRepresentative').value : 'median',
    max_capacity: maxCapVal,
    quote_10kwh_eur: q10Val === '' ? null : Number(q10Val),
    quote_15kwh_eur: q15Val === '' ? null : Number(q15Val),
    battery_lifetime_years: lifetimeVal,
    battery_degradation_annual: $('batDegradation') ? Number($('batDegradation').value) * 0.01 : 0.015,
    deduction_rate: $('batDeduction') ? Number($('batDeduction').value) : (settings?.deduction_rate ?? 0.5),
    sensitivity_cost_delta_pct: $('batCostSens') ? Number($('batCostSens').value) * 0.01 : 0,
    sensitivity_spread_delta: $('batSpreadDelta') ? Number($('batSpreadDelta').value) : 0
  };
  if (!isMixed) {
    opts.step = stepVal;
  }
  return opts;
}

function shouldBlockBatterySizingForAcDraft(options = null, s = settings) {
  if (!s) return false;
  const effScenario = (options && options.load_scenario) || ($('batScenario') ? $('batScenario').value : s.load_scenario);
  const sizingSettings = { ...s, load_scenario: effScenario };
  return Boolean(typeof shouldBlockAcRecompute === 'function' && shouldBlockAcRecompute(sizingSettings));
}

function batteryStateFingerprint(customSettings = settings, customQuote = quote, customOptions = null) {
  try {
    const freshSettings = deepClone(customSettings);
    normalizeBatterySettings(freshSettings);
    delete freshSettings.hasItemized;
    cleanSettingsForBackend(freshSettings);
    const freshQuote = deepClone(customQuote);
    const opts = customOptions ? deepClone(customOptions) : getBatteryOptions();
    const canonical = value => Array.isArray(value) ? value.map(canonical) : (value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value);
    return JSON.stringify(canonical({ s: freshSettings, q: freshQuote, o: opts }));
  } catch (_) {
    return 'invalid_battery_state';
  }
}

function isBatterySizingRequestValid(token, requestFingerprint, options = null) {
  if (token !== batteryRequestId) return false;
  if (shouldBlockBatterySizingForAcDraft(options)) return false;
  if (requestFingerprint !== batteryStateFingerprint()) return false;
  return true;
}

function isBatterySizingMatching(data = batteryData) {
  if (!data) return false;
  if (batteryAcceptedFingerprint === null) return false;
  if (shouldBlockBatterySizingForAcDraft()) return false;
  if (batteryAcceptedFingerprint !== batteryStateFingerprint()) return false;
  const wrap = $('batResultsWrap');
  if (wrap && wrap.classList.contains('stale')) return false;
  return true;
}

function saveBatteryOptionsToStorage() {
  try {
    const opts = {
      load_scenario: $('batScenario') ? $('batScenario').value : 'electrified',
      weather_representative: $('batRepresentative') ? $('batRepresentative').value : 'median',
      step: $('batStep') ? Number($('batStep').value) : 1,
      max_capacity: $('batMaxCapacity') ? Number($('batMaxCapacity').value) : 25,
      quote_10kwh_eur: $('batQuote10') && $('batQuote10').value !== '' ? Number($('batQuote10').value) : null,
      quote_15kwh_eur: $('batQuote15') && $('batQuote15').value !== '' ? Number($('batQuote15').value) : null,
      battery_lifetime_years: $('batLifetime') ? Number($('batLifetime').value) : 12,
      battery_degradation_pct: $('batDegradation') ? Number($('batDegradation').value) : 1.5,
      deduction_rate: $('batDeduction') ? Number($('batDeduction').value) : 0.5,
      sensitivity_cost_delta_pct: $('batCostSens') ? Number($('batCostSens').value) : 0,
      sensitivity_spread_delta: $('batSpreadDelta') ? Number($('batSpreadDelta').value) : 0
    };
    localStorage.setItem(BATTERY_STORAGE_KEY, JSON.stringify(opts));
  } catch (_) {}
}

function loadBatteryOptionsFromStorage() {
  try {
    const raw = localStorage.getItem(BATTERY_STORAGE_KEY);
    if (!raw) return;
    const opts = JSON.parse(raw);
    if (!opts || typeof opts !== 'object') return;
    if (opts.load_scenario && $('batScenario')) $('batScenario').value = opts.load_scenario;
    if (opts.weather_representative && $('batRepresentative')) $('batRepresentative').value = opts.weather_representative;
    if (opts.step != null && $('batStep')) $('batStep').value = String(opts.step);
    if (opts.max_capacity != null && $('batMaxCapacity')) $('batMaxCapacity').value = String(opts.max_capacity);
    if ($('batQuote10')) $('batQuote10').value = opts.quote_10kwh_eur != null ? String(opts.quote_10kwh_eur) : '';
    if ($('batQuote15')) $('batQuote15').value = opts.quote_15kwh_eur != null ? String(opts.quote_15kwh_eur) : '';
    if (opts.battery_lifetime_years != null && $('batLifetime')) $('batLifetime').value = String(opts.battery_lifetime_years);
    if (opts.battery_degradation_pct != null && $('batDegradation')) $('batDegradation').value = String(opts.battery_degradation_pct);
    if (opts.deduction_rate != null && $('batDeduction')) $('batDeduction').value = String(opts.deduction_rate);
    if (opts.sensitivity_cost_delta_pct != null && $('batCostSens')) $('batCostSens').value = String(opts.sensitivity_cost_delta_pct);
    if (opts.sensitivity_spread_delta != null && $('batSpreadDelta')) $('batSpreadDelta').value = String(opts.sensitivity_spread_delta);
  } catch (_) {}
}

const batteryControlIds = [
  'batScenario', 'batRepresentative', 'batStep', 'batMaxCapacity',
  'batQuote10', 'batQuote15', 'batLifetime', 'batDegradation',
  'batDeduction', 'batCostSens', 'batSpreadDelta'
];

function syncBatterySizingControls() {
  const isMixed = Boolean(typeof settings !== 'undefined' && settings.battery_mixed_sizes);
  const stepEl = $('batStep');
  if (stepEl) {
    if (isMixed) {
      stepEl.disabled = true;
      stepEl.hidden = true;
      const parentLabel = stepEl.closest ? stepEl.closest('label') : null;
      if (parentLabel) parentLabel.style.display = 'none';
    } else {
      stepEl.disabled = false;
      stepEl.hidden = false;
      const parentLabel = stepEl.closest ? stepEl.closest('label') : null;
      if (parentLabel) parentLabel.style.display = '';
    }
  }
}

let batteryControlsInitialized = false;
function initBatterySizingControls() {
  loadBatteryOptionsFromStorage();
  syncBatterySizingControls();
  if (batteryControlsInitialized) return;
  batteryControlsInitialized = true;
  batteryControlIds.forEach(id => {
    const el = $(id);
    if (!el) return;
    const onMod = () => {
      saveBatteryOptionsToStorage();
      markBatterySizingStale();
      if (id === 'batScenario') {
        if (typeof editSetting === 'function' && typeof settings !== 'undefined' && el.value !== settings.load_scenario) {
          editSetting('load_scenario', el.value, el);
        }
      }
      if (id === 'batQuote10' || id === 'batQuote15') {
        if (typeof markSolarSizingStale === 'function') markSolarSizingStale();
      }
    };
    el.addEventListener('input', onMod);
    el.addEventListener('change', onMod);
  });
  if ($('runBatterySizing')) {
    $('runBatterySizing').onclick = runBatterySizingAnalysis;
  }
}
initBatterySizingControls();

function renderBatterySizing(data) {
  batteryData = data;
  syncBatterySizingControls();
  hideChartTooltip();
  const eco = data.economics;
  
  const isMixedGrid = data.chart1_capacity_dispatch?.grid_mode === 'mixed' || data.grid_mode === 'mixed' || Boolean(typeof settings !== 'undefined' && settings.battery_mixed_sizes);
  if ($('batStatus')) {
    const stepDesc = isMixedGrid ? 'attainable capacity combinations' : 'capacity steps';
    	const batMeta = getWeatherEnsembleMeta(data || current?.result);
	const batAnaloguesDesc = batMeta.count ? `${batMeta.count} historical PVGIS analogues` : 'historical PVGIS analogues';
	$('batStatus').textContent = `${data.chart1_capacity_dispatch?.capacities?.length ?? 0} ${stepDesc} (0–${data.options?.max_capacity ?? 25} kWh) · ${batAnaloguesDesc} · Scenario: ${dayhourScenarioLabel(data.options?.load_scenario)}`;
  }
  
  if ($('batEconomicsBanner')) $('batEconomicsBanner').innerHTML = `
    <span class="pill">Marginal battery cost: <strong>${fmt(eco.effective_marginal_battery_cost_eur_kwh, 'eur')}/kWh</strong> (${esc(eco.marginal_cost_source)})</span>
    <span class="pill">Annualized cost (net deduction): <strong>${fmt(eco.annualized_marginal_cost_net, 'eur')}/kWh/yr</strong></span>
    <span class="pill">Annualized cost (gross): <strong>${fmt(eco.annualized_marginal_cost_gross, 'eur')}/kWh/yr</strong></span>
    <span class="pill">Electricity spread: <strong>${fmt(eco.base_spread_eur_kwh, 'eur2')}/kWh</strong></span>
    <span class="pill">RTE margin/cycle: <strong>${fmt(eco.margin_per_charge_cycle_eur_kwh, 'eur2')}/kWh</strong></span>
    <span class="pill">Wholesale profile: <strong>${esc(eco.export_price_active_source || eco.export_price_profile || 'Empirical CSUD')}</strong></span>
  `;
  
  if ($('batSourceVsScenario')) $('batSourceVsScenario').innerHTML = table(
    ['Parameter / Assumption', 'Value', 'Source & Provenance', 'Status'],
    data.source_vs_scenario.map(r => [r.item, r.value, r.source, r.status])
  );
  if ($('batLoadNote')) $('batLoadNote').textContent = data.load_representativeness_note;
  if ($('batMarginalTitle')) {
    const opt = data.chart2_incremental_savings?.optimum_band;
    if (opt?.ensemble_optimum_kwh != null) {
      const spread = (opt.weather_min_kwh != null && opt.weather_max_kwh != null)
        ? ` (weather spread: ${nf(opt.weather_min_kwh, 1)}–${nf(opt.weather_max_kwh, 1)} kWh)`
        : '';
      $('batMarginalTitle').textContent = `Marginal battery savings · First-year annualized heuristic identifies ${nf(opt.ensemble_optimum_kwh, 1)} kWh${spread} (compare lifetime NPV)`;
    } else {
      $('batMarginalTitle').textContent = 'Marginal battery savings (€/usable kWh/yr) vs annualized installed battery cost';
    }
  }
  
  renderBatteryDispatchChart(data);
  renderBatteryIncrementalChart(data);
  renderBatteryEfcChart(data);
  renderBatteryNpvHeatmap(data);
  renderBatteryCdfChart(data);
  renderBatterySocHeatmap(data);
}

function renderBatteryDispatchChart(data) {
  const target = $('batChart1');
  if (!target) return;
  const caps = data.chart1_capacity_dispatch.capacities;
  const width = 720, height = 280, left = 60, right = 660, top = 20, bottom = 230;
  const plotW = right - left, plotH = bottom - top;
  const maxCap = Math.max(1, ...caps.map(c => c.capacity_kwh));
  const maxImp = Math.max(1, ...caps.map(c => c.grid_import_kwh.max)) * 1.1;
  
  const x = c => left + (c / maxCap) * plotW;
  const yPct = p => bottom - p * plotH;
  const yImp = imp => bottom - (imp / maxImp) * plotH;
  
  let body = '';
  for (let f = 0; f <= 1; f += 0.25) {
    const yy = bottom - f * plotH;
    body += `<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/>`;
    body += `<text x="${left - 8}" y="${yy + 4}" text-anchor="end" font-size="10" fill="#62756e">${Math.round(f * 100)}%</text>`;
    body += `<text x="${right + 8}" y="${yy + 4}" text-anchor="start" font-size="10" fill="#df8a54">${nf(maxImp * f)}</text>`;
  }
  body += `<text x="${left - 8}" y="${top - 6}" text-anchor="end" font-size="9" fill="#62756e">Autarky / SC</text>`;
  body += `<text x="${right + 8}" y="${top - 6}" text-anchor="start" font-size="9" fill="#df8a54">Grid import kWh</text>`;
  
  const autarkyBand = caps.map(c => `${x(c.capacity_kwh)},${yPct(c.autarky.max)}`).join(' ') + ' ' +
    [...caps].reverse().map(c => `${x(c.capacity_kwh)},${yPct(c.autarky.min)}`).join(' ');
  const scBand = caps.map(c => `${x(c.capacity_kwh)},${yPct(c.self_consumption.max)}`).join(' ') + ' ' +
    [...caps].reverse().map(c => `${x(c.capacity_kwh)},${yPct(c.self_consumption.min)}`).join(' ');
  const impBand = caps.map(c => `${x(c.capacity_kwh)},${yImp(c.grid_import_kwh.max)}`).join(' ') + ' ' +
    [...caps].reverse().map(c => `${x(c.capacity_kwh)},${yImp(c.grid_import_kwh.min)}`).join(' ');
    
  body += `<polygon points="${autarkyBand}" fill="#217a61" fill-opacity="0.12"/>`;
  body += `<polygon points="${scBand}" fill="#6285bd" fill-opacity="0.12"/>`;
  body += `<polygon points="${impBand}" fill="#df8a54" fill-opacity="0.12"/>`;
  
  body += `<polyline points="${caps.filter(c => c.autarky.mean != null).map(c => `${x(c.capacity_kwh)},${yPct(c.autarky.mean)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="2.5"/>`;
  body += `<polyline points="${caps.filter(c => c.self_consumption.mean != null).map(c => `${x(c.capacity_kwh)},${yPct(c.self_consumption.mean)}`).join(' ')}" fill="none" stroke="#6285bd" stroke-width="2.5"/>`;
  body += `<polyline points="${caps.filter(c => c.grid_import_kwh.mean != null).map(c => `${x(c.capacity_kwh)},${yImp(c.grid_import_kwh.mean)}`).join(' ')}" fill="none" stroke="#df8a54" stroke-width="2.5"/>`;
  
  caps.forEach(c => {
    const cx = x(c.capacity_kwh);
    const autarkyVal = c.autarky.mean != null ? fmt(c.autarky.mean, 'pct') : 'Unavailable';
    const scVal = c.self_consumption.mean != null ? fmt(c.self_consumption.mean, 'pct') : 'Unavailable';
    const impVal = c.grid_import_kwh.mean != null ? `${nf(c.grid_import_kwh.mean)} kWh` : 'Unavailable';
    const savVal = c.annual_savings_eur.mean != null ? `€${nf(c.annual_savings_eur.mean)}/yr` : 'Unavailable';
    const detail = `Capacity: ${c.capacity_kwh} kWh\nAutarky (ensemble mean): ${autarkyVal} (weather min–max: ${fmt(c.autarky.min, 'pct')} – ${fmt(c.autarky.max, 'pct')})\nSelf-consumption (ensemble mean): ${scVal} (weather: ${fmt(c.self_consumption.min, 'pct')} – ${fmt(c.self_consumption.max, 'pct')})\nAnnual grid imports (ensemble mean): ${impVal} (${nf(c.grid_import_kwh.min)}–${nf(c.grid_import_kwh.max)})\nAnnual savings vs no battery (ensemble mean): ${savVal}`;
    body += `<g ${chartTarget(detail)}>${c.autarky.mean != null ? `<circle cx="${cx}" cy="${yPct(c.autarky.mean)}" r="3" fill="#217a61"/>` : ''}${c.self_consumption.mean != null ? `<circle cx="${cx}" cy="${yPct(c.self_consumption.mean)}" r="3" fill="#6285bd"/>` : ''}${c.grid_import_kwh.mean != null ? `<circle cx="${cx}" cy="${yImp(c.grid_import_kwh.mean)}" r="3" fill="#df8a54"/>` : ''}<rect class="chart-hit" x="${cx - 6}" y="${top}" width="12" height="${plotH}"/></g>`;
  });
  
  caps.forEach(c => {
    if (c.capacity_kwh % 5 === 0 || c.capacity_kwh === maxCap) {
      body += `<text x="${x(c.capacity_kwh)}" y="${bottom + 16}" text-anchor="middle" font-size="10" fill="#62756e">${c.capacity_kwh} kWh</text>`;
    }
  });
  
  target.innerHTML = chartSvg(`Battery capacity from 0 to 25 kWh: annual grid imports, self-consumption and autarky across ${getWeatherEnsembleMeta(data || current?.result).count ? getWeatherEnsembleMeta(data || current?.result).count + ' ' : ''}PVGIS weather analogues`, body, height, width);
  if ($('batTable1')) $('batTable1').innerHTML = table(
    ['Capacity', 'Autarky (ensemble mean)', 'Autarky (weather min–max)', 'Self-consumption (ensemble mean)', 'Grid imports (ensemble mean kWh)', 'Annual savings vs 0 kWh (ensemble mean)'],
    caps.map(c => [
      `${c.capacity_kwh} kWh`,
      c.autarky.mean != null ? fmt(c.autarky.mean, 'pct') : '—',
      `${fmt(c.autarky.min, 'pct')} – ${fmt(c.autarky.max, 'pct')}`,
      c.self_consumption.mean != null ? fmt(c.self_consumption.mean, 'pct') : '—',
      c.grid_import_kwh.mean != null ? nf(c.grid_import_kwh.mean) : '—',
      c.annual_savings_eur.mean != null ? `€${nf(c.annual_savings_eur.mean)}/yr` : '—'
    ])
  );
}

function renderBatteryIncrementalChart(data) {
  const target = $('batChart2');
  if (!target) return;
  const inc = data.chart2_incremental_savings;
  const steps = inc.steps;
  const width = 720, height = 300, left = 60, right = 680, top = 25, bottom = 240;
  const plotW = right - left, plotH = bottom - top;
  
  const maxCap = Math.max(1, ...steps.map(s => s.capacity_to));
  const allCostsNet = steps.map(s => s.annualized_cost_net ?? inc.annualized_marginal_cost_net);
  const allCostsGross = steps.map(s => s.annualized_cost_gross ?? inc.annualized_marginal_cost_gross);
  const minVal = Math.min(0, ...steps.map(s => s.incremental_savings_eur_kwh.min), ...allCostsNet, ...allCostsGross);
  const maxVal = Math.max(inc.annualized_marginal_cost_gross * 1.3, ...steps.map(s => s.incremental_savings_eur_kwh.max), ...allCostsGross, 10) * 1.15;
  const rangeVal = Math.max(1, maxVal - minVal);
  
  const x = c => left + (c / maxCap) * plotW;
  const y = v => bottom - ((v - minVal) / rangeVal) * plotH;
  const yZero = y(0);
  
  let body = '';
  const yStep = rangeVal > 80 ? 20 : (rangeVal > 40 ? 10 : 5);
  const startGrid = Math.floor(minVal / yStep) * yStep;
  for (let v = startGrid; v <= maxVal; v += yStep) {
    const yy = y(v);
    if (yy >= top && yy <= bottom) {
      body += `<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/>`;
      body += `<text x="${left - 8}" y="${yy + 4}" text-anchor="end" font-size="10" fill="#62756e">€${nf(v)}</text>`;
    }
  }
  body += `<line x1="${left}" x2="${right}" y1="${yZero}" y2="${yZero}" stroke="#62756e" stroke-width="1.2"/>`;
  body += `<text x="${left - 8}" y="${top - 8}" text-anchor="end" font-size="9" fill="#62756e">€/kWh/yr</text>`;
  
  const opt = inc.optimum_band;
  if (opt?.ensemble_optimum_kwh != null && opt.ensemble_optimum_kwh >= 0) {
    if (opt.weather_max_kwh != null && opt.weather_max_kwh > 0) {
      const matchingWeatherStep = steps.find(s => Math.abs(s.capacity_to - opt.weather_min_kwh) < 1e-4);
      const bandFrom = matchingWeatherStep ? matchingWeatherStep.capacity_from : Math.max(0, opt.weather_min_kwh - (steps[0]?.delta_capacity_kwh || 1));
      const bx0 = x(bandFrom);
      const bx1 = x(opt.weather_max_kwh);
      body += `<rect x="${bx0}" y="${top}" width="${Math.max(8, bx1 - bx0)}" height="${plotH}" fill="#217a61" fill-opacity="0.08"/>`;
    }
    const matchingStep = steps.find(s => Math.abs(s.capacity_to - opt.ensemble_optimum_kwh) < 1e-4);
    const optFrom = matchingStep ? matchingStep.capacity_from : Math.max(0, opt.ensemble_optimum_kwh - (steps[0]?.delta_capacity_kwh || 1));
    const x0 = x(optFrom);
    const x1 = x(opt.ensemble_optimum_kwh);
    body += `<rect x="${x0}" y="${top}" width="${Math.max(8, x1 - x0)}" height="${plotH}" fill="#217a61" fill-opacity="0.16" stroke="#185a46" stroke-width="1.2" stroke-dasharray="3 2"/>`;
    const spreadLabel = (opt.weather_min_kwh != null && opt.weather_max_kwh != null) ? ` (weather spread ${nf(opt.weather_min_kwh, 1)}–${nf(opt.weather_max_kwh, 1)} kWh)` : '';
    body += `<text x="${(x0 + x1) / 2}" y="${top + 14}" text-anchor="middle" font-size="10" font-weight="600" fill="#185a46">Ensemble optimum ${nf(opt.ensemble_optimum_kwh, 1)} kWh${spreadLabel}</text>`;
  }
  
  const isVarying = Boolean(inc.marginal_cost_varies_by_step) || steps.some(st => 
    st.annualized_cost_net != null && Math.abs(st.annualized_cost_net - inc.annualized_marginal_cost_net) > 0.05
  );

  const yGross = y(inc.annualized_marginal_cost_gross);
  const yNet = y(inc.annualized_marginal_cost_net);

  if (isVarying) {
    let grossPath = '';
    let netPath = '';
    steps.forEach((st, idx) => {
      const x0 = x(st.capacity_from);
      const x1 = x(st.capacity_to);
      const yg = y(st.annualized_cost_gross ?? inc.annualized_marginal_cost_gross);
      const yn = y(st.annualized_cost_net ?? inc.annualized_marginal_cost_net);
      if (idx === 0) {
        grossPath += `M ${x0} ${yg} L ${x1} ${yg}`;
        netPath += `M ${x0} ${yn} L ${x1} ${yn}`;
      } else {
        grossPath += ` L ${x0} ${yg} L ${x1} ${yg}`;
        netPath += ` L ${x0} ${yn} L ${x1} ${yn}`;
      }
    });
    body += `<path d="${grossPath}" fill="none" stroke="#7b827c" stroke-width="2" stroke-dasharray="4 3"/>`;
    body += `<path d="${netPath}" fill="none" stroke="#bd8334" stroke-width="2.5" stroke-dasharray="6 4"/>`;
    body += `<text x="${right - 6}" y="${top + 10}" text-anchor="end" font-size="10" font-weight="600" fill="#bd8334">Net marginal cost (varying by step)</text>`;
    body += `<text x="${right - 6}" y="${top + 22}" text-anchor="end" font-size="10" fill="#62756e">Gross marginal cost (varying by step)</text>`;
  } else {
    if (yGross >= top && yGross <= bottom) {
      body += `<line x1="${left}" x2="${right}" y1="${yGross}" y2="${yGross}" stroke="#7b827c" stroke-width="2" stroke-dasharray="4 3"/>`;
      body += `<text x="${right - 6}" y="${yGross - 5}" text-anchor="end" font-size="10" fill="#62756e">Gross marginal cost: €${nf(inc.annualized_marginal_cost_gross, 1)}/kWh/yr</text>`;
    }
    if (yNet >= top && yNet <= bottom) {
      body += `<line x1="${left}" x2="${right}" y1="${yNet}" y2="${yNet}" stroke="#bd8334" stroke-width="2.5" stroke-dasharray="6 4"/>`;
      body += `<text x="${right - 6}" y="${yNet - 5}" text-anchor="end" font-size="10" font-weight="600" fill="#bd8334">Net marginal cost (tax-deducted): €${nf(inc.annualized_marginal_cost_net, 1)}/kWh/yr</text>`;
    }
  }
  
  steps.forEach(st => {
    const cx = (x(st.capacity_from) + x(st.capacity_to)) / 2;
    const band = st.incremental_savings_eur_kwh;
    const barW = Math.max(4, plotW / steps.length * 0.7);
    const stepCostNet = st.annualized_cost_net != null ? st.annualized_cost_net : inc.annualized_marginal_cost_net;
    const stepCostGross = st.annualized_cost_gross != null ? st.annualized_cost_gross : inc.annualized_marginal_cost_gross;
    const incSavingsMean = band.mean;
    const netSurplus = incSavingsMean != null ? incSavingsMean - stepCostNet : null;
    const detail = `Step: ${nf(st.capacity_from, 1)} → ${nf(st.capacity_to, 1)} kWh (+${nf(st.delta_capacity_kwh, 1)} kWh)\nTrue incremental savings (ensemble mean): ${incSavingsMean != null ? `€${nf(incSavingsMean, 2)}/kWh/year` : 'Unavailable'}\nWeather range: €${nf(band.min, 2)} – €${nf(band.max, 2)}/kWh/yr\nNet marginal cost: €${nf(stepCostNet, 2)}/kWh/yr\nGross marginal cost: €${nf(stepCostGross, 2)}/kWh/yr\nEconomic surplus: ${netSurplus != null ? `${netSurplus >= 0 ? '+' : ''}€${nf(netSurplus, 2)}/kWh/yr (Above net cost: ${netSurplus >= 0 ? 'Yes' : 'No'})` : 'Unavailable'}`;
    
    const barY = incSavingsMean != null ? (incSavingsMean >= 0 ? y(incSavingsMean) : yZero) : yZero;
    const barH = incSavingsMean != null ? Math.max(1, Math.abs(y(incSavingsMean) - yZero)) : 0;
    
    body += `<g ${chartTarget(detail)}>`;
    body += `<line x1="${cx}" x2="${cx}" y1="${y(band.min)}" y2="${y(band.max)}" stroke="#a1b8a5" stroke-width="2"/>`;
    body += `<line x1="${cx - 3}" x2="${cx + 3}" y1="${y(band.min)}" y2="${y(band.min)}" stroke="#a1b8a5" stroke-width="2"/>`;
    body += `<line x1="${cx - 3}" x2="${cx + 3}" y1="${y(band.max)}" y2="${y(band.max)}" stroke="#a1b8a5" stroke-width="2"/>`;
    body += `${incSavingsMean != null ? `<rect x="${cx - barW / 2}" y="${barY}" width="${barW}" height="${barH}" fill="${incSavingsMean >= 0 ? '#217a61' : '#b94c37'}" opacity="0.85"/>` : ''}`;
    body += `<rect class="chart-hit" x="${cx - barW / 2 - 2}" y="${top}" width="${barW + 4}" height="${plotH}"/>`;
    body += `</g>`;
  });
  
  steps.forEach(st => {
    if (Math.abs(st.capacity_to % 5) < 1e-4 || st.capacity_to === maxCap || steps.length <= 12) {
      body += `<text x="${x(st.capacity_to)}" y="${bottom + 16}" text-anchor="middle" font-size="10" fill="#62756e">${nf(st.capacity_to, 1)}</text>`;
    }
  });
  body += `<text x="${(left + right) / 2}" y="${bottom + 32}" text-anchor="middle" font-size="11" fill="#62756e">Capacity increment (kWh)</text>`;
  
  target.innerHTML = chartSvg('True C to C+1 annual incremental savings in euro per kWh per year against annualized marginal installed battery cost', body, height + 15, width);
  
  const optPrimary = opt.ensemble_optimum_kwh != null ? `${nf(opt.ensemble_optimum_kwh, 1)} kWh` : '—';
  const grossOpt = opt.gross_ensemble_optimum_kwh != null ? `${nf(opt.gross_ensemble_optimum_kwh, 1)} kWh` : '—';
  if ($('batOptimumCallout')) $('batOptimumCallout').innerHTML = `
    <strong>Economic optimum capacity (mean curve optimum): ${optPrimary} (span: ${nf(opt.weather_min_kwh, 1)} to ${nf(opt.weather_max_kwh, 1)} kWh${opt.weather_mean_kwh != null ? `, weather mean: ${nf(opt.weather_mean_kwh, 1)} kWh` : ''})</strong>
    <p>First-year annualized heuristic on the weather ensemble mean (omits multi-year future export price ramp; direct authoritative sizing decisions to the full discounted NPV heatmap below). Sensitivity span across spread, degradation & equipment cost: <strong>${nf(opt.sensitivity_min_kwh, 1)} to ${nf(opt.sensitivity_max_kwh, 1)} kWh</strong>. Unsubsidized gross optimum: <strong>${grossOpt}</strong>.</p>
    <span>${esc((opt.definition || '').replace(/\(Chart 4\)/g, '(lifetime NPV heatmap)'))}</span>
  `;
  
  if ($('batTable2')) $('batTable2').innerHTML = table(
    ['Increment step', 'Incremental savings (ensemble mean)', 'Weather min–max', 'Net cost line', 'Economic surplus?'],
    steps.map(st => {
      const b = st.incremental_savings_eur_kwh;
      const stepCostNet = st.annualized_cost_net != null ? st.annualized_cost_net : inc.annualized_marginal_cost_net;
      const incMean = b.mean;
      const surplus = incMean != null ? incMean - stepCostNet : null;
      return [
        `${nf(st.capacity_from, 1)} → ${nf(st.capacity_to, 1)} kWh (+${nf(st.delta_capacity_kwh, 1)} kWh)`,
        incMean != null ? `€${nf(incMean, 2)}/kWh/yr` : '—',
        `€${nf(b.min, 2)} – €${nf(b.max, 2)}`,
        `€${nf(stepCostNet, 2)}/kWh/yr`,
        surplus != null ? (surplus >= 0 ? `Yes (+€${nf(surplus, 2)})` : `No (-€${nf(Math.abs(surplus), 2)})`) : '—'
      ];
    })
  );
}

function renderBatteryEfcChart(data) {
  const target = $('batChart3');
  if (!target) return;
  const efcData = data.chart3_efc_breakeven;
  const list = efcData.capacity_efc;
  const width = 720, height = 280, left = 60, right = 660, top = 25, bottom = 230;
  const plotW = right - left, plotH = bottom - top;
  
  const netBeText = efcData.breakeven_charge_cycles_net != null ? `${nf(efcData.breakeven_charge_cycles_net, 1)} cycles/year` : (data.economics.annualized_marginal_cost_net === 0 ? '0 cycles (zero cost)' : 'Infinite (negative margin)');
  const grossBeText = efcData.breakeven_charge_cycles_gross != null ? `${nf(efcData.breakeven_charge_cycles_gross, 1)} cycles/year` : (data.economics.annualized_marginal_cost_gross === 0 ? '0 cycles' : 'Infinite');
  const refDesc = efcData.reference_candidate_description ? ` (${esc(efcData.reference_candidate_description)})` : '';
  
  const hasRowThresholds = list.some(c => c.breakeven_charge_cycles_net != null || c.breakeven_charge_cycles_gross != null);
  const isVarying = Boolean(efcData.marginal_cost_varies_by_step) ||
    Boolean(data.chart2_incremental_savings?.marginal_cost_varies_by_step) ||
    (hasRowThresholds && list.some(c => {
      const first = list.find(o => o.breakeven_charge_cycles_net != null);
      return first && c.breakeven_charge_cycles_net != null && Math.abs(c.breakeven_charge_cycles_net - first.breakeven_charge_cycles_net) > 0.05;
    }));

  const varyingNotice = isVarying
    ? `<div style="font-size:11px;color:#bd8334;margin-top:5px;"><strong>Tiered/varying cost active:</strong> Sizing economics vary across capacities. The reference formula above displays the reference candidate benchmark${refDesc}; per-capacity charge-input break-even requirements reflect capacity-average annualized cost, distinguished from incremental step cost in Chart 2.</div>`
    : '';

  if ($('batBreakevenFormula')) $('batBreakevenFormula').innerHTML = `
    <div><strong>Break-Even Formula (Charge-Input Convention):</strong></div>
    <div>Required Cycles = Annualized Marginal Cost / [Import Tariff × RTE − Export Price]</div>
    <div>Reference candidate${refDesc}: €${nf(data.economics.annualized_marginal_cost_net, 2)} / [€${nf(efcData.import_price_eur_kwh, 4)} × ${nf(efcData.roundtrip_efficiency, 2)} − €${nf(efcData.export_price_eur_kwh, 4)}] = €${nf(data.economics.annualized_marginal_cost_net, 2)} / €${nf(efcData.margin_per_charge_cycle_eur_kwh, 4)} = <strong>${netBeText}</strong> (net deduction) | <strong>${grossBeText}</strong> (gross)</div>
    <div style="font-size:11px;color:#62756e;margin-top:4px;">${esc((efcData.cycle_conversion_note || '').replace(/Chart 3/g, 'Equivalent-cycle heuristic').replace(/\(Chart 4\)/g, '(lifetime NPV heatmap)'))}</div>
    ${varyingNotice}
  `;
  
  const maxCap = Math.max(1, ...list.map(c => c.capacity_kwh));
  const allRowThresholds = list.flatMap(c => [c.breakeven_charge_cycles_net, c.breakeven_charge_cycles_gross])
    .filter(v => v != null && Number.isFinite(v) && v > 0);
  const rowThresholdsInReason = allRowThresholds.filter(v => v <= 1200);

  const maxCycles = Math.max(
    365,
    (efcData.breakeven_charge_cycles_net || 0) <= 1200 ? (efcData.breakeven_charge_cycles_net || 0) * 1.15 : 0,
    (efcData.breakeven_charge_cycles_gross || 0) <= 1200 ? (efcData.breakeven_charge_cycles_gross || 0) * 1.15 : 0,
    ...rowThresholdsInReason.map(v => v * 1.15),
    ...list.map(c => Math.max(c.efc_physical?.max || 0, c.efc_charge?.max || 0, c.efc_delivered?.max || 0))
  ) * 1.1;

  const hasOffScaleThreshold = (efcData.breakeven_charge_cycles_net != null && efcData.breakeven_charge_cycles_net > maxCycles) ||
    allRowThresholds.some(v => v > maxCycles);
  
  const x = c => left + ((c - 1) / Math.max(1, maxCap - 1)) * plotW;
  const y = cy => bottom - (Math.max(0, cy) / maxCycles) * plotH;
  
  let body = '';
  for (let c = 0; c <= maxCycles; c += maxCycles > 400 ? 100 : 50) {
    const yy = y(c);
    body += `<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/>`;
    body += `<text x="${left - 8}" y="${yy + 4}" text-anchor="end" font-size="10" fill="#62756e">${nf(c)}</text>`;
  }
  body += `<text x="${left - 8}" y="${top - 8}" text-anchor="end" font-size="9" fill="#62756e">Cycles / yr</text>`;
  
  if (isVarying) {
    const netPoints = list.filter(c => c.breakeven_charge_cycles_net != null && c.breakeven_charge_cycles_net <= maxCycles);
    const grossPoints = list.filter(c => c.breakeven_charge_cycles_gross != null && c.breakeven_charge_cycles_gross <= maxCycles);

    if (netPoints.length > 1) {
      body += `<polyline points="${netPoints.map(c => `${x(c.capacity_kwh)},${y(c.breakeven_charge_cycles_net)}`).join(' ')}" fill="none" stroke="#bd8334" stroke-width="2.5" stroke-dasharray="5 4"/>`;
    }
    netPoints.forEach(c => {
      body += `<circle cx="${x(c.capacity_kwh)}" cy="${y(c.breakeven_charge_cycles_net)}" r="3.5" fill="#bd8334"/>`;
    });

    if (grossPoints.length > 1) {
      body += `<polyline points="${grossPoints.map(c => `${x(c.capacity_kwh)},${y(c.breakeven_charge_cycles_gross)}`).join(' ')}" fill="none" stroke="#7b827c" stroke-width="1.8" stroke-dasharray="4 3"/>`;
    }
    grossPoints.forEach(c => {
      body += `<circle cx="${x(c.capacity_kwh)}" cy="${y(c.breakeven_charge_cycles_gross)}" r="2.5" fill="#7b827c"/>`;
    });

    body += `<text x="${right - 6}" y="${top + 10}" text-anchor="end" font-size="10" font-weight="600" fill="#bd8334">Net break-even (capacity-avg, varying)</text>`;
    body += `<text x="${right - 6}" y="${top + 22}" text-anchor="end" font-size="10" fill="#62756e">Gross break-even (capacity-avg, varying)</text>`;
    if (hasOffScaleThreshold) {
      body += `<text x="${right - 6}" y="${top + 34}" text-anchor="end" font-size="9" fill="#b94c37">▲ Thresholds exceed visible range (&gt;${Math.round(maxCycles)} cy/yr)</text>`;
    }
  } else {
    if (efcData.breakeven_charge_cycles_net != null && efcData.breakeven_charge_cycles_net <= maxCycles) {
      const yBeNet = y(efcData.breakeven_charge_cycles_net);
      body += `<line x1="${left}" x2="${right}" y1="${yBeNet}" y2="${yBeNet}" stroke="#bd8334" stroke-width="2" stroke-dasharray="5 4"/>`;
      body += `<text x="${right - 6}" y="${yBeNet - 5}" text-anchor="end" font-size="10" font-weight="600" fill="#bd8334">Net break-even: ${nf(efcData.breakeven_charge_cycles_net, 1)}/yr</text>`;
    }
    if (efcData.breakeven_charge_cycles_gross != null && efcData.breakeven_charge_cycles_gross <= maxCycles) {
      const yBeGross = y(efcData.breakeven_charge_cycles_gross);
      body += `<line x1="${left}" x2="${right}" y1="${yBeGross}" y2="${yBeGross}" stroke="#7b827c" stroke-width="1.8" stroke-dasharray="4 3"/>`;
      body += `<text x="${right - 6}" y="${yBeGross - 5}" text-anchor="end" font-size="10" fill="#62756e">Gross break-even: ${nf(efcData.breakeven_charge_cycles_gross, 1)}/yr</text>`;
    }
    if (hasOffScaleThreshold) {
      body += `<text x="${right - 6}" y="${top + 10}" text-anchor="end" font-size="9" fill="#b94c37">▲ Break-even threshold off-scale (&gt;${Math.round(maxCycles)} cy/yr)</text>`;
    }
  }
  
  body += `<polyline points="${list.filter(c => c.efc_physical.mean != null).map(c => `${x(c.capacity_kwh)},${y(c.efc_physical.mean)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="2.5"/>`;
  body += `<polyline points="${list.filter(c => c.efc_delivered.mean != null).map(c => `${x(c.capacity_kwh)},${y(c.efc_delivered.mean)}`).join(' ')}" fill="none" stroke="#6285bd" stroke-width="2.5"/>`;
  body += `<polyline points="${list.filter(c => c.efc_charge.mean != null).map(c => `${x(c.capacity_kwh)},${y(c.efc_charge.mean)}`).join(' ')}" fill="none" stroke="#8e60aa" stroke-width="2.5"/>`;
  
  const incSteps = data.chart2_incremental_savings?.steps || [];
  list.forEach(c => {
    const cx = x(c.capacity_kwh);
    const capAvgNet = c.breakeven_charge_cycles_net != null ? `${nf(c.breakeven_charge_cycles_net, 1)} cycles/yr` : netBeText;
    const capAvgGross = c.breakeven_charge_cycles_gross != null ? `${nf(c.breakeven_charge_cycles_gross, 1)} cycles/yr` : grossBeText;

    const matchingStep = incSteps.find(st => Math.abs(st.capacity_to - c.capacity_kwh) < 1e-4);
    const stepNet = matchingStep?.breakeven_charge_cycles_net != null ? `${nf(matchingStep.breakeven_charge_cycles_net, 1)} cycles/yr` : null;

    let beDetail = '';
    if (isVarying) {
      beDetail = `\nCapacity-average net break-even: ${capAvgNet}\nCapacity-average gross break-even: ${capAvgGross}`;
      if (stepNet) {
        beDetail += `\nIncremental marginal step break-even (net): ${stepNet} (${matchingStep.capacity_from}→${matchingStep.capacity_to} kWh step)`;
      }
    } else {
      beDetail = `\nNet break-even cycles: ${capAvgNet}\nGross break-even cycles: ${capAvgGross}`;
    }

    const physVal = c.efc_physical.mean != null ? `${nf(c.efc_physical.mean, 1)} cycles/yr` : 'Unavailable';
    const delivVal = c.efc_delivered.mean != null ? `${nf(c.efc_delivered.mean, 1)} cycles/yr` : 'Unavailable';
    const chargeVal = c.efc_charge.mean != null ? `${nf(c.efc_charge.mean, 1)} cycles/yr` : 'Unavailable';
    const detail = `Capacity: ${c.capacity_kwh} kWh\nPhysical cell EFC (ensemble mean): ${physVal} (${nf(c.efc_physical.min, 1)}–${nf(c.efc_physical.max, 1)})\nDelivered EFC (ensemble mean): ${delivVal}\nCharge-input EFC (ensemble mean): ${chargeVal}${beDetail}`;
    body += `<g ${chartTarget(detail)}>${c.efc_physical.mean != null ? `<circle cx="${cx}" cy="${y(c.efc_physical.mean)}" r="3" fill="#217a61"/>` : ''}${c.efc_delivered.mean != null ? `<circle cx="${cx}" cy="${y(c.efc_delivered.mean)}" r="3" fill="#6285bd"/>` : ''}${c.efc_charge.mean != null ? `<circle cx="${cx}" cy="${y(c.efc_charge.mean)}" r="3" fill="#8e60aa"/>` : ''}<rect class="chart-hit" x="${cx - 6}" y="${top}" width="12" height="${plotH}"/></g>`;
  });
  
  list.forEach(c => {
    if (c.capacity_kwh % 5 === 0 || c.capacity_kwh === maxCap || c.capacity_kwh === 1) {
      body += `<text x="${x(c.capacity_kwh)}" y="${bottom + 16}" text-anchor="middle" font-size="10" fill="#62756e">${c.capacity_kwh} kWh</text>`;
    }
  });
  
  target.innerHTML = chartSvg('Equivalent full cycles per year versus battery capacity and spread break-even requirement', body, height, width);
  if ($('batTable3')) $('batTable3').innerHTML = table(
    ['Capacity', 'Physical EFC (ensemble mean)', 'Delivered EFC (ensemble mean)', 'Charge EFC (ensemble mean)', isVarying ? 'Net break-even (capacity-avg)' : 'Net break-even cycles', isVarying ? 'Gross break-even (capacity-avg)' : 'Gross break-even'],
    list.map(c => [
      `${c.capacity_kwh} kWh`,
      c.efc_physical.mean != null ? `${nf(c.efc_physical.mean, 1)} cycles/yr` : '—',
      c.efc_delivered.mean != null ? `${nf(c.efc_delivered.mean, 1)} cycles/yr` : '—',
      c.efc_charge.mean != null ? `${nf(c.efc_charge.mean, 1)} cycles/yr` : '—',
      c.breakeven_charge_cycles_net != null ? `${nf(c.breakeven_charge_cycles_net, 1)}/yr` : (efcData.breakeven_charge_cycles_net != null ? `${nf(efcData.breakeven_charge_cycles_net, 1)}/yr` : '—'),
      c.breakeven_charge_cycles_gross != null ? `${nf(c.breakeven_charge_cycles_gross, 1)}/yr` : (efcData.breakeven_charge_cycles_gross != null ? `${nf(efcData.breakeven_charge_cycles_gross, 1)}/yr` : '—')
    ])
  );
}

function renderBatteryNpvHeatmap(data) {
  const target = $('batChart4');
  if (!target || !data.chart4_npv_heatmap) return;
  const hm = data.chart4_npv_heatmap;
  const pvs = hm.pv_grid_kwp;
  const bats = hm.battery_grid_kwh;
  const cells = hm.cells;
  const width = 720, height = 320, left = 70, right = 700, top = 30, bottom = 280;
  
  const colW = (right - left) / bats.length;
  const rowH = (bottom - top) / pvs.length;
  
  const validNpv = cells.map(c => c.npv_mean).filter(v => v != null && Number.isFinite(v));
  const minNpv = validNpv.length > 0 ? Math.min(...validNpv) : 0;
  const maxNpv = validNpv.length > 0 ? Math.max(...validNpv) : 0;
  
  let body = '';
  bats.forEach((b, j) => {
    body += `<text x="${left + (j + 0.5) * colW}" y="${top - 10}" text-anchor="middle" font-size="10" font-weight="600" fill="#62756e">${b} kWh</text>`;
  });
  
  pvs.forEach((p, i) => {
    body += `<text x="${left - 8}" y="${top + (i + 0.5) * rowH + 4}" text-anchor="end" font-size="10" font-weight="600" fill="#62756e">${nf(p, 1)} kWp</text>`;
    bats.forEach((b, j) => {
      const cell = cells.find(c => Math.abs(c.pv_kwp - p) < 1e-4 && Math.abs(c.battery_kwh - b) < 1e-4);
      if (!cell) return;
      const x = left + j * colW;
      const y = top + i * rowH;
      const hasVal = cell.npv_mean != null && Number.isFinite(cell.npv_mean);
      const cellVal = hasVal ? cell.npv_mean : null;
      let bg = '#eef3ef';
      let textFill = '#8b9690';
      let labelText = '—';
      if (hasVal) {
        const norm = maxNpv === minNpv ? 0.5 : (cellVal - minNpv) / (maxNpv - minNpv);
        const r = Math.round(240 - norm * 215);
        const g = Math.round(245 - norm * 155);
        const bl = Math.round(240 - norm * 170);
        bg = `rgb(${r},${g},${bl})`;
        textFill = norm > 0.55 ? '#ffffff' : '#183b33';
        labelText = `€${(cellVal / 1000).toFixed(1)}k`;
      }
      
      const isCurrent = Math.abs(hm.current_configuration.pv_kwp - p) < 0.2 && Math.abs(hm.current_configuration.battery_kwh - b) < 0.2;
      const npvMeanDesc = hasVal ? `€${nf(cellVal)}` : 'Unavailable';
      const detail = `PV: ${cell.pv_kwp} kWp · Battery: ${nf(cell.battery_kwh, 1)} kWh\nDiscounted NPV (mean): ${npvMeanDesc} (weather min–max: €${nf(cell.npv_min)} – €${nf(cell.npv_max)})\nEstimated capex: €${nf(cell.capex_eur)}\nReuses DCF engine with benchmark/variant storage economics${typeof routeDisplayForCandidate==='function'?`\nRouting: ${routeDisplayForCandidate(cell)}`:''}${cell.physical_feasible===false?'\nExceeds annual clipping cap — diagnostic only':''}`;
      
      body += `<g ${chartTarget(detail)}>`;
      body += `<rect x="${x + 1}" y="${y + 1}" width="${colW - 2}" height="${rowH - 2}" fill="${bg}" rx="3" stroke="${isCurrent ? '#bd8334' : 'none'}" stroke-width="${isCurrent ? 3 : 0}"/>`;
      body += `<text x="${x + colW / 2}" y="${y + rowH / 2 + 4}" text-anchor="middle" font-size="10" font-weight="600" fill="${textFill}">${labelText}</text>`;
      body += `</g>`;
    });
  });
  
  const horizon = data.settings?.horizon_years || 20;
  target.innerHTML = chartSvg(`${horizon}-year PV kWp versus battery kWh heatmap of genuine discounted NPV reusing the current financial engine`, body, height, width);
  if ($('batRoofMixNote')) $('batRoofMixNote').textContent = hm.roof_mix_note || '';
  if ($('batTable4')) $('batTable4').innerHTML = table(
    ['PV array (kWp)', 'Battery (kWh)', 'Discounted NPV (mean)', 'Weather min–max', 'Installed capex'],
    cells.map(c => [
      `${nf(c.pv_kwp, 1)} kWp`,
      `${nf(c.battery_kwh, 1)} kWh`,
      c.npv_mean != null && Number.isFinite(c.npv_mean) ? `€${nf(c.npv_mean)}` : '—',
      `€${nf(c.npv_min)} – €${nf(c.npv_max)}`,
      fmt(c.capex_eur, 'eur')
    ])
  );
}

function renderBatteryCdfChart(data) {
  const target = $('batChart5');
  if (!target) return;
  const cdf = data.chart5_needed_capacity_cdf;
  const points = cdf.cdf_points;
  const p = cdf.percentiles;
  const meanCapacity = cdf.mean_needed_kwh;
  const totalDays = cdf.day_count;
  const dayBasisLabel = totalDays != null ? (cdf.weather_count ? `${nf(totalDays)} pooled days (${cdf.weather_count} yrs)` : `${nf(totalDays)} days`) : 'day count unavailable';
  
  if ($('batCdfHeuristic')) $('batCdfHeuristic').textContent = cdf.heuristic_description;
  if ($('batCdfPercentiles')) $('batCdfPercentiles').innerHTML = `
    <div class="card"><div class="label">P10 needed</div><div class="value">${nf(p.p10, 1)} <small>kWh</small></div></div>
    <div class="card"><div class="label">P25 needed</div><div class="value">${nf(p.p25, 1)} <small>kWh</small></div></div>
    <div class="card"><div class="label">Mean needed</div><div class="value">${meanCapacity != null ? `${nf(meanCapacity, 1)} <small>kWh</small>` : '—'}</div><small>Ensemble daily mean</small></div>
    <div class="card"><div class="label">P75 needed</div><div class="value">${nf(p.p75, 1)} <small>kWh</small></div></div>
    <div class="card"><div class="label">P80 needed</div><div class="value">${nf(p.p80, 1)} <small>kWh</small></div></div>
    <div class="card"><div class="label">P90 needed</div><div class="value">${nf(p.p90, 1)} <small>kWh</small></div><small>High utilization boundary</small></div>
    <div class="card"><div class="label">Max single-day</div><div class="value">${nf(p.max, 1)} <small>kWh</small></div></div>
  `;
  
  const width = 720, height = 260, left = 60, right = 660, top = 20, bottom = 210;
  const plotW = right - left, plotH = bottom - top;
  const maxCap = Math.max(1, ...points.map(pt => pt.capacity_kwh));
  
  const x = c => left + (c / maxCap) * plotW;
  const y = frac => bottom - frac * plotH;
  
  let body = '';
  for (let f = 0; f <= 1; f += 0.25) {
    const yy = y(f);
    body += `<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/>`;
    body += `<text x="${left - 8}" y="${yy + 4}" text-anchor="end" font-size="10" fill="#62756e">${Math.round(f * 100)}%</text>`;
  }
  body += `<text x="${left - 8}" y="${top - 6}" text-anchor="end" font-size="9" fill="#62756e">% of days met</text>`;
  
  body += `<polyline points="${points.map(pt => `${x(pt.capacity_kwh)},${y(pt.fraction_days_met)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="3"/>`;
  
  points.forEach(pt => {
    const cx = x(pt.capacity_kwh);
    const cy = y(pt.fraction_days_met);
    const detail = `Capacity: ${pt.capacity_kwh} kWh\nDays fully met: ${fmt(pt.fraction_days_met, 'pct')} (${pt.days_met_count}/${dayBasisLabel})\nDays with capacity fully utilized: ${fmt(pt.fraction_days_full_utilization, 'pct')}`;
    body += `<g ${chartTarget(detail)}><circle cx="${cx}" cy="${cy}" r="3" fill="#217a61"/><rect class="chart-hit" x="${cx - 6}" y="${top}" width="12" height="${plotH}"/></g>`;
  });
  
  points.forEach(pt => {
    if (pt.capacity_kwh % 5 === 0 || pt.capacity_kwh === maxCap) {
      body += `<text x="${x(pt.capacity_kwh)}" y="${bottom + 16}" text-anchor="middle" font-size="10" fill="#62756e">${pt.capacity_kwh} kWh</text>`;
    }
  });
  
  target.innerHTML = chartSvg(`${cdf.weather_count ? `${cdf.weather_count}-year pooled` : 'Pooled'} single-cycle needed storage capacity cumulative distribution function`, body, height, width);
  if ($('batTable5')) $('batTable5').innerHTML = table(
    ['Battery capacity', 'Days fully met (%)', `Days fully met (${dayBasisLabel})`, 'Days fully utilized (%)'],
    points.map(pt => [
      `${pt.capacity_kwh} kWh`,
      fmt(pt.fraction_days_met, 'pct'),
      `${pt.days_met_count} / ${dayBasisLabel}`,
      fmt(pt.fraction_days_full_utilization, 'pct')
    ])
  );
}

function renderBatterySocHeatmap(data) {
  const target = $('batChart6');
  if (!target) return;
  const soc = data.chart6_soc_heatmap;
  const matrix = soc.hourly_soc_matrix;
  
  if ($('batSocNotice')) {
    if (soc.battery_capacity_kwh === 0 || soc.active_capacity_kwh === 0 || soc.status === 'NO_STORAGE' || soc.no_storage) {
      $('batSocNotice').hidden = false;
      $('batSocNotice').innerHTML = '<strong>No storage configured (0 kWh):</strong> Battery state of charge is 0% throughout the entire year. To inspect battery state of charge dispatch, configure a non-zero battery capacity in the sidebar or evaluate storage options.';
    } else {
      $('batSocNotice').hidden = true;
    }
  }
  
  const weatherCount = soc.weather_count ?? (getWeatherEnsembleMeta(data || current?.result).count || null);
  const emptyHrs = soc.empty_hours != null ? nf(soc.empty_hours, soc.empty_hours % 1 !== 0 ? 1 : 0) : '—';
  const fullHrs = soc.full_hours != null ? nf(soc.full_hours, soc.full_hours % 1 !== 0 ? 1 : 0) : '—';
  const midHrs = soc.mid_hours != null ? nf(soc.mid_hours, soc.mid_hours % 1 !== 0 ? 1 : 0) : '—';
  if ($('batSocFractions')) $('batSocFractions').innerHTML = `
    <div class="card"><div class="label">Empty fraction (≤1%)</div><div class="value">${fmt(soc.empty_fraction, 'pct')}</div><small>${emptyHrs} hours/year</small></div>
    <div class="card"><div class="label">Full fraction (≥99%)</div><div class="value">${fmt(soc.full_fraction, 'pct')}</div><small>${fullHrs} hours/year</small></div>
    <div class="card"><div class="label">Mid / utilizing fraction</div><div class="value">${fmt(soc.mid_fraction, 'pct')}</div><small>${midHrs} hours/year</small></div>
    <div class="card"><div class="label">Weather ensemble</div><div class="value">${weatherCount != null ? `${nf(weatherCount)} <small>years</small>` : 'Ensemble'}</div><small>Mean of independently simulated years</small></div>
  `;
  
  const width = 760, height = 230, left = 45, right = 740, top = 25, bottom = 195;
  const plotW = right - left, plotH = bottom - top;
  const colW = plotW / 365;
  const rowH = plotH / 24;
  
  let body = '';
  [0, 6, 12, 18, 23].forEach(h => {
    body += `<text x="${left - 6}" y="${top + (h + 0.5) * rowH + 3}" text-anchor="end" font-size="9" fill="#62756e">${String(h).padStart(2, '0')}:00</text>`;
  });
  
  for (let d = 0; d < 365; d++) {
    const x = left + d * colW;
    for (let h = 0; h < 24; h++) {
      const pct = matrix[d][h];
      const y = top + h * rowH;
      const norm = pct / 100;
      const r = Math.round(213 - norm * 189);
      const g = Math.round(223 - norm * 133);
      const b = Math.round(215 - norm * 145);
      body += `<rect x="${x}" y="${y}" width="${Math.max(1, colW + 0.2)}" height="${rowH}" fill="rgb(${r},${g},${b})"/>`;
    }
  }
  
  const monthDays = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  monthDays.forEach((md, m) => {
    const mx = left + md * colW;
    body += `<line x1="${mx}" x2="${mx}" y1="${top}" y2="${bottom}" stroke="#ffffff" stroke-width="0.8" opacity="0.6"/>`;
    body += `<text x="${mx + 12}" y="${bottom + 14}" font-size="9" fill="#62756e">${monthNames[m]}</text>`;
  });
  
  target.innerHTML = chartSvg('Full-year day by hour state of charge continuous dispatch heatmap for 8,760 hours (ensemble mean)', body, height, width);
  
  const backendSummaries = Array.isArray(soc.monthly_summaries) ? soc.monthly_summaries : null;
  const isZeroStorage = Boolean(soc.battery_capacity_kwh === 0 || soc.active_capacity_kwh === 0 || soc.status === 'NO_STORAGE' || soc.no_storage);
  const monthlySummaries = monthNames.map((mName, mIdx) => {
    const summary = backendSummaries?.find(s => s?.month === mIdx + 1);

    let avgSoc;
    if (summary && summary.mean_soc_pct != null) {
      avgSoc = fmt(summary.mean_soc_pct * 0.01, 'pct');
    } else {
      const startDay = monthDays[mIdx];
      const endDay = mIdx === 11 ? 365 : monthDays[mIdx + 1];
      let totalPct = 0, count = 0;
      if (matrix) {
        for (let d = startDay; d < endDay; d++) {
          if (matrix[d]) {
            for (let h = 0; h < 24; h++) {
              totalPct += matrix[d][h] || 0;
              count++;
            }
          }
        }
      }
      avgSoc = count > 0 ? fmt(totalPct / count * 0.01, 'pct') : (isZeroStorage ? fmt(0, 'pct') : '—');
    }

    const emptyFrac = (summary && summary.empty_fraction != null)
      ? fmt(summary.empty_fraction, 'pct')
      : (isZeroStorage ? fmt(0.0, 'pct') : '—');
    const fullFrac = (summary && summary.full_fraction != null)
      ? fmt(summary.full_fraction, 'pct')
      : (isZeroStorage ? fmt(0.0, 'pct') : '—');
    const midFrac = (summary && summary.mid_fraction != null)
      ? fmt(summary.mid_fraction, 'pct')
      : (isZeroStorage ? fmt(0.0, 'pct') : '—');

    return [
      mName,
      avgSoc,
      emptyFrac,
      fullFrac,
      midFrac
    ];
  });
  
  if ($('batTable6')) $('batTable6').innerHTML = table(
    ['Month', 'Average SOC (%)', 'Empty state fraction (%)', 'Full state fraction (%)', 'Utilizing state fraction (%)'],
    monthlySummaries
  );
}

async function runBatterySizingAnalysis(customOptions = null) {
  const btn = $('runBatterySizing');
  if (btn) btn.disabled = true;
  batteryInFlight = true;
  const token = ++batteryRequestId;
  if ($('batStatus')) $('batStatus').textContent = 'Simulating historical weather ensemble over storage capacity steps…';
  
  let options, requestFingerprint;
  try {
    const q10Val = $('batQuote10') ? $('batQuote10').value.trim() : '';
    const q15Val = $('batQuote15') ? $('batQuote15').value.trim() : '';
    const isMixed = Boolean(typeof settings !== 'undefined' && settings.battery_mixed_sizes);
    const stepVal = Number($('batStep') ? $('batStep').value : 1);
    if (!isMixed && (!Number.isInteger(stepVal) || (stepVal !== 1 && stepVal !== 2))) {
      if ($('batStatus')) $('batStatus').textContent = 'Step must be integer 1 or 2.';
      if (btn) btn.disabled = false;
      batteryInFlight = false;
      return;
    }
    const maxCapVal = Number($('batMaxCapacity') ? $('batMaxCapacity').value : 25);
    if (!Number.isInteger(maxCapVal) || maxCapVal < 10 || maxCapVal > 50) {
      if ($('batStatus')) $('batStatus').textContent = 'Maximum capacity must be an integer between 10 and 50.';
      if (btn) btn.disabled = false;
      batteryInFlight = false;
      return;
    }
    const lifetimeVal = Number($('batLifetime') ? $('batLifetime').value : 12);
    if (!Number.isInteger(lifetimeVal) || lifetimeVal < 1 || lifetimeVal > 30) {
      if ($('batStatus')) $('batStatus').textContent = 'Battery lifetime must be an integer between 1 and 30.';
      if (btn) btn.disabled = false;
      batteryInFlight = false;
      return;
    }
    options = {
      load_scenario: $('batScenario') ? $('batScenario').value : settings.load_scenario,
      weather_representative: $('batRepresentative') ? $('batRepresentative').value : 'median',
      max_capacity: maxCapVal,
      quote_10kwh_eur: q10Val === '' ? null : Number(q10Val),
      quote_15kwh_eur: q15Val === '' ? null : Number(q15Val),
      battery_lifetime_years: lifetimeVal,
      battery_degradation_annual: $('batDegradation') ? Number($('batDegradation').value) * 0.01 : 0.015,
      deduction_rate: $('batDeduction') ? Number($('batDeduction').value) : (settings.deduction_rate ?? 0.5),
      sensitivity_cost_delta_pct: $('batCostSens') ? Number($('batCostSens').value) * 0.01 : 0,
      sensitivity_spread_delta: $('batSpreadDelta') ? Number($('batSpreadDelta').value) : 0
    };
    if (!isMixed) {
      options.step = stepVal;
    }
    if (customOptions && typeof customOptions === 'object' && !customOptions.type && !customOptions.preventDefault) {
      Object.assign(options, customOptions);
    }

    if (shouldBlockBatterySizingForAcDraft(options)) {
      if ($('batStatus')) $('batStatus').textContent = 'Cannot run battery sizing with invalid heating draft.';
      const wrap = $('batResultsWrap');
      if (wrap && batteryData) wrap.classList.add('stale');
      if (btn) btn.disabled = false;
      batteryInFlight = false;
      return;
    }
    
    const freshSettings = deepClone(settings);
    normalizeBatterySettings(freshSettings);
    delete freshSettings.hasItemized;
    cleanSettingsForBackend(freshSettings);
    const freshQuote = deepClone(quote);
    requestFingerprint = batteryStateFingerprint(freshSettings, freshQuote, options);
    
    const result = await api('/api/battery-sizing', {
      settings: freshSettings,
      options,
      quote: freshQuote
    });
    
    if (!isBatterySizingRequestValid(token, requestFingerprint, options)) {
      if (token === batteryRequestId) {
        markBatterySizingStale();
      }
      return;
    }
    
    batteryData = result;
    batteryAcceptedFingerprint = requestFingerprint;
    const wrap = $('batResultsWrap');
    if (wrap) wrap.classList.remove('stale');
    
    renderBatterySizing(result);
    saveBatteryOptionsToStorage();
  } catch (err) {
    if (!requestFingerprint || !isBatterySizingRequestValid(token, requestFingerprint, options)) {
      if (token === batteryRequestId) {
        markBatterySizingStale();
      }
      return;
    }
    if ($('batStatus')) $('batStatus').textContent = 'Error: ' + (err.message || err);
    showError(err);
  } finally {
    if (token === batteryRequestId) {
      if (btn) btn.disabled = false;
      batteryInFlight = false;
    }
  }
}
/* END BATTERY SIZING */

/* SOLAR SIZING SECTION */
const SOLAR_STORAGE_KEY = 'pv-lab-solar-sizing-v1';
let solarData = null, solarRequestId = 0, solarInFlight = false, solarAcceptedFingerprint = null;

function formatPaybackYearsValue(val, unit = ' y') {
  const num = (val && typeof val === 'object') ? val.median : val;
  if (num !== null && num !== undefined && Number.isFinite(Number(num))) {
    return `${nf(Number(num), 1)}${unit}`;
  }
  return num === null ? 'Not reached' : '—';
}

function parsePercentInput(elId, defaultVal) {
  const el = $(elId);
  if (!el) return defaultVal;
  const raw = el.value;
  if (raw === undefined || raw === null) return defaultVal;
  const trimmed = String(raw).trim();
  if (trimmed === '') return NaN;
  return Number(trimmed);
}

function getSolarOptions() {
  const bMode = $('solarBatteryMode') ? $('solarBatteryMode').value : 'joint';
  const bMin = Number($('solarBatteryMin') ? $('solarBatteryMin').value : 0);
  const bMax = Number($('solarBatteryMax') ? $('solarBatteryMax').value : 20);
  const modKwh = Number(settings.battery_module_kwh);
  const hasValidStep = Number.isFinite(modKwh) && modKwh > 0;
  const q10Val = $('batQuote10') ? $('batQuote10').value.trim() : '';
  const q15Val = $('batQuote15') ? $('batQuote15').value.trim() : '';
  const sacVal = parsePercentInput('solarNpvSacrifice', 5);
  const covVal = parsePercentInput('solarCoverageTarget', 70);
  const finBasis = ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const retVal = parsePercentInput('solarRetainedProfit', 95);

  const opts = {
    load_scenario: $('solarScenario') ? $('solarScenario').value : (settings.load_scenario || 'electrified'),
    objective: $('solarObjective') ? $('solarObjective').value : 'npv_eur',
    financial_basis: finBasis,
    topology_mode: $('solarTopologyMode') ? $('solarTopologyMode').value : 'auto',
    battery_mode: bMode,
    storage_cost_mode: bMode === 'joint' ? 'purchased' : ($('solarBatteryCostMode') ? $('solarBatteryCostMode').value : 'purchased'),
    battery_min_kwh: bMin,
    battery_max_kwh: bMax,
    include_theoretical: $('solarIncludeTheoretical') ? $('solarIncludeTheoretical').value === 'true' : true,
    deduction_rate: $('solarDeduction') ? Number($('solarDeduction').value) : (settings.deduction_rate ?? 0.5),
    weather_representative: $('solarRepresentative') ? $('solarRepresentative').value : 'median',
    panel_maxima: optimizerPrefs?.panel_maxima || defaultRoofLimits,
    quote_10kwh_eur: q10Val === '' ? null : Number(q10Val),
    quote_15kwh_eur: q15Val === '' ? null : Number(q15Val),
    npv_sacrifice_pct: sacVal,
    coverage_target_pct: covVal,
    retained_profit_pct: retVal
  };
  const isMixed = Boolean(settings.battery_mixed_sizes);
  if (!isMixed && hasValidStep) {
    opts.battery_step_kwh = modKwh;
  }
  if (!Number.isFinite(sacVal) || sacVal < 0 || sacVal > 100) {
    throw Error('Allowed NPV sacrifice percentage must be a finite number between 0 and 100%.');
  }
  if (!Number.isFinite(covVal) || covVal < 0 || covVal > 100) {
    throw Error('Budget household demand coverage target must be a finite number between 0 and 100%.');
  }
  if (!Number.isFinite(retVal) || retVal < 0 || retVal > 100) {
    throw Error('Profit retained by lower-cost alternative must be a finite number between 0 and 100%.');
  }
  return opts;
}

function syncSolarBatteryStep() {
  const stepEl = $('solarBatteryStep');
  const helpEl = $('solarBatteryStepHelp');
  const isMixed = Boolean(settings.battery_mixed_sizes);
  const modKwh = Number(settings.battery_module_kwh);
  const secKwh = Number(settings.battery_secondary_module_kwh);
  const hasValidModule = Number.isFinite(modKwh) && modKwh > 0;
  const hasValidSec = Number.isFinite(secKwh) && secKwh > 0;
  const currentMods = Number(settings.battery_modules) || 0;
  const currentSec = Number(settings.battery_secondary_modules) || 0;
  const currentFirst = Math.max(0, currentMods - currentSec);
  const currentTotal = Number(settings.battery_kwh) || 0;
  if (stepEl) {
    if (isMixed) {
      if (hasValidModule && hasValidSec) {
        stepEl.innerHTML = `<option value="combinations" selected>Module combinations (${nf(modKwh, 1)} + ${nf(secKwh, 1)} kWh)</option>`;
        stepEl.value = 'combinations';
      } else {
        stepEl.innerHTML = '<option value="" selected>Unset / invalid (set in left menu)</option>';
        stepEl.value = '';
      }
    } else {
      if (hasValidModule) {
        stepEl.innerHTML = `<option value="${modKwh}" selected>${nf(modKwh, 2)} kWh per module</option>`;
        stepEl.value = String(modKwh);
      } else {
        stepEl.innerHTML = '<option value="" selected>Unset / invalid (set in left menu)</option>';
        stepEl.value = '';
      }
    }
    stepEl.disabled = true;
  }
  if (helpEl) {
    if (isMixed) {
      if (hasValidModule && hasValidSec) {
        helpEl.textContent = `Module combinations (${nf(modKwh, 1)} kWh + ${nf(secKwh, 1)} kWh · currently ${currentFirst} × ${nf(modKwh, 1)} + ${currentSec} × ${nf(secKwh, 1)} = ${nf(currentTotal, 1)} kWh installed).`;
      } else {
        helpEl.textContent = 'Battery module capacities are unset or 0. Set positive module capacities in the left menu under Battery & storage.';
      }
    } else {
      if (hasValidModule) {
        helpEl.textContent = `Follows left menu module capacity (${nf(modKwh, 2)} kWh/mod · currently ${currentMods} mod / ${nf(currentTotal, 2)} kWh installed).`;
      } else {
        helpEl.textContent = 'Battery module capacity is unset or 0. Set a positive module capacity in the left menu under Battery & storage.';
      }
    }
  }
}

function shouldBlockSolarSizingForAcDraft(options = null, s = settings) {
  if (!s) return false;
  const effScenario = (options && options.load_scenario) || ($('solarScenario') ? $('solarScenario').value : s.load_scenario);
  const sizingSettings = { ...s, load_scenario: effScenario };
  return Boolean(typeof shouldBlockAcRecompute === 'function' && shouldBlockAcRecompute(sizingSettings));
}

function solarStateFingerprint(customSettings = settings, customQuote = quote, customOptions = null) {
  try {
    const freshSettings = deepClone(customSettings);
    normalizeBatterySettings(freshSettings);
    delete freshSettings.hasItemized;
    cleanSettingsForBackend(freshSettings);
    const freshQuote = deepClone(customQuote);
    const opts = customOptions ? deepClone(customOptions) : getSolarOptions();
    const canonical = value => Array.isArray(value) ? value.map(canonical) : (value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value);
    return JSON.stringify(canonical({ s: freshSettings, q: freshQuote, o: opts }));
  } catch (_) {
    return 'invalid_solar_state';
  }
}

function isSolarSizingRequestValid(token, requestFingerprint, options = null) {
  if (token !== solarRequestId) return false;
  if (shouldBlockSolarSizingForAcDraft(options)) return false;
  if (requestFingerprint !== solarStateFingerprint()) return false;
  return true;
}

function isSolarSizingMatching(data = solarData) {
  if (!data) return false;
  if (solarAcceptedFingerprint === null) return false;
  if (shouldBlockSolarSizingForAcDraft()) return false;
  if (solarAcceptedFingerprint !== solarStateFingerprint()) return false;
  const wrap = $('solarResultsWrap');
  if (wrap && wrap.classList.contains('stale')) return false;
  return true;
}

const queryAll = sel => (typeof document !== 'undefined' && document.querySelectorAll ? Array.from(document.querySelectorAll(sel)) : []);

function markSolarSizingStale() {
  solarRequestId++;
  solarInFlight = false;
  solarAcceptedFingerprint = null;
  const wrap = $('solarResultsWrap');
  if (wrap && solarData) {
    wrap.classList.add('stale');
  }
  const btn = $('runSolarSizing');
  if (btn) btn.disabled = false;
  const applyBtn = $('applySolarSystem');
  if (applyBtn) applyBtn.disabled = true;
  queryAll('.solar-choice-apply').forEach(b => { b.disabled = true; });
  queryAll('button[data-apply-config-idx]').forEach(b => { b.disabled = true; });
  queryAll('button[data-alt-idx]').forEach(b => { b.disabled = true; });
  if ($('solarStatus') && solarData) {
    $('solarStatus').textContent = 'Inputs changed · Click Run sizing analysis to update';
  }
  if ($('solarApplyStatus') && solarData) {
    $('solarApplyStatus').textContent = (typeof solarAppliedMessage !== 'undefined' ? solarAppliedMessage : '') || 'Inputs changed since analysis. Re-run analysis before applying.';
  }
  if (typeof markSolarSensitivityStale === 'function') {
    markSolarSensitivityStale();
  }
}

function saveSolarOptionsToStorage() {
  try {
    const sac = parsePercentInput('solarNpvSacrifice', 5);
    const cov = parsePercentInput('solarCoverageTarget', 70);
    const ret = parsePercentInput('solarRetainedProfit', 95);
    const finBasis = ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
    let existing = {};
    try {
      const raw = localStorage.getItem(SOLAR_STORAGE_KEY);
      if (raw) existing = JSON.parse(raw) || {};
    } catch (_) {}
    const opts = {
      ...existing,
      load_scenario: $('solarScenario') ? $('solarScenario').value : (existing.load_scenario || 'electrified'),
      objective: $('solarObjective') ? $('solarObjective').value : (existing.objective || 'npv_eur'),
      financial_basis: finBasis,
      topology_mode: $('solarTopologyMode') ? $('solarTopologyMode').value : (existing.topology_mode || 'auto'),
      battery_mode: $('solarBatteryMode') ? $('solarBatteryMode').value : (existing.battery_mode || 'joint'),
      storage_cost_mode: $('solarBatteryCostMode') ? $('solarBatteryCostMode').value : (existing.storage_cost_mode || 'purchased'),
      battery_min_kwh: $('solarBatteryMin') ? Number($('solarBatteryMin').value) : (existing.battery_min_kwh ?? 0),
      battery_max_kwh: $('solarBatteryMax') ? Number($('solarBatteryMax').value) : (existing.battery_max_kwh ?? 20),
      include_theoretical: $('solarIncludeTheoretical') ? $('solarIncludeTheoretical').value === 'true' : (existing.include_theoretical ?? true),
      deduction_rate: $('solarDeduction') ? Number($('solarDeduction').value) : (existing.deduction_rate ?? 0.5),
      weather_representative: $('solarRepresentative') ? $('solarRepresentative').value : (existing.weather_representative || 'median'),
      npv_sacrifice_pct: Number.isFinite(sac) ? sac : (existing.npv_sacrifice_pct ?? 5),
      coverage_target_pct: Number.isFinite(cov) ? cov : (existing.coverage_target_pct ?? 70),
      retained_profit_pct: Number.isFinite(ret) ? ret : (existing.retained_profit_pct ?? 95)
    };
    localStorage.setItem(SOLAR_STORAGE_KEY, JSON.stringify(opts));
  } catch (_) {}
}

function loadSolarOptionsFromStorage() {
  try {
    const raw = localStorage.getItem(SOLAR_STORAGE_KEY);
    if (!raw) {
      if ($('solarFinancialBasis') && !$('solarFinancialBasis').value) $('solarFinancialBasis').value = 'project';
      if ($('solarRetainedProfit') && !$('solarRetainedProfit').value) $('solarRetainedProfit').value = '95';
      syncSolarBatteryStep();
      return;
    }
    const opts = JSON.parse(raw);
    if (!opts || typeof opts !== 'object') {
      if ($('solarFinancialBasis') && !$('solarFinancialBasis').value) $('solarFinancialBasis').value = 'project';
      if ($('solarRetainedProfit') && !$('solarRetainedProfit').value) $('solarRetainedProfit').value = '95';
      syncSolarBatteryStep();
      return;
    }
    if ($('solarFinancialBasis')) $('solarFinancialBasis').value = opts.financial_basis || 'project';
    if (opts.load_scenario && $('solarScenario')) $('solarScenario').value = opts.load_scenario;
    if (opts.objective && $('solarObjective')) $('solarObjective').value = opts.objective;
    if ($('solarTopologyMode')) $('solarTopologyMode').value = opts.topology_mode || 'auto';
    if (opts.battery_mode && $('solarBatteryMode')) $('solarBatteryMode').value = opts.battery_mode;
    if (opts.storage_cost_mode && $('solarBatteryCostMode')) $('solarBatteryCostMode').value = opts.storage_cost_mode;
    if (opts.battery_min_kwh != null && $('solarBatteryMin')) $('solarBatteryMin').value = String(opts.battery_min_kwh);
    if (opts.battery_max_kwh != null && $('solarBatteryMax')) $('solarBatteryMax').value = String(opts.battery_max_kwh);
    if (opts.include_theoretical != null && $('solarIncludeTheoretical')) $('solarIncludeTheoretical').value = String(opts.include_theoretical);
    if (opts.deduction_rate != null && $('solarDeduction')) $('solarDeduction').value = String(opts.deduction_rate);
    if (opts.weather_representative && $('solarRepresentative')) $('solarRepresentative').value = opts.weather_representative;
    if (opts.npv_sacrifice_pct != null && $('solarNpvSacrifice')) $('solarNpvSacrifice').value = String(opts.npv_sacrifice_pct);
    if (opts.coverage_target_pct != null && $('solarCoverageTarget')) $('solarCoverageTarget').value = String(opts.coverage_target_pct);
    if ($('solarRetainedProfit')) $('solarRetainedProfit').value = String(opts.retained_profit_pct ?? 95);
    syncSolarBatteryStep();
  } catch (_) {
    syncSolarBatteryStep();
  }
}

const solarControlIds = [
  'solarTopologyMode', 'solarFinancialBasis', 'solarScenario', 'solarObjective', 'solarBatteryMode', 'solarBatteryCostMode',
  'solarBatteryMin', 'solarBatteryMax', 'solarBatteryStep', 'solarIncludeTheoretical',
  'solarDeduction', 'solarRepresentative', 'solarNpvSacrifice', 'solarCoverageTarget', 'solarRetainedProfit'
];

let solarControlsInitialized = false;
function initSolarSizingControls() {
  loadSolarOptionsFromStorage();
  updateSolarBatteryModeUi();
  syncSolarBatteryStep();
  syncSetting('clipping_dc_max');
  if (solarControlsInitialized) return;
  solarControlsInitialized = true;
  solarControlIds.forEach(id => {
    const el = $(id);
    if (!el) return;
    const onMod = () => {
      if (id === 'solarBatteryMode') updateSolarBatteryModeUi();
      saveSolarOptionsToStorage();
      markSolarSizingStale();
      if (typeof markSolarSensitivityStale === 'function') markSolarSensitivityStale();
      if (id === 'solarScenario') {
        if (typeof editSetting === 'function' && typeof settings !== 'undefined' && el.value !== settings.load_scenario) {
          editSetting('load_scenario', el.value, el);
        }
      }
    };
    el.addEventListener('input', onMod);
    el.addEventListener('change', onMod);
  });
  queryAll('.preset-chip[data-target]').forEach(chip => {
    chip.onclick = () => {
      const targetId = chip.getAttribute('data-target');
      const val = chip.getAttribute('data-value');
      const input = $(targetId);
      if (input) {
        input.value = val;
        input.dispatchEvent(new Event('input'));
        saveSolarOptionsToStorage();
        markSolarSizingStale();
        if (typeof markSolarSensitivityStale === 'function') markSolarSensitivityStale();
      }
    };
  });
  ['solarNpvSacrifice', 'solarCoverageTarget', 'solarRetainedProfit'].forEach(id => {
    const el = $(id);
    if (!el) return;
    const validatePct = () => {
      const v = parsePercentInput(id, NaN);
      if (Number.isFinite(v) && v >= 0 && v <= 100) {
        el.removeAttribute?.('aria-invalid');
        el.classList.remove('is-invalid');
      } else {
        el.setAttribute?.('aria-invalid', 'true');
        el.classList.add('is-invalid');
      }
    };
    el.addEventListener('input', validatePct);
    el.addEventListener('change', validatePct);
    validatePct();
  });
  const clipInput = $('solarClippingDcMax');
  if (clipInput) {
    const onClipMod = (e) => {
      const raw = clipInput.value.trim();
      const num = raw === '' ? NaN : Number(raw);
      if (!Number.isFinite(num)) {
        clipInput.setAttribute?.('aria-invalid', 'true');
        clipInput.classList.add('is-invalid');
        return;
      }
      const clamped = Math.max(0, Math.min(100, num));
      if (num < 0 || num > 100) {
        clipInput.setAttribute?.('aria-invalid', 'true');
        clipInput.classList.add('is-invalid');
        if (e && e.type === 'change') {
          clipInput.value = String(clamped);
          clipInput.removeAttribute?.('aria-invalid');
          clipInput.classList.remove('is-invalid');
        } else {
          return;
        }
      } else {
        clipInput.removeAttribute?.('aria-invalid');
        clipInput.classList.remove('is-invalid');
      }
      const fraction = Number((clamped / 100).toFixed(6));
      const currFraction = settings.clipping_dc_max == null ? null : Number(settings.clipping_dc_max);
      if (currFraction == null || Math.abs(currFraction - fraction) > 1e-6) {
        editSetting('clipping_dc_max', fraction, clipInput);
      }
      markSolarSizingStale();
      if (typeof markSolarSensitivityStale === 'function') markSolarSensitivityStale();
    };
    clipInput.addEventListener('input', onClipMod);
    clipInput.addEventListener('change', onClipMod);
  }
  ['sensExportDelta', 'sensCostDelta', 'sensConsDelta', 'sensAprDelta'].forEach(id => {
    const el = $(id);
    if (!el) return;
    const onSensMod = () => {
      if (typeof markSolarSensitivityStale === 'function') markSolarSensitivityStale();
    };
    el.addEventListener('input', onSensMod);
    el.addEventListener('change', onSensMod);
  });
  if ($('runSolarSizing')) {
    $('runSolarSizing').onclick = runSolarSizingAnalysis;
  }
  if ($('applySolarSystem')) {
    $('applySolarSystem').onclick = applyRecommendedSolarSystem;
  }
  if ($('runSolarSensitivity')) {
    $('runSolarSensitivity').onclick = runSolarSensitivityAnalysis;
  }

  const expandBtn = $('solarHeatmapExpandBtn');
  const dialog = $('solarHeatmapDialog');
  const closeBtn = $('solarHeatmapDialogClose');
  if (expandBtn && dialog) {
    let lastActiveElement = null;
    const openDialog = () => {
      lastActiveElement = (typeof document !== 'undefined' && document.activeElement && document.activeElement !== document.body && (!dialog.contains || !dialog.contains(document.activeElement)))
        ? document.activeElement
        : expandBtn;
      expandBtn.setAttribute('aria-expanded', 'true');
      if (typeof dialog.showModal === 'function') {
        try {
          dialog.showModal();
        } catch (_) {
          dialog.setAttribute('open', '');
        }
      } else {
        dialog.setAttribute('open', '');
      }
      if (typeof solarTradeoffData !== 'undefined' && solarTradeoffData) {
        renderSolarTradeoffHeatmap(solarTradeoffData);
      } else if (typeof solarData !== 'undefined' && solarData) {
        renderSolarTradeoffHeatmap(solarData);
      }
      if (closeBtn && typeof closeBtn.focus === 'function') closeBtn.focus();
    };
    const closeDialog = () => {
      expandBtn.setAttribute('aria-expanded', 'false');
      if (typeof dialog.close === 'function') {
        try {
          dialog.close();
        } catch (_) {
          dialog.removeAttribute('open');
        }
      } else {
        dialog.removeAttribute('open');
      }
      if (typeof hideChartTooltip === 'function') {
        hideChartTooltip();
      }
      const targetFocus = lastActiveElement || expandBtn;
      lastActiveElement = null;
      if (targetFocus && typeof targetFocus.focus === 'function') {
        try { targetFocus.focus(); } catch (_) {}
      }
    };
    expandBtn.addEventListener('click', openDialog);
    if (closeBtn) {
      closeBtn.addEventListener('click', closeDialog);
    }
    dialog.addEventListener('cancel', () => {
      expandBtn.setAttribute('aria-expanded', 'false');
      if (typeof hideChartTooltip === 'function') {
        hideChartTooltip();
      }
      const targetFocus = lastActiveElement || expandBtn;
      lastActiveElement = null;
      if (targetFocus && typeof targetFocus.focus === 'function') {
        setTimeout(() => { try { targetFocus.focus(); } catch (_) {} }, 0);
      }
    });
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog) {
        const rect = typeof dialog.getBoundingClientRect === 'function' ? dialog.getBoundingClientRect() : null;
        const isOutside = Boolean(
          rect &&
          typeof e.clientX === 'number' &&
          typeof e.clientY === 'number' &&
          (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom)
        );
        if (isOutside) closeDialog();
      }
    });
  }
}
initSolarSizingControls();

function updateSolarBatteryModeUi() {
  const mode = $('solarBatteryMode') ? $('solarBatteryMode').value : 'joint';
  const isFixed = mode === 'fixed';
  ['solarBatteryMin', 'solarBatteryMax', 'solarBatteryStep'].forEach(id => {
    const el = $(id);
    if (el) el.disabled = isFixed || id === 'solarBatteryStep';
  });
  const costMode = $('solarBatteryCostMode');
  if (costMode) {
    if (!isFixed) {
      costMode.value = 'purchased';
    }
    costMode.disabled = !isFixed;
  }
  syncSolarBatteryStep();
}

async function runSolarSizingAnalysis() {
  const btn = $('runSolarSizing');
  if (btn) btn.disabled = true;
  const applyBtn = $('applySolarSystem');
  if (applyBtn) applyBtn.disabled = true;
  queryAll('.solar-choice-apply').forEach(b => { b.disabled = true; });
  queryAll('button[data-apply-config-idx]').forEach(b => { b.disabled = true; });
  queryAll('button[data-alt-idx]').forEach(b => { b.disabled = true; });
  solarInFlight = true;
  const token = ++solarRequestId;
  if ($('solarStatus')) $('solarStatus').textContent = 'Evaluating discrete solar layouts & storage configurations across historical weather ensemble…';
  if ($('solarApplyStatus')) $('solarApplyStatus').textContent = '';

  let options, requestFingerprint;
  try {
    options = getSolarOptions();
    if (shouldBlockSolarSizingForAcDraft(options)) {
      if ($('solarStatus')) $('solarStatus').textContent = 'Cannot run solar sizing with invalid heating draft.';
      const wrap = $('solarResultsWrap');
      if (wrap && solarData) wrap.classList.add('stale');
      if (btn) btn.disabled = false;
      solarInFlight = false;
      return;
    }
    if (!Number.isFinite(options.npv_sacrifice_pct) || options.npv_sacrifice_pct < 0 || options.npv_sacrifice_pct > 100) {
      throw Error('Allowed NPV sacrifice percentage must be a finite number between 0 and 100%.');
    }
    if (!Number.isFinite(options.coverage_target_pct) || options.coverage_target_pct < 0 || options.coverage_target_pct > 100) {
      throw Error('Budget household demand coverage target must be a finite number between 0 and 100%.');
    }
    if (!Number.isFinite(options.retained_profit_pct) || options.retained_profit_pct < 0 || options.retained_profit_pct > 100) {
      throw Error('Profit retained by lower-cost alternative must be a finite number between 0 and 100%.');
    }
    if (options.battery_mode === 'joint') {
      if (!Number.isFinite(options.battery_min_kwh) || options.battery_min_kwh < 0 || options.battery_min_kwh > 50) {
        throw Error('Storage minimum capacity must be between 0 and 50 kWh.');
      }
      if (!Number.isFinite(options.battery_max_kwh) || options.battery_max_kwh < 0 || options.battery_max_kwh > 50) {
        throw Error('Storage maximum capacity must be between 0 and 50 kWh.');
      }
      if (options.battery_min_kwh > options.battery_max_kwh) {
        throw Error('Storage minimum capacity cannot exceed maximum capacity.');
      }
      if (options.battery_max_kwh > 0) {
        const isMixed = Boolean(settings.battery_mixed_sizes);
        const modKwh = Number(settings.battery_module_kwh);
        if (isMixed) {
          const secKwh = Number(settings.battery_secondary_module_kwh);
          if (!Number.isFinite(modKwh) || modKwh <= 0 || !Number.isFinite(secKwh) || secKwh <= 0) {
            throw Error('Joint storage optimization requires positive battery module capacities for both module sizes. Set module capacities (kWh) in the left menu under Battery & storage.');
          }
          const attainable = new Set();
          const maxC1 = Math.floor((options.battery_max_kwh + 1e-9) / modKwh);
          for (let c1 = 0; c1 <= maxC1; c1++) {
            const rem = options.battery_max_kwh + 1e-9 - c1 * modKwh;
            if (rem < -1e-9) continue;
            const maxC2 = Math.floor(rem / secKwh);
            for (let c2 = 0; c2 <= maxC2; c2++) {
              if (c1 === 0 && c2 === 0) continue;
              const cap = c1 * modKwh + c2 * secKwh;
              if (cap >= options.battery_min_kwh - 1e-9 && cap <= options.battery_max_kwh + 1e-9) {
                attainable.add(Math.round(cap * 1e6) / 1e6);
              }
            }
          }
          if (options.battery_min_kwh > 0 && attainable.size === 0) {
            throw Error(`No battery module combinations exist between ${options.battery_min_kwh} kWh and ${options.battery_max_kwh} kWh with module sizes ${modKwh} kWh and ${secKwh} kWh.`);
          }
          if (attainable.size > 50) {
            throw Error('Battery module combinations generate too many candidate capacities (maximum 50 steps)');
          }
        } else {
          if (!Number.isFinite(modKwh) || modKwh <= 0) {
            throw Error('Joint storage optimization requires a positive battery module capacity. Set battery module capacity (kWh) in the left menu under Battery & storage.');
          }
          const mStart = options.battery_min_kwh <= 0 ? 1 : Math.ceil((options.battery_min_kwh - 1e-9) / modKwh);
          const mEnd = Math.floor((options.battery_max_kwh + 1e-9) / modKwh);
          if (options.battery_min_kwh > 0 && mStart > mEnd) {
            throw Error(`No whole battery module capacities exist between ${options.battery_min_kwh} kWh and ${options.battery_max_kwh} kWh with module size ${modKwh} kWh.`);
          }
          const posSteps = mEnd >= mStart ? (mEnd - mStart + 1) : 0;
          if (posSteps > 50) {
            throw Error('battery_module_kwh generates too many candidate capacities (maximum 50 steps)');
          }
        }
      }

      const panelWatts = Number(settings.panel_watts) || 440;
      const invKw = Number(settings.inverter_kw) || 6;
      const maxInvPanels = Math.max(0, Math.floor((invKw * 1000 + 1e-6) / panelWatts));
      const incTheor = options.include_theoretical;
      const roofMaxima = options.panel_maxima || {};
      let effPanelMax = 0;
      for (const r of ((typeof activeResultProfile!=='undefined'&&activeResultProfile)?activeResultProfile.roofs.map(r=>r.id):['south', 'west', 'east', 'dormer', 'flat', 'secondary_south'])) {
        let lim = Number(roofMaxima[r]) || 0;
        if (!incTheor&&!(typeof activeResultProfile!=='undefined'&&activeResultProfile)) {
          if (['dormer', 'flat', 'secondary_south'].includes(r)) lim = 0;
          else if (r === 'south') lim = Math.min(lim, 15);
        }
        lim = Math.min(lim, maxInvPanels);
        effPanelMax += lim;
      }
      effPanelMax = Math.min(effPanelMax, maxInvPanels);
      if (!incTheor) {
        effPanelMax = Math.min(effPanelMax, Math.floor(20000 / panelWatts));
      }
      if (effPanelMax === 0 && options.battery_min_kwh > 0) {
        throw Error('Positive storage capacity cannot be installed without solar PV panels. Increase roof limits or inverter capacity, or set storage minimum to 0.');
      }
    }

    if (!batteryInFlight && !isBatterySizingMatching() && typeof runBatterySizingAnalysis === 'function') {
      try {
        const p = runBatterySizingAnalysis();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } catch (_) {}
    }

    const freshSettings = deepClone(settings);
    normalizeBatterySettings(freshSettings);
    delete freshSettings.hasItemized;
    cleanSettingsForBackend(freshSettings);
    const freshQuote = deepClone(quote);
    requestFingerprint = solarStateFingerprint(freshSettings, freshQuote, options);

    const result = await api('/api/solar-sizing', {
      settings: freshSettings,
      options,
      quote: freshQuote
    });

    if (!isSolarSizingRequestValid(token, requestFingerprint, options)) {
      if (token === solarRequestId) {
        markSolarSizingStale();
      }
      return;
    }

    if (!result.recommended_candidate) {
      solarData = result;
      solarAcceptedFingerprint = null;
      solarInFlight = false;
      if (applyBtn) applyBtn.disabled = true;
      queryAll('.solar-choice-apply').forEach(b => { b.disabled = true; });
      queryAll('button[data-apply-config-idx]').forEach(b => { b.disabled = true; });
      queryAll('button[data-alt-idx]').forEach(b => { b.disabled = true; });
      renderSolarSizing(result);
      if (typeof renderDesignUnifiedComparisonTable === 'function') renderDesignUnifiedComparisonTable(result);
      if (typeof renderConfigVsRecommended === 'function') renderConfigVsRecommended();
      return;
    }

    solarData = result;
    solarAcceptedFingerprint = requestFingerprint;
    solarInFlight = false;

    const wrap = $('solarResultsWrap');
    if (wrap) wrap.classList.remove('stale');
    if (applyBtn) applyBtn.disabled = false;

    renderSolarSizing(result);
    if (typeof renderDesignUnifiedComparisonTable === 'function') renderDesignUnifiedComparisonTable(result);
    if (typeof renderConfigVsRecommended === 'function') renderConfigVsRecommended();
    saveSolarOptionsToStorage();
  } catch (err) {
    if (requestFingerprint && !isSolarSizingRequestValid(token, requestFingerprint, options)) {
      if (token === solarRequestId) {
        markSolarSizingStale();
      }
      return;
    }
    solarAcceptedFingerprint = null;
    solarInFlight = false;
    if ($('applySolarSystem')) $('applySolarSystem').disabled = true;
    queryAll('.solar-choice-apply').forEach(b => { b.disabled = true; });
    queryAll('button[data-apply-config-idx]').forEach(b => { b.disabled = true; });
    queryAll('button[data-alt-idx]').forEach(b => { b.disabled = true; });
    if ($('solarStatus')) $('solarStatus').textContent = 'Error: ' + (err.message || err);
    showError(err);
  } finally {
    if (token === solarRequestId) {
      if (btn) btn.disabled = false;
      solarInFlight = false;
    }
  }
}

function getMainRecommendation(data) {
  if (!data) return null;
  const choices = data.recommendation_choices;
  if (choices) {
    if (choices.balanced) {
      return choices.balanced;
    }
    const mainRole = choices.main_recommendation;
    if (mainRole && choices[mainRole]) {
      return choices[mainRole];
    }
    if (choices.financial) {
      return choices.financial;
    }
    return null;
  }
  return data.recommended_candidate || null;
}

function applySolarCandidate(rec, label = 'recommended system') {
  if (!rec) return;
  const wrap = $('solarResultsWrap');
  if ((wrap && wrap.classList.contains('stale')) || solarInFlight) {
    if ($('solarApplyStatus')) $('solarApplyStatus').textContent = 'Cannot apply: analysis is stale or running. Re-run analysis first.';
    return;
  }
  if (!isSolarSizingMatching() || shouldBlockSolarSizingForAcDraft() || solarAcceptedFingerprint !== solarStateFingerprint()) {
    if ($('solarApplyStatus')) $('solarApplyStatus').textContent = 'Cannot apply: inputs changed since sizing analysis. Re-run analysis first.';
    return;
  }

  let selectedCounts=null;
  if(typeof activeResultProfile!=='undefined'&&activeResultProfile){
   try{if(solarData?._presentation?.input_revision!==householdClient.revision||solarData?._presentation?.profile_id!==householdClient.profileId)throw Error('Sizing revision changed. Re-run analysis.');selectedCounts=profileCounts(rec);}catch(error){if($('solarApplyStatus'))$('solarApplyStatus').textContent=error.message;return;}
  }
  const recSettings = rec.settings || {};
  for (const name of ['pv_dispatch_model', 'pv_battery_coupling', 'routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month', 'inverter_efficiency', 'ilr_ac_max', 'ilr_dc_max', 'clipping_ac_max', 'clipping_dc_max', 'battery_inverter_kw', 'battery_c_rate_max', 'battery_soc_min', 'battery_soc_max', 'battery_charge_efficiency', 'battery_discharge_efficiency']) {
    if (Object.prototype.hasOwnProperty.call(recSettings, name)) settings[name] = recSettings[name];
  }
  const candBatteryKwh = Number(rec.battery_kwh ?? recSettings.battery_kwh ?? 0);
  const candModules = Number(rec.battery_modules ?? recSettings.battery_modules ?? 0);
  const candSecondaryModules = Number(rec.battery_secondary_modules ?? recSettings.battery_secondary_modules ?? 0);
  const isNoInstall = Boolean(rec.is_no_install || (rec.panel_count === 0 && candBatteryKwh === 0));
  const isZeroStorage = Boolean(
    isNoInstall ||
    candBatteryKwh === 0 ||
    (candModules === 0 && candSecondaryModules === 0)
  );

  const candStorageCostMode = recSettings.storage_cost_mode || rec.storage_cost_mode;
  const isExistingStorage = Boolean(candStorageCostMode === 'existing');

  // Preserve original genuine overrides, extras, and battery €/kWh in quote as recoverable references before applying candidate settings
  if (settings.battery_module_cost_override != null) {
    quote.battery_module_cost_override = settings.battery_module_cost_override;
  }
  if (settings.battery_secondary_module_cost_override != null) {
    quote.battery_secondary_module_cost_override = settings.battery_secondary_module_cost_override;
  }
  if (settings.battery_module_extra_cost != null) {
    quote.battery_module_extra_cost = settings.battery_module_extra_cost;
  }
  if (settings.battery_secondary_module_extra_cost != null) {
    quote.battery_secondary_module_extra_cost = settings.battery_secondary_module_extra_cost;
  }
  if (settings.battery_cost_kwh != null && Number.isFinite(Number(settings.battery_cost_kwh)) && Number(settings.battery_cost_kwh) >= 0) {
    quote.battery_cost_kwh = Number(settings.battery_cost_kwh);
  }

  const userStartingBatteryCostKwh = (settings.battery_cost_kwh != null && Number.isFinite(Number(settings.battery_cost_kwh)) && Number(settings.battery_cost_kwh) >= 0)
    ? Number(settings.battery_cost_kwh)
    : (quote.battery_cost_kwh != null && Number.isFinite(Number(quote.battery_cost_kwh)) && Number(quote.battery_cost_kwh) >= 0 ? Number(quote.battery_cost_kwh) : null);
  const userStartingModOverride = settings.battery_module_cost_override ?? quote.battery_module_cost_override ?? null;
  const userStartingSecModOverride = settings.battery_secondary_module_cost_override ?? quote.battery_secondary_module_cost_override ?? null;
  const userStartingModExtra = settings.battery_module_extra_cost ?? quote.battery_module_extra_cost ?? null;
  const userStartingSecModExtra = settings.battery_secondary_module_extra_cost ?? quote.battery_secondary_module_extra_cost ?? null;

  if(typeof activeResultProfile!=='undefined'&&activeResultProfile)settings.roof_panels=isNoInstall?Object.fromEntries(activeResultProfile.roofs.map(r=>[r.id,0])):selectedCounts;
  if (isNoInstall) {
    for (const r of ((typeof activeResultProfile!=='undefined'&&activeResultProfile)?[]:['south', 'west', 'east', 'dormer', 'flat', 'secondary_south'])) {
      settings[r + '_panels'] = 0;
    }
    if(!(typeof activeResultProfile!=='undefined'&&activeResultProfile))settings.dormer_south_proxy = false;
    settings.battery_modules = 0;
    settings.battery_secondary_modules = 0;
    settings.battery_kwh = 0.0;
  } else {
    for (const r of ((typeof activeResultProfile!=='undefined'&&activeResultProfile)?[]:['south', 'west', 'east', 'dormer', 'flat', 'secondary_south'])) {
      settings[r + '_panels'] = rec.roof_counts ? (rec.roof_counts[r] ?? recSettings[r + '_panels'] ?? 0) : (recSettings[r + '_panels'] ?? 0);
    }
    if(!(typeof activeResultProfile!=='undefined'&&activeResultProfile))settings.dormer_south_proxy = false;

    const isMixed = Boolean(rec.battery_mixed_sizes ?? recSettings.battery_mixed_sizes ?? false);
    settings.battery_mixed_sizes = isMixed;
    settings.battery_modules = Number(rec.battery_modules ?? recSettings.battery_modules ?? 0);
    settings.battery_module_kwh = Number(rec.battery_module_kwh ?? recSettings.battery_module_kwh ?? settings.battery_module_kwh ?? 0);
    settings.battery_secondary_modules = isMixed ? Number(rec.battery_secondary_modules ?? recSettings.battery_secondary_modules ?? 0) : 0;
    settings.battery_secondary_module_kwh = Number(rec.battery_secondary_module_kwh ?? recSettings.battery_secondary_module_kwh ?? settings.battery_secondary_module_kwh ?? 0);
    settings.battery_kwh = Number(rec.battery_kwh ?? recSettings.battery_kwh ?? 0);

    if (isZeroStorage) {
      settings.battery_kwh = 0.0;
      settings.battery_modules = 0;
      settings.battery_secondary_modules = 0;
    }
  }

  if (candStorageCostMode) {
    settings.storage_cost_mode = candStorageCostMode;
    if ($('solarBatteryCostMode')) $('solarBatteryCostMode').value = candStorageCostMode;
  }

  if (isZeroStorage || isExistingStorage) {
    // For no-storage and sunk existing storage preserve the actual user setting including 0,
    // module overrides/extras for future purchased searches; their current installed cost remains zero through capacity/cost mode.
    if (userStartingBatteryCostKwh != null) {
      settings.battery_cost_kwh = userStartingBatteryCostKwh;
    }
    settings.battery_module_cost_override = userStartingModOverride;
    settings.battery_secondary_module_cost_override = userStartingSecModOverride;
    if (userStartingModExtra != null) settings.battery_module_extra_cost = userStartingModExtra;
    if (userStartingSecModExtra != null) settings.battery_secondary_module_extra_cost = userStartingSecModExtra;
  } else {
    // For positive purchased candidate accept its finite nonnegative effective cost including 0; no fallback to old positive benchmark
    settings.battery_module_cost_override = recSettings.battery_module_cost_override ?? null;
    settings.battery_secondary_module_cost_override = recSettings.battery_secondary_module_cost_override ?? null;
    if (recSettings.battery_module_extra_cost != null) settings.battery_module_extra_cost = recSettings.battery_module_extra_cost;
    if (recSettings.battery_secondary_module_extra_cost != null) settings.battery_secondary_module_extra_cost = recSettings.battery_secondary_module_extra_cost;

    if (recSettings.battery_cost_kwh != null && Number.isFinite(Number(recSettings.battery_cost_kwh)) && Number(recSettings.battery_cost_kwh) >= 0) {
      settings.battery_cost_kwh = Number(recSettings.battery_cost_kwh);
    } else if (userStartingBatteryCostKwh != null) {
      settings.battery_cost_kwh = userStartingBatteryCostKwh;
    }
  }

  if (recSettings.inverter_kw != null) settings.inverter_kw = recSettings.inverter_kw;
  if (recSettings.charge_kw != null) settings.charge_kw = recSettings.charge_kw;
  if (recSettings.discharge_kw != null) settings.discharge_kw = recSettings.discharge_kw;
  if (recSettings.roundtrip_efficiency != null) settings.roundtrip_efficiency = recSettings.roundtrip_efficiency;

  if (recSettings.load_scenario) settings.load_scenario = recSettings.load_scenario;
  if (recSettings.deduction_rate != null) settings.deduction_rate = recSettings.deduction_rate;

  settings.quote_total = null;
  quote.price_basis = 'modeled';
  delete quote.battery_nameplate_kwh;
  delete quote.battery_dod;
  delete quote.continuous_charge_kw;
  delete quote.continuous_discharge_kw;
  delete quote.quote_total;

  normalizeBatterySettings(settings, 'battery_modules');

  for (const r of ((typeof activeResultProfile!=='undefined'&&activeResultProfile)?[]:['south', 'west', 'east', 'dormer', 'flat', 'secondary_south'])) {
    if (typeof syncSetting === 'function') syncSetting(r + '_panels');
    if (typeof updateControlModifiedState === 'function') updateControlModifiedState(r + '_panels');
  }
  const batterySettingsList = (typeof BATTERY_SETTINGS !== 'undefined')
    ? BATTERY_SETTINGS
    : ['battery_kwh', 'battery_modules', 'battery_module_kwh', 'battery_mixed_sizes', 'battery_secondary_module_kwh', 'battery_secondary_modules'];
  for (const linked of batterySettingsList) {
    if (typeof syncSetting === 'function') syncSetting(linked);
    if (typeof updateControlModifiedState === 'function') updateControlModifiedState(linked);
  }
  if (typeof updateControlModifiedState === 'function') updateControlModifiedState('battery_group');
  for (const tech of ['pv_dispatch_model', 'pv_battery_coupling', 'routing_priority', 'routing_switch_hour', 'scheduled_start_month', 'scheduled_end_month', 'inverter_efficiency', 'ilr_ac_max', 'ilr_dc_max', 'clipping_ac_max', 'clipping_dc_max', 'battery_inverter_kw', 'battery_c_rate_max', 'battery_soc_min', 'battery_soc_max', 'battery_charge_efficiency', 'battery_discharge_efficiency', 'inverter_kw', 'pv_max_kwp', 'charge_kw', 'discharge_kw', 'roundtrip_efficiency', 'load_scenario', 'deduction_rate', 'dormer_south_proxy', 'battery_cost_kwh', 'storage_cost_mode', 'battery_module_cost_override', 'battery_secondary_module_cost_override', 'battery_module_extra_cost', 'battery_secondary_module_extra_cost']) {
    if (typeof syncSetting === 'function') syncSetting(tech);
    if (typeof updateControlModifiedState === 'function') updateControlModifiedState(tech);
  }
  if (typeof syncOptimizerEquipmentInputs === 'function') syncOptimizerEquipmentInputs();
  if (typeof syncSolarBatteryStep === 'function') syncSolarBatteryStep();
  if (typeof updateCurrentSystemSummary === 'function') updateCurrentSystemSummary();
  if (typeof refreshConditionalControls === 'function') refreshConditionalControls();

  if (typeof syncQuoteFields === 'function') syncQuoteFields();
  if (typeof renderControls === 'function') renderControls();
  if (typeof renderProfileRoofControls === 'function') renderProfileRoofControls();
  if (typeof renderRoofLimitsComparison === 'function') renderRoofLimitsComparison();

  if (isNoInstall) {
    solarAppliedMessage = `Applied ${label}: 0 panels and 0 kWh storage (No-install baseline, €0 capex). Battery module capacity preserved as reference. Recalculating scenario…`;
  } else {
    solarAppliedMessage = `Applied ${label}: ${rec.panel_count} panels (${nf(rec.kwp, 2)} kWp) and ${nf(rec.battery_kwh, 1)} kWh storage with modeled pricing (${fmt(rec.capex_eur, 'eur')} capex). Quote line items preserved as reference with price basis set to Modeled Sizing. Recalculating scenario…`;
  }
  if ($('solarApplyStatus')) {
    $('solarApplyStatus').textContent = solarAppliedMessage;
  }
  schedule({ replace: true });
}

function applyRecommendedSolarSystem() {
  const rec = getMainRecommendation(solarData);
  if (!rec) return;
  applySolarCandidate(rec, 'main recommendation');
}

function renderSolarRecommendationChoices(data) {
  const target = $('solarChoiceCards');
  if (!target) return;
  const choices = data.recommendation_choices;
  if (!choices) {
    target.innerHTML = '<p class="muted">Recommendation choices are not available for this run.</p>';
    return;
  }

  const { financial, balanced, budget, smaller, preferences, role_status } = choices;
  const sac = preferences?.allowed_sacrifice_pct ?? 5;
  const covTarget = preferences?.coverage_target_pct ?? 70;
  const retTarget = preferences?.retained_profit_pct ?? 95;
  const finBasis = data?.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  const candSig = (c, hardwareOnly = false, includeRoofs = true) => {
    if (!c) return '';
    const roofs = (typeof activeResultProfile!=='undefined'&&activeResultProfile)?activeResultProfile.roofs.map(r=>r.id):['dormer', 'east', 'flat', 'secondary_south', 'south', 'west'];
    const roofCounts = c.settings?.roof_panels || c.roof_panels || c.roof_counts || c.settings?.roof_counts || {};
    const roofSig = roofs.map(r => `${r}:${roofCounts[r] ?? c.settings?.[r + '_panels'] ?? c[r + '_panels'] ?? 0}`).join(',');
    const totKwh = Number(c.battery_kwh ?? c.settings?.battery_kwh ?? 0);
    const totMods = Number(c.battery_modules ?? c.settings?.battery_modules ?? 0);
    const secMods = Number(c.battery_secondary_modules ?? c.settings?.battery_secondary_modules ?? 0);
    const isMix = Boolean(c.battery_mixed_sizes ?? c.settings?.battery_mixed_sizes);
    const storageSig = `${totKwh}_${totMods}_${secMods}_${isMix ? 1 : 0}`;
    const coupling = c.settings?.pv_battery_coupling || c.pv_battery_coupling || '';
    if (hardwareOnly) {
      return includeRoofs
        ? `${c.panel_count}_${c.kwp}_${storageSig}_${coupling}_${roofSig}`
        : `${c.panel_count}_${c.kwp}_${storageSig}_${coupling}`;
    }
    const route = c.settings?.routing_priority || c.routing_priority || '';
    const resRoute = c.resolved_routing_priority || '';
    const swHour = c.settings?.routing_switch_hour ?? c.routing_switch_hour ?? '';
    const stMonth = c.settings?.scheduled_start_month ?? c.scheduled_start_month ?? '';
    const endMonth = c.settings?.scheduled_end_month ?? c.scheduled_end_month ?? '';
    const routeSig = `${route}_${resRoute}_${swHour}_${stMonth}_${endMonth}`;
    return includeRoofs
      ? `${c.panel_count}_${c.kwp}_${storageSig}_${coupling}_${routeSig}_${roofSig}`
      : `${c.panel_count}_${c.kwp}_${storageSig}_${coupling}_${routeSig}`;
  };

  const formatRoofBreakdown = c => {
    if (!c) return '';
    const roofCounts = c.settings?.roof_panels || c.roof_panels || c.roof_counts || c.settings?.roof_counts || {};
    const roofs = (typeof activeResultProfile!=='undefined'&&activeResultProfile)?profileRoofRows():[
      { key: 'south', label: 'South' },
      { key: 'secondary_south', label: 'Secondary South' },
      { key: 'dormer', label: 'Dormer' },
      { key: 'east', label: 'East' },
      { key: 'west', label: 'West' },
      { key: 'flat', label: 'Flat' }
    ];
    const parts = [];
    for (const { key, label } of roofs) {
      const cnt = Number(roofCounts[key] ?? c.settings?.[key + '_panels'] ?? c[key + '_panels'] ?? 0);
      if (cnt > 0) parts.push(`${cnt} ${label}`);
    }
    return parts.join(', ');
  };

  const formatChoiceStorage = c => {
    if (!c) return '0 kWh storage (no battery)';
    const totKwh = Number(c.battery_kwh ?? c.settings?.battery_kwh ?? 0);
    const totMods = Number(c.battery_modules ?? c.settings?.battery_modules ?? 0);
    if (totKwh <= 0 && totMods <= 0) return '0 kWh storage (no battery)';
    if (totMods === 0) return `${nf(totKwh, 1)} kWh storage`;
    const isMix = Boolean(c.battery_mixed_sizes ?? c.settings?.battery_mixed_sizes);
    const secMods = isMix ? Number(c.battery_secondary_modules ?? c.settings?.battery_secondary_modules ?? 0) : 0;
    const firstMods = Number(c.first_modules ?? Math.max(0, totMods - secMods));
    const firstCap = Number(c.battery_module_kwh ?? c.settings?.battery_module_kwh ?? (typeof settings !== 'undefined' ? settings.battery_module_kwh : 0));
    const secCap = Number(c.battery_secondary_module_kwh ?? c.settings?.battery_secondary_module_kwh ?? (typeof settings !== 'undefined' ? settings.battery_secondary_module_kwh : 0));
    const fmtN = n => Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));

    if (isMix) {
      if (firstMods > 0 && secMods > 0) {
        return `${nf(totKwh, 1)} kWh storage (${firstMods} × ${fmtN(firstCap)} + ${secMods} × ${fmtN(secCap)} mod)`;
      } else if (secMods > 0) {
        return `${nf(totKwh, 1)} kWh storage (${secMods} × ${fmtN(secCap)} mod)`;
      } else {
        return `${nf(totKwh, 1)} kWh storage (${firstMods} × ${fmtN(firstCap)} mod)`;
      }
    }
    return `${nf(totKwh, 1)} kWh storage (${totMods} mod)`;
  };

  const formatTopology = c => {
    const isDc = (c?.settings?.pv_battery_coupling || c?.pv_battery_coupling || c?.effective_pv_battery_coupling) === 'dc_coupled';
    return isDc ? 'DC coupled · shared hybrid inverter' : 'AC coupled · separate battery inverter';
  };

  const getRouteVal = (cand, currentObj = null) => {
    if (currentObj) {
      return (typeof routeDisplayForResult === 'function' ? routeDisplayForResult(currentObj) : '—');
    }
    return (typeof routeDisplayForCandidate === 'function' && cand ? routeDisplayForCandidate(cand) : (cand?.resolved_routing_priority || cand?.settings?.routing_priority || cand?.routing_priority || '—'));
  };

  const getPaybackVal = (c, isCurrent = false, currentObj = null) => {
    if (isCurrent && currentObj) {
      const pbEnv = isEquityBasis ? currentObj.finance?.solar_equity : currentObj.finance?.pv_incremental;
      if (pbEnv && (pbEnv.discounted_payback_of_mean_cash_flows !== undefined || pbEnv.discounted_payback !== undefined)) {
        const pbRaw = pbEnv.discounted_payback_of_mean_cash_flows !== undefined ? pbEnv.discounted_payback_of_mean_cash_flows : pbEnv.discounted_payback;
        return formatPaybackYearsValue(pbRaw);
      } else if (!isEquityBasis && (currentObj.metrics?.discounted_payback_of_mean_cash_flows !== undefined || currentObj.metrics?.discounted_payback_years !== undefined)) {
        const pbRaw = currentObj.metrics?.discounted_payback_of_mean_cash_flows !== undefined ? currentObj.metrics.discounted_payback_of_mean_cash_flows : currentObj.metrics.discounted_payback_years;
        return formatPaybackYearsValue(pbRaw);
      }
      return '—';
    }
    if (!c) return '—';
    const cPb = c.discounted_payback_of_mean_cash_flows !== undefined
      ? c.discounted_payback_of_mean_cash_flows
      : (c.discounted_payback_years !== undefined
        ? c.discounted_payback_years
        : (c.metrics?.discounted_payback_of_mean_cash_flows !== undefined
          ? c.metrics.discounted_payback_of_mean_cash_flows
          : (c.metrics?.discounted_payback_years ?? null)));
    return formatPaybackYearsValue(cPb);
  };

  const getNumericPayback = (c, isCurrent = false, currentObj = null) => {
    if (isCurrent && currentObj) {
      const pbEnv = isEquityBasis ? currentObj.finance?.solar_equity : currentObj.finance?.pv_incremental;
      const raw = pbEnv?.discounted_payback_of_mean_cash_flows !== undefined
        ? pbEnv.discounted_payback_of_mean_cash_flows
        : (pbEnv?.discounted_payback?.median ?? (!isEquityBasis ? (currentObj.metrics?.discounted_payback_of_mean_cash_flows ?? currentObj.metrics?.discounted_payback_years?.median ?? currentObj.metrics?.discounted_payback_years) : null));
      return (raw !== null && raw !== undefined && Number.isFinite(Number(raw))) ? Number(raw) : null;
    }
    if (!c) return null;
    const raw = c.discounted_payback_of_mean_cash_flows !== undefined
      ? c.discounted_payback_of_mean_cash_flows
      : (c.discounted_payback_years !== undefined
        ? c.discounted_payback_years
        : (c.metrics?.discounted_payback_of_mean_cash_flows !== undefined
          ? c.metrics.discounted_payback_of_mean_cash_flows
          : (c.metrics?.discounted_payback_years ?? null)));
    const val = (raw && typeof raw === 'object') ? raw.median : raw;
    return (val !== null && val !== undefined && Number.isFinite(Number(val))) ? Number(val) : null;
  };

  const isSameAsFin = Boolean(financial && balanced && candSig(balanced) === candSig(financial));
  const sameSizeStorageCoupling = Boolean(financial && balanced && candSig(balanced, true, false) === candSig(financial, true, false));
  const hasConfigDifference = Boolean(sameSizeStorageCoupling && !isSameAsFin);
  const roofsMatch = Boolean(hasConfigDifference && candSig(balanced, true, true) === candSig(financial, true, true));
  const routesDiffer = Boolean(hasConfigDifference && candSig(balanced, false, false) !== candSig(financial, false, false));
  const diffBadgeText = roofsMatch
    ? 'Same hardware · different schedule'
    : `Same size · different roof layout${routesDiffer ? ' & schedule' : ''}`;
  const mainDiffNote = roofsMatch
    ? 'Hardware is identical to financial winner; schedule changes coverage and return.'
    : (routesDiffer
      ? 'Same total size and storage as financial winner; roof layout and schedule change coverage and return.'
      : 'Same total size and storage as financial winner; roof layout changes generation profile and coverage.');
  const finDiffNote = roofsMatch
    ? 'Hardware is identical to balanced choice; schedule changes coverage and return.'
    : (routesDiffer
      ? 'Same total size and storage as balanced choice; roof layout and schedule change coverage and return.'
      : 'Same total size and storage as balanced choice; roof layout changes generation profile and coverage.');

  // Current baseline from actual backend payload (comparisons or current_system)
  const currentRes = (typeof current !== 'undefined') ? current?.result : null;
  const currComp = (Array.isArray(data.comparisons) ? data.comparisons.find(c => c && (c.id === 'current' || c.is_current || c.cost_basis === 'modeled_benchmark')) : null)
    || data.current_system
    || data.benchmarks?.current_system
    || null;
  const quotedComp = Array.isArray(data.comparisons)
    ? data.comparisons.find(c => c && (c.id === 'current_quoted' || c.cost_basis === 'contractor_quote'))
    : null;

  const curFlows = (typeof getCanonicalEnsembleFlows === 'function' ? getCanonicalEnsembleFlows(currentRes) : null) ?? currentRes?.headline_energy_flows ?? null;
  const isQuoteActive = (typeof isItemizedPricingActive === 'function' && isItemizedPricingActive()) || (typeof settings !== 'undefined' && settings?.quote_total != null);

  let curPanels = 0, curKwp = 0, curStorageDesc = '0 kWh storage (no battery)', curTopology = 'AC coupled · separate battery inverter';
  let curCapex = null, curEquity = null, curLoan = null, curProjNpv = null, curEqNpv = null, curAut = null;
  let curClippedKwh = null, curClippedPct = null, curPbStr = '—', curPbNum = null, curRoute = '—';
  let canCompareFinancials = false;

  if (currComp) {
    curPanels = currComp.panel_count ?? currentRes?.layout?.panel_count ?? 0;
    curKwp = currComp.kwp ?? currentRes?.layout?.kwp ?? 0;
    curStorageDesc = formatChoiceStorage(currComp);
    curTopology = formatTopology(currentRes || currComp);
    curCapex = currComp.capex_eur ?? (currentRes?.finance?.capex_eur ?? null);
    curLoan = currComp.solar_loan_principal_eur ?? currComp.loan_principal_eur ?? (currentRes?.finance?.solar_loan_principal_eur ?? null);
    curEquity = currComp.initial_solar_equity_eur ?? (curCapex != null && curLoan != null ? curCapex - curLoan : (currentRes?.finance?.initial_solar_equity_eur ?? null));
    curProjNpv = currComp.project_npv_mean !== undefined ? currComp.project_npv_mean : (currComp.project_npv_median !== undefined ? currComp.project_npv_median : (currComp.project_npv?.mean ?? currComp.project_npv?.median ?? currentRes?.finance?.pv_incremental?.npv_mean ?? currentRes?.finance?.pv_incremental?.npv?.mean ?? currentRes?.finance?.pv_incremental?.npv?.median ?? null));
    curEqNpv = currComp.equity_npv_mean !== undefined ? currComp.equity_npv_mean : (currComp.equity_npv_median !== undefined ? currComp.equity_npv_median : (currComp.equity_npv?.mean ?? currComp.equity_npv?.median ?? currentRes?.finance?.solar_equity?.npv_mean ?? currentRes?.finance?.solar_equity?.npv?.mean ?? currentRes?.finance?.solar_equity?.npv?.median ?? null));
    curAut = curFlows?.autarky ?? currComp.autarky_mean ?? (currComp.autarky_median ?? (currentRes?.energy?.ensemble_autarky ?? (currentRes?.metrics?.autarky?.mean !== undefined ? currentRes.metrics.autarky.mean : (currentRes?.metrics?.autarky?.median ?? null))));
    curClippedKwh = curFlows?.clipped_kwh ?? currComp.annual_clipped_kwh_mean ?? (currComp.annual_clipped_kwh_median ?? null);
    curClippedPct = curFlows?.clipped_pct ?? currComp.annual_clipped_pct_mean ?? (currComp.annual_clipped_pct_median ?? null);
    curPbStr = getPaybackVal(currComp, false, null) !== '—' ? getPaybackVal(currComp, false, null) : getPaybackVal(null, true, currentRes);
    curPbNum = getNumericPayback(currComp, false, null);
    curRoute = getRouteVal(currComp, currentRes);
    canCompareFinancials = true;
  } else if (currentRes) {
    curPanels = currentRes.layout?.panel_count ?? 0;
    curKwp = currentRes.layout?.kwp ?? 0;
    curStorageDesc = formatChoiceStorage(currentRes);
    curTopology = formatTopology(currentRes);
    curCapex = currentRes.finance?.capex_eur ?? null;
    curLoan = currentRes.finance?.solar_loan_principal_eur ?? null;
    curEquity = currentRes.finance?.initial_solar_equity_eur ?? (curCapex != null && curLoan != null ? Math.max(0, curCapex - curLoan) : null);
    curProjNpv = currentRes.finance?.pv_incremental?.npv_mean !== undefined ? currentRes.finance.pv_incremental.npv_mean : (currentRes.finance?.pv_incremental?.npv?.mean !== undefined ? currentRes.finance.pv_incremental.npv.mean : (currentRes.finance?.pv_incremental?.npv?.median ?? currentRes.finance?.pv_incremental?.npv_eur?.median ?? null));
    curEqNpv = currentRes.finance?.solar_equity?.npv_mean !== undefined ? currentRes.finance.solar_equity.npv_mean : (currentRes.finance?.solar_equity?.npv?.mean !== undefined ? currentRes.finance.solar_equity.npv.mean : (currentRes.finance?.solar_equity?.npv?.median ?? currentRes.finance?.solar_equity?.npv_eur?.median ?? null));
    curAut = curFlows?.autarky ?? currentRes.energy?.ensemble_autarky ?? (currentRes.metrics?.autarky?.mean !== undefined ? currentRes.metrics.autarky.mean : (currentRes.metrics?.autarky?.median ?? null));
    curClippedKwh = curFlows?.clipped_kwh ?? null;
    curClippedPct = curFlows?.clipped_pct ?? null;
    curPbStr = getPaybackVal(null, true, currentRes);
    curPbNum = getNumericPayback(null, true, currentRes);
    curRoute = getRouteVal(null, currentRes);
    canCompareFinancials = !isQuoteActive;
  }

  // Active quoted euro for benchmark sublabel
  const activeQuotedEur = quotedComp?.capex_eur ?? (isQuoteActive && currentRes?.finance?.capex_eur != null ? currentRes.finance.capex_eur : null);
  const priceSublabel = (activeQuotedEur != null && (curCapex == null || Math.abs(activeQuotedEur - curCapex) > 1))
    ? `<div class="solar-price-sublabel muted"><small>Parametric sizing benchmark · Active quote: ${fmt(activeQuotedEur, 'eur')}</small></div>`
    : '<div class="solar-price-sublabel muted"><small>Parametric sizing benchmark</small></div>';

  const curRankingNpv = isEquityBasis ? curEqNpv : curProjNpv;

  const mainCand = balanced || null;
  let mainColHtml = '';

  if (mainCand) {
    const mainCapex = mainCand.capex_eur ?? null;
    const mainLoan = mainCand.solar_loan_principal_eur ?? null;
    const mainEquity = mainCand.initial_solar_equity_eur ?? (mainCapex != null && mainLoan != null ? Math.max(0, mainCapex - mainLoan) : null);
    const mainProj = mainCand.project_npv_mean !== undefined ? mainCand.project_npv_mean : (mainCand.project_npv_median !== undefined ? mainCand.project_npv_median : (finBasis === 'project' ? (mainCand.npv_mean !== undefined ? mainCand.npv_mean : (mainCand.npv_median ?? null)) : null));
    const mainEq = mainCand.equity_npv_mean !== undefined ? mainCand.equity_npv_mean : (mainCand.equity_npv_median !== undefined ? mainCand.equity_npv_median : (finBasis === 'after_financing' ? (mainCand.npv_mean !== undefined ? mainCand.npv_mean : (mainCand.npv_median ?? null)) : null));
    const mainRankingNpv = isEquityBasis ? mainEq : mainProj;
    const mainAut = mainCand.autarky_mean !== undefined ? mainCand.autarky_mean : (mainCand.autarky_median ?? null);
    const mainClippedKwh = mainCand.annual_clipped_kwh_mean !== undefined ? mainCand.annual_clipped_kwh_mean : (mainCand.annual_clipped_kwh_median ?? null);
    const mainClippedPct = mainCand.annual_clipped_pct_mean !== undefined ? mainCand.annual_clipped_pct_mean : (mainCand.annual_clipped_pct_median ?? null);
    const mainPbStr = getPaybackVal(mainCand);
    const mainPbNum = getNumericPayback(mainCand);
    const mainRoute = getRouteVal(mainCand);
    const mainTopology = formatTopology(mainCand);

    const displayedDelta = (a, b, digits = 0) => {
      if (a == null || b == null || !Number.isFinite(Number(a)) || !Number.isFinite(Number(b))) return null;
      const vA = Number(nf(Number(a), digits).replace(/,/g, ''));
      const vB = Number(nf(Number(b), digits).replace(/,/g, ''));
      const diff = Number((vA - vB).toFixed(digits));
      return (diff === 0 || Object.is(diff, -0)) ? 0 : diff;
    };

    // Explicit deltas vs current
    const dPanels = (mainCand.panel_count != null && curPanels != null) ? (mainCand.panel_count - curPanels) : null;
    const dKwp = (mainCand.kwp != null && curKwp != null) ? (mainCand.kwp - curKwp) : null;
    const dPanelsStr = (dPanels != null && dKwp != null)
      ? `${dPanels >= 0 ? '+' : ''}${dPanels} panels (${dKwp >= 0 ? '+' : ''}${nf(dKwp, 2)} kWp)`
      : `${mainCand.panel_count} panels (${nf(mainCand.kwp, 2)} kWp)`;

    const dCapex = canCompareFinancials ? displayedDelta(mainCapex, curCapex, 0) : null;
    const deltaCapexStr = dCapex != null ? ` <span class="solar-delta-pill ${dCapex < 0 ? 'good' : 'neutral'}">(${dCapex >= 0 ? '+' : ''}${fmt(dCapex, 'eur')})</span>` : '';

    const dEquity = canCompareFinancials ? displayedDelta(mainEquity, curEquity, 0) : null;
    const deltaEquityStr = dEquity != null ? ` <span class="solar-delta-pill ${dEquity < 0 ? 'good' : 'neutral'}">(${dEquity >= 0 ? '+' : ''}${fmt(dEquity, 'eur')})</span>` : '';

    const dProj = canCompareFinancials ? displayedDelta(mainProj, curProjNpv, 0) : null;
    const deltaProjStr = dProj != null ? ` <span class="solar-delta-pill ${dProj === 0 ? 'neutral' : (dProj > 0 ? 'good' : 'bad')}">(${dProj >= 0 ? '+' : ''}${fmt(dProj, 'eur')})</span>` : '';

    const dEq = canCompareFinancials ? displayedDelta(mainEq, curEqNpv, 0) : null;
    const deltaEqStr = dEq != null ? ` <span class="solar-delta-pill ${dEq === 0 ? 'neutral' : (dEq > 0 ? 'good' : 'bad')}">(${dEq >= 0 ? '+' : ''}${fmt(dEq, 'eur')})</span>` : '';

    const dRankingNpv = (canCompareFinancials && mainRankingNpv != null && curRankingNpv != null) ? (mainRankingNpv - curRankingNpv) : null;
    const dNpvPillStr = dRankingNpv != null
      ? `${dRankingNpv >= 0 ? '+' : ''}${fmt(dRankingNpv, 'eur')} ${isEquityBasis ? 'Equity' : 'Project'} NPV`
      : `${fmt(mainRankingNpv, 'eur')} ${isEquityBasis ? 'Equity' : 'Project'} NPV`;

    const dAutPp = displayedDelta(mainAut != null ? mainAut * 100 : null, curAut != null ? curAut * 100 : null, 1);
    const deltaAutStr = dAutPp != null ? ` <span class="solar-delta-pill ${dAutPp === 0 ? 'neutral' : (dAutPp > 0 ? 'good' : 'bad')}">(${dAutPp >= 0 ? '+' : ''}${dAutPp.toFixed(1)} pp)</span>` : '';
    const dAutPillStr = dAutPp != null ? `${dAutPp >= 0 ? '+' : ''}${dAutPp.toFixed(1)} pp coverage` : `${fmt(mainAut, 'pct')} coverage`;

    // Explicit-null payback numeric delta: no fallback through null
    const dPb = displayedDelta(mainPbNum, curPbNum, 1);
    const deltaPbStr = dPb != null ? ` <span class="solar-delta-pill ${dPb === 0 ? 'neutral' : (dPb < 0 ? 'good' : 'bad')}">(${dPb >= 0 ? '+' : ''}${dPb.toFixed(1)} y)</span>` : '';

    const dClippedKwh = displayedDelta(mainClippedKwh, curClippedKwh, 1);
    const deltaClippedStr = dClippedKwh != null
      ? ` <span class="solar-delta-pill ${dClippedKwh === 0 ? 'neutral' : (dClippedKwh < 0 ? 'good' : 'bad')}">(${dClippedKwh >= 0 ? '+' : ''}${nf(dClippedKwh, 1)} kWh/yr)</span>`
      : '';
    // Concise rationale: do not claim max household coverage just coincidence of two selected strategies
    const conciseRationale = isSameAsFin
      ? `Coincident configuration for both highest financial return and balanced preference strategy (${mainCand.certified_constrained_optimum ? 'certified global optimum' : 'best found candidate'}).`
      : (hasConfigDifference
        ? (roofsMatch
          ? `Same hardware as financial winner; dispatch schedule changes coverage and financial return (retains <strong>${mainCand.retained_npv_pct}%</strong> of max NPV, ${fmt(mainCand.sacrificed_npv_eur ?? 0, 'eur')} trade-off for <strong>${mainCand.coverage_gain_pct >= 0 ? '+' : ''}${mainCand.coverage_gain_pct}%</strong> coverage gain). ${mainCand.certified_constrained_optimum ? 'Certified optimum.' : 'Best found.'}`
          : `Alternative roof placement${routesDiffer ? ' and schedule' : ''} changes coverage and return (retains <strong>${mainCand.retained_npv_pct}%</strong> of max NPV, ${fmt(mainCand.sacrificed_npv_eur ?? 0, 'eur')} trade-off for <strong>${mainCand.coverage_gain_pct >= 0 ? '+' : ''}${mainCand.coverage_gain_pct}%</strong> coverage gain). ${mainCand.certified_constrained_optimum ? 'Certified optimum.' : 'Best found.'}`)
        : `Retains <strong>${mainCand.retained_npv_pct}%</strong> of max NPV (${fmt(mainCand.sacrificed_npv_eur ?? 0, 'eur')} trade-off for <strong>${mainCand.coverage_gain_pct >= 0 ? '+' : ''}${mainCand.coverage_gain_pct}%</strong> coverage gain vs financial winner). ${mainCand.certified_constrained_optimum ? 'Certified optimum within model bounds.' : 'Best found among evaluated candidates.'}`);

    mainColHtml = `
      <div class="solar-hero-column main-choice">
        <div class="solar-choice-header">
          <div>
            <h3 class="solar-choice-title">${isSameAsFin ? 'Balanced &amp; Financial Winner' : 'Balanced choice'}</h3>
            <div class="solar-choice-badges">
              <span class="solar-choice-badge main">Main recommendation</span>
              ${isSameAsFin ? '<span class="solar-choice-badge financial">Financial benchmark</span>' : ''}
            </div>
          </div>
          <div class="solar-main-pills">
            <span class="solar-delta-pill">${dPanelsStr}</span>
            <span class="solar-delta-pill">${dNpvPillStr}</span>
            <span class="solar-delta-pill">${dAutPillStr}</span>
          </div>
        </div>
        <div>
          <div class="solar-choice-specs">${mainCand.panel_count} panels (${nf(mainCand.kwp, 2)} kWp) · ${formatChoiceStorage(mainCand)}</div>
          <div class="solar-choice-subspecs">${esc(mainTopology)}</div>
          ${hasConfigDifference ? `
          <div class="${roofsMatch ? 'solar-schedule-difference' : 'solar-configuration-difference'}">
            <span class="${roofsMatch ? 'solar-schedule-difference-badge' : 'solar-configuration-difference-badge'}">${esc(diffBadgeText)}</span>
            ${formatRoofBreakdown(mainCand) ? `<div class="solar-configuration-roofs"><span class="muted">Roofs:</span> ${esc(formatRoofBreakdown(mainCand))}</div>` : ''}
            <div class="${roofsMatch ? 'solar-schedule-difference-route' : 'solar-configuration-difference-route'}"><span class="muted">Routing:</span> ${esc(mainRoute).replace(/(switch \d{1,2}:\d{2})/g, '<strong>$1</strong>')}</div>
            <div class="${roofsMatch ? 'solar-schedule-difference-note' : 'solar-configuration-difference-note'} muted"><small>${esc(mainDiffNote)}</small></div>
          </div>` : ''}
        </div>
        <div class="solar-delta-note muted"><small><strong>Δ vs Current:</strong> Differences against active baseline</small></div>
        <div class="solar-choice-metrics">
          <div>
            <div class="solar-choice-metric-label">Turnkey Capex</div>
            <div class="solar-choice-metric-val">${mainCapex != null ? fmt(mainCapex, 'eur') : '—'}${deltaCapexStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Initial Equity / Loan Principal</div>
            <div class="solar-choice-metric-val">${mainEquity != null ? fmt(mainEquity, 'eur') : '—'} <small class="muted">equity</small> · ${mainLoan != null ? fmt(mainLoan, 'eur') : '—'} <small class="muted">loan</small>${deltaEquityStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Project NPV ${!isEquityBasis ? '<span class="metric-basis-pill">Ranking</span>' : ''}</div>
            <div class="solar-choice-metric-val ${!isEquityBasis ? 'highlight' : ''}">${mainProj != null ? fmt(mainProj, 'eur') : '—'}${deltaProjStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Equity NPV ${isEquityBasis ? '<span class="metric-basis-pill">Ranking</span>' : ''}</div>
            <div class="solar-choice-metric-val ${isEquityBasis ? 'highlight' : ''}">${mainEq != null ? fmt(mainEq, 'eur') : '—'}${deltaEqStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Household coverage (Autarky)</div>
            <div class="solar-choice-metric-val">${mainAut != null ? fmt(mainAut, 'pct') : '—'}${deltaAutStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Discounted payback</div>
            <div class="solar-choice-metric-val">${mainPbStr}${deltaPbStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Modeled PV curtailed</div>
            <div class="solar-choice-metric-val">${mainClippedKwh != null ? `${nf(mainClippedKwh, 1)} kWh/yr · ${nf(mainClippedPct, 2)}%` : '—'}${deltaClippedStr}</div>
            <small>Historical-weather ensemble mean first-year dispatch</small>
          </div>
          <div>
            <div class="solar-choice-metric-label">Routing priority</div>
            <div class="solar-choice-metric-val routing-val">${esc(mainRoute)}</div>
          </div>
        </div>
        <div class="solar-choice-footer">
          <div class="solar-choice-meta">
            <p>${conciseRationale}</p>
          </div>
          <button type="button" class="solar-choice-apply confirm-save" data-choice="balanced">Apply recommended choice</button>
        </div>
      </div>
    `;
  } else {
    const balReason = role_status?.balanced?.reason || (financial ? `Best financial return has non-positive mean NPV (${fmt(financial.npv_mean ?? (financial.npv_mean === undefined ? financial.npv_median : null), 'eur')}). An NPV sacrifice threshold cannot be computed for non-positive returns. The no-install baseline (€0 capex, €0 NPV) is the economically advised choice.` : 'No feasible configuration was found within the evaluated search constraints.');
    mainColHtml = `
      <div class="solar-hero-column main-choice unavailable">
        <div class="solar-choice-header">
          <div>
            <h3 class="solar-choice-title">Balanced choice</h3>
            <span class="solar-choice-badge main">Main recommendation</span>
          </div>
        </div>
        <div class="solar-choice-meta">
          <p><strong>Unavailable:</strong> ${esc(balReason)}</p>
        </div>
      </div>
    `;
  }

  let curColHtml = '';
  if (currentRes || currComp) {
    curColHtml = `
      <div class="solar-hero-column current-choice">
        <div class="solar-choice-header">
          <div>
            <h3 class="solar-choice-title">Current configuration</h3>
            <span class="solar-choice-badge current">Active system</span>
          </div>
        </div>
        <div>
          <div class="solar-choice-specs">${curPanels} panels (${nf(curKwp, 2)} kWp) · ${curStorageDesc}</div>
          <div class="solar-choice-subspecs">${esc(curTopology)}</div>
        </div>
        <div class="solar-delta-note muted"><small>Comparison baseline · current physical configuration</small></div>
        <div class="solar-choice-metrics">
          <div>
            <div class="solar-choice-metric-label">Turnkey Capex</div>
            <div class="solar-choice-metric-val">${curCapex != null ? fmt(curCapex, 'eur') : '—'}</div>
            ${priceSublabel}
          </div>
          <div>
            <div class="solar-choice-metric-label">Initial Equity / Loan Principal</div>
            <div class="solar-choice-metric-val">${curEquity != null ? fmt(curEquity, 'eur') : '—'} <small class="muted">equity</small> · ${curLoan != null ? fmt(curLoan, 'eur') : '—'} <small class="muted">loan</small></div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Project NPV ${!isEquityBasis ? '<span class="metric-basis-pill">Ranking</span>' : ''}</div>
            <div class="solar-choice-metric-val ${!isEquityBasis ? 'highlight' : ''}">${curProjNpv != null ? fmt(curProjNpv, 'eur') : '—'}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Equity NPV ${isEquityBasis ? '<span class="metric-basis-pill">Ranking</span>' : ''}</div>
            <div class="solar-choice-metric-val ${isEquityBasis ? 'highlight' : ''}">${curEqNpv != null ? fmt(curEqNpv, 'eur') : '—'}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Household coverage (Autarky)</div>
            <div class="solar-choice-metric-val">${curAut != null ? fmt(curAut, 'pct') : '—'}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Discounted payback</div>
            <div class="solar-choice-metric-val">${curPbStr}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Modeled PV curtailed</div>
            <div class="solar-choice-metric-val">${curClippedKwh != null ? `${nf(curClippedKwh, 1)} kWh/yr · ${nf(curClippedPct, 2)}%` : '—'}</div>
            <small>Historical-weather ensemble mean first-year dispatch</small>
          </div>
          <div>
            <div class="solar-choice-metric-label">Routing priority</div>
            <div class="solar-choice-metric-val routing-val">${esc(curRoute)}</div>
          </div>
        </div>
        <div class="solar-choice-footer">
          <div class="solar-choice-meta">
            <p>Your active configuration under evaluation. Serves as baseline against which recommendations are benchmarked.</p>
          </div>
          <button type="button" class="solar-current-btn secondary" disabled>Active</button>
        </div>
      </div>
    `;
  } else {
    curColHtml = `
      <div class="solar-hero-column current-choice unavailable">
        <div class="solar-choice-header">
          <div>
            <h3 class="solar-choice-title">Current configuration</h3>
            <span class="solar-choice-badge current">Active system</span>
          </div>
        </div>
        <div class="solar-choice-meta">
          <p>Current configuration data unavailable.</p>
        </div>
      </div>
    `;
  }

  const heroHtml = `
    <div class="solar-hero-comparison">
      ${curColHtml}
      ${mainColHtml}
    </div>
  `;

  const renderAltCard = ({ roleKey, title, badgeText, badgeClass, cand, ctaText, tradeoffText, isNoInstall = false }) => {
    const cCapex = cand.capex_eur ?? null;
    const cLoan = cand.solar_loan_principal_eur ?? null;
    const cEquity = cand.initial_solar_equity_eur ?? (cCapex != null && cLoan != null ? Math.max(0, cCapex - cLoan) : null);
    const cProj = cand.project_npv_mean !== undefined ? cand.project_npv_mean : (cand.project_npv_median !== undefined ? cand.project_npv_median : (finBasis === 'project' ? (cand.npv_mean !== undefined ? cand.npv_mean : (cand.npv_median ?? null)) : null));
    const cEq = cand.equity_npv_mean !== undefined ? cand.equity_npv_mean : (cand.equity_npv_median !== undefined ? cand.equity_npv_median : (finBasis === 'after_financing' ? (cand.npv_mean !== undefined ? cand.npv_mean : (cand.npv_median ?? null)) : null));
    const cAut = cand.autarky_mean !== undefined ? cand.autarky_mean : (cand.autarky_median ?? null);
    const cClippedKwh = cand.annual_clipped_kwh_mean !== undefined ? cand.annual_clipped_kwh_mean : (cand.annual_clipped_kwh_median ?? null);
    const cClippedPct = cand.annual_clipped_pct_mean !== undefined ? cand.annual_clipped_pct_mean : (cand.annual_clipped_pct_median ?? null);
    const cPb = getPaybackVal(cand);
    const cRoute = getRouteVal(cand);
    const cTopology = formatTopology(cand);

    return `
      <div class="solar-alternative-card">
        <div class="solar-choice-header">
          <div>
            <h4 class="solar-choice-title">${esc(title)}</h4>
            <div class="solar-choice-badges">
              <span class="solar-choice-badge ${badgeClass}">${esc(badgeText)}</span>
            </div>
          </div>
          <button type="button" class="solar-choice-apply secondary" data-choice="${roleKey}">${esc(ctaText)}</button>
        </div>
        <div>
          <div class="solar-choice-specs">${cand.panel_count} panels (${nf(cand.kwp, 2)} kWp) · ${formatChoiceStorage(cand)}</div>
          <div class="solar-choice-subspecs">${esc(cTopology)}</div>
          ${(roleKey === 'financial' && hasConfigDifference) ? `
          <div class="${roofsMatch ? 'solar-schedule-difference' : 'solar-configuration-difference'}">
            <span class="${roofsMatch ? 'solar-schedule-difference-badge' : 'solar-configuration-difference-badge'}">${esc(diffBadgeText)}</span>
            ${formatRoofBreakdown(cand) ? `<div class="solar-configuration-roofs"><span class="muted">Roofs:</span> ${esc(formatRoofBreakdown(cand))}</div>` : ''}
            <div class="${roofsMatch ? 'solar-schedule-difference-route' : 'solar-configuration-difference-route'}"><span class="muted">Routing:</span> ${esc(cRoute).replace(/(switch \d{1,2}:\d{2})/g, '<strong>$1</strong>')}</div>
            <div class="${roofsMatch ? 'solar-schedule-difference-note' : 'solar-configuration-difference-note'} muted"><small>${esc(finDiffNote)}</small></div>
          </div>` : ''}
        </div>
        <div class="solar-tradeoff-pills">
          <span class="solar-tradeoff-pill">${esc(tradeoffText)}</span>
        </div>
        <div class="solar-alternative-metrics">
          <div>
            <div class="solar-choice-metric-label">Turnkey capex / loan</div>
            <div class="solar-choice-metric-val">${cCapex != null ? fmt(cCapex, 'eur') : '—'}</div>
            <div class="solar-metric-sub">Equity: ${cEquity != null ? fmt(cEquity, 'eur') : '—'} · Loan: ${cLoan != null ? fmt(cLoan, 'eur') : '—'}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Project / Equity NPV</div>
            <div class="solar-choice-metric-val ${!isEquityBasis ? 'highlight' : ''}">${cProj != null ? fmt(cProj, 'eur') : '—'}</div>
            <div class="solar-metric-sub ${isEquityBasis ? 'highlight' : ''}">Equity: ${cEq != null ? fmt(cEq, 'eur') : '—'}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Coverage / Payback</div>
            <div class="solar-choice-metric-val">${cAut != null ? fmt(cAut, 'pct') : '—'}</div>
            <div class="solar-metric-sub">Payback: ${cPb}</div>
          </div>
          <div>
            <div class="solar-choice-metric-label">Modeled PV curtailed</div>
            <div class="solar-choice-metric-val">${cClippedKwh != null ? `${nf(cClippedKwh, 1)} kWh/yr · ${nf(cClippedPct, 2)}%` : '—'}</div>
            <small>Historical-weather ensemble mean first-year dispatch</small>
          </div>
        </div>
        <div class="solar-metric-sub solar-alternative-routing"><strong>Routing:</strong> ${esc(cRoute)}</div>
      </div>
    `;
  };

  const renderUnavailableAltCard = (roleKey, title, badgeClass, badgeText, reason) => `
    <div class="solar-alternative-card unavailable">
      <div class="solar-choice-header">
        <div>
          <h4 class="solar-choice-title">${esc(title)}</h4>
          <div class="solar-choice-badges">
            <span class="solar-choice-badge ${badgeClass}">${esc(badgeText)}</span>
          </div>
        </div>
      </div>
      <div class="solar-choice-meta">
        <p><strong>Unavailable:</strong> ${esc(reason)}</p>
      </div>
    </div>
  `;

  let finAltHtml = '';
  if (!isSameAsFin) {
    if (financial) {
      const isNoInstall = Boolean(financial.is_no_install);
      const finTitle = isNoInstall ? 'Best financial return: No-install' : 'Best financial return';
      const finBadge = isNoInstall ? 'No-install baseline' : 'Financial benchmark';
      const finTradeoff = isNoInstall
        ? 'No-install baseline (€0 capex, €0 NPV)'
        : (hasConfigDifference
          ? (roofsMatch
            ? `Highest modeled ${isEquityBasis ? 'equity' : 'project'} NPV · Same hardware as balanced, different schedule`
            : `Highest modeled ${isEquityBasis ? 'equity' : 'project'} NPV · Same size as balanced, ${routesDiffer ? 'different layout & schedule' : 'different roof layout'}`)
          : `Highest modeled ${isEquityBasis ? 'equity' : 'project'} NPV · Baseline benchmark`);
      finAltHtml = renderAltCard({
        roleKey: 'financial',
        title: finTitle,
        badgeText: finBadge,
        badgeClass: 'financial',
        cand: financial,
        ctaText: isNoInstall ? 'Apply no-install choice' : 'Apply financial choice',
        tradeoffText: finTradeoff,
        isNoInstall
      });
    } else {
      const finReason = role_status?.financial?.reason || 'No feasible configuration was found within the evaluated search constraints.';
      finAltHtml = renderUnavailableAltCard('financial', 'Best financial return', 'financial', 'Financial benchmark', finReason);
    }
  }

  let budAltHtml = '';
  if (budget) {
    const budSavings = budget.capex_savings_vs_financial ?? (financial ? Math.max(0, financial.capex_eur - budget.capex_eur) : 0);
    const budTarget = budget.coverage_target_pct ?? covTarget;
    const budTradeoff = `Target ≥${budTarget}% coverage${budSavings > 0 ? ` · Saves €${nf(budSavings)} upfront vs financial winner` : ' with positive NPV'}`;
    budAltHtml = renderAltCard({
      roleKey: 'budget',
      title: 'Budget choice',
      badgeText: 'Budget choice',
      badgeClass: 'budget',
      cand: budget,
      ctaText: 'Apply budget choice',
      tradeoffText: budTradeoff
    });
  } else {
    const budReason = role_status?.budget?.reason || (financial ? `No configuration reaches the ${covTarget}% household demand coverage target with strictly positive ${isEquityBasis ? 'equity' : 'project'} NPV under current constraints.` : 'No feasible configuration was found within the evaluated search constraints.');
    budAltHtml = renderUnavailableAltCard('budget', 'Budget choice', 'budget', 'Budget choice', budReason);
  }

  let smAltHtml = '';
  const hasSmallerRole = (smaller !== undefined) || (role_status && role_status.smaller);
  if (hasSmallerRole) {
    if (smaller) {
      const capexSav = smaller.capex_savings_vs_financial ?? (financial ? Math.max(0, financial.capex_eur - smaller.capex_eur) : 0);
      const finNpv = financial ? (financial.npv_mean ?? (financial.npv_mean === undefined ? financial.npv_median : null) ?? 0) : 0;
      const smallNpv = smaller ? (smaller.npv_mean ?? (smaller.npv_mean === undefined ? smaller.npv_median : null) ?? 0) : 0;
      const sacEur = smaller.sacrificed_npv_eur ?? (financial ? Math.max(0, finNpv - smallNpv) : 0);
      const sacPct = smaller.sacrificed_npv_pct ?? (financial && finNpv > 0 ? Math.round(sacEur / finNpv * 1000) / 10 : 0);
      const retPct = smaller.retained_npv_pct ?? smaller.retained_profit_pct ?? retTarget;
      const smTradeoff = `Retains ${retPct}% of financial return · Saves €${nf(capexSav)} upfront capex${sacEur > 0 ? ` (sacrifices €${nf(sacEur)} · ${sacPct}%)` : ''}`;
      smAltHtml = renderAltCard({
        roleKey: 'smaller',
        title: 'Lower-cost alternative',
        badgeText: 'Lower-cost alternative',
        badgeClass: 'smaller',
        cand: smaller,
        ctaText: 'Apply lower-cost alternative',
        tradeoffText: smTradeoff
      });
    } else {
      const smReason = role_status?.smaller?.reason || `No configuration retaining at least ${retTarget}% of financial optimum return with positive NPV was found in the evaluated domain.`;
      smAltHtml = renderUnavailableAltCard('smaller', 'Lower-cost alternative', 'smaller', 'Lower-cost alternative', smReason);
    }
  }

  const altsHtml = (finAltHtml || budAltHtml || smAltHtml)
    ? `
      <div class="solar-wide-alternatives">
        ${finAltHtml}
        ${budAltHtml}
        ${smAltHtml}
      </div>
    `
    : '';

  target.innerHTML = heroHtml + altsHtml;

  const wrap = $('solarResultsWrap');
  const isStale = (wrap && wrap.classList.contains('stale')) || solarInFlight || (solarAcceptedFingerprint !== solarStateFingerprint()) || (typeof shouldBlockSolarSizingForAcDraft === 'function' && shouldBlockSolarSizingForAcDraft());

  if (typeof target.querySelectorAll === 'function') {
    target.querySelectorAll('.solar-choice-apply').forEach(btn => {
      const role = btn.getAttribute('data-choice');
      const cand = role ? choices[role] : null;
      if (!cand || isStale) {
        btn.disabled = true;
      } else {
        btn.disabled = false;
      }
      btn.addEventListener('click', () => {
        if (cand && !btn.disabled) {
          applySolarCandidate(cand, cand.role_label || role);
        }
      });
    });
  }
}

function renderSolarAlternativesTable(data) {
  const target = $('solarAlternativesTable');
  if (!target) return;
  const alts = data.top_alternatives;
  if (!alts || !alts.length) {
    target.innerHTML = '<p class="muted">No alternative trade-off configurations available.</p>';
    return;
  }
  const finBasis = data.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  const headers = [
    'Configuration & Strategy',
    'PV Array & Storage',
    'Modeled PV curtailed',
    'Installed Capex',
    'Extra investment vs financial choice',
    `${isEquityBasis ? 'Equity' : 'Project'} NPV (mean)`,
    'Difference vs financial choice',
    'Household coverage',
    'Annual Imports',
    'Annual Exports',
    'Discounted Payback',
    'Action'
  ];

  const rows = alts.map((c, idx) => {
    const rolesHtml = (c.roles || []).map(r => `<span class="pill ${r.includes('Main') || r.includes('Balanced') ? 'active' : ''}">${esc(r)}</span>`).join(' ');

    const extraCapex = c.extra_capex_vs_financial;
    let extraCapexHtml = '';
    if (Math.abs(extraCapex) < 0.01) {
      extraCapexHtml = '€0 <small class="muted">(baseline)</small>';
    } else if (extraCapex > 0) {
      extraCapexHtml = `+${fmt(extraCapex, 'eur')} <small class="bad">(extra cost)</small>`;
    } else {
      extraCapexHtml = `-${fmt(Math.abs(extraCapex), 'eur')} <small class="good">(saves capex)</small>`;
    }

    const npvDiff = c.npv_diff_vs_financial;
    let npvDiffHtml = '';
    if (Math.abs(npvDiff) < 0.01) {
      npvDiffHtml = `€0 <small class="muted">(baseline)</small>${c.retained_npv_pct != null ? '<br><small>100% max NPV</small>' : ''}`;
    } else if (npvDiff < 0) {
      npvDiffHtml = `-${fmt(Math.abs(npvDiff), 'eur')}${c.retained_npv_pct != null ? `<br><small>${c.retained_npv_pct}% max NPV</small>` : ''}`;
    } else {
      npvDiffHtml = `+${fmt(npvDiff, 'eur')}`;
    }

    const covDiff = c.coverage_diff_vs_financial_pct;
    const covDiffHtml = covDiff !== 0 ? `<br><small class="${covDiff > 0 ? 'good' : 'muted'}">${covDiff > 0 ? '+' : ''}${covDiff}% vs fin.</small>` : '';

    return [
      rolesHtml,
      `<strong>${c.panel_count} panels</strong> (${nf(c.kwp, 2)} kWp)<br><small class="muted">${formatBatteryComposition(c, { short: true })}</small>`,
      `${c.annual_clipped_kwh_mean !== undefined ? nf(c.annual_clipped_kwh_mean, 1) : (c.annual_clipped_kwh_median != null ? nf(c.annual_clipped_kwh_median, 1) : '—')} kWh/yr<br><small class="muted">${c.annual_clipped_pct_mean !== undefined ? nf(c.annual_clipped_pct_mean, 2) : (c.annual_clipped_pct_median != null ? nf(c.annual_clipped_pct_median, 2) : '—')}% of PV input</small>`,
      `<strong>${fmt(c.capex_eur, 'eur')}</strong>`,
      extraCapexHtml,
      `<strong>${fmt(c.npv_mean !== undefined ? c.npv_mean : c.npv_median, 'eur')}</strong><br><small class="muted">${fmt(c.npv_min, 'eur')} – ${fmt(c.npv_max, 'eur')}</small>`,
      npvDiffHtml,
      `<strong>${fmt(c.autarky_mean !== undefined ? c.autarky_mean : c.autarky_median, 'pct')}</strong>${covDiffHtml}`,
      `${c.annual_grid_import_kwh_mean !== undefined ? nf(c.annual_grid_import_kwh_mean) : (c.annual_grid_import_kwh_median != null ? nf(c.annual_grid_import_kwh_median) : '—')} kWh/yr`,
      (c.annual_grid_export_kwh_mean !== undefined ? c.annual_grid_export_kwh_mean : c.annual_grid_export_kwh_median) != null ? `${nf(c.annual_grid_export_kwh_mean !== undefined ? c.annual_grid_export_kwh_mean : c.annual_grid_export_kwh_median)} kWh/yr` : '—',
      formatPaybackYearsValue(c.discounted_payback_of_mean_cash_flows !== undefined ? c.discounted_payback_of_mean_cash_flows : c.discounted_payback_years),
      `<button type="button" class="solar-choice-apply action-btn quiet" data-alt-idx="${idx}" style="padding:4px 8px;font-size:11px;">Apply</button>`
    ];
  });

  let html = '<table><thead><tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>';
  rows.forEach(row => {
    html += '<tr>' + row.map(cell => `<td>${cell}</td>`).join('') + '</tr>';
  });
  html += '</tbody></table>';

  target.innerHTML = html;

  const wrap = $('solarResultsWrap');
  const isStale = (wrap && wrap.classList.contains('stale')) || solarInFlight || (solarAcceptedFingerprint !== solarStateFingerprint()) || (typeof shouldBlockSolarSizingForAcDraft === 'function' && shouldBlockSolarSizingForAcDraft());

  if (typeof target.querySelectorAll === 'function') {
    target.querySelectorAll('button[data-alt-idx]').forEach(btn => {
      const idx = parseInt(btn.getAttribute('data-alt-idx'), 10);
      const cand = alts[idx];
      if (!cand || isStale) {
        btn.disabled = true;
      } else {
        btn.disabled = false;
      }
      btn.addEventListener('click', () => {
        if (cand && !btn.disabled) {
          applySolarCandidate(cand, cand.role_label || 'alternative configuration');
        }
      });
    });
  }
}

function renderSolarSizing(data) {
  solarData = data;
  if (typeof hideChartTooltip === 'function') hideChartTooltip();
  const rec = getMainRecommendation(data);
  const summary = data.search_summary || {};
  const horizon = data.settings?.horizon_years || settings.horizon_years || 20;
  const pw = data.settings?.panel_watts || settings.panel_watts || 440;
  const finBasis = data.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  if (!rec) {
    if ($('solarStatus')) {
      $('solarStatus').textContent = 'No feasible solar installation configurations found with current constraints and roof limits.';
    }
    if ($('solarRecommendedBanner')) {
      $('solarRecommendedBanner').innerHTML = '<span class="pill bad">No feasible configuration found</span>';
    }
    if ($('solarRecTitle')) {
      $('solarRecTitle').textContent = 'No Feasible Configuration';
    }
    if ($('solarRecCards')) {
      $('solarRecCards').innerHTML = '<p class="muted">All evaluated panel and storage combinations were outside physical limits or violated active constraints.</p>';
    }
    if ($('solarRoofPills')) $('solarRoofPills').innerHTML = '';
    if ($('solarPricingNote')) $('solarPricingNote').textContent = '';
    if ($('solarSizeCurveTitle')) $('solarSizeCurveTitle').textContent = `Solar size curve · ${isEquityBasis ? 'Equity' : 'Project'} NPV & energy metrics by panel count`;
    if ($('applySolarSystem')) $('applySolarSystem').disabled = true;
    if ($('solarApplyStatus')) $('solarApplyStatus').textContent = 'No feasible recommendation to apply.';
    renderSolarRecommendationChoices(data);
    renderSolarAlternativesTable(data);
    renderSolarComparisonsTable(data);
    renderSolarSizeCurveChart(data);
    renderSolarDiminishingChart(data);
    renderSolarTradeoffHeatmap(data);
    renderSolarSearchSummary(data);
    return;
  }

  const fin = data.recommendation_choices?.financial;
  if ($('solarSizeCurveTitle')) {
    const recNpvVal = rec.npv_mean !== undefined ? rec.npv_mean : rec.npv_median;
    const finNpvVal = fin ? (fin.npv_mean !== undefined ? fin.npv_mean : fin.npv_median) : null;
    const recAutVal = rec.autarky_mean !== undefined ? rec.autarky_mean : rec.autarky_median;
    if (fin && recNpvVal < (finNpvVal - 5)) {
      $('solarSizeCurveTitle').textContent = `Solar size curve · Balanced choice: ${rec.panel_count} panels (${nf(rec.kwp, 1)} kWp, ${fmt(recAutVal, 'pct')} autarky) with ${fmt(recNpvVal, 'eur')} ${isEquityBasis ? 'equity' : 'project'} NPV (highest financial return is ${fin.panel_count} panels with ${fmt(finNpvVal, 'eur')} peak NPV)`;
    } else {
      $('solarSizeCurveTitle').textContent = `Solar size curve · ${rec.panel_count} panels (${nf(rec.kwp, 1)} kWp) delivers peak mean ${isEquityBasis ? 'equity' : 'project'} NPV of ${fmt(recNpvVal, 'eur')} with ${fmt(recAutVal, 'pct')} autarky`;
    }
  }

  if ($('solarStatus')) {
    const statusEl = $('solarStatus');
    const sec = data.compute_seconds != null ? ` in ${data.compute_seconds.toFixed(2)}s` : '';
    const objDesc = summary.objective === 'npv' || summary.objective === 'npv_eur'
      ? 'NPV maximization (balances investment against bill savings and midday export value)'
      : summary.objective === 'autarky'
        ? 'Autarky maximization (maximizes self-sufficiency without capital cost penalty)'
        : summary.objective === 'self_consumption'
          ? 'Self-consumption maximization'
          : (summary.objective_label || summary.objective);
    const line1Text = `Evaluated ${summary.evaluated_count} candidate layouts across ${summary.weather_count} weather analogues (${summary.exact ? 'Exhaustive certified optimum' : 'Bounded best-found'})${sec}`;
    const line2Text = `Objective: ${objDesc} · Scenario: ${dayhourScenarioLabel(summary.effective_roof_limits ? rec.settings?.load_scenario : 'electrified')}`;

    if (typeof document !== 'undefined' && typeof document.createElement === 'function' && typeof statusEl.appendChild === 'function') {
      statusEl.textContent = '';
      const line1 = document.createElement('span');
      line1.className = 'solar-status-line';
      line1.textContent = line1Text;
      const line2 = document.createElement('span');
      line2.className = 'solar-status-line';
      line2.textContent = line2Text;
      statusEl.appendChild(line1);
      statusEl.appendChild(line2);
    } else {
      statusEl.textContent = `${line1Text}\n${line2Text}`;
    }
  }

  if ($('solarRecommendedBanner')) {
    $('solarRecommendedBanner').innerHTML = `
      <span class="pill active">Main recommendation: <strong>${rec.panel_count} panels (${nf(rec.kwp, 2)} kWp)</strong></span>
      <span class="pill">Storage: <strong>${formatBatteryComposition(rec, { short: true })}</strong></span>
      <span class="pill">Estimated Capex: <strong>${fmt(rec.capex_eur, 'eur')}</strong></span>
      <span class="pill">Discounted NPV: <strong>${fmt(rec.npv_mean !== undefined ? rec.npv_mean : rec.npv_median, 'eur')}</strong> (${fmt(rec.npv_min, 'eur')} – ${fmt(rec.npv_max, 'eur')})</span>
      ${(rec.npv_mean !== undefined ? rec.npv_mean : rec.npv_median) < 0 ? '<span class="pill warn">Negative constrained NPV (refer to No-install baseline)</span>' : ''}
      <span class="pill">Household coverage: <strong>${fmt(rec.autarky_mean !== undefined ? rec.autarky_mean : rec.autarky_median, 'pct')}</strong></span>
      <span class="pill">Inverter: <strong>${nf(summary.inverter_kw, 2)} kW AC (Fixed)</strong></span>
      <span class="pill ${rec.reached_pv_ceiling || rec.reached_inverter_ceiling ? 'warn' : 'good'}">${rec.reached_pv_ceiling || rec.reached_inverter_ceiling ? 'At PV array limit' : (rec.reached_roof_bound ? 'At roof limit' : 'Within PV array limit')}</span>
    `;
  }

  if ($('solarSourceVsScenario') && data.cost_assumptions) {
    $('solarSourceVsScenario').innerHTML = table(
      ['Parameter / Assumption', 'Value', 'Source & Provenance', 'Status'],
      data.cost_assumptions.map(r => [r.item, r.value, r.source, r.status])
    );
  }

  // Render 3 recommendation choices cards
  renderSolarRecommendationChoices(data);

  // Recommended Card (Detailed view of Main / Balanced)
  if ($('solarRecTitle')) {
    const isBal = data.recommendation_choices?.balanced && rec === data.recommendation_choices.balanced;
    $('solarRecTitle').textContent = `${rec.panel_count} Panels (${nf(rec.kwp, 2)} kWp) · ${nf(rec.battery_kwh, 1)} kWh Storage${isBal ? ' · Balanced Choice (Main)' : ''}`;
  }
  if ($('solarRecCards')) {
    $('solarRecCards').innerHTML = `
      <div class="card"><div class="label">Total Solar Array · ${rec.settings?.pv_battery_coupling==='dc_coupled'?'DC coupled':'AC coupled'}</div><div class="value">${rec.panel_count} <small>panels</small></div><small>${nf(rec.kwp, 2)} kWp @ ${pw}W</small></div>
      <div class="card"><div class="label">Paired Battery</div><div class="value">${nf(rec.battery_kwh, 1)} <small>kWh</small></div><small>${formatBatteryComposition(rec)}</small></div>
      <div class="card"><div class="label">Installed Capex</div><div class="value">${fmt(rec.capex_eur, 'eur')}</div><small>Modeled benchmark cost</small></div>
      <div class="card"><div class="label">${horizon}-Yr ${isEquityBasis ? 'Equity' : 'Project'} NPV</div><div class="value">${fmt(rec.npv_mean !== undefined ? rec.npv_mean : rec.npv_median, 'eur')}</div><small>Range: ${fmt(rec.npv_min, 'eur')} – ${fmt(rec.npv_max, 'eur')}</small></div>
      <div class="card"><div class="label">Discounted Payback</div><div class="value">${formatPaybackYearsValue(rec.discounted_payback_of_mean_cash_flows !== undefined ? rec.discounted_payback_of_mean_cash_flows : rec.discounted_payback_years, ' <small>years</small>')}</div><small>Payback of ensemble mean cash flows · Simple: ${rec.simple_payback_years != null ? `${nf(rec.simple_payback_years, 1)} y` : '—'}</small></div>
      <div class="card"><div class="label">Household Demand Coverage</div><div class="value">${fmt(rec.autarky_mean !== undefined ? rec.autarky_mean : rec.autarky_median, 'pct')}</div><small>Share of household demand met by solar/storage</small></div>
      <div class="card"><div class="label">Self-consumption</div><div class="value">${fmt(rec.self_consumption_mean !== undefined ? rec.self_consumption_mean : rec.self_consumption_median, 'pct')}</div><small>Solar used locally</small></div>
      <div class="card"><div class="label">Annual Generation</div><div class="value">${nf(rec.annual_pv_kwh_mean !== undefined ? rec.annual_pv_kwh_mean : rec.annual_pv_kwh_median)} <small>kWh/yr</small></div><small>${getWeatherEnsembleMeta(data?.summary || data || current?.result).count ? getWeatherEnsembleMeta(data?.summary || data || current?.result).count + '-yr' : 'historical'} ${rec.annual_pv_kwh_mean !== undefined ? 'mean' : 'median'} production</small></div>
      <div class="card"><div class="label">Grid Imports</div><div class="value">${nf(rec.annual_grid_import_kwh_mean !== undefined ? rec.annual_grid_import_kwh_mean : rec.annual_grid_import_kwh_median)} <small>kWh/yr</small></div><small>Residual annual demand</small></div>
      ${rec.detail?.export_price_analysis?.export_weighted_captured_price_eur_kwh != null ? `
        <div class="card"><div class="label">Captured Export Price</div><div class="value">${fmt(rec.detail.export_price_analysis.export_weighted_captured_price_eur_kwh, 'eur2')} <small>/ kWh</small></div><small>Capture factor: ${fmt(rec.detail.export_price_analysis.export_weighted_capture_factor, 'pct')}</small></div>
      ` : ''}
    `;
  }

  if ($('solarRoofPills')) {
    if (typeof renderRoofPills === 'function') {
      renderRoofPills();
    } else {
      const facets = (typeof activeResultProfile!=='undefined'&&activeResultProfile)?activeResultProfile.roofs.map(r=>[esc(r.label),settings.roof_panels?.[r.id]||0]):[
        ['South', settings?.south_panels ?? 0],
        ['West', settings?.west_panels ?? 0],
        ['East', settings?.east_panels ?? 0],
        ['Dormer', settings?.dormer_panels ?? 0],
        ['Flat', settings?.flat_panels ?? 0],
        ['Secondary South', settings?.secondary_south_panels ?? 0]
      ].filter(([_, count]) => Number(count) > 0);
      $('solarRoofPills').innerHTML = facets.map(([label, count]) => `<span>${label}: <strong>${count} panels</strong> (${nf(count * (settings?.panel_watts || 400) / 1000, 2)} kWp)</span>`).join('');
    }
  }

  if ($('solarPricingNote')) {
    const bNote = rec.bounds_note ? `${rec.bounds_note} ` : '';
    $('solarPricingNote').textContent = `${bNote}${rec.inverter_status || 'Inverter held fixed'}. Alternate configurations use transparent marginal benchmark pricing calibrated to research/quote data rather than scaling turnkey lump sums indiscriminately.`;
  }

  // Top alternatives table
  renderSolarAlternativesTable(data);

  // Comparisons table
  renderSolarComparisonsTable(data);

  // Chart 1: Size curve
  renderSolarSizeCurveChart(data);

  // Chart 2: Diminishing returns
  renderSolarDiminishingChart(data);

  // Chart 3: Heatmap
  renderSolarTradeoffHeatmap(data);

  // Search summary & limitations
  renderSolarSearchSummary(data);
}

function renderSolarComparisonsTable(data) {
  const target = $('solarComparisonsTable');
  if (!target || !data.comparisons) return;
  const finBasis = data.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');
  const headers = [
    'Configuration', 'Panels', 'System kWp', 'Storage',
    'Capex (€)', `${isEquityBasis ? 'Equity' : 'Project'} NPV (mean)`, 'Weather min–max',
    'Discounted payback', 'Autarky', 'Self-consumption', 'Annual PV', 'Grid imports'
  ];
  const rows = data.comparisons.map(c => {
    let cfgLabel = c.id === 'recommended' ? `★ ${c.label}` : c.label;
    if (c.exceeds_pv_ceiling || c.exceeds_inverter_ceiling) {
      cfgLabel += ` <span class="tag warn" title="${esc(c.inverter_ceiling_note || 'Exceeds selected PV array ceiling')}">Exceeds PV array ceiling</span>`;
    }
    return [
      cfgLabel,
      `${c.panel_count} panels`,
      `${nf(c.kwp, 2)} kWp`,
      formatBatteryComposition(c, { short: true }),
      fmt(c.capex_eur, 'eur'),
      fmt(c.npv_mean !== undefined ? c.npv_mean : c.npv_median, 'eur'),
      `${fmt(c.npv_min, 'eur')} – ${fmt(c.npv_max, 'eur')}`,
      formatPaybackYearsValue(c.discounted_payback_of_mean_cash_flows !== undefined ? c.discounted_payback_of_mean_cash_flows : c.discounted_payback_years),
      fmt(c.autarky_mean !== undefined ? c.autarky_mean : c.autarky_median, 'pct'),
      fmt(c.self_consumption_mean !== undefined ? c.self_consumption_mean : c.self_consumption_median, 'pct'),
      `${nf(c.annual_pv_kwh_mean !== undefined ? c.annual_pv_kwh_mean : c.annual_pv_kwh_median)} kWh`,
      `${nf(c.annual_grid_import_kwh_mean !== undefined ? c.annual_grid_import_kwh_mean : c.annual_grid_import_kwh_median)} kWh`
    ];
  });
  target.innerHTML = table(headers, rows);
}

function candMatches(c1, c2) {
  if (!c1 || !c2) return false;
  if (c1.panel_count !== c2.panel_count) return false;
  if (Math.abs(Number(c1.battery_kwh || 0) - Number(c2.battery_kwh || 0)) > 1e-4) return false;
  if (Number(c1.battery_modules ?? c1.settings?.battery_modules ?? 0) !== Number(c2.battery_modules ?? c2.settings?.battery_modules ?? 0)) return false;
  if (Number(c1.battery_secondary_modules ?? c1.settings?.battery_secondary_modules ?? 0) !== Number(c2.battery_secondary_modules ?? c2.settings?.battery_secondary_modules ?? 0)) return false;
  const r1 = c1.roof_counts || {};
  const r2 = c2.roof_counts || {};
  const keys = new Set([...Object.keys(r1), ...Object.keys(r2)]);
  for (const k of keys) {
    if ((Number(r1[k]) || 0) !== (Number(r2[k]) || 0)) return false;
  }
  return true;
}

function renderSolarSizeCurveChart(data) {
  const target = $('solarChart1');
  if (!target) return;
  const curve = data.size_curve;
  if (!curve || !curve.length) {
    target.innerHTML = '<p class="muted">No size curve data available.</p>';
    return;
  }
  const summary = data.search_summary || {};
  const finBasis = data.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');
  const width = 720, height = 300, left = 65, right = 655, top = 25, bottom = 240;
  const plotW = right - left, plotH = bottom - top;

  const counts = curve.map(c => c.panel_count);
  const minPanels = Math.min(...counts);
  const maxPanels = Math.max(...counts);

  const allNpv = curve.flatMap(c => [c.npv_min, c.npv_mean !== undefined ? c.npv_mean : c.npv_median, c.npv_max, 0]);
  const minNpv = Math.min(...allNpv);
  const maxNpv = Math.max(...allNpv, 500);
  const spanNpv = maxNpv - minNpv || 1;

  const x = p => left + ((p - minPanels) / Math.max(1, maxPanels - minPanels)) * plotW;
  const yNpv = v => bottom - ((v - minNpv) / spanNpv) * plotH;
  const yPct = p => bottom - p * plotH;
  const yZero = yNpv(0);

  let body = '';
  // Horizontal grid lines for NPV
  const gridSteps = 4;
  for (let i = 0; i <= gridSteps; i++) {
    const val = minNpv + (spanNpv * i) / gridSteps;
    const yy = yNpv(val);
    body += `<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/>`;
    body += `<text x="${left - 8}" y="${yy + 4}" text-anchor="end" font-size="10" fill="#62756e">€${nf(val)}</text>`;
    const pct = i / gridSteps;
    const yyp = yPct(pct);
    body += `<text x="${right + 8}" y="${yyp + 4}" text-anchor="start" font-size="10" fill="#6285bd">${Math.round(pct * 100)}%</text>`;
  }
  body += `<text x="${left - 8}" y="${top - 8}" text-anchor="end" font-size="9" fill="#62756e">Mean ${isEquityBasis ? 'Equity' : 'Project'} NPV (€)</text>`;
  body += `<text x="${right + 8}" y="${top - 8}" text-anchor="start" font-size="9" fill="#6285bd">Autarky / SC</text>`;

  if (yZero >= top && yZero <= bottom) {
    body += `<line x1="${left}" x2="${right}" y1="${yZero}" y2="${yZero}" stroke="#62756e" stroke-width="1.2" stroke-dasharray="3 3"/>`;
  }

  // Shaded envelope for NPV min-max
  const upper = curve.map(c => `${x(c.panel_count)},${yNpv(c.npv_max)}`).join(' ');
  const lower = [...curve].reverse().map(c => `${x(c.panel_count)},${yNpv(c.npv_min)}`).join(' ');
  body += `<polygon points="${upper} ${lower}" fill="#217a61" fill-opacity="0.12"/>`;

  // Polyline for NPV mean
  body += `<polyline points="${curve.map(c => `${x(c.panel_count)},${yNpv(c.npv_mean !== undefined ? c.npv_mean : c.npv_median)}`).join(' ')}" fill="none" stroke="#217a61" stroke-width="3"/>`;
  // Polyline for Autarky
  body += `<polyline points="${curve.map(c => `${x(c.panel_count)},${yPct(c.autarky_mean !== undefined ? c.autarky_mean : c.autarky_median)}`).join(' ')}" fill="none" stroke="#6285bd" stroke-width="2" stroke-dasharray="4 2"/>`;
  // Polyline for Self-consumption
  body += `<polyline points="${curve.map(c => `${x(c.panel_count)},${yPct(c.self_consumption_mean !== undefined ? c.self_consumption_mean : c.self_consumption_median)}`).join(' ')}" fill="none" stroke="#bd8334" stroke-width="2"/>`;

  // Draw points & recommended markers
  const mainRec = getMainRecommendation(data);
  const finRec = data.recommendation_choices?.financial || null;
  const isBalActive = Boolean(data.recommendation_choices?.balanced);

  let mainFoundInCurve = false;
  let finFoundInCurve = false;

  curve.forEach(c => {
    const cx = x(c.panel_count);
    const cy = yNpv(c.npv_mean !== undefined ? c.npv_mean : c.npv_median);
    const isExact = summary.exact === true;

    const isMain = candMatches(c, mainRec);
    const isFin = candMatches(c, finRec);
    if (isMain) mainFoundInCurve = true;
    if (isFin) finFoundInCurve = true;

    let optLabel = '';
    if (isMain && isFin) {
      optLabel = isBalActive
        ? (isExact ? '\n★ Main recommendation (Balanced choice & Financial winner - certified optimum)' : '\n★ Main recommendation (Balanced choice & Financial winner)')
        : (isExact ? '\n◆ Best financial return (Financial reference fallback - certified optimum)' : '\n◆ Best financial return (Financial reference fallback)');
    } else if (isMain) {
      optLabel = isBalActive ? '\n★ Main recommendation (Balanced choice)' : '\n◆ Best financial return (Financial reference fallback)';
    } else if (isFin) {
      optLabel = '\n◆ Best financial return (Financial reference)';
    } else if (c.is_recommended) {
      optLabel = isExact ? '\n★ Objective winner (certified global optimum)' : '\n★ Objective winner (best-found among evaluated)';
    }

    const cPb = c.discounted_payback_of_mean_cash_flows !== undefined ? c.discounted_payback_of_mean_cash_flows : c.discounted_payback_years;
    const cAut = c.autarky_mean !== undefined ? c.autarky_mean : c.autarky_median;
    const cClipKwh = c.annual_clipped_kwh_mean !== undefined ? c.annual_clipped_kwh_mean : c.annual_clipped_kwh_median;
    const cClipPct = c.annual_clipped_pct_mean !== undefined ? c.annual_clipped_pct_mean : c.annual_clipped_pct_median;
    const cSc = c.self_consumption_mean !== undefined ? c.self_consumption_mean : c.self_consumption_median;
    const detail = `Panels: ${c.panel_count} (${c.kwp} kWp)\nPaired storage: ${nf(c.battery_kwh, 1)} kWh (${c.battery_modules} mod)\nCapex: €${nf(c.capex_eur)}\nMean NPV: €${nf(c.npv_mean !== undefined ? c.npv_mean : c.npv_median)} (weather: €${nf(c.npv_min)} – €${nf(c.npv_max)})\nHousehold demand coverage: ${fmt(cAut, 'pct')}\nPV curtailed: ${cClipKwh != null ? `${nf(cClipKwh, 1)} kWh/yr (${nf(cClipPct, 2)}% of input)` : '—'}\nSelf-consumption: ${fmt(cSc, 'pct')}\nPayback of ensemble mean: ${formatPaybackYearsValue(cPb)}${optLabel}`;

    if (isMain) {
      body += `<line x1="${cx}" x2="${cx}" y1="${top}" y2="${bottom}" stroke="#183b33" stroke-width="1.5" stroke-dasharray="4 3"/>`;
      body += `<circle cx="${cx}" cy="${cy}" r="9" fill="none" stroke="#183b33" stroke-width="2.5"/>`;
    } else if (isFin) {
      body += `<circle cx="${cx}" cy="${cy}" r="8" fill="none" stroke="#bd8334" stroke-width="2" stroke-dasharray="2 2"/>`;
    }
    body += `<g ${chartTarget(detail)}>`;
    body += `<circle cx="${cx}" cy="${cy}" r="${isMain ? 5 : (isFin ? 4.5 : 3.5)}" fill="${isMain ? '#183b33' : (isFin ? '#bd8334' : '#217a61')}"/>`;
    body += `<circle cx="${cx}" cy="${yPct(c.autarky_mean !== undefined ? c.autarky_mean : c.autarky_median)}" r="2.5" fill="#6285bd"/>`;
    body += `<circle cx="${cx}" cy="${yPct(c.self_consumption_mean !== undefined ? c.self_consumption_mean : c.self_consumption_median)}" r="2.5" fill="#bd8334"/>`;
    body += `<rect class="chart-hit" x="${cx - 6}" y="${top}" width="12" height="${plotH}"/></g>`;
  });

  // If main recommendation is not on curve (e.g. curve had different storage at this panel count), overlay it at its actual metrics
  if (mainRec && !mainFoundInCurve && mainRec.panel_count != null && curve.length > 0) {
    const minP = curve[0].panel_count;
    const maxP = curve[curve.length - 1].panel_count;
    if (mainRec.panel_count >= minP && mainRec.panel_count <= maxP) {
      const cx = x(mainRec.panel_count);
      const cy = yNpv(mainRec.npv_mean !== undefined ? mainRec.npv_mean : mainRec.npv_median);
      const optLabel = isBalActive ? '\n★ Main recommendation (Balanced choice - distinct storage pair)' : '\n◆ Best financial return (Financial fallback - distinct storage pair)';
      const mainPb = mainRec.discounted_payback_of_mean_cash_flows !== undefined ? mainRec.discounted_payback_of_mean_cash_flows : mainRec.discounted_payback_years;
      const mainAut = mainRec.autarky_mean !== undefined ? mainRec.autarky_mean : mainRec.autarky_median;
      const detail = `[Main Recommendation]\nPanels: ${mainRec.panel_count} (${mainRec.kwp} kWp)\nStorage: ${formatBatteryComposition(mainRec, { short: true })}\nCapex: €${nf(mainRec.capex_eur)}\nMean NPV: €${nf(mainRec.npv_mean !== undefined ? mainRec.npv_mean : mainRec.npv_median)}\nHousehold demand coverage: ${fmt(mainAut, 'pct')}\nPayback of ensemble mean: ${formatPaybackYearsValue(mainPb)}${optLabel}`;

      body += `<line x1="${cx}" x2="${cx}" y1="${top}" y2="${bottom}" stroke="#183b33" stroke-width="1.5" stroke-dasharray="4 3"/>`;
      body += `<circle cx="${cx}" cy="${cy}" r="9" fill="none" stroke="#183b33" stroke-width="2.5"/>`;
      body += `<g ${chartTarget(detail)}>`;
      body += `<circle cx="${cx}" cy="${cy}" r="5" fill="#183b33"/>`;
      body += `<circle cx="${cx}" cy="${yPct(mainAut)}" r="2.5" fill="#6285bd"/>`;
      body += `<rect class="chart-hit" x="${cx - 6}" y="${top}" width="12" height="${plotH}"/></g>`;
    }
  }

  // X-axis ticks
  curve.forEach((c, idx) => {
    if (idx % 2 === 0 || idx === curve.length - 1 || c.is_recommended) {
      body += `<text x="${x(c.panel_count)}" y="${bottom + 16}" text-anchor="middle" font-size="10" fill="#62756e">${c.panel_count}p</text>`;
    }
  });
  body += `<text x="${(left + right) / 2}" y="${bottom + 32}" text-anchor="middle" font-size="11" fill="#62756e">Evaluated panel count (total integer panels)</text>`;

  target.innerHTML = chartSvg(`Solar array panel count size curve showing ${isEquityBasis ? 'equity' : 'project'} NPV and autarky across ${getWeatherEnsembleMeta(data?.summary || data || current?.result).count ? getWeatherEnsembleMeta(data?.summary || data || current?.result).count + ' ' : ''}weather years`, body, height + 15, width);

  if ($('solarTable1')) {
    $('solarTable1').innerHTML = table(
      ['Panels', 'kWp', 'Battery', 'Capex (€)', `${isEquityBasis ? 'Equity' : 'Project'} NPV (mean)`, 'Weather min–max', 'Discounted payback', 'Household coverage', 'PV curtailed (kWh/yr; %)', 'Self-consumption'],
      curve.map(c => {
        const isMain = candMatches(c, mainRec);
        const isFin = candMatches(c, finRec);
        let star = '';
        if (isMain && isFin) star = isBalActive ? ' ★ (Main & Fin)' : ' ◆ (Fin)';
        else if (isMain) star = isBalActive ? ' ★ (Main)' : ' ◆ (Fin)';
        else if (isFin) star = ' ◆ (Fin)';
        else if (c.is_recommended) star = ' ★';
        return [
          `${c.panel_count} panels${star}`,
          `${nf(c.kwp, 2)} kWp`,
          formatBatteryComposition(c, { short: true }),
          fmt(c.capex_eur, 'eur'),
          fmt(c.npv_mean !== undefined ? c.npv_mean : c.npv_median, 'eur'),
          `${fmt(c.npv_min, 'eur')} – ${fmt(c.npv_max, 'eur')}`,
          formatPaybackYearsValue(c.discounted_payback_of_mean_cash_flows !== undefined ? c.discounted_payback_of_mean_cash_flows : c.discounted_payback_years),
          fmt(c.autarky_mean !== undefined ? c.autarky_mean : c.autarky_median, 'pct'),
          `${c.annual_clipped_kwh_mean !== undefined ? nf(c.annual_clipped_kwh_mean, 1) : (c.annual_clipped_kwh_median != null ? nf(c.annual_clipped_kwh_median, 1) : '—')}; ${c.annual_clipped_pct_mean !== undefined ? nf(c.annual_clipped_pct_mean, 2) : (c.annual_clipped_pct_median != null ? nf(c.annual_clipped_pct_median, 2) : '—')}%`,
          fmt(c.self_consumption_mean !== undefined ? c.self_consumption_mean : c.self_consumption_median, 'pct')
        ];
      })
    );
  }
}

function renderSolarDiminishingChart(data) {
  const target = $('solarChart2');
  if (!target) return;
  const curve = data.size_curve || [];
  const steps = curve.filter(c => c.marginal_npv_per_panel != null);
  if (!steps.length) {
    target.innerHTML = '<p class="muted">Need at least 2 evaluated sizes to compute marginal incremental returns.</p>';
    if ($('solarDiminishingNotice')) $('solarDiminishingNotice').innerHTML = '';
    return;
  }
  const finBasis = data.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  const width = 720, height = 280, left = 65, right = 670, top = 25, bottom = 220;
  const plotW = right - left, plotH = bottom - top;

  const minVal = Math.min(0, ...steps.map(s => s.marginal_npv_per_panel));
  const maxVal = Math.max(10, ...steps.map(s => s.marginal_npv_per_panel)) * 1.15;
  const rangeVal = Math.max(1, maxVal - minVal);

  const y = v => bottom - ((v - minVal) / rangeVal) * plotH;
  const yZero = y(0);

  let body = '';
  // Horizontal grid lines
  const gridSteps = 4;
  for (let i = 0; i <= gridSteps; i++) {
    const val = minVal + (rangeVal * i) / gridSteps;
    const yy = y(val);
    body += `<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" stroke="#e0e7df"/>`;
    body += `<text x="${left - 8}" y="${yy + 4}" text-anchor="end" font-size="10" fill="#62756e">€${nf(val, 1)}</text>`;
  }
  body += `<line x1="${left}" x2="${right}" y1="${yZero}" y2="${yZero}" stroke="#62756e" stroke-width="1.2"/>`;
  body += `<text x="${left - 8}" y="${top - 8}" text-anchor="end" font-size="9" fill="#62756e">ΔNPV / panel (€)</text>`;

  const barSlot = plotW / steps.length;
  const barW = Math.max(4, Math.min(28, barSlot * 0.7));

  steps.forEach((s, idx) => {
    const cx = left + (idx + 0.5) * barSlot;
    const val = s.marginal_npv_per_panel;
    const barY = val >= 0 ? y(val) : yZero;
    const barH = Math.max(1, Math.abs(y(val) - yZero));
    const isPos = val >= 0;
    const detail = `Step to ${s.panel_count} panels (${s.kwp} kWp)\nFinite difference: ΔNPV = €${nf(val, 2)} / added panel${s.delta_battery_kwh ? ` (ΔBattery: ${s.delta_battery_kwh > 0 ? '+' : ''}${nf(s.delta_battery_kwh, 1)} kWh)` : ''}\nIncremental NPV per kWp: €${nf(s.marginal_npv_per_kwp, 2)} / kWp\nTotal mean ${isEquityBasis ? 'equity' : 'project'} NPV: €${nf(s.npv_mean !== undefined ? s.npv_mean : s.npv_median)}\nNote: Reflects finite differences between re-optimized discrete layouts; does not establish monotonic causality.`;

    body += `<g ${chartTarget(detail)}>`;
    body += `<rect x="${cx - barW / 2}" y="${barY}" width="${barW}" height="${barH}" fill="${isPos ? '#217a61' : '#b94c37'}" opacity="0.85" rx="2"/>`;
    body += `<rect class="chart-hit" x="${cx - barSlot / 2}" y="${top}" width="${barSlot}" height="${plotH}"/>`;
    body += `</g>`;

    if (idx % 2 === 0 || idx === steps.length - 1 || s.is_recommended) {
      body += `<text x="${cx}" y="${bottom + 16}" text-anchor="middle" font-size="10" fill="#62756e">${s.panel_count}p</text>`;
    }
  });
  body += `<text x="${(left + right) / 2}" y="${bottom + 32}" text-anchor="middle" font-size="11" fill="#62756e">Panel count step</text>`;

  target.innerHTML = chartSvg('Marginal economic return per added panel across evaluated solar sizes', body, height + 15, width);

  // Diminishing notice
  const negSteps = steps.filter(s => s.marginal_npv_per_panel < 0);
  const recPoint = curve.find(p => p.is_recommended);
  if ($('solarDiminishingNotice')) {
    if (negSteps.length > 0) {
      const firstNeg = negSteps[0];
      $('solarDiminishingNotice').innerHTML = `<strong>Finite differences between evaluated sizes:</strong> At ${firstNeg.panel_count} panels, the evaluated configuration has lower ${isEquityBasis ? 'equity' : 'project'} NPV than the preceding step (ΔNPV = €${nf(firstNeg.marginal_npv_per_panel, 1)}/panel${firstNeg.delta_battery_kwh ? `, paired storage change: ${firstNeg.delta_battery_kwh > 0 ? '+' : ''}${nf(firstNeg.delta_battery_kwh, 1)} kWh` : ''}). Each step represents an independently selected discrete layout and battery pairing; differences reflect discrete re-optimization rather than monotonic causal loss. Best-scoring system: <strong>${recPoint ? recPoint.panel_count : 0} panels (${recPoint ? recPoint.kwp : 0} kWp)</strong>.`;
    } else {
      $('solarDiminishingNotice').innerHTML = `<strong>Finite differences across evaluated range:</strong> Evaluated layouts show positive incremental NPV across tested sizes. Best-scoring system: <strong>${recPoint ? recPoint.panel_count : 0} panels (${recPoint ? recPoint.kwp : 0} kWp, ${recPoint ? nf(recPoint.battery_kwh, 1) : 0} kWh)</strong>.`;
    }
  }

  if ($('solarTable2')) {
    $('solarTable2').innerHTML = table(
      ['Panel count', 'System kWp', 'Marginal NPV (€/panel)', 'Marginal NPV (€/kWp)', `Total ${isEquityBasis ? 'Equity' : 'Project'} NPV (mean)`],
      steps.map(s => [
        `${s.panel_count} panels${s.is_recommended ? ' ★' : ''}`,
        `${nf(s.kwp, 2)} kWp`,
        `€${nf(s.marginal_npv_per_panel, 2)}`,
        `€${nf(s.marginal_npv_per_kwp, 2)}`,
        fmt(s.npv_mean !== undefined ? s.npv_mean : s.npv_median, 'eur')
      ])
    );
  }
}

let solarTradeoffData = null;

function renderSolarTradeoffHeatmap(data) {
  solarTradeoffData = data;
  const target = $('solarChart3');
  if (!target) return;
  const tm = data.tradeoff_matrix;
  if (!tm || !tm.cells || !tm.cells.length) {
    const emptyMsg = '<p class="muted">No tradeoff matrix available.</p>';
    target.innerHTML = emptyMsg;
    if ($('solarHeatmapDialogContent')) {
      $('solarHeatmapDialogContent').innerHTML = emptyMsg;
    }
    return;
  }
  const pvs = tm.pv_axis_kwp;
  const bats = tm.battery_axis_kwh;
  const cells = tm.cells;
  if (!pvs || !pvs.length || !bats || !bats.length) {
    const emptyMsg = '<p class="muted">No tradeoff matrix axis data available.</p>';
    target.innerHTML = emptyMsg;
    if ($('solarHeatmapDialogContent')) {
      $('solarHeatmapDialogContent').innerHTML = emptyMsg;
    }
    return;
  }

  const rec = data.recommended_candidate;
  const mainRec = getMainRecommendation(data);
  const finRec = data.recommendation_choices?.financial || null;
  const isBalActive = Boolean(data.recommendation_choices?.balanced);
  const summary = data.search_summary || {};
  const horizon = data.settings?.horizon_years || (typeof settings !== 'undefined' ? settings.horizon_years : 20) || 20;
  const finBasis = data.options?.financial_basis || ($('solarFinancialBasis') && $('solarFinancialBasis').value) || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  // Readable dimensions: 96px column width, 44px row height, no row-height cap
  const left = 90;
  const top = 56;
  const colW = 96;
  const rowH = 44;
  const plotW = bats.length * colW;
  const plotH = pvs.length * rowH;
  const right = left + plotW;
  const bottom = top + plotH;
  const width = right + 24;
  const height = bottom + 38;

  const allNpv = cells.map(c => c.npv_mean !== undefined ? c.npv_mean : c.npv_median);
  const minNpv = Math.min(...allNpv);
  const maxNpv = Math.max(...allNpv);

  let body = '';

  // Axis title for PV rows
  body += `<text x="${left - 10}" y="${top - 14}" text-anchor="end" font-size="11" font-weight="700" fill="#183b33">PV (kWp)</text>`;

  // Column header tick labels (Battery kWh)
  bats.forEach((b, j) => {
    const cx = left + (j + 0.5) * colW;
    body += `<text x="${cx}" y="${top - 12}" text-anchor="middle" font-size="12" font-weight="600" fill="#62756e">${nf(b, 1)} kWh</text>`;
  });

  // Rows and cells
  pvs.forEach((p, i) => {
    const cy = top + (i + 0.5) * rowH;
    body += `<text x="${left - 10}" y="${cy + 4}" text-anchor="end" font-size="12" font-weight="600" fill="#62756e">${nf(p, 1)} kWp</text>`;
    bats.forEach((b, j) => {
      const cell = cells.find(c => Math.abs(c.pv_kwp - p) < 1e-4 && Math.abs(c.battery_kwh - b) < 1e-4);
      const x = left + j * colW;
      const y = top + i * rowH;

      if (!cell) {
        body += `<rect x="${x + 2}" y="${y + 2}" width="${colW - 4}" height="${rowH - 4}" rx="4" fill="#f4f6f4" stroke="#d5dad5" stroke-dasharray="3 3"/>`;
        return;
      }

      const cellVal = cell.npv_mean !== undefined ? cell.npv_mean : cell.npv_median;
      const norm = maxNpv === minNpv ? 0.5 : (cellVal - minNpv) / (maxNpv - minNpv);
      const r = Math.round(240 - norm * 215);
      const g = Math.round(245 - norm * 155);
      const bl = Math.round(240 - norm * 170);
      const bg = `rgb(${r},${g},${bl})`;
      const textFill = norm > 0.55 ? '#ffffff' : '#183b33';

      const isMain = mainRec && Math.abs(mainRec.kwp - p) < 1e-3 && Math.abs(mainRec.battery_kwh - b) < 1e-3;
      const isFin = finRec && Math.abs(finRec.kwp - p) < 1e-3 && Math.abs(finRec.battery_kwh - b) < 1e-3;
      const isExact = summary.exact === true;

      let recNote = '';
      if (isMain && isFin) {
        const mainNpvVal = mainRec.npv_mean !== undefined ? mainRec.npv_mean : mainRec.npv_median;
        const layoutNote = (cell && Math.abs(mainNpvVal - cellVal) > 1.0)
          ? ` (Coordinate match; cell shows objective winner €${nf(cellVal)}, chosen NPV €${nf(mainNpvVal)})`
          : '';
        recNote = isBalActive
          ? (isExact ? `\n★ Main recommendation (Balanced choice & Financial winner - certified optimum)${layoutNote}` : `\n★ Main recommendation (Balanced choice & Financial winner)${layoutNote}`)
          : (isExact ? `\n◆ Best financial return (Financial reference fallback - certified optimum)${layoutNote}` : `\n◆ Best financial return (Financial reference fallback)${layoutNote}`);
      } else if (isMain) {
        const mainNpvVal = mainRec.npv_mean !== undefined ? mainRec.npv_mean : mainRec.npv_median;
        const layoutNote = (cell && Math.abs(mainNpvVal - cellVal) > 1.0)
          ? ` (Coordinate match; cell shows objective winner €${nf(cellVal)}, balanced NPV €${nf(mainNpvVal)})`
          : '';
        recNote = isBalActive
          ? `\n★ Main recommendation (Balanced choice)${layoutNote}`
          : `\n◆ Best financial return (Financial reference fallback)${layoutNote}`;
      } else if (isFin) {
        const finNpvVal = finRec ? (finRec.npv_mean !== undefined ? finRec.npv_mean : finRec.npv_median) : null;
        const layoutNote = (cell && finNpvVal != null && Math.abs(finNpvVal - cellVal) > 1.0)
          ? ` (Coordinate match; cell shows objective winner €${nf(cellVal)}, financial winner NPV €${nf(finNpvVal)})`
          : '';
        recNote = `\n◆ Best financial return (NPV optimum reference)${layoutNote}`;
      } else if (rec && Math.abs(rec.kwp - p) < 1e-3 && Math.abs(rec.battery_kwh - b) < 1e-3) {
        recNote = isExact ? '\n★ Objective winner (certified global optimum)' : '\n★ Objective winner (best-found among evaluated)';
      }

      const curRes = (typeof current !== 'undefined') ? current?.result : null;
      const currentKwp = curRes?.layout?.kwp ?? (typeof settings !== 'undefined' ? settings.kwp : null);
      const currentBat = curRes?.settings?.battery_kwh ?? (typeof settings !== 'undefined' ? settings.battery_kwh : null);
      const isCurrent = currentKwp != null && currentBat != null && Math.abs(currentKwp - p) < 0.25 && Math.abs(currentBat - b) < 0.25;
      if (isCurrent) recNote += '\n● Current active system configuration';
      if (typeof routeDisplayForCandidate === 'function') recNote += `\nRouting: ${routeDisplayForCandidate(cell)}`;

      const strokeCol = isCurrent ? '#2b6cb0' : (isMain ? '#183b33' : (isFin ? '#bd8334' : '#d5dad5'));
      const strokeW = (isCurrent || isMain) ? 3 : (isFin ? 2.5 : 1);
      const detail = `PV: ${cell.pv_kwp} kWp · Battery: ${formatBatteryComposition(cell, { short: true })}\nMean Discounted ${isEquityBasis ? 'Equity' : 'Project'} NPV: €${nf(cellVal)} (weather: €${nf(cell.npv_min)} – €${nf(cell.npv_max)})\nEstimated capex: €${nf(cell.capex_eur)}\nOnly actually evaluated combinations are displayed.${recNote}`;

      body += `<g ${chartTarget(detail)}>`;
      body += `<rect x="${x + 2}" y="${y + 2}" width="${colW - 4}" height="${rowH - 4}" fill="${bg}" rx="4" stroke="${strokeCol}" stroke-width="${strokeW}"/>`;
      if (isMain || isFin) {
        body += `<text x="${x + colW - 10}" y="${y + 13}" font-size="11" fill="#e6b65b" font-weight="bold">★</text>`;
      }
      if (isCurrent) {
        body += `<circle cx="${x + 10}" cy="${y + 10}" r="3.5" fill="#2b6cb0"/>`;
      }
      body += `<text x="${x + colW / 2}" y="${y + rowH / 2 + 5}" text-anchor="middle" font-size="13" font-weight="600" fill="${textFill}">€${(cellVal / 1000).toFixed(1)}k</text>`;
      body += `</g>`;
    });
  });

  const curRes = (typeof current !== 'undefined') ? current?.result : null;
  const currentKwp = curRes?.layout?.kwp ?? (typeof settings !== 'undefined' ? settings.kwp : null);
  const currentBat = curRes?.settings?.battery_kwh ?? (typeof settings !== 'undefined' ? settings.battery_kwh : null);
  let outOfGridNote = '';
  if (currentKwp != null && currentBat != null) {
    const minP = Math.min(...pvs), maxP = Math.max(...pvs);
    const minB = Math.min(...bats), maxB = Math.max(...bats);
    const inGrid = pvs.some(p => Math.abs(currentKwp - p) < 0.25) && bats.some(b => Math.abs(currentBat - b) < 0.25);
    if (!inGrid) {
      const curNpv = isEquityBasis
        ? (curRes?.finance?.solar_equity?.npv_mean !== undefined ? curRes.finance.solar_equity.npv_mean : (curRes?.finance?.solar_equity?.npv?.mean ?? curRes?.finance?.solar_equity?.npv_eur?.mean ?? curRes?.metrics?.equity_npv_eur?.mean ?? null))
        : (curRes?.finance?.pv_incremental?.npv_mean !== undefined ? curRes.finance.pv_incremental.npv_mean : (curRes?.finance?.pv_incremental?.npv?.mean ?? curRes?.finance?.pv_incremental?.npv_eur?.mean ?? curRes?.metrics?.npv_eur?.mean ?? null));
      outOfGridNote = `<p class="muted" style="margin-top:8px;"><strong>Active system placement:</strong> Current configuration (${nf(currentKwp, 1)} kWp, ${nf(currentBat, 1)} kWh) lies outside the evaluated matrix range (${minP}–${maxP} kWp, ${minB}–${maxB} kWh). Current ${isEquityBasis ? 'equity' : 'project'} NPV: ${curNpv != null ? fmt(curNpv, 'eur') : '—'}.</p>`;
    }
  }

  const svgAriaLabel = `PV kWp versus battery kWh heatmap of historical-weather ensemble mean ${horizon}-year discounted ${isEquityBasis ? 'equity' : 'project'} NPV across ${getWeatherEnsembleMeta(data?.summary || data || current?.result).count ? getWeatherEnsembleMeta(data?.summary || data || current?.result).count + ' ' : ''}weather years`;
  const rawSvg = chartSvg(svgAriaLabel, body, height, width);
  const sizedSvg = /^<svg[^>]*\bwidth=/i.test(rawSvg)
    ? rawSvg
    : rawSvg.replace('<svg ', `<svg width="${width}" height="${height}" style="min-width:${width}px;width:${width}px;height:${height}px;" `);

  const metaHtml = `<div class="solar-heatmap-meta"><span class="solar-heatmap-caption">Battery storage (kWh) →</span><span class="solar-heatmap-basis">Basis: Discounted ${isEquityBasis ? 'Equity' : 'Project'} NPV (mean)</span></div>`;
  const legendHtml = `<div class="solar-heatmap-legend"><span class="heatmap-legend-item"><strong style="color:#e6b65b;">★</strong> Best candidate (Balanced / Financial)</span><span class="heatmap-legend-item"><strong style="color:#2b6cb0;">●</strong> Current system configuration</span><span class="heatmap-legend-item">Darker green = Higher discounted ${isEquityBasis ? 'equity' : 'project'} NPV</span></div>`;

  const fullHtml = metaHtml + sizedSvg + legendHtml + outOfGridNote;
  target.innerHTML = fullHtml;
  const dlgContent = $('solarHeatmapDialogContent');
  if (dlgContent) {
    dlgContent.innerHTML = fullHtml;
  }

  if ($('solarTable3')) {
    $('solarTable3').innerHTML = table(
      ['PV array (kWp)', 'Battery (kWh)', `Discounted ${isEquityBasis ? 'Equity' : 'Project'} NPV (mean)`, 'Weather min–max', 'Installed capex'],
      cells.map(c => [
        `${nf(c.pv_kwp, 1)} kWp`,
        `${nf(c.battery_kwh, 1)} kWh`,
        fmt(c.npv_mean ?? (c.npv_mean === undefined ? c.npv_median : null), 'eur'),
        `€${nf(c.npv_min)} – €${nf(c.npv_max)}`,
        fmt(c.capex_eur, 'eur')
      ])
    );
  }
}

function renderSolarSearchSummary(data) {
  const summary = data.search_summary;
  if (!summary) return;
  const rec = data.recommended_candidate;

  if ($('solarSearchSummaryCards')) {
    $('solarSearchSummaryCards').innerHTML = `
      <div class="card"><div class="label">Search Method</div><div class="value">${summary.exact ? 'Exhaustive Enumeration' : 'Bounded Sampling'}</div><small>${summary.exact ? 'All combinations tested' : 'Deterministic refinement'}</small></div>
      <div class="card"><div class="label">Optimality Guarantee</div><div class="value">${summary.exact ? 'Certified Global Optimum' : 'Best-Found Candidate'}</div><small>${summary.exact ? 'Global optimum within bounds' : 'Best discovered candidate'}</small></div>
      <div class="card"><div class="label">Candidates Evaluated</div><div class="value">${summary.evaluated_count} <small>/ ${summary.search_space_size}</small></div><small>Budget: ${summary.evaluation_budget}</small></div>
      <div class="card"><div class="label">Inverter AC Capacity</div><div class="value">${nf(summary.inverter_kw, 2)} <small>kW</small></div><small>DC coupling shares this rating across PV and battery output. AC coupling uses a separate battery inverter.</small></div>
      <div class="card"><div class="label">PV / battery coupling</div><div class="value">${summary.physical_constraints?.topology_mode==='auto'?'Optimize AC / DC':(settings.pv_battery_coupling==='dc_coupled'?'DC coupled':'AC coupled')}</div><small>ILR: AC ${nf(settings.ilr_ac_max,2)}, DC ${nf(settings.ilr_dc_max,2)}. Annual clipping caps: AC ${nf(100*settings.clipping_ac_max,1)}%, DC ${nf(100*settings.clipping_dc_max,1)}%. Battery C-rate ${nf(settings.battery_c_rate_max,2)} C.</small></div>
      <div class="card"><div class="label">Routing priority (EMS work mode)</div><div class="value">${rec?(typeof routeDisplayForCandidate==='function'?esc(routeDisplayForCandidate(rec)):'—'):(typeof routingPriorityCompact==='function'?esc(routingPriorityCompact(settings.routing_priority||'auto')):'—')}</div><small>Scheduled AM/PM searches the switch hour and active months for each system. Auto selects a physical route per candidate. Apply a recommendation to use its routing settings.</small></div>
      <div class="card"><div class="label">Maximum PV Array</div><div class="value">${nf(summary.pv_max_kwp ?? summary.inverter_kwp_ceiling, 2)} <small>kWp</small></div><small>${summary.pv_panel_ceiling ?? summary.inverter_panel_ceiling} whole panels at ${nf(settings.panel_watts,0)} W each</small></div>
      <div class="card"><div class="label">Weather Analogues</div><div class="value">${summary.weather_count} <small>years</small></div><small>PVGIS ${(Array.isArray(summary.weather_years)&&summary.weather_years.length>1)?summary.weather_years[0]+"–"+summary.weather_years[summary.weather_years.length-1]:(getWeatherEnsembleMeta().rangeLabel||"historical ensemble")}</small></div>
      <div class="card"><div class="label">Storage Sizing Mode</div><div class="value">${summary.battery_mode === 'joint' ? 'Joint PV + Storage' : 'Fixed Storage'}</div><small>${summary.battery_mode === 'joint' ? ((summary.battery_mixed_sizes || settings.battery_mixed_sizes) ? `Attainable combinations (${nf(summary.battery_module_kwh || settings.battery_module_kwh, 1)} + ${nf(summary.battery_secondary_module_kwh || settings.battery_secondary_module_kwh, 1)} kWh)` : `${nf(summary.battery_module_kwh, 2)} kWh/mod steps`) : 'Retained current capacity'}</small></div>
    `;
  }

  let noticesHtml = '';
  if (rec) {
    const totalRoofLimit = summary.effective_roof_limits ? Object.values(summary.effective_roof_limits).reduce((a, b) => a + b, 0) : 0;
    const isAtMaxPanels = totalRoofLimit > 0 && rec.panel_count >= totalRoofLimit;
    const isAtMaxBattery = summary.battery_mode === 'joint' && summary.battery_capacities_evaluated && rec.battery_kwh >= Math.max(...summary.battery_capacities_evaluated);
    const isBudgetTruncated = !summary.exact && summary.evaluated_count < summary.search_space_size;

    if (isAtMaxPanels && rec.panel_count > 0) {
      noticesHtml += `<div class="notice"><strong>Roof limit reached:</strong> The recommended layout reaches the maximum allowed roof panel limit (${rec.panel_count} panels). If additional unshaded roof area is available, increasing roof limits may reveal larger beneficial layouts.</div>`;
    }
    if (isAtMaxBattery && rec.battery_kwh > 0) {
      noticesHtml += `<div class="notice"><strong>Storage upper bound reached:</strong> The recommended storage capacity matches the maximum evaluated sweep limit (${nf(rec.battery_kwh, 1)} kWh). Expanding the storage upper bound may identify higher-capacity storage pairings.</div>`;
    }
    if (isBudgetTruncated) {
      noticesHtml += `<div class="notice"><strong>Bounded search coverage:</strong> Evaluated ${summary.evaluated_count} of ${summary.search_space_size} possible layout-battery combinations within budget. Best-found results represent the highest-scoring candidate among evaluated discrete layouts.</div>`;
    }
    if (summary.roof_yield_priority && summary.roof_production_kwh_kwp) {
      noticesHtml += `<div class="notice"><strong>Roof priority by modeled annual yield:</strong> Panel allocation priority: ${summary.roof_yield_priority.map(r => `${title(r)} (${Math.round(summary.roof_production_kwh_kwp[r] || 0)} kWh/kWp)`).join(' &gt; ')}.</div>`;
    }
    if (summary.advisory_note) {
      noticesHtml += `<div class="notice warn"><strong>Economic advisory:</strong> ${esc(summary.advisory_note)}</div>`;
    }
  }

  if ($('solarLimitationsList') && summary.limitations) {
    $('solarLimitationsList').innerHTML = noticesHtml + summary.limitations.map(lim => `<li>${esc(lim)}</li>`).join('');
  }
}

/* SOLAR SIZING SENSITIVITY (ECONOMIC STRESS TESTING) */
let solarSensitivityData = null, solarSensitivityRequestId = 0, solarSensitivityInFlight = false, solarSensitivityAcceptedFingerprint = null;

function solarSensitivityStateFingerprint(customSettings = settings, customQuote = quote, customOptions = null, customSensOptions = null) {
  const baseFp = typeof solarStateFingerprint === 'function' ? solarStateFingerprint(customSettings, customQuote, customOptions) : '';
  const expD = customSensOptions ? customSensOptions.export_revenue_delta_pct : Number($('sensExportDelta')?.value ?? 20);
  const costD = customSensOptions ? customSensOptions.installed_cost_delta_pct : Number($('sensCostDelta')?.value ?? 20);
  const consD = customSensOptions ? customSensOptions.household_consumption_delta_pct : Number($('sensConsDelta')?.value ?? 10);
  const aprD = customSensOptions ? customSensOptions.financing_apr_delta_pp : Number($('sensAprDelta')?.value ?? 2.0);
  return JSON.stringify({ baseFp, expD, costD, consD, aprD });
}

function isSolarSensitivityRequestValid(token, requestFingerprint, options = null) {
  if (token !== solarSensitivityRequestId) return false;
  if (shouldBlockSolarSizingForAcDraft(options)) return false;
  if (requestFingerprint !== solarSensitivityStateFingerprint()) return false;
  return true;
}

function isSolarSensitivityMatching(data = solarSensitivityData) {
  if (!data) return false;
  if (solarSensitivityAcceptedFingerprint === null) return false;
  if (shouldBlockSolarSizingForAcDraft()) return false;
  if (solarSensitivityAcceptedFingerprint !== solarSensitivityStateFingerprint()) return false;
  const wrap = $('solarSensitivityResultsWrap');
  if (wrap && wrap.classList.contains('stale')) return false;
  return true;
}

function markSolarSensitivityStale() {
  solarSensitivityRequestId++;
  solarSensitivityInFlight = false;
  solarSensitivityAcceptedFingerprint = null;
  const wrap = $('solarSensitivityResultsWrap');
  if (wrap && solarSensitivityData) {
    wrap.classList.add('stale');
  }
  const btn = $('runSolarSensitivity');
  if (btn) btn.disabled = false;
  if ($('solarSensitivityStatus') && solarSensitivityData) {
    $('solarSensitivityStatus').textContent = 'Assumptions or inputs changed · Click Run economic sensitivity to update';
  }
}

async function runSolarSensitivityAnalysis() {
  const btn = $('runSolarSensitivity');
  if (btn) btn.disabled = true;
  solarSensitivityInFlight = true;
  const token = ++solarSensitivityRequestId;
  if ($('solarSensitivityStatus')) {
    $('solarSensitivityStatus').textContent = 'Evaluating 5 economic stress scenarios across historical weather ensemble…';
  }

  let options, sens_opts, requestFingerprint;
  try {
    const expDelta = Number($('sensExportDelta') ? $('sensExportDelta').value : 20);
    const costDelta = Number($('sensCostDelta') ? $('sensCostDelta').value : 20);
    const consDelta = Number($('sensConsDelta') ? $('sensConsDelta').value : 10);
    const aprDelta = Number($('sensAprDelta') ? $('sensAprDelta').value : 2.0);

    if (!Number.isFinite(expDelta) || expDelta < 0 || expDelta > 100) {
      throw Error('Export price reduction percentage must be between 0% and 100%.');
    }
    if (!Number.isFinite(costDelta) || costDelta < 0 || costDelta > 200) {
      throw Error('Installed capital cost increase percentage must be between 0% and 200%.');
    }
    if (!Number.isFinite(consDelta) || consDelta < 0 || consDelta > 90) {
      throw Error('Baseline electricity consumption reduction must be between 0% and 90%.');
    }
    if (!Number.isFinite(aprDelta) || aprDelta < 0 || aprDelta > 30) {
      throw Error('Financing TAN increase must be between 0 and 30 percentage points.');
    }

    options = getSolarOptions();
    sens_opts = {
      export_revenue_delta_pct: expDelta,
      installed_cost_delta_pct: costDelta,
      household_consumption_delta_pct: consDelta,
      financing_apr_delta_pp: aprDelta
    };

    if (shouldBlockSolarSizingForAcDraft(options)) {
      if ($('solarSensitivityStatus')) $('solarSensitivityStatus').textContent = 'Cannot run solar sensitivity with invalid heating draft.';
      const wrap = $('solarSensitivityResultsWrap');
      if (wrap && solarSensitivityData) wrap.classList.add('stale');
      if (btn) btn.disabled = false;
      solarSensitivityInFlight = false;
      return;
    }

    const freshSettings = deepClone(settings);
    normalizeBatterySettings(freshSettings);
    delete freshSettings.hasItemized;
    cleanSettingsForBackend(freshSettings);
    const freshQuote = deepClone(quote);
    requestFingerprint = solarSensitivityStateFingerprint(freshSettings, freshQuote, options, sens_opts);

    const res = await api('/api/solar-sizing-sensitivity', {
      settings: freshSettings,
      options,
      quote: freshQuote,
      sensitivity_options: sens_opts
    });

    if (!isSolarSensitivityRequestValid(token, requestFingerprint, options)) {
      if (token === solarSensitivityRequestId) {
        markSolarSensitivityStale();
      }
      return;
    }

    solarSensitivityData = res;
    solarSensitivityAcceptedFingerprint = requestFingerprint;
    solarSensitivityInFlight = false;

    const wrap = $('solarSensitivityResultsWrap');
    if (wrap) {
      if (wrap.style) wrap.style.display = 'block';
      wrap.classList.remove('stale');
    }
    if ($('solarSensitivityStatus')) {
      $('solarSensitivityStatus').textContent = 'Sensitivity evaluation complete';
    }

    renderSolarSensitivityResults(res);
  } catch (err) {
    if (requestFingerprint && !isSolarSensitivityRequestValid(token, requestFingerprint, options)) {
      if (token === solarSensitivityRequestId) {
        markSolarSensitivityStale();
      }
      return;
    }
    solarSensitivityAcceptedFingerprint = null;
    solarSensitivityInFlight = false;
    if ($('solarSensitivityStatus')) {
      $('solarSensitivityStatus').textContent = 'Error: ' + (err.message || err);
    }
    showError(err);
  } finally {
    if (token === solarSensitivityRequestId) {
      if (btn) btn.disabled = false;
      solarSensitivityInFlight = false;
    }
  }
}

function renderSolarSensitivityResults(data) {
  if (!data || !data.scenarios) return;
  const gridEl = $('solarSensitivityScenariosGrid');
  const tableEl = $('solarSensitivityComparisonTable');
  const finBasis = data.financial_basis || 'project';
  const isEquityBasis = (finBasis === 'after_financing');

  const fmtStorage = c => {
    if (!c) return '0 kWh storage';
    const totKwh = Number(c.battery_kwh ?? 0);
    const totMods = Number(c.battery_modules ?? 0);
    if (totKwh === 0) return '0 kWh storage (no battery)';
    if (totMods === 0) return `${nf(totKwh, 1)} kWh`;
    const isMix = Boolean(c.battery_mixed_sizes);
    const secMods = isMix ? Number(c.battery_secondary_modules ?? 0) : 0;
    const firstMods = Number(c.first_modules ?? Math.max(0, totMods - secMods));
    const firstCap = Number(c.battery_module_kwh ?? (typeof settings !== 'undefined' ? settings.battery_module_kwh : 0));
    const secCap = Number(c.battery_secondary_module_kwh ?? (typeof settings !== 'undefined' ? settings.battery_secondary_module_kwh : 0));
    const fmtN = n => Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));

    if (isMix) {
      if (firstMods > 0 && secMods > 0) {
        return `${nf(totKwh, 1)} kWh (${firstMods} × ${fmtN(firstCap)} + ${secMods} × ${fmtN(secCap)} mod)`;
      } else if (secMods > 0) {
        return `${nf(totKwh, 1)} kWh (${secMods} × ${fmtN(secCap)} mod)`;
      } else {
        return `${nf(totKwh, 1)} kWh (${firstMods} × ${fmtN(firstCap)} mod)`;
      }
    }
    return `${nf(totKwh, 1)} kWh (${totMods} mod)`;
  };

  const canonicalKeys = ['base', 'lower_export', 'higher_installed_cost', 'lower_consumption', 'higher_apr'];
  const actualKeys = Object.keys(data.scenarios || {});
  let scenarioKeys;
  if (actualKeys.length === 5) {
    scenarioKeys = actualKeys.includes('base') ? ['base', ...actualKeys.filter(k => k !== 'base')] : actualKeys;
  } else {
    const seen = new Set();
    const ordered = [];
    canonicalKeys.forEach(k => { seen.add(k); ordered.push(k); });
    actualKeys.forEach(k => { if (!seen.has(k)) { seen.add(k); ordered.push(k); } });
    scenarioKeys = ordered;
  }

  let robustCount = 0;
  let shiftCount = 0;
  let unavailableCount = 0;
  let unknownCount = 0;

  scenarioKeys.forEach(k => {
    const s = data.scenarios?.[k];
    const comp = data.comparisons?.find(c => c.scenario_id === k);
    const status = comp?.status || s?.status || (s ? 'OK' : 'UNKNOWN');
    if (status === 'UNAVAILABLE') {
      unavailableCount++;
      return;
    }
    if (!s || status === 'UNKNOWN') {
      unknownCount++;
      return;
    }
    const fRole = s.roles?.financial || s.financial;
    const rep = s.base_preferred_repriced;
    const reg = comp?.regret_eur ?? rep?.regret_vs_scenario_financial_best_eur ?? rep?.regret_eur;
    const changed = comp?.changed_layout ?? rep?.changed_layout;

    if (rep == null && comp == null) {
      unknownCount++;
      return;
    }
    if (reg == null || !Number.isFinite(Number(reg))) {
      unknownCount++;
      return;
    }
    const regNum = Number(reg);
    if (fRole && regNum === 0 && changed === false) {
      robustCount++;
    } else {
      shiftCount++;
    }
  });

  const baseScen = data.scenarios?.base;
  const baseFin = baseScen?.roles?.financial || baseScen?.financial;
  const isBaseNoInstall = Boolean(baseFin?.is_no_install);
  const baseSpec = isBaseNoInstall
    ? '0 panels (no-install)'
    : (baseFin ? `${baseFin.panel_count} panels (${nf(baseFin.kwp, 2)} kWp) · ${fmtStorage(baseFin)}` : '0 panels');

  const allExact = scenarioKeys.every(k => Boolean(data.scenarios?.[k]?.search_summary?.exact));
  const candidateTerm = allExact ? 'optimal' : 'best found';
  const totalCount = scenarioKeys.length;

  let verdictTag = '';
  let verdictText = '';

  if (totalCount === 0) {
    verdictTag = '<span class="tag base-tag">No scenarios</span>';
    verdictText = 'No sensitivity scenarios evaluated.';
  } else if (robustCount === totalCount) {
    verdictTag = `<span class="tag robust-tag">${robustCount} of ${totalCount} robust</span>`;
    verdictText = `Base configuration (${baseSpec}) retained as ${candidateTerm} across all <strong>${robustCount} of ${totalCount}</strong> evaluated scenarios (€0 value regret).`;
  } else {
    const tagClass = robustCount > 0 ? 'robust-tag' : 'delta-tag';
    verdictTag = `<span class="tag ${tagClass}">${robustCount} of ${totalCount} robust</span>`;
    const details = [];
    if (shiftCount > 0) details.push(`${shiftCount} layout shift(s)`);
    if (unavailableCount > 0) details.push(`${unavailableCount} unavailable`);
    if (unknownCount > 0) details.push(`${unknownCount} unknown`);
    const detailStr = details.length ? ` (${details.join(', ')})` : '';
    verdictText = `Base configuration (${baseSpec}) robust in <strong>${robustCount} of ${totalCount}</strong> scenarios${detailStr}.`;
  }

  const verdictInnerHtml = `
    <div class="robustness-verdict-header">
      <div class="robustness-verdict-title"><strong>Economic Robustness Verdict</strong></div>
      ${verdictTag}
    </div>
    <p class="robustness-verdict-desc">${verdictText}</p>
  `;
  const verdictCardHtml = `<div class="solar-robustness-verdict">${verdictInnerHtml}</div>`;

  const verdictEl = $('solarRobustnessVerdict');
  if (verdictEl) verdictEl.innerHTML = verdictCardHtml;
  if (gridEl) {
    gridEl.classList?.remove?.('solar-robustness-verdict');
    gridEl.innerHTML = verdictCardHtml;
  }

  if (tableEl) {
    const rawRows = (data.comparisons && data.comparisons.length)
      ? data.comparisons
      : scenarioKeys.map(k => {
          const s = data.scenarios?.[k];
          const fRole = s?.roles?.financial || s?.financial;
          const rep = s?.base_preferred_repriced;
          return {
            scenario_id: k,
            label: s?.scenario_label || k,
            status: s ? (s.status || 'OK') : 'UNKNOWN',
            unavailable_reason: s?.unavailable_reason,
            financial_panel_count: fRole?.panel_count,
            financial_kwp: fRole?.kwp,
            financial_battery_kwh: fRole?.battery_kwh,
            financial_capex_eur: fRole?.capex_eur,
            financial_project_npv_mean: fRole?.project_npv_mean !== undefined ? fRole.project_npv_mean : fRole?.project_npv_median,
            financial_equity_npv_mean: fRole?.equity_npv_mean !== undefined ? fRole.equity_npv_mean : fRole?.equity_npv_median,
            base_preferred_repriced_npv: rep?.npv_mean !== undefined ? rep.npv_mean : rep?.npv_median,
            regret_eur: rep?.regret_vs_scenario_financial_best_eur ?? rep?.regret_eur,
            changed_layout: rep?.changed_layout,
            delta_panels: rep?.delta_panels,
            delta_battery_kwh: rep?.delta_battery_kwh
          };
        });

    tableEl.innerHTML = `
      <table class="design-unified-table">
        <thead>
          <tr>
            <th>Scenario</th>
            <th>Optimal PV Array &amp; Storage</th>
            <th>Turnkey Capex</th>
            <th>Project NPV ${!isEquityBasis ? '<span class="tag">Ranked</span>' : ''}</th>
            <th>Equity NPV ${isEquityBasis ? '<span class="tag">Ranked</span>' : ''}</th>
            <th>Coverage</th>
            <th>Lower-cost Tradeoff</th>
            <th>Regret &amp; Status</th>
          </tr>
        </thead>
        <tbody>
          ${rawRows.map(c => {
            const scen = data.scenarios?.[c.scenario_id];
            const isMissing = !scen;
            const isUnavailable = (c.status === 'UNAVAILABLE' || scen?.status === 'UNAVAILABLE');
            const pertDesc = scen?.perturbation_description || c.perturbation_description || '';
            const isBase = c.scenario_id === 'base';

            if (isMissing) {
              return `
                <tr class="unavailable-row">
                  <td data-label="Scenario"><strong>${esc(c.label || c.scenario_id)}</strong></td>
                  <td data-label="Availability" colspan="6" class="muted">Scenario not evaluated or data unavailable</td>
                  <td data-label="Status"><span class="tag unknown-tag">Unknown</span></td>
                </tr>
              `;
            }

            if (isUnavailable) {
              const reason = c.unavailable_reason || scen?.unavailable_reason || 'Parameter bounds exceeded specification limits.';
              return `
                <tr class="unavailable-row">
                  <td data-label="Scenario">
                    <strong>${esc(c.label || scen?.scenario_label || c.scenario_id)}</strong>
                    ${pertDesc ? `<div class="scenario-perturbation-subtitle muted" title="${esc(pertDesc)}"><small>${esc(pertDesc)}</small></div>` : ''}
                  </td>
                  <td data-label="Availability" colspan="6" class="muted">${esc(reason)}</td>
                  <td data-label="Status"><span class="tag unavailable-tag">UNAVAILABLE</span></td>
                </tr>
              `;
            }

            const finRole = scen.roles?.financial || scen.financial;
            const repriced = scen.base_preferred_repriced;

            // PV/Storage & qualification
            const pCount = c.financial_panel_count ?? finRole?.panel_count;
            const kwpVal = c.financial_kwp ?? finRole?.kwp;
            const batVal = c.financial_battery_kwh ?? finRole?.battery_kwh;
            const isNoInstall = Boolean(finRole?.is_no_install);

            let pvStorageDesc = '—';
            if (isNoInstall) {
              pvStorageDesc = '0 panels (No-install baseline)';
            } else if (pCount != null) {
              pvStorageDesc = `${pCount}p (${nf(kwpVal, 2)} kWp) · ${finRole ? fmtStorage(finRole) : `${nf(batVal ?? 0, 1)} kWh`}`;
            }

            const isExact = Boolean(scen.search_summary?.exact);
            const qualText = isExact
              ? 'Certified global optimum within bounds'
              : `Best found among ${scen.search_summary?.evaluated_count || 'sampled'} evaluated candidates (gap unknown)`;

            // Capex
            const capexVal = c.financial_capex_eur ?? finRole?.capex_eur;

            // Project NPV & Weather range
            const projNpvVal = c.financial_project_npv_mean !== undefined ? c.financial_project_npv_mean : (finRole?.project_npv_mean !== undefined ? finRole.project_npv_mean : finRole?.project_npv_median);
            const projRange = finRole?.project_npv;
            const projMin = projRange?.min ?? finRole?.project_npv_min;
            const projMax = projRange?.max ?? finRole?.project_npv_max;
            const projWeatherHtml = (projMin != null && projMax != null)
              ? `<div class="weather-range-note"><small class="muted">Weather: ${fmt(projMin, 'eur')} – ${fmt(projMax, 'eur')}</small></div>`
              : '';

            // Equity NPV & Weather range
            const eqNpvVal = c.financial_equity_npv_mean !== undefined ? c.financial_equity_npv_mean : (finRole?.equity_npv_mean !== undefined ? finRole.equity_npv_mean : finRole?.equity_npv_median);
            const eqRange = finRole?.equity_npv;
            const eqMin = eqRange?.min ?? finRole?.equity_npv_min;
            const eqMax = eqRange?.max ?? finRole?.equity_npv_max;
            const eqWeatherHtml = (eqMin != null && eqMax != null)
              ? `<div class="weather-range-note"><small class="muted">Weather: ${fmt(eqMin, 'eur')} – ${fmt(eqMax, 'eur')}</small></div>`
              : '';

            // Coverage
            let covDisplay = '—';
            if (finRole && finRole.coverage_pct != null) {
              covDisplay = `${nf(finRole.coverage_pct, 1)}%`;
            } else if (finRole && (finRole.autarky_mean != null || finRole.autarky_median != null)) {
              covDisplay = fmt(finRole.autarky_mean !== undefined ? finRole.autarky_mean : finRole.autarky_median, 'pct');
            }

            // Lower-cost alternative (smaller_sacrifice)
            const smSac = scen.smaller_sacrifice;
            let smSacHtml = '<span class="muted">—</span>';
            if (smSac && smSac.sacrifice_eur != null && Number.isFinite(Number(smSac.sacrifice_eur))) {
              smSacHtml = `<small>Sacrifices <strong>€${nf(smSac.sacrifice_eur)}</strong> (${smSac.sacrifice_pct != null ? nf(smSac.sacrifice_pct, 1) : '—'}% NPV)</small>`;
            }

            // Regret & Status
            const regVal = c.regret_eur ?? repriced?.regret_vs_scenario_financial_best_eur ?? repriced?.regret_eur;
            const changedLayout = c.changed_layout ?? repriced?.changed_layout;
            const dPanels = c.delta_panels ?? repriced?.delta_panels;
            const dBat = c.delta_battery_kwh ?? repriced?.delta_battery_kwh;

            const repNpv = c.base_preferred_repriced_npv ?? (repriced?.npv_mean !== undefined ? repriced.npv_mean : repriced?.npv_median);
            const repSubline = (repNpv != null && Number.isFinite(Number(repNpv)))
              ? `<div class="base-repriced-subline muted"><small>Base repriced: ${fmt(repNpv, 'eur')}</small></div>`
              : '';

            let regretStatusHtml = '';
            if (isBase) {
              regretStatusHtml = `<span class="good">€0 regret</span> <span class="tag base-tag">Base</span>${repSubline}`;
            } else if (repriced == null || regVal == null || !Number.isFinite(Number(regVal))) {
              regretStatusHtml = `<span class="muted">Unknown</span> <span class="tag unknown-tag">Unknown</span>${repSubline}`;
            } else if (Number(regVal) > 0 || changedLayout) {
              const regNum = Number(regVal);
              const regStr = regNum > 0
                ? `<span class="bad">€${nf(regNum)} regret</span>`
                : '<span class="bad">Layout shift</span>';
              const deltaStr = (changedLayout && (dPanels != null || dBat != null))
                ? `<div class="repriced-deltas"><small>${(dPanels ?? 0) >= 0 ? '+' : ''}${dPanels ?? 0}p, ${(dBat ?? 0) >= 0 ? '+' : ''}${nf(dBat ?? 0, 1)} kWh</small></div>`
                : '';
              regretStatusHtml = `${regStr} <span class="tag delta-tag">${changedLayout ? 'Layout shift' : 'Regret'}</span>${deltaStr}${repSubline}`;
            } else if (finRole && Number(regVal) === 0 && changedLayout === false) {
              regretStatusHtml = `<span class="good">€0 regret</span> <span class="tag robust-tag">Robust winner</span>${repSubline}`;
            } else {
              regretStatusHtml = `<span class="muted">Unknown</span> <span class="tag unknown-tag">Unknown</span>${repSubline}`;
            }

            return `
              <tr class="${isBase ? 'current-row' : ''}">
                <td data-label="Scenario">
                  <strong>${esc(c.label || scen?.scenario_label || c.scenario_id)}</strong>
                  ${pertDesc ? `<div class="scenario-perturbation-subtitle muted" title="${esc(pertDesc)}"><small>${esc(pertDesc)}</small></div>` : ''}
                </td>
                <td data-label="Optimal PV Array &amp; Storage">
                  <div>${esc(pvStorageDesc)}</div>
                  <small class="muted">${esc(qualText)}</small>
                </td>
                <td data-label="Turnkey Capex">${capexVal != null ? fmt(capexVal, 'eur') : '—'}</td>
                <td data-label="Project NPV" class="${!isEquityBasis ? 'highlight-cell' : ''}">
                  <div><strong>${projNpvVal != null ? fmt(projNpvVal, 'eur') : '—'}</strong></div>
                  ${projWeatherHtml}
                </td>
                <td data-label="Equity NPV" class="${isEquityBasis ? 'highlight-cell' : ''}">
                  <div><strong>${eqNpvVal != null ? fmt(eqNpvVal, 'eur') : '—'}</strong></div>
                  ${eqWeatherHtml}
                </td>
                <td data-label="Coverage">${covDisplay}</td>
                <td data-label="Lower-cost Tradeoff">${smSacHtml}</td>
                <td data-label="Regret &amp; Status">${regretStatusHtml}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
      <p class="muted" style="margin-top:8px;font-size:12px;">
        <small><strong>Weather evaluation methodology:</strong> All 5 economic scenarios evaluate every candidate across the full ${(typeof getWeatherEnsembleMeta === 'function' ? getWeatherEnsembleMeta(data?.summary || data || (typeof current !== 'undefined' ? current?.result : null)) : {}).count ? (typeof getWeatherEnsembleMeta === 'function' ? getWeatherEnsembleMeta(data?.summary || data || (typeof current !== 'undefined' ? current?.result : null)) : {}).count + " " : ""}historical PVGIS weather analogues${(typeof getWeatherEnsembleMeta === 'function' ? getWeatherEnsembleMeta(data?.summary || data || (typeof current !== 'undefined' ? current?.result : null)) : {}).rangeLabel ? " (" + (typeof getWeatherEnsembleMeta === 'function' ? getWeatherEnsembleMeta(data?.summary || data || (typeof current !== 'undefined' ? current?.result : null)) : {}).rangeLabel + ")" : ""} to preserve empirical climate spread alongside economic parameter stress, evaluating deterministic historical weather sequences without synthetic forecast models.</small>
      </p>
    `;
  }
}

/* END SOLAR SIZING */

init();

$('toggleInputs').onclick=()=>{const open=document.querySelector('aside').classList.toggle('inputs-open');$('toggleInputs').setAttribute('aria-expanded',String(open));$('toggleInputs').textContent=open?'Hide inputs':'Inputs';};

function initRedesignEventListeners(){
 const tabNames=['summary','design','financing','behavior','prices','assumptions'];
 document.querySelectorAll('button[data-view]').forEach(b=>{
  b.onclick=()=>switchView(b.dataset.view);
 });
 document.querySelectorAll('[data-view-target]').forEach(el=>{
  el.onclick=()=>switchView(el.dataset.viewTarget);
 });
 document.addEventListener('click',e=>{
  const link=e.target.closest?e.target.closest('a[href^="#"]'):null;
  if(!link)return;
  const hash=link.getAttribute('href').slice(1);
  if(tabNames.includes(hash)){
   e.preventDefault();
   switchView(hash);
  }else if(hash){
   const targetEl=document.getElementById(hash);
   const parentView=targetEl?.closest?.('.view');
   if(parentView&&tabNames.includes(parentView.id)){
    if(parentView.hidden)switchView(parentView.id);
    targetEl.scrollIntoView({behavior:'smooth',block:'start'});
   }
  }
 });
 const initialHash=(typeof window!=='undefined'&&window.location?.hash)?window.location.hash.slice(1):'';
 if(tabNames.includes(initialHash)){
  switchView(initialHash);
 }
 if(typeof window!=='undefined'&&window.addEventListener){
  window.addEventListener('hashchange',()=>{
   const h=window.location.hash?window.location.hash.slice(1):'';
   if(tabNames.includes(h))switchView(h);
  });
 }
 const scopeSolar=$('scopeSolarBtn'),scopePackage=$('scopePackageBtn');
 if(scopeSolar)scopeSolar.onclick=()=>setSummaryScope('solar');
 if(scopePackage)scopePackage.onclick=()=>setSummaryScope('package');
 const cashBefore=$('cashBeforeBtn'),cashAfter=$('cashAfterBtn');
 if(cashBefore)cashBefore.onclick=()=>setSummaryCash('cash');
 if(cashAfter)cashAfter.onclick=()=>setSummaryCash('loan');
 const curveUndisc=$('curveUndiscountedBtn'),curveDisc=$('curveDiscountedBtn');
 if(curveUndisc)curveUndisc.onclick=()=>setSummaryCurveBasis('undiscounted');
 if(curveDisc)curveDisc.onclick=()=>setSummaryCurveBasis('discounted');
 syncSummaryControls();
 const moneyBasis=$('moneyBasisSelect');
 if(moneyBasis)moneyBasis.onchange=e=>editSetting('cashflow_mode',e.target.value);
 const sensBtn=$('runSummarySensitivityBtn');
 if(sensBtn)sensBtn.onclick=()=>fetchSummarySensitivity();
 const printBtn=$('printSummaryBtn'), pitchBtn=$('printPitchBtn');
 if(printBtn)printBtn.onclick=()=>printPitchComparisonSheet();
 if(pitchBtn)pitchBtn.onclick=()=>printPitchComparisonSheet();
 const toggleSolar=$('toggleSolarCurveView');
 if(toggleSolar){
  toggleSolar.onclick=()=>{
   const w2=$('solarChart2Wrap'), c1=$('solarChart1'), leg1=$('solarSizeLegend');
   if(w2&&c1){
    w2.hidden=!w2.hidden;
    c1.hidden=!w2.hidden;
    if(leg1)leg1.hidden=!w2.hidden;
    toggleSolar.textContent=w2.hidden?'Show marginal returns (ΔNPV/panel)':'Show total size curve (NPV & autarky)';
    toggleSolar.classList.toggle('active',!w2.hidden);
    toggleSolar.setAttribute('aria-pressed',!w2.hidden?'true':'false');
   }
  };
 }
 document.querySelectorAll('[data-balance-view]').forEach(btn=>{
  btn.onclick=()=>setBalanceView(btn.dataset.balanceView);
 });
 const seasonPills=$('seasonSelectorPills');
 if(seasonPills){
  seasonPills.onclick=e=>{
   const btn=e.target.closest?e.target.closest('button[data-season]'):null;
   if(!btn)return;
   seasonPills.querySelectorAll('button').forEach(b=>{
    b.classList.remove('active');
    b.setAttribute('aria-pressed','false');
   });
   btn.classList.add('active');
   btn.setAttribute('aria-pressed','true');
   activeSeason=btn.dataset.season;
   if(behaviorMatrixCache)drawTypicalDayChart(behaviorMatrixCache,activeSeason);
   else refreshBehaviorViews();
  };
 }
 document.querySelectorAll('[data-heatmap-metric]').forEach(btn=>{
  btn.onclick=()=>{
   document.querySelectorAll('[data-heatmap-metric]').forEach(b=>{
    b.classList.remove('active');
    b.setAttribute('aria-pressed','false');
   });
   btn.classList.add('active');
   btn.setAttribute('aria-pressed','true');
   activeHeatmapMetric=btn.dataset.heatmapMetric;
   if(behaviorMatrixCache)drawMonthHourHeatmap(behaviorMatrixCache,activeHeatmapMetric);
   else refreshBehaviorViews();
  };
 });
 document.querySelectorAll('[data-behavior-scen]').forEach(btn=>{
  btn.onclick=()=>{
   editSetting('load_scenario',btn.dataset.behaviorScen);
   document.querySelectorAll('[data-behavior-scen]').forEach(b=>{
    const match = b.dataset.behaviorScen === btn.dataset.behaviorScen;
    b.classList.toggle('active', match);
    b.setAttribute('aria-pressed', match ? 'true' : 'false');
   });
  };
 });
 const menuDrop=$('scenarioMenu');
 if(menuDrop){
  document.addEventListener('click',e=>{
   if(menuDrop.open&&!menuDrop.contains(e.target)){
    menuDrop.open=false;
   }
  });
  document.addEventListener('keydown',e=>{
   if(e.key==='Escape'&&menuDrop.open){
    menuDrop.open=false;
   }
  });
  if(typeof menuDrop.querySelectorAll==='function'){
   menuDrop.querySelectorAll('button, a, label').forEach(item=>{
    item.addEventListener('click',()=>{
     setTimeout(()=>{ if(menuDrop)menuDrop.open=false; },50);
    });
   });
  }
 }
 const runSolar=$('runSolarSizing');
 if(runSolar)runSolar.onclick=()=>{if(typeof runSolarSizingAnalysis==='function')runSolarSizingAnalysis();};
 initTheme();
}
initRedesignEventListeners();

