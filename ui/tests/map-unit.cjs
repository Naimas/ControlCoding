'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {PassThrough}=require('node:stream');
const path=require('node:path');
const {PanelState,safePreferences}=require('../panel-state.cjs');
const {BridgeClient}=require('../bridge-client.cjs');
const scope={preview_id:'a'.repeat(64),schema_version:1,adapter:'cc-project-map-sources/v1',design_paths:[],limits:{max_entries:5000,file_bytes:1048576,seconds:10}};
const good=(op)=>({status:'ok',observed_at:'now',result:op==='map_preview_v1'?{scope}:{map:{marker:'current'}}});
test('map preview is separate from read and selected documents invalidate map',async()=>{
  const operations=[];
  const state=new PanelState({cancel(){},async run(op,root,options){operations.push({op,root,options});return good(op);}});
  await state.select('first');await state.observeMap('read');assert.equal(operations.length,1);
  await state.observeMap('preview');assert.equal(state.value.map,null);assert.equal(state.value.mapScope,scope);
  await state.observeMap('read');assert.equal(state.value.map.marker,'current');
  assert.equal(operations.at(-1).options.preview_id,scope.preview_id);
  state.setDesignPaths(['design.md']);assert.equal(state.value.map,null);assert.equal(state.value.mapScope,null);
  assert.equal(safePreferences({tab:'Project Map'}).tab,'Project Map');
});
test('map refresh obtains fresh preview, clears immediately, and fails closed',async()=>{
  let fail=false, blocked;
  const state=new PanelState({cancel(){},async run(op){if(fail&&op==='map_read_v1')return new Promise(r=>blocked=r);return good(op);}});
  await state.select('first');await state.observeMap('refresh');fail=true;
  const pending=state.observeMap('refresh');assert.equal(state.value.map,null);assert.equal(state.value.mapObservedAt,null);
  await new Promise(r=>setImmediate(r));blocked({status:'error',error:{code:'scope_limit'}});await pending;
  assert.equal(state.value.map,null);assert.equal(state.value.mapScope,null);assert.equal(state.value.mapError.code,'scope_limit');
});
test('late map replies and pending previews cannot cross project generation',async()=>{
  const pending=[];
  const state=new PanelState({cancel(){},run(op){return op==='read'?Promise.resolve(good(op)):new Promise(r=>pending.push(r));}});
  await state.select('old');const old=state.observeMap('refresh');await state.select('new');
  pending[0](good('map_preview_v1'));await old;
  assert.equal(pending.length,1);assert.equal(state.value.map,null);assert.equal(state.value.mapScope,null);assert.equal(state.value.busy,false);
});
test('cancellation cannot cancel a setup read after a project switch',async()=>{
  let cancellations=0;
  const state=new PanelState({cancel(){cancellations++;},async run(op){return good(op);}});
  await state.select('first');state.mapActive=true;await state.select('second');
  state.value.busy=true;const before=cancellations;state.cancelMap();assert.equal(cancellations,before);
});
test('map transport exceptions clear old state without leaking raw error',async()=>{
  const state=new PanelState({cancel(){},async run(op){if(op.startsWith('map_'))throw Error('SECRET');return good(op);}});
  await state.select('first');await state.observeMap('refresh');
  assert.equal(state.value.busy,false);assert.equal(state.value.mapError.code,'helper_failure');assert(!JSON.stringify(state.value).includes('SECRET'));
});
function child(){const p=new EventEmitter();p.stdin=new PassThrough();p.stdout=new PassThrough();p.stderr=new PassThrough();p.exitCode=null;p.kill=()=>{p.exitCode=-1;queueMicrotask(()=>p.emit('close',-1));};return p;}
const root=path.resolve('fixture'),python=path.resolve('python.exe'),script=path.resolve('cc_panel_bridge.py');
test('new map operation carries only typed scope fields and validates preview',async()=>{
  const process=child(),client=new BridgeClient(python,script,{spawn:()=>process});let request='';process.stdin.on('data',d=>request+=d);
  const result=client.run('map_preview_v1',root,{design_paths:[],observed_at:'2026-09-21T12:00:00Z',command:'SECRET'});
  await new Promise(r=>setImmediate(r));const parsed=JSON.parse(request);assert(!('command' in parsed));
  process.stdout.write(JSON.stringify({version:1,id:parsed.id,operation:parsed.operation,status:'ok',result:{project_root:root,scope}}));process.exitCode=0;process.emit('close',0);
  assert.equal((await result).status,'ok');
});
test('incomplete or demo map response cannot become a real observation',async()=>{
  const process=child(),client=new BridgeClient(python,script,{spawn:()=>process});let request='';process.stdin.on('data',d=>request+=d);
  const result=client.run('map_read_v1',root,{design_paths:[],observed_at:'2026-09-21T12:00:00Z',preview_id:scope.preview_id});
  await new Promise(r=>setImmediate(r));const parsed=JSON.parse(request);
  process.stdout.write(JSON.stringify({version:1,id:parsed.id,operation:parsed.operation,status:'ok',result:{project_root:root,map:{mode:'demo'}}}));process.exitCode=0;process.emit('close',0);
  assert.equal((await result).error.code,'invalid_helper_response');
});
test('invalid map options are rejected without starting a process',async()=>{
  const client=new BridgeClient(python,script,{spawn(){assert.fail('must not spawn');}});
  assert.equal((await client.run('map_read_v1',root,{})).error.code,'invalid_request');
});
