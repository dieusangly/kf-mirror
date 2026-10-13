#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
PARSED_OUTPUT="artifacts/parsed-${RUN_ID}"
CHUNKED_OUTPUT="artifacts/chunked-${RUN_ID}"

if [[ -e "$PARSED_OUTPUT" || -e "$CHUNKED_OUTPUT" ]]; then
  printf 'Refusing to overwrite existing run output for %s.\n' "$RUN_ID" >&2
  exit 1
fi

PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -q

PYTHONPATH=src:. .venv/bin/python scripts/parse.py \
  --config config/pipeline.json \
  --output-dir "$PARSED_OUTPUT"

PYTHONPATH=src:. .venv/bin/python scripts/chunk.py \
  --config config/pipeline.json \
  --input-dir "$PARSED_OUTPUT" \
  --output-dir "$CHUNKED_OUTPUT"

PYTHONPATH=src:. .venv/bin/python scripts/validate_artifact_examples.py \
  "$CHUNKED_OUTPUT/artifact-bundle.json" \
  "$PARSED_OUTPUT"/documents/*/artifact-bundle.json

PYTHONPATH=src:. .venv/bin/python scripts/evaluate_parsing.py \
  --input-dir "$PARSED_OUTPUT" \
  --output "$PARSED_OUTPUT/parsing-evaluation-report.json" || PARSING_EVALUATION_STATUS=$?

PYTHONPATH=src:. .venv/bin/python scripts/evaluate_chunking.py \
  --chunked-dir "$CHUNKED_OUTPUT" \
  --parsed-dir "$PARSED_OUTPUT" \
  --output "$CHUNKED_OUTPUT/chunking-evaluation-report.json" || CHUNKING_EVALUATION_STATUS=$?

PARSING_EVALUATION_STATUS="${PARSING_EVALUATION_STATUS:-0}"
CHUNKING_EVALUATION_STATUS="${CHUNKING_EVALUATION_STATUS:-0}"

printf 'Run ID: %s\nParsed output: %s\nChunked output: %s\nParsing evaluation exit: %s\nChunking evaluation exit: %s\n' \
  "$RUN_ID" "$PARSED_OUTPUT" "$CHUNKED_OUTPUT" \
  "$PARSING_EVALUATION_STATUS" "$CHUNKING_EVALUATION_STATUS"

if (( PARSING_EVALUATION_STATUS != 0 || CHUNKING_EVALUATION_STATUS != 0 )); then
  printf 'One or more evaluation gates failed; reports were still written.\n' >&2
  exit 1
fi
