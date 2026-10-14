"""Generation-scoped tunnel with bounded I/O and automatic DNS recovery."""
import asyncio
import base64
import json
import logging
import random
import time
from urllib.parse import urlsplit

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)
MAX_FRAME = 64 * 1024 * 1024


class RemoteClient:
    def __init__(self, hass, ws_url, token, local_ha_url, *, retry_base=2, retry_max=60, shutdown_grace=3, shutdown_final=2):
        self.hass = hass
        self.endpoint = str(URL(ws_url).with_query(None))
        self.token = token
        self.local_url = local_ha_url.rstrip('/')
        self.retry_base, self.retry_max = retry_base, retry_max
        self.task = None
        self.session = None
        self.shutdown_grace = shutdown_grace
        self.shutdown_final = shutdown_final
        self.shutdown_stage = None
        self.ws = None
        self.state = 'stopped'
        self.last_error = None
        self.reconnects = 0
        self.connected_since = None
        self._stop = asyncio.Event()

    async def start(self, task_factory=None):
        if self.task is None or self.task.done():
            self._stop.clear()
            # Let Home Assistant own the lifetime when called by a config entry.
            self.task = (task_factory(self._run()) if task_factory else
                         asyncio.create_task(self._run(), name='hophop-casa-tunnel'))

    async def stop(self):
        """Bound shutdown even when aiohttp or a worker waits during cancellation."""
        self._stop.set()
        self.state = 'stopping'
        self.shutdown_stage = 'cancel_tunnel'
        task = self.task
        _LOGGER.info('HopHop Casa 1.0.1: stopping tunnel')
        if task and not task.done():
            task.cancel()
            # wait_for/gather can themselves wait forever for cancellation to
            # finish. wait() observes the deadline without joining indefinitely.
            done, _ = await asyncio.wait({task}, timeout=self.shutdown_grace)
            if not done:
                self.shutdown_stage = 'close_network_session'
                stack = task.get_stack(limit=5)
                locations = ', '.join(f'{f.f_code.co_name}:{f.f_lineno}' for f in stack)
                _LOGGER.warning('HopHop Casa shutdown is slow (%s); closing network session', locations or 'awaiting cleanup')
                if self.session and not self.session.closed:
                    closer = asyncio.create_task(self.session.close(), name='hophop-casa-close-session')
                    closed, _ = await asyncio.wait({closer}, timeout=1)
                    if not closed:
                        closer.cancel()
                    closer.add_done_callback(self._consume_task_result)
                self.shutdown_stage = 'cancel_remaining_cleanup'
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=self.shutdown_final)
                if not done:
                    self.state = 'stop_failed'
                    self.last_error = 'Tunnel cleanup did not finish within the shutdown deadline'
                    _LOGGER.error('HopHop Casa shutdown failed; retaining client for diagnostics')
                    # HA marks this as FAILED_UNLOAD instead of Unloading forever.
                    raise RuntimeError(self.last_error)
        if task:
            self._consume_task_result(task)
        if self.task is task:
            self.task = None
        self.ws = None
        self.session = None
        self.connected_since = None
        self.state = 'stopped'
        self.shutdown_stage = 'complete'
        _LOGGER.info('HopHop Casa 1.0.1: tunnel stopped')

    @staticmethod
    def _consume_task_result(task):
        if not task.cancelled():
            task.exception()

    async def _run(self):
        attempt = 0
        while not self._stop.is_set():
            started = time.monotonic()
            self.state = 'connecting'
            try:
                # Recreate the connector after every disconnect. Use the OS resolver
                # instead of aiodns, which can time out in HA/container DNS setups.
                connector = aiohttp.TCPConnector(
                    resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=30,
                    limit=40, enable_cleanup_closed=True)
                timeout = aiohttp.ClientTimeout(total=30, connect=15, sock_read=25)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                                auto_decompress=False) as session:
                    self.session = session
                    async with asyncio.timeout(25):
                        ws = await session.ws_connect(
                            self.endpoint, params={'token': self.token}, heartbeat=20,
                            max_msg_size=MAX_FRAME)
                        welcome = await ws.receive_json()
                        if welcome.get('type') != 'WELCOME':
                            await ws.close()
                            raise ConnectionError('missing welcome')
                    async with ws:
                        self.ws = ws
                        self.state = 'connected'
                        self.last_error = None
                        self.connected_since = time.time()
                        _LOGGER.info('HopHop Casa tunnel connected to %s', urlsplit(self.endpoint).hostname)
                        await self._serve(session, ws)
                self.last_error = 'Connection closed by server'
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as exc:
                self.last_error = f'Server rejected connection (HTTP {exc.status})'
            except (aiohttp.ClientConnectorError, OSError):
                self.last_error = 'DNS or network connection unavailable'
            except TimeoutError:
                self.last_error = 'Connection timed out'
            except Exception as exc:
                # Never log exception text/URL: it may contain the device token.
                self.last_error = f'Tunnel error ({type(exc).__name__})'
            finally:
                self.session = None
                self.ws = None
                self.connected_since = None
            if self._stop.is_set():
                break
            attempt = 0 if time.monotonic() - started > 60 else min(attempt + 1, 8)
            delay = min(self.retry_max, self.retry_base * 2 ** (attempt - 1))
            delay *= random.uniform(.8, 1.2)
            self.reconnects += 1
            self.state = 'reconnecting'
            _LOGGER.warning('HopHop Casa: %s; retrying in %.1fs', self.last_error, delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _serve(self, session, ws):
        tasks, peers, queues = set(), {}, {}
        send_lock = asyncio.Lock()

        async def send(frame):
            async with asyncio.timeout(10):
                async with send_lock:
                    if not ws.closed:
                        await ws.send_json(frame)

        def spawn(coro):
            task = asyncio.create_task(coro)
            tasks.add(task)
            def done(t):
                tasks.discard(t)
                if not t.cancelled() and t.exception():
                    _LOGGER.debug('HopHop Casa worker ended: %s', type(t.exception()).__name__)
            task.add_done_callback(done)

        async def http(frame):
            rid = frame['rid']
            try:
                path = frame['path']
                if not path.startswith('/') or path.startswith('//'):
                    raise ValueError('Invalid path')
                async with session.request(frame['method'], URL(self.local_url + path, encoded=True),
                        headers=frame.get('headers', {}),
                        data=base64.b64decode(frame.get('body_b64') or '', validate=True),
                        allow_redirects=False) as response:
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_FRAME:
                            raise ValueError('Response too large')
                        chunks.append(chunk)
                    body = b''.join(chunks)
                    await send({'type': 'RES', 'rid': rid, 'status': response.status,
                                'headers': dict(response.headers),
                                'body_b64': base64.b64encode(body).decode()})
            except (aiohttp.ClientError, OSError, ValueError, TimeoutError):
                await send({'type': 'RES', 'rid': rid, 'status': 502, 'headers': {}, 'body_b64': ''})

        async def websocket(frame, queue):
            rid = frame['rid']
            reader = None
            try:
                path = frame['path']
                if not path.startswith('/') or path.startswith('//'):
                    raise ValueError('Invalid path')
                async with asyncio.timeout(15):
                    peer = await session.ws_connect(URL(self.local_url + path, encoded=True),
                        headers=frame.get('headers', {}), heartbeat=20, max_msg_size=MAX_FRAME)
                peers[rid] = peer
                async def pump():
                    try:
                        async for msg in peer:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await send({'type': 'WS_MSG', 'rid': rid, 'kind': 'text', 'data': msg.data})
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await send({'type': 'WS_MSG', 'rid': rid, 'kind': 'bin',
                                            'data_b64': base64.b64encode(msg.data).decode()})
                    finally:
                        # Never await a queue slot during cancellation. The consumer
                        # may already be stopped and unable to free one. After the
                        # peer ends, queued outgoing frames cannot be delivered.
                        if queue.full():
                            queue.get_nowait()
                        queue.put_nowait(None)
                reader = asyncio.create_task(pump())
                while True:
                    message = await queue.get()
                    if message is None or message['type'] == 'WS_CLOSE':
                        break
                    async with asyncio.timeout(10):
                        if message.get('kind') == 'text':
                            await peer.send_str(message.get('data', ''))
                        elif message.get('kind') == 'bin':
                            await peer.send_bytes(base64.b64decode(message.get('data_b64') or '', validate=True))
            finally:
                if reader:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                peer = peers.pop(rid, None)
                queues.pop(rid, None)
                if peer:
                    async with asyncio.timeout(3):
                        await peer.close()
                if not self._stop.is_set():
                    await send({'type': 'WS_CLOSE', 'rid': rid})

        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    frame = json.loads(msg.data)
                    if not isinstance(frame, dict):
                        continue
                    kind, rid = frame.get('type'), frame.get('rid')
                    if kind == 'PING':
                        await send({'type': 'PONG'})
                    elif isinstance(rid, str):
                        if kind == 'REQ':
                            if len(tasks) >= 96:
                                await send({'type': 'RES', 'rid': rid, 'status': 503, 'headers': {}, 'body_b64': ''})
                            else:
                                spawn(http(frame))
                        elif kind == 'WS_OPEN' and rid not in queues:
                            if len(queues) >= 64:
                                await send({'type': 'WS_CLOSE', 'rid': rid})
                            else:
                                queue = queues[rid] = asyncio.Queue(maxsize=128)
                                spawn(websocket(frame, queue))
                        elif kind in ('WS_MSG', 'WS_CLOSE') and rid in queues:
                            # Bounded backpressure; an overloaded peer forces a clean
                            # reconnect rather than dropping ordered HA messages.
                            queues[rid].put_nowait(frame)
                except (ValueError, TypeError, KeyError):
                    continue
        finally:
            pending = list(tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
