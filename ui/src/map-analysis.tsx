import React from 'react';
const human=(s:string)=>s.replaceAll('_',' ');
export function AnalysisScope({data,invoke}:{data:any;invoke:(f:()=>Promise<any>)=>void}) {
 const s=data.analysisScope;
 return <section className="analysis-scope" aria-label="Static analysis scope">
  {data.map&&!s&&<button className="secondary" disabled={data.busy} onClick={()=>invoke(window.panel.analysisPreview)}>Preview quality & dependencies</button>}
  {s&&<details open={!data.map?.analysis}><summary>Quality & dependency read scope</summary>
   <p>Measure source size, supported function syntax, normalized repetition and local static imports. Source code is parsed, never executed. No build, tests, plugins or package installation run.</p>
   <p>Python AST: {s.python_parser} · JavaScript/TypeScript parser: {human(s.parser.state)} · {s.parser.parser}</p>
   <p>Optional coding declaration: <code>{s.config}</code>. {s.configuration.expectedFiles.length} expected files · {s.configuration.forbiddenDependencies.length} explicit dependency rules.</p>
   <p>Python module roots: {s.configuration.pythonRoots.map((r:string)=>r||'(project root)').join(', ')}</p>
   <p>Declared report reads (beyond the source inventory): {s.configuration.reports.length?s.configuration.reports.map((r:any)=>`${r.kind}: ${r.path}`).join(', '):'None'}. Report results do not establish current acceptance.</p>
   <p>Up to 128 files, 1 MiB/file, 8 MiB retained input, 20,000 syntax nodes/file, 256 findings and 1,024 references. The helper stops after 15 seconds.</p>
   <p>{s.includes_controls?'Previously previewed canonical controls will also be refreshed.':'Canonical controls are not included in this read.'} {s.coverage}</p>
   <button className="primary" disabled={data.busy} onClick={()=>invoke(window.panel.analysisRead)}>Analyze quality & dependencies</button>
  </details>}
 </section>;
}
export function FindingCards({findings,nodes,sources,select}:{findings:any[];nodes:any[];sources:any[];select:(id:string)=>void}) {
 const title=(id:string)=>nodes.find(n=>n.id===id)?.title||id;
 return <div className="analysis-findings">{findings.map(f=><article key={f.id} className="analysis-finding">
  <b>{human(f.rule)} <span>{f.classification} · {f.severity} · {f.lifecycle}</span></b><p>{f.summary}</p>
  {f.value!==null&&<p>Measured: {f.value} · Threshold: {f.threshold}</p>}
  <div className="analysis-links">{f.subjects.map((id:string)=><button key={id} onClick={()=>select(id)}>{title(id)}</button>)}</div>
  <details><summary>Locations, provenance & limits</summary><p>{f.analyzer} · {f.limits}</p>{f.locations.map((l:any,i:number)=><p key={i}><code>{l.path}:{l.line}–{l.end_line}</code><br/><code>{l.identity}</code></p>)}{sources.filter(s=>f.sources.includes(s.id)&&!f.locations.some((l:any)=>l.source===s.id)).map(s=><p key={s.id}>{s.owner}: <code>{s.locator.path}</code><br/><code>{s.identity||'Identity unavailable'}</code></p>)}</details>
 </article>)}</div>;
}
export function AnalysisSummary({analysis,nodes,sources,select}:{analysis:any;nodes:any[];sources:any[];select:(id:string)=>void}) {
 if(!analysis)return null;
 const title=(id:string)=>nodes.find(n=>n.id===id)?.title||id;
 return <section className="analysis-summary" aria-label="Quality and dependency analysis">
  <div className="map-section-head"><h3>Quality & dependencies</h3><span>Static · partial coverage</span></div>
  <div className="analysis-stats"><span><b>{analysis.files.filter((f:any)=>f.state==='parsed').length}</b> Parsed files</span><span><b>{analysis.findings.length}</b> Findings to inspect</span><span><b>{analysis.dependencies.filter((d:any)=>d.target).length}</b> Local candidate links</span><span><b>{analysis.comparison.filter((c:any)=>c.state==='not_observed').length}</b> Expected files not observed</span></div>
  {analysis.notices.map((s:string)=><p className="map-notice" key={s}>{s}</p>)}
  <details className="analysis-section"><summary>Findings ({analysis.findings.length})</summary>{analysis.findings.length?<FindingCards findings={analysis.findings} nodes={nodes} sources={sources} select={select}/>:<p>No findings in the supported bounded analysis. This is not a healthy-project verdict.</p>}</details>
  <details className="analysis-section"><summary>Dependency relationships ({analysis.dependencies.length})</summary><p>Arrows are static local candidates, not runtime execution or import-success evidence. Package aliases and dynamic imports remain unresolved.</p>
   {analysis.dependencies.map((d:any,i:number)=><div className="analysis-dependency" key={i}><button onClick={()=>select(d.source)}>{title(d.source)}:{d.line}</button><span>→</span>{d.target?<button onClick={()=>select(d.target)}>{title(d.target)}</button>:<span>{human(d.resolution)}</span>}<small>{human(d.reason)}</small></div>)}
  </details>
  <details className="analysis-section"><summary>Intended / observed ({analysis.comparison.length})</summary><p>Only explicit expected-file declarations are compared. Presence does not establish acceptance.</p>{analysis.comparison.map((c:any)=><p key={c.path}><button className="analysis-link" onClick={()=>select(c.node)}>{c.path}</button> · {human(c.state)}</p>)}</details>
  <details className="analysis-section"><summary>Existing report links ({analysis.reports.length})</summary>{analysis.reports.map((r:any)=><article className="analysis-report" key={r.path}><b>{r.kind} · {human(r.state)}</b><p><code>{r.path}</code></p><p>Currentness: {r.currentness} · Reported counts only</p><p>{Object.entries(r.counts).map(([k,v])=>`${k}: ${v}`).join(' · ')}</p><code>{r.identity||'No report identity'}</code></article>)}</details>
  <details className="analysis-section"><summary>Analyzer coverage & measurements ({analysis.files.length})</summary>{analysis.files.map((f:any)=><p key={f.path}><button className="analysis-link" onClick={()=>select(f.node)}>{f.path}</button> · {human(f.state)} · {f.lines} lines · {f.functions} functions</p>)}<p>Analyzers: {analysis.analyzers.join(', ')}</p><p>Engines: {analysis.engines.python} · {analysis.engines.javascript.parser} ({analysis.engines.javascript.state})</p><code>{analysis.engines.javascript.identity||'No JavaScript parser identity'}</code></details>
 </section>;
}
