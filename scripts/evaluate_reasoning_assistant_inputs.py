"""Frozen natural-language path runner. No references/queries enter Assistant.ask.

Default: inventory only, no external calls. --execute requires an agreed config,
budget reservation and (when enabled) verified published document snapshot.
Exit 0: inventory/completed requests; 1: failures or unexecuted prompts; 2: input error.
No automatic retries. Resume skips every recorded attempt, including failures.
"""
import argparse
from collections import Counter
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from knowledge_foundry.assistant.config import load_config
from knowledge_foundry.assistant.datasets import digest
from knowledge_foundry.assistant.service import Assistant

DATASET = ROOT / 'artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001'


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_questions(directory=DATASET):
    manifest = read(directory / 'dataset-manifest-v1.json')
    for name, info in manifest['files'].items():
        if digest(directory / name) != info['sha256']:
            raise ValueError('frozen dataset hash differs: ' + name)
    questions = [json.loads(line) for line in (directory / 'questions-v1.jsonl').read_text().splitlines()]
    if len(questions) != 288 or len({q['question_id'] for q in questions}) != 288:
        raise ValueError('expected all 288 unique frozen questions')
    # Read the question file only; manifest source mappings and references never
    # leave this input validation boundary.
    return questions


def identity(config, agreement, directory=DATASET):
    files = list((ROOT / 'src').rglob('*.py'))
    files += list((ROOT / 'scripts').glob('*.py'))
    files += list((ROOT / 'src/knowledge_foundry/prompts').glob('assistant_*.txt'))
    files += [ROOT / 'config/reasoning-vocabulary.json', ROOT / config['datasets_path'],
              ROOT / config['documents']['retrieval_config']]
    registry = read(ROOT / config['datasets_path'])
    for package in registry['datasets']:
        files += [ROOT / package[field] for field in ('inputs_path', 'encodings_path', 'manifest_path',
                  'engine_config_path', 'engine_path', 'schema_path', 'acceptance_path', 'status_path')]
    return {'questions_sha256': digest(directory / 'questions-v1.jsonl'),
            'manifest_sha256': digest(directory / 'dataset-manifest-v1.json'),
            'config': config, 'agreement': agreement,
            'code_and_inputs': {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(files))},
            'dependencies': {name: version(name) for name in ('jsonschema', 'tiktoken', 'psycopg')},
            'git_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}


def validate_agreement(config, agreement):
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if agreement.get('config_sha256') != config_hash:
        raise ValueError('agreement must pin the effective provider/model configuration')
    for field in ('api_budget_usd', 'reserve_usd_per_prompt'):
        value = agreement.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('agreement requires a positive ' + field)
    if not agreement.get('cost_reservation_acknowledged'):
        raise ValueError('agreement must acknowledge reservations are estimates, not a provider billing cap')
    if config['documents']['enabled'] and not config['local_only']:
        if not agreement.get('snapshot_id'):
            raise ValueError('document evaluation requires an agreed published snapshot')
        if agreement.get('retrieval_config_sha256') != digest(ROOT / config['documents']['retrieval_config']):
            raise ValueError('agreement must pin document provider/model and embedding configuration')
    if agreement['reserve_usd_per_prompt'] > agreement['api_budget_usd']:
        raise ValueError('budget cannot fund one prompt reservation')


