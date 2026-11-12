"""Minimum POC activation gates retain safety and do not require three positives."""
import copy
import importlib.util
import json

import pytest

from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import ROOT

PATH=ROOT/'artifacts/20261028-docling-aml-cft-01/reasoning-poc-01/poc-checks/poc-activate.py'


def module():
    spec=importlib.util.spec_from_file_location('poc_activation_gate',PATH)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def evidence(tmp_path):
    inventory={};results=[]
    for i in range(1,10):
        cid=f'poc-{i:02d}';inventory[cid]={'id':cid,'expected_status':'reasoned' if i<=6 else 'insufficient'}
        path=tmp_path/f'poc-case-{i:02d}.json';path.write_text(json.dumps({'fixture':'policy-only','case':cid}))
        verified=i in (6,8,9);digest=file_hash(path)
        results.append({'id':cid,'evidence':path.name,'evidence_sha256':digest,'ui_verified':verified,
                        'verification_result':'pass' if verified else 'fail','mechanical_pass':verified,
                        'source_review':{'evidence_sha256':digest,'verdict':'pass' if verified else 'fail'}})
    return {'results':results,'remaining_case_ids':[]},inventory


def test_one_verified_positive_and_two_withholds_are_enough(tmp_path):
    verification,inventory=evidence(tmp_path)
    assert module().minimum_gates(verification,inventory,tmp_path)==['poc-06','poc-08','poc-09']


@pytest.mark.parametrize('defect',['missing_outcome','duplicate_outcome','pending','tampered','failed_suggestion','no_positive'])
def test_minimum_gate_rejects_integrity_or_honesty_failures(tmp_path,defect):
    verification,inventory=evidence(tmp_path)
    if defect=='missing_outcome':verification['results'].pop()
    elif defect=='duplicate_outcome':verification['results'][-1]=copy.deepcopy(verification['results'][-2])
    elif defect=='pending':verification['remaining_case_ids']=['poc-09']
    elif defect=='tampered':(tmp_path/'poc-case-01.json').write_text('tampered')
    elif defect=='failed_suggestion':verification['results'][0]['ui_verified']=True
    else:verification['results'][5]['ui_verified']=False
    with pytest.raises(ValueError):module().minimum_gates(verification,inventory,tmp_path)
