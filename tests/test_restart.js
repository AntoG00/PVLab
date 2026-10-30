'use strict';
const assert=require('node:assert/strict');
const test=require('node:test');
const {reloadServer,waitForNewServer}=require('../feasibility/web/restart.js');

test('restart saves pending edits, posts restart, waits for a new instance, then reloads',async()=>{
 const calls=[],sync={update:(settings,quote)=>calls.push(['update',settings,quote]),flush:async()=>calls.push(['flush'])};
 await reloadServer({sync,settings:{scop:5},quote:{line_items:[]},request:async(path,body)=>{calls.push(['request',path,body]);return {instance_id:'boot-old'};},onStatus:message=>calls.push(['status',message]),waitForReady:async id=>{calls.push(['ready',id]);},onReload:()=>calls.push(['reload'])});
 assert.deepEqual(calls.filter(call=>['update','flush','request','ready','reload'].includes(call[0])).map(call=>call[0]),['update','flush','request','ready','reload']);
 assert.deepEqual(calls.find(call=>call[0]==='request'),['request','/api/restart',{}]);
 assert.ok(calls.some(call=>call[0]==='ready'&&call[1]==='boot-old'));
});

test('a failed save or readiness check never reloads the page',async()=>{
 let reloads=0,requests=0;
 const sync={update(){},flush:async()=>{throw Error('invalid edit');}};
 await assert.rejects(reloadServer({sync,settings:{},quote:{},request:async()=>{requests++;},onReload:()=>reloads++}),/invalid edit/);
 assert.equal(requests,0);assert.equal(reloads,0);
 sync.flush=async()=>{};
 await assert.rejects(reloadServer({sync,settings:{},quote:{},request:async()=>({instance_id:'old'}),waitForReady:async()=>{throw Error('server unavailable');},onReload:()=>reloads++}),/server unavailable/);
 assert.equal(reloads,0);
});

test('health polling waits for a distinct server instance',async()=>{
 const instances=['boot-old','boot-old','boot-new'];let time=0,calls=0;
 const health=await waitForNewServer('boot-old',{fetchImpl:async()=>({ok:true,json:async()=>({instance_id:instances[calls++]})}),timeoutMs:1000,intervalMs:10,now:()=>time,pause:async ms=>{time+=ms;}});
 assert.equal(health.instance_id,'boot-new');assert.equal(calls,3);
});

test('health polling times out with a retryable error',async()=>{
 let time=0;
 await assert.rejects(waitForNewServer('boot-old',{fetchImpl:async()=>({ok:true,json:async()=>({instance_id:'boot-old'})}),timeoutMs:20,intervalMs:10,now:()=>time,pause:async ms=>{time+=ms;}}),/retry/);
});
