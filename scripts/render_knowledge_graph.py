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


def _record_label(record_type, record):
    if record_type in {"topic", "source"}:
        return _short(record.get("title") or record["id"])
    if record_type == "document":
        return _short(record.get("media_type") or record["id"])
    if record_type == "element":
        return _short(record.get("locator_key") or record["id"])
    return _short(record.get("text") or record["id"])


def _node(record_type, record):
    record = dict(record)
    meta = record_type
    if record_type == "candidate":
        meta = f"unclassified candidate · {record.get('grounding_review_status', 'draft')}"
    return {
        "id": record["id"],
        "group": record_type,
        "meta": meta,
        "label": _record_label(record_type, record),
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

    def add_record(record_type, record):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError(f"each {record_type} record must have a string id")
        if record["id"] in records:
            raise ValueError(f"duplicate record id: {record['id']}")
        records[record["id"]] = record
        nodes.append(_node(record_type, record))

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
    """Return a standalone local HTML viewer for a graph returned by build_graph."""
    title = html.escape(title)
    graph_json = _json_for_script(graph)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --bg: light-dark(#f7f7f5, #161817); --surface: light-dark(#ffffff, #202320); --text: light-dark(#1d211f, #eef1ed); --muted: light-dark(#637067, #aeb8af); --border: light-dark(#d8ddd7, #3d453e); --accent: light-dark(#1e5c42, #9bdab6); --topic: light-dark(#eee7fb, #322848); --claim: light-dark(#e3efff, #21354e); --candidate: light-dark(#fff0d9, #49351f); --chunk: light-dark(#e3f3e8, #203d2b); --evidence: light-dark(#f4eee0, #423925); }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, sans-serif; }}
header {{ padding: 22px clamp(18px, 4vw, 48px) 16px; border-bottom: 1px solid var(--border); }}
h1 {{ font-size: 21px; margin: 0 0 5px; font-weight: 600; }}
header p {{ margin: 0; color: var(--muted); }}
main {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, 310px); gap: 18px; padding: 18px clamp(18px, 4vw, 48px) 36px; }}
.toolbar {{ display: flex; gap: 10px; align-items: center; margin-bottom: 12px; color: var(--muted); }}
select {{ color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; font: inherit; max-width: min(100%, 360px); }}
.canvas {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: auto; min-height: 260px; }}
svg {{ display: block; min-width: 1000px; width: 100%; }}
.edge {{ fill: none; stroke: var(--muted); stroke-width: 1.4; opacity: .72; }}
.edge.evidence {{ stroke: var(--accent); stroke-dasharray: 4 4; }}
.node rect {{ stroke: var(--border); stroke-width: 1; rx: 7; }}
.node.topic rect {{ fill: var(--topic); }} .node.claim rect {{ fill: var(--claim); }} .node.candidate rect {{ fill: var(--candidate); }} .node.chunk rect {{ fill: var(--chunk); }} .node.element rect, .node.document rect, .node.source rect {{ fill: var(--evidence); }}
.node {{ cursor: pointer; }} .node.selected rect {{ stroke: var(--accent); stroke-width: 3; }} .node:focus rect {{ stroke: var(--accent); stroke-width: 3; }}
.node-label {{ font-size: 12px; fill: var(--text); }} .node-kind {{ font-size: 10px; fill: var(--muted); letter-spacing: .05em; }}
aside {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px; align-self: start; position: sticky; top: 16px; }}
aside h2 {{ font-size: 16px; margin: 0 0 8px; }} aside p {{ margin: 0 0 13px; }} .meta {{ color: var(--muted); }} code {{ display: block; overflow-wrap: anywhere; color: var(--muted); font-size: 12px; }} a {{ color: var(--accent); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 10px 0 0; color: var(--muted); font-size: 12px; }} .line {{ display: inline-block; width: 18px; border-top: 2px solid var(--muted); vertical-align: middle; margin-right: 4px; }} .line.dashed {{ border-top-color: var(--accent); border-top-style: dashed; }}
@media (max-width: 760px) {{ main {{ grid-template-columns: 1fr; }} aside {{ position: static; }} }}
</style>
</head>
<body>
<header><h1>{title}</h1><p>Topic-first exploration with source-grounded claims and unclassified candidates.</p></header>
<main>
  <section aria-label="Knowledge graph">
    <div class="toolbar"><label for="topic-filter">Focus</label><select id="topic-filter" aria-label="Focus graph on a topic"></select></div>
    <div class="canvas"><svg id="graph" role="img" aria-label="Interactive knowledge provenance graph"></svg></div>
    <div class="legend"><span><i class="line"></i>record link</span><span><i class="line dashed"></i>supporting evidence</span></div>
  </section>
  <aside id="detail" aria-live="polite"><h2>Choose a record</h2><p class="meta">Select a node to inspect its identifier, details, and provenance.</p></aside>
</main>
<script>
const graph = {graph_json};
const groups = ["topic", "claim", "candidate", "chunk", "element", "document", "source"];
const labels = {{topic:"Topics", claim:"Claims", candidate:"Candidates", chunk:"Chunks", element:"Elements", document:"Documents", source:"Sources"}};
const nodes = new Map(graph.nodes.map(node => [node.id, node]));
const topicFilter = document.getElementById("topic-filter");
const svg = document.getElementById("graph");
const detail = document.getElementById("detail");
let selectedId = null;

