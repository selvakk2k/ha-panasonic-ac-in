import asyncio
import re
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Sequence, Mapping
from datetime import date, datetime, timezone, timedelta

from miraie_ac import Device as MirAIeDevice, MirAIeHub, ConsumptionPeriodType

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_import_statistics,
    get_last_statistics,
)

try:
    from homeassistant.components.recorder.statistics import StatisticMeanType
    MEAN_TYPE_NONE: Any = StatisticMeanType.NONE
except ImportError:
    MEAN_TYPE_NONE: Any = 0
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .utils import six_months_ago, get_last_sunday, get_devices_for_entry, is_ir_only_entry
from .logger import LOGGER




class MirAIeEnergySensor(SensorEntity, ABC):
    """Sensor for AC Power Consumption."""
    @property
    @abstractmethod
    def period_type(self) -> ConsumptionPeriodType:
        """Return the consumption period type."""
        ...

    @property
    def sensor_label(self) -> str:
        """Human-facing label used for the entity name/unique_id.

        Defaults to period_type's value, but can be overridden -- needed
        for sensors that share the same underlying period_type (e.g.
        Yesterday and Today both use ConsumptionPeriodType.DAILY, just
        with different dates) but must still get distinct names/IDs.
        """
        return self.period_type.value

    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        """Initialize the sensor."""
        self.hub = hub
        self.device = device
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{device.id}_{self.sensor_label.lower()}_energy"
        self._attr_should_poll = False
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = None
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_suggested_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_suggested_display_precision = 2
        self._attr_native_value = None

    async def async_update(self):
        """Update the sensor state with the latest energy consumption data."""
        if not self.hub.http or self.hub.http.closed:
            LOGGER.error("MirAIe HTTP session is closed or unavailable")
            return
        consumption = await self.get_energy_consumption()

        if consumption is None:
            """Skip update if no new data."""
            return

        if self._attr_state_class == SensorStateClass.TOTAL:
            await self._set_last_reset_time()
        else:
            self._attr_last_reset = None
            
        self._attr_native_value = consumption

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        LOGGER.debug(f"Removing energy consumption entity ({self.entity_id}) from HA")
        return await super().async_will_remove_from_hass()

    @abstractmethod
    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest power consumption data."""
        raise NotImplementedError

    @abstractmethod
    async def _set_last_reset_time(self):
        """Set the last reset time for the sensor entity."""
        raise NotImplementedError

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

class MirAIeYesterdayEnergySensor(MirAIeEnergySensor):
    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        super().__init__(hub, device)
        self._attr_translation_key = "yesterday_consumption"

    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.DAILY

    @property
    def sensor_label(self) -> str:
        return "Yesterday"

    async def get_energy_consumption(self) -> float | None:
        """Fetch yesterday's total energy consumption data."""
        yesterday = dt_util.now().date() - timedelta(days=1)
        date_string = yesterday.strftime("%d%m%Y")
        try:
            consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
            value = consumption.get(date_string)
            LOGGER.debug(
                "%s | %s | key=%s | raw=%s | value=%s",
                self.sensor_label, self.device.friendly_name, date_string, consumption, value,
            )
            return value
        except Exception:
            LOGGER.exception(
                "%s energy consumption fetch failed for %s [date_key=%s]",
                self.sensor_label, self.device.friendly_name, date_string,
            )
            raise

    async def _set_last_reset_time(self):
        """Set the last reset time for the yesterday energy sensor entity."""
        now = dt_util.now()
        start_of_today = dt_util.start_of_local_day(now)
        if not getattr(self, "_attr_last_reset", None) or self._attr_last_reset < start_of_today:
            self._attr_last_reset = now

