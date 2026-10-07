"""Opt-in OCR integration test; requires local Tesseract and Docling models."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from knowledge_foundry.parsers import parse_pdf


def distance(left, right):
    previous = list(range(len(right) + 1))
    for row, char_left in enumerate(left, 1):
        current = [row]
        for col, char_right in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[col] + 1,
                               previous[col - 1] + (char_left != char_right)))
        previous = current
    return previous[-1]


@unittest.skipUnless(os.environ.get("KF_TEST_OCR") == "1", "set KF_TEST_OCR=1 for the OCR integration test")
class OcrIntegrationTests(unittest.TestCase):
    def test_auto_ocr_recovers_text_from_raster_only_pdf(self):
        from PIL import Image, ImageDraw, ImageFont

        command = os.environ.get("KF_TESSERACT_CMD", "tesseract")
        if not (shutil.which(command) or Path(command).is_file()):
            self.skipTest("Tesseract executable unavailable")
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not Path(font_path).is_file():
            self.skipTest("DejaVu Sans test font unavailable")
        expected = "Contrôle des bénéficiaires effectifs La vigilance est exercée pendant toute la relation."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            image = Image.new("RGB", (1240, 1754), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype(font_path, 32)
            draw.text((80, 120), "Contrôle des bénéficiaires effectifs", font=font, fill="black")
            draw.text((80, 200), "La vigilance est exercée pendant toute la relation.", font=font, fill="black")
            image.save(path, "PDF", resolution=150.0)
            options = {"table_structure": False, "ocr": {
                "mode": "auto", "languages": ["fra", "eng"], "scale": 3.0, "psm": 3,
                "tesseract_cmd": command,
                "tessdata_path": os.environ.get("KF_TESSDATA_PATH"),
            }}
            items, _native, warnings = parse_pdf(path, {}, options)
            self.assertFalse(warnings)
            actual = " ".join(item["text"] for item in items)
            normal = lambda value: " ".join(value.casefold().split())
            reference = normal(expected)
            recognized = normal(actual)
            cer = distance(reference, recognized) / len(reference)
            self.assertLessEqual(cer, 0.10,
                                 f"OCR CER above 10%: {recognized!r}")
            ref_words = reference.split()
            got_words = recognized.split()
            wer = distance(ref_words, got_words) / len(ref_words)
            self.assertLessEqual(wer, 0.10,
                                 f"OCR WER above 10%: {recognized!r}")
            print(f"OCR fixture CER={cer:.4f} WER={wer:.4f}")


if __name__ == "__main__":
    unittest.main()
