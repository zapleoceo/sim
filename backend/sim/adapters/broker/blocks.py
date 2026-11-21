"""SIM chat messages → the Broker's OpenAI-style `messages`, plus the request choices that depend
on them (capability, response format, workflow tag).

Every attachment block format lives in `attachment_block`: when the Broker changes how it takes
video or PDF, that function is the only thing to adjust."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sim.adapters.broker.http import encode_b64, is_subscription_model
from sim.ports import ChatMessage, ChatPart, PortError

PDF_MIME = "application/pdf"
PDF_FILENAME = "document.pdf"
DEFAULT_SCHEMA_NAME = "answer"
MAX_TOKENS_LIMIT = 16_384           # the Broker's accepted range for chat jobs is 1..16384
_TAG_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]+")
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{encode_b64(data)}"


def attachment_block(data: bytes, mime: str) -> dict[str, Any]:
    """One inline file as a content block: image, video or PDF."""
    kind = mime.lower().split(";", 1)[0].strip()
    url = data_url(data, kind)
    if kind.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": url}}
    if kind.startswith("video/"):
        return {"type": "video_url", "video_url": {"url": url}}
    if kind == PDF_MIME:
        return {"type": "file", "file": {"file_data": url, "filename": PDF_FILENAME}}
    raise PortError(f"chat: unsupported attachment type {kind or 'unknown'}")


def part_blocks(part: ChatPart) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if part.text:
        blocks.append({"type": "text", "text": part.text})
    if part.data is not None:
        blocks.append(attachment_block(part.data, part.mime or ""))
    return blocks


def has_attachments(messages: Sequence[ChatMessage]) -> bool:
    return any(part.data is not None for message in messages for part in message.parts)


def message_payload(message: ChatMessage) -> dict[str, Any]:
    """A text-only message is sent as a plain string; a user message with files as blocks."""
    if all(part.data is None for part in message.parts):
        text = "\n\n".join(part.text for part in message.parts if part.text)
        return {"role": message.role, "content": text}
    if message.role != "user":
        raise PortError(f"chat: only user messages may carry files, not {message.role}")
    return {"role": message.role,
            "content": [block for part in message.parts for block in part_blocks(part)]}


def pick_capability(model: str, messages: Sequence[ChatMessage],
                    json_schema: dict[str, Any] | None) -> str:
    """vision when any file is attached, else structured for a JSON schema, else chat:smart.
    Subscription models run as agents on chat:smart (the Broker's documented lane for them)."""
    if has_attachments(messages):
        return "vision"
    if json_schema is not None and not is_subscription_model(model):
        return "structured"
    return "chat:smart"


def schema_name(label: str) -> str:
    name = _NAME_UNSAFE.sub("_", label).strip("_")[:64]
    return name or DEFAULT_SCHEMA_NAME


def inline_refs(schema: Any) -> Any:
    """The same JSON Schema without `$ref` / `$defs`: every local reference is replaced by its
    definition, and pydantic's `discriminator` mapping (it points into `$defs`) is dropped —
    `oneOf` / `anyOf` stay. Subscription models in the Broker reject schemas with references."""
    defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    def walk(node: Any, seen: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name in seen:
                    raise ValueError(f"recursive schema reference: {name}")
                return walk(defs[name], (*seen, name))
            return {key: walk(value, seen) for key, value in node.items()
                    if key not in ("$defs", "discriminator")}
        if isinstance(node, list):
            return [walk(value, seen) for value in node]
        return node

    return walk(schema, ())


# Value constraints the model does not need: SIM validates every answer itself (pydantic) and
# retries a bad one. Gemini refuses a schema whose constraints make «too many states for
# serving» (HTTP 400: string/array length limits, numeric ranges, formats, patterns) — every
# attention-agent call failed on 2026-11-21. Titles only add text.
CONSTRAINT_KEYS = frozenset({
    "maxLength", "minLength", "maxItems", "minItems", "uniqueItems", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "title",
})


def structure_only(schema: Any) -> Any:
    """The schema's structure (types, properties, required, enums, unions, descriptions)
    without value constraints. Property NAMES are kept even when they look like a constraint
    keyword (a field called `format` stays)."""
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in CONSTRAINT_KEYS:
                    continue
                if key == "properties" and isinstance(value, dict):
                    out[key] = {name: walk(field) for name, field in value.items()}
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(value) for value in node]
        return node

    return walk(schema)


def response_format(json_schema: dict[str, Any], label: str = "") -> dict[str, Any]:
    """`json_schema` is either a bare JSON Schema or the full OpenAI `json_schema` object
    (`{"name", "strict", "schema"}`); both become the Broker's strict `response_format`, with
    references inlined so every model (subscription ones too) accepts it."""
    if isinstance(json_schema.get("schema"), dict):
        spec = {"name": schema_name(str(json_schema.get("name") or label)), "strict": True,
                **{k: v for k, v in json_schema.items() if k != "name"}}
        spec["schema"] = structure_only(inline_refs(spec["schema"]))
    else:
        spec = {"name": schema_name(label), "strict": True,
                "schema": structure_only(inline_refs(json_schema))}
    return {"type": "json_schema", "json_schema": spec}


def workflow_tag(label: str) -> str | None:
    """The Broker `workflow` tag of a call (groups usage and keeps the prompt cache warm)."""
    clean = _TAG_UNSAFE.sub("-", label).strip("-")
    return f"sim-{clean}"[:128] if clean else None


def clamp_max_tokens(value: int) -> int:
    return max(1, min(int(value), MAX_TOKENS_LIMIT))
