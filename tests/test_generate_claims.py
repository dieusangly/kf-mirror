import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts.generate_claims import _print_progress, main


class GenerateClaimsCliTests(unittest.TestCase):
    def test_unknown_cost_does_not_fail_after_generation(self):
        def generate(*args, **kwargs):
            kwargs['on_progress']({'phase': 'complete', 'state': 'complete',
                                   'telemetry': {'requests': 1, 'estimated_cost_usd': None}})
            return {'status': 'complete', 'projected_full_generation': {'estimated_cost_usd': None},
                    'compilation_scope': {'selected_cluster_numbers': [1], 'total_semantic_clusters': 2}}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('scripts.generate_claims.generate_claims', side_effect=generate), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(['--context-dir', 'prepared', '--output-dir', 'generated']), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'complete')
        self.assertEqual(stderr.getvalue().count('unknown'), 2)

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
            self.assertEqual(main(["--context-dir", "prepared", "--output-dir", "generated",
                                   "--subset-batches", "1"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertIn("Subset: 4/4 chunks; generating candidate claims in 1/1 semantic clusters", stderr.getvalue())
        self.assertIn("Generation: 1/1", stderr.getvalue())
        self.assertIn("Writing validated candidate claim artifacts", stderr.getvalue())
        self.assertIn("OpenAI estimated cost: $0.012345 (2 API requests)", stderr.getvalue())
        self.assertEqual(captured["subset_clusters"], 1)
        self.assertFalse(captured["resume"])

    def test_full_generation_progress_includes_chunk_total(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            _print_progress({"phase": "generation", "state": "started", "chunks": 806,
                             "source_chunks": 806, "total": 202, "source_total": 202, "subset": False})
        self.assertIn("Full: 806/806 chunks; generating candidate claims in 202/202 semantic clusters", stderr.getvalue())

    def test_cli_passes_explicit_batches_and_rejects_conflicting_subset_options(self):
        captured = {}

        def generate_claims(*_args, **kwargs):
            captured.update(kwargs)
            kwargs["on_progress"]({"phase": "complete", "state": "complete", "telemetry": {
                "requests": 0, "estimated_cost_usd": 0}})
            return {"status": "complete"}

        with patch("scripts.generate_claims.generate_claims", side_effect=generate_claims), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--context-dir", "prepared", "--output-dir", "generated",
                                   "--batch-numbers", "14", "202"]), 0)
        self.assertEqual(captured["cluster_numbers"], [14, 202])
        self.assertIsNone(captured["subset_clusters"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--context-dir", "prepared", "--output-dir", "generated", "--batch-numbers", "1",
                  "--subset-batches", "1"])
        self.assertEqual(error.exception.code, 2)

    def test_cli_reports_coverage_separately_from_processing_completion(self):
        for status, missing, label in [("needs_review", ["src:cmf-L561-6"], "Coverage needs review"),
                                       ("no_gaps_detected", [], "No coverage gaps detected")]:
            with self.subTest(status=status):
                report = {"status": "complete", "coverage": {
                    "status": status, "selected_source_ids_without_candidates": missing,
                    "summary": {"cited_chunks": 1, "selected_chunks": 2 if missing else 1,
                                "sources_with_candidates": 1, "selected_sources": 2 if missing else 1,
                                "failed_chunks": 0, "chunks_without_eligible_evidence": 0}}}

                def generate_claims(*_args, **kwargs):
                    kwargs["on_progress"]({"phase": "complete", "state": "complete", "telemetry": {
                        "requests": 1, "estimated_cost_usd": 0.01}})
                    return report

                stdout, stderr = io.StringIO(), io.StringIO()
                with patch("scripts.generate_claims.generate_claims", side_effect=generate_claims), \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(main(["--context-dir", "prepared", "--output-dir", "generated"]), 0)
                self.assertEqual(json.loads(stdout.getvalue()), report)
                self.assertIn("Complete in", stderr.getvalue())
                self.assertIn(label, stderr.getvalue())
                self.assertIn("(1 API requests)", stderr.getvalue())
                self.assertIn("verified grounding", stderr.getvalue())
                if missing:
                    self.assertIn("Selected source without candidate claims: src:cmf-L561-6", stderr.getvalue())
                else:
                    self.assertNotIn("Selected source without candidate claims", stderr.getvalue())

