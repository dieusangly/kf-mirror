"""Compare two frozen provider runs against the same original PDF text layer.

These are diagnostic proxies, not extraction accuracy or visual acceptance.
PDFium text excludes raster content; page furniture and tokenization affect
recall proxies. Sequence agreement uses PDFium's order, not adjudicated layout.
"""
import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from knowledge_foundry.document_structuring import audit_structure, validate_frozen
from scripts.validate_pilot import load_run_ingestion


def read(path):
    return json.loads(Path(path).read_text())


def tokens(text):
    value = unicodedata.normalize('NFKD', text).casefold()
    return re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c)))


def numbers(text):
    return re.findall(r'\d+(?:[.,/\-]\d+)*', text)


def agreement(expected, actual):
    ref, observed = Counter(expected), Counter(actual)
    matched = sum((ref & observed).values())
    return {'reference': len(expected), 'matched': matched,
            'coverage': round(matched / len(expected), 5) if expected else None,
            'missing_examples': list((ref - observed).elements())[:20]}


def run_metrics(run, ingestion):
    directory = run / 'document-structuring/chunks'
    frozen = validate_frozen(directory)
    bundle, metadata = read(directory / 'artifact-bundle.json'), read(directory / 'structure-metadata.json')
    expected = {r['document']['id']: r['document'] for r in ingestion['outcomes']}
    if {d['id']: d for d in bundle['documents']} != expected:
        raise ValueError('comparison document identities differ from pinned ingestion')
    details = {row['element_id']: row for m in metadata for row in m['elements']}
    grouped = defaultdict(list)
    for element in bundle['elements']:
        grouped[element['document_id']].append(element)
    sources = []
    import pypdfium2 as pdfium
    for outcome in ingestion['outcomes']:
        elements = sorted(grouped[outcome['document']['id']], key=lambda e: e['ordinal'])
        kinds = Counter(e['kind'] for e in elements)
        entry = {'manifest_id': outcome['manifest_id'], 'format': outcome['source']['format'],
                 'elements': len(elements), 'kinds': dict(kinds),
                 'located_with_bbox': sum(bool(e['location'].get('bbox')) for e in elements),
                 'headings_with_parent': sum(e['kind'] == 'heading' and bool(details[e['id']].get('parent_element_id')) for e in elements),
                 'table_html': sum(bool(details[e['id']].get('provider_metadata', {}).get('metadata', {}).get('text_as_html')) for e in elements),
                 'pages': []}
        if outcome['source']['format'] == 'pdf':
            by_page = defaultdict(list)
            for element in elements:
                if details[element['id']].get('evidence_role') == 'authoritative':
                    by_page[element['location']['page']].append(element['text'])
            with pdfium.PdfDocument(outcome['artifact_file']) as pdf:
                for i, page in enumerate(pdf, 1):
                    textpage = page.get_textpage()
                    original = textpage.get_text_range()
                    textpage.close()
                    actual = '\n'.join(by_page[i])
                    a, b = tokens(original), tokens(actual)
                    entry['pages'].append({'page': i, 'text_tokens': agreement(a, b),
                                           'numeric_tokens': agreement(numbers(original), numbers(actual)),
                                           'pdfium_order_similarity': round(SequenceMatcher(None, a, b, autojunk=False).ratio(), 5),
                                           'authoritative_characters': len(actual)})
        for key in ('text_tokens', 'numeric_tokens'):
            total = sum(p[key]['reference'] for p in entry['pages'])
            matched = sum(p[key]['matched'] for p in entry['pages'])
            entry[key] = {'reference': total, 'matched': matched,
                          'coverage': round(matched / total, 5) if total else None}
        sources.append(entry)
    parsing = read(run / 'document-structuring/parsing/report.json')
    aggregate = {}
    for key in ('text_tokens', 'numeric_tokens'):
        total = sum(s[key]['reference'] for s in sources)
        matched = sum(s[key]['matched'] for s in sources)
        aggregate[key] = {'reference': total, 'matched': matched, 'coverage': round(matched / total, 5) if total else None}
    return {'run_id': run.name, 'snapshot_sha256': frozen['snapshot_sha256'],
            'provider': parsing['provider'], 'provider_version': parsing['provider_version'],
            'settings': parsing['settings'], 'execution': parsing.get('execution'),
            'documents': len(bundle['documents']), 'elements': len(bundle['elements']),
            'kinds': dict(Counter(e['kind'] for e in bundle['elements'])),
            'structural_chunks': len((directory / 'structural-chunks.jsonl').read_text().splitlines()),
            'figure_assets': len(frozen['assets']), 'coverage': audit_structure(bundle, metadata),
            'parsing_statuses': dict(Counter(o['status'] for o in parsing['outcomes'])),
            'parser_warnings': dict(Counter(w['code'] for o in parsing['outcomes'] for w in o.get('warnings', []))),
            'recorded_parse_seconds': round(sum(o['elapsed_seconds'] for o in parsing['outcomes']), 3),
            'peak_process_rss_mb': max(o['peak_process_rss_mb'] for o in parsing['outcomes']),
            'pdf_text_layer_proxies': aggregate, 'sources': sources}


def compare(baseline, candidate):
    baseline, candidate = Path(baseline), Path(candidate)
    ingestion = load_run_ingestion(baseline)
    if load_run_ingestion(candidate)['artifact_id'] != ingestion['artifact_id']:
        raise ValueError('comparison requires the same immutable ingestion snapshot')
    results = [run_metrics(run, ingestion) for run in (baseline, candidate)]
    return {'version': 1, 'ingested_artifact_id': ingestion['artifact_id'], 'runs': results,
            'limitations': ['PDFium text-layer agreement is a diagnostic proxy, not independently labelled accuracy.',
                            'Raster text and visual semantics require original-page inspection.',
                            'Numeric tokens retain separators; line wrapping and formatting can lower agreement.',
                            'Order similarity uses PDFium order and cannot certify multi-column reading order.',
                            'Provider-native element boundaries differ; counts alone do not measure quality.',
                            'Docling timings describe retained replay/normalization; Unstructured timings describe fresh partitioning.'],
            'inputs': {str(run / 'run.json'): hashlib.sha256((run / 'run.json').read_bytes()).hexdigest()
                       for run in (baseline, candidate)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    report = compare(args.baseline, args.candidate)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({r['run_id']: {k: v for k, v in r.items() if k != 'sources'} for r in report['runs']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
