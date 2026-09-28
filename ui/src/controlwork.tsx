import React, {useEffect, useState} from 'react';
import {WorkManagement} from './work-management';
import {DocumentationMap} from './documentation-map';
import {KnowledgeMemory} from './knowledge-memory';
import {KnowledgeWork} from './knowledge-work';
import {KnowledgeSchedule} from './knowledge-schedule';

const pages=['Overview','Documents','Connections','Sessions','Decisions','Search & RAG'];
const errors:Record<string,string>={
  memory_limit:'The archive exceeds this observer’s bounded scope (96 records, 64 KiB per file). No partial result is presented.',
  invalid_memory:'A memory record is malformed or uses an unsupported schema. The archive was left untouched.',
  unsupported_path:'A linked or special path cannot be read by this observer.',
  changed_input:'Memory changed during observation. Preview the scope again before reading.',
  scope_mismatch:'The selected root or read scope changed. Preview again.',
  helper_timeout:'The memory read exceeded its time budget. No current result is available.'
};

export function ControlWork({data,invoke}:{data:any;invoke:(fn:()=>Promise<any>)=>void}) {
  const [page,setPage]=useState('Overview'),[selected,setSelected]=useState(''),[filter,setFilter]=useState(''),[query,setQuery]=useState('');
  const work=data.work,docs=work?.documents||[],sessions=work?.sessions||[],graph=work?.graph;
  useEffect(()=>{setSelected('');},[work?.snapshot_id]);
  const nodes=graph?.nodes||[],node=nodes.find((n:any)=>n.id===selected);
  const doc=docs.find((d:any)=>d.id===selected||d.path===node?.path),session=sessions.find((s:any)=>s.node_id===selected||s.path===node?.path);
  const pick=(id:string)=>setSelected(id);
  const relations=[...(graph?.edges||[]),...(graph?.suggestions||[])].filter((e:any)=>e.sourceId===selected||e.targetId===selected);
  const list=page==='Decisions'?docs.filter((d:any)=>d.area==='decisions'):docs;
  const visible=list.filter((d:any)=>(d.title+' '+d.path+' '+d.category).toLowerCase().includes(filter.toLowerCase()));
  function tabsKey(event:React.KeyboardEvent<HTMLDivElement>) {
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();let index=pages.indexOf(page);
    index=event.key==='Home'?0:event.key==='End'?pages.length-1:(index+(event.key==='ArrowRight'?1:-1)+pages.length)%pages.length;
    setPage(pages[index]);document.getElementById('cw-tab-'+index)?.focus();
  }
  const detail=<aside className="cw-detail" aria-label="Selected memory record">
    {node?<><span className="eyebrow">{node.type} · {node.lifecycle}</span><h3>{node.title}</h3><code>{node.path}</code>
      {doc&&<><p>Source: {doc.source||'Not recorded'} · Category: {doc.category||'Not recorded'}</p><pre className="cw-text">{doc.excerpt}</pre>{doc.excerpt_truncated&&<p className="cw-notice">Preview limited to 8,000 characters.</p>}</>}
      {session&&<><p className="cw-notice">Recorded session summary. A full conversation transcript is not available here.</p><p>{session.summary||'No summary recorded.'}</p>
        <h4>Decisions</h4>{session.decisions.length?session.decisions.map((s:string,i:number)=><p key={i}>{s}</p>):<p>No decisions recorded.</p>}
        <h4>Follow-ups</h4>{session.followups.length?session.followups.map((s:string,i:number)=><p key={i}>{s}</p>):<p>No follow-ups recorded.</p>}
        {session.notes.map((n:any,i:number)=><p key={i}><b>{n.kind}</b> · {n.text}</p>)}
        {!!session.links.length&&<details><summary>Recorded references</summary>{session.links.map((l:any,i:number)=><p key={i}>{l.type} · <code>{l.target}</code></p>)}</details>}</>}
      <h4>Connections</h4>{relations.length?relations.map((r:any,i:number)=>{
        const target=nodes.find((n:any)=>n.id===(r.sourceId===selected?r.targetId:r.sourceId));
        return <div className="cw-relation" key={r.id+i}><span className="pill">{r.status}</span> {r.type}<p>{r.reason}</p>{target&&<button className="quiet" onClick={()=>pick(target.id)}>{target.title}</button>}</div>;
      }):<p>No captured connections for this record.</p>}
    </>:<div className="cw-prompt"><span aria-hidden="true">+</span><h3>Choose a record</h3><p>Inspect its source, recorded state and connections.</p></div>}
  </aside>;
  return <section className="cw-root">
    <KnowledgeMemory data={data} invoke={invoke}/>
    <KnowledgeSchedule data={data} invoke={invoke}/>
    <DocumentationMap data={data} invoke={invoke}/>
    <KnowledgeWork data={data} invoke={invoke}/>
    <WorkManagement data={data} invoke={invoke}/>
    <div className="cw-heading"><div><p className="eyebrow">PROJECT KNOWLEDGE</p><h2>Your project’s memory, connected.</h2><p>Documents, decisions and session continuity from the embedded ControlWork archive.</p></div><span className="pill">LOCAL · READ ONLY</span></div>
    <div className="cw-actions"><button className="secondary" disabled={data.busy} onClick={()=>invoke(window.panel.workPreview)}>Preview memory scope</button>
      <button className="primary" disabled={data.busy||!data.workScope} onClick={()=>invoke(()=>window.panel.workRead(''))}>{work?'Refresh memory':'Read project memory'}</button></div>
    {data.workScope&&<details className="cw-scope" open={!work}><summary>Read scope · local text and session records</summary><p>{data.workScope.notice}</p><ul>{data.workScope.paths.map((p:string)=><li key={p}><code>{p}</code></li>)}</ul><p>Up to {data.workScope.max_records} records; 64 KiB per file. External references are displayed as text, never followed.</p></details>}
    {data.busy&&!work&&<p role="status">Preparing a fresh observation…</p>}
    {data.workError&&<div className="error" role="alert"><strong>ControlWork unavailable</strong><p>{errors[data.workError.code]||'The local helper could not read memory. Preview again to retry.'}</p><code>{data.workError.code}</code></div>}
    {!work&&!data.workError&&!data.busy&&<div className="empty-inline"><h3>Read existing knowledge</h3><p>Preview the scope, then read the selected project. No archive is created by opening this page.</p></div>}
    {work&&<>
      <div className="cw-metrics"><div><span>Documents</span><strong>{docs.length}</strong></div><div><span>Sessions</span><strong>{sessions.length}</strong></div><div><span>Recorded edges</span><strong>{graph.edges.length}</strong></div><div><span>Needs review</span><strong>{docs.filter((d:any)=>d.lifecycle==='needs_review').length}</strong></div></div>
      {work.state==='absent'?<div className="cw-notice" role="status"><h3>No ControlWork archive found</h3><p>This project has no records in the observed locations. Use Manage project memory above to preview initialization and import selected documents. Reading alone does not create records.</p></div>:
        work.state==='context_only'?<p className="cw-notice">Project context found; no .controlwork archive found. Installation health is not verified.</p>:null}
      <div className="cw-tabs" role="tablist" aria-label="ControlWork views" onKeyDown={tabsKey}>{pages.map((p,i)=><button key={p} id={'cw-tab-'+i} role="tab" aria-selected={page===p} aria-controls="cw-content" tabIndex={page===p?0:-1} onClick={()=>setPage(p)}>{p}</button>)}</div>
      <div id="cw-content" role="tabpanel" aria-labelledby={'cw-tab-'+pages.indexOf(page)}>
      {page==='Overview'&&<><div className="cw-overview"><article><h3>What is recorded</h3><p>Browse source documents, notes, plans, decisions, checkpoints and saved context packets. Lifecycle labels come from the records; they do not certify code completion.</p><button className="quiet" onClick={()=>setPage('Documents')}>Explore documents →</button></article><article><h3>Continue the work</h3><p>{sessions.length?'Review the latest session’s decisions and follow-ups.':'No session records were found in this scope.'}</p><button className="quiet" onClick={()=>setPage('Sessions')}>Open sessions →</button></article></div>
        <h3>Observation coverage</h3>{work.notices.map((n:string)=><p className="cw-notice" key={n}>{n}</p>)}<p className="muted">{work.counts.records} source records · {work.counts.bytes.toLocaleString()} bytes read · {new Date(data.workObservedAt).toLocaleString()}</p></>}
      {(page==='Documents'||page==='Decisions')&&<><label className="cw-search">Find a record<input value={filter} maxLength={240} onChange={e=>setFilter(e.target.value)} placeholder="Title, path or category"/></label><div className="cw-layout"><div className="cw-list">{visible.length?visible.map((d:any)=><button key={d.id} className={'cw-record '+(selected===d.id?'selected':'')} aria-pressed={selected===d.id} onClick={()=>pick(d.id)}><span className="eyebrow">{d.area} · {d.lifecycle}</span><strong>{d.title}</strong><code>{d.path}</code></button>):<p>No matching records in this scope.</p>}</div>{detail}</div></>}
      {page==='Sessions'&&<><p className="cw-notice">These are explicit session records, not an automatically collected chat history.</p><div className="cw-layout"><div className="cw-list">{sessions.length?sessions.map((s:any)=><button key={s.id} className={'cw-record '+(selected===s.node_id?'selected':'')} aria-pressed={selected===s.node_id} onClick={()=>pick(s.node_id)}><span className="eyebrow">{s.status} · {s.updatedAt||s.startedAt||'Date not recorded'}</span><strong>{s.topic||s.id}</strong><span>{s.summary.slice(0,200)}</span></button>):<p>No sessions recorded.</p>}</div>{detail}</div></>}
      {page==='Connections'&&<><p className="cw-notice">Solid: recorded relationships. Dashed: unaccepted topic suggestions. Rejected suggestions are available in record details; they are not drawn as active connections.</p><div className="cw-layout"><div><MemoryGraph graph={graph} selected={selected} pick={pick}/><details><summary>All records and chunks ({nodes.length})</summary><div className="cw-list">{nodes.map((n:any)=><button className="cw-record" key={n.id} onClick={()=>pick(n.id)}><strong>{n.title}</strong><span>{n.type} · {n.lifecycle}</span></button>)}</div></details></div>{detail}</div></>}
      {page==='Search & RAG'&&<><form className="cw-query" onSubmit={e=>{e.preventDefault();if(query.trim())invoke(()=>window.panel.workRead(query.trim()));}}><label>Search project knowledge<input value={query} maxLength={240} onChange={e=>setQuery(e.target.value)} placeholder="Architecture, decision, session topic…"/></label><button className="primary" disabled={data.busy||!query.trim()||!data.workScope}>Retrieve context</button></form><p className="cw-notice">Core graph retrieval over titles, headings and metadata. Each query rereads the approved scope. No AI answer is generated and nothing is sent to a provider.</p>
        {work.packet?<><h3>Context for “{work.query}”</h3><div className="cw-layout"><div>{work.packet.citations.length?work.packet.citations.map((c:any)=><button className="cw-record" key={c.citationId} onClick={()=>pick(c.id)}><span className="eyebrow">{c.citationId} · {c.lifecycle}</span><strong>{c.title}</strong><code>{c.citation}</code><span>{c.reasons.join(' · ')}</span></button>):<p>No matching citations. Try a recorded title, heading or topic.</p>}</div>{detail}</div>{work.packet.warnings.map((w:any,i:number)=><p className="cw-notice" key={i}>{w.citationId}: {w.message}</p>)}<details><summary>RAG context packet · generated in memory</summary><pre className="cw-text">{work.packet.packetMarkdown}</pre></details></>:<p>Enter a topic to inspect the retrieved citations and context packet.</p>}</>}
      </div>
    </>}
  </section>;
}

