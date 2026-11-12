"""Frozen release integrity and SYNTHETIC runtime-review contract tests.

Generated reviews below exist only in pytest's disposable copy. They exercise
mechanical admission, never constitute independent source review of the release.
"""
import copy
import json
import shutil
from pathlib import Path

import pytest

from knowledge_foundry.reasoning.adapter import compile_release_projection
from knowledge_foundry.reasoning.assistant import ReasoningAssistant
from knowledge_foundry.reasoning.catalogue import load_dataset, resolve_checker
from knowledge_foundry.reasoning.engine import ReasoningEngine
from knowledge_foundry.reasoning.release import (
    ReviewRequired, admit_release, inspect_release, release_evidence, release_projection,
)
from knowledge_foundry.reasoning.retrieval import bounded_knowledge_context, validate_plan
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import ROOT, load_config
from knowledge_foundry.structural_chunks import stable_hash
from scripts.setup_reasoning_assistant import ReleaseEvaluationStore
from rag_fixtures import json_stream

RELEASE = ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-release-01'


@pytest.fixture(scope='module')
def candidate(tmp_path_factory):
    if not (RELEASE / 'integration.json').exists():
        pytest.skip('local accepted AML/CFT release required')
    # Missing-review cases must not depend on the real release's review status.
    directory = tmp_path_factory.mktemp('unreviewed-release') / 'release'
    shutil.copytree(RELEASE, directory,
                    ignore=shutil.ignore_patterns('proofs', 'postgres', '__pycache__'))
    contract_path = directory / 'integration.json'
    contract = json.loads(contract_path.read_text())
    contract['reviews'] = {}
    contract_path.write_text(json.dumps(contract))
    return inspect_release(directory)


