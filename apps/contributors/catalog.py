"""Read the pinned, human-readable case artifacts for the contributor UI."""

from knowledge_foundry.assistant.datasets import (
    BO_SOURCE_TITLES, accepted_pins, digest, project_path, read, resolve_passages, rows,
)


FAMILIES = {
    'KYC': 'Identification et entrée en relation',
    'BO': 'Bénéficiaires effectifs et contrôle',
    'PEP': 'Personnes politiquement exposées',
    'RISK': 'Évaluation des risques et vigilance renforcée',
    'MON': 'Surveillance et déclarations de soupçon',
    'TFS': 'Sanctions ciblées liées au financement du terrorisme',
    'PAY': 'Informations sur les transferts',
    'CORR': 'Correspondance bancaire',
}
CATEGORIES = {
    'DL': 'Recherche directe',
    'OH': 'Déduction en une étape',
    'MH': 'Déduction en plusieurs étapes',
    'MP': 'Prérequis manquant',
    'EX': 'Exception',
    'CF': 'Conflit',
}


def pinned_path(name, pins):
    path = project_path(name)
    if digest(path) != pins.get(name):
        raise ValueError(f'Empreinte du fichier non conforme · {name}')
    return path


def load_catalog():
    registry = read(project_path('config/aml-cft-reasoning-datasets.json'))
    sources = {
        (row['publisher_url'], row['source_version']): {
            'title': row['title'], 'url': row['publisher_url'], 'version': row['source_version'],
            'language': row['language'], 'cases': [],
        }
        for row in read(project_path('data/manifest.json'))['sources']
    }
    cases = {}
    for entry in registry['datasets']:
        acceptance_name, status_name = entry['acceptance_path'], entry['status_path']
        acceptance = read(pinned_path(acceptance_name, {acceptance_name: entry['acceptance_sha256']}))
        status = read(pinned_path(status_name, {status_name: entry['status_sha256']}))
        pins, artifacts = accepted_pins(entry, acceptance, status)
        inputs_path = pinned_path(entry['inputs_path'], pins)
        inputs = rows(inputs_path)
        manifest = read(pinned_path(entry['manifest_path'], pins))
        codes = {category: code for code, category in manifest['category_codes'].items()}
        source_backed = entry['context_kind'] == 'source_backed'

        if source_backed:
            questions = rows(pinned_path(artifacts['readable_questions']['path'], pins))
            answers = rows(pinned_path(artifacts['proposed_answers']['path'], pins))
            proofs = rows(pinned_path(artifacts['proposed_proofs']['path'], pins))
            index = read(pinned_path(entry['source_index_path'], pins))
            for source_id, source in index['sources'].items():
                key = (source['official_url'], source['version'])
                sources.setdefault(key, {
                    'title': BO_SOURCE_TITLES.get(source_id, source_id), 'url': key[0],
                    'version': key[1], 'language': index['language'], 'cases': [],
                })
        else:
            parent = project_path(entry['inputs_path']).parent
            root = project_path('.')
            questions = rows(pinned_path(str((parent / 'questions-v1.jsonl').relative_to(root)), pins))
            reference = project_path(acceptance_name).with_name('accepted-references-v1.jsonl')
            answers = proofs = rows(pinned_path(str(reference.relative_to(root)), pins))

        cases_in_manifest = {case['case_id']: case for case in manifest['cases']}
        selected = {(case_id, lang) for case_id in acceptance['case_ids'] for lang in ('fr', 'en')}
        if any(not selected <= records.keys() for records in (inputs, questions, answers, proofs)):
            raise ValueError('Une version attendue du catalogue est absente.')
        for case_id in acceptance['case_ids']:
            meta = cases_in_manifest[case_id]
            case = {
                'id': case_id, 'family': meta['family_id'], 'category': codes[meta['category']],
                'dataset': entry['id'], 'version': entry['version'], 'kind': entry['context_kind'],
                'reference': entry['acceptance_sha256'], 'variants': {},
            }
            for lang in ('fr', 'en'):
                identity = (case_id, lang)
                inputs_row, question, answer = inputs[identity], questions[identity], answers[identity]
                clauses = inputs_row['policy_clauses']
                passages = resolve_passages(index, clauses) if source_backed else {}
                for passage in passages.values():
                    source = sources[passage['official_url'], passage['version']]
                    if case_id not in source['cases']:
                        source['cases'].append(case_id)
                case['variants'][lang] = {
                    'title': inputs_row['title'], 'question': question['text'],
                    'scope': inputs_row['scope'], 'facts': inputs_row['scenario_facts'],
                    'clauses': clauses, 'passages': passages, 'status': answer['expected_status'],
                    'conclusions': answer.get('expected_conclusions', []),
                    'missing': answer.get('missing_premises', []),
                    'withheld': answer.get('withheld_conclusions', []),
                    'steps': proofs[identity]['proof_steps'],
                }
            if case_id in cases:
                raise ValueError(f'Identifiant de scénario en double · {case_id}')
            cases[case_id] = case

    ordered = sorted(cases.values(), key=lambda case: (
        case['id'] != 'BO-RC-OH-01', case['kind'] != 'source_backed',
        list(FAMILIES).index(case['family']), list(CATEGORIES).index(case['category']), case['id'],
    ))
    summary = [{
        **{key: value for key, value in case.items() if key not in {'reference', 'variants'}},
        'variants': {lang: {key: variant[key] for key in ('title', 'question')}
                     for lang, variant in case['variants'].items()},
    } for case in ordered]
    return cases, {'cases': summary, 'families': FAMILIES, 'categories': CATEGORIES,
                   'sources': list(sources.values())}
