"""Reproduce cancellation with a full forwarding queue, without HA installation."""
import asyncio
import json
import os
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
from types import SimpleNamespace
import aiohttp
spec=importlib.util.spec_from_file_location('reload_client',Path(os.environ.get('CLIENT_TEST_PATH',str(Path(__file__).parents[1]/'custom_components/hophop_casa/client.py'))))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_with_full_queue_and_blocked_local_writer(self):
        frames=asyncio.Queue(); blocked=asyncio.Event(); release=asyncio.Event(); queues=[]
        class Peer:
            closed=False
            def __aiter__(self):return self
            async def __anext__(self):await asyncio.Event().wait()
            async def send_str(self,data):blocked.set();await release.wait()
            async def close(self):self.closed=True
        peer=Peer()
        class Session:
            async def ws_connect(self,*a,**k):return peer
        class Broker:
            closed=False
            def __aiter__(self):return self
            async def __anext__(self):return await frames.get()
            async def send_json(self,data):pass
        original=asyncio.Queue
        class TrackedQueue(original):
            def __init__(self,*a,**k):super().__init__(*a,**k);queues.append(self)
        client=m.RemoteClient(None,'wss://example.test/ws','secret','http://localhost:8123')
        def frame(data):return SimpleNamespace(type=aiohttp.WSMsgType.TEXT,data=json.dumps(data))
        with patch.object(m.asyncio,'Queue',TrackedQueue):
            client.task=asyncio.create_task(client._serve(Session(),Broker()))
            await frames.put(frame({'type':'WS_OPEN','rid':'test','path':'/api/websocket'}))
            await frames.put(frame({'type':'WS_MSG','rid':'test','kind':'text','data':'block'}))
            await asyncio.wait_for(blocked.wait(),1)
            q=queues[0]
            for i in range(q.maxsize):q.put_nowait({'type':'WS_MSG','kind':'text','data':'queued'})
            stopped=asyncio.create_task(client.stop())
            try:
                done,_=await asyncio.wait({stopped},timeout=.5)
                self.assertIn(stopped,done,'Unload hangs when reader tries to signal a full queue')
                await stopped
                self.assertTrue(peer.closed)
            finally:
                release.set()
                while not q.empty():q.get_nowait()
                if not stopped.done():
                    stopped.cancel()
                    await asyncio.gather(stopped,return_exceptions=True)

    async def test_stop_interrupts_stuck_first_cancellation_cleanup(self):
        entered=asyncio.Event()
        async def stuck():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.Event().wait()
        client=m.RemoteClient(None,'wss://example.test/ws','secret','http://localhost:8123',shutdown_grace=.02,shutdown_final=.1)
        client.task=asyncio.create_task(stuck())
        task=client.task
        await entered.wait()
        with self.assertLogs(m.__name__,level='WARNING') as logs:
            await asyncio.wait_for(client.stop(),.5)
        self.assertTrue(task.done())
        self.assertEqual(client.state,'stopped')
        self.assertEqual(client.shutdown_stage,'complete')
        self.assertNotIn('secret',' '.join(logs.output))

    async def test_shutdown_failure_has_deadline_and_retains_task(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def stubborn():
            entered.set()
            while not release.is_set():
                try: await release.wait()
                except asyncio.CancelledError: pass
        client=m.RemoteClient(None,'wss://example.test/ws','secret','http://localhost:8123',shutdown_grace=.02,shutdown_final=.02)
        client.task=asyncio.create_task(stubborn());task=client.task
        await entered.wait()
        try:
            with self.assertLogs(m.__name__,level='WARNING'):
                with self.assertRaises(RuntimeError):
                    await asyncio.wait_for(client.stop(),.5)
            self.assertIs(client.task,task)
            self.assertEqual(client.state,'stop_failed')
        finally:
            release.set();await task;await client.stop()
