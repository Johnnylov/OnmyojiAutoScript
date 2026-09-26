from pathlib import Path
import tempfile
import unittest

from module.server.solana_disk_usage import sample_existing_files


class ExistingFilesTests(unittest.TestCase):
    def test_history_budget_excludes_existing_files_and_partial_scan_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, count in [('log/run.txt', 3), ('log/error/frame.png', 7),
                                ('config/backup/p.json', 11), ('runtime_data/events/a.jsonl', 17)]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x' * count)
            result = sample_existing_files(root)
            self.assertTrue(result['complete'])
            self.assertFalse(result['included_in_budget'])
            self.assertEqual(result['bytes'], {'text_logs': 3, 'screenshots': 7,
                                               'config_backups': 11, 'other_log_files': 0})
            self.assertFalse(sample_existing_files(root, max_entries=1)['complete'])
