"""Exercise setup data and endpoint preservation with real Voluptuous schemas.

Home Assistant flow return methods are stubbed; no network connection is made.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class FakeFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


location = Path(__file__).parents[1] / "custom_components/hophop_casa"
package_spec = importlib.util.spec_from_file_location(
    "hophop_setup_tests", location / "__init__.py",
    submodule_search_locations=[str(location)],
)
integration = importlib.util.module_from_spec(package_spec)
sys.modules[package_spec.name] = integration
package_spec.loader.exec_module(integration)

ha = ModuleType("homeassistant")
entries = ModuleType("homeassistant.config_entries")
entries.ConfigFlow = FakeFlow
entries.OptionsFlow = FakeFlow
ha.config_entries = entries
core = ModuleType("homeassistant.core")
core.callback = lambda function: function
flow_spec = importlib.util.spec_from_file_location(
    "hophop_setup_tests.config_flow", location / "config_flow.py",
)
flow_module = importlib.util.module_from_spec(flow_spec)
with patch.dict(sys.modules, {"homeassistant": ha,
                             "homeassistant.config_entries": entries,
                             "homeassistant.core": core}):
    flow_spec.loader.exec_module(flow_module)

DEFAULT_WS = "wss://hophop.casa/ws"
LOCAL_URL = "http://127.0.0.1:8123"


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_form_only_asks_for_token_and_local_url(self):
        result = await flow_module.Flow().async_step_user()
        self.assertEqual({key.schema for key in result["data_schema"].schema},
                         {"device_token", "local_ha_url"})
        self.assertEqual(result["data_schema"]({})["local_ha_url"], LOCAL_URL)

    async def test_new_entry_uses_default_broker_and_normalizes_values(self):
        flow = flow_module.Flow()
        submitted = {"device_token": " token ", "local_ha_url": " " + LOCAL_URL + " "}
        result = await flow.async_step_user(submitted)
        self.assertEqual(result["data"], {"device_token": "token",
                                         "local_ha_url": LOCAL_URL, "ws_url": DEFAULT_WS})
        self.assertEqual(result["title"], "HopHop Casa")
        self.assertEqual(flow.unique_id, hashlib.sha256(b"token").hexdigest())
        self.assertEqual(submitted["device_token"], " token ")

    async def test_invalid_inputs_keep_form_values(self):
        for token, local_url, error in [(" ", LOCAL_URL, "invalid_token"),
                                        ("token", "ftp://localhost", "invalid_url"),
                                        ("token", "http://[broken", "invalid_url")]:
            with self.subTest(error=error, local_url=local_url):
                submitted = {"device_token": token, "local_ha_url": local_url}
                result = await flow_module.Flow().async_step_user(submitted)
                self.assertEqual(result["errors"], {"base": error})
                self.assertEqual(result["data_schema"]({}), submitted)

    async def test_options_preserve_previously_saved_endpoint(self):
        options = flow_module.Options()
        options.config_entry = SimpleNamespace(
            data={"device_token": "old", "local_ha_url": LOCAL_URL,
                  "ws_url": "wss://old.example/ws"},
            options={"ws_url": "wss://custom.example/ws"},
        )
        form = await options.async_step_init()
        self.assertEqual({key.schema for key in form["data_schema"].schema},
                         {"device_token", "local_ha_url"})
        result = await options.async_step_init({"device_token": " new ",
                                               "local_ha_url": LOCAL_URL})
        self.assertEqual(result["data"]["ws_url"], "wss://custom.example/ws")
        self.assertEqual(result["data"]["device_token"], "new")

    async def test_options_default_when_entry_has_no_endpoint(self):
        options = flow_module.Options()
        options.config_entry = SimpleNamespace(data={}, options={})
        result = await options.async_step_init({"device_token": "token",
                                               "local_ha_url": LOCAL_URL})
        self.assertEqual(result["data"]["ws_url"], DEFAULT_WS)

    async def test_options_invalid_input_keeps_user_values(self):
        options = flow_module.Options()
        options.config_entry = SimpleNamespace(
            data={"device_token": "old", "local_ha_url": LOCAL_URL}, options={})
        result = await options.async_step_init({"device_token": "new", "local_ha_url": "invalid"})
        self.assertEqual(result["errors"], {"base": "invalid_url"})
        self.assertEqual(result["data_schema"]({})["device_token"], "new")

    async def test_setup_defaults_and_respects_saved_endpoint(self):
        for stored, expected in [({}, DEFAULT_WS),
                                  ({"ws_url": "wss://custom.example/ws"}, "wss://custom.example/ws")]:
            with self.subTest(stored=stored):
                client = SimpleNamespace(start=AsyncMock())
                hass = SimpleNamespace(data={})
                entry = SimpleNamespace(
                    data={"device_token": "token", **stored}, options={}, entry_id="test",
                    async_on_unload=lambda callback: None,
                    add_update_listener=lambda listener: lambda: None,
                )
                with patch.object(integration, "RemoteClient", return_value=client) as constructor:
                    self.assertTrue(await integration.async_setup_entry(hass, entry))
                constructor.assert_called_once_with(hass, expected, "token", LOCAL_URL)
                self.assertIs(hass.data["hophop_casa"]["test"], client)
