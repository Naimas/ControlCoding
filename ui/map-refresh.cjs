'use strict';
const {createHash} = require('node:crypto');
const {identity, watchProject} = require('./map-watch.cjs');
const MAX_HISTORY = 20, MAX_DIFF = 100;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
function scopeKey(scope) {
  if (!scope) return null;
  const {scope_id, snapshot, revision, ...readScope} = scope;
  return digest(readScope);
}
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().filter(k => !['observed_at','snapshot_id','source_snapshot','preview_id','scope_id'].includes(k)).map(k => [k,stable(value[k])]));
}
function fingerprints(map) {
  const bundle = map.projection.bundle;
  const sources = new Map(bundle.sources.map(s => [s.id, s]));
  const statuses = new Map(map.projection.statuses.map(s => [s.id, s]));
  const edges=new Map(),constraints=new Map(),findings=new Map();
  const add=(index,id,item)=>{if(!index.has(id))index.set(id,[]);index.get(id).push(item);};
  for(const edge of bundle.edges){add(edges,edge.source,edge);if(edge.target!==edge.source)add(edges,edge.target,edge);}
  for(const constraint of bundle.constraints||[])add(constraints,constraint.subject,constraint);
  for(const finding of map.analysis?.findings||[])for(const id of finding.subjects)add(findings,id,finding);
  const result = new Map();
  for (const node of bundle.nodes) result.set(node.id, digest(stable({node,
    sources:node.sources.map(id => sources.get(id)), status:statuses.get(node.id),
    edges:edges.get(node.id)||[],constraints:constraints.get(node.id)||[],findings:findings.get(node.id)||[]})));
  return result;
}
class MapRefresh {
  constructor(store, options = {}) {
    this.store=store; this.now=options.now||Date.now;
    this.setTimer=options.setTimer||setTimeout;this.clearTimer=options.clearTimer||clearTimeout;
    this.identity=options.identity||identity;this.watch=options.watch||watchProject;
    this.debounce=options.debounce??750;this.interval=options.interval??30000;this.minimum=options.minimum??3000;
    this.reset();
  }
  reset() {
    this.stop();this.epoch=0;this.pending=false;this.running=false;this.scopeChanged=null;this.lastStart=-Infinity;
    this.approved={controls:null,analysis:null};this.lastMap=null;this.previous=null;
    this.history=[];this.order=[];this.observation=0;this.scanEpoch=null;
    this.status='manual';this.reason=null;this.pendingReason=null;this.rootIdentity=null;
  }
  stop() {
    this.enabled=false;this.pending=false;this.pendingReason=null;this.clearTimer(this.timer);this.clearTimer(this.periodic);
    this.timer=null;this.periodic=null;this.watcher?.close();this.watcher=null;
  }
  dispose() {this.stop();this.epoch++;}
  toggle() {
    if (this.enabled) {this.stop();this.epoch++;if(this.running)this.store.client.cancel();this.status='paused';this.store.publish();return;}
    const value=this.store.value;
    if(value.busy||!value.map||!this.store.mapRequest)return;
    this.rootIdentity=this.identity(value.project);
    if(!this.rootIdentity){this.rootChanged();return;}
    this.enabled=true;this.status='watching';this.armWatch();this.reconcileTimer();this.store.publish();
  }
  armWatch() {
    this.watcher?.close();const generation=this.store.generation;
    this.watcher=this.watch(this.store.value.project, reason=>{
      if(this.enabled&&generation===this.store.generation)this.invalidate(reason);
    },this.rootIdentity);
  }
  reconcileTimer() {
    this.clearTimer(this.periodic);
    if(!this.enabled)return;
    this.periodic=this.setTimer(()=>{
      if(this.enabled){this.armWatch();this.invalidate('reconciliation');this.reconcileTimer();}
    },this.interval);this.periodic?.unref?.();
  }
  rootChanged() {
    this.stop();this.epoch++;this.store.generation++;this.store.value.generation=this.store.generation;
    // Do not interrupt an owned mapping transaction. Its late result is fenced;
    // the state owner releases its busy guard when that transaction returns.
    if(!this.store.value.committing){this.store.client.cancel();this.store.value.busy=false;this.store.mapActive=false;}
    this.running=false;this.scanEpoch=null;this.history=[];this.previous=null;this.order=[];
    this.approved={controls:null,analysis:null};this.status='unavailable';this.reason='root_changed';
    Object.assign(this.store.value,{map:null,mapObservedAt:null,mapScope:null,controlsScope:null,analysisScope:null,
      reviewPreview:null,mapError:{code:'root_changed',source:'refresh'}});
    this.store.mapRequest=null;this.store.reviewRequest=null;this.store.controlsRequest=null;this.store.analysisRequest=null;
    this.store.publish();
  }
  invalidate(reason) {
    if(!this.enabled)return;
    if(!this.pending&&this.identity(this.store.value.project)!==this.rootIdentity){this.rootChanged();return;}
    // A periodic timer is not evidence of a write. Do not starve a bounded scan
    // that takes longer than the reconciliation interval.
    const priority={reconciliation:0,watcher_unavailable:1,filesystem:2,git_metadata:3};
    if(this.pendingReason===null||priority[reason]>priority[this.pendingReason])this.pendingReason=reason;
    if(reason==='reconciliation'&&this.store.value.busy){this.pending=true;this.schedule();return;}
    // One pending invalidation, no unbounded event queue. A hint never validates data.
    this.epoch++;this.pending=true;this.reason=this.pendingReason;this.status='stale';
    Object.assign(this.store.value,{map:null,mapObservedAt:null,reviewPreview:null});this.store.reviewRequest=null;
    if(this.store.mapActive&&!this.store.value.committing)this.store.client.cancel();
    this.store.publish();this.schedule();
  }
  schedule() {
    if(!this.enabled||!this.pending||this.timer)return;
    const delay=Math.max(this.debounce,this.minimum-(this.now()-this.lastStart));
    this.timer=this.setTimer(()=>{this.timer=null;void this.run();},delay);this.timer?.unref?.();
  }
  capture() {
    const value=this.store.value;
    if(!this.running){
      if(value.busy&&this.scanEpoch===null){this.scanEpoch=this.epoch;if(this.store.mapActive)this.status='updating';}
      if(!value.busy&&this.scanEpoch!==null){
        if(this.scanEpoch!==this.epoch){value.map=null;value.mapObservedAt=null;value.reviewPreview=null;this.store.reviewRequest=null;}
        if(!value.map&&value.mapError)this.status='unavailable';
        else if(!value.map&&!this.pending&&this.status==='updating')this.status='awaiting_read';
        this.scanEpoch=null;
      }
      if(value.map&&value.map!==this.lastMap&&value.map.projection){
        this.remember(value.map);this.lastMap=value.map;
        if(value.map.controls)this.approved.controls=scopeKey(value.controlsScope);
        if(value.map.analysis)this.approved.analysis=scopeKey(value.analysisScope);
        if(this.scopeChanged==='controls'&&value.map.controls||this.scopeChanged==='analysis'&&value.map.analysis||
          this.scopeChanged==='controls and analysis'&&value.map.controls&&value.map.analysis)this.scopeChanged=null;
        this.status=this.enabled?'watching':'manual';
      }
      if(!value.busy&&this.pending)this.schedule();
    }
    value.mapRefresh={enabled:!!this.enabled,status:this.status,reason:this.reason,
      intervalSeconds:this.interval/1000,watcherCount:this.watcher?.count||0,scopeChanged:this.scopeChanged||null};
    value.mapHistory=this.history;value.mapOrder=this.order;
  }
  remember(map) {
    const next=fingerprints(map),old=this.previous;
    const sameRoot=old?.root===map.root_identity;
    const before=sameRoot?old.nodes:new Map();
    const added=[...next.keys()].filter(id=>!before.has(id)),removed=[...before.keys()].filter(id=>!next.has(id));
    const changed=[...next.keys()].filter(id=>before.has(id)&&before.get(id)!==next.get(id));
    const surviving=this.order.filter(id=>next.has(id)),known=new Set(surviving);
    this.order=[...surviving,...[...next.keys()].filter(id=>!known.has(id))];
    if(!sameRoot&&old){this.history=[];this.order=[...next.keys()];this.approved={controls:null,analysis:null};this.scopeChanged=null;}
    this.history=[{id:++this.observation,at:new Date(this.now()).toISOString(),
      snapshot:map.review.source_snapshot,revision:map.review.revision,
      previousSnapshot:sameRoot?old.snapshot:null,previousRevision:sameRoot?old.revision:null,
      nodes:next.size,added:added.length,changed:changed.length,removed:removed.length,
      changedIds:[...added,...changed,...removed].slice(0,MAX_DIFF),
      truncated:added.length+changed.length+removed.length>MAX_DIFF,
      controls:!!map.controls,analysis:!!map.analysis,reason:this.reason||'manual'},...this.history].slice(0,MAX_HISTORY);
    this.previous={root:map.root_identity,nodes:next,snapshot:map.review.source_snapshot,revision:map.review.revision};
  }
  async run() {
    if(!this.enabled||!this.pending||this.running)return;
    if(this.store.value.busy||this.store.value.reviewPreview){this.schedule();return;}
    const store=this.store,value=store.value,root=value.project,generation=store.generation,epoch=this.epoch;
    if(this.identity(root)!==this.rootIdentity){this.rootChanged();return;}
    this.pending=false;this.running=true;this.scanEpoch=null;this.lastStart=this.now();this.status='updating';
    this.reason=this.pendingReason||'reconciliation';this.pendingReason=null;
    value.busy=true;store.mapActive=true;value.map=null;value.mapObservedAt=null;value.mapError=null;
    store.reviewRequest=null;value.reviewPreview=null;value.reviewError=null;
    store.publish();
    const alive=()=>generation===store.generation&&this.enabled&&epoch===this.epoch;
    const run=async(op,options)=>{
      if(!alive())throw {code:'cancelled'};
      const response=await store.client.run(op,root,options);
      if(!alive())throw {code:'cancelled'};
      if(this.identity(root)!==this.rootIdentity)throw {code:'root_changed'};
      if(response.status!=='ok')throw {code:response.error?.code||'helper_failure'};
      return response;
    };
    try {
      const options={design_paths:[...value.designPaths],observed_at:new Date(this.now()).toISOString().replace(/\.\d{3}Z$/,'Z')};
      const preview=await run('map_preview_v1',options);
      const request={...options,preview_id:preview.result.scope.preview_id};
      let response=await run('map_read_v1',request),map=response.result.map;
      let controlsScope=null,analysisScope=null,controlsRequest=null,analysisRequest=null;
      const binding={...request,snapshot:map.review.source_snapshot,revision:map.review.revision};
      if(this.approved.controls){
        controlsScope=(await run('map_controls_preview_v1',binding)).result.controls_scope;
        if(scopeKey(controlsScope)===this.approved.controls){
          controlsRequest={...binding,scope_id:controlsScope.scope_id};
          response=await run('map_controls_read_v1',controlsRequest);map=response.result.map;
        } else {this.scopeChanged='controls';controlsScope=null;this.approved.controls=null;}
      }
      if(this.approved.analysis){
        const analysisOptions={...binding,controls_scope_id:controlsRequest?.scope_id||null};
        analysisScope=(await run('map_analysis_preview_v1',analysisOptions)).result.analysis_scope;
        if(scopeKey(analysisScope)===this.approved.analysis){
          analysisRequest={...analysisOptions,scope_id:analysisScope.scope_id};
          response=await run('map_analysis_read_v1',analysisRequest);map=response.result.map;
        } else {this.scopeChanged=this.scopeChanged?'controls and analysis':'analysis';analysisScope=null;this.approved.analysis=null;}
      }
      // All operations validated, same selected root and invalidation generation.
      if(!alive())throw {code:'cancelled'};
      store.mapRequest=request;store.controlsRequest=controlsRequest;store.analysisRequest=analysisRequest;
      Object.assign(value,{map,mapObservedAt:response.observed_at,mapScope:preview.result.scope,controlsScope,analysisScope});
      this.remember(map);this.lastMap=map;this.status='watching';
    } catch(error) {
      if(generation!==store.generation)return;
      value.map=null;value.mapObservedAt=null;
      if(error.code==='root_changed'){this.rootChanged();}
      else if(epoch===this.epoch){
        value.mapError={code:error.code||'helper_failure',source:'refresh'};this.status='unavailable';
      }
    } finally {
      if(generation===store.generation){
        this.running=false;value.busy=false;store.mapActive=false;
        value.activity.unshift({operation:'map_auto_refresh',outcome:value.map?'Observed':'Unavailable',time:new Date(this.now()).toISOString(),code:value.mapError?.code||null});
        value.activity=value.activity.slice(0,100);store.publish();this.schedule();
      }
    }
  }
}
module.exports={MapRefresh,scopeKey,fingerprints,MAX_HISTORY,MAX_DIFF};
