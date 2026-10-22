# Run the document assistant

Run these commands from the repository root in VS Code's terminal. Prerequisites:
Python 3.11+ (tested on 3.12), direnv, and a running PostgreSQL server with pgvector
installed. PostgreSQL runs separately; Docker is not required.
These commands reuse the saved pilot. For new source files, follow the
[source-to-chatbot runbook](pipeline-runbook.md); import that run's explicit paths.

## 1. Create the local database once

```sh
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE knowledge_foundry LOGIN PASSWORD 'baracoda2026';
CREATE DATABASE knowledge_foundry_db OWNER knowledge_foundry;
\connect knowledge_foundry_db
CREATE EXTENSION IF NOT EXISTS vector;
SQL
```

If the role/database already exist, skip their `CREATE` commands. To reset this
role's password: `sudo -u postgres psql -c "ALTER ROLE knowledge_foundry PASSWORD 'baracoda2026';"`.
This is a local demo password; choose a different one for a shared environment.

## 2. Configure Python and credentials

```sh
test -d .venv || python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .envrc || cp .envrc.example .envrc
```

Set these exports in `.envrc` (ignored by Git). Leave the API key empty for offline mode:

```sh
export KF_DATABASE_URL="postgresql://knowledge_foundry:baracoda2026@127.0.0.1:5432/knowledge_foundry_db"
export OPENAI_API_KEY="your-api-key"
```

Run `direnv allow` after editing. The commands below use `direnv exec .` so they
also work without a shell hook. Do not rely on `source .envrc`.

## 3. Migrate, import and publish

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py migrate
direnv exec . .venv/bin/python scripts/index_artifacts.py import --corpus aml-cft-pilot --run 20261019 --embed-missing > /tmp/kf-import.json
cat /tmp/kf-import.json
SNAPSHOT_ID=$(.venv/bin/python -c "import json; print(json.load(open('/tmp/kf-import.json'))['snapshot_id'])")
direnv exec . .venv/bin/python scripts/index_artifacts.py publish --corpus aml-cft-pilot --snapshot-id "$SNAPSHOT_ID"
```

For offline setup, omit `--embed-missing`; keyword search and compatible saved
vectors remain available. Validation of the saved pilot can take several minutes.
Import validates frozen artifacts and stores searchable projections, exact source
lineage and compatible saved vectors. It does not regenerate chunks, claims or
summaries. Identical imports are idempotent. Publication is explicit; failed or
incomplete imports cannot become active, and earlier snapshots remain available.

## 4. Start the chatbot

```sh
direnv exec . .venv/bin/python -m streamlit run apps/chat/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open <http://127.0.0.1:8501>. Select a corpus/version, ask a question and open the
citation links or **Sources and exact passages**. Reset starts a new conversation;
the history selector restores previous sessions. Keep the private session URL private.

With an API key: combined search, reranking and streamed draft answers. Without
one: keyword search and exact source excerpts, without synthesis or rewriting.
Embeddings, routing, reranking and generation can incur API costs. Restart
Streamlit after changing code/configuration.

## Other corpora and commands

`config/retrieval-corpora.json` contains saved runs, suggested questions and optional
local graph paths. `config/retrieval.json` controls search, language, models and budgets;
set its PostgreSQL text-search language for each new corpus **before importing**.
Use `--config` in CLIs or `KF_RETRIEVAL_CONFIG` in the app for another configuration.
`KF_CORPORA_CONFIG` selects another corpus-definition file. Register the actual
outputs under your corpus/run IDs; replace the example IDs and paths below:

```json
{
  "my-corpus": {
    "title": "My corpus",
    "suggested_questions": ["A question supported by these documents"],
    "runs": {
      "my-run": {
        "preparation_dir": "artifacts/structural-chunks-and-semantic-clusters-my-run",
        "claims_dir": "artifacts/classified-claims-my-run",
        "summaries_dir": "artifacts/summaries-my-run",
        "graph_path": "artifacts/classified-claims-my-run/knowledge-provenance-graph.html"
      }
    }
  }
}
```