class MirAIeTodayEnergySensor(MirAIeEnergySensor):
    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        super().__init__(hub, device)
        self._attr_translation_key = "current_consumption"

    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.DAILY

    @property
    def sensor_label(self) -> str:
        return "Today"

    async def get_energy_consumption(self) -> float | None:
        """Fetch today's (live, rolling) energy consumption data so far."""
        today = dt_util.now().date()
        date_string = today.strftime("%d%m%Y")
        try:
            consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
            value = consumption.get(date_string)
            LOGGER.debug(
                "%s | %s | key=%s | raw=%s | value=%s",
                self.sensor_label, self.device.friendly_name, date_string, consumption, value,
            )
            return value
        except Exception:
            LOGGER.exception(
                "%s energy consumption fetch failed for %s [date_key=%s]",
                self.sensor_label, self.device.friendly_name, date_string,
            )
            raise

    async def async_update(self):
        """Update today's energy consumption sensor state and sync recorder statistic."""
        await super().async_update()
        if self._attr_native_value is not None and isinstance(self._attr_native_value, (int, float, str)):
            await self._async_sync_today_statistic(float(self._attr_native_value))

    async def _async_sync_today_statistic(self, today_val: float) -> None:
        """Sync today's live energy consumption to recorder statistics DB."""
        try:
            clean_dev_id = re.sub(r"[^a-z0-9_]", "_", self.device.id.lower())
            statistic_id = f"sensor.{clean_dev_id}_energy_history"
            baseline_sum = float(getattr(self.device, "backfilled_energy_sum", 0.0))

            if baseline_sum <= 0.0:
                last_stats = await get_instance(self.hass).async_add_executor_job(
                    get_last_statistics, self.hass, 50, statistic_id, False, {"sum"}
                )
                if last_stats and last_stats.get(statistic_id):
                    today_start_ts = _get_statistic_timestamp(dt_util.now().date()).timestamp()
                    past_entries = [e for e in last_stats[statistic_id] if float(e.get("start") or 0.0) <= today_start_ts and float(e.get("sum") or 0.0) > 0.0]
                    if past_entries:
                        baseline_sum = float(past_entries[-1].get("sum") or 0.0)
                        setattr(self.device, "backfilled_energy_sum", baseline_sum)

            if baseline_sum <= 0.0:
                return

            expected_sum = round(baseline_sum + max(0.0, float(today_val)), 2)
            now_dt = dt_util.as_utc(dt_util.now()).replace(minute=0, second=0, microsecond=0)

            # Check existing today statistic in DB to avoid unneeded imports
            last_stats = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 5, statistic_id, False, {"sum"}
            )
            today_start_ts = _get_statistic_timestamp(dt_util.now().date()).timestamp()
            if last_stats and last_stats.get(statistic_id):
                today_entries = [e for e in last_stats[statistic_id] if float(e.get("start") or 0.0) >= today_start_ts]
                if today_entries:
                    latest_today_sum = float(today_entries[-1].get("sum") or 0.0)
                    if abs(latest_today_sum - expected_sum) < 0.02:
                        return

            metadata = StatisticMetaData(
                has_sum=True,
                has_mean=False,
                mean_type=MEAN_TYPE_NONE,
                unit_class="energy",
                name=f"{self.device.friendly_name} Energy History",
                source="recorder",
                statistic_id=statistic_id,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )
            async_import_statistics(self.hass, metadata, [StatisticData(start=now_dt, sum=expected_sum, state=expected_sum)])
            LOGGER.info("[%s] Synced today's live energy statistic to DB: %s kWh", self.device.friendly_name, expected_sum)
        except Exception as e:
            LOGGER.debug("[%s] Failed to sync today's energy statistic: %s", self.device.friendly_name, e)

    async def _set_last_reset_time(self):
        """Set the last reset time for the today energy sensor entity."""
        now = dt_util.now()
        start_of_today = dt_util.start_of_local_day(now)
        if not getattr(self, "_attr_last_reset", None) or self._attr_last_reset < start_of_today:
            self._attr_last_reset = now

