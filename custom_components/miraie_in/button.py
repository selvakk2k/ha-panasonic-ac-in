"""The MirAIe button platform."""

from __future__ import annotations

from datetime import timedelta

from miraie_ac import (
    Device as MirAIeDevice,
    MirAIeHub,
    PresetMode,
)

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .sensor import async_backfill_energy_statistics, async_rebuild_full_energy_statistics

PARALLEL_UPDATES = 0
from .logger import LOGGER
from .utils import get_devices_for_entry


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the MirAIe button platform."""
    hub: MirAIeHub = entry.runtime_data

    entry_data = getattr(entry, "data", {}) if isinstance(getattr(entry, "data", {}), dict) else {}
    is_ir_entry = entry_data.get("is_ir_only", False) or "username" not in entry_data
    coordinators = getattr(hub, "coordinators", {})

    devices = get_devices_for_entry(hub, entry)
    LOGGER.debug(
        "[button.setup] entry_id=%s is_ir_entry=%s device_count=%d entry_data_keys=%s",
        entry.entry_id,
        is_ir_entry,
        len(devices),
        list(entry_data.keys()),
    )

    entities: list[ButtonEntity] = []

    for device in devices:
        coordinator = coordinators.get(device.id)
        has_wifi = getattr(coordinator, "has_wifi", True) if coordinator else getattr(getattr(device, "details", None), "has_wifi", True)

        LOGGER.debug("[button.setup] device=%s is_ir_entry=%s has_wifi=%s → coil clean & energy=%s", device.id, is_ir_entry, has_wifi, not is_ir_entry and has_wifi)
        if not is_ir_entry and has_wifi:
            entities.append(MirAIeCoilCleanButton(device))
            entities.append(MirAIeRebuildEnergyStatsButton(hub, device))
            entities.append(MirAIeVerifyEnergyStatsButton(hub, device))

    LOGGER.debug("[button.setup] total entities registered: %d", len(entities))
    async_add_entities(entities)



class MirAIeCoilCleanButton(ButtonEntity):
    """Representation of the Coil Clean trigger button entity."""

    def __init__(self, device: MirAIeDevice) -> None:
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "start_coil_clean"
        self._attr_unique_id = f"{device.id}_start_coil_clean"
        self.device = device

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return "mdi:spray-bottle"

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

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.device.status.is_online

    async def async_press(self) -> None:
        """Press the button."""
        LOGGER.debug("Triggering coil clean cycle")
        await self.device.set_preset_mode(PresetMode.CLEAN)


class MirAIeRebuildEnergyStatsButton(ButtonEntity):
    """Diagnostic button entity to force a full 6-8 month rebuild of energy statistics."""

    def __init__(self, hub: MirAIeHub, device: MirAIeDevice) -> None:
        self.hub = hub
        self.device = device
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "rebuild_energy_statistics"
        self._attr_unique_id = f"{device.id}_rebuild_energy_statistics"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def icon(self) -> str:
        """Return the icon for the rebuild button."""
        return "mdi:database-refresh"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_press(self) -> None:
        """Trigger a full 6-8 month energy statistics rebuild for this device."""
        LOGGER.info("User requested full energy statistics rebuild for %s", self.device.friendly_name)
        hass = getattr(self, "hass", None)
        if not hass:
            LOGGER.error("Cannot rebuild energy statistics: hass instance not bound to entity %s", self.entity_id)
            return
        await async_rebuild_full_energy_statistics(hass, self.hub, self.device)


class MirAIeVerifyEnergyStatsButton(ButtonEntity):
    """Diagnostic button entity to run Yesterday -> Weekly -> Monthly gating verification."""

    def __init__(self, hub: MirAIeHub, device: MirAIeDevice) -> None:
        self.hub = hub
        self.device = device
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "verify_energy_statistics"
        self._attr_unique_id = f"{device.id}_verify_energy_statistics"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def icon(self) -> str:
        """Return the icon for the verify button."""
        return "mdi:database-check"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_press(self) -> None:
        """Trigger Yesterday -> Weekly -> Monthly gating verification."""
        LOGGER.info("[%s] Manual Diagnostic Button pressed: Running Yesterday -> Weekly -> Monthly gating verification", self.device.friendly_name)
        hass = getattr(self, "hass", None)
        if not hass:
            LOGGER.error("Cannot verify energy statistics: hass instance not bound to entity %s", self.entity_id)
            return
        default_start_date = dt_util.now().date() - timedelta(days=240)
        await async_backfill_energy_statistics(hass, self.hub, self.device, default_start_date, force_full_rebuild=False)

