'use strict';
const fs=require('node:fs'),path=require('node:path');

function physicalDirectory(value) {
  if(typeof value!=='string'||!value||value.length>4096||value.includes('\0')||!path.isAbsolute(value))
    throw Error('An absolute ordinary directory is required');
  if(process.platform==='win32' && (!/^[A-Za-z]:[\\/]/.test(value)||value.replaceAll('/','\\').startsWith('\\')))
    throw Error('Device and UNC namespace paths are unsupported');
  const normalized=path.resolve(value),parts=normalized.slice(path.parse(normalized).root.length).split(path.sep).filter(Boolean);
  if(parts.length>128)throw Error('Directory depth limit');
  let current=path.parse(normalized).root;
  for(const part of parts){
    if(process.platform==='win32' && (/[ .]$/.test(part)||/[:<>"|?*]/.test(part)))throw Error('Ambiguous Windows path');
    current=path.join(current,part);
    try {const info=fs.lstatSync(current);if(!info.isDirectory()||info.isSymbolicLink())throw Error('Linked or non-directory path');}
    catch(error){if(error.code!=='ENOENT')throw error;}
  }
  let ancestor=normalized;const suffix=[];
  while(!fs.existsSync(ancestor)){
    if(path.dirname(ancestor)===ancestor)throw Error('Directory root does not exist');
    suffix.unshift(path.basename(ancestor));ancestor=path.dirname(ancestor);
  }
  return path.join(fs.realpathSync.native(ancestor),...suffix);
}
function overlaps(left,right){
  if(process.platform==='win32'){left=left.toLowerCase();right=right.toLowerCase();}
  const inside=(a,b)=>{const r=path.relative(a,b);return r===''||(!path.isAbsolute(r)&&r!=='..'&&!r.startsWith('..'+path.sep));};
  return inside(left,right)||inside(right,left);
}
function separateProfile(profile,project){
  const resolved=physicalDirectory(profile);
  if(project && overlaps(resolved,physicalDirectory(project)))throw Error('Project and panel profile must use separate, non-overlapping folders');
  return resolved;
}
module.exports={physicalDirectory,overlaps,separateProfile};
