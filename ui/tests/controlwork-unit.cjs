'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PanelState}=require('../panel-state.cjs');
const {BridgeClient,validWorkResult}=require('../bridge-client.cjs');
const hash='a'.repeat(64);
const scope={scope_id:hash,paths:['CONTROLWORK.md'],max_records:96,file_bytes:65536,notice:'Scope'};
const work={schema_version:1,scope_id:hash,snapshot_id:hash,state:'absent',query:'',notices:[],counts:{records:0,bytes:0,directories:0},documents:[],sessions:[],graph:{nodes:[],edges:[],suggestions:[]},packet:null};
test('work transport validates scope and complete result shape',()=>{
 assert(validWorkResult('work_preview_v1',{work_scope:scope},{}));
 assert(validWorkResult('work_read_v1',{work},{scope_id:hash,query:''}));
 for(const patch of [{scope_id:'b'.repeat(64)},{documents:[{}]},{counts:{}},{sessions:[{}]},{graph:{nodes:[{title:'bad'}],edges:[],suggestions:[]}},{packet:{}}])
  assert(!validWorkResult('work_read_v1',{work:{...work,...patch}},{scope_id:hash,query:''}));
});
test('invalid memory query never starts a helper',async()=>{
 let spawns=0;const client=new BridgeClient(process.execPath,__filename,{spawn:()=>spawns++});
 for(const query of [null,'x'.repeat(241),'a\0b'])assert.equal((await client.run('work_read_v1',process.cwd(),{scope_id:hash,query})).error.code,'invalid_request');
 assert.equal(spawns,0);
});
test('work requires preview and drops stale data on failure',async()=>{
 const calls=[];let failure=false;
 const client={cancel(){},async run(op,root,options){calls.push({op,root,options});return failure?{status:'error',error:{code:'invalid_memory'}}:{status:'ok',observed_at:'now',result:op==='work_preview_v1'?{work_scope:scope}:op==='work_read_v1'?{work}:{} };}};
 const state=new PanelState(client);await state.select('/one');
 await state.observeWork('read');assert.equal(calls.length,1);
 await state.observeWork('preview');await state.observeWork('read');assert.equal(state.value.work,work);
 failure=true;await state.observeWork('read','private query');
 assert.equal(state.value.work,null);assert.equal(state.value.workScope,null);
 assert(!JSON.stringify(state.value.activity).includes('private query'));
 state.refresh.reset();
});
test('project switching rejects late memory response',async()=>{
 let release;const client={cancel(){},run(op){if(op==='work_read_v1')return new Promise(r=>release=r);return Promise.resolve({status:'ok',result:op==='work_preview_v1'?{work_scope:scope}:{}});}};
 const state=new PanelState(client);await state.select('/one');await state.observeWork('preview');
 const pending=state.observeWork('read');await state.select('/two');
 release({status:'ok',result:{work},observed_at:'old'});await pending;
 assert.equal(state.value.project,'/two');assert.equal(state.value.work,null);assert.equal(state.value.workScope,null);
 state.refresh.reset();
});
