"""Inventory saved local sources without parsing or exporting their contents."""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


SCHEMA_VERSION = "1.0.0"
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "html": "text/html",
    "markdown": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class IngestionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_fingerprint(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        head = stream.read(4096)
        digest.update(head)
        size += len(head)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return head, size, digest.hexdigest()


def _detect_format(path, head):
    if head.startswith(b"%PDF-"):
        return "pdf"
    if re.search(rb"<(?:!doctype\s+html|html(?:\s|>))", head.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()):
        return "html"
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        if {"[Content_Types].xml", "word/document.xml"} <= names:
            return "docx"
    if path.suffix.lower() in {".md", ".markdown"} and b"\x00" not in head:
        try:
            path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            return None
        return "markdown"
    return None


def _source_path(root, local_path):
    if not isinstance(local_path, str) or not local_path:
        raise IngestionError("invalid_path", "local_path must be a nonempty relative path")
    relative = Path(local_path)
    if relative.is_absolute():
        raise IngestionError("invalid_path", "local_path must be relative to the configured root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise IngestionError("invalid_path", "local_path resolves outside the configured root")
    if not path.is_file():
        raise IngestionError("missing_file", f"file not found: {local_path}")
    return path


def _ingest_entry(entry, mapping, root):
    outcome = {"manifest_id": entry.get("id"), "local_path": entry.get("local_path")}
    try:
        path = _source_path(root, entry.get("local_path"))
        head, size, content_sha256 = _file_fingerprint(path)
        outcome.update(size_bytes=size, content_sha256=content_sha256)
        detected = _detect_format(path, head)
        outcome["detected_format"] = detected
        if detected is None:
            raise IngestionError("unsupported_format", "file content is not a supported PDF, HTML, Markdown, or DOCX")
        media_type = MEDIA_TYPES[detected]
        outcome["detected_media_type"] = media_type
        if entry.get("media_type") != media_type:
            raise IngestionError("media_type_mismatch", f"manifest media_type does not match detected {media_type}")
        if entry.get("size_bytes") != size:
            raise IngestionError("size_mismatch", "file size differs from manifest size_bytes")
        if entry.get("sha256") != content_sha256:
            raise IngestionError("hash_mismatch", "file content differs from manifest sha256")
        metadata = mapping.get(entry.get("id"))
        if not isinstance(metadata, dict) or any(not isinstance(metadata.get(key), str) or not metadata[key] for key in ("source_id", "title", "class")):
            raise IngestionError("missing_source_metadata", "configure source_id, title, and class for this manifest ID")
        source_id = metadata["source_id"]
        if not source_id.startswith("src:"):
            raise IngestionError("invalid_source_id", "source_id must start with src:")
        source_version = entry.get("source_version")
        uri = entry.get("publisher_url")
        retrieved_on = entry.get("retrieved_on")
        if not all(isinstance(value, str) and value for value in (source_version, uri, retrieved_on)):
            raise IngestionError("invalid_manifest_entry", "source version, URL, and retrieval date are required")
        parsed_uri = urlparse(uri)
        if parsed_uri.scheme not in {"http", "https"} or not parsed_uri.netloc:
            raise IngestionError("invalid_manifest_entry", "publisher_url must be an HTTP(S) URL")
        try:
            if date.fromisoformat(retrieved_on).isoformat() != retrieved_on:
                raise ValueError("noncanonical date")
        except ValueError as exc:
            raise IngestionError("invalid_manifest_entry", "retrieved_on must be an ISO date") from exc
        source = {
            "schema_version": SCHEMA_VERSION,
            "id": source_id,
            "version": source_version,
            "uri": uri,
            "title": metadata["title"],
            "class": metadata["class"],
            "format": detected,
        }
        document = {
            "schema_version": SCHEMA_VERSION,
            "id": "doc:" + _digest(source_id + "\n" + content_sha256),
            "source_id": source_id,
            "source_version": source_version,
            "content_sha256": content_sha256,
            "media_type": media_type,
            "retrieved_on": retrieved_on,
        }
        outcome.update(status="ingested", source=source, document=document)
    except IngestionError as exc:
        outcome.update(status="failed", error={"code": exc.code, "message": str(exc)})
    except (OSError, TypeError, zipfile.BadZipFile) as exc:
        outcome.update(status="failed", error={"code": "read_error", "message": str(exc)})
    return outcome


def ingest(config_path):
    """Return one result per manifest entry, including failed entries."""
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = (config_path.parent / config["root"]).resolve()
    manifest_path = (root / config["manifest"]).resolve()
    if not manifest_path.is_relative_to(root):
        raise ValueError("manifest path resolves outside the configured root")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("sources"), list):
        raise ValueError("manifest must contain schema_version 1.0.0 and a sources array")
    mapping = config["sources"]
    if not isinstance(mapping, dict):
        raise ValueError("config sources must be an object keyed by manifest ID")
    outcomes = [
        _ingest_entry(entry, mapping, root) if isinstance(entry, dict) else {
            "manifest_id": None,
            "local_path": None,
            "status": "failed",
            "error": {"code": "invalid_manifest_entry", "message": "manifest source must be an object"},
        }
        for entry in manifest["sources"]
    ]
    return {"report_version": 1, "manifest": str(manifest_path), "outcomes": outcomes}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inventory local source files and verify their manifest metadata")
    parser.add_argument("--config", required=True, type=Path, help="pipeline JSON configuration file")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path (stdout by default)")
    args = parser.parse_args(argv)
    try:
        report = ingest(args.config)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"ingestion setup error: {exc}\n")
    return 1 if any(item["status"] == "failed" for item in report["outcomes"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
