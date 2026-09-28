"""Synthetic retrieval behavior; no production or held-out corpus is read."""
from pathlib import Path
import sys
from collections import Counter
import math
import random
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_ranking import bm25_ranks, tokenize
from cc_memory_lib.knowledge_semantic import encode_vector
from cc_memory_lib.knowledge_store import database, put


def seed(root, passages, edges=(), vectors=None, titles=None):
    service.configure(root, {**service.DEFAULT, 'scopes': ['project']})
    with database(root) as db, db:
        for identifier, body in passages.items():
            db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                       (identifier, f'docs/{identifier}.md', (titles or {}).get(identifier, identifier), 'rev', body,
                        'document', 0, 'now'))
            vector = encode_vector(vectors[identifier]) if vectors else None
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                       (identifier, identifier, 'rev', 1, 1, body, vector,
                        'fixture@one' if vector else None))
        for source, target in edges:
            db.execute('INSERT INTO edges VALUES(?,?,?,?)',
                       (source, target, 'rev', 'explicit_markdown_reference'))
        put(db, 'needs_reconcile', False)
        put(db, 'generation', 1)
        if vectors:
            put(db, 'embedding_identity', 'fixture@one')


def paths(result):
    return [citation['path'] for citation in result['citations']]


def test_rare_term_outranks_repeated_generic_term(tmp_path):
    passages = {'rare': 'rare generic', 'repeated': 'generic ' * 20}
    passages.update({f'generic{i}': 'generic' for i in range(10)})
    seed(tmp_path, passages)
    result = service.query(tmp_path, 'rare generic', False)
    assert result['citations'][0]['source'] == 'rare'
    assert len(result['citations']) == 10
    assert result['retrieval_status'] == 'candidates'
    assert result['answerability'] == 'not_assessed'
    assert not result['abstained']
    assert result['answer'].startswith('Retrieved passages; whether they answer the question has not been assessed.')


def test_bm25_normalizes_length_and_breaks_equal_scores_by_id(tmp_path):
    seed(tmp_path, {'short': 'needle', 'long': 'needle ' + 'padding ' * 30})
    assert paths(service.query(tmp_path, 'needle', False)) == ['docs/short.md', 'docs/long.md']

    tie_root = tmp_path / 'ties'
    tie_root.mkdir()
    seed(tie_root, {'z': 'needle', 'a': 'needle', 'm': 'needle'})
    assert paths(service.query(tie_root, 'needle', False)) == [
        'docs/a.md', 'docs/m.md', 'docs/z.md']


def test_graph_neighbor_becomes_original_retrieval_candidate(tmp_path):
    seed(tmp_path, {'hit': 'needle', 'neighbor': 'unrelated'}, [('hit', 'neighbor')])
    result = service.query(tmp_path, 'needle', False)
    assert paths(result) == ['docs/hit.md', 'docs/neighbor.md']
    assert result['edges'][0]['target'] == 'neighbor'


def test_semantic_synonym_survives_lexical_absence(tmp_path):
    seed(tmp_path, {'synonym': 'passphrase', 'other': 'unrelated'},
         vectors={'synonym': [1., 0.], 'other': [0., 1.]})

    class Embedding:
        def pin(self):
            return 'fixture@one'

        def embed(self, texts):
            return [[1., 0.]]

    result = service.query(tmp_path, 'login', True, Embedding())
    assert paths(result) == ['docs/synonym.md']
    assert result['mode'].startswith('neural')


def test_divergent_lexical_and_semantic_ranks_retain_each_signal(tmp_path):
    passages = {f'exact{i}': 'needle' for i in range(3)}
    passages.update({f'generic{i:02d}': 'common' for i in range(12)})
    vectors = {identifier: ([0., 1.] if identifier.startswith('exact') else [1., 0.])
               for identifier in passages}
    seed(tmp_path, passages, vectors=vectors)

    class Embedding:
        def pin(self):
            return 'fixture@one'

        def embed(self, texts):
            return [[1., 0.]]

    result = service.query(tmp_path, 'needle common', True, Embedding())
    selected = [citation['source'] for citation in result['citations']]
    assert len(selected) == 10
    assert {'exact0', 'exact1', 'exact2'} <= set(selected)
    assert {'generic00', 'generic01', 'generic02'} <= set(selected)
    assert selected[:3] == ['generic00', 'generic01', 'generic02']
    assert 'reserving up to 3 from each signal' in result['selection_policy']


def test_incomplete_semantic_index_keeps_exact_lexical_selection(tmp_path):
    seed(tmp_path, {'lexical': 'needle', 'semantic': 'unrelated'},
         vectors={'lexical': [0., 1.], 'semantic': [1., 0.]})
    with database(tmp_path) as db, db:
        db.execute("UPDATE chunks SET vector=NULL,model=NULL WHERE id='lexical'")

    class Embedding:
        def pin(self):
            return 'fixture@one'

        def embed(self, texts):
            return [[1., 0.]]

    result = service.query(tmp_path, 'needle', True, Embedding())
    assert paths(result) == ['docs/lexical.md']
    assert result['mode'] == 'lexical BM25'
    assert 'incomplete' in result['warning']


