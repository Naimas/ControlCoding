'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream'),{spawn,spawnSync}=require('node:child_process');
const {PanelState}=require('../panel-state.cjs'),{BridgeClient}=require('../bridge-client.cjs');
const snapshot='snapshot:'+'b'.repeat(64),scopeId='c'.repeat(64);
function fixture(){
 const calls=[];const client={cancel(){},async run(operation,root,options){calls.push({operation,root,options});return {status:'ok',result:
  operation==='map_preview_v1'?{scope:{preview_id:'a'.repeat(64)}}:operation==='map_analysis_preview_v1'?{analysis_scope:{scope_id:scopeId}}:
  operation==='map_controls_preview_v1'?{controls_scope:{scope_id:'d'.repeat(64)}}:
  {map:{review:{source_snapshot:snapshot,revision:'absent'},...(operation==='map_controls_read_v1'?{controls:{host_coverage:'unknown'}}:{}),...(operation==='map_analysis_read_v1'?{analysis:{coverage:'partial'}}:{})}}};}};
 return {client,calls,state:new PanelState(client)};
}
test('analysis requires explicit preview and keeps source revision and optional controls private',async()=>{
 const {state,calls}=fixture();await state.select('root');await state.observeMap('refresh');await state.observeAnalysis('read');
 assert(!calls.some(c=>c.operation.startsWith('map_analysis')));
 await state.observeControls('preview');await state.observeControls('read');await state.observeAnalysis('preview');
 assert.equal(state.value.map,null);assert(state.value.analysisScope);
 await state.observeAnalysis('read');const call=calls.find(c=>c.operation==='map_analysis_read_v1');
 assert.deepEqual([call.root,call.options.snapshot,call.options.revision,call.options.scope_id,call.options.controls_scope_id],['root',snapshot,'absent',scopeId,'d'.repeat(64)]);
 assert.equal(state.value.map.analysis.coverage,'partial');
});
test('normal refresh document change project switch and controls replacement invalidate analysis',async()=>{
 for(const action of ['refresh','design','root','controls']){
  const {state,calls}=fixture();await state.select('root');await state.observeMap('refresh');await state.observeAnalysis('preview');await state.observeAnalysis('read');
  if(action==='refresh')await state.observeMap('refresh');if(action==='design')state.setDesignPaths(['DESIGN.md']);if(action==='root')await state.select('other');if(action==='controls')await state.observeControls('preview');
  const count=calls.length;await state.observeAnalysis('read');assert.equal(calls.length,count);assert.equal(state.analysisRequest,null);assert.equal(state.value.analysisScope,null);
 }
});
test('failure cancellation and late generations never retain old analysis',async()=>{
 const {state,client}=fixture();await state.select('root');await state.observeMap('refresh');await state.observeAnalysis('preview');await state.observeAnalysis('read');
 const original=client.run;client.run=async()=>({status:'error',error:{code:'cancelled'}});await state.observeAnalysis('read');
 assert.equal(state.value.map,null);assert.equal(state.analysisRequest,null);assert.equal(state.value.mapError.code,'cancelled');
 client.run=original;await state.observeMap('refresh');await state.observeAnalysis('preview');let done;
 client.run=(op,...rest)=>op==='map_analysis_read_v1'?new Promise(r=>done=r):original(op,...rest);
 const pending=state.observeAnalysis('read');await state.select('other');done({status:'ok',result:{map:{analysis:{}}}});await pending;
 assert.equal(state.value.map,null);assert.equal(state.value.project,'other');
});
test('analysis bridge uses only launcher runtime arguments and typed request fields',async()=>{
 const child=new EventEmitter();Object.assign(child,{stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),exitCode:null,kill(){}});
 let argv,request='';child.stdin.on('data',d=>request+=d);
 const runtime=[path.resolve('trusted-node'),path.resolve('trusted-worker')];
 const client=new BridgeClient(path.resolve('python'),path.resolve('bridge'),{analysisRuntime:runtime,spawn:(_p,a)=>(argv=a,child)});
 const pending=client.run('map_analysis_preview_v1',path.resolve('fixture'),{design_paths:[],observed_at:'2026-09-21T12:00:00Z',preview_id:'a'.repeat(64),snapshot,revision:'absent',controls_scope_id:null,node:'FOREIGN',worker:'FOREIGN',command:'CANARY'});
 await new Promise(r=>setImmediate(r));const sent=JSON.parse(request);
 assert.deepEqual(argv.slice(-4),['--analysis-node',runtime[0],'--analysis-worker',runtime[1]]);assert(!request.includes('FOREIGN')&&!request.includes('CANARY'));
 child.stdout.write(JSON.stringify({version:1,id:sent.id,operation:sent.operation,status:'ok',result:{project_root:sent.project_root,analysis_scope:{}}}));child.exitCode=0;child.emit('close',0);
 assert.equal((await pending).error.code,'invalid_helper_response');
});
test('unsupported analyzer runtime and invalid bindings fail before spawn',async()=>{
 assert.throws(()=>new BridgeClient(path.resolve('python'),path.resolve('bridge'),{analysisRuntime:['relative','relative']}));
 const client=new BridgeClient(path.resolve('python'),path.resolve('bridge'),{spawn:()=>assert.fail('must not spawn')});
 const opts={design_paths:[],observed_at:'2026-09-21T12:00:00Z',preview_id:'a'.repeat(64),snapshot,revision:'absent',controls_scope_id:null};
 assert.equal((await client.run('map_analysis_read_v1',path.resolve('root'),opts)).error.code,'invalid_request');
 assert.equal((await client.run('map_analysis_preview_v1',path.resolve('root'),{...opts,controls_scope_id:false})).error.code,'invalid_request');
});
const runtime=process.env.CC_MAP_TEST_NODE,worker=process.env.CC_MAP_TEST_WORKER;
function parse(files){const result=spawnSync(runtime,[worker],{input:JSON.stringify({files}),encoding:'utf8',timeout:7000,maxBuffer:1048576});assert.equal(result.status,0);assert.equal(result.stderr,'');return JSON.parse(result.stdout);}
test('real worker parses TypeScript and JSX without executing input', {skip:!runtime||!worker},()=>{
 const result=parse([{path:'a.tsx',text:'throw Error("CANARY"); export const view=(n:number)=>n?<div/>:<span/>;'}]);
 assert.equal(result.files[0].state,'parsed');assert.equal(result.files[0].symbols[0].branches,2);assert(!JSON.stringify(result).includes('CANARY'));
});
test('real worker input and AST budgets fail explicitly', {skip:!runtime||!worker},()=>{
 assert.equal(parse(Array.from({length:129},(_,i)=>({path:`f${i}.js`,text:'pass'}))).error,'parser_input');
 assert.equal(parse([{path:'a.js',text:'const a=['+'1,'.repeat(22000)+'];'}]).error,'parse_limit');
});
test('worker deadline terminates even if input never closes', {skip:!runtime||!worker,timeout:7000},async()=>{
 const child=spawn(runtime,[worker],{stdio:['pipe','pipe','pipe']});let text='';child.stdout.on('data',d=>text+=d);
 const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',resolve);});
 assert.equal(code,0);assert.equal(JSON.parse(text).error,'parser_timeout');
});
