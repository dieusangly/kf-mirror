import json
import pytest

from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.provenance import graph_path


def test_graph_requires_trusted_config_and_matching_claim_branch(tmp_path):
    directory = tmp_path / 'claims'
    directory.mkdir()
    manifest = directory / 'build-manifest.json'
    manifest.write_text('{}')
    graph = directory / 'graph.html'
    graph.write_text('<html>Trusted generated graph</html>')
    snapshot = {'identity': {'claims_manifest_sha256': file_hash(manifest), 'graph_url': '/etc/passwd'}}
    definition = {'runs': {'run': {'claims_dir': 'claims', 'graph_path': 'claims/graph.html'}}}
    assert graph_path(snapshot, definition, tmp_path) == graph
    assert graph_path(snapshot, {}, tmp_path) is None
    manifest.write_text('{"different": true}')
    assert graph_path(snapshot, definition, tmp_path) is None
    snapshot['identity']['claims_manifest_sha256'] = file_hash(manifest)
    definition['runs']['run']['graph_path'] = '../outside.html'
    assert graph_path(snapshot, definition, tmp_path) is None


@pytest.mark.parametrize('flat', [False, True])
def test_separate_graph_stage_requires_saved_current_hashes(tmp_path, flat):
    run = tmp_path / 'artifacts/20261023-manual-workflow'
    directory = run / 'claim-classification'
    claims = directory if flat else directory / 'build'
    claims.mkdir(parents=True)
    manifest = claims / 'build-manifest.json'
    manifest.write_text('{}')
    graph = run / 'provenance-graph/graph.html'
    graph.parent.mkdir()
    graph.write_text('<html>Managed graph</html>')
    snapshot = {'identity': {'claims_manifest_sha256': file_hash(manifest)}}
    definition = {'runs': {'run': {'claims_dir': str(claims.relative_to(tmp_path)),
                                  'graph_path': str(graph.relative_to(tmp_path))}}}
    assert graph_path(snapshot, definition, tmp_path) is None
    state = {'stages': {
        'classify': {'directory': str(directory), 'bundle_directory': str(claims), 'status': 'complete',
                     'fingerprints': {str(manifest.relative_to(directory)): file_hash(manifest)}},
        'graph': {'directory': str(graph.parent), 'status': 'complete',
                  'fingerprints': {'graph.html': file_hash(graph)}}}}
    (run / 'state.json').write_text(json.dumps(state))
    assert graph_path(snapshot, definition, tmp_path) == graph
    graph.write_text('changed graph')
    assert graph_path(snapshot, definition, tmp_path) is None
    snapshot['identity']['claims_manifest_sha256'] = file_hash(manifest)
    definition['runs']['run']['graph_path'] = '../outside.html'
    assert graph_path(snapshot, definition, tmp_path) is None
