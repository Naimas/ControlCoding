'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PanelState}=require('../panel-state.cjs');
const {scopeKey,MAX_HISTORY}=require('../map-refresh.cjs');
const {DIRECTORIES}=require('../map-watch.cjs');
const tick=()=>new Promise(r=>setImmediate(r));
function map(version=1,ids=['a','b']){
 return {root_identity:'root',review:{source_snapshot:'snapshot:'+version,revision:'absent'},
  projection:{bundle:{nodes:ids.map(id=>({id,title:id,sources:[id]})),sources:ids.map(id=>({id,hash:id==='a'?version:0})),edges:[],constraints:[]},statuses:[]}};
}
async function fixture(){
 let now=100000,identity='root',version=1;const timers=new Map(),calls=[],watchers=[];let serial=0;
 const client={cancel(){},async run(op,root,options){calls.push({op,root,options});return {status:'ok',observed_at:'2026-09-21T00:00:00Z',result:
  op==='map_preview_v1'?{scope:{preview_id:'preview'}}:op==='map_controls_preview_v1'?{controls_scope:{scope_id:'controls',files:['fixed'],snapshot:'new',revision:'absent'}}:
  op==='map_analysis_preview_v1'?{analysis_scope:{scope_id:'analysis',configuration:{reports:[]},snapshot:'new',revision:'absent',includes_controls:!!options.controls_scope_id}}:
  {map:{...map(version),...(op==='map_controls_read_v1'?{controls:{}}:{}),...(op==='map_analysis_read_v1'?{analysis:{findings:[]},...(options.controls_scope_id?{controls:{}}:{})}:{})}}};}};
 const state=new PanelState(client,()=>{},{identity:()=>identity,now:()=>now,
  setTimer:(fn,delay)=>{const id=++serial;timers.set(id,{fn,at:now+delay});return id;},clearTimer:id=>timers.delete(id),
  watch:(_root,fn)=>{const w={fn,count:1,closed:false,close(){this.closed=true;}};watchers.push(w);return w;}});
 const advance=async ms=>{now+=ms;for(const [id,t]of [...timers])if(t.at<=now){timers.delete(id);t.fn();}for(let i=0;i<15;i++)await tick();};
 await state.select('root');await state.observeMap('refresh');state.refresh.toggle();
 return {state,client,calls,timers,watchers,advance,setIdentity:v=>identity=v,setVersion:v=>version=v};
}
test('automatic refresh is opt-in, nonpersistent and has bounded hint subscriptions',async()=>{
 const s=new PanelState({cancel(){},run(){assert.fail('no automatic read');}});assert.equal(s.value.mapRefresh.enabled,false);s.refresh.toggle();assert.equal(s.refresh.enabled,false);
 assert.equal(DIRECTORIES.length,10);assert(DIRECTORIES.every(p=>!p.includes('**')));
});
test('thousands of hints coalesce; minimum interval and one pending job bound churn',async()=>{
 const f=await fixture();const before=f.calls.length;
 for(let i=0;i<10000;i++)f.watchers[0].fn('filesystem');
 assert.equal(f.state.value.map,null);assert.equal(f.timers.size,2);
 await f.advance(750);assert.equal(f.calls.length-before,2);assert(f.state.value.map);assert.equal(f.state.value.mapHistory.length,2);
 f.state.refresh.invalidate('filesystem');await f.advance(750);assert.equal(f.calls.length-before,2);await f.advance(2250);assert.equal(f.calls.length-before,4);
 f.state.refresh.dispose();assert.equal(f.timers.size,0);assert(f.watchers[0].closed);
});
test('periodic reconciliation refreshes missed deep events without a watcher signal',async()=>{
 const f=await fixture();f.setVersion(2);await f.advance(30000);assert.equal(f.state.value.map,null);await f.advance(750);
 assert.equal(f.state.value.map.review.source_snapshot,'snapshot:2');assert.equal(f.state.value.mapHistory[0].reason,'reconciliation');
 assert.equal(f.state.value.mapHistory[0].changed,1);f.state.refresh.dispose();
});
test('no unchanged element is invalidated by observation timestamps alone',async()=>{
 const f=await fixture();f.state.refresh.invalidate('git_metadata');await f.advance(750);
 assert.equal(f.state.value.mapHistory[0].changed,0);assert.equal(f.state.value.mapHistory[0].added,0);f.state.refresh.dispose();
});
test('mid-scan changes cancel the owned helper and discard late valid replies',async()=>{
 const f=await fixture();let complete,cancel=0;const run=f.client.run;
 f.client.cancel=()=>cancel++;f.client.run=(op,...args)=>op==='map_read_v1'?new Promise(r=>complete=r):run(op,...args);
 f.state.refresh.invalidate('filesystem');await f.advance(750);assert(f.state.value.busy);
 f.state.refresh.invalidate('filesystem');assert.equal(cancel,1);assert.equal(f.state.value.map,null);
 complete({status:'ok',result:{map:map(99)}});await tick();assert.equal(f.state.value.map,null);assert.equal(f.state.value.mapHistory.length,1);
 f.client.run=run;await f.advance(3000);assert(f.state.value.map);f.state.refresh.dispose();
});
test('switch during a scan cannot leak old results, authorization, history or timers',async()=>{
 const f=await fixture();let complete;const run=f.client.run;
 f.client.run=(op,...args)=>op==='map_read_v1'?new Promise(r=>complete=r):run(op,...args);
 f.state.refresh.invalidate('filesystem');await f.advance(750);await f.state.select('other');
 complete({status:'ok',result:{map:map(99)}});await tick();
 assert.equal(f.state.value.project,'other');assert.equal(f.state.value.map,null);assert.equal(f.state.value.mapHistory.length,0);assert.equal(f.timers.size,0);assert(!f.state.refresh.running);
});
test('root replacement revokes scopes and clears history before further reads',async()=>{
 const f=await fixture(),before=f.calls.length;f.setIdentity('replacement');f.state.refresh.invalidate('filesystem');await f.advance(30000);
 assert.equal(f.calls.length,before);assert.equal(f.state.value.map,null);assert.equal(f.state.value.mapError.code,'root_changed');assert.equal(f.state.value.mapHistory.length,0);assert(!f.state.refresh.enabled);
});
test('root replacement discovered at scan completion cannot publish',async()=>{
 const f=await fixture();const run=f.client.run;f.client.run=async(...args)=>{const result=await run(...args);if(args[0]==='map_read_v1')f.setIdentity('replacement');return result;};
 f.state.refresh.invalidate('filesystem');await f.advance(750);assert.equal(f.state.value.map,null);assert.equal(f.state.value.mapError.code,'root_changed');assert(!f.state.refresh.enabled);
});
test('failed bounded read retains historical summary but never a current old map',async()=>{
 const f=await fixture();f.client.run=async()=>({status:'error',error:{code:'scope_limit'}});
 f.state.refresh.invalidate('filesystem');await f.advance(750);assert.equal(f.state.value.map,null);assert.equal(f.state.value.mapRefresh.status,'unavailable');assert.equal(f.state.value.mapHistory.length,1);f.state.refresh.dispose();
});
test('only explicitly read overlays recur and changed scope stops a broader read',async()=>{
 const f=await fixture();await f.state.observeControls('preview');await f.state.observeControls('read');await f.state.observeAnalysis('preview');await f.state.observeAnalysis('read');
 f.state.refresh.invalidate('filesystem');await f.advance(750);assert(f.state.value.map.controls);assert(f.state.value.map.analysis);
 const run=f.client.run;f.client.run=async(...args)=>{const result=await run(...args);if(args[0]==='map_analysis_preview_v1')result.result.analysis_scope.configuration.reports=['broader.xml'];return result;};
 const reads=f.calls.filter(c=>c.op==='map_analysis_read_v1').length;
 f.state.refresh.invalidate('filesystem');await f.advance(3000);
 assert.equal(f.calls.filter(c=>c.op==='map_analysis_read_v1').length,reads);assert(f.state.value.map.controls);assert(!f.state.value.map.analysis);assert.equal(f.state.value.mapRefresh.scopeChanged,'analysis');f.state.refresh.dispose();
});
test('preview alone does not authorize repeated controls or analysis',async()=>{
 const f=await fixture();await f.state.observeControls('preview');const before=f.calls.length;
 f.state.refresh.invalidate('filesystem');await f.advance(750);
 assert.deepEqual(f.calls.slice(before).map(c=>c.op),['map_preview_v1','map_read_v1']);f.state.refresh.dispose();
});
test('scope comparison binds report paths contracts limits parser and configuration',()=>{
 const s={snapshot:'old',revision:'r',scope_id:'id',files:['a'],limits:{x:1}};
 assert.equal(scopeKey(s),scopeKey({...s,snapshot:'new',revision:'new',scope_id:'new'}));
 assert.notEqual(scopeKey(s),scopeKey({...s,files:['b']}));assert.notEqual(scopeKey(s),scopeKey({...s,limits:{x:2}}));
});
test('history and navigation are capped while surviving node layout remains stable',async()=>{
 const f=await fixture();f.state.refresh.stop();
 for(let i=0;i<30;i++)f.state.refresh.remember(map(i,i===29?['b','a','c']:['a','b']));
 assert.equal(f.state.refresh.history.length,MAX_HISTORY);assert.deepEqual(f.state.refresh.order,['a','b','c']);
 const large=map(31,Array.from({length:2000},(_,i)=>'node'+i));f.state.refresh.remember(large);
 assert.equal(f.state.refresh.history[0].changedIds.length,100);assert(f.state.refresh.history[0].truncated);assert.equal(f.state.refresh.previous.nodes.size,2000);
});
test('mapping revisions are recorded and selected document changes revoke auto scope',async()=>{
 const f=await fixture();const next=map();next.review.revision='reviewed';f.state.refresh.remember(next);
 assert.equal(f.state.refresh.history[0].previousRevision,'absent');assert.equal(f.state.refresh.history[0].revision,'reviewed');
 f.state.setDesignPaths(['design.md']);assert.equal(f.timers.size,0);assert.equal(f.state.value.mapHistory.length,0);assert(!f.state.refresh.enabled);
});
test('pause cancels in-flight auto chain and prevents another step',async()=>{
 const f=await fixture();let complete;const run=f.client.run;
 f.client.run=(op,...args)=>op==='map_preview_v1'?new Promise(r=>complete=r):run(op,...args);
 f.state.refresh.invalidate('filesystem');await f.advance(750);const count=f.calls.length;
 f.state.refresh.toggle();complete({status:'ok',result:{scope:{preview_id:'late'}}});await tick();
 assert.equal(f.calls.length,count);assert.equal(f.state.value.map,null);assert.equal(f.timers.size,0);
});
test('periodic reconciliation does not starve an in-flight validated scan',async()=>{
 const f=await fixture();let complete,cancel=0;const run=f.client.run;
 f.client.cancel=()=>cancel++;f.client.run=(op,...args)=>op==='map_read_v1'?new Promise(r=>complete=r):run(op,...args);
 f.state.refresh.invalidate('filesystem');await f.advance(750);const epoch=f.state.refresh.epoch;
 await f.advance(30000);assert.equal(cancel,0);assert.equal(f.state.refresh.epoch,epoch);assert(f.state.refresh.pending);
 complete({status:'ok',result:{map:map()}});await tick();assert(f.state.value.map);f.state.refresh.dispose();
});
test('root replacement during mapping apply fences the result without killing the transaction',async()=>{
 const f=await fixture();let complete,cancellations=0;f.client.cancel=()=>cancellations++;
 f.client.run=()=>new Promise(r=>complete=r);
 const pending=f.state.runReview('map_review_apply_v1',{});assert(f.state.value.committing);
 f.setIdentity('replacement');f.state.refresh.invalidate('filesystem');assert.equal(cancellations,0);assert(f.state.value.committing);
 complete({status:'ok',result:{review_saved:{revision:'old-root'}}});await pending;
 assert.equal(f.state.value.map,null);assert.equal(f.state.value.reviewSaved,null);assert(!f.state.value.busy);assert(!f.state.value.committing);
});
test('late manual preview cannot restore approval after an invalidation',async()=>{
 const f=await fixture();let complete;f.client.run=()=>new Promise(r=>complete=r);
 const pending=f.state.runReview('map_review_preview_v1',{});f.state.refresh.invalidate('filesystem');
 complete({status:'ok',result:{review_preview:{approval_id:'old'}}});await pending;
 assert.equal(f.state.value.reviewPreview,null);assert.equal(f.state.reviewRequest,null);f.state.refresh.dispose();
});
test('coalesced directory and periodic hints do not erase a Git metadata reason',async()=>{
 const f=await fixture();for(const reason of ['filesystem','git_metadata','filesystem','reconciliation'])f.state.refresh.invalidate(reason);
 await f.advance(750);assert.equal(f.state.value.mapHistory[0].reason,'git_metadata');f.state.refresh.dispose();
});
