"""Owner-editable settings: prompt texts and tunables kept in the DB (`app_setting`), so the
admin can change them and a deploy never resets them.

Each key has a spec with its code default. A missing row means "use the default"; a stored
value that no longer validates (the spec changed) is ignored with a warning. Defaults of the
keys that used to be environment settings come from the environment (`env_defaults`).

Every prompt sent to a model is editable: the persona prompt is the persona's own text; the guard,
passport and summary prompts and every text the code adds to a model request live here (the
«Промпт · …» groups, in the order they act; defaults in sim.services.instructions). Nothing is
fixed or hidden in code. Reading the settings publishes those texts (`instructions.publish`)."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sim.adapters.db.repositories.settings import AppSettingRepo
from sim.adapters.db.session import transaction
from sim.config import Settings
from sim.services import instructions
from sim.services.content import CAPTION_RULES
from sim.services.guard import GUARD_PROMPT
from sim.services.owner_accounts import parse_ids
from sim.services.passport import PASSPORT_PROMPT
from sim.services.prompt import HISTORY_LIMIT, OUTPUT_CONTRACT, RETRY_NUDGE, PromptTexts
from sim.services.summary import SUMMARY_PROMPT

log = logging.getLogger(__name__)

Kind = Literal["text", "int", "capability", "model", "ids"]
TEXT_MAX = 20000
MEDIA_GROUP = "Фото и кружочки персонажа"
SCHEDULE_GROUP = "Время ответа по распорядку"
OWNER_GROUP = "Аккаунты владельца"
CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,79}$")
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")


PROMPT_REPLY = "Промпт · 1. Ответ в чате: правила"
PROMPT_CONTEXT = "Промпт · 2. Ответ в чате: контекст"
PROMPT_CALL = "Промпт · 3. Звонок"
PROMPT_GUARD = "Промпт · 4. Проверка охранником"
PROMPT_FOLLOWUP = "Промпт · 5. Напоминания и продолжения"
PROMPT_MEDIA = "Промпт · 6. Фото и кружочки"
PROMPT_EXTRA = "Промпт · 7. Вложения и подписи постов"

# key, group, label, where and when the text is applied. The default is `instructions.DEFAULTS[key]`.
INSTRUCTION_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("link_contract", PROMPT_REPLY, "Правило ссылок",
     "Системный промпт каждого ответа и звонка: сразу после промпта персонажа и распорядка."),
    ("unified_boundaries", PROMPT_REPLY, "Обязательные границы",
     "Системный промпт каждого ответа и звонка, после правила ссылок."),
    ("identity_boundary", PROMPT_REPLY, "Честное раскрытие ИИ",
     "Последний абзац системного промпта каждого ответа и звонка. Этот же текст открывает "
     "правило охранника «Проверка раскрытия ИИ»."),
    ("repair_nudge", PROMPT_REPLY, "Повтор после отклонения",
     "Вторая попытка ответа, когда первый ответ не разобрался или охранник его отклонил. "
     "{code}: format или review."),
    ("neutral_handoff_reply", PROMPT_REPLY, "Ответ клиенту при передаче оператору",
     "Отправляется клиенту вместо ответа, если модель дважды не дала ответа, прошедшего проверку, "
     "и диалог уходит оператору."),
    ("routine_header", PROMPT_CONTEXT, "Заголовок распорядка",
     "Перед строками распорядка персонажа в системном промпте (если распорядок заполнен)."),
    ("facts_header", PROMPT_CONTEXT, "Заголовок точных данных",
     "Начало контекста ответа: перед датой, временем, возрастом и распорядком на сейчас, "
     "которые считает код."),
    ("delay_pause_note", PROMPT_CONTEXT, "Задержка: была пауза ИИ",
     "Строка о задержке ответа, когда ответы стояли на паузе ИИ."),
    ("delay_routine_hint", PROMPT_CONTEXT, "Задержка: по распорядку",
     "Строка о задержке ответа, когда персонаж был занят по распорядку."),
    ("delay_pause_hint", PROMPT_CONTEXT, "Задержка: после паузы",
     "Строка о задержке ответа после паузы ИИ без дела по распорядку."),
    ("context_header", PROMPT_CONTEXT, "Заголовок контекста разговора",
     "Контекст каждого ответа и звонка: перед этапом, статусом, потребностями и ссылками."),
    ("known_contact_rule", PROMPT_CONTEXT, "Правило про известное имя",
     "Строка контекста ответа после имени и username собеседника."),
    ("handoff_links_header", PROMPT_CONTEXT, "Заголовок ссылок в другие каналы",
     "Контекст ответа, если у персонажа есть другие каналы: перед списком ссылок."),
    ("contact_profile_note", PROMPT_CONTEXT, "Профиль собеседника",
     "Контекст ответа, когда персонаж прочитал профиль собеседника в Telegram (по его просьбе)."),
    ("contact_photo_note", PROMPT_CONTEXT, "Фото профиля собеседника",
     "Подпись к фото профиля собеседника, которое уходит модели вместе с ответом."),
    ("passport_header", PROMPT_CONTEXT, "Заголовок паспорта",
     "Контекст ответа и звонка: перед паспортом собеседника."),
    ("summary_header", PROMPT_CONTEXT, "Заголовок саммари",
     "Контекст ответа и звонка: перед саммари старой переписки. {until}: до какого времени оно."),
    ("journal_header", PROMPT_CONTEXT, "Заголовок фактов журнала",
     "Контекст ответа и звонка: перед служебными событиями диалога."),
    ("history_trimmed", PROMPT_CONTEXT, "История обрезана",
     "Контекст ответа, когда окно не поместилось в лимит токенов. {hours}, {trimmed}: часы окна "
     "и число отрезанных сообщений."),
    ("attachment_note", PROMPT_CONTEXT, "Распознанное вложение",
     "Перед распознанным содержимым вложения клиента в тексте его сообщения."),
    ("attachment_failed_note", PROMPT_CONTEXT, "Нераспознанное вложение",
     "Вместо содержимого вложения, которое распознать не удалось."),
    ("call_rules", PROMPT_CALL, "Правила звонка",
     "Промпт голосовой модели: после промпта персонажа (без формата ответа)."),
    ("call_identity", PROMPT_CALL, "Кто ты на звонке",
     "Промпт звонка после правил. {name}: имя персонажа."),
    ("call_greeting", PROMPT_CALL, "Первая реплика",
     "Первое сообщение голосовой модели, когда персонаж взял трубку."),
    ("call_history_header", PROMPT_CALL, "Заголовок переписки до звонка",
     "Промпт звонка: перед перепиской этого диалога."),
    ("call_history_trimmed", PROMPT_CALL, "Переписка сокращена",
     "Первая строка переписки в промпте звонка, если она не поместилась целиком."),
    ("call_issue_fact", PROMPT_CALL, "Факт журнала о сбое звонка",
     "Строка фактов журнала в следующих ответах (сутки), если в звонке часть ответа могла не прозвучать."),
    ("guard_control", PROMPT_GUARD, "Проверка решения целиком",
     "Промпт охранника, сразу после его основного промпта (страница «Помощники»)."),
    ("guard_fact_control", PROMPT_GUARD, "Проверяемые факты",
     "Промпт охранника, после проверки решения."),
    ("guard_voice_control", PROMPT_GUARD, "Голосовые ответы",
     "Промпт охранника, после проверяемых фактов."),
    ("guard_media_control", PROMPT_GUARD, "Медиа по запросу",
     "Промпт охранника, после правила голосовых ответов."),
    ("guard_identity_rule", PROMPT_GUARD, "Проверка раскрытия ИИ",
     "Последний абзац промпта охранника, сразу после текста «Честное раскрытие ИИ»."),
    ("followup_event", PROMPT_FOLLOWUP, "Плановое напоминание",
     "Контекст ответа-напоминания, когда клиент молчит. {step}, {total}: номер и число "
     "напоминаний; {hours}: сколько часов молчит; {last}: да или нет."),
    ("reengagement_instruction", PROMPT_FOLLOWUP, "Продолжение после тишины",
     "Контекст плана продолжения беседы после пяти минут тишины. Должен требовать send_at и "
     "plan_reason, иначе план не разберётся."),
    ("reengagement_no_reply", PROMPT_FOLLOWUP, "Продолжение: человек не ответил",
     "Добавляется к продолжению, если после прошлого сообщения персонажа клиент не ответил."),
    ("media_none", PROMPT_MEDIA, "Медиа недоступно",
     "Контекст ответа, когда фото и кружочки сейчас недоступны."),
    ("media_brief", PROMPT_MEDIA, "Медиа можно приложить",
     "Контекст ответа, когда можно приложить фото или кружочек. {kinds}: что можно; {values}: "
     "значения поля kind."),
    ("media_report", PROMPT_MEDIA, "Отказ или «позже»",
     "Контекст каждого ответа после блока медиа: как сообщить решение не присылать."),
    ("media_priority", PROMPT_MEDIA, "Медиа по запросу",
     "Контекст ответа для контактов из «Telegram id: медиа по запросу»."),
    ("media_earlier", PROMPT_MEDIA, "Прошлые решения по медиа",
     "Контекст ответа: перед решениями по медиа за последние сутки."),
    ("neutral_media_scene", PROMPT_MEDIA, "Нейтральная сцена",
     "Сцена фото или кружочка, когда клиент просто попросил медиа, а персонаж ответил согласием."),
    ("photo_frame", PROMPT_MEDIA, "Кадр фото",
     "Первая строка запроса к модели фото персонажа."),
    ("video_note_frame", PROMPT_MEDIA, "Кадр кружочка",
     "Первая строка запроса к модели кружочка персонажа."),
    ("shot_person", PROMPT_MEDIA, "Кто в кадре",
     "Запрос фото и кружочка после вида кадра. {who}: пол и возраст персонажа."),
    ("shot_light", PROMPT_MEDIA, "Освещение кадра",
     "Запрос фото и кружочка после сцены. {light}: ночь, утро, день или вечер по её часам."),
    ("shot_rules", PROMPT_MEDIA, "Ограничения кадра",
     "Последняя строка запроса фото и кружочка."),
    ("vision_prompt", PROMPT_EXTRA, "Описание картинки в превью",
     "Запрос к модели зрения, когда в превью персонажа прикладывают изображение."),
    ("caption_intro", PROMPT_EXTRA, "Подпись поста: роль",
     "Системный промпт подписи поста Instagram. {name}, {agent_prompt}, {language}: имя, "
     "промпт и язык персонажа."),
    ("caption_request", PROMPT_EXTRA, "Подпись поста: запрос",
     "Сообщение модели при генерации подписи. {kind}: тип поста; {idea}: идея или черновик."),
    ("caption_default_idea", PROMPT_EXTRA, "Подпись поста: идея по умолчанию",
     "Подставляется в {idea}, если идея поста не указана."),
)


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
            if model and self.key == "studio_photo_subscription_model" and not model.startswith("codex-sub/"):
                raise SettingError("Studio photo primary requires a codex-sub model")
            if model and self.key == "studio_photo_google_model" and not model.startswith("gemini/"):
                raise SettingError("Studio photo reserve requires a Google Broker model")
            if model and self.key == "studio_video_model" and model != "gemini/gemini-omni-1.2-flash":
                raise SettingError("Studio video model has no supported Broker contract")
            if model and not MODEL.match(model):
                raise SettingError(f"{self.key}: a Broker model id like codex-sub/gpt-6.1-terra is required")
            return model
        if self.kind == "ids":
            if not isinstance(value, str):
                raise SettingError(f"{self.key}: numeric Telegram ids separated by commas are required")
            try:
                return ", ".join(parse_ids(value))
            except ValueError as exc:
                raise SettingError(f"{self.key}: {exc}") from None
        if not isinstance(value, str) or not value.strip():
            raise SettingError(f"{self.key}: a non-empty text is required")
        text = value.strip()
        if self.kind == "capability" and not CAPABILITY.match(text):
            raise SettingError(f"{self.key}: a Broker capability like chat:sales is required")
        if len(text) > TEXT_MAX:
            raise SettingError(f"{self.key}: at most {TEXT_MAX} characters")
        return text


def instruction_specs(*groups: str) -> tuple[SettingSpec, ...]:
    return tuple(SettingSpec(key, group, label, where, "text", instructions.DEFAULTS[key])
                 for key, group, label, where in INSTRUCTION_SPECS if group in groups)


SPECS: tuple[SettingSpec, ...] = instruction_specs(PROMPT_REPLY) + (
    SettingSpec("output_contract", PROMPT_REPLY, "Формат ответа персонажа",
                "Системный промпт каждого ответа после обязательных границ: какой JSON вернуть. Если "
                "сломать формат, ответы перестанут разбираться. В звонке не используется.",
                "text", OUTPUT_CONTRACT),
    SettingSpec("retry_nudge", PROMPT_REPLY, "Повтор при неверном ответе",
                "Что сказать модели, если её ответ не разобрался как JSON.",
                "text", RETRY_NUDGE),
) + instruction_specs(PROMPT_CONTEXT, PROMPT_CALL, PROMPT_GUARD, PROMPT_FOLLOWUP, PROMPT_MEDIA,
                       PROMPT_EXTRA) + (
    SettingSpec("caption_rules", PROMPT_EXTRA, "Подпись поста: правила",
                "Системный промпт подписи поста Instagram, после роли: требования к тексту.",
                "text", CAPTION_RULES),
) + (
    SettingSpec("incoming_media_subscription", "Входящие вложения", "Распознавание по подписке",
                "0 — прежний режим; 1 — изображения, UTF-8 документы и кадры видео через "
                "разрешённую подписочную Terra. Звук, PDF и Office не распознаются; "
                "платного резерва нет. Только новые вложения после включения.", "int", 0, 0, 1),
    SettingSpec("history_limit", "Ответы", "Сколько реплик помнить",
                "Сколько последних сообщений уходит охраннику, паспортистке, в симуляции и звонки. Ответ персонажа в диалоге собирается иначе: см. «Окно контекста».",
                "int", HISTORY_LIMIT, 6, 100),
    SettingSpec("context_window_hours", "Ответы", "Окно контекста, часов",
                "Сообщения диалога за это время персонаж читает целиком (48 = два дня), вместе "
                "со служебными событиями журнала. Что старше, приходит к нему саммари.",
                "int", 48, 1, 720),
    SettingSpec("context_token_limit", "Ответы", "Лимит окна контекста, токенов",
                "Оценка объёма сообщений окна (около трёх знаков на токен). Если окно больше, "
                "самые старые его сообщения обрезаются с пометкой, их покрывает саммари.",
                "int", 6000, 500, 200000),
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
    SettingSpec("summary_model", "Саммари диалога", "Модель",
                "Модель, которая дописывает саммари старой части диалога. Пусто: маршрут chat:sales.",
                "model", ""),
    SettingSpec("summary_prompt", "Саммари диалога", "Инструкция саммари",
                "Как вести саммари сообщений, которые старше окна контекста. Саммари не "
                "пересчитывается с нуля: модель получает текущее и только новые старые сообщения.",
                "text", SUMMARY_PROMPT),
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
    SettingSpec("typo_percent", "Стиль сообщений", "Опечатки, % ответов",
                "Как часто код делает в ответе одну оплошность: пропущенную запятую или опечатку (не в именах, ссылках и цифрах). Не больше одной на весь ответ. 0: никогда.",
                "int", None, 0, 100),
    SettingSpec("lowercase_percent", "Стиль сообщений", "С маленькой буквы, % сообщений",
                "Как часто ответ начинается с маленькой буквы. Точку в самом конце код убирает всегда.",
                "int", None, 0, 100),
    SettingSpec("persona_photo_model", MEDIA_GROUP, "Модель для фото",
                "Подписочная модель Broker с генерацией изображений, которая делает фото персонажа "
                "по референсам из его галереи, например codex-sub/gpt-6.1-sol. Пусто: персонажи "
                "фото не отправляют.",
                "model", ""),
    SettingSpec("persona_media_gap_min", MEDIA_GROUP, "Пауза между медиа, мин",
                "Не чаще одного сгенерированного фото или кружочка на диалог за столько минут. "
                "0: без паузы.",
                "int", 30, 0, 1440),
    SettingSpec("media_priority_telegram_ids", MEDIA_GROUP, "Telegram id: медиа по запросу",
                "Контакты через запятую: выполнять допустимый запрос фото или кружочка, без отказов "
                "по настроению. Доступность, паузы, безопасность и маркировка ИИ сохраняются.", "ids", ""),
    SettingSpec("contact_profile_channel_id", OWNER_GROUP, "Аккаунт Telegram: профиль собеседника",
                "ID одного канала Telegram user: читать доступный профиль и одно фото текущего "
                "собеседника по его просьбе. Без других чатов и управления аккаунтом. 0: выключено.",
                "int", 0, 0, 2_147_483_647),
    SettingSpec("instant_reply_telegram_ids", OWNER_GROUP, "Telegram id: отвечать сразу",
                "Числовые Telegram id через запятую. С этими аккаунтами персонаж отвечает сразу, "
                "как в диалоге с пометкой «Срочный»: без распорядка и занятости, и присылает фото "
                "и кружочки без «Паузы между медиа». Пусто: таких аккаунтов нет.",
                "ids", ""),
    SettingSpec("video_note_owner_telegram_id", OWNER_GROUP, "Telegram id для кружочков ИИ",
                "Числовой Telegram id единственного аккаунта, которому можно отправлять "
                "сгенерированные ИИ кружочки. 0: кружочки не отправляются никому.",
                "int", 0, 0, 9_999_999_999_999),
    SettingSpec("trace_retention_days", "Журнал", "Хранить журнал, дней",
                "Сколько дней хранить записи журнала системы (Активность → Журнал). 0 — не удалять.",
                "int", None, 0, 365),
)
SPECS += (
    SettingSpec("studio_photo_subscription_model", MEDIA_GROUP, "Studio: модель фото по подписке",
                "План, не активация. Точный codex-sub ID; экспорт PNG и права требуют проверки.",
                "model", ""),
    SettingSpec("studio_photo_google_model", MEDIA_GROUP, "Studio: резерв фото Google",
                "План, не активация. Порт генерации ещё отсутствует; vision не генерирует фото.",
                "model", ""),
    SettingSpec("studio_video_model", MEDIA_GROUP, "Studio: модель обычного видео",
                "План, не активация. Пока допустим только gemini/gemini-omni-1.2-flash; "
                "пусто — не выбрана. Качество, права и бюджет ещё не подтверждены.", "model", ""),
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
    incoming_media_subscription: int
    history_limit: int
    context_window_hours: int
    context_token_limit: int
    guard_prompt: str
    guard_model: str
    guard_capability: str
    guard_fallback_capability: str
    passport_model: str
    passport_prompt: str
    summary_model: str
    summary_prompt: str
    typo_percent: int
    lowercase_percent: int
    reply_debounce_s: int
    pause_recovery_window_min: int
    media_priority_telegram_ids: str
    contact_profile_channel_id: int
    instant_reply_telegram_ids: str
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
    studio_photo_subscription_model: str
    studio_photo_google_model: str
    studio_video_model: str
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
        """Every setting with its value in force; publishes the instruction texts as a side effect,
        so a prompt built after any settings read in this process uses the owner's texts."""
        rows = {row.key: row for row in await self.repo.all()}
        out = []
        for spec in SPECS:
            row, default = rows.get(spec.key), self.default(spec)
            value = self.stored(spec, row.value) if row is not None else None
            if value is None:
                out.append(SettingItem(spec, default, default))
            else:
                out.append(SettingItem(spec, value, default, row.updated_at, row.updated_by_id))
        instructions.publish({item.spec.key: item.value for item in out})
        return out

    async def load(self) -> AppSettings:
        names = {field.name for field in fields(AppSettings)}
        return AppSettings(**{item.spec.key: item.value for item in await self.items()
                              if item.spec.key in names})

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


async def publish_instructions(factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """At process start: publish the owner's model texts before the first prompt is built. A
    failure keeps the code defaults; the next settings read publishes them again."""
    try:
        async with transaction(factory) as s:
            await AppSettingsService(s, env_defaults(settings)).items()
    except Exception:
        log.exception("owner instructions not loaded at start; code defaults until the next read")
