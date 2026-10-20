"""Small validated runtime configuration, independent of clustering settings."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_config(path=None):
    path = Path(path) if path else ROOT / 'config/retrieval.json'
    value = json.loads(path.read_text())
    if value['search']['method'] not in {'keyword', 'vector', 'combined'}:
        raise ValueError('invalid retrieval method')
    if value['search']['mode'] not in {'passage', 'claim', 'summary'}:
        raise ValueError('invalid retrieval mode')
    for key in ('top_k', 'candidate_k', 'max_context_evidence', 'max_context_depth'):
        if type(value['search'][key]) is not int or value['search'][key] < 1:
            raise ValueError('search bounds must be positive integers: ' + key)
    for key in ('input_tokens', 'output_tokens', 'safety_tokens', 'max_question_tokens', 'history_tokens'):
        if type(value['budget'][key]) is not int or value['budget'][key] < 1:
            raise ValueError('budget must be positive: ' + key)
    if value['budget']['input_tokens'] + value['budget']['output_tokens'] + value['budget']['safety_tokens'] > value['budget']['context_window_tokens']:
        raise ValueError('request budget exceeds configured model window')
    if value['answer']['provider'] not in {'extractive', 'openai'}:
        raise ValueError('answer provider must be extractive or openai')
    return value


def corpora(path=None):
    return json.loads((Path(path) if path else ROOT / 'config/retrieval-corpora.json').read_text())
