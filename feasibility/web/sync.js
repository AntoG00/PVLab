'use strict';

// Keep only this tab's edits pending; unrelated changes from other tabs merge in.
class SharedScenarioSync {
 constructor({request,onState=()=>{},onStatus=()=>{}}){
  this.scope=0;this.profileId=null;this.inputRevision=null;this.request=request;this.onState=onState;this.onStatus=onStatus;
  this.revision=null;this.settings={};this.quote={line_items:[]};
  this.dirtySettings=new Map();this.dirtyQuote=new Map();this.sequence=0;
  this.replaceVersion=null;this.saving=null;this.polling=null;this.retryNeeded=false;this.lastPollSucceeded=false;
 }
 setScope(profileId,inputRevision=null){this.scope++;this.profileId=profileId;this.inputRevision=inputRevision;this.revision=null;this.settings={};this.quote={line_items:[]};this.dirtySettings.clear();this.dirtyQuote.clear();this.replaceVersion=null;}
 clone(value){return JSON.parse(JSON.stringify(value));}
 equal(a,b){return SharedScenarioSync.equal(a,b);}
 pending(){return this.replaceVersion!==null||this.dirtySettings.size>0||this.dirtyQuote.size>0;}
 receive(state){
  if(state.unchanged)return false;
  let settings=this.clone(state.settings),quote=this.clone(state.quote);
  if(this.replaceVersion!==null){settings=this.clone(this.settings);quote=this.clone(this.quote);}
  else{
   for(const [key,entry] of this.dirtySettings)settings[key]=this.clone(entry.value);
   for(const [key,entry] of this.dirtyQuote)quote[key]=this.clone(entry.value);
  }
  this.revision=state.revision;this.settings=settings;this.quote=quote;
  this.onState({...state,settings:this.clone(settings),quote:this.clone(quote)});
  this.onStatus(this.pending()?'Changes waiting to save':'Saved across devices');
  return true;
 }
 update(settings,quote,{replace=false}={}){
  let changed=replace;
  const track=(previous,next,dirty)=>{
   for(const key of new Set([...Object.keys(previous),...Object.keys(next)])){
    if(!replace&&this.equal(previous[key],next[key]))continue;
    const value=Object.hasOwn(next,key)?next[key]:null;
    dirty.set(key,{value:this.clone(value),version:++this.sequence});changed=true;
   }
  };
  track(this.settings,settings,this.dirtySettings);track(this.quote,quote,this.dirtyQuote);
  this.settings=this.clone(settings);this.quote=this.clone(quote);
  if(replace)this.replaceVersion=++this.sequence;
  if(changed)this.onStatus('Changes waiting to save');
  return changed;
 }
 async flush(){
  if(this.saving)return this.saving;
  if(!this.pending())return;
  const scope=this.scope;
  const save=async()=>{
   while(this.pending()){
    const sentSettings=new Map(this.dirtySettings),sentQuote=new Map(this.dirtyQuote),replaceVersion=this.replaceVersion;
    const values=entries=>Object.fromEntries([...entries].map(([key,entry])=>[key,this.clone(entry.value)]));
    const body=replaceVersion!==null?{replace:true,settings:this.clone(this.settings),quote:this.clone(this.quote)}:{settings:values(sentSettings),quote:values(sentQuote)};
    this.onStatus('Saving changes…');
    if(this.profileId!==null)body.revision=this.revision;
    const state=await this.request('/api/state',body);
    if(this.scope!==scope)return;
    for(const [key,entry] of sentSettings)if(this.dirtySettings.get(key)?.version===entry.version)this.dirtySettings.delete(key);
    for(const [key,entry] of sentQuote)if(this.dirtyQuote.get(key)?.version===entry.version)this.dirtyQuote.delete(key);
    if(this.replaceVersion===replaceVersion)this.replaceVersion=null;
    this.receive(state);
   }
  };
  this.saving=save();
  try{await this.saving;this.retryNeeded=false;}catch(error){this.retryNeeded=!error.status||error.status>=500;this.onStatus('Changes not saved · '+(error.message||error));throw error;}finally{this.saving=null;}
 }
 async poll(){
  this.lastPollSucceeded=false;
  if(this.saving||this.polling)return false;
  const revision=this.revision,scope=this.scope;
  this.polling=this.request('/api/state'+(revision===null?'':'?revision='+encodeURIComponent(revision)));
  try{
   const state=await this.polling;
   this.lastPollSucceeded=true;
   // A save may finish while an older read is travelling back to this tab.
   if(this.scope!==scope||this.saving||this.revision!==revision)return false;
   const changed=this.receive(state);
   if(!changed)this.onStatus(this.pending()?'Changes waiting to save':'Saved across devices');
   return changed;
  }catch(error){this.onStatus('Sync unavailable · changes kept in this tab');return false;}finally{this.polling=null;}
 }
}
SharedScenarioSync.equal=function equal(a,b){
 if(a===b)return true;
 if(a==null||b==null||typeof a!=='object'||typeof b!=='object')return false;
 if(Array.isArray(a)!==Array.isArray(b))return false;
 if(Array.isArray(a)){
  if(a.length!==b.length)return false;
  for(let i=0;i<a.length;i++){
   if(!equal(a[i],b[i]))return false;
  }
  return true;
 }
 const keysA=Object.keys(a),keysB=Object.keys(b);
 if(keysA.length!==keysB.length)return false;
 for(const k of keysA){
  if(!Object.hasOwn(b,k)||!equal(a[k],b[k]))return false;
 }
 return true;
};
SharedScenarioSync.settingValuesEqual=SharedScenarioSync.equal;
if(typeof window!=='undefined'){
 window.SharedScenarioSync=SharedScenarioSync;
}
if(typeof globalThis!=='undefined'){
 globalThis.SharedScenarioSync=SharedScenarioSync;
}
if(typeof module!=='undefined')module.exports=SharedScenarioSync;



