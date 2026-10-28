"""Deterministic prepare/finalize phases around source inspection by a reviewer."""
import argparse
import json
from pathlib import Path
import re
import sys

from knowledge_foundry import chunking_audit, parsing_audit, parsing_review
from knowledge_foundry.document_structuring import accept_structure, validate_frozen

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def _uninspected(review):
    """Only empty generated templates may be refreshed without losing findings."""
    return (not review.get('additional_findings') and
            all(row.get('status') == 'pending' and
                row.get('notes', '').casefold() in {'', 'source inspection pending'} and
                not any(row.get('inspection', {}).values())
                for row in review.get('reviews', [])))


def _matches(review, template):
    identity = set(template) - {'reviewer', 'reviews', 'additional_findings'}
    key = 'sample_id' if 'snapshot_sha256' in template else 'chunk_id'
    return (all(review.get(k) == template[k] for k in identity) and
            sorted(row.get(key, '') for row in review.get('reviews', [])) ==
            sorted(row[key] for row in template['reviews']))


def _package(phase, folder, fresh, template, evaluate):
    path = folder / 'review.json'
    if path.exists():
        saved = read(path)
        if _matches(saved, template):
            report = evaluate(path)
        elif phase == 'prepare' and _uninspected(saved):
            write(path, template)
            report = fresh
        else:
            raise ValueError(f'stale review at {path}; retain judgments/findings and use a fresh review directory')
    elif phase == 'prepare':
        write(path, template)
        report = fresh
    else:
        raise ValueError(f'missing review: {path}; run prepare first')
    write(folder / 'report.json', report)
    return report


