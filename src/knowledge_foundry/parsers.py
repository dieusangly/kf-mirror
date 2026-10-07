"""Format adapters producing source-located blocks for Task 4."""

import re
from pathlib import Path


NUMBERED = re.compile(r"^\s*((?:\d+[°.)]|[IVX]+\.)\s*)")


def _item(kind, text, locator_key, location, *, parent_key=None, details=None):
    return {
        "kind": kind,
        "text": text.strip(),
        "locator_key": locator_key,
        "location": location,
        "parent_key": parent_key,
        "details": details or {},
    }


def _css_path(node):
    """Produce an unambiguous CSS path in the saved DOM."""
    parts = []
    while node is not None and isinstance(node.tag, str):
        parent = node.getparent()
        siblings = [child for child in parent] if parent is not None else [node]
        same = [child for child in siblings if child.tag == node.tag]
        part = node.tag
        if len(same) > 1:
            part += f":nth-of-type({same.index(node) + 1})"
        parts.append(part)
        node = parent
    return " > ".join(reversed(parts))


def parse_html(path, source, options):
    from lxml import html

    document = html.parse(str(path))
    root = document.getroot()
    selector = options.get("content_selector", "main")
    matches = root.cssselect(selector)
    if len(matches) != 1:
        raise ValueError(f"HTML content selector {selector!r} matched {len(matches)} nodes; expected one")
    content = matches[0]
    article_id = options.get("article_id") or source["version"]
    items = []
    headings = []
    heading_keys = []

    def add(node, kind, text, parent_key=None, details=None):
        text = " ".join(text.split())
        if not text:
            return None
        css = _css_path(node)
        selected = root.cssselect(css)
        if len(selected) != 1 or selected[0] is not node:
            raise ValueError(f"HTML element selector is not unique: {css}")
        location = {"format": "html", "article_id": article_id, "selector": css}
        match = NUMBERED.match(text)
        if match:
            location["clause"] = match.group(1).strip()
        if node.get("id"):
            location["anchor"] = node.get("id")
        key = f"html:{article_id}:{css}"
        if parent_key is None and heading_keys:
            parent_key = heading_keys[-1]
        items.append(_item(kind, text, key, location, parent_key=parent_key,
                           details={"heading_path": list(headings), **(details or {})}))
        return key

    def visit(node):
        if not isinstance(node.tag, str) or node.tag in {"script", "style", "nav", "aside", "form", "button"}:
            return
        tag = node.tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            del headings[level - 1:]
            del heading_keys[level - 1:]
            text = " ".join(node.itertext())
            headings.append(" ".join(text.split()))
            key = add(node, "heading", text, details={"level": level})
            if key:
                heading_keys.append(key)
            return
        if tag == "table":
            rows = []
            for row in node.xpath(".//tr"):
                cells = row.xpath("./th|./td")
                rows.append([" ".join(" ".join(cell.itertext()).split()) for cell in cells])
            table_key = add(node, "table", "\n".join(" | ".join(row) for row in rows),
                            details={"rows": len(rows)})
            for row_index, row in enumerate(node.xpath(".//tr")):
                for cell_index, cell in enumerate(row.xpath("./th|./td")):
                    add(cell, "table_cell", " ".join(cell.itertext()), parent_key=table_key,
                        details={"row": row_index, "column": cell_index,
                                 "header": cell.tag.lower() == "th",
                                 "header_origin": "html_th" if cell.tag.lower() == "th" else "html_td",
                                 "row_span": int(cell.get("rowspan", "1")),
                                 "col_span": int(cell.get("colspan", "1"))})
            return
        if tag == "li":
            text = " ".join(node.xpath("./text() | ./p//text()"))
            add(node, "list_item", text)
            for child in node:
                if child.tag in {"ul", "ol", "table"}:
                    visit(child)
            return
        if tag in {"dt", "dd"}:
            add(node, "list_item", " ".join(node.itertext()))
            return
        if tag == "p":
            add(node, "paragraph", " ".join(node.itertext()))
            return
        if tag == "pre":
            add(node, "paragraph", "".join(node.itertext()), details={"html_type": "pre"})
            return
        if tag == "figcaption":
            add(node, "caption", " ".join(node.itertext()))
            return
        for child in node:
            visit(child)

    if content.tag.lower() == "article":
        # Saved article pages contain controls and related links after the body.
        for heading in content.xpath(".//h1|.//h2"):
            if "article_title" in heading.get("class", "").split():
                visit(heading)
        for body in content.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' content ')]"):
            if "fr-text--md" in body.get("class", "").split():
                visit(body)
    else:
        visit(content)
    if not items:
        raise ValueError("HTML content selector produced no text blocks")
    return items, None, []


