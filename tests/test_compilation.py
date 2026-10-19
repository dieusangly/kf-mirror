import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_claim_context import generate_claims, prepare_fixture, fixture_settings

from knowledge_foundry.claim_generation_and_classification import (
    ROOT, CANDIDATE_SCHEMA, FixtureProvider, OpenAIProvider, _candidate_input, _candidate_record,
    _evidence_choices, _generation_coverage, _project_generation, _subset_batch_indices, _telemetry,
    _validate_candidate, canonical, digest,
    classify_claims,
)
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

    def _write_settings(self, directory, provider="fixture", retries=0):
        rate_card = {"short_context_limit_tokens": 272000,
                     "short_context_usd_per_million_tokens": {"input": 2, "cached_input": 0.1,
                                                              "cache_write": 2.5, "output": 10},
                     "long_context_usd_per_million_tokens": {"input": 4, "cached_input": 0.2,
                                                             "cache_write": 5, "output": 15}}
        config = fixture_settings()
        config['pipeline_version'] = 'compilation-test-v1'
        for name in ('claim_generation', 'topic_classification'):
            config['steps'][name].update(provider=provider, retries=retries)
            config['pricing']['providers'][provider]['models'][DEFAULT_MODEL].update(rate_card)
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
            fixture = {"candidate_clusters": {chunk["id"]: {"claims": [{
                "text": "Les lignes directrices sauvegardées identifient l'autorité de contrôle compétente.",
                "evidence": ["E1"]}]}},
                "classifications": {"Les lignes directrices sauvegardées identifient l'autorité de contrôle compétente.":
                                    "topic:beneficial-ownership"}}
            fixture_path = root / "fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated, output = root / "generated", root / "compiled"
            generation_events, classification_events = [], []
            with patch("knowledge_foundry.claim_generation_and_classification.time") as clock:
                clock.monotonic.side_effect = [100, 112.3456]
                generation_report = generate_claims(chunked, generated, config_path, fixture_path,
                                                    on_progress=generation_events.append)
            self.assertEqual(generation_report["duration_seconds"], 12.346)
            saved_report = json.loads((generated / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_report["duration_seconds"], 12.346)
            report = classify_claims(generated, output, config_path, taxonomy_path, fixture_path,
                                     on_progress=classification_events.append)
            self.assertEqual((generation_report["status"], generation_report["candidates"]), ("complete", 1))
            self.assertEqual(generation_report["coverage"]["status"], "no_gaps_detected")
            self.assertEqual(generation_report["coverage"]["summary"]["cited_chunks"], 1)
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
            self.assertTrue((generated / "report.json").is_file())
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
            fixture.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": [{
                "text": "Assertion persistée.",
                "evidence": ["E1"],
            }]}}, "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            with patch("knowledge_foundry.claim_generation_and_classification._write_json", side_effect=RuntimeError("export failure")):
                with self.assertRaisesRegex(RuntimeError, "export failure"):
                    generate_claims(chunked, generated, config_path, fixture)
            self.assertTrue((generated / "generation-checkpoint.json").is_file())
            self.assertTrue((generated / "claim-candidates.jsonl").is_file())
            # Simulate an older interrupted run: telemetry must survive resume.
            saved_path = generated / 'generation-checkpoints/cluster-000001.json'
            saved = json.loads(saved_path.read_text())
            saved['token_counting'] = saved.pop('claim_generation_request_token_counting')
            saved['token_counting']['cache_hits'] = 7
            saved_path.write_text(json.dumps(saved))
            empty_fixture = root / "empty-fixture.json"
            empty_fixture.write_text(json.dumps({"candidate_clusters": {}, "classifications": {
                "Assertion persistée.": None}}), encoding="utf-8")
            report = generate_claims(chunked, generated, config_path, empty_fixture, resume=True)
            self.assertEqual((report["status"], report["candidates"]), ("complete", 1))
            self.assertEqual(report["coverage"]["summary"]["cited_chunks"], 1)
            self.assertEqual(report['claim_generation_request_token_counting']['cache_hits'], 7)
            self.assertNotIn('token_counting', report)
            self.assertFalse((generated / "generation-checkpoint.json").exists())
            self.assertTrue((generated / "build-manifest.json").is_file())
            audit = json.loads((generated / 'evidence-accounting.json').read_text())
            self.assertEqual(audit['clusters'][0]['passages'], saved['evidence_accounting'])
            self.assertEqual(report['evidence_accounting']['unaccounted_passages'], 0)
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
            fixture.write_text(json.dumps({"candidate_clusters": {}, "classifications": {}}), encoding="utf-8")
            config_path, _ = self._write_settings(root)
            generated = root / "generated"
            with patch("knowledge_foundry.claim_generation_and_classification._write_json", side_effect=RuntimeError("export failure")):
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
            fixture.write_text(json.dumps({"candidate_clusters": {}, "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            with patch("knowledge_foundry.claim_generation_and_classification._write_json", side_effect=RuntimeError("export failure")):
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
            fixture.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": []}},
                                           "classifications": {}}), encoding="utf-8")
            config_path, _ = self._write_settings(root)
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_claims.py"),
                 "--context-dir", str(prepare_fixture(chunked, config_path)), "--output-dir", str(root / "generated"),
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
            fixture.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": []}},
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
        legacy = {"provider": "openai", "model": DEFAULT_MODEL, "pricing": {
            "rate_card_version": "test", "models": {DEFAULT_MODEL: {
                "short_context_limit_tokens": 272000,
                "short_context_usd_per_million_tokens": {"input": 2, "cached_input": 0.1,
                                                           "cache_write": 2.5, "output": 10},
                "long_context_usd_per_million_tokens": {"input": 4, "cached_input": 0.2,
                                                          "cache_write": 5, "output": 15},
            }},
        }}
        config = fixture_settings()
        config['steps']['claim_generation']['provider'] = 'openai'
        config['pricing']['version'] = 'test'
        config['pricing']['providers']['openai']['models'][DEFAULT_MODEL].update(legacy['pricing']['models'][DEFAULT_MODEL])
        telemetry = _telemetry(provider, config)
        self.assertEqual(telemetry["usage"], {"input_tokens": 300100, "cached_input_tokens": 20,
                                               "cache_write_tokens": 10, "output_tokens": 15})
        self.assertAlmostEqual(telemetry["estimated_cost_usd"], 1.200367)
        self.assertEqual(telemetry["rate_card"]["version"], "test")

    def test_length_stratified_subset_and_generation_projection_are_deterministic(self):
        self.assertEqual(_subset_batch_indices([10, 100, 10, 100], 2), [1, 3])
        legacy = {"model": DEFAULT_MODEL, "pricing": {"models": {DEFAULT_MODEL: {
            "short_context_limit_tokens": 272000,
            "short_context_usd_per_million_tokens": {"input": 1, "cached_input": 0,
                                                       "cache_write": 0, "output": 1},
            "long_context_usd_per_million_tokens": {"input": 2, "cached_input": 0,
                                                      "cache_write": 0, "output": 2},
        }}}}
        config = fixture_settings()
        config['pricing']['providers']['fixture']['models'][DEFAULT_MODEL].update(legacy['pricing']['models'][DEFAULT_MODEL])
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
            fixture_path.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": []}},
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
            generation_fixture.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": [{
                "text": text, "evidence": ["E1"]}]}},
                "classifications": {}}), encoding="utf-8")
            null_fixture, topic_fixture = root / "null.json", root / "topic.json"
            null_fixture.write_text(json.dumps({"candidate_clusters": {}, "classifications": {text: None}}), encoding="utf-8")
            topic_fixture.write_text(json.dumps({"candidate_clusters": {}, "classifications": {
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
            fixture_path.write_text(json.dumps({"candidate_clusters": {}, "classifications": {}}), encoding="utf-8")
            config_path, taxonomy_path = self._write_settings(root)
            generated = root / "generated"
            generate_claims(chunked, generated, config_path, fixture_path)
            (generated / "claim-candidates.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate claim artifact manifest is invalid"):
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

    def test_choices_show_exact_passages_and_exclude_spanless_context(self):
        bundle = self._input_bundle()
        chunk = copy.deepcopy(bundle["chunks"][0])
        elements = {item["id"]: item for item in bundle["elements"]}
        # A title may be included as context, but cannot be selected without a span.
        context = copy.deepcopy(chunk["evidence"][0])
        context.pop("span")
        chunk["evidence"].insert(0, context)
        chunk["text"] = "Contexte plus large que le passage exact."
        payload = json.loads(_candidate_input([chunk], elements))
        choices = _evidence_choices([chunk], elements)
        self.assertEqual(list(choices), ["E1"])
        ref = chunk["evidence"][1]
        expected = elements[ref["element_id"]]["text"][ref["span"]["start"]:ref["span"]["end"]]
        self.assertNotIn('text', payload['eligible_evidence'][0])
        self.assertEqual(payload['structural_chunks'][0]['text'].count(expected), 1)
        self.assertIn('[C1] Contexte non citable. Voir [E1]', payload['structural_chunks'][0]['text'])
        self.assertIn('[E1]\n' + expected, payload['structural_chunks'][0]['text'])
        self.assertNotIn('[E1] Voir [C1]', payload['structural_chunks'][0]['text'])
        self.assertNotIn(chunk['text'], payload['structural_chunks'][0]['text'])
        record = _candidate_record("build:test", {"text": "Assertion.", "evidence": ["E1"]},
                                   {"method": "manual", "version": "test"}, choices)
        self.assertEqual(record["evidence"], [ref])
        self.assertEqual(record["chunk_ids"], [chunk["id"]])
        with self.assertRaisesRegex(ValueError, "unknown evidence label"):
            _candidate_record("build:test", {"text": "Assertion.", "evidence": ["E2"]},
                              {"method": "manual", "version": "test"}, choices)

    def test_invalid_generation_is_retried_without_leaking_partial_candidates(self):
        self._check_generation_retries(recover=True)

    def test_heading_context_survives_resume_classification_and_validation(self):
        bundle = self._input_bundle()
        heading = copy.deepcopy(bundle["elements"][0])
        heading.update(kind="heading", text="Surveillance des retraits à un DAB",
                       locator_key=heading["locator_key"] + "-heading")
        heading["id"] = "el:" + digest(heading["document_id"] + "\n" + heading["locator_key"])
        heading["revision"] = digest(heading["kind"] + "\n" + heading["text"])
        bundle["elements"].append(heading)
        chunk = bundle["chunks"][0]
        reference = {**chunk["evidence"][0], "element_id": heading["id"],
                     "element_revision": heading["revision"]}
        reference.pop("span")
        chunk["evidence"].insert(0, reference)
        chunk["text"] = heading["text"] + "\n\n" + chunk["text"]
        chunk["id"] = "chunk:" + digest(chunk["document_id"] + "\n" + chunk["chunker_version"] +
                                         "\n" + canonical(chunk["evidence"]) + "\n" + chunk["text"])
        bundle["builds"][0]["output_chunk_ids"] = [chunk["id"]]
        check(bundle)
        original = copy.deepcopy(bundle)
        elements = {item["id"]: item for item in bundle["elements"]}
        payload = json.loads(_candidate_input([chunk], elements))
        self.assertNotIn('text', payload['eligible_evidence'][0])
        self.assertIn('[E1]\n' + heading['text'], payload['structural_chunks'][0]['text'])
        self.assertEqual(payload["eligible_evidence"][0]["kind"], "heading")
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            config, taxonomy = self._write_settings(root)
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": [{
                "text": "Assertion avec contexte sauvegardé.", "evidence": ["E1", "E2"]}]}},
                "classifications": {"Assertion avec contexte sauvegardé.": "topic:beneficial-ownership"}}))
            generated = root / "generated"
            with patch("knowledge_foundry.claim_generation_and_classification._write_json", side_effect=RuntimeError("export failure")):
                with self.assertRaisesRegex(RuntimeError, "export failure"):
                    generate_claims(chunked, generated, config, fixture)
            with patch.object(FixtureProvider, "candidates", side_effect=AssertionError("must reuse checkpoint")):
                report = generate_claims(chunked, generated, config, fixture, resume=True)
            self.assertEqual(report["coverage"]["chunks"][0]["eligible_evidence_count"], 2)
            self.assertEqual(report["coverage"]["summary"]["cited_chunks"], 1)
            classify_claims(generated, root / "classified", config, taxonomy, fixture)
            result = json.loads((root / "classified" / "artifact-bundle.json").read_text())
            check(result)
            self.assertEqual(result["chunks"], original["chunks"])
            self.assertEqual(result["elements"], original["elements"])
            candidate = json.loads((generated / "claim-candidates.jsonl").read_text())
            self.assertEqual(candidate["evidence"][0]["span"], {"start": 0, "end": len(heading["text"])})
            self.assertEqual(result["claims"][0]["evidence"], candidate["evidence"])
            # Only the full span of an already referenced heading is derived.
            candidate["evidence"][0]["span"]["end"] -= 1
            with self.assertRaisesRegex(ValueError, "not supported"):
                _validate_candidate(candidate, candidate["generation_build_id"], {chunk["id"]: chunk}, elements)
            result["claims"][0]["evidence"] = candidate["evidence"]
            with self.assertRaisesRegex(ValueError, "outside supporting chunks"):
                check(result)
        self.assertEqual(bundle, original)

    def test_spanless_non_heading_cannot_be_promoted_to_a_claim_passage(self):
        bundle = self._input_bundle()
        chunk = bundle["chunks"][0]
        ref = chunk["evidence"][0]
        ref.pop("span")
        elements = {item["id"]: item for item in bundle["elements"]}
        self.assertEqual(_evidence_choices([chunk], elements), {})
        fake = {**ref, "span": {"start": 0, "end": len(elements[ref["element_id"]]["text"])}}
        choices = {"E1": {"chunk_id": chunk["id"], "reference": fake,
                          "text": elements[ref["element_id"]]["text"]}}
        candidate = _candidate_record("build:test", {"text": "Assertion.", "evidence": ["E1"]},
                                      {"method": "manual", "version": "test"}, choices)
        with self.assertRaisesRegex(ValueError, "not supported"):
            _validate_candidate(candidate, "build:test", {chunk["id"]: chunk}, elements)

    def test_exhausted_generation_retries_export_only_a_partial_build(self):
        self._check_generation_retries(recover=False)

    def _check_generation_retries(self, recover):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            (chunked / "artifact-bundle.json").write_text(json.dumps(self._input_bundle()), encoding="utf-8")
            config, _ = self._write_settings(root, retries=1)
            good = {"text": "Assertion complète et conditionnelle.", "evidence": ["E1"]}
            bad = {"text": "Référence invalide.", "evidence": ["E999"]}
            prompts = []

            class ScriptedProvider(FixtureProvider):
                def candidates(self, *_args):
                    prompts.append(_args[1])
                    self.usage_records.append({"input_tokens": 10, "cached_input_tokens": 0,
                                               "cache_write_tokens": 0, "output_tokens": 5})
                    return {"claims": [good] if recover and len(self.usage_records) == 2 else [good, bad]}

            provider = ScriptedProvider({})
            with patch("knowledge_foundry.claim_generation_and_classification._provider", return_value=provider), \
                    patch("knowledge_foundry.claim_generation_and_classification.time.sleep"):
                report = generate_claims(chunked, root / "generated", config)
            self.assertEqual(report["telemetry"]["requests"], 2)
            self.assertEqual(report["telemetry"]["usage"]["input_tokens"], 20)
            self.assertGreater(report["telemetry"]["estimated_cost_usd"], 0)
            self.assertEqual(report["candidates"], 1 if recover else 0)
            self.assertEqual(report["status"], "complete" if recover else "partial")
            self.assertEqual(len(report["errors"]), 0 if recover else 1)
            self.assertEqual(report["coverage"]["summary"]["failed_chunks"], 0 if recover else 1)
            self.assertIn("unknown evidence label", prompts[1])
            self.assertIn("Corrigez la réponse complète", prompts[1])
            manifest = json.loads((root / "generated/build-manifest.json").read_text())
            self.assertIn("report.json", [item["path"] for item in manifest["outputs"]])

    def test_classification_receives_passages_versions_and_retries_invalid_topics(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            chunk = bundle["chunks"][0]
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            config, taxonomy = self._write_settings(root, retries=1)
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_clusters": {chunk["id"]: {"claims": [{
                "text": "Assertion conservée.", "evidence": ["E1"]}]}}, "classifications": {}}))
            generated = root / "generated"
            generate_claims(chunked, generated, config, fixture)
            responses = [{"topic_id": "topic:unknown"}, {"unexpected": True},
                         {"topic_id": "topic:beneficial-ownership"}]
            contexts = []

            def classify(context, *_args):
                contexts.append(context)
                return responses.pop(0)

            settings = json.loads(config.read_text())
            settings["steps"]["topic_classification"]["retries"] = 2
            config.write_text(json.dumps(settings))
            with patch.object(FixtureProvider, "classify", side_effect=classify), \
                    patch("knowledge_foundry.claim_generation_and_classification.time.sleep"):
                report = classify_claims(generated, root / "classified", config, taxonomy, fixture)
            self.assertEqual(len(contexts), 3)
            self.assertEqual((report["status"], report["claims"], report["errors"]), ("complete", 1, []))
            self.assertEqual(report["unclassified_candidate_ids"], [])
            context = contexts[0]
            self.assertEqual(context["claim"], "Assertion conservée.")
            self.assertEqual(context["sources"], bundle["sources"])
            ref = chunk["evidence"][0]
            element = next(item for item in bundle["elements"] if item["id"] == ref["element_id"])
            self.assertEqual(context["evidence"][0]["text"],
                             element["text"][ref["span"]["start"]:ref["span"]["end"]])
            self.assertEqual(context["evidence"][0]["location"], element["location"])
            self.assertEqual(context["chunks"][0]["text"], chunk["text"])
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                    patch("knowledge_foundry.claim_generation_and_classification.OpenAIResponsesTool.json_response",
                          return_value={"topic_id": None}) as response:
                provider = OpenAIProvider(DEFAULT_MODEL, "medium")
                provider.classify(context, json.loads(taxonomy.read_text()), "test", {"type": "object"})
            sent = json.loads(response.call_args.kwargs["input_text"])
            self.assertEqual(sent["evidence"], context["evidence"])
            self.assertEqual(sent["sources"], context["sources"])

    def test_generation_schema_matches_documented_model_contract(self):
        schema = json.loads((ROOT / "schemas/compilation-model-output-v2.json").read_text())
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        response = {"claims": [{"text": "Assertion.", "evidence": ["E1", "E2"]}], 'coverage': [
            {'evidence_id': label, 'status': 'represented', 'claim_indices': [0],
             'reason': 'The complete proposition and qualification are represented.'} for label in ['E1', 'E2']]}
        Draft202012Validator(schema).validate(response)
        Draft202012Validator(CANDIDATE_SCHEMA).validate(response)

    def test_explicit_batch_selection_validates_numbers_and_order(self):
        self.assertEqual(_subset_batch_indices([10, 20, 30], None, [3, 1]), [0, 2])
        for numbers in [[], [0], [4], [1, 1], [True], [1.5], "1"]:
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                _subset_batch_indices([10, 20, 30], None, numbers)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            _subset_batch_indices([10, 20, 30], 1, [1])

    def test_targeted_run_records_scope_and_omits_biased_full_cost_projection(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            chunked = root / "chunked"
            chunked.mkdir()
            bundle = self._input_bundle()
            (chunked / "artifact-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
            config, _ = self._write_settings(root)
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"candidate_clusters": {}}))
            report = generate_claims(chunked, root / "targeted", config, fixture, batch_numbers=[1])
            scope = report["compilation_scope"]
            self.assertEqual((scope["mode"], scope["selection"], scope["selected_cluster_numbers"]),
                             ("subset", "explicit", [1]))
            self.assertEqual(scope["selected_chunk_ids"], [bundle["chunks"][0]["id"]])
            self.assertEqual(json.loads((root / "targeted/artifact-bundle.json").read_text())["chunks"],
                             bundle["chunks"])
            self.assertIsNone(report["projected_full_generation"])
            self.assertEqual((report["status"], report["coverage"]["status"]), ("complete", "needs_review"))
            self.assertEqual(report["coverage"]["selected_chunk_ids_without_candidates"],
                             [bundle["chunks"][0]["id"]])
            records = [{"input_tokens": 100, "cached_input_tokens": 0,
                        "cache_write_tokens": 0, "output_tokens": 5}]
            self.assertIsNone(_project_generation(scope, records, json.loads(config.read_text())))

    def _coverage_fixture(self):
        # Coverage uses validated saved records; these small records isolate accounting.
        refs = {name: {"element_id": f"el:{name}", "span": {"start": 0, "end": 10}}
                for name in ["a1", "a2", "b", "c"]}
        bundle = {
            "sources": [{"id": f"src:{name}", "version": "v1", "title": name} for name in "abc"],
            "documents": [{"id": f"doc:{name}", "source_id": f"src:{name}", "source_version": "v1"}
                          for name in "abc"],
            "chunks": [{"id": f"chunk:{name}", "document_id": f"doc:{name[0]}", "evidence": [ref]}
                       for name, ref in refs.items()] + [
                           {"id": "chunk:heading", "document_id": "doc:a", "evidence": [{"element_id": "el:h"}]}],
        }
        return bundle, refs

    def test_coverage_flags_uncited_selected_material_without_counting_context(self):
        bundle, refs = self._coverage_fixture()
        scope = {"selected_chunk_ids": ["chunk:a1", "chunk:a2", "chunk:b", "chunk:heading"]}
        candidate = {"id": "candidate:1", "chunk_ids": ["chunk:a1", "chunk:a2"], "evidence": [refs["a1"]]}
        report = _generation_coverage(bundle, [candidate], scope, [])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["summary"], {
            "selected_chunks": 4, "cited_chunks": 1, "chunks_without_candidates": 3,
            "failed_chunks": 0, "chunks_without_eligible_evidence": 1, "not_selected_chunks": 1,
            "selected_sources": 2, "sources_with_candidates": 1, "sources_without_candidates": 1,
            "not_selected_sources": 1,
        })
        self.assertEqual(report["selected_source_ids_without_candidates"], ["src:b"])
        rows = {row["chunk_id"]: row for row in report["chunks"]}
        self.assertEqual(rows["chunk:a2"]["status"], "no_candidates")
        self.assertEqual(rows["chunk:heading"]["status"], "no_eligible_evidence")
        self.assertNotIn("chunk:c", rows)
        self.assertEqual(report["sources"][-1]["status"], "not_selected")

    def test_coverage_counts_cross_source_candidates_once_per_source(self):
        bundle, refs = self._coverage_fixture()
        scope = {"selected_chunk_ids": ["chunk:a1", "chunk:a2", "chunk:b"]}
        candidate = {"id": "candidate:1", "chunk_ids": scope["selected_chunk_ids"],
                     "evidence": [refs["a1"], refs["a2"], refs["b"]]}
        report = _generation_coverage(bundle, [candidate], scope, [])
        self.assertEqual(report["status"], "no_gaps_detected")
        self.assertEqual(report["summary"]["cited_chunks"], 3)
        self.assertEqual([row["candidate_count"] for row in report["sources"]], [1, 1, 0])
        self.assertEqual(report["selected_chunk_ids_without_candidates"], [])

    def test_coverage_distinguishes_failed_batches_from_empty_successful_batches(self):
        bundle, _ = self._coverage_fixture()
        scope = {"selected_chunk_ids": ["chunk:a1", "chunk:b"]}
        errors = [{"stage": "generation", "chunk_ids": ["chunk:b"], "error": "invalid response"}]
        report = _generation_coverage(bundle, [], scope, errors)
        self.assertEqual(report["summary"]["failed_chunks"], 1)
        self.assertEqual([row["status"] for row in report["chunks"]], ["no_candidates", "generation_failed"])
        self.assertEqual(report["status"], "needs_review")

    def test_coverage_does_not_flag_unselected_failures_as_selected_gaps(self):
        bundle, refs = self._coverage_fixture()
        scope = {"selected_chunk_ids": ["chunk:a1"]}
        candidate = {"id": "candidate:1", "chunk_ids": ["chunk:a1"], "evidence": [refs["a1"]]}
        errors = [{"stage": "generation", "chunk_ids": ["chunk:c"], "error": "outside subset"}]
        report = _generation_coverage(bundle, [candidate], scope, errors)
        self.assertEqual(report["status"], "no_gaps_detected")
        self.assertEqual(report["summary"]["failed_chunks"], 0)
