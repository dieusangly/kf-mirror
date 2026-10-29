# AML/CFT pipeline commands

Run from the repository root. The pilot selects 13 originals in
`data/manifest.json` (11 PDFs / 801 pages, two HTML articles).
Results belong in [benchmarks](benchmarks.md); adapter details in [providers](providers.md).

## 1. Install and select inputs

Requires Tesseract with French/English data; PDF engines download model weights
on first use. Keep caches outside dated runs.

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Ingest only for new source bytes/provenance; omit this for the retained snapshot:

```sh
.venv/bin/python scripts/ingest.py --config config/pipeline.json --output-dir artifacts/ingested
```

Use a fresh run ID per provider/configuration execution (Europe/Paris date).
Pin the snapshot rather than the changing `artifacts/ingested/report.json`:

```sh
PROFILE=docling  # or unstructured (hi_res)
RUN_ID=20261028-${PROFILE}-aml-cft-01
RUN=artifacts/$RUN_ID
SNAPSHOT=artifacts/ingested/snapshots/6d50d268e3fbdbaa4ecc5f1845d0b1e1352635fb02afda76b7257202b5bba965.json
mkdir -p "$RUN"
# Reference is relative to RUN; retain this descriptor for pilot validation.
.venv/bin/python - "$RUN" "$SNAPSHOT" <<'PYTHON'
import json, os, sys
from pathlib import Path
run, snapshot = map(Path, sys.argv[1:])
(run / 'run.json').write_text(json.dumps({'ingestion_snapshot': os.path.relpath(snapshot, run)}, indent=2) + '\n')
PYTHON
```

## 2. Parse and chunk locally

```sh
HF_HOME="$PWD/artifacts/cache/$PROFILE/huggingface" PDF_RENDER_DPI=350 \
  .venv/bin/python scripts/parse.py --config config/pipeline.json --profile "$PROFILE" --ingested-dir "$SNAPSHOT" --run-id "$RUN_ID" --output-dir "$RUN/document-structuring/parsing"
.venv/bin/python scripts/chunk.py --config config/pipeline.json --input-dir "$RUN/document-structuring/parsing" --output-dir "$RUN/document-structuring/chunks"
```

For compatible Docling adapter repairs, add `--native-input-dir <prior-parsing>`
to reuse saved native output. OCR/DPI changes require fresh parsing. Rechunking
needs only `chunk.py` and saved parsing; preserve prior outputs.

## 3. Review and accept

Use the [review prompt](../src/knowledge_foundry/prompts/agent_prompt_document_structuring_review)
in a Codex session with `RUN_ID`, `MODEL` and `MODEL_REASONING_EFFORT` set to the
saved run and actual session settings. Codex orchestrates **prepare → inspect →
finalize**; the script performs deterministic checks and scoring without model calls.

```sh
.venv/bin/python scripts/deterministic_document_structuring_review.py prepare --run-id "$RUN_ID" --required-pages tests/fixtures/parsing-expanded-review-pages.json
```

Prepare runs extraction/chunk audits and reuses `parsing-review/` and
`chunk-review/`. It preserves current judgments and refreshes stale empty pending
templates. Stale files containing actual findings require a fresh review directory;
use `--parsing-review-dir` / `--chunk-review-dir` and retain those paths for finalize.

Follow the review prompt's completion rule for every `to_review`
sample in both packages, including original sources and rendered layout. Save
judgments, source-located notes and actual reviewer identity in each `review.json`.
Run finalize only after every sample has a completed judgment or a documented
inspection blocker. Automatic passes never substitute for source inspection.

```sh
.venv/bin/python scripts/deterministic_document_structuring_review.py finalize --run-id "$RUN_ID" --required-pages tests/fixtures/parsing-expanded-review-pages.json
```

