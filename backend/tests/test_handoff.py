"""A lead stays with the persona it came to: identities from other channels of the SAME persona
merge into one client (ref code or phone); never across personas. Plus the handoff links."""
from __future__ import annotations

from sqlalchemy import select

from sim.adapters.db.models import Channel, Client, ClientIdentity, Dialog, Message, Persona
from sim.adapters.db.session import transaction
from sim.domain.enums import ChannelKind, SessionStatus
from tests.conftest import decision_json, inbound
from tests.test_conversation import run_reply
from tests.test_ingest import ingest


async def add_channel(container, persona_id, kind=ChannelKind.WHATSAPP, label="+380501234567") -> int:
    async with transaction(container.factory) as s:
        ch = Channel(kind=kind, persona_id=persona_id, name=kind.value, status=SessionStatus.ACTIVE,
                     account_label=label, session_enc=container.box.encrypt_json({"t": 1}))
        s.add(ch)
        await s.flush()
        return ch.id


async def clients(container) -> list[Client]:
    async with container.factory() as s:
        return list((await s.scalars(select(Client).order_by(Client.id))).all())


async def test_ref_code_merges_the_new_channel_into_the_existing_client(container, world):
    wa = await add_channel(container, world.persona.id)
    await ingest(container, world.channel.id, inbound("Привет", user="tg-1", thread="tg-chat"))
    (client,) = await clients(container)
    res = await ingest(
        container, wa,
        inbound(f"Привет! Я по поводу нашего разговора #{client.ref_code.upper()}",
                user="wa-1", thread="wa-chat", ext="w1"),
    )
    assert not res.duplicate and res.client.id == client.id
    assert len(await clients(container)) == 1
    async with container.factory() as s:
        idents = (await s.scalars(select(ClientIdentity))).all()
        assert {(i.channel_id, i.external_id) for i in idents} == {
            (world.channel.id, "tg-1"), (wa, "wa-1")}
        dialogs = (await s.scalars(select(Dialog))).unique().all()
        assert len(dialogs) == 2 and {d.client_id for d in dialogs} == {client.id}
        texts = [m.text for m in (await s.scalars(select(Message).order_by(Message.id))).all()]
    # the code is stripped from what the AI sees
    assert texts[-1] == "Привет! Я по поводу нашего разговора"
    assert client.ref_code not in " ".join(texts)


async def test_start_command_with_code_merges_and_bare_code_gets_a_placeholder(container, world):
    bot = await add_channel(container, world.persona.id, ChannelKind.INSTAGRAM, "@ig")
    await ingest(container, world.channel.id, inbound("Привет", user="tg-1", thread="tg-chat"))
    (client,) = await clients(container)
    await ingest(container, bot, inbound(f"/start {client.ref_code}", user="ig-1", thread="ig-chat"))
    assert len(await clients(container)) == 1
    async with container.factory() as s:
        last = (await s.scalars(select(Message).order_by(Message.id.desc()))).first()
    assert client.ref_code not in last.text and last.text.startswith("[")


async def test_unknown_code_is_not_a_merge_and_text_is_kept(container, world):
    wa = await add_channel(container, world.persona.id)
    await ingest(container, world.channel.id, inbound("Привет", user="tg-1", thread="tg-chat"))
    await ingest(container, wa, inbound("Привет #zzzzzzzz", user="wa-1", thread="wa-chat", ext="w1"))
    assert len(await clients(container)) == 2
    async with container.factory() as s:
        last = (await s.scalars(select(Message).order_by(Message.id.desc()))).first()
    assert last.text == "Привет #zzzzzzzz"


async def test_same_phone_within_the_persona_merges(container, world):
    tguser = await add_channel(container, world.persona.id, ChannelKind.TELEGRAM_USER, "@eva")
    wa = await add_channel(container, world.persona.id)
    await ingest(container, wa, inbound("Привет", user="wa-1", thread="wa-chat", phone="+380991112233"))
    await ingest(container, tguser, inbound("Здравствуйте", user="tu-1", thread="tu-chat",
                                            phone="+380991112233", ext="t1"))
    cs = await clients(container)
    assert len(cs) == 1 and cs[0].phone == "+380991112233"
    async with container.factory() as s:
        assert len((await s.scalars(select(Dialog))).unique().all()) == 2


async def test_no_merge_across_personas(container, world):
    async with transaction(container.factory) as s:
        eva = Persona(name="Ева", strategy_id=world.strategy.id, language="ru")
        s.add(eva)
        await s.flush()
        eva_id = eva.id
    other_channel = await add_channel(container, eva_id, ChannelKind.TELEGRAM, "@eva_bot")
    await ingest(container, world.channel.id,
                 inbound("Привет", user="a", thread="a-chat", phone="+380991112233"))
    (client,) = await clients(container)
    # same phone, and even the first persona's code: a different persona gets its own client
    res = await ingest(container, other_channel, inbound(
        f"Привет #{client.ref_code}", user="b", thread="b-chat", phone="+380991112233"))
    cs = await clients(container)
    assert len(cs) == 2 and res.client.id != client.id
    assert {c.persona_id for c in cs} == {world.persona.id, eva_id}
    async with container.factory() as s:
        last = (await s.scalars(select(Message).order_by(Message.id.desc()))).first()
    assert client.ref_code in last.text                      # not stripped: no merge happened


async def test_a_second_person_on_the_same_channel_is_not_merged(container, world):
    await ingest(container, world.channel.id, inbound("Привет", user="u1", thread="c1"))
    (client,) = await clients(container)
    await ingest(container, world.channel.id,
                 inbound(f"Это я #{client.ref_code}", user="u2", thread="c2", ext="x"))
    assert len(await clients(container)) == 2


async def test_dynamic_context_lists_the_other_channels_with_the_ref_code(container, world, fake_llm):
    await add_channel(container, world.persona.id, ChannelKind.WHATSAPP, "+380501234567")
    await add_channel(container, world.persona.id, ChannelKind.INSTAGRAM, "@ig")
    await ingest(container, world.channel.id, inbound("Привет"))
    (client,) = await clients(container)
    fake_llm.replies = [decision_json(reply="Привет!")]
    await run_reply(container)
    prompt = fake_llm.gen_calls[0][0]
    context = prompt[-1]["content"]
    assert "https://wa.me/380501234567?text=" in context and f"%23{client.ref_code}" in context
    assert "instagram" not in context.split("продолжить разговор", 1)[1]    # no link for Instagram
    assert client.ref_code not in prompt[0]["content"]                     # stable part stays clean
    # the guard allows exactly the registration link and the handoff link
    import json
    allowed = json.loads(fake_llm.guard_calls[0][0][1]["content"])["allowed_links"]
    assert any(link.startswith("https://wa.me/") for link in allowed)
    assert any("rcipm.com/?ref=" in link for link in allowed)
