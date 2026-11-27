# Panasonic AC India (`ha-panasonic-ac-in`)

<p align="center">
  <img src="custom_components/miraie_in/brand/logo.png" alt="Panasonic AC India Logo" width="380">
</p>

<!-- ha-marketplace:exclude-start -->
<p align="center">
  <a href="https://www.home-assistant.io/integrations/marketplace/"><img src="https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg?style=flat-square" alt="Marketplace Custom"></a>
  <a href="https://github.com/selvakk2k/ha-panasonic-ac-in/releases/latest"><img src="https://img.shields.io/github/v/release/selvakk2k/ha-panasonic-ac-in?label=Stable&style=flat-square" alt="Stable"></a>
  <a href="https://github.com/selvakk2k/ha-panasonic-ac-in/releases"><img src="https://img.shields.io/github/v/release/selvakk2k/ha-panasonic-ac-in?include_prereleases&label=Beta&color=orange&style=flat-square" alt="Beta"></a>
  <a href="https://github.com/selvakk2k"><img src="https://img.shields.io/badge/AI%20Assisted-Antigravity%20%7C%20Claude%20%7C%20CodeRabbit-blueviolet?style=flat-square" alt="AI-Assisted"></a>
  <a href="https://aiattribution.github.io/interpret-attribution"><img src="https://img.shields.io/badge/AI%20Attribution-AIA%20PAI%20Nc%20Hin-orange?style=flat-square" alt="AI Attribution"></a>
</p>
<!-- ha-marketplace:exclude-end -->

A comprehensive Home Assistant custom integration for Panasonic Air Conditioners operating on the Indian-market MirAIe IoT platform. Features a multi-connection architecture combining real-time Cloud MQTT, zero-latency Local UDP, Matter, and offline Local IR blaster failover, blended real-time and historical energy statistics, deep inverter diagnostics, and convertible capacity limits.

> [!IMPORTANT]
> This integration is designed **exclusively** for Panasonic Air Conditioners using the Indian **MirAIe** mobile application (or local Matter / IR control). It is **not compatible** with Panasonic ACs that use the global **Comfort Cloud** platform.

