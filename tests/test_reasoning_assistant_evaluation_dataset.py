"""Evaluation data contracts; these checks never tune or invoke assistant models."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from scripts.generate_reasoning_assistant_evaluation import (
    AUTHORING, FILES, fact_signature, generate, read_rows)
from scripts.validate_reasoning_assistant_evaluation import (
    check_contrast, load_submission)
from scripts.evaluate_reasoning_assistant_inputs import execute_questions
from knowledge_foundry.assistant.datasets import DatasetRegistry, digest


@pytest.fixture(scope='module')
def cases():
    return {c['case_id']:c for c in read_rows(AUTHORING)}


@pytest.fixture(scope='module')
def prepared_dataset(tmp_path_factory):
    directory = tmp_path_factory.mktemp('evaluation') / 'dataset'
    report = generate(directory)
    return directory, report


@pytest.fixture(scope='module')
def dataset(prepared_dataset):
    return prepared_dataset[0]


@pytest.fixture(scope='module')
def results(dataset):
    return {r['question_id']:r['result']['results'][0] for r in read_rows(dataset/FILES['results'])}


def test_frozen_dataset_passes_preparation_and_remains_provisional(prepared_dataset):
    directory, report = prepared_dataset
    assert report['passed'],report['failures']
    assert report['engine_checked_references']==192
    assert report['reference_acceptance'] is False
    assert report['external_calls']==0
    _, manifest = load_submission(directory)
    registry = DatasetRegistry()
    for package_id in ('real-claims-bo-v1', 'real-claims-kyc-v1'):
        query = registry.packages[package_id]['accepted_artifacts']['queries']
        assert manifest['source_hashes'][query['path']] == query['sha256']


def test_all_variants_have_one_fact_change_and_checked_relationship(cases,results):
    for variant in cases.values():
        if variant['question_type'] != 'variant':
            continue
        base=cases[variant['base_case_id']]
        check_contrast(base,variant,results[base['case_id']+'-en'],results[variant['case_id']+'-en'])


def test_extra_fact_change_and_reversed_expectation_are_rejected(cases,results):
    base = cases['RAE1-new-KYC-MH-01']
    variant = deepcopy(cases['RAE1-variant-KYC-MH-01'])
    left,right=results[base['case_id']+'-en'],results[variant['case_id']+'-en']
    variant['facts']=variant['facts'][1:]
    with pytest.raises(ValueError,match='one-fact change'):
        check_contrast(base,variant,left,right)
    variant=deepcopy(cases['RAE1-variant-KYC-MH-01'])
    variant['change']['expected_relation']='answers_contract'
    with pytest.raises(ValueError,match='relationship'):
        check_contrast(base,variant,left,right)


def test_policy_changes_cannot_hide_as_fact_variants(cases,results):
    base = cases['RAE1-new-KYC-MH-01']
    variant = deepcopy(cases['RAE1-variant-KYC-MH-01'])
    variant['context']['en']='A new unaccepted policy.'
    with pytest.raises(ValueError,match='policy'):
        check_contrast(base,variant,results[base['case_id']+'-en'],results[variant['case_id']+'-en'])


def test_renaming_screen_ignores_entity_labels():
    facts=[{'atom':{'predicate':'controls','args':['Person','Company']}}]
    query={'target':{'predicate':'controls','args':['Person','?company']}}
    renamed=[{'atom':{'predicate':'controls','args':['Alice','Acme']}}]
    assert fact_signature(facts,query)==fact_signature(renamed,{'target':{'predicate':'controls','args':['Alice','?x']}})


def test_modified_engine_answer_cannot_retain_valid_certificate(dataset):
    registry=DatasetRegistry()
    encoding=read_rows(dataset/FILES['encodings'])[0]
    query=read_rows(dataset/FILES['queries'])[0]
    result=deepcopy(read_rows(dataset/FILES['results'])[0]['result'])
    result['results'][0]['answers']=[]
    with pytest.raises(ValueError,match='incorrect answers'):
        registry.engine.check_result(encoding,query,registry.packages['synthetic-v1']['engine_config'],result)


def test_tampered_question_and_unsafe_manifest_paths_are_rejected(tmp_path, dataset):
    folder = tmp_path / 'data'
    shutil.copytree(dataset, folder)
    question = folder / FILES['questions']
    question.write_text(question.read_text() + '\n')
    with pytest.raises(ValueError,match='hash differs'):
        load_submission(folder)
    manifest_path=folder/'dataset-manifest-v1.json'
    manifest=json.loads(manifest_path.read_text())
    manifest['roles']['questions']='../outside.jsonl'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='roles differ'):
        load_submission(folder)


def test_missing_translation_cannot_pass_rehashed_submission(tmp_path, dataset):
    folder = tmp_path / 'data'
    shutil.copytree(dataset, folder)
    question = folder / FILES['questions']
    questions = read_rows(question)
    questions[1]['language'] = 'en'
    questions[1]['question_id'] = questions[0]['question_id']
    question.write_text(''.join(json.dumps(q)+'\n' for q in questions))
    path = folder / 'dataset-manifest-v1.json'
    manifest = json.loads(path.read_text())
    manifest['files'][FILES['questions']]['sha256']=digest(question)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='duplicate question ID'):
        load_submission(folder)


def test_submission_never_parses_reference_files(monkeypatch,tmp_path, dataset):
    original=Path.read_text
    def guarded(path,*args,**kwargs):
        if path.name in {FILES[r] for r in ('references','encodings','queries','results')}:
            raise AssertionError('Reference content must not enter submission loading')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded)
    questions,_=load_submission(dataset)
    seen=[]
    class Backend:
        def ask(self,text,history):
            seen.append((text,history))
            return {'route':'clarify','status':'clarification'}
    execute_questions(Backend(),questions,tmp_path/'run',{},max_prompts=2,budget=1,reserve=.1)
    assert seen==[(q['text'],[]) for q in questions[:2]]


def test_changed_source_pin_blocks_submission(tmp_path, dataset):
    folder = tmp_path / 'data'
    shutil.copytree(dataset, folder)
    path = folder / 'dataset-manifest-v1.json'
    manifest = json.loads(path.read_text())
    manifest['source_hashes']['config/aml-cft-reasoning-datasets.json']='0'*64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='pinned source changed'):
        load_submission(folder)
