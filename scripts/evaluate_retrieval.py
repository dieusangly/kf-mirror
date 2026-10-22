"""Compare retrieval methods/modes without generating answers or upstream data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.retrieval.evaluation import SnapshotEvaluationStore, evaluate
from knowledge_foundry.retrieval.service import DocumentAssistant
from knowledge_foundry.storage.postgres import PostgresStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--snapshot-id')
    parser.add_argument('--unpublished', action='store_true', help='evaluate one explicit ready snapshot without publishing it')
    parser.add_argument('--methods', nargs='+', choices=['keyword', 'vector', 'combined'], default=['keyword', 'vector', 'combined'])
    parser.add_argument('--modes', nargs='+', choices=['passage', 'claim', 'summary'], default=['passage'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sequence', action='store_true', help='run in one conversation; OpenAI mode may call the query rewriter')
    parser.add_argument('--check-excerpts', action='store_true', help='audit deterministic excerpts without answer-model calls')
    parser.add_argument('--no-rerank', action='store_true', help='compare search without model reranking')
    args = parser.parse_args(argv)
    if args.unpublished and not args.snapshot_id:
        parser.error('--unpublished requires --snapshot-id')
    config = load_config(args.config)
    if args.no_rerank:
        config['search']['rerank'] = False
    store = PostgresStore()
    if args.unpublished:
        store = SnapshotEvaluationStore(store, args.corpus, args.snapshot_id)
    report = evaluate(DocumentAssistant(store, config), args.corpus,
                      json.loads(args.cases.read_text()), methods=args.methods, modes=args.modes,
                      snapshot_id=args.snapshot_id, sequential=args.sequence, check_excerpts=args.check_excerpts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['metrics'], indent=2))
    return report


if __name__ == '__main__':
    report = main()
    raise SystemExit(1 if any('error' in row for row in report['rows']) else 0)
