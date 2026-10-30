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

Follow the [review prompt](../src/knowledge_foundry/prompts/agent_prompt_document_structuring_review)
with `RUN_ID`, `MODEL` and `MODEL_REASONING_EFFORT` matching the saved run and
actual session. Codex orchestrates **prepare → inspect → finalize**; script
checks/scoring are deterministic and make no model calls.

```sh
.venv/bin/python scripts/deterministic_document_structuring_review.py prepare --run-id "$RUN_ID" --required-pages tests/fixtures/parsing-expanded-review-pages.json
```

Prepare audits extraction/chunks, preserving judgments in `parsing-review/` and
`chunk-review/` and refreshing stale empty templates. Stale files with findings
require fresh `--parsing-review-dir` / `--chunk-review-dir` paths; reuse those
paths for finalize.

Inspect every `to_review` sample against original sources and rendered layout
under the prompt's completion rule. Save judgments, located notes and reviewer
identity in each `review.json`. Finalize only after every sample has a judgment
or documented inspection blocker; automatic passes cannot replace inspection.

```sh
.venv/bin/python scripts/deterministic_document_structuring_review.py finalize --run-id "$RUN_ID" --required-pages tests/fixtures/parsing-expanded-review-pages.json
```

Finalize reruns checks and scores both reviews. It writes acceptance for the exact
frozen output only when both gates pass; pending/failing results return exit 1,
and invalidate any prior acceptance. Invalid inputs return exit 2. It never
reparses or rechunks. For defects, use the
[repair prompt](../src/knowledge_foundry/prompts/agent_prompt_repair_failed_review_findings)
for diagnosis, corrections, focused regressions and reporting. Rebuilding from
the recommended checkpoint and source/layout review are separate tasks; repair
completion does not grant corpus acceptance.

The combined report is `document-structuring/document_structuring_review_report.json`;
checkpoint folders retain their own `report.json`. The standalone claim evaluator
defaults to `claim_review_report.json` in its classified directory. Both commands
accept `--output` to select another report path.

## 4. Generate semantic clusters, claims and recursive summaries (paid)

Requires accepted structure and `OPENAI_API_KEY`; these commands make paid calls.
Claims/topics and summaries are independent branches:

Claims distinguish primary cluster evidence (`E`) from exact-span support (`S`):
governing context, bounded neighbors and same-page notes from saved chunks.
Support counts toward request size and may qualify claims, but cannot create
them alone or expand primary coverage. Proximity does not establish applicability;
spanless context remains non-citable.

```sh
.venv/bin/python scripts/build_semantic_clusters.py --chunked-dir "$RUN/document-structuring/chunks" --output-dir "$RUN/semantic-clustering"
.venv/bin/python scripts/generate_claims.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/claim-generation"
.venv/bin/python scripts/classify_claims.py --candidates-dir "$RUN/claim-generation" --output-dir "$RUN/claim-classification"
.venv/bin/python scripts/generate_summaries.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/summary-generation"
```

`semantic_clusters.max_claim_request_input_tokens` in `config/semantic-clustering.json`
limits each cluster's complete claim-sizing request to 32,000 input tokens.
Claim and summary generation have no operational input cap; their complete requests
must fit the configured model context window after output reservation and safety
margin.

Clustering, generation, classification and summaries default to four workers per
command; use `--workers N` or `--workers 1` for serial execution. Clustering
parallelizes singleton sizing, independent counts and final checks while keeping
adaptive decisions deterministic and reusing cached embeddings. Classification
requires complete generation; summaries run independently, parallel within each level.

Identical candidate IDs are stored once, retaining every producing cluster link
and its evidence accounting. Different wording or evidence remains separate.

### Resume and retry

Stop the previous process before restarting. Keep inputs, settings, prompts,
selection/reuse flags and taxonomy unchanged; concurrency can change. Saved
successes are reused after validation; unsaved in-flight requests may repeat.

For **interrupted claims or classification**, keep the output folder and add `--resume`:

```sh
.venv/bin/python scripts/generate_claims.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/claim-generation" --resume
.venv/bin/python scripts/classify_claims.py --candidates-dir "$RUN/claim-generation" --output-dir "$RUN/claim-classification" --resume
```

For a **finalized `partial` run**, preserve the old folder and retry the affected
stage into a fresh output path. Classification needs complete generation: point
`--candidates-dir` at recovered claims and `--reuse-candidates-dir` at the
candidates consumed by the old classification.

```sh
.venv/bin/python scripts/generate_claims.py --context-dir "$RUN/semantic-clustering" --retry-failed-from "$RUN/claim-generation" --output-dir "$RUN/claim-generation-retry"
.venv/bin/python scripts/classify_claims.py --candidates-dir "$RUN/claim-generation" --reuse-classification-from "$RUN/claim-classification" --reuse-candidates-dir "$RUN/claim-generation" --output-dir "$RUN/claim-classification-retry"
```

For **interrupted summaries**, rerun with the same cache and an absent output folder;
there is no `--resume` flag. Preserve the cache (default `artifacts/summary-cache`).
To reuse the original output path, first archive or remove only its failed folder.

