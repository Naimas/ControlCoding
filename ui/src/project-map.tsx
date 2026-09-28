import React, {useEffect, useMemo, useState} from 'react';
import {MappingEditor,ReviewStatus} from './map-review';
import {ControlsScope,ControlsSummary,ScopedConstraints,ConstraintMarker} from './map-controls';
import {AnalysisScope,AnalysisSummary,FindingCards} from './map-analysis';

type Node = {id:string;kind:string;title:string;presence:string;mapping:string;plan:string;reason:string;sources:string[];criteria:string[]};
type Props = {data:{map:any;mapScope:any;mapError:any;mapObservedAt:string|null;designPaths:string[];busy:boolean;committing:boolean;controlsScope:any;analysisScope:any;mapRefresh:any;mapHistory:any[];mapOrder:string[];preferences:{compact:boolean}};
  invoke:(fn:()=>Promise<any>)=>void;clock:number};
const human = (value:string) => value.replaceAll('_',' ');
const errors:Record<string,string> = {
  root_changed:'The selected folder was removed or replaced. Reopen the project and preview its scope. Automatic refresh and history were cleared.',
  scope_limit:'This scope exceeds the bounded map budget. Choose a smaller project folder.',
  time_limit:'Observation exceeded its time budget. Choose a smaller scope and retry.',
  changed_input:'Files changed while being observed. Refresh to get a new snapshot.',
  excluded_design:'A selected document is outside the supported design scope. Clear the selection and choose ordinary project Markdown or text files.',
  excluded_root:'This folder is excluded from project observation.',
  source_unavailable:'A selected input is unavailable or open for writing.',
  unsupported_path:'A link or unsupported path prevents a safe observation.',
  parse_limit:'A source exceeds the static parsing budget. Choose a smaller scope.',
  cancelled:'Observation cancelled. No previous map is presented as current.',
  recovery_required:'A mapping save was interrupted. Preserve definition.json and transaction.json for manual inspection; automatic overwrite or recovery is disabled.',
  invalid_definition:'The saved mapping definition is invalid. Its original bytes have been preserved.',
  definition_limit:'The saved mapping definition exceeds its size budget. Its original bytes have been preserved.',
  root_conflict:'The saved mapping definition belongs to a different project identity.',
  inaccessible:'A map input or mapping transaction is busy or inaccessible. Retry after the other operation finishes.',
  preview_mismatch:'The previewed scope or declared inputs changed. Refresh the source map and preview the controls again.',
  stale_snapshot:'Sources changed since the map observation. Refresh the source map before observing controls.',
  definition_conflict:'Reviewed mappings changed. Refresh the source map before observing controls.',
  policy_owner_unavailable:'The installed Core policy template is unavailable. No policy projection is presented.',
  invalid_analysis_configuration:'The coding architecture declaration is invalid or outside the supported scope. Its original bytes are preserved.',
  parser_unavailable:'The bounded parser could not finish. No old analysis is presented as current.',
  analysis_unavailable:'Static analysis could not complete safely. Review its scope and retry.',
};
export function ProjectMap({data,invoke,clock}:Props) {
  const [view,setView] = useState('roadmap'), [selected,setSelected] = useState<string|null>(null);
  const [hover,setHover] = useState<string|null>(null), [granularity,setGranularity] = useState('file');
  const [filter,setFilter] = useState(''), [closed,setClosed] = useState<Set<string>>(new Set());
  const map=data.map, projection=map?.projection, bundle=projection?.bundle;
  const rank=useMemo(()=>new Map((data.mapOrder||[]).map((id,i)=>[id,i])),[data.mapOrder]);
  const nodes:Node[]=useMemo(()=>[...(bundle?.nodes||[])].sort((a,b)=>(rank.get(a.id)??2000)-(rank.get(b.id)??2000)),[bundle,rank]);
  const edges:any[]=bundle?.edges||[], statuses:any[]=projection?.statuses||[];
  const byId=useMemo(()=>new Map<string,Node>(nodes.map(n=>[n.id,n])),[nodes]);
  const statusById=useMemo(()=>new Map<string,any>(statuses.map(s=>[s.id,s])),[statuses]);
  const parents=useMemo(()=>new Map<string,string>(edges.filter(e=>e.relation.startsWith('contains_')).map(e=>[e.target,e.source])),[edges]);
  const children=useMemo(()=>{const result=new Map<string,Node[]>(); for(const node of nodes){const parent=parents.get(node.id);if(parent)result.set(parent,[...(result.get(parent)||[]),node]);}return result;},[nodes,parents]);
  useEffect(()=>{setHover(null);if(map && selected && !byId.has(selected))setSelected(null);},[map]);
  const matches=(n:Node)=>!filter || n.title.toLowerCase().includes(filter.toLowerCase());
  const status=(n:Node)=>statusById.get(n.id);
  const constraints=(n:Node):any[]=>(bundle?.constraints||[]).filter((c:any)=>c.subject===n.id);
  const findings=(n:Node):any[]=>(map?.analysis?.findings||[]).filter((f:any)=>f.subjects.includes(n.id));
  const deliveryLabel=(n:Node)=>status(n)?.impediments?.includes('feature_reported_blocked')?'Reported blocked':human(status(n)?.delivery||'unknown');
  const tone=(n:Node)=>{
    const s=status(n), assessments=(bundle?.assessments||[]).filter((a:any)=>a.subject===n.id&&a.applicability==='applicable');
    if(assessments.some((a:any)=>a.outcome==='failed'&&a.freshness==='current'))return 'failed';
    if(assessments.some((a:any)=>a.freshness==='stale'))return 'stale';
    if(s?.verified_accepted)return 'accepted';
    if(s?.impediments?.includes('feature_reported_blocked'))return 'blocked';
    if(s?.delivery==='review')return 'review';
    if(s?.delivery==='active')return 'active';
    return 'unknown';
  };
  const label=(n:Node)=>`${n.title} · ${n.kind} · ${status(n)?.verified_accepted?'Verified acceptance':deliveryLabel(n)} · ${human(n.mapping)} mapping`;
  const choose=(id:string)=>{setSelected(id);setHover(null);};
  const ancestorPath=(id:string)=>{const result:Node[]=[];let current=parents.get(id);while(current&&result.length<nodes.length){const node=byId.get(current);if(!node)break;result.unshift(node);current=parents.get(current);}return result;};
  const treeRows:{node:Node;depth:number}[]=[];
  const queue=nodes.filter(n=>!parents.has(n.id)).map(node=>({node,depth:0})).reverse();
  while(queue.length){const row=queue.pop()!;if(matches(row.node))treeRows.push(row);
    if(filter||!closed.has(row.node.id))for(const child of [...(children.get(row.node.id)||[])].reverse())queue.push({node:child,depth:row.depth+1});}
  const units=nodes.filter(n=>n.kind===granularity&&matches(n));
  const groups=new Map<string,Node[]>();
  for(const n of units){let parent=parents.get(n.id)||'',steps=0;
    while(parent&&byId.get(parent)?.kind!=='module'&&byId.get(parent)?.kind!=='system'&&steps++<nodes.length)parent=parents.get(parent)||'';
    groups.set(parent,[...(groups.get(parent)||[]),n]);}
  const detail=selected?byId.get(selected):null, popup=hover?byId.get(hover):null;
  const age=data.mapObservedAt?Math.max(0,Math.floor((clock-Date.parse(data.mapObservedAt))/1000)):null;
  const switchView=(next:string)=>{setView(next);setHover(null);};
  const tabKeys=(event:React.KeyboardEvent<HTMLDivElement>)=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const buttons=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const current=buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(current+(event.key==='ArrowRight'?1:buttons.length-1))%buttons.length;
    event.preventDefault();buttons[next]?.click();buttons[next]?.focus();
  };
  return <section className={`project-map ${data.preferences.compact&&map?'map-compact':''}`} aria-label="Project Map" onKeyDown={e=>{if(e.key==='Escape'){setHover(null);setSelected(null);}}}>
    <div className="map-intro"><div><p className="eyebrow">ONE PROJECT · THREE PERSPECTIVES</p><h2>See how your code fits together.</h2><p>Trace structure, inspect delivery evidence and explore each code unit.</p></div><span className="map-scope-badge">{map?'PARTIAL OBSERVATION':'LOCAL SOURCES'}</span></div>
    <div className="map-actions"><button className="secondary" disabled={data.busy} onClick={()=>invoke(window.panel.chooseDesign)}>Select design documents</button>
      {data.designPaths.length>0&&<button className="quiet" disabled={data.busy} onClick={()=>invoke(window.panel.clearDesign)}>Clear {data.designPaths.length} documents</button>}
      <button className="secondary" disabled={data.busy} onClick={()=>invoke(window.panel.mapPreview)}>Preview read scope</button>
      {data.mapScope&&<button className="primary" disabled={data.busy} onClick={()=>invoke(map?window.panel.mapRefresh:window.panel.mapRead)}>{map?'Refresh map':'Observe project'}</button>}
      {data.busy&&!data.committing&&<button className="secondary" onClick={()=>invoke(window.panel.mapCancel)}>Cancel map observation</button>}</div>
    <div className="map-refresh-bar" aria-live="polite"><div><b>{data.mapRefresh?.enabled?'Automatic refresh on':'Automatic refresh off'}</b><p>{human(data.mapRefresh?.status||'manual')} · Source and mapping observations remain partial.</p></div>
      <button className="secondary" disabled={!data.mapRefresh?.enabled&&(data.busy||!map)} onClick={()=>invoke(window.panel.mapAuto)}>{data.mapRefresh?.enabled?'Pause automatic refresh':'Enable automatic refresh'}</button>
      <p>While this panel runs: file and Git metadata hints, plus reconciliation every {data.mapRefresh?.intervalSeconds||30}s. Repeats previously read scopes; changed scope requires a new preview. A commit is not verification.</p>
      {data.mapRefresh?.scopeChanged&&<p role="status">The {data.mapRefresh.scopeChanged} scope changed. Preview and read it again to resume that overlay.</p>}
    </div>
    {data.mapHistory?.length>0&&<details className="map-history"><summary>Observation history · {data.mapHistory.length} / 20 · This session</summary>
      <p>Historical summaries only. Changes compare observed elements, including source identities and reviewed mapping revisions; they are not completion percentages or a Git diff.</p>
      {data.mapHistory.map(h=><details key={h.id}><summary>{new Date(h.at).toLocaleTimeString()} · +{h.added} / ~{h.changed} / −{h.removed} elements · {human(h.reason)}</summary>
        <p>{h.nodes} elements · Controls {h.controls?'observed':'not observed'} · Analysis {h.analysis?'observed':'not observed'}</p>
        <dl><dt>Source observation</dt><dd>{h.snapshot}</dd><dt>Mapping revision</dt><dd>{h.revision}</dd><dt>Compared with observation</dt><dd>{h.previousSnapshot||'None'}</dd><dt>Previous mapping revision</dt><dd>{h.previousRevision||'None'}</dd></dl>
        <div className="history-links">{h.changedIds.filter((id:string)=>byId.has(id)).map((id:string)=><button className="quiet" key={id} onClick={()=>choose(id)}>{byId.get(id)?.title}</button>)}</div>
        <p>{h.truncated?'Navigation limited to 100 changed elements. ':''}Removed elements have no current navigation target.</p>
      </details>)}
    </details>}
    <ReviewStatus data={data} invoke={invoke}/>
    <ControlsScope data={data} invoke={invoke}/>
    <AnalysisScope data={data} invoke={invoke}/>
    {data.mapError&&<div role="alert" className="error"><b>Project Map unavailable</b><p>{errors[data.mapError.code]||'The local map observation could not complete. Preview the scope and try again.'}</p><code>{data.mapError.code}</code></div>}
    {data.busy&&<p role="status" className="map-notice">Observing selected inputs. Previous map cleared while the request is in progress.</p>}
    {data.mapScope&&<details className="map-scope" open={!map}><summary>Read scope · {data.designPaths.length} selected design documents</summary>
      <p>Relative file inventory, code identities, Python symbols and imports, package manifests. Hidden, dependency, build and private stores are excluded.</p>
      <p>Observation runs no project code and changes no files. Reviewed mapping choices have a separate save preview. Source bodies and document excerpts stay out of this view.</p>
      <ul>{data.designPaths.map(p=><li key={p}><code>{p}</code></li>)}</ul>
      <p>{data.mapScope.limits.max_entries.toLocaleString()} entries · {data.mapScope.limits.file_bytes/1024} KiB per file · {data.mapScope.limits.seconds}s cooperative budget</p></details>}
    {!map&&!data.busy&&!data.controlsScope&&!data.analysisScope&&<div className="empty-inline"><h3>{data.mapError?'No current map':'Start with a bounded observation'}</h3><p>Preview the read scope, then observe your selected project. Design documents are optional. Completion and controls require their own evidence.</p></div>}
    {map&&<><div className="map-stats"><div><b>{nodes.length}</b><span>Map elements</span></div><div><b>{nodes.filter(n=>n.kind==='file'&&['observed','both'].includes(n.presence)).length}</b><span>Observed files</span></div><div><b>{projection.summary.verified_units}</b><span>Verified files</span></div><div><b>{map.findings.length}</b><span>Scope notices</span></div></div>
      {map.findings.some((f:any)=>['parse_limit','symbol_detail_omitted'].includes(f.code))&&<div className="map-notice" role="status"><strong>Limited static detail</strong><p>The file map is available. Some parsing or symbol details exceeded the analysis budget and were omitted; this does not mark files as complete or failed.</p>{map.findings.some((f:any)=>f.code==='symbol_detail_omitted')&&<p>Symbol expansion was omitted for this map. Use the file squares, or open a smaller project folder for symbol detail.</p>}{map.findings.filter((f:any)=>f.code==='parse_limit').map((f:any)=><p key={f.source}><code>{f.source}</code> · Parsing limit; file identity retained.</p>)}</div>}
      <p className="map-notice"><strong>{age!==null&&age>=60?'Refresh recommended':'Partial coverage'}</strong> · {age}s since observation. Unreviewed structure is proposed. {map.controls?'Canonical signals are scoped below; host enforcement and per-file acceptance remain unverified.':'Plans, lifecycle, ownership and ControlCoding controls are not yet assessed.'}</p>
      {!data.preferences.compact&&<ControlsSummary controls={map.controls}/>}
      {!data.preferences.compact&&<AnalysisSummary analysis={map.analysis} nodes={nodes} sources={bundle.sources} select={choose}/>}
      {data.preferences.compact?<><button className="primary" onClick={()=>invoke(()=>window.panel.control('compact'))}>Expand Project Map</button><p className="muted">Expand to explore the linked views and unit details.</p></>:<>
      <div className="map-view-tabs" role="tablist" aria-label="Map perspectives" onKeyDown={tabKeys}>{[['roadmap','Roadmap & ownership'],['architecture','Architecture & delivery'],['code','Code completion']].map(([id,title])=><button role="tab" id={`map-tab-${id}`} aria-controls="map-content" aria-selected={view===id} tabIndex={view===id?0:-1} key={id} className={view===id?'chosen':''} onClick={()=>switchView(id)}>{title}</button>)}</div>
      <div className="map-filter"><label>Find an element <input type="search" value={filter} onChange={e=>setFilter(e.target.value)} placeholder="File, module or symbol"/></label>
        {view==='code'&&<label>Square represents <select value={granularity} onChange={e=>{setGranularity(e.target.value);setHover(null);}}><option value="file">One file</option><option value="symbol">One Python symbol</option></select></label>}</div>
      <div className="map-layout"><div className="map-stage" id="map-content" role="tabpanel" aria-labelledby={`map-tab-${view}`}>
        {view==='roadmap'&&<><div className="map-section-head"><h3>Structure & ownership</h3><span>Proposed hierarchy</span></div><ul className="map-tree" aria-label="Project structure">{treeRows.map(({node,depth})=><li key={node.id} className={`depth-${Math.min(depth,5)}`}>
          {children.has(node.id)?<button className="tree-toggle" aria-label={`${closed.has(node.id)?'Expand':'Collapse'} ${node.title}`} aria-expanded={!closed.has(node.id)} onClick={()=>setClosed(previous=>{const next=new Set(previous);next.has(node.id)?next.delete(node.id):next.add(node.id);return next;})}>{closed.has(node.id)?'+':'−'}</button>:<span className="tree-leaf">·</span>}
          <button className={`tree-node ${selected===node.id?'is-selected':''}`} aria-pressed={selected===node.id} onClick={()=>choose(node.id)}><span className={`map-dot ${tone(node)}`}/><span>{node.title}<small>{node.kind} · {human(node.mapping)}</small></span><span className="tree-state">{deliveryLabel(node)}</span></button></li>)}</ul>
          <div className="map-unavailable"><h3>Work roadmap not observed</h3><p>No authoritative phases, owners or dates are supplied by this source inventory. Software containment above does not imply a delivery schedule.</p></div></>}
        {view==='architecture'&&<><div className="map-section-head"><h3>Architecture & delivery</h3><span>Independent dimensions</span></div><div className="matrix-scroll"><table className="map-matrix"><caption>Structure against source, plan, delivery, evidence and controls</caption><thead><tr>{['Element','Source','Plan','Delivery','Evidence','Controls',...(map.analysis?['Quality']:[])].map(c=><th key={c} scope="col">{c}</th>)}</tr></thead><tbody>{nodes.filter(n=>n.kind!=='symbol'&&matches(n)).map(n=><tr key={n.id} className={selected===n.id?'is-selected':''}><th scope="row"><button onClick={()=>choose(n.id)} aria-pressed={selected===n.id}>{n.title}<small>{n.kind}</small></button></th><td><span className="dimension observed">{human(n.presence)}</span></td><td>{human(n.plan)}</td><td>{deliveryLabel(n)}</td><td><span className={`dimension ${tone(n)}`}>{status(n)?.verified_accepted?'Verified':'Unverified'}</span></td><td>{status(n)?.constraints.length?'Scoped records':'Not assessed'}</td>{map.analysis&&<td>{findings(n).length} findings</td>}</tr>)}</tbody></table></div></>}
        {view==='code'&&<><div className="map-section-head"><h3>Code completion</h3><span>{units.length} {granularity==='file'?'files':'symbols'} · one square per unit</span></div>
          <div className="map-legend">{[['accepted','Verified'],['review','Review'],['active','Active'],['stale','Stale'],['failed','Failed'],['unknown','Unverified']].map(([color,title])=><span key={color}><i className={`map-dot ${color}`}/>{title}</span>)}</div>
          {[...groups].map(([parent,items])=><section className="square-group" key={parent}><h4><button onClick={()=>parent&&choose(parent)}>{byId.get(parent)?.title||'Project'}</button><span>{items.length} units</span></h4><div className="square-grid">{items.map(n=><button key={n.id} className={`code-square ${tone(n)} ${selected===n.id?'is-selected':''}`} aria-label={label(n)} aria-pressed={selected===n.id} aria-describedby={hover===n.id?'map-tooltip':undefined} onMouseEnter={()=>setHover(n.id)} onMouseLeave={()=>setHover(null)} onFocus={()=>setHover(n.id)} onBlur={()=>setHover(null)} onClick={()=>choose(n.id)}>{constraints(n).length?<ConstraintMarker constraints={constraints(n)}/>:tone(n)==='unknown'?<span>?</span>:<span className="sr-only">{tone(n)}</span>}{findings(n).length>0&&<span className="quality-marker" aria-label="Static quality findings">!</span>}</button>)}</div></section>)}
          {map.controls&&<p className="map-help">L: predicted restriction · A: scoped approval request · P: perimeter configuration. Overlays never change completion color or grant permission.</p>}
          {!units.length&&<p className="map-unavailable">No {granularity==='symbol'?'supported Python symbols':'files'} in this selection. Unsupported syntax remains unverified.</p>}
          <p className="map-help">Hover or focus for an explanation. Click, Enter or tap to pin details. Escape dismisses details. Equal squares do not mean equal effort.</p></>}
      </div><aside className="map-inspector" aria-label="Element details" aria-live="polite">
        {detail?<><div className="map-section-head"><span>SELECTED ELEMENT</span><button className="quiet" aria-label="Close element details" onClick={()=>setSelected(null)}>×</button></div><h3>{detail.title}</h3><p className="muted">{detail.kind} · {human(detail.mapping)} mapping</p>
          <nav className="map-breadcrumb" aria-label="Element ownership">{ancestorPath(detail.id).map(n=><button key={n.id} onClick={()=>choose(n.id)}>{n.title} /</button>)}</nav>
          <dl><dt>Delivery</dt><dd>{deliveryLabel(detail)}</dd><dt>Reported lifecycle</dt><dd>{status(detail)?.reported_lifecycle?.state||'unknown'}</dd><dt>Plan</dt><dd>{human(detail.plan)}</dd><dt>Presence</dt><dd>{human(detail.presence)}</dd><dt>Why this status</dt><dd>{detail.reason}</dd></dl>
          <ul className="map-reasons">{status(detail)?.reasons.map((r:string)=><li key={r}>{human(r)}</li>)}</ul>
          {map.review&&!detail.id.startsWith('analysis-')&&!['feature','criterion','phase','task','milestone'].includes(detail.kind)&&<MappingEditor key={`${detail.id}:${map.review.revision}`} node={detail} map={map} busy={data.busy} invoke={invoke}/>}
          {map.analysis&&<><h4>Quality findings ({findings(detail).length})</h4><FindingCards findings={findings(detail)} nodes={nodes} sources={bundle.sources} select={choose}/>{!findings(detail).length&&<p>No attached findings; supported coverage remains partial.</p>}</>}
          <ScopedConstraints constraints={constraints(detail)} sources={bundle.sources}/>
          <h4>Related elements</h4>{edges.filter(e=>!e.relation.startsWith('contains_')&&(e.source===detail.id||e.target===detail.id)).map(e=><button className="map-related" key={e.id} onClick={()=>choose(e.source===detail.id?e.target:e.source)}>{human(e.relation)}: {byId.get(e.source===detail.id?e.target:e.source)?.title}</button>)}<p className="muted">Import declarations do not establish resolved dependencies.</p>
          <h4>Source references</h4>{detail.sources.length?bundle.sources.filter((s:any)=>detail.sources.includes(s.id)).map((s:any)=><details key={s.id}><summary>{s.locator.path}</summary><p>{s.reason}</p><code>{s.identity||'Content identity unknown'}</code><p>{s.adapter}</p></details>):<p>Directory inventory; no file-content identity.</p>}
          {map.observations.filter((o:any)=>o.node===detail.id).map((o:any)=><details key={o.source}><summary>Static observation · {o.adapter}</summary><pre>{JSON.stringify(o.details,null,2)}</pre></details>)}</>:<div className="inspector-empty"><span className="inspector-cross">+</span><h3>Choose an element</h3><p>Its ownership, evidence, source references and scoped controls appear here.</p><p>Selection follows you across all three views.</p></div>}
      </aside></div>
      {popup&&<div id="map-tooltip" role="tooltip" className="map-tooltip"><strong>{popup.title}</strong>{findings(popup).length>0&&<p>{findings(popup).map(f=>human(f.rule)).join(' · ')} · Review findings, independent of completion</p>}<span>{label(popup)}</span><p>{popup.reason}</p><small>{status(popup)?.reasons.map(human).join(' · ')}</small>{constraints(popup).slice(0,3).map(c=><p key={c.id}>{human(c.stage)} {human(c.decision)} · {human(c.rule)} · coverage {human(c.coverage)}. Pin details for the full rule and source.</p>)}</div>}
      </>}
      <details className="map-findings"><summary>Coverage & notices ({map.findings.length})</summary><p>{bundle.coverage.reason}</p><ul>{bundle.coverage.omissions.map((r:string)=><li key={r}>{human(r)}</li>)}</ul><ul>{map.findings.map((f:any,i:number)=><li key={i}><code>{f.source}</code> · {human(f.code)}</li>)}</ul></details>
    </>}
  </section>;
}
