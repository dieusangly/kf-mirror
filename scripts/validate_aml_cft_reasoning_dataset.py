"""Validate the Stage 1 readable dataset's structure, coverage and hashes.

No model, engine or database access. This does not judge policy/reference meaning.
Exit 0: valid; 1: validation findings; 2: usage, I/O or malformed JSON error.
Reports are deterministic and created exclusively; use a fresh --output path.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

FAMILIES = ("KYC", "BO", "PEP", "RISK", "MON", "TFS", "PAY", "CORR")
CATEGORIES = ("direct-lookup", "one-hop", "multi-hop", "missing-prerequisite", "exception", "conflict")
FILES = ("inputs-v1.jsonl", "questions-v1.jsonl", "proposed-references-v1.jsonl")
BASE = {"dataset_id", "dataset_version", "family_id", "case_id", "language"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate(manifest_path):
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    manifest = json.loads(manifest_path.read_text())
    check(manifest["schema_version"] == "readable-dataset-1", "unsupported schema")
    check(manifest["languages"] == ["en", "fr"], "languages must be en/fr")
    check(manifest["semantic_cases"] == 96, "manifest must declare 96 semantic cases")
    check(manifest["language_versions"] == 192, "manifest must declare 192 language versions")
    check(manifest["category_codes"] == dict(zip(("DL", "OH", "MH", "MP", "EX", "CF"), CATEGORIES)), "category code mapping differs")
    check(set(manifest["families"]) == set(FAMILIES), "family selection differs from Stage 1 scope")
    check(set(manifest["files"]) == set(FILES), "payload file set differs")
    records, indexes = {}, {}
    for name in FILES:
        data = (manifest_path.parent / name).read_bytes()
        rows = [json.loads(line) for line in data.splitlines()]
        check(sha(data) == manifest["files"][name]["sha256"], f"{name}: file hash mismatch")
        check(len(rows) == manifest["files"][name]["records"] == 192, f"{name}: expected 192 records")
        index = {}
        for line_number, row in enumerate(rows, 1):
            key = (row["case_id"], row["language"])
            check(key not in index, f"{name}: duplicate {key}")
            index[key] = line_number
            check(row["dataset_id"] == manifest["dataset_id"] and row["dataset_version"] == manifest["dataset_version"],
                  f"{name}/{key}: dataset identity mismatch")
        records[name], indexes[name] = rows, index
    case_ids = [entry["case_id"] for entry in manifest["cases"]]
    check(len(case_ids) == len(set(case_ids)) == 96, "case IDs must be unique and total 96")
    expected_keys = {(cid, lang) for cid in case_ids for lang in ("en", "fr")}
    for name in FILES:
        check(set(indexes[name]) == expected_keys, f"{name}: language/case keys differ from manifest")
    mapped = [cid for ids in manifest["families"].values() for cid in ids]
    check(Counter(mapped) == Counter(case_ids), "family mapping must partition cases exactly once")
    coverage = {family: Counter() for family in FAMILIES}
    patterns = {}
    per_case = []
    for entry in manifest["cases"]:
        before = len(errors)
        cid, family, category = entry["case_id"], entry["family_id"], entry["category"]
        check(family in FAMILIES and cid in manifest["families"].get(family, []), f"{cid}: wrong family")
        check(category in CATEGORIES, f"{cid}: unknown category")
        coverage.setdefault(family, Counter())[category] += 1
        patterns.setdefault((family, category), []).append(entry.get("structural_pattern"))
        check(nonempty(entry.get("structural_pattern")), f"{cid}: declared structural pattern missing")
        code = next((k for k, v in manifest["category_codes"].items() if v == category), "INVALID")
        check(entry["variant"] in (1, 2) and cid == f"{family}-{code}-{entry['variant']:02d}", f"{cid}: unstable ID")
        check(set(entry["language_versions"]) == {"en", "fr"}, f"{cid}: bilingual versions missing")
        check(entry["semantic_bundle_sha256"] == sha(canonical(entry["language_versions"])), f"{cid}: bundle hash mismatch")
        bilingual = []
        for language in ("en", "fr"):
            key = (cid, language)
            if any(key not in indexes[name] for name in FILES):
                continue
            loaded = []
            for name in FILES:
                location = entry["language_versions"][language][name]
                line = location["line"]
                check(line == indexes[name][key], f"{cid}/{language}/{name}: wrong line mapping")
                row = records[name][indexes[name][key] - 1]
                check(row["family_id"] == family, f"{cid}/{language}: wrong payload family")
                check(sha(canonical(row)) == location["canonical_sha256"], f"{cid}/{language}/{name}: record hash mismatch")
                loaded.append(row)
            inp, question, ref = loaded
            check(set(inp) == BASE | {"title", "scope", "policy_clauses", "scenario_facts"}, f"{cid}/{language}: input schema / answer separation")
            check(set(question) == BASE | {"question_id", "text"}, f"{cid}/{language}: question schema / answer separation")
            check(set(ref) == BASE | {"question_id", "reference_status", "expected_status", "expected_conclusions", "missing_premises", "withheld_conclusions", "proposed_derivation_depth", "proof_steps"}, f"{cid}/{language}: reference schema")
            check(nonempty(inp["title"]) and nonempty(inp["scope"]["text"]) and nonempty(question["text"]), f"{cid}/{language}: empty readable field")
            check(inp["scope"]["id"] == f"{cid}-SC01", f"{cid}/{language}: scope ID")
            check(question["question_id"] == ref["question_id"] == f"{cid}-Q01", f"{cid}/{language}: question ID")
            sources = {inp["scope"]["id"]}
            for group, letter in ((inp["policy_clauses"], "P"), (inp["scenario_facts"], "F")):
                for ordinal, item in enumerate(group, 1):
                    check(item["id"] == f"{cid}-{letter}{ordinal:02d}" and nonempty(item["text"]), f"{cid}/{language}: clause/fact ID or text")
                    check(item["id"] not in sources, f"{cid}/{language}: duplicate source ID")
                    sources.add(item["id"])
                    if letter == "F":
                        check(item.get("synthetic_assumption") is True, f"{cid}/{language}: fact not labelled synthetic")
            check(bool(inp["scenario_facts"]), f"{cid}/{language}: no scenario facts")
            check(ref["reference_status"] == "provisional", f"{cid}/{language}: reference prematurely accepted")
            check(ref["expected_status"] in ("entailed", "unknown", "conflict"), f"{cid}/{language}: unknown outcome")
            for field in ("expected_conclusions", "missing_premises", "withheld_conclusions"):
                check(isinstance(ref[field], list) and all(nonempty(v) for v in ref[field]), f"{cid}/{language}: invalid {field}")
            check(bool(ref["proof_steps"]), f"{cid}/{language}: no proof/justification links")
            for ordinal, step in enumerate(ref["proof_steps"], 1):
                check(step["id"] == f"{cid}-S{ordinal:02d}", f"{cid}/{language}: proof-step ID")
                check(step["kind"] in ("assertion", "inference", "control", "conflict") and nonempty(step["statement"]), f"{cid}/{language}: invalid step")
                check(step["scope_id"] == inp["scope"]["id"], f"{cid}/{language}: proof scope mismatch")
                check(bool(step["source_ids"]) and set(step["source_ids"]) <= sources, f"{cid}/{language}: dangling, cross-case or forward proof link")
                if step["kind"] == "inference":
                    check(any(re.fullmatch(re.escape(cid) + r"-P\d{2}", source) for source in step["source_ids"]), f"{cid}/{language}: inference without clause link")
                sources.add(step["id"])
            if ref["expected_status"] == "entailed":
                check(bool(ref["expected_conclusions"]) and not ref["missing_premises"], f"{cid}/{language}: incomplete positive reference")
            else:
                check(not ref["expected_conclusions"], f"{cid}/{language}: unsupported conclusions on control")
            if category == "missing-prerequisite":
                check(ref["expected_status"] == "unknown" and bool(ref["missing_premises"]), f"{cid}/{language}: missing-premise coverage")
            if category == "conflict":
                check(ref["expected_status"] == "conflict" and bool(ref["withheld_conclusions"]), f"{cid}/{language}: conflict coverage")
                check(any(s["kind"] == "conflict" for s in ref["proof_steps"]), f"{cid}/{language}: conflict justification missing")
            if category == "direct-lookup":
                check(not inp["policy_clauses"] and ref["proposed_derivation_depth"] == 0 and all(s["kind"] == "assertion" for s in ref["proof_steps"]), f"{cid}/{language}: direct lookup contains deduction")
            if category in ("one-hop", "multi-hop"):
                depth = 1 if category == "one-hop" else entry["variant"] + 1
                check(ref["proposed_derivation_depth"] == depth and len(inp["policy_clauses"]) == depth and sum(s["kind"] == "inference" for s in ref["proof_steps"]) == depth, f"{cid}/{language}: declared hop coverage")
            if category == "exception":
                check(ref["expected_status"] == "entailed", f"{cid}/{language}: exception reference missing")
                if entry["variant"] == 1:
                    check(bool(ref["withheld_conclusions"]) and any(s["kind"] == "control" for s in ref["proof_steps"]), f"{cid}/{language}: active exception control missing")
            bilingual.append((
                [p["id"] for p in inp["policy_clauses"]], [f["id"] for f in inp["scenario_facts"]],
                ref["expected_status"], ref["proposed_derivation_depth"],
                [(s["id"], s["kind"], s["source_ids"], s["scope_id"]) for s in ref["proof_steps"]],
                [len(ref[k]) for k in ("expected_conclusions", "missing_premises", "withheld_conclusions")],
            ))
        check(len(bilingual) == 2 and bilingual[0] == bilingual[1], f"{cid}: bilingual structural mismatch")
        per_case.append({"case_id": cid, "structural_status": "pass" if len(errors) == before else "fail"})
    for family in FAMILIES:
        check(len(manifest["families"].get(family, [])) == 12, f"{family}: must have 12 cases")
        check(coverage[family] == Counter({category: 2 for category in CATEGORIES}), f"{family}: category coverage differs")
        for category in CATEGORIES:
            pair = patterns.get((family, category), [])
            check(len(pair) == 2 and len(set(pair)) == 2, f"{family}/{category}: distinct declared patterns required")
    return dict(status="pass" if not errors else "fail", dataset_id=manifest["dataset_id"],
                dataset_version=manifest["dataset_version"], manifest_sha256=sha(manifest_path.read_bytes()),
                semantic_cases=len(case_ids), language_versions=len(expected_keys),
                coverage={f: dict(coverage[f]) for f in FAMILIES}, errors=errors, cases=per_case,
                scope="Structure, declared coverage, source-link integrity, bilingual structural correspondence and content hashes only. No reference correctness, translation equivalence, inference execution or independent acceptance judgment.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Never replace a frozen report, including on a malformed-input attempt.
        if args.output.exists():
            raise FileExistsError(f"Output exists: {args.output}")
        report = validate(args.manifest)
        with args.output.open("x") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        print(f"validation error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({k: report[k] for k in ("status", "semantic_cases", "language_versions", "errors")}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
