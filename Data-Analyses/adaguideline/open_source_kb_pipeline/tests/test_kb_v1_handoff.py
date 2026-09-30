import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
import kb_v1_delivery as delivery
import kb_v1_acceptance as acceptance
import kb_v1_review as review_helper
from kb_v1_runtime import STRUCTURED_FIELDS


class HandoffTests(unittest.TestCase):
    def test_nested_delivery_output_rejected_before_writes_or_validation(self):
        with tempfile.TemporaryDirectory() as folder, patch('kb_v1_runtime.verify_bundle') as verify:
            root = Path(folder)
            for name in ('bundle','review','notes'):
                (root/name).mkdir()
                with self.assertRaisesRegex(ValueError, 'overlap'):
                    delivery.package_candidate(root/'bundle', root/'review', root/'notes', root/name/'delivery')
                self.assertFalse((root/name/'delivery').exists())
            verify.assert_not_called()

    def test_delivery_output_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, 'overlap'):
                delivery.package_candidate(root/'bundle', root/'review', root/'notes', root)

    def test_acceptance_overlap_rejected_before_any_validation(self):
        with tempfile.TemporaryDirectory() as folder, patch('kb_v1_runtime.verify_bundle') as verify:
            root = Path(folder)
            for output, acceptance_dir in ((root/'acceptance/delivery', root/'acceptance'), (root/'delivery', root/'delivery/acceptance')):
                with self.assertRaisesRegex(ValueError, 'overlap'):
                    delivery.package_candidate(root/'bundle', root/'review', root/'notes', output, acceptance_dir=acceptance_dir)
                self.assertFalse(output.exists())
            verify.assert_not_called()

    def test_existing_zip_rejected_before_creating_delivery(self):
        with tempfile.TemporaryDirectory() as folder, patch('kb_v1_runtime.verify_bundle') as verify:
            root = Path(folder)
            archive = root/'delivery.zip'
            archive.write_bytes(b'previous user artifact')
            with self.assertRaises(FileExistsError):
                delivery.package_candidate(root/'bundle', root/'review', root/'notes', root/'delivery', make_zip=True)
            self.assertEqual(archive.read_bytes(), b'previous user artifact')
            self.assertFalse((root/'delivery').exists())
            verify.assert_not_called()

    def test_case_projection_ignores_generated_plan_and_records_blank_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'source.csv'
            selected = ['case_id','vignette_text',*[f for f,_ in STRUCTURED_FIELDS]]
            names = ['DM020','DM022','DM025','DM026(1)','DM030','DM040','DM050','DM060','DM080','DM100']
            with source.open('w',newline='') as h:
                writer=csv.writer(h); writer.writerow(selected+['treatment_plan','',''])
                for name in names:
                    r={k:'' for k in selected};r.update(case_id=name,vignette_text='Synthetic case only',hba1c_percent='8.0')
                    writer.writerow([r[k] for k in selected]+['NEVER_QUERY_THIS_PLAN','',''])
            result=acceptance.export_cases(source,root)
            self.assertEqual(result['legacy_empty_header_count'],2)
            self.assertEqual(result['distinct_cases'],10)
            self.assertNotIn('NEVER_QUERY_THIS_PLAN',(root/'sample_cases.csv').read_text())
            self.assertNotIn('treatment_plan',(root/'sample_cases_structured.csv').read_text())

    def test_case_projection_rejects_duplicate_clinical_header(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'bad.csv'
            source.write_text('case_id,vignette_text,vignette_text\nx,a,b\n')
            with self.assertRaisesRegex(ValueError,'Missing or repeated'):
                acceptance.export_cases(source,root)


class DeliveryIntegrityTests(unittest.TestCase):
    """Synthetic one-record packaging fixtures; no clinical approval/model run."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle, self.review, self.notes, self.output = [self.root/name for name in ('bundle', 'review', 'notes', 'delivery')]
        for path in (self.bundle, self.review, self.notes):
            path.mkdir()
        self.image = self.root/'tile.png'
        self.image.write_bytes(b'synthetic tile bytes')
        self.overview = self.root/'ada_page_001.png'
        self.overview.write_bytes(b'synthetic overview bytes')
        self.pdf = self.root/'source.pdf'
        self.pdf.write_bytes(b'synthetic PDF bytes, not clinical evidence')
        source_hash = delivery.sha(self.pdf)
        (self.bundle/'sources').mkdir()
        (self.bundle/'sources/source.pdf').write_bytes(self.pdf.read_bytes())
        self.dump(self.bundle/'sources.json', {source_hash: {'relative_path': 'sources/source.pdf'}})
        self.record = {
            'record_id': 'synthetic-1', '_record_type': 'action', 'page_number': 1,
            'source_pdf': str(self.pdf), 'source_pdf_sha256': source_hash,
            'image_path': str(self.image), 'image_sha256': delivery.sha(self.image),
            'source_json': 'raw.json', 'action': 'Synthetic conditional action.',
            'evidence_text': 'Synthetic source evidence.',
        }
        self.note = {
            'record_id': 'synthetic-1', 'ai_review_status': 'reviewed_with_findings',
            'checked_sources': [{'path': str(self.image), 'sha256': delivery.sha(self.image), 'method': 'Synthetic test source comparison'}],
            'findings': ['Synthetic fixture, no clinical assessment'], 'suggested_corrections': {}, 'uncertainty': 'Test only',
        }
        self.ledger = {**self.note, 'page_number': 1, 'final_disposition': 'pending', 'validation_status': 'valid', 'current_v1_content_sha256': review_helper.record_fingerprint(self.record)}
        self.groups = {'expected_record_ids': ['synthetic-1'], 'groups': [{'record_ids': ['synthetic-1'], 'group_status': 'pending', 'human_confirmed': False}]}
        self.lines(self.review/'canonical_records.jsonl', [self.record])
        self.lines(self.review/'review_ledger.jsonl', [self.ledger])
        self.dump(self.review/'group_reviews.json', self.groups)
        self.lines(self.notes/'all_notes.jsonl', [self.note])
        frozen = self.review/'frozen_inputs'
        (frozen/'ai_notes').mkdir(parents=True)
        self.lines(frozen/'ai_notes/notes.jsonl', [self.note])
        (frozen/'raw_json').mkdir()
        self.dump(frozen/'raw_json/raw.json', {'synthetic': True})
        inventory = [
            {'role': 'image', 'original_path': str(self.image), 'sha256': delivery.sha(self.image)},
            {'role': 'page_overview', 'original_path': str(self.overview), 'sha256': delivery.sha(self.overview)},
            {'role': 'source_pdf', 'original_path': str(self.pdf), 'sha256': source_hash},
            {'role': 'ai_notes', 'original_path': str(self.notes/'all_notes.jsonl'), 'sha256': delivery.sha(self.notes/'all_notes.jsonl'), 'frozen_path': 'frozen_inputs/ai_notes/notes.jsonl'},
            {'role': 'raw_json', 'original_path': str(self.root/'raw.json'), 'sha256': delivery.sha(frozen/'raw_json/raw.json'), 'frozen_path': 'frozen_inputs/raw_json/raw.json'},
        ]
        self.dump(self.review/'source_inventory.json', {'files': inventory})
        self.manifest = {'status': 'review_candidate', 'kb_version': 'synthetic-version'}
        for target, options in (
            ('kb_v1_runtime.verify_bundle', {'return_value': self.manifest}),
            ('kb_v1_review.verify_review_inputs', {'return_value': {'expected_record_ids': ['synthetic-1']}}),
            ('kb_v1_review.ORIGIN_RECORD_COUNT', {'new': 1}),
        ):
            mocked = patch(target, **options)
            handle = mocked.start()
            self.addCleanup(mocked.stop)
            if target.endswith('verify_review_inputs'):
                self.input_verification = handle

    def dump(self, path, value):
        path.write_text(json.dumps(value)+'\n')

    def lines(self, path, rows):
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def package(self, **kwargs):
        return delivery.package_candidate(self.bundle, self.review, self.notes, self.output, **kwargs)

    def test_package_verifies_frozen_inputs_and_copies_portable_tools_and_raw_links(self):
        result = self.package()
        self.input_verification.assert_called_once_with(self.review.resolve())
        self.assertEqual(result['status'], 'review_candidate_only')
        self.assertFalse((self.output/'.incomplete').exists())
        for name in ('kb_v1.py', 'kb_v1_runtime.py', 'kb_v1_review.py', '07_validate_visual_logic_outputs.py', '08_build_enhanced_guideline_kb.py', 'requirements-v1-consumer.lock.txt'):
            self.assertTrue((self.output/'tools'/name).is_file(), name)
        page = (self.output/'review_pages/page_001.html').read_text()
        self.assertIn('../review/frozen_inputs/raw_json/raw.json', page)
        self.assertNotIn("href='raw.json'", page)
        for command in ('verify', 'query'):
            process = subprocess.run([sys.executable, str(self.output/'tools/kb_v1.py'), command, '--help'], cwd=self.output, text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_frozen_verifier_failure_prevents_all_output(self):
        self.input_verification.side_effect = ValueError('frozen raw inventory changed')
        with self.assertRaisesRegex(ValueError, 'frozen raw'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_zero_candidate_inventory_tile_is_copied_hashed_and_linked(self):
        unused_tile = self.root/'ada_page_001_tile_r01_c02.png'
        unused_tile.write_bytes(b'synthetic tile with no extracted candidates')
        inventory_path = self.review/'source_inventory.json'
        inventory = json.loads(inventory_path.read_text())
        inventory['files'].append({'role': 'image', 'original_path': str(unused_tile), 'sha256': delivery.sha(unused_tile)})
        self.dump(inventory_path, inventory)
        result = self.package()
        self.assertEqual(result['source_tiles'], 2)
        self.assertEqual(result['zero_candidate_source_tiles'], 1)
        paths = json.loads((self.output/'source_path_map.json').read_text())
        delivered = self.output/paths[str(unused_tile)]
        self.assertEqual(delivered.read_bytes(), unused_tile.read_bytes())
        manifest = json.loads((self.output/'delivery_manifest.json').read_text())
        self.assertEqual(manifest['files'][paths[str(unused_tile)]], delivery.sha(unused_tile))
        page = (self.output/'review_pages/page_001.html').read_text()
        self.assertIn('All 2 original detail tiles', page)
        self.assertIn('../'+paths[str(unused_tile)], page)

    def test_duplicate_ledger_ids_rejected_even_when_id_set_matches(self):
        self.lines(self.review/'review_ledger.jsonl', [self.ledger, self.ledger])
        with self.assertRaisesRegex(ValueError, 'duplicate ledger'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_group_membership_cannot_omit_candidate(self):
        self.groups['groups'][0]['record_ids'] = []
        self.dump(self.review/'group_reviews.json', self.groups)
        with self.assertRaisesRegex(ValueError, 'group inventory'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_changed_ledger_findings_rejected_against_frozen_notes(self):
        self.ledger['findings'] = ['Changed after review freeze']
        self.lines(self.review/'review_ledger.jsonl', [self.ledger])
        with self.assertRaisesRegex(ValueError, 'Ledger AI fields differ'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_changed_delivery_notes_rejected_against_frozen_notes(self):
        self.note['findings'] = ['Changed after review freeze']
        self.lines(self.notes/'all_notes.jsonl', [self.note])
        with self.assertRaisesRegex(ValueError, 'Delivery AI note differs'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_missing_ai_coverage_rejected(self):
        self.lines(self.notes/'all_notes.jsonl', [])
        with self.assertRaisesRegex(ValueError, 'do not cover'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_human_signature_in_pending_handoff_rejected(self):
        self.ledger['human_reviewer'] = 'Accidental pending signature'
        self.lines(self.review/'review_ledger.jsonl', [self.ledger])
        with self.assertRaisesRegex(ValueError, 'must not claim human'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_changed_overview_rejected_before_writing(self):
        self.overview.write_bytes(b'changed after AI review')
        with self.assertRaisesRegex(ValueError, 'Overview changed'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_wrong_version_acceptance_rejected_before_writing(self):
        folder = self.root/'acceptance'
        folder.mkdir()
        self.dump(folder/'acceptance_results.json', {'status': 'SUCCESS', 'kb_version': 'different'})
        with self.assertRaisesRegex(ValueError, 'exact KB version'):
            self.package(acceptance_dir=folder)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
