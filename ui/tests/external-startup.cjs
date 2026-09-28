'use strict';
const {app}=require('electron'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const option=n=>process.argv.find(a=>a.startsWith('--'+n+'='))?.slice(n.length+3);
const build=option('build'),fixtures=option('fixtures'),report=option('report');
const checks=[];
async function run(){
 const source=path.join(fixtures,'source'),workspace=path.join(fixtures,'workspace');fs.mkdirSync(source,{recursive:true});fs.mkdirSync(workspace,{recursive:true});
 fs.writeFileSync(path.join(source,'sentinel.txt'),'preserved');
 const mod=require(path.join(build,'external-app.cjs'));
 const initial=process.argv.slice();
 async function reject(label,profile,selected,binding=null){
  const descriptor=path.join(workspace,'external.json');
  if(binding)fs.writeFileSync(descriptor,JSON.stringify({source:binding}));else if(fs.existsSync(descriptor))fs.unlinkSync(descriptor);
  process.argv=[...initial.filter(a=>!['--profile=','--source=','--workspace='].some(p=>a.startsWith(p))),`--profile=${profile}`,`--source=${selected}`,`--workspace=${workspace}`];
  let refused=false;try{await mod.start();}catch{refused=true;}
  assert(refused,label);assert(!fs.existsSync(profile),label+' did not create profile');assert.equal(fs.readFileSync(path.join(source,'sentinel.txt'),'utf8'),'preserved');checks.push(label);
 }
 await reject('profile child of explicit source',path.join(source,'profile'),source);
 const other=path.join(fixtures,'other-source');fs.mkdirSync(other,{recursive:true});
 await reject('descriptor cannot hide a source containing the profile',path.join(source,'profile'),other,source);
 await reject('explicit source must match bound source before startup',path.join(fixtures,'profile-mismatch'),other,source);
 fs.writeFileSync(report,JSON.stringify({checks},null,2));app.exit(0);
}
run().catch(error=>{fs.writeFileSync(report,JSON.stringify({checks,error:String(error)},null,2));app.exit(1);});
