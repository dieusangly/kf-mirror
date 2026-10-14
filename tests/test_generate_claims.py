import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts.generate_claims import _print_progress, main


class GenerateClaimsCliTests(unittest.TestCase):
    def test_cli_prints_coarse_progress_and_summary_to_stderr(self):
        report = {"status": "complete", "candidates": 1}
        captured = {}

        def generate_claims(*_args, **kwargs):
            captured.update(kwargs)
            progress = kwargs["on_progress"]
            progress({"phase": "generation", "state": "started", "chunks": 4, "source_chunks": 4,
                      "total": 1, "source_total": 1, "subset": True})
            progress({"phase": "generation", "state": "progress", "completed": 1, "total": 1})
            progress({"phase": "export", "state": "started"})
            progress({"phase": "complete", "state": "complete", "telemetry": {
                "provider": "openai", "requests": 2, "estimated_cost_usd": 0.012345}})
            return report

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("scripts.generate_claims.generate_claims", side_effect=generate_claims), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["--chunked-dir", "chunked", "--output-dir", "generated",
                                   "--subset-batches", "1"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertIn("Subset: 4/4 chunks; generating candidates in 1/1 batches", stderr.getvalue())
        self.assertIn("Generation: 1/1", stderr.getvalue())
        self.assertIn("Writing validated candidate artifacts", stderr.getvalue())
        self.assertIn("OpenAI estimated cost: $0.012345 (2 API requests)", stderr.getvalue())
        self.assertEqual(captured["subset_batches"], 1)
        self.assertFalse(captured["resume"])

    def test_full_generation_progress_includes_chunk_total(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            _print_progress({"phase": "generation", "state": "started", "chunks": 806,
                             "source_chunks": 806, "total": 202, "source_total": 202, "subset": False})
        self.assertIn("Full: 806/806 chunks; generating candidates in 202/202 batches", stderr.getvalue())

