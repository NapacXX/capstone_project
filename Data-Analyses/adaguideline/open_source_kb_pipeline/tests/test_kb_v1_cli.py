import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
import kb_v1


class CommandTests(unittest.TestCase):
    def test_four_core_commands_exist(self):
        for args in (["doctor"], ["verify", "--bundle", "x"], ["build", "--kb-dir", "x", "--pdf", "x", "--model", "x", "--output", "x"], ["query", "--bundle", "x", "--cases", "x", "--output", "x"]):
            self.assertTrue(kb_v1.parser().parse_args(args).command)

    def test_doctor_no_model_load(self):
        with patch("kb_v1.importlib.metadata.version", return_value="test"):
            result = kb_v1.doctor()
        self.assertFalse(result["generation_api"])
        self.assertFalse(result["network_required_for_query"])
        self.assertEqual(result["device"], "cpu")

    def test_missing_package_inventory(self):
        with patch("kb_v1.importlib.metadata.version", side_effect=kb_v1.importlib.metadata.PackageNotFoundError):
            self.assertEqual(len(kb_v1.doctor()["missing_packages"]), 4)

    def test_single_case_uses_full_file_and_text_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "\u75c5\u4f8b input.txt"
            p.write_text("Full case with Unicode \u4e2d\u6587 and line\nbreak", encoding="utf-8")
            captured = {}
            def retrieve(bundle, source, output, mode, topk, allow):
                import csv
                with Path(source).open() as h:
                    row = next(csv.DictReader(h))
                captured.update(row)
                return {"status":"SUCCESS"}
            with patch("kb_v1_runtime.retrieve_cases", side_effect=retrieve), contextlib.redirect_stdout(io.StringIO()):
                code = kb_v1.main(["query","--bundle",folder,"--vignette-file",str(p),"--case-id","case-A","--output",str(Path(folder)/"out")])
            self.assertEqual(code, 0)
            self.assertEqual(captured, {"case_id":"case-A","vignette_text":p.read_text()})

    def test_candidate_is_never_default(self):
        a = kb_v1.parser().parse_args(["query","--bundle","x","--cases","x","--output","x"])
        self.assertFalse(a.allow_candidate)

    def test_errors_are_nonzero(self):
        with patch("kb_v1_runtime.verify_bundle", side_effect=ValueError("blocked")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(kb_v1.main(["verify","--bundle","x"]), 2)


if __name__ == '__main__':
    unittest.main()
