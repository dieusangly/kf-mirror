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
from scripts.auto_review_chunking import (
    _apply_reviews, _audit_document, _audit_figures, _audit_references,
    _read_review_policy, _read_sample_per_document, _write_review_seed, evaluate, main,
)


CHUNKED = ROOT / 'artifacts/20261027-docling-aml-cft-01/document-structuring/chunks'
PARSED = ROOT / 'artifacts/20261027-docling-aml-cft-01/document-structuring/parsing'
PILOT_AVAILABLE = (CHUNKED / 'structuring-manifest.json').is_file()
REVIEW_POLICY = _read_review_policy(ROOT / "config/review-policy.json")


class ChunkEvaluationTests(unittest.TestCase):
    def test_split_opening_context_is_required_and_not_duplicate_body(self):
        bundle = json.loads((ROOT / 'examples/artifact-bundle-html.json').read_text())
        element = copy.deepcopy(bundle['elements'][0])
        element.update(kind='list_item', ordinal=0, text='(85) Foreign entities present a risk. ' +
                       ' '.join('Those risks require ' + 'detail ' * 15 + '.' for _ in range(8)))
        bundle['elements'] = [element]
        sidecar = {'elements': [{'element_id': element['id'], 'evidence_role': 'authoritative', 'heading_path': []}]}
        chunks, _ = _chunk_document(bundle, sidecar, 'build:test', {'max_tokens': 64})
        audit = lambda cs: _audit_document(bundle['documents'][0], bundle['sources'][0], cs,
                                           [element], sidecar, 64, 0)
        self.assertEqual(audit(chunks)['findings'], [])
        broken = copy.deepcopy(chunks)
        self.assertEqual(len(chunks), 1)
        broken[0]['evidence'][0]['span']['start'] = 40
        codes = {f['code'] for f in audit(broken)['findings']}
        self.assertIn('STRUCTURAL_CONTENT_MISSING', codes)

    def test_cross_page_body_and_detached_notes_have_exact_independent_order(self):
        bundle = json.loads((ROOT / 'examples/artifact-bundle-html.json').read_text())
        template = bundle['elements'][0]
        rows = [('list_item', '10. A person does not qualify when the', 1),
                ('footnote', '2 A detached note.', 1),
                ('paragraph', 'conditions are absent.', 2)]
        elements, details = [], []
        for index, (kind, text, page) in enumerate(rows):
            element = {**template, 'id': 'el:' + digest(str(index)), 'ordinal': index,
                       'kind': kind, 'text': text, 'location': {'format': 'pdf', 'page': page, 'label': str(index)}}
            elements.append(element)
            details.append({'element_id': element['id'], 'evidence_role': 'authoritative', 'heading_path': []})
        bundle['elements'] = elements
        sidecar = {'elements': details}
        chunks, _ = _chunk_document(bundle, sidecar, 'build:test', {'max_tokens': 64})
        audit = lambda cs: _audit_document(bundle['documents'][0], bundle['sources'][0], cs, elements, sidecar, 64, 0)
        self.assertEqual(audit(chunks)['findings'], [])
        changed = copy.deepcopy(chunks)
        changed[0]['evidence'][0], changed[0]['evidence'][1] = changed[0]['evidence'][1], changed[0]['evidence'][0]
        self.assertIn('READING_ORDER', {f['code'] for f in audit(changed)['findings']})

    def test_native_multiline_table_headers_preserve_exact_coverage(self):
        bundle = json.loads((ROOT / 'examples/artifact-bundle-html.json').read_text())
        table = copy.deepcopy(bundle['elements'][0])
        value = '1. Region | Annual results\nArea | Number\n' + '\n'.join(
            f'area {i} | ' + 'detail ' * 12 for i in range(5))
        table.update(kind='table', text=value, ordinal=0)
        elements = [table]
        sidecar = [{'element_id': table['id'], 'heading_path': [], 'evidence_role': 'authoritative'}]
        for row in range(2):
            for col in range(2):
                cell = copy.deepcopy(table)
                cell.update(id='el:' + digest(f'{row}:{col}'), kind='table_cell', text='label',
                            ordinal=len(elements))
                elements.append(cell)
                sidecar.append({'element_id': cell['id'], 'parent_element_id': table['id'],
                                'row': row, 'column': col, 'header': True, 'row_span': 1,
                                'evidence_role': 'structural'})
        bundle['elements'] = elements
        chunks, _ = _chunk_document(bundle, {'elements': sidecar}, 'build:test', {'max_tokens': 40})
        result = _audit_document(bundle['documents'][0], bundle['sources'][0], chunks,
                                 elements, {'elements': sidecar}, 40, 0)
        self.assertEqual(result['findings'], [])
        self.assertEqual(len(chunks), 1)
        self.assertTrue(all('Region | Annual results' in c['text'] and 'Area | Number' in c['text']
                            for c in chunks))

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
            with redirect_stdout(io.StringIO()), patch("scripts.auto_review_chunking.evaluate", return_value=report):
                self.assertEqual(main(["--chunked-dir", str(chunked), "--initialize-review"]), 0)
            review_path = chunked / "chunking-review.json"
            self.assertEqual(json.loads(review_path.read_text(encoding="utf-8")), report["review_template"])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--chunked-dir", str(chunked), "--initialize-review"])
            self.assertEqual(error.exception.code, 2)
            with self.assertRaises(FileExistsError):
                _write_review_seed(review_path, report)
            custom_path = chunked / "custom-review.json"
            with redirect_stdout(io.StringIO()), patch("scripts.auto_review_chunking.evaluate", return_value=report):
                self.assertEqual(main(["--chunked-dir", str(chunked), "--initialize-review",
                                       str(custom_path)]), 0)
            self.assertEqual(json.loads(custom_path.read_text(encoding="utf-8")), report["review_template"])
            with redirect_stdout(io.StringIO()), patch("scripts.auto_review_chunking.evaluate", return_value=report):
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


    @unittest.skipUnless(PILOT_AVAILABLE, "saved chunked pilot artifacts are unavailable")
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
        self.assertIn("STRUCTURAL_CONTENT_MISSING", codes)

    @unittest.skipUnless(PILOT_AVAILABLE, "saved chunked pilot artifacts are unavailable")
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
        policy = {**REVIEW_POLICY, "minimum_weighted_score": 95}
        samples = [{"chunk_id": f"chunk:{index}"} for index in range(38)]
        build_id = "build:test"
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            {"chunk_id": "chunk:0", "status": "pass"},
            {"chunk_id": "chunk:1", "status": "pending", "notes": "source is unavailable"}]},
            build_id, samples, policy)
        self.assertEqual((summary["status"], summary["reviewed"], summary["pending"]), ("pending", 1, 37))
        review = [{"chunk_id": f"chunk:{index}", "status": "pass"} for index in range(38)]
        review[0] = {"chunk_id": "chunk:0", "status": "fail", "severity": "major", "notes": "missing lead"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": [
            *review]}, build_id, samples, policy)
        self.assertEqual((summary["status"], summary["allowed_failures"]),
                         ("pass", {"critical": 0}))
        self.assertAlmostEqual(summary["weighted_score"], 97.89473684210526)
        review[1] = {"chunk_id": "chunk:1", "status": "fail", "severity": "major", "notes": "split sentence"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, policy)
        self.assertEqual(summary["status"], "pass")
        self.assertAlmostEqual(summary["weighted_score"], 95.78947368421052)
        review[2] = {"chunk_id": "chunk:2", "status": "fail", "severity": "major", "notes": "missing condition"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, policy)
        self.assertEqual(summary["status"], "fail")
        self.assertAlmostEqual(summary["weighted_score"], 93.6842105263158)
        self.assertEqual(summary["severity_counts"], {"critical": 0, "major": 3, "minor": 0})
        review[2] = {"chunk_id": "chunk:2", "status": "fail", "severity": "critical", "notes": "wrong table mapping"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, policy)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["severity_counts"], {"critical": 1, "major": 2, "minor": 0})
        review = [{"chunk_id": f"chunk:{index}", "status": "pass"} for index in range(38)]
        for index in range(8):
            review[index] = {"chunk_id": f"chunk:{index}", "status": "fail", "severity": "minor",
                             "notes": "formatting defect"}
        summary, _ = _apply_reviews({"build_id": build_id, "reviews": review}, build_id, samples, policy)
        self.assertEqual(summary["status"], "fail")
        self.assertGreater(summary["weighted_score"], policy["minimum_weighted_score"])
        self.assertLess(summary["review_pass_rate"], policy["minimum_review_pass_rate"])
        with self.assertRaises(ValueError):
            _apply_reviews({"build_id": build_id, "reviews": [
                {"chunk_id": "chunk:0", "status": "fail", "notes": "missing severity"}
            ]}, build_id, samples, policy)
        with self.assertRaises(ValueError):
            _apply_reviews({"build_id": "build:old", "reviews": []}, build_id, samples, policy)

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

    @unittest.skipUnless(PILOT_AVAILABLE, "saved chunked pilot artifacts are unavailable")
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

    @unittest.skipUnless(PILOT_AVAILABLE, "saved chunked pilot artifacts are unavailable")
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


class ChunkReviewerMetadataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.parsed = self.root / "parsed"
        self.chunked = self.root / "chunked"
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        for name in ("chunks", "claims", "topics", "builds"):
            bundle[name] = []
        doc_id = bundle["documents"][0]["id"]
        target = self.parsed / "documents" / doc_id.removeprefix("doc:")
        target.mkdir(parents=True)
        (target / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        sidecar = {"document_id": doc_id, "elements": [
            {"element_id": element["id"], "evidence_role": "authoritative", "heading_path": []}
            for element in bundle["elements"]]}
        (target / "parse.json").write_text(json.dumps(sidecar), encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"root": str(ROOT), "manifest_file": "data/manifest.json",
                                      "manifest_ids": ["L1"], "chunking": {"max_tokens": 64}}))
        build_collection(config, self.parsed, self.chunked)
        self.initial = evaluate(self.chunked, self.parsed)
        self.review = copy.deepcopy(self.initial["review_template"])
        self.review_path = self.root / "review.json"

    def assess(self):
        for row in self.review["reviews"]:
            row.update(status="pass", notes="Fixture judgment only.")

    def evaluate_review(self, **kwargs):
        self.review_path.write_text(json.dumps(self.review), encoding="utf-8")
        return evaluate(self.chunked, self.parsed, review_path=self.review_path, **kwargs)

    def prepare_additional_finding(self):
        parsed_path = next(self.parsed.glob("documents/*/artifact-bundle.json"))
        bundle = json.loads(parsed_path.read_text())
        element = copy.deepcopy(bundle["elements"][0])
        element.update(ordinal=1, locator_key="html:fixture:paragraph-2",
                       text="Fixture paragraph " + " ".join(f"word{i}" for i in range(52)) + ".")
        element["id"] = "el:" + digest(element["document_id"] + "\n" + element["locator_key"])
        element["revision"] = digest(element["kind"] + "\n" + element["text"])
        element["location"]["selector"] = "article > p:nth-of-type(2)"
        bundle["elements"].append(element)
        parsed_path.write_text(json.dumps(bundle))
        sidecar_path = parsed_path.with_name("parse.json")
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["elements"].append({"element_id": element["id"], "evidence_role": "authoritative", "heading_path": []})
        sidecar_path.write_text(json.dumps(sidecar))
        self.chunked = self.root / "chunked-extra"
        build_collection(self.root / "config.json", self.parsed, self.chunked)
        initial = evaluate(self.chunked, self.parsed, sample_count=1)
        self.review = copy.deepcopy(initial["review_template"])
        self.review["reviewer"] = {"kind": "agent", "name": "Fixture reviewer", "model": "test-model"}
        sampled = {row["chunk_id"] for row in self.review["reviews"]}
        bundle = json.loads((self.chunked / "artifact-bundle.json").read_text())
        extra_id = next(chunk["id"] for chunk in bundle["chunks"] if chunk["id"] not in sampled)
        self.review["additional_findings"] = [{"chunk_id": extra_id, "status": "fail",
            "severity": "critical", "notes": "Fixture source location: saved HTML article, paragraph 2."}]

    def test_additional_critical_finding_blocks_complete_and_pending_review(self):
        self.prepare_additional_finding()
        for status in ("pending", "pass"):
            self.review["reviews"][0]["status"] = status
            report = self.evaluate_review(sample_count=1)
            self.assertEqual(report["automatic_gate"], "pass")
            self.assertEqual(report["review_gate"], "fail")
            self.assertEqual(report["gate"], "fail")
            self.assertEqual(report["review"]["to_review"], 2)
            self.assertEqual(report["review"]["severity_counts"]["critical"], 1)
            self.assertEqual(report["additional_findings"], self.review["additional_findings"])
        output = self.root / "evaluation.json"
        before = self.review_path.read_bytes()
        with redirect_stdout(io.StringIO()):
            code = main(["--chunked-dir", str(self.chunked), "--parsed-dir", str(self.parsed),
                         "--sample-per-document", "1", "--review-file", str(self.review_path),
                         "--output", str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.read_text())["additional_findings"], self.review["additional_findings"])
        self.assertEqual(self.review_path.read_bytes(), before)

    def test_additional_noncritical_findings_use_selected_policy(self):
        self.prepare_additional_finding()
        self.assess()
        policy = {**REVIEW_POLICY, "minimum_review_pass_rate": 0.5}
        policy_path = self.root / "policy.json"
        policy_path.write_text(json.dumps(policy))
        del self.review["review_policy_sha256"]  # legacy review uses the explicitly selected policy
        for severity, score, gate in [("minor", 90.0, "pass"), ("major", 60.0, "fail")]:
            self.review["additional_findings"][0]["severity"] = severity
            report = self.evaluate_review(sample_count=1, review_policy_path=policy_path)
            self.assertEqual(report["review"]["weighted_score"], score)
            self.assertEqual(report["gate"], gate)

    def test_invalid_additional_findings_are_rejected(self):
        self.prepare_additional_finding()
        extra = self.review["additional_findings"][0]
        invalid = [None, {}, [extra, extra],
                   [{**extra, "chunk_id": self.review["reviews"][0]["chunk_id"]}],
                   [{**extra, "chunk_id": "chunk:unknown"}], [{**extra, "status": "pass"}],
                   [{**extra, "severity": None}], [{**extra, "notes": ""}]]
        for additional in invalid:
            with self.subTest(additional=additional):
                self.review["additional_findings"] = additional
                with self.assertRaises(ValueError):
                    self.evaluate_review(sample_count=1)

    def test_initial_template_and_pending_review_have_unknown_identity(self):
        unknown = {"kind": None, "name": None, "model": None,
                   "model_reasoning_effort": None, "launch_mode": None}
        self.assertIsNone(self.initial["reviewer"])
        self.assertEqual(self.review["reviewer"], unknown)
        report = self.evaluate_review()
        self.assertEqual(report["reviewer"], unknown)
        self.assertEqual(report["review_gate"], "pending")
        seed = self.root / "seed.json"
        _write_review_seed(seed, self.initial)
        self.assertEqual(json.loads(seed.read_text())["reviewer"], unknown)

    def test_session_attribution_reaches_report_and_legacy_scores_are_preserved(self):
        self.assess()
        self.review['reviewer'] = {'kind': 'agent', 'name': 'Fixture reviewer', 'model': 'gpt-6.1-sol'}
        legacy = self.evaluate_review()
        for mode in ('manual_workflow', 'automated_workflow'):
            self.review['reviewer'].update(model_reasoning_effort='medium', launch_mode=mode)
            report = self.evaluate_review()
            self.assertEqual(report['reviewer'], self.review['reviewer'])
            self.assertEqual(report['review'], legacy['review'])
        for field, value in [('model_reasoning_effort', ''), ('model_reasoning_effort', 3),
                             ('launch_mode', 'standalone'), ('launch_mode', [])]:
            with self.subTest(field=field, value=value):
                original = self.review['reviewer'].copy()
                self.review['reviewer'][field] = value
                with self.assertRaisesRegex(ValueError, 'reviewer'):
                    self.evaluate_review()
                self.review['reviewer'] = original

    def test_human_and_agent_identity_reaches_final_cli_report(self):
        self.assess()
        for kind, model in [("human", None), ("agent", None), ("agent", "test-model")]:
            with self.subTest(kind=kind, model=model):
                reviewer = {"kind": kind, "name": "Test reviewer", "model": model}
                self.review["reviewer"] = reviewer
                report = self.evaluate_review()
                self.assertEqual(report["reviewer"], reviewer)
                self.assertEqual(report["gate"], "pass")
                before = self.review_path.read_bytes()
                output = self.root / "evaluation.json"
                with redirect_stdout(io.StringIO()):
                    code = main(["--chunked-dir", str(self.chunked), "--parsed-dir", str(self.parsed),
                                 "--review-file", str(self.review_path), "--output", str(output)])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output.read_text())["reviewer"], reviewer)
                self.assertEqual(self.review_path.read_bytes(), before)

    def test_legacy_review_keeps_unknown_identity_and_same_scores(self):
        self.assess()
        self.review["reviewer"] = {"kind": "human", "name": "Test reviewer"}
        known = self.evaluate_review()
        self.assertIsNone(known["reviewer"]["model"])
        del self.review["reviewer"]
        legacy = self.evaluate_review()
        self.assertEqual(legacy["reviewer"], {"kind": None, "name": None, "model": None})
        self.assertEqual(legacy["review"], known["review"])
        self.assertEqual(legacy["gate"], known["gate"])
        self.assertNotIn("reviewer", json.loads(self.review_path.read_text()))

    def test_invalid_or_missing_identity_in_new_assessed_review_is_rejected(self):
        self.assess()
        for reviewer in [None, [], "agent", {},
                         {"kind": "agent"}, {"kind": "agent", "name": " "},
                         {"kind": "agent", "name": 1}, {"kind": "robot", "name": "Test"},
                         {"kind": [], "name": "Test"},
                         {"kind": "agent", "name": "Test", "model": 1},
                         {"kind": "agent", "name": "Test", "model": " "}]:
            with self.subTest(reviewer=reviewer):
                self.review["reviewer"] = reviewer
                with self.assertRaisesRegex(ValueError, "reviewer"):
                    self.evaluate_review()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--chunked-dir", str(self.chunked), "--parsed-dir", str(self.parsed),
                  "--review-file", str(self.review_path), "--output", str(self.root / "invalid.json")])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
