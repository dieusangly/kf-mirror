"""Read exact repository snapshots from a sealed provenance archive as data."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


class ProvenanceArchive:
    """Verify package bindings before reading indexed files; never extract or execute.

    The local seal supplies integrity, not reviewer authentication or acceptance.
    Historical input pins are checked against archived bytes, never current code.
    """

    def __init__(self, package):
        package = Path(package)
        manifest_bytes = (package / 'manifest.json').read_bytes()
        seal = json.loads((package / 'seal.json').read_bytes())
        _require(_digest(manifest_bytes) == seal['manifest_sha256'], 'provenance manifest seal mismatch')
        self.manifest = json.loads(manifest_bytes)
        index_bytes = (package / 'provenance/index.json').read_bytes()
        _require(_digest(index_bytes) == self.manifest['output_file_sha256']['provenance/index.json'],
                 'provenance index hash mismatch')
        self.index = json.loads(index_bytes)
        _require(self.index['archive'] == 'history.zip' and self.index['repository_prefix'] == 'repository/',
                 'provenance archive identity mismatch')
        self.path = package / 'provenance/history.zip'

    def __enter__(self):
        self.stream = self.path.open('rb')
        try:
            _require(hashlib.file_digest(self.stream, 'sha256').hexdigest() ==
                     self.manifest['output_file_sha256']['provenance/history.zip'],
                     'provenance archive hash mismatch')
            self.stream.seek(0)
            self.archive = zipfile.ZipFile(self.stream)
            names = self.archive.namelist()
            _require(len(names) == len(set(names)), 'duplicate provenance archive member')
            _require(set(names) == {e['archive_member'] for e in self.index['files'].values()},
                     'provenance archive population mismatch')
        except Exception:
            self.stream.close()
            raise
        return self

    def __exit__(self, *exc):
        self.archive.close()
        self.stream.close()

    def location(self, repository_path):
        return str(self.path) + '!/repository/' + repository_path

    def read(self, repository_path, *, expected_sha256=None):
        path = PurePosixPath(repository_path)
        _require(not path.is_absolute() and '..' not in path.parts and
                 str(path) == repository_path and '\\' not in repository_path,
                 'invalid provenance repository path')
        entry = self.index['files'][repository_path]
        member = 'repository/' + repository_path
        _require(entry['archive_member'] == member, 'provenance member identity mismatch')
        _require(self.manifest['input_file_sha256'][repository_path] == entry['sha256'],
                 'provenance historical input hash mismatch')
        data = self.archive.read(member)
        _require(len(data) == entry['bytes'] and _digest(data) == entry['sha256'],
                 'provenance member hash mismatch')
        if expected_sha256 is not None:
            _require(_digest(data) == expected_sha256, 'provenance requested file hash mismatch')
        return data
