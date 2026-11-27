import subprocess
import sys
from pathlib import Path

import pytest

from knowledge_foundry.shared.paths import run_path


def test_checkout_resources_load_outside_working_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'src'))

from knowledge_foundry.retrieval import answering, config, query, ranking
from knowledge_foundry.shared.paths import PACKAGE_ROOT, REPOSITORY_ROOT

assert REPOSITORY_ROOT == config.ROOT == root
assert PACKAGE_ROOT == root / 'src/knowledge_foundry'
assert config.load_config() == config.load_config(root / 'config/retrieval.json')
assert config.corpora() == config.corpora(root / 'config/retrieval-corpora.json')
assert {p.name for p in (PACKAGE_ROOT / 'storage/migrations').glob('*.sql')} == {
    '001_initial.sql', '002_provenance_lookup.sql',
}
for module, name in [(answering, 'rag_answer_v1.txt'), (query, 'rag_query_v2.txt'),
                     (ranking, 'rag_rank_v1.txt')]:
    assert module.PROMPT == (PACKAGE_ROOT / 'prompts' / name).read_text()

explicit = Path('custom-retrieval.json')
explicit.write_bytes((root / 'config/retrieval.json').read_bytes())
assert config.load_config(explicit) == config.load_config()
"""
    result = subprocess.run([sys.executable, '-I', '-c', script, str(root)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_run_path_anchors_a_future_output_without_creating_it(tmp_path):
    run_directory = tmp_path / 'run'
    output = run_path(run_directory, Path('document-structuring/parsing'))
    assert output == run_directory / 'document-structuring/parsing'
    assert not run_directory.exists()


def test_run_path_rejects_absolute_paths_and_parent_traversal(tmp_path):
    for invalid in (tmp_path / 'absolute', 'stage/../../outside'):
        with pytest.raises(ValueError, match='relative to the run directory'):
            run_path(tmp_path / 'run', invalid)
