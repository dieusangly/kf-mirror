"""Source-linked parsing review selection and scoring; no model calls or approvals."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from knowledge_foundry.review import apply_reviews, read_review_policy, review_policy_digest, select_samples


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def prepare(audit, outcomes, source_root, manifest_path, policy_path, count=12,
            required_locations=(), review_path=None, reference_path=None):
    """Audit every input; inspect all table/warning/required pages plus spread samples.

    Page samples contain all parsed elements on that page, including auxiliary
    evidence. HTML samples cover the entire saved document. No sampled review is
    advertised as a visual inspection of every PDF page.
    """
    if type(count) is not int or count < 1:
        raise ValueError('sample-per-document must be a positive integer')
    policy = read_review_policy(policy_path, 'parsing')
    manifest = read(manifest_path)
    entries = {e['manifest_id']: e for e in manifest['sources']}
    files = {str(Path(manifest_path).resolve()): digest(manifest_path)}
    repository = Path(__file__).resolve().parents[2]
    for tool in [Path(__file__), Path(__file__).with_name('review.py'),
                 repository / 'scripts/auto_review_parsing.py',
                 Path(__file__).parent / 'prompts/agent_prompt_parsing_review']:
        files[str(tool.resolve())] = digest(tool)
    samples, coverage, matched = [], [], set()
    for outcome in outcomes:
        if outcome['status'] not in {'parsed', 'partial'}:
            continue
        entry = entries[outcome['manifest_id']]
        source = Path(source_root) / entry['local_path']
        if digest(source) != entry['sha256']:
            raise ValueError('source hash mismatch: ' + outcome['manifest_id'])
        files[str(source.resolve())] = digest(source)
        folder = Path(outcome['output'])
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                files[str(path.resolve())] = digest(path)
        bundle, sidecar = read(folder / 'artifact-bundle.json'), read(folder / 'parse.json')
        if (bundle['sources'][0]['id'] != entry['source_id'] or
                bundle['documents'][0]['content_sha256'] != entry['sha256']):
            raise ValueError('source manifest identity mismatch')
        by_page = defaultdict(list)
        for element in bundle['elements']:
            by_page[element['location'].get('page')].append(element)
        pdf = bundle['sources'][0]['format'] == 'pdf'
        pages = list(range(1, outcome['page_count'] + 1)) if pdf else [None]
        if pdf and (not pages or any(p not in pages for p in by_page)):
            raise ValueError('invalid page population')
        mandatory = set()
        for index, request in enumerate(required_locations):
            if not isinstance(request, dict) or not request.get('source_id') or type(request.get('page')) is not int or request['page'] < 1:
                raise ValueError('required review pages need source_id and positive integer page')
            if request['source_id'] == entry['source_id']:
                if not pdf or request['page'] not in pages:
                    raise ValueError('required review page unavailable')
                mandatory.add(request['page'])
                matched.add(index)
        warning_locations = []
        native = read(folder / 'docling.json') if (folder / 'docling.json').exists() else {}
        native_pages = {item.get('self_ref'): {prov['page_no'] for prov in item.get('prov', [])}
                        for category in ('texts', 'pictures', 'tables') for item in native.get(category, [])}
        for warning in sidecar.get('warnings', []):
            page = warning.get('page', warning.get('location', {}).get('page'))
            located = {page} if page is not None else set()
            for ref in [warning.get('picture_ref'), warning.get('ref'), *warning.get('refs', [])]:
                located.update(native_pages.get(ref, set()))
            warning_locations.append((warning, located))
        rows = []
        for page in pages:
            elements = by_page[page]
            reasons = sorted({e['kind'] for e in elements})
            warnings = [w for w, located in warning_locations if not located or page in located]
            # Warnings without page locations apply to the whole document.
            if warnings:
                reasons.append('warning')
            if page in mandatory:
                reasons.append('required')
            if not elements:
                reasons.append('no_parsed_elements')
            row = {'sample_id': identity([entry['source_id'], page]), 'source_id': entry['source_id'],
                   'manifest_id': entry['manifest_id'], 'source_file': entry['local_path'],
                   'location': {'format': 'pdf', 'page': page} if pdf else {'format': bundle['sources'][0]['format']},
                   'reasons': reasons, 'elements': elements, 'warnings': warnings,
                   'parsed_directory': str(folder.resolve())}
            rows.append(row)
        selected = select_samples(rows, count, ('table', 'footnote', 'figure', 'heading', 'list_item', 'no_parsed_elements'))
        for row in rows:
            if set(row['reasons']) & {'table', 'warning', 'required'} and row not in selected:
                selected.append(row)
        samples.extend(selected)
        coverage.append({'manifest_id': entry['manifest_id'], 'source_id': entry['source_id'],
                         'total_units': len(rows), 'selected_units': len(selected),
                         'unit': 'PDF page' if pdf else 'whole document',
                         'all_table_pages_selected': True})
    if matched != set(range(len(required_locations))):
        raise ValueError('required review source unavailable')
    if reference_path:
        files[str(Path(reference_path).resolve())] = digest(reference_path)
    policy_hash = review_policy_digest(policy, 'parsing')
    snapshot = identity({'files': files, 'samples': samples, 'policy': policy_hash,
                         'audit': audit, 'selection_version': 1})
    review = read(review_path) if review_path else None
    if review is not None:
        for key, value in [('build_id', snapshot), ('snapshot_sha256', snapshot), ('review_policy_sha256', policy_hash)]:
            if review.get(key) != value:
                raise ValueError('stale parsing review: ' + key)
        if {r.get('sample_id') for r in review.get('reviews', [])} != {s['sample_id'] for s in samples}:
            raise ValueError('parsing review sample selection mismatch')
        assessed = [r for r in review['reviews'] if r.get('status') in {'pass', 'fail'}]
        reviewer = review.get('reviewer') or {}
        if assessed and (reviewer.get('kind') not in {'agent', 'human'} or not reviewer.get('name') or
                         (reviewer['kind'] == 'agent' and not reviewer.get('model'))):
            raise ValueError('assessed parsing reviews require reviewer identity and actual agent model')
        for row in assessed:
            evidence = row.get('inspection', {})
            if evidence.get('original_inspected') is not True or not isinstance(row.get('notes'), str) or not row['notes'].strip():
                raise ValueError('assessed parsing reviews require source inspection evidence and notes')
            sample = next(s for s in samples if s['sample_id'] == row['sample_id'])
            if sample['location']['format'] == 'pdf' and evidence.get('layout_inspected') is not True:
                raise ValueError('PDF review requires layout inspection')
    summary, findings = apply_reviews(review, snapshot, samples, policy, 'sample_id')
    inspected_ids = {r['sample_id'] for r in review['reviews'] if r['status'] in {'pass', 'fail'}} if review else set()
    for row in coverage:
        row['inspected_units'] = sum(s['source_id'] == row['source_id'] and s['sample_id'] in inspected_ids
                                     for s in samples)
    # Additional defects outside selected pages block acceptance; never silently drop them.
    additional = review.get('additional_findings', []) if review else []
    if not isinstance(additional, list) or any(not isinstance(f, dict) or not f.get('notes') or
            not f.get('source_id') or not isinstance(f.get('location'), dict) for f in additional):
        raise ValueError('additional findings require source, location and notes')
    if additional:
        summary['status'] = 'fail'
    automatic = 'pass' if audit['gate']['status'] == 'pass' else 'fail'
    gate = 'fail' if automatic == 'fail' or summary['status'] == 'fail' else (
        'pass' if summary['status'] == 'pass' else 'pending_review')
    return {'evaluation_version': 'parsing-review-v1', 'build_id': snapshot, 'snapshot_sha256': snapshot,
            'review_policy_sha256': policy_hash, 'input_files': files, 'automatic_gate': automatic,
            'review_gate': summary['status'], 'gate': gate, 'automatic_audit': audit,
            'review_sample': samples, 'coverage': coverage, 'review': summary,
            'findings': findings, 'additional_findings': additional,
            'reviewer': review.get('reviewer') if review else None,
            'limitation': 'Automated checks cover all inputs; visual review covers only the recorded selected units.'}


def seed(report):
    return {k: report[k] for k in ('build_id', 'snapshot_sha256', 'review_policy_sha256')} | {
        'reviewer': {'kind': None, 'name': None, 'model': None}, 'additional_findings': [],
        'reviews': [{'sample_id': s['sample_id'], 'status': 'pending', 'notes': 'Source inspection pending',
                     'inspection': {'original_inspected': False, 'layout_inspected': False}}
                    for s in report['review_sample']]}
