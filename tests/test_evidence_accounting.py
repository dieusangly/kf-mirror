"""Validate accounting mechanics without pretending to verify model semantics."""

import copy
import unittest

from jsonschema import Draft202012Validator, ValidationError

from knowledge_foundry.claim_generation_and_classification import (
    CANDIDATE_SCHEMA,
    _accounting_summary,
    _derive_coverage_links,
    _evidence_accounting,
    _fixture_accounting,
    _validate_accounting_rows,
)


class EvidenceAccountingTests(unittest.TestCase):
    def setUp(self):
        self.choices = {label: {'reference': {'element_id': label}} for label in ['E1', 'E2']}
        self.records = [{'id': 'candidate:one'}]
        self.response = {'claims': [{'text': 'Operators retain logs for two years.', 'evidence': ['E1']}],
                         'coverage': [
                             {'evidence_id': 'E1', 'status': 'represented', 'claim_indices': [0],
                              'reason': 'The retention rule and its duration are represented.'},
                             {'evidence_id': 'E2', 'status': 'insufficient_context', 'claim_indices': [],
                              'reason': 'The referenced instrument is not identified in this passage.'}]}

    def assess(self, response):
        Draft202012Validator(CANDIDATE_SCHEMA).validate(response)
        return _evidence_accounting(response, self.choices, self.records)

    def test_exact_references_and_unresolved_context_are_preserved(self):
        rows = self.assess(self.response)
        self.assertEqual(rows[0]['candidate_ids'], ['candidate:one'])
        self.assertEqual(rows[1]['status'], 'insufficient_context')
        self.assertEqual(rows[1]['reference'], self.choices['E2']['reference'])

    def test_omitted_duplicated_unknown_and_context_labels_rejected(self):
        for label in [None, 'E1', 'E99', 'C1']:
            response = copy.deepcopy(self.response)
            if label is None:
                response['coverage'].pop()
            else:
                response['coverage'][1]['evidence_id'] = label
            with self.subTest(label=label), self.assertRaises((ValueError, ValidationError)):
                self.assess(response)

    def test_claim_indices_must_match_actual_citations(self):
        for indices in [[], [1], [0, 0], [-1]]:
            response = copy.deepcopy(self.response)
            response['coverage'][0]['claim_indices'] = indices
            with self.subTest(indices=indices), self.assertRaises((ValueError, ValidationError)):
                self.assess(response)

    def test_live_links_are_derived_without_changing_coverage_judgments(self):
        response = copy.deepcopy(self.response)
        response['coverage'][0]['claim_indices'] = [99, 99]
        response['coverage'][1]['claim_indices'] = [0]
        fixed = _derive_coverage_links(response)
        self.assertEqual(fixed, self.response)
        self.assertEqual(response['coverage'][0]['claim_indices'], [99, 99])
        self.assess(fixed)
        response['coverage'][0]['status'] = 'non_substantive'
        with self.assertRaisesRegex(ValueError, 'non_substantive'):
            self.assess(_derive_coverage_links(response))

    def test_non_substantive_cannot_hide_a_citing_claim(self):
        response = copy.deepcopy(self.response)
        response['coverage'][0]['status'] = 'non_substantive'
        with self.assertRaisesRegex(ValueError, 'non_substantive'):
            self.assess(response)

    def test_unrepresented_passage_cannot_be_marked_represented(self):
        response = copy.deepcopy(self.response)
        response['coverage'][1]['status'] = 'represented'
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.assess(response)

    def test_zero_claims_still_require_every_passage_to_be_accounted(self):
        response = {'claims': [], 'coverage': [dict(evidence_id=label, status='non_substantive',
                    claim_indices=[], reason='Bibliographic reference only.') for label in self.choices]}
        self.assertEqual(len(self.assess(response)), 2)
        with self.assertRaises(ValidationError):
            self.assess({'claims': []})

    def test_partial_representation_is_explicit_and_fixtures_never_assert_semantic_success(self):
        response = copy.deepcopy(self.response)
        response['coverage'][0]['status'] = 'ambiguous'
        self.assertEqual(self.assess(response)[0]['candidate_ids'], ['candidate:one'])
        fixture = _fixture_accounting({'claims': self.response['claims']}, self.choices)
        self.assertEqual({row['status'] for row in fixture['coverage']}, {'ambiguous'})
        self.assess(fixture)

    def test_saved_accounting_rejects_forged_references_and_candidate_links(self):
        candidates = [{'id': 'candidate:one', 'text': self.response['claims'][0]['text'],
                       'evidence': [self.choices['E1']['reference']]}]
        rows = self.assess(self.response)
        _validate_accounting_rows(rows, self.choices, candidates)
        for kind in ['reference', 'candidate', 'missing']:
            broken = copy.deepcopy(rows)
            if kind == 'reference':
                broken[0]['reference'] = {'element_id': 'foreign'}
            elif kind == 'candidate':
                broken[0]['candidate_ids'] = ['candidate:foreign']
            else:
                broken.pop()
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'invalid saved'):
                _validate_accounting_rows(broken, self.choices, candidates)

    def test_accounting_summary_exposes_partial_and_unaccounted_passages(self):
        summary = _accounting_summary([{'passages': self.assess(self.response)}], 'model_self_report', 3)
        self.assertEqual(summary['status'], 'needs_review')
        self.assertEqual(summary['unaccounted_passages'], 1)
        self.assertEqual(summary['status_counts']['insufficient_context'], 1)
