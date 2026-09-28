'use strict';
const {validDraft}=require('./config-contract.cjs');
const emptyConfiguration=()=>({configuration:null,configPreview:null,configAnalysis:null,configError:null,configSaved:null,configDraftVersion:0});
class ConfigurationState {
 constructor(store){this.store=store;this.pending=null;}
 reset(){this.pending=null;}
 discard(){if(this.store.value.busy)return;this.pending=null;Object.assign(this.store.value,{configPreview:null,configAnalysis:null,configSaved:null,configError:null});this.store.publish();}
 async run(action,draft,extra){
  const s=this.store,v=s.value;
  if(v.busy||!v.project)return;
  if(!['read','preview','commit','analysis','import'].includes(action))return;
  const applying=action==='commit';
  let options={};
  if(applying){if(!this.pending||!v.configPreview?.write_supported||v.configPreview.blockers.length)return;options=this.pending;}
  else if(action!=='read'){
   if(!v.configuration||!validDraft(draft))throw Error('Invalid configuration draft');
   options={draft:JSON.parse(JSON.stringify(draft))};
   if(action==='preview')Object.assign(options,{revision:v.configuration.revision,intent:extra});
   if(action==='import')options.proposal=extra;
  }
  const generation=s.generation,root=v.project;
  this.pending=null;
  Object.assign(v,{busy:true,committing:applying,configPreview:null,configError:null,configSaved:null,configAnalysis:null});
  if(action==='read')v.configuration=null;
  s.publish();let response;
  try{response=await s.client.run(`config_${action}_v1`,root,options);}
  catch{response={status:'error',error:{code:'helper_failure',source:'configuration'}};}
  if(generation!==s.generation)return;
  v.busy=false;v.committing=false;
  if(response.status==='ok'){
   const r=response.result;
   if(action==='read'){v.configuration=r.configuration;v.configDraftVersion++;}
   if(action==='preview'){v.configPreview=r.config_preview;this.pending={...options,draft:r.config_preview.draft,approval_id:r.config_preview.approval_id};}
   if(action==='analysis')v.configAnalysis=r.config_analysis;
   if(action==='import'){v.configuration={...v.configuration,draft:r.config_draft,local_changes:true};v.configDraftVersion++;}
   if(applying){v.configSaved=r.config_saved;v.configuration={draft:options.draft,revision:r.config_saved.revision,saved:true,path:'.controlcoding/panel-setup-draft.json'};v.configDraftVersion++;
    if(options.intent==='apply'){v.state=null;v.preview=null;v.observedAt=null;s.mapRequest=null;v.map=null;v.mapScope=null;v.mapObservedAt=null;s.reviewRequest=null;v.reviewPreview=null;s.controlsRequest=null;v.controlsScope=null;s.analysisRequest=null;v.analysisScope=null;s.refresh.reset();}
   }
  }else v.configError=response.error;
  v.activity.unshift({operation:'config_'+action,outcome:response.status==='ok'?(applying?'Saved':'Prepared'):'Unavailable',time:new Date().toISOString(),code:response.error?.code||null});
  v.activity=v.activity.slice(0,100);s.publish();
 }
}
module.exports={ConfigurationState,emptyConfiguration};
