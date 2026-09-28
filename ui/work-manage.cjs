'use strict';
const {randomUUID}=require('node:crypto');
const operations=['work_manage_preview_v1','work_manage_commit_v1'];
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const strings=v=>Array.isArray(v)&&v.every(x=>typeof x==='string');
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()===keys.sort().join();
function validValue(v){
 const fields={init:['name','purpose'],capture:['title','body','area'],import:['area'],session:['title','body','decisions','followups'],conversation:['title','provider','messages']}[v?.action];
 if(!fields||!exact(v,['action',...fields]))return false;
 return fields.every(k=>k==='messages'?Array.isArray(v[k])&&v[k].length>0&&v[k].length<=24&&v[k].every(m=>exact(m,['role','content'])&&['user','assistant'].includes(m.role)&&typeof m.content==='string'&&m.content.length<=12000):['decisions','followups'].includes(k)?strings(v[k])&&v[k].length<=16&&v[k].every(s=>s.length<=400):typeof v[k]==='string'&&v[k].length<=(k==='body'?8000:k==='purpose'?1600:120))&&
  (!fields.includes('area')||['inbox','sources','notes','ideas','decisions','plans','outputs','legacy'].includes(v.area))&&Buffer.byteLength(JSON.stringify(v))<=40000;
}
function validRequest(op,o){return exact(o,['value','source_paths','request_id','timestamp',...(op==='work_manage_commit_v1'?['approval_id']:[])])&&validValue(o.value)&&
 strings(o.source_paths)&&o.source_paths.length<=8&&o.source_paths.every(p=>p.length<=256)&&/^[a-f0-9]{32}$/.test(o.request_id)&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(o.timestamp)&&
 (op!=='work_manage_commit_v1'||hash(o.approval_id));}
function validResult(op,r,o){
 if(op==='work_manage_commit_v1'){const s=r.work_manage_saved;return s?.saved===true&&s.verified===false&&s.action===o.value.action&&strings(s.files)&&strings(s.directories_created);}
 const p=r.work_manage_preview;return p?.schema_version===1&&p.action===o.value.action&&p.root===r.project_root&&hash(p.approval_id)&&typeof p.write_supported==='boolean'&&strings(p.sources)&&strings(p.blockers)&&typeof p.notice==='string'&&
 Array.isArray(p.files)&&p.files.length<=64&&p.files.every(f=>typeof f.path==='string'&&['create','keep','conflict'].includes(f.action)&&hash(f.sha256)&&typeof f.preview==='string'&&f.preview.length<=8000&&typeof f.truncated==='boolean'&&Number.isSafeInteger(f.bytes)&&f.bytes>=0);
}
const empty=()=>({workManagePreview:null,workManageSaved:null,workManageError:null,workImportPaths:[]});
class WorkManagement {
 constructor(store){this.store=store;this.pending=null;}
 reset(){this.pending=null;}
 discard(){if(this.store.value.busy)return;this.pending=null;Object.assign(this.store.value,{workManagePreview:null,workManageSaved:null,workManageError:null});this.store.publish();}
 selectSources(paths){if(this.store.value.busy)return;this.discard();this.store.value.workImportPaths=[...paths];this.store.publish();}
 async run(action,value){
  const s=this.store,v=s.value;
  if(v.busy||!v.project||!['preview','commit'].includes(action))return;
  const writing=action==='commit';let options;
  if(writing){if(!this.pending||!v.workManagePreview?.write_supported||v.workManagePreview.blockers.length)return;options=this.pending;}
  else{if(!validValue(value))throw Error('Invalid memory request');options={value:JSON.parse(JSON.stringify(value)),source_paths:value.action==='import'?[...v.workImportPaths]:[],request_id:randomUUID().replaceAll('-',''),timestamp:new Date().toISOString().replace(/\.\d{3}Z$/,'Z')};}
  const generation=s.generation,root=v.project;
  this.pending=null;Object.assign(v,{busy:true,committing:writing,workManagePreview:null,workManageSaved:null,workManageError:null});s.publish();
  let response;try{response=await s.client.run(`work_manage_${action}_v1`,root,options);}catch{response={status:'error',error:{code:'helper_failure',source:'controlwork'}};}
  if(generation!==s.generation)return;
  v.busy=false;v.committing=false;
  if(response.status==='ok'){
   if(writing){v.workManageSaved=response.result.work_manage_saved;Object.assign(v,{work:null,workScope:null,workError:null,workObservedAt:null});}
   else{v.workManagePreview=response.result.work_manage_preview;this.pending={...options,approval_id:v.workManagePreview.approval_id};}
  }else v.workManageError=response.error;
  v.activity.unshift({operation:'work_manage_'+action,outcome:response.status==='ok'?(writing?'Saved':'Prepared'):'Unavailable',time:new Date().toISOString(),code:response.error?.code||null});v.activity=v.activity.slice(0,100);s.publish();
  if(writing&&response.status==='ok'){
   await s.observeWork('preview');if(generation===s.generation&&v.workScope)await s.observeWork('read');
  }
 }
}
module.exports={operations,validValue,validRequest,validResult,empty,WorkManagement};
