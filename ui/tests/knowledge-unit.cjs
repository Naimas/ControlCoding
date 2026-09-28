'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {KnowledgeState}=require('../knowledge-state.cjs');
const {validRequest,validResult}=require('../knowledge-contract.cjs');
function setup(handler){const store={generation:1,value:{project:'C:/fixture',generation:1,busy:false},publish(){}};
 const calls=[];const client={cancel(){},async run(op,root,options){calls.push({op,root,...options});return {status:'ok',result:{knowledge:options.action==='graph-view'?{sources:[],edges:[],wiki:[],conversations:[],graph:{topic:null,query:'',focus:null,snapshot:'a'.repeat(64),offset:0,total:0,source_total:0,next_offset:null,external_edges:0,window_size:200,topics:[]}}:await handler(options.action,options.value)}};}};
 const memory=new KnowledgeState(store,client);return {store,memory,calls};}
const status={schema_version:1,enabled:true,generation:1,policy:{automatic:true,retention:'transcript',embedding:'local',worker:false},counts:{chunks:2,embedded:2}};
test('work relation mutation drops wiki inspection and retrieval caches',async()=>{
 const {store,memory}=setup(a=>a==='work-propose'?{relations:[]}:status);
 store.value.knowledgeWikiTools={snapshot:'old'};store.value.knowledgeQuery={citations:[]};store.value.knowledgeAnswer={text:'old'};
 await memory.run('work-propose',{});
 assert.equal(store.value.knowledgeWikiTools,null);assert.equal(store.value.knowledgeQuery,null);assert.equal(store.value.knowledgeAnswer,null);
 memory.dispose();
});
test('knowledge IPC rejects unknown action and extra root routing',()=>{assert(!validRequest({action:'exec',value:null}));assert(!validRequest({action:'status',value:null,root:'/outside'}));assert(!validResult({enabled:true},'status'));});
test('library IPC accepts bounded windows and rejects unbounded results',()=>{
 const page={kind:'sources',query:'',snapshot:'a'.repeat(64),total:100,offset:0,page_size:50,rows:[{id:'a',title:'A'}],next_offset:1};
 assert(validRequest({action:'library',value:{kind:'sources',query:'',offset:0,snapshot:null}}));
 assert(validResult(page,'library'));assert(!validResult({...page,rows:Array(51).fill({id:'a',title:'A'})},'library'));
 assert(!validResult({...page,snapshot:'invalid'},'library'));assert(!validResult({...page,next_offset:0},'library'));
});
test('interactive read and archived turns wait for a background library window',async()=>{
 let release;const {memory,store,calls}=setup(a=>a==='library'?new Promise(r=>release=r):a==='conversation'?{saved:true}:a==='conversation-read'?{id:'chat',turns:[]}:status);
 store.value.knowledge=status;const listing=memory.run('library',{kind:'sources',query:'',offset:0,snapshot:null});
 const read=memory.run('conversation-read','chat');
 release({kind:'sources',rows:[],total:0});await Promise.all([listing,read]);
 assert.equal(store.value.knowledgeConversation.id,'chat');assert.deepEqual(calls.map(c=>c.action),['library','conversation-read']);
 const next=memory.run('library',{kind:'sources',query:'',offset:0,snapshot:null});
 const archive=memory.archive('concierge',[{role:'user',content:'Keep this turn'}]);
 release({kind:'sources',rows:[],total:0});await next;assert.equal(await archive,true);memory.dispose();
});
test('ordinary reconciliation preserves an open wiki page',async()=>{const {store,memory}=setup(a=>a==='catalog'?{sources:[],edges:[],conversations:[]}:status);store.value.knowledge=status;store.value.knowledgePage={id:'read'};await memory.run('sync','timer');assert.equal(store.value.knowledgePage.id,'read');memory.dispose();});

test('paused automatic refresh makes no timer read; manual retry remains available',async()=>{
 const {store,memory,calls}=setup(()=>status);store.value.knowledge={...status,policy:{...status.policy,automatic:false}};
 await memory.tick();assert.equal(calls.length,0);await memory.run('sync','manual');assert(calls.some(c=>c.action==='sync'));memory.dispose();
});

