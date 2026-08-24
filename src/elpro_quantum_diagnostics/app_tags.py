"""Published tag values for the ELPRO Quantum diagnostics app.

The UI binds to these, so the dashboard is entirely a function of what the main
loop sets. More is published than the dashboard shows on purpose: a diagnostics
app is as often read through the API or a chart as through its own tiles, and
the counters that matter when a link is misbehaving are not the ones worth a
permanent tile.

Units are in the tag names wherever they are not obvious, because the sources
disagree: the radio reports frequency in MHz and bandwidth in MHz-that-means-a
-kHz-channel, the charger's hwmon is mV/mA, and the Modbus rails are already in
volts and amps.
"""

from pydoover.tags import Boolean, Number, String, Tags


class QuantumDiagnosticsTags(Tags):
    # ---------------------------------------------------------------- radio
    # Whether each source answered at all. These drive the warnings, and they
    # are what tells "no radio fitted" apart from "radio fitted and unhappy".
    radio_present = Boolean(default=False)
    radio_initialised = Boolean(default=False)
    radio_alarm = Boolean(default=False)
    radio_alarm_code = Number(default=None)
    radio_state = String(default=None)
    radio_driver_state = String(default=None)
    radio_mode = String(default=None)
    radio_uptime_s = Number(default=None)
    radio_reset_type = String(default=None)
    radio_last_error = String(default=None)
    radio_init_error = String(default=None)
    radio_tx_enabled = Boolean(default=False)
    radio_rx_enabled = Boolean(default=False)
    radio_driver_restarts = Number(default=None)

    # Signal. `live` so a dashboard being watched during an antenna alignment
    # updates every poll instead of waiting for a logging trigger.
    rssi_dbm = Number(default=None, live=True)
    rssi_background_dbm = Number(default=None, live=True)
    rssi_last_dbm = Number(default=None, live=True)
    rssi_error_dbm = Number(default=None)
    snr_db = Number(default=None, live=True)
    vswr = Number(default=None)
    pa_temperature_c = Number(default=None)

    # Band plan and configuration - the numbers that have to match at both ends
    # of a link, and the first thing to check when two units cannot hear each
    # other at all.
    radio_band_mhz = Number(default=None)
    radio_bandwidth_khz = Number(default=None)
    tx_frequency_mhz = Number(default=None)
    rx_frequency_mhz = Number(default=None)
    tx_power_dbm = Number(default=None)
    tx_power_max_dbm = Number(default=None)
    antenna_gain_db = Number(default=None)
    radio_modulation = String(default=None)
    radio_encoding = String(default=None)
    data_rate_kbps = Number(default=None)
    radio_address = String(default=None)

    # Traffic counters, cumulative since the radio last reset.
    rx_unicast_to_me = Number(default=None)
    rx_unicast_to_other = Number(default=None)
    rx_multicast = Number(default=None)
    rx_crc_errors = Number(default=None)
    rx_unsupported = Number(default=None)
    tx_unicast_ok = Number(default=None)
    tx_unicast_failed = Number(default=None)
    tx_multicast = Number(default=None)
    tx_retries = Number(default=None)
    # The two headline link-quality ratios: how much gets through, and how hard
    # the radio is working to get it through.
    tx_success_pct = Number(default=None)
    tx_retry_pct = Number(default=None)

    # Channel occupancy, percent of the radio's accumulated time.
    util_tx_pct = Number(default=None)
    util_rx_pct = Number(default=None)
    util_error_pct = Number(default=None)
    util_holdoff_pct = Number(default=None)

    # Linux-side driver counters, distinct from the radio module's own.
    drv_tx_good = Number(default=None)
    drv_tx_failed = Number(default=None)
    drv_tx_timeout = Number(default=None)
    drv_rx_packets = Number(default=None)
    drv_rx_dropped = Number(default=None)

    # Radio identity.
    radio_firmware = String(default=None)
    radio_hardware = String(default=None)
    radio_serial = String(default=None)

    # -------------------------------------------------------------- charger
    charger_present = Boolean(default=False)
    charger_status = String(default=None)
    charger_charging = Boolean(default=False)
    charger_input_present = Boolean(default=False)
    charger_battery_detected = Boolean(default=False)
    charger_input_voltage_v = Number(default=None, live=True)
    charger_input_current_a = Number(default=None, live=True)
    charger_input_power_w = Number(default=None, live=True)

    # MPPT and charge settings, from the unit's own configuration. These are
    # what the charger has been told to do; the tags above are what it is
    # actually doing.
    mppt_enabled = Boolean(default=False)
    mppt_panel_type = String(default=None)
    charge_limits_custom = Boolean(default=False)
    charge_voltage_v = Number(default=None)
    max_charge_current_a = Number(default=None)
    input_current_limit_a = Number(default=None)
    reverse_mode_enabled = Boolean(default=False)
    reverse_voltage_v = Number(default=None)
    reverse_current_a = Number(default=None)

    # ---------------------------------------------------------------- rails
    supply_voltage_v = Number(default=None, live=True)
    supply_current_a = Number(default=None, live=True)
    supply_power_w = Number(default=None, live=True)
    battery_voltage_v = Number(default=None, live=True)
    # Negative = the battery is carrying the unit; positive = it is charging.
    battery_current_a = Number(default=None, live=True)
    battery_power_w = Number(default=None, live=True)
    #: "supply" or "battery".
    active_source = String(default=None)
    running_on_battery = Boolean(default=False)

    # -------------------------------------------------------------- the unit
    unit_model = String(default=None)
    unit_product = String(default=None)
    unit_radio_type = String(default=None)
    unit_serial = String(default=None)
    unit_firmware = String(default=None)

    # ----------------------------------------------------- source health
    # One per source, so a failure names the thing that failed instead of
    # blanking the dashboard and leaving the reason in the logs.
    charger_available = Boolean(default=False)
    config_available = Boolean(default=False)
    rails_available = Boolean(default=False)
    last_read = Number(default=None)

    # Resolved warning visibility. True = hidden. Kept as tags rather than
    # computed in the UI so the precedence between them lives in one place.
    radio_missing_warning_hidden = Boolean(default=True)
    radio_alarm_warning_hidden = Boolean(default=True)
    weak_signal_warning_hidden = Boolean(default=True)
    battery_warning_hidden = Boolean(default=True)
    rails_warning_hidden = Boolean(default=True)
