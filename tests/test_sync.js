'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const SharedScenarioSync=require('../feasibility/web/sync.js');
const clone=value=>JSON.parse(JSON.stringify(value));
function server(initial={}){
 let state={revision:'0',settings:{export_irpef:.33,scop:4,...(initial.settings||{})},quote:clone(initial.quote||{line_items:[]}),defaults:{export_irpef:.33,scop:4,...(initial.defaults||{})},records:initial.records||[]};
 let counter=0,fail=false,hook=null;const writes=[];
 const request=async(path,body)=>{
  if(body){
   if(fail)throw Error('invalid edit');
   writes.push(clone(body));
   const settings=body.replace?body.settings:{...state.settings,...body.settings};
   const quote=body.replace?body.quote:{...state.quote,...body.quote};
   state={...state,revision:String(++counter),settings:clone(settings),quote:clone(quote)};
   const response=clone(state);
   if(hook){const waiting=hook;hook=null;await waiting();}
   return response;
  }
  const revision=new URL('http://test'+path).searchParams.get('revision');
  return revision===state.revision?{revision,unchanged:true}:clone(state);
 };
 return {request,writes,get state(){return clone(state);},set fail(value){fail=value;},set hook(value){hook=value;},defaults(value){state.defaults={...state.defaults,...value};state.revision=String(++counter);}};
}
function client(store){let displayed;const sync=new SharedScenarioSync({request:store.request,onState:state=>displayed=state});sync.receive(store.state);return {sync,get displayed(){return displayed;}};}

test('two stale clients merge different variables and propagate saved defaults independently',async()=>{
 const store=server(),a=client(store),b=client(store);
 a.sync.update({...a.sync.settings,export_irpef:.4},a.sync.quote);
 b.sync.update({...b.sync.settings,scop:5.1},b.sync.quote);
 await a.sync.flush();await b.sync.flush();await a.sync.poll();
 assert.deepEqual(store.state.settings,{export_irpef:.4,scop:5.1});
 assert.deepEqual(a.displayed.settings,b.displayed.settings);
 assert.deepEqual(store.writes[1].settings,{scop:5.1},'stale client posts only its own edit');
 store.defaults({export_irpef:.23});await a.sync.poll();await b.sync.poll();
 assert.equal(a.displayed.defaults.export_irpef,.23);assert.equal(b.displayed.defaults.export_irpef,.23);
 assert.equal(a.displayed.settings.export_irpef,.4,'saved default is independent of the current input');
});
test('edits made during a save survive its acknowledgement and persist in the next write',async()=>{
 const store=server(),a=client(store);
 a.sync.update({...a.sync.settings,export_irpef:.4},a.sync.quote);
 store.hook=async()=>{a.sync.update({...a.sync.settings,export_irpef:.25},a.sync.quote);};
 await a.sync.flush();
 assert.equal(store.writes.length,2);assert.equal(store.state.settings.export_irpef,.25);
 assert.equal(a.displayed.settings.export_irpef,.25);assert.equal(a.sync.pending(),false);
});
test('remote changes preserve an unsaved local draft; failed writes do not lose it',async()=>{
 const store=server(),a=client(store),b=client(store);
 a.sync.update({...a.sync.settings,export_irpef:null},a.sync.quote);store.fail=true;
 await assert.rejects(a.sync.flush(),/invalid edit/);store.fail=false;
 b.sync.update({...b.sync.settings,scop:5},b.sync.quote);await b.sync.flush();await a.sync.poll();
 assert.equal(a.displayed.settings.export_irpef,null);assert.equal(a.displayed.settings.scop,5);
 assert.equal(store.state.settings.export_irpef,.33);
 a.sync.update({...a.sync.settings,export_irpef:.24},a.sync.quote);await a.sync.flush();
 assert.equal(store.state.settings.export_irpef,.24);assert.equal(store.state.settings.scop,5);
});
test('deliberate scenario replacement clears old quote equipment and reload restores shared inputs',async()=>{
 const store=server(),a=client(store);
 a.sync.update(a.sync.settings,{line_items:[],battery_nameplate_kwh:10,battery_dod:.8});await a.sync.flush();
 a.sync.update({export_irpef:.3,scop:3},{line_items:[]},{replace:true});await a.sync.flush();
 assert.equal(store.writes.at(-1).replace,true);assert.deepEqual(store.state.quote,{line_items:[]});
 const reloaded=client(store);assert.deepEqual(reloaded.displayed.settings,{export_irpef:.3,scop:3});
 assert.deepEqual(reloaded.displayed.quote,{line_items:[]});
});
test('a read started before a completed save cannot roll the client back',async()=>{
 const store=server();let release;
 const wait=new Promise(resolve=>{release=resolve;});
 let held=false;
 const sync=new SharedScenarioSync({request:async(path,body)=>{const result=await store.request(path,body);if(!body&&held){held=false;await wait;}return result;}});
 sync.receive(store.state);
 // Force a full older response instead of the compact unchanged response.
 store.defaults({scop:4.1});held=true;
 const polling=sync.poll();
 sync.update({...sync.settings,export_irpef:.4},sync.quote);await sync.flush();release();await polling;
 assert.equal(sync.settings.export_irpef,.4);
});
test('connection failures retain edits for retry while invalid inputs wait for correction',async()=>{
 const store=server(),a=client(store);
 a.sync.update({...a.sync.settings,export_irpef:.4},a.sync.quote);store.fail=true;
 await assert.rejects(a.sync.flush());assert.equal(a.sync.retryNeeded,true);
 store.fail=false;await a.sync.poll();assert.equal(a.sync.lastPollSucceeded,true);
 await a.sync.flush();assert.equal(store.state.settings.export_irpef,.4);assert.equal(a.sync.retryNeeded,false);
 const invalid=new SharedScenarioSync({request:async()=>{const error=Error('invalid input');error.status=400;throw error;}});
 invalid.receive(store.state);invalid.update({...invalid.settings,export_irpef:null},invalid.quote);
 await assert.rejects(invalid.flush());assert.equal(invalid.pending(),true);assert.equal(invalid.retryNeeded,false);
});

