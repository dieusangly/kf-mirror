"""Measure pilot parse fidelity against draft source-checked landmarks."""

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from lxml import html


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests/fixtures/pilot_parsing_reference.json"


def normalized(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def matches(elements, sidecar, reference):
    wanted = normalized(reference["text"])
    found = []
    for element, details in zip(elements, sidecar):
        if reference.get("page") != element["location"].get("page") and "page" in reference:
            continue
        if reference.get("kind") and reference["kind"] != element["kind"]:
            continue
        if reference.get("header") and not details.get("header"):
            continue
        if wanted in normalized(element["text"]):
            found.append(element)
    return found


def evaluate(input_dir):
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    report = json.loads((input_dir / "report.json").read_text(encoding="utf-8"))
    corpus = {}
    for outcome in report["outcomes"]:
        if outcome["status"] in {"parsed", "partial"}:
            folder = Path(outcome["output"])
            bundle = json.loads((folder / "artifact-bundle.json").read_text(encoding="utf-8"))
            sidecar = json.loads((folder / "parse.json").read_text(encoding="utf-8"))
            corpus[outcome["manifest_id"]] = (bundle["elements"], sidecar["elements"])
    measurements = {"reference_status": reference["status"], "source_status": {
        outcome["manifest_id"]: outcome["status"] for outcome in report["outcomes"]},
        "passages": [], "structure": [], "reading_order": [], "locations": [], "runtime": []}
    for outcome in report["outcomes"]:
        if outcome["status"] not in {"parsed", "partial"}:
            continue
        elements = corpus[outcome["manifest_id"]][0]
        folder = Path(outcome["output"])
        invalid = []
        if (folder / "docling.json").is_file():
            pages = json.loads((folder / "docling.json").read_text(encoding="utf-8"))["pages"]
            for element in elements:
                location = element["location"]
                page = pages.get(str(location["page"]))
                if page is None:
                    invalid.append(element["id"])
                    continue
                if "bbox" in location:
                    left, top, right, bottom = location["bbox"]
                    if not (0 <= left <= right <= page["size"]["width"] + 1 and
                            0 <= top <= bottom <= page["size"]["height"] + 1):
                        invalid.append(element["id"])
        else:
            document = html.parse(str(ROOT / outcome["local_path"])).getroot()
            for element in elements:
                selected = document.cssselect(element["location"]["selector"])
                if len(selected) != 1:
                    invalid.append(element["id"])
        measurements["locations"].append({"source": outcome["manifest_id"],
                                          "checked": len(elements), "valid": len(elements) - len(invalid),
                                          "invalid_element_ids": invalid})
    for category in ("passages", "structure"):
        for landmark in reference[category]:
            selected = corpus.get(landmark["source"])
            located = matches(*selected, landmark) if selected else []
            measurements[category].append({**landmark, "found": bool(located),
                                           "element_ids": [item["id"] for item in located]})
    for group in reference["reading_order"]:
        selected = corpus.get(group["source"])
        ordinals = []
        for marker in group["markers"]:
            target = {"text": marker} if isinstance(marker, str) else marker
            found = matches(*selected, {**group, **target}) if selected else []
            ordinals.append(found[0]["ordinal"] if found else None)
        expected_pairs = len(ordinals) * (len(ordinals) - 1) // 2
        correct_pairs = sum(a is not None and b is not None and a < b
                            for i, a in enumerate(ordinals) for b in ordinals[i + 1:])
        measurements["reading_order"].append({**group, "ordinals": ordinals,
                                               "correct_pairs": correct_pairs,
                                               "expected_pairs": expected_pairs})
    for outcome in report["outcomes"]:
        if outcome["status"] not in {"parsed", "partial"}:
            continue
        elements = corpus[outcome["manifest_id"]][0]
        pages = outcome.get("page_count")
        measurements["runtime"].append({"source": outcome["manifest_id"],
                                        "seconds": outcome["elapsed_seconds"],
                                        "parsed_pages": pages or None,
                                        "seconds_per_page": round(outcome["elapsed_seconds"] / pages, 3)
                                        if pages else None,
                                        "peak_process_rss_mb": outcome.get("peak_process_rss_mb"),
                                        "element_count": outcome["element_count"]})
    measurements["totals"] = {
        "passages_found": sum(item["found"] for item in measurements["passages"]),
        "passages_checked": len(measurements["passages"]),
        "structure_found": sum(item["found"] for item in measurements["structure"]),
        "structure_checked": len(measurements["structure"]),
        "order_pairs_correct": sum(item["correct_pairs"] for item in measurements["reading_order"]),
        "order_pairs_checked": sum(item["expected_pairs"] for item in measurements["reading_order"]),
        "locations_valid": sum(item["valid"] for item in measurements["locations"]),
        "locations_checked": sum(item["checked"] for item in measurements["locations"]),
    }
    for numerator, denominator, rate in (
        ("passages_found", "passages_checked", "passage_recall"),
        ("structure_found", "structure_checked", "structure_recall"),
        ("order_pairs_correct", "order_pairs_checked", "reading_order_accuracy"),
        ("locations_valid", "locations_checked", "location_validity"),
    ):
        checked = measurements["totals"][denominator]
        measurements["totals"][rate] = round(measurements["totals"][numerator] / checked, 4) if checked else None
    return measurements


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure Task 4 pilot parsing fidelity")
    parser.add_argument("--input-dir", type=Path, default=Path("artifacts/parsed"))
    parser.add_argument("--output", type=Path, help="write measurements here (stdout by default)")
    args = parser.parse_args(argv)
    result = evaluate(args.input_dir)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
