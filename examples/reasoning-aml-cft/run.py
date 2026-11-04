"""Offline end-to-end demonstration using the real assistant and a memory store.

The memory store substitutes only PostgreSQL persistence/search; the Haystack
pipeline, evidence admission, symbolic engine and certificate checker are real.
No LLM, embedding API or retraining is used. This is not a PostgreSQL benchmark.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from knowledge_foundry.reasoning.engine import ReasoningEngine
from knowledge_foundry.retrieval.artifacts import project
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.retrieval.service import DocumentAssistant


class ExampleStore:
    def __init__(self, bundle):
        structural = [{'id': 'passage:' + c['id'], 'text': c['text'], 'original_chunk_ids': [c['id']],
                       'evidence_memberships': [{'reference': r, 'original_chunk_ids': [c['id']]} for r in c['evidence']]}
                      for c in bundle['chunks']]
        self.records, evidence = project({'bundle': bundle, 'structural': structural,
                                          'claims': bundle['claims'], 'summaries': [], 'embeddings': [],
                                          'hierarchy': [], 'links': []})
        self.by_id = {e['id']: e for e in evidence}
        self.identity = {'sources': bundle['sources'], 'documents': bundle['documents']}

    def snapshot(self, corpus, snapshot_id=None):
        return {'id': 'snapshot:offline-example', 'identity': self.identity}

    def search(self, snapshot, query, kind, method, search, vector, source_ids, document_ids):
        return [r for r in self.records if r['kind'] == kind]

    def evidence(self, snapshot_id, ids):
        return {k: copy.deepcopy(self.by_id[k]) for k in ids if k in self.by_id}

    def rank_evidence(self, snapshot_id, query, rows):
        return rows

    def history(self, *args):
        return []

    def save_turn(self, *args):
        return 'message:offline-example'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--checker')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    bundle = json.loads((here / 'artifact-bundle.json').read_text())
    mapping = json.loads((here / 'mapping.json').read_text())
    engine = ReasoningEngine(bundle, mapping, jurisdiction='FR', as_of='2026-10-05')
    engine.run(args.output, checker=args.checker)
    config = load_config()
    config['search'].update(method='keyword', rerank=False)
    config['answer']['provider'] = 'extractive'
    assistant = DocumentAssistant(ExampleStore(bundle), config, reasoning_engine=engine)
    answer = assistant.ask('demo', 'Under the stated assumptions, is Alice a beneficial owner of Demo Client?',
                           token='offline', session_id='offline',
                           reasoning_goal=['urn:kf:demo:Alice', 'https://knowledge-foundry.local/ontology/beneficialOwnerOf',
                                           'urn:kf:demo:Client'])
    (args.output / 'assistant-answer.json').write_text(json.dumps(answer, ensure_ascii=False, indent=2) + '\n')
    proof = answer['retrieval']['reasoning']
    print(json.dumps({'answer_status': answer['status'], 'reasoning_status': proof['status'],
                      'conclusions': proof['conclusions'], 'premises': proof.get('premises', []),
                      'rules': proof.get('rules', []), 'certificate': proof.get('certificate'),
                      'citations': proof['citations'], 'output': str(args.output)}, ensure_ascii=False, indent=2))
    return 0 if proof['status'] == 'inferred' else 1


if __name__ == '__main__':
    raise SystemExit(main())
