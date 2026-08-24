"""Read an ELPRO licensed-band radio's diagnostics out of sysfs.

The radio module in a Quantum (and in the E2 family generally) is a separate
transceiver on an internal serial link, not a Linux 802.11 device. Its driver
still registers under ``/sys/class/ieee80211/phyN``, but the interesting part
is the vendor's own ``radio/`` subdirectory: ~118 plain-text attributes
covering signal, configuration, health and traffic counters.

Reading them costs nothing a poller needs to worry about - ~5 ms per attribute
measured on a QE-R-C4, and the curated set below is well under a hundred.

**Container access.** Doover app containers run with ``network_mode: host``,
and ``/sys/class/ieee80211`` is network-namespace scoped: in the host namespace
the radio is visible with no bind mount and no privilege, in any other
namespace the directory is empty. Verified on hardware both ways - see the
README. That is also why :func:`find_radio_path` returning ``None`` is a normal
outcome to report rather than an error to raise.

Nothing here writes. Several attributes (``RadioReset``, ``FirmwareUpgrade``,
``Utilisation.Reset``, ``drv_discover``) are command triggers rather than
values, and are deliberately absent from :data:`RADIO_ATTRS`.
"""

from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass, field

from . import sysfs

log = logging.getLogger(__name__)

#: Where the vendor driver hangs its attributes. One ``phyN`` per radio; on a
#: QE-R-C4 the licensed-band module is phy1 and the WiFi phy has no ``radio/``.
#: Resolved through :mod:`.sysfs` so a simulator can stand in for the hardware.
RADIO_GLOB = "/sys/class/ieee80211/*/radio"
RADIO_DIR = "/sys/class/ieee80211/{phy}/radio"

#: Present on every ELPRO radio directory and on nothing else, so it is what
#: distinguishes a real radio from an empty or half-populated directory.
MARKER_ATTR = "Radio.Initialised"

#: "No packet has been received yet" in the RSSI registers, not -999 dBm of
#: signal. Seen on a radio that is up but has never heard a peer.
RSSI_NO_READING = 999

#: The VSWR register's equivalent - reported when no transmission has been
#: measured since the last reset.
VSWR_NO_READING = -999

#: Every attribute the poller reads, grouped as the driver groups them.
RADIO_ATTRS: tuple[str, ...] = (
    # -- identity and calibration
    "FirmwareVersion",
    "HardwareVersion",
    "SoftwareVersion",
    "Calibration.SerialNumber",
    "Calibration.Date",
    "Configuration.Address.Primary",
    # -- band plan and modulation
    "Radio.Band",
    "Radio.Bandwidth",
    "Radio.MinimumFrequency",
    "Radio.MaximumFrequency",
    "Radio.MaximumPower",
    "Configuration.Transmit.Frequency",
    "Configuration.Receive.Frequency",
    "Configuration.Transmit.Power",
    "Configuration.Transmit.MaxPower.Conducted",
    "Configuration.Transmit.AntennaGain",
    "Configuration.Modulation",
    "Configuration.BaseEncoding",
    "Configuration.Data.Rate",
    # -- health
    "Radio.Initialised",
    "Radio.Alarm",
    "Radio.InitialisationError",
    "Radio.PATemperature",
    "RadioState",
    "RadioUptime",
    "ResetType",
    "LastReadError",
    "LastWriteError",
    "Transmit",
    "Receive",
    # -- signal
    "Statistics.RSSI.Current",
    "Statistics.RSSI.Background",
    "Statistics.RSSI.Last",
    "Statistics.RSSI.Errors",
    "Statistics.VSWR.Last",
    # -- traffic counters
    "Statistics.Receive.Unicast.ToMe",
    "Statistics.Receive.Unicast.ToOther",
    "Statistics.Receive.Multicast",
    "Statistics.Receive.Error.CRC",
    "Statistics.Receive.Unsupported",
    "Statistics.Receive.Acknowledgement.ToMe",
    "Statistics.Transmit.Unicast.OK",
    "Statistics.Transmit.Unicast.Failed",
    "Statistics.Transmit.Multicast",
    "Statistics.Transmit.Acknowledgement",
    # -- channel occupancy
    "Utilisation.TotalTime",
    "Utilisation.Transmit.Data.First",
    "Utilisation.Transmit.Data.Retry",
    "Utilisation.Transmit.Control.First",
    "Utilisation.Transmit.Control.Retry",
    "Utilisation.Receive.Data.ToMe",
    "Utilisation.Receive.Data.ToOther",
    "Utilisation.Receive.Control.ToMe",
    "Utilisation.Receive.Control.ToOther",
    "Utilisation.Receive.Error",
    "Utilisation.Holdoff",
    # -- driver side (Linux, not the radio module)
    "drv_mode",
    "drv_state",
    "drv_txpower",
    "drv_txretries",
    "drv_restarted",
    "drv_stats",
)


