"""A stand-in for an ELPRO Quantum, for developing without a unit on the bench.

Emulates all four of the interfaces the app reads:

* a **sysfs tree** under a temporary directory - the radio's ``radio/``
  attribute directory and the BQ25756's power-supply and hwmon nodes;
* the **configuration daemon**'s line protocol on TCP 4783;
* the **Modbus TCP server** on 502, with the analog float block populated so
  the supply and battery rails decode.

Run it, then point the app at it::

    python simulators/quantum_sim.py
    QUANTUM_SYSFS_ROOT=/tmp/quantum-sim uv run doover-app-run

The default scenario is the lab unit as found: radio up on 472.1 MHz having
never heard a peer, MPPT disabled, running off its battery at ~11.95 V with
nothing on the supply terminals. ``--scenario linked`` switches to a unit with a
live radio link, a solar array charging the battery, and mains on SUP - which is
what exercises the parts of the dashboard the idle unit leaves blank.

Ports below 1024 need privilege; pass ``--modbus-port`` to move Modbus somewhere
unprivileged and set the app's "Modbus Server Port" to match.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import shutil
import struct
import time
from pathlib import Path

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("quantum-sim")

DEFAULT_ROOT = Path("/tmp/quantum-sim")
CONFIG_PORT = 4783
MODBUS_PORT = 502

# Matches rails.py; repeated rather than imported so the simulator stays a
# standalone script that can be run before the package is installed.
IR_ANALOG_FLOAT_BASE = 8000
IR_SERIAL_BASE = 493
IR_FIRMWARE_BASE = 496
ANALOG_CHANNEL_COUNT = 14


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

#: Attribute values for the radio's sysfs directory. Verbatim from a QE-R-C4
#: with an E2-455 module on firmware 2.52, including the RSSI 999 and VSWR -999
#: sentinels that a radio which has never received a packet reports.
IDLE_RADIO = {
    "FirmwareVersion": "E2_455 radio application 2.52 20230202",
    "HardwareVersion": "400-480MHz 10Watt 25kHz Channel R1.4 mod J",
    "SoftwareVersion": "E2-455 2.52 [Feb  2 2023 11:13:19] (10012)",
    "Calibration.SerialNumber": "30081638",
    "Calibration.Date": "NOT SET",
    "Configuration.Address.Primary": "00:12:AF:13:01:B1",
    "Radio.Band": "445",
    "Radio.Bandwidth": "12.500",
    "Radio.MinimumFrequency": "400.000000",
    "Radio.MaximumFrequency": "480.000000",
    "Radio.MaximumPower": "40",
    "Configuration.Transmit.Frequency": "472.100000",
    "Configuration.Receive.Frequency": "472.100000",
    "Configuration.Transmit.Power": "20",
    "Configuration.Transmit.MaxPower.Conducted": "40",
    "Configuration.Transmit.AntennaGain": "0",
    "Configuration.Modulation": "QAM",
    "Configuration.BaseEncoding": "4QAM",
    "Configuration.Data.Rate": "9.000",
    "Radio.Initialised": "1",
    "Radio.Alarm": "0",
    "Radio.InitialisationError": "",
    "Radio.PATemperature": "32",
    "RadioState": "application",
    "RadioUptime": "  10:20:33:05",
    "ResetType": "Brown-out 3.3V",
    "LastReadError": "No Read Error",
    "LastWriteError": "Statistics.Utilisation.Reset (83) : Data out of range",
    "Transmit": "ENABLE",
    "Receive": "ENABLE",
    "Statistics.RSSI.Current": "-113",
    "Statistics.RSSI.Background": "-113",
    "Statistics.RSSI.Last": "999",
    "Statistics.RSSI.Errors": "-112",
    "Statistics.VSWR.Last": "-999",
    "Statistics.Receive.Unicast.ToMe": "0",
    "Statistics.Receive.Unicast.ToOther": "0",
    "Statistics.Receive.Multicast": "0",
    "Statistics.Receive.Error.CRC": "0",
    "Statistics.Receive.Unsupported": "0",
    "Statistics.Receive.Acknowledgement.ToMe": "0",
    "Statistics.Transmit.Unicast.OK": "0 0 0 0 0 0 0",
    "Statistics.Transmit.Unicast.Failed": "0 0 0 0 0 0 0",
    "Statistics.Transmit.Multicast": "0 0 0 0 0 0 0",
    "Statistics.Transmit.Acknowledgement": "0",
    "Utilisation.TotalTime": "502",
    "Utilisation.Transmit.Data.First": "0",
    "Utilisation.Transmit.Data.Retry": "0",
    "Utilisation.Transmit.Control.First": "0",
    "Utilisation.Transmit.Control.Retry": "0",
    "Utilisation.Receive.Data.ToMe": "0",
    "Utilisation.Receive.Data.ToOther": "0",
    "Utilisation.Receive.Control.ToMe": "0",
    "Utilisation.Receive.Control.ToOther": "0",
    "Utilisation.Receive.Error": "0",
    "Utilisation.Holdoff": "0",
    "drv_mode": "MESH",
    "drv_state": "AWAKE",
    "drv_txpower": "30",
    "drv_txretries": "3",
    "drv_restarted": "0",
    "drv_stats": (
        "\nRadio Driver Statistics:\ntx_good    0\ntx_bad     0\ntx_timeout 0\n"
        "tx_failed  0\ntx_cancel  0\nrx_packets  0\nrx_dropped  0\nrx_bytes  0\n"
    ),
}

IDLE_CHARGER = {
    "model_name": "BQ25756",
    "manufacturer": "Texas Instruments",
    "status": "Not charging",
    "charge_type": "Unknown",
    "online": "1",
    "present": "0",
}

IDLE_CONFIG = {
    "E2Config.PowerSupply.Enable": "0",
    "E2Config.PowerSupply.PanelType": "0",
    "E2Config.PowerSupply.Enable_man": "0",
    "E2Config.PowerSupply.ChargeVoltage": "13.8",
    "E2Config.PowerSupply.MaxChargeCurrent": "4.2",
    "E2Config.PowerSupply.InputCurrentLimit": "2",
    "E2Config.PowerSupply.Enable_reverse": "0",
    "E2Config.PowerSupply.ReverseVoltage": "24.0",
    "E2Config.PowerSupply.ReverseCurrent": "2.0",
    "E2Config.webpage.MODEL_NAME": "QE-R-C4",
    "E2Config.identification.ProductName": "QE-E",
    "E2Config.identification.Radio": "USBWIFI",
}

#: (supply V, battery V, supply A, battery A). Battery current is negative when
#: the battery is carrying the unit.
IDLE_RAILS = (0.0, 11.952, 0.0, -0.564)


def linked_scenario():
    """A unit with a working link, solar charging, and mains on SUP."""
    radio = dict(IDLE_RADIO)
    radio.update(
        {
            "Statistics.RSSI.Current": "-108",
            "Statistics.RSSI.Background": "-114",
            "Statistics.RSSI.Last": "-89",
            "Statistics.VSWR.Last": "1.32",
            "Radio.PATemperature": "41",
            "Statistics.Receive.Unicast.ToMe": "18422",
            "Statistics.Receive.Unicast.ToOther": "3310",
            "Statistics.Receive.Multicast": "774",
            "Statistics.Receive.Error.CRC": "63",
            "Statistics.Transmit.Unicast.OK": "16003 1204 214 39 0 0 0",
            "Statistics.Transmit.Unicast.Failed": "0 0 0 0 88 0 0",
            "Statistics.Transmit.Multicast": "512 21 0 0 0 0 0",
            "Utilisation.TotalTime": "864000",
            "Utilisation.Transmit.Data.First": "31000",
            "Utilisation.Transmit.Data.Retry": "4200",
            "Utilisation.Receive.Data.ToMe": "52000",
            "Utilisation.Receive.Data.ToOther": "9100",
            "Utilisation.Receive.Error": "1300",
            "drv_stats": (
                "\nRadio Driver Statistics:\ntx_good    16003\ntx_bad     88\n"
                "tx_timeout 2\ntx_failed  88\ntx_cancel  0\nrx_packets  18422\n"
                "rx_dropped  63\nrx_bytes  1204188\n"
            ),
        }
    )

    charger = dict(IDLE_CHARGER)
    charger.update({"status": "Charging", "charge_type": "Fast", "present": "1"})

    config = dict(IDLE_CONFIG)
    config.update(
        {
            "E2Config.PowerSupply.Enable": "1",
            "E2Config.PowerSupply.Enable_man": "1",
        }
    )

    return {
        "radio": radio,
        "charger": charger,
        "config": config,
        # Mains on SUP carrying the unit, battery taking a charge.
        "rails": (24.1, 13.62, 0.41, 1.85),
        # 18.4 V at 2.5 A off the array.
        "hwmon": (18400, 2500),
    }


SCENARIOS = {
    "idle": {
        "radio": IDLE_RADIO,
        "charger": IDLE_CHARGER,
        "config": IDLE_CONFIG,
        "rails": IDLE_RAILS,
        "hwmon": (0, 0),
    },
    "linked": linked_scenario(),
}


# --------------------------------------------------------------------------
# Fake sysfs
# --------------------------------------------------------------------------


def write_sysfs(root: Path, scenario: dict) -> None:
    """Build the sysfs tree the app expects, under ``root``.

    Laid out at the real absolute paths so the only difference from a Quantum
    is the prefix, which ``QUANTUM_SYSFS_ROOT`` supplies.
    """
    if root.exists():
        shutil.rmtree(root)

    # phy0 has no radio/ subdirectory, exactly as on a real unit - it is what
    # proves the app picks the radio out rather than taking the first phy.
    (root / "sys/class/ieee80211/phy0").mkdir(parents=True)

    radio_dir = root / "sys/class/ieee80211/phy1/radio"
    radio_dir.mkdir(parents=True)
    for name, value in scenario["radio"].items():
        (radio_dir / name).write_text(value + "\n")

    charger_dir = root / "sys/class/power_supply/bq25756@1-006b"
    charger_dir.mkdir(parents=True)
    for name, value in scenario["charger"].items():
        (charger_dir / name).write_text(value + "\n")

    hwmon_dir = root / "sys/class/hwmon/hwmon0"
    hwmon_dir.mkdir(parents=True)
    millivolts, milliamps = scenario["hwmon"]
    (hwmon_dir / "name").write_text("bq25756@1_006b\n")
    (hwmon_dir / "in0_input").write_text(f"{millivolts}\n")
    (hwmon_dir / "curr1_input").write_text(f"{milliamps}\n")

    log.info("sysfs tree written to %s", root)


# --------------------------------------------------------------------------
# Fake configuration daemon
# --------------------------------------------------------------------------


async def serve_config(port: int, values: dict[str, str]) -> None:
    """Answer ``read <key>`` the way the unit's config daemon does.

    Including its two distinct "nothing here" replies: ``not-found`` for an
    unknown key, and the key with an empty value for one that is simply unset.
    """

    async def handle(reader, writer):
        peer = writer.get_extra_info("peername")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                command, _, key = line.decode(errors="replace").strip().partition(" ")
                if command != "read":
                    writer.write(b"poll:\nnot-found\n")
                elif key in values:
                    writer.write(f"poll:\n{key} {values[key]}\n".encode())
                else:
                    writer.write(b"poll:\nnot-found\n")
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            log.debug("config client %s gone", peer)
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    log.info("config daemon listening on 127.0.0.1:%s", port)
    async with server:
        await server.serve_forever()


# --------------------------------------------------------------------------
# Fake Modbus server
# --------------------------------------------------------------------------


def encode_float(value: float) -> list[int]:
    """Big-endian float32, high word first - the Quantum's layout."""
    return list(struct.unpack(">HH", struct.pack(">f", value)))