@pytest.fixture(autouse=True)
def no_paid(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('release tests must not call paid providers')
    for target in ['knowledge_foundry.reasoning.assistant.openai_stream',
                   'knowledge_foundry.retrieval.answering.openai_stream',
                   'knowledge_foundry.retrieval.query.openai_stream',
                   'knowledge_foundry.embeddings.EmbeddingAdapter._request']:
        monkeypatch.setattr(target, forbidden)


def synthetic_reviews(dataset, directory):
    """Test-only assertions, conspicuously named; never written to RELEASE."""
    evidence, by_mapping = release_evidence(dataset)
    ledger = {x['mapping_id']: x for x in dataset['ledger']}
    common = {'binding': dataset['release_identity'], 'reviewed_at': '2026-11-10T21:00:00+00:00',
              'notes': 'SYNTHETIC unit-test assertions; no source review performed.'}
    ground = {**common, 'reviewer': {'kind': 'agent', 'name': 'SYNTHETIC grounding fixture'},
              'judgments': [{'claim_id': c['id'], 'claim_sha256': stable_hash(c),
                  'evidence_sha256': stable_hash(c['evidence']), 'grounding': 'pass',
                  'notes': 'SYNTHETIC grounding fixture.'} for c in dataset['bundle']['claims']]}
    semantic = {**common, 'reviewer': {'kind': 'agent', 'name': 'SYNTHETIC semantic fixture'},
                'judgments': [{'mapping_id': r['id'], 'claim_id': r['claim_id'],
                    'mapping_sha256': stable_hash(r), 'claim_sha256': r['claim_sha256'],
                    'vocabulary_dependencies_sha256': stable_hash(ledger[r['id']]['vocabulary_dependencies']),
                    'evidence_sha256': stable_hash([evidence[k] for k in by_mapping[r['id']]]),
                    'dimensions': dict.fromkeys(('formalization', 'scope', 'vocabulary'), 'pass'),
                    'notes': 'SYNTHETIC semantics fixture.'} for r in dataset['mapping']['mappings']]}
    contract = {'format': dataset['format'], 'binding': dataset['release_identity'], 'reviews': {}, 'catalogue': []}
    for name, value in [('grounding', ground), ('formalization', semantic)]:
        path = directory / (name + '.json')
        path.write_text(json.dumps(value))
        contract['reviews'][name] = {'file': path.name, 'sha256': file_hash(path)}
    (directory / 'integration.json').write_text(json.dumps(contract))
    return ground, semantic


@pytest.fixture
def synthetic(candidate, tmp_path):
    # All fabricated review assertions live in an independent disposable copy.
    directory = tmp_path / 'release'
    shutil.copytree(RELEASE, directory,
                    ignore=shutil.ignore_patterns('proofs', 'postgres', '__pycache__'))
    dataset = {**candidate, 'directory': directory}
    ground, semantic = synthetic_reviews(dataset, directory)
    return dataset, ground, semantic


def test_exact_population_and_no_silent_evidence_omissions(candidate):
    records, evidence, identity = release_projection(candidate, evaluation=True)
    assert len(records) == 2164 and len(evidence) == 3506
    claim_refs = {stable_hash(ref) for c in candidate['bundle']['claims'] for ref in c['evidence']}
    assert len({stable_hash(e['reference']) for e in evidence} - claim_refs) == 212
    assert identity['knowledge_status'] == 'runtime_review_pending_evaluation'
    assert all(set(r['evidence_ids']) <= {e['id'] for e in evidence} for r in records)
    assert candidate['catalogue']['questions'] == []
    with pytest.raises(ValueError, match='runtime review'):
        release_projection(candidate)


def test_missing_reviews_keep_runtime_gate_closed(candidate):
    with pytest.raises(ReviewRequired, match='grounding'):
        load_dataset(candidate['directory'])
    store = ReleaseEvaluationStore(candidate)
    assistant = ReasoningAssistant(store, load_config(), dataset_dir=candidate['directory'])
    result = assistant.ask(store.corpus, 'What is a crypto-asset transfer?', token='local',
                           session_id='local', snapshot_id=store.sid)
    assert result['status'] == 'unavailable' and 'independent reviews' in result['text']
    assert result['knowledge_status'] == 'content_accepted_runtime_review_pending'
    assert not result['citations'] and result['telemetry']['phases'] == []


def test_same_content_with_stale_snapshot_review_digest_is_rejected_before_language(candidate, monkeypatch):
    # SYNTHETIC store/review admission only: candidate bytes and source facts stay
    # unchanged. Equal content identity must not substitute for exact runtime
    # review/projection identity after an approved review record changes.
    store = ReleaseEvaluationStore(candidate)
    store.identity['knowledge_status'] = 'accepted'
    newer_review = {**candidate, 'trust': 'accepted', 'digest': 'SYNTHETIC-new-runtime-review-digest'}
    calls = []
    def interpretation(payload, config):
        calls.append(True)
        yield from json_stream({'status':'unsupported','language':'en','message':'','operation':'check',
                                'jurisdiction':'','as_of':'','patterns':[],'select':[]})(payload, config)
    assistant = ReasoningAssistant(store, load_config(), interpretation_stream=interpretation)
    monkeypatch.setattr(assistant, '_dataset', lambda: newer_review)
    row = next(r for r in candidate['mapping']['mappings'] if r['facts'])
    claim = next(c for c in candidate['bundle']['claims'] if c['id'] == row['claim_id'])
    answer = assistant.ask(store.corpus, claim['text'], token='local', session_id='local', snapshot_id=store.sid)
    assert not calls
    assert answer['status'] == 'unavailable' and not answer['citations']
    assert answer['telemetry']['phases'] == []


def test_synthetic_reviews_admit_exact_export_without_promoting_statuses(synthetic):
    dataset, _, _ = synthetic
    admitted = admit_release(dataset)
    assert admitted['trust'] == 'accepted'
    assert admitted['bundle'] == dataset['bundle'] and admitted['sidecars'] == dataset['sidecars']
    assert any(c['grounding_review_status'] == 'draft' for c in admitted['bundle']['claims'])
    assert all(s['review']['status'] == 'draft' for s in admitted['sidecars'])
    assert load_dataset(dataset['directory'])['digest'] == admitted['digest']
    row = next(r for r in admitted['mapping']['mappings'] if r['facts'])
    projection = compile_release_projection(admitted, jurisdiction=row['jurisdiction'], as_of=row['valid_from'])
    assert projection['evaluation_only'] is False and projection['version'] == 'reviewed-release-v1'


def test_rehashing_current_manifest_cannot_change_frozen_semantics(synthetic):
    dataset, _, _ = synthetic
    path = dataset['directory'] / 'dataset/mapping-001.json'
    sidecar = json.loads(path.read_bytes())
    sidecar['mappings'][0]['jurisdiction'] = 'FORGED'
    path.write_text(json.dumps(sidecar))
    manifest_path = dataset['directory'] / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['dataset/mapping-001.json'] = file_hash(path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='frozen candidate file changed'):
        inspect_release(dataset['directory'])


@pytest.mark.parametrize('defect', ['identity', 'population', 'vocabulary', 'evidence', 'scope', 'claim', 'notes'])
def test_runtime_review_failures_cannot_be_rehashed_into_approval(synthetic, defect):
    dataset, ground, semantic = synthetic
    if defect == 'identity': semantic['reviewer'] = ground['reviewer']
    elif defect == 'population': ground['judgments'].pop()
    elif defect == 'vocabulary': semantic['judgments'][0]['vocabulary_dependencies_sha256'] = '0' * 64
    elif defect == 'evidence': semantic['judgments'][0]['evidence_sha256'] = '0' * 64
    elif defect == 'scope': semantic['judgments'][0]['dimensions']['scope'] = 'pending'
    elif defect == 'claim': ground['judgments'][0]['claim_sha256'] = '0' * 64
    else: ground['notes'] = ''
    contract = json.loads((dataset['directory'] / 'integration.json').read_bytes())
    for key, review in [('grounding', ground), ('formalization', semantic)]:
        path = dataset['directory'] / (key + '.json')
        path.write_text(json.dumps(review))
        contract['reviews'][key]['sha256'] = file_hash(path)
    (dataset['directory'] / 'integration.json').write_text(json.dumps(contract))
    with pytest.raises(ValueError):
        admit_release(dataset)


def test_selection_is_bounded_and_never_exhaustive_by_omission(candidate):
    from knowledge_foundry.embeddings import token_count
    context = bounded_knowledge_context(candidate, 'beneficial owner', input_tokens=8000)
    assert token_count(json.dumps(context, ensure_ascii=False)) <= 8000
    assert not context['selection']['exhaustive_for_corpus']
    assert context['selection']['omitted_mapping_count'] > 0
    selected = {r['mapping']['id'] for r in context['knowledge']}
    assert selected == set(context['selection']['selected_mapping_ids'])
    originals = {t['iri']: t for t in candidate['vocabulary']}
    for term in context['vocabulary']:
        original = originals[term['iri']]
        assert {k: v for k, v in term.items() if k not in {
            'record_sha256', 'supporting_source_references_sha256'}} == {
            k: v for k, v in original.items() if k != 'supporting_source_references'}
        assert term['record_sha256'] == stable_hash(original)
        assert term['supporting_source_references_sha256'] == stable_hash(original['supporting_source_references'])
    absent = next(r for r in candidate['mapping']['mappings'] if r['id'] not in selected and r['facts'])
    plan = {'status': 'ready', 'language': 'en', 'message': '', 'operation': 'check',
            'jurisdiction': absent['jurisdiction'], 'as_of': absent['valid_from'],
            'patterns': [absent['facts'][0]], 'select': []}
    with pytest.raises(ValueError):
        validate_plan(plan, context, {'max_patterns': 4})


def test_selection_keeps_rule_producers_and_withholds_over_budget():
    baseline = load_dataset()
    row = copy.deepcopy(baseline['mapping']['mappings'][0])
    a, b, c, predicate = 'urn:test:a', 'urn:test:b', 'urn:test:c', 'urn:test:p'
    first = {**row, 'id': 'producer', 'facts': [[a, predicate, b]], 'rules': [], 'conditions': [], 'exceptions': []}
    second = {**row, 'id': 'consumer', 'facts': [], 'rules': [{'body': [[a, predicate, b]], 'head': [a, predicate, c]}],
              'conditions': [], 'exceptions': []}
    dataset = {**baseline, 'mapping': {'mappings': [first, second]}}
    claim = next(x['text'] for x in baseline['bundle']['claims'] if x['id'] == row['claim_id'])
    context = bounded_knowledge_context(dataset, claim, input_tokens=8000)
    assert set(context['selection']['selected_mapping_ids']) == {'producer', 'consumer'}
    short = bounded_knowledge_context(dataset, claim, input_tokens=300)
    assert not short['knowledge'] and short['selection']['reason'] == 'dependency_budget'
    partial = bounded_knowledge_context(dataset, claim, input_tokens=1000)
    assert partial['selection']['selected_mapping_ids'] == ['producer']
    with pytest.raises(ValueError, match='unknown term'):
        validate_plan({'status': 'ready', 'language': 'en', 'message': '', 'operation': 'check',
                       'jurisdiction': row['jurisdiction'], 'as_of': row['valid_from'],
                       'patterns': [[a, predicate, c]], 'select': []}, partial, {'max_patterns': 4})


@pytest.mark.parametrize('case_id,question', [
    ('unfiltered-aml-cft-v1-kyc21-205', None),
    ('unfiltered-aml-cft-v1-kyc21-205', 'Dans le guide ACPR, à quoi correspond le sigle SICAV ?'),
    ('unfiltered-aml-cft-v1-kyc21-205', 'What does SICAV stand for in the customer identification guidelines?'),
    ('EN-CONJUNCTION', None),
    ('EN-CONJUNCTION', 'In the English regulation title, are the date 31 May 2024 and the EEA relevance label both printed?'),
    ('EN-CONJUNCTION', 'Does the 2024/1624 caption give both its May date and its relevance to the EEA?'),
    ('EXCEPTION-NO-CLEARANCE', None),
    ('EXCEPTION-NO-CLEARANCE', 'Under Article 33, can a low-risk client get simplified due diligence when exception clearance is unknown?'),
    ('EXCEPTION-NO-CLEARANCE', 'Do Article 33 simplified customer checks follow from low risk alone, without evidence clearing exceptions?'),
])
def test_selection_covers_release_questions_and_paraphrases(candidate, case_id, question):
    cases = json.loads((RELEASE / 'checks/stage3-matrix.json').read_bytes())['cases']
    case = next(c for c in cases if c['id'] == case_id)
    context = bounded_knowledge_context(candidate, question or case['question'],
        document_ids=case.get('document_ids'), input_tokens=8000)
    if case_id == 'EXCEPTION-NO-CLEARANCE':
        # Both pinned language editions describe Article 33. Require a complete
        # relevant guarded description, never an invented customer premise.
        originals = [r for r in candidate['mapping']['mappings'] if r['id'] in {
            'aml-cft-v1-r24en-797:repair01:repair02', 'aml-cft-v1-r24fr-799'}]
        retained = [r for r in originals if r in [v['mapping'] for v in context['knowledge']]]
        assert retained and all(r['conditions'] and not r['facts'] for r in retained)
    else:
        validate_plan(case['plan'], context, {'max_patterns': 4})
    assert context['knowledge'] and not context['selection']['exhaustive_for_corpus']


@pytest.mark.parametrize('case_id', ['unfiltered-aml-cft-v1-kyc21-205', 'EN-CONJUNCTION'])
def test_selection_correction_does_not_cross_source_or_date_scope(candidate, case_id):
    from knowledge_foundry.reasoning.retrieval import knowledge_context
    case = next(c for c in json.loads((RELEASE / 'checks/stage3-matrix.json').read_bytes())['cases']
                if c['id'] == case_id)
    for filters in ({'source_ids': ['src:absent']}, {'document_ids': ['doc:absent']}):
        context = bounded_knowledge_context(candidate, case['question'], input_tokens=8000, **filters)
        assert not context['knowledge']
        assert context['selection']['reason'] == 'no_lexical_coverage'
    context = knowledge_context(candidate, document_ids=case.get('document_ids'))
    for plan in ({**case['plan'], 'jurisdiction': 'DE'}, {**case['plan'], 'as_of': '1900-01-01'}):
        with pytest.raises(ValueError, match='scope'):
            validate_plan(plan, context, {'max_patterns': 4})


def selection_fixture(specs):
    """Synthetic selection-only records; never added to a release or database."""
    baseline = load_dataset()
    template = baseline['mapping']['mappings'][0]
    claim = next(c for c in baseline['bundle']['claims'] if c['id'] == template['claim_id'])
    rows, claims = [], []
    for mid, text, fields in specs:
        rows.append({**template, 'id': mid, 'claim_id': mid, 'facts': [], 'rules': [],
                     'conditions': [], 'exceptions': [], **fields})
        claims.append({**claim, 'id': mid, 'text': text})
    return {**baseline, 'bundle': {**baseline['bundle'], 'claims': claims},
            'mapping': {'mappings': rows}, 'vocabulary': []}


def test_selection_continues_after_a_complete_group_cannot_fit():
    facts = [['urn:test:entity', 'urn:test:has', 'urn:test:quasar']]
    dataset = selection_fixture([
        ('a-large', 'quasar ' * 5000, {'facts': facts}),
        ('b-small', 'quasar', {'facts': facts}),
    ])
    context = bounded_knowledge_context(dataset, 'quasar', input_tokens=3000)
    assert context['selection']['selected_mapping_ids'] == ['b-small']
    short = bounded_knowledge_context(dataset, 'quasar', input_tokens=300)
    assert not short['knowledge'] and short['selection']['reason'] == 'dependency_budget'


def test_selection_retains_missing_guard_without_expanding_unusable_alternatives():
    premise = ['?entity', 'urn:test:category', 'urn:test:member']
    guard = ['?entity', 'urn:test:scope', 'urn:test:confirmed']
    dataset = selection_fixture([
        ('root', 'quasar', {'conditions': [guard], 'rules': [
            {'body': [premise], 'head': ['?entity', 'urn:test:must', 'urn:test:quasar']}]}),
        ('alternative', 'classification ' * 5000, {'facts': [
            ['urn:test:entity', 'urn:test:category', 'urn:test:member']]}),
    ])
    context = bounded_knowledge_context(dataset, 'quasar', input_tokens=3000)
    assert context['selection']['selected_mapping_ids'] == ['root']
    assert context['knowledge'][0]['mapping'] == dataset['mapping']['mappings'][0]
    assert not context['knowledge'][0]['mapping']['facts']
    # Once the missing guard has support, the complete dependency group is
    # mandatory. Its oversized producer must still cause a real budget failure.
    dataset['mapping']['mappings'][1]['facts'].append(
        ['urn:test:entity', 'urn:test:scope', 'urn:test:confirmed'])
    context = bounded_knowledge_context(dataset, 'quasar', input_tokens=3000)
    assert not context['knowledge'] and context['selection']['reason'] == 'dependency_budget'


def test_selection_keeps_transitive_support_and_independent_exception_conflicts():
    premise = ['urn:test:entity', 'urn:test:has', 'urn:test:category']
    seed = ['urn:test:entity', 'urn:test:has', 'urn:test:seed']
    guard = ['urn:test:entity', 'urn:test:scope', 'urn:test:confirmed']
    when = ['urn:test:entity', 'urn:test:exception', 'urn:test:applies']
    clearance = ['urn:test:entity', 'urn:test:exception', 'urn:test:cleared']
    dataset = selection_fixture([
        ('root', 'quasar', {'conditions': [guard], 'exceptions': [{'when': when, 'clearance': clearance}],
                            'rules': [{'body': [premise], 'head': ['urn:test:entity', 'urn:test:must', 'urn:test:quasar']}]}),
        ('middle', 'classification', {'rules': [{'body': [seed], 'head': premise}]}),
        ('seed', 'support', {'facts': [seed]}),
        ('when', 'support', {'facts': [when]}),
        ('clearance', 'support', {'facts': [clearance]}),
    ])
    context = bounded_knowledge_context(dataset, 'quasar', input_tokens=8000)
    # A missing rule guard cannot hide an independently supported conflict.
    assert set(context['selection']['selected_mapping_ids']) == {'root', 'when', 'clearance'}
    dataset['mapping']['mappings'][2]['facts'].append(guard)
    context = bounded_knowledge_context(dataset, 'quasar', input_tokens=8000)
    assert set(context['selection']['selected_mapping_ids']) == {'root', 'middle', 'seed', 'when', 'clearance'}
    assert all(r['mapping'] in dataset['mapping']['mappings'] for r in context['knowledge'])


def test_real_checker_over_all_scope_content_retains_evaluation_label(candidate, tmp_path):
    checker = resolve_checker()
    if not Path(checker).is_file(): pytest.skip('pinned checker required')
    row = next(r for r in candidate['mapping']['mappings'] if r['facts'])
    engine = ReasoningEngine.from_dataset(candidate, jurisdiction=row['jurisdiction'], as_of=row['valid_from'], evaluation=True)
    engine.run(tmp_path / 'certificate', checker=checker)
    proof = engine.explain(row['facts'][0])
    assert proof['status'] == 'asserted' and proof['certificate']['status'] == 'accepted'
    assert proof['evaluation_only'] is True
    # Re-read the certificate, not just the stored verdict.
    (engine.directory / 'certificate/horn.tsv').write_text('forged\n')
    assert engine.explain(row['facts'][0])['status'] == 'unchecked'


@pytest.fixture(scope='module')
def admitted_snapshot():
    """Actual reviews, no pre-admission monkeypatches or fabricated status flags."""
    return load_dataset(RELEASE)


def test_validated_snapshot_views_and_evidence_are_independent(admitted_snapshot):
    from knowledge_foundry.reasoning.release import validated_release
    first = validated_release(admitted_snapshot)
    second = validated_release(admitted_snapshot)
    assert first == second == admitted_snapshot
    first['mapping']['mappings'][0]['jurisdiction'] = 'FORGED'
    assert second['mapping']['mappings'][0] == admitted_snapshot['mapping']['mappings'][0]
    with pytest.raises(ValueError, match='mutated'):
        validated_release(first)
    evidence, by_mapping = release_evidence(second)
    key = next(iter(evidence)); evidence[key]['text'] = 'FORGED'
    by_mapping[next(iter(by_mapping))].clear()
    intact, owners = release_evidence(admitted_snapshot)
    assert intact[key]['text'] != 'FORGED' and all(owners.values())


@pytest.mark.parametrize('kind', ['source', 'archive', 'seal', 'index', 'pin', 'schema'])
def test_content_changes_to_external_dependencies_invalidate_receipts(admitted_snapshot, monkeypatch, kind, tmp_path):
    import knowledge_foundry.reasoning.release as module
    dependencies = admitted_snapshot._receipt['dependencies']
    package = ROOT / admitted_snapshot['provenance']['input_package']
    paths = {
        'source': package / admitted_snapshot['provenance']['original_sources'][0]['package_path'],
        'archive': package / 'provenance/history.zip', 'seal': package / 'seal.json',
        'index': package / 'provenance/index.json',
        'pin': ROOT / next(iter(json.loads((RELEASE / 'checks/input-pins.json').read_bytes())['current_file_sha256'])),
        'schema': ROOT / 'schemas/artifact-schema-v1.0.0.json',
    }
    target = paths[kind].absolute()
    assert str(target) in dependencies
    # Read changed bytes from a disposable replacement. Never edit frozen sources.
    replacement = tmp_path / target.name; replacement.write_bytes(b'changed dependency contents')
    changed_hash = file_hash(replacement)
    original = module._file_hash
    monkeypatch.setattr(module, '_file_hash', lambda path: changed_hash if Path(path).absolute() == target else original(path))
    with pytest.raises(ValueError, match='dependency changed'):
        module.validated_release(admitted_snapshot)


def _mechanical_release_copy(directory):
    """Copy only loader inputs, excluding all old proofs, dumps and run outputs."""
    frozen = json.loads(json.loads((RELEASE / 'checks/frozen-candidate.json').read_bytes())['files']['manifest.json']['content'])
    contract = json.loads((RELEASE / 'integration.json').read_bytes())
    names = set(frozen['files']) | {'manifest.json', 'acceptance.json', 'integration.json',
                                  'checks/frozen-candidate.json'}
    names.update(s['file'] for s in contract['reviews'].values())
    for name in names:
        target = directory / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RELEASE / name, target)