def review_structure(phase, *, run_id=None, run_dir=None, parsed_dir=None, chunked_dir=None,
                     parsing_review_dir=None, chunk_review_dir=None, checkpoint='all',
                     source_root=ROOT, manifest=ROOT / 'data/manifest.json',
                     review_policy=ROOT / 'config/review-policy.json', sample_per_document=12,
                     required_pages=None, parsing_reference=None, chunking_reference=None,
                     output=None, audit_only=False, on_progress=None):
    """Reuse saved inputs; never invoke providers or create source judgments.

    Full-run finalize accepts only after both gates pass. A controller may select
    one checkpoint, leaving acceptance to its existing accept checkpoint.
    """
    if run_id is not None:
        if run_dir is not None or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', run_id):
            raise ValueError('run ID must be a safe directory name and cannot accompany --run-dir')
        run_dir = ROOT / 'artifacts' / run_id
    base = Path(run_dir).resolve() / 'document-structuring' if run_dir else None
    parsed = Path(parsed_dir).resolve() if parsed_dir else base / 'parsing' if base else None
    chunked = Path(chunked_dir).resolve() if chunked_dir else base / 'chunks' if base else None
    folders = {
        'parsing': Path(parsing_review_dir).resolve() if parsing_review_dir else base / 'parsing-review' if base else None,
        'chunking': Path(chunk_review_dir).resolve() if chunk_review_dir else base / 'chunk-review' if base else None,
    }
    selected = ('parsing', 'chunking') if checkpoint == 'all' else (checkpoint,)
    if phase not in {'prepare', 'finalize'} or checkpoint not in {'all', 'parsing', 'chunking'}:
        raise ValueError('unsupported review phase/checkpoint')
    if type(sample_per_document) is not int or sample_per_document < 1:
        raise ValueError('sample-per-document must be a positive integer')
    if parsed is None or ('chunking' in selected and chunked is None):
        raise ValueError('select a run or supply the required parsed/chunked input directories')
    if audit_only and (phase != 'prepare' or checkpoint != 'parsing'):
        raise ValueError('--audit-only is limited to prepare --checkpoint parsing')
    if not audit_only:
        if any(folders[name] is None for name in selected):
            raise ValueError('select a run or supply the checkpoint review directories')
        protected = [p for p in (parsed, chunked) if p is not None]
        for folder in [folders[name] for name in selected]:
            if any(folder.is_relative_to(p) or p.is_relative_to(folder) for p in protected):
                raise ValueError('review directories must be separate from frozen inputs')
        if checkpoint == 'all' and (folders['parsing'].is_relative_to(folders['chunking']) or
                                    folders['chunking'].is_relative_to(folders['parsing'])):
            raise ValueError('parsing and chunking review directories must be separate')
        if output:
            target = Path(output).resolve()
            if any(target.is_relative_to(p) for p in protected) or any(
                    target == folders[name] / 'review.json' or
                    (checkpoint == 'all' and target == folders[name] / 'report.json') for name in selected):
                raise ValueError('output must not overwrite frozen inputs or review packages')

    reports = {}
    if 'parsing' in selected:
        audit = parsing_audit.evaluate(parsed, parsing_reference, source_root=source_root, on_progress=on_progress)
        if audit_only:
            if output:
                write(output, audit)
            return audit
        outcomes = parsing_audit.select_outcomes(parsed)
        locations = read(required_pages) if required_pages else []

        def evaluate_parsing(path=None):
            return parsing_review.prepare(audit, outcomes, source_root, manifest, review_policy,
                                          sample_per_document, locations, path, parsing_reference)

        fresh = evaluate_parsing()
        reports['parsing'] = _package(phase, folders['parsing'], fresh,
                                      parsing_review.seed(fresh), evaluate_parsing)
    if 'chunking' in selected:
        if on_progress:
            on_progress({'checkpoint': 'chunking', 'status': 'auditing'})
        validate_frozen(chunked)

        def evaluate_chunking(path=None):
            return chunking_audit.evaluate(chunked, parsed, sample_per_document,
                                           chunking_reference, path, manifest, review_policy)

        fresh = evaluate_chunking()
        reports['chunking'] = _package(phase, folders['chunking'], fresh,
                                       fresh['review_template'], evaluate_chunking)

    if checkpoint != 'all':
        result = reports[checkpoint]
    else:
        def combined(key):
            values = [report[key] for report in reports.values()]
            return 'fail' if 'fail' in values else 'pass' if all(v == 'pass' for v in values) else 'pending_review'

        result = {'stage': 'document-structuring-review', 'phase': phase,
                  'automatic_gate': combined('automatic_gate'), 'review_gate': combined('review_gate'),
                  'gate': combined('gate'), 'accepted': False,
                  'checkpoints': {name: {**{k: report[k] for k in ('automatic_gate', 'review_gate', 'gate', 'review')},
                                        'report': str(folders[name] / 'report.json'),
                                        'review_file': str(folders[name] / 'review.json')}
                                  for name, report in reports.items()}}
        if phase == 'finalize' and result['gate'] == 'pass':
            accept_structure(chunked, folders['parsing'] / 'report.json', folders['chunking'] / 'report.json')
            result['accepted'] = True
        elif phase == 'finalize' and (chunked / 'acceptance.json').exists():
            acceptance = read(chunked / 'acceptance.json')
            acceptance.update(status='review_invalidated', review_gate=result['gate'])
            write(chunked / 'acceptance.json', acceptance)
        if output is None and base:
            output = base / 'document_structuring_review_report.json'
    if output:
        write(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='phase', required=True)
    for phase in ('prepare', 'finalize'):
        command = commands.add_parser(phase, help='audit and prepare samples' if phase == 'prepare' else
                                      'reevaluate source judgments; accept a full run only when both gates pass')
        run = command.add_mutually_exclusive_group()
        run.add_argument('--run-id')
        run.add_argument('--run-dir', type=Path)
        command.add_argument('--checkpoint', choices=('all', 'parsing', 'chunking'), default='all',
                             help='internal controller checkpoint; manual sessions normally review all')
        for name in ('parsed-dir', 'chunked-dir', 'parsing-review-dir', 'chunk-review-dir', 'required-pages',
                     'parsing-reference', 'chunking-reference', 'output'):
            command.add_argument('--' + name, type=Path)
        command.add_argument('--source-root', type=Path, default=ROOT)
        command.add_argument('--manifest', type=Path, default=ROOT / 'data/manifest.json')
        command.add_argument('--review-policy', type=Path, default=ROOT / 'config/review-policy.json')
        command.add_argument('--sample-per-document', type=int, default=12)
        if phase == 'prepare':
            command.add_argument('--audit-only', action='store_true',
                                 help='parsing audit for the controller before chunking; no source-review package')
    args = parser.parse_args(argv)
    try:
        result = review_structure(**vars(args), on_progress=lambda event:
                                  print(json.dumps(event), file=sys.stderr, flush=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'document structuring review failed: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.phase == 'prepare' and getattr(args, 'audit_only', False):
        return 0 if result['gate']['status'] == 'pass' else 1
    return 1 if result['gate'] == 'fail' or args.phase == 'finalize' and result['gate'] != 'pass' else 0


if __name__ == '__main__':
    raise SystemExit(main())
