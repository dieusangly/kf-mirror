"""Bounded capability orchestration, independent checks and one persisted result."""
import copy
import json
import re
import time
from uuid import UUID, uuid4

from knowledge_foundry.assistant.bindings import BindingFailure
from knowledge_foundry.embeddings import token_count
from knowledge_foundry.reasoning.assistant import (
    BOUNDARY, FIDELITY_FIELDS, LanguageFailure, ReasoningAssistant, audit_schema,
    phase_config, question_language, settings, structured_call,
)
from knowledge_foundry.retrieval.answering import estimate_cost, generate, openai_stream, request_tokens
from knowledge_foundry.retrieval.service import DocumentAssistant, inventory, inventory_answer
from knowledge_foundry.retrieval.config import ROOT
from knowledge_foundry.reasoning.retrieval import bounded_knowledge_context


ROUTE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'language': {'type': 'string', 'enum': ['fr', 'en']},
    'parts': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {
        'type': 'object', 'additionalProperties': False, 'properties': {
            'question': {'type': 'string', 'minLength': 1},
            'retrieval_query': {'type': 'string', 'minLength': 1},
            'capability': {'type': 'string', 'enum': ['documents', 'accepted', 'inventory', 'clarify']},
        }, 'required': ['question', 'retrieval_query', 'capability']}},
}, 'required': ['language', 'parts']}
ROUTE_CHECKS = ['all_parts_covered', 'references_resolved_faithfully', 'no_invented_assumptions',
                'scope_and_filters_preserved', 'capabilities_appropriate']
ROUTE_PROMPT = BOUNDARY + '''Plan an answer to any French or English corpus question, without answering it.
Resolve follow-up references from previous USER QUESTIONS only. Do not invent facts, dates or conditions.
Preserve the user's language and scope. Divide multipart requests into independently answerable questions
(maximum four), preserving every requested item. Use documents for definitions, explanations, summaries,
comparisons and documentary facts without matching accepted coverage: these need original passages,
not symbolic mappings. Use accepted for factual checks represented by relevant accepted_candidates,
formal/logical consequences, deductions and scenario
conclusions. A request to explain a term AND check a consequence needs separate documents/accepted parts.
Candidate descriptions are partial navigation metadata, never answers or premises. Never force a similar
mapping onto a different question. Definitions and summaries still use documents even when candidates exist.
Missing candidates do not make a logical or scenario conclusion safe to answer as documentary fact.
Use inventory for the list of documents. Use clarify for unresolved ambiguous references, excessive
multipart requests or missing user scope that prevents faithful planning. Never answer from model memory,
create premises/rules, expand filters or substitute a similar known question. No question is allowlisted.
In question fields return self-contained questions, not answers, source facts or invented assumptions.'''
ROUTE_PROMPT += ''' For retrieval_query provide a concise lexical query with the subject and discriminating
terms from the question/history (preserve acronyms and the user's named source). Omit conversational filler.
This is navigation only: it does not establish evidence, answer the question or change application filters.'''

SAFE_MESSAGES = {
    'coverage_gap': ('Les passages sélectionnés ne suffisent pas à répondre à cette partie.',
                     'The selected passages do not provide enough evidence for this part.'),
    'binding_failure': ('Réponse suspendue : les identités des sources ou des instantanés ne correspondent pas.',
                        'Answer withheld: source or snapshot identities do not match.'),
    'citation_failure': ('Réponse suspendue : les citations de cette partie n’ont pas passé la validation.',
                         'Answer withheld: citations for this part failed validation.'),
    'audit_failure': ('Réponse suspendue : la fidélité de cette partie aux sources ou à la question n’a pas été confirmée.',
                      'Answer withheld: fidelity to the sources or question was not confirmed for this part.'),
    'validation_failure': ('La conclusion demandée reste non établie : un contrôle requis a échoué.',
                           'The requested conclusion remains unestablished: a required check failed.'),
    'certificate_failure': ('La conclusion reste non établie : le certificat de raisonnement n’a pas été validé.',
                            'The conclusion remains unestablished: the reasoning certificate did not pass.'),
    'scope_failure': ('La conclusion reste non établie dans le périmètre, la date ou les filtres demandés.',
                      'The conclusion remains unestablished within the requested scope, date or filters.'),
    'integrity_failure': ('Réponse suspendue : le contrôle d’intégrité des connaissances a échoué.',
                          'Answer withheld: the knowledge integrity check failed.'),
    'conflict_failure': ('La conclusion reste non établie : des prémisses contradictoires ont été détectées.',
                         'The conclusion remains unestablished: conflicting premises were detected.'),
    'review_failure': ('Réponse suspendue : une vérification indépendante requise est absente.',
                       'Answer withheld: a required independent review is missing.'),
    'limit': ('La limite d’exécution a été atteinte. Précisez la question ; cette partie reste non établie.',
              'An execution limit was reached. Narrow the question; this part remains unestablished.'),
    'clarify': ('Précisez la source, le sujet ou les conditions de cette partie pour lever l’ambiguïté.',
                'Please specify the source, subject or conditions for this part to resolve the ambiguity.'),
    'unavailable': ('Cette partie n’a pas pu aboutir : service ou configuration indisponible.',
                    'This part could not complete: a service or configuration is unavailable.'),
}


