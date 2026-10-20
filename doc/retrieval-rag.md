# Retrieval/RAG and document assistant

The assistant consumes validated, frozen artifacts. It uses Haystack, PostgreSQL
with pgvector, and Streamlit. Run commands from the repository root with Python
3.11+ (tested on 3.12), one `.venv` and one `.envrc`.

Use a running PostgreSQL server with pgvector installed. Create a database and
role, then set `KF_DATABASE_URL` in `.envrc` to their connection URL. The role
needs permission to create tables in that database. An administrator must enable
`CREATE EXTENSION IF NOT EXISTS vector;` there, or allow the migration role to do so.

## Start locally

```sh
test -d .venv || python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .envrc || cp .envrc.example .envrc
# Set local values in .envrc; URL-encode special characters in the database URL.
source .envrc
.venv/bin/python scripts/index_artifacts.py migrate
.venv/bin/python scripts/index_artifacts.py import --corpus aml-cft-pilot --run 20261019
# Replace the ID below with snapshot_id from the import output.
.venv/bin/python scripts/index_artifacts.py publish --corpus aml-cft-pilot --snapshot-id 'snapshot:REPLACE'
.venv/bin/python -m streamlit run apps/chat/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open <http://127.0.0.1:8501>. The default keyword/extractive mode shows source
excerpts without API calls. Vector queries, new embeddings and generated answers
require `OPENAI_API_KEY`. `.envrc` is ignored by Git; direnv users can load it with
`direnv allow`.

Database installation, storage and backups are managed separately from this repository.

## Import and query

```sh
.venv/bin/python scripts/index_artifacts.py import --corpus my-corpus --artifact-path /path/to/artifact-bundle.json
.venv/bin/python scripts/index_artifacts.py import --corpus my-corpus --preparation-dir /path/to/preparation --claims-dir /path/to/classified --summaries-dir /path/to/summaries
.venv/bin/python scripts/index_artifacts.py list --corpus my-corpus
.venv/bin/python scripts/query.py 'Your question' --corpus aml-cft-pilot --method vector --provider openai
.venv/bin/python scripts/query.py 'Your question' --corpus aml-cft-pilot --mode claim --method keyword --retrieve-only
```

- `config/retrieval-corpora.json` selects saved runs, questions and graph links.
  The pilot uses the structural chunk and semantic cluster build outputs frozen
  in `generated-claims-20261019/preparation`, matching both optional branches. Claims and summaries are not required.
- `--build-id` selects a chunk build. `--snapshot-id` pins a published snapshot;
  otherwise each request pins the active one. Repeat `--source-id` or
  `--document-id` to filter queries.
- Compatible saved vectors are reused. `--embed-missing` embeds all missing
  projections, including claims/summaries, and may incur API costs. Bundle-only
  imports start without vectors; use keyword search until vectors are available.

## Chat configuration

`config/retrieval.json` controls search, models and request budgets. CLI overrides
use `--config`; the app uses `KF_RETRIEVAL_CONFIG`. For generated answers, copy
that file and set `answer.provider` to `openai`; optionally set `search.method`
to `vector`. Start with the custom file:

```sh
KF_RETRIEVAL_CONFIG=/path/to/config.json .venv/bin/python -m streamlit run apps/chat/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

`KF_CORPORA_CONFIG` selects alternative corpus definitions. To enable the pilot's
provenance-graph link, serve its saved graph in another terminal:

```sh
.venv/bin/python -m http.server 8502 --bind 127.0.0.1 --directory artifacts
```

History and feedback persist per private URL token. Keep that URL private. Reset
starts a new conversation; the history selector restores previous conversations.

## Architecture and guarantees

Under `src/knowledge_foundry/`, `retrieval/` maps artifacts and runs the Haystack
retrieval → evidence resolution → context pipeline; `storage/` owns PostgreSQL and
versioned migrations. The chat app and CLIs call `DocumentAssistant`.

- Imports validate existing artifacts and commit atomically. Publication is
  explicit; identical imports are idempotent and prior snapshots remain stored.
  Frozen files remain authoritative, with source editions, exact spans, original
  chunk/build IDs, hierarchy and review statuses retained in projections.
- Search supports PostgreSQL keyword ranking, exact cosine search and combined
  reciprocal-rank fusion. Claims/summaries resolve through original chunk and
  evidence lineage to passages. They are navigation aids, not answer evidence.
- Each turn retrieves again. Context expands bounded parents and explicit local
  note links, deduplicates exact locations and admits whole passage/context
  bundles within the complete request budget. Clustering thresholds stay separate.
- The versioned prompt treats retrieved text as data. Final citation IDs and
  paragraph coverage are validated; this does not establish semantic support.
  Streamed text is an unvalidated draft until completion. Capture dates do not
  establish legal effectiveness. Telemetry reports model, usage, latency and
  dated cost estimates, including cache writes.

## Tests and evaluation

```sh
export KF_TEST_DATABASE_URL="$KF_DATABASE_URL"
PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q
.venv/bin/python scripts/evaluate_retrieval.py --corpus aml-cft-pilot --cases tests/fixtures/retrieval-pilot-smoke.json --output artifacts/retrieval-evaluation/passage-smoke.json
.venv/bin/python scripts/evaluate_retrieval.py --corpus aml-cft-pilot --cases tests/fixtures/retrieval-pilot-smoke.json --methods keyword --modes claim summary --output artifacts/retrieval-evaluation/navigation-smoke.json
```

Model tests use deterministic fixtures. Database tests create disposable schemas
and require schema/extension privileges; they skip if `KF_TEST_DATABASE_URL` is
unset. See [results and limitations](retrieval-rag-evaluation.md).

## Troubleshooting and limits

| Problem | Action |
| --- | --- |
| Database unavailable | Check PostgreSQL, `KF_DATABASE_URL` and pgvector, then run migrations. |
| Artifact hash/lineage mismatch | Select compatible frozen branches; validation can take several minutes. |
| No published snapshot | Import, inspect the result, then publish its ID. |
| Missing/incompatible vectors | Use keyword search, match the embedding profile, or explicitly embed missing records. |
| Empty/oversized evidence | Inspect budget/depth warnings in the source panel; insufficient evidence is reported. |
| API failure or incomplete stream | Check the key, model and timeout; failed turns are not saved. |

The demo has session isolation, not authentication. Production needs access
controls, concurrent-turn protection, backups/retention, connection pooling and
deployment hardening. Retrieval quality needs a reviewed evaluation set.

Framework documentation checked on 2026-10-19:
[Haystack](https://docs.haystack.deepset.ai/docs/custom-components),
[Streamlit](https://docs.streamlit.io/develop/api-reference/chat),
[pgvector](https://github.com/pgvector/pgvector),
[OpenAI streaming](https://developers.openai.com/api/docs/guides/streaming-responses)
and [model pricing](https://developers.openai.com/api/docs/models/gpt-6-luna).
