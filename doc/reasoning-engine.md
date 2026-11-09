# Reasoning Engine

Knowledge Foundry uses [Open Ontologies Lite](https://github.com/fabio-rovai/open-ontologies/tree/c7fd75f9491253c56e4d4f408ceac179de343e48/python)
to derive conclusions from reviewed claims and rules. It is optional and keeps
existing retrieval, PostgreSQL/pgvector and the provenance graph unchanged.
No large language model (LLM) retraining is needed.

## Workflow and review gates

```text
Reviewed claims + reviewed mappings → select jurisdiction/date
  → validate → reason → check conflicts and certificates
  → return conclusion, premises, rules and original citations
```

Each mapped claim needs verified grounding and a complete
build. The mapping also requires a reviewer, timestamp, inspection notes and
matching content hashes. Unreviewed claims and stale mappings are rejected.
Topics, classification status and summaries are not consumed. The input keeps
claims and their evidence lineage (sources, documents, passages, chunks and
builds); topic/classification fields may be omitted. Existing complete artifact
bundles are also accepted, with those fields ignored for reasoning validation.
Hashes still bind the exact supplied input. Sampled review alone does not verify
individual claim grounding.

The [initial subset](../examples/reasoning-aml-cft/artifact-bundle.json) contains
two reviewed L. 561-2-2 article claims for anti-money laundering and combating
the financing of terrorism (AML/CFT), and a separate
[synthetic case](../examples/reasoning-aml-cft/scenario.md). Its
[mapping review](../examples/reasoning-aml-cft/mapping.json) preserves the article's
qualifications. Case conditions are assumptions, not verified customer facts
or a determination of current legal applicability.

The [mapping file](../examples/reasoning-aml-cft/mapping.json) is authored and
reviewed explicitly; there is no automatic LLM conversion. Reviewers choose the
entities and relationships, write `facts` as subject–predicate–object triples,
and encode rules as `body`/`head` patterns with conditions and exceptions.
The [adapter](../src/knowledge_foundry/reasoning/adapter.py) validates and
serializes these mappings, creating Open Ontologies Lite `AtomPat`/`RulePattern`
objects. Open Ontologies Lite then applies the rules; it does not interpret
the original prose. Automated claim-to-mapping generation remains unimplemented.

For session-based authoring, use the [generation prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_formalization_generation),
then the [review prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_formalization_review)
in a fresh session. They cover all generated claim candidates (the upstream
proposals linked to exported claim records), shared vocabulary,
batches of up to 100 mappings, coverage tracking and separate grounding/semantic
judgments. Both preserve draft artifacts; corpus loading and reviewed-output
promotion still need implementation. Repair findings in a separate session,
then review changed entries and any affected vocabulary dependents again.

For the completed AML/CFT formalization run, use the [revised formalization corpus](../artifacts/20261028-docling-aml-cft-01/formalization-v2/README.md)
and [archive-aware draft harness](assistants.md#experimental-drafts-and-testing).
The authoring prompts above describe a workflow, not a request to reopen that
run's completed cycle. The corpus underwent full initial review and subsequent
repair/review work; final acceptance covers four exact revisions. See
[review coverage and acceptance](../artifacts/20261028-docling-aml-cft-01/formalization-v2/README.md#acceptance-and-input-selection).
Application use remains disabled.
Use v2's `current/input-bundle.json` to inspect revised claims. Runtime reasoning
continues to use the separately reviewed export; see [input selection](assistants.md#experimental-drafts-and-testing).

## Six capabilities

| Capability and abbreviation | Purpose | Limit |
| --- | --- | --- |
| **RDF — Resource Description Framework** | Stores subject–relationship–object facts with source versions, passages and review provenance. | In-memory execution; separate asserted/inferred graph files. |
| **SHACL — Shapes Constraint Language** | Validates graph structure through the upstream pySHACL integration. | Does not verify legal interpretation. |
| **OWL — Web Ontology Language; RDFS — RDF Schema** | Derives facts using upstream Horn rules and reviewed custom rules. | Limited logical fragment, not full OWL reasoning. |
| **SPARQL — SPARQL Protocol and RDF Query Language** | Queries facts, deductions and provenance. | Local read-only queries; raw results are not automatically certified. |
| **Consistency checks** | Finds incompatible types, identity conflicts, conflicting obligations and exception/clearance conflicts. | Cannot prove that all possible contradictions are absent. |
| **Inference certificates** | Uses the upstream Lean checker to verify deduction steps. | Sources and supplied rules remain assumptions. |

## Conditions and exceptions

The [mapping schema](../schemas/reasoning-mapping-schema-v1.0.0.json) accepts facts
and rules using Internationalized Resource Identifiers (IRIs): names for entities
and relationships. Rules have premises (`body`), conditions and a conclusion
(`head`). Exceptions need explicit `clearance` evidence; missing evidence means
unknown. An exception and its clearance together block conclusions.

Each run selects one jurisdiction and date (`as_of`). Reviewers supply the
applicability interval: `valid_from` inclusive, `valid_until` exclusive.
Conditional facts must be represented as guarded rules. Inferred conclusions
never become reviewed assertions.

## Setup and example

The project [requirements](../requirements.txt) include Open Ontologies Lite
**0.5.0** and its dependencies. Build the checker from the same source commit,
using Lean **4.33.1** and its `lake` build tool:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
git clone https://github.com/fabio-rovai/open-ontologies.git /tmp/kf-open-ontologies
git -C /tmp/kf-open-ontologies checkout c7fd75f9491253c56e4d4f408ceac179de343e48
(cd /tmp/kf-open-ontologies/lean && lake build oo-horn)
export OO_HORN=/tmp/kf-open-ontologies/lean/.lake/build/bin/oo-horn

.venv/bin/python examples/reasoning-aml-cft/run.py --output /tmp/kf-reasoning-e2e
```

Use fresh paths if these already exist. The example runs the real assistant
pipeline with in-memory search and source excerpts, without model calls.
Its symbolic answer identifies Alice as a beneficial owner under the stated
assumptions: four premises, one rule and five source spans. Read
`assistant-answer.json` → `retrieval.reasoning` for the result.

Check the certificate independently:

```sh
"$OO_HORN" check /tmp/kf-reasoning-e2e/certificate/rules.tsv \
  /tmp/kf-reasoning-e2e/certificate/asserted.tsv \
  /tmp/kf-reasoning-e2e/certificate/horn.tsv
```

Built-in rules can earn `entailed`; custom rules earn
`entailed_under_supplied_rules`. Missing/rejected certificates, changed bytes or
incomplete reasoning withhold conclusions. For standalone deductions and queries,
run `.venv/bin/python scripts/reason.py --help`.

## Assistant integration

The Streamlit app provides **Document assistant** (default retrieval) and
**Reasoning assistant** (bounded French/English questions over accepted facts/rules).
See [assistant setup and usage](assistants.md) for the accepted subset, separate
reviews, PostgreSQL import, history migration and proof boundaries.

Pass a completed engine as `DocumentAssistant(..., reasoning_engine=engine)`.
Then pass `reasoning_goal=[subject, predicate, object]` to `retrieve`, `stream`
or `ask`. Alternatively, enable the `reasoning` configuration with `bundle`,
`mapping`, `jurisdiction`, `as_of` and `output_dir`; see
[configuration loading](../src/knowledge_foundry/reasoning/retrieval.py).

Every proof citation must resolve in the selected snapshot, respect filters and
fit the evidence/token budgets. Accepted proofs enter the answer prompt; full
explanations appear in `retrieval.reasoning`. Unsupported or unchecked goals
return insufficient evidence without calling the answer model. Requests without
a goal keep normal retrieval-augmented generation (RAG) behavior.

## Validation and limitations

```sh
PYTHONPATH=.:src .venv/bin/python -m pytest tests/reasoning -q
PYTHONPATH=.:src .venv/bin/python -m pytest -q
```

Tests cover review gates, scope, exceptions, contradictions, certificate integrity
and citation budgets. The real checker is required for certificate tests;
PostgreSQL tests require `KF_TEST_DATABASE_URL`. See [assistant usage and validation](assistants.md#experimental-drafts-and-testing)
for natural-language and browser coverage. Model responses in those checks are mocked.

Remaining work: broader reviewed mappings, full OWL consistency support and
comparison with the 123-question RAG dataset. Natural-language interpretation
now produces validated finite queries; see [assistant usage](assistants.md#configuration).
Numeric comparisons, general negation and automatic prose-to-rule extraction
are unsupported. This example does not establish production readiness or a
measured accuracy/speed advantage.
