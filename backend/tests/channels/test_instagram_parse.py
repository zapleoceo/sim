"""Raw Direct threads -> items (sim/channels/instagram/parse.py): shared media that instagrapi's
extractor chokes on, our own items, items without a sender (fail closed), oldest first."""
from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

import pytest

from sim.channels.instagram.parse import (
    canonical_media,
    clean_url,
    item_text,
    media_of,
    media_text,
    read_box,
    thread_items,
)
from tests.channels.ig_fixtures import INBOX, LEAD, OWN, PENDING, SHARED_REEL, VIEW_ONCE


def test_items_are_oldest_first_with_sides_and_skips() -> None:
    items = thread_items(INBOX + PENDING, OWN)
    ids = [item.item_id for item in items]
    # system events, items without a sender, groups, empty threads and t-1 listed twice: out
    assert ids == ["i-old", "i-new", "i-mine", "i-story", "i-reel", "i-photo", "i-raven",
                   "i-voice"]
    assert "i-ghost" not in ids and "i-sys" not in ids and "i-g" not in ids
    assert "i-dup" not in ids                              # the second listing of t-1
    mine = next(item for item in items if item.item_id == "i-mine")
    assert mine.from_me and mine.user_id == LEAD           # the client of the thread
    story = next(item for item in items if item.item_id == "i-story")
    assert (story.from_me, story.thread_id, story.username, story.full_name) == (
        False, "t-1", "dima", "Дима Петров")
    stranger = next(item for item in items if item.item_id == "i-new")
    assert (stranger.user_id, stranger.thread_id, stranger.username) == ("2002", "t-2", "anna")
    assert all(a.at <= b.at for a, b in pairwise(items))
    assert items[0].at.tzinfo is UTC


def test_without_our_own_id_nothing_is_parsed() -> None:
    # stepan2: a blank own id filed 1401 of our own sends as client messages.
    with pytest.raises(ValueError, match="own Instagram id"):
        thread_items(INBOX, "")


def test_a_shared_reel_becomes_a_line_with_its_link() -> None:
    text = item_text(SHARED_REEL)
    assert text == "🎬 смешное видео\nhttps://www.instagram.com/reel/ABC123/"
    assert media_of(SHARED_REEL) is None                   # not media of the DM


def test_media_of_photos_view_once_and_voice() -> None:
    items = {item.item_id: item for item in thread_items(INBOX, OWN)}
    assert (items["i-photo"].media_kind, items["i-photo"].media_url) == (
        "photo", "https://cdn.example/p.jpg")
    assert media_of(VIEW_ONCE) == ("photo", "https://cdn.example/once.jpg")
    assert (items["i-voice"].media_kind, items["i-voice"].media_url) == (
        "voice", "https://cdn.example/v.m4a")
    # The placeholder is not a caption once the attachment is there.
    assert media_text(items["i-photo"].text, "photo") == ""
    assert media_text("смотри", "photo") == "смотри"


def test_links_are_unwrapped_and_canonical() -> None:
    wrapped = "https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&e=x"
    assert clean_url(wrapped) == "https://example.com/a?b=1"
    # A sponsored clip shares a POST under /reel/: the type comes from the item.
    assert canonical_media("https://instagram.com/reel/XYZ/?x=1", "xma_media_share") == \
        "https://www.instagram.com/p/XYZ/"
    assert canonical_media("https://example.com/reel/XYZ/") == "https://example.com/reel/XYZ/"
    assert clean_url("") is None


def test_timestamps_are_microseconds() -> None:
    [item] = [i for i in thread_items(PENDING, OWN) if i.item_id == "i-new"]
    assert item.at == datetime.fromtimestamp(1_763_750_000 - 7 * 60, tz=UTC)


def test_read_box_reads_pages_until_there_are_no_older() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def private_request(self, endpoint: str, params: dict[str, str]) -> dict:
            self.calls.append(params)
            if "cursor" not in params:
                return {"inbox": {"threads": [{"thread_id": "a"}], "has_older": True,
                                  "oldest_cursor": "c1"}, "pending_requests_total": 2}
            return {"inbox": {"threads": [{"thread_id": "b"}], "has_older": False},
                    "pending_requests_total": 9}

    client = Client()
    box = read_box(client, "direct_v2/inbox/", amount=40)
    assert [t["thread_id"] for t in box.threads] == ["a", "b"]
    assert box.pending == 2                                 # the first page says it
    assert client.calls[1]["cursor"] == "c1" and client.calls[1]["direction"] == "older"
    assert client.calls[0]["thread_message_limit"] == "10"


@pytest.mark.parametrize(("value", "pending"), [
    (0, 0), ("3", 3), (None, None), ("many", None), (True, None), (-1, 0)])
def test_the_waiting_requests_count_is_read_strictly(value: object, pending: int | None) -> None:
    class Client:
        def private_request(self, endpoint: str, params: dict[str, str]) -> dict:
            return {"inbox": {"threads": []}, "pending_requests_total": value}

    assert read_box(Client(), "direct_v2/inbox/").pending == pending
