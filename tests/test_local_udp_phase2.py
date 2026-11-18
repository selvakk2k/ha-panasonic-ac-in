"""Test suite for Phase 2 Local UDP decoupled routing and fast IR verification."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.components.climate import HVACMode
from custom_components.miraie_in.coordinator import MirAIeDeviceCoordinator
from custom_components.miraie_in.climate import MirAIeClimate
from custom_components.miraie_in.utils import async_benchmark_transports


@pytest.mark.asyncio
async def test_decoupled_routing_udp_primary_ir_secondary_no_cloud_touch():
    """Verify that when Local UDP is primary and IR is secondary, Cloud MQTT is NEVER touched on UDP failure."""
    mock_device = MagicMock()
    mock_device.id = "test_ac_8c53"
    mock_device.supports_local_udp = True
    mock_device.udp_transport = MagicMock()
    mock_device.send_udp_command = AsyncMock(side_effect=RuntimeError("UDP Packet timeout"))
    mock_device.refresh = AsyncMock()

    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.entry_id = "entry_1"

    coord = MirAIeDeviceCoordinator(
        hass=mock_hass,
        entry_id="entry_1",
        device_id="test_ac_8c53",
        model_code="CS-HZ24XKE",
        has_wifi=True,
        blaster_entity_id="infrared.test_blaster",
        primary_backend="local_udp",
        secondary_backend="ir",
        tertiary_backend=None,
    )
    coord.device = mock_device
    coord.async_dispatch_ir_command = AsyncMock(return_value=True)

    climate = MirAIeClimate(device=mock_device, entry=mock_entry, coordinator=coord)

    # Cloud coro that should NEVER be awaited
    cloud_coro = AsyncMock()

    await climate._send_command_via_hybrid(
        mode="cool",
        temp=24,
        cloud_coro=cloud_coro,
    )

    # UDP should have been attempted
    mock_device.send_udp_command.assert_awaited_once()

    # IR Blaster should have been dispatched as secondary failover
    coord.async_dispatch_ir_command.assert_awaited_once_with(
        mode="cool",
        target_temp=24,
        fan=None,
        v_vane=None,
        h_vane=None,
        eco=None,
        nanoe=None,
        preset=None,
        origin="IR Failover (local_udp failed)",
    )

    # Crucial assertion: Cloud MQTT was NEVER touched
    cloud_coro.assert_not_awaited()
    mock_device.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_ir_verification_via_local_udp():
    """Verify that async_verify_physical_state_after_ir queries UDP directly for UDP-capable ACs."""
    mock_device = MagicMock()
    mock_device.id = "test_ac_8c53"
    mock_device.supports_local_udp = True
    mock_device.udp_transport = MagicMock()
    mock_device.send_udp_command = AsyncMock(return_value={"ps": "on", "actmp": "24"})
    mock_device.refresh = AsyncMock()

    coord = MirAIeDeviceCoordinator(
        hass=MagicMock(),
        entry_id="entry_1",
        device_id="test_ac_8c53",
        model_code="CS-HZ24XKE",
        has_wifi=True,
        blaster_entity_id="infrared.test_blaster",
        primary_backend="ir",
    )
    coord.device = mock_device

    with patch("asyncio.sleep", AsyncMock()):
        await coord.async_verify_physical_state_after_ir()

    # Fast ~20ms UDP probe query should be sent
    mock_device.send_udp_command.assert_awaited_once_with({"ps": "query"})
    # Cloud refresh should not be called when UDP succeeds
    mock_device.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_benchmark_transports_udp_and_mqtt():
    """Verify async_benchmark_transports measures latency for UDP and Cloud."""
    mock_transport = MagicMock()
    mock_transport.is_running = True
    mock_transport.register_device = MagicMock()
    mock_transport.send_command = AsyncMock(return_value={"ps": "on"})

    mock_coord = MagicMock()
    mock_device = MagicMock()
    mock_broker = MagicMock()
    mock_broker.is_connected = True
    mock_device.broker = mock_broker
    mock_device.refresh = AsyncMock()
    mock_coord.device = mock_device

    with patch("miraie_ac.transport.udp.LocalUdpTransportManager.get_instance", return_value=mock_transport):
        bench = await async_benchmark_transports(
            device_id="test_ac_8c53",
            ip_address="192.168.0.167",
            key="0" * 32,
            iv="0" * 32,
            coordinator=mock_coord,
        )

        assert bench["device_id"] == "test_ac_8c53"
        assert bench["local_udp"]["available"] is True
        assert bench["local_udp"]["latency_ms"] is not None
        assert bench["cloud_mqtt"]["available"] is True
        assert bench["cloud_mqtt"]["latency_ms"] is not None

