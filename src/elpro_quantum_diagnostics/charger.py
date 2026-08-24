"""Read the Quantum's battery charger / MPPT controller out of sysfs.

The charger is a TI **BQ25756** buck-boost charger on the unit's I2C bus,
driven by ELPRO's out-of-tree ``bq25756.ko``. It is what implements the
"Enable MPPT" option in the unit's web portal, so it is where the solar side of
a Quantum shows up.

Two sysfs faces, and this module deliberately uses each only for what it is
trustworthy for:

``/sys/class/power_supply/bq25756@*``
    Charge state - ``status``, ``charge_type``, ``online``, ``present``. Its
    *numeric* attributes are **not** read here: the standard power-supply class
    specifies µV/µA, and this driver plainly does not follow it (a stock unit
    reports ``constant_charge_voltage`` as ``16``, i.e. volts). Rather than
    publish numbers under a unit we cannot prove, the charger's configured
    limits are read from the unit's own config server instead, where they carry
    documented engineering units - see :mod:`.config_server`.

``/sys/class/hwmon/hwmonN`` (``name`` = ``bq25756@...``)
    The live measurements. hwmon *is* strictly specified - ``inN_input`` in mV,
    ``currN_input`` in mA - so these convert exactly.

Unlike the radio, none of this is network-namespace scoped, so it reads the
same from any container.
"""

from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass

from . import sysfs

log = logging.getLogger(__name__)

POWER_SUPPLY_GLOB = "/sys/class/power_supply/*"
HWMON_GLOB = "/sys/class/hwmon/hwmon*"

#: Matched case-insensitively against ``model_name`` (power supply) and
#: ``name`` (hwmon). The hwmon name substitutes ``_`` for the ``-`` in the I2C
#: address, so only the part before the ``@`` is compared.
CHARGER_MODEL = "bq25756"

#: Values of ``status`` that mean current is going into the battery. The
#: standard set is Charging / Discharging / Not charging / Full / Unknown.
CHARGING_STATES = frozenset({"charging"})

MV_PER_V = 1000.0
MA_PER_A = 1000.0


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError as e:
        log.debug("charger attribute %s unreadable: %s", path, e)
        return None


def find_charger_path() -> str | None:
    """Return the BQ25756's power-supply directory, or ``None``."""
    for candidate in sorted(glob.glob(sysfs.path(POWER_SUPPLY_GLOB))):
        model = _read(os.path.join(candidate, "model_name")) or ""
        if CHARGER_MODEL in model.lower():
            return candidate
        # Older driver builds do not export model_name; the directory is named
        # after the device, so fall back to that.
        if CHARGER_MODEL in os.path.basename(candidate).lower():
            return candidate
    return None


def find_hwmon_path() -> str | None:
    """Return the charger's hwmon directory, or ``None``."""
    for candidate in sorted(glob.glob(sysfs.path(HWMON_GLOB))):
        name = _read(os.path.join(candidate, "name")) or ""
        if CHARGER_MODEL in name.lower():
            return candidate
    return None


def _int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


@dataclass
class ChargerSnapshot:
    """One read of the charger's state and live measurements."""

    path: str

    # -- state, from the power-supply class
    status: str | None = None
    charge_type: str | None = None
    charging: bool = False
    #: An input source (solar array / DC supply) is connected and in range.
    input_present: bool = False
    #: The charger has detected a battery on its battery terminals. A Quantum
    #: powered from BAT but with charging disabled reports this false, so it is
    #: "the charger sees a battery", not "a battery is wired up".
    battery_detected: bool = False

    # -- live measurements, from hwmon (exact units by specification)
    input_voltage_v: float | None = None
    input_current_a: float | None = None
    input_power_w: float | None = None


def parse(path: str, hwmon_path: str | None) -> ChargerSnapshot:
    snap = ChargerSnapshot(path=path)

    snap.status = _read(os.path.join(path, "status"))
    snap.charge_type = _read(os.path.join(path, "charge_type"))
    snap.charging = (snap.status or "").strip().lower() in CHARGING_STATES
    snap.input_present = _read(os.path.join(path, "online")) == "1"
    snap.battery_detected = _read(os.path.join(path, "present")) == "1"

    if hwmon_path:
        # in0/curr1 are the charger's only exported ADC channels. They read 0
        # on a unit with nothing on its supply terminals while the battery rail
        # is live, which is why they are labelled as the *input* side - see the
        # README's note on inferred values.
        millivolts = _int(_read(os.path.join(hwmon_path, "in0_input")))
        milliamps = _int(_read(os.path.join(hwmon_path, "curr1_input")))
        if millivolts is not None:
            snap.input_voltage_v = round(millivolts / MV_PER_V, 3)
        if milliamps is not None:
            snap.input_current_a = round(milliamps / MA_PER_A, 3)
        if snap.input_voltage_v is not None and snap.input_current_a is not None:
            snap.input_power_w = round(snap.input_voltage_v * snap.input_current_a, 2)

    return snap


def read() -> ChargerSnapshot | None:
    """Take one charger snapshot, or ``None`` if no BQ25756 is present."""
    path = find_charger_path()
    if path is None:
        return None
    return parse(path, find_hwmon_path())
