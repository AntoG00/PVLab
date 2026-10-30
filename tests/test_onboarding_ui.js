'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {HouseholdClient,HouseholdStorage}=require('../feasibility/web/sync.js');
const {Workflow,numbers,validateDraft,csvMetadata,RoofListEditor}=require('../feasibility/web/onboarding.js');
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body,headers:{get:()=>null}});
function profile(){return {country:'IT',timezone:'Europe/Rome',location:{latitude:45,longitude:9},consumption:{kind:'monthly',acknowledged:true},roofs:[],thermal:{end_uses:[],electric_loads:[]},fiscal:{parameters:{confirmed:true}},market:{valuation_mode:'flat'}};}
const load=()=>({monthly_totals:Array(12).fill(0),policy:{annual_kwh:0}});
test('monthly zero is valid, missing months and synthetic acknowledgement block',()=>{
 assert.equal(numbers(Array(12).fill('0').join(',')).length,12);assert.throws(()=>numbers('1,2'),/MONTHLY_TOTALS/);
 const p=profile();assert.equal(validateDraft(p,load(),{gross:true}),true);p.consumption.acknowledged=false;assert.throws(()=>validateDraft(p,load(),{gross:true}),/ACKNOWLEDGEMENT/);
});
test('annual-only timing and existing PV require acknowledgement and gross reconciliation',()=>{
 const p=profile();p.consumption.kind='annual';assert.equal(validateDraft(p,load(),{gross:true}),true);assert.throws(()=>validateDraft(p,load(),{gross:true,existingPv:true}),/GROSS_LOAD/);
});
test('CSV mapping exposes units and explicit clock semantics',()=>{
 const v={timestamp:'time',energy:'load',delimiter:';',decimal_separator:',',unit:'average_kW',duration_seconds:'900',timezone:'Europe/Rome',timestamp_convention:'end'};
 const result=csvMetadata(v);assert.deepEqual(result.columns,{timestamp:'time',value:'load'});assert.equal(result.unit,'average_kW');assert.equal(result.timestamp_convention,'end');assert.throws(()=>csvMetadata({...v,unit:''}),/UNIT_REQUIRED/);
});
test('embedded electrical replacement blocks with actionable separated-series code',()=>{
 const p=profile();p.thermal.end_uses=[{action:'replace',included_in_electric_load:true}];assert.throws(()=>validateDraft(p,load(),{gross:true}),/END_USE_SERIES_REQUIRED/);
 p.thermal.end_uses=[];p.thermal.electric_loads=[{action:'add',included_in_electric_load:true,incremental_annual_kwh:500}];assert.throws(()=>validateDraft(p,load(),{gross:true}),/INCREMENTAL_LOAD_REQUIRED/);
});
test('credentials, CSRF and expected revision are sent without persistence',async()=>{
 const calls=[],client=new HouseholdClient({fetch:async(path,options)=>{calls.push([path,options]);if(path==='/api/session')return response({csrf_token:'tab-only'});return response({revision:'r2'});}});
 client.select('p','r1');await client.profileRequest('',{profile:{}},{method:'PATCH'});const [path,opts]=calls.at(-1);assert.equal(path,'/api/profiles/p');assert.equal(opts.credentials,'same-origin');assert.equal(opts.headers['X-CSRF-Token'],'tab-only');assert.equal(opts.headers['If-Match'],'r1');assert.equal(client.revision,'r2');
});
test('switch clears rendered data, cancels requests and rejects a late response',async()=>{
 let release,clears=0;const wait=new Promise(r=>release=r);const client=new HouseholdClient({onClear:()=>clears++,fetch:async(path)=>{if(path==='/api/session')return response({csrf_token:'t'});await wait;return response({revision:'old'});}});
 client.select('old','r1');await client.session();const pending=client.profileRequest('');await new Promise(r=>setImmediate(r));client.select('new','r2');release();await assert.rejects(pending,/STALE_RESPONSE/);assert.equal(client.profileId,'new');assert.equal(client.revision,'r2');assert.equal(clears,2);
});
test('session expiry clears profile and never silently creates another household for a failed write',async()=>{
 const calls=[],client=new HouseholdClient({fetch:async(path,opts)=>{calls.push([path,opts]);return path==='/api/session'?response({csrf_token:'t'}):response({},401);}});client.select('p','r');await assert.rejects(client.profileRequest('',{}),e=>e.status===401);assert.equal(client.profileId,null);assert.equal(client.csrf,null);assert.equal(calls.length,2);
});
for(const code of [409,413,422,429])test('HTTP '+code+' retains actionable status and bounded diagnostic',async()=>{
 const c=new HouseholdClient({fetch:async path=>path==='/api/session'?response({csrf_token:'t'}):response({diagnostic:{code:'ambiguous_time',row:2}},code)});c.select('p','r');await assert.rejects(c.profileRequest('/consumption','x'),e=>e.status===code&&e.diagnostic.row===2);assert.equal(c.ready,false);
});
test('explicit deletion clears profile and session',async()=>{
 const c=new HouseholdClient({fetch:async path=>response(path==='/api/session'?{csrf_token:'t'}:{status:'deleted'})});c.select('p','r');await c.deleteProfile();assert.equal(c.profileId,null);c.select('p','r');await c.deleteHousehold();assert.equal(c.csrf,null);
});
test('only display preferences reach localStorage; household snapshots remain scoped memory',()=>{
 const writes=[],store=new HouseholdStorage({setItem:(...v)=>writes.push(v),getItem:()=>null});store.setItem('pv-lab-scenarios-v1','private result');store.setItem('pv-lab-theme-v1','night');assert.equal(writes.length,1);assert.equal(store.getItem('pv-lab-scenarios-v1'),'private result');store.clearPrivate();assert.equal(store.getItem('pv-lab-scenarios-v1'),null);
});
test('draft to compiled ready uses current revision; failed generation stays draft',async()=>{
 let revision=0,ready=false;const client={profileId:'p',generation:0,revision:'r0',ready:false,profileRequest:async(suffix,body)=>{client.revision='r'+(++revision);if(suffix==='/validation')return {ready,fiscal_rule_revision:'f'.repeat(64)};if(suffix==='/solar-jobs')return {id:'j'};if(suffix==='/compile'){ready=true;return {compiled:true};}return {};},waitJob:async()=>({state:'complete'})};
 const w=new Workflow(client);await w.save(profile(),load(),{gross:true});assert.equal(w.status,'draft');await w.generate([2020],{});assert.equal(w.status,'ready');assert.equal(client.ready,true);
 client.waitJob=async()=>{throw Error('failed');};await assert.rejects(w.generate([2020],{}));assert.equal(w.status,'draft');assert.equal(client.ready,false);
});
class Element {
 constructor(tag,doc){this.tag=tag;this.ownerDocument=doc;this.children=[];this.attrs={};this.listeners={};this._text='';this.value='';}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text;}
 appendChild(n){this.children.push(n);return n;}setAttribute(k,v){this.attrs[k]=String(v);}addEventListener(k,f){this.listeners[k]=f;}focus(){this.focused=true;}
 set innerHTML(v){throw Error('DOM injection sink');}
}
const doc={createElement:tag=>new Element(tag,doc)};
test('roof editor adds/removes stable IDs and renders labels only as text',()=>{
 const root=new Element('div',doc),editor=new RoofListEditor(root,{doc});const r=editor.addRoof({label:'<img onerror=alert(1)>',tilt_deg:20,azimuth_deg:180,max_kwp:3,shading_fraction:.1});assert.equal(editor.getRoofs()[0].label,r.label);assert.equal(editor.listEl.children[0].children[0].textContent,r.label);editor.removeRoof(r.id);assert.equal(editor.getRoofs().length,0);assert.equal(editor.addBtn.focused,true);
});
test('roof editor preserves invalid numeric input for correction and enforces twelve-plane limit',()=>{
 const editor=new RoofListEditor(new Element('div',doc),{doc});for(let i=0;i<12;i++)editor.addRoof({max_kwp:2});assert.throws(()=>editor.addRoof(),/ROOF_LIMIT/);editor.setRoofs([{id:'roof_1',label:'A',tilt_deg:91,azimuth_deg:180,max_kwp:1,shading_fraction:0}]);const p=profile();p.roofs=editor.getRoofs();assert.throws(()=>validateDraft(p,load(),{gross:true}),/ROOF_INVALID/);
});

