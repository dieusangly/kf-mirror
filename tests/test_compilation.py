import copy
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_foundry.compilation import ROOT, _project_full_build, _sample_batch_indices, _telemetry, compile_bundle
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

    def test_fixture_compilation_exports_cross_build_lineage_and_draft_views(self):
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
            output = root / "compiled"
            events = []
            report = compile_bundle(chunked, output, config_path, taxonomy_path, fixture_path,
                                    on_progress=events.append)
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
            self.assertEqual([event["phase"] for event in events],
                             ["generation", "generation", "generation", "classification", "classification",
                              "export", "complete"])
            self.assertEqual(events[-1]["telemetry"]["estimated_cost_usd"], 0)

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

    def test_length_stratified_sampling_and_projection_are_deterministic(self):
        self.assertEqual(_sample_batch_indices([10, 100, 10, 100], 2), [1, 3])
        config = {"model": DEFAULT_MODEL, "pricing": {"models": {DEFAULT_MODEL: {
            "short_context_limit_tokens": 272000,
            "short_context_usd_per_million_tokens": {"input": 1, "cached_input": 0,
                                                       "cache_write": 0, "output": 1},
            "long_context_usd_per_million_tokens": {"input": 2, "cached_input": 0,
                                                      "cache_write": 0, "output": 2},
        }}}}
        scope = {"mode": "sample", "total_chunks": 8, "selected_chunks": 2,
                 "generation_request_characters": {"total": 400, "selected": 100}}
        records = [{"input_tokens": 100, "cached_input_tokens": 0,
                    "cache_write_tokens": 0, "output_tokens": 10}]
        projection = _project_full_build(scope, [{"text": "a"}, {"text": "b"}], records, records, config)
        self.assertAlmostEqual(projection["generation_estimated_cost_usd"], 0.00044)
        self.assertAlmostEqual(projection["classification_estimated_cost_usd"], 0.00044)
        self.assertAlmostEqual(projection["estimated_cost_usd"], 0.00088)

    def test_sample_build_records_selection_and_has_distinct_lineage(self):
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
            full = compile_bundle(chunked, root / "full", config_path, taxonomy_path, fixture_path)
            sample = compile_bundle(chunked, root / "sample", config_path, taxonomy_path, fixture_path,
                                    sample_batches=1)
            self.assertNotEqual(full["build_id"], sample["build_id"])
            self.assertEqual(sample["compilation_scope"]["mode"], "sample")
            self.assertEqual(sample["compilation_scope"]["selected_chunk_ids"], [chunk["id"]])

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
