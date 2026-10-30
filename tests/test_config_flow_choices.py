"""Unit tests verifying setup choices and manual device creation."""
import unittest
import asyncio
from unittest.mock import MagicMock

from tests.ha_stub import setup_ha_stubs
setup_ha_stubs()

class MockHass:
    def __init__(self):
        self.config_entries = MagicMock()
        self.services = MagicMock()
        self.states = {}

from custom_components.miraie_in.config_flow import ConfigFlow, OptionsFlowHandler



class TestConfigFlowChoices(unittest.IsolatedAsyncioTestCase):
    async def test_setup_menu_choices(self):
        flow = ConfigFlow()
        flow.hass = MockHass()

        res = await flow.async_step_user()
        self.assertEqual(res["type"], "menu")
        self.assertEqual(res["step_id"], "user")
        self.assertIn("cloud_account", res["menu_options"])
        self.assertIn("ir_device", res["menu_options"])

    async def test_ir_device_step_creation(self):
        flow = ConfigFlow()
        flow.hass = MockHass()

        user_input = {
            "name": "Guest Room AC",
            "model_code": "CS-CU-KN18YKY",
            "blaster_entity_id": "infrared.guest_room_ir_blaster",
        }

        res1 = await flow.async_step_ir_device({"name": "Guest Room AC", "model_code": "CS-CU-KN18YKY"})
        self.assertEqual(res1["type"], "form")
        self.assertEqual(res1["step_id"], "feature_confirmation")

        res2 = await flow.async_step_feature_confirmation({"control_mode": "ir", "has_heat_mode": False, "has_nanoe": False, "converti_type": "7-in-1", "h_vane_enabled": True})
        self.assertEqual(res2["type"], "form")
        self.assertEqual(res2["step_id"], "attach_blaster")

        res_final = await flow.async_step_attach_blaster({"blaster_entity_id": "infrared.guest_room_ir_blaster"})
        self.assertEqual(res_final["type"], "create_entry")
        self.assertEqual(res_final["title"], "Guest Room AC (IR Only)")
        self.assertTrue(res_final["data"]["is_ir_only"])
        self.assertEqual(res_final["data"]["model_code"], "CS-CU-KN18YKY")
        self.assertEqual(res_final["options"]["blaster_entity_id"], "infrared.guest_room_ir_blaster")

    async def test_attach_blaster_tasmota_mqtt_success(self):
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow._device_data = {
            "name": "Guest Room AC",
            "model_code": "CS-CU-KN18YKY",
            "control_mode": "ir",
            "capabilities": {},
        }

        res = await flow.async_step_attach_blaster({
            "ir_format": "tasmota",
            "mqtt_topic": "cmnd/tasmota_bedroom/IRsend",
            "availability_topic": "tele/tasmota_bedroom/LWT",
        })
        self.assertEqual(res["type"], "create_entry")
        self.assertEqual(res["options"]["blaster_entity_id"], "cmnd/tasmota_bedroom/IRsend")
        self.assertEqual(res["options"]["mqtt_topic"], "cmnd/tasmota_bedroom/IRsend")
        self.assertEqual(res["options"]["availability_topic"], "tele/tasmota_bedroom/LWT")

    async def test_attach_blaster_tasmota_validation_errors(self):
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow._device_data = {
            "name": "Guest Room AC",
            "model_code": "CS-CU-KN18YKY",
            "control_mode": "ir",
            "capabilities": {},
        }

        # 1. Both endpoints provided
        res1 = await flow.async_step_attach_blaster({
            "ir_format": "tasmota",
            "blaster_entity_id": "remote.bedroom_blaster",
            "mqtt_topic": "cmnd/tasmota_bedroom/IRsend",
        })
        self.assertEqual(res1["type"], "form")
        self.assertEqual(res1["errors"]["base"], "both_endpoints_provided")

        # 2. Neither endpoint provided
        res2 = await flow.async_step_attach_blaster({
            "ir_format": "tasmota",
            "blaster_entity_id": "",
            "mqtt_topic": "",
        })
        self.assertEqual(res2["type"], "form")
        self.assertEqual(res2["errors"]["base"], "missing_tasmota_endpoint")

        # 3. Invalid MQTT topic (no slash or has wildcards)
        res3 = await flow.async_step_attach_blaster({
            "ir_format": "tasmota",
            "mqtt_topic": "invalid_topic_without_slash",
        })
        self.assertEqual(res3["type"], "form")
        self.assertEqual(res3["errors"]["base"], "invalid_mqtt_topic")

        res3b = await flow.async_step_attach_blaster({
            "ir_format": "tasmota",
            "mqtt_topic": "cmnd/+/IRsend",
        })
        self.assertEqual(res3b["type"], "form")
        self.assertEqual(res3b["errors"]["base"], "invalid_mqtt_topic")

        # 4. Invalid availability topic
        res4 = await flow.async_step_attach_blaster({
            "ir_format": "tasmota",
            "mqtt_topic": "cmnd/tasmota/IRsend",
            "availability_topic": "tele/#",
        })
        self.assertEqual(res4["type"], "form")
        self.assertEqual(res4["errors"]["base"], "invalid_availability_topic")

        # 5. Topic not supported for non-tasmota format
        res5 = await flow.async_step_attach_blaster({
            "ir_format": "broadlink",
            "mqtt_topic": "cmnd/tasmota/IRsend",
        })
        self.assertEqual(res5["type"], "form")
        self.assertEqual(res5["errors"]["base"], "topic_not_supported_for_format")

    async def test_options_flow_add_manual_device(self):
        handler = OptionsFlowHandler()
        mock_entry = MagicMock()
        mock_entry.options = {"manual_devices": []}
        handler._config_entry = mock_entry

    async def test_options_flow_remove_ir_blaster(self):
        mock_entry = MagicMock()
        mock_entry.data = {"device_id": "dev123", "device_name": "Living Room AC"}
        mock_entry.options = {
            "blaster_entity_id": "infrared.living_room_blaster",
            "devices": {"dev123": {"blaster_entity_id": "infrared.living_room_blaster"}}
        }
        handler = OptionsFlowHandler(mock_entry)

        from datetime import date, timedelta
        valid_date = (date.today() - timedelta(days=60)).isoformat()
        res = await handler.async_step_device_settings({"blaster_entity_id": None, "install_date": valid_date, "primary_backend": "cloud", "hybrid_submode": "auto"})
        self.assertEqual(res["type"], "create_entry")
        self.assertEqual(res["data"]["blaster_entity_id"], "")
        self.assertEqual(res["data"]["devices"]["dev123"]["blaster_entity_id"], "")


    async def test_validate_input_live_rest_discovery(self):
        from unittest.mock import AsyncMock, patch
        from custom_components.miraie_in.config_flow import validate_input

        hass = MockHass()
        login_data = {"username": "testuser@gmail.com", "password": "password123"}

        homes_response_data = [
            {
                "homeId": "home_123",
                "spaces": [
                    {
                        "spaceId": "space_1",
                        "devices": [
                            {
                                "deviceId": "dev_room_2_ac",
                                "deviceName": "Room 2 AC"
                            }
                        ]
                    }
                ]
            }
        ]

        details_response_data = [
            {
                "deviceId": "dev_room_2_ac",
                "modelNumber": "CS-CU-EU18CKY5XFM",
                "modelName": "Panasonic AC"
            }
        ]

        class MockResponse:
            def __init__(self, json_data, status=200):
                self._json_data = json_data
                self.status = status

            async def json(self):
                return self._json_data

        class MockSession:
            def __init__(self):
                self.closed = False

            async def get(self, url, headers=None):
                if "homeManagement" in url:
                    return MockResponse(homes_response_data)
                elif "deviceManagement" in url:
                    return MockResponse(details_response_data)
                return MockResponse([])

            async def close(self):
                self.closed = True

        mock_session = MockSession()

        with patch("custom_components.miraie_in.config_flow.async_get_clientsession", return_value=mock_session), \
             patch("custom_components.miraie_in.config_flow.MirAIeHub") as mock_hub_class:

            mock_hub = MagicMock()
            mock_hub.http = mock_session
            mock_hub.user = MagicMock()
            mock_hub.user.access_token = "fake_access_token_123"
            mock_hub._authenticate = AsyncMock(return_value=True)

            mock_dev = MagicMock()
            mock_dev.id = "dev_room_2_ac"
            mock_dev.friendly_name = "Room 2 AC"
            mock_dev.details = MagicMock()
            mock_dev.details.model_number = "CS-CU-EU18CKY5XFM"

            mock_home = MagicMock()
            mock_home.devices = [mock_dev]

            async def fake_get_home_details():
                mock_hub.home = mock_home

            mock_hub._get_home_details = AsyncMock(side_effect=fake_get_home_details)
            mock_hub_class.return_value = mock_hub

            info, devices = await validate_input(hass, login_data)

            self.assertEqual(info["title"], "MirAIe Cloud Account")
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["id"], "dev_room_2_ac")
            self.assertEqual(devices[0]["name"], "Room 2 AC")
            self.assertEqual(devices[0]["model_code"], "CS-CU-EU18CKY5XFM")

    async def test_reauth_flow_success(self):
        """Test async_step_reauth and async_step_reauth_confirm."""
        from unittest.mock import AsyncMock, patch
        hass = MockHass()
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_123"
        mock_entry.data = {"username": "old_user@example.com", "password": "old_password"}
        hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
        hass.config_entries.async_update_entry = MagicMock()
        hass.config_entries.async_reload = AsyncMock(return_value=True)

        flow = ConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "test_entry_123"}

        # Step 1: Trigger reauth
        res_reauth = await flow.async_step_reauth(mock_entry.data)
        self.assertEqual(res_reauth["type"], "form")
        self.assertEqual(res_reauth["step_id"], "reauth_confirm")

        # Step 2: Confirm reauth with validated new password
        with patch("custom_components.miraie_in.config_flow.validate_input", new_callable=AsyncMock) as mock_validate:
            mock_validate.return_value = ({"title": "MirAIe"}, [{"id": "dev1"}])
            res_confirm = await flow.async_step_reauth_confirm({
                "username": "new_user@example.com",
                "password": "new_password"
            })
            self.assertEqual(res_confirm["type"], "abort")
            self.assertEqual(res_confirm["reason"], "reauth_successful")
            hass.config_entries.async_update_entry.assert_called_once()
            hass.config_entries.async_reload.assert_called_once_with("test_entry_123")


if __name__ == "__main__":
    unittest.main()
