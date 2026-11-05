"""Login rate limiting keyed by the real client address."""
from __future__ import annotations

import pytest

from sim.api.clientip import resolve_client_ip
from sim.services.auth import LoginRateLimiter, TooManyAttempts
from tests.conftest import OWNER, widget_payload

TRUSTED = "127.0.0.1,::1,172.16.0.0/12"


def test_forwarding_headers_ignored_from_untrusted_peer():
    headers = {"x-real-ip": "9.9.9.9", "x-forwarded-for": "8.8.8.8"}
    assert resolve_client_ip("203.0.113.7", headers, TRUSTED) == "203.0.113.7"


def test_trusted_peer_uses_real_ip_then_rightmost_untrusted_forwarded_for():
    assert resolve_client_ip("172.18.0.1", {"x-real-ip": "5.5.5.5"}, TRUSTED) == "5.5.5.5"
    # leftmost entry is client-controlled; the right-most non-proxy hop is what nginx saw
    xff = {"x-forwarded-for": "6.6.6.6, 5.5.5.5, 172.18.0.2"}
    assert resolve_client_ip("172.18.0.1", xff, TRUSTED) == "5.5.5.5"
    assert resolve_client_ip("172.18.0.1", {}, TRUSTED) == "172.18.0.1"
    assert resolve_client_ip("172.18.0.1", {"x-real-ip": "junk"}, TRUSTED) == "172.18.0.1"


def test_failures_of_one_address_do_not_lock_the_account_for_others():
    lim = LoginRateLimiter(max_failures=3)
    attacker = lim.keys_for("Boss", "1.1.1.1")
    for _ in range(3):
        lim.record_failure(*attacker, now=0)
    with pytest.raises(TooManyAttempts):
        lim.check(*attacker, now=1)
    lim.check(*lim.keys_for("boss", "2.2.2.2"), now=1)  # the owner from elsewhere is fine


def test_one_address_spraying_many_usernames_is_stopped_by_ip_key():
    lim = LoginRateLimiter(max_failures=3, ip_max_failures=6)
    for i in range(6):
        lim.record_failure(*lim.keys_for(f"user{i}", "1.1.1.1"), now=0)
    with pytest.raises(TooManyAttempts):
        lim.check(*lim.keys_for("fresh-name", "1.1.1.1"), now=1)


def test_success_resets_both_keys():
    lim = LoginRateLimiter(max_failures=3)
    keys = lim.keys_for("ann", "1.1.1.1")
    lim.record_failure(*keys, now=0)
    lim.reset(*keys)
    assert lim.failures == {}


def test_limiter_table_is_bounded_and_check_does_not_grow_it():
    lim = LoginRateLimiter(max_keys=50)
    for i in range(500):
        lim.check(f"pair:x|{i}", now=0)
    assert len(lim.failures) == 0
    for i in range(500):
        lim.record_failure(f"pair:x|{i}", now=0)
    assert len(lim.failures) <= 50


async def test_api_limit_is_per_client_address_behind_proxy(api):
    """Every request has the same TCP peer (the docker gateway); only X-Real-IP differs."""
    bad = {**widget_payload(OWNER[0]), "hash": "0" * 64}
    attacker = {"X-Real-IP": "198.51.100.9"}
    for _ in range(5):
        assert (await api.post("/api/auth/telegram", json=bad, headers=attacker)).status_code == 401
    assert (await api.post("/api/auth/telegram", json=bad, headers=attacker)).status_code == 429
    owner = await api.post(
        "/api/auth/telegram", json=widget_payload(OWNER[0]), headers={"X-Real-IP": "203.0.113.5"}
    )
    assert owner.status_code == 200


async def test_spoofed_forwarding_header_from_untrusted_peer_is_ignored(container, api):
    container.settings.trusted_proxies = "10.99.0.0/16"  # the test peer (127.0.0.1) is not trusted
    bad = {**widget_payload(OWNER[0]), "hash": "0" * 64}
    for i in range(5):
        r = await api.post("/api/auth/telegram", json=bad, headers={"X-Real-IP": f"1.2.3.{i}"})
        assert r.status_code == 401
    r = await api.post("/api/auth/telegram", json=bad, headers={"X-Real-IP": "1.2.3.99"})
    assert r.status_code == 429  # rotating the forged header did not reset the limit
