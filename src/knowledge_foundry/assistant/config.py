"""Configuration for independently replaceable language-model roles."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
ROLES = ('question_interpretation', 'answer_verbalization', 'general')


def local_endpoint(url):
    parsed = urlparse(url)
    return (parsed.scheme in {'http', 'https'} and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)


def load_config(path=None):
    config = json.loads(Path(path or os.environ.get('KF_ASSISTANT_CONFIG') or
                                  ROOT / 'config/reasoning-assistant.json').read_text())
    if config['version'] != 'reasoning-assistant-v1':
        raise ValueError('unsupported assistant configuration version')
    if type(config['local_only']) is not bool:
        raise ValueError('local_only must be a boolean')
    for name in ('candidate_limit', 'max_question_characters', 'history_turns'):
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError('invalid assistant bound: ' + name)
    search = config['scenario_search']
    embedding = search['embedding']
    if embedding['provider'] not in {'openai', 'local'} or not embedding['model']:
        raise ValueError('invalid scenario embedding provider/model')
    if embedding['provider'] == 'openai' and embedding['endpoint'] != 'https://api.openai.com/v1/embeddings':
        raise ValueError('OpenAI embeddings require the official endpoint')
    if embedding['provider'] == 'local' and not local_endpoint(embedding['endpoint']):
        raise ValueError('local embeddings require a loopback endpoint')
    if config['local_only'] and embedding['provider'] != 'local':
        raise ValueError('local_only requires local providers for embeddings too')
    for key in ('dimensions', 'expected_dimensions', 'max_input_tokens', 'max_request_inputs',
                'max_request_tokens', 'timeout_seconds'):
        if type(embedding[key]) is not int or embedding[key] < 1:
            raise ValueError('invalid scenario embedding bound: ' + key)
    if embedding['dimensions'] != embedding['expected_dimensions'] or embedding['retries'] != 0:
        raise ValueError('scenario embeddings require matching dimensions and zero retries')
    if not isinstance(search['cache_dir'], str) or not search['cache_dir']:
        raise ValueError('scenario embedding cache directory is required')
    for role in ROLES:
        step = config['steps'][role]
        if step['provider'] not in {'openai', 'local'} or not isinstance(step['model'], str) or not step['model']:
            raise ValueError('invalid assistant provider/model')
        if step['provider'] == 'openai' and step['endpoint'] != 'https://api.openai.com/v1/responses':
            raise ValueError('OpenAI endpoint must be the official Responses endpoint')
        if step['provider'] == 'local' and not local_endpoint(step['endpoint']):
            raise ValueError('local provider requires a loopback endpoint')
        if config['local_only'] and step['provider'] != 'local':
            raise ValueError('local_only requires local providers for every role')
        if step['reasoning_effort'] not in {None, 'none', 'low', 'medium', 'high', 'xhigh'}:
            raise ValueError('unsupported reasoning effort')
        if step['provider'] == 'openai' and step['reasoning_effort'] is None:
            raise ValueError('OpenAI reasoning effort is required')
        for key in ('max_input_tokens', 'max_output_tokens', 'timeout_seconds'):
            if type(step[key]) is not int or step[key] < 1:
                raise ValueError('invalid role bound: ' + key)
        if step['retries'] != 0:
            raise ValueError('automatic retries are disabled')
    return config
