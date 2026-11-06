"""Unit tests for MirAIe config flow and per-device options flow forms."""

import unittest
import asyncio
import importlib
from unittest.mock import MagicMock
import tests.ha_stub


class TestConfigFlowDevices(unittest.TestCase):
    """Test options flow device listing, __all__ overrides, and error validation."""

    def setUp(self):
        self.mod_flow = importlib.import_module("custom_components.miraie_in.config_flow")

    async def async_test_options_flow_init_menu_render(self):
        """Test async_step_init menu rendering."""
        from tests.fixtures import MockConfigEntry, MockDevice

        dev1 = MockDevice(device_id="dev_living", friendly_name="Living Room AC")
        entry = MockConfigEntry(devices=[dev1])

        flow = self.mod_flow.OptionsFlowHandler()
        flow.config_entry = entry

        res = await flow.async_step_init(user_input=None)
        self.assertEqual(res["type"], "form")
        self.assertEqual(res["step_id"], "device_settings")

    async def async_test_options_flow_device_settings_form_render(self):
        """Test async_step_device_settings form rendering when user_input is None."""
        from tests.fixtures import MockConfigEntry, MockDevice

        dev1 = MockDevice(device_id="dev_living", friendly_name="Living Room AC")
        entry = MockConfigEntry(devices=[dev1], data={"device_id": "dev_living"})

        flow = self.mod_flow.OptionsFlowHandler()
        flow.config_entry = entry

        res = await flow.async_step_device_settings(user_input=None)
        self.assertEqual(res["type"], "form")
        self.assertEqual(res["step_id"], "device_settings")

    async def async_test_options_flow_device_settings_save(self):
        """Test async_step_device_settings successfully saves options."""
        from datetime import date, timedelta
        from tests.fixtures import MockConfigEntry, MockDevice

        target_date = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
        dev1 = MockDevice(device_id="dev1")
        entry = MockConfigEntry(
            options={"install_date": "2026-01-01", "devices": {"dev1": {"install_date": "2026-02-01"}}},
            devices=[dev1],
            data={"device_id": "dev1"},
        )

        flow = self.mod_flow.OptionsFlowHandler()
        flow.config_entry = entry

        res = await flow.async_step_device_settings(user_input={"install_date": target_date})
        self.assertEqual(res["type"], "create_entry")
        self.assertEqual(res["data"]["install_date"], target_date)
        self.assertEqual(res["data"]["devices"]["dev1"]["install_date"], target_date)

    async def async_test_options_flow_invalid_date_error_redisplay(self):
        """Test invalid install date returns form with error."""
        from tests.fixtures import MockConfigEntry, MockDevice

        dev1 = MockDevice(device_id="dev1")
        entry = MockConfigEntry(devices=[dev1], data={"device_id": "dev1"})

        flow = self.mod_flow.OptionsFlowHandler()
        flow.config_entry = entry

        res = await flow.async_step_device_settings(user_input={"install_date": "invalid-date"})
        self.assertEqual(res["type"], "form")
        self.assertIn("errors", res)
        self.assertEqual(res["errors"]["install_date"], "invalid_install_date")


    async def async_test_cloud_devices_multi_device_discovery(self):
        """Test multi-device cloud discovery triggers flow.async_init for non-last devices."""
        flow = self.mod_flow.ConfigFlow()
        flow.hass = MagicMock()
        async def mock_async_init(*args, **kwargs):
            return {"type": "create_entry"}
        flow.hass.config_entries.flow.async_init = MagicMock(side_effect=mock_async_init)
        flow.hass.async_create_task = MagicMock(side_effect=lambda coroutine: asyncio.create_task(coroutine))

        flow._discovered_cloud_devices = [
            {"id": "dev_living", "name": "Living Room AC", "model_code": "CS-CU-RU18CKY"},
            {"id": "dev_bed", "name": "Bedroom AC", "model_code": "CS-CU-KN18YKY"},
            {"id": "dev_guest", "name": "Guest Room AC", "model_code": "CS-CU-RU18CKY"},
        ]
        flow._cloud_credentials = {"username": "test@example.com", "password": "password123"}
        flow._current_device_index = 0

        # Step 1: Device 1 config
        res1 = await flow.async_step_cloud_devices(user_input={"name": "Living Room AC", "blaster_entity_id": "", "primary_backend": "cloud"})
        self.assertEqual(res1["type"], "form")
        self.assertEqual(flow._current_device_index, 1)

        # Step 2: Device 2 config
        res2 = await flow.async_step_cloud_devices(user_input={"name": "Bedroom AC", "blaster_entity_id": "remote.bed_blaster", "primary_backend": "ir"})
        self.assertEqual(res2["type"], "form")
        self.assertEqual(flow._current_device_index, 2)

        # Step 3: Device 3 config (Last device creates entry and spawns flow.async_init for previous ones)
        res3 = await flow.async_step_cloud_devices(user_input={"name": "Guest Room AC", "blaster_entity_id": "", "primary_backend": "cloud"})
        self.assertEqual(res3["type"], "create_entry")
        self.assertEqual(res3["title"], "Guest Room AC")

        # Verify flow.async_init was called for the first 2 devices
        self.assertEqual(flow.hass.config_entries.flow.async_init.call_count, 2)
        call_args_list = flow.hass.config_entries.flow.async_init.call_args_list
        self.assertEqual(call_args_list[0][0][0], "miraie_in")
        from homeassistant import config_entries
        self.assertEqual(call_args_list[0][1]["context"]["source"], config_entries.SOURCE_IMPORT)
        self.assertEqual(call_args_list[0][1]["data"]["device_id"], "dev_living")
        self.assertEqual(call_args_list[1][1]["data"]["device_id"], "dev_bed")
        self.assertEqual(call_args_list[1][1]["data"]["options"]["blaster_entity_id"], "remote.bed_blaster")

    async def async_test_feature_confirmation_missing_model_code_safe_default(self):
        """Test feature confirmation with missing model_code routes to safe_default."""
        flow = self.mod_flow.ConfigFlow()
        flow.hass = MagicMock()
        flow._device_data = {"name": "Test AC", "model_code": ""}

        res = await flow.async_step_feature_confirmation(user_input=None)
        self.assertEqual(res["type"], "form")
        self.assertEqual(res["step_id"], "feature_confirmation")
        self.assertIn("double-check", res["description_placeholders"]["resolved_via"])

    async def async_test_cloud_login_no_devices_aborts(self):
        """Test cloud login on account with 0 discovered devices aborts cleanly with no_devices_found."""
        flow = self.mod_flow.ConfigFlow()
        flow.hass = MagicMock()
        flow._async_current_entries = MagicMock(return_value=[])

        from unittest.mock import patch, AsyncMock
        with patch("custom_components.miraie_in.config_flow.validate_input", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = ({"title": "MirAIe Cloud Account"}, [])
            res = await flow.async_step_cloud_account({"username": "empty@example.com", "password": "password"})
            self.assertEqual(res["type"], "abort")
            self.assertEqual(res["reason"], "no_devices_found")

    def test_run_async_tests(self):
        """Runner for async test methods."""
        asyncio.run(self.async_test_options_flow_init_menu_render())
        asyncio.run(self.async_test_options_flow_device_settings_form_render())
        asyncio.run(self.async_test_options_flow_device_settings_save())
        asyncio.run(self.async_test_options_flow_invalid_date_error_redisplay())
        asyncio.run(self.async_test_cloud_devices_multi_device_discovery())
        asyncio.run(self.async_test_feature_confirmation_missing_model_code_safe_default())
        asyncio.run(self.async_test_cloud_login_no_devices_aborts())



if __name__ == "__main__":
    unittest.main()
