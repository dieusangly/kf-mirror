import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
from scripts.build_semantic_clusters import main


class StructuralChunksAndSemanticClustersCliTests(unittest.TestCase):
    def test_cli_passes_explicit_inputs_and_reports_failure(self):
        report = {'status': 'complete', 'structural_chunks': 3, 'semantic_clusters': 2}
        out = io.StringIO()
        with patch('scripts.build_semantic_clusters.prepare_claim_context', return_value=report) as prepare, redirect_stdout(out):
            self.assertEqual(main(['--chunked-dir', 'chunks', '--output-dir', 'fresh', '--cache-dir', 'cache']), 0)
        self.assertEqual(json.loads(out.getvalue()), report)
        self.assertEqual(str(prepare.call_args.args[0]), 'chunks')
        self.assertEqual(prepare.call_args.kwargs['workers'], 4)
        stderr = io.StringIO()
        with patch('scripts.build_semantic_clusters.prepare_claim_context', side_effect=ValueError('oversized structural chunk')), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main(['--chunked-dir', 'chunks', '--output-dir', 'fresh'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('structural chunks and semantic clusters build failed: oversized structural chunk', stderr.getvalue())
        self.assertNotIn('claim context preparation', stderr.getvalue())

    def test_cli_forwards_workers_and_rejects_invalid_count_before_loading(self):
        with patch('scripts.build_semantic_clusters.prepare_claim_context', return_value={}) as prepare, redirect_stdout(io.StringIO()):
            main(['--chunked-dir', 'chunks', '--output-dir', 'fresh', '--workers', '8'])
        self.assertEqual(prepare.call_args.kwargs['workers'], 8)
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main(['--chunked-dir', 'missing', '--output-dir', 'fresh', '--workers', '0'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('workers must be a positive integer', stderr.getvalue())
