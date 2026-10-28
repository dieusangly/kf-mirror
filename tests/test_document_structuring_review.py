"""Three-phase review mechanics; synthetic judgments never approve real sources."""
import copy
import json

import pytest

from knowledge_foundry.chunking import build_collection
from knowledge_foundry.document_structuring import load_validated_structure
from knowledge_foundry.document_structuring_review import ROOT, review_structure
from test_pipeline_commands import parsed_sources, command

SCRIPT = 'deterministic_document_structuring_review.py'


@pytest.fixture
def inputs(parsed_sources):
    root, manifest, config, parsed = parsed_sources
    run = root / 'run'
    chunked = run / 'document-structuring/chunks'
    build_collection(config, parsed, chunked)
    return {'run_dir': run, 'parsed_dir': parsed, 'chunked_dir': chunked,
            'source_root': root, 'manifest': manifest, 'sample_per_document': 2}


def review_files(inputs):
    base = inputs['run_dir'] / 'document-structuring'
    return [base / folder / 'review.json' for folder in ('parsing-review', 'chunk-review')]


def judge(inputs, status='pass'):
    for path in review_files(inputs):
        value = json.loads(path.read_text())
        value['reviewer'] = {'kind': 'agent', 'name': 'Synthetic fixture only',
                             'model': 'fixture-model', 'model_reasoning_effort': 'low'}
        for row in value['reviews']:
            row.update(status=status, notes='Synthetic source inspection; not real-corpus approval.',
                       inspection={'original_inspected': True, 'layout_inspected': False})
            if status == 'fail':
                row['severity'] = 'critical'
        path.write_text(json.dumps(value))


def test_prepare_inspect_finalize_accepts_exact_fixture_and_is_resumable(inputs):
    prepared = review_structure('prepare', **inputs)
    assert prepared['automatic_gate'] == 'pass' and prepared['gate'] == 'pending_review'
    report_path = inputs['run_dir'] / 'document-structuring/document_structuring_review_report.json'
    assert json.loads(report_path.read_text()) == prepared
    assert not (inputs['chunked_dir'] / 'acceptance.json').exists()
    before = [p.read_bytes() for p in review_files(inputs)]
    review_structure('prepare', **inputs)
    assert [p.read_bytes() for p in review_files(inputs)] == before
    pending = review_structure('finalize', **inputs)
    assert pending['gate'] == 'pending_review' and pending['accepted'] is False
    judge(inputs)
    judgments = [p.read_bytes() for p in review_files(inputs)]
    review_structure('prepare', **inputs)
    assert [p.read_bytes() for p in review_files(inputs)] == judgments
    result = review_structure('finalize', **inputs)
    assert result['accepted'] is True
    assert load_validated_structure(inputs['chunked_dir'])['acceptance']['status'] == 'accepted'


def test_failed_source_judgments_never_accept(inputs):
    review_structure('prepare', **inputs)
    judge(inputs, 'fail')
    result = review_structure('finalize', **inputs)
    assert result['gate'] == 'fail' and not result['accepted']
    assert not (inputs['chunked_dir'] / 'acceptance.json').exists()


def test_failed_re_review_revokes_prior_acceptance(inputs):
    review_structure('prepare', **inputs)
    judge(inputs)
    review_structure('finalize', **inputs)
    judge(inputs, 'fail')
    result = review_structure('finalize', **inputs)
    assert result['gate'] == 'fail' and not result['accepted']
    with pytest.raises(ValueError, match='current acceptance'):
        load_validated_structure(inputs['chunked_dir'])


@pytest.mark.parametrize('index', [0, 1])
def test_stale_empty_templates_refresh_in_place_without_backups(inputs, index):
    review_structure('prepare', **inputs)
    path = review_files(inputs)[index]
    value = json.loads(path.read_text())
    value['build_id'] = 'old-template'
    path.write_text(json.dumps(value))
    review_structure('prepare', **inputs)
    assert json.loads(path.read_text())['build_id'] != 'old-template'
    assert {p.name for p in path.parent.iterdir()} == {'review.json', 'report.json'}


