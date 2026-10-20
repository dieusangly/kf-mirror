"""Query a published snapshot using the same service as the chatbot."""
import argparse
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.retrieval.evaluation import audit_answer
from knowledge_foundry.retrieval.service import DocumentAssistant
from knowledge_foundry.storage.postgres import PostgresStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question')
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--snapshot-id')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--method', choices=['keyword', 'vector', 'combined'])
    parser.add_argument('--mode', choices=['passage', 'claim', 'summary'])
    parser.add_argument('--source-id', action='append')
    parser.add_argument('--document-id', action='append')
    parser.add_argument('--retrieve-only', action='store_true')
    parser.add_argument('--session-id', help='resume with KF_CHAT_TOKEN from the environment')
    parser.add_argument('--provider', choices=['extractive', 'openai'])
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.provider:
        config['answer']['provider'] = args.provider
    store = PostgresStore()
    assistant = DocumentAssistant(store, config)
    options = {'snapshot_id': args.snapshot_id, 'method': args.method, 'mode': args.mode,
               'source_ids': args.source_id, 'document_ids': args.document_id}
    if args.retrieve_only:
        result = assistant.retrieve(args.corpus, args.question, **options)
        result.pop('payload')
    else:
        import os
        token = os.environ.get('KF_CHAT_TOKEN') or secrets.token_urlsafe(32)
        if args.session_id and not os.environ.get('KF_CHAT_TOKEN'):
            parser.error('set KF_CHAT_TOKEN to resume a session')
        sid = args.session_id or store.new_session(store.snapshot(args.corpus, args.snapshot_id)['id'], token)
        result = assistant.ask(args.corpus, args.question, token=token, session_id=sid, **options)
        result['session_id'] = sid
        result['citation_evaluation'] = audit_answer(result, store)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


if __name__ == '__main__':
    main()
