'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {HouseholdClient,HouseholdStorage}=require('../feasibility/web/sync.js');
const {Workflow,numbers,validateDraft,csvMetadata,RoofListEditor,YearMenu,SUPPORTED_YEARS,mount}=require('../feasibility/web/onboarding.js');
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
 reportValidity(){return true;}
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
  if(path.endsWith('/solar-coverage'))return response({years:SUPPORTED_YEARS});
  if(path.endsWith('/solar-jobs'))return response({id:'job',state:'queued'});
  if(path==='/api/jobs/job')return response({id:'job',profile_id:'a'.repeat(32),revision:'r'+rev,state:'complete'});
  if(path.endsWith('/compile')){compiled=true;return response({compiled:true,revision:'r'+(++rev)});}
  if(opts.method==='PATCH'){document=body.profile;rev++;}return response({id:'a'.repeat(32),revision:'r'+rev,profile:document});
 }});
 const setup=mount({root,client,onReady:()=>{opened=true;}});
 const all=()=>{const nodes=[];const visit=n=>{nodes.push(n);for(const c of n.children)visit(c);};visit(root);return nodes;};
 const click=async text=>{const b=all().find(n=>n.tag==='button'&&n.textContent===text);assert.ok(b,text);await b.onclick();};
 await click('Create profile');const f=setup.fields;
 for(const [key,value]of Object.entries({country:'IT',timezone:'Europe/Rome',latitude:'45',longitude:'9',kind:'monthly',coverage_start:'2025-01-01',coverage_end:'2026-01-01',totals:Array(12).fill(0).join(','),baseline_cooling_kwh:'0',reference_year:'2025',years:'2020',loss_percent:'14',mounting:'Free standing 2025',technology:'cSi',zone:'NORD',series:'NORD',valuation:'flat',window_start:'2025-01-01',window_end:'2026-01-01',fiscal_mode:'parameters',effective_from:'2025-01-01',effective_to:'2027-01-01',as_of:'2026-01-01',sources:'Synthetic test',ev_action:'unchanged',cooling_action:'unchanged'}))f[key].value=value;
 for(const key of ['gross','ack','confirmed','providerConsent'])f[key].checked=true;
 await click('Save and review draft');assert.equal(setup.workflow.status,'draft');assert.ok(document);
 await click('Check solar coverage');f.years.value='2020';
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

test('azimuth compass hides on blank or NaN and displays valid cardinals', () => {
 const root = new Element('div', doc), editor = new RoofListEditor(root, { doc });
 editor.addRoof({});
 const row = editor.listEl.children[0];
 const azLbl = row.children.find(c => c.textContent && c.textContent.includes('Azimuth'));
 const inp = azLbl.children.find(c => c.tag === 'input');
 const hint = azLbl.children.find(c => c.tag === 'span');
 assert.equal(hint.textContent, '');
 assert.equal(hint.hidden, true);

 const setAz = v => { inp.value = v; inp.listeners.input(); };
 setAz('0');
 assert.equal(hint.textContent, ' (N)');
 assert.equal(hint.hidden, false);
 setAz('90');
 assert.equal(hint.textContent, ' (E)');
 assert.equal(hint.hidden, false);
 setAz('180');
 assert.equal(hint.textContent, ' (S)');
 assert.equal(hint.hidden, false);
 setAz('');
 assert.equal(hint.textContent, '');
 assert.equal(hint.hidden, true);
});

test('roof actions wrapper groups remove button and supports targeted removal', () => {
 const root = new Element('div', doc), editor = new RoofListEditor(root, { doc });
 const r1 = editor.addRoof({ label: 'Plane 1' });
 const r2 = editor.addRoof({ label: 'Plane 2' });
 assert.equal(editor.listEl.children.length, 2);
 const row1 = editor.listEl.children[0];
 assert.equal(row1.className, 'roof-plane');
 const actions1 = row1.children.find(c => c.className === 'roof-actions');
 assert.ok(actions1);
 const btn1 = actions1.children.find(c => c.tag === 'button');
 assert.equal(btn1.textContent, 'Remove Plane 1');

 btn1.listeners.click();
 assert.equal(editor.getRoofs().length, 1);
 assert.equal(editor.getRoofs()[0].id, r2.id);
 assert.equal(editor.listEl.children.length, 1);
 assert.equal(editor.listEl.children[0].children[0].textContent, 'Plane 2');
});

