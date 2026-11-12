"""Unified binding configuration preserves the activated POC; no model calls."""
import json
import pytest

from knowledge_foundry.assistant.bindings import BindingFailure, KnowledgeBinding
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.reasoning.poc import load_poc
from knowledge_foundry.retrieval.config import ROOT


def test_unified_poc_pins_the_existing_accepted_snapshot_and_full_documents():
    b = KnowledgeBinding.load()
    active = json.loads((ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-poc-01/poc-checks/poc-application-activation.json').read_text())
    assert b.value['accepted']['corpus'] == 'reasoning-poc-01'
    assert b.value['accepted']['snapshot_id'] == active['snapshot_id']
    assert b.value['accepted']['mapping_count'] == active['accepted_mappings'] == 2164
    assert b.value['documents']['snapshot_id'] != b.value['accepted']['snapshot_id']
    assert b.value['documents']['corpus'] == 'aml-cft-pilot-13'
    poc = load_poc(ROOT / b.value['accepted']['poc_reference'], 'reasoning-poc-01')
    assert b.dataset_path() == poc['dataset_path']
    assert b.value['accepted']['poc_reference_sha256'] == file_hash(ROOT / b.value['accepted']['poc_reference'])
    assert 'examples' not in b.value and 'questions' not in b.value
    b.value['accepted']['poc_reference_sha256'] = '0' * 64
    with pytest.raises(BindingFailure, match='reference changed'):
        b.dataset_path()
