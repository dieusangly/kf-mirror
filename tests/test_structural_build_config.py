"""Split live settings preserve frozen requests, identities and offline reuse."""
import copy
import json
from unittest.mock import patch

import pytest

from knowledge_foundry.claim_context import (
    ROOT, execution_settings, load_preparation, load_settings, prepare_claim_context,
)
from knowledge_foundry.claim_generation_and_classification import generate_claims
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash
from claim_fixtures import fixture_settings


def write_split(root, config):
    root.mkdir(parents=True, exist_ok=True)
    claims = copy.deepcopy(config)
    build = {'steps': {key: claims['steps'].pop(key) for key in (
        'structural_chunks', 'embeddings', 'semantic_clusters')},
        'request_sizing': {'claim_config': 'claims.json'}}
    claims['structural_build_config'] = 'build.json'
    claim_path, build_path = root / 'claims.json', root / 'build.json'
    claim_path.write_text(json.dumps(claims))
    build_path.write_text(json.dumps(build))
    return claim_path, build_path


def test_default_configs_resolve_to_same_complete_settings():
    claim_path = ROOT / 'config/claim-generation-and-classification.json'
    build_path = ROOT / 'config/structural-chunks-and-semantic-clusters.json'
    claims, build = json.loads(claim_path.read_text()), json.loads(build_path.read_text())
    assert set(claims['steps']) == {'claim_generation', 'topic_classification'}
    assert set(build['steps']) == {'structural_chunks', 'embeddings', 'semantic_clusters'}
    assert load_settings(claim_path) == load_settings(build_path)


def test_relative_references_and_legacy_identity(tmp_path, monkeypatch):
    expected = fixture_settings()
    claim_path, build_path = write_split(tmp_path / 'settings', expected)
    monkeypatch.chdir(tmp_path)
    assert load_settings(claim_path) == expected
    assert load_settings(build_path) == expected
    legacy = tmp_path / 'legacy.json'
    legacy.write_text(json.dumps(expected))
    assert load_settings(legacy) == expected
    assert execution_settings(load_settings(build_path)) == execution_settings(expected)


@pytest.mark.parametrize('change', ['missing', 'mismatch', 'overlap', 'unknown', 'bad_budget', 'bad_sizing'])
def test_invalid_dependencies_fail_before_requests(tmp_path, change):
    claim_path, build_path = write_split(tmp_path, fixture_settings())
    claims, build = json.loads(claim_path.read_text()), json.loads(build_path.read_text())
    if change == 'missing':
        claims['structural_build_config'] = 'absent.json'
    elif change == 'mismatch':
        other = tmp_path / 'other.json'
        other.write_text(json.dumps(claims))
        build['request_sizing']['claim_config'] = other.name
    elif change == 'overlap':
        claims['steps']['embeddings'] = build['steps']['embeddings']
    elif change == 'unknown':
        build['unknown'] = True
    elif change == 'bad_budget':
        claims['steps']['claim_generation']['budget']['operational_input_cap_tokens'] = 0
    else:
        build['request_sizing'] = {'summary_config': 'summaries.json'}
    claim_path.write_text(json.dumps(claims))
    build_path.write_text(json.dumps(build))
    with pytest.raises(ValueError):
        load_settings(claim_path)
    if change == 'mismatch':
        # The build itself explicitly selects the other valid claim config.
        assert load_settings(build_path) == fixture_settings()
        return
    with pytest.raises(ValueError):
        load_settings(build_path)


def test_split_build_matches_legacy_and_freezes_dependencies(tmp_path):
    config = fixture_settings()
    claim_path, build_path = write_split(tmp_path / 'settings', config)
    legacy = tmp_path / 'legacy.json'
    legacy.write_text(json.dumps(config))
    chunked = tmp_path / 'chunked'
    chunked.mkdir()
    bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
    bundle['claims'], bundle['topics'] = [], []
    bundle['builds'][0].update(output_claim_ids=[], output_topic_ids=[])
    (chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
    records, _ = assemble_structural_chunks(bundle)
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps({'embedding_vectors': {
        stable_hash(r['text']): [1., 0.] for r in records}, 'candidate_clusters': {}}))
    legacy_dir, split_dir = tmp_path / 'legacy-build', tmp_path / 'split-build'
    prepare_claim_context(chunked, legacy_dir, legacy, tmp_path / 'cache', fixture)
    report = prepare_claim_context(chunked, split_dir, build_path, tmp_path / 'cache', fixture)
    assert report['embeddings']['requests'] == 0
    assert report['claim_generation_request_token_counting']['requests'] == 0
    for filename in ('structural-chunks.jsonl', 'semantic-clusters.json'):
        assert (legacy_dir / filename).read_bytes() == (split_dir / filename).read_bytes()

    def embeddings(directory):
        return [{key: value for key, value in json.loads(line).items() if key != 'cache_hit'}
                for line in (directory / 'embeddings.jsonl').read_text().splitlines()]

    assert embeddings(legacy_dir) == embeddings(split_dir)
    frozen = json.loads((split_dir / 'build-manifest.json').read_text())
    assert frozen['config'] == config
    first = generate_claims(None, tmp_path / 'generated-legacy', legacy, fixture, context_dir=legacy_dir)
    second = generate_claims(None, tmp_path / 'generated-split', claim_path, fixture, context_dir=legacy_dir)
    assert first['generation_build_id'] == second['generation_build_id']
    # Fresh builds have distinct manifests when their cache-hit telemetry differs.
    assert generate_claims(None, tmp_path / 'generated-fresh', claim_path, fixture,
                           context_dir=split_dir)['status'] == 'complete'
    # Changing consumer sizing invalidates the old plan before any model calls.
    changed = json.loads(claim_path.read_text())
    changed['steps']['claim_generation']['budget']['operational_input_cap_tokens'] -= 1
    claim_path.write_text(json.dumps(changed))
    with patch('knowledge_foundry.claim_generation_and_classification._provider') as provider:
        with pytest.raises(ValueError, match='settings|configuration'):
            generate_claims(None, tmp_path / 'incompatible', claim_path, fixture, context_dir=split_dir)
        provider.assert_not_called()
    claim_path.unlink()
    build_path.unlink()
    assert load_preparation(split_dir)['clusters'] == load_preparation(legacy_dir)['clusters']
