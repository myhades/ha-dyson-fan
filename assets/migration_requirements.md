# Dyson Tower Fan Control System

This document describes the current Home Assistant YAML implementation for the living-room Dyson tower fan. It is intended as migration context for building a custom integration that replaces the helper/script/automation state machine while preserving the same behavior.

## Goal

The Dyson fan is controlled by infrared commands through a Broadlink remote. The fan itself does not expose a reliable native state entity. Current YAML implements a feedback-based state machine using smart-plug power readings as the readback channel.

The replacement integration should expose a normal `fan` entity while internally handling:

- target state changes from UI/HomeKit/automations
- IR command sequencing
- power-reading based state inference
- retry/convergence after commands
- target changes while a command attempt is already in progress
- the Dyson hardware quirk where oscillation must be treated as off after power-off

## Current Public Entity

The public entity consumed by UI, HomeKit, and automations is a template fan:

- `fan.dyson_tower_fan`
- Display name in UI: `Dyson 落地扇`
- Template source: [configuration.yaml](/home/myhades/Documents/GitHub/ha-config/configuration.yaml:255)
- It exposes:
  - power from `input_boolean.dyson_power`
  - percentage from `input_number.dyson_speed * 10`
  - `speed_count: 10`
  - oscillation from `input_boolean.dyson_oscillate`
  - availability from smart-plug power sensor plus Broadlink remote availability

Availability template:

```jinja
{{
    states('sensor.iot_plug_dyson_power')|float(default=-999)!=-999 and
    states('remote.bo_lian_rm4c_mini') not in ['unavailable','unknown',None]
}}
```

## Current Helpers

These helpers are the user-facing target buffer:

- `input_boolean.dyson_power`
  - friendly name: `{Dyson} Power`
  - target/accepted power state

- `input_number.dyson_speed`
  - friendly name: `{Dyson} Speed`
  - expected logical range: `1..10`
  - exposed to the fan as `10..100%`
  - speed is intentionally preserved when the fan is off

- `input_boolean.dyson_oscillate`
  - friendly name: `{Dyson} Oscillate`
  - target/accepted oscillation state

This helper is an internal state carrier:

- `input_select.living_room_dyson_io`
  - friendly name: `{Dyson} I/O`
  - normal state values used by YAML: `read` and `set`
  - custom attributes are written by `python_script.set_state`

Important attributes currently stored on `input_select.living_room_dyson_io`:

- `state`
- `during_attempt`
- `last_operation`
- `last_read`
- `last_write_back`
- `reading_match_count`
- `try_count`
- `operate_power`
- `operate_speed`
- `operate_oscillate`
- `read_power`
- `read_speed`
- `read_oscillate`
- `supposed_power`
- `supposed_speed`
- `supposed_oscillate`

In a custom integration, this helper should disappear and become internal runtime state.

## Hardware And External Dependencies

Readback:

- `sensor.iot_plug_dyson_power`
  - smart-plug power reading in watts
  - used to infer fan power, speed, and oscillation

Power plug configuration:

- ESPHome source: [esphome/iot-plug-dyson.yaml](/home/myhades/Documents/GitHub/ha-config/esphome/iot-plug-dyson.yaml:1)
- ESPHome node name: `iot-plug-dyson`
- Friendly name: `Dyson Fan Plug`
- Role in this system: power-measurement feedback plug for the Dyson fan.
- Important: this plug is not the normal user-facing way to control the fan. The relay should generally stay on so the Dyson remains powered. Fan commands are sent by IR through the Broadlink remote.
- Hardware/platform:
  - `esp8266`
  - board: `nodemcuv2`
  - `restore_from_flash: true`
- Energy monitor:
  - platform: `bl0942`
  - `update_interval: 2s`
  - UART baud rate: `4800`
- ESPHome API:
  - encryption enabled
  - `reboot_timeout: 0s`
- Wi-Fi:
  - `reboot_timeout: 0s`
  - manual/static IP configured
  - AP fallback configured
- Web server:
  - enabled on port `80`
- Secrets intentionally not copied into this document:
  - ESPHome API encryption key
  - OTA password
  - web server password
  - fallback AP password
  - Wi-Fi credentials

Power plug entities currently produced:

| Entity | Role |
| --- | --- |
| `sensor.iot_plug_dyson_power` | Main feedback signal for Dyson state inference |
| `sensor.iot_plug_dyson_voltage` | Voltage telemetry |
| `sensor.iot_plug_dyson_current` | Current telemetry |
| `sensor.iot_plug_dyson_energy` | Energy telemetry |
| `sensor.iot_plug_dyson_frequency` | Frequency telemetry |
| `sensor.iot_plug_dyson_uptime_sensor` | Uptime timestamp |
| `binary_sensor.iot_plug_dyson_button` | Physical button |
| `binary_sensor.iot_plug_dyson_status` | ESPHome connectivity status |
| `switch.iot_plug_dyson_relay` | Physical relay, expected to remain on during normal fan operation |
| `switch.iot_plug_dyson_led_indicator` | Config switch for LED behavior |
| `switch.iot_plug_dyson_restart` | ESPHome restart switch |
| `device_tracker.dyson_fan_plug` | Network presence used by offline UI |

