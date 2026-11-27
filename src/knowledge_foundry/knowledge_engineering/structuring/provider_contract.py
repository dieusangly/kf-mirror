"""Provider return types and parser settings shared by dispatch and adapters."""
from dataclasses import dataclass, field
from typing import Protocol
import importlib.metadata
from knowledge_foundry.knowledge_engineering.structuring.parsers import parse_docx, parse_html, parse_markdown, parse_pdf

@dataclass
class ProviderResult:
    elements: list
    native: object = None
    warnings: list = field(default_factory=list)
    chunks: list | None = None  # [{text, evidence: [{locator_key, span?}]}]
    metadata: dict = field(default_factory=dict)


class StructureProvider(Protocol):
    version: str

    def structure(self, artifact: dict, options: dict) -> ProviderResult: ...


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
