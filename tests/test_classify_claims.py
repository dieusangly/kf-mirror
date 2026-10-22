import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts.classify_claims import main


class ClassifyClaimsCliTests(unittest.TestCase):
    def test_unknown_cost_does_not_fail_after_classification(self):
        def classify(*args, **kwargs):
            kwargs['on_progress']({'phase': 'complete', 'state': 'complete',
                                   'telemetry': {'requests': 1, 'estimated_cost_usd': None}})
            return {'status': 'complete', 'projected_full_build': {'estimated_cost_usd': None}}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('scripts.classify_claims.classify_claims', side_effect=classify), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(['--candidates-dir', 'candidates', '--output-dir', 'classified']), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'complete')
        self.assertEqual(stderr.getvalue().count('unknown'), 2)

    def test_cli_prints_classification_progress_and_summary(self):
        report = {"status": "complete", "claims": 1}

        def classify_claims(*_args, **kwargs):
            progress = kwargs["on_progress"]
            progress({"phase": "classification", "state": "started", "total": 1})
            progress({"phase": "classification", "state": "progress", "completed": 1, "total": 1})
            progress({"phase": "export", "state": "started"})
            progress({"phase": "complete", "state": "complete", "telemetry": {
                "provider": "openai", "requests": 1, "estimated_cost_usd": 0.001}})
            return report

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("scripts.classify_claims.classify_claims", side_effect=classify_claims), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["--candidates-dir", "candidates", "--output-dir", "compiled"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertIn("Classifying 1 candidate claims", stderr.getvalue())
        self.assertIn("Classification: 1/1", stderr.getvalue())
        self.assertIn("Writing validated topic artifacts", stderr.getvalue())
        self.assertIn("OpenAI estimated cost: $0.001000 (1 API requests)", stderr.getvalue())
