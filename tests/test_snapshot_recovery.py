from __future__ import annotations

import http.client
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from email.message import Message
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.repository import Repository
from bom_change_assistant.web import AppState, Handler, RequestError, SNAPSHOT_BACKUPS_TO_KEEP
from tests.test_xlsx_reader import WORKBOOK_XML, RELS_XML, SHEET_XML


class SnapshotRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'active.sqlite3'
        self.state = AppState(self.path)
        load_demo_snapshot(self.state.repository)
        self.state.repository.save_analysis('OLD', {'analysis_id': 'OLD'}, 'old report')
        self.old = self.contents(self.path)
        self.file = Path(self.temp.name) / 'new.xlsx'
        with zipfile.ZipFile(self.file, 'w') as archive:
            archive.writestr('xl/workbook.xml', WORKBOOK_XML)
            archive.writestr('xl/_rels/workbook.xml.rels', RELS_XML)
            archive.writestr('xl/worksheets/sheet1.xml', SHEET_XML.replace('FG-A', 'NEW-FG'))
        handler = type('RecoveryHandler', (Handler,), {'state': self.state})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.state.abort_import()
        self.temp.cleanup()

    @staticmethod
    def contents(path):
        with closing(sqlite3.connect(path)) as conn:
            return '\n'.join(conn.iterdump())

    def request(self, path, body=None, method='POST', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
            conn.request(method, path, raw if method == 'POST' else None, headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def start_and_import(self, expected=1):
        previous_analysis = self.state.repository.last_analysis()
        self.assertEqual(self.request('/api/snapshot/start', {'expected_files': expected})[0], 200)
        self.assertEqual(self.request('/api/import/file', self.file.read_bytes())[0], 200)
        self.assertEqual(self.contents(self.path), self.old)
        self.assertEqual(self.state.repository.last_analysis(), previous_analysis)

    def test_bad_second_file_preserves_entire_old_snapshot_and_allows_retry(self):
        self.start_and_import(2)
        self.assertGreaterEqual(self.request('/api/import/file', b'not a workbook')[0], 400)
        self.assertEqual(self.contents(self.path), self.old)
        self.assertEqual(self.state.repository.last_analysis()[1], 'old report')
        self.assertIsNone(self.state.staging_repository)
        self.assertEqual(self.request('/api/snapshot/start')[0], 200)

    def test_success_publishes_only_at_finish_and_backups_include_wal_history(self):
        # Hold a WAL reader open so a raw copy would miss committed pages.
        keeper = sqlite3.connect(self.path)
        try:
            keeper.execute('PRAGMA wal_autocheckpoint=0')
            keeper.execute('SELECT COUNT(*) FROM meta').fetchone()
            self.state.repository.save_analysis('WAL', {'analysis_id': 'WAL'}, 'committed WAL report')
            self.old = self.contents(self.path)
            self.start_and_import()
            self.assertEqual(self.request('/api/snapshot/finish')[0], 200)
            self.assertNotEqual(self.contents(self.path), self.old)
            backups = list(self.path.parent.glob('snapshot-backup-*.sqlite3'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(self.contents(backups[0]), self.old)
            self.assertEqual(Repository(backups[0]).last_analysis()[1], 'committed WAL report')
            self.assertIsNone(self.state.repository.last_analysis())
            self.assertEqual(self.state.repository.stats()['bom_edges'], 1)
        finally:
            keeper.close()

    def test_cancel_discards_staged_data(self):
        self.start_and_import()
        self.assertEqual(self.request('/api/snapshot/abort')[0], 200)
        self.assertEqual(self.contents(self.path), self.old)
        self.assertGreaterEqual(self.request('/api/snapshot/finish')[0], 400)

    def test_missing_file_and_empty_batch_cannot_replace_old_snapshot(self):
        self.start_and_import(2)
        self.assertEqual(self.request('/api/snapshot/finish')[0], 400)
        self.assertEqual(self.contents(self.path), self.old)
        self.assertEqual(self.request('/api/snapshot/start')[0], 200)
        self.assertEqual(self.request('/api/snapshot/finish')[0], 400)
        self.assertEqual(self.contents(self.path), self.old)

    def test_import_without_start_and_analysis_during_import_are_rejected(self):
        self.assertEqual(self.request('/api/import/file', self.file.read_bytes())[0], 400)
        self.start_and_import()
        self.assertEqual(self.request('/api/analyze', {})[0], 400)
        self.assertEqual(self.request('/api/snapshot/start')[0], 400)
        self.assertEqual(self.contents(self.path), self.old)

    def test_failed_backup_keeps_active_data_and_cleans_staging(self):
        self.start_and_import()
        with patch.object(self.state, 'copy_database', side_effect=OSError('disk full')):
            self.assertEqual(self.request('/api/snapshot/finish')[0], 500)
        self.assertEqual(self.contents(self.path), self.old)
        self.assertIsNone(self.state.staging_repository)

    def test_failed_demo_import_keeps_active_data(self):
        with patch('bom_change_assistant.web.load_demo_snapshot', side_effect=ValueError('invalid demo')):
            self.assertEqual(self.request('/api/demo')[0], 400)
        self.assertEqual(self.contents(self.path), self.old)

    def test_request_length_contract_is_checked_before_reading(self):
        handler = Handler.__new__(Handler)
        handler.rfile = BytesIO(b"untouched")
        for header, required, status in (
            ("-1", True, 400), ("bad", True, 400), (None, True, 411), ("1025", True, 413),
        ):
            with self.subTest(length=header):
                handler.headers = Message()
                if header is not None:
                    handler.headers["Content-Length"] = header
                with self.assertRaises(RequestError) as error:
                    handler._content_length(1024, required=required)
                self.assertEqual(error.exception.status, status)
                self.assertEqual(handler.rfile.tell(), 0)
        handler.headers = Message()
        self.assertEqual(handler._content_length(1024, required=False), 0)
        handler.headers["Content-Length"] = "2"
        self.assertEqual(handler._content_length(1024, required=True), 2)

    def test_incomplete_or_timed_out_body_does_not_reach_dispatch(self):
        handler = Handler.__new__(Handler)
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 5
        handler.rfile = BytesIO(b"a")
        with self.assertRaises(RequestError) as incomplete:
            handler._read_body(2, BytesIO())
        self.assertEqual(incomplete.exception.status, 400)
        handler.connection.settimeout.assert_called_with(5)

        handler.rfile = Mock()
        handler.rfile.read1.side_effect = TimeoutError
        with self.assertRaises(RequestError) as timeout:
            handler._read_body(2, BytesIO())
        self.assertEqual(timeout.exception.status, 408)
        handler.connection.settimeout.assert_called_with(5)

    def test_paused_upload_keeps_reads_available_and_cannot_join_next_batch(self):
        status, body = self.request('/api/snapshot/start')
        self.assertEqual(status, 200)
        headers = {"X-BOM-Import-Batch": json.loads(body)["batch_id"]}
        reading, resume = threading.Event(), threading.Event()
        read_body = Handler._read_body

        def hold_upload(handler, length, output):
            if handler.path == '/api/import/file':
                reading.set()
                if not resume.wait(4):
                    raise RuntimeError("test did not release the upload")
            return read_body(handler, length, output)

        with patch.object(Handler, '_read_body', hold_upload):
            with ThreadPoolExecutor(max_workers=1) as pool:
                upload = pool.submit(self.request, '/api/import/file', self.file.read_bytes(), headers=headers)
                try:
                    self.assertTrue(reading.wait(1))
                    self.assertEqual(self.request('/api/health', method='GET')[0], 200)
                    self.assertEqual(self.request('/api/status', method='GET')[0], 200)
                    self.assertFalse(upload.done())
                    self.assertEqual(self.request('/api/snapshot/abort')[0], 200)
                    self.assertEqual(self.request('/api/snapshot/start')[0], 200)
                finally:
                    resume.set()
                self.assertEqual(upload.result(timeout=3)[0], 409)
        self.assertEqual(self.request('/api/snapshot/abort', headers=headers)[0], 409)
        self.assertEqual(self.state.imported_files, 0)
        self.assertIsNotNone(self.state.staging_importer)
        self.assertEqual(self.request('/api/import/file', self.file.read_bytes())[0], 200)
        self.assertEqual(self.request('/api/snapshot/finish')[0], 200)

    def test_backup_retention_is_per_database_and_spares_legacy_backups(self):
        root = self.path.parent
        existing = root / 'snapshot-backup-legacy.sqlite3'
        existing.write_bytes(b'unscoped')
        other = AppState(root / 'other.sqlite3')
        other.begin_import()
        load_demo_snapshot(other.staging_repository)
        other.imported_files = 1
        other.finish_import()
        excluded = set(root.glob('snapshot-backup-*.sqlite3'))
        latest = []
        for index in range(SNAPSHOT_BACKUPS_TO_KEEP + 2):
            self.state.repository.save_analysis(str(index), {"analysis_id": str(index)}, str(index))
            old_backups = set(root.glob('snapshot-backup-*.sqlite3'))
            self.assertEqual(self.request('/api/demo')[0], 200)
            current = set(root.glob('snapshot-backup-*.sqlite3'))
            (new,) = current - old_backups
            self.assertEqual(Repository(new).last_analysis()[1], str(index))
            latest.append(new)
            os.utime(new, (1000 + index, 1000 + index))
        self.assertEqual(set(root.glob('snapshot-backup-*.sqlite3')),
                         excluded | set(latest[-SNAPSHOT_BACKUPS_TO_KEEP:]))
        self.assertEqual(existing.read_bytes(), b'unscoped')

    def test_process_exit_before_finish_preserves_old_snapshot_on_restart(self):
        code = '''import os,sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from bom_change_assistant.web import AppState
s=AppState(Path(sys.argv[1])); s.begin_import(1)
s.staging_importer.import_file(Path(sys.argv[2])); s.imported_files=1
os._exit(0)
'''
        subprocess.run([sys.executable, '-c', code, str(self.path), str(self.file),
                        str(Path(__file__).resolve().parents[1] / 'src')], check=True)
        restarted = AppState(self.path)
        self.assertEqual(self.contents(self.path), self.old)
        self.assertEqual(restarted.repository.last_analysis()[1], 'old report')
        restarted.begin_import(1)
        restarted.abort_import()


if __name__ == '__main__':
    unittest.main()
