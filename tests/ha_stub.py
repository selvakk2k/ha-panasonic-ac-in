# type: ignore
# pyrefly: ignore-errors[all]
"""Dynamic Home Assistant module stub loader for standalone unit testing."""

import sys
import types
import asyncio
from enum import Enum, IntFlag
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

LOCAL_MIRAIE_AC = Path("/home/skk/Documents/GitHub/miraie-ac")
LOCAL_PANASONIC_MODELS = Path("/home/skk/Documents/GitHub/panasonic-ac-models")

if LOCAL_MIRAIE_AC.exists():
    if str(LOCAL_MIRAIE_AC) in sys.path:
        sys.path.remove(str(LOCAL_MIRAIE_AC))
    sys.path.insert(0, str(LOCAL_MIRAIE_AC))

if LOCAL_PANASONIC_MODELS.exists():
    if str(LOCAL_PANASONIC_MODELS) in sys.path:
        sys.path.remove(str(LOCAL_PANASONIC_MODELS))
    sys.path.insert(0, str(LOCAL_PANASONIC_MODELS))



class _HADynamicModule(types.ModuleType):
    """Module that returns dummy objects for unknown attributes while allowing custom getattr overrides."""

    def __getattr__(self, name):
        if self.__name__ == "homeassistant.components.climate" and name in (
            "PRECISION_HALVES",
            "PRECISION_WHOLE",
            "PRECISION_TENTHS",
        ):
            raise AttributeError(f"module 'homeassistant.components.climate' has no attribute '{name}'")

        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        dummy = type(name, (), {})
        setattr(self, name, dummy)
        return dummy


