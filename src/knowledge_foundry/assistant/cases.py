"""Build a fresh symbolic case from a policy and cited user assertions only."""
from copy import deepcopy
import re
from uuid import uuid4


class InterpretationError(ValueError):
    """An interpretation needs clarification before it can be executed."""


def require(condition, message):
    if not condition:
        raise InterpretationError(message)


def citation(evidence, messages):
    message_id, quote = evidence['message_id'], evidence['quote']
    require(message_id in messages, 'Fact/entity evidence must come from an available user message.')
    text = messages[message_id]
    require(bool(quote.strip()) and text.count(quote) == 1,
            'Please state the relevant facts and question separately; the cited text is absent or ambiguous.')
    start = text.index(quote)
    return {'message_id': message_id, 'start': start, 'end': start + len(quote), 'text': quote}


def overlaps(left, right):
    return (left['message_id'] == right['message_id'] and
            left['start'] < right['end'] and right['start'] < left['end'])


def question_citation(evidence, messages):
    span = citation(evidence, messages)
    # Models sometimes cite the entire request, including its asserted context.
    # Narrow only when punctuation identifies one explicit question sentence.
    # Ambiguous/multiple questions retain their original span and fail closed
    # if a fact overlaps it. Fact citations are never narrowed this way.
    questions = list(re.finditer(r'[^.!?]+\?', span['text']))
    if len(questions) == 1:
        question = questions[0]
        quoted = question.group()
        leading = len(quoted) - len(quoted.lstrip())
        start = span['start'] + question.start() + leading
        end = span['start'] + question.end()
        return {'message_id': span['message_id'], 'start': start, 'end': end,
                'text': messages[span['message_id']][start:end]}
    return span


def assertion_span(span, messages):
    """Conservative question/conditional guard; semantic fidelity still needs review."""
    text = messages[span['message_id']]
    before = list(re.finditer(r'[.!?\n]', text[:span['start']]))
    start = before[-1].end() if before else 0
    after = re.search(r'[.!?\n]', text[span['end']:])
    end = (span['end'] if text[span['end'] - 1] in '.!?\n' else
           span['end'] + after.end() if after else len(text))
    sentence = re.sub(r'(?i)^(?:policy|politique)\s*:\s*', '', text[start:end].strip())
    require('?' not in sentence and not re.match(
        r'(?i)^(?:if|si|suppose|assuming|what|which|who|is|are|does|do|must|can|would|should|'
        r'est-ce|quel\w*|qui|faut|peut|pourrait)\b', sentence),
        'A question or conditional cannot supply a fact. Please state the premises explicitly.')


def build_case(registry, policy_key, decision, messages, previous_policy=None):
    require(decision['context_match'] == 'supported_policy', 'Which supported policy should apply to these facts?')
    policy = registry.policies[policy_key]
    if decision['use_history']:
        require(previous_policy == policy_key, 'Please restate the case facts when changing policy context.')
    else:
        messages = {k: v for k, v in messages.items() if k == 'current'}
    require(decision['question_evidence'] is not None, 'Please identify the question separately from its premises.')
    question_span = question_citation(decision['question_evidence'], messages)
    require(question_span['message_id'] == 'current', 'Please identify the question in your current message.')
    encoding = deepcopy(policy['template'])
    encoding.update(dataset_id=policy['dataset_id'], dataset_version=policy['dataset_version'],
                    case_id='user-' + uuid4().hex, language=decision['language'], facts=[])
    declarations = {}
    entity_sources = []
    for entity in decision['entities']:
        name = entity['name']
        span = citation(entity['evidence'], messages)
        require(not name.startswith('?') and bool(re.search(
                    r'(?<!\w)' + re.escape(name) + r'(?!\w)', span['text'])),
                'Entity names must preserve an exact user mention; an identity mapping cannot be invented.')
        require(name not in declarations and name not in encoding['entities'], 'Duplicate or reserved entity name.')
        require(entity['type'] in {t for types in encoding['predicates'].values() for t in types},
                'Unsupported entity type.')
        declarations[name] = entity['type']
        entity_sources.append({'name': name, 'type': entity['type'], **span})
    encoding['entities'].update(declarations)
    supplied = []
    for index, fact in enumerate(decision['facts'], 1):
        span = citation(fact['evidence'], messages)
        require(not overlaps(span, question_span), 'The interpreted question cannot be supplied as a fact.')
        require(not any(span['text'] in clause['text'] for clause in policy['policy_clauses']),
                'A policy clause cannot be supplied as a customer fact.')
        assertion_span(span, messages)
        source_id = f'user-fact-{index}'
        encoding['facts'].append({'id': source_id + '-e1', 'source_ids': [source_id],
                                  'atom': {'predicate': fact['predicate'], 'args': list(fact['args'])}})
        supplied.append({'id': source_id, 'kind': 'user_assertion', **span})
    query = {k: encoding[k] for k in (*registry.engine.IDENTITY, 'schema_version')}
    variables = {v['name']: v['type'] for v in decision['variables']}
    require(len(variables) == len(decision['variables']), 'Duplicate query variables.')
    query['queries'] = [{'id': 'assistant-query', 'question_id': 'assistant-question',
                         'target': {'predicate': decision['predicate'], 'args': list(decision['args'])},
                         'variables': variables, 'select': list(decision['select'])}]
    try:
        registry.engine.check_encoding(encoding)
        registry.engine.check_queries(encoding, query)
    except ValueError as exc:
        raise InterpretationError('Please clarify the facts or question using the selected policy vocabulary: ' + str(exc)) from exc
    row = {**deepcopy(policy), 'case_id': encoding['case_id'], 'language': encoding['language'],
           'encoding': encoding, 'scenario_facts': supplied}
    return row, query, {'entities': entity_sources, 'facts': supplied, 'question': question_span,
                        'uses_history': decision['use_history']}
