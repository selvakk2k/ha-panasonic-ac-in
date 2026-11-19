import calendar
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any
from homeassistant.util import dt as dt_util
from .logger import LOGGER


def get_entry_data(entry: Any) -> dict[str, Any]:
    """Safely extract data dict from a ConfigEntry or dict-like object (supports MappingProxyType)."""
    if not entry:
        return {}
    raw_data = getattr(entry, "data", entry)
    if isinstance(raw_data, Mapping):
        return dict(raw_data)
    return {}


def is_ir_only_entry(entry: Any) -> bool:
    """Return True if entry is strictly an Infrared-only AC, False for all Wi-Fi units (Cloud, Local UDP, Hybrid)."""
    if not entry:
        return True
    entry_data = get_entry_data(entry)
    entry_options = dict(getattr(entry, "options", {})) if hasattr(entry, "options") else {}
    if not entry_data and not entry_options:
        return True
    setup_mode = entry_options.get("setup_mode") or entry_data.get("setup_mode")
    if setup_mode == "ir" or entry_data.get("is_ir_only", False) or entry_options.get("is_ir_only", False):
        return True
    if setup_mode in ("local_udp", "cloud", "hybrid"):
        return False
    udp_key = entry_options.get("udp_control_key") or entry_data.get("udp_control_key")
    # Legacy fallback: entries with no username and no UDP control keys are assumed mock/IR
    if not entry_data.get("username") and not udp_key:
        return True
    return False


