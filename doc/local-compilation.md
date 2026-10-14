# Local topic compilation

Compilation consumes an existing reviewed chunk bundle. It never parses,
rechunks, or changes the input directory. It writes a new directory and refuses
to overwrite one.

## Setup

Copy `.envrc.example` to `.envrc`, replace its placeholder with the local
`OPENAI_API_KEY`, and load it with your shell or `direnv`. `.envrc` is ignored;
do not commit it. The default provider is the OpenAI Responses API and the
default model is `gpt-6-luna` with `medium` reasoning effort. Both values are
configured in `config/compilation.json` and are recorded through its build
configuration digest.

The configuration's versioned rate catalogue contains entries for the available
models. When changing `model`, select an existing entry or add its published
rate card; do not replace historical rates. Each report records the model and
specific rate card used. CLI costs are estimates derived from API-reported token
usage and that card, not invoice totals.

`batch_size_chunks` is the number of saved chunks sent in one claim-generation
request (the pilot value is `4`). Topic classification remains one request per
candidate claim.

Curate stable topic IDs in `config/topics-v1.json` before compiling. IDs must
remain stable across builds; title or scope changes are taxonomy revisions, not
new generated knowledge.

The pilot uses French prompts and requires French claim text, topic labels, and
draft views. Stable topic IDs and JSON field names remain unchanged. Claims must
preserve the source's terminology and qualifications; a translation or
paraphrase must never be presented as a verbatim citation.

## Generate candidate claims

Generate durable candidate claims in a fresh directory. This stage makes only
claim-generation calls; it does not need the topic taxonomy.

```sh
.venv/bin/python scripts/generate_claims.py \
  --chunked-dir artifacts/chunked-20261013-114244 \
  --output-dir artifacts/generated-claims-20261013-114244
```

To generate claims from a length-stratified subset of the original batches, add
`--subset-batches` to the same command shape:

```sh
.venv/bin/python scripts/generate_claims.py \
  --chunked-dir artifacts/chunked-20261013-114244 \
  --output-dir artifacts/generated-claims-20261013-114244 \
  --subset-batches 10
```

The subset run selects ten original generation batches (up to forty chunks), spread
across the cumulative character volume of the exact serialized generation
requests. It makes real model calls and preserves all input chunk records for
lineage. Its manifest and report identify it as a subset run and its generation
build ID differs from a full generation run.

The generated directory contains `claim-candidates.jsonl`. Every candidate has
resolved, exact evidence spans and generation metadata, including candidates
that may later receive no topic. Do not alter it; it is the immutable input to
classification.

Generation persists a checkpoint after every completed batch. If a process or
final export stops, rerun the exact command with the same output directory and
`--resume`; completed batches are not sent to the model again. A resumed run
rejects different input, configuration, prompt, or subset scope. Until final
validation writes `build-manifest.json`, the directory is incomplete and cannot
be classified.

## Classify saved candidates

Classify candidates in a separate fresh directory:

```sh
.venv/bin/python scripts/classify_claims.py \
  --candidates-dir artifacts/generated-claims-20261013-114244 \
  --output-dir artifacts/classified-claims-20261013-114244
```

This stage makes classification calls only. It reads the candidate manifest and
hashes before use, then validates the final normalized bundle. To revise the
curated topics, update `config/topics-v1.json` and rerun this command against
the same candidate directory. This includes candidates previously returned as
unclassified; it never regenerates their text or evidence.

Both commands print coarse progress, elapsed seconds, and their OpenAI cost
estimate to stderr. Their JSON reports remain on stdout. The classification
report additionally combines a subset-generation projection with observed
classification cost when its candidate input came from a subset run.

A subset generation run reports projected full generation cost from observed
usage and the known serialized-request character volume of all batches. After
classification, the classification report adds its projected classification
cost. These are estimates: claim yield, output/reasoning tokens, and
token-to-character ratios can vary. Use more subset batches for a more
representative estimate; do not multiply a short run's cost by the number of
chunks.

For a deterministic test run, set `provider` to `fixture` in a copied
compilation config and supply a fixture response file with `--fixture`. Tests
use this mode; CI has no live model dependency.

## Output and review

The generation directory contains:

- `artifact-bundle.json`: source, document, element, and chunk context plus a
  generation build record; it has no classified claims or topics.
- `claim-candidates.jsonl`: durable atomic candidate records with exact evidence.
- `report.json` and `build-manifest.json`: source lineage, hashes,
  usage, cost, failures, and subset scope.

The classification directory contains:

- `artifact-bundle.json`: normalized source through topic records and separate
  upstream chunk and compilation build records.
- `chunks.jsonl` and `claims.jsonl`: collection records suitable for streaming.
- `topics/<topic-id>.json` and `.md`: draft inspection views.
- `build-manifest.json`: candidate input, taxonomy, prompt, configuration, and
  export hashes; `report.json`: usage, estimated cost, provider failures, and
  unclassified candidate IDs.

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
  artifacts/classified-claims-20261013-114244/artifact-bundle.json
```

The validator checks schemas, IDs, exact evidence spans, source/document links,
upstream chunk lineage, inverse claim/topic links, and build outputs.