test('profile setup controls precede inert form and explanation tracks form inertness', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 let failSelect = false;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p1', revision: 'r1' } : { profiles: [{ id: 'p1', revision: 'r1' }] });
   if (failSelect && path === '/api/profiles/p1') return response({ error: 'fail' }, 500);
   if (path.endsWith('/validation')) return response({ ready: false, revision: 'r1' });
   return response({ id: 'p1', revision: 'r1', profile: profile() });
  }
 });
 const setup = mount({ root, client });
 const setupGroupIdx = root.children.findIndex(c => c.className === 'profile-setup-group');
 const formIdx = root.children.findIndex(c => c.tag === 'form');
 assert.ok(setupGroupIdx >= 0 && formIdx > setupGroupIdx, 'setup controls must precede form');

 const setupGroup = root.children[setupGroupIdx];
 const help = setupGroup.children.find(c => (c.attrs && c.attrs.id === 'profileSetupExplanation') || c.id === 'profileSetupExplanation');
 const form = root.children[formIdx];
 assert.equal(form.attrs['aria-describedby'], 'profileSetupExplanation');
 assert.equal(form.inert, true);
 assert.equal(help.hidden, false);
 assert.ok(help.textContent.includes('Setup fields are disabled'));

 const allNodes = () => { const res = []; const v = n => { res.push(n); for (const c of n.children) v(c); }; v(root); return res; };
 const createBtn = allNodes().find(n => n.tag === 'button' && n.textContent === 'Create profile');
 await createBtn.onclick();
 assert.equal(form.inert, false);
 assert.equal(help.hidden, true);

 setup.fields.profile.value = '';
 await setup.fields.profile.onchange();
 assert.equal(form.inert, true);
 assert.equal(help.hidden, false);
 assert.equal(root.children.find(c => c.attrs.role === 'status').textContent, 'Select or create a profile to continue.');

 failSelect = true;
 setup.fields.profile.value = 'p1';
 await setup.fields.profile.onchange();
 assert.equal(form.inert, true);
 assert.equal(help.hidden, false);
});

test('create sends trimmed profile name and refresh displays name with id fallback', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 const createdBodies = [];
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') {
    if (opts.method === 'POST') {
     createdBodies.push(JSON.parse(opts.body));
     return response({ id: 'p_new', revision: 'r1' });
    }
    return response({ profiles: [{ id: 'p1', name: 'My Villa' }, { id: 'p2' }] });
   }
   if (path.endsWith('/validation')) return response({ ready: false, revision: 'r1' });
   return response({ id: 'p_new', revision: 'r1', profile: profile() });
  }
 });
 const setup = mount({ root, client });
 setup.fields.profileName.value = '  Sunny Cottage  ';
 const allNodes = () => { const res = []; const v = n => { res.push(n); for (const c of n.children) v(c); }; v(root); return res; };
 const createBtn = allNodes().find(n => n.tag === 'button' && n.textContent === 'Create profile');
 await createBtn.onclick();

 assert.deepEqual(createdBodies, [{ name: 'Sunny Cottage' }]);
 assert.equal(setup.fields.profileName.value, '');

 const options = setup.fields.profile.children;
 assert.equal(options[1].textContent, 'My Villa');
 assert.equal(options[1].value, 'p1');
 assert.equal(options[2].textContent, 'p2');
 assert.equal(options[2].value, 'p2');
});

