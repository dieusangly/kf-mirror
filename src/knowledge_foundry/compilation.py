"""Compile immutable chunk bundles into evidence-linked draft topic records."""

import copy
import hashlib
import json
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge_foundry.openai_tools import DEFAULT_MODEL, REASONING_EFFORTS, OpenAIResponsesTool


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
CANDIDATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims"],
    "properties": {"claims": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "evidence"],
        "properties": {"text": {"type": "string", "minLength": 1},
                       "evidence": {"type": "array", "minItems": 1, "items": {
                           "type": "object", "additionalProperties": False,
                           "required": ["chunk_id", "evidence_index"],
                           "properties": {"chunk_id": {"type": "string", "minLength": 1},
                                          "evidence_index": {"type": "integer", "minimum": 0}}}}}}}},
}
CLASSIFICATION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["topic_id"],
    "properties": {"topic_id": {"type": ["string", "null"], "minLength": 1}},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prompt(name):
    value = (ROOT / "src" / "knowledge_foundry" / "prompts" / name).read_text(encoding="utf-8")
    return value, digest(value)


def _config(path):
    value = _read_json(path)
    required = {"pipeline_version", "batch_size", "retries", "provider", "model", "reasoning_effort",
                "generation_version", "classification_version"}
    if set(value) != required or value["provider"] not in {"openai", "fixture"}:
        raise ValueError("compilation configuration has an invalid shape")
    if not isinstance(value["batch_size"], int) or value["batch_size"] < 1:
        raise ValueError("compilation batch_size must be a positive integer")
    if not isinstance(value["retries"], int) or value["retries"] < 0:
        raise ValueError("compilation retries must be a non-negative integer")
    if value["reasoning_effort"] not in REASONING_EFFORTS:
        raise ValueError("compilation reasoning_effort is invalid")
    if not all(isinstance(value[key], str) and value[key] for key in required - {"batch_size", "retries"}):
        raise ValueError("compilation configuration strings must be nonempty")
    return value


def _taxonomy(path):
    value = _read_json(path)
    if set(value) != {"taxonomy_version", "topics"} or not isinstance(value["taxonomy_version"], str):
        raise ValueError("topic taxonomy has an invalid shape")
    topics = value["topics"]
    if not isinstance(topics, list) or not topics:
        raise ValueError("topic taxonomy must contain topics")
    ids = []
    for topic in topics:
        if set(topic) != {"id", "title", "scope"} or not all(isinstance(topic[key], str) and topic[key]
                                                                  for key in topic):
            raise ValueError("topic taxonomy entries require id, title, and scope")
        if not topic["id"].startswith("topic:"):
            raise ValueError("topic IDs must start with topic:")
        ids.append(topic["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("topic taxonomy has duplicate IDs")
    return value


class FixtureProvider:
    """Deterministic adapter used by tests and offline demonstration builds."""

    model_id = "fixture-model"

    def __init__(self, fixture):
        self.fixture = fixture

    def candidates(self, chunk_ids, _prompt_text, _schema):
        key = ",".join(chunk_ids)
        return self.fixture.get("candidate_batches", {}).get(key, {"claims": []})

    def classify(self, text, _taxonomy, _prompt_text, _schema):
        try:
            return {"topic_id": self.fixture["classifications"][text]}
        except KeyError as exc:
            raise ValueError(f"fixture has no classification for claim: {text}") from exc


class OpenAIProvider:
    def __init__(self, model, reasoning_effort):
        self.tool = OpenAIResponsesTool(model=model or DEFAULT_MODEL, reasoning_effort=reasoning_effort)
        self.model_id = self.tool.model

    def candidates(self, chunks, prompt_text, schema):
        evidence = []
        for chunk in chunks:
            for index, ref in enumerate(chunk["evidence"]):
                if "span" in ref:
                    evidence.append({"chunk_id": chunk["id"], "evidence_index": index,
                                     "text": chunk["text"], "reference": ref})
        input_text = canonical({"chunks": chunks, "eligible_evidence": evidence})
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="claim_candidates", schema=schema)

    def classify(self, text, taxonomy, prompt_text, schema):
        input_text = canonical({"claim": text, "topics": taxonomy["topics"]})
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="topic_classification", schema=schema)


def _call(method, retries):
    last = None
    for attempt in range(retries + 1):
        try:
            return method()
        except Exception as exc:  # Preserve the final provider error in the report.
            last = exc
            if attempt < retries:
                time.sleep(min(2 ** attempt, 4))
    raise last


def _reference(chunk_map, item):
    chunk_id = item["chunk_id"]
    if chunk_id not in chunk_map:
        raise ValueError(f"candidate names an unknown chunk: {chunk_id}")
    refs = chunk_map[chunk_id]["evidence"]
    index = item["evidence_index"]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(refs):
        raise ValueError(f"candidate evidence index is invalid: {chunk_id}")
    ref = refs[index]
    if "span" not in ref:
        raise ValueError("candidate evidence must select an exact element span")
    return copy.deepcopy(ref)


