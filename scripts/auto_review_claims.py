"""Evaluate saved claim generation and classification artifacts and prepare source-linked review samples. No API calls."""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys

from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from knowledge_foundry.claim_generation_and_classification import (  # noqa: E402
    _candidate_manifest, _generation_coverage, _validate_candidate, canonical, digest, file_digest,
)
from knowledge_foundry.review import (  # noqa: E402
    apply_reviews, read_review_policy, review_policy_digest, select_samples as _select,
    validate_reviewer_settings,
)
from scripts.validate_artifact_examples import check  # noqa: E402

EVALUATION_VERSION = 'claim-eval-v3'
DIMENSIONS = ('generation', 'classification', 'completeness', 'topics')
DEFAULT_POLICY = ROOT / 'config/review-policy.json'


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


def _source_context(bundle, manifest_path, source_root):
    manifest = _read(manifest_path)
    sources = {(s['id'], s['version']): s for s in bundle['sources']}
    result, hashes, findings = {}, {}, []
    for document in bundle['documents']:
        source = sources[(document['source_id'], document['source_version'])]
        matches = [s for s in manifest['sources'] if s['source_id'] == document['source_id'] and
                   s['source_version'] == document['source_version'] and s['sha256'] == document['content_sha256']]
        location = {'source_id': source['id'], 'source_version': source['version'], 'source_uri': source['uri'],
                    'document_id': document['id'], 'source_file': None}
        if len(matches) != 1:
            findings.append({'code': 'SOURCE_MANIFEST_MISMATCH', 'severity': 'error', 'document_id': document['id']})
        else:
            entry = matches[0]
            if not (source['uri'] == entry.get('publisher_url') and source['title'] == entry.get('title') and
                    source['class'] == entry.get('class') and document['media_type'] == entry.get('media_type') and
                    document['retrieved_on'] == entry.get('retrieved_on')):
                findings.append({'code': 'SOURCE_METADATA_MISMATCH', 'severity': 'error', 'document_id': document['id']})
            path = (source_root / entry['local_path']).resolve()
            location['source_file'] = str(path)
            try:
                actual = file_digest(path)
                hashes[str(path)] = actual
                _require(actual == document['content_sha256'] and path.stat().st_size == entry['size_bytes'],
                         'saved source hash or size mismatch')
            except (OSError, ValueError) as exc:
                findings.append({'code': 'SAVED_SOURCE_INVALID', 'severity': 'error',
                                 'document_id': document['id'], 'reason': str(exc)})
        result[document['id']] = location
    return result, hashes, findings


