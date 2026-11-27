"""The MirAIe select platform."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import MirAIeDeviceCoordinator

from miraie_ac import (
    Device as MirAIeDevice,
    MirAIeHub,
    DisplayMode,
)

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .logger import LOGGER
from .utils import (
    get_devices_for_entry,
    async_attach_blaster_listener,
    safe_async_write_ha_state,
    is_device_available,
    build_device_info,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the MirAIe select platform."""
    hub: MirAIeHub = entry.runtime_data
    coordinators = getattr(hub, "coordinators", {})
    ent_reg = er.async_get(hass)
    devices = get_devices_for_entry(hub, entry)

    entities: list[SelectEntity] = []

    for device in devices:
        coordinator = coordinators.get(device.id)
        t_caps = coordinator.get_transport_caps() if coordinator and hasattr(coordinator, "get_transport_caps") else {}
        if isinstance(t_caps, dict) and t_caps.get("is_matter_only_configured") is True:
            eid = ent_reg.async_get_entity_id("select", DOMAIN, f"{device.id}_display_mode")
            if eid:
                ent_reg.async_remove(eid)
            continue
        supports_night = getattr(device, "supports_night_display", False)
        if not supports_night and coordinator and hasattr(coordinator, "capabilities"):
            supports_night = bool(coordinator.capabilities.get("supports_night_display", False))

        if supports_night:
            entities.append(MirAIeDisplayModeSelect(device, coordinator))
        else:
            disp_select_unq_id = f"{device.id}_display_mode"
            disp_select_entity_id = ent_reg.async_get_entity_id("select", DOMAIN, disp_select_unq_id)
            if disp_select_entity_id:
                ent_reg.async_remove(disp_select_entity_id)
                LOGGER.info("Cleaned up unsupported display mode select %s for device %s", disp_select_entity_id, device.id)

    async_add_entities(entities)


