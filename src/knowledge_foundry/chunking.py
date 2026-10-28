"""Deterministic structural chunking of saved parsing artifacts (schema v1.0.0)."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from knowledge_foundry.pdf_tables import table_context_errors


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
PIPELINE_VERSION = "chunking-v1"
CHUNKER_VERSION = "structural-v9"
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
SCHEMA = json.loads((ROOT / "schemas/artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def token_count(text):
    """Reproducible Unicode word/punctuation budget, independent of model tokens."""
    return len(TOKEN.findall(text))


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


def settings_from_config(config):
    value = config.get("chunking", {})
    if not isinstance(value, dict) or set(value) - {"max_tokens"}:
        raise ValueError("chunking must contain only max_tokens")
    maximum = value.get("max_tokens", 256)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 32:
        raise ValueError("chunking.max_tokens must be an integer >= 32")
    return {"max_tokens": maximum}


def _location_key(element):
    location = element["location"]
    if location["format"] == "pdf" and "bbox" in location:
        return ("pdf", location["page"], tuple(location["bbox"]))
    return (element["locator_key"],)


def _reference(element, source_id, span=None):
    ref = {"source_id": source_id, "document_id": element["document_id"],
           "element_id": element["id"], "element_revision": element["revision"]}
    if span is not None:
        ref["span"] = {"start": span[0], "end": span[1]}
    return ref


def same_prose_scope(prior, current, info):
    a, b = info[prior['id']].get('prose_box'), info[current['id']].get('prose_box')
    if a == b:
        return True
    return bool(a and b and b['page'] == a['page'] + 1 and
                all(abs(a['bbox'][edge] - b['bbox'][edge]) <= 3 for edge in (0, 2)))


def _continues_sentence(prior, current, info):
    return bool(prior and current and prior['id'] != current['id'] and
                prior['kind'] in {'paragraph', 'list_item'} and current['kind'] == 'paragraph' and
                prior['location'].get('format') == current['location'].get('format') == 'pdf' and
                current['location'].get('page', 0) - prior['location'].get('page', 0) in {0, 1} and
                info[prior['id']].get('heading_path') == info[current['id']].get('heading_path') and
                same_prose_scope(prior, current, info) and
                not clean(prior['text']).endswith(('.', '!', '?', ';', ':')) and
                re.match(r'^[a-zà-ÿ]', clean(current['text'])))


def prose_assemblies(elements, info):
    """Assemble source continuations while retaining every original span.

    Only detached notes/structural records may intervene. They follow the
    completed body group; both the body and note orders remain independently
    source ordered. The canonical elements themselves are never changed.
    """
    flow = []
    previous = None
    for e in elements:
        if info[e['id']].get('evidence_role') != 'authoritative' or not e['text'].strip():
            continue
        detached = (e['kind'] == 'paragraph' and previous and previous['kind'] == 'footnote'
                    and re.match(r'^\d+\s', e['text']))
        if e['kind'] != 'footnote' and not detached:
            flow.append(e)
        previous = e
    groups, owner, label_roots = {}, {}, set()
    for prior, current in zip(flow, flow[1:]):
        same_path = info[prior['id']].get('heading_path') == info[current['id']].get('heading_path')
        a, b = prior['location'], current['location']
        same_box = same_prose_scope(prior, current, info)
        continuation = _continues_sentence(prior, current, info)
        label = (prior['kind'] == 'list_item' and token_count(prior['text']) <= 32 and
                 re.match(r'^[a-z][.)]\s', prior['text']) and prior['text'].rstrip().endswith(':')
                 and re.match(r'^\d+[.)]\s', current['text']))
        box_group = (info[prior['id']].get('prose_box') is not None and
                     prior['kind'] in {'paragraph', 'list_item'} and current['kind'] in {'paragraph', 'list_item'})
        if (same_path and same_box and (continuation or label or box_group) and a.get('format') == b.get('format') == 'pdf'
                and b.get('page', 0) - a.get('page', 0) in {0, 1}):
            root = owner.get(prior['id'], prior['id'])
            if label:
                label_roots.add(root)
            groups.setdefault(root, [prior]).append(current)
            owner[current['id']] = root
    working, members, rank = [], {}, {}
    for element in elements:
        if element['id'] in owner:
            continue
        group = groups.get(element['id'], [element])
        members[element['id']] = []
        offset = 0
        for member in group:
            members[element['id']].append((offset, offset + len(member['text']), member))
            offset += len(member['text']) + 1
            rank[member['id']] = len(rank)
        working.append({**element, 'text': ' '.join(e['text'] for e in group),
                        'location': group[1]['location'] if element['id'] in label_roots else element['location']})
    return working, members, rank


def render_passages(passages, refs, elements, info):
    """Join only witnessed fragments of the same source sentence with spaces."""
    output = ''
    for index, (part, ref) in enumerate(zip(passages, refs)):
        separator = '\n\n'
        if index:
            prior = elements.get(refs[index - 1]['element_id'])
            current = elements.get(ref['element_id'])
            if _continues_sentence(prior, current, info):
                separator = ' '
        output += (separator if index else '') + part
    return output


def _chunk_document(bundle, sidecar, build_id, settings):
    """Group complete structural units; max_tokens is a packing target.

    Never split a table, list, paragraph or witnessed page continuation merely
    to satisfy a model budget. Consumers fail explicitly on oversized units.
    """
    from knowledge_foundry.document_structure import assemble_document_hierarchy, ancestor_ids, structure_snapshot
    document = bundle['documents'][0]
    info = {row['element_id']: row for row in sidecar['elements']}
    elements = {e['id']: e for e in bundle['elements']}
    if set(info) != set(elements) or len(info) != len(sidecar['elements']):
        raise ValueError('sidecar elements do not match bundle')
    if [e['ordinal'] for e in bundle['elements']] != list(range(len(elements))):
        raise ValueError('elements out of reading order')
    if errors := table_context_errors(bundle['elements'], info):
        raise ValueError('invalid table continuation: ' + str(errors[0]))
    hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle, structure_snapshot(sidecar['elements']))}
    metrics = {name: sum(info[eid].get('evidence_role') == role for eid in elements)
               for name, role in [('authoritative_elements', 'authoritative'), ('structural_elements', 'structural'),
                                  ('auxiliary_elements', 'auxiliary')]}
    metrics.update(exact_duplicates_removed=0, repeated_text_distinct_locations=0, chunks=0)
    eligible, seen = [], set()
    for e in bundle['elements']:
        if info[e['id']].get('evidence_role') != 'authoritative' or not clean(e['text']):
            continue
        key = (_location_key(e), e['kind'], clean(e['text'], table=e['kind'] == 'table'))
        if key in seen:
            metrics['exact_duplicates_removed'] += 1
            continue
        seen.add(key)
        eligible.append(e['id'])
    source_id = document['source_id']
    chunks = []

    def emit(refs, text=None, version=CHUNKER_VERSION):
        if text is None:
            text = render_passages([clean(elements[r['element_id']]['text'][r['span']['start']:r['span']['end']]
                    if 'span' in r else elements[r['element_id']]['text'],
                    table=elements[r['element_id']]['kind'] == 'table') for r in refs], refs, elements, info)
        if not text.strip() or not refs:
            raise ValueError('a structural chunk requires text and source evidence')
        chunk = {'schema_version': SCHEMA_VERSION, 'document_id': document['id'], 'build_id': build_id,
                 'chunker_version': version, 'sequence': len(chunks), 'text': text, 'evidence': refs}
        chunk['id'] = 'chunk:' + digest(document['id'] + '\n' + version + '\n' + canonical(refs) + '\n' + text)
        chunks.append(chunk)

    if 'provider_chunks' in sidecar:
        keys = {e['locator_key']: e for e in elements.values()}
        for row in sidecar['provider_chunks']:
            refs = []
            for r in row['evidence']:
                e = keys[r['locator_key']]
                span = r.get('span', {'start': 0, 'end': len(e['text'])})
                if not 0 <= span['start'] < span['end'] <= len(e['text']):
                    raise ValueError('provider chunk span is outside its element')
                refs.append(_reference(e, source_id, (span['start'], span['end'])))
            emit(refs, row['text'], version='provider-' + digest(canonical(sidecar.get('provider', 'external')))[:12])
        metrics['chunks'] = len(chunks)
        return chunks, metrics

    parents = {eid: eid for eid in eligible}
    def root(eid):
        while parents[eid] != eid:
            parents[eid] = parents[parents[eid]]
            eid = parents[eid]
        return eid
    sections = {eid: tuple(parent for parent in ancestor_ids(eid, hierarchy)
                           if elements[parent]['kind'] == 'heading') for eid in eligible}
    positions = {eid: i for i, eid in enumerate(eligible)}
    def join(a, b):
        if a in parents and b in parents and sections[a] == sections[b]:
            left, right = sorted((positions[a], positions[b]))
            # A stale parser parent can reconnect an earlier rule after a
            # different section. Do not pull its later items ahead of that
            # intervening body; detached notes may still be crossed.
            if any(elements[eid]['kind'] != 'footnote' and sections[eid] != sections[a]
                   for eid in eligible[left:right+1]):
                return
            parents[root(b)] = root(a)

    # Reuse source-proven sentence and framed-prose continuation detection.
    _, assemblies, _ = prose_assemblies(bundle['elements'], info)
    for group in assemblies.values():
        for _, _, e in group[1:]:
            join(group[0][2]['id'], e['id'])
    for eid in eligible:
        e = elements[eid]
        parent = hierarchy[eid]['parent_element_id']
        if e['kind'] == 'list_item':
            # Scope comes from hierarchy/geometry; never repeat a cached lead
            # after a peer definition has closed it.
            for ancestor in reversed(ancestor_ids(eid, hierarchy)):
                if hierarchy[ancestor]['role'] in {'list_introduction', 'numbered_paragraph'}:
                    join(ancestor, eid)
                elif elements[ancestor]['kind'] == 'heading':
                    break
        if e['kind'] == 'caption' and parent in elements and elements[parent]['kind'] == 'figure':
            for other in eligible:
                if hierarchy[other]['parent_element_id'] == parent:
                    join(eid, other)
        if e['kind'] == 'table' and info[eid].get('table_continuation'):
            join(info[eid]['table_continuation']['previous_table_id'], eid)
    # Bare lists with no introductory paragraph remain complete local lists.
    body = [eid for eid in eligible if elements[eid]['kind'] != 'footnote']
    for a, b in zip(body, body[1:]):
        if (elements[a]['kind'] == elements[b]['kind'] == 'list_item'
                and hierarchy[a]['role'] != 'numbered_paragraph'
                and hierarchy[b]['role'] != 'numbered_paragraph'
                and hierarchy[a]['parent_element_id'] == hierarchy[b]['parent_element_id']):
            join(a, b)
    # A complete list also owns intervening explanatory prose. Keep source
    # order rather than moving its later bullets before that prose.
    provisional = {}
    for eid in eligible:
        provisional.setdefault(root(eid), []).append(eid)
    for group in provisional.values():
        if len(group) > 1:
            left, right = min(positions[eid] for eid in group), max(positions[eid] for eid in group)
            for eid in eligible[left:right+1]:
                if elements[eid]['kind'] != 'footnote':
                    join(group[0], eid)
    groups = {}
    for eid in eligible:
        groups.setdefault(root(eid), []).append(eid)
    groups = sorted(groups.values(), key=lambda ids: min(elements[eid]['ordinal'] for eid in ids))
    pending, pending_section = [], None
    def flush():
        nonlocal pending
        if pending:
            emit(pending)
            pending = []
    for group in groups:
        group.sort(key=lambda eid: elements[eid]['ordinal'])
        kinds = {elements[eid]['kind'] for eid in group}
        governing = [eid for eid in ancestor_ids(group[0], hierarchy) if elements[eid]['kind'] == 'heading']
        refs = [_reference(elements[eid], source_id) for eid in governing if eid not in group]
        refs += [_reference(elements[eid], source_id, (0, len(elements[eid]['text']))) for eid in group]
        atomic = bool(kinds & {'table', 'list_item', 'footnote', 'caption'}) or len(group) > 1
        size = sum(token_count(elements[r['element_id']]['text']) for r in refs)
        heading_lead = bool(pending) and all(elements[r['element_id']]['kind'] == 'heading'
                and r['element_id'] in governing for r in pending if 'span' in r) and any('span' in r for r in pending)
        if pending and not heading_lead and (atomic or pending_section != governing or size + sum(
                token_count(elements[r['element_id']]['text']) for r in pending) > settings['max_tokens']):
            flush()
        if not pending:
            pending = refs
        else:
            seen_refs = {canonical(r) for r in pending}
            body_ids = {r['element_id'] for r in pending if 'span' in r}
            pending.extend(r for r in refs if canonical(r) not in seen_refs and (
                'span' in r or r['element_id'] not in body_ids))
        pending_section = governing
        if atomic:
            flush()
    flush()
    metrics['chunks'] = len(chunks)
    metrics['oversized_structural_units'] = sum(token_count(c['text']) > settings['max_tokens'] for c in chunks)
    return chunks, metrics


def build_collection(config_path, input_dir, output_dir):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    settings = settings_from_config(config)
    input_dir = Path(input_dir).resolve()
    paths = sorted(input_dir.glob("documents/*/artifact-bundle.json"))
    if not paths:
        raise ValueError(f"no parsed bundles in {input_dir}")
    ordered = []
    for path in paths:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        sidecar = json.loads(path.with_name("parse.json").read_text(encoding="utf-8"))
        Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(bundle)
        if len(bundle["sources"]) != 1 or len(bundle["documents"]) != 1 or any(bundle[key] for key in ("chunks", "claims", "topics", "builds")):
            raise ValueError(f"expected one parsed-document bundle: {path}")
        if sidecar["document_id"] != bundle["documents"][0]["id"] or path.parent.name != bundle["documents"][0]["id"].removeprefix("doc:"):
            raise ValueError(f"sidecar or folder document mismatch: {path}")
        ordered.append((bundle, sidecar, path.parent))
    report_path = input_dir / 'report.json'
    if report_path.exists():
        parsed_report = json.loads(report_path.read_text())
        outcomes = parsed_report['outcomes']
        if {row['manifest_id'] for row in outcomes} != set(config['manifest_ids']) or any(
                row['status'] not in {'parsed', 'partial'} for row in outcomes):
            raise ValueError('parsed collection is incomplete for configured manifest_ids')
        document_order = [row['document_id'] for row in outcomes]
        rank = {did: i for i, did in enumerate(document_order)}
        if len(ordered) != len(rank) or {row[0]['documents'][0]['id'] for row in ordered} != set(rank):
            raise ValueError('parsed directory contains missing or stale document editions; use a fresh directory')
        ordered.sort(key=lambda row: rank[row[0]['documents'][0]['id']])
    selected_ids = config['manifest_ids']
    actual_ids = [sidecar.get('metadata_origins', {}).get('manifest_id', {}).get('value')
                  for _, sidecar, _ in ordered]
    if all(actual_ids) and set(actual_ids) != set(selected_ids):
        raise ValueError('parsed documents do not match configured manifest_ids')
    ids = [bundle["documents"][0]["id"] for bundle, _, _ in ordered]
    config_sha = digest(canonical({"chunking": settings, "chunker_version": CHUNKER_VERSION}))
    build_id = "build:" + digest(PIPELINE_VERSION + "\n" + config_sha + "\n" + "\n".join(sorted(ids)))
    result = {"schema_version": SCHEMA_VERSION, "sources": [], "documents": [],
              "elements": [], "chunks": [], "claims": [], "topics": [], "builds": []}
    figures = []
    structure_elements = []
    document_views = {}
    report = {"schema_version": SCHEMA_VERSION, "build_id": build_id,
              "settings": settings, "documents": []}
    for bundle, sidecar, folder in ordered:
        structure_elements.extend(sidecar['elements'])
        document = bundle["documents"][0]
        result["sources"].extend(bundle["sources"])
        result["documents"].extend(bundle["documents"])
        result["elements"].extend(bundle["elements"])
        chunks, metrics = _chunk_document(bundle, sidecar, build_id, settings)
        result["chunks"].extend(chunks)
        document_report = {"source_id": bundle["sources"][0]["id"],
                           "document_id": document["id"], **metrics}
        report["documents"].append(document_report)
        document_figures = []
        for item in sidecar["elements"]:
            asset = item.get("asset")
            if asset:
                element = next(e for e in bundle["elements"] if e["id"] == item["element_id"])
                if element["kind"] != "figure" or asset["representation_role"] != "authoritative_visual":
                    raise ValueError(f"invalid figure asset: {item['element_id']}")
                file = folder / asset["path"]
                if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != asset["sha256"]:
                    raise ValueError(f"figure asset hash mismatch: {file}")
                link = {"source_id": bundle["sources"][0]["id"],
                        "document_id": document["id"],
                        "element_id": element["id"], "element_revision": element["revision"],
                        "location": element["location"], "asset": {**asset, "path": str(file.relative_to(input_dir))}}
                copied = Path(output_dir) / link['asset']['path']
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, copied)
                figures.append(link)
                document_figures.append(link)
        document_views[document["id"]] = {
            "view_version": 1, "build_id": build_id,
            "source": bundle["sources"][0], "document": document,
            "elements": bundle["elements"], "chunks": chunks,
            "figure_links": document_figures, "report": document_report,
        }
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id,
                             "pipeline_version": PIPELINE_VERSION, "config_sha256": config_sha,
                             "input_document_ids": ids,
                             "output_chunk_ids": [item["id"] for item in result["chunks"]],
                             "output_claim_ids": [], "output_topic_ids": [], "status": "complete"})
    from scripts.validate_artifact_examples import check
    check(result)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (("artifact-bundle.json", result), ("figure-links.json", figures), ("report.json", report)):
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from knowledge_foundry.document_structure import structure_snapshot, validate_structure
    structure = structure_snapshot(structure_elements)
    validate_structure(result, structure)
    (output_dir / 'document-structure.json').write_text(
        json.dumps(structure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    views_dir = output_dir / "documents"
    for document_id, view in document_views.items():
        folder = views_dir / document_id.removeprefix("doc:")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "document-view.json").write_text(
            json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current_folders = {document_id.removeprefix("doc:") for document_id in document_views}
    for stale in sorted(views_dir.glob("*/document-view.json")):
        if stale.parent.name not in current_folders:
            stale.unlink()
            try:
                stale.parent.rmdir()
            except OSError:
                pass  # Leave a folder containing files that this stage does not own.
    from knowledge_foundry.document_structuring import freeze_structure
    freeze_structure(output_dir, input_dir)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "artifacts/parsed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/chunked")
    args = parser.parse_args(argv)
    try:
        report = build_collection(args.config, args.input_dir, args.output_dir)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"chunking failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