def _samples(context, sources, count):
    bundle, candidates, outcomes = context['bundle'], context['candidates'], context['outcomes']
    elements = {e['id']: e for e in bundle['elements']}
    chunks = {c['id']: c for c in bundle['chunks']}
    order = {c['id']: i for i, c in enumerate(bundle['chunks'])}
    claims_by_chunk = defaultdict(list)
    rows = []

    def evidence(refs):
        result = []
        for ref in refs:
            element = elements[ref['element_id']]
            span = ref.get('span', {'start': 0, 'end': len(element['text'])})
            result.append({**ref, **sources[ref['document_id']], 'kind': element['kind'],
                           'location': element['location'], 'text': element['text'][span['start']:span['end']]})
        return result

    for candidate in candidates:
        for chunk_id in candidate['chunk_ids']:
            claims_by_chunk[chunk_id].append(candidate['id'])
        refs = candidate['evidence']
        own_sources = {ref['document_id'] for ref in refs}
        reasons = [kind for kind in ('footnote', 'table', 'list_item')
                   if any(elements[r['element_id']]['kind'] == kind for r in refs)]
        if len(candidate['chunk_ids']) > 1:
            reasons.append('multiple_chunks')
        if len(own_sources) > 1:
            reasons.append('multiple_documents')
        if re.search(r'exception|sauf|soupçon|au moins|habilit|incert|pas précisé', candidate['text'], re.I):
            reasons.append('qualification')
        if re.search(r'cas n[°o]?|exemple|typolog', candidate['text'], re.I):
            reasons.append('case_context')
        outcome = outcomes[candidate['id']]
        if 'case_context' in reasons and outcome['status'] == 'classified' and not re.search(
                r'critères?|recommand|doit|doivent|obligation|prévoit', candidate['text'], re.I):
            reasons.append('classified_case')
        if outcome['status'] != 'classified':
            reasons.append(outcome['status'])
        base = {'record_id': candidate['id'], 'candidate_id': candidate['id'], **outcome,
                'text': candidate['text'], 'chunk_ids': candidate['chunk_ids'],
                'evidence': evidence(refs), 'sources': [sources[did] for did in sorted(own_sources)],
                'reasons': reasons or ['coverage']}
        for dimension in ('generation', 'classification'):
            rows.append({**base, 'dimension': dimension, 'sample_id': dimension + ':' + candidate['id']})
    selected_chunks = set(context['generation_report']['compilation_scope']['selected_chunk_ids'])
    cited = {r['chunk_id'] for r in context['coverage']['chunks'] if r['candidate_count']}
    for chunk in bundle['chunks']:
        if chunk['id'] not in selected_chunks:
            continue
        reasons = []
        refs = chunk['evidence']
        if chunk['id'] not in cited:
            reasons.append('no_candidates')
        reasons += [kind for kind in ('footnote', 'table', 'list_item')
                    if any(elements[r['element_id']]['kind'] == kind for r in refs)]
        if any('span' not in r and elements[r['element_id']]['kind'] != 'heading' for r in refs):
            reasons.append('repeated_context')
        rows.append({'dimension': 'completeness', 'sample_id': 'completeness:' + chunk['id'],
                     'record_id': chunk['id'], 'chunk_id': chunk['id'], 'text': chunk['text'],
                     'candidate_ids': claims_by_chunk[chunk['id']], 'evidence': evidence(refs),
                     'sources': [sources[chunk['document_id']]], 'reasons': reasons or ['coverage']})
    populations = {row['sample_id']: row for row in rows}
    selected, selected_ids = [], set()
    for dimension in ('generation', 'classification', 'completeness'):
        for document in bundle['documents']:
            own = [row for row in rows if row['dimension'] == dimension and
                   any(s['document_id'] == document['id'] for s in row['sources'])]
            own.sort(key=lambda row: (
                min(order[cid] for cid in row.get('chunk_ids', [row.get('chunk_id')])), row['record_id']))
            if dimension == 'completeness':
                risks = ('no_candidates', 'footnote', 'table', 'list_item', 'repeated_context')
            else:
                risks = ('classified_case', 'unclassified', 'classification_error', 'case_context',
                         'footnote', 'qualification', 'multiple_documents', 'table', 'list_item', 'multiple_chunks')
            chosen = _select(own, count, risks)
            for row in chosen:
                if row['sample_id'] not in selected_ids:
                    selected.append(row)
                    selected_ids.add(row['sample_id'])
    for topic in bundle['topics']:
        claims = [c for c in bundle['claims'] if c['id'] in topic['claim_ids']]
        doc_ids = sorted({ref['document_id'] for claim in claims for ref in claim['evidence']})
        row = {'dimension': 'topics', 'sample_id': 'topics:' + topic['id'], 'record_id': topic['id'],
               'topic_id': topic['id'], 'text': topic['summary'], 'title': topic['title'],
               'claim_ids': topic['claim_ids'], 'sources': [sources[did] for did in doc_ids],
               'reasons': ['topic_view'], 'claims_to_inspect': [c['id'] for c in _select(
                   [{'reasons': [], 'id': c['id']} for c in claims], min(3, len(claims)), ())]}
        selected.append(row)
        populations[row['sample_id']] = row
    return selected, populations


