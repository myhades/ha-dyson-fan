# Dyson Fan

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-yellow.svg)](https://hacs.xyz/)
[![Maintainer](https://img.shields.io/badge/maintainer-%40myhades-green)](https://github.com/myhades)
[![Release](https://img.shields.io/github/v/release/myhades/ha-dyson-fan)](https://github.com/myhades/ha-dyson-fan/releases)

![Dyson Fan](assets/dyson_fan_repo_logo.png)

Dyson Fan integrates the Dyson AM07 into Home Assistant with fast, real-world state feedback.

## Requirements

- A smart plug dedicated to the fan that reports power readings in W or another power unit such as kW. Both fast reports and intervals around 10 seconds are supported; faster reports shorten feedback and calibration waits.
- An infrared (IR) blaster.
- A Dyson AM07 fan.

Power readings above 100 W are treated as invalid feedback.

## Installation

Home Assistant Core must be `2026.7.0` or newer.

Choose your preferred installation method, and reboot Home Assistant afterward.

### Method 1: Through HACS

This repository is not in the default list yet. To add it, use the My button below, or navigate to "HACS" > "Overflow menu" > "Custom repositories" and enter:

- **Repository:** `https://github.com/myhades/ha-dyson-fan`
- **Type:** Integration

Then, navigate to "HACS" > "Dyson Fan" and install the integration.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myhades&repository=ha-dyson-fan&category=integration)

### Method 2: Manually

Download the repository and copy the `/custom_components/dyson_fan` folder into your Home Assistant `/config/custom_components` directory.

## Configuration

To add the integration, navigate to "Settings" > "Devices & services" > "Add integration" > "Dyson Fan", or use the My button below. Then follow the configuration flow.

[![Add Dyson Fan to Home Assistant.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dyson_fan)

The power toggle, speed up, speed down, oscillation toggle, and feedback burst actions can each contain one or more Home Assistant action steps. For example, the following configuration sends a Broadlink IR command, assuming that the power toggle command was learned with the ID `power_toggle` for the device `dyson_tower_fan`:

```yaml
action: remote.send_command
target:
  entity_id: remote.living_room
data:
  device: dyson_tower_fan
  command: power_toggle
```

Each configured control action should send its corresponding IR command exactly once. The integration repeats the speed action when multiple steps are required. To change the power sensor or any configured action, select "Reconfigure" from the integration menu.

After setup, the fan entity becomes available once several power reports establish a stable state.
While the fan is off, its speed cannot be observed from power readings, so the integration remembers the last known speed for the next power-on.

## Calibration

After configuring the integration, run the initial calibration by pressing the `Calibrate power table` button. This adapts the included power table to your setup. In my experience, repeating calibration every two weeks helps maintain long-term feedback accuracy.

The calibration process turns the fan off, waits for the power-off cycle to clear oscillation, drives it to stationary speed 1, measures the oscillation power increment, and power-cycles it again to clear oscillation. Endpoints mode then measures speed 10 and shifts and scales the current power curve, while Full mode measures every remaining speed. Redundant commands confirm the endpoints before the previous state is restored. This takes several minutes. Do not control the fan manually with its onboard button or remote during calibration. Any fan command issued through Home Assistant safely cancels calibration.

If feedback is still inaccurate after Endpoints calibration, your setup's power curve may not scale uniformly. In that case, select Full under "Options" > "Control behavior", or manually enter the oscillation increment, off power, and stationary power for speeds 1 through 10 under "Options" > "Power table".

The `Undo last calibration` button restores the previous power table without controlling the fan. The backup survives Home Assistant restarts and is replaced after the next successful calibration. Editing the power table manually makes that backup unavailable for undo.

Automations can listen for the `dyson_fan_calibration_finished` event without enabling `Diagnostics`. It fires after calibration and the attempt to restore the previous fan state, and includes `entity_id`, `device_id`, `config_entry_id`, `mode`, `result`, `error`, `started_at`, and `finished_at`. Filter by your fan's `entity_id` when more than one fan is configured. The result is `success`, `failed`, or `cancelled`; `success_restore_failed`, `success_restore_cancelled`, and `failed_restore_cancelled` distinguish restoration problems. A successful calibration remains saved even if restoring the fan is interrupted or fails.

## Additional Configuration

### Options

| Option | Default | Description |
|--------|---------|-------------|
| Maximum attempts | 1 | Number of complete control attempts |
| Infrared send interval | 0.35 s | Time between consecutive IR actions |
| Calibration mode | Endpoints | Measure only the endpoints or every speed |
| Power table | | Oscillation increment, off power, and stationary power for speeds 1 through 10 |

### Faster Feedback

The optional feedback burst action runs before the integration waits for fresh power readings during normal control and calibration. Most off-the-shelf smart plugs cannot provide this behavior with their stock firmware.

The best option is a smart plug that supports ESPHome or similarly customizable firmware. This gives you control over the reporting frequency and lets you implement features like feedback burst. An ESPHome configuration example is included at
[`assets/esphome_power_burst.yaml`](assets/esphome_power_burst.yaml).

The integration works without this action; feedback will simply take longer.
The action should request faster reporting and return promptly. It is stopped after five seconds or when a newer fan request supersedes it; the integration then continues using the available power reports.

### Diagnostics Sensor

If you encounter an issue, enable the `Diagnostics` sensor, which is disabled by default.

The sensor exposes the requested, predicted, decoded, and confirmed states, along with the current power signature, attempts, timestamps, and last error.
Raw diagnostic readings refresh at most once every five seconds, while control and calibration status changes appear immediately. High-frequency debugging attributes are excluded from history; power sampling and fan feedback are unaffected.

## Feedback

When reporting an issue, include your setup, configuration, and logs.

You can enable debug logging in the UI when available, or add the following to your Home Assistant configuration:

```yaml
logger:
  logs:
    custom_components.dyson_fan: debug
```

## Thanks

Shout-out to Robert Nilsson (@rnilsson on the Home Assistant Community), who inspired this project and provided the basis for turning my old YAML-based, closed-loop IR control into an integration: [Dyson AM07 as a Full Fan Entity via Power Monitoring (IR + Kasa EP25)](https://community.home-assistant.io/t/dyson-am07-as-a-full-fan-entity-via-power-monitoring-ir-kasa-ep25/1003430).

## Disclaimer

This is an unofficial community integration and is not affiliated with, endorsed by, or supported by Dyson Limited. Dyson and the Dyson logo are trademarks of Dyson Limited.
