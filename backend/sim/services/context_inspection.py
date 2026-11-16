"""Read-only inspection of saved instructions and optionally one authorized dialog."""
from sim.adapters.db.models import Persona
from sim.services.conversation import ConversationService, history_turns
from sim.services.guard import guard_system_prompt
from sim.services.reengagement import INSTRUCTION
from sim.services.safe_payload import safe_payload


async def inspect_context(conv: ConversationService, persona: Persona, dialog_id: int | None) -> dict:
    cfg = await conv.config()
    mapping = await conv.prompt_map(persona)
    layers = [dict(key=p.key, title=p.title, source=p.source, field=p.field,
                   payload=safe_payload(p.text)) for p in mapping.parts]
    layers.extend(dict(key=r.key, title=r.title, source="code", field="", payload=safe_payload(r.text))
                  for r in mapping.rules)
    for key, title, value in (
        ("guard_prompt", "Проверка и повторная проверка исправления", guard_system_prompt(cfg.guard_prompt)),
        ("retry_nudge", "Повтор генерации при неверном формате", cfg.retry_nudge),
        ("passport_prompt", "Обновление паспорта", cfg.passport_prompt),
        ("summary_prompt", "Сжатие памяти", cfg.summary_prompt),
        ("caption_rules", "Подписи контента", cfg.caption_rules),
    ):
        layers.append(dict(key=key, title=title, source="setting", field=key, payload=safe_payload(value)))
    layers.append(dict(key="reengagement", title="План продолжения после тишины", source="code", field="",
                       payload=safe_payload(INSTRUCTION)))
    layers.append(dict(key="routing", title="Модели, память и обработка стиля", source="setting", field="",
                       payload=safe_payload({"persona_model": persona.model,
                           **{key: getattr(cfg, key) for key in (
                               "guard_model", "guard_capability", "guard_fallback_capability", "passport_model",
                               "summary_model", "history_limit", "context_window_hours", "context_token_limit",
                               "typo_percent", "lowercase_percent")}})))
    snapshot = None
    if dialog_id is not None:
        dialog = await conv.dialogs.get(dialog_id)
        if dialog is None or dialog.client.persona_id != persona.id:
            raise ValueError("Dialog is not part of this persona")
        strategy = await conv.strategy_for(persona.id)
        history, ctx = await conv.memory(dialog.id, await conv.dialog_context(dialog, persona), refresh_summary=False)
        snapshot = safe_payload({
            "dialog_id": dialog.id, "assembled_at_utc": conv.clock().isoformat() + "Z",
            "text_messages": await conv.prompt(persona, strategy, history_turns(history), ctx, whole_history=True),
            "voice_system": await conv.call_prompt(dialog, persona),
        })
    return {"persona_id": persona.id, "layers": layers, "snapshot": snapshot,
            "example_context": safe_payload(mapping.context),
            "notice": "Сборка на сейчас, не сохранённый исторический запрос. Модель не вызывается; память не обновляется. "
                      "Формат и обязательные проверки имеют приоритет над пожеланиями стиля. "
                      "Fallback меняет маршрут модели, а не отменяет проверку; rewrite проверяется повторно. "
                      "Фактически отправленные запросы смотрите в журнале вызовов; старые payload могут отсутствовать."}
