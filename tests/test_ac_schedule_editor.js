'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const SharedScenarioSync=require('../feasibility/web/sync.js');

const appSource=fs.readFileSync(path.join(__dirname,'../feasibility/web/app.js'),'utf8');

function createAppContext(options={}){
 const sharedStore=options.stateStore;
 const elements=new Map();
 function makeEl(tag='div',id=''){
  const classes=new Set();
  const attrs=new Map();
  const listeners=new Map();
  let focused=false;
  let open=false;
  let returnValue='';
  const el={
   tagName:tag.toUpperCase(),
   id:id||'',
   classList:{
    toggle(c,force){if(force===undefined){if(classes.has(c))classes.delete(c);else classes.add(c);}else if(force)classes.add(c);else classes.delete(c);return classes.has(c);},
    add(c){classes.add(c);},remove(c){classes.delete(c);},contains(c){return classes.has(c);}
   },
   hidden:false,textContent:'',innerHTML:'',value:'',dataset:{},style:{},options:[],
   get open(){return open;},
   set open(v){open=Boolean(v);},
   get returnValue(){return returnValue;},
   set returnValue(v){returnValue=String(v);},
   showModal(){open=true;},
   close(){open=false;this.dispatchEvent({type:'close'});},
   get disabled(){const d=attrs.get('disabled');return d===true||d===''||d==='true'||d==='disabled';},
   set disabled(v){if(v)attrs.set('disabled',true);else attrs.delete('disabled');},
   querySelector(sel){
    if(sel.startsWith('#')){
     const targetId=sel.slice(1);
     return elements.get(targetId)||null;
    }
    if(sel==='[data-save-default]'){
     for(const child of elements.values()){if(child.dataset?.saveDefault)return child;}
    }
    if(sel==='[data-reset-setting]'){
     for(const child of elements.values()){if(child.dataset?.resetSetting)return child;}
    }
    return null;
   },
   querySelectorAll(sel){return [];},
   addEventListener(event,fn){
    if(!listeners.has(event))listeners.set(event,[]);
    listeners.get(event).push(fn);
   },
   removeEventListener(event,fn){
    if(listeners.has(event)){
     listeners.set(event,listeners.get(event).filter(f=>f!==fn));
    }
   },
   dispatchEvent(event){
    const list=listeners.get(event.type)||[];
    list.forEach(fn=>fn(event));
   },
   closest(sel){
    if(sel==='[data-reset-setting]'&&this.dataset?.resetSetting)return this;
    if(sel==='[data-remove-window]'&&this.dataset?.removeWindow)return this;
    if(sel==='[data-save-default]'&&this.dataset?.saveDefault)return this;
    if(sel==='[data-control]'&&this.dataset?.control)return this;
    if((sel.includes('acWindowAddBtn')||sel.includes('[data-ac-add-window]'))&&(this.id==='acWindowAddBtn'||this.dataset?.acAddWindow))return this;
    if((sel.includes('copyBoilerWindowsBtn')||sel.includes('copyAcScheduleBtn'))&&(this.id==='copyBoilerWindowsBtn'||this.id==='copyAcScheduleBtn'))return this;
    return null;
   },
   removeAttribute(k){attrs.delete(k);},
   setAttribute(k,v){attrs.set(k,String(v));},
   getAttribute(k){return attrs.get(k)??null;},
   hasAttribute(k){return attrs.has(k);},
   focus(){focused=true;if(typeof doc!=='undefined')doc.activeElement=this;},
   get isFocused(){return focused;},
   set isFocused(v){focused=v;}
  };
  if(id)elements.set(id,el);
  return el;
 }

 const badge={textContent:'',className:''};
 const controlsEl=makeEl('div','controls');
 const statusEl=makeEl('div','status');
 const errorEl=makeEl('div','error');
 errorEl.hidden=true;
 const defaultConfirmEl=makeEl('dialog','defaultConfirm');
 const defaultConfirmTitleEl=makeEl('h2','defaultConfirmTitle');
 const defaultConfirmDescriptionEl=makeEl('p','defaultConfirmDescription');
 const systemSummaryEl=makeEl('div','systemSummary');

 const ui={
  modifiedCountBadge:badge,
  controls:controlsEl,
  status:statusEl,
  error:errorEl,
  defaultConfirm:defaultConfirmEl,
  defaultConfirmTitle:defaultConfirmTitleEl,
  defaultConfirmDescription:defaultConfirmDescriptionEl,
  systemSummary:systemSummaryEl
 };

 const doc={
  activeElement:null,
  getElementById:id=>{
   if(ui[id])return ui[id];
   if(!elements.has(id)){
    elements.set(id,makeEl('div',id));
   }
   return elements.get(id);
  },
  querySelector:sel=>{
   if(sel==='aside'||sel==='nav button.active')return null;
   if(sel.startsWith('#'))return doc.getElementById(sel.slice(1));
   if(sel.startsWith('[data-setting="')){
    const m=sel.match(/\[data-setting="([^"]+)"\]/);
    if(m){for(const el of elements.values()){if(el.dataset?.setting===m[1])return el;}}
   }
   if(sel.startsWith('[data-control="')){
    const m=sel.match(/\[data-control="([^"]+)"\]/);
    if(m){for(const el of elements.values()){if(el.dataset?.control===m[1])return el;}}
   }
   if(sel.includes('.is-invalid')||sel.includes('[aria-invalid="true"]')){
    for(const el of elements.values()){
     if(el.classList?.contains?.('is-invalid')||el.getAttribute?.('aria-invalid')==='true')return el;
    }
   }
   if(sel==='[data-save-default]'){
    for(const el of elements.values()){if(el.dataset?.saveDefault)return el;}
   }
   if(sel==='[data-reset-setting]'){
    for(const el of elements.values()){if(el.dataset?.resetSetting)return el;}
   }
   return null;
  },
  querySelectorAll:sel=>{
   const results=[];
   for(const el of elements.values()){
    if(sel==='[data-setting],[data-slider]'){
     if(el.dataset?.setting||el.dataset?.slider)results.push(el);
    }else if(sel.startsWith('[data-control="')){
     const m=sel.match(/\[data-control="([^"]+)"\]/);
     if(m&&el.dataset?.control===m[1])results.push(el);
    }else if(sel.startsWith('[data-setting="')){
     const m=sel.match(/\[data-setting="([^"]+)"\]/);
     if(m&&el.dataset?.setting===m[1])results.push(el);
    }
   }
   return results;
  },
  addEventListener(){},
  removeEventListener(){}
 };

 const win={addEventListener(){},removeEventListener(){},location:{hash:''},matchMedia:()=>({matches:false}),innerWidth:1024,innerHeight:768};
 win.window=win;
 win.document=doc;

 let evaluateCalls=[];
 let stateCalls=[];
 let defaultsCalls=[];
 let pendingTimers=[];
 let timerSeq=1;
 let pendingEvaluations=[];
 let autoResolveEvaluate=true;

 function fakeSetTimeout(fn,ms){
  const id=timerSeq++;
  pendingTimers.push({id,fn,ms});
  return id;
 }
 function fakeClearTimeout(id){
  const idx=pendingTimers.findIndex(t=>t.id===id);
  if(idx!==-1)pendingTimers.splice(idx,1);
 }
 async function flushTimers(){
  while(pendingTimers.length>0){
   const timers=[...pendingTimers];
   pendingTimers.length=0;
   for(const t of timers){
    const res=t.fn();
    if(res&&typeof res.then==='function')await res;
   }
  }
 }

 let context;
 function getVmVar(name){
  if(!context)return null;
  try{
   return vm.runInContext(`typeof ${name}!=='undefined'?${name}:null`,context);
  }catch(_){
   return null;
  }
 }
 const fallbackDefaults={
  timezone_mode:'civil',
  load_scenario:'electrified',
  planning_heat_pump:false,
  heating_schedule_mode:'scheduled',
  ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
 };
 const fallbackRecords=[
  {name:'timezone_mode',unit:'mapping'},
  {name:'load_scenario',unit:'scenario',group:'loads'},
  {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
  {name:'heating_schedule_mode',unit:'mode',group:'loads'},
  {name:'ac_heating_windows',unit:'windows',group:'loads'}
 ];

 const ctx={
  window:win,
  document:doc,
  localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
  setTimeout:fakeSetTimeout,
  clearTimeout:fakeClearTimeout,
  fetch:async(path,options)=>{
   if(path==='/api/evaluate'){
    const body=JSON.parse(options.body);
    evaluateCalls.push(body);
    if(autoResolveEvaluate){
     return {
      ok:true,
      json:async()=>({
       compute_seconds:0.01,
       result:{settings:body.settings,layout:{kwp:6,panel_count:14},metrics:{},finance:{},energy:{}}
      })
     };
    }
    return new Promise(resolve=>{
     pendingEvaluations.push({
      body,
      resolve:(customResult)=>resolve({
       ok:true,
       json:async()=>({
        compute_seconds:0.01,
        result:customResult||{settings:body.settings,layout:{kwp:6,panel_count:14},metrics:{},finance:{}}
       })
      })
     });
    });
   }
   if(path==='/api/state'||path.startsWith('/api/state?')){
    const body=options.body?JSON.parse(options.body):{};
    stateCalls.push(body);
    const curSchema=getVmVar('schema');
    const curSettings=getVmVar('settings');
    const curQuote=getVmVar('quote');
    const defs=(curSchema?.defaults&&Object.keys(curSchema.defaults).length>0)?curSchema.defaults:fallbackDefaults;
    const recs=(curSchema?.records&&curSchema.records.length>0)?curSchema.records:fallbackRecords;
    const store=sharedStore;
    if(store&&options.body&&store.failStateSaves>0){store.failStateSaves--;return {ok:false,status:500,statusText:'Mock save failure',json:async()=>({error:'Mock save failure'})};}
    if(store&&options.body){store.settings=body.replace?Object.assign({},body.settings||{}):Object.assign({},store.settings||{},body.settings||{});store.quote=body.quote||store.quote||curQuote||{line_items:[]};store.revision='saved_'+stateCalls.length;}
    const currentSettings=store?store.settings:(curSettings?Object.assign({},curSettings,body.settings||{}):(body.settings||{}));
    const unchanged=Boolean(store&&path.includes('?revision=')&&decodeURIComponent(path.split('revision=')[1])===store.revision);
    return {
     ok:true,
     json:async()=>({
      ...(unchanged?{unchanged:true}:{}),
      revision:store?.revision||'rev_1',
      defaults:defs,
      records:recs,
      settings:currentSettings,
      quote:store?.quote||body.quote||curQuote||{line_items:[]}
     })
    };
   }
   if(path==='/api/schema'){
    const curSchema=getVmVar('schema');
    return {
     ok:true,
     json:async()=>((curSchema?.records&&curSchema.records.length>0)?curSchema:{
      defaults:fallbackDefaults,
      records:fallbackRecords
     })
    };
   }
   if(path==='/api/defaults'){
    const body=options.body?JSON.parse(options.body):{};
    defaultsCalls.push(body);
    const curSchema=getVmVar('schema');
    if(body.name==='ac_heating_windows'||body.name==='heating_schedule_mode'){
     const schedDefs={
      ac_heating_windows:body.schedule_settings?.ac_heating_windows||(body.name==='ac_heating_windows'?body.value:curSchema.defaults.ac_heating_windows),
      heating_schedule_mode:body.schedule_settings?.heating_schedule_mode||(body.name==='heating_schedule_mode'?body.value:curSchema.defaults.heating_schedule_mode)
     };
     const schedRecs=[
      {name:'ac_heating_windows',status:'provided',source:'User scenario',unit:'windows'},
      {name:'heating_schedule_mode',status:'provided',source:'User scenario',unit:'mode'}
     ];
     return {
      ok:true,
      json:async()=>({
       name:body.name,
       value:body.value,
       record:schedRecs.find(r=>r.name===body.name),
       defaults:schedDefs,
       records:schedRecs
      })
     };
    }
    return {
     ok:true,
     json:async()=>({
      name:body.name,
      value:body.value,
      record:{name:body.name,status:'provided',source:'User scenario'}
     })
    };
   }
   return {ok:true,json:async()=>({})};
  },
  syncOptimizerEquipmentInputs:()=>{},
  SharedScenarioSync,
  console
 };

 context=vm.createContext(ctx);
 vm.runInContext(appSource,context);
 context.render=()=>{};
 context.setupDayhour=()=>{};
 context.refreshDayhour=()=>{};
 context.refreshBehaviorViews=()=>{};

 return {
  context,
  run:code=>vm.runInContext(code,context),
  doc,
  ui,
  elements,
  evaluateCalls,
  stateCalls,
  defaultsCalls,
  approveConfirm:()=>{
   defaultConfirmEl.returnValue='save';
   defaultConfirmEl.dispatchEvent({type:'close'});
  },
  cancelConfirm:()=>{
   defaultConfirmEl.returnValue='cancel';
   defaultConfirmEl.dispatchEvent({type:'close'});
  },
  pendingTimers,
  flushTimers,
  hasPendingTimer:()=>pendingTimers.length>0,
  setAutoResolveEvaluate:v=>{autoResolveEvaluate=v;},
  resolvePendingEvaluation:(idx=0,res)=>{const p=pendingEvaluations.splice(idx,1)[0];if(p)p.resolve(res);},
  makeEl
 };
}

test('minute parsing: start and end24 edge cases', ()=>{
 const {context}=createAppContext();
 const parse=context.parseAcScheduleTime;
 const format=context.formatAcScheduleMinute;

 // Valid start and end minute boundaries
 assert.equal(parse('00:00',false),0,'start 00:00 is 0');
 assert.equal(parse('00:00',true),0,'end 00:00 is 0');
 assert.equal(parse('23:59',false),1439,'start 23:59 is 1439');
 assert.equal(parse('23:59',true),1439,'end 23:59 is 1439');
 assert.equal(parse('06:30',false),390,'06:30 is 390');
 assert.equal(parse('12:00',true),720,'12:00 is 720');

 // 24:00 is valid for end only, NEVER for start
 assert.equal(parse('24:00',true),1440,'end 24:00 is 1440');
 assert.equal(parse('24:00',false),null,'start 24:00 is rejected');

 // Rejection of out of bounds and non-24:00 overflows
 assert.equal(parse('24:01',true),null,'24:01 is rejected');
 assert.equal(parse('25:00',false),null,'25:00 is rejected');
 assert.equal(parse('12:60',false),null,'12:60 is rejected');
 assert.equal(parse('12:5',false),null,'12:5 requires 2-digit minute');
 assert.equal(parse('8:00',false),null,'8:00 requires 2-digit hour');
 assert.equal(parse('-01:00',false),null,'negative hour rejected');
 assert.equal(parse('',false),null,'empty string rejected');
 assert.equal(parse('   ',false),null,'whitespace string rejected');
 assert.equal(parse('abc',false),null,'non-time rejected');
 assert.equal(parse(null,false),null,'null rejected');
 assert.equal(parse(undefined,false),null,'undefined rejected');
 assert.equal(parse(480,false),null,'numeric primitive rejected');

 // Formatting back to HH:MM strings
 assert.equal(format(0),'00:00');
 assert.equal(format(390),'06:30');
 assert.equal(format(1439),'23:59');
 assert.equal(format(1440),'24:00');
 assert.equal(format(null),'00:00');
 assert.equal(format(undefined),'00:00');
 assert.equal(format(NaN),'00:00');
});

test('union duration calculation: disjoint, overlapping, inactive, overnight, 24:00, and empty', ()=>{
 const {context}=createAppContext();
 const unionMins=context.calculateAcScheduleUnionMinutes;
 const formatUnion=context.formatAcScheduleUnionDuration;

 // 1. Disjoint daytime windows (6-8=120m, 16-20=240m -> 360m = 6h)
 const disjoint=[
  {id:'w1',start_minute:360,end_minute:480},
  {id:'w2',start_minute:960,end_minute:1200}
 ];
 assert.equal(unionMins(disjoint),360);
 assert.equal(formatUnion(unionMins(disjoint)),'6h 00m / day');

 // 2. Overlapping windows: overlap counted strictly once
 // Window 1: 06:00..10:00 (240m), Window 2: 08:00..12:00 (240m)
 // Union is 06:00..12:00 = 360m (not 480m)
 const overlapping=[
  {id:'w1',start_minute:360,end_minute:600},
  {id:'w2',start_minute:480,end_minute:720}
 ];
 assert.equal(unionMins(overlapping),360,'overlapping window union counts overlap once');

 // 3. Inactive window (equal bounds 8:00..8:00 contributes 0 minutes)
 const withInactive=[
  {id:'w1',start_minute:480,end_minute:480}
 ];
 assert.equal(unionMins(withInactive),0,'inactive window contributes 0 duration');
 assert.equal(formatUnion(0),'0h 00m / day');

 // 4. Overnight window (22:00..06:00: 1320..1440=120m + 0..360=360m -> 480m = 8h)
 const overnight=[
  {id:'w1',start_minute:1320,end_minute:360}
 ];
 assert.equal(unionMins(overnight),480,'overnight window wraps midnight accurately');
 assert.equal(formatUnion(480),'8h 00m / day');

 // 5. Overnight window overlapping with morning window
 // 22:00..06:00 (1320..360) and 05:00..08:00 (300..480)
 // Union is 22:00..08:00 = 10h = 600m
 const overnightOverlap=[
  {id:'w1',start_minute:1320,end_minute:360},
  {id:'w2',start_minute:300,end_minute:480}
 ];
 assert.equal(unionMins(overnightOverlap),600,'overnight overlap with morning union counted once');
 assert.equal(formatUnion(600),'10h 00m / day');

 // 6. End at 24:00 (18:00..24:00 = 1080..1440 = 360m = 6h)
 const end24=[
  {id:'w1',start_minute:1080,end_minute:1440}
 ];
 assert.equal(unionMins(end24),360,'end 24:00 contributes to midnight boundary');

 // 7. Empty schedule
 assert.equal(unionMins([]),0,'empty schedule gives 0 union minutes');
 assert.equal(formatUnion(unionMins([])),'0h 00m / day');
});

test('rendering: specialized editor renders without generic numeric input or [object Object]', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'night',start_minute:1320,end_minute:360},
     {id:'inactive',start_minute:600,end_minute:600},
     {id:'evening24',start_minute:1080,end_minute:1440}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads',label:'Space-heating operating windows'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");
 assert.ok(!html.includes('[object Object]'),'must not render [object Object]');
 assert.ok(!html.includes('type="number" data-setting="ac_heating_windows"'),'must not render generic numeric input');

 // Labels and instructions
 assert.ok(html.includes('Window 1'),'renders Window 1 label');
 assert.ok(html.includes('Window 2'),'renders Window 2 label');
 assert.ok(html.includes('Window 3'),'renders Window 3 label');
 assert.ok(html.includes('Window 4'),'renders Window 4 label');
 assert.ok(html.includes('ac-schedule-instructions'),'renders instructions element');

 // Text fields with numeric inputmode
 assert.ok(html.includes('type="text" inputmode="numeric"'),'uses text with inputmode numeric');
 assert.ok(html.includes('placeholder="HH:MM"'),'provides HH:MM placeholder');

 // Badges and status texts
 assert.ok(html.includes('Crosses midnight'),'renders Crosses midnight text for start>end');
 assert.ok(html.includes('Inactive window'),'renders Inactive window text for start==end');
 assert.ok(html.includes('value="24:00"'),'end 24:00 is visible');

 // Summary with mode, timezone and union duration
 assert.ok(html.includes('Mode: Scheduled operating windows'),'mode visible in summary');
 assert.ok(html.includes('Europe/Rome'),'timezone visible in summary');
 assert.ok(html.includes('Permitted heating:'),'permitted heating summary visible');

 // Buttons
 assert.ok(html.includes('id="acWindowAddBtn"'),'renders Add button');
 assert.ok(html.includes('data-remove-window="morning"'),'renders Remove button for row');
 assert.ok(html.includes('aria-label="Add operating window"'),'add button has accessible label');
});