Finalize reruns checks and scores both reviews. It writes acceptance for the exact
frozen output only when both gates pass; pending/failing results return exit 1,
and invalidate any prior acceptance. Invalid inputs return exit 2. It never
reparses or rechunks. For defects, use the
[repair prompt](../src/knowledge_foundry/prompts/agent_prompt_document_structuring_repair)
for diagnosis, implementation corrections, focused regression tests and a results
report. Rebuilding from the recommended checkpoint is a separate task, followed
by a separate source/layout review task. Corpus acceptance is not part of repair
completion.

The combined report is `document-structuring/document_structuring_review_report.json`;
checkpoint folders retain their own `report.json`. The standalone claim evaluator
defaults to `claim_review_report.json` in its classified directory. Both commands
accept `--output` to select another report path.

## 4. Generate semantic clusters, claims and recursive summaries (paid)

Requires accepted structure and `OPENAI_API_KEY`; these commands make paid calls.
Claims/topics and summaries are independent branches:

```sh
.venv/bin/python scripts/build_semantic_clusters.py --chunked-dir "$RUN/document-structuring/chunks" --output-dir "$RUN/semantic-clustering"
.venv/bin/python scripts/generate_claims.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/claim-generation"
.venv/bin/python scripts/classify_claims.py --candidates-dir "$RUN/claim-generation" --output-dir "$RUN/claim-classification"
.venv/bin/python scripts/generate_summaries.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/summary-generation"
```

Generation, classification and summaries each default to four concurrent workers.
Pass `--workers N` to set the limit, or `--workers 1` for serial execution.
Classification requires completed generation; summaries run independently, with
concurrency within each level. Limits apply per command when running stages together.

An already running process keeps its current execution settings. To use concurrency
for interrupted claim generation, stop the old process before restarting the same
command with `--resume --workers 4`. Completed checkpoints are reused; in-flight
requests may need repeating. Classification also supports `--resume`; summaries
reuse their response cache when restarted into a fresh output directory.

For claim defects, the
[claim repair prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_repair)
ends after diagnosis, corrections, focused regressions and reporting. Rebuilding
from its recommended checkpoint and reviewing the rebuilt claims are separate
tasks; passing corpus review is not part of repair completion.

## 5. Review generated claims

For newly generated/classified artifacts, initialize the source-review package:

```sh
.venv/bin/python scripts/auto_review_claims.py --candidates-dir "$RUN/claim-generation" --classified-dir "$RUN/claim-classification" --initialize-review "$RUN/claim-review/review.json" --output "$RUN/claim-review/report.json"
```

Complete the separate [claim review task](../src/knowledge_foundry/prompts/agent_prompt_claim_review)
against the saved sources, then evaluate its judgments:

```sh
.venv/bin/python scripts/auto_review_claims.py --candidates-dir "$RUN/claim-generation" --classified-dir "$RUN/claim-classification" --review-file "$RUN/claim-review/review.json" --output "$RUN/claim-review/report.json"
```

Continue only when both automatic and source-review gates pass. Retain earlier
findings; initialization refuses to overwrite an existing review file.

## 6. Build the knowledge provenance graph

Include generated candidates, semantic clusters and recursive summaries:

```sh
.venv/bin/python scripts/render_knowledge_graph.py --bundle "$RUN/claim-classification/artifact-bundle.json" --claim-generation-dir "$RUN/claim-generation" --summary-dir "$RUN/summary-generation" --output "$RUN/claim-classification/graph.html"
```

Open the standalone HTML file in a browser. Add `--force` to replace an existing
viewer. Keeping this standalone graph under `claim-classification` also lets the
assistant resolve it to the matching classified artifact.

## 7. Rebuild the PostgreSQL search projection

Set `KF_DATABASE_URL` for an existing PostgreSQL database with pgvector available;
see [.envrc.example](../.envrc.example). Migrations create the repository tables
and vector extension. The database role needs the corresponding permissions.

Reimporting saved artifacts creates an immutable search snapshot, reusing an
identical existing snapshot when possible. Previous snapshots and conversation
history are retained. `--embed-missing` may make paid embedding calls for records
without compatible saved vectors; omit it for import without new embedding calls.