Claim/summary paths are optional; `graph_path` needs the matching claim branch.
Do not point a new corpus at the older pilot's directories. After registering a
run, import it with `--corpus my-corpus --run my-run --embed-missing`.

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py import --corpus my-corpus --artifact-path /path/to/artifact-bundle.json --embed-missing
direnv exec . .venv/bin/python scripts/index_artifacts.py list --corpus aml-cft-pilot
direnv exec . .venv/bin/python scripts/query.py 'Your question' --corpus aml-cft-pilot --method vector --provider openai
```

Claims/summaries are optional. `--build-id` selects a chunk build; `--snapshot-id`
pins a published snapshot. Repeat `--source-id` or `--document-id` for explicit
filters. `--embed-missing` embeds missing projections and may incur API costs.
Vectors are reused only when text, model, dimensions and preprocessing match.

All three retrieval modes support keyword, vector and combined search. Imports
reuse compatible saved passage and summary vectors, then `--embed-missing` fills
remaining claim/summary vectors through the existing cache/client. Publish the
returned snapshot explicitly; prior snapshots and upstream files remain intact.
The sidebar shows vector coverage for the selected mode. See measured limits in
[the evaluation](retrieval-rag-evaluation.md); navigation vectors do not guarantee
better answers.

**Open provenance graph** displays the local HTML inside the chatbot; no second
server or port 8502 is needed. Set a run's `graph_path` under its `claims_dir` in
`retrieval-corpora.json`. The graph appears only when that branch's manifest hash
matches the selected snapshot. Configure only trusted, generated graph HTML.

**Documents in corpus** lists snapshot metadata with the selected source filter,
without a model call. Generated chat also routes inventory questions to this
metadata; other questions retrieve evidence. Offline chat requires the button
for inventory questions. No source's table of contents is a corpus inventory.

## Understanding retrieval.json

These settings control retrieval, independently of semantic-cluster building.
Sidebar choices override the corresponding runtime defaults. Credentials belong
in environment variables; `version` labels the configuration, not the corpus.

| Settings | Meaning |
| --- | --- |
| `search.method`, `mode` | Default combined keyword/vector search over passages. Claims/summaries locate supporting passages before answering. |
| `candidate_k: 30`, `top_k: 5`, `rerank: true` | Take up to 30 candidates per search method, then retain at most five. Model reranking runs only in generated mode. |
| `minimum_vector_similarity: 0.25` | Minimum cosine similarity for vector candidates; not answer confidence. |
| `minimum_keyword_score: 0`, `minimum_keyword_coverage: 0.25` | Require a positive keyword score and 25% weighted query-term coverage in the record or its best evidence span. Rarer terms weigh more: coverage = matched weights / total query weights. |
| `rrf_constant: 60` | Combine ranks using `1/(60 + keyword_rank) + 1/(60 + vector_rank)`; an absent result contributes zero. Larger constants soften rank differences. |
| `followup_turns: 2`, `max_context_evidence: 24`, `max_context_depth: 4` | Bound previous user questions, admitted evidence records and governing-context expansion. |
| `budget` | Count tokens locally with `cl100k_base`. Allow 12,000 input tokens, 1,600 output and a 1,024 safety reserve within the configured 32,768 window; question/history limits are 1,500 each. |
| `answer` | Provider, model, reasoning effort and timeout for generation, rewriting and reranking. Pricing is a dated estimate, not a spending cap. |
| `search.language`, `embedding` | Text-search language, embedding model/dimensions/preprocessing and request limits. Language or embedding-profile changes require a compatible new import; batching/timeouts do not. Existing snapshots keep their identity. |
| `embedding_pricing`, `cache_dir` | Embedding cost estimates and reusable vector cache. Conversation history is stored in PostgreSQL. |

## Optional feedback

Type an optional comment (up to 2,000 characters), then click **Helpful** (+1) or
**Needs work** (-1). Typing alone does not save. The button stores the rating and
comment in PostgreSQL's `kf_feedback`, attached to that answer and checked against
your session. Clicking again updates the same feedback. It supports later review;
it does not automatically train the model or change retrieval.

## Behavior and limits

`DocumentAssistant` is shared by chat and CLIs. Haystack runs search → rerank →
original-evidence resolution → context assembly. PostgreSQL stores immutable index
snapshots and private conversation histories; frozen pipeline files remain authoritative.

Prompts: `rag_query_v2.txt` routes/rewrites questions; `rag_rank_v1.txt` selects
candidates; `rag_answer_v1.txt` grounds answers and distinguishes source scopes.
They live in `src/knowledge_foundry/prompts/`.

Context expands saved hierarchy and explicit local relationships, completes bounded
lists and deduplicates exact evidence. Passages retain governing context or are
excluded together when request/count/depth budgets are full. Headings provide
uncitable scope. Claim/summary text helps locate evidence and is never cited as proof.

Follow-ups retrieve again in the pinned snapshot using bounded recent user
questions. Standalone questions ignore unrelated history. Excerpt mode has no
semantic reranker/rewrite; ask standalone questions. Generated streams are drafts
until citation validation completes. Validation checks citation identity/coverage,
**not semantic entailment**; inspect original text, qualifications and editions.
Missing evidence is reported; capture dates never prove legal effectiveness.
Model, usage, latency and available cost estimates are saved in request details.

## Validation and troubleshooting

```sh
direnv exec . .venv/bin/python scripts/evaluate_retrieval.py --corpus aml-cft-pilot --cases tests/fixtures/retrieval-pilot-smoke.json --modes passage claim summary --no-rerank --check-excerpts --output artifacts/retrieval-evaluation/pilot.json
direnv exec . .venv/bin/python scripts/evaluate_retrieval.py --corpus aml-cft-pilot --cases tests/fixtures/retrieval-additional-smoke.json --modes passage claim summary --no-rerank --check-excerpts --output artifacts/retrieval-evaluation/additional.json
direnv exec . sh -c 'KF_TEST_DATABASE_URL="$KF_DATABASE_URL" PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q'
```

Evaluations separate locator hits, text anchors and exact citation resolution.
`--no-rerank` compares search alone; omit it to evaluate configured model
reranking (API calls). Query embeddings can still incur costs on cache misses.
Evaluation exits 1 if a case cannot execute; inspect the saved errors. Locator
and anchor scores are metrics, not automatic acceptance thresholds.
`--modes passage claim summary` compares navigation modes; `--sequence` tests
conversation order and may call the query model. See [measured results and limits](retrieval-rag-evaluation.md).

| Failure | Action |
| --- | --- |
| Password authentication | Reset the role password above; match `.envrc`, run `direnv allow`, retry. |
| Missing `vector` extension | Install pgvector for the server's PostgreSQL version, then enable it in `knowledge_foundry_db` as administrator. |
| Artifact hash/lineage mismatch | Select compatible frozen branches; do not edit their files. |
| No published snapshot | Import successfully, then publish the returned ID. |
| Missing/incompatible vectors | Reimport with `--embed-missing`, publish the returned snapshot, then select it; or use keyword search. |
| Graph button absent | Check configured `graph_path`, file availability and the selected snapshot's claim branch. |
| Missing evidence/budget warning | Inspect sources and retrieval details; narrow the question or adjust verified budgets. |
| API failure | Check key/model/timeout; incomplete answer streams are not saved. |

Production still needs authentication, access control, concurrent-turn protection,
backups/retention, connection pooling and deployment hardening. New corpora need
independent evaluation; the pilot is not a general reliability guarantee.

References checked: [Haystack](https://docs.haystack.deepset.ai/docs/custom-components),
[Streamlit](https://docs.streamlit.io/develop/api-reference/chat),
[local HTML embedding](https://docs.streamlit.io/develop/api-reference/text/st.iframe),
[pgvector](https://github.com/pgvector/pgvector),
[PostgreSQL text search](https://www.postgresql.org/docs/17/textsearch-controls.html),
[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
