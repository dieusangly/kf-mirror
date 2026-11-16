"""Evidence-aware orchestration with terminal unknown/conflict/error outcomes."""
from copy import deepcopy
import json
import time

from .config import ROOT, load_config
from .datasets import DatasetRegistry, terms
from .llm import LanguageModels, ModelError, ROUTE_SCHEMA, TEXT_SCHEMA, WORDING_SCHEMA


def atom_text(atom):
    return atom['predicate'].replace('_', ' ') + ' (' + ', '.join(atom['args']) + ')'


class DocumentEvidence:
    """Read-only facade: reuse retrieval/generation, never session persistence."""
    def __init__(self, config):
        self.config = config
        self.assistant = None

    def _assistant(self):
        if self.assistant is None:
            from knowledge_foundry.retrieval.config import load_config as retrieval_config
            from knowledge_foundry.retrieval.service import DocumentAssistant
            from knowledge_foundry.storage.postgres import PostgresStore
            config = retrieval_config(ROOT / self.config['retrieval_config'])
            self.assistant = DocumentAssistant(PostgresStore(), config)
        return self.assistant

    def preview(self, question):
        try:
            assistant = self._assistant()
            from knowledge_foundry.retrieval.service import DocumentAssistant
            config = deepcopy(assistant.config)
            config['answer']['provider'] = 'extractive'
            config['search']['rerank'] = False
            query_terms = terms(question)
            keyword_query = ' '.join(sorted(query_terms)) or question
            preview = DocumentAssistant(assistant.store, config).retrieve(
                self.config['corpus'], keyword_query, method='keyword', history=[])
            # Context expansion can put surrounding headings/paragraphs first.
            # Give the router matching anchors before filling its bounded preview.
            evidence = sorted(preview['evidence'], key=lambda e: (
                -len(query_terms & terms(e['text'])),
                e.get('element_kind') == 'heading', bool(e.get('is_context'))))
            return {'available': True, 'snapshot_id': preview['snapshot_id'], 'passages': [
                {'label': e['label'], 'text': e['text'], 'source': e['source'], 'location': e['location'],
                 'element_kind': e.get('element_kind'), 'is_context': bool(e.get('is_context'))}
                for e in evidence[:5]]}
        except Exception:
            # Connection strings and provider payloads must never enter model input/errors.
            return {'available': False, 'passages': [], 'reason': 'Published document corpus unavailable.'}

    def answer(self, question, history, snapshot_id=None):
        from knowledge_foundry.retrieval.answering import generate
        assistant = self._assistant()
        retrieved = assistant.retrieve(self.config['corpus'], question, history=history,
                                       snapshot_id=snapshot_id, route_inventory=True)
        if retrieved.get('intent') == 'document_inventory':
            from knowledge_foundry.retrieval.service import inventory_answer
            return inventory_answer(retrieved['documents'])
        for event in generate(assistant.config, retrieved['payload'], retrieved['evidence'], assistant.answer_stream):
            if event['type'] == 'answer':
                return {**event['answer'], 'snapshot_id': retrieved['snapshot_id'],
                        'retrieval': {k: v for k, v in retrieved.items() if k != 'payload'}}
        raise ValueError('document answer did not complete')


