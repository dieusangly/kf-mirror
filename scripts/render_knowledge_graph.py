#!/usr/bin/env python3
"""Render one artifact bundle as a standalone interactive provenance graph."""

import argparse
import sys
import json
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_foundry.applications.kpg_viewer.render import _json_for_script, render_html
from knowledge_foundry.provenance.graph import (
    RECORD_TYPES, _short,
    _record_detail,
    _record_label,
    _node,
    _edge,
    _candidate_identity,
    _candidate_classification,
    build_graph,
    load_claim_candidates,
    _load_json,
    _load_jsonl,
    load_claim_generation_context,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="artifact-bundle.json to render")
    parser.add_argument("--claim-candidates", type=Path,
                        help="optional claim-candidates.jsonl to show unclassified candidate claims")
    parser.add_argument("--claim-generation-dir", type=Path,
                        help="optional generated-claims run directory with structural chunks and semantic clusters")
    parser.add_argument('--summary-dir', type=Path, help='optional frozen recursive-summary run; claims are optional')
    parser.add_argument("--output", type=Path, required=True, help="HTML file to create or replace; parent directories are created")
    parser.add_argument("--title", default="Knowledge provenance graph", help="HTML page title")
    args = parser.parse_args(argv)

    try:
        with args.bundle.open(encoding="utf-8") as handle:
            bundle = json.load(handle)
        candidates = load_claim_candidates(args.claim_candidates) if args.claim_candidates else None
        generation = load_claim_generation_context(args.claim_generation_dir) if args.claim_generation_dir else None
        summaries = None
        if args.summary_dir:
            from knowledge_foundry.knowledge_engineering.summaries.summaries import load_summaries
            summaries = load_summaries(args.summary_dir)
        report_path = args.bundle.parent / "report.json"
        classification_report = _load_json(report_path, "classification report") if report_path.is_file() else None
        graph = build_graph(bundle, candidates, generation, classification_report, summaries)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
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
