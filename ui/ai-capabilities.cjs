'use strict';
// Exact model IDs only. Official references and verification date: docs/panel-ai-roles.md.
const common={efforts:[],thinking:[],modes:[],verbosity:false,temperature:'never',json:false,summary:false,chat:null,embedding:null};
const catalog=Object.create(null);
function add(ids,efforts,extra={}){for(const id of ids)catalog[id]={...common,chat:true,embedding:false,efforts,json:true,...extra,source:'OpenAI documentation · 2026-09-24'};}
add(['gpt-6-astra'],['low','medium','high','xhigh','max'],{modes:['standard','pro'],summary:true});
add(['gpt-6-sol','gpt-6-luna','gpt-5.6','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna'],['none','low','medium','high','xhigh','max'],{modes:['standard','pro'],temperature:'none'});
add(['gpt-5.5','gpt-5.5-2026-04-23'],['none','low','medium','high','xhigh']);
add(['gpt-5.4','gpt-5.4-2026-03-05','gpt-5.2','gpt-5.2-2025-12-11'],['none','low','medium','high','xhigh'],{temperature:'none',verbosity:true});
add(['gpt-5.1','gpt-5.1-2025-11-13'],['none','low','medium','high'],{temperature:'none',verbosity:true});
add(['gpt-5','gpt-5-2025-08-07'],['minimal','low','medium','high'],{verbosity:true});
add(['gpt-4.1','gpt-4.1-2025-04-14'],[],{temperature:'always'});
function unknown(){return {...common,source:'Advanced capabilities unverified; provider defaults only'};}
function openaiCapabilities(model){return structuredClone(catalog[model]||unknown());}
function ollamaCapabilities(v){
 if(!v||!Array.isArray(v.capabilities)||v.capabilities.some(x=>typeof x!=='string'))throw Error('invalid_model_capabilities');
 const chat=v.capabilities.includes('completion'),embedding=v.capabilities.includes('embedding');
 let thinking=[];
 if(v.thinking!==undefined&&v.thinking!==null){if(!Array.isArray(v.thinking.values)||v.thinking.values.length>16||v.thinking.values.some(x=>typeof x!=='boolean'&&(typeof x!=='string'||!x||x.length>40||/[\x00-\x1f]/.test(x))))throw Error('invalid_model_capabilities');thinking=[...new Set(v.thinking.values)];}
 else if(v.capabilities.includes('thinking')){
  // Older servers lack the thinking.values field. Do not invent named levels.
  thinking=v.details?.family==='gptoss'?['low','medium','high']:[true];
 }
 return {...common,chat,embedding,thinking,temperature:chat?'always':'never',json:chat,source:v.thinking?'Ollama /api/show · exact thinking values':'Ollama /api/show · legacy metadata (limited thinking controls)'};
}
function generationDefaults(){return {effort:'default',thinking:null,reasoningMode:'default',verbosity:'default',temperature:null,format:'text',summary:false};}
function validGeneration(v){return v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()==='effort,format,reasoningMode,summary,temperature,thinking,verbosity'&&
 ['default','none','minimal','low','medium','high','xhigh','max'].includes(v.effort)&&
 (v.thinking===null||typeof v.thinking==='boolean'||(typeof v.thinking==='string'&&v.thinking.length>0&&v.thinking.length<=40&&!/[\x00-\x1f]/.test(v.thinking)))&&
 ['default','standard','pro'].includes(v.reasoningMode)&&['default','low','medium','high'].includes(v.verbosity)&&
 (v.temperature===null||(typeof v.temperature==='number'&&Number.isFinite(v.temperature)&&v.temperature>=0&&v.temperature<=2))&&['text','json'].includes(v.format)&&typeof v.summary==='boolean';}
function generationPayload(provider,caps,g){
 if(!validGeneration(g))throw Error('invalid_generation_options');
 if((g.effort!=='default'&&!caps.efforts.includes(g.effort))||(g.thinking!==null&&!caps.thinking.includes(g.thinking))||
 (g.reasoningMode!=='default'&&!caps.modes.includes(g.reasoningMode))||(g.verbosity!=='default'&&!caps.verbosity)||
 (g.temperature!==null&&!(caps.temperature==='always'||(caps.temperature==='none'&&g.effort==='none')))||(g.format==='json'&&!caps.json)||(g.summary&&!caps.summary))throw Error('role_option_unavailable');
 if(provider==='ollama'){if(g.effort!=='default'||g.reasoningMode!=='default'||g.verbosity!=='default'||g.summary)throw Error('role_option_unavailable');return {...(g.thinking!==null?{think:g.thinking}:{}),...(g.format==='json'?{format:'json'}:{}),...(g.temperature!==null?{options:{temperature:g.temperature}}:{})};}
 if(provider!=='openai'||g.thinking!==null)throw Error('role_option_unavailable');
 const reasoning={...(g.effort!=='default'?{effort:g.effort}:{}),...(g.reasoningMode!=='default'?{mode:g.reasoningMode}:{}),...(g.summary?{summary:'auto'}:{})};
 const text={...(g.verbosity!=='default'?{verbosity:g.verbosity}:{}),...(g.format==='json'?{format:{type:'json_object'}}:{})};
 return {...(Object.keys(reasoning).length?{reasoning}:{}),...(Object.keys(text).length?{text}:{}),...(g.temperature!==null?{temperature:g.temperature}:{})};
}
module.exports={unknown,openaiCapabilities,ollamaCapabilities,generationDefaults,validGeneration,generationPayload};
