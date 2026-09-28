'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {memoryContext,revalidateMemory,supplementQuery}=require('../knowledge-context.cjs');
const {AIRoles}=require('../ai-roles.cjs'),{defaults}=require('../role-config.cjs');
const {AISession}=require('../ai-session.cjs');
function fixture(){
 const citations=Array.from({length:8},(_,i)=>({id:'S'+(i+1),source:'source:'+i,path:'docs/source-'+i+'.md',revision:'a'.repeat(64),line:1,end_line:2,kind:i?'document':'conversation',excerpt:'source text '+i+' '+'.'.repeat(1400)}));
 const store={generation:1,value:{project:process.cwd(),busy:false,knowledge:{enabled:true,generation:3,needs_reconcile:false},knowledgeQuery:{query:'Design choice',generation:3,observed_at:'2026-09-24',citations,answer:'GENERATED ANSWER MUST NOT BE CONTEXT'}},publish(){}};
 let changed=false;const sent=[],archived=[];
 store.knowledge={async call(){return {...store.value.knowledge,generation:changed?4:3};},async archive(...v){archived.push(v);return true;}};
 let config=defaults();const storage={read(){return {roles:config,revision:'fixed'};},save(_p,value){config=structuredClone(value);return 'fixed';}};
 const provider={provider:'ollama',models:['local'],async send(_m,messages){sent.push(messages);return {text:'Visible reply',incomplete:false};}};
 const roles=new AIRoles(store,{storage,providers:{ollama:provider}});
 const chosen=structuredClone(store.value.roleConfig);Object.assign(chosen.concierge,{enabled:true,provider:'ollama',model:'local',context:'memory',maxContextChars:6000});roles.save(chosen);
 const ai=new AISession(store,provider);return {store,roles,ai,sent,archived,change(){changed=true;}};
}
test('bounded memory context keeps full cited blocks and excludes generated answers',()=>{
 const f=fixture(),context=memoryContext(f.store.value,6000);
 assert(context.text.length<=6000);assert(context.text.includes('omitted'));assert(context.text.includes('Evidence kind: conversation'));
 assert(!context.text.includes('GENERATED ANSWER'));assert(context.lineage.citations.length<8);assert(context.lineage.citations[0].revision==='a'.repeat(64));
 assert.throws(()=>memoryContext(f.store.value,100),/role_context_limit/);
});
test('enabled unified memory never silently falls back to legacy or stale evidence',()=>{
 const f=fixture();f.store.value.work={packet:{packetMarkdown:'Legacy source'}};f.store.value.knowledge.needs_reconcile=true;
 assert.throws(()=>memoryContext(f.store.value),/role_context_stale/);f.store.value.knowledge.enabled=false;
 assert.equal(memoryContext(f.store.value).text,'Legacy source');
});
test('role uses shared evidence and records source lineage',async()=>{
 const f=fixture();await f.roles.submit({role:'concierge',prompt:'Explain'});assert(f.store.value.rolePreview.messages.at(-1).content.includes('[S1]'));
 await f.roles.send();assert.equal(f.sent.length,1);assert.equal(f.archived[0][2].context.origin,'unified-knowledge');
});
test('source change before role send blocks inference and consumes no provider quota',async()=>{
 const f=fixture();await f.roles.submit({role:'concierge',prompt:'Explain'});f.change();await f.roles.send();
 assert.equal(f.sent.length,0);assert.equal(f.store.value.roleError,'role_context_stale');assert(!f.store.value.roleCounts.concierge);
 assert(!f.store.value.roleEvents.some(e=>e.kind==='request_started'));
});
test('Direct AI checks current source generation before sending',async()=>{
 const f=fixture();f.ai.prepare({role:'advisor',model:'local',prompt:'Explain',context:'memory',archive:false});
 assert(f.store.value.aiPreview.messages.at(-1).content.includes('[S1]'));f.change();await f.ai.send();
 assert.equal(f.sent.length,0);assert.equal(f.store.value.aiError,'role_context_stale');
});
test('manual handoff includes evidence and revalidates before clipboard and acceptance',async()=>{
 const f=fixture();f.roles.save({...f.store.value.roleConfig,concierge:{...f.store.value.roleConfig.concierge,mode:'handoff'}});
 await f.roles.submit({role:'concierge',prompt:'Explain'});const packet=f.store.value.manualHandoff;
 assert(packet.packet.includes('Evidence kind: conversation'));let copied=false;f.roles.manual.copyText=()=>copied=true;
 f.roles.manual.preview({requestId:packet.id,text:'CC-REPLY: '+packet.id+'\nExternal answer'});f.change();
 await f.roles.manual.copy();assert(!copied);assert.equal(f.sent.length,0);
 await f.roles.manual.accept();assert.equal(f.store.value.manualResult,null);assert.equal(f.archived.length,0);
});
test('late reconciliation cannot attach one project evidence to another',async()=>{
 const f=fixture();f.store.knowledge.call=async()=>{f.store.generation++;f.store.value.project='another';return {enabled:true,generation:3};};
 await assert.rejects(revalidateMemory(f.store,'memory'),/role_context_stale/);
});
test('agent graph follows unified evidence availability and freshness',()=>{
 const f=fixture();f.store.value.knowledge.policy={retention:'transcript'};
 const {projectGraph}=require('../agent-graph.cjs');
 let graph=projectGraph(f.store.value);assert.equal(graph.nodes.find(n=>n.id==='memory').status,'available');
 assert.equal(graph.nodes.find(n=>n.id==='concierge').status,'available');
 f.store.value.knowledge.needs_reconcile=true;graph=projectGraph(f.store.value);
 assert.equal(graph.nodes.find(n=>n.id==='memory').status,'blocked');assert.match(graph.nodes.find(n=>n.id==='concierge').detail,/stale/);
});
test('approved claim maps to current original anchors and preserves citation indices',()=>{
 const f=fixture(),packet=f.store.value.knowledgeQuery,original=packet.citations[0];
 const extra={source:'source:extra',path:'docs/extra.md',title:'Extra',revision:'b'.repeat(64),line:3,end_line:3,excerpt:'Other original passage',kind:'document',origin:'original_source'};
 const claim={id:'memory:1',title:'Reviewed summary',body:'Original one [S1] and other [S2].',revision:'r1',review_status:'approved',current_status:'current',epistemic_status:'disputed',scope:'project',evidence:[{...original,origin:'original_source'},extra]};
 const projection={generation:3,consolidation_epoch:7,claims:[claim]};
 const merged=supplementQuery(packet,projection);
 assert.equal(merged.citations[0].id,'S1');assert.equal(merged.citations[0].source,original.source);
 assert.equal(merged.citations.length,9);
 assert.equal(merged.approvedClaims[0].body,'Original one [S1] and other [S9].');
 f.store.value.knowledgeConsolidationContext=projection;
 const context=memoryContext(f.store.value,16000);
 assert.match(context.text,/DISPUTED; do not resolve from recency/);
 assert.match(context.text,/Other original passage/);
 assert(!context.lineage.citations.some(c=>c.source==='memory:1'));
});
test('stale, rejected, pending and replaced claims never enter operational context',()=>{
 const f=fixture(),anchor={...f.store.value.knowledgeQuery.citations[0],origin:'original_source'};
 for(const [review_status,current_status] of [['pending','current'],['rejected','current'],['approved','stale'],['approved','replaced']]){
  const projection={generation:3,consolidation_epoch:1,claims:[{id:'memory:excluded',title:'Excluded',body:'EXCLUDED CLAIM [S1]',review_status,current_status,evidence:[anchor]}]};
  assert.equal(supplementQuery(f.store.value.knowledgeQuery,projection).approvedClaims.length,0);
 }
});
test('answer prompt uses approved claim with original citation and rechecks epoch',async()=>{
 const {KnowledgeState}=require('../knowledge-state.cjs');
 const citation={id:'S1',source:'source:1',path:'docs/design.md',title:'Design',revision:'r1',line:2,end_line:2,excerpt:'Choose Postgres.',kind:'document'};
 const projection={schema:2,generation:3,consolidation_epoch:4,claims:[{id:'memory:x',title:'Database choice',body:'Postgres was chosen [S1].',revision:'c1',review_status:'approved',current_status:'current',epistemic_status:'decided',scope:'project',evidence:[{...citation,origin:'original_source'}]}]};
 const sent=[];const provider={provider:'ollama',models:['local'],async send(_model,messages){sent.push(messages);return {text:'Postgres was chosen [S1].',incomplete:false};}};
 const value={project:'test',busy:false,knowledgeQuery:{query:'Database?',generation:3,citations:[citation]},knowledgeConsolidationContext:projection,roleConfig:{concierge:{enabled:true,mode:'provider',provider:'ollama',model:'local',maxRequests:3,maxOutputTokens:200,timeoutSeconds:10,generation:{}}}};
 const store={value,generation:1,publish(){},roles:{providers:{ollama:provider},counts:{}}};
 const fake={store,running:false,async call(action){if(action==='sync')return {generation:3};if(action==='consolidation-context')return projection;throw Error(action);},async archive(){}};
 await KnowledgeState.prototype.answer.call(fake);
 assert.equal(sent.length,1);
 assert.match(sent[0][1].content,/Reviewed derived memory/);
 assert.match(sent[0][1].content,/Original evidence:[\s\S]*\[S1\] docs\/design.md/);
 assert.equal(value.knowledgeAnswer.status,'cited_draft');
 assert(value.knowledgeQuery.citations.every(c=>!c.source.startsWith('memory:')));
});
test('answer stops before provider when approved context epoch changes',async()=>{
 const {KnowledgeState}=require('../knowledge-state.cjs');
 let sent=0;
 const provider={provider:'ollama',models:['local'],async send(){sent++;return {text:'Answer [S1].',incomplete:false};}};
 const value={project:'test',busy:false,knowledgeQuery:{query:'Question',generation:3,citations:[{id:'S1',source:'source:1',path:'x',revision:'r',line:1,end_line:1,excerpt:'x',kind:'document'}]},knowledgeConsolidationContext:{generation:3,consolidation_epoch:4},roleConfig:{concierge:{enabled:true,mode:'provider',provider:'ollama',model:'local',maxRequests:3,maxOutputTokens:200,timeoutSeconds:10,generation:{}}}};
 const store={value,generation:1,publish(){},roles:{providers:{ollama:provider},counts:{}}};
 const fake={store,running:false,async call(action){if(action==='sync')return {generation:3};if(action==='consolidation-context')return {generation:3,consolidation_epoch:5,claims:[]};throw Error(action);},async archive(){}};
 await KnowledgeState.prototype.answer.call(fake);
 assert.equal(sent,0);assert.equal(value.knowledgeError,'answer_context_changed');
});
