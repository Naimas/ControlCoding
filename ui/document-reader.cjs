'use strict';
const {parseDocument}=require('./markdown-model.cjs');
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function validRequest(o){return o&&Object.keys(o).sort().join()==='images,path,sha256'&&typeof o.path==='string'&&o.path.length<=4096&&hash(o.sha256)&&Array.isArray(o.images)&&o.images.length<=24&&o.images.every(i=>typeof i==='string'&&i.length<=4096)&&new Set(o.images).size===o.images.length;}
function validResult(r,o){const d=r?.document;return d&&d.path===o.path&&d.sha256===o.sha256&&typeof d.markdown==='string'&&Buffer.byteLength(d.markdown,'utf8')<=262144&&Number.isSafeInteger(d.bytes)&&d.bytes>=0&&d.bytes<=262144&&Array.isArray(d.images)&&d.images.length===o.images.length&&d.images.every((i,n)=>i.reference===o.images[n]&&(i.status==='unavailable'?typeof i.reason==='string':i.status==='available'&&typeof i.data==='string'&&i.data.length<=524340&&/^data:image\/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/]*={0,2}$/.test(i.data)));}
async function openDocument(store,generation,id){
 const v=store.value,failure=code=>({status:'error',error:{code},generation});
 if(!Number.isSafeInteger(generation)||generation!==store.generation||typeof id!=='string'||id.length>160||!v.project)return failure('stale_selection');
 if(v.busy)return failure('busy');
 let record=[...(v.knowledgeCatalog?.sources||[]).filter(d=>d.id.startsWith('source:')).map(d=>({...d,sha256:d.revision})),...(v.documentation?.documents||[]),...(v.work?.documents||[])].find(d=>d.id===id);
 v.busy=true;store.publish();
 try{
  if(!record&&/^source:[a-f0-9]{64}$/.test(id)){
   const lookup=await store.client.run('knowledge_v1',v.project,{action:'graph-source',value:id});
   if(generation!==store.generation)return failure('stale_selection');
   if(lookup.status==='ok'){const source=lookup.result.knowledge;if(source.id===id)record={...source,sha256:source.revision};}
  }
  if(!record||!record.path.toLowerCase().endsWith('.md')||!hash(record.sha256))return failure('unknown_document');
  const options={path:record.physicalPath||record.path,sha256:record.sha256,images:[]};
  let result=await store.client.run('document_read_v1',v.project,options);
  if(generation!==store.generation)return failure('stale_selection');
  if(result.status!=='ok')return failure(result.error?.code||'document_unavailable');
  options.images=parseDocument(result.result.document.markdown).images;
  if(options.images.length)result=await store.client.run('document_read_v1',v.project,options);
  if(generation!==store.generation)return failure('stale_selection');
  return result.status==='ok'?{status:'ok',generation,document:result.result.document}:failure(result.error?.code||'document_unavailable');
 }catch{return failure('document_unavailable');}
 finally{if(generation===store.generation){v.busy=false;store.publish();}}
}
module.exports={validRequest,validResult,openDocument};
