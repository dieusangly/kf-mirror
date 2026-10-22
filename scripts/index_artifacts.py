"""Thin downstream import, migration and publication CLI."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.retrieval.config import corpora, load_config
from knowledge_foundry.retrieval.indexing import index_artifacts
from knowledge_foundry.storage.postgres import PostgresStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['migrate', 'import', 'publish', 'list'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--corpora-config', type=Path)
    parser.add_argument('--corpus')
    parser.add_argument('--run', help='configured saved run name')
    parser.add_argument('--artifact-path', type=Path)
    parser.add_argument('--preparation-dir', type=Path)
    parser.add_argument('--claims-dir', type=Path)
    parser.add_argument('--summaries-dir', type=Path)
    parser.add_argument('--build-id')
    parser.add_argument('--snapshot-id')
    parser.add_argument('--embed-missing', action='store_true', help='embed records without compatible saved vectors; may incur API costs')
    parser.add_argument('--fixture', type=Path)
    args = parser.parse_args(argv)
    store = PostgresStore()
    if args.command == 'migrate':
        store.migrate()
        result = {'migrations': 'applied'}
    elif args.command == 'list':
        result = store.snapshots(args.corpus)
    elif not args.corpus:
        parser.error('--corpus is required')
    elif args.command == 'publish':
        if not args.snapshot_id:
            parser.error('--snapshot-id is required for explicit publication')
        store.publish(args.corpus, args.snapshot_id)
        result = {'published': args.snapshot_id, 'corpus': args.corpus}
    else:
        selection = {k: getattr(args, k) for k in ('artifact_path', 'preparation_dir', 'claims_dir', 'summaries_dir', 'build_id') if getattr(args, k) is not None}
        if args.run:
            run = corpora(args.corpora_config)[args.corpus]['runs'][args.run]
            paths = {k: ROOT / run[k] for k in ('artifact_path', 'preparation_dir', 'claims_dir', 'summaries_dir') if run.get(k)}
            selection = {**paths, **selection}
        fixture = json.loads(args.fixture.read_text()) if args.fixture else None
        result = index_artifacts(store, args.corpus, load_config(args.config), embed_missing=args.embed_missing,
                                 fixture=fixture, **selection)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


if __name__ == '__main__':
    main()