def build_input_registers(rails: tuple[float, float, float, float]) -> list[int]:
    """Lay out the input-register space the app reads from."""
    # Sized to cover the float block; everything not set stays zero, which is
    # what the unused analog channels read anyway.
    registers = [0] * (IR_ANALOG_FLOAT_BASE + 2 * ANALOG_CHANNEL_COUNT)

    # Channels 0-5 are the field analog inputs; 6-9 are the two power rails.
    channels = [0.0] * 6 + list(rails) + [0.0] * 4
    for index, value in enumerate(channels):
        base = IR_ANALOG_FLOAT_BASE + 2 * index
        registers[base : base + 2] = encode_float(value)

    registers[IR_SERIAL_BASE : IR_SERIAL_BASE + 3] = [1225, 201, 1]
    registers[IR_FIRMWARE_BASE : IR_FIRMWARE_BASE + 4] = [2, 15, 0, 0]
    return registers


async def serve_modbus(port: int, rails: tuple[float, float, float, float]) -> None:
    registers = build_input_registers(rails)
    # The blocks start at 1, not 0. pymodbus 3.9's slave context adds 1 to
    # every incoming address before it reaches the datastore, so a block based
    # at 0 answers one register low - which the Quantum does not do: its server
    # takes wire addresses literally. Basing the block at 1 cancels the offset.
    context = ModbusServerContext(
        slaves=ModbusSlaveContext(
            ir=ModbusSequentialDataBlock(1, registers),
            di=ModbusSequentialDataBlock(1, [0] * 16),
            co=ModbusSequentialDataBlock(1, [0] * 16),
            hr=ModbusSequentialDataBlock(1, [0] * 16),
        ),
        single=True,
    )
    log.info("modbus server listening on 127.0.0.1:%s", port)
    await StartAsyncTcpServer(context=context, address=("127.0.0.1", port))


