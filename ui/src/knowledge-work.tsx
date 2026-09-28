import React,{useState} from 'react';
export function KnowledgeWork({data,invoke}:{data:any;invoke:(f:()=>Promise<any>)=>void}){
 const [source,setSource]=useState(''),[target,setTarget]=useState(''),[sourceRole,setSourceRole]=useState('activity'),[targetRole,setTargetRole]=useState('objective'),[kind,setKind]=useState('part_of'),[reason,setReason]=useState('');
 const state=data.knowledgeWork,busy=data.busy||data.knowledgeBusy,sources=data.knowledgeCatalog?.sources||[];
 const [sourceRecord,setSourceRecord]=useState<any>(null),[targetRecord,setTargetRecord]=useState<any>(null);
 const choices=[...sources,...[sourceRecord,targetRecord].filter((r,i,a)=>r&&!sources.some((s:any)=>s.id===r.id)&&a.findIndex(x=>x?.id===r.id)===i)];
 const call=(action:string,value:any=null)=>invoke(()=>window.panel.knowledge(action,value));
 if(!data.knowledge?.enabled)return null;
 return <details className="section knowledge-work"><summary>Work traceability and reviewed relations</summary>
  <button disabled={busy} onClick={()=>call('work-view')}>Read work relations</button>
  {state&&<><p>{state.notice}</p>
   <details><summary>Canonical Work/Dev observations ({state.canonical?.length||0})</summary><p>Feature states and criteria come from existing project records. Enable Dev records in Memory settings to include them. Open a record to inspect its evidence; missing owners are shown explicitly.</p>
    {state.canonical?.map((n:any)=><article className="cw-record" key={n.id}><strong>{n.title}</strong><span>{n.record_type} · {n.state}{n.stale?' · refresh required':''}</span><span>Owner: {n.owner}</span><code>{n.path}</code>{n.issues?.map((issue:string)=><span key={issue}>{issue}</span>)}<button disabled={busy} onClick={()=>call('page',n.id)}>Read source-bound record</button></article>)}
    <p>{state.recorded_edge_total||0} recorded owner relationships ({state.recorded_edges?.length||0} in this bounded detail view). Canonical record changes use their existing owner workflow.</p>
   </details>
   <p>Select observed records and state their roles explicitly. The source choices follow the archive graph window above; use its search to find other records. No role or completion is inferred from a filename.</p>
   <div className="knowledge-controls"><label>From source<select value={source} onChange={e=>{setSource(e.target.value);setSourceRecord(choices.find((s:any)=>s.id===e.target.value));}}><option value="">Choose source</option>{choices.map((s:any)=><option key={s.id} value={s.id}>{s.title} · {s.path}</option>)}</select></label><label>Role<select value={sourceRole} onChange={e=>setSourceRole(e.target.value)}>{state.roles.map((r:string)=><option key={r}>{r}</option>)}</select></label>
   <label>Relation<select value={kind} onChange={e=>setKind(e.target.value)}>{state.kinds.map((r:string)=><option key={r}>{r}</option>)}</select></label>
   <label>To source<select value={target} onChange={e=>{setTarget(e.target.value);setTargetRecord(choices.find((s:any)=>s.id===e.target.value));}}><option value="">Choose target</option>{choices.map((s:any)=><option key={s.id} value={s.id}>{s.title} · {s.path}</option>)}</select></label><label>Role<select value={targetRole} onChange={e=>setTargetRole(e.target.value)}>{state.roles.map((r:string)=><option key={r}>{r}</option>)}</select></label></div>
   <label>Proposal or review rationale<textarea maxLength={2000} value={reason} onChange={e=>setReason(e.target.value)}/></label>
   <button disabled={busy||!source||!target||source===target||reason.trim().length<5} onClick={()=>call('work-propose',{snapshot:state.snapshot,source,target,source_role:sourceRole,target_role:targetRole,kind,reason})}>Save proposed relation</button>
   {!state.relations.length&&<p>No reviewed relations have been recorded.</p>}
   {state.relations.map((r:any)=><article className="cw-record" key={r.id}>
    <strong>{r.source_role}: {r.endpoints[0]?.title||r.source} → {r.kind} → {r.target_role}: {r.endpoints[1]?.title||r.target}</strong>
    <span>{r.status}{r.stale?' · evidence changed; review required':''}{r.effective?' · current reviewed revisions':''}</span><p>{r.reason}</p><p>{r.review_reason}</p>
    <details><summary>Source revisions and review history</summary>{r.endpoints.map((n:any,i:number)=><p key={i}>{n?.path||'Missing source'} · approved input <code>{r[i?'target_revision':'source_revision']}</code></p>)}{r.history.map((h:any,i:number)=><p key={i}>{h.updated}: {h.status} · {h.reason}</p>)}</details>
    <div>{['proposed','approved','rejected',...(r.kind==='conflicts'&&r.status==='approved'?['resolved']:[])].map(status=><button key={status} disabled={busy||(r.stale&&!['proposed','rejected'].includes(status))||reason.trim().length<5} onClick={()=>call('work-review',{snapshot:state.snapshot,id:r.id,status,reason})}>{status==='proposed'?'Rebind current sources as proposal':status==='approved'?'Approve relation':status==='resolved'?'Record conflict resolution':'Reject relation'}</button>)}</div>
   </article>)}
  </>}
 </details>;
}
