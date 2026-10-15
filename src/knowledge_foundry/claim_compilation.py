"""Generate grounded claim candidates and classify them into draft topics."""

import copy
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from knowledge_foundry.evidence import claim_evidence_references
from knowledge_foundry.openai_tools import DEFAULT_MODEL, REASONING_EFFORTS, OpenAIResponsesTool


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
ARTIFACT_SCHEMA = json.loads((ROOT / "schemas" / "artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))
CANDIDATE_ARTIFACT_SCHEMA = json.loads(
    (ROOT / "schemas" / "claim-candidate-artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))
CANDIDATE_VALIDATION_SCHEMA = json.loads(json.dumps(CANDIDATE_ARTIFACT_SCHEMA).replace(
    "artifact-schema-v1.0.0.json#/$defs/", "#/$defs/"))
CANDIDATE_VALIDATION_SCHEMA["$defs"] = ARTIFACT_SCHEMA["$defs"]
CANDIDATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims"],
    "properties": {"claims": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "evidence"],
        "properties": {"text": {"type": "string", "minLength": 1},
                       "evidence": {"type": "array", "minItems": 1, "items": {
                           "type": "string", "pattern": "^E[1-9][0-9]*$"}}}}}},
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


def _write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_jsonl_atomic(path, records):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(canonical(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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

    def classify(self, context, _taxonomy, _prompt_text, _schema):
        text = context["claim"]
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

    def candidates(self, input_text, prompt_text, schema):
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="claim_candidates", schema=schema)

    def candidate_request_characters(self, input_text, prompt_text, schema):
        return self.tool.request_characters(instructions=prompt_text, input_text=input_text,
                                            schema_name="claim_candidates", schema=schema)

    def classify(self, context, taxonomy, prompt_text, schema):
        input_text = canonical({**context, "topics": taxonomy["topics"]})
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="topic_classification", schema=schema)


def _passage(ref, element_map):
    element = element_map[ref["element_id"]]
    span = ref["span"]
    if not 0 <= span["start"] < span["end"] <= len(element["text"]):
        raise ValueError("evidence span is out of bounds")
    return {"text": element["text"][span["start"]:span["end"]],
            "kind": element["kind"], "location": element["location"]}


def _evidence_choices(chunks, element_map):
    choices = {}
    for chunk in chunks:
        for ref in claim_evidence_references(chunk, element_map):
            if "span" in ref:
                choices[f"E{len(choices) + 1}"] = {
                    "chunk_id": chunk["id"], "reference": ref, **_passage(ref, element_map)}
    return choices


def _candidate_input(chunks, element_map):
    choices = _evidence_choices(chunks, element_map)
    return canonical({"chunks": [{"chunk_id": chunk["id"], "text": chunk["text"]} for chunk in chunks],
                      "eligible_evidence": [{"id": label, **{key: value for key, value in item.items()
                                                            if key != "reference"}}
                                            for label, item in choices.items()]})


def _classification_input(candidate, chunk_map, element_map, document_map, source_map):
    documents = [document_map[item] for item in sorted({ref["document_id"] for ref in candidate["evidence"]})]
    return {"claim": candidate["text"],
            "evidence": [{**ref, **_passage(ref, element_map)} for ref in candidate["evidence"]],
            "chunks": [{"chunk_id": item, "text": chunk_map[item]["text"]} for item in candidate["chunk_ids"]],
            "sources": [source_map[(doc["source_id"], doc["source_version"])] for doc in documents]}


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


def _telemetry(provider, config, usage_records=None):
    rate_card = _rate_card(config)
    usage_records = provider.usage_records if usage_records is None else usage_records
    return {"provider": config["provider"], "model": provider.model_id,
            "requests": len(usage_records), "usage": _usage(usage_records),
            "estimated_cost_usd": _cost(usage_records, rate_card),
            "rate_card": {"version": config["pricing"]["rate_card_version"],
                          "model": config["model"], "rates": copy.deepcopy(rate_card)}}


def _subset_batch_indices(characters, subset_batches, batch_numbers=None):
    """Choose unique batch indices distributed by cumulative request characters."""
    count = len(characters)
    if batch_numbers is not None:
        if subset_batches is not None:
            raise ValueError("batch_numbers and subset_batches are mutually exclusive")
        if not isinstance(batch_numbers, list) or not batch_numbers or any(
                not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= count
                for number in batch_numbers) or len(set(batch_numbers)) != len(batch_numbers):
            raise ValueError(f"batch_numbers must be unique integers between 1 and {count}")
        return sorted(number - 1 for number in batch_numbers)
    if subset_batches is None:
        return list(range(count))
    if not isinstance(subset_batches, int) or isinstance(subset_batches, bool) or not 1 <= subset_batches <= count:
        raise ValueError(f"subset_batches must be between 1 and {count}")
    if subset_batches == count:
        return list(range(count))
    total = sum(characters)
    midpoints, current = [], 0
    for value in characters:
        midpoints.append(current + value / 2)
        current += value
    available = set(range(count))
    selected = []
    for position in range(subset_batches):
        target = total * (position + 0.5) / subset_batches
        choice = min(available, key=lambda index: (abs(midpoints[index] - target), index))
        available.remove(choice)
        selected.append(choice)
    return sorted(selected)


def _candidate_record(generation_build_id, candidate, generation, choices):
    Draft202012Validator(CANDIDATE_SCHEMA).validate({"claims": [candidate]})
    if any(label not in choices for label in candidate["evidence"]):
        raise ValueError("candidate selects an unknown evidence label")
    selected = [choices[label] for label in candidate["evidence"]]
    evidence = [copy.deepcopy(item["reference"]) for item in selected]
    if len({canonical(item) for item in evidence}) != len(evidence):
        raise ValueError("candidate repeats evidence")
    chunk_ids = []
    for value in selected:
        if value["chunk_id"] not in chunk_ids:
            chunk_ids.append(value["chunk_id"])
    text = candidate["text"].strip()
    if not text:
        raise ValueError("candidate claim text is blank")
    if re.search(r"(?:^|[.!?;]\s+)(?:pour ce service\s*(?=,|$)|il en va de même\b|"
                 r"pour le reste de ce schéma\b)", text, re.IGNORECASE):
        raise ValueError(f"candidate has an unresolved contextual reference; name its subject and rule: {text}")
    # Look at the available passages of the cited chunks too: selecting only a
    # heading must not hide prerequisites in the accompanying paragraph.
    _check_fallback_conditions(text, " ".join(item["text"] for item in choices.values()
                                             if item["chunk_id"] in chunk_ids))
    result = {"schema_version": SCHEMA_VERSION,
              "id": "candidate:" + digest(generation_build_id + "\n" + canonical(evidence) + "\n" + text),
              "generation_build_id": generation_build_id, "chunk_ids": chunk_ids, "text": text,
              "evidence": evidence, "grounding_review_status": "draft", "generation": generation}
    Draft202012Validator(CANDIDATE_VALIDATION_SCHEMA).validate(result)
    return result


def _plain_text(text):
    text = text.lower().replace("’", "'").replace("œ", "oe")
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text)
                            if not unicodedata.combining(c)).split())