test('provider consent is not HTML-required for offline save and blocks provider generation when omitted',async()=>{
 const {mount}=require('../feasibility/web/onboarding.js');
 const root=new Element('section',doc);let rev=0,savedDoc=null,jobCalled=false;
 const client=new HouseholdClient({fetch:async(path,opts)=>{
  const body=opts.body?JSON.parse(opts.body):null;
  if(path==='/api/session')return response({csrf_token:'tab-only'});
  if(path==='/api/profiles')return response(opts.method==='POST'?{id:'p_test',revision:'r'+(++rev)}:{profiles:[{id:'p_test',revision:'r'+rev}]});
  if(path.endsWith('/validation'))return response({ready:false,revision:'r'+rev,fiscal_rule_revision:'f'.repeat(64)});
  if(path.endsWith('/solar-jobs')){jobCalled=true;return response({id:'job',state:'queued'});}
  if(opts.method==='PATCH'){savedDoc=body.profile;rev++;}return response({id:'p_test',revision:'r'+rev,profile:savedDoc});
 }});
 const setup=mount({root,client});
 assert.equal(setup.fields.providerConsent.required,false,'providerConsent must not be HTML-required');
 const all=()=>{const nodes=[];const visit=n=>{nodes.push(n);for(const c of n.children)visit(c);};visit(root);return nodes;};
 const click=async text=>{const b=all().find(n=>n.tag==='button'&&n.textContent===text);assert.ok(b,text);await b.onclick();};
 await click('Create profile');const f=setup.fields;
 for(const [key,value]of Object.entries({country:'IT',timezone:'Europe/Rome',latitude:'45',longitude:'9',kind:'monthly',coverage_start:'2025-01-01',coverage_end:'2026-01-01',totals:Array(12).fill(100).join(','),baseline_cooling_kwh:'0',reference_year:'2025',years:'2020',loss_percent:'14',mounting:'Free standing 2025',technology:'cSi',zone:'NORD',series:'NORD',valuation:'flat',window_start:'2025-01-01',window_end:'2026-01-01',fiscal_mode:'parameters',effective_from:'2025-01-01',effective_to:'2027-01-01',as_of:'2026-01-01',sources:'Synthetic test',ev_action:'unchanged',cooling_action:'unchanged'}))f[key].value=value;
 for(const key of ['gross','ack','confirmed'])f[key].checked=true;
 f.providerConsent.checked=false;
 await click('Save and review draft');
 assert.equal(setup.workflow.status,'draft');
 assert.ok(savedDoc,'offline draft saved without provider consent');
 assert.equal(savedDoc.consumption.kind,'monthly');
 assert.equal(savedDoc.consumption.annual_kwh,null);
 assert.deepEqual(savedDoc.consumption.monthly_totals,Array(12).fill(100));
 const statusNode=root.children.find(c=>c.attrs?.role==='status');
 assert.equal(statusNode.textContent,'Draft saved. Review inputs before generating.');
 await click('Generate solar data and compile (retry)');
 assert.equal(jobCalled,false,'generation must not be called without consent');
 assert.match(statusNode.textContent,/PROVIDER_CONSENT_REQUIRED/);
});

