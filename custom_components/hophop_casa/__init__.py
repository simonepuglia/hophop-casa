"""Config-entry lifecycle for the HopHop Casa tunnel."""
import asyncio
import logging
from .client import RemoteClient
from .const import DOMAIN, DEFAULT_WS, DEFAULT_LOCAL_URL

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry):
    settings = {**entry.data, **entry.options}
    client = RemoteClient(hass, settings.get('ws_url', DEFAULT_WS), settings['device_token'],
                          settings.get('local_ha_url', DEFAULT_LOCAL_URL))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = client
    entry.async_on_unload(entry.add_update_listener(_reload))
    await client.start(lambda coro: entry.async_create_background_task(
        hass, coro, 'hophop-casa-tunnel'))
    _LOGGER.info('HopHop Casa client setup complete (entry=%s)', entry.entry_id)
    return True

async def _reload(hass, entry):
    # Let HA schedule the reload outside the options-update request lifecycle.
    hass.config_entries.async_schedule_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    clients = hass.data.get(DOMAIN, {})
    client = clients.get(entry.entry_id)
    if client:
        # Keep the reference until cleanup succeeds, so an interrupted unload
        # does not lose the only handle to its running task.
        _LOGGER.info('HopHop Casa unload requested (entry=%s)', entry.entry_id)
        cleanup = asyncio.create_task(client.stop(), name='hophop-casa-unload')
        interrupted = False
        while True:
            try:
                # wait() does not pass caller cancellation to the cleanup task.
                # Unlike a cancelled shield wrapper on Python 3.14.8, it also
                # does not log an exception we will explicitly retrieve below.
                await asyncio.wait({cleanup})
                cleanup.result()
                break
            except asyncio.CancelledError:
                if cleanup.cancelled():
                    raise
                # A reload HTTP request can arrive through this very tunnel.
                # Closing it makes HA cancel that request handler. Propagating
                # cancellation here strands HA in UNLOAD_IN_PROGRESS, before
                # it can start the replacement entry. Finish this bounded
                # critical section and let HA complete the reload instead.
                if not interrupted:
                    _LOGGER.warning(
                        'HopHop Casa reload request disconnected; finishing unload '
                        'so Home Assistant can reconnect (entry=%s)', entry.entry_id)
                    interrupted = True
                if task := asyncio.current_task():
                    task.uncancel()
        if clients.get(entry.entry_id) is client:
            clients.pop(entry.entry_id)
    _LOGGER.info('HopHop Casa client unload complete (entry=%s)', entry.entry_id)
    return True