Relay details:

- Template switch name: `Relay`
- HA entity: `switch.iot_plug_dyson_relay`
- `restore_mode: RESTORE_DEFAULT_ON`
- State is stored in global `relay_state` with `restore_value: yes`
- Physical button on `GPIO0` toggles this relay
- Relay action drives GPIO outputs `ina` and `inb` with short pulses

LED details:

- Template switch name: `LED Indicator`
- HA entity: `switch.iot_plug_dyson_led_indicator`
- `restore_mode: RESTORE_DEFAULT_OFF`
- State is stored in global `led_follow_state` with `restore_value: yes`
- Internal LED GPIO is `GPIO2`, inverted

Why the integration needs this plug:

- The fan is IR-controlled and IR commands are relative/toggle based.
- The plug's wattage is the only real feedback channel that lets the controller confirm what state the fan actually reached.
- The current decoder relies on stable wattage signatures, so the future integration must subscribe to this power sensor or directly own equivalent power telemetry.

IR sender:

- `remote.bo_lian_rm4c_mini`
  - Broadlink remote

IR command target:

- `device: dyson_tower_fan`

IR commands used:

- `power_toggle`
- `oscillate_toggle`
- `speed_up`
- `speed_down`

Offline UI also watches:

- `device_tracker.dyson_fan_plug`

## Power Reading Decoder

Implemented by script `{Dyson} Read`, source: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:736).

Input:

- `fan_power`: current absolute smart-plug power reading
- `match_count_threshold`: passed by the automation

The script compares current wattage against known typical values and picks the closest state:

| Speed | Oscillation off | Oscillation on |
| --- | ---: | ---: |
| 0 | 1.2 W | n/a |
| 1 | 4.8 W | 7.7 W |
| 2 | 6.5 W | 9.3 W |
| 3 | 9.7 W | 12.5 W |
| 4 | 13.0 W | 16.0 W |
| 5 | 18.2 W | 21.1 W |
| 6 | 22.8 W | 25.7 W |
| 7 | 28.5 W | 31.2 W |
| 8 | 35.3 W | 38.3 W |
| 9 | 43.3 W | 46.3 W |
| 10 | 52.2 W | 55.2 W |

Derived read state:

- if inferred speed is `0`, `read_power = off`
- otherwise `read_power = on`
- `read_speed = inferred speed`
- `read_oscillate = on/off`

It then updates the I/O helper attributes:

- `read_power`
- `read_speed`
- `read_oscillate`
- `reading_match_count`

`reading_match_count` increments only when the newly decoded state matches the previous decoded state. Otherwise it resets to `0`. It is capped at `match_count_threshold`.

## IR Command Script

Implemented by script `{Dyson} Send Command`, source: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:768).

Mode:

- `restart`

Base interval:

- `interval_ms: 350`

Execution order:

1. Wait `350 ms`.

2. Oscillation correction.
   - Condition:
     - `operate_oscillate != supposed_oscillate`
     - `supposed_power == "on"`
   - Action:
     - set `supposed_oscillate = operate_oscillate`
     - send `oscillate_toggle`
   - Oscillation is only toggled while the fan is believed to be on.

3. Power correction.
   - Condition:
     - `operate_power != supposed_power`
   - Action:
     - set `supposed_power = operate_power`
     - send `power_toggle`

4. Power-off normalization.
   - If the new `supposed_power` is `off`, the script deliberately sets:
     - `supposed_oscillate = off`
     - `operate_oscillate = off`
     - `operate_speed = supposed_speed`
   - It also writes `input_number.dyson_speed = supposed_speed`.
   - This is intentional. The real Dyson has a quirk where if oscillation is not handled before/around shutdown, the next power-on can resume oscillation. The YAML model assumes a fully off fan starts with oscillation off.

5. Speed correction.
   - Condition:
     - `supposed_power == "on"`
     - `operate_speed != supposed_speed`
     - both speeds are valid
     - target speed is in `1..10`
   - Action:
     - repeatedly send `speed_up` or `speed_down`
     - update `supposed_speed` after each IR command
     - stop when `supposed_speed == operate_speed`

6. Increment `try_count`.

## State Machine Automation

Implemented by automation `[Device] Living Room - Dyson Tower Fan`, source: [automations.yaml](/home/myhades/Documents/GitHub/ha-config/automations.yaml:3593).

