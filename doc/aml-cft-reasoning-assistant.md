# Reasoning assistant

Ask in English or French without selecting a family, category or language. The
chat app uses accepted reasoning policies, the published document corpus or labelled
general knowledge, with conversation history for clarification and follow-ups.
Inspect proofs, sources and question interpretation beside each answer.

## Start

From the repository root, with [pipeline dependencies](aml-cft-pipeline.md#1-install-and-select-inputs)
installed and the accepted pilot artifacts present:

```sh
source .envrc
.venv/bin/python -m streamlit run apps/assistant/app.py --server.port 8502
```

Open `http://localhost:8502`. `OPENAI_API_KEY` enables interpretation, explanation
and general answers. `KF_DATABASE_URL` and a published `aml-cft-pilot-13` snapshot
enable the document route; see [publication](aml-cft-pipeline.md#7-rebuild-the-postgresql-search-projection).
Without that database, document requests report unavailability. Startup makes no
model calls; submitted questions can incur embedding and LLM costs.

The Document Assistant runs separately at port 8501 using `apps/chat/app.py`.
This app reuses its retrieval/generation services without saving turns, feedback
or sessions to the application database.

Try:

- “Selon la politique de l'exemple, un client enregistré comme PPE doit-il
  obtenir une approbation de la direction ?”
- “What is a COSI?”

## Routing and retrieval

Each submitted question follows three steps:

1. **Retrieve accepted policy contexts by vector similarity.** The registry derives
   contexts from the 192 accepted pilot language versions and deduplicates identical
   policies. It embeds scope, clauses and vocabulary meanings/signatures, excluding
   saved customer facts, queries, reference answers and results. Cosine similarity
   against the question selects up to `candidate_limit` contexts (default **6**),
   prioritising the previous policy for follow-ups. Accepted rule-free lookup
   vocabularies are appended separately, so the router can receive more than six
   candidates.
2. **Preview document evidence by keyword search.** Read-only search over question
   content terms supplies at most **5 passages**, prioritising matches over
   surrounding context. This preview uses neither vector search nor LLM reranking;
   unavailable document services are reported to the router.
3. **Route and interpret in one LLM call.** `steps.question_interpretation` uses
   [`assistant_route_v1.txt`](../src/knowledge_foundry/prompts/assistant_route_v1.txt)
   with the question, bounded user history, policy candidates and document preview.
   It selects a route and, for reasoning, one candidate, case entities, facts with
   exact user quotations, and a separate symbolic query, without a second policy
   search or candidate-selection call.

| Route | When it applies | What follows |
| --- | --- | --- |
| `reasoning` | Supplied facts and a question match one accepted policy or lookup vocabulary. | Build a fresh case, execute the symbolic engine and check its proof. |
| `documents` | Actual requirements, laws, corpus inventory, definitions or acronyms such as COSI. | Run full document retrieval and cited answering, even when the preview did not contain the answer. |
| `clarify` | Policy applicability, identity or temporal scope is ambiguous; requested logic is unsupported; or facts cannot be translated faithfully. | Ask a short question identifying the missing distinction. |
| `general` | Ordinary conversation outside sources or explicitly requested general knowledge. | Generate a labelled answer without an engine-checked proof, subject to the document check below. |

Accepted policies are fictional demonstrations, not real obligations.
New customer facts are supported under an established policy;
missing premises yield `unknown` through reasoning rather than clarification.
Code also returns clarification when interpretation validation rejects a reasoning
case. Failures in routing, policy search or engine execution produce an error.

Full document answering uses `documents.retrieval_config`, currently
[`config/retrieval-multilingual.json`](../config/retrieval-multilingual.json):
`search.method: combined` combines keyword and vector retrieval, with
`search.rerank: true`. Its `answer.model` is `gpt-6-luna` with low reasoning effort.
This is separate from the lightweight preview and accepted-policy search.

If the router proposes general knowledge and the preview contains passages, the
full document assistant checks the question first. Only explicit insufficient
evidence permits that proposed general answer; errors and unavailability remain
document outcomes. An explicit request for general knowledge bypasses this check.
A selected document route never switches to general knowledge on insufficient
evidence, unavailable services or invalid output.

## Symbolic reasoning and proof

1. **Build a fresh case in code.** The adapter copies only selected accepted rules,
   incompatibilities, vocabulary and required rule constants. Customer assertions
   come exclusively from cited user text. It validates citation locations, literal
   entity mentions, predicate arity/types and query variables. A broad question
   citation is narrowed only when it contains one explicit question sentence.
   Questions and conditionals cannot supply facts; assistant messages are excluded
   from this interpretation input. No customer identity is mapped to a pilot identity.
2. **Execute and check.** The symbolic engine binds variables and applies rules to
   this runtime encoding, including direct lookup with zero rules. Explicit
   negatives, positive exception clearance and conflicts retain their accepted
   semantics. Code checks the proof against this exact encoding and query before
   displaying a result.
3. **Explain and inspect.** A subsequent `answer_verbalization` LLM call explains
   the checked result. Inspect supplied excerpts, encoded facts/entities, query,
   proof and sources in the UI. Fact steps cite user text; policy steps retain
   accepted source IDs. Wording with a changed status or proof-step list falls
   back to the checked result.

Encoding an assertion does not verify its truth. Independent semantic review must
assess fact/query interpretation, explanation accuracy and legal applicability:
models can omit or mistranslate assertions despite valid citations and structure.

Each request builds a new case identity; the service stores no case encoding between
calls. A clear follow-up may cite prior user assertions only within the established
policy context. **New conversation** clears the history, pending question and private
query cache. Empty history always starts an independent case. Ambiguous corrections
or temporal changes require clarification rather than combining assessment scopes.

## Configuration and local providers

Set each role's `model` and `reasoning_effort` in
[`config/aml-cft-reasoning-assistant.json`](../config/aml-cft-reasoning-assistant.json):

| Configuration path | Purpose | Model | Reasoning effort |
| --- | --- | --- | --- |
| `steps.question_interpretation` | Routing, policy selection and fact/query translation | `gpt-6-luna` | `medium` |
| `steps.answer_verbalization` | Explain the checked symbolic result | `gpt-6-luna` | `none` |
| `steps.general` | General conversation | `gpt-6-luna` | `low` |

Each role also has input/output limits, a timeout and zero retries. Input estimates
use `cl100k_base`; provider counts can differ. Set `KF_ASSISTANT_CONFIG` to override
the file and restart to reload.

`scenario_search.embedding.model` is `text-embedding-3-small` with `dimensions: 1536`.
Uncached policy contexts are embedded on demand; subsequent questions reuse the
persistent cache at `artifacts/aml-cft-reasoning-assistant/embeddings`. Cache keys
include text, provider, endpoint, model, dimensions and preprocessing version;
`reasoning-policy-text-v2` separates policy inputs from the former scenario cache.
Question vectors stay in session memory. Embedding failures stop the request
without fallback. The engine consumes the fresh symbolic case, not vectors.

Service responses retain retrieval, proposed/selected routes, API usage, latency
and estimated LLM cost for diagnostics; raw technical details are not shown in chat.

For local operation:

- Set each role's `provider` to `local`, `endpoint` to its loopback `/v1/responses`
  URL and `model` to the served ID; use `reasoning_effort: null` if unsupported.
  The server must support strict JSON schema and completed Responses-style output.
- Set `scenario_search.embedding` to a local provider, loopback `/v1/embeddings`
  endpoint and the served model/dimensions. It must accept those dimensions and
  return an OpenAI-style embedding response.
- Set `local_only: true` to require local providers and disable the entire cloud
  document adapter. Local requests send no cloud key, refuse redirects and never
  fall back to cloud. Local runtimes/models and a fully local document route still
  require installation and validation.

Conversations and raw provider responses are not persisted. OpenAI LLM requests
use `store: false`, which does not establish provider retention policy. Cloud mode
sends input to the provider; avoid sensitive data. This development UI has no
production authentication/access controls.

## Register another accepted dataset

[`config/aml-cft-reasoning-datasets.json`](../config/aml-cft-reasoning-datasets.json)
owns package paths and pinned acceptance/status hashes. Loading checks prerequisite
hashes and accepted cases/languages.
[`config/aml-cft-reasoning-vocabulary.json`](../config/aml-cft-reasoning-vocabulary.json)
defines predicate meanings and argument roles; keep it aligned with accepted
signatures.

Stage 5 remains deferred. After an independent Stage 4 review accepts a new batch,
register its manifest, inputs, encodings, acceptance/status, engine config and
schema. Use `source_backed`, preserve passage/source metadata, verify engine/schema
compatibility, then test applicability and source rendering. Source-backed UI
rendering remains unvalidated. The assistant never updates pilot acceptance/status.

## Validation and evaluation status

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_reasoning_assistant.py tests/test_reasoning_assistant_search.py tests/test_reasoning_assistant_ui.py tests/test_reasoning_assistant_inputs.py -q
.venv/bin/python scripts/replay_reasoning_pilot.py --output /tmp/reasoning-pilot-replay-NEW.json
```

Use a fresh output filename. The pilot replay script verifies 192 accepted
language versions and their certificates without API calls. It does not validate
natural-language fact formalization.

Runtime tests use separate development fixtures and establish orchestration and
data integrity. The final live evaluation is **not accepted —
`completed-with-failures`**: routing and construction compatibility remain blockers,
with both repair rounds exhausted. The [evaluation guide](aml-cft-reasoning-assistant-evaluation.md)
owns scores, evidence, generalization limits and prerequisites for further runs.
