"""Cluster accepted structural chunks and verify frozen semantic lineage."""

from collections import Counter
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge_foundry.embeddings import EmbeddingAdapter, atomic_json
from knowledge_foundry.generation_context import RequestCounter, generation_payload, usable_input_budget
from knowledge_foundry.semantic_clusters import cluster_members, refine_clusters, similarity_matrix, cosine
from knowledge_foundry.structural_chunks import VERSION, assemble_structural_chunks, stable_hash
from knowledge_foundry.document_structure import assemble_document_hierarchy
from knowledge_foundry.claim_source_context import SOURCE_CONTEXT_VERSION, source_context_index, uses_source_context
from knowledge_foundry.concurrency import run_bounded, validate_workers

ROOT = Path(__file__).resolve().parents[2]


def load_settings(path):
    """Resolve live split configs; frozen artifacts keep self-contained settings."""
    path = Path(path).resolve()
    value = _json(path)
    build_steps = {'structural_chunks', 'embeddings', 'semantic_clusters'}
    claim_steps = {'claim_generation', 'topic_classification'}

    def reference(parent, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('configuration reference must be a nonempty path')
        target = (parent.parent / value).resolve()
        if not target.is_file():
            raise ValueError('referenced configuration does not exist: ' + str(target))
        return target

    # Existing combined live configs and offline fixtures remain supported.
    if isinstance(value, dict) and {'request_sizing', 'structural_build_config'}.isdisjoint(value):
        validate_settings(value)
        return value
    if not isinstance(value, dict):
        raise ValueError('configuration must be an object')
    if 'request_sizing' in value:
        build, build_path = value, path
        sizing = build.get('request_sizing')
        if not isinstance(sizing, dict) or set(sizing) != {'claim_config'}:
            raise ValueError('request_sizing must explicitly select claim_config')
        claim_path = reference(build_path, sizing['claim_config'])
        claim = _json(claim_path)
    else:
        claim, claim_path = value, path
        build_path = reference(claim_path, claim.get('structural_build_config'))
        build = _json(build_path)
    if not isinstance(claim, dict) or set(claim) != {
            'pipeline_version', 'steps', 'pricing', 'structural_build_config'}:
        raise ValueError('invalid split claim configuration shape')
    if not isinstance(build, dict) or set(build) != {'steps', 'request_sizing'}:
        raise ValueError('invalid structural chunk and semantic cluster build configuration shape')
    sizing = build['request_sizing']
    if not isinstance(sizing, dict) or set(sizing) != {'claim_config'}:
        raise ValueError('request_sizing must explicitly select claim_config')
    if reference(claim_path, claim['structural_build_config']) != build_path or \
            reference(build_path, sizing['claim_config']) != claim_path:
        raise ValueError('build and request-sizing configurations must reference each other')
    if not isinstance(build['steps'], dict) or set(build['steps']) != build_steps or \
            not isinstance(claim['steps'], dict) or set(claim['steps']) != claim_steps:
        raise ValueError('split configurations must own their respective steps without overlap')
    resolved = {'pipeline_version': claim['pipeline_version'],
                'steps': {**build['steps'], **claim['steps']}, 'pricing': claim['pricing']}
    validate_settings(resolved)
    return resolved


def execution_settings(config, stage='claim_generation'):
    """Only output-affecting settings participate in execution/cache identity."""
    names = ('topic_classification',) if stage == 'topic_classification' else (
        'structural_chunks', 'embeddings', 'semantic_clusters', 'claim_generation')
    return {'pipeline_version': config['pipeline_version'],
            'steps': {name: config['steps'][name] for name in names}}


def cluster_namespace(config):
    """Keep stable semantic IDs across configuration layout changes."""
    return {'version': 'claim-context-v1',
            'structural_version': config['steps']['structural_chunks']['version'],
            'clustering_version': config['steps']['semantic_clusters']['version']}


def pricing_for_step(config, stage):
    step = config['steps'][stage]
    return config['pricing']['providers'][step['provider']]['models'][step['model']]


def validate_settings(config, frozen=False):
    """Validate the step configuration and the shared provider/model price catalogue."""
    from knowledge_foundry.openai_tools import REASONING_EFFORTS
    if not isinstance(config, dict) or set(config) != {'pipeline_version', 'steps', 'pricing'}:
        raise ValueError('claim generation and classification configuration has an invalid shape')
    if not isinstance(config['pipeline_version'], str) or not config['pipeline_version']:
        raise ValueError('pipeline_version must be nonempty')
    steps = config['steps']
    shapes = {
        'structural_chunks': {'version'},
        'embeddings': {'provider', 'model', 'dimensions', 'expected_dimensions', 'tokenizer', 'max_input_tokens',
                       'max_request_inputs', 'max_request_tokens', 'timeout_seconds', 'retries', 'preprocessing_version'},
        'semantic_clusters': {'version', 'minimum_similarity', 'refinement_step', 'max_refinements'},
        'claim_generation': {'version', 'provider', 'model', 'reasoning_effort', 'retries', 'budget'},
        'topic_classification': {'version', 'provider', 'model', 'reasoning_effort', 'retries'}}
    if not isinstance(steps, dict) or set(steps) != set(shapes):
        raise ValueError('invalid steps configuration shape')
    for name, keys in shapes.items():
        actual = set(steps[name]) if isinstance(steps[name], dict) else set()
        if not isinstance(steps[name], dict) or actual not in (
                [keys, keys | {'source_context'}] if name == 'claim_generation' else [keys]):
            raise ValueError('invalid ' + name + ' configuration shape')
    if steps['claim_generation'].get('source_context', SOURCE_CONTEXT_VERSION) != SOURCE_CONTEXT_VERSION:
        raise ValueError('unsupported claim generation source context')
    versions = {VERSION}
    if steps['structural_chunks']['version'] not in versions or steps['semantic_clusters']['version'] != 'all-members-v1':
        raise ValueError('unsupported structural chunk or semantic clustering version')
    embedding, clustering = steps['embeddings'], steps['semantic_clusters']
    for key in ('expected_dimensions', 'max_input_tokens', 'max_request_inputs', 'max_request_tokens', 'timeout_seconds'):
        if type(embedding[key]) is not int or embedding[key] < 1:
            raise ValueError('invalid embedding ' + key)
    if embedding['dimensions'] is not None and (type(embedding['dimensions']) is not int or embedding['dimensions'] < 1):
        raise ValueError('invalid embedding dimensions')
    for step in (embedding, steps['claim_generation'], steps['topic_classification']):
        if step['provider'] not in {'openai', 'fixture'} or not isinstance(step['model'], str) or not step['model'] or \
                type(step['retries']) is not int or step['retries'] < 0:
            raise ValueError('invalid step provider, model or retries')
    for name in ('claim_generation', 'topic_classification'):
        step = steps[name]
        if step['reasoning_effort'] not in REASONING_EFFORTS or not isinstance(step['version'], str) or not step['version']:
            raise ValueError('invalid ' + name + ' version/reasoning_effort')
    if any(not isinstance(embedding[key], str) or not embedding[key] for key in ('tokenizer', 'preprocessing_version')):
        raise ValueError('invalid embedding tokenizer or preprocessing_version')
    if embedding['provider'] == 'openai':
        if embedding['model'] not in {'text-embedding-3-small', 'text-embedding-3-large', 'text-embedding-ada-002'}:
            raise ValueError('configure a supported verified embedding model')
        maximum = 3072 if embedding['model'] == 'text-embedding-3-large' else 1536
        dimensions = embedding['dimensions'] or embedding['expected_dimensions']
        if dimensions > maximum or (embedding['model'] == 'text-embedding-ada-002' and embedding['dimensions'] is not None):
            raise ValueError('embedding dimensions unsupported by model')
        if embedding['tokenizer'] != 'cl100k_base' or embedding['max_input_tokens'] > 8192 or \
                embedding['max_request_inputs'] > 2048 or embedding['max_request_tokens'] > 300000:
            raise ValueError('embedding limits exceed verified provider limits')
    for key in ('minimum_similarity', 'refinement_step'):
        if type(clustering[key]) not in (int, float) or not math.isfinite(clustering[key]):
            raise ValueError('invalid clustering ' + key)
    if not -1 <= clustering['minimum_similarity'] <= 1 or not 0 < clustering['refinement_step'] <= 2 or \
            type(clustering['max_refinements']) is not int or not 0 <= clustering['max_refinements'] <= 100:
        raise ValueError('invalid clustering bounds')
    budget = steps['claim_generation']['budget']
    if not isinstance(budget, dict) or set(budget) != {'context_window_tokens', 'reserved_output_tokens',
            'safety_margin_tokens', 'operational_input_cap_tokens', 'verified_model', 'documentation_url', 'verified_on'}:
        raise ValueError('invalid generation budget shape')
    for key in ('context_window_tokens', 'reserved_output_tokens', 'safety_margin_tokens'):
        if type(budget[key]) is not int or budget[key] < (0 if key == 'safety_margin_tokens' else 1):
            raise ValueError('invalid generation budget ' + key)
    cap = budget['operational_input_cap_tokens']
    if cap is not None and (type(cap) is not int or cap < 1):
        raise ValueError('invalid operational input cap')
    if usable_input_budget(budget) < 1 or budget['verified_model'] != steps['claim_generation']['model'] or \
            any(not isinstance(budget[k], str) or not budget[k] for k in ('verified_model', 'documentation_url', 'verified_on')):
        raise ValueError('generation budget must match a verified model and have positive usable input')
    if steps['claim_generation']['model'] == 'gpt-6-luna' and (budget['context_window_tokens'] > 1050000 or budget['reserved_output_tokens'] > 128000):
        raise ValueError('generation budget exceeds verified model limits')
    pricing = config['pricing']
    if not isinstance(pricing, dict) or set(pricing) != {'version', 'providers'} or \
            not isinstance(pricing['version'], str) or not pricing['version'] or not isinstance(pricing['providers'], dict):
        raise ValueError('invalid pricing catalogue')
    rate_keys = {'input', 'cached_input', 'cache_write', 'output'}
    for provider, group in pricing['providers'].items():
        if provider not in {'openai', 'fixture'} or not isinstance(group, dict) or set(group) != {'models'} or not isinstance(group['models'], dict):
            raise ValueError('invalid pricing provider')
        for model, card in group['models'].items():
            if not isinstance(model, str) or not model or not isinstance(card, dict):
                raise ValueError('invalid pricing model')
            common = {'verified_on', 'documentation_url'}
            if any(not isinstance(card.get(k), str) or not card[k] for k in common):
                raise ValueError('pricing needs verification date and documentation URL')
            if 'usd_per_million_input_tokens' in card:
                if set(card) != common | {'usd_per_million_input_tokens'}:
                    raise ValueError('invalid embedding pricing shape')
                rates = [card['usd_per_million_input_tokens']]
            else:
                if set(card) != common | {'short_context_limit_tokens', 'short_context_usd_per_million_tokens', 'long_context_usd_per_million_tokens'} or \
                        type(card['short_context_limit_tokens']) is not int or card['short_context_limit_tokens'] < 1:
                    raise ValueError('invalid generation/classification pricing shape')
                rates = []
                for key in ('short_context_usd_per_million_tokens', 'long_context_usd_per_million_tokens'):
                    if not isinstance(card[key], dict) or set(card[key]) != rate_keys:
                        raise ValueError('invalid inference pricing rate keys')
                    rates.extend(card[key].values())
            if any(type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0 for rate in rates):
                raise ValueError('invalid pricing rates')
    for name in ('embeddings', 'claim_generation', 'topic_classification'):
        try:
            card = pricing_for_step(config, name)
        except KeyError as exc:
            raise ValueError('missing pricing for ' + name) from exc
        if ('usd_per_million_input_tokens' in card) != (name == 'embeddings'):
            raise ValueError('wrong pricing kind for ' + name)


def _json(path):
    return json.loads(Path(path).read_text())


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows))