# --------------------------------------------------------------------------
# Drift, so a watched dashboard actually moves
# --------------------------------------------------------------------------


async def drift(root: Path, scenario_name: str, period: float) -> None:
    """Nudge the live values every ``period`` seconds.

    Only the measurements that genuinely move on a real unit: RSSI, PA
    temperature, the counters, and the charger's input. A dashboard that never
    changes hides bugs in the parts of the app that only run on a change.
    """
    if scenario_name != "linked":
        return

    started = time.monotonic()
    radio_dir = root / "sys/class/ieee80211/phy1/radio"
    hwmon_dir = root / "sys/class/hwmon/hwmon0"
    rx_count = 18422

    while True:
        await asyncio.sleep(period)
        elapsed = time.monotonic() - started
        wobble = math.sin(elapsed / 30.0)

        (radio_dir / "Statistics.RSSI.Last").write_text(f"{-89 + 6 * wobble:.0f}\n")
        (radio_dir / "Statistics.RSSI.Current").write_text(f"{-108 + 4 * wobble:.0f}\n")
        (radio_dir / "Radio.PATemperature").write_text(f"{41 + 3 * wobble:.0f}\n")

        rx_count += 7
        (radio_dir / "Statistics.Receive.Unicast.ToMe").write_text(f"{rx_count}\n")

        # A solar array on a partly cloudy day.
        milliamps = max(0, int(2500 + 900 * wobble))
        (hwmon_dir / "curr1_input").write_text(f"{milliamps}\n")


# --------------------------------------------------------------------------


async def run(args) -> None:
    scenario = SCENARIOS[args.scenario]
    root = Path(args.root)
    write_sysfs(root, scenario)

    log.info(
        "Run the app against this simulator with:\n"
        "    QUANTUM_SYSFS_ROOT=%s uv run doover-app-run",
        root,
    )

    await asyncio.gather(
        serve_config(args.config_port, scenario["config"]),
        serve_modbus(args.modbus_port, scenario["rails"]),
        drift(root, args.scenario, args.drift_period),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default="idle",
        help="'idle' is the lab unit as found; 'linked' has a live radio link and solar.",
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="Where to build the stand-in sysfs tree. Rebuilt from scratch on start.",
    )
    parser.add_argument("--config-port", type=int, default=CONFIG_PORT)
    parser.add_argument(
        "--modbus-port",
        type=int,
        default=MODBUS_PORT,
        help="502 needs privilege; move it and set the app's Modbus port to match.",
    )
    parser.add_argument("--drift-period", type=float, default=5.0)
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
