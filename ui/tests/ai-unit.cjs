'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http');
const {AIProvider,requestJSON}=require('../ai-provider.cjs');
const {AISession}=require('../ai-session.cjs');
test('HTTP bounds, errors and abort are real transport behavior',async()=>{
 const server=http.createServer((req,res)=>{if(req.url==='/ok')res.end(JSON.stringify({ok:true}));else if(req.url==='/redirect'){res.writeHead(302,{Location:'/ok'});res.end();}else if(req.url==='/large')res.end('x'.repeat(600000));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 try{assert.deepEqual(await requestJSON(base+'/ok'),{ok:true});await assert.rejects(requestJSON(base+'/redirect'),/http_302/);await assert.rejects(requestJSON(base+'/large'),/output_limit/);const c=new AbortController();const p=requestJSON(base+'/wait',{signal:c.signal});c.abort();await assert.rejects(p,/cancelled/);}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});
test('OpenAI adapter never sends credentials in payload or model state',async()=>{
 const calls=[];const p=new AIProvider(async(url,options)=>{calls.push({url,options});return url.endsWith('/models')?{data:[{id:'fixture-model'}]}:{status:'completed',output:[{type:'message',content:[{type:'output_text',text:'Advisory answer'}]}]};});
 await p.connect('openai','SECRET',undefined);const result=await p.send('fixture-model',[{role:'user',content:'Reviewed input'}]);
 assert.equal(result.text,'Advisory answer');assert.equal(calls[1].options.body.store,false);assert.equal(calls[1].options.body.max_output_tokens,2048);assert(!JSON.stringify(calls[1].options.body).includes('SECRET'));assert.equal(calls[1].options.key,'SECRET');p.clear();assert.equal(p.key,'');
});
test('Ollama adapter sends reviewed messages without tools or streaming',async()=>{
 const calls=[];const p=new AIProvider(async(url,options)=>{calls.push({url,options});return url.endsWith('/api/tags')?{models:[{name:'fixture'}]}:{done:true,message:{content:'Answer'}};});
 await p.connect('ollama','');await p.send('fixture',[{role:'user',content:'Question'}]);assert.equal(calls[1].options.body.stream,false);assert.equal(calls[1].options.key,undefined);assert.equal(calls[1].options.body.tools,undefined);await assert.rejects(p.send('unknown',[]),/model_not_connected/);
});
function store(){return {generation:1,value:{project:'P',busy:false,activity:[],configuration:null},publish(){},client:{async run(){return {status:'error',error:{code:'memory_not_initialized'}}}},async observeWork(){}};}
test('AI preview does not call provider, edits invalidate send, failed archive retains reply',async()=>{
 const s=store();let calls=0;const provider={provider:'ollama',models:['fixture'],async send(){calls++;return {text:'Answer',incomplete:false}}};const ai=new AISession(s,provider);ai.reset();const v={model:'fixture',role:'advisor',prompt:'Question',context:'none',archive:true};ai.prepare(v);assert.equal(calls,0);ai.discard();await ai.send();assert.equal(calls,0);ai.prepare(v);await ai.send();assert.equal(calls,1);assert.equal(s.value.aiMessages[1].content,'Answer');assert.equal(s.value.aiArchive.saved,false);assert(!s.value.busy);assert(!s.value.committing);assert(!JSON.stringify(s.value).includes('request_id'));
});
test('Project switch clears conversation and approval while retaining provider connection only',()=>{
 const s=store(),ai=new AISession(s,{provider:'ollama',models:['fixture']});ai.prepare({model:'fixture',role:'reviewer',prompt:'Private',context:'none',archive:false});s.value.project='Q';s.generation++;ai.reset();assert.equal(ai.pending,null);assert.equal(s.value.aiPreview,null);assert.deepEqual(s.value.aiMessages,[]);assert.equal(s.value.aiConnection,'ollama');
});
