import React,{useMemo,useState} from 'react';

export type OriginalAnchor={source:string;path:string;title:string;revision:string;line:number;end_line:number;excerpt:string;kind:string;origin:'original_source'};
export type ContextClaim={id:string;target:string;kind:string;title:string;body:string;revision:string;review_status:string;current_status:string;epistemic_status:string;scope:string;updated:string;evidence:OriginalAnchor[];source_ids:string[]};
export type ContextBacklink={source:string;claim:string;target:string;line:number;end_line:number;revision:string};
export type ContextView={schema:number;query?:string;generation:number|null;consolidation_epoch:number;claims:ContextClaim[];backlinks:ContextBacklink[];history:ContextClaim[];total:number;offset:number;next_offset:number|null;history_total:number;history_next_offset:number|null;notice:string};
type Props={view:ContextView|null;busy:boolean;onRefresh:()=>void;onNext:(offset:number)=>void;onHistory:(claim:string,offset?:number)=>void;onOpenSource:(source:string,line:number)=>void;Paper:React.ComponentType<{text:string}>};

/** A small graph window. All displayed edges have a source anchor in this page. */
export function KnowledgeConsolidationMap({view,busy,onRefresh,onNext,onHistory,onOpenSource,Paper}:Props){
 const [selected,setSelected]=useState('');
 const [hoveredClaim,setHoveredClaim]=useState(''),[hoveredSource,setHoveredSource]=useState('');
 const claims=view?.claims||[];
 const active=claims.find(c=>c.id===selected)||claims[0];
 const sources=useMemo(()=>{
  const found=new Map<string,OriginalAnchor>();
  for(const claim of claims)for(const evidence of claim.evidence)found.set(evidence.source,evidence);
  return [...found.values()].slice(0,40);
 },[claims]);
 const highlightedClaim=hoveredClaim||(!hoveredSource?active?.id||'':'');
 const highlighted=claims.find(c=>c.id===highlightedClaim);
 const connected=new Set(highlighted?.source_ids||[]);
 const width=720,row=46,sourceX=12,claimX=390,sourceY=48,claimY=48;
 const sourcePositions=new Map(sources.map((s,i)=>[s.source,sourceY+i*row]));
 const height=Math.max(120,Math.max(sources.length,claims.length)*row+70);
 return <section className="knowledge-consolidation-map" aria-label="Approved memory and original evidence map">
  <div className="cw-actions"><h4>Approved memory map</h4><button disabled={busy} onClick={onRefresh}>Refresh approved claims</button></div>
  <p className="small muted">Current, approved claims only. Each line leads to a current original source anchor. Selecting a claim highlights its source links; history is opened explicitly.</p>
  {!view?<p>Load approved claims to inspect their sources.</p>:<>
   <p className="small muted">{view.total} matching current claims · generation {view.generation??'unavailable'} · consolidation revision {view.consolidation_epoch}. {view.notice}</p>
   {!claims.length&&<p>No current approved claims in this window.</p>}
   {!!claims.length&&<div style={{overflowX:'auto',maxWidth:'100%'}}><svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{minWidth:520,maxHeight:500}} role="group" aria-label={`${sources.length} original sources linked to ${claims.length} approved claims`}>
    <text x={sourceX} y="25" fontSize="14" fill="currentColor">ORIGINAL SOURCES</text><text x={claimX} y="25" fontSize="14" fill="currentColor">REVIEWED CLAIMS</text>
    {claims.flatMap((claim,i)=>claim.evidence.map((evidence,j)=>{
     const sy=sourcePositions.get(evidence.source);if(sy===undefined)return null;
     const cy=claimY+i*row,highlight=hoveredSource?evidence.source===hoveredSource:claim.id===highlightedClaim;
     return <path key={`${claim.id}:${evidence.source}:${j}`} d={`M 288 ${sy+15} H 335 Q 345 ${sy+15} 345 ${sy<cy?sy+25:sy+5} V ${cy+5} Q 345 ${cy+15} 355 ${cy+15} H ${claimX}`} fill="none" stroke={highlight?'#219b6b':'#9aa9b2'} strokeWidth={highlight?2.5:1} opacity={highlight?1:.08}/>;
    }))}
    {sources.map((source,i)=><g key={source.source} role="button" tabIndex={0} aria-label={`Open ${source.title}, ${source.path}, line ${source.line}`} onMouseEnter={()=>setHoveredSource(source.source)} onMouseLeave={()=>setHoveredSource('')} onFocus={()=>setHoveredSource(source.source)} onBlur={()=>setHoveredSource('')} onClick={()=>onOpenSource(source.source,source.line)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onOpenSource(source.source,source.line);}}} style={{cursor:'pointer'}} opacity={hoveredSource?hoveredSource===source.source?1:.35:!active||connected.has(source.source)?1:.35}>
     <rect x={sourceX} y={sourceY+i*row} width="276" height="31" rx="5" fill="#e9f2f3" stroke="#739ca3"/>
     <text x={sourceX+8} y={sourceY+i*row+20} fontSize="12" fill="#152b2f">{source.title.slice(0,36)}</text>
    </g>)}
    {claims.map((claim,i)=><g key={claim.id} role="button" tabIndex={0} aria-label={`Select approved claim ${claim.title}`} aria-pressed={active?.id===claim.id} onMouseEnter={()=>setHoveredClaim(claim.id)} onMouseLeave={()=>setHoveredClaim('')} onFocus={()=>setHoveredClaim(claim.id)} onBlur={()=>setHoveredClaim('')} onClick={()=>setSelected(claim.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setSelected(claim.id);}}} style={{cursor:'pointer'}} opacity={hoveredSource&&!claim.source_ids.includes(hoveredSource)?0.35:1}>
     <rect x={claimX} y={claimY+i*row} width="306" height="31" rx="5" fill={claim.id===active?.id?'#d9f4e8':'#f2f1e9'} stroke={claim.id===active?.id?'#219b6b':'#a9a58b'}/>
     <text x={claimX+8} y={claimY+i*row+20} fontSize="12" fill="#152b2f">{claim.title.slice(0,39)}</text>
    </g>)}
   </svg></div>}
   {!!view.backlinks.length&&<details><summary>Reverse source backlinks ({view.backlinks.length})</summary>
    <p>Each source anchor links to the approved claim that cites it.</p>
    <div className="cw-list">{view.backlinks.map((link,i)=>{
     const source=sources.find(item=>item.source===link.source),claim=claims.find(item=>item.id===link.claim);
     if(!source||!claim)return null;
     return <div className="cw-record" key={`${link.claim}:${link.source}:${i}`}>
      <button onClick={()=>onOpenSource(link.source,link.line)}>{source.title} · {source.path}:{link.line}-{link.end_line}</button>
      <span>cited by</span><button onClick={()=>setSelected(claim.id)}>{claim.title}</button>
     </div>;
    })}</div>
   </details>}
   <div className="knowledge-columns"><div className="cw-list" aria-label="Approved claims">{claims.map(claim=><button key={claim.id} className="cw-record" aria-pressed={active?.id===claim.id} onClick={()=>setSelected(claim.id)}>
    <strong>{claim.title}</strong><span>{claim.epistemic_status} · {claim.review_status} · {claim.current_status}</span><small>{claim.scope} · {claim.target}</small>
   </button>)}</div>
   <aside aria-live="polite">{active&&<><h5>{active.title}</h5><p>{active.kind} · epistemic: <strong>{active.epistemic_status}</strong> · review: {active.review_status} · {active.current_status}</p>
    {active.epistemic_status==='disputed'&&<p role="status">Disputed evidence remains unresolved. Inspect every side before using this claim.</p>}
    <Paper text={active.body}/><div className="cw-actions"><button disabled={busy} onClick={()=>onHistory(active.id)}>Show claim history</button></div>
    <h5>Original evidence and reverse links</h5>{active.evidence.map((source,i)=><button className="cw-record" key={`${source.source}:${i}`} onClick={()=>onOpenSource(source.source,source.line)}>
     <strong>{source.title}</strong><span>{source.path}:{source.line}-{source.end_line} · {source.kind}</span><small>{source.revision.slice(0,16)}</small>
    </button>)}
   </>}</aside></div>
   {!!view.history.length&&<details open><summary>Explicit historical claim view ({view.history.length})</summary><p>Older revisions are historical records, not current answer context.</p>{view.history.map(item=><section key={item.id+item.revision} className="cw-record"><strong>{item.title}</strong><p>{item.updated} · {item.review_status} · historical · {item.epistemic_status}</p><Paper text={item.body}/></section>)}</details>}
   {!!view.history.length&&view.history_next_offset!==null&&<button disabled={busy} onClick={()=>onHistory(view.history[0].id,view.history_next_offset!)}>Next historical revisions</button>}
   <div className="cw-actions"><button disabled={busy||!view.offset} onClick={()=>onNext(Math.max(0,view.offset-20))}>Previous approved claims</button>
   {view.next_offset!==null&&<button disabled={busy} onClick={()=>onNext(view.next_offset!)}>Next approved claims</button>}</div>
  </>}
 </section>;
}
