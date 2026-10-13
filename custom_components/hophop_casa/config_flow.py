import hashlib
from urllib.parse import urlsplit
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DEFAULT_WS, DEFAULT_LOCAL_URL


def schema(values=None):
    values=values or {}
    return vol.Schema({vol.Required('device_token',default=values.get('device_token','')):str,
        vol.Required('local_ha_url',default=values.get('local_ha_url',DEFAULT_LOCAL_URL)):str})


def connection_data(user_input, ws_url=DEFAULT_WS):
    """Use the service endpoint automatically and normalize editable values."""
    return {'device_token':user_input['device_token'].strip(),
            'local_ha_url':user_input['local_ha_url'].strip(),
            'ws_url':ws_url}


def validate(data):
    if not data['device_token'].strip(): return 'invalid_token'
    try:
        local=urlsplit(data['local_ha_url'].strip())
    except ValueError:
        return 'invalid_url'
    if local.scheme not in ('http','https') or not local.hostname or local.query or local.username: return 'invalid_url'
    return None


class Flow(config_entries.ConfigFlow,domain='hophop_casa'):
    VERSION=1
    async def async_step_user(self,user_input=None):
        errors={}
        if user_input is not None:
            error=validate(user_input)
            if error: errors['base']=error
            else:
                data=connection_data(user_input)
                await self.async_set_unique_id(hashlib.sha256(data['device_token'].encode()).hexdigest())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title='HopHop Casa',data=data)
        return self.async_show_form(step_id='user',data_schema=schema(user_input),errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Options()


class Options(config_entries.OptionsFlow):
    async def async_step_init(self,user_input=None):
        errors={}
        settings={**self.config_entry.data,**self.config_entry.options}
        if user_input is not None:
            error=validate(user_input)
            if error: errors['base']=error
            else:
                # Preserve any endpoint saved by an earlier version. New entries
                # always use DEFAULT_WS; the endpoint is not an editable field.
                data=connection_data(user_input,settings.get('ws_url',DEFAULT_WS))
                return self.async_create_entry(title='',data=data)
        return self.async_show_form(step_id='init',data_schema=schema(user_input if user_input is not None else settings),errors=errors)
