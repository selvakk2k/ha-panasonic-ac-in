"""The mirAIe integration."""
from __future__ import annotations
from typing import Any

from .logger import LOGGER

from miraie_ac import MirAIeBroker, MirAIeHub

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from datetime import date
import aiohttp
import asyncio

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
    SWING_V_MAP,
    SWING_H_MAP,
    V0,
    H0,
)
from .coordinator import MirAIeDeviceCoordinator
from .sensor import async_backfill_energy_statistics
from .utils import six_months_ago, get_devices_for_entry





PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
]

# Mapping of entity-domain prefixes that appear in old-format unique_ids.
_OLD_UID_PREFIXES = ("sensor.", "switch.", "button.", "binary_sensor.")


def _migrate_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, hub: MirAIeHub
) -> None:
    """Migrate entity unique_ids from the old format to the new format.

    Old format: ``<domain>.<device_name>_<device_id>_<suffix>``
    New format: ``<device_id>_<suffix>``

    The migration is idempotent — entities already in the new format are
    skipped.
    """
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)

    # Build a set of known device IDs for fast lookup
    device_ids = {device.id for device in hub.home.devices}

    migrated = 0
    for entity_entry in entities:
        old_uid = entity_entry.unique_id

        # Only process unique_ids that start with a domain prefix (old format)
        if not any(old_uid.startswith(prefix) for prefix in _OLD_UID_PREFIXES):
            continue

        # Strip the domain prefix (e.g. "sensor.")
        stripped = old_uid.split(".", 1)[1] if "." in old_uid else old_uid

        # Find which device_id is embedded in this unique_id
        for device_id in device_ids:
            idx = stripped.find(f"_{device_id}_")
            if idx == -1:
                # Also check if the stripped string starts with device_id
                if stripped.startswith(f"{device_id}_"):
                    # No name prefix, just device_id_suffix
                    new_uid = stripped
                    break
                continue

            # Everything after the device_id is the suffix (including leading _)
            suffix_start = idx + 1 + len(device_id)
            suffix = stripped[suffix_start:]
            new_uid = f"{device_id}{suffix}"
            break
        else:
            LOGGER.warning(
                "Could not determine device_id for entity %s (unique_id=%s), skipping migration",
                entity_entry.entity_id,
                old_uid,
            )
            continue

        if new_uid == old_uid:
            continue

        LOGGER.info(
            "Migrating unique_id for %s: %s → %s",
            entity_entry.entity_id,
            old_uid,
            new_uid,
        )
        registry.async_update_entity(entity_entry.entity_id, new_unique_id=new_uid)
        migrated += 1

    # Clean up orphaned 1.x climate entities without a config entry
    for dev_id in device_ids:
        for reg_entry in list(registry.entities.values()):
            if reg_entry.platform == DOMAIN and reg_entry.domain == "climate":
                if reg_entry.config_entry_id is None and dev_id in (reg_entry.unique_id or ""):
                    LOGGER.info("Removing orphaned climate entity %s", reg_entry.entity_id)
                    registry.async_remove(reg_entry.entity_id)
    if migrated:
        LOGGER.info("Migrated %d entity unique_id(s) to new format", migrated)