def parse_markdown(path, _source, options):
    from markdown_it import MarkdownIt

    text = path.read_text(encoding="utf-8-sig")
    parser = MarkdownIt("commonmark")
    if options.get("tables", True):
        parser.enable("table")
    tokens = parser.parse(text)
    items = []
    heading_path = []
    heading_keys = []
    index = 0
    list_depth = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "list_item_open":
            list_depth += 1
        elif token.type == "list_item_close":
            list_depth -= 1
        if token.type in {"heading_open", "paragraph_open", "list_item_open"}:
            # Paragraphs inside list items are emitted by their enclosing list item.
            if token.type == "paragraph_open" and (token.hidden or list_depth):
                index += 1
                continue
            body = tokens[index + 1] if index + 1 < len(tokens) else token
            if token.type == "list_item_open":
                content = []
                depth = 1
                cursor = index + 1
                while cursor < len(tokens) and depth:
                    if tokens[cursor].type == "list_item_open":
                        depth += 1
                    elif tokens[cursor].type == "list_item_close":
                        depth -= 1
                    elif depth == 1 and tokens[cursor].type == "inline":
                        content.append(tokens[cursor].content)
                    cursor += 1
                body_text = " ".join(content)
                if token.map:
                    source_line = text.splitlines()[token.map[0]]
                    marker = re.match(r"^\s*((?:\d+[.)]|[-+*]))\s+", source_line)
                    if marker:
                        body_text = f"{marker.group(1)} {body_text}"
                kind = "list_item"
            else:
                body_text = body.content
                kind = "heading" if token.type == "heading_open" else "paragraph"
            if kind == "heading":
                level = int(token.tag[1])
                del heading_path[level - 1:]
                del heading_keys[level - 1:]
                heading_path.append(body_text)
            if token.map and body_text.strip():
                start, end = token.map
                key = f"markdown:line-{start + 1}:block-{len(items)}"
                items.append(_item(kind, body_text, key,
                                   {"format": "markdown", "heading_path": list(heading_path),
                                    "line_start": start + 1, "line_end": end},
                                   parent_key=heading_keys[-1] if heading_keys else None,
                                   details={"level": int(token.tag[1])} if kind == "heading" else {}))
                if kind == "heading":
                    heading_keys.append(key)
        elif token.type == "table_open" and token.map:
            start, end = token.map
            key = f"markdown:line-{start + 1}:table"
            rows = []
            cells = []
            row = -1
            col = 0
            header = False
            for child in tokens[index + 1:]:
                if child.type == "table_close":
                    break
                if child.type == "thead_open":
                    header = True
                elif child.type == "thead_close":
                    header = False
                elif child.type == "tr_open":
                    row += 1
                    col = 0
                    rows.append([])
                elif child.type in {"th_open", "td_open"}:
                    cell_text = child.content
                    cells.append((row, col, header, cell_text))
                    col += 1
                elif child.type == "inline" and cells:
                    r, c, h, _ = cells[-1]
                    cells[-1] = (r, c, h, child.content)
                    rows[r].append(child.content)
            location = {"format": "markdown", "heading_path": list(heading_path),
                        "line_start": start + 1, "line_end": end}
            items.append(_item("table", "\n".join(" | ".join(r) for r in rows), key,
                               location, parent_key=heading_keys[-1] if heading_keys else None,
                               details={"rows": len(rows)}))
            for row_index, col_index, header, cell_text in cells:
                cell_line = min(end, start + (1 if row_index == 0 else row_index + 2))
                cell_location = {**location, "line_start": cell_line, "line_end": cell_line}
                items.append(_item("table_cell", cell_text,
                                   f"{key}:r{row_index}:c{col_index}", cell_location,
                                   parent_key=key,
                                   details={"row": row_index, "column": col_index,
                                            "header": header, "header_origin": "markdown_header_row",
                                            "row_span": 1, "col_span": 1}))
        elif token.type in {"fence", "code_block", "html_block"} and token.map and token.content.strip():
            start, end = token.map
            key = f"markdown:line-{start + 1}:block-{len(items)}"
            items.append(_item("paragraph", token.content, key,
                               {"format": "markdown", "heading_path": list(heading_path),
                                "line_start": start + 1, "line_end": end},
                               parent_key=heading_keys[-1] if heading_keys else None,
                               details={"markdown_type": token.type, "language": token.info.strip()}))
        index += 1
    return items, None, []


