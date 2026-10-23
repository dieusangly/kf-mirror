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
CHUNKER_VERSION = "structural-v4"
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
                if table and spans and spans[-1][1] == start:
                    room = maximum - token_count(clean(text[spans[-1][0]:start], table=True))
                    if room >= min(32, maximum // 4):
                        prefix = _token_parts(text, start, end, room)[0]
                        spans[-1] = (spans[-1][0], prefix[1])
                        start = prefix[1]
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


def _table_parts(text, maximum):
    """Split table rows while repeating the first serialized header row."""
    lines = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line.rstrip("\r\n"))
        if line.strip():
            lines.append((offset, end))
        offset += len(line)
    if len(lines) < 2 or token_count(clean(text[lines[0][0]:lines[0][1]], table=True)) >= maximum:
        return [[span] for span in _parts(text, maximum, table=True)]
    header = lines[0]
    header_tokens = token_count(clean(text[header[0]:header[1]], table=True))
    chunks = []
    current = [header]
    current_tokens = header_tokens
    for row in lines[1:]:
        row_tokens = token_count(clean(text[row[0]:row[1]], table=True))
        if current_tokens + row_tokens <= maximum:
            current.append(row)
            current_tokens += row_tokens
            continue
        if len(current) > 1:
            chunks.append(current)
            current = [header]
            current_tokens = header_tokens
        available = maximum - header_tokens
        if row_tokens <= available:
            current.append(row)
            current_tokens += row_tokens
            continue
        for piece in _token_parts(text, row[0], row[1], max(1, available)):
            chunks.append([header, piece])
    if len(current) > 1 or not chunks:
        chunks.append(current)
    return chunks


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
    active_lead = None
    active_lead_is_oversized = False
    compact_lead = None
    first_numbered = None
    last_flow = None
    previous_authoritative = None
    step_reference = None
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

    def pending_body_kinds():
        return [by_id[ref["element_id"]]["kind"] for _, ref in pending if "span" in ref]

    by_id = {element["id"]: element for element in elements}
    authoritative = [element for element in elements
                     if info[element["id"]].get("evidence_role") == "authoritative" and
                     clean(element["text"]) and element["kind"] != "table_cell"]
    flow = [element for element in authoritative if element["kind"] != "footnote"]
    next_body = {element["id"]: flow[index + 1] if index + 1 < len(flow) else None
                 for index, element in enumerate(flow)}

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
            if pending_body_kinds() and pending_body_kinds() != ["heading"] * len(pending_body_kinds()):
                emit()
            section = path
        body_kinds = pending_body_kinds()
        if body_kinds and ((element["kind"] == "footnote") != (body_kinds[-1] == "footnote")):
            emit()
        following = next_body.get(element["id"])
        is_lead = (element["kind"] in {"paragraph", "list_item"} and
                   cleaned.endswith((":", ",", "…")) and
                   following is not None and following["kind"] == "list_item" and
                   tuple(info[following["id"]].get("heading_path") or []) == path)
        is_unmarked_list_continuation = (
            not is_lead and active_lead is not None and element["kind"] == "paragraph" and
            last_flow is not None and last_flow["kind"] == "list_item" and
            tuple(info[last_flow["id"]].get("heading_path") or []) == path and
            re.match(r"^[a-zà-ÿ]", cleaned)
        )
        is_subheading = (element["kind"] == "list_item" and following is not None and
                         following["kind"] == "paragraph" and token_count(cleaned) <= 32 and
                         not cleaned.endswith((".", ";", ":", "?", "!")) and
                         tuple(info[following["id"]].get("heading_path") or []) == path)
        if is_lead or is_subheading:
            if pending_body_kinds() and any(kind != "heading" for kind in pending_body_kinds()):
                emit()
            active_lead = element if is_lead else None
            active_lead_is_oversized = False
            compact_lead = None
        elif (active_lead and element["kind"] == "list_item" and re.match(r"^\d+[.)]\s", cleaned) and
              (active_lead["kind"] == "list_item" or
               (active_lead['location'].get('bbox') and element['location'].get('bbox') and
                element['location']['bbox'][0] < active_lead['location']['bbox'][0] - 2))):
            if pending_body_kinds() and any(kind != "heading" for kind in pending_body_kinds()):
                emit()
            active_lead = None
            active_lead_is_oversized = False
            compact_lead = None
        elif (active_lead and element["kind"] not in {"list_item", "footnote"} and
              not is_unmarked_list_continuation):
            if pending_body_kinds() and any(kind != "heading" for kind in pending_body_kinds()):
                emit()
            active_lead = None
            active_lead_is_oversized = False
            compact_lead = None
        if is_table and any(kind != "heading" for kind in pending_body_kinds()):
            emit()  # A table starts independently of preceding prose.
        if element["kind"] == "heading":
            headings[path] = element
            step_reference = None
            first_numbered = None
            last_flow = None
        # Reserve context space, while guaranteeing room for source text.
        context = _context(path, headings, max(0, maximum // 4))
        if element["kind"] == "footnote":
            context = []  # Page-bottom notes may refer to an earlier section on the page.
        if element["kind"] == "heading":
            context = [item for item in context if item["id"] != element["id"]]
        if is_lead:
            group = [element]
            cursor = following
            while cursor and cursor["kind"] == "list_item" and tuple(
                    info[cursor["id"]].get("heading_path") or []) == path:
                group.append(cursor)
                cursor = next_body.get(cursor["id"])
            group_tokens = sum(token_count(clean(item["text"])) for item in group)
            full_tokens = group_tokens + sum(token_count(clean(item["text"])) for item in context)
            short_tokens = group_tokens + (token_count(clean(context[-1]["text"])) if context else 0)
            active_lead_is_oversized = group_tokens > maximum
            if full_tokens > maximum >= short_tokens:
                compact_lead = element["id"]
        if (active_lead_is_oversized and active_lead and
                (element["kind"] == "list_item" or is_unmarked_list_continuation) and
                any("span" in ref and ref["element_id"] != active_lead["id"]
                    for _, ref in pending)):
            # A lead such as "one of the following conditions" must not govern
            # only an unlabeled subset of an oversized list. Start every item or
            # unmarked continuation with the cited governing lead instead.
            emit()
        if compact_lead and active_lead and compact_lead == active_lead["id"]:
            context = context[-1:]
        extra_context = []
        if element["kind"] == "list_item" and active_lead and active_lead["id"] != element["id"]:
            extra_context.append(active_lead)
        elif is_unmarked_list_continuation:
            extra_context.append(active_lead)
        if element["kind"] == "list_item" and re.match(r"^1\s", cleaned):
            first_numbered = element
        elif element["kind"] == "list_item" and first_numbered and re.match(r"^[2-9]\s", cleaned):
            if first_numbered["id"] != element["id"] and not active_lead:
                extra_context.append(first_numbered)
        elif element["kind"] not in {"footnote", "heading"}:
            first_numbered = None
        step_number = re.match(r"^(\d+)[.)\s]", cleaned) if element["kind"] == "list_item" else None
        if step_reference and step_number and int(step_number.group(1)) == step_reference[0]:
            extra_context.insert(0, step_reference[1])
        if (element["kind"] == "paragraph" and not is_unmarked_list_continuation and last_flow and
                last_flow["kind"] in {"paragraph", "list_item"} and
                last_flow["location"].get("format") == element["location"].get("format") == "pdf" and
                element["location"].get("page", 0) - last_flow["location"].get("page", 0) in {0, 1} and
                not clean(last_flow["text"]).endswith((".", "!", "?", ";", ":")) and
                re.match(r"^[a-zà-ÿ]", cleaned)):
            extra_context.append(last_flow)
            emit()
        while extra_context and token_count(cleaned) + sum(token_count(clean(item["text"]))
                for item in extra_context) > maximum:
            extra_context.pop()
        while context and token_count(cleaned) + sum(token_count(clean(item["text"]))
                for item in [*context, *extra_context]) > maximum:
            context.pop(0)
        while extra_context and token_count(cleaned) + sum(token_count(clean(item["text"]))
                for item in [*context, *extra_context]) > maximum:
            extra_context.pop()
        if element["kind"] == "list_item" and token_count(cleaned) <= maximum:
            # An optional ancestor heading must not force an otherwise fitting
            # list item to split. Drop outer headings before the nearest one.
            while context and token_count(cleaned) + token_count(
                "\n\n".join(clean(heading["text"]) for heading in context)
            ) > maximum:
                context.pop(0)
        # A heading is included once per chunk and cited as supporting context.
        if not pending:
            for item in [*context, *extra_context]:
                pending.append((clean(item["text"]), _reference(item, source_id)))
        # Split against a fresh chunk's budget. A sentence that fits there
        # must move intact instead of being split to fill the current chunk.
        fresh_budget = maximum - token_count("\n\n".join(
            clean(item["text"]) for item in [*context, *extra_context]))
        if pending and all(kind == "heading" for kind in pending_body_kinds()) and pending_body_kinds():
            if element["kind"] == "list_item" and token_count(cleaned) <= maximum and \
                    token_count(cleaned) + token_count("\n\n".join(part for part, _ in pending)) > maximum:
                emit()
            fresh_budget = min(fresh_budget, maximum - token_count(
                "\n\n".join(part for part, _ in pending)))
        fresh_budget = max(1, fresh_budget)
        if is_table:
            for table_piece in _table_parts(element["text"], fresh_budget):
                if not pending:
                    for item in [*context, *extra_context]:
                        pending.append((clean(item["text"]), _reference(item, source_id)))
                for span in table_piece:
                    part = clean(element["text"][span[0]:span[1]], table=True)
                    if not part:
                        continue
                    if token_count("\n\n".join([*(p for p, _ in pending), part])) > maximum:
                        raise ValueError("split table exceeds fresh chunk budget")
                    pending.append((part, _reference(element, source_id, span)))
                emit()  # Do not mix table rows with prose or another table.
        else:
            pieces = _parts(element["text"], fresh_budget)
            for span in pieces:
                part = clean(element["text"][span[0]:span[1]])
                if not part:
                    continue
                if pending and token_count("\n\n".join([*(p for p, _ in pending), part])) > maximum:
                    emit()
                    for item in [*context, *extra_context]:
                        pending.append((clean(item["text"]), _reference(item, source_id)))
                if token_count("\n\n".join([*(p for p, _ in pending), part])) > maximum:
                    raise ValueError("split passage exceeds fresh chunk budget")
                pending.append((part, _reference(element, source_id, span)))
        footnote_like_paragraph = (
            element["kind"] == "paragraph" and previous_authoritative and
            previous_authoritative["kind"] == "footnote" and re.match(r"^\d+\s", cleaned)
        )
        if element["kind"] not in {"heading", "footnote", "table"} and not footnote_like_paragraph:
            last_flow = element
        previous_authoritative = element
        if element["kind"] == "paragraph":
            mentioned_step = re.search(r"\b(?:point|étape)\s+(\d+)\b", cleaned, re.I)
            if mentioned_step:
                step_reference = (int(mentioned_step.group(1)), element)
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
            raise ValueError(f"expected one parsed-document bundle: {path}")
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
    structure_elements = []
    document_views = {}
    report = {"schema_version": SCHEMA_VERSION, "build_id": build_id,
              "settings": settings, "documents": []}
    for bundle, sidecar, folder in ordered:
        structure_elements.extend(sidecar['elements'])
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
    from knowledge_foundry.document_structure import structure_snapshot, validate_structure
    structure = structure_snapshot(structure_elements)
    validate_structure(result, structure)
    (output_dir / 'document-structure.json').write_text(
        json.dumps(structure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
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
