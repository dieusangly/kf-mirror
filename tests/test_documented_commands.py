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
def cli_options(script):
    assert (ROOT / script).is_file(), script
    # The artifact validator accepts positional bundles rather than --help.
    args = [] if script == 'scripts/validate_artifact_examples.py' else ['--help']
    env = {**os.environ, 'OPENAI_API_KEY': ''}
    result = subprocess.run([sys.executable, '-I', script, *args], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return set(re.findall(r'--[a-z][\w-]*', result.stdout))


@pytest.mark.parametrize('document,script,args', list(documented_commands()),
                         ids=lambda value: str(value) if isinstance(value, Path) else None)
def test_documented_cli_options_exist(document, script, args):
    options = cli_options(script)
    flags = {arg.split('=', 1)[0] for arg in args if re.fullmatch(r'--[a-z][\w-]*(?:=.*)?', arg)}
    assert flags <= options, f'{document}: {script}: unsupported options {flags - options}'


@pytest.mark.parametrize('extra_dirs,reference', [([], ''), (['subset one', 'subset two'], 'reference fixture.json')])
def test_parsing_review_wrapper_retains_optional_inputs(tmp_path, extra_dirs, reference):
    prompt = (ROOT / 'src/knowledge_foundry/prompts/agent_prompt_parsing_review').read_text()
    wrapper = re.search(r'auto_review_parsing\(\) \{.*?\n\}', prompt, re.S).group()
    capture = tmp_path / 'arguments'
    script = '\n'.join([
        'direnv() { printf "%s\\0" "$@" >> "$CAPTURE"; }',
        'PARSED_DIR=parsed SOURCE_ROOT=. MANIFEST_PATH=manifest REVIEW_POLICY=policy',
        'SAMPLE_PER_DOCUMENT=12 EVALUATION_REPORT=report REQUIRED_PAGES=required',
        'REFERENCE_PATH=' + shlex.quote(reference),
        'ADDITIONAL_INPUT_DIRS=(' + ' '.join(map(shlex.quote, extra_dirs)) + ')',
        wrapper,
        'auto_review_parsing --initialize-review review',
        'auto_review_parsing --review-file review',
    ])
    subprocess.run(['bash', '-c', script], env={**os.environ, 'CAPTURE': str(capture)}, check=True)
    calls = capture.read_bytes().decode().split('\0')[:-1]
    midpoint = len(calls) // 2
    initial, final = calls[:midpoint], calls[midpoint:]
    assert initial.count('--initialize-review') == final.count('--review-file') == 1
    assert [arg.replace('--initialize-review', '--review-file') for arg in initial] == final
    for call in (initial, final):
        assert call.count('--additional-input-dir') == len(extra_dirs)
        assert [call[i + 1] for i, arg in enumerate(call) if arg == '--additional-input-dir'] == extra_dirs
        assert call.count('--reference') == bool(reference)
        if reference:
            assert call[call.index('--reference') + 1] == reference
