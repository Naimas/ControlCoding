'use strict';
// Private, project-bound pending visible turns. Never holds API credentials.
const fs=require('node:fs'),path=require('node:path'),{createHash,randomUUID}=require('node:crypto');
const {physicalDirectory}=require('./profile-paths.cjs');
const MAX=512*1024;
class KnowledgeOutbox{
 constructor(profile){this.profile=profile;}
 location(root){
  const project=physicalDirectory(root),key=process.platform==='win32'?project.toLowerCase():project;
  const directory=path.join(physicalDirectory(this.profile),'knowledge-outbox');
  if(!fs.existsSync(directory))fs.mkdirSync(directory);
  physicalDirectory(directory);
  return {file:path.join(directory,createHash('sha256').update(key).digest('hex')+'.json'),key};
 }
 ordinary(file){const stat=fs.lstatSync(file);if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||stat.size>MAX)throw Error('invalid_conversation_outbox');}
 load(root){
  const {file,key}=this.location(root);if(!fs.existsSync(file))return [];
  this.ordinary(file);const value=JSON.parse(fs.readFileSync(file,'utf8'));
  if(value.version!==1||value.project!==key||!Array.isArray(value.entries)||value.entries.length>64||value.entries.some(e=>typeof e.role!=='string'||!e.value||typeof e.value.id!=='string'||!Array.isArray(e.value.turns)||e.value.turns.some(t=>!Number.isSafeInteger(t.sequence)||!['user','assistant'].includes(t.role)||typeof t.content!=='string')))throw Error('invalid_conversation_outbox');
  return value.entries;
 }
 save(root,entries){
  const {file,key}=this.location(root);
  if(fs.existsSync(file))this.ordinary(file);
  if(!entries.length){if(fs.existsSync(file))fs.unlinkSync(file);return;}
  const raw=JSON.stringify({version:1,project:key,entries:entries.map(e=>({role:e.role,value:e.value}))});
  if(entries.length>64||Buffer.byteLength(raw)>MAX)throw Error('conversation_outbox_limit');
  const temporary=path.join(path.dirname(file),randomUUID()+'.tmp');
  const fd=fs.openSync(temporary,'wx',0o600);
  try{fs.writeFileSync(fd,raw);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  try{fs.renameSync(temporary,file);}catch(error){fs.unlinkSync(temporary);throw error;}
 }
}
module.exports={KnowledgeOutbox};
