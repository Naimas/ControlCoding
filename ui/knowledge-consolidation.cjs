'use strict';
const {randomUUID}=require('node:crypto');
const {effectivePrompt}=require('./role-prompts.cjs');
const {generationPayload}=require('./ai-capabilities.cjs');
const {buildMessages,manualText,runPrepared}=require('./knowledge-consolidation-runner.cjs');
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

class ConsolidationRunner{
 constructor(store,{copyText,clientFactory}={}){this.store=store;this.copyText=copyText;this.clientFactory=clientFactory;this.owner=randomUUID();this.packet=null;this.abort=null;this.running=false;this.epoch=0;store.value.knowledgeConsolidationExecution=null;
  this.timer=setInterval(()=>{const epoch=this.epoch;this.tick().catch(error=>{if(epoch!==this.epoch)return;this.store.value.knowledgeConsolidationQueue={...(this.store.value.knowledgeConsolidationQueue||{}),app_executor:'error',error:error.message};this.store.publish();});},30000);this.timer.unref?.();}
 call(action,value,root=this.store.value.project){return this.store.knowledge.call(action,value,root,this.clientFactory?.());}
 reset(){this.epoch++;this.setAbortOperation?.('cancel');this.abort?.abort();this.abort=null;this.setAbortOperation=null;this.packet=null;this.running=false;this.store.value.knowledgeConsolidationExecution=null;}
 dispose(){clearInterval(this.timer);this.reset();}
 state(phase,error=null){this.store.value.knowledgeConsolidationExecution={phase,error,at:new Date().toISOString(),request_id:this.packet?.request_id||null};this.store.publish();}
 role(){const v=this.store.value,c=v.roleConfig?.memory_curator;if(!v.project||!c?.enabled||v.roleConfigError)throw Error('curator_role_disabled');
  if(this.store.roles?.storage.read(v.project).revision!==this.store.roles?.revision)throw Error('role_file_changed');return c;}
 config(c){return {provider:c.provider,model:c.model,generation:c.generation,maxContextChars:c.maxContextChars,maxOutputTokens:c.maxOutputTokens,timeoutSeconds:c.timeoutSeconds,maxRequests:c.maxRequests,roleRevision:this.store.roles.revision||'default'};}
 policy(){const c=this.role();return {enabled:true,prompt:effectivePrompt(c),config:this.config(c),provider:c.provider,model:c.model,mode:c.mode};}
 alive(root,generation,epoch){return this.store.generation===generation&&this.store.value.project===root&&this.epoch===epoch;}
 async refresh(job,epoch=this.epoch){const v=this.store.value,root=v.project,generation=this.store.generation;
  const view=await this.call('consolidation-view',job?{job}:null,root);if(!this.alive(root,generation,epoch))return null;
  v.knowledgeConsolidation=view;this.packet=view.job?.packet||null;this.store.publish();return view;
 }
 async prepare(job,mode){if(this.running||!['local','api','manual'].includes(mode))throw Error('invalid_consolidation_mode');
  const c=this.role();if(mode==='local'&&c.provider!=='ollama'||mode==='api'&&c.provider!=='openai')throw Error('consolidation_provider_mismatch');
  if(mode!=='manual'){const provider=this.store.roles.providers[c.provider];if(provider.provider!==c.provider||!provider.models.includes(c.model))throw Error('model_not_connected');generationPayload(c.provider,provider.capability(c.model),c.generation);}
  const root=this.store.value.project,generation=this.store.generation,epoch=this.epoch;this.state('preparing');
  try{const packet=await this.call('consolidation-prepare',{job,prompt:effectivePrompt(c),config:this.config(c),mode},root);
   if(!this.alive(root,generation,epoch))throw Error('consolidation_project_changed');
   this.packet=packet;buildMessages(packet);await this.refresh(job,epoch);if(!this.alive(root,generation,epoch))throw Error('consolidation_project_changed');this.state('prepared');return packet;
  }catch(e){if(this.alive(root,generation,epoch))this.state('failed',e.message);throw e;}
 }
 current(job,requestId){const p=this.packet||this.store.value.knowledgeConsolidation?.job?.packet;
  if(!p||p.job!==job||p.request_id!==requestId)throw Error('consolidation_packet_unavailable');return p;}
 preview(job,requestId){const p=this.current(job,requestId),c=this.role();
  if(p.config.roleRevision!==this.store.roles.revision||p.prompt!==effectivePrompt(c))throw Error('consolidation_role_changed');
  return {packet:p,messages:buildMessages(p),provider:p.config.provider,model:p.config.model,mode:p.mode};
 }
 async send(job,requestId){if(this.running)throw Error('consolidation_running');const p=this.preview(job,requestId).packet;
  if(p.mode==='manual')throw Error('consolidation_manual_packet');const provider=this.store.roles.providers[p.config.provider];
  const root=this.store.value.project,generation=this.store.generation,epoch=this.epoch,controller=new AbortController();let abortMode='cancel';this.abort=controller;this.setAbortOperation=op=>{abortMode=op;};this.running=true;this.state('running');
  try{const result=await runPrepared({packet:p,provider,owner:this.owner,signal:controller.signal,abortOperation:()=>abortMode,call:(action,value)=>this.call(action,value,root)});
   if(this.alive(root,generation,epoch)){await this.refresh(job,epoch);if(this.alive(root,generation,epoch))this.state('ready');}return result;
  }catch(e){if(this.alive(root,generation,epoch)){try{await this.refresh(job,epoch);}catch{}if(this.alive(root,generation,epoch))this.state(e.message==='cancelled'?'cancelled':'failed',e.message);}throw e;
  }finally{if(this.abort===controller){this.abort=null;this.setAbortOperation=null;this.running=false;}}
 }
 cancel(){this.setAbortOperation?.('cancel');this.abort?.abort();}
 async manualPacket(job,requestId){const root=this.store.value.project,generation=this.store.generation,epoch=this.epoch;const fresh=await this.call('sync','manual',root);
  if(!this.alive(root,generation,epoch))throw Error('consolidation_project_changed');
  if(this.store.value.knowledge?.generation!=null&&this.store.value.knowledge.generation!==fresh.generation){this.reset();this.store.value.knowledge=fresh;this.store.value.knowledgeConsolidation=null;this.store.value.knowledgeConsolidationContext=null;this.store.publish();throw Error('consolidation_packet_stale');}
  const view=await this.refresh(job,epoch);if(!this.alive(root,generation,epoch))throw Error('consolidation_project_changed');if(view?.job?.stale)throw Error('consolidation_packet_stale');const p=this.preview(job,requestId).packet;if(p.mode!=='manual')throw Error('consolidation_manual_packet_required');return manualText(p);}
 async copy(job,requestId){const epoch=this.epoch,text=await this.manualPacket(job,requestId);if(epoch!==this.epoch)throw Error('consolidation_project_changed');await this.copyText(text);if(epoch===this.epoch)this.state('copied');}
 async exportText(job,requestId){return this.manualPacket(job,requestId);}
 async import(job,requestId,text){const p=this.preview(job,requestId).packet;if(p.mode!=='manual'||typeof text!=='string'||Buffer.byteLength(text)>48000)throw Error('invalid_consolidation_reply');
  const root=this.store.value.project,generation=this.store.generation,epoch=this.epoch;this.state('validating');try{const result=await this.call('consolidation-import',{job,request_id:requestId,text},root);if(!this.alive(root,generation,epoch))throw Error('consolidation_project_changed');await this.refresh(job,epoch);if(this.alive(root,generation,epoch))this.state('ready');return result;}
  catch(e){if(this.alive(root,generation,epoch))this.state('failed',e.message);throw e;}
 }
 async control(job,requestId,operation){if(!['pause','resume','cancel','retry'].includes(operation))throw Error('invalid_consolidation_operation');
  if(this.running&&['pause','cancel'].includes(operation)){this.setAbortOperation?.(operation);this.abort?.abort();this.state(operation==='pause'?'pausing':'cancelling');return {state:operation};}
  const root=this.store.value.project,generation=this.store.generation,epoch=this.epoch;
  const result=await this.call('consolidation-attempt',{job,request_id:requestId,operation,owner:this.owner},root);
  if(!this.alive(root,generation,epoch))throw Error('consolidation_project_changed');await this.refresh(job,epoch);if(this.alive(root,generation,epoch))this.state(operation);return result;
 }
 async tick(){const s=this.store,v=s.value;if(this.running||v.busy||v.knowledgeBusy||!v.project||!v.knowledge?.enabled||v.roleExecution?.phase==='running'||s.roles?.abort)return;
  const root=v.project,generation=s.generation,epoch=this.epoch;
  const settingsResult=await this.call('consolidation-settings',null,root),settings=settingsResult.settings;
  if(!this.alive(root,generation,epoch)||!settings?.enabled)return;
  v.knowledgeConsolidationSettings=settingsResult;
  if(settings.triggers?.includes('daily')){
   let day;try{day=new Intl.DateTimeFormat('en-CA',{timeZone:settings.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}catch{day=null;}
   if(day)try{await this.call('consolidation-queue',{trigger:'daily',event_id:'app_daily_'+day.replace(/[^0-9]/g,'')},root);}catch(error){
    if(error.message!=='consolidation_window_not_due')throw error;
   }
   if(!this.alive(root,generation,epoch))return;
  }
  const queue=await this.call('consolidation-queue',null,root);
  if(!this.alive(root,generation,epoch))return;
  v.knowledgeConsolidationQueue={...queue,app_executor:'available'};s.publish();
  if(!['local','api'].includes(settings.send_policy))return;
  let item=null,packet=null;
  for(const candidate of queue.items||[]){if(candidate.state!=='queued'||!candidate.job)continue;
   const view=await this.call('consolidation-view',{job:candidate.job},root);
   if(!this.alive(root,generation,epoch))return;
   if(view.job?.stale||!view.job?.packet)continue;
   if(view.job.packet.mode!==settings.send_policy)continue;
   item=candidate;packet=view.job.packet;break;
  }
  if(!item||!packet)return;
  let policy;try{policy=this.policy();}catch{v.knowledgeConsolidationQueue={...queue,app_executor:'curator_disabled_or_changed'};s.publish();return;}
  if(packet.prompt!==policy.prompt||canonical(packet.config)!==canonical(policy.config)||settings.prompt!==policy.prompt||canonical(settings.config)!==canonical(policy.config)){
   v.knowledgeConsolidationQueue={...queue,app_executor:'policy_changed'};s.publish();return;
  }
  const provider=s.roles?.providers?.[packet.config.provider];
  if(!provider||provider.provider!==packet.config.provider||!provider.models.includes(packet.config.model)){v.knowledgeConsolidationQueue={...queue,app_executor:'provider_disconnected'};s.publish();return;}
  if(!this.alive(root,generation,epoch)||v.busy||v.knowledgeBusy||this.running)return;
  this.packet=packet;const controller=new AbortController();let abortMode='cancel';this.abort=controller;this.setAbortOperation=op=>{abortMode=op;};this.running=true;this.state('running');
  try{await runPrepared({packet,provider,owner:this.owner,signal:controller.signal,abortOperation:()=>abortMode,call:(action,value)=>this.call(action,value,root)});
   if(this.alive(root,generation,epoch)){await this.refresh(item.job,epoch);if(this.alive(root,generation,epoch))this.state('ready');}
  }catch(e){if(this.alive(root,generation,epoch)){try{await this.refresh(item.job,epoch);}catch{}if(this.alive(root,generation,epoch))this.state('failed',e.message);}}
  finally{if(this.abort===controller){this.abort=null;this.setAbortOperation=null;this.running=false;}}
 }
}
module.exports={ConsolidationRunner};
