"""Main loop for the ELPRO Quantum diagnostics app.

Four independent sources (radio sysfs, charger sysfs, the unit's config daemon,
the unit's Modbus server), read once per poll. Each is wrapped separately: a
Quantum with no radio fitted, or with its Modbus server left disabled, must
still fill in every section it can. A source that fails clears only its own
tags and its own availability flag.

The sysfs reads are synchronous file reads of a handful of kilobytes and take
single-digit milliseconds, so they run inline rather than in a thread.
"""

import logging
import time

from pydoover.docker import Application

from . import charger as charger_mod
from . import config_server
from . import radio as radio_mod
from .app_config import QuantumDiagnosticsConfig
from .app_tags import QuantumDiagnosticsTags
from .app_ui import QuantumDiagnosticsUI
from .config_server import ConfigServerClient, ConfigServerError, as_bool, as_float
from .rails import RailsClient, RailsError

log = logging.getLogger()

#: Config keys read each poll, mapped to the name used below. The MPPT and
#: charge settings can be changed from the unit's web portal at any time, so
#: they are re-read rather than cached; the identity keys below are not.
POWER_KEYS = {
    "mppt_enabled": f"{config_server.CONFIG_ROOT}.PowerSupply.Enable",
    "panel_type": f"{config_server.CONFIG_ROOT}.PowerSupply.PanelType",
    "custom_limits": f"{config_server.CONFIG_ROOT}.PowerSupply.Enable_man",
    "charge_voltage": f"{config_server.CONFIG_ROOT}.PowerSupply.ChargeVoltage",
    "max_charge_current": f"{config_server.CONFIG_ROOT}.PowerSupply.MaxChargeCurrent",
    "input_current_limit": f"{config_server.CONFIG_ROOT}.PowerSupply.InputCurrentLimit",
    "reverse_enabled": f"{config_server.CONFIG_ROOT}.PowerSupply.Enable_reverse",
    "reverse_voltage": f"{config_server.CONFIG_ROOT}.PowerSupply.ReverseVoltage",
    "reverse_current": f"{config_server.CONFIG_ROOT}.PowerSupply.ReverseCurrent",
}

#: Read once, on the first poll that reaches the config daemon. `MODEL_NAME` is
#: the marketing model (QE-R-C4) and `ProductName` the firmware's own idea of
#: the platform (QE-E); they differ, and both are worth having when matching a
#: unit against a datasheet.
IDENTITY_KEYS = {
    "model": f"{config_server.CONFIG_ROOT}.webpage.MODEL_NAME",
    "product": f"{config_server.CONFIG_ROOT}.identification.ProductName",
    "radio_type": f"{config_server.CONFIG_ROOT}.identification.Radio",
}

#: `PanelType` is an index into the web portal's solar-panel dropdown. Only the
#: default is confirmed on hardware, so anything else is passed through as its
#: raw index rather than guessed at.
PANEL_TYPES = {"0": "Default"}

MS_PER_S = 1000