test('roundtrip annual and monthly profile inputs, hydration, switching, and legacy missing fields',async()=>{
 const {mount}=require('../feasibility/web/onboarding.js');
 const root=new Element('section',doc);
 const store=new Map();let rev=0;
 const client=new HouseholdClient({fetch:async(path,opts)=>{
  const body=opts.body?JSON.parse(opts.body):null;
  if(path==='/api/session')return response({csrf_token:'tab-only'});
  if(path==='/api/profiles'){
   if(opts.method==='POST'){
    const id='p_'+(++rev);
    const p={schema_version:1,id,country:'IT',timezone:'Europe/Rome',location:{latitude:45,longitude:9},consumption:{kind:'monthly',acknowledged:true},roofs:[],thermal:{end_uses:[],electric_loads:[]},market:{valuation_mode:'flat'},fiscal:{parameters:{confirmed:true}},default_settings:{baseline_cooling_kwh:0}};
    store.set(id,p);
    return response({id,revision:'r'+rev});
   }
   return response({profiles:Array.from(store.keys()).map(id=>({id,name:id}))});
  }
  const match=path.match(/^\/api\/profiles\/([^/]+)/);
  const id=match?match[1]:null;
  if(path.endsWith('/validation'))return response({ready:false,revision:'r'+rev,fiscal_rule_revision:'f'.repeat(64)});
  if(opts.method==='PATCH'){store.set(id,body.profile);rev++;}
  return response({id,revision:'r'+rev,profile:store.get(id)});
 }});
 const setup=mount({root,client});
 const all=()=>{const nodes=[];const visit=n=>{nodes.push(n);for(const c of n.children)visit(c);};visit(root);return nodes;};
 const click=async text=>{const b=all().find(n=>n.tag==='button'&&n.textContent===text);assert.ok(b,text);await b.onclick();};
 const f=setup.fields;
 const setDefaults=()=>{
  for(const [key,value]of Object.entries({country:'IT',timezone:'Europe/Rome',latitude:'45',longitude:'9',coverage_start:'2025-01-01',coverage_end:'2026-01-01',baseline_cooling_kwh:'0',reference_year:'2025',years:'2020',loss_percent:'14',mounting:'Free standing 2025',technology:'cSi',zone:'NORD',series:'NORD',valuation:'flat',window_start:'2025-01-01',window_end:'2026-01-01',fiscal_mode:'parameters',effective_from:'2025-01-01',effective_to:'2027-01-01',as_of:'2026-01-01',sources:'Synthetic test',ev_action:'unchanged',cooling_action:'unchanged'}))f[key].value=value;
  for(const key of ['gross','ack','confirmed'])f[key].checked=true;
 };
 await click('Create profile');
 const p1Id=client.profileId;
 setDefaults();f.kind.value='monthly';f.totals.value=Array(12).fill(250).join(',');f.annual.value='9999';
 await click('Save and review draft');
 const p1Saved=store.get(p1Id);
 assert.equal(p1Saved.consumption.kind,'monthly');
 assert.equal(p1Saved.consumption.annual_kwh,null);
 assert.deepEqual(p1Saved.consumption.monthly_totals,Array(12).fill(250));

 await click('Create profile');
 const p2Id=client.profileId;
 setDefaults();f.kind.value='annual';f.annual.value='4200';f.totals.value=Array(12).fill(0).join(',');
 await click('Save and review draft');
 const p2Saved=store.get(p2Id);
 assert.equal(p2Saved.consumption.kind,'annual');
 assert.equal(p2Saved.consumption.annual_kwh,4200);
 assert.equal(p2Saved.consumption.monthly_totals,null);

 const statusNode=root.children.find(c=>c.attrs?.role==='status');
 f.profile.value=p1Id;
 await f.profile.onchange();
 assert.equal(f.kind.value,'monthly');
 assert.equal(f.totals.value,Array(12).fill(250).join(','));
 assert.equal(f.annual.value,'');
 assert.equal(statusNode.textContent,'Draft selected. Confirm gross demand and re-enter CSV mapping and weather-generation choices to revise it.');

 f.profile.value=p2Id;
 await f.profile.onchange();
 assert.equal(f.kind.value,'annual');
 assert.equal(f.annual.value,'4200');
 assert.equal(f.totals.value,'');
 assert.equal(statusNode.textContent,'Draft selected. Confirm gross demand and re-enter CSV mapping and weather-generation choices to revise it.');

 setDefaults();f.kind.value='monthly';f.totals.value=Array(12).fill(300).join(',');
 await click('Save and review draft');
 const p2Switched=store.get(p2Id);
 assert.equal(p2Switched.consumption.kind,'monthly');
 assert.equal(p2Switched.consumption.annual_kwh,null);
 assert.deepEqual(p2Switched.consumption.monthly_totals,Array(12).fill(300));

 const legacyId='p_legacy';
 store.set(legacyId,{schema_version:1,id:legacyId,country:'IT',timezone:'Europe/Rome',location:{latitude:45,longitude:9},consumption:{kind:'monthly',acknowledged:true,coverage:{start:'2025-01-01',end:'2026-01-01'}},roofs:[],thermal:{end_uses:[],electric_loads:[]},market:{valuation_mode:'flat'},fiscal:{parameters:{confirmed:true}},default_settings:{baseline_cooling_kwh:0}});
 f.profile.value=legacyId;
 await f.profile.onchange();
 assert.equal(f.kind.value,'monthly');
 assert.equal(f.totals.value,'');
 assert.equal(f.annual.value,'');
 assert.equal(statusNode.textContent,'Draft selected. Confirm gross demand and re-enter monthly/annual totals, CSV mapping and weather-generation choices to revise it.');
});