class Assistant:
    def __init__(self, config=None, *, registry=None, models=None, documents=None):
        self.config = config or load_config()
        self.registry = registry or DatasetRegistry(self.config['datasets_path'],
            search_config=self.config['scenario_search'], local_only=self.config['local_only'])
        self.models = models or LanguageModels(self.config)
        # The existing retrieval stack has cloud embedding/reranking/answer stages.
        # Disable that entire adapter in local-only mode until separately localised.
        enabled = self.config['documents']['enabled'] and not self.config['local_only']
        self.documents = (documents or DocumentEvidence(self.config['documents'])) if enabled else None

    def ask(self, question, history=None):
        question = question.strip()
        if not question or len(question) > self.config['max_question_characters']:
            raise ValueError('Enter a question within the configured length limit.')
        started = time.monotonic()
        start_calls = len(self.models.records)
        history = list(history or [])[-2 * self.config['history_turns']:]
        context = [dict(role=m['role'], text=m['text'][:6000], route=m.get('route'),
                        candidate_id=m.get('candidate_id')) for m in history
                   if m.get('role') in {'user', 'assistant'}]
        preferred = next((m.get('candidate_id') for m in reversed(context) if m.get('candidate_id')), None)
        try:
            candidates = self.registry.search(question, self.config['candidate_limit'], preferred)
        except ValueError:
            return {'route': 'error', 'status': 'error',
                    'text': 'Scenario search is unavailable. Check the embedding provider and cache, then try again.',
                    'warnings': ['No fallback answer was generated.'],
                    'telemetry': {'calls': [], 'scenario_search': deepcopy(self.registry.searcher.telemetry),
                                  'latency_seconds': round(time.monotonic() - started, 3)}}
        document_preview = self.documents.preview(question) if self.documents else {
            'available': False, 'passages': [], 'reason': 'Document route disabled by configuration/privacy policy.'}
        document_preview['corpus'] = self.config['documents']['corpus']
        decision = None
        try:
            decision = self.models.call('question_interpretation', 'assistant_route_v1.txt',
                {'question': question, 'history': context, 'candidates': candidates, 'documents': document_preview}, ROUTE_SCHEMA)
            answer = self._dispatch(question, context, candidates, document_preview, decision)
        except (ValueError, KeyError, TypeError):
            answer = {'route': 'error', 'status': 'error',
                      'text': 'The request could not be validated or completed. Check the model/corpus configuration and try again.',
                      'warnings': ['No fallback answer was generated.']}
        document_telemetry = answer.get('telemetry') if answer['route'] == 'documents' else None
        answer['telemetry'] = {'calls': deepcopy(self.models.records[start_calls:]),
                               'scenario_search': deepcopy(self.registry.searcher.telemetry),
                               'document_answer': document_telemetry,
                               'document_check': answer.pop('document_check', None),
                               'routing': {'proposed': decision['route'] if decision else None, 'selected': answer['route']},
                               'document_preview': {k: document_preview[k] for k in
                                   ('available', 'corpus', 'snapshot_id', 'reason') if k in document_preview} | {
                                   'passage_labels': [p['label'] for p in document_preview['passages']]},
                               'latency_seconds': round(time.monotonic() - started, 3)}
        return answer

    def _dispatch(self, question, history, candidates, preview, decision):
        route, language = decision['route'], decision['language']
        base = {'route': route, 'language': language, 'interpretation': decision['interpretation'], 'warnings': []}
        if route != 'general' and decision['general_requested']:
            raise ValueError('general-knowledge preference on a non-general route')
        if route != 'reasoning' and (decision['candidate_id'] is not None or decision['predicate'] is not None
                                    or decision['args'] or decision['variables'] or decision['select']):
            raise ValueError('non-reasoning route contains an executable query')
        if route == 'clarify':
            if not decision['clarification'] or not decision['clarification'].strip():
                raise ValueError('clarification is empty')
            return {**base, 'status': 'clarification', 'text': decision['clarification']}
        if decision['clarification'] is not None:
            raise ValueError('unexpected clarification on an answer route')
        if route == 'reasoning':
            key = decision['candidate_id']
            if key not in {c['candidate_id'] for c in candidates}:
                raise ValueError('selected scenario was not retrieved')
            if decision['context_match'] not in {'same_scenario', 'illustration'}:
                raise ValueError('scenario applicability was not established')
            row = self.registry.records[key]
            variables = {v['name']: v['type'] for v in decision['variables']}
            if len(variables) != len(decision['variables']):
                raise ValueError('duplicate query variables')
            query = {k: row['encoding'][k] for k in (*self.registry.engine.IDENTITY, 'schema_version')}
            query['queries'] = [{'id': 'assistant-query', 'question_id': 'assistant-question',
                                 'target': {'predicate': decision['predicate'], 'args': decision['args']},
                                 'variables': variables, 'select': decision['select']}]
            result, checked = self.registry.execute(key, query)
            return {**base, **self._reasoning(question, row, query, result, checked, language)}
        if route == 'documents':
            return {**base, **self._document_answer(question, history, preview, language)}
        if route == 'general':
            if not decision['general_requested'] and preview['available'] and preview['passages']:
                checked = self._document_answer(question, history, preview, language)
                if checked['status'] != 'insufficient':
                    return {**base, **checked, 'route': 'documents', 'interpretation': (
                        'Répondre à partir du corpus documentaire.' if language == 'fr'
                        else 'Answer using the document corpus.')}
                # Only an explicit insufficient-evidence outcome permits this
                # proposed general route to continue. Failures remain terminal.
                base['document_check'] = {'status': checked['status'], 'snapshot_id': preview.get('snapshot_id'),
                                          'answer_telemetry': checked.get('telemetry')}
            value = self.models.call('general', 'assistant_general_v1.txt',
                                     {'question': question, 'history': history, 'language': language}, TEXT_SCHEMA)
            return {**base, 'status': 'general', 'text': value['text']}
        raise ValueError('unsupported route')

    def _document_answer(self, question, history, preview, language):
        if not self.documents or not preview['available']:
            return {'status': 'unavailable', 'text': (
                'Le corpus documentaire est indisponible. Aucune réponse générale ne lui a été substituée.' if language == 'fr'
                else 'The document corpus is unavailable. No general answer was substituted.')}
        try:
            return self.documents.answer(question, [m['text'] for m in history
                if m['role'] == 'user'], preview.get('snapshot_id'))
        except Exception:
            return {'status': 'error', 'text': (
                'L’assistance documentaire n’a pas pu aboutir. Aucune réponse générale ne lui a été substituée.' if language == 'fr'
                else 'Document assistance could not complete. No general answer was substituted.')}

    def _reasoning(self, question, row, query, result, checked, language):
        outcome = result['results'][0]
        sources = {s['id']: s for kind in ('scenario_facts', 'policy_clauses') for s in row[kind]}
        encoded = {s['id']: s for kind in ('facts', 'rules', 'incompatibilities') for s in row['encoding'][kind]}
        certificate = result['usable_certificate']
        if outcome['status'] == 'entailed':
            by_atom = {json.dumps(s['atom'], sort_keys=True): s for s in certificate}
            needed = set()

            def visit(atom):
                key = json.dumps(atom, sort_keys=True)
                if key in needed:
                    return
                needed.add(key)
                for premise in by_atom.get(key, {}).get('premises', []):
                    visit(premise)

            for atom in outcome['supported_atoms']:
                visit(atom)
            certificate = [s for s in certificate if json.dumps(s['atom'], sort_keys=True) in needed]
        elif outcome['status'] == 'conflict':
            certificate = result['candidate_certificate']
        steps = []
        for n, step in enumerate(certificate, 1):
            source_ids = encoded[step.get('fact_id', step.get('rule_id'))]['source_ids']
            deduction = 'rule_id' in step
            exact = ' '.join(sources[s]['text'] for s in source_ids)
            text = (('Application de la règle : ' if language == 'fr' else 'Apply policy: ') + exact +
                    ' → ' + atom_text(step['atom'])) if deduction else exact
            steps.append({'proof_id': f'S{n}', 'kind': 'deduction' if deduction else 'assertion',
                          'text': text, 'source_ids': source_ids, **step})
        status = outcome['status']
        labels = ({'entailed': 'Conclusion étayée', 'unknown': 'Informations insuffisantes', 'conflict': 'Informations contradictoires'}
                  if language == 'fr' else
                  {'entailed': 'Supported conclusion', 'unknown': 'Insufficient information', 'conflict': 'Conflicting information'})
        text = labels[status] + (': ' + '; '.join(atom_text(a) for a in outcome['supported_atoms'])
                                if outcome['supported_atoms'] else '.')
        diagnostics = []

        def missing(items):
            for item in items:
                if 'atom' in item:
                    diagnostics.append({'kind': item['kind'], 'text': atom_text(item['atom'])})
                missing(item.get('missing', []))

        for finding in outcome['missing']:
            missing(finding['explanation'])
        warnings = []
        try:
            generated = self.models.call('answer_verbalization', 'assistant_reasoning_answer_v1.txt',
                {'question': question, 'language': language, 'context_kind': row['context_kind'],
                 'scope': row['scope'], 'outcome': outcome, 'proof_steps': steps,
                 'conflicts': result['conflicts'], 'sources': list(sources.values())}, WORDING_SCHEMA)
            if generated['status'] != status or [s['proof_id'] for s in generated['steps']] != [s['proof_id'] for s in steps]:
                raise ModelError('wording changed status or proof step identities')
            text = generated['answer']
            for step, wording in zip(steps, generated['steps']):
                step['generated_text'] = wording['text']
        except (ValueError, KeyError, TypeError):
            warnings.append('Generated wording was unavailable or invalid; displaying the checked result and source text.')
        return {'status': status, 'text': text, 'candidate_id': row['candidate_id'],
                'context': {k: row[k] for k in ('candidate_id', 'case_id', 'language', 'dataset_id', 'dataset_version',
                                               'context_kind', 'scope', 'scenario_facts', 'policy_clauses')},
                'proof_steps': steps, 'sources': list(sources.values()), 'diagnostics': diagnostics,
                'checker': checked, 'query': query, 'result': result, 'warnings': warnings}