class QuantumDiagnosticsApplication(Application):
    config: QuantumDiagnosticsConfig
    tags: QuantumDiagnosticsTags
    ui: QuantumDiagnosticsUI

    config_cls = QuantumDiagnosticsConfig
    tags_cls = QuantumDiagnosticsTags
    ui_cls = QuantumDiagnosticsUI

    async def setup(self):
        self.loop_target_period = self.config.poll_interval.value

        self.config_client = ConfigServerClient(
            self.config.config_server_host.value,
            self.config.config_server_port.value,
        )
        self.rails_client = RailsClient(
            self.config.modbus_host.value,
            self.config.modbus_port.value,
        )

        # Read once and cached: the unit's identity does not change under a
        # running app, and re-reading it every poll would be six extra
        # round trips for nothing.
        self.identity_published = False
        self.modbus_identity_published = False

        radio_path = radio_mod.find_radio_path(self.config.radio_phy.value)
        if self.config.radio_enabled.value and radio_path is None:
            # Worth saying loudly once at startup: the usual cause is not a
            # missing radio but an app running outside the host network
            # namespace, where this directory is empty by construction.
            log.warning(
                "No ELPRO radio found under %s. If this unit does have a radio, "
                "check the app is running on the Quantum with host networking.",
                radio_mod.RADIO_GLOB,
            )
        else:
            log.info("Reading radio diagnostics from %s", radio_path)

    async def close(self):
        await self.rails_client.close()
        await super().close()

    async def main_loop(self):
        if self.config.radio_enabled.value:
            await self._poll_radio()
        if self.config.charger_enabled.value:
            await self._poll_charger()
        if self.config.config_server_enabled.value:
            await self._poll_config_server()
        if self.config.rails_enabled.value:
            await self._poll_rails()

        await self.tags.last_read.set(int(time.time() * MS_PER_S))

    # -- radio ------------------------------------------------------------

    async def _poll_radio(self):
        try:
            snapshot = radio_mod.read(self.config.radio_phy.value)
        except OSError as e:
            log.warning(f"Radio read failed: {e}")
            snapshot = None

        if snapshot is None:
            await self.tags.radio_present.set(False)
            await self.tags.radio_initialised.set(False)
            await self.tags.radio_alarm.set(False)
            await self.tags.radio_missing_warning_hidden.set(False)
            await self.tags.radio_alarm_warning_hidden.set(True)
            await self.tags.weak_signal_warning_hidden.set(True)
            for tag in (
                self.tags.rssi_dbm,
                self.tags.rssi_last_dbm,
                self.tags.rssi_background_dbm,
                self.tags.snr_db,
            ):
                await tag.set(None)
            return

        await self.tags.radio_present.set(True)
        await self.tags.radio_missing_warning_hidden.set(True)

        await self._publish_radio_health(snapshot)
        await self._publish_radio_signal(snapshot)
        await self._publish_radio_config(snapshot)
        await self._publish_radio_traffic(snapshot)

    async def _publish_radio_health(self, snap: radio_mod.RadioSnapshot):
        await self.tags.radio_initialised.set(snap.initialised)
        await self.tags.radio_alarm.set(snap.alarm)
        await self.tags.radio_alarm_code.set(snap.alarm_code)
        await self.tags.radio_state.set(snap.state)
        await self.tags.radio_driver_state.set(snap.driver_state)
        await self.tags.radio_mode.set(snap.mode)
        await self.tags.radio_uptime_s.set(snap.uptime_s)
        await self.tags.radio_reset_type.set(snap.reset_type)
        await self.tags.radio_last_error.set(snap.last_error)
        await self.tags.radio_init_error.set(snap.init_error)
        await self.tags.radio_tx_enabled.set(bool(snap.tx_enabled))
        await self.tags.radio_rx_enabled.set(bool(snap.rx_enabled))
        await self.tags.radio_driver_restarts.set(snap.driver_restarts)
        await self.tags.pa_temperature_c.set(snap.pa_temperature_c)
        await self.tags.vswr.set(snap.vswr)

        # A radio that never came up is a different fault from one that is up
        # and alarming, but both want the same "look at the radio" indicator.
        unhealthy = snap.alarm or not snap.initialised
        await self.tags.radio_alarm_warning_hidden.set(not unhealthy)
        if unhealthy:
            log.warning(
                f"Radio unhealthy: initialised={snap.initialised} "
                f"alarm={snap.alarm_code} error={snap.init_error or snap.last_error}"
            )

    async def _publish_radio_signal(self, snap: radio_mod.RadioSnapshot):
        await self.tags.rssi_dbm.set(snap.rssi_dbm)
        await self.tags.rssi_background_dbm.set(snap.rssi_background_dbm)
        await self.tags.rssi_last_dbm.set(snap.rssi_last_dbm)
        await self.tags.rssi_error_dbm.set(snap.rssi_error_dbm)
        await self.tags.snr_db.set(snap.snr_db)

        # Judged on the last decoded packet, not the current receiver level:
        # `Current` follows whatever is on the channel including noise, so it
        # would raise a weak-signal warning on a perfectly healthy quiet link.
        # A radio that has never received anything reports no level at all, and
        # that is the missing/alarm warning's job, not this one's.
        weak = (
            snap.rssi_last_dbm is not None
            and snap.rssi_last_dbm < self.config.weak_signal_dbm.value
        )
        await self.tags.weak_signal_warning_hidden.set(not weak)

    async def _publish_radio_config(self, snap: radio_mod.RadioSnapshot):
        await self.tags.radio_band_mhz.set(snap.band_mhz)
        await self.tags.radio_bandwidth_khz.set(snap.bandwidth_khz)
        await self.tags.tx_frequency_mhz.set(snap.tx_frequency_mhz)
        await self.tags.rx_frequency_mhz.set(snap.rx_frequency_mhz)
        await self.tags.tx_power_dbm.set(snap.tx_power_dbm)
        await self.tags.tx_power_max_dbm.set(snap.tx_power_max_dbm)
        await self.tags.antenna_gain_db.set(snap.antenna_gain_db)
        await self.tags.radio_modulation.set(snap.modulation)
        await self.tags.radio_encoding.set(snap.encoding)
        await self.tags.data_rate_kbps.set(snap.data_rate_kbps)
        await self.tags.radio_address.set(snap.address)
        await self.tags.radio_firmware.set(snap.firmware)
        await self.tags.radio_hardware.set(snap.hardware)
        await self.tags.radio_serial.set(snap.serial)

    async def _publish_radio_traffic(self, snap: radio_mod.RadioSnapshot):
        await self.tags.rx_unicast_to_me.set(snap.rx_unicast_to_me)
        await self.tags.rx_unicast_to_other.set(snap.rx_unicast_to_other)
        await self.tags.rx_multicast.set(snap.rx_multicast)
        await self.tags.rx_crc_errors.set(snap.rx_crc_errors)
        await self.tags.rx_unsupported.set(snap.rx_unsupported)
        await self.tags.tx_unicast_ok.set(snap.tx_unicast_ok)
        await self.tags.tx_unicast_failed.set(snap.tx_unicast_failed)
        await self.tags.tx_multicast.set(snap.tx_multicast)
        await self.tags.tx_retries.set(snap.tx_retries)
        await self.tags.tx_success_pct.set(snap.tx_success_pct)
        await self.tags.tx_retry_pct.set(snap.tx_retry_pct)

        await self.tags.util_tx_pct.set(snap.util_tx_pct)
        await self.tags.util_rx_pct.set(snap.util_rx_pct)
        await self.tags.util_error_pct.set(snap.util_error_pct)
        await self.tags.util_holdoff_pct.set(snap.util_holdoff_pct)

        stats = snap.drv_stats
        await self.tags.drv_tx_good.set(stats.get("tx_good"))
        await self.tags.drv_tx_failed.set(stats.get("tx_failed"))
        await self.tags.drv_tx_timeout.set(stats.get("tx_timeout"))
        await self.tags.drv_rx_packets.set(stats.get("rx_packets"))
        await self.tags.drv_rx_dropped.set(stats.get("rx_dropped"))

    # -- charger ----------------------------------------------------------

    async def _poll_charger(self):
        try:
            snapshot = charger_mod.read()
        except OSError as e:
            log.warning(f"Charger read failed: {e}")
            snapshot = None

        if snapshot is None:
            await self.tags.charger_present.set(False)
            await self.tags.charger_available.set(False)
            await self.tags.charger_charging.set(False)
            for tag in (
                self.tags.charger_input_voltage_v,
                self.tags.charger_input_current_a,
                self.tags.charger_input_power_w,
            ):
                await tag.set(None)
            return

        await self.tags.charger_present.set(True)
        await self.tags.charger_available.set(True)
        await self.tags.charger_status.set(snapshot.status)
        await self.tags.charger_charging.set(snapshot.charging)
        await self.tags.charger_input_present.set(snapshot.input_present)
        await self.tags.charger_battery_detected.set(snapshot.battery_detected)
        await self.tags.charger_input_voltage_v.set(snapshot.input_voltage_v)
        await self.tags.charger_input_current_a.set(snapshot.input_current_a)
        await self.tags.charger_input_power_w.set(snapshot.input_power_w)

    # -- unit configuration -----------------------------------------------

    async def _poll_config_server(self):
        try:
            values = await self.config_client.read_many(POWER_KEYS)
            if not self.identity_published:
                await self._publish_identity()
        except ConfigServerError as e:
            log.warning(f"Config server read failed: {e}")
            await self.tags.config_available.set(False)
            return

        await self.tags.config_available.set(True)

        await self.tags.mppt_enabled.set(bool(as_bool(values["mppt_enabled"])))
        panel = values["panel_type"]
        await self.tags.mppt_panel_type.set(
            PANEL_TYPES.get(panel, panel) if panel is not None else None
        )
        await self.tags.charge_limits_custom.set(bool(as_bool(values["custom_limits"])))
        await self.tags.charge_voltage_v.set(as_float(values["charge_voltage"]))
        await self.tags.max_charge_current_a.set(as_float(values["max_charge_current"]))
        await self.tags.input_current_limit_a.set(as_float(values["input_current_limit"]))
        await self.tags.reverse_mode_enabled.set(bool(as_bool(values["reverse_enabled"])))
        await self.tags.reverse_voltage_v.set(as_float(values["reverse_voltage"]))
        await self.tags.reverse_current_a.set(as_float(values["reverse_current"]))

    async def _publish_identity(self):
        values = await self.config_client.read_many(IDENTITY_KEYS)
        await self.tags.unit_model.set(values["model"])
        await self.tags.unit_product.set(values["product"])
        await self.tags.unit_radio_type.set(values["radio_type"])
        self.identity_published = True

    # -- supply and battery rails -----------------------------------------

    async def _poll_rails(self):
        try:
            snapshot = await self.rails_client.read_rails()
            if not self.modbus_identity_published:
                serial, firmware = await self.rails_client.read_identity()
                await self.tags.unit_serial.set(serial)
                await self.tags.unit_firmware.set(firmware)
                self.modbus_identity_published = True
        except RailsError as e:
            log.warning(f"Rail read failed: {e}")
            await self.tags.rails_available.set(False)
            await self.tags.rails_warning_hidden.set(False)
            # Force a re-read of the identity when the link comes back, since
            # a server we lost may not be the same unit we reconnect to.
            self.modbus_identity_published = False
            for tag in (
                self.tags.supply_voltage_v,
                self.tags.supply_current_a,
                self.tags.supply_power_w,
                self.tags.battery_voltage_v,
                self.tags.battery_current_a,
                self.tags.battery_power_w,
            ):
                await tag.set(None)
            return

        await self.tags.rails_available.set(True)
        await self.tags.rails_warning_hidden.set(True)

        await self.tags.supply_voltage_v.set(snapshot.supply_voltage_v)
        await self.tags.supply_current_a.set(snapshot.supply_current_a)
        await self.tags.supply_power_w.set(snapshot.supply_power_w)
        await self.tags.battery_voltage_v.set(snapshot.battery_voltage_v)
        await self.tags.battery_current_a.set(snapshot.battery_current_a)
        await self.tags.battery_power_w.set(snapshot.battery_power_w)
        await self.tags.active_source.set(snapshot.active_source)

        # Published as a value, not a warning: plenty of Quantums are meant to
        # run off their battery, so it says which rail is carrying the unit
        # rather than implying something is wrong.
        await self.tags.running_on_battery.set(snapshot.active_source == "battery")
