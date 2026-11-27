# Stage A1 regression baseline

**27 November 2026 · `940d65209827c1032f15d789189cb12cdd5fd5c7` · pre-migration checkout.** The integrated offline suite passed **1,020 tests and 139 subtests**, with **five skipped and zero failures**. All **242/242** accepted symbolic results reproduced exactly and passed proof checking. Use both measures as migration gates. The independent live Assistant evaluation has its own retained failure decision.

## Measured runs

| Check | Result | Inputs and scope |
| --- | --- | --- |
| Full pytest, default environment without a test DSN or OCR opt-in | Exit 0. 971 passed, 139 subtests passed, 54 skipped, one warning, 215.66 s | 48 skips required PostgreSQL/pgvector, one required OCR opt-in and five requested an unavailable dated pilot. |
| Full pytest, isolated PostgreSQL and offline OCR enabled | Exit 0. 1,020 passed, 139 subtests passed, five skipped, three warnings, 299.44 s | Same suite and revision. Includes real transactional storage, retrieval, document pipeline CLI integration, contributor auth, Streamlit and Node viewer tests. Test and subtest counts follow the pytest terminal summary. |
| PostgreSQL/pgvector subset | 166 passed in 42.83 s | `tests/retrieval` and `tests/storage` against a disposable PostgreSQL 17 cluster with a private Unix socket. The integrated run also covered the DB-backed command test outside those two directories. |
| OCR opt-in | One passed. Character and word error rates both 0.0000 | Cached Docling models, local Tesseract and French/English language data on the synthetic raster fixture. The integrated warnings concern deprecated list parenting, OCR mode and table-image options. |
| Registry and accepted replay | 121 semantic cases, two languages each, zero mismatches, zero external calls | Verified all path/digest entries in each acceptance pin map, in addition to the replay's prerequisite, query, result and certificate checks. See the package breakdown below. |
| Frozen evaluation preparation | 8/8 checks passed, 288 prompts, 192 engine-checked provisional references | `01-dataset-run-007`. Includes source hashes, deterministic reproduction, proof categories, FR/EN symbolic agreement, contrast checks and document passage integrity. Offline inventory confirmed all 288 question identities. |
| Mocked runtime smoke | 7/7 attempts completed, 7 valid proof checks, zero real-model calls | The development fixtures exercised `Assistant.ask` with controlled models and embeddings. The 288 evaluation prompts were excluded from the smoke. |
| Source manifest and examples | 13 source editions verified. PDF and HTML example bundles both passed. | Draft 2020-12 schema, record links, IDs and original-byte checks through the example validator. |
| CLI startup | 24/25 passed under isolated Python. The formalizer alone exited 1 with `ModuleNotFoundError: aml_cft_reasoning`. Its documented direct invocation passed. | Tried `--help` for 24 commands and both default examples for the positional artifact validator. `python -I` removes the script-directory lookup required by the formalizer’s bare sibling import. This limitation predates migration. |
| Application starts | Assistant initial AppTest rendered its title and chat input with model key and DB DSN unset. Contributors returned HTTP 200 for the root, session, JS and CSS on a fresh temporary SQLite store. The KPG CLI emitted standalone HTML with six nodes and six edges from the PDF example. | The existing contributor DB passed read-only SQLite `quick_check`. Schema inspection found the five app tables and the `active_contributions` view. The test suite separately exercised authenticated flows and legacy ownership. |
| Python environment | `pip check` reported no broken requirements. | Python 3.12.13, pytest 9.1.1, Docling 2.135.0, Unstructured 0.27.35, Haystack 3.4.0, psycopg 3.3.6, pgvector Python 0.5.1, Streamlit 1.66.0, Node 20.20.2. PostgreSQL server 17.12 with vector extension 0.8.0 available locally. |

Subset and smoke results overlap the full suite. The repository discovers CLI tests from documentation. At the audited revision it collected 35 documented-command cases. After adding the A1 documents, the targeted command check passed all 41 cases.

### Accepted symbolic packages

| Package | Semantic cases | FR + EN versions | Matching records with valid proofs | Receipt pin entries verified |
| --- | ---: | ---: | ---: | ---: |
| `synthetic-v1` | 96 | 192 | 192 | 32 |
| `real-claims-bo-v1` | 9 | 18 | 18 | 23 |
| `real-claims-kyc-v1` | 16 | 32 | 32 | 18 |

The registry SHA-256 was `6a9c201033f614a0bd058dc0e20faa89dbe2e66245328f53399c4f184ecd7bfb`. The routing fixture digest was `e01b49cde86e5dc662244d23140796be316fc12d89be7c1d662c7c98b79d8f75`. The replay checks complete result records by case and language against each package's accepted queries/results. Recall, routing, interpretation and wording metrics remain unmeasured by this command. [Replay implementation](../scripts/replay_reasoning_pilot.py) · [Acceptance paths and hashes](../config/aml-cft-reasoning-datasets.json)

### Saved document runs

Both validators saw 13 originals, including 11 PDFs with 801 pages. They checked exact input editions, frozen structure, original coverage, three critical scope controls and the 123 development anchors.

| Run | Elements | Chunks / structural chunks | Covered authoritative elements | Anchors / scope | Existing structure acceptance |
| --- | ---: | ---: | ---: | --- | --- |
| `20261028-docling-aml-cft-01` | 14,677 | 4,514 / 4,513 | 9,229/9,229 | 123/123 · 3/3 | Present |
| `20261027-unstructured-aml-cft-01` | 12,782 | 4,846 / 4,838 | 11,570/11,570 | 123/123 · 3/3 | Absent |

### Gaps and historical failures