def _check_fallback_conditions(text, evidence_text):
    """Check paired beneficial-owner fallback prerequisites; not general grounding."""
    claim, evidence = _plain_text(text), _plain_text(evidence_text)
    if "beneficiaire" not in claim or "dernier ressort" not in claim:
        return
    failed_identification = r"aucune personne physique.{0,100}identifi|recherches?\s+infructueuses?"
    no_suspicion = r"n'a pas de soupcon|absence de soupcon|sans soupcon"
    if re.search(failed_identification, evidence) and re.search(no_suspicion, evidence):
        if not re.search(failed_identification, claim) or not re.search(no_suspicion, claim):
            raise ValueError("fallback claim omits explicit prerequisites from its evidence: "
                             "state unsuccessful identification and absence of suspicion in this same claim, "
                             "with the relevant source context; another claim or 'conditions réunies' "
                             "does not supply them: " + text)


def _check_substantive_footnotes(records, choices):
    """Check the specific 'tient compte de ce que' footnote pattern."""
    cited = {canonical(ref) for record in records for ref in record["evidence"]}
    for label, passage in choices.items():
        if passage["kind"] == "footnote" and "tient compte de ce que" in _plain_text(passage["text"]) \
                and canonical(passage["reference"]) not in cited:
            raise ValueError(f"substantive footnote {label} was omitted; extract its qualified statement "
                             "with its scope and exact evidence: " + passage["text"])


