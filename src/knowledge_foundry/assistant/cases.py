"""Build a fresh symbolic case from a policy and cited user assertions only."""
from copy import deepcopy
from datetime import date
import re
import unicodedata
from uuid import uuid4

from .datasets import BO_DATASET


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


def mentions(text, name):
    return bool(name.strip() and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text))


def folded(text):
    return ''.join(char for char in unicodedata.normalize('NFKD', text.lower())
                   if not unicodedata.combining(char)).replace('’', "'")


MONTHS = dict(zip('january february march april may june july august september october november december'.split(),
                  range(1, 13)))
MONTHS.update(zip('janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre'.split(),
                  range(1, 13)))
MONTH = '|'.join(MONTHS)
DATE_PATTERNS = (
    r'\b(?P<year>\d{4})[-/](?P<month>\d{2})[-/](?P<day>\d{2})\b',
    rf'\b(?P<day>\d{{1,2}})\s+(?P<month>{MONTH})\s+(?P<year>\d{{4}})\b',
    rf'\b(?P<month>{MONTH})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})\b',
)


def explicit_dates(text):
    text = folded(text)
    require(not re.search(r'\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b', text),
            'Please give the assessment date with a named month or in YYYY-MM-DD format.')
    dates = set()
    for pattern in DATE_PATTERNS:
        for match in re.finditer(pattern, text):
            year, month, day = match.group('year', 'month', 'day')
            try:
                dates.add(date(int(year), MONTHS[month] if month in MONTHS else int(month), int(day)).isoformat())
            except ValueError as exc:
                raise InterpretationError('Please clarify the assessment date.') from exc
    return dates


def bo_scope(policy, decision, messages, question_span):
    scope = decision.get('scope_evidence')
    assessment_date = policy['scope']['assessment_date']
    require(scope is not None, 'Which company, covered financial institution and assessment date apply to this case?')
    spans = {role: citation(scope[role + '_evidence'], messages) for role in ('company', 'institution', 'date')}
    for span in spans.values():
        require(not overlaps(span, question_span), 'Please state the scope separately from the question.')
        assertion_span(span, messages)
    company, institution = scope['company'], scope['institution']
    require(company != institution and mentions(spans['company']['text'], company)
            and mentions(spans['institution']['text'], institution),
            'Please identify the client company and the assessing institution in your assertions.')
    company_text, institution_text = (folded(spans[role]['text']) for role in ('company', 'institution'))
    require(re.search(r'\b(?:unlisted|privately [ -]?(?:held|owned)|non[ -]cotee?)\b', company_text)
            and re.search(r'\b(?:registered in france|french[ -]registered|immatriculee? en france)\b', company_text)
            and not re.search(r"\b(?:not (?:registered|unlisted|privately)|n'est pas immatriculee?)\b", company_text),
            'Is the company unlisted and registered in France?')
    require(re.search(r'\b(?:financial institution|etablissement financier|organisme financier)\b', institution_text)
            and re.search(r'\b(?:obliged|covered|subject to|assujetti)e?\b', institution_text)
            and not re.search(r'\b(?:not (?:an? )?(?:obliged|covered|subject to|financial institution)|'
                              r'(?:non|pas) assujetti|unregulated|exempt)\b', institution_text),
            'Which obliged financial institution is assessing this company?')
    require(any(mentions(span['text'], company) and mentions(span['text'], institution)
                and re.search(r"\b(?:cliente?|customer|business relationship|relation d'affaires)\b", folded(span['text']))
                for span in (spans['company'], spans['institution'])),
            'Please state the business relationship between the company and the covered institution.')
    require(explicit_dates(spans['date']['text']) == {assessment_date}
            and explicit_dates(question_span['text']) <= {assessment_date}
            and not re.search(r"\b(?:today|as of now|currently|aujourd'hui|actuellement|a ce jour)\b",
                              folded(question_span['text'])),
            'The accepted policy applies to an assessment on ' + assessment_date + '. Which date applies to your case?')
    return {'company': company, 'institution': institution, 'assessment_date': assessment_date, 'evidence': spans}


