import asyncio
import json
import unittest
from unittest.mock import patch

from fastmcp import Client, FastMCP
from cdwagent.tools.schema import get_schema_ref, register_schema_tools


class SchemaReviewTests(unittest.TestCase):
    def setUp(self):
        self.server = FastMCP('schema-tests')
        register_schema_tools(self.server, 'CDW-')

    def call(self, name, **arguments):
        async def invoke():
            async with Client(self.server) as client:
                result = await client.call_tool('CDW-' + name, arguments)
                return json.loads(result.content[0].text), len(result.content[0].text.encode())
        return asyncio.run(invoke())

    def test_overview_is_small_and_pagination_recovers_all_tables(self):
        first, size = self.call('get_database_overview')
        self.assertLess(size, 12000)
        self.assertEqual(first['returned'], 20)
        self.assertTrue(first['has_more'])
        self.assertIn('not a live', first['source'])
        tables = [x['table_name'] for x in first['results']]
        page = first
        while page['has_more']:
            page, _ = self.call('get_database_overview', offset=page['next_offset'])
            tables += [x['table_name'] for x in page['results']]
        self.assertEqual(tables, sorted(get_schema_ref()))
        self.assertIsNone(page['next_offset'])

    def test_description_detail_is_lossless_and_clipping_explicit(self):
        fixture = {'Example': {'description': 'd' * 1000, 'columns': []}}
        with patch('cdwagent.tools.schema._get_schema_ref', return_value=fixture):
            compact, _ = self.call('get_database_overview')
            detailed, _ = self.call('get_database_overview', detail=True)
        self.assertTrue(compact['results'][0]['description_truncated'])
        self.assertEqual(len(compact['results'][0]['description']), 180)
        self.assertFalse(detailed['results'][0]['description_truncated'])
        self.assertEqual(detailed['results'][0]['description'], 'd' * 1000)

    def test_column_pages_preserve_complete_dictionary_with_detail(self):
        first, size = self.call('describe_table', table_name='patientdim')
        self.assertLess(size, 12000)
        self.assertEqual(first['table_name'], 'PatientDim')
        self.assertEqual(first['returned'], 25)
        self.assertIn('not evidence of a current record', first['data_notes'])
        self.assertNotIn('always fall back', first['data_notes'])
        all_columns = []
        offset = 0
        while True:
            page, _ = self.call('describe_table', table_name='PatientDim', offset=offset, detail=True)
            all_columns.extend(page['results'])
            if not page['has_more']:
                break
            offset = page['next_offset']
        expected = get_schema_ref()['PatientDim']['columns']
        self.assertEqual(len(all_columns), len(expected))
        for source, returned in zip(expected, all_columns):
            for key, value in source.items():
                self.assertEqual(returned[key], value)

    def test_search_flattens_matches_and_paginates_without_loss(self):
        fixture = {
            'Alpha': {'description': 'needle table', 'columns': [
                {'name': f'needle_{i}', 'data_type': 'bigint', 'description': 'needle ' + 'x'*300,
                 'ordinal_position': i, 'queryable': False, 'note': 'Dictionary only'}
                for i in range(25)]},
            'Beta': {'description': 'another needle table', 'columns': []},
        }
        with patch('cdwagent.tools.schema._get_schema_ref', return_value=fixture):
            first, _ = self.call('search_schema', keyword=' needle ', limit=5)
            self.assertEqual(first['total'], 27)
            self.assertEqual(first['returned'], 5)
            self.assertEqual(first['results'][0]['scope'], 'table')
            self.assertFalse(first['results'][1]['queryable'])
            self.assertTrue(first['results'][1]['description_truncated'])
            matches = first['results']
            page = first
            while page['has_more']:
                page, _ = self.call('search_schema', keyword='needle', offset=page['next_offset'], limit=5)
                matches.extend(page['results'])
            self.assertEqual(len(matches), 27)
            detailed, _ = self.call('search_schema', keyword='needle', offset=1, limit=1, detail=True)
            self.assertEqual(detailed['results'][0]['ordinal_position'], 0)
            self.assertFalse(detailed['results'][0]['description_truncated'])

    def test_search_missing_and_offset_past_end_are_structured_empty_pages(self):
        result, _ = self.call('search_schema', keyword='no_such_column_0xdeadbeef')
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['results'], [])
        result, _ = self.call('get_database_overview', offset=99999)
        self.assertFalse(result['has_more'])
        self.assertEqual(result['returned'], 0)

    def test_blank_search_and_invalid_bounds_are_rejected(self):
        for name, arguments in [
            ('search_schema', {'keyword': '  '}),
            ('get_database_overview', {'limit': 0}),
            ('get_database_overview', {'offset': -1}),
            ('describe_table', {'table_name': 'PatientDim', 'limit': 101}),
            ('search_schema', {'keyword': 'patient', 'limit': 101}),
        ]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(Exception):
                self.call(name, **arguments)
