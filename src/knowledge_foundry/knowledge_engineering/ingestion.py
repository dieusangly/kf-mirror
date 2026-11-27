"""Validate sources and persist reusable, content-addressed original bytes."""

import argparse
import hashlib
import json
import re
import sys
import zipfile
import os
import tempfile
from urllib.request import urlopen
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


def _ingest_entry(entry, root, store=None, *, fetch=False):
    outcome = {"manifest_id": entry.get("manifest_id"), "local_path": entry.get("local_path")}
    try:
        try:
            path = _source_path(root, entry.get("local_path"))
        except IngestionError as exc:
            if exc.code != "missing_file" or not fetch:
                raise
            # Download only an explicitly requested missing original. Validate
            # against the declared size/hash before installing it.
            relative = Path(entry["local_path"])
            target = (root / relative).resolve()
            uri = entry.get("download_url") or entry.get("publisher_url", "")
            if urlparse(uri).scheme not in {"http", "https"}:
                raise IngestionError("invalid_url", "fetch requires an HTTP(S) URL")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as directory:
                candidate = Path(directory) / target.name
                with urlopen(uri, timeout=60) as response, candidate.open("wb") as output:
                    limit = entry.get("size_bytes", 0)
                    size = 0
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > limit:
                            raise IngestionError("size_mismatch", "download exceeds declared size")
                        output.write(block)
                _, size, sha = _file_fingerprint(candidate)
                if sha != entry.get("sha256") or size != entry.get("size_bytes"):
                    raise IngestionError("hash_mismatch", "download differs from declared original")
                os.replace(candidate, target)
            path = target
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
        if any(not isinstance(entry.get(key), str) or not entry[key] for key in ("source_id", "title", "class")):
            raise IngestionError("missing_source_metadata", "manifest source_id, title, and class are required")
        source_id = entry["source_id"]
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
            "title": entry["title"],
            "class": entry["class"],
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
        outcome.update(status="ingested", source=source, document=document,
                       manifest_entry=entry)
        if store is not None:
            extension = {"markdown": ".md", "html": ".html", "pdf": ".pdf", "docx": ".docx"}[detected]
            relative = Path("objects") / (content_sha256 + extension)
            target = store / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if _file_fingerprint(target)[2] != content_sha256:
                    raise IngestionError("artifact_corrupt", "existing ingested bytes failed integrity verification")
            else:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                    name = Path(temporary.name)
                    try:
                        # Hash the exact bytes copied, closing the validation/copy race.
                        data = path.read_bytes()
                        if hashlib.sha256(data).hexdigest() != content_sha256:
                            raise IngestionError("source_changed", "original changed during ingestion")
                        temporary.write(data)
                        temporary.flush()
                        os.fsync(temporary.fileno())
                    except BaseException:
                        name.unlink(missing_ok=True)
                        raise
                os.replace(name, target)
            outcome["artifact_path"] = str(relative)
    except IngestionError as exc:
        error = {"code": exc.code, "message": str(exc)}
        if ("content_sha256" in outcome and isinstance(entry.get("sha256"), str)
                and entry["sha256"] != outcome["content_sha256"]):
            error["expected_sha256"] = entry["sha256"]
        outcome.update(status="failed", error=error)
    except (OSError, TypeError, zipfile.BadZipFile) as exc:
        outcome.update(status="failed", error={"code": "read_error", "message": str(exc)})
    return outcome


def ingest(config_path, output_dir=None, *, fetch=False):
    """Return one result per configured manifest ID, including failed entries."""
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = (config_path.parent / config["root"]).resolve()
    manifest_path = (root / config["manifest_file"]).resolve()
    if not manifest_path.is_relative_to(root):
        raise ValueError("manifest path resolves outside the configured root")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("sources"), list):
        raise ValueError("manifest must contain schema_version 1.0.0 and a sources array")
    selected = config["manifest_ids"]
    if (not isinstance(selected, list) or not selected or
            any(not isinstance(item, str) or not item for item in selected) or
            len(selected) != len(set(selected))):
        raise ValueError("config manifest_ids must be a nonempty list of unique manifest IDs")
    entries = [entry for entry in manifest["sources"] if isinstance(entry, dict) and isinstance(entry.get("manifest_id"), str)]
    ids = [entry["manifest_id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest IDs must be unique")
    by_id = {entry["manifest_id"]: entry for entry in entries}
    unknown = set(selected) - set(by_id)
    if unknown:
        raise ValueError(f"unknown configured manifest IDs: {', '.join(sorted(unknown))}")
    store = Path(output_dir).resolve() if output_dir is not None else None
    outcomes = [_ingest_entry(by_id[item], root, store, fetch=fetch) for item in selected]
    report = {"report_version": 2, "manifest": str(manifest_path), "outcomes": outcomes}
    if store is not None:
        store.mkdir(parents=True, exist_ok=True)
        # Immutable snapshot names allow several editions/selections in one store.
        identity = _digest(json.dumps(outcomes, sort_keys=True, ensure_ascii=False))
        report["artifact_id"] = "ingested:" + identity
        snapshots = store / "snapshots"
        snapshots.mkdir(exist_ok=True)
        data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (snapshots / (identity + ".json")).write_text(data, encoding="utf-8")
        temporary = store / "report.json.tmp"
        temporary.write_text(data, encoding="utf-8")
        temporary.replace(store / "report.json")
    return report


def load_ingested(path):
    """Read a frozen ingestion snapshot; never access the manifest or original paths."""
    path = Path(path).resolve()
    report_path = path / "report.json" if path.is_dir() else path
    store = report_path.parent.parent if report_path.parent.name == "snapshots" else report_path.parent
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("report_version") != 2 or not report.get("artifact_id"):
        raise ValueError("ingested artifact required; run scripts/ingest.py --output-dir first")
    expected = "ingested:" + _digest(json.dumps(report["outcomes"], sort_keys=True, ensure_ascii=False))
    if expected != report["artifact_id"]:
        raise ValueError("ingested snapshot identity mismatch")
    for row in report["outcomes"]:
        if row["status"] != "ingested":
            continue
        file = (store / row["artifact_path"]).resolve()
        if not file.is_relative_to(store) or _file_fingerprint(file)[2] != row["document"]["content_sha256"]:
            raise ValueError("ingested artifact integrity mismatch")
        row["artifact_file"] = str(file)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inventory saved source files and verify their manifest metadata")
    parser.add_argument("--config", required=True, type=Path, help="pipeline JSON configuration file")
    parser.add_argument("--output-dir", type=Path, help="persist verified original bytes and an ingestion snapshot")
    parser.add_argument("--fetch", action="store_true", help="fetch missing originals and verify declared hashes")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path (stdout by default)")
    args = parser.parse_args(argv)
    try:
        report = ingest(args.config, args.output_dir, fetch=args.fetch)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"ingestion setup error: {exc}\n")
    return 1 if any(item["status"] == "failed" for item in report["outcomes"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
