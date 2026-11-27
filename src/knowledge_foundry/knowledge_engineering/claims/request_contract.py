"""Evidence-bound request construction and validation for claim generation and classification."""
import hashlib
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
from knowledge_foundry.shared.paths import REPOSITORY_ROOT as ROOT
from knowledge_foundry.provenance.evidence import claim_evidence_references
from knowledge_foundry.knowledge_engineering.claims.claim_source_context import supporting_passages, source_context_index, uses_source_context
from knowledge_foundry.knowledge_engineering.structuring.structural_chunks import assemble_structural_chunks
from knowledge_foundry.knowledge_engineering.structuring.structural_links import footnote_links

ARTIFACT_SCHEMA = json.loads((ROOT / "schemas" / "artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))


CANDIDATE_ARTIFACT_SCHEMA = json.loads(
    (ROOT / "schemas" / "claim-candidate-artifact-schema-v1.0.0.json").read_text(encoding="utf-8"))


CANDIDATE_VALIDATION_SCHEMA = json.loads(json.dumps(CANDIDATE_ARTIFACT_SCHEMA).replace(
    "artifact-schema-v1.0.0.json#/$defs/", "#/$defs/"))


CANDIDATE_VALIDATION_SCHEMA["$defs"] = ARTIFACT_SCHEMA["$defs"]


CANDIDATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims", "coverage"],
    "properties": {"claims": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "evidence"],
        "properties": {"text": {"type": "string", "minLength": 1},
                       "evidence": {"type": "array", "minItems": 1, "items": {
                           "type": "string", "pattern": "^[ES][1-9][0-9]*$"}}}}}},
}


CANDIDATE_SCHEMA['properties']['coverage'] = {
    'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['evidence_id', 'status', 'claim_indices', 'reason'],
        'properties': {
            'evidence_id': {'type': 'string', 'pattern': '^E[1-9][0-9]*$'},
            'status': {'type': 'string', 'enum': [
                'represented', 'non_substantive', 'insufficient_context', 'ambiguous']},
            'claim_indices': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}},
            'reason': {'type': 'string', 'minLength': 1}}}}


CLASSIFICATION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["topic_id"],
    "properties": {"topic_id": {"type": ["string", "null"], "minLength": 1}},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _prompt(name):
    value = (ROOT / "src" / "knowledge_foundry" / "prompts" / name).read_text(encoding="utf-8")
    return value, digest(value)


def _passage(ref, element_map):
    element = element_map[ref["element_id"]]
    span = ref["span"]
    if not 0 <= span["start"] < span["end"] <= len(element["text"]):
        raise ValueError("evidence span is out of bounds")
    return {"text": element["text"][span["start"]:span["end"]],
            "kind": element["kind"], "location": element["location"]}


def _evidence_choices(chunks, element_map, structural_chunks=None, support_index=None):
    choices, by_reference = {}, {}
    for chunk in chunks:
        for ref in claim_evidence_references(chunk, element_map):
            if 'span' not in ref:
                continue
            key = canonical(ref)
            if key in by_reference:
                entry = choices[by_reference[key]]
                if chunk['id'] not in entry['original_chunk_ids']:
                    entry['original_chunk_ids'].append(chunk['id'])
            else:
                label = f'E{len(choices) + 1}'
                by_reference[key] = label
                choices[label] = {'chunk_id': chunk['id'], 'original_chunk_ids': [chunk['id']],
                                  'document_id': chunk['document_id'], 'source_id': ref['source_id'],
                                  'reference': ref, **_passage(ref, element_map)}
    labels = {}
    for label, item in choices.items():
        labels.setdefault(canonical(item["reference"]), []).append(label)
    for parent, note in footnote_links(chunks, element_map):
        for parent_label in labels[canonical(parent)]:
            for note_label in labels[canonical(note)]:
                choices[parent_label].setdefault("footnote_evidence", []).append(note_label)
                choices[note_label].setdefault("parent_evidence", []).append(parent_label)
    if structural_chunks is not None:
        allowed = {canonical(item['reference']) for record in structural_chunks
                   for item in record['evidence_memberships'] if 'span' in item['reference']}
        choices = {label: item for label, item in choices.items() if canonical(item['reference']) in allowed}
    if support_index is not None:
        for number, item in enumerate(supporting_passages(chunks, structural_chunks, support_index), 1):
            ref = item['reference']
            choices[f'S{number}'] = {**item, 'chunk_id': item['original_chunk_ids'][0],
                'document_id': ref['document_id'], 'source_id': ref['source_id'],
                **_passage(ref, element_map)}
    return choices


