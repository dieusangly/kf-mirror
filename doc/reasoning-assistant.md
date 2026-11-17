# Reasoning assistant

Ask naturally in English or French. The chat app uses accepted reasoning
policies, the published document corpus or labelled general knowledge, without
requiring family, category or language selection. Conversation history supports
clarification and follow-up questions.

Inspect proofs, sources and question interpretation beside answers. Use
**New conversation** in the main header to reset the chat.

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
model calls; submitted questions can incur embedding and LLM costs. The first
question embeds uncached policy contexts; later questions reuse those vectors.

The existing Document Assistant remains independently runnable at port 8501 using
`apps/chat/app.py`. The new app reuses its retrieval/generation services without
saving turns, feedback or sessions to the application database.

Try:

- “Selon la politique de l'exemple, un client enregistré comme PPE doit-il
  obtenir une approbation de la direction ?”
- “What is a COSI?”

## How reasoning works

1. **Retrieve policy contexts.** Vector inputs contain accepted scope, policy
   clauses and vocabulary meanings/signatures, never pilot customer facts, saved
   queries or answers. Identical policy variants are deduplicated. The embedding
   preprocessing version separates these inputs from the former scenario cache.
2. **Interpret supplied context and question.** The interpretation LLM receives up
   to six policy candidates, ordered predicate arguments/types, required policy
   constants, bounded user history and document previews. It chooses one coherent
   policy, declares case entities, cites exact user excerpts for facts and translates
   the question separately. It clarifies ambiguous applicability, unsupported
   policies/logic or unresolved identities. New customer facts are supported;
   missing premises under an established policy remain an engine question.
3. **Build a fresh case in code.** The adapter copies only selected accepted rules,
   incompatibilities, vocabulary and required rule constants. Customer assertions
   come exclusively from cited user text. It validates citation locations, literal
   entity mentions, predicate arity/types and query variables. A broad question
   citation is narrowed only when it contains one explicit question sentence.
   Questions and conditionals cannot supply facts; assistant messages are excluded
   from this interpretation input. No customer identity is mapped to a pilot identity.
4. **Execute and check.** The unchanged symbolic engine binds variables and applies
   rules to this runtime encoding, including direct lookup with zero rules. Missing
   premises yield unknown; explicit negatives, positive exception clearance and
   conflicts retain their accepted semantics. Code checks the proof against this
   exact encoding and query before displaying a result.
5. **Explain and inspect.** A second LLM explains the checked result. The UI shows
   supplied excerpts, encoded facts/entities, interpreted query, proof and sources.
   Fact proof steps cite user text; policy steps retain accepted source IDs. Wording
   with a changed status or proof-step list falls back to the checked result.

Policies remain fictional demonstrations unless registered as accepted source-backed
policies. Encoding an assertion does not verify its truth. Citation/structural checks
cannot establish semantic fact/query fidelity or explanation accuracy: these require
real-model evaluation and independent review. Models can still omit or mistranslate
assertions despite using valid citations.

Each request builds a new case identity; the service stores no case encoding between
calls. A clear follow-up may cite prior user assertions only within the established
policy context. **New conversation** clears the history, pending question and private
query cache. Empty history always starts an independent case. Ambiguous corrections
or temporal changes require clarification rather than combining assessment scopes.

## Other routes

Actual requirements, definitions and acronyms covered by the corpus use document
assistance, including short questions such as “What is a COSI?”. Fictional policies
cannot establish real obligations. Document previews use read-only keyword search
over content terms, prioritising matching passages over surrounding context.

If the first LLM proposes general knowledge and a preview contains passages, the
full document assistant checks the question first. A cited corpus answer takes
precedence; only explicit insufficient evidence permits that proposed general
answer. An explicit request for general knowledge bypasses this check. A selected
document route never falls back on insufficient evidence, unavailable services or
invalid output. Citation checks do not establish semantic or legal applicability.
General answers are labelled and carry no engine-checked proof.

## Configuration and local providers

[`config/reasoning-assistant.json`](../config/reasoning-assistant.json) configures
interpretation, wording and general conversation separately: GPT-6 Luna with
medium, none and low effort respectively. Each role has input/output limits, a
timeout and zero retries. Input estimates use `cl100k_base`; provider counts can
differ. Set `KF_ASSISTANT_CONFIG` to override the file and restart to reload.

`scenario_search` uses `text-embedding-3-small` at 1536 dimensions. Its persistent
cache is `artifacts/reasoning-assistant/embeddings`, keyed by text, provider,
endpoint, model, dimensions and preprocessing version. These vectors retrieve
policies; the engine consumes the fresh symbolic case, not vectors.
Question vectors stay in session memory. Embedding failures stop the request
without fallback. Service responses retain retrieval, routing, API usage, latency
and estimated LLM cost for diagnostics; raw technical details are not shown in
chat. The document route has its own retrieval configuration.

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

[`config/reasoning-datasets.json`](../config/reasoning-datasets.json) owns package
paths and pinned acceptance/status hashes. Loading checks prerequisite hashes and
accepted cases/languages. Runtime projects accepted policies and vocabulary from
readable inputs/encodings; it never executes their saved customer facts. Reference
answers, saved queries and results are excluded from request processing.
`config/reasoning-vocabulary.json` defines predicate meanings and argument roles;
keep it aligned with accepted signatures.

Stage 5 remains deferred. After an independent Stage 4 review accepts a new batch,
register its manifest, inputs, encodings, acceptance/status, engine config and
schema. Use `source_backed`, preserve passage/source metadata, verify engine/schema
compatibility, then test applicability and source rendering. Source-backed UI
rendering remains unvalidated. The assistant never updates pilot acceptance/status.

## Validate offline

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_reasoning_assistant.py tests/test_reasoning_assistant_search.py tests/test_reasoning_assistant_ui.py tests/test_reasoning_assistant_inputs.py -q
.venv/bin/python scripts/replay_reasoning_pilot.py --output /tmp/reasoning-pilot-replay-NEW.json
```

Use a fresh output filename. The pilot replay script verifies 192 accepted
language versions and their certificates without
API calls. It does not validate natural-language fact formalization.

For the version 1 evaluation of new cases, their fact-change variants, and routing,
see the [evaluation protocol](aml-cft-reasoning-assistant-evaluation.md). Stage 1
produces engine-checked proposed references; independent semantic review is still
required. Runtime tests use separate development fixtures. Offline checks establish
orchestration and data integrity; real-model retrieval, interpretation and wording
quality remain unmeasured. Live runs require agreed providers, budget reservations
and a published document snapshot.
