# Offline Hybrid Setup (`Local UDP` + `Matter` + `IR`)

Offline Hybrid lets you combine local connection methods with zero MirAIe Cloud account or internet dependency.

## Supported Combinations
- **Local UDP + IR Blaster**: Sub-20ms LAN control with IR failover.
- **Matter + IR Blaster**: Matter handles Power, Cool, Setpoint, and Fan Speed; IR handles Dry, Fan Only, Auto, Swings, Nanoe, Display LED, and Coil Clean.
- **Matter + Local UDP**: Fast LAN control with Matter backup (or vice versa).

## Steps in Home Assistant
1. Go to **Settings -> Devices & Services -> Add Integration -> Panasonic AC (India)**.
2. Choose **Offline Hybrid (Local UDP / Matter / IR)**.
3. Select your **Primary Connection Method** and **Secondary Backup Connection Method**.
4. Provide the matching local fields (Matter `climate.*` entity, IR blaster entity, and/or Manual UDP IP + AES Key/IV).
5. Submit to create your unified AC device.

