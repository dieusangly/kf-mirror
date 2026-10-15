"""Evaluation references score saved outputs and never supply pipeline assignments."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_foundry.claim_compilation import ROOT, classify_claims, file_digest, generate_claims
from scripts.evaluate_classification import compare, evaluate


class ClassificationEvaluationTests(unittest.TestCase):
    def reference(self):
        return {"topic_ids": ["topic:a", "topic:b"], "cases": [
            {"candidate_id": "candidate:1", "text": "One", "acceptable_topic_ids": ["topic:a"],
             "critical": True, "reason": "Operational purpose"},
            {"candidate_id": "candidate:2", "text": "Two", "acceptable_topic_ids": [None],
             "critical": False, "reason": "Narrative"},
            {"candidate_id": "candidate:3", "text": "Three", "acceptable_topic_ids": ["topic:a", "topic:b"],
             "critical": False, "reason": "Explicitly mixed purpose"}]}

    def test_scores_wrong_topics_nulls_and_errors_separately(self):
        r = compare(self.reference(), {"candidate:1": "topic:b", "candidate:2": "classification_error",
                                       "candidate:3": "topic:a"})
        self.assertEqual((r["correct"], r["critical_correct"], r["unclassified"]), (1, 0, 0))
        self.assertEqual(r["ambiguous_reference_cases"], 1)
        r = compare(self.reference(), {"candidate:1": "topic:a", "candidate:2": None, "candidate:3": "topic:b"})
        self.assertEqual((r["agreement"], r["critical_correct"], r["unclassified"]), (1, 1, 1))

    def test_rejects_missing_extra_duplicate_and_unknown_labels(self):
        actual = {"candidate:1": "topic:a", "candidate:2": None, "candidate:3": "topic:b"}
        for change in [lambda x: x.pop("candidate:2"), lambda x: x.update({"candidate:4": None}),
                       lambda x: x.update({"candidate:1": "topic:unknown"})]:
            invalid = copy.deepcopy(actual)
            change(invalid)
            with self.assertRaises(ValueError):
                compare(self.reference(), invalid)
        reference = self.reference()
        reference["cases"].append(copy.deepcopy(reference["cases"][0]))
        with self.assertRaises(ValueError):
            compare(reference, actual)

    def test_evaluates_saved_fixture_run_without_using_reference_to_classify(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            bundle = json.loads((ROOT / "examples/artifact-bundle-pdf.json").read_text())
            bundle["claims"], bundle["topics"] = [], []
            bundle["builds"][0].update(output_claim_ids=[], output_topic_ids=[])
            chunked = root / "chunked"
            chunked.mkdir()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle))
            config = json.loads((ROOT / "config/compilation.json").read_text())
            config["provider"] = "fixture"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))
            fixture = root / "fixture.json"
            text = "La source décrit une règle."
            fixture.write_text(json.dumps({"candidate_batches": {bundle["chunks"][0]["id"]: {
                "claims": [{"text": text, "evidence": ["E1"]}]}},
                "classifications": {text: "topic:beneficial-ownership"}}))
            generated, classified = root / "generated", root / "classified"
            generate_claims(chunked, generated, config_path, fixture)
            report = classify_claims(generated, classified, config_path, fixture_path=fixture)
            self.assertGreaterEqual(report["duration_seconds"], 0)
            candidate = json.loads((generated / "claim-candidates.jsonl").read_text())
            reference = {"reference_version": 1, "purpose": "evaluation_only", "review_status": "draft",
                         "assessed_by": "test", "limitation": "Fixture, not legal verification",
                         "candidate_sha256": file_digest(generated / "claim-candidates.jsonl"),
                         "taxonomy_sha256": file_digest(ROOT / "config/topics-v1.json"),
                         "topic_ids": ["topic:beneficial-ownership", "topic:ongoing-vigilance"],
                         "criteria": {"minimum_agreement": .95, "maximum_stage_errors": 0},
                         "cases": [{"candidate_id": candidate["id"], "text": text,
                                    "acceptable_topic_ids": ["topic:beneficial-ownership"],
                                    "critical": True, "reason": "Test expectation"}]}
            reference_path = root / "reference.json"
            reference_path.write_text(json.dumps(reference))
            bundle_hash = file_digest(classified / "artifact-bundle.json")
            result = evaluate(generated, classified, reference_path)
            self.assertEqual(result["agreement"], 1)
            self.assertFalse(result["checks"]["live_model_output"])
            self.assertEqual(result["gate"], "fail")  # Fixtures cannot establish model quality.
            reference["cases"][0]["acceptable_topic_ids"] = ["topic:ongoing-vigilance"]
            reference_path.write_text(json.dumps(reference))
            result = evaluate(generated, classified, reference_path)
            self.assertEqual((result["gate"], result["critical_correct"]), ("fail", 0))
            self.assertEqual(file_digest(classified / "artifact-bundle.json"), bundle_hash)
            reference["candidate_sha256"] = "0" * 64
            reference_path.write_text(json.dumps(reference))
            with self.assertRaisesRegex(ValueError, "exact saved candidate"):
                evaluate(generated, classified, reference_path)

    def test_reference_is_complete_and_marks_known_boundary_cases_critical(self):
        reference = json.loads((ROOT / "tests/fixtures/classification_reference_v6.json").read_text())
        self.assertEqual(len(reference["cases"]), 84)
        critical = {case["candidate_id"] for case in reference["cases"] if case["critical"]}
        for prefix in ["candidate:0ff3b0", "candidate:630e5d", "candidate:f29398", "candidate:e9aef9"]:
            self.assertTrue(any(candidate.startswith(prefix) for candidate in critical))
        actual = {case["candidate_id"]: case["acceptable_topic_ids"][0] for case in reference["cases"]}
        self.assertEqual(compare(reference, actual)["agreement"], 1)