def _cleanup_cross_device_entities(hass: HomeAssistant, entry: ConfigEntry, target_dev_id: str | None) -> None:
    """Clean up any entities and device entries registered under this entry_id that belong to a different device_id."""
    if not target_dev_id:
        return

    # 1. Clean up Entity Registry (removes foreign entities from this entry)
    ent_reg = er.async_get(hass)
    entries_to_remove = []
    clean_target_id = target_dev_id.lower().replace("-", "_")

    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        unq_id = entity_entry.unique_id
        if not unq_id:
            continue
        clean_unq = unq_id.lower().replace("-", "_")
        if target_dev_id not in unq_id and clean_target_id not in clean_unq:
            entries_to_remove.append(entity_entry.entity_id)

    for ent_id in entries_to_remove:
        ent_reg.async_remove(ent_id)
        LOGGER.info("Pruned duplicate cross-device entity %s from entry %s", ent_id, entry.entry_id)

    # 2. Clean up Device Registry (removes foreign device associations from this entry)
    try:
        dev_reg = dr.async_get(hass)
        for dev_entry in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            matching_identifiers = [
                ident[1] for ident in dev_entry.identifiers
                if ident[0] == DOMAIN
            ]
            if matching_identifiers and target_dev_id not in matching_identifiers:
                dev_reg.async_update_device(dev_entry.id, remove_config_entry_id=entry.entry_id)
                LOGGER.info("Pruned foreign device %s (%s) from entry %s", dev_entry.id, matching_identifiers, entry.entry_id)
    except Exception as exc:
        LOGGER.debug("Could not prune device registry entries for %s: %s", entry.entry_id, exc)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up mirAIe from a config entry."""

    loaded_components = getattr(getattr(hass, "config", None), "components", set())
    configured_domains = set(hass.config_entries.async_domains()) if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "async_domains") else set()

    if "miraie" in loaded_components or "miraie" in configured_domains:
        LOGGER.warning(
            "Conflicting integration 'miraie' detected! Running both 'miraie' and 'miraie_in' "
            "simultaneously causes MQTT connection drops and database corruption. Please remove one of the integrations."
        )
        async_create_issue(
            hass,
            DOMAIN,
            "conflicting_miraie_integration",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=IssueSeverity.WARNING,
            translation_key="conflicting_miraie_integration",
        )

    session = async_get_clientsession(hass)
    has_username = "username" in entry.data
    is_ir_only = entry.data.get("is_ir_only", False) or not has_username

    async def _dummy_async_coro(*args: Any, **kwargs: Any) -> None:
        return None

    if is_ir_only or not has_username:
        hub = MirAIeHub()
        hub.home = type("Home", (), {"devices": []})()
        dev_id = entry.unique_id or f"manual_{entry.entry_id}"
        model_code = entry.data.get("model_code", "CS-CU-RU18CKY-1")
        name = entry.data.get("name", entry.title)

        dummy_dev = type("Device", (), {
            "id": dev_id,
            "friendly_name": name,
            "details": type("Details", (), {"model_number": model_code, "brand": "Panasonic", "firmware_version": "IR-1.0", "has_wifi": not entry.data.get("is_ir_only", False)})(),
            "status": type("Status", (), {
                "is_online": True,
                "power_mode": type("Pwr", (), {"value": "off"})(),
                "hvac_mode": type("Md", (), {"value": "cool"})(),
                "temperature": 24,
                "room_temperature": None,
                "fan_mode": type("Fn", (), {"value": "auto"})(),
                "v_swing_mode": type("Vs", (), {"value": 0})(),
                "h_swing_mode": type("Hs", (), {"value": 0})(),
                "converti_mode": type("Cv", (), {"value": 0})(),
                "preset_mode": type("Pr", (), {"value": "none"})(),
            })(),
            "register_callback": lambda *args, **kwargs: None,
            "remove_callback": lambda *args, **kwargs: None,
            "turn_on": _dummy_async_coro,
            "turn_off": _dummy_async_coro,
            "set_temperature": _dummy_async_coro,
            "set_hvac_mode": _dummy_async_coro,
            "set_fan_mode": _dummy_async_coro,
            "set_v_swing_mode": _dummy_async_coro,
            "set_h_swing_mode": _dummy_async_coro,
            "set_preset_mode": _dummy_async_coro,
            "set_converti_mode": _dummy_async_coro,
            "set_display_mode": _dummy_async_coro,
            "set_nanoe": _dummy_async_coro,
        })()
        hub.home.devices = [dummy_dev]
    else:
        sessions = hass.data.setdefault(DOMAIN, {}).setdefault("sessions", {})
        username_key = entry.data["username"].lower()


        if username_key in sessions:
            account_session = sessions[username_key]
            hub = account_session["hub"]
            broker = account_session["broker"]
            account_session["entries"].add(entry.entry_id)

            # Ensure broker connection task is alive if a previous reload cancelled background tasks
            bg_tasks = getattr(hub, "background_tasks", set())
            has_running_broker = any(
                not task.done() and not task.cancelled()
                for task in bg_tasks
            )
            if not has_running_broker:
                LOGGER.info("Restarting broker connection task for shared session %s", username_key)
                try:
                    topics = hub.get_device_topics()
                    broker.set_topics(topics)
                    loop = asyncio.get_running_loop()
                    b_task = loop.create_task(
                        broker.connect(hub.home.id, hub.user.access_token, hub.get_token)
                    )
                    hub.background_tasks.add(b_task)
                    b_task.add_done_callback(hub.background_tasks.remove)
                    conn = getattr(broker, "connected", None)
                    if conn is not None and hasattr(conn, "wait"):
                        try:
                            await asyncio.wait_for(conn.wait(), timeout=10.0)
                        except (asyncio.TimeoutError, TimeoutError):
                            LOGGER.warning("Broker reconnect wait timed out for %s; continuing async", username_key)
                except Exception as err:
                    LOGGER.warning("Could not restart broker task for %s: %s", username_key, err)
        else:
            try:
                hub = MirAIeHub(session)
            except TypeError:
                hub = MirAIeHub()
            broker = MirAIeBroker()
            try:
                await hub.init(entry.data["username"], entry.data["password"], broker)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise ConfigEntryNotReady from err
            except Exception as err:
                raise ConfigEntryAuthFailed from err

            sessions[username_key] = {
                "hub": hub,
                "broker": broker,
                "entries": {entry.entry_id},
            }


        # Auto-split legacy v1.x single-parent account entries into per-device config entries
        if "device_id" not in entry.data:
            LOGGER.info("Legacy single-account entry detected (%s). Auto-migrating to per-device entries...", entry.entry_id)
            devices = getattr(getattr(hub, "home", None), "devices", [])
            if devices:
                existing_entries = hass.config_entries.async_entries(DOMAIN)
                existing_dev_ids = {e.data.get("device_id") for e in existing_entries if e.data.get("device_id")}

                migrated_count = 0
                failed_devices = []
                for device in devices:
                    if device.id in existing_dev_ids:
                        migrated_count += 1
                        continue
                    model_code = getattr(getattr(device, "details", None), "model_number", "") or ""

                    # Extract all options from legacy entry to preserve install_date and any device-specific settings
                    legacy_devices_opt = entry.options.get("devices", {})
                    legacy_dev_opts = legacy_devices_opt.get(device.id, {})
                    new_options = dict(legacy_dev_opts)

                    legacy_install_date = legacy_dev_opts.get("install_date") or entry.options.get("install_date")
                    if legacy_install_date:
                        new_options["install_date"] = legacy_install_date

                    blaster_id = legacy_dev_opts.get(CONF_BLASTER_ENTITY_ID) or entry.options.get(CONF_BLASTER_ENTITY_ID)
                    if blaster_id:
                        new_options[CONF_BLASTER_ENTITY_ID] = blaster_id

                    primary = legacy_dev_opts.get(CONF_PRIMARY_BACKEND) or entry.options.get(CONF_PRIMARY_BACKEND)
                    if primary:
                        new_options[CONF_PRIMARY_BACKEND] = primary

                    submode = legacy_dev_opts.get(CONF_HYBRID_SUBMODE) or entry.options.get(CONF_HYBRID_SUBMODE)
                    if submode:
                        new_options[CONF_HYBRID_SUBMODE] = submode

                    ir_fmt = legacy_dev_opts.get("working_ir_format") or entry.options.get("working_ir_format")
                    if ir_fmt:
                        new_options["working_ir_format"] = ir_fmt

                    try:
                        result = await hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={"source": config_entries.SOURCE_IMPORT},
                            data={
                                "username": entry.data["username"],
                                "password": entry.data["password"],
                                "device_id": device.id,
                                "name": device.friendly_name,
                                "model_code": model_code,
                                "is_ir_only": False,
                                "options": new_options,
                            },
                        )
                        if isinstance(result, dict) and result.get("type") in ("create_entry", "abort"):
                            migrated_count += 1
                        else:
                            LOGGER.warning("Auto-migration: Unexpected flow result for device %s: %s", device.id, result)
                            failed_devices.append(device.id)
                    except Exception as exc:  # pylint: disable=broad-except
                        LOGGER.exception("Auto-migration failed for device %s: %s", device.id, exc)
                        failed_devices.append(device.id)

                if not failed_devices and migrated_count >= len(devices):
                    LOGGER.info(
                        "Successfully auto-migrated all %d device(s) into individual config entries. Removing legacy parent entry %s",
                        migrated_count,
                        entry.entry_id,
                    )
                    _notify_migration_successful(hass)
                    hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
                    return True

                LOGGER.error(
                    "Auto-migration partially or completely failed (failed devices: %s). Preserving legacy parent entry %s to prevent data loss.",
                    failed_devices,
                    entry.entry_id,
                )
                async_create_issue(
                    hass,
                    DOMAIN,
                    f"manual_migration_required_{entry.entry_id}",
                    is_fixable=False,
                    issue_domain=DOMAIN,
                    severity=IssueSeverity.WARNING,
                    translation_key="manual_migration_required",
                )
                return False
            else:
                entry_title = getattr(entry, "title", "MirAIe Cloud Account")
                LOGGER.warning("Auto-migration: No devices discovered for legacy entry %s", entry_title)
                async_create_issue(
                    hass,
                    DOMAIN,
                    f"manual_migration_required_{entry.entry_id}",
                    is_fixable=False,
                    issue_domain=DOMAIN,
                    severity=IssueSeverity.WARNING,
                    translation_key="manual_migration_required",
                )
                return False

    entry.runtime_data = hub

    # Process manual devices added via options flow
    manual_devices_opt = entry.options.get("manual_devices", [])
    for m_dev in manual_devices_opt:
        m_id = m_dev["id"]
        if not any(d.id == m_id for d in hub.home.devices):
            m_dummy = type("Device", (), {
                "id": m_id,
                "friendly_name": m_dev.get("name", "Manual AC"),
                "details": type("Details", (), {"model_number": m_dev.get("model_code", "CS-CU-KN18YKY"), "brand": "Panasonic", "firmware_version": "IR-1.0", "has_wifi": False})(),
                "status": type("Status", (), {
                    "is_online": True,
                    "power_mode": type("Pwr", (), {"value": "off"})(),
                    "hvac_mode": type("Md", (), {"value": "cool"})(),
                    "temperature": 24,
                    "room_temperature": 25.0,
                    "fan_mode": type("Fn", (), {"value": "auto"})(),
                    "v_swing_mode": type("Vs", (), {"value": 0})(),
                    "h_swing_mode": type("Hs", (), {"value": 0})(),
                    "converti_mode": type("Cv", (), {"value": 0})(),
                    "preset_mode": type("Pr", (), {"value": "none"})(),
                })(),
                "register_callback": lambda *args, **kwargs: None,
                "remove_callback": lambda *args, **kwargs: None,
                "turn_on": _dummy_async_coro,
                "turn_off": _dummy_async_coro,
                "set_temperature": _dummy_async_coro,
                "set_hvac_mode": _dummy_async_coro,
                "set_fan_mode": _dummy_async_coro,
                "set_v_swing_mode": _dummy_async_coro,
                "set_h_swing_mode": _dummy_async_coro,
                "set_preset_mode": _dummy_async_coro,
                "set_converti_mode": _dummy_async_coro,
                "set_display_mode": _dummy_async_coro,
                "set_nanoe": _dummy_async_coro,
            })()
            hub.home.devices.append(m_dummy)


    # Initialize 2.0 State Coordinators for all discovered & manual AC units
    coordinators = {}
    devices_opt = entry.options.get("devices", {})

    try:
        try:
            from panasonic_ac_models import ACModelLookup  # type: ignore[import-not-found, import-untyped]
        except ImportError:
            from .panasonic_ac_models import ACModelLookup
        lookup = await hass.async_add_executor_job(ACModelLookup)
    except Exception as exc:
        LOGGER.error("Failed to load ACModelLookup: %s", exc)
        lookup = None

    target_dev_id = entry.data.get("device_id")
    target_devices = get_devices_for_entry(hub, entry)
    if not target_devices and target_dev_id:
        found_ids = [getattr(d, "id", None) for d in getattr(getattr(hub, "home", None), "devices", [])]
        LOGGER.error(
            "ConfigEntry %s expects device_id '%s', but it was not found in MirAIe cloud account (available devices: %s). Postponing setup.",
            entry.entry_id,
            target_dev_id,
            found_ids,
        )
        raise ConfigEntryNotReady(f"Device {target_dev_id} not found in MirAIe account")

    _cleanup_cross_device_entities(hass, entry, target_dev_id)


    for device in target_devices:
        dev_opt = devices_opt.get(device.id, {})
        blaster_id = dev_opt.get(CONF_BLASTER_ENTITY_ID) or entry.options.get(CONF_BLASTER_ENTITY_ID) or entry.data.get(CONF_BLASTER_ENTITY_ID)
        receiver_id = dev_opt.get(CONF_RECEIVER_ENTITY_ID) or entry.options.get(CONF_RECEIVER_ENTITY_ID) or entry.data.get(CONF_RECEIVER_ENTITY_ID)
        primary = dev_opt.get(CONF_PRIMARY_BACKEND) or entry.options.get(CONF_PRIMARY_BACKEND, "cloud" if not is_ir_only else "ir")
        submode = dev_opt.get(CONF_HYBRID_SUBMODE) or entry.options.get(CONF_HYBRID_SUBMODE, "auto" if not is_ir_only else "manual")
        ir_fmt = dev_opt.get(CONF_IR_FORMAT) or entry.options.get(CONF_IR_FORMAT) or entry.data.get(CONF_IR_FORMAT) or dev_opt.get("working_ir_format") or entry.options.get("working_ir_format") or "auto"
        temp_sensor_id = dev_opt.get("room_temp_sensor") or entry.options.get("room_temp_sensor") or entry.data.get("room_temp_sensor") or dev_opt.get(CONF_ROOM_TEMP_SENSOR) or entry.options.get(CONF_ROOM_TEMP_SENSOR) or entry.data.get(CONF_ROOM_TEMP_SENSOR)
        availability_id = dev_opt.get(CONF_AVAILABILITY_ENTITY_ID) or entry.options.get(CONF_AVAILABILITY_ENTITY_ID) or entry.data.get(CONF_AVAILABILITY_ENTITY_ID)

        model_code = dev_opt.get("model_code") or entry.options.get("model_code") or entry.data.get("model_code") or getattr(getattr(device, "details", None), "model_number", "") or ""

        coordinator = MirAIeDeviceCoordinator(
            hass=hass,
            entry_id=entry.entry_id,
            device_id=device.id,
            model_code=model_code,
            has_wifi=not is_ir_only,
            blaster_entity_id=blaster_id,
            receiver_entity_id=receiver_id,
            temperature_sensor_entity_id=temp_sensor_id,
            availability_entity_id=availability_id,
            primary_backend=primary,
            hybrid_submode=submode,
            ir_format=ir_fmt,
            lookup=lookup,
        )
        coordinator.hub = hub
        model_caps = getattr(device, "model_capabilities", None)
        if model_caps:
            coordinator.update_model_capabilities(model_caps)
        coordinator.async_setup_receiver()
        coordinators[device.id] = coordinator

        if not is_ir_only:
            cloud_cb = _make_cloud_cb(hass, coordinator, device)
            device.register_callback(cloud_cb)
            if getattr(device, "status", None):
                cloud_cb()

    setattr(hub, "coordinators", coordinators)





    # Migrate old-format unique_ids (idempotent, safe to run every startup)
    _migrate_unique_ids(hass, entry, hub)


    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Immediately push current device status to Home Assistant so entities
    # evaluate availability and state without waiting for a state change.
    for dev in target_devices:
        try:
            dev.refresh()
        except Exception as ref_err:
            LOGGER.debug("Initial device refresh for %s encountered: %s", dev.id, ref_err)

    # Register listener for option updates to automatically reload entry when options change
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    def _log_backfill_result(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            LOGGER.error("Energy statistics backfill task failed", exc_info=exc)

    def _get_device_install_date(device_id: str) -> date:
        devices_opt = entry.options.get("devices", {})
        dev_opt = devices_opt.get(device_id, {})
        install_str = dev_opt.get(CONF_INSTALL_DATE) or entry.options.get(CONF_INSTALL_DATE)
        if install_str:
            return date.fromisoformat(install_str)
        return six_months_ago(date.today())

    async def _run_startup_backfill(hass: HomeAssistant) -> None:
        """Run the initial backfill only once HA has fully finished starting."""
        for device in target_devices:
            start_date = _get_device_install_date(device.id)
            task = hass.async_create_task(
                async_backfill_energy_statistics(hass, hub, device, start_date)
            )
            task.add_done_callback(_log_backfill_result)

    # Run nightly and startup backfill only for Wi-Fi / Cloud accounts
    if not is_ir_only and has_username:
        if hass.is_running:
            hass.async_create_task(_run_startup_backfill(hass))
        else:
            entry.async_on_unload(async_at_started(hass, _run_startup_backfill))

        async def nightly_backfill(now=None):
            for device in target_devices:
                backfill_start = _get_device_install_date(device.id)
                task = hass.async_create_task(
                    async_backfill_energy_statistics(hass, hub, device, backfill_start)
                )
                task.add_done_callback(_log_backfill_result)

        # Run nightly backfill at 02:05 AM IST to ensure Panasonic cloud servers have finalized yesterday's daily batch
        unsub = async_track_time_change(hass, nightly_backfill, hour=2, minute=5, second=0)
        entry.async_on_unload(unsub)

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry automatically when options are updated from Options Flow."""
    hub = getattr(entry, "runtime_data", None)
    coordinators = getattr(hub, "coordinators", {}) if hub else {}
    if any(getattr(c, "_suppress_reload", False) for c in coordinators.values()):
        for c in coordinators.values():
            c._suppress_reload = False
        return
    await hass.config_entries.async_reload(entry.entry_id)



