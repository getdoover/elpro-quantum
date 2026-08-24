"""Dashboard for the ELPRO Quantum diagnostics app.

Laid out for the two questions an operator actually asks: *is the radio link
healthy* and *is the unit being powered properly*. Those live at the top level.
Everything else - band plan, counters, occupancy, identity - sits in
submodules, because it is what you open when the answer to one of the first two
is "no".
"""

from pathlib import Path

from pydoover import ui

from .app_tags import QuantumDiagnosticsTags as Tags

# RSSI bands for a narrowband licensed radio, not WiFi. A 12.5 kHz channel has
# a thermal noise floor near -130 dBm and these units report a background
# around -113, so -100 dBm is a workable link and -110 is scraping.
_RSSI_FLOOR = -120
_RSSI_POOR = -110
_RSSI_FAIR = -100
_RSSI_CEILING = -40


class QuantumDiagnosticsUI(ui.UI):
    # -- the two headline answers
    rssi = ui.NumericVariable(
        "Signal (last packet)",
        units="dBm",
        value=Tags.rssi_last_dbm,
        precision=0,
        ranges=[
            ui.Range("Poor", _RSSI_FLOOR, _RSSI_POOR, ui.Colour.red),
            ui.Range("Fair", _RSSI_POOR, _RSSI_FAIR, ui.Colour.yellow),
            ui.Range("Good", _RSSI_FAIR, _RSSI_CEILING, ui.Colour.green),
        ],
    )
    snr = ui.NumericVariable(
        "Signal Margin",
        units="dB",
        value=Tags.snr_db,
        precision=0,
    )
    radio_state = ui.TextVariable("Radio State", value=Tags.radio_state)
    tx_success = ui.NumericVariable(
        "Transmit Success",
        units="%",
        value=Tags.tx_success_pct,
        precision=1,
    )

    supply_voltage = ui.NumericVariable(
        "Supply", units="V", value=Tags.supply_voltage_v, precision=2
    )
    battery_voltage = ui.NumericVariable(
        "Battery", units="V", value=Tags.battery_voltage_v, precision=2
    )
    solar_power = ui.NumericVariable(
        "Solar Input", units="W", value=Tags.charger_input_power_w, precision=1
    )
    charging = ui.BooleanVariable("Charging", value=Tags.charger_charging)

    # -- warnings, in the order they make sense to act on
    radio_missing_warning = ui.WarningIndicator(
        "No ELPRO radio found on this unit",
        hidden=Tags.radio_missing_warning_hidden,
    )
    radio_alarm_warning = ui.WarningIndicator(
        "Radio alarm - the radio module is reporting a fault",
        hidden=Tags.radio_alarm_warning_hidden,
    )
    weak_signal_warning = ui.WarningIndicator(
        "Weak radio signal - check antenna, feeder and alignment",
        hidden=Tags.weak_signal_warning_hidden,
    )
    battery_warning = ui.WarningIndicator(
        "Running on battery - no supply on the SUP terminals",
        hidden=Tags.battery_warning_hidden,
    )
    rails_warning = ui.WarningIndicator(
        "Cannot read the unit's Modbus server - supply and battery unknown",
        hidden=Tags.rails_warning_hidden,
    )

    # -- detail
    radio = ui.Submodule(
        "Radio",
        children=[
            ui.NumericVariable(
                "RSSI (current)", units="dBm", value=Tags.rssi_dbm, precision=0
            ),
            ui.NumericVariable(
                "Background Noise",
                units="dBm",
                value=Tags.rssi_background_dbm,
                precision=0,
            ),
            ui.NumericVariable("VSWR", value=Tags.vswr, precision=2),
            ui.NumericVariable(
                "PA Temperature", units="C", value=Tags.pa_temperature_c, precision=0
            ),
            ui.TextVariable("Mesh Mode", value=Tags.radio_mode),
            ui.TextVariable("Driver State", value=Tags.radio_driver_state),
            ui.NumericVariable(
                "Radio Uptime", units="s", value=Tags.radio_uptime_s, precision=0
            ),
            ui.TextVariable("Last Reset", value=Tags.radio_reset_type),
            ui.TextVariable("Last Error", value=Tags.radio_last_error),
            ui.BooleanVariable("Transmitter Enabled", value=Tags.radio_tx_enabled),
            ui.BooleanVariable("Receiver Enabled", value=Tags.radio_rx_enabled),
        ],
    )

    band_plan = ui.Submodule(
        "Radio Configuration",
        children=[
            ui.NumericVariable(
                "Transmit Frequency",
                units="MHz",
                value=Tags.tx_frequency_mhz,
                precision=4,
            ),
            ui.NumericVariable(
                "Receive Frequency",
                units="MHz",
                value=Tags.rx_frequency_mhz,
                precision=4,
            ),
            ui.NumericVariable(
                "Channel Bandwidth",
                units="kHz",
                value=Tags.radio_bandwidth_khz,
                precision=1,
            ),
            ui.NumericVariable(
                "Transmit Power", units="dBm", value=Tags.tx_power_dbm, precision=0
            ),
            ui.NumericVariable(
                "Maximum Power", units="dBm", value=Tags.tx_power_max_dbm, precision=0
            ),
            ui.NumericVariable(
                "Antenna Gain", units="dB", value=Tags.antenna_gain_db, precision=1
            ),
            ui.NumericVariable(
                "Data Rate", units="kbps", value=Tags.data_rate_kbps, precision=3
            ),
            ui.TextVariable("Modulation", value=Tags.radio_modulation),
            ui.TextVariable("Encoding", value=Tags.radio_encoding),
            ui.TextVariable("Radio Address", value=Tags.radio_address),
        ],
    )

    traffic = ui.Submodule(
        "Radio Traffic",
        children=[
            ui.NumericVariable(
                "Received (to me)", value=Tags.rx_unicast_to_me, precision=0
            ),
            ui.NumericVariable(
                "Received (to others)", value=Tags.rx_unicast_to_other, precision=0
            ),
            ui.NumericVariable("Received (broadcast)", value=Tags.rx_multicast, precision=0),
            ui.NumericVariable("CRC Errors", value=Tags.rx_crc_errors, precision=0),
            ui.NumericVariable("Transmitted OK", value=Tags.tx_unicast_ok, precision=0),
            ui.NumericVariable("Transmit Failures", value=Tags.tx_unicast_failed, precision=0),
            ui.NumericVariable("Retries", value=Tags.tx_retries, precision=0),
            ui.NumericVariable(
                "Retry Rate", units="%", value=Tags.tx_retry_pct, precision=1
            ),
            ui.NumericVariable(
                "Channel Busy (tx)", units="%", value=Tags.util_tx_pct, precision=2
            ),
            ui.NumericVariable(
                "Channel Busy (rx)", units="%", value=Tags.util_rx_pct, precision=2
            ),
            ui.NumericVariable(
                "Channel Errors", units="%", value=Tags.util_error_pct, precision=2
            ),
        ],
    )

    power = ui.Submodule(
        "Power",
        children=[
            ui.TextVariable("Running From", value=Tags.active_source),
            ui.NumericVariable(
                "Supply Current", units="A", value=Tags.supply_current_a, precision=3
            ),
            ui.NumericVariable(
                "Supply Power", units="W", value=Tags.supply_power_w, precision=2
            ),
            ui.NumericVariable(
                "Battery Current", units="A", value=Tags.battery_current_a, precision=3
            ),
            ui.NumericVariable(
                "Battery Power", units="W", value=Tags.battery_power_w, precision=2
            ),
        ],
    )

    charger = ui.Submodule(
        "Charger / MPPT",
        children=[
            ui.TextVariable("Charger Status", value=Tags.charger_status),
            ui.BooleanVariable("MPPT Enabled", value=Tags.mppt_enabled),
            ui.TextVariable("Solar Panel Type", value=Tags.mppt_panel_type),
            ui.BooleanVariable("Input Connected", value=Tags.charger_input_present),
            ui.BooleanVariable("Battery Detected", value=Tags.charger_battery_detected),
            ui.NumericVariable(
                "Input Voltage", units="V", value=Tags.charger_input_voltage_v, precision=2
            ),
            ui.NumericVariable(
                "Input Current", units="A", value=Tags.charger_input_current_a, precision=3
            ),
            ui.NumericVariable(
                "Charge Voltage Setpoint",
                units="V",
                value=Tags.charge_voltage_v,
                precision=2,
            ),
            ui.NumericVariable(
                "Max Charge Current",
                units="A",
                value=Tags.max_charge_current_a,
                precision=2,
            ),
            ui.NumericVariable(
                "Input Current Limit",
                units="A",
                value=Tags.input_current_limit_a,
                precision=2,
            ),
            ui.BooleanVariable("Reverse Mode", value=Tags.reverse_mode_enabled),
        ],
    )

    unit = ui.Submodule(
        "Unit",
        children=[
            ui.TextVariable("Model", value=Tags.unit_model),
            ui.TextVariable("Product", value=Tags.unit_product),
            ui.TextVariable("Radio Type", value=Tags.unit_radio_type),
            ui.TextVariable("Serial Number", value=Tags.unit_serial),
            ui.TextVariable("Firmware", value=Tags.unit_firmware),
            ui.TextVariable("Radio Firmware", value=Tags.radio_firmware),
            ui.TextVariable("Radio Hardware", value=Tags.radio_hardware),
            ui.TextVariable("Radio Serial", value=Tags.radio_serial),
            ui.Timestamp("Last Read", value=Tags.last_read),
        ],
    )


def export():
    QuantumDiagnosticsUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json", "elpro_quantum_diagnostics"
    )
