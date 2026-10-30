'use strict';

async function waitForNewServer(previousInstanceId,{fetchImpl=fetch,timeoutMs=30000,intervalMs=350,now=()=>Date.now(),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
 const deadline=now()+timeoutMs;
 while(now()<deadline){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
  try{
   const response=await fetchImpl('/api/health',{cache:'no-store',signal:controller.signal});
   if(response.ok){const health=await response.json();if(health.instance_id&&health.instance_id!==previousInstanceId)return health;}
  }catch{}
  finally{clearTimeout(timer);}
  const remaining=deadline-now();if(remaining>0)await pause(Math.min(intervalMs,remaining));
 }
 throw Error('The server did not finish reloading within 30 seconds. You can retry.');
}

async function reloadServer({sync,settings,quote,request,onStatus=()=>{},waitForReady=waitForNewServer,onReload=()=>{}}){
 sync.update(settings,quote);
 onStatus('Saving pending changes…');
 await sync.flush();
 onStatus('Asking the server to reload…');
 const result=await request('/api/restart',{});
 onStatus('Server is reloading… waiting for it to return.');
 await waitForReady(result.instance_id);
 onStatus('Server is ready. Loading the updated app…');
 onReload();
}

if(typeof window!=='undefined')window.ServerReload={reloadServer,waitForNewServer};
if(typeof module!=='undefined')module.exports={reloadServer,waitForNewServer};
