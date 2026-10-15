"""Parse verified saved sources into v1 artifacts and a loss-aware sidecar."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from knowledge_foundry.ingestion import ingest
from knowledge_foundry.parsers import build_pdf_converter, parse_docx, parse_html, parse_markdown, parse_pdf


SCHEMA_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))
PARSERS = {
    "pdf": (parse_pdf, "docling"),
    "html": (parse_html, "lxml"),
    "markdown": (parse_markdown, "markdown-it-py"),
    "docx": (parse_docx, "python-docx"),
}
DEFAULTS = {
    "pdf": {"backend": "docling_parse", "table_structure": True,
            "table_mode": "accurate", "artifacts_path": None},
    "ocr": {"mode": "auto", "languages": ["fra", "eng"], "scale": 3.0,
            "psm": 3, "tesseract_cmd": "tesseract", "tessdata_path": None},
    "html": {"content_selector": "main", "source_overrides": {}},
    "markdown": {"tables": True},
    "docx": {"include_headers_footers": False},
}


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _configuration(config):
    configured = config.get("parsing", {})
    if not isinstance(configured, dict) or set(configured) - set(DEFAULTS):
        raise ValueError("parsing must be an object with pdf, ocr, html, markdown, and docx sections")
    settings = {}
    for section, defaults in DEFAULTS.items():
        overrides = configured.get(section, {})
        if not isinstance(overrides, dict) or set(overrides) - set(defaults):
            raise ValueError(f"invalid parsing.{section} fields")
        settings[section] = {**defaults, **overrides}
    if settings["pdf"]["backend"] != "docling_parse":
        raise ValueError("only the docling_parse PDF backend is supported")
    if settings["pdf"]["table_mode"] not in {"fast", "accurate"}:
        raise ValueError("pdf.table_mode must be fast or accurate")
    if not isinstance(settings["pdf"]["table_structure"], bool):
        raise ValueError("pdf.table_structure must be boolean")
    if settings["pdf"]["artifacts_path"] is not None and not isinstance(settings["pdf"]["artifacts_path"], str):
        raise ValueError("pdf.artifacts_path must be a path or null")
    if settings["ocr"]["mode"] not in {"off", "auto", "force"}:
        raise ValueError("ocr.mode must be off, auto, or force")
    if not isinstance(settings["ocr"]["languages"], list) or not all(
        isinstance(language, str) and language for language in settings["ocr"]["languages"]
    ):
        raise ValueError("ocr.languages must be a list of language codes")
    if not isinstance(settings["ocr"]["scale"], (int, float)) or settings["ocr"]["scale"] <= 0:
        raise ValueError("ocr.scale must be positive")
    if not isinstance(settings["ocr"]["psm"], int) or not 0 <= settings["ocr"]["psm"] <= 13:
        raise ValueError("ocr.scale and ocr.psm are invalid")
    if not isinstance(settings["docx"]["include_headers_footers"], bool):
        raise ValueError("docx.include_headers_footers must be boolean")
    if settings["docx"]["include_headers_footers"]:
        raise ValueError("DOCX headers and footers are not supported by the v1 block locations")
    if not isinstance(settings["markdown"]["tables"], bool):
        raise ValueError("markdown.tables must be boolean")
    if not isinstance(settings["html"]["content_selector"], str) or not settings["html"]["content_selector"]:
        raise ValueError("html.content_selector must be a nonempty CSS selector")
    source_overrides = settings["html"]["source_overrides"]
    if not isinstance(source_overrides, dict):
        raise ValueError("html.source_overrides must be an object keyed by manifest ID")
    for manifest_id, override in source_overrides.items():
        if (not isinstance(manifest_id, str) or not manifest_id or not isinstance(override, dict) or
                set(override) - {"content_selector", "article_id"} or
                any(not isinstance(value, str) or not value for value in override.values())):
            raise ValueError("html.source_overrides values must contain nonempty content_selector or article_id strings")
    if not isinstance(settings["ocr"]["tesseract_cmd"], str) or not settings["ocr"]["tesseract_cmd"]:
        raise ValueError("ocr.tesseract_cmd must be a nonempty path or command")
    if settings["ocr"]["tessdata_path"] is not None and not isinstance(settings["ocr"]["tessdata_path"], str):
        raise ValueError("ocr.tessdata_path must be a path or null")
    return settings


def _tool_versions(format_name):
    names = {"pdf": ["docling", "docling-core", "docling-parse"],
             "html": ["lxml", "cssselect"],
             "markdown": ["markdown-it-py"],
             "docx": ["python-docx"]}[format_name]
    versions = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _source_metadata(source, document, manifest_entry, parser_settings, selector_origin):
    return {
        "source.id": {"value": source["id"], "origin": "manifest"},
        "source.title": {"value": source["title"], "origin": "manifest"},
        "source.class": {"value": source["class"], "origin": "manifest"},
        "source.version": {"value": source["version"], "origin": "manifest"},
        "source.uri": {"value": source["uri"], "origin": "manifest"},
        "source.format": {"value": source["format"], "origin": "content_detection"},
        "document.retrieved_on": {"value": document["retrieved_on"], "origin": "manifest"},
        "document.content_sha256": {"value": document["content_sha256"], "origin": "saved_file"},
        "document.media_type": {"value": document["media_type"], "origin": "content_detection"},
        "manifest_id": {"value": manifest_entry["manifest_id"], "origin": "manifest"},
        "capture_method": {"value": manifest_entry.get("capture_method"), "origin": "manifest"},
        "html.content_selector": {"value": parser_settings["content_selector"],
                                  "origin": selector_origin} if source["format"] == "html" else None,
        "html.article_id": {"value": parser_settings.get("article_id", source["version"]),
                            "origin": "parsing_config" if "article_id" in parser_settings else "manifest_source_version"}
        if source["format"] == "html" else None,
    }


def _embedded_metadata(path, format_name):
    """Keep embedded properties as observations, never authoritative overrides."""
    try:
        if format_name == "pdf":
            import pypdfium2 as pdfium

            values = pdfium.PdfDocument(str(path)).get_metadata_dict()
            origin = "pdf_properties"
        elif format_name == "html":
            from lxml import html

            document = html.parse(str(path))
            values = {"title": " ".join(document.xpath("//head/title/text()")[:1])}
            origin = "html_head"
        elif format_name == "docx":
            from docx import Document

            properties = Document(str(path)).core_properties
            values = {"title": properties.title, "author": properties.author}
            origin = "docx_core_properties"
        else:
            return {}
        return {f"embedded.{key}": {"value": value, "origin": origin}
                for key, value in values.items() if isinstance(value, str) and value.strip()}
    except (OSError, ValueError, AttributeError):
        return {}


def _normalize(source, document, items):
    records = []
    sidecar = []
    seen = set()
    id_by_key = {}
    for ordinal, item in enumerate(items):
        key = item["locator_key"]
        if key in seen:
            raise ValueError(f"duplicate element locator_key: {key}")
        seen.add(key)
        element_id = "el:" + _digest(document["id"] + "\n" + key)
        id_by_key[key] = element_id
        record = {
            "schema_version": SCHEMA_VERSION,
            "id": element_id,
            "document_id": document["id"],
            "locator_key": key,
            "revision": _digest(item["kind"] + "\n" + item["text"]),
            "kind": item["kind"],
            "ordinal": ordinal,
            "text": item["text"],
            "location": item["location"],
        }
        records.append(record)
        sidecar.append({"element_id": element_id, "parent_key": item["parent_key"],
                        "evidence_role": "structural" if item["kind"] in {"table_cell", "figure"}
                        else "authoritative", **item["details"]})
    for entry in sidecar:
        parent_key = entry.pop("parent_key")
        entry["parent_element_id"] = id_by_key.get(parent_key) if parent_key else None
        if parent_key and entry["parent_element_id"] is None:
            raise ValueError(f"unresolved element parent: {parent_key}")
    bundle = {"schema_version": SCHEMA_VERSION, "sources": [source],
              "documents": [document], "elements": records,
              "chunks": [], "claims": [], "topics": [], "builds": []}
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(bundle)
    return bundle, sidecar


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_pdf_figure_assets(pdf_path, elements, sidecar, staged, warnings):
    """Save lossless source-page crops and link them by canonical element ID."""
    import pypdfium2 as pdfium

    details = {entry["element_id"]: entry for entry in sidecar}
    figures = {}
    for element in elements:
        if element["kind"] == "figure":
            figures.setdefault(element["location"]["page"], []).append(element)
    if not figures:
        return
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_no, page_figures in sorted(figures.items()):
            page = pdf[page_no - 1]
            scale = 2
            raster = page.render(scale=scale).to_pil()
            for element in page_figures:
                box = element["location"].get("bbox")
                if (not box or len(box) != 4 or
                        not 0 <= box[0] < box[2] <= page.get_width() + .5 or
                        not 0 <= box[1] < box[3] <= page.get_height() + .5):
                    warnings.append({"code": "figure_asset_unavailable", "element_id": element["id"],
                                     "reason": "missing_or_invalid_bbox"})
                    continue
                pixels = (max(0, math.floor(box[0] * scale)),
                          max(0, math.floor(box[1] * scale)),
                          min(raster.width, math.ceil(box[2] * scale)),
                          min(raster.height, math.ceil(box[3] * scale)))
                if pixels[0] >= pixels[2] or pixels[1] >= pixels[3]:
                    warnings.append({"code": "figure_asset_unavailable", "element_id": element["id"],
                                     "reason": "empty_pixel_crop"})
                    continue
                relative = f"figures/{element['id'].removeprefix('el:')}.png"
                target = staged / relative
                target.parent.mkdir(exist_ok=True)
                raster.crop(pixels).save(target, format="PNG")
                details[element["id"]]["asset"] = {
                    "path": relative, "media_type": "image/png",
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "representation_role": "authoritative_visual", "render_scale": scale,
                }
    finally:
        pdf.close()


def parse_collection(config_path, output_dir, selected_ids=None, on_progress=None):
    """Parse verified manifest entries; report each selected source as it finishes."""
    config_path = Path(config_path).resolve()
    output_dir = Path(output_dir).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings = _configuration(config)
    ingestion_report = ingest(config_path)
    manifest = json.loads(Path(ingestion_report["manifest"]).read_text(encoding="utf-8"))
    entries = {entry["manifest_id"]: entry for entry in manifest["sources"] if isinstance(entry, dict)}
    selected_ids = list(selected_ids or [])
    configured_ids = {item["manifest_id"] for item in ingestion_report["outcomes"]}
    unknown = set(selected_ids) - configured_ids
    if unknown:
        raise ValueError(f"manifest IDs are not configured: {', '.join(sorted(unknown))}")
    outcomes = [item for item in ingestion_report["outcomes"]
                if not selected_ids or item["manifest_id"] in selected_ids]
    ocr_error = None
    if any(item.get("detected_format") == "pdf" and item["status"] == "ingested"
           for item in outcomes) and settings["ocr"]["mode"] != "off":
        cmd = settings["ocr"]["tesseract_cmd"]
        if not (shutil.which(cmd) or Path(cmd).is_file()):
            ocr_error = f"Tesseract CLI unavailable: {cmd}"
        else:
            command = [cmd, "--list-langs"]
            if settings["ocr"]["tessdata_path"]:
                command += ["--tessdata-dir", settings["ocr"]["tessdata_path"]]
            try:
                checked = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
                if checked.returncode:
                    ocr_error = f"Tesseract language check failed: {checked.stderr.strip()}"
                else:
                    available = set(checked.stdout.splitlines()[1:])
                    missing = [language for language in settings["ocr"]["languages"]
                               if not language.startswith("iso:") and language not in available]
                    if missing:
                        ocr_error = f"Tesseract language data unavailable: {', '.join(missing)}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                ocr_error = f"Tesseract language check failed: {exc}"
    root = (config_path.parent / config["root"]).resolve()
    settings_digest = _digest(json.dumps(settings, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")))
    started_at = datetime.now(timezone.utc)
    run_id = f"{started_at:%Y%m%d-%H-%M-%S}-{uuid.uuid4().hex[:8]}"
    report = {"report_version": 1, "schema_version": SCHEMA_VERSION,
              "run_id": run_id, "started_at": started_at.isoformat(),
              "manifest": str(Path(ingestion_report["manifest"])),
              "config_sha256": settings_digest, "settings": settings, "outcomes": []}
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(outcomes)
    pdf_converter = None
    for index, outcome in enumerate(outcomes, start=1):
        result = {"manifest_id": outcome["manifest_id"],
                  "local_path": outcome["local_path"]}
        if outcome["status"] != "ingested":
            result.update(status="failed", error=outcome["error"])
            report["outcomes"].append(result)
            if on_progress:
                on_progress({"index": index, "total": total, **result})
            continue
        start = time.perf_counter()
        if on_progress:
            on_progress({"index": index, "total": total, **result, "status": "parsing"})
        error_code = "parse_error"
        try:
            source = outcome["source"]
            document = outcome["document"]
            format_name = outcome["detected_format"]
            if format_name == "pdf" and ocr_error:
                error_code = "ocr_setup_error"
                raise ValueError(ocr_error)
            manifest_entry = entries[outcome["manifest_id"]]
            local_path = (root / outcome["local_path"]).resolve()
            parser, parser_name = PARSERS[format_name]
            parser_settings = dict(settings[format_name])
            selector_origin = None
            reuse_pdf_converter = format_name == "pdf" and parser is parse_pdf
            if format_name == "pdf":
                parser_settings["ocr"] = settings["ocr"]
                if reuse_pdf_converter and pdf_converter is None:
                    pdf_converter = build_pdf_converter(parser_settings)
            if format_name == "html":
                overrides = parser_settings.pop("source_overrides")
                selected_override = overrides.get(outcome["manifest_id"], {})
                parser_settings.update(selected_override)
                selector_origin = ("parsing_config" if "content_selector" in selected_override or
                                   "content_selector" in config.get("parsing", {}).get("html", {}) else "parser_default")
            if reuse_pdf_converter:
                items, native, warnings = parse_pdf(local_path, source, parser_settings,
                                                     converter=pdf_converter)
            else:
                items, native, warnings = parser(local_path, source, parser_settings)
            if not items:
                raise ValueError("parser produced no located elements")
            bundle, sidecar = _normalize(source, document, items)
            metadata = _source_metadata(source, document, manifest_entry,
                                        parser_settings, selector_origin)
            metadata.update(_embedded_metadata(local_path, format_name))
            metadata = {key: value for key, value in metadata.items() if value is not None}
            parse_data = {"parse_version": 1, "document_id": document["id"], "parser": parser_name,
                          "parser_versions": _tool_versions(format_name),
                          "settings": parser_settings, "metadata_origins": metadata,
                          "elements": sidecar, "warnings": warnings}
            target = output_dir / "documents" / document["id"].removeprefix("doc:")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temp_name:
                staged = Path(temp_name)
                if format_name == "pdf":
                    _save_pdf_figure_assets(local_path, bundle["elements"], sidecar, staged, warnings)
                _write_json(staged / "artifact-bundle.json", bundle)
                _write_json(staged / "parse.json", parse_data)
                if native is not None:
                    _write_json(staged / "docling.json", native)
                if target.exists():
                    shutil.rmtree(target)
                os.replace(staged, target)
            harmless_warnings = {"excluded_picture_ocr_fragments", "excluded_picture_corrupt_ocr",
                                 "excluded_picture_furniture", "excluded_figure_text_uncertain",
                                 "empty_docling_table"}
            result.update(status="partial" if any(
                warning.get("code") not in harmless_warnings for warning in warnings
            ) else "parsed", document_id=document["id"],
                          parser=parser_name, element_count=len(items),
                          page_count=len(native.get("pages", {})) if native is not None else None,
                          warnings=warnings,
                          output=str(target), elapsed_seconds=round(time.perf_counter() - start, 3))
        except Exception as exc:
            result.update(status="failed", error={"code": error_code, "message": str(exc)},
                          elapsed_seconds=round(time.perf_counter() - start, 3))
        result["peak_process_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        report["outcomes"].append(result)
        if on_progress:
            on_progress({"index": index, "total": total, **result})
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(output_dir / "report.json", report)
    return report


def _print_progress(event):
    label = f"[{event['index']}/{event['total']}] {event['manifest_id']} {event['local_path']}"
    if event["status"] == "parsing":
        message = f"{label}: parsing..."
    else:
        status = "succeeded" if event["status"] == "parsed" else event["status"]
        elapsed = f" in {event['elapsed_seconds']:.3f}s" if "elapsed_seconds" in event else ""
        detail = f" ({event['element_count']} elements)" if "element_count" in event else ""
        error = f": {event['error']['message']}" if "error" in event else ""
        message = f"{label}: {status}{elapsed}{detail}{error}"
    print(message, file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Parse verified saved documents into source-linked artifacts")
    parser.add_argument("--config", required=True, type=Path, help="pipeline JSON configuration file")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/parsed"),
                        help="output directory (default: artifacts/parsed)")
    parser.add_argument("--source", action="append", default=[], metavar="ID",
                        help="manifest ID to parse; repeatable (default: all)")
    args = parser.parse_args(argv)
    try:
        report = parse_collection(args.config, args.output_dir, args.source, on_progress=_print_progress)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"parsing setup error: {exc}\n")
    report_path = Path(args.output_dir).resolve() / "report.json"
    print(f"Report saved: {report_path}", file=sys.stderr, flush=True)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 1 if any(item["status"] != "parsed" for item in report["outcomes"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
