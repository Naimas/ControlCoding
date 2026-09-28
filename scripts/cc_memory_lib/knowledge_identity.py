"""Keep path bindings stable; infer only unambiguous content-preserving moves."""
from collections import Counter, defaultdict
from .knowledge_sources import digest
from .knowledge_store import KnowledgeError


def bind(sources, old):
    present = {s['path'] for s in sources}
    by_path = {r['path']: r for r in old.values()}
    revisions = Counter(s['revision'] for s in sources)
    moved = defaultdict(list)
    for row in old.values():
        if not row['deleted'] and row['path'] not in present:
            moved[(row['kind'], row['revision'])].append(row['id'])
    assigned, pending = set(), []
    for source in sources:
        previous = by_path.get(source['path'])
        if previous:
            source['id'] = previous['id']
            assigned.add(source['id'])
        else:
            pending.append(source)
    reserved = set(old) | {s['id'] for s in sources}
    for source in pending:
        candidates = moved[(source['kind'], source['revision'])]
        if len(candidates) == 1 and revisions[source['revision']] == 1 and candidates[0] not in assigned:
            source['id'] = candidates[0]
        elif source['id'] in old or source['id'] in assigned:
            # A new file at the old pathname must not take the moved file's ID.
            if not source['id'].startswith('source:'):
                raise KnowledgeError('source_identity_conflict')
            number = 0
            while True:
                candidate = 'source:' + digest('source-instance\0' + source['path'] + '\0' + source['revision'] + '\0' + str(number))
                if candidate not in reserved and candidate not in assigned:
                    source['id'] = candidate
                    break
                number += 1
        assigned.add(source['id'])
    if len(assigned) != len(sources):
        raise KnowledgeError('source_identity_conflict')