def _check_reporting_assignment(response, context, valid_topics):
    """Catch known DS procedure assignments when that curated topic is available."""
    topic = "topic:suspicious-transaction-reporting"
    if topic not in valid_topics:
        return

    def procedural_relationship(text):
        text = _plain_text(text)
        return ("plainte" in text and
                ("declaration de soupcon" in text or re.search(r"\bds\b", text)) and
                re.search(r"independan|substitu|obstacle", text))

    if procedural_relationship(context["claim"]) and any(
            procedural_relationship(item["text"]) for item in context["evidence"]) and \
            response["topic_id"] != topic:
        raise ValueError("explicit DS/complaint procedural relationship belongs to " + topic +
                         "; reconsider the assignment using the cited passage and curated scope")

    def ermes_authentication(text):
        text = _plain_text(text)
        return (re.search(r"\bermes\b", text) and "authentification" in text and
                re.search(r"\bsimple\b|\bforte\b|certificat|identifiant|mot de passe", text))

    if ermes_authentication(context["claim"]) and any(
            ermes_authentication(item["text"]) for item in context["evidence"]) and \
            response["topic_id"] != topic:
        raise ValueError("ERMES authentication procedure belongs to " + topic +
                         "; both simple and strong authentication are within its ERMES scope; "
                         "reconsider the assignment using the cited passage")


def _claim(build_id, candidate, classification, classifier):
    Draft202012Validator(CLASSIFICATION_SCHEMA).validate(classification)
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "claim:" + digest(build_id + "\n" + canonical(candidate["evidence"]) + "\n" + candidate["text"]),
        "topic_id": classification["topic_id"], "build_id": build_id, "chunk_ids": candidate["chunk_ids"],
        "text": candidate["text"], "evidence": candidate["evidence"],
        "grounding_review_status": candidate["grounding_review_status"],
        "classification_review_status": "draft", "generation": candidate["generation"],
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


def _provider(config, fixture_path):
    if config["provider"] == "fixture":
        if fixture_path is None:
            raise ValueError("fixture provider requires --fixture")
        return FixtureProvider(_read_json(fixture_path))
    return OpenAIProvider(config["model"] or DEFAULT_MODEL, config["reasoning_effort"])


def _write_jsonl(path, records):
    Path(path).write_text("".join(canonical(item) + "\n" for item in records), encoding="utf-8")


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def _build_id(pipeline_version, config_digest, document_ids):
    return "build:" + digest(pipeline_version + "\n" + config_digest + "\n" + "\n".join(sorted(document_ids)))


def _project_generation(scope, usage, config):
    if scope["mode"] != "subset" or scope.get("selection") == "explicit" or not usage:
        return None
    estimated_cost = _cost(usage, _rate_card(config)) * scope["generation_request_characters"]["total"] / \
        scope["generation_request_characters"]["selected"]
    return {"estimated_cost_usd": estimated_cost,
            "assumption": "observed cost scaled by exact serialized generation-request characters"}


