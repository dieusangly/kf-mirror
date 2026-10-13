"""Compile a saved chunk bundle into draft topic knowledge artifacts."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.compilation import compile_bundle  # noqa: E402


def _print_progress(event):
    phase = event["phase"]
    if event["state"] == "started":
        if phase == "generation":
            prefix = f"Sample: {event['chunks']}/{event['source_chunks']} chunks; " if event["sample"] else "Input: "
            print(f"[compile] {prefix}generating claims in {event['total']}/{event['source_total']} batches",
                  file=sys.stderr, flush=True)
        elif phase == "classification":
            print(f"[compile] Classifying {event['total']} candidates", file=sys.stderr, flush=True)
        elif phase == "export":
            print("[compile] Writing validated artifacts", file=sys.stderr, flush=True)
    elif event["state"] == "progress":
        print(f"[compile] {phase.capitalize()}: {event['completed']}/{event['total']}",
              file=sys.stderr, flush=True)
    elif event["state"] == "retry":
        print(f"[compile] Retrying {phase} {event['completed']}/{event['total']} "
              f"(attempt {event['attempt']}/{event['attempts']})", file=sys.stderr, flush=True)
    elif phase == "generation" and event["state"] == "complete":
        print(f"[compile] Generated {event['candidates']} candidates", file=sys.stderr, flush=True)


def _print_summary(report, telemetry, elapsed):
    label = "OpenAI estimated cost" if telemetry["provider"] == "openai" else "OpenAI cost"
    print(f"[compile] {report['status'].capitalize()} in {elapsed:.1f} s — {label}: "
          f"${telemetry['estimated_cost_usd']:.6f} ({telemetry['requests']} API requests)",
          file=sys.stderr, flush=True)
    projection = report.get("projected_full_build")
    if projection:
        scope = report["compilation_scope"]
        print(f"[compile] Projected full build from {len(scope['selected_batch_numbers'])}/"
              f"{scope['total_batches']} sampled batches: ${projection['estimated_cost_usd']:.6f} (estimate)",
              file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunked-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "compilation.json")
    parser.add_argument("--taxonomy", type=Path, default=ROOT / "config" / "topics-v1.json")
    parser.add_argument("--fixture", type=Path, help="deterministic fixture required by provider fixture")
    parser.add_argument("--review-file", type=Path)
    parser.add_argument("--sample-batches", type=int,
                        help="compile this many length-stratified claim-generation batches")
    args = parser.parse_args(argv)
    telemetry = None

    def progress(event):
        nonlocal telemetry
        _print_progress(event)
        if event["phase"] == "complete":
            telemetry = event["telemetry"]

    started = time.monotonic()
    try:
        report = compile_bundle(args.chunked_dir, args.output_dir, args.config, args.taxonomy,
                                args.fixture, args.review_file, on_progress=progress,
                                sample_batches=args.sample_batches)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"compilation failed: {exc}\n")
    _print_summary(report, telemetry, time.monotonic() - started)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
