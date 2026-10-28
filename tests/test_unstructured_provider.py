"""Local execution contract, independent of optional inference installation."""
import sys
from types import SimpleNamespace

import pytest

from knowledge_foundry.unstructured_provider import Provider
from knowledge_foundry.parsing import _normalize
from test_structure_providers import artifact  # noqa: F401


def sdk(monkeypatch, rows, effective='fast'):
    calls = []
    def partition_pdf(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(to_dict=lambda row=row: row) for row in rows]
    monkeypatch.setitem(sys.modules, 'unstructured.partition.pdf', SimpleNamespace(partition_pdf=partition_pdf))
    monkeypatch.setitem(sys.modules, 'unstructured.partition.strategies', SimpleNamespace(
        determine_pdf_or_image_strategy=lambda **kwargs: effective))
    monkeypatch.setattr('knowledge_foundry.unstructured_provider.version', lambda name: 'test-version')
    return calls


def test_local_pdf_uses_saved_bytes_and_retains_native_data(artifact, monkeypatch):
    rows = [{'element_id': 'h', 'type': 'Title', 'text': 'Controls', 'metadata': {'page_number': 1}},
            {'element_id': 't', 'type': 'Table', 'text': 'Count 42', 'metadata': {
                'page_number': 1, 'parent_id': 'h', 'text_as_html': '<table><tr><td>42</td></tr></table>',
                'coordinates': {'points': [[10, 20], [90, 60]], 'layout_width': 100, 'layout_height': 100}}}]
    calls = sdk(monkeypatch, rows, 'hi_res')
    result = Provider().structure(artifact, {'strategy': 'hi_res'})
    assert calls == [{'filename': artifact['artifact_file'], 'include_page_breaks': False,
                      'strategy': 'hi_res', 'languages': ['fra', 'eng'], 'infer_table_structure': True,
                      'hi_res_model_name': 'yolox'}]
    assert result.native == rows
    assert result.metadata['service_cost_usd'] == 0
    assert result.metadata['effective_strategy'] == 'hi_res'
    assert result.elements[1]['parent_key'] == 'h'
    assert result.elements[1]['location']['bbox'] == [60, 160, 540, 480]
    assert result.elements[1]['details']['provider_metadata']['metadata']['text_as_html'].startswith('<table>')
    assert __import__('os').environ['DO_NOT_TRACK'] == 'true'
    assert _normalize(artifact['source'], artifact['document'], result.elements)


@pytest.mark.parametrize('options', [{'url': 'https://example.org'}, {'strategy': 'auto'},
                                    {'strategy': 'fast', 'infer_table_structure': True},
                                    {'languages': 'fra'}, {'hi_res_model_name': 'yolox'}])
def test_invalid_or_remote_options_fail_before_loading_sdk(artifact, options):
    with pytest.raises(ValueError):
        Provider().structure(artifact, options)


def test_silent_strategy_fallback_is_rejected(artifact, monkeypatch):
    calls = sdk(monkeypatch, [], 'ocr_only')
    with pytest.raises(ValueError, match='fall back'):
        Provider().structure(artifact, {'strategy': 'hi_res'})
    assert not calls


def test_empty_pages_are_reported_and_unknown_types_fail(artifact, monkeypatch):
    sdk(monkeypatch, [])
    result = Provider().structure(artifact, {})
    assert result.warnings[0]['code'] == 'unstructured_page_without_elements'
    assert result.warnings[0]['page'] == 1
    sdk(monkeypatch, [{'type': 'Unknown', 'metadata': {'page_number': 1}}])
    with pytest.raises(ValueError, match='unknown provider element'):
        Provider().structure(artifact, {})


def test_sdk_categories_and_forward_parent_do_not_corrupt_hierarchy(artifact, monkeypatch):
    rows = [{'element_id': str(i), 'type': kind, 'text': kind,
             'metadata': {'page_number': 1, **({'parent_id': '7'} if i == 0 else {})}}
            for i, kind in enumerate(['NarrativeText', 'Header', 'Footer', 'PageNumber',
                                     'UncategorizedText', 'Formula', 'CodeSnippet', 'Title', 'Footnote'])]
    sdk(monkeypatch, rows)
    result = Provider().structure(artifact, {})
    assert result.elements[0]['parent_key'] is None
    assert result.warnings[0]['code'] == 'unstructured_unresolved_parent'
    assert all(e['details']['evidence_role'] == 'structural' for e in result.elements[1:4])
    assert all(e['kind'] == 'paragraph' for e in result.elements[1:4])
    assert result.native == rows
    assert result.elements[-1]['kind'] == 'footnote'


def test_non_pdf_requires_an_explicit_override(artifact):
    with pytest.raises(ValueError, match='source_overrides'):
        Provider().structure({**artifact, 'source': {'format': 'html'}}, {})


def test_duplicate_native_ids_preserve_both_occurrences_and_ambiguous_parent(artifact, monkeypatch):
    rows = [{'element_id': 'same', 'type': 'Title', 'text': 'Repeated title', 'metadata': {'page_number': 1}},
            {'element_id': 'same', 'type': 'Title', 'text': 'Repeated title', 'metadata': {'page_number': 1}},
            {'element_id': 'child', 'type': 'Text', 'text': 'Body', 'metadata': {'page_number': 1, 'parent_id': 'same'}}]
    sdk(monkeypatch, rows)
    result = Provider().structure(artifact, {})
    assert len({e['locator_key'] for e in result.elements}) == 3
    assert result.elements[-1]['parent_key'] is None
    assert result.native == rows
    assert _normalize(artifact['source'], artifact['document'], result.elements)


def test_native_replay_cannot_hide_an_empty_semantic_element(artifact, monkeypatch, tmp_path):
    import json
    from knowledge_foundry.parsing_audit import audit_provider_document
    native = [{'element_id': 'empty', 'type': 'ListItem', 'text': '\n', 'metadata': {'page_number': 1}}]
    sdk(monkeypatch, native)
    result = Provider().structure(artifact, {})
    bundle, details = _normalize(artifact['source'], artifact['document'], result.elements)
    (tmp_path / 'provider.json').write_text(json.dumps(native))
    audit = audit_provider_document({'manifest_id': 'X'}, bundle,
                                    {**result.metadata, 'elements': details}, tmp_path, artifact['artifact_file'])
    assert audit['metrics']['empty_semantic_elements'] == 1
    assert [f['code'] for f in audit['findings']] == ['EMPTY_SEMANTIC_ELEMENT']