def _review_metadata(review):
    reviewer = review.get('reviewer', {})
    _require(isinstance(reviewer, dict), 'reviewer must be an object')
    validate_reviewer_settings(reviewer)
    assessed = any(row.get('status') in {'pass', 'fail'} for row in review['reviews']) or review.get('additional_findings')
    if assessed:
        _require(reviewer.get('kind') in {'human', 'agent'} and
                 isinstance(reviewer.get('name'), str) and reviewer['name'].strip(),
                 'reviewer needs kind human/agent and a name')
    telemetry = review.get('telemetry', {})
    _require(isinstance(telemetry, dict), 'review telemetry must be an object')
    for key in ('duration_seconds', 'requests', 'estimated_cost_usd'):
        value = telemetry.get(key)
        _require(value is None or isinstance(value, (float, int)) and not isinstance(value, bool) and
                 math.isfinite(value) and value >= 0 and (key != 'requests' or isinstance(value, int)),
                 'invalid review telemetry ' + key)
    if telemetry.get('usage') is not None:
        _require(isinstance(telemetry['usage'], dict) and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                 for v in telemetry['usage'].values()), 'invalid review token usage')
    start, finish = telemetry.get('started_at'), telemetry.get('finished_at')
    for value in (start, finish):
        _require(value is None or datetime.fromisoformat(value).utcoffset() is not None,
                 'review timestamps must include a timezone')
    if start is not None and finish is not None:
        duration = (datetime.fromisoformat(finish) - datetime.fromisoformat(start)).total_seconds()
        _require(duration >= 0, 'review finishes before it starts')
        telemetry = {**telemetry, 'duration_seconds': duration}
    return reviewer, telemetry