def test_no_matches_does_not_claim_answerability(tmp_path):
    seed(tmp_path, {'other': 'unrelated'})
    result = service.query(tmp_path, 'needle', False)
    assert result['citations'] == []
    assert result['retrieval_status'] == 'no_matches'
    assert result['answerability'] == 'not_assessed'
    assert result['abstained'] is True
    assert 'whether an answer exists has not been assessed' in result['answer']


def test_identifier_parts_match_natural_language_without_substring_matches(tmp_path):
    seed(tmp_path, {'receipt': 'The verification result is passed_subset.',
                    'near': 'The verification result is passedsubsetted.'})
    assert tokenize('PASSed_subset') == ['passed', 'subset']
    assert paths(service.query(tmp_path, 'passed subset', False)) == ['docs/receipt.md']
    assert paths(service.query(tmp_path, 'passed_subset', False)) == ['docs/receipt.md']
    assert paths(service.query(tmp_path, 'passedsubsetted', False)) == ['docs/near.md']
    assert service.query(tmp_path, 'pass', False)['citations'] == []


def test_source_title_rescues_subject_omitted_by_short_passage(tmp_path):
    seed(tmp_path, {'relevant': 'Expires after seven days.',
                    'other': 'Expires after seven days.'},
         titles={'relevant': 'Receipt validity', 'other': 'General notes'})
    result = service.query(tmp_path, 'receipt validity expires', False)
    assert paths(result) == ['docs/relevant.md', 'docs/other.md']
    assert result['citations'][0]['excerpt'] == 'Expires after seven days.'


def test_title_only_match_remains_unassessed_candidate(tmp_path):
    seed(tmp_path, {'boilerplate': 'The team meets every Tuesday.'},
         titles={'boilerplate': 'Refund policy'})
    result = service.query(tmp_path, 'refund policy', False)
    assert paths(result) == ['docs/boilerplate.md']
    assert result['answerability'] == 'not_assessed'
    assert result['retrieval_status'] == 'candidates'
    assert 'whether they answer the question has not been assessed' in result['answer']


def test_title_budget_and_rows_without_title():
    rows = [{'id': 'body', 'text': 'needle'},
            {'id': 'late', 'text': 'unrelated', 'title': 'x' * 180 + ' needle'}]
    assert bm25_ranks(rows, {'needle'}) == ['body']


def test_bm25_count_path_matches_tokenwise_numeric_oracle():
    rng = random.Random(20260928)
    words = ['needle', 'rare', 'common', 'über', 'other', 'absent']
    rows = [{'id': f'{number:03}',
             'text': ' '.join(rng.choice(words) for _ in range(rng.randrange(2, 45))),
             'title': ' '.join(rng.choice(words) for _ in range(rng.randrange(0, 12)))}
            for number in range(80)]
    rows.append({'id': 'no-title', 'text': 'rare needle'})

    def oracle(terms):
        query = frozenset(terms)
        documents = []
        frequency = Counter()
        for row in rows:
            counts = Counter()
            length = 0
            for token in tokenize(row['text']):
                length += 1
                if token in query:
                    counts[token] += 1
            for token in tokenize(row.get('title', '')[:180]):
                length += 2
                if token in query:
                    counts[token] += 2
            frequency.update(counts.keys())
            documents.append((row['id'], counts, length))
        average = sum(length for _, _, length in documents) / len(documents)
        scored = []
        for identifier, counts, length in documents:
            if not counts:
                continue
            normalization = 1.2 * (1 - .75 + .75 * length / average)
            score = sum(math.log1p((len(documents) - frequency[term] + .5) /
                                   (frequency[term] + .5)) * count * 2.2 /
                        (count + normalization) for term, count in counts.items())
            scored.append((score, identifier))
        return [identifier for _, identifier in sorted(scored,
                                                       key=lambda item: (-item[0], item[1]))]

    for terms in ({'needle'}, {'rare', 'common'}, {'über', 'absent'},
                  {'needle', 'rare', 'common', 'über'},
                  {'needle', 'rare', 'common', 'über', 'other', 'absent', 'never'}):
        assert bm25_ranks(iter(rows), terms) == oracle(terms)


def test_tokenizer_preserves_word_boundaries_casefold_and_unicode():
    oracle = re.compile(r'[^\W_]{2,}')
    rng = random.Random(20260928)
    examples = ['A_B_Cat12 12a 1 x', 'HTTP://example.org/X?a=ALPHA',
                'Straße İstanbul CAFÉ 日本語 ∑ λ 漢字', '\x00alpha\x7fBeta\r\n99',
                ''.join(chr(n) for n in range(128))]
    examples.extend(''.join(chr(rng.randrange(128)) for _ in range(300)) for _ in range(300))
    for value in examples:
        assert tokenize(value) == oracle.findall(value.casefold())