def _file_hash(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request_for_members(members, structural, bundle, config, prompt, schema, support_index=None):
    from knowledge_foundry.claim_generation_and_classification import _candidate_input
    chunk_map = {c['id']: c for c in bundle['chunks']}
    element_map = {e['id']: e for e in bundle['elements']}
    ids = [cid for sid in sorted(members) for cid in structural[sid]['original_chunk_ids']]
    if uses_source_context(config) and support_index is None:
        support_index = source_context_index(bundle)
    text = _candidate_input([chunk_map[cid] for cid in ids], element_map, bundle=bundle,
                            structural_chunks=[structural[sid] for sid in sorted(members)],
                            support_index=support_index)
    return generation_payload(config, text, prompt, schema), ids


def prepare_claim_context(chunked_dir, output_dir, config_path=ROOT / 'config/semantic-clustering.json',
                          cache_dir=None, fixture_path=None, on_progress=None, workers=4):
    from knowledge_foundry.claim_generation_and_classification import _config, _prompt, CANDIDATE_SCHEMA
    from scripts.validate_artifact_examples import check
    validate_workers(workers)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise ValueError('structural chunks and semantic clusters output directory already exists; choose a fresh directory')
    if output_dir.resolve().is_relative_to(Path(chunked_dir).resolve()):
        raise ValueError('structural chunks and semantic clusters output cannot be inside the saved input run')
    input_path = Path(chunked_dir) / 'artifact-bundle.json'
    bundle, config = _json(input_path), _config(config_path)
    check(bundle)
    if not bundle['chunks'] or bundle['claims'] or bundle['topics'] or len(bundle['builds']) != 1:
        raise ValueError('input must be an immutable chunk bundle without claims or topics')
    cache_dir = Path(cache_dir) if cache_dir else ROOT / 'artifacts/embeddings-and-token-counts'
    if cache_dir.resolve().is_relative_to(Path(chunked_dir).resolve()):
        raise ValueError('mutable cache cannot be inside the saved input run')
    if cache_dir.resolve().is_relative_to(output_dir.resolve()) or output_dir.resolve().is_relative_to(cache_dir.resolve()):
        raise ValueError('structural chunks and semantic clusters output and reusable embedding/token-count directories must be separate')
    prompt, _ = _prompt('claim_generation_v1.txt')
    from knowledge_foundry.document_structuring import load_validated_structure
    accepted = load_validated_structure(chunked_dir, allow_test=all(
        config['steps'][step]['provider'] == 'fixture' for step in ('embeddings', 'claim_generation')))
    structure, hierarchy = accepted['structure'], accepted['hierarchy']
    structural, links = accepted['chunks'], accepted['links']
    if on_progress:
        on_progress({'state': 'validated_structural_chunks_loaded', 'structural_chunks': len(structural)})
    by_id = {r['id']: r for r in structural}
    counter = RequestCounter(config, cache_dir / 'request-counts')
    support_index = source_context_index(bundle) if uses_source_context(config) else None

    def payload(ids):
        return request_for_members(ids, by_id, bundle, config, prompt, CANDIDATE_SCHEMA, support_index)[0]

    # Check every singleton before paid embeddings or cluster refinement.
    if on_progress:
        on_progress({'state': 'checking_individual_structural_chunks_fit_claim_generation', 'completed': 0, 'total': len(structural)})
    completed = 0

    def check_singleton(record):
        return counter.check(payload([record['id']]), 'structural chunk ' + record['id'])

    def singleton_checked(_tokens):
        nonlocal completed
        completed += 1
        if on_progress and (completed == 1 or completed % 25 == 0 or completed == len(structural)):
            on_progress({'state': 'checking_individual_structural_chunks_fit_claim_generation',
                         'completed': completed, 'total': len(structural)})

    run_bounded(check_singleton, structural, workers, singleton_checked)
    fixture = _json(fixture_path) if fixture_path else None
    adapter = EmbeddingAdapter(config['steps']['embeddings'], cache_dir / 'embeddings', fixture,
                               pricing=pricing_for_step(config, 'embeddings'), pricing_version=config['pricing']['version'])
    output_dir.mkdir(parents=True)
    try:
        embeddings = adapter.embed(structural)
        if on_progress:
            on_progress({'state': 'embedded', 'structural_chunks': len(structural)})
        vectors = {r['structural_chunk_id']: r['vector'] for r in embeddings}
        scores = similarity_matrix(vectors)
        clustering = config['steps']['semantic_clusters']
        groups = cluster_members(by_id, scores, clustering['minimum_similarity'])
        limit = usable_input_budget(config['steps']['claim_generation']['budget'])
        # Pre-count independent groups concurrently. Adaptive splits still run
        # in deterministic order and reuse these exact measurements.
        measurements = {}
        if on_progress:
            on_progress({'state': 'counting_semantic_cluster_requests', 'completed': 0, 'total': len(groups)})

        def count_group(group):
            return tuple(group), counter.count(payload(group))

        def group_counted(result):
            key, tokens = result
            measurements[key] = tokens
            done = len(measurements)
            if on_progress and (done == 1 or done % 25 == 0 or done == len(groups)):
                on_progress({'state': 'counting_semantic_cluster_requests', 'completed': done, 'total': len(groups)})

        run_bounded(count_group, groups, workers, group_counted)

        def fits(ids):
            key = tuple(ids)
            if key not in measurements:
                measurements[key] = counter.count(payload(ids))
            return measurements[key] <= limit

        groups, splits = refine_clusters(groups, scores, clustering['minimum_similarity'],
                                        fits, clustering['refinement_step'], clustering['max_refinements'],
                                        on_progress=on_progress)
        clusters = []
        if on_progress:
            on_progress({'state': 'checking_semantic_clusters_fit_claim_generation',
                         'phase': 'final', 'completed': 0, 'total': len(groups)})

        def final_cluster(group):
            request, ids = request_for_members(group, by_id, bundle, config, prompt, CANDIDATE_SCHEMA, support_index)
            tokens = counter.check(request, 'semantic cluster')
            return {'id': 'semantic:' + stable_hash([cluster_namespace(config), group]),
                    'structural_chunk_ids': group, 'original_chunk_ids': ids,
                    'document_ids': sorted({by_id[sid]['document_id'] for sid in group}),
                    'input_tokens': tokens, 'request_sha256': stable_hash(request),
                    'minimum_similarity': min((cosine(scores, a, b) for i, a in enumerate(group)
                                               for b in group[i + 1:]), default=1.)}

        def final_checked(cluster):
            clusters.append(cluster)
            done = len(clusters)
            if on_progress and (done == 1 or done % 25 == 0 or done == len(groups)):
                on_progress({'state': 'checking_semantic_clusters_fit_claim_generation',
                             'phase': 'final', 'completed': done, 'total': len(groups)})

        run_bounded(final_cluster, groups, workers, final_checked)
        clusters.sort(key=lambda c: c['id'])
        report = {'status': 'complete', 'original_chunks': len(bundle['chunks']),
                  'structural_chunks': len(structural), 'semantic_clusters': len(clusters),
                  'singletons': sum(len(c['structural_chunk_ids']) == 1 for c in clusters),
                  'cross_document_clusters': sum(len(c['document_ids']) > 1 for c in clusters),
                  'structural_chunk_original_size_distribution': dict(Counter(len(c['original_chunk_ids']) for c in structural)),
                  'cluster_structural_size_distribution': dict(Counter(len(c['structural_chunk_ids']) for c in clusters)),
                  'oversized_cluster_splits': splits, 'usable_input_tokens': limit,
                  'claim_generation_request_token_counting': counter.telemetry, 'embeddings': adapter.telemetry,
                  'structure_metadata_elements': len(structure['elements']),
                  'structural_limitations': 'Heading metadata is provider-derived; numbered and local list scopes '
                                           'are pipeline inferences. Footnotes are non-merging hints. '
                                           'Without parser sidecars, heading levels fall back to source flow/numbering.'}
        # Preserve acceptance, rich metadata and review evidence with the semantic snapshot.
        import shutil
        frozen_names = ['structuring-manifest.json', 'structure-metadata.json', 'figure-links.json', 'acceptance.json']
        if accepted['acceptance']['status'] == 'accepted':
            frozen_names += ['parsing-review.json', 'chunking-review.json']
        for filename in {**accepted['manifest'].get('assets', {}), **accepted['manifest'].get('native_outputs', {})}:
            target = output_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(chunked_dir) / filename, target)
        for filename in frozen_names:
            shutil.copyfile(Path(chunked_dir) / filename, output_dir / filename)
        # Preserve the exact original file bytes, including immutable upstream records.
        (output_dir / 'artifact-bundle.json').write_bytes(input_path.read_bytes())
        for filename in ('structural-chunks.jsonl', 'structural-links.jsonl', 'document-structure.json', 'document-hierarchy.json'):
            shutil.copyfile(Path(chunked_dir) / filename, output_dir / filename)
        _write_jsonl(output_dir / 'embeddings.jsonl', embeddings)
        atomic_json(output_dir / 'semantic-clusters.json', clusters)
        atomic_json(output_dir / 'report.json', report, indent=2)
        manifest = {'version': 2, 'stage': 'semantic-clustering', 'config': config,
                    'input_bundle_sha256': _file_hash(input_path),
                    'cluster_plan_sha256': stable_hash(clusters),
                    'generation_prompt_sha256': stable_hash(prompt), 'candidate_schema_sha256': stable_hash(CANDIDATE_SCHEMA),
                    'outputs': [{'path': str(p.relative_to(output_dir)), 'sha256': _file_hash(p)} for p in sorted(output_dir.rglob('*')) if p.is_file()]}
        atomic_json(output_dir / 'build-manifest.json', manifest)
        load_preparation(output_dir, config)
        return report
    except BaseException as exc:
        atomic_json(output_dir / 'failure.json', {'status': 'failed', 'error': str(exc),
                    'embeddings': adapter.telemetry, 'claim_generation_request_token_counting': counter.telemetry})
        raise