def find_radio_path(override: str | None = None) -> str | None:
    """Return the sysfs directory of the ELPRO radio, or ``None`` if absent.

    ``override`` may be either a full path or a bare phy name (``"phy1"``),
    for the unusual unit with two radio modules fitted.
    """
    if override:
        candidate = (
            override
            if os.path.isabs(override)
            else sysfs.path(RADIO_DIR.format(phy=override))
        )
        return candidate if os.path.isdir(candidate) else None

    for candidate in sorted(glob.glob(sysfs.path(RADIO_GLOB))):
        if os.path.exists(os.path.join(candidate, MARKER_ATTR)):
            return candidate
    return None


def read_attrs(path: str, attrs: tuple[str, ...] = RADIO_ATTRS) -> dict[str, str]:
    """Read the named attributes, skipping any the driver refuses.

    A per-attribute failure is logged at debug and dropped rather than raised:
    the attribute set differs between radio models and firmware revisions, and
    one missing entry must not cost the caller the other hundred.
    """
    values: dict[str, str] = {}
    for attr in attrs:
        try:
            with open(os.path.join(path, attr), encoding="utf-8", errors="replace") as f:
                values[attr] = f.read().strip()
        except OSError as e:
            log.debug("radio attribute %s unreadable: %s", attr, e)
    return values


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return None


def _int(raw: str | None) -> int | None:
    value = _float(raw)
    return None if value is None else int(value)


def _int_list(raw: str | None) -> list[int]:
    """Parse a per-attempt counter row, e.g. ``"12 3 1 0 0 0 0"``.

    The radio reports transmit counters as one figure per attempt: index 0 is
    the first try, the rest are successive retries. Summing loses the shape
    that makes the counter diagnostic, so both forms are kept.
    """
    if not raw:
        return []
    out = []
    for token in raw.split():
        try:
            out.append(int(token))
        except ValueError:
            return []
    return out


def _bool_enable(raw: str | None) -> bool | None:
    """``Transmit``/``Receive`` read back as ``ENABLE``/``DISABLE``."""
    if raw is None:
        return None
    return raw.strip().upper().startswith("ENABLE")


def parse_uptime(raw: str | None) -> float | None:
    """Parse ``RadioUptime`` (``"  10:20:33:05"`` = d:h:m:s) into seconds.

    Rendered with leading spaces and a variable number of fields, so it is
    parsed right-to-left: seconds, minutes, hours, days.
    """
    if not raw:
        return None
    parts = raw.strip().split(":")
    multipliers = (1, 60, 3600, 86400)
    total = 0.0
    for value, multiplier in zip(reversed(parts), multipliers, strict=False):
        try:
            total += float(value) * multiplier
        except ValueError:
            return None
    return total