- **Five skipped checks.** One pilot validation and four chunk-audit checks hard-code `artifacts/20261027-docling-aml-cft-01`. That run was retired. The four cover unsupported text/missing coverage, figure links, broken references and a missing build. The retained October 28 run passed its direct validation. Substituting it under the retired name would obscure which bytes were tested. [Pilot test](../tests/test_aml_cft_pilot.py) · [Chunk-audit tests](../tests/test_chunking_audit.py) · [Retirement record](../artifacts/20261028-docling-aml-cft-01/inspection/baseline-retirement.json)
- **Claim review.** The retained Docling claim topics scored 84.76 against a floor of 90. A bound exception permits retention and defers regeneration. The failed gate and unresolved findings still apply to downstream publication decisions. [Exception record](../artifacts/20261028-docling-aml-cft-01/claim-review/acceptance-exception.json)
- **Assistant quality.** The November 18 independent verdict was `completed-with-failures` for dataset 005, live run 004 and review 003. Overall correctness was 264/288. Routing scored 80/96 against 82 required. Construction answer/status/proof fidelity was 191/192 and full response fidelity was 190/192. Both repair rounds were exhausted. Dataset 007 is the current offline preparation, with 36 verified source pins and manifest SHA-256 `56324e87eddef552a09be035486f2418802f2b2b0a0b6f0e562c8874538a111d`. It carries no new live verdict. [Final evaluation and scores](aml-cft-reasoning-assistant-evaluation.md)
- **Historical retrieval benchmark.** The retained 123-question development evaluation recorded 1,476 results across 12 configurations. Combined passage-and-claim had 112/123 anchor hits and 110/123 completely grounded cases. This historical reranked benchmark used a particular published snapshot and live provider. A1 exercised real retrieval on a disposable fixture database. A fresh production-snapshot or model-quality measurement would require the matching published snapshot and provider configuration. [Retrieval report](retrieval-evaluation-123q-20261103.md)
- **Portability and local state.** A clean clone omits accepted artifact trees, DB dumps, the SQLite store and model/token caches. This checkout supplied the three reasoning packages, both retained document runs, the 007 evaluation and the cached models. A production DSN, live API credentials and a paid budget were outside the offline run. Existing tests can write ignored local state. In particular, the HTML parsing test refreshed `artifacts/ingested/report.json` and the manifest-selected snapshot. Both yielded SHA-256 `a6c1cceb576a077553dbd6bf976778b81683e3dfb65b8a4ffa7b0fb5865e7c94` and the expected ingestion identity. The four crop-cache entries created by the PDF checks were moved to the temporary audit directory. Use a disposable checkout and artifact copy when original timestamps or cache contents matter. [Parsing test](../tests/test_parsing.py) · [Local artifact layout](../artifacts/README.md)

## Reproduction

Run from the repository root at the recorded revision with local artifacts available. Write reports to a fresh directory. The environment below uses installed dependencies and cached models and disables provider credentials and telemetry.

```sh
A1_OUT=$(mktemp -d)
unset OPENAI_API_KEY KF_TEST_DATABASE_URL KF_TEST_OCR
export PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HAYSTACK_TELEMETRY_ENABLED=false

.venv/bin/python -m pip check
.venv/bin/python -m pytest -p no:cacheprovider -q -ra --tb=short --junitxml="$A1_OUT/default.xml" tests
.venv/bin/python scripts/replay_reasoning_pilot.py --output "$A1_OUT/replay.json"
.venv/bin/python -I scripts/validate_artifact_examples.py
.venv/bin/python scripts/validate_pilot.py --run-dir artifacts/20261028-docling-aml-cft-01 --output "$A1_OUT/docling.json"
.venv/bin/python scripts/validate_pilot.py --run-dir artifacts/20261027-unstructured-aml-cft-01 --output "$A1_OUT/unstructured.json"
.venv/bin/python scripts/validate_reasoning_assistant_evaluation.py --output "$A1_OUT/evaluation.json"
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --output "$A1_OUT/inventory"
.venv/bin/python tests/record_reasoning_input_smoke.py --output "$A1_OUT/smoke"
.venv/bin/python scripts/render_knowledge_graph.py --bundle examples/artifact-bundle-pdf.json --output "$A1_OUT/graph.html"
```

For the integrated suite, use the same environment and a dedicated PostgreSQL cluster with the vector extension installed. The following Linux commands reproduce the private-socket setup used here. The test fixture creates a disposable schema per test. The cluster shuts down on exit.

```sh
(
  set -e
  PG17_BIN=/usr/lib/postgresql/17/bin
  mkdir -m 700 "$A1_OUT/socket"
  "$PG17_BIN/initdb" -D "$A1_OUT/pgdata" --locale=C.UTF-8 -E UTF8 -A trust
  "$PG17_BIN/pg_ctl" -D "$A1_OUT/pgdata" -l "$A1_OUT/pg.log" \
    -o "-k $A1_OUT/socket -c listen_addresses='' -p 55437" -w start
  trap '"$PG17_BIN/pg_ctl" -D "$A1_OUT/pgdata" -m fast -w stop' EXIT
  export KF_TEST_DATABASE_URL="host=$A1_OUT/socket port=55437 dbname=postgres"
  export KF_TEST_OCR=1 OMP_NUM_THREADS=2
  .venv/bin/python -m pytest -p no:cacheprovider -q -ra --tb=short --junitxml="$A1_OUT/integrated.xml" tests
)
```

To verify all direct receipt entries, run the following with the same `PYTHONPATH`. This includes pinned evidence beyond the files that the runtime opens on an ordinary request.

```python
from knowledge_foundry.assistant.datasets import DatasetRegistry, digest, project_path

for package_id, package in DatasetRegistry().packages.items():
    pins = package["artifact_hashes"]
    for name, expected in pins.items():
        assert digest(project_path(name)) == expected, (package_id, name)
    print(package_id, len(pins))
```
