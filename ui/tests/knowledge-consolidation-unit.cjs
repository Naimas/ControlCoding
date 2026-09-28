'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {validRequest,validResult}=require('../knowledge-contract.cjs');
const {KnowledgeState}=require('../knowledge-state.cjs');
const source={source:'source:a',path:'docs/a.md',revision:'a'.repeat(64),line:2,end_line:2,excerpt:'Recorded choice.',title:'A'};
const view={enabled:true,schema:2,notice:'Manual review',jobs:[{id:'job:a',title:'Review',state:'ready',created:'now',count:0}],job:{id:'job:a',title:'Review',state:'ready',stale:false,proposals:[],receipt:null},sources:[source],coverage:{selected:1,total:1,deferred:0},pages:[{type:'overview',id:'wiki:review:overview',title:'Overview',revision:'b'.repeat(64)}]};
const status={schema_version:1,enabled:true,generation:1,policy:{automatic:false,worker:false,retention:'none',embedding:''},counts:{chunks:1,embedded:0}};
function fixture(handler){const store={generation:1,value:{project:'C:/fixture',busy:false,knowledge:status},publish(){}};const calls=[];
 const client={cancel(){},async run(_op,_root,{action,value}){calls.push({action,value});return {status:'ok',result:{knowledge:await handler(action,value)}};}};
 return {store,calls,memory:new KnowledgeState(store,client)};}
