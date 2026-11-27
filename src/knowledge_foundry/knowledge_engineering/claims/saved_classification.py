"""Read and verify saved generation and classification artifacts."""
import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from knowledge_foundry.provenance.bundle import check
from knowledge_foundry.provenance.evidence import claim_evidence_references
from knowledge_foundry.knowledge_engineering.claims.claim_source_context import source_context_index, uses_source_context
from .request_contract import (
    CANDIDATE_SCHEMA, _evidence_choices, _read_json, _validate_candidate, _validate_candidate_choices, canonical, digest, file_digest,
)

def _evidence_accounting(response, choices, records):
    """Check complete source accounting and links, never infer semantic coverage."""
    rows = response['coverage']
    labels = [row['evidence_id'] for row in rows]
    if len(labels) != len(set(labels)) or set(labels) != {k for k in choices if k.startswith('E')}:
        raise ValueError('coverage must account for every eligible evidence ID exactly once')
    result = []
    for row in rows:
        label, indices = row['evidence_id'], row['claim_indices']
        expected = [i for i, claim in enumerate(response['claims']) if label in claim['evidence']]
        if sorted(indices) != expected:
            raise ValueError('coverage claim_indices must match claims citing ' + label)
        if row['status'] == 'represented' and not indices:
            raise ValueError('represented evidence needs at least one citing claim: ' + label)
        if row['status'] == 'non_substantive' and indices:
            raise ValueError('non_substantive evidence cannot cite generated claims: ' + label)
        if not row['reason'].strip():
            raise ValueError('coverage needs a substantive reason: ' + label)
        result.append({'reference': choices[label]['reference'], 'status': row['status'],
                       'candidate_ids': list(dict.fromkeys(records[i]['id'] for i in indices)), 'reason': row['reason']})
    return result


def _accounting_summary(clusters, method, expected_passages, failed=False):
    counts = Counter(row['status'] for cluster in clusters for row in cluster['passages'])
    total = sum(counts.values())
    return {
        'version': 1, 'method': method,
        'status': 'needs_review' if failed or total != expected_passages or counts['ambiguous'] or
                  counts['insufficient_context'] else 'accounted',
        'expected_passages': expected_passages, 'passages': total,
        'unaccounted_passages': expected_passages - total,
        'status_counts': {status: counts[status] for status in
                          ('represented', 'non_substantive', 'insufficient_context', 'ambiguous')},
        'limitation': 'Complete accounting is not proof of full support or semantic completeness.'}


def _validate_accounting_rows(rows, choices, candidates):
    labels = {canonical(value['reference']): label for label, value in choices.items()}
    index = {candidate['id']: i for i, candidate in enumerate(candidates)}
    try:
        for candidate in candidates:
            _validate_candidate_choices(candidate, choices)
        response = {'claims': [{'text': c['text'], 'evidence': [labels[canonical(ref)] for ref in c['evidence']]}
                               for c in candidates],
                    'coverage': [{'evidence_id': labels[canonical(row['reference'])],
                                  'status': row['status'], 'reason': row['reason'],
                                  'claim_indices': [index[cid] for cid in row['candidate_ids']]}
                                 for row in rows]}
        Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
        _evidence_accounting(response, choices, candidates)
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError('invalid saved evidence accounting: ' + str(exc)[:200]) from exc


