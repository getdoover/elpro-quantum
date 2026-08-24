"""The simulator has to stay in step with the readers.

It is the only way to exercise this app without a Quantum, so a scenario that
silently stops matching what the readers expect - a renamed attribute, a moved
register - would take the whole development loop with it. These tests round-trip
each scenario back through the real readers.

Loaded by path because ``simulators/`` is a script directory, not a package.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from elpro_quantum_diagnostics import charger, radio, sysfs
from elpro_quantum_diagnostics.config_server import ConfigServerClient
from elpro_quantum_diagnostics.rails import RailsClient

SIM_PATH = Path(__file__).parents[1] / "simulators" / "quantum_sim.py"


def load_sim():
    spec = importlib.util.spec_from_file_location("quantum_sim", SIM_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sim = load_sim()


@pytest.fixture(params=sorted(sim.SCENARIOS))
def scenario_root(request, tmp_path, monkeypatch):
    """A built sysfs tree for each scenario, with the app pointed at it."""
    root = tmp_path / "quantum-sim"
    sim.write_sysfs(root, sim.SCENARIOS[request.param])
    monkeypatch.setenv(sysfs.SYSFS_ROOT_ENV, str(root))
    return request.param, root


def test_readers_find_the_simulated_hardware(scenario_root):
    name, _ = scenario_root

    snap = radio.read()
    assert snap is not None, f"{name}: no radio found in the simulated tree"
    # phy0 exists in the tree without a radio/ directory, so finding phy1 means
    # discovery picked the radio rather than the first phy it saw.
    assert snap.path.endswith("phy1/radio")
    assert snap.initialised is True

    charge = charger.read()
    assert charge is not None, f"{name}: no charger found in the simulated tree"
    assert charge.status


def test_every_attribute_the_app_reads_is_simulated(scenario_root):
    """A reader asking for something the simulator omits reads as blank."""
    _, root = scenario_root
    written = {p.name for p in (root / "sys/class/ieee80211/phy1/radio").iterdir()}
    assert set(radio.RADIO_ATTRS) <= written


def test_idle_scenario_matches_the_lab_unit(tmp_path, monkeypatch):
    """The default scenario is the unit as found, sentinels and all."""
    root = tmp_path / "quantum-sim"
    sim.write_sysfs(root, sim.SCENARIOS["idle"])
    monkeypatch.setenv(sysfs.SYSFS_ROOT_ENV, str(root))

    snap = radio.read()
    assert snap.rssi_dbm == -113
    # Never having heard a peer, so no last-packet level and no VSWR.
    assert snap.rssi_last_dbm is None
    assert snap.vswr is None
    assert charger.read().charging is False


def test_linked_scenario_exercises_what_idle_leaves_blank(tmp_path, monkeypatch):
    root = tmp_path / "quantum-sim"
    sim.write_sysfs(root, sim.SCENARIOS["linked"])
    monkeypatch.setenv(sysfs.SYSFS_ROOT_ENV, str(root))

    snap = radio.read()
    assert snap.rssi_last_dbm is not None
    assert snap.snr_db is not None
    assert snap.vswr is not None
    assert snap.tx_success_pct is not None
    assert snap.util_tx_pct > 0

    charge = charger.read()
    assert charge.charging is True
    assert charge.input_power_w > 0


def test_sysfs_root_override_is_what_makes_this_possible(tmp_path, monkeypatch):
    """Without the override the readers must go back to the real /sys."""
    monkeypatch.delenv(sysfs.SYSFS_ROOT_ENV, raising=False)
    assert sysfs.path("/sys/class/hwmon") == "/sys/class/hwmon"

    monkeypatch.setenv(sysfs.SYSFS_ROOT_ENV, str(tmp_path) + "/")
    # A trailing slash in the environment must not double up in the path.
    assert sysfs.path("/sys/class/hwmon") == f"{tmp_path}/sys/class/hwmon"


async def test_simulated_config_daemon_speaks_the_real_protocol(unused_tcp_port):
    values = sim.SCENARIOS["idle"]["config"]
    server = asyncio.create_task(sim.serve_config(unused_tcp_port, values))
    await asyncio.sleep(0.1)
    try:
        client = ConfigServerClient("127.0.0.1", unused_tcp_port)
        assert await client.read("E2Config.PowerSupply.ChargeVoltage") == "13.8"
        assert await client.read("E2Config.webpage.MODEL_NAME") == "QE-R-C4"
        assert await client.read("E2Config.no.such.key") is None
    finally:
        server.cancel()


def test_simulated_registers_decode_back_to_the_scenario():
    """The register layout has to survive the app's own decoder.

    Exercised through `RailsClient.read_rails` rather than against the live
    server, so it does not need a port: the encoding is the part that can drift.
    """
    rails = (24.1, 13.62, 0.41, 1.85)
    registers = sim.build_input_registers(rails)

    client = RailsClient()

    async def fake_read(address, count):
        return registers[address : address + count]

    client._read_input_registers = fake_read

    snap = asyncio.run(client.read_rails())
    assert snap.supply_voltage_v == pytest.approx(24.1, abs=1e-3)
    assert snap.battery_voltage_v == pytest.approx(13.62, abs=1e-3)
    assert snap.active_source == "supply"

    serial, firmware = asyncio.run(client.read_identity())
    assert serial == "12252010001"
    assert firmware == "2.15.0.0"
