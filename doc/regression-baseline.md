# Offline regression checks

Run from the repository root in a disposable Git checkout with copies of the accepted artifacts and cached models. Workflow checkpoints and evaluation preparations record Git and file identities. Some tests write ingestion reports and image caches. Use a private PostgreSQL database with pgvector for integrated testing and a copy of the Contributors SQLite database for application checks.

## What to verify

| Check | Expected evidence |
| --- | --- |
| Document inputs | The manifest verifies 13 source editions. Both PDF and HTML example bundles pass schema and cross-record validation. |
| Retained document runs | Docling has 4,514 chunks and accepted structure. Unstructured has 4,846 chunks. Both cover the 123 development anchors and three scope controls. |
| Reasoning replay | Each of the 242 accepted French and English case versions reproduces its result and passes certificate checking. The synthetic package contains 192 versions, beneficial ownership 18 and KYC 32. |
| Assistant preparation | The frozen 007 dataset passes eight offline validation checks for 288 ordered questions, including 192 engine-checked provisional references. The inventory verifies the question identities. The separate development smoke exercises seven mocked runtime cases. |
| Integrated tests | Compare test and subtest identities, skip reasons and warnings. The accepted checkout passed 1,037 tests and 139 subtests. Five tests reference the retired October 27 Docling run and skip when it is absent. Three Docling deprecation warnings were recorded. |

The retained Docling claim review has a bound exception to its quality threshold. The independent live Assistant review recorded 264/288 and a verdict of `completed-with-failures`. Offline replay and preparation checks cover different inputs and outcomes from that live review. [Claim review exception](../artifacts/20261028-docling-aml-cft-01/claim-review/acceptance-exception.json) · [Assistant evaluation](aml-cft-reasoning-assistant-evaluation.md) · [Archived review evidence](archive/README.md)

## Reproduction

Use the repository's Python environment with cached model and token resources. Write reports to a fresh temporary directory. The default suite skips checks that need PostgreSQL or OCR opt-in.

```sh
CHECK_OUT=$(mktemp -d)
unset OPENAI_API_KEY KF_DATABASE_URL KF_TEST_DATABASE_URL KF_TEST_OCR
export PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HAYSTACK_TELEMETRY_ENABLED=false

.venv/bin/python -m pip check
.venv/bin/python -m pytest -p no:cacheprovider -q -ra --tb=short --junitxml="$CHECK_OUT/default.xml" tests
.venv/bin/python scripts/replay_reasoning_pilot.py --output "$CHECK_OUT/replay.json"
.venv/bin/python -I scripts/validate_artifact_examples.py
.venv/bin/python scripts/validate_pilot.py --run-dir artifacts/20261028-docling-aml-cft-01 --output "$CHECK_OUT/docling.json"
.venv/bin/python scripts/validate_pilot.py --run-dir artifacts/20261027-unstructured-aml-cft-01 --output "$CHECK_OUT/unstructured.json"
.venv/bin/python scripts/validate_reasoning_assistant_evaluation.py --output "$CHECK_OUT/evaluation.json"
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --output "$CHECK_OUT/inventory"
.venv/bin/python tests/record_reasoning_input_smoke.py --output "$CHECK_OUT/smoke"
.venv/bin/python scripts/render_knowledge_graph.py --bundle examples/artifact-bundle-pdf.json --output "$CHECK_OUT/graph.html"
```

Run the integrated suite in the same shell. This example uses PostgreSQL 17 on a private Unix socket, the pgvector extension, cached Docling models, and Tesseract with English and French language data.

```sh
(
  set -e
  PG17_BIN=/usr/lib/postgresql/17/bin
  mkdir -m 700 "$CHECK_OUT/socket"
  "$PG17_BIN/initdb" -D "$CHECK_OUT/pgdata" --locale=C.UTF-8 -E UTF8 -A trust
  "$PG17_BIN/pg_ctl" -D "$CHECK_OUT/pgdata" -l "$CHECK_OUT/pg.log" \
    -o "-k $CHECK_OUT/socket -c listen_addresses='' -p 55437" -w start
  trap '"$PG17_BIN/pg_ctl" -D "$CHECK_OUT/pgdata" -m fast -w stop' EXIT
  export KF_TEST_DATABASE_URL="host=$CHECK_OUT/socket port=55437 dbname=postgres"
  export KF_TEST_OCR=1 OMP_NUM_THREADS=2
  .venv/bin/python -m pytest -p no:cacheprovider -q -ra --tb=short --junitxml="$CHECK_OUT/integrated.xml" tests
)
```

The workflow package also exposes offline preparation, validation, inventory and replay as Python modules. With the same environment, use fresh paths for each output.

```sh
.venv/bin/python -m knowledge_foundry.workflows.reasoning_pilot.replay --output "$CHECK_OUT/package-replay.json"
.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.preparation --output "$CHECK_OUT/preparation"
.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.validation --dataset "$CHECK_OUT/preparation" --output "$CHECK_OUT/preparation-validation.json"
.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.execution --dataset "$CHECK_OUT/preparation" --output "$CHECK_OUT/preparation-inventory"
```

To verify every recorded receipt entry, run this Python snippet with the same `PYTHONPATH`. The three packages contain 73 entries in total.

```python
from knowledge_foundry.assistant.datasets import DatasetRegistry, digest, project_path

for package_id, package in DatasetRegistry().packages.items():
    pins = package["artifact_hashes"]
    for name, expected in pins.items():
        assert digest(project_path(name)) == expected, (package_id, name)
    print(package_id, len(pins))
```
