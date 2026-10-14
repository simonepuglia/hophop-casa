"""Diagnostics identify the new integration without disclosing credentials."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

location = Path(__file__).parents[1] / 'custom_components/hophop_casa'
spec = importlib.util.spec_from_file_location('diagnostic_integration', location / '__init__.py',
                                            submodule_search_locations=[str(location)])
integration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = integration
spec.loader.exec_module(integration)
spec = importlib.util.spec_from_file_location('diagnostic_integration.diagnostics', location / 'diagnostics.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unloaded_entry_identifies_new_domain_and_version(self):
        result = await diagnostics.async_get_config_entry_diagnostics(
            SimpleNamespace(data={}), SimpleNamespace(entry_id='test', state=SimpleNamespace(value='not_loaded')))
        self.assertEqual(result['integration_domain'], 'hophop_casa')
        self.assertEqual(result['version'], '1.0.1')
        self.assertEqual(result['entry_state'], 'not_loaded')
        self.assertEqual(result['state'], 'unloaded')
        self.assertFalse(result['task_running'])

    async def test_connected_entry_does_not_expose_endpoint_or_token(self):
        client = integration.RemoteClient(None, 'wss://private.example/ws', 'private-token', 'http://private-host:8123')
        client.state = 'connected'
        result = await diagnostics.async_get_config_entry_diagnostics(
            SimpleNamespace(data={'hophop_casa': {'test': client}}),
            SimpleNamespace(entry_id='test', state=SimpleNamespace(value='loaded')))
        self.assertEqual(result['state'], 'connected')
        self.assertEqual(result['entry_state'], 'loaded')
        serialized = json.dumps(result)
        for private in ('private-token', 'private.example', 'private-host'):
            self.assertNotIn(private, serialized)