test('waitJob maps allowlisted SOLAR_TIMEOUT, SOLAR_NETWORK_ERROR, and SOLAR_DATA_GAPS, keeps unknown errors generic, and respects stale generation',async()=>{
 let jobPayload={id:'j1',profile_id:'p1',revision:'r1',state:'failed',error_code:'SOLAR_TIMEOUT',error:'Solar data request timed out; retry later.'};
 const client=new HouseholdClient({fetch:async path=>{
  if(path==='/api/session')return response({csrf_token:'t'});
  if(path==='/api/jobs/j1')return response(jobPayload);
  return response({},404);
 }});
 client.select('p1','r1');

 await assert.rejects(
  client.waitJob('j1'),
  err=>{
   assert.equal(err.code,'SOLAR_TIMEOUT');
   assert.equal(err.message,'SOLAR_TIMEOUT: Solar data request timed out; retry later.');
   return true;
  }
 );

 jobPayload={id:'j1',profile_id:'p1',revision:'r1',state:'failed',error_code:'SOLAR_NETWORK_ERROR',error:'Solar data request network error; retry later.'};
 await assert.rejects(
  client.waitJob('j1'),
  err=>{
   assert.equal(err.code,'SOLAR_NETWORK_ERROR');
   assert.equal(err.message,'SOLAR_NETWORK_ERROR: Solar data request network error; retry later.');
   return true;
  }
 );

 jobPayload={id:'j1',profile_id:'p1',revision:'r1',state:'failed',error_code:'SOLAR_DATA_GAPS',error:'PVGIS data gaps exceed the supported repair limits. Review the selected weather years; no years were omitted.'};
 await assert.rejects(
  client.waitJob('j1'),
  err=>{
   assert.equal(err.code,'SOLAR_DATA_GAPS');
   assert.equal(err.message,'SOLAR_DATA_GAPS: PVGIS data gaps exceed the supported repair limits. Review the selected weather years; no years were omitted.');
   return true;
  }
 );

 jobPayload={id:'j1',profile_id:'p1',revision:'r1',state:'failed',error:'Some internal error /path/to/leak'};
 await assert.rejects(
  client.waitJob('j1'),
  err=>{
   assert.equal(err.code,undefined);
   assert.equal(err.message,'JOB_FAILED: job failed; review inputs and retry.');
   return true;
  }
 );

 const gen=client.generation;
 client.generation++;
 await assert.rejects(client.waitJob('j1',{generation:gen}),/STALE_RESPONSE/);
});
test('year menu exposes 21 supported years 2005–2025 and handles onchange DOM events', () => {
 assert.equal(SUPPORTED_YEARS.length, 21);
 assert.equal(SUPPORTED_YEARS[0], 2005);
 assert.equal(SUPPORTED_YEARS[20], 2025);

 const root = new Element('div', doc);
 const menu = new YearMenu(root, { doc });
 assert.equal(menu.inputs.size, 21);
 assert.equal(menu.getSelectedYears().length, 0);
 assert.equal(menu.allCb.checked, false);
 assert.equal(menu.allCb.indeterminate, false);
 assert.equal(menu.summaryBadge.textContent, '0 / 21 years selected');

 // Select all via native onchange event
 menu.allCb.checked = true;
 menu.allCb.onchange();
 assert.equal(menu.getSelectedYears().length, 21);
 assert.equal(menu.allCb.indeterminate, false);
 assert.equal(menu.summaryBadge.textContent, 'All 21 years (2005–2025)');

 // Uncheck 2025 via onchange event
 const cb2025 = menu.inputs.get(2025);
 cb2025.checked = false;
 cb2025.onchange();
 assert.equal(menu.getSelectedYears().length, 20);
 assert.equal(menu.allCb.checked, false);
 assert.equal(menu.allCb.indeterminate, true);
 assert.equal(menu.summaryBadge.textContent, '20 / 21 years selected');

 // Toggle 2025 back via onchange
 cb2025.checked = true;
 cb2025.onchange();
 assert.equal(menu.getSelectedYears().length, 21);
 assert.equal(menu.allCb.checked, true);
 assert.equal(menu.allCb.indeterminate, false);
});

