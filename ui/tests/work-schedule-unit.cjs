'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {validRequest,validResult}=require('../knowledge-contract.cjs');
const {KnowledgeState}=require('../knowledge-state.cjs');
const plan={snapshot:'a'.repeat(64),start_date:null,complete:true,critical_available:false,duration_days:null,nodes:[],dependencies:[],hierarchy:[],waves:[],warnings:[],notice:'Observed work'};
const status={schema_version:1,enabled:true,generation:1,policy:{automatic:true,worker:false,retention:'none',embedding:''},counts:{chunks:0,embedded:0}};
function setup(handler){const calls=[],store={generation:1,value:{project:'C:/fixture',busy:false},publish(){}};
 const client={cancel(){},async run(_op,_root,{action,value}){calls.push(action);return {status:'ok',result:{knowledge:action==='graph-view'?{sources:[],wiki:[],edges:[],conversations:[],graph:{topic:null,query:'',focus:null,snapshot:'a'.repeat(64),offset:0,total:0,source_total:0,external_edges:0,next_offset:null,window_size:200,topics:[]}}:await handler(action,value)}};}};
 const memory=new KnowledgeState(store,client);store.value.knowledge=status;return {memory,store,calls};}
test('schedule request limits reject invalid estimates and routing',()=>{
 const value={snapshot:plan.snapshot,start_date:'2026-10-01',task:{id:'source:a',revision:'b'.repeat(64),duration_days:2,buffer_days:1,earliest_start:null,deadline:'2026-10-09'},reason:'Reviewed estimates'};
 assert(validRequest({action:'work-schedule-view',value:null}));assert(!validRequest({action:'work-schedule-view',value:{}}));
 assert(validRequest({action:'work-schedule-save',value}));
 for(const duration_days of [true,-1,NaN,Infinity,3651])assert(!validRequest({action:'work-schedule-save',value:{...value,task:{...value.task,duration_days}}}));
 assert(!validRequest({action:'work-schedule-save',value:{...value,root:'/other'}}));
 assert(!validRequest({action:'work-schedule-save',value:{...value,task:{...value.task,state:'done'}}}));
 assert(!validRequest({action:'work-schedule-save',value:{...value,reason:''}}));
 for(const start_date of ['2026-02-30','0000-01-01','2026-1-01'])assert(!validRequest({action:'work-schedule-save',value:{...value,start_date}}));
});
test('schedule projection rejects malformed graph before renderer',()=>{
 assert(validResult(plan,'work-schedule-view'));assert(!validResult({...plan,snapshot:'wrong'},'work-schedule-view'));
 assert(!validResult({...plan,nodes:[{id:'missing-fields'}]},'work-schedule-view'));
 assert(!validResult({...plan,waves:[{stage:0,ids:'bad'}]},'work-schedule-view'));
});
test('loaded plan refreshes on changed source generation but not unchanged timer sync',async()=>{
 let current=status;const {memory,store,calls}=setup(action=>action==='work-schedule-view'?plan:current);
 store.value.knowledgeWorkSchedule=plan;await memory.run('sync','timer');assert(!calls.includes('work-schedule-view'));
 current={...status,generation:2};await memory.run('sync','timer');assert(calls.includes('work-schedule-view'),JSON.stringify({calls,error:store.value.knowledgeError}));assert.equal(store.value.knowledgeWorkSchedule,plan);memory.dispose();
});
test('reviewed relation change refreshes loaded schedule',async()=>{
 const {memory,store,calls}=setup(action=>action==='work-schedule-view'?plan:action==='work-review'?{relations:[]}:status);
 store.value.knowledgeWorkSchedule=plan;await memory.run('work-review',{});assert(calls.includes('work-schedule-view'),JSON.stringify({calls,error:store.value.knowledgeError}));memory.dispose();
});
test('late plan never returns after project switch',async()=>{
 let release;const {memory,store}=setup(()=>new Promise(resolve=>release=resolve));
 const pending=memory.run('work-schedule-view');store.generation++;store.value.project='C:/other';memory.reset();release(plan);await pending;
 assert.equal(store.value.knowledgeWorkSchedule,null);memory.dispose();
});
test('failed refresh clears prior readiness instead of retaining a current-looking plan',async()=>{
 const {memory,store}=setup(action=>{if(action==='sync')throw Error('source_changed');return status;});
 store.value.knowledgeWorkSchedule=plan;await memory.run('sync');assert.equal(store.value.knowledgeWorkSchedule,null);assert.equal(store.value.knowledgeError,'source_changed');memory.dispose();
});
test('forget clears loaded plan before waiting for backend',async()=>{
 let release;const {memory,store}=setup(action=>action==='forget'?new Promise(resolve=>release=resolve):action==='work-schedule-view'?plan:status);
 store.value.knowledgeWorkSchedule=plan;const pending=memory.run('forget','chat');assert.equal(store.value.knowledgeWorkSchedule,null);release({forgotten:'chat'});await pending;memory.dispose();
});
test('a failed review after source refresh cannot retain old work readiness',async()=>{
 const {memory,store}=setup(action=>{if(action==='sync')return {...status,generation:2};if(action==='wiki-review-view')throw Error('review_changed');return status;});
 store.value.knowledgeWorkSchedule=plan;await memory.run('wiki-review-view');assert.equal(store.value.knowledgeWorkSchedule,null);assert.equal(store.value.knowledgeError,'review_changed');memory.dispose();
});
