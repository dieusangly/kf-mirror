"""Validate the 13-document pilot's frozen structuring output without model calls."""
import argparse
import json
from pathlib import Path
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from knowledge_foundry.ingestion import load_ingested
from knowledge_foundry.document_structuring import validate_frozen, audit_structure
from knowledge_foundry.document_structure import ancestor_ids
from knowledge_foundry.embeddings import token_count


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text).casefold() if c.isalnum())


def passage_elements(elements, source_id, text, documents, pages=None):
    """Locate a source passage across adjacent elements on the same page.

    Provider boundaries and IDs differ. Page/source identity and literal text
    define the regression, while each matched element retains its evidence ID.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for element in elements.values():
        page = element['location'].get('page')
        if (documents[element['document_id']]['source_id'] == source_id
                and (not pages or page in pages)):
            groups[(element['document_id'], page)].append(element)
    found, wanted = [], normalized(text)
    if not wanted:
        return found
    for group in groups.values():
        group.sort(key=lambda e: e['ordinal'])
        values = [normalized(e['text']) for e in group]
        stream = ''.join(values)
        start = stream.find(wanted)
        while start >= 0:
            offset = 0
            for element, value in zip(group, values):
                end = offset + len(value)
                if value and offset < start + len(wanted) and end > start:
                    found.append(element)
                offset = end
            start = stream.find(wanted, start + 1)
    return found


def scope_regressions(elements, hierarchy, documents, regressions):
    results = []
    for case in regressions:
        targets = passage_elements(elements, case['source_id'], case['text'], documents, [case['page']])
        forbidden = passage_elements(elements, case['source_id'], case['forbidden_ancestor_text'], documents)
        forbidden_ids = {e['id'] for e in forbidden}
        bad = []
        for target in targets:
            for aid in ancestor_ids(target['id'], hierarchy):
                if aid in forbidden_ids:
                    bad.append(aid)
        merged = bool({e['id'] for e in targets} & forbidden_ids)
        results.append({'source_id': case['source_id'], 'page': case['page'],
                        'target_element_ids': sorted({e['id'] for e in targets}),
                        'forbidden_passage_element_ids': sorted(forbidden_ids),
                        'forbidden_ancestor_ids': sorted(set(bad)), 'merged_scope_passages': merged,
                        'pass': bool(targets) and bool(forbidden) and not bad and not merged})
    return results


def load_run_ingestion(run_dir):
    """Pin shared source bytes independently of a dated provider run."""
    run = Path(run_dir).resolve()
    descriptor = run / 'run.json'
    if descriptor.exists():
        reference = json.loads(descriptor.read_text())['ingestion_snapshot']
        return load_ingested(run / reference)
    return load_ingested(run / 'ingestion')


def validate(run_dir):
    run = Path(run_dir)
    ingestion = load_run_ingestion(run)
    structured = run / 'document-structuring/chunks'
    manifest = validate_frozen(structured)
    bundle = json.loads((structured / 'artifact-bundle.json').read_text())
    metadata = json.loads((structured / 'structure-metadata.json').read_text())
    inventory = json.loads((ROOT / 'data/manifest.json').read_text())['sources']
    expected = {e['manifest_id'] for e in inventory}
    if len(expected) != 13 or {r['manifest_id'] for r in ingestion['outcomes']} != expected:
        raise ValueError('pilot must contain exactly the 13 manifest documents')
    if len(bundle['documents']) != 13 or any(r['status'] != 'ingested' for r in ingestion['outcomes']):
        raise ValueError('pilot ingestion/structuring is incomplete')
    if {d['id'] for d in bundle['documents']} != {r['document']['id'] for r in ingestion['outcomes']}:
        raise ValueError('ingested and structured document identities differ')
    if {r['manifest_id']: r['document']['content_sha256'] for r in ingestion['outcomes']} != {e['manifest_id']: e['sha256'] for e in inventory}:
        raise ValueError('pilot snapshot does not match the active manifest editions')
    import pypdfium2 as pdfium
    pdf_pages, pdf_documents = 0, 0
    for outcome in ingestion['outcomes']:
        if outcome['source']['format'] == 'pdf':
            with pdfium.PdfDocument(outcome['artifact_file']) as pdf:
                pdf_documents += 1
                pdf_pages += len(pdf)
    if (pdf_documents, pdf_pages) != (11, 801):
        raise ValueError('pilot physical PDF scope differs from 11 PDFs / 801 pages')
    audit = audit_structure(bundle, metadata)
    elements = {e['id']: e for e in bundle['elements']}
    hierarchy = {r['element_id']: r for r in json.loads((structured / 'document-hierarchy.json').read_text())}
    regressions = json.loads((ROOT / 'tests/fixtures/structuring-pilot-regressions.json').read_text())
    cases = json.loads((ROOT / 'tests/fixtures/retrieval-expanded-development.json').read_text())
    documents = {d['id']: d for d in bundle['documents']}
    scope_results = scope_regressions(elements, hierarchy, documents, regressions)
    anchor_results = []
    for case in cases:
        matched = []
        for anchor in case['text_anchors']:
            found = passage_elements(elements, case['source_id'], anchor, documents, case.get('pages'))
            covered = [c['id'] for c in bundle['chunks'] if any(r['element_id'] in {e['id'] for e in found}
                                                              and 'span' in r for r in c['evidence'])]
            matched.append(bool(covered))
        anchor_results.append({'id': case['id'], 'anchors_checked': len(matched), 'pass': all(matched)})
    rows = [json.loads(line) for line in (structured / 'structural-chunks.jsonl').read_text().splitlines()]
    passed = audit['gate'] == 'pass' and all(case['pass'] for case in anchor_results + scope_results)
    return {'status': 'pass' if passed else 'fail', 'documents': 13, 'pdf_documents': pdf_documents, 'pdf_pages': pdf_pages,
            'elements': len(elements), 'chunks': len(bundle['chunks']), 'structural_chunks': len(rows),
            'figure_assets': len(manifest.get('assets', {})), 'coverage': audit,
            'critical_scope_regressions_checked': len(regressions), 'scope_regressions': scope_results,
            'development_anchors': anchor_results,
            'largest_structural_input_tokens': max(token_count(r['text']) for r in rows),
            'accepted': (structured / 'acceptance.json').is_file(),
            'limitation': 'Mechanical regression checks do not replace fresh source/layout review or semantic retrieval evaluation.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        report = validate(args.run_dir)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.write_text(rendered)
        print(rendered)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'pilot validation failed: {exc}\n')
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
