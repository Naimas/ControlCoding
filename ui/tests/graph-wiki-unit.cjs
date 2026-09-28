'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {supplementQuery}=require('../knowledge-context.cjs');
const {formatEvidence}=require('../knowledge-evidence.cjs');
const {validRequest,validResult}=require('../knowledge-contract.cjs');
const citation={id:'S1',source:'source',path:'docs/a.md',revision:'a'.repeat(64),line:1,end_line:1,excerpt:'Evidence',kind:'document'};
test('scoped graph query cannot acquire outside-scope approved memory',()=>{
 const packet={query:'needle',generation:1,citations:[citation],retrieval:{options:{paths:['docs/a.md'],kinds:[],lifecycle:'all'}}};
 const projection={generation:1,query:'needle',claims:[{id:'claim',title:'Other',body:'Private [S1]',review_status:'approved',current_status:'current',evidence:[{...citation,path:'private/b.md',origin:'original_source'}]}]};
 assert.equal(supplementQuery(packet,projection).approvedClaims.length,0);
 assert.equal(supplementQuery(packet,projection).citations.length,1);
});
test('historical retrieval never becomes current model evidence',()=>{
 const packet={historical:true,citations:[citation]};
 assert.equal(supplementQuery(packet,{}),packet);
 assert.throws(()=>formatEvidence({knowledgeQuery:packet}),/historical_evidence/);
});
test('reviewed graph changes invalidate outgoing original context',()=>{
 assert.throws(()=>formatEvidence({knowledge:{generation:1,work_revision:'new'},knowledgeQuery:{generation:1,work_revision:'old',citations:[citation]}}),/stale/);
});
test('wiki tools bridge allows only named bounded actions',()=>{
 assert(validRequest({action:'wiki-tools-view',value:{page:'wiki:review:overview'}}));
 assert(!validRequest({action:'wiki-tools-write-file',value:{}}));
 assert(!validRequest({action:'wiki-tools-view',value:{page:'X'.repeat(60000)}}));
 assert(validResult({pages:Array.from({length:11},()=>({})),proposals:[],notice:'review'},'wiki-review-view'));
});
