"""Cancellation during unload must not strand HA's reload state transition."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

path = Path(__file__).parents[1] / 'custom_components/hophop_casa'
spec = importlib.util.spec_from_file_location('cancel_unload_integration', path / '__init__.py',
                                            submodule_search_locations=[str(path)])
integration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = integration
spec.loader.exec_module(integration)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

        async def stop():
            self.entered.set()
            await self.release.wait()

        self.client = SimpleNamespace(stop=AsyncMock(side_effect=stop))
        self.other = object()
        self.hass = SimpleNamespace(data={'hophop_casa': {'entry': self.client, 'other': self.other}})
        self.entry = SimpleNamespace(entry_id='entry')

    async def test_request_cancellation_finishes_unload_and_retains_other_entry(self):
        task = asyncio.create_task(integration.async_unload_entry(self.hass, self.entry))
        await self.entered.wait()
        with self.assertLogs(integration.__name__, level='WARNING'):
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.release.set()
            self.assertTrue(await asyncio.wait_for(task, 1))
        self.assertEqual(task.cancelling(), 0)
        self.assertNotIn('entry', self.hass.data['hophop_casa'])
        self.assertIs(self.hass.data['hophop_casa']['other'], self.other)
        self.client.stop.assert_awaited_once()

    async def test_repeated_request_cancellation_does_not_cancel_cleanup(self):
        task = asyncio.create_task(integration.async_unload_entry(self.hass, self.entry))
        await self.entered.wait()
        with self.assertLogs(integration.__name__, level='WARNING') as logs:
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            self.release.set()
            self.assertTrue(await asyncio.wait_for(task, 1))
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(task.cancelling(), 0)

    async def test_cleanup_failure_is_not_hidden_after_request_cancellation(self):
        async def fail():
            self.entered.set()
            await self.release.wait()
            raise RuntimeError('cleanup deadline')
        self.client.stop = AsyncMock(side_effect=fail)
        task = asyncio.create_task(integration.async_unload_entry(self.hass, self.entry))
        await self.entered.wait()
        with self.assertLogs(integration.__name__, level='WARNING'):
            task.cancel()
            await asyncio.sleep(0)
            self.release.set()
            with self.assertRaisesRegex(RuntimeError, 'cleanup deadline'):
                await asyncio.wait_for(task, 1)
        self.assertIs(self.hass.data['hophop_casa']['entry'], self.client)

    async def test_cancelled_cleanup_propagates_and_retains_client(self):
        self.client.stop = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await integration.async_unload_entry(self.hass, self.entry)
        self.assertIs(self.hass.data['hophop_casa']['entry'], self.client)

    async def test_options_update_schedules_reload_outside_request(self):
        self.hass.config_entries = SimpleNamespace(async_schedule_reload=Mock())
        await integration._reload(self.hass, self.entry)
        self.hass.config_entries.async_schedule_reload.assert_called_once_with('entry')
