"""OFFLINE language fixtures only. They never enter the application runtime."""
import json

from knowledge_foundry.retrieval.config import ROOT


def emit(value):
    yield {'type': 'delta', 'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}
    yield {'type': 'usage', 'usage': {'input_tokens': 500, 'output_tokens': 150,
                                     'cached_input_tokens': 0, 'cache_write_tokens': 0}}


def case():
    return json.loads((ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-poc-01/poc-examples.json').read_text())['cases'][0]


def route(payload, config):
    data = json.loads(payload['input'])
    q = data['question']
    if q.startswith('Multipart:'):
        parts = [{'question': "C'est quoi une COSI ?", 'capability': 'documents'},
                 {'question': case()['question'], 'capability': 'accepted'}]
    elif q.startswith('Check accepted:') or q.startswith('Vérifiez'):
        parts = [{'question': q, 'capability': 'accepted'}]
    elif q.startswith('Follow-up:') or q.startswith('Et ') or q.startswith('Explain that'):
        assert data['previous_user_questions']
        parts = [{'question': ('Explain the COSI reporting distinction in English' if q.startswith('Explain') else
                               "Qu'est-ce qui distingue une COSI d'une déclaration de soupçon ?"), 'capability': 'documents'}]
    elif q == 'Clarify this':
        parts = [{'question': q, 'capability': 'clarify'}]
    else:
        parts = [{'question': q, 'capability': 'documents'}]
    for part in parts:
        part['retrieval_query'] = 'COSI' if 'COSI' in part['question'] else part['question']
    yield from emit({'language': 'en' if q.startswith(('Explain', 'What', 'Follow')) else 'fr', 'parts': parts})


def audits(payload, config):
    yield from emit(dict.fromkeys(payload['text']['format']['schema']['properties'], True))


def ranking(payload, config):
    data = json.loads(payload['input'])
    # Synthetic language relevance judgments, used only by offline tests.
    ranked = sorted(data['candidates'], key=lambda e: (
        'La finalité des COSI est différente' not in e['text'],
        "communications systématiques d'information (COSI)" not in e['text'], e['index']))
    yield from emit({'selected_indices': [e['index'] for e in ranked[:data['maximum_results']]]})


def interpretation(payload, config):
    yield from emit(case()['evaluation_oracle'])


def document_answer(payload, config):
    data = json.loads(payload['input'])
    rows = data['evidence']
    objective = next((e for e in rows if 'La finalité des COSI est différente' in e['text']), None)
    expansion = next((e for e in rows if "communications systématiques d'information (COSI)" in e['text']), None)
    if objective and expansion:
        fr = 'Answer in French' in payload['instructions']
        text = ("Une COSI est une communication systématique d’information adressée à Tracfin. " if fr else
                'A COSI is a systematic information report sent to Tracfin. ')
        text += '[' + expansion['label'] + ']\n\n'
        text += ("La source distingue ses critères objectifs du soupçon de la DS, et précise qu’une COSI ne dispense pas d’une DS lorsqu’elle est requise. " if fr else
                 'The source distinguishes its objective criteria from suspicion-based reports, and says a COSI does not remove the requirement for a suspicious-transaction report when applicable. ')
        text += '[' + objective['label'] + ']'
    else:
        text = rows[0]['text'] + ' [' + rows[0]['label'] + ']' if rows else 'INSUFFICIENT_EVIDENCE'
    yield from emit(text)


def reasoning_answer(payload, config):
    data = json.loads(payload['input'])
    row = next(e for e in data['evidence'] if 'décret en Conseil' in e['text'])
    yield from emit('La source indique qu’un décret en Conseil d’État précise la définition du bénéficiaire effectif '
                    'et les modalités de sa détermination. Il s’agit d’une assertion de la source dans cet instantané, '
                    'sans nouvelle déduction ni constat d’applicabilité actuelle. [' + row['label'] + ']')
