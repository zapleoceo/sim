"""One person in many messengers, pure parts: the «возможно, один человек» matching, what a merge
keeps of two profiles and two passports, and when it is refused (docs/leads.md)."""
from __future__ import annotations

from datetime import UTC, datetime

from sim.domain import client_match
from sim.domain.client_match import (
    ReasonKind,
    build_profile,
    handle_key,
    handle_stem,
    name_key,
    phone_key,
    reasons,
    said_handles,
    said_phones,
    suggest,
    whatsapp_phone,
)
from sim.domain.client_merge import Profile, merge_profiles, merge_refusal
from sim.domain.enums import ChannelKind, LeadStage
from sim.domain.names import name_words
from sim.domain.passport import Passport, merge_passports

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
TG, WA, IG = ChannelKind.TELEGRAM_USER, ChannelKind.WHATSAPP, ChannelKind.INSTAGRAM


def profile(client_id: int, messenger: ChannelKind, *, phone: str | None = None,
            username: str | None = None, name: str = "",
            texts: tuple[str, ...] = ()) -> client_match.Profile:
    return build_profile(client_id, messengers=[messenger], phones=[phone], handles=[username],
                         names=[name], texts=texts)


def test_keys_normalize_phones_handles_and_names() -> None:
    assert phone_key("+380 99 481-18-89") == phone_key("099 481 18 89") == "994811889"
    assert phone_key("12-34") is None and phone_key(None) is None
    assert whatsapp_phone("380994811889@s.whatsapp.net") == "380994811889"
    assert whatsapp_phone("1234567890@lid") is None             # a linked id, not a phone
    assert handle_key("@ZapleoSoft") == "zapleosoft" and handle_key("@ab") is None
    assert name_key("Zaporozhets Dmitry") == name_key("dmitry  ZAPOROZHETS") is not None
    assert name_key("Дима") is None                             # one word suggests nothing
    assert name_key("Пётр Иванов") == name_key("Петр иванов")
    assert said_handles("мой тг @ZapleoSoft, инста instagram.com/dima.z") == {"zapleosoft",
                                                                             "dima.z"}
    assert said_phones("звони +380 (99) 481-18-89 вечером") == {"994811889"}


def test_the_real_case_one_phone_in_telegram_and_whatsapp() -> None:
    telegram = profile(1, TG, phone="+380994811889", username="zapleosoft", name="Дима")
    whatsapp = build_profile(7, messengers=[WA], phones=[None, whatsapp_phone(
        "380994811889@s.whatsapp.net")], handles=[None], names=["Zaporozhets Dmitry"], texts=[])
    [found] = suggest({1: telegram, 7: whatsapp})
    assert (found.client_id, found.other_id) == (1, 7)
    assert [r.kind for r in found.reasons] == [ReasonKind.PHONE, ReasonKind.NAME]
    assert found.reasons[0].text == "один телефон …994811889"
    assert found.reasons[1].text == "похожие имена: «Дима» и «Zaporozhets Dmitry»"


def test_names_in_any_alphabet_and_spelling() -> None:
    assert name_words("Дмитрий Dmitriy Dmitry Dmytro Дима") == ["dmitri"] * 5
    assert name_words("Zaporozhets Запорожец") == ["zaporozec"] * 2
    assert name_words("Саша Alexander Олена Lena") == ["aleksandr", "aleksandr", "elena", "elena"]
    assert name_key("Dmitriy Zaporozhets") == name_key("Запорожец Дмитрий") is not None


def test_the_instagram_case_similar_usernames_and_a_shared_first_name() -> None:
    telegram = build_profile(1, messengers=[TG, WA], phones=["+380994811889"],
                             handles=["zapleosoft"], names=["Дима", "Дима"], texts=[])
    instagram = profile(8, IG, username="zapleo_ceo", name="Dmitriy Zaporozhets")
    [found] = suggest({1: telegram, 8: instagram})
    assert [r.text for r in found.reasons] == [
        "похожие username @zapleosoft и @zapleo_ceo",
        "похожие имена: «Дима» и «Dmitriy Zaporozhets»"]
    assert handle_stem("zapleo_ceo") == "zapleo" and handle_stem("max_1") is None


