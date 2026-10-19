"""Build, freeze and verify structural chunks and semantic clusters."""

from collections import Counter
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge_foundry.embeddings import EmbeddingAdapter, atomic_json
from knowledge_foundry.generation_context import RequestCounter, generation_payload, usable_input_budget
from knowledge_foundry.semantic_clusters import cluster_members, refine_clusters, similarity_matrix, cosine
from knowledge_foundry.structural_chunks import VERSION, assemble_structural_chunks, stable_hash
from knowledge_foundry.document_structure import (assemble_document_hierarchy, structure_snapshot,
                                                validate_structure)

ROOT = Path(__file__).resolve().parents[2]


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


def saved_settings(value):
    """Read frozen legacy settings without accepting the old live config format."""
    if 'steps' in value:
        return value
    import copy
    old = copy.deepcopy(value)
    embedding = old['embeddings']
    rate = {key: embedding.pop(key) for key in (
        'usd_per_million_input_tokens', 'rate_card_version', 'documentation_url')}
    models = {key: {**card, 'verified_on': '2026-10-13', 'documentation_url':
                   'https://developers.openai.com/api/docs/models/' + key}
              for key, card in old['pricing']['models'].items()}
    # A fixture may use the same model name as generation; its zero-cost embedding
    # provenance is read independently of inference pricing.
    embedding_models = {embedding['model']: {'usd_per_million_input_tokens': rate['usd_per_million_input_tokens'],
                        'verified_on': '2026-10-16', 'documentation_url': rate['documentation_url']}}
    providers = {old['provider']: {'models': models}}
    providers.setdefault(embedding['provider'], {'models': {}})['models'].update(embedding_models)
    return {'pipeline_version': old['pipeline_version'], 'steps': {
        'structural_chunks': {'version': old['preparation']['structural_version']},
        'embeddings': embedding,
        'semantic_clusters': {'version': old['preparation']['clustering_version'], **old['clustering']},
        'claim_generation': {'version': old['generation_version'], **{key: old[key] for key in
                             ('provider', 'model', 'reasoning_effort', 'retries')}, 'budget': old['generation_budget']},
        'topic_classification': {'version': old['classification_version'], **{key: old[key] for key in
                                 ('provider', 'model', 'reasoning_effort', 'retries')}}},
        'pricing': {'version': old['pricing']['rate_card_version'], 'providers': providers}}


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
        if not isinstance(steps[name], dict) or set(steps[name]) != keys:
            raise ValueError('invalid ' + name + ' configuration shape')
    versions = {VERSION, 'structural-chunks-v1', 'structural-chunks-v2'} if frozen else {VERSION}
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


def request_for_members(members, structural, bundle, config, prompt, schema):
    from knowledge_foundry.claim_generation_and_classification import _candidate_input
    chunk_map = {c['id']: c for c in bundle['chunks']}
    element_map = {e['id']: e for e in bundle['elements']}
    ids = [cid for sid in sorted(members) for cid in structural[sid]['original_chunk_ids']]
    text = _candidate_input([chunk_map[cid] for cid in ids], element_map, bundle=bundle,
                            structural_chunks=[structural[sid] for sid in sorted(members)])
    return generation_payload(config, text, prompt, schema), ids