class MirAIeWeeklyEnergySensor(MirAIeEnergySensor):
    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        super().__init__(hub, device)
        self._attr_translation_key = "weekly_consumption"

    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.WEEKLY

    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest weekly energy consumption data."""
        date_string = get_last_sunday().strftime("%d%m%Y")
        try:
            consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
            value = consumption.get(date_string)
            LOGGER.debug(
                "%s | %s | key=%s | raw=%s | value=%s",
                self.period_type.value, self.device.friendly_name, date_string, consumption, value,
            )
            return value
        except Exception:
            LOGGER.exception(
                "%s energy consumption fetch failed for %s [date_key=%s]",
                self.period_type.value, self.device.friendly_name, date_string,
            )
            raise

    async def _set_last_reset_time(self):
        """Set the last reset time for the weekly energy sensor entity."""
        now = dt_util.now()
        start_of_week = dt_util.start_of_local_day(now - timedelta(days=now.weekday() + 1))
        if not getattr(self, "_attr_last_reset", None) or self._attr_last_reset < start_of_week:
            self._attr_last_reset = now

class MirAIeMonthlyEnergySensor(MirAIeEnergySensor):
    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        super().__init__(hub, device)
        self._attr_translation_key = "monthly_consumption"

    @property
    def period_type(self) -> ConsumptionPeriodType:
        return ConsumptionPeriodType.MONTHLY

    async def get_energy_consumption(self) -> float | None:
        """Fetch the latest monthly energy consumption data."""
        yesterday = dt_util.now().date() - timedelta(days=1)
        date_string = yesterday.strftime("%m%Y")
        try:
            consumption = await self.hub.get_energy_consumption(self.device, self.period_type, from_date=date_string)
            value = consumption.get(date_string)
            LOGGER.debug(
                "%s | %s | key=%s | raw=%s | value=%s",
                self.period_type.value, self.device.friendly_name, date_string, consumption, value,
            )
            return value
        except Exception:
            LOGGER.exception(
                "%s energy consumption fetch failed for %s [date_key=%s]",
                self.period_type.value, self.device.friendly_name, date_string,
            )
            raise

    async def _set_last_reset_time(self):
        """Set the last reset time for the monthly energy sensor entity."""
        now = dt_util.now()
        start_of_month = dt_util.start_of_local_day(now.replace(day=1))
        if not getattr(self, "_attr_last_reset", None) or self._attr_last_reset < start_of_month:
            self._attr_last_reset = now


class MirAIeEnergyHistorySensor(MirAIeTodayEnergySensor, RestoreEntity):
    """Cumulative energy history sensor for long-term statistics & Energy Dashboard."""

    def __init__(self, hub: MirAIeHub, device: MirAIeDevice):
        super().__init__(hub, device)
        self._attr_translation_key = "energy_history"
        self._attr_unique_id = f"{device.id}_energy_history"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._restored_last_state: float | None = None

    @property
    def sensor_label(self) -> str:
        return "Energy History"

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unavailable", "unknown"):
            try:
                self._restored_last_state = float(last_state.state)
                LOGGER.debug("%s: Restored last state=%s kWh from HA state machine", self.sensor_label, self._restored_last_state)
            except (ValueError, TypeError):
                pass

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"miraie_backfill_complete_{self.device.id}",
                self._handle_backfill_complete,
            )
        )

    async def _set_last_reset_time(self):
        """No last_reset for TOTAL_INCREASING sensors."""
        pass

    async def _handle_backfill_complete(self):
        """Update sensor state when backfill finishes."""
        LOGGER.debug("%s: Backfill completed, updating state", self.sensor_label)
        self.async_schedule_update_ha_state(True)

    async def get_energy_consumption(self) -> float | None:
        """Fetch total cumulative energy consumption (yesterday's baseline + today's running energy)."""
        today_val = 0.0
        try:
            today_consumption = await super().get_energy_consumption()
            if today_consumption is not None:
                today_val = max(0.0, float(today_consumption))
        except Exception as e:
            LOGGER.debug("%s: Could not fetch today's consumption: %s", self.sensor_label, e)

        base_sum = float(getattr(self.device, "backfilled_energy_sum", 0.0))
        if base_sum <= 0.0 and self._restored_last_state is not None and self._restored_last_state > 50.0:
            base_sum = max(0.0, self._restored_last_state - today_val)

        if base_sum > 0.0:
            return round(base_sum + today_val, 2)

        if self._restored_last_state is not None and self._restored_last_state > 50.0:
            return round(self._restored_last_state + today_val, 2)

        # Do NOT report uninitialized partial state (e.g. today_val alone) to prevent negative statistic drops
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up MirAIe energy and status sensors from a config entry."""
    hub: MirAIeHub = entry.runtime_data
    coordinators = getattr(hub, "coordinators", {})
    is_ir_entry = is_ir_only_entry(entry)
    
    devices = get_devices_for_entry(hub, entry)


    # 1. Setup Energy Sensors (which need active polling — Wi-Fi models only)
    energy_sensors: list[MirAIeEnergySensor] = []
    if not is_ir_entry:
        for device in devices:
            coord = coordinators.get(device.id)
            has_wifi = getattr(coord, "has_wifi", True) if coord else getattr(getattr(device, "details", None), "has_wifi", True)
            if not has_wifi:
                continue

            clean_dev_id = re.sub(r"[^a-z0-9_]", "_", device.id.lower())
            statistic_id = f"sensor.{clean_dev_id}_energy_history"
            try:
                last_stats = await get_instance(hass).async_add_executor_job(
                    get_last_statistics, hass, 100, statistic_id, False, {"sum"}
                )
                if last_stats and last_stats.get(statistic_id):
                    entries = sorted(last_stats[statistic_id], key=lambda e: float(e.get("start") or 0.0))
                    for entry_stat in reversed(entries):
                        raw_sum = float(entry_stat.get("sum") or 0.0)
                        if 0 < raw_sum <= 400:
                            setattr(device, "backfilled_energy_sum", raw_sum)
                            LOGGER.debug("[%s] Restored backfilled_energy_sum=%s kWh from recorder statistics", device.friendly_name, raw_sum)
                            break
            except Exception as e:
                LOGGER.debug("Could not pre-initialize backfilled_energy_sum for %s: %s", device.friendly_name, e)

            energy_sensors += [
                MirAIeYesterdayEnergySensor(hub, device),
                MirAIeTodayEnergySensor(hub, device),
                MirAIeWeeklyEnergySensor(hub, device),
                MirAIeMonthlyEnergySensor(hub, device),
            ]
    async_add_entities(energy_sensors, update_before_add=True)

    poll_sensors = list(energy_sensors)

    async def update_sensors(now=None):
        # Gather updates concurrently to avoid sequential HTTP requests
        await asyncio.gather(
            *(sensor.async_update() for sensor in poll_sensors),
            return_exceptions=True
        )
        for sensor in poll_sensors:
            sensor.async_write_ha_state()  # Ensure HA is notified of new data

    cancel_interval = async_track_time_interval(hass, update_sensors, timedelta(minutes=30))
    if hasattr(entry, "async_on_unload"):
        entry.async_on_unload(cancel_interval)

    # 2. Setup Non-Polling Sensors (updated via device callback pushed via MQTT)
    pushed_sensors: list[SensorEntity] = []
    for device in devices:
        coordinator = coordinators.get(device.id)
        if not is_ir_entry and (not coordinator or coordinator.has_wifi):
            pushed_sensors.append(MirAIeRoomTemperatureSensor(device))
            pushed_sensors.append(MirAIeErrorCodeSensor(device, coordinator))
        pushed_sensors.append(MirAIeModelCapabilitiesSensor(device, coordinator))

        if not is_ir_entry and (not coordinator or coordinator.has_wifi):
            pushed_sensors.append(MirAIeWifiSignalSensor(device))

        if coordinator:
            pushed_sensors.append(MirAIeControlSourceSensor(device, coordinator))

    async_add_entities(pushed_sensors)



