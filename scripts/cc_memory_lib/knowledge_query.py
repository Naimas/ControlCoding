"""Stream ranking inputs; hydrate only selected current evidence passages."""
from .knowledge_store import database, get, KnowledgeError
from .knowledge_sources import physical_source_path
from .knowledge_semantic import OllamaEmbedding
from .knowledge_work import snapshot as work_snapshot, read_db as work_view
from .knowledge_vector_rank import scores as vector_scores
from .knowledge_ranking import bm25_ranks, tokenize

CURRENT = ' FROM chunks c JOIN sources s ON c.source=s.id WHERE s.deleted=0 AND s.revision=c.revision'
STOPWORDS = set(('a an and are as at be by for from has how i in is it of on or that the this to was we '
                 'what when where which who why with al alla alle che chi come con da dal dei del '
                 'della delle di e gli ha i il in la le lo nel non o per posso se si sono su un una '
                 'uno vengono').split())


def check_snapshot(db, snapshot):
    from .knowledge_service import initialized, require
    initialized(db)
    require(not get(db, 'needs_reconcile', True) and
            (get(db, 'generation', 0), get(db, 'policy'), get(db, 'embedding_identity'), work_snapshot(db)) == snapshot,
            'query_context_changed')


def query(root, text, semantic=True, adapter=None, retrieval=None):
    from .knowledge_service import initialized, require
    from .knowledge_retrieval import options, allowed, expand, history
    require(type(text) is str and 0 < len(text.strip()) <= 500 and type(semantic) is bool)
    terms = set(tokenize(text)) - STOPWORDS
    routing = options(retrieval, text)
    with database(root) as db:
        initialized(db)
        require(not get(db, 'needs_reconcile', True), 'reconcile_required')
        config = get(db, 'policy')
        pinned = get(db, 'embedding_identity')
        generation = get(db, 'generation', 0)
        observed_at = get(db, 'last_reconcile')
        snapshot = generation, config, pinned, work_snapshot(db)
        if routing['intent'] == 'history':
            citations, truncated = history(db, text, routing)
            return {'query': text, 'mode': 'recorded history', 'warning': 'Recorded versions only; current paths are locators, not proof of past project membership. Historical evidence is not current AI context.',
                    'generation': generation, 'work_revision': snapshot[3], 'observed_at': observed_at,
                    'answer': 'Recorded original passages before ' + routing['before'], 'citations': citations,
                    'edges': [], 'historical': True, 'retrieval': {'intent': 'history', 'options': routing, 'truncated': truncated},
                    'abstained': not bool(citations), 'answerability': 'not_assessed',
                    'retrieval_status': 'candidates' if citations else 'no_matches', 'selection_policy': 'Latest retained version per available source before cutoff; ten original lines.'}
        eligible = {r['id'] for r in db.execute('SELECT * FROM sources') if allowed(r, routing)}
        # BM25 retains only query counts and length for matched passages.
        chunk_sources = dict(db.execute('SELECT c.id,c.source' + CURRENT))
        lexical = bm25_ranks((r for r in db.execute('SELECT c.id,c.text,s.title' + CURRENT)
                             if chunk_sources[r[0]] in eligible), terms)
        lexical_priority = lexical[:3]
        ranks = {identifier: 1 / (60 + i) for i, identifier in enumerate(lexical, 1)}
        del lexical

    mode, warning, vector, identity = 'lexical BM25', None, None, None
    if semantic:
        try:
            require(bool(config['embedding']), 'embeddings_disabled')
            adapter = adapter or OllamaEmbedding(config['embedding'])
            identity = adapter.pin()
            require(identity == pinned, 'embedding_index_not_current')
            vector = adapter.embed([text])[0]
            require(adapter.pin() == identity, 'embedding_model_changed')
        except KnowledgeError as exc:
            vector = None
            warning = str(exc) + '; lexical fallback is active.'

    # Inference may have allowed a new generation or policy: check before scoring
    # or hydrating anything. This lease also guards graph and final evidence reads.
    semantic_priority = []
    with database(root) as db:
        check_snapshot(db, snapshot)
        if vector is not None:
            try:
                similarities = []
                total = db.execute('SELECT count(*)' + CURRENT).fetchone()[0]
                count = 0
                for identifier, score in vector_scores(db.execute('SELECT c.id,c.vector' + CURRENT + ' AND c.vector IS NOT NULL AND c.model=?', (identity,)), vector):
                    count += 1
                    if score >= .3 and chunk_sources[identifier] in eligible:
                        similarities.append((score, identifier))
                similarities.sort(key=lambda item: (-item[0], item[1]))
                if count == total:
                    semantic_priority = [identifier for _, identifier in similarities[:3]]
                    for i, (_, identifier) in enumerate(similarities, 1):
                        ranks[identifier] = ranks.get(identifier, 0) + 1 / (60 + i)
                    mode = 'neural embeddings + lexical BM25'
                else:
                    warning = 'Semantic index is incomplete; lexical search still covers all current passages.'
                del similarities
            except KnowledgeError as exc:
                warning = str(exc) + '; lexical fallback is active.'

        ordered = sorted(ranks, key=lambda identifier: (-ranks[identifier], identifier))
        extensions, trace = expand(db, root, text, list(dict.fromkeys(chunk_sources[i] for i in ordered[:6])), routing)
        original_order = list(ordered)
        for i, item in enumerate(extensions):
            ranks[item['id']] = ranks.get(item['id'], 0) + 1 / (100 + i)
        ordered = sorted(ranks, key=lambda identifier: (-ranks[identifier], identifier))
        reserved = set(lexical_priority + semantic_priority)
        # Keep core signals while guaranteeing room for two graph/wiki discoveries.
        reserved.update(item['id'] for item in extensions[:2])
        for identifier in ordered:
            if len(reserved) >= 10:
                break
            reserved.add(identifier)
        identifiers = [identifier for identifier in ordered if identifier in reserved][:10]
        trace['options'] = routing
        trace['selected'] = [{'passage': identifier, 'source': chunk_sources[identifier],
                              'signal': next((e['signal'] for e in extensions if e['id'] == identifier), 'lexical_or_semantic'),
                              'path': trace['paths'].get(chunk_sources[identifier], [])} for identifier in identifiers]
        trace['excluded'] += [{'passage': identifier, 'reason': 'passage_budget'} for identifier in original_order if identifier not in identifiers][:10]
        selected = [dict(db.execute('SELECT c.id,c.source,c.revision,c.line,c.end_line,c.text,s.path,s.title,s.kind' +
                                   CURRENT + ' AND c.id=?', (identifier,)).fetchone()) for identifier in identifiers]
        sources = {row['source'] for row in selected}
        edges = []
        # Stream graph metadata as well; retain only the returned edge budget.
        for row in db.execute('SELECT * FROM edges ORDER BY source,target,kind'):
            if row['source'] in eligible and row['target'] in eligible and (row['source'] in sources or row['target'] in sources):
                if len(edges) < 40:
                    edges.append(dict(row))
        for row in work_view(db, root)['relations']:
            if row['effective'] and row['source'] in eligible and row['target'] in eligible and (row['source'] in sources or row['target'] in sources):
                if len(edges) < 40:
                    edges.append({'source': row['source'], 'target': row['target'],
                                  'kind': 'human_reviewed:'+row['kind'], 'revision': row['source_revision']})

    citations = [{'id': 'S' + str(i), 'source': r['source'], 'path': r['path'],
                  'physicalPath': physical_source_path(root, r['path'], r['kind']), 'title': r['title'],
                  'revision': r['revision'], 'line': r['line'], 'end_line': r['end_line'],
                  'excerpt': r['text'], 'kind': r['kind']} for i, r in enumerate(selected, 1)]
    answer = ('No matching passage found in the indexed scope; whether an answer exists has not been assessed.' if not citations else
              'Retrieved passages; whether they answer the question has not been assessed.\n\n' + '\n\n'.join(
                  '[' + c['id'] + '] ' + c['title'] + '\n' + c['excerpt'] for c in citations))
    return {'query': text, 'mode': mode, 'warning': warning, 'generation': generation, 'work_revision': snapshot[3],
            'observed_at': observed_at, 'answer': answer, 'citations': citations, 'edges': edges, 'retrieval': trace,
            'retrieval_status': 'candidates' if citations else 'no_matches', 'answerability': 'not_assessed',
            'abstained': not bool(citations),
            'selection_policy': 'Top 10 original passages, reserving up to 3 from each signal and up to 2 graph/wiki discoveries; bounded two-hop traversal and current wiki anchors guide retrieval.'}