def _validate_saved_accounting(directory, manifest, candidates, preparation):
    """Validate the audit sidecar as part of lineage, including on resume/export."""
    report = _read_json(directory / 'report.json')
    version = manifest.get('evidence_accounting_version')
    if version is None and 'evidence_accounting' not in report:
        return  # Historical artifacts predate the accounting contract.
    if version != 1 or preparation is None:
        raise ValueError('invalid evidence accounting version or preparation')
    audit = _read_json(directory / 'evidence-accounting.json')
    if audit.get('version') != 1 or audit.get('generation_build_id') != manifest['generation_build_id']:
        raise ValueError('evidence accounting build mismatch')
    selected = {c['id']: c for c in preparation['clusters']
                if c['id'] in report['compilation_scope']['selected_semantic_cluster_ids']}
    clusters = audit.get('clusters', [])
    if len(clusters) != len(selected) or {c['semantic_cluster_id'] for c in clusters} != set(selected):
        raise ValueError('evidence accounting cluster mismatch')
    bundle = preparation['bundle']
    chunks, elements = ({item['id']: item for item in bundle[name]} for name in ('chunks', 'elements'))
    support_index = source_context_index(bundle) if uses_source_context(preparation['manifest']['config']) else None
    structural = {r['id']: r for r in preparation['structural_chunks']}
    mapping = {(row['candidate_id'], row['semantic_cluster_id']) for row in
               _read_json(directory / 'candidate-clusters.json')}
    expected_passages = 0
    for cluster in clusters:
        semantic = selected[cluster['semantic_cluster_id']]
        ids = semantic['original_chunk_ids']
        choices = _evidence_choices([chunks[cid] for cid in ids], elements,
                                   [structural[sid] for sid in semantic['structural_chunk_ids']],
                                   support_index)
        expected_passages += sum(k.startswith('E') for k in choices)
        own = [c for c in candidates if (c['id'], cluster['semantic_cluster_id']) in mapping]
        if not cluster['passages'] and not own and any(e.get('chunk_ids') == ids for e in report['errors']):
            continue
        _validate_accounting_rows(cluster['passages'], choices, own)
    method = 'model_self_report' if report['telemetry']['provider'] == 'openai' else 'offline_fixture'
    if report['evidence_accounting'] != _accounting_summary(clusters, method, expected_passages, bool(report['errors'])):
        raise ValueError('evidence accounting summary mismatch')


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def _generation_coverage(bundle, candidates, scope, errors):
    """Account for selected chunks cited by candidate claims, without judging extraction quality."""
    selected = set(scope["selected_chunk_ids"])
    chunk_map = {item["id"]: item for item in bundle["chunks"]}
    document_map = {item["id"]: item for item in bundle["documents"]}
    failed = {chunk_id for error in errors if error.get("stage") == "generation"
              for chunk_id in error["chunk_ids"]} & selected
    element_map = {item["id"]: item for item in bundle.get("elements", [])}
    references = {chunk_id: {canonical(ref) for ref in claim_evidence_references(chunk, element_map)}
                  for chunk_id, chunk in chunk_map.items()}
    cited = {chunk_id: set() for chunk_id in selected}
    for candidate in candidates:
        evidence = {canonical(ref) for ref in candidate["evidence"]}
        if not selected.intersection(candidate['chunk_ids']):
            raise ValueError('coverage candidate claim chunk is outside the selected scope')
        for chunk_id in candidate["chunk_ids"]:
            if chunk_id not in selected:
                # Independently validated support chunks qualify a selected
                # claim, but do not expand the primary extraction population.
                continue
            if evidence & references[chunk_id]:
                cited[chunk_id].add(candidate["id"])
    chunks = []
    for chunk in bundle["chunks"]:
        chunk_id = chunk["id"]
        if chunk_id not in selected:
            continue
        document = document_map[chunk["document_id"]]
        eligible = sum("span" in ref for ref in claim_evidence_references(chunk, element_map))
        status = ("generation_failed" if chunk_id in failed else "cited" if cited[chunk_id] else
                  "no_eligible_evidence" if not eligible else "no_candidates")
        chunks.append({"chunk_id": chunk_id, "document_id": chunk["document_id"],
                       "source_id": document["source_id"], "source_version": document["source_version"],
                       "candidate_count": len(cited[chunk_id]), "eligible_evidence_count": eligible,
                       "status": status})
    sources = []
    for source in bundle["sources"]:
        rows = [row for row in chunks if (row["source_id"], row["source_version"]) ==
                (source["id"], source["version"])]
        source_candidates = set().union(*(cited[row["chunk_id"]] for row in rows))
        sources.append({"source_id": source["id"], "source_version": source["version"],
                        "title": source["title"], "selected_chunks": len(rows),
                        "cited_chunks": sum(row["candidate_count"] > 0 for row in rows),
                        "candidate_count": len(source_candidates),
                        "status": "not_selected" if not rows else
                                  "has_candidates" if source_candidates else "no_candidates"})
    missing_chunks = [row["chunk_id"] for row in chunks if not row["candidate_count"]]
    missing_sources = [row for row in sources if row["status"] == "no_candidates"]
    return {
        "coverage_version": 1,
        "status": "needs_review" if missing_chunks or failed else "no_gaps_detected",
        "summary": {"selected_chunks": len(chunks), "cited_chunks": len(chunks) - len(missing_chunks),
                    "chunks_without_candidates": len(missing_chunks),
                    "failed_chunks": sum(row["status"] == "generation_failed" for row in chunks),
                    "chunks_without_eligible_evidence": sum(not row["eligible_evidence_count"] for row in chunks),
                    "not_selected_chunks": len(bundle["chunks"]) - len(chunks),
                    "selected_sources": sum(row["status"] != "not_selected" for row in sources),
                    "sources_with_candidates": sum(row["status"] == "has_candidates" for row in sources),
                    "sources_without_candidates": len(missing_sources),
                    "not_selected_sources": sum(row["status"] == "not_selected" for row in sources)},
        "selected_chunk_ids_without_candidates": missing_chunks,
        "selected_source_ids_without_candidates": sorted({row["source_id"] for row in missing_sources}),
        "chunks": chunks, "sources": sources,
    }


