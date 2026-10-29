"""Independent, bounded recursive summaries of frozen semantic clusters.

The original bundle and cluster preparation are copied byte-for-byte. Summaries
are draft synthesis with transitive provenance, never claim evidence or topic
classification inputs. No candidate or classified claim files are read.
"""

from collections import Counter
import json
import math
from pathlib import Path
from threading import Lock
import shutil
import time

from jsonschema import Draft202012Validator

from knowledge_foundry.claim_context import (
    ROOT, _file_hash, _json, _jsonl, _write_jsonl, load_preparation,
    pricing_for_step, validate_settings,
)
from knowledge_foundry.concurrency import run_bounded, validate_workers
from knowledge_foundry.embeddings import EmbeddingAdapter, atomic_json
from knowledge_foundry.evidence import evidence_key
from knowledge_foundry.generation_context import RequestCounter, usable_input_budget
from knowledge_foundry.openai_tools import OpenAIResponsesTool, response_payload
from knowledge_foundry.semantic_clusters import cluster_members, cosine, similarity_matrix
from knowledge_foundry.structural_chunks import VERSION as STRUCTURAL_VERSION, stable_hash

VERSION = 'recursive-summaries-v1'
OUTPUT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['text'],
                 'properties': {'text': {'type': 'string', 'minLength': 1}}}


def validate_summary_settings(config):
    if set(config) != {'pipeline_version', 'steps', 'pricing'} or config['pipeline_version'] != VERSION or \
            set(config['steps']) != {'embeddings', 'summary_generation', 'summary_clusters'}:
        raise ValueError('invalid summary configuration shape/version')
    # Reuse the existing verified provider, embedding, budget and price checks;
    # the summary configuration itself has no claim-generation settings.
    generation = config['steps']['summary_generation']
    classification = {k: v for k, v in generation.items() if k != 'budget'}
    validate_settings({'pipeline_version': VERSION, 'pricing': config['pricing'], 'steps': {
        'structural_chunks': {'version': STRUCTURAL_VERSION},
        'semantic_clusters': {'version': 'all-members-v1', 'minimum_similarity': .8,
                              'refinement_step': .05, 'max_refinements': 4},
        'embeddings': config['steps']['embeddings'], 'claim_generation': generation,
        'topic_classification': classification}})
    clustering = config['steps']['summary_clusters']
    if set(clustering) != {'minimum_similarity', 'max_members', 'max_levels'} or \
            type(clustering['minimum_similarity']) not in (float, int) or \
            not -1 <= clustering['minimum_similarity'] <= 1 or \
            type(clustering['max_members']) is not int or clustering['max_members'] < 2 or \
            type(clustering['max_levels']) is not int or not 1 <= clustering['max_levels'] <= 20:
        raise ValueError('invalid summary clustering bounds')


def _lineage(cluster_ids, prepared):
    clusters = {c['id']: c for c in prepared['clusters']}
    original_ids = sorted({cid for sid in cluster_ids for cid in clusters[sid]['original_chunk_ids']})
    chunks = {c['id']: c for c in prepared['bundle']['chunks']}
    structural = {r['id']: r for r in prepared['structural_chunks']}
    evidence = {evidence_key(item['reference']): item['reference'] for sid in cluster_ids
                for structural_id in clusters[sid]['structural_chunk_ids']
                for item in structural[structural_id]['evidence_memberships'] if 'span' in item['reference']}
    return {'semantic_cluster_ids': sorted(cluster_ids), 'original_chunk_ids': original_ids,
            'document_ids': sorted({chunks[cid]['document_id'] for cid in original_ids}),
            'evidence': [evidence[key] for key in sorted(evidence)]}


def _input(level, cluster, inputs, prepared):
    if level > 1:
        return {'level': level, 'input_cluster_id': cluster['id'],
                'summaries': [{key: row[key] for key in ('id', 'level', 'text')} for row in inputs]}
    structural = {r['id']: r for r in prepared['structural_chunks']}
    elements = {e['id']: e for e in prepared['bundle']['elements']}
    records = [structural[sid] for sid in cluster['structural_chunk_ids']]
    return {'level': 1, 'input_cluster_id': cluster['id'],
            'structural_chunks': [{**{key: r[key] for key in ('id', 'text', 'original_chunk_ids', 'evidence_memberships')},
                                   'context_element_ids': r.get('context_element_ids', [])} for r in records],
            'parent_context': [{'element_id': eid, 'text': elements[eid]['text'],
                                'location': elements[eid]['location'], 'citable': False}
                               for eid in sorted({eid for r in records for eid in r.get('context_element_ids', [])})],
            'documents': [d for d in prepared['bundle']['documents'] if d['id'] in cluster['document_ids']]}