test('wiki review proposals reconcile before write and refresh review view',async()=>{
 const review={pages:[],proposals:[],notice:'review'};
 const {store,memory,calls}=setup(a=>a==='wiki-review-propose'?{id:'proposal',status:'pending'}:a==='wiki-review-view'?review:status);
 await memory.run('wiki-review-propose',{});assert.deepEqual(calls.map(c=>c.action),['sync','wiki-review-propose','wiki-review-view','status']);
 assert.equal(store.value.knowledgeWikiReview,review);memory.dispose();
});

test('late wiki review fetch never repopulates a switched project',async()=>{
 let resolve;const {store,memory}=setup(a=>a==='wiki-review-view'?new Promise(r=>resolve=r):status);
 const operation=memory.run('wiki-review-view');await new Promise(r=>setImmediate(r));
 store.generation++;memory.reset();resolve({pages:[],proposals:[],notice:'old'});await operation;
 assert.equal(store.value.knowledgeWikiReview,null);memory.dispose();
});

test('forget immediately clears cached reviewed private content',async()=>{
 const {store,memory}=setup(a=>a==='forget'?{forgotten:'chat'}:status);
 store.value.knowledgeWikiReview={proposals:[{body:'private transcript'}]};
 await memory.run('forget','chat');assert.equal(store.value.knowledgeWikiReview,null);memory.dispose();
});
test('changed generation invalidates answers and page',async()=>{const {store,memory}=setup(a=>a==='catalog'?{sources:[],edges:[],conversations:[]}:{...status,generation:2});store.value.knowledge=status;store.value.knowledgePage={id:'read'};store.value.knowledgeAnswer={text:'old'};await memory.run('sync');assert.equal(store.value.knowledgePage,null);assert.equal(store.value.knowledgeAnswer,null);memory.dispose();});
test('retrieval refreshes sources before loading the approved context projection',async()=>{const {memory,calls}=setup(a=>a==='query'?{query:'test',generation:status.generation,citations:[],answer:'No evidence'}:a==='consolidation-context'?{schema:0,generation:status.generation,claims:[]}:status);await memory.run('query',{text:'test',semantic:true});assert.deepEqual(calls.map(c=>c.action),['sync','query','consolidation-context']);memory.dispose();});
test('failed reconciliation refreshes stored ledger status and keeps the original error',async()=>{
 const {memory,store}=setup(a=>{if(a==='sync')throw Error('ocr_required');return {...status,needs_reconcile:true};});
 store.value.knowledge=status;await memory.run('sync');assert.equal(store.value.knowledgeError,'ocr_required');assert.equal(store.value.knowledge.needs_reconcile,true);memory.dispose();
});
test('late completion after project selection is discarded',async()=>{let release;const {memory,store}=setup(()=>new Promise(r=>release=r));const running=memory.run('status');store.generation++;store.value.project='C:/other';memory.reset();release(status);await running;assert.equal(store.value.knowledge,null);assert(!store.value.knowledgeBusy);memory.dispose();});
test('retention none makes no archive write',async()=>{const {memory,store,calls}=setup(()=>status);store.value.knowledge={...status,policy:{...status.policy,retention:'none'}};assert.equal(await memory.archive('concierge',[{role:'user',content:'private'}]),false);assert.equal(calls.length,0);memory.dispose();});
test('one conversation retains ordered events across exchanges',async()=>{const {memory,store,calls}=setup(a=>a==='conversation'?{saved:true}:status);store.value.knowledge=status;await memory.archive('concierge',[{role:'user',content:'first'}]);await memory.archive('concierge',[{role:'assistant',content:'reply'}]);const writes=calls.filter(c=>c.action==='conversation');assert.equal(writes[0].value.id,writes[1].value.id);assert.equal(writes[1].value.turns[0].sequence,1);memory.dispose();});
test('resume restores bounded visible history and next event sequence',()=>{const {memory,store}=setup(()=>status);store.ai={pending:{}};store.value.knowledgeConversation={id:'saved',title:'Direct AI conversation',summary:'summary',turns:[{sequence:4,role:'user',content:'continue'}]};memory.resume();assert.equal(store.value.aiMessages[0].content,'continue');assert.equal(memory.ids['Direct AI'].sequence,5);assert.equal(store.ai.pending,null);memory.dispose();});
test('external imports label provenance as unverified import',async()=>{const {memory,calls}=setup(a=>a==='conversation'?{saved:true}:a==='catalog'?{sources:[],edges:[],conversations:[]}:status);await memory.run('conversation',{turns:[{provenance:{origin:'native'}}]});assert.equal(calls[0].value.turns[0].provenance.origin,'external-import');memory.dispose();});
test('unknown generated citations are rejected',async()=>{const {memory,store}=setup(()=>status);store.value.knowledgeQuery={generation:1,query:'test',citations:[{id:'S1',path:'a.md',revision:'hash',excerpt:'evidence'}]};store.value.roleConfig={concierge:{enabled:true,mode:'direct',provider:'ollama',model:'local',generation:{}}};store.roles={providers:{ollama:{provider:'ollama',models:['local'],async send(){return {text:'Invented [S99]'};}}}};await memory.answer();assert.equal(store.value.knowledgeError,'answer_citations_invalid');assert.equal(store.value.knowledgeAnswer,null);memory.dispose();});

