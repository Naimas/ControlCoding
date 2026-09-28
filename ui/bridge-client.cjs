const documentReader=require('./document-reader.cjs');
'use strict';
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const path = require('node:path');
const configuration = require('./config-contract.cjs');
const management = require('./work-manage.cjs');
const documentation = require('./documentation-state.cjs');
const MAX_REPLY = 1024 * 1024 + 1;
const WORK_OPERATIONS = ['work_preview_v1','work_read_v1'];
function validWorkResult(operation, result, options) {
  if(operation==='work_preview_v1') {
    const s=result.work_scope;
    return s && hash(s.scope_id) && stringList(s.paths) && s.paths.length<=16 &&
      s.max_records===96 && s.file_bytes===65536 && typeof s.notice==='string';
  }
  const w=result.work;
  const list=(v,max,check)=>Array.isArray(v)&&v.length<=max&&v.every(check);
  const relation=r=>strings(r,['id','sourceId','targetId','type','status','reason']);
  return w && w.schema_version===1 && w.scope_id===options.scope_id && hash(w.snapshot_id) &&
    ['absent','context_only','present'].includes(w.state) && w.query===options.query.trim() && stringList(w.notices) &&
    w.counts && ['records','bytes','directories'].every(k=>Number.isInteger(w.counts[k])&&w.counts[k]>=0) &&
    list(w.documents,96,d=>strings(d,['id','title','path','area','lifecycle','source','category','captured','excerpt'])&&hash(d.sha256)&&typeof d.excerpt_truncated==='boolean') &&
    list(w.sessions,96,s=>strings(s,['id','topic','summary','status','startedAt','updatedAt','endedAt','path','node_id'])&&
      ['categories','memoryChanged','packets','decisions','followups'].every(k=>stringList(s[k]))&&
      list(s.notes,128,n=>strings(n,['createdAt','kind','text']))&&list(s.links,128,l=>strings(l,['type','target','targetType']))) &&
    w.graph && list(w.graph.nodes,608,n=>strings(n,['id','title','path','type','lifecycle','recordType'])) &&
    list(w.graph.edges,6000,relation) && list(w.graph.suggestions,6000,s=>typeof s.id==='string'&&typeof s.status==='string'&&
      (s.auditOnly===true||relation(s))) &&
    (w.packet===null || (strings(w.packet,['query','packetMarkdown'])&&w.packet.query===w.query&&
      list(w.packet.citations,10,c=>strings(c,['id','title','path','citation','citationId','lifecycle'])&&stringList(c.reasons))&&
      list(w.packet.warnings,100,c=>strings(c,['citationId','message']))));
}
const MAP_OPERATIONS = ['map_preview_v1', 'map_read_v1', 'map_review_preview_v1', 'map_review_apply_v1', 'map_controls_preview_v1', 'map_controls_read_v1', 'map_analysis_preview_v1', 'map_analysis_read_v1'];
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const revision = value => value === 'absent' || hash(value);
const strings = (row, fields) => row && fields.every(key => typeof row[key] === 'string');
const stringList = value => Array.isArray(value) && value.every(s => typeof s === 'string');
function validMapResult(operation, result, options) {
  if(operation==='map_analysis_preview_v1') {
    const s=result.analysis_scope,c=s?.configuration;
    return s?.schema_version===1&&s.adapter==='cc-project-map-analysis/v1'&&hash(s.scope_id)&&s.snapshot===options.snapshot&&s.revision===options.revision&&
      ['controlcoding.architecture.json','cc/controlcoding.architecture.json'].includes(s.config)&&(s.config_identity===null||hash(s.config_identity))&&
      s.includes_controls===(options.controls_scope_id!==null)&&['available','unavailable'].includes(s.parser?.state)&&
      typeof s.python_parser==='string'&&typeof s.parser.parser==='string'&&(s.parser.identity===null||hash(s.parser.identity))&&
      stringList(c?.pythonRoots)&&stringList(c?.expectedFiles)&&Array.isArray(c?.forbiddenDependencies)&&
      c.forbiddenDependencies.every(r=>strings(r,['id','from','to']))&&Array.isArray(c?.reports)&&c.reports.every(r=>strings(r,['kind','path']))&&
      s.limits?.files===128&&s.limits.helper_seconds===15&&typeof s.coverage==='string';
  }
  if(operation==='map_controls_preview_v1') {
    const s=result.controls_scope;
    return s?.schema_version===1&&s.adapter==='cc-project-map-controls/v1'&&hash(s.scope_id)&&s.snapshot===options.snapshot&&s.revision===options.revision&&
      ['files','extra_paths','environment_names','receipt_folders'].every(k=>stringList(s[k]))&&Array.isArray(s.contracts)&&
      typeof s.source_identity==='string'&&s.limits?.helper_seconds===15&&s.evidence_limits?.input_total_bytes===268435456;
  }
  if(operation === 'map_review_preview_v1') {
    const p=result.review_preview;
    return p && hash(p.approval_id) && p.snapshot===options.snapshot && p.revision===options.revision &&
      ['.controlcoding/project-map/definition.json','cc/.controlcoding/project-map/definition.json'].includes(p.path) && typeof p.write_supported==='boolean' &&
      Array.isArray(p.changes) && p.changes.length>0 && p.changes.length<=128 && p.changes.every(c=>validChoice(c.after)&&(c.before===null||validChoice(c.before)));
  }
  if(operation === 'map_review_apply_v1') return result.review_saved?.saved===true && hash(result.review_saved.revision) && ['.controlcoding/project-map/definition.json','cc/.controlcoding/project-map/definition.json'].includes(result.review_saved.path);
  if (operation === 'map_preview_v1') return /^[a-f0-9]{64}$/.test(result.scope?.preview_id || '') &&
    result.scope.schema_version === 1 && result.scope.adapter === 'cc-project-map-sources/v1' &&
    Number.isInteger(result.scope.limits?.max_entries) && result.scope.limits.max_entries > 0 && result.scope.limits.max_entries <= 5000 &&
    Number.isInteger(result.scope.limits?.file_bytes) && result.scope.limits.file_bytes > 0 && result.scope.limits.file_bytes <= 1048576 &&
    Number.isInteger(result.scope.limits?.seconds) && result.scope.limits.seconds > 0 && result.scope.limits.seconds <= 10 &&
    JSON.stringify(result.scope.design_paths) === JSON.stringify([...options.design_paths].sort());
  const map = result.map, p = map?.projection;
  return map?.schema_version === 1 && map.mode === 'observation' && map.canonical === false &&
    map.preview_id === options.preview_id && map.adapter === 'cc-project-map-sources/v1' &&
    Buffer.byteLength(JSON.stringify(map), 'utf8') <= 768 * 1024 &&
    p?.schema_version === 1 && p.mode === 'observation' && p.canonical === false &&
    Array.isArray(p.bundle?.nodes) && p.bundle.nodes.length <= 2000 &&
    Array.isArray(p.bundle.edges) && Array.isArray(p.bundle.sources) && Array.isArray(p.bundle.constraints) &&
    Array.isArray(p.bundle.assessments) && Array.isArray(p.statuses) && p.statuses.length === p.bundle.nodes.length &&
    p.bundle.nodes.every(n => strings(n,['id','kind','title','presence','mapping','plan','reason']) && stringList(n.sources) && stringList(n.criteria)) &&
    new Set(p.bundle.nodes.map(n=>n.id)).size === p.bundle.nodes.length &&
    p.bundle.edges.every(e => strings(e,['id','source','target','relation'])) &&
    p.bundle.sources.every(s => strings(s,['id','reason','adapter']) && typeof s.locator?.path === 'string') &&
    p.bundle.constraints.every(c => strings(c,['id','subject','kind','operation','scope','reason','condition','stage','decision','coverage','host','context','rule']) && stringList(c.sources)) &&
    p.bundle.assessments.every(a => strings(a,['id','subject','applicability','freshness','outcome'])) &&
    p.statuses.every(s => strings(s,['id','delivery']) && typeof s.verified_accepted === 'boolean' && stringList(s.reasons) && Array.isArray(s.constraints) && p.bundle.nodes.some(n=>n.id===s.id)) &&
    new Set(p.statuses.map(s=>s.id)).size === p.statuses.length &&
    Array.isArray(map.findings) && map.findings.every(f => typeof f.code === 'string' && typeof f.source === 'string') &&
    Array.isArray(map.observations) && map.observations.every(o => strings(o,['source','node','adapter']) && o.details && typeof o.details === 'object') &&
    Number.isInteger(p.summary?.verified_units) && stringList(p.bundle.coverage?.omissions) &&
    typeof p.bundle.coverage.reason === 'string' &&
    (operation!=='map_controls_read_v1'||(validControls(map.controls)&&map.review?.source_snapshot===options.snapshot&&map.review?.revision===options.revision)) &&
    (operation!=='map_analysis_read_v1'||(validAnalysis(map.analysis,p.bundle.nodes)&&map.review?.source_snapshot===options.snapshot&&map.review?.revision===options.revision&&(!options.controls_scope_id||validControls(map.controls)))) &&
    (map.review === undefined || (revision(map.review.revision) && typeof map.review.source_snapshot==='string' &&
      typeof map.review.write_supported==='boolean' && Array.isArray(map.review.entries) && map.review.entries.length<=128 && map.review.entries.every(validChoice)));
}