def parse_drv_stats(raw: str | None) -> dict[str, int]:
    """Parse the driver's ``drv_stats`` block.

    Free text, one ``name value`` pair per line under a heading::

        Radio Driver Statistics:
        tx_good    0
        rx_bytes   0
    """
    stats: dict[str, int] = {}
    for line in (raw or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            stats[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return stats


def _rssi(raw: str | None) -> float | None:
    """RSSI in dBm, or ``None`` for the "never heard anything" sentinel."""
    value = _float(raw)
    return None if value is None or value >= RSSI_NO_READING else value


def _percent(part: float | None, total: float | None) -> float | None:
    if not total or part is None:
        return None
    return round(100.0 * part / total, 3)


@dataclass
class RadioSnapshot:
    """One consistent read of the radio's diagnostics."""

    path: str

    # -- identity
    firmware: str | None = None
    hardware: str | None = None
    software: str | None = None
    serial: str | None = None
    address: str | None = None

    # -- band plan
    band_mhz: float | None = None
    bandwidth_khz: float | None = None
    tx_frequency_mhz: float | None = None
    rx_frequency_mhz: float | None = None
    tx_power_dbm: float | None = None
    tx_power_max_dbm: float | None = None
    antenna_gain_db: float | None = None
    modulation: str | None = None
    encoding: str | None = None
    data_rate_kbps: float | None = None

    # -- health
    initialised: bool = False
    alarm: bool = False
    alarm_code: int | None = None
    init_error: str | None = None
    state: str | None = None
    driver_state: str | None = None
    mode: str | None = None
    uptime_s: float | None = None
    reset_type: str | None = None
    last_error: str | None = None
    pa_temperature_c: float | None = None
    vswr: float | None = None
    tx_enabled: bool | None = None
    rx_enabled: bool | None = None
    driver_restarts: int | None = None

    # -- signal
    rssi_dbm: float | None = None
    rssi_background_dbm: float | None = None
    rssi_last_dbm: float | None = None
    rssi_error_dbm: float | None = None
    snr_db: float | None = None

    # -- traffic
    rx_unicast_to_me: int | None = None
    rx_unicast_to_other: int | None = None
    rx_multicast: int | None = None
    rx_crc_errors: int | None = None
    rx_unsupported: int | None = None
    rx_acks_to_me: int | None = None
    tx_unicast_ok: int | None = None
    tx_unicast_failed: int | None = None
    tx_multicast: int | None = None
    tx_acks: int | None = None
    tx_first_attempt: int | None = None
    tx_retries: int | None = None
    tx_success_pct: float | None = None
    tx_retry_pct: float | None = None

    # -- channel occupancy, percent of the radio's own accumulated time
    util_tx_pct: float | None = None
    util_rx_pct: float | None = None
    util_error_pct: float | None = None
    util_holdoff_pct: float | None = None

    # -- Linux driver counters
    drv_stats: dict[str, int] = field(default_factory=dict)


def parse(path: str, values: dict[str, str]) -> RadioSnapshot:
    """Turn raw attribute strings into a typed, unit-bearing snapshot."""
    get = values.get
    snap = RadioSnapshot(path=path)

    snap.firmware = get("FirmwareVersion") or None
    snap.hardware = get("HardwareVersion") or None
    snap.software = get("SoftwareVersion") or None
    snap.serial = get("Calibration.SerialNumber") or None
    snap.address = get("Configuration.Address.Primary") or None

    snap.band_mhz = _float(get("Radio.Band"))
    # Reported in MHz by the driver; kHz is how ELPRO's own documentation and
    # the licence paperwork describe a channel, so it is converted once here.
    bandwidth_mhz = _float(get("Radio.Bandwidth"))
    snap.bandwidth_khz = None if bandwidth_mhz is None else round(bandwidth_mhz * 1000, 3)
    snap.tx_frequency_mhz = _float(get("Configuration.Transmit.Frequency"))
    snap.rx_frequency_mhz = _float(get("Configuration.Receive.Frequency"))
    snap.tx_power_dbm = _float(get("Configuration.Transmit.Power"))
    snap.tx_power_max_dbm = _float(get("Configuration.Transmit.MaxPower.Conducted"))
    snap.antenna_gain_db = _float(get("Configuration.Transmit.AntennaGain"))
    snap.modulation = get("Configuration.Modulation") or None
    snap.encoding = get("Configuration.BaseEncoding") or None
    snap.data_rate_kbps = _float(get("Configuration.Data.Rate"))

    snap.initialised = _int(get("Radio.Initialised")) == 1
    snap.alarm_code = _int(get("Radio.Alarm"))
    snap.alarm = bool(snap.alarm_code)
    snap.init_error = get("Radio.InitialisationError") or None
    snap.state = get("RadioState") or None
    snap.driver_state = get("drv_state") or None
    snap.mode = get("drv_mode") or None
    snap.uptime_s = parse_uptime(get("RadioUptime"))
    snap.reset_type = get("ResetType") or None
    snap.pa_temperature_c = _float(get("Radio.PATemperature"))
    snap.tx_enabled = _bool_enable(get("Transmit"))
    snap.rx_enabled = _bool_enable(get("Receive"))
    snap.driver_restarts = _int(get("drv_restarted"))

    # The driver keeps "No Read Error" in LastReadError rather than blanking
    # it, so only a genuinely different string is worth surfacing. Write errors
    # are reported too: a stock unit sits with a harmless out-of-range write
    # error latched, and hiding it would mean hiding real ones as well.
    read_error = get("LastReadError") or ""
    snap.last_error = read_error if read_error and "no read error" not in read_error.lower() else None

    vswr = _float(get("Statistics.VSWR.Last"))
    snap.vswr = None if vswr is None or vswr <= VSWR_NO_READING else vswr

    snap.rssi_dbm = _rssi(get("Statistics.RSSI.Current"))
    snap.rssi_background_dbm = _rssi(get("Statistics.RSSI.Background"))
    snap.rssi_last_dbm = _rssi(get("Statistics.RSSI.Last"))
    snap.rssi_error_dbm = _rssi(get("Statistics.RSSI.Errors"))
    # Signal margin over the noise floor. Computed from Last rather than
    # Current because Current tracks whatever the receiver hears right now,
    # including noise; Last is the level of the last decoded packet, which is
    # the number that says whether the link has headroom.
    if snap.rssi_last_dbm is not None and snap.rssi_background_dbm is not None:
        snap.snr_db = round(snap.rssi_last_dbm - snap.rssi_background_dbm, 1)

    snap.rx_unicast_to_me = _int(get("Statistics.Receive.Unicast.ToMe"))
    snap.rx_unicast_to_other = _int(get("Statistics.Receive.Unicast.ToOther"))
    snap.rx_multicast = _int(get("Statistics.Receive.Multicast"))
    snap.rx_crc_errors = _int(get("Statistics.Receive.Error.CRC"))
    snap.rx_unsupported = _int(get("Statistics.Receive.Unsupported"))
    snap.rx_acks_to_me = _int(get("Statistics.Receive.Acknowledgement.ToMe"))

    ok = _int_list(get("Statistics.Transmit.Unicast.OK"))
    failed = _int_list(get("Statistics.Transmit.Unicast.Failed"))
    snap.tx_unicast_ok = sum(ok) if ok else None
    snap.tx_unicast_failed = sum(failed) if failed else None
    multicast = _int_list(get("Statistics.Transmit.Multicast"))
    snap.tx_multicast = sum(multicast) if multicast else None
    snap.tx_acks = _int(get("Statistics.Transmit.Acknowledgement"))
    if ok:
        snap.tx_first_attempt = ok[0]
        snap.tx_retries = sum(ok[1:]) + sum(failed[1:] if failed else [])
    attempted = (snap.tx_unicast_ok or 0) + (snap.tx_unicast_failed or 0)
    if attempted:
        snap.tx_success_pct = _percent(snap.tx_unicast_ok, attempted)
        snap.tx_retry_pct = _percent(snap.tx_retries, attempted)

    total_time = _float(get("Utilisation.TotalTime"))
    tx_time = sum(
        _float(get(k)) or 0.0
        for k in (
            "Utilisation.Transmit.Data.First",
            "Utilisation.Transmit.Data.Retry",
            "Utilisation.Transmit.Control.First",
            "Utilisation.Transmit.Control.Retry",
        )
    )
    rx_time = sum(
        _float(get(k)) or 0.0
        for k in (
            "Utilisation.Receive.Data.ToMe",
            "Utilisation.Receive.Data.ToOther",
            "Utilisation.Receive.Control.ToMe",
            "Utilisation.Receive.Control.ToOther",
        )
    )
    snap.util_tx_pct = _percent(tx_time, total_time)
    snap.util_rx_pct = _percent(rx_time, total_time)
    snap.util_error_pct = _percent(_float(get("Utilisation.Receive.Error")), total_time)
    snap.util_holdoff_pct = _percent(_float(get("Utilisation.Holdoff")), total_time)

    snap.drv_stats = parse_drv_stats(get("drv_stats"))

    return snap


def read(override: str | None = None) -> RadioSnapshot | None:
    """Locate the radio and take one snapshot, or ``None`` if there is none."""
    path = find_radio_path(override)
    if path is None:
        return None
    return parse(path, read_attrs(path))
