"""Read-only explanations and source cards; never rewrite retained wiki bodies."""
from pathlib import PurePosixPath

from .knowledge_sources import physical_source_path
from .knowledge_wiki import classify, freshness
from .knowledge_document_types import TYPES, result as type_result
from .knowledge_document_review import read_type

PURPOSES = {
    'sessions': 'Conversations and handoffs help you recover the context of earlier work.',
    'decisions': 'These documents record choices and the reasons behind them.',
    'plans': 'These documents describe planned work, requirements and milestones. A plan does not prove that the work is complete.',
    'verification': 'These documents describe checks or record results. Read the original to distinguish a test plan from an executed test.',
    'architecture': 'These documents describe how the system is structured and how its components communicate. A contract states the expected behavior; it does not by itself prove that the code implements it.',
    'operations': 'These documents explain setup, adoption, releases or everyday operation.',
    'knowledge': 'These documents explain project memory, documentation and knowledge workflows.',
    'reference': 'These are general project documents that did not match a more specific topic.',
}


def presentation(root, db, page):
    identifier = page['id']
    topic = identifier.removeprefix('wiki:topic:') if identifier.startswith('wiki:topic:') else None
    kind = ('topic' if topic else 'review' if identifier.startswith('wiki:review:')
            else 'draft' if identifier.startswith('wiki:draft:') else 'source')
    purpose = PURPOSES.get(topic, {
        'review': 'This page combines an automatic source index with any explicitly accepted human-reviewed sections. Their evidence can become outdated.',
        'draft': 'This is a saved AI synthesis. Check its cited documents before relying on its claims; saving a draft does not approve it.',
        'source': 'This page is an indexed copy or extracted text. Open the original Markdown document to read its complete formatting and local images.',
    }.get(kind, 'Related source documents for this topic.'))
    cards = []
    for dep in page['dependencies']:
        # One page may cite both a current passage and a retained reviewed
        # passage from an older revision of the same source.
        issues = freshness(db, [dep])
        row = db.execute('SELECT id,path,title,revision,kind,deleted FROM sources WHERE id=?',
                         (dep['source'],)).fetchone()
        matching = row is not None and not row['deleted'] and row['revision'] == dep['revision'] and row['path'] == dep['path']
        physical = physical_source_path(root, dep['path'], row['kind']) if row else ''
        markdown = dep['source'].startswith('source:') and dep['path'].lower().endswith('.md') and bool(physical)
        cards.append({**dep, 'id': dep['source'],
                      'title': row['title'] if matching else PurePosixPath(dep['path']).name,
                      'kind': row['kind'] if matching else 'source',
                      'physicalPath': physical, 'readable': bool(markdown),
                      'document_type': read_type(db, dict(row)) if matching else type_result('unclassified', 'uncertain', 'The original metadata for this cited revision is not available. The current title cannot classify an older version.'),
                      'availability': issues[0]['code'] if issues else 'current',
                      'reason': classify(dict(row))[2] if matching and topic else ''})
    return {'kind': kind, 'purpose': purpose, 'sources': cards,
            'document_types': [dict(key=key, label=label) for key, (label, _) in TYPES.items()]}
