"""Basic tests: modules import and the schemas build."""


def test_import_app():
    from elpro_quantum_diagnostics.application import QuantumDiagnosticsApplication

    assert QuantumDiagnosticsApplication


def test_config():
    from elpro_quantum_diagnostics.app_config import QuantumDiagnosticsConfig

    config = QuantumDiagnosticsConfig()
    assert isinstance(QuantumDiagnosticsConfig.to_schema(), dict)
    # Radio counters move slowly and each read costs a transaction on the
    # radio's internal link, so the default poll is deliberately unhurried.
    assert config.poll_interval.default == 30.0
    # Blank means auto-detect, which is right for a single-radio unit.
    assert config.radio_phy.default is None


def test_tags():
    from elpro_quantum_diagnostics.app_tags import QuantumDiagnosticsTags

    assert QuantumDiagnosticsTags


def test_ui():
    from elpro_quantum_diagnostics.app_ui import QuantumDiagnosticsUI

    # Building the schema (as `export-ui` does) exercises the tag bindings, so
    # a UI element bound to a tag that does not exist fails here.
    ui = QuantumDiagnosticsUI(None, None, None)
    assert ui.to_schema(resolve_config=False)
