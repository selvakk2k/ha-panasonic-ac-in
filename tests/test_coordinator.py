import unittest
from pathlib import Path
import sys

# Ensure ha_stub is loaded
LOCAL_MIRAIE_AC = Path("/home/skk/Documents/GitHub/miraie-ac")
LOCAL_PANASONIC_MODELS = Path("/home/skk/Documents/GitHub/panasonic-ac-models")

if str(LOCAL_MIRAIE_AC) not in sys.path and LOCAL_MIRAIE_AC.exists():
    sys.path.insert(0, str(LOCAL_MIRAIE_AC))

if str(LOCAL_PANASONIC_MODELS) not in sys.path and LOCAL_PANASONIC_MODELS.exists():
    sys.path.insert(0, str(LOCAL_PANASONIC_MODELS))

from tests.ha_stub import setup_ha_stubs
setup_ha_stubs()

from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

class MockServiceRegistry:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, service_data, blocking=True):
        self.calls.append({
            "domain": domain,
            "service": service,
            "service_data": service_data,
        })
        return True

class MockHass:
    def __init__(self):
        self.services = MockServiceRegistry()
        self.states = {}

class TestCoordinatorIRDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_infrared_domain_dispatch(self):
        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub_123",
            device_id="dev_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="infrared.living_room_ir_transmitter"
        )

        success = await coord.async_dispatch_ir_command(mode="cool", target_temp=24, fan="low")
        self.assertTrue(success)

        # Test remote domain service call
        coord_remote = MirAIeDeviceCoordinator(
            hass=hass,
            subentry_id="sub_123",
            device_id="dev_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            blaster_entity_id="remote.living_room_blaster"
        )
        success_remote = await coord_remote.async_dispatch_ir_command(mode="cool", target_temp=24, fan="low")
        self.assertTrue(success_remote)
        self.assertEqual(len(hass.services.calls), 1)
        call = hass.services.calls[0]
        self.assertEqual(call["domain"], "remote")
        self.assertEqual(call["service"], "send_command")
        self.assertEqual(call["service_data"]["entity_id"], "remote.living_room_blaster")

    async def test_cloud_update_grace_window_power_and_display(self):
        import asyncio
        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_123",
            device_id="dev_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=True,
            primary_backend="ir",
            blaster_entity_id="remote.living_room_blaster",
        )

        # Optimistically turn power ON and display ON
        coord.async_optimistic_update(mode="cool", target_temp=24, display=True, origin="IR")
        coord._last_ir_command_timestamp = asyncio.get_event_loop().time()
        self.assertEqual(coord.state["power"], "on")
        self.assertEqual(coord.state["display"], "on")

        # Stale cloud update arriving within grace window reflecting old "off" states
        await coord.async_handle_cloud_update({"pwr": "off", "acdc": "off", "tset": 26})

        # Grace window must protect power and display and temp from being overwritten by stale payload
        self.assertEqual(coord.state["power"], "on")
        self.assertEqual(coord.state["display"], "on")
        self.assertEqual(coord.state["temperature"], 24)

        # Simulate expiration of grace window (> 8.0s ago)
        coord._last_ir_command_timestamp = asyncio.get_event_loop().time() - 10.0
        await coord.async_handle_cloud_update({"pwr": "off", "acdc": "off"})
        self.assertEqual(coord.state["power"], "off")
        self.assertEqual(coord.state["display"], "off")

    async def test_blaster_reconnect_resync_success(self):
        import time
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
        )
        coord._is_esphome_blaster = True

        # 1. Dispatch command (Cool 22C, Fan Low)
        with patch.object(coord, "async_dispatch_ir_command", wraps=coord.async_dispatch_ir_command):
            await coord.async_dispatch_ir_command(mode="cool", target_temp=22, fan="low", origin="HA UI")
            self.assertEqual(coord._last_ir_command_source, "HA UI")
            self.assertEqual(coord._last_requested_ir_params["target_temp"], 22)
            self.assertEqual(coord._last_requested_ir_params["mode"], "cool")

        # 2. Simulate blaster reconnect event (unavailable -> available)
        event = MagicMock(spec=Event)
        event.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch, patch(
            "custom_components.miraie_in.coordinator.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            mock_dispatch.return_value = True
            await coord._async_blaster_state_changed(event)

            mock_sleep.assert_called_once_with(0.3)
            mock_dispatch.assert_called_once()
            call_kwargs = mock_dispatch.call_args[1]
            self.assertEqual(call_kwargs["target_temp"], 22)
            self.assertEqual(call_kwargs["mode"], "cool")
            self.assertEqual(call_kwargs["origin"], "Blaster Reconnect Resync")
            # Action is consumed
            self.assertIsNone(coord._last_requested_ir_params)

    async def test_blaster_reconnect_ttl_discard(self):
        import time
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
        )

        # Simulate command issued 200 seconds ago (> 180s TTL)
        coord._last_ir_command_source = "HA UI"
        coord._last_ir_command_timestamp = time.monotonic() - 200.0
        coord._last_requested_ir_params = {"mode": "cool", "target_temp": 20}

        event = MagicMock(spec=Event)
        event.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch:
            await coord._async_blaster_state_changed(event)
            mock_dispatch.assert_not_called()

    async def test_blaster_reconnect_physical_remote_precedence(self):
        import time
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
            receiver_entity_id="infrared.living_room_receiver",
        )

        # 1. HA command issued
        await coord.async_dispatch_ir_command(mode="cool", target_temp=22, origin="HA UI")
        self.assertIsNotNone(coord._last_requested_ir_params)

        # 2. Physical remote decoded
        coord._apply_decoded_ir_state({
            "packet_type": "full_frame",
            "power": "on",
            "mode": "cool",
            "temperature": 26,
            "fan_speed": "auto",
        })
        self.assertEqual(coord._last_ir_command_source, "IR Remote")
        self.assertIsNone(coord._last_requested_ir_params)

        # 3. Blaster reconnects
        event = MagicMock(spec=Event)
        event.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch:
            await coord._async_blaster_state_changed(event)
            mock_dispatch.assert_not_called()

    async def test_blaster_reconnect_ha_startup_no_spurious_transmission(self):
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
        )

        self.assertEqual(coord._last_ir_command_source, "Init")
        self.assertIsNone(coord._last_requested_ir_params)

        event = MagicMock(spec=Event)
        event.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch:
            await coord._async_blaster_state_changed(event)
            mock_dispatch.assert_not_called()


    async def test_blaster_reconnect_flapping_no_rearm(self):
        """Verify that connection flapping (multiple reconnects) does not recursively re-arm resync."""
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
        )
        coord._is_esphome_blaster = False

        # 1. Dispatch initial command (Cool 22C)
        await coord.async_dispatch_ir_command(mode="cool", target_temp=22, origin="HA UI")
        self.assertEqual(coord._last_ir_command_source, "HA UI")
        self.assertIsNotNone(coord._last_requested_ir_params)

        # 2. First reconnect event (unavailable -> available)
        event1 = MagicMock(spec=Event)
        event1.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        # Trigger first resync (runs actual async_dispatch_ir_command with is_resync=True)
        await coord._async_blaster_state_changed(event1)

        # Verify that after first resync, pending params is None and source is stamped as resync
        self.assertIsNone(coord._last_requested_ir_params)
        self.assertEqual(coord._last_ir_command_source, "Blaster Reconnect Resync")

        # 3. Flapping: Second reconnect event 30 seconds later (unavailable -> available)
        event2 = MagicMock(spec=Event)
        event2.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch2:
            await coord._async_blaster_state_changed(event2)
            # Second flap must NOT trigger any new IR dispatch!
            mock_dispatch2.assert_not_called()

    async def test_blaster_reconnect_toggle_display_not_resynced(self):
        """Verify that hardware toggle commands like display LED are never retransmitted on blaster reconnect."""
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
        )
        coord._is_esphome_blaster = False

        # 1. Dispatch display toggle command
        await coord.async_dispatch_ir_command(mode="display", origin="HA UI")
        self.assertEqual(coord._last_ir_command_source, "HA UI")
        # Ensure toggle commands are never stored in _last_requested_ir_params
        self.assertIsNone(coord._last_requested_ir_params)

        # 2. Simulate blaster reconnect event
        event = MagicMock(spec=Event)
        event.data = {
            "old_state": MagicMock(state="unavailable"),
            "new_state": MagicMock(state="available"),
        }

        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch:
            await coord._async_blaster_state_changed(event)
            mock_dispatch.assert_not_called()

        # 3. Defense-in-depth: even if _last_requested_ir_params somehow contains mode="display",
        # the reconnect handler's guard must reject it.
        coord._last_requested_ir_params = {"mode": "display"}
        with patch.object(coord, "async_dispatch_ir_command", new_callable=AsyncMock) as mock_dispatch:
            await coord._async_blaster_state_changed(event)
            mock_dispatch.assert_not_called()


    async def test_esphome_version_detection(self):
        """Test detection of ESPHome 2026.10+ firmware versions."""
        from unittest.mock import patch, MagicMock
        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
        )
        with patch("homeassistant.helpers.entity_registry.async_get") as mock_er_fn, \
             patch("homeassistant.helpers.device_registry.async_get") as mock_dr_fn:
            mock_ent = MagicMock()
            mock_ent.device_id = "device_123"
            mock_ent.platform = "esphome"
            mock_dev = MagicMock()
            mock_er_fn.return_value.async_get = MagicMock(return_value=mock_ent)
            mock_dr_fn.return_value.async_get = MagicMock(return_value=mock_dev)

            # 2026.9.1 -> False
            mock_dev.sw_version = "2026.9.1 (2026-10-04)"
            self.assertFalse(coord.is_esphome_2026_10_or_newer)

            # 2026.10.0b1 -> True
            mock_dev.sw_version = "2026.10.0b1"
            self.assertTrue(coord.is_esphome_2026_10_or_newer)

            # 2026.10.0 -> True
            mock_dev.sw_version = "2026.10.0"
            self.assertTrue(coord.is_esphome_2026_10_or_newer)

            # 2026.11.0 -> True
            mock_dev.sw_version = "2026.11.0"
            self.assertTrue(coord.is_esphome_2026_10_or_newer)

            # 2025.12.4 -> False
            mock_dev.sw_version = "2025.12.4"
            self.assertFalse(coord.is_esphome_2026_10_or_newer)

    async def test_availability_sensor_and_device_tracker(self):
        """Test availability logic for switches, device trackers, and binary sensors."""
        from unittest.mock import MagicMock
        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
            availability_entity_id="switch.blaster_smart_plug",
        )
        hass.states["infrared.living_room_blaster"] = MagicMock(state="available")

        # Smart plug switch: 'on' -> available, 'off' -> unavailable
        hass.states["switch.blaster_smart_plug"] = MagicMock(state="on")
        self.assertTrue(coord.is_blaster_available_by_sensor)
        self.assertTrue(coord.is_ir_blaster_available)

        hass.states["switch.blaster_smart_plug"] = MagicMock(state="off")
        self.assertFalse(coord.is_blaster_available_by_sensor)
        self.assertFalse(coord.is_ir_blaster_available)

        # Device tracker (router): 'home' -> available, 'not_home' -> unavailable
        coord.availability_entity_id = "device_tracker.ir_blaster"
        hass.states["device_tracker.ir_blaster"] = MagicMock(state="home")
        self.assertTrue(coord.is_blaster_available_by_sensor)
        self.assertTrue(coord.is_ir_blaster_available)

        hass.states["device_tracker.ir_blaster"] = MagicMock(state="not_home")
        self.assertFalse(coord.is_blaster_available_by_sensor)
        self.assertFalse(coord.is_ir_blaster_available)

        # Standard binary sensor / connection status: 'on' -> available, 'off' -> unavailable
        coord.availability_entity_id = "binary_sensor.blaster_status"
        hass.states["binary_sensor.blaster_status"] = MagicMock(state="on")
        self.assertTrue(coord.is_blaster_available_by_sensor)
        self.assertTrue(coord.is_ir_blaster_available)

        hass.states["binary_sensor.blaster_status"] = MagicMock(state="off")
        self.assertFalse(coord.is_blaster_available_by_sensor)
        self.assertFalse(coord.is_ir_blaster_available)

        hass.states["binary_sensor.blaster_status"] = MagicMock(state="unavailable")
        self.assertFalse(coord.is_blaster_available_by_sensor)
        self.assertFalse(coord.is_ir_blaster_available)

    async def test_availability_entity_triggers_reconnect_resync(self):
        """Verify that an availability entity transitioning to available triggers reconnect resync."""
        from unittest.mock import patch, AsyncMock, MagicMock
        from homeassistant.core import Event

        hass = MockHass()
        coord = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id="entry_ir_123",
            device_id="dev_ir_456",
            model_code="CS-CU-RU18CKY-1",
            has_wifi=False,
            primary_backend="ir",
            blaster_entity_id="infrared.living_room_blaster",
            availability_entity_id="switch.blaster_smart_plug",
        )
        coord._is_esphome_blaster = False
        hass.states["infrared.living_room_blaster"] = MagicMock(state="available")
        hass.states["switch.blaster_smart_plug"] = MagicMock(state="on")

        # 1. Dispatch initial command
        await coord.async_dispatch_ir_command(mode="cool", target_temp=24, origin="HA UI")
        self.assertIsNotNone(coord._last_requested_ir_params)

        # 2. Simulate smart plug turning ON (old_state='off' -> new_state='on')
        event = MagicMock(spec=Event)
        event.data = {
            "entity_id": "switch.blaster_smart_plug",
            "old_state": MagicMock(state="off"),
            "new_state": MagicMock(state="on"),
        }

        with patch.object(coord, "_async_resync_on_reconnect", new_callable=AsyncMock) as mock_resync:
            await coord._async_availability_state_changed(event)
            mock_resync.assert_called_once()


if __name__ == "__main__":
    unittest.main()

