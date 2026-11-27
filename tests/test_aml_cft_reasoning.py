"""Stage 2 controls. Run with python tests/test_aml_cft_reasoning.py.

Uses unittest only; does not load database/application pytest fixtures.
"""
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('reasoning', Path(__file__).resolve().parents[1] / 'scripts/aml_cft_reasoning.py')
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
CONFIG = dict(engine_version=r.VERSION, adapter_version=r.VERSION, checker_version=r.VERSION,
              schema_version=r.SCHEMA, semantics='candidate-conflict-then-usable-closure-1',
              max_ground_instances=100000, max_atoms=10000)


def atom(p, *args):
    return {'predicate': p, 'args': list(args)}


def rule(name, body, head):
    return {'id': name, 'source_ids': ['P1'], 'variables': {'?x': 'item'}, 'body': body, 'head': head}


def control_batch(root):
    """Write a generic bilingual fixture dataset, never the selected 96 cases."""
    enc, query = r.fixture()
    data = {
        'inputs-v1.jsonl': [{'dataset_id': enc['dataset_id'], 'dataset_version': enc['dataset_version'],
                            'case_id': enc['case_id'], 'language': lang, 'scope': {'id': enc['scope_id']},
                            'scenario_facts': [{'id': 'F1'}, {'id': 'F2'}],
                            'policy_clauses': [{'id': 'P' + str(i)} for i in range(1, 5)]} for lang in ('en', 'fr')],
        'questions-v1.jsonl': [{'case_id': enc['case_id'], 'language': lang, 'question_id': 'Q1'} for lang in ('en', 'fr')],
        'proposed-references-v1.jsonl': [{'case_id': enc['case_id'], 'language': lang} for lang in ('en', 'fr')],
    }
    files = {}
    lang_records = {lang: {} for lang in ('en', 'fr')}
    for name, records in data.items():
        path = root / name
        path.write_text(''.join(r.canonical(x) + '\n' for x in records))
        files[name] = {'sha256': r.digest(path), 'records': len(records)}
        for i, row in enumerate(records, 1):
            lang_records[row['language']][name] = {'line': i, 'canonical_sha256': r.hashlib.sha256(r.canonical(row).encode()).hexdigest()}
    manifest = {'dataset_id': enc['dataset_id'], 'dataset_version': enc['dataset_version'],
                'files': files, 'families': {'KYC': ['control']},
                'cases': [{'case_id': 'control', 'language_versions': lang_records}]}
    mp = root / 'dataset-manifest.json'
    mp.write_text(json.dumps(manifest))
    batch = {'batch_id': 'generic-setup-control', 'dataset_id': enc['dataset_id'], 'dataset_version': enc['dataset_version'],
             'source_dataset_manifest': {'path': str(mp), 'sha256': r.digest(mp)}, 'selected_families': ['KYC'],
             'case_ids': ['control'], 'languages': ['en', 'fr'], 'semantic_cases': 1, 'language_versions': 2,
             'selection': [{'case_id': 'control', 'language': lang, 'source_records': lang_records[lang]} for lang in ('en', 'fr')],
             'source_files': {str(root / name): v['sha256'] for name, v in files.items()}}
    (root / 'batch.json').write_text(json.dumps(batch))
    (root / 'config.json').write_text(json.dumps(CONFIG))
    encs, queries = [], []
    for lang in ('en', 'fr'):
        e, q = deepcopy(enc), deepcopy(query)
        e['language'] = q['language'] = lang
        encs.append(e)
        queries.append(q)
    (root / 'encodings.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in encs))
    (root / 'queries.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in queries))
    return batch, encs, queries


class ReasoningControls(unittest.TestCase):
    def setUp(self):
        self.enc, self.q = r.fixture()

    def solve(self):
        result = r.solve(self.enc, self.q, CONFIG)
        r.check_result(self.enc, self.q, CONFIG, result)
        return result

    def fact(self, p, *args):
        self.enc['facts'].append({'id': 'f' + str(len(self.enc['facts']) + 1), 'source_ids': ['F2'], 'atom': atom(p, *args)})

    def status(self, result):
        return [x['status'] for x in result['results']]

    def test_lookup_one_and_two_hop_have_real_certificates(self):
        result = self.solve()
        self.assertEqual(self.status(result), ['entailed', 'entailed', 'entailed', 'unknown'])
        proof = result['usable_certificate']
        self.assertEqual([x.get('rule_id') for x in proof], [None, 'r1', 'r2'])
        self.assertEqual(proof[-1]['premises'], [atom('ready', 'a')])

    def test_three_hop_needs_positive_clearance(self):
        self.fact('clearance', 'a')
        result = self.solve()
        self.assertEqual(result['results'][-1]['status'], 'entailed')
        self.assertEqual(r.check_result(self.enc, self.q, CONFIG, result)['deduction_steps'], 3)

    def test_missing_premise_is_named_not_negated(self):
        missing = self.solve()['results'][-1]
        self.assertEqual(missing['status'], 'unknown')
        self.assertIn('clearance', json.dumps(missing['missing']))
        self.assertEqual(missing['answers'], [])

    def test_entity_mismatch_does_not_supply_clearance(self):
        self.fact('clearance', 'b')
        self.assertEqual(self.solve()['results'][-1]['status'], 'unknown')

    def test_derived_conflict_blocks_downstream_preserves_unrelated(self):
        self.fact('denied', 'a')
        result = self.solve()
        self.assertEqual(self.status(result)[:3], ['entailed', 'conflict', 'conflict'])
        self.assertEqual([x['atom'] for x in result['usable_certificate']], [atom('seed', 'a')])

    def test_asserted_conflict(self):
        self.enc['rules'] = []
        self.fact('ready', 'a')
        self.fact('denied', 'a')
        self.assertEqual(self.solve()['results'][1]['status'], 'conflict')

    def test_conflicts_are_entity_local(self):
        self.fact('denied', 'b')
        self.assertEqual(self.solve()['results'][1]['status'], 'entailed')

    def test_binary_join_and_projected_lookup(self):
        self.enc['predicates']['link'] = ['item', 'item']
        self.enc['predicates']['gate'] = ['item']
        self.fact('link', 'a', 'b')
        self.fact('gate', 'b')
        self.enc['rules'].append({'id': 'join', 'source_ids': ['P1'], 'variables': {'?x': 'item', '?y': 'item'},
                                  'body': [atom('link', '?x', '?y'), atom('gate', '?y')], 'head': atom('released', '?x')})
        self.q['queries'] = [{'id': 'lookup', 'question_id': 'Q1', 'target': atom('link', 'a', '?v'),
                              'variables': {'?v': 'item'}, 'select': ['?v']},
                             {'id': 'join', 'question_id': 'Q1', 'target': atom('released', 'a'), 'variables': {}, 'select': []}]
        result = self.solve()
        self.assertEqual(result['results'][0]['answers'], [{'?v': 'b'}])
        self.assertEqual(result['results'][1]['status'], 'entailed')

    def test_binary_incompatibility_never_crosses_pairs(self):
        self.enc['predicates'].update({'link': ['item', 'item'], 'no_link': ['item', 'item']})
        self.fact('link', 'a', 'b')
        self.fact('no_link', 'a', 'c')
        self.enc['incompatibilities'] = [{'id': 'pair', 'source_ids': ['P1'], 'variables': {'?x': 'item', '?y': 'item'},
                                         'left': atom('link', '?x', '?y'), 'right': atom('no_link', '?x', '?y')}]
        self.assertEqual(self.solve()['conflicts'], [])
        self.fact('no_link', 'a', 'b')
        self.assertEqual(len(self.solve()['conflicts']), 1)

    def test_safe_alternative_derivation_survives_conflicted_route(self):
        self.fact('denied', 'a')
        self.enc['rules'].append(rule('safe', [atom('seed', '?x')], atom('done', '?x')))
        self.assertEqual(self.solve()['results'][2]['status'], 'entailed')

    def test_unsupported_cycle_does_not_prove_itself(self):
        self.enc['facts'] = []
        self.enc['rules'].append(rule('cycle', [atom('done', '?x')], atom('seed', '?x')))
        result = self.solve()
        self.assertEqual(result['usable_certificate'], [])
        self.assertIn('unsupported-cycle', json.dumps(result['results']))

    def test_language_field_does_not_change_symbolic_result(self):
        en = self.solve()['results']
        self.enc['language'] = self.q['language'] = 'fr'
        self.assertEqual(self.solve()['results'], en)

    def test_unsafe_head_rejected(self):
        self.enc['rules'][0]['variables']['?z'] = 'item'
        self.enc['rules'][0]['head']['args'] = ['?z']
        with self.assertRaisesRegex(r.Invalid, 'unsafe'):
            self.solve()

    def test_wrong_type_and_undeclared_entity_rejected(self):
        for entity in ('unknown', 'b'):
            e = deepcopy(self.enc)
            e['entities']['b'] = 'other'
            e['facts'][0]['atom']['args'] = [entity]
            with self.assertRaises(r.Invalid):
                r.solve(e, self.q, CONFIG)

    def test_empty_rule_and_extra_hint_rejected(self):
        for kind in ('body', 'hint'):
            e = deepcopy(self.enc)
            if kind == 'body':
                e['rules'][0]['body'] = []
            else:
                e['expected_answer'] = 'entailed'
            with self.assertRaises(r.Invalid):
                r.solve(e, self.q, CONFIG)

    def test_bound_exceeded_is_error_not_silent_truncation(self):
        with self.assertRaisesRegex(r.Invalid, 'limit'):
            r.solve(self.enc, self.q, {**CONFIG, 'max_ground_instances': 1})
        with self.assertRaisesRegex(r.Invalid, 'limit'):
            r.solve(self.enc, self.q, {**CONFIG, 'max_atoms': 1})

    def test_checker_rejects_invented_assertion(self):
        result = self.solve()
        result['usable_certificate'][0]['atom'] = atom('seed', 'b')
        with self.assertRaises(r.Invalid):
            r.check_result(self.enc, self.q, CONFIG, result)

    def test_checker_rejects_wrong_substitution_or_missing_premise(self):
        for field, value in [('binding', {'?x': 'b'}), ('premises', [])]:
            result = self.solve()
            result['usable_certificate'][1][field] = value
            with self.assertRaises(r.Invalid):
                r.check_result(self.enc, self.q, CONFIG, result)

    def test_checker_rejects_empty_truncated_reordered_certificates(self):
        for change in ('empty', 'truncate', 'reorder'):
            result = self.solve()
            proof = result['usable_certificate']
            result['usable_certificate'] = [] if change == 'empty' else proof[:-1] if change == 'truncate' else list(reversed(proof))
            with self.assertRaises(r.Invalid):
                r.check_result(self.enc, self.q, CONFIG, result)

    def test_checker_rejects_wrong_status_or_stale_input(self):
        result = self.solve()
        result['results'][-1]['status'] = 'entailed'
        with self.assertRaises(r.Invalid):
            r.check_result(self.enc, self.q, CONFIG, result)
        result = self.solve()
        self.fact('clearance', 'a')
        with self.assertRaisesRegex(r.Invalid, 'stale'):
            r.check_result(self.enc, self.q, CONFIG, result)

    def test_checker_rejects_hidden_conflict_or_blocked_derivation(self):
        self.fact('denied', 'a')
        result = self.solve()
        result['conflicts'] = []
        with self.assertRaises(r.Invalid):
            r.check_result(self.enc, self.q, CONFIG, result)
        result = self.solve()
        result['usable_certificate'] = result['candidate_certificate']
        with self.assertRaises(r.Invalid):
            r.check_result(self.enc, self.q, CONFIG, result)


class CommandControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch, self.encs, self.queries = control_batch(self.root)

    def call(self, command, output, *extra):
        argv = ['reasoning', command, '--batch', str(self.root / 'batch.json'), '--config', str(self.root / 'config.json'), '--output', str(self.root / output), *extra]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return r.main()

    def io_args(self):
        return ['--encodings', str(self.root / 'encodings.jsonl'), '--queries', str(self.root / 'queries.jsonl')]

    def test_cli_selection_setup_validate_execute_check(self):
        for command in ('validate-selection', 'verify-setup', 'validate', 'execute'):
            args = self.io_args() if command in ('validate', 'execute') else []
            self.assertEqual(self.call(command, command + '.json', *args), 0)
        self.assertEqual(self.call('check', 'check.json', *self.io_args(), '--results', str(self.root / 'execute.json')), 0)
        self.assertEqual(len(r.read(self.root / 'check.json')['checks']), 2)

    def test_cli_generation_failure_kept_and_other_language_executes(self):
        self.encs[0] = {**{k: self.encs[0][k] for k in (*r.IDENTITY, 'schema_version')}, 'generation_error': 'saved failure'}
        (self.root / 'encodings.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in self.encs))
        self.assertEqual(self.call('validate', 'validate.json', *self.io_args()), 1)
        self.assertEqual(self.call('execute', 'execute.json', *self.io_args()), 1)
        records = r.read(self.root / 'execute.json')['records']
        self.assertIn('error', records[0])
        self.assertIn('usable_certificate', records[1])
        self.assertEqual(self.call('check', 'check.json', *self.io_args(), '--results', str(self.root / 'execute.json')), 1)

    def test_scope_and_provenance_failures_are_recorded(self):
        self.encs[0]['scope_id'] = 'wrong'
        self.encs[1]['facts'][0]['source_ids'] = ['invented']
        (self.root / 'encodings.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in self.encs))
        self.assertEqual(self.call('validate', 'bad.json', *self.io_args()), 1)
        self.assertTrue(all(not c['valid'] for c in r.read(self.root / 'bad.json')['checks']))

    def test_selection_tampering_detected(self):
        for prop, value in [('case_ids', []), ('languages', ['en']), ('language_versions', 4), ('selection', [])]:
            bad = {**self.batch, prop: value}
            with self.assertRaises(r.Invalid):
                r.selection(bad)
        (self.root / 'inputs-v1.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(r.Invalid, 'hash'):
            r.selection(self.batch)

    def test_missing_or_duplicate_case_rejected(self):
        for records in (self.encs[:1], self.encs + self.encs[:1]):
            with self.assertRaises(r.Invalid):
                r.selected_records(self.batch, records, self.queries)

    def test_output_overwrite_is_rejected(self):
        self.assertEqual(self.call('validate-selection', 'frozen.json'), 0)
        original = (self.root / 'frozen.json').read_bytes()
        with self.assertRaises(SystemExit) as error:
            self.call('validate-selection', 'frozen.json')
        self.assertEqual(error.exception.code, 2)
        self.assertEqual((self.root / 'frozen.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