test('honest provider abstention is displayed and retained without a citation error',async()=>{
 const {memory,store,calls}=setup(a=>a==='conversation'?{saved:true}:a==='catalog'?{sources:[],edges:[],conversations:[]}:status);
 store.value.knowledge=status;
 store.value.knowledgeQuery={generation:1,query:'Unknown deployment date?',citations:[{id:'S1',path:'a.md',revision:'hash',excerpt:'Deployment is planned.'}]};
 store.value.roleConfig={concierge:{enabled:true,mode:'direct',provider:'ollama',model:'local',generation:{}}};
 store.roles={providers:{ollama:{provider:'ollama',models:['local'],async send(_model,messages){assert(messages[0].content.includes('INSUFFICIENT_EVIDENCE'));return {text:'INSUFFICIENT_EVIDENCE',incomplete:false};}}}};
 await memory.answer();assert.equal(store.value.knowledgeError,null);
 assert.equal(store.value.knowledgeAnswer.abstained,true);
 assert.equal(store.value.knowledgeAnswer.status,'insufficient_evidence');
 assert(calls.some(call=>call.action==='conversation'));memory.dispose();
});
test('retry retains original event and conversation identity after a new chat',async()=>{
 let offline=true;const {memory,store,calls}=setup(a=>{if(a==='conversation'&&offline)throw Error('offline');return a==='conversation'?{saved:true}:status;});
 store.value.knowledge=status;const provenance={model:'original'};
 await memory.archive('concierge',[{role:'user',content:'first'}],provenance);
 const original=calls[0].value.id;provenance.model='mutated';memory.newConversation('concierge');
 offline=false;await memory.archive('concierge',[{role:'user',content:'second'}]);
 const writes=calls.filter(c=>c.action==='conversation');
 assert.equal(writes[1].value.id,original);assert.equal(writes[1].value.turns[0].provenance.model,'original');
 assert.notEqual(writes[2].value.id,original);assert.equal(memory.pending.length,0);memory.dispose();
});
test('late archive error cannot contaminate a newly selected project',async()=>{
 let reject;const {memory,store}=setup(()=>new Promise((_r,j)=>reject=j));store.value.knowledge=status;
 const saving=memory.archive('concierge',[{role:'user',content:'private'}]);store.generation++;store.value.project='D:/other';memory.reset();
 reject(Error('old project error'));await saving;assert.equal(store.value.knowledgeArchive,null);assert.equal(memory.pending.length,0);memory.dispose();
});
test('concurrent saves serialize distinct reserved event sequences',async()=>{
 let release;const {memory,store,calls}=setup((a,v)=>a==='conversation'&&v.turns[0].sequence===0?new Promise(r=>release=r):a==='conversation'?{saved:true}:status);
 store.value.knowledge=status;const a=memory.archive('concierge',[{role:'user',content:'first'}]);
 const b=memory.archive('concierge',[{role:'assistant',content:'second'}]);release({saved:true});
 assert.deepEqual(await Promise.all([a,b]),[true,true]);assert.deepEqual(calls.filter(c=>c.action==='conversation').map(c=>c.value.turns[0].sequence),[0,1]);memory.dispose();
});
