"""Whole-figure CLI is explicit and cannot masquerade as KB release."""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kb_v1


class FigureCommandTests(unittest.TestCase):
    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return kb_v1.main(args)

    def test_new_commands_are_explicit(self):
        for args in [
            ["figure-review", "--document", "d", "--scope", "s", "--output", "o"],
            ["figure-verify", "--review-dir", "r"],
            ["figure-import-decision", "--review-dir", "r", "--decision", "d", "--output", "o"],
            ["figure-scope-check", "--scope", "s", "--review-dirs", "r one", "r two", "--decisions", "d"],
        ]:
            self.assertEqual(kb_v1.parser().parse_args(args).command, args[0])

    def test_prepare_does_not_build_or_generate(self):
        with patch("kb_v1_figure_review.prepare_figure_review", return_value={"status": "VERIFIED_CANDIDATE"}) as prepare, patch("kb_v1_build.build_bundle") as build:
            self.assertEqual(self.invoke(["figure-review", "--document", "d", "--scope", "s", "--output", "o"]), 0)
            prepare.assert_called_once_with(Path("d"), Path("s"), Path("o"))
            build.assert_not_called()

    def test_missing_figures_return_nonzero(self):
        with patch("kb_v1_figure_review.check_figure_scope", return_value={"status": "BLOCKED", "ready": False}):
            self.assertEqual(self.invoke(["figure-scope-check", "--scope", "s"]), 2)

    def test_bad_figure_approval_is_error(self):
        with patch("kb_v1_figure_review.import_figure_decision", side_effect=ValueError("missing explicit approval")):
            self.assertEqual(self.invoke(["figure-import-decision", "--review-dir", "r", "--decision", "d", "--output", "o"]), 2)

    def test_existing_build_route_is_unchanged(self):
        with patch("kb_v1_build.build_bundle", return_value={"status": "SUCCESS"}) as build, patch("kb_v1_figure_review.check_figure_scope") as chart_gate:
            self.assertEqual(self.invoke(["build", "--kb-dir", "k", "--pdf", "p", "--model", "m", "--output", "o"]), 0)
            build.assert_called_once_with(Path("k"), Path("p"), Path("m"), Path("o"), None, False, ledger_path=None)
            chart_gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
