"""Config flow for mirAIe integration."""
from __future__ import annotations

import logging
import ipaddress
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
    CONF_MQTT_TOPIC,
    CONF_RECEIVER_ENTITY_ID,
    CONF_ROOM_TEMP_SENSOR,
    CONF_AVAILABILITY_ENTITY_ID,
    CONF_AVAILABILITY_TOPIC,
    CONF_IR_FORMAT,
    CONF_PRIMARY_BACKEND,
    CONF_SECONDARY_BACKEND,
    CONF_HYBRID_SUBMODE,
    CONF_SETUP_MODE,
    CONF_DEVICE_IP,
    CONF_DEVICE_MAC,
    CONF_UDP_CONTROL_KEY,
    CONF_UDP_CONTROL_IV,
    CONF_CONTROLLER_ID,
    DEFAULT_CONTROLLER_ID,
    CONF_MODEL_CODE,
    SETUP_MODE_LOCAL_UDP,
    SETUP_MODE_CLOUD,
    SETUP_MODE_HYBRID,
    SETUP_MODE_IR,
    BACKEND_LOCAL_UDP,
    BACKEND_CLOUD,
    BACKEND_IR,
    DOMAIN,
)
from .utils import (
    six_months_ago,
    eight_months_ago,
    is_ac_device,
    is_mqtt_topic,
    validate_mqtt_topic,
    get_entry_data,
    async_resolve_device_ip,
    async_benchmark_transports,
)

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


def build_simple_devices_schema(default_install_date: str) -> vol.Schema:
    """Schema for single-transport Wi-Fi devices (pure Cloud or pure Local UDP)."""
    return vol.Schema(
        {
            vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_INSTALL_DATE, default=default_install_date): selector.DateSelector(),
        }
    )


def build_local_udp_devices_schema(
    default_install_date: str,
    default_ip: str = "",
    default_controller: str = DEFAULT_CONTROLLER_ID,
) -> vol.Schema:
    """Schema for Local UDP devices discovered via cloud bootstrap."""
    fields: dict[Any, Any] = {}
    if default_ip:
        fields[vol.Required(CONF_DEVICE_IP, default=default_ip)] = selector.TextSelector()
    else:
        fields[vol.Required(CONF_DEVICE_IP)] = selector.TextSelector()
    fields[vol.Optional(CONF_CONTROLLER_ID, default=default_controller)] = selector.TextSelector()
    fields[vol.Optional(CONF_ROOM_TEMP_SENSOR)] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
    )
    fields[vol.Optional(CONF_INSTALL_DATE, default=default_install_date)] = selector.DateSelector()
    return vol.Schema(fields)


def build_local_udp_manual_schema(default_install_date: str) -> vol.Schema:
    """Schema for manual Local UDP key and IP configuration without cloud login."""
    return vol.Schema(
        {
            vol.Required("name"): selector.TextSelector(),
            vol.Required(CONF_DEVICE_IP): selector.TextSelector(),
            vol.Required(CONF_DEVICE_MAC): selector.TextSelector(),
            vol.Optional(CONF_MODEL_CODE): selector.TextSelector(),
            vol.Required(CONF_UDP_CONTROL_KEY): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_UDP_CONTROL_IV): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(CONF_CONTROLLER_ID, default=DEFAULT_CONTROLLER_ID): selector.TextSelector(),
            vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_INSTALL_DATE, default=default_install_date): selector.DateSelector(),
        }
    )


