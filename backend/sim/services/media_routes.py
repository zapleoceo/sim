"""Dormant media preferences: code inventory, never a Broker health/rights check."""
from __future__ import annotations

from sim.services.app_settings import AppSettings


def route_plan(settings: AppSettings) -> list[dict]:
    return [
        {"kind": "photo", "label": "Фото", "chain": [
            {"provider": "Подписка Codex", "model": settings.studio_photo_subscription_model},
            {"provider": "Google — резерв", "model": settings.studio_photo_google_model}],
         "available": False, "reason": "Экспорт PNG, права и бюджет не подтверждены. "
         "Порт генерации фото Google ещё не подключён; vision только анализирует изображения."},
        {"kind": "video_note", "label": "Кружочек", "chain": [
            {"provider": "Google", "model": "gemini/gemini-omni-1.2-flash"}],
         "available": False, "reason": "Нужны подтверждённые video-права, бюджет, контракт "
         "ответа и восстановление квитанции. Упаковка Telegram и маркировка ИИ остаются в SIM."},
        {"kind": "video", "label": "Обычное видео", "chain": [
            {"provider": "Выбор владельца", "model": settings.studio_video_model}],
         "available": False, "reason": "Omni — кандидат с существующим портом Broker; Vivix W1 "
         "ещё не подключён. Качество и итоговая стоимость не сравнены, автоматического выбора нет."},
        {"kind": "video_call", "label": "Видеозвонок", "chain": [
            {"provider": "Vivix — аватар", "model": ""},
            {"provider": "ElevenLabs — голос", "model": ""}],
         "available": False, "reason": "План: проверенный ответ → голос → аватар. Адаптеры, "
         "RTC-мост и Telegram-видеозвонок не подтверждены. Собеседник должен видеть, что это ИИ."},
    ]


def fallback_candidate(outcome: str, *, terminal: bool, receipt_reconciled: bool,
                       target_verified: bool, budget_authorized: bool) -> bool:
    """Future executor precondition, not permission to issue a POST; no runtime caller.

    HTTP status alone is not evidence of nonacceptance. Unknown, timeout, lost receipt,
    financial, safety or permission refusal never authorize a different provider.
    """
    return (outcome == "unavailable_not_accepted" and terminal and receipt_reconciled
            and target_verified and budget_authorized)