test('canonical output preserves IDs, ordering, inactive equal bounds, and overnight bounds', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'morning_id',start_minute:360,end_minute:480},
     {id:'inactive_id',start_minute:500,end_minute:500},
     {id:'overnight_id',start_minute:1320,end_minute:360}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // Render initializes draft
 run("control(schema.records[0])");

 // Add a window
 run("addAcScheduleWindow()");
 const committed=run("settings.ac_heating_windows");
 assert.equal(committed.length,4);
 assert.equal(committed[0].id,'morning_id','preserves original morning ID');
 assert.equal(committed[1].id,'inactive_id','preserves original inactive ID');
 assert.equal(committed[1].start_minute,500);
 assert.equal(committed[1].end_minute,500,'preserves inactive equal bounds');
 assert.equal(committed[2].id,'overnight_id','preserves original overnight ID');
 assert.equal(committed[2].start_minute,1320);
 assert.equal(committed[2].end_minute,360,'preserves overnight bounds');
 assert.ok(committed[3].id.startsWith('window_'),'new window gets unique stable ID');

 // Only id, start_minute, end_minute keys present in committed canonical array
 for(const w of committed){
  const keys=Object.keys(w).sort();
  assert.deepEqual(keys,['end_minute','id','start_minute'],'only canonical keys persisted');
 }
});