function validAnalysis(a,nodes) {
  const identifiers=new Set(nodes.map(n=>n.id)),natural=n=>Number.isSafeInteger(n)&&n>=0;
  return a?.schema_version===1&&a.adapter==='cc-project-map-analysis/v1'&&a.coverage==='partial'&&stringList(a.notices)&&stringList(a.analyzers)&&
    typeof a.engines?.python==='string'&&strings(a.engines.javascript,['parser','state'])&&(a.engines.javascript.identity===null||hash(a.engines.javascript.identity))&&
    Array.isArray(a.files)&&a.files.length<=128&&a.files.every(f=>strings(f,['path','node','source','identity','state'])&&identifiers.has(f.node)&&natural(f.lines)&&natural(f.bytes)&&natural(f.functions))&&
    Array.isArray(a.findings)&&a.findings.length<=256&&a.findings.every(f=>strings(f,['id','rule','analyzer','classification','severity','lifecycle','summary','limits'])&&stringList(f.subjects)&&f.subjects.every(n=>identifiers.has(n))&&stringList(f.sources)&&
      Array.isArray(f.locations)&&f.locations.every(l=>strings(l,['node','path','source','identity'])&&identifiers.has(l.node)&&natural(l.line)&&natural(l.end_line)))&&
    Array.isArray(a.dependencies)&&a.dependencies.length<=1024&&a.dependencies.every(d=>strings(d,['source','path','resolution','reason','analyzer','source_ref','identity'])&&identifiers.has(d.source)&&(d.target===null||identifiers.has(d.target))&&stringList(d.candidates)&&natural(d.line))&&
    Array.isArray(a.comparison)&&a.comparison.length<=128&&a.comparison.every(c=>strings(c,['path','node','state','source'])&&identifiers.has(c.node))&&
    Array.isArray(a.reports)&&a.reports.length<=8&&a.reports.every(r=>strings(r,['kind','path','source','state','currentness'])&&(r.identity===null||typeof r.identity==='string')&&r.counts&&Object.values(r.counts).every(natural));
}

