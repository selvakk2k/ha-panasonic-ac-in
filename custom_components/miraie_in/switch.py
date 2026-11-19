"""The MirAIe climate platform."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import MirAIeDeviceCoordinator

from miraie_ac import (
    Device as MirAIeDevice,
    MirAIeHub,
    DisplayMode,
)

from homeassistant.components.switch import (
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    supports_nanoe,
)

from .logger import LOGGER
from .utils import (
    get_devices_for_entry,
    async_attach_blaster_listener,
    is_ir_only_entry,
    safe_async_write_ha_state,
    is_device_available,
    build_device_info,
)

PARALLEL_UPDATES = 0



async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:

    """Set up the MirAIe Climate Hub."""
    hub: MirAIeHub = entry.runtime_data
    coordinators = getattr(hub, "coordinators", {})
    ent_reg = er.async_get(hass)

    entities: list[SwitchEntity] = []
    devices = get_devices_for_entry(hub, entry)


    for device in devices:
        coordinator = coordinators.get(device.id)
        entities.append(MirAIeDisplaySwitch(device, coordinator))

        # Buzzer Switch gating & orphan cleanup
        has_buzzer = False
        if coordinator and hasattr(coordinator, "capabilities") and coordinator.capabilities and "has_buzzer" in coordinator.capabilities:
            has_buzzer = bool(coordinator.capabilities.get("has_buzzer", False))
        elif getattr(device, "supports_buzzer", False) is True:
            has_buzzer = True

        if has_buzzer:
            entities.append(MirAIeBuzzerSwitch(device, coordinator))
        else:
            buzzer_unq_id = f"{device.id}_buzzer"
            buzzer_entity_id = ent_reg.async_get_entity_id("switch", DOMAIN, buzzer_unq_id)
            if buzzer_entity_id:
                ent_reg.async_remove(buzzer_entity_id)
                LOGGER.info("Cleaned up unsupported buzzer switch %s for device %s", buzzer_entity_id, device.id)
        
        # Expose Nanoe switch only if the model supports it
        has_nanoe = False
        if coordinator and hasattr(coordinator, "capabilities") and coordinator.capabilities and "has_nanoe" in coordinator.capabilities:
            has_nanoe = bool(coordinator.capabilities.get("has_nanoe", False))
        elif getattr(device, "supports_nanoe", False) is True:
            has_nanoe = True
        else:
            model_number = getattr(getattr(device, "details", None), "model_number", None)
            has_nanoe = supports_nanoe(model_number)

        if has_nanoe:
            entities.append(MirAIeNanoeSwitch(device, coordinator))
        else:
            nanoe_unq_id = f"{device.id}_nanoe"
            nanoe_entity_id = ent_reg.async_get_entity_id("switch", DOMAIN, nanoe_unq_id)
            if nanoe_entity_id:
                ent_reg.async_remove(nanoe_entity_id)
                LOGGER.info("Cleaned up unsupported nanoe switch %s for device %s", nanoe_entity_id, device.id)

        if coordinator:
            is_ir_only = is_ir_only_entry(entry) or not coordinator.has_wifi
            has_udp = bool(
                getattr(coordinator, "has_udp", False)
                or (getattr(device, "supports_local_udp", False) and getattr(device, "udp_transport", None))
            )
            has_cloud = bool(coordinator.has_wifi and not is_ir_only and getattr(hub, "broker", None))
            has_ir = bool(coordinator.blaster_entity_id)
            transports_count = sum([has_udp, has_cloud, has_ir])
            is_hybrid = transports_count >= 2

            if is_hybrid:
                entities.append(MirAIeHybridSubmodeSwitch(device, coordinator))
                entities.append(MirAIeBackendSelectSwitch(device, coordinator))
            else:
                for suffix in ("_hybrid_submode", "_active_backend"):
                    unq_id = f"{device.id}{suffix}"
                    entity_id = ent_reg.async_get_entity_id("switch", DOMAIN, unq_id)
                    if entity_id:
                        ent_reg.async_remove(entity_id)
                        LOGGER.info("Cleaned up orphaned hybrid entity %s (single transport)", entity_id)

    async_add_entities(entities)



class MirAIeDisplaySwitch(SwitchEntity):
    """Representation of a MirAIe Display LED switch."""

    def __init__(self, device: MirAIeDevice, coordinator=None) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "display"
        self._attr_unique_id = f"{device.id}_display"
        self.device = device
        self.coordinator = coordinator

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return "mdi:eye-outline" if self.is_on else "mdi:eye-off-outline"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return build_device_info(self.device)

    @property
    def is_on(self) -> bool:
        """Return True if display is on or in night mode."""
        if self.coordinator and "display" in self.coordinator.state:
            val = self.coordinator.state.get("display")
            return val in (True, "on", "night")
        st = getattr(self.device, "status", None)
        mode = getattr(st, "display_mode", None)
        return bool(st and mode in (DisplayMode.ON, DisplayMode.NIGHT))

    @property
    def available(self) -> bool:
        """Return True if entity is available across UDP, Cloud, and IR channels."""
        return is_device_available(self.device, self.coordinator)

    async def _send_display_command(self, turn_on: bool) -> None:
        target_mode = DisplayMode.ON if turn_on else DisplayMode.OFF
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

        prev_display = coord.state.get("display") if coord else None

        if coord:
            coord.async_optimistic_update(
                display=turn_on,
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
                # If target state already matches physical state, skip blind toggle to prevent inverting
                current_disp = prev_display
                current_is_on = (current_disp in ("on", "night"))
                if current_disp is not None and turn_on == current_is_on:
                    LOGGER.debug("Skipping IR display toggle for %s: display is already %s", self.device.id, current_disp)
                    dispatched = True
                    break
                try:
                    origin = "IR Blaster" if primary == "ir" else "IR Failover (Offline)"
                    success = await coord.async_dispatch_ir_command(
                        mode="display",
                        display=turn_on,
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
        ):
            current_disp = prev_display
            current_is_on = (current_disp in ("on", "night"))
            if current_disp is not None and turn_on == current_is_on:
                LOGGER.debug("Skipping auto failover IR display toggle for %s: already %s", self.device.id, current_disp)
                dispatched = True
            else:
                LOGGER.info("Auto Failover triggered: Transmitting IR display command for %s", self.device.id)
                dispatched = await coord.async_dispatch_ir_command(mode="display", display=turn_on, origin="IR Failover")

        if not dispatched:
            if coord and prev_display is not None:
                coord.state["display"] = prev_display
                if hasattr(coord, "_notify_listeners"):
                    coord._notify_listeners()
            raise HomeAssistantError(f"Failed to set display switch to {turn_on} across configured transports")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_display_command(False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_display_command(True)

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        LOGGER.debug("Successfully added display switch to HA")
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
        LOGGER.debug("Successfully removed display switch from HA")
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeBuzzerSwitch(SwitchEntity):
    """Representation of a MirAIe Buzzer Mute/Unmute switch."""

    def __init__(self, device: MirAIeDevice, coordinator=None) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_translation_key = "buzzer"
        self._attr_unique_id = f"{device.id}_buzzer"
        self.device = device
        self.coordinator = coordinator

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return "mdi:volume-high" if self.is_on else "mdi:volume-mute"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return build_device_info(self.device)

    @property
    def is_on(self) -> bool:
        """Return True if buzzer is on (enabled)."""
        if self.coordinator and "buzzer" in self.coordinator.state:
            return bool(self.coordinator.state.get("buzzer"))
        return bool(getattr(getattr(self.device, "status", None), "buzzer", False))

    @property
    def available(self) -> bool:
        """Return True if entity is available across UDP, Cloud, and IR channels."""
        return is_device_available(self.device, self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable buzzer sound."""
        await self._send_buzzer_command(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Mute buzzer sound."""
        await self._send_buzzer_command(False)

    async def _send_buzzer_command(self, state: bool) -> None:
        if self.coordinator:
            self.coordinator.state["buzzer"] = state
            self.coordinator._notify_listeners()

        if hasattr(self.device, "set_buzzer"):
            await self.device.set_buzzer(state)
        elif self.coordinator and hasattr(self.coordinator, "hub") and getattr(self.coordinator.hub, "broker", None):
            await self.coordinator.hub.broker.set_buzzer(self.device.control_topic, state)

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        LOGGER.debug("Successfully added buzzer switch to HA")
        if self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )

        def _safe_device_cb(*args, **kwargs) -> None:
            safe_async_write_ha_state(self)

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Entity being removed from hass."""
        LOGGER.debug("Successfully removed buzzer switch from HA")
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeNanoeSwitch(SwitchEntity):
    """Switch entity for Panasonic AC nanoe-G Air Purification Control."""

    _attr_has_entity_name = True
    _attr_translation_key = "nanoe"
    _attr_icon = "mdi:air-filter"

    def __init__(
        self,
        device: MirAIeDevice,
        coordinator: MirAIeDeviceCoordinator | None = None,
        entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the switch."""
        self.device = device
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{device.id}_nanoe"

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        if self.coordinator and "nanoe" in self.coordinator.state:
            return bool(self.coordinator.state.get("nanoe"))
        st = getattr(self.device, "status", None)
        return getattr(st, "nanoe_mode", "off") == "on" if st else False

    @property
    def available(self) -> bool:
        """Return True if entity is available across UDP, Cloud, and IR channels."""
        return is_device_available(self.device, self.coordinator)

    async def _send_nanoe_command(self, turn_on: bool) -> None:
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

        prev_nanoe = coord.state.get("nanoe") if coord else None

        if coord:
            coord.async_optimistic_update(
                nanoe=turn_on,
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
                    await self.device.set_nanoe(turn_on, transport="udp")
                    if coord:
                        coord.state["last_controlled_by"] = "Local UDP"
                    dispatched = True
                    break
                except Exception as err:
                    LOGGER.warning("Local UDP nanoe command failed for %s: %s", self.device.id, err)
            elif transport == "cloud":
                try:
                    await self.device.set_nanoe(turn_on, transport="cloud")
                    if coord:
                        coord.state["last_controlled_by"] = "Cloud"
                    dispatched = True
                    break
                except Exception as err:
                    LOGGER.warning("Cloud nanoe command failed for %s: %s", self.device.id, err)
            elif transport == "ir" and blaster_id:
                # In failover mode, do not send Nanoe IR frames when AC is off;
                # full frame transmission powers the AC on
                if primary != "ir" and coord and coord.state.get("power") != "on":
                    LOGGER.debug("Skipping IR failover nanoe command for %s: AC is powered off", self.device.id)
                    continue
                try:
                    origin = "IR Blaster" if primary == "ir" else "IR Failover (Offline)"
                    success = await coord.async_dispatch_ir_command(
                        nanoe=turn_on,
                        origin=origin,
                    )
                    if success:
                        if coord:
                            coord.state["last_controlled_by"] = "IR Blaster"
                        dispatched = True
                        break
                    LOGGER.warning("IR nanoe command failed for %s", self.device.id)
                except Exception as err:
                    LOGGER.warning("IR nanoe command error for %s: %s", self.device.id, err)

        if (
            not dispatched
            and coord
            and submode == "auto"
            and secondary != "none"
            and blaster_id
            and "ir" not in order
            and coord.state.get("power") == "on"
        ):
            LOGGER.info("Auto Failover triggered: Transmitting IR nanoe command for %s", self.device.id)
            dispatched = await coord.async_dispatch_ir_command(nanoe=turn_on, origin="IR Failover")

        if not dispatched:
            if coord and prev_nanoe is not None:
                coord.state["nanoe"] = prev_nanoe
                if hasattr(coord, "_notify_listeners"):
                    coord._notify_listeners()
            raise HomeAssistantError(f"Failed to set Nanoe state to {turn_on} across configured transports")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_nanoe_command(False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_nanoe_command(True)

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        LOGGER.debug("Successfully added Nanoe switch to HA")
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
        LOGGER.debug("Successfully removed Nanoe switch from HA")
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)



from homeassistant.helpers.restore_state import RestoreEntity
from .const import (
    CONF_PRIMARY_BACKEND,
    CONF_SECONDARY_BACKEND,
    CONF_HYBRID_SUBMODE,
)


class MirAIeHybridSubmodeSwitch(SwitchEntity, RestoreEntity):
    """Switch toggling between Hybrid Automatic and Manual control mode."""

    def __init__(self, device: MirAIeDevice, coordinator) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_name = "Hybrid Automatic Control"
        self._attr_translation_key = "hybrid_submode"
        self._attr_unique_id = f"{device.id}_hybrid_submode"
        self.device = device
        self.coordinator = coordinator

    @property
    def icon(self) -> str:
        return "mdi:auto-fix" if self.is_on else "mdi:hand-back-right"

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.device)

    @property
    def is_on(self) -> bool:
        return self.coordinator.hybrid_submode == "auto"

    def _persist_option(self, option_key: str, option_val: str) -> None:
        entry = self.coordinator.hass.config_entries.async_get_entry(self.coordinator.entry_id)
        if not entry:
            return
        options = dict(entry.options)
        devices_opt = dict(options.get("devices", {}))
        dev_opt = dict(devices_opt.get(self.coordinator.device_id, {}))
        dev_opt[option_key] = option_val
        devices_opt[self.coordinator.device_id] = dev_opt
        options["devices"] = devices_opt
        self.coordinator._suppress_reload = True
        self.coordinator.hass.config_entries.async_update_entry(entry, options=options)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.hybrid_submode = "auto"
        self._persist_option(CONF_HYBRID_SUBMODE, "auto")
        self.async_write_ha_state()
        if hasattr(self.coordinator, "_notify_listeners"):
            self.coordinator._notify_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.hybrid_submode = "manual"
        self._persist_option(CONF_HYBRID_SUBMODE, "manual")
        self.async_write_ha_state()
        if hasattr(self.coordinator, "_notify_listeners"):
            self.coordinator._notify_listeners()

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to coordinator state updates."""
        await super().async_added_to_hass()
        if hasattr(self, "coordinator") and self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
        # Ensure switch state reflects current coordinator hybrid_submode
        self.async_write_ha_state()


class MirAIeBackendSelectSwitch(SwitchEntity, RestoreEntity):
    """Switch toggling active primary backend transport (Cloud vs IR)."""

    def __init__(self, device: MirAIeDevice, coordinator) -> None:
        self._attr_should_poll: bool = False
        self._attr_has_entity_name = True
        self._attr_name = "Primary Transport Backend"
        self._attr_translation_key = "active_backend"
        self._attr_unique_id = f"{device.id}_active_backend"
        self.device = device
        self.coordinator = coordinator

    @property
    def icon(self) -> str:
        primary = getattr(self.coordinator, "primary_backend", "cloud")
        if primary == "local_udp":
            return "mdi:lan-connect"
        if primary == "ir":
            return "mdi:remote"
        return "mdi:cloud-sync"

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self.device)

    @property
    def is_on(self) -> bool:
        has_udp = bool(getattr(self.coordinator, "has_udp", False))
        if has_udp:
            return self.coordinator.primary_backend == "local_udp"
        return self.coordinator.primary_backend == "cloud"

    def _persist_option(self, option_key: str, option_val: str) -> None:
        entry = self.coordinator.hass.config_entries.async_get_entry(self.coordinator.entry_id)
        if not entry:
            return
        options = dict(entry.options)
        devices_opt = dict(options.get("devices", {}))
        dev_opt = dict(devices_opt.get(self.coordinator.device_id, {}))
        dev_opt[option_key] = option_val
        devices_opt[self.coordinator.device_id] = dev_opt
        options["devices"] = devices_opt
        self.coordinator._suppress_reload = True
        self.coordinator.hass.config_entries.async_update_entry(entry, options=options)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.coordinator.hybrid_submode == "auto":
            self.coordinator.hybrid_submode = "manual"
            self._persist_option(CONF_HYBRID_SUBMODE, "manual")
            LOGGER.info("External touch on backend switch: Flipped hybrid mode to Manual for %s", self.device.id)
        has_udp = bool(getattr(self.coordinator, "has_udp", False))
        target_backend = "local_udp" if has_udp else "cloud"
        former_primary = self.coordinator.primary_backend
        current_secondary = getattr(self.coordinator, "secondary_backend", None)
        self.coordinator.primary_backend = target_backend
        self._persist_option(CONF_PRIMARY_BACKEND, target_backend)
        if target_backend == current_secondary and former_primary and former_primary != target_backend and current_secondary != "none":
            self.coordinator.secondary_backend = former_primary
            self._persist_option(CONF_SECONDARY_BACKEND, former_primary)
        self.async_write_ha_state()
        if hasattr(self.coordinator, "_notify_listeners"):
            self.coordinator._notify_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.coordinator.hybrid_submode == "auto":
            self.coordinator.hybrid_submode = "manual"
            self._persist_option(CONF_HYBRID_SUBMODE, "manual")
            LOGGER.info("External touch on backend switch: Flipped hybrid mode to Manual for %s", self.device.id)
        has_udp = bool(getattr(self.coordinator, "has_udp", False))
        hub = getattr(self.coordinator, "hub", None)
        has_cloud = bool(hub and getattr(hub, "broker", None))
        target_backend = "cloud" if (has_udp and has_cloud) else "ir"
        former_primary = self.coordinator.primary_backend
        current_secondary = getattr(self.coordinator, "secondary_backend", None)
        self.coordinator.primary_backend = target_backend
        self._persist_option(CONF_PRIMARY_BACKEND, target_backend)
        if target_backend == current_secondary and former_primary and former_primary != target_backend and current_secondary != "none":
            self.coordinator.secondary_backend = former_primary
            self._persist_option(CONF_SECONDARY_BACKEND, former_primary)
        self.async_write_ha_state()
        if hasattr(self.coordinator, "_notify_listeners"):
            self.coordinator._notify_listeners()

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to coordinator state updates."""
        await super().async_added_to_hass()
        if hasattr(self, "coordinator") and self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
        # Ensure switch state reflects current coordinator primary_backend
        self.async_write_ha_state()
