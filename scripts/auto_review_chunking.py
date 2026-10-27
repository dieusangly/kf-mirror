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

from knowledge_foundry.chunking import (  # noqa: E402
    TOKEN, clean, token_count, render_passages,
)
from knowledge_foundry.review import (  # noqa: E402
    apply_reviews as _apply_reviews,
    read_review_policy, review_policy_digest,
    select_samples, validate_reviewer_settings,
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
    validate_reviewer_settings(value)
    reviewer = {**value, **{key: value.get(key) for key in unknown}}
    if reviewer["kind"] not in (None, "human", "agent"):
        raise ValueError("reviewer kind must be human, agent or null")
    for field in ("name", "model"):
        value = reviewer[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"reviewer {field} must be a nonempty string or null")
    if any(row.get("status") in {"pass", "fail"} for row in review["reviews"]) or review.get("additional_findings"):
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
    """Audit complete structural units without imposing a hard token ceiling."""
    from knowledge_foundry.document_structuring import audit_evidence
    info = {row['element_id']: row for row in sidecar['elements']}
    ids = {e['id']: e for e in elements}
    if set(info) != set(ids):
        return {'source_id': source['id'], 'document_id': doc['id'], 'metrics': {},
                'findings': [{'code': 'SIDECAR_MISMATCH', 'severity': 'error', 'count': 1, 'examples': []}],
                'review_sample': []}
    audit = audit_evidence(elements, chunks, info)
    findings = {}
    for item in audit['findings']:
        _finding(findings, item['code'], item['severity'], item)
    if [c['sequence'] for c in chunks] != list(range(len(chunks))):
        _finding(findings, 'CHUNK_SEQUENCE', 'error', {'document_id': doc['id']})
    body_chunks = defaultdict(set)
    order = {'body': [], 'notes': []}
    for chunk in chunks:
        rendered = []
        for ref in chunk['evidence']:
            e = ids.get(ref['element_id'])
            if e is None:
                _finding(findings, 'EVIDENCE_MISSING', 'error', {'chunk_id': chunk['id']})
                continue
            span = ref.get('span')
            if span:
                if not 0 <= span['start'] < span['end'] <= len(e['text']):
                    _finding(findings, 'EVIDENCE_SPAN', 'error', {'chunk_id': chunk['id']})
                    continue
                if info[e['id']].get('evidence_role') != 'authoritative':
                    _finding(findings, 'NON_AUTHORITATIVE_BODY', 'error', {'chunk_id': chunk['id']})
                body_chunks[e['id']].add(chunk['id'])
                order['notes' if e['kind'] == 'footnote' else 'body'].append((e['ordinal'], span['start']))
                text = e['text'][span['start']:span['end']]
            else:
                text = e['text']
            rendered.append(clean(text, table=e['kind'] == 'table'))
        if not chunk['chunker_version'].startswith('provider-') and chunk['text'] != render_passages(rendered, chunk['evidence'], ids, info):
            _finding(findings, 'CHUNK_TEXT_MISMATCH', 'error', {'chunk_id': chunk['id']})
    if any(rows != sorted(rows) for rows in order.values()):
        _finding(findings, 'READING_ORDER', 'error', {'document_id': doc['id']})
    sizes = sorted(token_count(c['text']) for c in chunks)
    metrics = {'chunks': len(chunks), 'authoritative_elements': audit['authoritative_elements'],
               'covered_elements': audit['covered_elements'], 'exact_duplicates_removed': 0,
               'list_items_split_while_fitting': 0, 'oversize_list_items_split': 0, 'tables_split': 0,
               'short_chunks': sum(n < max(16, maximum // 8) for n in sizes),
               'near_limit_chunks': sum(n >= maximum * .9 for n in sizes),
               'oversized_structural_units': sum(n > maximum for n in sizes),
               'min_tokens': sizes[0] if sizes else None, 'max_tokens': sizes[-1] if sizes else None,
               'median_tokens': sizes[len(sizes)//2] if sizes else None}
    return {'source_id': source['id'], 'document_id': doc['id'], 'metrics': metrics,
            'findings': list(findings.values()),
            'review_sample': _sample(chunks, ids, info, source, maximum, sample_count, body_chunks)}


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
    structural_path = chunked_dir / 'structural-chunks.jsonl'
    if structural_path.exists():
        final_chunks = [json.loads(line) for line in structural_path.read_text().splitlines()]
        for sample in samples:
            sample['structural_chunks'] = [{**record, 'context_elements': [elements[eid]
                for eid in record.get('context_element_ids', [])]} for record in final_chunks
                if sample['chunk_id'] in record['original_chunk_ids']]

    review = json.loads(Path(review_path).read_text(encoding="utf-8")) if review_path else None
    policy_hash = review_policy_digest(policy, 'chunking')
    if review is not None and review.get('review_policy_sha256', policy_hash) != policy_hash:
        raise ValueError('stale review policy snapshot')
    review_summary, review_findings = _apply_reviews(review, bundle["builds"][0]["id"], samples, policy)
    reviewer = _reviewer(review)
    additional = review.get("additional_findings", []) if review else []
    if not isinstance(additional, list):
        raise ValueError("additional_findings must be an array")
    chunk_ids = {chunk["id"] for chunk in bundle["chunks"]}
    seen = {sample["chunk_id"] for sample in samples}
    for item in additional:
        if (not isinstance(item, dict) or item.get("chunk_id") not in chunk_ids or
                item["chunk_id"] in seen or item.get("status") != "fail"):
            raise ValueError("additional findings require unique unsampled chunk IDs and fail status")
        seen.add(item["chunk_id"])
    if additional:
        assessed_samples = samples + [{"chunk_id": item["chunk_id"]} for item in additional]
        assessed_review = {**review, "reviews": review["reviews"] + additional}
        review_summary, review_findings = _apply_reviews(
            assessed_review, bundle["builds"][0]["id"], assessed_samples, policy)
    if review_summary["severity_counts"]["critical"] > policy["maximum_critical_failures"]:
        review_summary["status"] = "fail"
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
    snapshot = json.loads((chunked_dir / "structuring-manifest.json").read_text()) if (chunked_dir / "structuring-manifest.json").exists() else {}
    if review is not None and review.get("structuring_snapshot_sha256") != snapshot.get("snapshot_sha256"):
        raise ValueError("stale structuring review snapshot")
    return {"structuring_snapshot_sha256": snapshot.get("snapshot_sha256"), "evaluation_version": EVALUATION_VERSION, "build_id": bundle["builds"][0]["id"],
            "automatic_gate": automatic_gate, "review_gate": review_summary["status"],
            "gate": gate, "max_tokens": maximum, "corpus": corpus, "documents": documents,
            "figures": {key: figure_audit[key] for key in ("expected", "linked")},
            "references": references, "review_policy": policy, "review_policy_sha256": policy_hash, "review": review_summary, "reviewer": reviewer,
            "review_template": {"structuring_snapshot_sha256": snapshot.get("snapshot_sha256"), "build_id": bundle["builds"][0]["id"],
                                "review_policy_sha256": policy_hash,
                                "reviewer": {"kind": None, "name": None, "model": None,
                                             "model_reasoning_effort": None, "launch_mode": None},
                                "additional_findings": [],
                                "reviews": [{"chunk_id": item["chunk_id"], "status": "pending",
                                             "severity": None, "notes": "source inspection pending"}
                                            for item in samples]},
            "additional_findings": additional, "findings": list(findings.values())}


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
