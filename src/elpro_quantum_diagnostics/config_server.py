"""Read-only client for the Quantum's own configuration server.

Everything the unit's web portal and its ``cfg`` CLI show comes from a config
daemon listening on ``127.0.0.1:4783``. The protocol is line-oriented plain
text, one request per connection::

    -> read E2Config.PowerSupply.ChargeVoltage\\n
    <- poll:\\nE2Config.PowerSupply.ChargeVoltage 13.8\\n

    -> read E2Config.nosuch.key\\n
    <- poll:\\nnot-found\\n

A key that exists but is unset answers with the key and an empty value.
(Derived by tracing ``cfg read`` on a QE-R-C4 - the protocol is not documented
by ELPRO, so it is treated as best-effort: every failure here degrades to
``None`` and the rest of the dashboard carries on.)

**This module never writes.** That is not incidental: ``cfg write`` on this
firmware silently drops every ``lock="locked"`` item from ``config.conf``,
including the unit's own product identity, so writes through this interface are
not safe to make casually. See ``elpro/quantum-default-route-loss.md`` §3.

Pipelining several reads down one connection was tried and drops responses, so
each key gets its own short-lived connection. On loopback that costs
microseconds and the app reads a dozen keys per poll at most.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4783
DEFAULT_TIMEOUT = 2.0

#: The root of the configuration tree. ELPRO's own scripts take this from
#: ``/bin/config_top_name``; it has been ``E2Config`` across the E2 family.
CONFIG_ROOT = "E2Config"

RESPONSE_HEADER = "poll:"
NOT_FOUND = "not-found"


class ConfigServerError(Exception):
    """The config server could not be reached, or answered unintelligibly."""


class ConfigServerClient:
    """Reads single configuration keys from the unit's config daemon."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"<ConfigServerClient {self.host}:{self.port}>"

    async def read(self, key: str) -> str | None:
        """Return a key's value, or ``None`` if it is unset or unknown.

        Raises :class:`ConfigServerError` if the server cannot be reached, so
        "the daemon is down" stays distinguishable from "the key is not set".
        """
        try:
            return await asyncio.wait_for(self._read(key), timeout=self.timeout)
        except TimeoutError as e:
            raise ConfigServerError(f"timed out reading {key}") from e
        except OSError as e:
            raise ConfigServerError(
                f"cannot reach the config server at {self.host}:{self.port}: {e}"
            ) from e

    async def _read(self, key: str) -> str | None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(f"read {key}\n".encode())
            await writer.drain()
            # The server holds the connection open after answering, so the read
            # is bounded by the response shape (header line, then one value
            # line) rather than by EOF.
            header = (await reader.readline()).decode(errors="replace").strip()
            if header != RESPONSE_HEADER:
                raise ConfigServerError(f"unexpected response header {header!r} for {key}")
            body = (await reader.readline()).decode(errors="replace").rstrip("\n")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

        if not body or body.strip() == NOT_FOUND:
            return None
        # "<key> <value>"; the value may itself contain spaces, and may be
        # empty for a key that exists but is unset.
        _, _, value = body.partition(" ")
        return value.strip() or None

    async def read_many(self, keys: dict[str, str]) -> dict[str, str | None]:
        """Read a batch of keys, mapping caller-chosen names to values.

        A single unreachable server fails the whole batch (there is no useful
        partial answer); an individual missing key is simply ``None``.
        """
        results: dict[str, str | None] = {}
        for name, key in keys.items():
            results[name] = await self.read(key)
        return results


def as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def as_bool(value: str | None) -> bool | None:
    """ELPRO booleans are ``"1"``/``"0"`` throughout the config tree."""
    if value is None:
        return None
    return value.strip() == "1"
