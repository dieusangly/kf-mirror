"""The active 13 originals are the sole saved pilot regression corpus."""
import json
from pathlib import Path

import pytest

from scripts.validate_pilot import load_run_ingestion, validate

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'artifacts/20261027-docling-aml-cft-01'


def test_pilot_scope_and_regression_sources_are_exactly_the_active_thirteen():
    manifest = json.loads((ROOT / 'data/manifest.json').read_text())['sources']
    configured = json.loads((ROOT / 'config/pipeline.json').read_text())['manifest_ids']
    assert len(manifest) == len(configured) == 13
    assert {e['manifest_id'] for e in manifest} == set(configured)
    cases = json.loads((ROOT / 'tests/fixtures/structuring-pilot-regressions.json').read_text())
    assert all(c['source_id'] in {e['source_id'] for e in manifest} for c in cases)


@pytest.mark.skipif(not (PILOT / 'document-structuring/chunks/structuring-manifest.json').exists(),
                    reason='run the 13-document pilot before checking its frozen output')
def test_real_pilot_preserves_coverage_anchors_and_legal_definition_scope():
    report = validate(PILOT)
    assert report['documents'] == 13
    assert report['coverage']['authoritative_elements'] == report['coverage']['covered_elements']
    assert report['critical_scope_regressions_checked'] == 3
    assert all(case['pass'] for case in report['development_anchors'])


def test_dated_provider_runs_reuse_the_same_pinned_ingestion_snapshot(tmp_path):
    import hashlib
    from knowledge_foundry.ingestion import ingest

    original = tmp_path / 'source.md'
    original.write_text('# Shared original\n\nReuse these exact bytes.\n')
    raw = original.read_bytes()
    manifest = {'schema_version': '1.0.0', 'sources': [{
        'manifest_id': 'X', 'source_id': 'src:shared', 'source_version': 'v1',
        'title': 'Shared original', 'class': 'test', 'publisher_url': 'https://example.org/source',
        'local_path': original.name, 'retrieved_on': '2026-10-27', 'media_type': 'text/markdown',
        'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    config = tmp_path / 'pipeline.json'
    config.write_text(json.dumps({'root': '.', 'manifest_file': 'manifest.json', 'manifest_ids': ['X']}))
    shared = tmp_path / 'ingested'
    first = ingest(config, shared)
    snapshot = first['artifact_id'].split(':')[1] + '.json'
    for label in ('20261027-docling-aml-cft-01', '20261028-unstructured-aml-cft-01'):
        run = tmp_path / label
        run.mkdir()
        (run / 'run.json').write_text(json.dumps({
            'ingestion_snapshot': '../ingested/snapshots/' + snapshot}))
    # A later ingestion selection changes report.json, but dated runs remain
    # pinned to the original snapshot rather than following that latest pointer.
    manifest['sources'][0]['retrieved_on'] = '2026-10-28'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    assert ingest(config, shared)['artifact_id'] != first['artifact_id']
    original.unlink()
    for label in ('20261027-docling-aml-cft-01', '20261028-unstructured-aml-cft-01'):
        loaded = load_run_ingestion(tmp_path / label)
        assert loaded['artifact_id'] == first['artifact_id']
        assert Path(loaded['outcomes'][0]['artifact_file']).read_bytes() == raw
