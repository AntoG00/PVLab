'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {HouseholdClient,HouseholdStorage}=require('../feasibility/web/sync.js');
const {Workflow,numbers,validateDraft,csvMetadata,RoofListEditor,YearMenu,SUPPORTED_YEARS,mount,parseCoordinatePair}=require('../feasibility/web/onboarding.js');
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
 constructor(tag,doc){this.tag=tag;this.ownerDocument=doc;this.children=[];this.attrs={};this.listeners={};this._text='';this.value='';this.parentNode=null;this.hidden=false;this.disabled=false;}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text;}
 appendChild(n){if(n.parentNode)n.parentNode.removeChild(n);n.parentNode=this;this.children.push(n);return n;}
 removeChild(n){const i=this.children.indexOf(n);if(i!==-1){this.children.splice(i,1);n.parentNode=null;}return n;}
 remove(){if(this.parentNode)this.parentNode.removeChild(this);}
 setAttribute(k,v){this.attrs[k]=String(v);}removeAttribute(k){delete this.attrs[k];}getAttribute(k){return this.attrs[k]!==undefined?this.attrs[k]:null;}addEventListener(k,f){const prev=this.listeners[k];this.listeners[k]=prev?(e)=>{prev.call(this,e);f.call(this,e);}:f;}focus(){this.focused=true;}
 dispatchEvent(evt){let defaultPrevented=false;const e=Object.assign({},evt,{type:evt?.type||'event',bubbles:Boolean(evt?.bubbles),target:this,preventDefault:()=>{defaultPrevented=true;if(typeof evt?.preventDefault==='function')evt.preventDefault();},get defaultPrevented(){return defaultPrevented;}});let curr=this;while(curr){e.currentTarget=curr;if(curr.listeners&&curr.listeners[e.type])curr.listeners[e.type](e);const inline=curr['on'+e.type];if(typeof inline==='function')inline.call(curr,e);if(!e.bubbles)break;curr=curr.parentNode;}return !defaultPrevented;}
 set innerHTML(v){throw Error('DOM injection sink');}
 reportValidity(){return true;}
 reset(){if(this.listeners?.reset)try{this.listeners.reset({type:'reset',target:this});}catch(_){}const visit=n=>{if(n.defaultValue!==undefined)n.value=n.defaultValue;if(n.defaultChecked!==undefined)n.checked=n.defaultChecked;for(const c of n.children)visit(c);};visit(this);}
}
const doc={createElement:tag=>new Element(tag,doc)};
test('roof editor adds/removes stable IDs and renders labels only as text',()=>{
 const root=new Element('div',doc),editor=new RoofListEditor(root,{doc});const r=editor.addRoof({label:'<img onerror=alert(1)>',tilt_deg:20,azimuth_deg:180,max_kwp:3,shading_fraction:.1});assert.equal(editor.getRoofs()[0].label,r.label);assert.equal(editor.listEl.children[0].children[0].textContent,r.label);editor.removeRoof(r.id);assert.equal(editor.getRoofs().length,0);assert.equal(editor.addBtn.focused,true);
});
test('roof editor disables add button at 12 with explanation, ignores normal clicks, and re-enables on removal',()=>{
 const root=new Element('div',doc),editor=new RoofListEditor(root,{doc});
 for(let i=0;i<12;i++)editor.addRoof({max_kwp:2});
 assert.equal(editor.getRoofs().length,12);
 assert.equal(editor.addBtn.disabled,true);
 assert.equal(editor.addBtn.getAttribute('aria-disabled'),'true');
 assert.equal(editor.limitNote.hidden,false);
 assert.match(editor.limitNote.textContent,/12 roof planes/i);
 // Normal click does not throw and does not add a 13th roof
 assert.doesNotThrow(()=>editor.addBtn.dispatchEvent({type:'click'}));
 assert.equal(editor.getRoofs().length,12);
 // Programmatic addRoof still enforces and throws ROOF_LIMIT
 assert.throws(()=>editor.addRoof({max_kwp:2}),/ROOF_LIMIT/);
 // Removal re-enables add button and clears/hides explanation
 const id=editor.getRoofs()[0].id;
 editor.removeRoof(id);
 assert.equal(editor.getRoofs().length,11);
 assert.equal(editor.addBtn.disabled,false);
 assert.equal(editor.limitNote.hidden,true);
 // Can add roof again
 editor.addBtn.dispatchEvent({type:'click'});
 assert.equal(editor.getRoofs().length,12);
 assert.equal(editor.addBtn.disabled,true);
 assert.equal(editor.limitNote.hidden,false);
});
test('roof editor handles loading 12, null/undefined normalization, and rejects malformed data',()=>{
 const twelve=Array.from({length:12},(_,i)=>({id:'r_'+i,label:'R '+i,max_kwp:2}));
 const ed12=new RoofListEditor(new Element('div',doc),{doc,initialRoofs:twelve});
 assert.equal(ed12.getRoofs().length,12);
 assert.equal(ed12.addBtn.disabled,true);
 assert.equal(ed12.limitNote.hidden,false);
 assert.throws(()=>ed12.setRoofs(Array.from({length:13},(_,i)=>({id:'r_'+i}))),/ROOF_LIMIT/);
 const edNull=new RoofListEditor(new Element('div',doc),{doc,initialRoofs:null});
 assert.equal(edNull.getRoofs().length,0);
 edNull.setRoofs(twelve);
 assert.equal(edNull.addBtn.disabled,true);
 assert.match(edNull.limitNote.textContent,/12 roof planes/i);
 assert.doesNotThrow(()=>edNull.setRoofs(null));
 assert.equal(edNull.getRoofs().length,0);
 assert.doesNotThrow(()=>edNull.setRoofs(undefined));
 assert.equal(edNull.getRoofs().length,0);
 assert.doesNotThrow(()=>edNull.setRoofs());
 assert.equal(edNull.getRoofs().length,0);
 assert.equal(edNull.addBtn.disabled,false);
 assert.throws(()=>edNull.setRoofs('not-an-array'),/ROOF_INVALID/);
 assert.throws(()=>edNull.setRoofs(123),/ROOF_INVALID/);
 assert.throws(()=>edNull.setRoofs([null]),/ROOF_INVALID/);
 assert.throws(()=>edNull.setRoofs([[]]),/ROOF_INVALID/);
 assert.throws(()=>edNull.setRoofs(['invalid']),/ROOF_INVALID/);
 assert.throws(()=>edNull.addRoof('invalid'),/ROOF_INVALID/);
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
 Element.prototype.reset=function(){if(this.listeners?.reset)try{this.listeners.reset({type:'reset',target:this});}catch(_){}const visit=n=>{if(n.defaultValue!==undefined)n.value=n.defaultValue;if(n.defaultChecked!==undefined)n.checked=n.defaultChecked;for(const c of n.children)visit(c);};visit(this);};
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
 assert.equal(menu.allCb.name, 'weather_year');
 assert.equal(menu.allCb.value, 'all');
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
 let rev = 0, coverageCalls = 0, jobCalled = false, compiled = false, submittedYears = null, submittedConsent = null;
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
    submittedConsent = body.consent;
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
 assert.equal(submittedConsent, true);
 assert.deepEqual(submittedYears, [2020, 2021]);
 assert.equal(setup.workflow.status, 'ready');
});

test('selecting all historical weather years via select-all checkbox preserves verified coverage without disappearing', async () => {
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
 await click('Save and review draft');
 await click('Check solar coverage');
 assert.equal(coverageCalls, 1);
 assert.equal(statusNode.textContent, 'Solar coverage verified: 3 supported years.');
 assert.deepEqual(f.years.getSelectedYears(), []);

 const allCb = allNodes().find(n => n.parentNode?.className === 'year-select-all');
 assert.ok(allCb, 'Select all checkbox exists');
 assert.equal(allCb.name, 'weather_year');
 assert.equal(allCb.value, 'all');

 allCb.checked = true;
 allCb.dispatchEvent(new (doc.defaultView?.Event || Event)('input', { bubbles: true }));
 allCb.onchange();

 assert.deepEqual(f.years.getSelectedYears(), [2020, 2021, 2022]);
 const badge = allNodes().find(n => n.className === 'year-summary-badge');
 assert.equal(badge.textContent, 'All 3 years (2020–2022)');
 const yearCbs = allNodes().filter(n => n.tag === 'input' && n.name === 'weather_year' && n.value !== 'all');
 assert.equal(yearCbs.length, 3);
 assert.equal(yearCbs.every(cb => cb.checked), true);

 await click('Generate solar data and compile (retry)');
 assert.equal(jobCalled, true);
 assert.deepEqual(submittedYears, [2020, 2021, 2022]);
 assert.equal(setup.workflow.status, 'ready');
});

test('Section 5 percentage fields scaling, serialization, hydration, and bounds validation', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 let savedDoc = null, rev = 0;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   const body = opts.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 'tab-only' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_pct', revision: 'r' + (++rev) } : { profiles: [{ id: 'p_pct', revision: 'r' + rev }] });
   if (path.endsWith('/validation')) return response({ ready: false, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (opts.method === 'PATCH') { savedDoc = body.profile; rev++; }
   return response({ id: 'p_pct', revision: 'r' + rev, profile: savedDoc });
  }
 });
 const setup = mount({ root, client });
 const all = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root); return nodes; };
 const click = async text => { const b = all().find(n => n.tag === 'button' && n.textContent === text); assert.ok(b, text); await b.onclick(); };
 await click('Create profile');
 const f = setup.fields;

 for (const [k, v] of Object.entries({
  country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
  kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
  totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '0',
  reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025',
  technology: 'cSi', zone: 'CSUD', series: 'CSUD', valuation: 'flat',
  window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
  effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
  sources: 'Synthetic test'
 })) f[k].value = v;
 for (const k of ['gross', 'ack', 'confirmed']) f[k].checked = true;

 f.electric_vat.value = '10';
 f.pmg_nominal_escalation.value = '1.5';
 f.gse_fee_vat.value = '22';
 f.export_irpef.value = '35';
 f.regional_additional_tax.value = '1.7';
 f.municipal_additional_tax.value = '0.8';
 f.deduction_rate.value = '50';
 f.gas_vat_reduced.value = '10';
 f.gas_vat_standard.value = '22';

 f.retail_commodity.value = '0.135';
 f.retail_regulated.value = '0.045';
 f.electric_excise.value = '0.0227';
 f.pmg_price.value = '0.0475';
 f.gse_fee_eur_kw_year.value = '0.70';
 f.gas_excise_0_120.value = '0.044';
 f.gas_excise_120_480.value = '0.175';
 f.gas_excise_480_1560.value = '0.170';
 f.gas_excise_over_1560.value = '0.186';
 f.gas_regional_0_120.value = '0.02';
 f.gas_regional_over_120.value = '0.03';

 await click('Save and review draft');
 assert.ok(savedDoc);
 const p = savedDoc.fiscal.parameters;

 assert.equal(p.electric_vat, 0.1);
 assert.equal(p.pmg_nominal_escalation, 0.015);
 assert.equal(p.gse_fee_vat, 0.22);
 assert.equal(p.export_irpef, 0.35);
 assert.equal(p.regional_additional_tax, 0.017);
 assert.equal(p.municipal_additional_tax, 0.008);
 assert.equal(p.deduction_rate, 0.50);
 assert.equal(p.gas_vat_reduced, 0.10);
 assert.equal(p.gas_vat_standard, 0.22);

 assert.equal(p.electric_excise, 0.0227);
 assert.equal(p.pmg_price, 0.0475);
 assert.equal(p.gse_fee_eur_kw_year, 0.70);
 assert.equal(p.gas_excise_0_120, 0.044);
 assert.equal(p.gas_excise_120_480, 0.175);
 assert.equal(savedDoc.default_settings.retail_commodity, 0.135);
 assert.equal(savedDoc.default_settings.retail_regulated, 0.045);

 f.profile.value = 'p_pct';
 await f.profile.onchange();
 assert.equal(f.electric_vat.value, '10');
 assert.equal(f.pmg_nominal_escalation.value, '1.5');
 assert.equal(f.gse_fee_vat.value, '22');
 assert.equal(f.export_irpef.value, '35');
 assert.equal(f.regional_additional_tax.value, '1.7');
 assert.equal(f.municipal_additional_tax.value, '0.8');
 assert.equal(f.deduction_rate.value, '50');
 assert.equal(f.gas_vat_reduced.value, '10');
 assert.equal(f.gas_vat_standard.value, '22');

 f.electric_vat.value = '105';
 f.reference_year.value = '2025';
 await click('Save and review draft');
 const statusNode = root.children.find(c => c.attrs?.role === 'status');
 assert.match(statusNode.textContent, /PERCENT_LIMIT/);
});

test('Section 5 stateless presets invalidate ready/dirty, update live summary and inputs', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_preset', revision: 'r1' } : { profiles: [{ id: 'p_preset', revision: 'r1' }] });
   if (path.endsWith('/validation')) return response({ ready: true, revision: 'r1', fiscal_rule_revision: 'f'.repeat(64) });
   return response({ id: 'p_preset', revision: 'r1', profile: profile() });
  }
 });
 const setup = mount({ root, client });
 const all = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root); return nodes; };
 const click = async text => { const b = all().find(n => n.tag === 'button' && n.textContent?.includes(text)); assert.ok(b, text); await b.onclick(); };
 await click('Create profile');
 const f = setup.fields;

 assert.equal(f.electric_excise.value, '0.0227');
 assert.equal(f.electric_vat.value, '10');
 assert.equal(f.retail_commodity.value, '0.135');
 assert.equal(f.retail_regulated.value, '0.045');

 const summaryEl = all().find(n => n.className === 'summary-value');
 assert.ok(summaryEl);
 assert.ok(summaryEl.textContent.includes('0.2230'));

 await click('Altri Usi / P.IVA');
 assert.equal(f.electric_excise.value, '0.0125');
 assert.equal(f.electric_vat.value, '22');
 assert.equal(setup.workflow.status, 'draft');
 assert.equal(client.ready, false);
 assert.ok(summaryEl.textContent.includes('0.2349'));

 await click('Prima Casa');
 assert.equal(f.electric_excise.value, '0.0227');
 assert.equal(f.electric_vat.value, '10');
 assert.ok(summaryEl.textContent.includes('0.2230'));

 await click('Centro-Nord');
 assert.equal(f.gas_excise_0_120.value, '0.044');
 assert.equal(f.gas_excise_120_480.value, '0.175');
 assert.equal(f.gas_excise_480_1560.value, '0.170');
 assert.equal(f.gas_excise_over_1560.value, '0.186');
 assert.equal(f.gas_vat_reduced.value, '10');
 assert.equal(f.gas_vat_standard.value, '22');

 await click('Sud e Isole');
 assert.equal(f.gas_excise_0_120.value, '0.038');
 assert.equal(f.gas_excise_120_480.value, '0.135');
 assert.equal(f.gas_excise_480_1560.value, '0.120');
 assert.equal(f.gas_excise_over_1560.value, '0.150');

 await click('Scaglione 28k€');
 assert.equal(f.export_irpef.value, '23');
 assert.equal(f.regional_additional_tax.value, '1.5');
 assert.equal(f.municipal_additional_tax.value, '0.8');

 await click('Scaglione >50k€');
 assert.equal(f.export_irpef.value, '43');
 assert.equal(f.regional_additional_tax.value, '2.0');
 assert.equal(f.municipal_additional_tax.value, '0.8');
});

test('Section 5 gas fiscal modes switch, visibility, value preservation in payload and UI', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 let savedDoc = null, rev = 0;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   const body = opts.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_mode', revision: 'r' + (++rev) } : { profiles: [{ id: 'p_mode', revision: 'r' + rev }] });
   if (path.endsWith('/validation')) return response({ ready: false, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (opts.method === 'PATCH') { savedDoc = body.profile; rev++; }
   return response({ id: 'p_mode', revision: 'r' + rev, profile: savedDoc });
  }
 });
 const setup = mount({ root, client });
 const all = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root); return nodes; };
 const click = async text => { const b = all().find(n => n.tag === 'button' && n.textContent === text); assert.ok(b, text); await b.onclick(); };
 await click('Create profile');
 const f = setup.fields;

 for (const [k, v] of Object.entries({
  country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
  kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
  totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '0',
  reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025',
  technology: 'cSi', zone: 'CSUD', series: 'CSUD', valuation: 'flat',
  window_start: '2025-01-01', window_end: '2026-01-01',
  effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
  sources: 'Synthetic test'
 })) f[k].value = v;
 for (const k of ['gross', 'ack', 'confirmed']) f[k].checked = true;

 assert.equal(f.fiscal_mode.value, 'parameters');
 assert.equal(f.annual_bill_eur.disabled, true);
 assert.equal(f.gas_excise_0_120.disabled, false);

 assert.equal(f.gas_region.tag, 'select');
 assert.equal(f.gas_region.children.length, 21);
 const campaniaOpt = f.gas_region.children.find(c => c.value === 'CAMPANIA');
 assert.ok(campaniaOpt);
 assert.equal(campaniaOpt.textContent, 'Campania');
 const lombardiaOpt = f.gas_region.children.find(c => c.value === 'LOMBARDIA');
 assert.ok(lombardiaOpt);
 assert.equal(lombardiaOpt.textContent, 'Lombardia / Lombardy');

 f.gas_region.value = 'LOMBARDIA_CUSTOM';
 f.gas_excise_0_120.value = '0.044';
 f.gas_vat_reduced.value = '10';

 // Switch mode via input event on hidden input
 f.fiscal_mode.value = 'annual_bill';
 f.fiscal_mode.listeners?.input?.({ target: f.fiscal_mode });
 assert.equal(f.fiscal_mode.value, 'annual_bill');
 assert.equal(f.annual_bill_eur.disabled, false);
 assert.equal(f.gas_excise_0_120.disabled, true);

 assert.equal(f.gas_excise_0_120.value, '0.044');
 assert.equal(f.gas_vat_reduced.value, '10');
 assert.equal(f.gas_region.value, 'LOMBARDIA_CUSTOM');

 f.annual_bill_eur.value = '1200';
 await click('Save and review draft');
 assert.ok(savedDoc);
 assert.equal(savedDoc.fiscal.parameters.mode, 'annual_bill');
 assert.equal(savedDoc.fiscal.parameters.annual_bill_eur, 1200);
 assert.equal(savedDoc.fiscal.gas_region, 'LOMBARDIA_CUSTOM');
 // Inactive gas values preserved in payload instead of dropped
 assert.equal(savedDoc.fiscal.parameters.gas_excise_0_120, 0.044);
 assert.equal(savedDoc.fiscal.parameters.gas_vat_reduced, 0.10);

 f.fiscal_mode.value = 'parameters';
 f.fiscal_mode.listeners?.input?.({ target: f.fiscal_mode });
 assert.equal(f.annual_bill_eur.disabled, true);
 assert.equal(f.gas_excise_0_120.disabled, false);
 assert.equal(f.gas_excise_0_120.value, '0.044');
 assert.equal(f.annual_bill_eur.value, '1200');

 await click('Save and review draft');
 assert.equal(savedDoc.fiscal.parameters.mode, 'parameters');
 assert.equal(savedDoc.fiscal.parameters.gas_excise_0_120, 0.044);
 assert.equal(savedDoc.fiscal.parameters.annual_bill_eur, 1200);

 // Hydration preserves custom gas_region and appends option if not present
 f.profile.value = 'p_mode';
 await f.profile.onchange();
 assert.equal(f.gas_region.value, 'LOMBARDIA_CUSTOM');
 const customOpt = f.gas_region.children.find(c => c.value === 'LOMBARDIA_CUSTOM');
 assert.ok(customOpt);
 assert.equal(customOpt.value, 'LOMBARDIA_CUSTOM');

 f.gas_region.value = 'CAMPANIA';
 f.reference_year.value = '2025';
 f.gross.checked = true;
 await click('Save and review draft');
 assert.equal(savedDoc.fiscal.gas_region, 'CAMPANIA');
});

