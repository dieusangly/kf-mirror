"""Locate trusted, configured provenance graphs for the selected snapshot."""
import json
from pathlib import Path
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import ROOT


def _managed_graph(path, claims, root):
    """Accept the separate graph stage only with matching, unchanged run evidence."""
    artifacts = (root / 'artifacts').resolve()
    if not claims.is_relative_to(artifacts):
        return False
    try:
        run = artifacts / claims.relative_to(artifacts).parts[0]
        stages = json.loads((run / 'state.json').read_text())['stages']
        graph, classified = stages['graph'], stages['classify']
        directory = Path(classified['directory']).resolve()
        bundle = Path(classified.get('bundle_directory', directory / 'build')).resolve()
        manifest_key = str((claims / 'build-manifest.json').relative_to(directory))
        return (graph['status'] == classified['status'] == 'complete'
                and bundle == claims
                and Path(graph['directory']).resolve() == path.parent
                and path.is_relative_to(run) and path.name == 'graph.html'
                and graph['fingerprints']['graph.html'] == file_hash(path)
                and classified['fingerprints'][manifest_key] == file_hash(claims / 'build-manifest.json'))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False


def graph_path(snapshot, definition, root=ROOT):
    digest = snapshot['identity'].get('claims_manifest_sha256')
    if not digest:
        return None
    artifacts = (root / 'artifacts').resolve()
    for run in definition.get('runs', {}).values():
        if not run.get('claims_dir') or not run.get('graph_path'):
            continue
        directory = (root / run['claims_dir']).resolve()
        path = (root / run['graph_path']).resolve()
        manifest = directory / 'build-manifest.json'
        # The standalone graph covers the whole run, including summaries and chunks.
        standalone = (directory.name == 'claim-classification'
                      and directory.parent.parent == artifacts
                      and path == directory.parent / 'graphs' / 'knowledge-provenance-graph.html')
        # Only trusted repository configuration can select executable HTML.
        # Never convert a URL or a database value into a local file path.
        if (path.suffix.lower() == '.html' and path.is_file() and manifest.is_file()
                and file_hash(manifest) == digest
                and (path.is_relative_to(directory) or standalone
                     or _managed_graph(path, directory, root))):
            return path
    return None