def _generation_coverage(bundle, candidates, scope, errors):
    """Account for selected chunks cited by candidates, without judging extraction quality."""
    selected = set(scope["selected_chunk_ids"])
    chunk_map = {item["id"]: item for item in bundle["chunks"]}
    document_map = {item["id"]: item for item in bundle["documents"]}
    failed = {chunk_id for error in errors if error.get("stage") == "generation"
              for chunk_id in error["chunk_ids"]} & selected
    element_map = {item["id"]: item for item in bundle.get("elements", [])}
    references = {chunk_id: {canonical(ref) for ref in claim_evidence_references(chunk, element_map)}
                  for chunk_id, chunk in chunk_map.items()}
    cited = {chunk_id: set() for chunk_id in selected}
    for candidate in candidates:
        evidence = {canonical(ref) for ref in candidate["evidence"]}
        for chunk_id in candidate["chunk_ids"]:
            if chunk_id not in selected:
                raise ValueError("coverage candidate chunk is outside the selected scope")
            if evidence & references[chunk_id]:
                cited[chunk_id].add(candidate["id"])
    chunks = []
    for chunk in bundle["chunks"]:
        chunk_id = chunk["id"]
        if chunk_id not in selected:
            continue
        document = document_map[chunk["document_id"]]
        eligible = sum("span" in ref for ref in claim_evidence_references(chunk, element_map))
        status = ("generation_failed" if chunk_id in failed else "cited" if cited[chunk_id] else
                  "no_eligible_evidence" if not eligible else "no_candidates")
        chunks.append({"chunk_id": chunk_id, "document_id": chunk["document_id"],
                       "source_id": document["source_id"], "source_version": document["source_version"],
                       "candidate_count": len(cited[chunk_id]), "eligible_evidence_count": eligible,
                       "status": status})
    sources = []
    for source in bundle["sources"]:
        rows = [row for row in chunks if (row["source_id"], row["source_version"]) ==
                (source["id"], source["version"])]
        source_candidates = set().union(*(cited[row["chunk_id"]] for row in rows))
        sources.append({"source_id": source["id"], "source_version": source["version"],
                        "title": source["title"], "selected_chunks": len(rows),
                        "cited_chunks": sum(row["candidate_count"] > 0 for row in rows),
                        "candidate_count": len(source_candidates),
                        "status": "not_selected" if not rows else
                                  "has_candidates" if source_candidates else "no_candidates"})
    missing_chunks = [row["chunk_id"] for row in chunks if not row["candidate_count"]]
    missing_sources = [row for row in sources if row["status"] == "no_candidates"]
    return {
        "coverage_version": 1,
        "status": "needs_review" if missing_chunks or failed else "no_gaps_detected",
        "summary": {"selected_chunks": len(chunks), "cited_chunks": len(chunks) - len(missing_chunks),
                    "chunks_without_candidates": len(missing_chunks),
                    "failed_chunks": sum(row["status"] == "generation_failed" for row in chunks),
                    "chunks_without_eligible_evidence": sum(not row["eligible_evidence_count"] for row in chunks),
                    "not_selected_chunks": len(bundle["chunks"]) - len(chunks),
                    "selected_sources": sum(row["status"] != "not_selected" for row in sources),
                    "sources_with_candidates": sum(row["status"] == "has_candidates" for row in sources),
                    "sources_without_candidates": len(missing_sources),
                    "not_selected_sources": sum(row["status"] == "not_selected" for row in sources)},
        "selected_chunk_ids_without_candidates": missing_chunks,
        "selected_source_ids_without_candidates": sorted({row["source_id"] for row in missing_sources}),
        "chunks": chunks, "sources": sources,
    }


def _candidate_manifest(candidate_dir):
    candidate_dir = Path(candidate_dir)
    manifest_path = candidate_dir / "build-manifest.json"
    if not manifest_path.is_file() and (candidate_dir / "generation-checkpoint.json").is_file():
        raise ValueError("candidate generation is incomplete; rerun generate_claims.py with --resume")
    manifest = _read_json(manifest_path)
    candidate_path = candidate_dir / "claim-candidates.jsonl"
    output_hashes = {item.get("path"): item.get("sha256") for item in manifest.get("outputs", [])}
    if manifest.get("stage") != "claim-generation" or \
            manifest.get("candidate_sha256") != file_digest(candidate_path) or \
            output_hashes.get("artifact-bundle.json") != file_digest(candidate_dir / "artifact-bundle.json") or \
            output_hashes.get("claim-candidates.jsonl") != file_digest(candidate_path):
        raise ValueError("candidate artifact manifest is invalid")
    return manifest, candidate_path, manifest_path


def _validate_candidate(candidate, generation_build_id, chunk_map, element_map):
    Draft202012Validator(CANDIDATE_VALIDATION_SCHEMA).validate(candidate)
    if candidate["generation_build_id"] != generation_build_id:
        raise ValueError("candidate generation build mismatch")
    chunks = [chunk_map.get(chunk_id) for chunk_id in candidate["chunk_ids"]]
    if not all(chunks):
        raise ValueError("candidate chunk missing")
    supporting = {canonical([item]) for chunk in chunks
                  for item in claim_evidence_references(chunk, element_map)}
    for evidence in candidate["evidence"]:
        if "span" not in evidence or canonical([evidence]) not in supporting:
            raise ValueError("candidate evidence is not supported by its chunks")
    expected = "candidate:" + digest(generation_build_id + "\n" + canonical(candidate["evidence"]) +
                                      "\n" + candidate["text"])
    if candidate["id"] != expected:
        raise ValueError("candidate ID mismatch")


def _generation_identity(build_id, input_path, config_digest, generation_hash, scope):
    return {"checkpoint_version": 1, "stage": "claim-generation", "generation_build_id": build_id,
            "input_bundle_sha256": file_digest(input_path), "config_sha256": config_digest,
            "generation_prompt_sha256": generation_hash, "compilation_scope": scope}


