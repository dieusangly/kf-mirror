"""Schema and cross-record validation of v1 artifact bundles."""
import hashlib
import json

from jsonschema import Draft202012Validator, FormatChecker

from knowledge_foundry.provenance.evidence import claim_evidence_references
from knowledge_foundry.shared.paths import REPOSITORY_ROOT

SCHEMA = json.loads((REPOSITORY_ROOT / "schemas/artifact-schema-v1.0.0.json").read_text())


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_evidence(evidence):
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def check_evidence(evidence, records, document_ids):
    element = records["elements"].get(evidence["element_id"])
    require(element is not None, "evidence element missing")
    require(element["document_id"] == evidence["document_id"], "evidence document mismatch")
    require(element["revision"] == evidence["element_revision"], "evidence element revision mismatch")
    require(evidence["document_id"] in document_ids, "evidence outside supporting chunks")
    doc = records["documents"][element["document_id"]]
    require(doc["source_id"] == evidence["source_id"], "evidence source mismatch")
    if "span" in evidence:
        span = evidence["span"]
        require(0 <= span["start"] < span["end"] <= len(element["text"]), "evidence span out of bounds")


def check(bundle):
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(bundle)
    records = {}
    for name in ("sources", "documents", "elements", "chunks", "claims", "topics", "builds"):
        ids = [(record["id"], record["version"]) for record in bundle[name]] if name == "sources" else [record["id"] for record in bundle[name]]
        require(len(ids) == len(set(ids)), f"duplicate {name} ID")
        records[name] = dict(zip(ids, bundle[name]))

    for doc in bundle["documents"]:
        source = records["sources"].get((doc["source_id"], doc["source_version"]))
        require(source is not None, "document source missing")
        require(doc["id"] == "doc:" + digest(doc["source_id"] + "\n" + doc["content_sha256"]), "document ID mismatch")
        require(doc["media_type"] == {
            "pdf": "application/pdf", "html": "text/html", "markdown": "text/markdown",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }[source["format"]], "source format and media type mismatch")

    for element in bundle["elements"]:
        doc = records["documents"].get(element["document_id"])
        require(doc is not None, "element document missing")
        source = records["sources"][(doc["source_id"], doc["source_version"])]
        require(element["location"]["format"] == source["format"], "element location format mismatch")
        require(element["id"] == "el:" + digest(element["document_id"] + "\n" + element["locator_key"]), "element ID mismatch")
        require(element["revision"] == digest(element["kind"] + "\n" + element["text"]), "element revision mismatch")

    for chunk in bundle["chunks"]:
        require(chunk["document_id"] in records["documents"], "chunk document missing")
        require(chunk["build_id"] in records["builds"], "chunk build missing")
        for evidence in chunk["evidence"]:
            check_evidence(evidence, records, {chunk["document_id"]})
        expected = "chunk:" + digest(chunk["document_id"] + "\n" + chunk["chunker_version"] + "\n" + canonical_evidence(chunk["evidence"]) + "\n" + chunk["text"])
        require(chunk["id"] == expected, "chunk ID mismatch")

    for claim in bundle["claims"]:
        require(claim["build_id"] in records["builds"], "claim build missing")
        require(claim["topic_id"] in records["topics"], "claim topic missing")
        chunks = [records["chunks"].get(chunk_id) for chunk_id in claim["chunk_ids"]]
        require(all(chunks), "claim chunk missing")
        claim_build = records["builds"][claim["build_id"]]
        require(all(chunk["document_id"] in claim_build["input_document_ids"] for chunk in chunks),
                "claim chunk outside build inputs")
        supporting = {canonical_evidence([item]) for chunk in chunks
                      for item in claim_evidence_references(chunk, records["elements"])}
        for evidence in claim["evidence"]:
            require("span" in evidence, "claim evidence requires an exact span")
            check_evidence(evidence, records, {chunk["document_id"] for chunk in chunks})
            require(canonical_evidence([evidence]) in supporting, "claim evidence outside supporting chunks")
        expected = "claim:" + digest(claim["build_id"] + "\n" + canonical_evidence(claim["evidence"]) + "\n" + claim["text"])
        require(claim["id"] == expected, "claim ID mismatch")

    for topic in bundle["topics"]:
        require(topic["id"].startswith("topic:") and len(topic["id"]) > len("topic:"), "topic ID must start with topic:")
        require(topic["build_id"] in records["builds"], "topic build missing")
        claims = [records["claims"].get(claim_id) for claim_id in topic["claim_ids"]]
        require(all(claims), "topic claim missing")
        require(all(claim["topic_id"] == topic["id"] and claim["build_id"] == topic["build_id"] for claim in claims), "topic claim link mismatch")
        assigned_claims = {claim["id"] for claim in bundle["claims"] if claim["topic_id"] == topic["id"]}
        require(set(topic["claim_ids"]) == assigned_claims, "topic claim set mismatch")
        expected = digest(topic["title"] + "\n" + topic["summary"] + "\n" + "\n".join(sorted(topic["claim_ids"])))
        require(topic["revision"] == expected, "topic revision mismatch")

    for build in bundle["builds"]:
        inputs = build["input_document_ids"]
        require(all(item in records["documents"] for item in inputs), "build input missing")
        expected = "build:" + digest(build["pipeline_version"] + "\n" + build["config_sha256"] + "\n" + "\n".join(sorted(inputs)))
        require(build["id"] == expected, "build ID mismatch")
        outputs = {chunk["id"] for chunk in bundle["chunks"] if chunk["build_id"] == build["id"]}
        require(outputs == set(build["output_chunk_ids"]), "build output mismatch")
        claim_outputs = {claim["id"] for claim in bundle["claims"] if claim["build_id"] == build["id"]}
        require(claim_outputs == set(build["output_claim_ids"]), "build claim output mismatch")
        topic_outputs = {topic["id"] for topic in bundle["topics"] if topic["build_id"] == build["id"]}
        require(topic_outputs == set(build["output_topic_ids"]), "build topic output mismatch")
        require(all(chunk["document_id"] in inputs for chunk in bundle["chunks"] if chunk["build_id"] == build["id"]), "chunk outside build inputs")