test('invalid draft never calls editSetting, blocks recompute, remains visible, and resumes on correction', ()=>{
 const {evaluateCalls,run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'evening',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 run("control(schema.records[0])");
 const initialRequestId=run("requestId");
 const initialSchedule=run("JSON.stringify(settings.ac_heating_windows)");

 // 1. Type invalid start format in Window 1: '06:99'
 run("handleAcScheduleTimeInput('morning','start','06:99')");

 // Canonical settings MUST NOT be mutated
 assert.equal(run("JSON.stringify(settings.ac_heating_windows)"),initialSchedule,'canonical settings untouched on invalid input');
 assert.equal(run("hasInvalidAcScheduleDraft()"),true,'draft flagged invalid');
 assert.ok(run("requestId")>initialRequestId,'requestId incremented to cancel in-flight evals');

 // Check draft row has errors
 const draft=run("getAcScheduleDraft()");
 assert.equal(draft[0].startText,'06:99');
 assert.ok(draft[0].startError!=null,'startError is populated');

 // 2. Edit Window 2 while Window 1 is invalid: Window 1 invalid text must not be erased
 run("handleAcScheduleTimeInput('evening','end','21:00')");
 const draft2=run("getAcScheduleDraft()");
 assert.equal(draft2[0].startText,'06:99','Window 1 invalid text retained after Window 2 edit');
 assert.equal(draft2[1].endText,'21:00');
 assert.equal(run("JSON.stringify(settings.ac_heating_windows)"),initialSchedule,'canonical settings still untouched');

 // 3. Recompute guard: recompute does nothing while draft is invalid
 run("recompute()");
 assert.equal(evaluateCalls.length,0,'recompute blocked while draft invalid');

 // 4. Retain raw draft across simulated rerenders
 const rerenderHtml=run("control(schema.records[0])");
 assert.ok(rerenderHtml.includes('value="06:99"'),'raw invalid text rendered in input');
 assert.ok(rerenderHtml.includes('aria-invalid="true"'),'aria-invalid set on invalid field');
 assert.ok(rerenderHtml.includes('aria-describedby="ac_window_start_err_morning"'),'aria-describedby set on invalid field');
 assert.ok(rerenderHtml.includes('Invalid input'),'visible badge text explains invalid status');

 // 5. Fix the invalid field to '06:15'
 run("handleAcScheduleTimeInput('morning','start','06:15')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false,'draft becomes valid');
 const updatedSchedule=run("settings.ac_heating_windows");
 assert.equal(updatedSchedule[0].start_minute,375,'canonical setting committed with minute precision');
 assert.equal(updatedSchedule[1].end_minute,1260,'evening window also committed');

 // Recompute guard is lifted
 run("recompute()");
 assert.equal(evaluateCalls.length,1,'eval runs after draft is valid');
});

test('removing an invalid row clears invalid state and commits valid remainder', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'w1',start_minute:360,end_minute:480},
     {id:'w2',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 run("control(schema.records[0])");
 // Make w1 invalid
 run("handleAcScheduleTimeInput('w1','start','invalid')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);

 // Remove invalid row w1
 run("removeAcScheduleWindow('w1')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false,'draft valid after removing invalid row');
 const remaining=run("settings.ac_heating_windows");
 assert.equal(remaining.length,1);
 assert.equal(remaining[0].id,'w2');
});

test('empty schedule array is allowed draft and commits [] without continuous fallback', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'w1',start_minute:360,end_minute:480}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 run("control(schema.records[0])");
 // Remove all rows
 run("removeAcScheduleWindow('w1')");
 const committed=run("settings.ac_heating_windows");
 assert.deepEqual(Array.from(committed),[],'empty schedule committed');
 assert.equal(run("hasInvalidAcScheduleDraft()"),false,'empty schedule is valid draft');

 const html=run("control(schema.records[0])");
 assert.ok(html.includes('No operating windows defined'),'shows empty schedule message');
 assert.ok(!html.includes('24h old model'),'does not invent continuous fallback');
});

test('predictable focus: focus moves correctly after add and remove', ()=>{
 const {doc,run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'row1',start_minute:360,end_minute:480},
     {id:'row2',start_minute:600,end_minute:720},
     {id:'row3',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 run("control(schema.records[0])");

 // Add window: focuses new window start
 run("addAcScheduleWindow()");
 const newId=run("settings.ac_heating_windows[3].id");
 const startEl=doc.getElementById(`ac_window_start_${newId}`);
 assert.equal(startEl.isFocused,true,'new window start input is focused after add');

 // Remove middle row: focuses next remaining row
 run("removeAcScheduleWindow('row2')");
 const nextRemainingStart=doc.getElementById('ac_window_start_row3');
 assert.equal(nextRemainingStart.isFocused,true,'next remaining row is focused after removing middle row');

 // Remove last rows until 0 rows: focuses Add button
 run("removeAcScheduleWindow('row1')");
 run("removeAcScheduleWindow('row3')");
 run(`removeAcScheduleWindow("${newId}")`);
 const addBtn=doc.getElementById('acWindowAddBtn');
 assert.equal(addBtn.isFocused,true,'add button is focused after removing last window');
});

test('continuous mode and planning heat pump preserve saved operating windows', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    planning_heat_pump:false,
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'evening',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'heating_schedule_mode',unit:'mode',group:'loads',bounds:['scheduled','continuous']},
    {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // 1. Initial scheduled mode
 let html=run("control(schema.records[2])");
 assert.ok(!html.includes('disabled'),'inputs enabled in scheduled mode');
 assert.ok(html.includes('Mode: Scheduled operating windows'));

 // 2. Switch to continuous mode
 run("settings.heating_schedule_mode='continuous'");
 html=run("control(schema.records[2])");
 assert.ok(html.includes('disabled'),'inputs disabled in continuous mode');
 assert.ok(html.includes('24h 00m / day (continuous 24h)'),'continuous 24h shown explicitly');
 assert.ok(html.includes('Continuous mode active (24h)'),'continuous note visible');
 assert.equal(run("settings.ac_heating_windows.length"),2,'saved windows preserved in continuous mode');

 // 3. Heat pump planning active: ac_heating_windows is in HP_LEGACY_KEYS and conditionally unavailable
 run("settings.planning_heat_pump=true");
 assert.equal(run("isRecordConditionallyAvailable(schema.records[2])"),false,'conditionally unavailable when heat pump planned');
 const explanation=run("getConditionalUnavailableExplanation('ac_heating_windows',schema.records[2])");
 assert.ok(explanation.includes('heat pump planning is active'),'explains unavailability');
 assert.equal(run("settings.ac_heating_windows.length"),2,'saved windows preserved while heat pump active');

 // 4. Disable heat pump planning: returns to available
 run("settings.planning_heat_pump=false");
 assert.equal(run("isRecordConditionallyAvailable(schema.records[2])"),true);
});

test('cleanSettingsForBackend strips legacy heating hours', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'scop',unit:'multiplier'}
   ]
  };
 `);
 const cleaned=run(`
  cleanSettingsForBackend({
   ac_heating_windows:[{id:'m',start_minute:360,end_minute:480}],
   scop:4.0,
   heating_on_hour:6,
   heating_off_hour:8,
   heating_evening_on_hour:16,
   heating_evening_off_hour:20,
   extra_unknown_key:123
  })
 `);

 assert.deepEqual(Object.keys(cleaned).sort(),['ac_heating_windows','scop']);
 assert.equal(cleaned.heating_on_hour,undefined);
 assert.equal(cleaned.heating_off_hour,undefined);
 assert.equal(cleaned.heating_evening_on_hour,undefined);
 assert.equal(cleaned.heating_evening_off_hour,undefined);
 assert.equal(cleaned.extra_unknown_key,undefined);
});

test('browser load order regression: full sync.js then app.js with schedule editor execute without collision', ()=>{
 const syncCode=fs.readFileSync(path.join(__dirname,'../feasibility/web/sync.js'),'utf8');
 const appCode=fs.readFileSync(path.join(__dirname,'../feasibility/web/app.js'),'utf8');

 function makeEl(tag='div'){
  const classes=new Set();
  return {
   classList:{
    toggle(c,force){if(force===undefined){if(classes.has(c))classes.delete(c);else classes.add(c);}else if(force)classes.add(c);else classes.delete(c);return classes.has(c);},
    add(c){classes.add(c);},remove(c){classes.delete(c);},contains(c){return classes.has(c);}
   },
   hidden:false,textContent:'',innerHTML:'',value:'',dataset:{},
   querySelector:()=>makeEl(),
   querySelectorAll:()=>[],
   addEventListener(){},
   removeEventListener(){},
   closest:()=>null,
   removeAttribute(){},
   setAttribute(){},
   focus(){},
   style:{}
  };
 }
 const win={addEventListener(){},removeEventListener(){},location:{hash:''},matchMedia:()=>({matches:false}),innerWidth:1024,innerHeight:768};
 win.window=win;
 const doc={
  getElementById:id=>makeEl(id),
  querySelector:()=>makeEl(),
  querySelectorAll:()=>[],
  addEventListener(){},
  removeEventListener(){}
 };
 win.document=doc;
 const ctx=vm.createContext({
  window:win,
  document:doc,
  localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
  setTimeout:(fn)=>setTimeout(fn,0),
  clearTimeout:()=>{},
  setInterval:()=>1,
  clearInterval:()=>{},
  fetch:async()=>({ok:true,json:async()=>({})}),
  console,
  structuredClone:global.structuredClone
 });

 vm.runInContext(syncCode,ctx);
 vm.runInContext(appCode,ctx);

 assert.equal(typeof ctx.parseAcScheduleTime,'function');
 assert.equal(typeof ctx.formatAcScheduleMinute,'function');
 assert.equal(typeof ctx.calculateAcScheduleUnionMinutes,'function');
 assert.equal(typeof ctx.renderAcHeatingScheduleControl,'function');
 assert.equal(typeof ctx.handleAcScheduleTimeInput,'function');
 assert.equal(typeof ctx.handleAcScheduleRangeInput,'function');
 assert.equal(typeof ctx.renderAcScheduleTimelineContent,'function');
 assert.equal(typeof ctx.updateAcScheduleTimeline,'function');
 assert.equal(typeof ctx.addAcScheduleWindow,'function');
 assert.equal(typeof ctx.removeAcScheduleWindow,'function');
 assert.equal(typeof ctx.validateAcDemandPct,'function');
 assert.equal(typeof ctx.handleAcHeatingDemandPctInput,'function');
 assert.equal(typeof ctx.hasInvalidAcDemandPctDraft,'function');
 assert.equal(typeof ctx.getAcDemandPctDraft,'function');
 assert.equal(typeof ctx.shouldBlockAcDemandPctRecompute,'function');
 assert.equal(typeof ctx.shouldBlockAcRecompute,'function');
 assert.equal(typeof ctx.markResultsOutdated,'function');
});

test('timezone labels: correctly labels civil Europe/Rome (DST) vs standard fixed UTC+1 respecting settings/default precedence', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    timezone_mode:'civil',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 // 1. civil mode from default
 let html=run("control(schema.records[0])");
 assert.ok(html.includes('Timezone: Europe/Rome (DST)'),'civil mode labels Europe/Rome (DST)');

 // 2. standard mode in settings overrides default
 run("settings.timezone_mode = 'standard'");
 html=run("control(schema.records[0])");
 assert.ok(html.includes('Timezone: Standard fixed UTC+1'),'standard mode in settings labels Standard fixed UTC+1');

 // 3. standard mode in schema default when settings is undefined
 run("delete settings.timezone_mode; schema.defaults.timezone_mode = 'standard'");
 html=run("control(schema.records[0])");
 assert.ok(html.includes('Timezone: Standard fixed UTC+1'),'standard mode default labels Standard fixed UTC+1');

 // 4. settings civil takes precedence over standard default
 run("settings.timezone_mode = 'civil'; schema.defaults.timezone_mode = 'standard'");
 html=run("control(schema.records[0])");
 assert.ok(html.includes('Timezone: Europe/Rome (DST)'),'settings civil takes precedence over standard default');

 // 5. fallback when neither specified
 run("delete settings.timezone_mode; delete schema.defaults.timezone_mode");
 html=run("control(schema.records[0])");
 assert.ok(html.includes('Timezone: Europe/Rome (DST)'),'fallback without setting or default labels Europe/Rome (DST)');
});

test('retained invalid draft: baseline, air-to-water HP, and continuous calculate while preserving draft, returning restores active blocking', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[
    {name:'load_scenario',unit:'scenario',group:'loads'},
    {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
    {name:'heating_schedule_mode',unit:'mode',group:'loads'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);
 run("control(schema.records[3])");

 // Initial evaluate
 await run("recompute()");
 assert.equal(evaluateCalls.length,1);
 const reqIdBefore=run("requestId");

 // 1. Active invalid blocking: invalid input invalidates requestId and blocks schedule/recompute
 run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.ok(run("requestId")>reqIdBefore);
 assert.equal(hasPendingTimer(),false,'no pending timer when invalid in active mode');

 // Calling schedule and recompute are blocked during active invalid state
 run("schedule()");
 assert.equal(hasPendingTimer(),false,'schedule blocked');
 await run("recompute()");
 assert.equal(evaluateCalls.length,1,'recompute blocked; no new evaluate call');

 // 2. Baseline calculation proceeds despite retained invalid draft
 run("editSetting('load_scenario', 'baseline')");
 assert.equal(hasPendingTimer(),true,'schedule allowed for baseline scenario');
 await flushTimers();
 assert.equal(evaluateCalls.length,2,'baseline evaluate executed');
 assert.equal(evaluateCalls[1].settings.load_scenario,'baseline');
 assert.equal(run("hasInvalidAcScheduleDraft()"),true,'draft remains invalid');
 assert.equal(run("getAcScheduleDraft()[0].startText"),'99:00','draft text retained in baseline');

 // 3. Air-to-water heat pump calculation proceeds despite retained invalid draft
 run("editSetting('load_scenario', 'electrified')");
 run("editSetting('planning_heat_pump', true)");
 assert.equal(hasPendingTimer(),true,'schedule allowed for planning_heat_pump');
 await flushTimers();
 assert.equal(evaluateCalls.length,3,'air-to-water HP evaluate executed');
 assert.equal(evaluateCalls[2].settings.planning_heat_pump,true);
 assert.equal(run("hasInvalidAcScheduleDraft()"),true,'draft remains invalid in HP mode');

 // 4. Continuous mode calculation proceeds despite retained invalid draft
 run("editSetting('planning_heat_pump', false)");
 run("editSetting('heating_schedule_mode', 'continuous')");
 assert.equal(hasPendingTimer(),true,'schedule allowed for continuous mode');
 await flushTimers();
 assert.equal(evaluateCalls.length,4,'continuous mode evaluate executed');
 assert.equal(evaluateCalls[3].settings.heating_schedule_mode,'continuous');
 assert.equal(run("hasInvalidAcScheduleDraft()"),true,'draft remains invalid in continuous mode');

 // 5. Returning to active invalid blocking
 run("editSetting('heating_schedule_mode', 'scheduled')");
 assert.equal(hasPendingTimer(),false,'returning to scheduled active mode blocks schedule');
 await run("recompute()");
 assert.equal(evaluateCalls.length,4,'recompute blocked in scheduled active mode');
 const recheckedHtml=run("control(schema.records[3])");
 assert.ok(recheckedHtml.includes('value="99:00"'),'invalid draft retained on return');
 assert.ok(recheckedHtml.includes('badge-invalid'),'invalid badge retained on return');
});

test('recovery paths: automatic recovery on unchanged canonical correction, and single schedule on changed correction', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[
    {name:'load_scenario',unit:'scenario',group:'loads'},
    {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
    {name:'heating_schedule_mode',unit:'mode',group:'loads'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);
 run("control(schema.records[3])");
 await run("recompute()");
 assert.equal(evaluateCalls.length,1);

 // Enter invalid time
 run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(hasPendingTimer(),false);

 // Correct invalid -> valid to the SAME canonical value (06:00 -> 360m)
 run("handleAcScheduleTimeInput('w1', 'start', '06:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false,'draft valid again');
 assert.equal(hasPendingTimer(),true,'automatic recovery scheduled recompute timer for unchanged canonical value');
 await flushTimers();
 assert.equal(evaluateCalls.length,2,'recompute executed via automatic recovery');
 assert.equal(evaluateCalls[1].settings.ac_heating_windows[0].start_minute,360);

 // Enter invalid time again
 run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(hasPendingTimer(),false);

 // Correct invalid -> valid to CHANGED canonical value (06:30 -> 390m)
 run("handleAcScheduleTimeInput('w1', 'start', '06:30')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false);
 assert.equal(run("settings.ac_heating_windows[0].start_minute"),390);
 assert.equal(hasPendingTimer(),true,'timer scheduled for changed value');
 assert.equal(app.pendingTimers.length,1,'exactly one schedule timer (no duplicate evaluation scheduling)');
 await flushTimers();
 assert.equal(evaluateCalls.length,3,'recompute executed for changed value');
 assert.equal(evaluateCalls[2].settings.ac_heating_windows[0].start_minute,390);
});

test('stale in-flight evaluate response cannot become accepted during active invalid state', async ()=>{
 const app=createAppContext();
 const {run,setAutoResolveEvaluate,resolvePendingEvaluation}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);
 run("control(schema.records[0])");

 // Set autoResolve to false to keep evaluate in-flight
 setAutoResolveEvaluate(false);
 const recomputePromise=run("recompute()");
 assert.equal(run("$('status')?.textContent"),'Computing…');

 // User types invalid text while evaluate is in-flight
 run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(run("$('status')?.textContent"),'Inputs changed…');

 // Server now responds to the earlier in-flight request
 resolvePendingEvaluation(0,{
  settings:run("settings"),
  layout:{kwp:99,panel_count:200},
  metrics:{npv_eur:99999},
  finance:{},
  energy:{}
 });
 await recomputePromise;

 // Stale response was rejected
 assert.equal(run("current"),null,'stale in-flight response was rejected and not accepted as current');
 assert.equal(run("$('status')?.textContent"),'Inputs changed…');
});

test('timeline hour labels and positions: 00:00 to 24:00 with marked hours', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");
 assert.ok(html.includes('id="acScheduleTimeline"'),'renders timeline container with ID');
 assert.ok(html.includes('role="region"'),'timeline has region role');
 assert.ok(html.includes('aria-label="AC heating schedule 24-hour timeline"'),'timeline has accessible label');

 // Hour scale ruler marks
 assert.ok(html.includes('data-hour="0"'),'has 00:00 hour mark');
 assert.ok(html.includes('data-hour="6"'),'has 06:00 hour mark');
 assert.ok(html.includes('data-hour="12"'),'has 12:00 hour mark');
 assert.ok(html.includes('data-hour="18"'),'has 18:00 hour mark');
 assert.ok(html.includes('data-hour="24"'),'has 24:00 hour mark');

 // Percentage positions
 assert.ok(html.includes('style="left:0.00%;"'),'00:00 at 0%');
 assert.ok(html.includes('style="left:25.00%;"'),'06:00 at 25%');
 assert.ok(html.includes('style="left:50.00%;"'),'12:00 at 50%');
 assert.ok(html.includes('style="left:75.00%;"'),'18:00 at 75%');
 assert.ok(html.includes('style="left:100.00%;"'),'24:00 at 100%');
});

test('timeline midnight split: overnight windows split accurately into late and early segments', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[{id:'night',start_minute:1320,end_minute:360}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");
 // Late segment (22:00 to 24:00): left = (1320/1440)*100 = 91.6667%, width = (120/1440)*100 = 8.3333%
 assert.ok(html.includes('data-timeline-split="late"'),'renders late segment attribute');
 assert.ok(html.includes('style="left:91.6667%;width:8.3333%;"'),'late segment exact left and width');
 assert.ok(html.includes('W1 (late): 22:00–24:00'),'late segment non-color-only text label');

 // Early segment (00:00 to 06:00): left = 0%, width = (360/1440)*100 = 25.0000%
 assert.ok(html.includes('data-timeline-split="early"'),'renders early segment attribute');
 assert.ok(html.includes('style="left:0%;width:25.0000%;"'),'early segment exact left and width');
 assert.ok(html.includes('W1 (early): 00:00–06:00'),'early segment non-color-only text label');
});

test('timeline overlap and union: user windows remain distinct and union duration is not double-counted', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'w1',start_minute:360,end_minute:600},
     {id:'w2',start_minute:480,end_minute:720}
    ]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");

 // Individual window tracks are preserved separately (not merged away!)
 assert.ok(html.includes('data-window-id="w1"'),'Window 1 track preserved');
 assert.ok(html.includes('data-window-id="w2"'),'Window 2 track preserved');
 assert.ok(html.includes('W1: 06:00–10:00 (4h 00m)'),'Window 1 segment distinct');
 assert.ok(html.includes('W2: 08:00–12:00 (4h 00m)'),'Window 2 segment distinct');

 // Union overview track shows 06:00 to 12:00 (360 to 720) with 6h 00m total, not 8h
 assert.ok(html.includes('data-timeline-track="union"'),'union track present');
 assert.ok(html.includes('style="left:25.0000%;width:25.0000%;"'),'union segment spans 06:00 to 12:00');
 assert.ok(html.includes('Permitted: 06:00–12:00 (6h 00m)'),'union label displays accurate non-double-counted duration');
});

test('timeline inactive and all-day window representation', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'inactive_win',start_minute:480,end_minute:480},
     {id:'allday_win',start_minute:0,end_minute:1440}
    ]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");

 // Inactive window representation: visible marker anchored at minute without 0% width segment box and readable status in track header
 assert.ok(html.includes('data-inactive="true"'),'inactive window has data-inactive marker');
 assert.ok(html.includes('ac-timeline-marker-inactive'),'inactive window renders distinct marker element');
 assert.ok(html.includes('style="left:33.3333%;"'),'inactive window marker anchored at 08:00 (480/1440=33.3333%)');
 assert.ok(!html.includes('ac-timeline-segment-inactive'),'does not use zero-width segment box inheriting segment padding');
 assert.ok(!html.includes('width:0%'),'does not emit zero-width segment geometry for inactive marker');
 assert.ok(html.includes('Inactive (0h 00m)'),'inactive status rendered as readable visible text in track header');

 // Equality => zero permitted duration semantics preserved
 assert.ok(html.includes('Permitted: 00:00–24:00 (24h 00m)'),'union only reflects positive duration of all-day window');

 // All-day window representation: 100% width and all-day label
 assert.ok(html.includes('ac-timeline-segment-allday'),'renders all-day segment class');
 assert.ok(html.includes('style="left:0.0000%;width:100.0000%;"'),'all-day segment spans 100%');
 assert.ok(html.includes('W2: All day (00:00–24:00)'),'all-day window has clear label');
});

test('native range controls: bounds, minute step, explicit accessible labels, and HH:MM values', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");

 // Start range control
 assert.ok(html.includes('id="ac_window_start_range_w1"'),'start range slider ID');
 assert.ok(html.includes('data-ac-range-type="start"'),'data-ac-range-type start');
 assert.ok(html.includes('min="0" max="1439" step="1"'),'start range accepts 0 to 1439 at minute step');
 assert.ok(html.includes('aria-label="Window 1 start time range"'),'explicit window and boundary start accessible name');
 assert.ok(html.includes('aria-valuemin="0" aria-valuemax="1439" aria-valuenow="360" aria-valuetext="06:00"'),'start range has accessible value semantics');

 // End range control (accepts up to 1440 / 24:00)
 assert.ok(html.includes('id="ac_window_end_range_w1"'),'end range slider ID');
 assert.ok(html.includes('data-ac-range-type="end"'),'data-ac-range-type end');
 assert.ok(html.includes('min="0" max="1440" step="1"'),'end range accepts 0 to 1440 at minute step');
 assert.ok(html.includes('aria-label="Window 1 end time range"'),'explicit window and boundary end accessible name');
 assert.ok(html.includes('aria-valuemin="0" aria-valuemax="1440" aria-valuenow="480" aria-valuetext="08:00"'),'end range has accessible value semantics');
});

test('range edits and field edits update same canonical array with minute precision and keep timeline in sync', ()=>{
 const {run,doc}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 // 1. Edit start boundary using real event-delegated controls.oninput to minute 375 (06:15)
 const controlsEl=doc.getElementById('controls');
 controlsEl.oninput({
  target:{
   dataset:{acWindowId:'w1',acRangeType:'start'},
   value:'375'
  }
 });
 assert.equal(run("settings.ac_heating_windows[0].start_minute"),375,'canonical start_minute updated by range');
 assert.equal(run("settings.ac_heating_windows[0].end_minute"),480);
 const textEl=doc.getElementById('ac_window_start_w1');
 assert.equal(textEl.value,'06:15','text input updated to HH:MM');
 const startRangeEl=doc.getElementById('ac_window_start_range_w1');
 assert.equal(startRangeEl.value,'375','range value updated');
 assert.equal(startRangeEl.getAttribute('aria-valuetext'),'06:15','range aria-valuetext updated');

 // Actual timeline-content update assertion after range edit
 const tl1=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tl1.includes('Permitted: 06:15–08:00 (1h 45m)'),'timeline union reflects range edit');
 assert.ok(tl1.includes('W1: 06:15–08:00 (1h 45m)'),'window 1 track reflects range edit');
 assert.ok(tl1.includes('style="left:26.0417%;width:7.2917%;"'),'timeline segment geometry reflects 06:15 to 08:00');

 // 2. Edit end boundary using text field to '09:22' (562m)
 run("handleAcScheduleTimeInput('w1', 'end', '09:22')");
 assert.equal(run("settings.ac_heating_windows[0].end_minute"),562,'canonical end_minute updated by text input');
 const endRangeEl=doc.getElementById('ac_window_end_range_w1');
 assert.equal(endRangeEl.value,'562','end range slider updated by text input');
 assert.equal(endRangeEl.getAttribute('aria-valuetext'),'09:22');

 // Actual timeline-content update assertion after field edit
 const tl2=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tl2.includes('Permitted: 06:15–09:22 (3h 07m)'),'timeline union reflects field edit');
 assert.ok(tl2.includes('W1: 06:15–09:22 (3h 07m)'),'window 1 track reflects field edit');

 // 3. Set end to 24:00 (1440) using range input
 run("handleAcScheduleRangeInput('w1', 'end', 1440)");
 assert.equal(run("settings.ac_heating_windows[0].end_minute"),1440,'canonical end 1440 committed');
 const endTextEl=doc.getElementById('ac_window_end_w1');
 assert.equal(endTextEl.value,'24:00','text input formatted as 24:00');

 // Actual timeline-content update assertion after 24:00 range edit
 const tl3=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tl3.includes('Permitted: 06:15–24:00 (17h 45m)'),'timeline union reflects 24:00 end');
 assert.ok(tl3.includes('W1: 06:15–24:00 (17h 45m)'),'window 1 track reflects 24:00 end');

 // 4. Start clamp at 1439: start boundary cannot be 1440 (24:00 end only)
 run("handleAcScheduleRangeInput('w1', 'start', 1440)");
 assert.equal(run("settings.ac_heating_windows[0].start_minute"),1439,'start minute clamped to 1439 (23:59)');
 const clampedStartText=doc.getElementById('ac_window_start_w1');
 assert.equal(clampedStartText.value,'23:59','start formatted as 23:59');

 // Actual timeline-content update assertion after clamp
 const tl4=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tl4.includes('Permitted: 23:59–24:00 (0h 01m)'),'timeline reflects 1-minute interval at day boundary');
 assert.ok(tl4.includes('W1: 23:59–24:00 (0h 01m)'),'window 1 track reflects 1-minute interval at day boundary');
});

test('invalid-draft retention: range controls are disabled while draft has invalid text and do not discard invalid drafts', ()=>{
 const {run,doc}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'w1',start_minute:360,end_minute:480},
     {id:'w2',start_minute:600,end_minute:720}
    ]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 run("control(schema.records[0])");
 const initialCanon=JSON.stringify(run("settings.ac_heating_windows"));

 // Enter invalid text in Window 1
 run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);

 // Range controls are disabled
 const sRange1=doc.getElementById('ac_window_start_range_w1');
 assert.equal(sRange1.disabled,true,'range control disabled while invalid draft exists');

 // Attempting range input on Window 2 while Window 1 is invalid is blocked
 run("handleAcScheduleRangeInput('w2', 'end', 800)");
 assert.equal(JSON.stringify(run("settings.ac_heating_windows")),initialCanon,'settings untouched by range input during invalid draft');
 const draft=run("getAcScheduleDraft()");
 assert.equal(draft[0].startText,'99:00','invalid draft text preserved');
 assert.equal(draft[1].endText,'12:00','other row end text untouched');

 // Correct Window 1: range controls re-enabled and settings commit
 run("handleAcScheduleTimeInput('w1', 'start', '06:30')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false);
 assert.equal(sRange1.disabled,false,'range control re-enabled after correction');
 assert.equal(run("settings.ac_heating_windows[0].start_minute"),390,'canonical schedule updated');
});

test('continuous mode and planning heat pump: controls disabled, saved windows preserved, and continuous 24h distinguished', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    planning_heat_pump:false,
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[
    {name:'heating_schedule_mode',unit:'mode',group:'loads',bounds:['scheduled','continuous']},
    {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // 1. Continuous mode active
 run("settings.heating_schedule_mode='continuous'");
 const html=run("control(schema.records[2])");

 // Controls disabled
 assert.ok(html.includes('disabled'),'controls disabled in continuous mode');
 // Saved windows preserved
 assert.equal(run("settings.ac_heating_windows.length"),1,'saved windows preserved in settings');
 // Timeline distinguishes continuous 24h operation from retained windows
 assert.ok(html.includes('Continuous 24h heating'),'timeline union track shows continuous 24h heating');
 assert.ok(html.includes('Retained Window 1'),'saved windows labeled as retained');
 assert.ok(html.includes('is-retained'),'retained window track marked with is-retained modifier');

 // Range inputs blocked in continuous mode
 const canonBefore=JSON.stringify(run("settings.ac_heating_windows"));
 run("handleAcScheduleRangeInput('w1', 'start', 500)");
 assert.equal(JSON.stringify(run("settings.ac_heating_windows")),canonBefore,'range input does nothing in continuous mode');
});

test('add and remove window render fresh timeline data via real application paths', ()=>{
 const {run,doc}=createAppContext();
 run(`
  schema={
   defaults:{
    timezone_mode:'civil',
    load_scenario:'electrified',
    heating_schedule_mode:'scheduled',
    planning_heat_pump:false,
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const tlInitial=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tlInitial.includes('data-window-id="w1"'),'initial timeline displays w1');
 assert.ok(tlInitial.includes('Permitted: 06:00–08:00 (2h 00m)'));

 // Add window via real application click path on controls
 const controlsEl=doc.getElementById('controls');
 controlsEl.onclick({ target: { id: 'acWindowAddBtn', dataset: { acAddWindow: 'true' } } });

 assert.equal(run("settings.ac_heating_windows.length"),2,'canonical array has 2 windows after add');
 const tlAfterAdd=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tlAfterAdd.includes('data-window-id="w1"'),'timeline retains w1');
 assert.ok(tlAfterAdd.includes('data-window-id="window_1"'),'timeline renders fresh added window track');
 assert.ok(tlAfterAdd.includes('Inactive (0h 00m)'),'freshly added 00:00–00:00 window renders as inactive in track header');

 // Remove window w1 via real application click path on controls
 controlsEl.onclick({ target: { dataset: { removeWindow: 'w1' } } });

 assert.equal(run("settings.ac_heating_windows.length"),1,'canonical array has 1 window after remove');
 const tlAfterRemove=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(!tlAfterRemove.includes('data-window-id="w1"'),'timeline no longer contains removed window w1');
 assert.ok(tlAfterRemove.includes('data-window-id="window_1"'),'timeline retains window_1');
});