def _load_generation_checkpoints(output_dir, identity, selected_batches, chunk_map, element_map):
    checkpoint_path = output_dir / "generation-checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("output directory is not a resumable claim-generation run")
    checkpoint = _read_json(checkpoint_path)
    if any(checkpoint.get(key) != value for key, value in identity.items()):
        raise ValueError("generation checkpoint does not match the requested input, configuration, prompt, or scope")
    checkpoint_dir = output_dir / "generation-checkpoints"
    batch_by_number = {number: batch for number, batch in enumerate(selected_batches, start=1)}
    candidates, errors, usage, completed = [], [], [], set()
    for path in sorted(checkpoint_dir.glob("batch-*.json")):
        record = _read_json(path)
        number = record.get("batch_number")
        batch = batch_by_number.get(number)
        if number in completed or batch is None or record.get("chunk_ids") != [item["id"] for item in batch]:
            raise ValueError("generation batch checkpoint is invalid")
        if not isinstance(record.get("candidates"), list) or not isinstance(record.get("errors"), list) or \
                not isinstance(record.get("usage_records"), list):
            raise ValueError("generation batch checkpoint is invalid")
        for candidate in record["candidates"]:
            _validate_candidate(candidate, identity["generation_build_id"], chunk_map, element_map)
        candidates.extend(record["candidates"])
        errors.extend(record["errors"])
        usage.extend(record["usage_records"])
        completed.add(number)
    return candidates, errors, usage, completed


