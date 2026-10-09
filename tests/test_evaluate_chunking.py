"""Focused checks for the reusable chunk-quality gate."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_foundry.chunking import ROOT, build_collection
from scripts.evaluate_chunking import _apply_reviews, _audit_document, _audit_figures, _audit_references, evaluate


CHUNKED = ROOT / "artifacts/chunked"
PARSED = ROOT / "artifacts/parsed"
PILOT_AVAILABLE = (CHUNKED / "artifact-bundle.json").is_file() and len(
    list(PARSED.glob("documents/*/artifact-bundle.json"))) == 5


class ChunkEvaluationTests(unittest.TestCase):
    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 5 pilot artifacts are unavailable")
    def test_pilot_gate_reference_cases_and_review_sample(self):
        report = evaluate(CHUNKED, PARSED, reference_path=ROOT / "tests/fixtures/pilot_parsing_reference.json")
        self.assertEqual(report["automatic_gate"], "pass")
        self.assertEqual(report["review_gate"], "pending")
        self.assertEqual(report["gate"], "pending_review")
        self.assertEqual(report["corpus"]["documents"], 5)
        self.assertEqual(report["corpus"]["chunks"], 805)
        self.assertEqual(report["corpus"]["authoritative_elements"], report["corpus"]["covered_elements"])
        self.assertEqual(report["corpus"]["fitting_list_items_split"], 0)
        self.assertEqual(report["figures"], {"expected": 94, "linked": 94})
        self.assertEqual(len(report["references"]), 8)
        self.assertTrue(all(item["pass"] for item in report["references"]))
        self.assertEqual(report["review"]["required"], len(report["review_template"]["reviews"]))
        self.assertEqual([len(doc["review_sample"]) for doc in report["documents"]], [12, 12, 12, 1, 1])
        self.assertTrue(all(doc["source_file"] for doc in report["documents"]))
        self.assertTrue(all(sample["evidence"] and all("location" in ref for ref in sample["evidence"])
                            for doc in report["documents"] for sample in doc["review_sample"]))
        self.assertEqual(report, evaluate(CHUNKED, PARSED,
                                          reference_path=ROOT / "tests/fixtures/pilot_parsing_reference.json"))

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 5 pilot artifacts are unavailable")
    def test_detects_unsupported_text_and_missing_coverage(self):
        bundle = json.loads((CHUNKED / "artifact-bundle.json").read_text(encoding="utf-8"))
        doc = next(item for item in bundle["documents"] if item["source_id"] == "src:cmf-L561-2-2")
        source = next(item for item in bundle["sources"] if item["id"] == doc["source_id"])
        elements = [item for item in bundle["elements"] if item["document_id"] == doc["id"]]
        sidecar = json.loads((PARSED / "documents" / doc["id"].removeprefix("doc:") / "parse.json").read_text(encoding="utf-8"))
        chunks = [copy.deepcopy(item) for item in bundle["chunks"] if item["document_id"] == doc["id"]]
        chunks[0]["text"] += " unsupported"
        codes = {item["code"] for item in _audit_document(doc, source, chunks, elements, sidecar, 256, 1)["findings"]}
        self.assertIn("CHUNK_TEXT_MISMATCH", codes)
        chunks[0]["evidence"].pop()
        codes = {item["code"] for item in _audit_document(doc, source, chunks, elements, sidecar, 256, 1)["findings"]}
        self.assertIn("AUTHORITATIVE_TEXT_MISSING", codes)

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 5 pilot artifacts are unavailable")
    def test_figure_link_failure(self):
        bundle = json.loads((CHUNKED / "artifact-bundle.json").read_text(encoding="utf-8"))
        link = copy.deepcopy(json.loads((CHUNKED / "figure-links.json").read_text(encoding="utf-8"))[0])
        doc = next(item for item in bundle["documents"] if item["id"] == link["document_id"])
        sidecar = json.loads((PARSED / "documents" / doc["id"].removeprefix("doc:") / "parse.json").read_text(encoding="utf-8"))
        sidecar["elements"] = [item for item in sidecar["elements"] if item["element_id"] == link["element_id"]]
        link["asset"]["path"] = "../outside.png"
        result = _audit_figures([link], PARSED, {doc["id"]: sidecar}, {doc["id"]: doc},
                                {element["id"]: element for element in bundle["elements"]})
        self.assertIn("FIGURE_ASSET_INVALID", {item["code"] for item in result["findings"]})
        two_assets = [item for item in json.loads((PARSED / "documents" / doc["id"].removeprefix("doc:") /
                                                  "parse.json").read_text(encoding="utf-8"))["elements"]
                      if item.get("asset")][:2]
        sidecar["elements"] = list(reversed(two_assets))
        missing = _audit_figures([], PARSED, {doc["id"]: sidecar}, {doc["id"]: doc},
                                 {element["id"]: element for element in bundle["elements"]})
        examples = next(item["examples"] for item in missing["findings"]
                        if item["code"] == "FIGURE_LINK_MISSING")
        self.assertEqual([item["element_id"] for item in examples],
                         sorted(item["element_id"] for item in two_assets))

    def test_manual_review_statuses(self):
        samples = [{"chunk_id": "chunk:one"}, {"chunk_id": "chunk:two"}]
        build_id = "build:test"
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            {"chunk_id": "chunk:one", "status": "pass"},
            {"chunk_id": "chunk:two", "status": "pending"}]}, build_id, samples)
        self.assertEqual((summary["status"], summary["reviewed"]), ("pending", 1))
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            {"chunk_id": "chunk:one", "status": "pass"},
            {"chunk_id": "chunk:two", "status": "fail", "notes": "wrong section"}]}, build_id, samples)
        self.assertEqual(summary["status"], "fail")
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            {"chunk_id": "chunk:one", "status": "pass"},
            {"chunk_id": "chunk:two", "status": "pass"}]}, build_id, samples)
        self.assertEqual(summary["status"], "pass")
        with self.assertRaises(ValueError):
            _apply_reviews({"build_id": "build:old", "reviews": []}, build_id, samples)

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 5 pilot artifacts are unavailable")
    def test_reference_failure_is_reported(self):
        bundle = json.loads((CHUNKED / "artifact-bundle.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
        results, findings = _audit_references({"passages": [{"id": "missing", "source": "L1",
                                                              "text": "phrase absente du document"}]},
                                               manifest, bundle["documents"], bundle["chunks"],
                                               {item["id"]: item for item in bundle["elements"]})
        self.assertFalse(results[0]["pass"])
        self.assertEqual(findings[0]["code"], "REFERENCE_PASSAGE_MISSING")

    def test_one_document_uses_same_generic_gate(self):
        parsed = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        for name in ("chunks", "claims", "topics", "builds"):
            parsed[name] = []
        doc_id = parsed["documents"][0]["id"]
        sidecar = {"document_id": doc_id, "elements": [{"element_id": parsed["elements"][0]["id"],
                                                          "evidence_role": "authoritative", "heading_path": []}]}
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            input_dir = root / "parsed"
            target = input_dir / "documents" / doc_id.removeprefix("doc:")
            target.mkdir(parents=True)
            (target / "artifact-bundle.json").write_text(json.dumps(parsed), encoding="utf-8")
            (target / "parse.json").write_text(json.dumps(sidecar), encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({"root": str(ROOT), "manifest_file": "data/manifest.json",
                                          "manifest_ids": ["L1"], "chunking": {"max_tokens": 64}}))
            output = root / "chunked"
            build_collection(config, input_dir, output)
            report = evaluate(output, input_dir, sample_count=2)
            self.assertEqual(report["automatic_gate"], "pass")
            self.assertEqual(report["corpus"]["documents"], 1)
            self.assertTrue(all(item["metrics"]["max_tokens"] <= 64 for item in report["documents"]))

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 5 pilot artifacts are unavailable")
    def test_bundle_without_build_reports_failure(self):
        bundle = json.loads((CHUNKED / "artifact-bundle.json").read_text(encoding="utf-8"))
        bundle["chunks"] = []
        bundle["builds"] = []
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            for filename in ("report.json", "figure-links.json"):
                (folder / filename).write_bytes((CHUNKED / filename).read_bytes())
            report = evaluate(folder, PARSED)
        self.assertEqual(report["automatic_gate"], "fail")
        self.assertEqual(report["findings"][0]["code"], "BUILD_RECORD_COUNT")


if __name__ == "__main__":
    unittest.main()
