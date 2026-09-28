'use strict';
const {AIProvider,endpoints}=require('./ai-provider.cjs');
const {ROLES,defaults,validRoles,RoleStorage,projectIdentity}=require('./role-config.cjs');
const {ManualHandoff}=require('./manual-handoff.cjs');
const {memoryContext,revalidateMemory}=require('./knowledge-context.cjs');
const {createHash}=require('node:crypto');
const promptTemplates=require('./role-prompts.cjs');
const {generationPayload,generationDefaults,unknown}=require('./ai-capabilities.cjs');
const digest=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const safeError=e=>/^(provider_[a-z_0-9]+|invalid_[a-z_]+|model_not_connected|missing_api_key|role_[a-z_]+|cancelled)$/.test(e?.message)?e.message:'role_operation_failed';
class AIRoles{
 constructor(store,{profile,storage,providers,copyText}={}){this.store=store;this.storage=storage||new RoleStorage(profile);this.providers=providers||{ollama:new AIProvider(),openai:new AIProvider()};this.countsByProject=new Map();this.manual=new ManualHandoff(this,copyText);this.reset();}
 reset(){this.abort?.abort();this.pending=null;this.resultBinding=null;this.histories={};const project=this.store.value.project?projectIdentity(this.store.value.project):null;this.counts=this.countsByProject.get(project)||{};this.countsByProject.set(project,this.counts);this.revision=null;
  let roles=defaults(),error=null;
  if(this.store.value.project)try{const r=this.storage.read(this.store.value.project);roles=r.roles;this.revision=r.revision;}catch{error='invalid_role_file';}
  this.manual.reset();this.loadError=error;this.eventId=0;Object.assign(this.store.value,{roleConfig:roles,roleConfigError:error,roleExecution:null,roleEvents:[],roleConfigVersion:(this.store.value.roleConfigVersion||0)+1,roleSaved:false,roleError:error,rolePreview:null,roleResult:null,roleCounts:{...this.counts}});this.connections();
 }
 event(role,kind,provider=null){const v=this.store.value;v.roleEvents=[...(v.roleEvents||[]),{id:++this.eventId,at:new Date().toISOString(),role,kind,provider}].slice(-40);}
 connections(){const {effectivePrompt,...templates}=promptTemplates;this.store.value.rolePromptTemplates={...templates,generationDefaults:generationDefaults()};this.store.value.roleConnections=Object.fromEntries(Object.entries(this.providers).map(([id,p])=>[id,{connected:p.provider===id,models:[...p.models],capabilities:Object.fromEntries(p.models.map(model=>[model,p.capability?.(model)||unknown()]))}]));}
 async inspect(id,model){const s=this.store,v=s.value;if(v.busy)return;if(!Object.hasOwn(this.providers,id)||typeof model!=='string')throw Error('invalid_provider');
  this.discard();this.abort=new AbortController();v.busy=true;v.committing=true;v.roleError=null;s.publish();
  try{await this.providers[id].inspect(model,this.abort.signal);}catch(e){v.roleError=safeError(e);}finally{this.abort=null;v.busy=false;v.committing=false;this.connections();s.publish();}
 }
 discard(){if(this.store.value.busy)return;this.pending=null;this.store.value.rolePreview=null;this.store.publish();}
 async connect(id,key){const s=this.store,v=s.value;if(v.busy)return;if(!Object.hasOwn(this.providers,id)||typeof key!=='string'||key.length>512)throw Error('invalid_provider');
  this.discard();this.abort=new AbortController();v.busy=true;v.committing=true;v.roleError=null;s.publish();
  try{await this.providers[id].connect(id,key,this.abort.signal);}catch(e){this.providers[id].clear();v.roleError=safeError(e);}finally{this.abort=null;v.busy=false;v.committing=false;this.connections();s.publish();}
 }
 disconnect(id){if(this.store.value.busy)return;if(!Object.hasOwn(this.providers,id))throw Error('invalid_provider');this.providers[id].clear();this.discard();this.connections();this.store.publish();}
 save(roles){const s=this.store,v=s.value;if(v.busy||!v.project)return;
  v.roleError=null;v.roleSaved=false;
  try{if(this.loadError)throw Error(this.loadError);if(!validRoles(roles))throw Error('invalid_role_configuration');
   this.revision=this.storage.save(v.project,roles,this.revision);v.roleConfig=structuredClone(roles);v.roleConfigVersion++;v.roleSaved=true;this.pending=null;v.rolePreview=null;v.roleResult=null;v.roleExecution=null;this.resultBinding=null;this.histories={};this.manual.reset(true);
  }catch(e){v.roleError=safeError(e);}s.publish();
 }
 context(c){const v=this.store.value;if(c.context==='none')return {text:'',identity:null};
  if(c.context==='memory')return memoryContext(v,c.maxContextChars);
  const source=v.configAnalysis;
  if(!source)throw Error('role_context_unavailable');
  const text=c.context==='memory'?source.packetMarkdown:JSON.stringify(source);
  if(typeof text!=='string'||text.length>c.maxContextChars)throw Error('role_context_limit');
  return {text,identity:digest(source)};
 }
 prepare(value){const s=this.store,v=s.value;if(v.busy||!v.project)return;
  this.pending=null;v.rolePreview=null;v.roleError=null;v.roleResult=null;this.resultBinding=null;
  try{
   if(!value||!['prompt,role','externalReplyId,prompt,role'].includes(Object.keys(value).sort().join())||!ROLES.includes(value.role)||typeof value.prompt!=='string'||!value.prompt.trim()||value.prompt.length>8000)throw Error('invalid_role_request');
   if(this.loadError)throw Error(this.loadError);
   if(this.storage.read(v.project).revision!==this.revision)throw Error('role_file_changed');
   const c=v.roleConfig[value.role],provider=this.providers[c.provider];
   const manual=c.mode==='handoff';
   if(value.externalReplyId!==undefined&&(typeof value.externalReplyId!=='string'||value.role!=='concierge'))throw Error('invalid_role_request');
   const attachment=value.externalReplyId===undefined?'':this.manual.attachment(value.externalReplyId);
   if(!c.enabled)throw Error('role_disabled');if(!manual&&(provider.provider!==c.provider||!provider.models.includes(c.model)))throw Error('model_not_connected');
   const caps=provider.capability?.(c.model)||unknown();
   if(!manual&&(value.role==='embedding'?caps.embedding:caps.chat)===false)throw Error('role_model_incompatible');
   const parameters=manual||value.role==='embedding'?{}:generationPayload(c.provider,caps,c.generation);
   if((this.counts[value.role]||0)>=c.maxRequests)throw Error('role_request_limit');
   const context=this.context(c),history=this.histories[value.role]||[];
   const messages=value.role==='embedding'?null:[{role:'system',content:promptTemplates.effectivePrompt(c)},...history,{role:'user',content:value.prompt+attachment+(context.text?'\n\nSelected context:\n'+context.text:'')}];
   if(history.length>=24||Buffer.byteLength(JSON.stringify(messages||value.prompt))>48000)throw Error('role_conversation_limit');
   const request={...value,config:structuredClone(c),configVersion:v.roleConfigVersion,project:v.project,generation:s.generation,contextIdentity:context.identity,contextLineage:context.lineage,capabilities:digest(caps),messages};
   if(manual){this.manual.prepare(request);if(value.externalReplyId)this.event(value.role,'external_reply_attached');s.publish();return;}
   this.pending=request;v.rolePreview={role:value.role,provider:c.provider,model:c.model,endpoint:endpoints[c.provider],context:c.context,mode:c.mode,messages,parameters,input:messages?null:value.prompt,limits:{maxOutputTokens:c.maxOutputTokens,timeoutSeconds:c.timeoutSeconds,maxRequests:c.maxRequests},requestsUsed:this.counts[value.role]||0};
   this.event(value.role,'request_prepared',c.provider);if(value.externalReplyId)this.event(value.role,'external_reply_attached');
  }catch(e){v.roleError=safeError(e);}s.publish();
 }
 async submit(value){this.prepare(value);if(this.pending?.config.mode==='direct')await this.send();}
 async send(){const s=this.store,v=s.value;if(v.busy||!this.pending)return;const p=this.pending;this.pending=null;v.rolePreview=null;
  try{if(p.project!==v.project||p.generation!==s.generation||p.configVersion!==v.roleConfigVersion||this.context(p.config).identity!==p.contextIdentity)throw Error('role_preview_stale');
   if(this.storage.read(v.project).revision!==this.revision)throw Error('role_file_changed');
   if(this.providers[p.config.provider].provider!==p.config.provider)throw Error('model_not_connected');
   if(digest(this.providers[p.config.provider].capability?.(p.config.model)||unknown())!==p.capabilities)throw Error('role_preview_stale');
  }catch(e){v.roleError=safeError(e);s.publish();return;}
  v.busy=true;v.committing=true;v.roleError=null;this.abort=new AbortController();v.roleExecution=null;s.publish();
  let knowledgeStarted=false;
  try{const provider=this.providers[p.config.provider];
   if(p.config.context==='memory'&&v.knowledge?.enabled){await revalidateMemory(s,'memory');if(this.context(p.config).identity!==p.contextIdentity)throw Error('role_preview_stale');}
   if(this.abort.signal.aborted)throw Error('cancelled');
   this.counts[p.role]=(this.counts[p.role]||0)+1;v.roleCounts={...this.counts};v.roleExecution={role:p.role,provider:p.config.provider,phase:'running'};this.event(p.role,'request_started',p.config.provider);s.publish();
   if(p.role!=='embedding'&&v.knowledge?.enabled&&s.knowledge)knowledgeStarted=await s.knowledge.archive(p.role,[{role:'user',content:p.prompt}],{provider:p.config.provider,model:p.config.model,origin:'role',context:p.contextLineage||p.contextIdentity,prompt_hash:digest(p.messages[0])});
   if(p.role==='embedding'){const vector=await provider.embed(p.config.model,p.prompt,this.abort.signal,p.config.timeoutSeconds*1000);v.roleResult={role:p.role,provider:p.config.provider,model:p.config.model,dimensions:vector.length,sample:vector.slice(0,8),text:null};}
   else{const answer=await provider.send(p.config.model,p.messages,this.abort.signal,{maxOutputTokens:p.config.maxOutputTokens,timeoutMs:p.config.timeoutSeconds*1000,generation:p.config.generation});
    this.histories[p.role]=[...p.messages.slice(1),{role:'assistant',content:answer.text}];v.roleResult={role:p.role,provider:p.config.provider,model:p.config.model,text:answer.text,summary:answer.summary||null,incomplete:answer.incomplete,setupProposals:p.config.setupProposals};this.resultBinding=p;
    await s.knowledge?.archive(p.role,[...(!knowledgeStarted?[{role:'user',content:p.prompt}]:[]),{role:'assistant',content:answer.text}],{provider:p.config.provider,model:p.config.model,origin:'role',context:p.contextLineage||p.contextIdentity,prompt_hash:digest(p.messages[0]),incomplete:!!answer.incomplete});
   }
   v.roleExecution={role:p.role,provider:p.config.provider,phase:'completed'};this.event(p.role,v.roleResult?.incomplete?'response_incomplete':'response_received',p.config.provider);
  }catch(e){v.roleError=safeError(e);v.roleExecution={role:p.role,provider:p.config.provider,phase:v.roleError==='cancelled'?'cancelled':'failed'};this.event(p.role,v.roleExecution.phase==='cancelled'?'request_cancelled':'request_failed',p.config.provider);if(knowledgeStarted)await s.knowledge.archive(p.role,[],{origin:'role',error:v.roleError},true);}finally{this.abort=null;v.busy=false;v.committing=false;s.publish();}
 }
 clear(){const v=this.store.value;if(v.busy)return;this.histories={};this.pending=null;this.resultBinding=null;this.manual.reset();v.roleResult=null;v.rolePreview=null;v.roleError=null;v.roleExecution=null;v.roleEvents=[];this.store.publish();}
 cancel(){this.abort?.abort();}
 async importProposal(){const s=this.store,v=s.value,p=this.resultBinding;if(v.busy)return;
  try{if(!p?.config.setupProposals||p.config.context!=='setup'||p.project!==v.project||p.generation!==s.generation||p.configVersion!==v.roleConfigVersion||v.roleResult?.incomplete||this.context(p.config).identity!==p.contextIdentity)throw Error('role_proposal_unavailable');
   if(this.storage.read(v.project).revision!==this.revision)throw Error('role_file_changed');
   const raw=v.roleResult.text.trim().replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,'');
   const proposal=JSON.parse(raw);this.event(p.role,'setup_import_requested');await s.configuration.run('import',v.configAnalysis.choices,proposal);
  }catch(e){v.roleError=safeError(e);}s.publish();
 }
}
module.exports={AIRoles};
