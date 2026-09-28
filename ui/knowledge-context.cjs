'use strict';
const {createHash}=require('node:crypto');
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const anchorKey=c=>JSON.stringify([c.source,c.path,c.revision,c.line,c.end_line,c.excerpt]);
function currentClaims(packet,projection){
 const scope=packet?.retrieval?.options;
 if(scope?.lifecycle&&scope.lifecycle!=='all')return [];
 if(!projection||projection.generation!==packet?.generation||!Array.isArray(projection.claims)||
   typeof projection.query==='string'&&projection.query!==String(packet?.query||'').trim().toLowerCase())return [];
 return projection.claims.filter(c=>c?.review_status==='approved'&&c.current_status==='current'&&
  Array.isArray(c.evidence)&&c.evidence.length>0&&c.evidence.length<=8&&
  c.evidence.every(e=>e.origin==='original_source'&&typeof e.excerpt==='string'&&e.excerpt&&
   typeof e.source==='string'&&typeof e.revision==='string'&&
   (!scope?.paths?.length||scope.paths.some(p=>e.path===p||e.path.startsWith(p.replace(/\/$/,'')+'/')))&&
   (!scope?.kinds?.length||scope.kinds.includes(e.kind))));
}
/** Attach only hydrated original anchors. Existing [S#] identities are fixed. */
function supplementQuery(packet,projection,maxCitations=10){
 if(!packet||packet.historical||!Array.isArray(packet.citations))return packet;
 const citations=packet.citations.map(c=>({...c})),ids=new Map(citations.map(c=>[anchorKey(c),c.id]));
 const approvedClaims=[];let claimChars=0;
 for(const claim of currentClaims(packet,projection)){
  if(approvedClaims.length>=4||claimChars+String(claim.body||'').length>4000)continue;
  const missing=claim.evidence.filter(e=>!ids.has(anchorKey(e)));
  if(citations.length+missing.length>maxCitations)continue;
  for(const evidence of missing){
   const key=anchorKey(evidence);
   if(ids.has(key))continue;
   const id='S'+(citations.length+1);
   citations.push({id,...evidence});ids.set(key,id);
  }
  const markers=claim.evidence.map(e=>ids.get(anchorKey(e)));
  if(markers.some(id=>!id))continue;
  // Original claim-local [S#] markers are remapped to this packet's IDs.
  const body=String(claim.body||'').replace(/\[S([1-8])\]/g,(_match,n)=>
   markers[Number(n)-1]?'['+markers[Number(n)-1]+']':'[unresolved source]');
  approvedClaims.push({id:claim.id,title:String(claim.title||''),body,
   epistemic_status:claim.epistemic_status||'unknown',scope:claim.scope||'project',
   source_citations:markers,revision:claim.revision});
  claimChars+=body.length;
 }
 return {...packet,citations,approvedClaims,consolidation_epoch:projection?.consolidation_epoch??null};
}
function memoryContext(value,limit=12000){
 if(!value.knowledge?.enabled){
  const packet=value.work?.packet;
  if(typeof packet?.packetMarkdown!=='string')throw Error('role_context_unavailable');
  if(packet.packetMarkdown.length>limit)throw Error('role_context_limit');
  return {text:packet.packetMarkdown,identity:hash(packet),lineage:{origin:'portable-work',identity:hash(packet)}};
 }
 const packet=supplementQuery(value.knowledgeQuery,value.knowledgeConsolidationContext);
 const evidence=require('./knowledge-evidence.cjs').formatEvidence({...value,knowledgeQuery:packet},limit);
 const included=new Set(evidence.lineage.citations.map(c=>c.id));
 let text=evidence.text;const claims=[];
 for(const claim of packet?.approvedClaims||[]){
  if(!claim.source_citations.every(id=>included.has(id)))continue;
  const prefix=claim.epistemic_status==='disputed'?'DISPUTED; do not resolve from recency. ':'Approved derived memory. ';
  const block='\n'+prefix+'Claim: '+claim.title+'\nEpistemic status: '+claim.epistemic_status+
   '; scope: '+claim.scope+'\n'+claim.body+'\nOriginal evidence: '+claim.source_citations.map(id=>'['+id+']').join(' ')+'\n';
  if(text.length+block.length>limit)continue;
  text+=block;claims.push({id:claim.id,revision:claim.revision,citations:claim.source_citations});
 }
 const lineage={...evidence.lineage,approved_claims:claims,consolidation_epoch:packet?.consolidation_epoch??null};
 return {text,lineage,identity:hash({text,lineage})};
}

async function revalidateMemory(store,mode){
 if(mode!=='memory'||!store.value.knowledge?.enabled)return;
 const root=store.value.project,generation=store.generation;
 if(store.value.knowledgeBusy||!store.knowledge?.call)throw Error('role_context_refresh_required');
 let current;
 try{current=await store.knowledge.call('sync','manual',root);}catch{throw Error('role_context_refresh_failed');}
 if(root!==store.value.project||generation!==store.generation)throw Error('role_context_stale');
 store.value.knowledge=current;
 if(store.value.knowledgeConsolidationContext){
  let projection;
  try{projection=await store.knowledge.call('consolidation-context',
   store.value.knowledgeQuery?.query?{query:store.value.knowledgeQuery.query}:null,root);}
  catch{throw Error('role_context_refresh_failed');}
  if(root!==store.value.project||generation!==store.generation||
    projection.consolidation_epoch!==store.value.knowledgeConsolidationContext.consolidation_epoch||
    projection.generation!==store.value.knowledgeConsolidationContext.generation)throw Error('role_context_stale');
  store.value.knowledgeConsolidationContext=projection;
 }
 memoryContext(store.value,16000);
}
module.exports={memoryContext,revalidateMemory,supplementQuery};
