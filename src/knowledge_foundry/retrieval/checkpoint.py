"""Durable per-case retrieval results, bound to one evaluation's inputs."""
import fcntl
import json
from pathlib import Path

from knowledge_foundry.embeddings import atomic_json
from knowledge_foundry.structural_chunks import stable_hash


class EvaluationCheckpoint:
    def __init__(self, path, identity, resume=False):
        self.path, self.resume = Path(path), resume
        self.manifest = {'version': 'retrieval-evaluation-checkpoint-v1', 'identity': identity}

    def __enter__(self):
        if self.resume and not (self.path / 'manifest.json').is_file():
            raise ValueError('resume requires an existing evaluation checkpoint')
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = (self.path / '.lock').open('a')
        try:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('evaluation checkpoint is in use by another process') from exc
            manifest = self.path / 'manifest.json'
            if manifest.exists():
                if not self.resume:
                    raise ValueError('evaluation checkpoint exists; use --resume or a new checkpoint path')
                if json.loads(manifest.read_text()) != self.manifest:
                    raise ValueError('evaluation checkpoint inputs or settings changed')
            else:
                if any(p.name != '.lock' for p in self.path.iterdir()):
                    raise ValueError('evaluation checkpoint directory is not empty')
                atomic_json(manifest, self.manifest)
            return self
        except BaseException:
            self.lock.close()
            raise

    def load(self):
        rows = {}
        for path in sorted(self.path.glob('case-*.json')):
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                raise ValueError('evaluation checkpoint result integrity mismatch')
            index, row = value.get('index'), value.get('row')
            if (type(index) is not int or index < 0 or path.name != f'case-{index:06d}.json'
                    or not isinstance(row, dict) or value.get('sha256') != stable_hash(row)):
                raise ValueError('evaluation checkpoint result integrity mismatch')
            rows[index] = row
        return rows

    def save(self, index, row):
        atomic_json(self.path / f'case-{index:06d}.json',
                    {'index': index, 'row': row, 'sha256': stable_hash(row)})

    def __exit__(self, *args):
        self.lock.close()
