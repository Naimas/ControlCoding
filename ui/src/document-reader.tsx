import React,{useEffect,useMemo,useRef,useState} from 'react';
import {parseDocument,MarkdownNode} from '../markdown-model.cjs';

type Source={id:string;title:string;path:string;physicalPath?:string;hash?:string};
type Asset={reference:string;status:string;data?:string;reason?:string};
type Document={path:string;sha256:string;markdown:string;bytes:number;images:Asset[]};
const messages:Record<string,string>={changed_input:'The file changed after observation. Close this reader, refresh the document map or archive, then reopen it.',busy:'Another read is in progress. Try again when it finishes.',stale_selection:'The selected project changed. Select the document again.',unknown_document:'This document is no longer in the observed scope. Refresh and select it again.',too_large:'This document exceeds the 256 KiB reading limit.',unsupported_path:'This source uses a linked or unsupported path.',invalid_document:'This file cannot be read as UTF-8 Markdown.'};
const imageReasons:Record<string,string>={remote_or_embedded_image:'Remote or embedded image — not downloaded',unsupported_path:'Image path is outside the allowed local scope or uses a link',unsupported_image:'Unsupported image format or SVG content',missing_image:'Image file not found',too_large:'Image exceeds the local reading budget'};

function localTarget(source:string,href:string){
 try{const [raw]=href.split('#');if(!raw||/^[a-z][a-z\d+.-]*:|^\/|\\/i.test(raw))return null;
  const parts=source.split('/').slice(0,-1);for(const p of decodeURIComponent(raw).split('/')){if(p==='..'){if(!parts.length)return null;parts.pop();}else if(p&&p!=='.')parts.push(p);}return parts.join('/');
 }catch{return null;}
}

