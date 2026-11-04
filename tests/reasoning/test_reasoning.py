"""Synthetic mutations below are test fixtures, never pilot acceptance."""
import copy
import hashlib
import json

import pytest

pytest.importorskip('open_ontologies_lite')
from open_ontologies_lite import check_horn, find_checker
from open_ontologies_lite.shacl import shacl_validate
from jsonschema import ValidationError

from knowledge_foundry.reasoning.adapter import KF, OWL, TYPE, compile_projection, content_digest
from knowledge_foundry.reasoning.engine import ReasoningEngine, SHAPES
from knowledge_foundry.retrieval.artifacts import project
from knowledge_foundry.retrieval.config import ROOT, load_config
from knowledge_foundry.retrieval.service import DocumentAssistant
from knowledge_foundry.structural_chunks import stable_hash

EXAMPLE = ROOT / 'examples/reasoning-aml-cft'
ALICE, CLIENT = 'urn:kf:demo:Alice', 'urn:kf:demo:Client'
GOAL = [ALICE, KF + 'beneficialOwnerOf', CLIENT]
SCOPE = {'jurisdiction': 'FR', 'as_of': '2026-10-05'}


def inputs():
    return [json.loads((EXAMPLE / name).read_text()) for name in ('artifact-bundle.json', 'mapping.json')]


def fixture_review(bundle, mapping):
    """Bind intentional test fixture changes; not a source review procedure."""
    mapping['bundle_sha256'] = stable_hash(bundle)
    claims = {c['id']: c for c in bundle['claims']}
    for row in mapping['mappings']:
        row['claim_sha256'] = stable_hash(claims[row['claim_id']])
    mapping['review']['content_sha256'] = content_digest(mapping)


@pytest.fixture
def checker():
    try:
        return str(find_checker())
    except RuntimeError:
        pytest.skip('build the pinned upstream oo-horn checker and set OO_HORN')


def run(tmp_path, bundle=None, mapping=None, checker='/missing/oo-horn', **options):
    original_bundle, original_mapping = inputs()
    engine = ReasoningEngine(bundle or original_bundle, mapping or original_mapping, **SCOPE)
    engine.run(tmp_path / 'run', checker=checker, **options)
    return engine


@pytest.mark.parametrize('status', ['draft', 'rejected'])
def test_unreviewed_claims_never_enter_graph(status):
    b, m = inputs()
    b['claims'][0]['grounding_review_status'] = status
    fixture_review(b, m)
    with pytest.raises(ValueError, match='verified grounding'):
        compile_projection(b, m, **SCOPE)


@pytest.mark.parametrize('classification_status', [None, 'draft', 'rejected'])
def test_reasoning_does_not_require_classification_or_topics(tmp_path, checker, classification_status):
    b, m = inputs()
    b.pop('topics', None)
    for claim in b['claims']:
        for key in ('topic_id', 'classification', 'classification_review_status'):
            claim.pop(key, None)
        if classification_status is not None:
            claim['classification_review_status'] = classification_status
    for build in b['builds']:
        build.pop('output_topic_ids', None)
    fixture_review(b, m)
    e = run(tmp_path, b, m, checker)
    assert e.explain(GOAL)['status'] == 'inferred'
    assert e.explain(GOAL)['certificate']['status'] == 'accepted'


def test_topics_and_summaries_are_not_converted_to_facts():
    b, m = inputs()
    original = compile_projection(b, m, **SCOPE)
    b['topics'] = [{'text': 'Alice is exempt from all obligations.', 'invalid': True}]
    b['summaries'] = [{'text': 'All clients are exempt.', 'invalid': True}]
    fixture_review(b, m)
    actual = compile_projection(b, m, **SCOPE)
    for key in ('asserted', 'rules', 'citations', 'active'):
        assert actual[key] == original[key]


def test_normal_pipeline_validation_still_requires_classification():
    from scripts.validate_artifact_examples import check
    b, _ = inputs()
    b.pop('topics', None)
    with pytest.raises(ValidationError):
        check(b)


@pytest.mark.parametrize('defect', ['span', 'revision', 'document', 'build'])
def test_decoupled_inputs_still_validate_evidence_lineage(defect):
    b, m = inputs()
    if defect == 'span':
        b['claims'][0]['evidence'][0]['span']['end'] = 999999
    elif defect == 'revision':
        b['claims'][0]['evidence'][0]['element_revision'] = '0' * 64
    elif defect == 'document':
        b['claims'][0]['evidence'][0]['document_id'] = 'doc:missing'
    else:
        b['claims'][0]['build_id'] = 'build:missing'
    fixture_review(b, m)
    with pytest.raises(ValueError):
        compile_projection(b, m, **SCOPE)


