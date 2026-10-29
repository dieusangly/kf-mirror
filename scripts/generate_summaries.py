"""Generate recursive summaries directly from frozen semantic clusters."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from knowledge_foundry.summaries import generate_summaries  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/summaries.json')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--fixture', type=Path, help='offline summaries and embedding_vectors fixture')
    parser.add_argument('--workers', type=int, default=4,
                        help='maximum simultaneous model requests (default: 4; use 1 for serial execution)')
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error('--workers must be a positive integer')
    try:
        report = generate_summaries(args.context_dir, args.output_dir, args.config, args.cache_dir,
                                    args.fixture, workers=args.workers, on_progress=lambda event: print(
                                        json.dumps(event), file=sys.stderr, flush=True))
    except Exception as exc:
        parser.exit(2, f'summary generation failed: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