def _candidate_manifest(candidate_dir, *, on_progress=None):
    candidate_dir = Path(candidate_dir)
    manifest_path = candidate_dir / "build-manifest.json"
    if not manifest_path.is_file() and (candidate_dir / "generation-checkpoint.json").is_file():
        raise ValueError("candidate claim generation is incomplete; rerun generate_claims.py with --resume")
    manifest = _read_json(manifest_path)
    candidate_path = candidate_dir / "claim-candidates.jsonl"
    output_hashes = {item.get("path"): item.get("sha256") for item in manifest.get("outputs", [])}
    if manifest.get("stage") != "claim-generation" or \
            manifest.get("candidate_sha256") != file_digest(candidate_path) or \
            output_hashes.get("artifact-bundle.json") != file_digest(candidate_dir / "artifact-bundle.json") or \
            output_hashes.get("claim-candidates.jsonl") != file_digest(candidate_path):
        raise ValueError("candidate claim artifact manifest is invalid")
    for entry in manifest.get('outputs', []):
        path = (candidate_dir / entry['path']).resolve()
        if not path.is_relative_to(candidate_dir.resolve()) or file_digest(path) != entry['sha256']:
            raise ValueError('candidate claim output hash mismatch')
    from knowledge_foundry.knowledge_engineering.semantic.claim_context import validate_candidate_lineage
    candidates = _read_jsonl(candidate_path)
    preparation = validate_candidate_lineage(candidate_dir, manifest, candidates, on_progress=on_progress)
    _validate_saved_accounting(candidate_dir, manifest, candidates, preparation)
    return manifest, candidate_path, manifest_path


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _manifest_files(directory, manifest, required):
    outputs = manifest.get('outputs', [])
    names = [item['path'] for item in outputs]
    _require(len(names) == len(set(names)) and required <= set(names), 'manifest outputs are incomplete or duplicated')
    for item in outputs:
        path = (directory / item['path']).resolve()
        _require(path.is_relative_to(directory.resolve()) and file_digest(path) == item['sha256'],
                 'output hash mismatch: ' + str(path))


