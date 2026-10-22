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
from scripts.validate_artifact_examples import canonical_evidence, check as check_artifact_bundle, digest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class IngestionTests(unittest.TestCase):
    def test_pilot_pdf_and_html_match_task_2_examples(self):
        report = ingest(ROOT / "config/pipeline.json")
        selected = json.loads((ROOT / "config/pipeline.json").read_text())["manifest_ids"]
        self.assertEqual([item["manifest_id"] for item in report["outcomes"]], selected)
        self.assertTrue(all(item["status"] == "ingested" for item in report["outcomes"]))
        outcomes = {item["manifest_id"]: item for item in report["outcomes"]}
        self.assertEqual([outcomes[mid]["detected_format"] for mid in ("A", "T", "M", "L1", "L2")],
                         ["pdf", "pdf", "pdf", "html", "html"])
        for manifest_id, example_name in (("A", "artifact-bundle-pdf.json"), ("L1", "artifact-bundle-html.json")):
            example = json.loads((ROOT / "examples" / example_name).read_text(encoding="utf-8"))
            self.assertEqual(outcomes[manifest_id]["source"], example["sources"][0])
            self.assertEqual(outcomes[manifest_id]["document"], example["documents"][0])
        bundle = {
            "schema_version": "1.0.0",
            "sources": [item["source"] for item in report["outcomes"]],
            "documents": [item["document"] for item in report["outcomes"]],
            "elements": [], "chunks": [], "claims": [], "topics": [], "builds": [],
        }
        VALIDATOR.validate(bundle)

    def test_configured_sources_select_and_order_manifest_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            config = json.loads((ROOT / "config/pipeline.json").read_text(encoding="utf-8"))
            config.update(root=str(ROOT), manifest_ids=["L1", "A"])
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            report = ingest(path)
            self.assertEqual([item["manifest_id"] for item in report["outcomes"]], ["L1", "A"])
            self.assertEqual(report["outcomes"][1]["source"]["id"], "src:acpr-tracfin-joint-guidelines")

    def test_manifest_metadata_is_required_and_unknown_selection_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "example.md"
            path.write_text("# Example\n", encoding="utf-8")
            raw = path.read_bytes()
            entry = {"manifest_id": "A", "title": "Example", "class": "test", "source_version": "v1",
                     "publisher_url": "https://example.org/example.md", "local_path": "example.md",
                     "retrieved_on": "2026-10-08", "media_type": "text/markdown",
                     "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": [entry]}))
            config_path = root / "config.json"
            config = {"root": ".", "manifest_file": "manifest.json", "manifest_ids": ["A"]}
            config_path.write_text(json.dumps(config))
            outcome = ingest(config_path)["outcomes"][0]
            self.assertEqual(outcome["error"]["code"], "missing_source_metadata")
            self.assertNotIn("expected_sha256", outcome["error"])
            config["manifest_ids"] = ["unknown"]
            config_path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "unknown configured manifest IDs"):
                ingest(config_path)

    def test_example_topics_have_resolvable_claims_and_evidence(self):
        for name in ("artifact-bundle-pdf.json", "artifact-bundle-html.json"):
            with self.subTest(name=name):
                bundle = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
                check_artifact_bundle(bundle)
                broken = copy.deepcopy(bundle)
                broken["claims"][0]["chunk_ids"] = ["chunk:missing"]
                with self.assertRaisesRegex(ValueError, "claim chunk missing"):
                    check_artifact_bundle(broken)
                broken = copy.deepcopy(bundle)
                broken["topics"][0]["claim_ids"] = ["claim:missing"]
                with self.assertRaisesRegex(ValueError, "topic claim missing"):
                    check_artifact_bundle(broken)
                broken = copy.deepcopy(bundle)
                broken["claims"][0]["topic_id"] = "topic:missing"
                with self.assertRaisesRegex(ValueError, "claim topic missing"):
                    check_artifact_bundle(broken)
                broken = copy.deepcopy(bundle)
                extra_claim = copy.deepcopy(broken["claims"][0])
                extra_claim["text"] += " Additional assertion."
                extra_claim["id"] = "claim:" + digest(extra_claim["build_id"] + "\n" + canonical_evidence(extra_claim["evidence"]) + "\n" + extra_claim["text"])
                broken["claims"].append(extra_claim)
                with self.assertRaisesRegex(ValueError, "topic claim set mismatch"):
                    check_artifact_bundle(broken)
                broken = copy.deepcopy(bundle)
                other_topic = copy.deepcopy(broken["topics"][0])
                other_topic["id"] = "topic:other"
                broken["topics"].append(other_topic)
                with self.assertRaisesRegex(ValueError, "topic claim link mismatch"):
                    check_artifact_bundle(broken)
                broken = copy.deepcopy(bundle)
                broken["claims"][0]["evidence"][0]["span"]["end"] -= 1
                with self.assertRaisesRegex(ValueError, "claim evidence outside supporting chunks"):
                    check_artifact_bundle(broken)
                for record_type in ("chunks", "claims"):
                    with self.subTest(record_type=record_type):
                        broken = copy.deepcopy(bundle)
                        del broken[record_type][0]["evidence"][0]["element_revision"]
                        self.assertFalse(VALIDATOR.is_valid(broken))
                        broken = copy.deepcopy(bundle)
                        broken[record_type][0]["evidence"][0]["element_revision"] = "0" * 64
                        with self.assertRaisesRegex(ValueError, "evidence element revision mismatch"):
                            check_artifact_bundle(broken)
                changed = copy.deepcopy(bundle)
                element = changed["elements"][0]
                element["text"] += " Changed by a later parse."
                element["revision"] = digest(element["kind"] + "\n" + element["text"])
                with self.assertRaisesRegex(ValueError, "evidence element revision mismatch"):
                    check_artifact_bundle(changed)
                for classification_review_status in ("draft", "rejected"):
                    with self.subTest(classification_review_status=classification_review_status):
                        reviewed = copy.deepcopy(bundle)
                        reviewed["claims"][0]["grounding_review_status"] = "verified"
                        reviewed["claims"][0]["classification_review_status"] = classification_review_status
                        check_artifact_bundle(reviewed)
                broken = copy.deepcopy(bundle)
                broken["claims"][0]["review_status"] = broken["claims"][0].pop("grounding_review_status")
                self.assertFalse(VALIDATOR.is_valid(broken))
                broken = copy.deepcopy(bundle)
                del broken["claims"][0]["classification"]
                self.assertFalse(VALIDATOR.is_valid(broken))
                broken = copy.deepcopy(bundle)
                broken["claims"][0]["classification"] = {"method": "model", "version": "classifier-v1"}
                self.assertFalse(VALIDATOR.is_valid(broken))
                broken = copy.deepcopy(bundle)
                broken["subjects"] = broken.pop("topics")
                self.assertFalse(VALIDATOR.is_valid(broken))

    def test_reclassification_preserves_claim_id(self):
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        claim = bundle["claims"][0]
        original_id = claim["id"]
        extra_claim = copy.deepcopy(claim)
        extra_claim["text"] += " Additional assertion."
        extra_claim["id"] = "claim:" + digest(extra_claim["build_id"] + "\n" + canonical_evidence(extra_claim["evidence"]) + "\n" + extra_claim["text"])
        bundle["claims"].append(extra_claim)
        original_topic = bundle["topics"][0]
        original_topic["claim_ids"] = [extra_claim["id"]]
        original_topic["revision"] = digest(original_topic["title"] + "\n" + original_topic["summary"] + "\n" + extra_claim["id"])
        new_topic = copy.deepcopy(original_topic)
        new_topic["id"] = "topic:reclassified"
        new_topic["claim_ids"] = [original_id]
        new_topic["revision"] = digest(new_topic["title"] + "\n" + new_topic["summary"] + "\n" + original_id)
        bundle["topics"].append(new_topic)
        claim["topic_id"] = new_topic["id"]
        bundle["builds"][0]["output_claim_ids"].append(extra_claim["id"])
        bundle["builds"][0]["output_topic_ids"].append(new_topic["id"])
        check_artifact_bundle(bundle)
        self.assertEqual(claim["id"], original_id)

    def test_missing_and_unsupported_files_have_separate_outcomes_and_cli_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unknown.bin").write_bytes(b"not a supported document\x00")
            raw = (root / "unknown.bin").read_bytes()
            entries = [
                {"manifest_id": "A", "local_path": "missing.pdf", "media_type": "application/pdf"},
                {"manifest_id": "L1", "local_path": "unknown.bin", "media_type": "text/html", "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            ]
            (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": entries}), encoding="utf-8")
            config = json.loads((ROOT / "config/pipeline.json").read_text(encoding="utf-8"))
            config["root"] = "."
            config["manifest_file"] = "manifest.json"
            config["manifest_ids"] = ["A", "L1"]
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
            config = json.loads((ROOT / "config/pipeline.json").read_text(encoding="utf-8"))
            config.update(root=".", manifest_file="manifest.json", manifest_ids=["A"])
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            outcome = ingest(config_path)["outcomes"][0]
            self.assertEqual(outcome["error"]["code"], "hash_mismatch")
            self.assertEqual(outcome["error"]["expected_sha256"], entry["sha256"])
            self.assertEqual(outcome["content_sha256"], hashlib.sha256((root / "changed.pdf").read_bytes()).hexdigest())
            self.assertEqual(outcome["detected_format"], "pdf")
            self.assertNotIn("document", outcome)

            entry["size_bytes"] += 1
            (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": [entry]}), encoding="utf-8")
            outcome = ingest(config_path)["outcomes"][0]
            self.assertEqual(outcome["error"]["code"], "size_mismatch")
            self.assertEqual(outcome["error"]["expected_sha256"], entry["sha256"])

            entry["sha256"] = outcome["content_sha256"]
            (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": [entry]}), encoding="utf-8")
            outcome = ingest(config_path)["outcomes"][0]
            self.assertEqual(outcome["error"]["code"], "size_mismatch")
            self.assertNotIn("expected_sha256", outcome["error"])


if __name__ == "__main__":
    unittest.main()