test('Section 5 retail values in default_settings and advanced drawer collapse structure', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 let savedDoc = null, rev = 0;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   const body = opts.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_ret', revision: 'r' + (++rev) } : { profiles: [{ id: 'p_ret', revision: 'r' + rev }] });
   if (path.endsWith('/validation')) return response({ ready: false, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (opts.method === 'PATCH') { savedDoc = body.profile; rev++; }
   return response({ id: 'p_ret', revision: 'r' + rev, profile: savedDoc });
  }
 });
 const setup = mount({ root, client });
 const all = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root); return nodes; };
 const click = async text => { const b = all().find(n => n.tag === 'button' && n.textContent === text); assert.ok(b, text); await b.onclick(); };
 await click('Create profile');
 const f = setup.fields;

 for (const [k, v] of Object.entries({
  country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
  kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
  totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '150',
  reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025',
  technology: 'cSi', zone: 'CSUD', series: 'CSUD', valuation: 'flat',
  window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
  effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
  sources: 'https://arera.it\nhttps://gse.it'
 })) f[k].value = v;
 for (const k of ['gross', 'ack', 'confirmed']) f[k].checked = true;

 f.retail_commodity.value = '0.165';
 f.retail_regulated.value = '0.055';

 await click('Save and review draft');
 assert.ok(savedDoc);
 assert.equal(savedDoc.default_settings.retail_contract, 'fixed');
 assert.equal(savedDoc.default_settings.baseline_cooling_kwh, 150);
 assert.equal(savedDoc.default_settings.retail_commodity, 0.165);
 assert.equal(savedDoc.default_settings.retail_regulated, 0.055);
 assert.equal(savedDoc.fiscal.parameters.policy_version, 'italy-fiscal-v1');
 assert.deepEqual(savedDoc.fiscal.parameters.sources, ['https://arera.it', 'https://gse.it']);

 const detailsEl = all().find(n => n.tag === 'details' && n.className === 'fiscal-advanced-details');
 assert.ok(detailsEl, 'advanced details drawer must exist');
 const summaryEl = detailsEl.children.find(c => c.tag === 'summary');
 assert.ok(summaryEl.textContent.includes('Parametri Avanzati'));

 assert.equal(detailsEl.children.includes(f.confirmed), false);
 assert.ok(f.confirmed);
});

function mountSection2Helper(initialDoc = null) {
 const root = new Element('section', doc); let rev = 0, savedDoc = null, savedLoad = null; const store = new Map();
 if (initialDoc) store.set(initialDoc.id, initialDoc);
 const client = new HouseholdClient({ fetch: async (path, opts) => {
  const b = opts.body ? JSON.parse(opts.body) : null;
  if (path === '/api/session') return response({ csrf_token: 't' });
  if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_' + (++rev) } : { profiles: Array.from(store.keys()).map(id => ({ id })) });
  if (path.endsWith('/validation')) return response({ ready: false, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
  const id = path.split('/').pop(); if (opts.method === 'PATCH') { savedDoc = b.profile; rev++; if (id) store.set(id, savedDoc); }
  return response({ id: id || 'p_1', revision: 'r' + rev, profile: store.get(id) || savedDoc || profile() });
 }});
 const setup = mount({ root, client });
 const all = () => { const res = []; const v = n => { res.push(n); for (const c of n.children) v(c); }; v(root); return res; };
 const click = async txt => { const b = all().find(n => n.tag === 'button' && (n.textContent === txt || n.textContent?.includes(txt))); assert.ok(b, 'btn: ' + txt); await b.onclick(); };
 const origSave = setup.workflow.save.bind(setup.workflow);
 setup.workflow.save = async (p, l, c) => { savedDoc = p; savedLoad = l; return origSave(p, l, c); };
 const fillBase = () => {
  for (const [k, v] of Object.entries({ country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9', reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025', technology: 'cSi', zone: 'CSUD', series: 'CSUD', valuation: 'flat', window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters', effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01', sources: 'Synthetic test', ev_action: 'unchanged', cooling_action: 'unchanged', retail_commodity: '0.135', retail_regulated: '0.045', baseline_cooling_kwh: '0', coverage_start: '2025-01-01', coverage_end: '2026-01-01' })) if (setup.fields[k]) setup.fields[k].value = v;
  for (const k of ['gross', 'ack', 'confirmed']) if (setup.fields[k]) setup.fields[k].checked = true;
 };
 return { root, client, setup, f: setup.fields, all, click, fillBase, getSaved: () => ({ profile: savedDoc, load: savedLoad }), store };
}

test('Section 2 active kind visibility, disabled controls, and hidden negative field tolerance', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 const [annC, monC, csvC, rawWrap] = ['consumption-annual-container', 'consumption-monthly-container', 'consumption-csv-container', 'totals-raw-wrap'].map(c => h.all().find(n => n.className === c));
 assert.ok(annC.hidden && monC.hidden && csvC.hidden);
 h.f.kind.value = 'annual';
 assert.ok(!annC.hidden && monC.hidden && csvC.hidden && rawWrap.hidden);
 assert.equal(h.f.totals.disabled,true);assert.equal(h.f.csv.disabled,true);assert.equal(h.f.annual.disabled,false);
 h.fillBase(); h.f.annual.value = '3000'; h.f.totals.value = '-50,0,0,0,0,0,0,0,0,0,0,0';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.consumption.annual_kwh, 3000);
 assert.equal(h.getSaved().profile.consumption.monthly_totals, null);
 h.f.kind.value = 'monthly';
 assert.ok(annC.hidden && !monC.hidden && csvC.hidden && !rawWrap.hidden);
 assert.equal(h.f.annual.disabled,true);assert.equal(h.f.monthly_f1.disabled,true);
 h.f.annual.value = '-999'; h.f.totals.value = Array(12).fill(200).join(',');
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.consumption.annual_kwh, null);
 assert.deepEqual(h.getSaved().profile.consumption.monthly_totals, Array(12).fill(200));
 h.f.kind.value = 'interval';
 assert.ok(annC.hidden && monC.hidden && !csvC.hidden && !rawWrap.hidden);
});

test('Section 2 monthly Mode A raw-to-grid and grid-to-raw bidirectional sync with zero and empty preservation', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = 'monthly';
 const g = Array.from({ length: 12 }, (_, i) => h.all().find(n => n.name === 'totals_' + i));
 h.f.totals.value = '100, 0, 250, 0, 0, 0, 0, 0, 0, 0, 0, 0';
 h.f.totals.oninput();
 assert.equal(g[0].value, '100'); assert.equal(g[1].value, '0'); assert.equal(g[2].value, '250');
 g[0].value = '350'; g[1].value = '0'; g[2].value = ''; g[0].oninput();
 assert.deepEqual(h.f.totals.value.split(',').slice(0,3), ['350','0','']);
 g[2].value = '180'; g[2].oninput(); assert.equal(h.f.totals.value.split(',')[2], '180');
});

test('Section 2 switching Mode A to Mode B clears and initializes all band inputs to empty', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = 'monthly'; h.f.totals.value = Array(12).fill(200).join(',');
 h.f.monthly_mode.value = 'B';
 assert.ok(!h.f.monthly_f1.value && !h.f.monthly_f2.value && !h.f.monthly_f3.value);
 for (const k of ['monthly_f1', 'monthly_f2', 'monthly_f3']) for (let i = 0; i < 12; i++) assert.equal(h.all().find(n => n.name === k + '_' + i).value, '');
 for (const sc of h.all().filter(n => n.className === 'sum-cell')) assert.equal(sc.textContent, '-');
 assert.equal(h.all().find(n => n.className === 'f1-share-info').textContent, '');
});

test('Section 2 valid Mode B saves TOU band arrays and omits monthly_totals in consumption and load policy', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'monthly'; h.f.monthly_mode.value = 'B';
 assert.equal(h.f.totals.disabled,true);assert.equal(h.f.monthly_f1.disabled,false);
 h.f.monthly_f1.value = Array(12).fill(100).join(','); h.f.monthly_f2.value = Array(12).fill(60).join(','); h.f.monthly_f3.value = Array(12).fill(40).join(',');
 await h.click('Save and review draft');
 const { profile: p, load: l } = h.getSaved();
 assert.equal(p.consumption.kind, 'monthly');
 assert.deepEqual(p.consumption.monthly_f1, Array(12).fill(100)); assert.deepEqual(p.consumption.monthly_f2, Array(12).fill(60)); assert.deepEqual(p.consumption.monthly_f3, Array(12).fill(40));
 assert.equal('monthly_totals' in p.consumption, false); assert.equal(p.consumption.annual_kwh, null);
 assert.deepEqual(l.policy.monthly_f1, Array(12).fill(100)); assert.deepEqual(l.policy.monthly_f2, Array(12).fill(60)); assert.deepEqual(l.policy.monthly_f3, Array(12).fill(40));
 assert.equal('monthly_totals' in l, false);
});

test('Section 2 switching Mode B to Mode A derives monthly totals and clears band state', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = 'monthly'; h.f.monthly_mode.value = 'B';
 h.f.monthly_f1.value = Array(12).fill(110).join(','); h.f.monthly_f2.value = Array(12).fill(50).join(','); h.f.monthly_f3.value = Array(12).fill(40).join(',');
 h.f.monthly_mode.value = 'A';
 assert.equal(h.f.totals.value, Array(12).fill(200).join(','));
 assert.equal(h.all().find(n => n.name === 'totals_0').value, '200');
 assert.ok(!h.f.monthly_f1.value && !h.f.monthly_f2.value && !h.f.monthly_f3.value);
});

test('Section 2 switching incomplete Mode B to Mode A clears totals without retaining stale numbers', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = 'monthly'; h.f.totals.value = Array(12).fill(500).join(',');
 h.f.monthly_mode.value = 'B'; h.f.monthly_f1.value = '100, 200'; h.f.monthly_mode.value = 'A';
 assert.equal(h.f.totals.value, '');
 assert.equal(h.all().find(n => n.name === 'totals_0').value, '');
});

test('Section 2 annual calculator requires three finite inputs and validates 0.28/0.38 thresholds', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = 'annual';
 const lbls = h.all().filter(n => n.tag === 'label');
 const c = [1, 2, 3].map(i => lbls.find(l => l.textContent?.includes('Annual F' + i)).children.find(n => n.tag === 'input'));
 const sumEl = h.all().find(n => n.className === 'calc-summary');
 const applyBtn = h.all().find(n => n.tag === 'button' && n.textContent === 'Apply recommended variant');

 c[0].value = '300'; c[1].value = ''; c[2].value = '300'; c[0].oninput();
 assert.ok(!sumEl.textContent && applyBtn.hidden);
 c[1].value = '-10'; c[1].oninput(); assert.ok(!sumEl.textContent && applyBtn.hidden);

 c[0].value = '200'; c[1].value = '400'; c[2].value = '400'; c[0].oninput();
 assert.ok(/Evening\/Night-heavy/.test(sumEl.textContent) && /20\.0%/.test(sumEl.textContent) && !applyBtn.hidden);
 c[0].value = '280'; c[1].value = '360'; c[2].value = '360'; c[0].oninput();
 assert.ok(/Flat \/ Continuous/.test(sumEl.textContent) && /28\.0%/.test(sumEl.textContent));
 c[0].value = '380'; c[1].value = '310'; c[2].value = '310'; c[0].oninput();
 assert.ok(/Daytime-heavy/.test(sumEl.textContent) && /38\.0%/.test(sumEl.textContent));
 c.forEach(input=>{input.value='0';});c[0].oninput();assert.equal(h.f.annual.value,'0');assert.equal(applyBtn.hidden,true);
});

test('Section 2 annual calculator apply changes only variant and annual payload omits bands', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'annual'; h.f.variant.value = 'evening';
 const lbls = h.all().filter(n => n.tag === 'label');
 const c = [1, 2, 3].map(i => lbls.find(l => l.textContent?.includes('Annual F' + i)).children.find(n => n.tag === 'input'));
 c[0].value = '400'; c[1].value = '300'; c[2].value = '300'; c[0].oninput();
 h.f.annual.value='1200';
 await h.click('Apply recommended variant');
 assert.equal(h.f.variant.value, 'daytime'); assert.equal(h.f.annual.value, '1200');
 await h.click('Save and review draft');
 const { profile: p, load: l } = h.getSaved();
 assert.equal(p.consumption.annual_kwh, 1200); assert.equal(p.consumption.monthly_totals, null);
 assert.ok(!('monthly_f1' in p.consumption) && !('monthly_f2' in p.consumption) && !('monthly_f3' in p.consumption));
 assert.ok(!('monthly_f1' in l.policy) && !('monthly_f2' in l.policy) && !('monthly_f3' in l.policy));
});

test('Section 2 Mode B full-year calculation computes F1 share without variant recommendation', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = 'monthly'; h.f.monthly_mode.value = 'B'; h.f.variant.value = 'evening';
 h.f.monthly_f1.value = Array(12).fill(100).join(','); h.f.monthly_f2.value = Array(12).fill(100).join(','); h.f.monthly_f3.value = Array(12).fill(100).join(',');
 h.f.monthly_f1.oninput();
 const txt = h.all().find(n => n.className === 'f1-share-info').textContent;
 assert.match(txt, /F1.*33\.3%/);
 assert.ok(!/Recommended|Peak/i.test(txt));
 assert.equal(h.f.variant.value, 'evening');
});

test('Section 2 hydration selects Mode B for TOU profiles and profile switch or reset clears band state', async () => {
 const pB = { ...profile(), id: 'p_b', consumption: { kind: 'monthly', acknowledged: true, monthly_f1: Array(12).fill(120), monthly_f2: Array(12).fill(80), monthly_f3: Array(12).fill(50) } };
 const pA = { ...profile(), id: 'p_a', consumption: { kind: 'monthly', acknowledged: true, monthly_totals: Array(12).fill(300) } };
 const h = mountSection2Helper(pB); h.store.set('p_a', pA);

 h.f.profile.value = 'p_b'; await h.f.profile.onchange();
 assert.equal(h.f.monthly_mode.value, 'B'); assert.equal(h.f.monthly_f1.value, Array(12).fill(120).join(','));

 h.f.profile.value = 'p_a'; await h.f.profile.onchange();
 assert.equal(h.f.monthly_mode.value, 'A'); assert.equal(h.f.totals.value, Array(12).fill(300).join(','));
 assert.ok(!h.f.monthly_f1.value && !h.f.monthly_f2.value && !h.f.monthly_f3.value);

 h.f.profile.value = 'p_b'; await h.f.profile.onchange();
 h.f.profile.value = ''; await h.f.profile.onchange();
 assert.equal(h.f.monthly_mode.value, 'A');
 assert.ok(!h.f.monthly_f1.value && !h.all().find(n => n.className === 'f1-share-info').textContent);
});

test('validateDraft rules enforce complete TOU bands, reject mixed modes, and validate zero bands', () => {
 const mk = (k, bands, totals) => {
  const p = profile(), l = load(); p.consumption.kind = k;
  if (bands) {Object.assign(p.consumption, bands);Object.assign(l.policy,bands);}
  if (totals !== undefined) { p.consumption.monthly_totals = totals; l.monthly_totals = totals; }
  else { delete p.consumption.monthly_totals; delete l.monthly_totals; }
  if (k === 'annual') l.policy.annual_kwh = 1000;
  return { p, l };
 };
 const b12 = v => Array(12).fill(v);
 const fullBands = { monthly_f1: b12(10), monthly_f2: b12(10), monthly_f3: b12(10) };

 assert.throws(() => validateDraft(mk('monthly', { monthly_f1: b12(10) }).p, mk('monthly', { monthly_f1: b12(10) }).l, { gross: true }), /BANDS|MONTHLY_TOTALS/);
 assert.throws(() => validateDraft(mk('annual', fullBands).p, mk('annual', fullBands).l, { gross: true }), /BANDS|MONTHLY_TOTALS/);
 assert.throws(() => validateDraft(mk('interval', fullBands).p, mk('interval', fullBands).l, { gross: true }), /BANDS|MONTHLY_TOTALS/);
 assert.throws(() => validateDraft(mk('monthly', fullBands, b12(30)).p, mk('monthly', fullBands, b12(30)).l, { gross: true }), /BANDS|MONTHLY_TOTALS/);
 const badNum = mk('monthly', { monthly_f1: ['-1', ...b12(10).slice(1)], monthly_f2: b12(10), monthly_f3: b12(10) });
 assert.throws(() => validateDraft(badNum.p, badNum.l, { gross: true }), /BANDS|MONTHLY_TOTALS/);

 const cZero = mk('monthly', { monthly_f1: b12(0), monthly_f2: b12(0), monthly_f3: b12(0) });
 cZero.l.policy.monthly_f1 = b12(0); cZero.l.policy.monthly_f2 = b12(0); cZero.l.policy.monthly_f3 = b12(0);
 assert.equal(validateDraft(cZero.p, cZero.l, { gross: true }), true);
});