test('per-schedule reset renders fresh timeline data via real application path', ()=>{
 const {run,doc}=createAppContext();
 run(`
  schema={
   defaults:{
    timezone_mode:'civil',
    load_scenario:'electrified',
    heating_schedule_mode:'scheduled',
    planning_heat_pump:false,
    ac_heating_windows:[{id:'def1',start_minute:360,end_minute:480}]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 // Edit window to 10:00–12:00
 run("handleAcScheduleTimeInput('def1', 'start', '10:00')");
 run("handleAcScheduleTimeInput('def1', 'end', '12:00')");
 assert.equal(run("isRecordModified('ac_heating_windows')"),true);

 const tlModified=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tlModified.includes('Permitted: 10:00–12:00 (2h 00m)'),'timeline displays modified window');
 assert.ok(!tlModified.includes('Permitted: 06:00–08:00'),'timeline no longer displays default window');

 // Reset via real application click path on controls
 const controlsEl=doc.getElementById('controls');
 const resetTarget={
  dataset:{resetSetting:'ac_heating_windows'},
  closest(sel){return sel.includes('data-reset-setting')?this:null;}
 };
 controlsEl.onclick({ target: resetTarget });

 assert.equal(run("isRecordModified('ac_heating_windows')"),false,'modified state cleared');
 assert.deepEqual(run("settings.ac_heating_windows"),run("schema.defaults.ac_heating_windows"),'canonical settings restored to defaults');

 const tlReset=doc.getElementById('acScheduleTimeline').innerHTML;
 assert.ok(tlReset.includes('Permitted: 06:00–08:00 (2h 00m)'),'timeline displays fresh default data after reset');
 assert.ok(!tlReset.includes('Permitted: 10:00–12:00'),'timeline cleared modified window after reset');
});

test('timeline overnight end 00:00 skips zero-duration split segment and handles boundary/tiny cases', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'overnight_midnight',start_minute:1320,end_minute:0},
     {id:'edge_start',start_minute:0,end_minute:0},
     {id:'edge_end',start_minute:1439,end_minute:1439}
    ]
   },
   records:[{name:'ac_heating_windows',unit:'windows',group:'loads'}]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");

 // Overnight window ending at 00:00:
 // Late segment (22:00 to 24:00) is emitted
 assert.ok(html.includes('data-timeline-split="late"'),'emits late split segment');
 assert.ok(html.includes('style="left:91.6667%;width:8.3333%;"'),'late segment geometry exact');
 assert.ok(html.includes('W1 (late): 22:00–24:00'),'late segment label');

 // Early segment with 0% width at midnight is SKIPPED
 assert.ok(!html.includes('data-timeline-split="early"'),'zero-duration early segment skipped at midnight');
 assert.ok(!html.includes('width:0.0000%'),'no width:0.0000% segment emitted');

 // Union semantics: only 22:00–24:00 (2h 00m)
 assert.ok(html.includes('Permitted: 22:00–24:00 (2h 00m)'),'union reflects actual late segment');

 // Inactive at 00:00 (edge start)
 assert.ok(html.includes('data-edge="start"'),'anchors edge start without negative translation');
 assert.ok(html.includes('style="left:0.0000%;"'),'anchored at 00:00');

 // Inactive near 24h boundary at 23:59 (edge end)
 assert.ok(html.includes('data-edge="end"'),'anchors edge end with -100% translation near 24h boundary');
 assert.ok(html.includes('style="left:99.9306%;"'),'anchored at 23:59');

 // Both inactive windows have readable status in track header
 assert.ok(html.includes('00:00 – 00:00 <span class="ac-timeline-track-status ac-timeline-status-inactive">Inactive (0h 00m)</span>'));
 assert.ok(html.includes('23:59 – 23:59 <span class="ac-timeline-track-status ac-timeline-status-inactive">Inactive (0h 00m)</span>'));

 // Positive tiny-duration overnight (e.g. 22:00 to 00:01)
 run(`
  acScheduleDraft=null;
  settings.ac_heating_windows=[
   {id:'tiny_early',start_minute:1320,end_minute:1}
  ];
 `);
 const htmlTiny=run("control(schema.records[0])");
 assert.ok(htmlTiny.includes('data-timeline-split="early"'),'positive 1-min early segment is preserved');
 assert.ok(htmlTiny.includes('style="left:0%;width:0.0694%;"'),'1-min early segment has exact geometry (1/1440=0.0694%)');
 assert.ok(htmlTiny.includes('W1 (early): 00:00–00:01'),'positive early segment label');
 assert.ok(htmlTiny.includes('Permitted: 00:00–00:01 (0h 01m)'),'union includes early segment');
 assert.ok(htmlTiny.includes('Permitted: 22:00–24:00 (2h 00m)'),'union includes late segment');
});

test('ac_heating_additional_demand_pct rendering: proper numeric step/min/no max even with sensitivity choices', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0
   },
   records:[
    {
     name:'ac_heating_additional_demand_pct',
     unit:'%',
     group:'loads',
     label:'Additional AC heating demand',
     choices:[0,25,50],
     bounds:[0,null]
    }
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 const html=run("control(schema.records[0])");
 // 1. Must NOT render as a select control despite choices
 assert.ok(!html.includes('<select'),'must not render select control despite illustrative choices');
 assert.ok(!html.includes('<option'),'must not render options');

 // 2. Must render finite nonnegative numeric input with step="any", min="0", and NO max attribute
 assert.ok(html.includes('type="number"'),'renders number input');
 assert.ok(html.includes('data-setting="ac_heating_additional_demand_pct"'),'sets data-setting attribute');
 assert.ok(html.includes('id="ac_heating_additional_demand_pct"'),'sets id attribute');
 assert.ok(html.includes('min="0"'),'enforces min="0"');
 assert.ok(html.includes('step="any"'),'uses step="any" for fractional precision');
 assert.ok(!html.includes('max='),'must not impose arbitrary upper cap or max attribute');

 // 3. Value defaults to 0 and title/unit are preserved
 assert.ok(html.includes('value="0"'),'defaults to 0');
 assert.ok(html.includes('<label>Additional AC heating demand</label>'),'preserves title');
 assert.ok(html.includes('<span class="unit">%</span>'),'preserves percentage unit');

 // 4. Modified marker and reset/save-default affordances
 assert.ok(!html.includes('is-modified'),'unmodified when matching default');
 assert.ok(html.includes('data-reset-setting="ac_heating_additional_demand_pct"'),'has reset button');
 assert.ok(html.includes('data-save-default="ac_heating_additional_demand_pct"'),'has save default button');

 // 5. Error container present with matching aria-describedby when error exists
 assert.ok(html.includes('id="ac_heating_additional_demand_pct_error"'),'has accessible error container');
 assert.ok(html.includes('hidden'),'error container initially hidden');
});

test('ac_heating_additional_demand_pct: arbitrary fractional (12.345) and large (125) values accepted via controls.oninput without rounding or clamping', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls,makeEl,doc}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 const errEl=makeEl('div','ac_heating_additional_demand_pct_error');

 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'continuous'
   },
   records:[
    {
     name:'ac_heating_additional_demand_pct',
     unit:'%',
     group:'loads',
     choices:[0,25,50],
     bounds:[0,50]
    }
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const controlsEl=doc.getElementById('controls');

 // 1. Enter arbitrary fractional value '12.345'
 inputEl.value='12.345';
 controlsEl.oninput({target:inputEl});

 assert.equal(run('settings.ac_heating_additional_demand_pct'),12.345,'fractional value 12.345 accepted without rounding');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),false,'draft is valid');
 assert.equal(inputEl.getAttribute('aria-invalid'),null,'no aria-invalid');
 assert.equal(errEl.hidden,true,'error element hidden');
 assert.equal(hasPendingTimer(),true,'recompute scheduled');
 await flushTimers();
 assert.equal(evaluateCalls.length,1);
 assert.equal(evaluateCalls[0].settings.ac_heating_additional_demand_pct,12.345,'evaluated with fractional value');

 // 2. Enter large value '125' (exceeding sensitivity bounds [0, 50])
 inputEl.value='125';
 controlsEl.oninput({target:inputEl});

 assert.equal(run('settings.ac_heating_additional_demand_pct'),125,'large value 125 accepted without clamping to bounds');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),false,'draft is valid');
 await flushTimers();
 assert.equal(evaluateCalls.length,2);
 assert.equal(evaluateCalls[1].settings.ac_heating_additional_demand_pct,125,'evaluated with 125');
});

test('ac_heating_additional_demand_pct: negative, nonfinite, and empty inputs rejected, canonical preserved, aria validation set', async ()=>{
 const app=createAppContext();
 const {run,hasPendingTimer,evaluateCalls,makeEl,doc}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 const errEl=makeEl('div','ac_heating_additional_demand_pct_error');

 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled'
   },
   records:[
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads',choices:[0,25,50]}
   ]
  };
  settings=deepClone(schema.defaults);
  settings.ac_heating_additional_demand_pct=25;
  renderControls();
 `);

 const controlsEl=doc.getElementById('controls');

 // Initial recompute with canonical 25
 await run("recompute()");
 assert.equal(evaluateCalls.length,1);
 assert.equal(evaluateCalls[0].settings.ac_heating_additional_demand_pct,25);

 // 1. Negative input: '-5'
 inputEl.value='-5';
 controlsEl.oninput({target:inputEl});

 assert.equal(run('settings.ac_heating_additional_demand_pct'),25,'canonical percentage preserved on negative input');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true,'draft flagged invalid');
 assert.equal(run('getAcDemandPctDraft()'),'-5','retains raw field draft');
 assert.equal(inputEl.getAttribute('aria-invalid'),'true','aria-invalid set');
 assert.equal(inputEl.getAttribute('aria-describedby'),'ac_heating_additional_demand_pct_error');
 assert.equal(inputEl.classList.contains('is-invalid'),true,'is-invalid class added');
 assert.equal(errEl.hidden,false,'error message visible');
 assert.match(errEl.textContent,/0 or greater/i,'informative accessible error');
 assert.equal(hasPendingTimer(),false,'schedule blocked in active mode');
 await run("recompute()");
 assert.equal(evaluateCalls.length,1,'recompute blocked; no evaluation dispatch');

 // 2. Empty input: ''
 inputEl.value='';
 controlsEl.oninput({target:inputEl});

 assert.equal(run('settings.ac_heating_additional_demand_pct'),25,'canonical percentage preserved on empty input (not coerced to 0)');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true);
 assert.equal(run('getAcDemandPctDraft()'),'');
 assert.equal(inputEl.getAttribute('aria-invalid'),'true');
 assert.equal(errEl.hidden,false);
 assert.equal(hasPendingTimer(),false);

 // 3. Nonfinite input: 'abc'
 inputEl.value='abc';
 controlsEl.oninput({target:inputEl});

 assert.equal(run('settings.ac_heating_additional_demand_pct'),25,'canonical percentage preserved on nonfinite input');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true);
 assert.equal(run('getAcDemandPctDraft()'),'abc');
 assert.equal(inputEl.getAttribute('aria-invalid'),'true');
 assert.equal(errEl.hidden,false);
});

