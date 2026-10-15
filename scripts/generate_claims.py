"""Generate durable evidence-linked claim candidates from a saved chunk bundle."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.claim_compilation import generate_claims  # noqa: E402


def _print_progress(event):
    if event["phase"] == "generation" and event["state"] == "started":
        scope = "Subset" if event["subset"] else "Full"
        prefix = f"{scope}: {event['chunks']}/{event['source_chunks']} chunks; "
        print(f"[generate] {prefix}generating candidates in {event['total']}/{event['source_total']} batches",
              file=sys.stderr, flush=True)
    elif event["state"] == "progress":
        print(f"[generate] {event['phase'].capitalize()}: {event['completed']}/{event['total']}",
              file=sys.stderr, flush=True)
    elif event["state"] == "retry":
        print(f"[generate] Retrying {event['phase']} {event['completed']}/{event['total']} "
              f"(attempt {event['attempt']}/{event['attempts']})", file=sys.stderr, flush=True)
    elif event["phase"] == "export" and event["state"] == "started":
        print("[generate] Writing validated candidate artifacts", file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunked-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "compilation.json")
    parser.add_argument("--fixture", type=Path, help="deterministic fixture required by provider fixture")
    subset = parser.add_mutually_exclusive_group()
    subset.add_argument("--subset-batches", type=int,
                        help="generate this many length-stratified claim-generation batches")
    subset.add_argument("--batch-numbers", type=int, nargs="+",
                        help="generate these one-based original batch numbers for a targeted review")
    parser.add_argument("--resume", action="store_true",
                        help="resume an incomplete matching generation run in --output-dir")
    args = parser.parse_args(argv)
    telemetry = None

    def progress(event):
        nonlocal telemetry
        _print_progress(event)
        if event["phase"] == "complete":
            telemetry = event["telemetry"]

    started = time.monotonic()
    try:
        report = generate_claims(args.chunked_dir, args.output_dir, args.config, args.fixture,
                                 on_progress=progress, subset_batches=args.subset_batches, resume=args.resume,
                                 batch_numbers=args.batch_numbers)
    except Exception as exc:
        parser.exit(2, f"claim generation failed; completed batches remain resumable with --resume: {exc}\n")
    print(f"[generate] {report['status'].capitalize()} in {time.monotonic() - started:.1f} s — "
          f"OpenAI estimated cost: ${telemetry['estimated_cost_usd']:.6f} "
          f"({telemetry['requests']} API requests)", file=sys.stderr, flush=True)
    coverage = report.get("coverage")
    if coverage:
        counts = coverage["summary"]
        label = "Coverage needs review" if coverage["status"] == "needs_review" else "No coverage gaps detected"
        print(f"[generate] {label}: {counts['cited_chunks']}/{counts['selected_chunks']} selected chunks cited; "
              f"{counts['sources_with_candidates']}/{counts['selected_sources']} selected sources have candidates; "
              f"{counts['failed_chunks']} failed chunks; "
              f"{counts['chunks_without_eligible_evidence']} chunks without eligible evidence. "
              "Citations do not establish complete extraction or verified grounding.", file=sys.stderr, flush=True)
        for source_id in coverage["selected_source_ids_without_candidates"]:
            print(f"[generate] Selected source without candidates: {source_id}", file=sys.stderr, flush=True)
    projection = report.get("projected_full_generation")
    if projection:
        scope = report["compilation_scope"]
        print(f"[generate] Projected full generation from {len(scope['selected_batch_numbers'])}/"
              f"{scope['total_batches']} subset batches: ${projection['estimated_cost_usd']:.6f} (estimate)",
              file=sys.stderr, flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
