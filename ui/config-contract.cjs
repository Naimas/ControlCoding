'use strict';
const fields=['stack','architecture','boundaries','rules','invariants','verification'];
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const revision=v=>v==='absent'||hash(v);
const object=v=>v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join()===keys.sort().join();
const strings=v=>Array.isArray(v)&&v.every(x=>typeof x==='string');
const bounded=(v,n)=>typeof v==='string'&&v.length<=n;
function validDraft(d){
 return exact(d,['schema_version','name','kind','goal','user_host','documentation_mode','memory_policy','design_paths','decisions'])&&
  d.schema_version===1&&bounded(d.name,120)&&d.name.trim().length>0&&bounded(d.goal,2000)&&['new','existing'].includes(d.kind)&&
  ['codex_cli','claude_code','cursor','windsurf','vscode','cline','gemini_cli','other'].includes(d.user_host)&&
  ['managed','project_managed'].includes(d.documentation_mode)&&['deferred','governed_scope'].includes(d.memory_policy)&&
  strings(d.design_paths)&&d.design_paths.length<=16&&d.design_paths.every(p=>p.length<=256)&&
  exact(d.decisions,[...fields])&&fields.every(f=>{const r=d.decisions[f];return exact(r,['mode','value','status','rationale','evidence','analysis_id'])&&
   ['manual','ai','defer'].includes(r.mode)&&['unset','proposed','accepted','rejected'].includes(r.status)&&bounded(r.value,4000)&&bounded(r.rationale,2000)&&
   strings(r.evidence)&&r.evidence.length<=16&&r.evidence.every(p=>p.length<=256)&&(r.analysis_id===''||hash(r.analysis_id));})&&Buffer.byteLength(JSON.stringify(d))<=49152;
}
const operations=['config_read_v1','config_preview_v1','config_commit_v1','config_analysis_v1','config_import_v1'];
function validRequest(op,o){
 const keys={config_read_v1:[],config_preview_v1:['draft','revision','intent'],config_commit_v1:['draft','revision','intent','approval_id'],config_analysis_v1:['draft'],config_import_v1:['draft','proposal']}[op];
 if(!keys||!exact(o,[...keys])||Buffer.byteLength(JSON.stringify(o))>60000)return false;
 return (!keys.includes('draft')||validDraft(o.draft))&&(!keys.includes('revision')||revision(o.revision)&&['save','apply'].includes(o.intent))&&
  (!keys.includes('approval_id')||hash(o.approval_id))&&(!keys.includes('proposal')||object(o.proposal));
}
function validResult(op,r,o){
 if(op==='config_read_v1'){const c=r.configuration;return c&&validDraft(c.draft)&&revision(c.revision)&&typeof c.saved==='boolean'&&['.controlcoding/panel-setup-draft.json','cc/.controlcoding/panel-setup-draft.json'].includes(c.path);}
 if(op==='config_preview_v1'){const p=r.config_preview;return p?.schema_version===1&&p.operation===o.intent&&p.revision===o.revision&&p.root===r.project_root&&validDraft(p.draft)&&hash(p.approval_id)&&
  strings(p.blockers)&&strings(p.notices)&&typeof p.write_supported==='boolean'&&p.configured_not_verified===true&&Array.isArray(p.files)&&p.files.length<=256&&p.files.every(f=>
   bounded(f.path,1024)&&['create','keep','update','conflict'].includes(f.action)&&hash(f.sha256)&&(f.before_sha256===null||hash(f.before_sha256))&&Number.isSafeInteger(f.bytes)&&f.bytes>=0);}
 if(op==='config_commit_v1'){const c=r.config_saved;return c?.saved===true&&c.operation===o.intent&&hash(c.revision)&&strings(c.files)&&strings(c.directories_created)&&c.configured_not_verified===true&&c.host_delivery==='unverified';}
 if(op==='config_analysis_v1'){const p=r.config_analysis;return p?.schema_version===1&&hash(p.request_id)&&validDraft(p.choices)&&Array.isArray(p.sources)&&p.sources.length<=3000&&p.sources.every(s=>bounded(s.path,1024))&&typeof p.instructions==='string';}
 return validDraft(r.config_draft);
}
module.exports={fields,validDraft,validRequest,validResult,operations};
