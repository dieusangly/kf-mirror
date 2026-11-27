# Stage A2 completion and A3 handoff

**Implementation complete. Independent acceptance belongs to A3.** All seven approved migration steps have implementation and verification records. This is a working-tree candidate on audit commit `d3c5df3d8c151ee612ce9a8f6ceb5c12ad41a6a2`. The historical baseline is `940d65209827c1032f15d789189cb12cdd5fd5c7`. The [progress record](repository-migration-progress.md) describes file ownership, compatibility decisions, preserved pins, corrections and remaining historical limitations.

| Integrated gate | Passed tests | Passed subtests | Inherited skips | Pytest time |
| --- | ---: | ---: | ---: | ---: |
| A1 | 1,020 | 139 | 5 | — |
| A2.1 | 1,029 | 139 | 5 | 280.79 s |
| A2.2 | 1,029 | 139 | 5 | 313.24 s |
| A2.3 | 1,029 | 139 | 5 | 337.01 s |
| A2.4 | 1,029 | 139 | 5 | 314.42 s |
| A2.5 | 1,029 | 139 | 5 | 368.31 s |
| A2.6 | 1,029 | 139 | 5 | 304.70 s |
| A2.7 | 1,037 | 139 | 5 | 339.22 s |
| After package-root cleanup | 1,037 | 139 | 5 | 323.27 s |

All six remaining steps passed the Git-backed suite with PostgreSQL/pgvector and offline OCR. Every A2.1 identity and outcome was retained. A2.1 accounted for nine additions and four renumbered documentation cases relative to A1. A2.7 added eight workflow invalidation cases. The five skips require the retired October 27 Docling run. Direct validation of both retained document runs passed. Three inherited Docling warnings persisted.

| Final gate | Result |
| --- | --- |
| Symbolic engine | 242/242 exact accepted results with valid certificates. Both engine interfaces agreed on all 192 synthetic, 18 BO and 32 KYC versions. |
| Assistant evaluation | 8/8 offline checks, 288 ordered question identities and 192 engine-checked provisional references. Five fresh dataset payloads and both offline inventories matched 007. Seven mocked Assistant responses matched at field level with fixed UUIDs and latency excluded. |
| Applications and CLI | 24 isolated launchers, three historical module entrypoints, direct formalizer verification, both Streamlit modes, Contributors against a SQLite backup and standalone KPG navigation passed. The formalizer retains its documented isolated-mode sibling-import limitation. |
| Storage and integrity | Both SQL migrations retained their checksums and timestamps on a restored database after two migrations. Verified 73 receipt entries, six receipt/status pins, both frozen executables, 36 preparation source pins, five evaluation outputs, 205 review inputs, 90 historical records and 41,474 original artifact paths and sizes. All 6,042 files in the two retained document runs, reasoning pilot and Assistant evaluation history matched the A2.1 copy by path and SHA-256. |
| Boundaries and workflow | The three engineering cycles are removed. The remaining static cycle belongs to frozen evaluation v1. The checked engine ran from a minimal directory, online Retrieval imported without engineering readers, stale resume was rejected and fresh same-code resume passed. |

The fixture comparison found that historical classification IDs incorporate the generation manifest, which hashes a report containing elapsed time. Independent wall-clock runs produced different downstream IDs on that basis. Running A2.1 and A2.7 against the same inputs with the same fixture clock reproduced candidate and claim IDs, topic membership, recursive summary ancestry, completeness reports and build manifests byte for byte. [Fixture comparison](../artifacts/repository-migration-a2/A27/fixture-comparison.log)

The recorded live Assistant verdict remains `completed-with-failures` at 264/288. The retained claim-review exception still applies. Verification used local fixtures and disposable database copies with zero paid calls. B1 owns Reasoning Foundry.

### Reproduction and rollback

The [local evidence archive](../artifacts/repository-migration-a2/) holds one patch per step and the identity reports. In a disposable checkout of the audit commit, apply A2.1 through A2.7 in order, followed by the [package-root cleanup patch](../artifacts/repository-migration-a2/compat-cleanup.patch). For rollback, reverse the cleanup first, then reverse the numbered patches in descending order. The archive is ignored local output and must travel with the candidate if a reviewer uses another machine. The [baseline reproduction instructions](regression-baseline.md#reproduction) specify services, model caches, source artifacts and environment. Preserve Git metadata for both workflow and evaluation checks.

From the root of that disposable checkout, run the new offline entrypoints alongside the historical baseline commands.

```sh
A3_OUT=$(mktemp -d)
export PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1
unset OPENAI_API_KEY KF_DATABASE_URL
.venv/bin/python -m knowledge_foundry.workflows.reasoning_pilot.replay --output "$A3_OUT/replay.json"
.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.preparation --output "$A3_OUT/preparation"
.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.validation --dataset "$A3_OUT/preparation" --output "$A3_OUT/validation.json"
.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.execution --dataset "$A3_OUT/preparation" --output "$A3_OUT/inventory"
```

### Handoff prompt for A3

> Independently audit the Stage A2 working-tree candidate against A1 and approved A2.1. Read the architecture, file migration map, migration plan, regression baseline and per-step progress. Work in a Git-backed disposable copy with the accepted ignored artifacts, cached OCR resources, a private PostgreSQL/pgvector cluster and a SQLite backup. Re-run the integrated suite, compare test and subtest identities and skip reasons, replay all 242 cases through both engine interfaces, verify the complete pin sets and revalidate 007 and the versioned preparation. Verify that the five root Python files include the four frozen review modules. Check all 29 historical names in both import orders and compare the module entry points against A1. Exercise the checkout CLI, all three application surfaces, dependency boundaries and workflow resume. Report actual results, blockers, historical exceptions and an independent acceptance decision. Keep historical hashes, schemas, stored data and live verdicts intact. Leave paid calls, production databases and migration step 8 outside A3.