def _payload(config, prompt, value):
    step = config['steps']['summary_generation']
    return response_payload(step['model'], step['reasoning_effort'], prompt,
                            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                            'knowledge_summary', OUTPUT_SCHEMA, step['budget']['reserved_output_tokens'])


def _summary_id(record):
    return 'summary:' + stable_hash({k: v for k, v in record.items() if k not in {'id', 'review_status'}})


def _cluster(level, ids, summaries, minimum):
    return {'id': 'summary-cluster:' + stable_hash([VERSION, level, ids]), 'level': level,
            'summary_ids': ids, 'minimum_similarity': minimum,
            'semantic_cluster_ids': sorted({sid for i in ids for sid in summaries[i]['semantic_cluster_ids']})}


def generate_summaries(context_dir, output_dir, config_path=ROOT / 'config/summaries.json',
                       cache_dir=None, fixture_path=None, on_progress=None, workers=4):
    validate_workers(workers)
    started = time.monotonic()
    config = _json(config_path)
    validate_summary_settings(config)
    context_dir, output_dir = Path(context_dir), Path(output_dir)
    cache_dir = Path(cache_dir) if cache_dir else ROOT / 'artifacts/summary-cache'
    if output_dir.exists():
        raise ValueError('summary output directory already exists; choose a fresh directory')
    for left, right in ((output_dir, context_dir), (cache_dir, context_dir), (cache_dir, output_dir)):
        if left.resolve().is_relative_to(right.resolve()) or right.resolve().is_relative_to(left.resolve()):
            raise ValueError('summary input, output and mutable cache directories must be separate')
    prepared = load_preparation(context_dir, verify_requests=False)
    if not prepared['clusters']:
        raise ValueError('summary generation requires semantic clusters')
    prompt = (Path(__file__).parent / 'prompts/summary_generation_v1.txt').read_text()
    fixture = _json(fixture_path) if fixture_path else None
    step = config['steps']['summary_generation']
    if step['provider'] == 'fixture' and fixture is None:
        raise ValueError('fixture summary provider requires --fixture')
    counter = RequestCounter(config, cache_dir / 'request-counts', stage='summary_generation')
    adapter = EmbeddingAdapter(config['steps']['embeddings'], cache_dir / 'embeddings', fixture,
                               pricing=pricing_for_step(config, 'embeddings'), pricing_version=config['pricing']['version'])
    summaries, summary_clusters, embeddings = [], [], []
    telemetry = {'requests': 0, 'failed_requests': 0, 'cache_hits': 0}
    telemetry_lock = Lock()
    usage_by_cluster = {}

    def summarize(job):
        level, cluster, inputs = job
        local_counter = RequestCounter(config, cache_dir / 'request-counts', stage='summary_generation')
        local_telemetry = {'requests': 0, 'failed_requests': 0, 'cache_hits': 0}
        tool = None
        try:
            request = _payload(config, prompt, _input(level, cluster, inputs, prepared))
            tokens = local_counter.check(request, 'summary input cluster ' + cluster['id'])
            key = stable_hash([VERSION, step['version'], request, stable_hash(fixture) if fixture is not None else None])
            cached = cache_dir / 'responses' / (key + '.json')
            if cached.exists():
                saved = _json(cached)
                response = saved['response']
                if saved.get('key') != key or saved.get('response_sha256') != stable_hash(response):
                    raise ValueError('summary response cache integrity mismatch')
                local_telemetry['cache_hits'] += 1
            else:
                for attempt in range(step['retries'] + 1):
                    try:
                        if step['provider'] == 'fixture':
                            # Exact cluster ID or hash of the complete input, never an implicit summary.
                            fixtures = fixture.get('summaries', {})
                            response = fixtures.get(cluster['id'], fixtures.get(stable_hash(json.loads(request['input']))))
                            if response is None:
                                raise ValueError('fixture has no summary for ' + cluster['id'])
                        else:
                            if tool is None:
                                tool = OpenAIResponsesTool(step['model'], step['reasoning_effort'],
                                                           max_output_tokens=step['budget']['reserved_output_tokens'])
                            local_telemetry['requests'] += 1
                            response = tool.json_response(instructions=prompt, input_text=request['input'],
                                                          schema_name='knowledge_summary', schema=OUTPUT_SCHEMA)
                        Draft202012Validator(OUTPUT_SCHEMA).validate(response)
                        if not response['text'].strip():
                            raise ValueError('summary text must be nonempty')
                        break
                    except Exception:
                        local_telemetry['failed_requests'] += 1
                        if attempt == step['retries']:
                            raise
                        time.sleep(min(2 ** attempt, 4))
                atomic_json(cached, {'key': key, 'response': response, 'response_sha256': stable_hash(response)})
            Draft202012Validator(OUTPUT_SCHEMA).validate(response)
            if not response['text'].strip():
                raise ValueError('summary text must be nonempty')
            cluster_ids = cluster['semantic_cluster_ids'] if level > 1 else [cluster['id']]
            record = {'level': level, 'input_cluster_id': cluster['id'],
                      'input_summary_ids': [r['id'] for r in inputs], **_lineage(cluster_ids, prepared),
                      'text': response['text'], 'review_status': 'draft', 'input_tokens': tokens,
                      'generation': {**{k: step[k] for k in ('version', 'provider', 'model', 'reasoning_effort')},
                                     'prompt_sha256': stable_hash(prompt), 'request_sha256': stable_hash(request)}}
            record['id'] = _summary_id(record)
            return record
        finally:
            with telemetry_lock:
                for key, value in local_telemetry.items():
                    telemetry[key] += value
                for key, value in local_counter.telemetry.items():
                    if type(value) is int:
                        counter.telemetry[key] += value
                if tool is not None:
                    usage_by_cluster[(level, cluster['id'])] = list(tool.usage_records)

    def summarize_level(jobs):
        records = []

        def save(record):
            records.append(record)
            summaries.append(record)
            if on_progress:
                on_progress({'state': 'summary_created', 'level': record['level'], 'summary_id': record['id'],
                             'input_cluster_id': record['input_cluster_id']})

        run_bounded(summarize, jobs, workers, save)
        return sorted(records, key=lambda record: record['id'])

    output_dir.mkdir(parents=True)
    try:
        shutil.copytree(context_dir, output_dir / 'preparation')
        (output_dir / 'summary-prompt.txt').write_text(prompt)
        atomic_json(output_dir / 'model-output-schema.json', OUTPUT_SCHEMA)
        frontier = summarize_level((1, c, []) for c in prepared['clusters'])
        settings = config['steps']['summary_clusters']
        stop_reason = 'max_levels'
        for level in range(2, settings['max_levels'] + 1):
            if len(frontier) == 1:
                stop_reason = 'single_root'
                break
            rows = adapter.embed(sorted(frontier, key=lambda r: r['id']))
            embeddings.extend({'round': level, 'summary_id': row['structural_chunk_id'],
                               **{k: v for k, v in row.items() if k != 'structural_chunk_id'}} for row in rows)
            scores = similarity_matrix({r['structural_chunk_id']: r['vector'] for r in rows})
            by_id = {r['id']: r for r in frontier}
            def fits(ids):
                if len(ids) == 1:
                    return True  # Singleton summaries carry forward unchanged.
                if len(ids) > settings['max_members']:
                    return False
                group = _cluster(level, ids, by_id, 1.)
                return counter.count(_payload(config, prompt, _input(level, group,
                    [by_id[i] for i in ids], prepared))) <= usable_input_budget(step['budget'])
            groups = cluster_members(by_id, scores, settings['minimum_similarity'], fits)
            if all(len(group) == 1 for group in groups):
                stop_reason = 'no_eligible_merges'
                break
            next_frontier, jobs = [], []
            for ids in groups:
                if len(ids) == 1:
                    next_frontier.append(by_id[ids[0]])
                    continue
                minimum = min(cosine(scores, a, b) for i, a in enumerate(ids) for b in ids[i + 1:])
                group = _cluster(level, ids, by_id, minimum)
                summary_clusters.append(group)
                jobs.append((level, group, [by_id[i] for i in ids]))
            frontier = next_frontier + summarize_level(jobs)
        if len(frontier) == 1:
            stop_reason = 'single_root'
        usage = [record for key in sorted(usage_by_cluster) for record in usage_by_cluster[key]]
        report = {'status': 'complete', 'summaries': len(summaries), 'summary_clusters': len(summary_clusters),
                  'levels': dict(sorted(Counter(r['level'] for r in summaries).items())),
                  'root_summary_ids': sorted(r['id'] for r in frontier), 'stop_reason': stop_reason,
                  'generation': {'provider': step['provider'], 'model': step['model'], **telemetry},
                  'duration_seconds': round(time.monotonic() - started, 3), 'usage_records': usage,
                  'request_token_counting': counter.telemetry, 'embeddings': adapter.telemetry}
        _write_jsonl(output_dir / 'summaries.jsonl', sorted(summaries, key=lambda r: (r['level'], r['id'])))
        atomic_json(output_dir / 'summary-clusters.json', sorted(summary_clusters, key=lambda r: (r['level'], r['id'])))
        _write_jsonl(output_dir / 'summary-embeddings.jsonl', embeddings)
        atomic_json(output_dir / 'report.json', report, indent=2)
        manifest = {'version': 1, 'stage': VERSION, 'config': config,
                    'preparation_manifest_sha256': prepared['manifest_sha256'],
                    'fixture_sha256': _file_hash(fixture_path) if fixture_path else None,
                    'outputs': [{'path': p.name, 'sha256': _file_hash(p)}
                                for p in sorted(output_dir.iterdir()) if p.is_file()]}
        atomic_json(output_dir / 'build-manifest.json', manifest)
        load_summaries(output_dir)
        return report
    except BaseException as exc:
        atomic_json(output_dir / 'failure.json', {'status': 'failed', 'error': str(exc),
                    'generation': telemetry, 'request_token_counting': counter.telemetry, 'embeddings': adapter.telemetry})
        raise


