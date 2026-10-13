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
    required = {"pipeline_version", "batch_size_chunks", "retries", "provider", "model", "reasoning_effort",
                "pricing", "generation_version", "classification_version"}
    if set(value) != required or value["provider"] not in {"openai", "fixture"}:
        raise ValueError("compilation configuration has an invalid shape")
    if not isinstance(value["batch_size_chunks"], int) or value["batch_size_chunks"] < 1:
        raise ValueError("compilation batch_size_chunks must be a positive integer")
    if not isinstance(value["retries"], int) or value["retries"] < 0:
        raise ValueError("compilation retries must be a non-negative integer")
    if value["reasoning_effort"] not in REASONING_EFFORTS:
        raise ValueError("compilation reasoning_effort is invalid")
    string_keys = {"pipeline_version", "provider", "model", "reasoning_effort", "generation_version",
                   "classification_version"}
    if not all(isinstance(value[key], str) and value[key] for key in string_keys):
        raise ValueError("compilation configuration strings must be nonempty")
    pricing = value["pricing"]
    rate_keys = {"input", "cached_input", "cache_write", "output"}
    if not isinstance(pricing, dict) or set(pricing) != {"rate_card_version", "models"} or \
            not isinstance(pricing["rate_card_version"], str) or not pricing["rate_card_version"] or \
            not isinstance(pricing["models"], dict) or value["model"] not in pricing["models"]:
        raise ValueError("compilation pricing configuration is invalid")
    for model, card in pricing["models"].items():
        if not isinstance(model, str) or not model or not isinstance(card, dict) or set(card) != {
                "short_context_limit_tokens", "short_context_usd_per_million_tokens",
                "long_context_usd_per_million_tokens"} or \
                not isinstance(card["short_context_limit_tokens"], int) or \
                card["short_context_limit_tokens"] < 1:
            raise ValueError("compilation pricing configuration is invalid")
        for key in ("short_context_usd_per_million_tokens", "long_context_usd_per_million_tokens"):
            rates = card[key]
            if not isinstance(rates, dict) or set(rates) != rate_keys or any(
                    not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate < 0
                    for rate in rates.values()):
                raise ValueError("compilation pricing rates are invalid")
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
        self.usage_records = []

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

    @property
    def usage_records(self):
        return self.tool.usage_records

    def candidates(self, chunks, prompt_text, schema):
        input_text = _candidate_input(chunks)
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="claim_candidates", schema=schema)

    def candidate_request_characters(self, chunks, prompt_text, schema):
        return self.tool.request_characters(instructions=prompt_text, input_text=_candidate_input(chunks),
                                            schema_name="claim_candidates", schema=schema)

    def classify(self, text, taxonomy, prompt_text, schema):
        input_text = canonical({"claim": text, "topics": taxonomy["topics"]})
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="topic_classification", schema=schema)


def _candidate_input(chunks):
    evidence = []
    for chunk in chunks:
        for index, ref in enumerate(chunk["evidence"]):
            if "span" in ref:
                evidence.append({"chunk_id": chunk["id"], "evidence_index": index,
                                 "text": chunk["text"], "reference": ref})
    return canonical({"chunks": chunks, "eligible_evidence": evidence})


def _call(method, retries, on_retry=None):
    last = None
    for attempt in range(retries + 1):
        try:
            return method()
        except Exception as exc:  # Preserve the final provider error in the report.
            last = exc
            if attempt < retries:
                if on_retry:
                    on_retry(attempt + 2, retries + 1)
                time.sleep(min(2 ** attempt, 4))
    raise last


def _checkpoint(completed, total):
    return completed in {1, total} or completed * 10 // total > (completed - 1) * 10 // total


