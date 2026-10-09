"""Config flow for mirAIe integration."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Optional
import aiohttp

from miraie_ac import MirAIeHub, constants
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_INSTALL_DATE,
    CONF_BLASTER_ENTITY_ID,
    CONF_RECEIVER_ENTITY_ID,
    CONF_ROOM_TEMP_SENSOR,
    CONF_AVAILABILITY_ENTITY_ID,
    CONF_IR_FORMAT,
    CONF_PRIMARY_BACKEND,
    CONF_HYBRID_SUBMODE,
    DOMAIN,
)
from .utils import six_months_ago, eight_months_ago, is_ac_device

from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


class InvalidInstallDate(HomeAssistantError):
    """Error to indicate invalid install date."""


def parse_install_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInstallDate from exc


def build_login_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("username"): str,
            vol.Required("password"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


def build_cloud_devices_schema(default_install_date: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_PRIMARY_BACKEND, default="cloud"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="cloud", label="Cloud"),
                        selector.SelectOptionDict(value="ir", label="Infrared"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_HYBRID_SUBMODE, default="auto"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="auto", label="Automatic Failover (Switch to secondary transport on outage)"),
                        selector.SelectOptionDict(value="manual", label="Manual Control"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_BLASTER_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["infrared", "remote"])
            ),
            vol.Optional(CONF_RECEIVER_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["infrared", "remote"])
            ),
            vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_AVAILABILITY_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch"])
            ),
            vol.Optional(CONF_IR_FORMAT, default="auto"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="auto", label="Auto-Detect (Recommended)"),
                        selector.SelectOptionDict(value="raw", label="Home Assistant Infrared / ESPHome (Hardware-Tested & Confirmed)"),
                        selector.SelectOptionDict(value="tasmota", label="Tasmota / AEHA Hex (Capture Verified)"),
                        selector.SelectOptionDict(value="broadlink", label="Broadlink Base64 (Format Verified)"),
                        selector.SelectOptionDict(value="tuya", label="Tuya Base64 (Format Verified)"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_INSTALL_DATE, default=default_install_date): selector.DateSelector(),
        }
    )


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate the user input allows us to connect and fetch discovered devices."""
    session = async_get_clientsession(hass)
    try:
        hub = MirAIeHub(session)
    except TypeError:
        hub = MirAIeHub()

    if not hasattr(hub, "_broker"):
        hub._broker = None

    discovered_devices = []
    # pylint: disable=protected-access
    try:
        await hub._authenticate(data["username"], data["password"])
        try:
            # 1. Primary: Direct REST endpoint query (fast, safe, no MQTT connection required)
            headers = {
                "Authorization": f"Bearer {getattr(getattr(hub, 'user', None), 'access_token', '')}",
                "Content-Type": "application/json",
            }
            if hasattr(hub, "http") and hub.http:
                try:
                    res = await hub.http.get(
                        "https://app.miraie.in/simplifi/v1/homeManagement/homes",
                        headers=headers,
                    )
                    resp = await res.json()
                    if isinstance(resp, list) and len(resp) > 0:
                        raw_devices = []
                        for space in resp[0].get("spaces", []):
                            for dev in space.get("devices", []):
                                dev_id = dev.get("deviceId")
                                if dev_id:
                                    direct_model = (
                                        dev.get("modelNumber")
                                        or dev.get("modelName")
                                        or dev.get("model")
                                        or (dev.get("details") or {}).get("modelNumber")
                                        or ""
                                    )
                                    raw_devices.append({
                                        "id": dev_id,
                                        "name": dev.get("deviceName", "MirAIe Cloud AC"),
                                        "model_code": direct_model,
                                        "raw_dev": dev,
                                    })

                        details_map: dict[str, Any] = {}
                        if raw_devices:
                            device_ids = ",".join([d["id"] for d in raw_devices])
                            try:
                                res_details = await hub.http.get(
                                    f"https://app.miraie.in/simplifi/v1/deviceManagement/devices/deviceId/{device_ids}",
                                    headers=headers,
                                )
                                details_list = await res_details.json()
                                if isinstance(details_list, dict):
                                    if "data" in details_list and isinstance(details_list["data"], list):
                                        details_list = details_list["data"]
                                    elif "devices" in details_list and isinstance(details_list["devices"], list):
                                        details_list = details_list["devices"]
                                    else:
                                        details_list = [details_list]

                                if isinstance(details_list, list):
                                    details_map = {
                                        dd.get("deviceId"): dd
                                        for dd in details_list
                                        if isinstance(dd, dict) and dd.get("deviceId")
                                    }
                            except Exception as exc:
                                _LOGGER.debug("Could not fetch batched device details: %s", exc)

                        filtered_devices = []
                        for rd in raw_devices:
                            dt = details_map.get(rd["id"], {})
                            if dt:
                                rd["model_code"] = dt.get("modelNumber") or dt.get("modelName") or rd["model_code"]

                            if is_ac_device(rd.get("raw_dev"), dt):
                                rd.pop("raw_dev", None)
                                filtered_devices.append(rd)
                            else:
                                _LOGGER.info(
                                    "Ignoring non-AC MirAIe device '%s' (ID: %s)",
                                    rd.get("name"),
                                    rd.get("id"),
                                )

                        discovered_devices = filtered_devices
                except Exception as exc:
                    _LOGGER.debug("REST homes fetch did not return device list: %s", exc)

            # 2. Fallback: Parse hub.home / _get_home_details (for mock environments and test harnesses)
            if not discovered_devices:
                if not hasattr(hub, "home") or not hub.home:
                    if hasattr(hub, "_get_home_details"):
                        await hub._get_home_details()
                if hasattr(hub, "home") and hub.home and hasattr(hub.home, "devices"):
                    for dev in hub.home.devices:
                        if not is_ac_device(dev):
                            _LOGGER.info("Ignoring non-AC MirAIe device from hub: %s", getattr(dev, "friendly_name", dev))
                            continue
                        dev_id = getattr(dev, "id", None)
                        if dev_id:
                            model_number = None
                            if hasattr(dev, "details") and dev.details:
                                model_number = getattr(dev.details, "model_number", None)
                            discovered_devices.append({
                                "id": dev_id,
                                "name": getattr(dev, "friendly_name", "MirAIe Cloud AC"),
                                "model_code": model_number or "",
                            })
        except Exception as exc:
            _LOGGER.warning("Direct device discovery failed during validation: %s", exc)

    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        _LOGGER.error("Cannot connect to MirAIe cloud: %s", exc)
        raise CannotConnect from exc
    except Exception as exc:
        _LOGGER.error("MirAIe authentication failed for %s: %s", data.get("username"), exc)
        raise InvalidAuth from exc
    finally:
        if hub.http != session and hasattr(hub, "http") and hub.http and not getattr(hub.http, "closed", True):
            await hub.http.close()

    return {"title": "MirAIe Cloud Account"}, discovered_devices


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for mirAIe."""

    VERSION = 2

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle import flow for automatic legacy v1.x per-device entry migration and multi-device setup."""
        data = dict(import_data)
        options = data.pop("options", {})
        device_id = data.get("device_id")
        unique_id = data.pop("unique_id", None)
        if not unique_id and device_id:
            username = data.get("username", "").lower()
            unique_id = f"{username}_{device_id}" if username else device_id

        if unique_id:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

        title = data.get("name") or (f"MirAIe AC ({device_id})" if device_id else "MirAIe AC")
        return self.async_create_entry(title=title, data=data, options=options)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Choose setup mode (MirAIe Cloud Account vs IR-Only Standalone)."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["cloud_account", "ir_device"]
        )

    async def async_step_cloud_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1 (Cloud): MirAIe Cloud Login."""
        if user_input is None:
            return self.async_show_form(
                step_id="cloud_account", data_schema=build_login_schema()
            )

        errors = {}
        try:
            info, discovered_devices = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception during cloud login")
            errors["base"] = "unknown"
        else:
            if not discovered_devices:
                return self.async_abort(reason="no_devices_found")

            # Filter out devices that already have configured entries
            existing_dev_ids = {e.data.get("device_id") for e in self._async_current_entries() if e.data.get("device_id")}
            new_devices = [d for d in discovered_devices if d["id"] not in existing_dev_ids]

            if not new_devices:
                return self.async_abort(reason="already_configured")

            self._cloud_credentials = user_input
            self._discovered_cloud_devices = new_devices
            self._current_device_index = 0
            self._per_device_options = {}
            return await self.async_step_cloud_devices()


        return self.async_show_form(
            step_id="cloud_account",
            data_schema=build_login_schema(),
            errors=errors,
        )

    async def async_step_cloud_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (Cloud): Configure control transport and options for each discovered Wi-Fi AC unit."""
        creds = getattr(self, "_cloud_credentials", {})
        devices = getattr(self, "_discovered_cloud_devices", [])
        if not devices:
            return self.async_abort(reason="no_devices_found")

        idx = getattr(self, "_current_device_index", 0)
        if idx >= len(devices):
            idx = 0
            self._current_device_index = 0

        current_dev = devices[idx]
        dev_name = current_dev.get("name", "AC Unit")
        dev_model = current_dev.get("model_code", "")
        model_display = f"Model: **{dev_model}**" if dev_model else "Model: Auto-detecting via Cloud"
        default_install = six_months_ago(date.today()).isoformat()

        if user_input is None:
            return self.async_show_form(
                step_id="cloud_devices",
                data_schema=build_cloud_devices_schema(default_install),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                    "model_code": dev_model or "Auto-detected",
                },
            )

        errors = {}
        try:
            install_date = parse_install_date(user_input.get(CONF_INSTALL_DATE, ""))
            today = date.today()
            min_date = six_months_ago(today)
            oldest_date = eight_months_ago(today)
            if install_date is None:
                install_date = min_date
            if install_date < oldest_date or install_date > today:
                errors[CONF_INSTALL_DATE] = "invalid_install_date"
                raise InvalidInstallDate
        except InvalidInstallDate:
            errors[CONF_INSTALL_DATE] = "invalid_install_date"
            return self.async_show_form(
                step_id="cloud_devices",
                data_schema=build_cloud_devices_schema(
                    user_input.get(CONF_INSTALL_DATE, default_install)
                ),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                    "model_code": dev_model or "Auto-detected",
                },
                errors=errors,
            )

        blaster_val = user_input.get(CONF_BLASTER_ENTITY_ID, "").strip()
        backend_val = user_input.get(CONF_PRIMARY_BACKEND, "cloud")
        submode_val = user_input.get(CONF_HYBRID_SUBMODE, "auto")

        if backend_val == "ir" and not blaster_val:
            errors[CONF_BLASTER_ENTITY_ID] = "blaster_required_for_ir"
            return self.async_show_form(
                step_id="cloud_devices",
                data_schema=build_cloud_devices_schema(
                    user_input.get(CONF_INSTALL_DATE, default_install)
                ),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                    "model_code": dev_model or "Auto-detected",
                },
                errors=errors,
            )

        dev_opt = {
            CONF_INSTALL_DATE: install_date.isoformat(),
            CONF_PRIMARY_BACKEND: backend_val,
            CONF_HYBRID_SUBMODE: submode_val,
            "model_code": dev_model,
        }
        if blaster_val:
            dev_opt[CONF_BLASTER_ENTITY_ID] = blaster_val
        receiver_raw = user_input.get(CONF_RECEIVER_ENTITY_ID)
        if receiver_raw and str(receiver_raw).lower() not in ("none", "null", ""):
            dev_opt[CONF_RECEIVER_ENTITY_ID] = str(receiver_raw).strip()
        temp_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
        if temp_raw and str(temp_raw).lower() not in ("none", "null", ""):
            dev_opt[CONF_ROOM_TEMP_SENSOR] = str(temp_raw).strip()
        avail_raw = user_input.get(CONF_AVAILABILITY_ENTITY_ID)
        if avail_raw and str(avail_raw).lower() not in ("none", "null", ""):
            dev_opt[CONF_AVAILABILITY_ENTITY_ID] = str(avail_raw).strip()
        ir_fmt = user_input.get(CONF_IR_FORMAT)
        if ir_fmt:
            dev_opt[CONF_IR_FORMAT] = ir_fmt

        entry_data = {
            "username": creds.get("username", ""),
            "password": creds.get("password", ""),
            "device_id": current_dev["id"],
            "device_name": dev_name,
            "model_code": dev_model,
            "is_ir_only": False,
        }

        created = getattr(self, "_created_entries", [])
        created.append({
            "title": dev_name,
            "data": entry_data,
            "options": dev_opt,
            "unique_id": f"{creds.get('username', '').lower()}_{current_dev['id']}",
        })
        self._created_entries = created

        self._current_device_index = idx + 1
        if self._current_device_index < len(devices):
            return await self.async_step_cloud_devices()

        # Create entry for each additional discovered device if multiple
        for item in self._created_entries[:-1]:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    data={
                        **item["data"],
                        "options": item["options"],
                        "unique_id": item["unique_id"],
                        "name": item["title"],
                    },
                )
            )

        last_item = self._created_entries[-1]
        await self.async_set_unique_id(last_item["unique_id"])
        return self.async_create_entry(
            title=last_item["title"],
            data=last_item["data"],
            options=last_item["options"],
        )


    async def async_step_ir_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Enter Name & Panasonic Model Code."""
        if user_input is not None:
            name = user_input["name"].strip()
            model_code = user_input["model_code"].strip().upper()

            unique_id = f"manual_ac_{name.lower().replace(' ', '_')}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            self._device_data = {
                "name": name,
                "model_code": model_code,
            }
            return await self.async_step_feature_confirmation()

        schema = vol.Schema(
            {
                vol.Required("name", default="Living Room AC"): str,
                vol.Required("model_code", default="CS-CU-RU18CKY-1"): str,
            }
        )
        return self.async_show_form(step_id="ir_device", data_schema=schema)

    async def async_step_feature_confirmation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Display database capabilities, select Control Mode (restricted by has_wifi), & allow feature overrides."""
        dev_data = getattr(self, "_device_data", {})
        model_code = str(dev_data.get("model_code") or "")

        if not model_code:
            caps = {
                "has_wifi": 1,
                "has_heat_mode": 0,
                "has_nanoe": 0,
                "converti_type": "7-in-1",
                "h_vane_enabled": 1,
                "resolved_via": "safe_default",
            }
        else:
            try:
                try:
                    from panasonic_ac_models import ACModelLookup  # type: ignore[import-not-found, import-untyped]
                except ImportError:
                    from .panasonic_ac_models import ACModelLookup
                lookup = await self.hass.async_add_executor_job(ACModelLookup)
                caps = lookup.get_capabilities(model_code)
            except Exception as err:
                _LOGGER.debug("Model capability lookup for %s failed (%s), using safe defaults", model_code, err)
                caps = {
                    "has_wifi": 1,
                    "has_heat_mode": 0,
                    "has_nanoe": 0,
                    "converti_type": "7-in-1",
                    "h_vane_enabled": 1,
                    "resolved_via": "safe_default",
                }

        has_wifi = bool(caps.get("has_wifi", 1))

        if user_input is not None:
            ctrl_mode = user_input.get("control_mode", "ir" if not has_wifi else "hybrid")
            dev_data["control_mode"] = ctrl_mode
            dev_data["capabilities"] = {
                "has_heat_mode": user_input.get("has_heat_mode", False),
                "has_nanoe": user_input.get("has_nanoe", False),
                "converti_type": user_input.get("converti_type", "7-in-1"),
                "h_vane_enabled": user_input.get("h_vane_enabled", True),
                "has_wifi": has_wifi,
            }

            if ctrl_mode in ("ir", "hybrid"):
                return await self.async_step_attach_blaster()

            # Cloud-only mode: create entry directly
            name = dev_data["name"]
            data = {
                "is_ir_only": False,
                "name": name,
                "model_code": model_code,
                "capabilities": dev_data["capabilities"],
            }
            options = {
                CONF_PRIMARY_BACKEND: "cloud",
                CONF_HYBRID_SUBMODE: "manual",
                "model_code": model_code,
            }
            return self.async_create_entry(title=f"{name} (Cloud Only)", data=data, options=options)

        has_heat = bool(caps.get("has_heat_mode", 0))
        has_nanoe = bool(caps.get("has_nanoe", 0))
        c_type = str(caps.get("converti_type", "7-in-1"))
        h_vane = bool(caps.get("h_vane_enabled", 1))

        mode_options = [
            selector.SelectOptionDict(value="ir", label="IR Only (Standalone Blaster — No MirAIe Account Required)"),
        ]
        default_mode = "ir"

        schema = vol.Schema(
            {
                vol.Required("control_mode", default=default_mode): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=mode_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("has_heat_mode", default=has_heat): selector.BooleanSelector(),
                vol.Required("has_nanoe", default=has_nanoe): selector.BooleanSelector(),
                vol.Required("converti_type", default=c_type): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="8-in-1", label="8-in-1 Convertible"),
                            selector.SelectOptionDict(value="7-in-1", label="7-in-1 Convertible"),
                            selector.SelectOptionDict(value="none", label="None (Non-Convertible)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("h_vane_enabled", default=h_vane): selector.BooleanSelector(),
            }
        )
        rv = str(caps.get("resolved_via", "database")).lower()
        if rv == "database":
            resolved_via_label = "verified model database"
        elif rv == "decoder":
            resolved_via_label = "model code decoder (please double-check capabilities below)"
        else:
            resolved_via_label = "default capability template (please double-check capabilities below)"

        return self.async_show_form(
            step_id="feature_confirmation",
            data_schema=schema,
            description_placeholders={
                "model_code": model_code,
                "resolved_via": resolved_via_label,
            },
        )

    async def async_step_attach_blaster(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: Attach mandatory IR Blaster for IR Only and Hybrid control modes."""
        dev_data = getattr(self, "_device_data", {})
        name = dev_data.get("name", "AC Unit")
        model_code = dev_data.get("model_code", "")
        ctrl_mode = dev_data.get("control_mode", "ir")
        errors = {}

        if user_input is not None:
            blaster_id = user_input.get(CONF_BLASTER_ENTITY_ID, "").strip()
            receiver_raw = user_input.get(CONF_RECEIVER_ENTITY_ID)
            receiver_id = "" if (receiver_raw is None or str(receiver_raw).lower() in ("none", "null", "")) else str(receiver_raw).strip()
            temp_sensor_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
            temp_sensor_id = "" if (temp_sensor_raw is None or str(temp_sensor_raw).lower() in ("none", "null", "")) else str(temp_sensor_raw).strip()
            avail_raw = user_input.get(CONF_AVAILABILITY_ENTITY_ID)
            avail_id = "" if (avail_raw is None or str(avail_raw).lower() in ("none", "null", "")) else str(avail_raw).strip()
            ir_fmt = user_input.get(CONF_IR_FORMAT, "auto")
            if not blaster_id:
                errors[CONF_BLASTER_ENTITY_ID] = "blaster_required"
            else:
                is_ir_only = (ctrl_mode == "ir")
                data = {
                    "is_ir_only": is_ir_only,
                    "name": name,
                    "model_code": model_code,
                    "capabilities": dev_data.get("capabilities", {}),
                }
                options = {
                    CONF_PRIMARY_BACKEND: "ir" if ctrl_mode == "ir" else "cloud",
                    CONF_HYBRID_SUBMODE: "auto" if ctrl_mode == "hybrid" else "manual",
                    CONF_BLASTER_ENTITY_ID: blaster_id,
                    CONF_RECEIVER_ENTITY_ID: receiver_id,
                    CONF_ROOM_TEMP_SENSOR: temp_sensor_id,
                    CONF_AVAILABILITY_ENTITY_ID: avail_id,
                    CONF_IR_FORMAT: ir_fmt,
                    "model_code": model_code,
                }
                title_suffix = "IR Only" if ctrl_mode == "ir" else "Hybrid"
                return self.async_create_entry(title=f"{name} ({title_suffix})", data=data, options=options)

        schema = vol.Schema(
            {
                vol.Required(CONF_BLASTER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["infrared", "remote"])
                ),
                vol.Optional(CONF_RECEIVER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["infrared", "remote"])
                ),
                vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Optional(CONF_AVAILABILITY_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch"])
                ),
                vol.Optional(CONF_IR_FORMAT, default="auto"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="auto", label="Auto-Detect (Recommended)"),
                            selector.SelectOptionDict(value="raw", label="Home Assistant Infrared / ESPHome (Hardware-Tested & Confirmed)"),
                            selector.SelectOptionDict(value="tasmota", label="Tasmota / AEHA Hex (Capture Verified)"),
                            selector.SelectOptionDict(value="broadlink", label="Broadlink Base64 (Format Verified)"),
                            selector.SelectOptionDict(value="tuya", label="Tuya Base64 (Format Verified)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="attach_blaster",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": name},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication with MirAIe Cloud."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with new credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.debug("Unexpected error during re-authentication confirmation: %s", err, exc_info=True)
                errors["base"] = "unknown"
            else:
                if getattr(self, "_reauth_entry", None):
                    old_username = self._reauth_entry.data.get("username", "").lower()
                    new_username = user_input["username"]
                    new_password = user_input["password"]

                    entries_to_update = [
                        entry for entry in self.hass.config_entries.async_entries(DOMAIN)
                        if hasattr(entry, "data") and entry.data.get("username", "").lower() == old_username
                    ]
                    if not entries_to_update:
                        entries_to_update = [self._reauth_entry]

                    for entry in entries_to_update:
                        self.hass.config_entries.async_update_entry(
                            entry,
                            data={
                                **entry.data,
                                "username": new_username,
                                "password": new_password,
                            },
                        )
                        if hasattr(self.hass.config_entries, "async_reload"):
                            await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")



        reauth_entry = getattr(self, "_reauth_entry", None)
        default_user = reauth_entry.data.get("username", "") if reauth_entry else ""
        schema = vol.Schema(
            {
                vol.Required("username", default=default_user): str,
                vol.Required("password"): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"username": default_user},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return OptionsFlowHandler(config_entry)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidInstallDate(HomeAssistantError):
    """Error to indicate invalid installation date."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for mirAIe."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        """Initialize options flow."""
        super().__init__()
        if config_entry is not None:
            self._config_entry = config_entry

    def _get_config_entry(self) -> config_entries.ConfigEntry | None:
        return getattr(self, "config_entry", None) or getattr(self, "_config_entry", None)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Go directly to settings for this AC unit."""
        return await self.async_step_device_settings(user_input)

    def _build_device_settings_schema(self) -> vol.Schema:

        return vol.Schema(
            {
                vol.Optional(CONF_INSTALL_DATE): selector.DateSelector(),
                vol.Optional(CONF_BLASTER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["infrared", "remote"])
                ),
                vol.Optional(CONF_RECEIVER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["infrared", "remote"])
                ),
                vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Optional(CONF_AVAILABILITY_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch"])
                ),
                vol.Optional(CONF_IR_FORMAT, default="auto"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="auto", label="Auto-Detect (Recommended)"),
                            selector.SelectOptionDict(value="raw", label="Home Assistant Infrared / ESPHome (Hardware-Tested & Confirmed)"),
                            selector.SelectOptionDict(value="tasmota", label="Tasmota / AEHA Hex (Capture Verified)"),
                            selector.SelectOptionDict(value="broadlink", label="Broadlink Base64 (Format Verified)"),
                            selector.SelectOptionDict(value="tuya", label="Tuya Base64 (Format Verified)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_PRIMARY_BACKEND): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="cloud", label="Cloud"),
                            selector.SelectOptionDict(value="ir", label="Infrared"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_HYBRID_SUBMODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="auto", label="Automatic Failover (Switch to secondary transport on outage)"),
                            selector.SelectOptionDict(value="manual", label="Manual Control"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def async_step_device_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure settings directly for this AC unit."""
        today = date.today()
        default_install = six_months_ago(today).isoformat()

        entry = self._get_config_entry()
        current_options = dict(getattr(entry, "options", {})) if entry else {}
        entry_data = getattr(entry, "data", {}) if isinstance(getattr(entry, "data", {}), dict) else {}
        target_id = entry_data.get("device_id", "")
        devices_opt = current_options.get("devices", {})
        target_opt = devices_opt.get(target_id, {}) if target_id else {}

        dev_name = entry_data.get("device_name") or entry_data.get("name") or getattr(entry, "title", "AC Unit")

        coord = None
        hub = getattr(entry, "runtime_data", None) if entry else None
        if hub and hasattr(hub, "coordinators"):
            coordinators = getattr(hub, "coordinators", {})
            if target_id and target_id in coordinators:
                coord = coordinators[target_id]
            elif coordinators and len(coordinators) > 0:
                coord = list(coordinators.values())[0]

        coord_model = getattr(coord, "model_code", "") if coord else ""

        current_install = target_opt.get(CONF_INSTALL_DATE, current_options.get(CONF_INSTALL_DATE, default_install))
        current_blaster = target_opt.get(CONF_BLASTER_ENTITY_ID, current_options.get(CONF_BLASTER_ENTITY_ID, entry_data.get(CONF_BLASTER_ENTITY_ID, "")))
        current_receiver = target_opt.get(CONF_RECEIVER_ENTITY_ID, current_options.get(CONF_RECEIVER_ENTITY_ID, entry_data.get(CONF_RECEIVER_ENTITY_ID, "")))
        current_temp_sensor = target_opt.get(CONF_ROOM_TEMP_SENSOR, current_options.get(CONF_ROOM_TEMP_SENSOR, entry_data.get(CONF_ROOM_TEMP_SENSOR, "")))
        current_avail = target_opt.get(CONF_AVAILABILITY_ENTITY_ID, current_options.get(CONF_AVAILABILITY_ENTITY_ID, entry_data.get(CONF_AVAILABILITY_ENTITY_ID, "")))
        current_ir_fmt = target_opt.get(CONF_IR_FORMAT, current_options.get(CONF_IR_FORMAT, entry_data.get(CONF_IR_FORMAT, "auto")))
        current_backend = target_opt.get(CONF_PRIMARY_BACKEND, current_options.get(CONF_PRIMARY_BACKEND, "cloud"))
        current_submode = target_opt.get(CONF_HYBRID_SUBMODE, current_options.get(CONF_HYBRID_SUBMODE, "auto"))

        raw_model = (
            target_opt.get("model_code")
            or current_options.get("model_code")
            or coord_model
            or entry_data.get("model_code")
            or entry_data.get("model_number")
            or entry_data.get("model_name")
        )
        current_model = raw_model if (raw_model and raw_model != entry_data.get("device_id") and raw_model != dev_name) else "CS-CU Series"

        if user_input is None:
            schema = self._build_device_settings_schema()
            suggested = {
                CONF_INSTALL_DATE: current_install,
                CONF_BLASTER_ENTITY_ID: current_blaster,
                CONF_RECEIVER_ENTITY_ID: current_receiver,
                CONF_ROOM_TEMP_SENSOR: current_temp_sensor,
                CONF_AVAILABILITY_ENTITY_ID: current_avail,
                CONF_IR_FORMAT: current_ir_fmt,
                CONF_PRIMARY_BACKEND: current_backend,
                CONF_HYBRID_SUBMODE: current_submode,
            }
            if hasattr(self, "add_suggested_values_to_schema"):
                schema = self.add_suggested_values_to_schema(schema, suggested)
            return self.async_show_form(
                step_id="device_settings",
                data_schema=schema,
                description_placeholders={
                    "device_name": dev_name,
                    "model_code": current_model,
                },
            )

        errors = {}
        try:
            install_date = parse_install_date(user_input.get(CONF_INSTALL_DATE, ""))
            min_date = six_months_ago(today)
            oldest_date = eight_months_ago(today)
            if install_date is None:
                install_date = min_date
            if install_date < oldest_date or install_date > today:
                errors[CONF_INSTALL_DATE] = "invalid_install_date"
                raise InvalidInstallDate
        except InvalidInstallDate:
            errors[CONF_INSTALL_DATE] = "invalid_install_date"
            schema = self._build_device_settings_schema()
            if hasattr(self, "add_suggested_values_to_schema"):
                schema = self.add_suggested_values_to_schema(schema, user_input)
            return self.async_show_form(
                step_id="device_settings",
                data_schema=schema,
                description_placeholders={
                    "device_name": dev_name,
                    "model_code": current_model,
                },
                errors=errors,
            )

        new_options = dict(current_options)
        install_date_str = install_date.isoformat()
        blaster_raw = user_input.get(CONF_BLASTER_ENTITY_ID)
        blaster_val = "" if (blaster_raw is None or str(blaster_raw).lower() in ("none", "null", "")) else str(blaster_raw).strip()
        receiver_raw = user_input.get(CONF_RECEIVER_ENTITY_ID)
        receiver_val = "" if (receiver_raw is None or str(receiver_raw).lower() in ("none", "null", "")) else str(receiver_raw).strip()
        temp_sensor_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
        temp_sensor_val = "" if (temp_sensor_raw is None or str(temp_sensor_raw).lower() in ("none", "null", "")) else str(temp_sensor_raw).strip()
        avail_raw = user_input.get(CONF_AVAILABILITY_ENTITY_ID)
        avail_val = "" if (avail_raw is None or str(avail_raw).lower() in ("none", "null", "")) else str(avail_raw).strip()
        ir_fmt_val = user_input.get(CONF_IR_FORMAT, "auto")
        backend_val = user_input.get(CONF_PRIMARY_BACKEND, "cloud")

        # If adding/re-adding an IR blaster (was empty, now set), default Hybrid Automatic Control to ON ("auto")
        if not current_blaster and blaster_val:
            submode_val = user_input.get(CONF_HYBRID_SUBMODE) or "auto"
        elif not blaster_val:
            submode_val = "manual"
        else:
            submode_val = user_input.get(CONF_HYBRID_SUBMODE) or current_submode or "auto"

        model_code_val = user_input.get("model_code", "").strip().upper()

        new_options[CONF_INSTALL_DATE] = install_date_str
        new_options[CONF_BLASTER_ENTITY_ID] = blaster_val
        new_options[CONF_RECEIVER_ENTITY_ID] = receiver_val
        new_options[CONF_ROOM_TEMP_SENSOR] = temp_sensor_val
        new_options[CONF_AVAILABILITY_ENTITY_ID] = avail_val
        new_options[CONF_IR_FORMAT] = ir_fmt_val
        if model_code_val:
            new_options["model_code"] = model_code_val
        new_options[CONF_PRIMARY_BACKEND] = backend_val
        new_options[CONF_HYBRID_SUBMODE] = submode_val

        if target_id:
            new_devices = dict(new_options.get("devices", {}))
            dev_entry = dict(new_devices.get(target_id, {}))
            dev_entry[CONF_INSTALL_DATE] = install_date_str
            dev_entry[CONF_BLASTER_ENTITY_ID] = blaster_val
            dev_entry[CONF_RECEIVER_ENTITY_ID] = receiver_val
            dev_entry[CONF_ROOM_TEMP_SENSOR] = temp_sensor_val
            dev_entry[CONF_AVAILABILITY_ENTITY_ID] = avail_val
            dev_entry[CONF_IR_FORMAT] = ir_fmt_val
            if model_code_val:
                dev_entry["model_code"] = model_code_val
            dev_entry[CONF_PRIMARY_BACKEND] = backend_val
            dev_entry[CONF_HYBRID_SUBMODE] = submode_val
            new_devices[target_id] = dev_entry
            new_options["devices"] = new_devices

        return self.async_create_entry(title="", data=new_options)

