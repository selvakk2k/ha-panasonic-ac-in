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
from .utils import get_devices_for_entry, get_entry_data, is_ir_only_entry, is_device_available, build_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the MirAIe button platform."""
    hub: MirAIeHub = entry.runtime_data

    entry_data = get_entry_data(entry)
    is_ir_entry = is_ir_only_entry(entry)
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
    has_username = bool(entry_data.get("username"))
    from homeassistant.helpers import entity_registry as er
    ent_reg = er.async_get(hass)

    for device in devices:
        coordinator = coordinators.get(device.id)
        t_caps = coordinator.get_transport_caps() if coordinator and hasattr(coordinator, "get_transport_caps") else {}
        if isinstance(t_caps, dict) and t_caps.get("is_matter_only_configured") is True:
            for suffix in ("start_coil_clean", "rebuild_energy_statistics", "verify_energy_statistics"):
                eid = ent_reg.async_get_entity_id("button", DOMAIN, f"{device.id}_{suffix}")
                if eid:
                    ent_reg.async_remove(eid)
            continue
        has_wifi = getattr(coordinator, "has_wifi", True) if coordinator else getattr(getattr(device, "details", None), "has_wifi", True)

        LOGGER.debug("[button.setup] device=%s is_ir_entry=%s has_wifi=%s has_username=%s", device.id, is_ir_entry, has_wifi, has_username)
        if not is_ir_entry and has_wifi:
            entities.append(MirAIeCoilCleanButton(device, coordinator))
            if has_username:
                entities.append(MirAIeRebuildEnergyStatsButton(hub, device))
                entities.append(MirAIeVerifyEnergyStatsButton(hub, device))
            else:
                for suffix in ["rebuild_energy_statistics", "verify_energy_statistics"]:
                    unq_id = f"{device.id}_{suffix}"
                    entity_id = ent_reg.async_get_entity_id("button", DOMAIN, unq_id)
                    if entity_id:
                        ent_reg.async_remove(entity_id)
                        LOGGER.info("Cleaned up orphaned energy button %s for offline local UDP entry", entity_id)

    LOGGER.debug("[button.setup] total entities registered: %d", len(entities))
    async_add_entities(entities)



class MirAIeCoilCleanButton(ButtonEntity):
    """Representation of the Coil Clean trigger button entity."""

    def __init__(self, device: MirAIeDevice, coordinator: Any | None = None) -> None:
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "start_coil_clean"
        self._attr_unique_id = f"{device.id}_start_coil_clean"
        self.device = device
        self.coordinator = coordinator

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return "mdi:spray-bottle"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return build_device_info(self.device)

    @property
    def available(self) -> bool:
        """Return True if entity is available across UDP, Cloud, and IR channels."""
        return is_device_available(self.device, self.coordinator)

    async def async_press(self) -> None:
        """Press the button."""
        LOGGER.debug("Triggering coil clean cycle")
        coord = self.coordinator
        primary = getattr(coord, "primary_backend", "cloud") if coord else "cloud"
        supports_udp = getattr(self.device, "supports_local_udp", False) is True
        has_udp = supports_udp and getattr(self.device, "udp_transport", None) is not None
        origin_label = "Local UDP" if (has_udp and primary == "local_udp") else "Cloud"
        if coord:
            coord.async_optimistic_update(preset="clean", origin=origin_label)
        await self.device.set_preset_mode(PresetMode.CLEAN)
        if coord:
            coord.state["last_controlled_by"] = origin_label

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator state updates."""
        await super().async_added_to_hass()
        if self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )


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
        return build_device_info(self.device)

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
        return build_device_info(self.device)

    async def async_press(self) -> None:
        """Trigger Yesterday -> Weekly -> Monthly gating verification."""
        LOGGER.info("[%s] Manual Diagnostic Button pressed: Running Yesterday -> Weekly -> Monthly gating verification", self.device.friendly_name)
        hass = getattr(self, "hass", None)
        if not hass:
            LOGGER.error("Cannot verify energy statistics: hass instance not bound to entity %s", self.entity_id)
            return
        default_start_date = dt_util.now().date() - timedelta(days=240)
        await async_backfill_energy_statistics(hass, self.hub, self.device, default_start_date, force_full_rebuild=False)