function validControls(c) {
  return c?.schema_version===1&&c.adapter==='cc-project-map-controls/v1'&&c.host_coverage==='unknown'&&
    ['observed','absent','invalid'].includes(c.features?.state)&&Number.isInteger(c.features.count)&&c.features.count>=0&&c.features.count<=128&&stringList(c.features.issues)&&
    ['conflict','invalid','file','process','absent'].includes(c.active_module?.source)&&
    ['unknown','conflicting'].includes(c.active_module.coverage)&&
    (c.active_module.module===null||typeof c.active_module.module==='string')&&stringList(c.notices)&&
    Array.isArray(c.gates)&&c.gates.length===2&&new Set(c.gates.map(g=>g.kind)).size===2&&c.gates.every(g=>strings(g,['kind','state','outcome','scope'])&&
      ['verification','invariants'].includes(g.kind)&&g.scope==='project'&&(g.receipt===null||typeof g.receipt==='string')&&
      typeof g.current_required_pass==='boolean'&&typeof g.contract_valid==='boolean'&&Number.isInteger(g.required_count)&&g.required_count>=0&&stringList(g.reasons));
}

function validChoice(row) {
  return strings(row,['id','kind','title','decision']) && ['confirmed','rejected'].includes(row.decision) &&
    ['bindings','members','supersedes'].every(k=>stringList(row[k])) && Array.isArray(row.sources);
}
function validChange(c) {
  if(!c || typeof c!=='object' || Array.isArray(c)) return false;
  const fields={accept:['target'],reject:['target'],rename:['target','title'],alias:['target','replacement'],group:['title','members'],split:['target','groups'],merge:['targets','title']}[c.operation];
  if(!fields || Object.keys(c).sort().join()!==['operation',...fields].sort().join())return false;
  const id=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(v);
  const title=v=>typeof v==='string'&&v.trim().length>0&&v.length<=160&&!/[\x00-\x1f\x7f-\x9f]/.test(v);
  const list=v=>Array.isArray(v)&&v.length>0&&v.length<=64&&v.every(id)&&new Set(v).size===v.length;
  return fields.every(k=>['target','replacement'].includes(k)?id(c[k]):k==='title'?title(c[k]):k==='groups'?
    Array.isArray(c.groups)&&c.groups.length>=2&&c.groups.length<=8&&c.groups.every(g=>g&&Object.keys(g).sort().join()==='members,title'&&title(g.title)&&list(g.members)):list(c[k]));
}

