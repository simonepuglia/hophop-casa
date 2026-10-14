"""Real sockets exercising the actual HA client (no Home Assistant install needed)."""
import asyncio
import base64
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

spec=importlib.util.spec_from_file_location('ha_client',Path(__file__).parents[1]/'custom_components/hophop_casa/client.py')
client_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(client_module)

async def until(condition):
    async with asyncio.timeout(5):
        while not condition(): await asyncio.sleep(.01)

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections=[]; self.reject=0
        async def tunnel(request):
            if self.reject:
                self.reject-=1; return web.Response(status=502)
            ws=web.WebSocketResponse();await ws.prepare(request)
            await ws.send_json({'type':'WELCOME'});self.connections.append(ws)
            async for msg in ws:
                if msg.type==aiohttp.WSMsgType.TEXT: self.frames.append(msg.json())
            return ws
        async def local_ws(request):
            ws=web.WebSocketResponse();await ws.prepare(request)
            async for msg in ws:
                if msg.type==aiohttp.WSMsgType.TEXT: await ws.send_str(msg.data)
                elif msg.type==aiohttp.WSMsgType.BINARY: await ws.send_bytes(msg.data)
            return ws
        async def local_http(request):
            response=web.StreamResponse();await response.prepare(request)
            await response.write(b'HTTP ');await asyncio.sleep(.02);await response.write(b'OK');await response.write_eof()
            return response
        self.frames=[]
        app=web.Application();app.router.add_get('/ws',tunnel)
        self.broker=TestServer(app);await self.broker.start_server()
        app=web.Application();app.router.add_get('/api/websocket',local_ws);app.router.add_get('/api/config',local_http)
        self.local=TestServer(app);await self.local.start_server()
        self.client=client_module.RemoteClient(None,str(self.broker.make_url('/ws')).replace('http:','ws:'),'private-token',str(self.local.make_url('/')).rstrip('/'),retry_base=.02,retry_max=.05)
    async def asyncTearDown(self):
        await self.client.stop(); await self.broker.close(); await self.local.close()
    async def test_502_then_recovery_and_clean_close_recovery(self):
        self.reject=2; await self.client.start()
        await until(lambda:len(self.connections)==1 and self.client.state=='connected')
        first=self.client.ws
        await self.connections[0].close()
        # Accepting a socket and sending WELCOME does not mean the client has
        # received it yet. Observe the replacement client, not just the server.
        await until(lambda:len(self.connections)==2 and self.client.state=='connected'
                    and self.client.ws is not first)
        await self.connections[1].send_json({'type':'PING'})
        await until(lambda:any(frame.get('type')=='PONG' for frame in self.frames))
        self.assertEqual(self.client.state,'connected'); self.assertGreaterEqual(self.client.reconnects,3)
    async def test_dns_failure_then_recovery(self):
        original=aiohttp.ClientSession.ws_connect;calls=0
        def fail_once(session,*a,**kw):
            nonlocal calls
            calls+=1
            if calls==1: raise OSError('DNS private-token must not be logged')
            return original(session,*a,**kw)
        with patch.object(aiohttp.ClientSession,'ws_connect',fail_once),self.assertLogs(client_module.__name__,level='WARNING') as logs:
            await self.client.start();await until(lambda:self.client.state=='connected')
        self.assertNotIn('private-token',' '.join(logs.output))
    async def test_stop_start_idempotent_and_no_lingering_workers(self):
        await self.client.start();first=self.client.task;await self.client.start();self.assertIs(first,self.client.task)
        await until(lambda:self.client.state=='connected');await self.client.stop();self.assertTrue(first.done())
        await self.client.start();await until(lambda:len(self.connections)==2);self.assertIsNot(first,self.client.task)
    async def test_http_and_websocket_frames(self):
        await self.client.start();await until(lambda:self.client.state=='connected');ws=self.connections[-1]
        await ws.send_json({'type':'REQ','rid':'http','method':'GET','path':'/api/config'})
        await until(lambda:any(f.get('rid')=='http' for f in self.frames))
        self.assertEqual(base64.b64decode(self.frames[-1]['body_b64']),b'HTTP OK')
        await ws.send_json({'type':'WS_OPEN','rid':'socket','path':'/api/websocket'})
        # A message immediately after open must be queued until the local handshake.
        await ws.send_json({'type':'WS_MSG','rid':'socket','kind':'text','data':'hello'})
        await until(lambda:any(f.get('data')=='hello' for f in self.frames))
        await ws.send_json({'type':'WS_MSG','rid':'socket','kind':'bin','data_b64':'AP8='})
        await until(lambda:any(f.get('data_b64')=='AP8=' for f in self.frames))
        await ws.send_json({'type':'WS_CLOSE','rid':'socket'})
        await until(lambda:any(f.get('type')=='WS_CLOSE' for f in self.frames))

    async def test_config_entry_reload_replaces_tunnel_three_times(self):
        from types import SimpleNamespace
        import sys
        location=Path(__file__).parents[1]/'custom_components/hophop_casa'
        package_spec=importlib.util.spec_from_file_location('entry_reload_test',location/'__init__.py',submodule_search_locations=[str(location)])
        integration=importlib.util.module_from_spec(package_spec);sys.modules[package_spec.name]=integration;package_spec.loader.exec_module(integration)
        hass=SimpleNamespace(data={})
        class Entry:
            entry_id='test';options={}
            data={'ws_url':self.client.endpoint,'device_token':'private-token','local_ha_url':self.client.local_url}
            def add_update_listener(self,listener):return lambda:None
            def async_on_unload(self,cleanup):pass
            def async_create_background_task(self,hass,coro,name):return asyncio.create_task(coro,name=name)
        entry=Entry()
        for i in range(3):
            self.assertTrue(await integration.async_setup_entry(hass,entry))
            client=hass.data['hophop_casa']['test'];task=client.task
            await until(lambda:client.state=='connected')
            self.assertTrue(await asyncio.wait_for(integration.async_unload_entry(hass,entry),1))
            self.assertTrue(task.done());self.assertNotIn('test',hass.data['hophop_casa'])
        self.assertEqual(len(self.connections),3)
