"""Where this app looks for sysfs.

On a Quantum the answer is always ``/sys``. The one reason to make it movable
is development: the radio and charger are read through absolute sysfs paths, so
without an override there is no way to exercise those code paths anywhere but
on real hardware. Setting ``QUANTUM_SYSFS_ROOT`` to a directory containing a
``sys/class/...`` tree - as ``simulators/quantum_sim.py`` builds - lets the whole
app run on a laptop.

Read from the environment on every call rather than captured at import, so a
test or a simulator can set it after the module is loaded.
"""

from __future__ import annotations

import os

#: Set to a directory prefix to read a stand-in sysfs tree instead of the real
#: one. Unset (the normal case on a Quantum) means the real ``/sys``.
SYSFS_ROOT_ENV = "QUANTUM_SYSFS_ROOT"


def root() -> str:
    """The prefix every sysfs path in this app is resolved against."""
    return os.environ.get(SYSFS_ROOT_ENV, "").rstrip("/")


def path(absolute: str) -> str:
    """Resolve an absolute sysfs path or glob against :func:`root`."""
    return root() + absolute
