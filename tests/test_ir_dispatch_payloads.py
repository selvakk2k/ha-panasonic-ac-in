"""Test multi-format IR payload transmission fallback in coordinator.py."""
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from tests.ha_stub import setup_ha_stubs
setup_ha_stubs()


class TestIRDispatchPayloads(unittest.IsolatedAsyncioTestCase):
    async def test_remote_send_command_multi_fallback(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        service_calls = []

        async def mock_call_service(domain, service, service_data, blocking=True):
            service_calls.append((domain, service, service_data, blocking))
            # Fail the first attempt (b64 with prefix) to test fallback
            if len(service_calls) == 1:
                raise ValueError("Broadlink prefix not supported on this entity")
            return True

        hass.services.async_call = AsyncMock(side_effect=mock_call_service)

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="remote.bedroom_ir_blaster",
        )

        res = await coord.async_dispatch_ir_command(
            mode="cool",
            target_temp=24,
            fan="low",
            v_vane="V1",
        )

        self.assertTrue(res)
        self.assertEqual(len(service_calls), 2)
        # Verify 1st attempt during discovery used blocking=True
        self.assertTrue(service_calls[0][3], "Auto-detect attempt should use blocking=True")
        # Verify 2nd attempt during discovery used blocking=True
        self.assertTrue(service_calls[1][3], "Auto-detect attempt should use blocking=True")
        # Verify 2nd attempt transmitted raw b64 payload in a list
        domain, service, data, blocking = service_calls[1]
        self.assertEqual(domain, "remote")
        self.assertEqual(service, "send_command")
        self.assertEqual(data["entity_id"], "remote.bedroom_ir_blaster")
        self.assertIsInstance(data["command"], list)

        # Now test that cached format uses blocking=False for zero-latency fire-and-forget
        res2 = await coord.async_dispatch_ir_command(
            mode="cool",
            target_temp=25,
            fan="low",
            v_vane="V1",
        )
        self.assertTrue(res2)
        self.assertEqual(len(service_calls), 3)
        self.assertFalse(service_calls[2][3], "Cached format transmission should use blocking=False")

    async def test_eco_mode_hardware_ir_dispatch(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        service_calls = []

        async def mock_call_service(domain, service, service_data, blocking=True):
            service_calls.append((domain, service, service_data))
            return True

        hass.services.async_call = AsyncMock(side_effect=mock_call_service)

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="remote.bedroom_ir_blaster",
        )

        with patch("custom_components.miraie_in.coordinator.generate_ir_code") as mock_gen:
            mock_gen.return_value = {
                "raw": [-100, 100],
                "ahea_hex": "0x0220E004000000060220E00400393480A10D000EE0000889000020",
                "description": "EU Series | COOL 26°C (Fan: auto, V-Vane: V1, H-Vane: Mirrored [Single-Vane], ECO: ON, NANOE: OFF)",
                "broadlink_b64": "b64test",
                "tuya_b64": "tuyatest",
                "tasmota_json": "{}",
            }
            res = await coord.async_dispatch_ir_command(
                mode="cool",
                target_temp=24,
                fan="auto",
                v_vane="V1",
                h_vane="H0",
                eco=True,
            )
            self.assertTrue(res)
            mock_gen.assert_called_with(
                mode="cool",
                target_temp=24,
                fan="auto",
                v_vane="V1",
                h_vane="H0",
                eco=True,
                nanoe=False,
                series="RU",
            )
            self.assertEqual(coord.state["eco"], True)
            self.assertEqual(coord.state["temperature"], 26)

    async def test_tasmota_direct_mqtt_dispatch(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        service_calls = []

        async def mock_call_service(domain, service, service_data, blocking=True):
            service_calls.append((domain, service, service_data, blocking))
            return True

        hass.services.async_call = AsyncMock(side_effect=mock_call_service)

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="cmnd/tasmota_bedroom/IRsend",
            ir_format="tasmota",
        )

        with patch("custom_components.miraie_in.coordinator.generate_ir_code") as mock_gen:
            mock_gen.return_value = {
                "raw": [-100, 100],
                "ahea_hex": "0x0220E004000000060220E00400393480A10D000EE0000889000020",
                "description": "EU Series | COOL 24°C",
                "broadlink_b64": "b64test",
                "tuya_b64": "tuyatest",
                "tasmota_json": '{"Vendor":"Panasonic","Power":"On"}',
            }
            res = await coord.async_dispatch_ir_command(
                mode="cool",
                target_temp=24,
                fan="low",
                v_vane="V1",
            )
            self.assertTrue(res)
            self.assertEqual(len(service_calls), 1)
            domain, service, data, blocking = service_calls[0]
            self.assertEqual(domain, "mqtt")
            self.assertEqual(service, "publish")
            self.assertEqual(data["topic"], "cmnd/tasmota_bedroom/IRsend")
            self.assertEqual(data["payload"], '{"Vendor":"Panasonic","Power":"On"}')

    async def test_tasmota_remote_entity_dispatch(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        service_calls = []

        async def mock_call_service(domain, service, service_data, blocking=True):
            service_calls.append((domain, service, service_data, blocking))
            return True

        hass.services.async_call = AsyncMock(side_effect=mock_call_service)

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="remote.bedroom_ir_blaster",
            ir_format="tasmota",
        )

        with patch("custom_components.miraie_in.coordinator.generate_ir_code") as mock_gen:
            mock_gen.return_value = {
                "raw": [-100, 100],
                "aeha_hex": "0x0220E004000000060220E00400393480A10D000EE0000889000020",
                "description": "EU Series | COOL 24°C",
                "broadlink_b64": "b64test",
                "tuya_b64": "tuyatest",
                "tasmota_json": '{"Vendor":"Panasonic","Power":"On"}',
            }
            res = await coord.async_dispatch_ir_command(
                mode="cool",
                target_temp=24,
                fan="low",
                v_vane="V1",
            )
            self.assertTrue(res)
            self.assertEqual(len(service_calls), 1)
            domain, service, data, blocking = service_calls[0]
            self.assertEqual(domain, "remote")
            self.assertEqual(service, "send_command")
            self.assertEqual(data["entity_id"], "remote.bedroom_ir_blaster")
            self.assertEqual(data["command"], ["0x0220E004000000060220E00400393480A10D000EE0000889000020"])

    async def test_tasmota_lwt_availability(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        hass.states = {}

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="cmnd/tasmota_ir/IRsend",
            availability_topic="tele/tasmota_ir/LWT",
            ir_format="tasmota",
        )

        # Initially, blaster_entity_id is an MQTT topic, and coord.ir_blaster_available is True
        self.assertTrue(coord.is_ir_blaster_available)

        # Subscribe to LWT
        await coord._async_subscribe_availability_topic()

        # Send offline message
        class MockMsg:
            def __init__(self, payload):
                self.payload = payload

        coord._async_mqtt_lwt_message(MockMsg("offline"))
        self.assertFalse(coord.is_ir_blaster_available)

        # Send online message
        coord._async_resync_on_reconnect = AsyncMock()
        coord._async_mqtt_lwt_message(MockMsg("online"))
        self.assertTrue(coord.is_ir_blaster_available)
        coord._async_resync_on_reconnect.assert_called_once()
        await asyncio.sleep(0)

    def test_invalid_emitter_and_topic_parsing(self):
        from custom_components.miraie_in.utils import is_mqtt_topic, emitter_available

        hass = MagicMock()
        # Slashless, dotless string is neither a topic nor an entity
        self.assertFalse(is_mqtt_topic("remote_bedroom"))
        self.assertTrue(is_mqtt_topic("cmnd/tasmota/IRsend"))
        self.assertFalse(emitter_available(hass, "remote_bedroom"))
        self.assertFalse(emitter_available(hass, "cmnd/tasmota/IRsend", lwt_online=False))

    async def test_lwt_offline_precedence_over_entity_state(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        power_state = MagicMock(state="on")
        hass.states = {"binary_sensor.ir_node_power": power_state}

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="cmnd/tasmota_ir/IRsend",
            availability_topic="tele/tasmota_ir/LWT",
            availability_entity_id="binary_sensor.ir_node_power",
        )

        class MockMsg:
            def __init__(self, payload):
                self.payload = payload

        # LWT goes offline
        coord._async_mqtt_lwt_message(MockMsg("offline"))
        self.assertFalse(coord.is_ir_blaster_available)

        # Availability entity receives an update to 'on'
        mock_event = MagicMock()
        mock_event.data = {
            "old_state": MagicMock(state="off"),
            "new_state": MagicMock(state="on"),
        }
        await coord._async_availability_state_changed(mock_event)

        # Because LWT is offline, coordinator MUST remain unavailable
        self.assertFalse(coord.is_ir_blaster_available)

    async def test_remote_entity_with_availability_topic_resync(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        remote_st = MagicMock(state="on")
        hass.states = {"remote.bedroom_ir_blaster": remote_st}
        loop = asyncio.get_running_loop()
        hass.async_create_task = MagicMock(side_effect=lambda coro: loop.create_task(coro))

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="remote.bedroom_ir_blaster",
            availability_topic="tele/tasmota_ir/LWT",
        )

        coord._async_resync_on_reconnect = AsyncMock()

        class MockMsg:
            def __init__(self, payload):
                self.payload = payload

        # Retained initial online message when _lwt_online was None should NOT trigger resync
        coord._async_mqtt_lwt_message(MockMsg("online"))
        self.assertTrue(coord.is_ir_blaster_available)
        coord._async_resync_on_reconnect.assert_not_called()

        # Offline message makes blaster unavailable
        coord._async_mqtt_lwt_message(MockMsg("offline"))
        self.assertFalse(coord.is_ir_blaster_available)

        # Now transition from offline -> online triggers resync
        coord._async_mqtt_lwt_message(MockMsg("online"))
        self.assertTrue(coord.is_ir_blaster_available)
        coord._async_resync_on_reconnect.assert_called_once()
        await asyncio.sleep(0)

    async def test_async_unload_cancels_and_unsubscribes_late_lwt(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        hass = MagicMock()
        hass.states = {}

        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub1",
            device_id="dev1",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="cmnd/tasmota_ir/IRsend",
            availability_topic="tele/tasmota_ir/LWT",
        )

        unsub_called = []
        mock_unsub = MagicMock(side_effect=lambda: unsub_called.append(True))

        async def late_subscribe(*args, **kwargs):
            return mock_unsub

        # Unload coordinator before subscription resolves
        coord.async_unload()
        self.assertTrue(coord._unloaded)

        with patch("homeassistant.components.mqtt.async_subscribe", side_effect=late_subscribe):
            await coord._async_subscribe_availability_topic()

        # Because _unloaded is True, the unsub was called immediately without polluting _unsub_event_bus
        self.assertEqual(len(unsub_called), 1)
        self.assertEqual(len(coord._unsub_event_bus), 0)


if __name__ == "__main__":
    unittest.main()