def failure(code, language, **extra):
    return {'status': 'clarification' if code == 'clarify' else 'withheld', 'outcome': code,
            'text': SAFE_MESSAGES.get(code, SAFE_MESSAGES['validation_failure'])[language != 'fr'],
            'citations': [], 'checks': {}, **extra}


class TurnLimit(LanguageFailure):
    pass


class TurnBudget:
    """Reserve each request before transport. No retries or unbounded providers."""
    def __init__(self, config, limits):
        self.config, self.limits, self.records = config, limits, []
        self.started = time.monotonic()

    def check(self):
        if time.monotonic() - self.started >= self.limits['max_elapsed_seconds']:
            raise TurnLimit('elapsed_budget_exceeded')

    def wrap(self, phase, transport=None):
        def stream(payload, config):
            self.check()
            input_tokens = request_tokens(payload, config)
            output_tokens = payload['max_output_tokens']
            if (len(self.records) >= self.limits['max_provider_requests'] or
                input_tokens > config['budget']['input_tokens'] or
                output_tokens > config['budget']['output_tokens'] or
                sum(r['reserved_input_tokens'] for r in self.records) + input_tokens > self.limits['max_total_input_tokens'] or
                sum(r['reserved_output_tokens'] for r in self.records) + output_tokens > self.limits['max_total_output_tokens']):
                raise TurnLimit('provider_budget_exceeded')
            record = {'phase': phase, 'model': payload['model'], 'reserved_input_tokens': input_tokens,
                      'reserved_output_tokens': output_tokens, 'status': 'started', 'usage': None,
                      'estimated_cost_usd': None}
            self.records.append(record)
            started, text, complete = time.monotonic(), '', False
            cfg = copy.deepcopy(config)
            cfg['answer']['timeout_seconds'] = min(cfg['answer']['timeout_seconds'],
                self.limits['max_elapsed_seconds'] - (started - self.started))
            try:
                for event in (transport or openai_stream)(payload, cfg):
                    self.check()
                    if time.monotonic() - started > cfg['answer']['timeout_seconds']:
                        raise TurnLimit('request_timeout')
                    if event['type'] == 'delta':
                        text += event['text']
                        if token_count(text, cfg['budget']['tokenizer']) > output_tokens:
                            raise TurnLimit('output_budget_exceeded')
                    if event['type'] == 'usage':
                        complete = True
                        record.update(usage=event['usage'], model=event.get('model', payload['model']),
                            estimated_cost_usd=estimate_cost(event['usage'], cfg['answer'].get('pricing'), payload['model']))
                    yield event
                if not complete:
                    raise LanguageFailure('incomplete_response')
                record['status'] = 'completed'
            except Exception as exc:
                record['status'] = 'limit' if isinstance(exc, TurnLimit) else 'failed'
                raise
            finally:
                record['latency_seconds'] = round(time.monotonic() - started, 3)
        return stream