```sh
.venv/bin/python scripts/generate_summaries.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/summary-generation-retry" --cache-dir artifacts/summary-cache
```

For claim defects, use the repair workflow in section 3, then separately rebuild
and review the affected claims.

After a generation prompt, schema or source-context change, rebuild semantic
clustering from the accepted chunks to recompute request sizes, then regenerate
and reclassify. The same run ID can be reused: first move the superseded
`semantic-clustering`, `claim-generation`, `claim-classification` and `claim-review`
directories together into a timestamped `RUN/history/` directory, recording the
repair reason. Rebuild into the now-empty canonical stage paths; do not resume
old outputs with changed settings. Keep accepted Document Structuring outputs
and the independent summary branch. A taxonomy-only repair can reclassify saved
candidates, followed by a fresh review of that exact output.

## 5. Review generated claims

For newly generated/classified artifacts, initialize the source-review package:

```sh
.venv/bin/python scripts/deterministic_claim_review.py prepare --candidates-dir "$RUN/claim-generation" --classified-dir "$RUN/claim-classification" --initialize-review "$RUN/claim-review/review.json" --output "$RUN/claim-review/report.json"
```

Complete the separate [claim review task](../src/knowledge_foundry/prompts/agent_prompt_claim_review)
against the saved sources, then evaluate its judgments:

```sh
.venv/bin/python scripts/deterministic_claim_review.py finalize --candidates-dir "$RUN/claim-generation" --classified-dir "$RUN/claim-classification" --review-file "$RUN/claim-review/review.json" --output "$RUN/claim-review/report.json"
```

Continue only when both automatic and source-review gates pass. Retain earlier
findings; initialization refuses to overwrite an existing review file.

## 6. Build the knowledge provenance graph

Include generated candidates, semantic clusters and recursive summaries:

```sh
.venv/bin/python scripts/render_knowledge_graph.py --bundle "$RUN/claim-classification/artifact-bundle.json" --claim-generation-dir "$RUN/claim-generation" --summary-dir "$RUN/summary-generation" --output "$RUN/graphs/knowledge-provenance-graph.html"
```

The renderer creates parent directories and replaces existing HTML. Open it in a
browser; the run-level graph covers all artifact stages. Section 8 registers it
for the assistant, which checks the claims manifest against the selected snapshot.

## 7. Rebuild the PostgreSQL search projection

Set `KF_DATABASE_URL` for an existing PostgreSQL database; see
[.envrc.example](../.envrc.example). Migrations create repository tables and the
pgvector extension, which must be installed and permitted for the database role.

Import creates an immutable snapshot or reuses an identical one, retaining older
snapshots and conversation history. `--embed-missing` may make paid calls for
records without compatible vectors; omit it to avoid new embedding calls.

```sh
CORPUS=aml-cft-pilot-13
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

Register the run once (or after changing paths) to enable its provenance graph.
This preserves other configured corpora/runs and does not import or publish data:

```sh
.venv/bin/python - "$RUN_ID" "$RUN" "$CORPUS" <<'PYTHON'
import json, sys
from pathlib import Path
run_id, run = sys.argv[1], Path(sys.argv[2])
path = Path('config/retrieval-corpora.json')
catalog = json.loads(path.read_text())
catalog[sys.argv[3]]['runs'][run_id] = {
    'artifact_path': str(run / 'document-structuring/chunks/artifact-bundle.json'),
    'preparation_dir': str(run / 'semantic-clustering'),
    'claims_dir': str(run / 'claim-classification'),
    'summaries_dir': str(run / 'summary-generation'),
    'graph_path': str(run / 'graphs/knowledge-provenance-graph.html'),
}
path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
PYTHON
.venv/bin/python -m streamlit run apps/chat/app.py
```

Open `http://localhost:8501` and select the corpus/published snapshot.
**Open provenance graph** shows lineage. The app inherits `KF_DATABASE_URL` and
`KF_RETRIEVAL_CONFIG`, defaulting to `config/retrieval-corpora.json` for the catalog.
`OPENAI_API_KEY` enables paid answers and vector/combined search; without it,
the app uses keyword search and exact excerpts.

## Back up results and PostgreSQL

Keep the selected run directory, `artifacts/ingested/`, and the repository version
with its configuration and `data/manifest.json`. Optional embedding and summary
caches avoid repeated work and paid calls during regeneration.

To preserve all database corpora, snapshots, vectors, chat history and feedback,
use PostgreSQL client tools compatible with the server:

```sh
source .envrc
mkdir -p artifacts/db
DUMP="artifacts/db/knowledge-foundry-$(TZ=Europe/Paris date +%Y%m%d-%H%M%S).dump"
pg_dump --dbname="$KF_DATABASE_URL" --format=custom --file="$DUMP"
pg_restore --list "$DUMP" > /dev/null
```

Upload the dump alongside the artifact backup. Listing checks archive readability,
not a full restore. Restore the original directory layout; saved metadata may
contain absolute paths. Recreate credentials separately; do not include `.envrc`.

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
