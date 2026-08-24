"""Read the Quantum's supply and battery rails over its local Modbus server.

A Quantum measures both of its power inputs and publishes them as analog
channels alongside the field inputs. They are the only place the supply/battery
split is visible: the charger's sysfs shows the solar side, and the Doover
platform interface serves a single "system voltage" that is already collapsed
to whichever rail is feeding the unit.

Addressing follows the same map as the platform interface's Quantum driver,
which is the authoritative copy and carries the derivation and the bench
verification behind it::

    doover-platform-interface/src/doover_platform_interface/drivers/elpro/quantum/registers.py

Only the handful of constants used here are repeated, so this app does not have
to depend on that package to read four numbers. Everything below is read-only.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
DEFAULT_TIMEOUT = 2.0

#: Input-register base of the float32 analog block (ELPRO doc 38001).
IR_ANALOG_FLOAT_BASE = 8000
REGS_PER_FLOAT = 2

#: Indices into the analog channel table for the two power rails. AI1-6 are the
#: field inputs at 0-5; the system telemetry follows.
CH_SUPPLY_VOLTAGE = 6
CH_BATTERY_VOLTAGE = 7
CH_SUPPLY_CURRENT = 8
CH_BATTERY_CURRENT = 9

#: One read covers all four, keeping them mutually consistent.
RAIL_FIRST_CHANNEL = CH_SUPPLY_VOLTAGE
RAIL_CHANNEL_COUNT = 4

#: Above this the SUP terminals are considered energised, which is how the unit
#: itself decides which rail is carrying it. Matches the platform interface.
SUPPLY_PRESENT_V = 1.0

#: Serial number and firmware version, read once (doc 30494-30500).
IR_SERIAL_BASE = 493
IR_SERIAL_COUNT = 3
IR_FIRMWARE_BASE = 496
IR_FIRMWARE_COUNT = 4


class RailsError(Exception):
    """The Modbus read failed, or the server is unreachable."""


def decode_float_block(registers: list[int], count: int) -> list[float]:
    """Decode ``count`` big-endian float32 values from consecutive registers.

    High-order word at the lower address, despite the HowTo's wording; verified
    on hardware by the platform interface team.
    """
    if len(registers) < REGS_PER_FLOAT * count:
        raise RailsError(
            f"need {REGS_PER_FLOAT * count} registers to decode {count} floats, "
            f"got {len(registers)}"
        )
    out = []
    for i in range(count):
        hi, lo = registers[REGS_PER_FLOAT * i], registers[REGS_PER_FLOAT * i + 1]
        out.append(struct.unpack(">f", struct.pack(">HH", hi, lo))[0])
    return out


def decode_serial_number(registers: list[int]) -> str | None:
    """Render doc 30494-30496 as decimal digit groups.

    Formatting is provisional - ELPRO documents the three words as digit groups
    but the result has not been checked against a labelled unit.
    """
    if len(registers) < IR_SERIAL_COUNT:
        return None
    first, middle, last = registers[:IR_SERIAL_COUNT]
    return f"{first:04d}{middle:03d}{last:04d}"


def decode_firmware_version(registers: list[int]) -> str | None:
    if len(registers) < IR_FIRMWARE_COUNT:
        return None
    return ".".join(str(r) for r in registers[:IR_FIRMWARE_COUNT])


@dataclass
class RailsSnapshot:
    """The unit's two power rails, plus which one is carrying it."""

    supply_voltage_v: float | None = None
    supply_current_a: float | None = None
    supply_power_w: float | None = None

    battery_voltage_v: float | None = None
    #: Sign convention is the unit's own: negative means the battery is
    #: supplying the load, positive means it is being charged.
    battery_current_a: float | None = None
    battery_power_w: float | None = None

    #: "supply" or "battery" - which rail the unit is actually running from.
    active_source: str | None = None

    serial_number: str | None = None
    firmware_version: str | None = None


class RailsClient:
    """One serialised, read-only Modbus TCP connection to a Quantum."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        unit_id: int = DEFAULT_UNIT_ID,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout

        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"<RailsClient {self.host}:{self.port} unit={self.unit_id}>"

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    async def _read_input_registers(self, address: int, count: int) -> list[int]:
        async with self._lock:
            if self._client is None:
                self._client = AsyncModbusTcpClient(
                    host=self.host, port=self.port, timeout=self.timeout
                )
            if not self._client.connected:
                await self._client.connect()
            if not self._client.connected:
                self._client = None
                # Not the default on a factory unit, and the most likely reason
                # for this app to find nothing on an otherwise healthy Quantum.
                raise RailsError(
                    f"cannot reach the Modbus TCP server at {self.host}:{self.port} - "
                    "check it is enabled on the unit (web config: Modbus Server = 1, "
                    "enabled, then 'Save and Activate Changes')"
                )
            try:
                result = await self._client.read_input_registers(
                    address, count=count, slave=self.unit_id
                )
            except Exception as e:
                # Drop the client rather than reuse a possibly half-closed
                # socket on the next poll.
                self._client = None
                raise RailsError(f"read_input_registers({address}, {count}) failed: {e}") from e

        if result.isError():
            raise RailsError(f"read_input_registers({address}, {count}): {result}")
        return list(result.registers)

    async def read_rails(self) -> RailsSnapshot:
        registers = await self._read_input_registers(
            IR_ANALOG_FLOAT_BASE + REGS_PER_FLOAT * RAIL_FIRST_CHANNEL,
            REGS_PER_FLOAT * RAIL_CHANNEL_COUNT,
        )
        supply_v, battery_v, supply_a, battery_a = decode_float_block(
            registers, RAIL_CHANNEL_COUNT
        )

        snap = RailsSnapshot(
            supply_voltage_v=round(supply_v, 3),
            supply_current_a=round(supply_a, 3),
            supply_power_w=round(supply_v * supply_a, 2),
            battery_voltage_v=round(battery_v, 3),
            battery_current_a=round(battery_a, 3),
            battery_power_w=round(battery_v * battery_a, 2),
        )
        snap.active_source = "supply" if supply_v > SUPPLY_PRESENT_V else "battery"
        return snap

    async def read_identity(self) -> tuple[str | None, str | None]:
        """Return ``(serial_number, firmware_version)``; read once at startup."""
        serial = decode_serial_number(
            await self._read_input_registers(IR_SERIAL_BASE, IR_SERIAL_COUNT)
        )
        firmware = decode_firmware_version(
            await self._read_input_registers(IR_FIRMWARE_BASE, IR_FIRMWARE_COUNT)
        )
        return serial, firmware
