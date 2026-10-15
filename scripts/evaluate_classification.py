"""Compare saved classification with an evaluation-only, draft reference. No API calls."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.claim_compilation import (  # noqa: E402
    _candidate_manifest, _validate_candidate, canonical, file_digest,
)
from scripts.validate_artifact_examples import check  # noqa: E402


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(reference, actual):
    """Compare every reference case, keeping null and failed classification distinct."""
    cases = reference["cases"]
    ids = [case["candidate_id"] for case in cases]
    if len(ids) != len(set(ids)) or not ids or set(ids) != set(actual):
        raise ValueError("reference must contain each candidate exactly once")
    allowed_topics = {None, *reference["topic_ids"]}
    rows, confusion = [], Counter()
    for case in cases:
        allowed = case["acceptable_topic_ids"]
        if not allowed or any(topic not in allowed_topics for topic in allowed) or \
                len(set(allowed)) != len(allowed) or not isinstance(case["critical"], bool) or not case["reason"]:
            raise ValueError("reference case needs valid expected topics, a critical flag and a reason")
        prediction = actual[case["candidate_id"]]
        if prediction not in allowed_topics | {"classification_error"}:
            raise ValueError("classification contains a topic outside the reference taxonomy")
        match = prediction in allowed
        rows.append({**case, "actual_topic_id": prediction, "match": match})
        expected = " | ".join("unclassified" if t is None else t for t in allowed)
        confusion[(expected, "unclassified" if prediction is None else prediction)] += 1
    correct = sum(row["match"] for row in rows)
    critical = [row for row in rows if row["critical"]]
    return {"candidates": len(rows), "correct": correct, "agreement": correct / len(rows),
            "critical_cases": len(critical), "critical_correct": sum(row["match"] for row in critical),
            "ambiguous_reference_cases": sum(len(row["acceptable_topic_ids"]) > 1 for row in rows),
            "unclassified": sum(value is None for value in actual.values()),
            "mismatches": [row for row in rows if not row["match"]],
            "confusion": [{"expected": a, "actual": b, "count": count}
                          for (a, b), count in sorted(confusion.items())]}


def evaluate(candidates_dir, classified_dir, reference_path):
    candidates_dir, classified_dir = Path(candidates_dir), Path(classified_dir)
    reference = _read(reference_path)
    if reference.get("reference_version") != 1 or reference.get("purpose") != "evaluation_only" or \
            reference.get("review_status") != "draft":
        raise ValueError("expected a version 1 evaluation-only draft reference")
    candidate_manifest, candidate_path, candidate_manifest_path = _candidate_manifest(candidates_dir)
    if file_digest(candidate_path) != reference["candidate_sha256"]:
        raise ValueError("reference does not describe this exact saved candidate file")
    manifest, report = _read(classified_dir / "build-manifest.json"), _read(classified_dir / "report.json")
    if manifest.get("stage") != "topic-classification" or \
            manifest.get("candidate_manifest_sha256") != file_digest(candidate_manifest_path) or \
            report.get("upstream_generation_build_id") != candidate_manifest["generation_build_id"]:
        raise ValueError("classification lineage does not match the candidate input")
    if report.get("topic_assignments", {}).get("input") or manifest.get("topic_assignments"):
        raise ValueError("override-assisted classification cannot be used for this evaluation")
    for item in manifest["outputs"]:
        path = classified_dir / item["path"]
        if not path.resolve().is_relative_to(classified_dir.resolve()) or file_digest(path) != item["sha256"]:
            raise ValueError("classification output hash mismatch")
    if not {"artifact-bundle.json", "report.json"} <= {item["path"] for item in manifest["outputs"]}:
        raise ValueError("classification manifest must hash the bundle and report")
    bundle, upstream = _read(classified_dir / "artifact-bundle.json"), _read(candidates_dir / "artifact-bundle.json")
    check(bundle)
    for collection in ("sources", "documents", "elements", "chunks"):
        if bundle[collection] != upstream[collection]:
            raise ValueError("classification changed upstream records")
    candidates = [json.loads(line) for line in candidate_path.read_text(encoding="utf-8").splitlines()]
    chunks, elements = ({c["id"]: c for c in bundle[name]} for name in ("chunks", "elements"))
    for candidate in candidates:
        _validate_candidate(candidate, candidate_manifest["generation_build_id"], chunks, elements)
    by_id = {c["id"]: c for c in candidates}
    for case in reference["cases"]:
        if case["candidate_id"] not in by_id or case["text"] != by_id[case["candidate_id"]]["text"]:
            raise ValueError("reference candidate ID or text mismatch")

    def key(candidate):
        return canonical([candidate["text"], candidate["evidence"], candidate["chunk_ids"]])

    candidate_keys = {key(c): c["id"] for c in candidates}
    if len(candidate_keys) != len(candidates):
        raise ValueError("candidate input contains duplicates")
    actual = {}
    for claim in bundle["claims"]:
        candidate_id = candidate_keys.get(key(claim))
        if candidate_id is None or candidate_id in actual:
            raise ValueError("claim text, evidence or chunks changed, or claim is duplicated")
        actual[candidate_id] = claim["topic_id"]
    for candidate_id in report["unclassified_candidate_ids"]:
        if candidate_id in actual or candidate_id not in by_id:
            raise ValueError("invalid unclassified candidate accounting")
        actual[candidate_id] = None
    for error in report["errors"]:
        candidate_id = error.get("candidate_id")
        if candidate_id in actual or candidate_id not in by_id:
            raise ValueError("invalid failed candidate accounting")
        actual[candidate_id] = "classification_error"
    result = compare(reference, actual)
    criteria = reference["criteria"]
    checks = {
        "agreement": result["agreement"] >= criteria["minimum_agreement"],
        "critical_cases": result["critical_correct"] == result["critical_cases"],
        "stage_complete": report["status"] == "complete" and
                          len(report["errors"]) <= criteria["maximum_stage_errors"],
        "model_only": all(c["classification"]["method"] == "model" for c in bundle["claims"]),
        "live_model_output": report["telemetry"]["provider"] == "openai",
        "draft_outputs": all(c["grounding_review_status"] == c["classification_review_status"] == "draft"
                             for c in bundle["claims"]) and all(t["review_status"] == "draft" for t in bundle["topics"]),
        "taxonomy_matches_reference": manifest["taxonomy_sha256"] == reference["taxonomy_sha256"],
    }
    return {"evaluation_version": 1, "gate": "pass" if all(checks.values()) else "fail",
            "checks": checks, "criteria": criteria, "integrity": "pass",
            "reference_sha256": file_digest(reference_path), "reference_assessed_by": reference["assessed_by"],
            "limitation": reference["limitation"], "build_id": report["build_id"],
            "candidate_sha256": file_digest(candidate_path), "topics": len(bundle["topics"]),
            "stage_errors": report["errors"], "telemetry": report["telemetry"],
            "classification_duration_seconds": report.get("duration_seconds"), **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-dir", type=Path, required=True)
    parser.add_argument("--classified-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=ROOT / "tests/fixtures/classification_reference_v6.json")
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.candidates_dir, args.classified_dir, args.reference)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"classification evaluation failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["gate"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
