"""Radio parsing tests.

The fixture below is a verbatim capture from a QE-R-C4 (E2-455 radio module,
firmware 2.52) taken over SSH, trimmed to the attributes the app reads. It is
deliberately a *quiet* radio - initialised, never having heard a peer - because
that is the state where the sentinel values show up, and getting them wrong
would put -999 dBm and 999 dBm on a dashboard.
"""

import pytest

from elpro_quantum_diagnostics import radio

# A radio that is up and idle: RSSI sentinels present, all counters zero.
QUIET_RADIO = {
    "FirmwareVersion": "E2_455 radio application 2.52 20230202",
    "HardwareVersion": "400-480MHz 10Watt 25kHz Channel R1.4 mod J",
    "SoftwareVersion": "E2-455 2.52 [Feb  2 2023 11:13:19] (10012)",
    "Calibration.SerialNumber": "30081638",
    "Configuration.Address.Primary": "00:12:AF:13:01:B1",
    "Radio.Band": "445",
    "Radio.Bandwidth": "12.500",
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


@pytest.fixture
def quiet():
    return radio.parse("/sys/class/ieee80211/phy1/radio", QUIET_RADIO)


def test_identity(quiet):
    assert quiet.serial == "30081638"
    assert quiet.address == "00:12:AF:13:01:B1"
    assert quiet.firmware.startswith("E2_455")


def test_band_plan(quiet):
    assert quiet.tx_frequency_mhz == pytest.approx(472.1)
    # The driver reports bandwidth in MHz; a 12.5 kHz channel must not surface
    # as "12.5 kHz-worth of MHz".
    assert quiet.bandwidth_khz == pytest.approx(12500.0)
    assert quiet.tx_power_dbm == 20
    assert quiet.modulation == "QAM"


def test_health(quiet):
    assert quiet.initialised is True
    assert quiet.alarm is False
    assert quiet.pa_temperature_c == 32
    assert quiet.state == "application"
    assert quiet.mode == "MESH"
    # "No Read Error" is the driver's idle text, not something to show a user.
    assert quiet.last_error is None


def test_uptime_is_days_hours_minutes_seconds(quiet):
    assert quiet.uptime_s == 10 * 86400 + 20 * 3600 + 33 * 60 + 5


def test_rssi_sentinels_become_none(quiet):
    # 999 means "nothing received yet", and -999 means "VSWR not measured".
    assert quiet.rssi_last_dbm is None
    assert quiet.vswr is None
    # Real readings still come through.
    assert quiet.rssi_dbm == -113
    assert quiet.rssi_background_dbm == -113
    # No last packet means no meaningful margin over the noise floor.
    assert quiet.snr_db is None


def test_zero_counters_are_zero_not_none(quiet):
    # A quiet radio genuinely has zero of everything; publishing None would
    # read on the dashboard as "not supported".
    assert quiet.rx_unicast_to_me == 0
    assert quiet.tx_unicast_ok == 0
    assert quiet.tx_multicast == 0
    # With nothing attempted there is no ratio to report.
    assert quiet.tx_success_pct is None


def test_driver_stats(quiet):
    assert quiet.drv_stats["tx_good"] == 0
    assert quiet.drv_stats["rx_bytes"] == 0
    assert "Statistics" not in quiet.drv_stats


def test_utilisation_is_percent_of_total_time(quiet):
    assert quiet.util_tx_pct == 0.0
    assert quiet.util_rx_pct == 0.0


def test_busy_link():
    """A link that is passing traffic, with retries and a real signal."""
    values = dict(QUIET_RADIO)
    values.update(
        {
            "Statistics.RSSI.Last": "-92",
            "Statistics.RSSI.Background": "-113",
            "Statistics.VSWR.Last": "1.35",
            "Statistics.Transmit.Unicast.OK": "800 120 30 0 0 0 0",
            "Statistics.Transmit.Unicast.Failed": "0 0 0 50 0 0 0",
            "Statistics.Receive.Unicast.ToMe": "940",
            "Utilisation.TotalTime": "1000",
            "Utilisation.Transmit.Data.First": "60",
            "Utilisation.Transmit.Data.Retry": "15",
            "Utilisation.Receive.Data.ToMe": "120",
        }
    )
    snap = radio.parse("/sys/class/ieee80211/phy1/radio", values)

    assert snap.rssi_last_dbm == -92
    assert snap.snr_db == pytest.approx(21.0)
    assert snap.vswr == pytest.approx(1.35)

    assert snap.tx_unicast_ok == 950
    assert snap.tx_unicast_failed == 50
    assert snap.tx_first_attempt == 800
    # Retries are every attempt after the first, successful or not.
    assert snap.tx_retries == 120 + 30 + 50
    assert snap.tx_success_pct == pytest.approx(95.0)
    assert snap.tx_retry_pct == pytest.approx(20.0)

    assert snap.util_tx_pct == pytest.approx(7.5)
    assert snap.util_rx_pct == pytest.approx(12.0)


def test_alarm_and_uninitialised():
    values = dict(QUIET_RADIO)
    values.update(
        {
            "Radio.Initialised": "0",
            "Radio.Alarm": "4",
            "Radio.InitialisationError": "No response from radio module",
            "LastReadError": "Timeout reading register 12",
        }
    )
    snap = radio.parse("/sys/class/ieee80211/phy1/radio", values)

    assert snap.initialised is False
    assert snap.alarm is True
    assert snap.alarm_code == 4
    assert snap.init_error == "No response from radio module"
    assert snap.last_error == "Timeout reading register 12"


def test_missing_attributes_are_tolerated():
    """Firmware revisions differ; an absent attribute must not raise."""
    snap = radio.parse("/sys/class/ieee80211/phy0/radio", {"Radio.Initialised": "1"})

    assert snap.initialised is True
    assert snap.rssi_dbm is None
    assert snap.uptime_s is None
    assert snap.drv_stats == {}


def test_command_triggers_are_never_read():
    """Reading these does nothing useful and writing them resets the radio."""
    for attr in ("RadioReset", "FirmwareUpgrade", "Utilisation.Reset", "drv_discover"):
        assert attr not in radio.RADIO_ATTRS


def test_configured_radio_path_is_taken_as_given(tmp_path):
    """An explicit path is trusted if it exists, and reported missing if not.

    Auto-detection insists on the marker attribute, but an operator naming a
    path has more context than the glob does - a firmware revision that renames
    the marker should still be readable by pointing the app at it.
    """
    configured = tmp_path / "phy0" / "radio"
    configured.mkdir(parents=True)
    assert radio.find_radio_path(str(configured)) == str(configured)
    assert radio.find_radio_path(str(tmp_path / "nope")) is None


def test_auto_detection_skips_a_directory_without_the_marker(tmp_path, monkeypatch):
    bare = tmp_path / "phy0" / "radio"
    real = tmp_path / "phy1" / "radio"
    bare.mkdir(parents=True)
    real.mkdir(parents=True)
    (real / radio.MARKER_ATTR).write_text("1")

    monkeypatch.setattr(radio, "RADIO_GLOB", str(tmp_path / "*" / "radio"))
    assert radio.find_radio_path() == str(real)
