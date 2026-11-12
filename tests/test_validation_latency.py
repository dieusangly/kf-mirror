"""Equivalent local-reference expansion, including strict malformed-input checks."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.validate_artifact_examples import SCHEMA, _expand_local_refs, check_claim_evidence

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', ['pdf', 'html'])
@pytest.mark.parametrize('defect', [None, 'root', 'source', 'location', 'span', 'extra', 'claim', 'duplicate'])
def test_expanded_schema_preserves_valid_and_invalid_bundle_outcomes(name, defect):
    bundle = json.loads((ROOT / f'examples/artifact-bundle-{name}.json').read_bytes())
    if defect == 'root': bundle['schema_version'] = 'invalid'
    elif defect == 'source': bundle['sources'][0]['format'] = 'invalid'
    elif defect == 'location': bundle['elements'][0]['location']['format'] = 'invalid'
    elif defect == 'span': bundle['claims'][0]['evidence'][0]['span']['start'] = -1
    elif defect == 'extra': bundle['elements'][0]['unreviewed_extra'] = True
    elif defect == 'claim': del bundle['claims'][0]['generation']
    elif defect == 'duplicate': bundle['builds'][0]['output_claim_ids'] *= 2
    outcomes = []
    for schema in [SCHEMA, _expand_local_refs(SCHEMA)]:
        try:
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(bundle)
            outcomes.append(None)
        except ValidationError as exc:
            outcomes.append((exc.validator, list(exc.absolute_path)))
    assert outcomes[0] == outcomes[1]
    assert (outcomes[0] is None) == (defect is None)


@pytest.mark.parametrize('schema', [
    {'$defs': {'node': {'$ref': '#/$defs/node'}}, '$ref': '#/$defs/node'},
    {'$defs': {'node': {'type': 'string'}}, '$ref': '#/$defs/node', 'minLength': 2},
    {'$ref': 'https://example.org/external'},
    {'properties': {'nested': {'$id': 'urn:test:resource', '$ref': '#/$defs/node'}}},
    {'$dynamicRef': '#node'},
])
def test_unsupported_schema_resources_fall_back_unchanged(schema):
    assert _expand_local_refs(schema) is schema


def test_enum_and_const_objects_are_data_and_formats_remain_checked():
    schema = {'$defs': {'date': {'type': 'string', 'format': 'date'}},
              'properties': {'day': {'$ref': '#/$defs/date'},
                             'data': {'const': {'$ref': 'literal-data'}}}}
    before = copy.deepcopy(schema)
    expanded = _expand_local_refs(schema)
    assert schema == before and expanded['properties']['data']['const'] == {'$ref': 'literal-data'}
    for spec in [schema, expanded]:
        validator = Draft202012Validator(spec, format_checker=FormatChecker())
        validator.validate({'day': '2026-11-11', 'data': {'$ref': 'literal-data'}})
        with pytest.raises(ValidationError):
            validator.validate({'day': '2026-02-31'})


def test_cross_record_exact_span_checks_still_run():
    bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_bytes())
    bundle['claims'][0]['evidence'][0]['span']['end'] = 10**9
    with pytest.raises(ValueError, match='span out of bounds'):
        check_claim_evidence(bundle)


@pytest.mark.parametrize('defect', ['extra', 'span', 'kind', 'array', 'location'])
def test_reference_deduplication_cannot_hide_a_changed_repeated_instance(defect):
    from scripts.validate_artifact_examples import _validate_claim_schema
    bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_bytes())
    schema = copy.deepcopy(SCHEMA)
    # Put an independently copied reference after an identical valid occurrence.
    refs = bundle['chunks'][0]['evidence']
    refs.append(copy.deepcopy(refs[0]))
    if defect == 'extra': refs[-1]['unexpected'] = True
    elif defect == 'span': refs[-1]['span'] = {'start': -1, 'end': 10}
    elif defect == 'kind': refs[-1] = 'not a reference'
    elif defect == 'array': bundle['claims'][0]['evidence'] = []
    elif defect == 'location': bundle['elements'][0]['location']['page'] = 'invalid page'
    outcomes = []
    for validate in [lambda: Draft202012Validator(schema, format_checker=FormatChecker()).validate(bundle),
                     lambda: _validate_claim_schema(schema, bundle)]:
        try:
            validate(); outcomes.append(True)
        except ValidationError:
            outcomes.append(False)
    assert outcomes == [False, False]