def load_preparation(context_dir, config=None, verify_requests=True, *,
                     validated_preparation=None, on_progress=None):
    """Verify offline; reuse an invocation's validated copy only after hashing files."""
    from knowledge_foundry.claim_generation_and_classification import _prompt, CANDIDATE_SCHEMA
    from scripts.validate_artifact_examples import check
    directory = Path(context_dir)

    def progress(step, completed=0, total=None):
        if on_progress:
            on_progress({'phase': 'preparation', 'state': 'progress', 'step': step,
                         'completed': completed, 'total': total})

    progress('checking saved artifacts')
    manifest = _json(directory / 'build-manifest.json')
    schema = _json(ROOT / "schemas/semantic-clustering-artifact-schema.json")
    Draft202012Validator(schema).validate(manifest)
    saved_config = manifest['config']
    validate_settings(saved_config, frozen=True)
    if config is not None and execution_settings(saved_config) != execution_settings(config):
        raise ValueError('structural chunks and semantic clusters configuration mismatch; build a fresh artifact directory')
    required = {'artifact-bundle.json', 'structural-chunks.jsonl', 'structural-links.jsonl',
                'embeddings.jsonl', 'semantic-clusters.json', 'report.json'}
    required |= {'document-structure.json', 'document-hierarchy.json', 'structuring-manifest.json',
                 'structure-metadata.json', 'figure-links.json', 'acceptance.json'}
    if _json(directory / 'acceptance.json')['status'] == 'accepted':
        required |= {'parsing-review.json', 'chunking-review.json'}
    frozen = _json(directory / 'structuring-manifest.json')
    required |= set(frozen.get('assets', {})) | set(frozen.get('native_outputs', {}))
    if {r['path'] for r in manifest['outputs']} != required or len(manifest['outputs']) != len(required):
        raise ValueError('structural chunks and semantic clusters manifest outputs mismatch')
    for index, entry in enumerate(manifest['outputs'], start=1):
        path = directory / entry['path']
        if not path.resolve().is_relative_to(directory.resolve()) or _file_hash(path) != entry['sha256']:
            raise ValueError('structural chunks and semantic clusters output hash mismatch: ' + str(path))
        if index == 1 or index % 25 == 0 or index == len(manifest['outputs']):
            progress('checking saved artifacts', index, len(manifest['outputs']))
    if _file_hash(directory / 'artifact-bundle.json') != manifest['input_bundle_sha256']:
        raise ValueError('structural chunks and semantic clusters upstream hash mismatch')
    prompt, _ = _prompt('claim_generation_v1.txt')
    if verify_requests and (manifest['generation_prompt_sha256'] != stable_hash(prompt) or
                           manifest['candidate_schema_sha256'] != stable_hash(CANDIDATE_SCHEMA)):
        raise ValueError('structural chunks and semantic clusters prompt/schema changed; build fresh artifacts')
    if validated_preparation is not None:
        if _file_hash(directory / 'build-manifest.json') != validated_preparation['manifest_sha256']:
            raise ValueError('saved structural chunks and semantic clusters artifacts mismatch')
        # A caller cannot reuse a request-unchecked load for a stricter check.
        if not verify_requests or validated_preparation.get('requests_verified', False):
            progress('reusing verified preparation', 1, 1)
            return validated_preparation
    progress('validating source structure')
    bundle = _json(directory / 'artifact-bundle.json')
    check(bundle)
    if bundle['claims'] or bundle['topics'] or len(bundle['builds']) != 1:
        raise ValueError('structural chunks and semantic clusters input is not an original chunk bundle')
    def validate_records(name, rows):
        validator = Draft202012Validator({'$ref': '#/$defs/' + name, '$defs': schema['$defs']})
        for row in rows:
            validator.validate(row)

    structural = _jsonl(directory / 'structural-chunks.jsonl')
    validate_records('structural_chunk', structural)
    validate_records('structural_link', _jsonl(directory / 'structural-links.jsonl'))
    from knowledge_foundry.document_structuring import load_validated_structure
    accepted = load_validated_structure(directory, allow_test=all(
        saved_config['steps'][step]['provider'] == 'fixture' for step in ('embeddings', 'claim_generation')))
    structure, hierarchy = accepted['structure'], accepted['hierarchy']
    validate_records('document_structure', [structure])
    validate_records('hierarchy_entry', hierarchy)
    if assemble_document_hierarchy(bundle, structure) != hierarchy:
        raise ValueError('document hierarchy mismatch')
    expected, links = assemble_structural_chunks(bundle, structure)
    if structural != expected or _jsonl(directory / 'structural-links.jsonl') != links:
        raise ValueError('structural chunks membership, ordering or links mismatch')
    embeddings = _jsonl(directory / 'embeddings.jsonl')
    validate_records('embedding', embeddings)
    if [r['structural_chunk_id'] for r in embeddings] != [r['id'] for r in structural]:
        raise ValueError('structural chunks embedding membership/order mismatch')
    by_id = {r['id']: r for r in structural}
    support_index = source_context_index(bundle) if uses_source_context(saved_config) else None
    embedding_config = saved_config['steps']['embeddings']
    for r in embeddings:
        if r['input_sha256'] != stable_hash(by_id[r['structural_chunk_id']]['text']) or \
                r['vector_sha256'] != stable_hash(r['vector']) or \
                len(r['vector']) != (embedding_config['dimensions'] or embedding_config['expected_dimensions']) or \
                any(r[k] != embedding_config[k] for k in ('provider', 'model', 'preprocessing_version')):
            raise ValueError('structural chunks embedding identity mismatch')
    clusters = _json(directory / 'semantic-clusters.json')
    validate_records('semantic_cluster', clusters)
    if stable_hash(clusters) != manifest['cluster_plan_sha256'] or clusters != sorted(clusters, key=lambda c: c['id']):
        raise ValueError('semantic clusters plan hash/order mismatch')
    for cluster in clusters:
        ids = cluster['structural_chunk_ids']
        if not ids or ids != sorted(set(ids)) or any(sid not in by_id for sid in ids):
            raise ValueError('structural chunks membership/order mismatch')
    progress('checking within-cluster similarities')
    scores = similarity_matrix({r['structural_chunk_id']: r['vector'] for r in embeddings},
                               groups=[cluster['structural_chunk_ids'] for cluster in clusters])
    seen, originals = [], []
    step = 'validating saved cluster requests' if verify_requests else 'validating saved cluster membership'
    progress(step, 0, len(clusters))
    for index, cluster in enumerate(clusters, start=1):
        ids = cluster['structural_chunk_ids']
        if verify_requests:
            request, chunk_ids = request_for_members(ids, by_id, bundle, saved_config, prompt, CANDIDATE_SCHEMA, support_index)
        else:
            chunk_ids = [cid for sid in ids for cid in by_id[sid]['original_chunk_ids']]
        minimum = min((cosine(scores, a, b) for i, a in enumerate(ids) for b in ids[i + 1:]), default=1.)
        if cluster['original_chunk_ids'] != chunk_ids or cluster['document_ids'] != sorted({by_id[s]['document_id'] for s in ids}) or \
                cluster['minimum_similarity'] != minimum or minimum < saved_config['steps']['semantic_clusters']['minimum_similarity'] or \
                cluster['id'] != 'semantic:' + stable_hash([cluster_namespace(saved_config), ids]):
            raise ValueError('semantic clusters membership/similarity mismatch')
        if type(cluster['input_tokens']) is not int or not 0 < cluster['input_tokens'] <= usable_input_budget(saved_config['steps']['claim_generation']['budget']):
            raise ValueError('semantic clusters request budget mismatch')
        if verify_requests and cluster['request_sha256'] != stable_hash(request):
            raise ValueError('semantic clusters request format changed; build fresh structural chunks and semantic clusters artifacts')
        seen.extend(ids)
        originals.extend(chunk_ids)
        if index == 1 or index % 25 == 0 or index == len(clusters):
            progress(step, index, len(clusters))
    if sorted(seen) != sorted(by_id) or sorted(originals) != sorted(c['id'] for c in bundle['chunks']):
        raise ValueError('structural chunks and semantic clusters require complete unique membership')
    report = _json(directory / 'report.json')
    expected_counts = {'status': 'complete', 'original_chunks': len(bundle['chunks']),
                       'structural_chunks': len(structural), 'semantic_clusters': len(clusters),
                       'singletons': sum(len(c['structural_chunk_ids']) == 1 for c in clusters),
                       'cross_document_clusters': sum(len(c['document_ids']) > 1 for c in clusters),
                       'usable_input_tokens': usable_input_budget(saved_config['steps']['claim_generation']['budget'])}
    if any(report.get(k) != v for k, v in expected_counts.items()):
        raise ValueError('structural chunks and semantic clusters diagnostics mismatch')
    return {'manifest': manifest, 'bundle': bundle, 'structural_chunks': structural,
            'clusters': clusters, 'hierarchy': hierarchy, 'report': report,
            'requests_verified': verify_requests,
            'manifest_sha256': _file_hash(directory / 'build-manifest.json')}


