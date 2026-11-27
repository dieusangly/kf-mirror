"""Chunk text and stable element location keys."""
import re

def clean(text, table=False):
    """Normalize layout whitespace and contents leaders; preserve wording."""
    text = text.replace("\u00ad", "")
    if table:
        # Contents leaders are layout, not words or column values. Keep source
        # offsets on the original row while budgeting/rendering its useful text.
        text = re.sub(r"[._]{3,}(?=\s*\|\s*\d+\s*(?:\n|$))", " ", text)
        return "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                         for line in text.splitlines() if line.strip())
    return re.sub(r"\s+", " ", text).strip()


def _location_key(element):
    location = element["location"]
    if location["format"] == "pdf" and "bbox" in location:
        return ("pdf", location["page"], tuple(location["bbox"]))
    return (element["locator_key"],)