@pytest.fixture(scope='module')
def copied_admission(tmp_path_factory):
    directory = tmp_path_factory.mktemp('latency-inputs')
    _mechanical_release_copy(directory)
    return load_dataset(directory)


@pytest.mark.parametrize('name', ['dataset/mapping-001.json', 'dataset/bundle.json',
                                 'checks/runtime-grounding-review.json',
                                 'checks/runtime-formalization-review.json', 'integration.json', 'acceptance.json'])
def test_same_path_same_size_and_time_replacements_fail_after_successful_reuse(copied_admission, name):
    import os
    from knowledge_foundry.reasoning.release import validated_release
    # A genuine successful reuse precedes each replacement.
    assert validated_release(copied_admission)['digest'] == copied_admission['digest']
    path = copied_admission['directory'] / name
    raw, stat = path.read_bytes(), path.stat()
    replacement = path.with_name(path.name + '.replacement')
    # Keep valid JSON, byte count and mtime: alter one whitespace byte.
    index = raw.index(b'\n'); changed = raw[:index] + b' ' + raw[index + 1:]
    replacement.write_bytes(changed); os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(path)
    try:
        assert path.stat().st_size == stat.st_size and path.stat().st_mtime_ns == stat.st_mtime_ns
        with pytest.raises(ValueError, match='dependency changed'):
            validated_release(copied_admission)
    finally:
        path.write_bytes(raw); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_forged_accepted_dictionary_cannot_authorize_projection_or_compilation(candidate, monkeypatch):
    from knowledge_foundry.reasoning.release import release_projection, validated_release
    forged = {**candidate, 'trust': 'accepted'}
    calls = []
    def denied(*args):
        calls.append(True)
        raise ReviewRequired('independent admission required')
    monkeypatch.setattr('knowledge_foundry.reasoning.release.load_release', denied)
    for operation in [lambda: validated_release(forged), lambda: release_projection(forged),
                      lambda: compile_release_projection(forged, jurisdiction='FR', as_of='2026-10-28')]:
        with pytest.raises(ReviewRequired): operation()
    assert len(calls) == 3


