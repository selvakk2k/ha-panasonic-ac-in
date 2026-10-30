"""The MirAIe climate platform."""

from __future__ import annotations
import asyncio
import time
from typing import Any
from miraie_ac import (
    Device as MirAIeDevice,
    MirAIeHub,
    HVACMode as MHVACMode,
    FanMode,
    SwingMode,
    PresetMode,
    ConvertiMode,
)

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_NONE,
    FAN_AUTO,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    FAN_OFF,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, PRECISION_WHOLE, PRECISION_HALVES, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PRESET_CLEAN,
    V0,
    V1,
    V2,
    V3,
    V4,
    V5,
    H0,
    H1,
    H2,
    H3,
    H4,
    H5,
    SWING_AUTO,
    SWING_V_LIST,
    SWING_H_LIST,
    SWING_V_TO_CODE,
    SWING_CODE_TO_V_FRIENDLY,
    SWING_H_TO_CODE,
    SWING_CODE_TO_H_FRIENDLY,
    CONVERTI_8IN1_PRESET_MODES,
    CONVERTI_7IN1_PRESET_MODES,
    get_converti_preset_modes,
    supports_heat_mode,
)


from .logger import LOGGER
from .utils import get_devices_for_entry, async_attach_blaster_listener

PARALLEL_UPDATES = 0

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:

    """Set up the MirAIe Climate Hub."""
    hub: MirAIeHub = entry.runtime_data
    coordinators = getattr(hub, "coordinators", {})
    devices = get_devices_for_entry(hub, entry)

    entities = [MirAIeClimate(device, entry, coordinators.get(device.id)) for device in devices]

    async_add_entities(entities)



