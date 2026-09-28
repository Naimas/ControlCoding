"""Explicit local-model acceptance corpus; never run by offline unit tests.

Usage: python -B tests/knowledge_ollama_evaluation.py EXTERNAL_FIXTURE_DIRECTORY
The directory must be new. This performs loopback-only Ollama inference.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service

CORPUS = {
    'access': '# Identity gateway\nA visitor proves their identity using a secret passphrase. The gateway issues a short-lived signed credential. It expires after fifteen minutes.',
    'backups': '# Recovery operations\nA nightly copy of the database is stored on a separate disk. Operators restore that copy after accidental deletion or storage failure.',
    'messages': '# Notification delivery\nOutgoing correspondence is placed in a durable queue. A worker retries delivery after temporary mail server failures.',
    'billing': '# Subscription ledger\nA recurring charge is calculated every month. An invoice lists the selected plan and the amount owed.',
    'images': '# Media pipeline\nUploaded photographs are resized into small previews. The original picture remains in object storage.',
}
QUERIES = [('Come posso entrare nel mio account?', 'access'),
           ('How do we recover lost records?', 'backups'),
           ('Che succede se non arriva una email?', 'messages'),
           ('Where is the customer monthly payment recorded?', 'billing'),
           ('Come vengono create le miniature delle foto?', 'images')]


def main():
    root = Path(sys.argv[1]).absolute()
    repository = Path(__file__).resolve().parents[1]
    if root == repository or repository in root.parents or root.exists():
        raise SystemExit('Choose a new external fixture directory')
    (root / 'docs').mkdir(parents=True)
    for name, text in CORPUS.items():
        (root / 'docs' / (name + '.md')).write_text(text, encoding='utf-8')
    service.configure(root, {**service.DEFAULT, 'scopes': ['project'], 'automatic': False})
    service.reconcile(root)
    state = service.index(root)
    results = []
    for question, expected in QUERIES:
        result = service.query(root, question, True)
        paths = [c['path'] for c in result['citations']]
        results.append({'question': question, 'expected': 'docs/' + expected + '.md',
                        'first': paths[0] if paths else None, 'mode': result['mode'], 'warning': result['warning'],
                        'passed': bool(paths) and paths[0] == 'docs/' + expected + '.md'})
    output = {'model': state['embedding_identity'], 'top1': sum(r['passed'] for r in results),
              'total': len(results), 'results': results, 'limits': 'Small authored corpus; not a general retrieval quality guarantee.'}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(r['passed'] and r['warning'] is None for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
