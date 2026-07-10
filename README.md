# Dyson Fan

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-yellow.svg)](https://hacs.xyz/)
[![Maintainer](https://img.shields.io/badge/maintainer-%40myhades-green)](https://github.com/myhades)
[![Release](https://img.shields.io/github/v/release/myhades/ha-dyson-fan)](https://github.com/myhades/ha-dyson-fan/releases)

![Dyson Fan](assets/dyson_fan_repo_logo.png)

Dyson Fan turns an infrared-controlled Dyson fan into a normal Home Assistant
`fan` entity with real feedback. It uses a power sensor to confirm power, speed,
and oscillation instead of assuming that an infrared command was received.

The integration works with any infrared transmitter that can be represented by
four Home Assistant actions: power toggle, oscillation toggle, speed up, and
speed down.

## Requirements

- Home Assistant `2026.7` or newer.
- A power sensor dedicated to the fan and updated every few seconds.
- Four working Home Assistant actions for the fan's infrared commands.
- A non-heating, 10-speed Dyson fan whose power use can be distinguished by
  speed and oscillation state.

Readings above 100 W are treated as invalid. Dyson heater models are not
supported.

## Installation

### HACS

1. Open HACS and add `https://github.com/myhades/ha-dyson-fan` as a custom
   integration repository.
2. Install **Dyson Fan**.
3. Restart Home Assistant.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myhades&repository=ha-dyson-fan&category=integration)

### Manual

Copy `custom_components/dyson_fan` to Home Assistant's
`/config/custom_components` directory, then restart Home Assistant.

## Configuration

Open **Settings → Devices & services → Add integration → Dyson Fan**, or use
the button below.

[![Add Dyson Fan to Home Assistant.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dyson_fan)

The setup flow asks for:

- the fan's power sensor;
- the power toggle action;
- the oscillation toggle action;
- the speed up action;
- the speed down action;
- an optional power-feedback burst action.

Actions can contain one or more normal Home Assistant action steps. For
example, a Broadlink command can be configured as:

```yaml
action: remote.send_command
target:
  entity_id: remote.living_room
data:
  device: dyson_tower_fan
  command: power_toggle
```

Each configured action should emit exactly one corresponding infrared command;
the integration repeats the speed action itself when several speed steps are
needed.

After setup, wait for several power reports. The fan becomes available once a
stable physical state has been recognized.

## Options

The integration's options page provides:

- **Feedback and actions** — changes the power sensor, optional feedback burst
  action, and four infrared actions.
- **Maximum attempts** — defaults to one complete command attempt.
- **Infrared send interval** — defaults to 0.35 seconds.
- **Power signature table** — the expected wattage for all 21 observable
  states.

The default table is based on one 10-speed Dyson tower fan. Adjust it if your
measurements differ.

## Faster power feedback

The optional feedback burst action runs whenever the integration wants fresh
power readings more quickly. It may call any Home Assistant action, script, or
service suitable for the configured power meter. An ESPHome example is included at
[`examples/esphome_power_burst.yaml`](examples/esphome_power_burst.yaml).

The integration works without this action; feedback will simply take longer.

## Automatic calibration

Use the **Calibrate power table** button on the device page to adapt the
included power signatures to your fan and power meter. Calibration turns the
fan off, waits for the hardware power cycle to clear oscillation, drives it to
stationary speed 1 and speed 10, checks each endpoint with redundant commands,
and then restores the previous power, speed, and oscillation state. Calibration
never sends the oscillation command while measuring endpoints.

The fan will run through these states for several minutes. Keep its power
sensor updating and do not use the remote during calibration. A Home Assistant
fan command safely cancels calibration at the next infrared-command boundary;
an incomplete or unreasonable measurement never replaces the existing table.

## Diagnostics

Dyson Fan creates one disabled diagnostic sensor. Enable it from the device's
entity list to inspect the requested, predicted, decoded, and confirmed states,
the current power signature, attempts, timestamps, and the last error.

Downloadable diagnostics are also available from the integration page.

## Limitations

- Infrared power and oscillation commands are toggles; speed commands are
  relative.
- The power sensor should measure only the fan.
- Speed cannot be observed while the fan is off. The last confirmed speed is
  remembered across Home Assistant restarts.
- Calibration projects the built-in non-linear reference curve onto the
  measured stationary speed 1 and speed 10 endpoints; it does not separately
  measure every intermediate or oscillating state.

## Feedback

When reporting an issue, include the downloadable integration diagnostics and
describe the power sensor, infrared transmitter, and fan model being used.

Debug logging can be enabled with:

```yaml
logger:
  logs:
    custom_components.dyson_fan: debug
```

## Disclaimer

This is an unofficial community integration and is not affiliated with,
endorsed by, or supported by Dyson Limited. Dyson and the Dyson logo are
trademarks of Dyson Limited.