def test_changed_dataset_cannot_hit_an_existing_engine_cache(admitted_snapshot, tmp_path, monkeypatch):
    from knowledge_foundry.reasoning.catalogue import get_engine
    def forbidden(*args, **kwargs): pytest.fail('mutated input reached engine cache')
    monkeypatch.setattr('knowledge_foundry.reasoning.catalogue._cached_engine', forbidden)
    forged = copy.copy(admitted_snapshot)
    forged['digest'] = 'forged-cache-key'
    with pytest.raises(ValueError, match='mutated'):
        get_engine(forged, 'snapshot:latency-test', {'jurisdiction': 'FR', 'as_of': '2026-10-28'}, output_dir=tmp_path)


def test_display_suggestions_cannot_grant_admission(copied_admission, monkeypatch):
    from knowledge_foundry.reasoning.catalogue import load_suggestions
    assert load_suggestions(copied_admission['directory']) == []
    monkeypatch.setattr('knowledge_foundry.reasoning.release.load_release',
                        lambda *args: (_ for _ in ()).throw(ReviewRequired('full admission denied')))
    # Presentation remains empty; it cannot be used to execute or import data.
    assert load_suggestions(copied_admission['directory']) == []
    with pytest.raises(ReviewRequired): load_dataset(copied_admission['directory'])


