"""Evaluate saved chunks against parsed evidence and prepare source-linked review samples."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import unicodedata

from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from knowledge_foundry.chunking import TOKEN, clean, token_count  # noqa: E402
from knowledge_foundry.pdf_tables import context_for_spans, table_context_errors  # noqa: E402
from knowledge_foundry.review import (  # noqa: E402
    apply_reviews as _apply_reviews,
    read_review_policy, review_policy_digest,
    select_samples,
)
from scripts.validate_artifact_examples import check as validate_bundle  # noqa: E402

EVALUATION_VERSION = "chunk-eval-v2"
EXAMPLE_LIMIT = 5
DEFAULT_REVIEW_POLICY = ROOT / "config/review-policy.json"
DEFAULT_PIPELINE_CONFIG = ROOT / "config/pipeline.json"


def _read_review_policy(path):
    return read_review_policy(path, subject='chunking')


def _read_sample_per_document(config_path=DEFAULT_PIPELINE_CONFIG):
    """Read the reproducible source-review sample size from pipeline configuration."""
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    sample_count = config.get("evaluation", {}).get("chunking", {}).get("sample_per_document")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
        raise ValueError("evaluation.chunking.sample_per_document must be an integer >= 1")
    return sample_count


def _reviewer(review):
    """Validate reviewer identity while retaining unknown legacy identities."""
    if review is None:
        return None
    unknown = {"kind": None, "name": None, "model": None}
    if "reviewer" not in review:
        return unknown
    value = review["reviewer"]
    if not isinstance(value, dict):
        raise ValueError("reviewer must be an object")
    reviewer = {key: value.get(key) for key in unknown}
    if reviewer["kind"] not in (None, "human", "agent"):
        raise ValueError("reviewer kind must be human, agent or null")
    for field in ("name", "model"):
        value = reviewer[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"reviewer {field} must be a nonempty string or null")
    if any(row.get("status") in {"pass", "fail"} for row in review["reviews"]):
        if reviewer["kind"] is None or reviewer["name"] is None:
            raise ValueError("reviewer needs kind human/agent and a name for assessed samples")
    return reviewer


def _finding(findings, code, severity, example):
    item = findings.setdefault(code, {"code": code, "severity": severity, "count": 0, "examples": []})
    item["count"] += 1
    if len(item["examples"]) < EXAMPLE_LIMIT:
        item["examples"].append(example)


def _tokens(text):
    return TOKEN.findall(text)


def _location_key(element):
    location = element["location"]
    if location["format"] == "pdf" and "bbox" in location:
        return ("pdf", location["page"], tuple(location["bbox"]))
    return (element["locator_key"],)


def _table_header_span(element):
    """Return the first serialized table row, which may repeat as context."""
    end = element["text"].find("\n")
    return (0, len(element["text"]) if end < 0 else end)


def _normalized(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def _sample(chunks, elements, sidecar, source, maximum, count, body_chunks):
    """Select deterministic boundary cases and evenly spaced ordinary chunks."""
    candidates = defaultdict(list)
    for chunk in chunks:
        body = [elements[ref["element_id"]] for ref in chunk["evidence"] if "span" in ref]
        kinds = {element["kind"] for element in body}
        reasons = []
        size = token_count(chunk["text"])
        if size < max(16, maximum // 8):
            reasons.append("short")
        if size >= maximum * .9:
            reasons.append("near_limit")
        if "table" in kinds:
            reasons.append("table")
        if "list_item" in kinds:
            reasons.append("list_item")
        if "footnote" in kinds:
            reasons.append("footnote")
        if any(len(body_chunks[element["id"]]) > 1 for element in body):
            reasons.append("split_element")
        if any(str(sidecar[element["id"]].get("docling_parent_ref", "")).startswith("#/pictures/")
               for element in body):
            reasons.append("picture_flow")
        for reason in reasons:
            candidates[reason].append(chunk["id"])
    choices = [{"chunk_id": chunk["id"],
                "reasons": [reason for reason, ids in candidates.items() if chunk["id"] in ids]}
               for chunk in chunks]
    risk_order = ("picture_flow", "table", "split_element", "short", "near_limit", "list_item", "footnote")
    chosen = {item["chunk_id"] for item in select_samples(choices, count, risk_order)}
    result = []
    for chunk in chunks:
        if chunk["id"] not in chosen:
            continue
        evidence = []
        for ref in chunk["evidence"]:
            element = elements[ref["element_id"]]
            evidence.append({**ref, "kind": element["kind"], "location": element["location"]})
        result.append({"chunk_id": chunk["id"], "sequence": chunk["sequence"],
                       "reasons": [reason for reason, ids in candidates.items() if chunk["id"] in ids]
                       or ["coverage"], "token_count": token_count(chunk["text"]),
                       "text": chunk["text"], "source_uri": source["uri"],
                       "source_version": source["version"], "evidence": evidence})
    return result


def _audit_document(doc, source, chunks, elements, sidecar, maximum, sample_count):
    info = {item["element_id"]: item for item in sidecar["elements"]}
    ids = {element["id"]: element for element in elements}
    findings = {}
    metrics = {"chunks": len(chunks), "authoritative_elements": 0,
               "covered_elements": 0, "exact_duplicates_removed": 0,
               "list_items_split_while_fitting": 0, "oversize_list_items_split": 0,
               "tables_split": 0, "short_chunks": 0, "near_limit_chunks": 0,
               "min_tokens": None, "median_tokens": None, "max_tokens": None}
    if len(info) != len(elements) or set(info) != set(ids):
        _finding(findings, "SIDECAR_MISMATCH", "error", {"document_id": doc["id"]})
        return {"source_id": doc["source_id"], "document_id": doc["id"],
                "metrics": metrics, "findings": list(findings.values()), "review_sample": []}
    if [chunk["sequence"] for chunk in chunks] != list(range(len(chunks))):
        _finding(findings, "CHUNK_SEQUENCE", "error", {"document_id": doc["id"]})
    context_errors = table_context_errors(elements, info)
    invalid_context_tables = {e['table_id'] for e in context_errors}
    for error in context_errors:
        _finding(findings, 'INVALID_TABLE_CONTEXT', 'error', error)
    body_refs = defaultdict(list)
    body_chunks = defaultdict(set)
    body_order = []
    repeated_table_headers = set()
    def header_spans(element):
        detail = info[element['id']]
        if 'table_geometry' in detail:
            return {(r['span']['start'], r['span']['end']) for r in detail.get('table_headers', [])}
        return {_table_header_span(element)}
    sizes = []
    for chunk in chunks:
        size = token_count(chunk["text"])
        sizes.append(size)
        if size > maximum:
            _finding(findings, "TOKEN_LIMIT", "error", {"chunk_id": chunk["id"], "tokens": size})
        if size < max(16, maximum // 8):
            metrics["short_chunks"] += 1
        if size >= maximum * .9:
            metrics["near_limit_chunks"] += 1
        rendered = []
        paths = set()
        table_ids = set()
        non_table_ids = set()
        body_seen = False
        table_sources = [ids[ref['element_id']] for ref in chunk['evidence']
                         if ref['element_id'] in ids and ids[ref['element_id']]['kind'] == 'table'
                         and 'span' in ref]
        table_body = max(table_sources, key=lambda e: e['ordinal']) if table_sources else None
        def signature(ref):
            span = ref.get('span', {})
            return ref['element_id'], span.get('start'), span.get('end')
        required_context = set()
        if table_body and table_body['id'] not in invalid_context_tables:
            body_spans = [(ref['span']['start'], ref['span']['end']) for ref in chunk['evidence']
                          if ref['element_id'] == table_body['id'] and 'span' in ref]
            required_context = {signature(ref) for ref in context_for_spans(info[table_body['id']], body_spans)}
            if not required_context <= {signature(ref) for ref in chunk['evidence']}:
                _finding(findings, 'TABLE_CONTEXT_MISSING', 'error', {'chunk_id': chunk['id']})
        for ref in chunk["evidence"]:
            element = ids.get(ref["element_id"])
            if element is None:
                _finding(findings, "EVIDENCE_MISSING", "error", {"chunk_id": chunk["id"], "element_id": ref["element_id"]})
                continue
            detail = info[element["id"]]
            if detail.get("evidence_role") != "authoritative":
                _finding(findings, "NON_AUTHORITATIVE_EVIDENCE", "error",
                         {"chunk_id": chunk["id"], "element_id": element["id"], "role": detail.get("evidence_role")})
            span = ref.get("span")
            if span is None:
                if element["kind"] != "heading" and (body_seen or not body_refs[element["id"]]):
                    _finding(findings, "UNSPANNED_BODY", "error", {"chunk_id": chunk["id"], "element_id": element["id"]})
                text = element["text"]
            else:
                start, end = span["start"], span["end"]
                if not 0 <= start < end <= len(element["text"]):
                    _finding(findings, "EVIDENCE_SPAN", "error", {"chunk_id": chunk["id"], "element_id": element["id"]})
                    continue
                text = element["text"][start:end]
                if signature(ref) in required_context:
                    if body_seen:
                        _finding(findings, 'TABLE_CONTEXT_ORDER', 'error', {'chunk_id': chunk['id']})
                    rendered.append(clean(text, table=True))
                    continue  # Cited inherited context is not another table's body.
                body_refs[element["id"]].append((start, end, chunk["id"]))
                body_chunks[element["id"]].add(chunk["id"])
                header_context = (element["kind"] == "table" and
                                  (start, end) in header_spans(element) and
                                  (element['id'], start, end) in repeated_table_headers)
                if not header_context:
                    body_order.append((element["ordinal"], start))
                if element["kind"] == "table" and (start, end) in header_spans(element):
                    repeated_table_headers.add((element['id'], start, end))
                body_seen = True
                if element["kind"] != "heading":
                    paths.add(tuple(detail.get("heading_path") or []))
                if element["kind"] == "table":
                    table_ids.add(element["id"])
                elif element["kind"] != "heading":
                    non_table_ids.add(element["id"])
            rendered.append(clean(text, table=element["kind"] == "table"))
        if chunk["text"] != "\n\n".join(rendered):
            _finding(findings, "CHUNK_TEXT_MISMATCH", "error", {"chunk_id": chunk["id"]})
        if len(paths) > 1:
            _finding(findings, "CROSS_SECTION", "error", {"chunk_id": chunk["id"]})
        if table_ids and (len(table_ids) > 1 or non_table_ids):
            _finding(findings, "TABLE_MIXED_WITH_PROSE", "error", {"chunk_id": chunk["id"]})
    if body_order != sorted(body_order):
        _finding(findings, "READING_ORDER", "error", {"document_id": doc["id"]})
    if sizes:
        ordered = sorted(sizes)
        metrics["min_tokens"] = ordered[0]
        metrics["median_tokens"] = (ordered[(len(ordered)-1)//2] + ordered[len(ordered)//2]) / 2
        metrics["max_tokens"] = ordered[-1]
    seen = set()
    for element in elements:
        role = info[element["id"]].get("evidence_role")
        if role != "authoritative" or not clean(element["text"]):
            if body_refs[element["id"]]:
                _finding(findings, "NON_AUTHORITATIVE_BODY", "error", {"element_id": element["id"]})
            continue
        metrics["authoritative_elements"] += 1
        is_table = element["kind"] == "table"
        source_text = clean(element["text"], table=is_table)
        key = (_location_key(element), element["kind"], source_text)
        duplicate = key in seen
        seen.add(key)
        refs = body_refs[element["id"]]
        if duplicate:
            metrics["exact_duplicates_removed"] += 1
            if refs:
                _finding(findings, "DUPLICATE_NOT_REMOVED", "error", {"element_id": element["id"]})
            continue
        if not refs:
            _finding(findings, "AUTHORITATIVE_TEXT_MISSING", "error", {"element_id": element["id"]})
            continue
        metrics["covered_elements"] += 1
        coverage_refs = refs
        if is_table:
            headers = header_spans(element)
            headers_seen = set()
            coverage_refs = []
            for ref in refs:
                if ref[:2] in headers:
                    if ref[:2] in headers_seen:
                        continue
                    headers_seen.add(ref[:2])
                coverage_refs.append(ref)
        actual = [token for start, end, _ in coverage_refs
                  for token in _tokens(clean(element["text"][start:end], table=is_table))]
        expected = _tokens(source_text)
        if actual != expected:
            _finding(findings, "TEXT_COVERAGE", "error", {"element_id": element["id"],
                                                           "expected_tokens": len(expected), "actual_tokens": len(actual)})
        if element["kind"] == "list_item" and len(body_chunks[element["id"]]) > 1:
            if token_count(source_text) <= maximum:
                metrics["list_items_split_while_fitting"] += 1
                _finding(findings, "FITTING_LIST_ITEM_SPLIT", "error", {"element_id": element["id"]})
            else:
                metrics["oversize_list_items_split"] += 1
        if is_table and len(body_chunks[element["id"]]) > 1:
            metrics["tables_split"] += 1
    return {"source_id": doc["source_id"], "document_id": doc["id"],
            "metrics": metrics, "findings": list(findings.values()),
            "review_sample": _sample(chunks, ids, info, source, maximum, sample_count, body_chunks)}


def _audit_figures(links, parsed_dir, sidecars, documents, elements):
    findings = {}
    expected = {}
    for doc_id, sidecar in sidecars.items():
        for item in sidecar["elements"]:
            if item.get("asset"):
                expected[item["element_id"]] = (doc_id, item["asset"])
    seen = set()
    for link in links:
        element_id = link.get("element_id")
        if element_id in seen or element_id not in expected:
            _finding(findings, "FIGURE_LINK_UNEXPECTED", "error", {"element_id": element_id})
            continue
        seen.add(element_id)
        doc_id, asset = expected[element_id]
        element = elements.get(element_id)
        doc = documents[doc_id]
        path = link.get("asset", {}).get("path")
        expected_path = asset.get("path")
        expected_hash = asset.get("sha256")
        relative = Path(path) if isinstance(path, str) else Path()
        target = (parsed_dir / relative).resolve()
        safe = (isinstance(path, str) and not relative.is_absolute() and
                target.is_relative_to(parsed_dir) and
                relative.parts[:2] == ("documents", doc_id.removeprefix("doc:")))
        if not safe or not target.is_file() or not isinstance(expected_hash, str) or hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            _finding(findings, "FIGURE_ASSET_INVALID", "error", {"element_id": element_id})
        if (element is None or element["kind"] != "figure" or link.get("document_id") != doc_id or
                link.get("source_id") != doc["source_id"] or
                link.get("element_revision") != element["revision"] or
                link.get("location") != element["location"] or
                link.get("asset", {}).get("sha256") != expected_hash or
                link.get("asset", {}).get("representation_role") != "authoritative_visual" or
                not isinstance(expected_path, str) or
                path != f"documents/{doc_id.removeprefix('doc:')}/{expected_path}"):
            _finding(findings, "FIGURE_LINK_MISMATCH", "error", {"element_id": element_id})
    for element_id in sorted(expected.keys() - seen):
        _finding(findings, "FIGURE_LINK_MISSING", "error", {"element_id": element_id})
    return {"expected": len(expected), "linked": len(seen), "findings": list(findings.values())}


def _audit_references(reference, manifest, docs, chunks, elements):
    findings = {}
    results = []
    labels = {entry["manifest_id"]: entry["source_id"] for entry in manifest["sources"]}
    sources = {doc["source_id"] for doc in docs}
    for case in reference.get("passages", []):
        source_id = labels.get(case["source"], case["source"])
        if source_id not in sources:
            continue  # One reusable fixture may cover more than the selected corpus.
        wanted = _normalized(case["text"])
        matches = []
        for chunk in chunks:
            doc = next(item for item in docs if item["id"] == chunk["document_id"])
            if doc["source_id"] != source_id or wanted not in _normalized(chunk["text"]):
                continue
            body = [elements[ref["element_id"]] for ref in chunk["evidence"] if "span" in ref]
            if case.get("page") is not None and not any(
                item["location"].get("page") == case["page"] for item in body
            ):
                continue
            matches.append(chunk["id"])
        passed = bool(matches)
        results.append({"id": case["id"], "source_id": source_id, "pass": passed,
                        "chunk_ids": matches[:EXAMPLE_LIMIT]})
        if not passed:
            _finding(findings, "REFERENCE_PASSAGE_MISSING", "error", {"reference_id": case["id"], "source_id": source_id})
    return results, list(findings.values())


def _audit_document_views(chunked_dir, bundle, build_report, links, chunks_by_doc):
    """Check that each inspection view exactly mirrors the collection output."""
    findings = {}
    sources = {(item["id"], item["version"]): item for item in bundle["sources"]}
    reports = {item["document_id"]: item for item in build_report.get("documents", [])}
    expected_folders = set()
    for document in bundle["documents"]:
        document_id = document["id"]
        folder_name = document_id.removeprefix("doc:")
        expected_folders.add(folder_name)
        path = chunked_dir / "documents" / folder_name / "document-view.json"
        expected = {
            "view_version": 1, "build_id": bundle["builds"][0]["id"],
            "source": sources[(document["source_id"], document["source_version"])],
            "document": document,
            "elements": [item for item in bundle["elements"] if item["document_id"] == document_id],
            "chunks": chunks_by_doc[document_id],
            "figure_links": [item for item in links if item["document_id"] == document_id],
            "report": reports.get(document_id),
        }
        try:
            view = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _finding(findings, "DOCUMENT_VIEW_MISSING", "error",
                     {"document_id": document_id, "reason": str(exc)[:200]})
            continue
        if view != expected:
            _finding(findings, "DOCUMENT_VIEW_MISMATCH", "error", {"document_id": document_id})
    for path in sorted((chunked_dir / "documents").glob("*/document-view.json")):
        if path.parent.name not in expected_folders:
            _finding(findings, "DOCUMENT_VIEW_EXTRA", "error", {"folder": path.parent.name})
    return list(findings.values())


def evaluate(chunked_dir, parsed_dir, sample_count=None, reference_path=None, review_path=None,
             manifest_path=ROOT / "data/manifest.json", review_policy_path=DEFAULT_REVIEW_POLICY):
    """Return a deterministic report; never mutate parsing or chunk artifacts."""
    chunked_dir = Path(chunked_dir).resolve()
    parsed_dir = Path(parsed_dir).resolve()
    sample_count = _read_sample_per_document() if sample_count is None else sample_count
    bundle = json.loads((chunked_dir / "artifact-bundle.json").read_text(encoding="utf-8"))
    build_report = json.loads((chunked_dir / "report.json").read_text(encoding="utf-8"))
    links = json.loads((chunked_dir / "figure-links.json").read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if sample_count < 1:
        raise ValueError("sample-per-document must be >= 1")
    policy = _read_review_policy(review_policy_path)
    findings = {}
    try:
        validate_bundle(bundle)
    except (ValidationError, ValueError, KeyError) as exc:
        _finding(findings, "BUNDLE_INVALID", "error", {"reason": str(exc)[:400]})
        return {"evaluation_version": EVALUATION_VERSION, "automatic_gate": "fail",
                "review_gate": "pending", "gate": "fail", "reviewer": None,
                "findings": list(findings.values()), "documents": []}
    if len(bundle["builds"]) != 1:
        _finding(findings, "BUILD_RECORD_COUNT", "error", {"count": len(bundle["builds"])})
        return {"evaluation_version": EVALUATION_VERSION, "automatic_gate": "fail",
                "review_gate": "pending", "gate": "fail", "reviewer": None,
                "findings": list(findings.values()), "documents": []}
    if build_report.get("build_id") != bundle["builds"][0]["id"]:
        _finding(findings, "BUILD_REPORT_MISMATCH", "error", {})
    maximum = build_report.get("settings", {}).get("max_tokens")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 32:
        _finding(findings, "MAX_TOKENS_INVALID", "error", {"value": maximum})
        maximum = 256
    docs = {doc["id"]: doc for doc in bundle["documents"]}
    sources = {(source["id"], source["version"]): source for source in bundle["sources"]}
    elements = {element["id"]: element for element in bundle["elements"]}
    chunks_by_doc = defaultdict(list)
    for chunk in bundle["chunks"]:
        chunks_by_doc[chunk["document_id"]].append(chunk)
    for item in _audit_document_views(chunked_dir, bundle, build_report, links, chunks_by_doc):
        findings[item["code"]] = item
    sidecars = {}
    documents = []
    for doc in bundle["documents"]:
        folder = parsed_dir / "documents" / doc["id"].removeprefix("doc:")
        try:
            parsed = json.loads((folder / "artifact-bundle.json").read_text(encoding="utf-8"))
            sidecar = json.loads((folder / "parse.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _finding(findings, "PARSED_INPUT_MISSING", "error", {"document_id": doc["id"], "reason": str(exc)[:200]})
            continue
        own = [item for item in bundle["elements"] if item["document_id"] == doc["id"]]
        if (parsed["documents"] != [doc] or parsed["elements"] != own or
                sidecar.get("document_id") != doc["id"]):
            _finding(findings, "PARSED_INPUT_MISMATCH", "error", {"document_id": doc["id"]})
        sidecars[doc["id"]] = sidecar
        source = sources[(doc["source_id"], doc["source_version"])]
        matches = [entry for entry in manifest["sources"] if entry["source_id"] == doc["source_id"]
                   and entry["source_version"] == doc["source_version"]
                   and entry["sha256"] == doc["content_sha256"]]
        if len(matches) != 1:
            _finding(findings, "SOURCE_MANIFEST_MISMATCH", "error", {"document_id": doc["id"]})
        document_report = _audit_document(doc, source, chunks_by_doc[doc["id"]], own,
                                          sidecar, maximum, sample_count)
        document_report["source_file"] = matches[0]["local_path"] if len(matches) == 1 else None
        for sample in document_report["review_sample"]:
            sample["source_file"] = document_report["source_file"]
        documents.append(document_report)
    figure_audit = _audit_figures(links, parsed_dir, sidecars, docs, elements)
    for item in figure_audit["findings"]:
        findings[item["code"]] = item
    references = []
    if reference_path is not None:
        reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
        references, reference_findings = _audit_references(reference, manifest, list(docs.values()), bundle["chunks"], elements)
        for item in reference_findings:
            findings[item["code"]] = item
    samples = [sample for document in documents for sample in document["review_sample"]]
    review = json.loads(Path(review_path).read_text(encoding="utf-8")) if review_path else None
    policy_hash = review_policy_digest(policy, 'chunking')
    if review is not None and review.get('review_policy_sha256', policy_hash) != policy_hash:
        raise ValueError('stale review policy snapshot')
    review_summary, review_findings = _apply_reviews(review, bundle["builds"][0]["id"], samples, policy)
    reviewer = _reviewer(review)
    for item in review_findings:
        findings[item["code"]] = item
    document_errors = any(item["severity"] == "error" for doc in documents for item in doc["findings"])
    automatic_gate = "fail" if document_errors or any(
        item["severity"] == "error" for item in findings.values()
        if item["code"] != "MANUAL_REVIEW_POLICY_FAILED") else "pass"
    gate = "fail" if automatic_gate == "fail" or review_summary["status"] == "fail" else "pass" if review_summary["status"] == "pass" else "pending_review"
    corpus = {"documents": len(documents), "chunks": sum(item["metrics"]["chunks"] for item in documents),
              "authoritative_elements": sum(item["metrics"]["authoritative_elements"] for item in documents),
              "covered_elements": sum(item["metrics"]["covered_elements"] for item in documents),
              "short_chunks": sum(item["metrics"]["short_chunks"] for item in documents),
              "near_limit_chunks": sum(item["metrics"]["near_limit_chunks"] for item in documents),
              "fitting_list_items_split": sum(item["metrics"]["list_items_split_while_fitting"] for item in documents)}
    return {"evaluation_version": EVALUATION_VERSION, "build_id": bundle["builds"][0]["id"],
            "automatic_gate": automatic_gate, "review_gate": review_summary["status"],
            "gate": gate, "max_tokens": maximum, "corpus": corpus, "documents": documents,
            "figures": {key: figure_audit[key] for key in ("expected", "linked")},
            "references": references, "review_policy": policy, "review_policy_sha256": policy_hash, "review": review_summary, "reviewer": reviewer,
            "review_template": {"build_id": bundle["builds"][0]["id"],
                                "review_policy_sha256": policy_hash,
                                "reviewer": {"kind": None, "name": None, "model": None},
                                "reviews": [{"chunk_id": item["chunk_id"], "status": "pending",
                                             "severity": None, "notes": "source inspection pending"}
                                            for item in samples]},
            "findings": list(findings.values())}


def _write_review_seed(path, report):
    """Create the pending review file for one newly evaluated chunk build."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"review file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report["review_template"], ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunked-dir", type=Path, default=ROOT / "artifacts/chunked")
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "artifacts/parsed")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sample-per-document", type=int,
                        help="override evaluation.chunking.sample_per_document in config/pipeline.json")
    parser.add_argument("--reference-fixture", type=Path)
    parser.add_argument("--review-file", type=Path)
    parser.add_argument("--initialize-review", nargs="?", type=Path, metavar="PATH", const=None,
                        default=argparse.SUPPRESS,
                        help="create a pending review file; default: <chunked-dir>/chunking-review.json")
    parser.add_argument("--review-policy", type=Path, default=DEFAULT_REVIEW_POLICY)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/manifest.json")
    args = parser.parse_args(argv)
    output = args.output or args.chunked_dir / "chunking-evaluation-report.json"
    write_review = getattr(args, "initialize_review", None)
    if hasattr(args, "initialize_review") and write_review is None:
        write_review = args.chunked_dir / "chunking-review.json"
    if write_review and args.review_file:
        parser.error("--initialize-review cannot be used with --review-file")
    if write_review and write_review.exists():
        parser.error(f"review file already exists: {write_review}")
    if write_review and write_review.resolve() == output.resolve():
        parser.error("--initialize-review path must differ from --output")
    try:
        sample_count = (_read_sample_per_document() if args.sample_per_document is None
                        else args.sample_per_document)
        report = evaluate(args.chunked_dir, args.parsed_dir, sample_count,
                          args.reference_fixture, args.review_file, args.manifest, args.review_policy)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if write_review:
            _write_review_seed(write_review, report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"chunk evaluation failed: {exc}\n")
    print(json.dumps({"automatic_gate": report["automatic_gate"],
                      "review_gate": report["review_gate"], "gate": report["gate"],
                      "report": str(output)}, ensure_ascii=False))
    return 1 if report["gate"] == "fail" or args.review_file and report["gate"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
