import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from knowledge_foundry.chunking import (
    ROOT, _chunk_document, _parts, build_collection, clean, digest, settings_from_config,
    token_count,
)
from scripts.validate_artifact_examples import check


PARSED = ROOT / "artifacts/parsed"
PILOT_AVAILABLE = len(list(PARSED.glob("documents/*/artifact-bundle.json"))) == 5


def parsed_source(source_id):
    for path in PARSED.glob("documents/*/artifact-bundle.json"):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if bundle["sources"][0]["id"] == source_id:
            return bundle, json.loads(path.with_name("parse.json").read_text(encoding="utf-8"))
    raise AssertionError(f"missing pilot source: {source_id}")


class ChunkingTests(unittest.TestCase):
    def test_oversized_table_row_keeps_header_with_row_content(self):
        value = "Column A | Column B\n" + " ".join(f"value{i}" for i in range(80))
        spans = _parts(value, 40, table=True)
        self.assertIn("Column A", value[spans[0][0]:spans[0][1]])
        self.assertIn("value0", value[spans[0][0]:spans[0][1]])
        self.assertTrue(all(token_count(value[start:end]) <= 40 for start, end in spans))

    def test_split_table_repeats_header_with_every_row_continuation(self):
        header = "Sector | Alert criterion | Bank | Insurer"
        chunks = self._small_document([
            ("table", "\n".join([header, *(f"row{i} | " + "detail " * 14
                                      for i in range(4))]), 1, ["Matrix"]),
        ], 48)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(header in chunk["text"] for chunk in chunks))
        for chunk in chunks:
            table_refs = [ref for ref in chunk["evidence"] if "span" in ref]
            self.assertTrue(any(ref["span"]["start"] == 0 and
                                ref["span"]["end"] == len(header) for ref in table_refs))

    def _small_document(self, rows, maximum=64):
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        bundle["elements"] = []
        details = []
        template = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))["elements"][0]
        for index, (kind, value, page, path) in enumerate(rows):
            element = copy.deepcopy(template)
            element.update(id="el:" + digest(str(index)), kind=kind, text=value,
                           ordinal=index, locator_key=f"pdf:p{page}:block-{index}",
                           revision=digest(kind + "\n" + value),
                           location={"format": "pdf", "page": page, "label": f"block {index}"})
            bundle["elements"].append(element)
            details.append({"element_id": element["id"], "evidence_role": "authoritative",
                            "heading_path": path})
        return _chunk_document(bundle, {"elements": details}, "build:test", {"max_tokens": maximum})[0]

    def test_parent_headings_attach_to_first_body(self):
        chunks = self._small_document([
            ("heading", "Section principale", 1, ["Section principale"]),
            ("heading", "Sous-section", 1, ["Section principale", "Sous-section"]),
            ("paragraph", "Le passage explique la règle applicable.", 1,
             ["Section principale", "Sous-section"]),
        ])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len([ref for ref in chunks[0]["evidence"] if "span" in ref]), 3)

    def test_list_lead_repeats_as_source_context_at_split(self):
        path = ["Liste"]
        chunks = self._small_document([
            ("heading", "Liste", 1, path),
            ("paragraph", "Les critères sont les suivants :", 1, path),
            ("list_item", "- " + " ".join(f"premier{i}" for i in range(27)) + ".", 1, path),
            ("list_item", "- " + " ".join(f"deuxième{i}" for i in range(27)) + ".", 1, path),
        ], 64)
        second = next(chunk for chunk in chunks if "deuxième0" in chunk["text"])
        self.assertIn("Les critères sont les suivants :", second["text"])
        self.assertEqual(sum("span" in ref for ref in second["evidence"] if
                             ref["element_id"] == chunks[0]["evidence"][1]["element_id"]), 0)

    def test_footnotes_separate_and_cross_page_sentence_keeps_context(self):
        chunks = self._small_document([
            ("heading", "Encadré", 20, ["Encadré"]),
            ("paragraph", "Ces cartes peuvent être adossées à des comptes et peuvent être", 20, ["Encadré"]),
            ("footnote", "1 Note sur un autre passage.", 20, ["Encadré"]),
            ("paragraph", "émises en France ou à l'étranger.", 21, ["Encadré"]),
        ])
        continuation = next(chunk for chunk in chunks if "émises en France" in chunk["text"])
        self.assertIn("peuvent être", continuation["text"])
        self.assertIn("Encadré", continuation["text"])
        self.assertNotIn("Note sur un autre passage", continuation["text"])

    def test_page_footnote_does_not_inherit_later_section_heading(self):
        chunks = self._small_document([
            ("heading", "New section", 1, ["New section"]),
            ("paragraph", "The section begins with a separate rule.", 1, ["New section"]),
            ("footnote", "1 Note citing introductory text.", 1, ["New section"]),
        ])
        note = next(chunk for chunk in chunks if "Note citing" in chunk["text"])
        self.assertNotIn("New section", note["text"])

    def test_lowercase_continuation_of_list_item_keeps_its_source_context(self):
        chunks = self._small_document([
            ("heading", "Section", 1, ["Section"]),
            ("list_item", "9. Les organismes appliquent les obligations", 1, ["Section"]),
            ("paragraph", "de vigilance françaises.", 1, ["Section"]),
        ])
        continuation = next(chunk for chunk in chunks if "de vigilance françaises" in chunk["text"])
        self.assertIn("9. Les organismes appliquent", continuation["text"])

    def test_full_short_list_fits_by_dropping_outer_heading(self):
        path = ["Long parent heading", "Specific list"]
        chunks = self._small_document([
            ("heading", "Long parent heading", 1, path[:1]),
            ("heading", "Specific list", 1, path),
            ("paragraph", "The cases in this example are as follows :", 1, path),
            ("list_item", "- first condition applies to the described client ;", 1, path),
            ("list_item", "- second condition applies to the described client ;", 1, path),
            ("list_item", "- third condition applies to the described client ;", 1, path),
        ], 43)
        self.assertTrue(any(all(term in chunk["text"] for term in
                                ("first condition", "second condition", "third condition"))
                            for chunk in chunks))

    def test_new_comma_lead_replaces_prior_ellipsis_list_context(self):
        path = ["Rules"]
        chunks = self._small_document([
            ("heading", "Rules", 1, path),
            ("paragraph", "By exception, checks may occur…", 1, path),
            ("list_item", "- during onboarding;", 1, path),
            ("paragraph", "And throughout the relationship,", 1, path),
            ("list_item", "- records must be kept current.", 1, path),
        ], 40)
        ongoing = next(chunk for chunk in chunks if "records must be kept current" in chunk["text"])
        self.assertIn("And throughout the relationship", ongoing["text"])
        self.assertNotIn("By exception", ongoing["text"])

    def test_page_footnotes_do_not_hide_following_list_from_lead(self):
        path = ["Example"]
        chunks = self._small_document([
            ("heading", "Example", 6, path),
            ("paragraph", "The persons satisfying either condition,", 6, path),
            ("footnote", "5 Citation for the preceding section.", 6, path),
            ("list_item", "- first condition on the next page;", 7, path),
            ("list_item", "- second condition on the next page.", 7, path),
        ], 40)
        following = next(chunk for chunk in chunks if "first condition" in chunk["text"])
        self.assertIn("The persons satisfying either condition", following["text"])
        self.assertNotIn("Citation for the preceding section", following["text"])

    def test_short_list_subheading_moves_to_following_prose(self):
        chunks = self._small_document([
            ("heading", "Section", 1, ["Section"]),
            ("paragraph", "Les deux positions précédentes sont terminées.", 1, ["Section"]),
            ("list_item", "➢ Informations pertinentes pour les personnes morales lorsque la relation d'affaires est établie à distance et que les pièces justificatives sont examinées", 1, ["Section"]),
            ("paragraph", "Les statuts et l'objet social sont examinés.", 1, ["Section"]),
        ])
        subheading = next(chunk for chunk in chunks if "➢ Informations" in chunk["text"])
        self.assertIn("Les statuts", subheading["text"])
        self.assertNotIn("positions précédentes", subheading["text"])

    def test_numbered_step_keeps_prior_explanation_when_split(self):
        path = ["Cas"]
        chunks = self._small_document([
            ("heading", "Cas", 1, path),
            ("paragraph", "Les déclarants peuvent déceler le point 3 de ce schéma.", 1, path),
            ("list_item", "1 " + " ".join(f"situation{i}" for i in range(27)) + ".", 1, path),
            ("list_item", "2 " + " ".join(f"action{i}" for i in range(27)) + ".", 1, path),
            ("list_item", "3 " + " ".join(f"observation{i}" for i in range(27)) + ".", 1, path),
        ], 64)
        third = next(chunk for chunk in chunks if "observation0" in chunk["text"])
        self.assertIn("point 3 de ce schéma", third["text"])

    def test_numbered_parent_does_not_govern_next_numbered_item(self):
        path = ["Introduction"]
        chunks = self._small_document([
            ("heading", "Introduction", 1, path),
            ("list_item", "3. Elle tient compte :", 1, path),
            ("list_item", "- de la jurisprudence récente ;", 1, path),
            ("list_item", "4. Le document est public.", 1, path),
        ])
        fourth = next(chunk for chunk in chunks if "4. Le document" in chunk["text"])
        self.assertNotIn("3. Elle tient compte", fourth["text"])

    def test_configuration_and_cleanup(self):
        self.assertEqual(settings_from_config({}), {"max_tokens": 256})
        for value in (0, 31, True, "256"):
            with self.assertRaises(ValueError):
                settings_from_config({"chunking": {"max_tokens": value}})
        self.assertEqual(clean("  Un\u00ad mot  \n reste.  "), "Un mot reste.")
        self.assertEqual(clean("| a | b |\n| c | d |", table=True), "| a | b |\n| c | d |")

    def test_self_contained_structure_and_evidence_roles(self):
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        for name in ("chunks", "claims", "topics", "builds"):
            bundle[name] = []
        template = bundle["elements"][0]
        elements = []
        details = []

        def add(kind, value, role="authoritative", locator=None):
            element = copy.deepcopy(template)
            element["kind"] = kind
            element["text"] = value
            element["ordinal"] = len(elements)
            element["locator_key"] = locator or f"test:{len(elements)}"
            element["id"] = "el:" + digest(element["document_id"] + "\n" + element["locator_key"])
            element["revision"] = digest(kind + "\n" + value)
            elements.append(element)
            details.append({"element_id": element["id"], "evidence_role": role,
                            "heading_path": ["Section"]})
            return element

        add("heading", "Section")
        add("paragraph", " ".join(f"alpha{i}" for i in range(20)) + ".")
        sentence = add("paragraph", " ".join(f"bravo{i}" for i in range(12)) + ".")
        item = add("list_item", " ".join(f"item{i}" for i in range(28)) + ".")
        table = add("table", "| a | b |\n| c | d |")
        add("table_cell", "a", role="structural")
        add("paragraph", "diagram label", role="auxiliary")
        repeated = add("paragraph", "Repeated source phrase.")
        add("paragraph", repeated["text"])
        duplicate = add("paragraph", repeated["text"], locator=repeated["locator_key"])
        duplicate["id"] = "el:" + digest("duplicate test element")
        details[-1]["element_id"] = duplicate["id"]
        bundle["elements"] = elements
        chunks, metrics = _chunk_document(bundle, {"elements": details}, "build:test", {"max_tokens": 32})
        body = [ref for chunk in chunks for ref in chunk["evidence"] if "span" in ref]
        self.assertEqual(metrics["exact_duplicates_removed"], 1)
        self.assertEqual(metrics["repeated_text_distinct_locations"], 1)
        self.assertEqual(sum(ref["element_id"] == sentence["id"] for ref in body), 1)
        self.assertEqual(sum(ref["element_id"] == item["id"] for ref in body), 1)
        self.assertTrue(any(ref["element_id"] == table["id"] for ref in body))
        excluded = {elements[5]["id"], elements[6]["id"]}
        self.assertFalse(any(ref["element_id"] in excluded for ref in body))
        for chunk in chunks:
            if any(ref["element_id"] == table["id"] for ref in chunk["evidence"]):
                self.assertFalse(any(ref["element_id"] not in {table["id"], elements[0]["id"]}
                                     for ref in chunk["evidence"] if "span" in ref))
        self.assertTrue(all(token_count(chunk["text"]) <= 32 for chunk in chunks))

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 4 pilot artifacts are unavailable")
    def test_exact_duplicates_keep_distinct_t_locations(self):
        bundle, sidecar = parsed_source("src:tracfin-threat-report-tome-3")
        details = {item["element_id"]: item for item in sidecar["elements"]}
        by_text = {}
        pair = None
        for element in bundle["elements"]:
            if details[element["id"]]["evidence_role"] != "authoritative":
                continue
            key = (element["kind"], clean(element["text"]))
            earlier = by_text.get(key)
            if earlier and earlier["location"] != element["location"] and len(key[1]) > 12:
                pair = (earlier, element)
                break
            by_text[key] = element
        self.assertIsNotNone(pair, "T must contain a repeated phrase at distinct locations")
        first, second = pair
        sample = copy.deepcopy(bundle)
        sample["elements"] = [copy.deepcopy(first), copy.deepcopy(second)]
        for i, element in enumerate(sample["elements"]):
            element["ordinal"] = i
        sample_sidecar = {"elements": [copy.deepcopy(details[first["id"]]), copy.deepcopy(details[second["id"]])]}
        chunks, metrics = _chunk_document(sample, sample_sidecar, "build:test", {"max_tokens": 256})
        cited = {ref["element_id"] for chunk in chunks for ref in chunk["evidence"]}
        self.assertIn(first["id"], cited)
        self.assertIn(second["id"], cited)
        self.assertEqual(metrics["repeated_text_distinct_locations"], 1)
        self.assertEqual(metrics["exact_duplicates_removed"], 0)

        duplicate = copy.deepcopy(first)
        duplicate["id"] = "el:" + digest(first["id"] + " duplicate")
        duplicate["locator_key"] += ":duplicate"
        duplicate["ordinal"] = 1
        sample["elements"] = [copy.deepcopy(first), duplicate]
        sample["elements"][0]["ordinal"] = 0
        duplicate_sidecar = copy.deepcopy(details[first["id"]])
        duplicate_sidecar["element_id"] = duplicate["id"]
        sample_sidecar["elements"] = [copy.deepcopy(details[first["id"]]), duplicate_sidecar]
        chunks, metrics = _chunk_document(sample, sample_sidecar, "build:test", {"max_tokens": 256})
        cited = {ref["element_id"] for chunk in chunks for ref in chunk["evidence"]}
        self.assertIn(first["id"], cited)
        self.assertNotIn(duplicate["id"], cited)
        self.assertEqual(metrics["exact_duplicates_removed"], 1)

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 4 pilot artifacts are unavailable")
    def test_table_aggregate_splits_at_configured_limit(self):
        bundle, sidecar = parsed_source("src:tracfin-threat-report-tome-3")
        table = next(element for element in bundle["elements"] if element["kind"] == "table" and "\n" in element["text"])
        info = next(item for item in sidecar["elements"] if item["element_id"] == table["id"])
        sample = copy.deepcopy(bundle)
        sample["elements"] = [copy.deepcopy(table)]
        sample["elements"][0]["ordinal"] = 0
        chunks, _ = _chunk_document(sample, {"elements": [info]}, "build:test", {"max_tokens": 64})
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(token_count(chunk["text"]) <= 64 for chunk in chunks))
        self.assertTrue(any("\n" in chunk["text"] for chunk in chunks))
        self.assertTrue(all({ref["element_id"] for ref in chunk["evidence"]} == {table["id"]} for chunk in chunks))

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 4 pilot artifacts are unavailable")
    def test_sentence_moves_intact_to_next_chunk_when_it_fits_there(self):
        bundle, sidecar = parsed_source("src:cmf-L561-2-2")
        sample = copy.deepcopy(bundle)
        sample["elements"] = copy.deepcopy(bundle["elements"][:3])
        first, second = sample["elements"][1:]
        first["text"] = " ".join(f"alpha{i}" for i in range(20)) + "."
        second["text"] = " ".join(f"bravo{i}" for i in range(12)) + "."
        for element in (first, second):
            element["revision"] = digest(element["kind"] + "\n" + element["text"])
        chunks, _ = _chunk_document(sample, {"elements": sidecar["elements"][:3]},
                                    "build:test", {"max_tokens": 32})
        citations = [ref for chunk in chunks for ref in chunk["evidence"]
                     if ref["element_id"] == second["id"]]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["span"], {"start": 0, "end": len(second["text"])})
        self.assertTrue(all(token_count(chunk["text"]) <= 32 for chunk in chunks))

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 4 pilot artifacts are unavailable")
    def test_fitting_list_item_keeps_one_span_by_dropping_heading_context(self):
        bundle, sidecar = parsed_source("src:cmf-L561-2-2")
        sample = copy.deepcopy(bundle)
        sample["elements"] = copy.deepcopy(bundle["elements"][:2])
        item = sample["elements"][1]
        item["kind"] = "list_item"
        item["text"] = " ".join(f"item{i}" for i in range(28)) + "."
        item["revision"] = digest(item["kind"] + "\n" + item["text"])
        self.assertLessEqual(token_count(item["text"]), 32)
        chunks, _ = _chunk_document(sample, {"elements": sidecar["elements"][:2]},
                                    "build:test", {"max_tokens": 32})
        citations = [ref for chunk in chunks for ref in chunk["evidence"]
                     if ref["element_id"] == item["id"]]
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["span"], {"start": 0, "end": len(item["text"])})
        self.assertTrue(all(token_count(chunk["text"]) <= 32 for chunk in chunks))

    @unittest.skipUnless(PILOT_AVAILABLE, "saved Task 4 pilot artifacts are unavailable")
    def test_pilot_schema_evidence_tables_figures_and_repeat_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            stale = first / "documents" / "stale" / "document-view.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}", encoding="utf-8")
            config = ROOT / "config/pipeline.json"
            report = build_collection(config, PARSED, first)
            build_collection(config, PARSED, second)
            self.assertFalse(stale.exists())
            for name in ("artifact-bundle.json", "figure-links.json", "report.json"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
            result = json.loads((first / "artifact-bundle.json").read_text(encoding="utf-8"))
            check(result)
            self.assertEqual(len(result["documents"]), 5)
            self.assertEqual(len(result["builds"]), 1)
            self.assertEqual(result["builds"][0]["status"], "complete")
            self.assertEqual(sum(item["chunks"] for item in report["documents"]), len(result["chunks"]))
            figure_links = json.loads((first / "figure-links.json").read_text(encoding="utf-8"))
            for document in result["documents"]:
                relative = Path("documents") / document["id"].removeprefix("doc:") / "document-view.json"
                self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())
                view = json.loads((first / relative).read_text(encoding="utf-8"))
                self.assertEqual(view["build_id"], result["builds"][0]["id"])
                self.assertEqual(view["document"], document)
                self.assertEqual(view["elements"], [item for item in result["elements"]
                                                    if item["document_id"] == document["id"]])
                self.assertEqual(view["chunks"], [item for item in result["chunks"]
                                                  if item["document_id"] == document["id"]])
                self.assertEqual(view["figure_links"], [item for item in figure_links
                                                         if item["document_id"] == document["id"]])
            elements = {item["id"]: item for item in result["elements"]}
            used = {ref["element_id"] for chunk in result["chunks"] for ref in chunk["evidence"]}
            citations_by_element = {}
            body_citations_by_element = {}
            for chunk in result["chunks"]:
                body_kinds = [elements[ref["element_id"]]["kind"] for ref in chunk["evidence"]
                              if "span" in ref]
                if "table" in body_kinds:
                    self.assertEqual(set(body_kinds) - {"table", "heading"}, set())
                for ref in chunk["evidence"]:
                    citations_by_element.setdefault(ref["element_id"], set()).add(chunk["id"])
                    if "span" in ref:
                        body_citations_by_element.setdefault(ref["element_id"], set()).add(chunk["id"])
            roles = {}
            flow = set()
            auxiliary = set()
            tables = set()
            cells = set()
            for doc in result["documents"]:
                path = PARSED / "documents" / doc["id"].removeprefix("doc:")
                sidecar = json.loads((path / "parse.json").read_text(encoding="utf-8"))
                for info in sidecar["elements"]:
                    roles[info["element_id"]] = info["evidence_role"]
                    if info.get("docling_parent_ref", "").startswith("#/pictures"):
                        (flow if info["evidence_role"] == "authoritative" else auxiliary).add(info["element_id"])
                for element in result["elements"]:
                    if element["document_id"] == doc["id"]:
                        if element["kind"] == "table":
                            tables.add(element["id"])
                        if element["kind"] == "table_cell":
                            cells.add(element["id"])
            self.assertTrue(flow)
            self.assertTrue(auxiliary)
            self.assertTrue(flow <= used, "picture-contained prose must survive")
            self.assertFalse(auxiliary & used, "diagram labels must not become chunk evidence")
            self.assertTrue(tables <= used)
            self.assertFalse(cells & used)
            self.assertTrue(all(roles[item] == "authoritative" for item in used))
            self.assertTrue(all(len(body_citations_by_element[element["id"]]) == 1
                                for element in result["elements"]
                                if element["kind"] == "list_item" and element["id"] in used
                                and token_count(clean(element["text"])) <= 256))
            self.assertTrue(all(token_count(chunk["text"]) <= 256 for chunk in result["chunks"]))
            for doc in result["documents"]:
                chunks = [chunk for chunk in result["chunks"] if chunk["document_id"] == doc["id"]]
                self.assertEqual([chunk["sequence"] for chunk in chunks], list(range(len(chunks))))
            for chunk in result["chunks"]:
                for ref in chunk["evidence"]:
                    element = elements[ref["element_id"]]
                    span = ref.get("span")
                    quoted = element["text"][span["start"]:span["end"]] if span else element["text"]
                    self.assertIn(clean(quoted, table=element["kind"] == "table"), chunk["text"])
            figures = json.loads((first / "figure-links.json").read_text(encoding="utf-8"))
            self.assertTrue(figures)
            for link in figures:
                self.assertEqual(elements[link["element_id"]]["kind"], "figure")
                self.assertNotIn(link["element_id"], used)
                path = PARSED / link["asset"]["path"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), link["asset"]["sha256"])


if __name__ == "__main__":
    unittest.main()