function addOptions() {{
  topicFilter.add(new Option("All knowledge", ""));
  graph.nodes.filter(node => node.group === "topic").sort((a, b) => a.label.localeCompare(b.label)).forEach(node => topicFilter.add(new Option(node.label, node.id)));
}}
function relatedToTopic(topicId) {{
  if (!topicId) return new Set(graph.nodes.map(node => node.id));
  const visible = new Set([topicId]);
  const queue = [topicId];
  while (queue.length) {{
    const id = queue.shift();
    graph.edges.forEach(edge => {{
      if (edge.source === id && !visible.has(edge.target)) {{ visible.add(edge.target); queue.push(edge.target); }}
    }});
  }}
  return visible;
}}
function wrap(label, width) {{
  const words = [];
  label.split(/\\s+/).filter(Boolean).forEach(word => {{
    while (word.length > width) {{ words.push(word.slice(0, width)); word = word.slice(width); }}
    if (word) words.push(word);
  }});
  const lines = [""];
  words.forEach(word => {{ const last = lines.length - 1; if ((lines[last] + " " + word).trim().length > width && lines[last]) lines.push(word); else lines[last] = (lines[last] + " " + word).trim(); }});
  return lines.slice(0, 3);
}}
function svgElement(name, attrs = {{}}) {{ const el = document.createElementNS("http://www.w3.org/2000/svg", name); Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value)); return el; }}
function showDetail(id) {{
  selectedId = id; const node = nodes.get(id); if (!node) return;
  detail.replaceChildren();
  const heading = document.createElement("h2"); heading.textContent = node.label; detail.append(heading);
  const meta = document.createElement("p"); meta.className = "meta"; meta.textContent = node.meta; detail.append(meta);
  if (node.detail) {{ const body = document.createElement("p"); body.textContent = node.detail; detail.append(body); }}
  const idCode = document.createElement("code"); idCode.textContent = node.id; detail.append(idCode);
  if (node.group === "source" && /^https?:\\/\\//.test(node.record.uri || "")) {{ const link = document.createElement("a"); link.href = node.record.uri; link.target = "_blank"; link.rel = "noreferrer"; link.textContent = "Open publisher source"; link.style.display = "inline-block"; link.style.marginTop = "12px"; detail.append(link); }}
  render();
}}
function render() {{
  const visible = relatedToTopic(topicFilter.value);
  const byGroup = Object.fromEntries(groups.map(group => [group, graph.nodes.filter(node => node.group === group && visible.has(node.id))]));
  const maxRows = Math.max(1, ...groups.map(group => byGroup[group].length));
  const height = Math.max(190, 100 + maxRows * 90);
  const width = 1000, column = 140, cardWidth = 126, cardHeight = 66;
  svg.replaceChildren(); svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`); svg.setAttribute("height", height);
  groups.forEach((group, index) => {{ const title = svgElement("text", {{x: 32 + index * column, y: 31, class: "node-kind"}}); title.textContent = labels[group]; svg.append(title); }});
  const positions = new Map();
  groups.forEach((group, index) => byGroup[group].forEach((node, row) => positions.set(node.id, {{x: 16 + index * column, y: 50 + row * 90}})));
  graph.edges.filter(edge => visible.has(edge.source) && visible.has(edge.target)).forEach(edge => {{ const a = positions.get(edge.source), b = positions.get(edge.target); if (!a || !b) return; const path = svgElement("path", {{class: `edge${{edge.evidence ? " evidence" : ""}}`, d: `M ${{a.x + cardWidth}} ${{a.y + cardHeight / 2}} C ${{a.x + cardWidth + 24}} ${{a.y + cardHeight / 2}}, ${{b.x - 24}} ${{b.y + cardHeight / 2}}, ${{b.x}} ${{b.y + cardHeight / 2}}`}}); svg.append(path); }});
  groups.forEach(group => byGroup[group].forEach(node => {{ const point = positions.get(node.id); const g = svgElement("g", {{class: `node ${{node.group}}${{selectedId === node.id ? " selected" : ""}}`, tabindex: "0", role: "button", "aria-label": `${{node.group}}: ${{node.label}}`}}); const rect = svgElement("rect", {{x: point.x, y: point.y, width: cardWidth, height: cardHeight}}); g.append(rect); const kind = svgElement("text", {{x: point.x + 10, y: point.y + 16, class: "node-kind"}}); kind.textContent = node.group.toUpperCase(); g.append(kind); wrap(node.label, 17).forEach((line, i) => {{ const label = svgElement("text", {{x: point.x + 10, y: point.y + 33 + i * 14, class: "node-label"}}); label.textContent = line; g.append(label); }}); g.addEventListener("click", () => showDetail(node.id)); g.addEventListener("keydown", event => {{ if (event.key === "Enter" || event.key === " ") {{ event.preventDefault(); showDetail(node.id); }} }}); svg.append(g); }}));
}}
topicFilter.addEventListener("change", () => {{ selectedId = null; detail.innerHTML = '<h2>Choose a record</h2><p class="meta">Select a node to inspect its identifier, details, and provenance.</p>'; render(); }});
addOptions(); render();
</script>
</body>
</html>"""


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