Mode:

- `parallel`
- `max: 3`

Constants:

- `match_count_threshold: 2`
- `max_try_count: 2`
- `after_write_tolerance: true`
- `tolerance_s: 180`

Triggers:

- `POWER_READING`
  - `sensor.iot_plug_dyson_power` state change

- `USER_OPERATE`
  - `input_boolean.dyson_oscillate`
  - `input_boolean.dyson_power`
  - `input_number.dyson_speed`

- `READING_MATCH_COUNT`
  - `input_select.living_room_dyson_io` attribute `reading_match_count`

Top-level trigger filters:

- `POWER_READING` is accepted only when:
  - `script.dyson_send_command` is not running
  - more than 2 seconds have passed since `last_operation`

- `USER_OPERATE` is accepted only when:
  - `last_write_back` is set
  - more than 2 seconds have passed since `last_write_back`
  - this prevents the automation from treating its own helper writeback as a fresh user command

- `READING_MATCH_COUNT` is always accepted.

Derived variables:

- `extra_count`
  - Adds one extra stable-reading requirement shortly after a write.
  - Current expression checks recent `last_operation` or `script.dyson_send_command.last_triggered` within `tolerance_s`.

- `device_online`
  - true only if the smart-plug power sensor is numeric and the Broadlink remote is not unavailable/unknown.

### POWER_READING Branch

When a power reading arrives:

1. write `last_read = now()` to the I/O helper
2. call `script.dyson_read`
   - `fan_power = abs(states('sensor.iot_plug_dyson_power'))`
   - `match_count_threshold = match_count_threshold + extra_count`

### USER_OPERATE Branch

When the public target helpers change and the device is online:

1. snapshot the target into `operate_*`
2. reset convergence counters
3. mark an active attempt
4. call `script.dyson_send_command`

Written attributes:

- `state: set`
- `during_attempt: true`
- `last_operation: now()`
- `reading_match_count: 0`
- `try_count: 0`
- `operate_power = states('input_boolean.dyson_power')`
- `operate_speed = states('input_number.dyson_speed')|int`
- `operate_oscillate = states('input_boolean.dyson_oscillate')`

### Stable Readback Branch

Runs when:

```jinja
state_attr('input_select.living_room_dyson_io','reading_match_count')|int(default=0)
  >= match_count_threshold + extra_count
```

There are two major paths.

#### If readback says fan is on

If any of power/speed/oscillation differs from target, and:

- `try_count < max_try_count`
- `during_attempt == true`

then:

1. set `supposed_*` to the current `read_*`
2. reset `reading_match_count`
3. update `last_operation`
4. call `script.dyson_send_command` again

This is the retry/convergence path.

Otherwise the automation accepts readback as truth:

1. mark I/O state as `read`
2. set `during_attempt: false`
3. set `last_write_back: now()`
4. copy `read_*` into both `operate_*` and `supposed_*`
5. after `100 ms`, write helpers:
   - `input_boolean.dyson_power = read_power`
   - `input_number.dyson_speed = read_speed`
   - `input_boolean.dyson_oscillate = read_oscillate`

#### If readback says fan is off

When the fan is off, speed is not treated as a reliable observable. The retry condition only compares power:

- if `operate_power != read_power`
- and `try_count < max_try_count`
- and `during_attempt == true`

then retry from current readback.

Otherwise accept readback as truth:

1. mark I/O state as `read`
2. set `during_attempt: false`
3. set `last_write_back: now()`
4. copy power/oscillation readback into `operate_*` and `supposed_*`
5. after `100 ms`, write helpers:
   - `input_boolean.dyson_power = read_power`
   - `input_boolean.dyson_oscillate = read_oscillate`

Notice that speed is not overwritten from `read_speed` in the off path. This preserves the last meaningful speed while the fan is off.

## Consumers

### UI

Living room page uses the public template fan:

- source: [dashboards/main.yaml](/home/myhades/Documents/GitHub/ha-config/dashboards/main.yaml:1806)
- card template: `std_fan`
- entity: `fan.dyson_tower_fan`
- name: `Dyson 落地扇`
- icon: `local:dyson-fan`

More/offline page includes the plug tracker:

- source: [dashboards/main.yaml](/home/myhades/Documents/GitHub/ha-config/dashboards/main.yaml:5615)
- `device_tracker.dyson_fan_plug`

### HomeKit

The template fan is exposed through HomeKit:

- source: [configuration.yaml](/home/myhades/Documents/GitHub/ha-config/configuration.yaml:183)
- included entity: `fan.dyson_tower_fan`

### Living/Dining Automatic Adjust

Script `living_dining_room_automatic_adjust` can turn on the Dyson when the living room is hot or the living-room AC is cooling:

- source: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:565)
- if living-room average temperature is above `26`, or AC state is `cool`:
  - turn on `fan.dyson_tower_fan`
  - set percentage based on temperature:

```jinja
{% set temp=states('sensor.ke_ting_ping_jun_wen_du')|float() %}
{% set diff=((temp-26)*1.5)|abs|int %}
{% set mode=min([diff+2,6])*10 %}
{{ mode }}
```

  - enable oscillation

Its wait logic also includes `fan.dyson_tower_fan` when the room is hot.

### A/C Recommendation Script

Script `frontend_a_c_recommendation` can turn the Dyson on when recommending cooling:

- source: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:641)
- if living-room average temperature is above `29`:
  - turn on living-room AC
  - set cool/25 C/auto fan
  - if Dyson is off:
    - turn on `fan.dyson_tower_fan` at `100%`
    - wait 1 second
    - enable oscillation

### Leaving System

The generic living-room leaving routine includes the Dyson as a generic entity:

- source: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:900)
- `fan.dyson_tower_fan` is turned off as part of living-room generic devices.

## Important Semantics To Preserve

1. The public entity should behave like a normal Home Assistant fan:
   - on/off
   - percentage/speed 1..10
   - oscillation
   - availability

2. IR commands are relative/toggle commands, not absolute commands.
   - Power is a toggle.
   - Oscillation is a toggle.
   - Speed changes are up/down steps.

3. The system must maintain an internal `supposed_*` state.
   - Because IR commands are relative, command planning depends on what the controller currently believes the fan state is.

4. Smart-plug power readings are the only real feedback channel.
   - Stable consecutive decoded readings are required before accepting state.

5. The latest user target should win.
   - Current YAML uses script `restart` plus helper snapshots.
   - A custom integration should use a serialized/cancellable command task or equivalent queue so a new target can supersede an old attempt safely.

6. Retries are bounded.
   - Current YAML retries up to `max_try_count = 2` after a mismatch.

7. Speed is not reliable while off.
   - Do not overwrite stored speed with `0` merely because the fan is off.

8. Power-off should normalize oscillation to off.
   - This is intentional hardware-quirk handling, not a bug.
   - Current YAML assumes a fully off Dyson will next start with oscillation off.

9. Avoid self-trigger loops.
   - Current YAML ignores user-operation triggers for 2 seconds after `last_write_back`.
   - A custom integration should distinguish external target requests from internal state reconciliation.

10. Preserve the post-write tolerance idea.
    - Current YAML requires one more stable reading shortly after a write attempt.

## Suggested Integration Shape

The custom integration can replace the current setup with one `FanEntity` plus an internal coordinator/state machine.

Recommended config options:

- power sensor entity id
- remote entity id
- Broadlink device name, currently `dyson_tower_fan`
- command names:
  - power toggle
  - oscillation toggle
  - speed up
  - speed down
- power signature table
- command interval, default `350 ms`
- stable read threshold, default `2`
- max retries, default `2`
- post-write extra stable read, default enabled

Recommended internal state fields:

- accepted/current state:
  - `power`
  - `speed`
  - `oscillating`
- latest requested target:
  - `target_power`
  - `target_speed`
  - `target_oscillating`
- supposed physical state after commands:
  - `supposed_power`
  - `supposed_speed`
  - `supposed_oscillating`
- decoded readback state:
  - `read_power`
  - `read_speed`
  - `read_oscillating`
- convergence metadata:
  - `during_attempt`
  - `try_count`
  - `reading_match_count`
  - `last_operation`
  - `last_read`
  - `last_write_back`

The integration should ideally expose diagnostic attributes for debugging, but the public HA state should stay clean.

## Current Source Index

- Dyson power plug ESPHome config: [esphome/iot-plug-dyson.yaml](/home/myhades/Documents/GitHub/ha-config/esphome/iot-plug-dyson.yaml:1)
- Template fan: [configuration.yaml](/home/myhades/Documents/GitHub/ha-config/configuration.yaml:255)
- HomeKit include: [configuration.yaml](/home/myhades/Documents/GitHub/ha-config/configuration.yaml:183)
- Dyson state machine automation: [automations.yaml](/home/myhades/Documents/GitHub/ha-config/automations.yaml:3593)
- Readback decoder script: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:736)
- IR command script: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:768)
- Living/dining automatic adjust consumer: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:565)
- A/C recommendation consumer: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:641)
- Leaving-system consumer: [scripts.yaml](/home/myhades/Documents/GitHub/ha-config/scripts.yaml:900)
- Living-room UI card: [dashboards/main.yaml](/home/myhades/Documents/GitHub/ha-config/dashboards/main.yaml:1806)
- Offline UI entry: [dashboards/main.yaml](/home/myhades/Documents/GitHub/ha-config/dashboards/main.yaml:5615)