export function DocumentReader({source,sources,generation,close,select,revisions=[],historyRequest=0}:{source:Source;sources:Source[];generation:number;close:()=>void;select:(id:string)=>void;revisions?:Source[];historyRequest?:number}){
 const [showHistory,setShowHistory]=useState(false);
 useEffect(()=>{if(historyRequest)setShowHistory(true);},[historyRequest]);
 const revisionIndex=revisions.findIndex(n=>n.id===source.id),older=revisionIndex>0;
 const [documentData,setDocument]=useState<Document|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[contents,setContents]=useState(false),[raw,setRaw]=useState(false),[active,setActive]=useState(''),[attempt,setAttempt]=useState(0);
 const drawer=useRef<HTMLElement>(null),scroll=useRef<HTMLDivElement>(null),opener=useRef(document.activeElement as HTMLElement|null);
 useEffect(()=>{const returnTo=opener.current;drawer.current?.querySelector<HTMLButtonElement>('#document-close')?.focus();return()=>{if(returnTo?.isConnected)returnTo.focus({preventScroll:true});};},[]);
 useEffect(()=>{let current=true;setDocument(null);setError('');setLoading(true);setRaw(false);setActive('');if(scroll.current)scroll.current.scrollTop=0;
  window.panel.documentOpen(generation,source.id).then(result=>{if(!current)return;if(result.status==='ok'&&result.generation===generation)setDocument(result.document);else setError(result.error?.code||'document_unavailable');setLoading(false);}).catch(()=>{if(current){setError('document_unavailable');setLoading(false);}});
  return()=>{current=false;};
 },[generation,source.id,attempt]);
 const frontmatter=useMemo(()=>documentData?/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(documentData.markdown):null,[documentData]);
 const sourceMetadata=frontmatter&&/^cc-(topic|series|revision|supersedes):/m.test(frontmatter[1])?frontmatter:null;
 const model=useMemo(()=>{try{return documentData?parseDocument(sourceMetadata?documentData.markdown.slice(sourceMetadata[0].length):documentData.markdown):null;}catch{return null;}},[documentData,sourceMetadata]);
 const jump=(id:string)=>{setRaw(false);setContents(false);setActive(id);requestAnimationFrame(()=>{const target=drawer.current?.querySelector<HTMLElement>('[data-heading='+JSON.stringify(id)+']');target?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});target?.focus({preventScroll:true});});};
 const images=new Map((documentData?.images||[]).map(a=>[a.reference,a]));
 function render(nodes:MarkdownNode[],prefix='n'):React.ReactNode[]{return nodes.map((node,index)=>{
  const key=prefix+'-'+index,children=render(node.children,key);
  if(node.type==='inline')return <React.Fragment key={key}>{children}</React.Fragment>;
  if(node.type==='text'||node.type.startsWith('html'))return <React.Fragment key={key}>{node.text}</React.Fragment>;
  if(node.type==='softbreak')return '\n';if(node.type==='hardbreak')return <br key={key}/>;
  if(node.type==='code_inline')return <code key={key}>{node.text}</code>;
  if(node.type==='fence'||node.type==='code_block')return <pre key={key}><code>{node.text}</code></pre>;
  if(node.type==='image'){const asset=images.get(node.attrs.src);return <span key={key} className="document-figure">{asset?.status==='available'?<img src={asset.data} alt={node.text} loading="lazy" onError={e=>{e.currentTarget.hidden=true;e.currentTarget.parentElement?.classList.add('image-decode-failed');}}/>:<span className="document-image-unavailable">{imageReasons[asset?.reason||'']||'Image unavailable in this bounded read'}<small>{node.attrs.src}</small></span>}<small className="document-image-failure">Image could not be decoded.</small>{node.text&&<span className="document-caption">{node.text}</span>}</span>;}
  if(node.type==='link_open'){const href=node.attrs.href||'';if(href.startsWith('#')){let target=href.slice(1);try{target=decodeURIComponent(target);}catch{}return <button key={key} className="document-link" onClick={()=>jump(target)}>{children}</button>;}
   const target=localTarget(source.physicalPath||source.path,href),other=sources.find(s=>(s.physicalPath||s.path)===target);return other?<button key={key} className="document-link" title={other.path} onClick={()=>select(other.id)}>{children}</button>:<span key={key} className="document-external" title={href+' · Outside this reader'}>{children}<span className="document-link-mark" aria-label={'Link target: '+href}> ↗</span></span>;
  }
  const allowed=['p','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','hr','strong','em','s','table','thead','tbody','tr','th','td'];
  if(!allowed.includes(node.tag))return <React.Fragment key={key}>{children.length?children:node.text}</React.Fragment>;
  const props:any={key};if(/^h[1-6]$/.test(node.tag))Object.assign(props,{id:'document-heading-'+node.attrs.id,'data-heading':node.attrs.id,tabIndex:-1});
  if(node.tag==='ol'&&/^\d{1,9}$/.test(node.attrs.start||''))props.start=Number(node.attrs.start);
  const element=React.createElement(node.tag,props,node.tag==='hr'?undefined:children);return node.tag==='table'?<div key={key} className="document-table">{element}</div>:element;
 });}
 const current=sources.find(s=>s.id===source.id),stale=!!documentData&&(!current||current.hash!==documentData.sha256);
 return <aside ref={drawer} id="document-reader" className="document-reader" role="dialog" aria-modal="false" aria-labelledby="document-reader-title" onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();if(contents)setContents(false);else close();}}}>
  <header className="document-toolbar"><div><span className="document-kicker">CONTROLWORK / DOCUMENT READER</span><h2 id="document-reader-title">{source.title}</h2><p title={source.physicalPath||source.path}>{source.physicalPath||source.path}</p></div><button id="document-close" aria-label="Close document reader" onClick={close}>✕</button></header>
  <div className="document-actions"><button id="document-toc-toggle" aria-expanded={contents} aria-controls="document-toc" disabled={!model?.headings.length} onClick={()=>setContents(!contents)}>☷ Contents{model?' · '+model.headings.length:''}</button><button id="document-source-toggle" disabled={!documentData} aria-pressed={raw} onClick={()=>setRaw(!raw)}>{raw?'Formatted document':'Markdown source'}</button>{revisions.length>1&&<button id="document-history-toggle" aria-expanded={showHistory} aria-controls="document-history" onClick={()=>setShowHistory(!showHistory)}>↶ History · {revisions.length}</button>}<span>Read-only · local file</span></div>
  {older&&<p className="document-warning" role="status">Historical revision — {revisions[0]?.title} is the latest observed document in this series.</p>}
  {showHistory&&revisions.length>1&&<section id="document-history" className="document-history" aria-label="Document revision history"><div><b>{older?'Earlier revision':'Latest observed revision'}</b><span>{revisionIndex+1} / {revisions.length}</span></div><label>Choose revision<select id="document-revision-select" value={source.id} disabled={loading} onChange={e=>select(e.target.value)}>{revisions.map((n,i)=><option key={n.id} value={n.id}>{i===0?'Latest':'Earlier '+i} — {n.title} · {n.path}</option>)}</select></label><div className="document-history-scroll"><button aria-label="Newer revision" disabled={loading||revisionIndex<=0} onClick={()=>select(revisions[revisionIndex-1].id)}>← Newer</button><input id="document-revision-range" type="range" aria-label="Browse document revisions" min={0} max={revisions.length-1} value={Math.max(0,revisionIndex)} disabled={loading} onChange={e=>select(revisions[Number(e.target.value)].id)}/><button aria-label="Older revision" disabled={loading||revisionIndex>=revisions.length-1} onClick={()=>select(revisions[revisionIndex+1].id)}>Older →</button></div><p>Only this observed series is collapsed. Earlier files remain intact and open as their own original source.</p></section>}
  {stale&&<p className="document-warning" role="status">The map has a newer observation or this source left its scope. This page shows the previously opened version. Close and reopen it after refreshing.</p>}
  <div className="document-body">
   <div ref={scroll} id="document-scroll" className="document-scroll" tabIndex={0} aria-label="Document reading area">
    {loading?<p role="status" className="document-message">Opening the full source…</p>:error?<div className="document-message" role="alert"><h3>Document unavailable</h3><p>{messages[error]||'The bounded file read could not complete. Refresh the source and retry.'}</p><code>{error}</code>{error==='busy'&&<button onClick={()=>setAttempt(attempt+1)}>Retry</button>}</div>:documentData&&model?<>
     <article className="document-paper">{raw?<pre id="document-raw">{documentData.markdown}</pre>:<>{sourceMetadata&&<details className="document-source-metadata"><summary>Source metadata</summary><pre>{sourceMetadata[1]}</pre></details>}<div id="document-formatted">{render(model.tree)}</div></>}</article>
     <footer className="document-footnote">Complete UTF-8 Markdown · {documentData.bytes.toLocaleString()} bytes. Contents follows source headings. HTML stays literal; unsupported Markdown extensions stay as text. {model.omittedImages>0&&`${model.omittedImages} additional images exceed the 24-reference limit.`}<details><summary>Source identity and image scope</summary><code>{documentData.sha256}</code><p>Local images only, up to 384 KiB combined. External links are shown without opening a browser; observed local documents open here. Refresh the map before reopening an edited file.</p></details></footer>
    </>:<p role="alert" className="document-message">This Markdown exceeds the formatting budget. Close and reduce the document size.</p>}
   </div>
   {contents&&<nav id="document-toc" className="document-toc" aria-label="Document contents"><header><b>In this document</b><button aria-label="Close contents" onClick={()=>setContents(false)}>✕</button></header><p>Headings from the original file</p>{model?.headings.map(h=><button key={h.id} className={'document-toc-level-'+h.level+(active===h.id?' active':'')} aria-current={active===h.id?'location':undefined} onClick={()=>jump(h.id)}>{h.label||'Untitled section'}</button>)}</nav>}
  </div>
 </aside>;
}
