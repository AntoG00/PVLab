'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const SharedScenarioSync=require('../feasibility/web/sync.js');

const appSource=fs.readFileSync(path.join(__dirname,'../feasibility/web/app.js'),'utf8');

test('pure equality: separate same-value array/object copies equal even reordered object properties', ()=>{
 const a=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 const b=[
  {end_minute:720,start_minute:480,id:'morning'},
  {start_minute:960,id:'evening',end_minute:1200}
 ];
 assert.equal(SharedScenarioSync.equal(a,b),true);
});

test('pure equality: start/end minute change, add/remove/reorder windows correctly unequal', ()=>{
 const base=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];

 // start minute changed
 const startChanged=[
  {id:'morning',start_minute:420,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 assert.equal(SharedScenarioSync.equal(base,startChanged),false);

 // end minute changed
 const endChanged=[
  {id:'morning',start_minute:480,end_minute:660},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 assert.equal(SharedScenarioSync.equal(base,endChanged),false);

 // id changed
 const idChanged=[
  {id:'custom',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 assert.equal(SharedScenarioSync.equal(base,idChanged),false);

 // window added
 const added=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'midday',start_minute:750,end_minute:900},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 assert.equal(SharedScenarioSync.equal(base,added),false);

 // window removed
 const removed=[
  {id:'morning',start_minute:480,end_minute:720}
 ];
 assert.equal(SharedScenarioSync.equal(base,removed),false);

 // windows reordered in array: array order is strictly meaningful
 const reordered=[
  {id:'evening',start_minute:960,end_minute:1200},
  {id:'morning',start_minute:480,end_minute:720}
 ];
 assert.equal(SharedScenarioSync.equal(base,reordered),false);
});

test('pure equality: sensible primitive semantics (null distinct 0, false, undefined, empty array/object)', ()=>{
 const eq=SharedScenarioSync.equal;
 assert.equal(eq(null,0),false);
 assert.equal(eq(0,null),false);
 assert.equal(eq(null,undefined),false);
 assert.equal(eq(undefined,null),false);
 assert.equal(eq(null,false),false);
 assert.equal(eq(0,false),false);
 assert.equal(eq(false,0),false);
 assert.equal(eq(0,''),false);
 assert.equal(eq('0',0),false);
 assert.equal(eq(null,null),true);
 assert.equal(eq(undefined,undefined),true);
 assert.equal(eq(0,0),true);
 assert.equal(eq(false,false),true);
 assert.equal(eq('',''),true);
 assert.equal(eq([],{}),false);
 assert.equal(eq({},[]),false);
 assert.equal(eq({a:1},{a:1,b:undefined}),false);
});

function createAppContext(){
 const elements=new Map();
 function makeEl(tag='div'){
  const classes=new Set();
  return {
   classList:{
    toggle(c,force){if(force===undefined){if(classes.has(c))classes.delete(c);else classes.add(c);}else if(force)classes.add(c);else classes.delete(c);return classes.has(c);},
    add(c){classes.add(c);},remove(c){classes.delete(c);},contains(c){return classes.has(c);}
   },
   hidden:false,textContent:'',innerHTML:'',value:'',dataset:{},
   querySelector(sel){return makeEl();},
   querySelectorAll(sel){return [];},
   addEventListener(){},
   closest(){return null;},
   removeAttribute(){},
   setAttribute(){}
  };
 }
 const badge={textContent:'',className:''};
 const ui={
  modifiedCountBadge:badge,
  controls:makeEl(),
  status:{textContent:''},
  error:{hidden:true},
  defaultConfirm:{addEventListener(){}},
  defaultConfirmTitle:{textContent:''},
  systemSummary:makeEl()
 };
 const context=vm.createContext({
  SharedScenarioSync,
  localStorage:{getItem:()=>null,setItem:()=>{}},
  document:{
   getElementById:id=>ui[id]||makeEl(),
   querySelector:()=>null,
   querySelectorAll:()=>[]
  },
  setTimeout:()=>{},clearTimeout:()=>{},
  fetch:async()=>({ok:true,json:async()=>({})}),
  syncOptimizerEquipmentInputs:()=>{}
 });
 const cutIndex=appSource.indexOf('function schedule(');
 const harnessScript=cutIndex>=0
  ?appSource.slice(0,cutIndex)+'\nlet scheduled=0;function schedule(){scheduled++;}'
  :appSource;
 vm.runInContext(harnessScript,context);
 return {context,run:code=>vm.runInContext(code,context),badge};
}

test('app.js: modified/default indicators recognize deep copies as unmodified, real edits modified', ()=>{
 const {run,badge}=createAppContext();
 run(`
  schema={
   defaults:{
    scop:4.0,
    ac_heating_windows:[
     {id:'morning',start_minute:480,end_minute:720},
     {id:'evening',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'scop',unit:'multiplier',bounds:[1,8],group:'loads'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // Exact clone in settings is unmodified
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'Default');

 // Reordered properties in cloned objects are also unmodified
 run(`
  settings.ac_heating_windows=[
   {end_minute:720,id:'morning',start_minute:480},
   {start_minute:960,end_minute:1200,id:'evening'}
  ];
 `);
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'Default');

 // Real edit: change start minute
 run(`
  settings.ac_heating_windows=[
   {id:'morning',start_minute:420,end_minute:720},
   {id:'evening',start_minute:960,end_minute:1200}
  ];
 `);
 assert.equal(run("isRecordModified('ac_heating_windows')"),true);
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'1 modified');

 // Real edit: reordered windows in array
 run(`
  settings.ac_heating_windows=[
   {id:'evening',start_minute:960,end_minute:1200},
   {id:'morning',start_minute:480,end_minute:720}
  ];
 `);
 assert.equal(run("isRecordModified('ac_heating_windows')"),true);

 // Real edit: remove window
 run(`
  settings.ac_heating_windows=[
   {id:'morning',start_minute:480,end_minute:720}
  ];
 `);
 assert.equal(run("isRecordModified('ac_heating_windows')"),true);
});

test('app.js: resetSetting resets canonical schedule and clears modified state', ()=>{
 const {run,badge}=createAppContext();
 run(`
  schema={
   defaults:{
    scop:4.0,
    ac_heating_windows:[
     {id:'morning',start_minute:480,end_minute:720},
     {id:'evening',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'scop',unit:'multiplier',bounds:[1,8],group:'loads'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // Perform edit
 run(`
  editSetting('ac_heating_windows',[
   {id:'morning',start_minute:360,end_minute:600}
  ]);
 `);
 assert.equal(run("isRecordModified('ac_heating_windows')"),true);
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'1 modified');

 // Reset setting
 run("resetSetting('ac_heating_windows')");
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);
 assert.deepEqual(run('settings.ac_heating_windows'),run('schema.defaults.ac_heating_windows'));
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'Default');
});

test('app.js: deep clones prevent source mutation leakage across edit and reset', ()=>{
 const {run}=createAppContext();
 run(`
  schema={
   defaults:{
    ac_heating_windows:[
     {id:'morning',start_minute:480,end_minute:720},
     {id:'evening',start_minute:960,end_minute:1200}
    ]
   },
   records:[
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 const originalDefaultMinute=run('schema.defaults.ac_heating_windows[0].start_minute');
 assert.equal(originalDefaultMinute,480);

 // 1. Mutating settings directly does NOT leak into schema.defaults
 run('settings.ac_heating_windows[0].start_minute=999;');
 assert.equal(run('schema.defaults.ac_heating_windows[0].start_minute'),480);

 // 2. Passing external object to editSetting does NOT create alias
 run(`
  const externalSchedule=[{id:'external',start_minute:500,end_minute:800}];
  editSetting('ac_heating_windows',externalSchedule);
  externalSchedule[0].start_minute=111;
 `);
 assert.equal(run('settings.ac_heating_windows[0].start_minute'),500);

 // 3. resetSetting gives fresh clone, mutating settings afterwards does NOT leak to defaults
 run("resetSetting('ac_heating_windows');");
 assert.equal(run('schema.defaults.ac_heating_windows[0].start_minute'),480);
 assert.equal(run('settings.ac_heating_windows[0].start_minute'),480);
 run('settings.ac_heating_windows[0].start_minute=222;');
 assert.equal(run('schema.defaults.ac_heating_windows[0].start_minute'),480);
});

test('app.js: unrelated scalar and battery settings behaviors are preserved', ()=>{
 const {run,badge}=createAppContext();
 run(`
  schema={
   defaults:{
    scop:4.0,
    export_irpef:0.33,
    battery_modules:2,
    battery_module_kwh:5.0,
    battery_secondary_modules:0,
    battery_secondary_module_kwh:0,
    battery_mixed_sizes:false,
    battery_kwh:10.0,
    ac_heating_windows:[
     {id:'morning',start_minute:480,end_minute:720}
    ]
   },
   records:[
    {name:'scop',unit:'multiplier',bounds:[1,8],group:'loads'},
    {name:'export_irpef',unit:'multiplier',bounds:[0,1],group:'export'},
    {name:'battery_modules',unit:'count',bounds:[0,10],group:'storage'},
    {name:'battery_module_kwh',unit:'kWh',bounds:[0,20],group:'storage'},
    {name:'battery_group',unit:'group',group:'storage'},
    {name:'ac_heating_windows',unit:'windows',group:'loads'}
   ]
  };
  settings=deepClone(schema.defaults);
 `);

 // Initially all default
 assert.equal(run("isRecordModified('scop')"),false);
 assert.equal(run("isRecordModified('battery_group')"),false);
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);

 // Modify scalar scop
 run("editSetting('scop',5.2);");
 assert.equal(run("isRecordModified('scop')"),true);
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'1 modified');

 // Reset scop
 run("resetSetting('scop');");
 assert.equal(run("isRecordModified('scop')"),false);
 assert.equal(run('settings.scop'),4.0);
 run('updateModifiedBadge()');
 assert.equal(badge.textContent,'Default');

 // Modify battery modules
 run("editSetting('battery_modules',4);");
 assert.equal(run("isRecordModified('battery_group')"),true);
 assert.equal(run("isRecordModified('battery_modules')"),true);
 assert.equal(run("isRecordModified('ac_heating_windows')"),false);

 // Reset battery group
 run("resetSetting('battery_group');");
 assert.equal(run("isRecordModified('battery_group')"),false);
 assert.equal(run('settings.battery_modules'),2);
});

test('browser script load order regression: full sync.js then app.js execute in same global context without declaration collisions', ()=>{
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
   querySelector(sel){return makeEl();},
   querySelectorAll(sel){return [];},
   addEventListener(){},
   removeEventListener(){},
   closest(){return null;},
   removeAttribute(){},
   setAttribute(){},
   focus(){},
   style:{}
  };
 }
 const win={
  addEventListener(){},removeEventListener(){},
  location:{hash:''},
  matchMedia:()=>({matches:false}),
  innerWidth:1024,innerHeight:768
 };
 win.window=win;
 const doc={
  getElementById:id=>makeEl(id),
  querySelector:sel=>makeEl(),
  querySelectorAll:sel=>[],
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

 // Both entire scripts execute in the exact load order specified by index.html (sync.js then app.js)
 vm.runInContext(syncCode,ctx);
 vm.runInContext(appCode,ctx);

 assert.equal(typeof ctx.SharedScenarioSync,'function','SharedScenarioSync class is defined in global context');
 assert.equal(typeof ctx.SharedScenarioSync.equal,'function','SharedScenarioSync.equal is exported');
 assert.equal(typeof ctx.settingValuesEqual,'function','settingValuesEqual is defined in global context without syntax collision');

 // Verify settingValuesEqual in the combined browser context uses the shared value equality
 const originalSchedule=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 const reorderedKeysSchedule=[
  {end_minute:720,id:'morning',start_minute:480},
  {start_minute:960,end_minute:1200,id:'evening'}
 ];
 assert.equal(ctx.settingValuesEqual(originalSchedule,reorderedKeysSchedule),true,'deep value equality handles reordered keys in combined context');

 const modifiedSchedule=[
  {id:'morning',start_minute:420,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 assert.equal(ctx.settingValuesEqual(originalSchedule,modifiedSchedule),false,'deep value equality detects minute changes');
});

