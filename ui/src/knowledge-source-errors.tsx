import React from 'react';
export function SourceErrors({rows,busy,retry}:{rows:any[];busy:boolean;retry:(path:string)=>void}){
 if(!rows?.length)return null;
 return <details className="section" open><summary>Source recovery ({rows.filter(r=>r.state!=='resolved').length} unresolved)</summary>
  <p>Up to 128 stored diagnostics. Retry checks the source and refreshes the selected project scope. Search stays paused until source errors are resolved; the last successful version is preserved.</p>
  {rows.map(row=><article className="cw-record" key={row.path}><strong>{row.path}</strong><code>{row.error}</code><span>{row.state} · {row.attempts} attempts · {row.observed}</span>
   {row.error==='ocr_required'&&<p>This PDF needs reviewed OCR text. Supply its adjacent .pdf.ocr.json sidecar with the original SHA-256, then retry.</p>}
   {row.error==='ocr_source_mismatch'&&<p>The OCR sidecar belongs to another revision. Regenerate and review it against the current PDF.</p>}
   {row.state!=='resolved'&&<button disabled={busy} onClick={()=>retry(row.path)}>Retry source and reconcile</button>}
  </article>)}
 </details>;
}