def generate_claims(chunked_dir, output_dir, config_path=ROOT / "config/compilation.json", fixture_path=None,
                    on_progress=None, subset_batches=None, resume=False, batch_numbers=None):
    """Generate durable, grounded candidate claims from one immutable chunk bundle."""
    chunked_dir, output_dir = Path(chunked_dir), Path(output_dir)
    input_path = chunked_dir / "artifact-bundle.json"
    bundle = _read_json(input_path)
    if not bundle["chunks"] or bundle["claims"] or bundle["topics"] or len(bundle["builds"]) != 1:
        raise ValueError("input must be a chunk bundle with no claims or topics")
    config = _config(config_path)
    generation_prompt, generation_hash = _prompt("claim_generation_v1.txt")
    provider = _provider(config, fixture_path)
    chunks, document_ids = bundle["chunks"], [item["id"] for item in bundle["documents"]]
    batch_size = config["batch_size_chunks"]
    all_batches = [chunks[start:start + batch_size] for start in range(0, len(chunks), batch_size)]
    element_map = {item["id"]: item for item in bundle["elements"]}
    batch_inputs = [_candidate_input(batch, element_map) for batch in all_batches]
    request_characters = [provider.candidate_request_characters(value, generation_prompt, CANDIDATE_SCHEMA)
                          for value in batch_inputs] if isinstance(provider, OpenAIProvider) else \
        [len(value) for value in batch_inputs]
    selected_indices = _subset_batch_indices(request_characters, subset_batches, batch_numbers)
    selected_batches = [all_batches[index] for index in selected_indices]
    selected_chunks = [chunk for batch in selected_batches for chunk in batch]
    scope = {"mode": "subset" if subset_batches is not None or batch_numbers is not None else "full",
             "selection": "explicit" if batch_numbers is not None else
                          "length-stratified" if subset_batches is not None else "all",
             "total_chunks": len(chunks),
             "selected_chunks": len(selected_chunks), "total_batches": len(all_batches),
             "selected_batch_numbers": [index + 1 for index in selected_indices],
             "selected_chunk_ids": [chunk["id"] for chunk in selected_chunks],
             "generation_request_characters": {"total": sum(request_characters),
                                               "selected": sum(request_characters[index] for index in selected_indices)}}
    config_digest = digest(canonical({"config": config, "input_bundle_sha256": file_digest(input_path),
                                      "generation_prompt_sha256": generation_hash, "scope": scope}))
    pipeline_version = config["pipeline_version"] + "-generation"
    build_id = _build_id(pipeline_version, config_digest, document_ids)
    generation = {"method": "model", "version": config["generation_version"],
                  "model_id": provider.model_id, "prompt_sha256": generation_hash}
    chunk_map = {item["id"]: item for item in selected_chunks}
    identity = _generation_identity(build_id, input_path, config_digest, generation_hash, scope)
    checkpoint_path = output_dir / "generation-checkpoint.json"
    checkpoint_dir = output_dir / "generation-checkpoints"
    candidate_path = output_dir / "claim-candidates.jsonl"
    if output_dir.exists():
        if not resume:
            raise ValueError(f"output directory already exists: {output_dir}; use --resume for an incomplete run")
        if (output_dir / "build-manifest.json").is_file():
            raise ValueError("claim-generation output is already complete")
        candidates, errors, usage, completed_batches = _load_generation_checkpoints(
            output_dir, identity, selected_batches, chunk_map, element_map)
        _write_jsonl_atomic(candidate_path, sorted(candidates, key=lambda item: item["id"]))
    else:
        if resume:
            raise ValueError("cannot resume because the claim-generation output directory does not exist")
        output_dir.mkdir(parents=True)
        checkpoint_dir.mkdir()
        candidates, errors, usage, completed_batches = [], [], [], set()
        _write_jsonl_atomic(candidate_path, candidates)
        _write_json_atomic(checkpoint_path, {**identity, "status": "in_progress", "completed_batch_numbers": []})
    if on_progress:
        on_progress({"phase": "generation", "state": "started", "chunks": len(selected_chunks),
                     "source_chunks": len(chunks), "total": len(selected_batches),
                     "source_total": len(all_batches), "subset": scope["mode"] == "subset",
                     "resumed_batches": len(completed_batches)})
    for completed, batch in enumerate(selected_batches, start=1):
        if completed in completed_batches:
            continue
        ids, usage_start = [item["id"] for item in batch], len(provider.usage_records)
        batch_candidates, batch_errors = [], []
        choices = _evidence_choices(batch, element_map)
        retry_feedback = ""

        def generate_batch():
            nonlocal retry_feedback
            response = provider.candidates(ids if isinstance(provider, FixtureProvider) else
                                           batch_inputs[selected_indices[completed - 1]],
                                           generation_prompt + retry_feedback, CANDIDATE_SCHEMA)
            try:
                Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
                records = [_candidate_record(build_id, item, generation, choices) for item in response["claims"]]
                _check_substantive_footnotes(records, choices)
                candidate_ids = {item["id"] for item in candidates}
                if len({item["id"] for item in records}) != len(records) or \
                        candidate_ids.intersection(item["id"] for item in records):
                    raise ValueError("generation produced duplicate candidate IDs")
            except (ValueError, ValidationError) as exc:
                retry_feedback = ("\nLa réponse précédente a été rejetée : " + str(exc)[:600] +
                                  "\nCorrigez la réponse complète. Explicitez les sujets et les règles ; "
                                  "omettez toute assertion impossible à étayer avec les preuves proposées.")
                raise
            return records

        try:
            batch_candidates = _call(
                generate_batch, config["retries"],
                lambda attempt, total: on_progress and on_progress(
                    {"phase": "generation", "state": "retry", "completed": completed,
                     "total": len(selected_batches), "attempt": attempt, "attempts": total}))
        except Exception as exc:
            batch_errors.append({"stage": "generation", "chunk_ids": ids, "error": str(exc)})
        batch_usage = provider.usage_records[usage_start:]
        batch_record = {"batch_number": completed, "chunk_ids": ids, "candidates": batch_candidates,
                        "errors": batch_errors, "usage_records": batch_usage}
        _write_json_atomic(checkpoint_dir / f"batch-{completed:06d}.json", batch_record)
        candidates.extend(batch_candidates)
        errors.extend(batch_errors)
        usage.extend(batch_usage)
        completed_batches.add(completed)
        candidates.sort(key=lambda item: item["id"])
        _write_jsonl_atomic(candidate_path, candidates)
        _write_json_atomic(checkpoint_path, {**identity, "status": "in_progress",
                                              "completed_batch_numbers": sorted(completed_batches)})
        if on_progress and _checkpoint(completed, len(selected_batches)):
            on_progress({"phase": "generation", "state": "progress", "completed": completed,
                         "total": len(selected_batches), "candidates": len(candidates)})
    if len({item["id"] for item in candidates}) != len(candidates):
        raise ValueError("generation produced duplicate candidate IDs")
    candidates.sort(key=lambda item: item["id"])
    result = copy.deepcopy(bundle)
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id, "pipeline_version": pipeline_version,
                               "config_sha256": config_digest, "input_document_ids": document_ids,
                               "output_chunk_ids": [], "output_claim_ids": [], "output_topic_ids": [],
                               "status": "partial" if errors else "complete"})
    from scripts.validate_artifact_examples import check
    check(result)
    if on_progress:
        on_progress({"phase": "export", "state": "started"})
    _write_json(output_dir / "artifact-bundle.json", result)
    _write_jsonl_atomic(candidate_path, candidates)
    telemetry = _telemetry(provider, config, usage)
    coverage = _generation_coverage(bundle, candidates, scope, errors)
    report = {"generation_build_id": build_id, "upstream_chunk_build_id": bundle["builds"][0]["id"],
              "documents": len(document_ids), "chunks": len(chunks), "candidates": len(candidates),
              "compilation_scope": scope, "coverage": coverage, "errors": errors, "telemetry": telemetry,
              "projected_full_generation": _project_generation(scope, usage, config),
              "status": "partial" if errors else "complete"}
    _write_json(output_dir / "report.json", report)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file() and checkpoint_dir not in path.parents and
                   path != checkpoint_path)
    manifest = {"manifest_version": 1, "stage": "claim-generation", "generation_build_id": build_id,
                "upstream_chunk_build_id": bundle["builds"][0]["id"], "input_bundle_sha256": file_digest(input_path),
                "config_sha256": config_digest, "generation_prompt_sha256": generation_hash,
                "compilation_scope": scope, "candidate_sha256": file_digest(candidate_path),
                "outputs": [{"path": str(path.relative_to(output_dir)), "sha256": file_digest(path)} for path in files]}
    _write_json(output_dir / "build-manifest.json", manifest)
    checkpoint_path.unlink(missing_ok=True)
    for path in checkpoint_dir.glob("batch-*.json"):
        path.unlink()
    checkpoint_dir.rmdir()
    if on_progress:
        on_progress({"phase": "complete", "state": report["status"], "telemetry": telemetry,
                     "projection": report["projected_full_generation"]})
    return report


