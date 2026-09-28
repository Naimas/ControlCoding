import React from 'react';
export function RoleBehavior({config,selected,caps,templates,edit,connected,invoke}:any){
 const g=config.generation;
 const change=(key:string,value:any)=>edit('generation',{...g,[key]:value,...(key==='effort'?{temperature:null}:{})});
 const embedding=selected==='embedding',temperature=caps?.temperature==='always'||(caps?.temperature==='none'&&g.effort==='none');
 const select=(key:string,title:string,values:string[])=><label>{title}<select id={'role-'+key} value={g[key]} disabled={!values.length} onChange={e=>change(key,e.target.value)}><option value="default">Provider default (omit parameter)</option>{g[key]!=='default'&&!values.includes(g[key])&&<option value={g[key]}>{g[key]} — unavailable; reset options</option>}{values.map(v=><option key={v} value={v}>{v}</option>)}</select></label>;
 const rules=[templates.boundary,config.setupProposals?templates.proposal:templates.noProposal,g.format==='json'?templates.json:''].filter(Boolean).join('\n\n');
 return <section className="role-behavior" aria-labelledby="role-behavior-heading"><h3 id="role-behavior-heading">How this role acts</h3>
 {embedding?<p className="cw-notice" id="embedding-contract">The embedding endpoint receives your entered text and returns a vector. It does not accept a system prompt, perform a conversational role, or expose reasoning effort. It does not update the project index.</p>:<>
 <label>Agent instructions · saved per project<textarea id="role-system-prompt" rows={7} maxLength={4000} value={config.systemPrompt} onChange={e=>edit('systemPrompt',e.target.value)}/></label>
 <div className="config-steps"><button id="role-reset-prompt" type="button" className="secondary" onClick={()=>edit('systemPrompt',templates.prompts[selected])}>Restore preset prompt</button><span className="small muted">{config.systemPrompt.length} / 4000 characters · {config.systemPrompt===templates.prompts[selected]?'Preset':'Customized'}</span></div>
 <details><summary>Runtime boundaries included with the prompt</summary><pre className="cw-text">{rules}</pre><p className="small muted">Editing instructions changes the requested behavior. Tool permissions are enforced separately by the application.</p></details>
 <details><summary>Full effective system prompt</summary><pre id="role-effective-prompt" className="cw-text">{config.systemPrompt+'\n\n'+rules}</pre></details>
 </>}
 {config.mode==='handoff'?<p className="cw-notice">Manual chat exchange: copy the prompt and choose your external chat. Saved model preferences are included as requests only; API controls, identity, effort and execution cannot be enforced outside this app. No connection is required.</p>:<>
 <h3>Model features and reasoning</h3><p id="role-capability-source" className="small muted">{caps?.source||'Select and inspect a model to discover its controls.'}</p>
 {config.provider==='ollama'&&<button id="role-inspect" type="button" className="secondary" disabled={!connected||!config.model} onClick={()=>invoke(()=>window.panel.roleInspect(config.provider,config.model))}>Inspect model capabilities</button>}
 {(embedding?caps?.embedding:caps?.chat)===false&&<p className="error">This model does not support this role's endpoint. Select a compatible model.</p>}
 {!embedding&&<><div className="role-fields">
 {select('effort','Reasoning effort',caps?.efforts||[])}
 <label>Local thinking<select id="role-thinking" value={JSON.stringify(g.thinking)} disabled={!caps?.thinking?.length} onChange={e=>change('thinking',JSON.parse(e.target.value))}><option value="null">Provider default (omit parameter)</option>{g.thinking!==null&&!caps?.thinking?.includes(g.thinking)&&<option value={JSON.stringify(g.thinking)}>Unavailable saved value — reset options</option>}{(caps?.thinking||[]).map((v:any)=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{v===true?'On':v===false?'Off':v}</option>)}</select></label>
 {select('reasoningMode','Reasoning mode',caps?.modes||[])}
 {select('verbosity','Response verbosity',caps?.verbosity?['low','medium','high']:[])}
 <label>Temperature · blank uses provider default<input id="role-temperature" type="number" min={0} max={2} step={0.1} disabled={!temperature} value={g.temperature??''} onChange={e=>change('temperature',e.target.value===''?null:Number(e.target.value))}/></label>
 <label>Output format<select id="role-format" value={g.format} onChange={e=>change('format',e.target.value)}><option value="text">Text</option><option value="json" disabled={!caps?.json}>JSON object</option></select></label>
 </div><label><input id="role-summary" type="checkbox" checked={g.summary} disabled={!caps?.summary} onChange={e=>change('summary',e.target.checked)}/> Request the provider's reasoning summary</label>
 <p className="small muted">A summary is not hidden reasoning. Output tokens include reasoning on supporting APIs. Higher effort or Pro mode can use more time and tokens; the saved deadline still applies. Temperature may require an explicit “none” effort. Unsupported controls stay disabled.</p>
 <button id="role-reset-options" type="button" className="secondary" onClick={()=>edit('generation',{...templates.generationDefaults})}>Reset model options to provider defaults</button>
 <p className="small muted">Changing provider or model resets these options. Inspecting a model does not send your prompt.</p></>}
 <p id="role-active-features" className="cw-notice">Active: {embedding?'explicit text embeddings':`advisory text; context: ${config.context}; output: ${g.format}; ${config.setupProposals?'reviewed Setup proposals':'no Setup proposal import'}`}. Web search, file access, command execution and autonomous tools are unavailable in this executor, even if the model supports them.</p>
 </>}
 </section>;
}
