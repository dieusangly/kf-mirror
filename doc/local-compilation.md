# Local topic compilation

Compilation consumes an existing reviewed Task 5 chunk bundle. It never parses,
rechunks, or changes the input directory. It writes a new directory and refuses
to overwrite one.

## Setup

Copy `.envrc.example` to `.envrc`, replace its placeholder with the local
`OPENAI_API_KEY`, and load it with your shell or `direnv`. `.envrc` is ignored;
do not commit it. The default provider is the OpenAI Responses API and the
default model is `gpt-6.1-sol` with `medium` reasoning effort. Both values are
configured in `config/compilation.json` and are recorded through its build
configuration digest.

Curate stable topic IDs in `config/topics-v1.json` before compiling. IDs must
remain stable across builds; title or scope changes are taxonomy revisions, not
new generated knowledge.

The pilot uses French prompts and requires French claim text, topic labels, and
draft views. Stable topic IDs and JSON field names remain unchanged. Claims must
preserve the source's terminology and qualifications; a translation or
paraphrase must never be presented as a verbatim citation.

## Run

Choose a fresh output directory:

```sh
.venv/bin/python scripts/compile_topics.py \
  --chunked-dir artifacts/chunked-20261013-114244 \
  --output-dir artifacts/compiled-20261013-114244
```

The compiler batches saved chunks, asks the model for atomic candidates, then
classifies each candidate separately. A candidate can cite several chunks, but
each citation must select an existing chunk evidence reference with an exact
element span. Bounded retries apply to each model request. The output reports
failed batches and unclassified candidates without assigning them to a topic.

For a deterministic test run, set `provider` to `fixture` in a copied
compilation config and supply a fixture response file with `--fixture`. Tests
use this mode; CI has no live model dependency.

## Output and review

The output directory contains:

- `artifact-bundle.json`: normalized source through topic records and separate
  upstream chunk and compilation build records.
- `chunks.jsonl` and `claims.jsonl`: collection records suitable for streaming.
- `topics/<topic-id>.json` and `.md`: draft inspection views.
- `build-manifest.json`: input, taxonomy, prompt, configuration, and export
  hashes; `report.json`: counts, provider failures, and unclassified candidates.

All generated claims begin with both review statuses set to `draft`; topic views
are also `draft`. A review file passed with `--review-file` may independently
set each claim's grounding and classification status to `verified` or
`rejected`, with an optional rejection reason preserved in `report.json`. A
topic cannot become verified unless every assigned claim is verified in both
dimensions.

Topic grouping never resolves inconsistent editions or sources. Claim text must
retain stated conditions, exceptions, scope, and effective-date uncertainty. A
capture date (`retrieved_on`) is never evidence that a legal rule is effective.
The topic summary is deliberately a non-substantive draft notice, not a legal
interpretation or verified synthesis.

## Validation

Compilation calls the artifact validator before writing. Independently validate
the normalized bundle with:

```sh
.venv/bin/python scripts/validate_artifact_examples.py \
  artifacts/compiled-20261013-114244/artifact-bundle.json
```

The validator checks schemas, IDs, exact evidence spans, source/document links,
upstream chunk lineage, inverse claim/topic links, and build outputs.
