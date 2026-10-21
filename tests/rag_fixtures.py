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
