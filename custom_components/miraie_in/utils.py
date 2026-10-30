import calendar
from datetime import date, timedelta
from typing import Any
from homeassistant.util import dt as dt_util
from .logger import LOGGER


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

    raw_data = getattr(entry, "data", entry)
    entry_data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    target_id = entry_data.get("device_id")
    account_devices = list(hub.home.devices)

    # 1. Standard 2.0 per-device entry with explicit device_id
    if target_id:
        matched = [d for d in account_devices if getattr(d, "id", None) == target_id]
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

    # 2. Standalone IR / Mock entry (no MirAIe cloud credentials)
    if entry_data.get("is_ir_only") or "username" not in entry_data:
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
    # MQTT topics usually contain '/' (e.g. cmnd/tasmota/IRsend) or start with cmnd/, stat/, tele/
    if "/" in value or value.startswith("cmnd/") or value.startswith("stat/") or value.startswith("tele/"):
        return True
    # If it has no dot, it cannot be a Home Assistant entity_id (domain.object_id)
    if "." not in value:
        return True
    return False


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
) -> bool:
    """Return True if the IR blaster emitter is online and available."""
    if not blaster_override:
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

    if "." not in emitter_id:
        return True

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




