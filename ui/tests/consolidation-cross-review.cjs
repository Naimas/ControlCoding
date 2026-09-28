'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {ConsolidationRunner}=require('../knowledge-consolidation.cjs');
const {generationDefaults}=require('../ai-capabilities.cjs');
const {effectivePrompt}=require('../role-prompts.cjs');
const {runPrepared}=require('../knowledge-consolidation-runner.cjs');
const {KnowledgeState}=require('../knowledge-state.cjs');

const config={provider:'ollama',model:'local',generation:{...generationDefaults(),format:'json'},
 maxContextChars:12000,maxOutputTokens:512,timeoutSeconds:30,maxRequests:2,roleRevision:'role-one'};
const packet={protocol:1,job:'job-one',request_id:'request-one',mode:'manual',
 prompt:'Curator prompt',manifest_digest:'a'.repeat(64),input_manifest:[],config};

test('project switch during prepare refresh cannot mark the new project prepared',async()=>{
 let releaseView;
 const value={project:'project-one',knowledgeConsolidationExecution:null};
 const store={generation:1,value,publish(){},knowledge:{async call(action){
  if(action==='consolidation-prepare')return packet;
  if(action==='consolidation-view')return new Promise(resolve=>{releaseView=resolve;});
  throw Error(action);
 }}};
 const runner=new ConsolidationRunner(store,{copyText(){}});
 runner.role=()=>({provider:'ollama',model:'local',generation:{format:'json'},systemPrompt:'Curator prompt'});
 runner.config=()=>config;
 const pending=runner.prepare(packet.job,'manual');
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(typeof releaseView,'function');
 store.generation++;value.project='project-two';runner.reset();
 releaseView({job:{packet}});
 await assert.rejects(pending,/consolidation_project_changed/);
 assert.equal(value.knowledgeConsolidationExecution,null);
 runner.dispose();
});

test('app daily trigger uses backend-safe ID and not-due window still reads queued work',async()=>{
 const actions=[];
 const value={project:'project-one',busy:false,knowledgeBusy:false,knowledge:{enabled:true},knowledgeConsolidationExecution:null};
 const store={generation:1,value,publish(){},knowledge:{async call(action,input){
  actions.push({action,input});
  if(action==='consolidation-settings')return {settings:{enabled:true,triggers:['daily'],send_policy:'local',timezone:'Europe/Rome'}};
  if(action==='consolidation-queue'&&input){
   assert.match(input.event_id,/^[A-Za-z0-9_-]{1,80}$/);
   throw Error('consolidation_window_not_due');
  }
  if(action==='consolidation-queue')return {queued:false,items:[]};
  throw Error(action);
 }}};
 const runner=new ConsolidationRunner(store,{copyText(){}});
 await runner.tick();
 assert.deepEqual(actions.map(item=>item.action),['consolidation-settings','consolidation-queue','consolidation-queue']);
 runner.dispose();
});

test('manual copy checks source freshness and curator role before clipboard',async()=>{
 const copied=[],role={enabled:true,provider:'ollama',model:'local',generation:{format:'json'},systemPrompt:'Initial curator'};
 const frozen={...packet,prompt:effectivePrompt(role)};
 const value={project:'project-one',knowledgeConsolidationExecution:null};
 const store={generation:1,value,publish(){},roles:{revision:'role-one'},knowledge:{async call(action){if(action==='sync')return {generation:1};throw Error(action);}}};
 const runner=new ConsolidationRunner(store,{copyText:text=>copied.push(text)});
 runner.role=()=>role;
 runner.refresh=async()=>{runner.packet=frozen;return {job:{stale:true}};};
 await assert.rejects(runner.copy(frozen.job,frozen.request_id),/consolidation_packet_stale/);
 await assert.rejects(runner.exportText(frozen.job,frozen.request_id),/consolidation_packet_stale/);
 runner.refresh=async()=>{runner.packet=frozen;return {job:{stale:false}};};
 role.systemPrompt='Changed curator';
 await assert.rejects(runner.copy(frozen.job,frozen.request_id),/consolidation_role_changed/);
 await assert.rejects(runner.exportText(frozen.job,frozen.request_id),/consolidation_role_changed/);
 assert.equal(copied.length,0);
 runner.dispose();
});

test('pause after transport starts aborts provider and stores pause rather than import',async()=>{
 const controller=new AbortController(),operations=[];
 let signalTransport;
 const started=new Promise(resolve=>{signalTransport=resolve;});
 const provider={provider:'ollama',models:['local'],capability:()=>({chat:true,efforts:[],thinking:[],modes:[],verbosity:false,temperature:'always',json:true,summary:false}),
  send:async(_model,_messages,signal)=>{signalTransport();await new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('cancelled')),{once:true}));}};
 const call=async(action,value)=>{operations.push(action==='consolidation-attempt'?value.operation:action);return {state:value.operation,lease_until:new Date(Date.now()+30000).toISOString()};};
 const local={...packet,mode:'local'};
 const pending=runPrepared({packet:local,provider,call,owner:'runner-a',signal:controller.signal,abortOperation:()=> 'pause'});
 await started;controller.abort();
 await assert.rejects(pending,/cancelled/);
 assert.deepEqual(operations,['reserve','start','pause']);
});

