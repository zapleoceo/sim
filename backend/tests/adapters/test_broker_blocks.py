"""Message → Broker block conversion and the request choices that depend on the messages."""
from __future__ import annotations

import json

import pytest

from sim.adapters.broker.blocks import (
    MAX_TOKENS_LIMIT,
    attachment_block,
    clamp_max_tokens,
    data_url,
    inline_refs,
    structure_only,
    message_payload,
    pick_capability,
    response_format,
    schema_name,
    workflow_tag,
)
from sim.ports import ChatMessage, ChatPart, PortError

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def user(*parts: ChatPart) -> ChatMessage:
    return ChatMessage(role="user", parts=parts)


def test_data_url() -> None:
    assert data_url(b"hi", "image/png") == "data:image/png;base64,aGk="


def test_image_block() -> None:
    assert attachment_block(b"hi", "image/jpeg") == {
        "type": "image_url", "image_url": {"url": "data:image/jpeg;base64,aGk="}}


def test_video_block() -> None:
    assert attachment_block(b"hi", "video/mp4") == {
        "type": "video_url", "video_url": {"url": "data:video/mp4;base64,aGk="}}


def test_pdf_block() -> None:
    assert attachment_block(b"hi", "application/pdf") == {
        "type": "file",
        "file": {"file_data": "data:application/pdf;base64,aGk=", "filename": "document.pdf"}}


def test_mime_parameters_are_dropped() -> None:
    block = attachment_block(b"hi", "Image/PNG; charset=binary")
    assert block["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("mime", ["audio/ogg", "application/zip", ""])
def test_unsupported_attachment(mime: str) -> None:
    with pytest.raises(PortError, match="unsupported attachment") as info:
        attachment_block(b"x", mime)
    assert info.value.retryable is False


def test_text_message_is_a_string() -> None:
    message = ChatMessage(role="system", parts=[ChatPart(text="one"), ChatPart(text="two"),
                                                ChatPart(text="")])
    assert message_payload(message) == {"role": "system", "content": "one\n\ntwo"}


def test_user_message_with_files_is_blocks() -> None:
    payload = message_payload(user(ChatPart(text="what is it?"),
                                   ChatPart(data=b"v", mime="video/mp4"),
                                   ChatPart(text="caption", data=b"i", mime="image/png")))
    assert [block["type"] for block in payload["content"]] == [
        "text", "video_url", "text", "image_url"]
    assert payload["role"] == "user"


def test_files_only_in_user_messages() -> None:
    message = ChatMessage(role="assistant", parts=[ChatPart(data=b"i", mime="image/png")])
    with pytest.raises(PortError, match="only user messages"):
        message_payload(message)


def test_capability_vision_for_any_file() -> None:
    messages = [user(ChatPart(text="hi")), user(ChatPart(data=b"%PDF", mime="application/pdf"))]
    assert pick_capability("gemini/flash", messages, SCHEMA) == "vision"
    assert pick_capability("claude-sub/sonnet", messages, None) == "vision"


def test_capability_structured_for_schema() -> None:
    messages = [user(ChatPart(text="hi"))]
    assert pick_capability("gemini/flash", messages, SCHEMA) == "structured"
    assert pick_capability("gemini/flash", messages, None) == "chat:smart"


def test_subscription_models_stay_on_chat_smart() -> None:
    messages = [user(ChatPart(text="hi"))]
    assert pick_capability("claude-sub/sonnet", messages, SCHEMA) == "chat:smart"
    assert pick_capability("codex-sub/gpt", messages, SCHEMA) == "chat:smart"


def test_response_format_wraps_bare_schema() -> None:
    assert response_format(SCHEMA, "reply plan") == {
        "type": "json_schema",
        "json_schema": {"name": "reply_plan", "strict": True, "schema": SCHEMA}}


def test_response_format_keeps_full_spec() -> None:
    spec = {"name": "verdict", "strict": False, "schema": SCHEMA}
    assert response_format(spec, "guard") == {
        "type": "json_schema",
        "json_schema": {"name": "verdict", "strict": False, "schema": SCHEMA}}


def test_schema_name() -> None:
    assert schema_name("") == "answer"
    assert schema_name("!!!") == "answer"
    assert schema_name("guard/v2") == "guard_v2"
    assert len(schema_name("x" * 100)) == 64


def test_workflow_tag() -> None:
    assert workflow_tag("") is None
    assert workflow_tag("reply") == "sim-reply"
    assert workflow_tag("reply run #42") == "sim-reply-run-42"
    assert len(workflow_tag("y" * 300) or "") == 128


def test_clamp_max_tokens() -> None:
    assert clamp_max_tokens(0) == 1
    assert clamp_max_tokens(4096) == 4096
    assert clamp_max_tokens(100_000) == MAX_TOKENS_LIMIT


def test_references_are_inlined_for_every_model() -> None:
    from sim.domain.plan import ReplyPlan

    schema = ReplyPlan.model_json_schema()
    assert "$defs" in schema
    flat = response_format(schema, "reply")["json_schema"]["schema"]
    text = json.dumps(flat)
    assert "$ref" not in text and "$defs" not in text and "discriminator" not in text
    kinds = {variant["properties"]["type"]["const"]
             for variant in flat["properties"]["messages"]["items"]["oneOf"]}
    assert {"text", "voice", "photo", "video"} <= kinds


def test_recursive_references_are_refused() -> None:
    schema = {"$defs": {"N": {"type": "object", "properties": {"next": {"$ref": "#/$defs/N"}}}},
              "$ref": "#/$defs/N"}
    with pytest.raises(ValueError, match="recursive"):
        inline_refs(schema)


def test_value_constraints_are_not_sent_to_the_model() -> None:
    schema = {"type": "object", "title": "Plan", "properties": {
        "later": {"anyOf": [{"type": "string", "pattern": "^\d\d:\d\d$"},
                            {"type": "null"}]},
        "reason": {"type": "string", "maxLength": 120, "description": "why"},
        "items": {"type": "array", "maxItems": 50, "items": {"type": "integer", "minimum": 0}},
        "format": {"type": "string", "format": "date-time"},   # a field called «format»
    }, "required": ["reason"]}
    flat = structure_only(schema)
    text = json.dumps(flat)
    for key in ("pattern", "maxLength", "maxItems", "minimum", '"title"', "date-time"):
        assert key not in text
    assert set(flat["properties"]) == {"later", "reason", "items", "format"}
    assert flat["properties"]["reason"] == {"type": "string", "description": "why"}
    assert flat["required"] == ["reason"]


def test_every_schema_sent_is_structure_only() -> None:
    from sim.domain.attention import AttentionPlan
    from sim.domain.plan import ReplyPlan

    for model in (ReplyPlan, AttentionPlan):
        sent = json.dumps(response_format(model.model_json_schema(), "x")["json_schema"])
        assert "maxLength" not in sent and "maxItems" not in sent and "$ref" not in sent