test('scoped sync ignores an old save after the profile changes',async()=>{
 const Sync=require('../feasibility/web/sync.js');let release;const wait=new Promise(r=>release=r);const states=[];
 const sync=new Sync({request:async()=>{await wait;return {revision:'old',settings:{private:1},quote:{}};},onState:s=>states.push(s)});
 sync.setScope('old','a');sync.receive({revision:'a',settings:{},quote:{}});sync.update({private:1},{});const saving=sync.flush();sync.setScope('new','b');release();await saving;
 assert.equal(sync.profileId,'new');assert.deepEqual(sync.settings,{});assert.equal(states.length,1);
});

test('mounted setup completes draft review and compile using explicit form inputs',async()=>{
 const {mount}=require('../feasibility/web/onboarding.js');
 Element.prototype.reset=function(){const visit=n=>{if(n.defaultValue!==undefined)n.value=n.defaultValue;for(const c of n.children)visit(c);};visit(this);};
 Element.prototype.reportValidity=()=>true;
 const root=new Element('section',doc);let rev=0,document=null,compiled=false,opened=false;
 const client=new HouseholdClient({fetch:async(path,opts)=>{
  const body=opts.body?JSON.parse(opts.body):null;
  if(path==='/api/session')return response({csrf_token:'tab-only'});
  if(path==='/api/profiles')return response(opts.method==='POST'?{id:'a'.repeat(32),revision:'r'+(++rev)}:{profiles:[{id:'a'.repeat(32),revision:'r'+rev}]});
  if(path.endsWith('/validation'))return response({ready:compiled,revision:'r'+rev,fiscal_rule_revision:'f'.repeat(64)});
  if(path.endsWith('/solar-jobs'))return response({id:'job',state:'queued'});
  if(path==='/api/jobs/job')return response({id:'job',profile_id:'a'.repeat(32),revision:'r'+rev,state:'complete'});
  if(path.endsWith('/compile')){compiled=true;return response({compiled:true,revision:'r'+(++rev)});}
  if(opts.method==='PATCH'){document=body.profile;rev++;}return response({id:'a'.repeat(32),revision:'r'+rev,profile:document});
 }});
 const setup=mount({root,client,onReady:()=>{opened=true;}});
 const all=()=>{const nodes=[];const visit=n=>{nodes.push(n);for(const c of n.children)visit(c);};visit(root);return nodes;};
 const click=async text=>{const b=all().find(n=>n.tag==='button'&&n.textContent===text);assert.ok(b,text);await b.onclick();};
 await click('Create new draft');const f=setup.fields;
 for(const [key,value]of Object.entries({country:'IT',timezone:'Europe/Rome',latitude:'45',longitude:'9',kind:'monthly',coverage_start:'2025-01-01',coverage_end:'2026-01-01',totals:Array(12).fill(0).join(','),baseline_cooling_kwh:'0',reference_year:'2025',years:'2020',loss_percent:'14',mounting:'Free standing 2025',technology:'cSi',zone:'NORD',series:'NORD',valuation:'flat',window_start:'2025-01-01',window_end:'2026-01-01',fiscal_mode:'parameters',effective_from:'2025-01-01',effective_to:'2027-01-01',as_of:'2026-01-01',sources:'Synthetic test',ev_action:'unchanged',cooling_action:'unchanged'}))f[key].value=value;
 for(const key of ['gross','ack','confirmed','providerConsent'])f[key].checked=true;
 await click('Save and review draft');assert.equal(setup.workflow.status,'draft');assert.ok(document);
 await click('Generate solar data and compile (retry)');assert.equal(setup.workflow.status,'ready');
 await click('Open calculation views');assert.equal(opened,true);
});

