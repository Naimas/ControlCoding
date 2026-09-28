'use strict';
const http=require('node:http'),https=require('node:https');
const {unknown,openaiCapabilities,ollamaCapabilities,generationDefaults,generationPayload}=require('./ai-capabilities.cjs');
function requestJSON(url,{method='GET',body,key,signal,timeout=120000}={}){
 return new Promise((resolve,reject)=>{
  let size=0,chunks=[],settled=false;const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value);};
  const data=body===undefined?null:Buffer.from(JSON.stringify(body));
  const req=(url.startsWith('https:')?https:http).request(url,{method,signal,headers:{'Accept':'application/json',...(data?{'Content-Type':'application/json','Content-Length':data.length}:{}),...(key?{Authorization:'Bearer '+key}:{})}},res=>{
   if(res.statusCode!==200){res.resume();finish(Error('provider_http_'+res.statusCode));return;}
   res.on('data',chunk=>{size+=chunk.length;if(size>512*1024){req.destroy();finish(Error('provider_output_limit'));}else chunks.push(chunk);});
   res.on('end',()=>{try{finish(null,JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{finish(Error('invalid_provider_response'));}});
   res.on('error',()=>finish(Error('provider_unavailable')));
  });
  const timer=setTimeout(()=>{req.destroy();finish(Error('provider_timeout'));},timeout);
  req.on('error',()=>finish(Error(signal?.aborted?'cancelled':'provider_unavailable')));req.end(data);
 });
}
const endpoints={ollama:'http://127.0.0.1:11434',openai:'https://api.openai.com/v1'};
class AIProvider{
 constructor(request=requestJSON){this.request=request;this.key='';this.provider=null;this.models=[];this.capabilities=Object.create(null);}
 clear(){this.key='';this.provider=null;this.models=[];this.capabilities=Object.create(null);}
 capability(model){return this.capabilities[model]||(this.provider==='openai'?openaiCapabilities(model):unknown());}
 async inspect(model,signal){
  if(!this.provider||!this.models.includes(model))throw Error('model_not_connected');
  delete this.capabilities[model];
  const caps=this.provider==='openai'?openaiCapabilities(model):ollamaCapabilities(await this.request(endpoints.ollama+'/api/show',{method:'POST',body:{model},signal,timeout:10000}));
  this.capabilities[model]=caps;return caps;
 }
 async connect(provider,key,signal){
  if(!Object.hasOwn(endpoints,provider)||typeof key!=='string'||key.length>512||/[\r\n]/.test(key))throw Error('invalid_provider');
  this.clear();if(provider==='openai'&&!key.trim())throw Error('missing_api_key');
  const r=await this.request(endpoints[provider]+(provider==='ollama'?'/api/tags':'/models'),{key:provider==='openai'?key:undefined,signal,timeout:10000});
  const list=provider==='ollama'?r.models?.map(m=>m.name):r.data?.map(m=>m.id);
  if(!Array.isArray(list)||list.some(m=>typeof m!=='string'||m.length>160))throw Error('invalid_provider_response');
  this.provider=provider;this.key=provider==='openai'?key:'';this.models=[...new Set(list)].slice(0,200).sort();return this.models;
 }
 async send(model,messages,signal,limits={}){
  if(!this.provider||!this.models.includes(model))throw Error('model_not_connected');
  const tokens=limits.maxOutputTokens??2048,timeout=limits.timeoutMs??120000,maxTextChars=limits.maxTextChars??12000;
  if(!Number.isInteger(tokens)||tokens<128||tokens>8192||!Number.isInteger(timeout)||timeout<5000||timeout>120000||!Number.isInteger(maxTextChars)||maxTextChars<12000||maxTextChars>48000)throw Error('invalid_ai_limits');
  const caps=this.capability(model);if(caps.chat===false)throw Error('role_model_incompatible');
  const extra=generationPayload(this.provider,caps,limits.generation||generationDefaults());
  const body=this.provider==='ollama'?{model,messages,stream:false,...extra,options:{...extra.options,num_predict:tokens}}:{model,input:messages,store:false,max_output_tokens:tokens,...extra};
  const r=await this.request(endpoints[this.provider]+(this.provider==='ollama'?'/api/chat':'/responses'),{method:'POST',body,key:this.key||undefined,signal,timeout});
  const result=this.provider==='ollama'?r.message?.content:r.output?.filter(m=>m.type==='message').flatMap(m=>m.content||[]).filter(c=>c.type==='output_text').map(c=>c.text).join('\n');
  const incomplete=this.provider==='openai'?r.status!=='completed':r.done!==true||r.done_reason==='length';
  if(incomplete&&(typeof result!=='string'||!result.trim()))throw Error('provider_no_final_text_within_budget');
  if(typeof result!=='string'||!result.trim()||result.length>maxTextChars)throw Error('invalid_provider_response');
  if(limits.generation?.format==='json'&&!incomplete){let parsed;try{parsed=JSON.parse(result);}catch{throw Error('invalid_json_output');}if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw Error('invalid_json_output');}
  const summary=this.provider==='openai'&&limits.generation?.summary?r.output?.filter(m=>m.type==='reasoning').flatMap(m=>m.summary||[]).filter(c=>c.type==='summary_text'&&typeof c.text==='string').map(c=>c.text).join('\n'):null;
  if(summary&&summary.length>12000)throw Error('provider_output_limit');
  const numbers=this.provider==='ollama'?[r.prompt_eval_count,r.eval_count]:[r.usage?.input_tokens,r.usage?.output_tokens];
  const usage=numbers.every(n=>Number.isSafeInteger(n)&&n>=0)?{input_tokens:numbers[0],output_tokens:numbers[1]}:null;
  return {text:result,summary:summary||null,incomplete,usage};
 }
 async embed(model,input,signal,timeout=120000){
  if(!this.provider||!this.models.includes(model))throw Error('model_not_connected');
  if(this.capability(model).embedding===false)throw Error('role_model_incompatible');
  if(typeof input!=='string'||!input.trim()||input.length>8000||!Number.isInteger(timeout)||timeout<5000||timeout>120000)throw Error('invalid_embedding_request');
  const body=this.provider==='ollama'?{model,input,truncate:false}:{model,input,encoding_format:'float'};
  const r=await this.request(endpoints[this.provider]+(this.provider==='ollama'?'/api/embed':'/embeddings'),{method:'POST',body,key:this.key||undefined,signal,timeout});
  const vector=this.provider==='ollama'?r.embeddings?.[0]:r.data?.[0]?.embedding;
  if(!Array.isArray(vector)||!vector.length||vector.length>16384||!vector.every(Number.isFinite))throw Error('invalid_embedding_response');
  return vector;
 }
}
module.exports={AIProvider,requestJSON,endpoints};
