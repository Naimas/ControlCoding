'use strict';
const {randomUUID,createHash}=require('node:crypto');
const {spawn}=require('node:child_process');
const path=require('node:path');
const {validRequest}=require('./knowledge-contract.cjs');
const {INSTRUCTION,validateAnswer}=require('./knowledge-answer.cjs');
const {supplementQuery}=require('./knowledge-context.cjs');
const empty=()=>({knowledge:null,knowledgeWork:null,knowledgeWorkSchedule:null,knowledgeOcr:null,knowledgeBusy:false,knowledgeError:null,knowledgeCatalog:null,knowledgeLibrary:null,knowledgePage:null,knowledgeQuery:null,knowledgeConversation:null,knowledgeArchive:null,knowledgeAnswer:null,knowledgeLint:null,knowledgeWikiTools:null,knowledgeWikiReview:null,knowledgeConsolidation:null,knowledgeConsolidationContext:null,knowledgeConsolidationSettings:null,knowledgeConsolidationQueue:null,knowledgeBackup:null});
class KnowledgeState{
 constructor(store,client,{python,core,profile,archiveClient}={}){this.store=store;this.client=client;this.archiveClient=archiveClient||client;this.python=python;this.core=core;this.outbox=profile?new (require('./knowledge-outbox.cjs').KnowledgeOutbox)(profile):null;this.loadedOutbox=null;this.running=false;this.ids=Object.create(null);this.pending=[];this.draining=null;Object.assign(store.value,empty());this.timer=setInterval(()=>this.tick(),30000);this.timer.unref?.();}
 reset(){this.store.consolidation?.reset();this.abort?.abort();this.client.cancel();this.archiveClient.cancel();this.ids=Object.create(null);this.pending=[];this.draining=null;this.loadedOutbox=null;this.retry=null;this.running=false;Object.assign(this.store.value,empty());}
 dispose(){clearInterval(this.timer);this.abort?.abort();this.client.cancel();this.archiveClient.cancel();}
 async call(action,value=null,root=this.store.value.project,client=this.client){
  if(action==='catalog'){
   const generation=this.store.generation;
   const previous=this.store.value.knowledgeCatalog?.graph;
   const selection=previous?{topic:previous.topic,query:previous.query,focus:previous.focus}:{};
   const load=selection=>require('./knowledge-catalog.cjs').loadCatalog((a,v)=>this.call(a,v,root,client),()=>generation===this.store.generation&&root===this.store.value.project,selection);
   const first=await load(selection);
   if(previous?.offset&&first.graph.total>200)return load({...selection,offset:Math.min(previous.offset,Math.floor((first.graph.total-1)/200)*200),snapshot:first.graph.snapshot});
   return first;
  }
  const response=await client.run('knowledge_v1',root,{action,value});
  if(response.status!=='ok')throw Error(response.error?.code||'knowledge_unavailable');
  return response.result.knowledge;
 }
 async run(action,value=null){
  const requestedGeneration=this.store.generation;
  if(this.runningAction==='library'&&action!=='library'&&this.libraryWait){await this.libraryWait;if(requestedGeneration!==this.store.generation)return;}
  if(!validRequest({action,value}))throw Error('invalid_knowledge_request');
  const s=this.store,v=s.value;if(!v.project||v.busy||this.running)return;
  const generation=s.generation,root=v.project,scheduleLoaded=!!v.knowledgeWorkSchedule,sourceGeneration=v.knowledge?.generation;this.running=true;this.runningAction=action;
  const clearConsolidationSources=()=>{v.knowledgeLibrary=null;v.knowledgePage=null;v.knowledgeWikiReview=null;v.knowledgeWikiTools=null;v.knowledgeCatalog=null;v.knowledgeConsolidationContext=null;
   v.knowledgeWork=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;v.knowledgeLint=null;};
  let libraryDone;if(action==='library')this.libraryWait=new Promise(resolve=>{libraryDone=resolve;});v.knowledgeBusy=true;v.knowledgeError=null;s.publish();
  try{
   if(['configure','forget'].includes(action))v.knowledgeWorkSchedule=null;
   if(action==='configure'){
    s.consolidation?.reset();v.knowledgeConsolidation=null;v.knowledgeConsolidationContext=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;s.publish();
   }
   // Query evidence is always reconciled before retrieval; no stale deletion hits.
   if(action==='query'){const fresh=await this.call('sync','manual',root);if(s.generation!==generation)return;if(v.knowledge?.generation!==fresh.generation){v.knowledgeLibrary=null;v.knowledgeConsolidationContext=null;v.knowledgeConsolidation=null;s.consolidation?.reset();}v.knowledge=fresh;}
   if(action.startsWith('wiki-review-')||action.startsWith('wiki-tools-')){const fresh=await this.call('sync','manual',root);if(s.generation!==generation)return;
    if(v.knowledge?.generation!==fresh.generation){v.knowledgePage=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;v.knowledgeLibrary=null;v.knowledgeConsolidationContext=null;v.knowledgeConsolidation=null;s.consolidation?.reset();}
    v.knowledge=fresh;if(action.startsWith('wiki-review-'))v.knowledgeWikiReview=null;v.knowledgeWikiTools=null;}
   if(action==='wiki-draft'){const fresh=await this.call('sync','manual',root);value={...value,generation:fresh.generation};}
   if(action==='forget'){
    s.consolidation?.reset();v.knowledgeConsolidation=null;v.knowledgeConsolidationContext=null;v.knowledgeConsolidationSettings=null;v.knowledgeConsolidationQueue=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;s.publish();
    if(this.draining)await this.draining;if(s.generation!==generation)return;
    const retained=this.pending.filter(e=>e.value.id!==value);this.outbox?.save(root,retained);this.pending=retained;this.retry=!!retained.length;
   }
   if(action==='conversation')value={...value,turns:value.turns?.map(t=>({...t,provenance:{...t.provenance,origin:'external-import'}}))};
   const result=await this.call(action,value,root);
   if(s.generation!==generation)return;
   if(['status','configure','sync','index','source-retry'].includes(action)){
    const changed=v.knowledge?.generation!==result.generation||action==='configure';
    if(changed||['sync','source-retry','configure'].includes(action)){v.knowledgeLibrary=null;v.knowledgeWork=null;v.knowledgeWikiReview=null;v.knowledgeWikiTools=null;}
    if(changed){v.knowledgeConsolidation=null;v.knowledgeConsolidationContext=null;s.consolidation?.reset();}
    v.knowledge=result;
    if(result.enabled&&this.outbox&&this.loadedOutbox!==root){
     const recovered=this.outbox.load(root);this.loadedOutbox=root;
     this.pending=recovered.map(e=>({...e,root,generation,saved:false}));this.retry=!!this.pending.length;
     for(const entry of this.pending)this.ids[entry.role]={id:entry.value.id,sequence:Math.max(-1,...entry.value.turns.map(t=>t.sequence))+1};
     if(this.pending.length)v.knowledgeArchive={saved:false,error:'pending_turns_recovered',pending:this.pending.length};
    }
    if(changed&&['configure','sync','source-retry'].includes(action)){v.knowledgeQuery=null;v.knowledgeAnswer=null;v.knowledgePage=null;v.knowledgeLibrary=null;}
    if(action==='configure'&&result.policy.worker)this.startWorker(root);
   }else if(['catalog','graph-view'].includes(action))v.knowledgeCatalog=result;
   else if(action==='library')v.knowledgeLibrary=result;
   else if(action==='ocr-preview')v.knowledgeOcr=result;
   else if(action==='ocr-save')v.knowledgeOcr=null;
   else if(['work-view','work-propose','work-review'].includes(action)){
    v.knowledgeWork=result;
    if(action!=='work-view'){
     v.knowledgeWikiTools=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;v.knowledgeConsolidationContext=null;
     const status=await this.call('status',null,root);if(s.generation!==generation)return;v.knowledge=status;
    }
   }
   else if(['work-schedule-view','work-schedule-save'].includes(action))v.knowledgeWorkSchedule=result;
   else if(['page','page-revision','notes','wiki-draft'].includes(action))v.knowledgePage=result;
   else if(action.startsWith('wiki-tools-')){
    v.knowledgeWikiTools=result;v.knowledgeQuery=null;v.knowledgeAnswer=null;v.knowledgeConsolidationContext=null;v.knowledgeWorkSchedule=null;
    const status=await this.call('status',null,root);if(s.generation!==generation)return;v.knowledge=status;
    if(result.proposal){const review=await this.call('wiki-review-view',null,root);if(s.generation!==generation)return;v.knowledgeWikiReview=review;const page=await this.call('page',result.page,root);if(s.generation!==generation)return;v.knowledgePage=page;}
   }
   else if(action.startsWith('wiki-review-')){
    const review=action==='wiki-review-view'?result:await this.call('wiki-review-view',null,root);if(s.generation!==generation)return;
    v.knowledgeWikiReview=review;v.knowledgeLibrary=null;
    const status=await this.call('status',null,root);if(s.generation!==generation)return;v.knowledge=status;
    if(v.knowledgePage?.id.startsWith('wiki:review:')){const page=await this.call('page',v.knowledgePage.id,root);if(s.generation!==generation)return;v.knowledgePage=page;}
   }
   else if(action==='consolidation-context')v.knowledgeConsolidationContext=result;
   else if(action==='consolidation-settings')v.knowledgeConsolidationSettings=result;
   else if(action==='consolidation-queue')v.knowledgeConsolidationQueue={...result,app_executor:v.knowledgeConsolidationQueue?.app_executor||'checking'};
   else if(action.startsWith('consolidation-')){
    v.knowledgeConsolidation=result;
    if(action!=='consolidation-view'){
     clearConsolidationSources();
     const status=await this.call('status',null,root);if(s.generation!==generation)return;v.knowledge=status;
    }
   }
   else if(action==='wiki-lint')v.knowledgeLint=result;
   else if(action==='query'){
    if(result.historical){v.knowledgeQuery=result;v.knowledgeAnswer=null;v.knowledgeConsolidationContext=null;return;}
    let projection=null;
    try{projection=await this.call('consolidation-context',{query:result.query},root);}
    catch{/* Original passages remain usable when approved-memory projection is unavailable. */}
    if(s.generation!==generation)return;
    if(projection?.generation!=null&&projection.generation!==result.generation){
     v.knowledgeConsolidationContext=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;
     v.knowledgeError='query_context_changed';
     try{v.knowledge=await this.call('status',null,root);}catch{/* Keep the changed-context error. */}
     if(s.generation!==generation)return;
    }else{
     v.knowledgeConsolidationContext=projection;
     v.knowledgeQuery=supplementQuery(result,projection);v.knowledgeAnswer=null;
    }
   }
   else if(action==='conversation-read')v.knowledgeConversation=result;
   if(action==='work-review'){
    const graph=await this.call('catalog',null,root);
    if(s.generation!==generation)return;
    v.knowledgeCatalog=graph;
   }
   if(['configure','sync','conversation','forget','wiki-draft','source-retry','ocr-save'].includes(action)){
    if(['conversation','forget'].includes(action)){v.knowledgeWikiReview=null;v.knowledgeWikiTools=null;v.knowledgeConsolidation=null;v.knowledgeConsolidationContext=null;}
    if(action!=='sync')v.knowledgeLibrary=null;
    const freshStatus=await this.call('status',null,root);
    if(s.generation!==generation)return;
    v.knowledge=freshStatus;
    const freshCatalog=await this.call('catalog',null,root);
    if(s.generation!==generation)return;
    v.knowledgeCatalog=freshCatalog;
    if(action==='conversation'&&result.id===v.knowledgeConversation?.id)v.knowledgeConversation=await this.call('conversation-read',result.id,root);
    if(action==='forget'){v.knowledgeConversation=null;v.knowledgePage=null;v.knowledgeQuery=null;v.knowledgeAnswer=null;v.knowledgeArchive=null;this.pending=this.pending.filter(e=>e.value.id!==value);this.retry=!!this.pending.length;this.outbox?.save(root,this.pending);for(const [role,binding] of Object.entries(this.ids))if(binding.id===value)delete this.ids[role];}
   }
   if(!action.startsWith('work-schedule-')&&scheduleLoaded&&(sourceGeneration!==v.knowledge?.generation||['work-propose','work-review','forget'].includes(action))){
    v.knowledgeWorkSchedule=null;
    const plan=await this.call('work-schedule-view',null,root);
    if(s.generation!==generation)return;v.knowledgeWorkSchedule=plan;
   }
  }catch(e){if(s.generation===generation){v.knowledgeError=e.message;if(action==='library')v.knowledgeLibrary=null;if(action==='query')v.knowledgeQuery=null;
   if(sourceGeneration!==v.knowledge?.generation||action.startsWith('wiki-review-')||action.startsWith('consolidation-')||['sync','query','configure','forget','source-retry','work-review','work-propose','work-schedule-view','work-schedule-save'].includes(action))v.knowledgeWorkSchedule=null;
   if(action.startsWith('consolidation-')&&action!=='consolidation-view'){
    const selected=value?.job||v.knowledgeConsolidation?.job?.id;
    clearConsolidationSources();v.knowledgeConsolidation=null;v.knowledge=null;
    try{const status=await this.call('status',null,root);if(s.generation!==generation)return;v.knowledge=status;}catch{/* Preserve the original mutation error. */}
    if(s.generation!==generation)return;
    try{const current=await this.call('consolidation-view',selected?{job:selected}:null,root);
     if(s.generation!==generation)return;v.knowledgeConsolidation=current;
    }catch{/* The selected job may no longer exist; keep the original error. */}
   }
   else if(['sync','query','configure','source-retry'].includes(action)){try{const status=await this.call('status',null,root);if(s.generation===generation)v.knowledge=status;}catch{/* Keep the original operation error if status is unavailable. */}}
  }}
  finally{libraryDone?.();if(s.generation===generation){this.running=false;this.runningAction=null;this.libraryWait=null;v.knowledgeBusy=false;s.publish();}}
 }
 async tick(){
  const v=this.store.value,generation=this.store.generation;if(!v.project||v.busy||this.running)return;
  if(!v.knowledge){await this.run('status');return;}
  if(!v.knowledge.enabled||!v.knowledge.policy.automatic)return;
  await this.run('sync','timer');
  if(generation===this.store.generation&&!this.store.value.knowledgeError&&v.knowledge.policy.embedding&&v.knowledge.counts.embedded<v.knowledge.counts.chunks)await this.run('index');
 }
 startWorker(root){
  if(!this.python||!this.core)return;
  const child=spawn(this.python,['-I','-B',path.join(this.core,'scripts','cc_knowledge.py'),'--project-root',root,'worker'],
    {detached:true,windowsHide:true,stdio:'ignore',shell:false});
  child.on('error',()=>{if(this.store.value.project===root){this.store.value.knowledgeError='worker_start_failed';this.store.publish();}});child.unref();
 }
 newConversation(role){delete this.ids[role];}
 async archive(role,messages,provenance={},interrupted=false){
  const s=this.store,v=s.value;
  if(!v.knowledge?.enabled||v.knowledge.policy.retention==='none')return false;
  const binding=this.ids[role]||(this.ids[role]={id:randomUUID(),sequence:0});
  // Reserve event identity at capture time, including failed/queued saves.
  // A retry must never drift to a newly selected conversation or edited payload.
  const turns=v.knowledge.policy.retention==='summary'?[]:messages.map((m,i)=>({id:binding.id+'-'+(binding.sequence+i),sequence:binding.sequence+i,role:m.role,content:m.content,provenance:structuredClone(provenance)}));
  binding.sequence+=turns.length;
  const entry={role,root:v.project,generation:s.generation,value:{id:binding.id,title:role+' conversation',retention:v.knowledge.policy.retention,
    summary:messages.filter(m=>m.role==='user').map(m=>m.content.slice(0,600)).join('\n'),status:interrupted?'interrupted':'active',turns},saved:false};
  this.pending.push(entry);this.retry=true;
  try{this.outbox?.save(v.project,this.pending);}catch(e){this.pending.pop();binding.sequence-=turns.length;v.knowledgeArchive={saved:false,error:e.message};s.publish();return false;}
  await this.drainArchives();return entry.saved;
 }
 async drainArchives(){
  if(this.draining)return this.draining;
  const s=this.store,generation=s.generation;
  if(this.running&&this.runningAction!=='library'&&this.archiveClient===this.client){s.value.knowledgeArchive={saved:false,error:'knowledge_busy'};s.publish();return;}
  const drain=async()=>{
   if(this.runningAction==='library'&&this.libraryWait)await this.libraryWait;
   while(this.pending.length&&generation===s.generation){
    const entry=this.pending[0];
    if(entry.generation!==generation||entry.root!==s.value.project){this.pending.shift();continue;}
    try{
     const result=await this.call('conversation',entry.value,entry.root,this.archiveClient);
     if(generation!==s.generation)return;
     entry.saved=result.saved;this.pending.shift();s.value.knowledgeArchive=result;
     this.outbox?.save(entry.root,this.pending);
     try{s.value.knowledge=await this.call('status',null,entry.root,this.archiveClient);}catch{if(generation===s.generation)s.value.knowledge.needs_reconcile=true;}
    }catch(e){if(generation===s.generation)s.value.knowledgeArchive={saved:false,error:e.message,pending:this.pending.length};break;}
   }
  };
  this.draining=drain();
  try{await this.draining;}finally{if(generation===s.generation){this.draining=null;this.retry=!!this.pending.length;s.publish();}}
 }
 async retryArchive(){await this.drainArchives();}
 async investigate(){
  const s=this.store,packet=s.value.knowledgeQuery,generation=s.generation;
  if(this.running||s.value.busy||!packet||packet.followup)return;
  const answered=packet.citations.length?await this.answer():null;
  const currentPacket=answered?.packet||packet;
  if(s.generation===generation&&s.value.knowledgeQuery===currentPacket&&!s.value.knowledgeError&&
     (!packet.citations.length||s.value.knowledgeAnswer?.abstained))await this.deepen();
 }
 async deepen(){
  const s=this.store,v=s.value,packet=v.knowledgeQuery,c=v.roleConfig?.concierge;
  if(this.running||v.busy||!packet||packet.historical||packet.followup||!c?.enabled||c.mode==='handoff')return;
  const provider=s.roles?.providers[c.provider];
  if(!provider||provider.provider!==c.provider||!provider.models.includes(c.model)){v.knowledgeError='model_not_connected';s.publish();return;}
  if((s.roles.counts?.concierge||0)>=c.maxRequests){v.knowledgeError='role_request_limit';s.publish();return;}
  const {PLAN_INSTRUCTION,parseQueries,mergePackets}=require('./knowledge-followup.cjs');
  const generation=s.generation,root=v.project,config=JSON.stringify(c),version=v.roleConfigVersion;
  let basePacket=packet;
  const controller=new AbortController(),started=Date.now(),trace={status:'running',attempts:[],elapsed_ms:0,notice:'At most two searches in the existing authorized index; not an exhaustive search.'};
  const preview={...packet,followup:trace};v.knowledgeQuery=preview;v.knowledgeAnswer=null;
  this.running=true;v.knowledgeBusy=true;v.knowledgeError=null;this.abort=controller;s.publish();
  let timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;controller.abort();if(s.generation===generation)this.client.cancel();},60000);
  const check=()=>{
   if(controller.signal.aborted)throw Error(timedOut?'followup_deadline':'cancelled');
   if(s.generation!==generation||v.project!==root||v.knowledgeQuery!==preview)throw Error('answer_context_changed');
   if(version!==v.roleConfigVersion||JSON.stringify(v.roleConfig?.concierge)!==config)throw Error('role_file_changed');
   if(provider.provider!==c.provider||!provider.models.includes(c.model))throw Error('model_not_connected');
   if(s.roles.storage&&s.roles.storage.read(root).revision!==s.roles.revision)throw Error('role_file_changed');
  };
  const bounded=async operation=>{
   check();let listener;
   try{return await Promise.race([Promise.resolve().then(()=>{check();return operation();}),new Promise((_,reject)=>{listener=()=>reject(Error(timedOut?'followup_deadline':'cancelled'));controller.signal.addEventListener('abort',listener,{once:true});})]);}
   finally{controller.signal.removeEventListener('abort',listener);}
  };
  const reconcile=async(initial=false)=>{const fresh=await bounded(()=>this.call('sync','manual',root));check();
   if(fresh.needs_reconcile)throw Error('answer_context_changed');
   if(initial){
    // Planning transmits the question only. If prior answer archival or source
    // changes advanced the index, drop old evidence and pin this fresh scope.
    if(fresh.generation!==packet.generation||fresh.work_revision!==packet.work_revision){
     basePacket={...packet,citations:[],edges:[],answer:'The source context changed; previous excerpts were discarded before follow-up retrieval.',generation:fresh.generation,work_revision:fresh.work_revision,observed_at:fresh.last_reconcile};
     Object.assign(preview,basePacket,{followup:trace});trace.initial_context_refreshed=true;
    }
    v.knowledge=fresh;
   }else if(fresh.generation!==basePacket.generation||fresh.work_revision!==basePacket.work_revision)throw Error('answer_context_changed');};
  try{
   await reconcile(true);check();
   if((s.roles.counts?.concierge||0)>=c.maxRequests)throw Error('role_request_limit');
   s.roles.counts=s.roles.counts||{};s.roles.counts.concierge=(s.roles.counts.concierge||0)+1;v.roleCounts={...s.roles.counts};
   const plan=await bounded(()=>provider.send(c.model,[{role:'system',content:PLAN_INSTRUCTION},{role:'user',content:packet.query}],controller.signal,
    {maxOutputTokens:c.maxOutputTokens,timeoutMs:Math.min(c.timeoutSeconds*1000||60000,60000),generation:c.generation}));
   check();const queries=parseQueries(plan.text,packet.query,plan.incomplete),results=[];
   for(const query of queries){
    check();const attempt={query,status:'running'};trace.attempts.push(attempt);s.publish();
    const result=await bounded(()=>this.call('query',{text:query,semantic:true,...(packet.retrieval?.options?{retrieval:packet.retrieval.options}:{})},root));check();
    if(result.generation!==basePacket.generation||result.work_revision!==basePacket.work_revision)throw Error('answer_context_changed');
    attempt.status='completed';attempt.returned=result.citations.length;attempt.warning=result.warning||null;results.push(result);
   }
   await reconcile();check();
   const merged=mergePackets(basePacket,results);
   merged.followup={...merged.followup,status:'completed',elapsed_ms:Date.now()-started,attempts:trace.attempts,initial_context_refreshed:!!trace.initial_context_refreshed};
   v.knowledgeQuery=merged;
  }catch(e){if(s.generation===generation&&root===v.project){
   trace.status='stopped';trace.error=e.message;trace.elapsed_ms=Date.now()-started;
   for(const attempt of trace.attempts)if(attempt.status==='running')attempt.status='stopped';
   v.knowledgeError=e.message;
   if(e.message==='answer_context_changed'){v.knowledgeQuery=null;v.knowledgeAnswer=null;}
  }}finally{clearTimeout(timer);if(this.abort===controller)this.abort=null;if(s.generation===generation){this.running=false;v.knowledgeBusy=false;s.publish();}}
 }
 resume(){
  const s=this.store,v=s.value,c=v.knowledgeConversation;if(v.busy||this.running||!c)return;
  const role=c.title.replace(/ conversation$/,'');
  const history=[];let size=0;
  for(const t of [...c.turns].reverse()){if(history.length>=20||size+t.content.length>26000)break;history.unshift({role:t.role,content:t.content});size+=t.content.length;}
  if(!history.length&&c.summary)history.push({role:'user',content:'Prior session summary (unverified conversation context):\n'+c.summary});
  if(role==='Direct AI'){v.aiMessages=history;s.ai.pending=null;v.aiPreview=null;}
  else if(s.roles&&Object.hasOwn(v.roleConfig||{},role)){s.roles.histories[role]=history;s.roles.pending=null;v.rolePreview=null;}
  else{v.aiMessages=history;s.ai.pending=null;v.aiPreview=null;}
  this.ids[role==='Direct AI'||Object.hasOwn(v.roleConfig||{},role)?role:'Direct AI']={id:c.id,sequence:Math.max(-1,...c.turns.map(t=>t.sequence))+1};
  v.knowledgeArchive={resumed:true,id:c.id};s.publish();
 }
 async answer(){
  const s=this.store,v=s.value,c=v.roleConfig?.concierge,packet=v.knowledgeQuery;
  if(this.running||v.busy||packet?.historical||!packet?.citations.length||!c?.enabled||c.mode==='handoff')return;
  const provider=s.roles.providers[c.provider];
  if(provider.provider!==c.provider||!provider.models.includes(c.model)){v.knowledgeError='model_not_connected';s.publish();return;}
  if((s.roles.counts?.concierge||0)>=c.maxRequests){v.knowledgeError='role_request_limit';s.publish();return;}
  const generation=s.generation;this.running=true;v.knowledgeBusy=true;v.knowledgeError=null;v.knowledgeAnswer=null;s.publish();let savedAnswer=null,usedEvidence=packet;
  this.abort=new AbortController();
  try{
   if(s.roles.storage&&s.roles.storage.read(v.project).revision!==s.roles.revision)throw Error('role_file_changed');
   const fresh=await this.call('sync','manual');if(fresh.generation!==packet.generation||(packet.work_revision&&fresh.work_revision!==packet.work_revision))throw Error('answer_context_changed');
   let projection=null;
   try{projection=await this.call('consolidation-context',{query:packet.query},v.project);}
   catch{if(v.knowledgeConsolidationContext)throw Error('answer_context_changed');}
   if(generation!==s.generation)return;
   if(projection&&(projection.generation!==packet.generation||
     v.knowledgeConsolidationContext&&projection.consolidation_epoch!==v.knowledgeConsolidationContext.consolidation_epoch))throw Error('answer_context_changed');
   const evidence=supplementQuery(packet,projection);
   usedEvidence=evidence;
   const claims=(evidence.approvedClaims||[]).map(claim=>
    '\nApproved derived memory; epistemic status: '+claim.epistemic_status+
    '; scope: '+claim.scope+'\n'+claim.title+'\n'+claim.body+
    '\nOriginal evidence: '+claim.source_citations.map(id=>'['+id+']').join(' ')).join('\n');
   const prompt='Question: '+packet.query+'\n\nOriginal evidence:\n'+evidence.citations.map(x=>
    '['+x.id+'] '+x.path+' @ '+x.revision+' | source: '+x.source+' | evidence kind: '+x.kind+'\n'+x.excerpt).join('\n\n')+
    (claims?'\n\nReviewed derived memory (cite its original evidence above):\n'+claims:'')+
    '\n\nRetrieval relationships (navigation/review metadata, not independent factual evidence):\n'+JSON.stringify(packet.retrieval?.edges||[]);
   v.knowledgeConsolidationContext=projection;
   v.knowledgeQuery=evidence;
   if(s.roles.counts){s.roles.counts.concierge=(s.roles.counts.concierge||0)+1;v.roleCounts={...s.roles.counts};}
   const configured=c.systemPrompt?require('./role-prompts.cjs').effectivePrompt(c):'';
   const result=await provider.send(c.model,[{role:'system',content:configured+'\n'+INSTRUCTION},{role:'user',content:prompt}],this.abort.signal,{maxOutputTokens:c.maxOutputTokens,timeoutMs:c.timeoutSeconds*1000,generation:c.generation});
   if(generation!==s.generation)return;
   if(evidence.approvedClaims?.length){
    const latest=await this.call('consolidation-context',{query:packet.query},v.project);
    if(latest.generation!==evidence.generation||latest.consolidation_epoch!==projection.consolidation_epoch)
     throw Error('answer_context_changed');
   }
   const checked=validateAnswer(result.text,evidence.citations,result.incomplete);
   v.knowledgeAnswer={...checked,provider:c.provider,model:c.model,incomplete:false,generation:packet.generation,
     notice:checked.abstained?'The model reported insufficient evidence; no answer was established.':'AI synthesis. Citation identifiers are checked; source entailment still requires human review.'};
   savedAnswer=v.knowledgeAnswer;
  }catch(e){if(generation===s.generation)v.knowledgeError=e.message;}
  finally{this.abort=null;if(generation===s.generation){this.running=false;v.knowledgeBusy=false;s.publish();}}
  if(savedAnswer&&generation===s.generation)await this.archive('concierge',[{role:'user',content:packet.query},{role:'assistant',content:savedAnswer.text}],{provider:c.provider,model:c.model,origin:savedAnswer.abstained?'insufficient-evidence':'grounded-answer',context:{generation:packet.generation,citations:usedEvidence.citations.map(x=>({path:x.path,revision:x.revision,id:x.id}))}});
  return savedAnswer&&generation===s.generation&&v.knowledgeAnswer===savedAnswer?{packet:usedEvidence,answer:savedAnswer}:null;
 }
 cancel(){this.abort?.abort();this.client.cancel();}
}
module.exports={KnowledgeState,empty};