class MirAIeClimate(ClimateEntity):
    """Representation of a MirAIe Climate."""

    def __init__(self, device: MirAIeDevice, entry: ConfigEntry | None = None, coordinator=None) -> None:
        self.device = device
        self.entry = entry
        self.coordinator = coordinator
        self._attr_should_poll: bool = False
        self._attr_has_entity_name: bool = True
        self._attr_name: str | None = None

        model_number = getattr(getattr(device, "details", None), "model_number", None)

        has_heat = supports_heat_mode(model_number)
        has_h_vane = True
        converti_presets = get_converti_preset_modes(model_number)

        if coordinator and hasattr(coordinator, "capabilities") and coordinator.capabilities:
            caps = coordinator.capabilities
            if "has_heat_mode" in caps:
                has_heat = bool(caps["has_heat_mode"])
            if "h_vane_enabled" in caps:
                has_h_vane = bool(caps["h_vane_enabled"])
            if "converti_type" in caps:
                c_tier = str(caps["converti_type"])
                if c_tier == "none":
                    converti_presets = []
                elif c_tier == "8-in-1":
                    converti_presets = list(CONVERTI_8IN1_PRESET_MODES)
                elif c_tier == "7-in-1":
                    converti_presets = list(CONVERTI_7IN1_PRESET_MODES)

        self._attr_hvac_modes = [
            HVACMode.AUTO,
            HVACMode.COOL,
        ]
        if has_heat:
            self._attr_hvac_modes.append(HVACMode.HEAT)
        self._attr_hvac_modes += [
            HVACMode.OFF,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        ]

        self._base_preset_modes = [
            PRESET_NONE,
            PRESET_ECO,
            PRESET_BOOST,
        ] + converti_presets

        self._attr_fan_mode = "auto"
        self._attr_fan_modes = [
            FAN_AUTO,
            FAN_LOW,
            FAN_MEDIUM,
            FAN_HIGH,
            "quiet",
        ]
        self._attr_swing_modes = list(SWING_V_LIST)

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )

        if has_h_vane:
            self._attr_swing_horizontal_modes = list(SWING_H_LIST)
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        else:
            self._attr_swing_horizontal_modes = []

        self._attr_supported_features = features
        self._attr_max_temp = 30.0
        self._attr_min_temp = 16.0
        self._attr_target_temperature_step = 1.0
        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = PRECISION_WHOLE
        self._attr_unique_id = device.id
        self._auto_mode_switch_time: float = 0.0
        self._pending_auto_temp_task: asyncio.Task | None = None

    async def _send_command_via_hybrid(
        self,
        mode: str | None = None,
        temp: int | None = None,
        fan: str | None = None,
        v_vane: str | None = None,
        h_vane: str | None = None,
        eco: bool | None = None,
        nanoe: bool | None = None,
        preset: str | None = None,
        cloud_coro=None,
    ) -> None:
        """Route command through Hybrid Coordinator based on active primary_backend and failover rules."""
        coord = getattr(self, "coordinator", None)
        hub = getattr(coord, "hub", None) if coord else None
        broker = getattr(hub, "broker", None) if hub else None
        broker_connected = broker.connected.is_set() if (broker and hasattr(broker, "connected")) else True
        is_cloud_offline = (not getattr(getattr(self.device, "status", None), "is_online", True)) or (not broker_connected)
        use_ir_first = (
            coord and (
                getattr(coord, "primary_backend", "cloud") == "ir"
                or (is_cloud_offline and getattr(coord, "hybrid_submode", "auto") == "auto" and getattr(coord, "blaster_entity_id", None))
            )
        )

        if coord:
            coord.async_optimistic_update(
                mode=mode,
                target_temp=temp,
                fan=fan,
                v_vane=v_vane,
                h_vane=h_vane,
                eco=eco,
                nanoe=nanoe,
                preset=preset,
                origin="IR" if use_ir_first else "Cloud",
            )
            if hasattr(self, "async_write_ha_state"):
                try:
                    self.async_write_ha_state()
                except Exception:
                    pass

        if use_ir_first and coord:
            success = await coord.async_dispatch_ir_command(
                mode=mode,
                target_temp=temp,
                fan=fan,
                v_vane=v_vane,
                h_vane=h_vane,
                eco=eco,
                nanoe=nanoe,
                preset=preset,
                origin="IR Blaster" if getattr(coord, "primary_backend", "cloud") == "ir" else "IR Failover (Offline)",
            )
            if success:
                if cloud_coro and hasattr(cloud_coro, "close"):
                    try:
                        cloud_coro.close()
                    except Exception:
                        pass
                return
            LOGGER.warning("IR command failed for %s, falling back to Cloud", self.device.id)

        if cloud_coro:
            async def _run_cloud_coro():
                try:
                    await cloud_coro
                except Exception as err:
                    LOGGER.warning("Cloud command failed for %s: %s", self.device.id, err)
                    if coord and getattr(coord, "hybrid_submode", "auto") == "auto" and getattr(coord, "blaster_entity_id", None):
                        LOGGER.info("Auto Failover triggered: Transmitting IR command for %s", self.device.id)
                        await coord.async_dispatch_ir_command(
                            mode=mode,
                            target_temp=temp,
                            fan=fan,
                            v_vane=v_vane,
                            h_vane=h_vane,
                            eco=eco,
                            nanoe=nanoe,
                            origin="IR Failover",
                        )

            if hasattr(self, "hass") and self.hass:
                self.hass.async_create_task(_run_cloud_coro())
            else:
                await _run_cloud_coro()

    @property
    def name(self) -> str | None:
        """Return None as primary climate entity so device name is used directly."""
        return None

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        return DOMAIN

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        return "mdi:air-conditioner"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.device.id)
            },
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    @property
    def assumed_state(self) -> bool:
        """Return True if entity state is assumed (IR control mode)."""
        coord = getattr(self, "coordinator", None)
        if coord:
            if not coord.has_wifi or getattr(coord, "primary_backend", "cloud") == "ir":
                return True
        entry_data = getattr(self.entry, "data", {}) if hasattr(self, "entry") and self.entry else {}
        if isinstance(entry_data, dict) and entry_data.get("is_ir_only", False):
            return True
        return False

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Dual-control (Wi-Fi + IR Blaster): Available if EITHER channel is online.
        Only transitions to unavailable when BOTH channels are unavailable.
        Single-control (Wi-Fi only or IR only): Available only if its sole channel is online.
        """
        coord = getattr(self, "coordinator", None)
        has_wifi = getattr(coord, "has_wifi", True) if coord else True
        has_cloud = bool(getattr(getattr(self.device, "status", None), "is_online", False))
        if coord and hasattr(coord, "hub") and getattr(coord.hub, "broker", None):
            broker = coord.hub.broker
            if hasattr(broker, "connected") and not broker.connected.is_set():
                has_cloud = False

        blaster_id = coord.blaster_entity_id if coord else None
        has_blaster = bool(blaster_id)
        has_ir = bool(coord.is_ir_blaster_available) if (has_blaster and coord) else False

        if has_blaster and has_wifi:
            return has_cloud or has_ir
        elif has_blaster and not has_wifi:
            return has_ir
        return has_cloud

    @property
    def hvac_mode(self) -> HVACMode | None:
        coord = getattr(self, "coordinator", None)
        if coord and coord.state:
            power_mode = coord.state.get("power", "off")
            if power_mode == "off":
                return HVACMode.OFF
            mode = coord.state.get("mode", "cool")
            if mode in ("powerful", "boost", "clean", "display") or mode.startswith("converti_"):
                mode = "cool"
            if mode == "fan":
                return HVACMode.FAN_ONLY
            try:
                return HVACMode(mode)
            except ValueError:
                return HVACMode.COOL

        power_mode = self.device.status.power_mode
        if power_mode.value == "off":
            return HVACMode.OFF

        mode = self.device.status.hvac_mode.value

        if mode == "fan":
            return HVACMode.FAN_ONLY

        try:
            return HVACMode(mode)
        except ValueError:
            return HVACMode.COOL

    @property
    def min_temp(self) -> float:
        return self._attr_min_temp

    @property
    def max_temp(self) -> float:
        return self._attr_max_temp

    @property
    def current_temperature(self) -> float | None:
        # 1. External temperature sensor override from coordinator / options
        coord = getattr(self, "coordinator", None)
        if coord and getattr(coord, "temperature_sensor_entity_id", None) and self.hass:
            state_obj = self.hass.states.get(coord.temperature_sensor_entity_id)
            if state_obj and state_obj.state.lower() not in ("unknown", "unavailable", "none", "null", ""):
                try:
                    return float(state_obj.state)
                except (ValueError, TypeError):
                    pass

        # 2. Coordinator state (set via external sensor listener or cloud telemetry)
        if coord and coord.state and coord.state.get("room_temperature") is not None:
            try:
                return float(coord.state["room_temperature"])
            except (ValueError, TypeError):
                pass

        # 3. Wi-Fi device built-in hardware room temperature
        if coord and not coord.has_wifi:
            return None
        return getattr(getattr(self, "device", None), "status", None) and getattr(self.device.status, "room_temperature", None)

    @property
    def target_temperature(self) -> float | int | None:
        coord = getattr(self, "coordinator", None)
        if coord and coord.state and "temperature" in coord.state:
            return round(coord.state["temperature"])
        temp = self.device.status.temperature
        if temp is None:
            return None
        return round(temp)

    @property
    def preset_modes(self) -> list[str]:
        return self._base_preset_modes

    @property
    def preset_mode(self) -> str | None:
        coord = getattr(self, "coordinator", None)
        if coord and coord.state:
            if coord.state.get("eco"):
                return PRESET_ECO
            active_p = coord.state.get("active_preset", "none")
            if active_p in ("powerful", "boost"):
                return PRESET_BOOST
            if active_p == "clean":
                return PRESET_CLEAN
            if isinstance(active_p, str):
                if active_p.startswith("converti_") or active_p.startswith("cv_"):
                    step = active_p.split("_")[1]
                    if step in ("0", "off"):
                        return PRESET_NONE
                    return f"cv_{step}"
                if "%" in active_p:
                    import re
                    m = re.search(r"\d+", active_p)
                    step = m.group(0) if m else "0"
                    if step == "0":
                        return PRESET_NONE
                    return f"cv_{step}"
                if active_p in ("off", "none", "NONE"):
                    return PRESET_NONE
                return active_p

        if hasattr(self, "device") and hasattr(self.device, "status") and self.device.status:
            c_mode = getattr(self.device.status, "converti_mode", None)
            c_val = getattr(c_mode, "value", 0) if c_mode else 0
            if c_val not in (0, "off", "ns", "OFF", "NS"):
                return f"cv_{c_val}"

            preset = getattr(self.device.status, "preset_mode", None)
            p_val = getattr(preset, "value", "none") if preset else "none"
            if p_val in ("off", "none", "CLEAN"):
                return PRESET_NONE
            return p_val

        return PRESET_NONE

    @property
    def fan_mode(self) -> str | None:
        coord = getattr(self, "coordinator", None)
        if coord and coord.state and "fan_speed" in coord.state:
            return coord.state["fan_speed"]

        return self.device.status.fan_mode.value

    @property
    def swing_mode(self) -> str | None:
        coord = getattr(self, "coordinator", None)
        if coord and coord.state and "v_vane" in coord.state:
            v_val = coord.state["v_vane"]
            return SWING_CODE_TO_V_FRIENDLY.get(v_val, str(v_val))

        mode = self.device.status.v_swing_mode.value
        return SWING_CODE_TO_V_FRIENDLY.get(mode, SWING_CODE_TO_V_FRIENDLY.get(V0, SWING_AUTO))

    @property
    def swing_horizontal_mode(self) -> str | None:
        coord = getattr(self, "coordinator", None)
        if coord and coord.state and "h_vane" in coord.state:
            h_val = coord.state["h_vane"]
            return SWING_CODE_TO_H_FRIENDLY.get(h_val, str(h_val))

        mode = self.device.status.h_swing_mode.value
        return SWING_CODE_TO_H_FRIENDLY.get(mode, SWING_CODE_TO_H_FRIENDLY.get(H0, SWING_AUTO))


    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        raw_temp = kwargs.get("temperature")
        if raw_temp is None:
            return

        target_temp = int(round(raw_temp))
        LOGGER.debug(f"Set temperature to {target_temp}")

        # In Auto mode, physical AC firmware blinks the display for 13-15 seconds upon entering Auto mode.
        # If multiple temperature changes arrive during this window, cancel the previous pending command
        # and only dispatch the latest one once the delay finishes.
        if self._pending_auto_temp_task and not self._pending_auto_temp_task.done():
            self._pending_auto_temp_task.cancel()
            self._pending_auto_temp_task = None

        # If Eco mode is active, adjusting temperature automatically exits Eco mode
        is_eco_active = (self.preset_mode == PRESET_ECO) or bool(getattr(getattr(self, "coordinator", None), "state", {}).get("eco"))

        # Update optimistic UI state immediately for instant response
        coord = getattr(self, "coordinator", None)
        if coord:
            coord.async_optimistic_update(
                target_temp=target_temp,
                eco=False if is_eco_active else None,
                origin="IR" if getattr(coord, "primary_backend", "cloud") == "ir" else "Cloud",
            )
            if hasattr(self, "async_write_ha_state"):
                try:
                    self.async_write_ha_state()
                except Exception:
                    pass

        async def _delayed_send(wait_s: float) -> None:
            try:
                if wait_s > 0:
                    LOGGER.info("Device %s: Auto mode blinking transition active. Waiting %.1fs before setting temperature to %d", self.device.id, wait_s, target_temp)
                    await asyncio.sleep(wait_s)
                await self._send_command_via_hybrid(
                    temp=target_temp,
                    eco=False if is_eco_active else None,
                    cloud_coro=self.device.set_temperature(target_temp),
                )
            except asyncio.CancelledError:
                LOGGER.debug("Device %s: Superseded by newer temperature command to %d", self.device.id, target_temp)
                raise

        if self.hvac_mode == HVACMode.AUTO and hasattr(self, "_auto_mode_switch_time"):
            elapsed = time.monotonic() - self._auto_mode_switch_time
            if elapsed < 15.0:
                wait_time = 15.0 - elapsed
                # Run the delayed send as a task and await it so only the latest task executes
                task = asyncio.create_task(_delayed_send(wait_time))
                self._pending_auto_temp_task = task
                try:
                    await task
                except asyncio.CancelledError:
                    return
                return

        # Zero delay for non-Auto or when past the blinking transition window
        await self._send_command_via_hybrid(
            temp=target_temp,
            eco=False if is_eco_active else None,
            cloud_coro=self.device.set_temperature(target_temp),
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:

        LOGGER.debug(f"Set hvac mode to {hvac_mode}")

        if hvac_mode == HVACMode.OFF:
            await self._send_command_via_hybrid(mode="off", cloud_coro=self.device.turn_off())
        else:
            async def _cloud_turn_on_and_mode():
                if self.device.status.power_mode.value == "off":
                    await self.device.turn_on()

                if hvac_mode == HVACMode.FAN_ONLY:
                    await self.device.set_hvac_mode(MHVACMode("fan"))
                else:
                    await self.device.set_hvac_mode(MHVACMode(hvac_mode.value))

            mode_str = hvac_mode.value if hvac_mode != HVACMode.FAN_ONLY else "fan"
            temp_param = None
            fan_param = None
            coord = getattr(self, "coordinator", None)

            if hvac_mode == HVACMode.AUTO:
                self._auto_mode_switch_time = time.monotonic()
                series = getattr(coord, "capabilities", {}).get("series", "EU") if coord else "EU"
                room_t = coord.state.get("room_temperature") if coord and coord.state else None
                if series in ("EZ", "KZ") and room_t is not None and isinstance(room_t, (int, float)) and room_t < 18:
                    temp_param = 21
                else:
                    temp_param = 24
            elif hvac_mode == HVACMode.DRY:
                fan_param = "low"

            await self._send_command_via_hybrid(
                mode=mode_str,
                temp=temp_param,
                fan=fan_param,
                cloud_coro=_cloud_turn_on_and_mode(),
            )

    async def async_set_fan_mode(self, fan_mode: str) -> None:

        LOGGER.debug(f"Set fan mode to {fan_mode}")
        target_mode = "quiet" if fan_mode in ("off", "FAN_OFF", FAN_OFF) else fan_mode

        cloud_coro = self.device.set_fan_mode(FanMode(target_mode))
        await self._send_command_via_hybrid(fan=target_mode, cloud_coro=cloud_coro)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        LOGGER.debug(f"Set swing vertical mode to {swing_mode}")
        v_code = SWING_V_TO_CODE.get(swing_mode, V0)
        swing_num = 0
        if v_code == V1: swing_num = 1
        elif v_code == V2: swing_num = 2
        elif v_code == V3: swing_num = 3
        elif v_code == V4: swing_num = 4
        elif v_code == V5: swing_num = 5

        await self._send_command_via_hybrid(v_vane=v_code, cloud_coro=self.device.set_v_swing_mode(SwingMode(swing_num)))

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        LOGGER.debug(f"Set swing horizontal mode to {swing_horizontal_mode}")
        h_code = SWING_H_TO_CODE.get(swing_horizontal_mode, H0)
        swing_num = 0
        if h_code == H1: swing_num = 1
        elif h_code == H2: swing_num = 2
        elif h_code == H3: swing_num = 3
        elif h_code == H4: swing_num = 4
        elif h_code == H5: swing_num = 5

        await self._send_command_via_hybrid(h_vane=h_code, cloud_coro=self.device.set_h_swing_mode(SwingMode(swing_num)))

    async def async_set_preset_mode(self, preset_mode: str) -> None:

        LOGGER.debug(f"Set preset mode to {preset_mode}")

        eco_val = None
        mode_val = None
        target_temp_val = None
        preset_val = None
        cloud_coro = None

        if self.hvac_mode not in (HVACMode.COOL, HVACMode.OFF) and preset_mode not in (PRESET_NONE, "none", "None", None, "cv_0", "cv 0"):
            LOGGER.info("Preset '%s' requested while in %s mode; auto-switching to Cool mode", preset_mode, self.hvac_mode)
            mode_val = "cool"

        if preset_mode in (PRESET_NONE, "none", "None", None, "cv_0", "cv 0"):
            eco_val = False
            preset_val = "none"
            mode_val = "cool" if self.hvac_mode == HVACMode.COOL else (self.hvac_mode.value if isinstance(self.hvac_mode, HVACMode) else "cool")

            def _clear_converti_and_preset():
                return asyncio.gather(
                    self.device.set_converti_mode(ConvertiMode.OFF),
                    self.device.set_preset_mode(PresetMode.NONE),
                    return_exceptions=True,
                )

            cloud_coro = _clear_converti_and_preset()

        elif preset_mode == PRESET_ECO:
            eco_val = True
            preset_val = "eco"
            target_temp_val = 26
            cloud_coro = self.device.set_preset_mode(PresetMode.ECO)
        elif preset_mode in (PRESET_BOOST, "boost", "powerful", "Powerful"):
            eco_val = False
            preset_val = "powerful"
            mode_val = "powerful"
            cloud_coro = self.device.set_preset_mode(PresetMode.BOOST)
        else:
            import re
            match = re.search(r"\d+", preset_mode)
            if match and ("%" in preset_mode or "Converti" in preset_mode or preset_mode.startswith("cv_") or preset_mode.startswith("converti_")):
                perc_str = match.group(0)
                if perc_str == "0":
                    eco_val = False
                    preset_val = "none"
                    mode_val = "cool" if self.hvac_mode == HVACMode.COOL else (self.hvac_mode.value if isinstance(self.hvac_mode, HVACMode) else "cool")

                    def _clear_converti_and_preset():
                        return asyncio.gather(
                            self.device.set_converti_mode(ConvertiMode.OFF),
                            self.device.set_preset_mode(PresetMode.NONE),
                            return_exceptions=True,
                        )

                    cloud_coro = _clear_converti_and_preset()
                else:
                    eco_val = False
                    preset_val = f"cv_{perc_str}"
                    mode_val = f"converti_{perc_str}"
                    try:
                        c_enum = ConvertiMode(int(perc_str))
                        cloud_coro = self.device.set_converti_mode(c_enum)
                    except (ValueError, KeyError):
                        cloud_coro = None
            else:
                p_mode_map = {
                    PRESET_BOOST: PresetMode.BOOST,
                    PRESET_ECO: PresetMode.ECO,
                    PRESET_NONE: PresetMode.NONE,
                }
                target_preset = p_mode_map.get(preset_mode, PresetMode.NONE)
                preset_val = "none" if target_preset == PresetMode.NONE else preset_mode
                cloud_coro = self.device.set_preset_mode(target_preset)

        await self._send_command_via_hybrid(
            eco=eco_val,
            mode=mode_val,
            temp=target_temp_val,
            preset=preset_val,
            cloud_coro=cloud_coro,
        )


    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""

        LOGGER.debug("Successfully added to HA")

        if hasattr(self, "coordinator") and self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
            async_attach_blaster_listener(self, self.coordinator)

        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Entity being removed from hass."""

        LOGGER.debug("Successfully removed from HA")

        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


