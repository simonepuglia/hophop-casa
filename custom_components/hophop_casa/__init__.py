"""Config-entry lifecycle for the HopHop Casa tunnel."""
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
    _LOGGER.info('HopHop Casa client setup complete')
    return True

async def _reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    clients = hass.data.get(DOMAIN, {})
    client = clients.get(entry.entry_id)
    if client:
        # Keep the reference until cleanup succeeds, so an interrupted unload
        # does not lose the only handle to its running task.
        await client.stop()
        if clients.get(entry.entry_id) is client:
            clients.pop(entry.entry_id)
    _LOGGER.info('HopHop Casa client unload complete')
    return True