def classify_claims(candidate_dir, output_dir, config_path=ROOT / "config/compilation.json",
                    taxonomy_path=ROOT / "config/topics-v1.json", fixture_path=None, review_path=None,
                    on_progress=None):
    """Classify a durable candidate artifact without regenerating its claims."""
    started = time.monotonic()
    candidate_dir, output_dir = Path(candidate_dir), Path(output_dir)
    manifest, candidate_path, manifest_path = _candidate_manifest(candidate_dir)
    bundle = _read_json(candidate_dir / "artifact-bundle.json")
    if bundle["claims"] or bundle["topics"]:
        raise ValueError("candidate artifact bundle must not contain classified claims or topics")
    candidates = _read_jsonl(candidate_path)
    chunk_map = {item["id"]: item for item in bundle["chunks"]}
    element_map = {item["id"]: item for item in bundle["elements"]}
    document_map = {item["id"]: item for item in bundle["documents"]}
    source_map = {(item["id"], item["version"]): item for item in bundle["sources"]}
    for candidate in candidates:
        _validate_candidate(candidate, manifest["generation_build_id"], chunk_map, element_map)
    if len({item["id"] for item in candidates}) != len(candidates):
        raise ValueError("candidate artifact has duplicate IDs")
    config, taxonomy = _config(config_path), _taxonomy(taxonomy_path)
    valid_topics = {item["id"] for item in taxonomy["topics"]}
    classification_prompt, classification_hash = _prompt("topic_classification_v1.txt")
    provider = _provider(config, fixture_path)
    document_ids = [item["id"] for item in bundle["documents"]]
    config_digest = digest(canonical({"config": config, "candidate_manifest_sha256": file_digest(manifest_path),
                                      "candidate_sha256": file_digest(candidate_path),
                                      "taxonomy_sha256": file_digest(taxonomy_path),
                                      "classification_prompt_sha256": classification_hash}))
    pipeline_version = config["pipeline_version"] + "-classification"
    build_id = _build_id(pipeline_version, config_digest, document_ids)
    classifier = {"method": "model", "version": config["classification_version"],
                  "model_id": provider.model_id, "prompt_sha256": classification_hash}
    claims, errors, unclassified, usage = [], [], [], []
    if on_progress:
        on_progress({"phase": "classification", "state": "started", "total": len(candidates)})
    for index, candidate in enumerate(candidates, start=1):
        usage_start = len(provider.usage_records)
        context = _classification_input(candidate, chunk_map, element_map, document_map, source_map)
        retry_feedback = ""

        def classify_candidate():
            nonlocal retry_feedback
            response = provider.classify(context, taxonomy, classification_prompt + retry_feedback,
                                         CLASSIFICATION_SCHEMA)
            try:
                Draft202012Validator(CLASSIFICATION_SCHEMA).validate(response)
                if response["topic_id"] is not None and response["topic_id"] not in valid_topics:
                    raise ValueError("classifier returned an uncurated topic ID")
                _check_reporting_assignment(response, context, valid_topics)
            except (ValueError, ValidationError) as exc:
                retry_feedback = ("\nLa réponse précédente a été rejetée : " + str(exc)[:600] +
                                  "\nRéexaminez les preuves et le périmètre organisé ; retournez le JSON corrigé.")
                raise
            return response

        try:
            classification = _call(
                classify_candidate, config["retries"],
                lambda attempt, total: on_progress and on_progress(
                    {"phase": "classification", "state": "retry", "completed": index,
                     "total": len(candidates), "attempt": attempt, "attempts": total}))
            if classification.get("topic_id") is None:
                unclassified.append(candidate["id"])
            else:
                claims.append(_claim(build_id, candidate, classification, classifier))
        except Exception as exc:
            errors.append({"stage": "classification", "candidate_id": candidate["id"], "error": str(exc)})
        usage.extend(provider.usage_records[usage_start:])
        if on_progress and _checkpoint(index, len(candidates)):
            on_progress({"phase": "classification", "state": "progress", "completed": index,
                         "total": len(candidates), "claims": len(claims)})
    claims.sort(key=lambda item: item["id"])
    if len({item["id"] for item in claims}) != len(claims):
        raise ValueError("classification produced duplicate claim IDs")
    topics = _topic_records(claims, taxonomy, build_id)
    review = _apply_review(claims, topics, review_path, build_id)
    result = copy.deepcopy(bundle)
    result["claims"], result["topics"] = claims, topics
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id, "pipeline_version": pipeline_version,
                               "config_sha256": config_digest, "input_document_ids": document_ids,
                               "output_chunk_ids": [], "output_claim_ids": [item["id"] for item in claims],
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
    _write_jsonl(output_dir / "chunks.jsonl", result["chunks"])
    _write_jsonl(output_dir / "claims.jsonl", claims)
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
            lines.append(f"- `{claim['id']}` — {claim['text']} (grounding: "
                         f"{claim['grounding_review_status']}; classification: {claim['classification_review_status']})")
        (topic_dir / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    telemetry = _telemetry(provider, config)
    generation_report = _read_json(candidate_dir / "report.json")
    projected = None
    scope = generation_report["compilation_scope"]
    if scope["mode"] == "subset" and candidates:
        classification_cost = _cost(usage, _rate_card(config)) * scope["total_chunks"] / scope["selected_chunks"]
        generation_projection = generation_report.get("projected_full_generation")
        if generation_projection:
            projected = {"estimated_cost_usd": generation_projection["estimated_cost_usd"] + classification_cost,
                         "generation_estimated_cost_usd": generation_projection["estimated_cost_usd"],
                         "classification_estimated_cost_usd": classification_cost,
                         "projected_candidates": len(candidates) * scope["total_chunks"] / scope["selected_chunks"],
                         "assumptions": {"generation": generation_projection["assumption"],
                                         "classification": "observed cost per candidate scaled by subset claim yield"}}
    report = {"build_id": build_id, "upstream_generation_build_id": manifest["generation_build_id"],
              "candidate_manifest_sha256": file_digest(manifest_path), "candidates": len(candidates),
              "claims": len(claims), "topics": len(topics), "errors": errors,
              "unclassified_candidate_ids": unclassified, "telemetry": telemetry,
              "projected_full_build": projected, "review": review,
              "duration_seconds": round(time.monotonic() - started, 3),
              "status": "partial" if errors else "complete"}
    _write_json(output_dir / "report.json", report)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    output_manifest = {"manifest_version": 1, "stage": "topic-classification", "compilation_build_id": build_id,
                       "upstream_generation_build_id": manifest["generation_build_id"],
                       "candidate_manifest_sha256": file_digest(manifest_path), "config_sha256": config_digest,
                       "taxonomy_sha256": file_digest(taxonomy_path), "classification_prompt_sha256": classification_hash,
                       "outputs": [{"path": str(path.relative_to(output_dir)), "sha256": file_digest(path)} for path in files]}
    _write_json(output_dir / "build-manifest.json", output_manifest)
    if on_progress:
        on_progress({"phase": "complete", "state": report["status"], "telemetry": telemetry,
                     "projection": projected})
    return report