test('year menu rejects unknown years and enforces exact submission without silent omissions', async () => {
 const root = new Element('section', doc);
 let submittedYears = null;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   const body = opts.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_y2', revision: 'r1' } : { profiles: [{ id: 'p_y2', revision: 'r1' }] });
   if (path.endsWith('/validation')) return response({ ready: false, revision: 'r1', fiscal_rule_revision: 'f'.repeat(64) });
   if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
   if (path.endsWith('/solar-jobs')) {
    submittedYears = body.years;
    return response({ id: 'job_y2', state: 'queued' });
   }
   if (path === '/api/jobs/job_y2') return response({ id: 'job_y2', profile_id: 'p_y2', revision: 'r1', state: 'complete' });
   if (path.endsWith('/compile')) return response({ compiled: true, revision: 'r2' });
   return response({ id: 'p_y2', revision: 'r1', profile: profile() });
  }
 });

 const setup = mount({ root, client });
 const allNodes = () => { const res = []; const v = n => { res.push(n); for (const c of n.children) v(c); }; v(root); return res; };
 const click = async text => { const b = allNodes().find(n => n.tag === 'button' && n.textContent === text); assert.ok(b, text); await b.onclick(); };

 await click('Create profile');
 const f = setup.fields;

 // Reject unknown year in setter
 assert.throws(() => { f.years.value = '1999'; }, /YEARS_INVALID/);
 assert.throws(() => { f.years.value = '2026'; }, /YEARS_INVALID/);

 // Exact selection
 f.years.value = '2005, 2015, 2025';
 assert.deepEqual(f.years.getSelectedYears(), [2005, 2015, 2025]);

 for (const [key, value] of Object.entries({ country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9', kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01', totals: Array(12).fill(0).join(','), baseline_cooling_kwh: '0', reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025', technology: 'cSi', zone: 'NORD', series: 'NORD', valuation: 'flat', window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters', effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01', sources: 'Synthetic test', ev_action: 'unchanged', cooling_action: 'unchanged' })) f[key].value = value;
 for (const key of ['gross', 'ack', 'confirmed', 'providerConsent']) f[key].checked = true;

 await click('Save and review draft');
 await click('Check solar coverage');
 f.years.value = '2005, 2015, 2025';
 await click('Generate solar data and compile (retry)');
 assert.deepEqual(submittedYears, [2005, 2015, 2025], 'Exact selected years submitted');

 // Deselect all and ensure rejection
 f.years.value = '';
 const statusNode = root.children.find(c => c.attrs?.role === 'status');
 await click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /YEARS_INVALID/);
});

test('year menu dynamic verified years, unsupported years disabled/not shown, and cleared on invalidation', () => {
 const root = new Element('div', doc);
 const menu = new YearMenu(root, { doc, verified: false });
 assert.equal(menu.verified, false);
 assert.equal(menu.inputs.size, 0);
 assert.equal(menu.allCb.disabled, true);
 assert.equal(menu.summaryBadge.textContent, 'No years available');

 // Dynamic verified years update
 menu.setSupportedYears([2020, 2021, 2022]);
 assert.equal(menu.verified, true);
 assert.equal(menu.inputs.size, 3);
 assert.equal(menu.allCb.disabled, false);
 assert.equal(menu.inputs.has(2020), true);
 assert.equal(menu.inputs.has(2021), true);
 assert.equal(menu.inputs.has(2022), true);
 assert.equal(menu.inputs.has(2019), false);
 assert.equal(menu.inputs.has(2023), false);
 assert.equal(menu.summaryBadge.textContent, '0 / 3 years selected');

 // Select verified years and test unsupported rejection
 menu.setSelectedYears([2020, 2022]);
 assert.deepEqual(menu.getSelectedYears(), [2020, 2022]);
 assert.equal(menu.summaryBadge.textContent, '2 / 3 years selected');
 assert.throws(() => menu.setSelectedYears([2019]), /YEARS_INVALID/);
 assert.throws(() => menu.setSelectedYears([2023]), /YEARS_INVALID/);

 // Shrink supported years drops previously selected unsupported years
 menu.setSupportedYears([2022]);
 assert.deepEqual(menu.getSelectedYears(), [2022]);
 assert.equal(menu.inputs.size, 1);
 assert.equal(menu.summaryBadge.textContent, 'All 1 years (2022)');

 // Cleared on invalidation (setSupportedYears([]))
 menu.setSupportedYears([]);
 assert.equal(menu.verified, false);
 assert.equal(menu.inputs.size, 0);
 assert.equal(menu.allCb.disabled, true);
 assert.equal(menu.summaryBadge.textContent, 'No years available');
});