test('Section 2 blank consumption kind fails draft validation with KIND_REQUIRED before other fields', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.kind.value = ''; await h.click('Save and review draft');
 assert.match(h.root.children.find(c => c.attrs?.role === 'status').textContent, /KIND/);
});

test('thermal end use conversion mapping sets conversion_kwh_per_unit and omits typo', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'annual'; h.f.annual.value = '2000';
 h.f.space_heat_enabled.checked = true; h.f.space_heat_carrier.value = 'gas'; h.f.space_heat_action.value = 'unchanged';
 h.f.space_heat_input.value = Array(12).fill(100).join(','); h.f.space_heat_conversion.value = '10.5';
 h.f.space_heat_fixed.value = '50'; h.f.space_heat_variable.value = '200';
 await h.click('Save and review draft');
 const u = h.getSaved().profile.thermal.end_uses.find(x => x.id === 'space_heat');
 assert.ok(u); assert.equal(u.conversion_kwh_per_unit, 10.5); assert.equal(u.conversionkwh_per_unit, undefined);
});

test('Validate CSV preview converts E-Distribuzione matrix and uploads normalized file with metadata', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'interval';
 const intervals = Array.from({ length: 96 }, (_, i) => {
  const s = String(Math.floor(i * 15 / 60)).padStart(2, '0') + ':' + String((i * 15) % 60).padStart(2, '0');
  const e = String(Math.floor((i + 1) * 15 / 60) % 24).padStart(2, '0') + ':' + String(((i + 1) * 15) % 60).padStart(2, '0');
  return s + '-' + e;
 });
 const csvText = 'Giorno;' + intervals.join(';') + '\n"15/01/2025";' + Array(96).fill('"0,250"').join(';') + '\n';
 const wide = { name: 'wide.csv', size: csvText.length, text: async () => csvText };
 h.f.csv.files = [wide];
 await h.f.csv.listeners.change();
 h.f.totals.value = Array(12).fill(250).join(',');

 let upFile = null, upMeta = null;
 h.setup.workflow.upload = async (file, meta) => { upFile = file; upMeta = meta; return { artifact_id: 'art_csv_1' }; };

 await h.click('Validate CSV preview');

 assert.notEqual(upFile, wide);
 const txt = await upFile.text();
 assert.ok(txt.includes('timestamp,energy_kwh') && !txt.includes('Giorno'));
 assert.equal(txt.trim().split('\n').length - 1, 96);
 assert.deepEqual(upMeta.columns, { timestamp: 'timestamp', value: 'energy_kwh' });
 assert.equal(upMeta.duration_seconds, 900); assert.equal(upMeta.unit, 'kWh');
 assert.equal(upMeta.delimiter, ','); assert.equal(upMeta.decimal_separator, '.');
 assert.equal(upMeta.timestamp_convention, 'start');
 const p = h.getSaved().profile;
 assert.equal(p.consumption.coverage.start, '2025-01-15');
 assert.equal(p.consumption.coverage.end, '2025-01-16');
 assert.equal(h.setup.workflow.profile.consumption.artifact_id, 'art_csv_1');
});

const thermalBodies = h => h.all().filter(n => n.className === 'thermal-card-body');
const thermalStatuses = h => h.all().filter(n => n.className === 'thermal-card-status');
const thermalHint = (h, i) => thermalBodies(h)[i].children.find(c => c.className === 'field-help');
const methodRadio = (h, name, value) => { const r = h.all().find(n => n.name === name && n.value === value); assert.ok(r, name + '=' + value); return r; };

test('Section 4 end-use card body and inputs are exposed only while enabled', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 assert.equal(thermalStatuses(h)[0].textContent, 'Not included');
 assert.equal(thermalBodies(h)[0].hidden, true);
 assert.equal(h.f.space_heat_input.disabled, true);
 assert.equal(h.f.space_heat_heat.disabled, true);
 assert.equal(h.f.space_heat_factor.disabled, true);
 assert.equal(h.f.space_heat_fixed.disabled, true);

 h.f.space_heat_enabled.checked = true; h.f.space_heat_enabled.onchange();
 assert.equal(thermalStatuses(h)[0].textContent, 'Included');
 assert.equal(thermalBodies(h)[0].hidden, false);
 assert.equal(h.f.space_heat_input.disabled, false, 'measured carrier input enabled for gas');
 assert.equal(h.f.space_heat_fixed.disabled, false, 'explicit cost fields enabled');
 assert.equal(h.f.space_heat_heat.disabled, true, 'useful heat stays disabled while measured');

 h.f.space_heat_enabled.checked = false; h.f.space_heat_enabled.onchange();
 assert.equal(thermalStatuses(h)[0].textContent, 'Not included');
 assert.equal(thermalBodies(h)[0].hidden, true);
 assert.equal(h.f.space_heat_input.disabled, true);
 assert.equal(h.f.space_heat_fixed.disabled, true);
});

test('Section 4 carrier switches couple conversion, inclusion and efficiency/COP semantics', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.space_heat_enabled.checked = true; h.f.space_heat_enabled.onchange();
 const includedWrap = h.f.space_heat_included.parentNode;
 const factorLabel = () => h.f.space_heat_factor.parentNode.children[0].textContent;

 assert.equal(includedWrap.hidden, true, 'inclusion only applies to electric carriers');
 h.f.space_heat_carrier.value = 'electricity'; h.f.space_heat_carrier.onchange();
 assert.equal(h.f.space_heat_conversion.value, '1');
 assert.equal(h.f.space_heat_conversion.disabled, true, 'conversion fixed for electricity');
 assert.equal(includedWrap.hidden, false);
 assert.equal(h.f.space_heat_included.disabled, false);
 assert.equal(factorLabel(), 'Seasonal COP (Coefficient of Performance, > 0)');
 assert.equal(h.f.space_heat_factor.disabled, false);
 assert.equal(h.f.space_heat_heat.disabled, true, 'efficiency only applies to measured input');

 h.f.space_heat_included.checked = true;
 h.f.space_heat_carrier.value = 'gas'; h.f.space_heat_carrier.onchange();
 assert.equal(h.f.space_heat_included.checked, false, 'non-electric carrier clears inclusion');
 assert.equal(includedWrap.hidden, true);
 assert.equal(h.f.space_heat_included.disabled, true);
 assert.equal(h.f.space_heat_conversion.disabled, false, 'gas conversion is editable');
 assert.equal(factorLabel(), 'System Efficiency (0–1)');

 h.f.space_heat_conversion.value = '10.7';
 h.f.space_heat_carrier.value = 'district'; h.f.space_heat_carrier.onchange();
 assert.equal(h.f.space_heat_conversion.value, '1');
 assert.equal(h.f.space_heat_conversion.disabled, true);
 assert.equal(includedWrap.hidden, true);

 h.f.space_heat_carrier.value = 'none'; h.f.space_heat_carrier.onchange();
 const methodGroups = h.all().filter(n => n.className === 'thermal-method-group');
 assert.equal(methodGroups[0].hidden, true, 'energy method group hidden for carrier none');
 assert.equal(h.f.space_heat_factor.disabled, true);
 assert.equal(h.f.space_heat_input.disabled, true);
});

test('Section 4 energy input method switch clears the opposing payload values', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.space_heat_enabled.checked = true; h.f.space_heat_enabled.onchange();
 h.f.space_heat_carrier.value = 'gas'; h.f.space_heat_carrier.onchange();
 const heatWrap = h.f.space_heat_heat.parentNode, inputWrap = h.f.space_heat_input.parentNode;
 const heat = Array(12).fill(300).join(',');
 h.f.space_heat_input.value = '10,11,12,13,14,15,16,17,18,19,20,21';
 h.f.space_heat_conversion.value = '10.7';
 h.f.space_heat_heat.value = heat;

 const useful = methodRadio(h, 'space_heat_energy_method', 'useful'); useful.checked = true; useful.onchange();
 assert.equal(h.f.space_heat_heat.value, heat, 'useful heat preserved');
 assert.equal(h.f.space_heat_input.value, '', 'measured input cleared');
 assert.equal(h.f.space_heat_conversion.value, '', 'conversion cleared');
 assert.equal(heatWrap.hidden, false);
 assert.equal(inputWrap.hidden, true);
 assert.equal(h.f.space_heat_heat.disabled, false);
 assert.equal(h.f.space_heat_input.disabled, true);
 assert.equal(h.f.space_heat_conversion.disabled, true);

 const measured = methodRadio(h, 'space_heat_energy_method', 'measured'); measured.checked = true; measured.onchange();
 assert.equal(h.f.space_heat_heat.value, '', 'useful heat cleared when returning to measured');
 assert.equal(heatWrap.hidden, true);
 assert.equal(inputWrap.hidden, false);
 assert.equal(h.f.space_heat_input.disabled, false);
});

test('Section 4 cost method switch clears the opposing cost values', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 h.f.space_heat_enabled.checked = true; h.f.space_heat_enabled.onchange();
 h.f.space_heat_carrier.value = 'gas'; h.f.space_heat_carrier.onchange();
 const fixedWrap = h.f.space_heat_fixed.parentNode, variableWrap = h.f.space_heat_variable.parentNode, billWrap = h.f.space_heat_bill.parentNode;
 h.f.space_heat_fixed.value = '50'; h.f.space_heat_variable.value = '200';

 const bill = methodRadio(h, 'space_heat_cost_method', 'bill'); bill.checked = true; bill.onchange();
 assert.equal(h.f.space_heat_fixed.value, '', 'fixed cost cleared');
 assert.equal(h.f.space_heat_variable.value, '', 'variable cost cleared');
 assert.equal(billWrap.hidden, false);
 assert.equal(fixedWrap.hidden, true);
 assert.equal(variableWrap.hidden, true);
 assert.equal(h.f.space_heat_bill.disabled, false);
 assert.equal(h.f.space_heat_fixed.disabled, true);

 h.f.space_heat_bill.value = '300';
 const explicit = methodRadio(h, 'space_heat_cost_method', 'explicit'); explicit.checked = true; explicit.onchange();
 assert.equal(h.f.space_heat_bill.value, '', 'annual bill cleared');
 assert.equal(billWrap.hidden, true);
 assert.equal(fixedWrap.hidden, false);
 assert.equal(h.f.space_heat_bill.disabled, true);
 assert.equal(h.f.space_heat_fixed.disabled, false);
});

test('Section 4 EV action gates incremental demand and presets switch the action to add', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 const incrementalWrap = h.f.ev_incremental.parentNode;
 assert.equal(thermalStatuses(h)[3].textContent, 'Unchanged');
 assert.equal(incrementalWrap.hidden, true);
 assert.equal(h.f.ev_incremental.disabled, true);
 assert.equal(h.f.ev_included.disabled, false);
 assert.match(thermalHint(h, 3).textContent, /already in gross consumption/);

 h.f.ev_included.checked = true;
 h.f.ev_action.value = 'add'; h.f.ev_action.onchange();
 assert.equal(thermalStatuses(h)[3].textContent, 'New load');
 assert.equal(incrementalWrap.hidden, false);
 assert.equal(h.f.ev_incremental.disabled, false);
 assert.equal(h.f.ev_included.checked, false, 'added demand cannot also be included');
 assert.equal(h.f.ev_included.disabled, true);
 assert.match(thermalHint(h, 3).textContent, /not already in gross consumption/);

 h.f.ev_action.value = 'replace'; h.f.ev_action.onchange();
 assert.equal(thermalStatuses(h)[3].textContent, 'Replacement');
 assert.equal(incrementalWrap.hidden, true);
 assert.equal(h.f.ev_incremental.disabled, true);
 assert.match(thermalHint(h, 3).textContent, /separated hourly series/);

 await h.click('10,000 km/yr');
 assert.equal(h.f.ev_action.value, 'add');
 assert.equal(h.f.ev_incremental.value, '1800');
 assert.equal(thermalStatuses(h)[3].textContent, 'New load');
 assert.equal(h.f.ev_included.checked, false);
});

test('Section 4 cooling action gates incremental demand and presets fill annual kWh', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 assert.equal(thermalStatuses(h)[4].textContent, 'Unchanged');
 assert.equal(h.f.cooling_incremental.disabled, true);
 assert.equal(h.f.cooling_included.disabled, false);
 assert.match(thermalHint(h, 4).textContent, /already in gross consumption/);

 h.f.cooling_action.value = 'add'; h.f.cooling_action.onchange();
 assert.equal(thermalStatuses(h)[4].textContent, 'New load');
 assert.equal(h.f.cooling_incremental.disabled, false);
 assert.equal(h.f.cooling_included.disabled, true);
 assert.equal(h.f.cooling_included.checked, false);
 assert.equal(h.f.baseline_cooling_kwh.disabled, false, 'existing cooling baseline stays editable');

 await h.click('Medium: 600 kWh/yr');
 assert.equal(h.f.cooling_action.value, 'add');
 assert.equal(h.f.cooling_incremental.value, '600');
 assert.equal(thermalStatuses(h)[4].textContent, 'New load');

 h.f.cooling_action.value = 'replace'; h.f.cooling_action.onchange();
 assert.equal(thermalStatuses(h)[4].textContent, 'Replacement');
 assert.equal(h.f.cooling_incremental.disabled, true);
 assert.match(thermalHint(h, 4).textContent, /separated hourly series/);
});

test('Section 4 saved payload maps measured, useful, bill and added loads explicitly', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'monthly'; h.f.totals.value = Array(12).fill(100).join(',');

 h.f.space_heat_enabled.checked = true; h.f.space_heat_enabled.onchange();
 h.f.space_heat_carrier.value = 'gas'; h.f.space_heat_carrier.onchange();
 h.f.space_heat_input.value = Array(12).fill(10).join(',');
 h.f.space_heat_conversion.value = '10.7';
 h.f.space_heat_factor.value = '0.85';
 h.f.space_heat_fixed.value = '50'; h.f.space_heat_variable.value = '200';

 h.f.dhw_enabled.checked = true; h.f.dhw_enabled.onchange();
 h.f.dhw_carrier.value = 'electricity'; h.f.dhw_carrier.onchange();
 h.f.dhw_included.checked = true;
 h.f.dhw_input.value = Array(12).fill(20).join(',');
 h.f.dhw_factor.value = '3.5';

 h.f.cooking_enabled.checked = true; h.f.cooking_enabled.onchange();
 h.f.cooking_carrier.value = 'gas'; h.f.cooking_carrier.onchange();
 const useful = methodRadio(h, 'cooking_energy_method', 'useful'); useful.checked = true; useful.onchange();
 h.f.cooking_heat.value = Array(12).fill(30).join(',');
 h.f.cooking_factor.value = '0.9';
 const bill = methodRadio(h, 'cooking_cost_method', 'bill'); bill.checked = true; bill.onchange();
 h.f.cooking_bill.value = '120';

 h.f.ev_action.value = 'add'; h.f.ev_action.onchange(); h.f.ev_incremental.value = '1800';
 h.f.cooling_action.value = 'add'; h.f.cooling_action.onchange(); h.f.cooling_incremental.value = '600';
 h.f.baseline_cooling_kwh.value = '150';

 await h.click('Save and review draft');
 const p = h.getSaved().profile;
 assert.equal(p.thermal.end_uses.length, 3);
 assert.equal(p.thermal.electric_loads.length, 2);

 const sh = p.thermal.end_uses.find(u => u.id === 'space_heat');
 assert.equal(sh.carrier, 'gas'); assert.equal(sh.action, 'unchanged');
 assert.deepEqual(sh.monthly_input, Array(12).fill(10));
 assert.equal(sh.measured_input, 120);
 assert.equal(sh.unit, 'smc');
 assert.equal(sh.monthly_useful_kwh, null);
 assert.equal(sh.conversion_kwh_per_unit, 10.7);
 assert.equal(sh.efficiency, 0.85); assert.equal(sh.cop, null);
 assert.equal(sh.annual_fixed_cost, 50); assert.equal(sh.annual_variable_cost, 200);
 assert.equal('annual_bill_eur' in sh, false);
 assert.equal(sh.included_in_electric_load, false);
 assert.equal(sh.artifact_id, null);

 const dhw = p.thermal.end_uses.find(u => u.id === 'dhw');
 assert.equal(dhw.carrier, 'electricity'); assert.equal(dhw.unit, 'kwh');
 assert.equal(dhw.cop, 3.5); assert.equal(dhw.efficiency, null);
 assert.equal(dhw.included_in_electric_load, true);
 assert.equal(dhw.conversion_kwh_per_unit, 1);
 assert.deepEqual(dhw.monthly_input, Array(12).fill(20));

 const ck = p.thermal.end_uses.find(u => u.id === 'cooking');
 assert.deepEqual(ck.monthly_useful_kwh, Array(12).fill(30));
 assert.equal(ck.measured_input, null);
 assert.equal('monthly_input' in ck, false, 'measured input array absent for useful heat');
 assert.equal(ck.unit, null);
 assert.equal('conversion_kwh_per_unit' in ck, false);
 assert.equal(ck.efficiency, 0.9); assert.equal(ck.cop, null);
 assert.equal(ck.annual_bill_eur, 120);
 assert.equal(ck.annual_fixed_cost, 0); assert.equal(ck.annual_variable_cost, 0);

 const ev = p.thermal.electric_loads.find(u => u.id === 'ev');
 assert.equal(ev.action, 'add'); assert.equal(ev.included_in_electric_load, false);
 assert.equal(ev.incremental_annual_kwh, 1800); assert.equal(ev.artifact_id, null);
 const cool = p.thermal.electric_loads.find(u => u.id === 'cooling');
 assert.equal(cool.action, 'add'); assert.equal(cool.included_in_electric_load, false);
 assert.equal(cool.incremental_annual_kwh, 600);
 assert.equal(p.default_settings.baseline_cooling_kwh, 150);
});