def _claim(build_id, candidate, classification, generation, classifier, chunk_map):
    Draft202012Validator(CANDIDATE_SCHEMA).validate({"claims": [candidate]})
    Draft202012Validator(CLASSIFICATION_SCHEMA).validate(classification)
    evidence = [_reference(chunk_map, value) for value in candidate["evidence"]]
    if len({canonical(item) for item in evidence}) != len(evidence):
        raise ValueError("candidate repeats evidence")
    chunk_ids = []
    for value in candidate["evidence"]:
        if value["chunk_id"] not in chunk_ids:
            chunk_ids.append(value["chunk_id"])
    text = candidate["text"].strip()
    if not text:
        raise ValueError("candidate claim text is blank")
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "claim:" + digest(build_id + "\n" + canonical(evidence) + "\n" + text),
        "topic_id": classification["topic_id"], "build_id": build_id, "chunk_ids": chunk_ids,
        "text": text, "evidence": evidence, "grounding_review_status": "draft",
        "classification_review_status": "draft", "generation": generation,
        "classification": classifier,
    }


def _apply_review(claims, topics, review_path, build_id):
    if review_path is None:
        return {"applied": False, "rejection_reasons": {}}
    review = _read_json(review_path)
    if review.get("build_id") != build_id or not isinstance(review.get("claims"), list):
        raise ValueError("review file must name this build and contain claims")
    by_claim = {item["id"]: item for item in claims}
    reasons = {}
    for item in review["claims"]:
        claim = by_claim.get(item.get("id"))
        if not claim or item.get("grounding_review_status") not in {"draft", "verified", "rejected"} or \
                item.get("classification_review_status") not in {"draft", "verified", "rejected"}:
            raise ValueError("review contains an invalid claim status")
        claim["grounding_review_status"] = item["grounding_review_status"]
        claim["classification_review_status"] = item["classification_review_status"]
        if item.get("reason"):
            reasons[claim["id"]] = str(item["reason"])
    by_topic = {item["id"]: item for item in topics}
    for item in review.get("topics", []):
        topic = by_topic.get(item.get("id"))
        if not topic or item.get("review_status") not in {"draft", "verified", "rejected"}:
            raise ValueError("review contains an invalid topic status")
        if item["review_status"] == "verified":
            assigned = [claim for claim in claims if claim["topic_id"] == topic["id"]]
            if not all(claim["grounding_review_status"] == claim["classification_review_status"] == "verified"
                       for claim in assigned):
                raise ValueError("a verified topic requires fully verified claims")
        topic["review_status"] = item["review_status"]
    return {"applied": True, "rejection_reasons": reasons}


def _topic_records(claims, taxonomy, build_id):
    result = []
    for item in taxonomy["topics"]:
        assigned = sorted(claim["id"] for claim in claims if claim["topic_id"] == item["id"])
        if not assigned:
            continue
        summary = "Thème à l'état de brouillon. Examinez les assertions candidates et leurs preuves de source avant de vous y fier."
        result.append({"schema_version": SCHEMA_VERSION, "id": item["id"], "title": item["title"],
                       "summary": summary, "claim_ids": assigned,
                       "revision": digest(item["title"] + "\n" + summary + "\n" + "\n".join(assigned)),
                       "build_id": build_id, "review_status": "draft",
                       "generation": {"method": "manual", "version": taxonomy["taxonomy_version"]}})
    return result


