'use strict';
const scopes=['project','plans','handoffs'];
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const text=(v,n)=>typeof v==='string'&&v.length<=n;
function validResult(result,options){const d=result?.documentation;
 if(!d||d.schema_version!==1||d.scope!==options.scope||!hash(d.snapshot_id)||!Array.isArray(d.paths)||d.paths.length>2||!d.paths.every(p=>text(p,100))||!text(d.notice,1000)||!Number.isSafeInteger(d.bytes)||d.bytes<0||d.bytes>8388608||!Number.isSafeInteger(d.unresolved_references)||d.unresolved_references<0||d.unresolved_references>2000)return false;
 if(!Array.isArray(d.documents)||d.documents.length>200||!d.documents.every(n=>text(n.id,80)&&/^source:[a-f0-9]{64}$/.test(n.id)&&text(n.path,4096)&&!n.path.startsWith('/')&&!n.path.split('/').includes('..')&&text(n.title,180)&&['documentation','plans','archive','evidence'].includes(n.area)&&hash(n.sha256)&&text(n.excerpt,1200)&&typeof n.excerpt_truncated==='boolean'&&Number.isSafeInteger(n.bytes)&&n.bytes>=0&&n.bytes<=262144))return false;
 const ids=new Set(d.documents.map(n=>n.id));if(ids.size!==d.documents.length||new Set(d.documents.map(n=>n.path)).size!==d.documents.length)return false;
 return Array.isArray(d.edges)&&d.edges.length<=2000&&d.edges.every(e=>e&&ids.has(e.source)&&ids.has(e.target)&&e.kind==='markdown_reference');
}
const empty=()=>({documentation:null,documentationScope:'project',documentationError:null,documentationObservedAt:null,documentationReading:false});
class DocumentationState{
 constructor(store){this.store=store;}
 async read(scope){const s=this.store,v=s.value;if(v.busy||!v.project||!scopes.includes(scope))return;
  const generation=s.generation;v.busy=true;v.documentationReading=true;v.documentationError=null;if(scope!==v.documentationScope)v.documentation=null;v.documentationScope=scope;s.publish();
  let response;try{response=await s.client.run('documentation_read_v1',v.project,{scope});}catch{response={status:'error',error:{code:'helper_failure',source:'documentation'}};}
  if(generation!==s.generation)return;
  v.busy=false;v.documentationReading=false;
  if(response.status==='ok'){v.documentation=response.result.documentation;v.documentationObservedAt=response.observed_at;}
  else{v.documentation=null;v.documentationObservedAt=null;v.documentationError=response.error;}
  s.publish();
 }
}
module.exports={scopes,validResult,empty,DocumentationState};
