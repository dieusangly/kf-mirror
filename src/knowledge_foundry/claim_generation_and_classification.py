"""Generate grounded candidate claims and classify them into draft topics."""

import copy
import hashlib
import json
import os
import shutil
import time
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from knowledge_foundry.claim_context import (
    execution_settings,
    load_preparation,
    pricing_for_step,
    request_for_members,
    validate_settings,
)
from knowledge_foundry.evidence import claim_evidence_references
from knowledge_foundry.generation_context import RequestCounter, footnote_links, generation_payload
from knowledge_foundry.openai_tools import DEFAULT_MODEL, OpenAIResponsesTool
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
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
                           "type": "string", "pattern": "^E[1-9][0-9]*$"}}}}}},
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


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_jsonl_atomic(path, records):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(canonical(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _prompt(name):
    value = (ROOT / "src" / "knowledge_foundry" / "prompts" / name).read_text(encoding="utf-8")
    return value, digest(value)


def _config(path):
    value = _read_json(path)
    validate_settings(value)
    return value


def _taxonomy(path):
    value = _read_json(path)
    if set(value) != {"taxonomy_version", "topics"} or not isinstance(value["taxonomy_version"], str):
        raise ValueError("topic taxonomy has an invalid shape")
    topics = value["topics"]
    if not isinstance(topics, list) or not topics:
        raise ValueError("topic taxonomy must contain topics")
    ids = []
    for topic in topics:
        if set(topic) != {"id", "title", "scope"} or not all(isinstance(topic[key], str) and topic[key]
                                                                  for key in topic):
            raise ValueError("topic taxonomy entries require id, title, and scope")
        if not topic["id"].startswith("topic:"):
            raise ValueError("topic IDs must start with topic:")
        ids.append(topic["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("topic taxonomy has duplicate IDs")
    return value


class FixtureProvider:
    """Deterministic adapter used by tests and offline demonstration builds."""

    model_id = "fixture-model"

    def __init__(self, fixture):
        self.fixture = fixture
        self.usage_records = []

    def candidates(self, chunk_ids, _prompt_text, _schema):
        key = ",".join(chunk_ids)
        return self.fixture.get("candidate_clusters", self.fixture.get("candidate_batches", {})).get(key, {"claims": []})

    def classify(self, context, _taxonomy, _prompt_text, _schema):
        text = context["claim"]
        try:
            return {"topic_id": self.fixture["classifications"][text]}
        except KeyError as exc:
            raise ValueError(f"fixture has no classification for claim: {text}") from exc


class OpenAIProvider:
    def __init__(self, model, reasoning_effort, max_output_tokens=None):
        self.tool = OpenAIResponsesTool(model=model or DEFAULT_MODEL, reasoning_effort=reasoning_effort,
                                        max_output_tokens=max_output_tokens)
        self.model_id = self.tool.model

    @property
    def usage_records(self):
        return self.tool.usage_records

    def candidates(self, input_text, prompt_text, schema):
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="claim_candidates", schema=schema)

    def candidate_request_characters(self, input_text, prompt_text, schema):
        return self.tool.request_characters(instructions=prompt_text, input_text=input_text,
                                            schema_name="claim_candidates", schema=schema)

    def classify(self, context, taxonomy, prompt_text, schema):
        input_text = canonical({**context, "topics": taxonomy["topics"]})
        return self.tool.json_response(instructions=prompt_text, input_text=input_text,
                                       schema_name="topic_classification", schema=schema)


def _passage(ref, element_map):
    element = element_map[ref["element_id"]]
    span = ref["span"]
    if not 0 <= span["start"] < span["end"] <= len(element["text"]):
        raise ValueError("evidence span is out of bounds")
    return {"text": element["text"][span["start"]:span["end"]],
            "kind": element["kind"], "location": element["location"]}


def _evidence_choices(chunks, element_map, structural_chunks=None):
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
    return choices


def _candidate_input(chunks, element_map, bundle=None, structural_chunks=None):
    """Render source text once in structural order, with a text-free citation map."""
    if structural_chunks is None:
        structural_chunks, _ = assemble_structural_chunks(
            {'chunks': chunks, 'elements': list(element_map.values())})
    choices = _evidence_choices(chunks, element_map, structural_chunks)
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
            # render the text at that E passage, even when C is encountered first.
            # Never make an E label depend on a non-citable C label's text.
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
    return canonical({'original_chunks': [{'chunk_id': c['id'], 'document_id': c['document_id']}
                                          for c in chunks],
                      'structural_chunks': rendered,
                      'documents': bundle['documents'] if bundle else [],
                      'sources': bundle['sources'] if bundle else [],
                      'eligible_evidence': [{'id': label, **{key: value for key, value in item.items()
                                                            if key not in {'chunk_id', 'text'}}}
                                            for label, item in choices.items()],
                      'non_citable_context': list(contexts.values())})


def _derive_coverage_links(response):
    """Derive mechanical links without changing model coverage judgments."""
    return {**response, 'coverage': [
        {**row, 'claim_indices': [i for i, claim in enumerate(response['claims'])
                                 if row['evidence_id'] in claim['evidence']]}
        for row in response['coverage']]}


def _evidence_accounting(response, choices, records):
    """Check complete source accounting and links, never infer semantic coverage."""
    rows = response['coverage']
    labels = [row['evidence_id'] for row in rows]
    if len(labels) != len(set(labels)) or set(labels) != set(choices):
        raise ValueError('coverage must account for every eligible evidence ID exactly once')
    result = []
    for row in rows:
        label, indices = row['evidence_id'], row['claim_indices']
        expected = [i for i, claim in enumerate(response['claims']) if label in claim['evidence']]
        if sorted(indices) != expected:
            raise ValueError('coverage claim_indices must match claims citing ' + label)
        if row['status'] == 'represented' and not indices:
            raise ValueError('represented evidence needs at least one citing claim: ' + label)
        if row['status'] == 'non_substantive' and indices:
            raise ValueError('non_substantive evidence cannot cite generated claims: ' + label)
        if not row['reason'].strip():
            raise ValueError('coverage needs a substantive reason: ' + label)
        result.append({'reference': choices[label]['reference'], 'status': row['status'],
                       'candidate_ids': [records[i]['id'] for i in indices], 'reason': row['reason']})
    return result


def _fixture_accounting(response, choices):
    """Adapt old offline fixtures, explicitly without semantic assessment."""
    return {**response, 'coverage': [
        {'evidence_id': label, 'status': 'ambiguous',
         'claim_indices': [i for i, claim in enumerate(response['claims']) if label in claim['evidence']],
         'reason': 'Offline legacy fixture; semantic coverage was not assessed.'}
        for label in choices]}


def _accounting_summary(clusters, method, expected_passages, failed=False):
    counts = Counter(row['status'] for cluster in clusters for row in cluster['passages'])
    total = sum(counts.values())
    return {
        'version': 1, 'method': method,
        'status': 'needs_review' if failed or total != expected_passages or counts['ambiguous'] or
                  counts['insufficient_context'] else 'accounted',
        'expected_passages': expected_passages, 'passages': total,
        'unaccounted_passages': expected_passages - total,
        'status_counts': {status: counts[status] for status in
                          ('represented', 'non_substantive', 'insufficient_context', 'ambiguous')},
        'limitation': 'Complete accounting is not proof of full support or semantic completeness.'}


def _validate_accounting_rows(rows, choices, candidates):
    labels = {canonical(value['reference']): label for label, value in choices.items()}
    index = {candidate['id']: i for i, candidate in enumerate(candidates)}
    try:
        response = {'claims': [{'text': c['text'], 'evidence': [labels[canonical(ref)] for ref in c['evidence']]}
                               for c in candidates],
                    'coverage': [{'evidence_id': labels[canonical(row['reference'])],
                                  'status': row['status'], 'reason': row['reason'],
                                  'claim_indices': [index[cid] for cid in row['candidate_ids']]}
                                 for row in rows]}
        Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
        _evidence_accounting(response, choices, candidates)
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError('invalid saved evidence accounting: ' + str(exc)[:200]) from exc


def _validate_saved_accounting(directory, manifest, candidates, preparation):
    """Validate the audit sidecar as part of lineage, including on resume/export."""
    report = _read_json(directory / 'report.json')
    version = manifest.get('evidence_accounting_version')
    if version is None and 'evidence_accounting' not in report:
        return  # Historical artifacts predate the accounting contract.
    if version != 1 or preparation is None:
        raise ValueError('invalid evidence accounting version or preparation')
    audit = _read_json(directory / 'evidence-accounting.json')
    if audit.get('version') != 1 or audit.get('generation_build_id') != manifest['generation_build_id']:
        raise ValueError('evidence accounting build mismatch')
    selected = {c['id']: c for c in preparation['clusters']
                if c['id'] in report['compilation_scope']['selected_semantic_cluster_ids']}
    clusters = audit.get('clusters', [])
    if len(clusters) != len(selected) or {c['semantic_cluster_id'] for c in clusters} != set(selected):
        raise ValueError('evidence accounting cluster mismatch')
    bundle = preparation['bundle']
    chunks, elements = ({item['id']: item for item in bundle[name]} for name in ('chunks', 'elements'))
    mapping = {row['candidate_id']: row['semantic_cluster_id'] for row in
               _read_json(directory / 'candidate-clusters.json')}
    expected_passages = 0
    for cluster in clusters:
        ids = selected[cluster['semantic_cluster_id']]['original_chunk_ids']
        structural = {r['id']: r for r in preparation['structural_chunks']}
        semantic = selected[cluster['semantic_cluster_id']]
        choices = _evidence_choices([chunks[cid] for cid in ids], elements,
                                    [structural[sid] for sid in semantic['structural_chunk_ids']])
        expected_passages += len(choices)
        own = [c for c in candidates if mapping[c['id']] == cluster['semantic_cluster_id']]
        if not cluster['passages'] and not own and any(e.get('chunk_ids') == ids for e in report['errors']):
            continue
        _validate_accounting_rows(cluster['passages'], choices, own)
    method = 'model_self_report' if report['telemetry']['provider'] == 'openai' else 'offline_fixture'
    if report['evidence_accounting'] != _accounting_summary(clusters, method, expected_passages, bool(report['errors'])):
        raise ValueError('evidence accounting summary mismatch')


def _classification_input(candidate, chunk_map, element_map, document_map, source_map):
    documents = [document_map[item] for item in sorted({ref["document_id"] for ref in candidate["evidence"]})]
    return {"claim": candidate["text"],
            "evidence": [{**ref, **_passage(ref, element_map)} for ref in candidate["evidence"]],
            "chunks": [{"chunk_id": item, "text": chunk_map[item]["text"]} for item in candidate["chunk_ids"]],
            "sources": [source_map[(doc["source_id"], doc["source_version"])] for doc in documents]}


def _call(method, retries, on_retry=None):
    last = None
    for attempt in range(retries + 1):
        try:
            return method()
        except Exception as exc:  # Preserve the final provider error in the report.
            last = exc
            if attempt < retries:
                if on_retry:
                    on_retry(attempt + 2, retries + 1)
                time.sleep(min(2 ** attempt, 4))
    raise last


def _checkpoint(completed, total):
    return completed in {1, total} or completed * 10 // total > (completed - 1) * 10 // total


def _usage(records):
    return {field: sum(record[field] for record in records) for field in
             ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")}


def _rate_card(config, stage="claim_generation"):
    return pricing_for_step(config, stage)


def _cost(records, rate_card):
    total_cost = 0.0
    for record in records:
        rates = rate_card["short_context_usd_per_million_tokens"] if \
            record["input_tokens"] <= rate_card["short_context_limit_tokens"] else \
            rate_card["long_context_usd_per_million_tokens"]
        uncached_input = max(0, record["input_tokens"] - record["cached_input_tokens"] -
                             record["cache_write_tokens"])
        total_cost += (uncached_input * rates["input"] +
                       record["cached_input_tokens"] * rates["cached_input"] +
                       record["cache_write_tokens"] * rates["cache_write"] +
                       record["output_tokens"] * rates["output"]) / 1_000_000
    return total_cost


def _telemetry(provider, config, usage_records=None, stage="claim_generation"):
    rate_card = _rate_card(config, stage)
    step = config["steps"][stage]
    usage_records = provider.usage_records if usage_records is None else usage_records
    return {"provider": step["provider"], "model": provider.model_id,
            "requests": len(usage_records), "usage": _usage(usage_records),
            "estimated_cost_usd": _cost(usage_records, rate_card),
            "rate_card": {"version": config["pricing"]["version"],
                          "model": step["model"], "rates": copy.deepcopy(rate_card)}}


def _subset_batch_indices(characters, subset_batches, batch_numbers=None):
    """Choose unique semantic cluster indices by cumulative request characters."""
    count = len(characters)
    if batch_numbers is not None:
        if subset_batches is not None:
            raise ValueError("cluster_numbers and subset_clusters are mutually exclusive")
        if not isinstance(batch_numbers, list) or not batch_numbers or any(
                not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= count
                for number in batch_numbers) or len(set(batch_numbers)) != len(batch_numbers):
            raise ValueError(f"cluster_numbers must be unique integers between 1 and {count}")
        return sorted(number - 1 for number in batch_numbers)
    if subset_batches is None:
        return list(range(count))
    if not isinstance(subset_batches, int) or isinstance(subset_batches, bool) or not 1 <= subset_batches <= count:
        raise ValueError(f"subset_clusters must be between 1 and {count}")
    if subset_batches == count:
        return list(range(count))
    total = sum(characters)
    midpoints, current = [], 0
    for value in characters:
        midpoints.append(current + value / 2)
        current += value
    available = set(range(count))
    selected = []
    for position in range(subset_batches):
        target = total * (position + 0.5) / subset_batches
        choice = min(available, key=lambda index: (abs(midpoints[index] - target), index))
        available.remove(choice)
        selected.append(choice)
    return sorted(selected)


def _candidate_record(generation_build_id, candidate, generation, choices):
    Draft202012Validator(CANDIDATE_SCHEMA['properties']['claims']['items']).validate(candidate)
    if any(label not in choices for label in candidate["evidence"]):
        raise ValueError("candidate claim selects an unknown evidence label")
    selected = [choices[label] for label in candidate["evidence"]]
    evidence = [copy.deepcopy(item["reference"]) for item in selected]
    if len({canonical(item) for item in evidence}) != len(evidence):
        raise ValueError("candidate claim repeats evidence")
    chunk_ids = []
    for value in selected:
        for chunk_id in value.get('original_chunk_ids', [value['chunk_id']]):
            if chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
    text = candidate["text"].strip()
    if not text:
        raise ValueError("candidate claim text is blank")
    result = {"schema_version": SCHEMA_VERSION,
              "id": "candidate:" + digest(generation_build_id + "\n" + canonical(evidence) + "\n" + text),
              "generation_build_id": generation_build_id, "chunk_ids": chunk_ids, "text": text,
              "evidence": evidence, "grounding_review_status": "draft", "generation": generation}
    Draft202012Validator(CANDIDATE_VALIDATION_SCHEMA).validate(result)
    return result


def _claim(build_id, candidate, classification, classifier):
    Draft202012Validator(CLASSIFICATION_SCHEMA).validate(classification)
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "claim:" + digest(build_id + "\n" + canonical(candidate["evidence"]) + "\n" + candidate["text"]),
        "topic_id": classification["topic_id"], "build_id": build_id, "chunk_ids": candidate["chunk_ids"],
        "text": candidate["text"], "evidence": candidate["evidence"],
        "grounding_review_status": candidate["grounding_review_status"],
        "classification_review_status": "draft", "generation": candidate["generation"],
        "classification": classifier,
    }


def _topic_records(claims, taxonomy, build_id):
    result = []
    for item in taxonomy["topics"]:
        assigned = sorted(claim["id"] for claim in claims if claim["topic_id"] == item["id"])
        if not assigned:
            continue
        summary = "Thème à l'état de brouillon. Examinez les assertions candidate claims et leurs preuves de source avant de vous y fier."
        result.append({"schema_version": SCHEMA_VERSION, "id": item["id"], "title": item["title"],
                       "summary": summary, "claim_ids": assigned,
                       "revision": digest(item["title"] + "\n" + summary + "\n" + "\n".join(assigned)),
                       "build_id": build_id, "review_status": "draft",
                       "generation": {"method": "manual", "version": taxonomy["taxonomy_version"]}})
    return result


def _provider(config, fixture_path, stage="claim_generation"):
    config = config["steps"][stage]
    if config["provider"] == "fixture":
        if fixture_path is None:
            raise ValueError("fixture provider requires --fixture")
        return FixtureProvider(_read_json(fixture_path))
    return OpenAIProvider(config["model"] or DEFAULT_MODEL, config["reasoning_effort"])


def _write_jsonl(path, records):
    Path(path).write_text("".join(canonical(item) + "\n" for item in records), encoding="utf-8")


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def _build_id(pipeline_version, config_digest, document_ids):
    return "build:" + digest(pipeline_version + "\n" + config_digest + "\n" + "\n".join(sorted(document_ids)))


def _project_generation(scope, usage, config):
    if scope["mode"] != "subset" or scope.get("selection") == "explicit" or not usage:
        return None
    estimated_cost = _cost(usage, _rate_card(config)) * scope["generation_request_characters"]["total"] / \
        scope["generation_request_characters"]["selected"]
    return {"estimated_cost_usd": estimated_cost,
            "assumption": "observed cost scaled by exact serialized generation-request characters"}


def _generation_coverage(bundle, candidates, scope, errors):
    """Account for selected chunks cited by candidate claims, without judging extraction quality."""
    selected = set(scope["selected_chunk_ids"])
    chunk_map = {item["id"]: item for item in bundle["chunks"]}
    document_map = {item["id"]: item for item in bundle["documents"]}
    failed = {chunk_id for error in errors if error.get("stage") == "generation"
              for chunk_id in error["chunk_ids"]} & selected
    element_map = {item["id"]: item for item in bundle.get("elements", [])}
    references = {chunk_id: {canonical(ref) for ref in claim_evidence_references(chunk, element_map)}
                  for chunk_id, chunk in chunk_map.items()}
    cited = {chunk_id: set() for chunk_id in selected}
    for candidate in candidates:
        evidence = {canonical(ref) for ref in candidate["evidence"]}
        for chunk_id in candidate["chunk_ids"]:
            if chunk_id not in selected:
                raise ValueError("coverage candidate claim chunk is outside the selected scope")
            if evidence & references[chunk_id]:
                cited[chunk_id].add(candidate["id"])
    chunks = []
    for chunk in bundle["chunks"]:
        chunk_id = chunk["id"]
        if chunk_id not in selected:
            continue
        document = document_map[chunk["document_id"]]
        eligible = sum("span" in ref for ref in claim_evidence_references(chunk, element_map))
        status = ("generation_failed" if chunk_id in failed else "cited" if cited[chunk_id] else
                  "no_eligible_evidence" if not eligible else "no_candidates")
        chunks.append({"chunk_id": chunk_id, "document_id": chunk["document_id"],
                       "source_id": document["source_id"], "source_version": document["source_version"],
                       "candidate_count": len(cited[chunk_id]), "eligible_evidence_count": eligible,
                       "status": status})
    sources = []
    for source in bundle["sources"]:
        rows = [row for row in chunks if (row["source_id"], row["source_version"]) ==
                (source["id"], source["version"])]
        source_candidates = set().union(*(cited[row["chunk_id"]] for row in rows))
        sources.append({"source_id": source["id"], "source_version": source["version"],
                        "title": source["title"], "selected_chunks": len(rows),
                        "cited_chunks": sum(row["candidate_count"] > 0 for row in rows),
                        "candidate_count": len(source_candidates),
                        "status": "not_selected" if not rows else
                                  "has_candidates" if source_candidates else "no_candidates"})
    missing_chunks = [row["chunk_id"] for row in chunks if not row["candidate_count"]]
    missing_sources = [row for row in sources if row["status"] == "no_candidates"]
    return {
        "coverage_version": 1,
        "status": "needs_review" if missing_chunks or failed else "no_gaps_detected",
        "summary": {"selected_chunks": len(chunks), "cited_chunks": len(chunks) - len(missing_chunks),
                    "chunks_without_candidates": len(missing_chunks),
                    "failed_chunks": sum(row["status"] == "generation_failed" for row in chunks),
                    "chunks_without_eligible_evidence": sum(not row["eligible_evidence_count"] for row in chunks),
                    "not_selected_chunks": len(bundle["chunks"]) - len(chunks),
                    "selected_sources": sum(row["status"] != "not_selected" for row in sources),
                    "sources_with_candidates": sum(row["status"] == "has_candidates" for row in sources),
                    "sources_without_candidates": len(missing_sources),
                    "not_selected_sources": sum(row["status"] == "not_selected" for row in sources)},
        "selected_chunk_ids_without_candidates": missing_chunks,
        "selected_source_ids_without_candidates": sorted({row["source_id"] for row in missing_sources}),
        "chunks": chunks, "sources": sources,
    }


def _candidate_manifest(candidate_dir):
    candidate_dir = Path(candidate_dir)
    manifest_path = candidate_dir / "build-manifest.json"
    if not manifest_path.is_file() and (candidate_dir / "generation-checkpoint.json").is_file():
        raise ValueError("candidate claim generation is incomplete; rerun generate_claims.py with --resume")
    manifest = _read_json(manifest_path)
    candidate_path = candidate_dir / "claim-candidates.jsonl"
    output_hashes = {item.get("path"): item.get("sha256") for item in manifest.get("outputs", [])}
    if manifest.get("stage") != "claim-generation" or \
            manifest.get("candidate_sha256") != file_digest(candidate_path) or \
            output_hashes.get("artifact-bundle.json") != file_digest(candidate_dir / "artifact-bundle.json") or \
            output_hashes.get("claim-candidates.jsonl") != file_digest(candidate_path):
        raise ValueError("candidate claim artifact manifest is invalid")
    for entry in manifest.get('outputs', []):
        path = (candidate_dir / entry['path']).resolve()
        if not path.is_relative_to(candidate_dir.resolve()) or file_digest(path) != entry['sha256']:
            raise ValueError('candidate claim output hash mismatch')
    from knowledge_foundry.claim_context import validate_candidate_lineage
    candidates = _read_jsonl(candidate_path)
    preparation = validate_candidate_lineage(candidate_dir, manifest, candidates)
    _validate_saved_accounting(candidate_dir, manifest, candidates, preparation)
    return manifest, candidate_path, manifest_path


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


def _generation_identity(build_id, input_path, config_digest, generation_hash, scope):
    return {"checkpoint_version": 2, "stage": "claim-generation", "generation_build_id": build_id,
            "input_bundle_sha256": file_digest(input_path), "config_sha256": config_digest,
            "generation_prompt_sha256": generation_hash, "compilation_scope": scope}


def _recovery_seed(directory, identity, selected_clusters, clusters):
    """Validate a finalized partial run and seed only its successful clusters."""
    directory = Path(directory)
    manifest, candidate_path, manifest_path = _candidate_manifest(directory)
    keys = ('generation_build_id', 'input_bundle_sha256', 'config_sha256',
            'generation_prompt_sha256', 'compilation_scope',
            'preparation_manifest_sha256', 'cluster_plan_sha256')
    if any(manifest.get(key) != identity[key] for key in keys):
        raise ValueError('recovery source does not match input, configuration, prompt, or scope')
    report = _read_json(directory / 'report.json')
    if report['status'] != 'partial' or not report['errors']:
        raise ValueError('recovery requires a finalized partial generation run with errors')
    candidates = _read_jsonl(candidate_path)
    mapping = {r['candidate_id']: r['semantic_cluster_id']
               for r in _read_json(directory / 'candidate-clusters.json')}
    accounting = {r['semantic_cluster_id']: r['passages']
                  for r in _read_json(directory / 'evidence-accounting.json')['clusters']}
    numbers = {tuple(c['original_chunk_ids']): n for n, c in enumerate(clusters, 1)}
    failed = set()
    for error in report['errors']:
        number = numbers.get(tuple(error.get('chunk_ids', [])))
        if error.get('stage') != 'generation' or number is None:
            raise ValueError('recovery error must identify a selected generation cluster')
        failed.add(number)
    seeds = []
    for ordinal, (chunks, cluster) in enumerate(zip(selected_clusters, clusters), 1):
        own = [c for c in candidates if mapping[c['id']] == cluster['id']]
        if ordinal in failed:
            if own or accounting[cluster['id']]:
                raise ValueError('failed recovery cluster must not contain accepted output')
            continue
        seeds.append({'cluster_number': ordinal, 'chunk_ids': [c['id'] for c in chunks],
                      'candidates': own, 'evidence_accounting': accounting[cluster['id']],
                      'errors': [], 'usage_records': []})
    metadata = {'source_directory': str(directory.resolve()),
                'source_manifest_sha256': file_digest(manifest_path),
                'reused_clusters': len(seeds),
                'retried_cluster_numbers': [identity['compilation_scope']['selected_cluster_numbers'][n - 1]
                                            for n in sorted(failed)],
                'prior_telemetry': report['telemetry'],
                'prior_request_token_counting': report['claim_generation_request_token_counting'],
                'prior_projected_full_generation': report['projected_full_generation']}
    return seeds, metadata


def _load_generation_checkpoints(output_dir, identity, selected_clusters, chunk_map, element_map,
                                 cluster_choices=None):
    checkpoint_path = output_dir / "generation-checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("output directory is not a resumable claim-generation run")
    checkpoint = _read_json(checkpoint_path)
    if any(checkpoint.get(key) != value for key, value in identity.items()):
        raise ValueError("generation checkpoint does not match the requested input, configuration, prompt, or scope")
    checkpoint_dir = output_dir / "generation-checkpoints"
    cluster_by_number = {number: cluster for number, cluster in enumerate(selected_clusters, start=1)}
    candidates, errors, usage, completed = [], [], [], set()
    for path in sorted(checkpoint_dir.glob("cluster-*.json")):
        record = _read_json(path)
        number = record.get("cluster_number")
        cluster = cluster_by_number.get(number)
        if number in completed or cluster is None or record.get("chunk_ids") != [item["id"] for item in cluster]:
            raise ValueError("generation cluster checkpoint is invalid")
        if not isinstance(record.get("candidates"), list) or not isinstance(record.get("errors"), list) or \
                not isinstance(record.get("usage_records"), list):
            raise ValueError("generation cluster checkpoint is invalid")
        for candidate in record["candidates"]:
            _validate_candidate(candidate, identity["generation_build_id"], {c["id"]: c for c in cluster}, element_map)
        if identity['checkpoint_version'] >= 2 and not isinstance(record.get('evidence_accounting'), list):
            raise ValueError('generation checkpoint lacks evidence accounting')
        if not record['errors']:
            choices = cluster_choices[number - 1] if cluster_choices is not None else _evidence_choices(cluster, element_map)
            _validate_accounting_rows(record['evidence_accounting'], choices,
                                      record['candidates'])
        candidates.extend(record["candidates"])
        errors.extend(record["errors"])
        usage.extend(record["usage_records"])
        completed.add(number)
    return candidates, errors, usage, completed


def generate_claims(chunked_dir, output_dir, config_path=ROOT / "config/claim-generation-and-classification.json", fixture_path=None,
                    on_progress=None, subset_clusters=None, resume=False, cluster_numbers=None, context_dir=None,
                    subset_batches=None, batch_numbers=None, retry_failed_from=None):
    """Generate durable, grounded candidate claims from one immutable chunk bundle."""
    started = time.monotonic()
    if subset_batches is not None:
        if subset_clusters is not None:
            raise ValueError('duplicate subset selection arguments')
        subset_clusters = subset_batches
    if batch_numbers is not None:
        if cluster_numbers is not None:
            raise ValueError('duplicate cluster selection arguments')
        cluster_numbers = batch_numbers
    if context_dir is None:
        raise ValueError("--context-dir is required; build structural chunks and semantic clusters first")
    output_dir = Path(output_dir)
    config = _config(config_path)
    if output_dir.resolve().is_relative_to(Path(context_dir).resolve()):
        raise ValueError('generation output cannot be inside frozen structural chunks and semantic clusters artifacts')
    preparation = load_preparation(context_dir, config)
    input_path = Path(context_dir) / "artifact-bundle.json"
    bundle = preparation['bundle']
    if chunked_dir is not None and file_digest(Path(chunked_dir) / 'artifact-bundle.json') != file_digest(input_path):
        raise ValueError('chunk bundle differs from the frozen structural chunk and semantic cluster input')
    generation_prompt, generation_hash = _prompt("claim_generation_v1.txt")
    provider = _provider(config, fixture_path)
    if isinstance(provider, OpenAIProvider):
        provider.tool.max_output_tokens = config['steps']['claim_generation']['budget']['reserved_output_tokens']
    chunks, document_ids = bundle["chunks"], [item["id"] for item in bundle["documents"]]
    element_map = {item["id"]: item for item in bundle["elements"]}
    original_map = {c['id']: c for c in chunks}
    clusters = preparation['clusters']
    all_clusters = [[original_map[cid] for cid in cluster['original_chunk_ids']] for cluster in clusters]
    structural = {r['id']: r for r in preparation['structural_chunks']}
    cluster_inputs = [request_for_members(cluster['structural_chunk_ids'], structural, bundle, config,
                                      generation_prompt, CANDIDATE_SCHEMA)[0]['input'] for cluster in clusters]
    request_characters = [len(canonical(generation_payload(config, value, generation_prompt, CANDIDATE_SCHEMA)))
                          for value in cluster_inputs]
    selected_indices = _subset_batch_indices(request_characters, subset_clusters, cluster_numbers)
    selected_clusters = [all_clusters[index] for index in selected_indices]
    cluster_choices = [_evidence_choices(all_clusters[index], element_map,
                       [structural[sid] for sid in clusters[index]['structural_chunk_ids']]) for index in selected_indices]
    selected_chunks = [chunk for cluster in selected_clusters for chunk in cluster]
    scope = {"mode": "subset" if subset_clusters is not None or cluster_numbers is not None else "full",
             "selection": "explicit" if cluster_numbers is not None else
                          "length-stratified" if subset_clusters is not None else "all",
             "total_chunks": len(chunks),
             "selected_chunks": len(selected_chunks), "total_semantic_clusters": len(all_clusters),
             "selected_cluster_numbers": [index + 1 for index in selected_indices],
             "selected_cluster_chunk_counts": [len(all_clusters[index]) for index in selected_indices],
             "selected_chunk_ids": [chunk["id"] for chunk in selected_chunks],
             "selected_semantic_cluster_ids": [clusters[i]["id"] for i in selected_indices],
             "generation_request_characters": {"total": sum(request_characters),
                                               "selected": sum(request_characters[index] for index in selected_indices)}}
    config_digest = digest(canonical({"config": execution_settings(config), "input_bundle_sha256": file_digest(input_path),
                                      "generation_prompt_sha256": generation_hash, "scope": scope,
                                      "preparation_manifest_sha256": preparation["manifest_sha256"],
                                      "cluster_plan_sha256": preparation["manifest"]["cluster_plan_sha256"]}))
    pipeline_version = config["pipeline_version"] + "-generation"
    build_id = _build_id(pipeline_version, config_digest, document_ids)
    generation = {"method": "model", "version": config["steps"]["claim_generation"]["version"],
                  "model_id": provider.model_id, "prompt_sha256": generation_hash}
    chunk_map = {item["id"]: item for item in selected_chunks}
    identity = _generation_identity(build_id, input_path, config_digest, generation_hash, scope)
    identity.update(preparation_manifest_sha256=preparation["manifest_sha256"],
                    cluster_plan_sha256=preparation["manifest"]["cluster_plan_sha256"])
    seeds, recovery = [], None
    if retry_failed_from is not None:
        source = Path(retry_failed_from).resolve()
        if output_dir.resolve().is_relative_to(source) or source.is_relative_to(output_dir.resolve()):
            raise ValueError('recovery output and source must be separate directories')
        seeds, recovery = _recovery_seed(source, identity, selected_clusters,
                                        [clusters[i] for i in selected_indices])
    identity['recovery'] = recovery
    checkpoint_path = output_dir / "generation-checkpoint.json"
    checkpoint_dir = output_dir / "generation-checkpoints"
    candidate_path = output_dir / "claim-candidates.jsonl"
    if output_dir.exists():
        if not resume:
            raise ValueError(f"output directory already exists: {output_dir}; use --resume for an incomplete run")
        if (output_dir / "build-manifest.json").is_file():
            raise ValueError("claim-generation output is already complete")
        candidates, errors, usage, completed_clusters = _load_generation_checkpoints(
            output_dir, identity, selected_clusters, chunk_map, element_map, cluster_choices)
        _write_jsonl_atomic(candidate_path, sorted(candidates, key=lambda item: item["id"]))
    else:
        if resume:
            raise ValueError("cannot resume because the claim-generation output directory does not exist")
        output_dir.mkdir(parents=True)
        checkpoint_dir.mkdir()
        candidates, errors, usage, completed_clusters = [], [], [], set()
        _write_jsonl_atomic(candidate_path, candidates)
        _write_json_atomic(checkpoint_path, {**identity, "status": "in_progress", "completed_cluster_numbers": []})
    if seeds:
        for record in seeds:
            path = checkpoint_dir / f"cluster-{record['cluster_number']:06d}.json"
            if not path.exists():
                _write_json_atomic(path, record)
        candidates, errors, usage, completed_clusters = _load_generation_checkpoints(
            output_dir, identity, selected_clusters, chunk_map, element_map, cluster_choices)
    if not (output_dir / 'preparation').exists():
        shutil.copytree(context_dir, output_dir / 'preparation')
    else:
        saved = load_preparation(output_dir / 'preparation', config)
        if saved['manifest_sha256'] != preparation['manifest_sha256']:
            raise ValueError('saved structural chunks and semantic clusters artifacts mismatch')
    counter = RequestCounter(config, output_dir / 'request-counts')
    counter.telemetry['frozen_counts_reused'] = 0
    if recovery:
        for key, value in recovery['prior_request_token_counting'].items():
            if key in counter.telemetry and type(value) is int:
                counter.telemetry[key] += value
    candidate_clusters, evidence_accounting = [], []
    for number in sorted(completed_clusters):
        saved_record = _read_json(checkpoint_dir / f'cluster-{number:06d}.json')
        evidence_accounting.append({'semantic_cluster_id': clusters[selected_indices[number - 1]]['id'],
                                    'passages': saved_record['evidence_accounting']})
        for key, value in saved_record.get('claim_generation_request_token_counting', saved_record.get('token_counting', {})).items():
            if key in counter.telemetry and type(value) is int:
                counter.telemetry[key] += value
        candidate_clusters.extend({'candidate_id': c['id'], 'semantic_cluster_id': clusters[selected_indices[number - 1]]['id']}
                                  for c in saved_record['candidates'])
    if on_progress:
        on_progress({"phase": "generation", "state": "started", "chunks": len(selected_chunks),
                     "source_chunks": len(chunks), "total": len(selected_clusters),
                     "source_total": len(all_clusters), "subset": scope["mode"] == "subset",
                     "resumed_clusters": len(completed_clusters)})
    for completed, cluster in enumerate(selected_clusters, start=1):
        if completed in completed_clusters:
            continue
        ids, usage_start = [item["id"] for item in cluster], len(provider.usage_records)
        cluster_candidates, cluster_errors, cluster_accounting = [], [], []
        choices = cluster_choices[completed - 1]
        retry_feedback = ""
        counter_start = dict(counter.telemetry)

        def generate_cluster():
            nonlocal retry_feedback, cluster_accounting
            request = generation_payload(config, cluster_inputs[selected_indices[completed - 1]],
                                         generation_prompt + retry_feedback, CANDIDATE_SCHEMA)
            # The frozen count is reusable only for the exact prepared request.
            cluster = clusters[selected_indices[completed - 1]]
            if not retry_feedback and cluster['request_sha256'] == stable_hash(request):
                counter.telemetry['frozen_counts_reused'] += 1
            else:
                counter.check(request, 'semantic cluster ' + cluster['id'])
            response = provider.candidates(ids if isinstance(provider, FixtureProvider) else
                                           cluster_inputs[selected_indices[completed - 1]],
                                           generation_prompt + retry_feedback, CANDIDATE_SCHEMA)
            try:
                if isinstance(provider, FixtureProvider) and 'coverage' not in response:
                    response = _fixture_accounting(response, choices)
                Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
                response = _derive_coverage_links(response)
                records = [_candidate_record(build_id, item, generation, choices) for item in response["claims"]]
                accounting = _evidence_accounting(response, choices, records)
                candidate_ids = {item["id"] for item in candidates}
                if len({item["id"] for item in records}) != len(records) or \
                        candidate_ids.intersection(item["id"] for item in records):
                    raise ValueError("generation produced duplicate candidate claim IDs")
                cluster_accounting = accounting
            except (ValueError, ValidationError) as exc:
                retry_feedback = ("\nLa réponse précédente a été rejetée : " + str(exc)[:600] +
                                  "\nCorrigez la réponse complète. Explicitez les sujets et les règles ; "
                                  "omettez toute assertion impossible à étayer avec les preuves proposées.")
                raise
            return records

        try:
            cluster_candidates = _call(
                generate_cluster, config["steps"]["claim_generation"]["retries"],
                lambda attempt, total: on_progress and on_progress(
                    {"phase": "generation", "state": "retry", "completed": completed,
                     "total": len(selected_clusters), "attempt": attempt, "attempts": total}))
        except Exception as exc:
            cluster_errors.append({"stage": "generation", "chunk_ids": ids, "error": str(exc)})
        cluster_usage = provider.usage_records[usage_start:]
        cluster_record = {"cluster_number": completed, "chunk_ids": ids, "candidates": cluster_candidates,
                          "evidence_accounting": cluster_accounting,
                          "errors": cluster_errors, "usage_records": cluster_usage,
                          "claim_generation_request_token_counting": {k: v - counter_start[k] for k, v in counter.telemetry.items()
                                             if type(v) is int}}
        _write_json_atomic(checkpoint_dir / f"cluster-{completed:06d}.json", cluster_record)
        evidence_accounting.append({'semantic_cluster_id': clusters[selected_indices[completed - 1]]['id'],
                                    'passages': cluster_accounting})
        candidate_clusters.extend({'candidate_id': c['id'],
                                   'semantic_cluster_id': clusters[selected_indices[completed - 1]]['id']}
                                  for c in cluster_candidates)
        candidates.extend(cluster_candidates)
        errors.extend(cluster_errors)
        usage.extend(cluster_usage)
        completed_clusters.add(completed)
        candidates.sort(key=lambda item: item["id"])
        _write_jsonl_atomic(candidate_path, candidates)
        _write_json_atomic(checkpoint_path, {**identity, "status": "in_progress",
                                              "completed_cluster_numbers": sorted(completed_clusters)})
        if on_progress and _checkpoint(completed, len(selected_clusters)):
            on_progress({"phase": "generation", "state": "progress", "completed": completed,
                         "total": len(selected_clusters), "candidates": len(candidates)})
    if len({item["id"] for item in candidates}) != len(candidates):
        raise ValueError("generation produced duplicate candidate claim IDs")
    candidates.sort(key=lambda item: item["id"])
    result = copy.deepcopy(bundle)
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id, "pipeline_version": pipeline_version,
                               "config_sha256": config_digest, "input_document_ids": document_ids,
                               "output_chunk_ids": [], "output_claim_ids": [], "output_topic_ids": [],
                               "status": "partial" if errors else "complete"})
    from scripts.validate_artifact_examples import check
    check(result)
    if on_progress:
        on_progress({"phase": "export", "state": "started"})
    _write_json(output_dir / "artifact-bundle.json", result)
    _write_jsonl_atomic(candidate_path, candidates)
    telemetry = _telemetry(provider, config, usage)
    if recovery:
        prior = recovery['prior_telemetry']
        telemetry['requests'] += prior['requests']
        telemetry['estimated_cost_usd'] += prior['estimated_cost_usd']
        for field, value in prior['usage'].items():
            telemetry['usage'][field] += value
    coverage = _generation_coverage(bundle, candidates, scope, errors)
    report = {"generation_build_id": build_id, "upstream_chunk_build_id": bundle["builds"][0]["id"],
              "documents": len(document_ids), "chunks": len(chunks), "candidates": len(candidates),
              "compilation_scope": scope, "coverage": coverage, "errors": errors, "telemetry": telemetry,
              "projected_full_generation": _project_generation(scope, usage, config),
              "duration_seconds": round(time.monotonic() - started, 3),
              "status": "partial" if errors else "complete"}
    projection = report['projected_full_generation']
    if recovery:
        report['recovery'] = recovery
        prior_projection = recovery['prior_projected_full_generation']
        if prior_projection:
            if projection is None:
                projection = report['projected_full_generation'] = copy.deepcopy(prior_projection)
            else:
                projection['estimated_cost_usd'] += prior_projection['estimated_cost_usd']
    if projection is not None:
        projection['whole_input_embedding_estimated_cost_usd'] = preparation['report']['embeddings']['estimated_cost_usd']
    report.update(preparation=preparation['report'],
                  preparation_manifest_sha256=preparation['manifest_sha256'],
                  cluster_plan_sha256=preparation['manifest']['cluster_plan_sha256'],
                  claim_generation_request_token_counting=counter.telemetry,
                  cross_document_candidates=sum(len({r['document_id'] for r in c['evidence']}) > 1 for c in candidates))
    expected_passages = sum(len(choices) for choices in cluster_choices)
    report['evidence_accounting'] = _accounting_summary(evidence_accounting,
        'model_self_report' if config['steps']['claim_generation']['provider'] == 'openai' else 'offline_fixture',
        expected_passages, bool(errors))
    _write_json(output_dir / 'evidence-accounting.json', {
        'version': 1, 'generation_build_id': build_id,
        'clusters': sorted(evidence_accounting, key=lambda row: row['semantic_cluster_id'])})
    _write_json(output_dir / 'candidate-clusters.json', sorted(candidate_clusters, key=lambda r: r['candidate_id']))
    _write_json(output_dir / "report.json", report)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file() and checkpoint_dir not in path.parents and
                   path != checkpoint_path)
    manifest = {"manifest_version": 1, "stage": "claim-generation", "generation_build_id": build_id,
                "evidence_accounting_version": 1,
                "upstream_chunk_build_id": bundle["builds"][0]["id"], "input_bundle_sha256": file_digest(input_path),
                "config_sha256": config_digest, "generation_prompt_sha256": generation_hash,
                "compilation_scope": scope, "candidate_sha256": file_digest(candidate_path),
                "preparation_manifest_sha256": preparation["manifest_sha256"],
                "cluster_plan_sha256": preparation["manifest"]["cluster_plan_sha256"],
                "outputs": [{"path": str(path.relative_to(output_dir)), "sha256": file_digest(path)} for path in files]}
    _write_json(output_dir / "build-manifest.json", manifest)
    checkpoint_path.unlink(missing_ok=True)
    for path in checkpoint_dir.glob("cluster-*.json"):
        path.unlink()
    checkpoint_dir.rmdir()
    if on_progress:
        on_progress({"phase": "complete", "state": report["status"], "telemetry": telemetry,
                     "projection": report["projected_full_generation"]})
    return report


