'use strict';
// Explicit loopback-only probe; calibration by default, frozen challenge by opt-in.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {AIProvider}=require('../ai-provider.cjs');
const {INSTRUCTION,validateAnswer}=require('../knowledge-answer.cjs');
const {generationDefaults}=require('../ai-capabilities.cjs');
const opt=name=>process.argv.find(a=>a.startsWith('--'+name+'='))?.slice(name.length+3);
async function main(){
 const corpus=JSON.parse(fs.readFileSync(opt('corpus'),'utf8'));
 const run=JSON.parse(fs.readFileSync(opt('run'),'utf8'));
 const model=opt('model');if(!model)throw Error('Specify an installed local model');
 const output=path.resolve(opt('output'));
 const repo=path.resolve(__dirname,'../..');
 if(fs.existsSync(output)||output.toLowerCase().startsWith(repo.toLowerCase()+path.sep))throw Error('Use new external output file');
 const provider=new AIProvider();await provider.connect('ollama','');await provider.inspect(model);
 const split=opt('split')||'calibration';if(!['calibration','held_out'].includes(split))throw Error('Invalid split');
 const positive=corpus.questions.filter(q=>q.split===split&&q.answerable).slice(0,2);
 const negative=corpus.questions.filter(q=>q.split===split&&!q.answerable);
 const selected=opt('case')?[...positive,...negative].filter(q=>q.id===opt('case')):[...positive,...negative];
 if(!selected.length)throw Error('Requested case is not in this probe');
 const maxOutputTokens=Number(opt('tokens')||2048);
 const results=[];
 for(const q of selected){
  const evidence=run.results.find(r=>r.id===q.id);if(!evidence)throw Error('Missing calibration evidence');
  const prompt='Question: '+q.question+'\n\nEvidence:\n'+evidence.citations.map(c=>'['+c.id+'] '+c.path+' @ '+c.revision+' | evidence kind: '+c.kind+'\n'+c.excerpt).join('\n\n');
  const started=Date.now();let observation;
  try{
   const raw=await provider.send(model,[{role:'system',content:INSTRUCTION},{role:'user',content:prompt}],undefined,
    {maxOutputTokens,timeoutMs:120000,generation:generationDefaults()});
   observation={raw,...validateAnswer(raw.text,evidence.citations,raw.incomplete)};
  }catch(error){observation={error:error.message};}
  results.push({id:q.id,answerable:q.answerable,elapsed_ms:Date.now()-started,...observation});
  fs.writeFileSync(output,JSON.stringify({model,maxOutputTokens,split,scope:'Two first answerable cases and all negatives in the declared split; no human entailment or acceptance',
   run_file_sha256:crypto.createHash('sha256').update(fs.readFileSync(opt('run'))).digest('hex'),results},null,2));
  process.stdout.write(JSON.stringify({id:q.id,elapsed_ms:Date.now()-started,abstained:observation.abstained,error:observation.error})+'\n');
 }
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
