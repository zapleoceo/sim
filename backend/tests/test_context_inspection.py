from sim.adapters.db.models import Persona
from sim.adapters.db.session import transaction
from tests.conftest import OPERATOR, OWNER, login
from tests.test_calls import chatting_world


async def test_owner_context_is_scoped_and_does_not_call_models(api, container, world, fake_llm):
    did = await chatting_world(container, world)
    async with transaction(container.factory) as s:
        other = Persona(name="Other context", strategy_id=world.strategy.id)
        s.add(other)
        await s.flush()
        other_id = other.id
    await login(api, *OWNER)
    before = len(fake_llm.calls)
    result = await api.get(f"/api/personas/{world.persona.id}/context", params={"dialog_id": did})
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    data = result.json()
    assert data["snapshot"]["content"]["dialog_id"] == did
    assert any(layer["key"] == "guard_prompt" for layer in data["layers"])
    assert len(fake_llm.calls) == before
    assert (await api.get(f"/api/personas/{other_id}/context", params={"dialog_id": did})).status_code == 404
    assert (await api.get(f"/api/personas/{world.persona.id}/context")).json()["snapshot"] is None


async def test_operator_cannot_read_owner_context_inspection(api, world):
    await login(api, *OPERATOR)
    assert (await api.get(f"/api/personas/{world.persona.id}/context")).status_code == 403