```sh
CORPUS=aml-cft-expanded-pilot
export KF_RETRIEVAL_CONFIG=config/retrieval-multilingual.json
.venv/bin/python scripts/index_artifacts.py migrate
mkdir -p "$RUN/indexing"
.venv/bin/python scripts/index_artifacts.py import --corpus "$CORPUS" --config "$KF_RETRIEVAL_CONFIG" --preparation-dir "$RUN/semantic-clustering" --claims-dir "$RUN/claim-classification" --summaries-dir "$RUN/summary-generation" --embed-missing > "$RUN/indexing/import.json"
INDEX_SNAPSHOT_ID=$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["snapshot_id"])' "$RUN/indexing/import.json")
```

Check retrieval against the ready, unpublished snapshot and inspect the report
before publication. Vector/combined search can make paid query-embedding calls:

```sh
.venv/bin/python scripts/evaluate_retrieval.py --corpus "$CORPUS" --config "$KF_RETRIEVAL_CONFIG" --snapshot-id "$INDEX_SNAPSHOT_ID" --unpublished --cases tests/fixtures/retrieval-expanded-development.json --methods keyword vector combined --modes passage claim summary --no-rerank --check-excerpts --output "$RUN/indexing/retrieval-report.json"
```

Publication explicitly selects the snapshot available to the assistant; it does
not approve every claim or summary:

```sh
.venv/bin/python scripts/index_artifacts.py publish --corpus "$CORPUS" --snapshot-id "$INDEX_SNAPSHOT_ID"
.venv/bin/python scripts/index_artifacts.py list --corpus "$CORPUS"
```

## 8. Run the document assistant

Register the selected run in the corpus configuration so the assistant can show
its matching provenance graph. This preserves other configured corpora/runs:

```sh
.venv/bin/python - "$RUN_ID" "$RUN" <<'PYTHON'
import json, sys
from pathlib import Path
run_id, run = sys.argv[1], Path(sys.argv[2])
path = Path('config/retrieval-corpora.json')
catalog = json.loads(path.read_text())
catalog['aml-cft-expanded-pilot']['runs'][run_id] = {
    'artifact_path': str(run / 'document-structuring/chunks/artifact-bundle.json'),
    'preparation_dir': str(run / 'semantic-clustering'),
    'claims_dir': str(run / 'claim-classification'),
    'summaries_dir': str(run / 'summary-generation'),
    'graph_path': str(run / 'claim-classification/graph.html'),
}
path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
PYTHON
.venv/bin/python -m streamlit run apps/chat/app.py
```

Open `http://localhost:8501`, select the corpus and published snapshot, and use
**Open provenance graph** to inspect lineage. The app inherits `KF_DATABASE_URL`
and `KF_RETRIEVAL_CONFIG`; it uses `config/retrieval-corpora.json` by default.
`OPENAI_API_KEY` enables generated answers and vector/combined search, which may
incur charges. Without it, the app uses keyword search and exact source excerpts.

## Validate or compare saved runs

Set `RUN` to a retained directory to validate or compare saved output without parsing.
Use `prepare` above for automatic checks and source-review preparation; it does not
grant acceptance:

```sh
.venv/bin/python scripts/validate_pilot.py --run-dir "$RUN" --output "$RUN/validation.json"
.venv/bin/python scripts/compare_structuring.py --baseline "$RUN" --candidate artifacts/20261027-unstructured-aml-cft-01 --output "$RUN/comparison-unstructured.json"
```

## Controller and tests

For managed runs, use `scripts/workflow.py step --run-id <id> --stage <checkpoint>`
or `scripts/workflow.py run` with explicit budgets; see `--help`. Controller rebuilds
from `parse` preserve ingestion; from `chunk` also preserve parsing. Publication is
separate. The relocated Docling run has no controller `state.json`: use individual
commands above or start a fresh managed run.

```sh
PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q
.venv/bin/python scripts/validate_artifact_examples.py
```

Optional tests: `KF_TEST_DATABASE_URL` enables PostgreSQL/pgvector;
`KF_TEST_OCR=1` enables raster OCR.
