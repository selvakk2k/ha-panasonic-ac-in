"""Matter entity-merging bridge for Panasonic ACs paired via HA Matter."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .logger import LOGGER
from .utils import parse_packed_matter_temp


MATTER_SUPPORTED_MODES = frozenset({"off", "cool"})
MATTER_UNSUPPORTED_MODES = frozenset({"dry", "fan_only", "auto", "heat"})


def discover_matter_sibling_entities(
    hass: HomeAssistant, matter_climate_id: str
) -> dict[str, str | None]:
    """Find sibling switch/fan/sensor entities sharing the Matter climate device_id."""
    result: dict[str, str | None] = {
        "climate": matter_climate_id,
        "power_switch": None,
        "fan": None,
        "outdoor_temp": None,
    }
    if not hass or not matter_climate_id:
        return result

    try:
        ent_reg = er.async_get(hass)
    except Exception:
        return result

    climate_entry = ent_reg.async_get(matter_climate_id) if hasattr(ent_reg, "async_get") else None
    device_id = getattr(climate_entry, "device_id", None) if climate_entry else None
    if not device_id:
        return result

    entries = er.async_entries_for_device(ent_reg, device_id) if hasattr(er, "async_entries_for_device") else []
    for entry in entries:
        eid = getattr(entry, "entity_id", "")
        if not eid or getattr(entry, "disabled_by", None) is not None:
            continue
        if eid.startswith("switch.") and (
            getattr(entry, "translation_key", None) == "power" or eid.endswith("_power")
        ):
            result["power_switch"] = eid
        elif eid.startswith("fan.") and result["fan"] is None:
            result["fan"] = eid
        elif eid.startswith("sensor.") and (
            getattr(entry, "translation_key", None) == "outdoor_temperature"
            or "outdoor_temperature" in eid
        ):
            result["outdoor_temp"] = eid

    return result


def get_matter_state(hass: HomeAssistant, matter_climate_id: str) -> dict[str, Any]:
    """Read merged state across Matter climate, power switch, fan, and outdoor sensor."""
    siblings = discover_matter_sibling_entities(hass, matter_climate_id)
    clim_state = hass.states.get(matter_climate_id) if hass else None
    if not clim_state or clim_state.state in ("unavailable", "unknown"):
        return {"is_online": False, "siblings": siblings}

    attrs = clim_state.attributes or {}
    power_sw = hass.states.get(siblings["power_switch"]) if siblings["power_switch"] else None
    fan_st = hass.states.get(siblings["fan"]) if siblings["fan"] else None
    out_st = hass.states.get(siblings["outdoor_temp"]) if siblings["outdoor_temp"] else None

    is_on = (
        power_sw.state == "on"
        if power_sw and power_sw.state not in ("unavailable", "unknown")
        else clim_state.state != "off"
    )
    hvac_mode = "off" if not is_on else (clim_state.state if clim_state.state != "off" else "cool")

    current_temp = parse_packed_matter_temp(attrs.get("current_temperature"))
    target_temp = attrs.get("temperature")
    if target_temp is not None:
        try:
            target_temp = max(16.0, min(30.0, float(target_temp)))
        except (TypeError, ValueError):
            target_temp = 24.0

    fan_mode = attrs.get("fan_mode")
    if fan_st and fan_st.state not in ("unavailable", "unknown"):
        fan_attrs = fan_st.attributes or {}
        if fan_attrs.get("preset_mode"):
            fan_mode = str(fan_attrs["preset_mode"]).lower()
        elif fan_attrs.get("percentage") is not None:
            pct = float(fan_attrs["percentage"])
            if pct <= 0:
                fan_mode = "auto"
            elif pct <= 25:
                fan_mode = "quiet"
            elif pct <= 50:
                fan_mode = "low"
            elif pct <= 75:
                fan_mode = "medium"
            else:
                fan_mode = "high"

    outdoor_temp = None
    if out_st and out_st.state not in ("unavailable", "unknown"):
        try:
            outdoor_temp = float(out_st.state)
        except (TypeError, ValueError):
            outdoor_temp = None

    return {
        "is_online": True,
        "is_on": is_on,
        "hvac_mode": hvac_mode,
        "target_temp": target_temp,
        "current_temp": current_temp,
        "fan_mode": fan_mode or "auto",
        "min_temp": 16.0,
        "max_temp": 30.0,
        "outdoor_temp": outdoor_temp,
        "siblings": siblings,
    }


async def async_send_matter_command(
    hass: HomeAssistant,
    matter_climate_id: str,
    *,
    power: str | None = None,
    hvac_mode: str | None = None,
    temperature: float | None = None,
    fan_mode: str | None = None,
) -> bool:
    """Dispatch supported core commands to merged Matter entities."""
    if not hass or not matter_climate_id:
        return False

    siblings = discover_matter_sibling_entities(hass, matter_climate_id)
    power_sw_id = siblings.get("power_switch")
    fan_id = siblings.get("fan")

    try:
        if power is not None:
            if power_sw_id:
                await hass.services.async_call(
                    "switch",
                    "turn_on" if power == "on" else "turn_off",
                    {"entity_id": power_sw_id},
                    blocking=True,
                )
            else:
                await hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": matter_climate_id, "hvac_mode": "cool" if power == "on" else "off"},
                    blocking=True,
                )

        if hvac_mode is not None:
            norm_mode = hvac_mode.lower()
            if norm_mode == "off":
                if power_sw_id:
                    await hass.services.async_call(
                        "switch", "turn_off", {"entity_id": power_sw_id}, blocking=True
                    )
                else:
                    await hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": matter_climate_id, "hvac_mode": "off"},
                        blocking=True,
                    )
            elif norm_mode == "cool":
                if power_sw_id:
                    await hass.services.async_call(
                        "switch", "turn_on", {"entity_id": power_sw_id}, blocking=True
                    )
                await hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": matter_climate_id, "hvac_mode": "cool"},
                    blocking=True,
                )

        if temperature is not None:
            clamped = max(16.0, min(30.0, float(temperature)))
            await hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": matter_climate_id, "temperature": clamped},
                blocking=True,
            )

        if fan_mode is not None:
            f_norm = fan_mode.lower()
            if fan_id:
                pct_map = {"quiet": 20, "low": 40, "medium": 65, "high": 100, "auto": 0}
                if f_norm == "auto":
                    fan_st = hass.states.get(fan_id)
                    presets = (fan_st.attributes or {}).get("preset_modes", []) if fan_st else []
                    if "auto" in [p.lower() for p in presets]:
                        await hass.services.async_call(
                            "fan",
                            "set_preset_mode",
                            {"entity_id": fan_id, "preset_mode": "auto"},
                            blocking=True,
                        )
                    else:
                        await hass.services.async_call(
                            "fan",
                            "set_percentage",
                            {"entity_id": fan_id, "percentage": 50},
                            blocking=True,
                        )
                else:
                    await hass.services.async_call(
                        "fan",
                        "set_percentage",
                        {"entity_id": fan_id, "percentage": pct_map.get(f_norm, 65)},
                        blocking=True,
                    )
            else:
                await hass.services.async_call(
                    "climate",
                    "set_fan_mode",
                    {"entity_id": matter_climate_id, "fan_mode": f_norm},
                    blocking=True,
                )
        return True
    except Exception as exc:
        LOGGER.warning("Matter command dispatch failed for %s: %s", matter_climate_id, exc)
        return False