def execute_questions(assistant, questions, output, run_identity, *, max_prompts, budget, reserve,
                      verify_snapshot=None, resume=False):
    """Injectable for offline orchestration checks; never receives references."""
    output = Path(output)
    run_file, response_file = output / 'run.json', output / 'responses.jsonl'
    if resume:
        if read(run_file)['identity'] != run_identity:
            raise ValueError('resume identity differs; use a fresh output directory')
    else:
        output.mkdir(exist_ok=False, parents=True)
        save(run_file, {'identity': run_identity, 'status': 'running', 'automatic_retries': 0,
                        'cost_limit': 'Per-prompt reservations; not a provider billing cap.'})
        response_file.touch(exist_ok=False)
    attempts = [json.loads(line) for line in response_file.read_text().splitlines()]
    done = {row['question_id'] for row in attempts}
    if len(done) != len(attempts) or not done <= {q['question_id'] for q in questions}:
        raise ValueError('checkpoint contains duplicate or foreign attempts')
    spent = len(attempts) * reserve
    attempted_now = 0
    stop_reason = None
    for question in questions:
        if question['question_id'] in done:
            continue
        if attempted_now >= max_prompts:
            stop_reason = 'requested execution bound'
            break
        if spent + reserve > budget + 1e-12:
            stop_reason = 'API budget reservation exhausted'
            break
        if verify_snapshot:
            try:
                verify_snapshot()
            except Exception:
                stop_reason = 'published snapshot unavailable or changed'
                break
        started = time.monotonic()
        try:
            # This is the entire application submission. No metadata, mappings,
            # reference answers or saved structured queries are passed.
            answer = assistant.ask(question['text'], history=[])
            row = {'question_id': question['question_id'], 'attempt': 1, 'answer': answer,
                   'execution': 'failed' if answer['status'] in {
                       'error', 'unavailable', 'citation_validation_failed'} else 'completed'}
        except Exception as exc:
            # Do not persist exception bodies: they can contain credentials/provider data.
            row = {'question_id': question['question_id'], 'attempt': 1, 'execution': 'failed',
                   'error_type': type(exc).__name__}
        if verify_snapshot:
            try:
                verify_snapshot()
            except Exception:
                row.update(execution='failed', snapshot_check='changed or unavailable after request')
                stop_reason = 'published snapshot changed during request; last attempt requires review'
        row.update(latency_seconds=round(time.monotonic() - started, 3), reserved_usd=reserve)
        with response_file.open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
        attempts.append(row)
        done.add(question['question_id'])
        spent += reserve
        attempted_now += 1
        if stop_reason:
            break
    summary = {'denominator': len(questions), 'attempted': len(attempts),
               'failed': sum(row['execution'] == 'failed' for row in attempts),
               'unexecuted': [{'question_id': q['question_id'], 'reason': stop_reason} for q in questions
                              if q['question_id'] not in done],
               'route_counts': dict(Counter(row.get('answer', {}).get('route', 'error') for row in attempts)),
               'proof_checks': dict(Counter(str(row.get('answer', {}).get('checker', {}).get('valid'))
                                           for row in attempts)),
               'reserved_usd': spent, 'budget_usd': budget, 'stop_reason': stop_reason,
               'quality_scores': None, 'reference_comparison': 'Not performed; references remain provisional.',
               'cost_limitations': ['Reservations bound attempted prompts, not provider billing.',
                    'Per-call LLM costs/usage and policy embedding usage are retained in answer telemetry.',
                    'Document retrieval/embedding/reranking failures may lack usage; no complete cost total is claimed.']}
    save(output / 'summary.json', summary)
    run = read(run_file)
    run['status'] = 'pending-review' if not summary['unexecuted'] else 'partial'
    save(run_file, run)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--agreement', type=Path)
    parser.add_argument('--max-prompts', type=int, default=288)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        questions, config = load_questions(), load_config(args.config)
        if args.max_prompts < 1 or args.max_prompts > 288:
            raise ValueError('max-prompts must be between 1 and 288')
        if not args.execute:
            if args.resume or args.agreement:
                raise ValueError('inventory does not accept execution/resume options')
            args.output.mkdir(parents=True, exist_ok=False)
            save(args.output / 'inventory.json', {'denominator': 288, 'executed': 0, 'external_calls': 0,
                'quality_scores': None, 'questions_sha256': digest(DATASET / 'questions-v1.jsonl'),
                'unexecuted': [{'question_id': q['question_id'], 'reason': 'No live provider/budget execution agreed.'}
                               for q in questions]})
            print('288 frozen prompts verified; 288 unexecuted; no external calls.')
            return 0
        if not args.agreement:
            raise ValueError('--execute requires a separately agreed --agreement JSON')
        agreement = read(args.agreement)
        validate_agreement(config, agreement)
        assistant = Assistant(config)
        verify = None
        snapshot_digest = None
        if assistant.documents:
            snapshot_digest = assistant.documents.pin_snapshot(agreement['snapshot_id'])
            verify = assistant.documents.verify_snapshot
        run_identity = identity(config, agreement) | {'snapshot_digest': snapshot_digest}
        summary = execute_questions(assistant, questions, args.output, run_identity,
            max_prompts=args.max_prompts, budget=agreement['api_budget_usd'],
            reserve=agreement['reserve_usd_per_prompt'], verify_snapshot=verify, resume=args.resume)
        print(json.dumps({k: summary[k] for k in ('denominator', 'attempted', 'failed', 'reserved_usd', 'stop_reason')}))
        return 1 if summary['failed'] or summary['unexecuted'] else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('Evaluation input/configuration error: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