> [!TIP]
> A companion Lovelace dashboard card is available: **[panasonic-ac-card-in](https://github.com/selvakk2k/panasonic-ac-card-in)** (Panasonic AC India Card).

> [!NOTE]
> ### Upgrading to 3.0
> Upgrading from `2.1` is fully automated. Config entries migrate cleanly, existing `climate.*` entities and automations keep working without reconfiguration, and cumulative energy statistics are preserved. See **[What's New in 3.0](docs/v3.0-whats-new.md)** for highlights and screenshots.

---

## Table of Contents

1. [Key Features](#key-features)
2. [Tested AC Models](#tested-ac-models)
3. [Control Modes & Architecture](#control-modes--architecture)
4. [Long-Term Energy Statistics & Diagnostics](#long-term-energy-statistics--diagnostics)
5. [Installation](#installation)
6. [Configuration & Per-Device Options](#configuration--per-device-options)
7. [Troubleshooting & Logs](#troubleshooting--logs)
8. [My Integrations & Lovelace Cards](#my-integrations--lovelace-cards)
9. [Credits & License](#credits--license)

---

## Key Features

### 1. Connection & Hardware Support
* **3-Level Connection Setup (`Online Only`, `Local Only`, `Hybrid`)**: Connect via `Cloud MQTT`, `Local UDP`, `IR Blaster`, `Matter`, or any offline/online combination with automatic failover.
* **Matter AC Bridge**: Bridge local Matter-paired Panasonic ACs with packed-temperature decoding, `16–30 °C` setpoint bounds, standalone Matter-Only mode, or capability-based Hybrid delegation.
* **Local UDP Heartbeat**: Periodic UDP keepalive keeps `Local UDP Connected` accurate even when the AC is idle.
* **Interactive AC Model Selector**: Guided model selector in initial setup and per-device options flow, powered jointly by `miraie-ac-in` (cloud capabilities) and `panasonic-ac-models` (hardware database covering 500+ Indian Panasonic AC models).
* **Converti7 and Converti8 Capacity Limits**: Dynamically exposes convertible capacity presets (40% up to 110%) matching your model's exact hardware generation.
* **Firmware 3.02+ Ambient Decoding**: Accurately parses packed decimal room temperature payloads returned by modern firmware (e.g. decoding `"134.30"` to `30°C`).
* **Hardware-Gated Heat Controls**: Automatically restricts `HEAT` mode options to verified Hot & Cold inverter hardware (such as `EZ` and `KZ` series).

### 2. Controls, Energy & Diagnostics
* **Blended Real-Time Energy (`sensor.<device>_energy_today`)**: Combines live inverter power estimation (from 508-model BEE data in `panasonic-ac-models`) with official MirAIe Cloud hourly/daily totals.
* **Deep Inverter Diagnostics (`/diag`)**: Optional live TLV telemetry for **Compressor Frequency (`Hz`)**, **Evaporator Coil Temp (`°C`)**, **Outdoor Condenser Temp (`°C`)**, **Voltage (`V`)**, and **Current (`A`)**.
* **Display Mode Control**: Automatically provides binary `switch.<device>_display` or multi-state `select.<device>_display_mode` for models supporting Night/Dim display modes.
* **Nanoe™ Air Purification**: Exposes switch entities to toggle built-in nanoe-G and nanoe-X air purification generators on supported series (`XU`, `HU`).
* **Coil Cleaning Cycle & Filter Alert**: Stateless trigger button to run the self-cleaning indoor coil cycle, binary sensor tracking active cycle progress, and filter clean notification.
* **Telemetry & Signal**: Tracks Wi-Fi RSSI (in dBm), local UDP connection state (`binary_sensor.<device>_local_udp_connected`), and last control source (`Local UDP`, `Matter`, physical IR remote, mobile app, or Home Assistant).

---

## Tested AC Models

Verified on physical Indian inverter hardware:

| Model Number | Series | Verified Capabilities | Status |
| :--- | :--- | :--- | :--- |
| **CS-CU-EU18CKY5XFM** | EU Series (1.5T Inverter) | Firmware 3.02+, Converti 7-in-1, Energy Import, Diagnostics | ✅ Hardware Verified |
| **CS-CU-SU18ZKYWT** | SU Series (1.5T Inverter) | Inverter Converti Series, Cloud MQTT Push | ✅ Hardware Verified |
| **CS-CU-XU18YKYF** | XU Series (1.5T Inverter) | Nanoe™ Air Purifier, Converti 8-in-1 | ✅ Hardware Verified |
| **CS-CU-KZ18XKY** | KZ Series (Hot & Cold) | Gated Heat Mode, Converti Series | ✅ Hardware Verified |

> [!NOTE]
> Models not listed in this table are not blocked during setup. Any Indian-market Panasonic AC model sharing this remote protocol or connected via the Indian MirAIe mobile app will function normally. The table above lists physically tested hardware, not a hard compatibility limit.

---

## Control Modes & Architecture

The integration supports a decoupled, multi-connection architecture allowing you to configure primary, secondary, and tertiary connection methods with automated failover:

* **Cloud MQTT Push**: Real-time bidirectional telemetry, status feedback, and historical energy analytics via MirAIe IoT servers.
* **Local UDP (Firmware 3.x)**: Zero-latency local network control with automatic device IP resolution, periodic heartbeat keepalive, and connection telemetry (`binary_sensor.<device>_local_udp_connected`). Local UDP protocol research and integration by @dtechterminal (`ha-panasonic-miraie-local`).
* **Matter AC Bridge**: Local Matter control with packed-temperature decoding, `16–30 °C` setpoint bounds, standalone Matter-Only mode, or capability-based Hybrid delegation. See [Matter Setup Guide](docs/setup-matter.md).
* **Local IR Blaster**: Offline survivability and instant local thermostat dispatch using ESPHome, Broadlink, or Tuya transmitters.
* **Offline Hybrid & Standalone Modes**: Run `Local UDP + IR`, `Matter + IR`, `Matter + Local UDP`, `Matter Only`, or `IR Only` completely offline with zero cloud dependency. See [Offline & Hybrid Setup Guide](docs/setup-offline-hybrid.md).

```
                  ┌─────────────────────────────────────────┐
                  │       Panasonic AC Climate Entity       │
                  └────────────────────┬────────────────────┘
                                       │
     ┌─────────────────────┬───────────┴───────────┬─────────────────────┐
     ▼                     ▼                       ▼                     ▼
[Local UDP (3.x)]    [Cloud MQTT Push]       [Matter Bridge]      [Local IR Blaster]
• Zero-latency LAN   • Real-time cloud sync  • Local Matter       • Offline failover
• Auto IP lookup     • Historical energy     • Packed temp decode • ESPHome / Broadlink
• Keepalive ping     • Diagnostics (/diag)   • Hybrid delegation  • Native HA IR / Tuya
```

* **Unified Single Climate Entity**: Commands and controls route through a single `climate.<device>` entity regardless of active connection method.
* **4-Tier Connection Priority**: In device Options, flexibly assign **Primary**, **Secondary**, and **Tertiary** connection methods across `Local UDP`, `IR Blaster`, `Matter`, `Cloud MQTT`, or `Disabled`.
* **Auto Failover Mode**: Dispatches commands through your primary connection method and automatically fails over to secondary or tertiary methods if the primary becomes unreachable.
* **Optional IR Receiver**: An IR receiver is optional. State confirmation can be received via Cloud MQTT, Local UDP, or Matter, preventing state drift without extra receiver hardware.

---

## Long-Term Energy Statistics & Diagnostics

* **Blended Real-Time Energy (`sensor.<device>_energy_today`)**: Combines live inverter power estimation (powered by the 508-model BEE database in `panasonic-ac-models`) with official MirAIe Cloud totals for accurate, non-duplicated Energy Dashboard tracking.
* **Historical Energy Import**: Automatically imports daily historical energy consumption from the MirAIe cloud (up to ~8 months back) directly into Home Assistant's recorder statistics database under `sensor.<device>_energy_history`.
* **Deep Inverter Diagnostics (`/diag`)**: Enable **Enable Deep Diagnostics Sensors** in Device Settings to decode live **Compressor Frequency (`Hz`)**, **Evaporator Coil Temp (`°C`)**, **Outdoor Condenser Temp (`°C`)**, **Voltage (`V`)**, and **Current (`A`)**. See [Energy & Diagnostics Guide](docs/energy-and-diagnostics.md).
* **Diagnostic Maintenance Buttons**: Exposes **Rebuild Energy Statistics** (`mdi:database-refresh`) and **Verify Energy Statistics** (`mdi:database-check`) button entities directly on the device page for on-demand audit and repair.

---

<!-- ha-marketplace:exclude-start -->
## Installation

* **Prerequisites**: Home Assistant **2024.1.0** or newer.

### Method 1: Via Home Assistant Marketplace (Recommended)

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=selvakk2k&repository=ha-panasonic-ac-in&category=integration)

**Home Assistant `2026.11+` (Marketplace):**
1. Click the **Open Marketplace repository** button above, or go to **Settings** → **Marketplace**.
2. Select **Add from link** (upper-right), enter `https://github.com/selvakk2k/ha-panasonic-ac-in` (connect GitHub if prompted), and select **Add** *(if prompted for **Type**, select **Integration**)*.
3. Open **Panasonic AC India**, select **Download**, and restart Home Assistant.

**Home Assistant `<= 2026.10` (Legacy HACS):**
1. Open **HACS** in your sidebar → top-right menu (⋮) → **Custom repositories** → Add `https://github.com/selvakk2k/ha-panasonic-ac-in` with category **Integration**.
2. Search for **Panasonic AC India**, click **Download**, and restart Home Assistant.

### Method 2: Manual Installation
1. Download the latest release ZIP from the [Releases](https://github.com/selvakk2k/ha-panasonic-ac-in/releases) page.
2. Copy the `custom_components/miraie_in` folder into your Home Assistant `<config>/custom_components/` directory.
3. Restart Home Assistant.

---
<!-- ha-marketplace:exclude-end -->

## Configuration & Per-Device Options

When adding the integration (**Settings → Devices & Services → Add Integration → Panasonic AC India**), the setup wizard prompts you to choose a setup path:

### Option 1: Online Only (`Cloud MQTT`)
1. Select **Online Only** → **MirAIe Cloud** in the setup menu.
2. Enter your **Username** (10-digit mobile number or email address) and **Password** (or `demo@miraie.in` for simulated demo mode).
3. The integration discovers all ACs linked to your account and provisions them with `Cloud MQTT`.

### Option 2: Local Only (`Local UDP`, `IR Blaster`, `Matter`, or Offline Combos)
1. Select **Local Only** in the setup menu—no cloud login is required.
2. Choose **Local UDP**, **Infrared Remote**, **Matter**, or an offline hybrid combination (**Local UDP + IR**, **Matter + IR**, **Matter + Local UDP**).
3. Follow the guided prompts to select your AC model (from `panasonic-ac-models`), bind your local IP/UDP, Matter climate entity, or IR transmitter/receiver.

### Option 3: Hybrid (Online + Local Connection Methods)
1. Select **Hybrid** in the setup menu to connect both online (`Cloud MQTT`) and local (`Local UDP`, `IR Blaster`, `Matter`) connection methods.
2. Sign in to MirAIe Cloud, then bind your local connection methods (`Local UDP`, `IR Blaster`, and/or `Matter`) for automatic failover and local speed.

### Per-Device Custom Tuning
Click **Configure** on any Panasonic AC device card to customize hardware bindings and control preferences:
* **Primary / Secondary / Tertiary Connection**: Choose connection priority across `Cloud MQTT`, `Local UDP`, `Matter`, `IR Blaster`, or `Disabled`.
* **Hybrid Failover**: Choose between `Automatic Failover (Switch to secondary connection on outage)` or `Manual Control`.
* **Enable Deep Diagnostics Sensors**: Toggle live `/diag` inverter telemetry (`Compressor Frequency`, `Evaporator Coil Temp`, `Outdoor Condenser Temp`, `Voltage`, `Current`).
* **IR Transmitter / Receiver Entity (Optional)**: Select an `infrared` or `remote` entity (e.g. ESPHome, Broadlink, Tuya) to enable Local IR dispatch and physical remote sync.
* **Matter Climate Entity (Optional)**: Link an existing Home Assistant Matter `climate.*` entity for Matter-Only or Hybrid Matter control.
* **Room Temperature Sensor (Optional)**: Bind an external temperature sensor (`sensor.*` with `temperature` device class) for accurate room temperature reporting.
* **Availability / Status Entity (Optional)**: Select an availability entity (`binary_sensor`, `input_boolean`, `switch`, or `device_tracker`) to detect blaster outages immediately without waiting for Home Assistant's default 90–120 second TCP timeout.
* **IR Protocol / Format**: Select the signal encoding format expected by your blaster (`Auto-Detect (Recommended)`, `Home Assistant Infrared / ESPHome`, `Tasmota / AEHA Hex`, `Broadlink Base64`, or `Tuya Base64`).
* **Energy History Start Date**: Select the date to begin importing historical energy statistics from the MirAIe cloud (defaults to 6 months ago, up to ~8 months supported).

### ESPHome Blaster Recommendations & Firmware Notice
* **ESPHome 2026.10+ Upgrade (Recommended)**: For native `infrared` transmitters, upgrading the blaster to ESPHome `2026.10` or newer enables hardware transmit-complete acknowledgements. The integration automatically detects firmware 2026.10+ and applies an explicit 2.5-second completion timeout to command dispatch, failing over immediately on hardware outages.
* **LibreTiny Buffer Size Change (PR #19101)**: If using Beken BK7231N (LibreTiny) or ESP8266 hardware with an explicit `buffer_size` configured under `remote_receiver:`, note that ESPHome 2026.10 measures buffer capacity in bytes rather than entry count. Multiply your existing `buffer_size` value by 4 (e.g. from `1000` to `4000`) when upgrading your YAML configuration to ESPHome 2026.10+. If `buffer_size` is omitted in your YAML, no change is needed.

---

## Troubleshooting & Logs

Sensitive credentials (phone numbers, passwords, and tokens) are automatically scrubbed from diagnostics and logs.

### 1. Enabling Debug Logs

#### Via the UI (Dynamic, no restart required)
1. Go to **Settings → Devices & Services** → Select the **Panasonic AC India** card.
2. Click the top-right menu (**⋮**) → **Enable debug logging**.
3. Reproduce the issue, then click **Disable debug logging** to download the log file.

#### Via `configuration.yaml` (Persistent / Startup Issues)
To capture early startup, initial configuration, and MQTT broker connection logs across Home Assistant restarts, add this to your `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.miraie_in: debug
    miraie_ac: debug
```

### 2. Core Diagnostics
* Go to the Device page for your Air Conditioner.
* Under **Device info**, click **Download diagnostics** to save the complete state and payload snapshot.

### 3. Offline Testing & Demo Mode
For testing dashboard cards (such as `panasonic-ac-card-in`) or Lovelace automations without physical hardware or live Panasonic cloud credentials, you can use the built-in mock mode:
1. Add a new integration entry (**Settings → Devices & Services → Add Integration → Panasonic AC India**).
2. Enter `demo@miraie.in` as the mobile number / user ID and any password.
3. The integration will create a simulated Panasonic AC entity with responsive controls, energy history, and convertible presets.

---

<!-- ha-marketplace:exclude-start -->
## My Integrations & Lovelace Cards

| Integration / Card | Category | Description | Status |
| :--- | :--- | :--- | :--- |
| [Atomberg Smart Lock](https://github.com/selvakk2k/ha-atomberg-lock) | Integration | Local BLE & Cloud-Assisted key extraction for Atomberg Smart Door Locks (SL1 / SL1 Pro) | `Stable` |
| [Atomberg Smart Lock Card](https://github.com/selvakk2k/atomberg-lock-card) | Lovelace Card | Custom Lovelace card for Atomberg Smart Door Locks with activity & audit logs | `Stable` |
| [CP PLUS STQC](https://github.com/selvakk2k/ha-cpplus-stqc) | Integration | Local NVR & STQC IP Camera integration with real-time AI push events | `Beta` |
| [Panasonic AC India](https://github.com/selvakk2k/ha-panasonic-ac-in) | Integration | Local IR, Local UDP, Matter & Cloud MQTT control for Panasonic MirAIe Air Conditioners | `Stable` |
| [Panasonic AC India Card](https://github.com/selvakk2k/panasonic-ac-card-in) | Lovelace Card | Modern Lovelace card for Panasonic ACs | `Stable` |
| [Indian BLDC Fan IR](https://github.com/selvakk2k/ha-bldc-fan-ir) | Integration | Native Home Assistant integration for Indian BLDC ceiling fans (Atomberg, Superfan) | `Stable` |
| [Indian BLDC Fan Card](https://github.com/selvakk2k/bldc-fan-card) | Lovelace Card | Interactive Lovelace card with speed dial & mode toggles for BLDC fans | `Stable` |
| [IFB Washer Local](https://github.com/selvakk2k/ifb-washer-local) | Integration | Local Wi-Fi integration for IFB Front Load Washing Machines & Washer Dryers | `Beta` |
| [IFB Washer Card](https://github.com/selvakk2k/ifb-washer-card) | Lovelace Card | Dedicated Lovelace card for IFB washers & dryers with cycle controls | `Beta` |
| [Tinxy Local Python](https://github.com/selvakk2k/ha-tinxylocal) | Integration | Pure-Python local control for Tinxy smart switches and modules | `Stable` |

---
<!-- ha-marketplace:exclude-end -->

## Credits & License

### Upstream Authors & Contributors
* Originally designed and written by [@rkzofficial](https://github.com/rkzofficial) and contributors in [`ha-miraie-ac`](https://github.com/rkzofficial/ha-miraie-ac).
* Upstream feature contributions by [@deCodeIt](https://github.com/deCodeIt) and [@gutpull](https://github.com/gutpull).
* **Local UDP Protocol Research**: [@dtechterminal](https://github.com/dtechterminal) ([ha-panasonic-miraie-local](https://github.com/dtechterminal/ha-panasonic-miraie-local)) for reverse engineering the local AES-256-CBC UDP protocol.

### Fork Maintainers & Contributors
* **Lead Architecture & Hardware Validation**: [@selvakk2k](https://github.com/selvakk2k) — physical hardware captures and domain requirements.
* **Historical Energy Backfill**: Contributed by [@shashi278](https://github.com/shashi278).
* **Code Implementation & Engineering**: **Antigravity** (Google DeepMind) — Hybrid connection failover, Matter bridge, firmware 3.02+ parsing, blended energy & `/diag` telemetry, and automated test suites.
* **Automated Code Review & Auditing**: **CodeRabbit** ([@coderabbitai](https://github.com/coderabbitai)) — automated pull request and pre-commit code reviews, edge case detection, and security verification.
* **Pre-Release Code Review & Auditing**: **Claude** (Anthropic) — independent architectural review, edge-case analysis, and verification of upstream compatibility.

Licensed under the **Apache-2.0 License**. See the [LICENSE](LICENSE) file for details.

