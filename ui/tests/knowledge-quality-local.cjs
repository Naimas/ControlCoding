'use strict';
// Explicit local evaluation; frozen runtime and copied corpus, no live archive.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const opt=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3);
const digest=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const {execFileSync}=require('node:child_process');
function corpusIdentity(){
 const script="import sqlite3,json,hashlib,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); tables={t:c.execute('SELECT '+cols+' FROM '+t+' ORDER BY 1').fetchall() for t,cols in [('sources','id,path,revision,body,kind,deleted'),('chunks','id,source,revision,line,end_line,text'),('edges','source,target,revision,kind'),('conversations','id,title,retention,summary,status'),('turns','id,conversation,sequence,role,content,provenance')]}; print(json.dumps({'sha256':hashlib.sha256(json.dumps(tables,sort_keys=True).encode()).hexdigest(),'counts':{t:len(rows) for t,rows in tables.items()}}))";
 return JSON.parse(execFileSync(opt('python'),['-c',script,path.join(path.resolve(opt('project')),'.controlcoding/knowledge/knowledge.db')],{encoding:'utf8',windowsHide:true}));
}
async function main(){
 const runtime=path.resolve(opt('runtime')),output=path.resolve(opt('output'));
 if(fs.existsSync(output))throw Error('Use new output directory');fs.mkdirSync(output,{recursive:true});
 const {KnowledgeState}=require(path.join(runtime,'ui/knowledge-state.cjs'));
 const {BridgeClient}=require(path.join(runtime,'ui/bridge-client.cjs'));
 const {AIProvider}=require(path.join(runtime,'ui/ai-provider.cjs'));
 const {generationDefaults}=require(path.join(runtime,'ui/ai-capabilities.cjs'));
 const corpus=JSON.parse(fs.readFileSync(opt('corpus'),'utf8'));
 const requestedIds=opt('question-ids')?.split(',');
 if(requestedIds&&(requestedIds.length>12||new Set(requestedIds).size!==requestedIds.length||requestedIds.some(id=>!corpus.questions.some(q=>q.id===id))))throw Error('Invalid diagnostic subset');
 if(!requestedIds&&corpus.questions.length<120)throw Error('At least 120 questions required');
 const selectedQuestions=requestedIds?corpus.questions.filter(q=>requestedIds.includes(q.id)):corpus.questions;
 const provider=new AIProvider(),model=opt('model');await provider.connect('ollama','');await provider.inspect(model);
 const client=new BridgeClient(opt('python'),path.join(runtime,'scripts/cc_panel_bridge.py'),{timeout:45000});
 const store={generation:1,value:{project:path.resolve(opt('project')),busy:false},publish(){}};
 const memory=new KnowledgeState(store,client);let requests=[];
 // Evaluation-only isolation: generated answers must never become benchmark evidence.
 memory.archive=async()=>false;
 const frozenIdentity=corpusIdentity();
 const send=provider.send.bind(provider);provider.send=async(...args)=>{const started=Date.now();try{const result=await send(...args);requests.push({result,elapsed_ms:Date.now()-started});return result;}catch(e){requests.push({error:e.message,elapsed_ms:Date.now()-started});throw e;}};
 store.roles={counts:{},providers:{ollama:provider}};
 const saved=opt('config')?JSON.parse(fs.readFileSync(opt('config'),'utf8')).roles.concierge:null;
 if(saved&&(saved.provider!=='ollama'||saved.model!==model))throw Error('Configuration/model mismatch');
 store.value.roleConfig={concierge:{...(saved||{maxOutputTokens:4096,timeoutSeconds:120,generation:generationDefaults()}),enabled:true,mode:'direct',provider:'ollama',model,maxRequests:300}};
 const results=[],meta={model,config_sha256:opt('config')?digest(opt('config')):null,timeoutSeconds:store.value.roleConfig.concierge.timeoutSeconds,maxOutputTokens:store.value.roleConfig.concierge.maxOutputTokens,corpus_sha256:digest(opt('corpus')),runtime_manifest_sha256:digest(path.join(runtime,'MANIFEST.json')),scope:'Exposed development corpus; no blind acceptance or human grading; isolated evaluation request quota 300',followup_policy:'First 12 initial abstentions or empty retrievals; one bounded round, explicit evaluator-authorized answer after new preview'};
 Object.assign(meta,{archive_policy:'Disabled in evaluator only; product retention unchanged',frozenIdentity});
 if(requestedIds)Object.assign(meta,{scope:'Explicit diagnostic subset; not a complete quality run or human acceptance',selected_ids:requestedIds});
 let followups=0;
 try{
  for(const q of selectedQuestions){
   requests=[];const started=Date.now();
   await memory.run('query',{text:q.question,semantic:true});
   const initial=structuredClone(store.value.knowledgeQuery);let initialAnswer=null;
   if(initial?.citations.length){await memory.answer();initialAnswer=structuredClone(store.value.knowledgeAnswer);}
   const initialError=store.value.knowledgeError;let followup=null,finalAnswer=initialAnswer;
   if(initial&&!initialError&&(!initial.citations.length||initialAnswer?.abstained)&&followups<12){
    followups++;await memory.deepen();followup=structuredClone(store.value.knowledgeQuery);
    if(!store.value.knowledgeError&&followup?.followup?.new_passages>0){await memory.answer();finalAnswer=structuredClone(store.value.knowledgeAnswer);}
   }
   const observedIdentity=corpusIdentity();
   if(observedIdentity.sha256!==frozenIdentity.sha256){fs.writeFileSync(path.join(output,'INVALID.json'),JSON.stringify({reason:'Frozen corpus changed',expected:frozenIdentity,actual:observedIdentity}));throw Error('Frozen corpus changed');}
   results.push({id:q.id,question:q.question,initial,initialAnswer,initialError,followup,finalAnswer,error:store.value.knowledgeError,requests,elapsed_ms:Date.now()-started});
   fs.writeFileSync(path.join(output,'responses.json'),JSON.stringify({...meta,followups,results},null,2));
   console.log(JSON.stringify({completed:results.length,total:selectedQuestions.length,id:q.id,abstained:finalAnswer?.abstained,error:store.value.knowledgeError,followups}));
  }
 }finally{memory.dispose();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
