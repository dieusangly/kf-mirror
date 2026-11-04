"""Build a reviewed reasoning snapshot; no model calls or database writes."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--jurisdiction', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory; existing runs are preserved')
    parser.add_argument('--checker', help='path to oo-horn from the pinned upstream source')
    parser.add_argument('--goal', nargs=3, metavar=('SUBJECT', 'PREDICATE', 'OBJECT'))
    parser.add_argument('--sparql', help='local read-only query over asserted + inferred triples')
    args = parser.parse_args(argv)
    try:
        from knowledge_foundry.reasoning.engine import ReasoningEngine
        from jsonschema import ValidationError
    except ImportError as exc:
        parser.exit(2, f'install requirements.txt first: {exc}\n')
    try:
        engine = ReasoningEngine.from_files(args.bundle, args.mapping, jurisdiction=args.jurisdiction, as_of=args.as_of)
        report = engine.run(args.output, checker=args.checker)
        output = {'status': report['status'], 'output': str(args.output), 'report': report}
        if args.goal:
            output['answer'] = engine.explain(args.goal)
            (args.output / 'answer.json').write_text(json.dumps(output['answer'], ensure_ascii=False, indent=2) + '\n')
        if args.sparql:
            output['query'] = engine.query(args.sparql)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'ready' and output.get('answer', {}).get('status', 'inferred') in {'asserted', 'inferred'} else 1
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, ValidationError) as exc:
        parser.exit(2, f'reasoning failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