def prepare_claim_context(chunked_dir, output_dir, config_path=ROOT / 'config/claim-generation-and-classification.json',
                          cache_dir=None, fixture_path=None, on_progress=None, parsed_dir=None):
    from knowledge_foundry.claim_generation_and_classification import _config, _prompt, CANDIDATE_SCHEMA
    from scripts.validate_artifact_examples import check
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
    if on_progress:
        on_progress({'state': 'assembling_structural_chunks', 'original_chunks': len(bundle['chunks'])})
    structure_path = Path(chunked_dir) / 'document-structure.json'
    structure = _json(structure_path) if structure_path.exists() else structure_snapshot([])
    if parsed_dir is not None:
        rows = []
        for document in bundle['documents']:
            sidecar = _json(Path(parsed_dir) / 'documents' / document['id'].removeprefix('doc:') / 'parse.json')
            if sidecar['document_id'] != document['id']:
                raise ValueError('parsed sidecar document mismatch')
            rows.extend(sidecar['elements'])
        from_parsed = structure_snapshot(rows)
        if structure_path.exists() and from_parsed != structure:
            raise ValueError('parsed sidecars conflict with saved document structure')
        structure = from_parsed
    validate_structure(bundle, structure)
    hierarchy = assemble_document_hierarchy(bundle, structure)
    structural, links = assemble_structural_chunks(bundle, structure)
    if on_progress:
        on_progress({'state': 'structural_chunks_assembled', 'structural_chunks': len(structural)})
    by_id = {r['id']: r for r in structural}
    counter = RequestCounter(config, cache_dir / 'request-counts')
    def payload(ids):
        return request_for_members(ids, by_id, bundle, config, prompt, CANDIDATE_SCHEMA)[0]
    # Check every singleton before paid embeddings or cluster refinement.
    if on_progress:
        on_progress({'state': 'checking_individual_structural_chunks_fit_claim_generation', 'completed': 0, 'total': len(structural)})
    for index, record in enumerate(structural, start=1):
        counter.check(payload([record['id']]), 'structural chunk ' + record['id'])
        if on_progress and (index == 1 or index % 25 == 0 or index == len(structural)):
            on_progress({"state": "checking_individual_structural_chunks_fit_claim_generation", "completed": index, "total": len(structural)})
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
        groups, splits = refine_clusters(groups, scores, clustering['minimum_similarity'],
                                        lambda ids: counter.count(payload(ids)) <= limit,
                                        clustering['refinement_step'], clustering['max_refinements'],
                                        on_progress=on_progress)
        clusters = []
        if on_progress:
            on_progress({'state': 'checking_semantic_clusters_fit_claim_generation',
                         'phase': 'final', 'completed': 0, 'total': len(groups)})
        for index, group in enumerate(groups, start=1):
            request, ids = request_for_members(group, by_id, bundle, config, prompt, CANDIDATE_SCHEMA)
            tokens = counter.check(request, 'semantic cluster')
            clusters.append({'id': 'semantic:' + stable_hash([cluster_namespace(config), group]),
                             'structural_chunk_ids': group, 'original_chunk_ids': ids,
                             'document_ids': sorted({by_id[sid]['document_id'] for sid in group}),
                             'input_tokens': tokens, 'request_sha256': stable_hash(request),
                             'minimum_similarity': min((cosine(scores, a, b) for i, a in enumerate(group)
                                                       for b in group[i + 1:]), default=1.)})
            if on_progress and (index == 1 or index % 25 == 0 or index == len(groups)):
                on_progress({'state': 'checking_semantic_clusters_fit_claim_generation',
                             'phase': 'final', 'completed': index, 'total': len(groups)})
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
                  'structural_limitations': 'Heading metadata is parser-derived; numbered and local list scopes '
                                           'are pipeline inferences. Footnotes are non-merging hints. '
                                           'Without parser sidecars, heading levels fall back to source flow/numbering.'}
        # Preserve the exact original file bytes, including immutable upstream records.
        (output_dir / 'artifact-bundle.json').write_bytes(input_path.read_bytes())
        _write_jsonl(output_dir / 'structural-chunks.jsonl', structural)
        _write_jsonl(output_dir / 'structural-links.jsonl', links)
        atomic_json(output_dir / 'document-structure.json', structure)
        atomic_json(output_dir / 'document-hierarchy.json', hierarchy)
        _write_jsonl(output_dir / 'embeddings.jsonl', embeddings)
        atomic_json(output_dir / 'semantic-clusters.json', clusters)
        atomic_json(output_dir / 'report.json', report, indent=2)
        manifest = {'version': 2, 'stage': 'claim-context-preparation', 'config': config,
                    'input_bundle_sha256': _file_hash(input_path),
                    'cluster_plan_sha256': stable_hash(clusters),
                    'generation_prompt_sha256': stable_hash(prompt), 'candidate_schema_sha256': stable_hash(CANDIDATE_SCHEMA),
                    'outputs': [{'path': p.name, 'sha256': _file_hash(p)} for p in sorted(output_dir.iterdir())]}
        atomic_json(output_dir / 'build-manifest.json', manifest)
        load_preparation(output_dir, config)
        return report
    except BaseException as exc:
        atomic_json(output_dir / 'failure.json', {'status': 'failed', 'error': str(exc),
                    'embeddings': adapter.telemetry, 'claim_generation_request_token_counting': counter.telemetry})
        raise