async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = getattr(entry, "data", {}) if isinstance(getattr(entry, "data", {}), dict) else {}
        username = entry_data.get("username", "").lower() if "username" in entry_data else None
        is_ir_only = entry_data.get("is_ir_only", False) or not username

        hub_inst = getattr(entry, "runtime_data", None)
        if hub_inst and hasattr(hub_inst, "coordinators"):
            for c in getattr(hub_inst, "coordinators", {}).values():
                if hasattr(c, "async_unload"):
                    c.async_unload()

        if is_ir_only or not username:
            hub: MirAIeHub | None = getattr(entry, "runtime_data", None)
            if hub:
                try:
                    if hasattr(hub, "close"):
                        await hub.close()
                    else:
                        for task in list(getattr(hub, "background_tasks", [])):
                            task.cancel()
                except Exception as err:
                    LOGGER.debug("Error closing hub during unload of %s: %s", entry.title, err)
        else:
            sessions = hass.data.get(DOMAIN, {}).get("sessions", {})
            account_session = sessions.get(username)
            if account_session:
                account_session["entries"].discard(entry.entry_id)
                if not account_session["entries"]:
                    hub = account_session["hub"]
                    try:
                        if hasattr(hub, "close"):
                            await hub.close()
                        else:
                            for task in list(getattr(hub, "background_tasks", [])):
                                task.cancel()
                    except Exception as err:
                        LOGGER.debug("Error closing shared hub for %s: %s", username, err)
                    sessions.pop(username, None)

    return unload_ok