def test_a_first_name_alone_suggests_nothing() -> None:
    assert suggest({1: profile(1, TG, name="Дима"), 2: profile(2, IG, name="Dmitriy Petrov")}) == []
    assert reasons(profile(1, TG, username="anna_k"), profile(2, IG, username="max_p")) == []


def test_mentions_usernames_and_names_are_reasons_too() -> None:
    telegram = profile(1, TG, username="zapleosoft", name="Dmitry Zaporozhets")
    instagram = profile(2, IG, name="zaporozhets dmitry",
                        texts=("я тебе писал в телеге @zapleosoft",))
    why = reasons(telegram, instagram)
    assert [r.kind for r in why] == [ReasonKind.MENTION, ReasonKind.NAME]
    assert why[0].text == "в переписке назван @zapleosoft"
    same_handle = reasons(profile(3, TG, username="anna_k"), profile(4, IG, username="Anna_K"))
    assert [r.text for r in same_handle] == ["один username @anna_k"]


def test_no_suggestion_without_a_reason_in_one_messenger_or_marked_distinct() -> None:
    a = profile(1, TG, phone="+380994811889")
    b = profile(2, WA, phone="0994811889")
    two_telegrams = profile(3, TG, phone="+380994811889")
    stranger = profile(4, IG, name="Анна Смирнова")
    found = suggest({1: a, 2: b, 3: two_telegrams, 4: stranger})
    # 1-3 share Telegram: two accounts, never one client. 4 has nothing in common.
    assert [(s.client_id, s.other_id) for s in found] == [(1, 2), (2, 3)]
    assert [(s.client_id, s.other_id) for s in suggest({1: a, 2: b}, distinct=[(2, 1)])] == []


def test_more_reasons_first() -> None:
    a = profile(1, TG, phone="+380991112233", username="max_p")
    b = profile(2, WA, phone="+380991112233", texts=("@max_p это я",))
    c = profile(3, IG, phone="0991112233")
    found = suggest({1: a, 2: b, 3: c})
    assert (found[0].client_id, found[0].other_id) == (1, 2) and len(found[0].reasons) == 2


def test_merge_refusal() -> None:
    assert merge_refusal(1, 1, [TG], [WA]) == "Нельзя объединить клиента с самим собой"
    refusal = merge_refusal(1, 2, [TG, WA], [WA])
    assert refusal is not None and "WhatsApp" in refusal
    assert merge_refusal(1, 2, [TG], [WA, IG]) is None


def test_merge_profiles_keeps_the_kept_and_fills_the_gaps() -> None:
    early, late = datetime(2026, 11, 26, tzinfo=UTC), datetime(2026, 11, 28, tzinfo=UTC)
    keep = Profile(name="Дима", username="zapleosoft", phone=None, notes="VIP",
                   last_seen_at=early)
    other = Profile(name="Zaporozhets Dmitry", username=None, phone="+380994811889",
                    timezone="Europe/Kyiv", notes="пишет в WhatsApp\n", last_seen_at=late)
    merged = merge_profiles(keep, other)
    assert (merged.name, merged.username, merged.phone, merged.timezone) == (
        "Дима", "zapleosoft", "+380994811889", "Europe/Kyiv")
    assert merged.notes == "VIP\n\nпишет в WhatsApp" and merged.last_seen_at == late
    assert merge_profiles(keep, Profile(notes="VIP")).notes == "VIP"


def test_merge_passports_loses_nothing() -> None:
    keep = Passport(text="Работа: SIM", city="Киев", stage=LeadStage.NEW, temperature=40,
                    locked_fields=["city"])
    other = Passport(text="Семья: дочь", city="Одесса", age=35, stage=LeadStage.WARM,
                     temperature=70, locked_fields=["age"])
    merged = merge_passports(keep, other, NOW)
    assert merged.text == "Работа: SIM\n\nСемья: дочь"
    assert (merged.city, merged.age, merged.stage, merged.temperature) == (
        "Киев", 35, LeadStage.WARM, 70)
    assert merged.locked_fields == ["age", "city"] and merged.updated_at == NOW.isoformat()
    again = merge_passports(Passport(text="то же", stage=LeadStage.HOT),
                            Passport(text="то же", stage=LeadStage.WARM), NOW)
    assert again.text == "то же" and again.stage == LeadStage.HOT
