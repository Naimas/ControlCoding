'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {KnowledgeOutbox}=require('../knowledge-outbox.cjs');
const {KnowledgeState}=require('../knowledge-state.cjs');
const base=process.env.CC_ROLES_TEST_ROOT;
test('durable pending events recover after restart without crossing project roots',{skip:!base},async()=>{
 const parent=fs.mkdtempSync(path.join(base,'outbox-'));const root=path.join(parent,'project'),profile=path.join(parent,'profile'),other=path.join(parent,'other');
 for(const dir of [root,profile,other])fs.mkdirSync(dir);
 const state={schema_version:1,enabled:true,generation:1,policy:{automatic:false,retention:'transcript'},counts:{}};
 const store=()=>({generation:1,value:{project:root,busy:false},publish(){}});
 let offline=true;const writes=[];
 const client={cancel(){},async run(_op,_root,{action,value}){if(action==='conversation'){writes.push(value);if(offline)throw Error('offline');}return {status:'ok',result:{knowledge:action==='conversation'?{saved:true}:state}};}};
 const first=new KnowledgeState(store(),client,{profile});await first.run('status');
 await first.archive('concierge',[{role:'user',content:'Pending private turn'}]);const original=writes[0];first.dispose();
 const outbox=new KnowledgeOutbox(profile);assert.equal(outbox.load(root).length,1);assert.equal(outbox.load(other).length,0);
 const second=new KnowledgeState(store(),client,{profile});await second.run('status');
 assert.equal(second.store.value.knowledgeArchive.error,'pending_turns_recovered');offline=false;await second.retryArchive();
 assert.deepEqual(writes[1],original);assert.equal(outbox.load(root).length,0);second.dispose();
});
test('summary retention never persists transcript turns in the outbox',{skip:!base},async()=>{
 const parent=fs.mkdtempSync(path.join(base,'summary-')),root=path.join(parent,'project'),profile=path.join(parent,'profile');fs.mkdirSync(root);fs.mkdirSync(profile);
 const store={generation:1,value:{project:root,knowledge:null},publish(){}};
 const memory=new KnowledgeState(store,{cancel(){},async run(){throw Error('offline');}},{profile});
 store.value.knowledge={enabled:true,policy:{retention:'summary'}};
 await memory.archive('concierge',[{role:'assistant',content:'Transcript must not be retained'}]);
 const entries=new KnowledgeOutbox(profile).load(root);assert.equal(entries.length,1);assert.deepEqual(entries[0].value.turns,[]);
 assert(!JSON.stringify(entries).includes('Transcript must not be retained'));memory.dispose();
});
