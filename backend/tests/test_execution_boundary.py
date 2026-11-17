import httpx
import pytest
import respx

from sim.adapters.llm.broker import BrokerLLM
from sim.services.job_execution import ExecutionLost, require_live_execution


@respx.mock
async def test_lost_worker_does_not_submit_a_new_broker_request(monkeypatch):
    async def lost():
        raise ExecutionLost("connection lost")

    monkeypatch.setattr("sim.adapters.llm.broker.require_live_execution", lost)
    route = respx.post("https://broker.test/v1/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": 1}),
    )
    broker = BrokerLLM("https://broker.test", "fake-key")
    try:
        with pytest.raises(ExecutionLost):
            await broker.chat([{"role": "user", "content": "synthetic"}])
        assert not route.called
    finally:
        await broker.aclose()


async def test_interactive_operations_keep_existing_boundary():
    await require_live_execution()  # no worker context: no new authorization requirement