test('Section 4 hydration restores measured, useful, bill and added loads into the form', async () => {
 const h = mountSection2Helper(); await h.click('Create profile');
 const hyd = { ...profile(), id: 'p_sec4', default_settings: { baseline_cooling_kwh: 220 }, thermal: {
  end_uses: [
   { id: 'space_heat', carrier: 'gas', action: 'unchanged', monthly_input: Array(12).fill(10), monthly_useful_kwh: null, conversion_kwh_per_unit: 10.7, efficiency: 0.86, cop: null, annual_fixed_cost: 40, annual_variable_cost: 120, included_in_electric_load: false },
   { id: 'dhw', carrier: 'electricity', action: 'add', monthly_input: null, monthly_useful_kwh: Array(12).fill(25), conversion_kwh_per_unit: null, efficiency: null, cop: 3.2, annual_fixed_cost: 0, annual_variable_cost: 0, included_in_electric_load: false },
   { id: 'cooking', carrier: 'gas', action: 'replace', monthly_input: Array(12).fill(5), monthly_useful_kwh: null, conversion_kwh_per_unit: 10.7, efficiency: 0.9, cop: null, annual_bill_eur: 99, included_in_electric_load: false }
  ],
  electric_loads: [
   { id: 'ev', action: 'add', included_in_electric_load: false, incremental_annual_kwh: 1800 },
   { id: 'cooling', action: 'replace', included_in_electric_load: true, incremental_annual_kwh: null }
  ]
 } };
 h.store.set('p_sec4', hyd);
 h.f.profile.value = 'p_sec4'; await h.f.profile.onchange();

 assert.equal(h.f.space_heat_enabled.checked, true);
 assert.equal(h.f.space_heat_carrier.value, 'gas');
 assert.equal(h.f.space_heat_input.value, Array(12).fill(10).join(','));
 assert.equal(h.f.space_heat_heat.value, '');
 assert.equal(h.f.space_heat_conversion.value, '10.7');
 assert.equal(h.f.space_heat_factor.value, '0.86');
 assert.equal(h.f.space_heat_fixed.value, '40');
 assert.equal(h.f.space_heat_variable.value, '120');
 assert.equal(h.f.space_heat_bill.value, '');
 assert.equal(h.f.space_heat_action.value, 'unchanged');
 assert.equal(h.f.space_heat_included.checked, false);
 assert.equal(methodRadio(h, 'space_heat_energy_method', 'measured').checked, true);
 assert.equal(methodRadio(h, 'space_heat_cost_method', 'explicit').checked, true);

 assert.equal(h.f.dhw_carrier.value, 'electricity');
 assert.equal(h.f.dhw_heat.value, Array(12).fill(25).join(','));
 assert.equal(h.f.dhw_input.value, '', 'measured input cleared for useful-heat profile');
 assert.equal(h.f.dhw_conversion.value, '1', 'electricity keeps its forced conversion even for useful heat');
 assert.equal(h.f.dhw_conversion.disabled, true);
 assert.equal(h.f.dhw_factor.value, '3.2');
 assert.equal(methodRadio(h, 'dhw_energy_method', 'useful').checked, true);
 assert.equal(h.f.dhw_included.checked, false);

 assert.equal(h.f.cooking_input.value, Array(12).fill(5).join(','));
 assert.equal(h.f.cooking_bill.value, '99');
 assert.equal(h.f.cooking_fixed.value, '');
 assert.equal(h.f.cooking_variable.value, '');
 assert.equal(methodRadio(h, 'cooking_cost_method', 'bill').checked, true);

 assert.equal(h.f.ev_action.value, 'add');
 assert.equal(h.f.ev_incremental.value, '1800');
 assert.equal(h.f.ev_included.checked, false);
 assert.equal(h.f.ev_included.disabled, true);
 assert.equal(h.f.cooling_action.value, 'replace');
 assert.equal(h.f.cooling_included.checked, true);
 assert.equal(h.f.cooling_incremental.disabled, true);
 assert.equal(h.f.baseline_cooling_kwh.value, '220');

 h.f.dhw_carrier.value = 'gas'; h.f.dhw_carrier.onchange();
 assert.equal(h.f.dhw_included.checked, false, 'carrier change clears hydrated inclusion');
});

test('HouseholdClient pair and join API methods handle credentials and tokens', async () => {
 const calls = [];
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   calls.push([path, opts]);
   if (path === '/api/session') return response({ csrf_token: 'csrf-initial' });
   if (path === '/api/session/pair') return response({ code: 'pair-code-123', expires_in: 300 });
   if (path === '/api/session/join') return response({ tenant_id: 't-joined', csrf_token: 'csrf-joined' });
   return response({});
  }
 });
 client.select('p1', 'r1');
 const pairData = await client.pair();
 assert.equal(pairData.code, 'pair-code-123');
 assert.equal(pairData.expires_in, 300);
 assert.equal(calls.at(-1)[0], '/api/session/pair');
 assert.equal(calls.at(-1)[1].headers['X-CSRF-Token'], 'csrf-initial');

 const joinData = await client.join('pair-code-123');
 assert.equal(joinData.tenant_id, 't-joined');
 assert.equal(client.csrf, 'csrf-joined');
 assert.equal(client.profileId, null, 'join clears profile scope');
 assert.equal(calls.at(-1)[0], '/api/session/join');
 assert.deepEqual(JSON.parse(calls.at(-1)[1].body), { code: 'pair-code-123' });
});

test('Cross-browser LAN pairing UI provides privacy notice, copyable code, and join with reload', async () => {
 const calls = [];
 let reloaded = false;
 let copiedText = null;

 const mockDoc = {
  createElement: tag => new Element(tag, mockDoc),
  defaultView: {
   location: { reload: () => { reloaded = true; } },
   navigator: {
    clipboard: {
     writeText: async text => { copiedText = text; }
    }
   }
  }
 };

 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   calls.push([path, opts]);
   if (path === '/api/session') return response({ csrf_token: 'csrf-1' });
   if (path === '/api/profiles') return response({ profiles: [{ id: 'p1', name: 'Home' }] });
   if (path === '/api/session/pair') return response({ code: 'safe-pair-code-xyz', expires_in: 300 });
   if (path === '/api/session/join') {
    const body = JSON.parse(opts.body);
    if (body.code === 'safe-pair-code-xyz') {
     return response({ tenant_id: 'tenant-linked', csrf_token: 'csrf-linked' });
    }
    return response({ error: 'Invalid or expired code' }, 401);
   }
   return response({});
  }
 });

 const root = new Element('section', mockDoc);
 const setup = mount({ root, client });

 const all = () => {
  const nodes = [];
  const visit = n => { nodes.push(n); for (const c of n.children) visit(c); };
  visit(root);
  return nodes;
 };

 const pairingGroup = all().find(n => (n.attrs?.id || n.id) === 'householdPairingGroup');
 assert.ok(pairingGroup, 'householdPairingGroup exists');

 const notice = all().find(n => (n.attrs?.id || n.id) === 'pairingPrivacyWarning');
 assert.ok(notice, 'privacy warning exists');
 const noticeText = [notice.textContent, ...notice.children.map(c => c.textContent)].join(' ');
 assert.ok(noticeText.includes('ALL profiles'), 'warning mentions access to ALL profiles');
 assert.ok(noticeText.includes('replaces this browser'), 'warning mentions session replacement');
 assert.ok(noticeText.includes('without deleting unlinked data'), 'warning clarifies unlinked data is not deleted');

 const pairBtn = all().find(n => (n.attrs?.id || n.id) === 'generatePairCodeBtn');
 const codeWrap = all().find(n => (n.attrs?.id || n.id) === 'pairingCodeWrap');
 const codeDisplay = all().find(n => (n.attrs?.id || n.id) === 'pairingCodeDisplay');
 const expiresInfo = all().find(n => (n.attrs?.id || n.id) === 'pairingCodeExpires');
 const copyBtn = all().find(n => (n.attrs?.id || n.id) === 'copyPairCodeBtn');
 const joinInput = all().find(n => (n.attrs?.id || n.id) === 'joinPairCodeInput');
 const joinBtn = all().find(n => (n.attrs?.id || n.id) === 'joinHouseholdBtn');
 const pairingStatus = all().find(n => (n.attrs?.id || n.id) === 'pairingStatus');

 assert.ok(pairBtn && codeWrap && codeDisplay && copyBtn && joinInput && joinBtn && pairingStatus);
 assert.equal(codeWrap.hidden, true, 'code display is initially hidden');

 // 1. Generate code
 assert.equal(codeWrap.attrs.hidden, '', 'code wrapper has hidden attribute initially');
 await pairBtn.onclick();
 assert.equal(codeWrap.hidden, false, 'code display reveals after generation');
 assert.equal(codeWrap.attrs.hidden, undefined, 'hidden attribute removed on generation');
 assert.equal(codeDisplay.textContent, 'safe-pair-code-xyz');
 assert.ok(expiresInfo.textContent.includes('5 minutes'), 'expires in 5 minutes displayed');
 assert.ok(pairingStatus.textContent.includes('Pair code generated'));

 // 2. Copy code
 await copyBtn.onclick();
 assert.equal(copiedText, 'safe-pair-code-xyz');
 assert.equal(copyBtn.textContent, 'Copied!');
 assert.ok(pairingStatus.textContent.includes('copied to clipboard'));

 // 3. Join with empty code
 joinInput.value = '   ';
 await joinBtn.onclick();
 assert.equal(reloaded, false, 'empty code does not trigger reload');
 assert.ok(pairingStatus.textContent.includes('Enter a pairing code to join'));

 // 4. Join with invalid code
 joinInput.value = 'invalid-code';
 await joinBtn.onclick();
 assert.equal(reloaded, false, 'invalid code does not reload');
 assert.ok(pairingStatus.textContent.includes('Failed to join'));

 // 5. Join with valid code
 joinInput.value = 'safe-pair-code-xyz';
 await joinBtn.onclick();
 assert.equal(reloaded, true, 'successful join triggers window.location.reload()');
 assert.ok(pairingStatus.textContent.includes('Joined household successfully'));
});

test('Pairing UI copy never falsely claims success on missing or rejected clipboard, enables selection and hidden CSS rule exists', async () => {
 const fs = require('node:fs');
 const styleCss = fs.readFileSync(require('node:path').join(__dirname, '../feasibility/web/style.css'), 'utf8');
 assert.match(styleCss, /\.pairing-code-wrap\[hidden\][^{]*\{[^}]*display:\s*none\s*!important/);

 let selectedNode = null;
 const createDocWithClipboard = clip => ({
  createElement: tag => new Element(tag, createDocWithClipboard(clip)),
  createRange: () => ({
   selectNodeContents: node => { selectedNode = node; }
  }),
  defaultView: {
   getSelection: () => {
    let ranges = [];
    return {
     removeAllRanges: () => { ranges = []; },
     addRange: r => { ranges.push(r); }
    };
   },
   navigator: clip ? { clipboard: clip } : {}
  }
 });

 const client = {
  pair: async () => ({ code: 'code-to-copy-123', expires_in: 300 }),
  join: async () => ({})
 };

 // Case A: Clipboard completely unavailable
 {
  selectedNode = null;
  const docNoClip = createDocWithClipboard(null);
  const root = new Element('section', docNoClip);
  mount({ root, client });

  const all = () => {
   const nodes = [];
   const visit = n => { nodes.push(n); for (const c of n.children) visit(c); };
   visit(root);
   return nodes;
  };

  const pairBtn = all().find(n => (n.attrs?.id || n.id) === 'generatePairCodeBtn');
  const copyBtn = all().find(n => (n.attrs?.id || n.id) === 'copyPairCodeBtn');
  const codeDisplay = all().find(n => (n.attrs?.id || n.id) === 'pairingCodeDisplay');
  const pairingStatus = all().find(n => (n.attrs?.id || n.id) === 'pairingStatus');

  await pairBtn.onclick();
  copyBtn.onclick();

  assert.notEqual(copyBtn.textContent, 'Copied!', 'never claim success when clipboard unavailable');
  assert.equal(selectedNode, codeDisplay, 'code contents selected for manual copy');
  assert.match(pairingStatus.textContent, /unavailable.*manual copy/i);

  // Manual click on codeDisplay selects too
  selectedNode = null;
  codeDisplay.onclick();
  assert.equal(selectedNode, codeDisplay, 'direct click on code allows manual selection');
 }

 // Case B: Clipboard writeText rejects
 {
  selectedNode = null;
  const docRejectClip = createDocWithClipboard({
   writeText: async () => { throw new Error('Permission denied'); }
  });
  const root = new Element('section', docRejectClip);
  mount({ root, client });

  const all = () => {
   const nodes = [];
   const visit = n => { nodes.push(n); for (const c of n.children) visit(c); };
   visit(root);
   return nodes;
  };

  const pairBtn = all().find(n => (n.attrs?.id || n.id) === 'generatePairCodeBtn');
  const copyBtn = all().find(n => (n.attrs?.id || n.id) === 'copyPairCodeBtn');
  const codeDisplay = all().find(n => (n.attrs?.id || n.id) === 'pairingCodeDisplay');
  const pairingStatus = all().find(n => (n.attrs?.id || n.id) === 'pairingStatus');

  await pairBtn.onclick();
  copyBtn.onclick();

  // Allow async rejection microtask to settle
  await new Promise(r => setImmediate(r));

  assert.notEqual(copyBtn.textContent, 'Copied!', 'never claim success when clipboard writeText rejects');
  assert.equal(selectedNode, codeDisplay, 'code contents selected for manual copy on rejection');
  assert.match(pairingStatus.textContent, /rejected.*manual copy/i);
 }
});

test('Selecting ready profile restores compiled years and generation settings without marking coverage verified or checking consent', async () => {
 const h = mountSection2Helper();
 const readyId = 'p_ready';
 const readyProfile = {
  ...profile(),
  id: readyId,
  consumption: { kind: 'annual', annual_kwh: 4000, acknowledged: true, complete: true, coverage: { complete: true } }
 };
 h.store.set(readyId, readyProfile);
 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path.endsWith('/validation')) {
   return response({
    ready: true,
    compiled: true,
    blockers: [],
    revision: 'r_ready',
    fiscal_rule_revision: 'f'.repeat(64),
    compiled_candidate: {
     years: [2018, 2019, 2020],
     solar: { loss_percent: 12.5, mounting: 'Integrated 2025', technology: 'cSi' },
     reference_year: 2023
    }
   });
  }
  return origFetch(path, opts);
 };

 h.f.profile.value = readyId;
 await h.f.profile.onchange();

 // 1. Restores compiled years and settings
 assert.deepEqual(h.f.years.getSelectedYears(), [2018, 2019, 2020]);
 assert.equal(h.f.loss_percent.value, '12.5');
 assert.equal(h.f.mounting.value, 'Integrated 2025');
 assert.equal(h.f.technology.value, 'cSi');
 assert.equal(h.f.reference_year.value, '2023');

 // 2. Does NOT represent compiled years as verified provider coverage or bypass consent
 assert.equal(Boolean(h.f.providerConsent.checked), false, 'consent is not automatically checked');
 const summaryBadge = h.all().find(n => n.className === 'year-summary-badge');
 assert.ok(summaryBadge.textContent.includes('Compiled 3 years'));
 const summaryTitle = h.all().find(n => n.parentNode?.className === 'year-menu-summary');
 assert.ok(summaryTitle.textContent.includes('compiled profile'));

 // 3. Step 3 re-generation fails because consent is required and coverage is unverified for new generation
 const statusNode = h.root.children.find(c => c.attrs?.role === 'status');
 await h.click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /PROVIDER_CONSENT_REQUIRED/);
 h.f.providerConsent.checked = true;
 await h.click('Generate solar data and compile (retry)');
 assert.match(statusNode.textContent, /COVERAGE_STALE/);

 // 4. Form input invalidation resets compiled state
 h.f.latitude.value = '46.1';
 h.f.latitude.dispatchEvent(new (doc.defaultView?.Event || Event)('input', { bubbles: true }));
 assert.deepEqual(h.f.years.getSelectedYears(), []);
 const currentBadge = h.all().find(n => n.className === 'year-summary-badge');
 assert.ok(currentBadge.textContent.includes('No years available'));
});

const getTreeText = n => (n ? [n.textContent || '', ...(n.children || []).map(getTreeText)].filter(Boolean).join(' ') : '');

test('GME ZIP upload section renders in Card 5 with multiple file input, import button, and empty status', async () => {
 const h = mountSection2Helper();
 const gmeSection = h.all().find(n => n.id === 'gmeCatalogSection');
 assert.ok(gmeSection, 'gmeCatalogSection exists');
 const fileInput = h.f.gme_archives;
 assert.ok(fileInput, 'file input exists');
 assert.equal(fileInput.type, 'file');
 assert.equal(fileInput.multiple, true);
 assert.ok(fileInput.accept.includes('.zip'));

 const importBtn = h.all().find(n => n.id === 'importGmeArchivesBtn');
 assert.ok(importBtn, 'import button exists');
 assert.equal(importBtn.textContent, 'Import GME price archives');

 const statusEl = h.all().find(n => n.id === 'gmeCatalogStatus');
 assert.ok(statusEl, 'status element exists');
 const statusText = getTreeText(statusEl);
 assert.ok(statusText.includes('Nessun catalogo prezzi GME') || statusText.includes('No GME price catalogue'));

 const flatOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'flat');
 assert.ok(flatOpt, 'flat valuation option exists');
 assert.equal(flatOpt.disabled, false, 'flat option is enabled by default');
});

test('GME archive upload blocks when no profile is selected', async () => {
 const h = mountSection2Helper();
 // No profile created or selected yet (client.profileId is null)
 h.f.gme_archives.files = [{ name: 'MGP_202501.zip' }];
 await h.click('Import GME price archives');

 const gmeErr = h.all().find(n => n.className?.includes('gme-upload-error') || (n.parentNode?.className?.includes('action-step-controls') && n.className?.includes('action-error')));
 const statusText = (gmeErr?.textContent || '') + ' ' + (h.root.children.find(c => c.attrs?.role === 'status')?.textContent || '');
 assert.ok(statusText.includes('Profile required') || statusText.includes('select a profile'), 'blocks with profile required');
});

