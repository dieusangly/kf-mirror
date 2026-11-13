# AML/CFT pipeline commands

Run from the repository root. The pilot selects 13 originals in
`data/manifest.json` (11 PDFs / 801 pages, two HTML articles).
For a published corpus, go to [assistant startup](#8-run-the-document-assistant).
To index saved outputs, set `RUN` to their directory and start at
[PostgreSQL import](#7-rebuild-the-postgresql-search-projection), after the review
gates below pass. Rebuild only the stages needed for changed inputs or settings.
See [benchmarks](benchmarks.md) for results and [providers](providers.md) for adapters.

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

Use a fresh run ID per provider/configuration execution (Europe/Paris date);
the example below names the retained run. Pin the ingestion snapshot:

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
actual session. The workflow is **prepare → inspect → finalize**; scripts make
no model calls.

```sh
.venv/bin/python scripts/deterministic_document_structuring_review.py prepare --run-id "$RUN_ID" --required-pages tests/fixtures/parsing-expanded-review-pages.json
```

Prepare audits extraction/chunks and preserves judgments in `parsing-review/`
and `chunk-review/`. Stale empty templates are refreshed; stale files with
findings require fresh `--parsing-review-dir` / `--chunk-review-dir` paths,
also used for finalize.

Inspect every `to_review` sample against original sources and rendered layout
under the prompt's completion rule. Save judgments, located notes and reviewer
identity in each `review.json`. Finalize only after every sample has a judgment
or documented inspection blocker; automatic passes cannot replace inspection.

```sh
.venv/bin/python scripts/deterministic_document_structuring_review.py finalize --run-id "$RUN_ID" --required-pages tests/fixtures/parsing-expanded-review-pages.json
```

Finalize reruns checks and accepts the exact frozen output only when both gates pass.
Pending/failing results return exit 1 and invalidate prior acceptance; invalid
inputs return exit 2. It never reparses or rechunks. For defects, use the
[repair prompt](../src/knowledge_foundry/prompts/agent_prompt_repair_failed_review_findings)
and then rebuild and review the affected output. Repair completion alone does
not grant acceptance.

The combined report is `document-structuring/document_structuring_review_report.json`;
checkpoint folders retain their own `report.json`. Use `--output` for another path.

## 4. Generate semantic clusters, claims and recursive summaries (paid)

Requires accepted structure and `OPENAI_API_KEY`. Claims/topics and summaries
are independent branches; these commands make paid calls.

Claims use primary cluster evidence (`E`). Exact-span support (`S`) adds governing
context, bounded neighbors and same-page notes. It counts toward request size and
may qualify claims, but cannot create them alone or expand primary coverage.
Proximity does not establish applicability; spanless context is non-citable.

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

Each command defaults to four workers; use `--workers N` (`1` for serial execution).
Clustering reuses cached embeddings and keeps adaptive decisions deterministic.
Classification requires complete generation; summaries parallelize within each level.

Identical candidate IDs retain all producing cluster links and evidence accounting;
different wording or evidence remains separate.

### Resume and retry

Stop the previous process first. Resume requires unchanged inputs, settings,
prompts, selection/reuse flags and taxonomy; worker count may change. Validated
successes are reused; unsaved in-flight requests may repeat.

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

For **interrupted summaries**, use the same cache and a fresh output folder;
there is no `--resume` flag. The default cache is `artifacts/summary-cache`.
Archive the failed folder before reusing its path.

```sh
.venv/bin/python scripts/generate_summaries.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/summary-generation-retry" --cache-dir artifacts/summary-cache
```

For claim defects, follow the repair prompt in section 3. A generation prompt,
schema or source-context change requires rebuilding semantic clustering from
accepted chunks, then regenerating and reclassifying. To reuse the run ID,
archive `semantic-clustering`, `claim-generation`, `claim-classification` and
`claim-review` together under timestamped `RUN/history/`, recording the reason.
Rebuild into empty stage paths; preserve accepted structure and the independent
summary branch. A taxonomy-only change can reclassify saved candidates. Review
the resulting output in either case.

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
Without `--output`, the report defaults to `claim_review_report.json` in the
classified directory.

## 6. Build the knowledge provenance graph

Include generated candidates, semantic clusters and recursive summaries:

```sh
.venv/bin/python scripts/render_knowledge_graph.py --bundle "$RUN/claim-classification/artifact-bundle.json" --claim-generation-dir "$RUN/claim-generation" --summary-dir "$RUN/summary-generation" --output "$RUN/graphs/knowledge-provenance-graph.html"
```

The renderer creates parent directories and replaces existing HTML. Section 8
registers the graph for the assistant, which checks its claims manifest against
the selected snapshot.

## 7. Rebuild the PostgreSQL search projection

Configure `KF_DATABASE_URL` in `.envrc` using
[.envrc.example](../.envrc.example), then load it in the launching shell.
PostgreSQL must already exist with pgvector available and permitted for the role;
migrations create the application tables and extension.

Import validates saved artifacts and creates or reuses an immutable snapshot,
preserving older snapshots and conversation history. Validation can take several
minutes. `--embed-missing` reuses cached vectors and may make paid calls for misses.
For cached embeddings only, prefix the import command with `OPENAI_API_KEY=`;
omitting `--embed-missing` keeps only vectors already exported in the artifacts.
Preserve earlier import and evaluation reports when choosing output paths.

```sh
source .envrc
CORPUS=aml-cft-pilot-13
export KF_RETRIEVAL_CONFIG=config/retrieval-multilingual.json
.venv/bin/python scripts/index_artifacts.py migrate
mkdir -p "$RUN/indexing"
.venv/bin/python scripts/index_artifacts.py import --corpus "$CORPUS" --config "$KF_RETRIEVAL_CONFIG" --preparation-dir "$RUN/semantic-clustering" --claims-dir "$RUN/claim-classification" --summaries-dir "$RUN/summary-generation" --embed-missing > "$RUN/indexing/import.json"
INDEX_SNAPSHOT_ID=$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["snapshot_id"])' "$RUN/indexing/import.json")
```

Evaluate the 123 development questions against the ready, unpublished snapshot
before publication. This compares all methods/modes with reranking enabled;
vector/combined search can make paid query-embedding calls:

```sh
.venv/bin/python scripts/evaluate_retrieval.py \
  --corpus "$CORPUS" --config "$KF_RETRIEVAL_CONFIG" \
  --snapshot-id "$INDEX_SNAPSHOT_ID" --unpublished \
  --cases tests/fixtures/retrieval-evaluation-123q.json \
  --methods keyword vector combined \
  --modes passage claim summary passage-and-claim \
  --workers 4 --check-excerpts --trace \
  --output "$RUN/retrieval-evaluation/123q-20261103/retrieval-report-rerank-four-modes.json"
```

The retained [evaluation](retrieval-evaluation-123q-20261103.md) links its report,
checkpoint and synthetic, unreviewed `V2-…` questions. Reports include metrics,
per-question results, usage, costs and warnings. `--trace` adds evidence and context
diagnostics; `--check-excerpts` audits retained quotations and citations.

Evaluation defaults to one worker; `--sequence` requires one. Checkpoints default
to the report path with a `.checkpoint` suffix; override with `--checkpoint DIR`.
Use a fresh path or repeat with `--resume` to reuse successes and retry errors.
Snapshot, cases, config and evaluation options must match; worker count may change.
Unsaved in-flight calls may repeat. Progress goes to stderr; `--quiet` suppresses it.

For fused retrieval, use `--mode passage-and-claim` for queries or
`--modes passage-and-claim` for evaluation. Vector/combined search needs vectors for both
passages and claims. LLM reranking requires `search.rerank: true` and
`answer.provider: "openai"`; keyword and `--no-rerank` comparisons remain available.
See [retrieval.json](../config/retrieval.json) for settings,
the [retrieval pipeline](../src/knowledge_foundry/retrieval/pipeline.py) for candidate
merging and budgets, and the [evaluator](../src/knowledge_foundry/retrieval/evaluation.py)
for metric definitions.

Inspect completed/error counts before comparing metrics. Coverage checks require
all annotated groundings, but measure mechanical reference recovery rather than
semantic completeness or answer correctness. Reported latency includes worker
contention; retrieval costs exclude answer generation and are unknown when a
stage is unpriced.

Publication explicitly selects the snapshot available to the assistant; it does
not approve every claim or summary:

```sh
.venv/bin/python scripts/index_artifacts.py publish --corpus "$CORPUS" --snapshot-id "$INDEX_SNAPSHOT_ID"
.venv/bin/python scripts/index_artifacts.py list --corpus "$CORPUS"
```

## 8. Run the document assistant

The retained Docling run is already registered in
[retrieval-corpora.json](../config/retrieval-corpora.json). Once its snapshot is
published, start the app from a shell with `.envrc` loaded:

```sh
source .envrc
.venv/bin/python -m streamlit run apps/chat/app.py
```

Open `http://localhost:8501` and select the corpus/published snapshot.
**Open provenance graph** shows lineage. The app inherits `KF_DATABASE_URL` and
`KF_RETRIEVAL_CONFIG`; `KF_CORPORA_CONFIG` selects another corpus catalog.
With `OPENAI_API_KEY`, answers and vector/combined search may incur costs;
without it, the app uses keyword search and exact excerpts.

- **`KF_DATABASE_URL is required`**: load `.envrc` in the same terminal and restart.
- **Database unavailable or migrations missing**: check the connection and run
  section 7's migrations.
- **No published snapshots**: complete import, evaluation and publication in
  section 7, then refresh the app. Source files on disk alone do not populate it.

For a new or relocated run, register its paths before startup. This preserves
other catalog entries and does not import or publish data:

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
```

## Back up results and PostgreSQL

Keep the run directory, `artifacts/ingested/`, and the repository version with
its configuration and `data/manifest.json`. Embedding and summary caches avoid
repeated work and paid calls.

To preserve all database corpora, snapshots, vectors, chat history and feedback,
use PostgreSQL client tools compatible with the server:

```sh
source .envrc
mkdir -p artifacts/db
DUMP="artifacts/db/knowledge-foundry-$(TZ=Europe/Paris date +%Y%m%d-%H%M%S).dump"
pg_dump --dbname="$KF_DATABASE_URL" --format=custom --file="$DUMP"
pg_restore --list "$DUMP" > /dev/null
```

Store the dump with the artifact backup. Listing checks readability, not a full
restore. Preserve the original directory layout because metadata may contain
absolute paths. Recreate credentials separately; exclude `.envrc` from backups.

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
