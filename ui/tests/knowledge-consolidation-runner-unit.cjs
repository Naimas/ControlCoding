'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {generationDefaults}=require('../ai-capabilities.cjs');
const {buildMessages,runPrepared}=require('../knowledge-consolidation-runner.cjs');
const packet={protocol:1,job:'job:one',request_id:'request:one',mode:'local',prompt:'Only original evidence. Return JSON.',manifest_digest:'a'.repeat(64),
 input_manifest:[{evidence_id:'S1',source:'source:one',path:'decision.md',revision:'b'.repeat(64),line:1,end_line:1,excerpt:'PostgreSQL was selected.',title:'Decision'}],
 config:{provider:'ollama',model:'local-chat',generation:{...generationDefaults(),format:'json'},maxContextChars:12000,maxOutputTokens:512,timeoutSeconds:30,maxRequests:2,roleRevision:'role:one'}};
const provider=send=>({provider:'ollama',models:['local-chat'],capability:()=>({chat:true,efforts:[],thinking:[],modes:[],verbosity:false,temperature:'always',json:true,summary:false}),send});
function callLog(){const calls=[];return {calls,call:async(action,value)=>{calls.push({action,value});return action==='consolidation-attempt'?{state:value.operation,lease_until:new Date(Date.now()+30000).toISOString()}:{outcome:'no_change',count:0};}};}
test('frozen prompt and original anchors bind the request within its context cap',()=>{
 const messages=buildMessages(packet);assert.equal(messages[0].content,packet.prompt);assert(messages[1].content.includes('PostgreSQL was selected.'));
 assert(messages[1].content.includes(packet.manifest_digest));assert.throws(()=>buildMessages({...packet,config:{...packet.config,maxContextChars:100}}),/consolidation_context_limit/);
});
test('approved context is visibly derived and cites only pinned original anchors',()=>{
 const approved={...packet,approved_evidence:[{...packet.input_manifest[0],evidence_id:'S2'}],approved_context:[{id:'memory:one',target:'wiki:overview',revision:'c'.repeat(64),title:'Approved choice',body:'PostgreSQL was approved [S2].',status:'approved',epistemic_status:'decided',scope:'project',evidence_ids:['S2']}]};
 const messages=buildMessages(approved),user=messages[1].content;
 assert(user.includes('approved_context'));assert(user.includes('PostgreSQL was approved [S2].'));
 assert(user.includes('Cite only original evidence IDs in input_manifest or approved_evidence'));
 assert(user.includes('Coverage counts only the new input_manifest passages'));
});
test('local send reserves and starts before transport, then imports measured usage',async()=>{
 const {calls,call}=callLog();let sent=0;const result=await runPrepared({packet,provider:provider(async()=>{sent++;assert.deepEqual(calls.map(x=>x.value.operation),['reserve','start']);return {text:'{"protocol":1}',incomplete:false,usage:{input_tokens:32,output_tokens:7}};}),call,owner:'worker:one'});
 assert.equal(sent,1);assert.equal(result.outcome,'no_change');assert.equal(calls.at(-1).action,'consolidation-import');assert.deepEqual(calls.at(-1).value.usage,{input_tokens:32,output_tokens:7});
});
test('incomplete provider output is an operational failure, never a no-change import or fallback',async()=>{
 const {calls,call}=callLog();await assert.rejects(runPrepared({packet,provider:provider(async()=>({text:'{"outcome":"no_change"}',incomplete:true,usage:null})),call,owner:'worker:one'}),/provider_incomplete/);
 assert(!calls.some(x=>x.action==='consolidation-import'));assert.equal(calls.at(-1).action,'consolidation-fail');assert.equal(calls.at(-1).value.usage,null);
});
test('provider mismatch blocks before reservation',async()=>{
 const {calls,call}=callLog();await assert.rejects(runPrepared({packet:{...packet,mode:'api'},provider:provider(async()=>{}),call,owner:'worker:one'}),/consolidation_provider_mismatch/);assert.equal(calls.length,0);
});
test('abort during start cannot send and persists the selected pause state',async()=>{
 const controller=new AbortController(),calls=[];let sent=false;
 const call=async(action,value)=>{calls.push({action,value});if(action==='consolidation-attempt'&&value.operation==='start')controller.abort();return {lease_until:new Date(Date.now()+30000).toISOString()};};
 await assert.rejects(runPrepared({packet,provider:provider(async()=>{sent=true;}),call,owner:'worker:one',signal:controller.signal,abortOperation:()=> 'pause'}),/cancelled/);
 assert.equal(sent,false);assert.deepEqual(calls.filter(x=>x.action==='consolidation-attempt').map(x=>x.value.operation),['reserve','start','pause']);
});