def classify_claims(candidate_dir, output_dir, config_path=ROOT / "config/claim-generation-and-classification.json",
                    taxonomy_path=ROOT / "config/topics-v1.json", fixture_path=None,
                    on_progress=None, reuse_classification_from=None, reuse_candidates_dir=None, resume=False):
    """Classify a durable candidate claim artifact without regenerating its claims."""
    started = time.monotonic()
    candidate_dir, output_dir = Path(candidate_dir), Path(output_dir)
    if (reuse_classification_from is None) != (reuse_candidates_dir is None):
        raise ValueError('classification reuse requires both the prior classification and its candidates directory')
    protected = [candidate_dir] + ([Path(reuse_classification_from), Path(reuse_candidates_dir)]
                                  if reuse_classification_from is not None else [])
    if any(output_dir.resolve().is_relative_to(path.resolve()) or path.resolve().is_relative_to(output_dir.resolve())
           for path in protected):
        raise ValueError('classification output must be separate from candidate and reuse artifacts')
    if output_dir.exists() and not resume:
        raise ValueError(f'output directory already exists: {output_dir}; use --resume for an interrupted run')
    manifest, candidate_path, manifest_path = _candidate_manifest(candidate_dir)
    generation_report = _read_json(candidate_dir / 'report.json')
    if generation_report.get('status') != 'complete' or generation_report.get('errors'):
        raise ValueError('classification requires complete generation; recover the partial run with '
                         'generate_claims.py --retry-failed-from into a fresh directory first')
    bundle = _read_json(candidate_dir / "artifact-bundle.json")
    if bundle["claims"] or bundle["topics"]:
        raise ValueError("candidate claim artifact bundle must not contain classified claims or topics")
    candidates = _read_jsonl(candidate_path)
    chunk_map = {item["id"]: item for item in bundle["chunks"]}
    element_map = {item["id"]: item for item in bundle["elements"]}
    document_map = {item["id"]: item for item in bundle["documents"]}
    source_map = {(item["id"], item["version"]): item for item in bundle["sources"]}
    for candidate in candidates:
        _validate_candidate(candidate, manifest["generation_build_id"], chunk_map, element_map)
    if len({item["id"] for item in candidates}) != len(candidates):
        raise ValueError("candidate claim artifact has duplicate IDs")
    config, taxonomy = _config(config_path), _taxonomy(taxonomy_path)
    valid_topics = {item["id"] for item in taxonomy["topics"]}
    classification_prompt, classification_hash = _prompt("topic_classification_v1.txt")
    provider = _provider(config, fixture_path, "topic_classification")
    document_ids = [item["id"] for item in bundle["documents"]]
    config_digest = digest(canonical({"config": execution_settings(config, "topic_classification"), "candidate_manifest_sha256": file_digest(manifest_path),
                                      "candidate_sha256": file_digest(candidate_path),
                                      "taxonomy_sha256": file_digest(taxonomy_path),
                                      "classification_prompt_sha256": classification_hash}))
    pipeline_version = config["pipeline_version"] + "-classification"
    build_id = _build_id(pipeline_version, config_digest, document_ids)
    classifier = {"method": "model", "version": config["steps"]["topic_classification"]["version"],
                  "model_id": provider.model_id, "prompt_sha256": classification_hash}
    from knowledge_foundry.classification_reuse import ClassificationJournal, load_reuse
    reusable, reuse_provenance = {}, None
    if reuse_classification_from is not None:
        reusable, reuse_provenance = load_reuse(
            reuse_classification_from, reuse_candidates_dir, candidates, bundle, config, taxonomy_path,
            classification_hash, classifier)
    contexts = {c['id']: _classification_input(c, chunk_map, element_map, document_map, source_map)
                for c in candidates}
    requests = {cid: digest(canonical(context)) for cid, context in contexts.items()}
    journal = ClassificationJournal(output_dir, {
        'version': 1, 'build_id': build_id, 'config_sha256': config_digest,
        'schema_sha256': digest(canonical(CLASSIFICATION_SCHEMA)), 'reuse': reuse_provenance,
        'fixture_sha256': file_digest(fixture_path) if fixture_path is not None else None,
    }, requests, valid_topics, resume)
    claims, errors, unclassified, usage = [], [], [], []
    if on_progress:
        on_progress({"phase": "classification", "state": "started", "total": len(candidates),
                     "reusable_candidates": len(reusable), "saved_candidates": len(journal.records)})
    for index, candidate in enumerate(candidates, start=1):
        usage_start = len(provider.usage_records)
        context = contexts[candidate['id']]
        retry_feedback = ""

        def classify_candidate():
            nonlocal retry_feedback
            response = provider.classify(context, taxonomy, classification_prompt + retry_feedback,
                                         CLASSIFICATION_SCHEMA)
            try:
                Draft202012Validator(CLASSIFICATION_SCHEMA).validate(response)
                if response["topic_id"] is not None and response["topic_id"] not in valid_topics:
                    raise ValueError("classifier returned an uncurated topic ID")
            except (ValueError, ValidationError) as exc:
                retry_feedback = ("\nLa réponse précédente a été rejetée : " + str(exc)[:600] +
                                  "\nRéexaminez les preuves et le périmètre organisé ; retournez le JSON corrigé.")
                raise
            return response

        saved = journal.records.get(candidate['id'])
        if saved is None or saved['error'] is not None:
            classification, error = None, None
            reused = candidate['id'] in reusable
            try:
                classification = reusable[candidate['id']] if reused else _call(
                    classify_candidate, config["steps"]["topic_classification"]["retries"],
                    lambda attempt, total: on_progress and on_progress(
                        {"phase": "classification", "state": "retry", "completed": index,
                         "total": len(candidates), "attempt": attempt, "attempts": total}))
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            saved = {'candidate_id': candidate['id'], 'request_sha256': requests[candidate['id']],
                     'classification': classification, 'error': error, 'reused': reused,
                     'usage_records': (saved['usage_records'] if saved else []) +
                                      provider.usage_records[usage_start:]}
            journal.save(saved)
        if saved['error'] is not None:
            errors.append({'stage': 'classification', 'candidate_id': candidate['id'], 'error': saved['error']})
        elif saved['classification']['topic_id'] is None:
            unclassified.append(candidate['id'])
        else:
            claims.append(_claim(build_id, candidate, saved['classification'], classifier))
        usage.extend(saved['usage_records'])
        if on_progress and _checkpoint(index, len(candidates)):
            on_progress({"phase": "classification", "state": "progress", "completed": index,
                         "total": len(candidates), "claims": len(claims)})
    claims.sort(key=lambda item: item["id"])
    if len({item["id"] for item in claims}) != len(claims):
        raise ValueError("classification produced duplicate claim IDs")
    topics = _topic_records(claims, taxonomy, build_id)
    result = copy.deepcopy(bundle)
    result["claims"], result["topics"] = claims, topics
    result["builds"].append({"schema_version": SCHEMA_VERSION, "id": build_id, "pipeline_version": pipeline_version,
                               "config_sha256": config_digest, "input_document_ids": document_ids,
                               "output_chunk_ids": [], "output_claim_ids": [item["id"] for item in claims],
                               "output_topic_ids": [item["id"] for item in topics],
                               "status": "partial" if errors else "complete"})
    from scripts.validate_artifact_examples import check
    check(result)
    if on_progress:
        on_progress({"phase": "export", "state": "started"})
    _write_json(output_dir / "artifact-bundle.json", result)
    _write_jsonl(output_dir / "chunks.jsonl", result["chunks"])
    _write_jsonl(output_dir / "claims.jsonl", claims)
    topic_dir = output_dir / "topics"
    topic_dir.mkdir(exist_ok=True)
    by_claim = {item["id"]: item for item in claims}
    for topic in topics:
        slug = topic["id"].removeprefix("topic:")
        view = {"view_version": 1, "build_id": build_id, "topic": topic,
                "claims": [by_claim[item] for item in topic["claim_ids"]]}
        _write_json(topic_dir / f"{slug}.json", view)
        lines = [f"# {topic['title']}", "", f"Status: `{topic['review_status']}`", "", topic["summary"], "", "## Claims", ""]
        for claim in view["claims"]:
            lines.append(f"- `{claim['id']}` — {claim['text']} (grounding: "
                         f"{claim['grounding_review_status']}; classification: {claim['classification_review_status']})")
        (topic_dir / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    telemetry = _telemetry(provider, config, usage, stage="topic_classification")
    projected = None
    scope = generation_report["compilation_scope"]
    if scope["mode"] == "subset" and candidates:
        classification_cost = _cost(usage, _rate_card(config, "topic_classification")) * scope["total_chunks"] / scope["selected_chunks"]
        generation_projection = generation_report.get("projected_full_generation")
        if generation_projection:
            projected = {"estimated_cost_usd": generation_projection["estimated_cost_usd"] + classification_cost,
                         "generation_estimated_cost_usd": generation_projection["estimated_cost_usd"],
                         "whole_input_embedding_estimated_cost_usd": generation_report.get('preparation', {}).get('embeddings', {}).get('estimated_cost_usd'),
                         "classification_estimated_cost_usd": classification_cost,
                         "projected_candidates": len(candidates) * scope["total_chunks"] / scope["selected_chunks"],
                         "assumptions": {"generation": generation_projection["assumption"],
                                         "classification": "observed cost per candidate claim scaled by subset claim yield"}}
    report = {"build_id": build_id, "upstream_generation_build_id": manifest["generation_build_id"],
              "candidate_manifest_sha256": file_digest(manifest_path), "candidates": len(candidates),
              "claims": len(claims), "topics": len(topics), "errors": errors,
              "unclassified_candidate_ids": unclassified, "telemetry": telemetry,
              "projected_full_build": projected,
              "duration_seconds": round(time.monotonic() - started, 3),
              "classification_reuse": {"source": reuse_provenance,
                  "reused_candidates": sum(r['reused'] for r in journal.records.values()),
                  "newly_classified_candidates": sum(not r['reused'] and r['error'] is None
                                                     for r in journal.records.values()),
                  "limitation": "Telemetry covers this output run, including resumed calls; prior reuse-source costs are excluded."},
              "status": "partial" if errors else "complete"}
    _write_json(output_dir / "report.json", report)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file() and
                   path != journal.path and journal.records_dir not in path.parents)
    output_manifest = {"manifest_version": 1, "stage": "topic-classification", "compilation_build_id": build_id,
                       "upstream_generation_build_id": manifest["generation_build_id"],
                       "candidate_manifest_sha256": file_digest(manifest_path), "config_sha256": config_digest,
                       "taxonomy_sha256": file_digest(taxonomy_path), "classification_prompt_sha256": classification_hash,
                       "outputs": [{"path": str(path.relative_to(output_dir)), "sha256": file_digest(path)} for path in files]}
    _write_json(output_dir / "build-manifest.json", output_manifest)
    journal.clean()
    if on_progress:
        on_progress({"phase": "complete", "state": report["status"], "telemetry": telemetry,
                     "projection": projected})
    return report