test('same-value cloned schedule generates no dirty setting/no pending patch',async()=>{
 const canonicalSchedule=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 const store=server({settings:{ac_heating_windows:clone(canonicalSchedule)}});
 const a=client(store);
 
 const changed1=a.sync.update({...a.sync.settings,ac_heating_windows:clone(canonicalSchedule)},a.sync.quote);
 assert.equal(changed1,false);
 assert.equal(a.sync.pending(),false);
 assert.equal(a.sync.dirtySettings.has('ac_heating_windows'),false);
 
 const reordered=[
  {start_minute:480,end_minute:720,id:'morning'},
  {end_minute:1200,start_minute:960,id:'evening'}
 ];
 const changed2=a.sync.update({...a.sync.settings,ac_heating_windows:reordered},a.sync.quote);
 assert.equal(changed2,false);
 assert.equal(a.sync.pending(),false);
 assert.equal(a.sync.dirtySettings.has('ac_heating_windows'),false);
});

test('only changed schedule key gets sent in sync patch; unrelated settings/quote preserved',async()=>{
 const canonicalSchedule=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 const store=server({
  settings:{ac_heating_windows:clone(canonicalSchedule)},
  quote:{line_items:[{description:'Solar Panels',quantity:10,net_eur:2500}]}
 });
 const a=client(store);
 
 const modifiedSchedule=[
  {id:'morning',start_minute:420,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 const changed=a.sync.update({...a.sync.settings,ac_heating_windows:modifiedSchedule},a.sync.quote);
 assert.equal(changed,true);
 assert.equal(a.sync.dirtySettings.has('ac_heating_windows'),true);
 assert.equal(a.sync.dirtySettings.size,1);
 assert.equal(a.sync.dirtyQuote.size,0);
 
 await a.sync.flush();
 const lastWrite=store.writes.at(-1);
 assert.deepEqual(Object.keys(lastWrite.settings),['ac_heating_windows']);
 assert.deepEqual(lastWrite.settings.ac_heating_windows,modifiedSchedule);
 assert.deepEqual(lastWrite.quote,{});
 assert.equal(store.state.settings.export_irpef,.33);
 assert.equal(store.state.settings.scop,4);
 assert.equal(store.state.quote.line_items.length,1);
});

test('local schedule edit survives incoming remote unrelated patch while dirty',async()=>{
 const canonicalSchedule=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 const store=server({settings:{ac_heating_windows:clone(canonicalSchedule)}});
 const a=client(store),b=client(store);
 
 const draftSchedule=[
  {id:'morning',start_minute:360,end_minute:600},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 a.sync.update({...a.sync.settings,ac_heating_windows:draftSchedule},a.sync.quote);
 assert.equal(a.sync.pending(),true);
 
 b.sync.update({...b.sync.settings,scop:4.8},b.sync.quote);
 await b.sync.flush();
 
 await a.sync.poll();
 assert.equal(a.displayed.settings.scop,4.8);
 assert.deepEqual(a.displayed.settings.ac_heating_windows,draftSchedule);
 assert.equal(a.sync.dirtySettings.has('ac_heating_windows'),true);
 assert.equal(a.sync.pending(),true);
});

test('incoming server equal canonical object keys in other order does not create save loop',async()=>{
 const serverScheduleReordered=[
  {end_minute:720,start_minute:480,id:'morning'},
  {end_minute:1200,start_minute:960,id:'evening'}
 ];
 const store=server({settings:{ac_heating_windows:serverScheduleReordered}});
 const a=client(store);
 
 const localSchedule=[
  {id:'morning',start_minute:480,end_minute:720},
  {id:'evening',start_minute:960,end_minute:1200}
 ];
 a.sync.settings.ac_heating_windows=clone(localSchedule);
 
 const changed=a.sync.update(a.sync.settings,a.sync.quote);
 assert.equal(changed,false);
 assert.equal(a.sync.pending(),false);
 assert.equal(a.sync.dirtySettings.size,0);
 assert.equal(store.writes.length,0);
});

