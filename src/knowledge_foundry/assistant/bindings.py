"""Explicit full-document/accepted-knowledge pairing with authentic source spans."""
import copy
from functools import lru_cache
import json
from pathlib import Path

from knowledge_foundry.reasoning.catalogue import accepted_projection, load_dataset
from knowledge_foundry.reasoning.poc import load_poc
from knowledge_foundry.retrieval.artifacts import evidence_id, file_hash
from knowledge_foundry.retrieval.config import ROOT
from knowledge_foundry.structural_chunks import stable_hash


class BindingFailure(ValueError):
    """No capability may bypass a source, version or snapshot binding failure."""


def require(condition, message):
    if not condition:
        raise BindingFailure(message)


@lru_cache(maxsize=2)
def _bundle(path, digest):
    value = json.loads(Path(path).read_text())
    return {**value, '_elements': {e['id']: e for e in value['elements']}}


class KnowledgeBinding:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        require(value['format'] == 'assistant-poc-v1', 'unsupported Assistant binding')
        for key, bound in value['limits'].items():
            require(type(bound) is int and bound > 0, 'invalid Assistant limit: ' + key)
        require(value['limits']['max_parts'] <= 4, 'at most four answer parts are supported')
        require(value['limits']['max_final_output_tokens'] >= 256, 'final output budget must retain safe explanations')

    @classmethod
    def load(cls, path=None):
        return cls(json.loads(Path(path or ROOT / 'config/assistant-poc.json').read_text()))

    @property
    def digest(self):
        return stable_hash(self.value)

    def snapshots(self, store):
        rows = []
        for key in ('documents', 'accepted'):
            spec = self.value[key]
            row = store.snapshot(spec['corpus'], spec['snapshot_id'])
            require(row['id'] == spec['snapshot_id'] and row['corpus'] == spec['corpus'],
                    'selected knowledge snapshot differs from the explicit binding')
            require(row['status'] == 'published', 'a bound snapshot is not published')
            require(row['id'] == 'snapshot:' + stable_hash(row['identity']), 'snapshot identity is corrupt')
            rows.append(row)
        return tuple(rows)

    def dataset_path(self):
        spec = self.value['accepted']
        reference = ROOT / spec['poc_reference']
        require(file_hash(reference) == spec['poc_reference_sha256'], 'accepted POC reference changed')
        poc = load_poc(reference, spec['corpus'])
        require(poc is not None, 'accepted knowledge POC reference is unavailable')
        return poc['dataset_path']

    def validate(self, store):
        full, accepted = self.snapshots(store)
        spec = self.value['documents']
        path = ROOT / spec['bundle']
        require(file_hash(path) == spec['bundle_sha256'] == full['identity'].get('bundle_sha256'),
                'full document bundle binding changed')
        bundle = _bundle(str(path), spec['bundle_sha256'])
        require(full['identity'].get('projection_version') == 'artifact-search-v1',
                'retrieval requires a full document projection')
        require(full['identity']['documents'] == bundle['documents'] and
                full['identity']['sources'] == bundle['sources'], 'full document inventory differs')
        if hasattr(self, '_admitted_dataset'):
            from knowledge_foundry.reasoning.release import validated_release
            dataset = validated_release(self._admitted_dataset)
        else:
            dataset = load_dataset(self.dataset_path())
        accepted_bundle = {**dataset['bundle'], '_elements': {e['id']: e for e in dataset['bundle']['elements']}}
        require(dataset['digest'] == self.value['accepted']['dataset_sha256'] ==
                accepted['identity'].get('reasoning_dataset_sha256'), 'accepted dataset binding changed')
        require(accepted['identity'].get('knowledge_status') == 'accepted' and
                accepted['identity'].get('release_identity') == dataset.get('release_identity'),
                'accepted snapshot review binding changed')
        records, evidence, identity = accepted_projection(dataset)
        require(len(records) == self.value['accepted']['mapping_count'] ==
                accepted['identity']['record_count'], 'accepted mapping population changed')
        require(all(accepted['identity'].get(k) == v for k, v in identity.items()),
                'accepted projection provenance changed')
        docs = {d['id']: d for d in full['identity']['documents']}
        sources = {(s['id'], s['version']): s for s in full['identity']['sources']}
        require(all(docs.get(d['id']) == d for d in accepted['identity']['documents']),
                'accepted document versions are incompatible with retrieval')
        require(all(sources.get((s['id'], s['version'])) == s for s in accepted['identity']['sources']),
                'accepted source identities are incompatible with retrieval')
        actual = store.evidence(accepted['id'], [e['id'] for e in evidence])
        require(actual == {e['id']: e for e in evidence}, 'accepted snapshot evidence changed')
        require(stable_hash(records) == accepted['identity']['records_sha256'] and
                stable_hash(evidence) == accepted['identity']['evidence_sha256'],
                'accepted projection digest changed')
        # A repaired accepted span may be a strict subspan of a full element.
        # Compare original identities and exact overlapping characters, not only IDs.
        full_evidence = store.evidence_for_elements(full['id'], {e['reference']['element_id'] for e in evidence})
        by_element = {}
        for row in full_evidence:
            self.validate_evidence(row, bundle)
            by_element.setdefault(row['reference']['element_id'], []).append(row)
        accepted_only_context = []
        for expected in evidence:
            self.validate_evidence(expected, accepted_bundle)
            ref, span = expected['reference'], expected['reference']['span']
            if ref['element_id'] not in bundle['_elements']:
                # Reviewed exports can retain recovered original headings.
                # They supply context only, never documentary body evidence.
                require(expected['element_kind'] == 'heading',
                        'accepted substantive evidence is absent from the full document bundle')
                accepted_only_context.append(expected['id'])
                continue
            self.validate_evidence(expected, bundle)
            covering = [e for e in by_element.get(ref['element_id'], []) if
                        e['reference']['span']['start'] <= span['start'] and
                        e['reference']['span']['end'] >= span['end']]
            require(bool(covering), 'accepted evidence has no compatible full document span')
        self.bundle = bundle
        self._admitted_dataset = dataset
        return {'status': 'passed', 'document_count': len(docs), 'accepted_document_count':
                len(accepted['identity']['documents']), 'accepted_mapping_count': len(records),
                'accepted_evidence_count': len(evidence), 'overlap_checked': len(evidence) - len(accepted_only_context),
                'accepted_only_governing_context_ids': accepted_only_context}

    def validate_evidence(self, row, bundle=None, source_ids=None, document_ids=None):
        bundle = bundle or self.bundle
        ref = row['reference']
        element = bundle['_elements'].get(ref['element_id'])
        require(element is not None, 'evidence element is outside the frozen document bundle')
        doc = next((d for d in bundle['documents'] if d['id'] == ref['document_id']), None)
        require(doc is not None, 'evidence document is outside the frozen document bundle')
        source = next((s for s in bundle['sources'] if (s['id'], s['version']) ==
                       (doc['source_id'], doc['source_version'])), None)
        span = ref['span']
        require(type(span['start']) is int and type(span['end']) is int and
                0 <= span['start'] < span['end'] <= len(element['text']), 'invalid evidence span')
        require(row['id'] == evidence_id(ref) and ref['source_id'] == doc['source_id'] and
                element['document_id'] == doc['id'] and ref['element_revision'] == element['revision'] and
                row['document'] == doc and row['source'] == source and row['location'] == element['location'] and
                row['element_kind'] == element['kind'] and
                row['text'] == element['text'][span['start']:span['end']], 'evidence integrity validation failed')
        require((not source_ids or source['id'] in source_ids) and
                (not document_ids or doc['id'] in document_ids), 'evidence escaped selected filters')

    def filters(self, snapshot, source_ids=None, document_ids=None):
        sources = {s['id'] for s in snapshot['identity']['sources']}
        docs = {d['id'] for d in snapshot['identity']['documents']}
        require(source_ids is None or isinstance(source_ids, list), 'source filters must be a list')
        require(document_ids is None or isinstance(document_ids, list), 'document filters must be a list')
        require(set(source_ids or []) <= sources and set(document_ids or []) <= docs,
                'unknown source or document filter; no filter was broadened')
        return {'source_ids': sorted(set(source_ids)) if source_ids else None,
                'document_ids': sorted(set(document_ids)) if document_ids else None}
