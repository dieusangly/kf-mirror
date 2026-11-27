"""Bounded exception structure without messages, payloads, URLs or local values."""
from pathlib import Path
import traceback


def exception_diagnostic(exc, stage):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 5:
        seen.add(id(exc))
        frames = [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno}
                  for f in traceback.extract_tb(exc.__traceback__)[-8:]]
        item = {'type': type(exc).__name__, 'frames': frames}
        if isinstance(getattr(exc, 'code', None), int):
            item['http_status'] = exc.code
        sqlstate = getattr(exc, 'sqlstate', None)
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum():
            item['sqlstate'] = sqlstate
        chain.append(item)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return {'stage': stage, 'exception_chain': chain,
            'message_policy': 'Exception messages, response bodies and local variables omitted.'}
