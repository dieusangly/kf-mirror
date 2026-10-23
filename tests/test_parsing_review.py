"""Synthetic judgments exercise mechanics only; never real corpus approval."""
import copy
import json
from pathlib import Path

import pytest

from knowledge_foundry import parsing_review as p

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / 'fixture.pdf'
    source.write_bytes(b'fixture bytes only; no real approval')
    folder = tmp_path / 'parsed/document'
    elements = [{'id': str(page), 'kind': 'table' if page == 7 else 'paragraph',
                 'text': 'Synthetic equipment', 'location': {'format': 'pdf', 'page': page}}
                for page in range(1, 11)]
    write(folder / 'artifact-bundle.json', {'sources': [{'id': 'src:equipment', 'format': 'pdf'}],
          'documents': [{'content_sha256': p.digest(source)}], 'elements': elements})
    write(folder / 'parse.json', {'warnings': [{'code': 'test_warning', 'page': 4}]})
    manifest = write(tmp_path / 'manifest.json', {'sources': [{'manifest_id': 'E', 'source_id': 'src:equipment',
                     'local_path': 'fixture.pdf', 'sha256': p.digest(source)}]})
    outcomes = [{'manifest_id': 'E', 'status': 'parsed', 'page_count': 10, 'output': str(folder)}]
    audit = {'gate': {'status': 'pass'}, 'test_only': True}
    def prepare(**kwargs):
        return p.prepare(audit, outcomes, tmp_path, manifest, ROOT / 'config/review-policy.json', 2, **kwargs)
    return tmp_path, source, folder, audit, prepare


def judged(report):
    review = p.seed(report)
    review['reviewer'] = {'kind': 'agent', 'name': 'Fixture ONLY', 'model': 'fixture-model'}
    for row in review['reviews']:
        row.update(status='pass', notes='Synthetic inspection, not real approval',
                   inspection={'original_inspected': True, 'layout_inspected': True})
    return review


def test_selection_covers_tables_warnings_required_pages_and_spread(inputs):
    root, _, _, _, prepare = inputs
    report = prepare(required_locations=[{'source_id': 'src:equipment', 'page': 8}])
    assert {s['location']['page'] for s in report['review_sample']} >= {1, 4, 7, 8}
    assert report['automatic_gate'] == 'pass' and report['gate'] == 'pending_review'
    assert report['coverage'][0]['total_units'] == 10
    with pytest.raises(ValueError, match='required review source unavailable'):
        prepare(required_locations=[{'source_id': 'src:absent', 'page': 1}])
    with pytest.raises(ValueError, match='page unavailable'):
        prepare(required_locations=[{'source_id': 'src:equipment', 'page': 11}])


def test_pending_failed_and_automatic_failure_gates(inputs):
    root, _, _, audit, prepare = inputs
    report = prepare()
    path = write(root / 'review.json', p.seed(report))
    assert prepare(review_path=path)['gate'] == 'pending_review'
    review = judged(report)
    write(path, review)
    assert prepare(review_path=path)['gate'] == 'pass'
    review['reviews'][0].update(status='fail', severity='critical', notes='Incorrect equipment table association')
    write(path, review)
    assert prepare(review_path=path)['gate'] == 'fail'
    audit['gate']['status'] = 'fail'
    assert prepare()['automatic_gate'] == 'fail' and prepare()['gate'] == 'fail'
    with pytest.raises(ValueError, match='stale parsing review'):
        prepare(review_path=path)


@pytest.mark.parametrize('change', ['artifact', 'source', 'selection', 'reference'])
def test_stale_review_rejected(inputs, change):
    root, source, folder, _, prepare = inputs
    report = prepare()
    path = write(root / 'review.json', judged(report))
    if change == 'artifact':
        write(folder / 'parse.json', {'warnings': []})
    elif change == 'source':
        source.write_bytes(b'changed')
    kwargs = {'required_locations': [{'source_id': 'src:equipment', 'page': 8}]} if change == 'selection' else {}
    if change == 'reference':
        kwargs['reference_path'] = write(root / 'reference.json', {'test_only': True})
    with pytest.raises(ValueError, match='stale parsing review|source hash mismatch'):
        prepare(review_path=path, **kwargs)


@pytest.mark.parametrize('field', ['layout_inspected', 'original_inspected'])
def test_uninspected_judgment_rejected(inputs, field):
    root, _, _, _, prepare = inputs
    review = judged(prepare())
    review['reviews'][0]['inspection'][field] = False
    with pytest.raises(ValueError, match='inspection'):
        prepare(review_path=write(root / 'review.json', review))


def test_additional_findings_block_and_duplicate_samples_rejected(inputs):
    root, _, _, _, prepare = inputs
    review = judged(prepare())
    review['additional_findings'] = [{'source_id': 'src:equipment', 'location': {'page': 3},
                                      'notes': 'Missing equipment condition'}]
    path = write(root / 'review.json', review)
    assert prepare(review_path=path)['gate'] == 'fail'
    review['reviews'].append(copy.deepcopy(review['reviews'][0]))
    write(path, review)
    with pytest.raises(ValueError, match='unique sampled'):
        prepare(review_path=path)


def test_html_review_covers_entire_document(inputs):
    root, _, folder, _, _ = inputs
    source = root / 'fixture.html'
    source.write_text('<main>Complete equipment article</main>')
    manifest = write(root / 'html-manifest.json', {'sources': [{'manifest_id': 'E', 'source_id': 'src:equipment',
                   'local_path': source.name, 'sha256': p.digest(source)}]})
    bundle = json.loads((folder / 'artifact-bundle.json').read_text())
    bundle['sources'][0]['format'] = 'html'
    bundle['documents'][0]['content_sha256'] = p.digest(source)
    for e in bundle['elements']:
        e['location'] = {'format': 'html', 'selector': 'main'}
    write(folder / 'artifact-bundle.json', bundle)
    report = p.prepare({'gate': {'status': 'pass'}}, [{'manifest_id': 'E', 'status': 'parsed',
                       'output': str(folder)}], root, manifest, ROOT / 'config/review-policy.json')
    assert report['coverage'][0]['unit'] == 'whole document'
    assert len(report['review_sample']) == 1
    assert len(report['review_sample'][0]['elements']) == 10