def evaluate(candidates_dir, classified_dir, sample_count=12, review_path=None,
             manifest_path=ROOT / 'data/manifest.json', taxonomy_path=ROOT / 'config/topics-v1.json',
             review_policy_path=DEFAULT_POLICY, source_root=ROOT):
    """Return scores from reviewer judgments, never infer qualitative pass from artifacts."""
    _require(isinstance(sample_count, int) and not isinstance(sample_count, bool) and sample_count > 0,
             'sample-per-document must be an integer >= 1')
    policy = read_review_policy(review_policy_path, subject='claim_generation_and_classification')
    candidates_dir, classified_dir, source_root = Path(candidates_dir), Path(classified_dir), Path(source_root)
    try:
        context = _load(candidates_dir, classified_dir, Path(taxonomy_path))
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        return {'evaluation_version': EVALUATION_VERSION, 'automatic_gate': 'fail', 'review_gate': 'pending',
                'gate': 'fail', 'findings': [{'code': 'COMPILATION_INVALID', 'severity': 'error', 'reason': str(exc)[:500]}],
                'review_sample': [], 'review_policy': policy}
    bundle = context['bundle']
    sources, source_hashes, findings = _source_context(bundle, Path(manifest_path), source_root)
    generation_report = context['generation_report']
    classification_report = context['classification_report']
    if (generation_report['status'] != 'complete' or classification_report['status'] != 'complete' or
            generation_report['errors'] or classification_report['errors']):
        findings.append({'code': 'STAGE_INCOMPLETE', 'severity': 'error', 'generation_errors': generation_report['errors'],
                         'classification_errors': classification_report['errors']})
    if any(c['grounding_review_status'] != 'draft' or c['classification_review_status'] != 'draft' for c in bundle['claims']) or \
            any(t['review_status'] != 'draft' for t in bundle['topics']):
        findings.append({'code': 'OUTPUTS_NOT_DRAFT', 'severity': 'error'})
    samples, populations = _samples(context, sources, sample_count)
    snapshot = {'evaluation_version': EVALUATION_VERSION,
                'candidate_manifest_sha256': file_digest(candidates_dir / 'build-manifest.json'),
                'classification_manifest_sha256': file_digest(classified_dir / 'build-manifest.json'),
                'source_manifest_sha256': file_digest(manifest_path), 'source_hashes': source_hashes,
                'taxonomy_sha256': file_digest(taxonomy_path), 'review_policy_sha256': review_policy_digest(policy, 'claim_generation_and_classification'),
                'sample_per_document': sample_count, 'sample_sha256': digest(canonical(samples))}
    snapshot_sha = digest(canonical(snapshot))
    review = _read(review_path) if review_path else None
    extra_samples, extra_reviews = [], []
    reviewer, telemetry = None, None
    if review is not None:
        _require(isinstance(review, dict) and isinstance(review.get('reviews'), list) and
                 all(isinstance(row, dict) for row in review['reviews']), 'review needs a reviews array of objects')
        _require(review.get('snapshot_sha256') == snapshot_sha, 'stale review snapshot; inputs, policy or sampling changed')
        _require(isinstance(review.get('additional_findings', []), list), 'additional_findings must be an array')
        for row in review['reviews']:
            _require(row.get('sample_id') in populations and row.get('dimension') == populations[row['sample_id']]['dimension'],
                     'review sample ID or dimension mismatch')
        reviewer, telemetry = _review_metadata(review)
        sampled_ids = {sample['sample_id'] for sample in samples}
        for item in review.get('additional_findings', []):
            _require(isinstance(item, dict), 'additional findings must be objects')
            sid = item.get('sample_id')
            _require(sid in populations and sid not in sampled_ids and item.get('status') == 'fail',
                     'additional findings need unique, unsampled compilation IDs and fail status')
            _require(item.get('dimension') == populations[sid]['dimension'], 'additional finding dimension mismatch')
            extra_samples.append(populations[sid])
            extra_reviews.append(item)
            sampled_ids.add(sid)
        review = {**review, 'reviews': review['reviews'] + extra_reviews}
    assessed_samples = samples + extra_samples
    summary, review_findings = apply_reviews(review, classification_report['build_id'], assessed_samples, policy, 'sample_id')
    dimensions = {}
    for dimension in DIMENSIONS:
        own = [s for s in assessed_samples if s['dimension'] == dimension]
        own_ids = {s['sample_id'] for s in own}
        own_review = {**review, 'reviews': [r for r in review['reviews'] if r.get('sample_id') in own_ids]} if review else None
        result, own_findings = apply_reviews(own_review, classification_report['build_id'], own, policy, 'sample_id')
        findings.extend({**finding, 'dimension': dimension} for finding in own_findings)
        dimensions[dimension] = {**result, 'status': result['status'] if own else 'not_applicable'}
    dimension_statuses = {result['status'] for result in dimensions.values()}
    if 'fail' in dimension_statuses or summary['severity_counts']['critical'] > policy['maximum_critical_failures']:
        review_gate = 'fail'
    elif 'pending' in dimension_statuses:
        review_gate = 'pending'
    else:
        review_gate = 'pass'
    findings.extend(review_findings)
    automatic_failed = any(f['severity'] == 'error' and f['code'] != 'MANUAL_REVIEW_POLICY_FAILED'
                           for f in findings)
    automatic_gate = 'fail' if automatic_failed else 'pass'
    if automatic_gate == 'fail' or review_gate == 'fail':
        gate = 'fail'
    elif review_gate == 'pass':
        gate = 'pass'
    else:
        gate = 'pending_review'
    return {'evaluation_version': EVALUATION_VERSION, 'build_id': classification_report['build_id'], 'snapshot_sha256': snapshot_sha,
            'snapshot': snapshot, 'automatic_gate': automatic_gate, 'review_gate': review_gate, 'gate': gate,
            'corpus': {'documents': len(bundle['documents']),
                       'selected_chunks': generation_report['compilation_scope']['selected_chunks'],
                       'candidates': len(context['candidates']), 'claims': len(bundle['claims']),
                       'unclassified': len(classification_report['unclassified_candidate_ids']),
                       'topics': len(bundle['topics'])},
            'coverage': context['coverage'], 'review_policy': policy,
            'review': {**summary, 'status': review_gate}, 'dimensions': dimensions,
            'taxonomy': context['taxonomy'],
            'reviewer': reviewer, 'review_telemetry': telemetry,
            'review_sha256': file_digest(review_path) if review_path else None,
            'preparation': generation_report.get('preparation'),
            'evidence_accounting': generation_report.get('evidence_accounting'),
            'compilation_telemetry': {'embeddings': generation_report.get('preparation', {}).get('embeddings'),
                'claim_generation_request_token_counting': generation_report.get(
                    'claim_generation_request_token_counting', generation_report.get('token_counting')),
                'generation': generation_report['telemetry'],
                'classification': classification_report['telemetry'],
                'generation_duration_seconds': generation_report.get('duration_seconds'),
                'classification_duration_seconds': classification_report.get('duration_seconds')},
            'review_sample': samples, 'additional_findings': extra_reviews, 'findings': findings,
            'review_template': {'build_id': classification_report['build_id'], 'snapshot_sha256': snapshot_sha,
                'reviewer': {'kind': None, 'name': None, 'model': None,
                             'model_reasoning_effort': None, 'launch_mode': None},
                'telemetry': {'started_at': None, 'finished_at': None, 'duration_seconds': None,
                              'requests': None, 'usage': None, 'estimated_cost_usd': None},
                'reviews': [{'sample_id': s['sample_id'], 'dimension': s['dimension'], 'status': 'pending',
                             'severity': None, 'notes': 'source inspection pending'} for s in samples],
                'additional_findings': []},
            'limitation': 'Source-linked sample review, not a whole-corpus accuracy estimate or verified legal interpretation.'}


