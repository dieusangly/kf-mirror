"""Parse verified saved sources into v1 artifacts and a loss-aware sidecar."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import resource
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from knowledge_foundry.ingestion import load_ingested
from knowledge_foundry.parsers import (
    SOURCE_REVIEW_WARNINGS, parse_docx, parse_html, parse_markdown, parse_pdf,
)


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
    configured = config.get("structuring", {}).get("options", {})
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
        sidecar.append({"element_id": element_id, "parent_key": item.get("parent_key"),
                        "evidence_role": "structural" if item["kind"] in {"table_cell", "figure"}
                        else "authoritative", **item.get("details", {})})
    for entry in sidecar:
        parent_key = entry.pop("parent_key")
        entry["parent_element_id"] = id_by_key.get(parent_key) if parent_key else None
        if parent_key and entry["parent_element_id"] is None:
            raise ValueError(f"unresolved element parent: {parent_key}")
        def table_reference(ref):
            ref = dict(ref)
            for key, target in [('element_key', 'element_id'), ('cell_key', 'cell_element_id')]:
                if key in ref:
                    value = ref.pop(key)
                    if value not in id_by_key:
                        raise ValueError(f'unresolved table context: {value}')
                    ref[target] = id_by_key[value]
            return ref
        for field in ['table_headers', 'table_section_headers']:
            if field in entry:
                entry[field] = [table_reference(ref) for ref in entry[field]]
        if 'table_continues_to_keys' in entry:
            entry['table_continues_to'] = [id_by_key[key] for key in entry.pop('table_continues_to_keys')]
        if 'table_continuation' in entry:
            value = dict(entry['table_continuation'])
            value['previous_table_id'] = id_by_key[value.pop('previous_table_key')]
            for key in ['header_context', 'row_context']:
                value[key] = [table_reference(ref) for ref in value[key]]
            entry['table_continuation'] = value
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


def parse_collection(config_path, output_dir, selected_ids=None, on_progress=None, *, run_id=None,
                     native_input_dir=None, ingested_dir=None):
    """Document Structuring substep: consume reusable ingested artifacts only."""
    from knowledge_foundry.structure_providers import resolve_provider, ProviderResult
    config_path, output_dir = Path(config_path).resolve(), Path(output_dir).resolve()
    if native_input_dir and Path(native_input_dir).resolve() == output_dir:
        raise ValueError('native input and output directories must differ; preserve the prior evidence')
    config = json.loads(config_path.read_text())
    root = (config_path.parent / config['root']).resolve()
    artifact = load_ingested(ingested_dir or root / config.get('ingested_dir', 'artifacts/ingested'))
    name, options, provider = resolve_provider(config, native_input_dir)
    providers = {}
    selected = list(selected_ids or config['manifest_ids'])
    configured = config['manifest_ids']
    if set(config.get('structuring', {}).get('source_overrides', {})) - set(configured):
        raise ValueError('structuring source overrides must name configured manifest IDs')
    outcomes = {row['manifest_id']: row for row in artifact['outcomes']}
    if not set(selected) <= set(configured) or not set(selected) <= outcomes.keys():
        raise ValueError('manifest IDs are not configured or ingested')
    if run_id is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', run_id):
        raise ValueError('run ID must be a safe directory name')
    started = datetime.now(timezone.utc)
    settings = getattr(provider, 'settings', options)
    report = {'report_version': 2, 'schema_version': SCHEMA_VERSION,
              'run_id': run_id or f'{started:%Y%m%d-%H-%M-%S}-{uuid.uuid4().hex[:8]}',
              'started_at': started.isoformat(), 'manifest': artifact['manifest'],
              'ingested_artifact_id': artifact['artifact_id'], 'provider': name,
              'provider_version': provider.version,
              'config_sha256': _digest(json.dumps(settings, sort_keys=True)), 'settings': settings, 'outcomes': []}
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, mid in enumerate(selected, 1):
        outcome = outcomes[mid]
        result = {'manifest_id': mid, 'local_path': outcome['local_path']}
        start = time.perf_counter()
        if on_progress:
            on_progress({'index': index, 'total': len(selected), **result, 'status': 'parsing'})
        try:
            if outcome['status'] != 'ingested':
                raise ValueError('source was not successfully ingested: ' + str(outcome.get('error')))
            override = config.get('structuring', {}).get('source_overrides', {}).get(mid, {})
            selected_name, selected_provider = name, provider
            effective = {**options, **override.get('options', {})}
            if 'provider' in override:
                key = json.dumps(override, sort_keys=True)
                if key not in providers:
                    providers[key] = resolve_provider({**config, 'structuring': override})
                selected_name, effective, selected_provider = providers[key]
            provided = selected_provider.structure(outcome, effective)
            if not isinstance(provided, ProviderResult):
                raise ValueError('provider must return ProviderResult')
            if not provided.elements and provided.chunks:
                for i, chunk in enumerate(provided.chunks):
                    key = chunk.get('locator_key', f'provider:chunk-{i}')
                    provided.elements.append({'kind': chunk.get('kind', 'paragraph'), 'text': chunk['text'],
                        'locator_key': key, 'location': chunk['location'], 'details': chunk.get('details', {})})
                    chunk['evidence'] = [{'locator_key': key}]
            if not provided.elements:
                raise ValueError('provider must return located elements or located structural chunks')
            source, document = outcome['source'], outcome['document']
            bundle, details = _normalize(source, document, provided.elements)
            parse_data = {'parse_version': 1, 'document_id': document['id'],
                          'parser': selected_name, 'adapter_version': selected_provider.version, 'parser_versions': {},
                          'settings': effective, 'metadata_origins': {}, **provided.metadata,
                          'provider': selected_name, 'ingested_artifact_file': outcome['artifact_file'], 'ingested_artifact_id': artifact['artifact_id'],
                          'elements': details, 'warnings': provided.warnings}
            if provided.chunks is not None:
                parse_data['provider_chunks'] = provided.chunks
            target = output_dir / 'documents' / document['id'].removeprefix('doc:')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as directory:
                staged = Path(directory)
                if source['format'] == 'pdf':
                    _save_pdf_figure_assets(Path(outcome['artifact_file']), bundle['elements'], details,
                                            staged, provided.warnings)
                _write_json(staged / 'artifact-bundle.json', bundle)
                _write_json(staged / 'parse.json', parse_data)
                if provided.native is not None:
                    _write_json(staged / ('docling.json' if selected_name == 'internal' else 'provider.json'), provided.native)
                if target.exists():
                    shutil.rmtree(target)
                os.replace(staged, target)
            harmless = {'excluded_picture_ocr_fragments', 'excluded_picture_corrupt_ocr',
                        'excluded_picture_furniture', 'excluded_figure_text_uncertain', 'empty_docling_table'} | SOURCE_REVIEW_WARNINGS
            result.update(status='partial' if any(w.get('code') not in harmless for w in provided.warnings) else 'parsed',
                          document_id=document['id'], parser=parse_data['parser'], element_count=len(provided.elements),
                          page_count=provided.metadata.get('page_count'), warnings=provided.warnings, output=str(target))
        except Exception as exc:
            result.update(status='failed', error=outcome['error'] if outcome['status'] != 'ingested' else {
                'code': 'ocr_setup_error' if 'Tesseract' in str(exc) else 'parse_error', 'message': str(exc)})
        result['elapsed_seconds'] = round(time.perf_counter() - start, 3)
        result['peak_process_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        report['outcomes'].append(result)
        if on_progress:
            on_progress({'index': index, 'total': len(selected), **result})
    prior_path = output_dir / 'report.json'
    if selected_ids and prior_path.exists():
        prior = json.loads(prior_path.read_text())
        if prior.get('ingested_artifact_id') != artifact['artifact_id']:
            raise ValueError('subset repair cannot mix ingestion snapshots; use a fresh collection')
        combined = {row['manifest_id']: row for row in prior['outcomes']}
        combined.update({row['manifest_id']: row for row in report['outcomes']})
        report['outcomes'] = [combined[mid] for mid in configured if mid in combined]
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    _write_json(output_dir / 'report.json', report)
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
    parser.add_argument("--ingested-dir", type=Path, help="reusable ingestion store or immutable snapshot JSON")
    parser.add_argument("--source", action="append", default=[], metavar="ID",
                        help="manifest ID to parse; repeatable (default: all)")
    parser.add_argument("--run-id", help="retain the controlling workflow's run identity")
    parser.add_argument('--native-input-dir', type=Path,
                        help='rebuild PDF adapters from prior source-bound native JSON; originals are reverified')
    args = parser.parse_args(argv)
    try:
        report = parse_collection(args.config, args.output_dir, args.source,
                                  on_progress=_print_progress, run_id=args.run_id,
                                  native_input_dir=args.native_input_dir, ingested_dir=args.ingested_dir)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"parsing setup error: {exc}\n")
    report_path = Path(args.output_dir).resolve() / "report.json"
    print(f"Report saved: {report_path}", file=sys.stderr, flush=True)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 1 if any(item["status"] != "parsed" for item in report["outcomes"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
