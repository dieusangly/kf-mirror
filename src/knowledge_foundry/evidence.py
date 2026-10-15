"""Resolve exact claim passages without changing saved chunk references."""


def claim_evidence_references(chunk, element_map):
    """Give spanless saved headings a deterministic full-text span, and nothing else."""
    references = []
    for reference in chunk["evidence"]:
        element = element_map.get(reference["element_id"])
        if "span" not in reference and element and element["kind"] == "heading" and element["text"]:
            reference = {**reference, "span": {"start": 0, "end": len(element["text"])}}
        references.append(reference)
    return references