class BridgeClient {
  constructor(python, script, options = {}) {
    if (!path.isAbsolute(python) || !path.isAbsolute(script)) throw new Error('Absolute trusted paths required');
    this.python = python; this.script = script;
    this.spawn = options.spawn || spawn;
    this.analysisRuntime=options.analysisRuntime||null;
    if(this.analysisRuntime&&(!Array.isArray(this.analysisRuntime)||this.analysisRuntime.length!==2||!this.analysisRuntime.every(p=>typeof p==='string'&&path.isAbsolute(p))))throw Error('Absolute trusted analyzer paths required');
    this.timeout = options.timeout || 15000;
    this.active = null;
    this.sequence = 0;
    this.drained = Promise.resolve(true);
  }
  cancel() { this.sequence++; if (this.active) this.active('cancelled'); }
  async run(operation, root, options = {}) {
    const isMap = MAP_OPERATIONS.includes(operation);
    const isWork = WORK_OPERATIONS.includes(operation);
    const isConfig = configuration.operations.includes(operation);
    const isManagement = management.operations.includes(operation);
    const isDocumentation=operation==='documentation_read_v1';
    const isDocument=operation==='document_read_v1';
    const isKnowledge=operation==='knowledge_v1';
    if (!['read', 'preview', 'knowledge_v1', 'documentation_read_v1', 'document_read_v1', ...MAP_OPERATIONS, ...WORK_OPERATIONS, ...configuration.operations, ...management.operations].includes(operation) || typeof root !== 'string' || !path.isAbsolute(root) || root.length > 4096 ||
        (isKnowledge&&!require('./knowledge-contract.cjs').validRequest(options)) ||
        (isDocument&&!documentReader.validRequest(options)) ||
        (isDocumentation&&(Object.keys(options).join()!=='scope'||!documentation.scopes.includes(options.scope))) ||
        (isManagement && !management.validRequest(operation,options)) ||
        (isConfig && !configuration.validRequest(operation,options)) ||
        (operation==='work_read_v1'&&(!hash(options.scope_id)||typeof options.query!=='string'||options.query.length>240||/[\x00-\x1f]/.test(options.query))) ||
        (isMap && (!Array.isArray(options.design_paths) || options.design_paths.length > 32 ||
          !options.design_paths.every(p => typeof p === 'string' && p.length <= 1024) ||
          typeof options.observed_at !== 'string' || options.observed_at.length !== 20 ||
          (operation !== 'map_preview_v1' && !hash(options.preview_id)) ||
          (operation.startsWith('map_review_') && (!revision(options.revision) || typeof options.snapshot!=='string' || !/^snapshot:[a-f0-9]{64}$/.test(options.snapshot) || !validChange(options.change))) ||
          (operation.startsWith('map_controls_') && (!revision(options.revision) || typeof options.snapshot!=='string' || !/^snapshot:[a-f0-9]{64}$/.test(options.snapshot))) ||
          (operation==='map_controls_read_v1'&&!hash(options.scope_id)) ||
          (operation.startsWith('map_analysis_')&&(!revision(options.revision)||typeof options.snapshot!=='string'||!/^snapshot:[a-f0-9]{64}$/.test(options.snapshot)||(options.controls_scope_id!==null&&!hash(options.controls_scope_id)))) ||
          (operation==='map_analysis_read_v1'&&!hash(options.scope_id)) ||
          (operation === 'map_review_apply_v1' && !hash(options.approval_id)))))
      return Promise.resolve({ status: 'error', error: { code: 'invalid_request', source: 'request' } });
    this.cancel();
    const ticket = this.sequence;
    const drained = await new Promise(resolve => {
      const guard = setTimeout(() => resolve(false), 1000);
      this.drained.then(value => { clearTimeout(guard); resolve(value); });
    });
    if (ticket !== this.sequence) return {status:'error',error:{code:'cancelled',source:'helper'}};
    if (!drained) return {status:'error',error:{code:'helper_busy',source:'helper'}};
    return new Promise(resolve => {
      const id = randomUUID();
      const env = { PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' };
      for (const key of ['SystemRoot', 'WINDIR', 'TEMP', 'TMP']) if (process.env[key]) env[key] = process.env[key];
      if(isKnowledge&&process.env.PATH)env.PATH=process.env.PATH;
      if((operation.startsWith('map_controls_')||operation.startsWith('map_analysis_'))&&process.env.CC_ACTIVE_MODULE) {
        const active=process.env.CC_ACTIVE_MODULE.trim();
        env.CC_ACTIVE_MODULE=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(active)?active:'?';
      }
      let child, timer, ended = false, chunks = [], size = 0, errors = 0;
      const finish = (code, response) => {
        if (ended) return;
        ended = true; clearTimeout(timer);
        if (this.active === cancel) this.active = null;
        if (code && child && child.exitCode === null) child.kill();
        resolve(response || { status: 'error', error: { code, source: 'helper' } });
      };
      const cancel = code => finish(code);
      this.active = cancel;
      try {
        const runtimeArgs=operation.startsWith('map_analysis_')&&this.analysisRuntime?['--analysis-node',this.analysisRuntime[0],'--analysis-worker',this.analysisRuntime[1]]:[];
        child = this.spawn(this.python, ['-I', '-B', this.script,...runtimeArgs], {
          cwd: path.dirname(this.script), env, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
      } catch { finish('helper_unavailable'); return; }
      this.drained = new Promise(resolve => child.once('close', () => resolve(true)));
      timer = setTimeout(() => finish('helper_timeout'), this.timeout);
      child.on('error', () => finish('helper_unavailable'));
      child.stdin.on('error', () => finish('helper_failure'));
      child.stderr.on('data', chunk => { errors += chunk.length; if (errors > 16384) finish('helper_output_limit'); });
      child.stdout.on('data', chunk => {
        size += chunk.length;
        if (size > MAX_REPLY) { finish('helper_output_limit'); return; }
        chunks.push(chunk);
      });
      child.on('close', code => {
        if (ended) return;
        if (code !== 0 || errors) { finish('helper_failure'); return; }
        try {
          const response = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (response.version !== 1 || response.id !== id || response.operation !== operation ||
              !['ok', 'error'].includes(response.status) ||
              (response.status === 'ok' && (response.result?.project_root !== root ||
                (isWork && !validWorkResult(operation,response.result,options)) ||
                (isConfig && !configuration.validResult(operation,response.result,options)) ||
                (isManagement && !management.validResult(operation,response.result,options)) ||
                (isDocument && !documentReader.validResult(response.result,options)) ||
                (isKnowledge && !require('./knowledge-contract.cjs').validResult(response.result.knowledge,options.action)) ||
                (isDocumentation && !documentation.validResult(response.result,options)) ||
                (isMap && !validMapResult(operation, response.result, options)))) ||
              (response.status === 'error' && typeof response.error?.code !== 'string')) throw Error();
          finish(null, response);
        } catch { finish('invalid_helper_response'); }
      });
      const request = { version: 1, id, operation, project_root: root };
      if(isConfig||isManagement)Object.assign(request,options);
      if(isDocumentation)request.scope=options.scope;
      if(isDocument)Object.assign(request,options);
      if(isKnowledge)Object.assign(request,options);
      if(operation==='work_read_v1')Object.assign(request,{scope_id:options.scope_id,query:options.query});
      if (isMap) Object.assign(request, {design_paths:options.design_paths, observed_at:options.observed_at},
        operation !== 'map_preview_v1' ? {preview_id:options.preview_id} : {});
      if(operation.startsWith('map_review_')) Object.assign(request,{snapshot:options.snapshot,revision:options.revision,change:options.change});
      if(operation==='map_review_apply_v1') request.approval_id=options.approval_id;
      if(operation.startsWith('map_controls_')) Object.assign(request,{snapshot:options.snapshot,revision:options.revision});
      if(operation==='map_controls_read_v1') request.scope_id=options.scope_id;
      if(operation.startsWith('map_analysis_'))Object.assign(request,{snapshot:options.snapshot,revision:options.revision,controls_scope_id:options.controls_scope_id});
      if(operation==='map_analysis_read_v1')request.scope_id=options.scope_id;
      const encoded = JSON.stringify(request);
      if (Buffer.byteLength(encoded, 'utf8') > 64 * 1024) { finish('invalid_request'); return; }
      child.stdin.end(encoded);
    });
  }
}
module.exports = { BridgeClient, validChange, validWorkResult };