test('GME archive upload rejects non-zip files before upload', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 h.f.gme_archives.files = [{ name: 'prices.csv' }];
 await h.click('Import GME price archives');

 const gmeErr = h.all().find(n => n.className?.includes('gme-upload-error') || n.className?.includes('action-error'));
 const statusText = (gmeErr?.textContent || '') + ' ' + (h.root.children.find(c => c.attrs?.role === 'status')?.textContent || '');
 assert.ok(statusText.includes('.zip') || statusText.includes('ZIP_REQUIRED'), 'rejects non-zip file');
});

test('Single GME ZIP upload calls client.uploadMarketCatalog with raw file, updates status, binds price revision, window, and save notice', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 const pid = h.client.profileId;

 const uploadCalls = [];
 const origFetch = h.client.fetcher;
 const catalogMeta = {
  revision: 'c'.repeat(64),
  series: ['CSUD', 'NORD'],
  coverage: ['2025-01-01', '2025-02-01'],
  window: ['2025-01-01', '2025-02-01'],
  windows: [['2025-01-01', '2025-02-01']],
  missing_months: ['2025-02', '2025-03', '2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09', '2025-10', '2025-11', '2025-12'],
  shape_available: false
 };

 h.client.fetcher = async (path, opts) => {
  if (path === `/api/profiles/${pid}/market-catalog` && opts.method === 'POST') {
   uploadCalls.push({ path, opts });
   return response({ catalog: catalogMeta });
  }
  return origFetch(path, opts);
 };

 const mockZipFile = { name: 'MGP_Prezzi_202501.zip', size: 2048 };
 h.f.gme_archives.files = [mockZipFile];
 await h.click('Import GME price archives');

 assert.equal(uploadCalls.length, 1);
 assert.equal(uploadCalls[0].opts.headers['X-Filename'], 'MGP_Prezzi_202501.zip');
 assert.equal(uploadCalls[0].opts.headers['Content-Type'], 'application/zip');
 assert.ok(uploadCalls[0].opts.headers['X-CSRF-Token'], 'CSRF token is present');

 // Binds revision and window
 assert.equal(h.f.price_revision.value, 'c'.repeat(64));
 assert.equal(h.f.window_start.value, '2025-01-01');
 assert.equal(h.f.window_end.value, '2025-02-01');

 // Save notice visible
 const saveNotice = h.all().find(n => n.id === 'gmeSaveNotice');
 assert.ok(saveNotice);
 assert.equal(saveNotice.hidden, false);
 assert.ok(saveNotice.textContent.includes('Save draft inputs') || saveNotice.textContent.includes('recompile'));

 // Status display checks
 const statusEl = h.all().find(n => n.id === 'gmeCatalogStatus');
 const statusText = getTreeText(statusEl);
 assert.ok(statusText.includes('2025-01-01 → 2025-02-01') || statusText.includes('2025-01-01'));
 assert.ok(statusText.includes('11 mancanti') || statusText.includes('11 missing'));
 assert.ok(statusText.includes('Non disponibile') || statusText.includes('Unavailable'));

 // Valuation mode: shape_scaled disabled because shape_available is false
 const shapeOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'shape_scaled');
 assert.equal(shapeOpt.disabled, true);
 // Flat option remains enabled
 const flatOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'flat');
 assert.equal(flatOpt.disabled, false);
});

test('HouseholdClient uploadMarketCatalog supports replace query and custom options', async () => {
 const calls = [];
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   if (path === '/api/session') return response({ csrf_token: 'tok' });
   calls.push({ path, opts });
   return response({ catalog: { revision: 'r1' } });
  }
 });
 client.select('p_test', 'rev_orig');
 await client.uploadMarketCatalog({ name: 'custom.zip' }, { replace: true, headers: { 'X-Custom': 'val' } });
 assert.equal(calls.length, 1);
 assert.equal(calls[0].path, '/api/profiles/p_test/market-catalog?replace=1');
 assert.equal(calls[0].opts.headers['X-Filename'], 'custom.zip');
 assert.equal(calls[0].opts.headers['X-Custom'], 'val');
});

test('Sequential multi-file GME ZIP upload uploads archives in order and updates to latest catalog', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 const pid = h.client.profileId;

 const uploadedFiles = [];
 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path === `/api/profiles/${pid}/market-catalog` && opts.method === 'POST') {
   const fn = opts.headers['X-Filename'];
   uploadedFiles.push(fn);
   const isSecond = fn.includes('202502');
   return response({
    catalog: {
     revision: isSecond ? '2'.repeat(64) : '1'.repeat(64),
     series: ['CSUD'],
     coverage: ['2025-01-01', isSecond ? '2025-03-01' : '2025-02-01'],
     window: ['2025-01-01', isSecond ? '2025-03-01' : '2025-02-01'],
     missing_months: isSecond ? ['2025-03'] : ['2025-02', '2025-03'],
     shape_available: false
    }
   });
  }
  return origFetch(path, opts);
 };

 h.f.gme_archives.files = [
  { name: 'MGP_Prezzi_202501.zip' },
  { name: 'MGP_Prezzi_202502.zip' }
 ];
 await h.click('Import GME price archives');

 assert.deepEqual(uploadedFiles, ['MGP_Prezzi_202501.zip', 'MGP_Prezzi_202502.zip']);
 assert.equal(h.f.price_revision.value, '2'.repeat(64));
 assert.equal(h.f.window_end.value, '2025-03-01');
});

test('Sequential upload halts immediately on error without processing remaining files and preserves earlier successful uploads', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 const pid = h.client.profileId;

 const uploadedFiles = [];
 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path === `/api/profiles/${pid}/market-catalog` && opts.method === 'POST') {
   const fn = opts.headers['X-Filename'];
   uploadedFiles.push(fn);
   if (fn.includes('corrupt')) {
    return response({ error: 'Correct the input fields.', diagnostic: { code: 'INVALID_PRICE_ARCHIVE', message: 'Corrupted GME zip payload header' } }, 422);
   }
   return response({
    catalog: {
     revision: '1'.repeat(64),
     series: ['CSUD'],
     coverage: ['2025-01-01', '2025-02-01'],
     window: ['2025-01-01', '2025-02-01'],
     missing_months: ['2025-02'],
     shape_available: false
    }
   });
  }
  return origFetch(path, opts);
 };

 h.f.gme_archives.files = [
  { name: 'good1.zip' },
  { name: 'corrupt.zip' },
  { name: 'unreached.zip' }
 ];
 await h.click('Import GME price archives');

 assert.deepEqual(uploadedFiles, ['good1.zip', 'corrupt.zip']);
 const gmeErr = h.all().find(n => n.className?.includes('gme-upload-error') || n.className?.includes('action-error'));
 const statusText = (gmeErr?.textContent || '') + ' ' + (h.root.children.find(c => c.attrs?.role === 'status')?.textContent || '');
 assert.ok(statusText.includes('Corrupted GME zip payload header') || statusText.includes('Invalid GME market archive'));

 // Verify that earlier successful upload (good1.zip) remains bound and save notice is displayed with count
 assert.equal(h.f.price_revision.value, '1'.repeat(64));
 assert.equal(h.f.window_start.value, '2025-01-01');
 assert.equal(h.f.window_end.value, '2025-02-01');
 const saveNotice = h.all().find(n => n.id === 'gmeSaveNotice');
 assert.ok(saveNotice);
 assert.equal(saveNotice.hidden, false);
 assert.ok(saveNotice.textContent.includes('Imported 1 of 3 archive(s)'));
});

test('Stale profile switching guards against late in-flight upload response', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile'); // Creates p_1
 const p1 = h.client.profileId;

 let releaseP1;
 const waitP1 = new Promise(r => releaseP1 = r);
 const origFetch = h.client.fetcher;

 h.client.fetcher = async (path, opts) => {
  if (path === `/api/profiles/${p1}/market-catalog` && opts.method === 'POST') {
   await waitP1;
   return response({
    catalog: {
     revision: '9'.repeat(64),
     series: ['CSUD'],
     coverage: ['2025-01-01', '2025-02-01'],
     window: ['2025-01-01', '2025-02-01'],
     missing_months: [],
     shape_available: true
    }
   });
  }
  return origFetch(path, opts);
 };

 h.f.gme_archives.files = [{ name: 'slow_p1.zip' }];
 const uploadPromise = h.click('Import GME price archives');

 // Switch to a new profile while upload for p1 is in flight
 await h.click('Create profile'); // Creates p_2
 const p2 = h.client.profileId;
 assert.notEqual(p1, p2);

 releaseP1();
 await uploadPromise;

 // Profile 2's price_revision must NOT be overwritten by p1's upload
 assert.notEqual(h.f.price_revision.value, '9'.repeat(64));
});

test('Profile selection and creation refreshes market catalog status and admin fallback', async () => {
 const root = new Element('section', doc);
 const client = new HouseholdClient({ fetch: async (path, opts) => {
  if (path === '/api/session') return response({ csrf_token: 't' });
  if (path === '/api/profiles') return response({ profiles: [{ id: 'p_admin' }] });
  if (path === '/api/profiles/p_admin') return response({ id: 'p_admin', revision: 'r1', profile: profile() });
  if (path === '/api/profiles/p_admin/validation') return response({ ready: false, revision: 'r1', fiscal_rule_revision: 'f'.repeat(64) });
  if (path === '/api/profiles/p_admin/market-catalog') {
   return response({
    catalog: {
     revision: 'd'.repeat(64),
     series: ['CSUD', 'NORD'],
     coverage: ['2025-01-01', '2026-01-01'],
     window: ['2025-01-01', '2026-01-01'],
     missing_months: [],
     shape_available: true
    }
   });
  }
  return response({});
 }});

 const setup = mount({ root, client });
 setup.fields.profile.value = 'p_admin';
 await setup.fields.profile.onchange();

 const statusEl = root.children.flatMap(function flatten(n){return [n,...(n.children||[]).flatMap(flatten)];}).find(n => n.id === 'gmeCatalogStatus');
 assert.ok(statusEl);
 const statusText = getTreeText(statusEl);
 assert.ok(statusText.includes('Admin Fallback') || statusText.includes('System Default') || statusText.includes('Privato'));
 assert.ok(statusText.includes('Completo') || statusText.includes('Complete'));
 assert.ok(statusText.includes('Disponibile') || statusText.includes('Available'));

 // Shape is available, so shape_scaled is enabled
 const shapeOpt = Array.from(setup.fields.valuation.options || setup.fields.valuation.children || []).find(o => o.value === 'shape_scaled');
 assert.equal(shapeOpt.disabled, false);
});

test('Per-series map support for shape_available updates reactivity on series change', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 const pid = h.client.profileId;

 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path === `/api/profiles/${pid}/market-catalog` && opts.method === 'POST') {
   return response({
    catalog: {
     revision: 'e'.repeat(64),
     series: ['CSUD', 'NORD'],
     coverage: ['2025-01-01', '2026-01-01'],
     window: ['2025-01-01', '2026-01-01'],
     missing_months: [],
     shape_available: { CSUD: true, NORD: false } // Per-series map!
    }
   });
  }
  return origFetch(path, opts);
 };

 h.f.gme_archives.files = [{ name: 'perseries.zip' }];
 await h.click('Import GME price archives');

 // Default series is CSUD -> shape should be available
 const shapeOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'shape_scaled');
 assert.equal(shapeOpt.disabled, false);
 const statusEl = h.all().find(n => n.id === 'gmeCatalogStatus');
 assert.ok(getTreeText(statusEl).includes('Disponibile') || getTreeText(statusEl).includes('Available'));

 // Switch series to NORD -> shape should become unavailable
 h.f.series.value = 'NORD';
 h.f.series.dispatchEvent(new (doc.defaultView?.Event || Event)('change', { bubbles: true }));

 assert.equal(shapeOpt.disabled, true);
 assert.ok(getTreeText(statusEl).includes('Non disponibile') || getTreeText(statusEl).includes('Unavailable'));
});

test('Selecting ready profile preserves ready status and pinned revision without triggering dirty input', async () => {
 const readyProfile = profile();
 readyProfile.id = 'p_ready';
 readyProfile.market = {
  zone: 'CSUD',
  series: 'CSUD',
  valuation_mode: 'absolute_proxy',
  price_revision: 'p'.repeat(64),
  window_start: '2024-01-01',
  window_end: '2025-01-01'
 };
 const h = mountSection2Helper(readyProfile);

 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path === '/api/profiles/p_ready/validation') {
   return response({
    ready: true,
    compiled: true,
    blockers: [],
    revision: 'r_ready',
    fiscal_rule_revision: 'f'.repeat(64)
   });
  }
  if (path === '/api/profiles/p_ready/market-catalog') {
   return response({
    catalog: {
     revision: 'c'.repeat(64),
     series: ['CSUD'],
     coverage: ['2025-01-01', '2026-01-01'],
     window: ['2025-01-01', '2026-01-01'],
     missing_months: [],
     shape_available: true
    }
   });
  }
  return origFetch(path, opts);
 };

 h.f.profile.value = 'p_ready';
 await h.f.profile.onchange();

 // Status remains ready and not invalidated
 assert.equal(h.setup.workflow.status, 'ready');
 assert.equal(h.client.ready, true);

 // Pinned revision from saved profile is preserved (not overwritten by catalog's 'c'.repeat(64))
 assert.equal(h.f.price_revision.value, 'p'.repeat(64));
 assert.equal(h.f.window_start.value, '2024-01-01');
 assert.equal(h.f.window_end.value, '2025-01-01');

 // Saved valuation selection is preserved
 assert.equal(h.f.valuation.value, 'absolute_proxy');
});

test('No-catalog disables absolute_proxy and shape_scaled options without mutating saved valuation', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 // Set valuation to shape_scaled manually as if saved/hydrated
 h.f.valuation.value = 'shape_scaled';

 // Calling updateCatalogUI with null (no catalog)
 h.setup.updateCatalogUI(null);

 const flatOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'flat');
 const proxyOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'absolute_proxy');
 const shapeOpt = Array.from(h.f.valuation.options || h.f.valuation.children || []).find(o => o.value === 'shape_scaled');

 assert.equal(flatOpt.disabled, false, 'flat valuation is enabled');
 assert.equal(proxyOpt.disabled, true, 'proxy valuation is disabled');
 assert.equal(shapeOpt.disabled, true, 'shape_scaled valuation is disabled');

 // Does NOT mutate saved selection
 assert.equal(h.f.valuation.value, 'shape_scaled');
});

test('HTTP 422 upload error displays backend diagnostic.message', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 const pid = h.client.profileId;

 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path === `/api/profiles/${pid}/market-catalog` && opts.method === 'POST') {
   return response({
    error: 'Correct the input fields.',
    diagnostic: { code: 'INVALID_ZIP', message: 'Specific parser error: missing MGP csv table' }
   }, 422);
  }
  return origFetch(path, opts);
 };

 h.f.gme_archives.files = [{ name: 'bad_archive.zip' }];
 await h.click('Import GME price archives');

 const gmeErr = h.all().find(n => n.className?.includes('gme-upload-error') || n.className?.includes('action-error'));
 const statusText = (gmeErr?.textContent || '') + ' ' + (h.root.children.find(c => c.attrs?.role === 'status')?.textContent || '');
 assert.ok(statusText.includes('Specific parser error: missing MGP csv table'), 'diagnostic message is surfaced');
});

test('Surface actual catalog.source private vs admin in status badges', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 // Private catalog
 h.setup.updateCatalogUI({
  revision: 'p'.repeat(64),
  series: ['CSUD'],
  coverage: ['2025-01-01', '2026-01-01'],
  missing_months: [],
  shape_available: true,
  source: 'private'
 });
 let badge = h.all().find(n => n.className?.includes('catalog-source-badge'));
 assert.ok(badge);
 assert.ok(badge.className.includes('badge-private'));
 assert.ok(badge.textContent.includes('Profilo Privato') || badge.textContent.includes('Private Catalog'));

 // Admin catalog
 h.setup.updateCatalogUI({
  revision: 'a'.repeat(64),
  series: ['CSUD'],
  coverage: ['2025-01-01', '2026-01-01'],
  missing_months: [],
  shape_available: true,
  source: 'admin'
 });
 badge = h.all().find(n => n.className?.includes('catalog-source-badge'));
 assert.ok(badge);
 assert.ok(badge.className.includes('badge-admin'));
 assert.ok(badge.textContent.includes('Admin Fallback') || badge.textContent.includes('System Default'));
});

