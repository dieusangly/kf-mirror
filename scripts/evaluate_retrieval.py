"""Compare retrieval methods/modes without generating answers or upstream data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.embeddings import atomic_json
from knowledge_foundry.retrieval.config import MODES, load_config
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
    parser.add_argument('--modes', nargs='+', choices=MODES, default=['passage'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sequence', action='store_true', help='run in one conversation; OpenAI mode may call the query rewriter')
    parser.add_argument('--check-excerpts', action='store_true', help='audit deterministic excerpts without answer-model calls')
    parser.add_argument('--trace', action='store_true', help='include candidate and evidence IDs, stage diagnostics and excerpt text')
    parser.add_argument('--no-rerank', action='store_true', help='compare search without model reranking')
    parser.add_argument('--workers', type=int, default=1, help='concurrent independent cases (default: 1)')
    parser.add_argument('--checkpoint', type=Path, help='checkpoint directory (default: OUTPUT with .checkpoint suffix)')
    parser.add_argument('--resume', action='store_true', help='reuse successful checkpointed cases and retry errors')
    parser.add_argument('--quiet', action='store_true', help='suppress progress on stderr')
    args = parser.parse_args(argv)
    if args.unpublished and not args.snapshot_id:
        parser.error('--unpublished requires --snapshot-id')
    if args.workers < 1:
        parser.error('--workers must be positive')
    if args.sequence and args.workers != 1:
        parser.error('--sequence requires --workers 1')
    checkpoint = args.checkpoint or args.output.with_suffix('.checkpoint')
    if checkpoint.resolve() == args.output.resolve():
        parser.error('--checkpoint must differ from --output')
    config = load_config(args.config)
    if args.no_rerank:
        config['search']['rerank'] = False
    store = PostgresStore()
    if args.unpublished:
        store = SnapshotEvaluationStore(store, args.corpus, args.snapshot_id)

    def progress(done, total, row):
        status = ('resumed' if done else 'starting') if row is None else (
            f"{row['mode']}/{row['method']} {row['id']}: " + ('error' if 'error' in row else 'ok'))
        print(f'[{done}/{total}] {status}', file=sys.stderr, flush=True)

    report = evaluate(DocumentAssistant(store, config), args.corpus,
                      json.loads(args.cases.read_text()), methods=args.methods, modes=args.modes,
                      snapshot_id=args.snapshot_id, sequential=args.sequence, check_excerpts=args.check_excerpts,
                      trace=args.trace, workers=args.workers, checkpoint_path=checkpoint,
                      resume=args.resume, on_progress=None if args.quiet else progress)
    atomic_json(args.output, report, indent=2)
    print(json.dumps(report['metrics'], indent=2))
    return report


if __name__ == '__main__':
    report = main()
    raise SystemExit(1 if any('error' in row for row in report['rows']) else 0)