test('ac_heating_additional_demand_pct: aria validation and draft retention across irrelevant rerenders/mode switches', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled'
   },
   records:[
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads',choices:[0,25,50]},
    {name:'heating_schedule_mode',unit:'mode',group:'loads',choices:['scheduled','continuous']}
   ]
  };
  settings=deepClone(schema.defaults);
  settings.ac_heating_additional_demand_pct=15;
 `);

 // Render initially
 let html=run("control(schema.records[0])");
 assert.ok(html.includes('value="15"'));
 assert.ok(!html.includes('aria-invalid="true"'));

 // Enter invalid draft
 run("handleAcHeatingDemandPctInput('-9.99')");
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);

 // Rerender control (e.g. after mode switch or filter change)
 html=run("control(schema.records[0])");
 assert.ok(html.includes('value="-9.99"'),'preserves invalid draft text in value attribute');
 assert.ok(html.includes('aria-invalid="true"'),'preserves aria-invalid attribute');
 assert.ok(html.includes('aria-describedby="ac_heating_additional_demand_pct_error"'),'preserves aria-describedby');
 assert.ok(html.includes('is-invalid'),'preserves is-invalid class');
 assert.ok(!html.includes('id="ac_heating_additional_demand_pct_error" class="hp-error-message ac-field-error" hidden'),'error element is not hidden');

 // Mode switch from scheduled to continuous preserves invalid draft
 run("settings.heating_schedule_mode='continuous'");
 html=run("control(schema.records[0])");
 assert.ok(html.includes('value="-9.99"'),'draft preserved across mode switch to continuous');
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);
});

test('ac_heating_additional_demand_pct scoped blocking: inactive baseline/HP calculate while active AC and continuous wait for correction', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls,makeEl,doc}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 makeEl('div','ac_heating_additional_demand_pct_error');

 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'continuous'
   },
   records:[
    {name:'load_scenario',unit:'scenario',group:'loads'},
    {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
    {name:'heating_schedule_mode',unit:'mode',group:'loads'},
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads',choices:[0,25,50]}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const controlsEl=doc.getElementById('controls');

 // 1. Active continuous mode blocked with invalid percentage draft
 inputEl.value='-12';
 controlsEl.oninput({target:inputEl});
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true);
 assert.equal(hasPendingTimer(),false,'continuous active AC blocks recompute timer on invalid percentage');
 await run("recompute()");
 assert.equal(evaluateCalls.length,0,'recompute blocked in continuous active AC');

 // 2. Baseline scenario allows calculation while retaining invalid percentage draft
 run("editSetting('load_scenario', 'baseline')");
 assert.equal(hasPendingTimer(),true,'schedule allowed in baseline scenario');
 await flushTimers();
 assert.equal(evaluateCalls.length,1,'baseline evaluate executed');
 assert.equal(evaluateCalls[0].settings.load_scenario,'baseline');
 assert.equal(evaluateCalls[0].settings.ac_heating_additional_demand_pct,0,'sends valid canonical percentage');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true,'invalid percentage draft retained');

 // 3. Planning heat pump allows calculation while retaining invalid percentage draft
 run("editSetting('load_scenario', 'electrified')");
 run("editSetting('planning_heat_pump', true)");
 assert.equal(hasPendingTimer(),true,'schedule allowed when planning_heat_pump is true');
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true,'draft remains retained');
 await flushTimers();
 assert.equal(evaluateCalls.length,2,'planning_heat_pump evaluate executed');
 assert.equal(evaluateCalls[1].settings.planning_heat_pump,true);
 assert.equal(run('hasInvalidAcDemandPctDraft()'),true,'draft remains retained');

 // 4. Returning to active AC (planning_heat_pump: false) restores active blocking
 run("editSetting('planning_heat_pump', false)");
 assert.equal(hasPendingTimer(),false,'returning to active AC restores blocking');
 await run("recompute()");
 assert.equal(evaluateCalls.length,2,'recompute blocked in active AC');
});

test('ac_heating schedule draft vs continuous mode: continuous not blocked solely for invalid window text', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'continuous',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],
    ac_heating_additional_demand_pct:20
   },
   records:[
    {name:'heating_schedule_mode',unit:'mode',group:'loads'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'},
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // Window text has invalid format '99:00', but percentage is valid 20
 run("handleAcScheduleTimeInput('w1', 'start', '99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false);

 // In continuous mode, window text errors do NOT block recompute
 run("schedule()");
 assert.equal(hasPendingTimer(),true,'continuous mode is not blocked by invalid window draft');
 await flushTimers();
 assert.equal(evaluateCalls.length,1,'continuous recompute executed');
 assert.equal(evaluateCalls[0].settings.heating_schedule_mode,'continuous');
 assert.equal(evaluateCalls[0].settings.ac_heating_additional_demand_pct,20);

 // Switch to scheduled mode: now blocked by the window text
 run("editSetting('heating_schedule_mode', 'scheduled')");
 assert.equal(hasPendingTimer(),false,'switching to scheduled mode blocks on invalid window draft');
 await run("recompute()");
 assert.equal(evaluateCalls.length,1,'scheduled mode recompute blocked');
});

test('ac_heating_additional_demand_pct recovery paths: same-value correction recovery and changed single dispatch', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls,makeEl,doc}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 makeEl('div','ac_heating_additional_demand_pct_error');

 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'continuous'
   },
   records:[
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads',choices:[0,25,50]}
   ]
  };
  settings=deepClone(schema.defaults);
  settings.ac_heating_additional_demand_pct=25;
  renderControls();
 `);

 const controlsEl=doc.getElementById('controls');

 await run("recompute()");
 assert.equal(evaluateCalls.length,1);

 // 1. Enter invalid value: canceled recomputation
 inputEl.value='-5';
 controlsEl.oninput({target:inputEl});
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);
 assert.equal(hasPendingTimer(),false);

 // 2. Correct invalid -> valid to SAME canonical value ('25')
 inputEl.value='25';
 controlsEl.oninput({target:inputEl});
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false,'draft valid again');
 assert.equal(hasPendingTimer(),true,'automatic recovery scheduled recompute for unchanged canonical correction');
 assert.equal(app.pendingTimers.length,1,'single timer scheduled');
 await flushTimers();
 assert.equal(evaluateCalls.length,2,'recompute executed via automatic recovery');
 assert.equal(evaluateCalls[1].settings.ac_heating_additional_demand_pct,25);

 // 3. Enter invalid value again
 inputEl.value='-10';
 controlsEl.oninput({target:inputEl});
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);
 assert.equal(hasPendingTimer(),false);

 // 4. Correct invalid -> valid to CHANGED canonical value ('35.5')
 inputEl.value='35.5';
 controlsEl.oninput({target:inputEl});
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false);
 assert.equal(run("settings.ac_heating_additional_demand_pct"),35.5);
 assert.equal(hasPendingTimer(),true,'timer scheduled for changed value');
 assert.equal(app.pendingTimers.length,1,'single dispatch (no duplicate evaluation scheduling)');
 await flushTimers();
 assert.equal(evaluateCalls.length,3,'recompute executed for changed value');
 assert.equal(evaluateCalls[2].settings.ac_heating_additional_demand_pct,35.5);
});

