'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {KnowledgeState}=require('../knowledge-state.cjs');
const {validResult}=require('../knowledge-contract.cjs');
test('bridge accepts the current ten-passage budget but rejects eleven',()=>{
 const packet={query:'test',answer:'Evidence',citations:Array.from({length:10},()=>({excerpt:'text',revision:'a'.repeat(64)}))};
 assert(validResult(packet,'query'));packet.citations.push(packet.citations[0]);assert(!validResult(packet,'query'));
});
function fixture(){
 const calls=[],sent=[],fresh={generation:7,work_revision:'w',needs_reconcile:false};
 const original={query:'When did recovery run?',generation:7,work_revision:'w',citations:[],edges:[]};
 const store={generation:1,value:{project:'C:/authorized',busy:false},publish(){}};
 const client={cancel(){calls.push({cancel:true});},async run(_op,root,{action,value}){
  calls.push({root,action,value});return {status:'ok',result:{knowledge:action==='sync'?fresh:await f.query(value)}};
 }};
 const memory=new KnowledgeState(store,client);
 const provider={provider:'ollama',models:['fixture'],async send(model,messages,signal,options){sent.push({model,messages,options});return f.send(signal);}};
 store.roles={counts:{},providers:{ollama:provider}};
 Object.assign(store.value,{knowledgeQuery:original,roleConfig:{concierge:{enabled:true,mode:'direct',provider:'ollama',model:'fixture',maxRequests:5,maxOutputTokens:1000,timeoutSeconds:30,generation:{}}}});
 const f={memory,store,calls,sent,fresh,original,provider,
  send:async()=>({text:'{"queries":["recovery audit timestamp","restore receipt"]}'}),
  query:async value=>({...original,query:value.text,citations:[{id:'S1',source:'log',path:'docs/recovery.md',revision:'r',line:1,end_line:1,excerpt:'Recovery ran at 10:01 UTC.',title:'Recovery',kind:'document'}]})};
 return f;
}
test('follow-up retrieves missing evidence, previews it and never auto-generates an answer',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,null);assert.equal(f.sent.length,1);
 assert.equal(f.sent[0].messages[1].content,f.original.query);
 assert.equal(f.store.value.knowledgeQuery.query,f.original.query);
 assert.equal(f.store.value.knowledgeQuery.citations[0].path,'docs/recovery.md');
 assert.equal(f.store.value.knowledgeQuery.followup.new_passages,1);
 assert.equal(f.store.value.knowledgeAnswer,null);assert.equal(f.store.roles.counts.concierge,1);
 assert(f.calls.every(x=>x.root==='C:/authorized'));
 assert(f.calls.filter(x=>x.action==='query').every(x=>Object.keys(x.value).sort().join()==='semantic,text'));
 await f.memory.deepen();assert.equal(f.sent.length,1);
});
test('absent evidence remains unassessed and does not claim exhaustive search',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.query=async v=>({...f.original,query:v.text});await f.memory.deepen();
 assert.equal(f.store.value.knowledgeQuery.citations.length,0);
 assert.equal(f.store.value.knowledgeQuery.answerability,'not_assessed');
 assert.equal(f.store.value.knowledgeQuery.followup.new_passages,0);
});
test('access failure is reported, not presented as an answer or hidden retry',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.query=async()=>{throw Error('source_access_denied');};await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,'source_access_denied');assert.equal(f.store.value.knowledgeQuery.followup.status,'stopped');
 assert.equal(f.calls.filter(x=>x.action==='query').length,1);assert.equal(f.sent.length,1);
});
test('quota prevents provider and bridge calls',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.store.roles.counts.concierge=5;await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,'role_request_limit');assert.equal(f.calls.length,0);assert.equal(f.sent.length,0);
});
test('model cannot expand source scope through plan fields',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.send=async()=>({text:'{"queries":["x"],"root":"D:/private"}'});await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,'followup_plan_invalid');assert.equal(f.calls.filter(x=>x.action==='query').length,0);
});
test('initial refresh drops stale evidence and pins new scope before planning',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.fresh.generation++;
 f.original.citations=[{id:'S1',excerpt:'Old private evidence'}];
 f.query=async v=>({...f.original,query:v.text,generation:f.fresh.generation,citations:[]});await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,null);assert.equal(f.sent.length,1);
 assert.equal(f.store.value.knowledgeQuery.citations.length,0);assert.equal(f.store.value.knowledgeQuery.generation,8);
 assert(f.store.value.knowledgeQuery.followup.initial_context_refreshed);
 assert(!JSON.stringify(f.sent).includes('Old private evidence'));
});
test('policy change during inference prevents retrieval',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.send=async()=>{f.store.value.roleConfig.concierge.enabled=false;return {text:'{"queries":["alternate"]}'};};await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,'role_file_changed');assert.equal(f.calls.filter(x=>x.action==='query').length,0);
});
test('project switch cannot send late results into new project',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.send=async()=>{f.store.generation++;f.store.value.project='D:/other';f.memory.reset();return {text:'{"queries":["alternate"]}'};};await f.memory.deepen();
 assert.equal(f.store.value.knowledgeQuery,null);assert.equal(f.store.value.knowledgeError,null);assert.equal(f.calls.filter(x=>x.action==='query').length,0);
});
test('cancel terminates even a provider which ignores abort signal',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.send=async()=>{queueMicrotask(()=>f.memory.cancel());return new Promise(()=>{});};await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,'cancelled');assert.equal(f.store.value.knowledgeBusy,false);
 assert.equal(f.calls.filter(x=>x.action==='query').length,0);
});
test('change detected by final reconciliation invalidates retrieved preview',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());f.query=async v=>{f.fresh.generation++;return {...f.original,query:v.text};};await f.memory.deepen();
 assert.equal(f.store.value.knowledgeError,'answer_context_changed');assert.equal(f.store.value.knowledgeQuery,null);
});
test('total deadline stops work without waiting for an unresponsive provider',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());let deadline;
 const original=setTimeout;global.setTimeout=(callback,ms)=>{assert.equal(ms,60000);deadline=callback;return original(()=>{},0);};
 f.send=async()=>{queueMicrotask(deadline);return new Promise(()=>{});};
 try{await f.memory.deepen();}finally{global.setTimeout=original;}
 assert.equal(f.store.value.knowledgeError,'followup_deadline');assert.equal(f.store.value.knowledgeBusy,false);
 assert.equal(f.calls.filter(x=>x.action==='query').length,0);
});
test('explicit investigate action deepens an abstention, but not a supported answer',async t=>{
 const f=fixture();t.after(()=>f.memory.dispose());let searches=0;
 f.original.citations=[{id:'S1',excerpt:'Background only'}];
 f.memory.deepen=async()=>{searches++;};
 f.send=async()=>({text:'INSUFFICIENT_EVIDENCE'});await f.memory.investigate();assert.equal(searches,1);
 f.send=async()=>({text:'Established [S1].'});await f.memory.investigate();assert.equal(searches,1);
 f.send=async()=>{throw Error('provider_timeout');};await f.memory.investigate();assert.equal(searches,1);
});
