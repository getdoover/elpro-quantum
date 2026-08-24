# ELPRO Quantum Diagnostics

Diagnostics for an **ELPRO Quantum** (EL-QE-E / QE-R-C4), covering the parts of
the unit that are specific to ELPRO hardware and that no generic Doover app can
see: the licensed-band **radio**, the solar **charger / MPPT** controller, and
the unit's **supply and battery rails**.

It is a read-only app. It runs *on* the Quantum, in the ordinary Doover app
container, and never writes to the unit's configuration or its radio.

## What it shows

| Section | What is in it |
|---|---|
| Headline | Last-packet RSSI, signal margin, radio state, transmit success, supply and battery volts, solar input watts, charging |
| Radio | Current and background RSSI, VSWR, PA temperature, mesh mode, driver state, radio uptime, last reset cause, last error, transmitter/receiver enabled |
| Radio Configuration | Transmit and receive frequency, channel bandwidth, transmit power and its ceiling, antenna gain, data rate, modulation, encoding, radio address |
| Radio Traffic | Received unicast/broadcast, CRC errors, transmitted OK/failed, retries and retry rate, channel occupancy for transmit, receive and errors |
| Power | Which rail is carrying the unit, supply current and power, battery current and power |
| Charger / MPPT | Charger status, MPPT enabled, panel type, input connected, battery detected, input volts/amps, charge voltage setpoint, max charge current, input current limit, reverse mode |
| Unit | Model, product name, radio type, serial number, firmware, and the radio's own firmware/hardware/serial |

Warnings are raised for: no radio found, radio alarm or a radio that never
initialised, a weak signal (threshold configurable, default −100 dBm), the unit
running on battery with no supply present, and an unreadable Modbus server.

Rather more is published as tags than is shown on the dashboard — the per-source
counters, the Linux driver's own transmit/receive statistics, the RSSI error
level, and channel holdoff. They are there for charting and for the API when a
link is being chased down.

## Where the data comes from

Four independent sources, each individually switchable in config. A source that
fails clears only its own tags, so a Quantum with no radio fitted or with its
Modbus server switched off still fills in everything else.

### 1. Radio — `/sys/class/ieee80211/*/radio/`

The radio module is a separate transceiver on an internal serial link, not a
Linux 802.11 device, but ELPRO's driver registers it under `ieee80211` and hangs
~118 plain-text attributes off a `radio/` subdirectory. Reads cost about 5 ms
each; the app reads a curated ~60 of them.

Attributes that are command triggers rather than values — `RadioReset`,
`FirmwareUpgrade`, `Utilisation.Reset`, `drv_discover` — are deliberately never
touched.

> **Host networking is required.** `/sys/class/ieee80211` is network-namespace
> scoped: in the host namespace the radio is visible with no bind mount and no
> privilege; in any other namespace the directory is empty. Doover deploys app
> containers with `network_mode: host`, so this works out of the box — but it is
> also why running the app anywhere else silently finds no radio. Verified both
> ways on a QE-R-C4.

### 2. Charger / MPPT — `/sys/class/power_supply/` and `/sys/class/hwmon/`

The charger is a TI **BQ25756** on the unit's I2C bus, driven by ELPRO's
out-of-tree `bq25756.ko`. It is what implements the web portal's "Enable MPPT"
option.

Only the charge *state* is taken from the power-supply class (`status`,
`charge_type`, `online`, `present`). Its numeric attributes are deliberately
**not** published: the power-supply class specifies µV/µA and this driver plainly
does not follow it — a stock unit reports `constant_charge_voltage` as `16`, i.e.
volts. Publishing those under an unproven unit would be worse than not
publishing them.

Live measurements come from the charger's **hwmon** node instead, where mV and
mA *are* specified, so they convert exactly. The configured limits come from the
unit's own configuration (below), where they carry documented engineering units.

### 3. Unit configuration — the config daemon on `127.0.0.1:4783`

Everything the web portal and the `cfg` CLI show comes from a config daemon on
loopback, speaking line-oriented plain text, one request per connection:

```
-> read E2Config.PowerSupply.ChargeVoltage\n
<- poll:\nE2Config.PowerSupply.ChargeVoltage 13.8\n
```

This is where the MPPT enable flag, the panel type, the charge and reverse-mode
setpoints and the unit's identity come from. The protocol is not documented by
ELPRO — it was derived by tracing `cfg read` — so it is treated as best-effort:
any failure degrades to blank fields.

