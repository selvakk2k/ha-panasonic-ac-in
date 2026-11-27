# Matter AC Setup (Matter-Only & Hybrid)

This mode wraps a Panasonic AC already paired through Home Assistant's Matter integration into a unified `climate` entity.

## What It Fixes Automatically
- **Unified Control**: Merges the split Matter `climate`, `switch.*_power`, `fan`, and `sensor.*_outdoor_temperature` entities (matched by `device_id`) into one `climate` entity.
- **Indoor Temperature Fix**: Decodes Panasonic's packed `LocalTemperature` (`61.3` -> `30.24 C`).
- **Setpoint Bounds**: Enforces realistic `16-30 C` limits instead of Matter's `7-35 C` default.

## Notes
- Keep the original Matter entities **enabled** in Home Assistant (you can mark them **Hidden** from dashboards).
- In **Matter-Only** mode, non-Matter controls (`Dry`, `Fan Only`, `Auto`, Swings, `Nanoe`, `Display`, `Coil Clean`, `Converti`) are hidden.
- In **Hybrid** mode, unsupported modes and features automatically route to your secondary connection method, or grey out if Matter is the only connection currently online.

