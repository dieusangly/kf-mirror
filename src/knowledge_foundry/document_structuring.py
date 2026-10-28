"""Freeze, review and accept structural output before semantic processing."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from knowledge_foundry.document_structure import assemble_document_hierarchy
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash


OUTPUTS = ('artifact-bundle.json', 'document-structure.json', 'document-hierarchy.json',
           'structural-chunks.jsonl', 'structural-links.jsonl', 'structure-metadata.json', 'figure-links.json')


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze_structure(directory, parsed_dir):
    """Document Structuring owns the final chunks, relationships and rich metadata."""
    directory, parsed_dir = Path(directory), Path(parsed_dir)
    bundle, structure = read(directory / 'artifact-bundle.json'), read(directory / 'document-structure.json')
    records, links = assemble_structural_chunks(bundle, structure)
    write(directory / 'document-hierarchy.json', assemble_document_hierarchy(bundle, structure))
    for filename, rows in [('structural-chunks.jsonl', records), ('structural-links.jsonl', links)]:
        (directory / filename).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows))
    metadata, native_outputs = [], {}
    for document in bundle['documents']:
        sidecar = parsed_dir / 'documents' / document['id'].removeprefix('doc:') / 'parse.json'
        metadata.append(read(sidecar))
        for name in ('provider.json', 'docling.json'):
            original = sidecar.with_name(name)
            if original.exists():
                relative = Path('documents') / document['id'].removeprefix('doc:') / name
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                native_outputs[str(relative)] = file_hash(target)
    write(directory / 'structure-metadata.json', metadata)
    manifest = {'version': 1, 'stage': 'document-structuring',
                'outputs': {name: file_hash(directory / name) for name in OUTPUTS},
                'native_outputs': native_outputs,
                'assets': {r['asset']['path']: r['asset']['sha256'] for r in read(directory / 'figure-links.json')},
                'ingested_artifact_ids': sorted({m['ingested_artifact_id'] for m in metadata if m.get('ingested_artifact_id')})}
    manifest['snapshot_sha256'] = stable_hash(manifest)
    write(directory / 'structuring-manifest.json', manifest)
    (directory / 'acceptance.json').unlink(missing_ok=True)
    return manifest


def validate_frozen(directory):
    directory = Path(directory)
    manifest = read(directory / 'structuring-manifest.json')
    if manifest.get('stage') != 'document-structuring' or manifest.get('version') != 1:
        raise ValueError('invalid Document Structuring manifest')
    if set(manifest['outputs']) != set(OUTPUTS) or any(
            file_hash(directory / name) != sha for name, sha in manifest['outputs'].items()):
        raise ValueError('structuring output hash mismatch')
    for name, sha in {**manifest.get('assets', {}), **manifest.get('native_outputs', {})}.items():
        target = (directory / name).resolve()
        if not target.is_relative_to(directory.resolve()) or file_hash(target) != sha:
            raise ValueError('structuring native output or figure asset hash mismatch')
    if manifest['snapshot_sha256'] != stable_hash({k: v for k, v in manifest.items() if k != 'snapshot_sha256'}):
        raise ValueError('structuring snapshot identity mismatch')
    return manifest


def audit_structure(bundle, metadata):
    """Provider-independent evidence coverage and structure conservation checks."""
    from scripts.validate_artifact_examples import check
    check(bundle)
    elements = {e['id']: e for e in bundle['elements']}
    details = {r['element_id']: r for m in metadata for r in m['elements']}
    if set(details) != set(elements):
        raise ValueError('structure metadata membership mismatch')
    return audit_evidence(bundle['elements'], bundle['chunks'], details)


def audit_evidence(element_rows, chunks, details):
    from knowledge_foundry.chunking import clean, _location_key
    elements = {e['id']: e for e in element_rows}
    owners, coverage = {}, {}
    for chunk in chunks:
        from knowledge_foundry.evidence import claim_evidence_references
        for ref in claim_evidence_references(chunk, elements):
            if 'span' in ref:
                eid = ref['element_id']
                owners.setdefault(eid, set()).add(chunk['id'])
                coverage.setdefault(eid, []).append((ref['span']['start'], ref['span']['end']))
    findings, required, covered = [], 0, 0
    for eid, e in elements.items():
        if details[eid].get('evidence_role') != 'authoritative' or not clean(e['text']):
            continue
        required += 1
        intervals = sorted(coverage.get(eid, []))
        end = 0
        for left, right in intervals:
            if e['text'][end:left].strip():
                break
            end = max(end, right)
        if e['text'][end:].strip():
            # Identical representations at exactly the same source location may
            # share one witness; repeated wording elsewhere must be covered.
            duplicate = any(other != eid and elements[other]['kind'] == e['kind']
                            and elements[other]['document_id'] == e['document_id']
                            and _location_key(elements[other]) == _location_key(e)
                            and clean(elements[other]['text']) == clean(e['text']) for other in coverage)
            if not duplicate:
                findings.append({'code': 'STRUCTURAL_CONTENT_MISSING', 'element_id': eid, 'severity': 'error'})
                continue
        covered += 1
        if e['kind'] in {'table', 'list_item'} and len(owners.get(eid, ())) > 1:
            findings.append({'code': 'STRUCTURAL_UNIT_SPLIT', 'element_id': eid, 'severity': 'error'})
    return {'gate': 'fail' if findings else 'pass', 'authoritative_elements': required,
            'covered_elements': covered, 'findings': findings}


def accept_structure(directory, parsing_review=None, chunk_review=None, *, test_only=False):
    """Acceptance binds the exact reviewed output; it never edits reviewer judgments."""
    directory = Path(directory)
    manifest = validate_frozen(directory)
    audit = audit_structure(read(directory / 'artifact-bundle.json'), read(directory / 'structure-metadata.json'))
    if audit['gate'] != 'pass':
        raise ValueError('structural automatic gate failed: ' + str(audit['findings'][:3]))
    evidence = {}
    if not test_only:
        for name, path in [('parsing', parsing_review), ('chunking', chunk_review)]:
            if path is None:
                raise ValueError('acceptance requires parsing and chunking source reviews')
            report = read(path)
            if any(report.get(key) != 'pass' for key in ('automatic_gate', 'review_gate', 'gate')):
                raise ValueError(name + ' review has not passed')
            reviewer = report.get('reviewer') or {}
            if reviewer.get('kind') not in {'human', 'agent'} or not reviewer.get('name'):
                raise ValueError('acceptance requires an identified source reviewer')
            if name == 'chunking' and report.get('structuring_snapshot_sha256') != manifest['snapshot_sha256']:
                raise ValueError('chunking review does not match current structuring snapshot')
            if name == 'parsing':
                # Existing native audits bind every parse artifact and original.
                files = report.get('input_files', {})
                if not files or any(not Path(file).is_file() or file_hash(file) != sha for file, sha in files.items()):
                    raise ValueError('parsing review evidence is missing or stale')
                metadata = read(directory / 'structure-metadata.json')
                reviewed_docs = {m['document_id'] for file in files if Path(file).name == 'parse.json'
                                 for m in [read(file)]}
                if reviewed_docs != {m['document_id'] for m in metadata}:
                    raise ValueError('parsing review document selection mismatch')
                for m in metadata:
                    if not any(Path(file).name == 'parse.json' and read(file) == m for file in files):
                        raise ValueError('parsing review does not cover saved structure metadata')
            evidence[name] = {'sha256': file_hash(path), 'reviewer': reviewer}
        # Retain review evidence inside accepted output for portable verification.
        for name, path in [('parsing', parsing_review), ('chunking', chunk_review)]:
            shutil.copyfile(path, directory / (name + '-review.json'))
    value = {'version': 1, 'status': 'test_only' if test_only else 'accepted',
             'snapshot_sha256': manifest['snapshot_sha256'], 'reviews': evidence, 'automatic_audit': audit}
    write(directory / 'acceptance.json', value)
    return value


def load_validated_structure(directory, *, allow_test=False):
    directory = Path(directory)
    manifest = validate_frozen(directory)
    acceptance = read(directory / 'acceptance.json')
    permitted = {'accepted', 'test_only'} if allow_test else {'accepted'}
    if acceptance['snapshot_sha256'] != manifest['snapshot_sha256'] or acceptance['status'] not in permitted:
        raise ValueError('validated structural chunks require current acceptance')
    if acceptance['status'] == 'accepted':
        if set(acceptance.get('reviews', {})) != {'parsing', 'chunking'}:
            raise ValueError('acceptance review evidence missing')
        for name, evidence in acceptance['reviews'].items():
            report = read(directory / (name + '-review.json'))
            if file_hash(directory / (name + '-review.json')) != evidence['sha256'] or report.get('gate') != 'pass':
                raise ValueError('accepted review evidence changed')
    audit = audit_structure(read(directory / 'artifact-bundle.json'), read(directory / 'structure-metadata.json'))
    if audit['gate'] != 'pass':
        raise ValueError('accepted structure failed evidence audit')
    return {'manifest': manifest, 'acceptance': acceptance,
            'bundle': read(directory / 'artifact-bundle.json'), 'structure': read(directory / 'document-structure.json'),
            'hierarchy': read(directory / 'document-hierarchy.json'),
            'chunks': [json.loads(line) for line in (directory / 'structural-chunks.jsonl').read_text().splitlines()],
            'links': [json.loads(line) for line in (directory / 'structural-links.jsonl').read_text().splitlines()]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--parsing-review', type=Path, required=True)
    parser.add_argument('--chunk-review', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(accept_structure(args.input_dir, args.parsing_review, args.chunk_review)))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'acceptance failed: {exc}\\n')
    return 0
