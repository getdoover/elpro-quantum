"""Rail decoding tests.

The register values are the ones a QE-R-C4 running off its battery terminals
reported on the bench: no supply, 11.95 V of battery at -0.56 A (negative =
the battery is carrying the unit).
"""

import struct

import pytest

from elpro_quantum_diagnostics import rails


def encode(values):
    """Encode floats the way the Quantum lays them out: high word first."""
    registers = []
    for value in values:
        registers.extend(struct.unpack(">HH", struct.pack(">f", value)))
    return registers


def test_decode_float_block_word_order():
    # 12.0 as big-endian float32 is 0x41400000, high word at the lower address.
    assert rails.decode_float_block([0x4140, 0x0000], 1) == [12.0]


def test_decode_float_block_needs_enough_registers():
    with pytest.raises(rails.RailsError):
        rails.decode_float_block([0x4140], 1)


def test_channel_order_is_supply_v_battery_v_supply_a_battery_a():
    """Order matters more than any other constant here.

    Swapping the middle two would report the battery's voltage as the supply's
    and leave a solar site looking mains-fed.
    """
    assert rails.CH_SUPPLY_VOLTAGE == 6
    assert rails.CH_BATTERY_VOLTAGE == 7
    assert rails.CH_SUPPLY_CURRENT == 8
    assert rails.CH_BATTERY_CURRENT == 9
    assert rails.RAIL_FIRST_CHANNEL == rails.CH_SUPPLY_VOLTAGE
    assert rails.RAIL_CHANNEL_COUNT == 4


async def test_running_on_battery(monkeypatch):
    client = rails.RailsClient()
    registers = encode([0.0, 11.952136, 0.0, -0.563901])

    async def fake_read(address, count):
        assert address == rails.IR_ANALOG_FLOAT_BASE + 2 * rails.CH_SUPPLY_VOLTAGE
        assert count == 8
        return registers

    monkeypatch.setattr(client, "_read_input_registers", fake_read)
    snap = await client.read_rails()

    assert snap.supply_voltage_v == 0.0
    assert snap.battery_voltage_v == pytest.approx(11.952, abs=1e-3)
    assert snap.battery_current_a == pytest.approx(-0.564, abs=1e-3)
    # Negative current on the battery means it is discharging into the unit.
    assert snap.battery_power_w < 0
    assert snap.active_source == "battery"


async def test_running_on_supply(monkeypatch):
    client = rails.RailsClient()
    registers = encode([24.1, 12.8, 0.42, 0.15])

    async def fake_read(address, count):
        return registers

    monkeypatch.setattr(client, "_read_input_registers", fake_read)
    snap = await client.read_rails()

    assert snap.active_source == "supply"
    assert snap.supply_power_w == pytest.approx(24.1 * 0.42, abs=0.01)
    # A positive battery current with a supply present is the charger working.
    assert snap.battery_power_w > 0


async def test_a_floating_supply_rail_does_not_count_as_present(monkeypatch):
    """A few hundred millivolts of leakage on SUP is not a supply."""
    client = rails.RailsClient()
    registers = encode([0.4, 12.2, 0.0, -0.3])

    async def fake_read(address, count):
        return registers

    monkeypatch.setattr(client, "_read_input_registers", fake_read)
    assert (await client.read_rails()).active_source == "battery"


def test_identity_decoders():
    assert rails.decode_serial_number([1225, 201, 1]) == "12252010001"
    assert rails.decode_firmware_version([2, 15, 0, 0]) == "2.15.0.0"
    # Short reads report nothing rather than a mangled string.
    assert rails.decode_serial_number([1225]) is None
    assert rails.decode_firmware_version([2, 15]) is None
