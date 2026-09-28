'use strict';
// Visible exports only. This parser never inspects another host's databases.
async function parseChat(text,format,title='Imported conversation'){
 if(typeof text!=='string'||!text.trim()||text.length>59000)throw Error('Paste a nonempty export of at most 59,000 characters.');
 if(format==='json'){
  const value=JSON.parse(text);
  if(!value||typeof value.id!=='string'||typeof value.title!=='string'||!Array.isArray(value.turns))throw Error('Invalid portable conversation JSON.');
  return value;
 }
 if(!['text','markdown'].includes(format)||!title.trim()||title.length>180)throw Error('Choose a format and a title of at most 180 characters.');
 const normalized=text.replace(/\r\n?/g,'\n').trim(),bytes=new TextEncoder().encode(normalized+'\0'+format);
 const sha=[...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
 const id='import-'+sha,parts=[];let role=null,lines=[],fence=false;
 const flush=()=>{const content=lines.join('\n').trim();if(content)parts.push({role:role||'user',content:role?content:'[Imported text; speaker unverified]\n'+content,uncertain:!role});lines=[];};
 for(const line of normalized.split('\n')){
  if(/^\s*(```|~~~)/.test(line))fence=!fence;
  const marker=!fence&&format==='markdown'&&/^\s*(?:#{1,6}\s+)?(user|assistant|utente|assistente)\s*:\s*(.*)$/i.exec(line);
  const heading=!fence&&format==='markdown'&&/^#{1,6}\s+(user|assistant|utente|assistente)\s*$/i.exec(line);
  const match=marker||heading;
  if(match){flush();role=/^(user|utente)$/i.test(match[1])?'user':'assistant';if(match[2])lines.push(match[2]);}
  else lines.push(line);
 }
 flush();const turns=[];
 for(const part of parts)for(let start=0;start<part.content.length;start+=15000){
  turns.push({id:id+'-'+turns.length,sequence:turns.length,role:part.role,content:part.content.slice(start,start+15000),
   provenance:{origin:part.uncertain?'external-import:unverified-speaker':'external-import:declared-speaker',provider:'manual'}});
 }
 const value={id,title:title.trim(),retention:'transcript',summary:'Imported visible '+format+' export. Speaker labels and content are unverified; no attachments were imported.',status:'closed',turns};
 if(turns.length>64||new TextEncoder().encode(JSON.stringify(value)).length>59000)throw Error('Export exceeds the bounded conversation import size; split it into reviewed sections.');
 return value;
}
module.exports={parseChat};
