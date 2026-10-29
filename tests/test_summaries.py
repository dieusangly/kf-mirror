"""Offline end-to-end summary creation, recursion and provenance validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from knowledge_foundry.claim_context import ROOT, load_preparation, _file_hash
from claim_fixtures import prepare_claim_context
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash
from knowledge_foundry.summaries import generate_summaries, load_summaries, _summary_id
from scripts.render_knowledge_graph import build_graph, main as graph_main
from scripts.generate_summaries import main as summary_main
from claim_fixtures import fixture_settings
import claim_fixtures as context_fixtures


class SummariesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        chunked = self.root / 'chunked'
        chunked.mkdir()
        self.bundle = context_fixtures.bundle([
            ('paragraph', 'Banking requirements.', 1), ('paragraph', 'Insurance requirements.', 2),
            ('paragraph', 'Reporting requirements.', 3)], [[0], [1], [2]])
        self.original = json.dumps(self.bundle).encode()
        (chunked / 'artifact-bundle.json').write_bytes(self.original)
        config = fixture_settings()
        config['steps']['embeddings'].update(dimensions=3, expected_dimensions=3)
        config_path = self.root / 'preparation-config.json'
        config_path.write_text(json.dumps(config))
        records, _ = assemble_structural_chunks(self.bundle)
        vectors = {stable_hash(r['text']): [int(i == j) for j in range(3)] for i, r in enumerate(records)}
        fixture_path = self.root / 'prepare-fixture.json'
        fixture_path.write_text(json.dumps({'embedding_vectors': vectors}))
        self.context = self.root / 'context'
        prepare_claim_context(chunked, self.context, config_path, self.root / 'preparation-cache', fixture_path)
        self.prepared = load_preparation(self.context)
        self.config = json.loads((ROOT / 'config/summaries.json').read_text())
        self.config['steps']['summary_generation']['provider'] = 'fixture'
        self.config['steps']['summary_generation']['retries'] = 0
        self.config['steps']['embeddings'].update(provider='fixture', dimensions=2, expected_dimensions=2, retries=0)
        self.config['pricing']['providers']['fixture'] = copy.deepcopy(self.config['pricing']['providers']['openai'])
        self.config['steps']['summary_clusters'].update(max_levels=1, max_members=2)
        self.config_path = self.root / 'summary-config.json'
        self.fixture = {'summaries': {c['id']: {'text': f'Source cluster summary {i}.'}
                                     for i, c in enumerate(self.prepared['clusters'])}, 'embedding_vectors': {}}
        self.fixture_path = self.root / 'summary-fixture.json'

    def generate(self, name):
        self.config_path.write_text(json.dumps(self.config))
        self.fixture_path.write_text(json.dumps(self.fixture))
        directory = self.root / name
        generate_summaries(self.context, directory, self.config_path, self.root / 'summary-cache', self.fixture_path)
        return directory, load_summaries(directory)

    def recursive_run(self):
        _, first = self.generate('first')
        leaves = sorted(first['summaries'], key=lambda r: r['id'])
        self.fixture['embedding_vectors'].update({stable_hash(r['text']): [1., 0.] for r in leaves})
        ids = [r['id'] for r in leaves[:2]]
        cluster_id = 'summary-cluster:' + stable_hash(['recursive-summaries-v1', 2, ids])
        self.fixture['summaries'][cluster_id] = {'text': 'A synthesis of the first two summaries.'}
        self.config['steps']['summary_clusters']['max_levels'] = 2
        _, second = self.generate('second')
        frontier = second['report']['root_summary_ids']
        for r in second['summaries']:
            self.fixture['embedding_vectors'][stable_hash(r['text'])] = [1., 0.]
        cluster_id = 'summary-cluster:' + stable_hash(['recursive-summaries-v1', 3, sorted(frontier)])
        self.fixture['summaries'][cluster_id] = {'text': 'A synthesis of all source clusters.'}
        self.config['steps']['summary_clusters']['max_levels'] = 4
        return self.generate('recursive')

    def test_one_summary_per_cluster_without_claims_and_immutable_originals(self):
        directory, loaded = self.generate('summaries')
        self.assertEqual(len(loaded['summaries']), 3)
        self.assertEqual((directory / 'preparation/artifact-bundle.json').read_bytes(), self.original)
        self.assertEqual(loaded['preparation']['bundle']['claims'], [])
        self.assertEqual({r['input_cluster_id'] for r in loaded['summaries']},
                         {r['id'] for r in self.prepared['clusters']})
        for r in loaded['summaries']:
            cluster = next(c for c in self.prepared['clusters'] if c['id'] == r['input_cluster_id'])
            self.assertEqual(r['original_chunk_ids'], sorted(cluster['original_chunk_ids']))
            self.assertTrue(r['evidence'])
            self.assertEqual(r['input_summary_ids'], [])
        _, repeated = self.generate('repeated')
        self.assertEqual(loaded['summaries'], repeated['summaries'])
        self.assertEqual(repeated['report']['generation']['cache_hits'], 3)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            generate_summaries(self.context, directory, self.config_path)

    def test_generation_reuses_verified_preparation_during_final_validation(self):
        from knowledge_foundry.semantic_clusters import similarity_matrix
        self.config_path.write_text(json.dumps(self.config))
        self.fixture_path.write_text(json.dumps(self.fixture))
        events = []
        with patch('knowledge_foundry.claim_context.similarity_matrix', wraps=similarity_matrix) as scores:
            generate_summaries(self.context, self.root / 'reused-preparation', self.config_path,
                               self.root / 'summary-cache', self.fixture_path, on_progress=events.append)
        self.assertEqual(scores.call_count, 1)
        self.assertEqual(events[0]['phase'], 'preparation')
        self.assertTrue(any(event.get('step') == 'reusing verified preparation' for event in events))

    def test_recursive_summaries_preserve_transitive_sources_and_carry_singletons(self):
        directory, loaded = self.recursive_run()
        self.assertEqual(loaded['report']['levels'], {'1': 3, '2': 1, '3': 1})
        self.assertEqual(loaded['report']['stop_reason'], 'single_root')
        self.assertEqual(len(loaded['report']['root_summary_ids']), 1)
        root = next(r for r in loaded['summaries'] if r['level'] == 3)
        self.assertEqual(root['original_chunk_ids'], sorted(r['id'] for r in self.bundle['chunks']))
        self.assertEqual(root['semantic_cluster_ids'], sorted(c['id'] for c in self.prepared['clusters']))
        self.assertEqual(len(root['evidence']), 3)
        # The lone unmerged level-one summary is consumed at level three,
        # without inventing a level-two wrapper or dropping its lineage.
        self.assertEqual(sorted(next(r for r in loaded['summaries'] if r['id'] == sid)['level']
                                for sid in root['input_summary_ids']), [1, 2])
        with patch('knowledge_foundry.summaries.OpenAIResponsesTool', side_effect=AssertionError('offline')):
            load_summaries(directory)

    def test_report_records_model_and_measured_stage_duration_without_changing_summary_ids(self):
        with patch('knowledge_foundry.summaries.time.monotonic', side_effect=[100., 112.3456]):
            directory, loaded = self.generate('timed')
        report = loaded['report']
        self.assertEqual(report['generation']['model'], self.config['steps']['summary_generation']['model'])
        self.assertEqual(report['generation']['provider'], 'fixture')
        self.assertEqual(report['duration_seconds'], 12.346)
        _, repeated = self.generate('timed-cache-reuse')
        self.assertEqual(loaded['summaries'], repeated['summaries'])

        def save_report(value):
            (directory / 'report.json').write_text(json.dumps(value))
            manifest_path = directory / 'build-manifest.json'
            manifest = json.loads(manifest_path.read_text())
            next(r for r in manifest['outputs'] if r['path'] == 'report.json')['sha256'] = _file_hash(directory / 'report.json')
            manifest_path.write_text(json.dumps(manifest))

        bad = copy.deepcopy(report)
        bad['generation']['model'] = 'different-model'
        save_report(bad)
        with self.assertRaisesRegex(ValueError, 'generation identity'):
            load_summaries(directory)
        for invalid in (-1, True, float('inf'), '12.346'):
            bad = copy.deepcopy(report)
            bad['duration_seconds'] = invalid
            save_report(bad)
            with self.assertRaisesRegex(ValueError, 'duration_seconds'):
                load_summaries(directory)
        legacy = copy.deepcopy(report)
        legacy['generation'].pop('model')
        legacy['generation'].pop('provider')
        legacy.pop('duration_seconds')
        save_report(legacy)
        load_summaries(directory)
        legacy['duration_seconds'] = None
        save_report(legacy)
        load_summaries(directory)

    def test_no_similarity_progress_stops_without_singleton_wrapping(self):
        _, first = self.generate('first')
        self.config['steps']['summary_clusters']['max_levels'] = 4
        for i, r in enumerate(first['summaries']):
            self.fixture['embedding_vectors'][stable_hash(r['text'])] = [[1., 0.], [0., 1.], [-1., 0.]][i]
        _, loaded = self.generate('no-merges')
        self.assertEqual(loaded['report']['stop_reason'], 'no_eligible_merges')
        self.assertEqual(len(loaded['summaries']), 3)
        self.assertEqual(loaded['summary_clusters'], [])

    def test_budget_failure_is_explicit_and_does_not_change_preparation(self):
        self.config['steps']['summary_generation']['budget']['operational_input_cap_tokens'] = 1
        self.config_path.write_text(json.dumps(self.config))
        self.fixture_path.write_text(json.dumps(self.fixture))
        with self.assertRaisesRegex(ValueError, 'summary input cluster.*limit 1'):
            generate_summaries(self.context, self.root / 'failed', self.config_path,
                               self.root / 'summary-cache', self.fixture_path)
        self.assertEqual((self.context / 'artifact-bundle.json').read_bytes(), self.original)

    def test_rehashed_forged_source_provenance_is_rejected(self):
        directory, loaded = self.generate('summaries')
        records = loaded['summaries']
        records[0]['evidence'] = records[1]['evidence']
        records[0]['id'] = _summary_id(records[0])
        path = directory / 'summaries.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        manifest_path = directory / 'build-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        next(r for r in manifest['outputs'] if r['path'] == path.name)['sha256'] = _file_hash(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'source provenance'):
            load_summaries(directory)

    def test_graph_summary_provenance_and_cli_do_not_require_candidates(self):
        directory, loaded = self.recursive_run()
        graph = build_graph(self.bundle, summary_context=loaded)
        root = next(r for r in loaded['summaries'] if r['level'] == 3)
        edges = {(r['source'], r['target'], r['relation']) for r in graph['edges']}
        self.assertIn((root['id'], root['input_cluster_id'], 'summarizes'), edges)
        for cluster in loaded['summary_clusters']:
            for sid in cluster['summary_ids']:
                self.assertIn((cluster['id'], sid, 'groups'), edges)
        self.assertTrue(any(n['group'] == 'summary' for n in graph['nodes']))
        self.assertFalse(any(n['group'] == 'claim' for n in graph['nodes']))
        self.assertEqual(graph_main(['--bundle', str(self.context / 'artifact-bundle.json'),
                                    '--summary-dir', str(directory), '--output', str(self.root / 'graph.html')]), 0)
        # A real script process must find both src and repository validators,
        # without relying on the test runner's import path or working directory.
        cli = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/render_knowledge_graph.py'),
                              '--bundle', str(self.context / 'artifact-bundle.json'),
                              '--summary-dir', str(directory), '--output', str(self.root / 'cli-graph.html')],
                             cwd=self.root, capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertTrue((self.root / 'cli-graph.html').is_file())
        altered = copy.deepcopy(self.bundle)
        altered['chunks'][0]['text'] = 'Wrong original'
        with self.assertRaisesRegex(ValueError, 'originals mismatch'):
            build_graph(altered, summary_context=loaded)

        # Both branches share the exact same preparation, while retaining claim
        # views, candidate classification links and summary provenance separately.
        with_claim = copy.deepcopy(self.bundle)
        chunk = self.bundle['chunks'][0]
        candidate = {'id': 'candidate:test', 'text': 'A supported atomic claim.',
                     'chunk_ids': [chunk['id']], 'evidence': chunk['evidence']}
        claim = {**candidate, 'id': 'claim:test', 'topic_id': 'topic:test'}
        with_claim['claims'] = [claim]
        semantic = next(c for c in self.prepared['clusters'] if chunk['id'] in c['original_chunk_ids'])
        generation = {'structural_chunks': self.prepared['structural_chunks'],
                      'semantic_clusters': self.prepared['clusters'], 'hierarchy': self.prepared['hierarchy'],
                      'candidates': [candidate], 'candidate_clusters': [
                          {'candidate_id': candidate['id'], 'semantic_cluster_id': semantic['id']}]}
        combined = build_graph(with_claim, claim_generation=generation, summary_context=loaded)
        edges = {(r['source'], r['target'], r['relation']) for r in combined['edges']}
        self.assertIn((candidate['id'], claim['id'], 'classified as'), edges)
        self.assertIn((claim['id'], semantic['id'], 'generated from'), edges)
        self.assertIn((root['id'], root['input_cluster_id'], 'summarizes'), edges)

    def test_cli_requires_explicit_input_and_output(self):
        self.config_path.write_text(json.dumps(self.config))
        self.fixture_path.write_text(json.dumps(self.fixture))
        self.assertEqual(summary_main(['--context-dir', str(self.context), '--output-dir', str(self.root / 'cli'),
                                     '--config', str(self.config_path), '--fixture', str(self.fixture_path),
                                     '--cache-dir', str(self.root / 'cli-cache')]), 0)
