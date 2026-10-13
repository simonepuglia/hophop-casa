"""Connection diagnostics with no credentials or private URLs."""
from .const import DOMAIN

async def async_get_config_entry_diagnostics(hass, entry):
    client = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return {'version': '1.0.0', 'state': client.state if client else 'unloaded',
            'task_running': bool(client and client.task and not client.task.done()),
            'shutdown_stage': client.shutdown_stage if client else None,
            'shutdown_task_locations': [f'{f.f_code.co_name}:{f.f_lineno}' for f in client.task.get_stack(limit=5)] if client and client.task and not client.task.done() else [],
            'stop_requested': client._stop.is_set() if client else False,
            'reconnects': client.reconnects if client else 0,
            'last_error': client.last_error if client else None,
            'connected_since': client.connected_since if client else None}
