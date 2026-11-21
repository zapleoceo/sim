"""ChannelRuntime: connect, record the account on the channel, reconnect, stop on a dead
session. Fake Telethon clients, the test's database."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from sim.domain.enums import ChannelStatus
from sim.ports import PortError
from sim.telegram.client import ChannelRuntime, Credentials, account_label, parse_proxy
from tests.fakes.telegram import FakeTelethonClient
from tests.telegram.conftest import seed_world

CREDS = {"api_id": 123, "api_hash": "abc", "session": "1AAA", "proxy": ""}


def decrypt(token: str, keys: str) -> dict[str, Any]:
    assert (token, keys) == ("token", "key")
    return dict(CREDS)


async def eventually(check: Callable[[], bool]) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


class Factory:
    def __init__(self, **client_kw: Any) -> None:
        self.client_kw = client_kw
        self.clients: list[FakeTelethonClient] = []
        self.credentials: list[Credentials] = []

    def __call__(self, credentials: Credentials) -> FakeTelethonClient:
        self.credentials.append(credentials)
        client = FakeTelethonClient(**self.client_kw)
        self.clients.append(client)
        return client


class BlockingSleep:
    """Records the pause, then blocks (the test decides when the loop may go on)."""

    def __init__(self, block: bool = True) -> None:
        self.calls: list[float] = []
        self.block = block
        self.gate = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.block:
            await self.gate.wait()


async def test_connects_records_the_account_runs_hooks_and_reconnects(db, sessions):
    world = await seed_world(db, channel_status=ChannelStatus.NEW)
    factory, hooked = Factory(), []

    async def hook(channel_id: int, client: Any) -> None:
        hooked.append((channel_id, client))

    sleep = BlockingSleep(block=False)
    runtime = ChannelRuntime(sessions, secret_key="key", hooks=[hook], client_factory=factory,
                             decrypt=decrypt, sleep=sleep)
    assert await runtime.start() == [world.channel.id]
    await eventually(lambda: len(hooked) == 1)
    assert await runtime.start() == []                 # already supervised
    channel = world.channel
    assert channel.status == ChannelStatus.CONNECTED and channel.last_error is None
    assert (channel.account_id, channel.account_label) == ("777", "@marina")
    assert factory.credentials[0] == Credentials(123, "abc", "1AAA", "")
    assert runtime.client(channel.id) is factory.clients[0]

    factory.clients[0].drop()                          # Telethon gave up reconnecting
    await eventually(lambda: len(hooked) == 2)
    assert sleep.calls == [2.0]                        # first pause after a working connection
    assert runtime.client(channel.id) is factory.clients[1]
    await runtime.stop()
    assert not factory.clients[1].connected
    with pytest.raises(PortError) as info:
        runtime.client(channel.id)
    assert info.value.retryable


async def test_an_unauthorized_session_is_not_retried(db, sessions):
    world = await seed_world(db)
    factory = Factory(authorized=False)
    runtime = ChannelRuntime(sessions, secret_key="key", client_factory=factory,
                             decrypt=decrypt, sleep=BlockingSleep())
    await runtime.start()
    task = runtime.tasks[world.channel.id]
    await asyncio.wait_for(task, 2)
    assert world.channel.status == ChannelStatus.ERROR
    assert "not authorized" in (world.channel.last_error or "")
    assert len(factory.clients) == 1 and not factory.clients[0].connected
    assert await runtime.start() == []                 # a dead session is not hammered

    world.channel.status = ChannelStatus.NEW            # the owner logged in again
    await db.commit()
    assert await runtime.start() == [world.channel.id]
    await asyncio.wait_for(runtime.tasks[world.channel.id], 2)
    assert len(factory.clients) == 2
    await runtime.stop()


async def test_a_dead_session_error_stops_the_supervisor(db, sessions):
    class AuthKeyUnregisteredError(Exception):
        pass

    world = await seed_world(db)
    factory = Factory(connect_error=AuthKeyUnregisteredError("revoked"))
    runtime = ChannelRuntime(sessions, secret_key="key", client_factory=factory,
                             decrypt=decrypt, sleep=BlockingSleep())
    await runtime.start()
    await asyncio.wait_for(runtime.tasks[world.channel.id], 2)
    assert world.channel.status == ChannelStatus.ERROR
    assert "no longer valid" in (world.channel.last_error or "")


async def test_a_network_error_is_recorded_and_retried_after_a_pause(db, sessions):
    world = await seed_world(db)
    sleep = BlockingSleep()
    factory = Factory(connect_error=OSError("network down"))
    runtime = ChannelRuntime(sessions, secret_key="key", client_factory=factory,
                             decrypt=decrypt, sleep=sleep, backoff_s=(1.0, 7.0))
    await runtime.start()
    await eventually(lambda: bool(sleep.calls))
    assert sleep.calls == [7.0]                        # a failed attempt: the next step
    assert world.channel.status == ChannelStatus.ERROR
    assert world.channel.last_error == "OSError: network down"
    await runtime.stop()


async def test_missing_credentials_and_disabled_channels(db, sessions):
    world = await seed_world(db, credentials=None)
    runtime = ChannelRuntime(sessions, secret_key="key", client_factory=Factory(),
                             decrypt=decrypt, sleep=BlockingSleep())
    await runtime.start()
    await asyncio.wait_for(runtime.tasks[world.channel.id], 2)
    assert world.channel.status == ChannelStatus.ERROR
    assert "no credentials" in (world.channel.last_error or "")

    world.channel.status = ChannelStatus.DISABLED
    await db.commit()
    other = ChannelRuntime(sessions, secret_key="key", client_factory=Factory(),
                           decrypt=decrypt)
    assert await other.start() == []


def test_parse_proxy():
    assert parse_proxy("") is None
    assert parse_proxy("socks5://u:p%40ss@1.2.3.4:1080") == {
        "proxy_type": "socks5", "addr": "1.2.3.4", "port": 1080, "rdns": True,
        "username": "u", "password": "p@ss"}
    assert parse_proxy("1.2.3.4:1080")["proxy_type"] == "socks5"
    with pytest.raises(ValueError, match="proxy"):
        parse_proxy("ftp://host:1")


def test_credentials_and_label():
    assert Credentials.from_json({**CREDS, "api_id": "123"}).api_id == 123
    with pytest.raises(ValueError, match="api_id"):
        Credentials.from_json({"api_id": 0, "api_hash": "", "session": ""})
    me = type("Me", (), {"username": None, "phone": "380", "first_name": "M"})()
    assert account_label(me) == "+380"
