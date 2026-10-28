"""Check documented CLI options against the actual executable entry points."""
from functools import lru_cache
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def documented_commands():
    paths = [*sorted((ROOT / 'doc').glob('*.md')),
             *sorted((ROOT / 'src/knowledge_foundry/prompts').glob('agent_prompt_*'))]
    for path in paths:
        for block in re.findall(r'```(?:sh|bash|shell)\n(.*?)```', path.read_text(), re.S):
            for line in block.replace('\\\n', ' ').splitlines():
                match = re.search(r'(?:\.venv/bin/python|python3?)\s+(scripts/[\w_]+\.py)\b(.*)', line)
                if match:
                    args = shlex.split(match[2], comments=True)
                    yield path.relative_to(ROOT), match[1], args


@lru_cache(maxsize=None)
def cli_options(script, subcommand=None):
    assert (ROOT / script).is_file(), script
    # The artifact validator accepts positional bundles rather than --help.
    args = [] if script == 'scripts/validate_artifact_examples.py' else ([subcommand] if subcommand else []) + ['--help']
    env = {**os.environ, 'OPENAI_API_KEY': ''}
    result = subprocess.run([sys.executable, '-I', script, *args], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return set(re.findall(r'--[a-z][\w-]*', result.stdout))


@pytest.mark.parametrize('document,script,args', list(documented_commands()),
                         ids=lambda value: str(value) if isinstance(value, Path) else None)
def test_documented_cli_options_exist(document, script, args):
    subcommand = args[0] if args and args[0] in {'prepare', 'finalize'} else None
    options = cli_options(script, subcommand)
    flags = {arg.split('=', 1)[0] for arg in args if re.fullmatch(r'--[a-z][\w-]*(?:=.*)?', arg)}
    assert flags <= options, f'{document}: {script}: unsupported options {flags - options}'
