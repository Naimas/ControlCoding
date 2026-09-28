import React,{useEffect,useState} from 'react';
export function ManualExchange({data,dirty,invoke,onConcierge}:any){
 const [reply,setReply]=useState('');
 const p=data.manualHandoff;
 useEffect(()=>setReply(''),[p?.id,data.generation]);
 return <section className="manual-exchange" aria-label="Manual chat exchange">
 {p&&<><h3>1. Copy the reviewed packet to another chat</h3><p>{p.role} · round {p.round} · <code>{p.id}</code></p><p className="small muted">No API connection is needed. You choose and operate the external chat. Model identity, effort, time limits and external actions cannot be verified here.</p>
 <details open><summary>Outgoing prompt packet</summary><pre id="manual-packet" className="cw-text">{p.packet}</pre></details>
 <button id="manual-copy" className="primary" disabled={data.busy||dirty} onClick={()=>invoke(window.panel.manualCopy)}>Copy packet</button>{p.copied&&<p role="status">Copied to clipboard. Paste it into your chosen chat.</p>}
 <fieldset disabled={data.busy||dirty} className="cw-management-form"><legend>2. Paste and review the external reply</legend><p className="small muted">Include the first line <code>CC-REPLY: {p.id}</code>. If it is missing, ask the other chat to include it. The identifier associates the text with this request; it does not authenticate its author.</p>
 <label>External reply<textarea id="manual-reply" rows={7} maxLength={12500} value={reply} onChange={e=>{setReply(e.target.value);invoke(window.panel.manualDiscard);}}/></label>
 <button id="manual-preview" className="secondary" disabled={!reply.trim()} onClick={()=>invoke(()=>window.panel.manualPreview({requestId:p.id,text:reply}))}>Review pasted reply</button></fieldset></>}
 {data.manualError&&<p className="error" role="alert">Manual exchange unavailable: <code>{data.manualError}</code></p>}
 {data.manualReplyPreview&&<section className="config-receipt"><h3>3. Accept this reply into the role conversation</h3><p>{data.manualReplyPreview.provenance}</p><pre id="manual-reviewed-reply" className="cw-text">{data.manualReplyPreview.text}</pre><button id="manual-accept" className="primary" disabled={data.busy||dirty} onClick={()=>invoke(window.panel.manualAccept)}>Accept reply into conversation</button><p className="small muted">This adds text for the next round. It does not apply suggestions, run code or mark work complete.</p></section>}
 {data.manualResult&&<section className="cw-detail"><h3>External reply accepted · {data.manualResult.role}</h3><p className="small muted">{data.manualResult.provenance}</p><pre className="cw-text">{data.manualResult.text}</pre><p>Enter a follow-up request to continue the selected role. Or send this reply back to the concierge for review.</p><button id="manual-to-concierge" className="secondary" disabled={data.busy||dirty} onClick={()=>onConcierge(data.manualResult.requestId)}>Return reply to concierge</button><p className="small muted">Returning selects the concierge and attaches the reply. You still choose when to prepare or send its next request.</p></section>}
 </section>;
}
