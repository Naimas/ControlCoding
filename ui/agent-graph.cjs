'use strict';
// Browser-safe projection. No I/O, credentials, prompts or execution authority.
const {generationPayload,unknown}=require('./ai-capabilities.cjs');
const {formatEvidence}=require('./knowledge-evidence.cjs');
function evidenceState(data,limit=16000){try{formatEvidence(data,limit);return null;}catch(e){return e.message;}}
const labels={concierge:'Concierge',documentation:'Documentation',architect:'Architecture',reviewer:'Review & control',developer:'Development',embedding:'Embeddings'};
function roleState(data,id,c){
 const reasons=[];
 if(!c.enabled)return {status:'disabled',reasons:['Enable and save this role to use it.']};
 if(!data.project)reasons.push('Select a project.');
 if(data.roleConfigError)reasons.push('The saved role configuration could not be loaded.');
 if((data.roleCounts?.[id]||0)>=c.maxRequests)reasons.push('The project/session request limit is exhausted.');
 if(c.context==='memory'&&data.knowledge?.enabled){
  const issue=evidenceState(data,c.maxContextChars);
  if(issue)reasons.push(issue==='role_context_limit'?'The selected evidence exceeds this role context budget.':issue==='role_context_stale'?'Selected memory evidence is stale. Retrieve fresh evidence in ControlWork.':'Retrieve a cited evidence packet in ControlWork first.');
 }else if(c.context!=='none'){
  const source=c.context==='memory'?data.work?.packet:data.configAnalysis;
  const text=c.context==='memory'?source?.packetMarkdown:source&&JSON.stringify(source);
  if(typeof text!=='string')reasons.push('The selected context packet is unavailable.');
  else if(text.length>c.maxContextChars)reasons.push('The selected context exceeds this role’s character budget.');
 }
 if(c.mode!=='handoff'){
  const connection=data.roleConnections?.[c.provider],caps=connection?.capabilities?.[c.model]||unknown();
  if(!connection?.connected||!connection.models.includes(c.model))reasons.push('Connect the selected provider and verify the saved model.');
  if((id==='embedding'?caps.embedding:caps.chat)===false)reasons.push('The selected model is incompatible with this role.');
  if(id!=='embedding')try{generationPayload(c.provider,caps,c.generation);}catch{reasons.push('Saved generation options are unavailable for this model.');}
 }
 // A running request can have consumed its last quota slot. It is still running.
 if(data.roleExecution?.role===id&&data.roleExecution.phase==='running')return {status:'active',reasons:['An explicit API request is in progress. No model-internal steps are observable.']};
 if(reasons.length)return {status:'blocked',reasons};
 return {status:c.mode==='handoff'?'manual':'available',reasons:[c.mode==='handoff'?'Prepare, copy, paste and review each round yourself. External execution is unobserved.':'Available for explicit submission. The provider and request checks still validate each attempt.']};
}
function projectGraph(data,{planned=false,focus='all'}={}){
 const nodes=[],edges=[];
 const node=(id,label,x,y,status,detail,extra={})=>nodes.push({id,label,x,y,status,detail,...extra});
 const edge=(id,from,to,label,status='available',role=null,detail='',rail=null)=>edges.push({id,from,to,label,status,role,detail,rail});
 node('user','You / coding host',30,50,'manual','You choose the role, submit each request and decide what to apply. The panel does not edit project code.');
 node('memory',data.knowledge?.enabled?'Selected memory evidence':'ControlWork packet',30,260,(data.knowledge?.enabled?!evidenceState(data):!!data.work?.packet)?'available':'blocked','Only explicitly retrieved, revision-bound evidence is added. Refresh the packet if its source generation changes.');
 node('setup','Setup analysis',30,410,data.configAnalysis?'available':'blocked','A prepared Setup analysis packet must fit the role’s context budget.');
 node('approval','Request preview',610,50,'manual','Reviewed API mode requires a separate Send. Direct mode still requires explicit submission.');
 for(const [id,y] of [['ollama',210],['openai',350]])node(id,id==='ollama'?'Ollama · local':'OpenAI API',610,y,data.roleExecution?.provider===id&&data.roleExecution.phase==='running'?'active':data.roleConnections?.[id]?.connected?'available':'blocked','Only the configured model receives the approved request. No automatic fallback or autonomous tools.');
 node('manual','Manual prompt packet',610,510,data.manualHandoff?'manual':'idle',data.manualHandoff?.copied?'Packet copied to the clipboard. Delivery to an external chat is not observed.':'Prepare a bounded packet with role instructions and a request ID. Copying does not run a model.');
 node('answer','Answer / vector',900,210,'idle','API results return to the user. Development output is advisory text. Embedding output is a vector; it does not update the RAG index.');
 node('external','External chat',900,510,'unobserved','You paste the prompt into a chat of your choice. The app cannot observe delivery, model identity, processing or completion.');
 node('review','Paste & review reply',900,660,data.manualReplyPreview?'manual':'idle','Paste a response with the matching request ID, review it, then accept it into the role’s session history. External content remains unverified.');
 node('proposal','Setup proposal review',610,720,'manual','Allowed concierge/architecture results can be imported explicitly into Setup for separate review. Import is not automatic application.');
 Object.entries(labels).forEach(([id,label],index)=>{
  const c=data.roleConfig?.[id];if(!c)return;
  const state=roleState(data,id,c),status=state.status;
  node(id,label,320,50+index*125,status,state.reasons.join(' '),{role:id,reasons:state.reasons,mode:c.mode,provider:c.mode==='handoff'?'External chat':c.provider,model:c.mode==='handoff'?'Unverified':c.model,context:c.context,used:data.roleCounts?.[id]||0,limit:c.maxRequests});
  edge('select-'+id,'user',id,'Explicit submission',status==='active'?'available':status,id,'You select this role; concierge does not automatically delegate.');
  if(c.context!=='none')edge('context-'+id,c.context,id,'Selected context',status==='active'?'available':status,id,'Only the current packet is added within the saved context budget.');
  if(c.mode==='handoff')edge('route-'+id,id,'manual','Prepare packet',status,id,'A manual packet is prepared after explicit submission. No provider call.');
  else{
   if(c.mode==='reviewed')edge('preview-'+id,id,'approval','Review before sending',status==='active'?'available':status,id,'Inspect the complete outgoing payload and explicitly approve Send.');
   edge('route-'+id,c.mode==='reviewed'?'approval':id,c.provider,c.mode==='reviewed'?'Send reviewed request':'Send on submission',status,id,'Saved policy, context, model metadata and request limits are rechecked.');
   edge('result-'+id,c.provider,'answer',id==='embedding'?'Vector response':'Text response',status,id,'One provider request. Internal model reasoning and tool loops are not visible.');
  }
  if(c.setupProposals&&c.context==='setup')edge('proposal-'+id,id,'proposal','Explicit result import',status==='active'?'available':status,id,'Only this role’s bound, complete result can be imported; Setup performs separate validation.');
 });
 edge('copy','manual','external','Copy → user transports','unobserved',null,'Clipboard copy is observable; external delivery and execution are not.');
 edge('paste','external','review','User pastes a reply','unobserved',null,'Matching ID binds a pasted reply to a packet, not to verified model execution.');
 edge('accept','review','user','Accept into role history','manual',null,'Accepted text becomes session context for the originating role. You can submit the next round.',820);
 edge('return','review','concierge','Explicit concierge review','manual','concierge','The user attaches an accepted reply to a new concierge request, then submits it. There is no automatic forwarding.',850);
 edge('iterate','answer','user','Inspect → next request','manual',null,'You inspect the result and submit another turn or use your coding host.',880);
 if(planned){
  node('router','Automatic routing',30,1020,'planned','Planned: concierge delegates work with bounded authority, budgets and explicit stop conditions. Not implemented.');
  node('executor','Coding executor',320,1020,'planned','Planned: a separate coding host edits and verifies code with scoped permissions. The current role only proposes text.');
  edge('planned-router','concierge','router','Proposed delegation','planned',null,'Not implemented.',950);
  edge('planned-executor','router','executor','Design → develop → verify','planned',null,'Proposed automatic loop. No executor is started.');
  edge('planned-review','executor','reviewer','Proposed review loop','planned',null,'Would need findings, bounded retries and a human approval/stop gate.',920);
 }
 if(data.knowledge?.enabled){
  const retained=data.knowledge.policy.retention!=='none';
  node('knowledge-archive','Unified conversation archive',610,1020,retained?'available':'disabled','Persistent visible role turns, manual replies and resumable context, governed by the saved retention policy. Direct AI also has an Archive option.');
  node('knowledge-index','Source-bound wiki & GraphRAG',900,1020,data.knowledge.embedding_error?'blocked':'available','The knowledge coordinator maintains source revisions, wiki, explicit edges and local neural passage vectors. Model: '+(data.knowledge.embedding_identity||'not yet indexed'));
  edge('retain','answer','knowledge-archive','Retention policy',retained?'available':'disabled',null,'Only retained visible conversation evidence enters this archive; not hidden reasoning.');
  edge('knowledge-refresh','knowledge-archive','knowledge-index','Incremental maintenance','available',null,'Automatic or explicit reconciliation updates derived sources; embedding batches are separate from manual embedding-role requests.');
 }
 const shared=data.roleConfig?.[focus]?.mode==='handoff'?['copy','paste','accept','return']:['iterate'];
 const relevantEdge=e=>focus==='all'||e.role===focus||shared.includes(e.id);
 const relevant=new Set(edges.filter(relevantEdge).flatMap(e=>[e.from,e.to]));
 return {nodes:nodes.map(n=>({...n,dim:focus!=='all'&&!relevant.has(n.id)})),edges:edges.map(e=>({...e,dim:!relevantEdge(e)})),height:planned||data.knowledge?.enabled?1150:940,labels};
}
module.exports={projectGraph,roleState};
