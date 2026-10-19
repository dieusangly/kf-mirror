#!/usr/bin/env python3
"""Render one artifact bundle as a standalone interactive provenance graph."""

import argparse
import html
import json
from pathlib import Path


RECORD_TYPES = ("sources", "documents", "elements", "chunks", "claims", "topics")
GROUPS = ("topic", "claim", "candidate", "summary", "summary_cluster", "semantic_cluster", "structural_chunk",
          "chunk", "element", "document", "source")


def _short(value, limit=72):
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _record_detail(record_type, record):
    if record_type == "topic":
        return record.get("summary", "")
    if record_type in {"claim", "candidate", "summary", "chunk", "element", "structural_chunk"}:
        return record.get("text", "")
    if record_type == "semantic_cluster":
        return (f"{len(record.get('structural_chunk_ids', []))} structural chunks · "
                f"{len(record.get('original_chunk_ids', []))} original chunks · "
                f"minimum similarity {record.get('minimum_similarity', 'unknown')}")
    if record_type == "summary_cluster":
        return f"{len(record['summary_ids'])} input summaries · produces level {record['level']}"
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
    if record_type in {"semantic_cluster", "structural_chunk", "summary_cluster"}:
        prefix = {"semantic_cluster": "Cluster", "structural_chunk": "Structural chunk",
                  "summary_cluster": "Summary cluster"}[record_type]
        suffix = record["id"].rsplit(":", 1)[-1][:8]
        return f"{prefix} {suffix}"
    return _short(record.get("text") or record["id"])


def _node(record_type, record, source_title=None, candidate_status=None):
    record = dict(record)
    meta = record_type
    if record_type == "candidate":
        meta = f"candidate claim · {candidate_status or 'classification status unavailable'}"
    node = {
        "id": record["id"],
        "group": record_type,
        "meta": meta,
        "label": _record_label(record_type, record, source_title),
        "detail": _record_detail(record_type, record),
        "record": record,
    }
    if record_type == "candidate":
        node["classification_status"] = candidate_status or "status unavailable"
    return node


def _edge(source, target, relation, evidence=False):
    return {"source": source, "target": target, "relation": relation, "evidence": evidence}


