/* Explicit local-model experiment; writes only to a new external workbench. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {AIProvider}=require('../ui/ai-provider.cjs');
const {generationDefaults}=require('../ui/ai-capabilities.cjs');
const {prompts,effectivePrompt}=require('../ui/role-prompts.cjs');
const {buildMessages,runPrepared}=require('../ui/knowledge-consolidation-runner.cjs');
const option=name=>process.argv.find(v=>v.startsWith('--'+name+'='))?.slice(name.length+3);
const repo=path.resolve(__dirname,'..');
const samples=[
 {id:'recorded-decision',question:'Summarize the approved production datastore decision.',
  files:{'decision.md':'# Approved datastore decision\nOn 2026-09-28 the team approved PostgreSQL for production storage. Redis is a cache, not the source of truth.\n'},
  expected:'A cited decision summary preserving PostgreSQL versus Redis scope.'},
 {id:'conflict',question:'Explain the conflicting production datastore records without deciding which one wins.',
  files:{'operations.md':'# Current operations decision\nThe production system of record is Redis. This record is approved and has not been superseded.\n',
         'architecture.md':'# Current architecture decision\nThe production system of record is PostgreSQL. This record is approved and has not been superseded.\n'},
  expected:'Explicit disputed evidence, without resolving the disagreement from recency.'},
 {id:'insufficient',question:'Which production datastore has been approved? If it cannot be established, return insufficient_evidence.',
  files:{'planning.md':'# Open design\nNo production datastore has been selected or approved. A future design review will compare candidates.\n'},
  expected:'Insufficient evidence; no invented approved datastore.'},
 {id:'approved-prior',question:'Compare the new Redis idea with the earlier approved production datastore decision. Do not promote speculation to a replacement decision.',
  files:{'decision.md':'# Production datastore decision\nThe team approved PostgreSQL as the production system of record.\n'},
  later:{'new-idea.md':'# Production datastore idea\nA participant wonders whether Redis could replace PostgreSQL someday. This is a speculative idea, not an approved change to the production datastore decision.\n'},
  expected:'Preserve the earlier approved PostgreSQL decision, label the Redis idea as speculation, cite original evidence.'},
];
function command(python,root,action,value=null,extra=[]){return new Promise((resolve,reject)=>{
 const child=spawn(python,['-I','-B',path.join(repo,'scripts/cc_knowledge.py'),'--project-root',root,...extra,action],{windowsHide:true,stdio:['pipe','pipe','pipe']});
 let output='',error='',over=false;
 const timer=setTimeout(()=>{child.kill();reject(Error('trial_backend_timeout'));},150000);
 child.stdout.on('data',data=>{output+=data;if(output.length>1048576){over=true;child.kill();}});
 child.stderr.on('data',data=>{error=(error+data).slice(-4000);});
 child.on('error',e=>{clearTimeout(timer);reject(e);});
 child.on('close',code=>{clearTimeout(timer);if(over)return reject(Error('trial_backend_output_limit'));
  let result;try{result=JSON.parse(output);}catch{return reject(Error('trial_backend_invalid_json: '+error));}
  if(code||result.error)return reject(Error(result.error||'trial_backend_exit_'+code));resolve(result);});
 child.stdin.end(JSON.stringify(value));
});}
async function main(){
 const python=option('python'),model=option('model'),output=option('output');
 const selected=option('case')?samples.filter(s=>s.id===option('case')):samples;
 if(!selected.length)throw Error('Unknown trial case');
 if(!python||!model||!output)throw Error('Provide --python=PATH --model=INSTALLED_MODEL --output=NEW_EXTERNAL_DIRECTORY');
 const target=path.resolve(output),relative=path.relative(repo,target);
 if(!relative||(!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative))||!path.relative(target,repo).startsWith('..'))throw Error('External destination required');
 fs.mkdirSync(target);const provider=new AIProvider();
 await provider.connect('ollama','');const capabilities=await provider.inspect(model);
 const report={model,provider:'ollama',capabilities,at:new Date().toISOString(),cases:[],
  human_review:'pending',scope:'Synthetic development corpus; not independent quality acceptance or live project configuration.'};
 const save=()=>fs.writeFileSync(path.join(target,'result.json'),JSON.stringify(report,null,2));save();
 for(const sample of selected){
  const folder=path.join(target,sample.id),root=path.join(folder,'project');fs.mkdirSync(path.join(root,'docs'),{recursive:true});
  for(const [name,text] of Object.entries(sample.files))fs.writeFileSync(path.join(root,'docs',name),text);
  const item={id:sample.id,question:sample.question,expected:sample.expected,success:false};report.cases.push(item);save();
  const call=(action,value=null)=>command(python,root,action,value);
  try{
   await call('configure',{scopes:['project'],automatic:false,worker:false,retention:'none',embedding:''});await call('sync');
   await command(python,root,'consolidation-migrate',null,['--archive',path.join(folder,'before.ccmemory')]);
   const generation={...generationDefaults(),format:'json',temperature:0};
   const config={provider:'ollama',model,generation,maxContextChars:12000,maxOutputTokens:4096,timeoutSeconds:120,maxRequests:3,
    roleRevision:crypto.createHash('sha256').update('local-trial-v1').digest('hex')};
   const prompt=effectivePrompt({systemPrompt:prompts.memory_curator,setupProposals:false,generation})+'\n\nSpecific review question: '+sample.question;
   if(sample.later){
    const seed=await call('consolidation-create',{title:'Fixture prior decision'});
    const seedPacket=await call('consolidation-prepare',{job:seed.job.id,prompt,config,mode:'manual'});
    const e=seedPacket.input_manifest[0],citation=Object.fromEntries(['source','path','revision','line','end_line','excerpt'].map(k=>[k,e[k]]));
    const response={protocol:1,job:seedPacket.job,request_id:seedPacket.request_id,manifest_digest:seedPacket.manifest_digest,outcome:'proposals',
     proposals:[{page_type:'overview',key:'production-datastore',title:'Approved production datastore',body:'The team approved PostgreSQL as the production system of record [S1].',kind:'decision_summary',epistemic_status:'decided',scope:'production',citations:[{...citation,citation:'S1'}],conflicting_evidence_ids:[],reason:'Controlled fixture seed',prerequisite_ids:[]}],
     used_evidence_ids:['S1'],unresolved_questions:[],coverage:{inspected:1,analyzed:1,deferred:0}};
    await call('consolidation-import',{job:seedPacket.job,request_id:seedPacket.request_id,text:JSON.stringify(response)});
    const seedView=await call('consolidation-view',{job:seedPacket.job});
    await call('consolidation-decide',{job:seedPacket.job,ids:seedView.job.proposals.map(p=>p.id),decision:'accept',request_id:'fixture_seed_approval'});
    item.syntheticApprovedSeed=true;
    for(const [name,text] of Object.entries(sample.later))fs.writeFileSync(path.join(root,'docs',name),text);
    await call('sync');
   }
   const view=await call('consolidation-create',{title:sample.question.slice(0,120)});
   const packet=await call('consolidation-prepare',{job:view.job.id,prompt,config,mode:'local'});
   if(sample.later&&!packet.approved_context?.length)throw Error('trial_prior_context_missing');
   const messages=buildMessages(packet);item.contextCharacters=messages.reduce((n,m)=>n+m.content.length,0);
   fs.writeFileSync(path.join(folder,'request.json'),JSON.stringify({packet,messages},null,2));
   const send=provider.send.bind(provider);let actual;
   provider.send=async(...args)=>{actual=await send(...args);fs.writeFileSync(path.join(folder,'reply.json'),JSON.stringify(actual,null,2));return actual;};
   const start=performance.now();
   try{item.result=await runPrepared({call,provider,packet,owner:'trial_'+sample.id});}
   finally{item.seconds=(performance.now()-start)/1000;provider.send=send;}
   item.view=await call('consolidation-view',{job:view.job.id});
   item.success=true;item.claimsPublished=item.view.job.proposals.some(p=>p.status==='accepted');
   item.outcome=JSON.parse(actual.text).outcome;
  }catch(error){item.error=String(error.message);}
  save();console.log(JSON.stringify({case:item.id,success:item.success,outcome:item.outcome,error:item.error,seconds:item.seconds}));
 }
 report.completed=true;report.operationalSuccesses=report.cases.filter(c=>c.success).length;save();
 if(report.operationalSuccesses!==selected.length)process.exitCode=1;
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