test('empty monthly slots are rejected rather than shifted',()=>{
 assert.throws(()=>numbers('1,,2,3,4,5,6,7,8,9,10,11,12'),/MONTHLY_TOTALS/);assert.equal(numbers(Array(12).fill(0).join(' ')).length,12);
});
test('readiness blockers prevent a provider job from being submitted',async()=>{
 const calls=[],client={generation:0,ready:false,profileRequest:async path=>{calls.push(path);return {blockers:['FISCAL_RULES_INVALID','COMPILED_CANDIDATE_REQUIRED']};}};
 const workflow=new Workflow(client);workflow.load=load();await assert.rejects(workflow.generate([2020],{}),/FISCAL_RULES_INVALID/);assert.deepEqual(calls,['/validation']);assert.equal(workflow.status,'draft');
});

test('selecting another profile blanks the previous form before its response arrives',async()=>{
 const {mount}=require('../feasibility/web/onboarding.js');let release;const wait=new Promise(r=>release=r);
 const client=new HouseholdClient({fetch:async path=>{if(path==='/api/session')return response({csrf_token:'t'});if(path.endsWith('/validation'))return response({ready:false,revision:'new'});await wait;return response({profile:null,revision:'new'});}});
 const root=new Element('section',doc),setup=mount({root,client});setup.fields.latitude.value='45';setup.fields.profile.value='newProfile';const pending=setup.fields.profile.onchange();assert.equal(setup.fields.latitude.value,'');assert.equal(setup.fields.profile.value,'newProfile');release();await pending;assert.equal(client.profileId,'newProfile');assert.equal(client.ready,false);
});

test('blocked browser storage does not prevent household setup or tab-local snapshots',()=>{
 const original=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 try{Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw Error('Storage blocked');}});const storage=new HouseholdStorage();storage.setItem('pv-lab-theme-v1','night');assert.equal(storage.getItem('pv-lab-theme-v1'),null);storage.setItem('pv-lab-scenarios-v1','private');assert.equal(storage.getItem('pv-lab-scenarios-v1'),'private');}
 finally{if(original)Object.defineProperty(globalThis,'localStorage',original);else delete globalThis.localStorage;}
});
