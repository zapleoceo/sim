"""Owner-editable settings: prompt texts and tunables kept in the DB (`app_setting`), so the
admin can change them and a deploy never resets them.

Each key has a spec with its code default. A missing row means "use the default"; a stored
value that no longer validates (the spec changed) is ignored with a warning. Defaults of the
keys that used to be environment settings come from the environment (`env_defaults`).

Every prompt sent to a model is editable: the persona prompt is the persona's own text, the
guard prompt and the service prompts live here. The fixed guard text is the decision consistency contract
(`guard.GUARD_CONTROL`); no identity rule is appended to either prompt."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.repositories.settings import AppSettingRepo
from sim.config import Settings
from sim.services.content import CAPTION_RULES
from sim.services.guard import GUARD_PROMPT
from sim.services.passport import PASSPORT_PROMPT
from sim.services.prompt import HISTORY_LIMIT, OUTPUT_CONTRACT, RETRY_NUDGE, PromptTexts

log = logging.getLogger(__name__)

Kind = Literal["text", "int", "capability", "model"]
TEXT_MAX = 20000
MEDIA_GROUP = "Фото и кружочки персонажа"
SCHEDULE_GROUP = "Время ответа по распорядку"
CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,79}$")
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")


class SettingError(ValueError):
    """Unknown key or a value that does not fit the key's spec."""


@dataclass(frozen=True)
class SettingSpec:
    key: str
    group: str
    label: str
    help: str
    kind: Kind
    default: str | int | None = None   # None: comes from env_defaults
    min: int | None = None
    max: int | None = None

    def validate(self, value: Any) -> str | int:
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise SettingError(f"{self.key}: an integer is required")
            if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
                raise SettingError(f"{self.key}: must be between {self.min} and {self.max}")
            return value
        if self.kind == "model":
            if not isinstance(value, str):
                raise SettingError(f"{self.key}: a Broker model id or an empty text is required")
            model = value.strip()
            if model and not MODEL.match(model):
                raise SettingError(f"{self.key}: a Broker model id like codex-sub/gpt-6.1-terra is required")
            return model
        if not isinstance(value, str) or not value.strip():
            raise SettingError(f"{self.key}: a non-empty text is required")
        text = value.strip()
        if self.kind == "capability" and not CAPABILITY.match(text):
            raise SettingError(f"{self.key}: a Broker capability like chat:sales is required")
        if len(text) > TEXT_MAX:
            raise SettingError(f"{self.key}: at most {TEXT_MAX} characters")
        return text


SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("history_limit", "Ответы", "Сколько реплик помнить",
                "Сколько последних сообщений диалога уходит модели и охраннику.",
                "int", HISTORY_LIMIT, 6, 100),
    SettingSpec("guard_prompt", "Охранник", "Промпт охранника",
                "Весь промпт охранника одним текстом: что проверять, вердикты и формат ответа. "
                "Если сломать JSON-формат в конце, проверки перестанут разбираться.",
                "text", GUARD_PROMPT),
    SettingSpec("guard_model", "Охранник", "Модель",
                "Если ответ писала эта же модель, проверку сделает другая.",
                "model", ""),
    SettingSpec("guard_capability", "Охранник", "Маршрут Broker",
                "Capability Broker для контролера. Должна отличаться от модели персонажа.",
                "capability"),
    SettingSpec("guard_fallback_capability", "Охранник", "Запасной маршрут Broker",
                "Capability, если контролеру попалась та же модель, что писала ответ.",
                "capability"),
    SettingSpec("passport_model", "Паспортистка", "Модель",
                "Модель, которая после каждого ответа обновляет паспорт собеседника. Пусто: маршрут chat:sales.",
                "model", ""),
    SettingSpec("passport_prompt", "Паспортистка", "Инструкция паспортистки",
                "Как вести паспорт лида: что собирать и в каком виде. Персонаж читает паспорт перед каждым ответом.",
                "text", PASSPORT_PROMPT),
    SettingSpec("typo_percent", "Стиль сообщений", "Опечатки, % ответов",
                "Как часто код делает в ответе одну оплошность: пропущенную запятую или опечатку (не в именах, ссылках и цифрах). Не больше одной на весь ответ. 0: никогда.",
                "int", None, 0, 100),
    SettingSpec("lowercase_percent", "Стиль сообщений", "С маленькой буквы, % сообщений",
                "Как часто ответ начинается с маленькой буквы. Точку в самом конце код убирает всегда.",
                "int", None, 0, 100),
    SettingSpec("reply_debounce_s", "Ответы", "Пауза перед ответом, сек",
                "Сколько ждать новых сообщений клиента перед ответом (серия сообщений).",
                "int", None, 0, 300),
    SettingSpec("pause_recovery_window_min", "Ответы", "Окно ответа после паузы ИИ, мин",
                "Когда ИИ в диалоге снова включают, персонаж сам отвечает на сообщения клиента, "
                "оставшиеся без ответа, только если последнее из них не старше этого времени. "
                "Более старые остаются оператору, в журнале запись «пропущено, устарело».",
                "int", 120, 1, 10080),
    SettingSpec("reply_schedule_enabled", SCHEDULE_GROUP, "Откладывать ответ по распорядку",
                "1: когда по распорядку персонаж спит, работает, в дороге или занят, ответ ждёт, "
                "пока он сможет заглянуть в чат. 0: отвечать сразу, как раньше.",
                "int", 1, 0, 1),
    SettingSpec("reply_check_sleep_min", SCHEDULE_GROUP, "Сон: заглядывает в чат раз в, мин",
                "0: не отвечает до конца сна. Срочные слова сон не прерывают, только кнопки "
                "«Срочный» и «Ответить сейчас» в диалоге.",
                "int", 0, 0, 1440),
    SettingSpec("reply_check_work_min", SCHEDULE_GROUP, "Работа: заглядывает в чат раз в, мин",
                "Ответ уходит через столько минут или в конце дела, что раньше. 0: только в конце дела.",
                "int", 30, 0, 1440),
    SettingSpec("reply_check_road_min", SCHEDULE_GROUP, "Дорога: заглядывает в чат раз в, мин",
                "Ответ уходит через столько минут или в конце дела, что раньше. 0: только в конце дела.",
                "int", 15, 0, 1440),
    SettingSpec("reply_check_busy_min", SCHEDULE_GROUP, "Другие дела: заглядывает в чат раз в, мин",
                "Любое дело распорядка, которое не сон, не работа, не дорога и не свободное время. "
                "0: только в конце дела.",
                "int", 20, 0, 1440),
    SettingSpec("reply_urgent_delay_min", SCHEDULE_GROUP, "Срочное: ответ через, мин",
                "Срочный диалог получает ответ через столько минут, даже если дело ещё идёт (кроме сна). "
                "0: сразу.",
                "int", 2, 0, 120),
    SettingSpec("reply_urgent_wait_min", SCHEDULE_GROUP, "Порог срочности: ждёт дольше, мин",
                "Если клиент ждёт ответа дольше этого времени, диалог считается срочным. 0: не учитывать.",
                "int", 60, 0, 1440),
    SettingSpec("reply_urgent_words", SCHEDULE_GROUP, "Срочные слова",
                "Через запятую, можно начало слова. Если такое слово есть в сообщениях клиента без "
                "ответа, диалог срочный.",
                "text", "срочно, оплат, предоплат, деньги, перевод, счёт, счет, цена, стоимость, договор, встреч"),
    SettingSpec("reply_sleep_words", SCHEDULE_GROUP, "Слова сна в распорядке",
                "Через запятую, можно начало слова. Дело распорядка с таким словом считается сном.",
                "text", "сплю, сон, спать"),
    SettingSpec("reply_work_words", SCHEDULE_GROUP, "Слова работы в распорядке",
                "Через запятую, можно начало слова. Дело распорядка с таким словом считается работой.",
                "text", "работ, консультац, совещан, клиент, офис, приём, прием, урок"),
    SettingSpec("reply_road_words", SCHEDULE_GROUP, "Слова дороги в распорядке",
                "Через запятую, можно начало слова. Дело распорядка с таким словом считается дорогой.",
                "text", "дорог, еду, в пути, метро, за рулём, за рулем, автобус, поезд, самолёт, такси"),
    SettingSpec("reply_free_words", SCHEDULE_GROUP, "Слова свободного времени в распорядке",
                "Через запятую, можно начало слова. Во время такого дела персонаж отвечает сразу.",
                "text", "свободн, отдых, дома, перерыв"),
    SettingSpec("output_contract", "Служебные промпты", "Формат ответа персонажа",
                "Добавляется в конец промпта персонажа: какой JSON вернуть. Если сломать формат, "
                "ответы перестанут разбираться.",
                "text", OUTPUT_CONTRACT),
    SettingSpec("retry_nudge", "Служебные промпты", "Повтор при неверном ответе",
                "Что сказать модели, если её ответ не разобрался как JSON.",
                "text", RETRY_NUDGE),
    SettingSpec("caption_rules", "Контент", "Правила подписей",
                "Требования к подписи поста в Instagram.",
                "text", CAPTION_RULES),
    SettingSpec("trace_retention_days", "Журнал", "Хранить журнал, дней",
                "Сколько дней хранить записи журнала системы (Активность → Журнал). 0 — не удалять.",
                "int", None, 0, 365),
    SettingSpec("video_note_owner_telegram_id", "Кружочки ИИ", "Telegram id получателя",
                "Числовой Telegram id единственного аккаунта, которому можно отправлять "
                "сгенерированные ИИ кружочки. 0: кружочки не отправляются никому.",
                "int", 0, 0, 9_999_999_999_999),
    SettingSpec("persona_photo_model", MEDIA_GROUP, "Модель для фото",
                "Подписочная модель Broker с генерацией изображений, которая делает фото персонажа "
                "по референсам из его галереи, например codex-sub/gpt-6.1-sol. Пусто: персонажи "
                "фото не отправляют.",
                "model", ""),
    SettingSpec("persona_media_gap_min", MEDIA_GROUP, "Пауза между медиа, мин",
                "Не чаще одного сгенерированного фото или кружочка на диалог за столько минут. "
                "0: без паузы.",
                "int", 30, 0, 1440),
)
BY_KEY: Mapping[str, SettingSpec] = {spec.key: spec for spec in SPECS}