test('ac_heating_additional_demand_pct: per-setting reset clears invalid draft and restores default', async ()=>{
 const app=createAppContext();
 const {run,flushTimers,hasPendingTimer,evaluateCalls,makeEl,doc}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 const errEl=makeEl('div','ac_heating_additional_demand_pct_error');

 const resetBtn=makeEl('button');
 resetBtn.dataset.resetSetting='ac_heating_additional_demand_pct';

 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:0,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'continuous'
   },
   records:[
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads',choices:[0,25,50]}
   ]
  };
  settings=deepClone(schema.defaults);
  settings.ac_heating_additional_demand_pct=50;
  renderControls();
 `);

 const controlsEl=doc.getElementById('controls');

 // Type invalid draft while setting was modified (50)
 inputEl.value='-99';
 controlsEl.oninput({target:inputEl});
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);
 assert.equal(inputEl.getAttribute('aria-invalid'),'true');

 // Click Reset affordance
 controlsEl.onclick({target:resetBtn});

 assert.equal(run("settings.ac_heating_additional_demand_pct"),0,'restores default 0');
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false,'invalid draft cleared');
 assert.equal(run("getAcDemandPctDraft()"),null);
 assert.equal(inputEl.getAttribute('aria-invalid'),null,'aria-invalid removed');
 assert.equal(errEl.hidden,true,'error element hidden');
 assert.equal(run("isRecordModified('ac_heating_additional_demand_pct')"),false,'modified marker cleared');
 assert.equal(hasPendingTimer(),true,'recompute scheduled with default');
 await flushTimers();
 assert.equal(evaluateCalls[evaluateCalls.length-1].settings.ac_heating_additional_demand_pct,0);
});

test('ac_heating_additional_demand_pct: stale in-flight evaluate response rejected during active invalid draft', async ()=>{
 const app=createAppContext();
 const {run,setAutoResolveEvaluate,resolvePendingEvaluation,makeEl,doc}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 makeEl('div','ac_heating_additional_demand_pct_error');

 run(`
  schema={
   defaults:{
    ac_heating_additional_demand_pct:10,
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'continuous'
   },
   records:[
    {name:'ac_heating_additional_demand_pct',unit:'%',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const controlsEl=doc.getElementById('controls');

 // Keep evaluate call in-flight
 setAutoResolveEvaluate(false);
 const recomputePromise=run("recompute()");
 assert.equal(run("$('status')?.textContent"),'Computing…');

 // User types invalid text while evaluate is in-flight
 inputEl.value='-5';
 controlsEl.oninput({target:inputEl});
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);
 assert.equal(run("$('status')?.textContent"),'Inputs changed…');

 // Server now responds to in-flight request
 resolvePendingEvaluation(0,{
  settings:run("settings"),
  layout:{kwp:99,panel_count:200},
  metrics:{npv_eur:99999},
  finance:{},
  energy:{}
 });
 await recomputePromise;

 // Stale response was rejected
 assert.equal(run("current"),null,'stale in-flight response rejected during active invalid percentage draft');
 assert.equal(run("$('status')?.textContent"),'Inputs changed…');
});

test('saving shared default from ac_heating_windows sends grouped payload with reviewable confirmation', async ()=>{
 const app=createAppContext();
 const {run,ui,defaultsCalls,approveConfirm}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'evening',start_minute:960,end_minute:1200}
    ],
    scop:4.0,
    timezone_mode:'civil'
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',status:'proposed',source:'Default'},
    {name:'heating_schedule_mode',unit:'mode',status:'proposed',source:'Default'},
    {name:'scop',unit:'multiplier',status:'provided',source:'Manual'},
    {name:'timezone_mode',unit:'mapping',status:'provided',source:'Manual'}
   ]
  };
  settings=deepClone(schema.defaults);
  settings.ac_heating_windows=[
   {id:'custom_morn',start_minute:420,end_minute:540},
   {id:'pause',start_minute:720,end_minute:720},
   {id:'custom_eve',start_minute:1080,end_minute:1320}
  ];
  renderControls();
 `);

 // Trigger saveDefault for ac_heating_windows
 const savePromise=run("saveDefault('ac_heating_windows')");

 // Reviewable confirmation dialog is open
 assert.equal(ui.defaultConfirm.open,true);
 const desc=ui.defaultConfirmDescription.textContent;
 assert.equal(desc.includes('[object Object]'),false,'confirmation does not contain [object Object]');
 assert.equal(desc.includes('Mode: Scheduled operating windows'),true,'confirmation contains mode');
 assert.equal(desc.includes('Clock context: Europe/Rome (DST)'),true,'confirmation contains clock context');
 assert.equal(desc.includes('custom_morn: 07:00–09:00 (2h 00m)'),true,'confirmation contains first window in HH:MM and duration');
 assert.equal(desc.includes('pause: 12:00–12:00 (inactive (0h 00m))'),true,'confirmation contains equal-bounds inactive window');
 assert.equal(desc.includes('custom_eve: 18:00–22:00 (4h 00m)'),true,'confirmation contains evening window in HH:MM');

 // User approves
 approveConfirm();
 await savePromise;

 // Verify payload sent to /api/defaults
 assert.equal(defaultsCalls.length,1);
 const req=defaultsCalls[0];
 assert.equal(req.name,'ac_heating_windows');
 assert.deepEqual(req.value,JSON.parse(run("JSON.stringify(settings.ac_heating_windows)")));
 assert.deepEqual(req.schedule_settings.ac_heating_windows,JSON.parse(run("JSON.stringify(settings.ac_heating_windows)")));
 assert.equal(req.schedule_settings.heating_schedule_mode,'scheduled');

 // Verify schema.defaults and records updated from backend response for both members
 assert.equal(run("settingValuesEqual(schema.defaults.ac_heating_windows, settings.ac_heating_windows)"),true,'defaults match settings');
 assert.equal(run("schema.defaults.heating_schedule_mode"),'scheduled');
 assert.equal(run("schema.defaults.scop"),4.0,'unrelated default scop preserved');
 assert.equal(run("schema.defaults.timezone_mode"),'civil','unrelated default timezone preserved');
 assert.equal(run("isRecordModified('ac_heating_windows')"),false,'control modified cleared');
 assert.equal(run("isRecordModified('heating_schedule_mode')"),false);
});

test('saving shared default from heating_schedule_mode sends grouped payload, retains windows, notes continuous mode', async ()=>{
 const app=createAppContext();
 const {run,ui,defaultsCalls,approveConfirm}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'evening',start_minute:960,end_minute:1200}
    ],
    scop:4.0,
    timezone_mode:'standard'
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',status:'proposed',source:'Default'},
    {name:'heating_schedule_mode',unit:'mode',status:'proposed',source:'Default'},
    {name:'scop',unit:'multiplier',status:'provided',source:'Manual'},
    {name:'timezone_mode',unit:'mapping',status:'provided',source:'Manual'}
   ]
  };
  settings=deepClone(schema.defaults);
  settings.heating_schedule_mode='continuous';
  settings.ac_heating_windows=[
   {id:'overnight',start_minute:1320,end_minute:360}
  ];
  renderControls();
 `);

 const savePromise=run("saveDefault('heating_schedule_mode')");

 assert.equal(ui.defaultConfirm.open,true);
 const desc=ui.defaultConfirmDescription.textContent;
 assert.equal(desc.includes('[object Object]'),false);
 assert.equal(desc.includes('Mode: Continuous (24h old model) [retained operating windows]'),true);
 assert.equal(desc.includes('Clock context: Standard fixed UTC+1'),true);
 assert.equal(desc.includes('overnight: 22:00–06:00 (crosses midnight'),true);

 approveConfirm();
 await savePromise;

 assert.equal(defaultsCalls.length,1);
 const req=defaultsCalls[0];
 assert.equal(req.name,'heating_schedule_mode');
 assert.equal(req.value,'continuous');
 assert.deepEqual(req.schedule_settings.ac_heating_windows,JSON.parse(run("JSON.stringify(settings.ac_heating_windows)")));
 assert.equal(req.schedule_settings.heating_schedule_mode,'continuous');

 assert.equal(run("schema.defaults.heating_schedule_mode"),'continuous');
 assert.equal(run("settingValuesEqual(schema.defaults.ac_heating_windows, settings.ac_heating_windows)"),true);
 assert.equal(run("schema.defaults.scop"),4.0);
});

test('snapshot isolation: confirmation values and payload match snapshot even if edited before approval', async ()=>{
 const app=createAppContext();
 const {run,ui,defaultsCalls,approveConfirm}=app;
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',status:'proposed'},
    {name:'heating_schedule_mode',unit:'mode',status:'proposed'}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 // Start save
 const savePromise=run("saveDefault('ac_heating_windows')");
 assert.equal(ui.defaultConfirm.open,true);

 // Background mutation occurs while user contemplates confirmation dialog
 run(`
  settings.ac_heating_windows=[{id:'w_mutated',start_minute:600,end_minute:900}];
  settings.heating_schedule_mode='continuous';
 `);

 // User approves the dialog which was showing the original snapshot
 approveConfirm();
 await savePromise;

 assert.equal(defaultsCalls.length,1);
 const req=defaultsCalls[0];
 // Payload must be the SNAPSHOT (w1 360-480, scheduled), NOT the mutated values!
 assert.deepEqual(req.schedule_settings.ac_heating_windows,[{id:'w1',start_minute:360,end_minute:480}]);
 assert.equal(req.schedule_settings.heating_schedule_mode,'scheduled');
});

test('grouped reset from either member resets both to defaults, clears schedule draft, updates DOM/timeline, schedules once', async ()=>{
 const app=createAppContext();
 const {run,evaluateCalls,pendingTimers,flushTimers}=app;
 run(`
  schema={
   defaults:{
    load_scenario:'electrified',
    planning_heat_pump:false,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'evening',start_minute:960,end_minute:1200}
    ],
    ac_heating_additional_demand_pct:10,
    scop:4.0,
    timezone_mode:'civil'
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'},
    {name:'ac_heating_additional_demand_pct',unit:'%'},
    {name:'scop',unit:'multiplier'},
    {name:'timezone_mode',unit:'mapping'}
   ]
  };
  settings=deepClone(schema.defaults);
  quote={line_items:[{id:'quote_item_1',price:1000}]};
  renderControls();
  // Phase 1: Modify mode and windows with invalid schedule draft, valid percentage
  editSetting('heating_schedule_mode','continuous');
  editSetting('ac_heating_windows',[{id:'custom',start_minute:600,end_minute:900}]);
  handleAcScheduleTimeInput('custom','start','99:00');
 `);

 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false);

 evaluateCalls.length=0;
 pendingTimers.length=0;

 // Reset from heating_schedule_mode
 run("resetSetting('heating_schedule_mode')");

 // BOTH windows and mode restored to defaults
 assert.equal(run("settings.heating_schedule_mode"),'scheduled');
 assert.equal(run("settingValuesEqual(settings.ac_heating_windows, schema.defaults.ac_heating_windows)"),true);
 assert.equal(run("settings.ac_heating_windows !== schema.defaults.ac_heating_windows"),true,'deep cloned');

 // Schedule draft cleared and reinitialized with default windows
 assert.equal(run("hasInvalidAcScheduleDraft()"),false);
 assert.equal(run("getAcScheduleDraft()[0].startText"),'06:00','draft reset to default');
 assert.equal(run("getAcScheduleDraft()[0].startError"),null,'draft error cleared');

 // Unrelated quote, scop, timezone, percentage preserved
 assert.equal(run("quote.line_items[0].id"),'quote_item_1');
 assert.equal(run("settings.scop"),4.0);
 assert.equal(run("settings.timezone_mode"),'civil');
 assert.equal(run("settings.ac_heating_additional_demand_pct"),10);

 // Calculation scheduled exactly ONCE
 assert.equal(pendingTimers.length,1,'calculation scheduled once');
 await flushTimers();
 assert.equal(evaluateCalls.length,1);

 // Phase 2: Test reset from ac_heating_windows when percentage draft is also invalid
 run(`
  editSetting('heating_schedule_mode','continuous');
  editSetting('ac_heating_windows',[{id:'w99',start_minute:100,end_minute:200}]);
  handleAcScheduleTimeInput('w99','start','99:00');
  handleAcHeatingDemandPctInput('-5');
 `);
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);

 evaluateCalls.length=0;
 pendingTimers.length=0;

 // Schedule reset from ac_heating_windows
 run("resetSetting('ac_heating_windows')");
 assert.equal(run("settings.heating_schedule_mode"),'scheduled');
 assert.equal(run("settingValuesEqual(settings.ac_heating_windows, schema.defaults.ac_heating_windows)"),true);
 assert.equal(run("hasInvalidAcScheduleDraft()"),false);

 // Percentage draft/error preserved across schedule reset
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true,'schedule reset must not clear percentage error');
 assert.equal(run("getAcDemandPctDraft()"),'-5');

 // Because percentage draft is still invalid, active AC heating recompute is blocked
 assert.equal(pendingTimers.length,0,'recompute blocked while percentage draft is invalid');

 // Resetting percentage clears its own error and schedules calculation
 run("resetSetting('ac_heating_additional_demand_pct')");
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false);
 assert.equal(run("settings.ac_heating_additional_demand_pct"),10);
 assert.equal(pendingTimers.length,1,'percentage reset scheduled calculation once');
 await flushTimers();
 assert.equal(evaluateCalls.length,1);
});

