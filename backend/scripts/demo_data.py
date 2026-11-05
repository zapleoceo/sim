"""Dev-only demo data for local UI checks. REFUSES to run on anything but a SQLite database.

    uv run alembic upgrade head && uv run python -m sim.seed && uv run python scripts/demo_data.py

Fills the seeded catalog with ~140 clients, dialogs, partner events, LLM calls, channels, content
posts, simulation branches and two extra admins. Deterministic (random.seed), no real secrets:
channel sessions are fake dicts encrypted with the normal SecretBox. Prints a one-time login link
for the superadmin."""
from __future__ import annotations

import asyncio
import random
import sys
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import (
    AdminUser,
    Channel,
    Client,
    ClientIdentity,
    ContentPost,
    Dialog,
    FunnelEvent,
    LlmCall,
    Message,
    PartnerEvent,
    Persona,
    SimBranch,
    SimMessage,
)
from sim.adapters.db.repositories.catalog import ChannelRepo, PersonaRepo
from sim.adapters.db.repositories.conversations import ClientRepo, DialogRepo
from sim.adapters.db.session import transaction
from sim.clock import utcnow
from sim.config import Settings, settings
from sim.domain.enums import (
    Author,
    ChannelKind,
    ClientStatus,
    ContentKind,
    ContentStatus,
    Delivery,
    Direction,
    PartnerEventKind,
    Role,
    SessionStatus,
    Stage,
)
from sim.persona_prompts import EVA, MARINA
from sim.services.ingest import new_ref_code
from sim.wiring import Container, build_container

SUPERADMIN_TG = 169510539
LOCAL_FRONTEND_URL = "http://localhost:4200"
CLIENTS = 140
MODEL = "claude-sub/claude-sonnet-5-5"
GUARD_MODEL = "codex-sub/gpt-6.1-sol"

FIRST = ["Анна", "Мария", "Ольга", "Елена", "Наталья", "Ирина", "Светлана", "Татьяна", "Юлия",
         "Екатерина", "Алексей", "Дмитрий", "Сергей", "Андрей", "Максим", "Иван", "Виктор",
         "Олег", "Павел", "Николай", "Оксана", "Виктория", "Дарья", "Кирилл", "Артём"]
LAST = ["Коваленко", "Шевченко", "Бондаренко", "Мельник", "Ткаченко", "Кравец", "Савченко",
        "Лысенко", "Руденко", "Мороз", "Волков", "Смирнова", "Попов", "Новикова", "Фёдоров"]
NEEDS = ["карьерный рост", "системное мышление", "управление проектами", "смена профессии",
         "работа с командой", "стратегическое планирование", "сертификат", "онлайн-формат"]
NEXT_STEPS = ["уточнить цели", "рассказать о программе", "отправить ссылку на регистрацию",
              "напомнить о вебинаре", "ответить на вопрос о цене", "передать партнёру"]
CLIENT_LINES = [
    "Здравствуйте! Увидела вашу страницу, расскажите подробнее", "А сколько это стоит?",
    "Мне интересно, подходит ли это для новичка", "Какая длительность программы?",
    "Я работаю менеджером проектов, хочу расти", "Есть ли сертификат по итогу?",
    "Можно онлайн, без выезда?", "Подумаю и отвечу позже", "А кто преподаёт?",
    "Хорошо, давайте ссылку", "Спасибо, зарегистрировалась", "Оплатил, что дальше?",
    "Мне не очень удобно по времени", "Это не развод?", "Вы живой человек?",
]
AI_LINES = [
    "Здравствуйте! Рада знакомству, расскажите, чем занимаетесь сейчас?",
    "Понимаю вас. Программа рассчитана как раз на тех, кто хочет мыслить системно.",
    "Формат онлайн, занятия записываются, так что можно догонять в своём темпе.",
    "Точных цен у меня под рукой нет, уточню и вернусь с ответом.",
    "Вот ссылка для регистрации, она персональная: https://rcipm.com/?ref={ref}",
    "Отлично, что вы с нами! Если появятся вопросы, пишите.",
    "Хотите, расскажу, как выглядит первое занятие?",
    "Давайте я напомню вам через пару дней, чтобы не потерялось.",
]
OPERATOR_LINES = ["Добрый день! Меня зовут Игорь, я оператор проекта, помогу с вопросом.",
                  "Сейчас уточню у партнёра и отвечу в этом же чате."]
