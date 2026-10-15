#!/usr/bin/env python3
"""Render one artifact bundle as a standalone interactive provenance graph."""

import argparse
import html
import json
from pathlib import Path


RECORD_TYPES = ("sources", "documents", "elements", "chunks", "claims", "topics")
GROUPS = ("topic", "claim", "candidate", "chunk", "element", "document", "source")


def _short(value, limit=72):
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _record_detail(record_type, record):
    if record_type == "topic":
        return record.get("summary", "")
    if record_type in {"claim", "candidate", "chunk", "element"}:
        return record.get("text", "")
    if record_type == "document":
        return f"{record.get('media_type', 'saved document')} · retrieved {record.get('retrieved_on', 'unknown')}"
    return record.get("uri", "")


def _record_label(record_type, record, source_title=None):
    if record_type in {"topic", "source"}:
        return _short(record.get("title") or record["id"])
    if record_type == "document":
        return _short(record.get("title") or source_title or record.get("media_type") or record["id"])
    if record_type == "element":
        return _short(record.get("locator_key") or record["id"])
    return _short(record.get("text") or record["id"])


def _node(record_type, record, source_title=None):
    record = dict(record)
    meta = record_type
    if record_type == "candidate":
        meta = f"unclassified candidate · {record.get('grounding_review_status', 'draft')}"
    return {
        "id": record["id"],
        "group": record_type,
        "meta": meta,
        "label": _record_label(record_type, record, source_title),
        "detail": _record_detail(record_type, record),
        "record": record,
    }


def _edge(source, target, relation, evidence=False):
    return {"source": source, "target": target, "relation": relation, "evidence": evidence}


def build_graph(bundle, claim_candidates=None):
    """Return graph nodes and provenance edges from a normalized artifact bundle."""
    if not isinstance(bundle, dict):
        raise ValueError("artifact bundle must be a JSON object")
    for record_type in RECORD_TYPES:
        if not isinstance(bundle.get(record_type), list):
            raise ValueError(f"artifact bundle must contain a {record_type} array")

    if claim_candidates is not None and not isinstance(claim_candidates, list):
        raise ValueError("claim candidates must be a list")

    records = {}
    nodes = []
    source_titles = {source["id"]: source.get("title") for source in bundle["sources"]}

    def add_record(record_type, record):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError(f"each {record_type} record must have a string id")
        if record["id"] in records:
            raise ValueError(f"duplicate record id: {record['id']}")
        records[record["id"]] = record
        source_title = source_titles.get(record.get("source_id")) if record_type == "document" else None
        nodes.append(_node(record_type, record, source_title))

    for plural in RECORD_TYPES:
        for record in bundle[plural]:
            add_record(plural.rstrip("s"), record)
    for candidate in claim_candidates or []:
        add_record("candidate", candidate)

    edges, seen = [], set()

    def add(source, target, relation, evidence=False):
        if source not in records or target not in records:
            return
        key = (source, target, relation, evidence)
        if key not in seen:
            seen.add(key)
            edges.append(_edge(source, target, relation, evidence))

    for document in bundle["documents"]:
        add(document["id"], document.get("source_id"), "saved from")
    for element in bundle["elements"]:
        add(element["id"], element.get("document_id"), "located in")
    for chunk in bundle["chunks"]:
        for evidence in chunk.get("evidence", []):
            if isinstance(evidence, dict):
                add(chunk["id"], evidence.get("element_id"), "supported by", evidence=True)
    for claim in bundle["claims"]:
        add(claim.get("topic_id"), claim["id"], "groups")
        for chunk_id in claim.get("chunk_ids", []):
            add(claim["id"], chunk_id, "uses")
        for evidence in claim.get("evidence", []):
            if isinstance(evidence, dict):
                add(claim["id"], evidence.get("element_id"), "supported by", evidence=True)
    for candidate in claim_candidates or []:
        for chunk_id in candidate.get("chunk_ids", []):
            add(candidate["id"], chunk_id, "uses")
        for evidence in candidate.get("evidence", []):
            if isinstance(evidence, dict):
                add(candidate["id"], evidence.get("element_id"), "supported by", evidence=True)
    for topic in bundle["topics"]:
        for claim_id in topic.get("claim_ids", []):
            add(topic["id"], claim_id, "groups")

    return {"nodes": nodes, "edges": edges}


def load_claim_candidates(path):
    """Read nonempty JSONL candidate records without changing them."""
    candidates = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON in claim candidates line {number}: {error.msg}") from error
            if not isinstance(candidate, dict):
                raise ValueError(f"claim candidates line {number} must be a JSON object")
            candidates.append(candidate)
    return candidates


def _json_for_script(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_html(graph, title="Knowledge provenance graph"):
    """Return a deterministic, standalone local HTML viewer for a built graph."""
    asset_dir = Path(__file__).with_name("kpg_viewer")
    template = (asset_dir / "index.html").read_text(encoding="utf-8")
    styles = (asset_dir / "styles.css").read_text(encoding="utf-8")
    scripts = [
        (asset_dir / name).read_text(encoding="utf-8")
        for name in ("core.js", "graph.js", "detail.js", "app.js")
    ]
    script = f"window.graphData = {_json_for_script(graph)};\n" + "\n".join(scripts)
    return (template.replace("__TITLE__", html.escape(title))
            .replace("__STYLES__", styles)
            .replace("__SCRIPT__", script))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="artifact-bundle.json to render")
    parser.add_argument("--claim-candidates", type=Path,
                        help="optional claim-candidates.jsonl to show unclassified candidates")
    parser.add_argument("--output", type=Path, required=True, help="new HTML file to create")
    parser.add_argument("--title", default="Knowledge provenance graph", help="HTML page title")
    parser.add_argument("--force", action="store_true", help="replace an existing output file")
    args = parser.parse_args(argv)

    if args.output.exists() and not args.force:
        parser.error(f"output already exists: {args.output} (use --force to replace it)")
    try:
        with args.bundle.open(encoding="utf-8") as handle:
            bundle = json.load(handle)
        candidates = load_claim_candidates(args.claim_candidates) if args.claim_candidates else None
        graph = build_graph(bundle, candidates)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    try:
        args.output.write_text(render_html(graph, args.title), encoding="utf-8")
    except OSError as error:
        parser.error(str(error))
    print(json.dumps({"output": str(args.output), "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                      "candidates": len(candidates or [])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