def test_stale_claim_bundle_and_semantic_review_rejected():
    b, m = inputs()
    m['bundle_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='bundle binding'):
        compile_projection(b, m, **SCOPE)
    b, m = inputs()
    m['mappings'][0]['claim_sha256'] = '0' * 64
    m['review']['content_sha256'] = content_digest(m)
    with pytest.raises(ValueError, match='stale or missing mapped claim'):
        compile_projection(b, m, **SCOPE)
    b, m = inputs()
    m['mappings'][0]['conditions'] = []
    with pytest.raises(ValueError, match='stale semantic review'):
        compile_projection(b, m, **SCOPE)
    m['review']['status'] = 'draft'
    with pytest.raises(ValueError, match='not reviewed'):
        compile_projection(b, m, **SCOPE)


def test_unknown_fields_and_unsafe_iris_fail_closed():
    b, m = inputs()
    m['mappings'][0]['conditons'] = []
    with pytest.raises(ValidationError):
        compile_projection(b, m, **SCOPE)
    b, m = inputs()
    m['mappings'][2]['facts'][0][0] = 'urn:bad> . <urn:injected'
    fixture_review(b, m)
    with pytest.raises(ValueError, match='absolute HTTP'):
        compile_projection(b, m, **SCOPE)


def test_provenance_and_shacl_preserve_source_version_and_spans():
    b, m = inputs()
    p = compile_projection(b, m, **SCOPE)
    assert len(p['citations']) == 5
    legal = [c for c in p['citations'].values() if c['source']['class'] != 'synthetic test scenario']
    assert all(c['source']['version'] == 'LEGIARTI000033517537' for c in legal)
    assert all(c['reference']['span'] for c in legal)
    assert shacl_validate(p['provenance'], SHAPES)['conforms'] is True
    broken = '\n'.join(line for line in p['provenance'].splitlines() if f'<{KF}evidence>' not in line)
    assert shacl_validate(broken, SHAPES)['conforms'] is False
    assert shacl_validate('', SHAPES)['conforms'] is None


def test_reviewed_example_matches_saved_originals():
    from lxml import html
    b, _ = inputs()
    manifest = json.loads((ROOT / 'data/manifest.json').read_text())['sources']
    for doc in b['documents']:
        if doc['source_id'] == 'src:reasoning-demo':
            path = EXAMPLE / 'scenario.md'
        else:
            original = next(s for s in manifest if s['source_id'] == doc['source_id']
                            and s['source_version'] == doc['source_version'])
            path = ROOT / original['local_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc['content_sha256']
        for element in (e for e in b['elements'] if e['document_id'] == doc['id']):
            if element['location']['format'] == 'html':
                selected = html.fromstring(path.read_bytes()).cssselect(element['location']['selector'])
                assert len(selected) == 1
                assert ' '.join(selected[0].text_content().split()) == element['text']
            else:
                assert element['text'] == path.read_text()


@pytest.mark.parametrize('scope', [{'jurisdiction': 'DE', 'as_of': '2026-10-05'},
                                  {'jurisdiction': 'FR', 'as_of': '2016-12-02'},
                                  {'jurisdiction': 'FR', 'as_of': '2026-11-01'}])
def test_scope_excludes_facts_and_rules(tmp_path, scope):
    b, m = inputs()
    for row in m['mappings']:
        row['valid_until'] = '2026-11-01'
    fixture_review(b, m)
    e = ReasoningEngine(b, m, **scope)
    assert e.run(tmp_path / 'run')['status'] == 'insufficient'
    assert not e.projection['asserted'] and not e.projection['rules']
    assert e.explain(GOAL)['status'] == 'insufficient'


def test_missing_checker_cannot_be_reported_as_verified(tmp_path):
    e = run(tmp_path)
    assert e.report['reasoning']['derived_triples'] == 2
    assert e.report['reasoning']['check']['status'] == 'unchecked_no_checker'
    assert e.explain(GOAL)['status'] == 'unchecked'
    assert not e.explain(GOAL)['conclusions']


def test_real_deductions_certificates_provenance_and_storage(tmp_path, checker):
    e = run(tmp_path, checker=checker)
    assert e.report['status'] == 'ready'
    proof = e.explain(GOAL)
    assert proof['status'] == 'inferred'
    assert proof['certificate']['verdict'] == 'entailed_under_supplied_rules'
    assert proof['rules'][0]['name'] == 'ultimate-control:0'
    assert len(proof['premises']) == 4 and len(proof['citations']) == 5
    assert len(proof['claim_ids']) == 2
    assert e.query(f'ASK {{ <{ALICE}> <{KF}beneficialOwnerOf> <{CLIENT}> }}')['boolean']
    assert not e.query(f'ASK {{ <{ALICE}> <{KF}beneficialOwnerOf> <{CLIENT}> }}', include_inferred=False)['boolean']
    assert e.verify_certificate()['status'] == 'accepted'
    from open_ontologies_lite import OntologyEngine
    reopened = OntologyEngine()
    reopened.load_path(str(tmp_path / 'run/closure.trig'))
    assert reopened.query(f'ASK {{ <{ALICE}> <{KF}beneficialOwnerOf> <{CLIENT}> }}')['boolean']
    with pytest.raises(FileExistsError):
        e.run(tmp_path / 'run')


def test_forged_conclusion_rejected_by_real_checker(tmp_path, checker):
    e = run(tmp_path, checker=checker)
    path = tmp_path / 'run/certificate/horn.tsv'
    lines = path.read_text().splitlines()
    fields = lines[0].split('\t')
    offset = 2 + 2 * int(fields[1])
    fields[offset + 2] = '<urn:kf:forged:OtherClient>'
    lines[0] = '\t'.join(fields)
    path.write_text('\n'.join(lines) + '\n')
    assert check_horn(path.parent, checker=checker)['status'] == 'rejected'
    assert e.verify_certificate()['status'] == 'binding_mismatch'
    assert e.explain(GOAL)['status'] == 'unchecked'


def test_forged_assertions_are_not_a_new_trusted_basis(tmp_path, checker):
    e = run(tmp_path, checker=checker)
    path = tmp_path / 'run/certificate/asserted.tsv'
    path.write_text(path.read_text() + '<urn:forged>\t<urn:p>\t<urn:o>\n')
    assert e.verify_certificate()['status'] == 'binding_mismatch'


def test_missing_conditions_and_unknown_goals_mean_insufficient(tmp_path, checker):
    b, m = inputs()
    m['mappings'][2]['facts'] = [f for f in m['mappings'][2]['facts'] if f[1] != KF + 'determinationRequirementsMetFor']
    fixture_review(b, m)
    e = run(tmp_path, b, m, checker)
    assert e.explain(GOAL)['status'] == 'insufficient'
    assert e.explain(['urn:unknown', KF + 'beneficialOwnerOf', CLIENT])['status'] == 'insufficient'


@pytest.mark.parametrize('exception,clearance,expected', [(False, False, 'insufficient'),
                                                        (True, False, 'insufficient'),
                                                        (False, True, 'inferred'),
                                                        (True, True, 'contradiction')])
def test_exception_needs_positive_clearance(tmp_path, checker, exception, clearance, expected):
    b, m = inputs()
    m['mappings'][0]['exceptions'] = [{'when': ['?person', TYPE, KF + 'Exempt'],
                                     'clearance': ['?person', TYPE, KF + 'ExemptionCleared']}]
    facts = m['mappings'][2]['facts']
    if exception:
        facts.append([ALICE, TYPE, KF + 'Exempt'])
    if clearance:
        facts.append([ALICE, TYPE, KF + 'ExemptionCleared'])
    fixture_review(b, m)
    e = run(tmp_path, b, m, checker)
    assert e.explain(GOAL)['status'] == expected


@pytest.mark.parametrize('conflict', ['disjoint', 'obligation', 'identity'])
def test_contradictions_withhold_conclusions(tmp_path, conflict):
    b, m = inputs()
    facts = m['mappings'][2]['facts']
    if conflict == 'disjoint':
        facts.extend([[ALICE, TYPE, KF + 'Eligible'], [ALICE, TYPE, KF + 'Ineligible'],
                      [KF + 'Eligible', OWL + 'disjointWith', KF + 'Ineligible']])
    elif conflict == 'obligation':
        facts.extend([[ALICE, KF + 'requires', KF + 'KYC'], [ALICE, KF + 'prohibits', KF + 'KYC']])
    else:
        facts.extend([[ALICE, OWL + 'sameAs', CLIENT], [ALICE, OWL + 'differentFrom', CLIENT]])
    fixture_review(b, m)
    e = run(tmp_path, b, m)
    assert e.report['status'] == 'contradiction' and e.report['conflicts']
    assert not e.explain(GOAL)['conclusions']


def test_consistency_checks_inferred_types(tmp_path):
    b, m = inputs()
    m['mappings'][2]['facts'].extend([
        [KF + 'NaturalPerson', 'http://www.w3.org/2000/01/rdf-schema#subClassOf', KF + 'Eligible'],
        [ALICE, TYPE, KF + 'Ineligible'],
        [KF + 'Eligible', OWL + 'disjointWith', KF + 'Ineligible']])
    fixture_review(b, m)
    e = run(tmp_path, b, m)
    assert e.report['status'] == 'contradiction'
    assert e.query(f'ASK {{ <{ALICE}> a <{KF}Eligible> }}')['boolean']
    assert not e.query(f'ASK {{ <{ALICE}> a <{KF}Eligible> }}', include_inferred=False)['boolean']


def test_owl_rdfs_uses_upstream_builtin_table(tmp_path, checker):
    b, m = inputs()
    m['mappings'] = [m['mappings'][2]]
    m['mappings'][0]['facts'] = [[ALICE, TYPE, KF + 'RegulatedEntity'],
                                [KF + 'RegulatedEntity', 'http://www.w3.org/2000/01/rdf-schema#subClassOf', KF + 'Entity'],
                                [KF + 'controls', TYPE, OWL + 'TransitiveProperty'],
                                [ALICE, KF + 'controls', 'urn:intermediate'],
                                ['urn:intermediate', KF + 'controls', CLIENT]]
    fixture_review(b, m)
    e = run(tmp_path, b, m, checker)
    assert e.explain([ALICE, TYPE, KF + 'Entity'])['certificate']['verdict'] == 'entailed'
    assert e.explain([ALICE, KF + 'controls', CLIENT])['status'] == 'inferred'


def test_incomplete_closure_is_not_ready(tmp_path, checker):
    e = run(tmp_path, checker=checker, max_iterations=1)
    assert e.report['status'] == 'incomplete'
    assert e.explain(GOAL)['status'] == 'incomplete'


def test_multistep_proof_retains_transitive_premises_and_rules(tmp_path, checker):
    b, m = inputs()
    m['mappings'][0]['rules'].append({
        'body': [['?person', KF + 'beneficialOwnerOf', '?client']],
        'head': ['?person', KF + 'subjectOfDemoReviewFor', '?client']})
    fixture_review(b, m)
    e = run(tmp_path, b, m, checker)
    proof = e.explain([ALICE, KF + 'subjectOfDemoReviewFor', CLIENT])
    assert proof['status'] == 'inferred'
    assert len(proof['proof']) == 2 and len(proof['rules']) == 2
    assert len(proof['premises']) == 4 and len(proof['citations']) == 5


@pytest.mark.parametrize('options', [{'max_iterations': 0}, {'max_iterations': True},
                                   {'timeout': float('inf')}, {'timeout': -1}])
def test_invalid_execution_bounds_rejected_before_writing(tmp_path, options):
    with pytest.raises(ValueError):
        run(tmp_path, **options)
    assert not (tmp_path / 'run').exists()


def test_sparql_external_queries_are_blocked(tmp_path):
    e = run(tmp_path)
    with pytest.raises(ValueError, match='external dataset'):
        e.query('SELECT * WHERE { SERVICE <https://example.org/> { ?s ?p ?o } }')


class MemoryStore:
    """Exercise the actual Haystack/assistant boundary without PostgreSQL."""
    def __init__(self):
        b, _ = inputs()
        structural = [{'id': 'passage:' + c['id'], 'text': c['text'], 'original_chunk_ids': [c['id']],
                       'evidence_memberships': [{'reference': r, 'original_chunk_ids': [c['id']]} for r in c['evidence']]}
                      for c in b['chunks']]
        self.records, rows = project({'bundle': b, 'claims': b['claims'], 'summaries': [], 'structural': structural,
                                      'hierarchy': [], 'links': [], 'embeddings': [], 'summary_embeddings': []})
        self.rows = {r['id']: r for r in rows}
        self.saved = []

    def snapshot(self, corpus, snapshot_id=None):
        return {'id': 'snapshot:demo', 'identity': {'sources': inputs()[0]['sources']}}

    def search(self, snapshot, query, kind, method, search, vector, source_ids, document_ids):
        return [r for r in self.records if r['kind'] == kind
                and (not source_ids or set(r['source_ids']) & set(source_ids))
                and (not document_ids or set(r['document_ids']) & set(document_ids))]

    def evidence(self, snapshot_id, ids):
        return {k: copy.deepcopy(self.rows[k]) for k in ids if k in self.rows}

    def rank_evidence(self, snapshot_id, query, rows):
        return rows

    def history(self, *args):
        return []

    def save_turn(self, session_id, token, snapshot_id, question, answer):
        self.saved.append(answer)
        return 'message:demo'


def assistant(tmp_path, engine, *, answer_stream=None):
    config = load_config()
    config['search'].update(method='keyword', rerank=False)
    config['answer']['provider'] = 'extractive' if answer_stream is None else 'openai'
    return DocumentAssistant(MemoryStore(), config, reasoning_engine=engine, answer_stream=answer_stream)


def test_assistant_end_to_end_returns_proof_and_original_citations(tmp_path, checker):
    e = run(tmp_path, checker=checker)
    def answer_stream(payload, config):
        data = json.loads(payload['input'])
        assert data['reasoning']['conclusions'] == [GOAL]
        assert data['reasoning']['rules'] and data['reasoning']['premises']
        assert data['reasoning']['verdict'] == 'entailed_under_supplied_rules'
        yield {'type': 'delta', 'text': 'Alice is a beneficial owner under the supplied assumptions [E1].'}
    a = assistant(tmp_path, e, answer_stream=answer_stream)
    result = a.ask('demo', 'Who is a beneficial owner?', reasoning_goal=GOAL, token='t', session_id='s')
    assert result['status'] == 'draft'
    assert result['retrieval']['reasoning']['status'] == 'inferred'
    assert len(result['retrieval']['reasoning']['citations']) == 5
    assert result['citations'][0]['source']['uri'].startswith('https://www.legifrance')
    assert a.store.saved[-1] == result


@pytest.mark.parametrize('cause', ['filters', 'missing', 'changed', 'count', 'tokens', 'unknown', 'unchecked'])
def test_assistant_withholds_incomplete_proof_without_model_call(tmp_path, checker, cause):
    e = run(tmp_path, checker=checker if cause != 'unchecked' else '/missing/oo-horn')
    def forbidden(*args):
        raise AssertionError('unverified symbolic answer must not call the model')
    a = assistant(tmp_path, e, answer_stream=forbidden)
    options = {}
    goal = GOAL
    if cause == 'filters':
        options['source_ids'] = ['src:reasoning-demo']
    elif cause == 'missing':
        del a.store.rows[next(iter(a.store.rows))]
        # Ordinary retrieval must still have complete lineage; exclude that
        # navigation record so only proof resolution encounters the missing span.
        a.store.records = [r for r in a.store.records if set(r['evidence_ids']) <= a.store.rows.keys()]
    elif cause == 'changed':
        next(iter(a.store.rows.values()))['text'] = 'different snapshot text'
    elif cause == 'count':
        a.config['search']['max_context_evidence'] = 1
    elif cause == 'tokens':
        # Enough for the source context, too little for the complete derivation.
        a.config['budget']['input_tokens'] = 1600
    elif cause == 'unknown':
        goal = ['urn:unknown', GOAL[1], GOAL[2]]
    result = a.ask('demo', 'Who is a beneficial owner?', reasoning_goal=goal, token='t', session_id='s', **options)
    assert result['status'] == 'insufficient' and not result['citations']


def test_disabled_reasoning_keeps_existing_payload(tmp_path):
    a = assistant(tmp_path, None)
    result = a.retrieve('demo', 'Who is a beneficial owner?')
    assert 'reasoning' not in result and 'reasoning' not in json.loads(result['payload']['input'])
    with pytest.raises(ValueError, match='explicitly configured'):
        a.retrieve('demo', 'Who?', reasoning_goal=GOAL)


def test_explicit_goal_cannot_be_routed_around_reasoning_gate(tmp_path, checker):
    e = run(tmp_path, checker=checker)
    a = assistant(tmp_path, e)
    def inventory_router(*args):
        raise AssertionError('a standalone explicit goal needs no inventory routing call')
    a.query_stream = inventory_router
    result = a.ask('demo', 'List the documents', reasoning_goal=GOAL, token='t', session_id='s')
    assert result['retrieval']['reasoning']['status'] == 'inferred'
    with pytest.raises(ValueError, match='mutually exclusive'):
        a.ask('demo', 'List documents', intent='document_inventory', reasoning_goal=GOAL, token='t', session_id='s')


def test_opt_in_configuration_builds_engine(tmp_path, checker):
    config = load_config()
    config['reasoning'] = {'enabled': True, 'bundle': str(EXAMPLE / 'artifact-bundle.json'),
                           'mapping': str(EXAMPLE / 'mapping.json'), 'output_dir': str(tmp_path),
                           **SCOPE, 'checker': checker}
    a = DocumentAssistant(MemoryStore(), config)
    assert a.reasoning_engine.report['status'] == 'ready'
