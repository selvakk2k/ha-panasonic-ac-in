import unittest
from datetime import date
from unittest.mock import MagicMock

from custom_components.miraie_in.utils import (
    get_transport_capabilities,
    parse_packed_matter_temp,
)
from custom_components.miraie_in.sensor import MirAIeTodayEnergySensor


class TestV3MatterAndBlended(unittest.TestCase):
    def test_parse_packed_matter_temp(self):
        self.assertAlmostEqual(parse_packed_matter_temp(61.3), 30.24, places=2)
        self.assertAlmostEqual(parse_packed_matter_temp(28.5), 28.5, places=2)
        self.assertIsNone(parse_packed_matter_temp(None))

    def test_transport_capabilities_matter_only_and_hybrid(self):
        dev = MagicMock(supports_local_udp=False, udp_transport=None)
        coord = MagicMock()
        coord.setup_mode = "matter"
        coord.primary_backend = "matter"
        coord.secondary_backend = "none"
        coord.has_wifi = False
        coord.blaster_entity_id = None
        coord.matter_entity_id = "climate.room_2_cs_cu_eu18cky5xfm_2"
        coord.has_cloud = False
        coord.hybrid_tier = "offline"
        coord.hub = None
        coord.hass = MagicMock()
        coord.hass.states.get.return_value = MagicMock(state="cool")

        caps = get_transport_capabilities(dev, coord)
        self.assertTrue(caps["is_matter_only_configured"])
        self.assertTrue(caps["is_matter_only_online"])
        self.assertFalse(caps["supports_full_miraie"])
        self.assertFalse(caps["non_matter_available"])

        # Hybrid Matter + IR when IR is offline -> Matter is only online transport
        coord.setup_mode = "hybrid"
        coord.secondary_backend = "ir"
        coord.blaster_entity_id = "remote.room_2_ir"
        coord.is_ir_blaster_available = False
        caps_failover = get_transport_capabilities(dev, coord)
        self.assertFalse(caps_failover["is_matter_only_configured"])
        self.assertTrue(caps_failover["is_matter_only_online"])
        self.assertTrue(caps_failover["supports_full_miraie"])
        self.assertFalse(caps_failover["non_matter_available"])

    def test_blended_today_energy_real_time_plus_cloud_anchor(self):
        hub = MagicMock()
        dev = MagicMock()
        dev.id = "dev_1"
        dev.friendly_name = "Room 2 AC"
        dev.status = MagicMock(estimated_energy_kwh=0.10)
        sensor = MirAIeTodayEnergySensor(hub, dev)
        sensor._anchor_date = date.today()
        sensor._cloud_anchor_kwh = 0.50
        sensor._est_at_anchor_kwh = 0.10

        # Estimator advances by +0.12 kWh between Cloud polls
        dev.status.estimated_energy_kwh = 0.22
        self.assertAlmostEqual(sensor.native_value, 0.62, places=3)

    def test_four_way_model_preserves_h_vane_with_list_capabilities(self):
        from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator

        coord = MirAIeDeviceCoordinator(
            hass=MagicMock(),
            entry_id="e1",
            device_id="d1",
            model_code="CS-CU-EU18CKY5XFM",
        )
        self.assertEqual(coord.capabilities.get("h_vane_enabled"), 1)
        coord.update_model_capabilities(
            {"controlCapabilities": [{"capability": "BuzzerControl"}]}
        )
        self.assertEqual(coord.capabilities.get("h_vane_enabled"), 1)
        self.assertTrue(coord.capabilities.get("has_buzzer"))

    def test_v3_hierarchical_setup_menus(self):
        import asyncio
        from custom_components.miraie_in.config_flow import ConfigFlow

        async def _run():
            flow = ConfigFlow()
            r1 = await flow.async_step_user()
            self.assertEqual(r1["type"], "menu")
            self.assertEqual(r1["menu_options"], ["cloud", "local_only", "hybrid_menu"])

            r2 = await flow.async_step_local_only()
            self.assertEqual(r2["menu_options"], ["local_udp", "ir_device", "matter"])

            r3 = await flow.async_step_hybrid_menu()
            self.assertEqual(r3["menu_options"], ["hybrid", "hybrid_local_menu"])

            r4 = await flow.async_step_hybrid_local_menu()
            self.assertEqual(r4["menu_options"], ["hybrid_local_auto", "hybrid_offline"])

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