class MirAIeRoomTemperatureSensor(SensorEntity):
    """Exposes current room temperature as a standalone sensor."""

    def __init__(self, device: MirAIeDevice):
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{device.id}_room_temperature"
        self._attr_translation_key = "ac_temperature"
        self.device = device
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        return self.device.status.room_temperature

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_added_to_hass(self):
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self):
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeWifiSignalSensor(SensorEntity):
    """Exposes WiFi RSSI signal strength (disabled by default)."""

    def __init__(self, device: MirAIeDevice):
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{device.id}_wifi_signal"
        self._attr_translation_key = "wifi_signal"
        self.device = device
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> int:
        return getattr(self.device.status, "wifi_signal", 0)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_added_to_hass(self):
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self):
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeErrorCodeSensor(SensorEntity):
    """Exposes AC hardware diagnostic error code."""

    def __init__(self, device: MirAIeDevice, coordinator=None):
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{device.id}_error_code"
        self._attr_translation_key = "error_code"
        self.device = device
        self.coordinator = coordinator
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:alert-circle-outline"

    @property
    def native_value(self) -> str:
        if self.coordinator and self.coordinator.state and "error_code" in self.coordinator.state:
            err = self.coordinator.state.get("error_code")
            return str(err) if err else "OK"
        raw_err = getattr(getattr(self.device, "status", None), "error_code", "")
        return str(raw_err) if raw_err else "OK"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self.coordinator and self.coordinator.state and "warning_code" in self.coordinator.state:
            attrs["warning_code"] = str(self.coordinator.state.get("warning_code", ""))
        elif hasattr(self.device, "status"):
            attrs["warning_code"] = str(getattr(self.device.status, "warning_code", ""))
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=getattr(getattr(self.device, "details", None), "brand", "Panasonic"),
            model=getattr(getattr(self.device, "details", None), "model_number", ""),
            sw_version=getattr(getattr(self.device, "details", None), "firmware_version", ""),
        )

    async def async_added_to_hass(self):
        if self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self):
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeControlSourceSensor(SensorEntity):
    """Exposes last control origin (Cloud, IR, or External)."""

    def __init__(self, device: MirAIeDevice, coordinator=None):
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{device.id}_control_source"
        self._attr_translation_key = "last_controlled_via"
        self.device = device
        self.coordinator = coordinator
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = True

    @property
    def native_value(self) -> str:
        if self.coordinator and self.coordinator.state:
            return str(self.coordinator.state.get("last_controlled_by", "Cloud"))
        return "Cloud"

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        raw_source = getattr(self.device.status, "control_source", "unknown")
        return {"raw_control_source": raw_source}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )

    async def async_added_to_hass(self):
        if self.coordinator:
            self.async_on_remove(
                self.coordinator.async_add_listener(self.async_write_ha_state)
            )
        def _safe_device_cb(*args, **kwargs) -> None:
            if hasattr(self, "hass") and self.hass and hasattr(self.hass, "loop"):
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            else:
                self.async_write_ha_state()

        self._device_callback = _safe_device_cb
        self.device.register_callback(self._device_callback)

    async def async_will_remove_from_hass(self):
        if hasattr(self, "_device_callback"):
            self.device.remove_callback(self._device_callback)


class MirAIeModelCapabilitiesSensor(SensorEntity):
    """Exposes hardware model details and auto-detected capabilities from panasonic-ac-models."""

    def __init__(self, device: MirAIeDevice, coordinator=None):
        self._attr_should_poll = False
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{device.id}_model_capabilities"
        self._attr_translation_key = "model_capabilities"
        self.device = device
        self.coordinator = coordinator
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:chip"

    @property
    def native_value(self) -> str:
        model_number = getattr(getattr(self.device, "details", None), "model_number", None)
        return model_number or "CS-CU-RU18CKY-1"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator and hasattr(self.coordinator, "capabilities") and self.coordinator.capabilities:
            return dict(self.coordinator.capabilities)
        return {
            "series": "EU",
            "has_wifi": True,
            "has_heat_mode": False,
            "has_nanoe": False,
            "converti_type": "7-in-1",
            "resolved_via": "default",
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            name=self.device.friendly_name,
            manufacturer=self.device.details.brand,
            model=self.device.details.model_number,
            sw_version=self.device.details.firmware_version,
        )





