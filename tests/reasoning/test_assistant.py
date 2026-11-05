"""Real reviewed pilot; any changed facts/reviews below are explicitly SYNTHETIC tests."""
import copy
import json

import pytest

from knowledge_foundry.reasoning.assistant import ReasoningAssistant
from knowledge_foundry.reasoning.catalogue import DEFAULT_DATASET, accepted_projection, get_engine, load_dataset
from knowledge_foundry.reasoning.engine import ReasoningEngine
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.structural_chunks import stable_hash


@pytest.fixture
def dataset():
    return load_dataset()


@pytest.fixture
def checker():
    from open_ontologies_lite import find_checker
    try:
        return str(find_checker())
    except RuntimeError:
        pytest.skip('build pinned oo-horn and set OO_HORN')


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('reasoning catalogue must never call model APIs')
    for name in ['knowledge_foundry.retrieval.answering.openai_stream',
                 'knowledge_foundry.retrieval.query.openai_stream',
                 'knowledge_foundry.embeddings.EmbeddingAdapter._request']:
        monkeypatch.setattr(name, forbidden)
    yield
    assert not calls


class PilotMemoryStore:
    """Exact reviewed PostgreSQL projection in memory; no synthetic source claims."""
    def __init__(self, dataset):
        self.records, evidence, self.identity = accepted_projection(dataset)
        self.rows = {e['id']: e for e in evidence}
        self.saved = []

    def snapshot(self, corpus, snapshot_id=None):
        return {'id': snapshot_id or 'snapshot:pilot', 'identity': self.identity}

    def evidence(self, snapshot_id, ids):
        return {k: copy.deepcopy(self.rows[k]) for k in ids if k in self.rows}

    def rank_evidence(self, snapshot_id, query, rows):
        return rows

    def list_siblings(self, *args):
        return {}

    def history(self, session, token, snapshot, assistant_mode):
        assert assistant_mode == 'reasoning'
        return []

    def save_turn(self, session, token, snapshot, question, answer, assistant_mode):
        assert assistant_mode == 'reasoning'
        self.saved.append(answer)
        return 'message:pilot'


def ask(dataset, tmp_path, entry, checker, *, store=None, config=None, **options):
    store = store or PilotMemoryStore(dataset)
    assistant = ReasoningAssistant(store, config or load_config(), output_dir=tmp_path, checker=checker)
    return assistant.ask('pilot', entry['question'], token='t' * 32, session_id='s', snapshot_id='snapshot:pilot', **options)


def test_three_reviewed_exports_preserve_frozen_claims_and_strict_document_validation(dataset):
    from jsonschema import ValidationError
    from scripts.validate_artifact_examples import check
    original = {c['id']: c for c in dataset['provenance']['original_claims']}
    assert len(original) == 3
    assert all(c['grounding_review_status'] == 'draft' for c in original.values())
    assert all(c['grounding_review_status'] == 'verified' for c in dataset['bundle']['claims'])
    assert all({**c, 'grounding_review_status': 'draft'} == original[c['id']] for c in dataset['bundle']['claims'])
    with pytest.raises(ValidationError):
        check(dataset['bundle'])
    records, evidence, identity = accepted_projection(dataset)
    assert len(records) == 3 and len(evidence) == 7 and len(identity['sources']) == 3
    assert {e['reference']['element_id'] for e in evidence} == {
        r['element_id'] for c in dataset['bundle']['claims'] for r in c['evidence']}
    assert identity['grounding_review']['reviewer'] != identity['formalization_review']['reviewer']
    entries = dataset['catalogue']['questions']
    assert {q['id'] for q in entries} == {'V2-009', 'V2-023', 'V2-013'}
    assert all(q['enabled'] for q in entries)
    archived = DEFAULT_DATASET.parent / 'proposals/reviewed-catalogue.json'
    assert file_hash(archived) == dataset['formalization']['catalogue_sha256']
    assert entries == [q for q in json.loads(archived.read_text())['questions'] if q['enabled']]


@pytest.mark.parametrize('name,defect', [
    ('catalogue.json', 'answer'), ('catalogue.json', 'remove'),
    ('catalogue.json', 'add'), ('catalogue.json', 'disable'),
    ('mapping.json', None), ('grounding.json', None), ('bundle.json', None)])