test('parseCoordinatePair parses Google Maps decimal pairs and rejects invalid or single values', () => {
 // Standard comma separated with space
 const pair1 = parseCoordinatePair('40.987331944990146, 14.231766994573993');
 assert.ok(pair1);
 assert.equal(pair1.lat, '40.987331944990146');
 assert.equal(pair1.lon, '14.231766994573993');
 assert.equal(pair1.latNum, 40.987331944990146);
 assert.equal(pair1.lonNum, 14.231766994573993);

 // Without space after comma
 const pair2 = parseCoordinatePair('40.987331944990146,14.231766994573993');
 assert.ok(pair2);
 assert.equal(pair2.lat, '40.987331944990146');
 assert.equal(pair2.lon, '14.231766994573993');

 // Space separated
 const pair3 = parseCoordinatePair('40.987331944990146 14.231766994573993');
 assert.ok(pair3);
 assert.equal(pair3.lat, '40.987331944990146');
 assert.equal(pair3.lon, '14.231766994573993');

 // Semicolon separated
 const pair4 = parseCoordinatePair('40.987331944990146; 14.231766994573993');
 assert.ok(pair4);
 assert.equal(pair4.lat, '40.987331944990146');
 assert.equal(pair4.lon, '14.231766994573993');

 // Parentheses / brackets
 const pair5 = parseCoordinatePair('(40.987331944990146, 14.231766994573993)');
 assert.ok(pair5);
 assert.equal(pair5.lat, '40.987331944990146');
 const pair6 = parseCoordinatePair('[40.987331944990146, 14.231766994573993]');
 assert.ok(pair6);
 assert.equal(pair6.lat, '40.987331944990146');

 // Negative coordinates
 const pair7 = parseCoordinatePair('-33.8688, 151.2093');
 assert.ok(pair7);
 assert.equal(pair7.lat, '-33.8688');
 assert.equal(pair7.lon, '151.2093');
 const pair8 = parseCoordinatePair('40.7128, -74.0060');
 assert.ok(pair8);
 assert.equal(pair8.lat, '40.7128');
 assert.equal(pair8.lon, '-74.0060');
 const pair9 = parseCoordinatePair('-12.0464, -77.0428');
 assert.ok(pair9);
 assert.equal(pair9.lat, '-12.0464');
 assert.equal(pair9.lon, '-77.0428');

 // Leading URL '@' prefix
 const pair10 = parseCoordinatePair('@40.9873319,14.2317669');
 assert.ok(pair10);
 assert.equal(pair10.lat, '40.9873319');
 assert.equal(pair10.lon, '14.2317669');

 // Boundary values
 assert.ok(parseCoordinatePair('90, 180'));
 assert.ok(parseCoordinatePair('-90, -180'));

 // Rejections: single number (preserve normal paste)
 assert.equal(parseCoordinatePair('40.987331944990146'), null);
 assert.equal(parseCoordinatePair('14.231766994573993'), null);

 // Rejections: out-of-range latitude
 assert.equal(parseCoordinatePair('90.0001, 14.2317'), null);
 assert.equal(parseCoordinatePair('-90.0001, 14.2317'), null);

 // Rejections: out-of-range longitude
 assert.equal(parseCoordinatePair('40.9873, 180.0001'), null);
 assert.equal(parseCoordinatePair('40.9873, -180.0001'), null);

 // Rejections: malformed / 3 numbers / text
 assert.equal(parseCoordinatePair('40.1, 14.2, 50.3'), null);
 assert.equal(parseCoordinatePair('abc, def'), null);
 assert.equal(parseCoordinatePair('40.1, abc'), null);
 assert.equal(parseCoordinatePair('abc, 14.2'), null);
 assert.equal(parseCoordinatePair('40.1,'), null);
 assert.equal(parseCoordinatePair(',14.2'), null);
 assert.equal(parseCoordinatePair(''), null);
 assert.equal(parseCoordinatePair(null), null);
 assert.equal(parseCoordinatePair(undefined), null);
});

test('Pasting Google Maps coordinate pair into latitude field fills both fields in lat/lon order, marks dirty, and invalidates coverage', async () => {
 const readyProfile = profile();
 readyProfile.id = 'p_coord_lat';
 readyProfile.location = { latitude: 45.0, longitude: 9.0 };
 const h = mountSection2Helper(readyProfile);

 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path === '/api/profiles/p_coord_lat/validation') {
   return response({ ready: true, compiled: true, blockers: [], revision: 'r_ready', fiscal_rule_revision: 'f'.repeat(64) });
  }
  return origFetch(path, opts);
 };

 h.f.profile.value = 'p_coord_lat';
 await h.f.profile.onchange();

 // Profile is initially ready
 assert.equal(h.setup.workflow.status, 'ready');
 assert.equal(h.client.ready, true);
 assert.equal(h.f.latitude.value, '45');
 assert.equal(h.f.longitude.value, '9');

 // Dispatch paste event on latitude input with Google Maps coordinate pair
 let prevented = false;
 const pasteEvt = {
  type: 'paste',
  clipboardData: {
   getData: (fmt) => (fmt === 'text/plain' || fmt === 'text' ? '40.987331944990146, 14.231766994573993' : '')
  },
  preventDefault: () => { prevented = true; }
 };

 const dispatched = h.f.latitude.dispatchEvent(pasteEvt);

 // Paste was intercepted and default prevented
 assert.equal(prevented, true);
 assert.equal(dispatched, false);

 // Both fields filled in latitude/longitude order
 assert.equal(h.f.latitude.value, '40.987331944990146');
 assert.equal(h.f.longitude.value, '14.231766994573993');

 // Input event fired: marks dirty, invalidates ready and coverage
 assert.equal(h.setup.workflow.status, 'draft');
 assert.equal(h.client.ready, false);
});

test('Pasting Google Maps coordinate pair into longitude field fills both fields in lat/lon order', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 h.f.latitude.value = '';
 h.f.longitude.value = '';

 let prevented = false;
 const pasteEvt = {
  type: 'paste',
  clipboardData: {
   getData: () => '40.987331944990146, 14.231766994573993'
  },
  preventDefault: () => { prevented = true; }
 };

 const dispatched = h.f.longitude.dispatchEvent(pasteEvt);

 assert.equal(prevented, true);
 assert.equal(dispatched, false);

 // Both fields filled in latitude/longitude order
 assert.equal(h.f.latitude.value, '40.987331944990146');
 assert.equal(h.f.longitude.value, '14.231766994573993');
});

test('Pasting normal single number does not intercept or overwrite opposing field', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 h.f.latitude.value = '45.0';
 h.f.longitude.value = '9.0';

 let prevented = false;
 const pasteEvt = {
  type: 'paste',
  clipboardData: {
   getData: () => '41.123456'
  },
  preventDefault: () => { prevented = true; }
 };

 const dispatched = h.f.latitude.dispatchEvent(pasteEvt);

 // Normal single-number paste is NOT intercepted (default not prevented)
 assert.equal(prevented, false);
 assert.equal(dispatched, true);

 // Longitude field remains unchanged
 assert.equal(h.f.longitude.value, '9.0');
});

test('Pasting malformed or out-of-range pair does not intercept', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 h.f.latitude.value = '45.0';
 h.f.longitude.value = '9.0';

 // 1. Out of range latitude (> 90)
 let prevented1 = false;
 h.f.latitude.dispatchEvent({
  type: 'paste',
  clipboardData: { getData: () => '95.0, 14.2' },
  preventDefault: () => { prevented1 = true; }
 });
 assert.equal(prevented1, false);
 assert.equal(h.f.latitude.value, '45.0');
 assert.equal(h.f.longitude.value, '9.0');

 // 2. Out of range longitude (> 180)
 let prevented2 = false;
 h.f.latitude.dispatchEvent({
  type: 'paste',
  clipboardData: { getData: () => '40.0, 195.2' },
  preventDefault: () => { prevented2 = true; }
 });
 assert.equal(prevented2, false);
 assert.equal(h.f.latitude.value, '45.0');
 assert.equal(h.f.longitude.value, '9.0');

 // 3. Malformed text
 let prevented3 = false;
 h.f.latitude.dispatchEvent({
  type: 'paste',
  clipboardData: { getData: () => 'hello, world' },
  preventDefault: () => { prevented3 = true; }
 });
 assert.equal(prevented3, false);
 assert.equal(h.f.latitude.value, '45.0');
 assert.equal(h.f.longitude.value, '9.0');
});

test('Manual coordinate entry without paste functions normally', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 h.f.latitude.value = '41.9028';
 h.f.latitude.dispatchEvent(new (doc.defaultView?.Event || Event)('input', { bubbles: true }));

 assert.equal(h.f.latitude.value, '41.9028');
 assert.equal(h.setup.workflow.status, 'draft');

 h.f.longitude.value = '12.4964';
 h.f.longitude.dispatchEvent(new (doc.defaultView?.Event || Event)('input', { bubbles: true }));

 assert.equal(h.f.longitude.value, '12.4964');
});

test('action buttons render accessible progress container with spinner and polite live region', async () => {
 const h = mountSection2Helper();
 const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
 assert.ok(step3Btn, 'step 3 button found');
 const controls = step3Btn.parentNode;
 assert.ok(controls.className?.includes('action-step-controls'));

 const progressEl = controls.children.find(c => c.className?.includes('action-progress'));
 assert.ok(progressEl, 'progress container exists');
 assert.equal(progressEl.hidden, true, 'progress is hidden initially');
 assert.equal(progressEl.attrs['role'], 'status', 'role is status');
 assert.equal(progressEl.attrs['aria-live'], 'polite', 'aria-live is polite');

 const spinner = progressEl.children.find(c => c.className?.includes('progress-spinner'));
 assert.ok(spinner, 'spinner element exists');
 assert.equal(spinner.attrs['aria-hidden'], 'true', 'spinner is aria-hidden');

 const textEl = progressEl.children.find(c => c.className?.includes('action-progress-text'));
 assert.ok(textEl, 'progress text element exists');
});

test('Generate solar data and compile sets aria-busy, updates stage text, prevents duplicate submit, and clears on finish', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 h.fillBase();
 h.f.years.value = '2020';
 h.f.providerConsent.checked = true;

 let compiled = false;
 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path.endsWith('/solar-coverage')) { return response({ years: SUPPORTED_YEARS }); }
  if (path.endsWith('/compile')) { compiled = true; return response({ compiled: true, revision: h.client.revision }); }
  if (path.endsWith('/validation')) { return response({ ready: compiled, revision: h.client.revision, blockers: [] }); }
  if (path.endsWith('/solar-jobs')) { return response({ id: 'j_1' }); }
  return origFetch(path, opts);
 };

 await h.click('Save and review draft');
 await h.click('Check solar coverage');

 const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
 const progressEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-progress'));
 const textEl = progressEl.children.find(c => c.className?.includes('action-progress-text'));

 let inFlightCalls = 0;
 let resolveJob;
 const jobPromise = new Promise(r => { resolveJob = r; });

 h.client.waitJob = async (jobId, { generation, onProgress } = {}) => {
  inFlightCalls++;
  // Verify in-flight busy states
  assert.equal(step3Btn.attrs['aria-busy'], 'true', 'button has aria-busy="true"');
  assert.equal(step3Btn.disabled, true, 'button is disabled');
  assert.equal(progressEl.hidden, false, 'progress container is visible');

  if (typeof onProgress === 'function') onProgress('fetching pvgis radiation');
  assert.equal(textEl.textContent, 'Solar generation: fetching pvgis radiation');

  // Attempt duplicate submission while in flight
  await step3Btn.onclick();
  assert.equal(inFlightCalls, 1, 'duplicate submission prevented while in flight');

  await jobPromise;
  return { state: 'complete' };
 };

 const clickPromise = step3Btn.onclick();
 await new Promise(r => setTimeout(r, 10));

 // Release job
 resolveJob();
 await clickPromise;

 // Verify final state
 assert.equal(step3Btn.disabled, false, 'button is re-enabled');
 assert.equal(step3Btn.attrs['aria-busy'], undefined, 'aria-busy is cleared');
 assert.equal(progressEl.hidden, true, 'progress is hidden on completion');
 assert.equal(textEl.textContent, '', 'progress text is cleared');
 assert.equal(h.setup.workflow.status, 'ready');
});

test('Action buttons clear busy indicators and show errors on failure', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');
 h.fillBase();
 h.f.providerConsent.checked = false; // missing consent blocks

 const step3Btn = h.all().find(n => n.tag === 'button' && n.textContent === 'Generate solar data and compile (retry)');
 const progressEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-progress'));
 const errEl = step3Btn.parentNode.children.find(c => c.className?.includes('action-error'));

 await step3Btn.onclick();

 assert.equal(step3Btn.disabled, false, 'button re-enabled');
 assert.equal(step3Btn.attrs['aria-busy'], undefined, 'aria-busy cleared on error');
 assert.equal(progressEl.hidden, true, 'progress remains hidden');
 assert.equal(errEl.hidden, false, 'action-error is visible');
 assert.ok(errEl.textContent.includes('authorization') || errEl.textContent.includes('consent'));
});

test('GME archive upload updates progress text, sets aria-busy, and clears on finish', async () => {
 const h = mountSection2Helper();
 await h.click('Create profile');

 const importBtn = h.all().find(n => n.id === 'importGmeArchivesBtn');
 const progressEl = importBtn.parentNode.children.find(c => c.className?.includes('action-progress'));
 const textEl = progressEl.children.find(c => c.className?.includes('action-progress-text'));

 h.f.gme_archives.files = [
  { name: 'MGP_202401.zip' },
  { name: 'MGP_202402.zip' }
 ];

 let uploadStages = [];
 const origUpload = h.setup.workflow.uploadMarketCatalog.bind(h.setup.workflow);
 h.setup.workflow.uploadMarketCatalog = async (file) => {
  assert.equal(importBtn.attrs['aria-busy'], 'true');
  assert.equal(importBtn.disabled, true);
  assert.equal(progressEl.hidden, false);
  uploadStages.push(textEl.textContent);
  return { catalog: { revision: '1'.repeat(64), series: ['CSUD'], coverage: ['2024-01-01', '2024-03-01'], missing_months: [], shape_available: true } };
 };

 await importBtn.onclick();

 assert.ok(uploadStages.length === 2);
 assert.ok(uploadStages[0].includes('archive 1 of 2'));
 assert.ok(uploadStages[1].includes('archive 2 of 2'));

 assert.equal(importBtn.disabled, false);
 assert.equal(importBtn.attrs['aria-busy'], undefined);
 assert.equal(progressEl.hidden, true);
});

test('resetSwitchHourOptimizer renders animated spinner on pending and omits spinner on error or clear', () => {
 const fs = require('node:fs');
 const path = require('node:path');
 const vm = require('node:vm');

 const schedSrc = fs.readFileSync(path.join(__dirname, '../feasibility/web/schedule.js'), 'utf8');
 let targetHtml = '';
 const mockTarget = {
  get innerHTML() { return targetHtml; },
  set innerHTML(v) { targetHtml = v; }
 };
 const win = {};
 const sandbox = {
  window: win,
  document: {
   getElementById: id => (id === 'switchHourOptimizer' ? mockTarget : null),
   addEventListener: () => {}
  },
  console
 };
 vm.createContext(sandbox);
 vm.runInContext(schedSrc, sandbox);

 const resetFn = win.resetSwitchHourOptimizer;
 assert.equal(typeof resetFn, 'function');

 // 1. Simulation pending message includes progress-spinner and aria-busy
 resetFn('Waiting for dispatch simulation.');
 assert.ok(targetHtml.includes('class="progress-spinner"'));
 assert.ok(targetHtml.includes('aria-busy="true"'));
 assert.ok(targetHtml.includes('Waiting for dispatch simulation.'));

 // 2. Error message omits spinner and aria-busy
 resetFn('Schedule comparison unavailable: network failure');
 assert.equal(targetHtml.includes('progress-spinner'), false);
 assert.equal(targetHtml.includes('aria-busy'), false);
 assert.ok(targetHtml.includes('Schedule comparison unavailable'));

 // 3. Empty message clears
 resetFn('');
 assert.equal(targetHtml, '');
});

test('displaySummarySensitivitySkeleton renders progress-spinner in pending banner and tornado rows', () => {
 const fs = require('node:fs');
 const path = require('node:path');
 const vm = require('node:vm');

 const appSrc = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
 const start = appSrc.indexOf('function displaySummarySensitivitySkeleton(');
 const end = appSrc.indexOf('\nasync function renderSummarySensitivity(', start);
 const fnCode = appSrc.slice(start, end);

 let cardHtml = '';
 const mockEl = {
  get innerHTML() { return cardHtml; },
  set innerHTML(v) { cardHtml = v; }
 };

 const sandbox = {
  esc: s => String(s ?? ''),
  fmt: (v, t) => String(v ?? ''),
  getWeatherEnsembleMeta: () => ({ count: 20 }),
  getScopeCashMetrics: () => null,
  current: null,
  summaryScope: 'solar',
  summaryCash: 'cash',
  console
 };
 vm.createContext(sandbox);
 vm.runInContext(fnCode + '\nglobalThis.displaySummarySensitivitySkeleton = displaySummarySensitivitySkeleton;', sandbox);

 sandbox.displaySummarySensitivitySkeleton(mockEl, 'Evaluating…');

 // Banner check
 assert.ok(cardHtml.includes('class="stale-badge-strip skeleton-strip" role="status" aria-live="polite" aria-busy="true"'));
 assert.ok(cardHtml.includes('<span class="progress-spinner" aria-hidden="true"></span>'));

 // Rows check
 assert.ok(cardHtml.includes('class="tornado-not-eval" role="status" aria-live="polite"'));
 assert.ok(cardHtml.includes('Evaluating…'));
});

test('renderBehaviorPending renders spinner in charts and clipping table when active', () => {
 const fs = require('node:fs');
 const path = require('node:path');
 const vm = require('node:vm');

 const appSrc = fs.readFileSync(path.join(__dirname, '../feasibility/web/app.js'), 'utf8');
 const sClip = appSrc.indexOf('function renderPvClippingBreakdown(');
 const eClip = appSrc.indexOf('\nlet behaviorMatrixCache', sClip);
 const clipCode = appSrc.slice(sClip, eClip);

 const sBeh = appSrc.indexOf('let behaviorMatrixCache');
 const eBeh = appSrc.indexOf('\nfunction renderHeatPumpReport(', sBeh);
 const behCode = appSrc.slice(sBeh, eBeh);

 const elements = {
  typicalDayChart: { innerHTML: '' },
  monthHourHeatmap: { innerHTML: '' },
  pvClippingTable: { innerHTML: '' },
  annualFlowMeta: { innerHTML: '' }
 };

 const sandbox = {
  $: id => elements[id] || null,
  esc: s => String(s ?? ''),
  resetSwitchHourOptimizer: () => {},
  document: { querySelectorAll: () => [] },
  settings: {},
  current: null,
  console
 };
 vm.createContext(sandbox);
 vm.runInContext(clipCode + '\n' + behCode + '\nglobalThis.renderBehaviorPending = renderBehaviorPending; globalThis.renderPvClippingBreakdown = renderPvClippingBreakdown; globalThis.pendingBehaviorRequests = pendingBehaviorRequests;', sandbox);

 // 1. While work is queued / running
 sandbox.renderBehaviorPending(true);
 assert.ok(elements.typicalDayChart.innerHTML.includes('class="progress-spinner"'));
 assert.ok(elements.typicalDayChart.innerHTML.includes('aria-busy="true"'));
 assert.ok(elements.monthHourHeatmap.innerHTML.includes('class="progress-spinner"'));

 sandbox.pendingBehaviorRequests.set('req1', Promise.resolve());
 sandbox.renderPvClippingBreakdown(null);
 assert.ok(elements.pvClippingTable.innerHTML.includes('class="progress-spinner"'));
 assert.ok(elements.pvClippingTable.innerHTML.includes('aria-busy="true"'));

 // 2. When work is NOT queued / running (no spinner)
 sandbox.pendingBehaviorRequests.clear();
 sandbox.renderBehaviorPending(false);
 assert.equal(elements.typicalDayChart.innerHTML.includes('progress-spinner'), false);
 assert.equal(elements.monthHourHeatmap.innerHTML.includes('progress-spinner'), false);

 sandbox.renderPvClippingBreakdown(null);
 assert.equal(elements.pvClippingTable.innerHTML.includes('progress-spinner'), false);
 assert.equal(elements.pvClippingTable.innerHTML.includes('aria-busy'), false);
});



