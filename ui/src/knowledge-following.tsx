import React from 'react';

const labels:Record<string,string>={indexed:'Indexed at last scan',refresh_required:'Refresh required',absent_at_last_scan:'Absent at last scan',not_followed:'No longer followed'};
export function FollowingLedger({ledger,busy,open}:{ledger:any;busy:boolean;open:(id:string)=>void}){
 if(!ledger)return null;
 return <details className="section" aria-label="Followed source status"><summary>Followed document status ({ledger.total})</summary>
  <p>Saved {ledger.mode==='selected'?'document selection':'rich-document scope'} · Last successful scan: {ledger.last_scan||'Never'}. These are stored observations, not a live disk check. Use Refresh sources &amp; wiki to reconcile changes.</p>
  {!ledger.enabled&&<p>Rich-document following is disabled. Retained history is separate.</p>}
  {!ledger.rows.length&&<p>No followed documents have been recorded. In whole-scope mode, files appear after a successful scan.</p>}
  {ledger.limited&&<p role="status">Showing the first {ledger.rows.length} of {ledger.total} records. Use an explicit selection for a smaller register.</p>}
  <div className="cw-list">{ledger.rows.map((row:any)=><article className="cw-record" key={row.path}>
   <strong>{row.path}</strong><span>{labels[row.state]||'Unknown'}</span>
   <span>{row.passages} indexed passages · {row.embedded} with embeddings</span>
   {row.revision&&<span>Last stored revision: <code>{row.revision.slice(0,12)}</code> · observed {row.observed}</span>}
   {row.state==='refresh_required'&&<span>Source freshness is unconfirmed. Previous passages may be retained until reconciliation succeeds.</span>}
   {row.state==='absent_at_last_scan'&&<span>This path was not present in the last successful scan; it has no active passages.</span>}
   {row.state==='indexed'&&row.id&&<button disabled={busy} onClick={()=>open(row.id)}>Read indexed page</button>}
  </article>)}</div>
 </details>;
}
