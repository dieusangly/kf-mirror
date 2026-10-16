import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
from scripts.build_structural_chunks_and_semantic_clusters import main


class StructuralChunksAndSemanticClustersCliTests(unittest.TestCase):
    def test_cli_passes_explicit_inputs_and_reports_failure(self):
        report = {'status': 'complete', 'structural_chunks': 3, 'semantic_clusters': 2}
        out = io.StringIO()
        with patch('scripts.build_structural_chunks_and_semantic_clusters.prepare_claim_context', return_value=report) as prepare, redirect_stdout(out):
            self.assertEqual(main(['--chunked-dir', 'chunks', '--output-dir', 'fresh', '--cache-dir', 'cache']), 0)
        self.assertEqual(json.loads(out.getvalue()), report)
        self.assertEqual(str(prepare.call_args.args[0]), 'chunks')
        stderr = io.StringIO()
        with patch('scripts.build_structural_chunks_and_semantic_clusters.prepare_claim_context', side_effect=ValueError('oversized structural chunk')), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main(['--chunked-dir', 'chunks', '--output-dir', 'fresh'])
        self.assertEqual(error.exception.code, 2)
        self.assertIn('structural chunks and semantic clusters build failed: oversized structural chunk', stderr.getvalue())
        self.assertNotIn('claim context preparation', stderr.getvalue())