class MirAIeDisplayModeSelect(SelectEntity):
    """Representation of a MirAIe Display Mode selector."""

    def __init__(self, device: MirAIeDevice, coordinator: MirAIeDeviceCoordinator | None = None) -> None:
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "display_mode"
        self._attr_unique_id = f"{device.id}_display_mode"
        self.device = device
        self.coordinator = coordinator

    @property
    def options(self) -> list[str]:
        """Return selectable display mode options."""
        supports_night = getattr(self.device, "supports_night_display", False)
        if not supports_night and self.coordinator and hasattr(self.coordinator, "capabilities"):
            supports_night = bool(self.coordinator.capabilities.get("supports_night_display", False))

        # If pure manual IR is configured without Wi-Fi failover, Night mode is physically unavailable via IR
        if self.coordinator:
            primary = getattr(self.coordinator, "primary_backend", "cloud")
            submode = getattr(self.coordinator, "hybrid_submode", "auto")
            secondary = getattr(self.coordinator, "secondary_backend", None)
            if primary == "ir" and (submode == "manual" or secondary in ("none", None)):
                supports_night = False

        if supports_night:
            return ["on", "night", "off"]
        return ["on", "off"]

    @property
    def current_option(self) -> str | None:
        """Return current display mode."""
        if self.coordinator and "display" in self.coordinator.state:
            val = self.coordinator.state.get("display")
            if isinstance(val, bool):
                return "on" if val else "off"
            if isinstance(val, str) and val in ("on", "night", "off"):
                return val

        st = getattr(self.device, "status", None)
        mode = getattr(st, "display_mode", None)
        if mode == DisplayMode.NIGHT:
            return "night"
        if mode == DisplayMode.ON:
            return "on"
        if mode == DisplayMode.OFF:
            return "off"
        return "off"

    @property
    def icon(self) -> str | None:
        """Return icon depending on display state."""
        opt = self.current_option
        if opt == "night":
            return "mdi:weather-night"
        if opt == "on":
            return "mdi:eye-outline"
        return "mdi:eye-off-outline"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return build_device_info(self.device)

    @property
    def available(self) -> bool:
        """Return True if entity is available across UDP, Cloud, and IR channels."""
        return is_device_available(self.device, self.coordinator)

    async def _send_display_mode_command(self, target_option: str) -> None:
        if target_option == "night":
            target_mode = DisplayMode.NIGHT
        elif target_option == "on":
            target_mode = DisplayMode.ON
        else:
            target_mode = DisplayMode.OFF

        coord = self.coordinator
        hub = getattr(coord, "hub", None) if coord else None
        broker = getattr(hub, "broker", None) if hub else None
        broker_connected = broker.connected.is_set() if (broker and hasattr(broker, "connected")) else True
        is_cloud_offline = (not getattr(getattr(self.device, "status", None), "is_online", True)) or (not broker_connected)
        has_wifi = getattr(coord, "has_wifi", True) if coord else True
        supports_udp = getattr(self.device, "supports_local_udp", False) is True
        has_udp = supports_udp and getattr(self.device, "udp_transport", None) is not None
        primary = getattr(coord, "primary_backend", "cloud") if coord else "cloud"
        secondary = getattr(coord, "secondary_backend", None) if coord else None
        submode = getattr(coord, "hybrid_submode", "auto") if coord else "auto"
        blaster_id = getattr(coord, "blaster_entity_id", None) if coord else None

        # Build candidate priority list (deduplicating while preserving order)
        # In Manual Control, failover is disabled: limit to primary transport only
        candidate_backends = [primary] if submode == "manual" else [primary, secondary]
        order = []
        for b in candidate_backends:
            if b and b != "none" and b not in order:
                order.append(b)

        # Preserve legacy Cloud fallback for older IR-primary entries without explicit secondary backend
        if submode != "manual" and primary == "ir" and not secondary and has_wifi and "cloud" not in order:
            order.append("cloud")

        first_transport = order[0] if order else "cloud"
        origin_label = "Local UDP" if first_transport == "local_udp" else ("IR" if first_transport == "ir" else "Cloud")

        raw_prev_display = coord.state.get("display") if coord else None
        prev_display = (
            ("on" if raw_prev_display else "off")
            if isinstance(raw_prev_display, bool)
            else raw_prev_display
        )

        if coord:
            coord.async_optimistic_update(
                display=target_option,
                origin=origin_label,
            )
            if hasattr(self, "async_write_ha_state"):
                try:
                    self.async_write_ha_state()
                except Exception:
                    pass

        dispatched = False
        for transport in order:
            if transport == "local_udp" and has_udp:
                try:
                    await self.device.set_display_mode(target_mode, transport="udp")
                    if coord:
                        coord.state["last_controlled_by"] = "Local UDP"
                    dispatched = True
                    break
                except Exception as err:
                    LOGGER.warning("Local UDP display command failed for %s: %s", self.device.id, err)
            elif transport == "cloud":
                try:
                    await self.device.set_display_mode(target_mode, transport="cloud")
                    if coord:
                        coord.state["last_controlled_by"] = "Cloud"
                    dispatched = True
                    break
                except Exception as err:
                    LOGGER.warning("Cloud display command failed for %s: %s", self.device.id, err)
            elif transport == "ir" and blaster_id:
                # Panasonic AC physical remotes only toggle between ON and OFF; Night mode cannot be dispatched via IR
                if target_option == "night":
                    LOGGER.debug("Skipping IR dispatch for device %s: Night mode is not supported by IR", self.device.id)
                    continue
                # If target state already matches physical state, skip blind toggle to prevent inverting
                current_disp = prev_display
                target_is_on = (target_option != "off")
                current_is_on = (current_disp in ("on", "night"))
                if current_disp is not None and target_is_on == current_is_on:
                    LOGGER.debug("Skipping IR display toggle for %s: display is already in state %s", self.device.id, current_disp)
                    dispatched = True
                    break
                try:
                    origin = "IR Blaster" if primary == "ir" else "IR Failover (Offline)"
                    success = await coord.async_dispatch_ir_command(
                        mode="display",
                        display=(target_option != "off"),
                        origin=origin,
                    )
                    if success:
                        if coord:
                            coord.state["last_controlled_by"] = "IR Blaster"
                        dispatched = True
                        break
                    LOGGER.warning("IR display command failed for %s", self.device.id)
                except Exception as err:
                    LOGGER.warning("IR display command error for %s: %s", self.device.id, err)

        if (
            not dispatched
            and coord
            and submode == "auto"
            and secondary != "none"
            and blaster_id
            and "ir" not in order
            and target_option != "night"
        ):
            current_disp = prev_display
            target_is_on = (target_option != "off")
            current_is_on = (current_disp in ("on", "night"))
            if current_disp is not None and target_is_on == current_is_on:
                LOGGER.debug("Skipping auto failover IR display toggle for %s: already %s", self.device.id, current_disp)
                dispatched = True
            else:
                LOGGER.info("Auto Failover triggered: Transmitting IR display command for %s", self.device.id)
                dispatched = await coord.async_dispatch_ir_command(mode="display", display=(target_option != "off"), origin="IR Failover")

        if not dispatched:
            if coord and raw_prev_display is not None:
                coord.state["display"] = raw_prev_display
                if hasattr(coord, "_notify_listeners"):
                    coord._notify_listeners()
            raise HomeAssistantError(f"Failed to set display mode to {target_option} across configured transports")

    async def async_select_option(self, option: str) -> None:
        """Change the selected display mode."""
        if option not in self.options:
            raise ValueError(f"Invalid option {option}. Valid options: {self.options}")
        await self._send_display_mode_command(option)

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        LOGGER.debug("Successfully added display mode select to HA")
        if self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
            async_attach_blaster_listener(self, self.coordinator)

        def _safe_device_cb(*args, **kwargs) -> None:
            safe_async_write_ha_state(self)

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Entity being removed from hass."""
        LOGGER.debug("Successfully removed display mode select from HA")
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)