def parse_docx(path, _source, _options):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    items = []
    headings = []
    heading_keys = []
    table_index = 0

    def add_table(table, block_index, parent_key):
        nonlocal table_index
        this_table = table_index
        table_index += 1
        key = f"docx:block-{block_index}:table-{this_table}"
        location = {"format": "docx", "block_index": block_index,
                    "table_index": this_table}
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        items.append(_item("table", "\n".join(" | ".join(row) for row in rows),
                           key, location, parent_key=parent_key,
                           details={"rows": len(rows), "heading_path": list(headings)}))
        cell_anchors = {}
        for row_index, row in enumerate(table.rows):
            seen = set()
            for col_index, cell in enumerate(row.cells):
                cell_identity = cell._tc
                if cell_identity in seen:
                    continue
                seen.add(cell_identity)
                if cell_identity in cell_anchors:
                    items[cell_anchors[cell_identity]]["details"]["row_span"] += 1
                    continue
                cell_location = {**location, "row_index": row_index, "cell_index": col_index}
                cell_key = f"{key}:r{row_index}:c{col_index}"
                items.append(_item("table_cell", cell.text, cell_key,
                                   cell_location, parent_key=key,
                                   details={"row": row_index, "column": col_index,
                                            "header": row_index == 0,
                                            "header_origin": "first_row_inference",
                                            "row_span": 1, "col_span": cell.grid_span,
                                            "paragraphs": [part.text for part in cell.iter_inner_content()
                                                           if isinstance(part, Paragraph)]}))
                cell_anchors[cell_identity] = len(items) - 1
                for part in cell.iter_inner_content():
                    if isinstance(part, Table):
                        add_table(part, block_index, cell_key)

    for block_index, block in enumerate(document.iter_inner_content()):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = block.style.name if block.style is not None else ""
            match = re.match(r"Heading\s+([1-6])$", style, flags=re.I)
            kind = "heading" if match else ("list_item" if "list" in style.lower() else "paragraph")
            if match:
                level = int(match.group(1))
                del headings[level - 1:]
                del heading_keys[level - 1:]
                headings.append(text)
            location = {"format": "docx", "block_index": block_index}
            key = f"docx:block-{block_index}"
            items.append(_item(kind, text, key, location,
                               parent_key=heading_keys[-1] if heading_keys else None,
                               details={"style": style, "heading_path": list(headings)}))
            if match:
                heading_keys.append(key)
            continue
        add_table(block, block_index, heading_keys[-1] if heading_keys else None)
    return items, None, []