def _get_statistic_timestamp(target_date: date) -> datetime:
    """Return top-of-hour UTC timestamp corresponding to local midnight of target_date.

    Home Assistant statistics require UTC timestamps with minute=0 and second=0.
    For UTC+5:30 (India), local midnight 00:00 IST on date D is 18:30 UTC on D-1.
    Rounding to top of UTC hour yields 18:00 UTC on D-1, which aligns precisely
    with the local day bucket in Home Assistant Energy Dashboard.
    """
    local_dt = dt_util.start_of_local_day(datetime.combine(target_date, datetime.min.time()))
    utc_dt = dt_util.as_utc(local_dt)
    return utc_dt.replace(minute=0, second=0, microsecond=0)


async def _async_clear_statistics_helper(hass: HomeAssistant, statistic_id: str) -> bool:
    """Safely clear statistics for a statistic_id using HA recorder API."""
    try:
        recorder = get_instance(hass)
        recorder.async_clear_statistics([statistic_id])
        async with asyncio.timeout(90):
            await recorder.async_block_till_done()
        LOGGER.info("Successfully cleared recorder statistics for %s", statistic_id)
        return True
    except Exception as e:
        LOGGER.exception("Failed to clear statistics for %s: %s", statistic_id, e)
        return False


def _extract_recorded_range_sum(entries: Sequence[Mapping[str, Any]] | list[dict[str, Any]], start_day: date, end_day: date) -> float | None:
    """Calculate recorded statistics sum delta between local midnight of start_day and end_day + 1."""
    start_ts = _get_statistic_timestamp(start_day).timestamp()
    end_ts = _get_statistic_timestamp(end_day + timedelta(days=1)).timestamp()

    start_sum = end_sum = None
    for entry in entries:
        ts = float(entry.get("start") or 0.0)
        if ts == start_ts:
            start_sum = float(entry.get("sum") or 0.0)
        if ts == end_ts:
            end_sum = float(entry.get("sum") or 0.0)

    # Fallback to closest bounding entry if exact timestamp isn't present
    if start_sum is None and entries:
        closest_start = min(entries, key=lambda e: abs(float(e.get("start") or 0.0) - start_ts))
        start_sum = float(closest_start.get("sum") or 0.0)

    if end_sum is None and entries:
        closest_end = min(entries, key=lambda e: abs(float(e.get("start") or 0.0) - end_ts))
        end_sum = float(closest_end.get("sum") or 0.0)

    if start_sum is not None and end_sum is not None:
        return max(0.0, end_sum - start_sum)
    return None


async def async_rebuild_full_energy_statistics(
    hass: HomeAssistant,
    hub: MirAIeHub,
    device: MirAIeDevice,
) -> None:
    """Manually force a complete 6-8 month rebuild of energy statistics for a device (diagnostic button)."""
    default_start_date = dt_util.now().date() - timedelta(days=240)
    LOGGER.info("[%s] Manual Diagnostic Button pressed: Triggering full 6-8 month energy statistics rebuild", device.friendly_name)
    await async_backfill_energy_statistics(hass, hub, device, default_start_date, force_full_rebuild=True)


