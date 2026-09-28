'use strict';
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process'),{randomUUID}=require('node:crypto');
const kinds=['layout_status','layout_init','install','engagement','project','memory','doctor','verify_status','verify_run','invariants'];
const valid=v=>v&&Object.keys(v).sort().join()==='answers,kind,suites'&&kinds.includes(v.kind)&&v.answers&&typeof v.answers==='object'&&!Array.isArray(v.answers)&&Array.isArray(v.suites)&&v.suites.length<=32&&v.suites.every(s=>typeof s==='string'&&s.length<=100)&&Buffer.byteLength(JSON.stringify(v))<=40000;
const empty=()=>({jobPlan:null,jobRun:null,jobError:null});
function environment(){const env={PYTHONDONTWRITEBYTECODE:'1',PYTHONIOENCODING:'utf-8',PYTHONUNBUFFERED:'1'};for(const k of ['SystemRoot','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','USERPROFILE','LOCALAPPDATA'])if(process.env[k])env[k]=process.env[k];return env;}
class PanelJobs{
 constructor(store,{python,core,profile,spawnProcess=spawn}){Object.assign(this,{store,python,core,profile,spawn:spawnProcess});this.pending=null;this.child=null;Object.assign(store.value,empty());}
 reset(){this.pending=null;Object.assign(this.store.value,empty());}
 discard(){if(this.store.value.busy)return;this.pending=null;this.store.value.jobPlan=null;this.store.publish();}
 async process(request,onOutput,seconds){
  return new Promise(resolve=>{
   let size=0,closed=false,result='',reason=null,timer,child;
   const finish=code=>{if(closed)return;closed=true;clearTimeout(timer);this.child=null;resolve({code,output:result,reason});};
   try{child=this.spawn(this.python,['-I','-B',path.join(this.core,'scripts','cc_panel_jobs.py')],{cwd:this.core,env:environment(),shell:false,windowsHide:true,stdio:['pipe','pipe','pipe']});this.child=child;}catch{finish(-1);return;}
   const stop=why=>{reason=why;this.stop();};
   timer=setTimeout(()=>stop('timeout'),seconds*1000);
   const output=chunk=>{size+=chunk.length;if(size>2*1024*1024){stop('output_limit');return;}const t=chunk.toString('utf8');result+=t;if(onOutput)onOutput(t);};
   child.stdout.on('data',output);child.stderr.on('data',output);child.on('error',()=>finish(-1));child.on('close',finish);child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(request));
  });
 }
 stop(){const c=this.child;if(!c||c.exitCode!==null)return;if(process.platform==='win32'){
  const killer=spawn(path.join(process.env.SystemRoot||'C:\\Windows','System32','taskkill.exe'),['/PID',String(c.pid),'/T','/F'],{windowsHide:true,stdio:'ignore',shell:false});killer.on('error',()=>c.kill());killer.on('close',code=>{if(code&&c.exitCode===null)c.kill();});
 }else c.kill('SIGTERM');}
 cancel(){if(this.store.value.jobRun?.status==='running'){this.store.value.jobRun.status='cancelling';this.stop();this.store.publish();}}
 async preview(value){
  const s=this.store,v=s.value;if(v.busy||!v.project)return;if(!valid(value))throw Error('Invalid job');
  this.pending=null;Object.assign(v,{busy:true,committing:true,jobPlan:null,jobError:null,jobRun:null});s.publish();
  const r=await this.process({operation:'preview',root:v.project,value},null,15);
  try{const d=JSON.parse(r.output);if(r.code!==0||!d.ok||!d.plan||d.plan.root!==v.project)throw Error(d.code||r.reason||'preview_failed');v.jobPlan=d.plan;this.pending={value:JSON.parse(JSON.stringify(value)),approval_id:d.plan.approval_id};}
  catch(e){v.jobError=/^[a-z_]+$/.test(e.message)?e.message:'preview_failed';}
  v.busy=false;v.committing=false;s.publish();
 }
 async run(){
  const s=this.store,v=s.value;if(v.busy||!this.pending||v.jobPlan?.blockers.length)return;
  const pending=this.pending;this.pending=null;
  const directory=path.join(this.profile,'jobs',randomUUID());fs.mkdirSync(directory,{recursive:true});
  const run={kind:pending.value.kind,status:'running',startedAt:new Date().toISOString(),output:'',exitCode:null,log:path.join(directory,'output.txt')};
  Object.assign(v,{busy:true,committing:true,jobRun:run,jobError:null,jobPlan:null});s.publish();
  const r=await this.process({operation:'run',root:v.project,...pending,answer_path:path.join(directory,'answers.json')},t=>{run.output=(run.output+t).slice(-64000);s.publish();},1800);
  run.status=run.status==='cancelling'?'cancelled':r.reason|| (r.code===0?'completed':'failed');run.exitCode=r.code;run.finishedAt=new Date().toISOString();
  try{fs.writeFileSync(run.log,r.output,{flag:'wx'});fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify({...run,output:undefined,project:v.project},null,2),{flag:'wx'});}catch{v.jobError='report_save_failed';}
  v.busy=false;v.committing=false;v.state=null;v.observedAt=null;
  v.activity.unshift({operation:'job_'+run.kind,outcome:run.status,time:run.finishedAt,code:r.reason});v.activity=v.activity.slice(0,100);s.publish();
  if(v.knowledge?.enabled&&v.knowledge.policy.automatic)await s.knowledge?.run('sync','source-change');
 }
}
module.exports={PanelJobs,valid,empty,environment};
