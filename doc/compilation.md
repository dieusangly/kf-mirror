# Topic compilation

Generate evidence-linked candidates, then classify into curated topics. Both
stages call models and need fresh output directories; chunks remain unchanged.
Claims/topic views are French drafts.

## Setup

Set up the [environment](ingestion.md). Copy `.envrc.example` to
`.envrc`, set `OPENAI_API_KEY`, and load it through your shell or `direnv`.
`.envrc` is ignored by Git.

[Configuration](../config/compilation.json) sets provider/model, reasoning, batches,
retries and pricing. Defaults: `gpt-6-luna`, `medium`, four-chunk target. Linked
paragraphs/lists, same-page numbered notes and continued table rows stay together,
even above that target. Chunks retain IDs/spans and belong to one batch.
Classification requests once per candidate, plus retries.

Assign by [topic scope](../config/topics-v1.json) and main proposition; unrelated
narratives may remain unclassified. IDs/JSON fields stay unchanged. Pipeline,
generation, classification and taxonomy versions are independent; manifests/hashes
identify their combination. Run names label folders.

## Choose run directories

From the repository root, choose a saved chunking run and fresh compilation
output directories. Keep the variables in the same shell session:

```sh
INPUT_RUN_ID="your-chunking-run-id"
RUN_ID="your-compilation-run-id"
CHUNKED_DIR="artifacts/chunked-${INPUT_RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
```

## Step 1 — Generate candidates

```sh
.venv/bin/python scripts/generate_claims.py \
  --chunked-dir "$CHUNKED_DIR" \
  --output-dir "$CANDIDATES_DIR"
```

By default, generation processes all batches. Optional arguments:

| Argument | Use |
| --- | --- |
| `--subset-batches N` | Select N batches spread by request length; source/topic coverage is not guaranteed. |
| `--batch-numbers N ...` | Select explicit one-based batches for this input/grouping. Mutually exclusive with `--subset-batches`. |
| `--resume` | Continue checkpoints with exactly the same inputs, configuration, prompt and selection. |

Recalculate batch selections after input/grouping/configuration changes.
`compilation_scope` records selected batches, counts and chunk IDs. Classification
requires a completed generation manifest.

## Step 2 — Classify saved candidates

```sh
.venv/bin/python scripts/classify_claims.py \
  --candidates-dir "$CANDIDATES_DIR" \
  --output-dir "$CLASSIFIED_DIR"
```

Classification checks the candidate manifest/hashes and assigns topics without
rewriting candidate text or evidence. To validate the bundle independently:

```sh
.venv/bin/python scripts/validate_artifact_examples.py \
  "$CLASSIFIED_DIR/artifact-bundle.json"
```

Validation checks schemas, IDs, spans, lineage and inverse links; source fidelity
and completeness require review.

## Steps 3–4 — Review and evaluate

Follow [compilation evaluation](compilation-evaluation.md) with these directories:
initialise report/sample, have a separate human or agent inspect originals, then
rerun evaluation with their review. Tests/automatic checks do not approve meaning;
a policy pass leaves claims/topics as drafts.

## Rerun after changes

| Change | Required work |
| --- | --- |
| Generation prompt or context | Regenerate from saved chunks, then classify into fresh directories. Reclassification cannot repair generation omissions or scope. |
| Taxonomy or classification only | Reuse saved candidates and classify into a fresh directory, including previously unclassified candidates. |
| Build, inputs, sample or policy | Initialise a new snapshot and perform a fresh source review; do not reuse old judgments. |
| Parsing or chunking | Rebuild the affected upstream stages before compilation. |

Preserve previous runs/reviews for comparison. Do not resume with changed inputs.
Review a targeted subset before scaling up; a subset pass does not establish
whole-corpus completeness.

## Evidence, retries and coverage

Labels such as `E1` select exact passages per request; exports resolve original
revisions/spans. Heading spans supply context, not operational support. Suggested
note links and context groups are structural hints; source review must confirm
attachment and scope.

Bounded retries catch invalid schemas, evidence labels, duplicate evidence and
uncurated topic IDs. Semantic decisions use the supplied evidence, prompts and
taxonomy; keyword rules do not reject candidates or force topic assignments.
No separate review calls occur. Exhausted retries produce errors and a `partial`
stage. Grounding, completeness and classification correctness require source review.

Generation's `report.json` counts candidates by source/chunk and lists uncited,
failed, ineligible and unselected inputs. **Coverage needs review** can accompany
`complete`: processing finished, but selected material has no candidates.
References, keywords or fragments may properly yield none; substantive omissions
need source review. Citing a passage does not establish complete extraction.

## Outputs and cost

| Stage | Files |
| --- | --- |
| Generation | Upstream `artifact-bundle.json`, `claim-candidates.jsonl`, `report.json`, `build-manifest.json`. |
| Classification | Normalized `artifact-bundle.json`, `chunks.jsonl`, `claims.jsonl`, topic JSON/Markdown views, `report.json`, `build-manifest.json`. |

Manifests record lineage, output hashes, configuration and prompt digests;
classification also pins taxonomy and candidate-manifest hashes. Both bundles
and classification's `chunks.jsonl` retain unselected upstream chunks. Use
`compilation_scope.selected_chunk_ids` to identify processed chunks.

Reports record errors, requests, tokens, model, pricing and estimated cost;
classification adds unclassified IDs and duration. CLIs print progress, elapsed
time and cost to stderr, reports to stdout. Cost estimates are not invoice totals;
missing duration means unknown runtime, not a value inferred from file timestamps.

Length-stratified subsets project full generation cost from request size and
classification cost from candidate yield. Explicit batch selections omit these
projections because their sample is biased. Actual costs depend on yield, tokens
and retries.

Preserve conflicting sources and editions. `retrieved_on` is a capture date, not
legal effectiveness. Topic summaries are draft notices, not synthesis. Use the
[provenance graph](knowledge-provenance-graph.md) to inspect source links.
