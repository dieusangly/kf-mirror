import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_knowledge_graph import build_graph, main, render_html


class RenderKnowledgeGraphTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads(Path("examples/artifact-bundle-pdf.json").read_text(encoding="utf-8"))

    def test_build_graph_preserves_provenance_path_and_evidence(self):
        graph = build_graph(self.bundle)
        self.assertEqual([node["group"] for node in graph["nodes"]],
                         ["source", "document", "element", "chunk", "claim", "topic"])
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

    def test_cli_writes_new_html(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "graph.html"
            self.assertEqual(main(["--bundle", "examples/artifact-bundle-pdf.json", "--output", str(output)]), 0)
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("Knowledge provenance graph", rendered)
        self.assertIn("topic:guidelines-status", rendered)

