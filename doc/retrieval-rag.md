# Run the document assistant

From the repository root, reuse the saved pilot with Python 3.11+ (tested on 3.12),
direnv and PostgreSQL/pgvector; Docker is optional. For new inputs use the
[runbook](pipeline-runbook.md) and explicit output paths.

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

Run `direnv allow` after editing. `direnv exec .` needs no shell hook or `source .envrc`.

## 3. Migrate, import and publish

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py migrate
direnv exec . .venv/bin/python scripts/index_artifacts.py import --corpus aml-cft-pilot --run 20261019 --embed-missing > /tmp/kf-import.json
cat /tmp/kf-import.json
SNAPSHOT_ID=$(.venv/bin/python -c "import json; print(json.load(open('/tmp/kf-import.json'))['snapshot_id'])")
direnv exec . .venv/bin/python scripts/index_artifacts.py publish --corpus aml-cft-pilot --snapshot-id "$SNAPSHOT_ID"
```

Offline, omit `--embed-missing` for keywords/saved vectors. Import validates frozen
artifacts and stores projections/lineage without regeneration; allow several minutes.
Identical imports are idempotent. Only successful complete imports can be explicitly
published; earlier snapshots remain available.

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

`--build-id` selects a chunk build; `--snapshot-id` pins a published snapshot.
Repeat `--source-id`/`--document-id` for filters. All modes support keyword, vector
and combined search. Vectors reuse matching text/model/dimensions/preprocessing;
`--embed-missing` fills gaps through the cache/client and may cost money. The
sidebar shows coverage; [evaluation](retrieval-rag-evaluation.md) measures quality.

**Open provenance graph** embeds trusted local HTML without another server.
Set `graph_path` under the run's `claims_dir`; its manifest hash must match the snapshot.

**Documents in corpus** lists filtered snapshot metadata without model calls.
Generated inventory questions use that metadata; offline inventory requires the button.

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

Click **Helpful** (+1) or **Needs work** (-1), optionally with a ≤2,000-character
comment, to save session-checked `kf_feedback` for that answer. Clicking again
updates it. Typing alone does not save; feedback does not train or alter retrieval.

## Behavior and limits

`DocumentAssistant` is shared by chat and CLIs. Haystack runs search → rerank →
original-evidence resolution → context assembly. PostgreSQL stores immutable index
snapshots and private conversation histories; frozen pipeline files remain authoritative.

Prompts: `rag_query_v2.txt` routes/rewrites questions; `rag_rank_v1.txt` selects
candidates; `rag_answer_v1.txt` grounds answers and distinguishes source scopes.
They live in `src/knowledge_foundry/prompts/`.

Context follows saved hierarchy/local links, completes bounded lists and deduplicates
exact evidence. Governing context stays with passages or both are excluded at budget
limits. Headings are uncitable; claim/summary text locates evidence without proving it.

Follow-ups retrieve within the pinned snapshot using bounded user history;
standalone questions ignore unrelated history. Excerpt mode lacks semantic
reranking/rewriting. Streams remain drafts until citation validation, which checks
identity/coverage, **not entailment**. Inspect original qualifications/editions;
capture dates do not establish effectiveness. Request details save missing-evidence
warnings, model, usage, latency and available cost estimates.

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
