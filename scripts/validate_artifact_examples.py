"""Validate artifact bundles with jsonschema and cross-record checks."""

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from jsonschema import Draft202012Validator

# Keep direct script execution usable without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_foundry.provenance.bundle import (
    SCHEMA, canonical_evidence, check, check_evidence, digest, require,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "data/manifest.json").read_text())


def check_manifest():
    require(MANIFEST["schema_version"] == "1.0.0", "manifest schema version mismatch")
    ids = [entry["manifest_id"] for entry in MANIFEST["sources"]]
    require(len(ids) == len(set(ids)), "duplicate manifest ID")
    for entry in MANIFEST["sources"]:
        require(all(isinstance(entry.get(key), str) and entry[key] for key in ("source_id", "title", "class")),
                f"manifest source metadata missing: {entry['manifest_id']}")
        require(entry["source_id"].startswith("src:"), f"invalid source ID: {entry['manifest_id']}")
        path = (ROOT / entry["local_path"]).resolve()
        require(path.is_relative_to(ROOT / "data"), "source file outside data directory")
        require(path.is_file(), f"source file missing: {entry['local_path']}")
        raw = path.read_bytes()
        require(len(raw) == entry["size_bytes"], f"source size mismatch: {entry['manifest_id']}")
        require(hashlib.sha256(raw).hexdigest() == entry["sha256"], f"source hash mismatch: {entry['manifest_id']}")
        require(date.fromisoformat(entry["retrieved_on"]).isoformat() == entry["retrieved_on"], "invalid retrieval date")
        require((raw.startswith(b"%PDF") if entry["media_type"] == "application/pdf" else b"<html" in raw[:100].lower()), f"media type mismatch: {entry['manifest_id']}")
    return MANIFEST["sources"]


def main():
    Draft202012Validator.check_schema(SCHEMA)
    captures = check_manifest()
    print(f"PASS data/manifest.json ({len(captures)} sources)")
    for filename in sys.argv[1:] or ["examples/artifact-bundle-pdf.json", "examples/artifact-bundle-html.json"]:
        path = ROOT / filename
        bundle = json.loads(path.read_text())
        check(bundle)
        for doc in bundle["documents"]:
            source = next(item for item in bundle["sources"] if item["id"] == doc["source_id"] and item["version"] == doc["source_version"])
            matches = [item for item in captures if item["publisher_url"] == source["uri"] and item["sha256"] == doc["content_sha256"]]
            require(len(matches) == 1, "example document missing from manifest")
            require(source["id"] == matches[0]["source_id"], "example source ID mismatch")
            require(source["title"] == matches[0]["title"], "example source title mismatch")
            require(source["class"] == matches[0]["class"], "example source class mismatch")
            require(doc["source_version"] == matches[0]["source_version"], "example source version mismatch")
            require(doc["retrieved_on"] == matches[0]["retrieved_on"], "example retrieval date mismatch")
            if source["format"] == "html":
                saved_html = (ROOT / matches[0]["local_path"]).read_text(encoding="utf-8")
                require(source["version"] in saved_html, "HTML article revision missing")
                require(all(element["text"] in saved_html for element in bundle["elements"] if element["document_id"] == doc["id"]), "HTML example passage missing")
        print(f"PASS {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
