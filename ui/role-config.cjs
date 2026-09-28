'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {physicalDirectory,separateProfile}=require('./profile-paths.cjs');
const {prompts}=require('./role-prompts.cjs');
const {generationDefaults,validGeneration}=require('./ai-capabilities.cjs');
const MAX_FILE_BYTES=131072;
const LEGACY_ROLES=['concierge','documentation','architect','reviewer','developer','embedding'];
const ROLES=[...LEGACY_ROLES,'memory_curator'];
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const keys=(v,want)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()===want.split(',').sort().join();
const integer=(v,min,max)=>Number.isInteger(v)&&v>=min&&v<=max;
function defaults(){return Object.fromEntries(ROLES.map(id=>[id,{enabled:false,provider:'ollama',model:'',context:'none',mode:'reviewed',setupProposals:false,maxOutputTokens:id==='memory_curator'?4096:2048,timeoutSeconds:120,maxContextChars:12000,maxRequests:id==='memory_curator'?6:20,systemPrompt:prompts[id],generation:{...generationDefaults(),...(id==='memory_curator'?{format:'json'}:{})}}]));}
function validRoles(value,legacy=false,allowHandoff=true,roleIds=ROLES){return keys(value,roleIds.join())&&roleIds.every(id=>{
 const c=value[id];return keys(c,'enabled,provider,model,context,mode,setupProposals,maxOutputTokens,timeoutSeconds,maxContextChars,maxRequests'+(legacy?'':',systemPrompt,generation'))&&
 typeof c.enabled==='boolean'&&['ollama','openai'].includes(c.provider)&&typeof c.model==='string'&&c.model.length<=160&&!/[\x00-\x1f\x7f]/.test(c.model)&&(!c.enabled||(allowHandoff&&c.mode==='handoff')||!!c.model.trim())&&
 ['none','memory','setup'].includes(c.context)&&(allowHandoff?['reviewed','direct','handoff']:['reviewed','direct']).includes(c.mode)&&typeof c.setupProposals==='boolean'&&
 (!c.setupProposals||['concierge','architect'].includes(id))&&(id!=='embedding'||(c.context==='none'&&!c.setupProposals&&c.mode!=='handoff'))&&(id!=='memory_curator'||(!c.setupProposals&&c.context!=='setup'))&&
 integer(c.maxOutputTokens,128,id==='memory_curator'?4096:8192)&&integer(c.timeoutSeconds,5,120)&&integer(c.maxContextChars,id==='memory_curator'?4096:0,id==='memory_curator'?12000:16000)&&integer(c.maxRequests,1,id==='memory_curator'?6:100)&&(legacy||(typeof c.systemPrompt==='string'&&c.systemPrompt.length<=4000&&!/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(c.systemPrompt)&&validGeneration(c.generation)&&(id==='embedding'?(c.systemPrompt===''&&Object.entries(generationDefaults()).every(([key,value])=>c.generation[key]===value)):!!c.systemPrompt.trim())));
 });}
function projectIdentity(root){const physical=physicalDirectory(root);return process.platform==='win32'?physical.toLowerCase():physical;}
class RoleStorage{
 constructor(profile){this.profile=profile;}
 target(project){const root=separateProfile(this.profile,project),dir=path.join(root,'ai-roles');physicalDirectory(dir);return {dir,file:path.join(dir,hash(projectIdentity(project))+'.json')};}
 read(project){const {file}=this.target(project);let info;try{info=fs.lstatSync(file);}catch(e){if(e.code==='ENOENT')return {roles:defaults(),revision:null};throw e;}
  if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.size>MAX_FILE_BYTES)throw Error('invalid_role_file');
  const fd=fs.openSync(file,'r');let bytes;try{const actual=fs.fstatSync(fd);if(actual.ino!==info.ino||actual.dev!==info.dev||actual.size!==info.size)throw Error('role_file_changed');const buffer=Buffer.alloc(MAX_FILE_BYTES+1);let length=0,n;while(length<buffer.length&&(n=fs.readSync(fd,buffer,length,buffer.length-length,null))>0)length+=n;if(length!==info.size||length>MAX_FILE_BYTES)throw Error('role_file_changed');bytes=buffer.subarray(0,length);}finally{fs.closeSync(fd);}
  const v=JSON.parse(bytes.toString('utf8'));if(!keys(v,'schemaVersion,project,roles')||![1,2,3,4].includes(v.schemaVersion)||v.project!==projectIdentity(project)||!validRoles(v.roles,v.schemaVersion===1,v.schemaVersion>=3,v.schemaVersion===4?ROLES:LEGACY_ROLES))throw Error('invalid_role_file');
  const old=v.schemaVersion===1?Object.fromEntries(LEGACY_ROLES.map(id=>[id,{...v.roles[id],systemPrompt:prompts[id],generation:generationDefaults()}])):v.roles;
  const roles=v.schemaVersion===4?old:{...old,memory_curator:defaults().memory_curator};
  return {roles,revision:hash(bytes)};
 }
 save(project,roles,revision){
  if(!validRoles(roles))throw Error('invalid_role_configuration');
  if(this.read(project).revision!==revision)throw Error('role_file_changed');
  const {dir,file}=this.target(project);fs.mkdirSync(dir,{recursive:true});physicalDirectory(dir);
  const bytes=JSON.stringify({schemaVersion:4,project:projectIdentity(project),roles},null,2)+'\n';
  if(Buffer.byteLength(bytes)>MAX_FILE_BYTES)throw Error('invalid_role_file');
  const temp=path.join(dir,crypto.randomUUID()+'.tmp');let owned=false;
  try{const fd=fs.openSync(temp,'wx',0o600);owned=true;
   try{fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
   if(this.read(project).revision!==revision)throw Error('role_file_changed');fs.renameSync(temp,file);owned=false;
  }finally{if(owned)fs.unlinkSync(temp);}
  return hash(bytes);
 }
}
module.exports={ROLES,defaults,validRoles,RoleStorage,projectIdentity};
