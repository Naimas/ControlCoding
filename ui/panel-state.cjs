'use strict';
const TABS = ['Status', 'Project Map', 'Activity', 'Setup', 'Agents', 'Memory', 'Checks'];
const {validChange} = require('./bridge-client.cjs');
const {MapRefresh} = require('./map-refresh.cjs');
const {ConfigurationState,emptyConfiguration}=require('./config-state.cjs');
const {WorkManagement,empty:emptyManagement}=require('./work-manage.cjs');
const {DocumentationState,empty:emptyDocumentation}=require('./documentation-state.cjs');
const emptyWork = () => ({work:null,workScope:null,workError:null,workObservedAt:null});
const emptyMap = () => ({ map: null, mapScope: null, mapError: null, mapObservedAt: null, designPaths: [], reviewPreview:null,reviewError:null,reviewSaved:null,committing:false,controlsScope:null,analysisScope:null });
function allowedSender(event, window, pageURL) {
  return event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && event.senderFrame.url === pageURL;
}
function safePreferences(input = {}) {
  return { pinned: input.pinned === true, compact: input.compact === true,
    tab: TABS.includes(input.tab) ? input.tab : 'Status',
    theme: input.theme === 'light' ? 'light' : 'dark',
    shortcut: ['CommandOrControl+Shift+Space', 'CommandOrControl+Alt+C', 'disabled'].includes(input.shortcut)
      ? input.shortcut : 'CommandOrControl+Shift+Space',
    bounds: input.bounds && ['x','y','width','height'].every(k => Number.isFinite(input.bounds[k]))
      ? { x: Math.round(input.bounds.x), y: Math.round(input.bounds.y), width: Math.max(380, Math.min(1400, Math.round(input.bounds.width))), height: Math.max(560, Math.min(1100, Math.round(input.bounds.height))) } : null };
}
function visibleBounds(saved, displays, compact) {
  const fallback = displays[0].workArea;
  let area = displays.find(d => saved && saved.x >= d.workArea.x && saved.y >= d.workArea.y &&
    saved.x + 100 <= d.workArea.x + d.workArea.width && saved.y + 100 <= d.workArea.y + d.workArea.height)?.workArea || fallback;
  const width = Math.min(compact ? 400 : saved?.width || 1040, area.width);
  const height = Math.min(compact ? 620 : saved?.height || 760, area.height);
  return { width, height, x: Math.max(area.x, Math.min(saved?.x ?? area.x + 60, area.x + area.width - width)),
    y: Math.max(area.y, Math.min(saved?.y ?? area.y + 60, area.y + area.height - height)) };
}
class PanelState {
  constructor(client, changed = () => {}, refreshOptions = {}, selectGuard = () => {}) { this.client = client; this.changed = changed; this.generation = 0; this.selectGuard = selectGuard;
    this.value = { project: null, generation: 0, busy: false, state: null, preview: null, error: null, observedAt: null, activity: [], ...emptyMap(), ...emptyWork(), ...emptyConfiguration() };
    this.configuration=new ConfigurationState(this);
    this.workManagement=new WorkManagement(this);this.documentation=new DocumentationState(this);Object.assign(this.value,emptyManagement(),emptyDocumentation());
    this.refresh = new MapRefresh(this, refreshOptions); this.refresh.capture(); }
  publish() { this.refresh.capture(); this.changed(this.value); }
  async select(root) {
    if(this.value.committing) return;
    if(root)this.selectGuard(root);
    this.refresh.reset();
    this.consolidation?.reset();this.knowledge?.reset();
    this.client.cancel(); this.generation++;
    this.mapActive = false;
    this.mapRequest = null;
    this.reviewRequest = null;
    this.controlsRequest = null;
    this.analysisRequest = null;
    this.configuration.reset();
    this.workManagement.reset();
    this.value = { project: root, generation: this.generation, busy: false, state: null, preview: null, error: null, observedAt: null, activity: [], ...emptyMap(), ...emptyWork(), ...emptyConfiguration() };
    Object.assign(this.value,emptyManagement(),emptyDocumentation());this.jobs?.reset();this.ai?.reset();this.roles?.reset();this.knowledge?.reset();this.publish(); if (root) {await this.observe('read');await this.knowledge?.run('status');}
  }
  async observe(operation) {
    if (!this.value.project || this.value.busy) return;
    const generation = this.generation, root = this.value.project;
    this.value.busy = true; this.value.error = null;
    // Every operation clears its old result before waiting, including failed refreshes.
    if (operation === 'read') { this.value.state = null; this.value.preview = null; this.value.observedAt = null; }
    else this.value.preview = null;
    this.publish();
    const response = await this.client.run(operation, root);
    if (generation !== this.generation) return;
    this.value.busy = false;
    const ok = response.status === 'ok';
    if (ok) {
      if (operation === 'read') { this.value.state = response.result; this.value.observedAt = response.observed_at; }
      else this.value.preview = { ...response.result, observed_at: response.observed_at };
    } else this.value.error = response.error;
    this.value.activity.unshift({ operation, outcome: ok ? 'Observed' : 'Unavailable',
      time: new Date().toISOString(), code: ok ? null : response.error.code });
    this.value.activity = this.value.activity.slice(0, 100); this.publish();
  }
  async observeWork(action, query='') {
    if(!this.value.project||this.value.busy||!['preview','read'].includes(action)||typeof query!=='string'||query.length>240||/[\x00-\x1f]/.test(query))return;
    if(action==='read'&&!this.value.workScope)return;
    const generation=this.generation,root=this.value.project,scope=this.value.workScope;
    Object.assign(this.value,emptyWork(),{workScope:action==='read'?scope:null,busy:true});this.publish();
    let response;
    try { response=await this.client.run('work_'+action+'_v1',root,action==='read'?{scope_id:scope.scope_id,query}:{}); }
    catch { response={status:'error',error:{code:'helper_failure',source:'controlwork'}}; }
    if(generation!==this.generation)return;
    this.value.busy=false;
    if(response.status==='ok') {
      if(action==='preview')this.value.workScope=response.result.work_scope;
      else {this.value.work=response.result.work;this.value.workObservedAt=response.observed_at;}
    } else {this.value.workError=response.error;this.value.workScope=null;}
    this.value.activity.unshift({operation:'work_'+action,outcome:response.status==='ok'?'Observed':'Unavailable',time:new Date().toISOString(),code:this.value.workError?.code||null});
    this.value.activity=this.value.activity.slice(0,100);this.publish();
  }
  setDesignPaths(paths) {
    if (this.value.busy) return;
    this.refresh.reset();
    this.analysisRequest=null;
    Object.assign(this.value, emptyMap(), {designPaths:[...paths].sort()}); this.mapRequest = null; this.reviewRequest=null; this.controlsRequest=null; this.publish();
  }
  cancelMap() {
    if (!this.value.busy || !this.mapActive) return;
    if(this.value.committing) return;
    this.refresh.stop();this.refresh.epoch++;this.refresh.status='paused';
    this.client.cancel();
  }
  async observeMap(operation) {
    if (!this.value.project || this.value.busy || !['preview','read','refresh'].includes(operation)) return;
    if (operation === 'read' && !this.mapRequest) return;
    const generation = this.generation, root = this.value.project;
    this.controlsRequest=null;this.value.controlsScope=null;
    this.analysisRequest=null;this.value.analysisScope=null;
    this.reviewRequest=null; this.value.reviewPreview=null; this.value.reviewError=null;
    this.value.busy = true; this.mapActive = true;
    Object.assign(this.value, {map:null, mapError:null, mapObservedAt:null});
    if (operation !== 'read') { this.mapRequest = null; this.value.mapScope = null; }
    this.publish();
    let response;
    try {
      if (operation !== 'read') {
        const options = {design_paths:[...this.value.designPaths], observed_at:new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')};
        response = await this.client.run('map_preview_v1', root, options);
        if (generation !== this.generation) return;
        if (response.status === 'ok') {
          this.value.mapScope = response.result.scope;
          this.mapRequest = {...options, preview_id:response.result.scope.preview_id};
        }
      }
      if (operation !== 'preview' && this.mapRequest && (!response || response.status === 'ok')) {
        response = await this.client.run('map_read_v1', root, this.mapRequest);
        if (generation !== this.generation) return;
        if (response.status === 'ok') {
          this.value.map = response.result.map; this.value.mapObservedAt = response.observed_at;
        }
      }
    } catch { response = {status:'error',error:{code:'helper_failure',source:'project_map'}}; }
    if (generation !== this.generation) return;
    this.value.busy = false; this.mapActive = false;
    if (response?.status !== 'ok') {
      this.value.mapError = response?.error || {code:'helper_failure',source:'project_map'};
      this.value.mapScope = null; this.mapRequest = null;
    }
    this.value.activity.unshift({operation:'map_'+operation,outcome:response?.status==='ok'?'Observed':'Unavailable',time:new Date().toISOString(),code:this.value.mapError?.code || null});
    this.value.activity = this.value.activity.slice(0,100); this.publish();
  }
  discardReview() {
    if(this.value.busy) return;
    this.reviewRequest=null;this.value.reviewPreview=null;this.value.reviewError=null;this.publish();
  }
  async review(change) {
    if(this.value.busy || !this.value.map?.review || !this.mapRequest || !validChange(change)) return;
    const review=this.value.map.review;
    const options={...this.mapRequest,snapshot:review.source_snapshot,revision:review.revision,change:JSON.parse(JSON.stringify(change))};
    await this.runReview('map_review_preview_v1',options);
  }
  async saveReview() {
    if(this.value.busy || !this.reviewRequest || !this.value.reviewPreview?.write_supported) return;
    const options=this.reviewRequest;this.reviewRequest=null;
    await this.runReview('map_review_apply_v1',options);
  }
  async runReview(operation,options) {
    const generation=this.generation,root=this.value.project,applying=operation==='map_review_apply_v1';
    this.controlsRequest=null;this.value.controlsScope=null;
    this.analysisRequest=null;this.value.analysisScope=null;
    this.value.busy=true;this.mapActive=!applying;this.value.committing=applying;
    this.value.reviewPreview=null;this.value.reviewError=null;this.value.reviewSaved=null;this.reviewRequest=null;this.publish();
    let response;
    try { response=await this.client.run(operation,root,options); }
    catch {response={status:'error',error:{code:'helper_failure',source:'mapping'}};}
    if(generation!==this.generation){
      if(applying&&this.value.committing){this.value.busy=false;this.value.committing=false;this.mapActive=false;this.publish();}
      return;
    }
    this.value.busy=false;this.mapActive=false;this.value.committing=false;
    if(response.status==='ok') {
      if(applying)this.value.reviewSaved=response.result.review_saved;
      else {this.value.reviewPreview=response.result.review_preview;this.reviewRequest={...options,approval_id:response.result.review_preview.approval_id};}
    } else {
      this.value.reviewError=response.error;
      this.value.map=null;this.value.mapObservedAt=null;this.value.mapScope=null;this.mapRequest=null;
    }
    this.value.activity.unshift({operation,outcome:response.status==='ok'?(applying?'Saved':'Previewed'):'Unavailable',time:new Date().toISOString(),code:response.error?.code||null});
    this.value.activity=this.value.activity.slice(0,100);this.publish();
    if(applying && response.status==='ok')await this.observeMap('refresh');
  }
  async observeControls(operation) {
    if(this.value.busy || !this.mapRequest || !['preview','read'].includes(operation))return;
    if(operation==='preview'&&!this.value.map?.review || operation==='read'&&!this.controlsRequest)return;
    this.analysisRequest=null;this.value.analysisScope=null;
    const generation=this.generation,root=this.value.project;
    const options=operation==='preview'?{...this.mapRequest,snapshot:this.value.map.review.source_snapshot,revision:this.value.map.review.revision}:this.controlsRequest;
    this.value.map=null;this.value.mapObservedAt=null;this.value.mapError=null;this.value.reviewPreview=null;this.reviewRequest=null;
    this.value.busy=true;this.mapActive=true;
    if(operation==='preview'){this.controlsRequest=null;this.value.controlsScope=null;}
    this.publish();let response;
    try {response=await this.client.run(`map_controls_${operation}_v1`,root,options);}
    catch {response={status:'error',error:{code:'helper_failure',source:'controls'}};}
    if(generation!==this.generation)return;
    this.value.busy=false;this.mapActive=false;
    if(response.status==='ok'){
      if(operation==='preview'){this.value.controlsScope=response.result.controls_scope;this.controlsRequest={...options,scope_id:response.result.controls_scope.scope_id};}
      else {this.value.map=response.result.map;this.value.mapObservedAt=response.observed_at;}
    } else {this.value.mapError=response.error;this.value.controlsScope=null;this.controlsRequest=null;}
    this.value.activity.unshift({operation:'controls_'+operation,outcome:response.status==='ok'?'Observed':'Unavailable',time:new Date().toISOString(),code:response.error?.code||null});
    this.value.activity=this.value.activity.slice(0,100);this.publish();
  }
  async observeAnalysis(operation) {
    if(this.value.busy||!this.mapRequest||!['preview','read'].includes(operation))return;
    if(operation==='preview'&&!this.value.map?.review||operation==='read'&&!this.analysisRequest)return;
    const generation=this.generation,root=this.value.project;
    const options=operation==='preview'?{...this.mapRequest,snapshot:this.value.map.review.source_snapshot,revision:this.value.map.review.revision,
      controls_scope_id:this.value.map.controls?this.controlsRequest?.scope_id||null:null}:this.analysisRequest;
    this.value.map=null;this.value.mapObservedAt=null;this.value.mapError=null;this.value.reviewPreview=null;this.reviewRequest=null;
    this.value.busy=true;this.mapActive=true;
    if(operation==='preview'){this.analysisRequest=null;this.value.analysisScope=null;}
    this.publish();let response;
    try {response=await this.client.run(`map_analysis_${operation}_v1`,root,options);}
    catch {response={status:'error',error:{code:'helper_failure',source:'analysis'}};}
    if(generation!==this.generation)return;
    this.value.busy=false;this.mapActive=false;
    if(response.status==='ok'){
      if(operation==='preview'){this.value.analysisScope=response.result.analysis_scope;this.analysisRequest={...options,scope_id:response.result.analysis_scope.scope_id};}
      else {this.value.map=response.result.map;this.value.mapObservedAt=response.observed_at;}
    } else {this.value.mapError=response.error;this.value.analysisScope=null;this.analysisRequest=null;}
    this.value.activity.unshift({operation:'analysis_'+operation,outcome:response.status==='ok'?'Observed':'Unavailable',time:new Date().toISOString(),code:response.error?.code||null});
    this.value.activity=this.value.activity.slice(0,100);this.publish();
  }
}
module.exports = { TABS, allowedSender, safePreferences, visibleBounds, PanelState };
