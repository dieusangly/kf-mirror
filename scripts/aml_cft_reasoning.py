"""Local symbolic reasoning setup, version 1.0.0; no model/database access.

Commands: validate-selection, validate, execute, check, verify-setup.
Exit 0: success; 1: validation/check findings; 2: usage/I/O/malformed input.
All reports are created exclusively. Inputs and frozen outputs are never changed.
"""

import argparse
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path
import sys

VERSION = "1.0.0"
SCHEMA = "aml-cft-symbolic-1"
FAMILIES = ["KYC", "BO", "PEP", "RISK", "MON", "TFS", "PAY", "CORR"]
IDENTITY = ("dataset_id", "dataset_version", "case_id", "language")


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def write(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def fields(obj, expected, label):
    require(isinstance(obj, dict) and set(obj) == set(expected), f"{label}: fields must be {sorted(expected)}")


def strings(values, label, nonempty=False):
    require(isinstance(values, list) and all(isinstance(x, str) and x and not x.startswith("?") for x in values), label)
    require(len(values) == len(set(values)) and (values or not nonempty), label)


def key(atom):
    return canonical(atom)


def variables(atom):
    return {x for x in atom["args"] if x.startswith("?")}


def ground(atom, binding):
    return {"predicate": atom["predicate"], "args": [binding.get(x, x) for x in atom["args"]]}


def check_atom(atom, encoding, var_types):
    fields(atom, ("predicate", "args"), "atom")
    require(atom["predicate"] in encoding["predicates"], "undeclared predicate")
    types = encoding["predicates"][atom["predicate"]]
    require(isinstance(atom["args"], list) and len(atom["args"]) == len(types), "predicate arity")
    for term, typ in zip(atom["args"], types):
        require(isinstance(term, str) and bool(term), "invalid term")
        require((var_types if term.startswith("?") else encoding["entities"]).get(term) == typ,
                f"unbound or incorrectly typed term: {term}")


def check_variables(var_types):
    require(isinstance(var_types, dict), "variables must be a mapping")
    require(all(isinstance(v, str) and v.startswith("?") and len(v) > 1 and isinstance(t, str) and t
                for v, t in var_types.items()), "invalid variable declarations")


def check_encoding(encoding):
    fields(encoding, (*IDENTITY, "schema_version", "scope_id", "entities", "predicates", "facts", "rules", "incompatibilities"), "encoding")
    require(encoding["schema_version"] == SCHEMA, "unsupported schema")
    require(all(isinstance(encoding[x], str) and encoding[x] for x in (*IDENTITY, "scope_id")), "identity/scope missing")
    require(encoding["language"] in ("en", "fr"), "unsupported language")
    require(isinstance(encoding["entities"], dict) and encoding["entities"], "entities required")
    require(all(isinstance(e, str) and e and not e.startswith("?") and isinstance(t, str) and t
                for e, t in encoding["entities"].items()), "invalid entities")
    require(isinstance(encoding["predicates"], dict) and encoding["predicates"], "predicates required")
    for name, types in encoding["predicates"].items():
        require(isinstance(name, str) and name and isinstance(types, list) and types
                and all(isinstance(t, str) and t for t in types), "invalid predicate declaration")
    ids = set()
    for kind in ("facts", "rules", "incompatibilities"):
        require(isinstance(encoding[kind], list), f"{kind}: array required")
        for item in encoding[kind]:
            expected = {"id", "source_ids"} | ({"atom"} if kind == "facts" else
                        {"variables", "body", "head"} if kind == "rules" else {"variables", "left", "right"})
            fields(item, expected, kind)
            require(isinstance(item["id"], str) and item["id"] and item["id"] not in ids, "duplicate/invalid ID")
            ids.add(item["id"])
            strings(item["source_ids"], "source IDs must be nonempty and unique", True)
            if kind == "facts":
                check_atom(item["atom"], encoding, {})
                continue
            check_variables(item["variables"])
            if kind == "rules":
                require(isinstance(item["body"], list) and item["body"], "empty rule body is not a deduction")
                atoms = item["body"] + [item["head"]]
            else:
                atoms = [item["left"], item["right"]]
            for atom in atoms:
                check_atom(atom, encoding, item["variables"])
            used = set().union(*(variables(a) for a in atoms))
            require(used == set(item["variables"]), "unused/undeclared variables")
            if kind == "rules":
                require(variables(item["head"]) <= set().union(*(variables(a) for a in item["body"])), "unsafe head variable")
            else:
                require(variables(item["left"]) == variables(item["right"]), "incompatible pair must share entity bindings")
                require(item["left"] != item["right"], "self-incompatibility unsupported")


def check_queries(encoding, query):
    fields(query, (*IDENTITY, "schema_version", "queries"), "query record")
    require(all(query[x] == encoding[x] for x in (*IDENTITY, "schema_version")), "encoding/query identity differs")
    require(isinstance(query["queries"], list) and query["queries"], "queries required")
    ids = set()
    for q in query["queries"]:
        fields(q, ("id", "question_id", "target", "variables", "select"), "query")
        require(isinstance(q["id"], str) and q["id"] and q["id"] not in ids, "duplicate/invalid query ID")
        require(isinstance(q["question_id"], str) and q["question_id"], "question link required")
        ids.add(q["id"])
        check_variables(q["variables"])
        check_atom(q["target"], encoding, q["variables"])
        require(variables(q["target"]) == set(q["variables"]), "query variable binding")
        require(isinstance(q["select"], list) and len(set(q["select"])) == len(q["select"])
                and set(q["select"]) <= set(q["variables"]), "invalid projection")


def check_config(config):
    fields(config, ("engine_version", "adapter_version", "checker_version", "schema_version", "semantics", "max_ground_instances", "max_atoms"), "config")
    require(all(config[x] == VERSION for x in ("engine_version", "adapter_version", "checker_version")), "unsupported component version")
    require(config["schema_version"] == SCHEMA and config["semantics"] == "candidate-conflict-then-usable-closure-1", "unsupported semantics")
    require(all(type(config[x]) is int and config[x] > 0 for x in ("max_ground_instances", "max_atoms")), "invalid resource bounds")


def bindings(var_types, encoding, limit):
    names = sorted(var_types)
    domains = [[entity for entity, typ in sorted(encoding["entities"].items()) if typ == var_types[v]] for v in names]
    count = 1
    for domain in domains:
        count *= len(domain)
    require(count <= limit, "grounding limit exceeded; record a failure, never truncate")
    return [dict(zip(names, values)) for values in product(*domains)]


def instances(encoding, config):
    result, pairs = [], []
    for rule in encoding["rules"]:
        for binding in bindings(rule["variables"], encoding, config["max_ground_instances"]):
            result.append({"rule_id": rule["id"], "binding": binding,
                           "premises": [ground(a, binding) for a in rule["body"]], "atom": ground(rule["head"], binding)})
    for pair in encoding["incompatibilities"]:
        for binding in bindings(pair["variables"], encoding, config["max_ground_instances"]):
            pairs.append({"id": pair["id"], "binding": binding, "left": ground(pair["left"], binding), "right": ground(pair["right"], binding)})
    require(len(result) + len(pairs) <= config["max_ground_instances"], "total grounding limit exceeded")
    return result, pairs


def closure(encoding, rules, blocked, max_atoms):
    supported, certificate = set(), []

    def add(atom, proof):
        atom_key = key(atom)
        if atom_key not in supported and atom_key not in blocked:
            require(len(supported) < max_atoms, "atom limit exceeded")
            supported.add(atom_key)
            certificate.append(proof)

    for fact in encoding["facts"]:
        add(fact["atom"], {"atom": fact["atom"], "fact_id": fact["id"]})
    while True:
        before = len(supported)
        for rule in rules:
            if all(key(a) in supported for a in rule["premises"]):
                add(rule["atom"], rule)
        if len(supported) == before:
            return supported, certificate


def query_answers(encoding, query, candidate, usable, blocked, rules, config):
    # Propagate conflict dependence only through an otherwise supported rule.
    tainted = set(blocked)
    while True:
        before = len(tainted)
        for r in rules:
            prem = {key(a) for a in r["premises"]}
            if prem <= candidate and prem & tainted and key(r["atom"]) not in usable:
                tainted.add(key(r["atom"]))
        if len(tainted) == before:
            break

    def explain(atom_key, seen):
        if atom_key in usable:
            return []
        if atom_key in tainted:
            return [{"kind": "conflicted-premise", "atom": json.loads(atom_key)}]
        if atom_key in seen:
            return [{"kind": "unsupported-cycle", "atom": json.loads(atom_key)}]
        options = [r for r in rules if key(r["atom"]) == atom_key]
        if not options:
            return [{"kind": "missing-premise", "atom": json.loads(atom_key)}]
        return [{"kind": "rule-prerequisites", "rule_id": r["rule_id"], "binding": r["binding"],
                 "missing": [finding for a in r["premises"] for finding in explain(key(a), seen | {atom_key})]}
                for r in options]

    answers = []
    for q in query["queries"]:
        targets = [(b, ground(q["target"], b)) for b in bindings(q["variables"], encoding, config["max_ground_instances"])]
        matches = [(b, a) for b, a in targets if key(a) in usable]
        conflicts = [a for _, a in targets if key(a) in tainted]
        status = "entailed" if matches else "conflict" if conflicts else "unknown"
        answers.append({"query_id": q["id"], "question_id": q["question_id"], "status": status,
                        "answers": [json.loads(s) for s in sorted({canonical({v: b[v] for v in q["select"]}) for b, _ in matches})],
                        "supported_atoms": [a for _, a in matches], "conflicted_targets": conflicts,
                        "missing": [] if matches else [{"target": a, "explanation": explain(key(a), set())} for _, a in targets]})
    return answers


def solve(encoding, query, config):
    check_config(config)
    check_encoding(encoding)
    check_queries(encoding, query)
    rules, pairs = instances(encoding, config)
    candidate, candidate_proof = closure(encoding, rules, set(), config["max_atoms"])
    conflicts = [p for p in pairs if key(p["left"]) in candidate and key(p["right"]) in candidate]
    blocked = {key(p[side]) for p in conflicts for side in ("left", "right")}
    usable, usable_proof = closure(encoding, rules, blocked, config["max_atoms"])
    return {**{x: encoding[x] for x in IDENTITY}, "engine_version": VERSION,
            "encoding_sha256": hashlib.sha256(canonical(encoding).encode()).hexdigest(),
            "query_sha256": hashlib.sha256(canonical(query).encode()).hexdigest(),
            "config_sha256": hashlib.sha256(canonical(config).encode()).hexdigest(),
            "candidate_certificate": candidate_proof, "conflicts": conflicts, "usable_certificate": usable_proof,
            "results": query_answers(encoding, query, candidate, usable, blocked, rules, config)}


def verify_certificate(encoding, certificate, rules, blocked, config):
    """Check supplied proof steps and closure completeness without running solve."""
    require(isinstance(certificate, list) and len(certificate) <= config["max_atoms"], "invalid certificate size")
    facts = {f["id"]: f["atom"] for f in encoding["facts"]}
    legal_rules = {canonical(r) for r in rules}
    proven = set()
    for step in certificate:
        atom_key = key(step["atom"])
        require(atom_key not in proven and atom_key not in blocked, "duplicate/blocked certificate step")
        if "fact_id" in step:
            fields(step, ("atom", "fact_id"), "fact certificate")
            require(facts.get(step["fact_id"]) == step["atom"], "invented or altered assertion")
        else:
            require(canonical(step) in legal_rules, "invalid rule instance/substitution")
            require(all(key(a) in proven for a in step["premises"]), "missing/cyclic proof premise")
        proven.add(atom_key)
    require(all(key(a) in proven or key(a) in blocked for a in facts.values()), "omitted assertion")
    for r in rules:
        require(not all(key(a) in proven for a in r["premises"]) or key(r["atom"]) in proven or key(r["atom"]) in blocked,
                "incomplete deductive closure")
    return proven


def check_result(encoding, query, config, result):
    check_config(config)
    check_encoding(encoding)
    check_queries(encoding, query)
    fields(result, (*IDENTITY, "engine_version", "encoding_sha256", "query_sha256", "config_sha256",
                    "candidate_certificate", "conflicts", "usable_certificate", "results"), "result")
    require(all(result[x] == encoding[x] for x in IDENTITY) and result["engine_version"] == VERSION, "result identity/version mismatch")
    for name, obj in (("encoding", encoding), ("query", query), ("config", config)):
        require(result[name + "_sha256"] == hashlib.sha256(canonical(obj).encode()).hexdigest(), "stale result inputs")
    rules, pairs = instances(encoding, config)
    candidate = verify_certificate(encoding, result["candidate_certificate"], rules, set(), config)
    conflicts = [p for p in pairs if key(p["left"]) in candidate and key(p["right"]) in candidate]
    require(result["conflicts"] == conflicts, "missing/invented conflict")
    blocked = {key(p[side]) for p in conflicts for side in ("left", "right")}
    usable = verify_certificate(encoding, result["usable_certificate"], rules, blocked, config)
    require(result["results"] == query_answers(encoding, query, candidate, usable, blocked, rules, config), "incorrect answers/diagnostics")
    return {"valid": True, "checker_version": VERSION, "candidate_steps": len(candidate), "usable_steps": len(usable),
            "deduction_steps": sum("rule_id" in x for x in result["usable_certificate"])}


def selection(batch):
    manifest_path = Path(batch["source_dataset_manifest"]["path"])
    require(digest(manifest_path) == batch["source_dataset_manifest"]["sha256"], "dataset manifest hash mismatch")
    manifest = read(manifest_path)
    require(batch["dataset_id"] == manifest["dataset_id"] and batch["dataset_version"] == manifest["dataset_version"], "dataset identity differs")
    families = batch["selected_families"]
    require(families and len(families) == len(set(families)) and set(families) <= set(FAMILIES), "invalid family selection")
    expected = [cid for f in families for cid in manifest["families"][f]]
    require(batch["case_ids"] == expected and len(set(expected)) == len(expected), "case selection differs")
    require(batch["languages"] == ["en", "fr"], "both languages required")
    require(batch["semantic_cases"] == len(expected) and batch["language_versions"] == 2 * len(expected), "denominator differs")
    expected_selection = [{"case_id": cid, "language": lang,
                           "source_records": next(c for c in manifest["cases"] if c["case_id"] == cid)["language_versions"][lang]}
                          for cid in expected for lang in batch["languages"]]
    require(batch["selection"] == expected_selection, "exact language/record selection differs")
    source_paths = {str(manifest_path.parent / name): info["sha256"] for name, info in manifest["files"].items()}
    require(batch["source_files"] == source_paths, "source file hash bindings differ")
    loaded = {}
    for name, info in manifest["files"].items():
        path = manifest_path.parent / name
        require(digest(path) == info["sha256"], f"payload hash mismatch: {path}")
        data = rows(path)
        require(len(data) == info["records"], "source row count differs")
        for entry in batch["selection"]:
            pointer = entry["source_records"][name]
            row = data[pointer["line"] - 1]
            require(row["case_id"] == entry["case_id"] and row["language"] == entry["language"], "source record identity differs")
            require(hashlib.sha256(canonical(row).encode()).hexdigest() == pointer["canonical_sha256"], "source record hash differs")
        loaded[name] = {(x["case_id"], x["language"]): x for x in data}
    return manifest, loaded


def selected_records(batch, encodings, queries):
    _, source = selection(batch)
    wanted = {(x["case_id"], x["language"]) for x in batch["selection"]}
    indexes = []
    for data in (encodings, queries):
        index = {(x["case_id"], x["language"]): x for x in data}
        require(len(index) == len(data) and set(index) == wanted, "missing/duplicate/unselected encoding or query")
        indexes.append(index)
    result = []
    for entry in batch["selection"]:
        k = entry["case_id"], entry["language"]
        enc, query = (idx[k] for idx in indexes)
        require(enc["dataset_id"] == batch["dataset_id"] and enc["dataset_version"] == batch["dataset_version"], "encoding dataset differs")
        result.append((enc, query))
    return result


def validate_pair(batch, source, enc, query):
    require(not enc.get("generation_error") and not query.get("generation_error"), "recorded Stage 3 generation failure")
    check_encoding(enc)
    check_queries(enc, query)
    k = enc["case_id"], enc["language"]
    original = source["inputs-v1.jsonl"][k]
    require(enc["scope_id"] == original["scope"]["id"], "scope link differs")
    for kind, original_kind in (("facts", "scenario_facts"), ("rules", "policy_clauses"), ("incompatibilities", "policy_clauses")):
        allowed = {x["id"] for x in original[original_kind]}
        require(all(set(x["source_ids"]) <= allowed for x in enc[kind]), f"invalid {kind} provenance")
    require(all(x["question_id"] == source["questions-v1.jsonl"][k]["question_id"] for x in query["queries"]), "question provenance differs")


def fixture():
    """Generic setup control, deliberately unrelated to selected policy encodings."""
    atom = lambda p, *a: {"predicate": p, "args": list(a)}
    enc = dict(dataset_id="setup-control", dataset_version="1", case_id="control", language="en", schema_version=SCHEMA,
               scope_id="control-SC", entities={"a": "item", "b": "item", "c": "item"},
               predicates={p: ["item"] for p in ("seed", "ready", "done", "denied", "clearance", "released")},
               facts=[{"id": "f1", "source_ids": ["F1"], "atom": atom("seed", "a")}],
               rules=[{"id": "r1", "source_ids": ["P1"], "variables": {"?x": "item"}, "body": [atom("seed", "?x")], "head": atom("ready", "?x")},
                      {"id": "r2", "source_ids": ["P2"], "variables": {"?x": "item"}, "body": [atom("ready", "?x")], "head": atom("done", "?x")},
                      {"id": "r3", "source_ids": ["P3"], "variables": {"?x": "item"}, "body": [atom("done", "?x"), atom("clearance", "?x")], "head": atom("released", "?x")}],
               incompatibilities=[{"id": "i1", "source_ids": ["P4"], "variables": {"?x": "item"}, "left": atom("ready", "?x"), "right": atom("denied", "?x")}])
    query = {**{k: enc[k] for k in (*IDENTITY, "schema_version")}, "queries": [
        {"id": p, "question_id": "Q1", "target": atom(p, "a"), "variables": {}, "select": []}
        for p in ("seed", "ready", "done", "released")]}
    return enc, query


def verify_setup(batch, config):
    manifest, _ = selection(batch)
    check_config(config)
    enc, query = fixture()
    base = solve(enc, query, config)
    require([r["status"] for r in base["results"]] == ["entailed", "entailed", "entailed", "unknown"], "lookup/chain/clearance control")
    checked = check_result(enc, query, config, base)
    require(checked["deduction_steps"] == 2, "real two-hop certificate required")
    conflict_enc = deepcopy(enc)
    conflict_enc["facts"].append({"id": "f2", "source_ids": ["F2"], "atom": {"predicate": "denied", "args": ["a"]}})
    conflict = solve(conflict_enc, query, config)
    require([r["status"] for r in conflict["results"]] == ["entailed", "conflict", "conflict", "unknown"], "conflict blocking control")
    check_result(conflict_enc, query, config, conflict)
    return {"valid": True, "setup_version": VERSION, "batch_id": batch["batch_id"], "semantic_cases": len(batch["case_ids"]),
            "language_versions": batch["language_versions"], "dataset_version": manifest["dataset_version"],
            "controls": {"lookup": "pass", "one_hop": "pass", "two_hop": "pass", "positive_clearance_missing": "pass",
                         "derived_conflict": "pass", "dependent_withheld": "pass", "unrelated_preserved": "pass", "proof_checks": "pass"},
            "generic_fixture": {"encoding": enc, "queries": query, "result": base, "conflict_encoding": conflict_enc,
                                "conflict_result": conflict},
            "limitations": "Generic setup controls only; dataset references, encoding fidelity and reasoning require Stage 4 review."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate-selection", "verify-setup", "validate", "execute", "check"))
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--encodings", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; use a fresh path")
    try:
        batch = read(args.batch)
        if args.command == "validate-selection":
            selection(batch)
            output = {"valid": True, "batch_id": batch["batch_id"], "semantic_cases": len(batch["case_ids"]), "language_versions": batch["language_versions"]}
        else:
            require(args.config is not None, "--config required")
            config = read(args.config)
            check_config(config)
            if args.command == "verify-setup":
                output = verify_setup(batch, config)
            else:
                require(args.encodings is not None and args.queries is not None, "--encodings and --queries required")
                pairs = selected_records(batch, rows(args.encodings), rows(args.queries))
                _, source = selection(batch)
                if args.command == "validate":
                    checks = []
                    for enc, q in pairs:
                        try:
                            validate_pair(batch, source, enc, q)
                            instances(enc, config)
                            for item in q["queries"]:
                                bindings(item["variables"], enc, config["max_ground_instances"])
                            check = {"valid": True}
                        except (Invalid, KeyError, TypeError, ValueError) as error:
                            check = {"valid": False, "error": str(error)}
                        checks.append({"case_id": enc["case_id"], "language": enc["language"], **check})
                    output = {"valid": all(x["valid"] for x in checks), "batch_id": batch["batch_id"], "language_versions": len(pairs), "checks": checks}
                elif args.command == "execute":
                    output = {"batch_id": batch["batch_id"], "provisional": True, "records": []}
                    for enc, q in pairs:
                        try:
                            validate_pair(batch, source, enc, q)
                            output["records"].append(solve(enc, q, config))
                        except (Invalid, KeyError, TypeError, ValueError) as error:
                            output["records"].append({**{x: enc[x] for x in IDENTITY}, "error": str(error)})
                else:
                    require(args.results is not None, "--results required")
                    results = read(args.results)
                    require(results["batch_id"] == batch["batch_id"] and results["provisional"] is True, "result batch/provisional mismatch")
                    saved = {(r["case_id"], r["language"]): r for r in results["records"]}
                    require(len(saved) == len(results["records"]) == len(pairs), "result selection differs")
                    checks = []
                    for enc, q in pairs:
                        result = saved.get((enc["case_id"], enc["language"]))
                        require(result is not None, "result case missing")
                        try:
                            validate_pair(batch, source, enc, q)
                            check = check_result(enc, q, config, result)
                        except (Invalid, KeyError, TypeError, ValueError) as error:
                            check = {"valid": False, "error": str(error)}
                        checks.append({"case_id": enc["case_id"], "language": enc["language"], **check})
                    output = {"valid": all(c["valid"] for c in checks), "batch_id": batch["batch_id"], "checks": checks}
        output["input_files_sha256"] = {str(p): digest(p) for p in (args.batch, args.config, args.encodings, args.queries, args.results) if p is not None}
        write(args.output, output)
        print(canonical({"output": str(args.output), "valid": output.get("valid", True)}))
        return 0 if output.get("valid", True) and not any("error" in r for r in output.get("records", [])) else 1
    except Invalid as error:
        write(args.output, {"valid": False, "error": str(error)})
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
