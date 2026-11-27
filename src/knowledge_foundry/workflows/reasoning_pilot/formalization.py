"""Invoke the frozen formalization CLI with its original sibling import environment."""
import subprocess
import sys

from knowledge_foundry.shared.paths import REPOSITORY_ROOT
from knowledge_foundry.shared.hashing import file_hash


def run(arguments, *, cwd=None):
    script = REPOSITORY_ROOT / 'scripts/aml_cft_formalization.py'
    if file_hash(script) != '22bbbd231f1a702af3d71e4b407665e7a71e25807541c75492c81a3b2c148254':
        raise ValueError('accepted formalizer hash differs')
    return subprocess.run([sys.executable, str(script), *arguments], cwd=cwd, check=False).returncode