def _usage(records):
    return {field: sum(record[field] for record in records) for field in
             ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")}


def _rate_card(config):
    return config["pricing"]["models"][config["model"]]


def _cost(records, rate_card):
    total_cost = 0.0
    for record in records:
        rates = rate_card["short_context_usd_per_million_tokens"] if \
            record["input_tokens"] <= rate_card["short_context_limit_tokens"] else \
            rate_card["long_context_usd_per_million_tokens"]
        uncached_input = max(0, record["input_tokens"] - record["cached_input_tokens"] -
                             record["cache_write_tokens"])
        total_cost += (uncached_input * rates["input"] +
                       record["cached_input_tokens"] * rates["cached_input"] +
                       record["cache_write_tokens"] * rates["cache_write"] +
                       record["output_tokens"] * rates["output"]) / 1_000_000
    return total_cost


def _telemetry(provider, config):
    rate_card = _rate_card(config)
    return {"provider": config["provider"], "model": provider.model_id,
            "requests": len(provider.usage_records), "usage": _usage(provider.usage_records),
            "estimated_cost_usd": _cost(provider.usage_records, rate_card),
            "rate_card": {"version": config["pricing"]["rate_card_version"],
                          "model": config["model"], "rates": copy.deepcopy(rate_card)}}


def _sample_batch_indices(characters, sample_batches):
    """Choose unique batch indices distributed by cumulative request characters."""
    count = len(characters)
    if sample_batches is None:
        return list(range(count))
    if not isinstance(sample_batches, int) or isinstance(sample_batches, bool) or not 1 <= sample_batches <= count:
        raise ValueError(f"sample_batches must be between 1 and {count}")
    if sample_batches == count:
        return list(range(count))
    total = sum(characters)
    midpoints, current = [], 0
    for value in characters:
        midpoints.append(current + value / 2)
        current += value
    available = set(range(count))
    selected = []
    for position in range(sample_batches):
        target = total * (position + 0.5) / sample_batches
        choice = min(available, key=lambda index: (abs(midpoints[index] - target), index))
        available.remove(choice)
        selected.append(choice)
    return sorted(selected)


def _project_full_build(scope, candidates, generation_usage, classification_usage, config):
    if scope["mode"] != "sample" or not generation_usage:
        return None
    rate_card = _rate_card(config)
    generation_scale = scope["generation_request_characters"]["total"] / \
        scope["generation_request_characters"]["selected"]
    generation_cost = _cost(generation_usage, rate_card) * generation_scale
    projected_candidates = len(candidates) * scope["total_chunks"] / scope["selected_chunks"]
    classification_cost = _cost(classification_usage, rate_card) * projected_candidates / len(candidates) \
        if candidates else 0.0
    return {"estimated_cost_usd": generation_cost + classification_cost,
            "generation_estimated_cost_usd": generation_cost,
            "classification_estimated_cost_usd": classification_cost,
            "projected_candidates": projected_candidates,
            "assumptions": {"generation": "observed cost scaled by exact serialized request characters",
                            "classification": "observed cost per candidate scaled by sampled claim yield"}}


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
                   taxonomy_path=ROOT / "config/topics-v1.json", fixture_path=None, review_path=None,
                   on_progress=None, sample_batches=None):
    """Compile one saved chunk collection without mutating its input directory."""
    chunked_dir, output_dir = Path(chunked_dir), Path(output_dir)
    input_path = chunked_dir / "artifact-bundle.json"
    bundle = _read_json(input_path)
    if not bundle["chunks"] or bundle["claims"] or bundle["topics"] or len(bundle["builds"]) != 1:
        raise ValueError("input must be a chunk bundle with no claims or topics")
    upstream = bundle["builds"][0]
    config, taxonomy = _config(config_path), _taxonomy(taxonomy_path)
    generation_prompt, generation_hash = _prompt("claim_generation_v1.txt")
    classification_prompt, classification_hash = _prompt("topic_classification_v1.txt")
    if config["provider"] == "fixture":
        if fixture_path is None:
            raise ValueError("fixture provider requires --fixture")
        provider = FixtureProvider(_read_json(fixture_path))
    else:
        provider = OpenAIProvider(config["model"] or DEFAULT_MODEL, config["reasoning_effort"])
    chunks = bundle["chunks"]
    batch_size = config["batch_size_chunks"]
    all_batches = [chunks[start:start + batch_size] for start in range(0, len(chunks), batch_size)]
    if isinstance(provider, OpenAIProvider):
        request_characters = [provider.candidate_request_characters(batch, generation_prompt, CANDIDATE_SCHEMA)
                              for batch in all_batches]
    else:
        request_characters = [len(_candidate_input(batch)) for batch in all_batches]
    selected_indices = _sample_batch_indices(request_characters, sample_batches)
    selected_batches = [all_batches[index] for index in selected_indices]
    selected_chunks = [chunk for batch in selected_batches for chunk in batch]
    scope = {"mode": "sample" if sample_batches is not None else "full",
             "total_chunks": len(chunks), "selected_chunks": len(selected_chunks),
             "total_batches": len(all_batches), "selected_batch_numbers": [index + 1 for index in selected_indices],
             "selected_chunk_ids": [chunk["id"] for chunk in selected_chunks],
             "generation_request_characters": {"total": sum(request_characters),
                                               "selected": sum(request_characters[index] for index in selected_indices)}}
    config_digest = digest(canonical({"config": config, "taxonomy_sha256": file_digest(taxonomy_path),
                                      "input_bundle_sha256": file_digest(input_path),
                                      "generation_prompt_sha256": generation_hash,
                                      "classification_prompt_sha256": classification_hash, "scope": scope}))
    document_ids = [item["id"] for item in bundle["documents"]]
    build_id = "build:" + digest(config["pipeline_version"] + "\n" + config_digest + "\n" +
                                  "\n".join(sorted(document_ids)))
    generation = {"method": "model", "version": config["generation_version"],
                  "model_id": provider.model_id, "prompt_sha256": generation_hash}
    classifier = {"method": "model", "version": config["classification_version"],
                  "model_id": provider.model_id, "prompt_sha256": classification_hash}
    chunk_map = {item["id"]: item for item in selected_chunks}
    candidates, errors, generation_usage, classification_usage = [], [], [], []
    batches = len(selected_batches)
    if on_progress:
        on_progress({"phase": "generation", "state": "started", "chunks": len(selected_chunks),
                     "source_chunks": len(chunks), "total": batches, "source_total": len(all_batches),
                     "sample": scope["mode"] == "sample"})
    for completed, batch in enumerate(selected_batches, start=1):
        ids = [item["id"] for item in batch]
        usage_start = len(provider.usage_records)
        try:
            response = _call(lambda: provider.candidates(ids if isinstance(provider, FixtureProvider) else batch,
                                                          generation_prompt, CANDIDATE_SCHEMA), config["retries"],
                             lambda attempt, total: on_progress and on_progress(
                                 {"phase": "generation", "state": "retry", "completed": completed,
                                  "total": batches, "attempt": attempt, "attempts": total}))
            Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
            candidates.extend(response["claims"])
        except Exception as exc:
            errors.append({"stage": "generation", "chunk_ids": ids, "error": str(exc)})
        finally:
            generation_usage.extend(provider.usage_records[usage_start:])
        if on_progress and _checkpoint(completed, batches):
            on_progress({"phase": "generation", "state": "progress", "completed": completed,
                         "total": batches, "candidates": len(candidates)})
    if on_progress:
        on_progress({"phase": "generation", "state": "complete", "candidates": len(candidates)})
    claims, unclassified = [], []
    valid_topics = {item["id"] for item in taxonomy["topics"]}
    if on_progress:
        on_progress({"phase": "classification", "state": "started", "total": len(candidates)})
    for index, candidate in enumerate(candidates, start=1):
        usage_start = len(provider.usage_records)
        try:
            classification = _call(lambda: provider.classify(candidate["text"], taxonomy, classification_prompt,
                                                              CLASSIFICATION_SCHEMA), config["retries"],
                                   lambda attempt, total: on_progress and on_progress(
                                       {"phase": "classification", "state": "retry", "completed": index,
                                        "total": len(candidates), "attempt": attempt, "attempts": total}))
            if classification.get("topic_id") is None:
                unclassified.append(candidate["text"])
                continue
            if classification.get("topic_id") not in valid_topics:
                raise ValueError("classifier returned an uncurated topic ID")
            claims.append(_claim(build_id, candidate, classification, generation, classifier, chunk_map))
        except Exception as exc:
            errors.append({"stage": "classification", "claim": candidate.get("text"), "error": str(exc)})
        finally:
            classification_usage.extend(provider.usage_records[usage_start:])
        if on_progress and _checkpoint(index, len(candidates)):
            on_progress({"phase": "classification", "state": "progress", "completed": index,
                         "total": len(candidates), "claims": len(claims)})
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
    if on_progress:
        on_progress({"phase": "export", "state": "started"})
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
    telemetry = _telemetry(provider, config)
    report = {"build_id": build_id, "upstream_chunk_build_id": upstream["id"], "documents": len(document_ids),
              "chunks": len(chunks), "compilation_scope": scope, "claims": len(claims), "topics": len(topics),
              "errors": errors, "telemetry": telemetry,
              "projected_full_build": _project_full_build(scope, candidates, generation_usage,
                                                           classification_usage, config),
              "unclassified_candidates": unclassified,
              "review": review, "status": "partial" if errors else "complete"}
    _write_json(output_dir / "report.json", report)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    manifest = {"manifest_version": 1, "compilation_build_id": build_id,
                "upstream_chunk_build_id": upstream["id"], "input_bundle_sha256": file_digest(input_path),
                "config_sha256": config_digest, "taxonomy_sha256": file_digest(taxonomy_path),
                "generation_prompt_sha256": generation_hash, "classification_prompt_sha256": classification_hash,
                "compilation_scope": scope,
                "outputs": [{"path": str(path.relative_to(output_dir)), "sha256": file_digest(path)} for path in files]}
    _write_json(output_dir / "build-manifest.json", manifest)
    if on_progress:
        on_progress({"phase": "complete", "state": report["status"],
                     "telemetry": telemetry, "projection": report["projected_full_build"]})
    return report
