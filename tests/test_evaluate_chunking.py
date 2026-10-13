"""Focused checks for the reusable chunk-quality gate."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from knowledge_foundry.chunking import ROOT, _chunk_document, build_collection, digest
from scripts.evaluate_chunking import (
    _apply_reviews, _audit_document, _audit_figures, _audit_references,
    _read_review_policy, _read_sample_per_document, _write_review_seed, evaluate, main,
)


CHUNKED = ROOT / "artifacts/chunked"
PARSED = ROOT / "artifacts/parsed"
PILOT_AVAILABLE = (CHUNKED / "artifact-bundle.json").is_file() and len(
    list(PARSED.glob("documents/*/artifact-bundle.json"))) == 5
REVIEW_POLICY = json.loads((ROOT / "config/chunking-review-policy.json").read_text(encoding="utf-8"))


class ChunkEvaluationTests(unittest.TestCase):
    def test_write_review_uses_default_path_and_refuses_overwrite(self):
        report = {
            "automatic_gate": "pass", "review_gate": "pending", "gate": "pending_review",
            "review_template": {"build_id": "build:test", "reviews": [
                {"chunk_id": "chunk:test", "status": "pending", "severity": None,
                 "notes": "source inspection pending"}
            ]},
        }
        with tempfile.TemporaryDirectory() as directory:
            chunked = Path(directory) / "chunked"
            with redirect_stdout(io.StringIO()), patch("scripts.evaluate_chunking.evaluate", return_value=report):
                self.assertEqual(main(["--chunked-dir", str(chunked), "--initialize-review"]), 0)
            review_path = chunked / "chunking-review.json"
            self.assertEqual(json.loads(review_path.read_text(encoding="utf-8")), report["review_template"])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--chunked-dir", str(chunked), "--initialize-review"])
            self.assertEqual(error.exception.code, 2)
            with self.assertRaises(FileExistsError):
                _write_review_seed(review_path, report)
            custom_path = chunked / "custom-review.json"
            with redirect_stdout(io.StringIO()), patch("scripts.evaluate_chunking.evaluate", return_value=report):
                self.assertEqual(main(["--chunked-dir", str(chunked), "--initialize-review",
                                       str(custom_path)]), 0)
            self.assertEqual(json.loads(custom_path.read_text(encoding="utf-8")), report["review_template"])
            with redirect_stdout(io.StringIO()), patch("scripts.evaluate_chunking.evaluate", return_value=report):
                self.assertEqual(main(["--chunked-dir", str(chunked), "--review-file",
                                       str(review_path)]), 1)

    def test_sample_size_is_read_from_pipeline_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "pipeline.json"
            config_path.write_text(json.dumps({
                "evaluation": {"chunking": {"sample_per_document": 3}}
            }), encoding="utf-8")
            self.assertEqual(_read_sample_per_document(config_path), 3)

    def test_source_backed_continuation_context_is_not_duplicate_body(self):
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        template = bundle["elements"][0]
        elements = []
        sidecar = []
        for index, (kind, value, page) in enumerate([
            ("paragraph", "Ces cartes peuvent être", 1),
            ("footnote", "1 Note distincte.", 1),
            ("paragraph", "émises en France.", 2),
        ]):
            element = copy.deepcopy(template)
            element.update(id="el:" + digest(str(index)), kind=kind, text=value,
                           ordinal=index, locator_key=f"pdf:p{page}:block-{index}",
                           revision=digest(kind + "\n" + value),
                           location={"format": "pdf", "page": page, "label": f"block {index}"})
            elements.append(element)
            sidecar.append({"element_id": element["id"], "evidence_role": "authoritative",
                            "heading_path": []})
        bundle["elements"] = elements
        chunks, _ = _chunk_document(bundle, {"elements": sidecar}, "build:test", {"max_tokens": 64})
        result = _audit_document(bundle["documents"][0], bundle["sources"][0], chunks,
                                 elements, {"elements": sidecar}, 64, 3)
        self.assertEqual(result["findings"], [])

    def test_repeated_table_header_is_context_not_duplicate_coverage(self):
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        template = bundle["elements"][0]
        table = copy.deepcopy(template)
        value = "Header A | Header B\nrow one | detail\nrow two | detail\nrow three | detail"
        table.update(id="el:" + digest("table"), kind="table", text=value, ordinal=0,
                     locator_key="pdf:p1:block-0", revision=digest("table\n" + value),
                     location={"format": "pdf", "page": 1, "label": "block 0"})
        bundle["elements"] = [table]
        sidecar = {"elements": [{"element_id": table["id"], "evidence_role": "authoritative",
                                  "heading_path": []}]}
        chunks, _ = _chunk_document(bundle, sidecar, "build:test", {"max_tokens": 16})
        result = _audit_document(bundle["documents"][0], bundle["sources"][0], chunks,
                                 [table], sidecar, 16, 1)
        self.assertEqual(result["findings"], [])

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 5 pilot artifacts are unavailable")
    def test_pilot_gate_reference_cases_and_review_sample(self):
        report = evaluate(CHUNKED, PARSED, reference_path=ROOT / "tests/fixtures/pilot_parsing_reference.json")
        self.assertEqual(report["automatic_gate"], "pass")
        self.assertEqual(report["review_gate"], "pending")
        self.assertEqual(report["gate"], "pending_review")
        self.assertEqual(report["corpus"]["documents"], 5)
        self.assertGreater(report["corpus"]["chunks"], 0)
        self.assertEqual(report["corpus"]["authoritative_elements"], report["corpus"]["covered_elements"])
        self.assertEqual(report["corpus"]["fitting_list_items_split"], 0)
        self.assertEqual(report["figures"], {"expected": 94, "linked": 94})
        self.assertEqual(len(report["references"]), 8)
        self.assertTrue(all(item["pass"] for item in report["references"]))
        self.assertEqual(report["review"]["to_review"], len(report["review_template"]["reviews"]))
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

    def test_manual_review_policy_scores_severity_and_requires_complete_review(self):
        samples = [{"chunk_id": f"chunk:{index}"} for index in range(38)]
        build_id = "build:test"
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            {"chunk_id": "chunk:0", "status": "pass"},
            {"chunk_id": "chunk:1", "status": "pending", "notes": "source is unavailable"}]},
            build_id, samples, REVIEW_POLICY)
        self.assertEqual((summary["status"], summary["reviewed"], summary["pending"]), ("pending", 1, 37))
        review = [{"chunk_id": f"chunk:{index}", "status": "pass"} for index in range(38)]
        review[0] = {"chunk_id": "chunk:0", "status": "fail", "severity": "major", "notes": "missing lead"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            *review]}, build_id, samples, REVIEW_POLICY)
        self.assertEqual((summary["status"], summary["allowed_failures"]),
                         ("pass", {"critical": 0}))
        self.assertAlmostEqual(summary["weighted_score"], 97.89473684210526)
        review[1] = {"chunk_id": "chunk:1", "status": "fail", "severity": "major", "notes": "split sentence"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, REVIEW_POLICY)
        self.assertEqual(summary["status"], "pass")
        self.assertAlmostEqual(summary["weighted_score"], 95.78947368421052)
        review[2] = {"chunk_id": "chunk:2", "status": "fail", "severity": "major", "notes": "missing condition"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, REVIEW_POLICY)
        self.assertEqual(summary["status"], "fail")
        self.assertAlmostEqual(summary["weighted_score"], 93.6842105263158)
        self.assertEqual(summary["severity_counts"], {"critical": 0, "major": 3, "minor": 0})
        review[2] = {"chunk_id": "chunk:2", "status": "fail", "severity": "critical", "notes": "wrong table mapping"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, REVIEW_POLICY)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["severity_counts"], {"critical": 1, "major": 2, "minor": 0})
        review = [{"chunk_id": f"chunk:{index}", "status": "pass"} for index in range(38)]
        for index in range(8):
            review[index] = {"chunk_id": f"chunk:{index}", "status": "fail", "severity": "minor",
                             "notes": "formatting defect"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, REVIEW_POLICY)
        self.assertEqual(summary["status"], "fail")
        self.assertGreater(summary["weighted_score"], REVIEW_POLICY["minimum_weighted_score"])
        self.assertLess(summary["review_pass_rate"], REVIEW_POLICY["minimum_review_pass_rate"])
        with self.assertRaises(ValueError):
            _apply_reviews({"build_id": build_id, "reviews": [
                {"chunk_id": "chunk:0", "status": "fail", "notes": "missing severity"}
            ]}, build_id, samples, REVIEW_POLICY)
        with self.assertRaises(ValueError):
            _apply_reviews({"build_id": "build:old", "reviews": []}, build_id, samples, REVIEW_POLICY)

    def test_review_points_and_minimum_score_come_from_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-policy.json"
            policy = copy.deepcopy(REVIEW_POLICY)
            policy["review_points"]["major"] = 5
            policy["minimum_weighted_score"] = 99
            path.write_text(json.dumps(policy), encoding="utf-8")
            loaded = _read_review_policy(path)
            samples = [{"chunk_id": f"chunk:{index}"} for index in range(10)]
            review = [{"chunk_id": item["chunk_id"], "status": "pass"} for item in samples]
            review[0] = {"chunk_id": "chunk:0", "status": "fail", "severity": "major", "notes": "missing lead"}
            summary, _ = _apply_reviews({"build_id": "build:test", "reviews": review},
                                        "build:test", samples, loaded)
            self.assertEqual(summary["weighted_score"], 95)
            self.assertEqual(summary["status"], "fail")
            policy["minimum_weighted_score"] = 95
            path.write_text(json.dumps(policy), encoding="utf-8")
            loaded = _read_review_policy(path)
            summary, _ = _apply_reviews({"build_id": "build:test", "reviews": review},
                                        "build:test", samples, loaded)
            self.assertEqual(summary["status"], "pass")

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
            view_path = output / "documents" / doc_id.removeprefix("doc:") / "document-view.json"
            view = json.loads(view_path.read_text(encoding="utf-8"))
            view["chunks"][0]["text"] += " changed"
            view_path.write_text(json.dumps(view), encoding="utf-8")
            report = evaluate(output, input_dir, sample_count=2)
            self.assertEqual(report["automatic_gate"], "fail")
            self.assertIn("DOCUMENT_VIEW_MISMATCH", {item["code"] for item in report["findings"]})
            view_path.unlink()
            report = evaluate(output, input_dir, sample_count=2)
            self.assertIn("DOCUMENT_VIEW_MISSING", {item["code"] for item in report["findings"]})

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
