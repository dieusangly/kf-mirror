"""Locate trusted, configured graph artifacts for the selected claim branch."""
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import ROOT


def graph_path(snapshot, definition, root=ROOT):
    digest = snapshot['identity'].get('claims_manifest_sha256')
    if not digest:
        return None
    for run in definition.get('runs', {}).values():
        if not run.get('claims_dir') or not run.get('graph_path'):
            continue
        directory = (root / run['claims_dir']).resolve()
        path = (root / run['graph_path']).resolve()
        manifest = directory / 'build-manifest.json'
        # Only trusted repository configuration can select executable HTML.
        # Never convert a URL or a database value into a local file path.
        if (path.suffix.lower() == '.html' and path.is_relative_to(directory)
                and path.is_file() and manifest.is_file() and file_hash(manifest) == digest):
            return path
    return None