def _candidate_identity(record):
    """Match a generated candidate to its classified claim without changing either record."""
    return json.dumps([record.get("text"), record.get("evidence"), record.get("chunk_ids")],
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_classification(candidate, claims_by_identity, report):
    claim = claims_by_identity.get(_candidate_identity(candidate))
    if claim:
        return "classified", claim
    candidate_id = candidate["id"]
    if candidate_id in report.get("unclassified_candidate_ids", []):
        return "unclassified", None
    if any(item.get("candidate_id") == candidate_id for item in report.get("errors", [])):
        return "classification error", None
    if report:
        return "classification status unavailable", None
    return "status unavailable", None


def build_graph(bundle, claim_candidates=None, claim_generation=None, classification_report=None, summary_context=None):
    """Return graph nodes and provenance edges from a normalized artifact bundle."""
    if not isinstance(bundle, dict):
        raise ValueError("artifact bundle must be a JSON object")
    for record_type in RECORD_TYPES:
        if not isinstance(bundle.get(record_type), list):
            raise ValueError(f"artifact bundle must contain a {record_type} array")

    if claim_candidates is not None and not isinstance(claim_candidates, list):
        raise ValueError("candidate claims must be a list")
    claim_generation = claim_generation or {}
    if not isinstance(claim_generation, dict):
        raise ValueError("claim generation context must be an object")
    summary_context = summary_context or {}
    preparation = summary_context.get('preparation')
    if preparation:
        for name in ('sources', 'documents', 'elements', 'chunks'):
            if {r['id']: r for r in bundle[name]} != {r['id']: r for r in preparation['bundle'][name]}:
                raise ValueError('summary preparation and bundle originals mismatch')
        claim_generation = dict(claim_generation)
        for target, source in (('structural_chunks', 'structural_chunks'), ('semantic_clusters', 'clusters'),
                               ('hierarchy', 'hierarchy')):
            rows = preparation[source]
            if target in claim_generation and claim_generation[target] != rows:
                raise ValueError('claim and summary preparations mismatch; use matching runs')
            claim_generation[target] = rows
    for key in ("structural_chunks", "semantic_clusters", "candidate_clusters"):
        if key in claim_generation and not isinstance(claim_generation[key], list):
            raise ValueError(f"claim generation {key.replace('_', ' ')} must be a list")
    if classification_report is None:
        classification_report = {}
    if not isinstance(classification_report, dict):
        raise ValueError("classification report must be an object")

    generation_candidates = claim_generation.get("candidates", [])
    if generation_candidates and claim_candidates:
        by_id = {item["id"]: item for item in generation_candidates}
        for item in claim_candidates:
            if item["id"] in by_id and item != by_id[item["id"]]:
                raise ValueError(f"conflicting candidate claim records: {item['id']}")
            by_id[item["id"]] = item
        claim_candidates = list(by_id.values())
    elif generation_candidates:
        claim_candidates = generation_candidates

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
    for record_type, records_to_add in (
        ("structural_chunk", claim_generation.get("structural_chunks", [])),
        ("semantic_cluster", claim_generation.get("semantic_clusters", [])),
        ("summary_cluster", summary_context.get("summary_clusters", [])),
        ("summary", summary_context.get("summaries", [])),
    ):
        for record in records_to_add:
            add_record(record_type, record)
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

    structural_ids = {record["id"] for record in claim_generation.get("structural_chunks", [])}
    semantic_ids = {record["id"] for record in claim_generation.get("semantic_clusters", [])}
    chunk_ids = {record["id"] for record in bundle["chunks"]}
    for structural in claim_generation.get("structural_chunks", []):
        for chunk_id in structural.get("original_chunk_ids", []):
            if chunk_id not in chunk_ids:
                raise ValueError(f"structural chunk references unknown original chunk: {chunk_id}")
            add(structural["id"], chunk_id, "composed from")
        for eid in structural.get('context_element_ids', []):
            add(structural['id'], eid, 'context')
    for row in claim_generation.get('hierarchy', []):
        add(row['element_id'], row['parent_element_id'], 'context parent')
    for cluster in claim_generation.get("semantic_clusters", []):
        for structural_id in cluster.get("structural_chunk_ids", []):
            if structural_id not in structural_ids:
                raise ValueError(f"semantic cluster references unknown structural chunk: {structural_id}")
            add(cluster["id"], structural_id, "groups")
    summary_ids = {r['id'] for r in summary_context.get('summaries', [])}
    summary_cluster_ids = {r['id'] for r in summary_context.get('summary_clusters', [])}
    for cluster in summary_context.get('summary_clusters', []):
        for sid in cluster['summary_ids']:
            if sid not in summary_ids:
                raise ValueError('summary cluster references unknown summary: ' + sid)
            add(cluster['id'], sid, 'groups')
    for summary in summary_context.get('summaries', []):
        allowed = semantic_ids if summary['level'] == 1 else summary_cluster_ids
        if summary['input_cluster_id'] not in allowed:
            raise ValueError('summary references unknown input cluster: ' + summary['input_cluster_id'])
        add(summary['id'], summary['input_cluster_id'], 'summarizes')

    candidate_cluster = {}
    for mapping in claim_generation.get("candidate_clusters", []):
        candidate_id, cluster_id = mapping.get("candidate_id"), mapping.get("semantic_cluster_id")
        if not isinstance(candidate_id, str) or not isinstance(cluster_id, str):
            raise ValueError("candidate cluster mappings need candidate_id and semantic_cluster_id")
        if candidate_id in candidate_cluster and candidate_cluster[candidate_id] != cluster_id:
            raise ValueError(f"candidate maps to multiple semantic clusters: {candidate_id}")
        if cluster_id not in semantic_ids:
            raise ValueError(f"candidate references unknown semantic cluster: {cluster_id}")
        candidate_cluster[candidate_id] = cluster_id
        add(candidate_id, cluster_id, "generated from")

    claim_by_identity = {}
    for claim in bundle["claims"]:
        key = _candidate_identity(claim)
        if key in claim_by_identity:
            raise ValueError("classified claims have duplicate candidate content")
        claim_by_identity[key] = claim
    candidates_by_id = {candidate["id"]: candidate for candidate in claim_candidates or []}
    if set(candidate_cluster) - set(candidates_by_id):
        missing = min(set(candidate_cluster) - set(candidates_by_id))
        raise ValueError(f"candidate cluster mapping references unknown candidate: {missing}")
    for candidate in claim_candidates or []:
        cluster_id = candidate_cluster.get(candidate["id"])
        claim = claim_by_identity.get(_candidate_identity(candidate))
        if claim:
            add(candidate["id"], claim["id"], "classified as")
        if cluster_id:
            if claim:
                add(claim["id"], cluster_id, "generated from")

    candidate_statuses = {}
    for candidate in claim_candidates or []:
        status, claim = _candidate_classification(candidate, claim_by_identity, classification_report)
        candidate_statuses[candidate["id"]] = (status, claim)

    # Rebuild candidate display metadata with classification outcomes while retaining
    # the original candidate record unchanged.
    for index, node in enumerate(nodes):
        if node["group"] != "candidate":
            continue
        status, claim = candidate_statuses[node["id"]]
        topic = records.get(claim.get("topic_id")) if claim else None
        description = f"classified · {topic.get('title', topic['id'])}" if status == "classified" and topic else status
        nodes[index] = _node("candidate", node["record"], candidate_status=description)
        if claim:
            nodes[index]["classification_status"] = "classified"
            nodes[index]["classified_claim_id"] = claim["id"]
        else:
            nodes[index]["classification_status"] = status
    for topic in bundle["topics"]:
        for claim_id in topic.get("claim_ids", []):
            add(topic["id"], claim_id, "groups")

    return {"nodes": nodes, "edges": edges}


def load_claim_candidates(path):
    """Read nonempty JSONL candidate claim records without changing them."""
    candidates = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON in candidate claims line {number}: {error.msg}") from error
            if not isinstance(candidate, dict):
                raise ValueError(f"candidate claims line {number} must be a JSON object")
            candidates.append(candidate)
    return candidates


def _load_json(path, description):
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {description} at {path}: {error}") from error
    return value


def _load_jsonl(path, description):
    try:
        with path.open(encoding="utf-8") as handle:
            rows = []
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSON in {description} line {number}: {error.msg}") from error
                if not isinstance(row, dict):
                    raise ValueError(f"{description} line {number} must be a JSON object")
                rows.append(row)
    except OSError as error:
        raise ValueError(f"cannot read {description} at {path}: {error}") from error
    return rows


def load_claim_generation_context(directory):
    """Load structural, semantic, and candidate-lineage sidecars from a generation run."""
    directory = Path(directory)
    preparation = directory / "preparation"
    if not preparation.is_dir():
        preparation = directory
    context = {
        "structural_chunks": _load_jsonl(preparation / "structural-chunks.jsonl", "structural chunks"),
        "semantic_clusters": _load_json(preparation / "semantic-clusters.json", "semantic clusters"),
        "candidate_clusters": _load_json(directory / "candidate-clusters.json", "candidate cluster mappings"),
        "candidates": load_claim_candidates(directory / "claim-candidates.jsonl"),
    }
    hierarchy_path = preparation / 'document-hierarchy.json'
    if hierarchy_path.exists():
        context['hierarchy'] = _load_json(hierarchy_path, 'document hierarchy')
    for key in ("semantic_clusters", "candidate_clusters"):
        if not isinstance(context[key], list):
            raise ValueError(f"{key.replace('_', ' ')} must be a JSON array")
    return context


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
                        help="optional claim-candidates.jsonl to show unclassified candidate claims")
    parser.add_argument("--claim-generation-dir", type=Path,
                        help="optional generated-claims run directory with structural chunks and semantic clusters")
    parser.add_argument('--summary-dir', type=Path, help='optional frozen recursive-summary run; claims are optional')
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
        generation = load_claim_generation_context(args.claim_generation_dir) if args.claim_generation_dir else None
        summaries = None
        if args.summary_dir:
            import sys
            root = Path(__file__).resolve().parents[1]
            sys.path[:0] = [str(root), str(root / 'src')]
            from knowledge_foundry.summaries import load_summaries
            summaries = load_summaries(args.summary_dir)
        report_path = args.bundle.parent / "report.json"
        classification_report = _load_json(report_path, "classification report") if report_path.is_file() else None
        graph = build_graph(bundle, candidates, generation, classification_report, summaries)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    try:
        args.output.write_text(render_html(graph, args.title), encoding="utf-8")
    except OSError as error:
        parser.error(str(error))
    candidate_count = sum(node["group"] == "candidate" for node in graph["nodes"])
    print(json.dumps({"output": str(args.output), "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                      "candidates": candidate_count,
                      "summaries": sum(node['group'] == 'summary' for node in graph['nodes']),
                      "summary_clusters": sum(node['group'] == 'summary_cluster' for node in graph['nodes']),
                      "structural_chunks": sum(node["group"] == "structural_chunk" for node in graph["nodes"]),
                      "semantic_clusters": sum(node["group"] == "semantic_cluster" for node in graph["nodes"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