test('guard save when relevant draft is invalid for schedule and percentage', async ()=>{
 const app=createAppContext();
 const {run,ui,defaultsCalls}=app;
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],
    ac_heating_additional_demand_pct:0
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'},
    {name:'ac_heating_additional_demand_pct',unit:'%'}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
  handleAcScheduleTimeInput('w1','start','99:00');
 `);

 // 1. Attempt save from ac_heating_windows while draft is invalid
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 ui.error.hidden=true;
 ui.defaultConfirm.open=false;

 await run("saveDefault('ac_heating_windows')");
 assert.equal(defaultsCalls.length,0,'no API save on invalid schedule draft');
 assert.equal(ui.defaultConfirm.open,false,'no confirmation on invalid schedule draft');
 assert.equal(ui.error.hidden,false,'actionable error shown');
 assert.equal(run("getAcScheduleDraft()[0].startText"),'99:00','invalid draft preserved');

 // 2. Attempt save from heating_schedule_mode while window draft is invalid
 ui.error.hidden=true;
 await run("saveDefault('heating_schedule_mode')");
 assert.equal(defaultsCalls.length,0,'schedule group invalid windows guard mode save');
 assert.equal(ui.defaultConfirm.open,false);
 assert.equal(ui.error.hidden,false);

 // Correct schedule draft -> valid
 run("handleAcScheduleTimeInput('w1','start','06:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false);

 // Enter invalid percentage draft
 run("handleAcHeatingDemandPctInput('-10')");
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);

 // 3. Attempt save percentage
 ui.error.hidden=true;
 await run("saveDefault('ac_heating_additional_demand_pct')");
 assert.equal(defaultsCalls.length,0,'no API save on invalid percentage draft');
 assert.equal(ui.defaultConfirm.open,false);
 assert.equal(ui.error.hidden,false);
 assert.equal(run("getAcDemandPctDraft()"),'-10','invalid percentage draft preserved');

 // 4. Valid schedule save is NOT blocked by percentage draft
 ui.error.hidden=true;
 const savePromise=run("saveDefault('ac_heating_windows')");
 assert.equal(ui.defaultConfirm.open,true,'valid schedule can be confirmed');
 app.approveConfirm();
 await savePromise;
 assert.equal(defaultsCalls.length,1,'schedule saved');
});

test('invalid AC drafts block evaluation while valid scenario and quote edits persist', async ()=>{
 const store={revision:'rev_0',settings:{heating_schedule_mode:'scheduled',ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],ac_heating_additional_demand_pct:10,south_panels:9,panel_watts:400,battery_modules:4,battery_module_kwh:2.5,battery_kwh:10,retail_commodity:0.25},quote:{line_items:[]}};
 const app=createAppContext({stateStore:store});
 const {run,evaluateCalls,stateCalls}=app;
 run(`
  schema={defaults:deepClone(${JSON.stringify(store.settings)}),records:Object.keys(${JSON.stringify(store.settings)}).map(name=>({name,unit:'number'}))};
  settings=deepClone(schema.defaults);quote=deepClone(${JSON.stringify(store.quote)});
  sharedSync=new SharedScenarioSync({request:api,onState:applySharedState});
  sharedSync.revision='rev_0';sharedSync.settings=deepClone(settings);sharedSync.quote=deepClone(quote);
  handleAcScheduleTimeInput('w1','start','99:00');
  handleAcHeatingDemandPctInput('-5');
  settings.south_panels=12;settings.panel_watts=430;settings.battery_modules=2;settings.battery_kwh=5;settings.retail_commodity=0.31;
  quote={line_items:[{id:'edited-quote',total_eur:1234}]};
  schedule();
 `);
 await run('sharedSync.saving');
 assert.equal(stateCalls.length,1,'valid edits are flushed to shared state');
 assert.equal(evaluateCalls.length,0,'invalid AC drafts prevent evaluation');
 assert.equal(store.settings.south_panels,12,JSON.stringify(stateCalls));
 assert.equal(store.settings.panel_watts,430);
 assert.equal(store.settings.battery_kwh,5);
 assert.equal(store.settings.retail_commodity,0.31);
 assert.equal(store.settings.ac_heating_windows[0].start_minute,360,'raw schedule text is excluded from canonical state');
 assert.equal(store.settings.ac_heating_additional_demand_pct,10,'raw percentage text is excluded from canonical state');
 assert.equal(store.quote.line_items[0].id,'edited-quote');
 assert.equal(run("getAcScheduleDraft()[0].startText"),'99:00');
 assert.equal(run('getAcDemandPctDraft()'),'-5');

 // A second client reads the canonical state saved by the first client.
 run(`secondSync=new SharedScenarioSync({request:api});`);
 await run('secondSync.poll()');
 assert.equal(run('secondSync.settings.south_panels'),12);
 assert.equal(run('secondSync.settings.panel_watts'),430);
 assert.equal(run('secondSync.settings.battery_kwh'),5);

 // Correcting both drafts permits exactly one evaluation using canonical values.
 run(`handleAcScheduleTimeInput('w1','start','06:00');handleAcHeatingDemandPctInput('12');`);
 await app.flushTimers();
 assert.equal(evaluateCalls.length,1);
 assert.equal(evaluateCalls[0].settings.south_panels,12);
 assert.equal(evaluateCalls[0].settings.ac_heating_additional_demand_pct,12);
});

test('invalid drafts save canonical edits staged before the draft cancels evaluation', async ()=>{
 const initial={heating_schedule_mode:'scheduled',ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],ac_heating_additional_demand_pct:10,south_panels:4,panel_watts:400,battery_modules:2,battery_module_kwh:2.5,battery_kwh:5,retail_commodity:0.25};
 for(const draftKind of ['schedule','percentage']){
  const store={revision:'rev_0',settings:JSON.parse(JSON.stringify(initial)),quote:{line_items:[]}};
  const app=createAppContext({stateStore:store});
  const {run,evaluateCalls,stateCalls}=app;
  run(`schema={defaults:deepClone(${JSON.stringify(initial)}),records:Object.keys(${JSON.stringify(initial)}).map(name=>({name,unit:'number'}))};settings=deepClone(schema.defaults);quote={line_items:[]};sharedSync=new SharedScenarioSync({request:api,onState:applySharedState});sharedSync.revision='rev_0';sharedSync.settings=deepClone(settings);sharedSync.quote=deepClone(quote);`);
  if(draftKind==='schedule'){
   run("settings.south_panels=7;schedule();handleAcScheduleTimeInput('w1','start','99:00');");
  }else{
   run("settings.panel_watts=500;schedule();handleAcHeatingDemandPctInput('-3');");
  }
  await run('sharedSync.saving.catch(()=>{})');
  assert.equal(stateCalls.length,1,`${draftKind} invalidation saves the already-staged edit`);
  assert.equal(Object.hasOwn(stateCalls[0],'replace'),false,'ordinary edit uses a patch');
  assert.equal(evaluateCalls.length,0,'invalid draft blocks evaluation');
  assert.equal(draftKind==='schedule'?store.settings.south_panels:store.settings.panel_watts,draftKind==='schedule'?7:500);
  assert.equal(draftKind==='schedule'?run("getAcScheduleDraft()[0].startText"):run('getAcDemandPctDraft()'),draftKind==='schedule'?'99:00':'-3');
 }
});

test('failed canonical save stays dirty and retries by polling while invalid draft blocks evaluation', async ()=>{
 const initial={heating_schedule_mode:'scheduled',ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],ac_heating_additional_demand_pct:10,south_panels:4,panel_watts:400,battery_modules:2,battery_module_kwh:2.5,battery_kwh:5,retail_commodity:0.25};
 const store={revision:'rev_0',settings:JSON.parse(JSON.stringify(initial)),quote:{line_items:[]},failStateSaves:1};
 const app=createAppContext({stateStore:store});
 const {run,evaluateCalls,stateCalls}=app;
 run(`schema={defaults:deepClone(${JSON.stringify(initial)}),records:Object.keys(${JSON.stringify(initial)}).map(name=>({name,unit:'number'}))};settings=deepClone(schema.defaults);quote={line_items:[]};sharedSync=new SharedScenarioSync({request:api,onState:applySharedState});sharedSync.revision='rev_0';sharedSync.settings=deepClone(settings);sharedSync.quote=deepClone(quote);settings.south_panels=8;schedule();handleAcHeatingDemandPctInput('-4');`);
 await run('sharedSync.saving.catch(()=>{})');
 assert.equal(store.settings.south_panels,4,'failed request does not mutate saved state');
 assert.equal(run('sharedSync.pending()'),true,'failed request leaves canonical edit dirty');
 assert.equal(run('sharedSync.retryNeeded'),true);
 assert.equal(run('getAcDemandPctDraft()'),'-4');
 await run('sharedSync.poll()');
 await run('recompute()');
 assert.equal(stateCalls.filter(body=>Object.keys(body).length>0).length,2,'poll followed by blocked recompute retries the pending patch');
 assert.equal(store.settings.south_panels,8);
 assert.equal(evaluateCalls.length,0,'retry does not evaluate while draft is invalid');
 assert.equal(run('getAcDemandPctDraft()'),'-4');
});

test('reset is visibly available when canonical equals defaults but draft is invalid for both draft kinds', ()=>{
 const app=createAppContext();
 const {run,doc,makeEl}=app;
 const inputEl=makeEl('input','ac_heating_additional_demand_pct');
 inputEl.type='number';
 inputEl.dataset.setting='ac_heating_additional_demand_pct';
 makeEl('div','ac_heating_additional_demand_pct_error');

 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],
    ac_heating_additional_demand_pct:0
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'},
    {name:'ac_heating_additional_demand_pct',unit:'%'}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const scheduleCtrl=doc.querySelector('[data-control="ac_heating_windows"]');
 const scheduleResetBtn=scheduleCtrl?.querySelector('[data-reset-setting]');
 const pctCtrl=doc.querySelector('[data-control="ac_heating_additional_demand_pct"]');
 const pctResetBtn=pctCtrl?.querySelector('[data-reset-setting]');

 // Initially everything default
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);
 assert.equal(run("isResetAvailable('ac_heating_windows')"),false);
 assert.equal(run("isRecordModified('ac_heating_additional_demand_pct')"),false);
 assert.equal(run("isResetAvailable('ac_heating_additional_demand_pct')"),false);

 // 1. Schedule invalid draft with canonical == defaults
 run("handleAcScheduleTimeInput('w1','start','99:00')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),true);
 assert.equal(run("isRecordModified('ac_heating_windows')"),false,'canonical equal to default');
 assert.equal(run("isResetAvailable('ac_heating_windows')"),true,'reset is available for recovery from draft');
 if(scheduleResetBtn)assert.equal(scheduleResetBtn.hidden,false,'schedule reset button is visible');

 // Reset restores default and clears error
 run("resetSetting('ac_heating_windows')");
 assert.equal(run("hasInvalidAcScheduleDraft()"),false);
 assert.equal(run("isResetAvailable('ac_heating_windows')"),false);
 if(scheduleResetBtn)assert.equal(scheduleResetBtn.hidden,true,'schedule reset button hidden after reset');

 // 2. Percentage invalid draft with canonical == defaults
 run("handleAcHeatingDemandPctInput('-25')");
 assert.equal(run("hasInvalidAcDemandPctDraft()"),true);
 assert.equal(run("isRecordModified('ac_heating_additional_demand_pct')"),false,'canonical equal to default');
 assert.equal(run("isResetAvailable('ac_heating_additional_demand_pct')"),true,'reset is available for percentage draft');
 if(pctResetBtn)assert.equal(pctResetBtn.hidden,false,'percentage reset button is visible');

 // Reset restores default and clears error
 run("resetSetting('ac_heating_additional_demand_pct')");
 assert.equal(run("hasInvalidAcDemandPctDraft()"),false);
 assert.equal(run("isResetAvailable('ac_heating_additional_demand_pct')"),false);
 if(pctResetBtn)assert.equal(pctResetBtn.hidden,true,'percentage reset button hidden after reset');
});

test('group modified state and reset availability reflects either member changing', ()=>{
 const app=createAppContext();
 const {run,doc}=app;
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const schedCtrl=doc.querySelector('[data-control="ac_heating_windows"]');
 const modeCtrl=doc.querySelector('[data-control="heating_schedule_mode"]');
 const schedResetBtn=schedCtrl?.querySelector('[data-reset-setting]');
 const modeResetBtn=modeCtrl?.querySelector('[data-reset-setting]');

 // Modify ONLY heating_schedule_mode
 run("editSetting('heating_schedule_mode','continuous')");
 assert.equal(run("isScheduleGroupModified()"),true);
 assert.equal(run("isRecordModified('ac_heating_windows')"),true);
 assert.equal(run("isRecordModified('heating_schedule_mode')"),true);
 assert.equal(run("isResetAvailable('ac_heating_windows')"),true);
 assert.equal(run("isResetAvailable('heating_schedule_mode')"),true);
 if(schedResetBtn)assert.equal(schedResetBtn.hidden,false);
 if(modeResetBtn)assert.equal(modeResetBtn.hidden,false);

 // Reset from ac_heating_windows clears both
 run("resetSetting('ac_heating_windows')");
 assert.equal(run("settings.heating_schedule_mode"),'scheduled');
 assert.equal(run("isScheduleGroupModified()"),false);
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);
 assert.equal(run("isRecordModified('heating_schedule_mode')"),false);
 if(schedResetBtn)assert.equal(schedResetBtn.hidden,true);
 if(modeResetBtn)assert.equal(modeResetBtn.hidden,true);
});

test('copy AC heating schedule: canonical values win over misleading legacy fields', ()=>{
 const app=createAppContext();
 const {run}=app;
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[
     {id:'morning',start_minute:420,end_minute:540},
     {id:'evening',start_minute:1020,end_minute:1260}
    ],
    heating_on_hour:8,
    heating_off_hour:11,
    heating_evening_on_hour:16,
    heating_evening_off_hour:20,
    hp_schedule_mode:'continuous',
    hp_morning_on:0,
    hp_morning_off:0,
    hp_evening_on:0,
    hp_evening_off:0
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'},
    {name:'hp_schedule_mode',unit:'mode'},
    {name:'hp_morning_on',unit:'hour'},
    {name:'hp_morning_off',unit:'hour'},
    {name:'hp_evening_on',unit:'hour'},
    {name:'hp_evening_off',unit:'hour'}
   ]
  };
  settings=deepClone(schema.defaults);
  copyAcHeatingSchedule();
 `);

 // Canonical ac_heating_windows (07:00-09:00 and 17:00-21:00) win over legacy fields (8, 11, 16, 20)
 assert.equal(run('settings.hp_morning_on'), 7, 'morning on copied from canonical 420 mins (7), not legacy 8');
 assert.equal(run('settings.hp_morning_off'), 9, 'morning off copied from canonical 540 mins (9), not legacy 11');
 assert.equal(run('settings.hp_evening_on'), 17, 'evening on copied from canonical 1020 mins (17), not legacy 16');
 assert.equal(run('settings.hp_evening_off'), 21, 'evening off copied from canonical 1260 mins (21), not legacy 20');
 assert.equal(run('settings.hp_schedule_mode'), 'scheduled', 'heat pump mode switched to scheduled');

 // Misleading legacy fields and source canonical settings remain unmutated
 assert.equal(run('settings.heating_on_hour'), 8);
 assert.equal(run('settings.heating_off_hour'), 11);
 assert.equal(run('settings.heating_evening_on_hour'), 16);
 assert.equal(run('settings.heating_evening_off_hour'), 20);
 assert.equal(run('settings.ac_heating_windows[0].start_minute'), 420);
 assert.equal(run('settings.ac_heating_windows[1].end_minute'), 1260);
});

