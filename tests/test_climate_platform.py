"""Unit tests for MirAIe Climate entity platform."""

import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from tests.ha_stub import MockHass, MockEntry
from homeassistant.components.climate import (
    HVACMode,
    HVACAction,
    ClimateEntityFeature,
)
from homeassistant.const import UnitOfTemperature

from custom_components.miraie_in.climate import MirAIeClimate
from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator
from miraie_ac.enums import (
    PowerMode as LibPowerMode,
    HVACMode as LibHVACMode,
    FanMode as LibFanMode,
    SwingMode as LibSwingMode,
    PresetMode as LibPresetMode,
    ConvertiMode as LibConvertiMode,
)
from miraie_ac.device import DeviceStatus, DeviceDetails


class TestClimatePlatform(unittest.IsolatedAsyncioTestCase):
    """Test MirAIeClimate entity state mapping and command dispatching."""

    def setUp(self):
        self.hass = MockHass()
        self.mock_entry = MockEntry(
            entry_id="entry_climate_test",
            data={"device_id": "dev_test", "model_code": "CS-HZ24XKE"},
            options={},
        )

        self.mock_device = MagicMock()
        self.mock_device.id = "dev_test"
        self.mock_device.friendly_name = "Living Room AC"
        self.calls = []
        async def _set_temp(t):
            self.calls.append(("set_temperature", t))
        async def _turn_off():
            self.calls.append(("turn_off",))
        async def _set_hvac(m):
            self.calls.append(("set_hvac_mode", m))
        async def _set_fan(f):
            self.calls.append(("set_fan_mode", f))
        async def _set_preset(p):
            self.calls.append(("set_preset_mode", p))
        async def _set_converti(c):
            self.calls.append(("set_converti_mode", c))

        self.mock_device.turn_on = AsyncMock()
        self.mock_device.turn_off = _turn_off
        self.mock_device.set_temperature = _set_temp
        self.mock_device.set_hvac_mode = _set_hvac
        self.mock_device.set_fan_mode = _set_fan
        self.mock_device.set_preset_mode = _set_preset
        self.mock_device.set_converti_mode = _set_converti
        self.mock_device.register_callback = MagicMock()
        self.mock_device.remove_callback = MagicMock()

        self.mock_device.details = DeviceDetails(
            model_name="Panasonic AC",
            mac_address="AA:BB:CC:DD:EE:FF",
            category="ac",
            brand="Panasonic",
            firmware_version="1.0.0",
            serial_number="SN12345",
            model_number="CS-HZ24XKE",
            product_serial_number="PSN12345",
        )

        self.mock_device.status = DeviceStatus(
            is_online=True,
            temperature=24.0,
            room_temperature=26.0,
            power_mode=LibPowerMode.ON,
            fan_mode=LibFanMode.AUTO,
            v_swing_mode=LibSwingMode.AUTO,
            h_swing_mode=LibSwingMode.AUTO,
            display_mode=MagicMock(value="on"),
            hvac_mode=LibHVACMode.COOL,
            preset_mode=LibPresetMode.NONE,
            converti_mode=LibConvertiMode.OFF,
        )

        self.coordinator = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id=self.mock_entry.entry_id,
            device_id=self.mock_device.id,
            model_code="CS-HZ24XKE",
            has_wifi=True,
            blaster_entity_id=None,
            primary_backend="cloud",
            hybrid_submode="auto",
        )
        self.coordinator.state["power"] = "on"
        self.coordinator.state["mode"] = "cool"
        self.coordinator.state["temperature"] = 24.0
        self.coordinator.state["room_temperature"] = 26.0

        self.climate = MirAIeClimate(
            device=self.mock_device,
            entry=self.mock_entry,
            coordinator=self.coordinator,
        )
        self.climate.hass = self.hass

    async def test_climate_properties(self):
        """Test climate property mappings."""
        self.assertEqual(self.climate._attr_unique_id, "dev_test")
        self.assertIsNone(self.climate.name)
        self.assertIsNone(self.climate._attr_name)
        self.assertEqual(self.climate._attr_temperature_unit, UnitOfTemperature.CELSIUS)
        self.assertEqual(self.climate.target_temperature, 24)
        self.assertEqual(self.climate.current_temperature, 26.0)
        self.assertEqual(self.climate.hvac_mode, "cool")

    async def test_set_temperature_in_bounds(self):
        """Test setting target temperature within limits."""
        await self.climate.async_set_temperature(temperature=22.0)
        await asyncio.sleep(0.01)
        self.assertIn(("set_temperature", 22.0), self.calls)

    async def test_set_hvac_mode_cool_and_off(self):
        """Test setting HVAC mode to COOL and OFF."""
        await self.climate.async_set_hvac_mode(HVACMode.DRY)
        await asyncio.sleep(0.01)
        self.assertIn(("set_hvac_mode", LibHVACMode.DRY), self.calls)

        await self.climate.async_set_hvac_mode(HVACMode.OFF)
        await asyncio.sleep(0.01)
        self.assertIn(("turn_off",), self.calls)

    async def test_set_fan_mode(self):
        """Test setting fan modes."""
        await self.climate.async_set_fan_mode("high")
        await asyncio.sleep(0.01)
        self.assertIn(("set_fan_mode", LibFanMode.HIGH), self.calls)

    async def test_set_preset_mode_boost_eco_converti(self):
        """Test preset mode setting for Boost, Eco, and Converti."""
        await self.climate.async_set_preset_mode("boost")
        await asyncio.sleep(0.01)
        self.assertIn(("set_preset_mode", LibPresetMode.BOOST), self.calls)

        await self.climate.async_set_preset_mode("eco")
        await asyncio.sleep(0.01)
        self.assertIn(("set_preset_mode", LibPresetMode.ECO), self.calls)

        await self.climate.async_set_preset_mode("80%")
        await asyncio.sleep(0.01)
        self.assertIn(("set_converti_mode", LibConvertiMode.C80), self.calls)

    async def test_climate_available_when_cloud_offline_with_blaster(self):
        """Test climate entity remains available when cloud is offline if blaster is attached."""
        self.mock_device.status.is_online = False
        # Without coordinator/blaster -> unavailable
        climate_cloud_only = MirAIeClimate(device=self.mock_device, entry=self.mock_entry, coordinator=None)
        self.assertFalse(climate_cloud_only.available)

        # With blaster attached and online -> available
        self.hass.states["infrared.living_room_blaster"] = MagicMock(state="on")
        coord_hybrid = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id=self.mock_entry.entry_id,
            device_id=self.mock_device.id,
            model_code="CS-HZ24XKE",
            has_wifi=True,
            blaster_entity_id="infrared.living_room_blaster",
            primary_backend="cloud",
            hybrid_submode="auto",
        )
        climate_hybrid = MirAIeClimate(device=self.mock_device, entry=self.mock_entry, coordinator=coord_hybrid)
        self.assertTrue(climate_hybrid.available)

        # When IR blaster is also unavailable -> entity becomes unavailable
        self.hass.states["infrared.living_room_blaster"] = MagicMock(state="unavailable")
        self.assertFalse(climate_hybrid.available)

    async def test_offline_proactive_ir_dispatch(self):
        """Test that offline cloud with auto hybrid proactively dispatches IR command."""
        self.mock_device.status.is_online = False
        coord_hybrid = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id=self.mock_entry.entry_id,
            device_id=self.mock_device.id,
            model_code="CS-HZ24XKE",
            has_wifi=True,
            blaster_entity_id="infrared.living_room_blaster",
            primary_backend="cloud",
            hybrid_submode="auto",
        )
        coord_hybrid.async_dispatch_ir_command = AsyncMock(return_value=True)
        climate = MirAIeClimate(device=self.mock_device, entry=self.mock_entry, coordinator=coord_hybrid)

        await climate.async_set_temperature(temperature=23.0)
        coord_hybrid.async_dispatch_ir_command.assert_awaited_with(
            mode=None,
            target_temp=23,
            fan=None,
            v_vane=None,
            h_vane=None,
            eco=None,
            nanoe=None,
            preset=None,
            origin="IR Failover (Offline)",
        )

    async def test_broker_disconnected_proactive_ir_dispatch(self):
        """Test that disconnected MQTT broker triggers proactive IR dispatch even if device is online."""
        self.mock_device.status.is_online = True
        mock_hub = MagicMock()
        mock_hub.broker = MagicMock()
        mock_hub.broker.connected = MagicMock()
        mock_hub.broker.connected.is_set = MagicMock(return_value=False)

        coord_hybrid = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id=self.mock_entry.entry_id,
            device_id=self.mock_device.id,
            model_code="CS-HZ24XKE",
            has_wifi=True,
            blaster_entity_id="infrared.living_room_blaster",
            primary_backend="cloud",
            hybrid_submode="auto",
        )
        coord_hybrid.hub = mock_hub
        coord_hybrid.async_dispatch_ir_command = AsyncMock(return_value=True)
        climate = MirAIeClimate(device=self.mock_device, entry=self.mock_entry, coordinator=coord_hybrid)

        await climate.async_set_hvac_mode(HVACMode.COOL)
        coord_hybrid.async_dispatch_ir_command.assert_awaited_with(
            mode="cool",
            target_temp=None,
            fan=None,
            v_vane=None,
            h_vane=None,
            eco=None,
            nanoe=None,
            preset=None,
            origin="IR Failover (Offline)",
        )


    async def test_eco_mode_snaps_temp_and_preserves_hardware_range(self):
        """Test that activating Eco snaps temp to 26C while preserving full hardware min_temp/max_temp for service callers."""
        from homeassistant.components.climate import PRESET_ECO
        await self.climate.async_set_preset_mode(PRESET_ECO)
        self.assertEqual(self.climate.preset_mode, PRESET_ECO)
        self.assertEqual(self.coordinator.state["eco"], True)
        self.assertEqual(self.coordinator.state["temperature"], 26)
        self.assertEqual(self.climate.min_temp, 16.0)
        self.assertEqual(self.climate.max_temp, 30.0)

    async def test_adjusting_temperature_exits_eco_mode(self):
        """Test that changing temperature automatically cancels Eco mode and restores temp range."""
        from homeassistant.components.climate import PRESET_ECO
        await self.climate.async_set_preset_mode(PRESET_ECO)
        self.assertEqual(self.climate.preset_mode, PRESET_ECO)

        # Now adjust temperature to 24C
        await self.climate.async_set_temperature(temperature=24.0)
        self.assertEqual(self.coordinator.state["eco"], False)
        self.assertEqual(self.coordinator.state["active_preset"], "none")
        self.assertEqual(self.climate.target_temperature, 24)
        self.assertEqual(self.climate.min_temp, 16.0)
        self.assertEqual(self.climate.max_temp, 30.0)

    async def test_preset_modes_and_dry_mode_auto_switch(self):
        """Test that preset_modes preserves full capability list and auto-switches Dry mode to Cool."""
        from homeassistant.components.climate import PRESET_NONE, PRESET_ECO, PRESET_BOOST
        self.coordinator.state["mode"] = "cool"
        self.assertIn(PRESET_ECO, self.climate.preset_modes)
        self.assertIn(PRESET_BOOST, self.climate.preset_modes)

        # In dry mode, preset_modes capability is preserved for card rendering
        self.coordinator.state["mode"] = "dry"
        self.assertIn(PRESET_ECO, self.climate.preset_modes)

        # Requesting eco preset in dry mode auto-switches to cool
        await self.climate.async_set_preset_mode(PRESET_ECO)
        self.assertEqual(self.coordinator.state["mode"], "cool")
        self.assertEqual(self.coordinator.state["eco"], True)

    async def test_friendly_swing_modes(self):
        """Test friendly swing mode mapping and bidirectional code compatibility."""
        from custom_components.miraie_in.const import V1, H2, SWING_AUTO, SWING_V_TOP, SWING_H_LEFT_CENTER

        # Set by friendly name
        await self.climate.async_set_swing_mode(SWING_V_TOP)
        self.assertEqual(self.coordinator.state["v_vane"], V1)
        self.assertEqual(self.climate.swing_mode, SWING_V_TOP)

        # Set by raw code (backward compatibility)
        await self.climate.async_set_swing_horizontal_mode(H2)
        self.assertEqual(self.coordinator.state["h_vane"], H2)
        self.assertEqual(self.climate.swing_horizontal_mode, SWING_H_LEFT_CENTER)

    async def test_current_temperature_ir_only_without_sensor_returns_none(self):
        """Test that IR-only AC without external temperature sensor returns None (no fake sensor)."""
        ir_coord = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id="ir_entry",
            device_id="ir_dev",
            model_code="CS-CU-KN18YKY",
            has_wifi=False,
            blaster_entity_id="infrared.blaster",
        )
        ir_device = MagicMock()
        ir_device.id = "ir_dev"
        ir_device.status = MagicMock()
        ir_device.status.room_temperature = None
        ir_climate = MirAIeClimate(ir_device, self.mock_entry, ir_coord)

        self.assertIsNone(ir_climate.current_temperature)

    async def test_current_temperature_with_optional_external_sensor(self):
        """Test that configuring an external temperature sensor dynamically updates current_temperature."""
        ir_coord = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id="ir_entry",
            device_id="ir_dev",
            model_code="CS-CU-KN18YKY",
            has_wifi=False,
            blaster_entity_id="infrared.blaster",
            temperature_sensor_entity_id="sensor.room_temp",
        )
        self.hass.states["sensor.room_temp"] = MagicMock(state="27.5")
        ir_coord.async_setup_receiver()

        ir_device = MagicMock()
        ir_device.id = "ir_dev"
        ir_device.status = MagicMock()
        ir_device.status.room_temperature = None
        ir_climate = MirAIeClimate(ir_device, self.mock_entry, ir_coord)
        ir_climate.hass = self.hass

        self.assertEqual(ir_climate.current_temperature, 27.5)

    async def test_convertible_preset_transition_and_reset_to_none(self):
        """Test setting a convertible preset and resetting back to 'none' in Cool mode."""
        from homeassistant.components.climate import PRESET_NONE

        # 1. Set convertible 80% preset
        await self.climate.async_set_preset_mode("cv_80")
        self.assertEqual(self.climate.preset_mode, "cv_80")
        self.assertEqual(self.coordinator.state["active_preset"], "cv_80")
        self.assertEqual(self.coordinator.state["converti"], "cv_80")

        # 2. Reset preset back to 'none'
        await self.climate.async_set_preset_mode(PRESET_NONE)
        self.assertEqual(self.climate.preset_mode, PRESET_NONE)
        self.assertEqual(self.coordinator.state["active_preset"], "none")
        self.assertEqual(self.coordinator.state["converti"], "cv_off")
        self.assertEqual(self.coordinator.state["mode"], "cool")

        # 3. Setting 'cv_0' should also reset back to 'none'
        await self.climate.async_set_preset_mode("cv_100")
        self.assertEqual(self.climate.preset_mode, "cv_100")
        await self.climate.async_set_preset_mode("cv_0")
        self.assertEqual(self.climate.preset_mode, PRESET_NONE)
        self.assertEqual(self.coordinator.state["active_preset"], "none")
        self.assertEqual(self.coordinator.state["converti"], "cv_off")

    async def test_convertible_preset_transition_and_reset_to_none_ir_mode(self):
        """Test setting convertible preset and resetting to none when primary_backend is ir."""
        from homeassistant.components.climate import PRESET_NONE

        coord_ir = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id="ir_test_entry",
            device_id="ir_dev_cv",
            model_code="CS-HZ24XKE",
            has_wifi=False,
            blaster_entity_id="infrared.living_room_blaster",
            primary_backend="ir",
        )
        coord_ir.async_dispatch_ir_command = AsyncMock(return_value=True)
        climate_ir = MirAIeClimate(self.mock_device, self.mock_entry, coord_ir)

        # 1. Set convertible 80% via IR
        await climate_ir.async_set_preset_mode("cv_80")
        self.assertEqual(climate_ir.preset_mode, "cv_80")
        self.assertEqual(coord_ir.state["active_preset"], "cv_80")
        self.assertEqual(coord_ir.state["converti"], "cv_80")
        coord_ir.async_dispatch_ir_command.assert_awaited_with(
            mode="converti_80",
            target_temp=None,
            fan=None,
            v_vane=None,
            h_vane=None,
            eco=False,
            nanoe=None,
            preset="cv_80",
            origin="IR Blaster",
        )

        # 2. Reset back to Normal mode via IR
        await climate_ir.async_set_preset_mode(PRESET_NONE)
        self.assertEqual(climate_ir.preset_mode, PRESET_NONE)
        self.assertEqual(coord_ir.state["active_preset"], "none")
        self.assertEqual(coord_ir.state["converti"], "cv_off")
        coord_ir.async_dispatch_ir_command.assert_awaited_with(
            mode="cool",
            target_temp=None,
            fan=None,
            v_vane=None,
            h_vane=None,
            eco=False,
            nanoe=None,
            preset="none",
            origin="IR Blaster",
        )

    async def test_climate_availability_honours_lwt_offline_for_remote_blaster(self):
        """Verify climate entity availability honours MQTT LWT offline status on remote.* blasters."""
        self.mock_device.status = MagicMock(is_online=False)

        coord = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id="entry_climate_test",
            device_id="dev_test",
            model_code="CS-HZ24XKE",
            has_wifi=False,
            blaster_entity_id="remote.bedroom_ir_blaster",
            availability_topic="tele/tasmota_ir/LWT",
        )
        self.hass.states["remote.bedroom_ir_blaster"] = MagicMock(state="on")
        climate = MirAIeClimate(self.mock_device, self.mock_entry, coord)

        # Initially, blaster entity is on and no LWT message received yet -> available
        self.assertTrue(climate.available)

        # LWT goes offline -> climate entity MUST become unavailable
        class MockMsg:
            def __init__(self, payload):
                self.payload = payload

        coord._async_mqtt_lwt_message(MockMsg("offline"))
        self.assertFalse(climate.available)

        # LWT goes back online -> climate entity becomes available again
        coord._async_mqtt_lwt_message(MockMsg("online"))
        self.assertTrue(climate.available)


if __name__ == "__main__":
    unittest.main()
