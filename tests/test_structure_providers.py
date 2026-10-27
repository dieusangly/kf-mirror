"""Native export adapters preserve vendor structure and never acquire sources."""
import json
from pathlib import Path

import pytest

from knowledge_foundry.provider_exports import adapt_export
from knowledge_foundry.parsing import _normalize, parse_collection
from knowledge_foundry.structure_providers import ExportProvider
from test_pdf_tables import write_pdf


@pytest.fixture
def artifact(tmp_path):
    from knowledge_foundry.ingestion import ingest, load_ingested
    import hashlib
    path = tmp_path / 'source.pdf'; write_pdf(path)
    raw = path.read_bytes()
    manifest = {'schema_version': '1.0.0', 'sources': [{'manifest_id': 'X', 'source_id': 'src:export',
        'title': 'Export test', 'class': 'test', 'source_version': 'v1', 'publisher_url': 'https://example.org/test',
        'local_path': path.name, 'retrieved_on': '2026-10-27', 'media_type': 'application/pdf',
        'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    config = tmp_path / 'pipeline.json'
    config.write_text(json.dumps({'root': '.', 'manifest_file': 'manifest.json', 'manifest_ids': ['X'], 'ingested_dir': 'ingested'}))
    ingest(config, tmp_path / 'ingested')
    return load_ingested(tmp_path / 'ingested')['outcomes'][0]


def normalized(name, native, artifact):
    result = adapt_export(name, native, artifact)
    bundle, metadata = _normalize(artifact['source'], artifact['document'], result.elements)
    return result, bundle, metadata


def test_unstructured_preserves_table_html_hierarchy_and_pixel_coordinates(artifact):
    table_html = '<table><tr><th colspan="2">Checks</th></tr><tr><td>Sensor</td><td>Daily</td></tr></table>'
    native = [
        {'element_id': 'heading', 'type': 'Title', 'text': 'Controls', 'metadata': {'page_number': 1}},
        {'element_id': 'table', 'type': 'Table', 'text': 'Checks\nSensor | Daily', 'metadata': {
            'page_number': 1, 'parent_id': 'heading', 'text_as_html': table_html,
            'coordinates': {'points': [[10, 20], [90, 60]], 'layout_width': 100, 'layout_height': 100}}}]
    result, bundle, metadata = normalized('unstructured', native, artifact)
    assert result.native == native and result.chunks is None
    assert metadata[1]['parent_element_id'] == bundle['elements'][0]['id']
    assert metadata[1]['provider_metadata']['metadata']['text_as_html'] == table_html
    assert bundle['elements'][1]['location']['bbox'][0] > 10  # points, not normalized pixels


def test_unstructured_ready_chunks_retain_boundaries(artifact):
    native = [{'element_id': 'chunk', 'type': 'CompositeElement', 'text': 'Keep the complete rule.',
               'metadata': {'page_number': 1, 'orig_elements': 'vendor-native-originals'}}]
    result, _, metadata = normalized('unstructured', native, artifact)
    assert result.chunks == [{'text': native[0]['text'], 'evidence': [{'locator_key': 'chunk'}]}]
    assert metadata[0]['provider_metadata']['metadata']['orig_elements'] == 'vendor-native-originals'


def test_llamaparse_nested_lists_tables_and_bounding_boxes(artifact):
    native = {'items': {'pages': [{'page_number': 1, 'success': True, 'items': [
        {'type': 'heading', 'level': 1, 'value': 'Controls', 'bbox': [{'x': 20, 'y': 30, 'w': 100, 'h': 20}]},
        {'type': 'list', 'ordered': True, 'items': [{'type': 'text', 'value': '1. Inspect.'}, {'type': 'text', 'value': '2. Record.'}]},
        {'type': 'table', 'rows': [['Region', 'Result'], ['North', 'Passed']], 'html': '<table>native cells</table>'}]}]}}
    result, bundle, metadata = normalized('llamaparse', native, artifact)
    assert [e['kind'] for e in bundle['elements']] == ['heading', 'paragraph', 'list_item', 'list_item', 'table']
    assert metadata[2]['parent_element_id'] == bundle['elements'][1]['id']
    assert bundle['elements'][0]['location']['bbox'] == [20, 30, 120, 50]
    assert metadata[-1]['provider_metadata']['rows'][1] == ['North', 'Passed']
    assert result.native is native


def test_mineru_preserves_table_and_does_not_convert_page_zero_to_missing(artifact):
    native = [{'type': 'text', 'text': 'Controls', 'text_level': 1, 'page_idx': 0, 'bbox': [0, 0, 1000, 100]},
              {'type': 'table', 'table_body': '<table><tr><th>Sensor</th></tr><tr><td>Passed</td></tr></table>',
               'table_caption': ['Inspection result'], 'page_idx': 0, 'bbox': [0, 100, 1000, 300]}]
    _, bundle, metadata = normalized('mineru', native, artifact)
    assert bundle['elements'][1]['location']['page'] == 1
    assert bundle['elements'][1]['text'] == 'Sensor\nPassed'
    assert metadata[1]['provider_metadata']['table_caption'] == ['Inspection result']


def test_mistral_markdown_and_separate_html_tables_keep_structure(artifact):
    native = {'model': 'export-model', 'pages': [{'index': 0, 'markdown': '# Controls\n\n[tbl.html](tbl.html)',
                'tables': [{'id': 'tbl.html', 'format': 'html', 'content': '<table><tr><th>Sensor</th><th>Result</th></tr><tr><td>A</td><td>Passed</td></tr></table>'}],
                'blocks': [{'label': 'table', 'bbox': [10, 30, 100, 80]}], 'confidence_scores': {'mean': .98}}]}
    result, bundle, metadata = normalized('mistral', native, artifact)
    assert any(e['kind'] == 'table' and 'Passed' in e['text'] for e in bundle['elements'])
    assert result.native['pages'][0]['blocks'] == native['pages'][0]['blocks']
    assert metadata[0]['provider_metadata']['confidence_scores'] == {'mean': .98}


def test_export_bridge_rejects_a_response_from_other_source_bytes(artifact, tmp_path):
    (tmp_path / 'X.json').write_text(json.dumps({'source_sha256': '0' * 64, 'native': []}))
    with pytest.raises(ValueError, match='not bound'):
        ExportProvider().structure(artifact, {'response_dir': str(tmp_path), 'format': 'unstructured'})


def test_unknown_elements_and_failed_pages_fail_explicitly(artifact):
    with pytest.raises(ValueError, match='unknown provider element'):
        normalized('unstructured', [{'type': 'Unsupported', 'metadata': {'page_number': 1}}], artifact)
    with pytest.raises(ValueError, match='failed page'):
        normalized('llamaparse', {'items': {'pages': [{'page_number': 1, 'success': False}]}}, artifact)
