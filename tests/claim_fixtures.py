"""Shared synthetic claim/context fixtures; never real pilot approval."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from knowledge_foundry.claim_generation_and_classification import (
    ROOT, canonical, classify_claims, digest, generate_claims as generate_prepared,
)
from knowledge_foundry.claim_context import load_settings, prepare_claim_context as _prepare_claim_context
from knowledge_foundry.document_structure import structure_snapshot
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash
from scripts.validate_artifact_examples import check



def save_structure_fixture(chunked, parsed_dir=None):
    """Synthetic acceptance is explicitly unusable with production providers."""
    from knowledge_foundry.document_structuring import freeze_structure, accept_structure
    import tempfile
    chunked = Path(chunked)
    bundle = json.loads((chunked / 'artifact-bundle.json').read_text())
    if parsed_dir is None:
        with tempfile.TemporaryDirectory() as directory:
            parsed = Path(directory)
            rows = []
            for doc in bundle['documents']:
                details = [{'element_id': e['id'], 'parent_element_id': None,
                            'evidence_role': 'structural' if e['kind'] in {'table_cell', 'figure'} else 'authoritative'}
                           for e in bundle['elements'] if e['document_id'] == doc['id']]
                folder = parsed / 'documents' / doc['id'].removeprefix('doc:')
                folder.mkdir(parents=True)
                (folder / 'parse.json').write_text(json.dumps({'document_id': doc['id'], 'elements': details}))
                rows.extend(details)
            saved = chunked / 'document-structure.json'
            snapshot = json.loads(saved.read_text()) if saved.exists() else structure_snapshot(rows)
            valid = {e['id'] for e in bundle['elements']}
            snapshot['elements'] = [r for r in snapshot['elements'] if r['element_id'] in valid]
            saved.write_text(json.dumps(snapshot))
            (chunked / 'figure-links.json').write_text('[]')
            freeze_structure(chunked, parsed)
    else:
        rows = [row for path in Path(parsed_dir).glob('documents/*/parse.json')
                for row in json.loads(path.read_text())['elements']]
        (chunked / 'document-structure.json').write_text(json.dumps(structure_snapshot(rows)))
        (chunked / 'figure-links.json').write_text('[]')
        freeze_structure(chunked, parsed_dir)
    accept_structure(chunked, test_only=True)


def prepare_claim_context(chunked, output, config_path=None, cache_dir=None, fixture_path=None,
                          on_progress=None, parsed_dir=None):
    save_structure_fixture(chunked, parsed_dir)
    args = (chunked, output) if config_path is None else (chunked, output, config_path, cache_dir, fixture_path)
    return _prepare_claim_context(*args, on_progress=on_progress)


def fixture_settings():
    config = load_settings(ROOT / 'config/claim-generation-and-classification.json')
    config['steps']['claim_generation']['provider'] = 'fixture'
    config['steps']['topic_classification']['provider'] = 'fixture'
    config['pricing']['providers']['fixture'] = copy.deepcopy(config['pricing']['providers']['openai'])
    config['steps']['embeddings'].update(provider='fixture', dimensions=2, expected_dimensions=2, retries=0)
    return config


def prepare_fixture(chunked, config_path, on_progress=None):
    """Explicit test preparation shared by existing generation regression suites."""
    chunked, config_path = Path(chunked), Path(config_path)
    context = chunked.parent / ('context-' + stable_hash([str(chunked), str(config_path), (chunked / 'artifact-bundle.json').read_text(), config_path.read_text()])[:12])
    if not context.exists():
        bundle = json.loads((chunked / 'artifact-bundle.json').read_text())
        records, _ = assemble_structural_chunks(bundle)
        fixture = chunked.parent / 'embedding-fixture.json'
        fixture.write_text(json.dumps({'embedding_vectors': {stable_hash(r['text']): [1., 0.] for r in records}}))
        prepare_claim_context(chunked, context, config_path, chunked.parent / 'cache', fixture,
                              on_progress=on_progress)
    return context


def generate_claims(chunked, output, config_path, fixture_path=None, **kwargs):
    return generate_prepared(chunked, output, config_path, fixture_path,
                             context_dir=prepare_fixture(chunked, config_path), **kwargs)


def bundle(passages, groups):
    """Create saved exact spans; a tuple in a group denotes spanless context."""
    bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
    bundle['claims'], bundle['topics'], bundle['elements'], bundle['chunks'] = [], [], [], []
    document = bundle['documents'][0]
    for index, (kind, text, page) in enumerate(passages):
        locator = f'pdf:p{page}:test-{index}'
        bundle['elements'].append({
            'schema_version': '1.0.0', 'id': 'el:' + digest(document['id'] + '\n' + locator),
            'document_id': document['id'], 'locator_key': locator,
            'revision': digest(kind + '\n' + text), 'kind': kind, 'ordinal': index,
            'text': text, 'location': {'format': 'pdf', 'page': page, 'label': f'test {index}'}})
    for group in groups:
        refs, texts = [], []
        for item in group:
            context = isinstance(item, tuple)
            element = bundle['elements'][item[0] if context else item]
            ref = {'source_id': document['source_id'], 'document_id': document['id'],
                   'element_id': element['id'], 'element_revision': element['revision']}
            if not context:
                ref['span'] = {'start': 0, 'end': len(element['text'])}
            refs.append(ref)
            texts.append(element['text'])
        text = '\n\n'.join(texts)
        bundle['chunks'].append({
            'schema_version': '1.0.0', 'document_id': document['id'],
            'id': 'chunk:' + digest(document['id'] + '\ntest-v1\n' + canonical(refs) + '\n' + text),
            'build_id': bundle['builds'][0]['id'], 'chunker_version': 'test-v1',
            'sequence': len(bundle['chunks']), 'text': text, 'evidence': refs})
    bundle['builds'][0].update(output_chunk_ids=[c['id'] for c in bundle['chunks']],
                               output_claim_ids=[], output_topic_ids=[])
    check(bundle)
    return bundle


def introduction_bundle():
    # Condensed source order, kinds and locations from the saved ACPR parse:
    # body introduction, numbered paragraphs, hanging sector leads, lists,
    # page-bottom notes, and the next independent numbered paragraph.
    passages = [('heading', 'INTRODUCTION', 3),
                ('list_item', '6. Les textes réglementaires sont complétés.', 3),
                ('list_item', '7. Le cadre juridique repose sur deux volets :', 4),
                ('paragraph', 'Dans le secteur de la banque :', 4),
                ('list_item', '- Les établissements de crédit 6 ;', 4),
                ('list_item', '- Les entreprises d’investissement ;', 4),
                ('footnote', '6 Cf 1° de l’article L. 561-2', 4),
                ('paragraph', "Dans le secteur de l'assurance :", 4),
                ('list_item', '- Les entreprises d’assurance ;', 4),
                ('list_item', '- Les mutuelles et unions.', 5),
                ('list_item', '8. Un nouveau paragraphe indépendant.', 5),
                ('heading', '1. Obligations de vigilance', 6),
                ('heading', '1.1. Identification', 6),
                ('paragraph', 'Identifier le client.', 6)]
    result = bundle(passages, [[i] for i in range(len(passages))])
    rows = []
    for i, e in enumerate(result['elements']):
        x = 94 if i == 3 else 76 if i in {4, 5, 7, 8, 9} else 58
        e['location']['bbox'] = [x, 40 + i * 15, 500, 50 + i * 15]
        parent = None if i in {0, 6, 11} else result['elements'][11 if i == 12 else 12 if i == 13 else 0]['id']
        rows.append({'element_id': e['id'], 'parent_element_id': parent,
                     'level': 2 if i == 12 else 1 if e['kind'] == 'heading' else None,
                     'heading_path': ['INTRODUCTION'] if i < 11 else ['1. Obligations de vigilance'],
                     'docling_ref': f'#/texts/{i}', 'docling_parent_ref': '#/body'})
    return result, structure_snapshot(rows)


def prepare_claim_review(root):
    scenario = SimpleNamespace(root=Path(root))
    scenario.chunked, scenario.generated, scenario.classified = [scenario.root / name for name in ('chunked', 'generated', 'classified')]
    scenario.chunked.mkdir()
    bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
    bundle['claims'], bundle['topics'] = [], []
    bundle['builds'][0].update(output_claim_ids=[], output_topic_ids=[])
    (scenario.chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
    config = fixture_settings()
    config['steps']['claim_generation']['retries'] = 0
    config['steps']['topic_classification']['retries'] = 0
    scenario.config = scenario.root / 'config.json'
    scenario.config.write_text(json.dumps(config))
    scenario.fixture = scenario.root / 'fixture.json'
    scenario.fixture.write_text(json.dumps({'candidate_clusters': {bundle['chunks'][0]['id']: {'claims': [
        {'text': text, 'evidence': ['E1']} for text in ['Assertion A.', 'Assertion B.', 'Récit sans règle.']]}},
        'classifications': {'Assertion A.': 'topic:beneficial-ownership',
                            'Assertion B.': 'topic:ongoing-vigilance', 'Récit sans règle.': None}}))
    generate_claims(scenario.chunked, scenario.generated, scenario.config, scenario.fixture)
    classify_claims(scenario.generated, scenario.classified, scenario.config, fixture_path=scenario.fixture)
    return scenario
