import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from docx import Document
from jsonschema import Draft202012Validator, FormatChecker
from lxml import html

from knowledge_foundry.parsing import PARSERS, SCHEMA, main, parse_collection
from knowledge_foundry.parsers import parse_docx, parse_markdown


ROOT = Path(__file__).resolve().parents[1]


def _fixture(root, name, media_type, contents):
    path = root / name
    if isinstance(contents, bytes):
        path.write_bytes(contents)
    else:
        path.write_text(contents, encoding="utf-8")
    raw = path.read_bytes()
    return {"id": name, "source_version": "v1", "publisher_url": f"https://example.org/{name}",
            "local_path": name, "retrieved_on": "2026-10-07", "media_type": media_type,
            "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _config(root, entries):
    (root / "manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "sources": entries}))
    data = {"root": ".", "manifest": "manifest.json",
            "sources": {entry["id"]: {"source_id": f"src:{entry['id']}",
                                      "title": entry["id"], "class": "test"} for entry in entries},
            "parsing": {"ocr": {"mode": "off"}}}
    path = root / "config.json"
    path.write_text(json.dumps(data))
    return path


class ParsingTests(unittest.TestCase):
    def test_pilot_html_clauses_have_resolvable_selectors_and_metadata_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            report = parse_collection(ROOT / "config/pipeline.json", directory, ["L1", "L2"])
            self.assertEqual([item["status"] for item in report["outcomes"]], ["parsed", "parsed"])
            for outcome in report["outcomes"]:
                folder = Path(outcome["output"])
                bundle = json.loads((folder / "artifact-bundle.json").read_text())
                sidecar = json.loads((folder / "parse.json").read_text())
                Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(bundle)
                document = html.parse(str(ROOT / outcome["local_path"])).getroot()
                self.assertTrue(bundle["elements"])
                for element in bundle["elements"]:
                    matches = document.cssselect(element["location"]["selector"])
                    self.assertEqual(len(matches), 1)
                    self.assertIn(element["text"], " ".join(" ".join(matches[0].itertext()).split()))
                self.assertEqual(sidecar["metadata_origins"]["source.version"]["origin"], "manifest")
                self.assertEqual(len(sidecar["elements"]), len(bundle["elements"]))
            l1 = json.loads((Path(report["outcomes"][0]["output"]) / "artifact-bundle.json").read_text())
            self.assertEqual({element["location"].get("clause") for element in l1["elements"]
                              if "clause" in element["location"]}, {"1°", "2°"})

    def test_markdown_and_docx_preserve_order_tables_and_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = "# Heading\n\nFirst paragraph.\n\n| A | B |\n|---|---|\n| x | y |\n"
            md = _fixture(root, "example.md", "text/markdown", markdown)
            document = Document()
            document.add_heading("Heading", level=1)
            document.add_paragraph("First paragraph.")
            table = document.add_table(rows=2, cols=2)
            for row, values in zip(table.rows, (("A", "B"), ("x", "y"))):
                for cell, value in zip(row.cells, values):
                    cell.text = value
            docx_path = root / "example.docx"
            document.save(docx_path)
            docx = _fixture(root, "example.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            docx_path.read_bytes())
            config = _config(root, [md, docx])
            report = parse_collection(config, root / "output")
            self.assertEqual([item["status"] for item in report["outcomes"]], ["parsed", "parsed"])
            for outcome in report["outcomes"]:
                folder = Path(outcome["output"])
                bundle = json.loads((folder / "artifact-bundle.json").read_text())
                sidecar = json.loads((folder / "parse.json").read_text())
                self.assertEqual([e["ordinal"] for e in bundle["elements"]], list(range(7)))
                self.assertEqual([e["kind"] for e in bundle["elements"][:3]],
                                 ["heading", "paragraph", "table"])
                self.assertEqual(len([e for e in bundle["elements"] if e["kind"] == "table_cell"]), 4)
                self.assertEqual(len([e for e in sidecar["elements"][3:] if e["parent_element_id"]]), 4)
                self.assertEqual(sidecar["elements"][1]["parent_element_id"], bundle["elements"][0]["id"])
                Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(bundle)
            md_bundle = json.loads((Path(report["outcomes"][0]["output"]) / "artifact-bundle.json").read_text())
            self.assertEqual(md_bundle["elements"][1]["location"]["line_start"], 3)
            docx_bundle = json.loads((Path(report["outcomes"][1]["output"]) / "artifact-bundle.json").read_text())
            self.assertEqual(docx_bundle["elements"][2]["location"]["block_index"], 2)

    def test_bad_source_does_not_block_good_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = _fixture(root, "good.md", "text/markdown", "# Heading\n\nText.\n")
            missing = dict(good, id="missing.md", local_path="missing.md")
            config = _config(root, [missing, good])
            report = parse_collection(config, root / "output")
            self.assertEqual([item["status"] for item in report["outcomes"]], ["failed", "parsed"])
            self.assertEqual(report["outcomes"][0]["error"]["code"], "missing_file")
            self.assertTrue((root / "output" / "report.json").is_file())

    def test_repeated_runs_keep_separate_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = _fixture(root, "good.md", "text/markdown", "# Heading\n")
            config = _config(root, [entry])
            output = root / "output"
            first = parse_collection(config, output)
            second = parse_collection(config, output)

            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertEqual(len(list(output.glob("report-*.json"))), 2)
            for report in (first, second):
                self.assertRegex(report["run_id"], r"^\d{8}-\d{2}-\d{2}-\d{2}-[0-9a-f]{8}$")
                self.assertIn("+00:00", report["started_at"])
                archived = output / f"report-{report['run_id']}.json"
                self.assertEqual(json.loads(archived.read_text()), report)
            self.assertEqual(json.loads((output / "report.json").read_text()), second)

    def test_cli_reports_each_document_and_continues_after_parser_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = _fixture(root, "bad.md", "text/markdown", "# Broken\n")
            good = _fixture(root, "good.md", "text/markdown", "# Heading\n\nText.\n")
            config = _config(root, [bad, good])
            stdout, stderr = io.StringIO(), io.StringIO()

            def parse_or_fail(path, source, settings):
                if path.name == "bad.md":
                    raise ValueError("broken document")
                return parse_markdown(path, source, settings)

            with patch.dict(PARSERS, {"markdown": (parse_or_fail, "fixture")}), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["--config", str(config), "--output-dir", str(root / "output")])

            self.assertEqual(exit_code, 1)
            report = json.loads(stdout.getvalue())
            self.assertEqual([item["status"] for item in report["outcomes"]], ["failed", "parsed"])
            self.assertEqual([item["status"] for item in json.loads(
                (root / "output" / "report.json").read_text())["outcomes"]], ["failed", "parsed"])
            self.assertEqual(len(stderr.getvalue().splitlines()), 5)
            progress = stderr.getvalue().splitlines()
            self.assertIn("[1/2] bad.md bad.md: parsing...", progress[0])
            self.assertIn("[1/2] bad.md bad.md: failed in ", progress[1])
            self.assertIn("broken document", progress[1])
            self.assertIn("[2/2] good.md good.md: parsing...", progress[2])
            self.assertIn("[2/2] good.md good.md: succeeded in ", progress[3])
            self.assertIn("(2 elements)", progress[3])
            self.assertIn(f"report-{report['run_id']}.json", progress[4])
            self.assertGreaterEqual(report["outcomes"][1]["elapsed_seconds"], 0)

    def test_docx_nested_table_keeps_parent_and_unique_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested.docx"
            document = Document()
            outer = document.add_table(rows=1, cols=1)
            nested = outer.cell(0, 0).add_table(rows=1, cols=1)
            nested.cell(0, 0).text = "Nested evidence"
            document.save(path)
            items, _native, _warnings = parse_docx(path, {}, {})
            self.assertEqual([item["kind"] for item in items],
                             ["table", "table_cell", "table", "table_cell"])
            self.assertEqual(items[2]["parent_key"], items[1]["locator_key"])
            self.assertEqual(items[3]["location"]["table_index"], 1)

    def test_docx_vertical_merge_keeps_one_cell_with_row_span(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "merged.docx"
            document = Document()
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "Merged"
            table.cell(0, 1).text = "A"
            table.cell(1, 1).text = "B"
            table.cell(0, 0).merge(table.cell(1, 0))
            document.save(path)
            items, _native, _warnings = parse_docx(path, {}, {})
            cells = [item for item in items if item["kind"] == "table_cell"]
            self.assertEqual([item["text"] for item in cells], ["Merged", "A", "B"])
            self.assertEqual(cells[0]["details"]["row_span"], 2)

    def test_missing_tesseract_fails_pdf_and_continues_to_next_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = _fixture(root, "scan.pdf", "application/pdf", b"%PDF-1.7\nfixture")
            markdown = _fixture(root, "good.md", "text/markdown", "# Heading\n")
            config_path = _config(root, [pdf, markdown])
            config = json.loads(config_path.read_text())
            config["parsing"]["ocr"] = {"mode": "auto", "tesseract_cmd": "kf-no-such-tesseract"}
            config_path.write_text(json.dumps(config))
            report = parse_collection(config_path, root / "output")
            self.assertEqual([item["status"] for item in report["outcomes"]], ["failed", "parsed"])
            self.assertEqual(report["outcomes"][0]["error"]["code"], "ocr_setup_error")
            self.assertIn("Tesseract CLI unavailable", report["outcomes"][0]["error"]["message"])

    def test_partial_parse_retains_valid_elements_and_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = _fixture(root, "partial.md", "text/markdown", "# Located\n")
            config = _config(root, [entry])
            item = {"kind": "heading", "text": "Located", "locator_key": "markdown:line-1:block-0",
                    "location": {"format": "markdown", "heading_path": ["Located"],
                                 "line_start": 1, "line_end": 1},
                    "parent_key": None, "details": {}}
            with patch.dict(PARSERS, {"markdown": (lambda *_args: (
                [item], None, [{"code": "unlocated_block", "message": "omitted content"}]), "fixture")}):
                report = parse_collection(config, root / "output")
            outcome = report["outcomes"][0]
            self.assertEqual(outcome["status"], "partial")
            self.assertEqual(outcome["warnings"][0]["code"], "unlocated_block")
            bundle = json.loads((Path(outcome["output"]) / "artifact-bundle.json").read_text())
            self.assertEqual(len(bundle["elements"]), 1)

    def test_markdown_code_block_keeps_text_and_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "code.md"
            path.write_text("# Heading\n\n```python\nprint('source')\n```\n", encoding="utf-8")
            items, _native, _warnings = parse_markdown(path, {}, {"tables": True})
            self.assertEqual([item["kind"] for item in items], ["heading", "paragraph"])
            self.assertIn("print('source')", items[1]["text"])
            self.assertEqual(items[1]["location"]["line_start"], 3)
            self.assertEqual(items[1]["location"]["line_end"], 5)


if __name__ == "__main__":
    unittest.main()
