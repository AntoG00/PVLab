/* Run with Node: test market metadata rendering for profiles and catalog provenance in UI. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const appPath = path.join(__dirname, '../feasibility/web/app.js');
const source = fs.readFileSync(appPath, 'utf8');

function extractRenderProfileMarketMetadata(src) {
  const start = src.indexOf('function renderProfileMarketMetadata(');
  if (start === -1) {
    return function renderProfileMarketMetadata() {
      throw new Error('renderProfileMarketMetadata is not yet implemented in feasibility/web/app.js');
    };
  }
  let end = src.indexOf('\nfunction ', start + 1);
  if (end === -1) end = src.length;
  const fnCode = src.slice(start, end);

  const esc = (s) => String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  const sandbox = { esc, console };
  vm.createContext(sandbox);
  vm.runInContext(`${fnCode}\nglobalThis.fn = renderProfileMarketMetadata;`, sandbox);
  return sandbox.fn || sandbox.renderProfileMarketMetadata;
}

const renderProfileMarketMetadata = extractRenderProfileMarketMetadata(source);

test('renderProfileMarketMetadata returns HTML string with EUR/kWh, EUR/MWh, and required disclaimers', () => {
  const profile = {
    zone: 'CSUD',
    market: {
      series: 'CSUD',
      valuation_mode: 'shape_scaled',
      zone: 'CSUD',
      price_revision: '2026-v1',
      window_start: '2025-10-01',
      window_end: '2026-09-30'
    }
  };
  const catalog = { revision: 'cat-2026.1' };

  const html = renderProfileMarketMetadata(profile, catalog);
  assert.equal(typeof html, 'string');
  assert.ok(html.length > 0);
  assert.match(html, /EUR\/kWh/i);
  assert.match(html, /EUR\/MWh/i);
  assert.match(html, /PUN/i);
  assert.match(html, /comparison/i);
  assert.match(html, /export/i);
  assert.match(html, /fixed retail/i);
});

test('valuation modes: renders mean-one normalized shape, signed raw prices, and explicit flat benchmark', () => {
  const baseProfile = {
    zone: 'CSUD',
    market: {
      series: 'CSUD',
      zone: 'CSUD',
      price_revision: '2026-v1',
      window_start: '2025-10-01',
      window_end: '2026-09-30'
    }
  };
  const catalog = { revision: 'v1' };

  const htmlShape = renderProfileMarketMetadata({
    ...baseProfile,
    market: { ...baseProfile.market, valuation_mode: 'shape_scaled' }
  }, catalog);
  assert.match(htmlShape, /mean-one normalized shape/i);

  const htmlAbs = renderProfileMarketMetadata({
    ...baseProfile,
    market: { ...baseProfile.market, valuation_mode: 'absolute_proxy' }
  }, catalog);
  assert.match(htmlAbs, /signed raw prices/i);

  const htmlFlat = renderProfileMarketMetadata({
    ...baseProfile,
    market: { ...baseProfile.market, valuation_mode: 'flat' }
  }, catalog);
  assert.match(htmlFlat, /explicit flat benchmark/i);
});

test('safely escapes malicious labels, series, zone, price_revision, windows, and catalog revision', () => {
  const profile = {
    zone: '<script>alert("zone")</script>',
    market: {
      series: '<img src=x onerror=alert(1)>',
      valuation_mode: 'shape_scaled',
      zone: '"><b onmouseover="alert(1)">ZONE</b>',
      price_revision: 'rev<svg onload=alert(2)>',
      window_start: '2025-01-01"><script>',
      window_end: '2025-12-31\'"onclick="alert(3)'
    }
  };
  const catalog = { revision: 'cat<script>evil()</script>' };

  const html = renderProfileMarketMetadata(profile, catalog);
  assert.doesNotMatch(html, /<script\b/i);
  assert.doesNotMatch(html, /<img\b/i);
  assert.doesNotMatch(html, /<svg\b/i);
  assert.doesNotMatch(html, /<b\b/i);
  assert.match(html, /&lt;b onmouseover=&quot;alert\(1\)&quot;&gt;ZONE&lt;\/b&gt;/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;svg onload=alert\(2\)&gt;/);
  assert.match(html, /&lt;script&gt;evil\(\)&lt;\/script&gt;/);
});

test('missing catalog metadata explicitly renders unavailable status without crashing', () => {
  const profile = {
    zone: 'NORD',
    market: {
      series: 'NORD',
      valuation_mode: 'shape_scaled',
      zone: 'NORD',
      price_revision: '2026-v2',
      window_start: '2025-01-01',
      window_end: '2025-12-31'
    }
  };

  const htmlNull = renderProfileMarketMetadata(profile, null);
  assert.match(htmlNull, /unavailable/i);

  const htmlUndef = renderProfileMarketMetadata(profile, undefined);
  assert.match(htmlUndef, /unavailable/i);

  const htmlEmpty = renderProfileMarketMetadata(profile, {});
  assert.match(htmlEmpty, /unavailable/i);
});

test('correctly renders custom/fake zones and non-standard time windows', () => {
  const profile = {
    zone: 'FAKE_ZONE_99',
    market: {
      series: 'PUN_MOCK',
      valuation_mode: 'absolute_proxy',
      zone: 'FAKE_ZONE_99',
      price_revision: 'custom_rev_2028_beta',
      window_start: '2028-04-01',
      window_end: '2028-10-31'
    }
  };
  const catalog = { revision: 'catalog_v99' };

  const html = renderProfileMarketMetadata(profile, catalog);
  assert.match(html, /FAKE_ZONE_99/);
  assert.match(html, /PUN_MOCK/);
  assert.match(html, /custom_rev_2028_beta/);
  assert.match(html, /2028-04-01/);
  assert.match(html, /2028-10-31/);
  assert.match(html, /catalog_v99/);
  assert.match(html, /signed raw prices/i);
});

function loadFunctions(names, globals={}) {
 const context=vm.createContext(globals);
 for(const name of names){
  let start=source.indexOf('function '+name+'(');assert.ok(start>=0,name);if(source.slice(start-6,start)==='async ')start-=6;
  const rest=source.slice(start+1),match=/\n(?:async )?function /.exec(rest);
  vm.runInContext(source.slice(start,match?start+1+match.index:undefined),context);
 }
 return context;
}
const profileWith=nr=>({id:'p',fiscal:{parameters:{deduction_rate:0}},roofs:Array.from({length:nr},(_,i)=>({id:'plane'+i,label:'Custom <'+i+'>',max_kwp:2}))});
for(const nr of [1,7])test(nr+' arbitrary planes retain IDs in a recommendation and reject missing/unknown planes',()=>{
 const profile=profileWith(nr),counts=Object.fromEntries(profile.roofs.map(r=>[r.id,2]));
 const ctx=loadFunctions(['profileCounts'],{activeResultProfile:profile,settings:{panel_watts:400}});
 assert.deepEqual(JSON.parse(JSON.stringify(ctx.profileCounts({settings:{roof_panels:counts}}))),counts);
 assert.throws(()=>ctx.profileCounts({roof_counts:{...counts,alien:1}}),/Unknown or missing/);
 const missing={...counts};delete missing.plane0;assert.throws(()=>ctx.profileCounts({roof_counts:missing}),/Unknown or missing/);
 assert.throws(()=>ctx.profileCounts({roof_counts:{...counts,plane0:6}}),/capacity/);
});
test('apply carries all seven roof counts only for matching sizing revision, without legacy keys',()=>{
 const profile=profileWith(7),counts=Object.fromEntries(profile.roofs.map(r=>[r.id,2]));
 const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',classList:{contains:()=>false}});return nodes.get(id);};let scheduled=0;
 const ctx=loadFunctions(['profileCounts','applySolarCandidate'],{activeResultProfile:profile,householdClient:{profileId:'p',revision:'r2'},solarData:{_presentation:{profile_id:'p',input_revision:'r2'}},solarAcceptedFingerprint:'f',solarStateFingerprint:()=> 'f',isSolarSizingMatching:()=>true,shouldBlockSolarSizingForAcDraft:()=>false,solarInFlight:false,settings:{panel_watts:400,battery_kwh:0,battery_modules:0},quote:{line_items:[]},$,normalizeBatterySettings:()=>{},schedule:()=>scheduled++,nf:String,fmt:String});
 const candidate={roof_counts:counts,panel_count:14,kwp:5.6,battery_kwh:0,battery_modules:0,settings:{panel_watts:400}};
 ctx.applySolarCandidate(candidate);
 assert.deepEqual(JSON.parse(JSON.stringify(ctx.settings.roof_panels)),counts);assert.equal(scheduled,1);
 assert.equal(Object.hasOwn(ctx.settings,'south_panels'),false);assert.equal(Object.hasOwn(ctx.settings,'dormer_south_proxy'),false);
 ctx.householdClient.revision='r3';ctx.applySolarCandidate(candidate);assert.equal(scheduled,1);assert.match($('solarApplyStatus').textContent,/revision changed/);
});
test('late result presentation cannot be accepted for another profile or revision',()=>{
 const stale=()=>Error('STALE_RESPONSE');const ctx=loadFunctions(['acceptProfilePresentation'],{activeResultProfile:profileWith(1),householdClient:{profileId:'p',revision:'new',stale}});
 assert.throws(()=>ctx.acceptProfilePresentation({_presentation:{profile_id:'p',input_revision:'old'}}),/STALE_RESPONSE/);
 assert.throws(()=>ctx.acceptProfilePresentation({_presentation:{profile_id:'other',input_revision:'new'}}),/STALE_RESPONSE/);
 const value={_presentation:{profile_id:'p',input_revision:'new'}};ctx.acceptProfilePresentation(value,{id:'owned'});assert.equal(value._ownedJob.id,'owned');
});
test('quality distinguishes projected targets and synthetic timing pending from weather ranges',()=>{
 const ctx=loadFunctions(['renderProfileDisclosures'],{esc:s=>String(s??'').replaceAll('<','&lt;'),nf:v=>v.toFixed(1)});
 const html=ctx.renderProfileDisclosures({profile_id:'<img>',input_revision:'rev',quality:{measured_training_fraction:.25,synthetic_training_fraction:.75,projected_output_fraction:1},weather_years:[2022,2023],limitations:['Historical weather min–max is a scenario range, not a confidence interval.','Synthetic timing scenario sensitivity: pending independent jobs; no range has been calculated.']});
 assert.match(html,/25.0%/);assert.match(html,/75.0%/);assert.match(html,/100.0%/);assert.match(html,/Synthetic timing.*pending/);assert.match(html,/Historical weather/);assert.doesNotMatch(html,/<img>/);
});
test('provenance renders ARERA bands label only when load provenance tou_mode is exactly arera_f1_f2_f3',()=>{
 const ctx=loadFunctions(['renderProfileDisclosures'],{esc:s=>String(s??'').replaceAll('<','&lt;'),nf:v=>v.toFixed(1)});
 const base={profile_id:'p',input_revision:'rev',quality:{},weather_years:[2022],limitations:[]};
 const htmlMatch=ctx.renderProfileDisclosures({...base,load_provenance:{tou_mode:'arera_f1_f2_f3'}});
 assert.match(htmlMatch,/Hourly shape: ARERA F1\/F2\/F3 bands/);
 const htmlAbsent=ctx.renderProfileDisclosures({...base,load_provenance:{}});
 assert.doesNotMatch(htmlAbsent,/Hourly shape: ARERA F1\/F2\/F3 bands/);
 const htmlNoProv=ctx.renderProfileDisclosures(base);
 assert.doesNotMatch(htmlNoProv,/Hourly shape: ARERA F1\/F2\/F3 bands/);
 const htmlOther=ctx.renderProfileDisclosures({...base,load_provenance:{tou_mode:'other_mode'}});
 assert.doesNotMatch(htmlOther,/Hourly shape: ARERA F1\/F2\/F3 bands/);
 const htmlBool=ctx.renderProfileDisclosures({...base,load_provenance:{tou_mode:true}});
 assert.doesNotMatch(htmlBool,/Hourly shape: ARERA F1\/F2\/F3 bands/);
});
test('CSV neutralizes formula prefixes after whitespace and retains safe labels',()=>{
 const ctx=loadFunctions(['csvSafeCell']);
 for(const v of ['=1+1',' +2','\t@cmd','-99','\r=1'])assert.equal(ctx.csvSafeCell(v),"'"+v);
 assert.equal(ctx.csvSafeCell('Plane 1'),'Plane 1');
});
const {HouseholdClient}=require('../feasibility/web/sync.js');
for(const response of [{id:'j',profile_id:'other',revision:'r',state:'complete'},{id:'j',profile_id:'p',revision:'old',state:'complete'},{id:'wrong',profile_id:'p',revision:'r',state:'complete'}])test('owned polling rejects mismatched job identity '+JSON.stringify(response),async()=>{
 const client=new HouseholdClient({fetch:async path=>({ok:true,json:async()=>path==='/api/session'?{csrf_token:'t'}:response})});client.select('p','r');await assert.rejects(client.waitJob('j'),/STALE_RESPONSE/);
});
test('cancel targets only the current owned job, and retries can use a fresh ID',async()=>{
 const calls=[],client=new HouseholdClient({fetch:async(path,options)=>{calls.push([path,options.method]);return {ok:true,json:async()=>path==='/api/session'?{csrf_token:'t'}:{id:'fresh',profile_id:'p',revision:'r',state:'complete'}};}});client.select('p','r');client.pendingJob={id:'old',profileId:'p',revision:'r'};await client.cancelJob();assert.deepEqual(calls.at(-1),['/api/jobs/old','DELETE']);assert.equal((await client.waitJob('fresh')).id,'fresh');
});

test('shared hydration keeps worker descriptors and removes profile-only cooling from scenario values',()=>{
 const schema={defaults:{baseline_cooling_kwh:12,panel_watts:400},records:[{name:'panel_watts',unit:'W',bounds:[1,null],value:400},{name:'baseline_cooling_kwh',value:12}]};
 const ctx=loadFunctions(['prepareScenarioSettings','applySharedState'],{activeResultProfile:profileWith(1),schema,settings:{},quote:{line_items:[]},LEGACY_HEATING_KEYS:[],deepClone:v=>JSON.parse(JSON.stringify(v)),normalizeBatterySettings:()=>{},refreshConditionalControls:()=>{},updateGasEliminatedLabels:()=>{},updateHpNotices:()=>{},hasInvalidAcScheduleDraft:()=>false,sharedUiReady:false});
 ctx.applySharedState({settings:{baseline_cooling_kwh:12,panel_watts:440,roof_panels:{plane0:1}},defaults:{baseline_cooling_kwh:12},records:[{name:'baseline_cooling_kwh',value:12}]});
 assert.equal(Object.hasOwn(ctx.settings,'baseline_cooling_kwh'),false);assert.equal(Object.hasOwn(schema.defaults,'baseline_cooling_kwh'),false);
 assert.equal(schema.records.length,1);assert.equal(schema.records[0].unit,'W');assert.equal(schema.records[0].value,440);
});
test('backend cleaning retains the structural profile plane map',()=>{
 const ctx=loadFunctions(['cleanSettingsForBackend'],{activeResultProfile:profileWith(1),schema:{records:[{name:'panel_watts'}]},LEGACY_HEATING_KEYS:[]});
 const value={roof_panels:{plane0:1},panel_watts:400,baseline_cooling_kwh:0};ctx.cleanSettingsForBackend(value);assert.deepEqual(value,{roof_panels:{plane0:1},panel_watts:400});
});
test('tab computations serialize inside the server quota and reject stale queued revisions',async()=>{
 const client=new HouseholdClient({fetch:async()=>{}});client.select('p','r');let release;const first=new Promise(r=>release=r),order=[];
 const a=client.runComputation(client.generation,async()=>{order.push('a');await first;});
 const b=client.runComputation(client.generation,async()=>{order.push('b');});
 await new Promise(r=>setImmediate(r));assert.deepEqual(order,['a']);client.revision='changed';release();await a;await assert.rejects(b,/STALE_RESPONSE/);assert.deepEqual(order,['a']);assert.equal(client.queuedComputations,0);
});
test('zone and window controls invalidate curves and downloads until a new revision is compiled',()=>{
 const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',hidden:false,disabled:false,appendChild(){},closest:()=>null});return nodes.get(id);};const main={hidden:false};let cleared=0;
 const profile={...profileWith(1),market:{zone:'NORD',series:'NORD',valuation_mode:'flat'}};
 const ctx=loadFunctions(['updateActionableWarnings','needsEligibilityWarning','invalidateProfileResults','setupProfileResults'],{activeResultProfile:profile,settings:{panel_watts:400},schema:{catalog:{series:['NORD','SARD','PUN'],windows:[['2024-01-01','2025-01-01']],revision:'new'}},optimizerPrefs:{},document:{querySelectorAll:()=>[],querySelector:()=>main,createElement:()=>({})},$,householdClient:{invalidate:()=>cleared++},renderProfileRoofControls:()=>{},renderProfileMarketMetadata:()=> 'market',requestId:0,current:{old:true},activeResultJob:{id:'old'},acceptedInputFingerprint:'old',candidateEvalCache:new Map([['old',{}]]),empiricalCache:{old:{}},markSolarSizingStale:()=>{},markBatterySizingStale:()=>{},clearOptimizerResults:()=>{}});
 ctx.setupProfileResults();assert.equal($('solarDeduction').value,'0');assert.equal($('solarDeduction').disabled,true);for(const id of ['profileMarketZone','profileMarketWindow']){ctx.current={old:true};ctx.activeResultJob={id:'old'};$(id).onchange();assert.equal(ctx.current,null);assert.equal(ctx.activeResultJob,null);assert.equal(main.hidden,true);assert.equal($('ownedReportHtml').disabled,true);}assert.equal(cleared,2);
});
test('owned report download uses its completed job and revision, including a late-body switch',async()=>{
 const downloads=[],paths=[],client={generation:1,revision:'r',waitJob:async id=>{assert.equal(id,'owned');},stale:()=>Error('STALE_RESPONSE'),fetcher:async path=>{paths.push(path);return {ok:true,text:async()=> '# report'};}};
 const ctx=loadFunctions(['downloadOwnedReport'],{activeResultJob:{id:'owned'},householdClient:client,getExportBlockReason:()=>null,download:(...args)=>downloads.push(args)});
 await ctx.downloadOwnedReport('md');assert.equal(paths[0],'/report.md?job_id=owned');assert.equal(downloads[0][0],'# report');
 let release;client.fetcher=async()=>({ok:true,text:()=>new Promise(r=>release=r)});const pending=ctx.downloadOwnedReport('md');await new Promise(r=>setImmediate(r));client.generation++;release('old body');await assert.rejects(pending,/STALE_RESPONSE/);assert.equal(downloads.length,1);
});

test('updateActionableWarnings hides projection checkbox when horizon inside pinned dates and preserves outside-date visibility', () => {
 const nodes = new Map(), $ = id => {
  if (!nodes.has(id)) nodes.set(id, { textContent: '', hidden: false });
  return nodes.get(id);
 };
 const profile = { fiscal: { parameters: { deduction_rate: 0.5, effective_from: '2025-01-01', effective_to: '2046-01-01' } } };
 const settings = { installation_year: 2026, horizon_years: 10, deduction_eligible_confirmed: false, pmg_election: false, unsupported_fiscal_year_acknowledged: false };
 const ctx = loadFunctions(['updateActionableWarnings', 'needsEligibilityWarning'], {
  activeResultProfile: profile, settings, current: { result: {} }, activeResultJob: { id: 'j1' }, $
 });
 ctx.updateActionableWarnings();
 assert.equal($('lbl_unsupported_fiscal_year_acknowledged').hidden, true);
 assert.equal($('lbl_deduction_eligible_confirmed').hidden, false);
 assert.equal($('profileConfirmationsFieldset').hidden, false);
 assert.doesNotMatch($('profileActionableWarning').textContent, /Selected project horizon exceeds pinned fiscal dates/);
 ctx.settings.horizon_years = 25;
 ctx.updateActionableWarnings();
 assert.equal($('lbl_unsupported_fiscal_year_acknowledged').hidden, false);
 assert.match($('profileActionableWarning').textContent, /Selected project horizon exceeds pinned fiscal dates/);
 ctx.settings.unsupported_fiscal_year_acknowledged = true;
 ctx.updateActionableWarnings();
 assert.equal($('lbl_unsupported_fiscal_year_acknowledged').hidden, false);
 assert.doesNotMatch($('profileActionableWarning').textContent, /Selected project horizon exceeds pinned fiscal dates/);
});
