import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from knowledge_foundry.ingestion import ingest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class IngestionTests(unittest.TestCase):
    def test_pilot_pdf_and_html_match_task_2_examples(self):
        report = ingest(ROOT / "config/ingestion.json")
        self.assertEqual(len(report["outcomes"]), 5)
        self.assertTrue(all(item["status"] == "ingested" for item in report["outcomes"]))
        outcomes = {item["manifest_id"]: item for item in report["outcomes"]}
        self.assertEqual([item["detected_format"] for item in report["outcomes"]], ["pdf", "pdf", "pdf", "html", "html"])
        for manifest_id, example_name in (("A", "artifact-bundle-pdf.json"), ("L1", "artifact-bundle-html.json")):
            example = json.loads((ROOT / "examples" / example_name).read_text(encoding="utf-8"))
            self.assertEqual(outcomes[manifest_id]["source"], example["sources"][0])
            self.assertEqual(outcomes[manifest_id]["document"], example["documents"][0])
        bundle = {
            "schema_version": "1.0.0",
            "sources": [item["source"] for item in report["outcomes"]],
            "documents": [item["document"] for item in report["outcomes"]],
            "elements": [], "chunks": [], "builds": [],
        }
        VALIDATOR.validate(bundle)

    def test_missing_and_unsupported_files_have_separate_outcomes_and_cli_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unknown.bin").write_bytes(b"not a supported document\x00")
            raw = (root / "unknown.bin").read_bytes()
            entries = [
                {"id": "A", "local_path": "missing.pdf", "media_type": "application/pdf"},
                {"id": "L1", "local_path": "unknown.bin", "media_type": "text/html", "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            ]
            (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": entries}), encoding="utf-8")
            config = json.loads((ROOT / "config/ingestion.json").read_text(encoding="utf-8"))
            config["root"] = "."
            config["manifest"] = "manifest.json"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            report = ingest(config_path)
            self.assertEqual([item["error"]["code"] for item in report["outcomes"]], ["missing_file", "unsupported_format"])
            self.assertEqual([item["manifest_id"] for item in report["outcomes"]], ["A", "L1"])
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/ingest.py"), "--config", str(config_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(len(json.loads(result.stdout)["outcomes"]), 2)

    def test_changed_bytes_are_reported_without_document_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "changed.pdf").write_bytes(b"%PDF-1.7\nchanged")
            manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
            entry = copy.deepcopy(manifest["sources"][0])
            entry["local_path"] = "changed.pdf"
            entry["size_bytes"] = (root / "changed.pdf").stat().st_size
            (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": [entry]}), encoding="utf-8")
            config = json.loads((ROOT / "config/ingestion.json").read_text(encoding="utf-8"))
            config.update(root=".", manifest="manifest.json")
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            outcome = ingest(config_path)["outcomes"][0]
            self.assertEqual(outcome["error"]["code"], "hash_mismatch")
            self.assertEqual(outcome["detected_format"], "pdf")
            self.assertNotIn("document", outcome)


if __name__ == "__main__":
    unittest.main()
