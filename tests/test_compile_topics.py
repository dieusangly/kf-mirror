import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts.compile_topics import main


class CompileTopicsCliTests(unittest.TestCase):
    def test_cli_prints_coarse_progress_and_summary_to_stderr(self):
        report = {"status": "complete", "claims": 1}
        captured = {}

        def compile_bundle(*_args, **kwargs):
            captured.update(kwargs)
            progress = kwargs["on_progress"]
            progress({"phase": "generation", "state": "started", "chunks": 4, "source_chunks": 4,
                      "total": 1, "source_total": 1, "sample": False})
            progress({"phase": "generation", "state": "progress", "completed": 1, "total": 1})
            progress({"phase": "generation", "state": "complete", "candidates": 1})
            progress({"phase": "export", "state": "started"})
            progress({"phase": "complete", "state": "complete", "telemetry": {
                "provider": "openai", "requests": 2, "estimated_cost_usd": 0.012345}})
            return report

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("scripts.compile_topics.compile_bundle", side_effect=compile_bundle), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["--chunked-dir", "chunked", "--output-dir", "compiled"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertIn("Input: generating claims in 1/1 batches", stderr.getvalue())
        self.assertIn("Generation: 1/1", stderr.getvalue())
        self.assertIn("Generated 1 candidates", stderr.getvalue())
        self.assertIn("Writing validated artifacts", stderr.getvalue())
        self.assertIn("OpenAI estimated cost: $0.012345 (2 API requests)", stderr.getvalue())
        self.assertIsNone(captured["sample_batches"])

