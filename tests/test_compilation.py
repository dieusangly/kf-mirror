import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_foundry.claim_compilation import ROOT, _project_generation, _subset_batch_indices, _telemetry, \
    classify_claims, generate_claims
from knowledge_foundry.openai_tools import DEFAULT_MODEL
from scripts.validate_artifact_examples import check


class CompilationTests(unittest.TestCase):
    def _input_bundle(self):
        bundle = json.loads((ROOT / "examples" / "artifact-bundle-pdf.json").read_text(encoding="utf-8"))
        bundle["claims"] = []
        bundle["topics"] = []
        bundle["builds"][0]["output_claim_ids"] = []
        bundle["builds"][0]["output_topic_ids"] = []
        check(bundle)
        return bundle

    def _write_settings(self, directory, provider="fixture"):
        rate_card = {"short_context_limit_tokens": 272000,
                     "short_context_usd_per_million_tokens": {"input": 2, "cached_input": 0.1,
                                                              "cache_write": 2.5, "output": 10},
                     "long_context_usd_per_million_tokens": {"input": 4, "cached_input": 0.2,
                                                             "cache_write": 5, "output": 15}}
        config = {"pipeline_version": "compilation-test-v1", "batch_size_chunks": 4, "retries": 0,
                  "provider": provider, "model": DEFAULT_MODEL, "reasoning_effort": "medium",
                  "pricing": {"rate_card_version": "test", "models": {DEFAULT_MODEL: rate_card}},
                  "generation_version": "claim-generation-test-v1",
                  "classification_version": "topic-classification-test-v1"}
        taxonomy = {"taxonomy_version": "taxonomy-test-v1", "topics": [
            {"id": "topic:beneficial-ownership", "title": "Bénéficiaires effectifs", "scope": "Propriétaires."}]}
        config_path, taxonomy_path = directory / "config.json", directory / "topics.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        taxonomy_path.write_text(json.dumps(taxonomy), encoding="utf-8")
        return config_path, taxonomy_path

    def test_fixture_generation_then_classification_exports_draft_views(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            chunk = bundle["chunks"][0]
            fixture = {"candidate_batches": {chunk["id"]: {"claims": [{
                "text": "Les lignes directrices sauvegardées identifient l'autorité de contrôle compétente.",
                "evidence": [{"chunk_id": chunk["id"], "evidence_index": 0}]}]}},
                "classifications": {"Les lignes directrices sauvegardées identifient l'autorité de contrôle compétente.":
                                    "topic:beneficial-ownership"}}
            fixture_path = root / "fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated, output = root / "generated", root / "compiled"
            generation_events, classification_events = [], []
            generation_report = generate_claims(chunked, generated, config_path, fixture_path,
                                                on_progress=generation_events.append)
            report = classify_claims(generated, output, config_path, taxonomy_path, fixture_path,
                                     on_progress=classification_events.append)
            self.assertEqual((generation_report["status"], generation_report["candidates"]), ("complete", 1))
            self.assertEqual((report["status"], report["claims"], report["topics"]), ("complete", 1, 1))
            result = json.loads((output / "artifact-bundle.json").read_text(encoding="utf-8"))
            check(result)
            claim, topic = result["claims"][0], result["topics"][0]
            self.assertNotEqual(claim["build_id"], chunk["build_id"])
            self.assertEqual((claim["grounding_review_status"], claim["classification_review_status"], topic["review_status"]),
                             ("draft", "draft", "draft"))
            self.assertTrue((output / "chunks.jsonl").is_file())
            self.assertTrue((output / "claims.jsonl").is_file())
            self.assertTrue((output / "topics" / "beneficial-ownership.json").is_file())
            self.assertTrue((output / "topics" / "beneficial-ownership.md").is_file())
            self.assertTrue((output / "build-manifest.json").is_file())
            self.assertTrue((generated / "claim-candidates.jsonl").is_file())
            self.assertEqual([event["phase"] for event in generation_events],
                             ["generation", "generation", "export", "complete"])
            self.assertEqual([event["phase"] for event in classification_events],
                             ["classification", "classification", "export", "complete"])
            self.assertEqual(classification_events[-1]["telemetry"]["estimated_cost_usd"], 0)

    def test_resume_finalizes_saved_batch_without_repeating_generation(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            chunk = bundle["chunks"][0]
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_batches": {chunk["id"]: {"claims": [{
                "text": "Assertion persistée.",
                "evidence": [{"chunk_id": chunk["id"], "evidence_index": 0}],
            }]}}, "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            with patch("scripts.validate_artifact_examples.check", side_effect=RuntimeError("export failure")):
                with self.assertRaisesRegex(RuntimeError, "export failure"):
                    generate_claims(chunked, generated, config_path, fixture)
            self.assertTrue((generated / "generation-checkpoint.json").is_file())
            self.assertTrue((generated / "claim-candidates.jsonl").is_file())
            empty_fixture = root / "empty-fixture.json"
            empty_fixture.write_text(json.dumps({"candidate_batches": {}, "classifications": {
                "Assertion persistée.": None}}), encoding="utf-8")
            report = generate_claims(chunked, generated, config_path, empty_fixture, resume=True)
            self.assertEqual((report["status"], report["candidates"]), ("complete", 1))
            self.assertFalse((generated / "generation-checkpoint.json").exists())
            self.assertTrue((generated / "build-manifest.json").is_file())
            classified = classify_claims(generated, root / "classified", config_path, taxonomy_path, empty_fixture)
            self.assertEqual(classified["status"], "complete")

    def test_resume_rejects_a_different_generation_scope(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_batches": {}, "classifications": {}}), encoding="utf-8")
            config_path, _ = self._write_settings(root)
            generated = root / "generated"
            with patch("scripts.validate_artifact_examples.check", side_effect=RuntimeError("export failure")):
                with self.assertRaisesRegex(RuntimeError, "export failure"):
                    generate_claims(chunked, generated, config_path, fixture)
            with self.assertRaisesRegex(ValueError, "checkpoint does not match"):
                generate_claims(chunked, generated, config_path, fixture, subset_batches=1, resume=True)

    def test_classification_rejects_an_incomplete_generation_directory(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_batches": {}, "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            with patch("scripts.validate_artifact_examples.check", side_effect=RuntimeError("export failure")):
                with self.assertRaisesRegex(RuntimeError, "export failure"):
                    generate_claims(chunked, generated, config_path, fixture)
            with self.assertRaisesRegex(ValueError, "generation is incomplete"):
                classify_claims(generated, root / "classified", config_path, taxonomy_path, fixture)

    def test_direct_generation_cli_can_import_the_validator(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            chunk = bundle["chunks"][0]
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_batches": {chunk["id"]: {"claims": []}},
                                           "classifications": {}}), encoding="utf-8")
            config_path, _ = self._write_settings(root)
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_claims.py"),
                 "--chunked-dir", str(chunked), "--output-dir", str(root / "generated"),
                 "--config", str(config_path), "--fixture", str(fixture)],
                cwd=root, env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "generated" / "build-manifest.json").is_file())

    def test_direct_classification_cli_can_import_the_validator(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            chunk = bundle["chunks"][0]
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_batches": {chunk["id"]: {"claims": []}},
                                           "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            generate_claims(chunked, generated, config_path, fixture)
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "classify_claims.py"),
                 "--candidates-dir", str(generated), "--output-dir", str(root / "classified"),
                 "--config", str(config_path), "--taxonomy", str(taxonomy_path), "--fixture", str(fixture)],
                cwd=root, env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "classified" / "build-manifest.json").is_file())

    def test_telemetry_uses_cached_and_long_context_pricing(self):
        provider = type("Provider", (), {"model_id": DEFAULT_MODEL, "usage_records": [
            {"input_tokens": 100, "cached_input_tokens": 20, "cache_write_tokens": 10, "output_tokens": 5},
            {"input_tokens": 300000, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 10},
        ]})()
        config = {"provider": "openai", "model": DEFAULT_MODEL, "pricing": {
            "rate_card_version": "test", "models": {DEFAULT_MODEL: {
                "short_context_limit_tokens": 272000,
                "short_context_usd_per_million_tokens": {"input": 2, "cached_input": 0.1,
                                                           "cache_write": 2.5, "output": 10},
                "long_context_usd_per_million_tokens": {"input": 4, "cached_input": 0.2,
                                                          "cache_write": 5, "output": 15},
            }},
        }}
        telemetry = _telemetry(provider, config)
        self.assertEqual(telemetry["usage"], {"input_tokens": 300100, "cached_input_tokens": 20,
                                               "cache_write_tokens": 10, "output_tokens": 15})
        self.assertAlmostEqual(telemetry["estimated_cost_usd"], 1.200367)
        self.assertEqual(telemetry["rate_card"]["version"], "test")

    def test_length_stratified_subset_and_generation_projection_are_deterministic(self):
        self.assertEqual(_subset_batch_indices([10, 100, 10, 100], 2), [1, 3])
        config = {"model": DEFAULT_MODEL, "pricing": {"models": {DEFAULT_MODEL: {
            "short_context_limit_tokens": 272000,
            "short_context_usd_per_million_tokens": {"input": 1, "cached_input": 0,
                                                       "cache_write": 0, "output": 1},
            "long_context_usd_per_million_tokens": {"input": 2, "cached_input": 0,
                                                      "cache_write": 0, "output": 2},
        }}}}
        scope = {"mode": "subset", "total_chunks": 8, "selected_chunks": 2,
                 "generation_request_characters": {"total": 400, "selected": 100}}
        records = [{"input_tokens": 100, "cached_input_tokens": 0,
                    "cache_write_tokens": 0, "output_tokens": 10}]
        projection = _project_generation(scope, records, config)
        self.assertAlmostEqual(projection["estimated_cost_usd"], 0.00044)

    def test_subset_build_records_selection_and_has_distinct_lineage(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            chunk = bundle["chunks"][0]
            fixture_path = root / "fixture.json"
            fixture_path.write_text(json.dumps({"candidate_batches": {chunk["id"]: {"claims": []}},
                                                "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            full = generate_claims(chunked, root / "full", config_path, fixture_path)
            subset = generate_claims(chunked, root / "subset", config_path, fixture_path, subset_batches=1)
            self.assertNotEqual(full["generation_build_id"], subset["generation_build_id"])
            self.assertEqual(subset["compilation_scope"]["mode"], "subset")
            self.assertEqual(subset["compilation_scope"]["selected_chunk_ids"], [chunk["id"]])

    def test_saved_candidates_can_be_reclassified_without_generation(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            chunk = bundle["chunks"][0]
            text = "Les lignes directrices sauvegardées identifient l'autorité de contrôle compétente."
            generation_fixture = root / "generation.json"
            generation_fixture.write_text(json.dumps({"candidate_batches": {chunk["id"]: {"claims": [{
                "text": text, "evidence": [{"chunk_id": chunk["id"], "evidence_index": 0}]}]}},
                "classifications": {}}), encoding="utf-8")
            null_fixture, topic_fixture = root / "null.json", root / "topic.json"
            null_fixture.write_text(json.dumps({"candidate_batches": {}, "classifications": {text: None}}), encoding="utf-8")
            topic_fixture.write_text(json.dumps({"candidate_batches": {}, "classifications": {
                text: "topic:beneficial-ownership"}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            generate_claims(chunked, generated, config_path, generation_fixture)
            unclassified = classify_claims(generated, root / "unclassified", config_path, taxonomy_path, null_fixture)
            reclassified = classify_claims(generated, root / "reclassified", config_path, taxonomy_path, topic_fixture)
            self.assertEqual(len(unclassified["unclassified_candidate_ids"]), 1)
            self.assertEqual((reclassified["claims"], reclassified["topics"]), (1, 1))
            self.assertEqual(unclassified["upstream_generation_build_id"], reclassified["upstream_generation_build_id"])

    def test_classification_rejects_a_tampered_candidate_artifact(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            fixture_path = root / "fixture.json"
            fixture_path.write_text(json.dumps({"candidate_batches": {}, "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            generate_claims(chunked, generated, config_path, fixture_path)
            (generated / "claim-candidates.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate artifact manifest is invalid"):
                classify_claims(generated, root / "compiled", config_path, taxonomy_path, fixture_path)

    def test_compiled_claim_requires_exact_span(self):
        bundle = self._input_bundle()
        claim = copy.deepcopy(json.loads((ROOT / "examples" / "artifact-bundle-pdf.json").read_text(encoding="utf-8"))["claims"][0])
        claim["build_id"] = bundle["builds"][0]["id"]
        claim["topic_id"] = "topic:beneficial-ownership"
        claim["evidence"][0].pop("span")
        bundle["claims"] = [claim]
        topic = copy.deepcopy(json.loads((ROOT / "examples" / "artifact-bundle-pdf.json").read_text(encoding="utf-8"))["topics"][0])
        topic["id"] = "topic:beneficial-ownership"
        topic["claim_ids"] = [claim["id"]]
        bundle["topics"] = [topic]
        bundle["builds"][0]["output_claim_ids"] = [claim["id"]]
        bundle["builds"][0]["output_topic_ids"] = [topic["id"]]
        with self.assertRaisesRegex(ValueError, "claim evidence requires an exact span"):
            check(bundle)