async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry."""
    LOGGER.info("Removed config entry: %s (%s)", entry.title, entry.entry_id)


def _notify_migration_successful(hass: HomeAssistant) -> None:
    """Notify users of successful migration and device setup instructions."""
    try:
        from homeassistant.components import persistent_notification
        title = "Panasonic AC India Migration Complete"
        msg = (
            "Your Panasonic AC integration has been successfully upgraded!\n\n"
            "Each AC unit is now managed as an independent device entry with hybrid Cloud + IR support.\n\n"
            "**To configure an IR Blaster, IR Receiver, or customize device settings:**\n"
            "1. Go to **Settings → Devices & Services → Panasonic AC India**.\n"
            "2. Find your AC device and click the **Configure (Settings Cog)** button.\n"
            "3. Set up your **IR Blaster / Transmitter**, **IR Receiver**, or control preferences."
        )
        persistent_notification.async_dismiss(hass, "miraie_20_migration_complete")
        persistent_notification.async_create(
            hass,
            msg,
            title=title,
            notification_id="panasonic_ac_migration_complete",
        )
    except Exception as exc:
        LOGGER.debug("Could not send migration notification: %s", exc)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate mirAIe_in config entry to version 2."""
    LOGGER.info("Migrating mirAIe_in config entry %s from version %s", config_entry.entry_id, config_entry.version)

    if config_entry.version == 1:
        hass.config_entries.async_update_entry(config_entry, version=2)
        LOGGER.info("Migrated mirAIe_in config entry %s to version 2", config_entry.entry_id)
        _notify_migration_successful(hass)

    return True