def load_preparation(context_dir, config=None, verify_requests=True):
    """Offline verification; never refresh embeddings or token counts while reading."""
    from knowledge_foundry.claim_generation_and_classification import _prompt, CANDIDATE_SCHEMA
    from scripts.validate_artifact_examples import check
    directory = Path(context_dir)
    manifest = _json(directory / 'build-manifest.json')
    schema = _json(ROOT / f"schemas/claim-context-artifact-schema-v{manifest['version']}.json")
    Draft202012Validator(schema).validate(manifest)
    saved_config = saved_settings(manifest['config'])
    validate_settings(saved_config, frozen=True)
    if (manifest['version'] == 1) != (saved_config['steps']['structural_chunks']['version'] == 'structural-chunks-v1'):
        raise ValueError('preparation manifest and structural version mismatch')
    if config is not None and execution_settings(saved_config) != execution_settings(config):
        raise ValueError('structural chunks and semantic clusters configuration mismatch; build a fresh artifact directory')
    required = {'artifact-bundle.json', 'structural-chunks.jsonl', 'structural-links.jsonl',
                'embeddings.jsonl', 'semantic-clusters.json', 'report.json'}
    if manifest['version'] == 2:
        required |= {'document-structure.json', 'document-hierarchy.json'}
    if {r['path'] for r in manifest['outputs']} != required or len(manifest['outputs']) != len(required):
        raise ValueError('structural chunks and semantic clusters manifest outputs mismatch')
    for entry in manifest['outputs']:
        path = directory / entry['path']
        if not path.resolve().is_relative_to(directory.resolve()) or _file_hash(path) != entry['sha256']:
            raise ValueError('structural chunks and semantic clusters output hash mismatch: ' + str(path))
    if _file_hash(directory / 'artifact-bundle.json') != manifest['input_bundle_sha256']:
        raise ValueError('structural chunks and semantic clusters upstream hash mismatch')
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
    hierarchy = []
    if manifest['version'] == 1:
        from knowledge_foundry._structural_chunks_v1 import assemble_structural_chunks as legacy_assemble
        expected, links = legacy_assemble(bundle)
    else:
        structure = _json(directory / 'document-structure.json')
        validate_records('document_structure', [structure])
        validate_structure(bundle, structure)
        if saved_config['steps']['structural_chunks']['version'] == 'structural-chunks-v2':
            from knowledge_foundry._document_structure_v2 import assemble_document_hierarchy as frozen_hierarchy
            from knowledge_foundry._structural_chunks_v2 import assemble_structural_chunks as frozen_assemble
            hierarchy = frozen_hierarchy(bundle, structure)
            expected, links = frozen_assemble(bundle, structure)
        else:
            hierarchy = assemble_document_hierarchy(bundle, structure)
            expected, links = assemble_structural_chunks(bundle, structure)
        saved_hierarchy = _json(directory / 'document-hierarchy.json')
        validate_records('hierarchy_entry', saved_hierarchy)
        if hierarchy != saved_hierarchy:
            raise ValueError('document hierarchy mismatch')
    if structural != expected or _jsonl(directory / 'structural-links.jsonl') != links:
        raise ValueError('structural chunks membership, ordering or links mismatch')
    embeddings = _jsonl(directory / 'embeddings.jsonl')
    validate_records('embedding', embeddings)
    if [r['structural_chunk_id'] for r in embeddings] != [r['id'] for r in structural]:
        raise ValueError('structural chunks embedding membership/order mismatch')
    by_id = {r['id']: r for r in structural}
    embedding_config = saved_config['steps']['embeddings']
    for r in embeddings:
        if r['input_sha256'] != stable_hash(by_id[r['structural_chunk_id']]['text']) or \
                r['vector_sha256'] != stable_hash(r['vector']) or \
                len(r['vector']) != (embedding_config['dimensions'] or embedding_config['expected_dimensions']) or \
                any(r[k] != embedding_config[k] for k in ('provider', 'model', 'preprocessing_version')):
            raise ValueError('structural chunks embedding identity mismatch')
    scores = similarity_matrix({r['structural_chunk_id']: r['vector'] for r in embeddings})
    clusters = _json(directory / 'semantic-clusters.json')
    validate_records('semantic_cluster', clusters)
    if stable_hash(clusters) != manifest['cluster_plan_sha256'] or clusters != sorted(clusters, key=lambda c: c['id']):
        raise ValueError('semantic clusters plan hash/order mismatch')
    seen, originals = [], []
    prompt, _ = _prompt('claim_generation_v1.txt')
    if verify_requests and (manifest['generation_prompt_sha256'] != stable_hash(prompt) or
                           manifest['candidate_schema_sha256'] != stable_hash(CANDIDATE_SCHEMA)):
        raise ValueError('structural chunks and semantic clusters prompt/schema changed; build fresh artifacts')
    for cluster in clusters:
        ids = cluster['structural_chunk_ids']
        if not ids or ids != sorted(set(ids)) or any(sid not in by_id for sid in ids):
            raise ValueError('structural chunks membership/order mismatch')
        request, chunk_ids = request_for_members(ids, by_id, bundle, saved_config, prompt, CANDIDATE_SCHEMA)
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
            'manifest_sha256': _file_hash(directory / 'build-manifest.json')}


def validate_candidate_lineage(directory, manifest, candidates):
    """Validate structural chunk and semantic cluster lineage; preserve legacy candidate claim contracts."""
    directory = Path(directory)
    if 'preparation_manifest_sha256' not in manifest:
        if 'selected_semantic_cluster_ids' in manifest.get('compilation_scope', {}):
            raise ValueError('claim generation is missing structural chunk and semantic cluster lineage')
        return None
    prepared = load_preparation(directory / 'preparation', verify_requests=False)
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
    if sorted(row['candidate_id'] for row in mapping) != sorted(c['id'] for c in candidates):
        raise ValueError('candidate claim cluster mapping is incomplete or duplicated')
    by_candidate = {r['candidate_id']: r['semantic_cluster_id'] for r in mapping}
    by_cluster = {c['id']: c for c in selected}
    original_map = {c['id']: c for c in bundle['chunks']}
    element_map = {e['id']: e for e in bundle['elements']}
    from knowledge_foundry.claim_generation_and_classification import _validate_candidate
    for candidate in candidates:
        cluster = by_cluster.get(by_candidate[candidate['id']])
        if cluster is None:
            raise ValueError('candidate claim maps outside selected semantic clusters')
        _validate_candidate(candidate, manifest['generation_build_id'],
                            {cid: original_map[cid] for cid in cluster['original_chunk_ids']}, element_map)
    return prepared
