import asyncio
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from cdwagent.config import CDWConfig, ClinicalDBConfig
from cdwagent.db import run_query_csv
from cdwagent.server import create_cdw_server


class CDWReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = ClinicalDBConfig(username='test', password='test')
        self.server = create_cdw_server(CDWConfig(clinical_db=self.db))

    def call(self, name, arguments):
        async def invoke():
            async with Client(self.server) as client:
                return await client.call_tool('CDW-' + name, arguments)
        return asyncio.run(invoke())

    def test_preview_discloses_truncation(self):
        with patch('cdwagent.db.run_rows', return_value=(['value'], [(1,), (2,), (3,)])) as execute:
            result = run_query_csv(self.db, 'SELECT value FROM t', row_limit=2)
        self.assertIn('Preview truncated to 2 rows', result)
        self.assertTrue(result.endswith('value\n1\n2'))
        self.assertEqual(execute.call_args.kwargs['row_limit'], 3)
        with self.assertRaises(ToolError):
            run_query_csv(self.db, 'SELECT 1', row_limit=0)

    def test_mcp_limits_reject_unbounded_request(self):
        with patch('cdwagent.tools.queries._execute_readonly_query') as execute:
            with self.assertRaises(Exception):
                self.call('query', {'sql_query': 'SELECT 1', 'row_limit': 1001})
            execute.assert_not_called()

    def test_patient_identifier_never_ambiguously_matches_both_keys(self):
        with patch('cdwagent.tools.queries._execute_readonly_query', return_value='ok') as execute:
            self.call('get_patient_demographics', {'patient_id': '123'})
            query = execute.call_args.args[1]
            self.assertIn("PatientDurableKey = '123'", query)
            self.assertNotIn(' OR ', query)
            self.assertIn('IsCurrent = 1', query)
            self.call('get_patient_demographics', {'patient_id': '123', 'id_type': 'PatientKey'})
            query = execute.call_args.args[1]
            self.assertIn('PatientDurableKey IN (SELECT PatientDurableKey', query)
            self.assertIn("WHERE PatientKey = '123'", query)

    def test_cohort_failure_does_not_retry_with_surrogate(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.side_effect = RuntimeError('query timeout')
        with patch('cdwagent.tools.stats.get_connection', return_value=connection):
            with self.assertRaises(Exception):
                self.call('cohort_summary', {'patient_key_query': 'SELECT PatientDurableKey FROM deid_uf.PatientDim'})
        self.assertEqual(cursor.execute.call_count, 1)
        connection.close.assert_called_once()

    def test_catalog_permission_failure_does_not_trigger_full_count(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.execute.side_effect = [RuntimeError('permission denied'), None, None]
        cursor.fetchall.return_value = [('PatientDurableKey', 'bigint')]
        cursor.fetchone.return_value = (10, 0)
        with patch('cdwagent.tools.stats.get_connection', return_value=connection):
            result = self.call('summarize_table', {'table_name': 'PatientDim'})
        result = json.loads(result.content[0].text)
        self.assertIsNone(result['row_count'])
        self.assertEqual(result['row_count_source'], 'unavailable')
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        self.assertFalse(any(s.startswith('SELECT COUNT(*) FROM [') for s in statements))

    def test_export_is_private_and_complete_and_never_overwrites(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.description = [('value',)]
        cursor.fetchmany.side_effect = [[('ok',)], []]
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'result.csv'
            with patch('cdwagent.tools.export.get_connection', return_value=connection):
                self.call('export_query_to_csv', {'sql_query': 'SELECT 1', 'filepath': str(destination)})
                self.assertEqual(destination.read_text(), 'value\nok\n')
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                with self.assertRaises(Exception):
                    self.call('export_query_to_csv', {'sql_query': 'SELECT 1', 'filepath': str(destination)})
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_export_failure_does_not_publish_partial_output(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.description = [('value',)]
        cursor.fetchmany.side_effect = [[('ok',)], RuntimeError('connection lost')]
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'result.csv'
            with patch('cdwagent.tools.export.get_connection', return_value=connection):
                with self.assertRaises(Exception):
                    self.call('export_query_to_csv', {'sql_query': 'SELECT 1', 'filepath': str(destination)})
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_schema_identifier_validation(self):
        with self.assertRaises(ValueError):
            create_cdw_server(CDWConfig(clinical_db=self.db, db_schema='dbo]; DROP TABLE t;--'))

    def test_empty_concept_cannot_scan_entire_catalog(self):
        from cdwagent.tools.concepts import _match_clause
        with self.assertRaises(ToolError):
            _match_clause("  ", ["Code"], ["Name"])

    def test_timeout_points_to_monitored_jobs_and_redacts_password(self):
        from cdwagent.db import run_rows
        connection = MagicMock()
        connection.cursor.return_value.execute.side_effect = RuntimeError('timeout test')
        with patch('cdwagent.db.get_connection', return_value=connection):
            with self.assertRaises(ToolError) as error:
                run_rows(self.db, 'SELECT 1')
        self.assertIn('submit_query_job', str(error.exception))
        self.assertIn('[redacted]', str(error.exception))