def env_defaults(settings: Settings) -> dict[str, Any]:
    """Defaults of the keys that used to be environment-only."""
    return {
        "guard_capability": settings.guard_capability,
        "guard_fallback_capability": settings.guard_fallback_capability,
        "reply_debounce_s": settings.reply_debounce_s,
        "typo_percent": settings.typo_percent,
        "lowercase_percent": settings.lowercase_percent,
        "trace_retention_days": settings.trace_retention_days,
    }


FALLBACK_ENV = {"guard_capability": "structured", "guard_fallback_capability": "prefilter",
                "reply_debounce_s": 8, "trace_retention_days": 30, "typo_percent": 12, "lowercase_percent": 30}


@dataclass(frozen=True)
class AppSettings:
    history_limit: int
    guard_prompt: str
    guard_model: str
    guard_capability: str
    guard_fallback_capability: str
    passport_model: str
    passport_prompt: str
    typo_percent: int
    lowercase_percent: int
    reply_debounce_s: int
    pause_recovery_window_min: int
    reply_schedule_enabled: int
    reply_check_sleep_min: int
    reply_check_work_min: int
    reply_check_road_min: int
    reply_check_busy_min: int
    reply_urgent_delay_min: int
    reply_urgent_wait_min: int
    reply_urgent_words: str
    reply_sleep_words: str
    reply_work_words: str
    reply_road_words: str
    reply_free_words: str
    output_contract: str
    retry_nudge: str
    caption_rules: str
    trace_retention_days: int
    video_note_owner_telegram_id: int
    persona_photo_model: str
    persona_media_gap_min: int

    def prompt_texts(self) -> PromptTexts:
        return PromptTexts(contract=self.output_contract, retry_nudge=self.retry_nudge)


@dataclass(frozen=True)
class SettingItem:
    spec: SettingSpec
    value: str | int
    default: str | int
    updated_at: datetime | None = None
    updated_by_id: int | None = None

    @property
    def is_default(self) -> bool:
        return self.updated_at is None


class AppSettingsService:
    def __init__(self, session: AsyncSession, env: Mapping[str, Any] | None = None) -> None:
        self.repo = AppSettingRepo(session)
        self.env = {**FALLBACK_ENV, **(env or {})}

    def default(self, spec: SettingSpec) -> str | int:
        return spec.default if spec.default is not None else self.env[spec.key]

    @staticmethod
    def spec(key: str) -> SettingSpec:
        try:
            return BY_KEY[key]
        except KeyError:
            raise SettingError(f"unknown setting {key!r}") from None

    async def items(self) -> list[SettingItem]:
        rows = {row.key: row for row in await self.repo.all()}
        out = []
        for spec in SPECS:
            row, default = rows.get(spec.key), self.default(spec)
            value = self.stored(spec, row.value) if row is not None else None
            if value is None:
                out.append(SettingItem(spec, default, default))
            else:
                out.append(SettingItem(spec, value, default, row.updated_at, row.updated_by_id))
        return out

    async def load(self) -> AppSettings:
        return AppSettings(**{item.spec.key: item.value for item in await self.items()})

    async def item(self, key: str) -> SettingItem:
        return next(item for item in await self.items() if item.spec.key == key)

    async def set(self, key: str, value: Any, admin_id: int | None) -> SettingItem:
        spec = self.spec(key)
        await self.repo.put(key, spec.validate(value), admin_id)
        return await self.item(key)

    async def reset(self, key: str) -> SettingItem:
        self.spec(key)
        await self.repo.delete(key)
        return await self.item(key)

    @staticmethod
    def stored(spec: SettingSpec, value: Any) -> str | int | None:
        try:
            return spec.validate(value)
        except SettingError as exc:
            log.warning("app_setting %s ignored, default used: %s", spec.key, exc)
            return None


async def trace_retention_days(session: AsyncSession, settings: Settings) -> int:
    """How long the system log is kept: the owner setting, else SIM_TRACE_RETENTION_DAYS."""
    item = await AppSettingsService(session, env_defaults(settings)).item("trace_retention_days")
    return int(item.value)
