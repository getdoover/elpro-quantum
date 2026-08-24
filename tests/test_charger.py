"""Charger tests, driven off a fake sysfs tree.

Values are the ones a stock QE-R-C4 reports with nothing on its solar input:
charger online, no battery detected, both ADC channels reading zero.
"""

from elpro_quantum_diagnostics import charger


def make_charger(tmp_path, **overrides):
    """Build a power_supply directory that looks like the real one."""
    attrs = {
        "model_name": "BQ25756",
        "manufacturer": "Texas Instruments",
        "status": "Not charging",
        "charge_type": "Unknown",
        "online": "1",
        "present": "0",
        # Deliberately non-standard units - the driver reports volts here where
        # the power-supply class specifies microvolts. Nothing reads these.
        "constant_charge_voltage": "16",
        "constant_charge_current": "1600",
    }
    attrs.update(overrides)

    path = tmp_path / "power_supply" / "bq25756@1-006b"
    path.mkdir(parents=True)
    for name, value in attrs.items():
        (path / name).write_text(value + "\n")
    return path


def make_hwmon(tmp_path, millivolts="0", milliamps="0"):
    path = tmp_path / "hwmon" / "hwmon0"
    path.mkdir(parents=True)
    (path / "name").write_text("bq25756@1_006b\n")
    (path / "in0_input").write_text(millivolts + "\n")
    (path / "curr1_input").write_text(milliamps + "\n")
    return path


def test_idle_charger(tmp_path):
    snap = charger.parse(str(make_charger(tmp_path)), str(make_hwmon(tmp_path)))

    assert snap.status == "Not charging"
    assert snap.charging is False
    assert snap.input_present is True
    assert snap.battery_detected is False
    assert snap.input_voltage_v == 0.0
    assert snap.input_power_w == 0.0


def test_charging_from_solar(tmp_path):
    path = make_charger(tmp_path, status="Charging", present="1", charge_type="Fast")
    # hwmon is specified in mV / mA, so 18.4 V at 2.5 A.
    hwmon = make_hwmon(tmp_path, millivolts="18400", milliamps="2500")

    snap = charger.parse(str(path), str(hwmon))

    assert snap.charging is True
    assert snap.battery_detected is True
    assert snap.input_voltage_v == 18.4
    assert snap.input_current_a == 2.5
    assert snap.input_power_w == 46.0


def test_without_hwmon_the_state_is_still_reported(tmp_path):
    snap = charger.parse(str(make_charger(tmp_path)), None)

    assert snap.status == "Not charging"
    assert snap.input_voltage_v is None
    assert snap.input_power_w is None


def test_discovery_matches_on_model_name(tmp_path, monkeypatch):
    make_charger(tmp_path)
    # A second, unrelated supply that must not be picked up.
    other = tmp_path / "power_supply" / "usb"
    other.mkdir(parents=True)
    (other / "model_name").write_text("something else\n")

    monkeypatch.setattr(
        charger, "POWER_SUPPLY_GLOB", str(tmp_path / "power_supply" / "*")
    )
    monkeypatch.setattr(charger, "HWMON_GLOB", str(tmp_path / "hwmon" / "hwmon*"))

    assert charger.find_charger_path().endswith("bq25756@1-006b")
    # No hwmon in this tree; discovery must say so rather than raise.
    assert charger.find_hwmon_path() is None


def test_missing_charger_reads_none(tmp_path, monkeypatch):
    monkeypatch.setattr(charger, "POWER_SUPPLY_GLOB", str(tmp_path / "nothing" / "*"))
    assert charger.read() is None
