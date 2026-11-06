"""Small exact-lineage fixtures; no models, networking or pilot regeneration."""
import copy
import json

from knowledge_foundry.retrieval.artifacts import project
from knowledge_foundry.retrieval.config import ROOT, load_config
from knowledge_foundry.structural_chunks import stable_hash
from scripts.validate_artifact_examples import check, digest, canonical_evidence


def bundle(passages=None, source_name=None):
    b = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
    b['elements'], b['chunks'], b['claims'], b['topics'] = [], [], [], []
    doc, build = b['documents'][0], b['builds'][0]
    if source_name:
        source = next(s for s in b['sources'] if s['id'] == doc['source_id'])
        source.update(id='src:' + source_name, title=source_name, uri='https://example.org/' + source_name)
        doc.update(source_id=source['id'], content_sha256=digest(json.dumps(passages)))
        doc['id'] = 'doc:' + digest(doc['source_id'] + '\n' + doc['content_sha256'])
        build['input_document_ids'] = [doc['id']]
        build['id'] = 'build:' + digest(build['pipeline_version'] + '\n' + build['config_sha256'] + '\n' + doc['id'])
    for i, (kind, text) in enumerate(passages or [
        ('paragraph', 'Rest requirements apply only to tired dogs.'),
        ('paragraph', 'Dogs must rest after exercise. [1]'),
        ('footnote', '1 Except emergency rescue dogs while on duty.'),
        ('footnote', '2 Unrelated cats may play.'),
        ('paragraph', 'Cats may play outdoors.')]):
        locator = f'pdf:p1:fixture-{i}'
        e = {'schema_version': '1.0.0', 'id': 'el:' + digest(doc['id'] + '\n' + locator),
             'document_id': doc['id'], 'locator_key': locator, 'kind': kind, 'ordinal': i,
             'text': text, 'revision': digest(kind + '\n' + text),
             'location': {'format': 'pdf', 'page': 1, 'label': f'paragraph {i}'}}
        b['elements'].append(e)
        ref = {'source_id': doc['source_id'], 'document_id': doc['id'],
               'element_id': e['id'], 'element_revision': e['revision'],
               'span': {'start': 0, 'end': len(text)}}
        chunk = {'schema_version': '1.0.0', 'document_id': doc['id'], 'build_id': build['id'],
                 'chunker_version': 'fixture-v1', 'sequence': i, 'text': text, 'evidence': [ref]}
        chunk['id'] = 'chunk:' + digest(doc['id'] + '\nfixture-v1\n' + canonical_evidence([ref]) + '\n' + text)
        b['chunks'].append(chunk)
    build.update(output_chunk_ids=[c['id'] for c in b['chunks']], output_claim_ids=[], output_topic_ids=[])
    check(b)
    return b


def artifacts():
    b = bundle()
    structural = [{'id': 'structural:' + stable_hash(c['id']), 'text': c['text'],
                   'original_chunk_ids': [c['id']], 'document_id': c['document_id'],
                   'evidence_memberships': [{'reference': c['evidence'][0], 'original_chunk_ids': [c['id']]}]}
                  for c in b['chunks']]
    elements = b['elements']
    claim = {'id': 'claim:fixture', 'text': 'Canine recovery proposition', 'build_id': b['builds'][0]['id'],
             'chunk_ids': [b['chunks'][1]['id']], 'evidence': b['chunks'][1]['evidence'],
             'grounding_review_status': 'draft', 'classification_review_status': 'draft'}
    summary = {'id': 'summary:fixture', 'text': 'Overview of animal recovery',
               'review_status': 'draft', 'evidence': b['chunks'][1]['evidence'],
               'original_chunk_ids': [b['chunks'][1]['id']]}
    return {'bundle': b, 'structural': structural, 'claims': [claim], 'summaries': [summary],
            'hierarchy': [{'element_id': e['id'], 'parent_element_id': elements[0]['id'] if i == 1 else None}
                          for i, e in enumerate(elements)],
            'links': [{'reason': 'same_page_unique_note_marker', 'element_ids': [elements[1]['id'], elements[2]['id']]}],
            'embeddings': [], 'identity': {'chunk_build_id': b['builds'][0]['id']}}


