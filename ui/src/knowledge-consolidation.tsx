import React,{useEffect,useState} from 'react';
import {ConsolidationExecution} from './knowledge-consolidation-execution';
import {KnowledgeConsolidationMap} from './knowledge-consolidation-map';

type Citation={source:string;path:string;revision:string;line:number;end_line:number;excerpt:string;title:string};
type Proposal={id:string;page_type:string;key:string;title:string;body:string;kind:string;reason:string;status:string;base_revision:string|null;dependencies:Citation[];before:{title:string;body:string}|null;eligible:boolean;epistemic_status?:string;scope?:string;conflicting?:Citation[];prerequisite_ids?:string[]};
type View={enabled:boolean;schema:1|2|3;notice:string;jobs:Array<{id:string;title:string;state:string;created:string;count:number}>;job:null|{id:string;title:string;state:string;stale:boolean;proposals:Proposal[];receipt:null|{request_id:string;decision:string;patches:Array<{target:string;before:any;after:any}>;at:string;undone?:boolean};packet?:any;attempts?:any[]};sources:Citation[];coverage:{selected:number;total:number;deferred:number};progress?:{inventory:number;eligible:number;analyzed:number;excluded:number;passages:number;pending_passages:number;analyzed_passages:number};pages:Array<{type:string;id:string;title:string;revision:string|null}>};
type Props={view:View|null;busy:boolean;memoryEnabled:boolean;projectGeneration:number;call:(action:string,value?:any)=>void;migrate:()=>void;Paper:React.ComponentType<{text:string}>;data:any;invoke:(f:()=>Promise<any>)=>void;context:any;onOpenSource:(source:string,line:number)=>void};
const requestId=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');