test('old project callback cannot clear the new project run controller',async()=>{
 const pending=new Map(),value={project:'project-one',knowledgeConsolidationExecution:null};
 const provider={provider:'ollama',models:['local'],capability:()=>({chat:true,efforts:[],thinking:[],modes:[],verbosity:false,temperature:'always',json:true,summary:false}),
  send:async(_model,messages)=>new Promise(resolve=>pending.set(messages[1].content.includes('job-one')?'old':'new',resolve))};
 const store={generation:1,value,publish(){},roles:{providers:{ollama:provider}},knowledge:{async call(action,input){
  if(action==='consolidation-attempt')return {state:input.operation,lease_until:new Date(Date.now()+30000).toISOString()};
  if(action==='consolidation-import')return {outcome:'no_change'};
  if(action==='consolidation-view')return {job:{packet:null}};
  throw Error(action);
 }}};
 const runner=new ConsolidationRunner(store,{copyText(){}});
 runner.preview=(job)=>({packet:{...packet,mode:'local',job,request_id:job},mode:'local'});
 const old=runner.send('job-one','job-one');
 await new Promise(resolve=>setImmediate(resolve));
 assert(pending.has('old'));
 store.generation++;value.project='project-two';runner.reset();
 const newer=runner.send('job-two','job-two');
 await new Promise(resolve=>setImmediate(resolve));
 assert(pending.has('new'));
 const current=runner.abort;
 pending.get('old')({text:'{}',incomplete:false});
 await assert.rejects(old,/cancelled/);
 assert.equal(runner.abort,current);assert.equal(runner.running,true);
 pending.get('new')({text:'{}',incomplete:false});
 await newer;runner.dispose();
});

test('forget immediately aborts curator and erases private packet and approved context',async()=>{
 let releaseForget,startedForget;const entered=new Promise(resolve=>{startedForget=resolve;});
 const value={project:'project-one',busy:false};const store={generation:1,value,publish(){}};
 const client={cancel(){},async run(_service,_root,{action}){
  if(action==='forget'){startedForget();return new Promise(resolve=>{releaseForget=()=>resolve({status:'ok',result:{knowledge:{forgotten:'conversation:one'}}});});}
  return {status:'ok',result:{knowledge:action==='status'?{enabled:true,generation:1}: {sources:[],edges:[],conversations:[]}}};
 }};
 const state=new KnowledgeState(store,client);store.knowledge=state;
 const runner=new ConsolidationRunner(store,{copyText(){}});store.consolidation=runner;
 value.knowledge={enabled:true,generation:1};value.knowledgeConsolidation={job:{packet}};value.knowledgeConsolidationContext={claims:[{id:'private'}]};
 runner.packet=packet;runner.abort=new AbortController();runner.running=true;
 const signal=runner.abort.signal,pending=state.run('forget','conversation:one');await entered;
 assert(signal.aborted);assert.equal(runner.packet,null);assert.equal(value.knowledgeConsolidation,null);assert.equal(value.knowledgeConsolidationContext,null);
 releaseForget();await pending;
 assert.equal(runner.packet,null);assert.equal(value.knowledgeConsolidationContext,null);
 runner.dispose();state.dispose();
});

test('unchanged background sync leaves an in-flight local send and frozen packet intact',async()=>{
 const value={project:'project-one',busy:false};const store={generation:1,value,publish(){}};
 const client={cancel(){},async run(_service,_root,{action}){assert.equal(action,'sync');return {status:'ok',result:{knowledge:{enabled:true,generation:7,policy:{},counts:{}}}};}};
 const state=new KnowledgeState(store,client);store.knowledge=state;
 const runner=new ConsolidationRunner(store,{copyText(){}});store.consolidation=runner;
 value.knowledge={enabled:true,generation:7};value.knowledgeConsolidation={job:{packet}};value.knowledgeConsolidationContext={generation:7,claims:[]};
 runner.packet=packet;runner.abort=new AbortController();runner.running=true;const signal=runner.abort.signal,epoch=runner.epoch;
 await state.run('sync','timer');
 assert(!signal.aborted);assert.equal(runner.packet,packet);assert.equal(runner.epoch,epoch);assert(value.knowledgeConsolidation);assert(value.knowledgeConsolidationContext);
 runner.dispose();state.dispose();
});

test('scope configuration clears approved context and aborts a send before backend completion',async()=>{
 let release,entered;const started=new Promise(resolve=>{entered=resolve;});
 const value={project:'project-one',busy:false},store={generation:1,value,publish(){}};
 const client={cancel(){},async run(_service,_root,{action}){if(action==='configure'){entered();return new Promise(resolve=>{release=()=>resolve({status:'ok',result:{knowledge:{enabled:true,generation:8,policy:{},counts:{}}}});});}return {status:'ok',result:{knowledge:{sources:[],edges:[],conversations:[]}}};}};
 const state=new KnowledgeState(store,client);store.knowledge=state;const runner=new ConsolidationRunner(store,{copyText(){}});store.consolidation=runner;
 value.knowledge={enabled:true,generation:7};value.knowledgeConsolidationContext={claims:[{id:'private'}]};runner.packet=packet;runner.abort=new AbortController();runner.running=true;
 const signal=runner.abort.signal,pending=state.run('configure',{scopes:['project']});await started;
 assert(signal.aborted);assert.equal(runner.packet,null);assert.equal(value.knowledgeConsolidationContext,null);
 release();await pending;runner.dispose();state.dispose();
});
