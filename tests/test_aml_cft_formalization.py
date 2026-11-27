"""Stage 3 boundary/retention controls using generic, non-dataset fixtures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import aml_cft_formalization as formalizer
import aml_cft_reasoning as engine


class FormalizationTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(scope=dict(id='scope', text='one scope'),
                           scenario_facts=[dict(id='source-f', text='a is seeded')],
                           policy_clauses=[dict(id='source-r', text='seed implies ready')],
                           title='SECRET', category='SECRET', expected_answer='SECRET')
        self.mapping = dict(scopes=['one scope'], predicates=dict(seed=['entity'], ready=['entity']),
                            facts={'a is seeded': [dict(kind='facts', atom=dict(predicate='seed', args=['a']))]},
                            policies={'seed implies ready': [dict(kind='rules', variables={'?x':'entity'}, body=[dict(predicate='seed', args=['?x'])], head=dict(predicate='ready', args=['?x']))]})
        self.identity = dict(dataset_id='generic', dataset_version='1', case_id='case', language='en', schema_version=engine.SCHEMA)

    def request(self):
        return formalizer.encoding_request(self.source, {}, 'encode supplied text')

    def test_request_allowlist_excludes_metadata_and_questions(self):
        request = self.request()
        self.assertEqual(set(request), formalizer.ENCODE_KEYS)
        self.assertNotIn('SECRET', json.dumps(request))
        self.assertEqual(set(request['scenario_facts'][0]), {'id','text'})

    def test_injected_extra_fields_rejected(self):
        for name in ['question', 'expected_answer', 'reference_proof', 'title', 'category', 'structural_pattern']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                formalizer.encode({**self.request(), name:'forbidden'}, self.mapping)

    def test_metadata_and_case_id_do_not_select_templates(self):
        first = formalizer.encode(self.request(), self.mapping)
        self.source.update(case_id='arbitrary', title='different', category='different')
        self.assertEqual(first, formalizer.encode(self.request(), self.mapping))

    def test_original_source_links_and_universal_variables(self):
        result = {**self.identity, **formalizer.encode(self.request(), self.mapping)}
        engine.check_encoding(result)
        self.assertEqual(result['rules'][0]['source_ids'], ['source-r'])
        self.assertEqual(result['rules'][0]['variables'], {'?x':'entity'})
        self.assertEqual(len(result['facts']), 1)  # no materialized rule head

    def test_unknown_text_and_scope_fail(self):
        for field in ['scenario_facts','policy_clauses']:
            request = self.request()
            request[field][0]['text'] = 'unrecognized'
            with self.assertRaises(ValueError): formalizer.encode(request, self.mapping)
        request = self.request()
        request['scope']['text'] = 'other'
        with self.assertRaises(ValueError): formalizer.encode(request, self.mapping)

    def test_unsafe_binding_is_rejected_by_adapter(self):
        self.mapping['policies']['seed implies ready'][0]['head']['args'] = ['?y']
        result = {**self.identity, **formalizer.encode(self.request(), self.mapping)}
        with self.assertRaises(engine.Invalid): engine.check_encoding(result)

    def test_query_request_contains_no_facts_or_rules(self):
        enc = formalizer.encode(self.request(), self.mapping)
        request = formalizer.query_request(dict(question_id='q', text='ready?'), enc, {}, 'translate')
        self.assertEqual(set(request), formalizer.QUERY_KEYS)
        self.assertEqual(set(request['vocabulary']), {'entities','predicates'})
        self.assertNotIn('facts', json.dumps(request))

    def test_unknown_query_and_extra_fields_fail(self):
        request = dict(question={'text':'unknown'}, vocabulary={}, target_schema={}, instructions='translate')
        with self.assertRaises(ValueError): formalizer.translate_query(request, {'questions':{}})
        with self.assertRaises(ValueError): formalizer.translate_query({**request,'expected_status':'entailed'}, {'questions':{}})

    def test_pipeline_preserves_failed_case_and_raw_invalid_response(self):
        batch = dict(dataset_id='generic', dataset_version='1', batch_id='b', source_dataset_manifest={'path':'manifest.json'},
                     selection=[dict(case_id='case', language='en',source_records={'inputs-v1.jsonl':{'line':1}})])
        config = dict(schema_path='schema', encoding_instructions='encode', mappings_path='mapping')
        source = {'inputs-v1.jsonl':{('case','en'):self.source}, 'questions-v1.jsonl':{('case','en'):{'question_id':'q'}}}
        self.mapping['policies']['seed implies ready'][0]['head']['args'] = ['?y']
        with tempfile.TemporaryDirectory() as tmp, patch.object(formalizer,'verify',return_value=(config,batch,source,self.mapping)), patch.object(formalizer,'read',return_value={}), patch.object(formalizer,'hash_file',return_value='hash'), patch.object(engine,'solve',side_effect=AssertionError('reasoning forbidden')):
            out = Path(tmp)/'out'
            self.assertFalse(formalizer.generate('config',out,'encode'))
            record = json.loads((out/'encodings-v1.jsonl').read_text())
            self.assertIn('generation_error',record)
            raw = json.loads((out/'responses.jsonl').read_text())
            self.assertIn('rules',raw['response'])
            self.assertIsNotNone(raw['validation_error'])
            report = json.loads((out/'generation-validation-v1.json').read_text())
            self.assertEqual(report['attempts_per_version'],1)
            self.assertFalse(report['failures'][0]['retry'])
            with self.assertRaises(FileExistsError): formalizer.generate('config',out,'encode')

    def test_save_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'evidence.json'
            formalizer.save(path, {'original':True})
            with self.assertRaises(FileExistsError): formalizer.save(path, {'original':False})
            self.assertTrue(json.loads(path.read_text())['original'])

    def test_pin_tamper_is_global_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'config.json'
            pin = Path(tmp)/'pin'
            pin.write_text('original')
            config.write_text(json.dumps(dict(formalizer_version=formalizer.VERSION,pins={str(pin):formalizer.hash_file(pin)})))
            pin.write_text('changed')
            with self.assertRaisesRegex(ValueError,'prerequisite hash differs'): formalizer.verify(config)


if __name__ == '__main__': unittest.main()