def test_concurrent_engine_setup_uses_independent_validated_views(admitted_snapshot, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import knowledge_foundry.reasoning.catalogue as catalogue
    gate = Barrier(2)
    def capture(*args, **kwargs):
        data = catalogue._engine_dataset.get()
        gate.wait(timeout=30)
        return data
    monkeypatch.setattr(catalogue, '_cached_engine', capture)
    plan = {'jurisdiction': 'FR', 'as_of': '2026-10-28'}
    def setup(index):
        return catalogue.get_engine(admitted_snapshot, 'snapshot:' + str(index), plan, output_dir=tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(setup, [1, 2]))
    assert first is not second and first == second == admitted_snapshot
    first['vocabulary'][0]['definition'] = 'FORGED'
    assert second['vocabulary'][0] == admitted_snapshot['vocabulary'][0]
    assert catalogue._engine_dataset.get() is None


@pytest.fixture(scope='module')
def cached_admitted_engine(copied_admission, tmp_path_factory):
    from knowledge_foundry.reasoning.catalogue import get_engine
    output = tmp_path_factory.mktemp('latency-cached-proof')
    plan = {'jurisdiction': 'FR', 'as_of': '2026-10-28'}
    engine = get_engine(copied_admission, 'snapshot:cache-invalidation', plan,
                        output_dir=output, checker=resolve_checker())
    assert engine.report['status'] == 'ready'
    return engine, plan, output


@pytest.mark.parametrize('dependency', ['candidate', 'review', 'source'])
def test_tampering_is_rejected_after_a_real_engine_cache_hit(copied_admission, cached_admitted_engine,
                                                            dependency, monkeypatch, tmp_path):
    from knowledge_foundry.reasoning.catalogue import get_engine
    import knowledge_foundry.reasoning.release as module
    engine, plan, output = cached_admitted_engine
    def reused():
        return get_engine(copied_admission, 'snapshot:cache-invalidation', plan,
                          output_dir=output, checker=resolve_checker())
    assert reused() is engine
    if dependency == 'source':
        source = copied_admission['provenance']['original_sources'][0]
        target = (ROOT / copied_admission['provenance']['input_package'] / source['package_path']).absolute()
        altered = tmp_path / 'changed-source'; altered.write_bytes(b'changed original edition')
        changed_hash = file_hash(altered); original_hash = module._file_hash
        with monkeypatch.context() as patch:
            patch.setattr(module, '_file_hash', lambda path: changed_hash if Path(path).absolute() == target else original_hash(path))
            with pytest.raises(ValueError, match='dependency changed'): reused()
    else:
        name = 'dataset/mapping-001.json' if dependency == 'candidate' else 'checks/runtime-formalization-review.json'
        target = copied_admission['directory'] / name
        original = target.read_bytes()
        try:
            target.write_bytes(original.replace(b'\n', b' ', 1))
            with pytest.raises(ValueError, match='dependency changed'): reused()
        finally:
            target.write_bytes(original)
    assert reused() is engine
    assert engine.verify_certificate()['status'] == 'accepted'
