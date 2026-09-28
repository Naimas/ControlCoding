import React,{useEffect,useState} from 'react';
export function KnowledgeOcr({data,invoke}:{data:any;invoke:(f:()=>Promise<any>)=>void}){
 const [path,setPath]=useState(''),[text,setText]=useState('');
 useEffect(()=>{setPath('');setText('');},[data.generation]);
 const preview=data.knowledgeOcr,busy=data.busy||data.knowledgeBusy;
 const call=(action:string,value:any)=>invoke(()=>window.panel.knowledge(action,value));
 if(!data.knowledge?.policy?.scopes.includes('rich-documents'))return null;
 return <details className="section"><summary>Review OCR text for a followed PDF</summary>
  <p>Paste text from your OCR tool and check it against the original PDF. This workflow makes no provider call. The approved transcript is stored in project memory, takes precedence over an adjacent OCR sidecar, and is valid only for the exact PDF revision.</p>
  <label>Followed PDF path<input value={path} maxLength={1024} placeholder="docs/scanned.pdf" onChange={e=>setPath(e.target.value)}/></label>
  <label>Reviewed transcription<textarea value={text} maxLength={24000} onChange={e=>setText(e.target.value)}/></label>
  <button disabled={busy||!path||!text.trim()||new TextEncoder().encode(text).length>24000} onClick={()=>call('ocr-preview',{path,text})}>Preview OCR binding</button>
  {preview&&<div><p>Original: {preview.path} · SHA-256: <code>{preview.source_hash}</code></p><pre className="cw-text">{preview.text}</pre>
   <p>Page boundaries and transcription accuracy require your review. Saving does not modify the PDF. Refresh sources afterward to index the approved text.</p>
   <button disabled={busy||path!==preview.path||text!==preview.text} onClick={()=>call('ocr-save',{path,text,approval:preview.approval})}>Approve and save reviewed OCR</button>
  </div>}
 </details>;
}