def build_hybrid_devices_schema(
    default_install_date: str,
    supports_udp: bool = True,
    default_ip: str = "",
    default_controller: str = DEFAULT_CONTROLLER_ID,
) -> vol.Schema:
    """Schema for Hybrid control with primary and secondary failover."""
    backend_options = [
        selector.SelectOptionDict(value="cloud", label="Cloud MQTT"),
        selector.SelectOptionDict(value="ir", label="Infrared Blaster"),
    ]
    if supports_udp:
        backend_options.insert(0, selector.SelectOptionDict(value="local_udp", label="Local UDP (LAN)"))

    secondary_options = [
        selector.SelectOptionDict(value="none", label="None (Single Transport Only)"),
        selector.SelectOptionDict(value="cloud", label="Cloud MQTT"),
        selector.SelectOptionDict(value="ir", label="Infrared Blaster"),
    ]
    if supports_udp:
        secondary_options.insert(1, selector.SelectOptionDict(value="local_udp", label="Local UDP (LAN)"))

    primary_default = "local_udp" if supports_udp else "cloud"
    secondary_default = "cloud" if supports_udp else "none"

    fields: dict[Any, Any] = {
        vol.Optional(CONF_PRIMARY_BACKEND, default=primary_default): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=backend_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_SECONDARY_BACKEND, default=secondary_default): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=secondary_options,
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
    }

    if supports_udp:
        if default_ip:
            fields[vol.Optional(CONF_DEVICE_IP, default=default_ip)] = selector.TextSelector()
        else:
            fields[vol.Optional(CONF_DEVICE_IP)] = selector.TextSelector()
        fields[vol.Optional(CONF_CONTROLLER_ID, default=default_controller)] = selector.TextSelector()

    fields.update(
        {
            vol.Optional(CONF_MQTT_TOPIC): selector.TextSelector(),
            vol.Optional(CONF_BLASTER_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["infrared", "remote"])
            ),
            vol.Optional(CONF_RECEIVER_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["infrared", "remote"])
            ),
            vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_AVAILABILITY_TOPIC): selector.TextSelector(),
            vol.Optional(CONF_AVAILABILITY_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch", "device_tracker"])
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

    return vol.Schema(fields)


def build_cloud_devices_schema(default_install_date: str, supports_udp: bool = True) -> vol.Schema:
    """Backward compatible alias for build_hybrid_devices_schema."""
    return build_hybrid_devices_schema(default_install_date, supports_udp=supports_udp)


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
    cloud_latency_ms: float | None = None
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
                    t_cloud_start = time.perf_counter()
                    res = await hub.http.get(
                        "https://app.miraie.in/simplifi/v1/homeManagement/homes",
                        headers=headers,
                    )
                    cloud_latency_ms = round((time.perf_counter() - t_cloud_start) * 1000, 1)
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
                                rd["udp_control_key"] = dt.get("udpControlKey")
                                rd["udp_control_iv"] = dt.get("udpControlIV")
                                rd["mac_address"] = dt.get("macAddress")
                                rd["ip_address"] = dt.get("ip") or dt.get("ipAddress")

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
                            dt = getattr(dev, "details", None)
                            if dt:
                                model_number = getattr(dt, "model_number", None)
                            udp_key = getattr(dt, "udp_control_key", None) if dt else None
                            udp_iv = getattr(dt, "udp_control_iv", None) if dt else None
                            mac = getattr(dt, "mac_address", None) if dt else None
                            discovered_devices.append({
                                "id": dev_id,
                                "name": getattr(dev, "friendly_name", "MirAIe Cloud AC"),
                                "model_code": model_number or "",
                                "udp_control_key": udp_key,
                                "udp_control_iv": udp_iv,
                                "mac_address": mac,
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
        hub_http = getattr(hub, "http", None)
        if hub_http and hub_http is not session and not getattr(hub_http, "closed", True):
            await hub_http.close()

    return {"title": "MirAIe Cloud Account", "cloud_latency_ms": cloud_latency_ms}, discovered_devices


def _get_existing_device_identifiers(entries: list[config_entries.ConfigEntry]) -> tuple[set[str], set[str]]:
    """Return sets of existing device IDs (lowercase) and normalized MAC addresses (uppercase)."""
    ids: set[str] = set()
    macs: set[str] = set()
    for e in entries:
        dev_id = e.data.get("device_id")
        if dev_id:
            ids.add(str(dev_id).strip().lower())
        mac = e.data.get(CONF_DEVICE_MAC) or e.data.get("mac_address")
        if mac:
            macs.add(str(mac).replace(":", "").replace("-", "").strip().upper())
        devices_dict = e.options.get("devices", {})
        if isinstance(devices_dict, dict):
            for d_id, d_data in devices_dict.items():
                ids.add(str(d_id).strip().lower())
                if isinstance(d_data, dict):
                    d_mac = d_data.get(CONF_DEVICE_MAC) or d_data.get("mac_address")
                    if d_mac:
                        macs.add(str(d_mac).replace(":", "").replace("-", "").strip().upper())
    return ids, macs


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for mirAIe."""

    VERSION = 3

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
        """Step 1: Choose setup mode."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["hybrid", "ir_device", "local_udp", "cloud"]
        )

    async def async_step_local_udp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between One-Time Cloud Bootstrap and Manual Key Entry for Local UDP."""
        return self.async_show_menu(
            step_id="local_udp",
            menu_options=["local_udp_auto", "local_udp_manual"],
        )

    async def async_step_local_udp_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual IP, MAC, and encryption key entry for pure offline Local UDP."""
        default_install = six_months_ago(date.today()).isoformat()
        if user_input is None:
            return self.async_show_form(
                step_id="local_udp_manual",
                data_schema=build_local_udp_manual_schema(default_install),
            )

        errors = {}
        ip_str = user_input.get(CONF_DEVICE_IP, "").strip()
        try:
            import ipaddress
            ip_obj = ipaddress.ip_address(ip_str)
            if not isinstance(ip_obj, ipaddress.IPv4Address):
                errors[CONF_DEVICE_IP] = "invalid_ip"
        except ValueError:
            errors[CONF_DEVICE_IP] = "invalid_ip"

        mac_raw = user_input.get(CONF_DEVICE_MAC, "").strip()
        clean_mac = mac_raw.replace(":", "").replace("-", "").upper()
        if len(clean_mac) != 12 or not all(c in "0123456789ABCDEF" for c in clean_mac):
            errors[CONF_DEVICE_MAC] = "invalid_mac"

        key_raw = user_input.get(CONF_UDP_CONTROL_KEY, "").strip()
        try:
            key_bytes = bytes.fromhex(key_raw)
            if len(key_bytes) != 32:
                errors[CONF_UDP_CONTROL_KEY] = "invalid_key"
        except (ValueError, TypeError):
            errors[CONF_UDP_CONTROL_KEY] = "invalid_key"

        iv_raw = user_input.get(CONF_UDP_CONTROL_IV, "").strip()
        try:
            iv_bytes = bytes.fromhex(iv_raw)
            if len(iv_bytes) != 16:
                errors[CONF_UDP_CONTROL_IV] = "invalid_iv"
        except (ValueError, TypeError):
            errors[CONF_UDP_CONTROL_IV] = "invalid_iv"

        try:
            install_date = parse_install_date(user_input.get(CONF_INSTALL_DATE, ""))
            today = date.today()
            min_date = six_months_ago(today)
            oldest_date = eight_months_ago(today)
            if install_date is None:
                install_date = min_date
            if install_date < oldest_date or install_date > today:
                errors[CONF_INSTALL_DATE] = "invalid_install_date"
        except InvalidInstallDate:
            errors[CONF_INSTALL_DATE] = "invalid_install_date"

        if errors:
            return self.async_show_form(
                step_id="local_udp_manual",
                data_schema=build_local_udp_manual_schema(default_install),
                errors=errors,
            )

        dev_name = user_input.get("name", "AC Unit").strip()
        model_code = user_input.get(CONF_MODEL_CODE, "").strip().upper()
        temp_sensor = user_input.get(CONF_ROOM_TEMP_SENSOR, "")
        ctrl_id = str(user_input.get(CONF_CONTROLLER_ID, DEFAULT_CONTROLLER_ID)).strip() or DEFAULT_CONTROLLER_ID
        unique_id = f"udp_{clean_mac.lower()}"

        existing_ids, existing_macs = _get_existing_device_identifiers(self._async_current_entries())
        if clean_mac in existing_macs or clean_mac.lower() in existing_ids:
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        entry_data = {
            "name": dev_name,
            "device_name": dev_name,
            "device_id": clean_mac.lower(),
            "model_code": model_code,
            "is_ir_only": False,
            CONF_DEVICE_IP: ip_str,
            CONF_DEVICE_MAC: clean_mac,
            CONF_UDP_CONTROL_KEY: key_raw,
            CONF_UDP_CONTROL_IV: iv_raw,
            CONF_CONTROLLER_ID: ctrl_id,
            CONF_PRIMARY_BACKEND: BACKEND_LOCAL_UDP,
            CONF_SECONDARY_BACKEND: "none",
            CONF_SETUP_MODE: SETUP_MODE_LOCAL_UDP,
        }
        entry_options = {
            CONF_INSTALL_DATE: install_date.isoformat(),
            CONF_CONTROLLER_ID: ctrl_id,
            CONF_PRIMARY_BACKEND: BACKEND_LOCAL_UDP,
            CONF_SECONDARY_BACKEND: "none",
            CONF_SETUP_MODE: SETUP_MODE_LOCAL_UDP,
            "model_code": model_code,
        }
        if temp_sensor:
            entry_options[CONF_ROOM_TEMP_SENSOR] = str(temp_sensor).strip()

        return self.async_create_entry(
            title=dev_name,
            data=entry_data,
            options=entry_options,
        )

    async def async_step_local_udp_auto(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One-Time MirAIe Cloud Bootstrap to extract Local UDP keys."""
        if user_input is None:
            return self.async_show_form(
                step_id="local_udp_auto", data_schema=build_login_schema()
            )

        errors = {}
        try:
            info, discovered_devices = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception during cloud login for UDP auto setup")
            errors["base"] = "unknown"
        else:
            if not discovered_devices:
                return self.async_abort(reason="no_devices_found")

            udp_devices = [d for d in discovered_devices if d.get("udp_control_key") and d.get("udp_control_iv")]
            if not udp_devices:
                return self.async_abort(reason="no_udp_devices_found")

            existing_dev_ids, existing_macs = _get_existing_device_identifiers(self._async_current_entries())
            new_devices = [
                d for d in udp_devices
                if str(d.get("id", "")).strip().lower() not in existing_dev_ids
                and (
                    not d.get("mac_address")
                    or str(d.get("mac_address")).replace(":", "").replace("-", "").strip().upper() not in existing_macs
                )
            ]

            if not new_devices:
                return self.async_abort(reason="already_configured")

            self._cloud_credentials = user_input
            self._cloud_latency_ms = info.get("cloud_latency_ms")
            self._discovered_cloud_devices = new_devices
            self._current_device_index = 0
            self._per_device_options = {}
            self._setup_mode = SETUP_MODE_LOCAL_UDP
            return await self.async_step_local_udp_devices()

        return self.async_show_form(
            step_id="local_udp_auto",
            data_schema=build_login_schema(),
            errors=errors,
        )

    async def async_step_local_udp_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure each discovered Wi-Fi AC unit for pure offline Local UDP."""
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
        default_install = six_months_ago(date.today()).isoformat()

        if not current_dev.get("ip_address"):
            resolved_ip = await async_resolve_device_ip(
                device_id=current_dev.get("id"),
                mac_address=current_dev.get("mac_address"),
            )
            if resolved_ip:
                current_dev["ip_address"] = resolved_ip

        default_ip = current_dev.get("ip_address") or ""

        if not hasattr(self, "_benchmark_cache"):
            self._benchmark_cache = {}

        dev_id = str(current_dev.get("id", ""))
        speed_test_str = self._benchmark_cache.get(dev_id, "")
        if not speed_test_str and default_ip and current_dev.get("udp_control_key") and current_dev.get("udp_control_iv") and user_input is None:
            try:
                bench = await async_benchmark_transports(
                    device_id=dev_id,
                    ip_address=default_ip,
                    key=current_dev.get("udp_control_key"),
                    iv=current_dev.get("udp_control_iv"),
                )
                udp_res = bench.get("local_udp", {})
                if udp_res.get("available") and udp_res.get("latency_ms") is not None:
                    speed_test_str = f" | Speed Test: Local UDP **{udp_res['latency_ms']} ms**"
                    self._benchmark_cache[dev_id] = speed_test_str
            except Exception as err:
                _LOGGER.debug("Speed test during local udp config flow failed: %s", err)

        model_display = f"Model: **{dev_model}**{speed_test_str}" if dev_model else f"Model: Auto-detecting via Cloud{speed_test_str}"

        if user_input is None:
            return self.async_show_form(
                step_id="local_udp_devices",
                data_schema=build_local_udp_devices_schema(default_install, default_ip=default_ip),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                },
            )

        errors = {}
        ip_str = user_input.get(CONF_DEVICE_IP, "").strip() if user_input.get(CONF_DEVICE_IP) else ""
        if ip_str:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if not isinstance(ip_obj, ipaddress.IPv4Address):
                    errors[CONF_DEVICE_IP] = "invalid_ip"
            except ValueError:
                errors[CONF_DEVICE_IP] = "invalid_ip"
        else:
            errors[CONF_DEVICE_IP] = "invalid_ip"

        try:
            install_date = parse_install_date(user_input.get(CONF_INSTALL_DATE, ""))
            today = date.today()
            min_date = six_months_ago(today)
            oldest_date = eight_months_ago(today)
            if install_date is None:
                install_date = min_date
            if install_date < oldest_date or install_date > today:
                errors[CONF_INSTALL_DATE] = "invalid_install_date"
        except InvalidInstallDate:
            errors[CONF_INSTALL_DATE] = "invalid_install_date"

        if errors:
            return self.async_show_form(
                step_id="local_udp_devices",
                data_schema=build_local_udp_devices_schema(
                    user_input.get(CONF_INSTALL_DATE, default_install),
                    default_ip=ip_str or default_ip,
                    default_controller=user_input.get(CONF_CONTROLLER_ID, DEFAULT_CONTROLLER_ID),
                ),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                },
                errors=errors,
            )

        clean_mac = (current_dev.get("mac_address") or "").replace(":", "").replace("-", "").upper()
        unique_id = f"udp_{clean_mac.lower()}" if clean_mac else f"udp_{current_dev['id']}"

        temp_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
        temp_val = str(temp_raw).strip() if (temp_raw and str(temp_raw).lower() not in ("none", "null", "")) else ""
        ctrl_val = str(user_input.get(CONF_CONTROLLER_ID, DEFAULT_CONTROLLER_ID)).strip() or DEFAULT_CONTROLLER_ID

        entry_data = {
            "name": dev_name,
            "device_name": dev_name,
            "device_id": current_dev["id"],
            "model_code": dev_model,
            "is_ir_only": False,
            CONF_DEVICE_IP: ip_str,
            CONF_DEVICE_MAC: clean_mac,
            CONF_UDP_CONTROL_KEY: current_dev.get("udp_control_key"),
            CONF_UDP_CONTROL_IV: current_dev.get("udp_control_iv"),
            CONF_CONTROLLER_ID: ctrl_val,
            CONF_PRIMARY_BACKEND: BACKEND_LOCAL_UDP,
            CONF_SECONDARY_BACKEND: "none",
            CONF_SETUP_MODE: SETUP_MODE_LOCAL_UDP,
        }
        dev_opt = {
            CONF_INSTALL_DATE: install_date.isoformat(),
            CONF_PRIMARY_BACKEND: BACKEND_LOCAL_UDP,
            CONF_SECONDARY_BACKEND: "none",
            CONF_SETUP_MODE: SETUP_MODE_LOCAL_UDP,
            CONF_DEVICE_IP: ip_str,
            CONF_CONTROLLER_ID: ctrl_val,
            "model_code": dev_model,
        }
        if temp_val:
            dev_opt[CONF_ROOM_TEMP_SENSOR] = temp_val

        blaster_val = user_input.get(CONF_BLASTER_ENTITY_ID)
        if blaster_val:
            entry_data[CONF_BLASTER_ENTITY_ID] = blaster_val
            dev_opt[CONF_BLASTER_ENTITY_ID] = blaster_val

        if user_input.get(CONF_PRIMARY_BACKEND):
            entry_data[CONF_PRIMARY_BACKEND] = user_input[CONF_PRIMARY_BACKEND]
            dev_opt[CONF_PRIMARY_BACKEND] = user_input[CONF_PRIMARY_BACKEND]

        created = getattr(self, "_created_entries", [])
        created.append({
            "title": dev_name,
            "data": entry_data,
            "options": dev_opt,
            "unique_id": unique_id,
        })
        self._created_entries = created

        self._current_device_index = idx + 1
        if self._current_device_index < len(devices):
            return await self.async_step_local_udp_devices()

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
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=last_item["title"],
            data=last_item["data"],
            options=last_item["options"],
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """MirAIe Cloud Only Setup."""
        if user_input is None:
            return self.async_show_form(
                step_id="cloud", data_schema=build_login_schema()
            )

        errors = {}
        try:
            info, discovered_devices = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception during cloud login")
            errors["base"] = "unknown"
        else:
            if not discovered_devices:
                return self.async_abort(reason="no_devices_found")

            existing_dev_ids, existing_macs = _get_existing_device_identifiers(self._async_current_entries())
            new_devices = [
                d for d in discovered_devices
                if str(d.get("id", "")).strip().lower() not in existing_dev_ids
                and (
                    not d.get("mac_address")
                    or str(d.get("mac_address")).replace(":", "").replace("-", "").strip().upper() not in existing_macs
                )
            ]

            if not new_devices:
                return self.async_abort(reason="already_configured")

            self._cloud_credentials = user_input
            self._cloud_latency_ms = info.get("cloud_latency_ms")
            self._discovered_cloud_devices = new_devices
            self._current_device_index = 0
            self._per_device_options = {}
            self._setup_mode = SETUP_MODE_CLOUD
            return await self.async_step_cloud_devices()

        return self.async_show_form(
            step_id="cloud",
            data_schema=build_login_schema(),
            errors=errors,
        )

    async def async_step_cloud_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure each discovered Wi-Fi AC unit for pure MirAIe Cloud."""
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
                data_schema=build_simple_devices_schema(default_install),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
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
        except InvalidInstallDate:
            errors[CONF_INSTALL_DATE] = "invalid_install_date"

        if errors:
            return self.async_show_form(
                step_id="cloud_devices",
                data_schema=build_simple_devices_schema(
                    user_input.get(CONF_INSTALL_DATE, default_install)
                ),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                },
                errors=errors,
            )

        temp_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
        temp_val = str(temp_raw).strip() if (temp_raw and str(temp_raw).lower() not in ("none", "null", "")) else ""

        entry_data = {
            "username": creds.get("username", ""),
            "password": creds.get("password", ""),
            "device_id": current_dev["id"],
            "device_name": dev_name,
            "model_code": dev_model,
            "is_ir_only": False,
            CONF_PRIMARY_BACKEND: BACKEND_CLOUD,
            CONF_SECONDARY_BACKEND: "none",
            CONF_SETUP_MODE: SETUP_MODE_CLOUD,
        }
        clean_mac = (current_dev.get("mac_address") or "").replace(":", "").replace("-", "").strip().upper()
        if clean_mac:
            entry_data[CONF_DEVICE_MAC] = clean_mac

        dev_opt = {
            CONF_INSTALL_DATE: install_date.isoformat(),
            CONF_PRIMARY_BACKEND: BACKEND_CLOUD,
            CONF_SECONDARY_BACKEND: "none",
            CONF_SETUP_MODE: SETUP_MODE_CLOUD,
            "model_code": dev_model,
        }
        if clean_mac:
            dev_opt[CONF_DEVICE_MAC] = clean_mac
        if temp_val:
            dev_opt[CONF_ROOM_TEMP_SENSOR] = temp_val

        blaster_val = user_input.get(CONF_BLASTER_ENTITY_ID)
        if blaster_val:
            entry_data[CONF_BLASTER_ENTITY_ID] = blaster_val
            dev_opt[CONF_BLASTER_ENTITY_ID] = blaster_val

        if user_input.get(CONF_PRIMARY_BACKEND):
            entry_data[CONF_PRIMARY_BACKEND] = user_input[CONF_PRIMARY_BACKEND]
            dev_opt[CONF_PRIMARY_BACKEND] = user_input[CONF_PRIMARY_BACKEND]

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
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=last_item["title"],
            data=last_item["data"],
            options=last_item["options"],
        )

    async def async_step_hybrid(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """MirAIe Cloud Login for Hybrid Setup."""
        if user_input is None:
            return self.async_show_form(
                step_id="hybrid", data_schema=build_login_schema()
            )

        errors = {}
        try:
            info, discovered_devices = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected exception during hybrid cloud login")
            errors["base"] = "unknown"
        else:
            if not discovered_devices:
                return self.async_abort(reason="no_devices_found")

            existing_dev_ids, existing_macs = _get_existing_device_identifiers(self._async_current_entries())
            new_devices = [
                d for d in discovered_devices
                if str(d.get("id", "")).strip().lower() not in existing_dev_ids
                and (
                    not d.get("mac_address")
                    or str(d.get("mac_address")).replace(":", "").replace("-", "").strip().upper() not in existing_macs
                )
            ]

            if not new_devices:
                return self.async_abort(reason="already_configured")

            self._cloud_credentials = user_input
            self._cloud_latency_ms = info.get("cloud_latency_ms")
            self._discovered_cloud_devices = new_devices
            self._current_device_index = 0
            self._per_device_options = {}
            self._setup_mode = SETUP_MODE_HYBRID
            return await self.async_step_hybrid_devices()

        return self.async_show_form(
            step_id="hybrid",
            data_schema=build_login_schema(),
            errors=errors,
        )

    async def async_step_hybrid_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (Hybrid): Configure dual-transport failover options for each AC unit."""
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
        default_install = six_months_ago(date.today()).isoformat()
        has_udp = bool(current_dev.get("udp_control_key") and current_dev.get("udp_control_iv"))

        if has_udp and not current_dev.get("ip_address"):
            resolved_ip = await async_resolve_device_ip(
                device_id=current_dev.get("id"),
                mac_address=current_dev.get("mac_address"),
            )
            if resolved_ip:
                current_dev["ip_address"] = resolved_ip

        default_ip = current_dev.get("ip_address") or ""

        if not hasattr(self, "_benchmark_cache"):
            self._benchmark_cache = {}

        dev_id = str(current_dev.get("id", ""))
        speed_test_str = self._benchmark_cache.get(dev_id, "")
        if not speed_test_str and user_input is None:
            parts = []
            if default_ip and has_udp:
                try:
                    bench = await async_benchmark_transports(
                        device_id=dev_id,
                        ip_address=default_ip,
                        key=current_dev.get("udp_control_key"),
                        iv=current_dev.get("udp_control_iv"),
                    )
                    udp_res = bench.get("local_udp", {})
                    if udp_res.get("available") and udp_res.get("latency_ms") is not None:
                        parts.append(f"Local UDP **{udp_res['latency_ms']} ms**")
                except Exception as err:
                    _LOGGER.debug("Speed test during hybrid config flow failed: %s", err)

            if getattr(self, "_cloud_latency_ms", None) is not None:
                parts.append(f"Cloud API **{self._cloud_latency_ms} ms**")

            if parts:
                speed_test_str = f" | Speed Test: {' | '.join(parts)}"
                self._benchmark_cache[dev_id] = speed_test_str

        model_display = f"Model: **{dev_model}**{speed_test_str}" if dev_model else f"Model: Auto-detecting via Cloud{speed_test_str}"

        if user_input is None:
            return self.async_show_form(
                step_id="hybrid_devices",
                data_schema=build_hybrid_devices_schema(default_install, supports_udp=has_udp, default_ip=default_ip),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
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
        except InvalidInstallDate:
            errors[CONF_INSTALL_DATE] = "invalid_install_date"

        blaster_val = user_input.get(CONF_BLASTER_ENTITY_ID, "").strip() if user_input.get(CONF_BLASTER_ENTITY_ID) else ""
        mqtt_topic_raw = user_input.get(CONF_MQTT_TOPIC)
        mqtt_topic = "" if (mqtt_topic_raw is None or str(mqtt_topic_raw).lower() in ("none", "null", "")) else str(mqtt_topic_raw).strip()
        avail_topic_raw = user_input.get(CONF_AVAILABILITY_TOPIC)
        avail_topic = "" if (avail_topic_raw is None or str(avail_topic_raw).lower() in ("none", "null", "")) else str(avail_topic_raw).strip()
        backend_val = user_input.get(CONF_PRIMARY_BACKEND, "local_udp" if has_udp else "cloud")
        secondary_backend_val = user_input.get(CONF_SECONDARY_BACKEND, "cloud" if has_udp else "none")
        submode_val = user_input.get(CONF_HYBRID_SUBMODE, "auto")
        ir_fmt = user_input.get(CONF_IR_FORMAT, "auto")

        if ir_fmt == "tasmota":
            if mqtt_topic and blaster_val:
                errors["base"] = "both_endpoints_provided"
            elif mqtt_topic and not validate_mqtt_topic(mqtt_topic):
                errors["base"] = "invalid_mqtt_topic"
            elif avail_topic and not validate_mqtt_topic(avail_topic):
                errors["base"] = "invalid_availability_topic"
            elif mqtt_topic:
                blaster_val = mqtt_topic
        else:
            if mqtt_topic:
                errors["base"] = "topic_not_supported_for_format"
            elif avail_topic and not validate_mqtt_topic(avail_topic):
                errors["base"] = "invalid_availability_topic"

        if (backend_val == "ir" or secondary_backend_val == "ir") and not blaster_val and not errors:
            errors[CONF_BLASTER_ENTITY_ID] = "blaster_required_for_ir"

        user_ip = str(user_input.get(CONF_DEVICE_IP, "")).strip() if user_input.get(CONF_DEVICE_IP) else ""
        if user_ip:
            try:
                ip_obj = ipaddress.ip_address(user_ip)
                if not isinstance(ip_obj, ipaddress.IPv4Address):
                    errors[CONF_DEVICE_IP] = "invalid_ip"
            except ValueError:
                errors[CONF_DEVICE_IP] = "invalid_ip"

        if errors:
            return self.async_show_form(
                step_id="hybrid_devices",
                data_schema=build_hybrid_devices_schema(
                    user_input.get(CONF_INSTALL_DATE, default_install),
                    supports_udp=has_udp,
                    default_ip=user_input.get(CONF_DEVICE_IP, default_ip),
                    default_controller=user_input.get(CONF_CONTROLLER_ID, DEFAULT_CONTROLLER_ID),
                ),
                description_placeholders={
                    "device_name": dev_name,
                    "model_display": model_display,
                },
                errors=errors,
            )

        dev_opt = {
            CONF_INSTALL_DATE: install_date.isoformat(),
            CONF_PRIMARY_BACKEND: backend_val,
            CONF_SECONDARY_BACKEND: secondary_backend_val,
            CONF_HYBRID_SUBMODE: submode_val,
            CONF_SETUP_MODE: SETUP_MODE_HYBRID,
            "model_code": dev_model,
        }

        if blaster_val:
            dev_opt[CONF_BLASTER_ENTITY_ID] = blaster_val
        if mqtt_topic:
            dev_opt[CONF_MQTT_TOPIC] = mqtt_topic
        if avail_topic:
            dev_opt[CONF_AVAILABILITY_TOPIC] = avail_topic
        receiver_raw = user_input.get(CONF_RECEIVER_ENTITY_ID)
        if receiver_raw and str(receiver_raw).lower() not in ("none", "null", ""):
            dev_opt[CONF_RECEIVER_ENTITY_ID] = str(receiver_raw).strip()
        temp_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
        if temp_raw and str(temp_raw).lower() not in ("none", "null", ""):
            dev_opt[CONF_ROOM_TEMP_SENSOR] = str(temp_raw).strip()
        avail_raw = user_input.get(CONF_AVAILABILITY_ENTITY_ID)
        if avail_raw and str(avail_raw).lower() not in ("none", "null", ""):
            dev_opt[CONF_AVAILABILITY_ENTITY_ID] = str(avail_raw).strip()
        if ir_fmt:
            dev_opt[CONF_IR_FORMAT] = ir_fmt

        clean_mac = (current_dev.get("mac_address") or "").replace(":", "").replace("-", "").upper()
        ctrl_val = str(user_input.get(CONF_CONTROLLER_ID, DEFAULT_CONTROLLER_ID)).strip() or DEFAULT_CONTROLLER_ID
        dev_opt[CONF_CONTROLLER_ID] = ctrl_val

        user_ip = str(user_input.get(CONF_DEVICE_IP, "")).strip() if user_input.get(CONF_DEVICE_IP) else ""
        final_ip = user_ip or default_ip
        if final_ip:
            dev_opt[CONF_DEVICE_IP] = final_ip

        entry_data = {
            "username": creds.get("username", ""),
            "password": creds.get("password", ""),
            "device_id": current_dev["id"],
            "device_name": dev_name,
            "model_code": dev_model,
            "is_ir_only": False,
            CONF_UDP_CONTROL_KEY: current_dev.get("udp_control_key"),
            CONF_UDP_CONTROL_IV: current_dev.get("udp_control_iv"),
            CONF_DEVICE_MAC: clean_mac,
            CONF_DEVICE_IP: final_ip or current_dev.get("ip_address"),
            CONF_CONTROLLER_ID: ctrl_val,
            CONF_PRIMARY_BACKEND: backend_val,
            CONF_SECONDARY_BACKEND: secondary_backend_val,
            CONF_SETUP_MODE: SETUP_MODE_HYBRID,
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
            return await self.async_step_hybrid_devices()

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
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=last_item["title"],
            data=last_item["data"],
            options=last_item["options"],
        )

    async def async_step_cloud_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Backward-compatible alias routing to cloud flow."""
        return await self.async_step_cloud(user_input)


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
            blaster_id = user_input.get(CONF_BLASTER_ENTITY_ID, "").strip() if user_input.get(CONF_BLASTER_ENTITY_ID) else ""
            mqtt_topic_raw = user_input.get(CONF_MQTT_TOPIC)
            mqtt_topic = "" if (mqtt_topic_raw is None or str(mqtt_topic_raw).lower() in ("none", "null", "")) else str(mqtt_topic_raw).strip()
            avail_topic_raw = user_input.get(CONF_AVAILABILITY_TOPIC)
            avail_topic = "" if (avail_topic_raw is None or str(avail_topic_raw).lower() in ("none", "null", "")) else str(avail_topic_raw).strip()
            receiver_raw = user_input.get(CONF_RECEIVER_ENTITY_ID)
            receiver_id = "" if (receiver_raw is None or str(receiver_raw).lower() in ("none", "null", "")) else str(receiver_raw).strip()
            temp_sensor_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
            temp_sensor_id = "" if (temp_sensor_raw is None or str(temp_sensor_raw).lower() in ("none", "null", "")) else str(temp_sensor_raw).strip()
            avail_raw = user_input.get(CONF_AVAILABILITY_ENTITY_ID)
            avail_id = "" if (avail_raw is None or str(avail_raw).lower() in ("none", "null", "")) else str(avail_raw).strip()
            ir_fmt = user_input.get(CONF_IR_FORMAT, "auto")

            if ir_fmt == "tasmota":
                if mqtt_topic and blaster_id:
                    errors["base"] = "both_endpoints_provided"
                elif not mqtt_topic and not blaster_id:
                    errors["base"] = "missing_tasmota_endpoint"
                elif mqtt_topic and not validate_mqtt_topic(mqtt_topic):
                    errors["base"] = "invalid_mqtt_topic"
                elif avail_topic and not validate_mqtt_topic(avail_topic):
                    errors["base"] = "invalid_availability_topic"
                else:
                    blaster_id = mqtt_topic if mqtt_topic else blaster_id
            else:
                if mqtt_topic:
                    errors["base"] = "topic_not_supported_for_format"
                elif not blaster_id:
                    errors[CONF_BLASTER_ENTITY_ID] = "blaster_required"
                elif avail_topic and not validate_mqtt_topic(avail_topic):
                    errors["base"] = "invalid_availability_topic"

            if not errors:
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
                if mqtt_topic:
                    options[CONF_MQTT_TOPIC] = mqtt_topic
                if avail_topic:
                    options[CONF_AVAILABILITY_TOPIC] = avail_topic
                title_suffix = "IR Only" if ctrl_mode == "ir" else "Hybrid"
                return self.async_create_entry(title=f"{name} ({title_suffix})", data=data, options=options)

        schema = vol.Schema(
            {
                vol.Optional(CONF_MQTT_TOPIC): selector.TextSelector(),
                vol.Optional(CONF_BLASTER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["infrared", "remote"])
                ),
                vol.Optional(CONF_RECEIVER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["infrared", "remote"])
                ),
                vol.Optional(CONF_ROOM_TEMP_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Optional(CONF_AVAILABILITY_TOPIC): selector.TextSelector(),
                vol.Optional(CONF_AVAILABILITY_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch", "device_tracker"])
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

    def _build_device_settings_schema(
        self,
        supports_udp: bool = True,
        has_cloud: bool = True,
        default_controller: str = DEFAULT_CONTROLLER_ID,
    ) -> vol.Schema:
        backend_options = []
        if supports_udp:
            backend_options.append(selector.SelectOptionDict(value="local_udp", label="Local UDP (LAN)"))
        if has_cloud:
            backend_options.append(selector.SelectOptionDict(value="cloud", label="Cloud MQTT"))
        backend_options.append(selector.SelectOptionDict(value="ir", label="Infrared Blaster"))

        secondary_options = [
            selector.SelectOptionDict(value="none", label="None (Single Transport Only)"),
        ]
        if supports_udp:
            secondary_options.append(selector.SelectOptionDict(value="local_udp", label="Local UDP (LAN)"))
        if has_cloud:
            secondary_options.append(selector.SelectOptionDict(value="cloud", label="Cloud MQTT"))
        secondary_options.append(selector.SelectOptionDict(value="ir", label="Infrared Blaster"))

        primary_default = "local_udp" if supports_udp else ("cloud" if has_cloud else "ir")

        fields: dict[Any, Any] = {
            vol.Optional(CONF_PRIMARY_BACKEND, default=primary_default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=backend_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_SECONDARY_BACKEND, default="none"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=secondary_options,
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
            vol.Optional(CONF_MQTT_TOPIC): selector.TextSelector(),
            vol.Optional(CONF_AVAILABILITY_TOPIC): selector.TextSelector(),
            vol.Optional(CONF_AVAILABILITY_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "input_boolean", "switch", "device_tracker"])
            ),
            vol.Optional(CONF_INSTALL_DATE): selector.DateSelector(),
        }
        if supports_udp:
            fields[vol.Optional(CONF_DEVICE_IP)] = selector.TextSelector()
            fields[vol.Optional(CONF_DEVICE_MAC)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
            fields[vol.Optional(CONF_UDP_CONTROL_KEY)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
            fields[vol.Optional(CONF_UDP_CONTROL_IV)] = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
            fields[vol.Optional(CONF_CONTROLLER_ID, default=default_controller)] = selector.TextSelector()

        return vol.Schema(fields)

    async def async_step_device_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure settings directly for this AC unit."""
        today = date.today()
        default_install = six_months_ago(today).isoformat()

        entry = self._get_config_entry()
        current_options = dict(getattr(entry, "options", {})) if entry else {}
        entry_data = get_entry_data(entry)
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
        current_mqtt = target_opt.get(CONF_MQTT_TOPIC, current_options.get(CONF_MQTT_TOPIC, entry_data.get(CONF_MQTT_TOPIC, "")))
        current_avail_topic = target_opt.get(CONF_AVAILABILITY_TOPIC, current_options.get(CONF_AVAILABILITY_TOPIC, entry_data.get(CONF_AVAILABILITY_TOPIC, "")))
        if not current_mqtt and is_mqtt_topic(current_blaster):
            current_mqtt = current_blaster
            current_blaster = ""
        current_receiver = target_opt.get(CONF_RECEIVER_ENTITY_ID, current_options.get(CONF_RECEIVER_ENTITY_ID, entry_data.get(CONF_RECEIVER_ENTITY_ID, "")))
        current_temp_sensor = target_opt.get(CONF_ROOM_TEMP_SENSOR, current_options.get(CONF_ROOM_TEMP_SENSOR, entry_data.get(CONF_ROOM_TEMP_SENSOR, "")))
        current_avail = target_opt.get(CONF_AVAILABILITY_ENTITY_ID, current_options.get(CONF_AVAILABILITY_ENTITY_ID, entry_data.get(CONF_AVAILABILITY_ENTITY_ID, "")))
        current_ir_fmt = target_opt.get(CONF_IR_FORMAT, current_options.get(CONF_IR_FORMAT, entry_data.get(CONF_IR_FORMAT, "auto")))
        current_backend = target_opt.get(CONF_PRIMARY_BACKEND, current_options.get(CONF_PRIMARY_BACKEND, "cloud"))
        current_secondary = target_opt.get(CONF_SECONDARY_BACKEND, current_options.get(CONF_SECONDARY_BACKEND, "none"))
        current_submode = target_opt.get(CONF_HYBRID_SUBMODE, current_options.get(CONF_HYBRID_SUBMODE, "auto"))
        current_ip = target_opt.get(CONF_DEVICE_IP, current_options.get(CONF_DEVICE_IP, entry_data.get(CONF_DEVICE_IP, "")))
        device_obj = getattr(coord, "device", None)
        dt = getattr(device_obj, "details", None)
        current_mac = (
            target_opt.get(CONF_DEVICE_MAC)
            or current_options.get(CONF_DEVICE_MAC)
            or entry_data.get(CONF_DEVICE_MAC, "")
            or getattr(dt, "mac_address", "")
        )
        current_ctrl = target_opt.get(CONF_CONTROLLER_ID, current_options.get(CONF_CONTROLLER_ID, entry_data.get(CONF_CONTROLLER_ID, DEFAULT_CONTROLLER_ID)))

        raw_model = (
            target_opt.get("model_code")
            or current_options.get("model_code")
            or coord_model
            or entry_data.get("model_code")
            or entry_data.get("model_number")
            or entry_data.get("model_name")
        )
        current_model = raw_model if (raw_model and raw_model != entry_data.get("device_id") and raw_model != dev_name) else "CS-CU Series"

        setup_mode = entry_data.get(CONF_SETUP_MODE, current_options.get(CONF_SETUP_MODE, ""))
        current_udp_key = (
            target_opt.get(CONF_UDP_CONTROL_KEY)
            or current_options.get(CONF_UDP_CONTROL_KEY)
            or entry_data.get(CONF_UDP_CONTROL_KEY)
            or getattr(dt, "udp_control_key", None)
        )
        current_udp_iv = (
            target_opt.get(CONF_UDP_CONTROL_IV)
            or current_options.get(CONF_UDP_CONTROL_IV)
            or entry_data.get(CONF_UDP_CONTROL_IV)
            or getattr(dt, "udp_control_iv", None)
        )
        is_ir_only = bool(entry_data.get("is_ir_only", False))
        supports_udp = not is_ir_only
        has_udp = supports_udp and bool(current_udp_key and current_udp_iv)
        has_cloud = bool(
            (entry_data.get("username") and not is_ir_only)
            or current_backend == BACKEND_CLOUD
            or current_secondary == BACKEND_CLOUD
        )

        if user_input is None:
            speed_test_str = ""
            if has_udp and current_ip:
                try:
                    bench = await async_benchmark_transports(
                        device_id=target_id,
                        ip_address=current_ip,
                        key=current_udp_key,
                        iv=current_udp_iv,
                        coordinator=coord,
                    )
                    parts = []
                    udp_res = bench.get("local_udp", {})
                    if udp_res.get("available") and udp_res.get("latency_ms") is not None:
                        parts.append(f"Local UDP **{udp_res['latency_ms']} ms**")
                    cloud_res = bench.get("cloud_mqtt", {})
                    if cloud_res.get("available") and cloud_res.get("latency_ms") is not None:
                        parts.append(f"Cloud MQTT **{cloud_res['latency_ms']} ms**")
                    if parts:
                        speed_test_str = f" | Speed Test: {' | '.join(parts)}"
                except Exception as err:
                    _LOGGER.debug("Speed test during options flow failed: %s", err)

            schema = self._build_device_settings_schema(supports_udp=supports_udp, has_cloud=has_cloud, default_controller=current_ctrl)
            suggested = {
                CONF_INSTALL_DATE: current_install,
                CONF_MQTT_TOPIC: current_mqtt,
                CONF_BLASTER_ENTITY_ID: current_blaster,
                CONF_RECEIVER_ENTITY_ID: current_receiver,
                CONF_ROOM_TEMP_SENSOR: current_temp_sensor,
                CONF_AVAILABILITY_TOPIC: current_avail_topic,
                CONF_AVAILABILITY_ENTITY_ID: current_avail,
                CONF_IR_FORMAT: current_ir_fmt,
                CONF_PRIMARY_BACKEND: current_backend,
                CONF_SECONDARY_BACKEND: current_secondary,
                CONF_HYBRID_SUBMODE: current_submode,
            }
            if supports_udp:
                if current_ip:
                    suggested[CONF_DEVICE_IP] = current_ip
                if current_mac:
                    suggested[CONF_DEVICE_MAC] = current_mac
                suggested[CONF_CONTROLLER_ID] = current_ctrl
                suggested[CONF_UDP_CONTROL_KEY] = current_udp_key or ""
                suggested[CONF_UDP_CONTROL_IV] = current_udp_iv or ""
            if hasattr(self, "add_suggested_values_to_schema"):
                schema = self.add_suggested_values_to_schema(schema, suggested)
            return self.async_show_form(
                step_id="device_settings",
                data_schema=schema,
                description_placeholders={
                    "device_name": dev_name,
                    "model_code": f"{current_model}{speed_test_str}",
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
            schema = self._build_device_settings_schema(supports_udp=supports_udp, has_cloud=has_cloud)
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
        mqtt_topic_raw = user_input.get(CONF_MQTT_TOPIC)
        mqtt_topic = "" if (mqtt_topic_raw is None or str(mqtt_topic_raw).lower() in ("none", "null", "")) else str(mqtt_topic_raw).strip()
        avail_topic_raw = user_input.get(CONF_AVAILABILITY_TOPIC)
        avail_topic = "" if (avail_topic_raw is None or str(avail_topic_raw).lower() in ("none", "null", "")) else str(avail_topic_raw).strip()
        receiver_raw = user_input.get(CONF_RECEIVER_ENTITY_ID)
        receiver_val = "" if (receiver_raw is None or str(receiver_raw).lower() in ("none", "null", "")) else str(receiver_raw).strip()
        temp_sensor_raw = user_input.get(CONF_ROOM_TEMP_SENSOR)
        temp_sensor_val = "" if (temp_sensor_raw is None or str(temp_sensor_raw).lower() in ("none", "null", "")) else str(temp_sensor_raw).strip()
        avail_raw = user_input.get(CONF_AVAILABILITY_ENTITY_ID)
        avail_val = "" if (avail_raw is None or str(avail_raw).lower() in ("none", "null", "")) else str(avail_raw).strip()
        ir_fmt_val = user_input.get(CONF_IR_FORMAT, "auto")
        backend_val = user_input.get(CONF_PRIMARY_BACKEND, "cloud")
        secondary_backend_val = user_input.get(CONF_SECONDARY_BACKEND, "none")

        if ir_fmt_val == "tasmota":
            if mqtt_topic and blaster_val:
                errors["base"] = "both_endpoints_provided"
            elif mqtt_topic and not validate_mqtt_topic(mqtt_topic):
                errors["base"] = "invalid_mqtt_topic"
            elif avail_topic and not validate_mqtt_topic(avail_topic):
                errors["base"] = "invalid_availability_topic"
            elif mqtt_topic:
                blaster_val = mqtt_topic
        else:
            if mqtt_topic:
                errors["base"] = "topic_not_supported_for_format"
            elif avail_topic and not validate_mqtt_topic(avail_topic):
                errors["base"] = "invalid_availability_topic"

        if (backend_val == "ir" or secondary_backend_val == "ir") and not blaster_val and not errors:
            errors[CONF_BLASTER_ENTITY_ID] = "blaster_required_for_ir"

        if has_udp and user_input.get(CONF_DEVICE_IP):
            ip_candidate = str(user_input.get(CONF_DEVICE_IP)).strip()
            try:
                ip_obj = ipaddress.ip_address(ip_candidate)
                if not isinstance(ip_obj, ipaddress.IPv4Address):
                    errors[CONF_DEVICE_IP] = "invalid_ip"
            except ValueError:
                errors[CONF_DEVICE_IP] = "invalid_ip"

        if has_udp and user_input.get(CONF_DEVICE_MAC):
            mac_candidate = str(user_input.get(CONF_DEVICE_MAC)).strip().replace(":", "").replace("-", "").upper()
            if len(mac_candidate) != 12 or not all(c in "0123456789ABCDEF" for c in mac_candidate):
                errors[CONF_DEVICE_MAC] = "invalid_mac"

        if has_udp and user_input.get(CONF_UDP_CONTROL_KEY):
            key_raw = str(user_input.get(CONF_UDP_CONTROL_KEY)).strip()
            try:
                key_bytes = bytes.fromhex(key_raw)
                if len(key_bytes) != 32:
                    errors[CONF_UDP_CONTROL_KEY] = "invalid_key"
            except (ValueError, TypeError):
                errors[CONF_UDP_CONTROL_KEY] = "invalid_key"

        if has_udp and user_input.get(CONF_UDP_CONTROL_IV):
            iv_raw = str(user_input.get(CONF_UDP_CONTROL_IV)).strip()
            try:
                iv_bytes = bytes.fromhex(iv_raw)
                if len(iv_bytes) != 16:
                    errors[CONF_UDP_CONTROL_IV] = "invalid_iv"
            except (ValueError, TypeError):
                errors[CONF_UDP_CONTROL_IV] = "invalid_iv"

        if errors:
            schema = self._build_device_settings_schema(supports_udp=supports_udp, has_cloud=has_cloud)
            suggested = {
                CONF_INSTALL_DATE: user_input.get(CONF_INSTALL_DATE, default_install),
                CONF_MQTT_TOPIC: mqtt_topic,
                CONF_BLASTER_ENTITY_ID: user_input.get(CONF_BLASTER_ENTITY_ID, ""),
                CONF_RECEIVER_ENTITY_ID: receiver_val,
                CONF_ROOM_TEMP_SENSOR: temp_sensor_val,
                CONF_AVAILABILITY_TOPIC: avail_topic,
                CONF_AVAILABILITY_ENTITY_ID: avail_val,
                CONF_IR_FORMAT: ir_fmt_val,
                CONF_PRIMARY_BACKEND: backend_val,
                CONF_SECONDARY_BACKEND: secondary_backend_val,
                CONF_HYBRID_SUBMODE: user_input.get(CONF_HYBRID_SUBMODE, current_submode),
                CONF_DEVICE_IP: user_input.get(CONF_DEVICE_IP, ""),
                CONF_DEVICE_MAC: user_input.get(CONF_DEVICE_MAC, ""),
                CONF_CONTROLLER_ID: user_input.get(CONF_CONTROLLER_ID, current_ctrl),
                CONF_UDP_CONTROL_KEY: user_input.get(CONF_UDP_CONTROL_KEY, ""),
                CONF_UDP_CONTROL_IV: user_input.get(CONF_UDP_CONTROL_IV, ""),
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
                errors=errors,
            )

        submode_val = user_input.get(CONF_HYBRID_SUBMODE) or current_submode or "auto"

        model_code_val = user_input.get("model_code", "").strip().upper()

        new_options[CONF_INSTALL_DATE] = install_date_str
        new_options[CONF_BLASTER_ENTITY_ID] = blaster_val
        if mqtt_topic:
            new_options[CONF_MQTT_TOPIC] = mqtt_topic
        else:
            new_options.pop(CONF_MQTT_TOPIC, None)
        if avail_topic:
            new_options[CONF_AVAILABILITY_TOPIC] = avail_topic
        else:
            new_options.pop(CONF_AVAILABILITY_TOPIC, None)
        new_options[CONF_RECEIVER_ENTITY_ID] = receiver_val
        new_options[CONF_ROOM_TEMP_SENSOR] = temp_sensor_val
        new_options[CONF_AVAILABILITY_ENTITY_ID] = avail_val
        new_options[CONF_IR_FORMAT] = ir_fmt_val
        if model_code_val:
            new_options["model_code"] = model_code_val
        new_options[CONF_PRIMARY_BACKEND] = backend_val
        new_options[CONF_SECONDARY_BACKEND] = secondary_backend_val
        if "tertiary_backend" in current_options:
            new_options["tertiary_backend"] = current_options["tertiary_backend"]
        new_options[CONF_HYBRID_SUBMODE] = submode_val

        is_multi_device = len(current_options.get("devices", {})) > 1
        if not is_multi_device:
            if supports_udp and user_input.get(CONF_DEVICE_IP):
                ip_val = str(user_input.get(CONF_DEVICE_IP)).strip()
                new_options[CONF_DEVICE_IP] = ip_val

            if supports_udp and user_input.get(CONF_DEVICE_MAC):
                clean_mac = str(user_input.get(CONF_DEVICE_MAC)).strip().replace(":", "").replace("-", "").upper()
                new_options[CONF_DEVICE_MAC] = clean_mac

            if supports_udp and user_input.get(CONF_CONTROLLER_ID):
                ctrl_val = str(user_input.get(CONF_CONTROLLER_ID)).strip() or DEFAULT_CONTROLLER_ID
                new_options[CONF_CONTROLLER_ID] = ctrl_val

            if supports_udp and user_input.get(CONF_UDP_CONTROL_KEY):
                new_options[CONF_UDP_CONTROL_KEY] = str(user_input.get(CONF_UDP_CONTROL_KEY)).strip()

            if supports_udp and user_input.get(CONF_UDP_CONTROL_IV):
                new_options[CONF_UDP_CONTROL_IV] = str(user_input.get(CONF_UDP_CONTROL_IV)).strip()

        if target_id:
            new_devices = dict(new_options.get("devices", {}))
            dev_entry = dict(new_devices.get(target_id, {}))
            dev_entry[CONF_INSTALL_DATE] = install_date_str
            dev_entry[CONF_BLASTER_ENTITY_ID] = blaster_val
            if mqtt_topic:
                dev_entry[CONF_MQTT_TOPIC] = mqtt_topic
            else:
                dev_entry.pop(CONF_MQTT_TOPIC, None)
            if avail_topic:
                dev_entry[CONF_AVAILABILITY_TOPIC] = avail_topic
            else:
                dev_entry.pop(CONF_AVAILABILITY_TOPIC, None)
            dev_entry[CONF_RECEIVER_ENTITY_ID] = receiver_val
            dev_entry[CONF_ROOM_TEMP_SENSOR] = temp_sensor_val
            dev_entry[CONF_AVAILABILITY_ENTITY_ID] = avail_val
            dev_entry[CONF_IR_FORMAT] = ir_fmt_val
            if model_code_val:
                dev_entry["model_code"] = model_code_val
            dev_entry[CONF_PRIMARY_BACKEND] = backend_val
            dev_entry[CONF_SECONDARY_BACKEND] = secondary_backend_val
            if "tertiary_backend" in target_opt:
                dev_entry["tertiary_backend"] = target_opt["tertiary_backend"]
            dev_entry[CONF_HYBRID_SUBMODE] = submode_val
            if supports_udp and user_input.get(CONF_DEVICE_IP):
                dev_entry[CONF_DEVICE_IP] = str(user_input.get(CONF_DEVICE_IP)).strip()
            if supports_udp and user_input.get(CONF_DEVICE_MAC):
                dev_entry[CONF_DEVICE_MAC] = str(user_input.get(CONF_DEVICE_MAC)).strip().replace(":", "").replace("-", "").upper()
            if supports_udp and user_input.get(CONF_CONTROLLER_ID):
                dev_entry[CONF_CONTROLLER_ID] = str(user_input.get(CONF_CONTROLLER_ID)).strip() or DEFAULT_CONTROLLER_ID
            if supports_udp and user_input.get(CONF_UDP_CONTROL_KEY):
                dev_entry[CONF_UDP_CONTROL_KEY] = str(user_input.get(CONF_UDP_CONTROL_KEY)).strip()
            if supports_udp and user_input.get(CONF_UDP_CONTROL_IV):
                dev_entry[CONF_UDP_CONTROL_IV] = str(user_input.get(CONF_UDP_CONTROL_IV)).strip()
            new_devices[target_id] = dev_entry
            new_options["devices"] = new_devices

        return self.async_create_entry(title="", data=new_options)

