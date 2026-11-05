"""Admin authentication: Telegram Login Widget only, server-side sessions, CSRF token, rate limit."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import AdminUser, AuthSession, LoginLink
from sim.adapters.db.repositories.accounts import (
    AdminUserRepo,
    AuthSessionRepo,
    LoginLinkRepo,
    UsedLoginHashRepo,
)
from sim.clock import Clock, utcnow
from sim.domain.enums import Role

INVALID_LOGIN = "Telegram login could not be verified"
INVALID_LINK = "Login link is invalid or expired"
ACCESS_DENIED = "Доступ не выдан"
WIDGET_MAX_AGE_S = 3600        # auth_date older than this is rejected
WIDGET_MAX_SKEW_S = 60         # auth_date this far in the future is tolerated
LOGIN_LINK_TTL = timedelta(minutes=10)
TG_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")


class AuthError(Exception):
    """The login could not be verified (bad / stale / replayed Telegram signature, bad link)."""


class AccessDenied(Exception):
    """Valid Telegram identity that is not an active admin."""

    def __init__(self, telegram_id: int) -> None:
        super().__init__(ACCESS_DENIED)
        self.telegram_id = telegram_id


class NotConfigured(Exception):
    """The Telegram login bot token is not set."""


class TooManyAttempts(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("Too many login attempts, try again later")
        self.retry_after_s = retry_after_s


def normalize_tg_username(value: str | None) -> str | None:
    """`@Name` / `Name` -> `name`; None for empty. Raises ValueError for an invalid name."""
    name = (value or "").strip().removeprefix("@").lower()
    if not name:
        return None
    if not TG_USERNAME_RE.match(name):
        raise ValueError("Invalid Telegram username")
    return name


@dataclass(frozen=True)
class TelegramProfile:
    id: int
    username: str | None
    display_name: str
    photo_url: str
    auth_date: int
    hash: str


def verify_telegram_widget(
    data: Mapping[str, Any], bot_token: str, *, now: float | None = None
) -> TelegramProfile:
    """Check a Telegram Login Widget payload: HMAC-SHA256 (secret = sha256(bot_token)) over the
    sorted `key=value` lines, constant-time compare, auth_date within 1 hour and not in the
    future. Raises AuthError / NotConfigured. One-time use of the hash is enforced by the
    service (it needs the database)."""
    if not bot_token:
        raise NotConfigured
    incoming = str(data.get("hash") or "").lower()
    pairs = sorted((k, str(v)) for k, v in data.items() if k != "hash" and v not in (None, ""))
    check_string = "\n".join(f"{k}={v}" for k, v in pairs)
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not incoming or not hmac.compare_digest(expected, incoming):
        raise AuthError(INVALID_LOGIN)
    try:
        auth_date = int(data["auth_date"])
        tg_id = int(data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError(INVALID_LOGIN) from exc
    now = time.time() if now is None else now
    if auth_date > now + WIDGET_MAX_SKEW_S or now - auth_date > WIDGET_MAX_AGE_S:
        raise AuthError(INVALID_LOGIN)
    first, last = str(data.get("first_name") or ""), str(data.get("last_name") or "")
    username = str(data.get("username") or "").lower() or None
    return TelegramProfile(
        id=tg_id,
        username=username,
        display_name=f"{first} {last}".strip() or (f"@{username}" if username else str(tg_id)),
        photo_url=str(data.get("photo_url") or ""),
        auth_date=auth_date,
        hash=incoming,
    )


class LoginRateLimiter:
    """Sliding window of FAILED attempts, keyed by (subject, ip) pair and by ip alone.

    There is deliberately NO per-subject key: it would let anyone lock the owner out from
    anywhere. The (subject, ip) pair stops hammering one identity from one address; the ip
    key (a looser cap) stops one address from spraying many identities.
    The key table is bounded: expired buckets are dropped and, past `max_keys`, the least
    recently touched ones go first."""

    def __init__(
        self,
        max_failures: int = 5,
        window_s: int = 900,
        *,
        ip_max_failures: int = 20,
        max_keys: int = 10_000,
    ) -> None:
        self.max_failures = max_failures
        self.ip_max_failures = ip_max_failures
        self.window_s = window_s
        self.max_keys = max_keys
        self.failures: OrderedDict[str, deque[float]] = OrderedDict()

    @staticmethod
    def keys_for(subject: str, ip: str) -> tuple[str, str]:
        """(pair key, ip key). The pair key is first."""
        return f"pair:{subject.lower()}|{ip}", f"ip:{ip}"

    def limit_for(self, key: str) -> int:
        return self.ip_max_failures if key.startswith("ip:") else self.max_failures

    def prune(self, now: float) -> None:
        for key in [k for k, b in self.failures.items() if not b or now - b[-1] > self.window_s]:
            del self.failures[key]

    def check(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            bucket = self.failures.get(key)
            if bucket is None:
                continue
            while bucket and now - bucket[0] > self.window_s:
                bucket.popleft()
            if len(bucket) >= self.limit_for(key):
                raise TooManyAttempts(int(self.window_s - (now - bucket[0])) + 1)

    def record_failure(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            self.failures.setdefault(key, deque()).append(now)
            self.failures.move_to_end(key)
        if len(self.failures) > self.max_keys:
            self.prune(now)
        while len(self.failures) > self.max_keys:
            self.failures.popitem(last=False)

    def reset(self, *keys: str) -> None:
        for key in keys:
            self.failures.pop(key, None)


@dataclass(frozen=True)
class LoginResult:
    user: AdminUser
    token: str
    csrf_token: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        ttl_h: int,
        limiter: LoginRateLimiter,
        bot_token: str = "",
        clock: Clock = utcnow,
    ) -> None:
        self.users = AdminUserRepo(session)
        self.sessions = AuthSessionRepo(session)
        self.used_hashes = UsedLoginHashRepo(session)
        self.links = LoginLinkRepo(session)
        self.ttl = timedelta(hours=ttl_h)
        self.limiter = limiter
        self.bot_token = bot_token
        self.clock = clock

    # ---- users -----------------------------------------------------------------------------
    async def sync_superadmins(self, telegram_ids: list[int]) -> int:
        """Make every configured id an active superadmin (idempotent). Returns rows changed."""
        changed = 0
        for tg_id in telegram_ids:
            user = await self.users.by_telegram_id(tg_id)
            if user is None:
                await self.users.add(
                    AdminUser(
                        telegram_id=tg_id, role=Role.SUPERADMIN, is_active=True,
                        display_name=str(tg_id),
                    )
                )
                changed += 1
            elif user.role is not Role.SUPERADMIN or not user.is_active:
                user.role, user.is_active = Role.SUPERADMIN, True
                changed += 1
        return changed

    # ---- Telegram login --------------------------------------------------------------------
    async def login_telegram(self, data: Mapping[str, Any], client_addr: str = "") -> LoginResult:
        keys = self.limiter.keys_for(str(data.get("id", "")), client_addr)
        self.limiter.check(*keys)
        now = self.clock()
        try:
            profile = verify_telegram_widget(data, self.bot_token)
            # Only a correctly signed hash is recorded, so garbage cannot burn real ones.
            await self.used_hashes.purge_expired(now)
            fresh = await self.used_hashes.claim(
                profile.hash, now + timedelta(seconds=WIDGET_MAX_AGE_S + WIDGET_MAX_SKEW_S)
            )
            if not fresh:
                raise AuthError(INVALID_LOGIN)  # replay
        except AuthError:
            self.limiter.record_failure(*keys)
            raise
        user = await self.find_admin(profile)
        if user is None:
            raise AccessDenied(profile.id)
        self.limiter.reset(*keys)
        await self.refresh_profile(user, profile)
        return await self.issue_session(user)

    async def find_admin(self, profile: TelegramProfile) -> AdminUser | None:
        user = await self.users.by_telegram_id(profile.id)
        if user is not None:
            return user if user.is_active else None
        if profile.username:  # invited by @username: the first login binds the numeric id
            invited = await self.users.by_tg_username(profile.username)
            if invited is not None and invited.telegram_id is None and invited.is_active:
                invited.telegram_id = profile.id
                return invited
        return None

    async def refresh_profile(self, user: AdminUser, profile: TelegramProfile) -> None:
        user.display_name = profile.display_name
        user.photo_url = profile.photo_url
        if profile.username != user.tg_username:
            holder = await self.users.by_tg_username(profile.username) if profile.username else None
            if holder is None or holder.id == user.id:
                user.tg_username = profile.username
        await self.users.session.flush()

    async def issue_session(self, user: AdminUser) -> LoginResult:
        now = self.clock()
        await self.sessions.purge_expired(now)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        await self.sessions.add(
            AuthSession(
                user_id=user.id,
                token_hash=hash_token(token),
                csrf_token=csrf,
                expires_at=now + self.ttl,
            )
        )
        return LoginResult(user=user, token=token, csrf_token=csrf)

    # ---- break-glass login link ------------------------------------------------------------
    async def create_login_link(self, telegram_id: int) -> str:
        """One-time token (valid 10 minutes) for an existing active admin; only sha256 stored."""
        user = await self.users.by_telegram_id(telegram_id)
        if user is None or not user.is_active:
            raise AccessDenied(telegram_id)
        now = self.clock()
        await self.links.purge_expired(now)
        token = secrets.token_urlsafe(32)
        await self.links.add(
            LoginLink(
                user_id=user.id, token_hash=hash_token(token), expires_at=now + LOGIN_LINK_TTL
            )
        )
        return token

    async def login_with_link(self, token: str, client_addr: str = "") -> LoginResult:
        keys = self.limiter.keys_for("link", client_addr)
        self.limiter.check(*keys)
        row = await self.links.by_token_hash(hash_token(token)) if token else None
        user = await self.users.get(row.user_id) if row is not None else None
        if (
            row is None
            or user is None
            or not user.is_active
            or row.expires_at < self.clock()
            or not await self.links.consume(row.id)
        ):
            self.limiter.record_failure(*keys)
            raise AuthError(INVALID_LINK)
        return await self.issue_session(user)

    # ---- sessions --------------------------------------------------------------------------
    async def authenticate(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        row = await self.sessions.by_token_hash(hash_token(token))
        if row is None or row.expires_at < self.clock() or not row.user.is_active:
            return None
        return row

    async def logout(self, token: str | None) -> None:
        if token and (row := await self.sessions.by_token_hash(hash_token(token))):
            await self.sessions.delete(row)
