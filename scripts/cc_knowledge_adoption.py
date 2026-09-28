"""Observe embedded knowledge adoption without modifying the project."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from cc_memory_lib.knowledge_adoption import observe, summarize


def _read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('receipt exceeds 1 MiB')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON number')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    one = actions.add_parser('observe')
    one.add_argument('--project', required=True)
    one.add_argument('--kind', required=True, choices=('software', 'documents'))
    one.add_argument('--note', required=True)
    many = actions.add_parser('summarize')
    many.add_argument('receipts', nargs='+')
    args = parser.parse_args(argv)
    try:
        result = observe(args.project, args.kind, args.note) if args.action == 'observe' else summarize([_read(path) for path in args.receipts])
    except (OSError, sqlite3.Error, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