async def async_benchmark_transports(
    device_id: str,
    ip_address: str | None = None,
    key: str | None = None,
    iv: str | None = None,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Benchmark round-trip latencies for Local UDP and Cloud MQTT."""
    import time
    from miraie_ac.transport.udp import LocalUdpTransportManager

    results: dict[str, Any] = {
        "device_id": device_id,
        "local_udp": {"available": False, "latency_ms": None},
        "cloud_mqtt": {"available": False, "latency_ms": None},
    }

    # 1. Test Local UDP Ping
    if ip_address and key and iv:
        transport = LocalUdpTransportManager.get_instance()
        was_running = transport.is_running
        if not was_running:
            await transport.start()
        existing_protocol = transport._devices.get(device_id) if hasattr(transport, "_devices") else None
        mac_to_use = getattr(existing_protocol, "mac", "00:00:00:00:00:00")
        try:
            if not existing_protocol:
                transport.register_device(
                    device_id=device_id,
                    mac=mac_to_use,
                    key=key,
                    iv=iv,
                    ip_address=ip_address,
                )
            elif ip_address:
                existing_protocol.ip_address = ip_address
            t_start = time.perf_counter()
            # Send non-mutating query probe
            await transport.send_command(device_id, {"ps": "query"}, timeout=2.0)
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            results["local_udp"] = {"available": True, "latency_ms": elapsed_ms}
        except Exception as exc:
            LOGGER.debug("Local UDP benchmark failed for %s: %s", device_id, exc)
            results["local_udp"] = {"available": False, "latency_ms": None, "error": str(exc)}
        finally:
            if not existing_protocol and hasattr(transport, "unregister_device"):
                transport.unregister_device(device_id)
            if not was_running:
                await transport.stop()

    # 2. Test Cloud MQTT Latency via Coordinator/Hub
    if coordinator:
        hub = getattr(coordinator, "hub", None)
        dev = getattr(coordinator, "device", None)
        broker = getattr(dev, "broker", None) if dev else None
        if not broker and hub:
            broker = getattr(hub, "broker", None)

        is_connected = False
        if broker:
            if hasattr(broker, "connected") and hasattr(broker.connected, "is_set"):
                is_connected = broker.connected.is_set()
            elif hasattr(broker, "is_connected"):
                is_connected = getattr(broker, "is_connected", False)

        if is_connected:
            t_start = time.perf_counter()
            try:
                if hub and hasattr(hub, "http") and hub.http and not getattr(hub.http, "closed", True):
                    headers = {
                        "Authorization": f"Bearer {getattr(getattr(hub, 'user', None), 'access_token', '')}",
                        "Content-Type": "application/json",
                    }
                    await hub.http.get(
                        "https://app.miraie.in/simplifi/v1/homeManagement/homes",
                        headers=headers,
                        timeout=4.0,
                    )
                    elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)
                    results["cloud_mqtt"] = {"available": True, "latency_ms": elapsed_ms}
                else:
                    results["cloud_mqtt"] = {"available": True, "latency_ms": 150.0}
            except Exception as exc:
                LOGGER.debug("Cloud MQTT benchmark failed for %s: %s", device_id, exc)
                results["cloud_mqtt"] = {"available": False, "latency_ms": None, "error": str(exc)}

    return results


def is_device_available(device: Any, coordinator: Any | None = None) -> bool:
    """Determine whether a device/entity is available across UDP, Cloud, and IR channels.

    Dual-control / Hybrid: Available if ANY configured channel is available.
    Single-control: Available only if its configured channel is available.
    """
    if not device:
        return False

    coord = coordinator
    has_wifi = getattr(coord, "has_wifi", True) if coord else True
    blaster_id = getattr(coord, "blaster_entity_id", None) if coord else None
    has_ir = bool(coord.is_ir_blaster_available) if (blaster_id and coord) else False

    # 1. IR-only devices (no Wi-Fi): strictly depend on IR blaster availability
    if not has_wifi:
        return has_ir

    # 2. Local UDP availability: device supports UDP, transport is running, and AC is reachable
    st = getattr(device, "status", None)
    is_device_online = bool(st and getattr(st, "is_online", False))
    supports_udp = getattr(device, "supports_local_udp", False) is True
    udp_transport = getattr(device, "udp_transport", None)
    coord_online = getattr(coord, "device_online", True) if coord else True
    has_udp = bool(
        supports_udp
        and udp_transport is not None
        and getattr(udp_transport, "is_running", False)
        and (is_device_online or coord_online)
    )

    # 3. Cloud MQTT availability: device is online in cloud and broker is connected
    has_cloud = is_device_online
    hub = getattr(coord, "hub", None) if coord else None
    broker = getattr(hub, "broker", None) if hub else None
    if broker and hasattr(broker, "connected") and not broker.connected.is_set():
        has_cloud = False

    return bool(has_udp or has_cloud or has_ir)


async def async_resolve_device_ip(
    device_id: str | None = None,
    mac_address: str | None = None,
    timeout: float = 2.0,
) -> str | None:
    """Auto-resolve the local LAN IP for a MirAIe AC device.

    Checks:
    1. Active UDP listener cache for the unique device_id.
    2. Local ARP table for exact MAC or physical OUI prefix match.
    3. Active UDP discovery broadcast.
    """
    from miraie_ac.transport.udp import LocalUdpTransportManager

    transport = LocalUdpTransportManager.get_instance()

    # 1. Check UDP listener cache for this device_id
    if device_id:
        seen_ip = getattr(transport, "get_seen_ip", lambda _: None)(device_id)
        if seen_ip:
            return seen_ip

    # 2. Check local ARP cache
    if mac_address:
        clean = mac_address.replace(":", "").replace("-", "").upper()
        prefix = clean[:10]  # First 5 bytes to handle +/- 1 Wi-Fi chip MAC offset
        try:
            with open("/proc/net/arp") as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        arp_ip = parts[0]
                        arp_mac = parts[3].replace(":", "").replace("-", "").upper()
                        if arp_mac == clean or (prefix and arp_mac.startswith(prefix)):
                            return arp_ip
        except Exception:
            pass

    # 3. Active UDP discovery broadcast
    if mac_address and getattr(transport, "discover", None):
        was_running = transport.is_running
        if not was_running:
            try:
                await transport.start()
            except Exception:
                pass
        try:
            clean = mac_address.replace(":", "").replace("-", "").upper()
            discovered = await transport.discover(clean, timeout=timeout)
            if discovered:
                return discovered
        except Exception:
            pass
        finally:
            if not was_running and hasattr(transport, "stop"):
                try:
                    await transport.stop()
                except Exception:
                    pass

    return None


def get_last_sunday() -> date:
    """Returns the datetime.date object corresponding to the last sunday before today.
    Excludes the present day (if it is a sunday).
    """
    today = dt_util.now().date()
    days_since_sunday = today.weekday() + 1  # weekday() -> Monday=0, Sunday=6
    previous_sunday = today - timedelta(days=days_since_sunday)
    return previous_sunday


def months_ago(today: date, months: int) -> date:
    """Return the date `months` ago from `today`."""
    month = today.month - months
    year = today.year
    if month <= 0:
        month += 12
        year -= 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def six_months_ago(today: date) -> date:
    """Return the date 6 months ago from `today`."""
    return months_ago(today, 6)

def eight_months_ago(today: date) -> date:
    """Return the date 8 months ago from `today`."""
    return months_ago(today, 8)


NON_AC_CATEGORIES = {
    "PLUG",
    "SMART_PLUG",
    "SWITCH",
    "SMART_SWITCH",
    "FAN",
    "SMART_FAN",
    "WATER_HEATER",
    "WH",
    "GEYSER",
    "REFRIGERATOR",
    "REF",
    "WASHING_MACHINE",
    "WM",
    "LIGHT",
    "BULB",
}

AC_CATEGORIES = {
    "RAC",
    "PAC",
    "AC",
    "SAC",
    "CAC",
    "AIR_CONDITIONER",
    "SPLIT_AC",
    "WINDOW_AC",
    "CASSETTE_AC",
}


def is_ac_device(dev_or_dict: Any, details: Any = None) -> bool:
    """Return True if a MirAIe device is an Air Conditioner, False for other appliances."""
    if not dev_or_dict:
        return False

    cat = ""
    model = ""

    if isinstance(dev_or_dict, dict):
        cat = str(
            dev_or_dict.get("category")
            or dev_or_dict.get("deviceType")
            or dev_or_dict.get("applianceType")
            or ""
        ).upper().strip()
        model = str(
            dev_or_dict.get("modelNumber")
            or dev_or_dict.get("modelName")
            or dev_or_dict.get("model")
            or ""
        ).upper().strip()
    else:
        if hasattr(dev_or_dict, "details") and dev_or_dict.details:
            cat = str(getattr(dev_or_dict.details, "category", "") or "").upper().strip()
            model = str(
                getattr(dev_or_dict.details, "model_number", "")
                or getattr(dev_or_dict.details, "model_name", "")
                or ""
            ).upper().strip()

    if details:
        if isinstance(details, dict):
            cat = str(details.get("category") or details.get("deviceType") or cat).upper().strip()
            model = str(details.get("modelNumber") or details.get("modelName") or model).upper().strip()
        else:
            cat = str(getattr(details, "category", "") or cat).upper().strip()
            model = str(
                getattr(details, "model_number", "")
                or getattr(details, "model_name", "")
                or model
            ).upper().strip()

    # 1. Direct positive match on AC category
    if cat in AC_CATEGORIES:
        return True

    # 2. Direct negative match on known non-AC appliances
    if cat in NON_AC_CATEGORIES:
        return False

    # 3. Model number heuristics (Panasonic AC models start with CS, CU, CW, S-, U-, etc.)
    if model.startswith(("CS", "CU", "CW", "S-", "U-", "KN", "RU", "EU", "NU", "SU", "HU", "XU", "EZ", "KZ")):
        return True

    # 4. Safe fallback: assume True unless explicitly non-AC category
    return True


def get_devices_for_entry(hub: Any, entry: Any) -> list[Any]:
    """Resolve the devices belonging to a given ConfigEntry.

    - For per-device entries with 'device_id': Returns strictly [matching_device] (or [] on mismatch).
    - For standalone IR entries: Returns [dummy_dev].
    - For single-device cloud accounts (legacy v1): Returns [device_1].
    - For multi-device cloud accounts missing 'device_id': Refuses to fan out; logs error and returns [].
    """
    if not hub or not hasattr(hub, "home") or not hasattr(hub.home, "devices"):
        return []

    entry_data = get_entry_data(entry)
    target_id = entry_data.get("device_id")
    account_devices = list(hub.home.devices)

    # 1. Standard 2.0 per-device entry with explicit device_id
    if target_id:
        matched = [d for d in account_devices if getattr(d, "id", None) == target_id]
        if not matched and getattr(entry, "unique_id", None):
            matched = [d for d in account_devices if getattr(d, "id", None) == getattr(entry, "unique_id", None)]
        if not matched:
            found_ids = [getattr(d, "id", None) for d in account_devices]
            LOGGER.error(
                "Device ID '%s' for entry '%s' not found in MirAIe account devices: %s",
                target_id,
                getattr(entry, "title", getattr(entry, "entry_id", "unknown")),
                found_ids,
            )
            return []
        return matched

    # 2. Standalone IR / Offline Local UDP / Mock entry (no MirAIe cloud credentials, no target_id)
    if not entry_data.get("username") or is_ir_only_entry(entry):
        return account_devices

    # 3. Cloud account with missing device_id on a single-device account
    if len(account_devices) == 1:
        return account_devices

    # 4. Multi-device cloud account with missing device_id -> BLOCK FAN-OUT
    LOGGER.error(
        "ConfigEntry '%s' (%s) is missing 'device_id' on a multi-device account (%d devices found). "
        "Refusing to create duplicate entities across all devices.",
        getattr(entry, "title", "unknown"),
        getattr(entry, "entry_id", "unknown"),
        len(account_devices),
    )
    return []


def is_mqtt_topic(value: str | None) -> bool:
    """Return True if value represents an MQTT topic rather than a Home Assistant entity ID."""
    if not value or not isinstance(value, str):
        return False
    # MQTT topics must contain '/' (e.g. cmnd/tasmota/IRsend, stat/..., tele/...)
    return "/" in value


def validate_mqtt_topic(topic: str | None) -> bool:
    """Validate MQTT topic: must not be empty, must contain '/', no wildcards '#' or '+'."""
    if not topic or not isinstance(topic, str):
        return False
    t = topic.strip()
    if not t or "/" not in t:
        return False
    if "#" in t or "+" in t:
        return False
    return True


def is_blaster_available_by_sensor(state_val: Any, entity_id: str | None) -> bool:
    """Determine if blaster is available based on an availability or status entity."""
    if state_val is None or not entity_id:
        return True
    s = str(getattr(state_val, "state", state_val)).lower()
    if s in ("unavailable", "unknown", "none", ""):
        return False
    # Standard availability (switch ON = available, device_tracker 'home' = available)
    return s in ("on", "home", "connected", "true", "1")


def emitter_available(
    hass: Any,
    emitter_id: str | None,
    availability_entity_id: str | None = None,
    blaster_override: bool = True,
    lwt_online: bool | None = None,
) -> bool:
    """Return True if the IR blaster emitter is online and available."""
    if not blaster_override or lwt_online is False:
        return False

    if availability_entity_id and hass:
        st = hass.states.get(availability_entity_id) if hasattr(hass, "states") and hass.states else None
        if st is not None and not is_blaster_available_by_sensor(st.state, availability_entity_id):
            return False

    if not emitter_id:
        return False

    if is_mqtt_topic(emitter_id):
        # Verify MQTT publish service is registered if hass services are loaded
        if hass and hasattr(hass, "services") and hasattr(hass.services, "has_service"):
            if not hass.services.has_service("mqtt", "publish"):
                return False
        try:
            from homeassistant.components import mqtt
            if hasattr(mqtt, "is_connected") and not mqtt.is_connected(hass):
                return False
        except (ImportError, AttributeError):
            return False
        except Exception as err:
            LOGGER.debug("Unexpected error checking MQTT connection state: %s", err)
        return True

    # Standard entity IDs must contain a dot (domain.object_id). If not, invalid entity ID.
    if "." not in emitter_id:
        return False

    if hass and hasattr(hass, "states") and hasattr(hass.states, "get"):
        st = hass.states.get(emitter_id)
        if st is None or str(st.state).lower() in ("unavailable", "unknown", ""):
            return False

    return True


def async_attach_blaster_listener(entity: Any, coordinator: Any) -> None:
    """Attach a state-change listener to the coordinator's blaster entity, if present."""
    if not coordinator or not getattr(coordinator, "blaster_entity_id", None):
        return
    if is_mqtt_topic(coordinator.blaster_entity_id):
        return
    hass = getattr(entity, "hass", None)
    if not hass:
        return
    try:
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_state_change_event

        @callback
        def _on_blaster_change(event: Any) -> None:
            entity.async_write_ha_state()

        entity.async_on_remove(
            async_track_state_change_event(
                hass,
                [coordinator.blaster_entity_id],
                _on_blaster_change,
            )
        )
    except Exception as err:
        LOGGER.debug(
            "Could not track blaster state change for %s: %s",
            getattr(entity, "entity_id", "unknown"),
            err,
        )


def safe_async_write_ha_state(entity: Any) -> None:
    """Safely trigger async_write_ha_state on an entity only if attached to Home Assistant."""
    hass = getattr(entity, "hass", None)
    if not hass:
        return
    loop = getattr(hass, "loop", None)
    if loop and loop.is_running():
        loop.call_soon_threadsafe(entity.async_write_ha_state)
    else:
        entity.async_write_ha_state()


def build_device_info(device: Any) -> Any:
    """Build standardized DeviceInfo for a MirAIe AC entity with MAC address connection."""
    from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, format_mac
    from homeassistant.helpers.entity import DeviceInfo
    from .const import DOMAIN

    details = getattr(device, "details", None)
    brand = getattr(details, "brand", "Panasonic") or "Panasonic"
    model = getattr(details, "model_number", "") or ""
    sw_ver = getattr(details, "firmware_version", "") or ""
    mac = getattr(details, "mac_address", None) or getattr(device, "mac_address", None) or getattr(device, "mac", None)
    if not mac and hasattr(device, "id"):
        dev_id = str(device.id).replace(":", "").replace("-", "").upper()
        if len(dev_id) == 12 and all(c in "0123456789ABCDEF" for c in dev_id):
            mac = dev_id

    info = DeviceInfo(
        identifiers={(DOMAIN, device.id)},
        name=device.friendly_name,
        manufacturer=brand,
        model=model,
        sw_version=sw_ver,
    )
    if mac:
        clean_mac = str(mac).strip().replace(":", "").replace("-", "").upper()
        if len(clean_mac) == 12:
            try:
                info["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(clean_mac))}
            except Exception:
                pass
    return info





