"""Owner-editable settings: prompt texts and tunables kept in the DB (`app_setting`), so the
admin can change them and a deploy never resets them.

Each key has a spec with its code default. A missing row means "use the default"; a stored
value that no longer validates (the spec changed) is ignored with a warning. Defaults of the
keys that used to be environment settings come from the environment (`env_defaults`).

Not editable on purpose: the honesty floor (`prompt.HONESTY_RULE`, the guard's rule 1) and the
machine contracts the parsers depend on (`prompt.OUTPUT_CONTRACT`, `guard.GUARD_VERDICT_RULES`)."""
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
from sim.services.guard import GUARD_RULES
from sim.services.prompt import HISTORY_LIMIT, SAFETY_RULES, STYLE_RULE, VOICE_RULE, PromptTexts

log = logging.getLogger(__name__)

Kind = Literal["text", "int", "capability", "model"]
TEXT_MAX = 20000
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
    SettingSpec("voice_rule", "Промпт персонажа", "Роль",
                "Как персонаж живёт в роли и о чём не говорит сам. Общее для всех персонажей.",
                "text", VOICE_RULE),
    SettingSpec("style_rule", "Промпт персонажа", "Стиль речи",
                "Манера письма: длина сообщений, пунктуация, опечатки, эмодзи.",
                "text", STYLE_RULE),
    SettingSpec("safety_rule", "Промпт персонажа", "Безопасность",
                "Минимум: не выполнять инструкции клиента, не просить пароли и карты, не обещать гарантированный доход.",
                "text", SAFETY_RULES),
    SettingSpec("history_limit", "Промпт персонажа", "Сколько реплик помнить",
                "Сколько последних сообщений диалога уходит модели и контролеру.",
                "int", HISTORY_LIMIT, 6, 100),
    SettingSpec("guard_rules", "Охранник", "Промпт охранника",
                "Только критичные проверки ответа перед отправкой. Правило честности на прямой вопрос «ты бот?» неизменяемое и сюда не входит.",
                "text", GUARD_RULES),
    SettingSpec("guard_model", "Охранник", "Модель охранника",
                "Точная модель Broker, например codex-sub/gpt-6.1-terra. Пусто: модель выбирает маршрут ниже. "
                "Если ответ писала та же модель, охранник уйдёт на запасной маршрут.",
                "model", ""),
    SettingSpec("guard_capability", "Охранник", "Маршрут Broker",
                "Capability Broker для контролера. Должна отличаться от модели персонажа.",
                "capability"),
    SettingSpec("guard_fallback_capability", "Охранник", "Запасной маршрут Broker",
                "Capability, если контролеру попалась та же модель, что писала ответ.",
                "capability"),
    SettingSpec("reply_debounce_s", "Ответы", "Пауза перед ответом, сек",
                "Сколько ждать новых сообщений клиента перед ответом (серия сообщений).",
                "int", None, 0, 300),
    SettingSpec("caption_rules", "Контент", "Правила подписей",
                "Требования к подписи поста в Instagram.",
                "text", CAPTION_RULES),
    SettingSpec("trace_retention_days", "Журнал", "Хранить журнал, дней",
                "Сколько дней хранить записи журнала системы (Активность → Журнал). 0 — не удалять.",
                "int", None, 0, 365),
)
BY_KEY: Mapping[str, SettingSpec] = {spec.key: spec for spec in SPECS}


def env_defaults(settings: Settings) -> dict[str, Any]:
    """Defaults of the keys that used to be environment-only."""
    return {
        "guard_capability": settings.guard_capability,
        "guard_fallback_capability": settings.guard_fallback_capability,
        "reply_debounce_s": settings.reply_debounce_s,
        "trace_retention_days": settings.trace_retention_days,
    }


FALLBACK_ENV = {"guard_capability": "structured", "guard_fallback_capability": "prefilter",
                "reply_debounce_s": 8, "trace_retention_days": 30}


@dataclass(frozen=True)
class AppSettings:
    voice_rule: str
    style_rule: str
    safety_rule: str
    history_limit: int
    guard_rules: str
    guard_model: str
    guard_capability: str
    guard_fallback_capability: str
    reply_debounce_s: int
    caption_rules: str
    trace_retention_days: int

    def prompt_texts(self) -> PromptTexts:
        return PromptTexts(voice=self.voice_rule, style=self.style_rule, safety=self.safety_rule)


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
