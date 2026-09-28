'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PanelState}=require('../panel-state.cjs');
const {validChange,BridgeClient}=require('../bridge-client.cjs');
const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream'),path=require('node:path');
const preview={approval_id:'b'.repeat(64),write_supported:true,changes:[]};
function fixture(){
 const calls=[];const client={cancel(){calls.push('cancel');},async run(operation,root,options){calls.push({operation,root,options});
  return {status:'ok',observed_at:'now',result:operation==='map_preview_v1'?{scope:{preview_id:'a'.repeat(64)}}:
   operation==='map_review_preview_v1'?{review_preview:preview}:operation==='map_review_apply_v1'?{review_saved:{saved:true,revision:'c'.repeat(64)}}:
   {map:{review:{revision:'absent',source_snapshot:'snapshot:'+'d'.repeat(64),entries:[]}}}};
 }};
 return {calls,client,state:new PanelState(client)};
}
test('review binds main-owned root snapshot and revision; save uses private preview then refreshes',async()=>{
 const {state,calls}=fixture();await state.select('root');await state.observeMap('refresh');
 const change={operation:'accept',target:'node:a'};await state.review(change);change.target='node:foreign';
 assert.equal(state.value.reviewPreview,preview);
 assert(!calls.some(c=>c.operation==='map_review_apply_v1'));
 await state.saveReview();const applied=calls.find(c=>c.operation==='map_review_apply_v1');
 assert.equal(applied.root,'root');assert.equal(applied.options.change.target,'node:a');assert.equal(applied.options.approval_id,preview.approval_id);
 assert.equal(state.value.reviewPreview,null);assert(state.value.reviewSaved.saved);assert(state.value.map);
 await state.saveReview();assert.equal(calls.filter(c=>c.operation==='map_review_apply_v1').length,1);
});
test('discard, refresh and project switch invalidate pending save',async()=>{
 for(const action of ['discard','refresh','switch']){
  const {state,calls}=fixture();await state.select('root');await state.observeMap('refresh');await state.review({operation:'reject',target:'node:a'});
  if(action==='discard')state.discardReview();else if(action==='refresh')await state.observeMap('refresh');else await state.select('other');
  await state.saveReview();assert(!calls.some(c=>c.operation==='map_review_apply_v1'));
 }
});
test('failed confirmation clears current observation, never retries, preserves safe error',async()=>{
 const {state,client,calls}=fixture();await state.select('root');await state.observeMap('refresh');await state.review({operation:'accept',target:'node:a'});
 client.run=async()=>({status:'error',error:{code:'definition_conflict'}});
 await state.saveReview();assert.equal(state.value.map,null);assert.equal(state.value.reviewError.code,'definition_conflict');assert.equal(state.value.committing,false);
});
test('commit prevents cancellation and project switch until result; source failure after save retains receipt',async()=>{
 const {state,client,calls}=fixture();await state.select('root');await state.observeMap('refresh');await state.review({operation:'accept',target:'node:a'});
 let done;client.run=(op)=>op==='map_review_apply_v1'?new Promise(r=>done=r):Promise.resolve({status:'error',error:{code:'scope_limit'}});
 const pending=state.saveReview();assert(state.value.committing);const count=calls.length;
 state.cancelMap();await state.select('other');assert.equal(state.value.project,'root');assert.equal(calls.length,count);
 done({status:'ok',result:{review_saved:{saved:true}}});await pending;
 assert.equal(state.value.map,null);assert(state.value.reviewSaved.saved);assert.equal(state.value.mapError.code,'scope_limit');
});
test('late preview cannot cross project generations',async()=>{
 const {state,client}=fixture();await state.select('root');await state.observeMap('refresh');
 let done;const run=client.run;client.run=(op,...rest)=>op==='map_review_preview_v1'?new Promise(r=>done=r):run(op,...rest);
 const pending=state.review({operation:'accept',target:'node:a'});await state.select('other');done({status:'ok',result:{review_preview:preview}});await pending;
 assert.equal(state.value.reviewPreview,null);assert.equal(state.reviewRequest,null);
});
test('mapping schema excludes paths commands permissions and excessive partitions',()=>{
 for(const c of [{operation:'accept',target:'x',root:'elsewhere'},{operation:'unlock',target:'x'},
  {operation:'rename',target:'x',title:'\n'},{operation:'alias',target:'x',replacement:'../x'},
  {operation:'split',target:'x',groups:Array(9).fill({title:'x',members:['a']})}])assert.equal(validChange(c),false);
 assert(validChange({operation:'group',title:'Service',members:['a','b']}));
});
test('bridge maps preview fields without forwarding arbitrary extras and rejects invalid reply shape',async()=>{
 const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.kill=()=>{};
 const client=new BridgeClient(path.resolve('python'),path.resolve('bridge'),{spawn:()=>child});let request='';child.stdin.on('data',d=>request+=d);
 const pending=client.run('map_review_preview_v1',path.resolve('fixture'),{design_paths:[],observed_at:'2026-09-21T12:00:00Z',preview_id:'a'.repeat(64),snapshot:'snapshot:'+'b'.repeat(64),revision:'absent',change:{operation:'accept',target:'node:a'},command:'SECRET'});
 await new Promise(r=>setImmediate(r));const sent=JSON.parse(request);assert(!('command' in sent));assert.equal(sent.change.target,'node:a');
 child.stdout.write(JSON.stringify({version:1,id:sent.id,operation:sent.operation,status:'ok',result:{project_root:sent.project_root,review_preview:{...preview,changes:[{after:{}}]}}}));child.exitCode=0;child.emit('close',0);
 assert.equal((await pending).error.code,'invalid_helper_response');
});
