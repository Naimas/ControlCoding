'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,dialog}=require('electron');
const option=n=>process.argv.find(a=>a.startsWith(`--${n}=`))?.slice(n.length+3);
const build=option('build'),artifacts=option('artifacts'),profile=option('profile'),fixtures=option('fixtures');
const checks=[];const record=(label,value)=>{assert(value,label);checks.push(label);};
const timeout=setTimeout(()=>app.exit(2),45000);timeout.unref();
async function run(){
 const start=require(path.join(build,'panel-app.cjs')).start;
 if(option('mode')==='reject-start'){
  const root=option('project'),before=fs.readdirSync(root).sort();
  await assert.rejects(start());record('startup rejected before profile configuration',app.getPath('userData')!==profile);
  record('adopter directory unchanged',JSON.stringify(before)===JSON.stringify(fs.readdirSync(root).sort()));
 } else {
  const root=path.join(fixtures,'Accepted project');fs.mkdirSync(root,{recursive:true});fs.writeFileSync(path.join(root,'keep.txt'),'keep');
  const panel=await start(),web=panel.window.webContents,js=s=>web.executeJavaScript(s,true);
  if(process.env.CC_ALIAS_PATH){
   const {separateProfile}=require(path.join(build,'profile-paths.cjs'));
   assert.throws(()=>separateProfile(process.env.CC_ALIAS_PATH,process.env.ProgramFiles));
   record('real Electron resolves existing 8.3 alias before comparison',true);
  }
  dialog.showOpenDialog=async()=>({canceled:false,filePaths:[root]});await js('window.panel.chooseProject()');
  const generation=panel.store.generation;let calls=0;const original=panel.client.run.bind(panel.client);panel.client.run=(...a)=>{calls++;return original(...a);};
  for(const denied of [profile,path.dirname(profile),path.join(profile,'new-project'),'\\\\?\\'+profile]){
   dialog.showOpenDialog=async()=>({canceled:false,filePaths:[denied]});
   record('native chooser refuses '+checks.length,await js('window.panel.chooseProject().then(()=>false,()=>true)'));
   record('selection remains unchanged '+checks.length,panel.store.value.project===root&&panel.store.generation===generation);
  }
  record('rejected selections never invoke Python',calls===0);
  record('no project source writes',fs.readFileSync(path.join(root,'keep.txt'),'utf8')==='keep'&&fs.readdirSync(root).join()==='keep.txt');
 }
 fs.writeFileSync(path.join(artifacts,'profile-smoke.json'),JSON.stringify({checks,versions:process.versions},null,2));app.exit(0);
}
run().catch(e=>{fs.writeFileSync(path.join(artifacts,'profile-failure.json'),JSON.stringify({checks,error:String(e),stack:e.stack},null,2));app.exit(1);});
