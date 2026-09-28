import React from 'react';
const human=(value:string)=>value.replaceAll('_',' ');
export function ControlsScope({data,invoke}:{data:any;invoke:(fn:()=>Promise<any>)=>void}) {
 const scope=data.controlsScope;
 return <section className="controls-scope" aria-label="ControlCoding observation scope">
  {data.map&&!scope&&<button className="secondary" disabled={data.busy} onClick={()=>invoke(window.panel.controlsPreview)}>Preview controls & evidence</button>}
  {scope&&<details open={!data.map?.controls}><summary>ControlCoding sources & evidence scope</summary>
   <p>Read feature declarations, module perimeters, protected-zone configuration, pending approval metadata and canonical receipt history. No checks or hooks run and no tokens are consumed.</p>
   <p>{scope.source_identity}</p>
   <ul>{scope.files.map((p:string)=><li key={p}><code>{p}</code></li>)}</ul>
   <p>Module locks: <code>.feature-lock.json</code> in bounded ordinary project directories. Evidence contracts: canonical project files, with supported control-plane fallbacks.</p>
   <p>Receipt folders: {scope.receipt_folders.join(', ')}</p>
   <p>Additional declared evidence paths: {scope.extra_paths.length?scope.extra_paths.join(', '):'None'}</p>
   <p>Declared environment names (values omitted): {scope.environment_names.length?scope.environment_names.join(', '):'None'}</p>
   <p>Control inputs: 1 MiB/file, 8 MiB total, 256 retained entries, 64 locks. Canonical evidence inputs: up to 32 MiB/file, 256 MiB and 10,000 entries; the shared helper still stops after 15 seconds.</p>
   <p className="muted">Results describe the panel process. The actual editing host, loaded hooks and active lifts remain unverified.</p>
   <button className="primary" disabled={data.busy} onClick={()=>invoke(window.panel.controlsRead)}>Observe controls & evidence</button>
  </details>}
 </section>;
}
export function ControlsSummary({controls}:{controls:any}) {
 if(!controls)return null;
 const active=controls.active_module;
 return <section className="controls-summary" aria-label="Canonical ControlCoding observations"><div className="map-section-head"><h3>ControlCoding signals</h3><span>Host delivery unverified</span></div>
  <div className="controls-cards"><article><h4>Feature lifecycle</h4><b>{controls.features.count} declarations · {human(controls.features.state)}</b><p>Reported completion is separate from source-bound acceptance. Free-text feature scope does not assign file completion.</p>{controls.features.issues.map((r:string)=><p key={r}>{human(r)}</p>)}</article>
   <article><h4>Module context</h4><b>{active.module||'Not established'}</b><p>{human(active.source)} · {active.mode||'No mode'} · coverage {human(active.coverage)}</p><p>may_read is declarative. Ownership never overrides protection.</p></article>
   {controls.gates.map((gate:any)=><article key={gate.kind} className={`gate-card ${gate.current_required_pass?'gate-current':gate.state==='stale'||gate.state==='context_changed'?'gate-stale':'gate-unknown'}`}><h4>{human(gate.kind)} gate</h4><b>{gate.current_required_pass?'Current required pass':human(gate.state)}</b><p>Latest outcome: {human(gate.outcome)} · {gate.required_count} required checks</p><p>Project scope only · {gate.contract_valid?'Contract validated':'Contract unavailable or invalid'}</p><ul>{gate.reasons.map((r:string)=><li key={r}>{human(r)}</li>)}</ul>{gate.receipt&&<code>{gate.receipt}</code>}</article>)}
  </div>{controls.notices.map((r:string)=><p className="map-notice" key={r}>{human(r)}</p>)}
 </section>;
}
export function ScopedConstraints({constraints,sources}:{constraints:any[];sources:any[]}) {
 return <><h4>ControlCoding controls</h4>{constraints.length?constraints.map(c=><article className="scoped-control" key={c.id}>
  <b>{human(c.kind)} · {human(c.decision)}</b><p>{human(c.stage)} · {c.operation} · <code>{c.scope}</code></p>
  <p>{c.reason}</p><p>Coverage: {human(c.coverage)} · Host: {c.host}</p><p>{c.context}</p>
  <p>Proceed condition: {c.condition}</p><details><summary>Rule & provenance</summary><code>{c.rule}</code>{sources.filter(s=>c.sources.includes(s.id)).map(s=><p key={s.id}>{s.owner}: <code>{s.locator.path}</code><br/>{s.reason}<br/><code>{s.identity||'Identity unknown'}</code></p>)}</details>
 </article>):<p>Not assessed. No permission or global block is inferred.</p>}</>;
}
export function ConstraintMarker({constraints}:{constraints:any[]}) {
 if(!constraints.length)return null;
 const approval=constraints.some(c=>c.kind==='approval'),restriction=constraints.some(c=>c.decision==='deny');
 const label=approval?'Scoped approval request':restriction?'Predicted restriction; host unverified':'Scoped configuration; no permission grant';
 return <span aria-label={label} title={label}>{approval?'A':restriction?'L':'P'}</span>;
}
