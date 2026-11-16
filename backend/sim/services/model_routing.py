"""Owner-selected conversation routes; never change honesty, guard or Broker rights."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from sim.ports.llm import LLMError, LLMTerminalError


def identity_question(history) -> bool:
    """Conservative multilingual routing hint, not an identity/disclosure policy.

    Only the current user's own question, or a short follow-up to an identity exchange.
    Quotes and third-party narratives do not switch models. False negatives retain the
    same mandatory honest-answer rules on the primary model.
    """
    users = [i for i, turn in enumerate(history) if turn.role == "user"]
    if not users:
        return False
    index = users[-1]
    text = unicodedata.normalize("NFKC", history[index].text).lower().replace("ё", "е")
    text = text.replace("you’re", "you are").replace("you're", "you are")
    text = re.sub(r'"[^"\n]*"|«[^»]*»|“[^”]*”|`[^`]*`', " ", text)
    identity = r"\b(ии|ai|бот|робот|человек|человк|нейросет\w*|искусственн\w*|human|bot|robot|real person|intelligenza artificiale|umano|humano|pessoa real)\b"
    address = r"\b(ты|вы|you|u|sei|eres|você|voce|tu)\b"
    modifiers = r"(?:\s+(?:не|точно|реально|правда|настоящий|настоящая|живой|живая|a|an|are|really|actually|not|real|un|una|um|uma|é|e|non|no|não|nao|isn't|aren't)){0,4}[\s:—-]+"
    if re.search(address + modifiers + identity, text) or re.search(
        r"(?:со мной|мне) (?:отвечает|разговаривает) (?:человек|бот|ии)\b", text
    ):
        # Questions may be implicit ("ты бот", "you're not human"). Negation
        # does not authorize a deceptive answer or suppress disclosure.
        return True
    if text.strip(" ?!.…") in {"а на самом деле", "правда", "точно", "серьезно", "really", "are you", "davvero", "mesmo"}:
        return index >= 2 and identity_question(history[:index - 1])
    return False


def selected_model(persona, history) -> str | None:
    return ((persona.identity_model if identity_question(history) else "") or persona.model or None)


def fallback_allowed(error: LLMTerminalError) -> bool:
    # Stable Broker codes, never infer permission from free-text errors. No quota,
    # billing, auth, cancellation, safety, timeout or unknown-outcome transitions.
    return error.data.get("status") == "error" and error.data.get("error_code") in {
        "model_unavailable", "capacity_busy",
    }


async def routed_chat(llm, messages, *, model, fallback_model="", recovery_key=None, **kwargs):
    """One explicit subscription reserve after a confirmed availability failure.

    Retain terminal receipts across worker generations so replay cannot buy the
    primary again before recovering an already accepted reserve request.
    """
    key = (hashlib.sha256(f"{recovery_key}\0{model}\0{fallback_model}".encode()).hexdigest()
           if recovery_key else None)
    options = {"recovery_key": key, **kwargs}
    if not fallback_model:
        return await llm.chat(messages, model=model, **options)
    options["retry_terminal"] = False
    try:
        return await llm.chat(messages, model=model, **options)
    except LLMTerminalError as error:
        if not fallback_allowed(error) or fallback_model == model:
            raise
        # GET catalog uses the same project credentials; no new key/grant or paid probe.
        rows = await llm.llm.list_models()
        permitted = any(row.get("id") == fallback_model and row.get("available") is True
                        and row.get("billing_type") == "subscription" for row in rows)
        if not permitted:
            raise LLMError("Selected subscription reserve is not confirmed available") from error
        return await llm.chat(messages, model=fallback_model, **options)
