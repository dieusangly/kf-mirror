"""Classify saved claim candidates into curated topic records."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.claim_compilation import classify_claims  # noqa: E402


def _print_progress(event):
    if event["phase"] == "classification" and event["state"] == "started":
        print(f"[classify] Classifying {event['total']} candidates", file=sys.stderr, flush=True)
    elif event["state"] == "progress":
        print(f"[classify] {event['phase'].capitalize()}: {event['completed']}/{event['total']}",
              file=sys.stderr, flush=True)
    elif event["state"] == "retry":
        print(f"[classify] Retrying classification {event['completed']}/{event['total']} "
              f"(attempt {event['attempt']}/{event['attempts']})", file=sys.stderr, flush=True)
    elif event["phase"] == "export" and event["state"] == "started":
        print("[classify] Writing validated topic artifacts", file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "compilation.json")
    parser.add_argument("--taxonomy", type=Path, default=ROOT / "config" / "topics-v1.json")
    parser.add_argument("--fixture", type=Path, help="deterministic fixture required by provider fixture")
    parser.add_argument("--review-file", type=Path)
    args = parser.parse_args(argv)
    telemetry = None

    def progress(event):
        nonlocal telemetry
        _print_progress(event)
        if event["phase"] == "complete":
            telemetry = event["telemetry"]

    started = time.monotonic()
    try:
        report = classify_claims(args.candidates_dir, args.output_dir, args.config, args.taxonomy, args.fixture,
                                 args.review_file, on_progress=progress)
    except Exception as exc:
        parser.exit(2, f"claim classification failed: {exc}\n")
    print(f"[classify] {report['status'].capitalize()} in {time.monotonic() - started:.1f} s — "
          f"OpenAI estimated cost: ${telemetry['estimated_cost_usd']:.6f} "
          f"({telemetry['requests']} API requests)", file=sys.stderr, flush=True)
    projection = report.get("projected_full_build")
    if projection:
        print(f"[classify] Projected full build: ${projection['estimated_cost_usd']:.6f} (estimate)",
              file=sys.stderr, flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
