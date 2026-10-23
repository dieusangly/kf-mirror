"""Audit normalized parsing artifacts and source-checked landmarks."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

from lxml import html
from jsonschema import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts.validate_artifact_examples import check as validate_bundle  # noqa: E402
from knowledge_foundry.parsers import (  # noqa: E402
    _pdf_text, FILENAME_FOOTNOTE, parse_markdown, parse_docx,
    _picture_contact_furniture, _picture_group_roles, _picture_text_groups, _picture_text_quality,
)
from knowledge_foundry.pdf_structure import pdf_page_characters, restore_numeric_identifiers  # noqa: E402

SUBSTANTIVE_TOKENS = 5
SUBSTANTIVE_CHARS = 30
SEMANTIC_KINDS = {"paragraph", "heading", "list_item", "table"}
KNOWN_DOCLING_LABELS = {"text", "section_header", "title", "list_item", "table",
                        "document_index", "picture", "caption", "footnote",
                        "page_header", "page_footer", "checkbox_selected",
                        "checkbox_unselected"}
HARMLESS_LIMITATIONS = {"excluded_picture_ocr_fragments", "excluded_picture_corrupt_ocr",
                        "excluded_picture_furniture", "excluded_figure_text_uncertain",
                        "empty_docling_table"}
DOCUMENTED_LIMITATIONS = HARMLESS_LIMITATIONS | {"potential_picture_text"}
METRIC_KEYS = ("source_text_checked", "source_text_lost", "duplicate_content_groups",
               "duplicate_content_excess", "multi_provenance_nodes", "multi_page_nodes",
               "multi_page_duplicated", "empty_semantic_elements", "picture_text_checked",
               "picture_text_lost", "picture_text_duplicated", "tables_with_cells",
               "table_duplication_risk", "table_cells_at_risk", "malformed_parents",
               "unsupported_structures", "parser_warnings", "excluded_furniture_nodes",
               "excluded_short_text_nodes", "picture_short_text_checked",
               "picture_short_text_lost", "picture_justified_exclusions",
               "picture_potential_exclusions", "excluded_corrupt_ocr_nodes",
               "excluded_contact_furniture_nodes", "figure_assets_checked",
               "figure_assets_missing", "figure_text_aux_missing", "source_repetition_groups")


def normalized(text):
    text = unicodedata.normalize("NFKD", _pdf_text(text)).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def matches(elements, sidecar, reference):
    wanted = normalized(reference["text"])
    found = []
    for element, details in zip(elements, sidecar):
        if reference.get("page") != element["location"].get("page") and "page" in reference:
            continue
        if reference.get("kind") and reference["kind"] != element["kind"]:
            continue
        if reference.get("header") and not details.get("header"):
            continue
        if wanted in normalized(element["text"]):
            found.append(element)
    return found


def substantive(value):
    words = normalized(value).split()
    return len(words) >= SUBSTANTIVE_TOKENS and sum(map(len, words)) >= SUBSTANTIVE_CHARS


def identifier_counts(value):
    """Numeric separators carry meaning even when prose punctuation does not."""
    return Counter(re.sub(r"\s+", "", token).translate(str.maketrans({'‐': '-', '‑': '-'}))
                   for token in re.findall(r"(?<![\w-])\d+(?:[-‐‑]\s*\d+)+(?![\w-])", value))


def covers_source_fragment(expected, actual):
    return (normalized(expected) in normalized(actual) and
            not (identifier_counts(expected) - identifier_counts(actual)))


def audit_document(outcome, elements, sidecar, folder, *, source_path=None):
    """Compare retrievable elements with the saved Docling source when available."""
    metrics = {key: 0 for key in METRIC_KEYS}
    metrics["heading_depth_distribution"] = dict(sorted(Counter(
        str(len(detail.get("heading_path", element['location'].get('heading_path')) or []))
        for element, detail in zip(elements, sidecar)
        if element["kind"] == "heading").items()))
    findings = {}

    def flag(code, severity, example):
        entry = findings.setdefault(code, {"code": code, "severity": severity,
                                           "count": 0, "examples": []})
        if severity == "error":
            entry["severity"] = "error"
        entry["count"] += 1
        if len(entry["examples"]) < 5:
            entry["examples"].append(example)

    ids = {element["id"]: element for element in elements}
    details = {detail["element_id"]: detail for detail in sidecar}
    repeated_pages = defaultdict(set)
    page_counts = Counter()
    for element in elements:
        if element["location"].get("format") == "pdf":
            repeated_pages[normalized(element["text"])].add(element["location"].get("page"))
            page_counts[element["location"].get("page")] += 1
    if len(elements) != len(sidecar) or set(ids) != set(details):
        flag("SIDECAR_MISMATCH", "error", {"elements": len(elements), "sidecar": len(sidecar)})
    by_ref = defaultdict(list)
    by_page_text = defaultdict(list)
    for element in elements:
        detail = details.get(element["id"], {})
        for ref in detail.get("docling_refs") or ([detail["docling_ref"]]
                                                  if detail.get("docling_ref") else []):
            by_ref[ref].append(element)
        value = normalized(element["text"])
        if value:
            by_page_text[(element["location"].get("page"), value)].append(element)
        if element["kind"] in SEMANTIC_KINDS and not element["text"].strip():
            metrics["empty_semantic_elements"] += 1
            flag("EMPTY_SEMANTIC_ELEMENT", "error", {"element_id": element["id"],
                                                       "kind": element["kind"]})
        expected_role = "structural" if element["kind"] in {"table_cell", "figure"} else "authoritative"
        filename_note = (element['kind'] == 'footnote' and
                         detail.get('evidence_role') == 'structural' and
                         detail.get('link_text_role') == 'non_substantive_filename' and
                         FILENAME_FOOTNOTE.fullmatch(element['text']))
        parent_id = detail.get("parent_element_id")
        auxiliary = (detail.get("evidence_role") == "auxiliary" and parent_id in ids and
                     ids[parent_id]["kind"] == "figure" and element["kind"] not in
                     {"table_cell", "figure"})
        cover_furniture = (detail.get("evidence_role") == "auxiliary" and
                           element["kind"] == "paragraph" and parent_id is None and
                           element["location"].get("format") == "pdf" and
                           element["location"].get("page") == 1 and
                           len(element["text"].split()) < 15)
        running_header = (detail.get("evidence_role") == "structural" and
                          element["kind"] == "paragraph" and
                          len(repeated_pages[value]) >= 3)
        drop_cap = (detail.get("evidence_role") == "structural" and
                    element["kind"] == "paragraph" and
                    len(element["text"].strip()) == 1 and
                    element["text"].strip().isalpha() and
                    element["location"].get("format") == "pdf" and
                    bool(element["location"].get("bbox")))
        sparse_fragment = (detail.get("evidence_role") == "structural" and
                           element["kind"] == "paragraph" and
                           element["location"].get("format") == "pdf" and
                           page_counts[element["location"].get("page")] <= 10 and
                           len(re.sub(r"\W", "", element["text"])) <= 4)
        if detail.get("evidence_role") != expected_role and not (
                auxiliary or cover_furniture or running_header or drop_cap or sparse_fragment or filename_note):
            flag("INVALID_EVIDENCE_ROLE", "error", {"element_id": element["id"],
                                                    "kind": element["kind"],
                                                    "role": detail.get("evidence_role")})
        if (detail.get("evidence_role") == "authoritative" and
                str(detail.get("docling_parent_ref", "")).startswith("#/pictures/") and
                element["text"].lstrip().startswith(("- ", "• ")) and
                element["kind"] != "list_item"):
            flag("PICTURE_LIST_ITEM_KIND_MISMATCH", "error",
                 {"element_id": element["id"], "text": element["text"][:120]})
        if parent_id:
            parent = ids.get(parent_id)
            invalid = (parent is None or parent_id == element["id"] or
                       parent["ordinal"] >= element["ordinal"] or
                       (element["kind"] == "table_cell" and parent["kind"] != "table"))
            if invalid:
                metrics["malformed_parents"] += 1
                flag("MALFORMED_PARENT", "error", {"element_id": element["id"],
                                                   "parent_element_id": parent_id})
        elif element["kind"] == "table_cell":
            metrics["malformed_parents"] += 1
            flag("MALFORMED_PARENT", "error", {"element_id": element["id"],
                                               "parent_element_id": None})
        path = detail.get("heading_path", element['location'].get('heading_path'))
        if element["kind"] == "heading" and (not isinstance(path, list) or
                                              not path or normalized(path[-1]) != value):
            flag("BROKEN_HEADING_PATH", "error", {"element_id": element["id"]})
        parent_path = (details[parent_id].get('heading_path', ids[parent_id]['location'].get('heading_path'))
                       if parent_id in details and parent_id in ids else None)
        if parent_path and path and not auxiliary:
            if path[:len(parent_path)] != parent_path:
                flag("BROKEN_HEADING_PATH", "error", {"element_id": element["id"],
                                                      "parent_element_id": parent_id})

    for (page, value), group in by_page_text.items():
        if not substantive(value) or len(group) < 2:
            continue
        independent = [item for item in group
                       if details.get(item["id"], {}).get("evidence_role") == "authoritative"]
        if len(independent) > 1:
            duplicate_pairs = []
            for index, first in enumerate(independent):
                for second in independent[index + 1:]:
                    first_refs = set(details.get(first["id"], {}).get("docling_refs") or
                                     [details.get(first["id"], {}).get("docling_ref")]) - {None}
                    second_refs = set(details.get(second["id"], {}).get("docling_refs") or
                                      [details.get(second["id"], {}).get("docling_ref")]) - {None}
                    a, b = first["location"].get("bbox"), second["location"].get("bbox")
                    distinct_boxes = bool(a and b and (a[2] <= b[0] or b[2] <= a[0] or
                                                         a[3] <= b[1] or b[3] <= a[1]))
                    if first_refs & second_refs or not distinct_boxes:
                        duplicate_pairs.append((first["id"], second["id"]))
            if duplicate_pairs:
                metrics["duplicate_content_groups"] += 1
                metrics["duplicate_content_excess"] += len(duplicate_pairs)
                flag("DUPLICATE_CONTENT", "error", {"page": page,
                                                     "element_ids": list(duplicate_pairs[0]),
                                                     "text": value[:100]})
            else:
                metrics["source_repetition_groups"] += 1

    for element in elements:
        if element["kind"] != "table":
            continue
        children = [item for item in elements
                    if details.get(item["id"], {}).get("parent_element_id") == element["id"]
                    and item["kind"] == "table_cell" and item["text"].strip()]
        if children:
            metrics["tables_with_cells"] += 1
            if any(normalized(child["text"]) in normalized(element["text"]) for child in children):
                # The aggregate is the sole retrieval evidence; cells carry coordinates
                # and structure and must never be chunked as independent evidence.
                if (details[element["id"]].get("evidence_role") != "authoritative" or
                        any(details[child["id"]].get("evidence_role") != "structural"
                            for child in children)):
                    metrics["table_duplication_risk"] += 1
                    metrics["table_cells_at_risk"] += len(children)
                    flag("TABLE_PARENT_CHILD_DUPLICATION", "error",
                         {"table_id": element["id"], "cell_count": len(children)})

    saved_warnings = json.loads((folder / "parse.json").read_text(encoding="utf-8")).get("warnings", [])
    all_warnings = outcome.get("warnings", []) + [warning for warning in saved_warnings
                                                   if warning not in outcome.get("warnings", [])]
    for warning in outcome.get("warnings", []):
        metrics["parser_warnings"] += 1
        flag("PARSER_WARNING", "warning" if warning.get("code") in DOCUMENTED_LIMITATIONS
             else "error", warning)
    for warning in saved_warnings:
        if warning not in outcome.get("warnings", []):
            metrics["parser_warnings"] += 1
            flag("PARSER_WARNING", "warning" if warning.get("code") in DOCUMENTED_LIMITATIONS
                 else "error", warning)
    incomplete = any(warning.get("code") not in HARMLESS_LIMITATIONS for warning in all_warnings)
    if outcome.get("status") in {"parsed", "partial"} and (outcome["status"] == "partial") != incomplete:
        flag("STATUS_COMPLETENESS_MISMATCH", "error",
             {"status": outcome["status"], "incomplete_warning": incomplete})

    native_path = folder / "docling.json"
    if native_path.is_file():
        native = json.loads(native_path.read_text(encoding="utf-8"))
        pages = native.get("pages", {})
        character_cache = {}

        def source_fragment(node, part):
            value = node.get("text", "")
            value = _pdf_text(value[slice(*part.get("charspan", [0, len(value)]))])
            box = part.get("bbox")
            page = part.get("page_no")
            size = native.get("pages", {}).get(str(page), {}).get("size", {})
            if (source_path and Path(source_path).is_file() and box and size.get("height")
                    and re.search(r"\b\d{3,}\b", value)):
                location = ([box["l"], box["t"], box["r"], box["b"]] if
                            box.get("coord_origin") == "TOPLEFT" else
                            [box["l"], size["height"] - box["t"], box["r"],
                             size["height"] - box["b"]])
                if page not in character_cache:
                    character_cache[page] = pdf_page_characters(source_path, page)
                value = restore_numeric_identifiers(value, location, character_cache[page])
            return value

        pictures_by_ref = {node["self_ref"]: node for node in native.get("pictures", [])}
        valid_figure_assets = set()
        for element in elements:
            if element["kind"] != "figure":
                continue
            detail = details.get(element["id"], {})
            location = element["location"]
            page = pages.get(str(location.get("page")))
            box = location.get("bbox")
            if pages and page is None:
                flag("FIGURE_PROVENANCE_INVALID", "error", {"element_id": element["id"],
                                                            "reason": "page_missing"})
            if page is not None:
                size = page["size"]
                if (not box or len(box) != 4 or not
                        (0 <= box[0] < box[2] <= size["width"] + 1 and
                         0 <= box[1] < box[3] <= size["height"] + 1)):
                    flag("FIGURE_PROVENANCE_INVALID", "error", {"element_id": element["id"]})
                source = pictures_by_ref.get(detail.get("docling_ref"), {})
                matches_source = False
                for part in source.get("prov") or []:
                    if part.get("page_no") != location.get("page") or not part.get("bbox"):
                        continue
                    raw = part["bbox"]
                    expected = ([raw["l"], raw["t"], raw["r"], raw["b"]] if
                                raw.get("coord_origin") == "TOPLEFT" else
                                [raw["l"], size["height"] - raw["t"], raw["r"],
                                 size["height"] - raw["b"]])
                    matches_source = bool(box and all(abs(a - b) <= .01
                                                      for a, b in zip(box, expected)))
                    if matches_source:
                        break
                if source and not matches_source:
                    flag("FIGURE_PROVENANCE_INVALID", "error", {"element_id": element["id"],
                                                                "reason": "docling_bbox_mismatch"})
            if not pages:
                continue
            asset = detail.get("asset")
            if not asset:
                metrics["figure_assets_missing"] += 1
                flag("FIGURE_ASSET_MISSING", "error", {"element_id": element["id"]})
                continue
            asset_path = asset.get("path")
            relative = Path(asset_path) if isinstance(asset_path, str) else Path()
            safe = (isinstance(asset_path, str) and not relative.is_absolute() and
                    relative.parts[:1] == ("figures",) and
                    ".." not in relative.parts and relative.suffix == ".png")
            if element["id"].startswith("el:") and asset_path != (
                    f"figures/{element['id'].removeprefix('el:')}.png"):
                safe = False
            candidate = folder / relative if safe else None
            if safe and not candidate.resolve().is_relative_to(folder.resolve()):
                safe = False
            payload = candidate.read_bytes() if safe and candidate.is_file() else b""
            correct = (safe and asset.get("media_type") == "image/png" and
                       asset.get("representation_role") == "authoritative_visual" and
                       isinstance(asset.get("sha256"), str) and len(asset["sha256"]) == 64 and
                       payload.startswith(b"\x89PNG\r\n\x1a\n") and
                       hashlib.sha256(payload).hexdigest() == asset["sha256"])
            if not correct:
                metrics["figure_assets_missing"] += 1
                flag("FIGURE_ASSET_INVALID", "error", {"element_id": element["id"],
                                                        "path": asset.get("path")})
            else:
                metrics["figure_assets_checked"] += 1
                valid_figure_assets.add((detail.get("docling_ref"), location.get("page")))
        native_by_ref = {node["self_ref"]: node for collection in ("texts", "groups")
                         for node in native.get(collection, [])}
        vocabulary = Counter(word.casefold() for node in native.get("texts", [])
                             for word in re.findall(r"[^\W\d_]+", node.get("text", ""),
                                                    flags=re.UNICODE))
        diagnosed_codes = defaultdict(set)
        for warning in all_warnings:
            if warning.get("code") in {"excluded_picture_ocr_fragments", "potential_picture_text",
                                       "excluded_picture_corrupt_ocr", "excluded_picture_furniture",
                                       "excluded_figure_text_uncertain"}:
                for ref in warning.get("refs", []):
                    diagnosed_codes[ref].add(warning["code"])
        picture_disposition = {}
        for picture in native.get("pictures", []):
            descendants = []

            def visit_picture(ref):
                node = native_by_ref.get(ref)
                if node is None:
                    return
                if ref.startswith("#/groups/"):
                    for child in node.get("children", []):
                        visit_picture(child["$ref"])
                elif ref.startswith("#/texts/") and node.get("content_layer") != "furniture":
                    descendants.append(node)

            for child in picture.get("children", []):
                visit_picture(child["$ref"])
            groups = _picture_text_groups(descendants)
            for group, role in zip(groups, _picture_group_roles(groups)):
                value = " ".join(node.get("text", "").strip() for node in group)
                furniture = _picture_contact_furniture(group, pages)
                quality = ("furniture" if furniture else
                           _picture_text_quality(value, group[0].get("label", "text"), vocabulary))
                for node in group:
                    picture_disposition[node["self_ref"]] = (role, quality, picture["self_ref"])
                if quality == "canonical":
                    short = [node for node in group if not substantive(node.get("text", ""))]
                    for node in short:
                        metrics["picture_short_text_checked"] += 1
                        ref = node["self_ref"]
                        represented = by_ref[ref]
                        if not represented or not any(
                            normalized(node.get("text", "")) in normalized(item["text"]) and
                            item["location"].get("page") == (node.get("prov") or [{}])[0].get("page_no")
                            for item in represented
                        ):
                            source_page = (node.get("prov") or [{}])[0].get("page_no")
                            if (role == "visual" and
                                    (picture["self_ref"], source_page) in valid_figure_assets):
                                metrics["figure_text_aux_missing"] += 1
                                flag("FIGURE_TEXT_NOT_SEARCHABLE", "warning",
                                     {"ref": ref, "picture_ref": picture["self_ref"]})
                            else:
                                metrics["picture_short_text_lost"] += 1
                                flag("PICTURE_SHORT_TEXT_LOST", "error",
                                     {"ref": ref, "text": node.get("text", "")[:120],
                                      "picture_ref": picture["self_ref"]})
                    for node in group:
                        for item in by_ref[node["self_ref"]]:
                            actual_role = details.get(item["id"], {}).get("evidence_role")
                            expected_role = "auxiliary" if role == "visual" else "authoritative"
                            if item['kind'] == 'footnote' and FILENAME_FOOTNOTE.fullmatch(item['text']):
                                expected_role = 'structural'
                            if actual_role != expected_role:
                                flag("FIGURE_TEXT_PROMOTED" if role == "visual" else
                                     "FLOW_TEXT_ROLE_INVALID", "error",
                                     {"ref": node["self_ref"], "element_id": item["id"],
                                      "role": actual_role})
                    if len(group) > 1 and all(by_ref[node["self_ref"]] for node in group):
                        common = set.intersection(*(set(item["id"] for item in by_ref[node["self_ref"]])
                                                    for node in group))
                        if not any(normalized(value) in normalized(ids[element_id]["text"])
                                   for element_id in common):
                            flag("PICTURE_GROUP_TEXT_MISMATCH", "error",
                                 {"refs": [node["self_ref"] for node in group]})
                else:
                    for node in group:
                        ref = node["self_ref"]
                        if by_ref[ref]:
                            continue
                        key = ("picture_potential_exclusions" if quality == "potential" and
                               role == "flow" else "picture_justified_exclusions")
                        metrics[key] += 1
                        if quality == "corrupt_ocr":
                            metrics["excluded_corrupt_ocr_nodes"] += 1
                        if quality == "furniture":
                            metrics["excluded_contact_furniture_nodes"] += 1
                        expected_code = ("excluded_picture_furniture" if quality == "furniture" else
                                         "excluded_picture_corrupt_ocr" if quality == "corrupt_ocr" else
                                         "excluded_figure_text_uncertain" if quality == "potential" and
                                         role == "visual" else
                                         "potential_picture_text" if quality == "potential" else
                                         "excluded_picture_ocr_fragments")
                        if not diagnosed_codes[ref]:
                            flag("UNDIAGNOSED_PICTURE_EXCLUSION", "error",
                                 {"ref": ref, "quality": quality})
                        elif expected_code not in diagnosed_codes[ref]:
                            flag("MISCLASSIFIED_PICTURE_EXCLUSION", "error",
                                 {"ref": ref, "quality": quality,
                                  "diagnosed_codes": sorted(diagnosed_codes[ref])})
        for detail in sidecar:
            parts = detail.get("source_parts")
            if parts is None:
                continue
            expected = [{"ref": ref, "text": native_by_ref[ref].get("text", ""),
                         "provenance": native_by_ref[ref].get("prov") or []}
                        for ref in detail.get("docling_refs", []) if ref in native_by_ref]
            if parts != expected or len(parts) < 2:
                flag("PICTURE_SOURCE_PART_MISMATCH", "error",
                     {"element_id": detail["element_id"]})
        for collection in ("key_value_items", "form_items"):
            for node in native.get(collection, []):
                metrics["unsupported_structures"] += 1
                flag("UNSUPPORTED_DOCLING_STRUCTURE", "error",
                     {"collection": collection, "ref": node.get("self_ref")})
        for node in native.get("texts", []):
            ref = node["self_ref"]
            label = node.get("label")
            if label not in KNOWN_DOCLING_LABELS:
                metrics["unsupported_structures"] += 1
                flag("UNSUPPORTED_DOCLING_LABEL", "warning", {"ref": ref, "label": label})
            if node.get("content_layer") == "furniture":
                if not by_ref[ref]:
                    metrics["excluded_furniture_nodes"] += 1
                continue
            disposition = picture_disposition.get(ref)
            if disposition and disposition[1] != "canonical":
                if disposition[1] == "furniture":
                    metrics["excluded_furniture_nodes"] += 1
                continue
            source_text = node.get("text", "")
            if not substantive(source_text):
                metrics["excluded_short_text_nodes"] += 1
                continue
            metrics["source_text_checked"] += 1
            picture = disposition is not None
            if picture:
                metrics["picture_text_checked"] += 1
            provenance = node.get("prov") or []
            pages = {part.get("page_no") for part in provenance}
            represented = by_ref[ref]
            pieces_covered = bool(provenance) and all(
                normalized(source_fragment(node, part)) and
                any(element["location"].get("page") == part.get("page_no") and
                    covers_source_fragment(source_fragment(node, part), element["text"])
                    for element in represented)
                for part in provenance)
            if not pieces_covered:
                if (disposition and disposition[0] == "visual" and
                        (disposition[2], next(iter(pages), None)) in valid_figure_assets):
                    if not by_ref[ref]:
                        metrics["figure_text_aux_missing"] += 1
                        flag("FIGURE_TEXT_NOT_SEARCHABLE", "warning",
                             {"ref": ref, "picture_ref": disposition[2]})
                    else:
                        flag("FIGURE_TEXT_PROVENANCE_MISMATCH", "error",
                             {"ref": ref, "pages": sorted(pages)})
                else:
                    metrics["source_text_lost"] += 1
                    if picture:
                        metrics["picture_text_lost"] += 1
                    flag("PICTURE_TEXT_LOST" if picture else "SOURCE_TEXT_LOST", "error",
                         {"ref": ref, "pages": sorted(pages), "text": source_text[:120]})
            if picture and len({item["id"] for item in represented}) > len(provenance):
                metrics["picture_text_duplicated"] += 1
                flag("PICTURE_TEXT_DUPLICATED", "error", {"ref": ref,
                                                          "pages": sorted(pages)})
            if len(provenance) > 1:
                metrics["multi_provenance_nodes"] += 1
                if len(pages) > 1:
                    metrics["multi_page_nodes"] += 1
                full = normalized(source_text)
                if len(represented) > 1 and all(normalized(item["text"]) == full
                                                for item in represented):
                    metrics["multi_page_duplicated"] += 1
                    flag("MULTI_PROVENANCE_FULL_TEXT_DUPLICATION", "error",
                         {"ref": ref, "pages": sorted(pages),
                          "element_ids": [item["id"] for item in represented]})
                elif len(represented) == len(provenance):
                    for part, item in zip(provenance, represented):
                        if (item["location"].get("page") != part.get("page_no") or
                                normalized(item["text"]) != normalized(source_fragment(node, part)) or
                                identifier_counts(item['text']) != identifier_counts(source_fragment(node, part))):
                            flag("MULTI_PROVENANCE_CHARSPAN_MISMATCH", "error",
                                 {"ref": ref, "element_id": item["id"]})
        for collection in ("pictures", "tables"):
            for node in native.get(collection, []):
                if node.get("prov") and not by_ref[node["self_ref"]]:
                    empty_table = (collection == "tables" and
                                   not node.get("data", {}).get("table_cells") and
                                   any(warning.get("code") == "empty_docling_table" and
                                       warning.get("ref") == node["self_ref"]
                                       for warning in outcome.get("warnings", [])))
                    if empty_table:
                        continue
                    metrics["unsupported_structures"] += 1
                    flag("UNREPRESENTED_DOCLING_NODE", "error",
                         {"collection": collection, "ref": node["self_ref"]})
        headings = [detail for element, detail in zip(elements, sidecar)
                    if element["kind"] == "heading"]
        if len(headings) >= 10 and all(len(item.get("heading_path") or []) == 1 for item in headings):
            flag("HEADING_DEPTH_FLATTENED", "warning", {"heading_count": len(headings),
                                                          "depths": metrics["heading_depth_distribution"]})
    if (outcome.get("status") == "parsed" and
            (metrics["source_text_lost"] or metrics["picture_short_text_lost"] or
             metrics["figure_assets_missing"]) and
            "STATUS_COMPLETENESS_MISMATCH" not in findings):
        flag("STATUS_COMPLETENESS_MISMATCH", "error",
             {"status": "parsed", "source_text_lost": metrics["source_text_lost"],
              "picture_short_text_lost": metrics["picture_short_text_lost"],
              "figure_assets_missing": metrics["figure_assets_missing"]})
    return {"source": outcome["manifest_id"], "status": outcome["status"],
            "run_id": outcome.get("_run_id"), "metrics": metrics,
            "findings": sorted(findings.values(), key=lambda item: item["code"])}


def select_outcomes(input_dir, additional_input_dirs=()):
    """Use the newest run outcome whose files still match its recorded element count."""
    reports = [json.loads(path.read_text(encoding="utf-8"))
               for directory in (input_dir, *additional_input_dirs)
               for path in directory.glob("report*.json")]
    if not reports:
        raise FileNotFoundError("no parse reports in the supplied input directories")
    latest = max(reports, key=lambda item: item.get("started_at", ""))
    manifest_path = Path(latest.get("manifest", ""))
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest_sources = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sources = {item["manifest_id"] for item in manifest["sources"]}
    selected = {}
    for report in sorted(reports, key=lambda item: item.get("started_at", ""), reverse=True):
        for outcome in report.get("outcomes", []):
            source = outcome["manifest_id"]
            if source in selected or (manifest_sources is not None and source not in manifest_sources):
                continue
            folder = Path(outcome.get("output", ""))
            if outcome.get("status") not in {"parsed", "partial"}:
                selected[source] = {**outcome, "_run_id": report.get("run_id")}
                continue
            bundle_path = folder / "artifact-bundle.json"
            if bundle_path.is_file() and (folder / "parse.json").is_file():
                bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
                if len(bundle["elements"]) == outcome.get("element_count"):
                    selected[source] = {**outcome, "_run_id": report.get("run_id")}
    for source in sorted((manifest_sources or set()) - set(selected)):
        selected[source] = {"manifest_id": source, "status": "missing", "_run_id": None}
    return list(selected.values())


def evaluate(input_dir, reference_path=None, additional_input_dirs=(), source_root=ROOT):
    reference = (json.loads(Path(reference_path).read_text(encoding="utf-8")) if reference_path else
                 {"status": "not_supplied", "passages": [], "structure": [], "reading_order": []})
    outcomes = select_outcomes(input_dir, additional_input_dirs)
    corpus, formats = {}, {}
    documents = []
    for outcome in outcomes:
        if outcome["status"] in {"parsed", "partial"}:
            folder = Path(outcome["output"])
            bundle = json.loads((folder / "artifact-bundle.json").read_text(encoding="utf-8"))
            sidecar = json.loads((folder / "parse.json").read_text(encoding="utf-8"))
            corpus[outcome["manifest_id"]] = (bundle["elements"], sidecar["elements"])
            formats[outcome['manifest_id']] = bundle['sources'][0]['format']
            document = audit_document(outcome, bundle["elements"], sidecar["elements"], folder,
                                      source_path=Path(source_root) / outcome["local_path"])
            try:
                validate_bundle(bundle)
                document["artifact_valid"] = True
            except (ValueError, KeyError, ValidationError) as exc:
                document["artifact_valid"] = False
                document["findings"].append({"code": "INVALID_ARTIFACT", "severity": "error",
                                             "count": 1, "examples": [{"message": str(exc)}]})
            documents.append(document)
    measurements = {"reference_status": reference["status"], "source_status": {
        outcome["manifest_id"]: outcome["status"] for outcome in outcomes},
        "selected_runs": {outcome["manifest_id"]: outcome.get("_run_id") for outcome in outcomes},
        "documents": documents, "passages": [], "structure": [], "reading_order": [],
        "locations": [], "runtime": []}
    for outcome in outcomes:
        if outcome["status"] not in {"parsed", "partial"}:
            continue
        elements = corpus[outcome["manifest_id"]][0]
        folder = Path(outcome["output"])
        invalid = []
        format_name = formats[outcome['manifest_id']]
        if format_name == 'pdf' and (folder / "docling.json").is_file():
            pages = json.loads((folder / "docling.json").read_text(encoding="utf-8"))["pages"]
            for element in elements:
                location = element["location"]
                page = pages.get(str(location["page"]))
                if page is None:
                    invalid.append(element["id"])
                    continue
                if "bbox" in location:
                    left, top, right, bottom = location["bbox"]
                    if not (0 <= left <= right <= page["size"]["width"] + 1 and
                            0 <= top <= bottom <= page["size"]["height"] + 1):
                        invalid.append(element["id"])
        elif format_name == 'html':
            document = html.parse(str(Path(source_root) / outcome["local_path"])).getroot()
            for element in elements:
                selected = document.cssselect(element["location"]["selector"])
                if len(selected) != 1:
                    invalid.append(element["id"])
        elif format_name in {'markdown', 'docx'}:
            # Replay these deterministic native locators against the saved file;
            # Markdown line ranges and DOCX blocks/cells are not CSS selectors.
            settings = json.loads((folder / 'parse.json').read_text())['settings']
            parser = parse_markdown if format_name == 'markdown' else parse_docx
            items, _, _ = parser(Path(source_root) / outcome['local_path'], {}, settings)
            by_locator = {item['locator_key']: item for item in items}
            for element in elements:
                original = by_locator.get(element['locator_key'])
                if original is None or any(original[k] != element[k] for k in ('kind', 'text', 'location')):
                    invalid.append(element['id'])
        else:
            # A PDF without its native sidecar cannot prove its page locations.
            invalid = [element['id'] for element in elements]
        measurements["locations"].append({"source": outcome["manifest_id"],
                                          "checked": len(elements), "valid": len(elements) - len(invalid),
                                          "invalid_element_ids": invalid})
    for category in ("passages", "structure"):
        for landmark in reference[category]:
            selected = corpus.get(landmark["source"])
            located = matches(*selected, landmark) if selected else []
            measurements[category].append({**landmark, "found": bool(located),
                                           "element_ids": [item["id"] for item in located]})
    for group in reference["reading_order"]:
        selected = corpus.get(group["source"])
        ordinals = []
        for marker in group["markers"]:
            target = {"text": marker} if isinstance(marker, str) else marker
            found = matches(*selected, {**group, **target}) if selected else []
            ordinals.append(found[0]["ordinal"] if found else None)
        expected_pairs = len(ordinals) * (len(ordinals) - 1) // 2
        correct_pairs = sum(a is not None and b is not None and a < b
                            for i, a in enumerate(ordinals) for b in ordinals[i + 1:])
        measurements["reading_order"].append({**group, "ordinals": ordinals,
                                               "correct_pairs": correct_pairs,
                                               "expected_pairs": expected_pairs})
    for outcome in outcomes:
        if outcome["status"] not in {"parsed", "partial"}:
            continue
        elements = corpus[outcome["manifest_id"]][0]
        pages = outcome.get("page_count")
        measurements["runtime"].append({"source": outcome["manifest_id"],
                                        "seconds": outcome["elapsed_seconds"],
                                        "parsed_pages": pages or None,
                                        "seconds_per_page": round(outcome["elapsed_seconds"] / pages, 3)
                                        if pages else None,
                                        "peak_process_rss_mb": outcome.get("peak_process_rss_mb"),
                                        "element_count": outcome["element_count"]})
    by_source_document = {document["source"]: document for document in documents}

    def add_reference_error(source, code, example):
        document = by_source_document.get(source)
        if document is None:
            return
        existing = next((item for item in document["findings"] if item["code"] == code), None)
        if existing is None:
            existing = {"code": code, "severity": "error", "count": 0, "examples": []}
            document["findings"].append(existing)
        existing["count"] += 1
        if len(existing["examples"]) < 5:
            existing["examples"].append(example)

    for category in ("passages", "structure"):
        for item in measurements[category]:
            if not item["found"]:
                add_reference_error(item["source"], {"passages": "MISSING_PASSAGE",
                                                    "structure": "MISSING_STRUCTURE"}[category],
                                    {"text": item["text"], "page": item.get("page")})
    for item in measurements["reading_order"]:
        if item["correct_pairs"] != item["expected_pairs"]:
            add_reference_error(item["source"], "READING_ORDER_MISMATCH",
                                {"ordinals": item["ordinals"]})
    for item in measurements["locations"]:
        for element_id in item["invalid_element_ids"]:
            add_reference_error(item["source"], "INVALID_LOCATION", {"element_id": element_id})
    for document in documents:
        document["gate_status"] = ("pass" if document["artifact_valid"] and
                                   not any(item["severity"] == "error" for item in document["findings"])
                                   else "fail")
    measurements["totals"] = {
        "passages_found": sum(item["found"] for item in measurements["passages"]),
        "passages_checked": len(measurements["passages"]),
        "structure_found": sum(item["found"] for item in measurements["structure"]),
        "structure_checked": len(measurements["structure"]),
        "order_pairs_correct": sum(item["correct_pairs"] for item in measurements["reading_order"]),
        "order_pairs_checked": sum(item["expected_pairs"] for item in measurements["reading_order"]),
        "locations_valid": sum(item["valid"] for item in measurements["locations"]),
        "locations_checked": sum(item["checked"] for item in measurements["locations"]),
    }
    for numerator, denominator, rate in (
        ("passages_found", "passages_checked", "passage_recall"),
        ("structure_found", "structure_checked", "structure_recall"),
        ("order_pairs_correct", "order_pairs_checked", "reading_order_accuracy"),
        ("locations_valid", "locations_checked", "location_validity"),
    ):
        checked = measurements["totals"][denominator]
        measurements["totals"][rate] = round(measurements["totals"][numerator] / checked, 4) if checked else None
    expected_sources = {item["source"] for category in ("passages", "structure", "reading_order")
                        for item in reference[category]}
    missing_sources = sorted(expected_sources - set(corpus))
    corpus_metrics = {key: sum(doc["metrics"][key] for doc in documents) for key in METRIC_KEYS}
    depth_counts = Counter()
    for doc in documents:
        depth_counts.update(doc["metrics"]["heading_depth_distribution"])
    corpus_metrics["heading_depth_distribution"] = dict(sorted(depth_counts.items()))
    code_counts = Counter()
    warning_counts = Counter()
    error_counts = Counter()
    for doc in documents:
        for finding in doc["findings"]:
            code_counts[finding["code"]] += finding["count"]
            (error_counts if finding["severity"] == "error" else warning_counts)[finding["code"]] += finding["count"]
    unparsed_sources = sorted(outcome["manifest_id"] for outcome in outcomes
                              if outcome["status"] not in {"parsed", "partial"})
    if unparsed_sources:
        code_counts["UNPARSED_SOURCE"] += len(unparsed_sources)
        error_counts["UNPARSED_SOURCE"] += len(unparsed_sources)
    mixed_runs = len(set(measurements["selected_runs"].values())) > 1
    if mixed_runs:
        code_counts["MIXED_PARSE_RUNS"] += 1
        warning_counts["MIXED_PARSE_RUNS"] += 1
    if missing_sources:
        code_counts["MISSING_REFERENCE_SOURCE"] += len(missing_sources)
        error_counts["MISSING_REFERENCE_SOURCE"] += len(missing_sources)
    measurements["corpus"] = {"document_count": len(documents), "metrics": corpus_metrics,
                              "finding_codes": dict(sorted(code_counts.items())),
                              "warning_codes": dict(sorted(warning_counts.items())),
                              "error_codes": dict(sorted(error_counts.items())),
                              "missing_reference_sources": missing_sources,
                              "unparsed_sources": unparsed_sources,
                              "mixed_parse_runs": mixed_runs}
    landmarks_pass = (measurements["totals"]["passages_found"] == measurements["totals"]["passages_checked"]
                      and measurements["totals"]["structure_found"] == measurements["totals"]["structure_checked"]
                      and measurements["totals"]["order_pairs_correct"] == measurements["totals"]["order_pairs_checked"]
                      and measurements["totals"]["locations_valid"] == measurements["totals"]["locations_checked"])
    passed = not error_counts and landmarks_pass and all(doc["artifact_valid"] for doc in documents)
    measurements["gate"] = {"status": "pass" if passed else "fail",
                            "criteria": "all reference and location checks pass; all bundles validate; no audit errors",
                            "landmarks_pass": landmarks_pass,
                            "artifact_validation_pass": all(doc["artifact_valid"] for doc in documents),
                            "error_count": sum(error_counts.values()),
                            "warning_count": sum(warning_counts.values())}
    return measurements


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit parsing fidelity and normalization quality")
    parser.add_argument("--input-dir", type=Path, default=Path("artifacts/parsed"))
    parser.add_argument("--additional-input-dir", type=Path, action="append", default=[],
                        help="also select newer parse outcomes from this directory (repeatable)")
    parser.add_argument("--reference", type=Path,
                        help="optional source-checked landmark JSON for this corpus")
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="root for saved source paths (repository root by default)")
    parser.add_argument("--output", type=Path, help="write measurements here (stdout by default)")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/manifest.json")
    parser.add_argument("--review-policy", type=Path, default=ROOT / "config/review-policy.json")
    parser.add_argument("--sample-per-document", type=int, default=12)
    parser.add_argument("--required-pages", type=Path, help="JSON list of source_id/page objects")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--initialize-review", type=Path)
    group.add_argument("--review-file", type=Path)
    args = parser.parse_args(argv)
    if args.initialize_review and args.initialize_review.exists():
        parser.error("review file already exists; reuse --review-file")
    if (args.initialize_review or args.review_file) and args.output and (args.initialize_review or args.review_file).resolve() == args.output.resolve():
        parser.error("review and output paths must differ")
    result = evaluate(args.input_dir, args.reference, args.additional_input_dir, args.source_root)
    if args.initialize_review or args.review_file:
        from knowledge_foundry.parsing_review import prepare, seed
        try:
            result = prepare(result, select_outcomes(args.input_dir, args.additional_input_dir),
                             args.source_root, args.manifest, args.review_policy, args.sample_per_document,
                             json.loads(args.required_pages.read_text()) if args.required_pages else [],
                             args.review_file, args.reference)
            if args.initialize_review:
                args.initialize_review.parent.mkdir(parents=True, exist_ok=True)
                args.initialize_review.write_text(json.dumps(seed(result), ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.exit(2, f"parsing review failed: {exc}\n")
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if isinstance(result["gate"], dict):
        return 0 if result["gate"]["status"] == "pass" else 1
    return 1 if result["gate"] == "fail" or args.review_file and result["gate"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