def load_summaries(directory):
    """Verify a saved summary run and transitive lineage entirely offline."""
    directory = Path(directory)
    manifest = _json(directory / 'build-manifest.json')
    schema = _json(ROOT / 'schemas/summary-artifact-schema-v1.json')
    Draft202012Validator(schema).validate(manifest)
    config = manifest['config']
    validate_summary_settings(config)
    required = {'summaries.jsonl', 'summary-clusters.json', 'summary-embeddings.jsonl',
                'summary-prompt.txt', 'model-output-schema.json', 'report.json'}
    if {r['path'] for r in manifest['outputs']} != required or len(manifest['outputs']) != len(required):
        raise ValueError('summary manifest outputs mismatch')
    for row in manifest['outputs']:
        if _file_hash(directory / row['path']) != row['sha256']:
            raise ValueError('summary output hash mismatch: ' + row['path'])
    prepared = load_preparation(directory / 'preparation', verify_requests=False)
    if prepared['manifest_sha256'] != manifest['preparation_manifest_sha256']:
        raise ValueError('summary preparation manifest mismatch')
    summaries = _jsonl(directory / 'summaries.jsonl')
    clusters = _json(directory / 'summary-clusters.json')
    for name, rows in (('summary', summaries), ('summary_cluster', clusters)):
        validator = Draft202012Validator({'$ref': '#/$defs/' + name, '$defs': schema['$defs']})
        for row in rows:
            validator.validate(row)
        if len({r['id'] for r in rows}) != len(rows):
            raise ValueError('duplicate summary or summary cluster ID')
    by_summary, by_cluster = {r['id']: r for r in summaries}, {r['id']: r for r in clusters}
    semantic = {r['id']: r for r in prepared['clusters']}
    frontier, used_clusters = set(), set()
    prompt = (directory / 'summary-prompt.txt').read_text()
    if _json(directory / 'model-output-schema.json') != OUTPUT_SCHEMA:
        raise ValueError('summary model output schema mismatch')
    embedding_rows = _jsonl(directory / 'summary-embeddings.jsonl')
    vectors = {}
    for row in embedding_rows:
        key = (row['round'], row['summary_id'])
        summary = by_summary.get(row['summary_id'])
        embedding = config['steps']['embeddings']
        if key in vectors or summary is None or row['round'] <= summary['level'] or \
                row['input_sha256'] != stable_hash(summary['text']) or \
                row['vector_sha256'] != stable_hash(row['vector']) or \
                len(row['vector']) != (embedding['dimensions'] or embedding['expected_dimensions']) or \
                any(row[k] != embedding[k] for k in ('provider', 'model', 'preprocessing_version')):
            raise ValueError('summary embedding identity mismatch')
        vectors[key] = row['vector']
    leaf_clusters = []
    step = config['steps']['summary_generation']
    for record in sorted(summaries, key=lambda r: (r['level'], r['id'])):
        level, cid = record['level'], record['input_cluster_id']
        if not record['text'].strip() or record['id'] != _summary_id(record) or not 1 <= level <= config['steps']['summary_clusters']['max_levels']:
            raise ValueError('summary identity/level mismatch')
        if level == 1:
            if cid not in semantic or record['input_summary_ids']:
                raise ValueError('level-one summary must come directly from a semantic cluster')
            cluster, inputs, semantic_ids = semantic[cid], [], [cid]
            leaf_clusters.append(cid)
        else:
            cluster = by_cluster.get(cid)
            ids = record['input_summary_ids']
            if cluster is None or cid in used_clusters or not 2 <= len(ids) <= config['steps']['summary_clusters']['max_members'] or \
                    ids != sorted(set(ids)) or cluster['summary_ids'] != ids or cluster['level'] != level or \
                    any(i not in frontier or by_summary[i]['level'] >= level for i in ids):
                raise ValueError('summary cluster input/level membership mismatch')
            inputs = [by_summary[i] for i in ids]
            semantic_ids = sorted({sid for r in inputs for sid in r['semantic_cluster_ids']})
            scores = similarity_matrix({i: vectors[(level, i)] for i in ids})
            minimum = min(cosine(scores, a, b) for i, a in enumerate(ids) for b in ids[i + 1:])
            if cluster != _cluster(level, ids, by_summary, minimum) or minimum < config['steps']['summary_clusters']['minimum_similarity']:
                raise ValueError('summary cluster identity/similarity mismatch')
            frontier.difference_update(ids)
            used_clusters.add(cid)
        expected_lineage = _lineage(semantic_ids, prepared)
        if any(record[k] != value for k, value in expected_lineage.items()):
            raise ValueError('summary source provenance mismatch')
        request = _payload(config, prompt, _input(level, cluster, inputs, prepared))
        generation = {**{k: step[k] for k in ('version', 'provider', 'model', 'reasoning_effort')},
                      'prompt_sha256': stable_hash(prompt), 'request_sha256': stable_hash(request)}
        if record['generation'] != generation or not 0 < record['input_tokens'] <= usable_input_budget(step['budget']):
            raise ValueError('summary generation identity/budget mismatch')
        frontier.add(record['id'])
    if sorted(leaf_clusters) != sorted(semantic) or used_clusters != set(by_cluster):
        raise ValueError('summary cluster coverage mismatch')
    report = _json(directory / 'report.json')
    # Old reports omitted execution metadata. A null duration means it was not
    # measured; never reconstruct historical runtime from file timestamps.
    for key in ('provider', 'model'):
        if key in report.get('generation', {}) and report['generation'][key] != step[key]:
            raise ValueError('summary report generation identity mismatch')
    duration = report.get('duration_seconds')
    if duration is not None and (type(duration) not in (int, float) or
                                 not math.isfinite(duration) or duration < 0):
        raise ValueError('invalid summary report duration_seconds')
    counts = {str(k): v for k, v in sorted(Counter(r['level'] for r in summaries).items())}
    if report.get('status') != 'complete' or report.get('summaries') != len(summaries) or \
            report.get('summary_clusters') != len(clusters) or report.get('levels') != counts or \
            report.get('root_summary_ids') != sorted(frontier):
        raise ValueError('summary report membership/counts mismatch')
    return {'manifest': manifest, 'preparation': prepared, 'summaries': summaries,
            'summary_clusters': clusters, 'report': report}
