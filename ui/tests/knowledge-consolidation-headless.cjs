'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const {RoleStorage,defaults}=require('../role-config.cjs');
const {effectivePrompt}=require('../role-prompts.cjs');
const {main}=require('../consolidation-headless.cjs');

test('headless local prepared packet reaches Python import; role drift and API packets never send',async()=>{
 const base=process.env.CC_HEADLESS_TEST_ROOT,python=process.env.CC_HEADLESS_PYTHON;
 assert(base&&python,'Use an external workbench fixture and an installed Python runtime');
 const root=path.join(base,'project'),profile=path.join(base,'profile'),core=path.resolve(__dirname,'../..');
 fs.mkdirSync(path.join(root,'docs'),{recursive:true});fs.writeFileSync(path.join(root,'docs','decision.md'),'# Decision\nPostgreSQL is the selected durable store.\n');
 const cli=(action,value=null,extra=[])=>{
  const child=spawnSync(python,['-I','-B',path.join(core,'scripts','cc_knowledge.py'),'--project-root',root,...extra,action],{input:JSON.stringify(value),encoding:'utf8',timeout:150000});
  if(child.error)throw child.error;const output=JSON.parse(child.stdout);assert.equal(child.status,0,output.error||child.stderr);return output;
 };
 cli('configure',{scopes:['project'],automatic:false,worker:false,retention:'none',embedding:''});cli('sync');
 cli('consolidation-migrate',null,['--archive',path.join(base,'before.ccmemory')]);
 const storage=new RoleStorage(profile),roles=defaults();Object.assign(roles.memory_curator,{enabled:true,model:'mock-local'});
 const revision=storage.save(root,roles,null),role=roles.memory_curator;
 const config={provider:role.provider,model:role.model,generation:role.generation,maxContextChars:role.maxContextChars,maxOutputTokens:role.maxOutputTokens,timeoutSeconds:role.timeoutSeconds,maxRequests:role.maxRequests,roleRevision:revision};
 const job=cli('consolidation-create',{title:'Headless local review'}).job.id;
 const packet=cli('consolidation-prepare',{job,prompt:effectivePrompt(role),config,mode:'local'});
 const apiJob=cli('consolidation-create',{title:'API path rejected by local runner'}).job.id;
 const apiPacket=cli('consolidation-prepare',{job:apiJob,prompt:effectivePrompt(role),config:{...config,provider:'openai',model:'mock-api'},mode:'api'});
 let sends=0,connects=0;
 const provider={provider:'ollama',models:[],async connect(){connects++;this.models=['mock-local'];},async inspect(){return this.capability();},capability(){return {chat:true,efforts:[],thinking:[],modes:[],verbosity:false,temperature:'always',json:true,summary:false};},async send(){sends++;return {text:JSON.stringify({protocol:1,job,request_id:packet.request_id,manifest_digest:packet.manifest_digest,outcome:'no_change',proposals:[],used_evidence_ids:[],unresolved_questions:[],coverage:{inspected:packet.input_manifest.length,analyzed:packet.input_manifest.length,deferred:0}}),incomplete:false,usage:null};}};
 const args=['--project='+root,'--python='+python,'--core='+core,'--job='+job,'--profile='+profile];
 const result=await main(args,provider);assert.equal(result.outcome,'no_change');assert.equal(sends,1);
 const view=cli('consolidation-view',{job});assert.equal(view.job.state,'analyzed');assert.equal(view.job.attempts.at(-1).outcome,'no_change');
 const altered=structuredClone(roles);altered.memory_curator.systemPrompt+=' Changed.';storage.save(root,altered,revision);
 await assert.rejects(main(args,provider),/consolidation_role_changed/);assert.equal(sends,1);assert.equal(connects,1);
 await assert.rejects(main(args.map(a=>a.startsWith('--job=')?'--job='+apiJob:a),provider),/local_prepared_packet_required/);
 assert.equal(apiPacket.mode,'api');assert.equal(sends,1);
});