test('end-to-end solar coverage checks enforce explicit consent, stale revision/location invalidation, and generation refusal', async () => {
 const root = new Element('section', doc);
 let rev = 0, coverageCalls = 0, jobCalled = false, compiled = false, submittedYears = null;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   const body = opts.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_cov', revision: 'r' + (++rev) } : { profiles: [{ id: 'p_cov', revision: 'r' + rev }] });
   if (path.endsWith('/validation')) return response({ ready: compiled, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (path.endsWith('/solar-coverage')) {
    coverageCalls++;
    return response({ years: [2020, 2021, 2022] });
   }
   if (path.endsWith('/solar-jobs')) {
    jobCalled = true;
    submittedYears = body.years;
    return response({ id: 'job_cov', state: 'queued' });
   }
   if (path === '/api/jobs/job_cov') return response({ id: 'job_cov', profile_id: 'p_cov', revision: 'r' + rev, state: 'complete' });
   if (path.endsWith('/compile')) { compiled = true; return response({ compiled: true, revision: 'r' + (++rev) }); }
   if (opts.method === 'PATCH') rev++;
   return response({ id: 'p_cov', revision: 'r' + rev, profile: profile() });
  }
 });

 const setup = mount({ root, client });
 const allNodes = () => { const res = []; const v = n => { res.push(n); for (const c of n.children) v(c); }; v(root); return res; };
 const click = async text => { const b = allNodes().find(n => n.tag === 'button' && n.textContent === text); assert.ok(b, text); await b.onclick(); };
 const statusNode = root.children.find(c => c.attrs?.role === 'status');
 const f = setup.fields;

 await click('Create profile');

 for (const [key, value] of Object.entries({
  country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
  kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
  totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '0',
  reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025',
  technology: 'cSi', zone: 'NORD', series: 'NORD', valuation: 'flat',
  window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
  effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
  sources: 'Synthetic test', ev_action: 'unchanged', cooling_action: 'unchanged'
 })) f[key].value = value;
 for (const key of ['gross', 'ack', 'confirmed']) f[key].checked = true;

 f.providerConsent.checked = true;
 const formEl = allNodes().find(n => n.tag === 'form');
 formEl.listeners.input({ target: f.latitude });
 await click('Check solar coverage');
 assert.match(statusNode.textContent, /SAVE_REQUIRED/);

 await click('Save and review draft');
 assert.equal(statusNode.textContent, 'Draft saved. Review inputs before generating.');

 f.providerConsent.checked = false;
 await click('Check solar coverage');
 assert.match(statusNode.textContent, /PROVIDER_CONSENT_REQUIRED/);
 assert.equal(coverageCalls, 0);

 f.providerConsent.checked = true;
 await click('Check solar coverage');
 assert.equal(coverageCalls, 1);
 assert.equal(statusNode.textContent, 'Solar coverage verified: 3 supported years.');
 assert.deepEqual(f.years.getSelectedYears(), []);

 assert.throws(() => { f.years.value = '2019'; }, /YEARS_INVALID/);
 f.years.value = '2020, 2021';
 assert.deepEqual(f.years.getSelectedYears(), [2020, 2021]);

 f.latitude.value = '46';
 formEl.listeners.input({ target: f.latitude });
 assert.deepEqual(f.years.getSelectedYears(), []);
 await click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /COVERAGE_STALE/);
 assert.equal(jobCalled, false);

 await click('Save and review draft');
 await click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /COVERAGE_STALE/);

 await click('Check solar coverage');
 assert.equal(coverageCalls, 2);
 f.years.value = '2020, 2022';

 f.providerConsent.checked = false;
 await click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /PROVIDER_CONSENT_REQUIRED/);
 assert.equal(jobCalled, false);

 f.providerConsent.checked = true;
 client.revision = 'r_stale';
 await click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /COVERAGE_STALE/);
 assert.equal(jobCalled, false);

 client.revision = 'r' + rev;
 await click('Check solar coverage');
 f.years.value = '2020, 2021';
 await click('Generate solar data and compile (retry)');
 assert.equal(jobCalled, true);
 assert.deepEqual(submittedYears, [2020, 2021]);
 assert.equal(setup.workflow.status, 'ready');
});
