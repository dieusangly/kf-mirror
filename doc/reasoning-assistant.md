# Reasoning assistant

Ask naturally in English or French. The chat app uses accepted reasoning
scenarios, the published document corpus or labelled general knowledge, without
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
question embeds uncached scenarios; later questions reuse those vectors.

The existing Document Assistant remains independently runnable at port 8501 using
`apps/chat/app.py`. The new app reuses its retrieval/generation services without
saving turns, feedback or sessions to the application database.

Try:

- “Selon la politique de l'exemple, un client enregistré comme PPE doit-il
  obtenir une approbation de la direction ?”
- “What is a COSI?”

## How reasoning works

1. **Vector search retrieves up to six candidate scenarios.** The original user
   question is embedded and compared with cached scenario vectors using cosine
   similarity. Explicit case IDs and established conversation context take
   priority; English/French variants are deduplicated. Embedding inputs contain
   readable scope, facts and policies, never saved questions or reference answers.
2. **The first LLM selects one candidate and generates a structured query.** It
   receives the original question, bounded history, the candidates' readable
   facts/policies and declared predicates/entities, plus document previews. In one
   call it chooses a route and, for reasoning, returns one candidate ID, query
   predicate/arguments/variables and a short readable restatement. The whole
   dataset is not sent to this LLM.
3. **The application adapter constructs the engine query.** It receives the LLM's
   structured output, checks candidate membership and applicability fields, and
   adds trusted dataset identifiers and the engine's schema wrapper. The
   restatement is displayed for interpretation review; it is not engine input.
4. **The engine executes the encoded query with the selected scenario's already
   encoded facts and policy rules.** These encodings come from the accepted
   formalization stage; the first LLM does not recreate them. The engine validates
   schema, vocabulary, types and bindings, then returns a conclusion/status and
   proof certificate. No LLM executes the deduction.
5. **Code checks the proof certificate** and prepares the relevant proof steps
   with their exact fact/policy source references. Unknown or conflicting results
   remain explicit outcomes, without switching to a general answer.
6. **The second LLM produces the chatbot's readable answer and proof explanation.**
   It receives the engine's conclusion/status, proof steps and source text. Code
   checks that the wording preserves the status and proof-step identities/order;
   the UI displays the answer, interpreted question, scenario facts, explained
   proof and exact sources. If wording fails, checked results and source text are
   shown instead. The LLM explains the proof; it does not create or verify it.

Synthetic scenarios are separate fictional contexts. The assistant cannot change
their facts or combine cases. Changed facts or ambiguous applicability require
clarification. Similarity and structural checks do not establish that a scenario
matches the user's situation, that the query captures their meaning, or that the
generated explanation is faithful; these still require semantic review.

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
endpoint, model, dimensions and preprocessing version. Question vectors stay in
session memory. Embedding failures stop the request without fallback. Service
responses retain retrieval, routing, API usage, latency and estimated LLM cost
for diagnostics; raw technical details are not shown in chat. The document route
has its own retrieval configuration.

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
accepted cases/languages. Runtime uses readable inputs and accepted encodings,
not reference answers, saved queries or results.

Stage 5 remains deferred. After an independent Stage 4 review accepts a new batch,
register its manifest, inputs, encodings, acceptance/status, engine config and
schema. Use `source_backed`, preserve passage/source metadata, verify engine/schema
compatibility, then test applicability and source rendering. Source-backed UI
rendering remains unvalidated. The assistant never updates pilot acceptance/status.

## Validate offline

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_reasoning_assistant.py tests/test_reasoning_assistant_search.py tests/test_reasoning_assistant_ui.py -q
.venv/bin/python scripts/evaluate_reasoning_assistant.py --output /tmp/reasoning-assistant-evaluation-NEW.json
```

Use a fresh output filename. The evaluator checks hashes, replays all 192 accepted
language versions, checks certificates and compares complete results, without API
calls. Exit codes: 0 pass, 1 failures, 2 input/I/O error.

Tests use controlled vectors and mocked LLM outputs. Real-model candidate recall,
routing, interpretation and wording quality remain unmeasured. Before relying on
them, agree an API budget and assess unseen bilingual paraphrases, ambiguity,
changed facts, misleading instructions and mixed-source questions. Independently
review semantic fidelity and measure end-to-end latency.
