"""Unit tests for Select platform in ha-miraie-ac."""

import unittest
from unittest.mock import MagicMock, AsyncMock

from tests.ha_stub import MockHass, MockEntry
from custom_components.miraie_in.select import (
    MirAIeDisplayModeSelect,
    async_setup_entry,
)
from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator
from miraie_ac.device import DeviceStatus, DeviceDetails
from miraie_ac.enums import (
    PowerMode as LibPowerMode,
    HVACMode as LibHVACMode,
    FanMode as LibFanMode,
    SwingMode as LibSwingMode,
    PresetMode as LibPresetMode,
    ConvertiMode as LibConvertiMode,
    DisplayMode as LibDisplayMode,
)


class TestSelectPlatform(unittest.IsolatedAsyncioTestCase):
    """Test Select platform entities execution and state reflection."""

    def setUp(self):
        self.hass = MockHass()
        self.mock_entry = MockEntry(
            entry_id="entry_select_test",
            data={"device_id": "dev_select_test", "model_code": "CS-HZ24XKE"},
            options={},
        )
        self.device = MagicMock()
        self.device.id = "dev_select_test"
        self.device.name = "Living Room AC"
        self.device.friendly_name = "Living Room AC"
        self.device.supports_local_udp = False
        self.device.udp_transport = None
        self.device.supports_night_display = True
        self.device.model_capabilities = {
            "controlCapabilities": [
                {
                    "capability": "DisplayControl",
                    "attributes": [{"key": "acdc", "values": ["on", "night", "off"]}]
                }
            ]
        }
        self.device.details = DeviceDetails(
            model_name="Panasonic AC",
            mac_address="A1:B2:C3:D4:E5:F6",
            category="AC",
            brand="Panasonic",
            firmware_version="1.0.0",
            serial_number="SN123456",
            model_number="CS-HZ24XKE",
            product_serial_number="PSN123456",
        )
        self.device.status = DeviceStatus(
            is_online=True,
            temperature=24.0,
            room_temperature=26.0,
            power_mode=LibPowerMode.ON,
            fan_mode=LibFanMode.AUTO,
            v_swing_mode=LibSwingMode.AUTO,
            h_swing_mode=LibSwingMode.AUTO,
            display_mode=LibDisplayMode.NIGHT,
            hvac_mode=LibHVACMode.COOL,
            preset_mode=LibPresetMode.NONE,
            converti_mode=LibConvertiMode.OFF,
        )
        self.device.set_display_mode = AsyncMock()

        self.coordinator = MagicMock()
        self.coordinator.has_wifi = True
        self.coordinator.primary_backend = "cloud"
        self.coordinator.blaster_entity_id = None
        self.coordinator.state = {"display": "night"}
        self.coordinator.capabilities = {"supports_night_display": True}
        self.coordinator.async_add_listener = MagicMock(return_value=lambda: None)

    async def test_display_mode_select_options_with_night_display(self):
        """Test options list when night display is supported."""
        select_entity = MirAIeDisplayModeSelect(self.device, self.coordinator)
        self.assertEqual(select_entity.options, ["on", "night", "off"])
        self.assertEqual(select_entity.current_option, "night")
        self.assertEqual(select_entity.icon, "mdi:weather-night")

    async def test_display_mode_select_options_without_night_display(self):
        """Test options list when night display is not supported."""
        self.device.supports_night_display = False
        self.coordinator.capabilities = {"supports_night_display": False}
        select_entity = MirAIeDisplayModeSelect(self.device, self.coordinator)
        self.assertEqual(select_entity.options, ["on", "off"])

    async def test_select_option_dispatches_correct_mode(self):
        """Test selecting options calls set_display_mode with appropriate enum."""
        select_entity = MirAIeDisplayModeSelect(self.device, self.coordinator)

        await select_entity.async_select_option("on")
        self.device.set_display_mode.assert_awaited_with(LibDisplayMode.ON, transport="cloud")

        await select_entity.async_select_option("night")
        self.device.set_display_mode.assert_awaited_with(LibDisplayMode.NIGHT, transport="cloud")

        await select_entity.async_select_option("off")
        self.device.set_display_mode.assert_awaited_with(LibDisplayMode.OFF, transport="cloud")

    async def test_select_option_invalid_raises_value_error(self):
        """Test selecting an invalid option raises ValueError."""
        select_entity = MirAIeDisplayModeSelect(self.device, self.coordinator)
        with self.assertRaises(ValueError):
            await select_entity.async_select_option("invalid_mode")

    async def test_real_coordinator_display_optimistic_update(self):
        """Test real MirAIeDeviceCoordinator optimistic updates with on, night, and off."""
        coord = MirAIeDeviceCoordinator(
            hass=self.hass,
            device_id=self.device.id,
            model_code="CS-HZ24XKE",
            has_wifi=True,
            device=self.device,
        )
        coord.async_optimistic_update(display="night", origin="Cloud")
        self.assertEqual(coord.state["display"], "night")

        coord.async_optimistic_update(display="off", origin="Cloud")
        self.assertEqual(coord.state["display"], "off")

        coord.async_optimistic_update(display="on", origin="Cloud")
        self.assertEqual(coord.state["display"], "on")

        # Verify legacy boolean compatibility
        coord.async_optimistic_update(display=False, origin="Cloud")
        self.assertEqual(coord.state["display"], "off")

        coord.async_optimistic_update(display=True, origin="Cloud")
        self.assertEqual(coord.state["display"], "on")

    async def test_async_setup_entry_creates_select_entity(self):
        """Test async_setup_entry instantiates MirAIeDisplayModeSelect."""
        mock_hub = MagicMock()
        mock_hub.home = MagicMock(devices=[self.device])
        mock_hub.devices = [self.device]
        mock_hub.coordinators = {self.device.id: self.coordinator}
        self.mock_entry.runtime_data = mock_hub

        added_entities = []
        await async_setup_entry(self.hass, self.mock_entry, lambda ents: added_entities.extend(ents))

        self.assertEqual(len(added_entities), 1)
        self.assertIsInstance(added_entities[0], MirAIeDisplayModeSelect)
        self.assertEqual(added_entities[0].unique_id, f"{self.device.id}_display_mode")

    async def test_ir_display_toggle_with_raw_boolean_prev_display(self):
        """Test IR display toggle when coordinator holds raw boolean display state."""
        from homeassistant.exceptions import HomeAssistantError

        self.coordinator.primary_backend = "ir"
        self.coordinator.blaster_entity_id = "remote.blaster"
        self.coordinator.state = {"display": True}
        self.coordinator.async_dispatch_ir_command = AsyncMock(return_value=True)

        select_entity = MirAIeDisplayModeSelect(self.device, self.coordinator)

        # Turning off should dispatch IR because prev_display True (normalized 'on') != 'off'
        await select_entity.async_select_option("off")
        self.coordinator.async_dispatch_ir_command.assert_awaited_once_with(
            mode="display",
            display=False,
            origin="IR Blaster",
        )

        # Now test failure rollback restores raw boolean True
        self.coordinator.state = {"display": True}
        self.coordinator.async_dispatch_ir_command = AsyncMock(return_value=False)
        with self.assertRaises(HomeAssistantError):
            await select_entity.async_select_option("off")
        self.assertIs(self.coordinator.state["display"], True)
