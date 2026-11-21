"""Fixtures of the adapter tests: the Broker is a respx router (no network) and time is fake."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import respx

from sim.adapters.broker.http import BrokerHttp
from tests.adapters.helpers import BASE_URL, PROJECT_KEY, FakeTime


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def broker() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def http(fake_time: FakeTime) -> AsyncIterator[BrokerHttp]:
    client = BrokerHttp(BASE_URL, PROJECT_KEY, poll_budget_s=60.0, media_budget_s=600.0,
                        sleep=fake_time.sleep, clock=fake_time.clock)
    yield client
    await client.aclose()
