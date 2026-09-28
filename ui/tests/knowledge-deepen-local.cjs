'use strict';
// Explicit loopback probe against a preconfigured disposable project.
const fs=require('node:fs'),path=require('node:path');
const {KnowledgeState}=require('../knowledge-state.cjs');
const {BridgeClient}=require('../bridge-client.cjs');
const {AIProvider}=require('../ai-provider.cjs');
const {generationDefaults}=require('../ai-capabilities.cjs');
const opt=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3);
async function main(){
 const output=path.resolve(opt('output')),repo=path.resolve(__dirname,'../..');
 if(fs.existsSync(output)||output.toLowerCase().startsWith(repo.toLowerCase()+path.sep))throw Error('Use new external output');
 const provider=new AIProvider(),model=opt('model');await provider.connect('ollama','');await provider.inspect(model);
 const client=new BridgeClient(opt('python'),path.join(repo,'scripts/cc_panel_bridge.py'));
 const store={generation:1,value:{project:path.resolve(opt('project')),busy:false},publish(){}};
 const memory=new KnowledgeState(store,client),requests=[];
 const send=provider.send.bind(provider);provider.send=async(...args)=>{const started=Date.now();const result=await send(...args);requests.push({messages:args[1],result,elapsed_ms:Date.now()-started});return result;};
 store.roles={counts:{},providers:{ollama:provider}};
 store.value.roleConfig={concierge:{enabled:true,mode:'direct',provider:'ollama',model,maxRequests:3,maxOutputTokens:4096,timeoutSeconds:60,generation:generationDefaults()}};
 try{
  await memory.run('query',{text:'When did restoration finish?',semantic:false});
  const initial=structuredClone(store.value.knowledgeQuery);if(!initial)throw Error(store.value.knowledgeError);
  await memory.deepen();const searchError=store.value.knowledgeError,followup=structuredClone(store.value.knowledgeQuery);
  if(!searchError&&followup.citations.length)await memory.answer();
  fs.writeFileSync(output,JSON.stringify({model,scope:'Synthetic authorized project, actual loopback model; not quality acceptance',initial,followup,searchError,answer:store.value.knowledgeAnswer,error:store.value.knowledgeError,requests},null,2));
  console.log(JSON.stringify({initial:initial.citations.length,followup:followup?.citations.length,searchError,answer:store.value.knowledgeAnswer,error:store.value.knowledgeError}));
 }finally{memory.dispose();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
