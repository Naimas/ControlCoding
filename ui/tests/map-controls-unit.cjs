'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');
const {PanelState}=require('../panel-state.cjs'),{BridgeClient}=require('../bridge-client.cjs');
const snapshot='snapshot:'+'b'.repeat(64),scopeId='c'.repeat(64);
function fixture(){
 const calls=[];const client={cancel(){},async run(operation,root,options){calls.push({operation,root,options});return {status:'ok',result:
  operation==='map_preview_v1'?{scope:{preview_id:'a'.repeat(64)}}:operation==='map_controls_preview_v1'?{controls_scope:{scope_id:scopeId}}:
  {map:{review:{source_snapshot:snapshot,revision:'absent'},...(operation==='map_controls_read_v1'?{controls:{host_coverage:'unknown'}}:{})}}};}};
 return {client,calls,state:new PanelState(client)};
}
test('controls require explicit preview and privately bind root snapshot revision and scope',async()=>{
 const {state,calls}=fixture();await state.select('root');await state.observeMap('refresh');
 await state.observeControls('read');assert(!calls.some(c=>c.operation.startsWith('map_controls')));
 await state.observeControls('preview');assert.equal(state.value.map,null);assert(state.value.controlsScope);
 assert(!calls.some(c=>c.operation==='map_controls_read_v1'));
 await state.observeControls('read');const call=calls.find(c=>c.operation==='map_controls_read_v1');
 assert.deepEqual([call.root,call.options.snapshot,call.options.revision,call.options.scope_id],['root',snapshot,'absent',scopeId]);
 assert.equal(state.value.map.controls.host_coverage,'unknown');
});
test('refresh design change and root switch invalidate broader read',async()=>{
 for(const action of ['refresh','design','root']){
  const {state,calls}=fixture();await state.select('root');await state.observeMap('refresh');await state.observeControls('preview');
  if(action==='refresh')await state.observeMap('refresh');if(action==='design')state.setDesignPaths(['DESIGN.md']);if(action==='root')await state.select('other');
  await state.observeControls('read');assert(!calls.some(c=>c.operation==='map_controls_read_v1'));assert.equal(state.controlsRequest,null);
 }
});
test('failed or cancelled read removes prior signals and preview',async()=>{
 for(const code of ['cancelled','helper_timeout','stale_snapshot','definition_conflict']){
  const {state,client}=fixture();await state.select('root');await state.observeMap('refresh');await state.observeControls('preview');await state.observeControls('read');
  client.run=async()=>({status:'error',error:{code}});await state.observeControls('read');
  assert.equal(state.value.map,null);assert.equal(state.value.controlsScope,null);assert.equal(state.value.mapError.code,code);
 }
});
test('late controls result cannot cross selected project generation',async()=>{
 const {state,client}=fixture();await state.select('root');await state.observeMap('refresh');await state.observeControls('preview');
 let done;const old=client.run;client.run=(op,...rest)=>op==='map_controls_read_v1'?new Promise(r=>done=r):old(op,...rest);
 const pending=state.observeControls('read');await state.select('other');done({status:'ok',result:{map:{controls:{}}}});await pending;
 assert.equal(state.value.project,'other');assert.equal(state.value.map,null);assert.equal(state.controlsRequest,null);
});
async function transport(envValue,operation='map_controls_preview_v1'){
 const original=process.env.CC_ACTIVE_MODULE;process.env.CC_ACTIVE_MODULE=envValue;
 try {
  const child=new EventEmitter();Object.assign(child,{stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),exitCode:null,kill(){}});
  let spawnOptions,request='';child.stdin.on('data',d=>request+=d);
  const bridge=new BridgeClient(path.resolve('python'),path.resolve('bridge'),{spawn:(_p,_a,o)=>(spawnOptions=o,child)});
  const pending=bridge.run(operation,path.resolve('fixture'),{design_paths:[],observed_at:'2026-09-21T12:00:00Z',preview_id:'a'.repeat(64),snapshot,revision:'absent',scope_id:scopeId,command:'CANARY',token:'CANARY'});
  await new Promise(r=>setImmediate(r));const sent=JSON.parse(request);
  child.stdout.write(JSON.stringify({version:1,id:sent.id,operation,status:'ok',result:{project_root:sent.project_root,controls_scope:{schema_version:1}}}));child.exitCode=0;child.emit('close',0);
  return {sent,spawnOptions,response:await pending};
 } finally {if(original===undefined)delete process.env.CC_ACTIVE_MODULE;else process.env.CC_ACTIVE_MODULE=original;}
}
test('typed transport omits commands secrets and unapproved environment',async()=>{
 const {sent,spawnOptions,response}=await transport(' api ');
 assert.equal(spawnOptions.env.CC_ACTIVE_MODULE,'api');assert(!('PATH' in spawnOptions.env));
 assert(!('command' in sent)&&!('token' in sent));assert.equal(spawnOptions.shell,false);
 assert.equal(response.error.code,'invalid_helper_response');
});
test('invalid process context is represented without leaking its value',async()=>{
 const {spawnOptions}=await transport('CANARY secret');assert.equal(spawnOptions.env.CC_ACTIVE_MODULE,'?');
 const ordinary=await transport('api','map_read_v1');assert(!('CC_ACTIVE_MODULE' in ordinary.spawnOptions.env));
});
test('controls reject missing scope and malformed revision before spawning',async()=>{
 const bridge=new BridgeClient(path.resolve('python'),path.resolve('bridge'),{spawn:()=>assert.fail('unexpected spawn')});
 const options={design_paths:[],observed_at:'2026-09-21T12:00:00Z',preview_id:'a'.repeat(64),snapshot,revision:'absent'};
 assert.equal((await bridge.run('map_controls_read_v1',path.resolve('root'),options)).error.code,'invalid_request');
 assert.equal((await bridge.run('map_controls_preview_v1',path.resolve('root'),{...options,revision:true})).error.code,'invalid_request');
});