async def async_backfill_energy_statistics(
    hass: HomeAssistant,
    hub: MirAIeHub,
    device: MirAIeDevice,
    default_start_date: date,
    force_full_rebuild: bool = False,
) -> None:
    """Backfill daily energy history into HA recorder statistics with API reconciliation."""
    if not hub.http or hub.http.closed:
        hub.http = async_get_clientsession(hass)

    clean_dev_id = re.sub(r"[^a-z0-9_]", "_", device.id.lower())
    statistic_id = f"sensor.{clean_dev_id}_energy_history"

    end_date = dt_util.now().date() - timedelta(days=1)
    LOGGER.info("[%s] Starting energy statistics verification & backfill check (force_full_rebuild=%s, end_date=%s)", device.friendly_name, force_full_rebuild, end_date.isoformat())

    # 1. Handle Forced Full Rebuild (e.g. Diagnostic Button press)
    if force_full_rebuild:
        LOGGER.info("[%s] Executing forced full energy statistics rebuild from %s to %s", device.friendly_name, default_start_date.isoformat(), end_date.isoformat())
        await _async_clear_statistics_helper(hass, statistic_id)
        await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
        return

    last_stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 100, statistic_id, False, {"sum"}
    )

    # 2. Case: NO existing data in HA statistics -> Pull max historical daily data from MirAIe
    if not last_stats or not last_stats.get(statistic_id):
        LOGGER.info("[%s] No existing energy statistics found in Home Assistant; pulling full history from MirAIe (%s to %s)", device.friendly_name, default_start_date.isoformat(), end_date.isoformat())
        await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
        return

    entries = sorted(last_stats[statistic_id], key=lambda e: float(e.get("start") or 0.0))
    if not entries:
        LOGGER.info("[%s] Empty energy statistics entries in Home Assistant; pulling full history from MirAIe (%s to %s)", device.friendly_name, default_start_date.isoformat(), end_date.isoformat())
        await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
        return

    LOGGER.debug("[%s] Found %s existing statistic entries in Home Assistant recorder DB", device.friendly_name, len(entries))

    # Basic sanity checks on existing historical entries (excluding today's intraday entries)
    latest_valid_timestamp = dt_util.now()
    today_start_ts = _get_statistic_timestamp(dt_util.now().date()).timestamp()
    corrupt = False
    prev_sum = None
    for entry in entries:
        ts = float(entry.get("start") or 0.0)
        if ts >= today_start_ts:
            continue
        entry_utc = datetime.fromtimestamp(entry["start"], tz=timezone.utc)
        raw_sum = float(entry.get("sum") or 0.0)
        reason = None
        if raw_sum < 0:
            reason = "negative sum"
        elif raw_sum > 400:
            reason = f"unrealistic sum ({raw_sum} > 400)"
        elif entry_utc > latest_valid_timestamp:
            reason = f"future timestamp ({entry_utc.isoformat()} > {latest_valid_timestamp.isoformat()})"
        elif prev_sum is not None and prev_sum > 0.0 and (raw_sum - prev_sum) > 50.0:
            reason = f"single day spike ({raw_sum - prev_sum:.2f} kWh > 50 kWh)"
        elif prev_sum is not None and prev_sum > 0.0 and (prev_sum - raw_sum) > 0.05:
            reason = f"decreasing sum ({raw_sum} < {prev_sum})"

        if reason is not None:
            LOGGER.warning(
                "[%s] Corrupted/inflated energy statistics detected (reason=%s, start=%s, sum=%s); rebuilding from API",
                device.friendly_name,
                reason,
                entry_utc.isoformat(),
                raw_sum,
            )
            corrupt = True
            break
        prev_sum = raw_sum

    if corrupt:
        await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
        return

    # 3. Hierarchy Verification: Yesterday -> Weekly -> Monthly (API > Recorder)

    # 3a. Yesterday Verification
    yesterday = end_date
    yesterday_str = yesterday.strftime("%d%m%Y")
    yesterday_api_val = None
    try:
        y_data = await hub.get_energy_consumption(device, ConsumptionPeriodType.DAILY, from_date=yesterday_str)
        if y_data and yesterday_str in y_data:
            yesterday_api_val = float(y_data[yesterday_str])
    except Exception as e:
        LOGGER.debug("[%s] Could not fetch yesterday's API energy: %s", device.friendly_name, e)

    recorded_yesterday_delta = _extract_recorded_range_sum(entries, yesterday, yesterday)
    LOGGER.info(
        "[%s] [Gating 1/3 Yesterday Check] API Yesterday: %s kWh | Recorder Delta: %s kWh",
        device.friendly_name,
        yesterday_api_val if yesterday_api_val is not None else "N/A",
        round(recorded_yesterday_delta, 3) if recorded_yesterday_delta is not None else "N/A",
    )

    if yesterday_api_val is not None and recorded_yesterday_delta is not None:
        if abs(recorded_yesterday_delta - yesterday_api_val) > 0.05:
            LOGGER.warning(
                "[%s] [Gating 1/3 Yesterday Check] MISMATCH (API: %s kWh vs Recorder: %s kWh). API overrides recorder -> triggering statistics rebuild",
                device.friendly_name,
                yesterday_api_val,
                round(recorded_yesterday_delta, 3),
            )
            await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
            return
        LOGGER.info("[%s] [Gating 1/3 Yesterday Check] PASSED (API and Recorder match)", device.friendly_name)

    # Fetch today's current running energy from MirAIe API
    today_str = dt_util.now().strftime("%d%m%Y")
    today_api_val = 0.0
    try:
        t_data = await hub.get_energy_consumption(device, ConsumptionPeriodType.DAILY, from_date=today_str)
        if t_data and today_str in t_data:
            today_api_val = max(0.0, float(t_data[today_str]))
    except Exception as e:
        LOGGER.debug("[%s] Could not fetch today's API energy: %s", device.friendly_name, e)

    # 3b. Weekly Verification
    last_sunday = get_last_sunday()
    weekly_str = last_sunday.strftime("%d%m%Y")
    weekly_api_val = None
    try:
        w_data = await hub.get_energy_consumption(device, ConsumptionPeriodType.WEEKLY, from_date=weekly_str)
        if w_data and weekly_str in w_data:
            weekly_api_val = float(w_data[weekly_str])
    except Exception as e:
        LOGGER.debug("[%s] Could not fetch weekly API energy: %s", device.friendly_name, e)

    recorded_weekly_sum = _extract_recorded_range_sum(entries, last_sunday, end_date)
    recorded_total_weekly = (recorded_weekly_sum + today_api_val) if recorded_weekly_sum is not None else None

    diff_weekly_no_today = abs(recorded_weekly_sum - weekly_api_val) if (weekly_api_val is not None and recorded_weekly_sum is not None) else 999.0
    diff_weekly_with_today = abs(recorded_total_weekly - weekly_api_val) if (weekly_api_val is not None and recorded_total_weekly is not None) else 999.0
    weekly_diff = min(diff_weekly_no_today, diff_weekly_with_today)

    LOGGER.info(
        "[%s] [Gating 2/3 Weekly Check] API Weekly Total: %s kWh | Recorder Stats Total (excl today %s kWh, incl today %s kWh): min diff=%s kWh",
        device.friendly_name,
        weekly_api_val if weekly_api_val is not None else "N/A",
        round(recorded_weekly_sum, 3) if recorded_weekly_sum is not None else "N/A",
        round(recorded_total_weekly, 3) if recorded_total_weekly is not None else "N/A",
        round(weekly_diff, 3) if weekly_diff != 999.0 else "N/A",
    )

    if weekly_api_val is not None and (recorded_weekly_sum is not None or recorded_total_weekly is not None):
        if weekly_diff > 0.15:
            LOGGER.warning(
                "[%s] [Gating 2/3 Weekly Check] MISMATCH (API: %s kWh vs Recorder: %s kWh / %s kWh) -> triggering statistics rebuild",
                device.friendly_name,
                weekly_api_val,
                round(recorded_weekly_sum, 3) if recorded_weekly_sum is not None else "N/A",
                round(recorded_total_weekly, 3) if recorded_total_weekly is not None else "N/A",
            )
            await _async_clear_statistics_helper(hass, statistic_id)
            await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
            return
        LOGGER.info("[%s] [Gating 2/3 Weekly Check] PASSED (API and Recorder match)", device.friendly_name)

    # 3c. Monthly Verification
    month_str = yesterday.strftime("%m%Y")
    monthly_api_val = None
    try:
        m_data = await hub.get_energy_consumption(device, ConsumptionPeriodType.MONTHLY, from_date=month_str)
        if m_data and month_str in m_data:
            monthly_api_val = float(m_data[month_str])
    except Exception as e:
        LOGGER.debug("[%s] Could not fetch monthly API energy: %s", device.friendly_name, e)

    start_of_month = yesterday.replace(day=1)
    recorded_monthly_sum = _extract_recorded_range_sum(entries, start_of_month, end_date)
    recorded_total_monthly = (recorded_monthly_sum + today_api_val) if recorded_monthly_sum is not None else None

    diff_monthly_no_today = abs(recorded_monthly_sum - monthly_api_val) if (monthly_api_val is not None and recorded_monthly_sum is not None) else 999.0
    diff_monthly_with_today = abs(recorded_total_monthly - monthly_api_val) if (monthly_api_val is not None and recorded_total_monthly is not None) else 999.0
    monthly_diff = min(diff_monthly_no_today, diff_monthly_with_today)

    LOGGER.info(
        "[%s] [Gating 3/3 Monthly Check] API Monthly Total: %s kWh | Recorder Stats Total (excl today %s kWh, incl today %s kWh): min diff=%s kWh",
        device.friendly_name,
        monthly_api_val if monthly_api_val is not None else "N/A",
        round(recorded_monthly_sum, 3) if recorded_monthly_sum is not None else "N/A",
        round(recorded_total_monthly, 3) if recorded_total_monthly is not None else "N/A",
        round(monthly_diff, 3) if monthly_diff != 999.0 else "N/A",
    )

    if monthly_api_val is not None and (recorded_monthly_sum is not None or recorded_total_monthly is not None):
        if monthly_diff > 0.2:
            LOGGER.warning(
                "[%s] [Gating 3/3 Monthly Check] MISMATCH (API: %s kWh vs Recorder Total: %s kWh / %s kWh) -> triggering statistics rebuild",
                device.friendly_name,
                monthly_api_val,
                round(recorded_monthly_sum, 3) if recorded_monthly_sum is not None else "N/A",
                round(recorded_total_monthly, 3) if recorded_total_monthly is not None else "N/A",
            )
            await _async_clear_statistics_helper(hass, statistic_id)
            await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
            return
        LOGGER.info("[%s] [Gating 3/3 Monthly Check] PASSED (API and Recorder match)", device.friendly_name)

    # All checks passed! Determine yesterday's baseline sum (ignoring any trailing 0.0 entries) and dispatch complete signal
    today_start_ts = _get_statistic_timestamp(dt_util.now().date()).timestamp()
    past_entries = [e for e in entries if float(e.get("start") or 0.0) <= today_start_ts and float(e.get("sum") or 0.0) > 0.0]
    valid_past_sums = [float(e.get("sum") or 0.0) for e in entries if float(e.get("sum") or 0.0) > 0.0]
    latest_entry_sum = float(past_entries[-1].get("sum") or 0.0) if past_entries else (valid_past_sums[-1] if valid_past_sums else float(entries[-1].get("sum") or 0.0))

    # Verify & update today's recorder statistics entry against today's API value
    expected_today_sum = round(latest_entry_sum + today_api_val, 2)
    now_dt = dt_util.as_utc(dt_util.now()).replace(minute=0, second=0, microsecond=0)
    today_entries = [e for e in entries if float(e.get("start") or 0.0) >= today_start_ts]

    if not today_entries:
        LOGGER.info("[%s] [Gating 4/4 Today Check] Today entry missing in DB; writing today's entry (%s kWh)", device.friendly_name, expected_today_sum)
        metadata = StatisticMetaData(
            has_sum=True,
            has_mean=False,
            mean_type=MEAN_TYPE_NONE,
            unit_class="energy",
            name=f"{device.friendly_name} Energy History",
            source="recorder",
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        async_import_statistics(hass, metadata, [StatisticData(start=now_dt, sum=expected_today_sum, state=expected_today_sum)])
    else:
        latest_today_sum = float(today_entries[-1].get("sum") or 0.0)
        if latest_today_sum < (latest_entry_sum - 0.5):
            LOGGER.warning(
                "[%s] Today's recorder statistics entry is corrupted in DB (sum %s < baseline %s); repairing statistics",
                device.friendly_name,
                latest_today_sum,
                latest_entry_sum,
            )
            await _async_rebuild_from_api(hass, hub, device, statistic_id, default_start_date, end_date, last_sum=0.0)
            return
        elif abs(latest_today_sum - expected_today_sum) >= 0.02:
            LOGGER.info(
                "[%s] [Gating 4/4 Today Check] Today's statistic entry differs from API (%s kWh vs expected %s kWh); updating today's entry",
                device.friendly_name,
                latest_today_sum,
                expected_today_sum,
            )
            metadata = StatisticMetaData(
                has_sum=True,
                has_mean=False,
                mean_type=MEAN_TYPE_NONE,
                unit_class="energy",
                name=f"{device.friendly_name} Energy History",
                source="recorder",
                statistic_id=statistic_id,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )
            async_import_statistics(hass, metadata, [StatisticData(start=now_dt, sum=expected_today_sum, state=expected_today_sum)])
        else:
            LOGGER.info("[%s] [Gating 4/4 Today Check] PASSED (Today statistic matches API: %s kWh)", device.friendly_name, latest_today_sum)

    LOGGER.info("[%s] All gating checks PASSED (Yesterday -> Weekly -> Monthly -> Today verified). No new backfill required (last_sum=%s kWh)", device.friendly_name, latest_entry_sum)
    setattr(device, "backfilled_energy_sum", latest_entry_sum)
    async_dispatcher_send(hass, f"miraie_backfill_complete_{device.id}")


async def _async_rebuild_from_api(
    hass: HomeAssistant,
    hub: MirAIeHub,
    device: MirAIeDevice,
    statistic_id: str,
    start_date: date,
    end_date: date,
    last_sum: float = 0.0,
) -> None:
    """Fetch daily energy from MirAIe API and build/import statistics points."""
    await _async_clear_statistics_helper(hass, statistic_id)
    daily = await hub.get_energy_consumption_full(
        device, ConsumptionPeriodType.DAILY, start_date, end_date
    )
    if not daily:
        LOGGER.info("Backfill: no daily data returned for %s", device.friendly_name)
        setattr(device, "backfilled_energy_sum", last_sum)
        async_dispatcher_send(hass, f"miraie_backfill_complete_{device.id}")
        return

    statistics = []
    running_sum = last_sum

    start_baseline_dt = _get_statistic_timestamp(start_date)
    statistics.append(StatisticData(start=start_baseline_dt, sum=last_sum, state=last_sum))

    first_day = last_day = None

    for key in sorted(daily.keys(), key=lambda k: datetime.strptime(k, "%d%m%Y").date()):
        day = datetime.strptime(key, "%d%m%Y").date()
        if day < start_date or day > end_date:
            continue
        value = daily.get(key)
        val_float = max(0.0, float(value or 0.0))
        running_sum += val_float
        end_dt = _get_statistic_timestamp(day + timedelta(days=1))
        statistics.append(StatisticData(start=end_dt, sum=round(running_sum, 2), state=round(running_sum, 2)))
        if first_day is None:
            first_day = day
        last_day = day

    # Append current running statistic point for today so recorder DB has correct cumulative sum
    today_val = 0.0
    try:
        today_key = dt_util.now().date().strftime("%d%m%Y")
        today_data = await hub.get_energy_consumption(device, ConsumptionPeriodType.DAILY, from_date=today_key)
        if today_data and today_key in today_data:
            today_val = max(0.0, float(today_data[today_key]))
    except Exception as e:
        LOGGER.debug("Could not fetch today's energy for statistics import: %s", e)

    now_dt = dt_util.as_utc(dt_util.now()).replace(minute=0, second=0, microsecond=0)
    current_total_sum = round(running_sum + today_val, 2)
    statistics.append(StatisticData(start=now_dt, sum=current_total_sum, state=current_total_sum))

    setattr(device, "backfilled_energy_sum", running_sum)

    metadata = StatisticMetaData(
        has_sum=True,
        has_mean=False,
        mean_type=MEAN_TYPE_NONE,
        unit_class="energy",
        name=f"{device.friendly_name} Energy History",
        source="recorder",
        statistic_id=statistic_id,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
    async_import_statistics(hass, metadata, statistics)
    LOGGER.info(
        "Backfill: imported %s daily points for %s (%s to %s)",
        len(statistics),
        device.friendly_name,
        first_day.isoformat() if first_day else start_date.isoformat(),
        last_day.isoformat() if last_day else end_date.isoformat(),
    )
    async_dispatcher_send(hass, f"miraie_backfill_complete_{device.id}")