def _candidate_input(chunks, element_map, bundle=None, structural_chunks=None, support_index=None):
    """Render source text once in structural order, with a text-free citation map."""
    if structural_chunks is None:
        structural_chunks, _ = assemble_structural_chunks(
            {'chunks': chunks, 'elements': list(element_map.values())})
    choices = _evidence_choices(chunks, element_map, structural_chunks, support_index)
    labels = {canonical(item['reference']): label for label, item in choices.items()}
    rendered, emitted, contexts = [], {}, {}
    for record in structural_chunks:
        parts, passage_ids = [], []
        memberships = list(record['evidence_memberships'])
        present = {item['reference']['element_id'] for item in memberships}
        for eid in reversed(record.get('context_element_ids', [])):
            if eid not in present:
                element = element_map[eid]
                source_id = record['evidence_memberships'][0]['reference']['source_id']
                memberships.insert(0, {'reference': {'source_id': source_id,
                    'document_id': element['document_id'], 'element_id': eid,
                    'element_revision': element['revision']}, 'original_chunk_ids': []})
        for membership in memberships:
            ref = membership['reference']
            key = canonical(ref)
            label = labels.get(key)
            if label is None:
                if key not in contexts:
                    element = element_map[ref['element_id']]
                    contexts[key] = {'id': f'C{len(contexts) + 1}', 'reference': ref,
                                     'kind': element['kind'], 'location': element['location'],
                                     'original_chunk_ids': []}
                context = contexts[key]
                for cid in membership['original_chunk_ids']:
                    if cid not in context['original_chunk_ids']:
                        context['original_chunk_ids'].append(cid)
                label = context['id']
            passage_ids.append(label)
            # Spanless context and independently citable full-element evidence
            # may share text; retain both identities, but render that location once.
            element = element_map[ref['element_id']]
            span = ref.get('span', {'start': 0, 'end': len(element['text'])})
            text_key = canonical({**ref, 'span': span})
            # If this context has an independently citable exact counterpart,
            # render the text at that E/S passage, even when C comes first.
            # A citable label must not depend on a non-citable C label's text.
            counterpart = labels.get(text_key)
            if label.startswith('C') and counterpart is not None:
                parts.append(f'[{label}] Contexte non citable. Voir [{counterpart}] (même passage source).')
                continue
            if text_key in emitted:
                parts.append(f'[{label}] Voir [{emitted[text_key]}] (même passage source).')
            else:
                emitted[text_key] = label
                qualifier = ' Contexte non citable' if label.startswith('C') else ''
                parts.append(f'[{label}]{qualifier}\n' + element['text'][span['start']:span['end']])
        rendered.append({**{k: record[k] for k in ('id', 'document_id', 'original_chunk_ids')},
                         'passage_ids': passage_ids, 'text': '\n\n'.join(parts)})
    support = []
    for label, item in choices.items():
        if not label.startswith('S'):
            continue
        ref = item['reference']
        key = canonical(ref)
        if key in emitted:
            text = f'[{label}] Voir [{emitted[key]}] (même passage source).'
        else:
            emitted[key] = label
            text = f'[{label}] Appui de source, sans extraction autonome\n' + item['text']
        support.append({'id': label, 'text': text})
    return canonical({'original_chunks': [{'chunk_id': c['id'], 'document_id': c['document_id']}
                                          for c in chunks],
                      'structural_chunks': rendered,
                      'documents': bundle['documents'] if bundle else [],
                      'sources': bundle['sources'] if bundle else [],
                      'eligible_evidence': [{'id': label, **{key: value for key, value in item.items()
                                                            if key not in {'chunk_id', 'text'}}}
                                            for label, item in choices.items()],
                      'non_citable_context': list(contexts.values()),
                      **({'supporting_passages': support} if support_index is not None else {})})


def _classification_input(candidate, chunk_map, element_map, document_map, source_map):
    documents = [document_map[item] for item in sorted({ref["document_id"] for ref in candidate["evidence"]})]
    return {"claim": candidate["text"],
            "evidence": [{**ref, **_passage(ref, element_map)} for ref in candidate["evidence"]],
            "chunks": [{"chunk_id": item, "text": chunk_map[item]["text"]} for item in candidate["chunk_ids"]],
            "sources": [source_map[(doc["source_id"], doc["source_version"])] for doc in documents]}


def _validate_candidate_choices(candidate, choices):
    """Keep support bounded to this request and require primary-cluster evidence."""
    permitted = {canonical(v['reference']) for v in choices.values()}
    primary = {canonical(v['reference']) for k, v in choices.items() if k.startswith('E')}
    selected = {canonical(ref) for ref in candidate['evidence']}
    if not selected <= permitted or not selected.intersection(primary):
        raise ValueError('candidate evidence is outside its request or lacks primary evidence')


def _validate_candidate(candidate, generation_build_id, chunk_map, element_map):
    Draft202012Validator(CANDIDATE_VALIDATION_SCHEMA).validate(candidate)
    if candidate["generation_build_id"] != generation_build_id:
        raise ValueError("candidate claim generation build mismatch")
    chunks = [chunk_map.get(chunk_id) for chunk_id in candidate["chunk_ids"]]
    if not all(chunks):
        raise ValueError("candidate claim chunk missing")
    supporting = {canonical([item]) for chunk in chunks
                  for item in claim_evidence_references(chunk, element_map)}
    selected_evidence = {canonical([ref]) for ref in candidate['evidence']}
    if any(not selected_evidence.intersection(canonical([ref]) for ref in claim_evidence_references(c, element_map))
           for c in chunks):
        raise ValueError('candidate claim chunk membership is not supported by its evidence')
    for evidence in candidate["evidence"]:
        if "span" not in evidence or canonical([evidence]) not in supporting:
            raise ValueError("candidate claim evidence is not supported by its chunks")
    expected = "candidate:" + digest(generation_build_id + "\n" + canonical(candidate["evidence"]) +
                                      "\n" + candidate["text"])
    if candidate["id"] != expected:
        raise ValueError("candidate claim ID mismatch")
