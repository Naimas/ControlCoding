#!/usr/bin/env node
'use strict';
// Explicit local-only execution of one already prepared job. No remote credentials or fallback.
const {execFile}=require('node:child_process');
const path=require('node:path');
const {AIProvider}=require('./ai-provider.cjs');
const {runPrepared}=require('./knowledge-consolidation-runner.cjs');
const {RoleStorage}=require('./role-config.cjs');
const {effectivePrompt}=require('./role-prompts.cjs');
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
function options(args){const o={};for(const arg of args){const m=/^--(project|python|core|job|profile)=(.+)$/.exec(arg);if(!m||Object.hasOwn(o,m[1]))throw Error('invalid_argument');o[m[1]]=m[2];}
 if(['project','python','core','job','profile'].some(k=>!o[k])||!path.isAbsolute(o.project)||!path.isAbsolute(o.python)||!path.isAbsolute(o.core)||!path.isAbsolute(o.profile))throw Error('invalid_argument');return o;}
async function main(args=process.argv.slice(2),provider=new AIProvider()){
 const o=options(args),script=path.join(o.core,'scripts','cc_knowledge.py');
 const call=(action,value)=>new Promise((resolve,reject)=>execFile(o.python,['-I','-B',script,'--project-root',o.project,action],
  {input:JSON.stringify(value),windowsHide:true,timeout:150000,maxBuffer:262144},(error,stdout)=>{
   let data;try{data=JSON.parse(stdout);}catch{return reject(Error('invalid_backend_response'));}
   if(error||data.error)return reject(Error(data.error||'backend_failed'));resolve(data);
  }).stdin.end(JSON.stringify(value)));
 const view=await call('consolidation-view',{job:o.job}),packet=view.job?.packet;
 if(!packet||packet.mode!=='local'||packet.config?.provider!=='ollama')throw Error('local_prepared_packet_required');
 const saved=new RoleStorage(o.profile).read(o.project),role=saved.roles.memory_curator;
 if(!role.enabled||role.provider!=='ollama'||saved.revision!==packet.config.roleRevision||effectivePrompt(role)!==packet.prompt||
  canonical({provider:role.provider,model:role.model,generation:role.generation,maxContextChars:role.maxContextChars,maxOutputTokens:role.maxOutputTokens,timeoutSeconds:role.timeoutSeconds,maxRequests:role.maxRequests,roleRevision:saved.revision})!==canonical(packet.config))throw Error('consolidation_role_changed');
 await provider.connect('ollama','');if(!provider.models.includes(packet.config.model))throw Error('model_not_connected');await provider.inspect(packet.config.model);
 return runPrepared({call,provider,packet});
}
if(require.main===module)main().then(result=>process.stdout.write(JSON.stringify({ok:true,result})+'\n'),error=>{process.stderr.write(JSON.stringify({error:error.message})+'\n');process.exitCode=1;});
module.exports={main,options};
