"""Config-server client tests against a stub speaking the real wire format.

The exchanges below are literal captures from a QE-R-C4, taken by tracing
``cfg read``. Getting the two "no value" cases apart matters: an unknown key
answers ``not-found`` while a known-but-unset key answers with its own name and
nothing after it, and both must read as ``None`` rather than as the string
``"not-found"`` landing on a dashboard.
"""

import asyncio

import pytest

from elpro_quantum_diagnostics.config_server import (
    ConfigServerClient,
    ConfigServerError,
    as_bool,
    as_float,
)

RESPONSES = {
    "E2Config.PowerSupply.Enable": "poll:\nE2Config.PowerSupply.Enable 0\n",
    "E2Config.PowerSupply.ChargeVoltage": "poll:\nE2Config.PowerSupply.ChargeVoltage 13.8\n",
    "E2Config.webpage.MODEL_NAME": "poll:\nE2Config.webpage.MODEL_NAME QE-R-C4\n",
    "E2Config.networking.eth1.IP_Address": "poll:\nE2Config.networking.eth1.IP_Address \n",
    "E2Config.nosuch.key": "poll:\nnot-found\n",
}


@pytest.fixture
async def server():
    """A stub config daemon: one request per connection, held open after."""

    async def handle(reader, writer):
        line = (await reader.readline()).decode().strip()
        _, _, key = line.partition(" ")
        writer.write(RESPONSES.get(key, "poll:\nnot-found\n").encode())
        await writer.drain()
        # The real daemon does not close after answering, which is why the
        # client reads two lines rather than reading to EOF.
        try:
            await reader.read()
        except (ConnectionError, asyncio.CancelledError):
            pass

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        yield port


async def test_reads_a_value(server):
    client = ConfigServerClient("127.0.0.1", server)
    assert await client.read("E2Config.PowerSupply.ChargeVoltage") == "13.8"
    assert await client.read("E2Config.webpage.MODEL_NAME") == "QE-R-C4"


async def test_unknown_key_is_none(server):
    client = ConfigServerClient("127.0.0.1", server)
    assert await client.read("E2Config.nosuch.key") is None


async def test_set_but_empty_key_is_none(server):
    client = ConfigServerClient("127.0.0.1", server)
    assert await client.read("E2Config.networking.eth1.IP_Address") is None


async def test_read_many_maps_names(server):
    client = ConfigServerClient("127.0.0.1", server)
    values = await client.read_many(
        {
            "mppt": "E2Config.PowerSupply.Enable",
            "charge_voltage": "E2Config.PowerSupply.ChargeVoltage",
            "missing": "E2Config.nosuch.key",
        }
    )
    assert values == {"mppt": "0", "charge_voltage": "13.8", "missing": None}


async def test_unreachable_server_raises():
    """A daemon that is down must not look like a key that is unset."""
    # Port 1 on loopback: reliably refuses rather than hanging.
    client = ConfigServerClient("127.0.0.1", 1, timeout=1.0)
    with pytest.raises(ConfigServerError):
        await client.read("E2Config.PowerSupply.Enable")


def test_coercion():
    assert as_bool("1") is True
    assert as_bool("0") is False
    assert as_bool(None) is None
    assert as_float("13.8") == 13.8
    assert as_float("not a number") is None
    assert as_float(None) is None
