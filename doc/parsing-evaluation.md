# Parsing evaluation

`scripts/auto_review_parsing.py` audits saved parsing outputs; the
[agent prompt](../src/knowledge_foundry/prompts/agent_prompt_parsing_review) adds
independent inspection against saved originals. Both must pass before chunking.
Managed `parsing-review` performs and records the audit itself; the standalone
audit command below is optional for diagnosis.

For normalization/source-adapter repairs, a manual `parsing` step may reuse archived
Docling JSON with `--native-input-dir PATH`. Source identity and parser settings
must match; input/output directories must differ. It rebuilds elements, corrections
and figures, recording native hashes/parser versions. Conversion changes require a
full parse. Code changes still invalidate from `ingest`; all rebuilt outputs need
fresh automatic and independent review.

## Automated audit

```sh
RUN_ID="your-parsing-run-id"
PARSED_DIR="artifacts/${RUN_ID}/parsing"
PARSING_REVIEW_DIR="artifacts/${RUN_ID}/parsing-review"
direnv exec . .venv/bin/python scripts/auto_review_parsing.py \
  --input-dir "$PARSED_DIR" --source-root . \
  --output "$PARSED_DIR/parsing-evaluation-report.json"
```

The audit checks bundles, locations, evidence roles, duplicates, headings/lists,
figure assets and provenance. PDF checks compare native Docling nodes, including
substantive prose, short picture text, multi-page character spans and numeric
identifiers verified against local PDF glyphs. Counts are complete; reports show
at most five examples per finding code. Identical text at distinct source locations
is genuine repetition. Missing canonical text/assets and invalid structures are
errors; documented furniture/visual/OCR limitations may remain warnings.

Before native comparison, the audit replays the explicit source-evidence adapter
against the untouched native JSON and original PDF. Missing, changed or
unreproducible `pdf_source_adapter` evidence is an error. This includes excluded
occluded duplicates as well as recovered cells, captions, note units,
heading roles and image extents. The audit then applies the existing native,
geometry, aggregate, location, role and continuation checks to the verified
working structure. It does not accept a correction merely because its metadata
claims source support. OCR verification is local and records actual engine and
language-model hashes; regression tests mock it. Neither replay nor fixture
success supplies an independent review judgment.
Preserve uncertain OCR transcripts. Inconclusive verification requires source
review of every affected page, including when saved warnings were omitted.
A parse remains complete only while its transcript, page and native reference
remain represented. Lost/altered text, unmatched suffixes and unreproducible repairs
fail the audit. Low confidence permits neither deletion nor approval. Reports count
warnings and errors separately, even under the same finding code.

For tables carrying `table_geometry`, the audit replays the explicit Docling
adapter against the saved original's literal glyphs and painted geometry. It
compares cells, boxes, row/column spans, the aggregate, grid metadata and glyph
digest. Missing source evidence or changed cell associations fail the gate.
Tables retained by the explicit native adapter are compared with saved native
cells and aggregates, including values, column/row associations, spans and boxes.
Source-verified staggered divider aliases are replayed with their physical grid;
the audit does not accept the recorded mapping without reconstructing it.
Numeric note-marker separation is replayed against original glyphs and native
footnotes for both table adapters. Merged values, changed digits, missing marker
metadata or altered note references fail the gate. Source-located running headers
must retain structural furniture roles, including fragments of multi-page nodes.
Continuation checks replay source-grid selection and verify reciprocal links,
header spans, governing-cell scope and any mapping from coarser physical columns
to the original header grid. Each removed boundary must lie inside a source header
cell on every header row; source boxes and glyph conservation still apply.
The chunk audit requires inherited context
with its original evidence spans, excludes that context from duplicate body counts,
and still rejects unrelated table mixing or lost text. Geometry and continuation
uncertainties remain errors; these checks do not independently approve a repair.

| Option | Use |
| --- | --- |
| Omit `--output` | Print JSON to stdout. |
| `--additional-input-dir PATH` | Combine subset reruns, retaining each source's newest valid outcome. |
| `--source-root PATH` | Resolve saved originals against another root. |
| `--reference PATH` | Check matching source-verified passages, structure and reading order. |

The report contains selected run IDs, metrics, findings and `gate.status`.
Exit `0` means automatic pass; `1` means fail. Without a reference,
`reference_status: not_supplied` claims no landmark coverage. Record all input
selections when combining runs. The workflow additionally requires every parse
outcome to be `parsed`.

## Independent parsing review

After parsing, invoke the agent prompt with the run ID and exact inputs. It runs
initialization/reuse, source inspection and final evaluation as one task, and
registers managed manual results through the controller. No user-run final command
is needed. The equivalent initialization command is:

```sh
direnv exec . .venv/bin/python scripts/auto_review_parsing.py \
  --input-dir "$PARSED_DIR" --source-root . \
  --manifest data/manifest.json --review-policy config/review-policy.json \
  --required-pages tests/fixtures/parsing-expanded-review-pages.json \
  --sample-per-document 12 \
  --initialize-review "$PARSING_REVIEW_DIR/review.json" \
  --output "$PARSING_REVIEW_DIR/report.json"
```

Record reviewer model, reasoning effort and launch mode using the
[attribution rules](manual-workflow.md#1-ingest-parse-and-review).

After inspection, repeat with `--review-file` instead of `--initialize-review`,
keeping every other selection. Initialization refuses to overwrite a review and
may exit zero while pending. Read `automatic_gate`, `review_gate` and `gate`;
`--review-file` exits nonzero unless the overall gate passes.

Automated checks cover all documents. Visual inspection covers every selected PDF
page (all its elements and possible omissions), and complete HTML articles.
Selection includes all table/warning pages, configured mandatory pages and spread
samples; 12 is a target, not a cap. Unlocated warnings select the whole document.
The [mandatory locations](../tests/fixtures/parsing-expanded-review-pages.json)
retain 30 source-confirmed pages after warnings disappear. They select inspection
evidence, not approval judgments. Reports distinguish selected, inspected and total units.

Approval binds sources, artifacts, audit, selection, policy and review-tool code.
Missing inspection stays pending; off-sample findings block acceptance. Thresholds:
80% pass rate, weighted score ≥90 and zero critical failures, with all samples
assessed. The [workflow](automated-workflow.md) verifies prior-session evidence
before carrying unchanged judgments forward; revised judgments need reinspection.

## Limits

Neither automated nor sampled review proves completeness or legal correctness.
The audit cannot detect everything Docling missed or interpret diagram meaning;
PDF-specific native checks currently depend on Docling. Another parser needs an
adapter and equivalent checks, not silent omission of native validation. HTML uses
DOM checks; Markdown/DOCX replay native line/block/cell locations without PDF models.
Fixtures test mechanics and never approve the pilot. Reports are ignored by Git.