test('consolidation bridge accepts bounded manual requests and rejects path injection',()=>{
 assert(validRequest({action:'consolidation-view',value:null}));
 assert(validRequest({action:'consolidation-create',value:{title:'Review'}}));
 assert(validRequest({action:'consolidation-propose',value:{job:'job:a',page_type:'overview',key:'choice',title:'Choice',body:'Recorded choice [S1].',kind:'decision_summary',reason:'Summarizes an explicit decision',citations:[source]}}));
 assert(validRequest({action:'consolidation-decide',value:{job:'job:a',ids:['proposal:a'],decision:'accept',request_id:'request:a'}}));
 assert(!validRequest({action:'consolidation-decide',value:{job:'job:a',ids:['proposal:a','proposal:a'],decision:'accept',request_id:'request:a'}}));
 assert(!validRequest({action:'consolidation-view',value:{job:'job:a',root:'D:/other'}}));
 assert(!validRequest({action:'consolidation-propose',value:{job:'job:a',page_type:'overview',key:'a',title:'A',body:'A',kind:'summary',reason:'A',citations:Array(9).fill(source)}}));
 assert(validResult(view,'consolidation-view'));
 assert(!validResult({...view,sources:Array(21).fill(source)},'consolidation-view'));
});
test('consolidation result is cached, source refresh clears it, and project switch discards late results',async()=>{
 let release;const {store,calls,memory}=fixture((action)=>action==='consolidation-view'?view:action==='consolidation-create'?view:action==='sync'?status:status);
 await memory.run('consolidation-view');assert.equal(store.value.knowledgeConsolidation,view);
 await memory.run('sync');assert.equal(store.value.knowledgeConsolidation,null);
 await memory.run('consolidation-create',{title:'Review'});assert.equal(store.value.knowledgeConsolidation,view);
 assert(calls.some(c=>c.action==='consolidation-create'));
 memory.dispose();
 const delayed=fixture(action=>action==='consolidation-view'?new Promise(resolve=>release=resolve):status);
 const pending=delayed.memory.run('consolidation-view');await new Promise(resolve=>setImmediate(resolve));
 delayed.store.generation++;delayed.memory.reset();release(view);await pending;
 assert.equal(delayed.store.value.knowledgeConsolidation,null);delayed.memory.dispose();
});
test('a consolidation mutation refreshes status and drops source-bound cached views',async()=>{
 const fresh={...status,generation:2};
 const {store,calls,memory}=fixture(action=>action==='consolidation-propose'?view:action==='status'?fresh:status);
 Object.assign(store.value,{knowledgePage:{id:'wiki:review:overview'},knowledgeLibrary:{rows:[]},knowledgeCatalog:{sources:[]},knowledgeWikiReview:{pages:[]},knowledgeWork:{relations:[]},knowledgeQuery:{answer:'old'},knowledgeAnswer:{text:'old'}});
 await memory.run('consolidation-propose',{job:'job:a',page_type:'overview',key:'choice',title:'Choice',body:'Choice [S1].',kind:'summary',reason:'Recorded',citations:[source]});
 assert.equal(store.value.knowledgeConsolidation,view);
 assert.equal(store.value.knowledge,fresh);
 for(const key of ['knowledgePage','knowledgeLibrary','knowledgeCatalog','knowledgeWikiReview','knowledgeWork','knowledgeQuery','knowledgeAnswer'])assert.equal(store.value[key],null,key);
 assert.deepEqual(calls.map(c=>c.action),['consolidation-propose','status']);memory.dispose();
});
test('failed proposal after backend reconciliation clears stale evidence and reloads the selected job',async()=>{
 const fresh={...status,generation:2},stale={...view,job:{...view.job,stale:true}};
 const {store,calls,memory}=fixture(action=>{if(action==='consolidation-propose')throw Error('consolidation_context_changed');return action==='status'?fresh:stale;});
 Object.assign(store.value,{knowledgeConsolidation:view,knowledgePage:{body:'old'},knowledgeLibrary:{rows:[]},knowledgeCatalog:{sources:[]},knowledgeWikiReview:{pages:[]},knowledgeWork:{relations:[]},knowledgeQuery:{answer:'old'},knowledgeAnswer:{text:'old'},knowledgeLint:{issues:[]}});
 await memory.run('consolidation-propose',{job:'job:a',page_type:'overview',key:'choice',title:'Choice',body:'Choice [S1].',kind:'summary',reason:'Recorded',citations:[source]});
 assert.equal(store.value.knowledgeError,'consolidation_context_changed');assert.equal(store.value.knowledge,fresh);
 assert.equal(store.value.knowledgeConsolidation,stale);
 for(const key of ['knowledgePage','knowledgeLibrary','knowledgeCatalog','knowledgeWikiReview','knowledgeWork','knowledgeQuery','knowledgeAnswer','knowledgeLint'])assert.equal(store.value[key],null,key);
 assert.deepEqual(calls.map(c=>c.action),['consolidation-propose','status','consolidation-view']);
 assert.deepEqual(calls.at(-1).value,{job:'job:a'});memory.dispose();
});
test('failed consolidation mutation keeps original error if status and job reload also fail',async()=>{
 const {store,calls,memory}=fixture(action=>{throw Error(action==='consolidation-create'?'consolidation_job_limit':'secondary_read_failed');});
 store.value.knowledgeConsolidation=view;store.value.knowledgeQuery={answer:'old'};
 await memory.run('consolidation-create',{title:'Review'});
 assert.equal(store.value.knowledgeError,'consolidation_job_limit');
 assert.equal(store.value.knowledge,null);assert.equal(store.value.knowledgeConsolidation,null);assert.equal(store.value.knowledgeQuery,null);
 assert.deepEqual(calls.map(c=>c.action),['consolidation-create','status','consolidation-view']);memory.dispose();
});
test('late failed-mutation recovery cannot repopulate another project',async()=>{
 let release;const {store,memory}=fixture(action=>{if(action==='consolidation-decide')throw Error('consolidation_context_changed');if(action==='status')return new Promise(resolve=>release=resolve);return view;});
 store.value.knowledgeConsolidation=view;
 const pending=memory.run('consolidation-decide',{job:'job:a',ids:['proposal:a'],decision:'accept',request_id:'request:a'});
 await new Promise(resolve=>setImmediate(resolve));store.generation++;store.value.project='D:/other';memory.reset();release(status);await pending;
 assert.equal(store.value.knowledgeConsolidation,null);assert.equal(store.value.knowledgeError,null);memory.dispose();
});
test('context reconciliation advancing generation discards an old query packet',async()=>{
 const packet={query:'Which design?',generation:1,citations:[{id:'S1',source:source.source,path:source.path,revision:source.revision,line:2,end_line:2,excerpt:source.excerpt,kind:'document'}]};
 const projection={schema:3,query:'which design?',generation:2,consolidation_epoch:1,claims:[],backlinks:[],history:[],total:0,offset:0,next_offset:null,history_total:0,history_next_offset:null,notice:'Current'};
 const fresh={...status,generation:2};
 const {store,calls,memory}=fixture(action=>action==='query'?packet:action==='consolidation-context'?projection:fresh);
 await memory.run('query',{text:'Which design?',semantic:false});
 assert.equal(store.value.knowledgeQuery,null);assert.equal(store.value.knowledgeError,'query_context_changed');
 assert.equal(store.value.knowledge.generation,2);
 assert.deepEqual(calls.map(c=>c.action),['sync','query','consolidation-context','status']);memory.dispose();
});
