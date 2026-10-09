"""The MirAIe binary sensor platform."""

from __future__ import annotations

from miraie_ac import (
    Device as MirAIeDevice,
    MirAIeHub,
    PresetMode,
)

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .logger import LOGGER
from .utils import get_devices_for_entry

PARALLEL_UPDATES = 0


from homeassistant.helpers import entity_registry as er

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the MirAIe Binary Sensors."""
    hub: MirAIeHub = entry.runtime_data
    coordinators = getattr(hub, "coordinators", {})
    ent_reg = er.async_get(hass)

    entities: list[BinarySensorEntity] = []
    entry_data = entry.data if hasattr(entry, "data") and isinstance(entry.data, dict) else {}
    is_ir_entry = entry_data.get("is_ir_only", False)
    devices = get_devices_for_entry(hub, entry)


    for device in devices:
        coordinator = coordinators.get(device.id)
        has_wifi = getattr(coordinator, "has_wifi", True) if coordinator else getattr(getattr(device, "details", None), "has_wifi", True)

        if not is_ir_entry and has_wifi:
            entities.append(MirAIeFilterCleanBinarySensor(device))
            entities.append(MirAIeCoilCleanBinarySensor(device))
            if coordinator:
                entities.append(MirAIeCloudMQTTConnectedBinarySensor(device, coordinator))
                entities.append(MirAIeDeviceOnlineBinarySensor(device, coordinator))

        if coordinator and coordinator.blaster_entity_id:
            entities.append(MirAIeIRBlasterAvailableBinarySensor(device, coordinator))
        else:
            unq_id = f"{device.id}_ir_blaster_available"
            entity_id = ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unq_id)
            if entity_id:
                ent_reg.async_remove(entity_id)
                LOGGER.info("Cleaned up orphaned binary sensor %s after IR blaster removal", entity_id)

    async_add_entities(entities)



class MirAIeFilterCleanBinarySensor(BinarySensorEntity):
    """Representation of the Filter Clean alert sensor."""

    def __init__(self, device: MirAIeDevice) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "filter_clean_alert"
        self._attr_unique_id = f"{device.id}_filter_clean_alert"
        self.device = device
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def icon(self) -> str | None:
        return "mdi:air-filter"

    @property
    def is_on(self) -> bool:
        """Return True if filter clean alert is active (dirty filter)."""
        return getattr(self.device.status, "filter_clean_alert", False)

    @property
    def available(self) -> bool:
        return self.device.status.is_online

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self.device.id)
            },
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        LOGGER.debug("Successfully added filter clean alert binary sensor to HA")
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Entity being removed from hass."""
        LOGGER.debug("Successfully removed filter clean alert binary sensor from HA")
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeCoilCleanBinarySensor(BinarySensorEntity):
    """Representation of the Coil Clean active sensor."""

    def __init__(self, device: MirAIeDevice) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "coil_cleaning"
        self._attr_unique_id = f"{device.id}_coil_cleaning"
        self.device = device
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def icon(self) -> str | None:
        return "mdi:spray-bottle"

    @property
    def is_on(self) -> bool:
        """Return True if coil clean is running."""
        return self.device.status.preset_mode == PresetMode.CLEAN

    @property
    def available(self) -> bool:
        return self.device.status.is_online

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, self.device.id)
            },
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        LOGGER.debug("Successfully added coil cleaning binary sensor to HA")
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Entity being removed from hass."""
        LOGGER.debug("Successfully removed coil cleaning binary sensor from HA")
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeIRBlasterAvailableBinarySensor(BinarySensorEntity):
    """Binary sensor reporting availability of configured IR blaster entity."""

    def __init__(self, device: MirAIeDevice, coordinator) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "ir_blaster_available"
        self._attr_unique_id = f"{device.id}_ir_blaster_available"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self.device = device
        self.coordinator = coordinator

    @property
    def is_on(self) -> bool:
        if not self.coordinator:
            return False
        return bool(self.coordinator.is_ir_blaster_available)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if hasattr(self, "coordinator") and self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
            track_entities = []
            if self.coordinator.blaster_entity_id:
                track_entities.append(self.coordinator.blaster_entity_id)
            if self.coordinator.availability_entity_id:
                track_entities.append(self.coordinator.availability_entity_id)
            if track_entities:
                from homeassistant.helpers.event import async_track_state_change_event
                self.async_on_remove(
                    async_track_state_change_event(
                        self.coordinator.hass, track_entities, self._async_blaster_changed
                    )
                )

    async def _async_blaster_changed(self, event) -> None:
        self.async_write_ha_state()


class MirAIeCloudMQTTConnectedBinarySensor(BinarySensorEntity):
    """Binary sensor reporting MirAIe broker MQTT connection status."""

    def __init__(self, device: MirAIeDevice, coordinator) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "cloud_mqtt_connected"
        self._attr_unique_id = f"{device.id}_cloud_mqtt_connected"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self.device = device
        self.coordinator = coordinator

    @property
    def is_on(self) -> bool:
        hub = getattr(self.coordinator, "hub", None)
        if hub and hasattr(hub, "broker"):
            broker = hub.broker
            if broker and hasattr(broker, "connected"):
                return broker.connected.is_set()
            client = getattr(broker, "client", None)
            if client is not None:
                is_connected = getattr(client, "is_connected", None)
                if callable(is_connected):
                    return bool(is_connected())
                return not getattr(client, "_closed", False)
        return False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if hasattr(self, "coordinator") and self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)
        from homeassistant.helpers.event import async_track_time_interval
        from homeassistant.core import callback
        from datetime import timedelta
        
        @callback
        def _update_state(now=None):
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_time_interval(self.hass, _update_state, timedelta(seconds=10))
        )

    async def async_will_remove_from_hass(self) -> None:
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeDeviceOnlineBinarySensor(BinarySensorEntity):
    """Binary sensor reporting device cloud online status."""

    def __init__(self, device: MirAIeDevice, coordinator) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "device_online"

        self._attr_unique_id = f"{device.id}_device_online"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self.device = device
        self.coordinator = coordinator

    @property
    def is_on(self) -> bool:
        return self.device.status.is_online

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if hasattr(self, "coordinator") and self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self) -> None:
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)