def _protect_outputs(paths, candidates_dir, classified_dir, manifest, taxonomy, source_root):
    protected = {Path(manifest).resolve(), Path(taxonomy).resolve()}
    for directory in (candidates_dir, classified_dir):
        directory = Path(directory)
        protected.add((directory / 'build-manifest.json').resolve())
        value = _read(directory / 'build-manifest.json')
        protected.update((directory / item['path']).resolve() for item in value['outputs'])
    protected.update((Path(source_root) / item['local_path']).resolve() for item in _read(manifest)['sources'])
    _require(not any(Path(path).resolve() in protected for path in paths if path), 'evaluation output would overwrite an input artifact')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates-dir', type=Path, required=True, help='directory containing saved candidate claims')
    parser.add_argument('--classified-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/manifest.json')
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--taxonomy', type=Path, default=ROOT / 'config/topics-v1.json')
    parser.add_argument('--sample-per-document', type=int, default=12)
    parser.add_argument('--review-policy', type=Path, default=DEFAULT_POLICY)
    parser.add_argument('--review-file', type=Path)
    parser.add_argument('--initialize-review', nargs='?', type=Path, const=None, default=argparse.SUPPRESS, metavar='PATH')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    output = args.output or args.classified_dir / 'compilation-evaluation-report.json'
    seed = getattr(args, 'initialize_review', None)
    if hasattr(args, 'initialize_review') and seed is None:
        seed = args.classified_dir / 'compilation-review.json'
    if seed and args.review_file:
        parser.error('--initialize-review cannot be used with --review-file')
    if seed and seed.exists():
        parser.error(f'review file already exists: {seed}')
    if ((seed and seed.resolve() == output.resolve()) or
            (args.review_file and args.review_file.resolve() == output.resolve())):
        parser.error('review path must differ from --output')
    try:
        _protect_outputs([output, seed], args.candidates_dir, args.classified_dir, args.manifest, args.taxonomy, args.source_root)
        _require(output.resolve() != args.review_policy.resolve(), 'evaluation output would overwrite its policy')
        report = evaluate(args.candidates_dir, args.classified_dir, args.sample_per_document, args.review_file,
                          args.manifest, args.taxonomy, args.review_policy, args.source_root)
        if seed and 'review_template' in report:
            seed.parent.mkdir(parents=True, exist_ok=True)
            with seed.open('x', encoding='utf-8') as handle:
                handle.write(json.dumps(report['review_template'], ensure_ascii=False, indent=2) + '\n')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'claim evaluation failed: {exc}\n')
    print(json.dumps({'automatic_gate': report['automatic_gate'], 'review_gate': report['review_gate'],
                      'gate': report['gate'], 'report': str(output)}))
    return 1 if report['gate'] == 'fail' or args.review_file and report['gate'] != 'pass' else 0


if __name__ == '__main__':
    raise SystemExit(main())
