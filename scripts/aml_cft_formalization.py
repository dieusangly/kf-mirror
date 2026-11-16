"""Stage 3 local text mappings; never execute reasoning or call a provider.

Commands: verify, encode, queries, compare. Exit 0 success, 1 recorded per-case
failure, 2 global input/usage error. Every output directory/file must be fresh.
The generation functions consume explicit allowlists, not dataset records.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import aml_cft_reasoning as engine

VERSION = "1.0.0"
ENCODE_KEYS = {"scope", "policy_clauses", "scenario_facts", "target_schema", "instructions"}
QUERY_KEYS = {"question", "vocabulary", "target_schema", "instructions"}


def read(path):
    return json.loads(Path(path).read_text())


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def jsonl(path, values):
    with Path(path).open("x") as stream:
        for value in values:
            stream.write(engine.canonical(value) + "\n")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def verify(config_path):
    config = read(config_path)
    require(config["formalizer_version"] == VERSION, "formalizer version differs")
    for path, expected in config["pins"].items():
        require(hash_file(path) == expected, f"prerequisite hash differs: {path}")
    batch = read(config["batch_path"])
    # Full batch/record verification is a prerequisite step, separate from the
    # generator. Its return value contains no source/reference solutions.
    manifest = read(batch["source_dataset_manifest"]["path"])
    require(hash_file(batch["source_dataset_manifest"]["path"]) == batch["source_dataset_manifest"]["sha256"], "manifest differs")
    require(batch["dataset_id"] == manifest["dataset_id"] and batch["dataset_version"] == manifest["dataset_version"], "dataset differs")
    expected = [cid for f in batch["selected_families"] for cid in manifest["families"][f]]
    require(batch["case_ids"] == expected and len(set(expected)) == len(expected), "case selection differs")
    require(batch["languages"] == ["en", "fr"], "both languages required")
    expected_selection = [{"case_id": cid, "language": lang, "source_records": next(c for c in manifest["cases"] if c["case_id"] == cid)["language_versions"][lang]} for cid in expected for lang in batch["languages"]]
    require(batch["selection"] == expected_selection, "record selection differs")
    require(batch["semantic_cases"] == len(expected) and batch["language_versions"] == 2 * len(expected), "denominator differs")
    dataset_dir = Path(batch["source_dataset_manifest"]["path"]).parent
    expected_files = {str(dataset_dir / name): info["sha256"] for name, info in manifest["files"].items()}
    require(batch["source_files"] == expected_files, "source file bindings differ")
    for path, expected_hash in expected_files.items():
        require(hash_file(path) == expected_hash, f"source file hash differs: {path}")
    # Only readable inputs and questions are deserialized here. Proposed
    # references are byte-hashed above and never loaded by this module.
    sources = {}
    for name in ("inputs-v1.jsonl", "questions-v1.jsonl"):
        data = [json.loads(line) for line in (dataset_dir / name).read_text().splitlines()]
        require(len(data) == manifest["files"][name]["records"], "record count differs")
        sources[name] = {}
        for entry in batch["selection"]:
            pointer = entry["source_records"][name]
            row = data[pointer["line"] - 1]
            identity = entry["case_id"], entry["language"]
            require((row["case_id"], row["language"]) == identity, "source identity differs")
            require(hashlib.sha256(engine.canonical(row).encode()).hexdigest() == pointer["canonical_sha256"], "source record hash differs")
            sources[name][identity] = row
    mappings = read(config["mappings_path"])
    require(mappings["version"] == VERSION, "mapping version differs")
    return config, batch, sources, mappings


def encoding_request(source, schema, instructions):
    return {"scope": {k: source["scope"][k] for k in ("id", "text")},
            "policy_clauses": [{k: x[k] for k in ("id", "text")} for x in source["policy_clauses"]],
            "scenario_facts": [{k: x[k] for k in ("id", "text")} for x in source["scenario_facts"]],
            "target_schema": schema, "instructions": instructions}


def encode(request, mappings):
    require(set(request) == ENCODE_KEYS, "unexpected encoding request field")
    require(request["scope"]["text"] in mappings["scopes"], "unsupported readable scope")
    result = {"scope_id": request["scope"]["id"], "entities": {}, "predicates": {}, "facts": [], "rules": [], "incompatibilities": []}

    def declare(atom):
        signature = mappings["predicates"][atom["predicate"]]
        result["predicates"][atom["predicate"]] = signature
        for arg, typ in zip(atom["args"], signature):
            if not arg.startswith("?"):
                require(result["entities"].get(arg, typ) == typ, "inconsistent entity type")
                result["entities"][arg] = typ

    for source_kind, target_kind in (("scenario_facts", "facts"), ("policy_clauses", "policies")):
        for source in request[source_kind]:
            templates = mappings[target_kind].get(source["text"])
            require(templates is not None, f"unmapped {source_kind}: {source['text']}")
            for ordinal, template in enumerate(templates, 1):
                item = deepcopy(template)
                kind = item.pop("kind")
                item.update(id=f"{source['id']}-e{ordinal}", source_ids=[source["id"]])
                atoms = [item["atom"]] if kind == "facts" else item["body"] + [item["head"]] if kind == "rules" else [item["left"], item["right"]]
                for atom in atoms:
                    declare(atom)
                result[kind].append(item)
    return result


def query_request(question, encoding, schema, instructions):
    return {"question": {k: question[k] for k in ("question_id", "text")},
            "vocabulary": {k: encoding[k] for k in ("entities", "predicates")},
            "target_schema": schema, "instructions": instructions}


def translate_query(request, mappings):
    require(set(request) == QUERY_KEYS, "unexpected query request field")
    template = mappings["questions"].get(request["question"]["text"])
    require(template is not None, f"unmapped question: {request['question']['text']}")
    query = deepcopy(template)
    query.update(id=request["question"]["question_id"] + "-q1", question_id=request["question"]["question_id"])
    return {"queries": [query]}


def generate(config_path, output_dir, mode, encoding_path=None, records_output=None):
    config, batch, sources, mappings = verify(config_path)
    directory = Path(output_dir)
    directory.mkdir(exist_ok=False)
    schema = read(config["schema_path"])
    encodings = {}
    if mode == "queries":
        mappings = read(config["query_mappings_path"])
        require(encoding_path is not None, "encodings required")
        rows = [json.loads(line) for line in Path(encoding_path).read_text().splitlines()]
        encodings = {(x["case_id"], x["language"]): x for x in rows}
        wanted = {(x["case_id"], x["language"]) for x in batch["selection"]}
        require(len(rows) == len(encodings) and set(encodings) == wanted, "encoding selection differs")
    requests, responses, outputs, failures, provenance = [], [], [], [], []
    for ordinal, entry in enumerate(batch["selection"], 1):
        k = entry["case_id"], entry["language"]
        identity = {"dataset_id": batch["dataset_id"], "dataset_version": batch["dataset_version"], "case_id": k[0], "language": k[1], "schema_version": engine.SCHEMA}
        source = sources["inputs-v1.jsonl"][k]
        question = sources["questions-v1.jsonl"][k]
        request_id = f"{mode}-{ordinal:03d}"
        request = None
        response = None
        validation_error = None
        try:
            if mode == "encode":
                request = encoding_request(source, schema, config["encoding_instructions"])
                response = encode(request, mappings)
                output = {**identity, **response}
                engine.check_encoding(output)
            else:
                require(not encodings[k].get("generation_error"), "encoding generation failed; query translation blocked")
                request = query_request(question, encodings[k], schema, config["query_instructions"])
                response = translate_query(request, mappings)
                output = {**identity, **response}
                engine.check_queries(encodings[k], output)
        except (ValueError, KeyError, TypeError) as error:
            validation_error = str(error)
            if response is None:
                response = {"error": str(error)}
            output = {**identity, "generation_error": str(error)}
            failures.append({**identity, "request_id": request_id, "error": str(error), "retry": False})
        # Identities are routing metadata outside the allowlisted request body.
        requests.append({"request_id": request_id, "payload": request})
        responses.append({"request_id": request_id, "response": response, "validation_error": validation_error})
        outputs.append(output)
        provenance.append({**identity, "request_id": request_id, "source_record": entry["source_records"]["inputs-v1.jsonl" if mode == "encode" else "questions-v1.jsonl"], "source_file": str(Path(batch["source_dataset_manifest"]["path"]).parent / ("inputs-v1.jsonl" if mode == "encode" else "questions-v1.jsonl")), "scope_id": source["scope"]["id"], "source_ids": [x["id"] for kind in ("policy_clauses", "scenario_facts") for x in source[kind]] if mode == "encode" else [question["question_id"]]})
    name = "encodings-v1.jsonl" if mode == "encode" else "queries-v1.jsonl"
    for filename, data in ((name, outputs), ("requests.jsonl", requests), ("responses.jsonl", responses), ("source-links.jsonl", provenance)):
        jsonl(directory / filename, data)
    if records_output:
        jsonl(records_output, outputs)
    report = {"valid": not failures, "batch_id": batch["batch_id"], "formalizer_version": VERSION, "mode": mode, "language_versions": len(outputs), "failures": failures, "attempts_per_version": 1, "external_model_calls": 0, "paid_calls": 0, "model": "not applicable; local deterministic mappings", "config_sha256": hash_file(config_path), "mappings_sha256": hash_file(config["mappings_path"]), "output_sha256": hash_file(directory / name)}
    if encoding_path:
        report["encodings_sha256"] = hash_file(encoding_path)
    save(directory / "generation-validation-v1.json", report)
    return not failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify", "encode", "queries", "compare"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--encodings")
    parser.add_argument("--records-output")
    parser.add_argument("--original")
    parser.add_argument("--replay")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            config, batch, _, _ = verify(args.config)
            save(args.output, {"valid": True, "batch_id": batch["batch_id"], "semantic_cases": batch["semantic_cases"], "language_versions": batch["language_versions"], "verified_pins": config["pins"], "formalizer_version": VERSION, "config_sha256": hash_file(args.config)})
            ok = True
        elif args.command == "compare":
            names = ["encodings-v1.jsonl", "queries-v1.jsonl", "encoding/requests.jsonl", "encoding/responses.jsonl", "encoding/source-links.jsonl", "encoding/generation-validation-v1.json", "query/requests.jsonl", "query/responses.jsonl", "query/source-links.jsonl", "query/generation-validation-v1.json"]
            checks = {name: hash_file(Path(args.original) / name) == hash_file(Path(args.replay) / name) for name in names}
            reports = [read(Path(p) / "validation-v1.json") for p in (args.original, args.replay)]
            # Fresh output paths differ; validation binds them in each report.
            checks["validation-v1.json (excluding fresh input path bindings)"] = all(r.get("valid") is True for r in reports) and {k: v for k, v in reports[0].items() if k != "input_files_sha256"} == {k: v for k, v in reports[1].items() if k != "input_files_sha256"}
            ok = all(checks.values())
            save(args.output, {"valid": ok, "checks": checks})
        else:
            ok = generate(args.config, args.output, args.command, args.encodings, args.records_output)
        print(engine.canonical({"valid": ok, "output": args.output, "command": args.command}))
        return 0 if ok else 1
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
