"""Build structural chunks and semantic clusters, saving embeddings and provenance."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from knowledge_foundry.claim_context import prepare_claim_context  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chunked-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/claim-generation-and-classification.json')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--fixture', type=Path, help='offline embedding vectors keyed by exact-text hash')
    parser.add_argument('--parsed-dir', type=Path, help='matching saved parsing run for legacy chunks without document-structure.json')
    args = parser.parse_args(argv)
    try:
        report = prepare_claim_context(args.chunked_dir, args.output_dir, args.config, args.cache_dir, args.fixture,
                                       on_progress=lambda event: print(json.dumps(event), file=sys.stderr, flush=True),
                                       parsed_dir=args.parsed_dir)
    except Exception as exc:
        parser.exit(2, f'structural chunks and semantic clusters build failed: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
