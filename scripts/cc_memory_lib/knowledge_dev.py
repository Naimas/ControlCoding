"""Read-only projection from the existing canonical Dev SQLite engine."""
import json
from pathlib import Path
from .knowledge_sources import digest
from .knowledge_store import KnowledgeError
from .store import _readonly_memory_connection


def capture_dev(root):
    from .knowledge_work_controls import capture as capture_controls
    control_sources, control_edges = capture_controls(root)
    path = Path(root) / '.controlcoding/memory/memory.db'
    if not path.exists() and not path.is_symlink():
        return control_sources, control_edges
    with _readonly_memory_connection(Path(root)) as db:
        rows = db.execute('SELECT id,type,title,path,lifecycle,body,source_refs,provenance FROM entities ORDER BY id LIMIT 129').fetchall()
        if len(rows) > 128:
            raise KnowledgeError('dev_record_budget')
        sources, identifiers = list(control_sources), {}
        for row in rows:
            # Preserve lifecycle and upstream provenance; a projection is never
            # promoted to current evidence just because it was indexed here.
            data = dict(row)
            body = '# ' + str(data['title']) + '\n\nCanonical Dev record (observed, not recertified):\n\n```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + '\n```'
            if len(body.encode()) > 262144:
                raise KnowledgeError('dev_record_budget')
            identifier = 'dev:' + digest(str(data['id']))
            identifiers[data['id']] = identifier
            sources.append({'id': identifier, 'path': 'dev-memory/' + str(data['id']), 'title': str(data['title'])[:180],
                            'body': body, 'revision': digest(body), 'kind': 'dev-record:' + str(data['lifecycle'])})
        edges = list(control_edges)
        for row in db.execute('SELECT source_id,target_id,type FROM edges ORDER BY id LIMIT 2001'):
            if len(edges) >= 2000:
                raise KnowledgeError('dev_edge_budget')
            if row['source_id'] in identifiers and row['target_id'] in identifiers:
                edges.append({'source': identifiers[row['source_id']], 'target': identifiers[row['target_id']],
                              'kind': 'recorded_dev:' + row['type']})
        return sources, edges
