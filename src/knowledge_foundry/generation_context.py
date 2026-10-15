"""Group saved passages using structural context hints for generation."""

import re
from collections import defaultdict


def footnote_links(chunks, elements):
    """Suggest numbered PDF note links from same-page text; review attachment."""
    notes = defaultdict(list)
    passages = []
    for chunk in chunks:
        for ref in chunk["evidence"]:
            if "span" not in ref:
                continue
            element = elements[ref["element_id"]]
            if element["location"]["format"] != "pdf":
                continue
            span = ref["span"]
            text = element["text"][span["start"]:span["end"]]
            location = (ref["document_id"], element["location"]["page"])
            if element["kind"] == "footnote" and re.match(r"^\d+\s+", text):
                notes[location].append((text.split()[0], ref))
            elif element["kind"] not in {"footnote", "heading"}:
                passages.append((location, text, ref))
    return [(parent, note) for location, text, parent in passages
            for number, note in notes[location]
            if not re.match(r"^\s*" + re.escape(number) + r"[.)]\s", text) and
            re.search(r"(?<![\w.\-])" + re.escape(number) + r"(?![\w°%\-]|\.\d)", text)]


def generation_batches(chunks, elements, batch_size):
    """Pack linked chunk groups once; batch_size is a target, not a context cutoff."""
    owners = defaultdict(set)
    for index, chunk in enumerate(chunks):
        for ref in chunk["evidence"]:
            if "span" in ref:
                owners[ref["element_id"]].add(index)
    parents = list(range(len(chunks)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def link(left, right):
        parents[root(right)] = root(left)

    def link_elements(left_id, right_id):
        for left in owners[left_id]:
            for right in owners[right_id]:
                link(left, right)

    for index, chunk in enumerate(chunks):
        for ref in chunk["evidence"]:
            if "span" not in ref and elements[ref["element_id"]]["kind"] != "heading":
                for owner in owners[ref["element_id"]]:
                    link(index, owner)
    # Lettered options need their introduction, including across PDF page breaks.
    flow = sorted((elements[key] for key in owners if elements[key]["kind"] != "footnote"),
                  key=lambda item: (item["document_id"], item["ordinal"]))
    for index, element in enumerate(flow):
        if not element["text"].rstrip().endswith(":"):
            continue
        related = []
        for other in flow[index + 1:]:
            if other["document_id"] != element["document_id"] or other["kind"] != "list_item" or \
                    not re.match(r"^[a-z][.)]\s", other["text"], re.I):
                break
            related.append(other)
        if related and index and flow[index - 1]["kind"] == "list_item" and \
                flow[index - 1]["text"].rstrip().endswith(":") and \
                flow[index - 1]["document_id"] == element["document_id"]:
            related.append(flow[index - 1])
        for other in related:
            link_elements(element["id"], other["id"])
    # A continued table row can carry an obligation in a different column from
    # the typology examples. Keep its saved spans together across page breaks.
    tables = sorted((elements[key] for key in owners if elements[key]["kind"] == "table"),
                    key=lambda item: (item["document_id"], item["ordinal"]))
    for previous, current in zip(tables, tables[1:]):
        left_location, right_location = previous["location"], current["location"]
        if previous["document_id"] != current["document_id"] or \
                left_location["format"] != "pdf" or right_location["format"] != "pdf" or \
                right_location["page"] != left_location["page"] + 1:
            continue
        cells = previous["text"].splitlines()[-1].split("|")
        next_cells = current["text"].splitlines()[0].split("|")
        if len(cells) < 2 or len(next_cells) < 2:
            continue
        # PDF extraction may omit an empty/merged column on the next page.
        # In that case require a visibly continued final cell as well.
        same_columns = len(cells) == len(next_cells)
        continued_cell = (len(next_cells) < len(cells) and
                          re.match(r"^[a-zà-ÿ]", next_cells[-1].lstrip()) and
                          not cells[-1].rstrip().endswith((".", ";", ":", "!", "?")))
        if not same_columns and not continued_cell:
            continue
        if any(cell.strip() and not cell.rstrip().endswith((".", ";", ":", "!", "?"))
               for cell in cells[1:][-2:]):
            link_elements(previous["id"], current["id"])
    for parent, note in footnote_links(chunks, elements):
        link_elements(parent["element_id"], note["element_id"])
    groups = defaultdict(list)
    for index, chunk in enumerate(chunks):
        groups[root(index)].append(chunk)
    batches, pending = [], []
    for group in groups.values():
        if pending and len(pending) + len(group) > batch_size:
            batches.append(pending)
            pending = []
        pending.extend(group)
        if len(pending) >= batch_size:
            batches.append(pending)
            pending = []
    if pending:
        batches.append(pending)
    return batches
