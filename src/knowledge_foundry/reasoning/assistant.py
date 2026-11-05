"""Closed question catalogue using the existing assistant's proof admission gates."""
import copy
import time

from knowledge_foundry.embeddings import token_count
from knowledge_foundry.reasoning.catalogue import DEFAULT_DATASET, get_engine, load_dataset
from knowledge_foundry.retrieval.service import DocumentAssistant


def withheld(reason, *, language='en', status='insufficient'):
    text = ('Réponse non établie : ' if language == 'fr' else 'Answer withheld: ') + reason
    return {'status': status, 'text': text, 'citations': [], 'warnings': [],
            'retrieval': {'reasoning': {'status': status, 'reason': reason, 'conclusions': [],
                                       'premises': [], 'rules': [], 'citations': []}},
            'telemetry': {'model': 'reviewed-catalogue-v1', 'estimated_cost_usd': 0.}}


def catalogue_answer(entry, retrieved, config):
    proof = retrieved['reasoning']
    if proof['status'] not in {'asserted', 'inferred'}:
        reason = proof.get('reason', proof['status'])
        if entry['language'] == 'fr':
            reason = {
                'proof evidence is outside the selected snapshot or filters': 'Les preuves sont absentes de l’instantané sélectionné ou exclues par les filtres.',
                'complete proof evidence exceeds context limits or is non-citable': 'Les preuves complètes dépassent les limites de contexte ou ne sont pas citables.',
                'complete proof exceeds input token budget': 'La preuve complète dépasse le budget de jetons.',
                'not entailed; absence is not falsity': 'Conclusion non établie ; une absence de fait ne prouve pas sa fausseté.',
                'unchecked': 'Le certificat de déduction n’a pas été validé.',
                'contradiction': 'Des prémisses contradictoires empêchent de conclure.',
                'insufficient': 'Les éléments de preuve sont insuffisants.',
                'incomplete': 'Le calcul des déductions est incomplet.',
            }.get(reason, reason)
        answer = withheld(reason, language=entry['language'])
        answer['retrieval']['reasoning'] = proof
        answer.update(scope_note=entry['scope_note'], language=entry['language'], catalogue_question_id=entry['id'])
        return answer
    by_id = {e['id']: e for e in retrieved['evidence']}
    citations = [by_id[c['id']] for c in proof['citations'] if c['role'] == 'source_passage']
    labels = ' '.join('[' + e['label'] + ']' for e in citations)
    if token_count(entry['answer'] + ' ' + labels, config['budget']['tokenizer']) > config['budget']['output_tokens']:
        reason = 'Catalogue explanation exceeds the answer budget.'
        retrieved['reasoning'] = {**proof, 'status': 'insufficient', 'reason': reason,
                                  'conclusions': [], 'citations': []}
        return withheld(reason, language=entry['language'])
    return {'status': 'reasoned', 'text': entry['answer'] + ' ' + labels, 'citations': citations,
            'warnings': [], 'explanation_kind': 'reviewed_catalogue_text',
            'catalogue_question_id': entry['id'], 'language': entry['language'],
            'assumptions': entry['assumptions'], 'deduction_note': entry['deduction_note'],
            'scope_note': entry['scope_note'],
            'telemetry': {'model': 'reviewed-catalogue-v1', 'estimated_cost_usd': 0.}}


class ReasoningAssistant:
    def __init__(self, store, config, *, dataset_dir=DEFAULT_DATASET, output_dir=None, checker=None):
        self.store, self.config = store, copy.deepcopy(config)
        # This mode never makes model calls, regardless of document preferences.
        self.config['answer']['provider'] = 'extractive'
        self.config['reasoning'] = {'enabled': False}
        self.dataset_dir, self.checker = dataset_dir, checker
        self.output_dir = output_dir or (DEFAULT_DATASET.parents[2] / 'artifacts/reasoning-assistant-runs')

    def ask(self, corpus, question, *, token, session_id, snapshot_id, source_ids=None, document_ids=None):
        started = time.monotonic()
        snapshot = self.store.snapshot(corpus, snapshot_id)
        self.store.history(session_id, token, snapshot['id'], 'reasoning')
        if not question.strip() or token_count(question, self.config['budget']['tokenizer']) > self.config['budget']['max_question_tokens']:
            raise ValueError('question is empty or exceeds the configured question budget')
        try:
            dataset = load_dataset(self.dataset_dir)
            entry = next((q for q in dataset['catalogue']['questions'] if question.strip() == q['question']), None)
            if entry is None:
                answer = withheld('Unsupported question. Choose an exact supported catalogue question. '
                                  'Question non prise en charge : choisissez une question du catalogue.')
            else:
                engine = get_engine(dataset, snapshot['id'], entry, output_dir=self.output_dir, checker=self.checker)
                assistant = DocumentAssistant(self.store, self.config, reasoning_engine=engine)
                result = assistant.ask(corpus, question, token=token, session_id=session_id, snapshot_id=snapshot['id'],
                    source_ids=source_ids, document_ids=document_ids, reasoning_goal=entry['goal'], assistant_mode='reasoning',
                    symbolic_answer=lambda retrieved: catalogue_answer(entry, retrieved, self.config))
                return result
        except Exception:
            # Keep document mode independent and never answer with unreviewed RAG.
            # Do not persist exception/provider/connection details.
            answer = withheld('Reasoning setup or validation is unavailable. Check the accepted dataset and checker. '
                              'Configuration ou validation du raisonnement indisponible.', status='unavailable')
        answer['snapshot_id'] = snapshot['id']
        answer['telemetry']['total_latency_seconds'] = round(time.monotonic() - started, 3)
        answer['message_id'] = self.store.save_turn(session_id, token, snapshot['id'], question, answer, assistant_mode='reasoning')
        return answer