export function KnowledgeConsolidation({view,busy,memoryEnabled,projectGeneration,call,migrate,Paper,data,invoke,context,onOpenSource}:Props){
 const [jobTitle,setJobTitle]=useState('');
 const [pageType,setPageType]=useState('overview'),[key,setKey]=useState(''),[title,setTitle]=useState(''),[body,setBody]=useState(''),[kind,setKind]=useState('summary'),[reason,setReason]=useState('');
 const [anchors,setAnchors]=useState<string[]>([]),[selected,setSelected]=useState<string[]>([]);
 useEffect(()=>{setAnchors([]);setSelected([]);setPageType('overview');setKey('');setTitle('');setBody('');setKind('summary');setReason('');},[projectGeneration,view?.job?.id]);
 if(!view)return <section aria-label="Memory consolidation"><p>Prepare source-bound memory proposals with a local model, an API, or a manual external chat packet. Publication always requires your selection.</p><button disabled={busy} onClick={()=>call('consolidation-view')}>Load consolidation</button></section>;
 const job=view.job;
 const sourceKey=(c:Citation)=>[c.source,c.revision,c.line,c.end_line].join(':');
 const citations=view.sources.filter(c=>anchors.includes(sourceKey(c))).slice(0,8).map(({source,path,revision,line,end_line,excerpt})=>({source,path,revision,line,end_line,excerpt}));
 const pending=job?.proposals.filter(p=>p.status==='pending')||[];
 const checked=pending.filter(p=>selected.includes(p.id));
 const eligible=checked.length>0&&checked.every(p=>p.eligible)&&!job?.stale;
 const decide=(decision:'accept'|'reject')=>{if(!job||!checked.length)return;call('consolidation-decide',{job:job.id,ids:checked.map(p=>p.id),decision,request_id:requestId()});setSelected([]);};
 return <section aria-label="Memory consolidation">
  <h3>Memory consolidation</h3><p>{view.notice}</p>
  <p>Draft changes for the eleven reviewed wiki pages using pinned source excerpts. Accept only selected proposals; publication checks source and page revisions again. Original sources and canonical records stay separate.</p>
  {view.schema<3?<section className="cw-notice" role="status"><p>Analysis requires an explicit schema 3 migration. Existing memory tools continue to use this archive until the verified external backup succeeds.</p><p>Choose a new external <code>.ccmemory</code> path and keep the backup for recovery.</p><button disabled={busy||!memoryEnabled} onClick={migrate}>Choose backup and migrate</button></section>:<>
   <div className="cw-actions"><button disabled={busy} onClick={()=>call('consolidation-view',job?{job:job.id}:null)}>Refresh consolidation</button></div>
   {!view.enabled?<p>Enable project memory before preparing a consolidation.</p>:<>
    <label>Job title<input maxLength={120} value={jobTitle} onChange={e=>setJobTitle(e.target.value)} placeholder="Review project memory"/></label>
    <button disabled={busy||!jobTitle.trim()} onClick={()=>{call('consolidation-create',{title:jobTitle.trim()});setJobTitle('');}}>Prepare consolidation</button>
    <p className="small muted">Coverage: {view.coverage.selected} selected of {view.coverage.total}; {view.coverage.deferred} deferred. Preparation pins a bounded evidence window. A partial window is never called project-wide analysis.</p>
    {view.progress&&<p className="small muted">Incremental progress: {view.progress.analyzed} sources analyzed; {view.progress.eligible} eligible sources remain; {view.progress.analyzed_passages} of {view.progress.passages} passages analyzed; {view.progress.pending_passages} passages pending; {view.progress.excluded} sources excluded.</p>}
    <details open><summary>Durable jobs ({view.jobs.length})</summary><div className="cw-list">{view.jobs.map(item=><button key={item.id} className="cw-record" disabled={busy} aria-pressed={job?.id===item.id} onClick={()=>call('consolidation-view',{job:item.id})}><strong>{item.title}</strong><span>{item.state} · {item.created} · {item.count} proposals</span></button>)}</div></details>
    <ConsolidationExecution data={data} view={view} busy={busy} call={call} invoke={invoke}/>
    {job&&<><h4>{job.title}</h4><p>State: {job.state}. {job.stale?'Sources or target changed; pending publication is stale. Prepare a new job.':'Pinned source and page revisions are checked at publication.'}</p>
     <details><summary>Draft a source-bound proposal</summary><p>Choose original source anchors, then write the reviewed text. Refer to selected anchors as [S1], [S2], and so on. The source excerpts below are a bounded starting set; they do not cover all eligible sources.</p>
      <label>Target wiki page<select value={pageType} onChange={e=>setPageType(e.target.value)}>{view.pages.map(p=><option key={p.type} value={p.type}>{p.title}</option>)}</select></label>
      <label>Section key<input value={key} maxLength={64} pattern="[a-z0-9][a-z0-9_-]*" onChange={e=>setKey(e.target.value)} placeholder="project-purpose"/></label>
      <label>Title<input value={title} maxLength={120} onChange={e=>setTitle(e.target.value)}/></label>
      <label>Kind<select value={kind} onChange={e=>setKind(e.target.value)}>{[['summary','Summary'],['decision_summary','Recorded decision summary'],['lesson','Lesson'],['preference','Explicit project preference'],['open_question','Open question']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      <label>Reason<textarea value={reason} maxLength={1000} onChange={e=>setReason(e.target.value)}/></label>
      <label>Proposed section<textarea value={body} maxLength={3000} onChange={e=>setBody(e.target.value)}/></label>
      <fieldset><legend>Original source anchors (select up to eight)</legend>{view.sources.map(c=>{const id=sourceKey(c);return <label key={id}><input type="checkbox" checked={anchors.includes(id)} disabled={!anchors.includes(id)&&anchors.length>=8} onChange={e=>setAnchors(e.target.checked?[...anchors,id]:anchors.filter(value=>value!==id))}/>{c.title} · {c.path}:{c.line}-{c.end_line}<details><summary>Read source excerpt</summary><Paper text={c.excerpt}/><code>Revision {c.revision}</code></details></label>;})}</fieldset>
      <button disabled={busy||job.stale||!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(key)||!title.trim()||!reason.trim()||!body.trim()||!citations.length} onClick={()=>call('consolidation-propose',{job:job.id,page_type:pageType,key,title:title.trim(),body:body.trim(),kind,citations,reason:reason.trim()})}>Add proposal for review</button>
     </details>
     <h4>Proposals ({pending.length} pending)</h4>{!job.proposals.length&&<p>No proposals yet. Draft one from the pinned source anchors.</p>}
     {job.proposals.map(p=><section className="cw-record" key={p.id}><label><input type="checkbox" checked={selected.includes(p.id)} disabled={busy||p.status!=='pending'} onChange={e=>setSelected(e.target.checked?[...selected,p.id]:selected.filter(id=>id!==p.id))}/><strong>{p.title}</strong> · {p.status}</label><p>{p.kind} · {p.page_type}/{p.key} · {p.reason}</p><p>Evidence status: <strong>{p.epistemic_status||'not recorded'}</strong> · scope: {p.scope||'not recorded'}</p>{!!p.prerequisite_ids?.length&&<p>Depends on proposals: {p.prerequisite_ids.join(', ')}</p>}{!!p.conflicting?.length&&<details open><summary>Conflicting original evidence ({p.conflicting.length})</summary>{p.conflicting.map(c=><p key={sourceKey(c)}>{c.path}:{c.line}-{c.end_line} · revision {c.revision}<br/>{c.excerpt}</p>)}</details>}{p.status==='pending'&&!p.eligible&&<p className="cw-notice" role="status">This proposal is ineligible; refresh or prepare a new job.</p>}<div className="knowledge-columns"><div><h5>Before</h5>{p.before?<><strong>{p.before.title}</strong><Paper text={p.before.body}/></>:<p>No existing reviewed section.</p>}</div><div><h5>Proposed</h5><Paper text={p.body}/></div></div><details><summary>Cited original sources ({p.dependencies.length})</summary>{p.dependencies.map((c,i)=><section key={sourceKey(c)}><strong>[S{i+1}] {c.title||c.path}</strong><p>{c.path}:{c.line}-{c.end_line} · revision {c.revision}</p><Paper text={c.excerpt}/></section>)}</details></section>)}
     <div className="cw-actions"><button disabled={busy||!eligible} onClick={()=>decide('accept')}>Accept selected ({checked.length})</button><button disabled={busy||!checked.length} onClick={()=>decide('reject')}>Reject selected ({checked.length})</button></div>
     {job.receipt&&<details open><summary>Published changes in this job</summary><p>{job.receipt.at} · {job.receipt.patches.length} target changes {job.receipt.undone?'· undone':''}</p>{job.receipt.patches.map((p,i)=><details key={i}><summary>{p.target}</summary><div className="knowledge-columns"><div><h5>Before</h5><Paper text={String(p.before?.body||'')}/></div><div><h5>After</h5><Paper text={String(p.after?.body||'')}/></div></div></details>)}{job.receipt.decision==='accept'&&!job.receipt.undone&&<button disabled={busy} onClick={()=>call('consolidation-undo',{job:job.id,request_id:requestId()})}>Undo published changes in this job</button>}</details>}
    </>}
    <KnowledgeConsolidationMap view={context} busy={busy} onRefresh={()=>call('consolidation-context')} onNext={offset=>call('consolidation-context',{offset})} onHistory={(history,offset)=>call('consolidation-context',{history,...(offset?{offset}:{})})} onOpenSource={onOpenSource} Paper={Paper}/>
   </>}
  </>}
 </section>;
}