@pytest.mark.parametrize('finding', ['judged', 'pending-note', 'additional'])
def test_stale_actual_findings_are_preserved_and_require_new_review_directory(inputs, finding):
    review_structure('prepare', **inputs)
    path = review_files(inputs)[0]
    value = json.loads(path.read_text())
    value['build_id'] = 'old-template'
    if finding == 'judged':
        value['reviews'][0].update(status='fail', severity='major', notes='Actual finding')
    elif finding == 'pending-note':
        value['reviews'][0]['notes'] = 'Image illegible; source review needs another rendering.'
    else:
        value['additional_findings'] = [{'source_id': 'src:equipment', 'location': {}, 'notes': 'Actual finding'}]
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='stale review'):
        review_structure('prepare', **inputs)
    assert path.read_bytes() == before


def test_finalize_requires_prepare_and_current_bindings(inputs):
    with pytest.raises(ValueError, match='missing review'):
        review_structure('finalize', **inputs)
    review_structure('prepare', **inputs)
    path = review_files(inputs)[0]
    value = json.loads(path.read_text()); value['snapshot_sha256'] = 'stale'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='stale review'):
        review_structure('finalize', **inputs)
    assert not (inputs['chunked_dir'] / 'acceptance.json').exists()


def test_single_checkpoint_finalize_leaves_acceptance_to_controller(inputs):
    review_structure('prepare', **inputs)
    judge(inputs)
    result = review_structure('finalize', checkpoint='chunking', **inputs)
    assert result['gate'] == 'pass'
    assert not (inputs['chunked_dir'] / 'acceptance.json').exists()


def test_frozen_output_tampering_blocks_review(inputs):
    review_structure('prepare', **inputs)
    judge(inputs)
    path = inputs['chunked_dir'] / 'document-hierarchy.json'
    path.write_text('[]')
    with pytest.raises(ValueError, match='hash mismatch'):
        review_structure('finalize', **inputs)


def test_finalization_requires_inspection_declarations(inputs):
    review_structure('prepare', **inputs)
    judge(inputs)
    path = review_files(inputs)[0]
    value = json.loads(path.read_text())
    value['reviews'][0]['inspection']['original_inspected'] = False
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='source inspection'):
        review_structure('finalize', **inputs)


def test_cli_prepare_and_finalize_have_distinct_exit_statuses(inputs):
    args = ['--run-dir', inputs['run_dir'], '--parsed-dir', inputs['parsed_dir'],
            '--source-root', inputs['source_root'], '--manifest', inputs['manifest'],
            '--sample-per-document', 2]
    prepared = command(SCRIPT, 'prepare', *args)
    assert prepared['gate'] == 'pending_review'
    assert command(SCRIPT, 'finalize', *args, expected=1)['accepted'] is False
    judge(inputs)
    assert command(SCRIPT, 'finalize', *args)['accepted'] is True


@pytest.mark.parametrize('extra', [
    {'parsing_review_dir': 'parsing'}, {'chunk_review_dir': 'chunked'},
    {'output': 'parsed-output'}, {'output': 'review-output'},
])
def test_review_outputs_cannot_overwrite_frozen_inputs_or_review_json(inputs, extra):
    params = copy.copy(inputs)
    locations = {'parsing': inputs['parsed_dir'] / 'review', 'chunked': inputs['chunked_dir'] / 'review',
                 'parsed-output': inputs['parsed_dir'] / 'report.json',
                 'review-output': inputs['run_dir'] / 'document-structuring/parsing-review/review.json'}
    params.update({k: locations[v] for k, v in extra.items()})
    with pytest.raises(ValueError, match='separate|overwrite'):
        review_structure('prepare', **params)


def test_invalid_run_ids_and_audit_mode_do_not_touch_sources():
    for run_id in ('../escape', '/absolute', 'a/b'):
        with pytest.raises(ValueError, match='safe directory'):
            review_structure('prepare', run_id=run_id)
    with pytest.raises(ValueError, match='audit-only'):
        review_structure('finalize', run_dir=ROOT, audit_only=True)
