"""Push-Based Two-Axis State Coordinator for Panasonic MirAIe AC Integration (2.0 Hybrid Release)."""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

from .const import NON_RESYNCABLE_IR_MODES
from .logger import LOGGER
try:
    from panasonic_ac_models import ACModelLookup, generate_ir_code, decode_ir_code  # type: ignore[import-not-found, import-untyped]
except ImportError:
    from .panasonic_ac_models import ACModelLookup, generate_ir_code, decode_ir_code


import re


def is_esphome_2026_10_or_newer(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Check if the emitter entity is backed by ESPHome 2026.10+ firmware."""
    if not entity_id or hass is None:
        return False
    try:
        from homeassistant.helpers import entity_registry as er, device_registry as dr

        ent_reg = er.async_get(hass) if hasattr(er, "async_get") else None
        dev_reg = dr.async_get(hass) if hasattr(dr, "async_get") else None
        if not ent_reg or not dev_reg:
            return False

        entry = ent_reg.async_get(entity_id) if hasattr(ent_reg, "async_get") else None
        if not entry or not getattr(entry, "device_id", None):
            return False
        if getattr(entry, "platform", None) != "esphome":
            return False

        device = dev_reg.async_get(entry.device_id) if hasattr(dev_reg, "async_get") else None
        if not device or not getattr(device, "sw_version", None):
            return False

        match = re.match(r"^(\d+)\.(\d+)", str(device.sw_version).strip())
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            return (year, month) >= (2026, 10)
    except Exception as err:
        LOGGER.debug("Could not determine ESPHome firmware version for %s: %s", entity_id, err)
    return False


def is_blaster_available_by_sensor(state_val: Any, entity_id: str | None) -> bool:
    """Determine if blaster is available based on an availability or cutoff sensor."""
    if state_val is None or not entity_id:
        return True
    s = str(getattr(state_val, "state", state_val)).lower()
    if s in (STATE_UNAVAILABLE, STATE_UNKNOWN, "none", ""):
        return False
    if "cutoff" in entity_id.lower():
        # Cutoff switch: ON = Cut off (unavailable), OFF = Connected/Normal (available)
        return s not in ("on", "true", "1")
    # Standard availability / ping / power sensor: ON = Available, OFF = Unavailable
    return s in ("on", "home", "connected", "true", "1")


class MirAIeDeviceCoordinator:
    """State Coordinator managing push-based state for a single Panasonic AC device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str = "",
        device_id: str = "",
        model_code: str = "",
        has_wifi: bool = True,
        control_mode: str = "cloud",  # "cloud", "ir", "hybrid"
        primary_backend: str = "cloud",  # "cloud" or "ir"
        hybrid_submode: str = "auto",  # "auto" or "manual"
        blaster_entity_id: Optional[str] = None,
        receiver_entity_id: Optional[str] = None,
        temperature_sensor_entity_id: Optional[str] = None,
        availability_entity_id: Optional[str] = None,
        ir_format: str = "auto",
        lookup=None,
        subentry_id: Optional[str] = None,  # Backward-compatible alias
    ):
        self.hass = hass
        resolved_entry_id = entry_id or subentry_id or ""
        self.entry_id = resolved_entry_id
        self.subentry_id = resolved_entry_id  # Kept as alias
        self.device_id = device_id
        self.model_code = model_code
        self.has_wifi = has_wifi
        self.control_mode = control_mode
        self.primary_backend = primary_backend
        self.hybrid_submode = hybrid_submode
        self.blaster_entity_id = blaster_entity_id
        self.receiver_entity_id = receiver_entity_id
        self.temperature_sensor_entity_id = temperature_sensor_entity_id
        self.availability_entity_id = availability_entity_id
        self.ir_format = ir_format
        self.hub: Any = None
        self._unsub_receiver: Optional[Callable[[], None]] = None
        self._unsub_native_receiver: Optional[Callable[[], None]] = None
        self._unsub_retry: Optional[Callable[[], None]] = None
        self._unsub_event_bus: list[Callable[[], None]] = []
        self._unsub_temp: Optional[Callable[[], None]] = None
        self._last_physical_rx_timestamp: float = 0.0

        # Resolve hardware capabilities
        self.lookup = lookup if lookup is not None else ACModelLookup()
        self.capabilities = self.lookup.get_capabilities(model_code)
        if "swing_type" in self.capabilities:
            self.capabilities["h_vane_enabled"] = 0 if self.capabilities["swing_type"] == "2-way" else 1
        elif "h_vane_enabled" not in self.capabilities:
            self.capabilities["h_vane_enabled"] = 1
        if "has_buzzer" not in self.capabilities:
            self.capabilities["has_buzzer"] = False

        self.model_capabilities: Dict[str, Any] = {}

        # State storage
        # For cloud-capable Wi-Fi devices, always start as Cloud even if an IR blaster is also configured.
        # "IR Blaster" / "IR Remote" are set at runtime when a command or physical press is detected.
        init_origin = "Cloud" if self.has_wifi else "IR Blaster"
        self.state: Dict[str, Any] = {
            "power": "off",
            "mode": "cool",
            "active_preset": "none",
            "temperature": 24,
            "fan_speed": "low",
            "v_vane": "V1",
            "h_vane": "H0",
            "eco": False,
            "nanoe": False,
            "display": "on",
            "buzzer": False,
            "error_code": "OK",
            "warning_code": "",
            "filter_clean_alert": False,
            "last_controlled_by": init_origin,
            "provisional": False,
        }

        # Operational diagnostics
        self.cloud_mqtt_connected = False
        self.device_online = False
        self.ir_blaster_available = False
        self._working_ir_format: Optional[str] = (
            ir_format if ir_format and ir_format.lower() not in ("auto", "") else None
        )
        self._is_esphome_blaster: bool = False

        self._last_ir_command_timestamp: float = 0.0
        self._last_ir_command_source: str = "Init"
        self._last_requested_ir_params: Optional[Dict[str, Any]] = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def is_esphome_2026_10_or_newer(self) -> bool:
        """Return True if the configured IR blaster runs ESPHome 2026.10 or newer."""
        if not self.blaster_entity_id:
            return False
        return is_esphome_2026_10_or_newer(self.hass, self.blaster_entity_id)

    @property
    def is_blaster_available_by_sensor(self) -> bool:
        """Return True if availability/cutoff sensor indicates the blaster is available."""
        if not self.availability_entity_id:
            return True
        if hasattr(self, "hass") and self.hass.states:
            st = self.hass.states.get(self.availability_entity_id)
            if st is not None:
                return is_blaster_available_by_sensor(getattr(st, "state", st), self.availability_entity_id)
        return True

    @property
    def is_ir_blaster_available(self) -> bool:
        """Return True if IR blaster is available in HA and not cut off by sensor."""
        if not self.blaster_entity_id:
            return False
        avail = self.ir_blaster_available
        if not avail and hasattr(self, "hass") and self.hass.states:
            st = self.hass.states.get(self.blaster_entity_id)
            if st is not None:
                raw_st = str(getattr(st, "state", st)).lower()
                avail = raw_st not in (STATE_UNAVAILABLE, STATE_UNKNOWN, "none", "")
        return bool(avail and self.is_blaster_available_by_sensor)

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Listen for coordinator state updates."""
        self._listeners.append(update_callback)

        def _remove_listener():
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify all entities of a state update."""
        for update_callback in self._listeners:
            update_callback()

    @callback
    def update_model_capabilities(self, model_caps: Dict[str, Any]) -> None:
        """Merge dynamic cloud model capabilities into coordinator capabilities."""
        if not model_caps or not isinstance(model_caps, dict):
            return
        self.model_capabilities = model_caps
        ctrl_caps = model_caps.get("controlCapabilities", {})
        if "BuzzerControl" in ctrl_caps:
            self.capabilities["has_buzzer"] = True
        else:
            self.capabilities["has_buzzer"] = False

        if "HorizontalVaneControl" in ctrl_caps:
            self.capabilities["h_vane_enabled"] = 1
        elif "HorizontalVaneControl" in model_caps.get("displayCapabilities", {}):
            self.capabilities["h_vane_enabled"] = 1
        elif ctrl_caps:
            self.capabilities["h_vane_enabled"] = 0

        self._notify_listeners()

    async def async_handle_cloud_update(self, cloud_data: Dict[str, Any]) -> None:
        """Process incoming Cloud MQTT state payload.

        Cloud telemetry serves as confirmation. Incoming cloud updates within the
        IR command grace window do not overwrite optimistic IR temperature or origin.
        """
        LOGGER.debug("Device %s: Cloud state update received: %s", self.device_id, cloud_data)

        now = time.monotonic()
        last_ir_ts = max(
            getattr(self, "_last_ir_command_timestamp", 0.0),
            getattr(self, "_last_physical_rx_timestamp", 0.0),
        )
        in_ir_grace_window = (last_ir_ts > 0.0) and (0.0 <= (now - last_ir_ts) < 8.0)

        # Map cloud payload keys
        if "pwr" in cloud_data:
            new_power = "on" if str(cloud_data["pwr"]).lower() in ["on", "1", "true"] else "off"
            if not in_ir_grace_window or new_power == self.state.get("power"):
                self.state["power"] = new_power
        if "acdc" in cloud_data:
            new_disp = "on" if str(cloud_data["acdc"]).lower() in ["on", "1", "true"] else "off"
            if not in_ir_grace_window or new_disp == self.state.get("display"):
                self.state["display"] = new_disp
        if "md" in cloud_data:
            md_val = str(cloud_data["md"]).lower()
            if md_val not in ("powerful", "boost", "clean") and not md_val.startswith("converti_"):
                if not in_ir_grace_window or md_val == self.state.get("mode"):
                    self.state["mode"] = md_val
                    if md_val != "cool":
                        self.state["active_preset"] = "none"
                        self.state["converti"] = "cv_off"
                        self.state["eco"] = False

        conv_val = cloud_data.get("converti")
        preset_val = str(cloud_data.get("preset", "")).lower()
        eco_val = str(cloud_data.get("acec", "")).lower() in ["on", "1", "true"]

        if not in_ir_grace_window:
            if eco_val:
                self.state["eco"] = True
                self.state["active_preset"] = "eco"
                self.state["converti"] = "cv_off"
                self.state["temperature"] = 26
            elif conv_val and int(conv_val) > 0:
                self.state["eco"] = False
                self.state["converti"] = f"cv_{conv_val}"
                self.state["active_preset"] = f"cv_{conv_val}"
            elif preset_val in ("boost", "powerful"):
                self.state["eco"] = False
                self.state["active_preset"] = "powerful"
                self.state["converti"] = "cv_off"
            elif preset_val == "clean":
                self.state["eco"] = False
                self.state["active_preset"] = "clean"
                self.state["converti"] = "cv_off"
            else:
                self.state["eco"] = False
                self.state["active_preset"] = "none"
                self.state["converti"] = "cv_off"

        if "tset" in cloud_data:
            try:
                cloud_temp = int(float(cloud_data["tset"]))
                if not in_ir_grace_window or cloud_temp == self.state.get("temperature"):
                    self.state["temperature"] = cloud_temp
            except (ValueError, TypeError):
                pass

        if "acfs" in cloud_data:
            if not in_ir_grace_window or str(cloud_data["acfs"]).lower() == self.state.get("fan_speed"):
                self.state["fan_speed"] = str(cloud_data["acfs"]).lower()
        if "acvs" in cloud_data:
            if not in_ir_grace_window or str(cloud_data["acvs"]).upper() == self.state.get("v_vane"):
                self.state["v_vane"] = str(cloud_data["acvs"]).upper()
        if "achs" in cloud_data:
            if not in_ir_grace_window or str(cloud_data["achs"]).upper() == self.state.get("h_vane"):
                self.state["h_vane"] = str(cloud_data["achs"]).upper()
        if "acngs" in cloud_data:
            self.state["nanoe"] = str(cloud_data["acngs"]).lower() in ["on", "1", "true"]
        if "bzr" in cloud_data:
            self.state["buzzer"] = str(cloud_data["bzr"]).lower() in ["on", "1", "true"]
        if "errors" in cloud_data:
            err = str(cloud_data["errors"]).strip()
            self.state["error_code"] = err if err else "OK"
        if "warnings" in cloud_data:
            self.state["warning_code"] = str(cloud_data["warnings"]).strip()
        if "acfc" in cloud_data:
            self.state["filter_clean_alert"] = str(cloud_data["acfc"]).lower() in ["on", "1", "true"]

        # Origin tracking: preserve "IR" origins during active IR control
        if not in_ir_grace_window:
            if "pwr" in cloud_data or "md" in cloud_data or "tset" in cloud_data:
                self._last_ir_command_source = "Cloud"
                self._last_requested_ir_params = None
            if self.state.get("last_controlled_by") not in ("IR Blaster", "IR Remote", "IR Failover", "IR Failover (Offline)"):
                self.state["last_controlled_by"] = "Cloud"

        self.state["provisional"] = False
        self.device_online = True

        self._notify_listeners()

    async def async_dispatch_ir_command(
        self,
        mode: Optional[str] = None,
        target_temp: Optional[int] = None,
        fan: Optional[str] = None,
        v_vane: Optional[str] = None,
        h_vane: Optional[str] = None,
        eco: Optional[bool] = None,
        nanoe: Optional[bool] = None,
        display: Optional[bool] = None,
        preset: Optional[str] = None,
        origin: str = "IR Blaster",
        is_resync: bool = False,
    ) -> bool:
        """Generate and transmit IR payload via configured blaster entity.

        RULE: IR writes are optimistic — updates coordinator state immediately.
        """
        if not self.blaster_entity_id:
            LOGGER.error("Device %s: Cannot dispatch IR command — no blaster entity configured", self.device_id)
            return False

        if self.availability_entity_id and hasattr(self, "hass") and getattr(self.hass, "states", None):
            st = self.hass.states.get(self.availability_entity_id)
            if st is not None and not is_blaster_available_by_sensor(st.state, self.availability_entity_id):
                LOGGER.warning("Device %s: IR dispatch blocked by availability/cutoff sensor %s (state=%s)", self.device_id, self.availability_entity_id, st.state)
                self.ir_blaster_available = False
                self._notify_listeners()
                return False

        # Apply parameters or use current coordinator state
        cmd_mode = mode or self.state["mode"]
        series_code = self.capabilities.get("series", "EU")
        if cmd_mode == "auto" and target_temp is None:
            room_t = self.state.get("room_temperature")
            if series_code in ("EZ", "KZ") and room_t is not None and isinstance(room_t, (int, float)) and room_t < 18:
                cmd_temp = 21
            else:
                cmd_temp = 24
        else:
            cmd_temp = target_temp if target_temp is not None else self.state["temperature"]

        if cmd_mode == "dry" and fan is None:
            cmd_fan = "low"
        else:
            cmd_fan = fan or self.state["fan_speed"]

        cmd_v = v_vane or self.state["v_vane"]
        cmd_h = h_vane or self.state["h_vane"]
        cmd_eco = eco if eco is not None else self.state["eco"]
        cmd_nanoe = nanoe if nanoe is not None else self.state["nanoe"]

        # Generate IR payload using panasonic-ac-models
        ir_data = generate_ir_code(
            mode=cmd_mode,
            target_temp=cmd_temp,
            fan=cmd_fan,
            v_vane=cmd_v,
            h_vane=cmd_h,
            eco=cmd_eco,
            nanoe=cmd_nanoe,
            series=series_code,
        )

        LOGGER.info(
            "Device %s: Transmitting IR command via %s -> %s",
            self.device_id,
            self.blaster_entity_id,
            ir_data["description"],
        )

        # Record command metadata and desired parameters for resync-on-reconnect
        if not is_resync:
            self._last_ir_command_timestamp = time.monotonic()
            self._last_ir_command_source = origin
            if cmd_mode not in NON_RESYNCABLE_IR_MODES:
                self._last_requested_ir_params = {
                    "mode": cmd_mode,
                    "target_temp": cmd_temp,
                    "fan": cmd_fan,
                    "v_vane": cmd_v,
                    "h_vane": cmd_h,
                    "eco": cmd_eco,
                    "nanoe": cmd_nanoe,
                    "display": display,
                    "preset": preset,
                }
            else:
                self._last_requested_ir_params = None
        else:
            self._last_ir_command_source = origin
            self._last_requested_ir_params = None

        def _on_success() -> bool:
            if cmd_mode == "display":
                self.async_optimistic_update(
                    display=display if display is not None else (self.state.get("display") != "on"),
                    origin=origin,
                )
            else:
                self.async_optimistic_update(
                    mode=cmd_mode,
                    target_temp=cmd_temp,
                    fan=cmd_fan,
                    v_vane=cmd_v,
                    h_vane=cmd_h,
                    eco=cmd_eco,
                    nanoe=cmd_nanoe,
                    preset=preset,
                    origin=origin,
                )
            return True

        # Determine target domain & service call based on blaster_entity_id
        target_domain = self.blaster_entity_id.split(".")[0]
        success = False

        if target_domain == "infrared":
            # Native Home Assistant Infrared platform helper check
            if getattr(self, "_native_ir_helper_available", True):
                try:
                    from homeassistant.components.infrared.helpers import async_send_command
                    try:
                        from infrared_protocols.commands import Command as BaseCommand  # type: ignore[import-not-found, import-untyped]
                    except ImportError:
                        class BaseCommand:  # type: ignore[no-redef]
                            def __init__(self, modulation=38000):
                                self.modulation = modulation

                    class MirAIeRawIRCommand(BaseCommand):  # type: ignore[misc, valid-type]
                        def __init__(self, raw_timings: list[int], modulation: int = 38000) -> None:
                            if hasattr(super(), "__init__"):
                                try:
                                    super().__init__(modulation=modulation)
                                except TypeError:
                                    self.modulation = modulation
                            else:
                                self.modulation = modulation
                            self._raw_timings = list(raw_timings)

                        def get_raw_timings(self) -> list[int]:
                            return self._raw_timings

                    cmd_obj = MirAIeRawIRCommand(ir_data["raw"])
                    is_esphome_2026_10 = is_esphome_2026_10_or_newer(self.hass, self.blaster_entity_id)
                    if is_esphome_2026_10:
                        LOGGER.info("Device %s: Transmitting native IR via ESPHome 2026.10+ blaster %s with 2.5s completion timeout", self.device_id, self.blaster_entity_id)
                        try:
                            await asyncio.wait_for(
                                async_send_command(self.hass, self.blaster_entity_id, cmd_obj),
                                timeout=2.5,
                            )
                            return _on_success()
                        except asyncio.TimeoutError:
                            LOGGER.warning("Device %s: ESPHome 2026.10+ IR blaster %s timed out after 2.5s waiting for transmit completion", self.device_id, self.blaster_entity_id)
                            self.ir_blaster_available = False
                            self._notify_listeners()
                            return False
                        except Exception as err:
                            LOGGER.warning("Device %s: ESPHome 2026.10+ IR blaster %s transmit failed: %s", self.device_id, self.blaster_entity_id, err)
                            self.ir_blaster_available = False
                            self._notify_listeners()
                            return False
                    else:
                        LOGGER.info("Device %s: Transmitting native IR command via %s", self.device_id, self.blaster_entity_id)
                        await async_send_command(self.hass, self.blaster_entity_id, cmd_obj)
                        return _on_success()
                except Exception as err:
                    LOGGER.debug("Native infrared helper threw exception for %s: %s, falling back to remote.send_command", self.device_id, err)

        if target_domain == "mqtt":
            # MQTT / Tasmota transmitter topic
            try:
                LOGGER.info("Device %s: Transmitting IR via mqtt.publish -> %s", self.device_id, self.blaster_entity_id)
                await self.hass.services.async_call(
                    "mqtt",
                    "publish",
                    {"topic": self.blaster_entity_id, "payload": ir_data["tasmota_json"]},
                    blocking=False,
                )
                return _on_success()
            except Exception as err:
                LOGGER.error("Device %s: MQTT IR transmission failed: %s", self.device_id, err)
                return False

        # All standard remote blasters (Broadlink, ESPHome, Tuya, Zigbee, etc.): remote.send_command
        if self._working_ir_format:
            format_map = {
                "Broadlink b64:": [f"b64:{ir_data['broadlink_b64']}"],
                "Broadlink raw b64:": [ir_data["broadlink_b64"]],
                "Tuya b64:": [ir_data["tuya_b64"]],
                "Raw pulse array:": [ir_data["raw"]],
                "Raw positive pulse array:": [[abs(p) for p in ir_data["raw"]]],
            }
            cmd_payload = format_map.get(self._working_ir_format, [f"b64:{ir_data['broadlink_b64']}"])
            try:
                LOGGER.info("Device %s: Transmitting IR payload (%s) via remote.send_command -> %s", self.device_id, self._working_ir_format, self.blaster_entity_id)
                await self.hass.services.async_call(
                    "remote",
                    "send_command",
                    {"entity_id": self.blaster_entity_id, "command": cmd_payload},
                    blocking=False,
                )
                return _on_success()
            except Exception as err:
                LOGGER.warning("Cached IR format %s failed for %s: %s, re-detecting format", self._working_ir_format, self.device_id, err)
                self._working_ir_format = None

        # Format detection / candidate list based on user configuration
        fmt = (self.ir_format or "auto").lower()
        if fmt == "raw":
            transmission_attempts = [
                ("Raw pulse array:", [ir_data["raw"]]),
                ("Raw positive pulse array:", [[abs(p) for p in ir_data["raw"]]]),
            ]
        elif fmt == "broadlink":
            transmission_attempts = [
                ("Broadlink b64:", [f"b64:{ir_data['broadlink_b64']}"]),
                ("Broadlink raw b64:", [ir_data["broadlink_b64"]]),
            ]
        elif fmt == "tuya":
            transmission_attempts = [
                ("Tuya b64:", [ir_data["tuya_b64"]]),
            ]
        else:
            # Auto-detect: prioritize standard Raw microsecond pulses (ESPHome / HA), then Broadlink, then Tuya
            transmission_attempts = [
                ("Raw pulse array:", [ir_data["raw"]]),
                ("Raw positive pulse array:", [[abs(p) for p in ir_data["raw"]]]),
                ("Broadlink b64:", [f"b64:{ir_data['broadlink_b64']}"]),
                ("Broadlink raw b64:", [ir_data["broadlink_b64"]]),
                ("Tuya b64:", [ir_data["tuya_b64"]]),
            ]

        for label, cmd_payload in transmission_attempts:
            try:
                LOGGER.info("Device %s: Transmitting IR payload (%s) via remote.send_command -> %s", self.device_id, label, self.blaster_entity_id)
                await self.hass.services.async_call(
                    "remote",
                    "send_command",
                    {"entity_id": self.blaster_entity_id, "command": cmd_payload},
                    blocking=True,
                )
                self._working_ir_format = label
                LOGGER.info("Device %s: Locked in working IR format: %s", self.device_id, label)
                return _on_success()
            except Exception as err:
                LOGGER.debug("IR payload format (%s) rejected: %s", label, err)

        LOGGER.error("Device %s: All IR transmission attempts failed for blaster entity %s", self.device_id, self.blaster_entity_id)
        return False

    @callback
    def async_optimistic_update(
        self,
        mode: Optional[str] = None,
        target_temp: Optional[int] = None,
        fan: Optional[str] = None,
        v_vane: Optional[str] = None,
        h_vane: Optional[str] = None,
        eco: Optional[bool] = None,
        nanoe: Optional[bool] = None,
        display: Optional[bool] = None,
        preset: Optional[str] = None,
        origin: str = "Cloud",
    ) -> None:
        """Optimistically update coordinator state for instant UI response."""
        if mode is not None:
            if mode == "display":
                pass
            elif mode in ("powerful", "boost"):
                self.state["power"] = "on"
                self.state["active_preset"] = "powerful"
                self.state["eco"] = False
                self.state["converti"] = "cv_off"
            elif mode == "clean":
                self.state["power"] = "on"
                self.state["active_preset"] = "clean"
                self.state["eco"] = False
                self.state["converti"] = "cv_off"
            elif mode.startswith("converti_"):
                self.state["power"] = "on"
                step = mode.split("_")[1]
                if step == "0":
                    self.state["converti"] = "cv_off"
                    self.state["active_preset"] = "none"
                else:
                    self.state["converti"] = f"cv_{step}"
                    self.state["active_preset"] = f"cv_{step}"
                self.state["eco"] = False
            elif mode == "off":
                self.state["power"] = "off"
                self.state["active_preset"] = "none"
                self.state["converti"] = "cv_off"
                self.state["eco"] = False
            else:
                self.state["power"] = "on"
                self.state["mode"] = mode
                if mode != "cool":
                    self.state["active_preset"] = "none"
                    self.state["converti"] = "cv_off"
                    self.state["eco"] = False
                if mode == "auto" and target_temp is None:
                    series = self.capabilities.get("series", "EU")
                    room_t = self.state.get("room_temperature")
                    if series in ("EZ", "KZ") and room_t is not None and isinstance(room_t, (int, float)) and room_t < 18:
                        self.state["temperature"] = 21
                    else:
                        self.state["temperature"] = 24
                elif mode == "dry" and fan is None:
                    self.state["fan_speed"] = "low"

        if preset is not None:
            norm_preset = preset.lower().strip()
            if norm_preset in ("none", "off", "0", "cv_0", "cv 0", "cv_off", "converti_0", "converti_off"):
                self.state["active_preset"] = "none"
                self.state["converti"] = "cv_off"
                self.state["eco"] = False
            elif norm_preset == "eco":
                self.state["eco"] = True
                self.state["active_preset"] = "eco"
                self.state["converti"] = "cv_off"
                self.state["temperature"] = 26
            elif norm_preset in ("boost", "powerful"):
                self.state["eco"] = False
                self.state["active_preset"] = "powerful"
                self.state["converti"] = "cv_off"
            elif norm_preset == "clean":
                self.state["eco"] = False
                self.state["active_preset"] = "clean"
                self.state["converti"] = "cv_off"
            elif norm_preset.startswith("cv_") or norm_preset.startswith("converti_"):
                step = norm_preset.split("_")[1]
                if step in ("0", "off"):
                    self.state["active_preset"] = "none"
                    self.state["converti"] = "cv_off"
                else:
                    self.state["active_preset"] = f"cv_{step}"
                    self.state["converti"] = f"cv_{step}"
                self.state["eco"] = False
            else:
                self.state["active_preset"] = norm_preset
                self.state["converti"] = "cv_off"
                self.state["eco"] = False

        if target_temp is not None:
            self.state["temperature"] = target_temp
            # Adjusting temperature cancels Eco mode if eco was not explicitly set
            if eco is None and self.state.get("eco"):
                self.state["eco"] = False
                if self.state.get("active_preset") == "eco":
                    self.state["active_preset"] = "none"

        if fan is not None:
            self.state["fan_speed"] = fan
        if v_vane is not None:
            self.state["v_vane"] = v_vane
        if h_vane is not None:
            self.state["h_vane"] = h_vane
        if eco is not None:
            self.state["eco"] = eco
            if eco:
                self.state["active_preset"] = "eco"
                self.state["converti"] = "cv_off"
                self.state["temperature"] = 26
            else:
                if self.state.get("active_preset") == "eco":
                    self.state["active_preset"] = "none"
        if nanoe is not None:
            self.state["nanoe"] = nanoe
        if display is not None:
            self.state["display"] = "on" if display else "off"
        self.state["last_controlled_by"] = origin
        self.state["provisional"] = True

        self._notify_listeners()

    @callback
    def async_setup_receiver(self) -> None:
        """Register state listeners on the configured IR receiver and blaster entities."""
        LOGGER.info(
            "Device %s: Initializing IR receiver setup (receiver_entity_id=%r, blaster_entity_id=%r, primary_backend=%r)",
            self.device_id,
            self.receiver_entity_id,
            self.blaster_entity_id,
            self.primary_backend,
        )

        from homeassistant.helpers.event import async_track_state_change_event

        if self.blaster_entity_id:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(self.hass) if hasattr(er, "async_get") else None
            entry = None
            if ent_reg:
                if hasattr(ent_reg, "async_get"):
                    entry = ent_reg.async_get(self.blaster_entity_id)
                elif hasattr(ent_reg, "get"):
                    entry = ent_reg.get(self.blaster_entity_id)
            self._is_esphome_blaster = (
                (entry is not None and getattr(entry, "platform", None) == "esphome")
                or self.blaster_entity_id.startswith(("infrared.", "esphome."))
            )
            unsub_blaster = async_track_state_change_event(
                self.hass, [self.blaster_entity_id], self._async_blaster_state_changed
            )
            self._unsub_event_bus.append(unsub_blaster)
            LOGGER.info("Device %s: Registered IR blaster state listener on entity %s (is_esphome=%s)", self.device_id, self.blaster_entity_id, self._is_esphome_blaster)

        if self.availability_entity_id:
            unsub_avail = async_track_state_change_event(
                self.hass, [self.availability_entity_id], self._async_availability_state_changed
            )
            self._unsub_event_bus.append(unsub_avail)
            LOGGER.info("Device %s: Registered IR blaster availability/cutoff listener on entity %s", self.device_id, self.availability_entity_id)
            if hasattr(self.hass, "states") and self.hass.states:
                cur_st = self.hass.states.get(self.availability_entity_id)
                if cur_st is not None and not is_blaster_available_by_sensor(cur_st.state, self.availability_entity_id):
                    self.ir_blaster_available = False

        if not self.receiver_entity_id:
            LOGGER.info("Device %s: No receiver_entity_id configured, skipping IR receiver setup", self.device_id)
            return

        try:
            from panasonic_ac_models import decode_ir_code  # type: ignore[import-not-found, import-untyped]
        except ImportError:
            from .panasonic_ac_models import decode_ir_code

        # 1. Native Home Assistant Infrared platform receiver subscription
        # The infrared entity may not be loaded yet during integration startup, so we try
        # immediately and also retry from the state change listener on first availability.
        def _try_native_subscribe() -> bool:
            """Attempt to subscribe to the native IR receiver. Returns True on success."""
            if self._unsub_native_receiver:
                return True
            if not self.receiver_entity_id.startswith("infrared."):
                return False
            try:
                from homeassistant.components.infrared.helpers import async_subscribe_receiver

                @callback
                def _on_native_ir_signal(signal: Any) -> None:
                    now = time.monotonic()
                    last_tx = getattr(self, "_last_ir_command_timestamp", 0.0)
                    if last_tx > 0.0 and (0.0 <= (now - last_tx) < 1.5):
                        LOGGER.info("Device %s: [Native IR] Suppressed signal within 1.5s transmitter echo window (dt=%.2fs)", self.device_id, now - last_tx)
                        return

                    raw_timings = (
                        getattr(signal, "timings", None)
                        or getattr(signal, "raw", None)
                        or getattr(signal, "pulses", None)
                        or (signal.get("timings") if isinstance(signal, dict) else None)
                    )
                    LOGGER.info(
                        "Device %s: [Native IR Callback] Received signal object=%r (timings length=%s)",
                        self.device_id,
                        signal,
                        len(raw_timings) if raw_timings else 0,
                    )
                    if raw_timings:
                        LOGGER.info(
                            "Device %s: [Native IR Callback] raw_timings type=%s len=%d first3=%r",
                            self.device_id,
                            type(raw_timings).__name__,
                            len(raw_timings),
                            list(raw_timings)[:3],
                        )
                        decoded = decode_ir_code(list(raw_timings))
                        LOGGER.info("Device %s: [Native IR Callback] decode_ir_code result: %r", self.device_id, decoded)
                        if decoded:
                            LOGGER.info("Device %s: Successfully applied physical remote state via native IR subscription: %s", self.device_id, decoded)
                            self._apply_decoded_ir_state(decoded)

                self._unsub_native_receiver = async_subscribe_receiver(
                    self.hass, self.receiver_entity_id, _on_native_ir_signal
                )
                if self._unsub_retry:
                    self._unsub_retry()
                    self._unsub_retry = None
                LOGGER.info("Device %s: Successfully registered native IR receiver subscription on %s", self.device_id, self.receiver_entity_id)
                return True
            except Exception as err:
                if not self.hass.is_running:
                    LOGGER.debug("Device %s: Infrared receiver entity %s not ready during HA startup — deferred until boot completes", self.device_id, self.receiver_entity_id)
                else:
                    LOGGER.warning("Device %s: Native infrared subscription failed for %s: %s — will retry on entity availability", self.device_id, self.receiver_entity_id, err)
                return False

        if self.receiver_entity_id.startswith("infrared."):
            if not _try_native_subscribe():
                from homeassistant.helpers.start import async_at_started
                from homeassistant.helpers.event import async_track_time_interval
                from datetime import timedelta

                @callback
                def _async_on_ha_started(_: Any) -> None:
                    LOGGER.info("Device %s: Home Assistant startup completed — retrying native IR receiver subscription", self.device_id)
                    _try_native_subscribe()

                unsub_start = async_at_started(self.hass, _async_on_ha_started)
                self._unsub_event_bus.append(unsub_start)

                @callback
                def _periodic_native_sub_retry(_: Any) -> None:
                    if _try_native_subscribe() and self._unsub_retry:
                        self._unsub_retry()
                        self._unsub_retry = None

                self._unsub_retry = async_track_time_interval(
                    self.hass, _periodic_native_sub_retry, timedelta(seconds=2)
                )

        # 2. Event bus listener for ESPHome / IR hardware event broadcasts
        @callback
        def _async_on_ir_event_bus(event: Any) -> None:
            ev_type = getattr(event, "event_type", "event")
            event_data = getattr(event, "data", {}) if hasattr(event, "data") else (event.get("data", {}) if isinstance(event, dict) else {})
            event_entity = event_data.get("entity_id")
            LOGGER.info("Device %s: [Event Bus %s] Received event_data=%r", self.device_id, ev_type, event_data)

            if event_entity and event_entity != self.receiver_entity_id:
                return

            now = time.monotonic()
            last_tx = getattr(self, "_last_ir_command_timestamp", 0.0)
            if last_tx > 0.0 and (0.0 <= (now - last_tx) < 1.5):
                LOGGER.info("Device %s: [Event Bus] Suppressed signal within 1.5s transmitter echo window", self.device_id)
                return

            for key in ("timings", "raw", "data", "pulses", "code", "command"):
                payload = event_data.get(key)
                if payload:
                    decoded = decode_ir_code(payload)
                    LOGGER.info("Device %s: [Event Bus] decode_ir_code for key '%s': %r", self.device_id, key, decoded)
                    if decoded:
                        LOGGER.info("Device %s: Decoded physical remote IR via event bus (%s): %s", self.device_id, key, decoded)
                        self._apply_decoded_ir_state(decoded)
                        break

        for ev_name in ("esphome.raw_infrared", "infrared_command_received", "raw_infrared"):
            try:
                unsub = self.hass.bus.async_listen(ev_name, _async_on_ir_event_bus)
                self._unsub_event_bus.append(unsub)
            except Exception as err:
                LOGGER.debug(
                    "Device %s: Could not register event bus listener for %s: %s",
                    self.device_id,
                    ev_name,
                    err,
                )

        # 3. Standard Home Assistant entity state / attribute listener
        @callback
        def _async_on_ir_state_change(event: Any) -> None:
            new_state = getattr(event, "data", {}).get("new_state") if hasattr(event, "data") else (event.get("data", {}).get("new_state") if isinstance(event, dict) else None)
            if not new_state:
                return

            raw_state = getattr(new_state, "state", None) if hasattr(new_state, "state") else (new_state.get("state") if isinstance(new_state, dict) else str(new_state))
            attributes = getattr(new_state, "attributes", {}) if hasattr(new_state, "attributes") else (new_state.get("attributes", {}) if isinstance(new_state, dict) else {})
            LOGGER.info("Device %s: [IR State Change] Entity %s state=%s, attributes=%r", self.device_id, self.receiver_entity_id, raw_state, attributes)

            # For infrared.* entities: the state is just a timestamp of last signal received.
            # No IR payload is carried in the state or attributes — the signal data is ONLY
            # available via async_subscribe_receiver callback. Retry the subscription here
            # if it failed at startup (entity not yet available at that point).
            if self.receiver_entity_id.startswith("infrared."):
                if not self._unsub_native_receiver and raw_state not in ("unavailable", "unknown", None, ""):
                    LOGGER.info("Device %s: [IR State Change] infrared entity now available — retrying native subscription", self.device_id)
                    _try_native_subscribe()
                # infrared.* state carries no decodable payload; nothing more to do here
                return

            # Echo suppression: Ignore signals received within 1.5s of our own TRANSMISSION
            now = time.monotonic()
            last_tx = getattr(self, "_last_ir_command_timestamp", 0.0)
            if last_tx > 0.0 and (0.0 <= (now - last_tx) < 1.5):
                LOGGER.info("Device %s: [IR State Change] Suppressing IR receiver echo (within 1.5s of transmission)", self.device_id)
                return

            event_data = getattr(event, "data", {}) if hasattr(event, "data") else (event.get("data", {}) if isinstance(event, dict) else {})

            # Candidate payload sources for non-infrared receiver entities (remote.*, event.*, sensor.*)
            candidates = [
                attributes.get("data"),
                attributes.get("command"),
                attributes.get("code"),
                attributes.get("raw"),
                attributes.get("pulses"),
                attributes.get("payload"),
                attributes.get("event_data"),
                event_data.get("data"),
                event_data.get("command"),
                event_data.get("code"),
                event_data.get("raw"),
                raw_state,
            ]

            decoded = None
            for cand in candidates:
                if cand is not None and str(cand).lower() not in ("unknown", "unavailable", "none", "null", ""):
                    decoded = decode_ir_code(cand)
                    if decoded:
                        break

            if not decoded:
                return

            LOGGER.info("Device %s: Successfully decoded physical remote IR transmission (state): %s", self.device_id, decoded)
            self._apply_decoded_ir_state(decoded)

        self._unsub_receiver = async_track_state_change_event(
            self.hass, [self.receiver_entity_id], _async_on_ir_state_change
        )
        LOGGER.info("Device %s: Registered IR receiver state listener on entity %s", self.device_id, self.receiver_entity_id)

        if self.temperature_sensor_entity_id:
            @callback
            def _async_on_temp_change(event: Any) -> None:
                new_state = getattr(event, "data", {}).get("new_state") if hasattr(event, "data") else (event.get("data", {}).get("new_state") if isinstance(event, dict) else None)
                if not new_state:
                    return
                raw_state = getattr(new_state, "state", None) if hasattr(new_state, "state") else (new_state.get("state") if isinstance(new_state, dict) else str(new_state))
                if raw_state not in ("unknown", "unavailable", "None", None, ""):
                    try:
                        self.state["room_temperature"] = float(raw_state)
                        self._notify_listeners()
                    except (ValueError, TypeError):
                        pass

            self._unsub_temp = async_track_state_change_event(
                self.hass, [self.temperature_sensor_entity_id], _async_on_temp_change
            )
            LOGGER.info("Device %s: Registered external temperature listener on entity %s", self.device_id, self.temperature_sensor_entity_id)
            # Read initial state if available
            cur_state = self.hass.states.get(self.temperature_sensor_entity_id)
            if cur_state and cur_state.state not in ("unknown", "unavailable", "None", None, ""):
                try:
                    self.state["room_temperature"] = float(cur_state.state)
                except (ValueError, TypeError):
                    pass

    async def _async_resync_on_reconnect(self) -> None:
        """Resync pending state to blaster upon reconnection."""
        elapsed = time.monotonic() - self._last_ir_command_timestamp
        if (
            self._last_ir_command_source not in ("IR Remote", "Cloud", "Init")
            and self._last_requested_ir_params is not None
            and self._last_requested_ir_params.get("mode") not in NON_RESYNCABLE_IR_MODES
            and elapsed <= 180.0
        ):
            LOGGER.info(
                "Device %s: IR blaster %s reconnected after %.1fs — resyncing AC state %s",
                self.device_id,
                self.blaster_entity_id,
                elapsed,
                self._last_requested_ir_params,
            )
            if self._is_esphome_blaster:
                await asyncio.sleep(0.3)
            params = dict(self._last_requested_ir_params)
            self._last_requested_ir_params = None
            await self.async_dispatch_ir_command(**params, origin="Blaster Reconnect Resync", is_resync=True)
        else:
            LOGGER.debug(
                "Device %s: IR blaster %s reconnected — resync skipped (source=%s, elapsed=%.1fs, pending=%s)",
                self.device_id,
                self.blaster_entity_id,
                self._last_ir_command_source,
                elapsed,
                self._last_requested_ir_params,
            )

    async def _async_availability_state_changed(self, event: Any) -> None:
        """Handle state changes on the availability or cutoff sensor."""
        event_data = getattr(event, "data", {}) if hasattr(event, "data") else (event.get("data", {}) if isinstance(event, dict) else {})
        old_state = event_data.get("old_state")
        new_state = event_data.get("new_state")
        new_raw = getattr(new_state, "state", None) if hasattr(new_state, "state") else (new_state.get("state") if isinstance(new_state, dict) else str(new_state) if new_state else None)
        old_raw = getattr(old_state, "state", None) if hasattr(old_state, "state") else (old_state.get("state") if isinstance(old_state, dict) else str(old_state) if old_state else None)

        is_avail = is_blaster_available_by_sensor(new_raw, self.availability_entity_id) if (new_raw and self.availability_entity_id) else False
        was_avail = is_blaster_available_by_sensor(old_raw, self.availability_entity_id) if (old_raw and self.availability_entity_id) else True

        # Blaster entity check: if blaster entity is defined, must ALSO be available in HA
        if self.blaster_entity_id and is_avail and hasattr(self, "hass") and self.hass.states:
            blaster_st = self.hass.states.get(self.blaster_entity_id)
            if blaster_st is None or str(blaster_st.state).lower() in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                is_avail = False

        self.ir_blaster_available = is_avail
        LOGGER.info(
            "Device %s: Availability/cutoff sensor %s changed to %s (ir_blaster_available=%s)",
            self.device_id,
            self.availability_entity_id,
            new_raw,
            self.ir_blaster_available,
        )
        self._notify_listeners()

        if is_avail and not was_avail:
            await self._async_resync_on_reconnect()

    async def _async_blaster_state_changed(self, event: Any) -> None:
        """Handle IR blaster emitter availability changes."""
        event_data = getattr(event, "data", {}) if hasattr(event, "data") else (event.get("data", {}) if isinstance(event, dict) else {})
        old_state = event_data.get("old_state")
        new_state = event_data.get("new_state")
        if new_state is None:
            self.ir_blaster_available = False
            self._notify_listeners()
            return

        new_raw = getattr(new_state, "state", None) if hasattr(new_state, "state") else (new_state.get("state") if isinstance(new_state, dict) else str(new_state))
        if new_raw in (STATE_UNAVAILABLE, STATE_UNKNOWN, None, ""):
            self.ir_blaster_available = False
            self._notify_listeners()
            return

        # Trigger only on the edge transition from unavailable/unknown to available
        old_raw = getattr(old_state, "state", None) if hasattr(old_state, "state") else (old_state.get("state") if isinstance(old_state, dict) else str(old_state) if old_state else None)
        if old_raw is not None and old_raw not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        # Check availability sensor boundary if configured
        if self.availability_entity_id and hasattr(self, "hass") and self.hass.states:
            st = self.hass.states.get(self.availability_entity_id)
            if st is not None and not is_blaster_available_by_sensor(st.state, self.availability_entity_id):
                self.ir_blaster_available = False
                self._notify_listeners()
                return

        self.ir_blaster_available = True
        self._notify_listeners()

        await self._async_resync_on_reconnect()

    @callback
    def _apply_decoded_ir_state(self, decoded: Dict[str, Any]) -> None:
        """Apply a decoded physical remote state payload and notify entities."""
        # Initiate 8-second grace window to protect against stale incoming cloud packets
        self._last_physical_rx_timestamp = time.monotonic()
        self._last_ir_command_source = "IR Remote"
        self._last_requested_ir_params = None
        self.state["last_controlled_by"] = "IR Remote"
        self.state["provisional"] = False

        if decoded.get("packet_type") == "short_frame":
            action = decoded.get("action")
            if action == "powerful":
                self.state["power"] = "on"
                self.state["active_preset"] = "powerful"
                self.state["converti"] = "cv_off"
                self.state["eco"] = False
            elif action == "display":
                self.state["display"] = "off" if self.state.get("display") == "on" else "on"
            elif action == "clean":
                self.state["power"] = "on"
                self.state["active_preset"] = "clean"
                self.state["converti"] = "cv_off"
                self.state["eco"] = False
            elif action and str(action).startswith("converti_"):
                step = str(action).split("_")[1]
                self.state["power"] = "on"
                self.state["converti"] = f"cv_{step}"
                self.state["active_preset"] = f"cv_{step}"
                self.state["eco"] = False

        elif decoded.get("packet_type") == "full_frame":
            pwr = decoded.get("power", "on")
            self.state["power"] = pwr
            if pwr == "off":
                self.state["mode"] = "off"
                self.state["active_preset"] = "none"
                self.state["converti"] = "cv_off"
                self.state["eco"] = False
            else:
                mode = decoded.get("mode", "cool")
                self.state["mode"] = mode
                self.state["temperature"] = decoded.get("temperature", 24)
                self.state["fan_speed"] = decoded.get("fan_speed", "auto")
                self.state["v_vane"] = decoded.get("v_vane", "AUTO")
                self.state["h_vane"] = decoded.get("h_vane", "AUTO")

                if decoded.get("powerful"):
                    self.state["active_preset"] = "powerful"
                    self.state["converti"] = "cv_off"
                    self.state["eco"] = False
                elif decoded.get("eco"):
                    self.state["eco"] = True
                    self.state["active_preset"] = "eco"
                    self.state["converti"] = "cv_off"
                    self.state["temperature"] = 26
                elif decoded.get("converti") and decoded.get("converti") != "cv_off":
                    self.state["eco"] = False
                    self.state["converti"] = decoded.get("converti")
                    self.state["active_preset"] = decoded.get("converti")
                else:
                    self.state["eco"] = False
                    self.state["active_preset"] = "none"
                    self.state["converti"] = "cv_off"

        self._notify_listeners()

    @callback
    def async_unload(self) -> None:
        """Unload and unregister listeners for this coordinator."""
        if self._unsub_native_receiver:
            self._unsub_native_receiver()
            self._unsub_native_receiver = None
        if self._unsub_retry:
            self._unsub_retry()
            self._unsub_retry = None
        for unsub in self._unsub_event_bus:
            unsub()
        self._unsub_event_bus.clear()
        if self._unsub_receiver:
            self._unsub_receiver()
            self._unsub_receiver = None
        if self._unsub_temp:
            self._unsub_temp()
            self._unsub_temp = None