test('gross confirmation checkbox persists across draft save, profile switching, and profile re-selection without re-prompt', async () => {
 const h = mountSection2Helper();

 // 1. Create Profile 1 in monthly mode
 await h.click('Create profile');
 const p1 = h.client.profileId;
 h.fillBase();
 h.f.kind.value = 'monthly';
 h.f.totals.value = Array(12).fill(200).join(',');
 h.f.totals.oninput();
 h.f.gross.checked = true;
 h.f.gross.onchange();

 // Save Profile 1
 await h.click('Save and review draft');
 assert.equal(h.store.get(p1).consumption.kind, 'monthly');
 assert.deepEqual(h.store.get(p1).consumption.monthly_totals, Array(12).fill(200));

 // 2. Create Profile 2 -> must NOT inherit gross confirmation (per-profile independence, no auto-check)
 await h.click('Create profile');
 const p2 = h.client.profileId;
 assert.notEqual(p1, p2);
 assert.equal(h.f.gross.checked, false, 'Profile 2 starts with gross unchecked');

 // 3. Switch back to Profile 1
 h.f.profile.value = p1;
 await h.f.profile.onchange();
 assert.equal(h.client.profileId, p1);
 assert.equal(h.f.gross.checked, true, 'Profile 1 restored gross confirmation checkbox');

 // 4. Modify monthly values and save directly WITHOUT manually re-checking gross -> succeeds without GROSS_LOAD_REQUIRED
 h.f.reference_year.value = '2025';
 h.f.totals.value = Array(12).fill(250).join(',');
 h.f.totals.oninput();
 await h.click('Save and review draft');
 assert.deepEqual(h.store.get(p1).consumption.monthly_totals, Array(12).fill(250));
});

test('existing PV and reconciled checkboxes persist independently per profile, preserving validation', async () => {
 const h = mountSection2Helper();

 await h.click('Create profile');
 const p1 = h.client.profileId;
 h.fillBase();
 h.f.kind.value = 'monthly';
 h.f.totals.value = Array(12).fill(200).join(',');
 h.f.totals.oninput();
 h.f.gross.checked = true;
 h.f.gross.onchange();
 h.f.existingPv.checked = true;
 h.f.existingPv.onchange();
 h.f.reconciled.checked = true;
 h.f.reconciled.onchange();

 await h.click('Save and review draft');

 // Switch to Profile 2
 await h.click('Create profile');
 const p2 = h.client.profileId;
 assert.equal(h.f.existingPv.checked, false, 'Profile 2 does not inherit existingPv');
 assert.equal(h.f.reconciled.checked, false, 'Profile 2 does not inherit reconciled');

 // Switch back to Profile 1
 h.f.profile.value = p1;
 await h.f.profile.onchange();
 assert.equal(h.f.existingPv.checked, true, 'Profile 1 restored existingPv');
 assert.equal(h.f.reconciled.checked, true, 'Profile 1 restored reconciled');

 // Now uncheck reconciled on Profile 1 -> saving must fail with GROSS_LOAD_REQUIRED (existing PV validation preserved)
 h.f.reference_year.value = '2025';
 h.f.reconciled.checked = false;
 h.f.reconciled.onchange();
 await h.click('Save and review draft');
 const errEl = h.all().find(n => n.className?.includes('action-error') && !n.hidden);
 const statusEl = h.root.children.find(c => c.attrs?.role === 'status');
 const errText = (errEl?.textContent || '') + ' ' + (statusEl?.textContent || '');
 assert.ok(errText.includes('Reconcile grid readings') || errText.includes('GROSS_LOAD_REQUIRED'), 'fails when existing PV without reconciliation');
});

test('provider consent checkbox persists across profile switching and solar coverage check', async () => {
 const h = mountSection2Helper();
 const origFetch = h.client.fetcher;
 h.client.fetcher = async (path, opts) => {
  if (path.endsWith('/solar-coverage')) return response({ years: [2020] });
  return origFetch(path, opts);
 };

 await h.click('Create profile');
 const p1 = h.client.profileId;
 h.fillBase();
 h.f.kind.value = 'monthly';
 h.f.totals.value = Array(12).fill(200).join(',');
 h.f.totals.oninput();
 h.f.providerConsent.checked = true;
 h.f.providerConsent.onchange();

 await h.click('Save and review draft');

 // Switch to Profile 2
 await h.click('Create profile');
 const p2 = h.client.profileId;
 assert.equal(h.f.providerConsent.checked, false, 'Profile 2 does not have consent silently inferred');

 // Switch back to Profile 1
 h.f.profile.value = p1;
 await h.f.profile.onchange();
 assert.equal(h.f.providerConsent.checked, true, 'Profile 1 restored providerConsent');

 // Check solar coverage passes without throwing PROVIDER_CONSENT_REQUIRED
 await h.click('Check solar coverage');
 const statusNode = h.root.children.find(c => c.attrs?.role === 'status');
 assert.ok(statusNode.textContent.includes('Solar coverage verified') || statusNode.textContent.includes('loaded') || statusNode.textContent.includes('Historical weather'));
});

test('explicit unchecking persists unchecked state and does not auto-check', async () => {
 const h = mountSection2Helper();

 await h.click('Create profile');
 const p1 = h.client.profileId;
 h.f.gross.checked = true;
 h.f.gross.onchange();

 // Now user unchecks gross
 h.f.gross.checked = false;
 h.f.gross.onchange();

 // Switch to Profile 2 and back
 await h.click('Create profile');
 h.f.profile.value = p1;
 await h.f.profile.onchange();
 assert.equal(h.f.gross.checked, false, 'Gross remains explicitly unchecked after switching back');
});

test('reopening onboarding (remounting) restores persisted profile checkboxes', async () => {
 const initial = profile();
 initial.id = 'p_persist';
 const h = mountSection2Helper(initial);

 await h.click('Create profile');
 const p1 = h.client.profileId;
 h.fillBase();
 h.f.kind.value = 'monthly';
 h.f.totals.value = Array(12).fill(200).join(',');
 h.f.totals.oninput();
 h.f.gross.checked = true;
 h.f.gross.onchange();
 h.f.providerConsent.checked = true;
 h.f.providerConsent.onchange();
 await h.click('Save and review draft');

 // Simulate reopening onboarding by remounting with a new root element and same client
 const root2 = new Element('section', doc);
 const setup2 = mount({ root: root2, client: h.client });
 setup2.fields.profile.value = p1;
 await setup2.fields.profile.onchange();

 assert.equal(setup2.fields.gross.checked, true, 'Remounted onboarding restored gross');
 assert.equal(setup2.fields.providerConsent.checked, true, 'Remounted onboarding restored providerConsent');
});

test('return-to-onboarding navigation path restores checkboxes with per-profile isolation and explicit unchecked values', async () => {
 const persistentMap = new Map();
 const mockLocalStorage = {
  getItem: k => persistentMap.get(k) ?? null,
  setItem: (k, v) => persistentMap.set(k, String(v)),
  removeItem: k => persistentMap.delete(k),
  clear: () => persistentMap.clear()
 };
 const storage = new HouseholdStorage(mockLocalStorage);

 const profilesStore = new Map();
 let rev = 0, compiled = false, opened = false, householdViewsOpened = false;

 const root = new Element('section', doc);
 root.id = 'householdOnboarding';

 const client = new HouseholdClient({
  storage,
  fetch: async (path, opts) => {
   const body = opts.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') {
    if (opts.method === 'POST') {
     const id = 'prof_' + (++rev);
     const p = profile();
     p.id = id;
     profilesStore.set(id, p);
     return response({ id, revision: 'r' + rev });
    }
    return response({ profiles: Array.from(profilesStore.keys()).map(id => ({ id })) });
   }
   if (path.endsWith('/validation')) return response({ ready: compiled, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
   if (path.endsWith('/solar-jobs')) return response({ id: 'job_1', state: 'queued' });
   if (path === '/api/jobs/job_1') return response({ id: 'job_1', profile_id: client.profileId, revision: 'r' + rev, state: 'complete' });
   if (path.endsWith('/compile')) { compiled = true; return response({ compiled: true, revision: 'r' + (++rev) }); }
   const id = path.split('/').pop();
   if (opts.method === 'PATCH') {
    rev++;
    profilesStore.set(id, body.profile);
    return response({ id, revision: 'r' + rev, profile: body.profile });
   }
   return response({ id, revision: 'r' + rev, profile: profilesStore.get(id) || profile() });
  }
 });

 const clearHouseholdView = () => {
  if (householdViewsOpened) householdViewsOpened = false;
  storage.clearPrivate();
  root.hidden = false;
 };
 const openHouseholdViews = () => {
  root.hidden = true;
  householdViewsOpened = true;
  opened = true;
 };

 const setup = mount({ root, client, onReady: openHouseholdViews, onClear: clearHouseholdView });
 const all = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root); return nodes; };
 const click = async text => { const b = all().find(n => n.tag === 'button' && n.textContent === text); assert.ok(b, text); await b.onclick(); };

 // Startup initialization as in app.js
 await setup.refresh();

 // 1. Create Profile 1
 await click('Create profile');
 const p1 = client.profileId;
 assert.ok(p1, 'profile 1 created');
 const f = setup.fields;
 for (const [k, v] of Object.entries({ country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9', kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01', totals: Array(12).fill(200).join(','), baseline_cooling_kwh: '0', reference_year: '2025', years: '2020', loss_percent: '14', mounting: 'Free standing 2025', technology: 'cSi', zone: 'NORD', series: 'NORD', valuation: 'flat', window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters', effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01', sources: 'Synthetic test', ev_action: 'unchanged', cooling_action: 'unchanged' })) f[k].value = v;
 f.gross.checked = true;
 f.providerConsent.checked = true;

 await click('Save and review draft');
 assert.equal(setup.workflow.status, 'draft');
 assert.equal(f.gross.checked, true);
 assert.equal(f.providerConsent.checked, true);

 // 2. Compile and open calculation views
 await click('Check solar coverage');
 f.years.value = '2020';
 await click('Generate solar data and compile (retry)');
 assert.equal(setup.workflow.status, 'ready');

 await click('Open calculation views');
 assert.equal(opened, true);
 assert.equal(householdViewsOpened, true);
 assert.equal(root.hidden, true);

 // 3. Return to onboarding via "Household profiles" click
 const inOnboarding = root && !root.hidden;
 assert.equal(inOnboarding, false, 'views are open');
 client.invalidate();
 clearHouseholdView();
 await setup.refresh({ hydrate: true });

 assert.equal(root.hidden, false, 'onboarding view restored');
 assert.equal(setup.fields.gross.checked, true, 'gross checkbox remembered without manual onchange');
 assert.equal(setup.fields.providerConsent.checked, true, 'providerConsent checkbox remembered without manual onchange');
 assert.equal(setup.fields.profile.value, p1, 'correct profile selected');

 // 4. Per-profile isolation: Create Profile 2 -> must start unchecked
 await click('Create profile');
 const p2 = client.profileId;
 assert.notEqual(p1, p2);
 assert.equal(setup.fields.gross.checked, false, 'Profile 2 starts unchecked');
 assert.equal(setup.fields.providerConsent.checked, false, 'Profile 2 consent starts unchecked');

 // 5. Switch back to Profile 1 via selector -> checks restored
 setup.fields.profile.value = p1;
 await setup.fields.profile.onchange();
 assert.equal(setup.fields.gross.checked, true, 'Profile 1 restored gross checked');
 assert.equal(setup.fields.providerConsent.checked, true, 'Profile 1 restored consent checked');

 // 6. Explicit uncheck on Profile 1 -> preserved across navigation and refresh
 setup.fields.gross.checked = false;
 setup.fields.gross.onchange();

 client.invalidate();
 clearHouseholdView();
 await setup.refresh({ hydrate: true });
 assert.equal(setup.fields.gross.checked, false, 'Profile 1 explicit unchecked gross preserved');
 assert.equal(setup.fields.providerConsent.checked, true, 'Profile 1 consent remains checked');

 // Re-check gross and uncheck consent
 setup.fields.gross.checked = true;
 setup.fields.gross.onchange();
 setup.fields.providerConsent.checked = false;
 setup.fields.providerConsent.onchange();

 client.invalidate();
 clearHouseholdView();
 await setup.refresh({ hydrate: true });
 assert.equal(setup.fields.gross.checked, true, 'Profile 1 gross remains checked');
 assert.equal(setup.fields.providerConsent.checked, false, 'Profile 1 explicit unchecked consent preserved');

 // Re-check consent and save valid draft
 setup.fields.providerConsent.checked = true;
 setup.fields.providerConsent.onchange();
 await click('Save and review draft');

 // 7. Full page remount / reload simulation
 const root2 = new Element('section', doc);
 root2.id = 'householdOnboarding';
 const client2 = new HouseholdClient({ storage, fetch: client.fetcher });
 assert.equal(client2.profileId, p1, 'fresh client restored selected profile from persistent storage');
 const setup2 = mount({ root: root2, client: client2 });
 await setup2.refresh(); // startup initialization
 assert.equal(setup2.fields.profile.value, p1, 'fresh mount restored profile selection');
 assert.equal(setup2.fields.gross.checked, true, 'fresh mount preserved gross checked');
 assert.equal(setup2.fields.providerConsent.checked, true, 'fresh mount preserved consent checked');
});

test('GME selected year survives eligibility refresh and profile reload', async () => {
 const h=mountSection2Helper(); await h.click('Create profile');
 const meta={revision:'a'.repeat(64),series:['CSUD'],coverage:['2025-01-01','2026-10-01'],window:['2025-01-01','2026-01-01'],windows:[['2025-01-01','2026-01-01'],['2026-01-01','2026-10-01']],years:{'2025':{complete:true,missing_months:[],shape_available:{CSUD:true}},'2026':{complete:false,missing_months:['2026-10','2026-11','2026-12'],shape_available:{CSUD:false}}},shape_available:{CSUD:true}};
 h.client.getMarketCatalog=async revision=>{assert.equal(revision,meta.revision);return {catalog:meta};};
 h.client.uploadMarketCatalog=async()=>({catalog:meta});
 h.f.gme_archives.files=[{name:'synthetic.zip'}];await h.click('Import GME price archives');
 const shape=()=>Array.from(h.f.valuation.children).find(o=>o.value==='shape_scaled');
 assert.equal(shape().disabled,false);
 h.f.profileMarketWindow.value=JSON.stringify(meta.windows[1]);
 h.f.profileMarketWindow.dispatchEvent({type:'change'});
 assert.equal(shape().disabled,true);assert.match(shape().title,/2026-10/);
 h.f.series.dispatchEvent({type:'change'});
 assert.equal(h.f.window_start.value,'2026-01-01');
 const saved=profile();saved.market={...saved.market,price_revision:meta.revision,window_start:'2026-01-01',window_end:'2026-10-01',valuation_mode:'absolute_proxy'};
 h.store.set('reload',saved);
 await h.setup.workflow.select('reload');
 assert.equal(h.setup.workflow.profile.market.window_start,'2026-01-01');
 assert.equal(h.setup.workflow.marketCatalog.revision,meta.revision);
});