def config(tmp_path):
    c = copy.deepcopy(load_config())
    c['search'].update(method='keyword', language='english', top_k=1, rerank=False)
    c['answer']['provider'] = 'extractive'
    c['embedding'].update(provider='fixture', model='fixture-v1', dimensions=3, expected_dimensions=3)
    c['cache_dir'] = str(tmp_path / 'cache')
    return c


def projection():
    a = artifacts()
    records, evidence = project(a)
    for r in records:
        # Only the target requirement has the vector nearest to the query.
        r['vector'] = [1., 0., 0.] if r['text'].startswith('Dogs must') else [0., 1., 0.]
    identity = {**a['identity'], 'sources': a['bundle']['sources'], 'documents': a['bundle']['documents'],
                'builds': a['bundle']['builds']}
    return records, evidence, identity


# Synthetic model responses for reasoning tests; no live model-quality claim.
def json_stream(value):
    def stream(payload, config):
        yield {'type': 'delta', 'text': json.dumps(value, ensure_ascii=False)}
        yield {'type': 'usage', 'usage': {'input_tokens': 500, 'output_tokens': 200,
                                         'cached_input_tokens': 0, 'cache_write_tokens': 0}}
    return stream


def plan_for(row, *, language='en', goal=None, patterns=None, select=None, status='ready'):
    return copy.deepcopy({'status': status, 'language': language, 'message': '',
            'operation': 'select' if select else 'check', 'jurisdiction': row['jurisdiction'],
            'as_of': row['valid_from'], 'patterns': patterns if patterns is not None else [goal or row['rules'][0]['head']],
            'select': select or []})


def faithful_audit(payload, config):
    from knowledge_foundry.reasoning.assistant import AUDIT_FIELDS, FIDELITY_FIELDS
    fields = FIDELITY_FIELDS if payload['text']['format']['name'] == 'explanation_audit' else AUDIT_FIELDS
    yield from json_stream(dict.fromkeys(fields, True))(payload, config)


def excerpt_explanation(payload, config):
    """Synthetic explanatory prose from original passages, never catalogue answers."""
    data = json.loads(payload['input'])
    fr = 'Answer in French' in payload['instructions']
    evidence = data['evidence'][0]
    conclusions = data['reasoning']['conclusions']
    objects = {g[2].rsplit(':', 1)[-1] for g in conclusions}
    if 'LatestIFIReturn' in objects:
        text = ('Oui. La dernière déclaration d’IFI figure dans la demande décrite par Tracfin. ' if fr else
                'Yes. The latest IFI return is included in the application described by Tracfin. ')
    elif objects & {'Liability', 'NoReportingOrLiabilityExemption'}:
        text = ('Non. La source précise qu’une COSI ne procure pas d’exonération de responsabilité. ' if fr else
                'No. The source states that a COSI does not provide an exemption from liability. ')
    elif 'PersonsActingForCustomers' in objects:
        text = ('La recommandation inclut les personnes agissant pour le compte du client dans le périmètre de vigilance représenté. ' if fr else
                'The recommendation includes people acting on a customer’s behalf in the represented due diligence scope. ')
    else:
        text = ('La source décrit les éléments demandés. ' if fr else 'The source describes the requested information. ')
    text += '[' + evidence['label'] + ']\n\n'
    text += ('Pourquoi cette réponse ? Information explicitement indiquée dans la source : ' if fr else
             'Why this answer? Information explicitly stated in the source: ')
    text += evidence['text'] + ' [' + evidence['label'] + ']'
    yield {'type': 'delta', 'text': text}
    yield {'type': 'usage', 'usage': {'input_tokens': 900, 'output_tokens': 300,
                                     'cached_input_tokens': 0, 'cache_write_tokens': 0}}


def suggestion_interpreter(payload, config):
    """UI regression stub: selects a mapping from question/source words in TESTS only."""
    from knowledge_foundry.reasoning.catalogue import load_dataset
    data = json.loads(payload['input'])
    dataset = load_dataset()
    question = data['question']
    entry = next((q for q in dataset['catalogue']['questions'] if q['question'] == question), None)
    if entry:
        row = next(r for r in dataset['mapping']['mappings'] if r['id'] in entry['mapping_ids'])
        plan = plan_for(row, language=entry['language'])
    else:
        row = dataset['mapping']['mappings'][2]
        if 'IFI' in question:
            plan = plan_for(row, language='fr', goal=row['facts'][1])
        else:
            plan = plan_for(row, patterns=[], status='unsupported')
    yield from json_stream(plan)(payload, config)
