import copy
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_foundry.compilation import ROOT, compile_bundle
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
        config = {"pipeline_version": "compilation-test-v1", "batch_size": 4, "retries": 0,
                  "provider": provider, "model": DEFAULT_MODEL, "reasoning_effort": "medium",
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
            report = compile_bundle(chunked, output, config_path, taxonomy_path, fixture_path)
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
