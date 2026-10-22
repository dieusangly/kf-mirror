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
