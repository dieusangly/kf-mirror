"""Compile a saved Task 5 chunk bundle into draft topic knowledge artifacts."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.compilation import compile_bundle  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunked-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "compilation.json")
    parser.add_argument("--taxonomy", type=Path, default=ROOT / "config" / "topics-v1.json")
    parser.add_argument("--fixture", type=Path, help="deterministic fixture required by provider fixture")
    parser.add_argument("--review-file", type=Path)
    args = parser.parse_args(argv)
    try:
        report = compile_bundle(args.chunked_dir, args.output_dir, args.config, args.taxonomy,
                                args.fixture, args.review_file)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"compilation failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