def _load(candidates_dir, classified_dir, taxonomy_path):
    generation_manifest, candidate_path, generation_manifest_path = _candidate_manifest(candidates_dir)
    classification_manifest = _read(classified_dir / 'build-manifest.json')
    _manifest_files(candidates_dir, generation_manifest, {'artifact-bundle.json', 'claim-candidates.jsonl', 'report.json'})
    _manifest_files(classified_dir, classification_manifest,
                    {'artifact-bundle.json', 'report.json', 'claims.jsonl', 'chunks.jsonl'})
    generation_report = _read(candidates_dir / 'report.json')
    classification_report = _read(classified_dir / 'report.json')
    generation_bundle = _read(candidates_dir / 'artifact-bundle.json')
    bundle = _read(classified_dir / 'artifact-bundle.json')
    for value in (generation_bundle, bundle):
        check(value)
    _require(classification_manifest['stage'] == 'topic-classification' and
             (classification_manifest['candidate_manifest_sha256'] ==
              classification_report['candidate_manifest_sha256'] == file_digest(generation_manifest_path)) and
             (classification_manifest['upstream_generation_build_id'] ==
              classification_report['upstream_generation_build_id'] ==
              generation_manifest['generation_build_id'] == generation_report['generation_build_id']) and
             classification_manifest['compilation_build_id'] == classification_report['build_id'],
             'compilation lineage mismatch')
    _require(not generation_bundle['claims'] and not generation_bundle['topics'],
             'candidate claim context contains classified knowledge')
    _require(bundle['builds'][:-1] == generation_bundle['builds'] and
             bundle['builds'][-1]['id'] == classification_report['build_id'],
             'classification changed upstream builds')
    _require(generation_bundle['builds'][-1]['id'] == generation_report['generation_build_id'] and
             generation_bundle['builds'][-1]['status'] == generation_report['status'] and
             bundle['builds'][-1]['status'] == classification_report['status'] and
             generation_manifest['upstream_chunk_build_id'] == generation_report['upstream_chunk_build_id'] and
             generation_manifest['upstream_chunk_build_id'] in {b['id'] for b in generation_bundle['builds'][:-1]},
             'stage build/report mismatch')
    for name in ('sources', 'documents', 'elements', 'chunks'):
        _require(bundle[name] == generation_bundle[name], 'classification changed upstream ' + name)
    taxonomy = _read(taxonomy_path)
    _require(classification_manifest['taxonomy_sha256'] == file_digest(taxonomy_path),
             'supply the taxonomy used by this classification build')
    curated = {item['id']: item for item in taxonomy['topics']}
    chunks, elements = ({item['id']: item for item in bundle[name]} for name in ('chunks', 'elements'))
    candidates = [json.loads(line) for line in candidate_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    for candidate in candidates:
        _validate_candidate(candidate, generation_manifest['generation_build_id'], chunks, elements)
    candidates_by_id = {c['id']: c for c in candidates}

    def candidate_key(candidate):
        return canonical([candidate['text'], candidate['evidence'], candidate['chunk_ids']])

    candidates_by_content = {candidate_key(c): c for c in candidates}
    _require(len(candidates_by_id) == len(candidates_by_content) == len(candidates), 'duplicate candidate claim IDs or content')
    outcomes = {}
    for claim in bundle['claims']:
        candidate = candidates_by_content.get(candidate_key(claim))
        _require(candidate is not None and candidate['id'] not in outcomes, 'changed or duplicated classified candidate claim')
        _require(claim['generation'] == candidate['generation'] and claim['topic_id'] in curated,
                 'claim generation metadata or topic mismatch')
        outcomes[candidate['id']] = {'status': 'classified', 'claim_id': claim['id'], 'topic_id': claim['topic_id']}
    for candidate_id in classification_report['unclassified_candidate_ids']:
        _require(candidate_id in candidates_by_id and candidate_id not in outcomes,
                 'invalid unclassified candidate claim accounting')
        outcomes[candidate_id] = {'status': 'unclassified', 'claim_id': None, 'topic_id': None}
    for error in classification_report['errors']:
        candidate_id = error.get('candidate_id')
        _require(candidate_id in candidates_by_id and candidate_id not in outcomes, 'invalid failed candidate claim accounting')
        outcomes[candidate_id] = {'status': 'classification_error', 'claim_id': None, 'topic_id': None}
    _require(set(outcomes) == set(candidates_by_id), 'candidate claims missing classification outcomes')
    _require(generation_report['candidates'] == classification_report['candidates'] == len(candidates) and
             classification_report['claims'] == len(bundle['claims']) and
             classification_report['topics'] == len(bundle['topics']), 'report counts mismatch')
    def export_matches(name):
        records = [json.loads(line) for line in (classified_dir / f'{name}.jsonl').read_text().splitlines()
                   if line.strip()]
        return records == bundle[name]

    _require(export_matches('claims') and export_matches('chunks'),
             'JSONL exports differ from bundle')
    output_paths = {item['path'] for item in classification_manifest['outputs']}
    claims_by_id = {claim['id']: claim for claim in bundle['claims']}
    for topic in bundle['topics']:
        _require(topic['id'] in curated and topic['title'] == curated[topic['id']]['title'], 'topic differs from taxonomy')
        slug = topic['id'].removeprefix('topic:')
        _require({f'topics/{slug}.json', f'topics/{slug}.md'} <= output_paths, 'topic views missing from manifest')
        view = _read(classified_dir / 'topics' / f'{slug}.json')
        claims = [claims_by_id[cid] for cid in topic['claim_ids']]
        expected_view = {'view_version': 1, 'build_id': classification_report['build_id'],
                         'topic': topic, 'claims': claims}
        _require(view == expected_view,
                 'topic JSON view differs from bundle')
        lines = [f"# {topic['title']}", '', f"Status: `{topic['review_status']}`", '', topic['summary'], '', '## Claims', '']
        for claim in claims:
            lines.append(f"- `{claim['id']}` — {claim['text']} (grounding: "
                         f"{claim['grounding_review_status']}; classification: {claim['classification_review_status']})")
        _require((classified_dir / 'topics' / f'{slug}.md').read_text() == '\n'.join(lines) + '\n',
                 'topic Markdown view differs from bundle')
    scope = generation_report['compilation_scope']
    _require(len(scope['selected_chunk_ids']) == len(set(scope['selected_chunk_ids'])) == scope['selected_chunks'],
             'invalid generation selection')
    _require(set(scope['selected_chunk_ids']) <= set(chunks) and scope['selected_chunks'] > 0 and
             scope['total_chunks'] == generation_report['chunks'] == len(chunks) and
             generation_report['documents'] == len(bundle['documents']) and
             generation_manifest['compilation_scope'] == scope, 'generation scope/report mismatch')
    coverage = _generation_coverage(generation_bundle, candidates, scope, generation_report['errors'])
    _require('coverage' not in generation_report or coverage == generation_report['coverage'],
             'generation coverage report mismatch')
    return {'generation_manifest': generation_manifest, 'classification_manifest': classification_manifest,
            'generation_report': generation_report,
            'classification_report': classification_report, 'bundle': bundle, 'candidates': candidates, 'outcomes': outcomes,
            'coverage': coverage, 'taxonomy': taxonomy}
