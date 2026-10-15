"""Shared deterministic scoring for source-reviewed chunk and compilation samples."""

import json
import math
from pathlib import Path

REVIEW_STATUSES = {"pending", "pass", "fail"}
REVIEW_SEVERITIES = {"critical", "major", "minor"}
EXAMPLE_LIMIT = 5


def read_review_policy(path):
    """Load and validate the policy that turns reviewed samples into a gate."""
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "policy_version", "minimum_review_pass_rate", "minimum_weighted_score",
        "maximum_critical_failures", "review_points",
    }
    if set(policy) != required or not isinstance(policy["policy_version"], str):
        raise ValueError("review policy has an invalid shape")
    pass_rate = policy["minimum_review_pass_rate"]
    if isinstance(pass_rate, bool) or not isinstance(pass_rate, (int, float)) or not 0 <= pass_rate <= 1:
        raise ValueError("review policy minimum_review_pass_rate must be a number from 0 to 1")
    minimum_score = policy["minimum_weighted_score"]
    if (isinstance(minimum_score, bool) or not isinstance(minimum_score, (int, float)) or
            not 0 <= minimum_score <= 100):
        raise ValueError("review policy minimum_weighted_score must be a number from 0 to 100")
    critical_limit = policy["maximum_critical_failures"]
    if isinstance(critical_limit, bool) or not isinstance(critical_limit, int) or critical_limit < 0:
        raise ValueError("review policy maximum_critical_failures must be a non-negative integer")
    points = policy["review_points"]
    if (not isinstance(points, dict) or set(points) != REVIEW_SEVERITIES | {"pass"} or
            any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in points.values()) or
            not 0 <= points["critical"] < points["major"] < points["minor"] < points["pass"]):
        raise ValueError("review policy review_points must satisfy 0 <= critical < major < minor < pass")
    return policy


def apply_reviews(review, build_id, samples, policy, id_field="chunk_id"):
    to_review = len(samples)
    empty = {"status": "pending", "reviewed": 0, "to_review": to_review,
             "passed": 0, "failed": 0, "pending": to_review,
             "review_coverage": 0.0, "review_pass_rate": None, "weighted_score": None,
             "severity_counts": {severity: 0 for severity in sorted(REVIEW_SEVERITIES)},
             "allowed_failures": {"critical": policy["maximum_critical_failures"]}}
    if review is None or not to_review:
        return empty, []
    if review.get("build_id") != build_id or not isinstance(review.get("reviews"), list):
        raise ValueError("review file must name the current build_id and a reviews array")
    sample_ids = {item[id_field] for item in samples}
    seen = {}
    for item in review["reviews"]:
        status = item.get("status")
        notes = item.get("notes", "")
        if item.get(id_field) not in sample_ids or item[id_field] in seen or status not in REVIEW_STATUSES:
            raise ValueError("reviews need unique sampled IDs and pending/pass/fail status")
        if status in {"pending", "fail"} and (not isinstance(notes, str) or not notes.strip()):
            raise ValueError("pending and failed reviews need notes")
        if status == "fail" and item.get("severity") not in REVIEW_SEVERITIES:
            raise ValueError("failed reviews need critical, major, or minor severity")
        seen[item[id_field]] = item
    passed = sum(item["status"] == "pass" for item in seen.values())
    failed = [item for item in seen.values() if item["status"] == "fail"]
    reviewed = passed + len(failed)
    pending = to_review - reviewed
    severity_counts = {severity: sum(item.get("severity") == severity for item in failed)
                       for severity in sorted(REVIEW_SEVERITIES)}
    allowed = {"critical": policy["maximum_critical_failures"]}
    coverage = reviewed / to_review if to_review else 1.0
    pass_rate = passed / reviewed if reviewed else None
    points = policy["review_points"]
    score = None if not reviewed else 100 * (
        passed * points["pass"] + sum(
            severity_counts[severity] * points[severity] for severity in REVIEW_SEVERITIES)
    ) / (reviewed * points["pass"])
    if pending:
        status = "pending"
    elif (pass_rate < policy["minimum_review_pass_rate"] or
          score < policy["minimum_weighted_score"] or
          severity_counts["critical"] > allowed["critical"]):
        status = "fail"
    else:
        status = "pass"
    findings = [{"code": "MANUAL_REVIEW_POLICY_FAILED", "severity": "error", "count": len(failed),
                 "examples": [{id_field: item[id_field], "severity": item["severity"],
                               "notes": item["notes"]} for item in failed[:EXAMPLE_LIMIT]]}] if status == "fail" else []
    summary = {"status": status, "reviewed": reviewed, "to_review": to_review,
            "passed": passed, "failed": len(failed), "pending": pending,
            "review_coverage": coverage, "review_pass_rate": pass_rate,
            "weighted_score": score, "severity_counts": severity_counts,
            "allowed_failures": allowed}
    return summary, findings


def select_samples(rows, count, risk_order):
    """Select one item per available risk, then spread through reading order."""
    if not rows:
        return []
    selected = []
    for reason in risk_order:
        match = next((row for row in rows if reason in row['reasons'] and row not in selected), None)
        if match:
            selected.append(match)
        if len(selected) == count:
            return selected
    positions = [0] if count == 1 else [round(i * (len(rows) - 1) / (count - 1)) for i in range(count)]
    for index in positions:
        if rows[index] not in selected:
            selected.append(rows[index])
        if len(selected) == count:
            break
    return (selected + [row for row in rows if row not in selected])[:count]

