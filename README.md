# Panasonic AC India (`ha-panasonic-ac-in`)

<p align="center">
  <img src="custom_components/miraie_in/brand/logo.png" alt="Panasonic AC India Logo" width="380">
</p>

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/integration)
[![Stable](https://img.shields.io/github/v/release/selvakk2k/ha-panasonic-ac-in?label=Stable&style=flat-square)](https://github.com/selvakk2k/ha-panasonic-ac-in/releases/latest)
[![Beta](https://img.shields.io/github/v/release/selvakk2k/ha-panasonic-ac-in?include_prereleases&label=Beta&color=orange&style=flat-square)](https://github.com/selvakk2k/ha-panasonic-ac-in/releases)
[![AI-Assisted](https://img.shields.io/badge/AI%20Assisted-Antigravity%20%7C%20Claude-blueviolet?style=flat-square&logo=google)](https://github.com/selvakk2k)
[![AI Attribution](https://img.shields.io/badge/AI%20Attribution-AIA%20PAI%20Nc%20Hin-orange?style=flat-square)](https://aiattribution.github.io/interpret-attribution)

A comprehensive Home Assistant custom integration for Panasonic Air Conditioners operating on the Indian-market MirAIe IoT platform. Features a dual-transport Hybrid architecture combining real-time Cloud MQTT with zero-latency Local IR blaster failover, long-term historical energy statistics import, and convertible capacity limits.

> [!IMPORTANT]
> This integration is designed **exclusively** for Panasonic Air Conditioners using the Indian **MirAIe** mobile application. It is **not compatible** with Panasonic ACs that use the global **Comfort Cloud** platform.

> [!TIP]
> A companion Lovelace dashboard card is available: **[panasonic-ac-card-in](https://github.com/selvakk2k/panasonic-ac-card-in)** (Panasonic AC India Card).

> [!NOTE]
> ### Upgrading from 1.x to 2.0
> Upgrading requires zero configuration changes. The integration domain remains `miraie_in`. All existing climate entities, automations, and dashboard cards continue working without reconfiguration. Dual-transport local IR failover is completely optional and can be enabled at any time via device Options.

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

### 1. Hardware & Platform Support
* **Converti7 and Converti8 Capacity Limits**: Dynamically exposes convertible capacity presets (40% up to 110%) matching your model's exact hardware generation.
* **Firmware 3.02+ Ambient Decoding**: Accurately parses packed decimal room temperature payloads returned by modern firmware (e.g. decoding `"134.30"` to `30°C`).
* **Hardware-Gated Heat Controls**: Automatically restricts `HEAT` mode options to verified Hot & Cold inverter hardware (such as `EZ` and `KZ` series).

### 2. Controls & Sensors
* **Nanoe™ Air Purification**: Exposes switch entities to toggle built-in nanoe-G and nanoe-X air purification generators on supported series (`XU`, `HU`).
* **Coil Cleaning Cycle**: Stateless trigger button to run the self-cleaning indoor coil cycle and a binary sensor tracking active cycle progress.
* **Filter Clean Notification**: Binary sensor indicating when the indoor unit's controller flags that the mesh air filter requires cleaning.
* **Telemetry & Signal**: Tracks Wi-Fi RSSI (in dBm) and detects whether the last command originated via physical IR remote, mobile app, or Home Assistant.

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
> Models not listed in this table are not blocked during setup. Any Indian-market Panasonic inverter split AC (including Converti 7-in-1 and Converti 8-in-1 series) sharing this remote protocol or connected via the Indian MirAIe mobile app will function normally. The table above lists physically tested hardware, not a hard compatibility limit.

---

## Control Modes & Architecture

The integration supports three operating modes depending on how you set up your AC:

* **Cloud-Only Mode**: Set up using your MirAIe account for Wi-Fi ACs. No IR blaster hardware is required. Provides full temperature control, convertible capacity presets, live room temperature readings, and long-term energy import.
* **Hybrid Mode**: Created by attaching a local IR blaster entity to a Cloud-connected Wi-Fi AC. Commands are dispatched instantly over local IR, while the cloud connection provides live state confirmation, diagnostics, and energy data. If the internet connection drops, local commands continue operating without interruption.
* **Standalone IR-Only Mode**: A separate, cloud-free setup path for non-Wi-Fi ACs or users wanting 100% offline control. In the setup wizard, choose **Standalone IR Device**, enter your Panasonic AC model, and it's feature set will then be retrieved from the hardware database. Confirm the features and then add your IR blaster (receiver is optional). Creates a fully capable local climate entity without any cloud account.

```
                  ┌─────────────────────────────────────────┐
                  │       Panasonic AC Climate Entity       │
                  └────────────────────┬────────────────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    ▼                                     ▼
          [Cloud MQTT Push]                       [Local IR Blaster]
          • Real-time bi-directional telemetry    • Zero-latency local dispatch
          • Energy data & diagnostic states       • Offline survivability
          • Automatic cloud failover target       • Native Home Assistant Infrared
```

* **Unified Single Climate Entity**: In Hybrid mode, commands and controls route through a single `climate.<device>` entity without generating duplicate entities.
* **Auto Failover Mode**: Thermostat commands are sent over local IR for instant response, while status updates are confirmed via cloud MQTT. If the internet connection drops, local commands continue functioning without interruption.
* **Manual Backend Selection**: Use the integration's backend switch entity (`switch.<device>_backend`) to lock control to Cloud-only or IR-only modes manually or via automations.
* **Optional IR Receiver**: An IR receiver is optional. In Hybrid mode, Cloud MQTT acts as the authoritative state feedback loop, preventing state drift without extra receiver hardware. The IR receiver is only for receiving commands from external IR remotes/devices (eg. IR blaster from phone). 

---

## Long-Term Energy Statistics & Diagnostics

* **Historical Energy Import**: Automatically imports daily historical energy consumption from the MirAIe cloud (up to ~8 months back) directly into Home Assistant's recorder statistics database under `sensor.<device>_energy_history`.
* **Energy Reconciliation**: Implements a 4-stage reconciliation cycle (`Yesterday -> Weekly -> Monthly -> Today`) to protect cumulative energy data against cloud outages.
* **Diagnostic Maintenance Buttons**: Exposes **Rebuild Energy Statistics** (`mdi:database-refresh`) and **Verify Energy Statistics** (`mdi:database-check`) button entities directly on the device page for on-demand audit and repair.

---

## Installation

* **Prerequisites**: Home Assistant **2024.1.0** or newer.

### Method 1: Using HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=selvakk2k&repository=ha-panasonic-ac-in&category=integration)

1. Click the **Open repository in HACS** button above, or open **HACS** from your Home Assistant sidebar.
2. Click the top-right menu (⋮) → **Custom repositories** → Add `https://github.com/selvakk2k/ha-panasonic-ac-in` with category **Integration**.
3. Search for **Panasonic AC India**, click **Download**, and restart Home Assistant.

### Method 2: Manual Installation
1. Download the latest release ZIP from the [Releases](https://github.com/selvakk2k/ha-panasonic-ac-in/releases) page.
2. Copy the `custom_components/miraie_in` folder into your Home Assistant `<config>/custom_components/` directory.
3. Restart Home Assistant.

---

## Configuration & Per-Device Options

When adding the integration (**Settings → Devices & Services → Add Integration → Panasonic AC India**), the setup wizard prompts you to **Select Setup Mode**:

### Option 1: MirAIe Cloud (Wi-Fi Models)
1. Select **MirAIe Cloud (Wi-Fi Models)** in the setup menu.
2. In the **MirAIe Cloud Login** screen, enter your **Username** (10-digit mobile number or email address) and **Password**.
3. The integration discovers all ACs linked to your account and provisions them in **Cloud-Only** mode.
4. *(Optional Hybrid Upgrade)*: To enable **Hybrid** control, click **Configure** on any device card and select your **IR Transmitter Entity (Optional)**.

### Option 2: Infrared Remote (Non-Wi-Fi Models)
1. Select **Infrared Remote (Non-Wi-Fi Models)** in the setup menu. No cloud login or Wi-Fi AC is required.
2. In **Step 1: Model Details**, enter your **AC Unit Name** and **Panasonic AC Model Code** (e.g. `CS-CU-RU18CKY-1`, `CS-KN12AKY`).
3. In **Step 2: Capabilities**, review the auto-detected features (Heat Mode, Nanoe, Convertible Capacity tier, Horizontal Swing).
4. In **Step 3: Attach IR Hardware**, select your **IR Transmitter Entity**, optional **IR Receiver Entity**, and optional **Room Temperature Sensor**.
5. The integration provisions a 100% local, standalone **IR-Only** climate entity with model-accurate presets, temperature ranges, and swing modes.

### Per-Device Custom Tuning
Click **Configure** on any Panasonic AC device card to customize hardware bindings and control preferences:
* **Primary Control**: Choose whether commands prefer `Cloud` or `Infrared` by default in Hybrid mode.
* **Hybrid Failover**: Choose between `Automatic Failover (Switch to secondary transport on outage)` or `Manual Control`.
* **IR Transmitter Entity (Optional)**: Select an `infrared` or `remote` entity (e.g. ESPHome, Broadlink, Tuya) to enable Local IR or Hybrid control. Leaving this empty operates in Cloud-Only mode.
* **IR Receiver Entity (Optional)**: Select an `infrared` or `remote` receiver entity to capture physical remote control button presses for state synchronization.
* **Room Temperature Sensor (Optional)**: Bind an external temperature sensor (`sensor.*` with `temperature` device class) for accurate room temperature reporting.
* **Availability / Cutoff Entity (Optional)**: Select an availability sensor (`binary_sensor`, `input_boolean`, or `switch`) to detect blaster outages immediately without waiting for Home Assistant's default 90-120 second TCP timeout. If the entity ID contains `cutoff` (e.g. `input_boolean.fake_ir_cutoff`), its state is automatically inverted (`on` = cut off / offline, `off` = connected / online).
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

---

## My Integrations & Lovelace Cards

| Integration / Card | Category | Description | Status |
| :--- | :--- | :--- | :--- |
| [CP PLUS STQC](https://github.com/selvakk2k/ha-cpplus-stqc) | Integration | Local NVR & STQC IP Camera integration with real-time AI push events | `Beta` |
| [Panasonic AC India](https://github.com/selvakk2k/ha-panasonic-ac-in) | Integration | Local IR & Cloud MQTT control for Panasonic MirAIe Air Conditioners | `Stable` |
| [Panasonic AC India Card](https://github.com/selvakk2k/panasonic-ac-card-in) | Lovelace Card | Modern Lovelace card for Panasonic ACs | `Stable` |
| [Indian BLDC Fan IR](https://github.com/selvakk2k/ha-bldc-fan-ir) | Integration | Native Home Assistant integration for Indian BLDC ceiling fans (Atomberg, Superfan) | `Stable` |
| [Indian BLDC Fan Card](https://github.com/selvakk2k/bldc-fan-card) | Lovelace Card | Interactive Lovelace card with speed dial & mode toggles for BLDC fans | `Stable` |
| [IFB Washer Local](https://github.com/selvakk2k/ifb-washer-local) | Integration | Local Wi-Fi integration for IFB Front Load Washing Machines & Washer Dryers | `Beta` |
| [IFB Washer Card](https://github.com/selvakk2k/ifb-washer-card) | Lovelace Card | Dedicated Lovelace card for IFB washers & dryers with cycle controls | `Beta` |
| [Tinxy Local Python](https://github.com/selvakk2k/ha-tinxylocal) | Integration | Pure-Python local control for Tinxy smart switches and modules | `Stable` |

---

## Credits & License

### Upstream Authors & Contributors
* Originally designed and written by [@rkzofficial](https://github.com/rkzofficial) and contributors in [`ha-miraie-ac`](https://github.com/rkzofficial/ha-miraie-ac).
* Upstream feature contributions by [@deCodeIt](https://github.com/deCodeIt) and [@gutpull](https://github.com/gutpull).

### Fork Maintainers & Contributors
* **Lead Architecture & Hardware Validation**: [@selvakk2k](https://github.com/selvakk2k) — physical hardware captures and domain requirements.
* **Historical Energy Backfill**: Contributed by [@shashi278](https://github.com/shashi278).
* **Code Implementation & Engineering**: **Antigravity** (Google DeepMind) — Hybrid transport failover, firmware 3.02+ parsing, long-term statistics reconciliation, and automated test suites.
* **Pre-Release Code Review & Auditing**: **Claude** (Anthropic) — independent architectural review, edge-case analysis, and verification of upstream compatibility.

Licensed under the **Apache-2.0 License**. See the [LICENSE](LICENSE) file for details.