class Assistant:
    def __init__(self, store, config, binding, *, route_stream=None, audit_stream=None,
                 document_stream=None, interpretation_stream=None, reasoning_stream=None,
                 reasoning_audit_stream=None, ranking_stream=None, checker=None, output_dir=None, progress=None):
        self.store, self.config, self.binding = store, copy.deepcopy(config), binding
        # The POC uses existing multilingual lexical passage retrieval. Auxiliary
        # claims are navigation only. The existing bounded relevance ranker
        # selects original passages for the full question, not only its keywords.
        self.config['search'].update(method='keyword', mode='passage')
        self.config['reasoning'] = {'enabled': False}
        self.route_stream, self.audit_stream = route_stream, audit_stream
        self.document_stream, self.interpretation_stream = document_stream, interpretation_stream
        self.reasoning_stream, self.reasoning_audit_stream = reasoning_stream, reasoning_audit_stream
        self.ranking_stream = ranking_stream
        self.checker = checker
        self.output_dir = output_dir or ROOT / 'artifacts/assistant-poc-runs'
        self.progress = progress or (lambda _: None)

    def _history(self, messages):
        history = [m['content'] for m in messages if m['role'] == 'user']
        turns = self.config['search']['followup_turns']
        history = history[-turns:] if turns else []
        while history and token_count('\n'.join(history), self.config['budget']['tokenizer']) > self.config['budget']['history_tokens']:
            history.pop(0)
        return history

    def _plan(self, question, history, intent, budget, diagnostics):
        if intent == 'document_inventory':
            return {'language': question_language(question), 'parts': [{'question': question, 'capability': 'inventory'}]}
        if self.config['answer']['provider'] == 'extractive' and self.route_stream is None:
            if history:
                # Without language interpretation, a referential follow-up must
                # not be guessed from unrelated keywords.
                return {'language': question_language(question), 'parts': [{'question': question, 'capability': 'clarify'}]}
            return {'language': question_language(question), 'parts': [{'question': question, 'capability': 'documents'}]}
        cfg = phase_config(self.config, settings(self.config), 'interpretation')
        candidates = {'candidates': [], 'coverage': 'unavailable; absence is not falsity'}
        dataset = getattr(self.binding, '_admitted_dataset', None)
        if dataset is not None:
            available = cfg['budget']['input_tokens'] - 2500 - token_count(
                question + '\n'.join(history), cfg['budget']['tokenizer'])
            if available > 512:
                selected = bounded_knowledge_context(dataset, question, history, **diagnostics['filters'],
                    input_tokens=min(3000, available), tokenizer=cfg['budget']['tokenizer'])
                candidates = {'coverage': selected['selection'], 'candidates': [
                    {'mapping_id': r['mapping']['id'], 'source_assertion': r['claim'],
                     'jurisdiction': r['mapping']['jurisdiction'], 'valid_from': r['mapping']['valid_from'],
                     'valid_until': r['mapping'].get('valid_until'), 'has_rules': bool(r['mapping']['rules'])}
                    for r in selected['knowledge']]}
        data = {'question': question, 'previous_user_questions': history, 'filters': diagnostics['filters'],
                'accepted_candidates': candidates, 'max_parts': self.binding.value['limits']['max_parts']}
        diagnostics['accepted_coverage_selection'] = candidates['coverage']
        plan = structured_call(cfg, 'assistant_plan', ROUTE_PROMPT,
            data,
            ROUTE_SCHEMA, budget.wrap('assistant_plan', self.route_stream), diagnostics['phases'])
        if len(plan['parts']) > self.binding.value['limits']['max_parts']:
            raise TurnLimit('part_budget_exceeded')
        for part in plan['parts']:
            if any(token_count(part[k], cfg['budget']['tokenizer']) > cfg['budget']['max_question_tokens']
                   for k in ('question', 'retrieval_query')):
                raise TurnLimit('part_question_budget_exceeded')
        check = structured_call(cfg, 'assistant_plan_audit', BOUNDARY +
            'Assess this proposed decomposition independently. Every requested item must be preserved, '
            'with no new facts, dates, premises or widened scope. Documents are appropriate for definitions, '
            'explanations and facts without matching accepted coverage; factual checks represented by relevant '
            'accepted candidates and logical/scenario conclusions require accepted knowledge. '
            'No omitted consequence may be disguised as documentary explanation. References must resolve '
            'faithfully using user questions only. All booleans must be true to admit this plan.',
            {**data, 'proposed_plan': plan}, audit_schema(ROUTE_CHECKS),
            budget.wrap('assistant_plan_audit', self.audit_stream), diagnostics['phases'])
        diagnostics['plan_audit'] = check
        if not all(check.values()):
            raise LanguageFailure('plan_audit_failed')
        return plan

    def _documents(self, question, snapshot, filters, language, budget, diagnostics, *, background_only=False,
                   retrieval_query=None):
        self.progress('Retrieving original passages and validating citations')
        budget.check()
        retrieved = DocumentAssistant(self.store, self.config,
            ranking_stream=budget.wrap('document_ranking', self.ranking_stream)).retrieve(
                snapshot['corpus'], question, search_query=retrieval_query,
                snapshot_id=snapshot['id'], **filters)
        for row in retrieved['evidence']:
            self.binding.validate_evidence(row, source_ids=filters['source_ids'], document_ids=filters['document_ids'])
        admitted_ids = {row['id'] for row in retrieved['evidence']}
        if any(set(row['context_ids']) - admitted_ids for row in retrieved['evidence']):
            # In particular, a filter must not remove governing qualifications
            # while leaving an apparently unconditional citable assertion.
            return failure('scope_failure', language)
        retrieved['payload']['instructions'] += '\n' + BOUNDARY + (
            '\nAnswer in ' + ('French' if language == 'fr' else 'English') +
            '. Give a direct source-backed answer and brief evidence-based explanation. '
            'Attribute source assertions. Do not claim a logical deduction or introduce scenario premises. '
            'For multipart questions preserve independently supported information and name missing support. '
            'Use INSUFFICIENT_EVIDENCE when no faithful answer is possible.')
        if background_only:
            retrieved['payload']['instructions'] += (
                '\nThe accepted-knowledge path has an explicit coverage gap. The requested checked conclusion '
                'is NOT established. Provide independently source-supported background only; explain missing '
                'support only when actually identified by the supplied evidence. Never invent missing premises. '
                'Never affirm a requested deduction, scenario conclusion or actual-case '
                'qualification, and never turn a source condition into a verified user fact.')
        if request_tokens(retrieved['payload'], self.config) > self.config['budget']['input_tokens']:
            raise TurnLimit('document_input_budget_exceeded')
        result = None
        for event in generate(self.config, retrieved['payload'], retrieved['evidence'],
                              budget.wrap('document_answer', self.document_stream)):
            if event['type'] == 'answer':
                result = event['answer']
        retrieval_details = {k: v for k, v in retrieved.items() if k not in {'payload', 'evidence'}}
        if result['status'] == 'citation_validation_failed':
            return failure('citation_failure', language, retrieval=retrieval_details)
        if result['status'] == 'insufficient':
            return failure('coverage_gap', language, retrieval=retrieval_details)
        checks = {'source_identity': True, 'citations': True, 'excerpts': result['status'] == 'extractive'}
        if result['status'] == 'draft':
            self.progress('Assessing the answer against the original passages')
            cfg = phase_config(self.config, settings(self.config), 'interpretation')
            check = structured_call(cfg, 'document_audit', BOUNDARY +
                'Evaluate every sentence and citation of this proposed documentary answer against the '
                'actual question and original evidence. All booleans must be true only if established. '
                'Require a direct faithful answer, correct language, complete conditions and exceptions, '
                'editions and scope. Reject invented facts, legal applicability, incorrect attached citations, '
                'unjustified completeness and claims of deduction or proof. Explicitly unsupported parts '
                'may be named without discarding independent supported answers.',
                {**json.loads(retrieved['payload']['input']), 'proposed_answer': result['text']},
                audit_schema(FIDELITY_FIELDS), budget.wrap('document_audit', self.audit_stream), diagnostics['phases'])
            checks['fidelity_audit'] = check
            if not all(check.values()):
                return failure('audit_failure', language, checks=checks, retrieval=retrieval_details)
        if background_only:
            prefix = ('Le résultat contrôlé demandé reste non établi dans les connaissances et opérations prises en charge. '
                      'Les passages suivants apportent seulement un contexte documentaire.\n\n' if language == 'fr' else
                      'The requested checked result remains unestablished within the selected knowledge and supported operations. '
                      'The following passages provide documentary background only.\n\n')
            result['text'] = prefix + result['text']
        return {**result, 'status': 'partial' if background_only else 'supported', 'outcome': 'source_passages', 'checks': checks,
                'retrieval': retrieval_details, 'support': 'source_assertions',
                'verbatim': result['status'] == 'extractive'}

    def _accepted(self, question, messages, snapshot, filters, language, budget):
        self.progress('Checking accepted knowledge, proof evidence and certificates')
        budget.check()
        cfg = copy.deepcopy(self.config)
        remaining = self.binding.value['limits']['max_elapsed_seconds'] - (time.monotonic() - budget.started)
        options = settings(cfg)
        options['limits']['checker_timeout_seconds'] = min(options['limits']['checker_timeout_seconds'], remaining)
        cfg['reasoning_assistant'] = options
        result = ReasoningAssistant(self.store, cfg, dataset_dir=self.binding.dataset_path(),
            dataset=self.binding._admitted_dataset,
            output_dir=self.output_dir, checker=self.checker,
            interpretation_stream=budget.wrap('interpretation', self.interpretation_stream),
            answer_stream=budget.wrap('explanation', self.reasoning_stream),
            audit_stream=budget.wrap('reasoning_audit', self.reasoning_audit_stream or self.audit_stream)).evaluate(
                snapshot['corpus'], question, snapshot_id=snapshot['id'], messages=messages, **filters)
        budget.check()
        if result['capability_outcome'] == 'supported':
            if result.get('language') != language:
                return failure('audit_failure', language)
            proof = result['retrieval']['reasoning']
            if proof['status'] not in {'asserted', 'inferred'} or proof['certificate'].get('status') != 'accepted':
                return failure('validation_failure', language)
            for row in result['citations']:
                self.binding.validate_evidence(row, source_ids=filters['source_ids'], document_ids=filters['document_ids'])
            return {**result, 'status': 'supported', 'outcome': proof['status'],
                    'checks': result['retrieval']['semantic_checks'],
                    'support': 'checked_deduction' if proof['status'] == 'inferred' else 'accepted_source_assertions'}
        # Result text from a failed capability is never reused as background.
        return failure(result['capability_outcome'], language, retrieval=result['retrieval'],
                       checks=result['retrieval'].get('semantic_checks', {}))

    def ask(self, question, *, token, conversation_id, submission_id=None, source_ids=None,
            document_ids=None, intent=None):
        if not isinstance(question, str) or not question.strip() or len(question) > self.config['budget']['max_question_tokens'] * 32:
            raise ValueError('question is empty or exceeds the transport character limit')
        over_question_budget = token_count(question, self.config['budget']['tokenizer']) > self.config['budget']['max_question_tokens']
        for values in (source_ids, document_ids):
            if values is not None and (not isinstance(values, list) or any(not isinstance(v, str) for v in values)):
                raise ValueError('filters must be lists of source/document identities')
        requested_filters = {'source_ids': sorted(set(source_ids or [])) or None,
                             'document_ids': sorted(set(document_ids or [])) or None}
        if intent not in {None, 'document_inventory'}:
            raise ValueError('invalid Assistant intent')
        submission_id = str(UUID(str(submission_id))) if submission_id else str(uuid4())
        messages = self.store.assistant_history(conversation_id, token, self.binding.value)
        old = [m for m in messages if m['submission_id'] == submission_id]
        if old:
            if len(old) != 2 or next(m['content'] for m in old if m['role'] == 'user') != question:
                raise ValueError('submission identity was reused for a different question')
            row = next(m for m in old if m['role'] == 'assistant')
            if row['payload']['filters'] != requested_filters or row['payload'].get('intent') != intent:
                raise ValueError('submission identity was reused with different filters or intent')
            return {**row['payload'], 'message_id': row['id']}
        budget = TurnBudget(self.config, self.binding.value['limits'])
        language, parts, plan = question_language(question), [], None
        filters = requested_filters
        diagnostics = {'phases': [], 'filters': filters, 'binding_check': {'status': 'not_checked'}}
        try:
            if over_question_budget:
                raise TurnLimit('question_budget_exceeded')
            self.progress('Checking the conversation’s pinned knowledge and source identities')
            diagnostics['binding_check'] = self.binding.validate(self.store)
            full, accepted = self.binding.snapshots(self.store)
            filters = self.binding.filters(full, source_ids, document_ids)
            diagnostics['filters'] = filters
            budget.check()
            plan = self._plan(question, self._history(messages), intent, budget, diagnostics)
            language = plan['language']
            for part in plan['parts']:
                try:
                    budget.check()
                    if part['capability'] == 'documents':
                        answer = self._documents(part['question'], full, filters, language, budget, diagnostics,
                                                 retrieval_query=part.get('retrieval_query'))
                    elif part['capability'] == 'accepted':
                        answer = self._accepted(part['question'], messages, accepted, filters, language, budget)
                        if answer['outcome'] == 'coverage_gap':
                            answer = self._documents(part['question'], full, filters, language, budget, diagnostics,
                                                     background_only=True, retrieval_query=part.get('retrieval_query'))
                            answer['coverage_fallback'] = 'symbolic_coverage_gap'
                    elif part['capability'] == 'inventory':
                        answer = {**inventory_answer(inventory(full, **filters)), 'status': 'supported',
                                  'outcome': 'inventory', 'checks': {'snapshot_metadata': True}}
                    else:
                        answer = failure('clarify', language)
                except BindingFailure:
                    answer = failure('binding_failure', language)
                except (TurnLimit, LanguageFailure) as exc:
                    answer = failure('limit' if isinstance(exc, TurnLimit) else 'unavailable', language)
                except Exception:
                    answer = failure('validation_failure', language)
                parts.append({**answer, 'question': part['question'], 'capability': part['capability']})
        except BindingFailure:
            diagnostics['binding_check'] = {'status': 'failed'}
            parts = [failure('binding_failure', language)]
        except (TurnLimit, LanguageFailure) as exc:
            parts = [failure('audit_failure' if str(exc) == 'plan_audit_failed' else
                             'limit' if isinstance(exc, TurnLimit) else 'unavailable', language)]
        except Exception:
            parts = [failure('validation_failure', language)]
        answer = self._compose(parts, language)
        answer.update(bindings=self.binding.value, binding_sha256=self.binding.digest, filters=filters,
                      submission_id=submission_id, intent=intent, knowledge_label='Experimental POC')
        costs = [r['estimated_cost_usd'] for r in budget.records]
        answer['telemetry'] = {'model': 'assistant-poc-v1', 'requests': budget.records,
            'estimated_cost_usd': sum(costs) if all(c is not None for c in costs) else None,
            'total_latency_seconds': round(time.monotonic() - budget.started, 3), 'automatic_retries': 0}
        answer['orchestration'] = {'plan': plan, 'diagnostics': diagnostics, 'limits': self.binding.value['limits']}
        answer['message_id'] = self.store.save_assistant_turn(conversation_id, token, self.binding.value,
                                                              submission_id, question, answer)
        # A concurrent duplicate may have won the transaction. Return the one
        # saved final answer rather than associating another draft with its ID.
        saved = next(m for m in self.store.assistant_history(conversation_id, token, self.binding.value)
                     if m['id'] == answer['message_id'])
        return {**saved['payload'], 'message_id': saved['id']}

    def _compose(self, parts, language):
        parts = list(parts)
        limit = self.binding.value['limits']['max_final_output_tokens']
        # Retain complete independently supported parts when the aggregate
        # budget is full; never cut off a qualification or discard everything.
        for index in reversed(range(len(parts))):
            if token_count('\n\n'.join(p['text'] for p in parts), self.config['budget']['tokenizer']) <= limit - 64:
                break
            prior = parts[index]
            parts[index] = failure('limit', language, question=prior.get('question'), capability=prior.get('capability'))
        citations, labels, rendered = [], {}, []
        for part in parts:
            mapping = {}
            for row in part['citations']:
                if row['id'] not in labels:
                    labels[row['id']] = 'E' + str(len(citations) + 1)
                    citations.append({**row, 'label': labels[row['id']]})
                mapping[row['label']] = labels[row['id']]
            if part.get('verbatim'):
                # Source bytes may contain arbitrary bracket text. Render these
                # literally and put application-owned citation links beside them.
                text = part['text']
            else:
                text = re.sub(r'\[(E\d+)\]', lambda m: '[' + mapping.get(m[1], m[1]) + ']', part['text'])
            part['display_text'] = text
            part['citation_labels'] = list(dict.fromkeys(mapping.values()))
            rendered.append(text)
        supported = sum(p['status'] in {'supported', 'partial'} for p in parts)
        status = ('answered' if all(p['status'] == 'supported' for p in parts) else 'partial' if supported else
                  'clarification' if any(p['status'] == 'clarification' for p in parts) else 'withheld')
        text = '\n\n'.join(rendered)
        if token_count(text, self.config['budget']['tokenizer']) > self.binding.value['limits']['max_final_output_tokens']:
            # Never truncate a qualification/citation midway through a part.
            return {'text': failure('limit', language)['text'], 'status': 'withheld', 'parts': [],
                    'citations': [], 'language': language, 'warnings': ['Final output budget exceeded.']}
        return {'text': text, 'status': status, 'parts': parts, 'citations': citations,
                'language': language, 'warnings': []}
