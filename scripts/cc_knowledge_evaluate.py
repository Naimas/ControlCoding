"""Score frozen knowledge evaluation packets without opening project memory."""
import argparse
import json
from pathlib import Path
import sys

from cc_memory_lib.knowledge_evaluation import evaluate


def read_packet(path):
    with Path(path).open('rb') as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Evaluation packet exceeds 8 MiB')

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON number: ' + value)

    return json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs,
                      parse_constant=invalid_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True, help='Frozen labeled questions JSON')
    parser.add_argument('--run', required=True, help='Recorded results JSON')
    parser.add_argument('--grades', help='Citation judgments bound to the exact run')
    args = parser.parse_args(argv)
    try:
        report = evaluate(read_packet(args.corpus), read_packet(args.run),
                          read_packet(args.grades) if args.grades else None)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report['quality_thresholds_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