def setup_ha_stubs():
    if LOCAL_MIRAIE_AC.exists():
        if str(LOCAL_MIRAIE_AC) in sys.path:
            sys.path.remove(str(LOCAL_MIRAIE_AC))
        sys.path.insert(0, str(LOCAL_MIRAIE_AC))

    try:
        import panasonic_ac_models
    except ImportError:
        pan_mod = types.ModuleType("panasonic_ac_models")
        class ACModelLookup:
            @classmethod
            def is_cooling_only(cls, model):
                m = str(model)
                return False if ("EZ" in m or "KZ" in m) else True
            @classmethod
            def get_capabilities(cls, model):
                m = str(model)
                has_heat = 1 if ("EZ" in m or "KZ" in m) else 0
                has_nanoe = 1 if ("XU" in m or "HU" in m) else 0
                has_wifi = 0 if ("-1" in m or "-2" in m or "KN" in m or "CW" in m) else 1
                converti_type = "8-in-1" if ("BKY" in m or "CKY" in m) else ("7-in-1" if "AKY" in m or "NU" in m else "none")
                return {
                    "has_wifi": has_wifi,
                    "has_heat_mode": has_heat,
                    "has_nanoe": has_nanoe,
                    "converti_type": converti_type,
                }
        pan_mod.ACModelLookup = ACModelLookup
        sys.modules["panasonic_ac_models"] = pan_mod

    try:
        import voluptuous
    except ImportError:
        vol = types.ModuleType("voluptuous")
        class MockHass:
            def __init__(self):
                self.data = {}
                self.is_running: bool = True
                self.config_entries = MagicMock()
                self.services = MagicMock()
                self.services.async_call = AsyncMock()
                self.states = MagicMock()
                
                async def _dummy_task(coro):
                    pass
                self.async_create_task = MagicMock(side_effect=_dummy_task)
                
                async def _dummy_exec(func, *args, **kwargs):
                    return func(*args, **kwargs)
                self.async_add_executor_job = AsyncMock(side_effect=_dummy_exec)
        class Schema:
            def __init__(self, schema):
                self.schema = schema
        def Required(key, default=None):
            return key
        def Optional(key, default=None):
            return key
        vol.Schema = Schema
        vol.Required = Required
        vol.Optional = Optional
        sys.modules["voluptuous"] = vol

    if "homeassistant" in sys.modules:
        return

    # Install sys.meta_path finder for homeassistant.*
    class HAModuleFinder:
        def find_spec(self, fullname, path, target=None):
            if fullname in (
                "homeassistant.components.climate.PRECISION_HALVES",
                "homeassistant.components.climate.PRECISION_WHOLE",
                "homeassistant.components.climate.PRECISION_TENTHS",
            ):
                return None
            if fullname.startswith("homeassistant"):
                from importlib.machinery import ModuleSpec
                return ModuleSpec(fullname, HAModuleLoader(), is_package=True)
            return None

    class HAModuleLoader:
        def create_module(self, spec):
            mod = _HADynamicModule(spec.name)
            mod.__path__ = []
            return mod

        def exec_module(self, module):
            pass

    sys.meta_path.insert(0, HAModuleFinder())

    # Pre-populate specific strict symbol locations
    import homeassistant.const
    import homeassistant.components.climate
    import homeassistant.config_entries
    class ConfigEntry:
        pass
    class ConfigFlow:
        def async_show_menu(self, step_id, menu_options, description_placeholders=None):
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options}
        def add_suggested_values_to_schema(self, data_schema, suggested_values):
            return data_schema
        def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors, "description_placeholders": description_placeholders}
        def async_create_entry(self, title="", data=None, options=None):
            return {"type": "create_entry", "title": title, "data": data, "options": options}
        def async_abort(self, reason=""):
            return {"type": "abort", "reason": reason}
        async def async_set_unique_id(self, unique_id=None, raise_on_progress=True):
            self.unique_id = unique_id
        def _abort_if_unique_id_configured(self):
            pass
        @classmethod
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    class OptionsFlow:
        def __init__(self, config_entry=None):
            self.config_entry = config_entry
        def add_suggested_values_to_schema(self, data_schema, suggested_values):
            return data_schema
        def async_show_menu(self, step_id, menu_options, description_placeholders=None):
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options}
        def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors, "description_placeholders": description_placeholders}
        def async_create_entry(self, title="", data=None):
            return {"type": "create_entry", "title": title, "data": data}

    class ConfigFlowResult:
        pass
    homeassistant.config_entries.ConfigEntry = ConfigEntry
    homeassistant.config_entries.ConfigFlow = ConfigFlow
    homeassistant.config_entries.OptionsFlow = OptionsFlow
    homeassistant.config_entries.ConfigFlowResult = ConfigFlowResult
    import homeassistant.core
    homeassistant.core.callback = lambda func: func

    import homeassistant.exceptions

    class HomeAssistantError(Exception):
        pass
    class ConfigEntryNotReady(HomeAssistantError):
        pass
    class ConfigEntryAuthFailed(HomeAssistantError):
        pass
    class ConfigEntryError(HomeAssistantError):
        pass
    homeassistant.exceptions.HomeAssistantError = HomeAssistantError
    homeassistant.exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    homeassistant.exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    homeassistant.exceptions.ConfigEntryError = ConfigEntryError

    import homeassistant.components.sensor

    # homeassistant.const symbols
    class UnitOfTemperature(Enum):
        CELSIUS = "°C"
        FAHRENHEIT = "°F"

    class Platform(Enum):
        CLIMATE = "climate"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        SWITCH = "switch"

    class UnitOfPower(Enum):
        KILO_WATT = "kW"
        WATT = "W"

    class UnitOfEnergy(Enum):
        KILO_WATT_HOUR = "kWh"

    class UnitOfElectricPotential(Enum):
        VOLT = "V"

    class UnitOfElectricCurrent(Enum):
        AMPERE = "A"

    homeassistant.const.UnitOfTemperature = UnitOfTemperature
    homeassistant.const.Platform = Platform
    homeassistant.const.UnitOfPower = UnitOfPower
    homeassistant.const.UnitOfEnergy = UnitOfEnergy
    homeassistant.const.UnitOfElectricPotential = UnitOfElectricPotential
    homeassistant.const.UnitOfElectricCurrent = UnitOfElectricCurrent
    homeassistant.const.PRECISION_WHOLE = 1.0
    homeassistant.const.PRECISION_HALVES = 0.5
    homeassistant.const.PRECISION_TENTHS = 0.1
    homeassistant.const.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"
    homeassistant.const.PERCENTAGE = "%"
    homeassistant.const.STATE_UNAVAILABLE = "unavailable"
    homeassistant.const.STATE_UNKNOWN = "unknown"
    homeassistant.const.STATE_ON = "on"
    homeassistant.const.STATE_OFF = "off"

    # homeassistant.components.climate symbols
    class ClimateEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        FAN_MODE = 2
        PRESET_MODE = 4
        SWING_MODE = 8
        TURN_OFF = 16
        TURN_ON = 32
        SWING_HORIZONTAL_MODE = 64

    class HVACMode(str, Enum):
        AUTO = "auto"
        COOL = "cool"
        HEAT = "heat"
        OFF = "off"
        DRY = "dry"
        FAN_ONLY = "fan_only"

    homeassistant.components.climate.ClimateEntityFeature = ClimateEntityFeature
    homeassistant.components.climate.HVACMode = HVACMode
    homeassistant.components.climate.PRESET_ECO = "eco"
    homeassistant.components.climate.PRESET_BOOST = "boost"
    homeassistant.components.climate.PRESET_NONE = "none"
    homeassistant.components.climate.FAN_AUTO = "auto"
    homeassistant.components.climate.FAN_LOW = "low"
    homeassistant.components.climate.FAN_MEDIUM = "medium"
    homeassistant.components.climate.FAN_HIGH = "high"
    homeassistant.components.climate.FAN_OFF = "off"

    # Sensor symbols
    class SensorStateClass(Enum):
        MEASUREMENT = "measurement"
        TOTAL_INCREASING = "total_increasing"
        TOTAL = "total"

    class SensorDeviceClass(Enum):
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        ENERGY = "energy"
        POWER = "power"
        VOLTAGE = "voltage"
        CURRENT = "current"
        SIGNAL_STRENGTH = "signal_strength"

    homeassistant.components.sensor.SensorStateClass = SensorStateClass
    homeassistant.components.sensor.SensorDeviceClass = SensorDeviceClass

    class BinarySensorDeviceClass(Enum):
        PROBLEM = "problem"
        CONNECTIVITY = "connectivity"
        RUNNING = "running"

    homeassistant.components.binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass

    # Recorder symbols
    import homeassistant.components.recorder.statistics
    import homeassistant.components.recorder.models
    class StatisticMeanType(Enum):
        NONE = 0
    class StatisticData(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__dict__.update(kwargs)
    class StatisticMetaData(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__dict__.update(kwargs)
    homeassistant.components.recorder.statistics.StatisticMeanType = StatisticMeanType
    homeassistant.components.recorder.models.StatisticData = StatisticData
    homeassistant.components.recorder.models.StatisticMetaData = StatisticMetaData
    homeassistant.components.recorder.statistics.StatisticData = StatisticData
    homeassistant.components.recorder.statistics.StatisticMetaData = StatisticMetaData
    homeassistant.components.recorder.statistics.async_import_statistics = lambda hass, metadata, statistics: None
    homeassistant.components.recorder.statistics.clear_statistics = lambda instance, statistic_ids: None

    class MockRecorderInstance:
        def async_clear_statistics(self, statistic_ids):
            pass
        async def async_block_till_done(self):
            pass
        async def async_add_executor_job(self, target, *args):
            return target(*args) if callable(target) else None

    _mock_rec = MockRecorderInstance()
    homeassistant.components.recorder.get_instance = lambda *args, **kwargs: _mock_rec
    homeassistant.components.recorder.statistics.get_last_statistics = lambda hass, count, stat_id, *args, **kwargs: {stat_id: [{"start": 0.0, "sum": 0.0}]}
    try:
        import homeassistant.helpers.recorder
        homeassistant.helpers.recorder.get_instance = lambda *args, **kwargs: _mock_rec
    except Exception:
        pass

    # Selector helper stubs
    import homeassistant.helpers.selector as selector_mod
    class SelectorBase(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(**kwargs)
        def __call__(self, v):
            return v
    class SelectOptionDict(dict):
        def __init__(self, value="", label=""):
            super().__init__(value=value, label=label)
            self.value = value
            self.label = label
    class SelectSelector(SelectorBase):
        pass
    class SelectSelectorConfig(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__dict__.update(kwargs)
    class SelectSelectorMode(Enum):
        DROPDOWN = "dropdown"
        LIST = "list"
    class BooleanSelector(SelectorBase):
        pass
    class EntitySelector(SelectorBase):
        pass
    class EntitySelectorConfig(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__dict__.update(kwargs)
    class DateSelector(SelectorBase):
        pass

    selector_mod.SelectOptionDict = SelectOptionDict
    selector_mod.SelectSelector = SelectSelector
    selector_mod.SelectSelectorConfig = SelectSelectorConfig
    selector_mod.SelectSelectorMode = SelectSelectorMode
    selector_mod.BooleanSelector = BooleanSelector
    selector_mod.EntitySelector = EntitySelector
    selector_mod.EntitySelectorConfig = EntitySelectorConfig
    selector_mod.DateSelector = DateSelector

    # MQTT helper stubs
    import homeassistant.components.mqtt as mqtt_mod
    import homeassistant.components.mqtt.const as mqtt_const_mod
    mqtt_const_mod.MQTT_CONNECTION_STATE = "mqtt_connection_state"
    mqtt_mod.is_connected = lambda hass: True
    mqtt_subscriptions = {}
    async def _async_subscribe_stub(hass, topic, msg_callback, *args, **kwargs):
        mqtt_subscriptions[topic] = msg_callback
        return lambda: mqtt_subscriptions.pop(topic, None)
    mqtt_mod.async_subscribe = _async_subscribe_stub
    mqtt_mod.subscriptions = mqtt_subscriptions


    # Event helper stubs
    import homeassistant.helpers.event
    homeassistant.helpers.event.async_track_time_change = lambda *args, **kwargs: (lambda: None)
    homeassistant.helpers.event.async_track_time_interval = lambda *args, **kwargs: (lambda: None)
    homeassistant.helpers.event.async_track_state_change_event = lambda *args, **kwargs: (lambda: None)

    # Entity helper stubs
    import homeassistant.helpers.entity
    import homeassistant.const
    class EntityCategory(Enum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"
    homeassistant.helpers.entity.EntityCategory = EntityCategory
    homeassistant.const.EntityCategory = EntityCategory

    # Dispatcher helper stubs
    import homeassistant.helpers.dispatcher
    homeassistant.helpers.dispatcher.async_dispatcher_send = lambda *args, **kwargs: None
    homeassistant.helpers.dispatcher.async_dispatcher_connect = lambda *args, **kwargs: (lambda: None)

    # dt_util helper stub
    import homeassistant.util.dt as dt_util
    from datetime import datetime, timezone
    dt_util.now = lambda: datetime.now(timezone.utc)
    dt_util.start_of_local_day = lambda dt: dt.replace(hour=0, minute=0, second=0, microsecond=0)
    dt_util.as_utc = lambda dt: dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    dt_util.as_local = lambda dt: dt

    # aiohttp_client helper symbol
    import homeassistant.helpers.aiohttp_client
    homeassistant.helpers.aiohttp_client.async_get_clientsession = lambda hass=None: None

    # issue_registry helper symbol
    import homeassistant.helpers.issue_registry
    class IssueSeverity(Enum):
        WARNING = "warning"
        CRITICAL = "critical"
    homeassistant.helpers.issue_registry.IssueSeverity = IssueSeverity
    homeassistant.helpers.issue_registry.async_create_issue = lambda *args, **kwargs: None

    # Infrared helper stubs
    ir_mod = types.ModuleType("homeassistant.components.infrared")
    ir_helpers_mod = types.ModuleType("homeassistant.components.infrared.helpers")

    async def _async_send_command_stub(hass, entity_id, command):
        return True
    def _async_subscribe_receiver_stub(hass, entity_id, callback):
        return lambda: None
    ir_helpers_mod.async_send_command = _async_send_command_stub
    ir_helpers_mod.async_subscribe_receiver = _async_subscribe_receiver_stub
    ir_mod.helpers = ir_helpers_mod
    sys.modules["homeassistant.components.infrared"] = ir_mod
    sys.modules["homeassistant.components.infrared.helpers"] = ir_helpers_mod

    # Binary sensor helper stubs
    import homeassistant.components.binary_sensor as bin_sensor_mod
    class BinarySensorDeviceClass(Enum):
        PROBLEM = "problem"
        CONNECTIVITY = "connectivity"
        RUNNING = "running"
    bin_sensor_mod.BinarySensorDeviceClass = BinarySensorDeviceClass
    class BinarySensorEntity:
        def __init__(self):
            pass
    bin_sensor_mod.BinarySensorEntity = BinarySensorEntity

    # Diagnostics helper stubs
    import homeassistant.components.diagnostics as diag_mod
    def async_redact_data(data, to_redact):
        if isinstance(data, dict):
            return {
                k: "**REDACTED**" if k in to_redact else async_redact_data(v, to_redact)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [async_redact_data(item, to_redact) for item in data]
        return data
    diag_mod.async_redact_data = async_redact_data

    # restore_state helper stub
    import homeassistant.helpers.restore_state
    class RestoreEntity:
        async def async_added_to_hass(self):
            pass
    homeassistant.helpers.restore_state.RestoreEntity = RestoreEntity

    # entity_registry helper stub
    import homeassistant.helpers.entity_registry as entity_registry_mod
    class MockRegistryEntry:
        def __init__(self, domain, platform, unique_id, config_entry=None, entity_id=None):
            self.domain = domain
            self.platform = platform
            self.unique_id = unique_id
            self.config_entry_id = getattr(config_entry, "entry_id", config_entry)
            self.entity_id = entity_id or f"{domain}.{unique_id}"

    class MockEntityRegistry:
        def __init__(self):
            self.entities = {}

        def async_get_or_create(self, domain, platform, unique_id, *, config_entry=None):
            for e in self.entities.values():
                if e.domain == domain and e.platform == platform and e.unique_id == unique_id:
                    return e
            e = MockRegistryEntry(domain, platform, unique_id, config_entry)
            self.entities[e.entity_id] = e
            return e

        def async_get_entity_id(self, domain, platform, unique_id):
            for e in self.entities.values():
                if e.domain == domain and e.platform == platform and e.unique_id == unique_id:
                    return e.entity_id
            return None

        def async_entries_for_config_entry(self, entry_id):
            return [e for e in self.entities.values() if e.config_entry_id == entry_id]

        def async_remove(self, entity_id):
            self.entities.pop(entity_id, None)

        def async_is_registered(self, entity_id):
            return entity_id in self.entities

        def async_update_entity(self, entity_id, *, new_unique_id=None, new_entity_id=None, **kwargs):
            if entity_id in self.entities:
                entry = self.entities[entity_id]
                if new_unique_id:
                    entry.unique_id = new_unique_id
                if new_entity_id:
                    self.entities.pop(entity_id, None)
                    entry.entity_id = new_entity_id
                    self.entities[new_entity_id] = entry

    _mock_ent_reg = MockEntityRegistry()
    entity_registry_mod.async_get = lambda hass: _mock_ent_reg
    entity_registry_mod.async_entries_for_config_entry = lambda reg, entry_id: reg.async_entries_for_config_entry(entry_id)

    # device_registry helper stub
    import homeassistant.helpers.device_registry as device_registry_mod
    class MockDeviceEntry:
        def __init__(self, id, identifiers=None, config_entries=None, name=None, manufacturer=None, model=None, sw_version=None):
            self.id = id
            self.identifiers = set(identifiers or [])
            self.config_entries = set(config_entries or [])
            self.name = name
            self.manufacturer = manufacturer
            self.model = model
            self.sw_version = sw_version

    class MockDeviceRegistry:
        def __init__(self):
            self.devices = {}

        def async_get_or_create(self, *, config_entry_id=None, identifiers=None, **kwargs):
            idents = set(identifiers or [])
            for dev in self.devices.values():
                if dev.identifiers.intersection(idents):
                    if config_entry_id:
                        dev.config_entries.add(config_entry_id)
                    return dev
            dev_id = f"device_entry_{len(self.devices) + 1}"
            dev = MockDeviceEntry(
                id=dev_id,
                identifiers=idents,
                config_entries={config_entry_id} if config_entry_id else set(),
                **kwargs,
            )
            self.devices[dev_id] = dev
            return dev

        def async_get_device(self, identifiers=None):
            idents = set(identifiers or [])
            for dev in self.devices.values():
                if dev.identifiers.intersection(idents):
                    return dev
            return None

        def async_entries_for_config_entry(self, config_entry_id):
            return [d for d in self.devices.values() if config_entry_id in d.config_entries]

        def async_update_device(self, device_id, *, remove_config_entry_id=None, **kwargs):
            if device_id in self.devices:
                dev = self.devices[device_id]
                if remove_config_entry_id:
                    dev.config_entries.discard(remove_config_entry_id)

        def async_remove_device(self, device_id):
            self.devices.pop(device_id, None)

    _mock_dev_reg = MockDeviceRegistry()
    device_registry_mod.async_get = lambda hass: _mock_dev_reg
    device_registry_mod.async_entries_for_config_entry = lambda reg, entry_id: reg.async_entries_for_config_entry(entry_id)



    # Ensure climate module dict strictly lacks precision constants
    for const_name in ("PRECISION_HALVES", "PRECISION_WHOLE", "PRECISION_TENTHS"):
        if const_name in homeassistant.components.climate.__dict__:
            del homeassistant.components.climate.__dict__[const_name]


class _MockEntryImpl:
    def __init__(self, entry_id, data=None, options=None, title="MirAIe AC"):
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
        self.title = title
        self.unique_id = data.get("device_id") if data else entry_id

    def async_on_unload(self, listener):
        pass

    def add_update_listener(self, listener):
        return lambda: None


class MockStates(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _MockHassImpl:
    def __init__(self):
        self.data = {}
        self.is_running: bool = True
        self.config_entries = MagicMock()
        self.config_entries.async_entries = MagicMock(return_value=[])
        self.config_entries.async_get_entry = MagicMock(return_value=None)
        self.config_entries.async_update_entry = MagicMock()
        self.services = MagicMock()
        self.services.async_call = AsyncMock()
        self.states = MockStates()
        self.bus = MagicMock()
        self.bus.async_listen_once = MagicMock()

        def _dummy_task(coro):
            try:
                loop = asyncio.get_running_loop()
                return loop.create_task(coro)
            except RuntimeError:
                return None
        self.async_create_task = MagicMock(side_effect=_dummy_task)

        async def _dummy_exec(func, *args, **kwargs):
            return func(*args, **kwargs)
        self.async_add_executor_job = AsyncMock(side_effect=_dummy_exec)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    MockEntry: Any = homeassistant.config_entries.ConfigEntry
    MockHass: Any = homeassistant.core.HomeAssistant
else:
    MockEntry = _MockEntryImpl
    MockHass = _MockHassImpl


# Auto-install Home Assistant stubs on import
setup_ha_stubs()