The app only ever **reads**. That is not incidental: on this firmware `cfg write`
silently drops every `lock="locked"` item from `config.conf`, including the
unit's own product identity. See `elpro/quantum-default-route-loss.md` §3.

### 4. Supply and battery rails — Modbus TCP on `127.0.0.1:502`

The Quantum measures both of its power inputs and publishes them as analog
channels 6–9 alongside the field inputs. This is the only place the
supply-versus-battery split is visible: the charger's sysfs shows the solar side,
and Doover's platform interface serves a single "system voltage" already
collapsed to whichever rail is feeding the unit.

Addressing follows the platform interface's Quantum driver, which is the
authoritative copy and carries the derivation and the bench verification:

```
doover-platform-interface/src/doover_platform_interface/drivers/elpro/quantum/registers.py
```

Only the few constants needed here are repeated, so this app does not depend on
that package to read four numbers.

> The Modbus server is **off on a factory Quantum**. Enable it in the web config
> (Modbus Server = 1, enabled, then *Save and Activate Changes*) or this section
> stays blank and raises its warning.

## Values that are inferred, not verified

Stated plainly, because a diagnostics app that quietly guesses is worse than one
that says where it is guessing:

- **Charger input voltage and current** are hwmon `in0_input` and `curr1_input`,
  the charger's only exported ADC channels. They are labelled as the *input*
  (solar/supply) side because they read zero on a unit whose battery rail is live
  at 11.95 V and whose supply terminals are empty. That is consistent with them
  being VAC/IAC, but it has not been confirmed against a datasheet or a meter.
- **`PanelType`** is an index into the web portal's solar-panel dropdown. Only
  index 0 ("Default") is confirmed; anything else is passed through as its raw
  index rather than guessed at.
- **The unit serial number** is rendered as the decimal digit groups ELPRO
  documents, but has not been checked against a labelled unit. The same caveat is
  carried in the platform interface driver.

## Configuration

| Setting | Default | Notes |
|---|---|---|
| Poll Interval | 30 s | Little to gain below ~10 s |
| Read Radio | on | Needs host networking, see above |
| Radio Device | auto | `phy1`, or a full sysfs path, for a two-radio unit |
| Read Charger / MPPT | on | |
| Read Unit Configuration | on | Config daemon host and port are configurable |
| Read Supply and Battery Rails | on | Modbus host and port are configurable |
| Weak Signal Threshold | −100 dBm | Judged on the last decoded packet, not the current receiver level |

## Development

```bash
uv sync --all-groups
uv run pytest tests -v
uv run export-config      # write config_schema into doover_config.json
uv run export-ui          # write ui_schema into doover_config.json
docker build -t elpro-quantum-diagnostics .
```

CI is the shared Doover app workflow (`.github/workflows/doover-app.yml`), which
lints, tests, builds the multi-arch image and publishes the app on a push to
`main`.

### Running without a Quantum

`simulators/quantum_sim.py` stands in for a unit, emulating all four interfaces:
it builds a sysfs tree, serves the config daemon's line protocol, and runs a
Modbus TCP server with the analog float block populated.

```bash
# terminal 1 - ports under 1024 need privilege, so move Modbus up
uv run python simulators/quantum_sim.py --scenario linked --modbus-port 15020

# terminal 2
QUANTUM_SYSFS_ROOT=/tmp/quantum-sim uv run doover-app-run
```

`QUANTUM_SYSFS_ROOT` is the only concession the app makes to being run off
hardware: the radio and charger are read through absolute sysfs paths, so
without a prefix there would be no way to exercise them anywhere but on a
Quantum. Unset — the normal case — it reads the real `/sys`.

Two scenarios:

- **`idle`** (default) — the lab unit as found: radio up on 472.1 MHz but having
  never heard a peer, MPPT disabled, running off its battery at ~11.95 V with
  nothing on the supply terminals. This is the one where the RSSI `999` and VSWR
  `-999` sentinels appear.
- **`linked`** — a live radio link with retries and a real signal, a solar array
  charging the battery, and mains on SUP. Signal, temperature and the counters
  drift every few seconds so a watched dashboard actually moves.

### Testing

The tests need no Quantum and no network. The radio, charger and config-server
fixtures are verbatim captures from a QE-R-C4 (E2-455 radio module, firmware
2.52); the Modbus decoding is tested against encoded register blocks; and
`tests/test_simulator.py` round-trips every simulator scenario back through the
real readers, so a scenario that drifts out of step with them fails the build
rather than quietly breaking the development loop.
