import React, {useEffect, useState} from 'react';

type Citation = {source:string;path:string;revision:string;line:number;end_line:number;excerpt:string};
type ReviewPage = {type:string;id:string;title:string;revision:string|null;sections:Array<{key:string;title:string;body:string;issues:Array<{code:string}>}>};
type Proposal = {id:string;page_type:string;key:string;title:string;body:string;status:string;created:string;dependencies:Citation[];issues:Array<{code:string}>;eligible:boolean};
type Review = {pages:ReviewPage[];proposals:Proposal[];notice:string};
type Props = {review?:Review|null;page?:{id:string;dependencies:Citation[]}|null;busy:boolean;call:(action:string,value?:any)=>void};

export function WikiReview({review,page,busy,call}:Props){
 const current=review?.pages.find(item=>item.id===page?.id);
 const [key,setKey]=useState('');
 const [title,setTitle]=useState('');
 const [body,setBody]=useState('');
 const [selected,setSelected]=useState<string[]>([]);
 useEffect(()=>{setKey('');setTitle('');setBody('');setSelected([]);},[page?.id]);
 if(!review)return <button disabled={busy} onClick={()=>call('wiki-review-view')}>Load reviewed wiki</button>;
 const candidates=(page?.dependencies||[]).filter((dep,index,all)=>
  dep.source&&!dep.source.startsWith('wiki:')&&all.findIndex(other=>other.source===dep.source&&other.revision===dep.revision&&other.line===dep.line)===index);
 const citations=candidates.filter(dep=>selected.includes(dep.source+':'+dep.line)).slice(0,8)
  .map(({source,path,revision,line,end_line,excerpt})=>({source,path,revision,line,end_line,excerpt}));
 return <section aria-label="Reviewed wiki">
  <p>{review.notice}</p>
  <div className="cw-actions"><button disabled={busy} onClick={()=>call('wiki-review-view')}>Refresh review status</button>{review.pages.map(item=><button key={item.id} disabled={busy} onClick={()=>call('page',item.id)}>{item.title}</button>)}</div>
  {current&&<>
   <h3>{current.title} review</h3>
   {current.sections.map(section=><details key={section.key}><summary>{section.title} {section.issues.length?' - sources changed':''}</summary><p>Section key: <code>{section.key}</code></p><pre className="cw-text">{section.body}</pre>{section.issues.map((issue,i)=><p key={i} role="status">{issue.code}</p>)}</details>)}
   <details><summary>Propose a human section or replacement</summary><p>The page and source revisions are checked again when a reviewer accepts. Cite selected source lines using [S1], [S2], and so on. Accepted text remains visible after source refresh and is flagged if its evidence changes.</p>
    <label>Section key<input value={key} maxLength={64} placeholder="project-purpose" onChange={event=>setKey(event.target.value)}/></label>
    <label>Title<input value={title} maxLength={120} onChange={event=>setTitle(event.target.value)}/></label>
    <label>Reviewed text<textarea value={body} maxLength={8000} onChange={event=>setBody(event.target.value)}/></label>
    <fieldset><legend>Source anchors (up to eight)</legend>{candidates.slice(0,32).map(dep=>{const id=dep.source+':'+dep.line;return <label key={id}><input type="checkbox" checked={selected.includes(id)} disabled={!selected.includes(id)&&selected.length>=8} onChange={event=>setSelected(event.target.checked?[...selected,id]:selected.filter(value=>value!==id))}/>{dep.path}:{dep.line} - {dep.excerpt.slice(0,120)}</label>;})}</fieldset>
    <button disabled={busy||!current.revision||!key.trim()||!title.trim()||!body.trim()||!citations.length} onClick={()=>call('wiki-review-propose',{page_type:current.type,key,title,body,citations,base_revision:current.revision})}>Submit for review</button>
   </details>
  </>}
  <details><summary>Review proposals ({review.proposals.filter(item=>item.status==='pending').length} pending)</summary>{review.proposals.slice().reverse().map(item=><section key={item.id} className="cw-record"><strong>{item.title}</strong><p>{item.page_type} / {item.key} - {item.status} - {item.created}</p><pre className="cw-text">{item.body}</pre>{item.dependencies.map((dep,i)=><p key={i}>[S{i+1}] {dep.path}:{dep.line} @ {dep.revision.slice(0,12)}</p>)}{item.issues.map((issue,i)=><p key={i} role="status">{issue.code} - propose against the current page and sources</p>)}{item.status==='pending'&&<div className="cw-actions"><button disabled={busy||!item.eligible} onClick={()=>call('wiki-review-decide',{id:item.id,decision:'accept'})}>Accept</button><button disabled={busy} onClick={()=>call('wiki-review-decide',{id:item.id,decision:'reject'})}>Reject</button></div>}</section>)}</details>
 </section>;
}
