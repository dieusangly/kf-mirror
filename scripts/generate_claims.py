"""Generate durable evidence-linked candidate claims from a saved chunk bundle."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.claim_generation_and_classification import generate_claims  # noqa: E402


def _print_progress(event):
    if event['phase'] == 'preparation':
        total = event.get('total')
        count = f" {event['completed']}/{total}" if total is not None else ''
        print(f"[generate] Preparation: {event['step']}{count}", file=sys.stderr, flush=True)
        return
    if event["phase"] == "generation" and event["state"] == "started":
        scope = "Subset" if event["subset"] else "Full"
        prefix = f"{scope}: {event['chunks']}/{event['source_chunks']} chunks; "
        print(f"[generate] {prefix}generating candidate claims in {event['total']}/{event['source_total']} semantic clusters",
              file=sys.stderr, flush=True)
    elif event["state"] == "progress":
        print(f"[generate] {event['phase'].capitalize()}: {event['completed']}/{event['total']}",
              file=sys.stderr, flush=True)
    elif event["state"] == "retry":
        print(f"[generate] Retrying {event['phase']} {event['completed']}/{event['total']} "
              f"(attempt {event['attempt']}/{event['attempts']})", file=sys.stderr, flush=True)
    elif event["phase"] == "export" and event["state"] == "started":
        print("[generate] Writing validated candidate claim artifacts", file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunked-dir", type=Path, help="optional original input hash check")
    parser.add_argument("--context-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "claim-generation-and-classification.json")
    parser.add_argument("--fixture", type=Path, help="deterministic fixture required by provider fixture")
    subset = parser.add_mutually_exclusive_group()
    subset.add_argument("--subset-clusters", "--subset-batches", dest="subset_clusters", type=int,
                        help="generate this many length-stratified claim-generation semantic clusters")
    subset.add_argument("--cluster-numbers", "--batch-numbers", dest="cluster_numbers", type=int, nargs="+",
                        help="generate these one-based semantic clusters from the current context grouping")
    parser.add_argument("--resume", action="store_true",
                        help="resume an incomplete matching generation run in --output-dir")
    parser.add_argument("--retry-failed-from", type=Path,
                        help="recover a finalized partial run into a fresh output directory; reuse successful clusters")
    parser.add_argument('--workers', type=int, default=4,
                        help='maximum simultaneous model requests (default: 4; use 1 for serial execution)')
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error('--workers must be a positive integer')
    telemetry = None

    def progress(event):
        nonlocal telemetry
        _print_progress(event)
        if event["phase"] == "complete":
            telemetry = event["telemetry"]

    started = time.monotonic()
    try:
        report = generate_claims(args.chunked_dir, args.output_dir, args.config, args.fixture,
                                 on_progress=progress, subset_clusters=args.subset_clusters, resume=args.resume,
                                 cluster_numbers=args.cluster_numbers, context_dir=args.context_dir,
                                 retry_failed_from=args.retry_failed_from, workers=args.workers)
    except Exception as exc:
        parser.exit(2, f"claim generation failed; completed semantic clusters remain resumable with --resume: {exc}\n")
    cost = telemetry['estimated_cost_usd']
    cost_text = f"${cost:.6f}" if cost is not None else 'unknown'
    print(f"[generate] {report['status'].capitalize()} in {time.monotonic() - started:.1f} s — "
          f"OpenAI estimated cost: {cost_text} "
          f"({telemetry['requests']} API requests)", file=sys.stderr, flush=True)
    coverage = report.get("coverage")
    if coverage:
        counts = coverage["summary"]
        label = "Coverage needs review" if coverage["status"] == "needs_review" else "No coverage gaps detected"
        print(f"[generate] {label}: {counts['cited_chunks']}/{counts['selected_chunks']} selected chunks cited; "
              f"{counts['sources_with_candidates']}/{counts['selected_sources']} selected sources have candidate claims; "
              f"{counts['failed_chunks']} failed chunks; "
              f"{counts['chunks_without_eligible_evidence']} chunks without eligible evidence. "
              "Citations do not establish complete extraction or verified grounding.", file=sys.stderr, flush=True)
        for source_id in coverage["selected_source_ids_without_candidates"]:
            print(f"[generate] Selected source without candidate claims: {source_id}", file=sys.stderr, flush=True)
    projection = report.get("projected_full_generation")
    if projection:
        scope = report["compilation_scope"]
        cost = projection['estimated_cost_usd']
        cost_text = f"${cost:.6f}" if cost is not None else 'unknown'
        print(f"[generate] Projected full generation from {len(scope['selected_cluster_numbers'])}/"
              f"{scope['total_semantic_clusters']} subset semantic clusters: {cost_text} (estimate)",
              file=sys.stderr, flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'complete' else 1


if __name__ == "__main__":
    raise SystemExit(main())
