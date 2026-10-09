"""Unit tests for Binary Sensor platform in ha-miraie-ac."""

import unittest
from unittest.mock import MagicMock

from tests.ha_stub import MockHass, MockEntry
from custom_components.miraie_in.binary_sensor import (
    MirAIeFilterCleanBinarySensor,
    MirAIeCoilCleanBinarySensor,
    MirAIeCloudMQTTConnectedBinarySensor,
    MirAIeDeviceOnlineBinarySensor,
    MirAIeIRBlasterAvailableBinarySensor,
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
)


class TestBinarySensorPlatform(unittest.TestCase):
    """Test binary sensor platform entities."""

    def setUp(self):
        self.hass = MockHass()
        self.mock_entry = MockEntry(
            entry_id="entry_bs_test",
            data={"device_id": "dev_bs_test", "model_code": "CS-HZ24XKE"},
            options={},
        )

        self.mock_hub = MagicMock()
        self.mock_hub.broker = MagicMock()
        self.mock_hub.broker.connected = MagicMock()
        self.mock_hub.broker.connected.is_set = MagicMock(return_value=True)

        self.mock_device = MagicMock()
        self.mock_device.id = "dev_bs_test"
        self.mock_device.friendly_name = "Office AC"
        self.mock_device.register_callback = MagicMock()
        self.mock_device.remove_callback = MagicMock()

        self.mock_device.details = DeviceDetails(
            model_name="Panasonic AC",
            mac_address="55:66:77:88:99:00",
            category="ac",
            brand="Panasonic",
            firmware_version="1.0.0",
            serial_number="SN88888",
            model_number="CS-HZ24XKE",
            product_serial_number="PSN88888",
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
            preset_mode=LibPresetMode.CLEAN,
            converti_mode=LibConvertiMode.OFF,
            filter_clean_alert=True,
        )

        self.coordinator = MirAIeDeviceCoordinator(
            hass=self.hass,
            entry_id=self.mock_entry.entry_id,
            device_id=self.mock_device.id,
            model_code="CS-HZ24XKE",
            has_wifi=True,
            blaster_entity_id="remote.bedroom_blaster",
            primary_backend="cloud",
            hybrid_submode="auto",
        )
        self.coordinator.hub = self.mock_hub

    def test_filter_clean_and_coil_cleaning_sensors(self):
        """Test filter alert and coil cleaning binary sensors."""
        filter_sensor = MirAIeFilterCleanBinarySensor(self.mock_device)
        self.assertTrue(filter_sensor.is_on)

        coil_sensor = MirAIeCoilCleanBinarySensor(self.mock_device)
        self.assertTrue(coil_sensor.is_on)

    def test_connectivity_and_online_sensors(self):
        """Test MQTT connected and device online binary sensors."""
        mqtt_sensor = MirAIeCloudMQTTConnectedBinarySensor(self.mock_device, self.coordinator)
        self.assertTrue(mqtt_sensor.is_on)

        online_sensor = MirAIeDeviceOnlineBinarySensor(self.mock_device, self.coordinator)
        self.assertTrue(online_sensor.is_on)

    def test_ir_blaster_available_binary_sensor(self):
        """Test IR blaster available binary sensor reflects coordinator availability."""
        ir_sensor = MirAIeIRBlasterAvailableBinarySensor(self.mock_device, self.coordinator)
        self.hass.states["remote.bedroom_blaster"] = MagicMock(state="on")
        self.assertTrue(ir_sensor.is_on)

        # Set fake cutoff entity
        self.coordinator.availability_entity_id = "input_boolean.fake_ir_cutoff"
        self.hass.states["input_boolean.fake_ir_cutoff"] = MagicMock(state="off")
        self.assertTrue(ir_sensor.is_on)

        # Cut off
        self.hass.states["input_boolean.fake_ir_cutoff"] = MagicMock(state="on")
        self.assertFalse(ir_sensor.is_on)


if __name__ == "__main__":
    unittest.main()
