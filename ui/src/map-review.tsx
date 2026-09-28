import React, {useState} from 'react';

const messages:Record<string,string>={
  stale_snapshot:'Sources changed since observation. Refresh and review the change again.',
  definition_conflict:'Someone changed the saved mappings. Their bytes were preserved. Refresh before preparing a new change.',
  definition_busy:'The mapping store is busy or unavailable. No completed save is reported.',
  recovery_required:'A mapping transaction needs manual inspection. Keep transaction.json and definition.json intact; no automatic recovery or overwrite will run.',
  root_conflict:'The saved definition belongs to a different project identity.',
  alias_conflict:'Relink only a missing reviewed element to an unreviewed source of the same kind.',
  no_change:'This choice is already saved.',
  invalid_partition:'Each existing group member must belong to exactly one new group.',
  write_unsupported:'Saving reviewed mappings is currently supported on local Windows drives only.',
};
export function ReviewStatus({data,invoke}:{data:any;invoke:(fn:()=>Promise<any>)=>void}) {
  const preview=data.reviewPreview;
  return <div className="map-review-status">
    {data.reviewError&&<div role="alert" className="error"><b>Mapping review unavailable</b><p>{messages[data.reviewError.code]||'The mapping change could not complete. Refresh the map and check the saved definition before retrying.'}</p><code>{data.reviewError.code}</code></div>}
    {data.reviewSaved&&<p role="status" className="map-notice">Mapping choices saved. Code, completion and permissions were not changed.</p>}
    {data.committing&&<p role="status" className="map-notice">Saving the reviewed mapping. Wait for the result before switching projects.</p>}
    {preview&&<section className="mapping-preview" aria-label="Mapping change preview"><h3>Review changes before saving</h3><p>Only <code>{preview.path}</code> will be saved. This accepts mapping choices, not completed or verified code.</p>
      <ul>{preview.changes.map((c:any)=><li key={c.after.id}><b>{c.before?`${c.before.title} → ${c.after.title}`:`Add ${c.after.title}`}</b><p>{c.before?.decision||'Proposed'} → {c.after.decision} · {c.after.kind}</p>
        <details><summary>Exact mapping diff</summary><pre>{JSON.stringify({before:c.before,after:c.after},null,2)}</pre></details></li>)}</ul>
      <p className="muted">The source snapshot and saved revision will be checked again. Changed inputs require a new preview.</p>
      {!preview.write_supported&&<p>{messages.write_unsupported}</p>}
      <button className="primary" disabled={data.busy||!preview.write_supported} onClick={()=>invoke(window.panel.saveMapping)}>Save reviewed mappings</button>
      <button className="secondary" disabled={data.busy} onClick={()=>invoke(window.panel.discardMapping)}>Discard preview</button></section>}
  </div>;
}
export function MappingEditor({node,map,busy,invoke}:{node:any;map:any;busy:boolean;invoke:(fn:()=>Promise<any>)=>void}) {
  const entries:any[]=map.review?.entries||[],nodes:any[]=map.projection.bundle.nodes;
  const entry=entries.find(r=>r.id===node.id),groups=entries.filter(r=>r.members.length&&r.id!==node.id);
  const [title,setTitle]=useState(node.title),[members,setMembers]=useState<string[]>([]),[operation,setOperation]=useState('rename');
  const [replacement,setReplacement]=useState(''),[secondTitle,setSecondTitle]=useState('');
  const toggle=(id:string)=>setMembers(old=>old.includes(id)?old.filter(x=>x!==id):[...old,id]);
  const send=(change:object)=>invoke(()=>window.panel.reviewMapping(change));
  const candidates=operation==='merge'?groups:operation==='split'?nodes.filter(n=>entry?.members.includes(n.id)):nodes.filter(n=>['file','module','component'].includes(n.kind)&&n.id!==node.id);
  const submit=()=>{
    if(operation==='alias')send({operation,target:node.id,replacement});
    else if(operation==='group')send({operation,title,members});
    else if(operation==='merge')send({operation,title,targets:[node.id,...members]});
    else if(operation==='split')send({operation,target:node.id,groups:[{title,members},{title:secondTitle,members:entry.members.filter((id:string)=>!members.includes(id))}]});
    else send({operation:'rename',target:node.id,title});
  };
  return <section className="mapping-editor" aria-label="Review selected mapping"><h4>Review this mapping</h4><p>Keep your names and groupings across refreshes. No completion or permission changes.</p>
    <div className="map-actions"><button className="secondary" disabled={busy||node.mapping==='confirmed'} onClick={()=>send({operation:'accept',target:node.id})}>Confirm mapping</button><button className="secondary" disabled={busy||node.mapping==='rejected'} onClick={()=>send({operation:'reject',target:node.id})}>Reject mapping</button></div>
    <label>Change <select value={operation} disabled={busy} onChange={e=>{setOperation(e.target.value);setMembers([]);}}><option value="rename">Rename display label</option><option value="group">Create a code group</option>
      {entry?.members.length>0&&<><option value="split">Split this group</option><option value="merge">Merge groups</option></>}
      {entry?.bindings.length>0&&node.presence==='missing'&&<option value="alias">Relink missing source</option>}</select></label>
    {operation!=='alias'&&<label>{operation==='split'?'First group name':'Display name'}<input maxLength={160} value={title} disabled={busy} onChange={e=>setTitle(e.target.value)}/></label>}
    {operation==='split'&&<label>Second group name<input maxLength={160} value={secondTitle} disabled={busy} onChange={e=>setSecondTitle(e.target.value)}/></label>}
    {operation==='alias'&&<label>Observed replacement<select value={replacement} disabled={busy} onChange={e=>setReplacement(e.target.value)}><option value="">Choose source</option>{nodes.filter(n=>n.kind===node.kind&&n.mapping==='proposed'&&n.presence==='observed').map(n=><option key={n.id} value={n.id}>{n.title}</option>)}</select></label>}
    {['group','split','merge'].includes(operation)&&<fieldset className="mapping-members"><legend>{operation==='split'?'First group members (all others go to the second)':'Select members'}</legend>{candidates.map(n=><label key={n.id}><input type="checkbox" disabled={busy||(!members.includes(n.id)&&members.length>=63)} checked={members.includes(n.id)} onChange={()=>toggle(n.id)}/>{n.title}</label>)}</fieldset>}
    <button className="secondary" disabled={busy||(operation==='alias'?!replacement:!title.trim())||(['group','split','merge'].includes(operation)&&!members.length)||(operation==='split'&&(!secondTitle.trim()||members.length===entry.members.length))} onClick={submit}>Preview mapping change</button>
    {entry?.supersedes.length>0&&<p>Earlier group identities are retained in related elements.</p>}
  </section>;
}
