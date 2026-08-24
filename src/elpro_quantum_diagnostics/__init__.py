from pydoover.docker import run_app

from .application import QuantumDiagnosticsApplication


def main():
    """Run the ELPRO Quantum diagnostics application."""
    run_app(QuantumDiagnosticsApplication())
