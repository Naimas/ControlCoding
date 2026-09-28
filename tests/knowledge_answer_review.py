"""Render actual model responses and supplied evidence for human answer review."""
import argparse
import hashlib
import html
import json
from pathlib import Path


def render(corpus, run, responses, digest):
    questions = {q['id']: q for q in corpus['questions']}
    evidence = {r['id']: r for r in run['results']}
    cards = []
    seen = set()
    for response in responses['results']:
        identifier = response['id']
        if identifier in seen or identifier not in questions or identifier not in evidence:
            raise ValueError('Duplicate or unknown response question')
        seen.add(identifier)
        q, original = questions[identifier], evidence[identifier]
        text = response.get('raw', {}).get('text') or response.get('text') or 'ERROR: ' + response.get('error', 'missing response')
        supplied = ''.join('<details><summary>[' + html.escape(c['id']) + '] ' + html.escape(c['path']) + ':' + str(c['line']) +
                           '</summary><pre>' + html.escape(c['excerpt']) + '</pre><small>Revisione ' + html.escape(c['revision']) + '</small></details>'
                           for c in original['citations'])
        options = '<option value="">Da valutare</option>' + ''.join('<option value="' + value + '">' + label + '</option>' for value, label in (
            ('supported', 'Risposta confermata dalle fonti fornite'),
            ('unsupported', 'Risposta non sostenuta dalle fonti'),
            ('justified_abstention', 'Astensione giustificata: mancano le prove'),
            ('unjustified_abstention', 'Astensione ingiustificata: le fonti permettevano di rispondere'),
            ('error', 'Risposta assente o inutilizzabile'),
            ('uncertain', 'Non riesco a stabilirlo')))
        cards.append('<article class="case" data-case><small>' + html.escape(identifier) + '</small><h2>' + html.escape(q['question']) +
                     '</h2><pre>' + html.escape(text) + '</pre><label>Giudizio <select data-id="' + html.escape(identifier, quote=True) +
                     '">' + options + '</select></label><p><label>Motivazione e riferimenti alle fonti<br>' +
                     '<textarea rows="3" placeholder="Esempio: S2 conferma questa affermazione; S4 riguarda una versione precedente."></textarea>' +
                     '</label></p>' + supplied + '</article>')
    script = '''<script>
const cases=Array.from(document.querySelectorAll('article[data-case]'));
const all=document.getElementById('show-all');
const counter=document.getElementById('case-counter');
let current=0;
function show(index){
 current=Math.max(0,Math.min(index,cases.length-1));
 cases.forEach((card,number)=>{card.hidden=!all.checked&&number!==current;});
 const graded=cases.filter(card=>card.querySelector('select[data-id]').value).length;
 counter.textContent=cases.length?`Caso ${current+1} di ${cases.length} · ${graded} valutati`:'Nessun caso';
 document.getElementById('previous').disabled=current===0;
 document.getElementById('next').disabled=current>=cases.length-1;
}
document.getElementById('previous').onclick=()=>show(current-1);
document.getElementById('next').onclick=()=>show(current+1);
document.getElementById('next-ungraded').onclick=()=>{
 const offset=cases.findIndex((_,step)=>{
  const index=(current+step+1)%cases.length;
  return !cases[index].querySelector('select[data-id]').value;
 });
 if(offset<0){counter.textContent='Tutti i casi sono valutati';return;}
 show((current+offset+1)%cases.length);
};
all.onchange=()=>show(current);
cases.forEach(card=>card.querySelector('select[data-id]').onchange=()=>show(current));
show(0);
document.getElementById('save').onclick=()=>{
 const reviewer=document.getElementById('reviewer').value.trim();
 if(!reviewer){alert('Inserisci il tuo nome o identificativo');return;}
 const judgments=cases.map(card=>({selection:card.querySelector('select[data-id]'),notes:card.querySelector('textarea')}))
  .filter(item=>item.selection.value)
  .map(item=>({question_id:item.selection.dataset.id,judgment:item.selection.value,notes:item.notes.value.trim()}));
 const data={schema_version:1,response_packet_sha256:RESPONSE_HASH,reviewer,reviewed_at:new Date().toISOString(),judgments};
 const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));link.download='answer-human-review.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
};</script>'''.replace('RESPONSE_HASH', json.dumps(digest))
    guide = '''<section><h2>Come valutare</h2>
<p>Leggi la domanda, la risposta e i passaggi forniti. Controlla se le fonti sostengono ogni affermazione importante.</p>
<p>INSUFFICIENT_EVIDENCE è un'astensione. È giustificata se i passaggi non permettono di rispondere; altrimenti scegli astensione ingiustificata. Se non puoi decidere, scegli “Non riesco a stabilirlo”.</p>
<p>Scrivi una breve motivazione e indica le fonti utili. Puoi lasciare un caso senza giudizio e tornarci dopo.</p></section>'''
    controls = ('<nav aria-label="Navigazione casi"><button id="previous" type="button">Precedente</button>'
                '<span id="case-counter" role="status" aria-live="polite"></span>'
                '<button id="next" type="button">Successivo</button>'
                '<button id="next-ungraded" type="button">Prossimo da valutare</button>'
                '<label><input id="show-all" type="checkbox">Mostra tutti</label></nav>')
    return ('<!doctype html><html lang="it"><head><meta charset="utf-8">'
            '<title>Valutazione umana delle risposte</title><style>'
            'body{max-width:1000px;margin:auto;background:#eee;font:16px system-ui;line-height:1.5}'
            'article,header,section{background:white;padding:2rem;margin:1rem 0}'
            'article[hidden]{display:none}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
            'textarea{width:100%;box-sizing:border-box}summary{cursor:pointer;overflow-wrap:anywhere}'
            'nav{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;padding:1rem;background:white}'
            '</style></head><body><header><h1>Risposte dell’AI: valutazione umana</h1>'
            '<p>Scelte e note restano qui finché la scheda è aperta. Scarica il file prima di chiudere. '
            'Sono esportati solo i casi con un giudizio selezionato.</p>'
            '<label>Nome o identificativo <input id="reviewer"></label>'
            '<button id="save" type="button">Scarica i giudizi</button></header>'
            + guide + controls + ''.join(cards) + script + '</body></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('corpus', 'run', 'responses', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    output, repo = args.output.resolve(), Path(__file__).resolve().parents[1]
    if output.exists() or output == repo or repo in output.parents:
        raise ValueError('Use a new external output file')
    raw = args.responses.read_bytes()
    response = json.loads(raw)
    if response['run_file_sha256'] != hashlib.sha256(args.run.read_bytes()).hexdigest():
        raise ValueError('Model evidence run differs from recorded hash')
    output.write_text(render(json.loads(args.corpus.read_bytes()), json.loads(args.run.read_bytes()),
                             response, hashlib.sha256(raw).hexdigest()), encoding='utf-8')


if __name__ == '__main__':
    main()
