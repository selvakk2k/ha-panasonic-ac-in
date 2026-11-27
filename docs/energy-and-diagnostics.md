# Blended Energy & Deep Diagnostics

## Blended Energy Pipeline
- **Live Power (`W`)**: Real-time wattage calculated from per-model BEE star-label profiles (`panasonic-ac-models`), `/diag` compressor frequency (`0xC2`), and room-temperature thermodynamics.
- **Today's Energy (`kWh`)**: Integrates live in real time between 30-minute Cloud polls (or while offline) and re-anchors to MirAIe Cloud's reported daily total when online.
- **Energy History (`kWh`)**: `total_increasing` sensor for Home Assistant's Energy Dashboard, compatible with **Rebuild Energy Statistics** and **Verify Energy Statistics**.

## Deep Diagnostics Sensors
To keep devices clean, deep `/diag` sensors (`Compressor Frequency`, `Evaporator Coil Temperature`, `Outdoor Condenser Temperature`, `Indoor Fan Stage`, `AC Voltage`, `AC Current`), `Model Capabilities`, and legacy period sensors (`Yesterday`, `Weekly`, `Monthly`) are **disabled by default**.

You can enable individual sensors anytime from the entity's **Settings** dialog, or toggle **Enable Deep Diagnostics Sensors** in the integration's **Configure** options.