def test_tampered_accepted_exports_fail_even_with_updated_manifest(tmp_path, name, defect):
    import shutil
    path = tmp_path / 'dataset'
    shutil.copytree(DEFAULT_DATASET, path)
    data = json.loads((path / name).read_text())
    if name == 'catalogue.json':
        if defect == 'remove':
            data['questions'].pop()
        elif defect == 'add':
            data['questions'].append({'id': 'unreviewed', 'question': 'Unreviewed question', 'enabled': False})
        elif defect == 'disable':
            data['questions'][0]['enabled'] = False
        else:
            data['questions'][0]['answer'] = 'COSI removes all liability.'
    elif name == 'mapping.json':
        data['mappings'][0]['facts'][0][2] = 'urn:forged'
    elif name == 'grounding.json':
        data['judgments'][0]['grounding'] = 'pending'
    else:
        data['claims'][0]['text'] += ' forged'
    (path / name).write_text(json.dumps(data))
    manifest = json.loads((path / 'manifest.json').read_text())
    manifest['files'][name] = file_hash(path / name)
    (path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises((ValueError, KeyError)):
        load_dataset(path)


@pytest.mark.parametrize('index', [0, 1, 2])
def test_real_pilot_deductions_in_question_language_with_original_citations(dataset, tmp_path, checker, index):
    entry = dataset['catalogue']['questions'][index]
    result = ask(dataset, tmp_path, entry, checker)
    assert result['status'] == 'reasoned'
    assert result['text'].startswith(entry['answer'])
    assert result['language'] == entry['language']
    assert result['telemetry']['estimated_cost_usd'] == 0
    proof = result['retrieval']['reasoning']
    assert proof['status'] == 'inferred' and len(proof['premises']) == 2 and len(proof['rules']) == 1
    assert proof['certificate']['status'] == 'accepted'
    assert proof['certificate']['verdict'] == 'entailed_under_supplied_rules'
    assert proof['goal'] == entry['goal']
    assert proof['jurisdiction'] == entry['jurisdiction'] and proof['as_of'] == entry['as_of']
    assert all(c['element_kind'] != 'heading' for c in result['citations'])
    if index == 0:
        assert "Les COSI n'entraînent pas de dispense de DS" in result['citations'][0]['text']
        assert result['language'] == 'en'  # English explanation, French citation.
    elif index == 2:
        assert result['language'] == 'fr'
        assert len(proof['citations']) == 3 and len(result['citations']) == 1
        assert sum(c['role'] == 'governing_context' for c in proof['citations']) == 2


@pytest.mark.parametrize('cause', ['filter', 'document_filter', 'missing', 'revision', 'version', 'text', 'location', 'count', 'tokens', 'output', 'checker'])
def test_no_fallback_or_models_on_proof_boundary_failure(dataset, tmp_path, checker, cause):
    entry = dataset['catalogue']['questions'][0]
    store, config, options = PilotMemoryStore(dataset), load_config(), {}
    key = next(k for k, e in store.rows.items() if e['source']['id'] == 'src:acpr-tracfin-joint-guidelines')
    if cause == 'filter': options['source_ids'] = ['src:bcbs-aml-cft-risk-management-2020']
    elif cause == 'document_filter': options['document_ids'] = ['doc:other']
    elif cause == 'missing': del store.rows[key]
    elif cause == 'revision': store.rows[key]['reference']['element_revision'] = '0' * 64
    elif cause == 'version': store.rows[key]['source']['version'] = 'future-edition'
    elif cause == 'text': store.rows[key]['text'] = 'forged source'
    elif cause == 'location': store.rows[key]['location']['page'] = 1
    elif cause == 'count':
        entry = dataset['catalogue']['questions'][2]
        config['search']['max_context_evidence'] = 1
    elif cause == 'tokens': config['budget']['input_tokens'] = 1450
    elif cause == 'output': config['budget']['output_tokens'] = 1
    elif cause == 'checker': checker = '/missing/oo-horn'
    result = ask(dataset, tmp_path, entry, checker, store=store, config=config, **options)
    assert result['status'] in {'insufficient', 'unavailable'}
    assert not result['citations']
    assert 'COSI neither' not in result['text']
    assert store.saved == [result]
    if cause in {'filter', 'document_filter', 'missing', 'revision', 'version', 'text', 'location', 'count'}:
        proof = result['retrieval']['reasoning']
        assert proof['jurisdiction'] == entry['jurisdiction'] and proof['as_of'] == entry['as_of']
        assert proof['certificate']['status'] == 'accepted'
        assert not proof['conclusions']


def test_unsupported_questions_are_persisted_without_engine(dataset, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unsupported questions need no engine')
    monkeypatch.setattr('knowledge_foundry.reasoning.assistant.get_engine', forbidden)
    archived = json.loads((DEFAULT_DATASET.parent / 'proposals/reviewed-catalogue.json').read_text())
    unsupported = [q for q in archived['questions'] if not q['enabled']]
    for entry in [*unsupported, {'question': 'Decide all my AML duties'}]:
        result = ask(dataset, tmp_path, entry, '/missing/checker')
        assert result['status'] == 'insufficient'
        assert 'Unsupported question' in result['text']
        assert not result['citations']


def test_cached_closure_checks_certificate_again_and_is_snapshot_scoped(dataset, tmp_path, checker):
    entry = dataset['catalogue']['questions'][0]
    engine = get_engine(dataset, 'snapshot:first', entry, output_dir=tmp_path, checker=checker)
    assert get_engine(dataset, 'snapshot:first', entry, output_dir=tmp_path, checker=checker) is engine
    assert get_engine(dataset, 'snapshot:second', entry, output_dir=tmp_path, checker=checker) is not engine
    assert get_engine(dataset, 'snapshot:first', dataset['catalogue']['questions'][1],
                      output_dir=tmp_path, checker=checker) is not engine
    path = engine.directory / 'certificate/horn.tsv'
    path.write_text(path.read_text() + 'forged\n')
    proof = engine.explain(entry['goal'])
    assert proof['status'] == 'unchecked' and not proof['conclusions']
    assert proof['certificate']['status'] == 'binding_mismatch'


def test_project_checker_is_bound_and_explicit_missing_paths_do_not_fall_back(tmp_path, checker, monkeypatch):
    import shutil
    from knowledge_foundry.reasoning.catalogue import resolve_checker
    from knowledge_foundry.reasoning.engine import UPSTREAM_COMMIT
    installed = tmp_path / 'oo-horn'
    shutil.copy2(checker, installed)
    manifest = installed.with_name('manifest.json')
    manifest.write_text(json.dumps({'upstream_commit': UPSTREAM_COMMIT, 'binary_sha256': file_hash(installed)}))
    monkeypatch.setattr('knowledge_foundry.reasoning.catalogue.DEFAULT_CHECKER', installed)
    monkeypatch.delenv('OO_HORN', raising=False)
    assert resolve_checker() == str(installed)
    assert resolve_checker('/missing/explicit-checker') == '/missing/explicit-checker'
    monkeypatch.setenv('OO_HORN', '/missing/environment-checker')
    assert resolve_checker() == '/missing/environment-checker'
    monkeypatch.delenv('OO_HORN')
    installed.write_bytes(installed.read_bytes() + b'forged')
    with pytest.raises(ValueError, match='binding mismatch'):
        resolve_checker()


@pytest.mark.parametrize('mutation', ['missing_premise', 'jurisdiction', 'date', 'contradiction'])
def test_synthetic_failed_conditions_missing_facts_and_conflicts(dataset, tmp_path, checker, monkeypatch, mutation):
    """SYNTHETIC mutations test failure handling; never accepted pilot assertions."""
    from knowledge_foundry.reasoning.adapter import KF, content_digest
    bundle, mapping = copy.deepcopy(dataset['bundle']), copy.deepcopy(dataset['mapping'])
    entry = dataset['catalogue']['questions'][0]
    jurisdiction, as_of = entry['jurisdiction'], entry['as_of']
    if mutation == 'missing_premise': mapping['mappings'][0]['facts'].pop()
    elif mutation == 'jurisdiction': jurisdiction = 'DE'
    elif mutation == 'date': as_of = '2026-10-29'
    else:
        mapping['mappings'][0]['facts'].extend([
            ['urn:synthetic:institution', KF + 'requires', 'urn:synthetic:action'],
            ['urn:synthetic:institution', KF + 'prohibits', 'urn:synthetic:action']])
    mapping['review']['content_sha256'] = content_digest(mapping)  # Synthetic test binding only.
    engine = ReasoningEngine(bundle, mapping, jurisdiction=jurisdiction, as_of=as_of)
    engine.run(tmp_path / 'synthetic', checker=checker)
    monkeypatch.setattr('knowledge_foundry.reasoning.assistant.get_engine', lambda *args, **kwargs: engine)
    result = ask(dataset, tmp_path, entry, checker)
    assert result['status'] == 'insufficient'
    assert not result['retrieval']['reasoning']['conclusions']
    assert result['retrieval']['reasoning']['status'] == ('contradiction' if mutation == 'contradiction' else 'insufficient')
