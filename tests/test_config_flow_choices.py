"""Unit tests verifying setup choices and manual device creation."""
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

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
        self.assertIn("local_udp", res["menu_options"])
        self.assertIn("cloud", res["menu_options"])
        self.assertIn("hybrid", res["menu_options"])
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

    async def test_manual_local_udp_duplicate_mac_aborts(self):
        """Test that adding a manual local UDP device with an already-configured MAC aborts."""
        from datetime import date
        from custom_components.miraie_in.utils import six_months_ago

        hass = MockHass()
        existing_entry = MagicMock()
        existing_entry.data = {
            "device_id": "existing_dev_123",
            "device_mac": "AABBCCDDEEFF",
        }
        existing_entry.options = {}
        hass.config_entries.async_entries = MagicMock(return_value=[existing_entry])

        flow = ConfigFlow()
        flow.hass = hass

        user_input = {
            "name": "Living Room AC",
            "device_ip": "192.168.1.100",
            "device_mac": "AA:BB:CC:DD:EE:FF",
            "udp_control_key": "00" * 32,
            "udp_control_iv": "11" * 16,
            "controller_id": "2",
            "install_date": six_months_ago(date.today()).isoformat(),
        }

        res = await flow.async_step_local_udp_manual(user_input)
        self.assertEqual(res["type"], "abort")
        self.assertEqual(res["reason"], "already_configured")

    async def test_firmware_compatibility_helper(self):
        """Test is_udp_supported_firmware utility."""
        from custom_components.miraie_in.utils import is_udp_supported_firmware

        self.assertTrue(is_udp_supported_firmware("3.02"))
        self.assertTrue(is_udp_supported_firmware("3.0.2"))
        self.assertTrue(is_udp_supported_firmware("v3.02"))
        self.assertTrue(is_udp_supported_firmware("V3.0.2"))
        self.assertTrue(is_udp_supported_firmware("3.1"))
        self.assertTrue(is_udp_supported_firmware("4.0"))
        self.assertFalse(is_udp_supported_firmware("2.15"))
        self.assertFalse(is_udp_supported_firmware("v2.15"))
        self.assertFalse(is_udp_supported_firmware("2.10"))
        self.assertFalse(is_udp_supported_firmware("1.0"))
        self.assertFalse(is_udp_supported_firmware("IR-1.0"))
        self.assertFalse(is_udp_supported_firmware(""))
        self.assertFalse(is_udp_supported_firmware(None))

    async def test_demo_account_discovery(self):
        """Test demo@miraie.in mock discovery returning multiple AC units."""
        from custom_components.miraie_in.config_flow import validate_input

        hass = MockHass()
        info, devices = await validate_input(hass, {"username": "demo@miraie.in", "password": "any"})
        self.assertEqual(info["title"], "MirAIe Demo Account")
        self.assertEqual(len(devices), 4)
        self.assertEqual(devices[0]["firmware_version"], "3.02")
        self.assertEqual(devices[1]["firmware_version"], "2.15")

    async def test_select_devices_step_filtering(self):
        """Test async_step_select_devices form and user selection."""
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow._setup_mode = "cloud"
        flow._discovered_cloud_devices = [
            {"id": "ac_1", "name": "Living Room AC", "model_code": "CS-CU-1"},
            {"id": "ac_2", "name": "Bedroom AC", "model_code": "CS-CU-2"},
        ]

        # 1. Show form
        res_form = await flow.async_step_select_devices()
        self.assertEqual(res_form["type"], "form")
        self.assertEqual(res_form["step_id"], "select_devices")

        # 2. Empty selection error
        res_empty = await flow.async_step_select_devices({"selected_devices": []})
        self.assertEqual(res_empty["type"], "form")
        self.assertEqual(res_empty["errors"]["base"], "no_devices_selected")

        # 3. Select single device and proceed
        from unittest.mock import AsyncMock
        flow.async_step_cloud_devices = AsyncMock(return_value={"type": "form", "step_id": "cloud_devices"})
        res_selected = await flow.async_step_select_devices({"selected_devices": ["ac_1"]})
        self.assertEqual(len(flow._discovered_cloud_devices), 1)
        self.assertEqual(flow._discovered_cloud_devices[0]["id"], "ac_1")

    async def test_local_udp_auto_incompatible_firmware_abort(self):
        """Test local UDP auto setup aborts with incompatible_firmware_udp if devices are < 3.x."""
        from unittest.mock import patch

        flow = ConfigFlow()
        flow.hass = MockHass()
        flow.hass.config_entries.async_entries = MagicMock(return_value=[])

        legacy_devices = [
            {
                "id": "ac_legacy",
                "name": "Legacy AC",
                "model_code": "CS-CU-KN18YKY",
                "firmware_version": "2.15",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
            }
        ]

        with patch("custom_components.miraie_in.config_flow.validate_input", return_value=({"cloud_latency_ms": 10}, legacy_devices)):
            res = await flow.async_step_local_udp_auto({"username": "user@example.com", "password": "pass"})
            self.assertEqual(res["type"], "abort")
            self.assertEqual(res["reason"], "incompatible_firmware_udp")

    async def test_hybrid_devices_requires_ip_for_udp(self):
        """Test that selecting local_udp in hybrid mode without an IP throws an error."""
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow._discovered_cloud_devices = [
            {
                "id": "ac_modern",
                "name": "Modern AC",
                "model_code": "CS-CU-EU18CKY",
                "firmware_version": "3.02",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
                "mac_address": "7C:2C:67:AA:BB:CC",
                "ip_address": "",
            }
        ]
        flow._current_device_index = 0

        from unittest.mock import AsyncMock, patch
        with patch(
            "custom_components.miraie_in.config_flow.async_resolve_device_ip",
            new=AsyncMock(return_value=None),
        ):
            res = await flow.async_step_hybrid_devices({
                "primary_backend": "local_udp",
                "secondary_backend": "cloud",
                "hybrid_submode": "auto",
                "device_ip": "",
            })
        self.assertEqual(res["type"], "form")
        self.assertEqual(res.get("errors", {}).get("device_ip"), "ip_required_for_udp")

    async def test_select_devices_udp_mode_disabled_options_and_capabilities(self):
        """Test async_step_select_devices partitions compatible vs incompatible devices in UDP mode."""
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow._setup_mode = "local_udp"
        flow._discovered_cloud_devices = [
            {
                "id": "ac_modern",
                "name": "Modern AC",
                "model_code": "CS-CU-EU18CKY5XFM",
                "firmware_version": "3.02",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
            },
            {
                "id": "ac_legacy",
                "name": "Legacy AC",
                "model_code": "CS-CU-KN18YKY",
                "firmware_version": "2.15",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
            },
        ]

        # 1. Show form with deterministic model capabilities lookup
        mock_lookup = MagicMock()
        mock_lookup.get_capabilities.return_value = {
            "capacity_class": "1.5T",
            "speed_type": "variable",
            "swing_type": "4-way",
        }
        with patch("custom_components.miraie_in.config_flow.get_model_lookup", return_value=mock_lookup):
            res_form = await flow.async_step_select_devices()
            self.assertEqual(res_form["type"], "form")
            self.assertEqual(res_form["step_id"], "select_devices")
            placeholders = res_form["description_placeholders"]
            self.assertIn("capabilities_overview", placeholders)
            self.assertEqual(placeholders["capabilities_overview"], "")
            self.assertIn("incompatible_overview", placeholders)
            self.assertIn("CS-CU-KN18YKY", placeholders["incompatible_overview"])
            self.assertIn("Incompatible firmware", placeholders["incompatible_overview"])

            # Check schema options: only compatible devices are offered as checkboxes
            schema = res_form["data_schema"]
            options = schema.schema["selected_devices"].config["options"]
            self.assertEqual(len(options), 1)
            self.assertEqual(options[0]["value"], "ac_modern")
            self.assertIn("1.5T Inverter, 4-Way Swing", options[0]["label"])

            # Check default selection only includes modern AC
            marker = next(k for k in schema.schema if k == "selected_devices")
            self.assertEqual(marker.default(), ["ac_modern"])

        # 2. Allow valid submission
        from unittest.mock import AsyncMock
        flow.async_step_local_udp_devices = AsyncMock(return_value={"type": "form", "step_id": "local_udp_devices"})
        res_valid = await flow.async_step_select_devices({"selected_devices": ["ac_modern"]})
        self.assertEqual(len(flow._discovered_cloud_devices), 1)
        self.assertEqual(flow._discovered_cloud_devices[0]["id"], "ac_modern")

    async def test_select_devices_udp_mode_fallback_capabilities(self):
        """Test fallback capabilities formatting when model lookup yields no data."""
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow._setup_mode = "local_udp"
        flow._discovered_cloud_devices = [
            {
                "id": "ac_modern",
                "name": "Modern AC",
                "model_code": "UNKNOWN_MODEL",
                "firmware_version": "3.02",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
            },
        ]

        mock_lookup = MagicMock()
        mock_lookup.get_capabilities.return_value = None
        with patch("custom_components.miraie_in.config_flow.get_model_lookup", return_value=mock_lookup):
            res_form = await flow.async_step_select_devices()
            schema = res_form["data_schema"]
            options = schema.schema["selected_devices"].config["options"]
            self.assertEqual(len(options), 1)
            self.assertIn("Standard, 2-Way Swing", options[0]["label"])

    async def test_demo_account_local_udp_auto_flow(self):
        """Test demo@miraie.in in local_udp_auto correctly renders select_devices with partitioned devices."""
        flow = ConfigFlow()
        flow.hass = MockHass()
        flow.hass.config_entries.async_entries = MagicMock(return_value=[])

        res = await flow.async_step_local_udp_auto({"username": "demo@miraie.in", "password": "any"})
        self.assertEqual(res["type"], "form")
        self.assertEqual(res["step_id"], "select_devices")

        # 2 compatible ACs (Living Room, Study Room) in checkboxes
        schema = res["data_schema"]
        options = schema.schema["selected_devices"].config["options"]
        self.assertEqual(len(options), 2)
        opt_ids = [o["value"] for o in options]
        self.assertIn("mock_living_room_ac", opt_ids)
        self.assertIn("mock_study_room_ac", opt_ids)

        # 2 incompatible ACs in description text
        incomp = res["description_placeholders"]["incompatible_overview"]
        self.assertIn("Master Bedroom AC", incomp)
        self.assertIn("Dining Room AC", incomp)
        self.assertIn("CS-CU-KN18YKY", incomp)

    async def test_local_udp_auto_mixed_devices_preserves_all_for_selection(self):
        """Test local UDP auto setup preserves both compatible and incompatible devices for select_devices."""
        from unittest.mock import patch

        flow = ConfigFlow()
        flow.hass = MockHass()
        flow.hass.config_entries.async_entries = MagicMock(return_value=[])

        mixed_devices = [
            {
                "id": "ac_modern",
                "name": "Modern AC",
                "model_code": "CS-CU-EU18CKY5XFM",
                "firmware_version": "3.02",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
            },
            {
                "id": "ac_legacy",
                "name": "Legacy AC",
                "model_code": "CS-CU-KN18YKY",
                "firmware_version": "2.15",
                "udp_control_key": "00" * 32,
                "udp_control_iv": "11" * 16,
            },
        ]

        with patch("custom_components.miraie_in.config_flow.validate_input", return_value=({"cloud_latency_ms": 10}, mixed_devices)):
            res = await flow.async_step_local_udp_auto({"username": "user@example.com", "password": "pass"})
            self.assertEqual(res["type"], "form")
            self.assertEqual(res["step_id"], "select_devices")
            self.assertEqual(len(flow._discovered_cloud_devices), 2)


if __name__ == "__main__":
    unittest.main()
