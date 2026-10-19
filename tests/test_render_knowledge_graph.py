import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_knowledge_graph import build_graph, load_claim_candidates, main, render_html


class RenderKnowledgeGraphTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads(Path("examples/artifact-bundle-pdf.json").read_text(encoding="utf-8"))

    def test_build_graph_preserves_provenance_path_and_evidence(self):
        graph = build_graph(self.bundle)
        self.assertEqual([node["group"] for node in graph["nodes"]],
                         ["source", "document", "element", "chunk", "claim", "topic"])
        document_node = next(node for node in graph["nodes"] if node["group"] == "document")
        source_record = next(source for source in self.bundle["sources"]
                             if source["id"] == document_node["record"]["source_id"])
        self.assertEqual(document_node["label"], source_record["title"])
        edges = {(edge["source"], edge["target"], edge["relation"], edge["evidence"])
                 for edge in graph["edges"]}
        source, document, element, chunk, claim, topic = [
            self.bundle[name][0]["id"] for name in ("sources", "documents", "elements", "chunks", "claims", "topics")]
        self.assertIn((topic, claim, "groups", False), edges)
        self.assertIn((claim, chunk, "uses", False), edges)
        self.assertIn((chunk, element, "supported by", True), edges)
        self.assertIn((element, document, "located in", False), edges)
        self.assertIn((document, source, "saved from", False), edges)

    def test_rendered_html_escapes_data_before_embedding_in_script(self):
        graph = build_graph(self.bundle)
        graph["nodes"][0]["record"]["title"] = "</script><script>alert(1)</script>"
        rendered = render_html(graph)
        self.assertNotIn("</script><script>alert(1)</script>", rendered)
        self.assertIn("\\u003c/script", rendered)
        self.assertIn('id="topic-filter"', rendered)

    def test_viewer_is_deterministic_and_defaults_to_paged_topic_claim_view(self):
        graph = build_graph(self.bundle)
        first = render_html(graph, "KPG <review>")
        second = render_html(graph, "KPG <review>")
        self.assertEqual(first, second)
        self.assertIn("Topics and claims", first)
        self.assertIn('value="topic"', first)
        self.assertIn("pageSize = 24", first)
        self.assertIn('id="document-filter"', first)
        self.assertIn('id="build-filter"', first)
        self.assertIn('id="review-filter"', first)
        self.assertIn('id="type-filter"', first)
        self.assertIn('setAttribute("aria-label", "Go to page")', first)
        self.assertIn('pageInput.type = "number"', first)
        self.assertIn("Explore provenance", first)
        self.assertIn("Explore local graph", first)
        self.assertIn("Document-derived knowledge", first)
        self.assertIn("Full graph / debug", first)
        self.assertIn("&lt;review&gt;", first)

    def test_cli_writes_new_html(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "graph.html"
            self.assertEqual(main(["--bundle", "examples/artifact-bundle-pdf.json", "--output", str(output)]), 0)
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("Knowledge provenance graph", rendered)
        self.assertIn("topic:guidelines-status", rendered)

    def test_claim_candidates_are_linked_to_chunks_and_evidence(self):
        chunk = self.bundle["chunks"][0]
        candidate = {
            "id": "candidate:inspection-example", "generation_build_id": "build:generation-example",
            "chunk_ids": [chunk["id"]], "text": "A generated claim awaiting classification.",
            "evidence": chunk["evidence"], "grounding_review_status": "draft",
            "generation": {"method": "manual", "version": "test-v1"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            candidate_path = Path(temporary) / "claim-candidates.jsonl"
            candidate_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            candidates = load_claim_candidates(candidate_path)
            output = Path(temporary) / "graph.html"
            self.assertEqual(main(["--bundle", "examples/artifact-bundle-pdf.json", "--claim-candidates",
                                   str(candidate_path), "--output", str(output)]), 0)
            rendered = output.read_text(encoding="utf-8")
        graph = build_graph(self.bundle, candidates)
        candidate_node = next(node for node in graph["nodes"] if node["id"] == candidate["id"])
        self.assertEqual(candidate_node["group"], "candidate")
        self.assertEqual(candidate_node["meta"], "candidate claim · status unavailable")
        edges = {(edge["source"], edge["target"], edge["evidence"]) for edge in graph["edges"]}
        self.assertIn((candidate["id"], chunk["id"], False), edges)
        self.assertIn((candidate["id"], chunk["evidence"][0]["element_id"], True), edges)
        self.assertIn("Candidate claims", rendered)