test('loss_percent native constraints, rejection of invalid input, fractions and error clear on correction and retry', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const root = new Element('section', doc);
 let rev = 1;
 let compiled = false;
 let generationCalled = false;
 let lastGenerationSolar = null;
 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   if (path === '/api/session') return response({ csrf_token: 't' });
   if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_loss', revision: 'r' + rev } : { profiles: [{ id: 'p_loss', revision: 'r' + rev }] });
   if (path.endsWith('/validation')) return response({ ready: compiled, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (path.endsWith('/solar-coverage')) return response({ years: [2020] });
   if (path.endsWith('/solar-jobs')) {
    generationCalled = true;
    lastGenerationSolar = JSON.parse(opts.body).solar;
    return response({ id: 'job_solar', state: 'queued' });
   }
   if (path === '/api/jobs/job_solar') return response({ id: 'job_solar', profile_id: 'p_loss', revision: 'r' + rev, state: 'complete' });
   if (path.endsWith('/compile')) { compiled = true; return response({ compiled: true, revision: 'r' + (++rev) }); }
   return response({ id: 'p_loss', revision: 'r' + rev, profile: profile() });
  }
 });
 const setup = mount({ root, client });
 const all = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root); return nodes; };
 const click = async text => { const b = all().find(n => n.tag === 'button' && (n.textContent === text || n.textContent?.includes(text))); assert.ok(b, 'btn: ' + text); await b.onclick(); };
 const statusNode = root.children.find(c => c.attrs?.role === 'status');
 const f = setup.fields;

 await click('Create profile');

 // 1. Verify native constraints on loss_percent input
 assert.equal(f.loss_percent.min, '0', 'min constraint is 0');
 assert.equal(f.loss_percent.max, '100', 'max constraint is 100');
 assert.equal(f.loss_percent.step, 'any', 'step permits fractions');
 assert.equal(f.loss_percent.required, true, 'loss_percent is required');

 // Set base valid profile inputs
 for (const [k, v] of Object.entries({
  country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
  kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
  totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '0',
  reference_year: '2025', mounting: 'Free standing 2025', technology: 'cSi',
  zone: 'CSUD', series: 'CSUD', valuation: 'flat',
  window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
  effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
  retail_commodity: '0.135', retail_regulated: '0.045',
  sources: 'Synthetic test'
 })) f[k].value = v;
 f.gross.checked = true;
 f.ack.checked = true;
 f.confirmed.checked = true;
 f.providerConsent.checked = true;

 await click('Save and review draft');
 await click('Check solar coverage');
 f.years.value = '2020';

 const step3Btn = all().find(n => n.tag === 'button' && n.textContent?.includes('Generate solar data and compile'));
 assert.ok(step3Btn, 'step 3 button exists');
 const step3Error = step3Btn.parentNode.children.find(c => c.className === 'action-error');

 // 2. Test invalid submissions: empty, nonfinite, negative, >100.
 // Verify invalid input NEVER reaches generation endpoint.

 // 2a. Empty
 f.loss_percent.value = '   ';
 generationCalled = false;
 await click('Generate solar data and compile (retry)');
 assert.equal(generationCalled, false, 'generation endpoint must not be reached when loss_percent is empty');
 assert.match(statusNode.textContent, /FIELD_REQUIRED: System loss percent is required/);
 assert.equal(step3Error.hidden, false);
 assert.match(step3Error.textContent, /System loss percent is required/);

 // 2b. Nonfinite / invalid text
 f.loss_percent.value = 'not-a-number';
 generationCalled = false;
 await click('Generate solar data and compile (retry)');
 assert.equal(generationCalled, false, 'generation endpoint must not be reached when loss_percent is nonfinite');
 assert.match(statusNode.textContent, /NUMBER_REQUIRED: System loss percent must be a finite number/);
 assert.equal(step3Error.hidden, false);
 assert.match(step3Error.textContent, /System loss percent must be a finite number/);

 // 2c. Negative out of range
 f.loss_percent.value = '-0.01';
 generationCalled = false;
 await click('Generate solar data and compile (retry)');
 assert.equal(generationCalled, false, 'generation endpoint must not be reached when loss_percent is negative');
 assert.match(statusNode.textContent, /PERCENT_LIMIT: System loss percent must be between 0% and 100%/);
 assert.equal(step3Error.hidden, false);
 assert.match(step3Error.textContent, /System loss percent must be between 0% and 100%/);

 // 2d. Greater than 100 out of range
 f.loss_percent.value = '100.01';
 generationCalled = false;
 await click('Generate solar data and compile (retry)');
 assert.equal(generationCalled, false, 'generation endpoint must not be reached when loss_percent > 100');
 assert.match(statusNode.textContent, /PERCENT_LIMIT: System loss percent must be between 0% and 100%/);
 assert.equal(step3Error.hidden, false);
 assert.match(step3Error.textContent, /System loss percent must be between 0% and 100%/);

 // 3. Clear error on correction and retry, preserving unrelated action errors
 const step1Btn = all().find(n => n.tag === 'button' && n.textContent?.includes('Save and review draft'));
 const step1Error = step1Btn.parentNode.children.find(c => c.className === 'action-error');
 step1Error.hidden = false;
 step1Error.textContent = 'Unrelated error on Step 1';

 // User corrects input with valid fractional value
 f.loss_percent.value = '14.5';
 f.loss_percent.dispatchEvent(new (doc.defaultView?.Event || Event)('input', { bubbles: true }));

 // Loss error is cleared on correction
 assert.equal(step3Error.hidden, true, 'button error element must be hidden on correction');
 assert.equal(step3Error.textContent, '', 'button error text must be cleared on correction');
 assert.equal(statusNode.textContent, '', 'status text must be cleared on correction');

 // Unrelated action error is preserved
 assert.equal(step1Error.hidden, false, 'unrelated step 1 action error must not be cleared');
 assert.equal(step1Error.textContent, 'Unrelated error on Step 1', 'unrelated step 1 action error text preserved');

 // Retry succeeds and reaches generation endpoint with exact fraction
 generationCalled = false;
 await click('Generate solar data and compile (retry)');
 assert.equal(generationCalled, true, 'generation endpoint reached on retry');
 assert.equal(lastGenerationSolar.loss_percent, 14.5, 'fractional loss_percent sent to generation');
 assert.equal(setup.workflow.status, 'ready');
});

test('loss_percent exact boundaries 0% and 100% are permitted and reach generation', async () => {
 const { mount } = require('../feasibility/web/onboarding.js');
 const runBoundary = async val => {
  const root2 = new Element('section', doc);
  let rev2 = 1, compiled2 = false, generationCalled2 = false, lastSolar2 = null;
  const client2 = new HouseholdClient({
   fetch: async (path, opts) => {
    if (path === '/api/session') return response({ csrf_token: 't' });
    if (path === '/api/profiles') return response(opts.method === 'POST' ? { id: 'p_bnd', revision: 'r' + rev2 } : { profiles: [{ id: 'p_bnd', revision: 'r' + rev2 }] });
    if (path.endsWith('/validation')) return response({ ready: compiled2, revision: 'r' + rev2, fiscal_rule_revision: 'f'.repeat(64) });
    if (path.endsWith('/solar-coverage')) return response({ years: [2020] });
    if (path.endsWith('/solar-jobs')) {
     generationCalled2 = true;
     lastSolar2 = JSON.parse(opts.body).solar;
     return response({ id: 'job_solar', state: 'queued' });
    }
    if (path === '/api/jobs/job_solar') return response({ id: 'job_solar', profile_id: 'p_bnd', revision: 'r' + rev2, state: 'complete' });
    if (path.endsWith('/compile')) { compiled2 = true; return response({ compiled: true, revision: 'r' + (++rev2) }); }
    return response({ id: 'p_bnd', revision: 'r' + rev2, profile: profile() });
   }
  });
  const setup2 = mount({ root: root2, client: client2 });
  const all2 = () => { const nodes = []; const visit = n => { nodes.push(n); for (const c of n.children) visit(c); }; visit(root2); return nodes; };
  const click2 = async text => { const b = all2().find(n => n.tag === 'button' && (n.textContent === text || n.textContent?.includes(text))); assert.ok(b, 'btn: ' + text); await b.onclick(); };
  const f2 = setup2.fields;

  await click2('Create profile');
  for (const [k, v] of Object.entries({
   country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
   kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
   totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '0',
   reference_year: '2025', mounting: 'Free standing 2025', technology: 'cSi',
   zone: 'CSUD', series: 'CSUD', valuation: 'flat',
   window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
   effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
   retail_commodity: '0.135', retail_regulated: '0.045',
   sources: 'Synthetic test'
  })) f2[k].value = v;
  f2.gross.checked = true; f2.ack.checked = true; f2.confirmed.checked = true; f2.providerConsent.checked = true;
  await click2('Save and review draft');
  await click2('Check solar coverage');
  f2.years.value = '2020';
  f2.loss_percent.value = val;
  await click2('Generate solar data and compile (retry)');
  return { generationCalled: generationCalled2, lastSolar: lastSolar2, status: setup2.workflow.status };
 };

 const res0 = await runBoundary('0');
 assert.equal(res0.generationCalled, true, '0% lower boundary accepted');
 assert.equal(res0.lastSolar.loss_percent, 0);
 assert.equal(res0.status, 'ready');

 const res100 = await runBoundary('100');
 assert.equal(res100.generationCalled, true, '100% upper boundary accepted');
 assert.equal(res100.lastSolar.loss_percent, 100);
 assert.equal(res100.status, 'ready');
});


test('baseline_cooling_kwh matrix: unchanged with included unchecked normalizes blank to zero and preserves explicit values', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'monthly'; h.f.totals.value = Array(12).fill(100).join(',');

 // 1. Unchanged, included unchecked, blank -> normalizes to 0
 h.f.cooling_action.value = 'unchanged'; h.f.cooling_action.onchange();
 h.f.cooling_included.checked = false;
 h.f.baseline_cooling_kwh.value = '   ';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 0);

 // 2. Unchanged, included unchecked, explicit valid value -> survives
 h.f.baseline_cooling_kwh.value = '175';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 175);

 // 3. Unchanged, included unchecked, invalid negative -> fails validation
 h.f.baseline_cooling_kwh.value = '-25';
 await h.click('Save and review draft');
 const errEl = h.all().find(n => n.className?.includes('action-error') && !n.hidden);
 const statusEl = h.root.children.find(c => c.attrs?.role === 'status');
 const errText = (errEl?.textContent || '') + ' ' + (statusEl?.textContent || '');
 assert.match(errText, /NUMBER_REQUIRED.*nonnegative/);
});

test('baseline_cooling_kwh matrix: required when existing cooling may be present (included checked, add, replace)', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'monthly'; h.f.totals.value = Array(12).fill(100).join(',');
 const getError = () => {
  const errEl = h.all().find(n => n.className?.includes('action-error') && !n.hidden);
  const statusEl = h.root.children.find(c => c.attrs?.role === 'status');
  return (errEl?.textContent || '') + ' ' + (statusEl?.textContent || '');
 };

 // 1. Unchanged with included checked: blank fails
 h.f.cooling_action.value = 'unchanged'; h.f.cooling_action.onchange();
 h.f.cooling_included.checked = true;
 h.f.baseline_cooling_kwh.value = '';
 await h.click('Save and review draft');
 assert.match(getError(), /FIELD_REQUIRED.*accurate reported cooling/);

 // Explicit 0 survives
 h.f.baseline_cooling_kwh.value = '0';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 0);

 // Explicit 250 survives
 h.f.baseline_cooling_kwh.value = '250';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 250);

 // 2. Add action: blank fails
 h.f.cooling_action.value = 'add'; h.f.cooling_action.onchange();
 h.f.cooling_incremental.value = '600';
 h.f.baseline_cooling_kwh.value = '';
 await h.click('Save and review draft');
 assert.match(getError(), /FIELD_REQUIRED.*accurate reported cooling/);

 // Explicit 0 survives (new cooling with no prior AC)
 h.f.baseline_cooling_kwh.value = '0';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 0);
 assert.equal(h.getSaved().profile.thermal.electric_loads.find(u => u.id === 'cooling').incremental_annual_kwh, 600);

 // Explicit pre-existing baseline survives (adding to prior AC)
 h.f.baseline_cooling_kwh.value = '350';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 350);

 // 3. Replace action: blank fails
 h.f.cooling_action.value = 'replace'; h.f.cooling_action.onchange();
 h.f.baseline_cooling_kwh.value = '';
 await h.click('Save and review draft');
 assert.match(getError(), /FIELD_REQUIRED.*accurate reported cooling/);

 // 4. Action switching preserves entered baseline value without clearing
 h.f.baseline_cooling_kwh.value = '420';
 h.f.cooling_action.value = 'unchanged'; h.f.cooling_action.onchange();
 assert.equal(h.f.baseline_cooling_kwh.value, '420');
 h.f.cooling_action.value = 'add'; h.f.cooling_action.onchange();
 assert.equal(h.f.baseline_cooling_kwh.value, '420');
 h.f.cooling_action.value = 'replace'; h.f.cooling_action.onchange();
 assert.equal(h.f.baseline_cooling_kwh.value, '420');
});


test('baseline_cooling_kwh persists positive baseline through save, compile, open calculation views, and return to Household profiles', async () => {
 const root = new Element('section', doc);
 let rev = 0, compiled = false, opened = false, savedDoc = null;
 const profileId = 'a'.repeat(32);

 const client = new HouseholdClient({
  fetch: async (path, opts) => {
   const body = opts?.body ? JSON.parse(opts.body) : null;
   if (path === '/api/session') return response({ csrf_token: 'tab-only' });
   if (path === '/api/profiles') return response(opts?.method === 'POST' ? { id: profileId, revision: 'r' + (++rev) } : { profiles: [{ id: profileId, revision: 'r' + rev }] });
   if (path.endsWith('/validation')) return response({ ready: compiled, revision: 'r' + rev, fiscal_rule_revision: 'f'.repeat(64) });
   if (path.endsWith('/solar-coverage')) return response({ years: SUPPORTED_YEARS });
   if (path.endsWith('/solar-jobs')) return response({ id: 'job', state: 'queued' });
   if (path === '/api/jobs/job') return response({ id: 'job', profile_id: profileId, revision: 'r' + rev, state: 'complete' });
   if (path.endsWith('/compile')) { compiled = true; return response({ compiled: true, revision: 'r' + (++rev) }); }
   if (opts?.method === 'PATCH') { savedDoc = body.profile; rev++; }
   return response({ id: profileId, revision: 'r' + rev, profile: savedDoc || profile() });
  }
 });

 let calculationViewProfile = null;
 const setup = mount({
  root,
  client,
  onReady: () => {
   opened = true;
   calculationViewProfile = JSON.parse(JSON.stringify(savedDoc));
   assert.equal(calculationViewProfile?.default_settings?.baseline_cooling_kwh, 250);
  }
 });

 const all = () => { const res = []; const v = n => { res.push(n); for (const c of n.children) v(c); }; v(root); return res; };
 const click = async txt => { const b = all().find(n => n.tag === 'button' && (n.textContent === txt || n.textContent?.includes(txt))); assert.ok(b, 'btn: ' + txt); await b.onclick(); };
 const f = setup.fields;

 // 1. Create profile and set positive baseline
 await click('Create profile');
 for (const [k, v] of Object.entries({
  country: 'IT', timezone: 'Europe/Rome', latitude: '45', longitude: '9',
  kind: 'monthly', coverage_start: '2025-01-01', coverage_end: '2026-01-01',
  totals: Array(12).fill(100).join(','), baseline_cooling_kwh: '250',
  reference_year: '2025', loss_percent: '14', mounting: 'Free standing 2025', technology: 'cSi',
  zone: 'CSUD', series: 'CSUD', valuation: 'flat',
  window_start: '2025-01-01', window_end: '2026-01-01', fiscal_mode: 'parameters',
  effective_from: '2025-01-01', effective_to: '2027-01-01', as_of: '2026-01-01',
  retail_commodity: '0.135', retail_regulated: '0.045',
  sources: 'Synthetic test', ev_action: 'unchanged', cooling_action: 'unchanged'
 })) f[k].value = v;
 f.gross.checked = true; f.ack.checked = true; f.confirmed.checked = true; f.providerConsent.checked = true;
 f.cooling_included.checked = true;

 // 2. Save draft and verify persistence in saved document
 await click('Save and review draft');
 assert.equal(savedDoc.default_settings.baseline_cooling_kwh, 250);

 // 3. Verify solar coverage and compile
 await click('Check solar coverage');
 f.years.value = '2020';
 await click('Generate solar data and compile (retry)');
 assert.equal(setup.workflow.status, 'ready');

 // 4. Open calculation views
 await click('Open calculation views');
 assert.equal(opened, true);
 assert.equal(calculationViewProfile.default_settings.baseline_cooling_kwh, 250);

 // 5. Return to Household profiles via refresh
 await setup.refresh({ hydrate: true });
 assert.equal(f.baseline_cooling_kwh.value, '250', 'Baseline cooling value is preserved upon returning to household profile');
 assert.equal(f.cooling_action.value, 'unchanged');
 assert.equal(f.cooling_included.checked, true);

 // 6. Save again and ensure value survives round-trip
 await click('Save and review draft');
 assert.equal(savedDoc.default_settings.baseline_cooling_kwh, 250);
});

test('baseline_cooling_kwh replace action across included states: zero, positive, blank, and invalid values', async () => {
 const h = mountSection2Helper(); await h.click('Create profile'); h.fillBase();
 h.f.kind.value = 'monthly'; h.f.totals.value = Array(12).fill(100).join(',');
 const getError = () => {
  const errEl = h.all().find(n => n.className?.includes('action-error') && !n.hidden);
  const statusEl = h.root.children.find(c => c.attrs?.role === 'status');
  return (errEl?.textContent || '') + ' ' + (statusEl?.textContent || '');
 };

 h.f.cooling_action.value = 'replace'; h.f.cooling_action.onchange();
 assert.equal(h.f.cooling_incremental.disabled, true, 'Incremental demand must be disabled for replacement');

 // State A: Replace with cooling_included === true (replacing existing embedded cooling)
 h.f.cooling_included.checked = true;

 // A1. Blank fails validation
 h.f.baseline_cooling_kwh.value = '   ';
 await h.click('Save and review draft');
 assert.match(getError(), /FIELD_REQUIRED.*accurate reported cooling/);

 // A2. Negative number fails validation
 h.f.baseline_cooling_kwh.value = '-40';
 await h.click('Save and review draft');
 assert.match(getError(), /NUMBER_REQUIRED.*nonnegative/);

 // A3. Non-numeric input fails validation
 h.f.baseline_cooling_kwh.value = 'notanumber';
 await h.click('Save and review draft');
 assert.match(getError(), /NUMBER_REQUIRED.*finite number/);

 // A4. Explicit zero survives
 h.f.baseline_cooling_kwh.value = '0';
 await h.click('Save and review draft');
 const savedA4 = h.getSaved().profile;
 assert.equal(savedA4.default_settings.baseline_cooling_kwh, 0);
 const loadA4 = savedA4.thermal.electric_loads.find(u => u.id === 'cooling');
 assert.equal(loadA4.action, 'replace');
 assert.equal(loadA4.included_in_electric_load, true);
 assert.equal(loadA4.incremental_annual_kwh, null);

 // A5. Explicit positive survives
 h.f.baseline_cooling_kwh.value = '350';
 await h.click('Save and review draft');
 const savedA5 = h.getSaved().profile;
 assert.equal(savedA5.default_settings.baseline_cooling_kwh, 350);

 // State B: Replace with cooling_included === false
 h.f.cooling_included.checked = false;

 // B1. Blank fails validation (existing cooling may be present in replacement)
 h.f.baseline_cooling_kwh.value = '';
 await h.click('Save and review draft');
 assert.match(getError(), /FIELD_REQUIRED.*accurate reported cooling/);

 // B2. Negative number fails validation
 h.f.baseline_cooling_kwh.value = '-15';
 await h.click('Save and review draft');
 assert.match(getError(), /NUMBER_REQUIRED.*nonnegative/);

 // B3. Explicit zero survives
 h.f.baseline_cooling_kwh.value = '0';
 await h.click('Save and review draft');
 const savedB3 = h.getSaved().profile;
 assert.equal(savedB3.default_settings.baseline_cooling_kwh, 0);
 const loadB3 = savedB3.thermal.electric_loads.find(u => u.id === 'cooling');
 assert.equal(loadB3.action, 'replace');
 assert.equal(loadB3.included_in_electric_load, false);

 // B4. Explicit positive survives
 h.f.baseline_cooling_kwh.value = '480';
 await h.click('Save and review draft');
 assert.equal(h.getSaved().profile.default_settings.baseline_cooling_kwh, 480);
});