VIOLATIONS = ["обещание дохода", "давление на клиента", "выдуманные условия"]
POSTS = [
    ("photo", "Утренний ритуал: 10 минут планирования экономят весь день."),
    ("reel", "Три вопроса, которые стоит задать себе перед стратегической сессией."),
    ("story", "Сегодня разбираем кейс про системное мышление. Заходите!"),
    ("photo", "Кофе, блокнот и спокойная голова: так выглядит мой вторник."),
    ("video", "Как я учусь видеть связи там, где раньше видела хаос."),
    ("photo", "Маленькие шаги ведут к большим решениям."),
]
STAGE_WEIGHTS = [
    (Stage.FIRST_CONTACT, 24), (Stage.ENGAGED, 26), (Stage.NEEDS_IDENTIFIED, 20),
    (Stage.INTERESTED, 14), (Stage.HANDED_TO_PARTNER, 8), (Stage.REGISTERED, 5), (Stage.PAID, 3),
]
STAGES = list(Stage)


def refuse_unless_sqlite(cfg: Settings) -> None:
    if not cfg.database_url.startswith("sqlite"):
        print(
            "REFUSED: demo data is for a local SQLite database only (SIM_DATABASE_URL must start "
            "with 'sqlite'); this one is not, and filling it would pollute real data.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def ago(now: datetime, *, days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
    return now - timedelta(days=days, hours=hours, minutes=minutes)


async def ensure_admins(c: Container, s: AsyncSession) -> dict[str, AdminUser]:
    await c.auth(s).sync_superadmins([SUPERADMIN_TG])
    await s.flush()
    for tg_id, role, name in ((700000001, Role.OWNER, "Демо Владелец"),
                              (700000002, Role.OPERATOR, "Игорь Оператор")):
        exists = await s.scalar(select(AdminUser).where(AdminUser.telegram_id == tg_id))
        if exists is None:
            s.add(AdminUser(telegram_id=tg_id, role=role, display_name=name, tg_username=f"demo_{tg_id}"))
    await s.flush()
    rows = (await s.scalars(select(AdminUser))).all()
    return {u.role.value: u for u in rows if u.telegram_id in (SUPERADMIN_TG, 700000001, 700000002)}


async def make_channels(
    c: Container, s: AsyncSession, personas: dict[str, Persona], now: datetime
) -> list[Channel]:
    spec = [  # persona, kind, status, label, error
        (MARINA, ChannelKind.INSTAGRAM, SessionStatus.ACTIVE, "@marina.strategy", None),
        (MARINA, ChannelKind.WHATSAPP, SessionStatus.ACTIVE, "+380501112233", None),
        (MARINA, ChannelKind.TELEGRAM, SessionStatus.ACTIVE, "@marina_rcipm_bot", None),
        (MARINA, ChannelKind.TELEGRAM_USER, SessionStatus.CHALLENGE, "+380501112233", None),
        (MARINA, ChannelKind.INSTAGRAM, SessionStatus.ERROR, "@marina.reserve",
         "ChallengeRequired: Instagram просит подтвердить вход"),
        (EVA, ChannelKind.INSTAGRAM, SessionStatus.ACTIVE, "@eva.thinks", None),
        (EVA, ChannelKind.WHATSAPP, SessionStatus.PENDING, "", None),
        (EVA, ChannelKind.TELEGRAM, SessionStatus.ACTIVE, "@eva_rcipm_bot", None),
        (EVA, ChannelKind.TELEGRAM_USER, SessionStatus.ACTIVE, "@eva_personal", None),
    ]
    repo, out = ChannelRepo(s), []
    for persona, kind, status, label, error in spec:
        fake_session = {"demo": True, "token": f"demo-{kind.value}-not-a-real-secret"}
        out.append(await repo.add(Channel(
            kind=kind, persona_id=personas[persona].id, name=f"{persona} / {kind.value}",
            status=status, account_label=label, last_error=error,
            last_activity_at=ago(now, minutes=7) if status is SessionStatus.ACTIVE else None,
            session_enc=c.box.encrypt_json(fake_session) if status is not SessionStatus.NEW else None,
        )))
    return out


def pick_stage(rng: random.Random) -> Stage:
    return rng.choices([s for s, _ in STAGE_WEIGHTS], [w for _, w in STAGE_WEIGHTS])[0]


def pick_status(rng: random.Random, stage: Stage) -> ClientStatus:
    if stage in (Stage.REGISTERED, Stage.PAID):
        return ClientStatus.ACTIVE
    return rng.choices(
        list(ClientStatus), [70, 10, 12, 8] if stage is not Stage.HANDED_TO_PARTNER else [85, 5, 10, 0]
    )[0]


def thread_times(rng: random.Random, start: datetime, now: datetime, n: int) -> list[datetime]:
    """n increasing timestamps from start, all before now (gaps shrink to fit)."""
    span = min((now - start).total_seconds() - 60, rng.uniform(1, 72) * 3600)
    cuts = sorted(rng.uniform(0, max(span, 1)) for _ in range(n))
    return [start + timedelta(seconds=x) for x in cuts]


def message_rows(  # noqa: PLR0913
    rng: random.Random, dialog: Dialog, client: Client, n: int, stamps: list[datetime],
    operator_involved: bool, last_from_client: bool,
) -> list[Message]:
    rows: list[Message] = []
    for i, at in enumerate(stamps):
        from_client = i % 2 == 0
        if i == n - 1:
            from_client = last_from_client
        if from_client:
            rows.append(Message(
                dialog_id=dialog.id, direction=Direction.IN, author=Author.CLIENT,
                text=rng.choice(CLIENT_LINES), external_id=f"d{dialog.id}-m{i}", created_at=at))
            continue
        if operator_involved and i >= n - 3:
            rows.append(Message(
                dialog_id=dialog.id, direction=Direction.OUT, author=Author.OPERATOR,
                text=rng.choice(OPERATOR_LINES), external_id=f"d{dialog.id}-m{i}", created_at=at))
            continue
        verdict = rng.choices(["ok", "rewrite", "block"], [82, 12, 6])[0]
        text = rng.choice(AI_LINES).format(ref=client.ref_code)
        blocked = verdict == "block"
        rows.append(Message(
            dialog_id=dialog.id, direction=Direction.OUT, author=Author.AI, text=text,
            external_id=None if blocked else f"d{dialog.id}-m{i}",
            delivery=Delivery.CANCELLED if blocked else Delivery.SENT,
            error="guard: ответ заблокирован" if blocked else None,
            model=MODEL, provider="anthropic", tokens_in=rng.randint(900, 2400),
            tokens_out=rng.randint(40, 220), cost_usd=0.0, guard_verdict=verdict,
            guard_violations=[rng.choice(VIOLATIONS)] if verdict != "ok" else None,
            guard_model=GUARD_MODEL,
            original_text="Это гарантированно изменит вашу жизнь!" if verdict == "rewrite" else None,
            created_at=at))
    return rows


async def make_clients(  # noqa: PLR0912, PLR0915
    s: AsyncSession, rng: random.Random, now: datetime, channels: list[Channel],
    personas: dict[str, Persona], admins: dict[str, AdminUser],
) -> dict[str, int]:
    live = [ch for ch in channels if ch.status is SessionStatus.ACTIVE]
    clients_repo, dialogs_repo = ClientRepo(s), DialogRepo(s)
    stats = {"dialogs": 0, "messages": 0, "needs_operator": 0, "unread": 0, "partner_events": 0}
    needs_operator_left, unread_left = 8, 22
    for i in range(CLIENTS):
        ch = live[i % len(live)]            # every active channel (and kind) gets clients
        stage, name = pick_stage(rng), f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        created = ago(now, days=rng.uniform(0.2, 30))
        n_msgs = rng.randint(3, 25) if stage is not Stage.FIRST_CONTACT else rng.randint(3, 6)
        stamps = thread_times(rng, created, now, n_msgs)
        status = pick_status(rng, stage)
        wants_operator = needs_operator_left > 0 and status is ClientStatus.ACTIVE and i % 17 == 3
        last_from_client = wants_operator or (unread_left > 0 and i % 6 == 1)
        client = await clients_repo.add(Client(
            persona_id=ch.persona_id, display_name=name, handle=f"@demo_{i:03d}",
            phone=f"+38050{rng.randint(1000000, 9999999)}" if ch.kind is ChannelKind.WHATSAPP else "",
            ref_code=new_ref_code(), stage=stage, status=status,
            needs=rng.sample(NEEDS, rng.randint(0, 3)) if stage is not Stage.FIRST_CONTACT else [],
            next_step=rng.choice(NEXT_STEPS) if status is ClientStatus.ACTIVE else "",
            next_step_at=now + timedelta(hours=rng.randint(1, 72)) if status is ClientStatus.ACTIVE else None,
            last_contact_at=stamps[-1], created_at=created,
        ))
        s.add(ClientIdentity(client_id=client.id, channel_id=ch.id, channel_kind=ch.kind,
                             external_id=f"demo-user-{i:03d}"))
        dialog = await dialogs_repo.add(Dialog(
            channel_id=ch.id, external_thread_id=f"demo-thread-{i:03d}", client_id=client.id,
            needs_operator=wants_operator, created_at=created,
            operator_id=admins["operator"].id if wants_operator else None,
            ai_paused=wants_operator,
        ))
        rows = message_rows(rng, dialog, client, n_msgs, stamps, wants_operator, last_from_client)
        s.add_all(rows)
        last = rows[-1]
        dialog.last_message_text, dialog.last_message_at = last.text, last.created_at
        dialog.last_message_direction = last.direction
        if last_from_client and not wants_operator:
            dialog.unread, unread_left = rng.randint(1, 3), unread_left - 1
            stats["unread"] += 1
        if wants_operator:
            dialog.unread, needs_operator_left = 1, needs_operator_left - 1
            stats["needs_operator"] += 1
        stats["dialogs"] += 1
        stats["messages"] += len(rows)
        # funnel history: one event per stage step, spread between creation and the last message
        path = STAGES[: STAGES.index(stage) + 1]
        for step, (old, new) in enumerate(zip(path, path[1:], strict=False), start=1):
            actor = "partner" if new in (Stage.REGISTERED, Stage.PAID) else "ai"
            s.add(FunnelEvent(
                client_id=client.id, kind="stage", from_value=old.value, to_value=new.value,
                actor=actor, reason=f"demo {actor}",
                created_at=created + (stamps[-1] - created) * step / len(path)))
        stats["partner_events"] += add_partner_events(s, rng, client, stage, stamps[-1], i)
    return stats


def add_partner_events(
    s: AsyncSession, rng: random.Random, client: Client, stage: Stage, last: datetime, i: int
) -> int:
    if stage not in (Stage.REGISTERED, Stage.PAID):
        return 0
    s.add(PartnerEvent(
        client_id=client.id, kind=PartnerEventKind.REGISTRATION, external_id=f"demo-reg-{i}",
        source="webhook", occurred_at=last - timedelta(hours=2), currency=""))
    if stage is not Stage.PAID:
        return 1
    amount = float(rng.choice([29, 49, 79, 99, 149, 199]))
    s.add(PartnerEvent(
        client_id=client.id, kind=PartnerEventKind.PAYMENT, external_id=f"demo-pay-{i}",
        amount=amount, currency="USD", payout=round(amount * 0.2, 2), source="webhook",
        occurred_at=last - timedelta(minutes=30)))
    return 2


async def make_llm_calls(s: AsyncSession, rng: random.Random, now: datetime) -> int:
    dialog_ids = list((await s.scalars(select(Dialog.id))).all())
    workflows = [("reply", "chat:sales", 62), ("guard", "structured", 28), ("followup", "chat:sales", 7),
                 ("content", "chat:sales", 3)]
    for _ in range(600):
        workflow, capability, _w = rng.choices(workflows, [w[2] for w in workflows])[0]
        failed = rng.random() < 0.03
        s.add(LlmCall(
            dialog_id=rng.choice(dialog_ids) if workflow != "content" else None,
            workflow=workflow, capability=capability,
            model=GUARD_MODEL if workflow == "guard" else MODEL,
            provider="openai" if workflow == "guard" else "anthropic",
            tokens_in=None if failed else rng.randint(800, 3200),
            tokens_out=None if failed else rng.randint(30, 400),
            cached_tokens=None if failed else rng.randint(0, 1800), cost_usd=0.0,
            latency_ms=rng.randint(900, 9000), error="broker: timeout" if failed else None,
            job_id=str(rng.randint(10000, 99999)), finish_reason=None if failed else "stop",
            created_at=ago(now, days=rng.uniform(0, 30))))
    return 600


async def make_content(
    s: AsyncSession, personas: dict[str, Persona], channels: list[Channel], now: datetime
) -> int:
    ig = {ch.persona_id: ch for ch in channels if ch.kind is ChannelKind.INSTAGRAM
          and ch.status is SessionStatus.ACTIVE}
    statuses = list(ContentStatus)
    for i, (kind, caption) in enumerate(POSTS * 2):
        persona = personas[MARINA if i % 2 == 0 else EVA]
        status = statuses[i % len(statuses)]
        s.add(ContentPost(
            persona_id=persona.id, channel_id=ig[persona.id].id, kind=ContentKind(kind),
            caption=caption, media_urls=[], status=status,
            scheduled_at=now + timedelta(days=i % 5 + 1) if status is ContentStatus.SCHEDULED else None,
            published_at=ago(now, days=i % 9 + 1) if status is ContentStatus.PUBLISHED else None,
            error="Instagram: media upload failed" if status is ContentStatus.FAILED else None,
            created_at=ago(now, days=i % 12 + 1)))
    return len(POSTS) * 2


async def make_branches(
    s: AsyncSession, personas: dict[str, Persona], admins: dict[str, AdminUser], now: datetime
) -> None:
    dialogue = [("client", "Привет! Что у вас за программа?", None),
                ("ai", "Здравствуйте! Это про системное мышление. Вам для работы или для себя?", "ok"),
                ("client", "Для работы, я руковожу отделом", None),
                ("ai", "Тогда вам точно будет полезно. Расскажете, какие задачи сейчас самые сложные?",
                 "rewrite"),
                ("client", "Сколько стоит?", None),
                ("ai", "Точную цену уточню и вернусь с ответом.", "ok")]
    for k, (persona, title, stage) in enumerate(
        ((MARINA, "Холодный лид из Instagram", Stage.ENGAGED), (EVA, "Скептик: «это развод?»",
                                                                 Stage.NEEDS_IDENTIFIED))
    ):
        branch = SimBranch(
            admin_user_id=admins["superadmin"].id, persona_id=personas[persona].id, title=title,
            stage=stage, needs=["системное мышление"], next_step="уточнить цели",
            created_at=ago(now, hours=5 + k), last_active_at=ago(now, hours=1 + k))
        s.add(branch)
        await s.flush()
        for j, (role, text, verdict) in enumerate(dialogue):
            s.add(SimMessage(
                branch_id=branch.id, role=role, text=text, guard_verdict=verdict,
                guard_violations=["обещание дохода"] if verdict == "rewrite" else None,
                original_text="Это гарантированно повысит ваш доход!" if verdict == "rewrite" else None,
                model=MODEL if role == "ai" else None,
                created_at=ago(now, hours=5 + k) + timedelta(minutes=2 * j)))


async def run(cfg: Settings) -> str:
    c = build_container(cfg)
    rng, now = random.Random(20261106), utcnow()  # noqa: S311  (demo data: seeded, not crypto)
    try:
        async with transaction(c.factory) as s:
            if await s.scalar(select(func.count(Client.id))):
                print("REFUSED: the database already has clients (demo data was loaded or this is "
                      "not a fresh DB). Recreate it: delete sim.db, alembic upgrade head, "
                      "python -m sim.seed.", file=sys.stderr)
                raise SystemExit(1)
            personas = {p.name: p for p in await PersonaRepo(s).all() if p.name in (MARINA, EVA)}
            if len(personas) != 2:
                print("Seeded personas not found: run `python -m sim.seed` first.", file=sys.stderr)
                raise SystemExit(1)
            admins = await ensure_admins(c, s)
            channels = await make_channels(c, s, personas, now)
            stats = await make_clients(s, rng, now, channels, personas, admins)
            stats["llm_calls"] = await make_llm_calls(s, rng, now)
            stats["content_posts"] = await make_content(s, personas, channels, now)
            await make_branches(s, personas, admins, now)
        async with transaction(c.factory) as s:
            token = await c.auth(s).create_login_link(SUPERADMIN_TG)
    finally:
        await c.aclose()
    print(f"Demo data loaded: {stats}")
    # The same one-time link as `python -m sim.cli login-link`; the base is SIM_PUBLIC_URL when set,
    # otherwise the local Angular dev server (its proxy forwards /api to the backend).
    base = cfg.public_url if "public_url" in cfg.model_fields_set else LOCAL_FRONTEND_URL
    return f"{base.rstrip('/')}/api/auth/link?token={token}"


def main() -> None:
    cfg = settings()
    refuse_unless_sqlite(cfg)
    link = asyncio.run(run(cfg))
    print(f"Login link (one-time, 10 minutes): {link}")


if __name__ == "__main__":
    main()