// Capabilities and household state stay in this tab; only display preferences persist.
class HouseholdStorage {
 constructor(persistent){try{this.persistent=persistent===undefined?globalThis.localStorage:persistent;}catch{this.persistent=null;}this.values=new Map();}
 allowed(key){return ['pv_lab_theme','theme','pv-lab-theme'].includes(key)||/(?:^|-)(?:theme|summary-prefs|cash-curve-prefs)-v1$/.test(key);}
 getItem(key){try{return this.allowed(key)?this.persistent.getItem(key):(this.values.get(key)??null);}catch{return null;}}
 setItem(key,value){if(this.allowed(key)){try{this.persistent.setItem(key,value);}catch{}}else this.values.set(key,String(value));}
 removeItem(key){this.values.delete(key);if(this.allowed(key)){try{this.persistent.removeItem(key);}catch{}}}
 clearPrivate(){this.values.clear();}
}
class HouseholdClient {
 constructor({fetch:fetcher=globalThis.fetch.bind(globalThis),onClear=()=>{},onStatus=()=>{}}={}){
  this.fetcher=fetcher;this.onClear=onClear;this.onStatus=onStatus;this.csrf=null;this.sessionPending=null;this.profileId=null;this.revision=null;this.ready=false;this.generation=0;this.controllers=new Set();
 }
 clear(){this.generation++;for(const c of this.controllers)c.abort();this.controllers.clear();this.profileId=null;this.revision=null;this.ready=false;this.onClear();}
 invalidate(){this.generation++;for(const c of this.controllers)c.abort();this.controllers.clear();this.ready=false;}
 select(id,revision=null){this.clear();this.profileId=id;this.revision=revision;}
 stale(){return Object.assign(Error('STALE_RESPONSE: profile changed'),{code:'STALE_RESPONSE'});}
 async session(){
  if(this.csrf)return;
  if(this.sessionPending)return this.sessionPending;
  this.sessionPending=(async()=>{
   let r=await this.fetcher('/api/session',{credentials:'same-origin'});
   if(r.status===401)r=await this.fetcher('/api/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});
   const data=await r.json();if(!r.ok)throw Object.assign(Error(data.error||'Session unavailable'),{status:r.status});this.csrf=data.csrf_token;
  })();
  try{await this.sessionPending;}finally{this.sessionPending=null;}
 }
 async request(path,body,options={}){
  const generation=this.generation;
  await this.session();if(generation!==this.generation)throw this.stale();
  const controller=new AbortController();this.controllers.add(controller);
  const method=options.method||(body===undefined?'GET':'POST');
  const headers={...(options.raw?{}:{'Content-Type':'application/json'}),...options.headers};
  if(method!=='GET'){headers['X-CSRF-Token']=this.csrf;if(this.revision)headers['If-Match']=this.revision;}
  try{
   const r=await this.fetcher(path,{method,credentials:'same-origin',headers,signal:controller.signal,...(body===undefined?{}:{body:options.raw?body:JSON.stringify(body)})});
   const value=await r.json();if(generation!==this.generation)throw this.stale();
   if(!r.ok){
    const labels={401:'Session expired. Reconnect to continue.',409:'Inputs changed. Reload this profile before retrying.',413:'File too large. CSV limit is 16 MiB.',422:'Correct the input fields.',429:'Server busy. Wait before retrying.'};
    if(r.status===401){this.csrf=null;this.clear();}else if(r.status===409){this.ready=false;this.onClear();}
    const error=Object.assign(Error(labels[r.status]||value.error||'Request failed'),{status:r.status,diagnostic:value.diagnostic,retryAfter:r.headers?.get?.('Retry-After')});this.onStatus(error.message);throw error;
   }
   return value;
  }finally{this.controllers.delete(controller);}
 }
 async profileRequest(suffix,body,options={}){
  if(!this.profileId)throw Error('Select a profile first');
  const id=this.profileId,revision=this.revision;
  const result=await this.request('/api/profiles/'+encodeURIComponent(id)+suffix,body,options);
  if(this.profileId!==id||this.revision!==revision)throw this.stale();
  if(result.revision)this.revision=result.revision;
  return result;
 }
 async runComputation(generation,submit){
  if((this.queuedComputations||0)>=8)throw Object.assign(Error('Compute queue full. Retry after current jobs finish.'),{status:429});
  const revision=this.revision,previous=this.computeTail||Promise.resolve();
  this.queuedComputations=(this.queuedComputations||0)+1;
  const task=previous.catch(()=>{}).then(()=>{
   if(generation!==this.generation||revision!==this.revision)throw this.stale();
   return submit();
  });
  this.computeTail=task.catch(()=>{});
  try{return await task;}finally{this.queuedComputations--;}
 }
 async waitJob(id,{generation=this.generation,onProgress=()=>{}}={}){
  const profileId=this.profileId,revision=this.revision;
  try{for(;;){
   if(generation!==this.generation||profileId!==this.profileId||revision!==this.revision)throw this.stale();
   const job=await this.request('/api/jobs/'+encodeURIComponent(id));
   if(job.id!==id||job.profile_id!==profileId||job.revision!==revision)throw this.stale();
   if(['queued','running'].includes(job.state))this.pendingJob={id,profileId,revision};
   onProgress(job.state);
   if(job.state==='complete')return job;
   if(!['queued','running'].includes(job.state)){
    if(job.error_code==='SOLAR_TIMEOUT')throw Object.assign(Error('SOLAR_TIMEOUT: Solar data request timed out; retry later.'),{code:'SOLAR_TIMEOUT'});
    if(job.error_code==='SOLAR_NETWORK_ERROR')throw Object.assign(Error('SOLAR_NETWORK_ERROR: Solar data request network error; retry later.'),{code:'SOLAR_NETWORK_ERROR'});
    if(job.error_code==='SOLAR_DATA_GAPS')throw Object.assign(Error('SOLAR_DATA_GAPS: PVGIS data gaps exceed the supported repair limits. Review the selected weather years; no years were omitted.'),{code:'SOLAR_DATA_GAPS'});
    throw Error('JOB_FAILED: job '+job.state+'; review inputs and retry.');
   }
   await new Promise(resolve=>setTimeout(resolve,2000));
  }}finally{if(this.pendingJob?.id===id)this.pendingJob=null;}
 }
 async cancelJob(){
  const job=this.pendingJob;if(!job)return;
  if(job.profileId!==this.profileId||job.revision!==this.revision)throw this.stale();
  await this.request('/api/jobs/'+encodeURIComponent(job.id),{},{method:'DELETE'});
 }
 async deleteProfile(){const id=this.profileId;await this.request('/api/profiles/delete',{profile_id:id});this.clear();}
 async deleteHousehold(){await this.request('/api/tenant/delete',{});this.csrf=null;this.clear();}
}
Object.assign(globalThis,{HouseholdClient,HouseholdStorage});
if(typeof module!=='undefined')Object.assign(module.exports,{HouseholdClient,HouseholdStorage});