def compile_bundle(chunked_dir, output_dir, config_path=ROOT / "config/compilation.json",
                   taxonomy_path=ROOT / "config/topics-v1.json", fixture_path=None, review_path=None):
    """Compile one saved chunk collection without mutating its input directory."""
    chunked_dir, output_dir = Path(chunked_dir), Path(output_dir)
    input_path = chunked_dir / "artifact-bundle.json"
    bundle = _read_json(input_path)
    if not bundle["chunks"] or bundle["claims"] or bundle["topics"] or len(bundle["builds"]) != 1:
        raise ValueError("input must be a Task 5 chunk bundle with no claims or topics")
    upstream = bundle["builds"][0]
    config, taxonomy = _config(config_path), _taxonomy(taxonomy_path)
    generation_prompt, generation_hash = _prompt("claim_generation_v1.txt")
    classification_prompt, classification_hash = _prompt("topic_classification_v1.txt")
    config_digest = digest(canonical({"config": config, "taxonomy_sha256": file_digest(taxonomy_path),
                                      "input_bundle_sha256": file_digest(input_path),
                                      "generation_prompt_sha256": generation_hash,
                                      "classification_prompt_sha256": classification_hash}))
    document_ids = [item["id"] for item in bundle["documents"]]
    build_id = "build:" + digest(config["pipeline_version"] + "\n" + config_digest + "\n" +
                                  "\n".join(sorted(document_ids)))
    if config["provider"] == "fixture":
        if fixture_path is None:
            raise ValueError("fixture provider requires --fixture")
        provider = FixtureProvider(_read_json(fixture_path))
    else:
        provider = OpenAIProvider(config["model"] or DEFAULT_MODEL, config["reasoning_effort"])
    generation = {"method": "model", "version": config["generation_version"],
                  "model_id": provider.model_id, "prompt_sha256": generation_hash}
    classifier = {"method": "model", "version": config["classification_version"],
                  "model_id": provider.model_id, "prompt_sha256": classification_hash}
    chunk_map = {item["id"]: item for item in bundle["chunks"]}
    candidates, errors = [], []
    chunks = bundle["chunks"]
    for start in range(0, len(chunks), config["batch_size"]):
        batch = chunks[start:start + config["batch_size"]]
        ids = [item["id"] for item in batch]
        try:
            response = _call(lambda: provider.candidates(ids if isinstance(provider, FixtureProvider) else batch,
                                                          generation_prompt, CANDIDATE_SCHEMA), config["retries"])
            Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
            candidates.extend(response["claims"])
        except Exception as exc:
            errors.append({"stage": "generation", "chunk_ids": ids, "error": str(exc)})
    claims, unclassified = [], []
    valid_topics = {item["id"] for item in taxonomy["topics"]}
    for candidate in candidates:
        try:
            classification = _call(lambda: provider.classify(candidate["text"], taxonomy, classification_prompt,
                                                              CLASSIFICATION_SCHEMA), config["retries"])
            if classification.get("topic_id") is None:
                unclassified.append(candidate["text"])
                continue
            if classification.get("topic_id") not in valid_topics:
                raise ValueError("classifier returned an uncurated topic ID")
            claims.append(_claim(build_id, candidate, classification, generation, classifier, chunk_map))
        except Exception as exc:
            errors.append({"stage": "classification", "claim": candidate.get("text"), "error": str(exc)})
    claims.sort(key=lambda item: item["id"])
    if len({item["id"] for item in claims}) != len(claims):
        raise ValueError("compilation produced duplicate claims")
    topics = _topic_records(claims, taxonomy, build_id)
    review = _apply_review(claims, topics, review_path, build_id)
    result = copy.deepcopy(bundle)
    result["claims"] = claims
    result["topics"] = topics
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id,
                               "pipeline_version": config["pipeline_version"], "config_sha256": config_digest,
                               "input_document_ids": document_ids, "output_chunk_ids": [],
                               "output_claim_ids": [item["id"] for item in claims],
                               "output_topic_ids": [item["id"] for item in topics],
                               "status": "partial" if errors else "complete"})
    from scripts.validate_artifact_examples import check
    check(result)
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    _write_json(output_dir / "artifact-bundle.json", result)
    for name, records in (("chunks.jsonl", result["chunks"]), ("claims.jsonl", claims)):
        (output_dir / name).write_text("".join(canonical(item) + "\n" for item in records), encoding="utf-8")
    topic_dir = output_dir / "topics"
    topic_dir.mkdir()
    by_claim = {item["id"]: item for item in claims}
    for topic in topics:
        slug = topic["id"].removeprefix("topic:")
        view = {"view_version": 1, "build_id": build_id, "topic": topic,
                "claims": [by_claim[item] for item in topic["claim_ids"]]}
        _write_json(topic_dir / f"{slug}.json", view)
        lines = [f"# {topic['title']}", "", f"Status: `{topic['review_status']}`", "", topic["summary"], "", "## Claims", ""]
        for claim in view["claims"]:
            lines.extend([f"- `{claim['id']}` — {claim['text']} "
                          f"(grounding: {claim['grounding_review_status']}; classification: {claim['classification_review_status']})"])
        (topic_dir / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = {"build_id": build_id, "upstream_chunk_build_id": upstream["id"], "documents": len(document_ids),
              "chunks": len(chunks), "claims": len(claims), "topics": len(topics), "errors": errors,
              "unclassified_candidates": unclassified,
              "review": review, "status": "partial" if errors else "complete"}
    _write_json(output_dir / "report.json", report)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    manifest = {"manifest_version": 1, "compilation_build_id": build_id,
                "upstream_chunk_build_id": upstream["id"], "input_bundle_sha256": file_digest(input_path),
                "config_sha256": config_digest, "taxonomy_sha256": file_digest(taxonomy_path),
                "generation_prompt_sha256": generation_hash, "classification_prompt_sha256": classification_hash,
                "outputs": [{"path": str(path.relative_to(output_dir)), "sha256": file_digest(path)} for path in files]}
    _write_json(output_dir / "build-manifest.json", manifest)
    return report
