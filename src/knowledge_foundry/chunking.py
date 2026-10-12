"""Deterministic structural chunking of saved parsing artifacts (schema v1.0.0)."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
PIPELINE_VERSION = "chunking-v1"
CHUNKER_VERSION = "structural-v1"
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+", re.UNICODE)
SCHEMA = json.loads((ROOT / "schemas/artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def token_count(text):
    """Reproducible Unicode word/punctuation budget, independent of model tokens."""
    return len(TOKEN.findall(text))


def clean(text, table=False):
    """Normalize layout whitespace only; keep punctuation, case, and wording."""
    text = text.replace("\u00ad", "")
    if table:
        return "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                         for line in text.splitlines() if line.strip())
    return re.sub(r"\s+", " ", text).strip()


def settings_from_config(config):
    value = config.get("chunking", {})
    if not isinstance(value, dict) or set(value) - {"max_tokens"}:
        raise ValueError("chunking must contain only max_tokens")
    maximum = value.get("max_tokens", 256)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 32:
        raise ValueError("chunking.max_tokens must be an integer >= 32")
    return {"max_tokens": maximum}


def _parts(text, maximum, table=False):
    """Return nonempty source spans, preferring table lines or sentence breaks."""
    if not text.strip():
        return []
    if token_count(clean(text)) <= maximum:
        return [(0, len(text))]
    boundaries = [match.end() for match in re.finditer(r"\n+" if table else SENTENCE_END, text)]
    boundaries.append(len(text))
    spans = []
    start = 0
    for end in boundaries:
        if not text[start:end].strip():
            start = end
            continue
        if token_count(clean(text[start:end])) > maximum:
            if start < end:
                spans.extend(_token_parts(text, start, end, maximum))
            start = end
            continue
        if spans and spans[-1][1] == start and token_count(clean(text[spans[-1][0]:end])) <= maximum:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
        start = end
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _token_parts(text, start, end, maximum):
    spans = []
    current_start = None
    current_end = None
    count = 0
    for word in re.finditer(r"\S+", text[start:end]):
        word_start, word_end = start + word.start(), start + word.end()
        tokens = token_count(word.group())
        if tokens > maximum:
            if current_start is not None:
                spans.append((current_start, current_end))
                current_start = None
                count = 0
            matches = list(TOKEN.finditer(text, word_start, word_end))
            spans.extend((matches[i].start(), matches[min(i + maximum, len(matches)) - 1].end())
                         for i in range(0, len(matches), maximum))
            continue
        if current_start is not None and count + tokens > maximum:
            spans.append((current_start, current_end))
            current_start = None
            count = 0
        if current_start is None:
            current_start = word_start
        current_end = word_end
        count += tokens
    if current_start is not None:
        spans.append((current_start, current_end))
    return spans


def _location_key(element):
    location = element["location"]
    if location["format"] == "pdf" and "bbox" in location:
        return ("pdf", location["page"], tuple(location["bbox"]))
    return (element["locator_key"],)


def _reference(element, source_id, span=None):
    ref = {"source_id": source_id, "document_id": element["document_id"],
           "element_id": element["id"], "element_revision": element["revision"]}
    if span is not None:
        ref["span"] = {"start": span[0], "end": span[1]}
    return ref


def _context(path, headings, budget):
    """Use only earlier authoritative heading elements, deepest first if tight."""
    selected = []
    for depth in range(len(path), 0, -1):
        heading = headings.get(tuple(path[:depth]))
        if heading and token_count(clean(heading["text"])) + sum(token_count(clean(x["text"])) for x in selected) <= budget:
            selected.append(heading)
    return list(reversed(selected))


def _chunk_document(bundle, sidecar, build_id, settings):
    document = bundle["documents"][0]
    source_id = document["source_id"]
    info = {item["element_id"]: item for item in sidecar["elements"]}
    elements = bundle["elements"]
    if len(info) != len(elements) or set(info) != {item["id"] for item in elements}:
        raise ValueError(f"sidecar elements do not match bundle: {document['id']}")
    if [item["ordinal"] for item in elements] != list(range(len(elements))):
        raise ValueError(f"elements out of reading order: {document['id']}")
    chunks = []
    headings = {}
    pending = []
    section = None
    seen = set()
    metrics = {"authoritative_elements": 0, "structural_elements": 0,
               "auxiliary_elements": 0, "exact_duplicates_removed": 0,
               "repeated_text_distinct_locations": 0, "chunks": 0}
    text_locations = {}
    maximum = settings["max_tokens"]

    def emit():
        nonlocal pending
        if not pending:
            return
        evidence = [ref for _, ref in pending]
        text = "\n\n".join(part for part, _ in pending)
        if token_count(text) > maximum:
            raise ValueError("internal token budget exceeded")
        chunk = {"schema_version": SCHEMA_VERSION, "document_id": document["id"],
                 "build_id": build_id, "chunker_version": CHUNKER_VERSION,
                 "sequence": len(chunks), "text": text, "evidence": evidence}
        chunk["id"] = "chunk:" + digest(document["id"] + "\n" + CHUNKER_VERSION + "\n" + canonical(evidence) + "\n" + text)
        chunks.append(chunk)
        pending = []

    for element in elements:
        details = info[element["id"]]
        role = details.get("evidence_role")
        if role not in {"authoritative", "structural", "auxiliary"}:
            raise ValueError(f"unknown evidence_role for {element['id']}")
        metrics[role + "_elements"] += 1
        if role != "authoritative" or not clean(element["text"]):
            continue
        if element["kind"] in {"table_cell", "figure"}:
            raise ValueError(f"non-authoritative kind marked authoritative: {element['id']}")
        location = _location_key(element)
        is_table = element["kind"] == "table"
        cleaned = clean(element["text"], table=is_table)
        duplicate_key = (location, element["kind"], cleaned)
        if duplicate_key in seen:
            metrics["exact_duplicates_removed"] += 1
            continue
        seen.add(duplicate_key)
        earlier_locations = text_locations.setdefault((element["kind"], cleaned), set())
        if earlier_locations and location not in earlier_locations:
            metrics["repeated_text_distinct_locations"] += 1
        earlier_locations.add(location)
        path = tuple(details.get("heading_path") or [])
        if path != section:
            emit()
            section = path
        if is_table:
            emit()  # A table starts independently of preceding prose.
        if element["kind"] == "heading":
            headings[path] = element
        # Reserve context space, while guaranteeing room for source text.
        context = _context(path, headings, max(0, maximum // 4))
        if element["kind"] == "heading":
            context = [item for item in context if item["id"] != element["id"]]
        if element["kind"] == "list_item" and token_count(cleaned) <= maximum:
            # An optional ancestor heading must not force an otherwise fitting
            # list item to split. Drop outer headings before the nearest one.
            while context and token_count(cleaned) + token_count(
                "\n\n".join(clean(heading["text"]) for heading in context)
            ) > maximum:
                context.pop(0)
        # A heading is included once per chunk and cited as supporting context.
        if not pending:
            for heading in context:
                pending.append((clean(heading["text"]), _reference(heading, source_id)))
        # Split against a fresh chunk's budget. A sentence that fits there
        # must move intact instead of being split to fill the current chunk.
        fresh_budget = maximum - token_count("\n\n".join(clean(heading["text"]) for heading in context))
        pieces = _parts(element["text"], fresh_budget, table=is_table)
        for span in pieces:
            part = clean(element["text"][span[0]:span[1]], table=is_table)
            if not part:
                continue
            if pending and token_count("\n\n".join([*(p for p, _ in pending), part])) > maximum:
                emit()
                for heading in context:
                    pending.append((clean(heading["text"]), _reference(heading, source_id)))
            if token_count("\n\n".join([*(p for p, _ in pending), part])) > maximum:
                raise ValueError("split passage exceeds fresh chunk budget")
            pending.append((part, _reference(element, source_id, span)))
        if element["kind"] == "table":
            emit()  # Do not mix table rows with prose or another table.
    emit()
    metrics["chunks"] = len(chunks)
    return chunks, metrics


def build_collection(config_path, input_dir, output_dir):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    settings = settings_from_config(config)
    input_dir = Path(input_dir).resolve()
    paths = sorted(input_dir.glob("documents/*/artifact-bundle.json"))
    if not paths:
        raise ValueError(f"no parsed bundles in {input_dir}")
    ordered = []
    for path in paths:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        sidecar = json.loads(path.with_name("parse.json").read_text(encoding="utf-8"))
        Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(bundle)
        if len(bundle["sources"]) != 1 or len(bundle["documents"]) != 1 or any(bundle[key] for key in ("chunks", "claims", "topics", "builds")):
            raise ValueError(f"expected one Task 4 parsing bundle: {path}")
        if sidecar["document_id"] != bundle["documents"][0]["id"] or path.parent.name != bundle["documents"][0]["id"].removeprefix("doc:"):
            raise ValueError(f"sidecar or folder document mismatch: {path}")
        ordered.append((bundle, sidecar, path.parent))
    # Configured manifest order is the public reading order across documents.
    manifest = json.loads((Path(config_path).resolve().parent / config["root"] / config["manifest_file"]).resolve().read_text(encoding="utf-8"))
    by_source = {bundle["sources"][0]["id"]: (bundle, sidecar, folder) for bundle, sidecar, folder in ordered}
    selected = [entry["source_id"] for manifest_id in config["manifest_ids"] for entry in manifest["sources"] if entry["manifest_id"] == manifest_id]
    if len(selected) != len(ordered) or len(set(selected)) != len(selected) or set(selected) != set(by_source):
        raise ValueError("parsed documents do not match configured manifest_ids")
    ordered = [by_source[source_id] for source_id in selected]
    ids = [bundle["documents"][0]["id"] for bundle, _, _ in ordered]
    config_sha = digest(canonical({"chunking": settings, "chunker_version": CHUNKER_VERSION}))
    build_id = "build:" + digest(PIPELINE_VERSION + "\n" + config_sha + "\n" + "\n".join(sorted(ids)))
    result = {"schema_version": SCHEMA_VERSION, "sources": [], "documents": [],
              "elements": [], "chunks": [], "claims": [], "topics": [], "builds": []}
    figures = []
    document_views = {}
    report = {"schema_version": SCHEMA_VERSION, "build_id": build_id,
              "settings": settings, "documents": []}
    for bundle, sidecar, folder in ordered:
        document = bundle["documents"][0]
        result["sources"].extend(bundle["sources"])
        result["documents"].extend(bundle["documents"])
        result["elements"].extend(bundle["elements"])
        chunks, metrics = _chunk_document(bundle, sidecar, build_id, settings)
        result["chunks"].extend(chunks)
        document_report = {"source_id": bundle["sources"][0]["id"],
                           "document_id": document["id"], **metrics}
        report["documents"].append(document_report)
        document_figures = []
        for item in sidecar["elements"]:
            asset = item.get("asset")
            if asset:
                element = next(e for e in bundle["elements"] if e["id"] == item["element_id"])
                if element["kind"] != "figure" or asset["representation_role"] != "authoritative_visual":
                    raise ValueError(f"invalid figure asset: {item['element_id']}")
                file = folder / asset["path"]
                if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != asset["sha256"]:
                    raise ValueError(f"figure asset hash mismatch: {file}")
                link = {"source_id": bundle["sources"][0]["id"],
                        "document_id": document["id"],
                        "element_id": element["id"], "element_revision": element["revision"],
                        "location": element["location"], "asset": {**asset, "path": str(file.relative_to(input_dir))}}
                figures.append(link)
                document_figures.append(link)
        document_views[document["id"]] = {
            "view_version": 1, "build_id": build_id,
            "source": bundle["sources"][0], "document": document,
            "elements": bundle["elements"], "chunks": chunks,
            "figure_links": document_figures, "report": document_report,
        }
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id,
                             "pipeline_version": PIPELINE_VERSION, "config_sha256": config_sha,
                             "input_document_ids": ids,
                             "output_chunk_ids": [item["id"] for item in result["chunks"]],
                             "output_claim_ids": [], "output_topic_ids": [], "status": "complete"})
    from scripts.validate_artifact_examples import check
    check(result)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (("artifact-bundle.json", result), ("figure-links.json", figures), ("report.json", report)):
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    views_dir = output_dir / "documents"
    for document_id, view in document_views.items():
        folder = views_dir / document_id.removeprefix("doc:")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "document-view.json").write_text(
            json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current_folders = {document_id.removeprefix("doc:") for document_id in document_views}
    for stale in sorted(views_dir.glob("*/document-view.json")):
        if stale.parent.name not in current_folders:
            stale.unlink()
            try:
                stale.parent.rmdir()
            except OSError:
                pass  # Leave a folder containing files that this stage does not own.
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "artifacts/parsed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/chunked")
    args = parser.parse_args(argv)
    try:
        report = build_collection(args.config, args.input_dir, args.output_dir)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"chunking failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