function MemoryGraph({graph,selected,pick}:{graph:any;selected:string;pick:(id:string)=>void}) {
  const records=graph.nodes.filter((n:any)=>n.recordType!=='chunk'),shown=records.slice(0,36);
  const positions=new Map<string,{x:number;y:number}>(shown.map((n:any,i:number)=>[n.id,{x:150+(i%2)*300,y:45+Math.floor(i/2)*85}]));
  const edges=[...graph.edges,...graph.suggestions.filter((s:any)=>s.status==='suggested'&&!s.auditOnly)].filter((e:any)=>positions.has(e.sourceId)&&positions.has(e.targetId));
  return <div className="cw-graph"><p>{shown.length} of {records.length} records shown · {edges.length} visible connections</p><svg viewBox={`0 0 600 ${Math.max(120,Math.ceil(shown.length/2)*85)}`} role="group" aria-label="Project knowledge graph">
    {edges.map((e:any,i:number)=>{const a=positions.get(e.sourceId)!,b=positions.get(e.targetId)!;return <line key={e.id+i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={e.status==='suggested'?'suggestion':'recorded'}><title>{e.status}: {e.reason}</title></line>;})}
    {shown.map((n:any)=>{const p=positions.get(n.id)!;return <g key={n.id} role="button" tabIndex={0} aria-label={n.title} aria-pressed={selected===n.id} onClick={()=>pick(n.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick(n.id);}}} className={selected===n.id?'selected':''}><title>{n.title} · {n.path}</title><rect x={p.x-128} y={p.y-28} width={256} height={56} rx={10}/><text x={p.x} y={p.y-3} textAnchor="middle">{n.title.length>28?n.title.slice(0,27)+'…':n.title}</text><text className="kind" x={p.x} y={p.y+17} textAnchor="middle">{n.type} · {n.lifecycle}</text></g>;})}
  </svg></div>;
}