def parse_pdf(path, _source, options):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline = PdfPipelineOptions()
    pipeline.do_table_structure = options.get("table_structure", True)
    pipeline.table_structure_options.mode = options.get("table_mode", "accurate")
    if options.get("artifacts_path"):
        pipeline.artifacts_path = Path(options["artifacts_path"])
    ocr = options.get("ocr", {})
    mode = ocr.get("mode", "auto")
    pipeline.do_ocr = mode != "off"
    if pipeline.do_ocr:
        pipeline.ocr_options = TesseractCliOcrOptions(
            mode="full_page" if mode == "force" else "pdf_aware_layout_regions",
            lang=ocr.get("languages", ["fra", "eng"]),
            scale=ocr.get("scale", 3.0),
            psm=ocr.get("psm", 3),
            tesseract_cmd=ocr.get("tesseract_cmd", "tesseract"),
            path=ocr.get("tessdata_path"),
        )
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)})
    result = converter.convert(path)
    if str(result.status).lower().split(".")[-1] != "success":
        raise ValueError(f"Docling conversion status: {result.status}")
    doc = result.document
    items = []
    warnings = []
    page_counts = {}
    heading_stack = []
    for item, level in doc.iterate_items(with_groups=False):
        label = str(getattr(item, "label", "")).lower()
        if "section_header" in label or "title" in label:
            kind = "heading"
        elif "list_item" in label:
            kind = "list_item"
        elif "table" in label:
            kind = "table"
        elif "picture" in label:
            kind = "figure"
        elif "caption" in label:
            kind = "caption"
        elif "footnote" in label:
            kind = "footnote"
        else:
            kind = "paragraph"
        if kind == "table":
            text = item.export_to_markdown(doc=doc) if hasattr(item, "export_to_markdown") else ""
        else:
            text = getattr(item, "text", "") or ""
        marker = getattr(item, "marker", None)
        if kind == "list_item" and marker:
            text = f"{marker} {text}"
        prov = getattr(item, "prov", []) or []
        if not prov:
            if text.strip():
                warnings.append({"code": "unlocated_pdf_block", "message": text[:120]})
            continue
        if kind == "table" and len(prov) > 1:
            warnings.append({"code": "multi_page_table", "message": "table omitted until its cells can be assigned to pages"})
            continue
        for provenance in prov:
            page = provenance.page_no
            page_counts[page] = page_counts.get(page, 0) + 1
            position = page_counts[page]
            numbered = NUMBERED.match(text)
            printed = numbered.group(1).strip() if numbered else None
            location = {"format": "pdf", "page": page,
                        "label": printed or f"block {position}"}
            bbox = getattr(provenance, "bbox", None)
            if bbox is not None:
                try:
                    box = bbox.to_top_left_origin(doc.pages[page].size.height)
                    location["bbox"] = [box.l, box.t, box.r, box.b]
                except (AttributeError, TypeError, KeyError):
                    pass
            key = f"pdf:p{page}:block-{position}"
            heading_level = max(1, int(getattr(item, "level", 1) or 1)) if kind == "heading" else None
            if heading_level is not None:
                while heading_stack and heading_stack[-1][0] >= heading_level:
                    heading_stack.pop()
            parent_key = heading_stack[-1][1] if heading_stack else None
            items.append(_item(kind, text, key, location,
                               parent_key=parent_key,
                               details={"docling_ref": getattr(item, "self_ref", None),
                                        "docling_parent_ref": getattr(getattr(item, "parent", None), "cref", None),
                                        "level": heading_level,
                                        "heading_path": [entry[2] for entry in heading_stack] +
                                                        ([text] if kind == "heading" else []),
                                        "docling_group_depth": level,
                                        "provenance": "ocr_or_pdf_text" if pipeline.do_ocr else "pdf_text"}))
            if kind == "heading":
                heading_stack.append((heading_level, key, text))
            if kind == "table" and getattr(item, "data", None) is not None:
                for cell in item.data.table_cells:
                    row = cell.start_row_offset_idx
                    col = cell.start_col_offset_idx
                    cell_location = dict(location)
                    cell_bbox = getattr(cell, "bbox", None)
                    if cell_bbox is not None:
                        try:
                            box = cell_bbox.to_top_left_origin(doc.pages[page].size.height)
                            cell_location["bbox"] = [box.l, box.t, box.r, box.b]
                        except (AttributeError, TypeError, KeyError):
                            cell_location.pop("bbox", None)
                    items.append(_item("table_cell", cell.text,
                                       f"{key}:r{row}:c{col}", cell_location,
                                       parent_key=key,
                                       details={"row": row, "column": col,
                                                "row_span": cell.row_span,
                                                "col_span": cell.col_span,
                                                "header": cell.column_header or cell.row_header,
                                                "header_origin": "docling_table_structure"}))
    return items, doc.export_to_dict(), warnings