def check_bo_atom(policy, scope, predicate, args, span):
    arguments = policy['vocabulary']['predicates'].get(predicate, {}).get('arguments', [])
    bindings = dict(zip((argument['name'] for argument in arguments), args))
    require(all(bindings.get(role, scope[role]) == scope[role] for role in ('company', 'institution', 'assessment_date')),
            'Please use the same client company, covered institution and assessment date throughout the case.')
    require(explicit_dates(span['text']) <= {scope['assessment_date']},
            'Please clarify whether the cited assertion applies on the assessment date.')
    if predicate == 'sas':
        text = folded(span['text'])
        require(re.search(r'\b(?:sas|simplified joint-stock company|societe par actions simplifiee)\b', text)
                and not re.search(r"\b(?:not (?:an? )?(?:sas|simplified joint-stock company)|"
                                  r"n'est pas une (?:sas|societe par actions simplifiee))\b", text),
                'Is the client a French simplified joint-stock company (SAS)?')
    if predicate == 'completed_paragraph_one_assessment':
        text = folded(span['text'])
        completion = r'(?:complet(?:ed|ing)|finish(?:ed|ing)|achev(?:e|ee)|termin(?:e|ee)|mene a terme)'
        require(re.search(rf'\b{completion}\b', text)
                and re.search(r'\b(?:first[ -]paragraph|paragraph[ -](?:one|1)|premier alinea|alinea 1)\b', text)
                and not re.search(rf'\b(?:not|never|pas|jamais|will be|will|sera|va)\s+(?:yet\s+)?{completion}\b', text),
                'Please confirm whether the institution completed the first-paragraph assessment.')


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
    scope = bo_scope(policy, decision, messages, question_span) if policy['dataset_id'] == BO_DATASET else None
    if scope:
        check_bo_atom(policy, scope, decision['predicate'], decision['args'], question_span)
    encoding = deepcopy(policy['template'])
    encoding.update(dataset_id=policy['dataset_id'], dataset_version=policy['dataset_version'],
                    case_id='user-' + uuid4().hex, language=decision['language'], facts=[])
    case_arguments = set(decision['args'])
    case_arguments.update(arg for fact in decision['facts'] for arg in fact['args'])
    entity_types = {kind for signature in encoding['predicates'].values() for kind in signature}
    declarations = {}
    entity_sources = []
    for entity in decision['entities']:
        name = entity['name']
        if scope and name not in case_arguments:
            # The institution and date may establish applicability without taking
            # part in the deduction. Their citations were checked with the scope.
            if name in (scope['institution'], scope['assessment_date']):
                continue
            date_text = scope['evidence']['date']['text']
            if mentions(date_text, name) and explicit_dates(name) == {scope['assessment_date']}:
                continue
        span = citation(entity['evidence'], messages)
        require(not name.startswith('?') and mentions(span['text'], name),
                'Entity names must preserve an exact user mention; an identity mapping cannot be invented.')
        require(name not in declarations and name not in encoding['entities'], 'Duplicate or reserved entity name.')
        require(entity['type'] in entity_types, 'Unsupported entity type.')
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
        if scope:
            check_bo_atom(policy, scope, fact['predicate'], fact['args'], span)
        source_id = f'user-fact-{index}'
        encoding['facts'].append({'id': source_id + '-e1', 'source_ids': [source_id],
                                  'atom': {'predicate': fact['predicate'], 'args': list(fact['args'])}})
        supplied.append({'id': source_id, 'kind': 'user_assertion', **span})
    if scope:
        predicates = {fact['atom']['predicate'] for fact in encoding['facts']}
        require('fallback_beneficial_owner' not in encoding['predicates'] or 'sas' in predicates,
                'Is the client a French simplified joint-stock company (SAS)?')
        require(not {'money_laundering_suspicion', 'no_ml_or_tf_suspicion'} <= predicates,
                'Please reconcile the opposing statements about the institution’s suspicion.')
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
    provenance = {'entities': entity_sources, 'facts': supplied, 'question': question_span,
                  'uses_history': decision['use_history']}
    if scope:
        row['scope']['case'] = scope
        provenance['scope'] = scope
    return row, query, provenance
