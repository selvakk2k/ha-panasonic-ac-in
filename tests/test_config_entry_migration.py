"""Unit test verifying Config Entry Migration from v1 to v2."""
import unittest
from unittest.mock import MagicMock, patch

from tests.ha_stub import setup_ha_stubs
setup_ha_stubs()


class TestConfigEntryMigration(unittest.IsolatedAsyncioTestCase):
    async def test_migrate_v1_cloud_entry(self):
        from custom_components.miraie_in import async_migrate_entry

        hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.version = 1
        mock_entry.entry_id = "test_entry_123"
        mock_entry.data = {"username": "selvakk@gmail.com", "password": "secret_password"}
        mock_entry.options = {
            "install_date": "2024-03-01",
            "blaster_entity_id": "infrared.room_2_blaster",
            "primary_backend": "cloud",
        }

        with patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
            res = await async_migrate_entry(hass, mock_entry)
            self.assertTrue(res)
            hass.config_entries.async_update_entry.assert_called_with(mock_entry, version=3)
            # Cloud entry without IP gets notification
            mock_notify.assert_called_once()
            self.assertEqual(mock_notify.call_args[1]["notification_id"], "panasonic_ac_udp_available_test_entry_123")

    async def test_migrate_v2_entry_already_configured_is_silent(self):
        from custom_components.miraie_in import async_migrate_entry

        hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.version = 2
        mock_entry.entry_id = "test_entry_v2"
        mock_entry.data = {"username": "selvakk@gmail.com", "password": "secret_password", "device_name": "Living Room AC"}
        mock_entry.options = {
            "primary_backend": "local_udp",
            "device_ip": "192.168.1.100",
            "udp_control_key": "aabbccddeeff11223344",
        }

        with patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
            res = await async_migrate_entry(hass, mock_entry)
            self.assertTrue(res)
            hass.config_entries.async_update_entry.assert_called_with(mock_entry, version=3)
            # Already configured: no notification
            mock_notify.assert_not_called()

    async def test_migrate_v2_entry_missing_ip_notifies(self):
        from custom_components.miraie_in import async_migrate_entry

        hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.version = 2
        mock_entry.entry_id = "test_entry_v2_needs_ip"
        mock_entry.data = {"username": "selvakk@gmail.com", "password": "secret_password", "device_name": "Bedroom AC"}
        mock_entry.options = {"primary_backend": "cloud"}

        with patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
            res = await async_migrate_entry(hass, mock_entry)
            self.assertTrue(res)
            hass.config_entries.async_update_entry.assert_called_with(mock_entry, version=3)
            mock_notify.assert_called_once()
            self.assertEqual(mock_notify.call_args[1]["notification_id"], "panasonic_ac_udp_available_test_entry_v2_needs_ip")
            self.assertIn("Bedroom AC", mock_notify.call_args[0][1])

    async def test_migrate_v1_ir_entry_is_silent(self):
        from custom_components.miraie_in import async_migrate_entry

        hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.version = 1
        mock_entry.entry_id = "test_ir_entry_456"
        mock_entry.data = {"is_ir_only": True, "name": "Living Room AC", "model_code": "CS-CU-RU18CKY-1"}
        mock_entry.options = {
            "blaster_entity_id": "infrared.living_room_blaster",
            "primary_backend": "ir",
        }

        with patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
            res = await async_migrate_entry(hass, mock_entry)
            self.assertTrue(res)
            hass.config_entries.async_update_entry.assert_called_with(mock_entry, version=3)
            # Pure IR entry: no UDP notification
            mock_notify.assert_not_called()

    async def test_multi_device_migration_isolation(self):
        """Test migrating multiple devices: only devices missing IP receive scoped notifications."""
        from custom_components.miraie_in import async_migrate_entry

        hass = MagicMock()

        # Device 1: Cloud entry, missing IP -> should notify
        dev1 = MagicMock()
        dev1.version = 2
        dev1.entry_id = "entry_bedroom"
        dev1.data = {"username": "user@test.com", "device_name": "Bedroom AC"}
        dev1.options = {"primary_backend": "cloud"}

        # Device 2: Cloud entry with UDP configured -> should be silent
        dev2 = MagicMock()
        dev2.version = 2
        dev2.entry_id = "entry_living"
        dev2.data = {"username": "user@test.com", "device_name": "Living AC"}
        dev2.options = {"device_ip": "192.168.1.55", "udp_control_key": "key123"}

        # Device 3: IR-only entry -> should be silent
        dev3 = MagicMock()
        dev3.version = 2
        dev3.entry_id = "entry_kitchen_ir"
        dev3.data = {"is_ir_only": True, "device_name": "Kitchen AC"}
        dev3.options = {"primary_backend": "ir"}

        with patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
            await async_migrate_entry(hass, dev1)
            await async_migrate_entry(hass, dev2)
            await async_migrate_entry(hass, dev3)

            # Exactly one notification created (for dev1 only)
            self.assertEqual(mock_notify.call_count, 1)
            call_kwargs = mock_notify.call_args[1]
            self.assertEqual(call_kwargs["notification_id"], "panasonic_ac_udp_available_entry_bedroom")
            self.assertIn("Bedroom AC", mock_notify.call_args[0][1])

    async def test_auto_split_preserves_install_date_and_options(self):
        from custom_components.miraie_in import async_setup_entry
        from unittest.mock import patch, AsyncMock
        import asyncio

        hass = MagicMock()
        hass.config_entries.async_entries.return_value = []

        mock_entry = MagicMock()
        mock_entry.entry_id = "legacy_parent_entry_789"
        mock_entry.version = 2
        mock_entry.data = {"username": "test@example.com", "password": "password"}
        mock_entry.options = {
            "install_date": "2024-01-15",
            "devices": {
                "dev_living": {
                    "install_date": "2023-11-01",
                    "blaster_entity_id": "remote.living_blaster",
                    "primary_backend": "ir",
                }
            }
        }

        mock_dev1 = MagicMock(id="dev_living", friendly_name="Living Room AC")
        mock_dev1.details = MagicMock(model_number="CS-CU-RU18CKY-1")
        mock_dev2 = MagicMock(id="dev_bedroom", friendly_name="Bedroom AC")
        mock_dev2.details = MagicMock(model_number="CS-CU-KN18YKY")

        mock_hub = MagicMock()
        mock_hub.init = AsyncMock(return_value=None)
        mock_hub.home = MagicMock(devices=[mock_dev1, mock_dev2])

        created_tasks = []
        def fake_create_task(coro):
            created_tasks.append(coro)

        hass.async_create_task.side_effect = fake_create_task
        hass.config_entries.flow.async_init = AsyncMock(return_value={"type": "create_entry"})
        hass.config_entries.async_remove = AsyncMock(return_value=None)

        with patch("custom_components.miraie_in.MirAIeHub", return_value=mock_hub), \
             patch("custom_components.miraie_in.MirAIeBroker", return_value=MagicMock()):
            res = await async_setup_entry(hass, mock_entry)
            self.assertTrue(res)

        # Run all scheduled tasks
        for t in created_tasks:
            if asyncio.iscoroutine(t):
                await t

        # Verify flow.async_init calls with preserved options
        calls = hass.config_entries.flow.async_init.call_args_list
        self.assertEqual(len(calls), 2)

        # First device: specific options
        self.assertEqual(calls[0][1]["data"]["device_id"], "dev_living")
        self.assertEqual(calls[0][1]["data"]["options"]["install_date"], "2023-11-01")
        self.assertEqual(calls[0][1]["data"]["options"]["blaster_entity_id"], "remote.living_blaster")
        self.assertEqual(calls[0][1]["data"]["options"]["primary_backend"], "ir")

        # Second device: inherits parent level install_date
        self.assertEqual(calls[1][1]["data"]["device_id"], "dev_bedroom")
        self.assertEqual(calls[1][1]["data"]["options"]["install_date"], "2024-01-15")

        # Verify parent entry was scheduled for removal
        hass.config_entries.async_remove.assert_called_with("legacy_parent_entry_789")

    async def test_auto_split_partial_failure_preserves_parent(self):
        from custom_components.miraie_in import async_setup_entry
        from unittest.mock import patch, AsyncMock
        import asyncio

        hass = MagicMock()
        hass.config_entries.async_entries.return_value = []

        mock_entry = MagicMock()
        mock_entry.entry_id = "legacy_parent_entry_fail"
        mock_entry.version = 2
        mock_entry.data = {"username": "test@example.com", "password": "password"}
        mock_entry.options = {}

        mock_dev1 = MagicMock(id="dev_1", friendly_name="AC 1")
        mock_dev1.details = MagicMock(model_number="CS-CU-RU18CKY-1")
        mock_dev2 = MagicMock(id="dev_2", friendly_name="AC 2")
        mock_dev2.details = MagicMock(model_number="CS-CU-KN18YKY")

        mock_hub = MagicMock()
        mock_hub.init = AsyncMock(return_value=None)
        mock_hub.home = MagicMock(devices=[mock_dev1, mock_dev2])

        hass.config_entries.async_remove = AsyncMock(return_value=None)

        # First device succeeds, second device fails
        side_effects = [{"type": "create_entry"}, Exception("Network timeout during import")]
        hass.config_entries.flow.async_init = AsyncMock(side_effect=side_effects)

        with patch("custom_components.miraie_in.MirAIeHub", return_value=mock_hub), \
             patch("custom_components.miraie_in.MirAIeBroker", return_value=MagicMock()):
            res = await async_setup_entry(hass, mock_entry)
            self.assertFalse(res)

        # Parent entry must NOT be removed on failure
        hass.config_entries.async_remove.assert_not_called()

    async def test_v1_to_v2_migration_with_orphan_buzzer_pruning(self):
        """Verify direct v1 to v2 migration prunes orphaned buzzer entities for unsupported models."""
        from custom_components.miraie_in.switch import async_setup_entry as async_setup_switch
        from custom_components.miraie_in.const import DOMAIN

        hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.data = {"device_id": "dev_migrated", "is_ir_only": False}

        mock_hub = MagicMock()
        mock_entry.runtime_data = mock_hub

        dev = MagicMock(id="dev_migrated", friendly_name="Migrated AC")
        dev.details = MagicMock(model_number="CS-CU-EU18CKY5XFM", brand="Panasonic", firmware_version="3.02")
        dev.status = MagicMock(is_online=True, buzzer=False)

        coord = MagicMock()
        coord.has_wifi = True
        coord.blaster_entity_id = None
        coord.capabilities = {"has_buzzer": False, "h_vane_enabled": 1}
        coord.state = {"buzzer": False}

        mock_hub.home = MagicMock(devices=[dev])
        mock_hub.coordinators = {"dev_migrated": coord}

        mock_ent_reg = MagicMock()
        # Simulate legacy entity in registry created under v1.x
        mock_ent_reg.async_get_entity_id.return_value = "switch.migrated_ac_buzzer"

        with patch("custom_components.miraie_in.switch.er.async_get", return_value=mock_ent_reg):
            entities = []
            await async_setup_switch(hass, mock_entry, lambda ents: entities.extend(ents))

            # Verify no buzzer switch was created
            buzzer_ents = [e for e in entities if e._attr_translation_key == "buzzer"]
            self.assertEqual(len(buzzer_ents), 0)

            # Verify orphaned buzzer switch from v1.x was pruned
            mock_ent_reg.async_remove.assert_called_with("switch.migrated_ac_buzzer")

    async def test_custom_entity_id_preserved_across_migrations_and_restarts(self):
        """Verify user-customized entity_ids are strictly preserved and never force-renamed across routine setup/migration passes."""
        from custom_components.miraie_in import _migrate_unique_ids
        from homeassistant.helpers import entity_registry as er
        from tests.ha_stub import MockHass, MockEntry

        hass = MockHass()
        mock_entry = MockEntry(
            entry_id="entry_bedroom_ac",
            data={"device_id": "dev_bedroom"},
            options={},
        )

        mock_dev = MagicMock(id="dev_bedroom", friendly_name="Room 2 AC")
        mock_hub = MagicMock()
        mock_hub.home = MagicMock(devices=[mock_dev])

        registry = er.async_get(hass)
        orig_entities = dict(registry.entities)
        orig_update_entity = registry.async_update_entity
        try:
            # Reset entities in mock registry for this test
            registry.entities.clear()

            # Simulate user having customized the entity_id in Home Assistant UI to 'climate.master_bedroom_cooling'
            climate_entry = registry.async_get_or_create(
                "climate",
                "miraie_in",
                "dev_bedroom",
                config_entry=mock_entry,
            )
            # Emulate custom entity_id assigned by user
            registry.entities.pop(climate_entry.entity_id)
            climate_entry.entity_id = "climate.master_bedroom_cooling"
            registry.entities[climate_entry.entity_id] = climate_entry

            # Also register a sensor with an old 1.x format unique_id that should have its unique_id migrated
            sensor_entry = registry.async_get_or_create(
                "sensor",
                "miraie_in",
                "sensor.room_2_ac_dev_bedroom_temperature",
                config_entry=mock_entry,
            )
            # User gave sensor a custom entity_id as well
            registry.entities.pop(sensor_entry.entity_id)
            sensor_entry.entity_id = "sensor.custom_ambient_temp"
            registry.entities[sensor_entry.entity_id] = sensor_entry

            # Wrap async_update_entity to spy on any forced renames
            update_calls = []

            def spy_update_entity(entity_id, **kwargs):
                update_calls.append((entity_id, kwargs))
                return orig_update_entity(entity_id, **kwargs)

            registry.async_update_entity = spy_update_entity

            # Pass 1: Initial startup / migration pass
            _migrate_unique_ids(hass, mock_entry, mock_hub)

            # 1. Custom climate entity_id must NOT be renamed to climate.room_2_ac
            self.assertEqual(climate_entry.entity_id, "climate.master_bedroom_cooling")
            self.assertIn("climate.master_bedroom_cooling", registry.entities)
            self.assertNotIn("climate.room_2_ac", registry.entities)

            # 2. Sensor unique_id was migrated, but custom entity_id was strictly preserved
            self.assertEqual(sensor_entry.unique_id, "dev_bedroom_temperature")
            self.assertEqual(sensor_entry.entity_id, "sensor.custom_ambient_temp")

            # 3. Verify async_update_entity was NEVER called with new_entity_id
            for eid, kwargs in update_calls:
                self.assertNotIn("new_entity_id", kwargs, f"Forced rename detected on entity {eid}")

            update_calls.clear()

            # Pass 2: Subsequent routine restart
            _migrate_unique_ids(hass, mock_entry, mock_hub)

            # Verify idempotency: entity IDs preserved, zero updates triggered
            self.assertEqual(climate_entry.entity_id, "climate.master_bedroom_cooling")
            self.assertEqual(sensor_entry.entity_id, "sensor.custom_ambient_temp")
            self.assertEqual(len(update_calls), 0)
        finally:
            registry.entities = orig_entities
            registry.async_update_entity = orig_update_entity


    async def test_options_flow_multi_device_udp_isolation(self):
        """Test Options Flow device settings preserves other devices when updating UDP."""
        from custom_components.miraie_in.config_flow import OptionsFlowHandler

        hass = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_multi"
        mock_entry.data = {"username": "user@test.com", "device_id": "dev_1", "device_name": "Living AC"}
        mock_entry.options = {
            "devices": {
                "dev_1": {"device_ip": "192.168.1.10", "primary_backend": "cloud"},
                "dev_2": {"device_ip": "192.168.1.20", "primary_backend": "local_udp", "udp_control_key": "secret2"},
            }
        }

        flow = OptionsFlowHandler(mock_entry)
        flow.hass = hass

        from datetime import date, timedelta
        user_input = {
            "install_date": (date.today() - timedelta(days=60)).isoformat(),
            "device_ip": "192.168.1.15",
            "udp_control_key": "aabbccddeeff00112233aabbccddeeff00112233aabbccddeeff00112233aabbccdd",
            "udp_control_iv": "aabbccddeeff00112233aabbccddeeff",
            "primary_backend": "local_udp",
            "secondary_backend": "none",
            "hybrid_submode": "auto",
        }

        result = await flow.async_step_device_settings(user_input)
        self.assertEqual(result["type"], "create_entry")
        new_opts = result["data"]

        # dev_1 updated
        self.assertEqual(new_opts["devices"]["dev_1"]["device_ip"], "192.168.1.15")
        self.assertEqual(new_opts["devices"]["dev_1"]["primary_backend"], "local_udp")

        # dev_2 completely preserved without modification or pollution
        self.assertEqual(new_opts["devices"]["dev_2"]["device_ip"], "192.168.1.20")
        self.assertEqual(new_opts["devices"]["dev_2"]["primary_backend"], "local_udp")
        self.assertEqual(new_opts["devices"]["dev_2"]["udp_control_key"], "secret2")


if __name__ == "__main__":
    unittest.main()
