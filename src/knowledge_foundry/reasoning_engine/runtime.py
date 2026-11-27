"""Checked execution of the accepted v1 engine at its recorded checkout path."""
import hashlib
from types import ModuleType

from knowledge_foundry.shared.paths import REPOSITORY_ROOT

_ENGINE_PATH = REPOSITORY_ROOT / 'scripts/aml_cft_reasoning.py'
_ENGINE_SHA256 = '5b57122338ed8fcff3441cf7f605e420f41963ab35597ba3367f9bea10c3014f'


def _load():
    source = _ENGINE_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != _ENGINE_SHA256:
        raise ValueError('accepted reasoning engine hash differs')
    engine = ModuleType('accepted_aml_cft_reasoning_v1')
    engine.__file__ = str(_ENGINE_PATH)
    exec(compile(source, engine.__file__, 'exec'), engine.__dict__)
    return engine


_engine = _load()
IDENTITY = _engine.IDENTITY
check_config = _engine.check_config
check_encoding = _engine.check_encoding
check_queries = _engine.check_queries
solve = _engine.solve
check_result = _engine.check_result


def execute(encoding, query, config):
    """Solve one separate encoding and query and independently check its result."""
    result = solve(encoding, query, config)
    return result, check_result(encoding, query, config, result)
