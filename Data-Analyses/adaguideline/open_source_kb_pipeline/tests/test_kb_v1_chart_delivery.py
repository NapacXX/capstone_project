"""Synthetic delivery integrity tests; no clinical or model accuracy claims."""
import json
import copy
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
import kb_v1_chart_delivery as delivery
import kb_v1_chart_acceptance as chart_acceptance


class ChartDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bundle = self.root / "bundle"
        self.output = self.root / "delivery"
        self.bundle.mkdir()
        self.tool_root = self.root / "pipeline"
        self.tool_root.mkdir()
        for name in delivery.TOOL_FILES:
            (self.tool_root / name).write_text("# synthetic fixture, not a working consumer\n", encoding="utf-8")
        (self.tool_root / "licenses").mkdir()
        for name in ("MiniLM-APACHE-2.0.txt", "MODEL_AND_SOURCE_NOTICE.md"):
            (self.tool_root / "licenses" / name).write_text("Synthetic license fixture\n", encoding="utf-8")
        for name, data in (("records.jsonl", '{"record_id":"synthetic"}\n'),
                           ("model/config.json", '{"synthetic":true}'),
                           ("sources/source.pdf", "Synthetic bytes; not a real PDF"),
                           ("audit/whole_charts/release_contract.json", '{"synthetic":true}')):
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
        self.manifest = {"status": "released", "release_mode": "whole_chart",
                         "kb_version": "synthetic-v1", "visual_record_count": 1,
                         "files": delivery._tree_hashes(self.bundle)}
        self.dump(self.bundle / "manifest.json", self.manifest)
        tool_patch = patch.object(delivery, "PIPE", self.tool_root)
        tool_patch.start()
        self.addCleanup(tool_patch.stop)
        verifier = patch("kb_v1_runtime.verify_bundle", side_effect=lambda root: delivery._json(Path(root) / "manifest.json"))
        self.verifier = verifier.start()
        self.addCleanup(verifier.stop)

    def dump(self, path, obj):
        path.write_text(json.dumps(obj) + "\n", encoding="utf-8")

    def package(self, **kwargs):
        return delivery.package_release(self.bundle, self.output, **kwargs)

    def make_acceptance(self):
        folder = self.root / "acceptance"
        folder.mkdir()
        report = {"status": "SUCCESS", "kb_version": self.manifest["kb_version"],
                  "bundle_manifest_sha256": delivery.sha(self.bundle / "manifest.json"),
                  "platform_results": {
                      "macOS": {"status": "SUCCESS", "platform": "macOS-synthetic-fixture"},
                      "Windows": {"status": "NOT_RUN", "reason": "No Windows test in fixture"},
                  }}
        self.dump(folder / "acceptance_results.json", report)
        (folder / "unit_tests.stdout.txt").write_text("Synthetic test output\n")
        (folder / "sample_cases.csv").write_text("Private input must never be copied\n")
        (folder / "portable-copy").mkdir()
        (folder / "portable-copy/large-model.bin").write_bytes(b"Do not copy this duplicate")
        return folder, report

    def test_release_delivery_complete_and_synthetic_only(self):
        result = self.package()
        self.assertEqual(result["status"], "controlled_research_release")
        self.assertEqual(result["released_visual_records"], 1)
        self.assertEqual(result["acceptance"]["platform_results"]["Windows"]["status"], "NOT_RUN")
        self.assertFalse((self.output / ".incomplete").exists())
        verified = delivery.verify_delivery(self.output)
        self.assertEqual(verified["kb_version"], "synthetic-v1")
        self.assertIn("Synthetic software test, not a real patient", (self.output / "examples/synthetic_case.txt").read_text())
        self.assertTrue((self.output / "tools/kb_v1_chart_release.py").is_file())
        self.assertTrue((self.output / "tools/kb_v1_chart_acceptance.py").is_file())
        self.assertTrue((self.output / "tools/kb_v1_chart_text.py").is_file())
        self.assertTrue(result["chart_locator_selftest"])
        self.assertTrue((self.output / "bundle/audit/whole_charts/release_contract.json").is_file())
        self.assertNotIn("sample_cases.csv", "\n".join(delivery._tree_hashes(self.output)))
        self.assertIn("only_this_host_tested", (self.output / "selftest.py").read_text())
        compile((self.output / "selftest.py").read_text(), "selftest.py", "exec")

    def test_generated_instructions_do_not_frame_kb_by_language(self):
        self.package()
        instructions = (self.output / "START_HERE.md").read_text()
        self.assertNotIn("English", instructions)
        self.assertIn("Table 9.1", instructions)
        self.assertIn("Table 9.4", instructions)
        self.assertIn("four command logs", instructions)

    def test_new_selftest_declares_complete_chart_probes(self):
        self.package()
        script = (self.output / "selftest.py").read_text()
        self.assertIn("chart-probes", script)
        self.assertIn("complete_chart_parents_returned", script)
        self.assertIn("bundle_manifest_sha256", script)

    def test_old_delivery_without_new_selftest_tools_still_verifies(self):
        self.package()
        for name in ("kb_v1_chart_acceptance.py", "kb_v1_chart_text.py"):
            (self.output / "tools" / name).unlink()
        path = self.output / "delivery_manifest.json"
        manifest = delivery._json(path)
        manifest.pop("chart_locator_selftest")
        payload = delivery._tree_hashes(self.output)
        payload.pop("delivery_manifest.json")
        payload.pop("SHA256SUMS")
        (self.output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(payload.items())))
        payload["SHA256SUMS"] = delivery.sha(self.output / "SHA256SUMS")
        manifest["files"] = payload
        self.dump(path, manifest)
        self.assertEqual(delivery.verify_delivery(self.output)["kb_version"], "synthetic-v1")

    def test_whole_tree_materializes_and_relocates_with_unicode_spaces(self):
        import shutil
        self.package()
        relocated = self.root / "中文 folder" / "teammate copy"
        shutil.copytree(self.output, relocated)
        self.assertEqual(delivery.verify_delivery(relocated)["kb_version"], "synthetic-v1")
        self.assertFalse(any(path.is_symlink() for path in relocated.rglob("*")))

    def test_archive_contains_complete_directory_and_no_marker(self):
        result = self.package(make_zip=True)
        archive = self.output.with_suffix(".zip")
        self.assertEqual(result["zip_sha256"], delivery.sha(archive))
        with zipfile.ZipFile(archive) as handle:
            self.assertIsNone(handle.testzip())
            self.assertIn("delivery/delivery_manifest.json", handle.namelist())
            self.assertNotIn("delivery/.incomplete", handle.namelist())

    def test_candidate_and_legacy_and_zero_visuals_rejected(self):
        for field, value in (("status", "review_candidate"), ("release_mode", "legacy"), ("visual_record_count", 0)):
            with self.subTest(field=field):
                changed = dict(self.manifest, **{field: value})
                self.dump(self.bundle / "manifest.json", changed)
                with self.assertRaises(ValueError):
                    self.package()
                self.assertFalse(self.output.exists())
        self.dump(self.bundle / "manifest.json", self.manifest)

    def test_unlisted_bundle_file_rejected_before_output(self):
        (self.bundle / "extra.csv").write_text("not in manifest")
        with self.assertRaisesRegex(ValueError, "every input file"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_symlink_bundle_root_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            delivery.package_release(alias, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_bundle_member_rejected(self):
        (self.bundle / "model/alias.json").symlink_to(self.bundle / "model/config.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_case_insensitive_collision_rejected(self):
        # A case-insensitive host cannot create both names, so simulate the
        # directory enumeration that a case-sensitive source host can expose.
        paths = [self.bundle / "model/config.json", self.bundle / "model/CONFIG.json"]
        with patch.object(Path, "rglob", return_value=paths):
            with self.assertRaisesRegex(ValueError, "collision"):
                delivery._tree_hashes(self.bundle)

    def test_nonportable_windows_or_checksum_paths_rejected(self):
        for name in ("CON.txt", "model/aux", "path/end.", "path/end ", "path/a:b", "path/line\nfeed", "path/question?"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                delivery._portable_name(name)

    def test_existing_outputs_and_zip_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.package()
        self.output.rmdir()
        archive = self.output.with_suffix(".zip")
        archive.write_bytes(b"existing user archive")
        with self.assertRaises(FileExistsError):
            self.package(make_zip=True)
        self.assertEqual(archive.read_bytes(), b"existing user archive")
        self.assertFalse(self.output.exists())

    def test_overlapping_output_rejected_before_verification(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            delivery.package_release(self.bundle, self.bundle / "delivery")
        self.verifier.assert_not_called()

    def test_acceptance_input_cannot_contain_output(self):
        acceptance, _ = self.make_acceptance()
        with self.assertRaisesRegex(ValueError, "overlap"):
            delivery.package_release(self.bundle, acceptance / "delivery", acceptance)
        self.verifier.assert_not_called()

    def test_acceptance_is_hash_bound_and_excludes_cases_or_model_copies(self):
        acceptance, _ = self.make_acceptance()
        result = self.package(acceptance=acceptance)
        self.assertEqual(result["acceptance"]["status"], "SUCCESS")
        self.assertEqual(sorted(path.name for path in (self.output / "test_results").iterdir()), ["acceptance_results.json", "unit_tests.stdout.txt"])
        self.assertEqual(delivery.verify_delivery(self.output)["acceptance"]["platform_results"]["Windows"]["status"], "NOT_RUN")

    def test_acceptance_wrong_version_or_digest_rejected(self):
        acceptance, report = self.make_acceptance()
        for field in ("kb_version", "bundle_manifest_sha256"):
            with self.subTest(field=field):
                self.dump(acceptance / "acceptance_results.json", {**report, field: "different"})
                with self.assertRaisesRegex(ValueError, "exact KB version"):
                    self.package(acceptance=acceptance)
                self.assertFalse(self.output.exists())

    def test_windows_success_cannot_be_claimed_from_macos_string(self):
        acceptance, report = self.make_acceptance()
        report["platform_results"]["Windows"] = {"status": "SUCCESS", "platform": "macOS-15.0"}
        self.dump(acceptance / "acceptance_results.json", report)
        with self.assertRaisesRegex(ValueError, "actual platform"):
            self.package(acceptance=acceptance)

    def test_nested_chart_acceptance_must_bind_exact_bundle(self):
        acceptance, _ = self.make_acceptance()
        (acceptance / "chart_probes").mkdir()
        path = acceptance / "chart_probes/chart_probe_results.json"
        report = {"status": "SUCCESS", "kb_version": self.manifest["kb_version"],
                  "bundle_manifest_sha256": delivery.sha(self.bundle / "manifest.json"),
                  "queries": 1, "complete_parents_returned": 1}
        self.dump(path, {**report, "bundle_manifest_sha256": "wrong"})
        with self.assertRaisesRegex(ValueError, "Chart locator report"):
            self.package(acceptance=acceptance)
        self.dump(path, report)
        with self.assertRaisesRegex(ValueError, "complete synthetic query and evidence"):
            self.package(acceptance=acceptance)
        for name in delivery.CHART_ACCEPTANCE_FILES:
            if name.endswith("chart_probe_results.json"):
                continue
            fixture = acceptance / name
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text("synthetic fixture only\n")
        (acceptance / "chart_probes/private_cases.csv").write_text("Must not enter a delivery\n")
        self.package(acceptance=acceptance)
        for name in delivery.CHART_ACCEPTANCE_FILES:
            copied = self.output / "test_results" / name
            self.assertTrue(copied.is_file(), name)
            self.assertEqual(copied.read_bytes(), (acceptance / name).read_bytes())
        self.assertFalse((self.output / "test_results/chart_probes/private_cases.csv").exists())

    def test_ignored_acceptance_symlink_still_rejected(self):
        acceptance, _ = self.make_acceptance()
        (acceptance / "ignored-alias").symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.package(acceptance=acceptance)

    def test_missing_tool_prevents_partial_delivery(self):
        (self.tool_root / "kb_v1_chart_release.py").unlink()
        with self.assertRaisesRegex(ValueError, "consumer tool missing"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_changed_input_while_copying_leaves_incomplete_marker(self):
        original = delivery._copy_checked
        def mutate(source, target, expected):
            source.write_text("Changed after inventory")
            return original(source, target, expected)
        with patch.object(delivery, "_copy_checked", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed before copying"):
                self.package()
        self.assertTrue((self.output / ".incomplete").is_file())
        with self.assertRaisesRegex(ValueError, "incomplete"):
            delivery.verify_delivery(self.output)

    def test_changed_deleted_or_extra_delivery_files_rejected(self):
        self.package()
        target = self.output / "START_HERE.md"
        original = target.read_bytes()
        target.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "checksum inventory"):
            delivery.verify_delivery(self.output)
        target.write_bytes(original)
        target.unlink()
        with self.assertRaisesRegex(ValueError, "checksum inventory"):
            delivery.verify_delivery(self.output)
        target.write_bytes(original)
        (self.output / "extra.txt").write_text("not hash covered")
        with self.assertRaisesRegex(ValueError, "checksum inventory"):
            delivery.verify_delivery(self.output)

    def test_delivery_version_metadata_cannot_disagree_with_bundle(self):
        self.package()
        path = self.output / "delivery_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["kb_version"] = "not the bundle version"
        self.dump(path, manifest)
        with self.assertRaisesRegex(ValueError, "identities do not match"):
            delivery.verify_delivery(self.output)

    def test_selftest_rejects_output_inside_package_before_writing(self):
        self.package()
        child = subprocess.run([sys.executable, str(self.output / "selftest.py"), "--output", str(self.output / "run")], text=True, capture_output=True)
        self.assertEqual(child.returncode, 2)
        self.assertIn("must not overlap", child.stderr)
        self.assertFalse((self.output / "run").exists())


class ChartProbeTests(unittest.TestCase):
    def setUp(self):
        self.ids = [row[0] for row in (*chart_acceptance.PROBES, *chart_acceptance.SUPPLEMENT_PROBES)]
        self.manifest = {"visual_record_count": 9}
        self.contract = {"schema_version": chart_acceptance.CONTRACT_V2, "n_approved_charts": 9,
                         "records": [{"figure_id": identifier} for identifier in self.ids]}
        self.records = [{"figure_id": identifier, "content_type": "visual_whole_chart"} for identifier in self.ids]

    def test_exact_nine_chart_contract_selects_nine_probes(self):
        self.assertEqual(len(chart_acceptance.select_probes(self.contract, self.manifest, self.records)), 9)

    def test_exact_old_contract_retains_seven_probes(self):
        contract = {"schema_version": chart_acceptance.CONTRACT_V1, "n_approved_charts": 7,
                    "records": self.contract["records"][:7]}
        self.assertEqual(len(chart_acceptance.select_probes(contract, {"visual_record_count": 7}, self.records[:7])), 7)

    def test_nine_chart_count_without_new_contract_is_rejected(self):
        contract = {**self.contract, "schema_version": chart_acceptance.CONTRACT_V1}
        with self.assertRaisesRegex(ValueError, "exact approved"):
            chart_acceptance.select_probes(contract, self.manifest, self.records)

    def test_missing_duplicate_or_substituted_chart_is_rejected(self):
        for records in (self.records[:-1], [*self.records[:-1], self.records[0]],
                        [*self.records[:-1], {"figure_id": "unexpected", "content_type": "visual_whole_chart"}]):
            with self.subTest(records=records), self.assertRaisesRegex(ValueError, "exact approved"):
                chart_acceptance.select_probes(self.contract, self.manifest, records)

    def _table_hit(self, figure_id):
        is_91 = figure_id.endswith("9-1")
        pages = [3, 4] if is_91 else [22]
        questions = ([{"text": "90% not 100%; outside of activity time course, or URAA or RAA injections"}]
                     if is_91 else [{"text": "15 July 2025; 100/3.6 mg prefilled pen; 100/33 mg prefilled pen; 1,000 units versus monthly"}])
        parent = {"record_id": figure_id + "::fixture", "figure_id": figure_id,
                  "source_id": "source", "page_numbers": pages, "printed_pages": ["fixture"],
                  "source_text": "source and footnote", "source_blocks": [
                      {"id": "R01", "kind": "comparison", "text": "source"},
                      {"id": "F01", "kind": "footnote", "text": "footnote"}],
                  "open_questions": questions,
                  "use_policy": {"automatic_dose_calculation_allowed": False, "clinical_execution_allowed": False}}
        source = {"relative_path": "sources/source.pdf", "images": [
            {"figure_id": figure_id, "page_number": page, "relative_path": f"sources/{page}.png"} for page in pages]}
        hit = {**copy.deepcopy(parent), "source": copy.deepcopy(source), "source_images": copy.deepcopy(source["images"])}
        return parent, hit, source

    def test_both_tables_require_full_pages_footnotes_and_limitations(self):
        for identifier in self.ids[-2:]:
            with self.subTest(identifier=identifier):
                parent, hit, source = self._table_hit(identifier)
                result = chart_acceptance.inspect_complete_hit(parent, hit, source)
                self.assertTrue(result["complete_parent_returned"])
                self.assertEqual(result["footnote_ids_returned"], ["F01"])

    def test_missing_returned_page_footnote_or_limitation_fails(self):
        for key, value in (("page_numbers", [3]), ("source_images", []),
                           ("source_blocks", []), ("open_questions", []), ("use_policy", {})):
            with self.subTest(key=key):
                parent, hit, source = self._table_hit(self.ids[-2])
                hit[key] = value
                self.assertFalse(chart_acceptance.inspect_complete_hit(parent, hit, source)["complete_parent_returned"])

    def test_absent_hit_is_not_success(self):
        parent, _, source = self._table_hit(self.ids[-1])
        self.assertFalse(chart_acceptance.inspect_complete_hit(parent, None, source)["complete_parent_returned"])


if __name__ == "__main__":
    unittest.main()