def _make_cloud_cb(hass: HomeAssistant, coord: MirAIeDeviceCoordinator, dev: Any):
    def _cloud_cb(*args, **kwargs):
        status_obj = getattr(dev, "status", None)
        if status_obj:
            v_swing = getattr(status_obj, "v_swing_mode", None)
            h_swing = getattr(status_obj, "h_swing_mode", None)
            v_val = v_swing.value if v_swing and hasattr(v_swing, "value") else v_swing
            h_val = h_swing.value if h_swing and hasattr(h_swing, "value") else h_swing

            c_mode = getattr(status_obj, "converti_mode", None)
            c_val = getattr(c_mode, "value", 0) if c_mode else 0
            if isinstance(c_val, str):
                import re
                m = re.search(r"\d+", c_val)
                c_val = int(m.group(0)) if m else 0

            preset_obj = getattr(status_obj, "preset_mode", None)
            preset_val = preset_obj.value if preset_obj and hasattr(preset_obj, "value") else str(preset_obj or "none")
            nanoe_val = getattr(status_obj, "nanoe_mode", "off")
            disp_obj = getattr(status_obj, "display_mode", None)
            disp_val = disp_obj.value if disp_obj and hasattr(disp_obj, "value") else disp_obj

            power_obj = getattr(status_obj, "power_mode", None)
            power_val = power_obj.value if power_obj and hasattr(power_obj, "value") else power_obj
            hvac_obj = getattr(status_obj, "hvac_mode", None)
            hvac_val = hvac_obj.value if hvac_obj and hasattr(hvac_obj, "value") else hvac_obj
            fan_obj = getattr(status_obj, "fan_mode", None)
            fan_val = fan_obj.value if fan_obj and hasattr(fan_obj, "value") else fan_obj

            cloud_data = {
                "pwr": "on" if power_val == "on" else "off",
                "md": hvac_val,
                "tset": getattr(status_obj, "temperature", None),
                "acfs": fan_val,
                "acvs": SWING_V_MAP.get(v_val, V0) if v_val is not None else None,
                "achs": SWING_H_MAP.get(h_val, H0) if h_val is not None else None,
                "acec": "on" if preset_val == "eco" else "off",
                "acngs": "on" if str(nanoe_val).lower() in ("on", "1", "true") else "off",
                "acdc": "on" if str(disp_val).lower() in ("on", "1", "true") else "off",
                "bzr": "on" if getattr(status_obj, "buzzer", False) else "off",
                "errors": getattr(status_obj, "error_code", ""),
                "warnings": getattr(status_obj, "warning_code", ""),
                "acfc": "on" if getattr(status_obj, "filter_clean_alert", False) else "off",
                "converti": c_val,
                "preset": preset_val,
            }
            hass.async_create_task(coord.async_handle_cloud_update(cloud_data))
    return _cloud_cb







