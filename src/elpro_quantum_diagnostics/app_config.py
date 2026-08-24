from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationPosition

from . import config_server, rails


class QuantumDiagnosticsConfig(config.Schema):
    """User-configurable settings for the Quantum diagnostics app.

    Every source can be turned off independently. A Quantum with no radio
    fitted, or with its Modbus server disabled, should show the sections it can
    fill and stay quiet about the rest rather than raise warnings an installer
    cannot act on.
    """

    poll_interval = config.Number(
        "Poll Interval (s)",
        default=30.0,
        minimum=5.0,
        description=(
            "How often to re-read the unit. The radio's counters move slowly "
            "and each read costs a transaction on the radio's internal link, "
            "so there is little to gain below about 10 seconds."
        ),
    )

    radio_enabled = config.Boolean(
        "Read Radio",
        default=True,
        description=(
            "Read the licensed-band radio's diagnostics from sysfs. Requires "
            "the app to run on the Quantum itself with host networking, which "
            "is how Doover deploys apps by default."
        ),
    )

    radio_phy = config.String(
        "Radio Device",
        default=None,
        description=(
            "Which radio to read, e.g. 'phy1', or a full sysfs path. Leave "
            "blank to auto-detect, which is correct unless the unit has more "
            "than one radio module fitted."
        ),
    )

    charger_enabled = config.Boolean(
        "Read Charger / MPPT",
        default=True,
        description="Read the BQ25756 solar charge controller from sysfs.",
    )

    config_server_enabled = config.Boolean(
        "Read Unit Configuration",
        default=True,
        description=(
            "Read the MPPT and charger settings, and the unit's identity, from "
            "the Quantum's own configuration daemon on localhost. Read-only."
        ),
    )

    config_server_host = config.String(
        "Configuration Server",
        default=config_server.DEFAULT_HOST,
        description="Address of the unit's configuration daemon.",
    )

    config_server_port = config.Integer(
        "Configuration Server Port",
        default=config_server.DEFAULT_PORT,
        minimum=1,
        maximum=65535,
        description="TCP port of the unit's configuration daemon.",
    )

    rails_enabled = config.Boolean(
        "Read Supply and Battery Rails",
        default=True,
        description=(
            "Read the supply and battery voltages and currents over the unit's "
            "Modbus TCP server. The server is off on a factory Quantum and has "
            "to be enabled in the web config before this will report anything."
        ),
    )

    modbus_host = config.String(
        "Modbus Server",
        default=rails.DEFAULT_HOST,
        description="Address of the Quantum's Modbus TCP server.",
    )

    modbus_port = config.Integer(
        "Modbus Server Port",
        default=rails.DEFAULT_PORT,
        minimum=1,
        maximum=65535,
        description="TCP port of the Quantum's Modbus TCP server.",
    )

    weak_signal_dbm = config.Number(
        "Weak Signal Threshold (dBm)",
        default=-100.0,
        description=(
            "Raise a warning when the last received packet was weaker than "
            "this. -100 dBm leaves useful margin on a 12.5 kHz channel; lower "
            "it on a link that is known to run close to the noise floor."
        ),
    )

    position = ApplicationPosition()


def export():
    QuantumDiagnosticsConfig.export(
        Path(__file__).parents[2] / "doover_config.json", "elpro_quantum_diagnostics"
    )