test('copy AC heating schedule: invalid AC draft never copies hidden last-valid values or mutates targets', ()=>{
 const app=createAppContext();
 const {run,doc,pendingTimers}=app;
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'w1',start_minute:360,end_minute:480}],
    hp_schedule_mode:'continuous',
    hp_morning_on:1,
    hp_morning_off:2,
    hp_evening_on:3,
    hp_evening_off:4
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'},
    {name:'hp_schedule_mode',unit:'mode'},
    {name:'hp_morning_on',unit:'hour'},
    {name:'hp_morning_off',unit:'hour'},
    {name:'hp_evening_on',unit:'hour'},
    {name:'hp_evening_off',unit:'hour'}
   ]
  };
  settings=deepClone(schema.defaults);
  handleAcScheduleTimeInput('w1','end','25:00');
 `);

 assert.equal(run('hasInvalidAcScheduleDraft()'), true, 'draft is invalid');
 pendingTimers.length = 0;

 const res = run('copyAcHeatingSchedule()');
 assert.equal(res.success, false, 'copy was rejected');

 // Error element populated
 const errEl = doc.getElementById('error');
 assert.equal(errEl.hidden, false);
 assert.match(errEl.textContent, /invalid time entries/i);

 // Targets remain completely unmutated
 assert.equal(run('settings.hp_schedule_mode'), 'continuous');
 assert.equal(run('settings.hp_morning_on'), 1);
 assert.equal(run('settings.hp_morning_off'), 2);
 assert.equal(run('settings.hp_evening_on'), 3);
 assert.equal(run('settings.hp_evening_off'), 4);
 assert.equal(pendingTimers.length, 0, 'no recompute scheduled when copy fails preflight');
});

test('copy AC heating schedule: unsupported inputs reject in preflight and never partially update targets', ()=>{
 const app=createAppContext();
 const {run,doc,pendingTimers}=app;

 const setup = (schedMode, windows) => {
  run(`
   schema={
    defaults:{
     heating_schedule_mode:${JSON.stringify(schedMode)},
     ac_heating_windows:${JSON.stringify(windows)},
     hp_schedule_mode:'continuous',
     hp_morning_on:10,
     hp_morning_off:12,
     hp_evening_on:14,
     hp_evening_off:16
    },
    records:[
     {name:'ac_heating_windows',unit:'windows'},
     {name:'heating_schedule_mode',unit:'mode'},
     {name:'hp_schedule_mode',unit:'mode'},
     {name:'hp_morning_on',unit:'hour'},
     {name:'hp_morning_off',unit:'hour'},
     {name:'hp_evening_on',unit:'hour'},
     {name:'hp_evening_off',unit:'hour'}
    ]
   };
   settings=deepClone(schema.defaults);
  `);
  pendingTimers.length = 0;
 };

 const assertUntouched = (errMsgRegex) => {
  const res = run('copyAcHeatingSchedule()');
  assert.equal(res.success, false, 'copy rejected');
  const errEl = doc.getElementById('error');
  assert.equal(errEl.hidden, false, 'error displayed');
  assert.match(errEl.textContent, errMsgRegex);
  assert.equal(run('settings.hp_schedule_mode'), 'continuous');
  assert.equal(run('settings.hp_morning_on'), 10);
  assert.equal(run('settings.hp_morning_off'), 12);
  assert.equal(run('settings.hp_evening_on'), 14);
  assert.equal(run('settings.hp_evening_off'), 16);
  assert.equal(pendingTimers.length, 0, 'no timer scheduled');
 };

 // Case 1: Minute precision bounds (06:30 to 08:00)
 setup('scheduled', [{id:'w1',start_minute:390,end_minute:480}]);
 assertUntouched(/minute-level timing/i);

 // Case 2: Extra windows (> 2 windows)
 setup('scheduled', [
  {id:'w1',start_minute:360,end_minute:480},
  {id:'w2',start_minute:720,end_minute:840},
  {id:'w3',start_minute:1080,end_minute:1200}
 ]);
 assertUntouched(/supports up to 2 operating windows/i);

 // Case 3: All-day (24h) window in scheduled mode
 setup('scheduled', [{id:'w1',start_minute:0,end_minute:1440}]);
 assertUntouched(/all-day/i);

 // Case 4: Empty schedule array in scheduled mode
 setup('scheduled', []);
 assertUntouched(/no operating windows/i);

 // Case 5: All-inactive schedule in scheduled mode
 setup('scheduled', [{id:'w1',start_minute:360,end_minute:360}]);
 assertUntouched(/inactive/i);
});

test('copy AC heating schedule: supported boundary cases correctly map to exact HP representation', ()=>{
 const app=createAppContext();
 const {run}=app;

 const testCase = (schedMode, windows, expected) => {
  run(`
   schema={
    defaults:{
     heating_schedule_mode:${JSON.stringify(schedMode)},
     ac_heating_windows:${JSON.stringify(windows)},
     hp_schedule_mode:'continuous',
     hp_morning_on:9,
     hp_morning_off:9,
     hp_evening_on:9,
     hp_evening_off:9
    },
    records:[
     {name:'ac_heating_windows',unit:'windows'},
     {name:'heating_schedule_mode',unit:'mode'},
     {name:'hp_schedule_mode',unit:'mode'},
     {name:'hp_morning_on',unit:'hour'},
     {name:'hp_morning_off',unit:'hour'},
     {name:'hp_evening_on',unit:'hour'},
     {name:'hp_evening_off',unit:'hour'}
    ]
   };
   settings=deepClone(schema.defaults);
   copyAcHeatingSchedule();
  `);
  for(const [k, v] of Object.entries(expected)){
   assert.equal(run(`settings.${k}`), v, `expected ${k} === ${v}`);
  }
 };

 // 1. Exactly one active morning window (06:00 to 09:00) -> evening disabled (0,0)
 testCase('scheduled', [{id:'morning',start_minute:360,end_minute:540}], {
  hp_schedule_mode: 'scheduled',
  hp_morning_on: 6,
  hp_morning_off: 9,
  hp_evening_on: 0,
  hp_evening_off: 0
 });

 // 2. Exactly one active evening window (17:00 to 22:00) -> morning disabled (0,0)
 testCase('scheduled', [{id:'evening',start_minute:1020,end_minute:1320}], {
  hp_schedule_mode: 'scheduled',
  hp_morning_on: 0,
  hp_morning_off: 0,
  hp_evening_on: 17,
  hp_evening_off: 22
 });

 // 3. One active window (08:00 to 11:00) + one inactive window (15:00 to 15:00)
 testCase('scheduled', [
  {id:'morning',start_minute:480,end_minute:660},
  {id:'evening',start_minute:900,end_minute:900}
 ], {
  hp_schedule_mode: 'scheduled',
  hp_morning_on: 8,
  hp_morning_off: 11,
  hp_evening_on: 15,
  hp_evening_off: 15
 });

 // 4. Overnight window wrapping midnight (22:00 to 06:00)
 testCase('scheduled', [{id:'w1',start_minute:1320,end_minute:360}], {
  hp_schedule_mode: 'scheduled',
  hp_morning_on: 0,
  hp_morning_off: 0,
  hp_evening_on: 22,
  hp_evening_off: 6
 });

 // 5. Window ending at 24:00 (18:00 to 24:00 -> off_hour 0)
 testCase('scheduled', [
  {id:'morning',start_minute:360,end_minute:480},
  {id:'evening',start_minute:1080,end_minute:1440}
 ], {
  hp_schedule_mode: 'scheduled',
  hp_morning_on: 6,
  hp_morning_off: 8,
  hp_evening_on: 18,
  hp_evening_off: 0
 });

 // 6. Window starting at 00:00 (00:00 to 07:00)
 testCase('scheduled', [
  {id:'morning',start_minute:0,end_minute:420},
  {id:'evening',start_minute:960,end_minute:1200}
 ], {
  hp_schedule_mode: 'scheduled',
  hp_morning_on: 0,
  hp_morning_off: 7,
  hp_evening_on: 16,
  hp_evening_off: 20
 });

 // 7. Continuous mode -> hp_schedule_mode='continuous', windows preserved
 testCase('continuous', [{id:'w1',start_minute:360,end_minute:480}], {
  hp_schedule_mode: 'continuous',
  hp_morning_on: 9,
  hp_morning_off: 9,
  hp_evening_on: 9,
  hp_evening_off: 9
 });
});

test('copy AC heating schedule: source and unrelated settings preserved, single scheduling', async ()=>{
 const app=createAppContext();
 const {run,pendingTimers,flushTimers,evaluateCalls}=app;
 run(`
  schema={
   defaults:{
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[
     {id:'morning',start_minute:360,end_minute:480},
     {id:'evening',start_minute:960,end_minute:1200}
    ],
    hp_schedule_mode:'continuous',
    hp_morning_on:0,
    hp_morning_off:0,
    hp_evening_on:0,
    hp_evening_off:0,
    south_panels:14,
    battery_kwh:12,
    retail_commodity:0.28
   },
   records:[
    {name:'ac_heating_windows',unit:'windows'},
    {name:'heating_schedule_mode',unit:'mode'},
    {name:'hp_schedule_mode',unit:'mode'},
    {name:'hp_morning_on',unit:'hour'},
    {name:'hp_morning_off',unit:'hour'},
    {name:'hp_evening_on',unit:'hour'},
    {name:'hp_evening_off',unit:'hour'},
    {name:'south_panels',unit:'panels'},
    {name:'battery_kwh',unit:'kWh'},
    {name:'retail_commodity',unit:'EUR/kWh'}
   ]
  };
  settings=deepClone(schema.defaults);
  quote={line_items:[{id:'line-1',name:'PV Package',total_eur:5000}]};
 `);

 pendingTimers.length = 0;
 evaluateCalls.length = 0;

 // Perform copy
 const res = run('copyAcHeatingSchedule()');
 assert.equal(res.success, true);

 // Target committed atomically
 assert.equal(run('settings.hp_schedule_mode'), 'scheduled');
 assert.equal(run('settings.hp_morning_on'), 6);
 assert.equal(run('settings.hp_morning_off'), 8);
 assert.equal(run('settings.hp_evening_on'), 16);
 assert.equal(run('settings.hp_evening_off'), 20);

 // Source schedule preserved
 assert.equal(run('settings.heating_schedule_mode'), 'scheduled');
 assert.equal(run('settings.ac_heating_windows.length'), 2);
 assert.equal(run('settings.ac_heating_windows[0].start_minute'), 360);
 assert.equal(run('settings.ac_heating_windows[1].end_minute'), 1200);

 // Unrelated settings preserved
 assert.equal(run('settings.south_panels'), 14);
 assert.equal(run('settings.battery_kwh'), 12);
 assert.equal(run('settings.retail_commodity'), 0.28);
 assert.equal(run('quote.line_items[0].id'), 'line-1');

 // Single scheduling: exactly 1 timer queued
 assert.equal(pendingTimers.length, 1, 'atomic commit triggered exactly one scheduled recompute');

 await flushTimers();
 assert.equal(evaluateCalls.length, 1, 'single evaluation executed after timer flush');
 assert.equal(evaluateCalls[0].settings.hp_morning_on, 6);
 assert.equal(evaluateCalls[0].settings.hp_schedule_mode, 'scheduled');
});

test('copy AC heating schedule button: renamed text and accessible label, click handler dispatches action', ()=>{
 const app=createAppContext();
 const {run}=app;
 run(`
  schema={
   defaults:{
    planning_heat_pump:true,
    heating_schedule_mode:'scheduled',
    ac_heating_windows:[{id:'morning',start_minute:360,end_minute:480},{id:'evening',start_minute:960,end_minute:1200}],
    hp_schedule_mode:'continuous',
    hp_morning_on:0,
    hp_morning_off:0,
    hp_evening_on:0,
    hp_evening_off:0
   },
   records:[
    {name:'planning_heat_pump',unit:'boolean',group:'heat_pump'},
    {name:'hp_schedule_mode',unit:'mode',group:'heat_pump',description:'Heat pump schedule mode'}
   ]
  };
  settings=deepClone(schema.defaults);
  renderControls();
 `);

 const renderedHtml = run("control(schema.records.find(r=>r.name==='hp_schedule_mode'))");
 assert.match(renderedHtml, /id="copyBoilerWindowsBtn"/);
 assert.match(renderedHtml, /aria-label="Copy AC heating schedule"/);
 assert.match(renderedHtml, />Copy AC heating schedule<\/button>/);

 // Dispatch click via controls event delegation
 run(`
  const btn={id:'copyBoilerWindowsBtn',closest:(sel)=>sel.includes('copyBoilerWindowsBtn')?btn:null};
  document.getElementById('controls').onclick({target:btn});
 `);

 assert.equal(run('settings.hp_schedule_mode'), 'scheduled');
 assert.equal(run('settings.hp_morning_on'), 6);
 assert.equal(run('settings.hp_morning_off'), 8);
 assert.equal(run('settings.hp_evening_on'), 16);
 assert.equal(run('settings.hp_evening_off'), 20);
});