def validate_candidate_lineage(directory, manifest, candidates, *, on_progress=None):
    """Validate structural chunk and semantic cluster lineage; preserve legacy candidate claim contracts."""
    directory = Path(directory)
    if 'preparation_manifest_sha256' not in manifest:
        if 'selected_semantic_cluster_ids' in manifest.get('compilation_scope', {}):
            raise ValueError('claim generation is missing structural chunk and semantic cluster lineage')
        return None
    prepared = load_preparation(directory / 'preparation', verify_requests=False, on_progress=on_progress)
    if prepared['manifest_sha256'] != manifest['preparation_manifest_sha256'] or \
            prepared['manifest']['cluster_plan_sha256'] != manifest.get('cluster_plan_sha256') or \
            prepared['manifest']['input_bundle_sha256'] != manifest['input_bundle_sha256']:
        raise ValueError('claim generation structural chunk and semantic cluster lineage mismatch')
    bundle = _json(directory / 'artifact-bundle.json')
    if any(bundle[k] != prepared['bundle'][k] for k in ('sources', 'documents', 'elements', 'chunks')):
        raise ValueError('claim generation changed original structural chunk and semantic cluster input records')
    scope = manifest['compilation_scope']
    clusters = prepared['clusters']
    numbers = scope['selected_cluster_numbers']
    if not numbers or numbers != sorted(set(numbers)) or any(type(n) is not int or not 1 <= n <= len(clusters) for n in numbers):
        raise ValueError('generation cluster selection mismatch')
    selected = [clusters[n - 1] for n in numbers]
    ids = [c['id'] for c in selected]
    chunks = [cid for c in selected for cid in c['original_chunk_ids']]
    if scope['selected_semantic_cluster_ids'] != ids or scope['selected_chunk_ids'] != chunks or \
            scope['selected_chunks'] != len(chunks) or scope['total_semantic_clusters'] != len(clusters) or \
            scope['selected_cluster_chunk_counts'] != [len(c['original_chunk_ids']) for c in selected]:
        raise ValueError('generation selected cluster membership mismatch')
    report = _json(directory / 'report.json')
    if report.get('preparation_manifest_sha256') != prepared['manifest_sha256'] or \
            report.get('cluster_plan_sha256') != prepared['manifest']['cluster_plan_sha256'] or \
            report.get('preparation') != prepared['report']:
        raise ValueError('claim generation structural chunk and semantic cluster report mismatch')
    mapping = _json(directory / 'candidate-clusters.json')
    pairs = {(r['candidate_id'], r['semantic_cluster_id']) for r in mapping}
    if len(pairs) != len(mapping) or {r['candidate_id'] for r in mapping} != {c['id'] for c in candidates}:
        raise ValueError('candidate claim cluster mapping is incomplete or duplicated')
    by_candidate = {c['id']: c for c in candidates}
    by_cluster = {c['id']: c for c in selected}
    original_map = {c['id']: c for c in bundle['chunks']}
    element_map = {e['id']: e for e in bundle['elements']}
    from knowledge_foundry.claim_generation_and_classification import (
        _validate_candidate, _validate_candidate_choices, _evidence_choices,
    )
    support_index = source_context_index(bundle) if uses_source_context(prepared['manifest']['config']) else None
    structural = {r['id']: r for r in prepared['structural_chunks']}
    choices_by_cluster = {}
    for row in mapping:
        candidate = by_candidate[row['candidate_id']]
        cluster = by_cluster.get(row['semantic_cluster_id'])
        if cluster is None:
            raise ValueError('candidate claim maps outside selected semantic clusters')
        if cluster['id'] not in choices_by_cluster:
            choices_by_cluster[cluster['id']] = _evidence_choices(
                [original_map[cid] for cid in cluster['original_chunk_ids']], element_map,
                [structural[sid] for sid in cluster['structural_chunk_ids']], support_index)
        _validate_candidate_choices(candidate, choices_by_cluster[cluster['id']])
        _validate_candidate(candidate, manifest['generation_build_id'], original_map, element_map)
    return prepared
