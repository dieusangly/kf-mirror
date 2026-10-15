# Local topic compilation

Compilation has two stages: generate evidence-linked candidates, then classify
saved candidates into curated topics. Both make model calls and write fresh
output directories. Neither changes the input chunks.

## Setup

Install [the local environment](local-ingestion.md). Copy `.envrc.example` to
`.envrc`, set `OPENAI_API_KEY`, and load it with your shell or `direnv`.
`.envrc` is ignored by Git.

[`config/compilation.json`](../config/compilation.json) sets the provider, model,
reasoning effort, batch size, retries and versioned pricing. Defaults are
`gpt-6-luna`, `medium` reasoning and four chunks per generation request.
Classification makes one request per candidate, plus retries.

[`config/topics-v1.json`](../config/topics-v1.json) defines stable topic IDs and
scopes. Assign by the claim's main subject; unrelated narratives may remain
unclassified. Topic changes require reclassification, not regeneration.
Claims and topic views use French; IDs and JSON fields remain unchanged.

## Generate and classify

Run from the repository root. Choose fresh output names for each run:

```sh
.venv/bin/python scripts/generate_claims.py \
  --chunked-dir artifacts/chunked-20261013-114244 \
  --output-dir artifacts/generated-claims-new

.venv/bin/python scripts/classify_claims.py \
  --candidates-dir artifacts/generated-claims-new \
  --output-dir artifacts/classified-claims-new
```

To test a subset first, add `--subset-batches 10` to generation. This samples by
request length and does not guarantee every source or topic is covered.
Alternatively, `--batch-numbers 1 2` selects explicit one-based original batches;
choose them for the current chunk bundle. The two options are mutually exclusive.

Generation saves checkpoints after each batch. To resume an interrupted run,
repeat its exact command with `--resume`. Input, configuration, prompt and subset
must match. Classification requires a completed generation manifest.

Classification reuses immutable `claim-candidates.jsonl` records and checks their
manifest and hashes. To apply revised topics, classify the same candidate
directory into another fresh output directory. Candidate text and evidence do
not change; previously unclassified candidates are reconsidered too.

## Evidence, retries and coverage

Generation selects exact passages using request-local labels such as `E1`.
Exports resolve them to original element revisions and spans. Saved context
headings receive full-text spans without changing input chunks or their IDs.
Headings supply scope, not standalone support for an operational rule.

Invalid responses, unknown evidence labels and duplicate candidates trigger
bounded retries with feedback. Narrow checks also catch known unresolved
subjects, beneficial-owner fallback prerequisites, a specific substantive
footnote pattern, and DS/complaint or ERMES-authentication topic mistakes.
These checks make no separate review calls and do not establish general
correctness. Exhausted retries produce errors and a `partial` stage.

Generation's `report.json` includes candidate counts by chunk and source,
uncited IDs, failed chunks, inputs without eligible evidence and unselected
inputs. **Coverage needs review** can accompany `complete`: processing finished,
but some selected material has no candidates. A citation does not establish
complete extraction. References, keywords and incomplete fragments may properly
yield no claims; substantive omissions require source review.

## Outputs and cost

| Stage | Files |
| --- | --- |
| Generation | Upstream `artifact-bundle.json`, `claim-candidates.jsonl`, `report.json`, `build-manifest.json`. |
| Classification | Normalized `artifact-bundle.json`, `chunks.jsonl`, `claims.jsonl`, topic JSON/Markdown views, `report.json`, `build-manifest.json`. |

Manifests record input lineage, output hashes, configuration and prompt digests;
classification also records the taxonomy and candidate-manifest hashes.
Reports record errors, requests, token usage, model, rate card and estimated
cost. Classification also records unclassified candidate IDs and stage duration.
The CLIs print progress, elapsed time and estimated cost to stderr; reports go
to stdout. Cost estimates are not invoice totals.

Length-stratified subsets project full generation cost from serialized request
size; classification adds a projection based on candidate yield. Explicitly
selected batches omit these projections because their sample is biased.
Actual costs depend on claim yield, tokens and retries. Missing duration fields
mean unknown runtime; do not infer runtime from file timestamps.

## Validate and review

```sh
.venv/bin/python scripts/validate_artifact_examples.py \
  artifacts/classified-claims-new/artifact-bundle.json
```

Validation checks schemas, IDs, exact evidence spans, source and build lineage,
and inverse claim/topic links. Separately review uncited chunks, unclassified
candidates, topic choices, qualifications, exceptions and source coverage against
the saved documents before scaling up.

For classification regression scoring, `scripts/evaluate_classification.py`
compares saved output with a draft reference and makes no API calls. Pass
`--candidates-dir`, `--classified-dir` and `--reference`. A reference must match
the exact candidate file and taxonomy; the bundled reference covers historical
regression cases and does not assess new documents. Generation and classification
never read it. Scoring reports agreement, critical mismatches, source locations,
hashes, cost and recorded runtime. Fixture outputs cannot pass its model-quality
gate. Use fixture provider configuration and `--fixture` for offline tests.

New claims have independent grounding and classification statuses set to
`draft`; topic views are also drafts. Optional `--review-file` records separate
`verified` or `rejected` assessments and rejection reasons. It supplies no topic
assignments. A topic can be marked verified only when all its claims are verified
in both dimensions. Automated checks do not provide legal verification.

Preserve conflicting sources and editions. `retrieved_on` is a capture date,
not evidence of legal effectiveness. Topic summaries are draft notices, not
substantive synthesis. See [the graph guide](knowledge-provenance-graph.md) to
inspect topic → claim → chunk → element → saved-source provenance.
