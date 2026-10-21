# Run the document assistant

Run these commands from the repository root in VS Code's terminal. Prerequisites:
Python 3.11+ (tested on 3.12), direnv, and a running PostgreSQL server with pgvector
installed. PostgreSQL runs separately; Docker is not required.

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
direnv exec . .venv/bin/python scripts/index_artifacts.py import --corpus aml-cft-pilot --run 20261019 > /tmp/kf-import.json
cat /tmp/kf-import.json
SNAPSHOT_ID=$(.venv/bin/python -c "import json; print(json.load(open('/tmp/kf-import.json'))['snapshot_id'])")
direnv exec . .venv/bin/python scripts/index_artifacts.py publish --corpus aml-cft-pilot --snapshot-id "$SNAPSHOT_ID"
```

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

With an API key, defaults are combined search, model reranking and streamed draft answers. Without a
key, the app uses keyword search and exact source excerpts. Excerpts are retrieved
text, not synthesized answers; reranking and follow-up rewriting are disabled in this mode. Embeddings, reranking, follow-up rewriting and generated answers can incur API costs. Restart Streamlit after changing code/configuration.

## Other corpora and commands

`config/retrieval-corpora.json` contains saved runs, suggested questions and optional
graph URLs. `config/retrieval.json` controls search, language, models and budgets;
set its PostgreSQL text-search language for each new corpus **before importing**.
Use `--config` in CLIs or `KF_RETRIEVAL_CONFIG` in the app for another configuration.
`KF_CORPORA_CONFIG` selects another corpus-definition file.

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py import --corpus my-corpus --artifact-path /path/to/artifact-bundle.json
direnv exec . .venv/bin/python scripts/index_artifacts.py import --corpus my-corpus --preparation-dir /path/to/preparation --claims-dir /path/to/classified --summaries-dir /path/to/summaries
direnv exec . .venv/bin/python scripts/index_artifacts.py list --corpus aml-cft-pilot
direnv exec . .venv/bin/python scripts/query.py 'Your question' --corpus aml-cft-pilot --method vector --provider openai
```

Claims/summaries are optional. `--build-id` selects a chunk build; `--snapshot-id`
pins a published snapshot. Repeat `--source-id` or `--document-id` for explicit
filters. `--embed-missing` embeds missing projections and may incur API costs.
Vectors are reused only when text, model, dimensions and preprocessing match.

For the pilot graph link, run `.venv/bin/python -m http.server 8502 --bind 127.0.0.1 --directory artifacts`.

## Behavior and limits

`retrieval/` runs the Haystack search → rerank → evidence resolution → context pipeline;
`storage/` manages PostgreSQL and migrations. Apps and CLIs share `DocumentAssistant`.
Frozen pipeline files remain authoritative.

- Keyword ranking uses PostgreSQL's configured language and distinct term coverage;
  vector search uses pgvector cosine similarity; combined search fuses both ranks.
  Generated mode reranks up to `candidate_k` complete records within the request budget;
  `search.rerank: false` disables this extra model call. Source/document filters are explicit. There are no corpus-specific aliases,
  question patterns, acronym rules or hardcoded example answers.
- Context expands only saved hierarchy and supported artifact relationships.
  Lists split across passages expand through their saved direct governing relationship.
  Exact spans/list groups retain their governing context or are excluded together
  when count, depth or complete request budgets are exceeded. Textual section
  numbers alone do not create relationships. Headings provide uncitable scope;
  body passages supply answer citations.
- Generated-mode follow-ups use a bounded structured query rewrite, then retrieve
  again in the pinned snapshot. Standalone questions ignore unrelated history.
  Source-excerpt mode or rewrite failure uses the current question and warns that
  a standalone question is needed. Only the configured recent history is available.
- Citations are validated and resolve to exact original text, edition and location.
  Validation checks identity/coverage, **not semantic entailment**. Answers remain
  drafts and may be incomplete or incorrect. Insufficient evidence is reported;
  retrieval dates never establish legal effectiveness.
- Model, usage, latency and available cost estimates are recorded; ranking, query-rewrite and embedding usage appear in retrieval details, separately from answer usage.

## Validation and troubleshooting

```sh
direnv exec . .venv/bin/python scripts/evaluate_retrieval.py --corpus aml-cft-pilot --cases tests/fixtures/retrieval-pilot-smoke.json --check-excerpts --output artifacts/retrieval-evaluation/pilot.json
direnv exec . .venv/bin/python scripts/evaluate_retrieval.py --corpus aml-cft-pilot --cases tests/fixtures/retrieval-additional-smoke.json --check-excerpts --output artifacts/retrieval-evaluation/additional.json
direnv exec . sh -c 'KF_TEST_DATABASE_URL="$KF_DATABASE_URL" PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q'
```

Evaluations separate locator hits, text anchors and exact citation resolution.
With `rerank: true` and OpenAI mode, evaluation calls the ranking model; set
`rerank: false` to compare search alone. All model requests have explicit bounds.
`--modes passage claim summary` compares navigation modes; `--sequence` tests
conversation order and may call the query model. See [measured results and limits](retrieval-rag-evaluation.md).

| Failure | Action |
| --- | --- |
| Password authentication | Reset the role password above; match `.envrc`, run `direnv allow`, retry. |
| Missing `vector` extension | Install pgvector for the server's PostgreSQL version, then enable it in `knowledge_foundry_db` as administrator. |
| Artifact hash/lineage mismatch | Select compatible frozen branches; do not edit their files. |
| No published snapshot | Import successfully, then publish the returned ID. |
| Missing/incompatible vectors | Use keyword search or explicitly embed compatible missing projections. |
| Missing evidence/budget warning | Inspect sources and retrieval details; narrow the question or adjust verified budgets. |
| API failure | Check key/model/timeout; incomplete answer streams are not saved. |

Production still needs authentication, access control, concurrent-turn protection,
backups/retention, connection pooling and deployment hardening. New corpora need
independent evaluation; the pilot is not a general reliability guarantee.

References checked: [Haystack](https://docs.haystack.deepset.ai/docs/custom-components),
[Streamlit](https://docs.streamlit.io/develop/api-reference/chat),
[pgvector](https://github.com/pgvector/pgvector),
[PostgreSQL text search](https://www.postgresql.org/docs/17/textsearch-controls.html),
[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
